# -*- coding: utf-8 -*-
"""「在页面中打开」深链接自测（Edge headless）：
  /results?sample=<样品>   → 「已分析样品」里那行高亮
  /results?run=<运行名>    → 切到「专项分析运行」、展开并高亮这次运行
  /tasks                   → 渲染后的内联脚本语法（node --check）

跑法：python tests/_it_page_deeplink_ui.py
自起一个临时服务（8926），不碰用户正在用的 8765；缺 Edge/Selenium 时打 SKIP 返回 0。
"""
import os
import re
import subprocess
import sys
import threading
import time

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault('VP_NO_RECOVER', '1')

from werkzeug.serving import make_server  # noqa: E402
import app as A  # noqa: E402

BASE = 'http://127.0.0.1:8926'


def main():
    cli = A.app.test_client()
    runs = cli.get('/api/tool/runs').get_json() or []
    run_name = runs[0]['name'] if runs else ''
    html = cli.get('/results').get_data(as_text=True)
    ms = re.findall(r'<tr data-project="[^"]*" data-sample="([^"]+)"', html)
    sample = ms[0] if ms else ''

    # 1) /tasks 渲染后的内联脚本语法
    t = cli.get('/tasks').get_data(as_text=True)
    blocks = re.findall(r'<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>', t, re.S)
    bad = 0
    for i, b in enumerate(blocks):
        fn = os.path.join(ROOT, 'run', '_tmp_dl%d.js' % i)
        with open(fn, 'w', encoding='utf-8') as f:
            f.write(b)
        p = subprocess.run(['node', '--check', fn], capture_output=True, text=True)
        if p.returncode != 0:
            bad += 1
            print('  [FAIL] /tasks 内联脚本第 %d 块语法错误: %s' % (i, p.stderr.splitlines()[:2]))
        os.remove(fn)
    print('  [%s] /tasks 内联脚本 %d 块，语法错误 %d'
          % ('PASS' if bad == 0 else 'FAIL', len(blocks), bad))

    try:
        from selenium import webdriver
        from selenium.webdriver.edge.options import Options
    except Exception:
        print('SKIP: 没有 selenium，跳过界面自测')
        return 0 if bad == 0 else 1

    srv = make_server('127.0.0.1', 8926, A.app, threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(1.0)
    opt = Options()
    opt.add_argument('--headless=new')
    opt.add_argument('--window-size=1400,820')
    fails = 0
    drv = None
    try:
        drv = webdriver.Edge(options=opt)
    except Exception:
        print('SKIP: 没有可用的 Edge，跳过界面自测')
        srv.shutdown()
        return 0 if bad == 0 else 1
    try:
        if sample:
            drv.get(BASE + '/results?sample=' + sample)
            time.sleep(3.5)
            hit = drv.execute_script(
                "return document.querySelectorAll('#sampleTbl tr.rs-hit').length;")
            ok = hit == 1
            print('  [%s] /results?sample=%s → 高亮行数 %d' % ('PASS' if ok else 'FAIL', sample, hit))
            fails += 0 if ok else 1
        else:
            print('  [SKIP] 结果中心没有已分析样品，跳过 ?sample= 用例')
        if run_name:
            drv.get(BASE + '/results?run=' + run_name + '#runs')
            time.sleep(5.0)
            st = drv.execute_script("""
              var hit = document.querySelectorAll('#runsBox .rs-hit').length;
              var sec = document.getElementById('sec-runs');
              return {hit: hit, visible: sec ? sec.style.display : '?'};
            """)
            ok = st['hit'] == 1 and st['visible'] != 'none'
            print('  [%s] /results?run=%s → %s' % ('PASS' if ok else 'FAIL', run_name, st))
            fails += 0 if ok else 1
        else:
            print('  [SKIP] tool_runs/ 为空，跳过 ?run= 用例')
    finally:
        try:
            drv.quit()
        except Exception:
            pass
        srv.shutdown()

    total = fails + bad
    if total:
        print('深链接自测失败 %d 项 ✘' % total)
        return 1
    print('深链接自测通过 ✔（/results?sample= + /results?run= + /tasks 语法）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
