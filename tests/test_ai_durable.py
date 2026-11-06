# -*- coding: utf-8 -*-
"""2026-11-03 平台三件底层加固的专项测试：
  1) 会话 JSONL 只追加留存（sessions.py + /api/ai/sync 端点）
  2) ${ENV_VAR} 密钥展开与防线
  3) 问答两级注入（章节检索）
"""
import json
import os
import sys
import tempfile

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors='replace')
    except (AttributeError, OSError):
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ['VP_NO_RECOVER'] = '1'
os.environ['VP_RUN_ROOT'] = tempfile.mkdtemp(prefix='vp_sess_')

FAIL = []


def check(label, ok, extra=''):
    print(f'  [{"PASS" if ok else "FAIL"}] {label}'
          + (f'  {extra}' if extra else ''), flush=True)
    if not ok:
        FAIL.append(label)


def main():
    from Virus_Platform_Core.ai_agent import sessions
    from Virus_Platform_Core.config import expand_secret

    print('== 1) 会话 JSONL ==')
    cid = 'c-test01'
    r = sessions.save(cid, [
        {'role': 'user', 'content': '你好'},
        {'role': 'assistant', 'content': '你好！有什么可以帮你？'},
    ])
    check('首存成功', r.get('ok') is True and r.get('appended') == 2, str(r))
    r = sessions.save(cid, [
        {'role': 'user', 'content': '你好'},
        {'role': 'assistant', 'content': '你好！有什么可以帮你？'},
        {'role': 'user', 'content': '看下任务'},
    ])
    check('前缀吻合→只追加尾部', r.get('appended') == 1, str(r))
    raw = open(os.path.join(sessions._DIR, cid + '.jsonl'),
               encoding='utf-8').read()
    check('文件恰好 3 行（历史未被改写）', raw.count('\n') == 3,
          str(raw.count(chr(10))) + ' 行')
    msgs = sessions.read(cid)
    check('读回 3 条', len(msgs) == 3 and msgs[2]['content'] == '看下任务')

    r = sessions.save(cid, msgs[:2])
    check('前缀断裂→原子重写', r.get('rewritten') is True, str(r))
    check('重写后 2 条', len(sessions.read(cid)) == 2)

    check('非法 conv_id 拒绝',
          sessions.save('../evil', [{'role': 'user', 'content': 'x'}]).get('ok') is False)
    check('非法 role 丢弃',
          sessions.save('c-test02', [{'role': 'system', 'content': 'x'}])['n'] == 0)
    m = sessions._norm_msg({'role': 'user', 'content': 'x' * 30000})
    check('超长 content 截断 24K', len(m['content']) == 24000)
    check('artifacts 只留 path/href 形状',
          sessions._norm_msg({'role': 'assistant', 'content': 'a',
                              'artifacts': [{'path': '/x'}, {'bad': 1}, 's']})
          ['artifacts'] == [{'path': '/x'}])

    print('== 1b) /api/ai/sync 端点 ==')
    import app as appmod
    appmod.app.config['TESTING'] = True
    with appmod.app.test_client() as c:
        r = c.post('/api/ai/sync/c-api01', json={
            'msgs': [{'role': 'user', 'content': 'hi'},
                     {'role': 'assistant', 'content': 'hello'}]})
        check('POST 保存 200', r.status_code == 200, str(r.status_code))
        r = c.get('/api/ai/sync/c-api01')
        check('GET 读回 2 条', r.status_code == 200
              and len(r.get_json()['msgs']) == 2)
        r = c.post('/api/ai/sync/c-api01', json={})
        check('空体 400', r.status_code == 400)
        r = c.post('/api/ai/sync/..%2F..%2Fetc', json={'msgs': []})
        check('路径穿越被拒（400/404）', r.status_code in (400, 404),
              str(r.status_code))
        r = c.get('/api/ai/sync?limit=5')
        check('清单 200', r.status_code == 200
              and any(x['conv_id'] == 'c-api01' for x in r.get_json()['convs']))

    print('== 2) ${ENV_VAR} 密钥展开 ==')
    os.environ['VP_TEST_KEY_X'] = 'sk-env-1234567890'
    check('占位符→环境变量值', expand_secret('${VP_TEST_KEY_X}')
          == 'sk-env-1234567890')
    check('明文原样返回', expand_secret('sk-plain-key') == 'sk-plain-key')
    check('空值安全', expand_secret('') == '' and expand_secret(None) == '')
    check('未设置的环境变量→空串（不抛）',
          expand_secret('${VP_NOPE_NOT_SET}') == '')
    check('半截占位符不展开',
          expand_secret('前缀${VP_TEST_KEY_X}') == '前缀${VP_TEST_KEY_X}')

    from Virus_Platform_Core import config as cfgmod
    os.environ['VP_TEST_KEY_Y'] = 'sk-merged-9999'
    lp = cfgmod._ai_local_path()
    os.makedirs(os.path.dirname(lp), exist_ok=True)
    with open(lp, 'w', encoding='utf-8') as f:
        json.dump({'api_key': '${VP_TEST_KEY_Y}'}, f)
    c2 = cfgmod.Config()
    check('config._load 展开占位符',
          c2.ai.get('api_key') == 'sk-merged-9999',
          repr(c2.ai.get('api_key'))[:40])
    try:
        os.remove(lp)
    except OSError:
        pass

    print('== 2b) 保存设置不得抹掉盘上已有的 Key ==')
    # 根因（2026-11-05 用户反馈「每次更新都要重设 Key」）：内存空 + 磁盘有
    # Key 的实例（第二实例 / 更新换代期间的旧实例 / 占位符展开失败）保存
    # 设置时，save() 按内存值回写 ai_local.json，把 Key 抹成空串。
    # 隔离：PLATFORM_ROOT / CONFIG_FILE 指到临时目录，不碰真实配置。
    _tmp_root = tempfile.mkdtemp(prefix='vp_keykeep_')
    _old_pr, _old_cf = cfgmod.PLATFORM_ROOT, cfgmod.CONFIG_FILE
    cfgmod.PLATFORM_ROOT = _tmp_root
    cfgmod.CONFIG_FILE = os.path.join(_tmp_root, 'platform.json')
    try:
        _lp = cfgmod._ai_local_path()
        os.makedirs(os.path.dirname(_lp), exist_ok=True)
        with open(_lp, 'w', encoding='utf-8') as f:
            json.dump({'api_key': 'sk-keep-1234',
                       'provider_keys': {'A': 'sk-a-1111'}}, f)
        # _load 只在 platform.json 带 ai 段时才执行密钥合并 → 补一份最小配置
        with open(cfgmod.CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump({'ai': {}}, f)
        _c = cfgmod.Config()
        check('载入：磁盘 Key 进内存',
              _c.ai.get('api_key') == 'sk-keep-1234',
              repr(_c.ai.get('api_key'))[:40])
        # 模拟「内存没有 Key 的实例」保存任意设置：
        _c.ai = dict(_c.ai, api_key='')
        _c.save()
        with open(_lp, encoding='utf-8') as f:
            _d = json.load(f)
        check('空内存保存不抹盘上 Key', _d.get('api_key') == 'sk-keep-1234',
              repr(_d.get('api_key'))[:40])
        check('同次保存保留 provider_keys',
              (_d.get('provider_keys') or {}).get('A') == 'sk-a-1111')
        _c2 = cfgmod.Config()
        check('下一个实例仍能载入 Key',
              _c2.ai.get('api_key') == 'sk-keep-1234')
        # 内存有值时照旧可换钥：
        _c2.ai = dict(_c2.ai, api_key='sk-rot-5678')
        _c2.save()
        with open(_lp, encoding='utf-8') as f:
            _d2 = json.load(f)
        check('显式新 Key 正常覆盖', _d2.get('api_key') == 'sk-rot-5678')
    finally:
        cfgmod.PLATFORM_ROOT, cfgmod.CONFIG_FILE = _old_pr, _old_cf

    print('== 3) 问答两级注入 ==')
    from Virus_Platform_Core.ai_agent import chat as ai_chat
    doc = ai_chat._qa_doc()
    secs = ai_chat._doc_sections(doc)
    check('AGENTS.md 切出 >=3 节', len(secs) >= 3, str(len(secs)) + ' 节')
    full_len = len(doc)
    picked = ai_chat._qa_pick_sections(doc, '怎么建样品并跑流水线？')
    check('检索结果短于全文', len(picked) < full_len,
          str(len(picked)) + ' < ' + str(full_len))
    check('含目录头', '【文档目录】' in picked)
    check('含章节标题', '## ' in picked)
    body_hit = ('样品' in picked) or ('流水线' in picked)
    check('命中内容与问题相关（正文含关键词）', body_hit,
          picked[:100].replace('\n', ' '))
    p0 = ai_chat._qa_system_prompt()
    p1 = ai_chat._qa_system_prompt(question='怎么建样品？')
    check('无问题→全文注入（旧行为）', len(p0) > len(p1))
    check('有问题→注入明显变短', len(p1) < len(p0))

    print('')
    if FAIL:
        print('失败 ' + str(len(FAIL)) + ' 项: ' + '; '.join(FAIL))
        return 1
    print('全部通过')
    return 0


# ----------------------------------------------------------------------
# 2026-11-06 加固回归（pytest 直接收集；main() 是历史脚本入口，不冲突）
# ----------------------------------------------------------------------
def test_session_artifacts_metadata_is_persisted(tmp_path):
    """final 消息先落盘，随后挂上 artifacts 二次保存必须落盘。

    缺陷：前缀比较只认 role/content，二次保存走只追加快路径且 tail 为空，
    直接 return appended=0——artifacts 永远进不了镜像文件。
    """
    from Virus_Platform_Core.ai_agent import sessions
    cid = 'c-artifact01'
    first = [{'role': 'user', 'content': '报告在哪'},
             {'role': 'assistant', 'content': '在 report_v2.html'}]
    r1 = sessions.save(cid, first)
    assert r1.get('ok') and r1.get('appended') == 2
    # 前端把产物卡片挂到同一条最终消息后再次保存（内容不变）
    msgs = sessions.read(cid)
    msgs[1]['artifacts'] = [{'path': '/x/report_v2.html', 'name': 'report_v2.html'}]
    r2 = sessions.save(cid, msgs)
    assert r2.get('ok')
    got = sessions.read(cid)
    assert got[1].get('artifacts') == msgs[1]['artifacts'], got
    # 同一份数据再存一次应回到稳定状态（不重复追加）
    n_before = len(sessions.read(cid))
    sessions.save(cid, got)
    assert len(sessions.read(cid)) == n_before


def test_session_partial_flag_metadata_is_persisted(tmp_path):
    from Virus_Platform_Core.ai_agent import sessions
    cid = 'c-partial01'
    sessions.save(cid, [{'role': 'user', 'content': 'q'},
                        {'role': 'assistant', 'content': '半截',
                         'partial': True}])
    msgs = sessions.read(cid)
    msgs[1].pop('partial', None)
    sessions.save(cid, msgs)
    assert 'partial' not in sessions.read(cid)[1]


def test_save_keeps_env_placeholder_on_disk(tmp_path):
    """Config.save 不得把 ${ENV_VAR} 展开后的明文回写到 ai_local.json。"""
    import json
    from Virus_Platform_Core import config as cfgmod
    os.environ['VP_TEST_KEY_PH'] = 'sk-placeholder-777'
    old_p, old_c = cfgmod.PLATFORM_ROOT, cfgmod.CONFIG_FILE
    cfgmod.PLATFORM_ROOT = str(tmp_path)
    cfgmod.CONFIG_FILE = str(tmp_path / 'platform.json')
    try:
        lp = cfgmod._ai_local_path()
        os.makedirs(os.path.dirname(lp), exist_ok=True)
        with open(lp, 'w', encoding='utf-8') as f:
            json.dump({'api_key': '${VP_TEST_KEY_PH}'}, f)
        with open(cfgmod.CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump({'ai': {}}, f)
        c = cfgmod.Config()
        assert c.ai.get('api_key') == 'sk-placeholder-777'
        c.save()
        with open(lp, encoding='utf-8') as f:
            d = json.load(f)
        assert d.get('api_key') == '${VP_TEST_KEY_PH}', d
    finally:
        cfgmod.PLATFORM_ROOT, cfgmod.CONFIG_FILE = old_p, old_c
        os.environ.pop('VP_TEST_KEY_PH', None)


def test_ai_enabled_explicit_false_is_respected(tmp_path):
    """盘上显式 enabled=false 不得在加载时被强制改回 True。"""
    import json
    from Virus_Platform_Core import config as cfgmod
    old_p, old_c = cfgmod.PLATFORM_ROOT, cfgmod.CONFIG_FILE
    cfgmod.PLATFORM_ROOT = str(tmp_path)
    cfgmod.CONFIG_FILE = str(tmp_path / 'platform.json')
    try:
        with open(cfgmod.CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump({'ai': {'enabled': False, 'base_url': 'https://x/v1',
                              'model': 'm'}}, f)
        c = cfgmod.Config()
        assert c.ai.get('enabled') is False
        assert c.ai.get('base_url') == 'https://x/v1'
        # 没有 ai 段 / 没有 enabled 键的旧配置仍默认 True
        with open(cfgmod.CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump({'ai': {'base_url': 'https://x/v1'}}, f)
        c2 = cfgmod.Config()
        assert c2.ai.get('enabled') is True
    finally:
        cfgmod.PLATFORM_ROOT, cfgmod.CONFIG_FILE = old_p, old_c


if __name__ == '__main__':
    raise SystemExit(main())
