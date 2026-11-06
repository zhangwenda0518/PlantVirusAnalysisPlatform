// ---------------- 任务轮询 + 结果预览 + 阶段卡片内嵌运行日志 + 上次结果恢复 ----------------
// 2026-09-13 自 app.js 拆出。普通 script 共享全局作用域：本文件在
// app.js 之后加载，可直接使用 $/esc/t/toast/jfetch 等核心工具；页面
// 内联脚本与其它 app-*.js 里的同名调用按全局解析（加载顺序见模板）。
// 注意：本块内引用的其它 app-*.js 函数均为调用期解析（事件/回调触发），
// 不存在加载期交叉依赖。
// ---------------- 任务轮询 + 结果预览 ----------------
let pollTimer = null;
const taskLogOpen = new Set();   // 展开日志的任务 id
const notifiedTasks = new Set(); // 已弹过结束通知的任务
let prevRunning = new Set();     // 上次轮询时运行中的任务

// 自适应轮询：有任务在跑时 2.5s（保持进度实时），全部空闲时降到 8s。
// 任务列表每 2.5s 全量回传，长时间挂着页面会产生大量无谓请求，
// 空闲降频可在不影响体验的前提下显著减轻服务端与浏览器负担。
const POLL_BUSY_MS = 2500;
const POLL_IDLE_MS = 8000;
let lastTaskList = [];
let connDown = false;

function startPolling() {
  if (pollTimer) clearTimeout(pollTimer);
  schedulePoll(0);
}

function schedulePoll(delay) {
  if (pollTimer) clearTimeout(pollTimer);
  pollTimer = setTimeout(async () => {
    // 递归调度不同于 setInterval：refreshTasks 抛异常会中断整条轮询链，
    // 这里必须兜住（原实现用 setInterval 不会有这个问题）
    try {
      await refreshTasks();
    } catch (e) {
      console.error('[poll] refreshTasks failed:', e);
    }
    const busy = lastTaskList.some(t => t.status === 'running') || connDown;
    schedulePoll(busy ? POLL_BUSY_MS : POLL_IDLE_MS);
  }, delay);
}

function taskStatusText(s) {
  return {running: t('c.st.running'), done: t('c.st.done'),
          failed: t('c.st.failed'), cancelled: t('c.st.cancelled'),
          paused: t('c.st.paused')}[s] || s;
}

/* 任务操作按钮：暂停/继续/取消。
   只有 pausable 的任务（下载类）在运行中给「暂停」；暂停后给「继续」+「取消」，
   取消是彻底停（会杀掉进程），暂停可以接着下。 */
function taskButtons(task) {
  const st = task.status;
  const id = task.id;
  if (st === 'running' || st === 'queued') {
    const pause = task.pausable
      ? `<button class="btn small" onclick="pauseTask('${id}')">${t('c.pause')}</button> ` : '';
    return pause + `<button class="btn small danger" onclick="cancelTask('${id}')">${t('c.cancel')}</button>`;
  }
  if (st === 'paused') {
    return `<button class="btn small" onclick="resumeTask('${id}')">${t('c.resume')}</button> ` +
      `<button class="btn small danger" onclick="cancelTask('${id}')">${t('c.cancel')}</button>`;
  }
  return '';
}

/* 任务结果预览面板（后端 result 字段为规范化预览结构） */
function taskResultHtml(res) {
  if (!res || !res.kind) return '';
  const chips = [];
  let links = [];
  if (res.kind === 'sample') {
    (res.stages || []).forEach(st => {
      if (st.status === 'done' && st.summary) {
        chips.push(`<span class="tr-chip" title="${esc(st.name)}">${esc(st.name)}：${esc(st.summary)}</span>`);
      }
    });
    if (res.report) {
      links.push(`<a class="btn small primary" href="${esc(res.report)}" target="_blank">${t('c.openReport')}</a>`);
      links.push(`<a class="btn small" href="/results" target="_blank">${t('nav.results')}</a>`);
    }
  } else if (res.kind === 'tool') {
    for (const [k, v] of Object.entries(res.stats || {})) {
      if (v === null || v === '') continue;
      const s = String(v);
      const isPath = /[\\/]/.test(s);
      const label = isPath ? s.split(/[\\/]/).pop() : s;
      chips.push(`<span class="tr-chip" title="${esc(k)}=${esc(s)}">${esc(k)}=${esc(label)}</span>`);
    }
    (res.files || []).forEach(f => {
      links.push(`<a class="btn small" href="/tool_runs/${esc(f.path)}" download title="${esc(f.path)}">📄 ${esc(f.path.split('/').pop())} <span class="hint">(${esc(f.size)})</span></a>`);
    });
  }
  if (!chips.length && !links.length) return '';
  return `<div class="task-result">
    <div class="tr-title">${t('c.resultTitle')}</div>
    ${chips.length ? `<div class="tr-stats">${chips.slice(0, 14).join('')}</div>` : ''}
    ${links.length ? `<div class="tr-links">${links.join('')}</div>` : ''}
  </div>`;
}

async function refreshTasks() {
  let tasks;
  try {
    const r = await fetch('/api/tasks');
    tasks = await r.json();
    lastTaskList = tasks || [];
    connFails = 0;
    connDown = false;
    setConnBanner(false);
    if (window.__rhOnTasks) { try { window.__rhOnTasks(tasks); } catch (e2) {} }
  } catch (e) {
    // 连接异常时保持短间隔重试，避免失联横幅要等满 3 次空闲周期才出现；
    // 仅真网络失败（TypeError）计入门槛，服务端 500/解析失败不谎报断连
    if (netFail(e)) {
      connDown = true;
      if (++connFails >= 3) setConnBanner(true);
    }
    return;
  }
  try {
  const box = $('tasks');
  // 页面没有「任务」汇总区（如工具页已移除底部任务面板）时跳过面板渲染；
  // 连接横幅、结束通知、工具卡内日志注入（injectStageLogs）仍照常执行。
  if (!box) {
    /* 无面板：仅保留通知与卡内日志注入 */
  } else if (!tasks.length) {
    box.innerHTML = `<p class="hint">${t('c.noTasks')}</p>`;
  } else {
  box.innerHTML = tasks.map(task => {
    const pct = Math.round((task.pct || 0) * 100);
    const stat = taskStatusText(task.status);
    const logHtml = taskLogOpen.has(task.id)
      ? `<pre data-autoscroll="${task.status === 'running' ? 1 : 0}">${esc(cleanLogText(task.log).join('\n'))}</pre>` : '';
    const err = task.error ? `<div class="err">${esc(task.error)}</div>` : '';
    let timing = '';
    if (task.status === 'running') {
      const el = fmtDur(Date.now() / 1000 - (task.started || Date.now() / 1000));
      timing = `<span class="hint" style="margin-left:10px">${t('c.elapsed')} ${el}` +
        (task.eta != null ? ` · ${t('c.eta')} ~${fmtDur(task.eta)}` : '') + '</span>';
    } else if (task.finished && task.started) {
      timing = `<span class="hint" style="margin-left:10px">${t('c.used')} ${fmtDur(task.finished - task.started)}</span>`;
    }
    const preview = task.status === 'done' ? taskResultHtml(task.result) : '';
    return `<div class="task ${task.status}">
      <div style="display:flex;justify-content:space-between;align-items:center">
        <span class="tname">${esc(task.name)}</span>
        <span><span class="tstat ${task.status}">${stat}</span>${timing}
        ${taskButtons(task)}
        </span>
      </div>
      <div class="tmsg">${esc(trTaskMsg(task.msg || ''))}</div>
      <div class="bar"><div style="width:${pct}%"></div></div>
      ${err}${preview}
      <button class="btn small" onclick="toggleLog('${task.id}')">${taskLogOpen.has(task.id) ? t('c.collapseLog') : t('c.expandLog')}</button>
      ${logHtml}
    </div>`;
  }).join('');
  }

  // 任务结束 → toast/系统通知 + 刷新管道视图
  const nowRunning = new Set(tasks.filter(x => x.status === 'running').map(x => x.id));
  for (const task of tasks) {
    if (task.status !== 'running' && prevRunning.has(task.id)
        && !notifiedTasks.has(task.id)) {
      notifiedTasks.add(task.id);
      if (task.status === 'done') {
        const hasReport = task.result && task.result.kind === 'sample' && task.result.report;
        toast(`${t('c.resultNotify')} · ${task.name}`,
              task.result && task.result.kind === 'sample' ? '' : (task.msg || ''),
              {actions: hasReport ? [
                {label: t('c.openReport'), primary: true,
                 onClick: () => window.open(task.result.report, '_blank')},
                {label: t('c.close'), onClick: () => {}},
              ] : [{label: t('c.close'), onClick: () => {}}]});
        sysNotify(t('c.resultNotify'), task.name);
      } else if (task.status === 'failed') {
        toast(`${t('c.failedNotify')} · ${task.name}`, task.error || '',
              {kind: 'failed', ttl: 12000, actions: [
                {label: t('c.close'), onClick: () => {}}]});
        sysNotify(t('c.failedNotify'), task.name);
      }
      loadSamples();
      if (curSample) selectSample(curSample);
      if (typeof loadToolRuns === 'function') loadToolRuns();
      break;   // 一次轮询只弹一条，避免轰炸
    }
  }
  prevRunning = nowRunning;
  } catch (e) { console.warn('任务面板渲染失败:', e); }
  injectStageLogs(tasks);
  autoscrollLogs();
}

// ---------------- 阶段卡片内嵌运行日志 ----------------
// 任务名按服务端语言生成；两种语言的标签都参与匹配
const STAGE_LABELS = {
  subsample:['预处理(子采样)', 'Prep (subsample)'],
  fastp:    ['⓪ Fastp 质控', '⓪ Fastp QC'],
  fq2fa:    ['⓪b 序列转换(FASTQ→FASTA)', '⓪b FASTQ→FASTA'],
  host:     ['① 宿主去除', '① Host removal'],
  kvsuite:  ['②b 已知病毒识别与定量', '②b Known virus ID & quantification'],
  assembly: ['③ 组装·分类·提取', '③ Assembly'],
  verify:   ['③b 候选序列验证', '③b Candidate verify'],
  consensus:['③c 共识序列与变异', '③c Consensus & variants'],
  hostana:  ['④ 宿主预测(ICTV)', '④ Host prediction (ICTV)'],
  orf:      ['⑥ ORF 预测', '⑥ ORF prediction'],
  orfa:     ['⑥b ORF 功能注释', '⑥b ORF annotation'],
  phylo:    ['⑦ 进化树与 SDT', '⑦ Phylogeny'],
  primer:   ['⑧ 引物设计', '⑧ Primer design'],
  gbdraw:   ['⑨ 基因组图', '⑨ Genome plots'],
  report:   ['⑩ 可视化报告', '⑩ Visual report'],
};

