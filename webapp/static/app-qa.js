/* ================= 问答平台（2026-11-02 用户定案）========================
   悬浮机器人 #aiFab 打开的就是这块面板 —— 它是**独立的问答平台**，不是
   「AI 工作台」。两者在三个层面彻底分开：

   1) 界面：本文件只操作 #qaDock，不引用 AI 的任何状态、不碰 #aiDock；
      反之 app-ai.js 也不操作本面板。唯一交集是「互斥」——一方打开时把
      另一方收起来（入口各自都在：球 = 问答，顶栏 🧠 = 工作台）。
   2) 数据：本面板历史存 vp_qa_msgs，开合存 vp_qa_open；工作台那套是
      vp_ai_msgs:* / vp_ai_open，互不覆盖。
   3) 服务端：请求体带 mode:'qa'，chat.py 走 _QA_RULES_* 分支 —— 不注入
      样品/附件/工作空间上下文、不发 tools、不产出结果卡。也就是说它
      在物理上不可能启动任务，也读不到你的数据。

   面板位置跟着悬浮球走（球被拖到左半屏就开在左边、右边同理），球本身
   支持拖动/贴边，见 app.js 的 vpDraggable(fab,'fab',{snap:true})。 */
(function () {
  if (window.QA) return;

  var QA_OPEN_KEY = 'vp_qa_open';
  var QA_MSGS_KEY = 'vp_qa_msgs';
  var QA_KEEP = 40;        // 本地最多留 40 条，别把 localStorage 撑爆
  var QA_SEND_KEEP = 12;   // 每次请求只带最近 12 条给模型
  var QA_W = 400;          // 面板参考宽（CSS 也有上限，这里用于定位计算）
  var QA_H = 560;

  var QA = {
    open: false,
    busy: false,
    msgs: [],
    _abort: null,
    _live: null,
  };
  window.QA = QA;

  function $(id) { return document.getElementById(id); }

  function isEn() {
    var l = (document.documentElement.getAttribute('lang') || '').toLowerCase();
    return l.indexOf('en') === 0;
  }

  /* 新增文案不进 414KB 的 i18n 词典：有词条用词条，没有就用兜底 */
  function tr(key, zh, en) {
    var fb = isEn() ? en : zh;
    try {
      if (typeof window.t === 'function') {
        var v = window.t(key, fb);
        if (v && v !== key) return v;
      }
    } catch (e) { /* 词典没这个键就走兜底 */ }
    return fb;
  }

  function esc(s) {
    if (window.VPMD && VPMD.esc) return VPMD.esc(s);
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function md(src) {
    try {
      if (window.VPMD && VPMD.render) return VPMD.render(src, { mode: 'chat' });
    } catch (e) { /* 渲染器挂了就退回纯文本，不能让回答消失 */ }
    return esc(src).replace(/\n/g, '<br>');
  }

  function examples() {
    return isEn()
      ? ['How do I create a sample?', 'What is an action ticket (执行单)?',
         'Where do result files go?', 'Why is a run showing as failed?']
      : ['怎么新建一个样品？', '执行单是什么？批准之后会发生什么？',
         '结果文件放在哪个目录？', '为什么某个阶段跑失败了？'];
  }

  /* ---------------- 渲染 ---------------- */
  /* 直接复用工作台的气泡类 .ai-msg/.ai-bot/.ai-user —— markdown 渲染产物
     （.ai-h / .ai-tbl / code / .ai-quote）的样式都挂在这几个类下面，自己
     重画一套既费事又会走形。注意：只是**样式**复用，逻辑与状态无关。 */
  QA._bubble = function (role, content) {
    var d = document.createElement('div');
    d.className = 'ai-msg ' + (role === 'user' ? 'ai-user' : 'ai-bot');
    if (role === 'user') d.textContent = content;
    else d.innerHTML = md(content);
    return d;
  };

  QA._scroll = function () {
    var box = $('qaMsgs');
    if (box) box.scrollTop = box.scrollHeight;
  };

  QA.render = function () {
    var box = $('qaMsgs');
    if (!box) return;
    box.innerHTML = '';
    if (!QA.msgs.length) {
      var intro = document.createElement('div');
      intro.className = 'qa-empty';
      intro.innerHTML = '<div class="qa-empty-t">' +
        esc(tr('qa.emptyTitle', '关于平台，随便问', 'Ask about the platform')) + '</div>' +
        '<div class="qa-empty-s">' + esc(tr('qa.emptySub',
          '用法、概念、某个按钮是干什么的、为什么某一步要批准。它不读你的数据，也不跑任何任务。',
          'Usage, concepts, what a button does, why a step needs approval. It does not read your data and never runs tasks.')) +
        '</div>';
      var chips = document.createElement('div');
      chips.className = 'qa-chips';
      examples().forEach(function (q) {
        var b = document.createElement('button');
        b.type = 'button';
        b.className = 'qa-chip';
        b.textContent = q;
        b.onclick = function () { QA.ask(q); };
        chips.appendChild(b);
      });
      intro.appendChild(chips);
      box.appendChild(intro);
      return;
    }
    QA.msgs.forEach(function (m) { box.appendChild(QA._bubble(m.role, m.content)); });
    QA._scroll();
  };

  /* ---------------- 持久化 ---------------- */
  QA.save = function () {
    try {
      localStorage.setItem(QA_MSGS_KEY, JSON.stringify(QA.msgs.slice(-QA_KEEP)));
    } catch (e) { /* 隐私模式/配额满：不影响本次对话 */ }
  };

  QA.restore = function () {
    var raw = null;
    try { raw = localStorage.getItem(QA_MSGS_KEY); } catch (e) { raw = null; }
    var arr = [];
    if (raw) {
      try { arr = JSON.parse(raw); } catch (e) { arr = []; }
    }
    if (!Array.isArray(arr)) arr = [];
    QA.msgs = arr.filter(function (m) {
      return m && (m.role === 'user' || m.role === 'assistant') && typeof m.content === 'string';
    }).slice(-QA_KEEP);
  };

  /* ---------------- 面板定位：默认贴着悬浮球开；手动拖过之后停在放下的地方 ------ */
  function savedPos() {
    try {
      var all = (typeof window._uiDragAll === 'function') ? window._uiDragAll() : null;
      var p = all && all.qa;
      return (p && isFinite(p.x) && isFinite(p.y)) ? {x: +p.x, y: +p.y} : null;
    } catch (e) { return null; }
  }

  /* 顶部安全线：存储/连接横幅 z-index 98 会盖住面板标题栏（拖上去就抓不回来） */
  function safeTop() {
    try {
      if (typeof window._uiSafeTop === 'function') return window._uiSafeTop();
    } catch (e) { /* 忽略 */ }
    return 6;
  }

  QA.position = function () {
    var d = $('qaDock');
    var fab = $('aiFab');
    if (!d || d.hidden) return;
    var vw = window.innerWidth || 800;
    var vh = window.innerHeight || 600;
    var top0 = safeTop();
    var w = Math.min(QA_W, vw - 24);
    var maxH = Math.max(300, vh - top0 - 40);
    var fr = fab ? fab.getBoundingClientRect()
                 : {left: vw - 60, top: vh - 60, width: 52, height: 52,
                    right: vw - 8, bottom: vh - 8};

    // 手动拖过：尊重用户放下的位置（只做视口夹取），不再跟着球跑
    var sp = savedPos();
    if (sp) {
      var h0 = Math.min(QA_H, maxH);
      var px = Math.round(Math.max(6, Math.min(sp.x, vw - w - 6)));
      var py = Math.round(Math.max(top0, Math.min(sp.y, vh - h0 - 6)));
      d.style.width = w + 'px';
      d.style.height = h0 + 'px';
      d.style.bottom = 'auto';
      d.style.left = px + 'px';
      d.style.top = py + 'px';
      d.style.right = 'auto';
      /* 夹取后的坐标写回存档：不然存的是「用户放下的地方」，显示的是「夹完的地方」，
         下次开面板会先跳到夹取前再回来。 */
      if ((px !== Math.round(sp.x) || py !== Math.round(sp.y))
          && typeof window._uiDragSave === 'function') {
        try { window._uiDragSave('qa', {x: px, y: py}); } catch (e) { /* 忽略 */ }
      }
      return;
    }

    /* 没拖过就贴着球：高度先按「球的上/下哪边放得下」缩——宁可矮一点，也别把球
       糊住（糊住以后既点不到球收起，也抓不到球拖动）。实在放不下才退回整高。 */
    var spaceAbove = Math.round(fr.top - 12 - top0);
    var spaceBelow = Math.round(vh - (fr.bottom + 12) - 6);
    var fits = Math.max(spaceAbove, spaceBelow);
    var h = (fits >= 300) ? Math.min(QA_H, maxH, fits) : Math.min(QA_H, maxH);
    d.style.width = w + 'px';
    d.style.height = h + 'px';
    d.style.bottom = 'auto';

    var leftSide = (fr.left + fr.width / 2) < vw / 2;
    if (leftSide) {
      d.style.left = Math.round(Math.max(10, Math.min(fr.left, vw - w - 10))) + 'px';
      d.style.right = 'auto';
    } else {
      d.style.right = Math.round(Math.max(10, Math.min(vw - (fr.left + fr.width), vw - w - 10))) + 'px';
      d.style.left = 'auto';
    }
    var top;
    if (spaceAbove >= h) top = fr.top - 12 - h;          // 球上方放得下：开在上方
    else if (spaceBelow >= h) top = fr.bottom + 12;      // 否则开在下方
    else top = Math.min(fr.bottom + 12, vh - h - 10);    // 都放不下：夹进视口
    d.style.top = Math.round(Math.max(top0, Math.min(top, vh - h - 10))) + 'px';
  };

  /* ---------------- 开合 ---------------- */
  /* 单击开面板，但延后 220ms 再动作：面板一开就盖住球，双击的第二下会落到面板上，
     vpDraggable 的「双击复位」就永远触发不了。220ms 人眼几乎无感，却能把两次点击分开。 */
  QA._ballClickAt = 0;
  QA.onBallClick = function () {
    var now = Date.now();
    if (now - QA._ballClickAt < 320) {   // 第二下：当双击，交给 vpDraggable 复位
      QA._ballClickAt = 0;
      return;
    }
    QA._ballClickAt = now;
    var mine = now;
    setTimeout(function () {
      if (QA._ballClickAt !== mine) return;   // 期间来了第二下
      QA._ballClickAt = 0;
      QA.toggle();
    }, 220);
  };

  QA.toggle = function (force) {
    var want = (force === undefined) ? !QA.open : !!force;
    QA.open = want;
    var d = $('qaDock');
    if (d) d.hidden = !want;
    try { localStorage.setItem(QA_OPEN_KEY, want ? '1' : '0'); } catch (e) { /* 忽略 */ }
    /* 与工作台互斥（两个入口各有各的按钮，收起不会丢入口）。
       首页例外：那儿的工作台是嵌在页面里的（.ai-inline），
       收它等于把整块面板收没，用户会以为工作台被删了。 */
    if (want && window.AI && AI.open) {
      var wb = $('aiDock');
      if (wb && !wb.classList.contains('ai-inline')) {
        try { AI.toggle(false); } catch (e) { /* 忽略 */ }
      }
    }
    if (want) {
      QA.render();
      QA.position();
      var input = $('qaInput');
      if (input) setTimeout(function () { try { input.focus(); } catch (e) { /* 忽略 */ } }, 30);
    }
  };

  QA.clear = function () {
    QA.stop();
    QA.msgs = [];
    QA.save();
    QA.render();
  };

  /* ---------------- 发送 / 停止 ---------------- */
  QA._setBusy = function (busy) {
    QA.busy = !!busy;
    var b = $('qaSendBtn');
    if (b) {
      b.classList.toggle('is-stop', !!busy);
      b.textContent = busy ? '■' : '➤';
      b.title = busy ? tr('qa.stop', '停止生成', 'Stop') : tr('qa.send', '发送（Enter）', 'Send (Enter)');
    }
    var i = $('qaInput');
    if (i) i.readOnly = false;   // 生成中也能先打字，发送时再拦
  };

  QA._startLive = function () {
    var box = $('qaMsgs');
    if (!box) return null;
    var d = QA._bubble('assistant', '');
    // 状态行必须是气泡的**兄弟**节点：气泡内容每来一段 delta 就整体重绘，
    // 放在气泡里面会被 innerHTML 一起冲掉。
    var st = document.createElement('div');
    st.className = 'qa-status';
    st.textContent = tr('qa.thinking', '思考中…', 'Thinking…');
    box.appendChild(d);
    box.appendChild(st);
    QA._scroll();
    return { el: d, body: d, status: st };
  };

  QA._liveText = function (text) {
    if (!QA._live) return;
    QA._live.body.innerHTML = md(text);
    if (QA._live.status) QA._live.status.textContent = '';
    QA._scroll();
  };

  /* 解析一个 SSE 事件块（"data: {...}"），返回 {text, stop} */
  QA._onEvent = function (block) {
    var line = null;
    block.split('\n').forEach(function (l) {
      if (!line && l.indexOf('data:') === 0) line = l.slice(5).trim();
    });
    if (!line) return {};
    var ev = null;
    try { ev = JSON.parse(line); } catch (e) { return {}; }
    if (!ev || !ev.type) return {};
    if (ev.type === 'delta') return { text: String(ev.content == null ? '' : ev.content) };
    if (ev.type === 'reasoning') return {};                     // 问答面板不展示思考链，保持清爽
    if (ev.type === 'notice') {
      if (QA._live && QA._live.status) QA._live.status.textContent = String(ev.message || '');
      return {};
    }
    if (ev.type === 'error') {
      return { text: (QA._live && QA._live.body.textContent ? '\n\n' : '') + '⚠ ' + String(ev.message || ev.error || 'error') };
    }
    if (ev.type === 'tick') {
      if (QA._live && QA._live.status && ev.elapsed != null) {
        QA._live.status.textContent = tr('qa.thinking', '思考中…', 'Thinking…') + ' ' + Math.round(ev.elapsed) + 's';
      }
      return {};
    }
    if (ev.type === 'done') return { stop: true };
    return {};
  };

  QA.send = async function (forcedText) {
    if (QA.busy) return;
    var input = $('qaInput');
    var text = String(forcedText != null ? forcedText : (input && input.value) || '').trim();
    if (!text) return;
    if (input) { input.value = ''; input.style.height = 'auto'; }

    QA.msgs.push({ role: 'user', content: text });
    QA.msgs = QA.msgs.slice(-QA_KEEP);
    QA.render();
    QA.save();

    var history = QA.msgs.slice(-QA_SEND_KEEP);
    QA._setBusy(true);
    QA._live = QA._startLive();
    QA._abort = window.AbortController ? new AbortController() : null;
    var acc = '';
    var failed = '';

    try {
      var resp = await fetch('/api/ai/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode: 'qa', messages: history }),
        signal: QA._abort ? QA._abort.signal : undefined,
      });
      if (!resp.ok || !resp.body) throw new Error('HTTP ' + resp.status);
      var reader = resp.body.getReader();
      var dec = new TextDecoder();
      var buf = '';
      outer:
      for (;;) {
        var rd = await reader.read();
        if (rd.done) break;
        buf += dec.decode(rd.value, { stream: true });
        var i;
        while ((i = buf.indexOf('\n\n')) >= 0) {
          var block = buf.slice(0, i);
          buf = buf.slice(i + 2);
          var got = QA._onEvent(block);
          if (got.text) { acc += got.text; QA._liveText(acc); }
          if (got.stop) break outer;
        }
      }
    } catch (e) {
      if (e && e.name === 'AbortError') {
        failed = tr('qa.stopped', '（已停止）', '(stopped)');
      } else if (e && e.__stopped) {
        /* 正常收尾 */
      } else {
        failed = '⚠ ' + ((e && e.message) || e);
      }
    } finally {
      if (QA._live && QA._live.status) QA._live.status.textContent = '';
      var finalText = (acc + (failed ? (acc ? '\n\n' : '') + failed : '')).trim();
      QA._live = null;
      QA._abort = null;
      QA._setBusy(false);
      if (finalText) QA.msgs.push({ role: 'assistant', content: finalText });
      else if (failed) QA.msgs.push({ role: 'assistant', content: failed });
      QA.msgs = QA.msgs.slice(-QA_KEEP);
      QA.save();
      QA.render();
    }
  };

  QA.sendOrStop = function () {
    if (QA.busy) QA.stop(); else QA.send();
  };

  QA.stop = function () {
    if (QA._abort) { try { QA._abort.abort(); } catch (e) { /* 忽略 */ } }
  };

  /* 从示例问题/外部直接发一句 */
  QA.ask = function (text) {
    if (!QA.open) QA.toggle(true);
    QA.send(text);
  };

  /* ---------------- 启动 ---------------- */
  function autosize() {
    var el = $('qaInput');
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight || 24, 120) + 'px';
  }

  /* 面板自己也能拖（2026-11-02 用户要求：悬浮机器人「支持移动、拖动、贴边」，
     说的不只是那颗球——面板挡着正文时也要能挪开）。手柄只给标题栏，
     免得在输入框/气泡里按住就整块跑；双击标题栏回「贴着球」的默认位。 */
  QA.dragInit = function () {
    var d = $('qaDock');
    if (!d || !window.vpDraggable || d.dataset.dragName) return;
    try {
      window.vpDraggable(d, 'qa', {
        handle: '.qa-head',
        onChange: function () { if (QA.open) QA.position(); },
        onReset: function () { QA.position(); },
      });
    } catch (e) { /* 老页面没有 vpDraggable 就退化成固定位置 */ }
  };

  function boot() {
    QA.restore();
    QA.dragInit();

    var input = $('qaInput');
    if (input) {
      input.addEventListener('keydown', function (ev) {
        if (ev.key === 'Enter' && !ev.shiftKey && !ev.isComposing) {
          ev.preventDefault();
          QA.send();
        }
      });
      input.addEventListener('input', autosize);
      autosize();
    }

    document.addEventListener('keydown', function (ev) {
      if (ev.key !== 'Escape' || !QA.open) return;
      var d = $('qaDock');
      // 只在焦点还在问答面板里时关，别抢页面上别处的 Esc
      if (d && (d.contains(document.activeElement) || document.activeElement === document.body)) {
        QA.toggle(false);
      }
    });

    window.addEventListener('resize', function () { if (QA.open) QA.position(); });
    /* 悬浮球拖完/贴边后，面板要重新贴合。用 vp:dragend 而不是 pointerup：
       pointerup 在 document 上比 app.js 的 window 监听更早触发，会读到拖动前的坐标。 */
    document.addEventListener('vp:dragend', function (ev) {
      var n = ev && ev.detail && ev.detail.name;
      if (n === 'qa' || !QA.open) return;   // 拖的就是面板自己：它已经落位了
      QA.position();
    });

    if (localStorage.getItem(QA_OPEN_KEY) === '1') QA.toggle(true);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
