/* ============================================================
 * 内置示例数据 · 统一入口（webapp/static/examples.js）
 * ------------------------------------------------------------
 * 目的：任何模块都不需要用户先准备数据 —— 点「✨ 示例」即填好该模块
 *       所需输入；页面首次打开且输入为空时也会自动填入（可在设置关闭）。
 *
 * 数据文件全部在 examples/（FASTA/GB/NWK 等由 tests/make_examples.py 生成，
 * 确定性、可重跑；reads 见下方 SIM 注释）：
 *   example_viral_contigs.fasta 2 条病毒 contig（含 Pepper yellows virus）
 *   example_virus_set.fasta     6 条同属近缘基因组（比对/建树/SDT）
 *   example_conserved_set.fasta 5 条同种近缘序列（保守区引物）
 *   example_genome.gb           带 CDS 注释的 GenBank（基因组图谱）
 *   example_tree.nwk            示例树
 *   example_synteny_A/B/C.gb    同属三基因组（GenBank 集合导入示例）
 *   example_tmv/pvy/cmv/pstvd/mix.fasta  Metabuli 示例病毒
 *   example_rdp.fasta(+meta)   RDP 重组检测示例：5 条模拟分离物比对
 *                              （examples/results/rdp 生成时用的同一输入）
 *   example_hostfree.fasta     病毒识别一键链（virchain）示例输入：sim100x
 *                              病毒序列提取产物降采样（2 万条 FASTA）
 *   example_sim100x_R1/R2.fastq.gz  内置示例双端 reads：多病毒混染模拟
 *                              （服务器 snap/simulator-data eval3_depth100，
 *                              固定 100×，96 条植物病毒参考基因组，
 *                              166,436 对 / 150 bp；reads 头=来源登记号-
 *                              坐标/1|2，自带 ground truth）。
 *                              2026-11-03 起替代旧合成对 example_R1/R2
 *                              （3 种病毒 ~60x，已退役删除；其生成脚本
 *                              tests/make_example_reads.py 保留备查）。
 *
 * 新增模块只改下面的 MAP / PAGE_MAP 两个表，不要在页面里散写示例路径。
 * ============================================================ */
