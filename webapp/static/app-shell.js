/* 模块切换不刷新（keep-alive 外壳）——「点一下菜单，整页重载、结果全没」的解法。
 *
 * 现场（2026-11-02，用户）：
 *   「你还是切换页面模块，你还是刷新，你到底能不能解决问题」
 *   之前的页面状态快照只能把「表单 + 结果面板」存起来再还原，页面本身还是重载：
 *   滚动位置、正在跑的前端轮询、图表实例、编辑中的表格全部重来。用户要的是
 *   **切换不刷新**。
 *
 * 做法（不动后端、不重写页面）：
 *   - 当前这份文档当「外壳」：顶栏与左侧导航始终留着；
 *   - 点导航链接时，把目标页装进一个 iframe（`?__frame=1`，iframe 内部隐藏自己的
 *     侧栏，顶栏保留），父文档里的原页面 DOM 不销毁、只是主内容藏起来；
 *   - 每个模块一个 iframe，切走只是隐藏，切回来原样显示——**模块自己的 JS 状态、
 *     滚动位置、轮询、图表都还在**，因为那个文档从来没被卸载；
 *   - iframe 里的导航链接被拦截后 message 给父层，由父层切换，不会套娃；
 *   - 点「工作台」(/ 本页) 或同一地址时，收回外壳，显示父文档自己的内容。
 *
 * 边界：
 *   - 只拦「同源 + 普通左键 + 没有 target=_blank/download」的 `<a>`；右键、新标签页、
 *     文件下载、跨站链接全按原样交给浏览器；
 *   - **所有模块都保活，不淘汰**（2026-11-02 用户：「同时保活 所有 模块，不要设上限」）；
 *     切走只是隐藏，iframe 里的轮询/滚动/图表继续活着——这是「回来还是原样」的前提；
 *   - iframe 里再跳页（页面自己的 location.href）只影响那个 iframe，父层不刷新；
 *   - 任何异常都静默降级成普通跳转——外壳坏了也不能把平台弄得不难用。
 */
