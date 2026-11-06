# -*- coding: utf-8 -*-
"""AI 提供方：OpenAI 兼容 /v1/chat/completions 客户端（流式 + 工具调用）。

为什么不用 openai 官方包：requirements.txt 里它长期处于注释态（平台依赖
策略是"能不新增就不新增"），且各家兼容端点的差异只在 base_url 与鉴权头，
requests 足以覆盖。Ollama 本地模型暴露的也是同一套 /v1/chat/completions，
因此一个客户端通吃云端与离线两类提供方。

流式协议：上游返回 SSE（`data: {...}` 行，`[DONE]` 结束）。本模块把碎片
增量组装成两类事件交出去：
    {'type': 'delta',      'content': '...'}            正文增量
    {'type': 'tool_calls', 'tool_calls': [...]}          完整组装后的工具调用
    {'type': 'message',    'message': {...}}             非流式/末帧的完整消息

工具调用的流式组装：OpenAI 协议下 tool_calls 以碎片到达（首片带 id/name，
后续片只有 arguments 的增量），按 index 归并拼接。
"""
import json
import time

import requests

# (connect, read) 超时；read 是**块间**超时——流式生成再长也不会触发，
# 只防上游卡死。本地 Ollama 首次加载模型可能要几十秒，connect 放宽到 15s。
TIMEOUT = (15, 300)

# 提供方预设：选预设后前端自动回填 base_url 与推荐模型；custom 由用户全填。
# 2026-11-03：DeepSeek 官方模型名已改为 deepseek-flash / deepseek-v4-pro
# （旧 deepseek-chat 在官网定价页已不再列出），预设随之更新；/v1 端点保持
# 不变（DeepSeek 的 OpenAI 兼容端点，带不带 /v1 都通）。
PROVIDER_PRESETS = [
    {'id': 'deepseek', 'name': 'DeepSeek（深度求索）',
     'base_url': 'https://api.deepseek.com/v1', 'model': 'deepseek-flash',
     'key_url': 'https://platform.deepseek.com'},
    {'id': 'zhipu', 'name': '智谱 GLM',
     'base_url': 'https://open.bigmodel.cn/api/paas/v4', 'model': 'glm-4-flash',
     'key_url': 'https://open.bigmodel.cn'},
    {'id': 'dashscope', 'name': '阿里云百炼（通义 Qwen）',
     'base_url': 'https://dashscope.aliyuncs.com/compatible-mode/v1',
     'model': 'qwen-plus', 'key_url': 'https://bailian.console.aliyun.com'},
    {'id': 'moonshot', 'name': '月之暗面 Kimi',
     'base_url': 'https://api.moonshot.cn/v1', 'model': 'moonshot-v1-8k',
     'key_url': 'https://platform.moonshot.cn'},
    {'id': 'openai', 'name': 'OpenAI',
     'base_url': 'https://api.openai.com/v1', 'model': 'gpt-4o-mini',
     'key_url': 'https://platform.openai.com'},
    {'id': 'ollama', 'name': 'Ollama（本地，离线可用）',
     'base_url': 'http://127.0.0.1:11434/v1', 'model': 'qwen2.5:7b',
     'key_url': ''},
    {'id': 'custom', 'name': '自定义（OpenAI 兼容端点）',
     'base_url': '', 'model': '', 'key_url': ''},
]


class AIConfigError(Exception):
    """AI 配置不完整（未启用 / 缺 Key / 缺模型），需要用户去设置页补全。"""


class AIProviderError(Exception):
    """上游端点错误（网络 / 鉴权 / 参数），message 面向用户可读。"""

    def __init__(self, message, status=0):
        super().__init__(message)
        self.status = status


def _key_of(ai_cfg):
    """取可用 Key（就地展开 ${ENV_VAR}，兼容任何入口塞进来的占位符）。"""
    raw = str((ai_cfg or {}).get('api_key') or '').strip()
    if not raw:
        return ''
    try:
        from Virus_Platform_Core.config import expand_secret
        return expand_secret(raw)
    except Exception:                     # pragma: no cover - 极端导入失败
        return raw


def ai_ready(ai_cfg):
    """配置是否可用（base_url + model；Ollama 允许无 Key）。

    2026-11-03 用户定案：**取消「启用 AI 工作台」总开关**——填好端点就等于
    启用，不再要求额外的 enabled 标志（实测用户填完 Key 却卡在「未配置完整」，
    根因就是这个默认关闭、位置还不显眼的开关）。字段仍保留写入（老配置、
    播报/执行单分级仍读它），但不再参与就绪判定。
    """
    return bool(ai_cfg and ai_cfg.get('base_url') and ai_cfg.get('model')
                and (_key_of(ai_cfg) or 'ollama' == ai_cfg.get('provider')))


