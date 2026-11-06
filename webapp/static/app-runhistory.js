// ---------------- 模块历史运行（折叠 · 上下游衔接 · 删除） ----------------
// 2026-09-13 自 app.js 拆出。普通 script 共享全局作用域：本文件在
// app.js 之后加载，可直接使用 $/esc/t/toast/jfetch 等核心工具；页面
// 内联脚本与其它 app-*.js 里的同名调用按全局解析（加载顺序见模板）。
// 注意：本块内引用的其它 app-*.js 函数均为调用期解析（事件/回调触发），
// 不存在加载期交叉依赖。
// ---------------- 模块历史运行（折叠 · 上下游衔接 · 删除） ----------------
/* 每个工具卡/独立模块页的持久历史：数据来自 /api/tool/runs（tool_runs/ 目录，
   重启不丢）。条目可展开文件下载、一键复用为下游模块输入（跨页暂存回填）、
   可删除。默认折叠，避免过多。 */
const RH_PAGES = {
  f_r1: '/tools#t-fastp', f_r2: '/tools#t-fastp',
  hr_r1: '/hostremoval', hr_r2: '/hostremoval',
  i_input: '/tools#t-identify', i_input2: '/tools#t-identify',
  a_r1: '/tools#t-assemble', a_r2: '/tools#t-assemble',
  c_fa: '/tools#t-contigs',
  of_fa: '/orf', oa_fa: '/annotation', oa_run: '/annotation',
  gp_fa: '/genome', gp_ann: '/genome', pr_fa: '/primer',
  al_fa: '/tools#t-align', qt_fa: '/tools#t-treebuild',
  tv_file: '/tools#t-treebuild', sd_fa: '/tools#t-sdt'
};

function rhPick(files, sub, excl) {
  return ((files || []).filter(f => (f.path || '').includes(sub)
                            && !(excl && (f.path || '').includes(excl)))[0] || {}).path;
}
/* 共识序列 → 下游分析衔接（2026-11-05 接通第一批）：.consensus.fasta 是
   kvsuite / consensus / virchain / kvchain 的稳定产物名（kv_consensus.py:
   {L2}.consensus.fasta）。产物存在才显示按钮，没有该产物就不出链。 */
