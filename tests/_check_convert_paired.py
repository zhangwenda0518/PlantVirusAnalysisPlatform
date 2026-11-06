# -*- coding: utf-8 -*-
"""格式转换「双端输入」的真实浏览器验收（Edge headless）。

2026-09-30 需求：格式转换支持 R1/R2 双端，双端分别转换、分别输出。

锁定：cv_input2 字段渲染；双端提交后输出区列出 R1/R2 两个产物、
日志含两条 seqkit 命令。
用法: python tests/_check_convert_paired.py
"""
import os
import sys
import time

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors='replace')
    except (AttributeError, OSError):
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ['VP_NO_RECOVER'] = '1'

FAIL = []


def check(label, ok, extra=''):
    print(f'  [{"PASS" if ok else "FAIL"}] {label}' + (f'  {extra}' if extra else ''),
          flush=True)
    if not ok:
        FAIL.append(label)


def main():
    try:
        from selenium import webdriver
        from selenium.webdriver.common.by import By
        from selenium.webdriver.edge.options import Options as EdgeOptions
        from selenium.webdriver.support.ui import WebDriverWait
    except ImportError as e:
        print(f'[SKIP] selenium 未安装: {e}')
        return 0

    import app as appmod

    from werkzeug.serving import make_server
    srv = make_server('127.0.0.1', 0, appmod.app, threaded=True)
    port = srv.server_address[1]
    import threading
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{port}'
    print(f'临时实例: {base}\n', flush=True)

    opts = EdgeOptions()
    opts.add_argument('--headless=new')
    opts.add_argument('--no-sandbox')
    opts.add_argument('--disable-gpu')
    opts.add_argument('--window-size=1440,1000')
    drv = webdriver.Edge(options=opts)
    try:
        drv.set_page_load_timeout(30)
        wait = WebDriverWait(drv, 60)
        drv.get(base + '/tools?g=sample')
        wait.until(lambda d: d.find_elements(By.ID, 'cv_input'))
        drv.execute_script("showModule && showModule('t-convert')")
        time.sleep(0.5)

        inp2 = drv.find_element(By.ID, 'cv_input2')
        check('R2 输入框渲染', inp2.is_displayed())

        r1 = os.path.join(ROOT, 'examples', 'example_sim100x_R1.fastq.gz')
        r2 = os.path.join(ROOT, 'examples', 'example_sim100x_R2.fastq.gz')
        drv.execute_script('arguments[0].value = arguments[1];',
                           drv.find_element(By.ID, 'cv_input'), r1)
        drv.execute_script('arguments[0].value = arguments[1];', inp2, r2)
        btn = drv.find_element(
            By.XPATH, "//button[contains(@onclick,'runTool')]")
        drv.execute_script("runTool('convert', arguments[0]);", btn)

        def done(d):
            txt = d.find_element(By.ID, 'toolrun-convert').text
            return ('已完成' in txt or 'failed' in txt.lower()) and 'fa.gz' in txt
        wait.until(done)
        # 2026-11-06 口径更新：已完成任务的卡内日志只保留尾 5 行（tasks.py
        # list_all 瘦身），R1 的 [CMD] 行通常已滑出窗口；产物按钮也收进
        # 「其他文件」<details> 折叠（collapsed 内容不进 innerText）。检查
        # 随产品演进：点开折叠后断言两个产物按钮可见；两条 fq2fa 命令改从
        # 完整任务日志断言（任务卡「日志」可单独拉全量，用户路径不变）。
        box_el = drv.find_element(By.ID, 'toolrun-convert')
        for s in box_el.find_elements(By.TAG_NAME, 'summary'):
            drv.execute_script('arguments[0].click();', s)   # 展开全部折叠
        box = box_el.text
        check('输出区列出 R1 产物', 'example_sim100x_R1.fa.gz' in box)
        check('输出区列出 R2 产物', 'example_sim100x_R2.fa.gz' in box)
        tid = drv.execute_script(
            "return fetch('/api/tasks').then(r=>r.json()).then(ts=>{"
            " const arr=ts.active||ts||[]; const t=arr.filter("
            " x=>x.name && x.name.indexOf('格式转换')>=0)[0]||{};"
            " return t.id||'';})")
        n_cmd = 0
        if tid:
            full_log = drv.execute_script(
                "return fetch('/api/task/' + arguments[0] + '/log')"
                ".then(r=>r.json()).then(d=>(d.log||[]).join('\\n'));", tid)
            n_cmd = full_log.count('fq2fa')
        check('运行日志含两条转换命令（R1 与 R2）', n_cmd >= 2,
              f'完整日志 fq2fa 出现 {n_cmd} 次')
        check('任务状态为已完成', '已完成' in box)
    finally:
        drv.quit()
        srv.shutdown()

    print('\n' + ('ALL PASS' if not FAIL else f'FAILED: {FAIL}'))
    return 0 if not FAIL else 1


if __name__ == '__main__':
    sys.exit(main())
