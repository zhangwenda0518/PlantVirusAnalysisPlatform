# -*- coding: utf-8 -*-
"""AI 助手 API（对话驱动智能代理的 HTTP 边界）。

路由：
    GET  /api/ai/config   读取 AI 配置（api_key 掩码）+ 提供方预设 + 就绪态
    POST /api/ai/config   保存 AI 配置到 platform.json（逐项校验；空体 400）
    POST /api/ai/test     连通性测试（可用请求体临时覆盖，不必先保存）
    GET  /api/ai/context  平台简况 + 可选样品 digest（对话坞开场上下文）
    GET  /api/ai/audit    AI 代理审计留痕（跨重启读盘：执行单状态变迁 / 事件流）
    POST /api/ai/feedback 回答反馈（👍👎）写入（2026-11-04；空体 400 无副作用）
    GET  /api/ai/feedback 最近反馈原始记录（有限 JSON，只读）
    POST /api/ai/actions/create 前端「建议按钮」直接起草执行单（仍是待批）
    POST /api/ai/chat     对话（SSE 流式；事件见 ai_agent/chat.py）

审计安全（tests/_audit_routes.py 口径）：两条 POST 在空请求体下一律 400
且无任何副作用（不写配置、不发起网络请求），故不进 DESTRUCTIVE_POST。
"""
import json
import re
import time

from flask import (Blueprint, Response, abort, jsonify, request,
                   stream_with_context)

from Virus_Platform_Core.ai_agent import audit
from Virus_Platform_Core.ai_agent import broadcast
from Virus_Platform_Core.ai_agent import chat as ai_chat
from Virus_Platform_Core.ai_agent import digest
from Virus_Platform_Core.ai_agent.provider import (PROVIDER_PRESETS,
                                                   ai_ready, test_connection)
from Virus_Platform_Core.config import (_AI_LOCAL_LOCK, _ai_local_read,
                                        _ai_local_write, _SECRET_ENV_RE,
                                        expand_secret)
from Virus_Platform_Core.web.state import cfg

bp = Blueprint('ai_assist', __name__)

_SECRET = 'api_key'
_DAY_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')


def _masked(ai):
    out = dict(ai or {})
    key = out.get(_SECRET) or ''
    out['api_key_set'] = bool(key)
    # 内部版口径（2026-11-06）：ai.reveal_key=true 时回显明文，便于核对
    # 所配 Key；缺省（False）维持掩码契约（•••• + 尾 4 位）。
    if out.get('reveal_key'):
        out[_SECRET] = key
        return out
    # 只露尾 4 位便于核对是哪把 Key；本体绝不出后端
    out[_SECRET] = ('••••' + key[-4:]) if len(key) > 4 else ('••••' if key else '')
    return out




# ------------------------------------------------------------------
# 供应商档案（ZCode 式多端点并存）：列表/保存/切换/删除。Key 分离存
# run/ai_local.json provider_keys[name]；platform.json 永不落 Key。
# 「切换」= 把档案值灌进平铺 cfg.ai（base_url/model/温度/长度/Key）并
# 落盘——对话坞/执行单/播报全链路立即使用新端点，零重启。
# ------------------------------------------------------------------

def _pf_local_read():
    """读 ai_local.json（与 Config.save 共用 config 的锁/原子写实现）。"""
    with _AI_LOCAL_LOCK:
        return _ai_local_read()


def _pf_local_update(fn):
    """在 `_AI_LOCAL_LOCK` 内完成「读 → 改 → 原子写」，返回改后的 dict。"""
    with _AI_LOCAL_LOCK:
        d = _ai_local_read()
        fn(d)
        _ai_local_write(d)
        return d