const RH_CONSENSUS_CHAINS = files => {
  const con = rhPick(files, '.consensus.fasta');
  if (!con) return [];
  return [
    { label: '→ 比对', page: RH_PAGES.al_fa, fills: { al_fa: con } },
    { label: '→ 建树', page: RH_PAGES.qt_fa, fills: { qt_fa: con } },
    { label: '→ SDT',  page: RH_PAGES.sd_fa, fills: { sd_fa: con } },
    { label: '→ ORF',  page: RH_PAGES.of_fa, fills: { of_fa: con } }
  ];
};
function rhFqPair(files) {
  const fqs = (files || []).map(f => f.path).filter(p => /\.fastq\.gz$/.test(p));
  if (!fqs.length) return null;
  const num = p => { const b = p.replace(/\.fastq\.gz$/, '').split(/[\/]/).pop();
                     return b.endsWith('_1') || b.endsWith('.1') || b.endsWith('_R1') ? 1
                          : b.endsWith('_2') || b.endsWith('.2') || b.endsWith('_R2') ? 2 : 0; };
  const r1 = fqs.find(p => num(p) === 1), r2 = fqs.find(p => num(p) === 2);
  if (r1 && r2) return [r1, r2];
  return [fqs[0], null];                       // 单端
}
const RH_DEFS = [
  { box: 'rh-convert', prefixes: ['convert_'],
    chains: files => {
      const out = [];
      const fa = rhPick(files, '.fa.gz') || rhPick(files, '.fasta.gz');
      const fq = rhFqPair(files);
      if (fq) out.push(
        { label: '→ 质控', page: RH_PAGES.f_r1,
          fills: { f_r1: fq[0], ...(fq[1] ? { f_r2: fq[1] } : {}) } },
        { label: '→ 宿主去除', page: RH_PAGES.hr_r1,
          fills: { hr_r1: fq[0], ...(fq[1] ? { hr_r2: fq[1] } : {}) } },
        { label: '→ 病毒识别', page: RH_PAGES.i_input,
          fills: { i_input: fq[0], ...(fq[1] ? { i_input2: fq[1] } : {}) } });
      if (fa) out.push(
        { label: '→ 识别(FASTA)', page: RH_PAGES.i_input,
          fills: { i_input: fa, i_type: 'fasta' } },
        { label: '→ ORF', page: RH_PAGES.of_fa, fills: { of_fa: fa } },
        { label: '→ 比对', page: RH_PAGES.al_fa, fills: { al_fa: fa } },
        { label: '→ 建树', page: RH_PAGES.qt_fa, fills: { qt_fa: fa } },
        { label: '→ SDT', page: RH_PAGES.sd_fa, fills: { sd_fa: fa } });
      return out;
    } },
  { box: 'rh-fastp', prefixes: ['fastp_'],
    chains: files => {
      const p = f => rhPick(files, f);
      const r1 = p('fastp_R1'), r2 = p('fastp_R2');
      if (!r1) return [];
      return [
        { label: '→ 宿主去除', page: RH_PAGES.hr_r1,
          fills: { hr_r1: r1, hr_r2: r2 || '' } },
        { label: '→ 病毒识别', page: RH_PAGES.i_input,
          fills: { i_input: r1, i_input2: r2 || '' } },
        { label: '→ 组装', page: RH_PAGES.a_r1,
          fills: { a_r1: r1, a_r2: r2 || '' } }];
    } },
  { box: 'rh-hostremoval', prefixes: ['hostremoval_'],
    chains: files => {
      const r1 = rhPick(files, 'kept_R1'), r2 = rhPick(files, 'kept_R2');
      if (!r1) return [];
      return [
        { label: '→ 病毒识别', page: RH_PAGES.i_input,
          fills: { i_input: r1, i_input2: r2 || '' } },
        { label: '→ 组装', page: RH_PAGES.a_r1,
          fills: { a_r1: r1, a_r2: r2 || '' } },
        { label: '→ 质控', page: RH_PAGES.f_r1,
          fills: { f_r1: r1, f_r2: r2 || '' } }];
    } },
  { box: 'rh-identify', prefixes: ['identify_'],
    chains: files => {
      const fa = rhPick(files, 'viral_sequences');
      if (!fa) return [];
      return [
        { label: '→ 组装', page: RH_PAGES.a_r1, fills: { a_r1: fa } },
        { label: '→ ORF', page: RH_PAGES.of_fa, fills: { of_fa: fa } },
        { label: '→ 比对', page: RH_PAGES.al_fa, fills: { al_fa: fa } },
        { label: '→ 建树', page: RH_PAGES.qt_fa, fills: { qt_fa: fa } },
        { label: '→ SDT', page: RH_PAGES.sd_fa, fills: { sd_fa: fa } }];
    } },
  { box: 'rh-assemble', prefixes: ['assemble_'],
    chains: files => {
      const fa = rhPick(files, 'contigs.filtered.fasta');
      if (!fa) return [];
      return [
        { label: '→ contigs 分类', page: RH_PAGES.c_fa, fills: { c_fa: fa } },
        { label: '→ ORF', page: RH_PAGES.of_fa, fills: { of_fa: fa } },
        { label: '→ 比对', page: RH_PAGES.al_fa, fills: { al_fa: fa } },
        { label: '→ 建树', page: RH_PAGES.qt_fa, fills: { qt_fa: fa } },
        { label: '→ SDT', page: RH_PAGES.sd_fa, fills: { sd_fa: fa } },
        { label: '→ 图谱', page: RH_PAGES.gp_fa, fills: { gp_fa: fa } },
        { label: '→ 引物', page: RH_PAGES.pr_fa, fills: { pr_fa: fa } }];
    } },
  { box: 'rh-contigs', prefixes: ['contigs_'],
    chains: files => {
      const fa = rhPick(files, 'viral_contigs.fasta');
      const out = [];
      if (fa) out.push(
        { label: '→ ORF', page: RH_PAGES.of_fa, fills: { of_fa: fa } },
        { label: '→ 功能注释', page: RH_PAGES.oa_fa, fills: { oa_fa: fa } },
        { label: '→ 比对', page: RH_PAGES.al_fa, fills: { al_fa: fa } },
        { label: '→ 建树', page: RH_PAGES.qt_fa, fills: { qt_fa: fa } },
        { label: '→ SDT', page: RH_PAGES.sd_fa, fills: { sd_fa: fa } },
        { label: '→ 图谱', page: RH_PAGES.gp_fa, fills: { gp_fa: fa } },
        { label: '→ 引物', page: RH_PAGES.pr_fa, fills: { pr_fa: fa } });
      /* 「→ 宿主预测」链随 /hostpredict 孤儿页删除（2026-09-27）一并移除：
         宿主预测现在由 contigs 分类后自动运行（tools.html autoHostIfMissing），
         不再需要手动载入分类表。 */
      return out;
    } },
  { box: 'rh-orf', prefixes: ['orf_'],
    chains: (files, run) => {
      const out = [];
      const faa = rhPick(files, 'pyrodigal.faa');
      const nt = rhPick(files, 'pyrodigal.ffn');
      if (faa) out.push(
        { label: '→ 比对(蛋白)', page: RH_PAGES.al_fa, fills: { al_fa: faa } },
        { label: '→ SDT(AA)', page: RH_PAGES.sd_fa, fills: { sd_fa: faa } });
      if (nt) out.push(
        { label: '→ SDT(NT)', page: RH_PAGES.sd_fa, fills: { sd_fa: nt } });
      out.push({ label: '→ 功能注释(该运行)', page: '/annotation',
                 fills: { oa_run: run.name } });
      return out;
    } },
  { box: 'rh-orfa', prefixes: ['orfa_'],
    chains: (files, run) => {
      const gff = rhPick(files, 'orf_annotation.gff3');
      const fa = rhPick(files, 'viral_contigs.fasta');
      const out = [];
      if (gff && fa) out.push(
        { label: '→ 基因组图谱(注释)', page: '/genome',
          fills: { gp_ann: gff, gp_fa: fa } });
      const realRun = (run.name || '').replace(/^orfa_/, 'orf_');
      out.push({ label: '→ 功能注释(orf 运行)', page: '/annotation',
                 fills: { oa_run: realRun } });
      return out;
    } },
  { box: 'rh-verify', prefixes: ['verify_', 'hom_'],
    chains: files => {
      // 验证通过/候选的序列 → 下游注释分析（历史运行此前没登记，这个盒子一直是空的）
      // 2026-11-03：hom_（单条深查）运行同盒列出，input.fasta 一样可直送下游
      const fa = rhPick(files, 'virus_input.fasta')
        || rhPick(files, 'viroid_input.fasta')
        || rhPick(files, 'input.fasta');
      if (!fa) return [];
      return [
        { label: '→ ORF', page: RH_PAGES.of_fa, fills: { of_fa: fa } },
        { label: '→ 功能注释', page: RH_PAGES.oa_fa, fills: { oa_fa: fa } },
        { label: '→ 比对', page: RH_PAGES.al_fa, fills: { al_fa: fa } },
        { label: '→ 建树', page: RH_PAGES.qt_fa, fills: { qt_fa: fa } },
        { label: '→ SDT', page: RH_PAGES.sd_fa, fills: { sd_fa: fa } }];
    } },
  { box: 'rh-genoplot', prefixes: ['genoplot_'], chains: () => [] },
  { box: 'rh-primer', prefixes: ['primer_'], chains: () => [] },
  { box: 'rh-align', prefixes: ['align_'],
    chains: files => {
      const aln = rhPick(files, 'aln.trim.fasta') || rhPick(files, 'aln.fasta');
      if (!aln) return [];
      return [
        { label: '→ 建树', page: RH_PAGES.qt_fa, fills: { qt_fa: aln } },
        { label: '→ SDT(已比对)', page: RH_PAGES.sd_fa,
          fills: { sd_fa: aln, sd_aligned: true } }];
    } },
  { box: 'rh-treebuild', prefixes: ['structcmp_', 'quicktree_'],
    chains: files => {
      const out = [];
      const aln = rhPick(files, 'aln.fasta');
      const nwk = rhPick(files, 'nj.nwk') || rhPick(files, 'tree.nwk');
      if (aln) out.push(
        { label: '→ 建树', page: RH_PAGES.qt_fa, fills: { qt_fa: aln } },
        { label: '→ SDT(已比对)', page: RH_PAGES.sd_fa,
          fills: { sd_fa: aln, sd_aligned: true } });
      if (nwk) out.push(
        { label: '→ 树查看', page: RH_PAGES.tv_file, fills: { tv_file: nwk } });
      return out;
    } },
  { box: 'rh-sdt', prefixes: ['sdt_', 'identity_'],
    chains: files => {
      const out = [];
      const csv = rhPick(files, 'sdt_matrix.csv');
      if (csv) out.push({ label: '⬇ 矩阵 CSV', page: '', fills: {}, dl: csv });
      return out;
    } },
  /* —— 2026-11-05 补登记：此前这批模块的历史区整块缺失（用户报告
     「每个模块的历史任务，有的有，有的缺少」）。两类缺口：
     ① 容器已在模板里（rh-kvsuite / rh-consensus / rh-annochain /
        rh-prepchain / rh-cdsexport / rh-loganfetch）但没有 RH_DEFS 条目，
        rhInitContainers 找不到登记直接跳过，容器永远是空壳；
     ② 连容器都没有（crispr / conan / cricon / mirna / phylogeo / rdp /
        rtt / phylodyn / pdprep / dsrna / virchain / kvchain / gbimport）。
     新条目用 after 指到各卡输出区（toolrun-<key>），页面没预留容器时由
     rhInitContainers 自动注入，模板不再需要挨个加 <div class="rh">。
     chains 留空（历史区照常有文件/复用展开/归档/删除/打开目录，
     下游衔接链后续按需补）。 —— */
  { box: 'rh-prepchain',  prefixes: ['prepchain_'],  after: 'toolrun-prepchain' },
  { box: 'rh-kvsuite',    prefixes: ['kvsuite_'],    after: 'toolrun-kvsuite',
    chains: RH_CONSENSUS_CHAINS },
  { box: 'rh-consensus',  prefixes: ['consensus_'],  after: 'toolrun-consensus',
    chains: RH_CONSENSUS_CHAINS },
  { box: 'rh-annochain',  prefixes: ['annochain_'],  after: 'toolrun-annochain' },
  { box: 'rh-cdsexport',  prefixes: ['cdsexport_'],  after: 'toolrun-cdsexport' },
  { box: 'rh-loganfetch', prefixes: ['loganfetch_'], after: 'toolrun-loganfetch' },
  { box: 'rh-virchain',   prefixes: ['virchain_'],   after: 'toolrun-virchain',
    chains: files => {
      const out = RH_CONSENSUS_CHAINS(files);
      const vc = rhPick(files, 'viral_contigs.fasta');
      if (vc) out.push({ label: '→ contigs 深度分析',
                         page: RH_PAGES.c_fa, fills: { c_fa: vc } });
      return out;
    } },
  { box: 'rh-kvchain',    prefixes: ['kvchain_'],    after: 'toolrun-kvchain',
    chains: RH_CONSENSUS_CHAINS },
  { box: 'rh-gbimport',   prefixes: ['gbimport_'],   after: 'toolrun-gbimport' },
  { box: 'rh-rdp',        prefixes: ['rdp_'],        after: 'toolrun-rdp' },
  { box: 'rh-rtt',        prefixes: ['rtt_'],        after: 'toolrun-rtt' },
  { box: 'rh-phylogeo',   prefixes: ['phylogeo_'],   after: 'toolrun-phylogeo' },
  { box: 'rh-pdprep',     prefixes: ['pdprep_'],     after: 'toolrun-pdprep' },
  { box: 'rh-phylodyn',   prefixes: ['phylodyn_'],   after: 'toolrun-phylodyn' },
  { box: 'rh-dsrna',      prefixes: ['dsrna_'],      after: 'toolrun-dsrna' },
  // 检测设计三件套：CLI 直跑的是另一套前缀（与 CARD_RUN_PREFIX 同口径）
  { box: 'rh-crispr', prefixes: ['crispr_', 'crispr_cli_'], after: 'toolrun-crispr' },
  { box: 'rh-cricon', prefixes: ['cricon_', 'cricon_cli_'], after: 'toolrun-cricon' },
  { box: 'rh-conan',  prefixes: ['conan_'],                 after: 'toolrun-conan' },
  { box: 'rh-mirna',      prefixes: ['mirna_'],      after: 'toolrun-mirna' },
  { box: 'rh-lamp',       prefixes: ['lamp_', 'lamp_cli_'], after: 'toolrun-lamp' },
  { box: 'rh-cmpchain',   prefixes: ['cmpchain_'],       after: 'cmpResult' },
  { box: 'rh-beastexport', prefixes: ['beastexport_'],   after: 'toolrun-beastexport' },
  { box: 'rh-gbdown',      prefixes: ['gbdown_'],        after: 'toolrun-gbdown' },
  { box: 'rh-ictv',        prefixes: ['ictv_'],          after: 'toolrun-ictv' }
];

