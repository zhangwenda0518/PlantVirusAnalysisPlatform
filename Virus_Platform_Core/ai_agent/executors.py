# -*- coding: utf-8 -*-
"""AI 执行层：分级执行权（有边界的"放开"）。

    第一级 执行单（默认，永远在）：AI 只能**起草**动作（create_action），
            产生待批卡片（对话坞里看参数 diff 与理由），用户批准后才经
            **与界面按钮完全相同的代码路径**执行；驳回即作废。
    第二级 自动重试（cfg.ai['exec_auto']，默认关）：阶段失败后原参数重跑，
            不经确认——因为"重试"就是平台断点续跑机制本身，AI 只是替你
            按了重试按钮。每样品每阶段限 1 次，杜绝无限循环。
    第三级 自动调参闭环（cfg.ai['exec_auto_tune']，默认关，需手动开启）：
            失败后由 AI 提议参数（限定键 + 数值边界钳制）并自动执行，
            每样品限 1 次。调参失败回落第二级（若其开启）。

永远不做的事（注册表里根本没有这些动作）：
    删除样品/数据、全局重置、修改 AI 配置自身、修改设置中心、
    平台目录之外的任何写操作。

安全闸（全部硬编码，不受配置影响）：
    - 同一样品同时只允许一个运行（复用 _sample_busy_register）；
    - AI 发起的运行全平台并发上限 MAX_AI_RUNS=1；
    - 执行单参数走白名单 + 类型钳制，未知键直接拒绝；
    - 全部动作（提案/批准/驳回/自动执行/结果）发布到事件流**并异步落盘**
      （`ai_agent/audit.py`，按天轮转 JSONL）；重启后 `list_actions` 从盘上
      重建历史，进行中的状态标为 `interrupted`，且 aid 计数器会推高防撞号。
"""
import json
import threading
import time

from Virus_Platform_Core.config import DIRS, get_config

# ------------------------------------------------------------------
# 动作注册表（白名单：没有登记的动作 AI 永远无法发起）
# ------------------------------------------------------------------
ACTIONS = {
    'rerun_stage': {
        'title': ('重跑指定阶段', 'Re-run stage(s)'),
        'risk': 'confirm',        # Tier1 确认；Tier3 开启时可自动（仍受预算）
    },
    'retry_failed': {
        'title': ('原参数重试失败阶段', 'Retry failed stage(s), same params'),
        'risk': 'auto_ok',        # 可进入第二级白名单
    },
    'download_runs': {
        'title': ('下载公共数据', 'Download public data'),
        'risk': 'confirm',        # 外网批量下载，永远人工确认（不走自动级）
    },
    'run_tool': {
        'title': ('运行工具箱工具', 'Run toolbox tool'),
        'risk': 'confirm',        # 对齐 SDT/RDP/引物/比对等 ~30 个工具箱能力
    },
    'create_sample': {
        'title': ('创建样品', 'Create sample'),
        'risk': 'confirm',        # 从已落盘的 FASTQ 建样品（不下载、不联网）
    },
    # 2026-10-29 用户要求：宿主基因组数据库构建也要能由 AI 发起
    #（「数据库构建」页 /api/build_host_db 的同一套校验与任务启动）。
    'build_host_db': {
        'title': ('构建宿主基因组数据库', 'Build host genome database'),
        'risk': 'confirm',
    },
    # 多步计划：一次批准后按顺序执行，前一步任务成功结束才进入下一步。
    # 用于「建样品 → 组装 → 建宿主库」这类跨模块链路（2026-10-29）。
    'plan': {
        'title': ('多步分析计划', 'Multi-step analysis plan'),
        'risk': 'confirm',
    },
}

# 计划里允许串起来的动作。download_runs 不在内：它归下载管理器管，没有
# 任务中心的 tid 可等（要等下载完成再继续，请拆成两次对话）。
PLAN_ACTIONS = ('rerun_stage', 'retry_failed', 'run_tool',
                'create_sample', 'build_host_db')
PLAN_MAX_STEPS = 6

# 允许 AI 调参的键与边界（第三级）；之外的键一律拒绝
TUNABLE_PARAMS = {
    'assembly_mode': ('rnaviral', 'metaviral', 'rna', 'meta', 'isolate', 'srna'),
    'min_contig_len': (50, 5000),
    'confidence': (0.0, 1.0),
    'subsample': (0, 5_000_000),
    'subsample_mode': ('prefix', 'random', 'proportion'),
    'subsample_prop': (0.0, 1.0),
    'subsample_seed': (1, 2_147_483_647),
    'min_orf_aa': (30, 1000),
    'top_n_refs': (5, 50),
}

# run_analysis 参数装配请求体里合法的键（白名单之外的提案直接拒）
BODY_PARAM_KEYS = {
    'assembly_mode', 'assembly_input', 'confidence', 'threads', 'memory',
    'subsample', 'subsample_mode', 'subsample_prop', 'subsample_seed',
    'min_contig_len', 'min_orf_aa', 'top_n_refs', 'tree_tool',
    'tree_sampling', 'primer_mode', 'do_trim', 'specificity', 'force',
    'fastp_dedup', 'do_fq2fa', 'gbdraw_max', 'plot_engine', 'kv_lib',
    'db_host', 'db_virus', 'chunk_dir', 'verify_host', 'verify_combine',
}

MAX_PENDING = 20                 # 待批执行单上限
MAX_AI_RUNS = 1                  # AI 发起的运行全平台并发上限
AUTO_RETRY_MAX_PER_STAGE = 1     # 第二级：每样品每阶段自动重试次数
AUTO_TUNE_MAX_PER_SAMPLE = 1     # 第三级：每样品自动调参次数

# ------------------------------------------------------------------
# 待决动作存储 + 预算
# ------------------------------------------------------------------
_lock = threading.Lock()
_actions = {}                    # aid -> rec（含全部历史状态）
_order_n = 0                     # 已发出的 aid 序号（重启后从审计盘推高）
_auto_retried = {}               # (sample, stage) -> 次数
_auto_tuned = {}                 # sample -> 次数
_hydrated = False                # 是否已从审计盘重建过历史
# 语言无关的「AI 发起」标记：任务中心的任务名在英文界面是 "AI run·…"，
# 按中文前缀 'AI执行·' 计数会漏，故在启动处登记真实任务 id。
_AI_TIDS = set()

# 内存里保留的历史条数上限（审计盘才是长期留痕，内存只为界面列表）
MAX_KEEP_ACTIONS = 500


def _ai_cfg():
    return get_config().ai or {}


def _stage_disp(stage):
    try:
        from Virus_Platform_Core.pipeline import STAGE_NAMES, STAGE_NAMES_EN
        lang = get_config().lang
        return ((STAGE_NAMES_EN if lang == 'en' else STAGE_NAMES)
                .get(stage) or STAGE_NAMES.get(stage) or stage)
    except Exception:
        return stage