@bp.route('/api/ai/profiles', methods=['GET'])
def api_ai_profiles_get():
    keys = _pf_local_read().get('provider_keys') or {}
    active = str(cfg.ai.get('active_profile') or '')
    out = [{'name': p.get('name'), 'base_url': p.get('base_url'),
            'model': p.get('model'), 'temperature': p.get('temperature'),
            'max_tokens': p.get('max_tokens'),
            'key_set': bool(keys.get(p.get('name'))),
            'active': p.get('name') == active}
           for p in (cfg.ai.get('providers') or [])]
    return jsonify({'count': len(out), 'profiles': out, 'active': active})


@bp.route('/api/ai/profiles/save', methods=['POST'])
def api_ai_profiles_save():
    body = request.get_json(silent=True) or {}
    name = str(body.get('name') or '').strip()
    if not name or len(name) > 40 or '/' in name or chr(92) in name:
        abort(400, '档案名须为 1~40 字符，且不含路径分隔符')
    base_url = str(body.get('base_url') or '').strip()
    model = str(body.get('model') or '').strip()
    if not base_url or not model:
        abort(400, 'base_url 与 model 必填')
    try:
        temp = min(2.0, max(0.0, float(body.get('temperature', 0.3))))
    except (TypeError, ValueError):
        abort(400, 'temperature 须为 0~2 的数字')
    try:
        mtok = max(64, min(32768, int(body.get('max_tokens', 2048))))
    except (TypeError, ValueError):
        abort(400, 'max_tokens 须为 64~32768 的整数')
    provs = [p for p in (cfg.ai.get('providers') or [])
             if p.get('name') != name]
    provs.append({'name': name, 'base_url': base_url, 'model': model,
                  'temperature': temp, 'max_tokens': mtok})
    key = str(body.get('api_key') or '').strip()
    # Key 先落 ai_local（失败即 500 中止，内存未动）：此前先改内存再写 Key，
    # 写 Key 失败时内存与磁盘已不一致（2026-11-06 并发收敛）。
    if key and not key.startswith('••••'):     # 掩码回传 = 未修改，保留旧 Key
        def _put(local):
            pk = local.get('provider_keys') or {}
            pk[name] = key
            local['provider_keys'] = pk
        _pf_local_update(_put)
    try:
        cfg.set_ai(dict(cfg.ai, providers=provs))
    except OSError as e:
        abort(500, f'配置保存失败: {e}')
    return jsonify({'ok': True, 'name': name})


@bp.route('/api/ai/profiles/activate', methods=['POST'])
def api_ai_profiles_activate():
    body = request.get_json(silent=True) or {}
    name = str(body.get('name') or '').strip()
    prof = next((p for p in (cfg.ai.get('providers') or [])
                 if p.get('name') == name), None)
    if not prof:
        abort(400, f'档案不存在: {name}')
    raw_key = str((_pf_local_read().get('provider_keys') or {}).get(name, '')
                  or '').strip()
    key = expand_secret(raw_key)
    if not key:
        # 空 Key 档案不能"沿用平铺 cfg.ai 里的旧 Key"：那会把上一次供应商
        # （A）的密钥发给这一份档案（B）的 base_url。更糟的是 Config.save
        # 会把旧 Key 落盘，重启后 _load_extras 继续按 B 的端点使用它。
        env = _SECRET_ENV_RE.fullmatch(raw_key)
        where = f'环境变量 {env.group(1)} 未设置' if env else '环境变量未设置'
        abort(400, f'档案 {name} 没有可用 API Key（{where}）')
    # 让 Config.save 知道内存里的明文对应盘上的占位符，保持零密钥落盘
    # （占位符须先于 set_ai→save 记好，save 读它决定 ai_local 落什么）。
    cfg._ai_key_placeholder = (raw_key
                               if _SECRET_ENV_RE.fullmatch(raw_key) else '')
    try:
        cfg.set_ai(dict(cfg.ai, provider='custom', base_url=prof['base_url'],
                        model=prof['model'],
                        temperature=prof.get('temperature', 0.3),
                        max_tokens=prof.get('max_tokens', 2048),
                        api_key=key, active_profile=name))
    except OSError as e:
        abort(500, f'配置保存失败: {e}')
    return jsonify({'ok': True, 'active': name, 'ready': ai_ready(cfg.ai),
                    'config': _masked(cfg.ai)})