const TOOL_LABELS = {
  convert:  ['格式转换', 'Format convert'],   // 缺这条时运行中的转换卡日志区一直是空的
  fastp:    ['质控预处理', 'QC preprocess'],
  hostremoval: ['非宿主序列提取（宿主去除）', 'Non-host extraction (host removal)'],
  orf:      ['ORF 预测', 'ORF predict'],
  orfa:     ['功能注释', 'ORF annotate'],
  genoplot: ['基因组图谱', 'Genome plots'],
  primer:   ['引物设计', 'Primer design'],
  identify: ['病毒序列提取（样本分析）', 'Viral extraction (sample analysis)'],
  assemble: ['病毒组装', 'Virus assembly'],
  contigs:  ['contig分类', 'Contig classify'],
  verify:   ['候选序列验证', 'Candidate verify'],
  consensus: ['共识序列与变异', 'Consensus & variants'],
  // 一键流程两张卡此前没登记：injectStageLogs 找不到对应 toolrun 容器，
  // 卡片输出区一直是空的（2026-09-11 用户实测「一键分析（全流程）没有输出」）。
  kvsuite:  ['已知病毒识别与定量', 'Known virus suite'],
  kvchain:  ['病毒定量与共识·一键', 'KV chain'],
  virchain: ['病毒识别分类·一键', 'Virus chain'],
  ncbi:     ['NCBI下载', 'NCBI download'],
  gbdown:   ['GenBank下载', 'GenBank download'],
  gbimport: ['GenBank导入', 'GenBank import'],
  mirna:    ['miRNA 靶标预测', 'miRNA target'],
  // 以下条目 2026-10-26 补齐：/tasks 里这些任务名一直没有英文对照，
  // 英文界面下会原样漏出中文。
  prepchain: ['一键前处理', 'One-click prep'],
  // 2026-11-02 补齐：以下 7 个工具此前没登记，卡片「输出」区永远空白
  //（与 2026-09-11 kvchain/virchain 漏登记同类）：任务名匹配不到
  // TOOL_LABELS，injectStageLogs 就找不到 toolrun-<key> 容器。
  annochain:  ['一键：注释→图谱→NCBI 提交准备',
              'One-click: annotation→plots→submission prep'],
  beastexport:['BEAST 作业包导出', 'BEAST job export'],
  crispr:     ['CRISPR 检测试剂设计', 'CRISPR assay design'],
  lamp:       ['LAMP 引物组设计', 'LAMP primer design'],
  dsrna:      ['dsRNA 设计', 'dsRNA design'],
  rdp:        ['RDP 重组分析', 'RDP recombination'],
  rtt:        ['时间信号检验', 'Root-to-tip'],
  pdprep:   ['进化动力学数据接入', 'Phylodynamics data prep'],
  // cmpchain 卡也显示「⚡ 一键分析」：任务名标签不再唯一，靠运行目录前缀
  // （phylodyn_…）认领，避免把比较基因组一键的日志灌进这张卡。
  phylodyn: { labels: ['⚡ 一键分析', '⚡ One-click analysis'],
              runPrefix: 'phylodyn_' },
  phylogeo: ['系统地理分析', 'Phylogeography'],
  quicktree:['快速建树', 'Quick tree'],
  // LOGAN 下载卡（toolrun-loganfetch）此前漏登记：运行中输出区一直空白，
  // 进度条与运行日志都没处显示（2026-11-06 用户截图反馈）。
  loganfetch:['LOGAN 数据下载', 'LOGAN download'],
  // 「集合建树」是 refs.py:356 自起的任务名（无「工具·」前缀），此前只有
  // 「快速建树」登记过，建树卡的输出区对集合建树永远是空的；两个按钮共用
  // 同一张卡，故 box 指到 quicktree 的输出容器。
  treebuild:{ labels: ['集合建树', 'Collection phylo', 'Collection tree'],
              prefix: false, box: 'quicktree' },
  // ICTV / CDS·PEP 导出同为后端自起名（「ICTV下载 …」「CDS/PEP 导出·…」）。
  ictv:     { labels: ['ICTV下载', 'ICTV download'], prefix: false },
  cdsexport:{ labels: ['CDS/PEP 导出', 'CDS/PEP export'], prefix: false },
  // 病毒变异分析卡走 kvsuite 工具 + stage=variant（tools_api.py 把任务名
  // 改成「病毒变异分析」），否则原名「已知病毒识别与定量」会把日志灌进
  // 上面那张识别定量卡，变异卡则永远是空的。
  variant:  ['病毒变异分析', 'Variant analysis'],
  // 宿主预测（分类后自动触发，tool_results.py:1079 `宿主预测·{run} {ts}`）不带
  // 「工具·」前缀。2026-11-04 起并入分类卡**同一输出块**显示：它是分类的
  // 自动后处理，单独成块会重复展示「运行日志 + 空下载区」（用户反馈页面重复）。
  // injectStageLogs 对该 key 走追加通道（暂存后并入 toolrun-contigs）。
  hostpredict: { labels: ['宿主预测', 'Host prediction'], prefix: false,
                 box: 'contigs' },
  identity: ['NT+AA 同一性表', 'NT+AA identity table'],
  sdt:      ['SDT 精确分析', 'SDT exact analysis'],
  structcmp:['结构比较', 'Structure comparison'],
  align:    ['序列比对', 'Sequence alignment'],
};

// 数据库构建页：建库任务名 → 卡片内嵌日志容器（buildrun-<key>）。
// 两种语言的标签都参与匹配；refvirus/rvdb 用各自独有词区分。
const BUILD_TASK_LABELS = {
  taxonomy: ['Taxonomy', 'taxonomy'],
  host:     ['宿主库构建', 'Host DB build'],
  refvirus: ['RefSeq Viral'],
  rvdb:     ['RVDB 库构建', 'C-RVDB'],
  k2:       ['Kraken2 库转换', 'Kraken2 convert'],
  // 鉴定库（构建鉴定库按钮，build.py:1245）与 kunpeng 分类库
  // （数据集行的「构建分类库」，build.py:1409）：此前两张卡都没有键，
  // 点了只在任务中心能看到，卡内全空。
  kvidx:    ['病毒鉴定库构建', 'Virus index build'],
  // 「通用病毒库构建 (RefSeq Viral)」也含「病毒库构建」，靠 excl 排除，
  // 否则一个任务会同时写进 refvirus 与 kunpeng 两个容器。
  kunpeng:  { labels: ['病毒库构建', 'Virus DB build'],
              excl: ['RefSeq', 'RVDB', 'C-RVDB'] },
};

// 数据资源页（refdata.html）三张下载卡的任务名 → 卡内进度/日志容器。
// 这些任务名不带「工具·」前缀，也不属于任何流水线阶段，故单独一张表；
// 之前该页连 app-jobs.js 都没加载，点下载只发请求、页面无任何反馈。
const REFDL_LABELS = {
  hostdl:  { labels: ['宿主基因组下载', 'Host genome download'], prefix: false },
  virusds: { labels: ['病毒数据集下载', 'Virus dataset download'], prefix: false },
  // datasets 通用下载：genome / gene / taxonomy 三种 mode，中英各一版
  dsgen:   { labels: ['datasets genome 下载', 'datasets gene 下载',
                      'datasets taxonomy 下载', 'datasets genome download',
                      'datasets gene download', 'datasets taxonomy download'],
             prefix: false },
};

/* 标签表取值两种写法，统一成 {labels, prefix, excl, box}：
     - 数组：['中文名', 'English name']，默认要求任务名带「工具·/Tool·」等前缀
       （前缀门存在的意义：避免池阶段的普通任务名误命中工具卡）。
     - 对象：{labels, prefix:false, excl:[...], box:'otherkey'}
         prefix:false —— 任务名不带「工具·」前缀的也要认（集合建树、ICTV 下载、
                         CDS/PEP 导出这类后端自起名的任务）；
         excl         —— 包含这些子串就不算（「病毒库构建」会被
                         「通用病毒库构建 (RefSeq Viral)」包含，必须排除）；
         box          —— 日志写进别的容器（同一张卡的两个按钮共用一个输出区）。 */
function labelSpec(spec, key, prefix, defaultGate) {
  const pre = prefix || 'toolrun-';
  const gate = defaultGate !== false;
  if (Array.isArray(spec)) {
    return { labels: spec, prefix: gate, excl: [], runPrefix: '',
             box: pre + key };
  }
  // 对象写法：prefix 未写时继承该表的默认（工具表默认过前缀门，建库表不过）。
  return { labels: spec.labels || [],
           prefix: spec.prefix !== undefined ? !!spec.prefix : gate,
           excl: spec.excl || [], runPrefix: spec.runPrefix || '',
           box: pre + (spec.box || key) };
}

/* runPrefix：任务名标签撞车时的第二道判据。两个工具卡可以显示同一个
   标题（如 cmpchain 与 phylodyn 都叫「⚡ 一键分析」），任务名因此也相同；
   只用标签匹配会让 A 工具的日志/产物灌进 B 工具的卡片。这里用运行目录
   前缀（<tool>_<时间戳>_<uuid>）把任务认回真正的工具。 */
function specHit(spec, nm, run) {
  if (spec.excl.some(w => nm.includes(w))) return false;
  if (!spec.labels.some(l => nm.includes(l))) return false;
  if (spec.runPrefix && run && !run.startsWith(spec.runPrefix)) return false;
  if (!spec.prefix) return true;
  return nm.includes('工具·') || nm.includes('Tool·')
      || nm.includes('NCBI') || nm.includes('GenBank')
      || nm.includes('同属') || nm.includes('Synteny');
}

/* 上一轮被任务注入写过的容器 id 集合（injectStageLogs 用）。
   2026-11-05 修复：此前每轮轮询先把所有 .toolrun/.buildrun 输出区清空、
   再按当前 /api/tasks 回填，而 /api/tasks 只含内存里最近 120 条任务
   （tasks.py list_all limit=_KEEP_FULL*2，服务重启后为空）——任务一
   出列，恢复出来的「上次运行」结果约 8 秒后（首个空闲轮询）就被擦掉
   且无人回填，用户看到「刚打开有结果，过一会儿自动消失」。
   改为认领制：
   - 有任务认领的容器：照常每轮重画（进度/日志照常更新）；
   - 上一轮被写过、这一轮无任务认领：清空（任务被删除/清理时同步收走）；
   - 从未被任务写过的容器（restoreLastResults 恢复的「上次运行」）：
     永不触碰，结果保留到下一个任务认领为止。 */
const _injectedBoxes = new Set();

/* 输出结果手动放入历史运行（2026-11-06 用户要求）——
   历史运行区本来就按 /api/tool/runs 列出全部运行，这里是把「当前输出区」
   让出来：记下被收起的 run 名（sessionStorage，刷新后仍收起），清空
   toolrun 容器并滚到本卡底部「🕘 历史运行」，结果随时可 ↺ 还原。
   不删任何产物；新的运行不会被旧记录挡住。 */
const OUT_HIDDEN_KEY = 'vp_out_hidden';
/* 用户手动 ↺ 还原后的「钉住」状态：key → run 名。钉住的输出区不被任务列表
   里其他任务（更早的/失败的）覆盖；这张卡出现新任务 id 时自动解除。
   （2026-11-06 用户反馈：还原后过几秒又消失——就是失败/旧任务重新认领。） */
const RH_PINNED = {};
const _seenLive = new Set();      // 本轮会话 injectStageLogs 见过的任务 id
function outHiddenSet() {
  try {
    const a = JSON.parse(sessionStorage.getItem(OUT_HIDDEN_KEY) || '[]');
    return new Set(Array.isArray(a) ? a : []);
  } catch (e) { return new Set(); }
}
function outSaveHidden(set) {
  try {
    sessionStorage.setItem(OUT_HIDDEN_KEY,
      JSON.stringify(Array.from(set).slice(-200)));
  } catch (e) {}
}
function outUnhideRun(name) {
  if (!name) return;
  const s = outHiddenSet();
  if (s.delete(String(name))) outSaveHidden(s);
}
/* 解除某张卡的「#<前缀>」伪收起标记（链式一键放入历史时写入；见
   CHAIN_CASCADE）。⚠️ 必须在新任务真实渲染进卡时调用——否则标记会永久
   滞留：链收起过一次 verify 后，刷新时该卡**后续所有新 run** 的自动恢复
   都会被前缀过滤挡住（任务记录被「清理」清掉后卡片就真空白了）。 */
function outUnhidePrefix(key) {
  const pfx = (typeof CARD_RUN_PREFIX !== 'undefined') ? CARD_RUN_PREFIX[key] : null;
  if (!pfx) return;
  const list = Array.isArray(pfx) ? pfx : [pfx];
  const s = outHiddenSet();
  let dirty = false;
  list.forEach(p => { if (s.delete('#' + p)) dirty = true; });
  if (dirty) outSaveHidden(s);
}
/* 结果面板清单：RICH_BOX 的富面板 + 分类/分析等独立面板 */
function resultPanelIds() {
  const ids = new Set(Object.values(
    (typeof RICH_BOX !== 'undefined') ? RICH_BOX : {}));
  ['contigReport', 'anaResult', 'vfResult', 'vcResult', 'kvChainResult',
   'oaResult', 'kvResult', 'pdResult'].forEach(x => ids.add(x));
  return ids;
}
/* 按 run 名收起所有可见结果面板——「放入历史」只看 toolrun 容器不够：
   分类报告/验证表等富面板可能是另一张卡的独立容器（不在 RICH_BOX 里）。 */
