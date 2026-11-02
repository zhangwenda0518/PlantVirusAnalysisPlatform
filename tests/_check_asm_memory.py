# -*- coding: utf-8 -*-
"""验证：组装内存默认=物理内存一半、提示文案、超量确认。"""
import json
import os
import socket
import subprocess
import sys
import time

ROOT = r'D:\桌面\植物病毒分析平台'
PY = sys.executable
PASS, FAIL = [], []


def check(name, ok, detail=''):
    (PASS if ok else FAIL).append(name)
    print(('  ok  ' if ok else ' FAIL ') + name + (('  ' + str(detail)) if detail else ''))


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


port = free_port()
srv = subprocess.Popen(
    [PY, '-c',
     'import sys\n'
     f'sys.path.insert(0, {ROOT!r})\n'
     'from werkzeug.serving import make_server\n'
     'import app\n'
     f"s = make_server('127.0.0.1', {port}, app.app, threaded=True)\n"
     's.serve_forever()'],
    cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
drv = None
try:
    for _ in range(150):
        with socket.socket() as s:
            if s.connect_ex(('127.0.0.1', port)) == 0:
                break
        time.sleep(0.3)

    sys.path.insert(0, ROOT)
    from Virus_Platform_Core.assembly import (assembler_memory_cap_gb,
                                              default_assembler_memory_gb,
                                              physical_ram_gb)
    ram = physical_ram_gb()
    dflt = default_assembler_memory_gb()
    cap = assembler_memory_cap_gb()
    print('本机: ram=%dGB 默认=%dGB 上限=%dGB' % (ram, dflt, cap))

    from selenium import webdriver
    from selenium.webdriver.edge.options import Options
    o = Options()
    o.add_argument('--headless=new')
    o.add_argument('--window-size=1440,1100')
    drv = webdriver.Edge(options=o)

    def js(code, *a):
        return drv.execute_script(code, *a)

    drv.get('http://127.0.0.1:%d/tools#t-assemble' % port)
    time.sleep(3.5)
    js("window.__msgs=[]; window.confirm=function(m){window.__msgs.push(m);return false;};")

    st = js("""
      const m = document.getElementById('a_mem');
      return {ram: window.VP_RAM_GB, dflt: window.VP_ASM_MEM_DEFAULT,
              cap: window.VP_ASM_MEM_CAP, value: m.value, max: m.max,
              hint: (document.getElementById('a_mem_hint')||{}).textContent};
    """)
    print('   ', st)
    check('注入物理内存值', st['ram'] == ram, st['ram'])
    check('默认值 = 物理内存一半', int(st['value']) == dflt, st['value'])
    check('max = 物理内存', int(st['max']) == ram, st['max'])
    check('提示含物理内存与建议上限',
          str(ram) in (st['hint'] or '') and str(cap) in (st['hint'] or ''), st['hint'])

    st2 = js("""
      const m = document.getElementById('a_mem');
      m.value = String(arguments[0]);
      m.dispatchEvent(new Event('input'));
      return (document.getElementById('a_mem_hint')||{}).textContent;
    """, cap + 20)
    print('   超上限提示:', st2)
    check('超过上限时提示会被钳制', '钳制' in (st2 or ''), st2)

    st3 = js("""
      const m = document.getElementById('a_mem');
      m.value = String(arguments[0]);
      m.dispatchEvent(new Event('input'));
      return (document.getElementById('a_mem_hint')||{}).textContent;
    """, max(4, int(ram * 0.9)))
    print('   高占用提示:', st3)
    check('接近物理内存时提示可能卡顿',
          '卡顿' in (st3 or '') or '钳制' in (st3 or ''), st3)

    conf = js("return {ok: asmMemConfirm(arguments[0]), msgs: window.__msgs}", max(4, int(ram * 0.9)))
    print('   确认框:', conf)
    check('高内存提交前要求确认（默认拒绝）', conf['ok'] is False and bool(conf['msgs']), conf)
    conf2 = js("return {ok: asmMemConfirm(arguments[0])}", dflt)
    check('默认值不弹确认', conf2['ok'] is True, conf2)

    # 后端：不传 memory 时用默认值
    import urllib.request
    td = os.path.join(ROOT, '3rd', 'tools', 'megahit-1.2.9-win64', 'share',
                      'megahit', 'test_data')
    body = json.dumps({'tool': 'assemble', 'threads': 2, 'params': {
        'r1': os.path.join(td, 'r3_1.fa'), 'mode': 'rnaviral',
        'assembler': 'megahit', 'megahit_mode': 'sensitive', 'min_len': 100}}).encode()
    req = urllib.request.Request('http://127.0.0.1:%d/api/tool/run' % port, data=body,
                                 headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            d = json.loads(r.read().decode())
        tid = d['task']
        snap = {}
        for _ in range(40):
            time.sleep(2)
            with urllib.request.urlopen('http://127.0.0.1:%d/api/task/%s' % (port, tid), timeout=30) as r:
                snap = json.loads(r.read().decode())
            if snap.get('status') in ('done', 'failed', 'cancelled'):
                break
        logs = [ln for ln in (snap.get('log') or []) if 'MEGAHIT 组装' in ln]
        print('   后端日志:', logs)
        check('不传 memory 时后端用默认一半',
              bool(logs) and ('mem=%dGB' % dflt) in logs[0], logs[:1])
    except Exception as e:
        check('后端默认内存', False, e)
finally:
    if drv:
        drv.quit()
    srv.terminate()
    time.sleep(1)

print('\n%d 通过 / %d 失败' % (len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