def _publish(rec, event='ai_action'):
    """执行单 → 事件总线。

    ⚠ 执行单的业务 id（'act_N' 字符串）以 **aid** 字段发布——绝不能叫
    'id'：事件总线自己的整型 id 是客户端去重/水位线的根基，被覆盖会把
    events_since 的 int 比较打成 TypeError，整条 SSE 流持续 500（实测踩过）。
    """
    from . import broadcast
    # 2026-11-02：白名单漏了 steps/step 系列，而对话坞的执行单卡片**只从
    # 事件里取这些字段**（app-ai.js 的 ai_action 处理器）——结果多步计划卡
    # 不列步骤，批准后也永远没有「第 2/3 步」进度，用户看起来像卡死。
    # 这里与 _snapshot 的字段取并集，别再各自维护一份。
    payload = {k: v for k, v in rec.items()
               if k in ('action', 'title', 'sample', 'stages', 'params',
                        'base_params', 'reason', 'status', 'source',
                        'error', 'tid', 'runs', 'tool', 'tool_params',
                        'steps', 'step', 'step_total', 'step_action',
                        'step_title', 'result_sample',
                        # 2026-11-02：执行单跑完要把产物做成卡片（app-ai.js
                        # 的 ai_action 处理器直接渲染，无需再问路径）
                        'artifacts')}
    if isinstance(payload.get('runs'), list):
        payload['runs'] = payload['runs'][:10]   # 事件里只带前 10 条防膨胀
        payload['runs_n'] = rec.get('params', {}).get('runs_n')
    payload['aid'] = rec['id']
    broadcast.publish(event, **payload)


def _snapshot(rec):
    return {k: rec.get(k) for k in
            ('id', 'action', 'title', 'sample', 'stages', 'params',
             'base_params', 'reason', 'status', 'source', 'error', 'tid',
             'ts', 'tool', 'tool_params', 'runs',
             # 多步计划（2026-10-29）：进度要随事件发给对话坞的执行单卡片
             'steps', 'step', 'step_total', 'step_action', 'step_title',
             # 2026-11-02：create_sample 是同步动作，返回值是**样品名**而不是
             # 任务 id。这里漏了它，刷新页面（走 /api/ai/actions 对账）时
             # 卡片就丢了「建了哪个样品」，只剩一个 ✓ 完成。
             'result_sample',
             # 2026-11-02：执行单完成后能直接点开的产物（只存 ≤4 条，很小）
             'artifacts')}


def _audit(rec):
    """执行单状态 → 审计盘。

    用 `record_sync`（**同步**写）而不是异步队列：状态变迁低频高价值，
    而 Windows 的 `TerminateProcess` 不可捕获，异步队列的 2s 窗口可能把
    "谁批的、执行没执行"整段丢掉。
    """
    try:
        from . import audit
        audit.record_sync('actions', rec)
    except ImportError:
        # record_sync 自身「永不抛异常」是它的契约；这里只兜 import 失败。
        pass


def _hydrate():
    """从审计盘重建历史执行单（**重启后 `list_actions` 仍能看到过去**）。

    只回灌内存里没有的 id；`pending/executing/launched` 这些"进行中"状态在
    重启后已失去执行线程，标为 `interrupted`（诚实反映"没跑完就没了"，而不是
    假装还能被批准）。同时把 aid 计数器推到已用最大值之后，**避免重启后
    `act_1` 撞号**（撞号会让新单覆盖旧记录）。

    ⚠ 必须在**取 `_lock` 之前**调用，否则与本函数内部的 `_lock` 自锁。
    """
    global _hydrated, _order_n
    if _hydrated:
        return
    with _lock:
        if _hydrated:
            return
        _hydrated = True
    try:
        from . import audit
        recs = audit.read('actions', limit=MAX_KEEP_ACTIONS * 2)
    except Exception:
        return
    if not recs:
        return
    mx = 0
    with _lock:
        for r in recs:                       # read() 返回新→旧
            aid = r.get('id')
            if not isinstance(aid, str) or not aid.startswith('act_'):
                continue
            try:
                mx = max(mx, int(aid.split('_', 1)[1]))
            except (IndexError, ValueError):
                continue
            if aid in _actions:
                continue
            r = dict(r)
            if r.get('status') in ('pending', 'executing', 'launched'):
                r['status'] = 'interrupted'
            _actions[aid] = r
        _order_n = max(_order_n, mx)
        # 内存只为界面列表服务：超量时按 ts 丢最旧
        if len(_actions) > MAX_KEEP_ACTIONS:
            keep = sorted(_actions.values(), key=lambda x: -(x.get('ts') or 0))
            _actions.clear()
            for r in keep[:MAX_KEEP_ACTIONS]:
                _actions[r['id']] = r


def list_actions(limit=30):
    _hydrate()
    with _lock:
        recs = sorted(_actions.values(), key=lambda r: -r['ts'])
        return [_snapshot(r) for r in recs[:limit]]


def get_action(aid):
    _hydrate()
    with _lock:
        rec = _actions.get(aid)
        return _snapshot(rec) if rec else None


# ------------------------------------------------------------------
# 参数校验（白名单 + 类型钳制；未知键直接拒绝）
# ------------------------------------------------------------------
def validate_params(action, params):
    """返回 (normalized_params, error)。error 非 None 表示提案非法。

    白名单 = BODY_PARAM_KEYS；TUNABLE_PARAMS 里的键额外做取值边界钳制
    （第三级自动调参的护栏，对对话提案同样生效）。
    """
    if params is None:
        return {}, None
    if not isinstance(params, dict):
        return {}, 'params 须为对象'
    bad = [k for k in params if k not in BODY_PARAM_KEYS]
    if bad:
        return {}, f'参数不在允许清单内: {", ".join(map(str, bad))}'
    out = {}
    for k, v in params.items():
        if v is None or v == '':
            continue
        if k in ('assembly_mode', 'subsample_mode'):
            if str(v) not in TUNABLE_PARAMS[k]:
                return {}, f'{k} 只允许: {", ".join(TUNABLE_PARAMS[k])}'
            out[k] = str(v)
            continue
        if k in ('do_trim', 'specificity', 'force', 'fastp_dedup',
                 'do_fq2fa'):
            out[k] = bool(v)
            continue
        if k in ('confidence', 'subsample_prop'):
            try:
                num = float(v)
            except (TypeError, ValueError):
                return {}, f'{k} 须为数字'
            if k in TUNABLE_PARAMS:
                lo, hi = TUNABLE_PARAMS[k]
                num = min(hi, max(lo, num))
            out[k] = round(num, 3)
            continue
        if k in ('primer_mode', 'tree_tool', 'tree_sampling',
                 'assembly_input', 'plot_engine', 'verify_host',
                 'verify_combine', 'kv_lib', 'db_host', 'db_virus',
                 'chunk_dir'):
            out[k] = str(v)
            continue
        # 其余全部是整数键
        try:
            num = int(v)
        except (TypeError, ValueError):
            return {}, f'{k} 须为整数'
        if k in TUNABLE_PARAMS:
            lo, hi = TUNABLE_PARAMS[k]
            num = int(min(hi, max(lo, num)))
        out[k] = num
    return out, None