(function () {
  'use strict';

  var DIR = 'examples/';
  var F = {
    /* 内置示例双端 reads：多病毒混染模拟（服务器 snap/simulator-data
       eval3_depth100，固定 100×）。2026-11-03 起唯一示例 —— 分析卡 ✨ 与
       /samples 接入页共用；配套「👁 示例结果」已用同一数据重新生成。 */
    R1: DIR + 'example_sim100x_R1.fastq.gz',
    R2: DIR + 'example_sim100x_R2.fastq.gz',
    SIM_R1: DIR + 'example_sim100x_R1.fastq.gz',
    SIM_R2: DIR + 'example_sim100x_R2.fastq.gz',
    /* 病毒识别一键链（virchain）专用：sim100x 经 kunpeng 病毒序列提取后的
       降采样 FASTA（2 万条，3.4MB）——该链 2026-11-02 起只收去宿主/病毒
       序列 FASTA，不再吃原始 FASTQ */
    HOSTFREE: DIR + 'example_hostfree.fasta',
    CONTIGS: DIR + 'example_viral_contigs.fasta',
    CONTIG1: DIR + 'example_contig_1.fasta',
    SET: DIR + 'example_virus_set.fasta',
    CONSERVED: DIR + 'example_conserved_set.fasta',
    GENOME: DIR + 'example_genome.gb',
    TMV: DIR + 'example_tmv.fasta',
    CMV: DIR + 'example_cmv.fasta',
    MIX: DIR + 'example_mix.fasta',
    TREE: DIR + 'example_tree.nwk',
    PHYLOGEO: DIR + 'example_phylogeo.fasta',
    PHYLOGEO_META: DIR + 'example_phylogeo.meta.csv',
    PDPREP_FASTA: DIR + 'example_pdprep.fasta',
    // 数据接入治理卡专用 meta（name/date/location 三列；name 与
    // phylogeo.fasta 头逐字一致——seq_id/region/year 旧口径治理不认）
    PDPREP_META: DIR + 'example_pdprep.meta.csv',
    ACMV: DIR + 'example_acmv.fasta',
    RENAME_MAP: DIR + 'example_rename_map.tsv',
    MIRNA: DIR + 'example_mirna.fasta',
    /* RDP 重组检测示例输入（与 examples/results/rdp 的生成输入同源：
       5 条模拟分离物比对 + meta；见 tests/make_examples.py） */
    RDP: DIR + 'example_rdp.fasta'
  };

  /* 工具卡（tools.html 里 <section class="card" id="t-xxx">）→ 字段映射。
     值写成 F 的键（示例文件）或字面量；select 会按 option value 匹配。 */
  var MAP = {
    't-convert':   { cv_input: 'R1', cv_target: 'fasta' },
    't-fastp':     { f_r1: 'R1', f_r2: 'R2' },
    't-identify':  { i_type: 'pe', i_input: 'R1', i_input2: 'R2' },
    't-assemble':  { a_r1: 'R1', a_r2: 'R2' },
    't-contigs':   { c_fa: 'CONTIGS' },
    't-verify':    { vf_fa: 'CONTIGS', homSeqText: 'CONTIGS_TEXT' },
    't-virchain':  { vc_input: 'HOSTFREE' },
    /* 2026-11-03：t-hom 卡并入 t-verify（homSeqText 映射随迁，深链不再指向 t-hom） */
    't-consensus': { cs_fa: 'CONTIGS', cs_reads: 'R1' },
    't-variant':   { vr_ref: 'CONTIGS' },
    't-dsrna':     { ds_input: 'CMV' },
    /* 进化动力学 5 张数据准备/下游卡 + miRNA：之前没有任何示例入口 */
    't-rtt':       { rtt_input: 'PHYLOGEO', rtt_meta: 'PHYLOGEO_META' },
    't-phylogeo':  { pg_input: 'PHYLOGEO', pg_meta: 'PHYLOGEO_META' },
    't-pdprep':    { pd_src: 'files', pd_fa: 'PHYLOGEO', pd_meta: 'PDPREP_META' },
    't-mirna':     { mirna_fasta: 'MIRNA', virus_fasta: 'ACMV' },
    /* 2026-11-05 补齐未接示例的工具卡：annochain 吃 contigs FASTA；
       t-crispr 用 ACMV 单序列目标；rdp 用现成的 example_rdp.fasta（其
       示例结果就是用这份输入生成的）；loganfetch / cmpchain 需联网拉
       参考，只填字面量 */
    't-annochain': { ano_input: 'CONTIGS' },
    't-crispr':    { cr_input: 'ACMV' },
    't-lamp':      { lp_input: 'ACMV' },
    't-rdp':       { rdp_input: 'RDP' },
    't-loganfetch': { lf_accs: 'SRR24759446, ERR3382580' },
    't-cmpchain':  { cmpMode: 'ictv', cmpRank: 'Genus', cmpTaxon: 'Potyvirus',
                     cmpLimit: '12', cmpSdtMax: '12' },
    't-seqprep':   null,      /* 已有自带示例按钮，保持原样 */
    't-align':     null,
    't-treebuild': null,
    't-sdt':       null,
    't-kvsuite':   null       /* 用样品名而非文件，见 fillKvsuiteExample */
  };

  /* 独立页面（按 location.pathname 匹配）→ 字段映射 */
  var PAGE_MAP = {
    '/hostremoval': { hr_r1: 'R1', hr_r2: 'R2' },
    '/orf':         { of_fa: 'CONTIGS' },
    '/annotation':  { oa_fa_text: 'CONTIGS_TEXT' },
    '/genome':      { gp_ann: 'GENOME' },
    '/primer':      { pd_fa: 'CONTIGS' },  /* primer3 页（PCR 引物设计）示例=2 条病毒 contig，与页内自带 ✨ 一致 */
    '/logan':       { pasteSeq: 'TMV_TEXT' },  /* 文本域：填序列内容 */
    /* 样品接入示例用模拟多病毒数据（sim100x，166,436 对，固定 100×）；该页
       无捆绑示例结果，换数据不会造成输入↔结果错位。reads 由已知病毒参考
       模拟而来，kvsuite/组装/变异演示保证有信号。注册后即可在
       kvsuite/kvchain 等按样品名运行的模块里直接用 sim100x。 */
    '/samples':     { sample: 'sim100x', r1: 'SIM_R1', r2: 'SIM_R2' },
    /* 该框是「物种拉丁名」= 检索**宿主/物种**的测序数据，不是病毒名：
       填病毒名（曾写 Tobacco mosaic virus）会让人以为在按病毒检索，且与
       placeholder「如 Lycium barbarum」和平台示例宿主（host-db/112863_
       Lycium barbarum、提交示例的 Host 字段）自相矛盾。统一用枸杞。 */
    '/meta':        { mtSpecies: 'Lycium barbarum', mtSource: 'All' },
    /* 预筛 / 批量下载：示例 Run 列表与预筛页 placeholder 同源（SRA/ENA/GSA
       各一），逐行一个编号；只填输入，不自动发起任何下载或检索 */
    '/prescreen':   { psRuns: 'SRR23843744\nERR7586041\nCRR125671' },
    '/download':    { dlItems: 'SRR23843744\nERR7586041\nCRR125671' }
  };

  var AUTO_KEY = 'vp_example_autofill';
  var _tmvText = null;

  /* 文本域示例：字段值写成 '<KEY>_TEXT' → 取该示例文件的内容填进 textarea。
     BLASTN·BLASTX 卡走「粘贴序列」入口，需要的是序列正文而非路径。
     （原 CDD 卡同款入口已随 CDD 下线移除。） */
  var TEXT_SRC = { CONTIGS_TEXT: 'CONTIG1', TMV_TEXT: 'TMV' };

  function $(id) { return document.getElementById(id); }

  function setField(id, raw) {
    var el = $(id);
    if (!el) return false;
    var val = (raw in F) ? F[raw] : raw;
    if (val === 'TMV_TEXT') return false;          /* 由专用逻辑处理 */
    if (el.tagName === 'SELECT') {
      var ok = false;
      for (var i = 0; i < el.options.length; i++) {
        if (el.options[i].value === String(val)) { el.value = String(val); ok = true; break; }
      }
      if (!ok) return false;
    } else {
      el.value = val;
    }
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
    return true;
  }

  function fillMap(map) {
    var n = 0;
    for (var id in map) {
      var v = map[id];
      if (typeof v === 'string' && TEXT_SRC[v]) {
        if (fillTextFromFile(id, TEXT_SRC[v])) n++;
        continue;
      }
      if (setField(id, v)) n++;
    }
    return n;
  }

  /* 取示例文件正文填入 textarea（FASTA 全文，含 >header）。
     databases/ 不经 HTTP 暴露 → 走只读路由 /api/example_input/<文件名>。 */
  function fillTextFromFile(id, fileKey) {
    var ta = $(id);
    if (!ta) return false;
    var rel = (fileKey in F) ? F[fileKey] : fileKey;
    var path = '/api/example_input/' + encodeURIComponent(
      rel.indexOf(DIR) === 0 ? rel.slice(DIR.length) : rel.split('/').pop());
    fetch(path).then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.text();
    }).then(function (t) {
      ta.value = t;
      ta.dispatchEvent(new Event('input', { bubbles: true }));
      ta.dispatchEvent(new Event('change', { bubbles: true }));
    }).catch(function (e) {
      if (typeof toast === 'function') toast('示例加载失败: ' + e.message, '', { ttl: 3000 });
    });
    return true;
  }

  /* kvsuite / kvchain 示例：之前把样品名填成 EXAMPLE，但那个样品从来没人
     创建过——打包生成示例结果的脚本（tests/make_example_results.py）也只是
     拿临时样品表指向内置 reads，明确「不建真样品」；用户清掉 run/results
     或换测试数据后点启动，就报「路径不存在: run\results\EXAMPLE」。
     改为填两张卡各自的「直接输入测序数据」R1/R2（后端该入口不必先建样品，
     _tool_job_kvsuite 的 reads 分支会自行配对），并清空样品框，避免触发
     「样品与直接输入测序数据只能二选一」；两卡字段都在时都填，便于来回对照。 */
  function fillKvsuiteExample() {
    var n = 0;
    ['kv_samples', 'kvc_samples'].forEach(function (id) {
      var el = $(id);
      if (!el || !el.value) return;
      el.value = '';
      el.dispatchEvent(new Event('input', { bubbles: true }));
      el.dispatchEvent(new Event('change', { bubbles: true }));
    });
    [['kv_reads_r1', F.R1], ['kv_reads_r2', F.R2],
     ['kvc_reads_r1', F.R1], ['kvc_reads_r2', F.R2]].forEach(function (kv) {
      var el = $(kv[0]);
      if (!el) return;
      n++;
      var put = function (v) {
        el.value = v;
        el.dispatchEvent(new Event('input', { bubbles: true }));
        el.dispatchEvent(new Event('change', { bubbles: true }));
      };
      /* 绝对路径来自 /api/example_paths（app.js 的 _exampleAbs），后端 reads
         分支直接 os.path.isfile 校验原始 token，相对路径会受服务端 CWD 影响 */
      if (typeof _exampleAbs === 'function') {
        _exampleAbs(kv[1]).then(put);
      } else {
        put(kv[1]);
      }
    });
    return n;
  }

  /* 取 example_tmv.fasta 的序列内容（logan 粘贴框用） */
  function fillLoganExample() {
    var ta = $('pasteSeq');
    if (!ta) return 0;
    if (_tmvText) {
      ta.value = _tmvText;
      ta.dispatchEvent(new Event('input', { bubbles: true }));
      return 1;
    }
    fetch('/api/example_input/' + encodeURIComponent(F.TMV.slice(DIR.length)))
      .then(function (r) { return r.text(); }).then(function (t) {
      _tmvText = t;
      ta.value = t;
      ta.dispatchEvent(new Event('input', { bubbles: true }));
    }).catch(function () {});
    return 1;
  }

  function applyFor(el) {
    /* el = 卡片 section 或 页面标识 */
    if (el === 'page') {
      var pm = PAGE_MAP[location.pathname];
      if (!pm) return 0;
      if (pm.pasteSeq === 'TMV_TEXT') return fillLoganExample();
      return fillMap(pm);
    }
    var id = el.id;
    if (id === 't-kvsuite' || id === 't-kvchain') return fillKvsuiteExample();
    var m = MAP[id];
    return m ? fillMap(m) : 0;
  }

  /* ---------- 「✨ 示例」/「👁 示例结果」按钮注入 ----------
     2026-11-04 用户要求：示例结果不再自动弹出（原「首次访问自动摊开」
     已删），一律手动点按钮；且按钮统一放在**输出区之后**，不再挤在标题旁。
     ✨ 示例（填输入）按钮仍留在标题旁——它属于输入区。 */
  function mkBtn(label, title, handler) {
    var b = document.createElement('button');
    b.type = 'button';
    b.className = 'btn small vp-ex-btn';
    b.style.marginLeft = '8px';
    b.textContent = label;
    b.title = title;
    b.addEventListener('click', function (ev) { ev.preventDefault(); handler(); });
    return b;
  }

  /* 独立页（/orf /primer …）的输出锚点：示例结果按钮条插在它后面 */
  var PAGE_OUT_ANCHOR = {
    '/orf': '#toolrun-orf', '/annotation': '#toolrun-orfa',
    '/genome': '#toolrun-genoplot', '/primer': '#pd_out',
    '/hostremoval': '#toolrun-hostremoval', '/logan': '#fragCard'
  };
  /* 一张卡承载多个模块时，主示例外再补的按钮（t-sdt 卡同时有 NT+AA
     同一性面板，同一性示例结果也该能从这里看） */
  var RESULT_BTN_EXTRA = {
    't-sdt': [{ module: 'identity', labelKey: 'ui.ex.btnResultIdentity' }]
  };

  function injectResultBar(anchor, entries) {
    if (!anchor || !entries || !entries.length) return false;
    var bar = document.createElement('div');
    bar.className = 'vp-ex-resultbar';
    bar.style.cssText = 'display:flex;gap:8px;align-items:center;flex-wrap:wrap;' +
      'margin:8px 0 2px;padding:6px 10px;border:1px dashed var(--line-200,#dcdcdc);' +
      'border-radius:8px;background:var(--surface-2,#fafafa)';
    var tip = t('ui.ex.btnResultTip', '查看内置示例的运行结果（只读）');
    entries.forEach(function (e) {
      var label = e.label || (e.labelKey ? t(e.labelKey, '👁 示例') : null) ||
                  t('ui.ex.btnResult', '👁 示例结果');
      var btn = mkBtn(label, tip,
        function () { showExampleResult(e.module); });
      btn.style.marginLeft = '0';
      bar.appendChild(btn);
    });
    var hint = document.createElement('span');
    hint.className = 'hint';
    hint.textContent = t('ui.ex.barHint', '示例结果不会自动加载，点按钮手动查看');
    bar.appendChild(hint);
    anchor.insertAdjacentElement('afterend', bar);
    return true;
  }

  function injectButtons() {
    document.querySelectorAll('section.card[id^="t-"]').forEach(function (sec) {
      var hasFill = !!MAP[sec.id] || sec.id === 't-kvsuite' ||
                    sec.id === 't-kvchain';
      var mod = resultModule(sec.id);
      if (hasFill) {
        var h2 = sec.querySelector('h2');
        if (h2 && !h2.querySelector('.vp-ex-btn')) {
          h2.appendChild(mkBtn(t('ui.ex.btnFill'), t('ui.ex.btnFillTip'),
            function () {
              var n = applyFor(sec);
              if (typeof toast === 'function') {
                toast(n ? t('ui.ex.fillOk', '已填入示例数据') : t('ui.ex.noCfg', '该模块暂未配置示例'), '', { ttl: 2200 });
              }
            }));
        }
      }
      /* 👁 示例结果：输出区（卡内最后一个 .toolrun）之后 */
      if (mod && !sec.querySelector('.vp-ex-resultbar')) {
        var outs = sec.querySelectorAll('.toolrun');
        var anchor = outs.length ? outs[outs.length - 1] : null;
        var entries = [{ module: mod }]
          .concat(RESULT_BTN_EXTRA[sec.id] || []);
        injectResultBar(anchor, entries);
      }
    });
    /* 独立页面：✨ 示例（填输入）仍挂页面主标题旁（输入区的入口）。
       独立页标题可能是 h1（如 /orf /hostremoval 改版后用 h1），故
       h1/h2 都要认；只看标题里有没有按钮，避免页面自带示例按钮时
       全局 .vp-ex-btn 判断把注入整体跳过。 */
    var pm = PAGE_MAP[location.pathname];
    if (pm) {
      var h = document.querySelector('main h1, main h2, .card h2');
      if (h && !h.querySelector('.vp-ex-btn')) {
        h.appendChild(mkBtn(t('ui.ex.btnFill', '✨ 示例'), t('ui.ex.btnFillTip', '一键填入内置示例数据'), function () {
          var n = applyFor('page');
          if (typeof toast === 'function') {
            toast(n ? t('ui.ex.fillOk', '已填入示例数据') : t('ui.ex.noCfg', '该模块暂未配置示例'), '', { ttl: 2200 });
          }
        }));
      }
    }
    /* 独立页面：示例结果按钮条放该页输出容器之后。
       页面主体可能由页内 JS 异步渲染（/logan /primer /orf 实测），锚点
       晚出现 → boot 里的定时补注入会反复进来，直到按钮条就位。 */
    var pmod = resultModule(location.pathname);
    var psel = PAGE_OUT_ANCHOR[location.pathname];
    if (pmod && psel && !document.querySelector('main .vp-ex-resultbar')) {
      var entries = [{ module: pmod }]
        .concat(RESULT_BTN_EXTRA[location.pathname] || []);
      injectResultBar(document.querySelector(psel), entries);
    }
  }

  /* ---------- 首次打开自动填充输入（示例结果一律手动，不再自动展示） ---------- */
  function autoFill(tries) {
    /* 上游页面带参数跳进来时（如「病毒鉴定」→ /primer 带 contig 序列），
       页内脚本会把 window.__vpNoAutoFill 置 true：示例填充必须让路，否则
       它 400ms 后的一次性写入会把刚带进来的输入覆盖成示例文件路径。 */
    if (window.__vpNoAutoFill) return;
    var off = false;
    try { off = localStorage.getItem(AUTO_KEY) === '0'; } catch (e) {}
    if (off) return;
    /* 独立页面优先走 PAGE_MAP：有的独立页里也藏着 section.card[id^="t-"]（比如
       class 控制显隐的残留卡），`:not([style*=...])` 过滤不掉它们，先匹配会
       抢走填充且 MAP 无对应项 → 什么都不填（/hostremoval 之前就是这样）。 */
    var pm = PAGE_MAP[location.pathname];
    var sec = pm ? null
                 : document.querySelector('section.card[id^="t-"]:not([style*="display: none"])');
    if (sec) {
      applyFor(sec);
    } else if (pm) {
      var n = applyFor('page');
      /* /logan /primer 等页主体由页内 JS 异步渲染，字段可能晚于本次执行出现；
         一次没填上（n=0）就按轮次重试，与工具卡的等待逻辑对齐。 */
      if (!n && (tries || 0) < 8) {
        setTimeout(function () { autoFill((tries || 0) + 1); }, 400);
        return;
      }
    } else if ((tries || 0) < 8) {
      /* /tools 的模块卡由 renderModuleTree() 按 ?g=#hash 异步渲染，
         可能晚于本函数首次执行；重试几轮等卡片出现再填。 */
      setTimeout(function () { autoFill((tries || 0) + 1); }, 400);
      return;
    }
    /* 示例结果不自动弹出（2026-11-04 用户要求）：想看就点输出区后的
       「👁 示例结果」按钮，加载动作完全由用户触发。 */
  }

  function boot() {
    injectButtons();
    setTimeout(autoFill, 400);
    /* 独立页主体可能由页内 JS 异步渲染（/logan /primer 实测）：
       标题与输出容器都晚出现 → 首轮 injectButtons 挂不上按钮条。
       定时补注入，直到 ✨ 按钮与示例结果按钮条都就位（或超时放弃）。 */
    if (PAGE_MAP[location.pathname]) {
      var tries = 0;
      var iv = setInterval(function () {
        tries++;
        var h = document.querySelector('main h1, main h2, .card h2');
        var fillOk = !PAGE_MAP[location.pathname] ||
          (h && h.querySelector('.vp-ex-btn'));
        var barOk = !!document.querySelector('main .vp-ex-resultbar');
        var done = fillOk && barOk;
        if (!done) injectButtons();
        if (done || tries > 12) clearInterval(iv);
      }, 400);
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }

  /* 供其它脚本/调试使用 */
  window.VPExamples = {
    files: F, map: MAP, pageMap: PAGE_MAP,
    apply: applyFor, inject: injectButtons, autofillKey: AUTO_KEY,
    showResult: showExampleResult,
    /* 交互热图（供 live 运行结果复用）：colorscales=可选配色，heatmap=渲染函数 */
    colorscales: COLORSCALES, heatmap: renderHeatmap
  };

  /* ============================================================
   * 示例结果查看器（阶段 C）
   * 读 /api/examples/<模块>（只读 examples/results/，与真实结果隔离）
   * 图片内联、TSV/CSV 转表格、JSON 格式化、其余给下载链接。
   * ============================================================ */
  var RESULT_MAP = {
    't-convert': 'convert', 't-fastp': 'fastp', 't-identify': 'identify',
    't-assemble': 'assemble', 't-contigs': 'contigs', 't-verify': 'verify',
    't-align': 'align', 't-treebuild': 'quicktree', 't-sdt': 'sdt',
    't-virchain': 'virchain', 't-kvchain': 'kvchain',
    't-kvsuite': 'kvsuite', 't-consensus': 'consensus', 't-variant': 'variant',
    '/orf': 'orf', '/annotation': 'orfa', '/genome': 'genoplot',
    '/primer': 'primer', '/hostremoval': 'hostremoval',
    /* 2026-11-03：旧 hom 卡键已删（并入 t-verify）；hom 示例产物
       examples/results/hom/ 仍在 /api/examples/hom 可读 */
    '/logan': 'logan',
    't-dsrna': 'dsrna', 't-mirna': 'mirna',
    't-rdp': 'rdp', 't-rtt': 'rtt', 't-phylogeo': 'phylogeo',
    't-pdprep': 'pdprep',
    't-phylodyn': 'phylodyn', 't-seqprep': 'seqprep',
    /* 2026-11-05 补：t-crispr + 注释一键链（示例结果见 examples/results/，
       由 tests/make_example_results.py 经 /api/tool/run 真跑固化；loganfetch /
       cmpchain 需联网拉参考，无离线示例结果，故不在此表） */
    't-crispr': 'crispr',
    't-lamp': 'lamp',
    't-annochain': 'annochain'
  };

  function resultModule(idOrPath) {
    return RESULT_MAP[idOrPath] || null;
  }

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }

  function tableHtml(text, maxRows) {
    var lines = text.replace(/\r/g, '').split('\n').filter(function (x) {
      return x.trim();
    });
    if (!lines.length) return '<p class="hint">（空文件）</p>';
    var sep = lines[0].indexOf('\t') >= 0 ? '\t' : ',';
    var rows = lines.slice(0, maxRows || 50).map(function (ln) {
      return ln.split(sep);
    });
    var h = '<div class="scroll-lg"><table class="tbl" style="font-size:11.5px">';
    h += '<tr>' + rows[0].map(function (c) {
      return '<th>' + esc(c) + '</th>';
    }).join('') + '</tr>';
    for (var i = 1; i < rows.length; i++) {
      h += '<tr>' + rows[i].map(function (c) {
        return '<td>' + esc(c) + '</td>';
      }).join('') + '</tr>';
    }
    h += '</table></div>';
    if (lines.length > rows.length) {
      h += '<p class="hint">仅显示前 ' + rows.length + ' 行，共 ' +
           lines.length + ' 行</p>';
    }
    return h;
  }

  function prettyJson(text) {
    try { return '<pre style="font-size:11.5px;max-height:340px;overflow:auto">' +
                 esc(JSON.stringify(JSON.parse(text), null, 1)) + '</pre>'; }
    catch (e) { return '<pre style="font-size:11.5px">' + esc(text) + '</pre>'; }
  }

  /* BLAST 的在线结果 JSON → 命中表（比裸 JSON 好看得多） */
  var HIT_COLS = {
    hom: ['accession', 'title', 'sciname', 'identity', 'align_len',
          'evalue', 'bit_score']
  };
  var HIT_LABEL = {
    query: '查询', hit_type: '类型', accession: '登录号', short_name: '结构域',
    evalue: 'E-value', from: '起始', to: '终止', superfamily: '超家族',
    title: '描述', sciname: '物种', identity: '同一性(%)',
    align_len: '比对长度', bit_score: 'bit score'
  };

  function hitsHtml(text, module) {
    var d;
    try { d = JSON.parse(text); } catch (e) { return prettyJson(text); }
    if (!d || !d.hits || !d.hits.length) {
      return '<p class="hint">该示例未命中任何条目。</p>' + prettyJson(text);
    }
    var cols = HIT_COLS[module] || HIT_COLS.hom;
    var h = '<p class="hint">contig ' + esc(d.contig || '-') +
      (d.orf_len_aa ? ' · 最长 ORF ' + esc(d.orf_len_aa) + ' aa' : '') +
      (d.rid ? ' · NCBI RID ' + esc(d.rid) : '') +
      ' · 命中 ' + d.hits.length + ' 条</p>' +
      '<div class="scroll-lg"><table class="tbl" style="font-size:11.5px"><tr>';
    cols.forEach(function (c) { h += '<th>' + esc(HIT_LABEL[c] || c) + '</th>'; });
    h += '</tr>';
    d.hits.slice(0, 50).forEach(function (hit) {
      h += '<tr>' + cols.map(function (c) {
        return '<td>' + esc(hit[c]) + '</td>';
      }).join('') + '</tr>';
    });
    h += '</table></div>';
    if (d.hits.length > 50) {
      h += '<p class="hint">仅显示前 50 条，共 ' + d.hits.length + ' 条</p>';
    }
    return h;
  }

  /* ---------- 交互热图（可切换配色 / 悬浮看值） ---------- */
  /* 哪些模块有矩阵数据、数据在哪个文件、含哪些序列类型 */
  var MATRIX_SOURCES = {
    sdt:      { file: 'sdt_matrix.json',      seqtypes: ['nt'],  label: 'SDT 同一性（NT）' },
    identity: { file: 'identity.json',        seqtypes: ['nt', 'aa'], label: '同一性（NT+AA）' }
  };
  /* 配色表：Plotly 有同名内置色标的直接用名字（YlGnBu/Viridis/Cividis/Blues/
     Reds/Greens）；matplotlib 系（Plasma/Inferno/Magma/RdYlBu/Spectral/PuOr/
     coolwarm）Plotly **没有**同名内置——写成显式色标数组，否则会被 Plotly
     静默回退成同一个默认渐变，表现就是「换配色没反应」。 */
  var COLORSCALES = [
    ['默认', 'YlOrRd'],
    ['Viridis', 'Viridis'],
    ['Plasma', [[0, '#0d0887'], [0.25, '#7e03a8'], [0.5, '#cc4778'],
                [0.75, '#f89540'], [1, '#f0f921']]],
    ['RdYlBu', [[0, '#313695'], [0.25, '#74add1'], [0.5, '#ffffbf'],
                [0.75, '#fdae61'], [1, '#a50026']]],
    ['Inferno', [[0, '#000004'], [0.25, '#420a68'], [0.5, '#932667'],
                 [0.75, '#dd513a'], [1, '#fca50a']]],
    ['Cividis', 'Cividis'],
    ['Blues', 'Blues'],
    ['Reds', 'Reds'],
    ['Greens', 'Greens'],
    ['PuOr', [[0, '#2d004b'], [0.5, '#f7f7f7'], [1, '#7f3b08']]],
    ['YlGnBu', 'YlGnBu'],
    ['Spectral', [[0, '#9e0142'], [0.25, '#f46d43'], [0.5, '#ffffbf'],
                  [0.75, '#66c2a5'], [1, '#5e4fa2']]],
    ['coolwarm', [[0, '#3b4cc0'], [0.5, '#f7f7f7'], [1, '#b40426']]],
    ['Magma', [[0, '#000004'], [0.25, '#3b0f70'], [0.5, '#8c2981'],
               [0.75, '#de4968'], [1, '#fcfdbf']]]
  ];
  /* window.VPExamples 在文件前部创建时 COLORSCALES 尚未赋值（var 提升只提升
     声明），此处补一次，供 live 运行结果（app.js）读取同一份配色表。 */
  window.VPExamples.colorscales = COLORSCALES;

  function ensurePlotly() {
    if (window.Plotly) return Promise.resolve();
    if (window.__plotlyLoading) return window.__plotlyLoading;
    window.__plotlyLoading = new Promise(function (resolve, reject) {
      var s = document.createElement('script');
      s.src = '/static/plotly.min.js';
      s.onload = resolve;
      s.onerror = function () {
        delete window.__plotlyLoading;
        reject(new Error('Plotly 加载失败'));
      };
      document.head.appendChild(s);
    });
    return window.__plotlyLoading;
  }

  /* 按 module/key 取矩阵数据并重排（sdt 的 order 字段） */
  function loadMatrixData(module, src) {
    var url = '/api/examples/' + encodeURIComponent(module) + '/' + src.file;
    return fetch(url).then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    }).then(function (d) {
      var mat = (d.matrix != null) ? d.matrix : (d.nt || null);
      var aa = d.aa || null;
      var names = d.names || [];
      if (d.order && d.order.length) {
        names = d.order.map(function (i) { return names[i]; });
        mat = mat.map(function (_, r) {
          return d.order.map(function (c) { return mat[d.order[r]][c]; });
        });
      }
      return { names: names, nt: d.matrix != null ? mat : (d.nt || mat),
               aa: aa, seqtype: d.seqtype || 'nt', n: d.n || names.length };
    });
  }

  function renderHeatmap(el, data, colorscale, seqtype) {
    var z = seqtype === 'aa' ? data.aa : data.nt;
    if (!z || !z.length) return;
    var m = data.names || [];
    var unit = seqtype === 'aa' ? 'AA 同一性(%)' : 'NT 同一性(%)';
    var trace = {
      z: z, x: m, y: m, type: 'heatmap', colorscale: colorscale,
      zmin: 0, zmax: 100,
      hovertemplate: '<b>%{x}</b><br>%{y}<br>' + unit + ': %{z:.1f}<extra></extra>',
      colorbar: { title: { text: unit, side: 'right' }, thickness: 14, len: 0.85 }
    };
    var layout = {
      margin: { l: Math.max(150, (m[0] || '').length * 7.2), r: 16, t: 44, b: 120 },
      xaxis: { tickangle: 45, automargin: true, showgrid: false },
      yaxis: { automargin: true, showgrid: false, autorange: 'reversed' },
      height: Math.max(380, m.length * 46 + 160),
      paper_bgcolor: '#fff', plot_bgcolor: '#fff'
    };
    Plotly.newPlot(el, [trace], layout, { responsive: true, displayModeBar: true });
  }

  function renderMatrixHeatmapPanel(module, box) {
    var src = MATRIX_SOURCES[module];
    if (!src || !box) return;
    loadMatrixData(module, src).then(function (mdata) {
      if (!box.isConnected) return;
      var stype = src.seqtypes[0];
      box.innerHTML = '<div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;' +
        'margin:8px 0"><b style="font-size:12.5px;color:#1a5276">🗺 交互热图（' +
        esc(src.label) + '）</b>' +
        '<span style="margin-left:auto;display:flex;gap:6px;align-items:center">' +
        (src.seqtypes.length > 1
          ? '<span class="hint">类型</span><select id="vpMatrixSt" style="padding:2px 6px">' +
            src.seqtypes.map(function (t) { return '<option value="' + t + '">' +
              (t === 'aa' ? 'AA' : 'NT') + '</option>'; }).join('') + '</select>' : '') +
        '<span class="hint">配色</span><select id="vpMatrixCs" style="padding:2px 6px">' +
        COLORSCALES.map(function (c) { return '<option value="' + c[0] + '">' +
          c[0] + '</option>'; }).join('') + '</select>' +
        '<span class="hint">悬浮看值 · 可缩放 · 可下载</span></div></div>' +
        '<div id="vpMatrixEl" style="width:100%;overflow:auto"><p class="hint">⏳ 渲染中…</p></div>';
      function rendercs() {
        var key = box.querySelector('#vpMatrixCs').value;
        var cs = COLORSCALES.filter(function (c) { return c[0] === key; })[0] ||
                 COLORSCALES[0];
        var st = (box.querySelector('#vpMatrixSt') || {}).value || stype;
        renderHeatmap(box.querySelector('#vpMatrixEl'), mdata, cs[1], st);
      }
      box.querySelector('#vpMatrixCs').addEventListener('change', rendercs);
      var stsel = box.querySelector('#vpMatrixSt');
      if (stsel) stsel.addEventListener('change', rendercs);
      rendercs();
    }).catch(function (e) {
      if (box && box.isConnected) {
        box.innerHTML = '<p class="hint">' + esc(t('ui.ex.hmFail', '交互热图不可用: ')) + esc(e.message || e) + '</p>';
      }
    });
  }

  function showExampleResult(module) {
    if (!module) {
      if (typeof toast === 'function') toast(t('ui.ex.noEntry', '该模块暂无示例结果'), '', { ttl: 2200 });
      return;
    }
    var mask = document.createElement('div');
    mask.className = 'dlgmask';
    mask.style.zIndex = 320;
    mask.innerHTML = '<div class="dlg" style="width:min(1120px,95vw);max-height:90vh;' +
      'display:flex;flex-direction:column">' +
      '<div class="dlghead"><b id="vpExTitle">' + esc(t('ui.ex.btnResult', '👁 示例结果')) + '</b>' +
      '<button class="btn small" id="vpExClose">✕</button></div>' +
      '<div id="vpExBody" style="overflow:auto;padding:12px 16px;flex:1 1 auto">' +
      '<p class="hint">⏳ ' + esc(t('c.loading', '加载中…')) + '</p></div></div>';
    document.body.appendChild(mask);
    mask.querySelector('#vpExClose').addEventListener('click', function () {
      mask.remove();
    });
    mask.addEventListener('click', function (e) {
      if (e.target === mask) mask.remove();
    });
    var body = mask.querySelector('#vpExBody');
    fetch('/api/examples/' + encodeURIComponent(module)).then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    }).then(function (d) {
      mask.querySelector('#vpExTitle').textContent =
        t('ui.ex.btnResult', '👁 示例结果') + ' · ' + t('ui.exr.' + module + '.t', d.title || module)
        + t('ui.ex.countPre', '（') + d.files.length + t('ui.ex.countPost', ' 个产物）');
      var imgs = d.files.filter(function (f) { return f.kind === 'image'; });
      var pdfs = d.files.filter(function (f) { return f.kind === 'pdf'; });
      var htmls = d.files.filter(function (f) { return f.kind === 'html'; });
      var texts = d.files.filter(function (f) { return f.kind === 'text'; });
      var others = d.files.filter(function (f) { return f.kind === 'other'; });
      var html = '<p class="hint">' + esc(t('ui.ex.genAt', '生成于 ')) + esc(d.generated_at || '-') +
        esc(t('ui.ex.dir', ' · 目录 ')) + 'examples/results/' + esc(module) +
        '/' + esc(t('ui.ex.roPre', '（只读，与真实结果隔离）')) + '</p>';
      if (imgs.length) {
        html += '<div style="display:flex;flex-wrap:wrap;gap:10px;margin:10px 0">';
        imgs.forEach(function (f) {
          var url = '/api/examples/' + encodeURIComponent(module) + '/' + f.path;
          html += '<div style="border:1px solid var(--line-200,#e3e3e3);border-radius:8px;padding:6px;background:#fff">' +
            '<div class="hint" style="margin:0 0 4px">' + esc(f.path) + '</div>' +
            '<img src="' + url + '" style="max-width:460px;max-height:320px;display:block">' +
            '</div>';
        });
        html += '</div>';
      }
      body.innerHTML = html + '<div id="vpExMatrix"></div><div id="vpExTexts"></div>';
      var textsBox = body.querySelector('#vpExTexts');
      /* 有矩阵数据的模块：渲染交互热图（可切配色 / NT-AA），无矩阵则忽略 */
      if (MATRIX_SOURCES[module]) {
        ensurePlotly().then(function () {
          renderMatrixHeatmapPanel(module, body.querySelector('#vpExMatrix'));
        }).catch(function (e) {
          var mb = body.querySelector('#vpExMatrix');
          if (mb) mb.innerHTML = '<p class="hint">' + esc(t('ui.ex.hmFail', '交互热图不可用: ')) +
            esc(e.message || e) + '</p>';
        });
      }
      /* PDF：不能当 <img>（会显示裂图），用同源 iframe 内嵌查看器预览 */
      if (pdfs.length) {
        var pd = document.createElement('div');
        pd.style.marginTop = '10px';
        pd.innerHTML = '<p class="hint">' + esc(t('ui.ex.pdfSec', '📄 PDF（内嵌预览）：')) + '</p>';
        pdfs.forEach(function (f) {
          var url = '/api/examples/' + encodeURIComponent(module) + '/' + f.path;
          var bx = document.createElement('details');
          bx.className = 'rpt-sec';
          bx.open = pdfs.length === 1;
          bx.style.marginTop = '6px';
          bx.innerHTML = '<summary>' + esc(f.path) + ' <span class="hint">(' +
            (f.size / 1024).toFixed(1) + ' KB' + esc(t('ui.ex.embed', ' · 内嵌预览')) + ')</span></summary>' +
            '<div style="margin-top:6px"><iframe src="' + url +
            '" type="application/pdf" style="width:100%;height:64vh;border:1px solid ' +
            'var(--line-200,#e3e3e3);border-radius:8px;background:#fff"></iframe></div>' +
            '<p class="hint" style="margin:4px 0 0"><a href="' + url +
            '" target="_blank">' + esc(t('ui.ex.newTab', '在新标签页打开 ↗')) + '</a></p>';
          pd.appendChild(bx);
        });
        textsBox.appendChild(pd);
      }
      /* 报告类（.html）：同源 iframe 内联渲染（LOGAN 溯源报告等） */
      htmls.forEach(function (f) {
        var url = '/api/examples/' + encodeURIComponent(module) + '/' + f.path;
        var box = document.createElement('details');
        box.className = 'rpt-sec';
        box.open = htmls.length === 1;
        box.style.marginTop = '8px';
        box.innerHTML = '<summary>' + esc(f.path) + ' <span class="hint">(' +
          (f.size / 1024).toFixed(1) + ' KB' + esc(t('ui.ex.inlinePreview', ' · 在线预览')) + ')</span></summary>' +
          '<div style="margin-top:6px"><iframe src="' + url +
          '" style="width:100%;height:70vh;border:1px solid var(--line-200,#e3e3e3);' +
          'border-radius:8px;background:#fff"></iframe></div>' +
          '<p class="hint" style="margin:4px 0 0"><a href="' + url +
          '" target="_blank">' + esc(t('ui.ex.newTab', '在新标签页打开 ↗')) + '</a></p>';
        textsBox.appendChild(box);
      });
      texts.forEach(function (f) {
        var url = '/api/examples/' + encodeURIComponent(module) + '/' + f.path;
        var card = document.createElement('details');
        card.className = 'rpt-sec';
        card.style.marginTop = '8px';
        card.innerHTML = '<summary>' + esc(f.path) + ' <span class="hint">(' +
          (f.size / 1024).toFixed(1) + ' KB)</span></summary>' +
          '<div class="rpt-sec-body" data-url="' + url + '"><p class="hint">' + esc(t('ui.ex.expandLoad', '展开后加载…')) + '</p></div>';
        card.addEventListener('toggle', function () {
          if (!card.open || card.dataset.loaded) return;
          card.dataset.loaded = '1';
          var box = card.querySelector('.rpt-sec-body');
          fetch(url).then(function (r) { return r.text(); }).then(function (t) {
            var ext = f.path.split('.').pop().toLowerCase();
            if (ext === 'json' && HIT_COLS[module]) box.innerHTML = hitsHtml(t, module);
            else if (ext === 'json') box.innerHTML = prettyJson(t);
            else if (ext === 'tsv' || ext === 'csv') box.innerHTML = tableHtml(t);
            else box.innerHTML = '<pre style="font-size:11.5px;max-height:300px;overflow:auto">' +
              esc(t.slice(0, 20000)) + (t.length > 20000 ? '\n…（已截断）' : '') + '</pre>';
          }).catch(function () { box.innerHTML = '<p class="hint">加载失败</p>'; });
        });
        textsBox.appendChild(card);
      });
      if (others.length) {
        var o = document.createElement('div');
        o.style.marginTop = '10px';
        o.innerHTML = '<p class="hint">其它产物：</p>' + others.map(function (f) {
          var url = '/api/examples/' + encodeURIComponent(module) + '/' + f.path;
          return '<a class="btn small" style="margin:2px" href="' + url + '" download>' +
                 esc(f.path) + '</a>';
        }).join('');
        textsBox.appendChild(o);
      }
    }).catch(function (e) {
      body.innerHTML = '<p class="hint">' + t('ui.ex.resFail') + esc(e.message || e) + '</p>';
    });
  }

})();
