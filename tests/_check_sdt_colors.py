# -*- coding: utf-8 -*-
"""验证 SDT 交互热图「切换配色」是否真的重绘（读 Plotly 数据 + SVG 实际颜色）。"""
import sys

from playwright.sync_api import sync_playwright

URL = 'http://127.0.0.1:8765/tools?g=compare#t-sdt'


def snap(pg):
    """取当前热图：Plotly heatmap 是内嵌 <image> 的 base64 PNG（小矩阵），
    直接哈希这张位图 + 读 colorscale，判断配色是否真的换了。"""
    return pg.evaluate("""() => {
      const box = document.getElementById('sdReadyHeat');
      const img = box.querySelector('image');
      const href = img ? (img.getAttribute('href') || img.getAttribute('xlink:href') || '') : '';
      let h = 0;
      for (let i = 0; i < href.length; i += 17) h = (h * 31 + href.charCodeAt(i)) >>> 0;
      let cs = null;
      try { cs = JSON.stringify(box._fullData[0].colorscale).slice(0, 46); } catch(e) {}
      return {imgLen: href.length, imgHash: String(h), cs: cs};
    }""")


def main() -> int:
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True,
                           args=['--no-proxy-server'])
        pg = b.new_page(viewport={'width': 1700, 'height': 1200})
        pg.goto(URL, wait_until='domcontentloaded')
        pg.evaluate("try{localStorage.setItem('vp_example_seen','1')}catch(e){}")
        pg.reload(wait_until='domcontentloaded')
        pg.wait_for_selector('#sdReadyHeat svg.main-svg', timeout=60000)
        pg.wait_for_timeout(800)

        opts = pg.eval_on_selector_all('#sdReadyCs option', 'e=>e.map(o=>o.value)')
        print(f'  可选配色（{len(opts)}）:', opts)
        results = {}
        for name in opts:
            pg.select_option('#sdReadyCs', name)
            pg.wait_for_timeout(600)
            s = snap(pg)
            results[name] = s
            print(f'  {name:10s} 位图 {s["imgLen"]:5d} B  哈希={s["imgHash"]:>12s}  '
                  f'cs={s["cs"][:40]}')
        uniq = {v['imgHash'] for v in results.values()}
        dup = {}
        for k, v in results.items():
            dup.setdefault(v['imgHash'], []).append(k)
        same = [v for v in dup.values() if len(v) > 1]
        print(f'  不同配色的热图位图哈希数: {len(uniq)} / {len(results)}'
              + (f'  重复组: {same}' if same else ''))
        b.close()
        return 0 if len(uniq) == len(results) else 1


if __name__ == '__main__':
    sys.exit(main())