let RH_DATA = null;
let RH_TOTALS = {};       // 前缀 → 全盘真实条数（/api/tool/runs?with_totals=1）
const RH_OPEN = new Set();
/* 用户亲手动过的卡（手动 ↺ 还原 / 手动收起富面板）：自动恢复
   （restoreLastResults，页面加载/切模块时跑）对这些卡不再自动补「最近
   一次运行」，还原/收起状态保持稳定，直到新任务产出自然替换。
   标记入口：rhRestoreRun（还原成功后）；清除入口：任务认领（新任务渲染
   覆盖画面时，app-jobs.js injectStageLogs）。 */
const RH_USER_TOUCHED = new Set();

async function rhRefresh() {
  try {
    // per=8：每个模块各取最近 8 次。用默认的「全局 30 条」时，老模块
    // （align / hostremoval / rdp …）的历史会被新模块挤空，展开是空的。
    // with_totals=1：顺带取全盘按前缀的真实条数 —— per=8 只列最近 8 条，
    // 计数器此前封顶显示 8（2026-11-05 用户报告），改显真数。
    const r = await fetch('/api/tool/runs?per=8&with_totals=1');
    const data = await r.json();
    RH_DATA = Array.isArray(data) ? data : (data.runs || []);
    RH_TOTALS = Array.isArray(data) ? {} : (data.totals || {});
  } catch (e) { RH_DATA = []; RH_TOTALS = {}; }
  for (const def of RH_DEFS) {
    const box = $(def.box);
    if (!box) continue;
    const runs = RH_DATA.filter(x => def.prefixes.some(p => x.name.startsWith(p)));
    const n = runs.length;
    // 真实计数：totals 键不带结尾下划线（与 rhClean 送参口径一致）。
    // 接口旧形态（纯数组、无 totals）时回落为列表条数，不比旧版差。
    const prefKeys = def.prefixes.map(p => p.replace(/_+$/, ''));
    const real = prefKeys.reduce((s, p) => s + (RH_TOTALS[p] || 0), 0);
    const shown = real > n ? real : n;
    const head = box.querySelector('.rh-head');
    if (head) head.querySelector('.rh-count').textContent = shown;
    const body = box.querySelector('.rh-body');
    if (!body) continue;
    if (!n) { body.innerHTML = '<p class="hint">（暂无历史运行）</p>'; continue; }
    const moreHint = shown > n
      ? `<div class="hint" style="margin:2px 0 4px">${
          t('ui.rh.totalHint', '共 {total} 条 · 仅显示最近 {shown} 条')
            .replace('{total}', shown).replace('{shown}', n)}</div>`
      : '';
    body.innerHTML = moreHint + runs.map(run => {
      const chains = (def.chains || (() => []))(run.files || [], run) || [];
      const chainBtns = chains.map((c, i) => c.dl
        ? `<a class="btn small" href="/tool_runs/${encodeURIComponent(run.name)}/${c.dl}" download>${esc(c.label)}</a> `
        : `<button class="btn small" onclick="rhGo(this, '${esc(run.name)}', '${esc(def.box)}', ${i})">${esc(c.label)}</button> `).join('');
      const open = RH_OPEN.has(run.name);
      // 关键产物：只展示主结果/报告/序列（primary/green），其余收进“全部文件”
      const keyed = (run.files || []).map(f => {
        const base = String(f.path).split('/').pop();
        const { label, color } = dlLabel(base, f.path);
        return { f, base, label, color };
      });
      const keys = keyed.filter(x => x.color === 'primary' || x.color === 'green');
      const rest = keyed.filter(x => x.color !== 'primary' && x.color !== 'green');
      const keyHtml = keys.length ? keys.map(x => {
        const cls = x.color ? ` dlbtn ${x.color}` : ' dlbtn';
        const low = x.base.toLowerCase();
        // 基因组图 (SVG)：直接内联预览
        if (low.endsWith('.svg')) {
          const normPath = String(x.f.path).replace(/\\/g, '/');
          const segs = normPath.split('/').map(encodeURIComponent).join('/');
          return `<div style="background:#fff;border-radius:8px;margin:6px 0;padding:8px"><img src="/tool_runs/${encodeURIComponent(run.name)}/${segs}" alt="${esc(x.base)}" style="display:block;width:100%;height:220px;object-fit:contain;border:1px solid var(--line-100);border-radius:8px;background:#fff" loading="lazy"><a class="${cls.trim()}" href="/tool_runs/${encodeURIComponent(run.name)}/${encodeURIComponent(normPath)}" title="${esc(x.f.path)}【${esc(x.f.size)}】">⬇ 下载 ${esc(x.label)}</a></div>`;
        }
        return `<a class="${cls.trim()}" href="/tool_runs/${encodeURIComponent(run.name)}/${encodeURIComponent(String(x.f.path).replace(/\\/g, '/'))}" title="${esc(x.f.path)}【${esc(x.f.size)}】">⬇ ${esc(x.label)}</a>`;
      }).join('') : '<p class="hint">（暂无关键产物）</p>';
      const restHtml = rest.length ? `<details class="rpt-sec" style="margin:8px 0 0"><summary>全部文件（${rest.length}）</summary><div class="rpt-sec-body" style="display:flex;flex-wrap:wrap;gap:4px">${rest.map(x => {
        return `<a class="dlbtn" href="/tool_runs/${encodeURIComponent(run.name)}/${esc(x.f.path)}" title="${esc(x.f.path)}【${esc(x.f.size)}】">⬇ ${esc(x.label)}</a>`;
      }).join('')}</div></details>` : '';
      const fileHtml = open ? `<div class="dlbtn-row">${keyHtml}</div>${restHtml}` : '';
      const rptBtn = (run.name.startsWith('identify_') || run.name.startsWith('contigs_'))
        ? `<button class="btn small" onclick="rhOpenReport('${esc(run.name)}')" title="查看该运行的分类报告（桑基图/分类表/明细）">📊 报告</button> `
        : '';
      return `<div style="border-bottom:1px dashed var(--line-100);padding:5px 0">
        <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">
          <b class="mono" style="font-size:12px;cursor:pointer" onclick="rhFiles('${esc(run.name)}')" title="点击展开/收起文件列表">${esc(run.name)}</b>
          <span class="hint">${(run.files || []).length} 文件</span>
          ${chainBtns}
          ${rptBtn}
          <button class="btn small" data-mp-url="/api/module_report?run=${encodeURIComponent(run.name)}"
                  data-mp-title="${esc(run.name)} · ${esc(t('c.modulePage', '模块结果页'))}"
                  onclick="previewModulePageFrom(this)"
                  title="${esc(t('c.modulePageTip', '打开该运行的模块结果页（图/表/文件）'))}">🧩 ${esc(t('c.modulePage', '模块结果页'))}</button>
          <button class="btn small primary" onclick="rhRestoreRun('${esc(def.box)}', '${esc(run.name)}')" title="${esc(t('ui.rh.restoreTip', '把这次运行的完整结果（图/表/文件）重新展示到上方输出区，同跑完时一样'))}">${t('ui.rh.restore', '↺ 还原')}</button>
          <button class="btn small" onclick="rhFiles('${esc(run.name)}')">📁 文件</button>
          <button class="btn small" data-dir="${esc(run.out_dir || '')}" onclick="rhOpenDir(this.dataset.dir)" title="在资源管理器中打开该运行目录（窗口置顶）">📂 打开</button>
          <button class="btn small" onclick="rhArchive(this, '${esc(run.name)}')" title="${esc(t('ui.rh.archiveTip', '移入归档区（不删数据，可恢复）'))}">${t('ui.rh.archive', '📦 归档')}</button>
          <button class="btn small danger" onclick="rhDel(this, '${esc(run.name)}')" title="删除该运行目录">🗑</button>
        </div>
        ${run.out_dir ? `<div class="hint mono" style="font-size:11px;margin:2px 0 0;word-break:break-all" title="结果输出位置">📍 ${esc(run.out_dir)}</div>` : ''}
        ${open ? (run.name.startsWith('contigs_') ? `<div id="rhcontig-${esc(run.name)}" style="margin-top:8px"><p class="hint">加载病毒序列分类明细…</p></div>` : '') + `<div style="margin-top:4px" class="dlbtn-row">${fileHtml}</div>` : ''}
      </div>`;
    }).join('');
  }
}