# ------------------------------------------------------------------
# 创建 / 决策
# ------------------------------------------------------------------
def create_action(action, sample, stages=None, params=None, reason='',
                  source='chat', base_params=None):
    """AI（或代码）起草一张执行单。返回 (rec, error)。"""
    if action not in ACTIONS:
        return None, f'未知动作: {action}（允许: {", ".join(ACTIONS)}）'
    # 先重建历史：否则重启后第一张单会拿到 act_1，与盘上旧记录撞号
    _hydrate()
    if action == 'download_runs':
        return _create_download(params, reason, source)
    if action == 'run_tool':
        return _create_run_tool(params, reason, source)
    if action == 'create_sample':
        return _create_sample_order(params, reason, source)
    if action == 'build_host_db':
        return _create_host_db_order(params, reason, source)
    if action == 'plan':
        return _create_plan(params, reason, source)
    sample = str(sample or '').strip()
    if not sample:
        return None, '需要 sample'
    from Virus_Platform_Core.utils import resolve_sample_name
    try:
        real = resolve_sample_name(sample, DIRS['results'])
        sd = __import__('os').path.join(DIRS['results'], real)
        if not __import__('os').path.isdir(sd):
            return None, f'样品不存在: {sample}'
    except Exception as e:
        return None, f'样品解析失败: {e}'
    if stages is not None:
        from Virus_Platform_Core.pipeline import STAGE_ORDER
        raw = [str(s).strip() for s in stages if str(s).strip()]
        # 2026-11-02：以前非法阶段被静默丢弃，模型说「重跑 A+B+C」实际只跑
        # A，执行单照样 created，完成后报「全部成功」。改为明确报错并列出
        # 合法键，模型可当场修正。
        bad = [s for s in raw if s not in STAGE_ORDER]
        if bad:
            return None, (f'阶段键非法: {", ".join(bad)}（合法键见平台阶段清单: '
                          f'{", ".join(STAGE_ORDER)}）')
        stages = raw
        if not stages:
            return None, 'stages 为空或全部非法'
    params, err = validate_params(action, params)
    if err:
        return None, err
    with _lock:
        pending = sum(1 for r in _actions.values() if r['status'] == 'pending')
        if pending >= MAX_PENDING:
            return None, f'待批执行单已达上限（{MAX_PENDING}），请先处理'
        global _order_n
        _order_n += 1
        aid = f'act_{_order_n}'
        lang = get_config().lang
        rec = {'id': aid, 'action': action,
               'title': ACTIONS[action]['title'][0 if lang == 'zh' else 1],
               'sample': real, 'stages': stages, 'params': params,
               'base_params': base_params or {},
               'reason': str(reason or '')[:800], 'status': 'pending',
               'source': source, 'error': '', 'tid': '', 'ts': time.time(),
               'origin': 'ai'}
        _actions[aid] = rec
    _snapshot_publish(rec, 'pending')
    return rec, None


def _create_download(params, reason, source):
    """download_runs 执行单：runs 编号清单 → 待批卡片。

    校验：runs 必须是 1~500 条非空编号（SRR/ERR/DRR/CRR 或 URL——合法性
    由下载管理器在执行时逐条解析，这里只挡空单与超量）。
    """
    params = params if isinstance(params, dict) else {}
    runs = [str(x).strip() for x in (params.get('runs') or [])
            if str(x).strip()]
    if not runs:
        return None, "download_runs 需要 params.runs（run 编号列表，1~500 条）"
    if len(runs) > 500:
        return None, '单批最多 500 条，请拆分'
    name = str(params.get('name') or '').strip() or 'AI 下载'
    convert_sra = bool(params.get('convert_sra', True))
    with _lock:
        pending = sum(1 for r in _actions.values() if r['status'] == 'pending')
        if pending >= MAX_PENDING:
            return None, f'待批执行单已达上限（{MAX_PENDING}），请先处理'
        global _order_n
        _order_n += 1
        aid = f'act_{_order_n}'
        lang = get_config().lang
        rec = {'id': aid, 'action': 'download_runs',
               'title': ACTIONS['download_runs']['title'][0 if lang == 'zh'
                                                          else 1],
               'sample': '', 'stages': None,
               'params': {'runs_n': len(runs), 'name': name,
                          'convert_sra': convert_sra},
               'base_params': {}, 'runs': runs,
               'reason': str(reason or '')[:800], 'status': 'pending',
               'source': source, 'error': '', 'tid': '', 'ts': time.time(),
               'origin': 'ai'}
        _actions[aid] = rec
    _snapshot_publish(rec, 'pending')
    return rec, None


def _create_run_tool(params, reason, source):
    """run_tool 执行单：把工具箱（TOOL_REGISTRY）里的任一工具作为待批动作。

    参数即该工具在界面上的表单键值（字符串口径，与 /api/tool/run 的
    body.params 一致）；文件存在性等校验在执行期由 job 工厂做——失败会
    如实变成 failed 状态与错误信息，AI 可据此修正后重新提案。
    """
    params = params if isinstance(params, dict) else {}
    tool = str(params.get('tool') or '').strip()
    try:
        from Virus_Platform_Core.web.tools_api import TOOL_REGISTRY
        spec = TOOL_REGISTRY.get(tool)
    except Exception as e:
        return None, f'工具注册表不可用: {e}'
    if not spec:
        return None, (f'未知工具: {tool or "(空)"}；'
                      '先用 list_platform_tools 查看可用工具')
    tool_params = {str(k): v for k, v in params.items() if k != 'tool'}
    with _lock:
        pending = sum(1 for r in _actions.values() if r['status'] == 'pending')
        if pending >= MAX_PENDING:
            return None, f'待批执行单已达上限（{MAX_PENDING}），请先处理'
        global _order_n
        _order_n += 1
        aid = f'act_{_order_n}'
        lang = get_config().lang
        rec = {'id': aid, 'action': 'run_tool',
               'title': (spec['title'] if lang == 'zh' else
                         spec.get('title_en') or spec['title']),
               'sample': '', 'stages': None,
               'params': {'tool': tool, **tool_params},
               'tool': tool, 'tool_params': tool_params,
               'base_params': {},
               'reason': str(reason or '')[:800], 'status': 'pending',
               'source': source, 'error': '', 'tid': '', 'ts': time.time(),
               'origin': 'ai'}
        _actions[aid] = rec
    _snapshot_publish(rec, 'pending')
    return rec, None


def _create_sample_order(params, reason, source):
    """create_sample 执行单：从已落盘的 FASTQ 建样品（不联网、不下载）。"""
    params = params if isinstance(params, dict) else {}
    r1 = str(params.get('r1') or '').strip()
    r2 = str(params.get('r2') or '').strip()
    sample = str(params.get('sample') or '').strip()
    if not r1:
        return None, 'create_sample 需要 params.r1（FASTQ 绝对路径）'
    import os
    for k, v in (('r1', r1), ('r2', r2)):
        if v and not os.path.isfile(v):
            return None, f'params.{k} 文件不存在: {v}'
    if not sample:
        sample = os.path.splitext(os.path.basename(r1))[0]
    with _lock:
        pending = sum(1 for r in _actions.values() if r['status'] == 'pending')
        if pending >= MAX_PENDING:
            return None, f'待批执行单已达上限（{MAX_PENDING}），请先处理'
        global _order_n
        _order_n += 1
        aid = f'act_{_order_n}'
        lang = get_config().lang
        rec = {'id': aid, 'action': 'create_sample',
               'title': ACTIONS['create_sample']['title'][0 if lang == 'zh'
                                                          else 1],
               'sample': sample, 'stages': None,
               'params': {'sample': sample, 'r1': r1, 'r2': r2,
                          'project': str(params.get('project') or '')},
               'base_params': {},
               'reason': str(reason or '')[:800], 'status': 'pending',
               'source': source, 'error': '', 'tid': '', 'ts': time.time(),
               'origin': 'ai'}
        _actions[aid] = rec
    _snapshot_publish(rec, 'pending')
    return rec, None


