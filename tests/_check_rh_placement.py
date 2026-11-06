# -*- coding: utf-8 -*-
"""全模块「历史运行区落点」检查（2026-11-05）。

背景：kvsuite 的历史区容器曾被嵌在富结果面板 kvResult 内部，用户点 ✕
（面板 display:none）时历史区被连带藏掉。修掉后，本脚本把同类检查铺到
**全部模块**，两道断言：

  A. 初始化：每个页面上的 .rh[id] 容器都必须已渲染出 .rh-head（RH_DEFS
     登记齐全，没有"空壳容器"）；
  B. 落点：每个 rh 容器的 DOM 父链不得穿过任何「✕ 可隐藏容器」——
     名单从静态模板扫出（onclick="$('X').style.display='none'" 的 id，
     含 RICH_BOX 之外的 anaResult / homResult / mir-detail / sumModal），
     在浏览器里用 closest 逐 id 验证（自动注入的 rh 也被覆盖）。

页面：/tools（一次加载即含全部 20+ 模块的 rh）+ 6 个独立页。
用法：python tests/_check_rh_placement.py   （需要本机 Edge；无则 SKIP）
"""
import os
import re
import socket
import subprocess
import sys
import time

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors='replace')
    except (AttributeError, OSError):
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

PASS, FAIL = [], []


def check(name, ok, detail=''):
    (PASS if ok else FAIL).append(name)
    print(('  ok  ' if ok else ' FAIL ') + name
          + (('  ' + str(detail)) if detail else ''), flush=True)


def hider_ids():
    """静态扫模板：✕ 能隐藏的全部容器 id。"""
    ids = set()
    pat = re.compile(r"\$\('([A-Za-z0-9_\-]+)'\)\.style\.display='none'")
    for f in os.listdir(os.path.join(ROOT, 'webapp', 'templates')):
        if not f.endswith('.html'):
            continue
        html = open(os.path.join(ROOT, 'webapp', 'templates', f),
                    encoding='utf-8').read()
        ids |= set(pat.findall(html))
    return sorted(ids)


def main():
    try:
        from selenium import webdriver
        from selenium.webdriver.edge.options import Options
    except ImportError as e:
        print(f'[SKIP] selenium 未安装: {e}')
        return 0

    hiders = hider_ids()
    check(f'静态：✕ 可隐藏容器名单提取（{len(hiders)} 个）', len(hiders) >= 15,
          ','.join(hiders))

    from werkzeug.serving import make_server
    import app as appmod
    srv = make_server('127.0.0.1', 0, appmod.app, threaded=True)
    port = srv.server_address[1]
    import threading
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{port}'

    drv = None
    try:
        opts = Options()
        opts.add_argument('--headless=new')
        opts.add_argument('--no-sandbox')
        drv = webdriver.Edge(options=opts)
        drv.set_page_load_timeout(30)

        # 父链检查 JS：rh 容器向上走，命中任一可隐藏 id 即违规
        PROBE = (
            "const HID = " + repr(hiders).replace("'", '"') + ";\n"
            "return [...document.querySelectorAll('div.rh[id]')].map(b => ({\n"
            "  id: b.id,\n"
            "  head: !!b.querySelector('.rh-head'),\n"
            "  bad: HID.find(h => b.closest('#' + h) !== null) || ''\n"
            "}));")

        pages = ['/tools', '/hostremoval', '/genome', '/annotation',
                 '/orf', '/primer', '/cds-export']
        for page in pages:
            drv.get(base + page)
            time.sleep(2.5)               # 等 rhInitContainers / 自动注入
            boxes = drv.execute_script(PROBE)
            if not boxes:
                check(f'{page}：页面含历史区容器', False, '0 个 .rh 容器')
                continue
            no_head = [b['id'] for b in boxes if not b['head']]
            check(f'{page}：{len(boxes)} 个历史区全部初始化',
                  not no_head, '空壳: ' + ','.join(no_head) if no_head else '')
            bad = [f"{b['id']}→{b['bad']}" for b in boxes if b['bad']]
            check(f'{page}：父链无 ✕ 可隐藏容器（✕ 不会连带藏）',
                  not bad, '嵌错: ' + ', '.join(bad) if bad else '')
    finally:
        if drv:
            drv.quit()
        srv.shutdown()

    print('\n%d 通过 / %d 失败' % (len(PASS), len(FAIL)))
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