@bp.route('/api/ai/profiles/delete', methods=['POST'])
def api_ai_profiles_delete():
    body = request.get_json(silent=True) or {}
    name = str(body.get('name') or '').strip()
    provs = [p for p in (cfg.ai.get('providers') or [])
             if p.get('name') != name]
    if len(provs) == len(cfg.ai.get('providers') or []):
        abort(400, f'档案不存在: {name}')
    def _drop(d):
        _pk = d.get('provider_keys') or {}
        _pk.pop(name, None)
        d['provider_keys'] = _pk
    # 删除在锁内执行（pop 对不存在的键本就安全）：消除「锁外读判存、锁内删」
    # 与并发写 Key 之间的 TOCTOU（2026-11-06 并发收敛）。
    _pf_local_update(_drop)
    try:
        cfg.set_ai(dict(cfg.ai, providers=provs,
                  active_profile=('' if cfg.ai.get('active_profile') == name
                                  else cfg.ai.get('active_profile'))))
    except OSError as e:
        abort(500, f'配置保存失败: {e}')
    return jsonify({'ok': True})

@bp.route('/api/ai/config', methods=['GET'])
def api_ai_config_get():
    # stage_options：流水线全部阶段（key + 当前语言显示名），供设置页
    # 把「播报阶段 / 闸门阶段」渲染成勾选组而不是手填代码
    try:
        from Virus_Platform_Core.pipeline import (STAGE_ORDER, STAGE_NAMES,
                                                  STAGE_NAMES_EN)
        lang = cfg.lang
        stage_options = [{'key': k,
                          'name': (STAGE_NAMES_EN if lang == 'en'
                                   else STAGE_NAMES).get(k) or k}
                         for k in STAGE_ORDER]
    except Exception:
        stage_options = []
    return jsonify({'config': _masked(cfg.ai),
                    'presets': PROVIDER_PRESETS,
                    'stage_options': stage_options,
                    'ready': ai_ready(cfg.ai)})


def _validate_ai_body(body):
    """校验并归一 AI 配置请求体 → 可合并进 cfg.ai 的 dict（非法项 400）。"""
    if not isinstance(body, dict) or not body:
        abort(400, '请求体须为非空 JSON 对象')
    out = {}
    # 2026-11-06 加固：不再把 enabled 强写为 True。总开关已从 UI 取消，
    # 但盘上显式 false（老配置/手工关闭）必须被尊重；请求体里的 enabled
    # 一律忽略，保持 cfg.ai 现值（merged 基于 cfg.ai）。
    if 'provider' in body:
        p = str(body['provider'] or '').strip()
        if p not in {x['id'] for x in PROVIDER_PRESETS}:
            abort(400, f'未知提供方: {p}')
        out['provider'] = p
    for k in ('base_url', 'model'):
        if k in body:
            out[k] = str(body[k] or '').strip()
    if _SECRET in body:
        v = str(body[_SECRET] or '').strip()
        if v.startswith('••••'):
            # 前端把掩码原样传回 = 未修改，保留已存 Key 不覆盖
            pass
        else:
            # ${ENV_VAR} 占位符先展开：内存态始终是可用明文，而盘上
            # 由 Config.save 按占位符原样写回（零密钥）。展开为空 = 环境
            # 变量没配，当场 400 比"保存成功但 AI 不可用"更可诊断。
            env = _SECRET_ENV_RE.fullmatch(v)
            expanded = expand_secret(v)
            if env and not expanded:
                abort(400, f'环境变量 {env.group(1)} 未设置'
                           f'（Key 保持原值不变）')
            out[_SECRET] = expanded
    if 'temperature' in body:
        try:
            out['temperature'] = min(2.0, max(0.0, float(body['temperature'])))
        except (TypeError, ValueError):
            abort(400, 'temperature 须为 0~2 的数字')
    if 'max_tokens' in body:
        try:
            out['max_tokens'] = max(64, min(32768, int(body['max_tokens'])))
        except (TypeError, ValueError):
            abort(400, 'max_tokens 须为 64~32768 的整数')
    # 主动驱动三层（讲解/解读/决策闸门）——见 ai_agent/broadcast.py
    for k in ('explain', 'broadcast', 'gate', 'exec_auto', 'exec_auto_tune'):
        if k in body:
            out[k] = bool(body[k])
    if 'gate_timeout_min' in body:
        try:
            out['gate_timeout_min'] = max(1, min(120,
                                                 int(body['gate_timeout_min'])))
        except (TypeError, ValueError):
            abort(400, 'gate_timeout_min 须为 1~120 的整数（分钟）')
    for k in ('broadcast_stages', 'gate_stages'):
        if k in body:
            v = body[k]
            if not isinstance(v, list) or not all(
                    isinstance(x, str) for x in v):
                abort(400, f'{k} 须为字符串数组')
            out[k] = [x.strip() for x in v if x.strip()][:15]
    return out


