# -*- coding: utf-8 -*-
"""CPU / 内存实时监测（顶栏徽章）契约测试。

覆盖三件事：
  1. **采样口径**（Virus_Platform_Core/sysmon.py）：CPU 需要两轮基线、内存
     total/used/available 自洽、平台进程树能认出自己、峰值只增不减、0.8 秒
     内并发请求合并为同一份快照。
  2. **HTTP 端点**：GET /api/sys_metrics 返回可渲染的 JSON；?force=1 可重采；
     采样失败时也不抛 500（状态条不能带崩页面）。
  3. **前端接线**：模板有 #sysStripBody、app.js 引用端点并 3 秒轮询、CSS
     有迷你占用条、i18n 中英文双份齐全。

用法: python tests/_check_sys_metrics.py
退出码: 0 = 通过；1 = 有断言失败。
"""
import os
import re
import sys
import time

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault('VP_EVENT_RELAY', '0')

fails = []


def check(label, cond, detail=''):
    print(f'  {"OK  " if cond else "FAIL"} {label}' + (f'  {detail}' if detail else ''))
    if not cond:
        fails.append(label)


def read(p):
    with open(os.path.join(ROOT, p), encoding='utf-8') as f:
        return f.read()


# ---------------------------------------------------------------- 1. 采样
print('sysmon 采样：')
from Virus_Platform_Core import sysmon  # noqa: E402

sysmon.reset_peaks()
s1 = sysmon.sample(force=True)
check('首次采样建立 CPU 基线（percent 为 None）',
      s1['cpu'] is not None and s1['cpu']['percent'] is None,
      str(s1.get('cpu')))
check('首次采样仍给出峰值字段', 'peak' in (s1['cpu'] or {}))

check('0.8 秒内重复采样合并为同一份快照',
      sysmon.sample() is sysmon.sample())

time.sleep(1.0)
s2 = sysmon.sample(force=True)
c = s2['cpu'] or {}
m = s2['mem'] or {}
check('第二轮 CPU 占用率在 0~100', c.get('percent') is not None
      and 0 <= c['percent'] <= 100, f"{c.get('percent')}%")
check('CPU 核数 = os.cpu_count', c.get('cores') == (os.cpu_count() or 1),
      f"{c.get('cores')} vs {os.cpu_count()}")

check('内存 total/used/available 齐全且自洽',
      bool(m) and m['total'] > 0 and m['used'] + m['available'] <= m['total'] * 1.02,
      f"total={m.get('total')} used={m.get('used')} avail={m.get('available')}")
check('内存百分比 = used/total',
      bool(m) and (m['percent'] is None
                   or abs(m['percent'] - m['used'] / m['total'] * 100) < 0.2),
      f"{m.get('percent')}%")
check('内存字节数带人类可读串', bool(m.get('total_h')) and bool(m.get('used_h')),
      f"{m.get('used_h')} / {m.get('total_h')}")

p = s2.get('proc')
check('平台进程树能认出主进程', bool(p) and p.get('pid') == os.getpid(),
      str(p and p.get('pid')))
check('平台进程数 ≥ 1 且有人类可读 RSS', bool(p) and p['count'] >= 1 and bool(p['rss_h']),
      str(p and (p['count'], p['rss_h'])))
check('平台进程 RSS 前 N 不超过 5 条', bool(p) and len(p.get('top') or []) <= 5,
      str(p and len(p.get('top') or [])))
check('峰值只增不减（第二轮 CPU/内存峰值 ≥ 当前值）',
      (c.get('peak') is None or c.get('percent') is None
       or c['peak'] >= c['percent'] - 0.01)
      and (m.get('peak') is None or m.get('percent') is None
           or m['peak'] >= m['percent'] - 0.01),
      f"cpu peak={c.get('peak')} mem peak={m.get('peak')}")

sysmon.reset_peaks()
s3 = sysmon.sample(force=True)
check('reset_peaks 后 CPU 重新走基线（percent=None）',
      s3['cpu']['percent'] is None, str(s3['cpu']))