def _create_host_db_order(params, reason, source):
    """build_host_db 执行单：用已下载的宿主基因组 FASTA 建 kunpeng 宿主库。

    参数口径与 /api/build_host_db 一致：genome（FASTA 绝对路径）、taxid
    （数字）；out_dir / threads / rebuild / clean_mid / hash_capacity 可选。
    真正的路径/根目录校验在执行时由 build.start_host_db_build 统一做，
    这里只挡明显的空参，让执行单在批准前就能被看懂。
    """
    params = params if isinstance(params, dict) else {}
    import os
    genome = str(params.get('genome') or '').strip()
    if not genome:
        return None, 'build_host_db 需要 params.genome（宿主基因组 FASTA 绝对路径）'
    if not os.path.isfile(genome):
        return None, f'params.genome 文件不存在: {genome}'
    try:
        taxid = int(params.get('taxid'))
    except (TypeError, ValueError):
        return None, 'build_host_db 需要数字 params.taxid（NCBI Taxonomy ID）'
    p = {'genome': genome, 'taxid': taxid}
    for k in ('out_dir', 'hash_capacity'):
        v = params.get(k)
        if v not in (None, ''):
            p[k] = str(v)
    if params.get('threads') not in (None, ''):
        # 2026-11-02：threads 直接 int(v) 没兜住 "abc"，ValueError 会逃出
        # create_action，被工具层压成英文异常字符串给模型。
        try:
            p['threads'] = max(1, int(params.get('threads')))
        except (TypeError, ValueError):
            return None, 'params.threads 须为正整数'
    for k in ('rebuild', 'clean_mid'):
        if params.get(k) is not None:
            p[k] = bool(params.get(k))
    with _lock:
        pending = sum(1 for r in _actions.values() if r['status'] == 'pending')
        if pending >= MAX_PENDING:
            return None, f'待批执行单已达上限（{MAX_PENDING}），请先处理'
        global _order_n
        _order_n += 1
        aid = f'act_{_order_n}'
        lang = get_config().lang
        rec = {'id': aid, 'action': 'build_host_db',
               'title': ACTIONS['build_host_db']['title'][0 if lang == 'zh'
                                                          else 1],
               'sample': '', 'stages': None, 'params': p,
               'base_params': {},
               'reason': str(reason or '')[:800], 'status': 'pending',
               'source': source, 'error': '', 'tid': '', 'ts': time.time(),
               'origin': 'ai'}
        _actions[aid] = rec
    _snapshot_publish(rec, 'pending')
    return rec, None


def _norm_plan_step(i, st):
    """校验计划里的第 i 步（1-based），返回 (step, error)。"""
    if not isinstance(st, dict):
        return None, f'第 {i} 步不是对象'
    act = str(st.get('action') or '').strip()
    if act not in PLAN_ACTIONS:
        return None, (f'第 {i} 步动作不支持: {act}'
                      f'（计划里可用: {", ".join(PLAN_ACTIONS)}）')
    p = st.get('params') if isinstance(st.get('params'), dict) else {}
    step = {'action': act, 'sample': str(st.get('sample') or '').strip(),
            'stages': st.get('stages') or None, 'params': p,
            'tool': p.get('tool'), 'tool_params': p}
    if act in ('rerun_stage', 'retry_failed'):
        if not step['sample']:
            return None, f'第 {i} 步（{act}）需要 sample'
        if act == 'rerun_stage' and not step['stages']:
            return None, f'第 {i} 步（rerun_stage）需要 stages'
    elif act == 'run_tool':
        if not str(p.get('tool') or '').strip():
            return None, f'第 {i} 步（run_tool）需要 params.tool'
    elif act == 'create_sample':
        if not str(p.get('r1') or '').strip():
            return None, f'第 {i} 步（create_sample）需要 params.r1'
    elif act == 'build_host_db':
        if not str(p.get('genome') or '').strip() or p.get('taxid') in (None, ''):
            return None, f'第 {i} 步（build_host_db）需要 params.genome 与 params.taxid'
    return step, None


def _create_plan(params, reason, source):
    """plan 执行单：steps 按顺序执行，前一步任务 done 才进入下一步。

    计划本身不复制子动作的参数校验（那由各动作在执行时给出可读错误），
    只在提案阶段挡住缺关键参数的步骤，避免用户批准一张注定失败的计划。
    """
    params = params if isinstance(params, dict) else {}
    steps = params.get('steps')
    if not isinstance(steps, list) or not steps:
        return None, 'plan 需要 params.steps（1~6 步，按顺序执行）'
    if len(steps) > PLAN_MAX_STEPS:
        return None, f'plan 最多 {PLAN_MAX_STEPS} 步（收到 {len(steps)}）'
    norm = []
    for i, st in enumerate(steps, 1):
        step, err = _norm_plan_step(i, st)
        if err:
            return None, err
        norm.append(step)
    with _lock:
        pending = sum(1 for r in _actions.values() if r['status'] == 'pending')
        if pending >= MAX_PENDING:
            return None, f'待批执行单已达上限（{MAX_PENDING}），请先处理'
        global _order_n
        _order_n += 1
        aid = f'act_{_order_n}'
        lang = get_config().lang
        rec = {'id': aid, 'action': 'plan',
               'title': ACTIONS['plan']['title'][0 if lang == 'zh' else 1],
               'sample': norm[0].get('sample') or '',
               'stages': None, 'params': {}, 'base_params': {},
               'steps': norm, 'step_total': len(norm),
               'reason': str(reason or '')[:800], 'status': 'pending',
               'source': source, 'error': '', 'tid': '', 'ts': time.time(),
               'origin': 'ai'}
        _actions[aid] = rec
    _snapshot_publish(rec, 'pending')
    return rec, None


def _action_artifacts(rec, limit=4):
    """执行单跑完后「点开就能看」的产物：阶段产物 / 工具运行目录 / 整轮报告。

    只在终态 done 时算一次（列表很小，随事件与审计盘一起带走），
    任何异常都返回空——卡片是锦上添花，不能反过来影响执行单收尾。
    """
    try:
        from . import broadcast
        action = str(rec.get('action') or '')
        sample = str(rec.get('result_sample') or rec.get('sample') or '')
        if action in ('rerun_stage', 'retry_failed', 'run_analysis', 'analysis'):
            stages = rec.get('stages') or (rec.get('params') or {}).get('stages') or []
            if isinstance(stages, str):
                stages = [stages]
            stages = [s for s in stages if s]
            # 整轮跑完 / 没指明阶段：直接给报告那一套（与 run_done 同口径）
            if action in ('run_analysis', 'analysis') or not stages:
                return broadcast._sample_artifacts(sample, limit=limit)
            # 合并多阶段的卡片：这里已经是 item（dict）不是路径，
            # 交给 finish 会被当成路径过滤 → 必须按 path 去重后再截断。
            out, seen = [], set()
            for st in stages[:3]:
                for it in broadcast._stage_artifacts(sample, st, limit=limit):
                    if it['path'] not in seen:
                        seen.add(it['path'])
                        out.append(it)
            return broadcast._prefer(out[:limit])
        if action == 'run_tool':
            return broadcast._tool_run_artifacts(
                str(rec.get('tool') or rec.get('params', {}).get('tool') or ''),
                rec.get('ts') or 0, limit=limit)
        if action == 'plan' and sample:
            return broadcast._sample_artifacts(sample, limit=limit)
        return []
    except Exception:
        return []


