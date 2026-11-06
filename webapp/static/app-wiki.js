/* 组概览的 wiki 文档面板（2026-10-29）
 *
 * 用户要求：把 wiki/ 下按模块写的文档放到「组概览」，别再只给一行描述。
 * 内容来源 = webapp/static/wiki/*.md（wiki/ 的静态副本，index.json 给出
 * 「分组 → 文档」映射）。这里只负责按组取文档、渲染、挂到 #groupDocs；
 * Markdown 解析统一走 VPMD（与 /help 同一个渲染器，语法表现一致）。
 *
 * 约定：
 *   · 组内文档默认全展开（组概览本身就该是一章可读的说明）；
 *   · 「平台通用文档」是跨组的，折叠起来、首次展开才 fetch；
 *   · 同一组重复渲染直接跳过（showLanding 会被 hash 变化反复调用）；
 *   · 任何 fetch 失败只影响那一篇，页面其余部分照常。
 */
window.VPWiki = (function () {
  'use strict';

  var BASE = '/static/wiki/';
  var IDX = null;                  // Promise<index.json>
  var CACHE = {};                  // slug -> Promise<markdown>
  var SLUG2GROUP = null;           // slug -> 组 id（含 global -> ''）
  var CURRENT = '';                // 当前已渲染的组 id
  var OBS = [];                    // 当前生效的 IntersectionObserver（重渲染前 disconnect）

  function t(key, fallback) {
    try {
      if (typeof window.t === 'function') {
        var v = window.t(key, fallback);
        return v || fallback;
      }
    } catch (e) { /* 忽略 */ }
    return fallback;
  }

  function esc(s) {
    return (window.VPMD && window.VPMD.esc)
      ? window.VPMD.esc(s)
      : String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
                 .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  function fetchText(url) {
    return fetch(url, { cache: 'no-cache' }).then(function (r) {
      if (!r.ok) throw new Error(r.status + ' ' + url);
      return r.text();
    });
  }

  function index() {
    if (!IDX) {
      IDX = fetchText(BASE + 'index.json').then(function (txt) {
        var idx = JSON.parse(txt);
        var map = {};
        Object.keys(idx.groups || {}).forEach(function (g) {
          idx.groups[g].forEach(function (s) { map[s] = g; });
        });
        (idx.global || []).forEach(function (s) { map[s] = ''; });
        SLUG2GROUP = map;
        return idx;
      }).catch(function () {
        SLUG2GROUP = {};
        return { groups: {}, global: [] };
      });
    }
    return IDX;
  }

  /* wiki 每篇开头都有一行「返回 Home ｜ 上一页 ｜ 下一页」导航，那是 GitHub
     wiki 的翻页条，在应用里点不动（相对链接），直接去掉。 */
  function cleanMd(md) {
    return String(md).replace(/^\s*>\s*返回[^\n]*$/m, '')
                     .replace(/^\s*>\s*上一页[^\n]*$/m, '')
                     .replace(/^\s*\n/, '');
  }

  /* 正文里的 156 个 `[模块](Slug)` 互链：改写成组概览内锚点或跨组跳转，
     否则渲染器会把相对链接降级成 href="#"（点了跳到页首）。 */
  function relink(md) {
    var map = SLUG2GROUP || {};
    return String(md).replace(/\]\(([A-Za-z][A-Za-z0-9_-]*)\)/g,
      function (whole, slug) {
        if (!(slug in map)) return whole;
        var g = map[slug];
        if (g === CURRENT) return '](#gd-' + slug + ')';
        if (!g) return '](/tools?g=' + CURRENT + '#gdGlobal)';
        return '](/tools?g=' + g + '#gd-' + slug + ')';
      });
  }

  /* wiki 文档里的图片按 GitHub wiki 口径写相对路径 `assets/xxx.png`（同一份
     原文在 GitHub 上也要能显示）。但应用页面的基址是 /tools，浏览器会把它
     拼成站点根下的 assets/xxx.png（404）。
     这里只重写**图片语法**里的 assets/ 相对路径；普通链接 [文字](assets/…)
     不动，带 `..` 的路径可疑（可能越出 assets/），原样保留不重写。
     重写后的 URL 仍交给 VPMD 渲染（产出 <img class="md-img">）。 */
  function relinkImages(md) {
    return String(md).replace(/!\[([^\]]*)\]\(([^)]+)\)/g,
      function (whole, alt, dest) {
        var u = String(dest).trim();
        if (u.indexOf('assets/') !== 0) return whole;
        if (u.indexOf('..') >= 0) return whole;
        return '![' + alt + '](' + BASE + 'assets/' + u.slice(7) + ')';
      });
  }

  function doc(slug) {
    if (!CACHE[slug]) CACHE[slug] = fetchText(BASE + slug + '.md');
    return CACHE[slug];
  }

  function titleOf(md) {
    var m = String(md).match(/^\s*#\s+(.+)$/m);
    return m ? m[1].replace(/[`*]/g, '').trim() : '';
  }

  function renderDoc(slug, md, meta) {
    var src = relink(relinkImages(cleanMd(md)));
    var body = (window.VPMD ? window.VPMD.render(src) : '<p>' + esc(src) + '</p>');
    var attrs = '';
    if (meta) {
      attrs = ' data-mod="' + meta.n + '" data-mod-total="' + meta.total +
        '" data-toc="' + esc(meta.toc || 'gd-toc') + '"' +
        ' data-title="' + esc(meta.title || '') + '"';
      if (meta.prev) {
        attrs += ' data-prev-slug="' + esc(meta.prev.slug) +
          '" data-prev-title="' + esc(meta.prev.title) + '"';
      }
      if (meta.next) {
        attrs += ' data-next-slug="' + esc(meta.next.slug) +
          '" data-next-title="' + esc(meta.next.title) + '"';
      }
    }
    return '<article class="gd-doc" data-slug="' + esc(slug) + '"' + attrs + '>' +
      body + '</article>';
  }

  function renderError(slug, err) {
    return '<article class="gd-doc gd-doc-err" data-slug="' + esc(slug) + '">' +
      '<p class="hint">' + esc(t('tk.wikiFail', '文档加载失败')) + '：' +
      esc(slug) + '.md（' + esc(String(err && err.message || err)) + '）</p></article>';
  }

  /* 按顺序取文档（保证 PDF/截图里的章节次序稳定），失败的那篇单独降级 */
  function loadDocs(slugs) {
    return Promise.all(slugs.map(function (s) {
      return doc(s).then(function (md) { return { slug: s, md: md }; },
                         function (e) { return { slug: s, err: e }; });
    }));
  }

  function toc(list, id) {
    var chips = list.filter(function (d) { return d.md; }).map(function (d) {
      var ti = titleOf(d.md) || d.slug;
      return '<a class="gd-chip" href="#gd-' + esc(d.slug) + '">' + esc(ti) + '</a>';
    });
    return chips.length ? '<div class="gd-toc" id="' + esc(id || 'gd-toc') + '">' +
      chips.join('') + '</div>' : '';
  }

  function docsHtml(list, tocId) {
    var titles = list.map(function (d) {
      return d.md ? (titleOf(d.md) || d.slug) : d.slug;
    });
    return list.map(function (d, i) {
      var meta = {
        n: i + 1, total: list.length, toc: tocId || 'gd-toc',
        title: titles[i],
        prev: i > 0 ? { slug: list[i - 1].slug, title: titles[i - 1] } : null,
        next: i < list.length - 1 ? { slug: list[i + 1].slug, title: titles[i + 1] } : null
      };
      var wrap = '<div id="gd-' + esc(d.slug) + '" class="gd-doc-wrap" data-slug="' +
        esc(d.slug) + '">';
      if (d.err) return wrap + renderError(d.slug, d.err) + '</div>';
      return wrap + renderDoc(d.slug, d.md, meta) + '</div>';
    }).join('');
  }

  /* ---------- 渲染后的 DOM 增强（2026-11-05） ----------
   * 文档内导航 / 标题锚点 / 模块徽章 / 页脚翻页 / 图片点击放大。
   * 标题 id 用确定性序号 gd-<slug>-h<k>（k = 文档内 h2/h3 出现顺序，h1 是
   * 文档标题不进目录），不从标题文本生成——中文标题做 slug 既不可靠又易撞。 */
  function textOf(el) {
    return String(el.textContent || '').replace(/\s+/g, ' ').trim();
  }

  function addBadge(art) {
    var n = parseInt(art.getAttribute('data-mod'), 10);
    var total = parseInt(art.getAttribute('data-mod-total'), 10);
    if (!(n > 0) || !(total > 0)) return;
    var b = document.createElement('span');
    b.className = 'gd-badge';
    b.textContent = t('tk.wikiModule', '模块') + ' ' + n + ' / ' + total;
    var h1 = art.querySelector('h1.md-h1');
    if (h1) h1.appendChild(b);
    else if (art.firstChild) art.insertBefore(b, art.firstChild);
    else art.appendChild(b);
  }

  function addPageToc(art, items) {
    if (items.length < 3) return;          // 短文档（≤2 个标题）不生成
    var nav = document.createElement('nav');
    nav.className = 'gd-page-toc';
    var html = '<span class="gd-page-toc-label">' +
      esc(t('tk.wikiNav', '本页导航')) + '</span>';
    items.forEach(function (it) {
      html += '<a class="gd-chip' + (it.level === 3 ? ' gd-chip-h3' : '') +
        '" href="#' + it.id + '">' + esc(it.text || it.id) + '</a>';
    });
    nav.innerHTML = html;
    var h1 = art.querySelector('h1.md-h1');
    if (h1 && h1.parentNode) h1.parentNode.insertBefore(nav, h1.nextSibling);
    else if (art.firstChild) art.insertBefore(nav, art.firstChild);
    else art.appendChild(nav);
  }

  function addFooter(art) {
    var tocId = art.getAttribute('data-toc') || 'gd-toc';
    var ps = art.getAttribute('data-prev-slug');
    var pt = art.getAttribute('data-prev-title') || ps;
    var ns = art.getAttribute('data-next-slug');
    var nt = art.getAttribute('data-next-title') || ns;
    var sep = (window.VP_LANG === 'en') ? ': ' : '：';
    var foot = document.createElement('div');
    foot.className = 'gd-doc-foot';
    var html = ps
      ? '<a class="gd-foot-prev" href="#gd-' + esc(ps) + '">← ' +
        esc(t('tk.wikiPrev', '上一篇')) + esc(sep) + esc(pt) + '</a>'
      : '<span class="gd-foot-prev is-off">← ' +
        esc(t('tk.wikiPrev', '上一篇')) + '</span>';
    html += '<a class="gd-foot-top" href="#' + esc(tocId) + '">↑ ' +
      esc(t('tk.wikiGroupTop', '返回本组导航')) + '</a>';
    html += ns
      ? '<a class="gd-foot-next" href="#gd-' + esc(ns) + '">' +
        esc(t('tk.wikiNext', '下一篇')) + esc(sep) + esc(nt) + ' →</a>'
      : '<span class="gd-foot-next is-off">' +
        esc(t('tk.wikiNext', '下一篇')) + ' →</span>';
    foot.innerHTML = html;
    art.appendChild(foot);
  }

  /* 图片点击在新标签页打开原图：包一层 <a target="_blank">，不改 img 本身 */
  function linkImages(art) {
    var imgs = art.querySelectorAll('img.md-img');
    for (var i = 0; i < imgs.length; i++) {
      var img = imgs[i];
      var parent = img.parentNode;
      if (!parent || parent.tagName === 'A') continue;
      var src = img.getAttribute('src') || '';
      if (!src) continue;
      var a = document.createElement('a');
      a.className = 'gd-img-link';
      a.setAttribute('href', src);
      a.setAttribute('target', '_blank');
      a.setAttribute('rel', 'noopener noreferrer');
      a.setAttribute('title', img.getAttribute('alt') || src);
      parent.insertBefore(a, img);
      a.appendChild(img);
    }
  }

  function enhanceDoc(art) {
    var slug = art.getAttribute('data-slug') || 'doc';
    var hs = art.querySelectorAll('h2.md-h2, h3.md-h3, h4.md-h4');
    var items = [];
    for (var i = 0; i < hs.length; i++) {
      var el = hs[i];
      var id = 'gd-' + slug + '-h' + (i + 1);
      el.id = id;
      el.className = el.className + ' gd-anch';
      var lv = el.tagName === 'H4' ? 4 : (el.tagName === 'H3' ? 3 : 2);
      items.push({ id: id, level: lv, text: textOf(el) });
    }
    addBadge(art);
    addPageToc(art, items);
    addFooter(art);
    linkImages(art);
    return items;
  }

  /* 收集整组文档的「文档 → 标题」结构，供粘性侧边栏目录使用 */
  function enhanceDocs(root) {
    var out = [];
    if (!root) return out;
    var arts = root.querySelectorAll('.gd-doc');
    for (var i = 0; i < arts.length; i++) {
      var art = arts[i];
      var items = enhanceDoc(art);
      out.push({
        slug: art.getAttribute('data-slug') || ('doc' + (i + 1)),
        title: art.getAttribute('data-title') ||
          (art.querySelector('h1.md-h1') ? '' : art.getAttribute('data-slug')),
        items: items
      });
    }
    return out;
  }

  /* ---------- 粘性侧边栏目录（2026-11-05b 起） ----------
   * 用户反馈：文档内目录胶囊在文档顶部，点完锚点滚下去后目录就留在上面，
   * 再想跳转得滚回来。改成整组（含展开后的「平台通用文档」）共用一条
   * 粘性目录：滚动全程可见、当前位置高亮、点即跳。
   * 2026-11-05c：目录默认放左侧（用户要求），层级一眼可辨（文档 ● → 二级 ─ →
   * 三级 ·），并支持调整布局：⇄ 左右换侧 / 「»」折叠成细条 / 边缘拖拽调宽，
   * 偏好写 localStorage（vp.wikiRail），全平台组概览共用。 */
  var RAIL_KEY = 'vp.wikiRail';
  var RAIL_W_MIN = 170, RAIL_W_MAX = 460;   // 拖拽调宽的上下限（px）

  function railPrefs() {
    try { return JSON.parse(localStorage.getItem(RAIL_KEY) || '{}'); }
    catch (e) { return {}; }
  }

  function saveRailPrefs(p) {
    try { localStorage.setItem(RAIL_KEY, JSON.stringify(p)); } catch (e) { /* 忽略 */ }
  }

  /* 2026-11-05c：树状层级（用户反馈：一级/二级/三级没有树状展示、模块间
     也没有间隔）。每篇文档 = 一个 .gd-rail-group（一级标题 + 子树），子树
     .gd-rail-sub 带垂直引导线，h2/h3 用 CSS 横枝连接；组与组之间靠
     .gd-rail-group + .gd-rail-group 的间距 + 虚线分隔。 */
  function railEntriesHtml(struct) {
    var html = '';
    struct.forEach(function (d) {
      html += '<div class="gd-rail-group">' +
        '<a class="gd-rail-doc" href="#gd-' + esc(d.slug) + '">' +
        esc(d.title || d.slug) + '</a>';
      if (d.items.length) {
        html += '<div class="gd-rail-sub">';
        d.items.forEach(function (it) {
          html += '<a class="gd-rail-h' + it.level + '" href="#' + it.id + '">' +
            esc(it.text || it.id) + '</a>';
        });
        html += '</div>';
      }
      html += '</div>';
    });
    return html;
  }

  /* 目录栏偏好落到位：换侧 / 折叠 / 宽度（--gd-rail-w 设在 box 上，
     .gd-layout 与返回顶部按钮 .gd-top 都从它继承） */
  function applyRailPrefs(box, rail) {
    var p = railPrefs();
    var side = (p.side === 'right') ? 'right' : 'left';    // 默认左侧
    var layout = box.querySelector('.gd-layout');
    if (layout) {
      layout.classList.add(side === 'left' ? 'rail-left' : 'rail-right');
      if (p.collapsed) layout.classList.add('rail-collapsed');
    }
    var w = parseInt(p.w, 10);
    if (w >= RAIL_W_MIN && w <= RAIL_W_MAX) {
      box.style.setProperty('--gd-rail-w', w + 'px');
    }
    updateRailBtns(rail, side, !!p.collapsed);
  }

  function updateRailBtns(rail, side, collapsed) {
    if (!rail) return;
    var sideBtn = rail.querySelector('.gd-rail-side');
    var foldBtn = rail.querySelector('.gd-rail-fold');
    if (sideBtn) {
      sideBtn.textContent = '⇄';
      sideBtn.title = (side === 'left')
        ? t('gd.railToRight', '目录移到右侧')
        : t('gd.railToLeft', '目录移到左侧');
    }
    if (foldBtn) {
      foldBtn.textContent = (side === 'right') ? '»' : '«';
      foldBtn.title = collapsed
        ? t('gd.railExpand', '展开目录')
        : t('gd.railCollapse', '收起目录');
    }
  }

  /* 折叠成 34px 细条后点细条本身展开 */
  function expandRail(box) {
    var layout = box.querySelector('.gd-layout');
    if (!layout) return;
    layout.classList.remove('rail-collapsed');
    var p = railPrefs(); delete p.collapsed; saveRailPrefs(p);
    var rail = box.querySelector('.gd-rail');
    updateRailBtns(rail, layout.classList.contains('rail-left') ? 'left' : 'right', false);
  }

  function wireRailControls(box, rail) {
    if (!rail || rail.__railWired) return;
    rail.__railWired = true;
    var layout = box.querySelector('.gd-layout');
    if (!layout) return;

    function curSide() {
      return layout.classList.contains('rail-left') ? 'left' : 'right';
    }

    rail.querySelector('.gd-rail-side').addEventListener('click', function () {
      var toLeft = !layout.classList.contains('rail-left');
      layout.classList.toggle('rail-left', toLeft);
      layout.classList.toggle('rail-right', !toLeft);
      var p = railPrefs(); p.side = toLeft ? 'left' : 'right'; saveRailPrefs(p);
      updateRailBtns(rail, curSide(), layout.classList.contains('rail-collapsed'));
    });

    rail.querySelector('.gd-rail-fold').addEventListener('click', function () {
      var toCollapse = !layout.classList.contains('rail-collapsed');
      layout.classList.toggle('rail-collapsed', toCollapse);
      var p = railPrefs();
      if (toCollapse) p.collapsed = 1; else delete p.collapsed;
      saveRailPrefs(p);
      updateRailBtns(rail, curSide(), toCollapse);
    });

    var mini = rail.querySelector('.gd-rail-mini');
    if (mini) mini.addEventListener('click', function () { expandRail(box); });

    /* 内侧边缘拖拽调宽（170–460px，松手落盘） */
    var grip = rail.querySelector('.gd-rail-grip');
    if (grip) grip.addEventListener('pointerdown', function (e) {
      e.preventDefault();
      var startX = e.clientX;
      var startW = rail.getBoundingClientRect().width;
      var side = curSide();
      function onMove(ev) {
        var d = ev.clientX - startX;
        var w = (side === 'right') ? startW - d : startW + d;
        w = Math.max(RAIL_W_MIN, Math.min(RAIL_W_MAX, w));
        box.style.setProperty('--gd-rail-w', w + 'px');
      }
      function onUp(ev) {
        window.removeEventListener('pointermove', onMove);
        window.removeEventListener('pointerup', onUp);
        var w = Math.max(RAIL_W_MIN, Math.min(RAIL_W_MAX,
          (side === 'right') ? startW - (ev.clientX - startX)
                             : startW + (ev.clientX - startX)));
        var p = railPrefs(); p.w = Math.round(w); saveRailPrefs(p);
      }
      window.addEventListener('pointermove', onMove);
      window.addEventListener('pointerup', onUp);
    });
  }

  function buildRail(box, struct) {
    var layout = box.querySelector('.gd-layout');
    if (!layout) return null;
    var old = layout.querySelector('.gd-rail');
    if (old) old.remove();
    var rail = document.createElement('nav');
    rail.className = 'gd-rail';
    rail.innerHTML =
      '<div class="gd-rail-head">' +
        '<span class="gd-rail-label">' + esc(t('tk.wikiContents', '目录')) + '</span>' +
        '<span class="gd-rail-btns">' +
          '<button type="button" class="gd-rail-btn gd-rail-side" aria-label="side">⇄</button>' +
          '<button type="button" class="gd-rail-btn gd-rail-fold" aria-label="fold">»</button>' +
        '</span>' +
      '</div>' +
      '<div class="gd-rail-body">' + railEntriesHtml(struct) + '</div>' +
      '<div class="gd-rail-mini" title="' + esc(t('gd.railExpand', '展开目录')) + '">' +
        '<span class="gd-rail-mini-ic">«</span>' +
        '<span class="gd-rail-mini-tx">目录</span>' +
      '</div>' +
      '<div class="gd-rail-grip" title="' + esc(t('gd.railDrag', '拖拽调整宽度')) + '"></div>';
    layout.appendChild(rail);
    applyRailPrefs(box, rail);
    wireRailControls(box, rail);
    return rail;
  }

  /* 「平台通用文档」展开后把它的条目追加到同一条侧栏（分隔线 + 文档块） */
  function appendRailSection(rail, struct) {
    if (!rail) return;
    var body = rail.querySelector('.gd-rail-body') || rail;
    body.insertAdjacentHTML('beforeend',
      '<div class="gd-rail-div">' +
      esc(t('tk.wikiGlobal', '平台通用文档')) + '</div>' +
      railEntriesHtml(struct));
  }

  /* ---------- 滚动监听（IntersectionObserver）+ 当前胶囊高亮 ---------- */
  function killObservers() {
    for (var i = 0; i < OBS.length; i++) {
      try { OBS[i].disconnect(); } catch (e) { /* 忽略 */ }
    }
    OBS = [];
  }

  function setDocActive(box, slug) {
    var chips = box.querySelectorAll('.gd-toc .gd-chip');
    for (var i = 0; i < chips.length; i++) {
      var on = chips[i].getAttribute('href') === '#gd-' + slug;
      if (on) chips[i].classList.add('on');
      else chips[i].classList.remove('on');
    }
    var rdocs = box.querySelectorAll('.gd-rail .gd-rail-doc');
    for (var j = 0; j < rdocs.length; j++) {
      var on2 = rdocs[j].getAttribute('href') === '#gd-' + slug;
      if (on2) rdocs[j].classList.add('on');
      else rdocs[j].classList.remove('on');
    }
  }

  function setHeadActive(box, id) {
    var links = box.querySelectorAll('.gd-page-toc .gd-chip');
    for (var i = 0; i < links.length; i++) {
      var on = links[i].getAttribute('href') === '#' + id;
      if (on) links[i].classList.add('on');
      else links[i].classList.remove('on');
    }
    var rl = box.querySelectorAll('.gd-rail .gd-rail-h2, .gd-rail .gd-rail-h3, .gd-rail .gd-rail-h4');
    for (var j = 0; j < rl.length; j++) {
      var on2 = rl[j].getAttribute('href') === '#' + id;
      if (on2) rl[j].classList.add('on');
      else rl[j].classList.remove('on');
    }
  }

  function wire(box) {
    killObservers();
    if (!box || typeof window.IntersectionObserver !== 'function') return;
    var docIO = new window.IntersectionObserver(function (entries) {
      for (var i = 0; i < entries.length; i++) {
        var en = entries[i];
        if (!en.isIntersecting) continue;
        var node = en.target;
        if (!node || node.isConnected === false) continue;   // DOM 已重渲染
        var slug = node.getAttribute('data-slug');
        if (slug) setDocActive(box, slug);
      }
    }, { rootMargin: '-14% 0px -70% 0px', threshold: 0 });
    OBS.push(docIO);
    var wraps = box.querySelectorAll('.gd-doc-wrap[data-slug]');
    for (var j = 0; j < wraps.length; j++) docIO.observe(wraps[j]);

    var headIO = new window.IntersectionObserver(function (entries) {
      for (var k = 0; k < entries.length; k++) {
        var en = entries[k];
        if (!en.isIntersecting) continue;
        var node = en.target;
        if (!node || node.isConnected === false) continue;
        setHeadActive(box, node.id);
        var m = /^gd-(.+)-h\d+$/.exec(node.id || '');
        if (m) setDocActive(box, m[1]);
      }
    }, { rootMargin: '-14% 0px -74% 0px', threshold: 0 });
    OBS.push(headIO);
    var anchors = box.querySelectorAll('.gd-anch');
    for (var n = 0; n < anchors.length; n++) headIO.observe(anchors[n]);
  }

  /* 组内锚点统一平滑滚动：顶部胶囊 / 本页导航 / 页脚翻页 / 返回本组导航 */
  function bindDocClicks(box) {
    if (box.__gdClickBound) return;        // show() 会多次调用，只绑一次
    box.__gdClickBound = true;
    box.addEventListener('click', function (ev) {
      var el = ev.target;
      while (el && el !== box && el.tagName !== 'A') el = el.parentNode;
      if (!el || el === box) return;
      var href = el.getAttribute('href') || '';
      if (href.indexOf('#gd-') !== 0) return;
      var id = href.slice(1);
      var target = document.getElementById(id);
      if (!target) return;
      ev.preventDefault();
      if (target.scrollIntoView) {
        target.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }
      try { history.replaceState(null, '', '#' + id); } catch (e) { /* 忽略 */ }
    });
  }

  /* 返回顶部：只挂在模块说明区容器里，容器被隐藏（切到具体模块）时自然不显示 */
  function ensureTop(box) {
    if (box.querySelector('.gd-top')) return;
    var btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'gd-top';
    btn.title = t('tk.wikiTop', '返回顶部');
    btn.setAttribute('aria-label', t('tk.wikiTop', '返回顶部'));
    btn.textContent = '↑';
    btn.addEventListener('click', function () {
      var sec = box.querySelector('.gd-sec');
      if (sec && sec.scrollIntoView) {
        sec.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }
    });
    box.appendChild(btn);
  }

  function langNote() {
    return (window.VP_LANG === 'en')
      ? '<span class="gd-note">Wiki 文档为中文原文</span>' : '';
  }

  function show(groupId, box) {
    if (!box) return;
    if (box.dataset.group === groupId && !box.hidden) return;   // 已渲染
    box.hidden = false;
    box.dataset.group = groupId;
    CURRENT = groupId;
    index().then(function (idx) {
      var slugs = (idx.groups && idx.groups[groupId]) || [];
      var globals = idx.global || [];
      if (!slugs.length && !globals.length) { box.hidden = true; return; }
      loadDocs(slugs).then(function (list) {
        if (CURRENT !== groupId) return;                        // 组已切走
        var head = '<div class="gd-head"><h2>' +
          esc(t('tk.wikiTitle', '模块说明')) + '</h2>' +
          '<span class="hint">' + esc(t('tk.wikiHint', '以下内容来自 wiki/ 的模块文档，与手册同一份原文')) +
          '</span>' + langNote() + '</div>';
        var html = '<div class="gd-layout"><div class="gd-main-col">' +
          '<section class="gd-sec">' + head + toc(list) + docsHtml(list) + '</section>';
        if (globals.length) {
          html += '<details class="gd-global" id="gdGlobal"><summary>' +
            esc(t('tk.wikiGlobal', '平台通用文档')) + '（' + globals.length + '）</summary>' +
            '<div class="gd-global-body" data-slugs="' + esc(globals.join(',')) + '"></div>' +
            '</details>';
        }
        html += '</div></div>';
        killObservers();                 // 旧 DOM 即将被替换，先把观察器断掉
        box.innerHTML = html;
        bindDocClicks(box);
        var struct = enhanceDocs(box);
        box.__gdRail = buildRail(box, struct);
        wire(box);
        ensureTop(box);
        // 跨组跳转带 #gd-<slug>：文档是异步渲染的，浏览器原生锚点定位会落空
        var h = (location.hash || '').replace('#', '');
        if (h.indexOf('gd-') === 0) {
          var target = document.getElementById(h);
          if (target && target.scrollIntoView) {
            target.scrollIntoView({ block: 'start' });
          }
        }
        var g = box.querySelector('#gdGlobal');
        if (g) {
          g.addEventListener('toggle', function once() {
            if (!g.open) return;
            g.removeEventListener('toggle', once);
            var holder = g.querySelector('.gd-global-body');
            var gs = (holder.dataset.slugs || '').split(',').filter(Boolean);
            loadDocs(gs).then(function (list) {
              holder.innerHTML = toc(list, 'gd-toc-global') +
                docsHtml(list, 'gd-toc-global');
              var gs2 = enhanceDocs(holder);
              appendRailSection(box.__gdRail, gs2);
              wire(box);
            });
          });
        }
      });
    });
  }

  function hide(box) {
    killObservers();
    if (box) box.hidden = true;
    CURRENT = '';
  }

  return { show: show, hide: hide, _index: index };
})();
