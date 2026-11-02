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

  function doc(slug) {
    if (!CACHE[slug]) CACHE[slug] = fetchText(BASE + slug + '.md');
    return CACHE[slug];
  }

  function titleOf(md) {
    var m = String(md).match(/^\s*#\s+(.+)$/m);
    return m ? m[1].replace(/[`*]/g, '').trim() : '';
  }

  function renderDoc(slug, md) {
    var src = relink(cleanMd(md));
    var body = (window.VPMD ? window.VPMD.render(src) : '<p>' + esc(src) + '</p>');
    return '<article class="gd-doc" data-slug="' + esc(slug) + '">' + body + '</article>';
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

  function toc(list) {
    var chips = list.filter(function (d) { return d.md; }).map(function (d) {
      var ti = titleOf(d.md) || d.slug;
      return '<a class="gd-chip" href="#gd-' + esc(d.slug) + '">' + esc(ti) + '</a>';
    });
    return chips.length ? '<div class="gd-toc">' + chips.join('') + '</div>' : '';
  }

  function docsHtml(list) {
    return list.map(function (d) {
      if (d.err) return renderError(d.slug, d.err);
      return '<div id="gd-' + esc(d.slug) + '">' + renderDoc(d.slug, d.md) + '</div>';
    }).join('');
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
        var html = '<section class="gd-sec">' + head + toc(list) + docsHtml(list) + '</section>';
        if (globals.length) {
          html += '<details class="gd-global" id="gdGlobal"><summary>' +
            esc(t('tk.wikiGlobal', '平台通用文档')) + '（' + globals.length + '）</summary>' +
            '<div class="gd-global-body" data-slugs="' + esc(globals.join(',')) + '"></div>' +
            '</details>';
        }
        box.innerHTML = html;
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
              holder.innerHTML = toc(list) + docsHtml(list);
            });
          });
        }
      });
    });
  }

  function hide(box) {
    if (box) box.hidden = true;
    CURRENT = '';
  }

  return { show: show, hide: hide, _index: index };
})();