def _snapshot_publish(rec, status):
    """状态迁移的唯一出口：改状态 → 发事件 → 写审计盘。"""
    rec['status'] = status
    if status == 'done':
        rec['artifacts'] = _action_artifacts(rec)
    else:
        rec.pop('artifacts', None)
    _publish(rec)
    _audit(rec)


def resolve(aid, decision, stages_override=None):
    """批准 / 驳回。返回 (rec, error)。decision: approve | reject。

    stages_override（2026-11-03）：请求里带的阶段列表，批准时用它覆盖 AI
    起草的 stages（仅 rerun_stage / retry_failed / run_analysis / analysis
    支持）；None 表示不覆盖，老调用方行为不变。校验与覆盖都在锁内、
    状态翻转之前完成——校验失败时执行单保持 pending，不会被"批了一半"。
    """
    _hydrate()
    if decision not in ('approve', 'reject'):
        return None, 'decision 只允许 approve / reject'
    with _lock:
        rec = _actions.get(aid)
        if not rec:
            return None, '执行单不存在'
        if rec['status'] != 'pending':
            return None, f'执行单已处理（{rec["status"]}）'
        # 2026-11-03：阶段覆盖只认批准；驳回时忽略。校验必须在置 executing
        # 之前（同一把锁内），否则校验失败的单子已被消费，用户想改也不能再批。
        if decision == 'approve' and stages_override is not None:
            if not isinstance(stages_override, list):
                return None, 'stages 需为字符串数组'
            if not stages_override:
                return None, '阶段列表不能为空'
            norm = []
            for s in stages_override:
                if not isinstance(s, str) or not s.strip():
                    return None, 'stages 需为非空字符串数组'
                s = s.strip()
                if s not in norm:        # 去重保序：重复阶段没有执行意义
                    norm.append(s)
            if rec.get('action') not in ('rerun_stage', 'retry_failed',
                                         'run_analysis', 'analysis'):
                return None, '该动作类型不支持阶段覆盖'
            from Virus_Platform_Core.pipeline import STAGE_ORDER
            bad = [s for s in norm if s not in STAGE_ORDER]
            if bad:
                return None, (f'未知阶段: {", ".join(bad)}'
                              f'（可选: {" / ".join(STAGE_ORDER)}）')
            orig = list(rec.get('stages') or [])
            if norm != orig:
                # 两处读取口径都要命中：_execute → _dispatch 读 rec['stages']，
                # _action_artifacts 还会读 params['stages']。
                rec['stages'] = norm
                rec.setdefault('params', {})['stages'] = norm
                rec['stages_override'] = {'from': orig, 'to': list(norm)}
        # 2026-11-02：状态翻转必须与「是不是 pending」的判定在同一把锁里。
        # 以前校验与置 executing 分两段加锁，双击「批准」时两个请求都看到
        # pending，各起一个执行线程 → 同一动作真的跑了两次。
        rec['status'] = 'rejected' if decision == 'reject' else 'executing'
    if decision == 'reject':
        _publish(rec)
        _audit(rec)
        return rec, None
    # 并发闸：AI 发起的运行全平台同时最多 MAX_AI_RUNS 个
    if _ai_active_count() >= MAX_AI_RUNS:
        with _lock:                      # 放回 pending，让用户稍后再批
            if rec.get('status') == 'executing':
                rec['status'] = 'pending'
        return None, (f'AI 发起的运行已达并发上限（{MAX_AI_RUNS}），'
                      f'请等当前运行结束')
    _snapshot_publish(rec, 'executing')
    threading.Thread(target=_execute, args=(rec,), daemon=True,
                     name=f'ai-exec-{aid}').start()
    return rec, None


def _execute(rec):
    """在后台线程把执行单落到真实任务（与界面按钮同一通道）。"""
    try:
        if rec.get('action') == 'plan':
            _run_plan(rec)          # 自己管状态与等待，逐步骤上报
            return
        tid = _dispatch(rec)
        if rec.get('action') == 'create_sample':
            # 同步动作：_dispatch 返回的是**样品名**而非任务 id，活已干完。
            # 2026-11-02：以前把样品名写进 rec['tid']，AI 拿它去 wait_task
            # 会轮空到超时并报「状态未知」。样品名单独存 result_sample。
            rec['result_sample'] = str(tid)
            _snapshot_publish(rec, 'done')
            return
        rec['tid'] = str(tid)
        _snapshot_publish(rec, 'launched')
        # 2026-11-02 关键修复：单动作执行单以前停在 launched 永不再动
        # （前台只有计划 plan 分支会等终态），卡片永远是「已启动」，
        # AI 只能一直说"还在跑"——这就是「AI 工作台跑不完整」的直接原因。
        # 这里给每条任务型执行单配一个守护线程，跑到终态回写 done/failed。
        if _watcher_kind(rec.get('action')):
            threading.Thread(target=_watch_to_terminal, args=(rec, str(tid)),
                             daemon=True, name=f'ai-watch-{rec.get("id")}'
                             ).start()
    except Exception as e:
        rec['error'] = f'{type(e).__name__}: {e}'[:400]
        _snapshot_publish(rec, 'failed')


def _watcher_kind(action):
    """哪些动作的返回值能被守护线程跟踪到终态。

    tm 任务（analysis / run_tool / build_host_db）→ 'task'；
    下载管理器批次（download_runs）→ 'download'；
    其余（create_sample 同步）→ None（已在上游直接收尾）。
    """
    if action in ('rerun_stage', 'retry_failed', 'run_analysis', 'analysis'):
        return 'task'
    if action in ('run_tool', 'build_host_db'):
        return 'task'
    if action == 'download_runs':
        return 'download'
    return None


def _watch_to_terminal(rec, rid, timeout=6 * 3600, interval=3.0):
    """守护线程：把执行单从 launched 推到真实终态（done / failed）。

    以前只有 plan 会等终态，单动作永远停在 launched，任务失败也不写
    error——卡片看着「已启动」，实际早就崩了，AI 还照旧说「还在跑」。
    这里按动作类型选轮询源，终态必回写，并把任务失败原因带进 rec.error。
    """
    kind = _watcher_kind(rec.get('action'))
    end = time.time() + timeout
    snap = {}
    try:
        if kind == 'download':
            snap = _watch_download(rid, end, interval)
        else:
            from Virus_Platform_Core.web.tasks import tm
            ok = _wait_task(rid, timeout=max(1.0, end - time.time()),
                            interval=interval)
            try:
                snap = tm.snapshot(rid, log_lines=0, include_result=False) or {}
            except Exception:
                snap = {}
            status = str(snap.get('status') or '')
            if ok or status == 'done':
                _snapshot_publish(rec, 'done')
                return
            rec['error'] = (str(snap.get('error') or '').strip()
                            or f'任务 {rid} 未成功（状态: {status or "未知"}）'
                            )[:400]
            _snapshot_publish(rec, 'failed')
            return
    except Exception as e:                      # 守护线程绝不能抛到无人接
        rec['error'] = f'状态跟踪失败: {type(e).__name__}: {e}'[:400]
        _snapshot_publish(rec, 'failed')
        return
    if kind == 'download':
        status = str((snap or {}).get('status') or '').lower()
        if status == 'completed':
            _snapshot_publish(rec, 'done')
        else:
            hint = '（可到「公共数据下载」页续传）' if status == 'interrupted' else ''
            rec['error'] = (str((snap or {}).get('error') or '').strip()
                            or f'下载批次 {rid} 未成功（状态: '
                               f'{status or "未知"}）{hint}')[:400]
            _snapshot_publish(rec, 'failed')


