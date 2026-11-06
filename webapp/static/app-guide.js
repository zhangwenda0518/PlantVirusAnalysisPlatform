/* 卡片内「📖 使用引导」（2026-11-06，用户要求"全模块补充引导使用说明"）
 * --------------------------------------------------------------
 * 背景：模块文档（wiki/*.md 的静态副本）此前只在**组概览**渲染；
 * 用户点进具体卡片后引导就消失了。本组件把引导带进每张卡片：
 *
 *   带 data-guide="Slug" 的卡片，在其第一个 <h2> 下方注入一条可折叠引导：
 *     折叠态：📖 使用引导 · 展开步骤
 *     展开态：输入 / 流程步骤（≤6）/ 产物 / 下一步衔接
 *             + 「完整文档」链接（跳组概览对应锚点）
 *
 * 内容单一来源 = /static/wiki/<Slug>.md（与组概览同一份文件，零重复维护）。
 * 折叠态零网络请求（展开才 fetch， Promise 缓存）；展开状态记忆在
 * localStorage(vp_guide_open)。
 *
 * 抽取口径（与 tests/test_card_guides.py 的 Python 侧逐字对齐，两边一起改）：
 *   · 找 h2 节：输入 / 流程(步骤|怎么用|使用|工作流程|链路|快速上手) / 产物(输出|示例输出) / 相关(下一步|衔接)
 *   · 输入·产物 = 该节第一段正文（跳过表格/代码块/子标题），截 110 字
 *   · 步骤 = 完整步骤概览：有 ### 子节 → 每子节一步（标题+前2条要点，≤8 步）；
 *     无子节 → 平铺 bullet ≤8 条
 *   · 下一步 = 相关节内 wiki 互链（≤3），href 改写为组概览锚点跳转
 */
