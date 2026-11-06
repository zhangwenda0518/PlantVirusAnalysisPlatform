/* 植物病毒分析平台 · AI 助手对话坞（总览页）
 * 依赖：app.js 的 $/esc、i18n.js 的 t()（均先于本脚本加载）。
 * 流式：POST /api/ai/chat → SSE（data: {...}\n\n），fetch ReadableStream 逐块解析。
 * 服务端无状态：每次请求携带完整对话历史（客户端持有），上下文注入在服务端做。
 */
'use strict';

/* 防重复包含：本脚本随 _nav.html 注入每个页面，同一页只会加载一次；
 * 万一某页额外手滑再引一次，这里直接短路，避免重复绑定与双份事件流。 */
if (window.AI) {
  /* already loaded */
} else {

const AI_MSGS_KEY = 'vp_ai_msgs';        // 旧版单键（迁移用，不再写入）
const AI_CONV_KEY = 'vp_ai_conv';        // sessionStorage：本标签页的会话 id
const AI_SEEN_KEY = 'vp_ai_lastseen';
const AI_EID_KEY = 'vp_ai_eid';          // sessionStorage：刷新后续传的事件水位
const AI_HIST_MAX = 12;                  // 最多保留几段会话（localStorage 键数）
/* 2026-11-02（P2）：原来固定 30 条，聊到一半回头就发现「前面那段没了」。
 * 改成「条数上限 + 字节上限」双闸：条数放宽到 200，真正卡的是体积
 * （AI_MSGS_BYTES≈1.8M 字符，localStorage 单域一般 5MB，留足余量）。
 * 裁剪永远从最旧的整轮开始丢，不会把一整轮拆成「有问无答」。 */
const AI_MSGS_CAP = 200;                 // 每段会话最多的消息条数（硬上限）
const AI_MSGS_BYTES = 1800000;           // 每段会话最大的序列化字符数
const AI_PERSIST_MS = 2000;              // 流式中周期性落盘的间隔（P0-1）
const AI_CLEAR_KEY = 'vp_ai_clear_at';   // 上次「清空」的时刻（过滤旧执行单卡）
const AI_OPEN_KEY = 'vp_ai_open';        // 坞的开合（换页照旧，见 AI.toggle）
/* 2026-11-02（用户第 N 次报「切模块回来问答没了」）：镜像键。
 * 每段会话一个键（vp_ai_msgs:<convId>），会话 id 存在 sessionStorage —— 新标签页、
 * 隐私模式、sessionStorage 被清时 id 会换，旧键就再也接不上；镜像键记「最后一次
 * 写过的对话 + 它的会话 id」，恢复时当前键为空就回落到它，并把 id 认回来。 */
const AI_LAST_KEY = 'vp_ai_msgs:__last';

const AI = {
  open: false, busy: false, ready: false, ctxLoaded: false,
  sample: '',            // 当前选中样品（'' = 不注入样品 digest）
  messages: [],          // {role:'user'|'assistant', content}
  lastEventId: 0,        // 本会话流水位（EventSource 重连自动带 Last-Event-ID）
  lastSeenId: 0,         // 跨刷新未读水位（localStorage）：看过的不再计未读
  unread: 0,             // 对话坞关闭期间的重要事件数
  attachments: [],       // 本轮附加的文件 [{name, path}]（2026-10-30）
  convId: '',            // 本标签页会话 id（sessionStorage 持久，刷新不换）
  _es: null,
  _ctrl: null,           // 当前生成的 AbortController
  _title0: null,         // 页面原始标题（未读计数前缀恢复用）
  _bus: null,            // BroadcastChannel：多标签事件中继
  _leader: false,        // 本标签页是否持有事件流的"主"身份
  _lockRelease: null,    // 释放 Web Locks 的回调（pagehide 时调用）
  _ctxP: null,           // ensureCtx 的在途 promise（并发调用合流）
  _backlog: null,        // 历史回放的折叠容器
  _backlogN: 0,
  _seenAction: {},       // 已渲染过的执行单 id → status（对账用）
  _navTicks: [],         // 右侧提问导航：[{tick, node}]（2026-11-02）
  _navIdx: -1,           // 当前高亮的轮次
  _navRaf: 0,            // 滚动合帧句柄
  _navMO: null,          // #aiMsgs 直接子节点观察器
  _navT: 0,              // 导航重建去抖定时器
  _navBound: false,
  _navMouse: false,      // 焦点是不是鼠标点出来的（决定要不要弹预览）
  _flashT: 0,            // 落点闪光清理定时器
  _restoring: false,     // 正在恢复历史（恢复期间不往 #aiMsgs 增量挂节点）
  _restoreP: null,       // 恢复历史在途 promise（并发调用合流，P0-2）
  _persistT: 0,          // 流式落盘定时器（P0-1）
  _persistFn: null,      // 流式落盘取文本的回调（P0-1）
  _convMenu: null,       // 历史会话下拉节点（P1-1）
  /* ---- 2026-11-04 对话坞增强 ---- */
  _samplesCache: {t: 0, raw: null, p: null},  // 样品清单缓存（60s，@补全与本地命令共用）
  _mention: null,        // @ 样品补全下拉节点
  _mentionItems: [],     // 当前候选样品名
  _mentionIdx: -1,       // 键盘高亮下标
  _mentionToken: null,   // 当前 @token 在输入框里的范围 {start, end}
  _retryMenu: null,      // ↻ 重试菜单节点（F7）
  _nextBusy: {},         // 下一步建议 key → 1（防重复起草）
};

try { AI.lastSeenId = parseInt(localStorage.getItem(AI_SEEN_KEY) || '0', 10)
      || 0; } catch (e) {}

/* sessionStorage 封装：隐私模式/禁用时静默降级为内存值 */
AI._ss = {
  get(k) { try { return sessionStorage.getItem(k); } catch (e) { return null; } },
  set(k, v) { try { sessionStorage.setItem(k, String(v)); } catch (e) {} },
};

/* ---------- 会话 id 与历史键 ----------
 * 2026-11-02：以前所有标签页共写 `vp_ai_msgs` 一个键，两个标签各聊一段就
 * 互相覆盖（刷新丢历史、上下文串台）。现在每段会话一个键，会话 id 存在
 * sessionStorage —— 刷新仍是同一段会话，新标签页开一段新的，互不干扰。 */
AI._initConv = function () {
  let id = AI._ss.get(AI_CONV_KEY);
  if (!id) {
    let seed = '';
    try {
      const raw = localStorage.getItem(AI_MSGS_KEY);
      const arr = raw ? JSON.parse(raw) : null;
      if (Array.isArray(arr) && arr.length) seed = raw;   // 迁移旧版单键
    } catch (e) {}
    id = seed ? 'main' : ('c' + Date.now().toString(36)
                          + Math.random().toString(36).slice(2, 6));
    AI._ss.set(AI_CONV_KEY, id);
    if (seed) {
      try {
        localStorage.setItem('vp_ai_msgs:' + id,
          JSON.stringify({t: Date.now(), msgs: JSON.parse(seed).slice(-AI_MSGS_CAP)}));
        localStorage.removeItem(AI_MSGS_KEY);
      } catch (e) {}
    }
  }
  AI.convId = id;
  AI.lastEventId = parseInt(AI._ss.get(AI_EID_KEY) || '0', 10) || 0;
};
AI._initConv();
AI.msgsKey = function () { return 'vp_ai_msgs:' + AI.convId; };

/* ---------- 开合与初始化 ---------- */

/* 坞的开合状态（2026-11-02）：一级导航是整页跳转，新页面里坞默认收起，
 * 用户就以为「换个模块刚才的对话没了」——历史其实还在 localStorage，只是
 * 没开坞所以没渲染。记住用户上次的开合，换页后照旧（历史由 boot 里的
 * AI.toggle(true) → ensureCtx → _restoreHistory 接上）。 */
AI.toggle = function (force) {
  /* 2026-11-02：开合意图以「屏幕上看得见什么」为准，而不是 AI.open 这个标志。
   * 实测踩到过一次：AI.open 与 #aiDock[hidden] 因为初始化时序短暂不一致，
   * 用户点 🧠 时按标志算出的 want 等于当前状态，点了跟没点一样（坞不弹）。
   * 从 DOM 反推就永远「点一下翻一次」，状态标志顺手被校正回真实值。 */
  const dockEl = $('aiDock');
  const shown = !!(dockEl && !dockEl.hidden);
  const want = (force === undefined) ? !shown : !!force;
  AI.open = want;
  if (dockEl) dockEl.hidden = !want;
  /* 悬浮球 #aiFab 自 2026-11-02 起是独立「问答平台」的入口（app-qa.js），
     不再跟工作台联动 —— 这里绝不能再去 hidden 它，否则问答入口会凭空消失。 */
  try { localStorage.setItem(AI_OPEN_KEY, want ? '1' : '0'); } catch (e) {}
  if (want) {
    /* 问答面板与工作台互斥（两个入口各有各的按钮，收起不会丢入口）；
       首页的内嵌台（.ai-inline）不算浮坞，不去关问答面板。 */
    const inlineWb = $('aiDock') && $('aiDock').classList.contains('ai-inline');
    if (!inlineWb && window.QA && QA.open) { try { QA.toggle(false); } catch (e) { /* 忽略 */ } }
    AI.clearUnread();
    AI.ensureCtx();
    try { AI.renderChips(); } catch (e) { /* 先按上次的上下文画一版，别留空行 */ }
    AI.hydratePending();
    $('aiInput').focus();
    /* 收起时量不到 offsetTop/clientHeight（display:none），开坞后补算一次
       右侧提问导航的落点与高亮（2026-11-02）。 */
    const raf = window.requestAnimationFrame || (f => setTimeout(f, 16));
    raf(() => { AI.initTurnNav(); AI.rebuildTurnNav(); });
  }
};

/* 补渲染/对账执行单（2026-11-01，2026-11-02 扩展）：
 * pending 状态只在事件流里活几秒——批准后状态翻转、按钮即被移除，页面刷新
 * 或断线重连错过了 pending 事件就永远看不到批准入口。
 * 打开对话坞（或建流）时向 /api/ai/actions 对一次账：
 *   ① 有 pending 单但卡片缺按钮 → 补按钮；
 *   ② 卡片还标着「待你批准」但后端早已非 pending（后端重启、事件丢失）
 *      → 按真实状态翻转并摘掉按钮，别让用户点了才吃 400；
 *   ③ 最近几条完全没渲染过的执行单 → 补一张只读历史卡，
 *      让用户回头能查到「上次批准到底跑没跑」。 */
AI.hydratePending = async function () {
  let d = null;
  try {
    const r = await fetch('/api/ai/actions');
    if (r.ok) d = await r.json();
  } catch (e) { return; }
  if (!d) return;
  const pend = Array.isArray(d.pending) ? d.pending : (d.pending ? [d.pending] : []);
  /* 用户点过「清空」之后，清空时刻之前的执行单不再补卡（否则换页回来
   * 又冒出最近三条只读历史卡）。pending 的不受影响 —— 那些还要人拍板。 */
  let clearedAt = Number(AI._clearedAt) || 0;
  if (!clearedAt) {
    try { clearedAt = Number(localStorage.getItem(AI_CLEAR_KEY) || 0) || 0; } catch (e) {}
  }
  const recent = (Array.isArray(d.recent) ? d.recent : [])
    .filter(p => !clearedAt || (Number(p && p.ts) || 0) > clearedAt);
  const live = {};
  pend.forEach(p => { live[p.id] = 'pending'; });
  recent.forEach(p => { live[p.id] = p.status; });

  // ② 卡片与真实状态对账（带上 recent 记录里的 tid，刷新后也能续上任务进度）
  [...document.querySelectorAll('.ai-action')].forEach(n => {
    const st = live[n.dataset.aid];
    if (!st || st === 'pending') return;
    const rec = recent.find(p => String(p.id) === String(n.dataset.aid));
    AI._flipActionCard(n, st, null, null, Object.assign({}, rec, {backlog: true}));
  });

  // ① 补按钮
  pend.forEach((p) => {
    const old = [...document.querySelectorAll('.ai-action')]
      .find((n) => n.dataset.aid === p.id);
    if (!old) {
      /* 整张卡片都没渲染过（错过全部事件）：按事件口径造一张 pending 卡 */
      AI.renderEvent(Object.assign({}, p, {
        type: 'ai_action', aid: p.id, id: -1, backlog: true,
      }));
      return;
    }
    if (old.querySelector('.ai-gate-btns')) return;   // 按钮还在，不用补
    /* 卡片在但按钮已被状态翻转摘掉：把徽章翻回「待你批准」并补按钮区 */
    const b = old.querySelector('.ai-gate-st');
    if (b) {
      b.textContent = t('ai.act.pending', '待你批准');
      b.classList.remove('done');
      b.classList.add('pending');
    }
    old.appendChild(AI._actionBtns(p.id));
  });

  /* ③ 已决执行单不再自动补只读历史卡（2026-11-02 二次定案）。
   *
   * 原来这里会把服务端 `recent` 里「最近已决、界面没渲染过」的单子补成卡片，
   * 本意是「回头能查到上次批准到底跑没跑」。代价太大：服务端最近名单里只要
   * 有历史单（含测试脚本造的 `reason=并发测试 / unit test`），用户每次开坞、
   * 换页都会在问候语下面凭空多出几张「AI 执行单」，清空也压不住 —— 用户反复
   * 反馈「我没建过这些单，怎么一直出现」。
   *
   * 现在：①（补 pending 按钮）②（状态对账）保留 —— 那是用户真在等的单；
   * 已决的单要看就去任务中心（`/tasks`）或右下角运行条，那里有真任务 id、
   * 进度和产物，比一张只读卡有用。`/api/ai/actions` 仍然照常对账状态。 */
};

/* 执行单按钮区（data 属性 + 事件委托；不再把 id 拼进 inline onclick，
 * 样品名/档案名里带引号时按钮会静默失效）。 */
AI._actionBtns = function (aid) {
  const d = document.createElement('div');
  d.className = 'ai-gate-btns';
  d.innerHTML =
    `<button class="btn small primary" data-ai-act="action" data-aid="${esc(aid)}"`
    + ` data-decision="approve">${esc(t('ai.act.approve', '✓ 批准执行'))}</button>`
    + `<button class="btn small danger" data-ai-act="action" data-aid="${esc(aid)}"`
    + ` data-decision="reject">${esc(t('ai.act.reject', '✗ 驳回'))}</button>`;
  return d;
};

/* ---------- 执行单的真实任务进度（2026-11-02） ----------
 * 后端批准后只推一条 launched（带 tid），中途进度与终态由任务中心自己
 * 维护；以前卡片停在「✓ 已启动运行」，用户看不到跑到哪、也找不到入口。
 * 这里在卡片里挂一个 .ai-task：任务中心深链 + 状态 + 进度条 + 最近一行
 * 日志，每 2.5s 拉一次 /api/task/<tid>，到终态自动停。 */
AI._taskStatusText = function (st) {
  const m = {
    queued: t('ai.task.wait', '已提交，等待进度…'),
    starting: t('ai.task.wait', '已提交，等待进度…'),
    executing: t('ai.task.wait', '已提交，等待进度…'),
    running: t('ai.task.running', '运行中'),
    done: t('ai.task.done', '✓ 已完成'),
    failed: t('ai.task.failed', '✘ 失败'),
    cancelled: t('ai.task.cancelled', '已停止'),
    interrupted: t('ai.task.cancelled', '已停止'),
  };
  return m[st] || st || t('ai.task.running', '运行中');
};

AI._isTaskEnd = function (st) {
  return st === 'done' || st === 'failed' || st === 'cancelled'
      || st === 'interrupted';
};

AI._bindTask = function (card, tid) {
  if (!card || !tid) return;
  tid = String(tid).trim();
  if (!tid) return;
  let box = card.querySelector('.ai-task');
  if (!box) {
    box = document.createElement('div');
    box.className = 'ai-task';
    box.setAttribute('aria-live', 'polite');
    box.innerHTML = `<div class="ai-task-top">`
      + `<a class="ai-task-link" target="_blank" rel="noopener"></a>`
      + `<span class="ai-task-st wait">${esc(t('ai.task.wait', '已提交，等待进度…'))}</span></div>`
      + `<div class="ai-task-bar"><i></i></div>`
      + `<div class="ai-task-msg"></div>`;
    const anchor = card.querySelector(
      '.ai-act-stages, .ai-act-params, .ai-act-reason, .ai-err, .ai-gate-btns');
    card.insertBefore(box, anchor || null);
  }
  const link = box.querySelector('.ai-task-link');
  if (link) {
    link.href = '/tasks?focus=' + encodeURIComponent(tid);
    link.textContent = '🗂 ' + t('ai.task.label', '任务') + ' ' + tid;
    link.title = t('ai.task.openTip', '在任务中心查看日志与产物');
  }
  box.dataset.tid = tid;
  if (box._tid === tid) {
    if (box._timer) return;                     // 同一任务不重复起轮询
    if (AI._isTaskEnd(box._lastSt)) return;      // 已到终态，别反复拉
  } else {
    // 多步计划会换 tid：上一阶段的 done/失败不能挡住下一阶段
    box._tid = tid;
    box._tries = 0;
    box._lastSt = '';
    if (box._timer) { clearInterval(box._timer); box._timer = 0; }
  }
  AI._watchTask(card, box, tid);
};

AI._watchTask = function (card, box, tid) {
  const tick = async function () {
    if (!box.isConnected) {                 // 卡片被清空/换页：停掉，别泄漏定时器
      if (box._timer) { clearInterval(box._timer); box._timer = 0; }
      return;
    }
    let d = null;
    try {
      const r = await fetch('/api/task/' + encodeURIComponent(tid) + '?log_lines=3',
                            {cache: 'no-store'});
      if (r.status === 404) {               // 任务刚提交，任务中心可能还没落盘
        if (++box._tries <= 5) return;
        const stEl = box.querySelector('.ai-task-st');
        if (stEl) {
          stEl.textContent = t('ai.task.missing', '任务不存在或已清理');
          stEl.className = 'ai-task-st bad';
        }
        const barEl = box.querySelector('.ai-task-bar');
        if (barEl) barEl.className = 'ai-task-bar bad';
        box.querySelector('.ai-task-msg').textContent = '';
        if (box._timer) { clearInterval(box._timer); box._timer = 0; }
        return;
      }
      if (!r.ok) return;
      d = await r.json();
    } catch (e) { return; }
    if (!d || !box.isConnected) return;
    box._tries = 0;
    AI._paintTask(card, box, d);
    const st = String(d.status || '');
    if (AI._isTaskEnd(st) && box._timer) {
      clearInterval(box._timer);
      box._timer = 0;
    }
  };
  box._timer = setInterval(tick, 2500);
  tick();                                   // 立刻先给一次，别让卡片空 2.5s
};

AI._paintTask = function (card, box, d) {
  const st = String(d.status || 'running');
  box._lastSt = st;
  const end = AI._isTaskEnd(st);
  const bad = st === 'failed' || st === 'cancelled' || st === 'interrupted';
  const pctNum = Number(d.pct);
  const pct = isFinite(pctNum) && pctNum >= 0
    ? Math.max(0, Math.min(100, Math.round(pctNum * 100))) : null;
  const showPct = pct !== null && pct > 0;      // 排队中 pct=0 别显示「0%」
  const stEl = box.querySelector('.ai-task-st');
  if (stEl) {
    stEl.textContent = AI._taskStatusText(st)
      + (st === 'running' && showPct ? ' · ' + pct + '%' : '');
    stEl.className = 'ai-task-st '
      + (st === 'done' ? 'done' : bad ? 'bad' : 'run');
  }
  const bar = box.querySelector('.ai-task-bar');
  if (bar) {
    bar.className = 'ai-task-bar'
      + (st === 'done' ? ' done' : bad ? ' bad' : '')
      + (!end && !showPct ? ' indet' : '');
    const fill = bar.querySelector('i');
    if (fill) {
      fill.style.width = st === 'done' ? '100%'
        : (showPct ? pct + '%' : (bad ? '0%' : '100%'));
    }
  }
  const msg = box.querySelector('.ai-task-msg');
  if (msg) {
    const bits = [];
    if (d.stage) bits.push(String(d.stage));
    let line = String(d.msg || '').trim();
    if (!line && Array.isArray(d.log) && d.log.length) {
      line = String(d.log[d.log.length - 1] || '')
        .replace(/^\[\d{2}:\d{2}:\d{2}\]\s*/, '').trim();
    }
    if (line) bits.push(line);
    if (bad && d.error) bits.push(String(d.error));
    msg.textContent = bits.join(' · ') || t('ai.task.wait', '已提交，等待进度…');
    msg.title = msg.textContent;
  }
  // 徽章也不要停在「正在启动/已启动」：任务已跑起来就照实显示；
  // 多步计划的「步骤 i/n」优先保留，别被轮询覆盖。
  const b = card && card.querySelector('.ai-gate-st');
  if (b && !/步骤/.test(b.textContent || '')) {
    if (st === 'done') {
      b.textContent = t('ai.act.done', '✓ 完成');
      b.classList.remove('pending', 'bad');
      b.classList.add('done');
    } else if (bad) {
      b.textContent = st === 'failed'
        ? t('ai.task.failed', '✘ 失败')
        : t('ai.task.cancelled', '已停止');
      b.classList.add('bad');
    } else if (st === 'running' && !/运行中/.test(b.textContent || '')) {
      b.textContent = t('ai.act.running', '运行中');
      b.classList.remove('pending', 'bad');
      b.classList.add('done');
    }
  }
};

/* ---------- 主动播报事件流（三层驱动） ----------
 * EventSource 常驻订阅 /api/ai/events；重连自动带 Last-Event-ID 续传。
 * 坞关着时重要事件（AI 播报/失败/待决闸门）计未读、FAB 挂红点。
 *
 * 多标签页（2026-11-02）：HTTP/1.1 同源只有 6 条连接，每页各开一条 SSE 时
 * 开够 6 个标签后所有 fetch（含 /api/ai/chat）一起排队冻死。现在选一个
 * "主"标签页持有唯一一条流（Web Locks 独占锁，标签关掉自动让位），其余
 * 标签通过 BroadcastChannel 收事件，行为与本地直连一致。 */
AI._initBus = function () {
  if (AI._bus || typeof BroadcastChannel === 'undefined') return;
  try { AI._bus = new BroadcastChannel('vp_ai_dock'); } catch (e) { return; }
  AI._bus.onmessage = function (m) {
    const d = m.data || {};
    if (d.kind !== 'ev') {
      AI._onBusCtl(d);
      return;
    }
    const ev = d.ev;
    if (ev.id !== undefined) {
      if (ev.id <= AI.lastEventId) return;      // 与本地水位去重
      AI.lastEventId = ev.id;
      AI._ss.set(AI_EID_KEY, ev.id);
    }
    AI.renderEvent(ev);
  };
};

AI._post = function (obj) {
  if (!AI._bus) return;
  try { AI._bus.postMessage(obj); } catch (e) {}
};

/* 无 Web Locks 的浏览器（Safari < 15.4 等）用「租约」选主，效果与 Web Locks
 * 一致：只留一条 SSE，其余标签收中继。
 *   抢：广播 {kind:'claim'}，等 CLAIM_WAIT 毫秒——期间有人应 {kind:'have'} 就
 *       说明已有主，退为跟随者；没人应就自己当主并每 BEAT_MS 广播心跳。
 *   守：跟随者超过 BEAT_MISS 毫秒没听到心跳（主标签被关掉/崩了），重新抢。
 *   让：pagehide 时广播 {kind:'bye'}，其余标签立刻接手（不必等租约过期）。 */
AI._CLAIM_WAIT = 600;
AI._BEAT_MS = 4000;
AI._BEAT_MISS = 12000;

/* 时间参数读取：业务默认值在上，页面上可用 window.VP_AI_TIMING 覆盖
 * （冒烟测试把等待压到毫秒级，线上不设即为默认）。 */
AI._timing = function (key, dflt, floor) {
  let v = null;
  try {
    const o = (typeof window !== 'undefined' && window.VP_AI_TIMING) || null;
    v = o && o[key] !== undefined ? Number(o[key]) : Number(AI['_' + key]);
  } catch (e) { v = null; }
  if (!isFinite(v) || v <= 0) v = dflt;
  return Math.max(floor, v);
};

AI._onBusCtl = function (d) {
  const kind = d && d.kind;
  if (!kind) return;
  if (kind === 'claim') {
    /* 已经有主就应一声，让抢的人别白当第二个主 */
    if (AI._leader) AI._post({kind: 'have', id: AI._tabId});
    return;
  }
  if (kind === 'have' || kind === 'beat') {
    if (d.id === AI._tabId) return;
    AI._heard = true;
    AI._lastBeat = Date.now();
    /* 两个标签同时启动时可能都以为自己是主（谁也没听见谁）。收到对端的
     * 心跳/应答说明撞车了：id 小的留下，另一个让位——不靠运气收敛。 */
    if (AI._leader && d.id && String(d.id) < String(AI._tabId)) {
      AI._yieldLeader();
    }
    return;
  }
  if (kind === 'bye' && !AI._leader) {
    AI._lastBeat = 0;                 // 主走了：不用等租约过期
    AI._claimLeader();
  }
};

AI._claimLeader = function () {
  if (AI._leader || AI._claiming) return;
  /* 连 BroadcastChannel 都没有（很老的内核）：没有任何跨标签通道可协商，
   * 别白等 CLAIM_WAIT 再当主——直接每页一条，退回单页行为。 */
  if (!AI._bus) { AI._becomeLeader(); return; }
  AI._claiming = true;
  AI._heard = false;
  AI._post({kind: 'claim', id: AI._tabId});
  const wait = AI._timing('CLAIM_WAIT', 600, 0);
  setTimeout(function () {
    AI._claiming = false;
    if (AI._leader) return;
    if (AI._heard) { AI._watchLeader(); return; }   // 已有主：只收中继
    AI._becomeLeader();
    AI._startBeat();
  }, wait);
};

AI._startBeat = function () {
  AI._post({kind: 'beat', id: AI._tabId});
  if (AI._beatTimer) return;
  const every = AI._timing('BEAT_MS', 4000, 20);
  AI._beatTimer = setInterval(function () {
    if (!AI._leader) {
      clearInterval(AI._beatTimer);
      AI._beatTimer = null;
      return;
    }
    AI._post({kind: 'beat', id: AI._tabId});
  }, every);
};

AI._watchLeader = function () {
  if (AI._leader || AI._watchTimer) return;
  AI._lastBeat = AI._lastBeat || Date.now();
  const miss = AI._timing('BEAT_MISS', 12000, 60);
  AI._watchTimer = setInterval(function () {
    if (AI._leader) {
      clearInterval(AI._watchTimer);
      AI._watchTimer = null;
      return;
    }
    if (Date.now() - AI._lastBeat > miss) {
      clearInterval(AI._watchTimer);
      AI._watchTimer = null;
      AI._claimLeader();              // 主标签疑似没了：抢租约
    }
  }, Math.max(20, Math.floor(miss / 3)));
};

AI._openES = function () {
  if (AI._es || typeof EventSource === 'undefined') return;
  const q = AI.lastEventId ? ('?since=' + AI.lastEventId) : '';
  try { AI._es = new EventSource('/api/ai/events' + q); } catch (e) { return; }
  AI._es.onmessage = (m) => {
    let ev = null;
    try { ev = JSON.parse(m.data); } catch (e) { return; }
    if (!ev || ev.id === undefined) return;
    if (ev.id <= AI.lastEventId) return;      // 重连补发去重
    AI.lastEventId = ev.id;
    AI._ss.set(AI_EID_KEY, ev.id);
    if (AI._bus) { try { AI._bus.postMessage({kind: 'ev', ev}); } catch (e) {} }
    AI.renderEvent(ev);
  };
  AI.hydratePending();                        // 建流时也对账一次 pending
};

/* 成为"主"标签页：持锁期间独占事件流；锁被别人持有就只收中继。 */
AI._becomeLeader = function () {
  if (AI._leader || AI._es) return;
  AI._leader = true;
  AI._openES();
};

/* 建流前对齐一次服务端水位（2026-11-02）：
 * ① 服务端环形缓冲重启后 id 归零，而 sessionStorage 里还存着旧的大 id，
 *    `?since=<大id>` 会一条都取不到、后续新事件又被本地去重过滤掉 →
 *    整个坞从此静默。这里用一次 JSON 快照比对最大值，发现回退就归零。
 * ② 快照同时带回待决闸门：刷新页面后闸门卡若已丢失，流水线会在后台阻塞
 *    到超时——补渲染出来，让人能立刻拍板。 */
AI._syncWatermark = async function () {
  let d = null;
  try {
    const r = await fetch('/api/ai/events?since=0');
    if (r.ok) d = await r.json();
  } catch (e) { return; }
  if (!d) return;
  const evs = Array.isArray(d.events) ? d.events : [];
  const maxId = evs.reduce((m, e) => Math.max(m, Number(e.id) || 0), 0);
  if (AI.lastEventId > maxId) {          // 服务端重启过，水位回退
    AI.lastEventId = 0;
    AI._ss.set(AI_EID_KEY, 0);
  }
  const gates = Array.isArray(d.pending_gates) ? d.pending_gates : [];
  gates.forEach((g) => {
    const key = `${g.sample}|${g.stage}`;
    const has = [...document.querySelectorAll('.ai-gate')]
      .some(n => n.dataset.gate === key && n.querySelector('.ai-gate-st.pending'));
    if (has) return;
    AI.renderEvent({
      type: 'ai_gate', status: 'pending', sample: g.sample, stage: g.stage,
      name: g.stage, deadline: g.deadline, backlog: false,
      text: t('ai.ev.gateRestored',
        '（这个闸门在你刷新页面前弹出，仍在等待决定；原始评估内容见上方播报）'),
    });
  });
};

AI._tabId = AI._tabId || ('t' + Math.random().toString(36).slice(2, 10));

AI._elect = function () {
  const locks = (typeof navigator !== 'undefined' && navigator.locks
                 && navigator.locks.request) ? navigator.locks : null;
  if (!locks) { AI._claimLeader(); return; }    // 老浏览器：租约选主（同效）
  locks.request('vp_ai_sse_leader', () => new Promise((resolve) => {
    /* 锁在页面卸载/让位时通过 resolve 释放；持锁期间本页唯一建流方。 */
    AI._lockRelease = resolve;
    AI._becomeLeader();
  })).catch(() => { AI._leader = false; AI._claimLeader(); });
};

AI.startEvents = function () {
  if (AI._es || AI._leader) return;
  AI._initBus();
  /* 先对齐水位与待决闸门，再决定谁建流（多一次轻量 GET，换来重启后
     不会整坞静默 + 刷新后闸门不丢）。 */
  AI._syncWatermark().then(AI._elect, AI._elect);
};

AI._releaseLeader = function () {
  if (AI._es) { AI._es.close(); AI._es = null; }
  if (AI._leader) AI._post({kind: 'bye', id: AI._tabId});
  AI._leader = false;
  if (AI._beatTimer) { clearInterval(AI._beatTimer); AI._beatTimer = null; }
  if (AI._watchTimer) { clearInterval(AI._watchTimer); AI._watchTimer = null; }
  const r = AI._lockRelease;
  AI._lockRelease = null;
  if (r) { try { r(); } catch (e) {} }      // 让位：下一个标签页接管
};

/* 撞车让位（2026-11-02）：两个标签页同时冷启动时，谁都还没听见对方的 claim
 * 就各自当了主 → 各开一条 SSE，HTTP/1.1 同源 6 连接上限会被吃满。收到对端
 * 心跳/应答时按 tabId 字典序收敛，id 小的留任；这里安静退位，**不广播 bye**
 * （bye 是"页面走了"，广播出去会让对端以为主没了，又抢一轮）。 */
AI._yieldLeader = function () {
  if (!AI._leader) return;
  if (AI._es) { AI._es.close(); AI._es = null; }
  AI._leader = false;
  AI._claiming = false;
  if (AI._beatTimer) { clearInterval(AI._beatTimer); AI._beatTimer = null; }
  AI._lastBeat = Date.now();
  AI._watchLeader();                       // 转成跟随者，靠对端心跳续命
};

/* 离开页面时显式关掉 SSE：服务端 SSE 生成器没有客户端断连检测（写心跳到
 * 半关连接永远不会失败），导航后若不主动 close，旧连接的套接字两头都不释放
 * ——每次导航泄漏 ~2 条 ESTABLISHED，攒到 ~6 条（HTTP/1.1 同主机上限）后
 * 新 fetch 全部排队冻死（实测 2026-10-11：连续 6 页后 /api/samples 5s 超时、
 * kvsuite 选样弹窗打不开）。pagehide 在导航/刷新时都会触发，比 unload 可靠。 */
window.addEventListener('pagehide', function () {
  AI._releaseLeader();
  /* 离开页面前落盘一次（2026-11-02）：流式回答可能还没来得及被 2s 定时器
   * 写进 localStorage（尤其是刚发出去就切模块），这里补最后一次。 */
  try { if (AI.messages.length) AI._saveHistory(); } catch (e) { /* 静默 */ }
});
/* 从 bfcache 回来（前进/后退）时 pagehide 已经关过流，pageshow 不会再建，
 * 于是闸门/执行单/播报全部静默失灵——重新建流并按 lastEventId 续传。 */
window.addEventListener('pageshow', function (e) {
  if (e && e.persisted) AI.startEvents();
});

AI.clearUnread = function () {
  AI.unread = 0;
  AI.lastSeenId = Math.max(AI.lastSeenId, AI.lastEventId);
  try { localStorage.setItem(AI_SEEN_KEY, String(AI.lastSeenId)); } catch (e) {}
  const b = $('aiFabBadge');
  if (b) { b.hidden = true; b.textContent = ''; }
  AI._titleSync();
};

AI._bumpUnread = function (ev) {
  if (AI.open) return;
  if (ev && ev.id <= AI.lastSeenId) return;   // 上一会话已看过（刷新重放）
  AI.unread += 1;
  const b = $('aiFabBadge');
  if (b) { b.hidden = false; b.textContent = AI.unread > 9 ? '9+' : String(AI.unread); }
  AI._titleSync();
};

// 浏览器标签页标题带未读数：用户在别的标签页也能看到有 AI 事件
AI._titleSync = function () {
  try {
    if (AI._title0 === null) AI._title0 = document.title.replace(/^\(\d+\)\s*/, '');
    document.title = AI.unread > 0 ? `(${AI.unread}) ${AI._title0}` : AI._title0;
  } catch (e) { /* 标题不可写则静默 */ }
};

/* 追加渲染目标：默认对话区；历史回放时临时指向折叠容器（见 _foldBacklog） */
AI._appendTo = null;
AI._append = function (n) {
  const box = AI._appendTo || $('aiMsgs');
  if (box) box.appendChild(n);
};

/* 历史回放折叠（2026-11-02）：刷新时服务端的环形缓冲会从头补发一遍旧事件，
 * 以前每页水位从 0 起，坞里当场刷出一堆旧卡、顺序还压在历史对话后面。
 * 现在除"待决闸门/待批执行单"（必须马上处理）外，一律折进一张可展开的
 * 「此前会话的 N 条事件」卡片，需要时才展开。 */
AI._foldBacklog = function (ev) {
  if (!AI._backlog) {
    const d = document.createElement('details');
    d.className = 'ai-backlog';
    d.innerHTML = '<summary></summary><div class="ai-backlog-body"></div>';
    const m = $('aiMsgs');
    if (m) m.insertBefore(d, m.firstChild);
    AI._backlog = d;
  }
  AI._backlogN += 1;
  const sum = AI._backlog.querySelector('summary');
  if (sum) {
    sum.textContent = t('ai.dock.backlogTpl', '此前会话的 {n} 条事件（点击展开）')
      .replace('{n}', String(AI._backlogN));
  }
  const body = AI._backlog.querySelector('.ai-backlog-body');
  const keep = AI._appendTo;
  AI._appendTo = body;
  try {
    AI.renderEvent(Object.assign({}, ev, {backlog: false, _folded: true}));
  } finally { AI._appendTo = keep; }
};

AI.renderEvent = function (ev) {
  if (!ev) return;
  /* 回放事件折叠：待决闸门与待批执行单必须可见，其余折进块里 */
  const mustShow = (ev.type === 'ai_gate' && ev.status === 'pending')
                || (ev.type === 'ai_action' && ev.status === 'pending');
  if (ev.backlog && !mustShow) { AI._foldBacklog(ev); return; }
  const s = ev.sample ? ` · ${ev.sample}` : '';
  if (ev.type === 'stage_start') {
    const n = document.createElement('div');
    n.className = 'ai-event ai-ev-start';
    n.innerHTML = `📖 <b>${esc(ev.name || ev.stage)}</b>${esc(s)}`
      + `<div class="ai-ev-note">${esc(ev.note || '')}</div>`;
    AI._append(n);
    _scroll();
  } else if (ev.type === 'stage_done') {
    const n = document.createElement('div');
    n.className = 'ai-event ai-ev-done';
    n.textContent = `✔ ${ev.name || ev.stage} 完成${s}`;
    AI._append(n);
    /* 这一阶段的产物（图/表/交互页）随手可点：服务端按阶段目录挑好送过来。
     * 与 run_done 同处理——卡片另起一屏宽气泡，不挤在居中的完成条里。 */
    if (ev.artifacts && ev.artifacts.length) {
      const sbox = document.createElement('div');
      sbox.className = 'ai-msg ai-bot ai-arts-msg';
      try { AI.renderArts(sbox, ev.artifacts); } catch (e) { /* 忽略 */ }
      AI._append(sbox);
    }
    _scroll();
  } else if (ev.type === 'ai_read') {
    AI._bumpUnread(ev);
    const n = document.createElement('div');
    n.className = 'ai-msg ai-bot ai-read';
    n.innerHTML = `<div class="ai-read-head">✨ ${esc(t('ai.ev.read', 'AI 播报'))}`
      + ` · ${esc(ev.name || ev.stage || '')}</div>`
      + (ev.failure ? `<div class="ai-read-fail">✘ ${esc(t('ai.ev.failed', '阶段失败诊断'))}</div>` : '')
      + `<div class="ai-read-body">${aiFmt(ev.text || '')}</div>`;
    /* 播报正文里提到的产物也做成卡片（服务端已解析，越界路径不会来） */
    if (ev.artifacts && ev.artifacts.length) {
      try { AI.renderArts(n, ev.artifacts); } catch (e) { /* 忽略 */ }
    }
    AI._append(n);
    _scroll();
  } else if (ev.type === 'stage_failed') {
    AI._bumpUnread(ev);
    const n = document.createElement('div');
    n.className = 'ai-event ai-ev-fail';
    n.innerHTML = `✘ <b>${esc(ev.name || ev.stage)}</b> ${esc(t('ai.ev.failed', '阶段失败'))}${esc(s)}`
      + (ev.error ? `<div class="ai-ev-note">${esc(ev.error)}</div>` : '');
    AI._append(n);
    _scroll();
  } else if (ev.type === 'run_done') {
    const n = document.createElement('div');
    n.className = 'ai-event ai-ev-done ai-ev-run';
    n.textContent = `🏁 ${t('ai.ev.runDone', '本轮分析全部完成')}${s}`;
    AI._append(n);
    /* 跑完就该能直接点开：报告/漏斗图/进化树/基因组图（服务端算好随事件送来）。
     * 卡片单独一屏宽的气泡，不塞进居中那条绿色完成条里。 */
    if (ev.artifacts && ev.artifacts.length) {
      const box = document.createElement('div');
      box.className = 'ai-msg ai-bot ai-arts-msg';
      try { AI.renderArts(box, ev.artifacts); } catch (e) { /* 忽略 */ }
      AI._append(box);
    }
    _scroll();
  } else if (ev.type === 'ai_gate') {
    AI._bumpUnread(ev);
    // 闸门会阻塞流水线：待决时自动弹出对话坞（唯一自动开坞的事件，
    // 必须有人拍板）。历史回放（backlog）不弹——刷新页面时旧闸门
    // 已决或已超时，弹坞纯属打扰。
    // 子进程回传的闸门（relay）也不弹：决策要在**发起进程**里落，
    // 网页端点了也传不回去，只能等它自己超时。
    const relayed = !!ev.relay;
    if (ev.status === 'pending' && !AI.open && !ev.backlog && !relayed) AI.toggle(true);
    const stTxt = {pending: t('ai.ev.gatePending', '等待你的决定'),
                   continue: t('ai.ev.gateContinue', '已确认继续'),
                   stop: t('ai.ev.gateStop', '已终止任务'),
                   timeout: t('ai.ev.gateTimeout', '超时自动继续')}[ev.status]
                || ev.status;
    // 同一样品同一阶段已有卡片：原地翻转状态（按钮移除、徽章更新），不再追加
    const old = [...document.querySelectorAll('.ai-gate')]
      .find(n => n.dataset.gate === `${ev.sample}|${ev.stage}`
                 && n.querySelector('.ai-gate-st.pending'));
    if (old && ev.status !== 'pending') {
      AI._flipGateCard(old, stTxt);
      return;
    }
    const pending = ev.status === 'pending';
    const n = document.createElement('div');
    n.className = 'ai-msg ai-bot ai-gate';
    n.dataset.gate = `${ev.sample}|${ev.stage}`;
    n.innerHTML = `🚦 <b>${esc(t('ai.ev.gate', 'AI 决策闸门'))} · ${esc(ev.name || ev.stage)}</b>${esc(s)}`
      + `<span class="ai-gate-st ${pending ? 'pending' : 'done'}">${esc(stTxt)}</span>`
      + `<div class="ai-read-body">${aiFmt(ev.text || '')}</div>`
      + (pending && relayed
         ? `<div class="ai-gate-note">${esc(t('ai.ev.gateRelayNote',
              '该任务由对话坞发起、跑在 MCP 子进程里：决策按钮在这里无效，'
              + '闸门会按超时自动继续；要中止请到任务中心停止该任务。'))}</div>`
         : pending ? `<div class="ai-gate-btns">`
         + `<button class="btn small primary" data-ai-act="gate"`
         + ` data-sample="${esc(ev.sample || '')}" data-stage="${esc(ev.stage || '')}"`
         + ` data-decision="continue">${esc(t('ai.ev.gateBtnGo', '▶ 继续下一阶段'))}</button>`
         + `<button class="btn small danger" data-ai-act="gate"`
         + ` data-sample="${esc(ev.sample || '')}" data-stage="${esc(ev.stage || '')}"`
         + ` data-decision="stop">${esc(t('ai.ev.gateBtnStop', '■ 终止任务'))}</button>`
         + `</div><span class="ai-gate-cd"></span>` : '');
    AI._append(n);
    if (pending && !relayed) AI._startCountdown(n, ev.deadline);
    _scroll();
  } else if (ev.type === 'ai_action') {
    AI._bumpUnread(ev);
    // 执行单不打扰（不自动弹坞）——它不阻塞流水线，红点足够
    const stMap = {pending: t('ai.act.pending', '待你批准'),
                   executing: t('ai.act.executing', '正在启动'),
                   running: t('ai.act.running', '运行中'),
                   launched: t('ai.act.launched', '✓ 已启动运行'),
                   done: t('ai.act.done', '✓ 完成'),
                   rejected: t('ai.act.rejected', '已驳回'),
                   failed: t('ai.act.failed', '启动失败')};
    let stTxt = stMap[ev.status] || ev.status;
    // create_sample 是同步动作：后端返回样品名而不是任务 id（2026-11-02），
    // 卡片上必须写清建了哪个样品，否则「✓ 完成」等于什么都没说。
    if (ev.status === 'done' && ev.result_sample) {
      stTxt += ' · ' + t('ai.act.sampleTpl', '样品 {s}')
        .replace('{s}', String(ev.result_sample));
    }
    // 多步计划：状态里带步骤进度（2/3 · 运行工具）
    if (ev.action === 'plan' && ev.step && ev.step_total) {
      stTxt = t('ai.act.stepTpl', '步骤 {i}/{n}')
        .replace('{i}', ev.step).replace('{n}', ev.step_total)
        + (ev.step_title ? ' · ' + ev.step_title : '');
      if (ev.status === 'done') stTxt = t('ai.act.done', '✓ 完成') + ' · ' + stTxt;
    }
    // 同一执行单已有卡片：原地翻转（不再要求旧卡片仍是 pending ——
    // 计划执行期间会多次上报 running，旧写法会每步新建一张卡刷屏）。
    // 2026-11-04：pending 重复事件也不再新建卡（下一步建议先建卡、事件流
    // 稍后补发同一条 pending 时，避免出现两张一模一样的执行单）。
    const oldA = [...document.querySelectorAll('.ai-action')]
      .find(n => n.dataset.aid === ev.aid);
    if (oldA) {
      if (ev.status !== 'pending') {
        AI._flipActionCard(oldA, ev.status, ev.error, stTxt, ev);
      }
      return;
    }
    const pendingA = ev.status === 'pending';
    const n = document.createElement('div');
    n.className = 'ai-msg ai-bot ai-action';
    n.dataset.aid = ev.aid;
    // 参数 diff：与上一轮基准不同的键高亮并加 *
    const base = ev.base_params || {};
    const paramsHtml = Object.entries(ev.params || {}).map(([k, v]) => {
      const changed = String(base[k]) !== String(v);
      return `<span class="ai-param ${changed ? 'changed' : ''}">`
        + `${esc(k)}=${esc(v)}${changed ? ' *' : ''}</span>`;
    }).join(' ') || `<span class="ai-hint">${esc(t('ai.act.sameParams', '（沿用上一轮参数）'))}</span>`;
    // 多步计划：把步骤清单列出来，批准前就能看清会跑什么
    const stepLine = (ev.action === 'plan' && Array.isArray(ev.steps))
      ? `<div class="ai-act-stages">📑 ${ev.steps.map((s, i) => {
          const bits = [String(i + 1) + '. ' + (s.action || '')];
          if (s.sample) bits.push(s.sample);
          if (s.params && s.params.tool) bits.push(s.params.tool);
          if (s.stages && s.stages.length) bits.push(s.stages.join('→'));
          return esc(bits.join(' · '));
        }).join('  →  ')}</div>`
      : '';
    n.innerHTML = `🔧 <b>${esc(t('ai.act.title', 'AI 执行单'))} · ${esc(ev.title || ev.action)}</b>${esc(ev.sample ? ' · ' + ev.sample : '')}`
      + `<span class="ai-gate-st ${pendingA ? 'pending' : 'done'}">${esc(stTxt)}</span>`
      + stepLine
      // 待批卡片的阶段行改成复选框组（默认全选 = AI 当初的选择），批准时可
      // 取消个别阶段；已拍板的卡片是历史记录，保持只读文本。外层复用
      // .ai-act-stages（间距/字号）与设置页现成的 .ai-stagegrid/.ai-stagechk
      // 样式，不为这一处新增 CSS。
      + (ev.stages && ev.stages.length
         ? (pendingA
            ? `<div class="ai-act-stages ai-act-stage-pick ai-stagegrid">📑 `
              + (ev.stages || []).map(s =>
                  `<label class="ai-stagechk"><input type="checkbox" checked`
                  + ` value="${esc(s)}">${esc(s)}</label>`).join('')
              + `</div>`
            : `<div class="ai-act-stages">📑 ${esc((ev.stages || []).join(' → '))}</div>`)
         : '')
      + (ev.action === 'download_runs'
         ? `<div class="ai-act-stages">⬇ ${esc((ev.runs || []).slice(0, 4).join(', '))}${((ev.runs || []).length > 4) ? ' …' : ''}（共 ${esc(String((ev.params || {}).runs_n || (ev.runs || []).length))} 条 run → 平台下载队列）</div>`
         : (ev.action === 'plan' ? '' : `<div class="ai-act-params">${paramsHtml}</div>`))
      + (ev.reason ? `<div class="ai-act-reason">${aiFmt(ev.reason)}</div>` : '')
      + (pendingA ? `<div class="ai-gate-btns">`
         + `<button class="btn small primary" data-ai-act="action"`
         + ` data-aid="${esc(ev.aid)}" data-decision="approve">`
         + `${esc(t('ai.act.approve', '✓ 批准执行'))}</button>`
         + `<button class="btn small danger" data-ai-act="action"`
         + ` data-aid="${esc(ev.aid)}" data-decision="reject">`
         + `${esc(t('ai.act.reject', '✗ 驳回'))}</button>`
         + `</div>` : '');
    if (ev.status === 'done' && ev.artifacts && ev.artifacts.length) {
      try { AI.renderArts(n, ev.artifacts); } catch (e) { /* 忽略 */ }
    }
    AI._append(n);
    if (!pendingA && ev.tid) AI._bindTask(n, ev.tid);
    AI._seenAction[ev.aid] = ev.status;
    _scroll();
  }
};

/* 卡片状态原地翻转（事件流与 /api/ai/actions 对账两条入口共用） */
AI._flipActionCard = function (node, status, err, stTxt, ev) {
  const stMap = {pending: t('ai.act.pending', '待你批准'),
                 executing: t('ai.act.executing', '正在启动'),
                 running: t('ai.act.running', '运行中'),
                 launched: t('ai.act.launched', '✓ 已启动运行'),
                 done: t('ai.act.done', '✓ 完成'),
                 rejected: t('ai.act.rejected', '已驳回'),
                 interrupted: t('ai.act.interrupted', '已中断'),
                 failed: t('ai.act.failed', '失败')};
  if (!node) return;
  // create_sample 走事件流翻转时同样要带样品名（与上面 ai_action 分支同口径）
  if (!stTxt && status === 'done' && ev && ev.result_sample) {
    stTxt = t('ai.act.done', '✓ 完成') + ' · '
      + t('ai.act.sampleTpl', '样品 {s}').replace('{s}', String(ev.result_sample));
  }
  const b = node.querySelector('.ai-gate-st');
  if (b) {
    b.textContent = stTxt || stMap[status] || status;
    b.classList.remove('pending', 'bad');
    b.classList.add('done');
    if (status === 'failed') b.classList.add('bad');
  }
  const btns = node.querySelector('.ai-gate-btns');
  if (btns) btns.remove();
  /* 阶段勾选框是「待批」专用的：翻成已决后换回只读文本（留着勾选框会让人
     以为还能再改）。优先用事件里的实际阶段，拿不到就取卡上当前勾中的值。 */
  const pick = node.querySelector('.ai-act-stage-pick');
  if (pick) {
    const stg = (ev && Array.isArray(ev.stages) && ev.stages.length)
      ? ev.stages
      : Array.prototype.map.call(pick.querySelectorAll('input:checked'), i => i.value);
    if (stg.length) {
      const ro = document.createElement('div');
      ro.className = 'ai-act-stages';
      ro.textContent = '📑 ' + stg.join(' → ');
      pick.replaceWith(ro);
    } else {
      pick.remove();
    }
  }
  // 后端只推「已启动」：把 tid 挂上，卡片自己轮询任务进度
  if (ev && ev.tid && status !== 'rejected') AI._bindTask(node, ev.tid);
  /* 跑完了就把产物挂进来（launched/running → done 的翻转路径） */
  if (status === 'done' && ev && ev.artifacts && ev.artifacts.length
      && !node.querySelector('.ai-arts')) {
    try { AI.renderArts(node, ev.artifacts); } catch (e) { /* 忽略 */ }
  }
  if (err && !node.querySelector('.ai-err')) {
    const e = document.createElement('div');
    e.className = 'ai-err';
    e.textContent = err;
    node.appendChild(e);
  }
  AI._stopCountdown(node);
  AI._seenAction[node.dataset.aid] = status;
  _scroll();
};

AI._flipGateCard = function (node, stTxt) {
  if (!node) return;
  const b = node.querySelector('.ai-gate-st');
  if (b) {
    b.textContent = stTxt;
    b.classList.remove('pending');
    b.classList.add('done');
  }
  const btns = node.querySelector('.ai-gate-btns');
  if (btns) btns.remove();
  /* 已拍板的卡片上不该再挂着「⏳ 剩余 00:58（超时自动继续）」——
     _stopCountdown 只停定时器，字还留在那儿（2026-11-02）。 */
  const cd = node.querySelector('.ai-gate-cd');
  if (cd) cd.hidden = true;
  AI._stopCountdown(node);
  _scroll();
};

/* 闸门倒计时（后端在 pending 事件里给 deadline = epoch 秒；超时自动继续） */
AI._startCountdown = function (node, deadline) {
  const el = node && node.querySelector('.ai-gate-cd');
  if (!el) return;
  const end = Number(deadline) * 1000;
  if (!isFinite(end) || !end) { el.hidden = true; return; }
  const tick = () => {
    const left = Math.max(0, Math.round((end - Date.now()) / 1000));
    const mm = String(Math.floor(left / 60)).padStart(2, '0');
    const ss = String(left % 60).padStart(2, '0');
    el.textContent = left > 0
      ? t('ai.ev.gateCd', '⏳ 剩余 {t}（超时自动继续）').replace('{t}', mm + ':' + ss)
      : t('ai.ev.gateCdOver', '⏳ 已到期限，正在自动继续…');
    if (left <= 0) AI._stopCountdown(node);
  };
  AI._stopCountdown(node);
  node._cdTimer = setInterval(tick, 1000);
  tick();
};

AI._stopCountdown = function (node) {
  if (node && node._cdTimer) { clearInterval(node._cdTimer); node._cdTimer = 0; }
};

AI.action = async function (aid, decision) {
  // 先本地置灰（乐观更新），结果状态由事件流原地翻转。
  // 2026-11-02：以前失败只在抛异常时恢复按钮、catch 分支忘了恢复，服务
  // 重启/断网的瞬间点一下按钮就永久灰掉、卡里还没有任何错误。这里统一
  // 失败恢复 + 卡内报错；成功但 4 秒内没等到状态翻转也把按钮放回可点。
  const cards = () => Array.prototype.slice
    .call(document.querySelectorAll('.ai-action'))
    .filter(n => n.dataset.aid === aid);
  const paint = (disabled) => cards().forEach(n =>
    n.querySelectorAll('.ai-gate-btns button').forEach(b => b.disabled = disabled));
  paint(true);
  const fail = (msg) => {
    if (msg) AI.sysNote(`⚠ ${msg}`);
    cards().forEach(n => {
      n.querySelectorAll('.ai-gate-btns button').forEach(b => b.disabled = false);
      if (msg && !n.querySelector('.ai-err')) {
        const e = document.createElement('div');
        e.className = 'ai-err';
        e.textContent = msg;
        n.appendChild(e);
      }
    });
  };
  // 阶段勾选只对 approve 有意义：取第一张带复选框的卡片（同 aid 可能有多张
  // 卡片，历史卡没有勾选框）；全部勾着 = 用户没改，请求不带 stages 走老行为，
  // 后端仍按执行单原始阶段执行。
  let stages = null;
  if (decision === 'approve') {
    const pick = cards().map(n => n.querySelector('.ai-act-stage-pick'))
      .find(Boolean);
    if (pick) {
      const all = pick.querySelectorAll('input[type=checkbox]');
      const on = pick.querySelectorAll('input[type=checkbox]:checked');
      const vals = Array.prototype.map.call(on, i => i.value);
      // 全部取消 = 没有阶段可跑：不发请求，卡内报错（fail 会恢复按钮）
      if (!vals.length) return fail(t('ai.act.stageMin', '至少保留一个阶段'));
      if (vals.length !== all.length) stages = vals;
    }
  }
  try {
    const body = {id: aid, decision};
    if (stages) body.stages = stages;
    const r = await fetch('/api/ai/actions/resolve', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body)});
    if (!r.ok) {
      let msg = `HTTP ${r.status}`;
      try { msg = (await r.json()).error || msg; } catch (e) {}
      return fail(msg);
    }
    // 成功：响应里已带 tid 就先挂任务进度（SSE 慢/断线也不会只剩一个徽章）
    let rec = null;
    try { rec = (await r.json()).action || null; } catch (e) {}
    if (rec && rec.status && rec.status !== 'pending') {
      if (rec.tid) cards().forEach(n => AI._bindTask(n, rec.tid, true));
      cards().forEach(n => AI._flipActionCard(n, rec.status, rec.error, null, rec));
    }
    // 等事件流把卡片翻成最终状态；SSE 若断了，别让按钮永远灰着
    setTimeout(() => cards().forEach(n => {
      if (n.querySelector('.ai-gate-st.pending')) paint(false);
    }), 4000);
  } catch (e) { fail(String(e)); }
};