def _watch_download(did, end, interval):
    """轮询下载管理器，直到批次终态（或超时）；返回末次快照。

    状态口径（public_data.DownloadManager）：resolving → downloading →
    [converting] → completed / failed / cancelled / interrupted。
    interrupted 可续传，对执行单而言仍是「未成功」，但在 error 里说清楚。
    """
    from Virus_Platform_Core.web.download import get_dl_manager
    mgr = get_dl_manager()
    snap = {}
    while time.time() < end:
        try:
            snap = mgr.snapshot(did) or {}
        except Exception:
            snap = {}
        st = str(snap.get('status') or '').lower()
        if st in ('completed', 'failed', 'cancelled', 'interrupted'):
            return snap
        time.sleep(interval)
    return snap


def _dispatch(rec):
    """把一个动作落到真实任务，返回任务 id。计划步骤复用同一条分派。"""
    act = rec.get('action')
    if act == 'download_runs':
        return _launch_download(rec)
    if act == 'run_tool':
        return _launch_tool(rec.get('tool'), rec.get('tool_params'))
    if act == 'create_sample':
        return _launch_create_sample(rec.get('params') or {})
    if act == 'build_host_db':
        return _launch_host_db(rec.get('params') or {})
    # 2026-11-02：base_params 以前只写不读，于是「原参数重试」实际按设置页
    # 默认值重跑（assembly_mode / min_contig_len 等被静默重置），AI 却说
    # 「按原参数重试成功」——两轮结果不可比。这里把上次运行参数作底、
    # 显式 params 覆盖（与 _analysis_kwargs「body > 设置页默认」同向）。
    body = dict(rec.get('base_params') or {})
    body.update(rec.get('params') or {})
    return _launch_analysis(rec['sample'], rec.get('stages'), body)


def _wait_task(tid, timeout=6 * 3600, interval=2.0):
    """等一个任务中心任务结束：done → True；failed/cancelled/超时 → False。

    任务 id 始终查不到时快速失败（15 秒宽限）：正常经 tm.start 提交的
    任务文件是同步落盘的，15 秒仍无此任务 = tid 不是任务中心 id（某条
    分派路径改成了同步动作却没在 _run_plan 里跳过等待），轮空 6 小时
    只会把卡片冻在「运行中」。
    """
    from Virus_Platform_Core.web.tasks import tm
    end = time.time() + timeout
    deadline_missing = time.time() + 15
    while time.time() < end:
        try:
            snap = tm.snapshot(tid, log_lines=0, include_result=False) or {}
        except Exception:
            snap = {}
        st = str(snap.get('status') or '')
        if st == 'done':
            return True
        if st in ('failed', 'cancelled'):
            return False
        if not st and time.time() > deadline_missing:
            return False
        time.sleep(interval)
    return False


def _run_plan(rec):
    """按顺序执行计划：一步启动 → 等它 done → 下一步。

    任何一步失败/取消/超时都立刻停下，并把「第几步、什么动作」写进 error，
    不继续往下跑（避免在半成品上继续叠加）。每步开始都发一次事件，对话坞
    的执行单卡片能看到进度（step / step_total）。
    """
    steps = rec.get('steps') or []
    rec['step_total'] = len(steps)
    lang = get_config().lang
    for i, step in enumerate(steps, 1):
        act = step.get('action')
        rec['step'] = i
        rec['step_action'] = act
        rec['step_title'] = ACTIONS.get(act, {}).get(
            'title', ('', ''))[0 if lang == 'zh' else 1]
        rec['status'] = 'running'
        _publish(rec)
        _audit(rec)
        try:
            tid = _dispatch(step)
        except Exception as e:
            rec['error'] = (f'第 {i}/{len(steps)} 步（{act}）启动失败: '
                            f'{type(e).__name__}: {e}')[:400]
            _snapshot_publish(rec, 'failed')
            return
        rec['tid'] = str(tid)
        _publish(rec)
        if act == 'create_sample':
            # 同步动作：_dispatch 返回样品名而非任务 id，没有可等的任务；
            # 拿它去 _wait_task 会把样品名当 tid 轮空到 6 小时超时（事故复现：
            # 卡片停在「运行中」、tasks/ 无新任务、审计盘无下一步记录）。
            continue
        if not _wait_task(str(tid)):
            rec['error'] = (f'第 {i}/{len(steps)} 步（{act}）失败、被取消或超时'
                            f'（任务 {tid}）')
            _snapshot_publish(rec, 'failed')
            return
    _snapshot_publish(rec, 'done')


def _launch_host_db(params):
    """宿主基因组库构建——与「数据库构建」页 /api/build_host_db 同一条路径。"""
    from Virus_Platform_Core.web.build import start_host_db_build
    tid, _out = start_host_db_build(params)
    return tid


def _launch_download(rec):
    """启动一批公共数据下载（与「公共数据下载」页同一管理器/同一队列）。"""
    from Virus_Platform_Core.web.download import get_dl_manager
    params = rec.get('params') or {}
    return get_dl_manager().create(
        str(params.get('name') or 'AI 下载'),
        rec.get('runs') or [],
        concurrency=2,
        convert_sra=bool(params.get('convert_sra', True)))