async function rhOpenDir(dir) {
  if (!dir) return;
  try {
    const r = await fetch('/api/open_dir', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: dir }) });
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      toast(t('c.openFail', '打开失败'), d.error || (r.status + ''), {kind: 'failed', ttl: 12000});
    }
  } catch (e) {
    toast(t('c.openFail', '打开失败'), String(e), {kind: 'failed', ttl: 12000});
  }
}

function rhToggle(boxId) {
  const body = $(boxId).querySelector('.rh-body');
  body.style.display = body.style.display === 'none' ? '' : 'none';
}
function rhFiles(name) {
  if (RH_OPEN.has(name)) RH_OPEN.delete(name); else RH_OPEN.add(name);
  rhRefresh();
  if (RH_OPEN.has(name) && name.startsWith('contigs_')) rhLoadContigDetail(name);
}

/* 历史条目「↺ 还原」（2026-11-05）：把该次运行的**完整结果**重新展示到
   上方输出区，同任务刚跑完时一样 —— 富结果卡（SDT / kvsuite / 共识基因组
   …）重放交互表格与图（复用自动恢复同一套 TOOL_RESTORE 通道），通用卡
   渲染「上次运行」横幅 + 产物下载区。与自动恢复的两点差别：不限「最近
   一次」（用户点名哪次就还原哪次），且无视「已有结果不覆盖」保护
   （还原就是要覆盖眼前的内容）。 */