@bp.route('/api/ai/config', methods=['POST'])
def api_ai_config_set():
    body = request.get_json(silent=True)
    merged = dict(cfg.ai)
    patch = _validate_ai_body(body)
    merged.update(patch)
    # 记录用户输入的 ${ENV_VAR} 原样占位符：Config.save 据此继续写占位符，
    # 而不是把展开后的明文 Key 落到 ai_local.json。
    raw_key = ''
    if _SECRET in body:
        v = str(body.get(_SECRET) or '').strip()
        if v and not v.startswith('••••') and _SECRET_ENV_RE.fullmatch(v):
            raw_key = v
    cfg._ai_key_placeholder = raw_key
    # 2026-11-02 P2：手改 model / base_url 后，active_profile 仍指着旧档案
    # → 对话坞显示「● 档案A」而实际跑的是手改配置。档案本身不动，只把
    # 不再匹配的 active_profile 清空（保存），坞据此不再点亮旧档案。
    active = str(merged.get('active_profile') or '')
    if active:
        prof = next((p for p in (merged.get('providers') or [])
                     if p.get('name') == active), None)
        if (not prof
                or str(prof.get('model') or '') != str(merged.get('model') or '')
                or str(prof.get('base_url') or '')
                != str(merged.get('base_url') or '')):
            merged['active_profile'] = ''
    try:
        cfg.set_ai(merged)
    except OSError as e:
        abort(500, f'配置保存失败: {e}')
    return jsonify({'ok': True, 'config': _masked(cfg.ai),
                    'ready': ai_ready(cfg.ai)})


@bp.route('/api/ai/test', methods=['POST'])
def api_ai_test():
    """连通性测试。请求体给要测试的配置（前端"先测再存"），**必须非空**——
    空体一律 400：否则全路由巡检的空 POST 会拿用户已存配置发真实网络请求。"""
    body = request.get_json(silent=True) or {}
    if not body:
        abort(400, '需要请求体（要测试的 AI 配置；留空字段即测试已存值）')
    ai = dict(cfg.ai)
    ai.update(_validate_ai_body(body))
    if not ai.get('api_key'):
        # 测试连接也要先展开 ${ENV_VAR}：展开为空说明不是 Key 错而是环境
        # 变量没设，直接给出可执行的提示，别让用户去猜 401。
        ai['api_key'] = expand_secret(ai.get('api_key'))
    if not ai_ready(ai):
        abort(400, '配置不完整：请填写 Base URL / 模型'
                   '（除本地 Ollama 外还需 API Key）')
    return jsonify(test_connection(ai))