AI.gate = async function (sample, stage, action) {
  // 同一样品同一阶段的旧闸门卡片立即置灰（新状态卡随后由事件流更新）
  const cards = () => Array.prototype.slice
    .call(document.querySelectorAll('.ai-gate'))
    .filter(n => n.dataset.gate === `${sample}|${stage}`);
  const paint = (disabled) => cards().forEach(n =>
    n.querySelectorAll('.ai-gate-btns button').forEach(b => b.disabled = disabled));
  paint(true);
  const fail = (msg) => {
    if (msg) AI.sysNote(`⚠ ${msg}`);
    paint(false);
  };
  try {
    const r = await fetch('/api/ai/gate', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({sample, stage, action})});
    if (!r.ok) {
      let msg = `HTTP ${r.status}`;
      try { msg = (await r.json()).error || msg; } catch (e) {}
      return fail(msg);
    }
    setTimeout(() => cards().forEach(n => {
      if (n.querySelector('.ai-gate-st.pending')) paint(false);
    }), 4000);
  } catch (e) { fail(String(e)); }
};

/* 上下文只加载一次；并发调用（开坞 + 立刻发送）合流到同一个 promise，
 * 否则可能两次 fetch 都跑 _restoreHistory，历史被渲染两遍。 */
AI.ensureCtx = function () {
  if (AI._ctxP) return AI._ctxP;
  AI._ctxP = AI._ensureCtx().catch(() => {}).then((r) => {
    AI._ctxP = null;
    return r;
  });
  return AI._ctxP;
};

