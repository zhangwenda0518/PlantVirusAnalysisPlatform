/* 页面状态快照（PS）——「切走再回来，页面就空了」的通用解法。
 *
 * 现场（2026-11-02，用户）：
 *   数据资源 → 基因组查询下载，填了 taxon=Oryza sativa、查到 200 条 NCBI 组装；
 *   切到别的模块再回来，表单与整张表都没了。用户问「是不是专门设置的」，
 *   随后又要求「全部都做，都保留」。
 *
 * 结论：不是后端清了什么，也不是开关——一级导航是**整页刷新**，
 *   页面级状态（表单值、查询结果表、单次运行的输出面板）只活在 JS 内存里，
 *   换页即丢。主题、当前样品/项目、对话坞历史早就单独存了，唯独页面自己的状态没存。
 *
 * 做法（两层，互相兜底）：
 *   ① 表单：带 id 的 input/select/textarea，输入后 250ms 落快照；
 *   ② 面板：**自动**。劫持 Element.prototype.innerHTML 的 setter，凡是被页面
 *      JS 写过内容的、带 id 的元素（没标 data-ps-off），就去抖 600ms 存档；
 *      模板里显式标了 data-ps 的容器同样纳入（且优先恢复）。
 *   进页面时按「路径 + 有意义查询参数」还原：表单先回，面板回在还没被本页
 *   JS 写过的地方（已写过的不覆盖，避免和真实渲染打架）。
 *
 * 边界：
 *   - 只影响「显示」，不碰任何数据、不触发查询、不重跑任务；
 *   - password / file / 标了 data-ps-off 的控件与容器不记；
 *   - sessionStorage：同标签页有效，关标签即失效，不会把上回的旧查询带进新会话；
 *   - 12h 过期；单页快照上限 800KB，超了就丢（宁可丢快照，也不撑爆存储）；
 *   - 任何异常静默吞掉——快照是便利功能，不能反过来把页面弄坏。
 */
