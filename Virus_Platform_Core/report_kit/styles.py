# -*- coding: utf-8 -*-
"""版式：学术论文风的 CSS（白底、衬线标题、克制墨绿/墨蓝）。

为什么把样式单独成文件：报告要「像论文」，而不是「像后台面板」。
配色只有三档：
  - 墨绿  #17513c  主色（小节标尺、结论卡左边条、表头底线）
  - 墨蓝  #23415c  次色（链接、章节编号、图注）
  - 暖灰  #f7f6f3  纸面（区块底色），正文纯白
所有样式内联在 HTML 里，双击即可离线打开，不依赖任何 CDN。
"""
from __future__ import annotations

ACCENT = '#17513c'
ACCENT_2 = '#23415c'
WARN = '#9a5b00'
BAD = '#8f2d2d'
OK = '#1d6b46'
PAPER = '#f7f6f3'
INK = '#1b1d1c'
INK_SOFT = '#5b625e'

CSS = f"""
:root {{
  --ink: {INK};
  --ink-soft: {INK_SOFT};
  --paper: {PAPER};
  --line: #e2dfd8;
  --line-strong: #c9c4b9;
  --accent: {ACCENT};
  --accent-2: {ACCENT_2};
  --warn: {WARN};
  --bad: {BAD};
  --ok: {OK};
  --serif: "Iowan Old Style", "Palatino Linotype", "Book Antiqua",
           "Songti SC", "STSong", "SimSun", Georgia, serif;
  --sans: "Inter", "Segoe UI", "Microsoft YaHei", "PingFang SC",
          "Hiragino Sans GB", "Source Han Sans SC", sans-serif;
  --mono: "Cascadia Mono", "JetBrains Mono", Consolas, "Courier New", monospace;
}}

/* 版式全用本地字体栈，不引任何 webfont / CDN：报告必须能离线双击打开，
   且在国内网络下不能因为外部字体加载失败而排版走样。 */

* {{ box-sizing: border-box; }}

html {{ background: #fff; }}
body {{
  margin: 0;
  color: var(--ink);
  background: #fff;
  font: 15.5px/1.78 var(--sans);
  -webkit-font-smoothing: antialiased;
  text-rendering: optimizeLegibility;
}}

a {{ color: var(--accent-2); text-decoration: none; }}
a:hover {{ text-decoration: underline; }}

/* ---------- 页面骨架 ---------- */
.page {{ display: grid; grid-template-columns: 268px minmax(0, 1fr); }}
.toc {{
  position: sticky; top: 0; align-self: start;
  height: 100vh; overflow-y: auto;
  padding: 30px 22px 40px;
  background: #fcfbf9;
  border-right: 1px solid var(--line);
  font-size: 13.5px;
}}
.toc h2 {{
  margin: 0 0 14px; font: 600 12px/1.4 var(--sans);
  letter-spacing: .14em; text-transform: uppercase; color: var(--ink-soft);
}}
.toc ol {{ margin: 0; padding-left: 0; list-style: none; }}
.toc li {{ margin: 2px 0; }}
.toc a {{
  display: block; padding: 4px 8px; border-radius: 4px;
  color: #333; border-left: 2px solid transparent;
}}
.toc a:hover {{ background: #f2f0ea; text-decoration: none; }}
.toc a.is-active {{
  border-left-color: var(--accent); background: #f0f4f1;
  color: var(--accent); font-weight: 600;
}}
.toc .sub {{ padding-left: 18px; font-size: 12.5px; color: #555; }}
.toc .brand {{
  margin: 26px 0 0; padding-top: 16px; border-top: 1px solid var(--line);
  font-size: 12px; color: var(--ink-soft); line-height: 1.7;
}}

/* 打印/导出 PDF：目录与交互件不占纸面，正文通栏；每节另起一页更接近稿件 */
@media print {{
  .toc, .copy, .to-top {{ display: none !important; }}
  .page {{ display: block; }}
  main {{ max-width: none; padding: 0; }}
  body {{ font-size: 11.5pt; }}
  section {{ margin-bottom: 26px; }}
  h2.sec {{ page-break-after: avoid; }}
  table, figure, .kpi-card, .qa {{ page-break-inside: avoid; }}
  details > * {{ display: block !important; }}
  a {{ color: var(--ink); text-decoration: none; }}
}}

main {{ padding: 52px 64px 96px; max-width: 1080px; }}

/* ---------- 封面 ---------- */
.cover {{ border-bottom: 3px double var(--line-strong); padding-bottom: 26px; margin-bottom: 34px; }}
.cover .kicker {{
  font: 600 12px/1 var(--sans); letter-spacing: .16em;
  text-transform: uppercase; color: var(--accent);
}}
.cover h1 {{
  margin: 12px 0 10px; font: 600 34px/1.28 var(--serif);
  letter-spacing: -.01em;
}}
.cover .subtitle {{ margin: 0 0 20px; color: var(--ink-soft); font-size: 16px; }}
.cover .meta {{
  display: flex; flex-wrap: wrap; gap: 8px 26px;
  font-size: 13px; color: var(--ink-soft);
}}
.cover .meta b {{ color: var(--ink); font-weight: 600; }}

/* ---------- 章节 ---------- */
section {{ margin: 0 0 54px; scroll-margin-top: 20px; }}
h2.sec {{
  display: flex; align-items: baseline; gap: 12px;
  margin: 0 0 18px; padding-bottom: 10px;
  border-bottom: 1px solid var(--line-strong);
  font: 600 23px/1.35 var(--serif); color: var(--ink);
}}
h2.sec .no {{
  font: 600 13px/1 var(--sans); letter-spacing: .08em;
  color: var(--accent); background: #eef3ef; border-radius: 3px;
  padding: 5px 8px; transform: translateY(-3px);
}}
h3 {{
  margin: 30px 0 10px; font: 600 17.5px/1.5 var(--serif); color: var(--accent-2);
}}
h4 {{ margin: 20px 0 8px; font: 600 15px/1.5 var(--sans); color: #2a2f2c; }}
p {{ margin: 0 0 12px; }}
.lede {{ font-size: 16.5px; line-height: 1.8; color: #2c312e; }}
.muted {{ color: var(--ink-soft); }}
.small {{ font-size: 13px; }}
hr.soft {{ border: 0; border-top: 1px solid var(--line); margin: 30px 0; }}

/* ---------- 摘要 / 结论 ---------- */
.abstract {{
  margin: 26px 0 40px; padding: 22px 26px;
  background: var(--paper); border-left: 3px solid var(--accent);
  border-radius: 0 4px 4px 0;
}}
.abstract h2 {{ margin: 0 0 10px; font: 600 13px/1 var(--sans);
  letter-spacing: .14em; text-transform: uppercase; color: var(--accent); }}
.abstract p {{ margin: 0 0 10px; }}
.abstract p:last-child {{ margin-bottom: 0; }}

ul.findings {{ list-style: none; margin: 0; padding: 0; }}
ul.findings > li {{
  position: relative; margin: 0 0 14px; padding: 14px 18px 12px 20px;
  background: #fff; border: 1px solid var(--line);
  border-left: 3px solid var(--ok); border-radius: 0 4px 4px 0;
}}
ul.findings > li.lv-warn {{ border-left-color: var(--warn); }}
ul.findings > li.lv-bad  {{ border-left-color: var(--bad); }}
ul.findings > li .ev {{
  display: block; margin-top: 8px; font: 12px/1.6 var(--mono);
  color: var(--ink-soft); word-break: break-all;
}}
ul.findings > li .ev span {{ background: #f3f1ec; padding: 1px 5px; border-radius: 3px; }}

/* ---------- 卡片 ---------- */
.card {{
  margin: 18px 0; padding: 20px 24px;
  background: var(--paper); border: 1px solid var(--line); border-radius: 5px;
}}
.card.white {{ background: #fff; }}
.card h4:first-child, .card h3:first-child {{ margin-top: 0; }}
.card .tag {{
  display: inline-block; margin-right: 8px; padding: 1px 7px;
  font: 600 11.5px/1.7 var(--sans); letter-spacing: .04em;
  border-radius: 3px; background: #e7eee9; color: var(--accent);
  vertical-align: 2px;
}}
.card .tag.gray {{ background: #ecebe6; color: #555; }}
.card .tag.warn {{ background: #f6ecdc; color: var(--warn); }}
.card .tag.bad  {{ background: #f5e3e3; color: var(--bad); }}

/* ---------- KPI ---------- */
.kpis {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(158px, 1fr));
  gap: 12px; margin: 18px 0 6px; }}
.kpi {{ padding: 14px 16px; background: #fff; border: 1px solid var(--line);
  border-radius: 5px; }}
.kpi .v {{ font: 600 25px/1.2 var(--serif); color: var(--accent); }}
.kpi .l {{ font-size: 12.5px; color: var(--ink-soft); margin-top: 2px; }}
.kpi .n {{ font-size: 11.5px; color: #8a8f8b; margin-top: 3px; }}

/* ---------- 表格 ---------- */
.tbl-wrap {{ overflow-x: auto; margin: 14px 0 6px; }}
table {{ border-collapse: collapse; width: 100%; font-size: 13.5px; }}
caption {{ caption-side: top; text-align: left; padding: 0 0 8px;
  font: 600 12.5px/1.5 var(--sans); color: var(--ink-soft); }}
th, td {{ padding: 7px 10px; border-bottom: 1px solid var(--line); text-align: left;
  vertical-align: top; }}
thead th {{ border-bottom: 2px solid var(--line-strong);
  font: 600 12.5px/1.4 var(--sans); color: #2c312e; background: #fff;
  position: sticky; top: 0; }}
tbody tr:nth-child(even) {{ background: #fbfaf7; }}
td.num, th.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
td.mono {{ font-family: var(--mono); font-size: 12.5px; }}
tr.hl td {{ background: #eef4ef !important; font-weight: 600; }}

/* ---------- 图 ---------- */
figure {{ margin: 22px 0; text-align: center; }}
figure img {{ max-width: 100%; height: auto; border: 1px solid var(--line);
  border-radius: 4px; background: #fff; }}
figcaption {{ margin-top: 8px; font-size: 12.5px; color: var(--ink-soft);
  text-align: left; }}
.fig-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }}
@media (max-width: 900px) {{ .fig-grid {{ grid-template-columns: 1fr; }} }}
/* 内联 SVG 图（figure() 的 .svg 分支）：自适应宽度、不撑破版心 */
.fig-svg svg {{ max-width: 100%; height: auto; background: #fff;
  border: 1px solid var(--line); border-radius: 4px; }}

/* ---------- 代码 ---------- */
pre {{ margin: 12px 0; padding: 14px 16px; overflow-x: auto;
  background: #1f2723; color: #e8efe9; border-radius: 5px;
  font: 12.5px/1.7 var(--mono); }}
pre .cmt {{ color: #8fa89a; }}
code {{ font-family: var(--mono); font-size: 13px;
  background: #f1efea; padding: 1px 5px; border-radius: 3px; }}
pre code {{ background: none; padding: 0; color: inherit; }}

/* ---------- 步骤清单 ---------- */
.steps {{ margin: 6px 0 0; padding-left: 24px; }}
.steps > li {{ margin: 0 0 14px; padding-left: 4px; }}
.steps > li > b {{ display: block; margin-bottom: 2px; }}

/* ---------- 折叠 ---------- */
details {{ margin: 12px 0; border: 1px solid var(--line); border-radius: 5px;
  background: #fff; }}
details > summary {{ cursor: pointer; padding: 10px 16px; font-size: 13.5px;
  font-weight: 600; color: var(--accent-2); list-style: none; }}
details > summary::-webkit-details-marker {{ display: none; }}
details > summary::before {{ content: "▸ "; color: var(--accent); }}
details[open] > summary::before {{ content: "▾ "; }}
details > .body {{ padding: 2px 16px 14px; }}

/* ---------- 问答卡 ---------- */
.qa {{ margin: 16px 0; border: 1px solid var(--line); border-radius: 5px;
  background: #fff; overflow: hidden; }}
.qa .q {{ padding: 13px 18px; background: var(--paper);
  border-bottom: 1px solid var(--line);
  font: 600 15px/1.6 var(--sans); }}
.qa .q .n {{ color: var(--accent); margin-right: 8px; }}
.qa .a {{ padding: 14px 18px; }}
.qa .a .lead {{ font-size: 13px; color: var(--ink-soft); margin-bottom: 6px; }}
.qa .prompt {{ margin-top: 10px; padding: 10px 12px; background: #f4f6f4;
  border-radius: 4px; font: 13px/1.7 var(--sans); color: #2b3a31;
  border-left: 2px solid var(--accent); }}

/* ---------- 提示条 ---------- */
.note {{ padding: 10px 14px; margin: 12px 0; border-radius: 4px;
  font-size: 13.5px; background: #f4f6f4; border-left: 3px solid var(--accent-2); }}
.note.warn {{ background: #fbf5ea; border-left-color: var(--warn); }}
.note.bad  {{ background: #f9eeee; border-left-color: var(--bad); }}

/* ---------- 页脚 ---------- */
footer {{ margin-top: 60px; padding-top: 18px; border-top: 3px double var(--line-strong);
  font-size: 12.5px; color: var(--ink-soft); }}

/* ---------- 打印 ---------- */
@media print {{
  .page {{ display: block; }}
  .toc {{ display: none; }}
  main {{ padding: 0; max-width: none; }}
  section {{ break-inside: auto; }}
  h2.sec {{ break-after: avoid; }}
  figure, table, .card {{ break-inside: avoid; }}
  pre {{ background: #f4f4f4; color: #111; }}
}}
@media (max-width: 1180px) {{
  .page {{ grid-template-columns: 1fr; }}
  .toc {{ position: static; height: auto; border-right: 0;
    border-bottom: 1px solid var(--line); }}
  main {{ padding: 32px 24px 64px; }}
}}
"""