(function () {
  'use strict';

  var FRAME_CLASS = 'vp-frame';
  var HOST_ID = 'vpShellHost';

  /* 防双挂：模板直引 + app.js 动态挂载同时存在时，先到者赢（否则导航点击
     会被绑两遍、外壳状态对象被重建）。 */
  if (window.VP_SHELL) return;

  var isFrame = window.self !== window.top;
  var frameMode = /[?&]__frame=1(?:&|$)/.test(location.search);

  /* ---------- iframe 内：把导航交给父层 ---------- */

  function hrefOf(a) {
    try {
      var u = new URL(a.getAttribute('href'), location.href);
      return u;
    } catch (e) { return null; }
  }

  function plainClick(ev) {
    if (!ev || ev.defaultPrevented) return false;
    if (ev.button !== 0) return false;
    if (ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey) return false;
    return true;
  }

  function interceptable(a, u) {
    if (!a || !u) return false;
    if (a.target && a.target !== '_self') return false;
    if (a.hasAttribute('download')) return false;
    if (u.origin !== location.origin) return false;
    if (u.protocol !== 'http:' && u.protocol !== 'https:') return false;
    if (/^javascript:/i.test(a.getAttribute('href') || '')) return false;
    /* 纯锚点（同一页内跳转）交给浏览器 */
    if (u.pathname === location.pathname && u.search === location.search && u.hash) return false;
    return true;
  }

  if (isFrame) {
    if (frameMode) document.documentElement.classList.add(FRAME_CLASS);
    document.addEventListener('click', function (ev) {
      var a = ev.target && ev.target.closest ? ev.target.closest('a[href]') : null;
      if (!a || !plainClick(ev)) return;
      var u = hrefOf(a);
      if (!interceptable(a, u)) return;
      ev.preventDefault();
      try {
        parent.postMessage({ vpNav: u.pathname + u.search + u.hash }, location.origin);
      } catch (e) {
        location.href = u.href;
      }
    }, true);
    return;
  }

  /* ---------- 父层：外壳 ---------- */

  var host = null;
  var frames = {};          /* key -> iframe（全部保活，不淘汰） */
  var activeKey = null;
  var activeHash = '';
  var ownHref = location.pathname + location.search;

  /* 一个「模块页」= path + search，一个 iframe。
     #hash 不是新页面，而是同一页里的二级模块（如 /tools?g=kvsuite#t-consensus）：
     切 hash 只改 iframe 内的 hash（页面自己的 hashchange 会换卡片），
     这样点「共识序列分析」「病毒变异分析」不会因为 key 相同被无视掉。 */
  function keyOf(href) {
    try {
      var u = new URL(href, location.href);
      return u.pathname + u.search;
    } catch (e) { return String(href); }
  }

  function hashOf(href) {
    try {
      return new URL(href, location.href).hash || '';
    } catch (e) { return ''; }
  }

  function ensureHost() {
    if (host && host.isConnected) return host;
    host = document.getElementById(HOST_ID);
    if (!host) {
      host = document.createElement('div');
      host.id = HOST_ID;
      host.className = 'vp-shell-host';
      document.body.appendChild(host);
    }
    return host;
  }

  /* ---- 浏览器历史与刷新 ----------------------------------------------------
     URL 刻意不动（用户刷新/收藏仍落在外壳自己的页面），但每切一个模块都压一条
     **同 URL 的历史记录**（history.pushState 只带 state、不带地址）：
       - 后退/前进 → popstate 里按 state.vp 切回对应模块，不再「一按后退就离开平台」；
       - F5 刷新 → history.state 跟着这条记录活着，加载时据此把当前模块装回来，
         不用从头再点一遍。
     pushState 被禁用（无痕/沙箱）时静默跳过，退化成「没历史集成」但外壳照常。 */
  function pushVp(key) {
    try { history.pushState({ vp: key || null }, ''); } catch (e) { /* 忽略 */ }
  }

  function showOwn(fromPop) {
    activeKey = null;
    activeHash = '';
    document.body.classList.remove('shell-mod');
    Object.keys(frames).forEach(function (k) {
      frames[k].classList.remove('on');
    });
    restoreNavOwn();
    if (!fromPop) pushVp(null);
  }

  function showFrame(key) {
    activeKey = key;
    document.body.classList.add('shell-mod');
    Object.keys(frames).forEach(function (k) {
      frames[k].classList.toggle('on', k === key);
    });
  }

  function frameSrc(href) {
    var u = new URL(href, location.href);
    u.searchParams.set('__frame', '1');
    return u.pathname + '?' + u.searchParams.toString() + u.hash;
  }

  function open(href, fromPop, forceReload) {
    var key = keyOf(href);
    var hash = hashOf(href);
    var bare = key.split('#')[0];
    /* 回到外壳自己的页面 → 收起来，父文档原样显示 */
    if (bare === ownHref || (location.pathname === '/' && /^\/(\?.*)?$/.test(bare))) {
      showOwn(fromPop);
      return;
    }
    var prev = activeKey;
    var prevHash = activeHash;
    ensureHost();
    var f = frames[key];
    if (!f) {
      f = document.createElement('iframe');
      f.className = 'vp-shell-frame';
      f.setAttribute('title', '\u6a21\u5757');
      f.src = frameSrc(href);
      frames[key] = f;
      host.appendChild(f);
    } else if (forceReload) {
      /* 强制重载（2026-11-06）：跨页带数据跳转（如分类表「🧪 Primer」把
         contig 序列交给 /primer）必须让目标页重新初始化才能消费 sessionStorage
         里的交接数据；保活帧默认复用旧文档，不重载就永远看不到新序列。 */
      f.src = frameSrc(href);
    } else {
      /* 同一模块页内的另一个二级模块：只改 iframe 里的 hash，
         页面自己的 hashchange 会把对应卡片换出来（不重载、不丢状态）。 */
      var cur = '';
      try { cur = f.contentWindow.location.hash || ''; } catch (e) { cur = ''; }
      if (hash !== cur) {
        try { f.contentWindow.location.hash = hash; }
        catch (e) { f.src = frameSrc(href); }
      }
    }
    showFrame(key);
    activeHash = hash;
    syncNav(key, hash);
    if (!fromPop && (prev !== key || prevHash !== hash)) pushVp(key + hash);
  }

  /* 父文档的导航高亮要跟着 iframe 走：外壳模式下 URL 停在父页面，
     侧栏若继续高亮旧模块，用户会以为「没切过去」。 */
  var ownActive = null;   // 第一次切换前记住父文档原本高亮的是谁

  function restoreNavOwn() {
    if (!ownActive) return;
    var links = document.querySelectorAll('#sideNav a[href]');
    for (var i = 0; i < links.length; i++) links[i].classList.remove('active');
    for (var k = 0; k < ownActive.length; k++) ownActive[k].classList.add('active');
  }

  function syncNav(key, hash) {
    var full = key.split('#')[0];              // path + search
    var path = full.split('?')[0];
    var links = document.querySelectorAll('#sideNav a[href]');
    if (!ownActive) {
      ownActive = [];
      for (var n = 0; n < links.length; n++) {
        if (links[n].classList.contains('active')) ownActive.push(links[n]);
      }
    }
    /* 优先匹配「同一个模块页里 hash 也相同」的那条，其次同 path+search，最后同 path。 */
    var hit = null, loose = null;
    for (var i = 0; i < links.length; i++) {
      var raw = links[i].getAttribute('href') || '';
      var p = raw.split('#')[0];
      var h = raw.indexOf('#') >= 0 ? raw.slice(raw.indexOf('#')) : '';
      if (p === full) {
        if (!hit) hit = links[i];
        if (hash && h === hash) { hit = links[i]; break; }
      } else if (!loose && p.split('?')[0] === path) {
        loose = links[i];
      }
    }
    hit = hit || loose;
    if (!hit) return;
    for (var j = 0; j < links.length; j++) links[j].classList.remove('active');
    hit.classList.add('active');
  }

  function onNavClick(ev) {
    var a = ev.target && ev.target.closest ? ev.target.closest('a[href]') : null;
    if (!a || !plainClick(ev)) return;
    if (a.closest('#' + HOST_ID)) return;
    var u = hrefOf(a);
    if (!interceptable(a, u)) return;
    ev.preventDefault();
    open(u.pathname + u.search + u.hash);
  }

  window.addEventListener('message', function (ev) {
    if (ev.origin !== location.origin) return;
    var d = ev.data;
    if (!d || typeof d !== 'object') return;
    if (typeof d.vpNav === 'string' && d.vpNav.charAt(0) === '/') {
      open(d.vpNav, false, d.vpReload === true);
    }
  });

  /* 后退/前进：按 history.state.vp 切回对应模块；没有 state 就回外壳自己的页面。
     这样「点了几下模块再按后退」不会一脚把用户踢出平台。 */
  window.addEventListener('popstate', function () {
    var st = null;
    try { st = history.state; } catch (e) { st = null; }
    if (st && typeof st.vp === 'string' && st.vp) open(st.vp, true);
    else showOwn(true);
  });

  /* 刷新（F5）后按同一条 history 记录把当前模块装回来，不用从头再点一遍。 */
  function restoreOnLoad() {
    var st = null;
    try { st = history.state; } catch (e) { st = null; }
    if (st && typeof st.vp === 'string' && st.vp) open(st.vp, true);
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', restoreOnLoad);
  } else {
    restoreOnLoad();
  }

  document.addEventListener('click', onNavClick, false);
  /* 注意：**不要**在加载时就把外壳挂出来。`.vp-shell-host` 是 fixed + 不透明底的
     覆盖层（left:侧栏宽），空壳覆盖时父文档自己的工作台/表单会被整片盖住，表现
     就是「工作台一片空白」。只在真要装 iframe 时（open() 里）才 ensureHost()。 */

  window.VP_SHELL = {
    open: open,
    /* 强制重载目标模块帧：跨页数据交接（sessionStorage 预填）必须重载才能生效 */
    reopen: function (href) { open(href, false, true); },
    own: showOwn,
    frames: frames,
    active: function () { return activeKey; },
    count: function () { return Object.keys(frames).length; }
  };
})();