const HISTORY_BOX_ALIAS = { identity: 'rh-sdt', variant: 'rh-kvsuite',
  quicktree: 'rh-treebuild', structcmp: 'rh-treebuild',
  hostpredict: 'rh-contigs', lamp: 'rh-lamp',
  cmpchain: 'rh-cmpchain', annochain: 'rh-annochain' };
function historyBoxId(key) {
  return document.getElementById(HISTORY_BOX_ALIAS[key] || ('rh-' + key));
}
function hideResultPanelsForRun(run, key) {
  if (!run) return 0;
  let n = 0;
  resultPanelIds().forEach(pid => {
    const el = document.getElementById(pid);
    if (!el || el.style.display === 'none') return;
    if (String(el.textContent || '').indexOf(String(run)) < 0) return;
    el.style.display = 'none';
    n++;
    if (key && typeof RH_USER_TOUCHED !== 'undefined') RH_USER_TOUCHED.add(key);
  });
  return n;
}
/* 页面快照恢复后，已「放入历史」的 run 不允许再被拉回；幂等。 */
function hideHiddenResultPanels() {
  const hid = outHiddenSet();
  if (!hid.size) return;
  resultPanelIds().forEach(pid => {
    const el = document.getElementById(pid);
    if (!el || el.style.display === 'none') return;
    const txt = String(el.textContent || '');
    for (const r of hid) {
      if (txt.indexOf(r) >= 0) { el.style.display = 'none'; break; }
    }
  });
}
/* 富结果卡专用：不给 toolrun 容器的卡（cmpchain）用「📥 放入历史」。
   run → 收起对应富面板 / 归档任务 / 展开该卡历史运行区。 */
async function pushRichRunToHistory(key, run, btn) {
  if (!run) return;
  { const s = outHiddenSet(); s.add(String(run)); outSaveHidden(s); }
  const rid = (typeof RICH_BOX !== 'undefined') ? RICH_BOX[key] : '';
  const panel = rid ? document.getElementById(rid) : null;
  if (panel) {
    panel.style.display = 'none';
    if (typeof RH_USER_TOUCHED !== 'undefined') RH_USER_TOUCHED.add(key);
  }
  let task = null;
  for (const t of (lastTaskList || [])) {
    const r = (t.result && t.result.run) || '';
    const od = t.out_dir ? String(t.out_dir).replace(/[\/]+$/, '')
      .split(/[\/]/).pop() : '';
    if (r === run || od === run) { task = t; break; }
  }
  if (task && task.id) {
    if (btn) btn.disabled = true;
    try { await fetch('/api/task/' + encodeURIComponent(task.id) + '/archive',
                      { method: 'POST' }); } catch (e) {}
    if (btn) btn.disabled = false;
  }
  if (typeof refreshTasks === 'function') { try { refreshTasks(); } catch (e) {} }
  const rh = historyBoxId(key);
  if (rh) {
    const body = rh.querySelector('.rh-body');
    if (body && body.style.display === 'none' && typeof rhToggle === 'function')
      rhToggle(rh.id);
    if (typeof rhRefresh === 'function') { try { rhRefresh(); } catch (e) {} }
    try { rh.scrollIntoView({ block: 'center', behavior: 'smooth' }); } catch (e) {}
  }
  toast(t('c.toHistoryDone', '已放入历史运行'), run, { ttl: 4000 });
}
async function pushOutputToHistory(btn) {
  const holder = btn && btn.closest && btn.closest('.toolrun');
  if (!holder) return;
  const task = OUT_TASKS[holder.id] || null;
  const run = (task && task.result && task.result.run)
    || (task && task.out_dir ? String(task.out_dir).replace(/[\/]+$/, '')
         .split(/[\/]/).pop() : '');
  if (!run) { toast(t('c.toHistoryFail', '没有找到这次运行的目录'), '', {ttl: 4000}); return; }
  // 记入「已放入历史」：刷新 / 切模块后 restoreLastResults 不再把它拉回输出区
  { const s = outHiddenSet(); s.add(String(run)); outSaveHidden(s); }
  const key = holder.id.replace(/^toolrun-/, '');
  // 先给即时反馈：清空输出块 + 收起同卡的富结果面板（验证/注释/定量…）。
  // 归档接口要等网络往返，若不先收面板，用户会觉得「点了没反应」。
  holder.innerHTML = '';
  try {
    const richId = (typeof RICH_BOX !== 'undefined') ? RICH_BOX[key] : '';
    const richPanel = richId ? document.getElementById(richId) : null;
    if (richPanel) {
      richPanel.style.display = 'none';
      if (typeof RH_USER_TOUCHED !== 'undefined') RH_USER_TOUCHED.add(key);
    }
    // contigs / identify 的结果不走 RICH_BOX：分类报告 + 分析结果面板
    // 直接按 run 名收起（截图里的「分类分析」表就是这两块）。
    if (key === 'contigs' || key === 'identify') {
      ['contigReport', 'anaResult'].forEach(pid => {
        const p = document.getElementById(pid);
        if (p) {
          p.style.display = 'none';
          if (typeof RH_USER_TOUCHED !== 'undefined') RH_USER_TOUCHED.add(key);
        }
      });
    }
    hideResultPanelsForRun(run, key);
    // 链式一键的级联收起（CHAIN_CASCADE）：链 run 名在阶段面板的标题里
    // 是阶段自己的 run（如「验证结果 · verify_…」），按 run 名匹配不到
    // —— 必须按映射点名收起，否则那张分类表会留在屏上（用户报告的
    // 「放入历史没用」）。
    try {
      const cas = CHAIN_CASCADE[key];
      if (cas) {
        (cas.panels || []).forEach(pid => {
          const p = document.getElementById(pid);
          if (p) p.style.display = 'none';
        });
        (cas.keys || []).forEach(k => {
          if (typeof RH_USER_TOUCHED !== 'undefined') RH_USER_TOUCHED.add(k);
          // '#<前缀>' 伪条目：restoreLastResults 对前缀级收起也生效
          // （阶段 run 名与链 run 名不同，刷新后不回弹）。
          const pfx = CARD_RUN_PREFIX[k];
          const s = outHiddenSet();
          if (pfx) s.add('#' + (Array.isArray(pfx) ? pfx[0] : pfx));
          outSaveHidden(s);
        });
      }
    } catch (e) {}
  } catch (e) {}
  delete RH_PINNED[key];            // 放入历史 = 输出区腾空，解除钉住
  // 1) 任务记录手动归档（任务中心 →「历史归档」，可 ↩ 恢复 / ↗ 在页面中打开）；
  //    目录批量时同批一起归档，避免卡片里只剩下一件样本的输出。
  const peers = (task && task.batch && Array.isArray(lastTaskList))
    ? lastTaskList.filter(x => x.batch === task.batch
        && x.status !== 'running' && x.status !== 'queued')
    : [task];
  // 整批一起收起：只隐藏点击的那一条，刷新后自动恢复会拿同批的别的样本
  // 补进输出区——用户又会看到「只有一个结果」。
  { const s = outHiddenSet(); s.add(String(run));
    peers.forEach(p => {
      const r = (p && p.result && p.result.run) || '';
      const od = p && p.out_dir ? String(p.out_dir).replace(/[\/]+$/, '')
        .split(/[\/]/).pop() : '';
      if (r) s.add(String(r)); else if (od) s.add(String(od));
    });
    outSaveHidden(s); }
  let ok = 0;
  if (btn) btn.disabled = true;
  for (const p of peers) {
    if (!p || !p.id) continue;
    try {
      const r = await fetch('/api/task/' + encodeURIComponent(p.id) + '/archive',
                            { method: 'POST' });
      if (r.ok) ok++;
    } catch (e) { /* 单条失败不拦整批 */ }
  }
  if (btn) btn.disabled = false;
  // 归档接口不可用/失败：退化为本页收起（sessionStorage），历史运行区仍可 ↺ 还原
  if (typeof refreshTasks === 'function') { try { refreshTasks(); } catch (e) {} }
  const rh = historyBoxId(key);
  if (rh) {
    const body = rh.querySelector('.rh-body');
    if (body && body.style.display === 'none' && typeof rhToggle === 'function') {
      rhToggle('rh-' + key);
    }
    if (typeof rhRefresh === 'function') { try { rhRefresh(); } catch (e) {} }
    try { rh.scrollIntoView({ block: 'center', behavior: 'smooth' }); } catch (e) {}
  }
  toast(t('c.toHistoryDone', '已放入历史运行'),
        (ok ? t('c.toHistoryN', '已归档 {n} 个任务').replace('{n}', ok) : '') || run,
        {ttl: 4000});
}

function injectStageLogs(tasks) {
  snapshotLogScroll();
  document.querySelectorAll('.stage-log').forEach(el => { el.innerHTML = ''; });
  // .toolrun / .buildrun 不再每轮整体清空：改为循环结束后的
  // 「认领失效才清空」（见 _injectedBoxes 注释）。
  // 追加型任务暂存：宿主预测并入分类卡块（循环后统一追加，见 TOOL_LABELS 注释）
  const hpTasks = [];
  // 每个容器只吃**第一个**匹配任务：/api/tasks 是新→旧序（order.insert(0)），
  // 若不去重，同卡的旧任务（数组靠后）会后写覆盖新任务——实测连跑两次
  // 格式转换，第二次完成后输出区仍显示第一次的运行日志（2026-09-30 用户报告）。
  const claimed = new Set();
  const _hideOut = outHiddenSet();
  const _hideBlocked = new Set();   // 本次轮询里被收起的新任务会挡住更早的结果
  for (const task of tasks) {
    // paused/cancelled 也要渲染：容器在上面被清空过，漏掉这两态会让「暂停」
    // 或「取消」瞬间把整张卡的进度条和日志抹掉（看起来像任务消失了）。
    if (!['running', 'done', 'failed', 'paused', 'cancelled'].includes(task.status)) continue;
    // 样品管道任务：按后端上报的当前阶段（task.stage）精确归属——
    // 日志只进"正在跑的这一张"阶段卡，不再整条链每张卡都重复一份。
    // stage 尚未上报（刚启动）时退化为名称匹配，且仅当名称只命中
    // 一个阶段时注入，避免多阶段链一次性铺满所有卡。
    // 另按样品名过滤：其它样品的管道任务不出现在当前样品的卡上。
    const nmStage = task.name || '';
    const sampleOk = !curSample || nmStage.includes(curSample);
    if (sampleOk) {
      let hitKeys = [];
      if (task.stage && STAGE_LABELS[task.stage]) {
        hitKeys = [task.stage];
      } else if (!task.stage) {
        hitKeys = Object.entries(STAGE_LABELS)
          .filter(([, labels]) => labels.some(label => nmStage.includes(label)))
          .map(([key]) => key);
        if (hitKeys.length > 1) hitKeys = [];   // 多阶段链未上报 → 等首个进度
      }
      for (const key of hitKeys) {
        const boxId = 'log-' + key;
        if (claimed.has(boxId)) continue;
        const box = $(boxId);
        if (box) { box.innerHTML = stageLogHtml(task); claimed.add(boxId); }
      }
    }
    // 专项分析工具任务（按工具标签匹配 → 卡内嵌 toolrun 容器）
    for (const [key, raw] of Object.entries(TOOL_LABELS)) {
      const spec = labelSpec(raw, key);
      // 宿主预测走追加通道（不占容器），其余 key 每容器只吃第一个匹配任务
      if (key !== 'hostpredict' && claimed.has(spec.box)) continue;
      const box = $(spec.box);
      // 运行名：完成后在 result.run，运行中用 out_dir 末段（两者都以 <tool>_ 开头）
      const runName = (task.result && task.result.run)
        || String(task.out_dir || '').replace(/[\\/]+$/, '')
             .split(/[\\/]/).pop() || '';
      if (box && specHit(spec, task.name || '', runName)) {
        // 已手动放入历史：当前输出区保持空白，不回落显示更早的运行
        if (runName && _hideOut.has(runName)) { _hideBlocked.add(spec.box); continue; }
        if (_hideBlocked.has(spec.box)) continue;
        // 用户手动 ↺ 还原过历史运行：钉住输出区，不被任务列表里其他
        // （更早的/失败的）任务覆盖；这张卡出现**新**任务 id 时才解除。
        if (RH_PINNED[key]) {
          if (_seenLive.has(task.id)) { claimed.add(spec.box); continue; }
          delete RH_PINNED[key];        // 新任务：解除钉住，正常接管
        }
        _seenLive.add(task.id);
        if (key === 'hostpredict') {
          // tasks 新→旧，第一个命中的就是最新一次宿主预测
          if (!hpTasks.length) hpTasks.push(task);
        } else {
          box.innerHTML = batchSummaryHtml(task, tasks)
            + toolRunHtml(task, spec.box); claimed.add(spec.box);
          // 新任务真实产出渲染进卡：解除「用户手动还原过」标记，此后自动
          // 恢复对该卡恢复正常工作（画面由新任务结果接管）。
          if (typeof RH_USER_TOUCHED !== 'undefined') RH_USER_TOUCHED.delete(key);
          // 同点解除「#<前缀>」链收起标记：新数据已接管本卡，放进历史时的
          // 前缀级遮蔽使命结束，不能永久挡住后续刷新的自动恢复。
          if (typeof outUnhidePrefix === 'function') outUnhidePrefix(key);
        }
      }
    }
    // 建库任务 → 数据库构建页对应卡片内嵌日志（.buildrun 容器，按任务名匹配）
    for (const [key, raw] of Object.entries(BUILD_TASK_LABELS)) {
      const spec = labelSpec(raw, key, 'buildrun-', false);
      if (claimed.has(spec.box)) continue;
      const box = $(spec.box);
      if (box && specHit(spec, task.name || '')) {
        box.innerHTML = inlineTaskHtml(task); claimed.add(spec.box);
      }
    }
    // 数据资源页下载任务 → 同一张卡的进度条 + 实时日志（.toolrun 容器）
    for (const [key, raw] of Object.entries(REFDL_LABELS)) {
      const spec = labelSpec(raw, key, 'toolrun-', false);
      if (claimed.has(spec.box)) continue;
      const box = $(spec.box);
      if (box && specHit(spec, task.name || '')) {
        box.innerHTML = inlineTaskHtml(task); claimed.add(spec.box);
      }
    }
  }
  // 宿主预测并入分类卡**同一结构**：状态行、日志、产物按钮全部合进分类卡
  // 自己的日志区/下载行，不再单列一套「宿主预测日志 + 宿主预测产物」
  // （2026-11-04 二次反馈：同块内两套小节仍显重复，要真正合并在同一区）。
  // 此时分类卡已渲染（tasks 新→旧遍历，contigs 任务先占容器），在其上合并。
  if (hpTasks.length) {
    mergeHostpredict($('toolrun-contigs'), hpTasks[0]);
  }
  // 认领失效才清空：上一轮被任务写过、这一轮没有任务再认领的容器，
  // 说明对应任务已删除/被清出任务列表，才同步收走它的输出块；
  // 「上次运行」恢复的内容从未被认领，不受影响。
  for (const id of _injectedBoxes) {
    if (claimed.has(id))  continue;
    const el = document.getElementById(id);
    if (el) el.innerHTML = '';
  }
  _injectedBoxes.clear();
  claimed.forEach(id => _injectedBoxes.add(id));
  autoscrollLogs();
}