def _launch_tool(tool, tool_params):
    """运行工具箱工具——与「工具箱」页 /api/tool/run 完全同一套
    「TOOL_REGISTRY job 工厂 + SimpleNamespace ctx + tm.start」路径。

    tool_params 是界面表单口径的键值（值统一转字符串）；缺必填参数时
    job 工厂 abort/raise → 执行单转 failed 并带可读错误，AI 可据此修正
    后重新提案。
    """
    import os
    import uuid
    from types import SimpleNamespace
    from Virus_Platform_Core.config import PLATFORM_ROOT
    from Virus_Platform_Core.utils import check_path, safe_open
    from Virus_Platform_Core.web.state import tool_runs_root as \
        _tool_runs_root, RUN_OUT_LINK
    from Virus_Platform_Core.web.tools_api import TOOL_REGISTRY, \
        _resolve_virus_db, LIGHT_TOOLS
    from Virus_Platform_Core.web.tasks import tm

    spec = TOOL_REGISTRY.get(tool)
    if not spec:
        raise RuntimeError(f'未知工具: {tool}')
    # 表单口径：值统一字符串（job 工厂按字符串读取/strip）
    p = {}
    for k, v in (tool_params or {}).items():
        if v is None:
            continue
        p[str(k)] = v if isinstance(v, str) else json.dumps(
            v, ensure_ascii=False) if isinstance(v, (list, dict)) else str(v)

    def _input_abs(v):
        return check_path(v if os.path.isabs(str(v))
                          else os.path.join(PLATFORM_ROOT, str(v)),
                          must_exist=True)

    def _req(key, what):
        v = str(p.get(key) or '').strip()
        if not v:
            raise RuntimeError(f'缺少必填参数: {what}（{key}）')
        return _input_abs(v)

    def _opt(key):
        v = str(p.get(key) or '').strip()
        return _input_abs(v) if v else None

    db_virus = _resolve_virus_db(p.get('db_virus'))
    if spec.get('need_db'):
        from Virus_Platform_Core.kunpeng import db_ready
        if not db_ready(db_virus):
            raise RuntimeError(f'病毒库未就绪: {db_virus}'
                               '（先到「数据库构建」页构建）')

    ts = time.strftime('%Y%m%d_%H%M%S')
    run_name = f'{tool}_{ts}_{uuid.uuid4().hex[:6]}'
    base_dir = check_path(os.path.join(_tool_runs_root(), run_name),
                          must_exist=False, in_platform=True)
    run_dir = base_dir
    created_here = not os.path.isdir(run_dir)
    os.makedirs(run_dir, exist_ok=True)
    threads = None
    if p.get('threads'):
        try:
            threads = max(1, min(int(p['threads']),
                                 (os.cpu_count() or 4) * 4))
        except (TypeError, ValueError):
            threads = None
    ctx = SimpleNamespace(p=p, run_dir=run_dir, threads=threads,
                          db_virus=db_virus, req=_req, opt=_opt)
    try:
        job = spec['job'](ctx)      # 缺参/文件缺失在这里抛错
    except BaseException:
        # 2026-11-02 P2：校验失败不能留下一个空运行目录。只回收本次
        # 新建且仍为空的目录（os.rmdir 对非空目录直接失败，绝不动
        # 用户已有目录）。
        if created_here:
            try:
                os.rmdir(run_dir)
            except OSError:
                pass
        raise
    name = get_config().tr(f'AI执行·工具·{spec["title"]} {ts}',
                           f'AI tool·{spec.get("title_en", tool)} {ts}')

    def _job_with_run(log, prog, cancel):
        res = job(log, prog, cancel)
        if isinstance(res, dict):
            res.setdefault('run', run_name)
            res['out_dir'] = str(run_dir)
        # 模块结果页（与 tools_api 同口径）：AI 执行单发起的工具运行
        # 完成时也出 results.html。失败只跳过，不影响任务。
        try:
            from Virus_Platform_Core.report_kit import module_page
            module_page.build_tool_run_page(str(run_dir), logger=log)
        except Exception as _mp_e:
            log(f"模块结果页生成失败（跳过）: {type(_mp_e).__name__}: {_mp_e}")
        try:
            os.utime(base_dir)
        except OSError:
            pass
        return res

    tid = tm.start(name, _job_with_run,
                   log_file=os.path.join(run_dir, 'run.log'),
                   weight=('light' if tool in LIGHT_TOOLS else 'heavy'),
                   out_dir=str(run_dir), origin='ai')
    _mark_ai_task(tid)              # 英文界面任务名不再以 'AI执行·' 开头
    return tid


def _launch_create_sample(params):
    """创建样品（与 /api/pipeline/create 同一套校验与落盘）。"""
    import os as _os
    from Virus_Platform_Core.utils import check_path, safe_sample_name
    from Virus_Platform_Core.pipeline import save_sample_input
    from Virus_Platform_Core.web.samples import _default_sample_name
    r1 = str(params.get('r1') or '').strip()
    r2 = str(params.get('r2') or '').strip()
    typed = str(params.get('sample') or '').strip()
    check_path(r1, must_exist=True)
    if r2:
        check_path(r2, must_exist=True)
    sample = safe_sample_name(typed or _default_sample_name(r1))
    sd = check_path(_os.path.join(DIRS['results'], sample),
                    must_exist=False, in_platform=True)
    if _os.path.isdir(sd) and any(_os.scandir(sd)):
        raise RuntimeError(f'样品 {sample} 已存在，请换一个样品名')
    _os.makedirs(sd, exist_ok=True)
    save_sample_input(sd, r1, r2 or None, sample,
                      project=str(params.get('project') or '') or None)
    return sample


# ------------------------------------------------------------------
# 执行器：复用与界面按钮完全相同的启动路径
# ------------------------------------------------------------------
def _mark_ai_task(tid):
    """登记本进程内由 AI 启动的任务 id（语言无关的并发计数依据）。"""
    tid = str(tid or '').strip()
    if not tid:
        return
    if len(_AI_TIDS) > 5000:          # 长跑进程防无界增长（只影响兜底精度）
        _AI_TIDS.clear()
    _AI_TIDS.add(tid)


def _count_ai_active(tasks):
    """数「AI 发起且进行中」的任务。

    判定优先级：记录上的 origin='ai' → 本进程登记过的任务 id →
    历史记录的中文名前缀 'AI执行·'（英文界面任务名是 "AI run·…"，
    只靠前缀会让并发上限形同虚设）。纯函数，便于单测直接喂记录。
    """
    n = 0
    for t in (tasks or []):
        if not isinstance(t, dict) or t.get('status') != 'running':
            continue
        tid = str(t.get('id') or '')
        if (t.get('origin') == 'ai'
                or tid in _AI_TIDS
                or str(t.get('name') or '').startswith('AI执行·')):
            n += 1
    return n


def _ai_active_count():
    try:
        from Virus_Platform_Core.web.tasks import tm
        return _count_ai_active(tm.list_all(logs=False) or [])
    except Exception:
        return 0


def _launch_analysis(sample, stages, body):
    """启动一次样品分析（run_analysis），返回 tm 任务 id。

    注意：这里与 /api/analyze、批处理队列走的是同一套
    「_sample_busy_register + tm.start + TaskLogger + run_analysis」；
    body 是 _analysis_kwargs 口径的参数请求体。
    """
    import os
    from Virus_Platform_Core.utils import (check_path, resolve_sample_name,
                                           TaskLogger)
    from Virus_Platform_Core.web.samples import (_analysis_kwargs,
                                                 _sample_busy_register,
                                                 _sample_busy_release)
    from Virus_Platform_Core.web.tasks import tm

    s = resolve_sample_name(sample, DIRS['results'])
    sd = check_path(os.path.join(DIRS['results'], s), must_exist=True,
                    in_platform=True)
    from Virus_Platform_Core.pipeline import (load_sample_input,
                                              DEFAULT_ANALYZE_STAGES)
    r1, r2, _proj = load_sample_input(sd)
    if not r1:
        raise RuntimeError(f'样品 {s} 缺少输入记录（input.json），无法运行')
    check_path(r1, must_exist=True)
    if not _sample_busy_register(s):
        raise RuntimeError(f'样品 {s} 已有任务在运行')
    st = [x for x in (stages or DEFAULT_ANALYZE_STAGES)]
    kwargs = _analysis_kwargs(dict(body or {}))

    def job(log, prog, cancel):
        from Virus_Platform_Core.pipeline import run_analysis
        logger = TaskLogger(callback=log)
        try:
            return run_analysis(sample=s, r1=r1, r2=r2, stages=st,
                                logger=logger, progress=prog, **kwargs)
        finally:
            _sample_busy_release(s)
            logger.close()

    log_dir = check_path(os.path.join(sd, 'logs'), must_exist=False,
                         in_platform=True)
    os.makedirs(log_dir, exist_ok=True)
    try:
        tid = tm.start(get_config().tr(f'AI执行·{s}', f'AI run·{s}'), job,
                       log_file=os.path.join(
                           log_dir,
                           f'ai_run_{time.strftime("%Y%m%d_%H%M%S")}.log'),
                       origin='ai')
    except BaseException:
        _sample_busy_release(s)      # 任务没起来也要放掉占位
        raise
    _mark_ai_task(tid)               # 英文界面是 "AI run·…"，靠登记而非前缀
    return tid


