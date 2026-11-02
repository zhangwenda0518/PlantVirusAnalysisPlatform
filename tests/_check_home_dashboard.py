# -*- coding: utf-8 -*-
"""总览页监控区回归（真浏览器 + 真 API，不是看模板猜）。

============================================================
2026-10-28：总览从「封面 + 两张卡」扩成工作台
============================================================
本次加的四块，每块都要能被"数据没到位"和"布局崩了"两种情况各自打红：

  A. （2026-10-30 移除）封面行动按钮不再存在；总览改为 AI 工作台
  B. 运行中任务（#runSec / #runList）
     · 无任务时整块 hidden（不许留空占位）
     · 有任务时最多 3 条 + 「还有 N 个」，进度条宽度 = pct
     · 用 renderRunTasks 直接喂假数据，不依赖真有任务在跑
  C. 数据库卡（#dbCards；2026-10-30 起总览不再展示，存在才检查）
     · /api/dbs 必须带 bytes / n_refs（库规模与参考条数）
     · 卡上出现 .mc-meta 且含容量单位
  D. 样品统计（#sampleStat / .sec-more）
     · 统计文字含总数，右侧「管理样品」链接指向 /samples
     · 统计与链接靠右对齐（.sec-split + .sec-fill 撑开）

用法：python tests/_check_home_dashboard.py
依赖 selenium + 本机 Edge/Chrome。
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding='utf-8', errors='replace')
    except (AttributeError, OSError):
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

FAIL = []


def check(name, cond, extra=''):
    print(('  ✔ ' if cond else '  ✘ ') + name + (f'  {extra}' if extra else ''))
    if not cond:
        FAIL.append(name)


def free_port(start=8941):
    for p in range(start, start + 60):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(('127.0.0.1', p))
                return p
            except OSError:
                continue
    raise RuntimeError('无可用端口')


def make_driver():
    from selenium import webdriver
    from selenium.webdriver.edge.options import Options as EdgeOptions
    o = EdgeOptions()
    o.add_argument('--headless=new')
    o.add_argument('--no-sandbox')
    o.add_argument('--window-size=1440,1000')
    try:
        return webdriver.Edge(options=o)
    except Exception:
        from selenium.webdriver.chrome.options import Options as ChromeOptions
        c = ChromeOptions()
        c.add_argument('--headless=new')
        c.add_argument('--window-size=1440,1000')
        return webdriver.Chrome(options=c)


def main():
    port = free_port()
    base = f'http://127.0.0.1:{port}'
    env = dict(os.environ, VP_NO_RECOVER='1')
    logpath = os.path.join(tempfile.gettempdir(), f'vp_home_{port}.log')
    logf = open(logpath, 'w', encoding='utf-8', errors='replace')
    srv = subprocess.Popen(
        [sys.executable, '-c',
         'import sys\n'
         f'sys.path.insert(0, {ROOT!r})\n'
         'from werkzeug.serving import make_server\n'
         'import app\n'
         f"s = make_server('127.0.0.1', {port}, app.app, threaded=True)\n"
         "print('ready', flush=True)\n"
         's.serve_forever()'],
        cwd=ROOT, env=env, stdout=logf, stderr=subprocess.STDOUT)
    drv = None
    try:
        for _ in range(120):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                if s.connect_ex(('127.0.0.1', port)) == 0:
                    break
            time.sleep(0.3)
        else:
            print('服务启动失败:', logpath)
            return 2
        print(f'临时实例 {base}（日志 {logpath}）')

        # ---------- 后端契约（前端靠这些字段渲染） ----------
        print('\n=== A. /api/dbs 元信息 ===')
        import app as _app
        c = _app.app.test_client()
        dbs = c.get('/api/dbs').get_json()
        for key in ('taxonomy', 'host', 'virus'):
            v = dbs.get(key) or {}
            check(f'{key} 带 bytes / n_refs 字段',
                  'bytes' in v and 'n_refs' in v, f"bytes={v.get('bytes')} n_refs={v.get('n_refs')}")
        check('taxonomy 有实际字节数（库存在时必须算得出）',
              (dbs.get('taxonomy') or {}).get('bytes'), str((dbs.get('taxonomy') or {}).get('bytes')))
        check('virus 库能数出参考条数', (dbs.get('virus') or {}).get('n_refs'),
              str((dbs.get('virus') or {}).get('n_refs')))
        check('/api/dbs 带 meta 段（构建时间/版本）',
              isinstance(dbs.get('meta'), dict)
              and 'built_at' in dbs['meta'] and 'git_rev' in dbs['meta'],
              str(dbs.get('meta')))

        print('\n=== B. /api/selfcheck 磁盘占用 ===')
        sc = c.get('/api/selfcheck').get_json()
        disk = [s for s in (sc.get('sections') or []) if s.get('id') == 'disk']
        check('selfcheck 有 disk 段', bool(disk))
        if disk:
            usage = disk[0].get('usage') or []
            check('disk 段带按目录占用（usage）', bool(usage),
                  str([(u['name'], u['bytes']) for u in usage][:3]))
            check('占用项含 bytes / files 字段',
                  all('bytes' in u and 'files' in u for u in usage) if usage else False)

        # ---------- 浏览器 ----------
        print('\n=== C. 页面渲染 ===')
        drv = make_driver()
        drv.set_page_load_timeout(60)
        drv.execute_cdp_cmd('Emulation.setDeviceMetricsOverride',
                            {'width': 1440, 'height': 1000,
                             'deviceScaleFactor': 1, 'mobile': False})
        drv.get(base + '/')
        time.sleep(1.5)
        # 异步渲染：等样品卡出现再量，别用固定 sleep 赌
        for _ in range(40):
            if drv.execute_script(
                    "return document.querySelectorAll('#homeSamples .module-card').length") > 0:
                break
            time.sleep(0.25)
        W = drv.execute_script

        # 2026-10-30：封面行动按钮已移除，空间让给 AI 工作台；
        # 工作台本身的存在性在下面 D 段检查。

        run_hidden = W("return document.getElementById('runSec').hidden")
        check('无运行任务时整块隐藏（不留空占位）', run_hidden is True, str(run_hidden))

        fake = W("""
          renderRunTasks([
            {status:'running', name:'t1', stage:'03 组装', pct:0.42, eta:312, msg:'组装中'},
            {status:'running', name:'t2', stage:'01 宿主去除', pct:0.8, eta:60, msg:''},
            {status:'running', name:'t3', stage:'下载', pct:0.1, eta:900, msg:''},
            {status:'running', name:'t4', stage:'02 鉴定', pct:0.05, eta:1800, msg:''},
            {status:'done', name:'t5', stage:'完成', pct:1, eta:0, msg:''}
          ]);
          const items=[...document.querySelectorAll('#runList .run-item')];
          return {hidden:document.getElementById('runSec').hidden,
                  n:items.length,
                  names:items.map(x=>x.querySelector('b').textContent),
                  w:items[0].querySelector('.run-bar i').style.width,
                  more:(document.querySelector('.run-more')||{}).textContent||''};
        """)
        check('有任务时显示且最多 3 条', (not fake['hidden']) and fake['n'] == 3, str(fake['n']))
        check('只列 running（done 不进列表）',
              fake['names'] == ['t1', 't2', 't3'], str(fake['names']))
        check('进度条宽度 = pct', fake['w'] == '42%', fake['w'])
        check('超过 3 条给「还有 N 个」', '1' in fake['more'], fake['more'])
        check('清空后重新隐藏',
              W("renderRunTasks([]); return document.getElementById('runSec').hidden") is True)

        # 数据库模块（2026-10-30 用户定案从总览移除）：存在才检查，不存在跳过。
        if W("return !!document.getElementById('dbCards')"):
            cards = W("""
              return [...document.querySelectorAll('#dbCards .module-card')].map(c => ({
                name:(c.querySelector('.mc-name')||{}).textContent,
                meta:(c.querySelector('.mc-meta')||{}).textContent||''}));
            """)
            check('库卡渲染出 4 张', len(cards) == 4, str([c['name'] for c in cards]))
            with_meta = [c for c in cards if c['meta']]
            check('至少 3 张库卡带规模信息', len(with_meta) >= 3,
                  str([(c['name'], c['meta']) for c in cards]))
            check('规模信息含容量单位（B/KB/MB/GB）',
                  any(any(u in c['meta'] for u in ('B', 'KB', 'MB', 'GB')) for c in with_meta),
                  str([c['meta'] for c in with_meta]))
        else:
            print('  – 总览未展示数据库模块（用户定案），跳过库卡检查')

        wb = W("""
          return {dock: !!document.querySelector('#aiConsoleHost #aiDock'),
                  files: !!document.getElementById('aiFilesList'),
                  ws: !!document.getElementById('aiWorkspaceBar'),
                  btns: document.querySelectorAll('.cover-act').length};
        """)
        check('总览是 AI 工作台（对话 + 文件面板 + 工作空间）',
              wb['dock'] and wb['files'] and wb['ws'], str(wb))
        check('封面不再放行动按钮（让位给工作台）', wb['btns'] == 0, str(wb['btns']))

        stat = W("""
          const s=document.getElementById('sampleStat');
          const m=[...document.querySelectorAll('.sec-more')].find(a=>a.getAttribute('href')==='/samples');
          const sr=s.getBoundingClientRect(), hr=s.closest('.sec').getBoundingClientRect();
          return {text:s.textContent.trim(), manage:!!m,
                  rightAligned: sr.left > hr.left + 200};
        """)
        check('样品统计文字非空', bool(stat['text']), stat['text'])
        check('统计含样品总数', any(ch.isdigit() for ch in stat['text']), stat['text'])
        check('「管理样品」链接指向 /samples', stat['manage'] is True)
        check('统计靠右对齐（.sec-fill 撑开）', stat['rightAligned'] is True, str(stat))

        ovx = W("return document.documentElement.scrollWidth - window.innerWidth")
        check('页面无横向溢出', ovx <= 1, str(ovx))
    finally:
        if drv is not None:
            try:
                drv.quit()
            except Exception:
                pass
        srv.terminate()
        try:
            srv.wait(timeout=15)
        except Exception:
            srv.kill()
        try:
            logf.close()
        except Exception:
            pass

    print()
    if FAIL:
        print(f'✘ {len(FAIL)} 项未通过:')
        for f in FAIL:
            print('   - ' + f)
        return 1
    print('✔ 全部通过')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