/* 宿主预测产物按钮的精选与排序（mergeHostpredict 用）：
   result.files 是 mtime 倒序的全运行目录清单，直接塞进分类下载行会把
   hostpredict.log / .host_analysis.done / plotly.min.js 这类中间件一起
   带出来（12 个名额挤掉主产物）；只留 08_host_analysis/ 下的主产物并按
   固定优先级排序，追加在分类按钮后面视觉才连贯。 */
const HP_FILE_ORDER = [
  'host_prediction.tsv', 'host_summary.tsv', 'sankey_host.html',
  'sunburst_host.html', '.classified.fasta'];
const HP_FILE_EXCLUDE = new Set(['summary.json', 'plotly.min.js',
  'hostpredict.log', '.host_analysis.done']);

/* 宿主预测并入分类卡（真正的同区合并，mergeHostpredict）：
   ① 分类徽章行下加一行「🧲 宿主预测 · 状态」（运行中带迷你进度条，
      完成时带条数）——自动后处理的存在感靠这一行，不加独立徽章卡；
   ② 宿主预测日志追加进分类卡同一日志区（中间空一行分隔，格式同为
      [时间] [级别] 文本，视觉连续）；
   ③ 产物按钮追加进分类卡同一下载行（精选排序见 HP_FILE_ORDER）。 */
function mergeHostpredict(box, task) {
  if (!box) return;
  box.querySelectorAll('[data-hp-append]').forEach(el => el.remove());  // 旧版子节清理
  const zh = (typeof VP_LANG === 'undefined' || VP_LANG !== 'en');
  const stat = taskStatusText(task.status);
  const stats = (task.result && task.result.stats) || {};
  const running = task.status === 'running' || task.status === 'paused';
  const err = task.error
    ? `<div class="err" style="margin:6px 0 0">${esc(task.error)}</div>` : '';
  const bits = [`${t('c.hpTitle', '宿主预测')}`, stat];
  if (task.status === 'done' && stats.n_contigs != null) {
    bits.push(`${stats.n_contigs} ${zh ? '条' : 'contigs'}`);
  }
  const statusLine = `<div data-hp-status style="margin:8px 0 2px;font-size:12.5px;font-weight:700">
      ${esc(bits.join(' · '))}</div>
    ${running ? `<div class="bar" style="height:5px"><i style="width:${Math.round((task.pct || 0) * 100)}%"></i></div>` : ''}${err}`;
  const badgeRow = box.querySelector(':scope > div');
  if (badgeRow) badgeRow.insertAdjacentHTML('afterend', statusLine);
  // ② 日志并区：接在分类日志之后，空一行分隔
  const pre = box.querySelector('pre.logbox');
  const lines = cleanLogText(task.log);
  if (pre && lines.length) {
    pre.insertAdjacentHTML('beforeend',
      (pre.textContent.trim() ? '\n\n' : '') + esc(lines.join('\n')));
  }
  // ③ 产物并区：精选 08_host_analysis/ 主产物，按固定优先级排序
  const run = (task.result && task.result.run) || '';
  const files = ((task.result && task.result.files) || [])
    .map(f => String(f.path).replace(/\\/g, '/'))
    .filter(p => p.includes('08_host_analysis/')
                 && !HP_FILE_EXCLUDE.has(p.split('/').pop()))
    .sort((a, b) => {
      const rank = p => {
        const base = p.split('/').pop().toLowerCase();
        const i = HP_FILE_ORDER.findIndex(k => base.endsWith(k));
        return i < 0 ? HP_FILE_ORDER.length : i;
      };
      return rank(a) - rank(b);
    });
  if (run && files.length) {
    const row = box.querySelector('.dlbtn-row');
    if (row) {
      const tmp = document.createElement('div');
      tmp.innerHTML = outFilesHtml({ result: { run, files: files.map(p => ({ path: p })) } });
      tmp.querySelectorAll('.dlbtn-row > a').forEach(a => row.appendChild(a));
    }
  }
}

// 输出区标签状态（boxId → 'result' | 'log' | 'files'）。
// 输出区已改为纵向平铺（日志/结果/文件同时展示），不再渲染标签页按钮；
// 这里保留状态与 setOutTab 仅为向后兼容（防御性，不再被触发）。
const OUT_TAB_STATE = {};
const OUT_TASKS = {};

/* 工具产物文件名 → 语义化下载名 + 按钮配色。
   用于把原始英文文件名（run.log / contigs.filtered.fasta 等）翻译成
   「运行日志 / 组装结果」这类可读名称，并按主结果/辅助产物配色。 */
const DL_LABEL_SPECS = [
  // [文件名包含, 语义化名, 按钮色]（先匹配先得，结果类放前）
  // —— 组装 / 分类主结果 ——
  ['contigs.filtered.fasta', '组装结果 (contigs)', 'primary'],
  ['transcripts.fasta', '组装结果 (transcripts)', 'primary'],
  ['viral_contigs.fasta', '病毒序列 (viral contigs)', 'primary'],
  ['virus_classification.tsv', '病毒分类结果', 'primary'],
  ['viral_ids.tsv', '分类序列 ID', 'primary'],
  ['viral_sequences.', '病毒序列', 'primary'],
  ['contigs.fasta', '组装结果 (contigs)', 'primary'],
  ['scaffolds.fasta', '组装结果 (scaffolds)', 'primary'],
  ['pyrodigal.faa', 'ORF 蛋白', 'primary'],
  ['pyrodigal.ffn', 'ORF 核苷酸', 'primary'],
  ['host_prediction.tsv', '宿主预测结果', 'primary'],
  // —— 宿主预测（08_host_analysis/，并入分类卡下载行的精选产物）——
  ['host_summary.tsv', '宿主类别汇总', 'green'],
  ['sankey_host', '宿主桑基图', 'green'],
  ['sunburst_host', '宿主旭日图', 'green'],
  ['.classified.fasta', '按宿主拆分序列', 'amber'],
  // —— 报告 / 汇总 ——
  ['report.html', '分析报告', 'green'],
  ['kreport', '分类报告 (kreport)', 'green'],
  // —— miRNA 靶标预测（语义化标签：这些不是基因组图，别落进 .svg 兜底）——
  ['mirna_interactive.html', 'miRNA 交互图表', 'green'],
  ['mirna_targets_table.html', 'miRNA 结果明细表', 'green'],
  ['mirna_duplex', 'miRNA 双链结构图', 'green'],
  ['mirna_relations', 'miRNA 关系图', 'green'],
  ['mirna_targets_overview', 'miRNA 预测概览', 'green'],
  ['keep_R1.fastq.gz', '保留 reads R1', 'green'],
  ['keep_R2.fastq.gz', '保留 reads R2', 'green'],
  ['kept_R1.fastq.gz', '保留 reads R1', 'green'],
  ['kept_R2.fastq.gz', '保留 reads R2', 'green'],
  ['taxburst', 'Krona 旭日图', 'green'],
  // —— 图 / 结构文件 ——
  ['.svg', '基因组图 (SVG)', 'green'],
  ['.gfa', '组装图 (GFA)', 'amber'],
  ['before_rr.fasta', '纠错前序列', 'amber'],
  ['fastp.', '质控报告', 'amber'],
  // —— 日志 / 配置 ——
  ['run.log', '运行日志', 'gray'],
  ['spades.log', 'SPAdes 日志', 'gray'],
  ['warnings.log', '警告日志', 'gray'],
  ['spades.sh', 'SPAdes 脚本', 'gray'],
  ['spades.yaml', 'SPAdes 配置', 'gray'],
  ['run_spades.sh', 'SPAdes 脚本', 'gray'],
  ['run_spades.yaml', 'SPAdes 配置', 'gray'],
  ['params.txt', '参数表', 'gray'],
  ['input_dataset.yaml', '输入数据描述', 'gray'],
  ['dataset.info', '数据描述', 'gray'],
  ['.json', '结果数据 (JSON)', 'gray'],
  // —— kvsuite 鉴定汇总：全量/最优语义不同，不能共用「阶段汇总」标签
  // （2026-11-05 用户反馈两个同名按钮）；须排在通用 summary. 条目之前 ——
  ['all_viruses.best.summary', '鉴定汇总（最优）', 'green'],
  ['all_viruses.summary', '鉴定汇总（全量）', 'gray'],
  ['all_viruses.review', '疑似待复核', 'amber'],
  ['summary.', '阶段汇总', 'gray']
];
function dlLabel(base, path) {
  const low = String(base || path || '').toLowerCase();
  for (const [key, label, color] of DL_LABEL_SPECS) {
    if (low.includes(key)) return { label, color };
  }
  return { label: base, color: '' };   // 未知 → 原始文件名
}

/* 中间件不配成下载按钮（2026-11-05 审稿反馈）：库文件 / 完成标记 /
   输入回显这类文件对「看结果」没有价值，列出来只会稀释主产物。
   仍可通过「模块结果页」访问，不丢数据。 */
const DL_NOISE_FILES = /^(plotly\.min\.js|.*\.(done|stderr|stdout)|output_1\.txt|sample_file\.txt|results\.html)$/i;
const dlIsNoise = base => DL_NOISE_FILES.test(String(base || ''));