/* 配置变更后的即时对齐（2026-11-03）
 * 设置页与对话坞在同一个页面里（_nav.html 的坞 + settings 页），但坞的就绪
 * 态只在页面加载时取一次：用户保存完 AI 设置，切回坞仍是「尚未配置」，
 * 发消息直接报「AI 助手未配置完整」，只能手动刷新页面——这就是
 * 「我明明设置好了还是不行 / 还得刷新」的直接原因。
 * 设置页保存成功后调用本函数；切回本标签页时也自动对齐一次（别的窗口改了
 * 配置同样能跟上）。 */
AI.refreshReady = async function () {
  try {
    const q = AI.sample ? `?sample=${encodeURIComponent(AI.sample)}` : '';
    const d = await (await fetch('/api/ai/context' + q,
                                {cache: 'no-store'})).json();
    AI.ready = !!d.ready;
    const dot = $('aiReadyDot');
    if (dot) {
      dot.hidden = false;
      dot.classList.toggle('ok', AI.ready);
      dot.classList.toggle('no', !AI.ready);
    }
    if (AI.ready) {
      // 早先压进去的「尚未配置」提示已过期，留着会继续误导用户
      document.querySelectorAll('#aiMsgs .ai-notice').forEach(n => n.remove());
    }
    try { await AI.loadModels(); } catch (e) { /* 下拉刷新失败不影响对话 */ }
    return AI.ready;
  } catch (e) {
    return AI.ready;             // 网络抖动时保持原状态，不误报
  }
};

document.addEventListener('visibilitychange', function () {
  if (!document.hidden && AI.ctxLoaded && AI.refreshReady) AI.refreshReady();
});

/* 历史恢复只做一次（2026-11-02）：与网络解耦。
 * 以前恢复写在 /api/ai/context 成功后的分支里，服务端在跑重活（批量分析、
 * 大批量下载）时该请求会排队/超时 → 历史一条都不渲染，用户看到的就是
 * 「问答又没了」。现在页面一加载就先读 localStorage 恢复，上下文接口只决定
 * 就绪徽标。 */
AI.restoreOnce = function () {
  if (AI._restored !== undefined) return AI._restored;
  let ok = false;
  try { ok = AI._restoreHistory(); } catch (e) { ok = false; }
  AI._restored = !!ok;
  if (!ok) AI.greet();
  return AI._restored;
};

AI._ensureCtx = async function () {
  // 先恢复，再问上下文（见 restoreOnce 注释）
  try { AI.restoreOnce(); } catch (e) { /* 恢复失败不阻塞上下文 */ }
  AI._serverTopUp();   // 异步核对服务端留存：换浏览器/清缓存后也能找回会话
  try {
    // 样品跟随顶栏全局胶囊（2026-10-30 起坞里不再有独立样品下拉）
    AI.sample = (window.VP_CTX && VP_CTX.sample) || '';
    const q = AI.sample ? `?sample=${encodeURIComponent(AI.sample)}` : '';
    const d = await (await fetch('/api/ai/context' + q)).json();
    AI.ready = !!d.ready;
    $('aiReadyDot').hidden = false;
    $('aiReadyDot').classList.toggle('ok', AI.ready);
    $('aiReadyDot').classList.toggle('no', !AI.ready);
    /* 快捷提示按上下文重画（2026-11-02）：样品数/在跑/失败数都来自这次 ctx，
       拿不到（接口失败）就沿用上一次的形状，不阻塞对话。 */
    const tasks = Array.isArray(d.tasks) ? d.tasks : [];
    AI._ctx = {
      samples: (Array.isArray(d.samples) ? d.samples : []).length,
      running: tasks.filter(x => x && (x.status === 'running' || x.status === 'queued')).length,
      failed: tasks.filter(x => x && x.status === 'failed').length,
    };
    try { AI.renderChips(); } catch (e) { /* 提示词渲染失败不影响对话 */ }
    if (!AI.ctxLoaded) {
      AI.ctxLoaded = true;
      if (!AI.ready) AI.notice(t('ai.dock.notice',
        'AI 工作台尚未配置：请到 设置 → AI 工作台 选择提供方并填写 API Key'
        + '（本地 Ollama 可完全离线使用）。'));
      // 兜底：上面若因异常没恢复成（例如 DOM 还没就绪），这里再试一次
      try { AI.restoreOnce(); } catch (e) { /* 静默 */ }
    }
  } catch (e) {
    AI.notice(t('ai.dock.loadFail', '上下文加载失败') + ': ' + e);
    try { AI.restoreOnce(); } catch (e2) { /* 静默 */ }
  }
};

/* ---------- 任务分解三态开关（2026-11-03 用户定案「任务分解可选」） ----------
 * auto（默认）= 模型按请求复杂度自判；off = 后端从工具契约物理移除 plan 动作
 * （强制单步）；always = 多阶段链路优先拆计划。存 localStorage，随每轮上送。 */
AI.planMode = (function () {
  try { return localStorage.getItem('vp_plan_mode') || 'auto'; }
  catch (e) { return 'auto'; }
})();

AI.cyclePlanMode = function () {
  const order = ['auto', 'off', 'always'];
  AI.planMode = order[(order.indexOf(AI.planMode) + 1) % order.length];
  try { localStorage.setItem('vp_plan_mode', AI.planMode); } catch (e) {}
  AI.paintPlanMode();
  const label = {auto: '模型自判', off: '只做单步（已禁用多步计划）',
                 always: '优先拆多步计划'}[AI.planMode];
  AI.sysNote('🧩 ' + t('ai.dock.planSet', '任务分解：') + label);
};

AI.paintPlanMode = function () {
  const btn = $('aiPlanBtn');
  if (!btn) return;
  const mark = {auto: '', off: '🚫', always: '🔁'}[AI.planMode] || '';
  btn.textContent = '🧩' + (AI.planMode === 'auto' ? '' : mark);
  btn.style.opacity = AI.planMode === 'auto' ? '' : '1';
  btn.title = {auto: '任务分解：auto=模型自判 / off=只单步 / always=优先拆计划',
               off: '任务分解：已关闭（只做单步）——点击切换',
               always: '任务分解：优先拆多步计划——点击切换'}[AI.planMode]
    || btn.title;
};

/* 初始着色（坞在每页注入，DOMContentLoaded 前脚本已就绪） */
try { AI.paintPlanMode(); } catch (e) { /* DOM 未就绪时坞打开会再画 */ }
document.addEventListener('DOMContentLoaded', () => {
  try { AI.paintPlanMode(); } catch (e) {}
});

/* ---------- 会话导出 / 重试上一问（2026-11-03 AI 工作台扩展） ---------- */

/* 当前会话 → Markdown 下载。产物路径原样保留（点开即卡片同源）。 */
AI.exportConv = function () {
  if (!AI.messages.length) {
    AI.sysNote(t('ai.dock.exportEmpty', '当前会话还没有内容可导出'));
    return;
  }
  const L = [];
  L.push('# ' + t('ai.dock.exportTitle', 'AI 工作台会话导出'));
  L.push('');
  L.push('- ' + t('ai.dock.exportAt', '导出时间') + ': '
    + new Date().toLocaleString());
  if (AI.sample) L.push('- ' + t('gx.sample', '样品') + ': ' + AI.sample);
  if (AI.planMode && AI.planMode !== 'auto') {
    L.push('- ' + t('ai.dock.planSet', '任务分解') + ': ' + AI.planMode);
  }
  L.push('');
  L.push('---');
  for (const m of AI.messages) {
    L.push('');
    L.push(m.role === 'user' ? '## 🙋 ' + t('ai.dock.user', '用户')
                             : '## 🤖 ' + t('ai.dock.assistant', 'AI'));
    L.push('');
    L.push(String(m.content || ''));
    if (Array.isArray(m.artifacts) && m.artifacts.length) {
      L.push('');
      L.push(t('ai.dock.exportArtifacts', '**产物**：'));
      for (const a of m.artifacts) {
        L.push('- ' + (a.path || a.href || JSON.stringify(a)));
      }
    }
  }
  const blob = new Blob([L.join('\n')], {type: 'text/markdown;charset=utf-8'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  const ts = new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-');
  a.download = 'AI-workbench-' + (AI.sample || 'conv') + '-' + ts + '.md';
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 4000);
  AI.sysNote('⬇ ' + t('ai.dock.exportDone', '已导出 Markdown'));
};

/* 重试某条提问：把该条 user 消息及其后的回答全部裁掉，重新发送。
 * 2026-11-06：入口从工具条 ↻ 挪到每条用户提问下方的「重新上一句」
 * （_addRetryRow）；点旧提问 = 从那一问重来，它之后的轮次一并截掉。
 * 屏幕上同步移除该轮及其后的所有气泡，像没发生过一样。 */
AI.retryFrom = function (bubbleEl) {
  if (AI.busy) return;
  const users = AI._turnNodes();
  const k = users.indexOf(bubbleEl);
  if (k < 0) return;
  /* 第 k 个 user 气泡 ↔ AI.messages 里第 k 条 user 消息（渲染与内存 1:1） */
  let idx = -1, seen = 0;
  for (let i = 0; i < AI.messages.length; i++) {
    if (AI.messages[i].role !== 'user') continue;
    if (seen === k) { idx = i; break; }
    seen++;
  }
  if (idx < 0) return;
  const text = AI.messages[idx].content;
  AI.messages = AI.messages.slice(0, idx);    // 连该条一起裁，AI.send 会重发
  AI._saveHistory();
  /* 同步裁屏幕：从尾部移除气泡，直到把目标 user 气泡也移掉。
     操作行挂在气泡内部，随气泡一起移除，不参与这里的判定。 */
  const box = $('aiMsgs');
  let el = box && box.lastElementChild;
  while (el) {
    const prev = el.previousElementSibling;
    el.remove();
    if (el === bubbleEl) break;
    el = prev;
  }
  AI.send(text);
};

/* 兼容包装：无参调用 = 重试最后一条提问 */
AI.retryLast = function () {
  const users = AI._turnNodes();
  if (!users.length) {
    AI.sysNote(t('ai.dock.noRetry', '没有可重试的提问'));
    return;
  }
  AI.retryFrom(users[users.length - 1]);
};

/* ---------- 模型切换 ----------
 * 2026-10-26：首页内嵌对话卡移除后，它独有的"当场换模型"能力并入全局坞。
 * 数据源与设置页同一套接口：/api/ai/profiles（档案清单 + 当前激活）
 * + /api/ai/config（平铺配置，未存档案时显示当前模型）。
 * 切换 = 把档案灌进平台配置并落盘，全链路（对话/执行单/播报）立即生效。 */
AI.loadModels = async function () {
  const sel = $('aiModelSel');
  if (!sel) return;
  try {
    const [pr, cf] = await Promise.all([
      fetch('/api/ai/profiles').then(x => x.json().catch(() => ({}))),
      fetch('/api/ai/config').then(x => x.json().catch(() => ({}))),
    ]);
    const profiles = (pr && pr.profiles) || [];
    const cfg = (cf && cf.config) || {};
    AI.activeProfile = cfg.active_profile || '';
    let html = '';
    if (!AI.activeProfile) {
      html += `<option value="" selected>${esc(t('ui.qs.cfg', '当前配置 · ')
        + (cfg.model || t('ui.qs.noModel', '未设置模型')))}</option>`;
    }
    for (const p of profiles) {
      html += `<option value="pf:${esc(p.name)}"${p.active ? ' selected' : ''}>`
        + `${p.active ? '● ' : ''}${esc(p.name)} · ${esc(p.model || '')}`
        + `${p.key_set ? '' : esc(t('ui.qs.noKey', '（未存Key）'))}</option>`;
    }
    if (!profiles.length) {
      html = `<option value="" selected>${esc(t('ui.qs.cfg', '当前配置 · ')
        + (cfg.model || t('ui.qs.noModelHint', '未设置（到「设置 → AI 助手」配置）')))}</option>`;
    }
    sel.innerHTML = html;
  } catch (e) { /* 静默：选择条属增强功能，失败不影响对话 */ }
};

AI.onModelChange = async function (v) {
  if (!v || v.indexOf('pf:') !== 0) return;
  const name = v.slice(3);
  const sel = $('aiModelSel');
  const prev = AI.activeProfile ? ('pf:' + AI.activeProfile) : '';
  try {
    const r = await fetch('/api/ai/profiles/activate', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name})});
    const d = await r.json().catch(() => ({}));
    if (!r.ok) {
      AI.sysNote('⚠ ' + (d.detail || d.error || ('HTTP ' + r.status))
        + t('ui.qs.switchBack', '（已保持原配置）'));
      if (sel) sel.value = prev;            // 失败回滚下拉，别让界面撒谎
      return;
    }
    AI.ready = !!d.ready;
    AI.activeProfile = name;
    const dot = $('aiReadyDot');
    if (dot) { dot.hidden = false;
               dot.classList.toggle('ok', AI.ready);
               dot.classList.toggle('no', !AI.ready); }
    AI.sysNote(t('ui.qs.switched', '✔ 已切换模型 → ') + name
      + (d.ready ? '' : t('ui.qs.incomplete', '（注意：该配置不完整或未启用 AI 总开关）')));
    AI.loadModels();                        // 成功也回读一次：● 标记与列表对齐
  } catch (e) {
    AI.sysNote('⚠ ' + String(e.message || e));
    if (sel) sel.value = prev;
  }
};

/* ---------- 对话历史本地持久化 ----------
 * 刷新/换页不丢对话（localStorage，每段会话一个键，最多 30 条）。事件卡不
 * 持久化——它们由服务端环形缓冲在重连时重放。
 * 2026-11-02：以前所有标签页共写一个键，两个标签各聊一段就互相覆盖。 */
