# -*- coding: utf-8 -*-
"""AI 配置并发保存测试（2026-11-06 配置收敛回归）。

覆盖目标（docs/DEVELOPMENT_NOTES.md「补七/补八」同轮）：
  1) 未知键回读进锁：并发保存不丢「更新版本新增、本进程不认识的键」；
  2) set_ai（AI 段唯一写通道）vs 设置页 save：AI 段不再整段
     last-writer-wins（丢 providers / active_profile）；
  3) set_ai 落盘契约：platform.json ai 段永不落明文 Key，ai_local.json
     的 provider_keys 原样保留；
  4) 路由级并发：/api/ai/profiles/save × /api/ai/config 并发不打架。
"""
import json
import os
import sys
import tempfile
import threading
import unittest

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors='replace')
    except (AttributeError, OSError):
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault('VP_NO_RECOVER', '1')
os.environ.setdefault('VP_RUN_ROOT', tempfile.mkdtemp(prefix='vp_aiconc_'))


class _Sandbox(unittest.TestCase):
    """platform.json / ai_local.json 隔离到临时目录（沿用 test_ai_hardening）。"""

    def _sandbox(self):
        from Virus_Platform_Core import config as cfgmod
        td = tempfile.TemporaryDirectory(prefix='vp_aiconc_')
        self.addCleanup(td.cleanup)
        os.makedirs(os.path.join(td.name, 'run'), exist_ok=True)
        old_p, old_c = cfgmod.PLATFORM_ROOT, cfgmod.CONFIG_FILE
        cfgmod.PLATFORM_ROOT = td.name
        cfgmod.CONFIG_FILE = os.path.join(td.name, 'platform.json')

        def _restore():
            cfgmod.PLATFORM_ROOT, cfgmod.CONFIG_FILE = old_p, old_c
        self.addCleanup(_restore)
        return cfgmod, td.name


