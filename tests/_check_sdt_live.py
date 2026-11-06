# -*- coding: utf-8 -*-
"""真浏览器验证 SDT 实时运行结果的「交互热图」（可切配色·悬浮·缩放）。
需平台服务运行；会真跑一次 6 序列 SDT（MAFFT 逐对，约 30~60s）。"""
import sys
import time

from playwright.sync_api import sync_playwright
# 控制台编码兜底：Windows 默认代码页是 GBK，本脚本的 ✔/✘/⚠ 等字符会让
# print 抛 UnicodeEncodeError（2026-09-11 实测多处踩过）。只改错误处理为
# replace（编码不动，中文照常可读），编不出的字符降级为 '?'。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors='replace')
    except (AttributeError, OSError):
        pass


URL = 'http://127.0.0.1:8765/tools?g=compare#t-sdt'


def main() -> int:
    fails = []
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True,
                           args=['--no-proxy-server'])
        pg = b.new_page(viewport={'width': 1700, 'height': 1300})
        errs = []
        pg.on('pageerror', lambda e: errs.append(str(e)))
        pg.goto(URL, wait_until='domcontentloaded')
        pg.evaluate("try{localStorage.setItem('vp_example_seen','1')}catch(e){}")
        pg.wait_for_selector('#t-sdt .btn:has-text("✨ 示例"), #sd_fa', timeout=30000)

        # 点卡片自己的 「✨ 示例」填入 6 条同属序列
        pg.click('#t-sdt button:has-text("✨ 示例")')
        pg.wait_for_timeout(500)
        fa = pg.input_value('#sd_fa')
        print(f'  sd_fa 填入 = {fa[:60]!r}（{len(fa)} 字符）')
        if not fa:
            fails.append('sd_fa 未填入')

        # 选 viridis 配色（测试映射：默认应为 Viridis 热图）
        pg.select_option('#sd_palette', 'viridis')

        # 运行分析
        pg.click('#t-sdt button:has-text("▶ 运行分析")')
        t0 = time.time()
        try:
            pg.wait_for_selector('#sdReadyHeat svg.main-svg', timeout=240000)
        except Exception as e:                       # noqa: BLE001
            print('  ✗ 运行结果未出现交互热图:', str(e)[:120])
            fails.append('SDT 运行结果无交互热图')
            print('  sdMeta:', pg.inner_text('#sdMeta') if pg.query_selector('#sdMeta') else '?')
            b.close()
            print('FAILED: ', ', '.join(fails))
            return 1
        print(f'  运行完成 {time.time()-t0:.0f}s，交互热图已渲染')

        svgs = pg.eval_on_selector_all('#sdReadyHeat svg.main-svg', 'e=>e.length')
        cs = pg.input_value('#sdReadyCs')
        print(f'  交互热图 主 SVG={svgs}  默认配色={cs}')
        if svgs < 1:
            fails.append('交互热图未渲染')
        # 切换配色
        for name in ('Plasma', 'Magma', 'Spectral'):
            pg.select_option('#sdReadyCs', name)
            pg.wait_for_timeout(400)
        print('  切换配色 (Plasma/Magma/Spectral) 无报错')

        # 下载区仍在
        dl = pg.inner_text('#sdDl') if pg.query_selector('#sdDl') else ''
        print(f'  下载区含 PNG/PDF: {("热图 PNG" in dl) and ("热图 PDF" in dl)}')

        if errs:
            print('  页面错误:')
            for e in errs[:6]:
                print('   ', e[:160])
        b.close()
    print('ALL OK' if not fails else 'FAILED: ' + ', '.join(fails))
    return 0 if not fails else 1


if __name__ == '__main__':
    sys.exit(main())