@bp.route('/api/ai/context', methods=['GET'])
def api_ai_context():
    """对话坞开场上下文：样品清单 + 可选的选中样品 digest + 任务概览。"""
    sample = (request.args.get('sample') or '').strip()
    try:
        ctx = {'samples': digest.samples_brief(),
               'tasks': digest.task_overview(8),
               'ready': ai_ready(cfg.ai)}
        if sample:
            d = digest.sample_digest(sample)
            if d is not None:
                ctx['sample'] = d
        return jsonify(ctx)
    except Exception as e:
        return jsonify({'samples': [], 'tasks': [], 'ready': ai_ready(cfg.ai),
                        'error': f'{type(e).__name__}: {e}'})


@bp.route('/api/ai/gate', methods=['POST'])
def api_ai_gate():
    """第三层决策闸门确认：{sample, stage, action: 'continue'|'stop'}。

    空参数 POST 一律 400 且无副作用（全路由巡检安全）。
    """
    body = request.get_json(silent=True) or {}
    sample = str(body.get('sample') or '').strip()
    stage = str(body.get('stage') or '').strip()
    action = str(body.get('action') or '').strip()
    if not sample or not stage or not action:
        abort(400, '需要 sample / stage / action 三项')
    if not broadcast.resolve_gate(sample, stage, action):
        abort(404, '该阶段没有待决的 AI 闸门（可能已超时或已处理）')
    return jsonify({'ok': True})


@bp.route('/api/ai/actions')
def api_ai_actions():
    """AI 执行单清单（有限 JSON，审计/轮询安全）。"""
    from Virus_Platform_Core.ai_agent import executors
    recs = executors.list_actions()
    return jsonify({'pending': [r for r in recs if r['status'] == 'pending'],
                    'recent': recs})


@bp.route('/api/ai/actions/resolve', methods=['POST'])
def api_ai_action_resolve():
    """执行单决策：{id, decision: 'approve'|'reject', stages?: [...]}。

    批准后动作在后台线程经与界面按钮相同的通道执行（executors）。
    可选 stages：批准时用这份阶段列表覆盖 AI 起草的 stages（仅
    rerun_stage / retry_failed / run_analysis / analysis 支持）；键缺省
    表示不覆盖。空参数 POST 一律 400 且无副作用（全路由巡检安全）。
    """
    from Virus_Platform_Core.ai_agent import executors
    body = request.get_json(silent=True) or {}
    aid = str(body.get('id') or '').strip()
    decision = str(body.get('decision') or '').strip()
    if not aid or not decision:
        abort(400, '需要 id 与 decision（approve/reject）')
    # 非数组（如逗号串）不在服务端猜：直接 400，让调用方按契约重发。
    stages = body.get('stages')
    if 'stages' in body and stages is not None \
            and not isinstance(stages, list):
        abort(400, 'stages 需为字符串数组')
    rec, err = executors.resolve(aid, decision, stages)
    if err:
        abort(404, err) if '不存在' in err else abort(400, err)
    return jsonify({'ok': True, 'action': executors.get_action(aid)})


@bp.route('/api/ai/actions/create', methods=['POST'])
def api_ai_action_create():
    """给前端「建议按钮」用（2026-11-04）：模型在回答里给 next_actions，
    用户点击后前端调本端点**直接起草**执行单。

    红线不变：只是起草，用户批准后才会运行（create_action 只产生 pending
    卡片）。请求体 {action, sample, stages, params, reason}；reason 必填，
    与 tools.py 的 propose_action 同一口径——审批卡片上的理由就是用户
    决定批不批的唯一依据。返回回显形状也与 propose_action 分支保持一致。

    空体/非法一律 400 且无副作用（全路由巡检安全）。
    """
    from Virus_Platform_Core.ai_agent import executors
    body = request.get_json(silent=True) or {}
    reason = str(body.get('reason') or '').strip()
    if not reason:
        abort(400, '需要 reason：用一两句话写清「为什么这么做、依据是什么」，'
                   '用户在对话坞凭它决定批不批')
    stages = body.get('stages')
    if stages is not None and not isinstance(stages, list):
        abort(400, 'stages 需为字符串数组')
    params = body.get('params')
    if params is not None and not isinstance(params, dict):
        abort(400, 'params 需为对象')
    try:
        rec, err = executors.create_action(
            str(body.get('action') or '').strip(),
            str(body.get('sample') or ''),
            stages=stages or None, params=params or None,
            reason=reason, source='chat_next')
    except Exception as e:                 # 执行层异常兜底 400，不出 500
        abort(400, f'{type(e).__name__}: {e}')
    if err:
        abort(400, err)
    echo = {'created': True, 'id': rec['id'], 'status': rec['status'],
            'action': rec.get('action'),
            'sample': rec.get('sample') or '',
            'reason': rec.get('reason') or '',
            'note': '执行单已创建，等待用户在对话坞批准；批准前不会运行'}
    if rec.get('stages'):
        echo['stages'] = rec['stages']
    if rec.get('params'):
        echo['params'] = rec['params']
    if rec.get('steps'):
        echo['steps'] = [{'action': s.get('action'),
                          'sample': s.get('sample') or '',
                          'stages': s.get('stages'),
                          'params': s.get('params')}
                         for s in rec['steps']]
    return jsonify(echo)