async function rhRestoreRun(boxId, runName) {
  const nm = String(runName || '');
  // 之前可能被「📥 放入历史运行」收起过：还原时解除收起，别让下一轮轮询把它擦掉
  if (typeof outUnhideRun === 'function') outUnhideRun(nm);
  // 链 run 还原 → 级联解除（CHAIN_CASCADE，app-jobs.js）：链上各阶段卡
  // （verify / kvsuite / orfa / sdt）的 '#<前缀>' 收起标记一并清掉，
  // 并把阶段卡的 RH_USER_TOUCHED 解除 —— 否则还原了链主面板，那张
  // 分类表/定量表还压在历史收起集合里，刷新后阶段面板回不来。
  try {
    const _m = nm.match(/^(.+?)_\d{8}_\d{6}/);
    const _cas = (_m && typeof CHAIN_CASCADE !== 'undefined')
      ? CHAIN_CASCADE[_m[1]] : null;
    if (_cas && typeof CARD_RUN_PREFIX !== 'undefined') {
      (_cas.keys || []).forEach(k => {
        const pfx = CARD_RUN_PREFIX[k];
        const p = Array.isArray(pfx) ? pfx[0] : pfx;
        if (!p) return;
        const s = outHiddenSet();
        s.delete('#' + p);
        outSaveHidden(s);
        if (typeof RH_USER_TOUCHED !== 'undefined') RH_USER_TOUCHED.delete(k);
      });
    }
  } catch (e) {}
  // run 详情（files 清单）：RH_DATA（最近 8 条）没有就按前缀再拉全量
  let run = (RH_DATA || []).find(x => x.name === nm);
  if (!run) {
    const m = nm.match(/^(.+?)_\d{8}_\d{6}/);
    const pfx = m ? m[1] : nm;
    try {
      const list = await (await fetch(
        `/api/tool/runs?limit=100&prefix=${encodeURIComponent(pfx)}`)).json();
      run = (list || []).find(x => x.name === nm);
    } catch (e) { /* 拉不到就按最小 run 兜底 */ }
  }
  if (!run) run = { name: nm, out_dir: '', files: [] };
  // 输出卡 key：CARD_RUN_PREFIX（app-jobs.js 全局表）前缀匹配；
  // 数组前缀（crispr / crispr_cli）任一命中即可。
  let key = '';
  const table = (typeof CARD_RUN_PREFIX !== 'undefined') ? CARD_RUN_PREFIX : {};
  for (const k in table) {
    const prefs = Array.isArray(table[k]) ? table[k] : [table[k]];
    if (prefs.some(p => nm.startsWith(p))) { key = k; break; }
  }
  const box = key ? $('toolrun-' + key) : null;
  const rich = !!(key && typeof TOOL_RESTORE !== 'undefined' && TOOL_RESTORE[key]);
  // 钉住：还原的内容不被轮询里其他任务（更早的/失败的）覆盖，
  // 直到这张卡出现新任务（app-jobs injectStageLogs 检测新 id 自动解除）。
  try { if (key && typeof RH_PINNED !== 'undefined') RH_PINNED[key] = nm; } catch (e) {}
  if (rich) {
    try { await TOOL_RESTORE[key](nm); }
    catch (e) { /* 富重放失败走通用兜底 */ }
  }
  // 富卡输出区已被专用加载函数填过就不重复铺；通用卡/空箱铺横幅+下载区
  if (box && (!rich || !box.innerHTML.trim())) {
    box.innerHTML = runRestoreHtml(run);
  }
  const richEl = (rich && typeof RICH_BOX !== 'undefined' && RICH_BOX[key])
    ? document.getElementById(RICH_BOX[key]) : null;
  const anchor = box || richEl || document.getElementById(boxId);
  if (anchor) { try { anchor.scrollIntoView({ block: 'start' }); } catch (e) {} }
  if (key) RH_USER_TOUCHED.add(key);      // 自动恢复不再覆盖这张卡的画面
  toast(t('ui.rh.restored', '已还原到输出区'), nm, { ttl: 4000 });
}