AI._saveHistory = function () {
  let synced = null;
  try {
    if (!AI.messages.length) return;              // 没有内容就不留空桶
    const msgs = AI._trim(AI.messages);
    const t = Date.now();
    localStorage.setItem(AI.msgsKey(), JSON.stringify({t, msgs}));
    /* 镜像：换标签页 / sessionStorage 被清 / 会话 id 换掉时，下一个页面
     * 仍能从 __last 接上最近这段对话（见 _restoreHistory 的回落分支）。 */
    try {
      localStorage.setItem(AI_LAST_KEY, JSON.stringify({t, conv: AI.convId, msgs}));
    } catch (e) { /* 体积超限：镜像写不进就只保留会话键 */ }
    AI._pruneHistory();
    synced = msgs;
  } catch (e) { /* 存储不可用则静默 */ }
  /* 2026-11-03 服务端留存（借鉴 shim）：localStorage 是即时备份，服务端
   * run/ai_sessions/<conv>.jsonl 是跨浏览器/清缓存的持久层。发送的是
   * _saveHistory 存的同一份裁剪结果（保证两端条数口径一致）；失败静默——
   * 老后端/离线时行为与从前完全一致。 */
  if (synced) AI._syncServer(synced);
};

/* 服务端镜像同步（防抖 800ms，fire-and-forget） */
AI._syncServer = function (msgs) {
  if (!AI.convId || !Array.isArray(msgs) || !msgs.length) return;
  if (AI._syncT) clearTimeout(AI._syncT);
  AI._syncT = setTimeout(function () {
    try {
      fetch('/api/ai/sync/' + encodeURIComponent(AI.convId), {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({conv_id: AI.convId, msgs: msgs})
      }).catch(function () { /* 老后端/网络抖动：静默 */ });
    } catch (e) { /* 静默 */ }
  }, 800);
};

/* 服务端优先补差（2026-11-03）：localStorage 恢复完成后异步核对服务端留存，
 * 服务端更长（换浏览器/清过缓存的第一印象）就把缺的尾部补渲染出来。
 * 每段会话每页只补一次；本地已够长 / 正在恢复 / 正在生成时不插手。 */
AI._topUpConv = '';
AI._serverTopUp = async function () {
  if (!AI.convId || AI.convId === AI._topUpConv) return;
  AI._topUpConv = AI.convId;
  let arr = [];
  try {
    const r = await fetch('/api/ai/sync/' + encodeURIComponent(AI.convId),
                          {cache: 'no-store'});
    if (!r.ok) return;
    const d = await r.json();
    arr = Array.isArray(d.msgs) ? d.msgs : [];
  } catch (e) { return; }                        // 老后端/离线：保持现状
  if (!arr.length || AI._restoring || AI.busy) return;
  if (arr.length <= AI.messages.length) return;  // 本地不缺
  const have = AI.messages.length;
  const box = $('aiMsgs');
  if (!box) return;
  const frag = document.createDocumentFragment();
  for (const m of arr.slice(have)) {
    if (!m || (m.role !== 'user' && m.role !== 'assistant')) continue;
    const raw = String(m.content || '');
    /* 与本地恢复同口径：剔除可能残留的 next_actions 围栏块 */
    const ctext = m.role === 'assistant' ? _stripNextActionsBlock(raw) : raw;
    const b = _mkBubble(m.role);
    if (m.role === 'user') {
      b.textContent = raw;
      _addRetryRow(b);
    } else {
      const ans = document.createElement('div');
      ans.className = 'ai-answer';
      ans.innerHTML = aiFmt(ctext);
      b.appendChild(ans);
      /* 反馈下标：AI.messages 在这轮循环末尾才 push，此刻的 length 正好是
       * 这条 assistant 消息的下标（2026-11-04）。 */
      _addCopyBar(b, ctext, AI.messages.length);
      AI._foldAnswer(b);
      if (m.artifacts && m.artifacts.length) {
        try { AI.renderArts(b, m.artifacts); } catch (e) { /* 忽略 */ }
      }
      if (m.partial) {
        const tag = document.createElement('div');
        tag.className = 'ai-sysnote';
        tag.textContent = t('ai.dock.partial',
          '（这条回答在生成中被打断，只保存了已完成的部分）');
        b.appendChild(tag);
      }
    }
    AI.messages.push({role: m.role, content: ctext});
    frag.appendChild(b);
  }
  box.appendChild(frag);
  try { AI._saveHistory(); } catch (e) { /* 回写本地失败不影响 */ }
  try { _scroll(); } catch (e) {}
  AI.sysNote(t('ai.dock.serverRestored',
    '🕘 已从平台服务端恢复本会话更早的内容（跨浏览器留存）'));
};

/* 流式中周期性落盘（P0-1，2026-11-02）
 * 以前只在 finally 里写一次：回答流到一半切页/切模块，整段回答直接不落盘，
 * 回来就只剩提问气泡。这里每 AI_PERSIST_MS 把「当前已生成的部分」写成一条
 * assistant 消息落盘；正常收尾时最终版再覆盖一次，所以中间态不会被误读成
 * 「这就是完整回答」——除非用户真的在流式中途离开了页面。
 * 传 live 为一个函数：返回此刻要追加的 assistant 文本（'' 表示还没内容）。 */
AI._persistStart = function (getText) {
  AI._persistStop();
  if (!getText) return;
  AI._persistFn = getText;
  AI._persistT = setInterval(() => {
    try {
      const s = String(getText() || '');
      if (!s.trim()) return;
      const last = AI.messages[AI.messages.length - 1];
      if (last && last.role === 'assistant' && last.partial) {
        last.content = s;
      } else {
        AI.messages.push({role: 'assistant', content: s, partial: true});
      }
      AI._saveHistory();
    } catch (e) { /* 落盘失败不影响生成 */ }
  }, AI_PERSIST_MS);
};

AI._persistStop = function () {
  if (AI._persistT) { clearInterval(AI._persistT); AI._persistT = 0; }
  AI._persistFn = null;
};

/* 正常收尾：撤掉流式期间写下的 partial 草稿，换成最终文本再落盘。
 * 收尾时用户其实已经离开了页面（aborted / 页面切走）则保留草稿，
 * 至少让回来的人看得到「当时生成到哪」。
 * artifacts（2026-11-06）：产物卡片必须在**这一次**落盘里就挂上。
 * 以前先 _persistFinish 再补挂 artifacts 二次保存，服务端前缀比较只认
 * role/content，artifacts 永远进不了镜像文件。 */
AI._persistFinish = function (finalText, keepDraft, artifacts) {
  AI._persistStop();
  const arts = (artifacts && artifacts.length) ? artifacts : null;
  const last = AI.messages[AI.messages.length - 1];
  if (last && last.role === 'assistant' && last.partial) {
    if (keepDraft) {
      if (finalText) last.content = finalText;
      if (arts) last.artifacts = arts;
      return;
    }
    AI.messages.pop();
  }
  if (finalText) {
    const msg = {role: 'assistant', content: finalText};
    if (arts) msg.artifacts = arts;
    AI.messages.push(msg);
    AI._saveHistory();
  }
};

/* 双闸裁剪（P2）：先按条数，再按字节；都从最旧的整轮开始丢，
 * 保证「一条提问 + 它后面的回答」要么都在、要么都不在。 */
AI._trim = function (msgs) {
  const arr = (msgs || []).slice(-AI_MSGS_CAP);
  let total = 0;
  for (const m of arr) total += String(m.content || '').length + 32;
  while (arr.length > 2 && total > AI_MSGS_BYTES) {
    const dropped = arr.shift();
    total -= String(dropped.content || '').length + 32;
    /* 丢掉的是提问，说明这一轮的回答也断了，连着一起丢 */
    while (arr.length && arr[0] && arr[0].role === 'assistant') {
      const d2 = arr.shift();
      total -= String(d2.content || '').length + 32;
    }
  }
  return arr.map(m => {
    const o = {role: m.role, content: m.content};
    if (m.partial) o.partial = 1;
    /* 产物卡片（2026-11-02）：回答里提到的文件清单也要落盘，否则重开页面
     * 只剩一段裸路径，用户又得自己去翻。 */
    if (m.artifacts && m.artifacts.length) o.artifacts = m.artifacts;
    return o;
  });
};

/* 只保留最近 AI_HIST_MAX 段会话，避免 localStorage 无限增长 */
AI._pruneHistory = function () {
  try {
    const cur = AI.msgsKey();
    const rows = [];
    for (let i = 0; i < localStorage.length; i += 1) {
      const k = localStorage.key(i);
      if (!k || k.indexOf('vp_ai_msgs:') !== 0 || k === cur) continue;
      if (k === AI_LAST_KEY) continue;            // 镜像是兜底，永远不裁
      let t = 0;
      try { t = Number((JSON.parse(localStorage.getItem(k) || '{}') || {}).t) || 0; }
      catch (e) { t = 0; }
      rows.push([k, t]);
    }
    if (rows.length < AI_HIST_MAX) return;
    rows.sort((a, b) => a[1] - b[1]);
    rows.slice(0, rows.length - (AI_HIST_MAX - 1)).forEach(([k]) => {
      localStorage.removeItem(k);
    });
  } catch (e) {}
};

/* 历史恢复（P0-2，2026-11-02 重写）
 * 旧实现逐条 _bubble() —— 那是「先把空壳 append 到 #aiMsgs，再往里填内容」。
 * 一旦在循环中途被切页/切模块/切工作区打断，就留下截图里那种「有提问气泡、
 * 右边空白」的残局。现在改为：
 *   ① 全程在 DocumentFragment 里造节点，最后一次性 append；
 *   ② 恢复期间置 AI._restoring，_bubble() 不再往 #aiMsgs 增量挂节点；
 *   ③ 并发调用合流到同一个 promise，不会恢复两遍；
 *   ④ 恢复写入 localStorage 的键与当前会话 id 一致才认，防止串台。 */
AI._restoreHistory = function () {
  if (AI._restoreP) return AI._restoreP;      // 并发合流
  AI._restoring = true;
  let resolved = false;
  let value = false;
  AI._restoreP = new Promise((resolve) => { AI._restoreResolve = (v) => { resolved = true; value = v; resolve(v); }; });
  AI._restoreSync = () => {
    if (resolved) return value;
    let ok = false;
    try {
      const box = $('aiMsgs');
      if (!box) { ok = false; }
      else {
        const read = (key) => {
          try {
            const raw = localStorage.getItem(key);
            const obj = raw ? JSON.parse(raw) : null;
            const arr = Array.isArray(obj) ? obj : (obj && Array.isArray(obj.msgs) ? obj.msgs : []);
            return {obj, arr};
          } catch (e) { return {obj: null, arr: []}; }
        };
        let {obj, arr} = read(AI.msgsKey());
        /* 当前会话键为空/损坏 → 回落到镜像键（并把它记回的会话 id 认下来），
         * 这样换标签页、sessionStorage 被清、或会话 id 换了也不会「对话没了」。 */
        if (!Array.isArray(arr) || !arr.length) {
          const mirror = read(AI_LAST_KEY);
          if (mirror.arr.length) {
            obj = mirror.obj;
            arr = mirror.arr;
            const mc = String((obj && obj.conv) || '');
            if (mc && mc !== AI.convId) {
              AI.convId = mc;
              AI._ss.set(AI_CONV_KEY, mc);
            }
            try { localStorage.setItem(AI.msgsKey(), JSON.stringify({t: Date.now(), msgs: arr})); }
            catch (e) { /* 认回失败不影响本次渲染 */ }
          }
        }
        if (!Array.isArray(arr) || !arr.length) { ok = false; }
        else {
          const frag = document.createDocumentFragment();
          /* 旧回答（升级前存的）只有裸路径、没有 artifacts：这里记下气泡，
           * 挂载后一次性问服务端要卡片，免得用户重新提问才看得到「可点开」。 */
          const needArts = [];
          for (const m of arr.slice(-AI_MSGS_CAP)) {
            if (!m || (m.role !== 'user' && m.role !== 'assistant')) continue;
            const raw = String(m.content || '');
            /* 旧草稿里可能还留着 ```next_actions``` 围栏块（流式中断时
               落盘的中间态）：恢复时一并剔除，不把裸 JSON 摆给用户，
               内存里的 AI.messages 也与界面同口径。 */
            const ctext = m.role === 'assistant'
              ? _stripNextActionsBlock(raw) : raw;
            const b = _mkBubble(m.role);
            if (m.role === 'user') { b.textContent = raw; _addRetryRow(b); }
            else {
              const ans = document.createElement('div');
              ans.className = 'ai-answer';
              ans.innerHTML = aiFmt(ctext);
              b.appendChild(ans);
              /* AI.messages 在这轮循环后面才 push，此刻的 length 正好是它的下标 */
              _addCopyBar(b, ctext, AI.messages.length);
              AI._foldAnswer(b);
              // 产物卡片随历史一起恢复（2026-11-02）
              if (m.artifacts && m.artifacts.length) {
                try { AI.renderArts(b, m.artifacts); } catch (e) { /* 忽略 */ }
              } else if (ctext.length > 8) {
                needArts.push({el: b, text: ctext,
                               idx: AI.messages.length});
              }
              if (m.partial) {
                /* 上次是在流式中途被切走的：明确标注「未完」，别让人当成完整版 */
                const tag = document.createElement('div');
                tag.className = 'ai-sysnote';
                tag.textContent = t('ai.dock.partial',
                  '（这条回答在生成中被打断，只保存了已完成的部分）');
                b.appendChild(tag);
              }
            }
            AI.messages.push({role: m.role, content: ctext});
            frag.appendChild(b);
          }
          box.appendChild(frag);        // ← 唯一一次挂载
          if (needArts.length) AI._backfillArts(needArts);
          ok = AI.messages.length > 0;
        }
      }
    } catch (e) { ok = false; }
    AI._restoring = false;
    try { _scroll(); } catch (e) {}
    if (AI._restoreResolve) AI._restoreResolve(ok);
    AI._restoreP = null;
    return ok;
  };
  return AI._restoreSync();
};


/* ---------- 消息渲染 ---------- */

/* Markdown 渲染（先转义，再放行少量标记；不引入任何原始 HTML）。
 *
 * 2026-10-30 重写：原实现只认加粗/行内代码/无序列表，模型输出的 Markdown
 * 表格（截图里 `| 文件 | 大小 |`）和图片被当成纯文本原样显示。现在按块解析：
 *   表格（含分隔行）→ <table>；图片 → <img>（只允许 http(s) / /static / /api）；
 *   标题 → 分级标题；引用；有序列表；分隔线。流式输出时表格在分隔行到达前
 *   先按普通行显示，到位后自动变表格。
 */
/* 统一渲染器在 webapp/static/app-md.js（_nav.html 先加载）。
 * chat 模式：不解释原始 HTML；代码块带语言标签/复制/横向滚动。 */
function aiInline(s) {
  try {
    return window.VPMD ? window.VPMD.inline(String(s), {mode: 'chat'}) : esc(s);
  } catch (e) { return esc(s); }
}

function aiFmt(s) {
  try {
    return window.VPMD ? window.VPMD.render(String(s), {mode: 'chat'}) : esc(s);
  } catch (e) { return esc(s); }
}

/* 是否贴着底部：只有用户本来就在看最新内容时才自动滚到底，
 * 用户往上翻历史时不抢滚动位置。 */
function _atBottom() {
  const m = $('aiMsgs');
  if (!m) return true;
  return (m.scrollHeight - m.scrollTop - m.clientHeight) < 56;
}

function _scroll(force) {
  const m = $('aiMsgs');
  if (!m) return;
  if (force === false && !_atBottom()) return;
  m.scrollTop = m.scrollHeight;
}

/* ---------- 右侧「历史提问导航」（2026-11-02） ----------
 * 一轮提问一个短横点：点一下把人送回那一轮；滚动时高亮"当前轮"。
 * 要点：
 *   · 节点挂在 .ai-msgs-wrap 上（不跟消息一起滚），消息区右侧留 26px；
 *   · 只认 #aiMsgs 的直接子节点里的 .ai-user —— 助理回答、播报卡、
 *     系统提示、回放折叠块都不打点，否则一屏几十个点没法用；
 *   · 重建靠 MutationObserver（只看 childList、不递归），新增提问 / 清空 /
 *     恢复历史 / 回放插入都自动重算，不必在每处渲染里埋钩子；
 *   · 流式输出只改回答节点内部的 innerHTML，不触发重建；点距是等高的，
 *     所以回答变长时导航条不会抖。
 * 没有提问、或只有一轮时整条隐藏（一轮的"导航"没有意义）。 */
AI._turnNodes = function () {
  const box = $('aiMsgs');
  if (!box) return [];
  return Array.prototype.filter.call(box.children, n => n.classList
    && n.classList.contains('ai-msg') && n.classList.contains('ai-user'));
};

/* 预览文案：提问气泡里可能挂着附件芯片（📎 文件名）与「↻ 重新上一句」
 * 操作行（2026-11-06），预览时一并剔掉 */
AI._turnLabel = function (node) {
  const c = node.cloneNode(true);
  Array.prototype.forEach.call(
    c.querySelectorAll('.ai-msg-atts, .ai-msg-actions'),
    x => x.parentNode.removeChild(x));
  return (c.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 120);
};

AI.rebuildTurnNav = function () {
  const nav = $('aiTurnNav');
  if (!nav) return;
  const turns = AI._turnNodes();
  AI.hideTurnTip();                 // 节点重建后旧预览的锚点就失效了
  nav.textContent = '';
  nav.setAttribute('aria-label', t('ai.nav.title', '历史提问导航'));
  AI._navTicks = turns.map((node, i) => {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'ai-turn-tick';
    b.dataset.i = String(i);
    b.setAttribute('aria-label',
      t('ai.nav.jumpTpl', '跳到第 {n} 轮提问').replace('{n}', String(i + 1)));
    b.addEventListener('click', () => AI.jumpTurn(i));
    b.addEventListener('mousedown', () => { AI._navMouse = true; });
    /* 预览挂在 mousemove 而不是 mouseenter：重建导航条时如果光标恰好停在
       轨道上，mouseenter 会给新点补一发预览 —— 明明没动鼠标却弹出气泡。
       真实悬停一定伴随 mousemove，所以这样既不丢功能也不乱弹。 */
    b.addEventListener('mousemove', () => AI._turnTip(b, node, i));
    b.addEventListener('mouseleave', () => AI.hideTurnTip());
    /* 鼠标点出来的焦点不弹预览 —— 点一下只想跳转，预览会挡住落点气泡；
       键盘 Tab 过来的焦点照常预览。 */
    b.addEventListener('focus', () => {
      if (AI._navMouse) { AI._navMouse = false; return; }
      AI._turnTip(b, node, i);
    });
    b.addEventListener('blur', () => AI.hideTurnTip());
    nav.appendChild(b);
    return {tick: b, node};
  });
  nav.hidden = turns.length < 2;
  /* 轮数多时压缩点距：容器高度由 top/bottom 定死，30 轮 ×(12+8)px 会把点
     顶出消息区。上下内边距 8px 之外全部分给间隙，最少 1px；间隙压到 1px
     仍装不下时（矮视口 × 多轮数），等比压缩点高（最低 4px，::before 短横
     居中不受影响），保证点带始终落在轨道高度内（2026-11-06 纵向 389px 实测）。 */
  if (!nav.hidden) {
    const n = turns.length;
    const avail = nav.clientHeight - 8;
    const TICK = 12;
    let h = TICK;
    let gap = Math.floor((avail - n * TICK) / Math.max(1, n - 1));
    if (gap < 1) {
      gap = 1;
      h = Math.max(4, Math.floor((avail - (n - 1) * gap) / n));
    }
    nav.style.gap = gap + 'px';
    for (const it of AI._navTicks) {
      it.tick.style.height = (h === TICK ? '' : h + 'px');
    }
  } else {
    nav.style.gap = '';
    for (const it of AI._navTicks) {
      it.tick.style.height = '';
    }
  }
  AI._navIdx = -1;
  AI.syncTurnNav();
};

AI.syncTurnNav = function () {
  const box = $('aiMsgs');
  if (!box || !AI._navTicks.length) return;
  const total = AI._navTicks.length;
  const end = (box.scrollHeight - box.scrollTop - box.clientHeight) < 12;
  let idx = 0;
  if (end) {
    idx = total - 1;                       // 贴底就是最后一轮，哪怕它顶在屏幕外
  } else {
    /* 视口顶部（留 10px 余量）落在内容坐标里的位置；各轮 offsetTop 与其
       相减才是同一个坐标系 —— offsetParent 是 .ai-msgs-wrap，所以统一先
       减掉消息区的 offsetTop。 */
    const line = box.scrollTop + 10;
    AI._navTicks.forEach((it, i) => {
      if (it.node.offsetTop - box.offsetTop <= line) idx = i;
    });
  }
  if (idx === AI._navIdx) return;
  AI._navIdx = idx;
  AI._navTicks.forEach((it, i) => {
    it.tick.classList.toggle('on', i === idx);
  });
};

AI._onMsgsScroll = function () {
  AI.hideTurnTip();            // 预览跟着点位走，滚了就不该留在原地
  if (AI._navRaf) return;
  const raf = window.requestAnimationFrame || (f => setTimeout(f, 16));
  AI._navRaf = raf(() => { AI._navRaf = 0; AI.syncTurnNav(); });
};

AI.jumpTurn = function (i) {
  const box = $('aiMsgs');
  const it = AI._navTicks[i];
  if (!box || !it) return;
  const top = Math.max(0, it.node.offsetTop - box.offsetTop - 8);
  if (box.scrollTo) box.scrollTo({top, behavior: 'smooth'});
  else box.scrollTop = top;
  AI.hideTurnTip();
  AI._navIdx = i;
  AI._navTicks.forEach((x, k) => x.tick.classList.toggle('on', k === i));
  // 重启动画：同一个气泡连着点两次也要再闪一次
  it.node.classList.remove('ai-turn-flash');
  void it.node.offsetWidth;
  it.node.classList.add('ai-turn-flash');
  clearTimeout(AI._flashT);
  AI._flashT = setTimeout(() => it.node.classList.remove('ai-turn-flash'), 1200);
};

AI._turnTip = function (tick, node, i) {
  const tip = $('aiTurnTip');
  const box = $('aiMsgs');
  const wrap = box && box.parentNode;
  if (!tip || !wrap) return;
  if (!tip.hidden && tip.dataset.i === String(i)) return;   // 同一条上滑动，别重排
  const q = AI._turnLabel(node);
  tip.dataset.i = String(i);
  tip.textContent = (i >= 0
    ? t('ai.nav.turnTpl', '第 {n} 轮 · ').replace('{n}', String(i + 1))
    : '') + (q || t('ai.nav.empty', '（这条提问只有附件）'));
  tip.hidden = false;
  const r = tick.getBoundingClientRect();
  const w = wrap.getBoundingClientRect();
  const y = r.top - w.top + r.height / 2;
  tip.style.top = Math.round(Math.max(30, Math.min(w.height - 30, y))) + 'px';
};

AI.hideTurnTip = function () {
  const tip = $('aiTurnTip');
  if (tip) tip.hidden = true;
};

AI.initTurnNav = function () {
  const box = $('aiMsgs');
  if (!box || AI._navBound) return;
  AI._navBound = true;
  box.addEventListener('scroll', AI._onMsgsScroll, {passive: true});
  const navEl = $('aiTurnNav');
  if (navEl) navEl.addEventListener('mouseleave', () => AI.hideTurnTip());
  if (typeof MutationObserver !== 'undefined') {
    AI._navMO = new MutationObserver(() => {
      if (AI._navT) return;
      AI._navT = setTimeout(() => {
        AI._navT = 0;
        AI.rebuildTurnNav();
      }, 30);
    });
    /* 只盯直接子节点：回答流式重排发生在 .ai-answer 内部，不该触发重建 */
    AI._navMO.observe(box, {childList: true});
  }
  AI.rebuildTurnNav();
};

/* 每条助手回答下方的“复制回答”条（复制用户看到的是纯文本）。
 * 2026-11-04：顺带挂 👍/👎 反馈（msgIdx = 该条在 AI.messages 里的下标，
 * 恢复历史/服务端补差时由调用方在渲染循环里记录；传 -1 表示不挂）。 */