(function () {
  'use strict';

  var PFX = 'vp_ps:';
  var TTL = 12 * 3600 * 1000;
  var MAX_PAGE = 800 * 1024;
  var MAX_ONE = 200 * 1024;
  var FORM_DEBOUNCE = 250;
  var PANE_DEBOUNCE = 600;

  /* 缓存击穿参数不参与页面键，否则刷新地址一变，快照就永远对不上 */
  var VOLATILE = { t: 1, _: 1, ts: 1, v: 1, _t: 1, cache: 1, p: 1, __frame: 1 };

  /* 页面可以补：「这个查询参数只是我自己的选中项，别拿它当页面身份」。
     （p=当前项目/集合，同一页内会被 history.replaceState 改写，
     若参与页面键，用户切走再回来就会命中另一个旧键 → 参数看起来被改了。）*/
  function volatileParam(k) {
    if (VOLATILE[k]) return true;
    var extra = window.PS_VOLATILE_PARAMS;
    return !!(extra && extra.indexOf && extra.indexOf(k) >= 0);
  }

  function pageKey() {
    try {
      var out = [];
      var sp = new URLSearchParams(location.search);
      sp.forEach(function (val, k) { if (!volatileParam(k)) out.push(k + '=' + val); });
      out.sort();
      return location.pathname + (out.length ? '?' + out.join('&') : '');
    } catch (e) {
      return location.pathname;
    }
  }

  function slot(kind) { return PFX + pageKey() + ':' + kind; }

  function put(kind, value) {
    try {
      var raw = JSON.stringify({ t: Date.now(), v: value });
      if (raw.length > MAX_PAGE) { sessionStorage.removeItem(slot(kind)); return; }
      sessionStorage.setItem(slot(kind), raw);
    } catch (e) { /* 配额满 / 隐私模式：静默 */ }
  }

  function get(kind) {
    try {
      var raw = sessionStorage.getItem(slot(kind));
      if (!raw) return null;
      var o = JSON.parse(raw);
      if (!o || typeof o !== 'object' || !o.t) return null;
      if (Date.now() - o.t > TTL) { sessionStorage.removeItem(slot(kind)); return null; }
      return o;
    } catch (e) { return null; }
  }

  function ago(ts) {
    var m = Math.max(0, Math.round((Date.now() - ts) / 60000));
    if (m < 1) return '刚刚';
    if (m < 60) return m + ' 分钟前';
    return Math.round(m / 60) + ' 小时前';
  }

  /* 示例自动填充（webapp/static/examples.js 的 autoFill）会在页面加载后 400ms
     内把「空表单」填成示例值（/meta 就是物种=Lycium barbarum、测序类型=All）。
     它比快照恢复晚一步，就会把刚恢复的参数又改回去——2026-11-02 用户第二次报
     「参数切页后变了」，真凶就是它，不是快照本身。
     examples.js 本来就留了让路开关：页内脚本置 window.__vpNoAutoFill = true。
     只要本页有快照，说明用户已经在用这一页，示例就该让路。 */
  if (get('form') || get('pane')) window.__vpNoAutoFill = true;

  /* ---------------- 表单 ---------------- */

  function skippable(el) {
    if (!el || !el.id) return true;
    if (el.dataset && el.dataset.psOff !== undefined) return true;
    var tag = (el.tagName || '').toLowerCase();
    if (tag !== 'input' && tag !== 'select' && tag !== 'textarea') return true;
    var ty = (el.type || '').toLowerCase();
    return ty === 'password' || ty === 'file' || ty === 'submit' || ty === 'button';
  }

  function controls() {
    return Array.prototype.filter.call(
      document.querySelectorAll('input[id],select[id],textarea[id]'), function (el) {
        return !skippable(el);
      });
  }

  var formTimer = null, formDirty = false;

  function saveForm() {
    formDirty = false;
    var o = {};
    controls().forEach(function (el) {
      o[el.id] = (el.type === 'checkbox' || el.type === 'radio') ? (el.checked ? 1 : 0) : el.value;
    });
    put('form', o);
  }

  function scheduleFormSave() {
    formDirty = true;
    if (formTimer) return;
    formTimer = setTimeout(function () { formTimer = null; if (formDirty) saveForm(); }, FORM_DEBOUNCE);
  }

  function restoreForm() {
    var rec = get('form');
    if (!rec || !rec.v) return 0;
    var n = 0;
    controls().forEach(function (el) {
      var v = rec.v[el.id];
      if (v === undefined || v === null) return;
      if (el.type === 'checkbox' || el.type === 'radio') {
        var want = !!v;
        if (el.checked !== want) { el.checked = want; n++; }
      } else if (String(el.value) !== String(v)) {
        el.value = v; n++;
        if (el.tagName === 'SELECT' && el.dataset && el.dataset.psOnchange === '1') {
          try { el.dispatchEvent(new Event('change', { bubbles: true })); } catch (e) { /* noop */ }
        }
      }
    });
    return n;
  }

  /* ---------------- 面板（自动跟踪） ---------------- */

  var RESTORED_RE = /<div class="ps-restored"[\s\S]*?<\/div>/g;

  function cleanHTML(html) {
    /* 页面级的「已恢复」提示条不能写进快照，否则会越叠越多 */
    return String(html || '').replace(RESTORED_RE, '');
  }

  function trackable(el) {
    if (!el || !el.id) return false;
    if (el.dataset && el.dataset.psOff !== undefined) return false;
    var tag = (el.tagName || '').toLowerCase();
    if (tag === 'script' || tag === 'style' || tag === 'input' || tag === 'select' ||
        tag === 'textarea' || tag === 'canvas' || tag === 'svg') return false;
    return !!el.closest('body');
  }

  var dirty = {};        /* 本次加载里已被本页 JS 写过内容的元素：恢复时不许覆盖 */
  var panes = {};        /* id -> 最新要存的 HTML */
  var paneTimer = null;

  function flushPanes() {
    paneTimer = null;
    var snap = {};
    var els = document.querySelectorAll('[data-ps]');
    Array.prototype.forEach.call(els, function (el) {
      if (el.id) snap[el.id] = cleanHTML(el.innerHTML);
    });
    Object.keys(autoWritten).forEach(function (id) {
      var el = document.getElementById(id);
      if (el && snap[id] === undefined) snap[id] = cleanHTML(el.innerHTML);
    });
    panes = {};
    put('pane', snap);
  }

  var autoWritten = {};  /* id -> 1：本页 JS 写过内容、可纳入快照的元素 */

  function schedulePaneSave(el) {
    if (!trackable(el)) return;
    var html = el.innerHTML;
    if (html.length > MAX_ONE) { delete autoWritten[el.id]; return; }
    autoWritten[el.id] = 1;
    panes[el.id] = 1;
    if (paneTimer) return;
    paneTimer = setTimeout(flushPanes, PANE_DEBOUNCE);
  }

  /* 劫持 innerHTML：页面写内容时顺手把它记成「可快照的面板」。
     只观察，不改写任何内容；出异常就退化成「只认 data-ps」。 */
  (function hookInnerHTML() {
    try {
      /* innerHTML 定义在 Element.prototype 上（HTMLElement.prototype 上取不到，
         第一版就从 document.documentElement 的原型取，descriptor 为 undefined，
         钩子静默没装上，于是「自动记录面板」永远是空的）。 */
      var d = Object.getOwnPropertyDescriptor(Element.prototype, 'innerHTML');
      if (!d || !d.set) return;
      Object.defineProperty(Element.prototype, 'innerHTML', {
        configurable: true,
        enumerable: d.enumerable,
        get: d.get,
        set: function (v) {
          d.set.call(this, v);
          try { schedulePaneSave(this); } catch (e) { /* 静默 */ }
        }
      });
    } catch (e) { /* 老浏览器 / 被冻结的原型：退化为 data-ps */ }
  })();

  function placeholderish(el) {
    var html = el.innerHTML.trim();
    if (!html) return true;
    if (html.length > 600) return false;
    return /class="hint"/.test(html) || /尚未|暂无|请先|尚无|点「|加载中|^---?$/.test(html);
  }

  function restorePanes() {
    var rec = get('pane');
    if (!rec || !rec.v) return 0;
    var n = 0;
    Object.keys(rec.v).forEach(function (id) {
      var el = document.getElementById(id);
      if (!el || dirty[id]) return;
      var html = cleanHTML(rec.v[id]);
      if (!String(html).trim()) return;
      if (!placeholderish(el) && !el.hasAttribute('data-ps')) return;  /* 本页已渲染，别打架 */
      el.innerHTML = html;
      dirty[id] = 1;
      n++;
    });
    return n;
  }

  function pageNotice(n, ts) {
    if (!n) return;
    try {
      var tip = document.createElement('div');
      tip.className = 'ps-restored';
      tip.textContent = '⏱ 已恢复 ' + ago(ts) + ' 的页面内容（' + n +
        ' 块；只影响显示，不会重跑任务）';
      var a = document.createElement('a');
      a.href = 'javascript:void 0';
      a.textContent = '丢弃本页快照';
      a.onclick = function (ev) { ev.preventDefault(); PS.forget(); tip.remove(); };
      tip.appendChild(document.createTextNode(' · '));
      tip.appendChild(a);
      /* 2026-11-03 布局巡检发现的真坑：无 <main> 的页面（首页等）以前把这条
         插在 body 最前——它是 sticky top:var(--head-h)，未滚动就会从自然位置
         （页面最顶）被"钉"到 head-h，占流空间却留在顶部：顶栏被推下 29px、
         自己又画在顶栏位置上（巡检报 ps-restored×topbar 重叠 oy=29 的根因）。
         现在插到顶栏之后：自然位置 = 吸顶位置，无缝；滚动后照常吸顶。 */
      var main = document.querySelector('main');
      if (main) {
        main.insertBefore(tip, main.firstChild);
      } else {
        var tb = document.querySelector('header.topbar');
        if (tb && tb.parentNode) {
          tb.parentNode.insertBefore(tip, tb.nextSibling);
        } else {
          document.body.insertBefore(tip, document.body.firstChild);
        }
      }
    } catch (e) { /* 静默 */ }
  }

  /* ---------------- 对外 ---------------- */

  var PS = {
    TTL: TTL,
    init: function () {
      try { restoreForm(); } catch (e) { /* 静默 */ }

      document.addEventListener('input', function (ev) {
        if (skippable(ev.target)) return;
        scheduleFormSave();
      }, true);
      document.addEventListener('change', function (ev) {
        if (skippable(ev.target)) return;
        scheduleFormSave();
      }, true);

      var rec = get('pane');
      var paneN = 0;
      try { paneN = restorePanes(); } catch (e) { /* 静默 */ }
      if (paneN && rec) pageNotice(paneN, rec.t);

      window.addEventListener('pagehide', function () {
        try {
          if (formDirty) saveForm();
          if (Object.keys(autoWritten).length) flushPanes();
        } catch (e) { /* 静默 */ }
      });
    },
    savePane: function (el) { if (el) schedulePaneSave(el); },
    forget: function () {
      try {
        var prefix = PFX + pageKey() + ':';
        Object.keys(sessionStorage).forEach(function (k) {
          if (k.indexOf(prefix) === 0) sessionStorage.removeItem(k);
        });
        autoWritten = {};
        dirty = {};
      } catch (e) { /* 静默 */ }
    }
  };

  window.PS = PS;

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', PS.init, { once: true });
  } else {
    PS.init();
  }
})();
