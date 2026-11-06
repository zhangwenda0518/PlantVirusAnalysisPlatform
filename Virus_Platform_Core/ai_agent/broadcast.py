# -*- coding: utf-8 -*-
"""主动播报与决策闸门：AI 代理从"被动应答"升级为"主动驱动"的三层实现。

    第一层 讲解（零 token）：note_stage_start —— 阶段开始时推一张"这一步在
            做什么"的卡片，文案来自 STAGE_NOTES 静态知识库（中英双语）。
    第二层 解读（少量 token）：note_stage_done / note_stage_failed /
            note_run_done —— 关键节点收尾后，后台线程取该样品 digest 调
            AI 生成"结果速读 + 注意 + 下一步"，发布为 ai_read 事件；AI 不
            可用时静默跳过，**绝不阻塞流水线**。
    第三层 决策（人在回路）：gate_point —— 在配置的关键阶段收尾后生成 AI
            评估并**阻塞等待用户确认**（继续 / 终止 / 超时自动继续）。AI
            只有建议权没有执行权：不能替用户改参数、不能自动重跑。

事件总线：进程内环形缓冲（最近 MAX_EVENTS 条，单调 id），web 层经
/api/ai/events 消费（SSE 常驻流或 JSON 轮询快照）。CLI 跑管道同样经过
这里——事件发进进程内存无人消费即自然消散，无副作用。

**审计留痕**：每条事件同时异步落盘（`ai_agent/audit.py`，按天轮转 JSONL）。
内存环形缓冲重启即失，而"AI 播报过什么、闸门何时弹的、用户选了继续还是
终止"属审计范畴 —— 跨重启可用 `history()` 读回，或走 GET /api/ai/audit。

安全边界：
  - 所有 AI 调用都是只读解读；闸门只提供"继续/终止"两值，没有
    "AI 替你改参数重跑"的通道；
  - 解读线程全部 daemon + 异常静默，任何 AI 故障都不影响分析任务本身；
  - 闸门默认关闭（cfg.ai['gate']），开启后也只在配置的阶段生效，且
    必须有超时兜底（用户走开管道不会永久卡死）。
"""
import itertools
import json
import os
import sys
import threading
import time

from Virus_Platform_Core.config import get_config

# ------------------------------------------------------------------
# 事件总线
# ------------------------------------------------------------------
MAX_EVENTS = 100
_lock = threading.Lock()
_events = []                      # 旧 → 新
_next_id = itertools.count(1)

# 令客户端点亮未读红点的事件类型（ai_gate 仅 pending 态）
ATTENTION_TYPES = ('ai_read', 'stage_failed', 'ai_gate')


# 子进程事件回传（2026-11-02）：对话坞发起的跑批跑在 MCP 子进程里
# （mcp_console 用 stdio 拉起 mcp_server.py），它的环形缓冲与网页进程
# 不是同一个 —— 阶段卡因此收不到。父进程拉起子进程时注入
# VP_EVENT_SPOOL=<目录>，子进程把每条 publish 追加成一行 JSON；网页进程的
# relay 线程（ai_agent/spool_relay.py）tail 这个目录，再 ingest 进本地缓冲。
SPOOL_ENV = 'VP_EVENT_SPOOL'
_spool_lock = threading.Lock()


def spool_dir():
    """当前进程的事件回传目录（未设置 = 不回传，返回 ''）。"""
    return (os.environ.get(SPOOL_ENV) or '').strip()


def publish(ev_type, _ts=None, **fields):
    """发布一条事件（返回带 id 的完整事件 dict）。

    事件**同时**写入审计盘（`ai_agent/audit.py`）：内存环形缓冲只有
    MAX_EVENTS 条且重启即失，而"AI 播报过什么、闸门什么时候弹的"属审计
    范畴，必须能事后追溯。落盘在后台线程，不阻塞流水线。

    `_ts`：保留原始时间戳（relay 回放子进程事件时用，别让它看起来像刚才发生）。
    `relay=True`：这条事件是从子进程回传的 —— 不再往 spool 写（防回环）。
    """
    ev = {'id': next(_next_id), 'type': ev_type,
          'ts': _ts if _ts else time.time()}
    ev.update(fields)
    with _lock:
        _events.append(ev)
        if len(_events) > MAX_EVENTS:
            del _events[:len(_events) - MAX_EVENTS]
    # relay 事件在子进程里已经写过审计盘，别重复记一遍
    if not ev.get('relay'):
        _audit(ev)
    try:
        _spool(ev)
    except (OSError, TypeError, ValueError):   # 回传失败绝不影响发布本身
        pass
    return ev