/* 输出目录行：结果产出位置随结果一起明示（完整路径 + 复制 + 打开）。
   路径经 data-* 属性传递再由 dataset 读回：Windows 路径里的 \t \r 这类
   序列若直接拼进 onclick 的 JS 字符串字面量会被当成转义符。 */
function outDirLine(outDir) {
  if (!outDir) return '';
  const od = String(outDir);
  return `<div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin:8px 0 2px;font-size:12.5px">
    <span style="font-weight:700">${t('c.outdir', '输出目录')}</span>
    <code class="mono" style="user-select:all;word-break:break-all" title="${esc(od)}">${esc(od)}</code>
    <button class="btn small" style="padding:1px 8px" data-d="${esc(od)}" onclick="copyText(this.dataset.d)" title="${t('c.copyPath', '复制路径')}">⧉</button>
    <button class="btn small" style="padding:1px 8px" data-d="${esc(od)}" onclick="openOutDir(this.dataset.d)" title="${t('c.openDir', '打开目录')}">📂</button>
  </div>`;
}

/* 在资源管理器中打开平台内的输出目录（/api/open_dir 做平台内校验） */
async function openOutDir(p) {
  try {
    const r = await fetch('/api/open_dir', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: p }) });
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      alert((d.error || t('ui.common.openFail')) + ' (HTTP ' + r.status + ')');
    }
  } catch (e) { alert(t('ui.common.openFail') + ': ' + e); }
}

function outFilesHtml(task) {
  const files = (task.result && task.result.files) || [];
  if (!files.length) {
    return `<p class="hint" style="margin:6px 0">${
      task.status === 'running' ? t('c.filesRunning') : t('c.noFiles')}</p>`;
  }
  // 链接必须含运行名（/tool_runs/<run>/<file>），task.result.run 由后端注入；
  // download 属性带运行名前缀：不同运行的同名产物（viral_ids.tsv 等）
  // 下载到本地不再互相覆盖。
  const run = (task.result && task.result.run) || '';
  // miRNA 交互页（自包含 HTML）：SVG 预览卡上要给「交互版」入口，先找到它
  const itFile = files.find(f => /mirna_interactive\.html$/i.test(String(f.path)));
  const itHref = (run && itFile)
    ? `/tool_runs/${encodeURIComponent(run)}/${String(itFile.path).replace(/\\/g, '/').split('/').map(encodeURIComponent).join('/')}`
    : '';
  const btns = [];
  const others = [];                       // 中性按钮收进「其他文件」折叠
  const prevs = [];
  const seenLabels = new Set();          // 同语义去重（截图中「病毒序列」出现两次）
  for (const f of files) {
    const normPath = String(f.path).replace(/\\/g, '/');
    const segs = normPath.split('/').map(encodeURIComponent).join('/');
    const base = String(f.path).split(/[\\/]/).pop();
    const dl = run ? `${run}_${base}` : base;
    const href = run ? `/tool_runs/${encodeURIComponent(run)}/${segs}` : '#';
    const { label, color } = dlLabel(base, f.path);
    // 中间件（plotly.min.js / .done / 输入回显 …）不配按钮；同语义产物
    // （如 viral_contigs.fasta 在两个子目录各一份）只保留最近的一个按钮。
    if (dlIsNoise(base) || seenLabels.has(label)) continue;
    seenLabels.add(label);
    // 2026-11-05 审稿反馈：配色收敛为三级语义色——主产物 primary（深绿）、
    // 图表/报告 green、其余一律中性灰白（原 amber/orange 混在行里把
    // 主产物淹了）。
    const cls = (color === 'primary' || color === 'green')
      ? ` dlbtn ${color}` : ' dlbtn';
    // 中性文件（原始 tsv/中间 fasta 等）不进主按钮行：行里只剩主产物与
    // 图表报告，与历史区「关键产物 / 全部文件」同一口径（2026-11-05）。
    const target = (color === 'primary' || color === 'green') ? btns : others;
    const low = base.toLowerCase();
    // 自包含交互 HTML（miRNA 图表/明细表、宿主桑基/旭日图）：主按钮「打开」
    // 而非下载，下载收成次按钮 —— 点了下载再找文件再打开的路径没人想要。
    if (/^(mirna_interactive|mirna_targets_table|sankey_host|sunburst_host)\.html$/i.test(base) && run) {
      btns.push(`<a class="${cls.trim()}" href="${href}" target="_blank" rel="noopener" `
        + `title="在浏览器中打开交互页">🔍 打开 ${esc(label)}</a>`
        + `<a class="dlbtn" href="${href}" download="${esc(dl)}" `
        + `title="${esc(f.path)}【${esc(f.size)}】">⬇ 下载</a>`);
      continue;
    }
    // 图件（SVG）额外给一个「整理」入口：同源 URL 直接喂给内置 svguest。
    // PNG 不给 —— svguest 只吃 SVG，PNG 的整理按钮点开是空白页。
    const edit = (run && /\.svg$/i.test(base))
      ? ' ' + svgEditLink(href, '整理', 'dlbtn') : '';
    // 图（SVG）：内联预览卡不进按钮行——预览卡独占整行、排在全部下载
    // 按钮下方，按钮行保持整齐（2026-11-03：之前卡在按钮中间把行撑乱）。
    if (low.endsWith('.svg') && run) {
      const img = `<img src="/tool_runs/${encodeURIComponent(run)}/${segs}" alt="${esc(base)}" style="display:block;width:100%;height:220px;object-fit:contain;border:1px solid var(--line-100);border-radius:8px;margin:8px 0;background:#fff" loading="lazy">`;
      // miRNA duplex/relations 有交互版：预览卡上直接给「交互版」入口
      const itLink = (itHref && /^mirna_(duplex|relations)\.svg$/i.test(base))
        ? ` <a class="dlbtn green" href="${itHref}" target="_blank" rel="noopener" title="悬停高亮、逐碱基配对提示的交互版">🔍 交互版</a>` : '';
      prevs.push(`<div class="svg-prev" style="border-radius:8px;padding:8px;background:#fff">${img}<a class="${cls.trim()}" href="${href}" download="${esc(dl)}" title="${esc(f.path)}【${esc(f.size)}】">⬇ 下载 ${esc(label)}</a>${itLink}${edit}</div>`);
      continue;
    }
    target.push(`<a class="${cls.trim()}" href="${href}" download="${esc(dl)}" title="${esc(f.path)}【${esc(f.size)}】">⬇ ${esc(label)}</a>${edit}`);
  }
  // 布局：主产物按钮行 → 「其他文件」折叠（中性 tsv/中间 fasta）→ 图预览
  // （每张独占一行、不再夹在按钮中间）。
  const othersHtml = others.length
    ? `<details style="margin-top:6px"><summary class="hint" style="cursor:pointer;margin:0">其他文件（${others.length}）</summary><div class="dlbtn-row" style="margin-top:4px">${others.join('')}</div></details>`
    : '';
  return `<div class="dlbtn-row">${btns.join('')}</div>` + othersHtml +
    (prevs.length
      ? `<div style="display:flex;flex-direction:column;gap:10px;margin-top:10px">${prevs.join('')}</div>`
      : '');
}

/* 工具运行的关键数字 chips（仅标量；路径取文件名；run 名已在状态徽标里，不重复） */
const TOOL_STAT_LABELS = {
  n_classified: ['已分类序列', 'Classified seqs'],
  n_extracted:  ['提取病毒序列', 'Extracted viral seqs'],
  n_contigs:    ['过滤后 contigs', 'Contigs kept'],
  n_viral:      ['病毒 contigs', 'Viral contigs'],
  total_bp:     ['总长度(bp)', 'Total bp'],
  // 组装结果基本统计（seqkit stats 口径，2026-11-06）
  avg_len:      ['平均长度', 'Mean length'],
  max_len:      ['最长', 'Max length'],
  N50:          ['N50', 'N50'],
  N50_num:      ['L50', 'L50'],
  'GC(%)':      ['GC(%)', 'GC(%)'],
  sum_n:        ['N 碱基', 'N bases'],
  // GenBank 检索预览（pdprep 两步导入的第一步）：这两个数是人在勾选前唯一
  // 需要看的判断依据，给中文标签，别显示成裸键 n_shown=8。
  n_shown:      ['列出记录', 'Listed'],
  n_usable:     ['可导入', 'Usable'],
};

/* 宿主预测状态行的专用键：渲染进「🧲 宿主预测 · 已完成 · N 条」状态行
   （toolRunHtml），不进关键数字 chips——展平后裸键名（categories.Plant=2、
   class_source.blast=0 …）挤满一行全是噪音（2026-11-04 用户圈红反馈）。 */
const HP_STAT_KEYS = new Set(['n_host_pred', 'host_warn']);

function toolStatChips(res) {
  if (!res || res.kind !== 'tool') return '';
  const zh = (typeof VP_LANG === 'undefined' || VP_LANG !== 'en');
  const chips = [];
  for (const [k, v] of Object.entries(res.stats || {})) {
    if (v === null || v === '' || k === 'run' || k === 'out_dir'
        || HP_STAT_KEYS.has(k)) continue;
    const lab = (TOOL_STAT_LABELS[k] || [k, k])[zh ? 0 : 1];
    const s = String(v);
    const shown = /[\\/]/.test(s) ? s.split(/[\\/]/).pop() : s;
    chips.push(`<span class="tr-chip" title="${esc(k)}=${esc(s)}">${esc(lab)}=${esc(shown)}</span>`);
  }
  return chips.length ? `<div class="tr-stats">${chips.slice(0, 14).join('')}</div>` : '';
}

/* 目录批量结果汇总（2026-11-06）：batchRunScan 逐样本提交的任务共享
   task.batch；卡片输出区原来只渲染第一个命中任务，用户会以为“只跑了一个”。
   这里把同一批的所有样本列成一张小表（样本名 / 状态 / 产物 / 查看），
   明细仍由下方 toolRunHtml 展示最新一条。 */
function batchPeers(task) {
  if (!task || !task.batch || !Array.isArray(lastTaskList)) return [task];
  const peers = lastTaskList.filter(x => x && x.batch === task.batch
    && x.status !== 'running' && x.status !== 'queued');
  return peers.length ? peers.slice().reverse() : [task];   // 提交顺序（旧→新）
}
/* 目录批量：每个样本自己的「模块结果页 + Downloads」。
   只渲染最新一条的 Downloads 会让用户以为批量只出了一个结果。 */
