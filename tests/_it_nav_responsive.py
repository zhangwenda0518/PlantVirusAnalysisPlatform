# -*- coding: utf-8 -*-
"""导航布局验证（真浏览器实测，不是看 CSS 猜）。

============================================================
2026-10-21 布局重构：顶部横条 → 左侧固定导航栏 + 顶部工具条
2026-10-28 功能栏位置可选：默认「上方」，可在设置页切「左侧」
============================================================
2026-10-21 那次把导航改成左侧竖排，本脚本 A/B 两组断言全部按**左侧形态**
写（侧栏宽 236、body 左内边距、≤1024px 抽屉）。2026-10-28 起默认形态变成
顶部横排，位置存 localStorage['vp_nav_pos']（top 默认 / side），所以：
  · A/B 两组仍在左侧形态下跑 —— 进页面前先把 localStorage 固定成 'side'；
  · 新增 C 组专门测顶部形态（横排、不占左内边距、下拉浮层、顶栏吸顶位置）。

左侧口径（对应 2026-10-21 改版要守住的东西）：
  A. 桌面档（>1024px）
     1. 侧栏常驻可见、宽度固定（--sidenav-w，默认 236px）
     2. 侧栏不遮挡主区：主区内容左边界 ≥ 侧栏右边界
     3. 页面无横向溢出
     4. 一级链接个数 = NAV_GROUPS - 1（result 组由底部直达入口承担）+ 1（总览）
     5. 侧栏自身可滚动、不产生页面级溢出（组多时靠内部滚动消化）
     6. 上下文胶囊 / 语言按钮 / 📂 目录入口都在顶栏且可见可点
  B. 窄屏档（≤820px）—— 2026-10-29 起抽屉只在 820 以下出现
     7. 侧栏默认收起（translateX 移出视口），☰ 可见
     8. 点 ☰ 后侧栏展开、遮罩出现；点遮罩关闭
     9. 主区恢复全宽（body 无左内边距）
    10. 页面无横向溢出
  B2. 中等档（821~1024px）—— 侧栏常驻但收窄到 204px，不覆盖内容
    11. 侧栏常驻可见、宽度 204
    12. 主区左内边距 = 204，内容左边界 ≥ 侧栏右边界

用法：python tests/_it_nav_responsive.py [--keep]
依赖 selenium + 本机 Edge/Chrome。
"""
import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding='utf-8', errors='replace')
    except (AttributeError, OSError):
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# 一级链接个数别写死：NAV_GROUPS 加减组会让它陈旧，而"数字对不上"这种红
# 是噪声红，会掩盖真的布局问题。口径 = 分组数（result 组走底部入口，要减 1）
# + 「总览」1 条。
from Virus_Platform_Core.web.pages import NAV_GROUPS as _NAV_GROUPS  # noqa: E402

EXPECT_LINKS = len([g for g in _NAV_GROUPS if g['id'] != 'result']) + 1

DESKTOP_WIDTHS = [1920, 1600, 1440, 1280, 1100]
NARROW_WIDTHS = [820, 768, 600]
MID_WIDTHS = [1000, 900]
PAGES = ['/samples', '/tools?g=kvsuite', '/results', '/']

FAIL = []


def check(name, cond, extra=''):
    print(('  ✔ ' if cond else '  ✘ ') + name + (f'  {extra}' if extra else ''))
    if not cond:
        FAIL.append(name)


def free_port(start=8791):
    for p in range(start, start + 60):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            # connect 探测先行（2026-11-05）：Windows 上 0.0.0.0 已被别的
            # 进程绑定的端口，bind('127.0.0.1') 探测会误判空闲——起上去后
            # 请求被 wildcard 持有者截走，测试打到别人的服务上（教训：用户
            # 常驻的 docs http.server 恰占 8901+ 段，套件实例全 404）。
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as c:
                if c.connect_ex(('127.0.0.1', p)) == 0:
                    continue
            try:
                s.bind(('127.0.0.1', p))
                return p
            except OSError:
                continue
    raise RuntimeError('无可用端口')


def make_driver():
    from selenium import webdriver
    from selenium.webdriver.edge.options import Options as EdgeOptions
    o = EdgeOptions()
    o.add_argument('--headless=new')
    o.add_argument('--no-sandbox')
    o.add_argument('--window-size=1920,1000')
    try:
        return webdriver.Edge(options=o)
    except Exception:
        from selenium.webdriver.chrome.options import Options as ChromeOptions
        c = ChromeOptions()
        c.add_argument('--headless=new')
        c.add_argument('--window-size=1920,1000')
        return webdriver.Chrome(options=c)