def _spool(ev):
    """把事件追加到本进程的回传文件（仅子进程设置 VP_EVENT_SPOOL 时）。"""
    d = spool_dir()
    if not d or ev.get('relay'):
        return
    try:
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, '%d.jsonl' % os.getpid())
        line = json.dumps(ev, ensure_ascii=False) + '\n'
        with _spool_lock:
            with open(path, 'a', encoding='utf-8') as f:
                f.write(line)
    except OSError:
        pass


def ingest(ev):
    """把子进程回传的事件并进本地缓冲（保留原 ts，标记 relay=True）。

    返回新事件 dict；无效输入返回 None。幂等性由 relay 的读取水位保证。
    """
    if not isinstance(ev, dict):
        return None
    ev = dict(ev)
    t = ev.pop('type', None)
    if not t or ev.get('relay'):
        return None
    ts = ev.pop('ts', None)
    ev.pop('id', None)
    return publish(t, _ts=ts, relay=True, **ev)


def _audit(ev):
    """事件 → 审计盘。审计故障绝不能影响事件发布本身。"""
    try:
        from . import audit
        audit.record('events', ev)
    except ImportError:
        # audit.record 自身「永不抛异常」是它的契约；这里只兜 import 失败。
        pass


def history(limit=200, day=None):
    """从审计盘读回历史事件（**新 → 旧**）。

    与 `events_since` 的分工：后者是**进程内**的 SSE 水位线游标（id 单调、
    重启归零），只服务实时流；本函数是**跨重启**的长期留痕查询。
    """
    try:
        from . import audit
        return audit.read('events', limit=limit, day=day)
    except Exception:
        return []


def events_since(last_id):
    """取 id 大于 last_id 的事件快照（旧→新）。"""
    with _lock:
        return [dict(e) for e in _events if e['id'] > (last_id or 0)]


def current_max_id():
    """当前已分配的最大事件 id（SSE 连接瞬间取快照，用于区分
    「连接前发生的历史事件（backlog）」与「连接后实时到达的事件」——
    前者重放时不应触发闸门自动弹坞这类强打扰动作）。"""
    with _lock:
        return _events[-1]['id'] if _events else 0


def _ai_cfg():
    return get_config().ai or {}


def _lang():
    return get_config().lang


def _stage_disp(stage):
    """阶段显示名（懒导入 pipeline，避免 config→pipeline 环）。"""
    try:
        from Virus_Platform_Core.pipeline import (STAGE_NAMES, STAGE_NAMES_EN)
        return ((STAGE_NAMES_EN if _lang() == 'en' else STAGE_NAMES)
                .get(stage) or STAGE_NAMES.get(stage) or stage)
    except Exception:
        return stage