function batchDownloadsHtml(peers) {
  if (!peers || peers.length < 2) return '';
  return peers.map(p => {
    const r = p.result || {};
    const run = r.run || '';
    const label = p.sample || run || p.name || '';
    const mp = run
      ? `<button class="btn small primary" style="margin-left:8px"`
        + ` data-mp-url="/api/module_report?run=${encodeURIComponent(run)}"`
        + ` data-mp-title="${esc(label)} · ${esc(t('c.modulePage', '模块结果页'))}"`
        + ` onclick="previewModulePageFrom(this)">🧩 ${t('c.modulePage', '模块结果页')}</button>`
      : '';
    const dir = p.out_dir
      ? `<button class="btn small" style="margin-left:6px" data-d="${esc(p.out_dir)}"`
        + ` onclick="openOutDir(this.dataset.d)" title="${esc(t('c.openDir', '打开目录'))}">📂</button>`
      : '';
    return `<div style="border:1px solid var(--line-100);border-radius:8px;`
      + `padding:6px 10px;margin:8px 0;background:#fbfdfc">`
      + `<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">`
      + `<b>${esc(label)}</b>`
      + `<span class="sbadge ${esc(p.status)}">${esc(taskStatusText(p.status))}</span>`
      + `<span class="hint mono" style="font-size:11px">${esc(run)}</span>`
      + `${mp}${dir}</div>`
      + outFilesHtml(p)
      + `</div>`;
  }).join('');
}
function batchSummaryHtml(task, tasks) {
  if (!task || !task.batch) return '';
  const peers = (tasks || []).filter(x => x && x.batch === task.batch);
  if (peers.length < 2) return '';
  const ordered = peers.slice().reverse();      // tasks 新→旧，按提交顺序列
  const doneN = peers.filter(x => x.status === 'done').length;
  const failN = peers.filter(x => x.status === 'failed'
                              || x.status === 'cancelled').length;
  const runN = peers.length - doneN - failN;
  const _runs = peers.map(x => (x.result && x.result.run) || '').filter(Boolean);
  const _batchUrl = _runs.length > 1
    ? `/api/batch_report?runs=${encodeURIComponent(_runs.join(','))}`
      + `&title=${encodeURIComponent(t('c.batchPage', '批量模块结果页'))}`
    : '';
  const rows = ordered.map(x => {
    const r = x.result || {};
    const st = r.stats || {};
    const run = r.run || '';
    const label = x.sample || run || x.name || '';
    const bits = [];
    const add = (k, lab) => {
      if (st[k] != null && st[k] !== '') bits.push(`${lab} ${st[k]}`);
    };
    add('n_contigs', 'contigs'); add('N50', 'N50');
    add('GC(%)', 'GC%');
    add('n_extracted', t('tk.batchResExtract', '提取'));
    add('n_viral', t('tk.batchResViral', '病毒'));
    if (!bits.length) {
      if (st.total_bp) bits.push(`${st.total_bp} bp`);
      else if (x.status !== 'done' && x.msg) bits.push(x.msg);
      else if (run) bits.push(run);
    }
    const acts = [];
    if (run) {
      acts.push(`<button class="btn small"`
        + ` data-mp-url="/api/module_report?run=${encodeURIComponent(run)}"`
        + ` data-mp-title="${esc(label)} · ${esc(t('c.modulePage', '模块结果页'))}"`
        + ` onclick="previewModulePageFrom(this)">🧩 ${esc(t('tk.batchResView', '结果'))}</button>`);
    }
    if (x.out_dir) {
      acts.push(`<button class="btn small" data-d="${esc(x.out_dir)}"`
        + ` onclick="openOutDir(this.dataset.d)" title="${esc(t('c.openDir', '打开目录'))}">📂</button>`);
    }
    // 逐样本卡片：运行中带进度条 + 可展开日志（不只是状态行）
    const running = x.status === 'running';
    const pct = Math.min(100, Math.round((x.pct || 0) * 100));
    const prog = running
      ? `<div style="margin:6px 0 2px">`
        + `<div class="bar"><i style="width:${pct}%"></i></div>`
        + `<div style="display:flex;gap:8px;align-items:center;margin-top:2px">`
        + `<span class="hint" style="margin:0">${pct}%${x.stage ? ' · ' + esc(x.stage) : ''}${x.msg ? ' · ' + esc(trTaskMsg(x.msg)) : ''}</span>`
        + `</div></div>`
      : (x.status === 'failed' && x.error
          ? `<div class="err" style="margin:6px 0 0;font-size:12px">${esc(x.error)}</div>`
          : '');
    const logLines = cleanLogText(x.log || []);
    const logHtml = logLines.length
      ? `<details style="margin:6px 0 0">`
        + `<summary class="hint" style="cursor:pointer">📜 ${esc(t('c.logBtn', '日志'))}（${logLines.length} 行）</summary>`
        + `<pre class="logbox" style="max-height:160px;overflow-y:auto;margin:4px 0 0;font-size:11.5px"`
        + ` data-autoscroll="${running ? 1 : 0}">${esc(logLines.join('\n'))}</pre></details>`
      : '';
    return `<div style="border-bottom:1px dashed var(--line-100);padding:6px 0">`
      + `<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">`
      + `<b>${esc(label)}</b>`
      + `<span class="sbadge ${esc(x.status)}" title="${esc(x.msg || '')}">${esc(taskStatusText(x.status))}</span>`
      + `<span class="hint mono">${esc(run)}</span>`
      + `<span class="hint">${esc(bits.join(' · '))}</span>`
      + `<span style="flex:1"></span>`
      + `<span style="white-space:nowrap">${acts.join(' ')}</span></div>`
      + prog + logHtml
      + `</div>`;
  }).join('');
  return `<div style="margin:8px 0 10px;border:1px solid var(--line-100);`
    + `border-radius:8px;padding:8px 10px;background:#fbfdfc">`
    + `<div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:4px">`
    + `<b>📦 ${esc(t('tk.batchResTitle', '本次批量'))}：${peers.length} ${esc(t('tk.batchResUnit', '个样本'))}</b>`
    + (_batchUrl
        ? `<button class="btn small primary" data-mp-url="${esc(_batchUrl)}"`
          + ` data-mp-title="${esc(t('c.batchPage', '批量模块结果页'))}"`
          + ` onclick="previewModulePageFrom(this)">🧩 ${esc(t('c.batchPage', '批量模块结果页'))}</button>`
        : '')
    + `<span class="hint">${esc(t('tk.batchResDone', '已完成 {d}').replace('{d}', doneN))}`
    + (runN ? ` · ${esc(t('tk.batchResRunning', '运行/排队 {n}').replace('{n}', runN))}` : '')
    + (failN ? ` · ${esc(t('tk.batchResFail', '失败/取消 {n}').replace('{n}', failN))}` : '')
    + `</span></div>`
    + `<div class="scroll-sm" style="max-height:420px;overflow:auto">${rows}</div></div>`;
}

/* 日志行清洗：去掉子进程（datasets.exe / curl / gcc 系工具）留下的 ANSI
   转义序列与 \r 原地重绘帧。不清的话任务卡里全是「[INFO]   [0A[2K」这种
   空行，真正的进度信息反而看不见（2026-11-02 宿主基因组下载实测）。
   纯控制序列清完为空的行直接丢弃，不占版面。 */
function cleanLogText(lines) {
  const out = [];
  for (const raw of (lines || [])) {
    let s = String(raw == null ? '' : raw)
      .replace(/\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)/g, '')
      .replace(/\x1b\[[0-9;?]*[ -/]*[@-~]/g, '')
      .replace(/\x1b[@-Z\\-_]/g, '');
    if (s.indexOf('\r') >= 0) {
      const parts = s.split('\r').filter(x => x.trim());
      s = parts.length ? parts[parts.length - 1] : '';
    }
    s = s.replace(/\s+$/, '');
    if (s.trim()) out.push(s);
  }
  return out;
}

function toolRunHtml(task, boxId) {
  boxId = boxId || '';
  if (boxId) { OUT_TASKS[boxId] = task; if (!OUT_TAB_STATE[boxId]) OUT_TAB_STATE[boxId] = 'result'; }
  const pct = Math.round((task.pct || 0) * 100);
  const stat = taskStatusText(task.status);
  const err = task.error ? `<div class="err" style="margin:6px 0 0">${esc(task.error)}</div>` : '';
  const chips = task.status === 'done' ? toolStatChips(task.result) : '';
  // 宿主预测状态行：一条龙分类任务的宿主侧数字（n_host_pred/host_warn，
  // HP_STAT_KEYS 已从 chips 剔除）渲染成独立一行；失败不显示（err 已有）
  const st = (task.result && task.result.stats) || {};
  const zhHp = (typeof VP_LANG === 'undefined' || VP_LANG !== 'en');
  const hpLine = (task.status === 'done' && st.n_host_pred != null)
    ? `<div style="margin:8px 0 2px;font-size:12.5px;font-weight:700">${esc(t('c.hpTitle', '🧲 宿主预测'))} · ${taskStatusText('done')} · ${esc(st.n_host_pred)} ${zhHp ? '条' : 'contigs'}${st.host_warn > 0 ? ` · ⚠ ${esc(st.host_warn)} ${zhHp ? '条待复核' : 'need review'}` : ''}</div>`
    : '';
  // 输出目录：任务创建即带（运行中也能看到将写到哪），完成后结果里再确认一次
  const outdir = task.out_dir || (task.result && task.result.out_dir) || '';
  const lines = esc(cleanLogText(task.log).join('\n'));
  const _peers = batchPeers(task);
  const _isBatch = _peers.length > 1;
  // 纵向平铺：状态/进度 → 输出目录 → 宿主预测状态 → 关键数字 → 运行日志
  // → Downloads（下载）。产物文件统一在 Downloads 一处列出。
  // task-result：激活 .tr-stats（flex+gap）/ .tr-chip 样式——此前该类从未
  // 被任何容器挂载，关键数字 chips 一直以无间距裸文本粘连（2026-11-05）
  return `<div class="task-result" style="margin:10px 0 2px">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px">
      <span class="sbadge ${task.status}">${stat} · ${esc(task.name)}</span>
      ${taskButtons(task)}
    </div>
    <div class="bar"><i style="width:${pct}%"></i></div>
    ${err}${outdir ? outDirLine(outdir) : ''}${hpLine}${chips}
    <div style="font-size:12.5px;font-weight:700;margin:10px 0 4px">${t('c.runlog')}</div>
    <pre class="logbox" style="max-height:180px;overflow-y:auto" data-autoscroll="${task.status === 'running' ? 1 : 0}">${lines || t('c.noLog')}</pre>
    <div style="font-size:12.5px;font-weight:700;margin:10px 0 4px">${t('c.downloads')}
      ${_isBatch ? ' · ' + esc(t('c.batchDl', '本次批量 {n} 个样本').replace('{n}', _peers.length)) : ''}
      ${_isBatch
        ? `<button class="btn small" style="margin-left:8px"
             onclick="pushOutputToHistory(this)"
             title="${esc(t('c.toHistoryTip', '收起当前输出；结果保留在本卡底部「🕘 历史运行」，可随时 ↺ 还原'))}"
             >${t('c.toHistory', '📥 放入历史运行（全部）')}</button>`
        : (((task.status === 'done' || task.status === 'failed'
              || task.status === 'cancelled')
             && ((task.result && task.result.run) || task.out_dir))
            ? ((task.status === 'done' && task.result && task.result.run)
                ? `<button class="btn small primary" style="margin-left:8px"
                     data-mp-url="/api/module_report?run=${encodeURIComponent(task.result.run)}"
                     data-mp-title="${esc(task.result.run)} · ${esc(t('c.modulePage', '模块结果页'))}"
                     onclick="previewModulePageFrom(this)">🧩 ${t('c.modulePage', '模块结果页')}</button>`
                : '')
              + `<button class="btn small" style="margin-left:8px"
                   onclick="pushOutputToHistory(this)"
                   title="${esc(t('c.toHistoryTip', '收起当前输出；结果保留在本卡底部「🕘 历史运行」，可随时 ↺ 还原'))}"
                   >${t('c.toHistory', '📥 放入历史运行')}</button>`
            : '')}
    </div>
    ${_isBatch ? batchDownloadsHtml(_peers) : outFilesHtml(task)}
  </div>`;
}

/* 卡内「状态 + 进度条 + 实时日志」通用渲染（无 Downloads 区）。
   适用：数据资源页三张下载卡（宿主基因组 / NCBI datasets 病毒库 / datasets 通用）
   与数据库构建页的建库任务——这些任务的产物落在 host-db/、databases/ 下，不在
   tool_runs/，套 toolRunHtml 只会多一行「暂无可下载文件」。
   运行中但后端没有 ETA（大文件下载只能上报阶段，15% 会停很久）时进度条走
   不确定扫条（.bar.indet），避免被看成卡死的假进度。 */
function inlineTaskHtml(task) {
  const pct = Math.round((task.pct || 0) * 100);
  const stat = taskStatusText(task.status);
  const running = task.status === 'running';
  const indet = running && task.eta == null && pct < 100;
  const err = task.error ? `<div class="err" style="margin:6px 0 0">${esc(task.error)}</div>` : '';
  const outdir = task.out_dir || (task.result && task.result.out_dir) || '';
  const lines = esc(cleanLogText(task.log).join('\n'));
  let timing = '';
  if (running && task.started) {
    timing = ` <span class="hint">${t('c.elapsed')} ${fmtDur(Date.now() / 1000 - task.started)}</span>`;
  } else if (task.finished && task.started) {
    timing = ` <span class="hint">${t('c.used')} ${fmtDur(task.finished - task.started)}</span>`;
  }
  return `<div style="margin:10px 0 2px">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px">
      <span class="sbadge ${task.status}">${stat} · ${esc(task.name)}${timing}</span>
      ${taskButtons(task)}
    </div>
    <div class="bar${indet ? ' indet' : ''}"><i style="width:${indet ? 36 : pct}%"></i></div>
    ${task.msg ? `<div class="hint" style="margin:0 0 2px">${esc(trTaskMsg(task.msg))}</div>` : ''}
    ${err}${outdir ? outDirLine(outdir) : ''}
    <div style="font-size:12.5px;font-weight:700;margin:10px 0 4px">${t('c.runlog')}</div>
    <pre class="logbox" style="max-height:180px;overflow-y:auto" data-autoscroll="${running ? 1 : 0}">${lines || t('c.noLog')}</pre>
  </div>`;
}

