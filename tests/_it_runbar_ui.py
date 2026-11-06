# -*- coding: utf-8 -*-
"""全局运行条 UI 自测（Edge headless）：展开 / 归档 / 恢复 全流程 + 截图。

跑法：python tests/_it_runbar_ui.py（需要 selenium 与本机 Edge）。
截图写到 run/_it_runbar/（run/ 已被 .gitignore 忽略）。"""
import os
import sys
import time
import shutil
import tempfile
import threading
from collections import deque

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ['VP_NO_RECOVER'] = '1'

# 隔离（2026-11-06 归账）：截图与任何落盘统一进临时根，不再写真实 run/
tmp = tempfile.mkdtemp(prefix='vp_runbar_')
os.environ['VP_RUN_ROOT'] = tmp

import app as APP
from Virus_Platform_Core.web import tasks as T

T.DIRS['tasks'] = tmp
m = T.tm
now = time.time()


def rec(tid, name, status, pct, msg):
    return {'id': tid, 'name': name, 'status': status, 'stage': '分析中',
            'pct': pct, 'msg': msg, 'error': '',
            'log': deque(['log a', 'log b'], maxlen=500),
            'started': now - 600, 'finished': now - 30 if status == 'done' else None,
            'cancel': threading.Event(), 'procs': [], 'weight': 'light',
            'log_file': None, 'link': None, 'out_dir': None, 'restart': None,
            'result': None}


A = 'deadbeef1010'
B = 'deadbeef2020'
m.tasks[A] = rec(A, '公共检索 Lycium barbarum', 'running', 0.42, '命中 12 条 Run · 提取元数据')
m.order.insert(0, A)
m.tasks[B] = rec(B, '工具运行 · vp_index_build', 'done', 1.0, '完成')
m.order.insert(0, B)
m._persist(m.tasks[A])
m._persist(m.tasks[B])

from werkzeug.serving import make_server

srv = make_server('127.0.0.1', 8899, APP.app, threaded=True)
th = threading.Thread(target=srv.serve_forever, daemon=True)
th.start()
time.sleep(1.0)


def _cleanup():
    for tid in (A, B):
        m.tasks.pop(tid, None)
        try:
            m.order.remove(tid)
        except ValueError:
            pass
    shutil.rmtree(tmp, ignore_errors=True)


try:
    from selenium import webdriver
    from selenium.webdriver.edge.options import Options
    opt = Options()
    opt.add_argument('--headless=new')
    opt.add_argument('--disable-gpu')
    opt.add_argument('--window-size=1440,900')
    drv = webdriver.Edge(options=opt)
except Exception as e:      # 与其它浏览器用例同口径：无浏览器就 SKIP，不阻塞批量
    print('SKIP: 没有可用的 Edge/Selenium，跳过界面自测 ->', e)
    srv.shutdown()
    _cleanup()
    sys.exit(0)