# ------------------------------------------------------------------
# 第一层：讲解（静态知识库，零 token）
# ------------------------------------------------------------------
# 每阶段一句"在做什么/为什么/产出什么"。（zh, en）
STAGE_NOTES = {
    'subsample': ('子采样：从原始 FASTQ 抽一小部分 reads 先探路——prefix=取前 N 对（最快，但可能偏向 lane/批次开头）；random=蓄水池等概率随机抽 N 对；proportion=按 subsample_prop 比例随机抽（如 0.5=50%，条数精确）。random/proportion 同 seed 完全可复现，适合代表性抽检；确认可行后再跑全量。',
                  'Subsampling: pilot on a small slice of the FASTQ — prefix = first N pairs (fastest, but may skew toward the start of the lane/batch); random = reservoir-sampled N pairs with uniform probability; proportion = random draw of subsample_prop (e.g. 0.5 = 50%, exact count). random/proportion are seed-reproducible; run the full set once validated.'),
    'fastp': ('⓪ 质控：fastp 去接头、过滤低质量 reads。⚠ sRNA 测序务必把最短读长设 ≤18，默认 50 会把小 RNA 全部滤掉。产出 clean reads 与质控报告。',
              'Stage ⓪ QC: fastp trims adapters and filters low-quality reads. Keep min length ≤18 for sRNA libraries. Outputs clean reads and a QC report.'),
    'fq2fa': ('⓪b 格式转换：FASTQ→FASTA，供分类引擎提速（分类只读序列本身，不需要质量值）。',
              'Stage ⓪b conversion: FASTQ to FASTA to speed up classification (classifiers read sequence only, not qualities).'),
    'host': ('① 宿主去除：kunpeng 用宿主分类库把寄主 reads 分流掉，留下非宿主 reads——病毒信号从此不再被植物基因组淹没。宿主占比本身也是重要的质控指标。',
             'Stage ① host removal: kunpeng classifies and separates host reads using the host database, so viral signal is no longer drowned by the plant genome. Host fraction is itself a QC metric.'),
    'kvsuite': ('②b 已知病毒识别与定量：salmon 把非宿主 reads 对到病毒参考库并定量——这是"样本里有哪些已知病毒、各有多少"的第一手答案，检出表会喂给下游组装与进化分析。',
                'Stage ②b known-virus ID & quantification: salmon maps non-host reads against the virus reference library — the first answer to "which known viruses and how much"; feeds assembly and phylogenetics.'),
    'assembly': ('③ 组装：SPAdes rnaviral 模式从病毒 reads 重建基因组 contig，再对每条 contig 做分类定位——这是发现"库里没有的新病毒"的主通道。',
                 'Stage ③ assembly: SPAdes (rnaviral) rebuilds viral contigs from virus reads, then classifies each contig — the main route to viruses absent from the reference library.'),
    'hostana': ('④ 宿主预测：把每条病毒 contig 与 ICTV 宿主知识级联匹配，预测其自然寄主并给出置信度——提示"该病毒可能感染哪些作物"。',
                'Stage ④ host prediction: matches each viral contig against ICTV host knowledge to predict natural hosts with confidence — hints which crops may be at risk.'),
    'verify': ('③b 候选验证：对组装 contig 做 blastx/BFVD/CDD 复核，排除宿主污染与假阳性，把"像病毒"变成"确认是病毒"。',
               'Stage ③b verification: blastx/BFVD/CDD re-screening of contigs to exclude host contamination and false positives — from "looks viral" to "confirmed viral".'),
    'consensus': ('③c 共识与变异：reads 回贴参考生成共识序列并逐位置计数变异——为进化分析和引物设计提供干净的代表性序列。',
                  'Stage ③c consensus & variants: reads are re-aligned to references to build consensus and per-position variant counts — clean representative sequences for downstream use.'),
    'orf': ('⑥ ORF 预测：pyrodigal 在病毒基因组上找编码区，产出 faa/ffn/gff——下一步功能注释的对象。',
            'Stage ⑥ ORF prediction: pyrodigal finds coding regions on viral genomes (faa/ffn/gff) — input for functional annotation.'),
    'orfa': ('⑥b 功能注释：DIAMOND/Pfam/BFVD 三层证据给每个 ORF 定功能——聚合蛋白、外壳蛋白、RNA 依赖的 RNA 聚合酶等标志性蛋白是判定病毒种类的关键。',
             'Stage ⑥b annotation: DIAMOND/Pfam/BFVD assign functions to each ORF — hallmark proteins (polyprotein, CP, RdRp) are key to identifying the virus.'),
    'phylo': ('⑦ 进化树与 SDT：近缘参考多序列比对后建树，并计算成对一致性矩阵——回答"它和哪些病毒聚在一起、亲缘多近、该判哪个种"。',
              'Stage ⑦ phylogeny & SDT: closest references are aligned and a tree built, plus a pairwise identity matrix — where it clusters, how close, which species.'),
    'primer': ('⑧ 引物设计：primer3 在保守区/全长上设计检测引物并做热力学评分——把发现变成可落地的检测方法。',
               'Stage ⑧ primer design: primer3 designs detection primers on conserved regions (or full length) with thermodynamic scoring — turning a discovery into an assay.'),
    'gbdraw': ('⑨ 基因组图：把注释画成圈图/线图——一眼看清基因组结构，报告与投稿都用得上。',
               'Stage ⑨ genome plots: circle/linear maps of the annotated genome — for reports and publications.'),
    'report': ('⑩ 可视化报告：汇总全部产物生成可交互的 HTML 报告——样品分析的一站式结论页。',
               'Stage ⑩ report: gathers all products into an interactive HTML report — the one-stop conclusions page.'),
}