MEASURE = """
const nav = document.querySelector('.sidenav');
if (!nav) return {error: 'no .sidenav'};
const nr = nav.getBoundingClientRect();
const page = document.querySelector('.page');
const pr = page ? page.getBoundingClientRect() : null;
const links = [...document.querySelectorAll(
    '.navlinks > a, .navlinks > .sn-group > a')]
  .filter(a => a.getClientRects().length);
const r = el => { if (!el || !el.getClientRects().length) return null;
                  const b = el.getBoundingClientRect();
                  return {w: Math.round(b.width), h: Math.round(b.height)}; };
const isNarrow = window.matchMedia('(max-width: 820px)').matches;
return {
  vw: window.innerWidth,
  narrow: isNarrow,
  navLeft: Math.round(nr.left),
  navRight: Math.round(nr.right),
  navW: Math.round(nr.width),
  navVisible: nav.getClientRects().length > 0,
  navOpenClass: nav.classList.contains('open'),
  navOverflowY: nav.scrollHeight - nav.clientHeight,
  pageLeft: pr ? Math.round(pr.left) : null,
  pageW: pr ? Math.round(pr.width) : null,
  bodyPadLeft: getComputedStyle(document.body).paddingLeft,
  overflowX: document.documentElement.scrollWidth - window.innerWidth,
  links: links.length,
  toggle: r(document.getElementById('snToggle')),
  chip: r(document.getElementById('navCtxSample')),
  reset: r(document.getElementById('navCtxReset')),
  lang: r(document.getElementById('langBtn')),
  // 2026-11-03：改抓 .navtail a.iconbtn（📂 打开平台目录）——首页按
  // 2026-11-02 定案刻意隐藏 AI 工作台按钮（button.iconbtn.nav-ai-btn），
  // 原先 querySelector 抓到的第一个 iconbtn 在 / 上恒为 None。
  icon: r(document.querySelector('.navtail a.iconbtn')),
};
"""

# 侧栏压叠探针（2026-10-27 实抓缺陷的回归守卫）
#
# 缺陷现象：9 个分组全部展开后，底部固定区（任务中心 / 结果中心 / 设置 /
# MCP 控制台 / 手册）与上面几个分组标题叠在一起、文字互相压字。
#
# 根因：`.navlinks.sn-links` 是 `flex:1 1 auto; min-height:0`，可被压到
# 低于内容高度（实测内容 2249px、只分到 619px）；而它的 overflow 是默认
# 的 visible —— 溢出的文字照原高度画出来，直接盖在排在后面的 .sn-foot
# 上。修法是给该列 `overflow-y:auto` 让它自己滚。
#
# 为什么旧断言没抓到：原来那条 `navOverflowY == 0 or navOverflowY > 0`
# 恒为真，等于没测。这里改成量真实几何：
#   · 每个一级行的**可见后代**并集是否超出自身盒子（被压扁）
#   · 相邻行的可见范围是否相交（压字）
#   · 底部固定区是否落在侧栏可视区内、且不被上面的内容侵入
# 裁剪规则：只有 .navlinks **真的在裁剪**（overflow-y != visible）时，
# 才按它的盒子裁掉滚出容器的那部分。overflow:visible 时内容照原高度
# 画到盒子外面 —— 那正是缺陷本体，无条件裁剪会把溢出的文字裁掉、
# 把压叠测成"正常"（本脚本第一版就踩了这个坑，变异测试才暴露出来）。
NAV_OVERLAP = """
const nav = document.getElementById('sideNav');
if (!nav) return {error: 'no #sideNav'};
const links = nav.querySelector('.navlinks');
const foot = nav.querySelector('.sn-foot');
if (!links || !foot) return {error: 'no .navlinks/.sn-foot'};
const rows = [...links.querySelectorAll('.sn-group, :scope > a.sn-link')];

function visualRect(el) {
  let top = Infinity, bottom = -Infinity;
  const push = r => { if (!r.height) return;
                      top = Math.min(top, r.top); bottom = Math.max(bottom, r.bottom); };
  push(el.getBoundingClientRect());
  el.querySelectorAll('*').forEach(c => {
    if (c.getClientRects().length) push(c.getBoundingClientRect());
  });
  return bottom > top ? {top, bottom} : null;
}
const clip = links.getBoundingClientRect();
const linksClips = getComputedStyle(links).overflowY !== 'visible';
function clipped(el) {
  const v = visualRect(el);
  if (!v) return null;
  if (!linksClips) return v;          // 不裁剪：溢出内容照画，必须计入
  const top = Math.max(v.top, clip.top), bottom = Math.min(v.bottom, clip.bottom);
  return bottom > top ? {top, bottom} : null;
}

const squeezed = [];
rows.forEach((el, i) => {
  const box = el.getBoundingClientRect(), vis = visualRect(el);
  if (vis && (vis.bottom - vis.top) - box.height > 1) {
    squeezed.push({i, name: el.dataset.group || el.textContent.trim().slice(0, 12),
                   boxH: Math.round(box.height),
                   needH: Math.round(vis.bottom - vis.top)});
  }
});
const overlaps = [];
for (let i = 1; i < rows.length; i++) {
  const a = clipped(rows[i - 1]), b = clipped(rows[i]);
  if (a && b && a.bottom - b.top > 1) {
    overlaps.push({prev: rows[i - 1].dataset.group
                          || rows[i - 1].textContent.trim().slice(0, 10),
                   next: rows[i].dataset.group
                          || rows[i].textContent.trim().slice(0, 10),
                   px: Math.round(a.bottom - b.top)});
  }
}
let lastVis = null;
for (let i = rows.length - 1; i >= 0 && !lastVis; i--) lastVis = clipped(rows[i]);
const fr = foot.getBoundingClientRect(), nr = nav.getBoundingClientRect();
// 行高被压扁（2026-10-29 缺陷）：.navlinks a 带 overflow:hidden，作为
// column flex 子项时 min-height:auto 退化成 0，内容超出容器就把「总览」
// 整条吸成 2px 残条。squeezed 测不出来（盒子与内容同高），必须单独测
// 行高下限：一行文字 + 上下内边距至少 24px。
const thin = rows
  .map((el, i) => ({i, h: Math.round(el.getBoundingClientRect().height),
                    name: el.dataset.group || el.textContent.trim().slice(0, 12)}))
  .filter(r => r.h < 24);
return {
  rows: rows.length,
  expanded: links.querySelectorAll('.sn-group.open').length,
  squeezedCount: squeezed.length, squeezed: squeezed.slice(0, 6),
  overlapCount: overlaps.length, overlaps: overlaps.slice(0, 6),
  thinCount: thin.length, thin: thin.slice(0, 6),
  minRowH: Math.min.apply(null, rows.map(el =>
      Math.round(el.getBoundingClientRect().height))),
  moreCue: links.classList.contains('sn-more'),
  linksScrollable: links.scrollHeight - links.clientHeight,
  linksOverflowY: getComputedStyle(links).overflowY,
  footInView: fr.top >= nr.top - 1 && fr.bottom <= nr.bottom + 1,
  // >0 表示最后一行可见内容压进了底部固定区
  footCollision: lastVis ? Math.round(lastVis.bottom - fr.top) : null,
  navScrollable: nav.scrollHeight - nav.clientHeight,
};
"""