/* 历史运行点开 → 展示该 run 的病毒序列分类（contig 明细）关键信息。
   读取 virus_classification.tsv，展示每条病毒 contig 的分类/长度/得分。 */
async function rhLoadContigDetail(run) {
  const box = document.getElementById('rhcontig-' + run);
  if (!box) {   // rhRefresh 异步渲染可能未完成，稍后重试
    setTimeout(() => rhLoadContigDetail(run), 300);
    return;
  }
  try {
    const r = await fetch(`/tool_runs/${encodeURIComponent(run)}/virus_classification.tsv`);
    if (!r.ok) { box.innerHTML = '<p class="hint">（无病毒分类明细）</p>'; return; }
    const text = await r.text();
    const lines = text.split(/\r?\n/).filter(Boolean);
    if (lines.length < 2) { box.innerHTML = '<p class="hint">（无病毒分类明细）</p>'; return; }
    const head = lines[0].split('\t');
    const idx = k => head.indexOf(k);
    const ic = idx('contig'), it = idx('taxon'), il = idx('length'), isc = idx('score'), ih = idx('host');
    const rows = lines.slice(1).map(l => l.split('\t'));
    const show = rows.slice(0, 12);
    const n = rows.length;
    const trs = show.map(row => {
      const c = ic >= 0 ? row[ic] : '';
      const t = it >= 0 ? row[it] : '';
      const l = il >= 0 ? row[il] : '';
      const s = isc >= 0 ? row[isc] : '';
      const h = ih >= 0 ? row[ih] : '';
      return `<tr><td class="mono" style="font-size:11px">${esc(c)}</td><td>${esc(t)}</td><td class="num">${esc(l)}</td><td class="num">${esc(s)}</td><td>${esc(h)}</td></tr>`;
    }).join('');
    box.innerHTML = `<div style="font-size:12.5px;font-weight:700;margin-bottom:6px;color:#1a5276">病毒序列分类（contig 明细 · ${n} 条）</div>
      <div style="max-height:260px;overflow:auto;border:1px solid #eee;border-radius:6px">
      <table class="tb"><thead><tr><th>Contig</th><th>分类</th><th class="num">长度</th><th class="num">得分</th><th>宿主</th></tr></thead>
      <tbody>${trs}</tbody></table></div>
      ${n > show.length ? `<p class="hint" style="margin-top:4px">共 ${n} 条，仅显示前 ${show.length} 条（点“📊 报告”看完整）</p>` : ''}`;
  } catch (e) { box.innerHTML = '<p class="hint">明细加载失败</p>'; }
}
/* ---------------- 手动清理 / 归档（2026-10-28） ----------------
   归档 = 把运行目录移进 tool_runs/_archive（列表接口不扫下划线目录），
   数据还在、随时能恢复；要真腾磁盘再在「归档区」里永久删除。 */
async function rhArchive(btn, name) {
  if (!confirm(t('ui.rh.archiveConfirm', '归档运行 {name}？文件移入归档区，可随时恢复。')
                   .replace('{name}', name))) return;
  try {
    const r = await fetch(`/api/tool/runs/${encodeURIComponent(name)}/archive`,
                          { method: 'POST' });
    if (!r.ok) { alert((await r.json().catch(() => ({}))).error
                       || t('ui.common.opFail', '操作失败')); return; }
    RH_OPEN.delete(name);
    toast(t('ui.rh.archivedOne', '已归档'), name, { ttl: 4000 });
    rhRefresh(); rhArchRefresh();
  } catch (e) { alert(t('ui.common.connShort') + ': ' + e); }
}

async function rhClean(boxId) {
  const def = RH_DEFS.find(d => d.box === boxId);
  const raw = prompt(t('ui.rh.cleanAsk', '每个模块保留最近几次运行？其余移入归档区（不删数据）。'), '3');
  if (raw === null) return;
  const keep = parseInt(String(raw).trim(), 10);
  if (isNaN(keep) || keep < 0) { alert(t('ui.rh.cleanBad', '请输入 0 或正整数')); return; }
  const prefixes = def ? def.prefixes.map(p => p.replace(/_+$/, '')) : [];
  try {
    const r = await fetch('/api/tool/runs/archive', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ keep: keep, prefixes: prefixes, confirm: 'archive' }) });
    if (!r.ok) { alert((await r.json().catch(() => ({}))).error
                       || t('ui.common.opFail', '操作失败')); return; }
    const d = await r.json();
    const n = (d.moved || []).length;
    toast(n ? t('ui.rh.cleanDone', '已归档 {n} 个运行（每模块保留最近 {k} 次）')
                .replace('{n}', n).replace('{k}', d.kept)
             : t('ui.rh.cleanNone', '没有需要归档的运行'),
          n ? '' : '', { ttl: 6000 });
    rhRefresh(); rhArchRefresh();
  } catch (e) { alert(t('ui.common.connShort') + ': ' + e); }
}

function rhArchToggle(boxId) {
  const box = $(boxId);
  if (!box) return;
  const panel = box.querySelector('.rh-arch');
  if (!panel) return;
  const show = panel.style.display === 'none';
  panel.style.display = show ? '' : 'none';
  if (show) rhArchRefresh();
}

