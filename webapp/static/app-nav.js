/* 主导航交互（2026-10-21 布局重构：顶部横条 → 左侧固定导航栏）
 *
 * 职责只有三件：
 *   1. 分组展开/收起（点击 .sn-caret 或组链接右侧的箭头区）；
 *   2. 窄屏抽屉开合（☰ / 遮罩 / Esc / 点链接后自动关）；
 *   3. 顶栏标题填当前分组名（侧栏收起时主区仍知道自己在哪）。
 *
 * 不负责：链接跳转、当前页高亮（服务端渲染时已定）、上下文胶囊（app.js）。
 * 与 app.js / i18n.js 的契约：本脚本先于它们执行，只做 DOM 绑定，
 * 不依赖 $ / t / esc 等全局函数；所有查询都是原生 API。
 */
(function () {
  'use strict';

  var STORE_KEY = 'vp_nav_open';       // 记住展开的分组（跨页面保持）

  /* 功能栏位置：top（默认）/ side，由 <html data-nav> 决定（见 _nav.html
     顶部内联脚本 + app.css 的 html[data-nav="top"] 段）。两种形态共用同一
     份 DOM，差异只在排布方向与浮层/内联，所以这里只分叉「行为」：
       · 顶部形态：下拉浮层，点外面/按 Esc 收起；不恢复上次展开的分组
         （否则每次进页面都弹一层菜单）；不写 localStorage。
       · 左侧形态：内联手风琴，维持原有跨页记忆。 */
  var TOP = document.documentElement.getAttribute('data-nav') === 'top';

  function byId(id) { return document.getElementById(id); }

  /* ---------------- 1. 分组展开 ---------------- */

  function loadOpen() {
    try {
      var raw = localStorage.getItem(STORE_KEY);
      var arr = raw ? JSON.parse(raw) : null;
      return Array.isArray(arr) ? arr : [];
    } catch (e) { return []; }
  }

  function saveOpen() {
    if (TOP) return;                 // 顶部形态不记忆，见文件头说明
    try {
      var ids = [];
      document.querySelectorAll('.sn-group.open').forEach(function (g) {
        ids.push(g.dataset.group);
      });
      localStorage.setItem(STORE_KEY, JSON.stringify(ids));
    } catch (e) { /* 隐私模式下 localStorage 不可写，忽略 */ }
  }

  function closeAllGroups() {
    document.querySelectorAll('.sn-group.open').forEach(function (g) {
      setOpen(g, false);
    });
  }

  function setOpen(group, open) {
    // 顶部形态是「下拉菜单」语义：同一时刻只允许开一个。展开新的之前先把
    // 其它收起来 —— 否则点开五个就同时挂着五层浮层互相压（2026-10-28 实抓）。
    // 左侧形态是手风琴，允许多组同时展开，保持原样。
    if (open && TOP) {
      document.querySelectorAll('.sn-group.open').forEach(function (o) {
        if (o !== group) setOpen(o, false);
      });
    }
    group.classList.toggle('open', !!open);
    var link = group.querySelector('a.sn-link');
    if (link) link.setAttribute('aria-expanded', open ? 'true' : 'false');
    if (open) positionTopSub(group);
  }

  /* 顶部形态下把下拉落到**整条功能栏下方**。
     功能栏在 1280~1600px 会折成两行（栏高 82px），而 .sn-sub 是相对
     「单个分组」绝对定位的（top:100%+6px），于是正好压住第二行的一级项
     （结果中心 / 设置 / MCP 控制台 / 手册）——2026-10-29 用户截图实抓。
     这里按整条栏的底部反算 top，单行时结果与原来一致（46px）。 */
  function positionTopSub(group) {
    if (!TOP) return;
    var nav = byId('sideNav');
    var sub = group.querySelector('.sn-sub');
    if (!nav || !sub) return;
    var navBottom = nav.getBoundingClientRect().bottom;
    var gTop = group.getBoundingClientRect().top;
    var top = Math.round(navBottom + 6 - gTop);
    sub.style.top = (top > 0 ? top : 0) + 'px';
  }

  /* 窗口尺寸/语言变化会让功能栏重新换行，展开中的下拉要跟着挪 */
  function repositionOpenSubs() {
    if (!TOP) return;
    document.querySelectorAll('.sn-group.open').forEach(positionTopSub);
  }

  /* ---------------- 1b. 左侧形态的可视区跟随 ----------------
     侧栏列表（.navlinks.sn-links）是左侧形态唯一的滚动容器。矮屏上
     一屏放不下 11 个一级项，展开靠下的分组时子项会落到容器底边之外，
     用户看到的是"点了没反应 / 内容被裁掉"（2026-10-29 用户截图实抓：
     720p 展开「数据资源」后内容 575px、可视区只有 303px）。
     这里只在**开合动作与进页面**时把目标滚进可视区，不接管用户
     自己的滚轮；组比可视区还高时以组头贴顶为准。 */
  function linksBox() {
    var links = document.querySelector('.navlinks.sn-links');
    return links ? links : null;
  }

  function revealGroup(group) {
    if (TOP || !group) return;
    var links = linksBox();
    if (!links) return;
    var lr = links.getBoundingClientRect();
    var gr = group.getBoundingClientRect();
    var pad = 8;
    var delta = 0;
    if (gr.bottom > lr.bottom - pad) delta = gr.bottom - (lr.bottom - pad);
    // 上移之后组头不能跑出容器顶边：组过高时让组头贴顶，子项继续往下滚
    if (gr.top - delta < lr.top + pad) delta = gr.top - (lr.top + pad);
    if (delta) links.scrollTop = Math.max(0, links.scrollTop + delta);
    syncMore();
  }

  function revealCurrent() {
    if (TOP) return;
    var links = linksBox();
    if (!links) return;
    var el = links.querySelector('.sn-group[data-current]') ||
             links.querySelector('a.active');
    if (!el) return;
    var lr = links.getBoundingClientRect();
    var er = el.getBoundingClientRect();
    var pad = 8;
    var tall = er.height > (lr.height - pad * 2);
    if (tall || er.top < lr.top + pad) {
      links.scrollTop = Math.max(0, links.scrollTop + er.top - lr.top - pad);
    } else if (er.bottom > lr.bottom - pad) {
      links.scrollTop += er.bottom - (lr.bottom - pad);
    }
    syncMore();
  }

  /* 下面还有内容时给链接列加 .sn-more（CSS 底部渐隐）。Windows 11 的
     覆盖式滚动条不滚就不显示，被切在半行上的画面容易被误读成"内容被裁"。 */
  function syncMore() {
    if (TOP) return;
    var links = linksBox();
    if (!links) return;
    var more = links.scrollHeight - links.clientHeight - links.scrollTop > 2;
    links.classList.toggle('sn-more', more);
  }

  function initMore() {
    if (TOP) return;
    var links = linksBox();
    if (!links) return;
    links.addEventListener('scroll', syncMore, { passive: true });
    window.addEventListener('resize', syncMore);
    if (document.fonts && document.fonts.ready) {
      document.fonts.ready.then(syncMore).catch(function () {});
    }
    syncMore();
  }

  function initGroups() {
    var groups = document.querySelectorAll('.sn-group');
    if (!groups.length) return;

    if (TOP) {
      // 顶部形态：一进页面就收起所有下拉。服务端会给当前组打 open（那是
      // 给左侧手风琴用的），横排下拉在页面加载时就弹着会挡住正文。
      closeAllGroups();
    } else {
      // 恢复上次展开状态：服务端已按当前组打上 open 的保持不动，
      // 其余按 localStorage 补开（同一页面里可能有多个组被用户手动展开过）
      var remembered = loadOpen();
      groups.forEach(function (g) {
        if (g.classList.contains('open')) return;         // 当前组由服务端标记
        if (remembered.indexOf(g.dataset.group) >= 0) setOpen(g, true);
      });
      // 当前页高亮项可能在矮屏可视区之外，进页面先带进来一次
      revealCurrent();
    }

    groups.forEach(function (g) {
      // 整行标题即开关：点一次展开、再点收起（热区 = 整行，不再依赖小箭头）。
      // href 指向组概览页，这里拦下来只做开合；想进组概览点展开后的
      // 「🏠 组概览」那一项（保持"点标题=开合"的单一语义，不猜用户意图）。
      var link = g.querySelector('a.sn-link');
      if (link) {
        link.addEventListener('click', function (e) {
          e.preventDefault();
          setOpen(g, !g.classList.contains('open'));
          saveOpen();
          revealGroup(g);
        });
      }
      // 箭头只是视觉指示（span），点击落到整行标题上即可；这里再兜一层，
      // 万一日后样式把箭头做成独立可点元素也不会漏。
      var caret = g.querySelector('.sn-caret');
      if (caret) {
        caret.addEventListener('click', function (e) {
          e.preventDefault();
          e.stopPropagation();
          setOpen(g, !g.classList.contains('open'));
          saveOpen();
          revealGroup(g);
        });
      }
      // 顶部形态：点了二级入口就收下拉，否则跳转回来还挂着一层浮层
      if (TOP) {
        g.querySelectorAll('.sn-sub a').forEach(function (a) {
          a.addEventListener('click', closeAllGroups);
        });
      }
    });

    if (TOP) {
      // 浮层语义：点空白处或按 Esc 收起（手风琴形态不需要，因为菜单在文档流里）
      document.addEventListener('click', function (e) {
        if (e.target.closest && e.target.closest('.sn-group')) return;
        closeAllGroups();
      });
      document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') closeAllGroups();
      });
    } else {
      initMore();
    }
  }

  /* 顶部形态下功能栏是 sticky 的，顶栏要贴在它下面吸顶。
     高度不能写死：英文标签更长、窄屏会换行，实测 1 行 46px、2 行 82px。
     这里把实测高度写进 --nav-top-h（app.css 里 .topbar { top: var(...) }）。 */
  function syncNavHeight() {
    if (!TOP) return;
    var nav = byId('sideNav');
    if (!nav) return;
    var h = Math.round(nav.getBoundingClientRect().height);
    if (h > 0) {
      document.documentElement.style.setProperty('--nav-top-h', h + 'px');
    }
  }

  function initNavHeight() {
    if (!TOP) return;
    syncNavHeight();
    var t = null;
    window.addEventListener('resize', function () {
      clearTimeout(t);
      t = setTimeout(function () { syncNavHeight(); repositionOpenSubs(); }, 80);
    });
    // 字体晚到会让标签变宽、提前换行，高度要再算一次
    if (document.fonts && document.fonts.ready) {
      document.fonts.ready.then(syncNavHeight).catch(function () {});
    }
  }

  /* ---------------- 2. 窄屏抽屉 ---------------- */

  function initDrawer() {
    // 顶部形态没有抽屉：功能栏常驻，窄屏只是换行（见 app.css 的
    // html[data-nav="top"] 窄屏段）。这里直接不绑定，避免 ☰ 被点开时
    // 把常驻的功能栏当成抽屉移出/移入。
    if (TOP) return;
    var nav = byId('sideNav');
    var toggle = byId('snToggle');
    var scrim = byId('snScrim');
    if (!nav || !toggle) return;

    function setOpen(open) {
      nav.classList.toggle('open', open);
      toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
      if (scrim) scrim.hidden = !open;
      document.body.classList.toggle('sn-drawer-open', open);
    }

    toggle.addEventListener('click', function () {
      setOpen(!nav.classList.contains('open'));
    });
    var closeBtn = byId('snClose');
    if (closeBtn) closeBtn.addEventListener('click', function () { setOpen(false); });
    if (scrim) scrim.addEventListener('click', function () { setOpen(false); });

    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && nav.classList.contains('open')) setOpen(false);
    });

    // 抽屉里点了链接就关（窄屏下侧栏盖住了主区，不关会挡住目标页）
    nav.addEventListener('click', function (e) {
      var a = e.target.closest && e.target.closest('a');
      if (a && window.matchMedia('(max-width: 1024px)').matches) setOpen(false);
    });

    // 拉宽到桌面断点时清掉抽屉态，否则 body 上残留的滚动锁定会生效
    var mq = window.matchMedia('(max-width: 1024px)');
    var onChange = function (ev) { if (!ev.matches) setOpen(false); };
    if (mq.addEventListener) mq.addEventListener('change', onChange);
    else if (mq.addListener) mq.addListener(onChange);
  }

  /* ---------------- 3. 顶栏标题 ---------------- */

  function initTitle() {
    var el = byId('tbTitle');
    if (!el) return;
    // 用服务端标记的 data-current（当前组）而不是「第一个 .open」：
    // .open 还包含用户手动展开过、靠 localStorage 恢复的其它组，
    // 拿第一个会让标题显示成别的组（实测 /results 显示「样本处理」）。
    var cur = document.querySelector('.sn-group[data-current] > a.sn-link');
    var text = cur ? (cur.textContent || '').trim() : '';
    if (!text) {
      // 非分组页（总览 / 任务中心 / 结果中心 / 设置 / 手册）：
      // 取侧栏底部区或首页链接里 active 的那条
      var act = document.querySelector('.sn-foot a.active')
             || document.querySelector('.sn-links > a.active');
      text = act ? (act.textContent || '').trim() : '';
    }
    el.textContent = text;
  }

  function boot() {
    initGroups();
    initDrawer();
    initTitle();
    initNavHeight();
    // i18n 落地后再算一次标题（见 i18n.js 的 vp:i18n 广播）；
    // 切语言会改标签宽度、可能多换一行，高度也要重算。
    document.addEventListener('vp:i18n', function () {
      initTitle();
      syncNavHeight();
      repositionOpenSubs();
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