class UnknownKeyTests(_Sandbox):

    def test_concurrent_saves_preserve_unknown_keys(self):
        """两个线程并发 save（改不同已知字段），未知键必须存活。"""
        cfgmod, _root = self._sandbox()
        unknown = {'added-by-newer': {'nested': [1, 2, 3]}}
        with open(cfgmod.CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump({'future_key': unknown}, f)
        c = cfgmod.Config()
        errs = []

        def _saver(field, values):
            try:
                for v in values:
                    setattr(c, field, v)
                    c.save()
            except Exception as e:                     # noqa: BLE001
                errs.append(e)

        t1 = threading.Thread(target=_saver,
                              args=('language', ['en', 'zh'] * 20))
        t2 = threading.Thread(target=_saver,
                              args=('threads', [4, 8] * 20))
        for t in (t1, t2):
            t.start()
        for t in (t1, t2):
            t.join()
        self.assertEqual(errs, [], f'并发保存不应抛错: {errs}')
        with open(cfgmod.CONFIG_FILE, encoding='utf-8') as f:
            disk = json.load(f)
        self.assertEqual(disk.get('future_key'), unknown,
                         '未知键在并发保存下不得丢失（回读已进 _SAVE_LOCK）')
        self.assertIn(disk.get('language'), ('en', 'zh'))
        self.assertIn(disk.get('threads'), (4, 8))


class SetAiTests(_Sandbox):

    def test_set_ai_beats_settings_save_no_lost_segment(self):
        """set_ai × 设置页 save 并发：AI 段最终以 set_ai 的值收口，不被抹掉。"""
        cfgmod, _root = self._sandbox()
        c = cfgmod.Config()
        provs = [{'name': 'P1', 'base_url': 'https://api.example.com/v1',
                  'model': 'test-model', 'temperature': 0.3,
                  'max_tokens': 2048}]
        errs = []

        def _profile():
            try:
                for _i in range(30):
                    c.set_ai(dict(c.ai, providers=provs,
                                  active_profile='P1'))
            except Exception as e:                     # noqa: BLE001
                errs.append(e)

        def _settings():
            try:
                for i in range(30):
                    c.threads = 4 + (i % 2) * 4
                    c.save()
            except Exception as e:                     # noqa: BLE001
                errs.append(e)

        t1 = threading.Thread(target=_profile)
        t2 = threading.Thread(target=_settings)
        for t in (t1, t2):
            t.start()
        for t in (t1, t2):
            t.join()
        self.assertEqual(errs, [], f'并发写不应抛错: {errs}')
        with open(cfgmod.CONFIG_FILE, encoding='utf-8') as f:
            disk = json.load(f)
        self.assertEqual(disk.get('ai', {}).get('providers'), provs,
                         'AI 段与设置页并发保存不得丢失 providers')
        self.assertEqual(disk.get('ai', {}).get('active_profile'), 'P1',
                         'AI 段与设置页并发保存不得丢失 active_profile')

    def test_set_ai_persists_and_keeps_profile_keys(self):
        """set_ai 落盘契约：ai 段无明文 Key；ai_local 的 provider_keys 保留。"""
        cfgmod, _root = self._sandbox()
        with open(cfgmod._ai_local_path(), 'w', encoding='utf-8') as f:
            json.dump({'api_key': 'sk-top-A',
                       'provider_keys': {'B': 'sk-B-own'}}, f)
        with open(cfgmod.CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump({'ai': {'provider': 'custom'}}, f)
        c = cfgmod.Config()
        c.set_ai(dict(c.ai, active_profile='B'))
        with open(cfgmod._ai_local_path(), encoding='utf-8') as f:
            local = json.load(f)
        with open(cfgmod.CONFIG_FILE, encoding='utf-8') as f:
            disk = json.load(f)
        self.assertEqual(local.get('provider_keys', {}).get('B'), 'sk-B-own',
                         'set_ai 不得动 provider_keys')
        self.assertEqual(disk.get('ai', {}).get('api_key'), '',
                         'platform.json ai 段永不落明文 Key')
        self.assertEqual(disk.get('ai', {}).get('active_profile'), 'B')


class RouteConcurrencyTests(_Sandbox):
    """路由级并发：profiles/save × config POST（走 Flask test_client）。"""

    def test_profile_save_and_config_post_concurrent(self):
        cfgmod, _root = self._sandbox()
        import app as appmod
        from Virus_Platform_Core.web.state import cfg
        old_ai = dict(cfg.ai)
        self.addCleanup(setattr, cfg, 'ai', old_ai)
        client = appmod.app.test_client()
        errs = []

        def _profile():
            try:
                for i in range(10):
                    r = client.post('/api/ai/profiles/save', json={
                        'name': 'ConcProf', 'base_url': 'https://api.c.cn/v1',
                        'model': 'm1', 'temperature': 0.2,
                        'max_tokens': 1024})
                    assert r.status_code == 200, r.get_data(as_text=True)
            except Exception as e:                     # noqa: BLE001
                errs.append(e)

        def _cfgpost():
            try:
                for i in range(10):
                    r = client.post('/api/ai/config',
                                    json={'temperature': 0.1 + i * 0.01})
                    assert r.status_code == 200, r.get_data(as_text=True)
            except Exception as e:                     # noqa: BLE001
                errs.append(e)

        t1 = threading.Thread(target=_profile)
        t2 = threading.Thread(target=_cfgpost)
        for t in (t1, t2):
            t.start()
        for t in (t1, t2):
            t.join()
        self.assertEqual(errs, [], f'路由并发不应抛错: {errs}')
        # 收口态：档案在、active_profile 未被抹
        r = client.get('/api/ai/profiles')
        self.assertEqual(r.status_code, 200)
        names = [p['name'] for p in r.get_json().get('profiles') or []]
        self.assertIn('ConcProf', names, '并发后档案必须仍在')


if __name__ == '__main__':
    unittest.main()