def note_stage_start(sample, stage):
    """层1：阶段开始 → 讲解卡片（静态文案，不调 AI）。"""
    if not _ai_cfg().get('explain', True):
        return
    note = STAGE_NOTES.get(stage)
    if not note:
        return
    publish('stage_start', sample=sample, stage=stage,
            name=_stage_disp(stage), note=note[0] if _lang() == 'zh' else note[1])


# ------------------------------------------------------------------
# 第二层：解读（关键节点 AI 播报，daemon 线程，静默失败）
# ------------------------------------------------------------------
BROADCAST_STAGES_DEFAULT = ['kvsuite', 'assembly', 'report']

_PROMPT_ZH = (
    '你是植物病毒分析平台的结果播报员。流水线一个阶段刚完成，请基于注入的'
    '样品 digest 写一段**简短播报**，供分析者在对话流里快速扫一眼：\n'
    '1. 结果速读（≤150 字，点出最关键的数字与发现）；\n'
    '2. 注意（≤2 条，数据里值得警惕的点；没有就写"无明显异常"）；\n'
    '3. 下一步（≤2 条，映射到平台具体阶段/模块）。\n'
    '铁律：数字只允许来自 digest；不编造；总长 ≤300 字；直接输出正文，'
    '不要客套。阶段：{stage}。\n'
    'digest：\n```json\n{digest}\n```')

_PROMPT_EN = (
    'You are the result broadcaster of VirusPlatform. A pipeline stage just '
    'finished. Based on the injected sample digest, write a SHORT briefing '
    'for a quick scan:\n'
    '1. Result digest (<=150 chars-ish, key numbers and findings);\n'
    '2. Watch-outs (<=2 items from the data; write "none" if clean);\n'
    '3. Next steps (<=2 items mapped to concrete platform stages/modules).\n'
    'Hard rules: numbers must come from the digest only; never invent; '
    '<=300 words total; output the briefing only. Stage: {stage}.\n'
    'Digest:\n```json\n{digest}\n```')


def _ai_text(ai, sys_prompt, user_msg, max_tokens=700):
    """单轮补全（无工具、无流式消费方），返回全文；失败返回 None。

    可靠性：后台播报面对的是"没人盯着"的场景，瞬时故障（网络抖动/429）
    直接放弃会丢播报——这里自动重试 1 次（3s 退避）；并使用**紧超时**
    （15s 连接 / 60s 块间读取）：层3 闸门在管道线程内同步等评估，绝不能
    被一个挂起的上游拖住几分钟。
    """
    from .provider import chat_stream
    msgs = [{'role': 'system', 'content': sys_prompt},
            {'role': 'user', 'content': user_msg}]
    last_err = None
    for attempt in (1, 2):
        try:
            out = []
            for ev in chat_stream(ai, msgs, tools=None, max_tokens=max_tokens,
                                  timeout=(15, 60)):
                if ev.get('type') == 'delta':
                    out.append(ev['content'])
            text = ''.join(out).strip()
            if text:
                # 播报/闸门是 AI 文本的另一个出口：漏出的内部工具标记
                # <|DSML|…> 会原样显示在播报卡里（2026-11-02 统一清洗）。
                try:
                    from .chat import _strip_dsml
                    text = _strip_dsml(text).strip()
                except ImportError:
                    pass
                return text or None
            last_err = RuntimeError('空响应')
        except Exception as e:
            last_err = e
        if attempt == 1:
            time.sleep(3)
    sys.stderr.write(f'[ai_agent.broadcast] AI 调用失败（重试后放弃）: '
                     f'{type(last_err).__name__}: {last_err}\n')
    return None