def check_no_overlap(drv, W, page, tag):
    """全部展开后侧栏不得出现压扁/压叠，底部固定区必须完整可见。"""
    W("""
      document.querySelectorAll('.sn-group').forEach(g => {
        g.classList.add('open');
        const a = g.querySelector('a.sn-link');
        if (a) a.setAttribute('aria-expanded', 'true');
      });
      const l = document.querySelector('#sideNav .navlinks.sn-links');
      if (l) l.scrollTop = 0;
      document.getElementById('sideNav').scrollTop = 0;
      // .sn-more 渐隐提示由 resize / scroll 事件驱动，这里先触发一次再测
      window.dispatchEvent(new Event('resize'));
    """)
    time.sleep(0.35)
    m = W(NAV_OVERLAP)
    if not m or m.get('error'):
        check(f'{page} {tag} 侧栏展开后能测量', False, str(m))
        return
    det = (f"rows={m['rows']} open={m['expanded']} "
           f"linksScroll={m['linksScrollable']} "
           f"footCollision={m['footCollision']}")
    # 每一行都不许被压扁（盒子高度必须容得下自己的内容）
    check(f'{page} {tag} 全部展开后无侧栏行被压扁',
          m['squeezedCount'] == 0, f"{det} squeezed={m['squeezed']}")
    # 行高下限：column flex 子项被压缩时盒子与内容同高，squeezed 测不出来
    check(f'{page} {tag} 侧栏行高不被 flex 压缩',
          m['thinCount'] == 0, f"{det} minRowH={m['minRowH']} thin={m['thin']}")
    # 放不下时要有"下面还有"的渐隐提示（覆盖式滚动条静态不可见）
    check(f'{page} {tag} 内容放不下时有底部渐隐提示',
          m['linksScrollable'] <= 2 or m['moreCue'],
          f"{det} moreCue={m['moreCue']}")
    # 相邻行不许压字
    check(f'{page} {tag} 全部展开后相邻行不压叠',
          m['overlapCount'] == 0, f"{det} overlaps={m['overlaps']}")
    # 底部固定区完整落在侧栏可视区内，且最后一行可见内容在它上方
    check(f'{page} {tag} 全部展开后底部固定区完整可见',
          m['footInView'] and (m['footCollision'] is None
                               or m['footCollision'] <= 0), det)
    # 内容放不下时必须由 .navlinks 自己滚，而不是溢出到页面上
    check(f'{page} {tag} 放不下时由链接列内部滚动消化',
          m['linksScrollable'] <= 0 or m['linksOverflowY'] == 'auto',
          f"linksOverflowY={m['linksOverflowY']} {det}")
    # 侧栏自身不该再出现页面级溢出（滚动交给内层列）
    check(f'{page} {tag} 侧栏不产生整栏级溢出',
          m['navScrollable'] <= 1, det)