function setOutTab(boxId, tab) {
  OUT_TAB_STATE[boxId] = tab;
  const task = OUT_TASKS[boxId];
  const box = $(boxId);
  if (task && box) box.innerHTML = toolRunHtml(task, boxId);
}
document.addEventListener('click', ev => {
  const btn = ev.target.closest?.('.out-tab');
  if (!btn) return;
  const holder = btn.closest('.toolrun');
  if (holder) setOutTab(holder.id, btn.dataset.tab);
});

/* ============================================================
 * 全局「上次运行结果」恢复（页面加载 / 模块切换时自动执行）
 * ------------------------------------------------------------
 * 工具卡的结果原本只在任务完成那一刻渲染进 DOM：跳到别的页面再回来，
 * 结果区就空了，只能重跑。这里在页面加载/模块切换后，从磁盘上的
 * tool_runs/（/api/tool/runs，按 mtime 倒序）找出每张**可见**卡片最近一次
 * 运行并自动恢复：
 *   - 通用卡：渲染「上次运行 · <run>」+ 产物下载块（outFilesHtml 复用）；
 *   - 富结果卡（SDT / 同一性）：调用其专用加载函数，恢复交互热图与矩阵表。
 * 有任务在跑、或卡片已有结果时不覆盖（交给轮询/用户操作）。
 * ============================================================ */
/* 值可为数组：同名卡有多套运行前缀时按序取最新（CLI 直跑的 crispr_cli_
   与网页版 crispr_ 是两套前缀，2026-11-04 修复：
   此前 CLI 跑的检测设计卡刷新后永远恢复不出来）。 */
const CARD_RUN_PREFIX = {
  convert: 'convert', fastp: 'fastp', identify: 'identify', assemble: 'assemble',
  contigs: 'contigs', verify: 'verify', virchain: 'virchain',
  consensus: 'consensus', kvsuite: 'kvsuite', kvchain: 'kvchain',
  variant: 'kvsuite', align: 'align', quicktree: 'quicktree', sdt: 'sdt',
  identity: 'identity',
  orf: 'orf', orfa: 'orfa', genoplot: 'genoplot', primer: 'primer',
  cmpchain: 'cmpchain', loganfetch: 'loganfetch',
  hostremoval: 'hostremoval', prepchain: 'prepchain', cdsexport: 'cdsexport',
  rdp: 'rdp', rtt: 'rtt', phylogeo: 'phylogeo', pdprep: 'pdprep',
  phylodyn: 'phylodyn', dsrna: 'dsrna', crispr: ['crispr', 'crispr_cli'],
  lamp: ['lamp', 'lamp_cli'],
  mirna: 'mirna', gbimport: 'gbimport', annochain: 'annochain',
  // 下载/导出类也有运行目录：给「放入历史」一个可落的历史盒
  beastexport: 'beastexport', gbdown: 'gbdown', ictv: 'ictv'
};
/* 富结果卡的恢复函数：刷新后直接重放渲染（表格/图/度量），而不是只给文件列表。
   全部是「跑完任务后本来就会调」的同一个函数，重复调用幂等；silent=true
   表示恢复路径——数据缺失/不匹配时返回 false 且不打扰用户（不弹窗、
   不写错误提示），恢复循环据此换上一次运行再试（2026-11-05 全面排查：
   变异卡曾把纯识别的 kvsuite_ 运行恢复成一块全 0 空面板）。 */
const TOOL_RESTORE = {
  sdt: async r => { sdRun = r; return await sdtExactLoad(true); },
  identity: async r => {
    if (typeof identityLoad === 'function') { await identityLoad(r, true); return true; }
    return false;
  },
  mirna: r => typeof loadMirnaResult === 'function' ? loadMirnaResult(r, true) : false,
  kvsuite: r => typeof loadKvsuiteResult === 'function' ? loadKvsuiteResult(r, true) : false,
  variant: r => typeof loadVariantResult === 'function' ? loadVariantResult(r, true) : false,
  consensus: r => typeof loadConsensusResult === 'function' ? loadConsensusResult(r, true) : false,
  verify: r => typeof loadVerifyResult === 'function' ? loadVerifyResult(r, true) : false,
  virchain: async r => {
    if (typeof loadChainResult !== 'function') return false;
    const ok = await loadChainResult('virchain', r, true);
    if (ok && typeof scanLoadSummary === 'function') scanLoadSummary('vir', r);
    return ok;
  },
  kvchain: async r => {
    if (typeof loadChainResult !== 'function') return false;
    const ok = await loadChainResult('kvchain', r, true);
    if (ok && typeof scanLoadSummary === 'function') scanLoadSummary('kv', r);
    return ok;
  },
  prepchain: r => typeof pcLoadSummary === 'function' ? pcLoadSummary(r) : undefined,
  rdp: r => typeof loadRdpResult === 'function' ? loadRdpResult(r) : undefined,
  rtt: r => typeof loadRttResult === 'function' ? loadRttResult(r) : undefined,
  phylogeo: r => typeof loadPhylogeoResult === 'function' ? loadPhylogeoResult(r) : undefined,
  pdprep: r => typeof loadPdprepResult === 'function' ? loadPdprepResult(r) : undefined,
  phylodyn: r => typeof loadPhylodynResult === 'function' ? loadPhylodynResult(r) : undefined,
  dsrna: r => typeof loadDsrnaResult === 'function' ? loadDsrnaResult(r) : undefined,
  crispr: r => typeof loadCrisprResult === 'function' ? loadCrisprResult(r) : undefined,
  lamp: r => typeof loadLampResult === 'function' ? loadLampResult(r) : undefined,
  orfa: r => { if (typeof loadOaResult === 'function') loadOaResult(); return true; },
  // 病毒鉴定 / 病毒识别：恢复「分类报告 + contig 明细 + 桑基图」整块，
  // 不是只铺一行「上次运行」。loadViralContigs 渲染表格，embedContigReport
  // 拉 /api/tool/report_data 画桑基/旭日/分类表。
  contigs: async r => {
    if (typeof anaCurRun === 'undefined') return false;
    anaCurRun = r;
    const lb = document.getElementById('anaRunLabel');
    if (lb) lb.textContent = r;
    try { if (typeof loadViralContigs === 'function') await loadViralContigs(); } catch (e) {}
    try { if (typeof embedContigReport === 'function') await embedContigReport(r); } catch (e) {}
    const cr = document.getElementById('contigReport');
    if (cr) { cr.style.display = ''; return true; }
    return false;
  },
  identify: async r => {
    const inp = document.getElementById('identRun');
    if (inp) inp.value = r;
    try { if (typeof lastIdentRun !== 'undefined') lastIdentRun = r; } catch (e) {}
    const rep = document.getElementById('identReport');
    if (rep) rep.style.display = '';
    try { if (typeof openIdentReport === 'function') await openIdentReport(); } catch (e) {}
    return !!rep;
  },
  // 注释一键链 / 比较基因组一键：各自有专用汇总面板与 loader，
  // 刷新后按 run 重放，不能只给一行文件列表。
  annochain: async r => {
    if (typeof loadAnnochainResult !== 'function') return false;
    try { await loadAnnochainResult(r); } catch (e) { return false; }
    const el = document.getElementById('anoResult');
    return !!(el && el.style.display !== 'none' && (el.innerText || '').trim());
  },
  cmpchain: async r => {
    if (typeof cmpRunName === 'undefined' || typeof cmpLoadResult !== 'function') return false;
    cmpRunName = r;
    try { await cmpLoadResult(); } catch (e) { return false; }
    const el = document.getElementById('cmpResult');
    return !!(el && (el.innerText || '').trim());
  }
};
/* 富结果框：用来判断「这张卡是否已有结果」，避免覆盖用户眼前的内容 */
const RICH_BOX = {
  sdt: 'sdExact', identity: 'idtResult', mirna: 'mirResult',
  kvsuite: 'kvResult', variant: 'vrResult', consensus: 'csResult',
  verify: 'vfResult', virchain: 'vcResult', kvchain: 'kvChainResult',
  prepchain: 'pcSummary',
  rdp: 'rdpResult', rtt: 'rttResult', phylogeo: 'pgResult',
  pdprep: 'pdprepResult', phylodyn: 'pdResult', dsrna: 'dsResult',
  crispr: 'crResult', lamp: 'lampResult', orfa: 'oaResult',
  annochain: 'anoResult', cmpchain: 'cmpResult'
};
/* 链式一键分析的**级联收起**（2026-11-06 用户报告「📥 放入历史没用」）：
   链是一次任务、一个主面板（virchain→vcResult），但链上各阶段的富结果
   会落到各自卡的专用面板（verify→vfResult、kvsuite→kvResult…）。
   只收主面板时，用户眼里的"那张分类表"还挂在屏上 → 看起来像点了没反应。
   链 run 放入历史时：panels 一并收起、keys 记入 RH_USER_TOUCHED（自动
   恢复不再拉回）、'#<前缀>' 伪条目记入收起集合（刷新后也不回弹）；
   历史区 ↺ 还原链 run 时级联解除（rhRestoreRun）。 */
