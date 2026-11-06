/* ============================================================
 * 平台功能全景树（webapp/static/feature-tree.js）
 * ------------------------------------------------------------
 * 平台功能层的单一数据源：手册页（/help）顶部交互树与独立全景页
 * （docs/功能全景图.html）共用同一份数据。节点 href 与顶部导航/
 * 工具页分组一一对应（t-* 卡 → /tools?g=<组>#<卡>）。
 * 结构：VP_FEATURE_TREE.groups[] = { id, icon, zh, en, items[] }
 *        （每个节点都带 zh/en 两份文案，切语言不动数据）
 * 渲染：VPFeatureTree.render(container, opts) —— 可折叠树 + 搜索，
 *       标签按 VP_LANG / <meta name="vp-lang"> / localStorage.vp_lang /
 *       <html lang> 的顺序取语言（zh/en）。
 * ============================================================ */
(function () {
  'use strict';

  window.VP_FEATURE_TREE = {
    updatedAt: '2026-11-04',
    groups: [
      { id: 'resource', icon: '🌐', zh: '数据资源', en: 'Data Resources', items: [
        { zh: '公共数据检索', en: 'Public Data Search', href: '/meta' },
        { zh: '病毒样本预筛', en: 'Virus Sample Prescreen', href: '/prescreen' },
        { zh: 'Open-Virome 公共病毒组', en: 'Open-Virome Public Virome', href: '/virome' },
        { zh: 'LOGAN 溯源', en: 'LOGAN Tracing', href: '/logan' },
        { zh: 'LOGAN 数据下载', en: 'LOGAN data download', href: '/tools?g=resource#t-loganfetch' },
        { zh: '公共数据下载', en: 'Bulk Data Download', href: '/download' },
        { zh: '基因组查询下载', en: 'Genome Query & Download', href: '/refdata' },
        { zh: '数据库构建', en: 'Database Builder', href: '/build' }
      ] },
      { id: 'sample', icon: '🧹', zh: '样本处理', en: 'Sample Processing', items: [
        { zh: '一键前处理', en: 'One-click Prep Chain', href: '/tools?g=sample#t-prepchain' },
        { zh: '样品创建 / 批量导入', en: 'Sample Registry & Batch Import', href: '/samples' },
        { zh: '格式转换', en: 'Format Conversion', href: '/tools?g=sample#t-convert' },
        { zh: '序列质控', en: 'Read QC (fastp)', href: '/tools?g=sample#t-fastp' },
        { zh: '非宿主序列提取（宿主去除）', en: 'Non-host extraction (host removal)', href: '/hostremoval' },
        { zh: '病毒序列提取（样本分析）', en: 'Viral extraction (sample analysis)', href: '/tools?g=sample#t-identify' }
      ] },
      { id: 'kvsuite', icon: '📈', zh: '病毒定量与共识', en: 'Virus Quantification & Consensus', items: [
        { zh: '一键分析（全流程）', en: 'One-click Full Pipeline', href: '/tools?g=kvsuite#t-kvchain' },
        { zh: '已知病毒识别与定量', en: 'Known-virus ID & Quantification', href: '/tools?g=kvsuite#t-kvsuite' },
        { zh: '共识序列分析', en: 'Consensus Analysis', href: '/tools?g=kvsuite#t-consensus' },
        { zh: '病毒变异分析', en: 'Variant Analysis', href: '/tools?g=kvsuite#t-variant' }
      ] },
      { id: 'virus', icon: '🦠', zh: '病毒识别和分类分析', en: 'Virus Identification & Classification', items: [
        { zh: '一键分析', en: 'One-click analysis', href: '/tools?g=virus#t-virchain' },
        { zh: '病毒组装', en: 'Virus Assembly', href: '/tools?g=virus#t-assemble' },
        { zh: '病毒鉴定', en: 'Virus Identification', href: '/tools?g=virus#t-contigs' },
        { zh: '候选序列验证（含同源比对）', en: 'Candidate Verification (+Homology)', href: '/tools?g=virus#t-verify' }
      ] },
      { id: 'annotate', icon: '🔬', zh: '病毒注释分析', en: 'Virus Annotation', items: [
      { zh: '⚡ 一键分析（注释→图谱→提交）', en: 'One-click (annotation→plots→submission)', href: '/tools?g=annotate#t-annochain' },
        { zh: '功能注释（含 ORF 预测）', en: 'Functional Annotation (+ORF)', href: '/annotation' },
        { zh: '基因组图谱', en: 'Genome Plot', href: '/genome' },
        { zh: '数据提交（NCBI 提交准备）', en: 'NCBI Submission Prep', href: '/submit' }
      ] },
      { id: 'detect', icon: '💊', zh: '检测与防治', en: 'Detection & Control', items: [
        { zh: '引物设计', en: 'Primer Design', href: '/primer' },
        { zh: 'CRISPR 检测试剂设计', en: 'CRISPR Assay Designer', href: '/tools?g=detect#t-crispr' },
        { zh: 'dsRNA 设计', en: 'dsRNA Designer', href: '/tools?g=detect#t-dsrna' },
        { zh: 'miRNA 靶标预测', en: 'miRNA Target Prediction', href: '/tools?g=detect#t-mirna' }
      ] },
      { id: 'compare', icon: '⚖️', zh: '比较基因组分析', en: 'Comparative Genomics', items: [
        { zh: '一键分析（参考→提取→比对→建树→SDT）', en: 'One-click (refs→extract→align→tree→SDT)', href: '/tools?g=compare#t-cmpchain' },
        { zh: '参考序列获取', en: 'Reference Acquisition', href: '/tools?g=compare#t-seqprep' },
        { zh: 'CDS / PEP 提取产物', en: 'CDS · PEP Export', href: '/cds-export' },
        { zh: '序列比对（MAFFT + trimAl）', en: 'MSA (MAFFT + trimAl)', href: '/tools?g=compare#t-align' },
        { zh: '进化树构建（科/属级）', en: 'Tree Building', href: '/tools?g=compare#t-treebuild' },
        { zh: 'SDT 同一性分析（属级）', en: 'SDT Identity', href: '/tools?g=compare#t-sdt' }
      ] },
      { id: 'phylodyn', icon: '🧬', zh: '进化动力学分析', en: 'Phylodynamics', items: [
        { zh: '⚡ 一键分析', en: '⚡ One-click analysis', href: '/tools?g=phylodyn#t-phylodyn' },
        { zh: '进化动力学数据接入', en: 'Data Intake & Curation', href: '/tools?g=phylodyn#t-pdprep' },
        { zh: 'RDP5 重组分析', en: 'RDP5 Recombination', href: '/tools?g=phylodyn#t-rdp' },
        { zh: '时间信号与定年', en: 'Temporal Signal & Dating', href: '/tools?g=phylodyn#t-rtt' },
        { zh: '系统地理分析', en: 'Phylogeography', href: '/tools?g=phylodyn#t-phylogeo' }
      ] },
      { id: 'result', icon: '📊', zh: '结果中心', en: 'Results Center', items: [
        { zh: '样品结果 / 专项结果', en: 'Sample & Special Results', href: '/results' },
        { zh: '进化树 · SDT · MSA 查看器', en: 'Tree · SDT · MSA Viewers', href: '/results' }
      ] },
      { id: 'platform', icon: '✨', zh: '平台级能力', en: 'Platform Capabilities', items: [
        { zh: 'AI 助手（解读 · 播报 · 闸门 · 检索执行）', en: 'AI Assistant (interpret · brief · gates · search-act)', href: '/help' },
        { zh: '任务中心（并发闸门 · 进度自学习）', en: 'Task Center (gates & ETA learning)', href: '/tasks' },
        { zh: '全量 CLI（main.py 40 子命令）', en: 'Full CLI (40 subcommands)', href: '/help' },
        { zh: '中英双语界面', en: 'Bilingual UI (zh/en)', href: '/settings' },
        { zh: '可视化报告', en: 'Visual Reports', href: '/results' }
      ] }
    ]
  };

  window.VPFeatureTree = {
    data: window.VP_FEATURE_TREE,

    render: function (container, opts) {
      opts = opts || {};
      // Language: VP_LANG is i18n.js's top-level `let` binding (not on window),
      // but classic scripts share the global lexical scope and can read it by
      // name. Fall back to <meta name="vp-lang">, then localStorage.vp_lang,
      // then <html lang>, so the tree still renders in English where i18n.js is
      // not in the page (standalone panorama page) or has not run yet.
      const lang = (function () {
        let l = '';
        try {
          if (typeof VP_LANG !== 'undefined' && VP_LANG) l = String(VP_LANG);
        } catch (e) { l = ''; }
        if (l !== 'en' && l !== 'zh') {
          try {
            const m = document.querySelector('meta[name="vp-lang"]');
            if (m && m.content) l = m.content.trim();
          } catch (e) { /* ignore */ }
        }
        if (l !== 'en' && l !== 'zh') {
          try {
            const s = localStorage.getItem('vp_lang');
            if (s === 'en' || s === 'zh') l = s;
          } catch (e) { /* ignore */ }
        }
        if (l !== 'en' && l !== 'zh') {
          const hl = (document.documentElement.lang || '').toLowerCase();
          l = hl.indexOf('zh') === 0 ? 'zh' : (hl ? 'en' : 'zh');
        }
        return l === 'en' ? 'en' : 'zh';
      })();
      const L = node => node[lang] || node.zh;
      const data = window.VP_FEATURE_TREE;
      const nItems = data.groups.reduce((a, g) => a + g.items.length, 0);
      const compact = !!opts.compact;

      const box = (typeof container === 'string')
        ? document.getElementById(container) : container;
      if (!box) return;

      box.innerHTML =
        '<div class="vp-ftree"' + (compact ? ' data-compact="1"' : '') + '>' +
        '<div class="vp-ftree-bar">' +
        '<span class="vp-ftree-count"></span>' +
        '<input class="vp-ftree-q" type="search" placeholder="' +
        (lang === 'en' ? 'Search features…' : '搜索功能…') + '">' +
        '<button type="button" class="vp-ftree-expand">' +
        (lang === 'en' ? 'Expand all' : '展开全部') + '</button>' +
        '<button type="button" class="vp-ftree-collapse">' +
        (lang === 'en' ? 'Collapse' : '收起') + '</button>' +
        '</div><ul class="vp-ftree-list"></ul></div>';

      const list = box.querySelector('.vp-ftree-list');
      const q = box.querySelector('.vp-ftree-q');
      box.querySelector('.vp-ftree-count').textContent =
        (lang === 'en'
          ? data.groups.length + ' groups · ' + nItems + ' features'
          : data.groups.length + ' 大类 · ' + nItems + ' 项功能');

      data.groups.forEach(g => {
        const li = document.createElement('li');
        li.className = 'vp-ftree-g';
        li.innerHTML =
          '<div class="vp-ftree-grow" role="button" tabindex="0">' +
          '<span class="vp-ftree-arrow">▸</span>' +
          '<span class="vp-ftree-gicon">' + g.icon + '</span>' +
          '<b>' + L(g) + '</b>' +
          '<span class="vp-ftree-gn">' + g.items.length + '</span></div>' +
          '<ul class="vp-ftree-items" hidden></ul>';
        const itemsUl = li.querySelector('.vp-ftree-items');
        g.items.forEach(it => {
          const eli = document.createElement('li');
          eli.className = 'vp-ftree-item';
          eli.innerHTML = '<a href="' + it.href + '">' + L(it) + '</a>';
          itemsUl.appendChild(eli);
        });
        const row = li.querySelector('.vp-ftree-grow');
        const toggle = force => {
          const open = (force !== undefined) ? force : itemsUl.hidden;
          itemsUl.hidden = !open;
          li.classList.toggle('open', open);
          row.querySelector('.vp-ftree-arrow').textContent = open ? '▾' : '▸';
        };
        row.addEventListener('click', () => toggle());
        row.addEventListener('keydown', ev => {
          if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); toggle(); }
        });
        li._toggle = toggle;
        list.appendChild(li);
      });

      const setAll = open =>
        box.querySelectorAll('li.vp-ftree-g').forEach(li => li._toggle(open));
      box.querySelector('.vp-ftree-expand').addEventListener('click', () => setAll(true));
      box.querySelector('.vp-ftree-collapse').addEventListener('click', () => setAll(false));

      q.addEventListener('input', () => {
        const kw = q.value.trim().toLowerCase();
        box.querySelectorAll('li.vp-ftree-g').forEach(li => {
          let hit = 0;
          li.querySelectorAll('.vp-ftree-item').forEach(eli => {
            const match = !kw || eli.textContent.toLowerCase().includes(kw);
            eli.style.display = match ? '' : 'none';
            if (match) hit++;
          });
          const groupHit = !kw || li.querySelector('.vp-ftree-grow')
            .textContent.toLowerCase().includes(kw);
          li.style.display = (kw && !hit && !groupHit) ? 'none' : '';
          if (kw && hit) li._toggle(true);
          if (!kw) li._toggle(false);
        });
      });

      if (opts.defaultOpen) setAll(true);
    }
  };
})();
