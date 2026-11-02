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
  const want = (force === undefined) ? !AI.open : !!force;
  AI.open = want;
  $('aiDock').hidden = !want;
  $('aiFab').hidden = want;
  try { localStorage.setItem(AI_OPEN_KEY, want ? '1' : '0'); } catch (e) {}
  if (want) {
    AI.clearUnread();
    AI.ensureCtx();
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
    _scroll();
  } else if (ev.type === 'ai_read') {
    AI._bumpUnread(ev);
    const n = document.createElement('div');
    n.className = 'ai-msg ai-bot ai-read';
    n.innerHTML = `<div class="ai-read-head">✨ ${esc(t('ai.ev.read', 'AI 播报'))}`
      + ` · ${esc(ev.name || ev.stage || '')}</div>`
      + (ev.failure ? `<div class="ai-read-fail">✘ ${esc(t('ai.ev.failed', '阶段失败诊断'))}</div>` : '')
      + `<div class="ai-read-body">${aiFmt(ev.text || '')}</div>`;
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
    _scroll();
  } else if (ev.type === 'ai_gate') {
    AI._bumpUnread(ev);
    // 闸门会阻塞流水线：待决时自动弹出对话坞（唯一自动开坞的事件，
    // 必须有人拍板）。历史回放（backlog）不弹——刷新页面时旧闸门
    // 已决或已超时，弹坞纯属打扰
    if (ev.status === 'pending' && !AI.open && !ev.backlog) AI.toggle(true);
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
      + (pending ? `<div class="ai-gate-btns">`
         + `<button class="btn small primary" data-ai-act="gate"`
         + ` data-sample="${esc(ev.sample || '')}" data-stage="${esc(ev.stage || '')}"`
         + ` data-decision="continue">${esc(t('ai.ev.gateBtnGo', '▶ 继续下一阶段'))}</button>`
         + `<button class="btn small danger" data-ai-act="gate"`
         + ` data-sample="${esc(ev.sample || '')}" data-stage="${esc(ev.stage || '')}"`
         + ` data-decision="stop">${esc(t('ai.ev.gateBtnStop', '■ 终止任务'))}</button>`
         + `</div><span class="ai-gate-cd"></span>` : '');
    AI._append(n);
    if (pending) AI._startCountdown(n, ev.deadline);
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
    // 计划执行期间会多次上报 running，旧写法会每步新建一张卡刷屏）
    const oldA = [...document.querySelectorAll('.ai-action')]
      .find(n => n.dataset.aid === ev.aid);
    if (oldA && ev.status !== 'pending') {
      AI._flipActionCard(oldA, ev.status, ev.error, stTxt, ev);
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
      + (ev.stages && ev.stages.length
         ? `<div class="ai-act-stages">📑 ${esc((ev.stages || []).join(' → '))}</div>` : '')
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
  // 后端只推「已启动」：把 tid 挂上，卡片自己轮询任务进度
  if (ev && ev.tid && status !== 'rejected') AI._bindTask(node, ev.tid);
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
  try {
    const r = await fetch('/api/ai/actions/resolve', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({id: aid, decision})});
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
  try {
    // 样品跟随顶栏全局胶囊（2026-10-30 起坞里不再有独立样品下拉）
    AI.sample = (window.VP_CTX && VP_CTX.sample) || '';
    const q = AI.sample ? `?sample=${encodeURIComponent(AI.sample)}` : '';
    const d = await (await fetch('/api/ai/context' + q)).json();
    AI.ready = !!d.ready;
    $('aiReadyDot').hidden = false;
    $('aiReadyDot').classList.toggle('ok', AI.ready);
    $('aiReadyDot').classList.toggle('no', !AI.ready);
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
  } catch (e) { /* 存储不可用则静默 */ }
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
 * 至少让回来的人看得到「当时生成到哪」。 */
AI._persistFinish = function (finalText, keepDraft) {
  AI._persistStop();
  const last = AI.messages[AI.messages.length - 1];
  if (last && last.role === 'assistant' && last.partial) {
    if (keepDraft) { if (finalText) last.content = finalText; return; }
    AI.messages.pop();
  }
  if (finalText) {
    AI.messages.push({role: 'assistant', content: finalText});
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
  return arr.map(m => (m.partial
    ? {role: m.role, content: m.content, partial: 1}
    : {role: m.role, content: m.content}));
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
          for (const m of arr.slice(-AI_MSGS_CAP)) {
            if (!m || (m.role !== 'user' && m.role !== 'assistant')) continue;
            const b = _mkBubble(m.role);
            if (m.role === 'user') b.textContent = String(m.content || '');
            else {
              const ans = document.createElement('div');
              ans.className = 'ai-answer';
              ans.innerHTML = aiFmt(String(m.content || ''));
              b.appendChild(ans);
              _addCopyBar(b, String(m.content || ''));
              if (m.partial) {
                /* 上次是在流式中途被切走的：明确标注「未完」，别让人当成完整版 */
                const tag = document.createElement('div');
                tag.className = 'ai-sysnote';
                tag.textContent = t('ai.dock.partial',
                  '（这条回答在生成中被打断，只保存了已完成的部分）');
                b.appendChild(tag);
              }
            }
            AI.messages.push({role: m.role, content: String(m.content || '')});
            frag.appendChild(b);
          }
          box.appendChild(frag);        // ← 唯一一次挂载
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

/* 预览文案：提问气泡里可能挂着附件芯片（📎 文件名），预览时剔掉 */
AI._turnLabel = function (node) {
  const c = node.cloneNode(true);
  Array.prototype.forEach.call(c.querySelectorAll('.ai-msg-atts'),
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
     顶出消息区。上下内边距 8px 之外全部分给间隙，最少 1px。 */
  if (!nav.hidden) {
    const n = turns.length;
    const avail = nav.clientHeight - 8;
    nav.style.gap = Math.max(1, Math.min(8,
      Math.floor((avail - n * 12) / Math.max(1, n - 1)))) + 'px';
  } else {
    nav.style.gap = '';
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

/* 每条助手回答下方的“复制回答”条（复制用户看到的是纯文本）。 */
function _addCopyBar(bb, text) {
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
  bb.appendChild(bar);
}

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
  $('aiMsgs').innerHTML = '';
  if (!AI._restoreHistory()) AI.greet();
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

/* ---------- 快捷指令 ---------- */

AI.chip = function (kind) {
  const q = {
    interpret: () => AI.sample
      ? t('ai.dock.qInterpret', '请解读这个样品的分析结果：检出了什么、各阶段质量如何、有什么要注意的。')
      : t('ai.dock.qNoSample', '平台目前有哪些样品，各自分析进行到哪一步？'),
    tasks: () => t('ai.dock.qTasks', '现在平台里有哪些分析任务？运行状态如何？'),
    next: () => AI.sample
      ? t('ai.dock.qNext', '基于这个样品目前的结果，下一步建议做什么？请给出可执行的具体步骤。')
      : t('ai.dock.qNextAny', '我刚接触这个平台，建议从哪里开始？'),
    params: () => AI.sample
      ? t('ai.dock.qParams', '如果要对这个样品重新分析（换更优参数），当前参数有哪些可以调整？请结合结果给出具体建议值。')
      : t('ai.dock.qParamsAny', '新建样品分析时，哪些参数最值得根据数据特点调整？'),
  }[kind];
  AI.send(q ? q() : '');
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
};

AI.clearWorkspace = function () {
  try { localStorage.removeItem(AI.WS_KEY); } catch (e) {}
  AI._renderWorkspace();
  if (window.VPFiles && VPFiles.clearRoot) VPFiles.clearRoot();
};

AI.workspacePath = function () {
  try { return localStorage.getItem(AI.WS_KEY) || ''; } catch (e) { return ''; }
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
  const inp = $('aiInput');
  const text = (preset !== undefined ? preset : inp.value).trim();
  if (!text) return;
  /* 开坞后立刻回车：_restoreHistory 可能还没跑完，先发新提问会让旧历史
     push 到它后面，下一轮上下文顺序错乱。发送前等一次上下文就位。 */
  if (!AI.ctxLoaded) { try { await AI.ensureCtx(); } catch (e) {} }
  if (preset === undefined) inp.value = '';
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
        updateThinkSum(false);
        if (stick) _scroll();
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
    btn.textContent = '➤';
    btn.title = t('ai.dock.send', '发送');
    collapseThink();
    if (paintTimer) { clearTimeout(paintTimer); paintTimer = 0; }
    if (!bb.querySelector('.ai-err') && !aborted && !acc) {
      ans.innerHTML =
        `<span class="ai-hint">${t('ai.dock.empty2', '（无返回内容）')}</span>`;
    } else if (acc) {
      ans.innerHTML = aiFmt(acc);   // 收尾补一次完整渲染（表格/围栏闭合）
      _addCopyBar(bb, acc);
    }
    /* P0-1 收尾：撤掉流式期间的 partial 草稿，换成最终文本。
     * 用户点「停止」留下的半截回答仍按旧口径处理——界面上留给人看，
     * 但不写进对话历史（否则下一轮模型会接着残句往下写）。 */
    AI._persistFinish((acc && !aborted) ? acc : '', false);
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

/* ---------- 启动（延迟到 load） ----------
 * 本脚本随 _nav.html 在页面顶部执行，而 $/esc/t 来自页面底部的
 * app.js / i18n.js —— 必须等 load 再绑定输入框与启动事件流，
 * 否则全局函数尚不存在（TDZ ReferenceError）。 */
(() => {
  const boot = () => {
    const inp = $('aiInput');
    if (!inp) return;                    // 本页无导航坞（理论不会发生）
    inp.addEventListener('keydown', e => {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); AI.send(); }
    });
    inp.addEventListener('input', () => {
      inp.style.height = 'auto';
      inp.style.height = Math.min(inp.scrollHeight, 120) + 'px';
    });
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
    // 样品跟随顶栏全局胶囊：用户在顶栏换样品后，下一轮对话自动带上新样品
    const syncSample = () => {
      AI.sample = (window.VP_CTX && VP_CTX.sample) || '';
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