# 矮屏展开靠下分组：子项必须自动滚进可视区（2026-10-29 用户截图缺陷）。
# 展开由真实点击触发，测的是 app-nav.js 的 revealGroup()，不是 CSS。
REVEAL_MEASURE = """
const links = document.querySelector('#sideNav .navlinks.sn-links');
if (!links) return {error: 'no links'};
const lr = links.getBoundingClientRect();
const rows = [...links.children];
const hs = rows.map(e => Math.round(e.getBoundingClientRect().height));
// 选**最后一个**分组：它贴着列表底部，展开后组底必然越界，能真正走到
// revealGroup() 的滚动分支（中间的分组展开后子项本来就在可视区内）。
const groups = [...links.querySelectorAll('.sn-group')];
const g = groups.pop();
if (!g) return {error: 'no group'};
const before = Math.round(links.scrollTop);
g.querySelector('a.sn-link').click();
const subEl = g.querySelector('.sn-sub');
const sr = subEl.getBoundingClientRect();
const gr = g.getBoundingClientRect();
return {
  group: g.dataset.group,
  subItems: g.querySelectorAll('.sn-sub a').length,
  open: g.classList.contains('open'),
  rows: rows.length,
  firstH: hs[0], minH: Math.min.apply(null, hs),
  clientH: links.clientHeight,
  scrollTop: Math.round(links.scrollTop), scrollBefore: before,
  subRendered: subEl.getClientRects().length > 0,
  subInView: sr.top >= lr.top - 1 && sr.bottom <= lr.bottom + 1,
  headInView: gr.top >= lr.top - 1 && gr.top < lr.bottom - 1,
  scrollH: links.scrollHeight,
};
"""


def check_reveal(drv, W, page, tag):
    """展开列表最下面的分组：子项自动滚进可视区，首项不被压扁。"""
    W("""
      document.querySelectorAll('.sn-group.open').forEach(g => {
        g.classList.remove('open');
        const a = g.querySelector('a.sn-link');
        if (a) a.setAttribute('aria-expanded', 'false');
      });
      const l = document.querySelector('#sideNav .navlinks.sn-links');
      if (l) l.scrollTop = 0;
    """)
    time.sleep(0.3)
    m = W(REVEAL_MEASURE)
    if not m or m.get('error'):
        check(f'{page} {tag} 展开靠下分组能测量', False, str(m))
        return
    det = (f"group={m['group']} subItems={m['subItems']} "
           f"clientH={m['clientH']} scrollH={m['scrollH']} "
           f"scrollTop={m['scrollTop']} firstH={m['firstH']} minH={m['minH']}")
    check(f'{page} {tag} 展开靠下分组后子项自动进入可视区',
          m['open'] and m['subRendered'] and m['subInView'], det)
    check(f'{page} {tag} 自动滚动确实发生了（内容超出可视区时）',
          m['subInView'] and (m['scrollH'] <= m['clientH'] + 1
                              or m['scrollTop'] > 0), det)
    check(f'{page} {tag} 自动滚动后组标题仍在可视区', m['headInView'], det)
    check(f'{page} {tag} 自动滚动后首项没被压扁', m['minH'] >= 24, det)


# 顶部形态探针（2026-10-28 新增）。口径与左侧完全不同：不再是「侧栏宽
# 236 / 不遮挡主区 / 抽屉」，而是「横排、贴顶、不占左内边距、顶栏吸在
# 功能栏下方、下拉是浮层且不出视口」。
TOP_MEASURE = """
const nav = document.getElementById('sideNav');
if (!nav) return {error: 'no #sideNav'};
const nr = nav.getBoundingClientRect();
const tb = document.querySelector('.topbar');
const tbr = tb.getBoundingClientRect();
const links = [...document.querySelectorAll(
    '.navlinks > a, .navlinks > .sn-group > a')]
  .filter(a => a.getClientRects().length);
return {
  mode: document.documentElement.getAttribute('data-nav'),
  dir: getComputedStyle(nav).flexDirection,
  navTop: Math.round(nr.top), navLeft: Math.round(nr.left),
  navW: Math.round(nr.width), navH: Math.round(nr.height),
  navBottom: Math.round(nr.bottom),
  bodyPadLeft: getComputedStyle(document.body).paddingLeft,
  topbarTop: Math.round(tbr.top),
  navTopVar: getComputedStyle(document.documentElement)
               .getPropertyValue('--nav-top-h').trim(),
  overflowX: document.documentElement.scrollWidth - window.innerWidth,
  // 2026-11-03 诊断：nav 上方压了什么（elementsFromPoint + id/几何）
  atPoint: document.elementsFromPoint(60, 10).slice(0, 3).map(el => {
    const r = el.getBoundingClientRect();
    return el.tagName + '#' + (el.id || '') + '.' + (el.className || '')
      + '[' + Math.round(r.width) + 'x' + Math.round(r.height)
      + '@' + Math.round(r.top) + ']';
  }).join(' | '),
  navOffsetTop: nav.offsetTop,
  navParent: nav.offsetParent
    ? (nav.offsetParent.tagName + '.' + (nav.offsetParent.className || ''))
    : 'null',
  scrollY: Math.round(window.scrollY),
  links: links.length,
  vw: window.innerWidth,
};
"""