@bp.route('/api/ai/events')
def api_ai_events():
    """主动播报事件流（双层协议，按客户端类型自动适配）：

    - 带 `Accept: text/event-stream`（前端 EventSource）→ SSE 常驻流：
      先补发 Last-Event-ID 之后的事件，随后实时推送 + 心跳；
    - 普通 GET（巡检/轮询/测试）→ **有限** JSON 快照
      `{events: [...], pending_gates: [...]}`——审计脚本会等完整响应体，
      无限流会把它挂死，这条边界必须守住。?since=<id> 增量取。
    """
    since = request.args.get('since')
    if not since:
        # EventSource 自动重连带的是 Last-Event-ID 请求头（不是查询参数）；
        # 不认它的话每次重连都会从头回放整个环形缓冲（前端虽去重，但浪费）。
        since = request.headers.get('Last-Event-ID')
    try:
        since_id = int(since) if since else 0
    except ValueError:
        since_id = 0
    wants_sse = 'text/event-stream' in (request.headers.get('Accept') or '')
    if not wants_sse:
        return jsonify({'events': broadcast.events_since(since_id),
                        'pending_gates': [g for g in broadcast.pending_gate()
                                          if not g.get('action')]})

    def gen():
        last = since_id
        idle = 0
        started = time.time()
        # 立即冲一行 SSE 注释：werkzeug 在首个非空块产生前不发响应头，
        # 不冲的话浏览器 EventSource 会一直停在 CONNECTING（实测踩过）
        cutoff = broadcast.current_max_id()
        yield ': hello\n\n'
        while True:
            # 客户端断连在这里**检测不到**：浏览器半关后（CLOSE_WAIT）写心跳
            # 依旧"成功"，生成器会永生，每页浏览泄漏一个线程 + 一条套接字。
            # 所以给连接设寿命上限，到期主动收尾——EventSource 会自动带
            # Last-Event-ID 重连续传，前端无感（2026-10-11 连接泄漏修复）。
            if time.time() - started > 300:
                yield ': rotate\n\n'
                return
            evs = broadcast.events_since(last)
            for ev in evs:
                last = ev['id']
                ev = dict(ev)
                # 连接瞬间之前已发生的事件 = 历史回放（重连/刷新补发），
                # 客户端据此抑制"自动弹坞"这类强打扰动作
                if ev['id'] <= cutoff:
                    ev['backlog'] = True
                yield (f"id: {ev['id']}\n"
                       f"data: {json.dumps(ev, ensure_ascii=False)}\n\n")
            if evs:
                idle = 0
            else:
                idle += 1
                if idle >= 20:                     # ~20s 心跳注释行
                    idle = 0
                    yield ': ping\n\n'
            time.sleep(1.0)

    return Response(stream_with_context(gen()),
                    mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache',
                             'X-Accel-Buffering': 'no'})