async function rhArchRefresh() {
  const panels = [...document.querySelectorAll('.rh-arch')];
  if (!panels.length) return;
  let data = [];
  try { data = await (await fetch('/api/tool/runs/archived')).json(); }
  catch (e) { return; }
  const list = (data || []);
  const html = list.length
    ? `<div class="hint" style="margin:2px 0 4px">${esc(t('ui.rh.archHint', '归档只移动目录、不删数据；要释放磁盘请点永久删除'))}</div>`
      + list.map(x => `<div style="display:flex;gap:6px;align-items:center;padding:3px 0;border-bottom:1px dashed var(--line-100);flex-wrap:wrap">
          <span class="mono" style="font-size:12px">${esc(x.name)}</span>
          <span class="hint">${esc(x.size)} · ${x.files || 0} ${esc(t('ui.rh.filesUnit', '文件'))}</span>
          <button class="btn small" onclick="rhUnarchive('${esc(x.name)}')">${t('ui.rh.unarchive', '↩ 恢复')}</button>
          <button class="btn small danger" onclick="rhPurge('${esc(x.name)}')">${t('ui.rh.purgeOne', '永久删除')}</button>
        </div>`).join('')
      + `<div style="margin-top:6px"><button class="btn small danger" onclick="rhPurgeAll()">${t('ui.rh.purgeAll', '🗑 清空归档（不可恢复）')}</button></div>`
    : `<p class="hint">${esc(t('ui.rh.archEmpty', '（归档区为空）'))}</p>`;
  panels.forEach(p => { p.innerHTML = html; });
}

async function rhUnarchive(name) {
  try {
    const r = await fetch(`/api/tool/runs/${encodeURIComponent(name)}/unarchive`,
                          { method: 'POST' });
    if (!r.ok) { alert((await r.json().catch(() => ({}))).error
                       || t('ui.common.opFail', '操作失败')); return; }
    toast(t('ui.rh.unarchiveDone', '已恢复到运行列表'), name, { ttl: 4000 });
    rhRefresh(); rhArchRefresh();
  } catch (e) { alert(t('ui.common.connShort') + ': ' + e); }
}

async function rhPurge(name) {
  if (!confirm(t('ui.rh.purgeConfirm', '永久删除归档 {name}？不可恢复。')
                   .replace('{name}', name))) return;
  try {
    const r = await fetch('/api/tool/runs/archived/purge', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ names: [name], confirm: 'purge' }) });
    if (!r.ok) { alert(t('ui.common.opFail', '操作失败')); return; }
    const d = await r.json();
    toast(t('ui.rh.purgeDone', '已永久删除 {n} 项').replace('{n}', (d.removed || []).length),
          name, { ttl: 4000 });
    rhArchRefresh();
  } catch (e) { alert(t('ui.common.connShort') + ': ' + e); }
}

async function rhPurgeAll() {
  if (!confirm(t('ui.rh.purgeAllConfirm', '永久删除归档区全部内容？不可恢复。'))) return;
  try {
    const r = await fetch('/api/tool/runs/archived/purge',
                          { method: 'POST', headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ confirm: 'purge' }) });
    if (!r.ok) { alert(t('ui.common.opFail', '操作失败')); return; }
    const d = await r.json();
    toast(t('ui.rh.purgeDone', '已永久删除 {n} 项').replace('{n}', (d.removed || []).length),
          '', { ttl: 4000 });
    rhArchRefresh();
  } catch (e) { alert(t('ui.common.connShort') + ': ' + e); }
}

async function rhDel(btn, name) {
  if (!confirm(t('ui.rh.delConfirm').replace('{name}', name))) return;
  try {
    const r = await fetch(`/api/tool/runs/${encodeURIComponent(name)}/delete`,
                          { method: 'POST' });
    if (!r.ok) { alert((await r.json()).error || t('ui.common.delFail')); return; }
    RH_OPEN.delete(name);
    rhRefresh();
  } catch (e) { alert(t('ui.common.connShort') + ': ' + e); }
}
function rhGo(btn, runName, boxId, idx) {
  const def = RH_DEFS.find(d => d.box === boxId);
  const run = (RH_DATA || []).find(x => x.name === runName);
  if (!def || !run) return;
  const chain = (def.chains(run.files || [], run) || [])[idx];
  if (!chain) return;
  // 暂存全部回填值 → 跳目标页 → rhApply 落位（同页目标立即填充）
  let fills = Object.assign({}, chain.fills);
  if (chain.page) {
    const samePage = !chain.page.startsWith('/') ||
      location.pathname === chain.page.split('#')[0];
    const missing = Object.keys(fills).filter(k => !$(k));
    if (samePage && !missing.length) {
      rhApplyFills(fills);
      toast('已回填下游输入', Object.keys(fills).join(', '), { ttl: 3000 });
      if (chain.page.includes('#')) location.hash = chain.page.split('#')[1];
      return;
    }
    try { sessionStorage.setItem('vp_runfill', JSON.stringify(fills)); } catch (e) {}
    gotoModule(chain.page);
  } else {
    rhApplyFills(fills);
  }
}
function rhApplyFills(fills) {
  for (const [id, val] of Object.entries(fills)) {
    const el = $(id);
    if (!el) continue;
    if (el.type === 'checkbox') el.checked = !!val;
    else if (el.tagName === 'SELECT') {
      if ([...el.options].some(o => o.value === val)) el.value = val;
    } else el.value = val;
  }
}
function rhApplyStashed() {
  let fills = null;
  try { fills = JSON.parse(sessionStorage.getItem('vp_runfill') || 'null'); } catch (e) {}
  if (!fills) return;
  const applied = [];
  for (const id of Object.keys(fills)) {
    if ($(id)) { rhApplyFills({ [id]: fills[id] }); applied.push(id); delete fills[id]; }
  }
  if (applied.length) {
    if (Object.keys(fills).length)
      try { sessionStorage.setItem('vp_runfill', JSON.stringify(fills)); } catch (e) {}
    else sessionStorage.removeItem('vp_runfill');
    toast(t('ui.rh.backfill'), applied.join(', '), { ttl: 4000 });
  }
}
/* 富面板 ✕ 统一打标：✕ 的语义是「收起来」，自动恢复（页面加载/切模块时
   的 restoreLastResults）不得把用户收起的面板再拉回来。各面板 ✕ 都是
   内联 onclick（$id.style.display='none'），逐个改模板不划算——这里在
   **捕获阶段**统一监听：点击发生在 RICH_BOX 容器内、按钮文本是关闭符
   → 记入 RH_USER_TOUCHED，restoreLastResults 见标记就跳过该卡。 */