function _addCopyBar(bb, text, msgIdx) {
  if (!bb || !text || !String(text).trim()) return;
  if (bb.querySelector('.ai-msg-bar')) return;
  const bar = document.createElement('div');
  bar.className = 'ai-msg-bar';
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'ai-msg-copy';
  btn.textContent = '⧉ ' + t('md.copyAnswer', '复制回答');
  btn.title = t('md.copyAnswer', '复制回答');
  btn.addEventListener('click', () => {
    const src = bb.querySelector('.ai-answer') || bb;
    const copy = window.VPMD && window.VPMD.copy;
    const done = ok => {
      btn.textContent = ok ? '✓ ' + t('md.copied', '已复制')
                           : t('md.copyFail', '复制失败');
      btn.classList.toggle('ok', !!ok);
      clearTimeout(btn.__vpT);
      btn.__vpT = setTimeout(() => {
        btn.textContent = '⧉ ' + t('md.copyAnswer', '复制回答');
        btn.classList.remove('ok');
      }, 1500);
    };
    if (copy) copy(src.innerText, done);
    else if (navigator.clipboard) navigator.clipboard.writeText(src.innerText).then(() => done(true), () => done(false));
  });
  bar.appendChild(btn);
  /* 2026-11-06：导出会话挪进消息流 —— 每条 AI 回复的操作条里都有「⬇ 导出」，
     点任意一条都导出当前完整会话（Markdown），与复制并排。 */
  const ex = document.createElement('button');
  ex.type = 'button';
  ex.className = 'ai-msg-copy ai-msg-export';
  ex.textContent = '⬇ ' + t('ai.msg.export', '导出');
  ex.title = t('ai.dock.export', '导出当前会话为 Markdown');
  ex.addEventListener('click', () => AI.exportConv());
  bar.appendChild(ex);
  if (typeof msgIdx === 'number' && msgIdx >= 0) _addFbBtns(bar, bb, msgIdx);
  bb.appendChild(bar);
}

/* 每条用户提问下方的「↻ 重新上一句」（2026-11-06，从工具条挪入消息流）。
 * 点旧提问 = 从那一问重来（AI.retryFrom）：该条及其后的轮次一并截掉再重发。
 * 行挂在气泡内部：不新增 #aiMsgs 直接子节点，避开 retryFrom 尾删循环的
 * ai-user 判定与 _turnNodes 的直接子节点统计；生成中禁用。 */
function _addRetryRow(ub) {
  if (!ub || ub.querySelector('.ai-msg-actions')) return;
  const row = document.createElement('div');
  row.className = 'ai-msg-actions';
  const b = document.createElement('button');
  b.type = 'button';
  b.className = 'ai-msg-act-retry';
  b.textContent = '↻ ' + t('ai.msg.retry', '重新上一句');
  b.title = t('ai.dock.retry', '重试上一问（可选带一句纠正）');
  b.disabled = !!AI.busy;
  b.addEventListener('click', () => AI.toggleRetryMenu(b));
  row.appendChild(b);
  ub.appendChild(row);
}

/* ---------- 👍👎 反馈（2026-11-04） ----------
 * 每条回答只允许反馈一次：提交成功后按钮置灰、去掉输入行；👎 先展开一行
 * 「哪里不对？」，发送时把 note 一起带给 /api/ai/feedback。 */
function _addFbBtns(bar, bb, msgIdx) {
  if (bar.querySelector('.ai-fb')) return;
  const box = document.createElement('span');
  box.className = 'ai-fb';
  const mk = (verdict, ico, label) => {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'ai-fb-btn';
    b.textContent = ico;
    b.title = label;
    b.setAttribute('aria-label', label);
    b.addEventListener('click', () => {
      if (bb.dataset.fbDone) return;
      if (verdict === 'up') { AI._sendFeedback(bb, msgIdx, 'up', ''); return; }
      _showFbNote(bb, msgIdx);
    });
    return b;
  };
  box.appendChild(mk('up', '👍', t('ai.fb.up', '回答有帮助')));
  box.appendChild(mk('down', '👎', t('ai.fb.down', '回答没帮助')));
  bar.appendChild(box);
}

function _showFbNote(bb, msgIdx) {
  if (!bb || bb.dataset.fbDone) return;
  const old = bb.querySelector('.ai-fb-note');
  if (old) {
    const i = old.querySelector('input');
    if (i) i.focus();
    return;
  }
  const row = document.createElement('div');
  row.className = 'ai-fb-note';
  const inp = document.createElement('input');
  inp.type = 'text';
  inp.className = 'ai-fb-input';
  inp.maxLength = 500;
  inp.placeholder = t('ai.fb.notePh', '哪里不对？一句话即可，可留空');
  const send = document.createElement('button');
  send.type = 'button';
  send.className = 'btn small primary';
  send.textContent = t('ai.fb.send', '发送');
  const skip = document.createElement('button');
  skip.type = 'button';
  skip.className = 'btn small';
  skip.textContent = t('ai.fb.skip', '跳过');
  const submit = () => {
    if (send.disabled) return;
    send.disabled = true;
    AI._sendFeedback(bb, msgIdx, 'down', inp.value.trim());
  };
  send.addEventListener('click', submit);
  skip.addEventListener('click', () => row.remove());
  inp.addEventListener('keydown', e => {
    if (e.key === 'Enter') { e.preventDefault(); submit(); }
  });
  row.appendChild(inp);
  row.appendChild(send);
  row.appendChild(skip);
  bb.appendChild(row);
  inp.focus();
}

/* 反馈端点的 conv_id 白名单是 [A-Za-z0-9_-]{6,64}；老版本迁移会留下
 * 'main' 这种短 id，这里做一次稳定归一（同一会话始终得到同一个值），
 * 免得老用户点 👍 直接吃 400。 */
function _fbConvId() {
  let cid = String(AI.convId || '');
  if (/^[A-Za-z0-9_-]{6,64}$/.test(cid)) return cid;
  cid = cid.replace(/[^A-Za-z0-9_-]/g, '_') || 'conv';
  while (cid.length < 6) cid += '_';
  return cid.slice(0, 64);
}

AI._sendFeedback = function (bb, msgIdx, verdict, note) {
  if (!bb || bb.dataset.fbDone) return;
  /* 提交期间先置灰，防止连点造成重复上报；失败时再放开 */
  const fbox = bb.querySelector('.ai-fb');
  if (fbox) fbox.querySelectorAll('button').forEach(b => { b.disabled = true; });
  const body = {conv_id: _fbConvId(), msg_idx: msgIdx, verdict: verdict,
                note: note || ''};
  fetch('/api/ai/feedback', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body)
  })
    .then(r => r.json().catch(() => ({})).then(d => {
      if (!r.ok) throw new Error((d && (d.error || d.detail)) || ('HTTP ' + r.status));
      if (d && d.ok === false) throw new Error(d.error || 'feedback failed');
      return d;
    }))
    .then(() => {
      bb.dataset.fbDone = verdict;
      const box = bb.querySelector('.ai-fb');
      if (box) {
        box.querySelectorAll('button').forEach(b => {
          b.disabled = true;
          b.title = t('ai.fb.done', '已反馈');
        });
      }
      const row = bb.querySelector('.ai-fb-note');
      if (row) row.remove();
      _aiToast(t('ai.fb.thanks', '已记录，谢谢'), '', '');
    })
    .catch(err => {
      const row = bb.querySelector('.ai-fb-note');
      if (row) row.querySelectorAll('button').forEach(b => { b.disabled = false; });
      const box = bb.querySelector('.ai-fb');
      if (box && !bb.dataset.fbDone) {
        box.querySelectorAll('button').forEach(b => { b.disabled = false; });
      }
      _aiToast(t('ai.fb.fail', '反馈提交失败'),
               String((err && err.message) || err), 'failed');
    });
};

/* 对话坞内的小提示：全局 toast 不可用（老页面/测试环境）时退回 sysNote */
function _aiToast(title, body, kind) {
  try {
    if (typeof window.toast === 'function') {
      window.toast(title, body || '', {kind: kind || '', ttl: 3000});
      return;
    }
  } catch (e) { /* 忽略，走兜底 */ }
  try { AI.sysNote(title + (body ? ' · ' + body : '')); } catch (e) { /* 静默 */ }
}

/* ---------- 长回答折叠（F5，2026-11-04） ----------
 * 正文超过 3000 字符时先把高度收在 ~460px（底部渐隐），点「展开全文」恢复。 */
/* 剔除回答末尾的 ```next_actions``` 围栏块（2026-11-04）。
 * 后端在落库正文里已经删掉它，但流式 delta 是先原样进前端 acc 的；
 * 收到 next_actions 事件时同步清理，避免界面上露出裸 JSON。只删最后一个
 * 块（与后端 _parse_next_actions 的口径一致）。 */
function _stripNextActionsBlock(s) {
  const text = String(s || '');
  const re = /```next_actions\b[\s\S]*?```/gi;
  let last = null;
  let m;
  while ((m = re.exec(text))) last = m;
  if (!last) return text;
  return (text.slice(0, last.index) + text.slice(last.index + last[0].length))
    .trim();
}

AI._foldAnswer = function (bb) {
  const ans = bb && bb.querySelector('.ai-answer');
  if (!ans || ans.classList.contains('ai-fold')) return;
  if (String(ans.textContent || '').length <= 3000) return;
  ans.classList.add('ai-fold');
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'ai-fold-btn';
  btn.textContent = '▾ ' + t('ai.fold.more', '展开全文');
  btn.addEventListener('click', () => {
    ans.classList.remove('ai-fold');
    btn.remove();
    _scroll(false);
  });
  if (ans.parentNode) ans.parentNode.insertBefore(btn, ans.nextSibling);
};

/* ---------- 下一步建议按钮（F1，2026-11-04） ----------
 * 回答收尾时把 SSE next_actions 渲染成一排按钮；点击 = 起草执行单
 * （POST /api/ai/actions/create），成功后复用 propose_action 的卡片渲染
 * 路径，把「待你批准」的卡片插进对话，**不会**立即执行。 */
AI._renderNextActions = function (bb, actions) {
  if (!bb || !Array.isArray(actions) || !actions.length) return null;
  const row = document.createElement('div');
  row.className = 'ai-next';
  row.setAttribute('aria-label', t('ai.next.aria', '下一步建议'));
  actions.forEach(a => {
    if (!a) return;
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'btn small ai-next-btn';
    b.textContent = String(a.label || a.action || '');
    b.title = String(a.reason || '');
    b.addEventListener('click', () => AI._createNextAction(b, a, row));
    row.appendChild(b);
  });
  bb.appendChild(row);
  _scroll(false);
  return row;
};

AI._createNextAction = function (btn, a, row) {
  if (!a || !a.action) return;
  const key = [a.action, a.sample || '',
               (Array.isArray(a.stages) ? a.stages.join(',') : '')].join('|');
  if (AI._nextBusy[key] || (row && row._busy)) return;
  AI._nextBusy[key] = 1;
  if (row) row._busy = 1;
  const btns = row ? Array.prototype.slice.call(row.querySelectorAll('button')) : [btn];
  btns.forEach(b => { b.disabled = true; });        // 渲染期间禁用，防重复起草
  btn.classList.add('busy');
  fetch('/api/ai/actions/create', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      action: a.action,
      sample: a.sample || '',
      stages: Array.isArray(a.stages) ? a.stages : [],
      params: (a.params && typeof a.params === 'object') ? a.params : {},
      reason: a.reason || ''
    })
  })
    .then(r => r.json().catch(() => ({})).then(d => {
      if (!r.ok) throw new Error((d && (d.error || d.detail)) || ('HTTP ' + r.status));
      return d;
    }))
    .then(d => {
      if (!d || d.created === false) {
        throw new Error((d && (d.error || d.detail)) || 'create failed');
      }
      AI._insertActionCard(d, a);
      btn.classList.remove('busy');
      btn.classList.add('ok');
      btn.textContent = '✓ ' + String(a.label || a.action || '');
      btn.disabled = true;                            // 已起草：同一条不再重复建单
      btns.forEach(b => { if (b !== btn) b.disabled = false; });
      if (row) row._busy = 0;
      _aiToast(t('ai.next.created', '已起草，待批准'),
               String(a.label || ''), '');
    })
    .catch(err => {
      delete AI._nextBusy[key];
      if (row) row._busy = 0;
      btns.forEach(b => { b.disabled = false; });
      btn.classList.remove('busy');
      _aiToast(t('ai.next.fail', '起草失败'),
               String((err && err.message) || err), 'failed');
    });
};

/* 用 /api/ai/actions/create 的响应造一张执行单卡（与 propose_action 同一条
 * 渲染路径）。同一 aid 已渲染过就不重复插卡 —— 事件流稍后补发 pending 事件
 * 时也不会出现两张一样的卡。 */
AI._insertActionCard = function (rec, a) {
  if (!rec) return null;
  const aid = rec.id || rec.aid;
  if (!aid) return null;
  const sid = String(aid);
  const old = Array.prototype.slice.call(document.querySelectorAll('.ai-action'))
    .find(n => n.dataset.aid === sid);
  if (old) return old;
  a = a || {};
  AI.renderEvent({
    type: 'ai_action', aid: sid, id: -1, backlog: false,
    status: rec.status || 'pending',
    title: rec.title || a.label || a.action || '',
    action: rec.action || a.action || '',
    sample: rec.sample !== undefined ? rec.sample : (a.sample || ''),
    stages: Array.isArray(rec.stages) ? rec.stages
            : (Array.isArray(a.stages) ? a.stages : []),
    params: (rec.params && typeof rec.params === 'object') ? rec.params
            : ((a.params && typeof a.params === 'object') ? a.params : {}),
    reason: rec.reason || a.reason || '',
    /* 多步计划的回显里带 steps（与 propose_action 卡片同口径），
       少了它计划卡就只剩标题，看不到会跑哪几步。 */
    steps: Array.isArray(rec.steps) ? rec.steps : undefined
  });
  return Array.prototype.slice.call(document.querySelectorAll('.ai-action'))
    .find(n => n.dataset.aid === sid) || null;
};

/* 只造节点、不挂载（恢复历史用；P0-2 起 _bubble 与 _mkBubble 分离） */
function _mkBubble(role) {
  const wrap = document.createElement('div');
  wrap.className = `ai-msg ${role === 'user' ? 'ai-user' : 'ai-bot'}`;
  return wrap;
}

function _bubble(role) {
  const wrap = _mkBubble(role);
  /* 恢复历史期间不往 #aiMsgs 增量挂节点：恢复最后一次性 append，
   * 中途被打断也不会留下半截空壳（见 _restoreHistory）。 */
  if (!AI._restoring && $('aiMsgs')) {
    $('aiMsgs').appendChild(wrap);
    _scroll();
  }
  return wrap;
}

AI.greet = function () {
  const name = AI.sample ? `"${AI.sample}"` : '';
  const b = _bubble('assistant');
  b.innerHTML = aiFmt(t('ai.dock.greeting',
    '你好！我是平台内置 AI 工作台。我可以解读分析结果、解释参数、建议下一步。')
    + (AI.sample ? `\n\n📎 ${t('ai.dock.sample', '样品')}: **${AI.sample}**` : ''));
};

AI.notice = function (text) {
  const n = document.createElement('div');
  n.className = 'ai-notice';
  n.innerHTML = esc(text)
    + ` <a href="/settings#aiCard">${t('ai.dock.goSettings', '去配置')} ›</a>`;
  $('aiMsgs').appendChild(n);
  _scroll();
};

AI.sysNote = function (text) {
  const n = document.createElement('div');
  n.className = 'ai-sysnote';
  n.textContent = text;
  $('aiMsgs').appendChild(n);
  _scroll();
};

/* 清空当前会话（P1-2，2026-11-02）
 * 以前点 ⟲ 直接抹掉 localStorage 里的整段会话，没有二次确认——鼠标一滑
 * 就把几十分钟的问答清干净了。现在：① 先弹确认；② 只有真的确认才删；
 * ③ 生成中先停再清（沿用旧逻辑）。 */
AI.clear = function (force) {
  if (!force) {
    const n = AI.messages.length;
    const warn = t('ai.dock.clearConfirm',
      n ? '确定清空当前会话？共 {n} 条消息，清空后无法恢复。'
        : '当前会话是空的，清空只会重置欢迎语。').replace('{n}', String(n));
    if (typeof window.confirm === 'function') {
      if (!window.confirm(warn)) return;
    }
  }
  // 生成中清空会让正在流式的回答写进已清空的会话（界面空了、下次刷新
  // 却冒出一条没有提问的 assistant 消息）。先停再清。
  AI._persistStop();
  if (AI.busy && AI._ctrl) { try { AI._ctrl.abort(); } catch (e) {} }
  AI.messages = [];
  try { localStorage.removeItem(AI.msgsKey()); } catch (e) {}
  /* 镜像键也要清（2026-11-02）：否则清空后换页，_restoreHistory 会从
   * `vp_ai_msgs:__last` 把刚清掉的对话又捞回来。 */
  try { localStorage.removeItem(AI_LAST_KEY); } catch (e) {}
  if (AI._backlog) { AI._backlog.remove(); AI._backlog = null; AI._backlogN = 0; }
  AI._seenAction = {};
  /* 2026-11-02：清空要连「服务端还能重放的东西」一起断掉。否则换页回来
   * hydratePending 会按 `/api/ai/actions` 的 recent 名单，把最近三条执行单
   * 补成只读历史卡 —— 用户看到的就是「清空了，切个模块又冒出来」。这里记
   * 一个清空时刻，再把事件水位推到服务端当前最大 id（留着的旧 SSE 也不回放）。 */
  AI._clearedAt = Date.now() / 1000;
  try { localStorage.setItem(AI_CLEAR_KEY, String(AI._clearedAt)); } catch (e) {}
  AI._dropReplay();
  $('aiMsgs').innerHTML = '';
  AI.greet();
};

/* ---------- 历史会话列表（P1-1，2026-11-02） ----------
 * localStorage 里本来就留着最近 AI_HIST_MAX 段会话，但界面上从来没有任何
 * 入口能看到它们——切个工作空间/清空一次，用户就以为「以前的对话没了」。
 * 这里补一个下拉：列出所有会话（首句提问 + 条数 + 相对时间），点一下切过去。
 * 关键设计：切会话 ≠ 清空，原来的会话键原地保留，随时能切回来。 */

/* 读出全部会话 [{id, key, t, n, first}]，按时间倒序 */
AI._listConvs = function () {
  const rows = [];
  try {
    for (let i = 0; i < localStorage.length; i += 1) {
      const k = localStorage.key(i);
      if (!k || k.indexOf('vp_ai_msgs:') !== 0) continue;
      if (k === AI_LAST_KEY) continue;          // 镜像是兜底副本，不算一段会话
      let t = 0, n = 0, first = '';
      try {
        const o = JSON.parse(localStorage.getItem(k) || '{}') || {};
        t = Number(o.t) || 0;
        const arr = Array.isArray(o.msgs) ? o.msgs : [];
        n = arr.length;
        const u = arr.find(m => m && m.role === 'user');
        first = u ? String(u.content || '').slice(0, 60) : '';
      } catch (e) { t = 0; }
      rows.push({id: k.slice('vp_ai_msgs:'.length), key: k, t, n, first});
    }
  } catch (e) {}
  rows.sort((a, b) => b.t - a.t);
  return rows;
};

function _timeAgo(t) {
  const s = Math.max(0, Math.round((Date.now() - (t || 0)) / 1000));
  if (s < 60) return '刚刚';
  if (s < 3600) return Math.round(s / 60) + ' 分钟前';
  if (s < 86400) return Math.round(s / 3600) + ' 小时前';
  return Math.round(s / 86400) + ' 天前';
}

AI.toggleConvMenu = function () {
  if (AI._convMenu) { AI._closeConvMenu(); return; }
  const anchor = $('aiConvBtn');
  if (!anchor) return;
  const rows = AI._listConvs();
  const m = document.createElement('div');
  m.className = 'ai-conv-menu';
  const head = document.createElement('div');
  head.className = 'ai-conv-head';
  head.textContent = t('ai.dock.convs', '历史会话') + `（${rows.length}）`;
  m.appendChild(head);

  const add = (id, label, sub, active) => {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'ai-conv-item' + (active ? ' active' : '');
    const l1 = document.createElement('div');
    l1.className = 'ai-conv-t1';
    l1.textContent = label;
    const l2 = document.createElement('div');
    l2.className = 'ai-conv-t2';
    l2.textContent = sub;
    b.appendChild(l1);
    b.appendChild(l2);
    b.addEventListener('click', () => { AI._closeConvMenu(); AI.switchConv(id); });
    m.appendChild(b);
  };

  if (!rows.length) {
    const e = document.createElement('div');
    e.className = 'ai-conv-empty';
    e.textContent = t('ai.dock.noConvs', '还没有别的会话');
    m.appendChild(e);
  }
  rows.forEach((r) => {
    add(r.id, r.first || t('ai.dock.untitled', '（无提问）'),
      `${r.n} ${t('ai.dock.msgsN', '条')} · ${_timeAgo(r.t)}`
      + (r.id === AI.convId ? ' · ' + t('ai.dock.current', '当前') : ''),
      r.id === AI.convId);
  });

  /* 底部：开一段新会话（真正的「新话题」，与清空区分开） */
  const nw = document.createElement('button');
  nw.type = 'button';
  nw.className = 'ai-conv-new';
  nw.textContent = '＋ ' + t('ai.dock.newConv', '开新会话');
  nw.addEventListener('click', () => { AI._closeConvMenu(); AI.newConv(); });
  m.appendChild(nw);

  document.body.appendChild(m);
  const r0 = anchor.getBoundingClientRect();
  const mw = m.offsetWidth || 280;
  m.style.left = Math.max(8, Math.min(r0.left, window.innerWidth - mw - 8)) + 'px';
  m.style.top = Math.max(8, r0.top - m.offsetHeight - 6) + 'px';
  AI._convMenu = m;
  setTimeout(() => {
    document.addEventListener('mousedown', AI._convOutside, true);
  }, 0);
  window.addEventListener('resize', AI._closeConvMenu, {once: true});
};

AI._convOutside = function (ev) {
  const m = AI._convMenu;
  if (!m) return;
  if (m.contains(ev.target) || ev.target === $('aiConvBtn')) return;
  AI._closeConvMenu();
};

AI._closeConvMenu = function () {
  if (AI._convMenu) { try { AI._convMenu.remove(); } catch (e) {} }
  AI._convMenu = null;
  document.removeEventListener('mousedown', AI._convOutside, true);
};

/* 切到某段历史会话：换会话 id → 重挂 ss → 清屏 → 恢复。
 * 原会话的 localStorage 键原地保留，随时能切回来。 */
AI.switchConv = function (id) {
  if (!id || id === AI.convId) return;
  if (AI.busy) {
    AI.sysNote(t('ai.dock.switchBusy', '⏳ 正在生成中，请先停止再切换会话'));
    return;
  }
  if (AI._backlog) { AI._backlog.remove(); AI._backlog = null; AI._backlogN = 0; }
  AI.convId = id;
  AI._ss.set(AI_CONV_KEY, id);
  AI.messages = [];
  AI._topUpConv = '';                       // 换会话后允许服务端补差一次
  $('aiMsgs').innerHTML = '';
  if (!AI._restoreHistory()) AI.greet();
  AI._serverTopUp();                        // 跨浏览器场景：本地空但服务端有
  if (typeof AI.rebuildTurnNav === 'function') AI.rebuildTurnNav();
};

/* 开一段全新的空会话（不动任何已有会话） */
AI.newConv = function () {
  if (AI.busy) {
    AI.sysNote(t('ai.dock.switchBusy', '⏳ 正在生成中，请先停止再切换会话'));
    return;
  }
  const id = 'c' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
  AI.convId = id;
  AI._ss.set(AI_CONV_KEY, id);
  AI.messages = [];
  AI._topUpConv = '';
  if (AI._backlog) { AI._backlog.remove(); AI._backlog = null; AI._backlogN = 0; }
  $('aiMsgs').innerHTML = '';
  AI.greet();
};

/* 把事件水位推到服务端当前最大 id：清空之后旧事件不再回放。
 * 服务端环形缓冲重启后会归零，这里只往「更大」的方向推，不覆盖回退。 */