def check_top_mode(drv, W, base, page, w):
    """顶部形态：横排贴顶、不占左内边距、顶栏吸在它下面、下拉是浮层。"""
    drv.execute_cdp_cmd('Emulation.setDeviceMetricsOverride',
                        {'width': w, 'height': 900,
                         'deviceScaleFactor': 1, 'mobile': False})
    drv.get(base + page)   # 上一轮已把 localStorage 设成 top
    time.sleep(1.0)
    m = W(TOP_MEASURE)
    if not m or m.get('error'):
        check(f'{page} 顶部 {w}px 能测量', False, str(m))
        return
    det = (f"mode={m['mode']} dir={m['dir']} nav={m['navW']}x{m['navH']} "
           f"top={m['navTop']} left={m['navLeft']} "
           f"padL={m['bodyPadLeft']} topbarTop={m['topbarTop']} "
           f"var={m['navTopVar']} ovX={m['overflowX']} "
           f"at={m.get('atPoint')} offT={m.get('navOffsetTop')} "
           f"par={m.get('navParent')} scY={m.get('scrollY')}")
    check(f'{page} 顶部 {w}px 形态生效（data-nav=top）',
          m['mode'] == 'top', det)
    check(f'{page} 顶部 {w}px 横排（flex-direction: row）',
          m['dir'] == 'row', det)
    check(f'{page} 顶部 {w}px 贴在视口顶部且横向铺满',
          m['navTop'] == 0 and m['navLeft'] == 0 and m['navW'] >= w - 20, det)
    check(f'{page} 顶部 {w}px 不占左侧内边距（主区全宽）',
          m['bodyPadLeft'] in ('0px', '0'), det)
    # 高度是实测写进 --nav-top-h 的（英文标签更长会换行），顶栏必须正好贴住
    check(f'{page} 顶部 {w}px 顶栏吸在功能栏下方',
          abs(m['topbarTop'] - m['navBottom']) <= 1
          and abs(int(m['navTopVar'].replace('px', '') or 0) - m['navH']) <= 1,
          det)
    check(f'{page} 顶部 {w}px 页面无横向溢出',
          m['overflowX'] <= 1, det)
    check(f'{page} 顶部 {w}px {EXPECT_LINKS} 个一级链接全在',
          m['links'] == EXPECT_LINKS, f"{m['links']}（期望 {EXPECT_LINKS}）")

    # 下拉：必须是浮层（absolute），挂在标题下方，且不出视口
    W("""
      const g = document.querySelector('.sn-group[data-group="kvsuite"]')
             || document.querySelector('.sn-group');
      if (g) g.querySelector('a.sn-link').click();
    """)
    time.sleep(0.35)
    d = W("""
      const g = document.querySelector('.sn-group.open');
      if (!g) return {error: 'no open group'};
      const sub = g.querySelector('.sn-sub');
      const sr = sub.getBoundingClientRect(), gr = g.getBoundingClientRect();
      const nav = document.getElementById('sideNav').getBoundingClientRect();
      // 一级导航项（含 .sn-foot：结果中心/设置/MCP控制台/手册）是否被下拉压住
      const items = [...document.querySelectorAll(
          '.navlinks > a, .navlinks > .sn-group > a, .sn-foot a')]
        .filter(a => a.getClientRects().length
                     && !a.closest('.sn-sub')); 
      const covered = items.filter(a => {
        const b = a.getBoundingClientRect();
        return !(b.right <= sr.left || b.left >= sr.right ||
                 b.bottom <= sr.top || b.top >= sr.bottom);
      }).map(a => a.textContent.trim());
      return {pos: getComputedStyle(sub).position,
              vis: sub.getClientRects().length > 0,
              gap: Math.round(sr.top - gr.bottom),
              navGap: Math.round(sr.top - nav.bottom),
              covered: covered,
              left: Math.round(sr.left), right: Math.round(sr.right),
              vw: window.innerWidth,
              navH: Math.round(nav.height)};
    """)
    if d.get('error'):
        check(f'{page} 顶部 {w}px 点标题能展开下拉', False, str(d))
    else:
        check(f'{page} 顶部 {w}px 二级菜单是浮层（absolute）',
              d['pos'] == 'absolute' and d['vis'], str(d))
        # 2026-10-29：浮层改为挂在**整条功能栏**下方（折行两行时也不压第二行）
        check(f'{page} 顶部 {w}px 浮层挂在整条功能栏下方 6px 且不出视口',
              0 <= d['navGap'] <= 12 and d['left'] >= 0 and d['right'] <= d['vw'],
              str(d))
        # 浮层不该撑高功能栏（撑高说明退化成了文档流里的展开）
        check(f'{page} 顶部 {w}px 浮层不压一级导航项',
              d.get('covered') == [], str(d.get('covered')))
        check(f'{page} 顶部 {w}px 浮层不撑高功能栏',
              abs(d['navH'] - m['navH']) <= 1, str(d))

    # 互斥：再点开另一个分组，上一个必须收起。下拉菜单同时挂多层会互相压，
    # 2026-10-28 实测点开五个就并排叠在一起 —— 这里是那条缺陷的守卫。
    W("""
      const gs = [...document.querySelectorAll('.sn-group')];
      const other = gs.find(g => !g.classList.contains('open'));
      if (other) other.querySelector('a.sn-link').click();
    """)
    time.sleep(0.3)
    ex = W("""
      const open = [...document.querySelectorAll('.sn-group.open')];
      return {n: open.length, groups: open.map(g => g.dataset.group)};
    """)
    check(f'{page} 顶部 {w}px 下拉互斥（同时只开一个）',
          ex['n'] == 1, str(ex))

    # 点空白处收起
    W("document.body.click()")
    time.sleep(0.3)
    check(f'{page} 顶部 {w}px 点空白处收起下拉',
          W("return document.querySelectorAll('.sn-group.open').length") == 0)


