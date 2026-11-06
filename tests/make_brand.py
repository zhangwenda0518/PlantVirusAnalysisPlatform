# -*- coding: utf-8 -*-
"""从 webapp/static/brand/*.svg 生成所有位图产物（徽标 / favicon）。

用 Playwright(Chromium) 而不是 cairosvg：本机通常没装 cairosvg，且 Chromium
与打包后的 WebView2 同源渲染，所见即最终效果。

用法:
    python tests/make_brand.py

产物（全部落在 webapp/static/brand/）:
    logo-512/192/96.png    徽标
    favicon.ico            多尺寸（浏览器标签页 / exe 图标）

启动页封面现在是文字封面（见 webapp/templates/home.html），不再有主视觉位图，
因此本脚本只服务徽标。若日后重新加回插图，按同样的 HERO_W/HERO_H 思路补一段
栅格化即可。

两个已踩过的坑，改动本文件时注意:
  1. XML 注释里出现连续两个连字符（比如把 CSS 变量全名写进注释）会让 SVG
     解析失败，Chromium 只返回一张"XML 解析错误"页 —— 所以先做 XML 校验，
     把这种错误挡在栅格化之前，而不是产出一张错误页当成果。
  2. 在 about:blank 页面里用 <img src="file://..."> 加载本地 SVG 会被拦成裂图；
     这里改成把 SVG 文本内联进 HTML。
"""
import os
import sys
import xml.dom.minidom as minidom
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
BRAND = ROOT / 'webapp' / 'static' / 'brand'
LOGO = BRAND / 'logo-mark.svg'

def check_xml(p):
    """XML 合法性自检：非法直接抛，避免渲染出错误页当成果。"""
    minidom.parse(str(p))
    print(f'  XML 校验通过: {p.name}')


def _shot(pg, path, out, w, h, transparent=False):
    pg.set_viewport_size({'width': w, 'height': h})
    pg.goto(path.as_uri(), wait_until='load')
    pg.wait_for_timeout(250)
    pg.screenshot(path=str(out), omit_background=transparent)
    a = Image.open(out)
    print(f'  {out.name:24s} {a.size[0]}x{a.size[1]}  '
          f'{os.path.getsize(out) / 1024:7.1f} KB')


def main() -> int:
    from playwright.sync_api import sync_playwright   # 延迟导入：仅本脚本需要

    print('① XML 校验:')
    check_xml(LOGO)

    print('\n② 栅格化:')
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True,
                           args=['--no-proxy-server'])
        pg = b.new_page(device_scale_factor=2)
        _shot(pg, LOGO, BRAND / 'logo-512.png', 256, 256, transparent=True)
        b.close()

    big = Image.open(BRAND / 'logo-512.png').convert('RGBA')
    big.resize((192, 192), Image.LANCZOS).save(BRAND / 'logo-192.png')
    big.resize((96, 96), Image.LANCZOS).save(BRAND / 'logo-96.png')
    big.save(BRAND / 'favicon.ico',
             sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128)])
    print(f'  {"logo-192/96.png + favicon.ico":24s} 由 logo-512 降采样')

    print('\n③ 产物:')
    for f in sorted(BRAND.iterdir()):
        print(f'  {f.name:24s} {os.path.getsize(f) / 1024:8.1f} KB')
    return 0


if __name__ == '__main__':
    sys.exit(main())
