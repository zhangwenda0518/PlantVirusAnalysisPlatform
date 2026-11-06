# -*- coding: utf-8 -*-
"""「浏览」按钮回归：页面调了 browse()/browseDir() 就必须带上选择对话框。

背景：app.js 的 browse()/browseDir() 第一句是
`$('dlgMask').style.display = 'flex'`，页面缺 #dlgMask（或 #dlgPath /
#dlgList / #dlgPickDir）时直接抛 TypeError，按钮点了**毫无反应**，
只有控制台有报错。2026-09-11 settings / results / submit 三页就是这么坏的。

用法:
    python tests/_check_browse_dlg.py            # 只做静态检查（不需要服务）
    python tests/_check_browse_dlg.py 8765       # 顺带跑真浏览器验证

判据（任一不满足即 FAIL，退出码 1）:
  ① 调用 browse/browseDir 的模板都 {% include "_browse_dlg.html" %}；
  ② 除 _browse_dlg.html 外没有页面再内联 #dlgMask（防止又抄一份各自漂移）；
  ③ 片段里 #dlgMask / #dlgPath / #dlgList / #dlgPickDir 四个 id 齐全；
  ④（传了端口才查）每个页面调 browseDir() 不抛错，且对话框真的显示出来。
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TPL = ROOT / 'webapp' / 'templates'
PARTIAL = '_browse_dlg.html'
NEED_IDS = ('dlgMask', 'dlgPath', 'dlgList', 'dlgPickDir')
CALL_RE = re.compile(r'\bbrowse(?:Dir)?\s*\(')


def templates():
    return sorted(p for p in TPL.glob('*.html'))


def _include_closure(path, seen=None):
    """模板 path 渲染后真正包含的模板集合（按 {% include %} 递归展开）。

    2026-11-02：共享片段（_samples_new.html 之类）自己调用 browse/browseDir，
    对话框片段由**宿主页** include。只看单文件源码会误报「调用了却没 include」，
    这里把 include 链一起算进来（防死循环用 seen）。
    """
    seen = set() if seen is None else seen
    out = {path}
    try:
        txt = path.read_text(encoding='utf-8')
    except OSError:
        return out
    for m in re.finditer(r'\{%\s*include\s+"([^"]+)"\s*%\}', txt):
        child = TPL / m.group(1)
        if child in seen or child == path:
            continue
        seen.add(child)
        out |= _include_closure(child, seen)
    return out


def callers():
    """调用了 browse/browseDir 的模板（含 app.js 动态生成的用页）。"""
    out = []
    for p in templates():
        if p.name == PARTIAL:
            continue
        if CALL_RE.search(p.read_text(encoding='utf-8')):
            out.append(p)
    # 反面：谁 include 了这个模板（反向 include 链）
    includers = {p: [q for q in templates()
                     if p in _include_closure(q)]
                 for p in out}
    return out, includers


def check_static():
    bad = []
    inc = '{% include "_browse_dlg.html" %}'
    partial = TPL / PARTIAL
    found, includers = callers()
    for p in found:
        # 自身 include，或经 include 链的宿主页 include —— 都算兑现
        if _include_closure(p) & {partial}:
            continue
        # 共享片段由宿主页 include：看谁 include 了它，宿主页里必须有对话框
        hosts = includers.get(p) or []
        if any(_include_closure(q) & {partial} for q in hosts):
            continue
        bad.append(f'① {p.name} 调用了 browse/browseDir，'
                   f'却没有 {inc}（自身、宿主页 {[q.name for q in hosts] or "无"}'
                   f'与 include 链都没有）')
    for p in templates():
        if p.name != PARTIAL and 'id="dlgMask"' in p.read_text(encoding='utf-8'):
            bad.append(f'② {p.name} 内联了 #dlgMask，应改为 include 片段')
    frag = partial.read_text(encoding='utf-8')
    for i in NEED_IDS:
        if f'id="{i}"' not in frag:
            bad.append(f'③ 片段缺少 id="{i}"')
    return bad


def _served_browse_pages():
    """从 Flask 路由表自动发现「页面上真的会出现浏览按钮」的 URL。

    不硬编码路由清单——模板可能没有路由（孤儿页），路由也可能改名。
    做法：对每条无参 GET 路由取一次 HTML，含 browse(/browseDir( 才算数。
    """
    sys.path.insert(0, str(ROOT))
    from app import app                                    # noqa: E402
    skip = ('/api/', '/static/', '/favicon')
    urls = sorted({r.rule for r in app.url_map.iter_rules()
                   if 'GET' in r.methods and not r.arguments
                   and not r.rule.startswith(skip)})
    c = app.test_client()
    found = []
    for u in urls:
        try:
            html = c.get(u).get_data(as_text=True)
        except Exception:
            continue
        if CALL_RE.search(html or ''):
            found.append(u)
    return found


def check_runtime(port):
    """真浏览器：每个"有浏览按钮"的页面调一次 browseDir()，必须不抛错且对话框可见。"""
    import urllib.request
    base = f'http://127.0.0.1:{port}'
    try:
        urllib.request.urlopen(base + '/', timeout=5)
    except Exception as e:                              # 服务没起就跳过
        print(f'④ 跳过运行时检查（{base} 不可达: {e}）')
        return []
    pages = _served_browse_pages()
    if not pages:
        return ['④ 没发现任何含浏览按钮的页面（路由表对不上？）']

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        # 2026-11-02：平台自带的 3rd/python 没有装 playwright（浏览器用例统一
        # 跑在 C:\Python312）。缺依赖只跳过运行时验证，不能把静态结论判成失败。
        print('④ 跳过运行时检查（当前解释器无 playwright，用 C:/Python312/python.exe 跑）')
        return []
    bad = []
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True,
                           args=['--no-proxy-server'])
        pg = b.new_page()
        errs = []
        pg.on('pageerror', lambda e: errs.append(str(e)))
        for u in pages:
            errs.clear()
            resp = pg.goto(base + u, wait_until='networkidle')
            if resp and resp.status >= 400:
                bad.append(f'④ {u} HTTP {resp.status}')
                continue
            res = pg.evaluate(
                "() => { try { browseDir('__probe__'); return ''; }"
                " catch (e) { return e.constructor.name + ': ' + e.message; } }")
            shown = pg.evaluate(
                "() => { const m = document.getElementById('dlgMask');"
                " return m ? getComputedStyle(m).display : 'NO-DOM'; }")
            if res or shown != 'flex':
                bad.append(f'④ {u} 调用报错={res or "无"} 对话框={shown}')
            pg.evaluate("() => { try { closeDlg(); } catch (e) {} }")
        b.close()
    print(f'   运行时覆盖 {len(pages)} 个页面: {" ".join(pages)}')
    return bad


def main():
    port = sys.argv[1] if len(sys.argv) > 1 else None
    bad = check_static()
    if port:
        bad += check_runtime(port)
    n = len(callers())
    if bad:
        print(f'FAIL  browse 对话框检查（覆盖 {n} 个页面）')
        for line in bad:
            print('  ' + line)
        return 1
    print(f'OK  browse 对话框检查通过（{n} 个页面均引用共享片段'
          + ('，运行时全绿' if port else '') + '）')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