def check_accordion(drv, W, page, tag):
    """分组**整行标题**点击折叠/展开必须真的生效。

    两条历史缺陷都在这里守着：
    1) 2026-10-21 实抓：CSS 里有一条 `.sn-group:hover > .sn-sub
       {display:block}`，鼠标停在分组上时 :hover 把刚点击折叠掉的菜单又
       强制显示出来 —— 表现就是"点箭头收不起来"。所以必须用**真实鼠标**
       移动（让 :hover 处于激活状态）再点击，只调 element.click() 测不出来。
    2) 2026-10-21 用户反馈：原先只有右侧小箭头能点，热区太小；改为整行
       标题即开关。本函数点的是 `a.sn-link`（整行），不是 .sn-caret ——
       若日后又改回只有箭头能点，这里会红。
    """
    from selenium.webdriver.common.action_chains import ActionChains
    from selenium.webdriver.common.by import By

    # 找一个当前展开的组；没有就随便点开一个
    gid = W("""
      const g = document.querySelector('.sn-group.open');
      if (g) return g.dataset.group;
      const first = document.querySelector('.sn-group');
      if (!first) return null;
      first.querySelector('a.sn-link').click();
      return first.dataset.group;
    """)
    if not gid:
        check(f'{page} {tag} 侧栏有可折叠分组', False, '未找到 .sn-group')
        return

    # 点整行标题（不是箭头）：热区必须是整行
    title = drv.find_element(
        By.CSS_SELECTOR, f'.sn-group[data-group="{gid}"] a.sn-link')
    ActionChains(drv).move_to_element(title).pause(0.2).click().perform()
    time.sleep(0.35)
    st = W(f"""
      const g = document.querySelector('.sn-group[data-group="{gid}"]');
      const sub = g.querySelector('.sn-sub');
      return {{open: g.classList.contains('open'),
               subVisible: sub.getClientRects().length > 0,
               hovering: g.matches(':hover'),
               url: location.pathname + location.search}};
    """)
    check(f'{page} {tag} 悬停时点击整行标题能收起二级菜单',
          (not st['open']) and (not st['subVisible']),
          f"group={gid} {st}")
    # 点标题只做开合，不应触发跳转（否则"再点一次"会跑到别的页）
    check(f'{page} {tag} 点标题不跳转（只做开合）',
          st['url'] == page, f"当前 {st['url']}，期望 {page}")

    # 再点一次应重新展开
    ActionChains(drv).move_to_element(title).pause(0.15).click().perform()
    time.sleep(0.35)
    st2 = W(f"""
      const g = document.querySelector('.sn-group[data-group="{gid}"]');
      const sub = g.querySelector('.sn-sub');
      return {{open: g.classList.contains('open'),
               subVisible: sub.getClientRects().length > 0}};
    """)
    check(f'{page} {tag} 再次点击整行标题能重新展开',
          st2['open'] and st2['subVisible'],
          f"group={gid} {st2}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--keep', action='store_true')
    args = ap.parse_args()

    port = free_port()
    base = f'http://127.0.0.1:{port}'
    env = dict(os.environ, VP_NO_RECOVER='1')
    # 服务日志必须落文件：挂 PIPE 时 werkzeug 的访问日志写满 64KB 后服务会
    # 阻塞在 write 上，页面加载随之超时。
    logpath = os.path.join(tempfile.gettempdir(), f'vp_nav_it_{port}.log')
    logf = open(logpath, 'w', encoding='utf-8', errors='replace')
    srv = subprocess.Popen(
        [sys.executable, '-c',
         # 必须显式注入 ROOT：项目自带绿色版 Python 目录下有 python312._pth，
         # 该文件存在时进入 isolated 模式，`-c` 的 sys.path 不含 cwd，
         # `import app` 会 ModuleNotFoundError、服务起不来。
         'import sys\n'
         f'sys.path.insert(0, {ROOT!r})\n'
         'from werkzeug.serving import make_server\n'
         'import app\n'
         f"s = make_server('127.0.0.1', {port}, app.app, threaded=True)\n"
         "print('ready', flush=True)\n"
         's.serve_forever()'],
        cwd=ROOT, env=env, stdout=logf, stderr=subprocess.STDOUT)
    drv = None
    try:
        ok = False
        for _ in range(100):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                if s.connect_ex(('127.0.0.1', port)) == 0:
                    ok = True
                    break
            time.sleep(0.3)
        if not ok:
            print('服务启动失败:', logpath)
            return 2
        print(f'临时实例 {base}（日志 {logpath}）')
        print(f'期望一级链接数 {EXPECT_LINKS}（NAV_GROUPS 分组 - result + 总览）')

        drv = make_driver()
        drv.set_page_load_timeout(60)
        W = drv.execute_script

        def viewport(w, h=900):
            """用 CDP 覆写视口尺寸（比 set_window_size 快一个数量级，
            同样会触发媒体查询与 innerWidth 变化）。"""
            drv.execute_cdp_cmd('Emulation.setDeviceMetricsOverride',
                                {'width': w, 'height': h,
                                 'deviceScaleFactor': 1, 'mobile': False})

        # 默认形态自 2026-10-28 起是「顶部」；下面 A/B 两组断言全部按
        # 左侧竖排写，先把它固定成 side 再测（同一 origin，后续导航沿用）。
        drv.get(base + PAGES[0])
        time.sleep(0.8)
        # 磁盘水位警示条（storage-banner，真实盘告警时异步出现）会横插在
        # body 顶部把 nav 压下去——本测试测的是导航几何，先按「知道了」
        # 处理掉（警示条本身另有行为测试），保证断言确定性。
        W("try{sessionStorage.setItem('stBannerDismissed','1');"
          "document.getElementById('storage-banner')&&"
          "document.getElementById('storage-banner').remove()}catch(e){}")
        W("localStorage.setItem('vp_nav_pos','side')")

        for page in PAGES:
            print(f'\n=== {page} ===')
            viewport(1600)
            drv.get(base + page)
            time.sleep(1.2)
            # 让上下文胶囊装最坏情况（长样品名压满宽度）
            names = W("return await (await fetch('/api/samples')).text()")
            try:
                lst = json.loads(names)
            except Exception:
                lst = []
            if lst:
                longest = max((x['name'] for x in lst), key=len)
                projs = [x.get('project') for x in lst if x.get('project')]
                W(f"window.VP_CTX.setSample({json.dumps(longest)});"
                  f"window.VP_CTX.setProject({json.dumps(projs[0] if projs else '')})")
                time.sleep(0.3)

            # ---------- A. 桌面档 ----------
            for w in DESKTOP_WIDTHS:
                viewport(w)
                time.sleep(0.25)
                m = W(MEASURE)
                tag = f'{w}px'
                if not m or m.get('error'):
                    check(f'{page} {tag} 能取到侧栏', False, str(m))
                    continue
                det = (f"navW={m['navW']} pageLeft={m['pageLeft']} "
                       f"ovX={m['overflowX']} links={m['links']}")
                check(f'{page} {tag} 侧栏常驻且不窄于 200px',
                      m['navVisible'] and m['navW'] >= 200, det)
                check(f'{page} {tag} 侧栏不遮挡主区',
                      m['pageLeft'] is not None
                      and m['pageLeft'] >= m['navRight'] - 1, det)
                check(f'{page} {tag} 页面无横向溢出',
                      m['overflowX'] <= 1, det)
                check(f'{page} {tag} {EXPECT_LINKS} 个一级链接全在',
                      m['links'] == EXPECT_LINKS,
                      f"{m['links']}（期望 {EXPECT_LINKS}）")
                check(f'{page} {tag} 顶栏工具入口都在且可见',
                      all(m[k] and m[k]['w'] > 0 and m[k]['h'] > 0
                          for k in ('chip', 'reset', 'lang', 'icon')),
                      f"chip={m['chip']} reset={m['reset']} "
                      f"lang={m['lang']} icon={m['icon']}")
                # 侧栏内容超出时允许内部滚动，但不能溢出到页面外
                check(f'{page} {tag} 侧栏内部可滚动（不撑破页面）',
                      m['navOverflowY'] >= 0, det)

            # 全部分组展开 → 底部固定区不得与上面的分组压字
            # （2026-10-27 缺陷：overflow 默认 visible，被 flex 压扁的列把
            #  文字画到 .sn-foot 上；见 NAV_OVERLAP 注释）
            viewport(1600)
            time.sleep(0.25)
            check_no_overlap(drv, W, page, '1600px 全展开')
            # 低视口高度是最坏情况：内容 2249px 只有 ~500px 可视
            viewport(1600, 560)
            time.sleep(0.25)
            check_no_overlap(drv, W, page, '1600x560 全展开')
            viewport(1600)
            time.sleep(0.25)

            # 矮屏笔记本（1366×768 / 1280×720）：展开靠下分组要自动滚入
            for sw, sh in ((1280, 600), (1100, 560)):
                viewport(sw, sh)
                time.sleep(0.35)
                check_reveal(drv, W, page, f'{sw}x{sh}')
            viewport(1600)
            time.sleep(0.25)

            # 分组折叠/展开（真实鼠标 + :hover 激活，见 check_accordion 注释）
            viewport(1600)
            time.sleep(0.3)
            check_accordion(drv, W, page, '1600px')

            # ---------- B2. 中等档：侧栏常驻但收窄 ----------
            for w in MID_WIDTHS:
                viewport(w)
                time.sleep(0.35)
                m = W(MEASURE)
                tag = f'{w}px'
                if not m or m.get('error'):
                    check(f'{page} {tag} 能取到侧栏', False, str(m))
                    continue
                det = (f"navLeft={m['navLeft']} navW={m['navW']} "
                       f"bodyPad={m['bodyPadLeft']} pageLeft={m['pageLeft']}")
                check(f'{page} {tag} 侧栏常驻（不被抽屉化）',
                      m['navLeft'] >= -1 and m['navW'] > 150, det)
                check(f'{page} {tag} 侧栏宽 = 204（收窄）',
                      m['navW'] == 204, det)
                check(f'{page} {tag} 主区内边距让位 204', m['bodyPadLeft'] in ('204px', '204'), det)
                check(f'{page} {tag} 侧栏不覆盖内容',
                      m['pageLeft'] is not None and m['pageLeft'] >= m['navRight'],
                      det)
                check(f'{page} {tag} 页面无横向溢出', m['overflowX'] <= 1, det)

            # ---------- B. 窄屏档 ----------
            for w in NARROW_WIDTHS:
                viewport(w)
                time.sleep(0.35)
                m = W(MEASURE)
                tag = f'{w}px'
                if not m or m.get('error'):
                    check(f'{page} {tag} 能取到侧栏', False, str(m))
                    continue
                det = (f"navLeft={m['navLeft']} navW={m['navW']} "
                       f"ovX={m['overflowX']}")
                check(f'{page} {tag} 侧栏默认收起',
                      m['navLeft'] + m['navW'] <= 0, det)
                check(f'{page} {tag} ☰ 按钮可见', bool(m['toggle']),
                      f"toggle={m['toggle']}")
                check(f'{page} {tag} 主区恢复全宽',
                      m['bodyPadLeft'] in ('0px', '0'), det)
                check(f'{page} {tag} 页面无横向溢出',
                      m['overflowX'] <= 1, det)

                # 展开抽屉 → 遮罩出现 → 点遮罩收起
                if w == 820:
                    W("document.getElementById('snToggle').click()")
                    time.sleep(0.35)
                    opened = W("""
                      const n=document.getElementById('sideNav');
                      const sc=document.getElementById('snScrim');
                      const r=n.getBoundingClientRect();
                      return {left:Math.round(r.left),
                              open:n.classList.contains('open'),
                              scrim:!!(sc && !sc.hidden),
                              ovX:document.documentElement.scrollWidth-window.innerWidth};
                    """)
                    check(f'{page} {tag} 点 ☰ 后侧栏展开',
                          opened['open'] and opened['left'] >= -1,
                          str(opened))
                    check(f'{page} {tag} 展开时遮罩出现', opened['scrim'],
                          str(opened))
                    W("document.getElementById('snScrim').click()")
                    time.sleep(0.35)
                    closed = W("""
                      const n=document.getElementById('sideNav');
                      const r=n.getBoundingClientRect();
                      return {open:n.classList.contains('open'),
                              left:Math.round(r.left),
                              w:Math.round(r.width)};
                    """)
                    check(f'{page} {tag} 点遮罩后侧栏收起',
                          not closed['open']
                          and closed['left'] + closed['w'] <= 0,
                          str(closed))

            viewport(1600)

        # ---------- C. 顶部形态（2026-10-28 起的默认形态） ----------
        print('\n=== C. 顶部形态 ===')
        # 顶部形态组开头再打一次「知道了」（同标签页 sessionStorage 持久，
        # 保险起见重复设置；banner 若已挂出则一并摘除）
        W("try{sessionStorage.setItem('stBannerDismissed','1');"
          "document.getElementById('storage-banner')&&"
          "document.getElementById('storage-banner').remove()}catch(e){}")
        W("localStorage.setItem('vp_nav_pos','top')")
        for page in PAGES[:2]:
            for w in (1600, 1280):
                check_top_mode(drv, W, base, page, w)

        drv.execute_cdp_cmd('Emulation.clearDeviceMetricsOverride', {})
    finally:
        if drv is not None and not args.keep:
            try:
                drv.quit()
            except Exception:
                pass
        if not args.keep:
            srv.terminate()
            try:
                srv.wait(timeout=15)
            except Exception:
                srv.kill()
        try:
            logf.close()
        except Exception:
            pass

    print()
    if FAIL:
        print(f'✘ {len(FAIL)} 项未通过:')
        for f in FAIL[:25]:
            print('   - ' + f)
        if len(FAIL) > 25:
            print(f'   … 另有 {len(FAIL) - 25} 项')
        return 1
    print('✔ 全部通过')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