def _headers(ai_cfg):
    h = {'Content-Type': 'application/json'}
    key = _key_of(ai_cfg)
    if key:
        h['Authorization'] = f"Bearer {key}"
    return h


def _endpoint(ai_cfg, path):
    base = str(ai_cfg.get('base_url') or '').rstrip('/')
    return f'{base}{path}'


def _raise_provider_error(resp):
    """把非 2xx 响应翻译成可读错误（各家错误体结构不一，尽量剥出 message）。"""
    detail = ''
    try:
        body = resp.json()
        err = body.get('error')
        if isinstance(err, dict):
            detail = err.get('message') or json.dumps(err, ensure_ascii=False)
        elif err:
            detail = str(err)
        else:
            detail = json.dumps(body, ensure_ascii=False)[:300]
    except Exception:
        detail = (resp.text or '')[:300]
    hint = ''
    if resp.status_code in (401, 403):
        hint = '（API Key 无效或无权限）'
    elif resp.status_code == 404:
        hint = '（端点路径不存在，请检查 Base URL 是否以 /v1 结尾）'
    elif resp.status_code == 429:
        hint = '（额度不足或限流）'
    raise AIProviderError(f'上游返回 {resp.status_code}{hint}: {detail}',
                          status=resp.status_code)


def _join_name(cur, add):
    """拼接流式 tool_call 的 name 碎片，容忍网关重复整名。

    标准 OpenAI 协议只在首片给 name，之后只给 arguments；但有兼容网关每片
    都重发完整 name，`cur + add` 会拼成 "propose_actionpropose_action"，
    结果 dispatch 找不到工具、模型被回一句「未知工具」。
    """
    if not cur:
        return add
    if add in cur or cur.endswith(add):
        return cur
    return cur + add


def _assemble_tool_calls(frags):
    """流式工具调用碎片按 index 归并 → OpenAI 消息格式的 tool_calls 列表。"""
    out = {}
    for f in frags:
        i = f.get('index') or 0
        slot = out.setdefault(i, {'id': '', 'type': 'function',
                                  'function': {'name': '', 'arguments': ''}})
        if f.get('id'):
            slot['id'] = f['id']
        fn = f.get('function') or {}
        if fn.get('name'):
            slot['function']['name'] = _join_name(slot['function']['name'],
                                                  fn['name'])
        if fn.get('arguments'):
            slot['function']['arguments'] += fn['arguments']
    return [out[k] for k in sorted(out)]