AI._dropReplay = async function () {
  try {
    const r = await fetch('/api/ai/events?since=0');
    if (!r.ok) return;
    const d = await r.json();
    const evs = Array.isArray(d.events) ? d.events : [];
    const maxId = evs.reduce((m, e) => Math.max(m, Number(e.id) || 0), 0);
    if (maxId > AI.lastEventId) {
      AI.lastEventId = maxId;
      AI._ss.set(AI_EID_KEY, maxId);
    }
  } catch (e) { /* 拿不到水位就算了，下次开流再说 */ }
};

/* ---------- 快捷指令（2026-11-02 扩充）----------
 * 原来的 4 个固定 chip 与处境无关、常常只有孤零零一行。现在按上下文动态给：
 *   · 没选样品 / 一个样品都没有 → 先给「从哪开始」这类入门提示
 *   · 有样品 → 解读 / 下一步 / 参数 / 报告
 *   · 有任务在跑 → 「还要多久」；有失败 → 「为什么失败」
 * 默认露 6 条，其余收在「更多」里（开合状态记 localStorage）。
 * 增删一条 = AI._chipDefs() 里加一项 + i18n 里加 ai.dock.chip<X> 与 ai.dock.q<X>。
 * 悬停显示完整问题（title 直接给 prompt），点了立即发送。 */
const AI_CHIPS_MORE_KEY = 'vp_ai_chips_more';
const AI_CHIPS_VISIBLE = 6;
try { AI._chipsMore = localStorage.getItem(AI_CHIPS_MORE_KEY) === '1'; }
catch (e) { AI._chipsMore = false; }

AI._chipCtx = function () {
  const c = AI._ctx || {};
  return { sample: !!AI.sample, samples: Number(c.samples) || 0,
           running: Number(c.running) || 0, failed: Number(c.failed) || 0 };
};

AI._chipDefs = function () {
  const has = () => !!AI.sample;
  return [
    { id: 'start', k: 'ai.dock.chipStart', z: '从哪开始',
      w: c => (!has() || !c.samples) ? 0 : 95,
      when: c => !has() || !c.samples,
      q: () => t('ai.dock.qStart',
        '我刚接触这个平台，请给我一条最短上手路径：先做什么、再做什么、每一步在哪里点，'
        + '并说明走到哪一步我能看到第一个结果。') },
    { id: 'interpret', k: 'ai.dock.chipInterpret', z: '解读这个样品',
      w: () => has() ? 2 : 92,
      when: () => has(),
      q: () => has()
        ? t('ai.dock.qInterpret', '请解读这个样品的分析结果：检出了什么病毒、各阶段质量如何、有什么需要注意的问题。')
        : t('ai.dock.qNoSample', '平台目前有哪些样品？各自的分析进行到哪一步了？') },
    { id: 'next', k: 'ai.dock.chipNext', z: '下一步建议', w: () => 3,
      q: () => has()
        ? t('ai.dock.qNext', '基于这个样品目前的结果，下一步建议做什么？请给出可执行的具体步骤和理由。')
        : t('ai.dock.qNextAny', '我刚接触这个平台，建议从哪里开始分析？') },
    { id: 'tasks', k: 'ai.dock.chipTasks', z: '任务状态', w: () => 4,
      q: () => t('ai.dock.qTasks', '现在平台里有哪些分析任务？各自运行到哪一步、状态如何？') },
    { id: 'eta', k: 'ai.dock.chipEta', z: '还要多久',
      w: c => c.running > 0 ? 0 : 96,
      when: c => c.running > 0,
      q: () => t('ai.dock.qEta',
        '现在有任务在跑，请根据它们的进度告诉我：每个任务跑到哪一步、'
        + '大概还要多久、有没有卡住或排队的风险。') },
    { id: 'fail', k: 'ai.dock.chipFail', z: '为什么失败',
      w: c => c.failed > 0 ? 1 : (has() ? 40 : 90),
      when: c => has() || c.failed > 0,
      q: () => t('ai.dock.qFail',
        '这个样品（或最近失败的任务）为什么失败 / 卡住了？请给出具体原因、'
        + '对应的报错位置，以及按顺序该怎么修。') },
    { id: 'where', k: 'ai.dock.chipWhere', z: '结果在哪看', w: () => 5,
      q: () => t('ai.dock.qWhere',
        '分析产物都落在哪些目录和文件里？请按阶段告诉我最该先看的那几个文件'
        + '（含路径），以及每个文件能回答什么问题。') },
    { id: 'params', k: 'ai.dock.chipParams', z: '参数建议', w: () => 6,
      q: () => has()
        ? t('ai.dock.qParams', '如果要对这个样品用更优参数重新分析，当前哪些参数值得调整？请结合已有结果给出具体建议值和理由。')
        : t('ai.dock.qParamsAny', '新建样品分析时，哪些参数最值得根据数据特点调整？') },
    { id: 'report', k: 'ai.dock.chipReport', z: '出结论文档',
      w: () => has() ? 7 : 97,
      when: () => has(),
      q: () => t('ai.dock.qReport',
        '给这个样品生成一份结论文档（report_v2.html）：包含检出的病毒、定量与组装'
        + '指标、逐阶段结论、缺口与下一步。先告诉我它会写到哪里、需要什么前置条件。') },
    { id: 'batch', k: 'ai.dock.chipBatch', z: '批量跑多个', w: () => 20,
      q: () => t('ai.dock.qBatch',
        '我有多个数据集要一起跑「已知病毒识别与定量」。请给出批量建样品并排队执行的'
        + '具体步骤、并发上限，以及怎么在不盯着的情况下知道哪几个跑完了。') },
    { id: 'toolbox', k: 'ai.dock.chipToolbox', z: '只做单步', w: () => 21,
      q: () => t('ai.dock.qToolbox',
        '我不想跑全流程，只想做单步分析（例如组装、鉴定、引物设计）。'
        + '工具箱里该选哪个工具、参数怎么填、产物落在哪里？') },
    { id: 'search', k: 'ai.dock.chipSearch', z: '公共检索', w: () => 22,
      q: () => t('ai.dock.qSearch',
        '我想先从公共数据库检索某个病毒的参考序列或原始数据再分析。'
        + '平台里的公共检索/下载怎么用？检索到的数据会落到哪里？') },
    { id: 'submit', k: 'ai.dock.chipSubmit', z: 'NCBI 提交', w: () => 23,
      q: () => t('ai.dock.qSubmit',
        '要把这条病毒的基因组提交到 NCBI，现有结果够不够？还缺什么、'
        + '按什么顺序准备（清单/注释/表格），提交前要核对哪些字段？') },
    { id: 'archive', k: 'ai.dock.chipArchive', z: '清理与归档', w: () => 24,
      q: () => t('ai.dock.qArchive',
        '任务中心里已完成的任务怎么归档或清理？归档之后还能恢复吗？'
        + '哪些操作不可逆、做之前要确认什么？') },
  ];
};

/* 点一条提示词 = 把对应问题发出去 */
AI.chip = function (kind) {
  const def = AI._chipDefs().filter(d => d.id === kind)[0];
  if (!def) return;
  AI.send(def.q());
};

/* 按当前上下文重画提示词行；容器不存在（老页面/非坞）就安静返回 */
AI.renderChips = function () {
  const box = $('aiChips');
  if (!box) return;
  const c = AI._chipCtx();
  /* 先按 w(c) 排序：正有任务在跑 →「还要多久」排第一；有失败 →「为什么失败」紧跟；
     选了样品 →「解读这个样品」提前。默认露出的 6 条始终是当下最有用的那批。 */
  const rank = d => (typeof d.w === 'function' ? d.w(c) : 50);
  const defs = AI._chipDefs()
    .filter(d => !d.when || d.when(c))
    .map((d, i) => ({ d: d, i: i, r: rank(d) }))
    .sort((a, b) => (a.r - b.r) || (a.i - b.i))
    .map(x => x.d);
  const shown = AI._chipsMore ? defs : defs.slice(0, AI_CHIPS_VISIBLE);
  const frag = document.createDocumentFragment();
  shown.forEach(d => {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'ai-chip';
    b.textContent = t(d.k, d.z);
    b.title = d.q();
    b.onclick = () => AI.chip(d.id);
    frag.appendChild(b);
  });
  if (defs.length > AI_CHIPS_VISIBLE) {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'ai-chip ai-chip-more';
    b.textContent = AI._chipsMore
      ? t('ai.dock.chipLess', '收起 ▴')
      : t('ai.dock.chipMore', '更多 ▾');
    b.onclick = () => {
      AI._chipsMore = !AI._chipsMore;
      try { localStorage.setItem(AI_CHIPS_MORE_KEY, AI._chipsMore ? '1' : '0'); } catch (e) {}
      AI.renderChips();
    };
    frag.appendChild(b);
  }
  box.innerHTML = '';
  box.appendChild(frag);
};

/* ---------- 发送与流式接收（可中断） ---------- */

// 发送 / 中断二合一：空闲时是发送键，生成中变成停止键
AI.sendOrStop = function () {
  if (AI.busy && AI._ctrl) { AI._ctrl.abort(); return; }
  AI.send();
};

/* ---------- 附件（2026-10-30） ----------
 * 用户点「＋」用全站文件对话框选文件，选中的路径做成芯片挂在输入框上方；
 * 发送时随请求带给服务端，由 chat.py 注入上下文（名字/大小/文本预览）。
 * 文件本身不上传、不复制——平台与数据在同一台机器上，模型拿路径就能用。 */
AI.attach = function () {
  if (typeof window.browse !== 'function') {
    if (typeof toast === 'function') toast(t('ai.dock.attach', '附件'), '文件对话框未加载');
    return;
  }
  const box = $('aiAttachPath');
  if (!box) return;
  box.value = '';
  browse('aiAttachPath');
};

AI.addAttachment = function (path) {
  const p = String(path || '').trim();
  if (!p) return;
  if (AI.attachments.some(a => a.path === p)) return;
  if (AI.attachments.length >= 8) {
    if (typeof toast === 'function') {
      toast(t('ai.dock.attach', '附件'), t('ai.dock.attachMax', '最多 8 个附件'));
    }
    return;
  }
  AI.attachments.push({name: p.split(/[\\/]/).pop(), path: p});
  AI._renderAttach();
};

AI.removeAttachment = function (i) {
  AI.attachments.splice(i, 1);
  AI._renderAttach();
};

AI._renderAttach = function () {
  const box = $('aiAttachChips');
  if (!box) return;
  box.hidden = !AI.attachments.length;
  box.innerHTML = AI.attachments.map((a, i) =>
    `<span class="ai-attach-chip" title="${esc(a.path)}">📎 ${esc(a.name)}` +
    `<button type="button" data-ai-act="attach-remove" data-i="${i}" ` +
    `title="${esc(t('ai.dock.attachRemove', '移除'))}">✕</button></span>`).join('');
};

/* ---------- 工作空间（2026-10-30） ----------
 * 「选择工作空间」= 选一个本地目录，右侧文件面板直接浏览它。目录记在
 * localStorage（vp_ai_ws），下次进总览沿用；与样品根、工具运行根并列可选。 */
AI.WS_KEY = 'vp_ai_ws';

AI.pickWorkspace = function () {
  if (typeof window.browseDir !== 'function') return;
  const box = $('aiWorkspacePath');
  if (!box) return;
  box.value = '';
  browseDir('aiWorkspacePath');
};

AI.setWorkspace = function (path) {
  const p = String(path || '').trim();
  if (!p) return;
  try { localStorage.setItem(AI.WS_KEY, p); } catch (e) {}
  AI._renderWorkspace();
  if (window.VPFiles && VPFiles.setRoot) VPFiles.setRoot(p);
  /* 上次已经把「工作空间 = 输出目录」定下来过，这次选完直接沿用，
     不再每次追问；没定过就只在栏里留一个「📥 输出到此」按钮。 */
  if (AI.wsOutWanted()) AI.applyOutputToWorkspace(true);
};

AI.clearWorkspace = function () {
  try { localStorage.removeItem(AI.WS_KEY); } catch (e) {}
  AI._renderWorkspace();
  if (window.VPFiles && VPFiles.clearRoot) VPFiles.clearRoot();
};

AI.workspacePath = function () {
  try { return localStorage.getItem(AI.WS_KEY) || ''; } catch (e) { return ''; }
};

/* 把工作空间当作输出根目录（2026-11-02）。
 * 用户诉求：「任务的输出目录默认在我们选择的工作空间，方便找结果」。
 * 平台的「输出目录」设置本来就是全局的（设置中心 → 输出目录），这里只是
 * 给它一个贴着工作空间的入口：一键把 output_root 设成工作空间，
 * 之后新任务的结果落在 <工作空间>\results\<样品>。
 * 记住选择（vp_ai_ws_out）：下次选同一类工作空间不再问。 */
AI.WS_OUT_KEY = 'vp_ai_ws_out';

AI.wsOutWanted = function () {
  try { return localStorage.getItem(AI.WS_OUT_KEY) === '1'; } catch (e) { return false; }
};

/* 归一化路径：Windows 大小写不敏感、允许末尾分隔符不同 */
function _samePath(a, b) {
  const n = (s) => String(s || '').replace(/[\\/]+/g, '\\')
    .replace(/\\+$/, '').toLowerCase();
  const x = n(a), y = n(b);
  return !!x && x === y;
}

/* POST /api/settings 的统一封装：失败时把 Flask 的 HTML 错误页压成一行文字，
   否则用户只会看到「HTTP false」这种没法排查的东西。 */
function _postSettings(body) {
  return fetch('/api/settings', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body || {})
  })
    .then(r => r.text().then(txt => {
      let d = null;
      try { d = JSON.parse(txt); } catch (e) {}
      if (!r.ok) {
        let msg = (d && (d.error || d.message)) || txt || ('HTTP ' + r.status);
        msg = String(msg).replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim();
        return {ok: false, error: msg.slice(0, 200) || ('HTTP ' + r.status)};
      }
      if (d && d.ok === false) {
        return {ok: false, error: String(d.error || d.message || '保存失败')};
      }
      return {ok: true, data: d || {}};
    }))
    .catch(e => ({ok: false, error: String((e && e.message) || e)}));
}

/* 以服务端的 output_root 为准（localStorage 只是"想不想"的偏好）。
   设置中心里改过输出目录也能同步过来，不会出现按钮状态和实际不一致。 */
AI._outRoot = '';
AI.syncOutputState = function (done) {
  fetch('/api/settings').then(r => r.json())
    .then(d => {
      AI._outRoot = String((d && d.output_root) || '');
      AI._renderWsOut();
      if (typeof done === 'function') done(AI._outRoot);
    })
    .catch(() => { AI._renderWsOut(); });
};

AI._renderWsOut = function () {
  const p = AI.workspacePath();
  const btn = $('aiWsOutBtn');
  const clr = $('aiWsOutClear');
  const st = $('aiWsOutState');
  const on = !!(p && _samePath(p, AI._outRoot));
  if (btn) btn.hidden = !p || on;
  if (clr) clr.hidden = !on;
  if (st) {
    st.hidden = !on;
    if (on) st.textContent = t('ai.ws.outOn', '输出目录 = 工作空间');
  }
};

AI.applyOutputToWorkspace = function (silent) {
  const p = AI.workspacePath();
  if (!p) return;
  const st = $('aiWsOutState');
  const say = (s) => { if (st) { st.hidden = false; st.textContent = s; } };
  if (!silent) say(t('ai.ws.outSetting', '正在切换输出目录…'));
  _postSettings({output_root: p})
    .then(res => {
      if (!res.ok) { say('⚠ ' + res.error); return; }
      try { localStorage.setItem(AI.WS_OUT_KEY, '1'); } catch (e) {}
      AI._outRoot = p;
      AI._renderWsOut();
      if (!silent) {
        say('✔ ' + t('ai.ws.outDone', '新任务的结果会写到') + ' ' + p + '\\results');
      }
    });
};

AI.clearOutputWorkspace = function () {
  const p = AI.workspacePath();
  const st = $('aiWsOutState');
  const say = (s) => { if (st) { st.hidden = false; st.textContent = s; } };
  _postSettings({output_root: ''})
    .then(res => {
      if (!res.ok) { say('⚠ ' + res.error); return; }
      try { localStorage.removeItem(AI.WS_OUT_KEY); } catch (e) {}
      AI._outRoot = '';
      AI._renderWsOut();
      say('✔ ' + t('ai.ws.outOff', '已恢复平台默认输出目录'));
      if (p) { /* 保持工作空间，只是不再当输出根 */ }
    });
};


AI._renderWorkspace = function () {
  const p = AI.workspacePath();
  const name = $('aiWorkspaceName');
  const clear = $('aiWorkspaceClear');
  if (!name) return;
  if (p) {
    const base = p.split(/[\\/]/).filter(Boolean).pop() || p;
    name.textContent = base;
    name.title = p;
    name.hidden = false;
    if (clear) clear.hidden = false;
  } else {
    name.textContent = '';
    name.hidden = true;
    if (clear) clear.hidden = true;
  }
  AI._renderWsOut();
};

/* 输入框粘贴路径即视为附件：Windows「复制为路径」带引号，也接受裸盘符路径。
   剪贴板里是"文件"（资源管理器 Ctrl+C / 截图）时先上传到 run/uploads 再附加
   ——浏览器拿不到本机路径，只能走平台既有的 /api/upload。
   只拦"整段就是一个路径"的粘贴，普通文本粘贴不受影响。 */
AI._maybeAttachPaste = function (e) {
  const dt = e.clipboardData || window.clipboardData;
  if (!dt) return;
  if (dt.files && dt.files.length) {
    e.preventDefault();
    AI._uploadFiles(dt.files);
    return;
  }
  let txt = String(dt.getData('text') || '').trim();
  if (!txt || txt.length > 500 || txt.indexOf('\n') >= 0) return;
  if (/^file:\/\//i.test(txt)) {
    try { txt = decodeURIComponent(txt.replace(/^file:\/\/\/?/i, '')); } catch (x) {}
  }
  txt = txt.replace(/^"|"$/g, '');
  const isPath = /^[A-Za-z]:[\\/]/.test(txt) || /^\\\\/.test(txt);
  if (!isPath) return;
  e.preventDefault();
  AI.addAttachment(txt);
};

/* 粘贴/拖入的文件 → /api/upload → run/uploads/<name>，再作为附件 */
AI._uploadFiles = function (files) {
  const arr = Array.prototype.slice.call(files || []);
  if (!arr.length) return;
  arr.forEach(function (f) {
    const fd = new FormData();
    fd.append('file', f);
    fetch('/api/upload', { method: 'POST', body: fd })
      .then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (d) {
          if (!r.ok) throw new Error(d.error || ('HTTP ' + r.status));
          return d;
        });
      })
      .then(function (d) { AI.addAttachment(d.path); })
      .catch(function (err) {
        if (typeof toast === 'function') {
          toast(t('ai.dock.attach', '附件'), String(err.message || err), { kind: 'failed' });
        }
      });
  });
};

