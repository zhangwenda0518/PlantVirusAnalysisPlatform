# -*- coding: utf-8 -*-
"""启动性能改造回归（2026-11-06）。

锁三件事：插件索引失败负缓存（离线机不再 3 源 × 30s 反复联网）、
selfcheck.collect 的 stale-while-revalidate（旧值即时返回 + 后台重算）、
/api/kv_index_list 的 TTL 缓存（?refresh=1 强制重扫）。
"""
import json
import time

import pytest


# ------------------------------------------------------------------
# 1) plugin_manager.fetch_index 失败负缓存
# ------------------------------------------------------------------
def test_fetch_index_negative_cache(tmp_path, monkeypatch):
    pm = pytest.importorskip('Virus_Platform_Core.plugin_manager')
    attempts = {'n': 0}

    def _fail(*a, **k):
        attempts['n'] += 1
        import urllib.error
        raise urllib.error.URLError('offline')

    import urllib.request
    monkeypatch.setattr(urllib.request, 'urlopen', _fail)
    pm._FAIL_CACHE['ts'] = 0.0
    monkeypatch.setattr(pm, '_FAIL_TTL', 60)
    out1, err1 = pm.fetch_index(url='http://127.0.0.1:1/idx.json')
    assert out1 is None and err1
    n_after_first = attempts['n']
    assert n_after_first > 0
    out2, err2 = pm.fetch_index(url='http://127.0.0.1:1/idx.json')
    assert out2 is None and err2 == err1, '负缓存期内返回同一错误，不再联网'
    assert attempts['n'] == n_after_first, '负缓存期内不得再发起网络请求'
    out3, _e3 = pm.fetch_index(url='http://127.0.0.1:1/idx.json', force=True)
    assert attempts['n'] > n_after_first, 'force=True 必须绕过负缓存'


# ------------------------------------------------------------------
# 2) selfcheck.collect stale-while-revalidate
# ------------------------------------------------------------------
def test_collect_serves_stale_then_refreshes(monkeypatch):
    sc = pytest.importorskip('Virus_Platform_Core.selfcheck')
    fake = {'ok': True, 'seconds': 0.1, 'ts': 0, 'cached': False,
            'sections': []}

    def _fake_section():
        d = dict(fake)
        d['ts'] = time.time()
        return d

    for name in ('_check_modules', '_check_tools', '_check_databases',
                 '_check_disk'):
        monkeypatch.setattr(sc, name, _fake_section)
    monkeypatch.setitem(sc._CACHE, 'data', None)
    monkeypatch.setitem(sc._CACHE, 'ts', 0.0)
    first = sc.collect()
    assert first['cached'] is False and first.get('stale') is None
    # 人为把缓存弄过期 → 下一次调用应即时返回旧值并后台重算
    sc._CACHE['ts'] = time.time() - sc.CACHE_TTL - 1
    sc._REFRESH['busy'] = False
    stale = sc.collect()
    assert stale.get('stale') is True and stale['cached'] is True
    # 后台重算完成 → 缓存被新数据覆盖（fake 段函数带新时间戳）
    deadline = time.time() + 5
    while time.time() < deadline:
        if sc._CACHE['data'] and sc._CACHE['data'].get('seconds') == 0.1:
            break
        time.sleep(0.05)
    assert sc._REFRESH['busy'] is False
    refreshed = sc.collect()
    assert refreshed['cached'] is True and refreshed.get('stale') is None


# ------------------------------------------------------------------
# 3) /api/kv_index_list TTL 缓存
# ------------------------------------------------------------------
def test_kv_index_list_cached(tmp_path, monkeypatch):
    import app as appmod
    from Virus_Platform_Core.web import build as buildmod
    buildmod._KVLIB_CACHE['data'] = None
    client = appmod.app.test_client()
    r1 = client.get('/api/kv_index_list')
    assert r1.status_code == 200
    assert r1.get_json().get('cached') is None
    r2 = client.get('/api/kv_index_list')
    assert r2.get_json().get('cached') is True, 'TTL 内第二次必须命中缓存'
    r3 = client.get('/api/kv_index_list?refresh=1')
    assert r3.get_json().get('cached') is None, '?refresh=1 必须强制重扫'
    buildmod._KVLIB_CACHE['data'] = None