SHOT_DIR = os.path.join(ROOT, 'run', '_it_runbar')
os.makedirs(SHOT_DIR, exist_ok=True)
shot = os.path.join(SHOT_DIR, 'runbar_expanded.png')
try:
    drv.get('http://127.0.0.1:8899/tasks')
    for _ in range(40):
        bar = drv.find_elements('css selector', '#vpRunBar:not([hidden])')
        if bar:
            break
        time.sleep(0.25)
    assert bar, '运行条没有出现'
    drv.find_element('css selector', '#rbHead').click()
    time.sleep(0.8)
    rows = drv.find_elements('css selector', '#vpRunBar .rb-row')
    names = [r.find_element('css selector', '.rb-name').text for r in rows]
    print('rows:', len(rows), names)
    assert any('公共检索' in n for n in names), names
    assert any('工具运行' in n for n in names), names
    acts = drv.find_elements('css selector', '#vpRunBar .rb-row .btn')
    labels = [a.text for a in acts]
    print('buttons:', labels)
    assert any('归档' in s for s in labels), labels
    assert any('停止' in s for s in labels), labels
    # 运行中那行必须有进度条（pct=0.42）
    pct = drv.execute_script(
        "var r=document.querySelector('#vpRunBar .rb-row.running .rb-bar i');"
        "return r ? r.getBoundingClientRect().width : -1;")
    print('running bar width:', pct)
    assert pct > 0, pct
    # 外部任务（下载批次）不给「归档」按钮：那些行没有 .btn
    for row in rows:
        nm = row.find_element('css selector', '.rb-name').text
        btns = row.find_elements('css selector', '.btn')
        if nm.startswith('下载'):
            assert not btns, (nm, [b.text for b in btns])
    drv.save_screenshot(shot)
    print('screenshot ->', shot)

    # 点「📦 归档」→ 确认框 → 记录进 /api/tasks?full=1 的 archived，运行条里消失
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    target = None
    for row in drv.find_elements('css selector', '#vpRunBar .rb-row'):
        if '工具运行' in row.find_element('css selector', '.rb-name').text:
            target = row.find_element('css selector', '.btn')
            break
    assert target is not None, '没找到工具运行行的归档按钮'
    target.click()
    WebDriverWait(drv, 8).until(EC.alert_is_present()).accept()
    time.sleep(2.0)
    import json
    import urllib.request
    d = json.load(urllib.request.urlopen('http://127.0.0.1:8899/api/tasks?full=1&logs=0'))
    assert any(x['id'] == B for x in d['archived']), [x['name'] for x in d['archived']]
    assert not any(x['id'] == B for x in d['active']), '归档后仍在 active'
    names2 = [r.find_element('css selector', '.rb-name').text
              for r in drv.find_elements('css selector', '#vpRunBar .rb-row')]
    print('after archive rows:', names2)
    assert not any('工具运行' in n for n in names2), names2
    drv.save_screenshot(os.path.join(SHOT_DIR, 'runbar_after_archive.png'))

    # 任务中心 → 历史归档 → 点「↩ 恢复」→ 回到运行列表
    drv.get('http://127.0.0.1:8899/tasks')
    restore_btn = None
    for _ in range(40):
        for b in drv.find_elements('css selector', '#archList .btn'):
            if '恢复' in b.text:
                restore_btn = b
                break
        if restore_btn:
            break
        time.sleep(0.25)
    assert restore_btn is not None, '历史归档里没有「恢复」按钮'
    # 列表每 10s 重绘，取到的元素可能已被替换：直接在页面里按文本点当前节点
    res = drv.execute_script(
        "var bs=[...document.querySelectorAll('#archList .btn')];"
        "var b=bs.find(x=>x.textContent.indexOf('恢复')>=0);"
        "if(!b) return 'none'; b.click(); return 'clicked';")
    print('restore click:', res)
    print('typeof restoreTask:', drv.execute_script('return typeof restoreTask'),
          '| typeof archiveTask:', drv.execute_script('return typeof archiveTask'))
    time.sleep(1.5)
    try:
        al = drv.switch_to.alert
        print('ALERT:', al.text)
        al.accept()
    except Exception:
        print('no alert')
    time.sleep(1.0)
    d = json.load(urllib.request.urlopen('http://127.0.0.1:8899/api/tasks?full=1&logs=0'))
    assert any(x['id'] == B for x in d['active']), '恢复后不在 active'
    assert not any(x['id'] == B for x in d['archived']), '恢复后仍在 archived'
    drv.save_screenshot(os.path.join(SHOT_DIR, 'tasks_restored.png'))

    # 再从任务中心列表点「📦 归档」→ 应回到 archived
    card = None
    for _ in range(40):
        for c in drv.find_elements('css selector', '#taskList .task-card'):
            if '工具运行' in c.text:
                card = c
                break
        if card:
            break
        time.sleep(0.25)
    assert card is not None, '任务列表里没有工具运行卡'
    print('card:', card.text.splitlines()[:2])
    btn = None
    for b in card.find_elements('css selector', '.btn'):
        if '归档' in b.text:
            btn = b
            break
    assert btn is not None, [x.text for x in card.find_elements('css selector', '.btn')]
    drv.execute_script('arguments[0].click()', btn)
    print('list archive click: ok')
    WebDriverWait(drv, 8).until(EC.alert_is_present()).accept()
    time.sleep(1.8)
    d = json.load(urllib.request.urlopen('http://127.0.0.1:8899/api/tasks?full=1&logs=0'))
    assert any(x['id'] == B for x in d['archived']), '列表归档没生效'
    assert not any(x['id'] == B for x in d['active']), '列表归档后仍在 active'
    drv.save_screenshot(os.path.join(SHOT_DIR, 'tasks_archived.png'))
finally:
    drv.quit()
    srv.shutdown()

_cleanup()
print('全局运行条 UI 自测通过')