AI.send = async function (preset) {
  const inp = $('aiInput');
  const text = (preset !== undefined ? preset : (inp ? inp.value : '')).trim();
  if (!text) return;
  AI._mentionHide();
  /* 本地斜杠命令（2026-11-04）：/tasks /env /samples /digest /help。
   * 完全不经过模型、不进 AI.messages、不落历史；未知 /xxx 也只在本地
   * 提示命令清单，不发给模型。 */
  if (String(text).charAt(0) === '/' && AI._slashCmd(text)) {
    if (preset === undefined && inp) inp.value = '';
    return;
  }
  if (AI.busy) {
    // 静默 return 会让人觉得「按了没反应」，反复敲回车
    if (!AI._busyWarn) {
      AI._busyWarn = 1;
      AI.sysNote(t('ai.dock.busyHint',
        '⏳ 正在生成中：先点 ■ 停止，再发下一条'));
      setTimeout(() => { AI._busyWarn = 0; }, 3000);
    }
    return;
  }
  /* 开坞后立刻回车：_restoreHistory 可能还没跑完，先发新提问会让旧历史
     push 到它后面，下一轮上下文顺序错乱。发送前等一次上下文就位。 */
  if (!AI.ctxLoaded) { try { await AI.ensureCtx(); } catch (e) {} }
  if (preset === undefined && inp) inp.value = '';
  const atts = AI.attachments.slice();
  AI.messages.push({role: 'user', content: text});
  AI._saveHistory();
  const ub = _bubble('user');
  ub.textContent = text;
  if (atts.length) {
    const at = document.createElement('div');
    at.className = 'ai-msg-atts';
    at.textContent = atts.map(a => '📎 ' + a.name).join('  ');
    ub.appendChild(at);
  }
  /* 2026-11-06：每条提问下方挂「↻ 重新上一句」（气泡内部，随截断一起移除） */
  _addRetryRow(ub);
  AI.attachments = [];
  AI._renderAttach();

  const bb = _bubble('assistant');
  /* 思考过程（2026-10-30）：推理模型的思维链（reasoning_content）与工具
     轨迹放在一个可折叠块里，答案正文单独一块。出正文/结束时自动折叠。 */
  const ans = document.createElement('div');
  ans.className = 'ai-answer';
  ans.innerHTML = `<span class="ai-hint">${t('ai.dock.thinking', '思考中…')}</span>`;
  bb.appendChild(ans);
  const think = {el: null, body: null, sum: null, reason: null,
                 tools: null, steps: 0};

  function ensureThink() {
    if (think.el) return;
    const d = document.createElement('details');
    d.className = 'ai-think';
    d.open = true;
    d.innerHTML = '<summary><span class="ai-think-ico">🧠</span>' +
      '<span class="ai-think-sum">' +
      esc(t('ai.dock.thinkingProcess', '思考过程')) + ' · ' +
      esc(t('ai.dock.thinkingRunning', '进行中…')) +
      '</span></summary><div class="ai-think-body"></div>';
    bb.insertBefore(d, ans);
    think.el = d;
    think.body = d.querySelector('.ai-think-body');
    think.sum = d.querySelector('.ai-think-sum');
    think.reason = document.createElement('div');
    think.reason.className = 'ai-reason';
    think.body.appendChild(think.reason);
    think.tools = document.createElement('div');
    think.tools.className = 'ai-think-tools';
    think.body.appendChild(think.tools);
    const tl = document.createElement('a');
    tl.className = 'ai-think-link';
    tl.href = '/tasks';
    tl.target = '_blank';
    tl.rel = 'noopener';
    tl.textContent = t('ai.dock.tasksLink', '任务进度 ›');
    think.body.appendChild(tl);
  }
  function updateThinkSum(done) {
    if (!think.sum) return;
    think.sum.textContent =
      t('ai.dock.thinkingDoneTpl', '思考过程（{n} 步）')
        .replace('{n}', String(think.steps))
      + (done ? '' : ' · ' + t('ai.dock.thinkingRunning', '进行中…'));
  }
  function collapseThink() {
    if (!think.el) return;
    updateThinkSum(true);
    if (think.el.open) think.el.open = false;
  }

  let acc = '';
  let aborted = false;
  let artsItems = null;      // 本轮产物卡片（2026-11-02），收尾时随回答落盘
  let nextActions = null;    // 本轮下一步建议（2026-11-04，done 前到达，收尾时渲染）
  /* 流式渲染节流：模型每个 token 都重排整段 HTML 会卡顿；60ms 合帧一次，
   * 且只在用户贴底时自动滚动（用户往上翻历史时不抢位置）。 */
  let paintTimer = 0;
  let lastPaint = 0;
  let firstPaint = true;
  const paint = () => {
    paintTimer = 0;
    const stick = _atBottom();
    ans.innerHTML = aiFmt(acc);
    lastPaint = Date.now();
    if (stick) _scroll();
  };
  const schedulePaint = () => {
    if (paintTimer) return;
    const wait = firstPaint ? 0 : Math.max(0, 55 - (Date.now() - lastPaint));
    firstPaint = false;
    paintTimer = setTimeout(paint, wait);
  };
  AI.busy = true;
  AI._syncRetryRows();                 // 生成中所有「重新上一句」不可点（F7）
  AI._mentionHide();                  // 发送时收起 @ 补全下拉（F3）
  AI._ctrl = (typeof AbortController !== 'undefined') ? new AbortController() : null;
  const btn = $('aiSendBtn');
  btn.textContent = '■';
  btn.title = t('ai.dock.stop', '停止生成');
  btn.disabled = false;
  /* P0-1：流式期间周期性落盘。回答生成到一半被切页/切模块，回来还能看到
   * 已经生成的部分（标注为「生成中被打断」），不再是一片空白。 */
  AI._persistStart(() => acc);
  try {
    await AI._stream({messages: AI.messages, sample: AI.sample,
                      workspace: AI.workspacePath(),
                      plan_mode: AI.planMode || 'auto',
                      attachments: atts.map(a => a.path)}, ev => {
      if (ev.type === 'reasoning') {
        ensureThink();
        const stick = _atBottom();
        think.reason.textContent += ev.content || '';
        if (stick) _scroll();
      } else if (ev.type === 'delta') {
        acc += ev.content || '';
        collapseThink();          // 正文一开始就折叠思考过程
        schedulePaint();
      } else if (ev.type === 'tool') {
        const stick = _atBottom();
        ensureThink();
        think.steps += 1;   // 跳过的重复调用也算一步（芯片划线展示）
        const chip = document.createElement('span');
        chip.className = 'ai-toolchip' + (ev.skipped ? ' skipped' : '');
        chip.textContent = `🔧 ${ev.name}(${ev.skipped ? '重复，已跳过'
          : Object.keys(ev.args || {}).length ? JSON.stringify(ev.args) : ''})`;
        think.tools.appendChild(chip);
        /* 工具透明化（2026-11-04）：后端给的人读摘要（如
           "read_result_table(sample=…) · 30 行"）贴在芯片旁，小号灰字、
           单行省略；完整内容进 title，悬停可看。steps 计数口径不变。 */
        if (ev.detail) {
          const det = String(ev.detail);
          const ds = document.createElement('span');
          ds.className = 'ai-toolchip-detail';
          ds.textContent = det;
          ds.title = det;
          think.tools.appendChild(ds);
          chip.title = det;
        }
        updateThinkSum(false);
        if (stick) _scroll();
      } else if (ev.type === 'next_actions') {
        /* 下一步建议（2026-11-04）：done 之前到达，先暂存在本轮局部状态里，
           收尾时统一渲染成一排按钮。只取前 3 条，防止模型刷屏。
           同时把流式 delta 里已经显示的 ```next_actions``` 围栏块清掉：
           后端只从落库正文删，不会补发修正后的 delta。 */
        const arr = Array.isArray(ev.actions) ? ev.actions : [];
        nextActions = arr.filter(a => a && (a.label || a.action)).slice(0, 3);
        const cleaned = _stripNextActionsBlock(acc);
        if (cleaned !== acc) {
          acc = cleaned;
          const stick = _atBottom();
          if (paintTimer) { clearTimeout(paintTimer); paintTimer = 0; }
          ans.innerHTML = aiFmt(acc);
          if (stick) _scroll();
        }
      } else if (ev.type === 'tick') {
        /* 服务端心跳（每 5s 一帧）：让「思考中…」变成「已等待 N 秒」，
           长推理/长工具轮期间用户能确认它还活着，而不是判定卡死去刷新。 */
        const h = ans.querySelector('.ai-hint');
        if (h) h.textContent = t('ai.dock.thinkingSec', '思考中… 已等待 {s} 秒')
          .replace('{s}', String(ev.elapsed || 0));
      } else if (ev.type === 'notice') {
        /* 后端主动告知（2026-11-02）：工具降级、预算自动加倍、
           回答被截断等——以前只埋在 done 事件里，用户看不见。 */
        const stick = _atBottom();
        const s = document.createElement('div');
        s.className = 'ai-hint' + (ev.level === 'warn' ? ' ai-warn' : '');
        s.textContent = (ev.level === 'warn' ? '⚠ ' : 'ℹ ')
          + String(ev.message || '');
        bb.appendChild(s);
        if (stick) _scroll();
      } else if (ev.type === 'artifacts') {
        /* 产物卡片（2026-11-02）：后端把「这一轮的工具结果 + 回答正文」里
           真实存在、且在平台目录内的文件整理成清单发下来。以前只给裸路径，
           用户只能自己去资源管理器里找。 */
        const stick = _atBottom();
        const items = Array.isArray(ev.items) ? ev.items : [];
        if (items.length) {
          artsItems = items;
          try { AI.renderArts(bb, items); } catch (e) { /* 卡片失败不影响正文 */ }
          if (stick) _scroll();
        }
      } else if (ev.type === 'error') {
        // 报错了就别再挂着「思考中…」——以前两个同时显示，用户一直空等
        ans.innerHTML = '';
        const e = document.createElement('div');
        e.className = 'ai-err';
        e.innerHTML = esc(ev.message || 'error')
          + (String(ev.message || '').includes('设置')
             ? ` <a href="/settings#aiCard">${t('ai.dock.goSettings', '去配置')} ›</a>` : '');
        bb.appendChild(e);
      }
    }, AI._ctrl ? AI._ctrl.signal : undefined);
  } catch (e) {
    if (e && (e.name === 'AbortError' || String(e).includes('abort'))) {
      aborted = true;
      const s = document.createElement('div');
      s.className = 'ai-hint';
      s.textContent = `⏹ ${t('ai.dock.aborted', '已停止生成（保留以上内容）')}`;
      bb.appendChild(s);
    } else {
      const er = document.createElement('div');
      er.className = 'ai-err';
      er.textContent = t('ai.dock.busyFail', '请求失败') + ': ' + e;
      bb.appendChild(er);
    }
  } finally {
    AI.busy = false;
    AI._ctrl = null;
    AI._syncRetryRows();               // 忙闲变化时同步「重新上一句」可点状态
    btn.textContent = '➤';
    btn.title = t('ai.dock.send', '发送');
    collapseThink();
    if (paintTimer) { clearTimeout(paintTimer); paintTimer = 0; }
    if (!bb.querySelector('.ai-err') && !aborted && !acc
        && !(nextActions && nextActions.length)) {
      ans.innerHTML =
        `<span class="ai-hint">${t('ai.dock.empty2', '（无返回内容）')}</span>`;
    } else if (acc) {
      ans.innerHTML = aiFmt(acc);   // 收尾补一次完整渲染（表格/围栏闭合）
    }
    /* P0-1 收尾：撤掉流式期间的 partial 草稿，换成最终文本。
     * 用户点「停止」留下的半截回答仍按旧口径处理——界面上留给人看，
     * 但不写进对话历史（否则下一轮模型会接着残句往下写）。
     * 产物卡片随这次落盘一起写入（2026-11-06）：服务端镜像比较包含
     * artifacts，二次保存不再被当作"前缀相同"忽略。 */
    AI._persistFinish((acc && !aborted) ? acc : '', false,
                      (artsItems && artsItems.length && !aborted)
                        ? artsItems : null);
    /* 反馈（2026-11-04）：落盘完成后再取下标，保证 msg_idx 与 AI.messages
     * 完全对齐（_persistFinish 会先 pop partial 再 push 最终文本）。 */
    let msgIdx = -1;
    if (!aborted && acc) {
      const last = AI.messages[AI.messages.length - 1];
      if (last && last.role === 'assistant') msgIdx = AI.messages.length - 1;
    }
    if (acc) {
      AI._foldAnswer(bb);               // F5：超长回答折叠
      _addCopyBar(bb, acc, msgIdx);     // F6：复制条 + 👍👎
    }
    /* F1：下一步建议按钮（错误/中断的回答不挂建议；正文为空但带建议
       时同样要渲染，否则按钮会被空回答吞掉）。 */
    if (nextActions && nextActions.length && !aborted
        && !bb.querySelector('.ai-err')) {
      AI._renderNextActions(bb, nextActions);
    }
    // 用户点「停止」留下的半截回答：只留在界面上给人看，不进对话历史
    // （否则下一轮模型会接着残句往下写，用户以为那就是完整版）。
    _scroll(false);
  }
};

AI._stream = async function (body, onEvent, signal) {
  const r = await fetch('/api/ai/chat', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body), signal});
  if (!r.ok) {
    let msg = `HTTP ${r.status}`;
    try { msg = (await r.json()).error || msg; } catch (e) {}
    onEvent({type: 'error', message: msg});
    return;
  }
  const reader = r.body.getReader();
  const dec = new TextDecoder();
  let buf = '';
  const drain = () => {
    let i;
    while ((i = buf.indexOf('\n\n')) >= 0) {
      const frame = buf.slice(0, i).trim();
      buf = buf.slice(i + 2);
      if (!frame.startsWith('data:')) continue;
      try { onEvent(JSON.parse(frame.slice(5).trim())); } catch (e) {}
    }
  };
  for (;;) {
    const {done, value} = await reader.read();
    if (done) break;
    buf += dec.decode(value, {stream: true});
    drain();
  }
  // 连接在末帧中途断开时，缓冲区里还留着最后一段（可能是正文最后一句）。
  // 以前直接丢弃：用户看到的回答少一截，历史也少一截且毫无提示。
  buf += dec.decode();
  if (buf.trim()) { buf += '\n\n'; drain(); }
};

/* ---------- 产物卡片（2026-11-02） ----------
 * 用户抱怨「回答只给一段裸路径，点不开也看不出是什么」。后端把这一轮真实
 * 落盘的文件整理成 artifacts 事件发下来，这里渲染成卡片：
 *   图片 → 缩略图（点开看大图）；HTML → 内嵌预览 / 新标签打开；
 *   表格 → 前几十行；其他 → 直接打开。
 * 一律走 /api/fs/raw（同源、只读平台目录），前端不再自己拼路径。 */
const ART_ICON = {html: '📄', image: '🖼', table: '📊', text: '📝',
                  pdf: '📕', file: '📎'};

function _fmtSize(n) {
  const v = Number(n) || 0;
  if (v >= 1048576) return (v / 1048576).toFixed(1) + ' MB';
  if (v >= 1024) return Math.round(v / 1024) + ' KB';
  return v + ' B';
}

function _artCard(it, idx) {
  const card = document.createElement('div');
  card.className = 'ai-art ai-art-' + (it.kind || 'file');
  const top = document.createElement('div');
  top.className = 'ai-art-top';
  if (it.kind === 'image') {
    const im = document.createElement('img');
    im.className = 'ai-art-thumb';
    im.loading = 'lazy';
    im.alt = it.name || '';
    im.src = it.url;
    im.title = it.path || '';
    im.setAttribute('data-art-act', 'preview');
    im.setAttribute('data-art-i', String(idx));
    card.appendChild(im);
  }
  const ico = document.createElement('span');
  ico.className = 'ai-art-ico';
  ico.textContent = ART_ICON[it.kind] || '📎';
  const nm = document.createElement('span');
  nm.className = 'ai-art-name';
  nm.textContent = it.name || '(未命名)';
  nm.title = it.path || '';
  top.appendChild(ico);
  top.appendChild(nm);
  card.appendChild(top);
  const meta = document.createElement('div');
  meta.className = 'ai-art-meta';
  meta.textContent = _fmtSize(it.size) + (it.dir ? ' · ' + it.dir : '');
  meta.title = it.path || '';
  card.appendChild(meta);
  const ops = document.createElement('div');
  ops.className = 'ai-art-ops';
  const previewable = (it.kind === 'html' || it.kind === 'image'
                       || it.kind === 'table' || it.kind === 'text');
  if (previewable) {
    const b = document.createElement('button');
    b.className = 'btn small';
    b.type = 'button';
    b.textContent = t('ai.art.preview', '预览');
    b.setAttribute('data-art-act', 'preview');
    b.setAttribute('data-art-i', String(idx));
    ops.appendChild(b);
  }
  const a = document.createElement('a');
  a.className = 'btn small';
  a.href = it.url;
  a.target = '_blank';
  a.rel = 'noopener';
  a.textContent = t('ai.art.open', '打开');
  a.setAttribute('data-art-act', 'open');
  a.setAttribute('data-art-i', String(idx));
  ops.appendChild(a);
  const cp = document.createElement('button');
  cp.className = 'btn small';
  cp.type = 'button';
  cp.textContent = t('ai.art.copy', '复制路径');
  cp.setAttribute('data-art-act', 'copy');
  cp.setAttribute('data-art-i', String(idx));
  ops.appendChild(cp);
  card.appendChild(ops);
  return card;
}

AI.renderArts = function (host, items) {
  if (!host || !items || !items.length) return null;
  const wrap = document.createElement('div');
  wrap.className = 'ai-arts';
  wrap._items = items;
  const head = document.createElement('div');
  head.className = 'ai-arts-head';
  head.textContent = t('ai.art.title', '产物') + '（' + items.length + '）';
  wrap.appendChild(head);
  const grid = document.createElement('div');
  grid.className = 'ai-arts-grid';
  items.forEach((it, i) => grid.appendChild(_artCard(it, i)));
  wrap.appendChild(grid);
  wrap.addEventListener('click', (e) => {
    const el = e.target && e.target.closest
      ? e.target.closest('[data-art-act]') : null;
    if (!el) return;
    const act = el.getAttribute('data-art-act');
    const idx = parseInt(el.getAttribute('data-art-i'), 10) || 0;
    const item = wrap._items[idx];
    if (!item) return;
    if (act === 'open') return;              // <a target=_blank> 自己会跳
    e.preventDefault();
    if (act === 'copy') {
      const p = item.path || '';
      try {
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(p);
        }
      } catch (err) { /* 剪贴板不可用则静默 */ }
      el.textContent = t('ai.art.copied', '已复制');
      setTimeout(() => { el.textContent = t('ai.art.copy', '复制路径'); }, 1200);
      return;
    }
    AI.openArt(item);
  });
  host.appendChild(wrap);
  return wrap;
};

/* 旧历史没有 artifacts 字段（升级前的回答）：把正文交给服务端解析一次，
 * 拿回卡片补渲染，并写回 AI.messages 免得每次刷新都重问。
 * 失败就静默——卡片是锦上添花，不能因为网络抖动把历史渲染搞挂。 */
AI._backfillArts = function (list) {
  if (!list || !list.length) return;
  const texts = list.map(x => String(x.text || '').slice(0, 20000));
  fetch('/api/ai/artifacts', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({texts})
  }).then(r => r.ok ? r.json() : null).then(d => {
    if (!d || !Array.isArray(d.results)) return;
    let dirty = false;
    d.results.forEach((items, i) => {
      const rec = list[i];
      if (!rec || !items || !items.length) return;
      try { AI.renderArts(rec.el, items); } catch (e) { return; }
      const msg = AI.messages[rec.idx];
      if (msg && msg.role === 'assistant') { msg.artifacts = items; dirty = true; }
    });
    if (dirty) { try { AI._saveHistory(); } catch (e) { /* 静默 */ } }
  }).catch(() => { /* 静默 */ });
};

function _artModal() {
  let m = document.getElementById('aiArtModal');
  if (m) return m;
  m = document.createElement('div');
  m.id = 'aiArtModal';
  m.className = 'ai-art-modal';
  m.hidden = true;
  m.innerHTML = '<div class="ai-art-dlg" role="dialog" aria-modal="true">'
    + '<div class="ai-art-dlg-head"><span class="ai-art-dlg-name"></span>'
    + '<span class="ai-art-dlg-ops">'
    + '<a class="btn small ai-art-dlg-open" target="_blank" rel="noopener">'
    + '<span data-art-openlabel></span></a>'
    + '<button class="btn small" type="button" data-art-close>'
    + '<span data-art-closelabel></span></button></span></div>'
    + '<div class="ai-art-dlg-body"></div></div>';
  m.addEventListener('click', (e) => {
    if (e.target === m || (e.target.closest && e.target.closest('[data-art-close]'))) {
      AI.closeArt();
    }
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') AI.closeArt();
  });
  document.body.appendChild(m);
  return m;
}

AI.closeArt = function () {
  const m = document.getElementById('aiArtModal');
  if (!m) return;
  m.hidden = true;
  const body = m.querySelector('.ai-art-dlg-body');
  if (body) body.innerHTML = '';      // 停掉 iframe 里的脚本/播放
};

AI.openArt = function (item) {
  if (!item || !item.url) return;
  const m = _artModal();
  const nm = m.querySelector('.ai-art-dlg-name');
  const body = m.querySelector('.ai-art-dlg-body');
  const openA = m.querySelector('.ai-art-dlg-open');
  const openLb = m.querySelector('[data-art-openlabel]');
  const closeLb = m.querySelector('[data-art-closelabel]');
  if (nm) { nm.textContent = item.name || ''; nm.title = item.path || ''; }
  if (openA) openA.href = item.url;
  if (openLb) openLb.textContent = t('ai.art.openNew', '在新标签打开');
  if (closeLb) closeLb.textContent = t('ai.art.close', '关闭');
  if (!body) return;
  body.innerHTML = '';
  if (item.kind === 'image') {
    const im = document.createElement('img');
    im.className = 'ai-art-full';
    im.src = item.url;
    im.alt = item.name || '';
    body.appendChild(im);
  } else if (item.kind === 'html') {
    const fr = document.createElement('iframe');
    fr.className = 'ai-art-frame';
    /* 2026-11-06：预览的是平台目录里的任意 HTML，可能来自分析产物之外
     * （用户附加文件、工具输出）。必须给沙箱：不保留 allow-same-origin
     * （否则脚本能读平台同源 API/LocalStorage），也不给脚本/表单/弹窗
     * 之外的权限；需要交互时点右上「在新标签打开」。 */
    fr.setAttribute('sandbox', '');
    fr.src = item.url;
    body.appendChild(fr);
    const hint = document.createElement('div');
    hint.className = 'ai-art-hint';
    hint.textContent = t('ai.art.htmlHint',
      '内嵌预览在沙箱中加载（脚本不执行）；可点「在新标签打开」查看原文件。');
    body.appendChild(hint);
  } else {
    const pre = document.createElement('pre');
    pre.className = 'ai-art-text';
    pre.textContent = t('ai.art.loading', '读取中…');
    body.appendChild(pre);
    fetch('/api/fs/read?path=' + encodeURIComponent(item.path || '')
          + '&max_bytes=60000')
      .then(r => r.json().then(d => ({ok: r.ok, d})).catch(() => ({ok: false, d: {}})))
      .then(({ok, d}) => {
        if (!ok || d.error) {
          pre.textContent = t('ai.art.readFail', '读取失败') + '：'
            + ((d && d.error) || '') + '\n' + (item.path || '');
          return;
        }
        const lines = String(d.text || '').split(/\r?\n/).slice(0, 40);
        pre.textContent = lines.join('\n')
          + (d.truncated ? '\n' + t('ai.art.truncated', '…（已截断，打开文件看全部）') : '');
      })
      .catch(e => { pre.textContent = String(e); });
  }
  m.hidden = false;
};

/* ==================================================================
   本地斜杠命令（F4，2026-11-04）
   /tasks /env /samples /digest /help 全在本地渲染：不调模型、不进
   AI.messages、不写 localStorage。信息气泡带「本地」徽标，明确它不是
   AI 回答；未知 /xxx 也只在本地提示命令清单，绝不发给模型。
   ================================================================== */
function _aiFetchJson(url) {
  return fetch(url, {cache: 'no-store'}).then(r =>
    r.json().catch(() => ({})).then(d => {
      if (!r.ok) {
        throw new Error((d && (d.error || d.detail)) || ('HTTP ' + r.status));
      }
      return d;
    }));
}

function _briefVal(v, max) {
  if (v == null) return '';
  let s;
  if (Array.isArray(v)) {
    s = v.slice(0, 4).map(x => (x && typeof x === 'object')
      ? JSON.stringify(x) : String(x)).join(', ');
    if (v.length > 4) s += ' …';
  } else if (typeof v === 'object') {
    const ks = Object.keys(v);
    s = ks.slice(0, 6).map(k => {
      const x = v[k];
      const val = Array.isArray(x) ? '[' + x.length + ']'
        : (x && typeof x === 'object') ? '{…}' : String(x);
      return k + '=' + val;
    }).join(' · ');
    if (ks.length > 6) s += ' …';
  } else {
    s = String(v);
  }
  s = s.replace(/\s+/g, ' ').trim();
  return s.length > max ? s.slice(0, max) + '…' : s;
}

/* 本地信息气泡：.ai-info 带「本地」徽标；不参与对话历史 */
AI._infoBubble = function (html) {
  const n = document.createElement('div');
  n.className = 'ai-msg ai-bot ai-info';
  n.innerHTML = `<div class="ai-info-head">`
    + `<span class="ai-info-badge">${esc(t('ai.slash.local', '本地'))}</span>`
    + `<span class="ai-info-note">${esc(t('ai.slash.localHint',
        '这是本地渲染，不是 AI 回答'))}</span></div>`
    + `<div class="ai-info-body">${html || ''}</div>`;
  const box = $('aiMsgs');
  if (box) box.appendChild(n);
  _scroll();
  return n;
};

AI._slashHelpHtml = function () {
  const rows = [
    ['/tasks', t('ai.slash.helpTasks', '任务概览：运行中 / 排队 / 最近完成')],
    ['/env', t('ai.slash.helpEnv', '环境概览：数据库 / 磁盘 / 输出根')],
    ['/samples', t('ai.slash.helpSamples', '样品清单与阶段进度')],
    ['/digest', t('ai.slash.helpDigest', '样品摘要（/digest 样品名）')],
    ['/help', t('ai.slash.helpHelp', '显示这份命令清单')],
  ];
  return `<div class="ai-info-title">⌨ ${esc(t('ai.slash.helpTitle', '本地命令'))}</div>`
    + `<ul class="ai-info-list">`
    + rows.map(r => `<li><code>${esc(r[0])}</code> ${esc(r[1])}</li>`).join('')
    + `</ul>`;
};

AI._slashCmd = function (text) {
  const m = /^\/([A-Za-z][A-Za-z0-9_-]*)(?:\s+([\s\S]*))?$/
    .exec(String(text || '').trim());
  if (!m) return false;
  const cmd = m[1].toLowerCase();
  const arg = String(m[2] || '').trim();
  if (['tasks', 'env', 'samples', 'digest', 'help'].indexOf(cmd) < 0) {
    AI._infoBubble(`<div class="ai-info-line">${esc(
      t('ai.slash.unknown', '未知命令：{c}').replace('{c}', '/' + cmd))}</div>`
      + AI._slashHelpHtml());
    return true;
  }
  if (cmd === 'tasks') AI._cmdTasks();
  else if (cmd === 'env') AI._cmdEnv();
  else if (cmd === 'samples') AI._cmdSamples();
  else if (cmd === 'digest') AI._cmdDigest(arg);
  else AI._infoBubble(AI._slashHelpHtml());
  return true;
};

/* 任务状态 → 本地化文案（/tasks /samples 共用；未知状态原样显示） */
function _taskStTxt(st) {
  const s = String(st || '');
  const map = {
    running: t('ai.slash.running', '运行中'),
    queued: t('ai.slash.queued', '排队'),
    paused: t('ai.slash.paused', '已暂停'),
    done: t('ai.task.done', '✓ 已完成'),
    failed: t('ai.task.failed', '✘ 失败'),
    cancelled: t('ai.task.cancelled', '已停止'),
    interrupted: t('ai.task.cancelled', '已停止'),
  };
  return map[s] || s;
}