const CHAIN_CASCADE = {
  virchain:  { panels: ['vfResult'], keys: ['verify'] },
  kvchain:   { panels: ['kvResult', 'vrResult', 'csResult'],
               keys: ['kvsuite', 'variant', 'consensus'] },
  annochain: { panels: ['oaResult'], keys: ['orfa'] },
  cmpchain:  { panels: ['sdExact'], keys: ['sdt'] },
  prepchain: { panels: [], keys: [] },
};
/* 没有独立卡片、只能按容器探测的富结果（identity 是 SDT 卡里的另一套面板） */
const RICH_ONLY = { identity: 'idtResult', cmpchain: 'cmpResult' };
function runPrefixOf(name) {
  const m = String(name || '').match(/^(.+?)_\d{8}_\d{6}/);
  return m ? m[1] : String(name || '');
}
/* 卡片「已有结果」探测：富结果看专用容器，其余看通用结果框 */
function cardResultFilled(key) {
  const probe = RICH_BOX[key];
  if (probe) {
    const el = $(probe);
    if (!el) return false;
    return !!(el.innerHTML.trim() && el.style.display !== 'none');
  }
  const box = $('toolrun-' + key);
  return !!(box && box.innerHTML.trim());
}
/* 本页需要恢复哪些卡：可见的通用结果框 + 可见的富容器 */
function restoreTargets() {
  const keys = [];
  document.querySelectorAll('.toolrun[id^="toolrun-"]').forEach(box => {
    if (box.offsetParent === null) return;          // /tools 一次只显示一个二级模块
    const key = box.id.slice('toolrun-'.length);
    if (CARD_RUN_PREFIX[key]) keys.push(key);
  });
  for (const key in RICH_ONLY) {
    if (keys.indexOf(key) >= 0 || !CARD_RUN_PREFIX[key]) continue;
    const el = $(RICH_ONLY[key]);
    /* identity（NT+AA 同一性）等「藏在可见卡里的富面板」初始 display:none：
       只看自身可见会永远选不中（sdt 卡切过去后同一性面板一直恢复不出来）。
       所在卡可见即算候选 —— 真正的「已有结果」判定交给 cardResultFilled。 */
    const card = el && el.closest('section.card, .card');
    if (el && (el.offsetParent !== null ||
               (card && card.offsetParent !== null))) keys.push(key);
  }
  return keys;
}
function runRestoreHtml(run) {
  const fake = { status: 'done', out_dir: run.out_dir || '',
                 result: { run: run.name, files: run.files || [] } };
  return `<div style="margin:10px 0 2px">
      <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;margin-bottom:4px">
        <span class="sbadge done">${t('c.restoredRun', '上次运行')} · ${esc(run.name)}</span>
        <span class="hint">${t('c.restoredHint', '结果已从磁盘恢复，无需重跑')}</span>
      </div>
      ${outDirLine(run.out_dir)}
      ${(() => {
        // 恢复结果不只是一行下载链接：图文件直接铺成缩略图，点开看原图。
        const imgs = (run.files || [])
          .filter(f => /[.](png|jpe?g|webp|gif|svg)$/i.test(f.path)).slice(0, 8);
        if (!imgs.length) return '';
        const base = '/tool_runs/' + encodeURIComponent(run.name) + '/';
        return '<div style="display:flex;gap:8px;flex-wrap:wrap;margin:8px 0">'
          + imgs.map(f => {
              const rel = String(f.path).split(String.fromCharCode(92)).join('/');
              const url = base + rel.split('/').map(encodeURIComponent).join('/');
              return `<a href="${url}" target="_blank" rel="noopener" title="${esc(f.path)}">`
                + `<img src="${url}" loading="lazy" style="height:120px;max-width:220px;object-fit:contain;`
                + `border:1px solid var(--line-200);border-radius:6px;background:#fff;padding:4px"></a>`;
            }).join('')
          + '</div>';
      })()}
      <div style="font-size:12.5px;font-weight:700;margin:10px 0 4px">${t('c.downloads')}
        <button class="btn small primary" style="margin-left:8px"
                data-mp-url="/api/module_report?run=${encodeURIComponent(run.name)}"
                data-mp-title="${esc(run.name)} · ${esc(t('c.modulePage', '模块结果页'))}"
                onclick="previewModulePageFrom(this)">🧩 ${t('c.modulePage', '模块结果页')}</button>
        <button class="btn small" style="margin-left:8px"
                data-run="${esc(run.name)}"
                onclick="putRestoredRunToHistory(this)"
                title="${esc(t('c.toHistoryTip', '收起当前输出；结果保留在本卡底部「🕘 历史运行」，可随时 ↺ 还原'))}"
                >${t('c.toHistory', '📥 放入历史运行')}</button>
      </div>
      ${outFilesHtml(fake)}
    </div>`;
}
/* 还原块专用「放入历史」：还原块没有 OUT_TASKS 任务对象，run 名直接从
   data-run 取；不需要归档任务（还原的运行多半早已归档/不在任务列表）。 */
function putRestoredRunToHistory(btn) {
  const run = btn && btn.dataset && btn.dataset.run;
  if (!run) { toast(t('c.toHistoryFail', '没有找到这次运行的目录'), '', { ttl: 4000 }); return; }
  const holder = btn.closest && btn.closest('.toolrun');
  if (holder) holder.innerHTML = '';
  // 兜底：拿不到容器时按运行前缀反查卡 key（历史盒也要能展开）
  let key = holder ? holder.id.replace(/^toolrun-/, '') : '';
  if (!key && typeof CARD_RUN_PREFIX !== 'undefined') {
    for (const k in CARD_RUN_PREFIX) {
      const prefs = Array.isArray(CARD_RUN_PREFIX[k])
        ? CARD_RUN_PREFIX[k] : [CARD_RUN_PREFIX[k]];
      if (prefs.some(pf => String(run).startsWith(pf))) { key = k; break; }
    }
  }
  if (key && typeof RH_PINNED !== 'undefined') delete RH_PINNED[key];
  { const s = outHiddenSet(); s.add(String(run)); outSaveHidden(s); }
  try { hideResultPanelsForRun(run, key); } catch (e) {}
  if (typeof refreshTasks === 'function') { try { refreshTasks(); } catch (e) {} }
  const rh = historyBoxId(key);
  if (rh) {
    const body = rh.querySelector('.rh-body');
    if (body && body.style.display === 'none' && typeof rhToggle === 'function')
      rhToggle(rh.id);
    if (typeof rhRefresh === 'function') { try { rhRefresh(); } catch (e) {} }
    try { rh.scrollIntoView({ block: 'center', behavior: 'smooth' }); } catch (e) {}
  }
  toast(t('c.toHistoryDone', '已放入历史运行'), run, { ttl: 4000 });
}
let _restoreBusy = false;
async function restoreLastResults() {
  if (_restoreBusy) return;
  const keys = restoreTargets();
  if (!keys.length) return;
  _restoreBusy = true;
  try {
    /* per=4：每个模块前缀取最近 4 次。以前 per=1 只看最近一次——变异卡
       映射的 kvsuite_ 前缀里识别运行与变异运行混在一起，最近一次若是
       识别跑的，变异卡就恢复不出真正有数据的那次（2026-11-05 用户反馈
       「恢复出一块全 0 空面板」）。恢复器返回 false（本次运行没有本卡
       数据）时按新旧顺序换上一次运行再试。 */
    const runs = await (await fetch('/api/tool/runs?per=4')).json();
    const byPrefix = {};
    for (const r of (runs || [])) {
      const p = r.prefix || runPrefixOf(r.name);
      (byPrefix[p] = byPrefix[p] || []).push(r);   // 接口已按 mtime 倒序
    }
    for (const key of keys) {
      if (cardResultFilled(key)) continue;         // 眼前已有结果就不动
      // 用户亲手动过的卡（✕ 收起富面板 / ↺ 手动还原）：自动恢复不再补
      // 「最近一次运行」——收起就是收起、还原画面保持稳定，直到新任务产出
      // 自然替换。标记入口：✕ 捕获监听 + rhRestoreRun（app-runhistory.js）；
      // 清除入口：任务认领（新任务渲染覆盖画面时）。注意不能用
      // innerHTML 非空判断「用户关过」：富面板模板骨架本身就非空。
      if (typeof RH_USER_TOUCHED !== 'undefined'
          && RH_USER_TOUCHED.has(key)) continue;
      const pref = CARD_RUN_PREFIX[key];
      const pfx = Array.isArray(pref)
        ? pref.find(p => byPrefix[p]) || pref[0]
        : pref;
      // 手动「放入历史运行」的 run 不再自动恢复（刷新后也一样）；
      // 需要时在历史区点 ↺ 还原（outUnhideRun 会解除）。
      const _hid = outHiddenSet();
      // 同一次批量提交的 run 共享 <tool>_<时间戳> 前缀；隐藏其中一条后，
      // 整批都不再自动回填（否则刷新后又冒出一条“唯一结果”）。
      const _hidBatch = [];
      for (const h of _hid) {
        const m = /^(.+?_[0-9]{8}_[0-9]{6})/.exec(h);
        if (m) _hidBatch.push(m[1]);
      }
      const cands = (byPrefix[pfx] || []).filter(r => !_hid.has(r.name)
        && !_hid.has('#' + runPrefixOf(r.name))
        && !_hidBatch.some(p => String(r.name).indexOf(p + '_') === 0));
      if (!cands.length) continue;
      if (TOOL_RESTORE[key]) {
        for (const cand of cands.slice(0, 4)) {
          let ok = true;
          try { ok = await TOOL_RESTORE[key](cand.name) !== false; }
          catch (e) { ok = false; }                // 恢复失败不打扰用户
          if (ok) break;
        }
      } else {
        const box = $('toolrun-' + key);
        if (box) box.innerHTML = runRestoreHtml(cands[0]);
      }
    }
  } catch (e) { /* 静默：恢复失败不影响正常使用 */ }
  finally { _restoreBusy = false; }
  try { hideHiddenResultPanels(); } catch (e) {}
}
/* 页面加载 / 模块切换 / 浏览器前进后退（bfcache）各触发一次。
   注意：这里只「补空」，不会清掉任何已有结果；换新任务时由任务自身的
   轮询把旧结果替换掉，所以旧结果会一直留到下一个任务产出为止。 */
window.addEventListener('load', () => {
  setTimeout(restoreLastResults, 900);
  // 页面快照恢复在 load 之后回填 DOM：再补两次，避免「放入历史」的面板被快照拉回
  setTimeout(hideHiddenResultPanels, 1500);
  setTimeout(hideHiddenResultPanels, 3200);
});
window.addEventListener('hashchange', () => setTimeout(restoreLastResults, 600));
window.addEventListener('pageshow', ev => {
  if (ev.persisted) setTimeout(restoreLastResults, 300);
});

function stageLogHtml(task) {
  const stat = taskStatusText(task.status);
  const lines = esc(cleanLogText(task.log).join('\n'));
  const closed = logClosedSet().has(task.id);
  return `
    <div style="display:flex;justify-content:space-between;align-items:center;margin:8px 0 4px">
      <span class="sbadge ${task.status}">${stat} · ${t('c.liveLog')}</span>
      <span>
        <button class="btn small" onclick="logToggle('${task.id}')" title="折叠/展开日志">${closed ? '▸' : '▾'} ${t('c.logBtn', '日志')}</button>
        <button class="btn small" onclick="copyStageLog(this)">${t('c.copyLog')}</button>
        ${taskButtons(task)}
      </span>
    </div>
    <pre class="logbox" style="margin:0;${closed ? 'display:none' : ''}" data-tid="${esc(task.id)}" data-autoscroll="${task.status === 'running' ? 1 : 0}">${lines || t('c.noLog')}</pre>`;
}

function copyStageLog(btn) {
  const pre = btn.closest('.stage-log')?.querySelector('pre.logbox');
  if (!pre) return;
  const text = pre.textContent;
  (navigator.clipboard?.writeText(text) ?? Promise.reject())
    .then(() => { btn.textContent = t('c.copied'); setTimeout(() => btn.textContent = t('c.copyLog'), 1500); })
    .catch(() => {
      const ta = document.createElement('textarea');
      ta.value = text; document.body.appendChild(ta); ta.select();
      document.execCommand('copy'); ta.remove();
      btn.textContent = t('c.copied'); setTimeout(() => btn.textContent = t('c.copyLog'), 1500);
    });
}

/* 日志滚动策略：重绘前记录每个日志框 scrollTop 与是否贴底；重绘后——
   运行中且原本贴底 → 跟随新日志；其余（含已结束任务、用户上翻阅读中）
   → 精确恢复原位置，不再被 2.5s 轮询重绘拉回顶部。 */
const _logScrollMemo = new WeakMap();
function snapshotLogScroll() {
  document.querySelectorAll('.toolrun, .stage-log, .buildrun').forEach(box => {
    const st = [...box.querySelectorAll('pre.logbox')].map(pre => ({
      top: pre.scrollTop,
      atBottom: pre.scrollHeight - pre.scrollTop - pre.clientHeight < 16
    }));
    _logScrollMemo.set(box, st);
  });
}
function restoreLogScroll() {
  document.querySelectorAll('.toolrun, .stage-log, .buildrun').forEach(box => {
    const memo = _logScrollMemo.get(box) || [];
    [...box.querySelectorAll('pre.logbox')].forEach((pre, i) => {
      const st = memo[i];
      if (pre.dataset.autoscroll === '1' && (!st || st.atBottom)) {
        pre.scrollTop = pre.scrollHeight;
      } else if (st) {
        pre.scrollTop = st.top;
      }
    });
  });
}
function autoscrollLogs() { restoreLogScroll(); }

function toggleLog(tid) {
  taskLogOpen.has(tid) ? taskLogOpen.delete(tid) : taskLogOpen.add(tid);
  refreshTasks();
}
function logClosedSet() {
  try { return new Set(JSON.parse(sessionStorage.getItem('vp_logclosed') || '[]')); }
  catch (e) { return new Set(); }
}
function logToggle(tid) {
  const s = logClosedSet();
  s.has(tid) ? s.delete(tid) : s.add(tid);
  try { sessionStorage.setItem('vp_logclosed', JSON.stringify([...s])); } catch (e) {}
  refreshTasks();
}

async function cancelTask(tid) {
  await fetch('/api/task/' + tid + '/cancel', {method: 'POST'});
}

async function pauseTask(tid) {
  const r = await fetch('/api/task/' + tid + '/pause', {method: 'POST'});
  const d = await r.json().catch(() => ({}));
  if (d && d.ok === false) toast(t('c.pauseNo'), 'warn');
  if (typeof refreshTasks === 'function') refreshTasks();
}

async function resumeTask(tid) {
  const r = await fetch('/api/task/' + tid + '/resume', {method: 'POST'});
  const d = await r.json().catch(() => ({}));
  if (d && d.ok === false) toast(t('c.resumeNo'), 'warn');
  if (typeof refreshTasks === 'function') refreshTasks();
  refreshTasks();
}