# 小屏（手机、窄窗口）：正文单栏、目录收到顶部，避免出现横向滚动条。
# 放在 JS 之后单独一段，便于以后调阈值而不动上面的大段版式。
CSS_MOBILE = """
@media (max-width: 860px) {
  main { padding: 22px 16px 56px; }
  .cover h1 { font-size: 25px; line-height: 1.35; }
  .cover .meta { gap: 6px 16px; font-size: 12.5px; }
  h2.sec { font-size: 19px; gap: 8px; }
  h2.sec .no { transform: none; padding: 3px 6px; font-size: 11.5px; }
  h3 { font-size: 16px; margin-top: 22px; }
  body { font-size: 15px; }
  table { display: block; overflow-x: auto; white-space: nowrap; }
  .kpi { grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); }
  pre { font-size: 12px; }
}
@media print {
  details > * { display: block !important; }
}
"""

JS = """
(function () {
  var links = Array.prototype.slice.call(document.querySelectorAll('.toc a[href^="#"]'));
  var heads = links.map(function (a) {
    var el = document.getElementById(a.getAttribute('href').slice(1));
    return el;
  });
  function onScroll() {
    var y = window.scrollY + 90, idx = 0;
    for (var i = 0; i < heads.length; i++) {
      if (heads[i] && heads[i].offsetTop <= y) idx = i;
    }
    links.forEach(function (a, i) { a.classList.toggle('is-active', i === idx); });
  }
  window.addEventListener('scroll', onScroll, { passive: true });
  onScroll();
  // 复制按钮：代码块一键复制
  document.querySelectorAll('pre[data-copy]').forEach(function (pre) {
    var b = document.createElement('button');
    b.textContent = '复制';
    b.style.cssText = 'float:right;margin:-6px -6px 6px 8px;padding:2px 8px;'
      + 'font:11px/1.6 sans-serif;border:1px solid #4a5a52;border-radius:3px;'
      + 'background:#2b3833;color:#cfe0d6;cursor:pointer';
    b.onclick = function () {
      navigator.clipboard.writeText(pre.innerText.replace(/^复制\\n/, ''));
      b.textContent = '已复制';
      setTimeout(function () { b.textContent = '复制'; }, 1200);
    };
    pre.insertBefore(b, pre.firstChild);
  });
})();
"""