def chat_stream(ai_cfg, messages, tools=None, temperature=None, max_tokens=None,
                timeout=None):
    """流式对话：yield 组装后的事件 dict（见模块 docstring）。

    timeout: (connect, read) 覆盖；后台播报/闸门评估应传紧超时
    （见 broadcast._AI_CALL_TIMEOUT），别让一次挂起的上游把流水线
    闸门拖住几分钟。

    网络与协议错误统一抛 AIProviderError / AIConfigError；
    requests 层异常（DNS/超时/拒绝连接）也归一为 AIProviderError。
    """
    if not ai_ready(ai_cfg):
        raise AIConfigError('AI 助手未配置完整（设置页需填写 Base URL / '
                            'API Key / 模型）')
    payload = {
        'messages': messages,
        'model': ai_cfg['model'],
        'stream': True,
        'temperature': (ai_cfg.get('temperature') if temperature is None
                        else temperature),
        'max_tokens': ai_cfg.get('max_tokens') or 32768,
    }
    if tools:
        payload['tools'] = tools
        payload['tool_choice'] = 'auto'
    try:
        resp = requests.post(_endpoint(ai_cfg, '/chat/completions'),
                             headers=_headers(ai_cfg), json=payload,
                             stream=True, timeout=timeout or TIMEOUT)
    except requests.RequestException as e:
        raise AIProviderError(f'无法连接 AI 端点: {type(e).__name__}: {e}')
    with resp:
        if resp.status_code != 200:
            _raise_provider_error(resp)
        # 兼容个别网关对 stream:true 返回整体 JSON（非 SSE）的情况
        ctype = (resp.headers.get('Content-Type') or '').lower()
        if 'text/event-stream' not in ctype:
            try:
                data = resp.json()
            except ValueError:
                raise AIProviderError('上游响应既非 SSE 也非 JSON')
            msg = ((data.get('choices') or [{}])[0].get('message')) or {}
            if msg.get('tool_calls'):
                yield {'type': 'tool_calls', 'tool_calls': msg['tool_calls']}
            if msg.get('reasoning_content'):
                yield {'type': 'reasoning',
                       'content': msg['reasoning_content']}
            if msg.get('content'):
                yield {'type': 'delta', 'content': msg['content']}
            yield {'type': 'message', 'message': msg}
            return
        content_started = False
        frags = []
        finish = ''
        reasoning_chars = 0
        for raw in resp.iter_lines(decode_unicode=True):
            if not raw:
                continue
            # 个别兼容端点不回 charset，requests 的 decode_unicode 就会把
            # bytes 原样给出（之后 startswith('data:') 直接 TypeError）。
            if isinstance(raw, bytes):
                raw = raw.decode('utf-8', 'replace')
            if not raw.startswith('data:'):
                continue
            body = raw[5:].strip()
            if body == '[DONE]':
                break
            try:
                chunk = json.loads(body)
            except ValueError:
                continue
            choice = (chunk.get('choices') or [{}])[0]
            delta = choice.get('delta') or {}
            # 推理模型（deepseek-reasoner / 部分网关）把思维链放在
            # reasoning_content；普通 chat 模型没有这个字段。
            rsn = delta.get('reasoning_content') or delta.get('reasoning')
            if rsn:
                reasoning_chars += len(rsn)
                yield {'type': 'reasoning', 'content': rsn}
            if delta.get('content'):
                content_started = True
                yield {'type': 'delta', 'content': delta['content']}
            if delta.get('tool_calls'):
                frags.extend(delta['tool_calls'])
            if choice.get('finish_reason'):
                finish = choice['finish_reason']
                # 显式把结束原因交出去：上层要据此判断「被 max_tokens 截断」
                # 「只有思考没有正文」（推理模型常见），而不是笼统报
                # 「模型未返回内容」。
                yield {'type': 'finish', 'reason': finish,
                       'reasoning_chars': reasoning_chars}
        if frags:
            tc = _assemble_tool_calls(frags)
            yield {'type': 'tool_calls', 'tool_calls': tc}
            yield {'type': 'message',
                   'message': {'role': 'assistant', 'content': '',
                               'tool_calls': tc}}
        elif content_started or finish != 'tool_calls':
            # 把已流出的正文再以完整消息收尾（工具循环追加历史时需要）
            yield {'type': 'message',
                   'message': {'role': 'assistant', 'content': None}}


def test_connection(ai_cfg):
    """连通性测试：发一个 1 token 的最小请求，返回时延与回复。"""
    t0 = time.time()
    payload = {'model': ai_cfg.get('model') or '', 'stream': False,
               'max_tokens': 8,
               'messages': [{'role': 'user', 'content': '回复"OK"两个字母即可'}]}
    try:
        resp = requests.post(_endpoint(ai_cfg, '/chat/completions'),
                             headers=_headers(ai_cfg), json=payload,
                             timeout=(10, 60))
    except requests.RequestException as e:
        return {'ok': False, 'error': f'{type(e).__name__}: {e}',
                'latency_ms': int((time.time() - t0) * 1000)}
    if resp.status_code != 200:
        try:
            _raise_provider_error(resp)
        except AIProviderError as e:
            return {'ok': False, 'error': str(e),
                    'latency_ms': int((time.time() - t0) * 1000)}
    try:
        data = resp.json()
        choice = (data.get('choices') or [{}])[0] or {}
        reply = ((choice.get('message') or {}).get('content') or '').strip()
    except ValueError:
        data, choice, reply = {}, {}, ''
    if not reply:
        # 连通成功但正文为空：推理型模型（deepseek-reasoner/flash 等）把
        # 预算耗在 reasoning_content 上时就是这样，设置页得说清楚，
        # 否则用户只会看到「测试成功」却没有回复。
        fr = str(choice.get('finish_reason') or '') or '未知'
        return {'ok': True, 'reply': '',
                'note': f'连通正常，但正文为空（finish_reason={fr}）；'
                        f'推理型模型的思考可能占满了「最大生成长度」，'
                        f'建议调到 4096 以上或改用非推理模型',
                'latency_ms': int((time.time() - t0) * 1000)}
    return {'ok': True, 'reply': reply[:80],
            'latency_ms': int((time.time() - t0) * 1000)}