def _interpret_run(sample, stage, failure=False, error=''):
    """层2 工作线程：digest → AI 播报 → ai_read 事件。任何异常静默。"""
    try:
        from . import digest
        from .provider import ai_ready
        ai = _ai_cfg()
        if not ai_ready(ai):
            return
        d = digest.sample_digest(sample)
        if d is None:
            return
        dj = json.dumps(d, ensure_ascii=False)
        if len(dj) > 12000:
            dj = dj[:12000] + '…(truncated)'
        tpl = _PROMPT_ZH if _lang() == 'zh' else _PROMPT_EN
        if failure:
            dj = f'{{"stage_error": {json.dumps(str(error)[:600], ensure_ascii=False)}, "digest": {dj}}}'
        text = _ai_text(ai, tpl.format(stage=stage, digest=dj),
                        '请输出播报。' if _lang() == 'zh' else 'Output the briefing.')
        if text:
            publish('ai_read', sample=sample, stage=stage,
                    name=_stage_disp(stage), text=text[:4000], failure=failure,
                    artifacts=_text_artifacts(text))
    except Exception as e:                     # 播报绝不能影响流水线
        sys.stderr.write(f'[ai_agent.broadcast] 播报失败（静默）: '
                         f'{type(e).__name__}: {e}\n')


# ------------------------------------------------------------------
# 产物卡片（2026-11-02）：播报里只给路径，对话坞把它渲染成可点开的卡片。
# 这里只读落盘文件、绝不抛异常——卡片是锦上添花，不能反过来影响播报。
# ------------------------------------------------------------------
def _text_artifacts(text, limit=6):
    """播报正文里提到的平台内文件 → 卡片（越界路径由 artifacts 过滤）。"""
    try:
        from . import artifacts as _art
        return _art.finish(_art.from_text(str(text or '')), limit=limit)
    except Exception:
        return []


# 阶段/目录级产物：只挑「点开就能看」的（html/svg/png/tsv），
# 不把 summary.json、日志这类长文本铺满对话坞。
_ART_EXT = {'.html': 0, '.htm': 0, '.pdf': 1, '.png': 2, '.jpg': 2,
            '.jpeg': 2, '.svg': 2, '.webp': 2, '.gif': 2,
            '.tsv': 3, '.csv': 3}
#: 模块结果页文件名（_prefer 把它排在阶段卡片最前）
OUT_PAGE = 'results.html'
# 已知的"每 contig 一份"明细表：数量多、单看意义小，做卡片只会挤掉主表
_ART_DENY = ('.poscounts.tsv',)


def _candidates(root, max_depth=2):
    """目录下（含最多 max_depth 层子目录）值得做成卡片的文件。

    排序键 (prio, depth, path)：**浅的先**。实测 ③c 的 poscounts/ 子目录里
    有 20 个按 contig 命名的表，深层的若排在前面会把 coverage.tsv 挤掉；
    另外每个目录最多贡献 2 张表，防止单个子目录刷屏。
    """
    out = []
    root = os.path.abspath(root)
    for dirpath, dirnames, filenames in os.walk(root):
        rel = os.path.relpath(dirpath, root)
        depth = 0 if rel == '.' else rel.count(os.sep) + 1
        if depth >= max_depth:
            dirnames[:] = []                  # 不再下钻
        dirnames[:] = sorted(dirnames)[:12]    # 目录数封顶，防超大运行目录
        for fn in filenames:
            low = fn.lower()
            if low.endswith(_ART_DENY):
                continue
            prio = _ART_EXT.get(os.path.splitext(fn)[1].lower())
            if prio is None:
                continue
            # 名字小写参与排序：否则 'NODE_10…tsv' 会排在 'coverage.tsv' 前面
            out.append((prio, depth, fn.lower(),
                        os.path.join(dirpath, fn)))
    out.sort()
    return [(prio, depth, path) for prio, depth, _n, path in out]