document.addEventListener('click', ev => {
  const btn = ev.target.closest && ev.target.closest('button');
  if (!btn) return;
  const tt = (btn.textContent || '').trim();
  if (tt !== '✕' && tt !== '×' && tt !== '✖') return;
  const holder = btn.closest('[id]');
  if (!holder) return;
  for (const k in RICH_BOX) {
    if (RICH_BOX[k] === holder.id) {
      RH_USER_TOUCHED.add(k);          // 自动恢复跳过这张卡
      break;
    }
  }
}, true);                              // capture：先于面板的内联 onclick

/* 任务完成钩子：任务从非终态跨进终态（running/queued/paused →
   done/failed/cancelled）时刷新历史区。2026-11-05 二次修复：原实现只记
   任务 id 集合，任务 running 时 id 已进「已见」集合，翻成 done 时判不出
   「新见终态」——页面开着等一个任务跑完，历史区永远不自动刷新（计数与
   条目停在旧值，API 列表明明是新的），只有归档/恢复/删除或手动刷新页面
   才更新；首见即终态的快任务（整个生命周期落在两次轮询之间）也漏。
   改为记 id→状态：终态且「此前非终态或首见」即算刚结束；页面加载后的
   第一次调用只播种（列表里全是早已完成的历史任务，不能全算刚结束）。 */
const RH_TERM = new Set(['done', 'failed', 'cancelled']);
const RH_STATUS = new Map();
let RH_SEEDED = false;
window.__rhOnTasks = tasks => {
  let justFinished = false;
  const next = new Map();
  for (const t of (tasks || [])) {
    const prev = RH_STATUS.get(t.id);
    if (RH_SEEDED && RH_TERM.has(t.status)
        && (prev === undefined || !RH_TERM.has(prev))) justFinished = true;
    next.set(t.id, t.status);
  }
  RH_STATUS.clear();
  for (const [k, v] of next) RH_STATUS.set(k, v);
  RH_SEEDED = true;
  if (justFinished) rhRefresh();
};
function rhInitContainers() {
  for (const def of RH_DEFS) {
    let box = $(def.box);
    // 自动注入：页面没预留 <div class="rh"> 容器时，把历史区插到各卡
    // 输出区（def.after = 'toolrun-<key>'）后面 —— 补登记的 19 个模块
    // 靠这一处覆盖全部页面，模板不再需要挨个加容器。
    if (!box && def.after) {
      const anchor = $(def.after);
      if (anchor) {
        anchor.insertAdjacentHTML('afterend',
          `<div class="rh" id="${def.box}"></div>`);
        box = $(def.box);
      }
    }
    if (!box || box.dataset.init) continue;
    box.dataset.init = '1';
    box.innerHTML = `<div class="rh-head" style="display:flex;gap:8px;align-items:center;margin-top:8px;flex-wrap:wrap">
        <button class="btn small" onclick="rhToggle('${def.box}')">${t('ui.rh.btnPre')}<span class="rh-count">…</span>${t('ui.rh.btnPost')}</button>
        <span class="hint">${t('ui.rh.hint')}</span>
        <button class="btn small" onclick="rhClean('${def.box}')" title="${esc(t('ui.rh.cleanTip', '每个模块保留最近几次，其余归档'))}">${t('ui.rh.clean', '🧹 清理…')}</button>
        <button class="btn small" onclick="rhArchToggle('${def.box}')" title="${esc(t('ui.rh.archTip', '归档区：可恢复；永久删除才释放磁盘'))}">${t('ui.rh.archBtn', '📦 归档区')}</button>
      </div>
      <div class="rh-body" style="display:none;margin-top:4px"></div>
      <div class="rh-arch" style="display:none;margin-top:6px"></div>`;
  }
}
/* ?run=<名称> 深链接（2026-11-02）：历史归档 / 任务卡「在页面中打开」跳到工具箱页时，
   直接把这套运行的最近结果展开、滚到眼前；找不到就说清楚，而不是停在页面顶部。 */
async function rhDeepLink() {
  const want = new URLSearchParams(location.search).get('run');
  if (!want) return false;
  // 只有真正带历史运行盒的页面才处理（结果中心也引了本文件，但那边用 rsFocusRun）
  if (!document.querySelector('.rh')) return false;
  if (!RH_DATA) await rhRefresh();
  const hit = (RH_DATA || []).find(x => x.name === want);
  const def = RH_DEFS.find(d => d.prefixes.some(p => want.startsWith(p)));
  const box = def ? $(def.box) : null;
  if (!hit || !box) {
    toast(t('ui.rh.deepMiss', '这次运行不在本页的历史里'), want, { kind: 'failed', ttl: 8000 });
    return false;
  }
  RH_OPEN.add(want);
  const body = box.querySelector('.rh-body');
  if (body) body.style.display = '';
  rhRefresh();
  if (want.startsWith('contigs_')) rhLoadContigDetail(want);
  try { box.scrollIntoView({ block: 'center' }); } catch (e) {}
  toast(t('ui.rh.deepHit', '已跳到这次运行'), want, { ttl: 4000 });
  return true;
}

document.addEventListener('DOMContentLoaded', () => {
  rhInitContainers();
  rhRefresh();
  setTimeout(rhApplyStashed, 350);   // 等各页自身 init（下拉等）先就绪
  setTimeout(() => { try { rhDeepLink(); } catch (e) {} }, 450);
});