window.VPGuide = (function () {
  'use strict';

  var BASE = '/static/wiki/';
  var OPEN_KEY = 'vp_guide_open';
  var IDX = null;                    // Promise<{slug: group}>
  var CACHE = {};                    // slug -> Promise<md>

  function t(key, fallback) {
    try { var v = window.t ? window.t(key, fallback) : fallback; return v || fallback; }
    catch (e) { return fallback; }
  }
  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function index() {
    if (!IDX) {
      IDX = fetch(BASE + 'index.json', { cache: 'no-cache' })
        .then(function (r) { return r.json(); })
        .then(function (idx) {
          var map = {};
          Object.keys(idx.groups || {}).forEach(function (g) {
            (idx.groups[g] || []).forEach(function (s) { map[s] = g; });
          });
          (idx.global || []).forEach(function (s) { map[s] = ''; });
          return map;
        })
        .catch(function () { return {}; });
    }
    return IDX;
  }

  function doc(slug) {
    if (!CACHE[slug]) {
      CACHE[slug] = fetch(BASE + encodeURIComponent(slug) + '.md',
                          { cache: 'no-cache' })
        .then(function (r) {
          if (!r.ok) throw new Error('HTTP ' + r.status);
          return r.text();
        });
    }
    return CACHE[slug];
  }

  /* ---------- 抽取（与 Python 侧对齐） ---------- */
  var STEP_H = /^(流程|步骤|怎么用|使用|工作流程|链路|快速上手)/;
  var IN_H = /^(输入|数据输入)/;
  var OUT_H = /^(产物|输出|示例输出)/;
  var NEXT_H = /^(相关|下一步|衔接)/;
  var LIST_IT = /^\s*(?:[-*]|\d+[.、])\s+/;

  function sections(txt) {
    var out = {}, cur = '__pre__', buf = [];
    String(txt).split(/\r?\n/).forEach(function (ln) {
      var m = ln.match(/^##\s+(.*)/);
      if (m) {
        if (cur !== '__pre__') out[cur] = buf;
        cur = m[1].trim(); buf = [];
      } else buf.push(ln);
    });
    out[cur] = buf;
    return out;
  }

  function stripMd(s) {
    return String(s)
      .replace(/\*\*(.+?)\*\*/g, '$1')
      .replace(/`([^`]*)`/g, '$1')
      .replace(/\[([^\]]*)\]\(([^)]*)\)/g, '$1')
      .replace(/\s+/g, ' ').trim();
  }

  function firstPara(lines) {
    for (var i = 0; i < lines.length; i++) {
      var s = lines[i].trim();
      if (!s || /^#/.test(s) || /^\|/.test(s) || /^```/.test(s)) continue;
      return s.slice(0, 110);
    }
    return '';
  }

  /* 完整步骤概览（2026-11-06 二轮，用户要求"更完整的步骤概览"）：
   * · 流程节有 ### 子节 → **每个子节 = 一步**（标题 + 前 2 条要点以"；"连接），
   *   完整覆盖 步骤一…步骤N / 配套 / 失败处理 等全部子节，不再只取第一子节；
   * · 无 ### 子节 → 收平铺 bullet（≤ cap 条）。
   * 步骤为 {title, detail}；两侧（tests/test_card_guides.py _steps_of）同改。
   * cap = 子节数上限，默认 8。 */
  function stepsOf(lines, cap) {
    cap = cap || 8;
    var subs = [], plain = [], cur = null;
    for (var i = 0; i < lines.length; i++) {
      var l = lines[i];
      var mh = l.match(/^###\s+(.*)$/);
      if (mh) {
        cur = { title: stripMd(mh[1]).slice(0, 60), bullets: [] };
        if (cur.title) subs.push(cur);
        continue;
      }
      if (LIST_IT.test(l)) {
        var b = stripMd(l.replace(LIST_IT, '')).slice(0, 120);
        if (!b) continue;
        if (cur) { if (cur.bullets.length < 2) cur.bullets.push(b); }
        else if (!subs.length) plain.push(b);
      } else if (cur && !cur.bullets.length && !cur.para && l.trim()
                 && !/^[|`>#-]/.test(l.trim())) {
        // 子节没有 bullet 时取首个正文段落兜底（如「五段顺序」的链式说明），
        // 不让步骤只剩裸标题；表格/代码块/引用/子标题不算。
        cur.para = stripMd(l).slice(0, 120);
      }
    }
    if (subs.length) {
      return subs.slice(0, cap).map(function (u) {
        var d = u.bullets.join('；') || u.para || '';
        return { title: u.title, detail: d.slice(0, 190) };
      });
    }
    return plain.slice(0, cap).map(function (b) {
      return { title: b, detail: '' };
    });
  }

  /* 展示层内联 md：先转义再套最小格式（b / code / wiki 链接改写） */
  function inline(s, slug2group, curSlug) {
    var x = esc(s);
    x = x.replace(/\*\*(.+?)\*\*/g, '<b>$1</b>');
    x = x.replace(/`([^`]*)`/g, '<code>$1</code>');
    x = x.replace(/\[([^\]]*)\]\(([^)]*)\)/g, function (w, txt, dest) {
      // txt/dest 已随 x 整体转义过，这里不再 esc（二次转义会把 &amp; 变 &amp;amp;）；
      // dest 里的引号在 x 里已是 &quot;，href 属性安全。
      var u = String(dest);
      if (/^(https?:|\/|#|mailto:)/.test(u)) return '<a href="' + u + '" target="_blank" rel="noopener">' + txt + '</a>';
      if (!/[\/\s]/.test(u)) {
        var g = slug2group[u];
        if (g === undefined) return txt;
        if (g === '' ) return '<a href="' + GH_WIKI + encodeURIComponent(u) + '.md">' + txt + '</a>';
        return '<a href="/tools?g=' + encodeURIComponent(g) + '#gd-' + encodeURIComponent(u) + '">' + txt + '</a>';
      }
      return txt;
    });
    return x;
  }

  function extract(slug, md, slug2group) {
    var secs = sections(md);
    var res = { title: '', input: '', steps: [], out: '', next: [] };
    var m1 = String(md).match(/^\s*#\s+(.+)$/m);
    if (m1) res.title = m1[1].replace(/[`*]/g, '').trim();
    for (var h in secs) {
      if (STEP_H.test(h) && !res.steps.length) res.steps = stepsOf(secs[h], 6);
      else if (IN_H.test(h) && !res.input) res.input = firstPara(secs[h]);
      else if (OUT_H.test(h) && !res.out) res.out = firstPara(secs[h]);
      else if (NEXT_H.test(h) && !res.next.length) {
        // 相关节：只收 wiki 互链文本
        var joined = secs[h].join('\n');
        var re = /\[([^\]]+)\]\(([A-Za-z][A-Za-z0-9_-]*)\)/g, mm;
        while ((mm = re.exec(joined)) && res.next.length < 3) {
          if (mm[2] === slug) continue;      // 指回自己的跳过
          res.next.push({ text: mm[1], slug: mm[2] });
        }
      }
    }
    return res;
  }

  /* ---------- 渲染 ---------- */
  function openSet() {
    try { return JSON.parse(localStorage.getItem(OPEN_KEY) || '{}') || {}; }
    catch (e) { return {}; }
  }
  function openSave(slug, on) {
    try {
      var s = openSet(); if (on) s[slug] = 1; else delete s[slug];
      localStorage.setItem(OPEN_KEY, JSON.stringify(s));
    } catch (e) { /* 隐私模式等：不记也罢 */ }
  }

  var GH_WIKI = 'https://github.com/zhangwenda0518/PlantVirusPlatform/blob/main/wiki/';
  function docHref(slug, slug2group) {
    var g = slug2group[slug];
    if (g === '') return GH_WIKI + encodeURIComponent(slug) + '.md';
    // 组内文档 → 组概览对应锚点；未知 slug 兜底 /help（手册页有 wiki 链接表）
    return '/tools?g=' + encodeURIComponent(g || 'sample') + '#gd-'
      + encodeURIComponent(slug);
  }

  function headHtml() {
    return '<span class="vp-guide-ico">📖</span> <b>' + esc(t('g.title', '使用引导')) + '</b>' +
      '<span class="vp-guide-sub">' + esc(t('g.openHint', '输入 · 步骤 · 产物 · 下一步')) + '</span>' +
      '<span class="vp-guide-caret">▾</span>';
  }

  function build(slug) {
    var bar = document.createElement('div');
    bar.className = 'vp-guide';
    bar.dataset.slug = slug;
    bar.innerHTML =
      '<button type="button" class="vp-guide-head" aria-expanded="false">' + headHtml() + '</button>' +
      '<div class="vp-guide-body" hidden><div class="vp-guide-loading hint">'
      + esc(t('g.loading', '加载引导内容…')) + '</div></div>';

    var opened = false;
    var head = bar.querySelector('.vp-guide-head');
    var body = bar.querySelector('.vp-guide-body');
    head.addEventListener('click', function () {
      opened = !opened;
      head.setAttribute('aria-expanded', opened ? 'true' : 'false');
      bar.classList.toggle('open', opened);
      body.hidden = !opened;
      if (opened) fill(slug, body);
      openSave(slug, opened);
    });
    // 语言切换（i18n.js 派发 vp:i18n）：重刷标题行 + 已展开内容
    document.addEventListener('vp:i18n', function () {
      head.innerHTML = headHtml();
      if (opened) fill(slug, body);
    });
    // 上次展开过：进来直接展开（fetch 走缓存，几乎无感）
    if (openSet()[slug]) { head.click(); }
    return bar;
  }

  function fill(slug, body) {
    Promise.all([doc(slug), index()]).then(function (arr) {
      var md = arr[0], s2g = arr[1];
      var r = extract(slug, md, s2g);
      var h = [];
      if (r.input) {
        h.push('<div class="vp-guide-row"><span class="vp-guide-k">'
          + esc(t('g.input', '输入')) + '</span><span>'
          + inline(r.input, s2g, slug) + '</span></div>');
      }
      if (r.steps.length) {
        h.push('<div class="vp-guide-row"><span class="vp-guide-k">'
          + esc(t('g.steps', '流程')) + '</span><ol>'
          + r.steps.map(function (s) {
              var tt = inline(s.title, s2g, slug);
              return '<li>'
                + (s.detail ? '<b>' + tt + '</b>：' + inline(s.detail, s2g, slug)
                            : tt)
                + '</li>';
            }).join('')
          + '</ol></div>');
      }
      if (r.out) {
        h.push('<div class="vp-guide-row"><span class="vp-guide-k">'
          + esc(t('g.out', '产物')) + '</span><span>'
          + inline(r.out, s2g, slug) + '</span></div>');
      }
      if (r.next.length) {
        h.push('<div class="vp-guide-row"><span class="vp-guide-k">'
          + esc(t('g.next', '下一步')) + '</span><span class="vp-guide-links">'
          + r.next.map(function (n) {
              var g = s2g[n.slug];
              var href = docHref(n.slug, s2g);
              return '<a href="' + href + '">' + esc(n.text) + '</a>';
            }).join('') + '</span></div>');
      }
      h.push('<div class="vp-guide-foot"><a href="' + esc(docHref(slug, s2g))
        + '">' + esc(t('g.full', '打开完整文档')) + ' ↗</a></div>');
      body.innerHTML = h.length
        ? h.join('')
        : '<div class="hint">' + esc(t('g.empty', '该模块暂无引导内容')) + '</div>';
    }).catch(function (e) {
      body.innerHTML = '<div class="hint">' + esc(t('g.fail', '引导内容加载失败'))
        + '：' + esc(String(e && e.message || e)) + '</div>';
    });
  }

  /* ---------- 注入 ---------- */
  function inject(root) {
    (root || document).querySelectorAll('[data-guide]').forEach(function (el) {
      if (el.querySelector(':scope > .vp-guide')) return;   // 幂等
      var slug = el.getAttribute('data-guide');
      if (!slug) return;
      var bar = build(slug);
      var h2 = el.querySelector(':scope > h2');
      if (h2 && h2.nextSibling) el.insertBefore(bar, h2.nextSibling);
      else if (h2) h2.insertAdjacentElement('afterend', bar);
      else el.insertBefore(bar, el.firstChild);
    });
  }

  return { inject: inject, extract: extract, _internal:
           { sections: sections, stepsOf: stepsOf, firstPara: firstPara, stripMd: stripMd } };
}());

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', function () { VPGuide.inject(); });
} else {
  VPGuide.inject();
}