AI._cmdTasks = function () {
  _aiFetchJson('/api/tasks?logs=0').then(d => {
    const rows = Array.isArray(d) ? d
      : (Array.isArray(d && d.active) ? d.active : []);
    const title = `<div class="ai-info-title">📋 ${esc(
      t('ai.slash.tasksTitle', '任务概览'))}</div>`;
    if (!rows.length) {
      AI._infoBubble(title + `<div class="ai-info-empty">${esc(
        t('ai.slash.noTasks', '当前没有任务'))}</div>`);
      return;
    }
    const running = rows.filter(x => x && (x.status === 'running'
      || x.status === 'queued'));
    const queued = running.filter(x => /排队|queue/i.test(
      String(x.msg || '') + ' ' + String(x.stage || '')));
    const recent = rows.filter(x => x && ['done', 'failed', 'cancelled',
      'interrupted'].indexOf(String(x.status)) >= 0).slice(0, 5);
    const rowHtml = (x, forceSt) => {
      const raw = forceSt || String(x.status || '');
      const label = _taskStTxt(raw);
      const pct = (typeof x.pct === 'number' && isFinite(x.pct))
        ? Math.round(x.pct * 100) + '%' : '';
      return `<li><span class="ai-info-name">${esc(
          String(x.name || x.id || ''))}</span>`
        + `<span class="ai-info-st ${esc(raw)}">${esc(label)}</span>`
        + (pct ? `<span class="ai-info-pct">${esc(pct)}</span>` : '')
        + `</li>`;
    };
    let html = title
      + `<div class="ai-info-stats">`
      + `<span>${esc(t('ai.slash.running', '运行中'))} <b>`
      + esc(String(running.length - queued.length)) + `</b></span>`
      + `<span>${esc(t('ai.slash.queued', '排队'))} <b>`
      + esc(String(queued.length)) + `</b></span>`
      + `<span>${esc(t('ai.slash.recent', '最近完成'))} <b>`
      + esc(String(recent.length)) + `</b></span></div>`;
    if (running.length) {
      html += `<ul class="ai-info-list">`
        + running.slice(0, 6).map(x => rowHtml(x,
            queued.indexOf(x) >= 0 ? 'queued'
                                   : String(x.status || ''))).join('')
        + `</ul>`;
    }
    if (recent.length) {
      html += `<div class="ai-info-sub">${esc(
        t('ai.slash.recent', '最近完成'))}</div>`
        + `<ul class="ai-info-list">` + recent.map(x => rowHtml(x)).join('')
        + `</ul>`;
    }
    AI._infoBubble(html);
  }).catch(err => AI._infoBubble(
    `<div class="ai-info-line">⚠ ${esc(t('ai.slash.loadFail', '读取失败'))} · `
    + esc(String((err && err.message) || err)) + `</div>`));
};

AI._cmdEnv = function () {
  Promise.all([
    _aiFetchJson('/api/settings').catch(() => ({})),
    _aiFetchJson('/api/dbs').catch(() => ({})),
    _aiFetchJson('/api/env_status').catch(() => ({})),
  ]).then(([s, dbs, env]) => {
    const db = key => (dbs && dbs[key]) || {};
    const badge = (label, it) => {
      const ok = !!(it && it.ready);
      return `<span class="ai-info-db ${ok ? 'ok' : 'no'}">${esc(label)} · `
        + `${esc(ok ? t('ai.slash.ready', '就绪')
                    : t('ai.slash.notReady', '未就绪'))}</span>`;
    };
    let disk = '—';
    try {
      const bits = (env && env.bits) || [];
      const b = bits.filter(x => x && x.id === 'disk')[0];
      if (b && b.summary) disk = String(b.summary);
    } catch (e) { /* 保持 — */ }
    const out = (s && s.output_root) ? String(s.output_root)
      : t('ai.slash.defaultRoot', '平台默认（run/）');
    AI._infoBubble(`<div class="ai-info-title">⚙ ${esc(
        t('ai.slash.envTitle', '环境概览'))}</div>`
      + `<div class="ai-info-dbs">`
      + badge(t('ai.slash.dbTaxonomy', '分类库'), db('taxonomy'))
      + badge(t('ai.slash.dbHost', '宿主库'), db('host'))
      + badge(t('ai.slash.dbVirus', '病毒库'), db('virus'))
      + `</div><ul class="ai-info-list">`
      + `<li>${esc(t('ai.slash.diskFree', '磁盘剩余'))}: <b>${esc(disk)}</b></li>`
      + `<li>${esc(t('ai.slash.outRoot', '输出根'))}: <code>${esc(out)}</code></li>`
      + `</ul>`);
  });
};

AI._cmdSamples = function () {
  _aiFetchJson('/api/samples').then(d => {
    const arr = Array.isArray(d) ? d
      : (Array.isArray(d && d.samples) ? d.samples : []);
    const title = `<div class="ai-info-title">🧪 ${esc(
      t('ai.slash.samplesTitle', '样品'))}</div>`;
    if (!arr.length) {
      AI._infoBubble(title + `<div class="ai-info-empty">${esc(
        t('ai.slash.noSamples', '还没有样品'))}</div>`);
      return;
    }
    const list = arr.slice(0, 40).map(x => {
      const name = typeof x === 'string' ? x
        : String((x && x.name) || '');
      const done = Number(x && x.done) || 0;
      const total = Number(x && x.total) || 0;
      const st = String((x && x.last_status) || '');
      return `<li><span class="ai-info-name">${esc(name)}</span>`
        + `<span class="ai-info-pct">${esc(t('ai.slash.stagesTpl',
            '阶段 {done}/{total}')
            .replace('{done}', String(done))
            .replace('{total}', String(total)))}</span>`
        + (st ? `<span class="ai-info-st ${esc(st)}">${esc(
            _taskStTxt(st))}</span>` : '')
        + `</li>`;
    }).join('');
    AI._infoBubble(title + `<ul class="ai-info-list">${list}</ul>`
      + (arr.length > 40 ? `<div class="ai-info-line">…</div>` : ''));
  }).catch(err => AI._infoBubble(
    `<div class="ai-info-line">⚠ ${esc(t('ai.slash.loadFail', '读取失败'))} · `
    + esc(String((err && err.message) || err)) + `</div>`));
};

AI._cmdDigest = function (name) {
  name = String(name || '').trim();
  if (!name) {
    AI._getSamples().then(raw => {
      const names = AI._sampleNames(raw).slice(0, 20);
      AI._infoBubble(`<div class="ai-info-line">${esc(
          t('ai.slash.digestUsage', '用法：/digest 样品名'))}</div>`
        + (names.length
           ? `<div class="ai-info-sub">${esc(
                t('ai.slash.samplesTitle', '样品'))}</div>`
             + `<ul class="ai-info-list">`
             + names.map(n => `<li><code>${esc(n)}</code></li>`).join('')
             + `</ul>`
           : ''));
    });
    return;
  }
  _aiFetchJson('/api/ai/context?sample=' + encodeURIComponent(name))
    .then(d => {
      const s = d && d.sample;
      if (!s) {
        AI._infoBubble(`<div class="ai-info-line">⚠ ${esc(
          t('ai.slash.digestMissing', '样品不存在：{s}')
            .replace('{s}', name))}</div>`);
        return;
      }
      const stages = Array.isArray(s.stages_done) ? s.stages_done : [];
      let html = `<div class="ai-info-title">🔎 ${esc(
        t('ai.slash.digestTitle', '样品摘要'))} · ${esc(
        String(s.sample || name))}</div>`;
      html += `<ul class="ai-info-list"><li>${esc(
        t('ai.slash.digestStages', '已完成阶段'))}: <b>`
        + esc(String(stages.length)) + `</b>`
        + (stages.length ? ` · <code>${esc(stages.join(' → '))}</code>` : '')
        + `</li>`;
      const fp = s.fastp;
      if (fp && typeof fp === 'object') {
        const bits = Object.keys(fp)
          .map(k => k + '=' + _briefVal(fp[k], 60)).join(' · ');
        if (bits) {
          html += `<li>${esc(t('ai.slash.digestFastp', '质控（fastp）'))}: `
            + `<b>${esc(bits)}</b></li>`;
        }
      }
      html += `</ul>`;
      const det = s.detail;
      if (det && typeof det === 'object') {
        const keys = Object.keys(det);
        if (keys.length) {
          html += `<div class="ai-info-sub">${esc(
            t('ai.slash.digestDetail', '阶段摘要'))}</div>`
            + `<ul class="ai-info-list">`
            + keys.map(k => `<li><b>${esc(k)}</b>: ${esc(
                _briefVal(det[k], 180))}</li>`).join('')
            + `</ul>`;
        }
      }
      if (!stages.length && !(det && Object.keys(det).length)) {
        html += `<div class="ai-info-empty">${esc(
          t('ai.slash.digestEmpty', '（该样品暂无可展示的摘要）'))}</div>`;
      }
      AI._infoBubble(html);
    }).catch(err => AI._infoBubble(
      `<div class="ai-info-line">⚠ ${esc(t('ai.slash.loadFail', '读取失败'))} · `
      + esc(String((err && err.message) || err)) + `</div>`));
};

/* ==================================================================
   @ 样品补全（F3，2026-11-04）
   光标前最近的 @token（@ 后连续非空白）触发下拉；数据源 /api/samples
   缓存 60s；↑↓ 选择、Enter 确认、Esc 关闭、点击确认。
   ================================================================== */
AI._getSamples = function () {
  const now = Date.now();
  const c = AI._samplesCache || (AI._samplesCache = {t: 0, raw: null, p: null});
  if (c.raw && (now - c.t) < 60000) return Promise.resolve(c.raw);
  if (c.p) return c.p;
  c.p = fetch('/api/samples', {cache: 'no-store'})
    .then(r => r.ok ? r.json() : [])
    .then(d => {
      const arr = Array.isArray(d) ? d
        : (Array.isArray(d && d.samples) ? d.samples : []);
      AI._samplesCache = {t: Date.now(), raw: arr, p: null};
      return arr;
    })
    .catch(() => { c.p = null; return c.raw || []; });
  return c.p;
};

AI._sampleNames = function (raw) {
  return (raw || []).map(x => typeof x === 'string' ? x
    : String((x && x.name) || '')).filter(Boolean);
};

AI._mentionHide = function () {
  if (AI._mention && AI._mention.parentNode) {
    AI._mention.parentNode.removeChild(AI._mention);
  }
  AI._mention = null;
  AI._mentionItems = [];
  AI._mentionIdx = -1;
  AI._mentionToken = null;
};

AI._mentionShow = function (names, start, end) {
  AI._mentionHide();
  const host = document.querySelector('.ai-inputrow');
  if (!host) return;
  const m = document.createElement('div');
  m.className = 'ai-mention';
  m.setAttribute('role', 'listbox');
  AI._mentionItems = names;
  AI._mentionIdx = 0;
  AI._mentionToken = {start, end};
  names.forEach((name, i) => {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'ai-mention-item' + (i === 0 ? ' on' : '');
    b.setAttribute('role', 'option');
    b.textContent = name;                 // 样品名可含中文/符号，纯文本
    b.addEventListener('mousedown', e => e.preventDefault()); // 保持输入焦点
    b.addEventListener('click', () => AI._mentionPick(i));
    m.appendChild(b);
  });
  const hint = document.createElement('div');
  hint.className = 'ai-mention-hint';
  hint.textContent = t('ai.mention.hint', '↑↓ 选择 · Enter 确认 · Esc 关闭');
  m.appendChild(hint);
  host.appendChild(m);
  AI._mention = m;
};

AI._mentionHighlight = function (i) {
  if (!AI._mention) return;
  const items = AI._mention.querySelectorAll('.ai-mention-item');
  if (!items.length) return;
  const n = items.length;
  AI._mentionIdx = ((i % n) + n) % n;
  Array.prototype.forEach.call(items, (el, k) => {
    el.classList.toggle('on', k === AI._mentionIdx);
  });
  const cur = items[AI._mentionIdx];
  if (cur && cur.scrollIntoView) {
    try { cur.scrollIntoView({block: 'nearest'}); } catch (e) {}
  }
};

AI._mentionPick = function (i) {
  const name = AI._mentionItems[i];
  const tok = AI._mentionToken;
  const inp = $('aiInput');
  if (!name || !tok || !inp) return;
  const v = String(inp.value || '');
  /* 以「当前光标前的 @token」为准（下拉渲染后用户可能又敲了一个字符，
   * 用旧的 end 会把那个字符留在样品名后面）。取不到才退回展示时的范围。 */
  const caret0 = typeof inp.selectionStart === 'number'
    ? inp.selectionStart : v.length;
  const b0 = v.slice(0, caret0);
  const m0 = /(^|[\s(（])@([^\s@]*)$/.exec(b0);
  const start = m0 ? caret0 - (m0[2] || '').length - 1 : tok.start;
  const end = m0 ? caret0 : tok.end;
  const rep = '@' + name + ' ';
  inp.value = v.slice(0, start) + rep + v.slice(end);
  const caret = start + rep.length;
  try { inp.setSelectionRange(caret, caret); } catch (e) {}
  AI._mentionHide();
  inp.focus();
  try { inp.dispatchEvent(new Event('input', {bubbles: true})); } catch (e) {}
};

AI._mentionUpdate = function () {
  const inp = $('aiInput');
  if (!inp) return;
  const caret = typeof inp.selectionStart === 'number'
    ? inp.selectionStart : String(inp.value || '').length;
  const before = String(inp.value || '').slice(0, caret);
  const m = /(^|[\s(（])@([^\s@]*)$/.exec(before);
  if (!m) { AI._mentionHide(); return; }
  const q = m[2] || '';
  AI._getSamples().then(raw => {
    const inp2 = $('aiInput');
    if (!inp2 || !inp2.isConnected) return;
    const c2 = typeof inp2.selectionStart === 'number'
      ? inp2.selectionStart : 0;
    const b2 = String(inp2.value || '').slice(0, c2);
    const m2 = /(^|[\s(（])@([^\s@]*)$/.exec(b2);
    if (!m2 || (m2[2] || '') !== q) return;   // 输入已变：放弃这次渲染
    const names = AI._sampleNames(raw)
      .filter(n => !q || n.toLowerCase().indexOf(q.toLowerCase()) >= 0)
      .slice(0, 8);
    if (!names.length) { AI._mentionHide(); return; }
    AI._mentionShow(names, c2 - q.length - 1, c2);
  });
};

AI._mentionKey = function (e) {
  if (!AI._mention) return false;
  if (e.key === 'Escape') {
    e.preventDefault();
    AI._mentionHide();
    return true;
  }
  if (e.key === 'ArrowDown') {
    e.preventDefault();
    AI._mentionHighlight(AI._mentionIdx + 1);
    return true;
  }
  if (e.key === 'ArrowUp') {
    e.preventDefault();
    AI._mentionHighlight(AI._mentionIdx - 1);
    return true;
  }
  if ((e.key === 'Enter' || e.key === 'Tab') && AI._mentionIdx >= 0) {
    e.preventDefault();
    AI._mentionPick(AI._mentionIdx);
    return true;
  }
  return false;
};

/* ==================================================================
   ↻ 重试菜单（F7，2026-11-04）
   原样重试 = AI.retryLast()；带一句纠正 = 把纠正作为新 user 消息
   走 AI.send(note)，不删除上一轮。定位沿用历史会话下拉的做法（挂 body、
   fixed、向上弹），点击外部关闭；生成中按钮禁用。
   ================================================================== */
AI.toggleRetryMenu = function (anchorBtn) {
  if (AI._retryMenu) { AI._closeRetryMenu(); return; }
  if (AI.busy) {
    AI.sysNote(t('ai.retry.busy', '⏳ 正在生成中，请先停止再重试'));
    return;
  }
  /* 2026-11-06：锚点从工具条 #aiRetryBtn 改为消息内的重试按钮本身；
     记住锚点与所属气泡，原样重试按该气泡截断（AI.retryFrom）。 */
  const anchor = anchorBtn;
  if (!anchor) return;
  AI._retryAnchor = anchor;
  AI._retryBubble = anchorBtn.closest('.ai-msg.ai-user');
  const m = document.createElement('div');
  m.className = 'ai-retry-menu';
  const head = document.createElement('div');
  head.className = 'ai-retry-head';
  head.textContent = t('ai.retry.menuTitle', '重试上一问');
  m.appendChild(head);
  const mkItem = (label, tip, fn) => {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'ai-retry-item';
    b.textContent = label;
    b.title = tip || '';
    b.addEventListener('click', fn);
    m.appendChild(b);
    return b;
  };
  mkItem(t('ai.retry.same', '原样重试'),
    t('ai.retry.sameTip', '丢弃上一轮回答，用同样的提问重新生成'),
    () => { AI._closeRetryMenu(); AI.retryFrom(AI._retryBubble); });
  const row = document.createElement('div');
  row.className = 'ai-retry-note';
  row.hidden = true;
  const inp = document.createElement('input');
  inp.type = 'text';
  inp.className = 'ai-retry-input';
  inp.maxLength = 500;
  inp.placeholder = t('ai.retry.notePh', '这次要改什么？');
  const go = document.createElement('button');
  go.type = 'button';
  go.className = 'btn small primary';
  go.textContent = t('ai.retry.send', '重试');
  const submit = () => {
    const note = String(inp.value || '').trim();
    if (!note) { inp.focus(); return; }
    AI._closeRetryMenu();
    AI.send(note);
  };
  go.addEventListener('click', submit);
  inp.addEventListener('keydown', e => {
    if (e.key === 'Enter') { e.preventDefault(); submit(); }
  });
  row.appendChild(inp);
  row.appendChild(go);
  const withNote = mkItem(t('ai.retry.withNote', '带一句纠正重试'),
    t('ai.retry.withNoteTip', '把纠正作为新消息发送，保留上一轮'),
    () => {
      row.hidden = !row.hidden;
      if (!row.hidden) {
        /* 输入行展开后菜单变高：重算 top，保持底边仍贴着按钮上方 */
        const r1 = anchor.getBoundingClientRect();
        m.style.top = Math.max(8, r1.top - m.offsetHeight - 6) + 'px';
        inp.focus();
      }
    });
  m.insertBefore(row, withNote.nextSibling);

  document.body.appendChild(m);
  const r0 = anchor.getBoundingClientRect();
  const mw = m.offsetWidth || 260;
  m.style.left = Math.max(8,
    Math.min(r0.left + r0.width - mw, window.innerWidth - mw - 8)) + 'px';
  m.style.top = Math.max(8, r0.top - m.offsetHeight - 6) + 'px';
  AI._retryMenu = m;
  setTimeout(() => {
    document.addEventListener('mousedown', AI._retryOutside, true);
  }, 0);
  window.addEventListener('resize', AI._closeRetryMenu, {once: true});
};

AI._retryOutside = function (ev) {
  const m = AI._retryMenu;
  if (!m) return;
  const a = AI._retryAnchor;
  if (m.contains(ev.target)
    || (a && (ev.target === a || a.contains(ev.target)))) return;
  AI._closeRetryMenu();
};

AI._closeRetryMenu = function () {
  if (AI._retryMenu) { try { AI._retryMenu.remove(); } catch (e) {} }
  AI._retryMenu = null;
  document.removeEventListener('mousedown', AI._retryOutside, true);
};

AI._syncRetryRows = function () {
  if (AI.busy) AI._closeRetryMenu();
  const box = $('aiMsgs');
  if (!box) return;
  box.querySelectorAll('.ai-msg-act-retry').forEach(b => {
    b.disabled = !!AI.busy;
    b.title = AI.busy
      ? t('ai.retry.busy', '⏳ 正在生成中，请先停止再重试')
      : t('ai.dock.retry', '重试上一问（可选带一句纠正）');
  });
};

/* ---------- 启动（延迟到 load） ----------
 * 本脚本随 _nav.html 在页面顶部执行，而 $/esc/t 来自页面底部的
 * app.js / i18n.js —— 必须等 load 再绑定输入框与启动事件流，
 * 否则全局函数尚不存在（TDZ ReferenceError）。 */
(() => {
  const boot = () => {
    const inp = $('aiInput');
    if (!inp) return;                    // 本页无导航坞（理论不会发生）
    inp.addEventListener('keydown', e => {
      /* @ 补全打开时先吃掉方向键/Enter/Esc（2026-11-04，F3） */
      if (AI._mentionKey(e)) return;
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); AI.send(); }
    });
    inp.addEventListener('input', () => {
      inp.style.height = 'auto';
      inp.style.height = Math.min(inp.scrollHeight, 120) + 'px';
      AI._mentionUpdate();               // 边打字边算 @token
    });
    /* 光标移动（方向键/Home/End）或点击也要重算：@token 是否还在光标前 */
    inp.addEventListener('keyup', e => {
      if (e.key === 'Enter' || e.key === 'Escape') return;
      AI._mentionUpdate();
    });
    inp.addEventListener('click', () => AI._mentionUpdate());
    /* 点下拉以外的区域关掉 @ 补全（点输入框本身不关，方便继续输入） */
    document.addEventListener('mousedown', e => {
      if (!AI._mention) return;
      if (AI._mention.contains && AI._mention.contains(e.target)) return;
      if (e.target === inp) return;
      AI._mentionHide();
    }, true);
    // 附件：文件对话框选中后由 app-browse.js 派发 vp:filepicked
    const ap = $('aiAttachPath');
    if (ap) {
      ap.addEventListener('vp:filepicked', e => {
        AI.addAttachment(e.detail && e.detail.path);
      });
    }
    // 工作空间：目录对话框选中后派发 vp:dirpicked
    const wp = $('aiWorkspacePath');
    if (wp) {
      wp.addEventListener('vp:dirpicked', e => {
        AI.setWorkspace(e.detail && e.detail.path);
      });
    }
    inp.addEventListener('paste', AI._maybeAttachPaste);
    /* 卡片按钮统一走事件委托（data-ai-act），不再把 id/样品名/档案名拼进
     * inline onclick —— 名字里带单引号时按钮会静默失效，构造过的名字还能
     * 变成脚本（2026-11-02 审计项）。 */
    const msgsBox = $('aiMsgs');
    if (msgsBox) {
      msgsBox.addEventListener('click', (e) => {
        const b = e.target && e.target.closest
          ? e.target.closest('button[data-ai-act]') : null;
        if (!b) return;
        const kind = b.dataset.aiAct;
        if (kind === 'action') AI.action(b.dataset.aid, b.dataset.decision);
        else if (kind === 'gate') AI.gate(b.dataset.sample, b.dataset.stage,
                                          b.dataset.decision);
        else if (kind === 'attach-remove') AI.removeAttachment(parseInt(b.dataset.i, 10) || 0);
      });
    }
    /* 右侧历史提问导航：绑滚动监听 + 观察消息区变化（2026-11-02）。
       必须在 _restoreHistory 之前就位，刷新后的历史同样能打点。 */
    AI.initTurnNav();
    AI._renderWorkspace();
    /* 输出目录状态要和服务端对齐（设置中心可能改过），只在有工作空间条时问一次 */
    if ($('aiWsOutBtn')) AI.syncOutputState();
    // 样品跟随顶栏全局胶囊：用户在顶栏换样品后，下一轮对话自动带上新样品
    const syncSample = () => {
      AI.sample = (window.VP_CTX && VP_CTX.sample) || '';
      try { AI.renderChips(); } catch (e) { /* 顶栏换样品后提示词跟着换 */ }
    };
    syncSample();
    document.addEventListener('vp-ctx', syncSample);
    // 拖文件进对话坞 = 上传并附加（与粘贴文件同一条路径）
    const dock = $('aiDock');
    if (dock) {
      dock.addEventListener('dragover', e => {
        if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length) {
          e.preventDefault();
          dock.classList.add('ai-drop');
        }
      });
      dock.addEventListener('dragleave', () => dock.classList.remove('ai-drop'));
      dock.addEventListener('drop', e => {
        if (!e.dataTransfer || !e.dataTransfer.files.length) return;
        e.preventDefault();
        dock.classList.remove('ai-drop');
        AI._uploadFiles(e.dataTransfer.files);
      });
    }
    AI.startEvents();   // 常驻订阅主动播报（讲解/解读/闸门三层共用一条流）
    /* 进页面就恢复历史（2026-11-02）：不等网络、不等开坞。坞收着时也把
     * 消息铺进 #aiMsgs，点开即见，不会出现「开了坞先空一下再冒出来」。 */
    try { AI.restoreOnce(); } catch (e) { /* 静默 */ }
    // 上次是开着坞的，换页后照旧开着（历史由 boot 的 restoreOnce 接回）
    let wantOpen = false;
    try { wantOpen = localStorage.getItem(AI_OPEN_KEY) === '1'; } catch (e) {}
    if (wantOpen && $('aiDock')) AI.toggle(true);
    // 模型选择条：本页有坞就预取一次（归入上面的 load 时机，
    // 打开坞时无需再等一次网络往返）
    AI.loadModels();
  };
  if (document.readyState === 'complete') boot();
  else window.addEventListener('load', boot);
})();

/* 渲染器也挂到 AI 上：消息/播报/外部页面复用同一套标记解析 */
AI.fmt = aiFmt;
window.AI = AI;

}  // end 防重复包含