@bp.route('/api/ai/audit')
def api_ai_audit():
    """AI 代理审计留痕（**跨重启读盘**）。

    与 `/api/ai/events` 的分工：后者是进程内环形缓冲的实时流（id 单调、
    重启归零）；本接口读的是 `run/ai_audit/*.jsonl` 的长期留痕 ——
    "AI 提过什么动作、谁批的、闸门何时弹的、用户选了继续还是终止"。

    参数：kind=actions|events（默认 actions）、limit=1..1000（默认 200）、
    day=YYYY-MM-DD（省略 = 从最近有记录的日期往前取）。只读。
    """
    kind = (request.args.get('kind') or 'actions').strip()
    if kind not in audit.KINDS:
        return jsonify({'error': f'kind 只允许: {", ".join(audit.KINDS)}'}), 400
    try:
        limit = int(request.args.get('limit') or 200)
    except ValueError:
        return jsonify({'error': 'limit 需为整数'}), 400
    limit = max(1, min(1000, limit))
    day = (request.args.get('day') or '').strip() or None
    if day is not None and not _DAY_RE.match(day):
        return jsonify({'error': 'day 需为 YYYY-MM-DD'}), 400
    try:
        records = audit.read(kind, limit=limit, day=day)
    except Exception as e:                 # 审计盘损坏也不该 500
        return jsonify({'kind': kind, 'records': [], 'stats': {},
                        'error': f'{type(e).__name__}: {e}'})
    return jsonify({'kind': kind, 'records': records,
                    'stats': audit.stats(), 'kinds': list(audit.KINDS)})


@bp.route('/api/ai/feedback', methods=['POST'])
def api_ai_feedback_post():
    """回答反馈（👍👎）写入（2026-11-04，见 ai_agent/feedback.py）。

    body {conv_id, msg_idx, verdict, note}；空体/非法一律 400 且无副作用
    （全路由巡检的空 POST 不会留下任何记录）。
    """
    from Virus_Platform_Core.ai_agent import feedback as _feedback
    body = request.get_json(silent=True) or {}
    if not body:
        abort(400, '需要请求体（conv_id / msg_idx / verdict）')
    r = _feedback.append(body.get('conv_id'), body.get('msg_idx'),
                         body.get('verdict'), body.get('note') or '')
    if not r.get('ok'):
        abort(400, r.get('error') or '反馈写入失败')
    return jsonify(r)


@bp.route('/api/ai/feedback')
def api_ai_feedback_get():
    """最近回答反馈的原始记录（旧 → 新，最多 limit 条；只读有限 JSON）。"""
    from Virus_Platform_Core.ai_agent import feedback as _feedback
    try:
        limit = max(1, min(200, int(request.args.get('limit') or 20)))
    except (TypeError, ValueError):
        limit = 20
    return jsonify({'items': _feedback.items(limit)})


@bp.route('/api/ai/sync/<conv_id>', methods=['POST'])
def api_ai_sync(conv_id):
    """会话镜像保存（只追加 JSONL，见 ai_agent/sessions.py）。

    前端每次落盘时把**全量**消息列表 POST 上来；服务端前缀比对决定追加
    还是原子重写。空体/不合法一律 400 无副作用（全路由巡检安全）。
    """
    from Virus_Platform_Core.ai_agent import sessions as _sessions
    body = request.get_json(silent=True) or {}
    r = _sessions.save(conv_id, body.get('msgs'))
    if not r.get('ok'):
        abort(400, r.get('error') or 'sync 失败')
    return jsonify(r)


@bp.route('/api/ai/sync/<conv_id>', methods=['GET'])
def api_ai_sync_get(conv_id):
    """读取某段会话的服务端留存（跨浏览器/清缓存恢复）。"""
    from Virus_Platform_Core.ai_agent import sessions as _sessions
    msgs = _sessions.read(conv_id)
    return jsonify({'conv_id': conv_id, 'n': len(msgs), 'msgs': msgs})


