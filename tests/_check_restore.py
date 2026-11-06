# -*- coding: utf-8 -*-
"""真浏览器验证「全局初始化 / 上次运行结果恢复」：

1) 直接打开 SDT 卡（磁盘上已有历史运行）→ 结果自动恢复（交互热图 + 矩阵表
   + 下载），无需重跑；
2) 跳到别的页面再回来 → 结果仍在；
3) 通用卡（如 contigs / identify）→ 输出区出现「上次运行 · <run>」+ 产物下载。

用法：python tests/_check_restore.py
"""
import sys

from playwright.sync_api import sync_playwright
# 控制台编码兜底：Windows 默认代码页是 GBK，本脚本的 ✔/✘/⚠ 等字符会让
# print 抛 UnicodeEncodeError（2026-09-11 实测多处踩过）。只改错误处理为
# replace（编码不动，中文照常可读），编不出的字符降级为 '?'。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors='replace')
    except (AttributeError, OSError):
        pass


TOOLS = 'http://127.0.0.1:8765/tools?g=compare#t-sdt'
VIRUS = 'http://127.0.0.1:8765/tools?g=virus#t-contigs'
OTHER = 'http://127.0.0.1:8765/samples'


def main() -> int:
    fails = []
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True,
                           args=['--no-proxy-server'])
        pg = b.new_page(viewport={'width': 1700, 'height': 1200})
        errs = []
        pg.on('pageerror', lambda e: errs.append(str(e)))

        # ---- 1) SDT 富结果自动恢复 ----
        pg.goto(TOOLS, wait_until='domcontentloaded')
        pg.evaluate("try{localStorage.setItem('vp_example_seen','1')}catch(e){}")
        pg.reload(wait_until='domcontentloaded')
        try:
            pg.wait_for_selector('#sdReadyHeat svg.main-svg', timeout=60000)
            print('  [1] SDT 结果自动恢复：交互热图已渲染')
        except Exception as e:                       # noqa: BLE001
            print(f'  [1] ✗ SDT 未自动恢复: {str(e)[:100]}')
            fails.append('SDT 未自动恢复')
        tbl = pg.eval_on_selector_all('#sdTable tr', 'e=>e.length')
        dl = pg.inner_text('#sdDl') if pg.query_selector('#sdDl') else ''
        meta = pg.inner_text('#sdMeta') if pg.query_selector('#sdMeta') else ''
        print(f'  [1] 矩阵表行数={tbl} · 下载区含热图 PNG={"热图 PNG" in dl} · meta={meta[:60]}')
        if tbl < 2:
            fails.append('SDT 矩阵表未恢复')
        if '热图 PNG' not in dl:
            fails.append('SDT 下载区未恢复')

        # ---- 2) 跳走再回来 ----
        pg.goto(OTHER, wait_until='domcontentloaded')
        pg.wait_for_timeout(800)
        pg.goto(TOOLS, wait_until='domcontentloaded')
        try:
            pg.wait_for_selector('#sdReadyHeat svg.main-svg', timeout=60000)
            print('  [2] 跳转 /samples 再回来 → 结果仍在')
        except Exception as e:                       # noqa: BLE001
            print(f'  [2] ✗ 回来后结果丢失: {str(e)[:100]}')
            fails.append('跳转后结果丢失')

        # ---- 3) 通用卡恢复 ----
        pg.goto(VIRUS, wait_until='domcontentloaded')
        try:
            pg.wait_for_function(
                "() => { const b = document.getElementById('toolrun-contigs');"
                " return b && /上次运行/.test(b.textContent); }", timeout=60000)
            txt = pg.inner_text('#toolrun-contigs')
            first = [l for l in txt.splitlines() if l.strip()][:2]
            print(f'  [3] 通用卡恢复：{first}')
        except Exception as e:                       # noqa: BLE001
            print(f'  [3] ✗ 通用卡未恢复: {str(e)[:100]}')
            fails.append('通用卡未恢复')

        if errs:
            print('  页面错误:')
            for e in errs[:6]:
                print('   ', e[:150])
        b.close()
    print('ALL OK' if not fails else 'FAILED: ' + ', '.join(fails))
    return 0 if not fails else 1


if __name__ == '__main__':
    sys.exit(main())