def _pick(cands, limit, per_subdir=2):
    """按 (prio, depth, 名字) 取前 limit 个；子目录里的表每目录最多 2 张。"""
    picked, counts = [], {}
    for prio, depth, path in cands:
        if prio >= 3 and depth >= 1:          # 只管子目录，阶段自己的根目录放宽
            d = os.path.dirname(path)
            if counts.get(d, 0) >= per_subdir:
                continue
            counts[d] = counts.get(d, 0) + 1
        picked.append(path)
        if len(picked) >= limit:
            break
    return picked


def _prefer(items):
    """模块结果页/结论文档排最前：同 kind 内按文件名排序会把
    report_v2 排到 report 之后、results.html 沉底。"""
    def _rank(x):
        n = x.get('name') or ''
        if n == OUT_PAGE:
            return 0
        if n == 'report_v2.html':
            return 1
        return 2
    items.sort(key=_rank)
    return items


def _dir_artifacts(root, limit=4):
    """某个产物目录里最值得点开的文件（越界/不存在由 artifacts 过滤）。"""
    try:
        from . import artifacts as _art
        if not root or not os.path.isdir(root):
            return []
        paths = _pick(_candidates(root)[:40], max(limit * 3, 8))
        return _prefer(_art.finish(paths, limit=limit))
    except Exception:
        return []


def _stage_artifacts(sample, stage, limit=3):
    """单阶段完成时给这个阶段的产物卡片（阶段目录映射以 report_kit 为准）。"""
    try:
        from Virus_Platform_Core.config import DIRS
        from Virus_Platform_Core.report_kit.model import STAGE_DIR
        d = STAGE_DIR.get(str(stage or ''))
        if not d:
            return []
        root = os.path.join(DIRS.get('results') or '', str(sample or ''), d)
        return _dir_artifacts(root, limit=limit)
    except Exception:
        return []


def _tool_run_artifacts(tool, since, limit=4):
    """工具箱执行单完成后，找它刚建的那个 run 目录（按 mtime 取最新一个）。"""
    try:
        from Virus_Platform_Core.config import DIRS
        base = DIRS.get('tool_runs') or ''
        for root in (base, os.path.join(base, '_archive')):
            if not os.path.isdir(root):
                continue
            hits = []
            for name in os.listdir(root):
                p = os.path.join(root, name)
                if not os.path.isdir(p):
                    continue
                if tool and not name.startswith(str(tool) + '_'):
                    continue
                try:
                    m = os.path.getmtime(p)
                except OSError:
                    continue
                if m >= float(since or 0) - 5:
                    hits.append((m, p))
            if hits:
                hits.sort()
                return _dir_artifacts(hits[-1][1], limit=limit)
        return []
    except Exception:
        return []


def _sample_artifacts(sample, limit=8):
    """整轮完成时值得直接点开的产物：报告 → 漏斗图 → 进化树 → 基因组图。"""
    try:
        import glob
        from Virus_Platform_Core.config import DIRS
        from . import artifacts as _art
        root = os.path.join(DIRS.get('results') or '', sample)
        if not os.path.isdir(root):
            return []
        paths = [os.path.join(root, '07_report', 'results.html'),
                 os.path.join(root, '07_report', 'report_v2.html'),
                 os.path.join(root, '07_report', 'report.html'),
                 os.path.join(root, '07_report', 'stage_funnel.png')]
        for pat in ('05_phylo/*/tree.png',
                    '07_report/genome_*.png',
                    '09_genome_plots/*.circular.svg'):
            hits = glob.glob(os.path.join(root, *pat.split('/')))
            paths.extend(sorted(hits)[:3])
        items = _art.finish([p for p in paths if os.path.isfile(p)],
                            limit=limit)
        # 模块结果页/结论文档排最前：同 kind 内按文件名排序会把 report_v2
        # 排到 report 之后。list.sort 稳定，其余顺序不动。
        items.sort(key=lambda x: 0 if x.get('name') == 'results.html'
                   else (1 if x.get('name') == 'report_v2.html' else 2))
        return items
    except Exception:
        return []