@bp.route('/api/ai/sync', methods=['GET'])
def api_ai_sync_list():
    """会话清单（conv_id/条数/时间/首问预览），给历史菜单与服务端列表用。"""
    from Virus_Platform_Core.ai_agent import sessions as _sessions
    try:
        limit = max(1, min(100, int(request.args.get('limit') or 30)))
    except (TypeError, ValueError):
        limit = 30
    return jsonify({'convs': _sessions.list_convs(limit)})


@bp.route('/api/ai/chat', methods=['POST'])
def api_ai_chat():
    body = request.get_json(silent=True) or {}
    messages = body.get('messages')
    if not isinstance(messages, list) or not messages:
        abort(400, '请求体须含非空 messages 数组')
    sample = str(body.get('sample') or '').strip() or None
    # 用户在本轮附加的文件（2026-10-30）：绝对路径列表，最多 8 个。
    # 只用于把「文件名 / 大小 / 文本预览」注入上下文，不授予任何额外权限。
    atts = body.get('attachments')
    if not isinstance(atts, list):
        atts = []
    atts = [str(a).strip() for a in atts if str(a or '').strip()][:8]
    # 用户选中的工作空间目录（2026-10-30）：把目录清单注入上下文，
    # 让模型知道"这一批文件在哪"，可以直接起草建样品/跑工具。
    workspace = str(body.get('workspace') or '').strip() or None
    # 问答平台（2026-11-02 用户定案）：独立于 AI 工作台的纯答疑通道。
    # mode='qa' 时服务端不注入任何当前数据、不发 tools、不产出结果卡；
    # 前端（app-qa.js）走同一端点，靠这个开关把两条通道彻底分开。
    mode = str(body.get('mode') or '').strip().lower()
    # 任务分解可选（2026-11-03）：auto（默认，模型自判）/ off（物理禁用
    # plan 动作）/ always（多阶段链路优先拆计划）。前端坞里可切换。
    plan_mode = str(body.get('plan_mode') or '').strip().lower()
    if plan_mode not in ('auto', 'off', 'always'):
        plan_mode = 'auto'

    def gen():
        # 心跳（2026-11-02）：模型排队/长推理/工具轮（mcp_call 最长 330s）
        # 期间可能几分钟零字节，前端只有一句「思考中…」，用户判定卡死只能刷新。
        # 把 run_chat 放到工作线程喂队列，主生成器每 5s 没数据就发一帧 tick
        # （elapsed 秒数），前端把「思考中… 已等待 Ns」显示出来。
        import queue as _q
        import threading as _th
        import time as _t
        q = _q.Queue(maxsize=512)
        t0 = _t.time()
        SENTINEL = object()

        def worker():
            try:
                for ev in ai_chat.run_chat(cfg.ai, messages, sample, atts,
                                           workspace, mode=mode,
                                           plan_mode=plan_mode):
                    q.put(ev)
            except Exception as e:     # 编排层兜底（chat.py 已兜一层）
                q.put("data: " + json.dumps(
                    {'type': 'error',
                     'message': f'{type(e).__name__}: {e}'},
                    ensure_ascii=False) + "\n\n")
                q.put("data: " + json.dumps({'type': 'done'}) + "\n\n")
            finally:
                q.put(SENTINEL)

        ctx = None
        try:
            from flask import has_request_context, copy_current_request_context
            if has_request_context():
                ctx = copy_current_request_context(worker)
        except Exception:
            ctx = None
        th = _th.Thread(target=ctx or worker, daemon=True,
                        name='ai-chat-stream')
        th.start()
        while True:
            try:
                item = q.get(timeout=5.0)
            except _q.Empty:
                yield "data: " + json.dumps(
                    {'type': 'tick', 'elapsed': int(_t.time() - t0)}) + "\n\n"
                continue
            if item is SENTINEL:
                break
            yield item

    return Response(stream_with_context(gen()),
                    mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache',
                             'X-Accel-Buffering': 'no'})