# 纯 ctypes 回退路径：与 psutil 的整机内存口径应基本一致（Windows 才有）
if sys.platform.startswith('win'):
    try:
        t_ct, u_ct, a_ct = sysmon._mem_windows()
        t_ps, u_ps, a_ps = sysmon._mem_psutil()
        check('Windows 原生内存回退与 psutil 口径一致（±2%）',
              abs(t_ct - t_ps) / t_ps < 0.02,
              f'ctypes={t_ct / 1e9:.1f}GB psutil={t_ps / 1e9:.1f}GB')
        b1, tot1 = sysmon._cpu_times_windows()
        time.sleep(0.25)
        b2, tot2 = sysmon._cpu_times_windows()
        check('Windows 原生 CPU 计数器在推进', tot2 > tot1 and b2 >= b1,
              f'total {tot1:.2f} -> {tot2:.2f}')
    except Exception as e:                       # noqa: BLE001
        check('Windows 原生回退可用', False, f'{type(e).__name__}: {e}')

# ---------------------------------------------------------------- 2. 端点
print('\nHTTP 端点：')
try:
    import app as appmod                          # noqa: E402
    client = appmod.app.test_client()
    r = client.get('/api/sys_metrics')
    d = r.get_json() or {}
    check('GET /api/sys_metrics 返回 200', r.status_code == 200, str(r.status_code))
    check('响应带 ok / cpu / mem / proc 四段',
          d.get('ok') is True and 'cpu' in d and 'mem' in d and 'proc' in d,
          str(sorted(d.keys())))
    r2 = client.get('/api/sys_metrics?force=1')
    check('?force=1 强制重采返回 200', r2.status_code == 200
          and (r2.get_json() or {}).get('ok') is True, str(r2.status_code))
except Exception as e:                           # noqa: BLE001
    check('测试客户端可加载 app 并访问端点', False, f'{type(e).__name__}: {e}')

# ---------------------------------------------------------------- 3. 前端
print('\n前端接线：')
nav = read('webapp/templates/_nav.html')
appjs = read('webapp/static/app.js')
css = read('webapp/static/app.css')
i18n = read('webapp/static/i18n.js')

check('_nav.html 有 #sysStripBody 容器', 'id="sysStripBody"' in nav)
check('app.js 轮询 /api/sys_metrics', "'/api/sys_metrics'" in appjs)
check('app.js 3 秒轮询并随页面隐藏暂停',
      'SYS_POLL_MS = 3000' in appjs and 'document.hidden' in appjs)
check('app.js 启动时拉起资源轮询', 'sysMetricsStart();' in appjs)
check('弹层有独立的 #envPopSys 段（不随环境位重绘）',
      "id=\"envPopSys\"" in appjs and 'function envPopSysRender' in appjs)
check('CSS 有迷你占用条与 stale 降级', '.sys-meter' in css and '.sys-bit.stale' in css)
for key in ('hm.sys.cpu', 'hm.sys.mem', 'hm.sys.cpuTip', 'hm.sys.memTip',
            'hm.sys.procNote', 'hm.sys.unavailable'):
    n = len(re.findall(r"'%s':" % re.escape(key), i18n))
    check(f'i18n 中英双份: {key}', n >= 2, f'{n} 份')

# ---------------------------------------------------------------- 4. 清后台小火箭
print('\n清后台小火箭：')
check('顶栏注入 🚀 按钮（navCtxRocket）', 'id="navCtxRocket"' in appjs
      and 'gxStopAllTasks(this)' in appjs)
check('按钮走任务中心既有的 /api/tasks/stop_all（不删记录、不碰结果）',
      "gxStopAllTasks" in appjs and "'/api/tasks/stop_all'" in appjs)
check('确认弹层走 gxModal（与全局清理同款交互）',
      "gxModal(t('gx.rocketTitle'" in appjs)
check('CSS 有琥珀色火箭按钮（亮/暗主题）',
      '.navctx-rocket' in css
      and 'html[data-theme="dark"] .navctx-rocket' in css)
for key in ('gx.rocket', 'gx.rocketTip', 'gx.rocketTitle', 'gx.rocketKeep',
            'gx.rocketConfirm', 'gx.rocketFail'):
    n = len(re.findall(r"'%s':" % re.escape(key), i18n))
    check(f'i18n 中英双份: {key}', n >= 2, f'{n} 份')

print()
if fails:
    print(f'✘ {len(fails)} 项未通过: {fails}')
    raise SystemExit(1)
print('✔ 全部通过')