def note_stage_done(sample, stage):
    """层2 挂点：阶段成功收尾。轻量完成条必发；关键节点另起 AI 播报线程。"""
    publish('stage_done', sample=sample, stage=stage, name=_stage_disp(stage),
            artifacts=_stage_artifacts(sample, stage))
    ai = _ai_cfg()
    if not (ai.get('enabled') and ai.get('broadcast', True)):
        return
    stages = ai.get('broadcast_stages') or BROADCAST_STAGES_DEFAULT
    if stage in stages:
        threading.Thread(target=_interpret_run, args=(sample, stage),
                         daemon=True,
                         name=f'ai-read-{sample}-{stage}').start()


def note_stage_failed(sample, stage, error):
    """层2 挂点：阶段失败。失败事件**必发**（不依赖 AI）；AI 诊断可选；
    随后按执行权开关走 Tier2 自动重试 / Tier3 自动调参（executors）。"""
    publish('stage_failed', sample=sample, stage=stage,
            name=_stage_disp(stage), error=str(error)[:500])
    ai = _ai_cfg()
    if ai.get('enabled') and ai.get('broadcast', True):
        threading.Thread(target=_interpret_run,
                         args=(sample, stage, True, error), daemon=True,
                         name=f'ai-diag-{sample}-{stage}').start()
    try:
        from . import executors
        executors.after_failure(sample, stage, str(error)[:500])
    except Exception as e:              # 执行层故障绝不影响主流程收尾
        import sys
        sys.stderr.write(f'[ai_agent.broadcast] 执行钩子异常（静默）: '
                         f'{type(e).__name__}: {e}\n')


def note_run_done(sample):
    """层2 挂点：整轮分析完成 → 最终总结播报。"""
    publish('run_done', sample=sample, artifacts=_sample_artifacts(sample))
    ai = _ai_cfg()
    if ai.get('enabled') and ai.get('broadcast', True):
        threading.Thread(target=_interpret_run, args=(sample, 'report'),
                         daemon=True, name=f'ai-final-{sample}').start()


# ------------------------------------------------------------------
# 第三层：决策闸门（人在回路；AI 建议权，无执行权）
# ------------------------------------------------------------------
GATE_STAGES_DEFAULT = ['kvsuite', 'assembly']


class GateAborted(RuntimeError):
    """用户在决策闸门选择终止任务（run_analysis 捕获后按正常失败路径收场）。"""


_gates = {}                                # (sample, stage) -> gate state
_gate_lock = threading.Lock()

_ASSESS_ZH = (
    '你是植物病毒分析平台的闸门评审。阶段 {stage} 刚完成，是否继续跑后续'
    '阶段由用户决定，请给出**决策评估**：\n'
    '1. 结论先行：建议继续 / 建议先检查（一句话）；\n'
    '2. 依据（≤3 条，引用 digest 数字）；\n'
    '3. 若建议检查：指出查什么、怎么查（映射到平台模块）。\n'
    '铁律：数字只来自 digest；≤250 字；不编造。'
    'digest：\n```json\n{digest}\n```')
_ASSESS_EN = (
    'You are the gate reviewer of VirusPlatform. Stage {stage} just finished; '
    'the user decides whether to continue. Give a decision brief:\n'
    '1. Conclusion first: continue / check first (one sentence);\n'
    '2. Evidence (<=3 items citing digest numbers);\n'
    '3. If check-first: what to check and where (platform modules).\n'
    'Hard rules: numbers from digest only; <=250 words; never invent.\n'
    'Digest:\n```json\n{digest}\n```')


def _ai_assess(sample, stage):
    """层3 评估生成（阻塞、带 provider 层超时）；失败返回 None → 自动继续。"""
    try:
        from . import digest
        from .provider import ai_ready
        ai = _ai_cfg()
        if not ai_ready(ai):
            return None
        d = digest.sample_digest(sample)
        if d is None:
            return None
        dj = json.dumps(d, ensure_ascii=False)
        if len(dj) > 12000:
            dj = dj[:12000] + '…(truncated)'
        tpl = _ASSESS_ZH if _lang() == 'zh' else _ASSESS_EN
        return _ai_text(ai, tpl.format(stage=stage, digest=dj), max_tokens=600)
    except Exception as e:
        sys.stderr.write(f'[ai_agent.broadcast] 闸门评估失败（自动继续）: '
                         f'{type(e).__name__}: {e}\n')
        return None