# ------------------------------------------------------------------
# 第二级 / 第三级：失败后的自动决策
# ------------------------------------------------------------------
def after_failure(sample, stage, error=''):
    """broadcast.note_stage_failed 的执行钩子：按开关走 Tier2/Tier3。

    Tier3（自动调参）优先；未开则 Tier2（原参数重试）；都关则什么都不做。
    Tier3 需要 AI 调参（秒级~几十秒），放后台线程。
    """
    ai = _ai_cfg()
    if not ai.get('enabled'):
        return
    if ai.get('exec_auto_tune'):
        threading.Thread(target=_auto_tune_run, args=(sample, stage, error),
                         daemon=True,
                         name=f'ai-tune-{sample}-{stage}').start()
    elif ai.get('exec_auto'):
        _auto_retry(sample, stage)


def _auto_retry(sample, stage):
    """第二级：原参数重跑失败阶段（每样品每阶段限 1 次）。"""
    with _lock:
        n = _auto_retried.get((sample, stage), 0)
        if n >= AUTO_RETRY_MAX_PER_STAGE:
            return None
        _auto_retried[(sample, stage)] = n + 1
    try:
        base, stages = _last_run_params(sample)
        rec, err = create_action('retry_failed', sample, stages=stages,
                                 params=None, reason='阶段失败，自动重试'
                                 '（第二级，原参数）', source='auto2',
                                 base_params=base)
        if err or not rec:
            return None
        rec2, err2 = resolve(rec['id'], 'approve')
        if err2:
            # 并发满等瞬态原因：执行单留在 pending 等人处理，不强行改状态
            import sys
            sys.stderr.write(f'[ai_agent.executors] 自动重试未启动: {err2}\n')
            return None
        return rec2
    except Exception as e:
        import sys
        sys.stderr.write(f'[ai_agent.executors] 自动重试失败（静默）: '
                         f'{type(e).__name__}: {e}\n')
        return None


def _auto_tune_run(sample, stage, error):
    """第三级：AI 提议参数 → 自动执行（每样品限 1 次；失败回落第二级）。"""
    ai = _ai_cfg()
    with _lock:
        n = _auto_tuned.get(sample, 0)
        if n >= AUTO_TUNE_MAX_PER_SAMPLE:
            return
        _auto_tuned[sample] = n + 1
    try:
        base, stages = _last_run_params(sample)
        proposal = _ai_propose_params(sample, stage, error, base)
        if not proposal:
            if ai.get('exec_auto'):
                _auto_retry(sample, stage)       # 调参不成回落原参数重试
            return
        rec, err = create_action('rerun_stage', sample, stages=stages,
                                 params=proposal.get('params'),
                                 reason=proposal.get('reason', ''),
                                 source='auto3', base_params=base)
        if err or not rec:
            return
        resolve(rec['id'], 'approve')
    except Exception as e:
        import sys
        sys.stderr.write(f'[ai_agent.executors] 自动调参失败（静默）: '
                         f'{type(e).__name__}: {e}\n')


def _ai_propose_params(sample, stage, error, base):
    """让 AI 在 TUNABLE_PARAMS 边界内提参；输出必须是 JSON，非法即放弃。"""
    from . import digest
    from .provider import ai_ready
    ai = _ai_cfg()
    if not ai_ready(ai):
        return None
    d = digest.sample_digest(sample)
    if d is None:
        return None
    dj = json.dumps({'stage': stage, 'error': str(error)[:400],
                     'current_params': base, 'digest': d},
                    ensure_ascii=False)
    if len(dj) > 12000:
        dj = dj[:12000] + '…(truncated)'
    prompt = (
        '你是植物病毒分析平台的自动调参器。一个阶段失败了，请提出**重跑参数'
        '**。只允许这些键（含义与边界）：assembly_mode（rnaviral/metaviral/'
        'rna/meta/isolate/srna）、min_contig_len（50~5000）、confidence'
        '（0~1）、subsample（0~5000000）、subsample_mode（prefix/random/'
        'proportion：前 N 对/等概率随机 N 对/按比例随机）、subsample_prop'
        '（0~1，仅 proportion 用，如 0.5=抽 50%，条数精确）、subsample_seed'
        '（1~2147483647，random/proportion 复现用）、min_orf_aa（30~1000）、'
        'top_n_refs（5~50）。只改确有依据的键，其余不要出现。\n'
        '输出**纯 JSON**（无 markdown 围栏）：{"params": {...}, '
        '"reason": "≤120字"}。失败原因与当前参数、结果摘要：\n'
        + dj)
    from .provider import chat_stream
    out = []
    try:
        for ev in chat_stream(ai, [{'role': 'user', 'content': prompt}],
                              tools=None, max_tokens=400, timeout=(15, 60)):
            if ev.get('type') == 'delta':
                out.append(ev['content'])
    except Exception:
        return None
    text = ''.join(out).strip()
    # 剥掉可能的围栏
    if text.startswith('```'):
        text = text.strip('`').lstrip('json').strip()
    try:
        obj = json.loads(text[text.index('{'):text.rindex('}') + 1])
        params = obj.get('params')
        if not isinstance(params, dict):
            return None
        norm, err = validate_params('rerun_stage', params)
        if err or not norm:
            return None
        return {'params': norm,
                'reason': str(obj.get('reason') or '')[:200]}
    except (ValueError, json.JSONDecodeError):
        return None


def _last_run_params(sample):
    """取上一轮运行的参数（project manifest）与待跑阶段清单。

    返回 (body_dict_for_analysis_kwargs, stages)。manifest 键与
    _analysis_kwargs 请求体键存在历史差异（memory_gb→memory、
    do_specificity→specificity），这里做映射。
    """
    import os
    from Virus_Platform_Core.config import PLATFORM_ROOT
    from Virus_Platform_Core.utils import resolve_sample_name
    from Virus_Platform_Core.pipeline import (load_project_manifest,
                                              pipeline_overview)
    try:
        real = resolve_sample_name(sample, DIRS['results'])
        sd = os.path.join(DIRS['results'], real)
        man = load_project_manifest(sd) or {}
    except Exception:
        man = {}
    p = dict(man.get('params') or {})
    body = {}
    keymap = {'memory_gb': 'memory', 'do_specificity': 'specificity'}
    for k, v in p.items():
        if k in ('db_host', 'db_virus', 'chunk_dir'):
            continue                      # 绝对路径类：留给 _analysis_kwargs 缺省
        body[keymap.get(k, k)] = v
    try:
        ov = pipeline_overview(sd)['stages']
        stages = [x['stage'] for x in ov
                  if x['stage'] != 'subsample'
                  and x.get('status') not in ('done', 'unavailable')]
    except Exception:
        stages = None
    return body, (stages or None)
