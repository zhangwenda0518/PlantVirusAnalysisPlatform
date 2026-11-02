# -*- coding: utf-8 -*-
"""验证：组装结果分类表的 BLASTN / Primer 跳转真的跑起来了。"""
import socket
import subprocess
import sys
import time

ROOT = r'D:\桌面\植物病毒分析平台'
PY = sys.executable


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
     "print('ready', flush=True)\n"
     's.serve_forever()'],
    cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
drv = None
PASS, FAIL = [], []


def check(name, ok, detail=''):
    (PASS if ok else FAIL).append(name)
    print(('  ok  ' if ok else ' FAIL ') + name + (('  ' + str(detail)) if detail else ''))


try:
    for _ in range(150):
        with socket.socket() as s:
            if s.connect_ex(('127.0.0.1', port)) == 0:
                break
        time.sleep(0.3)
    base = 'http://127.0.0.1:%d' % port

    from selenium import webdriver
    from selenium.webdriver.edge.options import Options
    o = Options()
    o.add_argument('--headless=new')
    o.add_argument('--window-size=1440,1200')
    drv = webdriver.Edge(options=o)

    def js(code, *args):
        return drv.execute_script(code, *args)

    CAPTURE = ("window.__msgs=[];"
               "['alert','confirm','prompt'].forEach(f=>{const o=window[f];"
               "window[f]=function(m){window.__msgs.push(f+': '+m);"
               "return f==='confirm'?true:(f==='prompt'?'3':undefined);};});"
               "const ot=window.toast;if(typeof ot==='function'&&!ot.__patched){"
               "const w=function(a,b,c){window.__msgs.push('toast: '+a+' | '+(b||''));"
               "return ot(a,b,c);};w.__patched=true;window.toast=w;}")

    def open_table():
        drv.get(base + '/tools?g=virus#t-contigs')
        time.sleep(3.5)
        js(CAPTURE)
        js("loadViralContigs()")
        time.sleep(2.5)
        return js("""
          const t=document.querySelector('#anaTable table');
          return {rows: t?t.querySelectorAll('tr').length-1:0,
                  contig: t?t.querySelector('tr:nth-child(2) td:nth-child(1)').getAttribute('title'):null,
                  btns: t?[...t.querySelectorAll('tr:nth-child(2) button')].map(b=>b.textContent.trim()):[]};
        """)

    # ---------- 1) 分类报告按钮存在 ----------
    drv.get(base + '/tools?g=virus#t-contigs')
    time.sleep(3.5)
    btn = js("""
      const b=[...document.querySelectorAll('#t-contigs button')]
        .find(x=>/分类报告|Classification report/.test(x.textContent));
      return b?b.textContent.trim():null;
    """)
    check('「📊 分类报告」按钮存在（此前只有文案没有按钮）', bool(btn), btn)

    # ---------- 2) 刷新后点按钮能把分类表拉回来 ----------
    js(CAPTURE)
    for _ in range(30):                       # 等 loadAnaRuns 把 anaCurRun 定下来
        if js("return typeof anaCurRun === 'string' && anaCurRun.length > 0"):
            break
        time.sleep(0.3)
    js("""
      const b=[...document.querySelectorAll('#t-contigs button')]
        .find(x=>/分类报告|Classification report/.test(x.textContent));
      b.click();
    """)
    rows_after = 0
    for _ in range(30):                       # 表格是异步拉的，最多等 12s
        time.sleep(0.4)
        rows_after = js("return document.querySelectorAll('#anaTable table tr').length-1")
        if rows_after and rows_after > 0:
            break
    diag = js("return {run:(typeof anaCurRun!=='undefined'?anaCurRun:null),"
              "msgs:(window.__msgs||[]).slice(0,4)}")
    check('点「分类报告」后分类表渲染出来', rows_after and rows_after > 0,
          '%s 行 %s' % (rows_after, diag))

    # ---------- 3) BLASTN 跳转 ----------
    info = open_table()
    print('   表:', info['rows'], '行; 按钮:', info['btns'])
    js("""
      document.querySelector('#anaTable table').querySelectorAll('tr:nth-child(2) button')[0].click();
    """)
    time.sleep(3.0)
    st = js("""
      const ta=document.getElementById('homSeqText');
      const card=document.getElementById('t-hom');
      const title=document.getElementById('homTitle');
      return {url:location.pathname+location.search+location.hash,
              homVisible:card?card.offsetParent!==null:null,
              seqLen:ta?(ta.value||'').length:0,
              seqHead:ta?(ta.value||'').slice(0,40):null,
              title:title?title.textContent:null,
              msgs:(window.__msgs||[]).slice(0,4)};
    """)
    print('   BLASTN 后:', {k: st[k] for k in ('url', 'homVisible', 'seqLen', 'title')})
    check('BLASTN：切到病毒注释分析组并显示同源比对卡',
          st['homVisible'] is True and 't-hom' in (st['url'] or ''), st['url'])
    check('BLASTN：contig 序列已填入 #homSeqText', (st['seqLen'] or 0) > 50, st['seqLen'])
    check('BLASTN：卡片标题指向该 contig',
          bool(st['title']) and ('BLAST' in (st['title'] or '') or '⏳' in (st['title'] or '')),
          st['title'])

    # 等分析出结果（本地 DIAMOND，通常几秒）
    done = None
    for _ in range(30):
        time.sleep(2.0)
        done = js("""
          const r=document.getElementById('homResult');
          const b=document.getElementById('homBody');
          return {shown:r?r.style.display:null, len:b?b.innerHTML.length:0,
                  head:b?(b.innerText||'').slice(0,90).replace(/\\s+/g,' '):''};
        """)
        head = (done.get('head') or '')
        if done['shown'] != 'none' and done['len'] > 40 and '初始化' not in head:
            break
    check('BLASTN：结果渲染进同源比对卡（不再静默无动作）',
          done and done['shown'] != 'none' and done['len'] > 40, done)

    # ---------- 4) Primer 跳转 ----------
    info = open_table()
    js("""
      document.querySelector('#anaTable table').querySelectorAll('tr:nth-child(2) button')[2].click();
    """)
    time.sleep(4.0)
    pr = {'len': 0, 'path': '', 'val': None, 'msgs': []}
    for _ in range(20):                 # examples.js 的 autoFill 在 400ms 后才跑
        time.sleep(0.5)
        pr = js("""
          const el=document.getElementById('pd_fa');
          return {path:location.pathname, val:el?(el.value||'').slice(0,40):null,
                  len:el?(el.value||'').length:0,
                  msgs:(window.__msgs||[]).slice(0,4)};
        """)
        if pr['path'] == '/primer' and (pr['len'] or 0) > 50 and str(pr['val']).startswith('>'):
            break
    print('   Primer 后:', {k: pr[k] for k in ('path', 'len')})
    check('Primer：跳到独立模块 /primer', pr['path'] == '/primer', pr['path'])
    check('Primer：contig 序列已带入 #pd_fa',
          (pr['len'] or 0) > 50 and str(pr['val']).startswith('>'), pr['val'])
finally:
    if drv:
        drv.quit()
    srv.terminate()
    time.sleep(1)

print('\n%d 通过 / %d 失败' % (len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