def gate_point(sample, stage, logger=None, timeout_s=None):
    """层3 主入口：阶段收尾后调用（管道线程内，阻塞）。

    逻辑：未启用/非配置阶段/AI 不可用 → 直接返回；否则生成评估 → 发布
    pending 闸门事件 → 等用户确认（继续/终止）或超时自动继续。
    返回 'continue'（含超时自动继续）；用户终止则抛 GateAborted。
    timeout_s 参数仅供测试注入，运行时一律走配置的分钟数。
    """
    ai = _ai_cfg()
    if not (ai.get('enabled') and ai.get('gate')):
        return 'continue'
    if stage not in (ai.get('gate_stages') or GATE_STAGES_DEFAULT):
        return 'continue'
    if timeout_s is None:
        try:
            timeout_s = max(1, int(ai.get('gate_timeout_min', 15))) * 60
        except (TypeError, ValueError):
            timeout_s = 900
    assessment = _ai_assess(sample, stage)
    if not assessment:
        if logger:
            logger.log('[AI 闸门] 评估不可用（未配置或生成失败），自动继续',
                       'WARN')
        return 'continue'
    # 2026-11-02 P2：pending 事件带期限，前端闸门卡才能显示倒计时；
    # 刷新页面后 pending_gate() 仍能给出剩余时间（不再只有 eid）。
    deadline = time.time() + timeout_s
    ev = publish('ai_gate', sample=sample, stage=stage,
                 name=_stage_disp(stage), status='pending',
                 text=assessment[:3000],
                 timeout_s=timeout_s, deadline=deadline)
    key = (sample, stage)
    g = {'ev': threading.Event(), 'action': None, 'eid': ev['id'],
         'deadline': deadline, 'timeout_s': timeout_s}
    with _gate_lock:
        _gates[key] = g
    try:
        if logger:
            logger.log(f'[AI 闸门] {_stage_disp(stage)} 完成，决策评估已推送到'
                       f'总览页对话坞（{max(timeout_s // 60, 1)} 分钟内未操作'
                       f'将自动继续）')
        g['ev'].wait(timeout_s)
        action = g['action'] or 'timeout'
    finally:
        with _gate_lock:
            _gates.pop(key, None)
    publish('ai_gate', sample=sample, stage=stage, name=_stage_disp(stage),
            status=action, text=assessment[:3000])
    if action == 'stop':
        if logger:
            logger.log('[AI 闸门] 用户选择终止任务', 'WARN')
        raise GateAborted(f'用户在 AI 决策闸门终止了任务（{_stage_disp(stage)}'
                          f' 之后）；可调整参数后重新运行')
    if logger:
        logger.log('[AI 闸门] ' + ('超时自动继续' if action == 'timeout'
                                   else '用户确认，继续下一阶段'))
    return action


def resolve_gate(sample, stage, action):
    """web 层确认入口：'continue' | 'stop'。无待决闸门/已决 → False。"""
    if action not in ('continue', 'stop'):
        return False
    with _gate_lock:
        g = _gates.get((sample, stage))
        if not g or g['action']:
            return False
        g['action'] = action
        g['ev'].set()
    return True


def pending_gate(sample=None, stage=None):
    """查询待决闸门（前端恢复用）；无参返回全部。"""
    with _gate_lock:
        out = [{'sample': k[0], 'stage': k[1], 'eid': v['eid'],
                'action': v['action'],
                # 刷新后 hydrate 也要能显示倒计时（epoch 秒，数值）
                'deadline': v.get('deadline'),
                'timeout_s': v.get('timeout_s')}
               for k, v in _gates.items()
               if (sample is None or k[0] == sample)
               and (stage is None or k[1] == stage)]
    return out
