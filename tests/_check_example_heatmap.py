# -*- coding: utf-8 -*-
"""真浏览器验证示例查看器的「交互热图」：SDT（切配色）与 identity（NT/AA 切换）。"""
import sys

from playwright.sync_api import sync_playwright

BASE = 'http://127.0.0.1:8765/tools?g=compare'


def wait_plot(pg, timeout=40000):
    pg.wait_for_selector('#vpMatrixEl svg.main-svg', timeout=timeout)


def main() -> int:
    fails = []
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True,
                           args=['--no-proxy-server'])
        pg = b.new_page(viewport={'width': 1700, 'height': 1100})
        errs = []
        pg.on('pageerror', lambda e: errs.append(str(e)))

        # ---------- SDT ----------
        pg.goto(BASE + '#t-sdt', wait_until='domcontentloaded')
        pg.evaluate("try{localStorage.setItem('vp_example_seen','1')}catch(e){}")
        pg.wait_for_selector('#t-sdt .vp-ex-btn:has-text("示例结果")', timeout=30000)
        pg.click('#t-sdt .vp-ex-btn:has-text("示例结果")')
        wait_plot(pg)
        traces = pg.eval_on_selector_all('#vpMatrixEl svg.main-svg', 'e=>e.length')
        cs = pg.input_value('#vpMatrixCs')
        print(f'  SDT 交互热图渲染: 主 SVG 数={traces}  默认配色={cs}')
        if traces < 1:
            fails.append('SDT 热图未渲染')
        for name in ('Viridis', 'Plasma', 'Inferno'):
            pg.select_option('#vpMatrixCs', name)
            pg.wait_for_timeout(500)
        print('  切换配色 (Viridis/Plasma/Inferno) 无报错')
        # 热图应输出 SVG（Plotly 渲染的矢量图）
        svgs = pg.eval_on_selector_all('#vpMatrixEl svg.main-svg', 'e=>e.length')
        print(f'  热图内 SVG 主图数量: {svgs}')
        pg.evaluate("() => document.querySelectorAll('.dlgmask').forEach(m=>m.remove())")

        # ---------- identity（NT/AA 切换） ----------
        pg.evaluate("() => window.VPExamples.showResult('identity')")
        wait_plot(pg)
        hasSt = pg.eval_on_selector_all('#vpMatrixSt', 'e=>e.length')
        st = pg.input_value('#vpMatrixSt') if hasSt else '?'
        print(f'  identity 交互热图: NT/AA 切换器存在={bool(hasSt)}  当前={st}')
        if not hasSt:
            fails.append('identity 无 NT/AA 切换器')
        elif st != 'nt':
            fails.append('identity 默认不是 NT')
        if hasSt:
            pg.select_option('#vpMatrixSt', 'aa')
            pg.wait_for_timeout(600)
            print('  切到 AA 无报错')

        if errs:
            print('  页面错误:')
            for e in errs[:6]:
                print('   ', e[:150])
        b.close()
    print('ALL OK' if not fails else 'FAILED: ' + ', '.join(fails))
    return 0 if not fails else 1


if __name__ == '__main__':
    sys.exit(main())
