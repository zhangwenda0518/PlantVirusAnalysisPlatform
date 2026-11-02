# -*- coding: utf-8 -*-
"""浏览器验证：组装引擎切换时「组装模式」跟着换，MEGAHIT 下不出现 SPAdes 模式。"""
import json
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

    print('=== 初始状态（auto + 本机有 MEGAHIT）===')
    st = js("""
      const m = document.getElementById('a_mode');
      return {hasMegahit: !!window.VP_HAS_MEGAHIT,
              engine: document.getElementById('a_engine').value,
              modes: [...m.options].map(o => o.value),
              mode: m.value,
              srnaShown: (document.getElementById('a_srna_wrap')||{}).style.display};
    """)
    print('   ', st)
    check('模板注入 VP_HAS_MEGAHIT 且为 true', st['hasMegahit'] is True, st['hasMegahit'])
    check('auto 下只出 MEGAHIT 的三档模式',
          st['modes'] == ['default', 'sensitive', 'large'], st['modes'])
    check('auto 默认选 default', st['mode'] == 'default', st['mode'])
    check('MEGAHIT 下隐藏「小RNA」', st['srnaShown'] == 'none', st['srnaShown'])

    print('=== 切到 SPAdes ===')
    js("""
      const e = document.getElementById('a_engine');
      e.value = 'spades';
      e.dispatchEvent(new Event('change'));
    """)
    time.sleep(0.5)
    st2 = js("""
      const m = document.getElementById('a_mode');
      return {modes: [...m.options].map(o => o.value), mode: m.value,
              srnaShown: (document.getElementById('a_srna_wrap')||{}).style.display};
    """)
    print('   ', st2)
    check('SPAdes 下出 5 个 SPAdes 模式',
          st2['modes'] == ['rnaviral', 'metaviral', 'rna', 'meta', 'srna'],
          st2['modes'])
    check('SPAdes 默认 rnaviral', st2['mode'] == 'rnaviral', st2['mode'])
    check('SPAdes 下显示「小RNA」', st2['srnaShown'] != 'none', st2['srnaShown'])

    print('=== 切回 MEGAHIT，且验证参数翻译 ===')
    js("""
      const e = document.getElementById('a_engine');
      e.value = 'megahit';
      e.dispatchEvent(new Event('change'));
      const m = document.getElementById('a_mode');
      m.value = 'sensitive';
    """)
    time.sleep(0.5)
    st3 = js("""
      const m = document.getElementById('a_mode');
      return {modes: [...m.options].map(o => o.value), mode: m.value,
              params: asmParams()};
    """)
    print('   ', st3)
    check('MEGAHIT 模式切回后只有 3 档', st3['modes'] == ['default', 'sensitive', 'large'],
          st3['modes'])
    check('asmParams 带 megahit_mode=sensitive 且不混入 SPAdes mode',
          st3['params'].get('megahit_mode') == 'sensitive'
          and st3['params'].get('assembler') == 'megahit', st3['params'])

    print('=== 勾小RNA 时强制 SPAdes ===')
    js("""
      const e = document.getElementById('a_engine');
      e.value = 'spades';
      e.dispatchEvent(new Event('change'));
      const s = document.getElementById('a_srna');
      s.checked = true;
      s.dispatchEvent(new Event('change'));
    """)
    time.sleep(0.4)
    st4 = js("return {mode: document.getElementById('a_mode').value, params: asmParams()}")
    print('   ', st4)
    check('小RNA → mode=srna 且 assembler=spades',
          st4['mode'] == 'srna' and st4['params'].get('mode') == 'srna', st4)


    print('=== 一键分析卡（vc_）也要联动 ===')
    js("""
      const c = document.getElementById('t-virchain');
      if (c) c.scrollIntoView();
    """)
    time.sleep(0.3)
    vc = js("""
      const m = document.getElementById('vc_mode');
      const e = document.getElementById('vc_engine');
      return {hasEngine: !!e, engine: e ? e.value : null,
              modes: m ? [...m.options].map(o => o.value) : null,
              params: (typeof vcParams === 'function') ? vcParams() : null};
    """)
    print('   ', vc)
    check('链式卡有引擎下拉且默认 auto', vc['hasEngine'] and vc['engine'] == 'auto', vc)
    check('链式卡 auto 下只出 MEGAHIT 模式',
          vc['modes'] == ['default', 'sensitive', 'large'], vc['modes'])
    check('链式卡参数带 assembler/megahit_mode',
          vc['params'] and vc['params'].get('assembler') == 'auto'
          and vc['params'].get('megahit_mode') == 'default', vc['params'])

finally:
    if drv:
        drv.quit()
    srv.terminate()
    time.sleep(1)

print('\n%d 通过 / %d 失败' % (len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
