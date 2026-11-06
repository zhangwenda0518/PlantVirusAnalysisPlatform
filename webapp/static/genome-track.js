/* genome-track.js — 基因组交互轨道（d3 v7，随 Archaeopteryx 一起分发）
 *
 * 与 gbdraw 静态 SVG 分工：静态图用来出图归档，这里用来**看细节**——
 * 滚轮/拖拽缩放、悬停读特征、双击复位、单条导出 SVG。
 *
 * 数据来自 POST /api/genome/track（Virus_Platform_Core/genome_track.py）：
 *   {records:[{name, description, length, features:[{type,start,end,strand,label,product}], gc:[[x,ratio]]}]}
 *
 * 依赖全局 d3（/static/vendor/archaeopteryx/d3.v7.min.js），不额外打包库。
 */
(function () {
  'use strict';

  const ROW_H = 20;          // 每条特征行高
  const PAD = 10;
  const GC_H = 64;           // GC 轨道高度
  const TYPE_COLOR = {
    CDS: '#2c7fb8', gene: '#41ab5d', tRNA: '#f0a03c', rRNA: '#d7301f',
    misc_feature: '#8c8c8c', repeat_region: '#7b3294', regulatory: '#c51b8a',
    '5\'UTR': '#66c2a4', '3\'UTR': '#66c2a4', mat_peptide: '#4575b4',
    promoter: '#e66101', terminator: '#b2182b'
  };
  const colorOf = tp => TYPE_COLOR[tp] || '#6b7c8f';

  function fmtBp(n) {
    n = Number(n) || 0;
    return n >= 1000 ? (n / 1000).toFixed(n >= 10000 ? 0 : 1) + ' kb' : n + ' bp';
  }
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g,
      c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  /* 贪心分行：同一行内特征不重叠（按 bp 判，缩放时不变，行数只算一次） */
  function packRows(feats) {
    const rows = [];
    feats.slice().sort((a, b) => a.start - b.start || b.end - a.end)
      .forEach(f => {
        let r = 0;
        while (r < rows.length && rows[r] > f.start - 1) r++;
        rows[r] = f.end;
        f._row = r;
      });
    return Math.max(1, rows.length);
  }

  /* 箭头形状：正链右尖、负链左尖、无链向画矩形 */
  function featurePath(x, y, w, h, strand) {
    if (!strand || w < 10) {
      return 'M' + x + ',' + y + 'h' + w + 'v' + h + 'h' + (-w) + 'z';
    }
    const a = Math.min(h / 2, w * 0.4);
    if (strand > 0) {
      return 'M' + x + ',' + y + 'h' + (w - a) + 'l' + a + ',' + (h / 2)
        + 'l' + (-a) + ',' + (h / 2) + 'h' + (-(w - a)) + 'z';
    }
    return 'M' + (x + w) + ',' + y + 'h' + (-(w - a)) + 'l' + (-a) + ',' + (h / 2)
      + 'l' + a + ',' + (h / 2) + 'h' + (w - a) + 'z';
  }

  function tipFor(f) {
    const len = f.end - f.start + 1;
    const st = f.strand > 0 ? '＋' : (f.strand < 0 ? '－' : '—');
    return '<b>' + esc(f.label || f.type) + '</b>'
      + '<div class="gp-tip-sub">' + esc(f.type) + ' · ' + st + '链</div>'
      + '<div class="gp-tip-sub">' + f.start.toLocaleString() + ' – '
      + f.end.toLocaleString() + '（' + len.toLocaleString() + ' bp）</div>'
      + (f.product && f.product !== f.label
        ? '<div class="gp-tip-sub">' + esc(f.product) + '</div>' : '');
  }

  function renderRecord(host, rec, idx) {
    const wrap = document.createElement('div');
    wrap.className = 'gp-rec';
    const head = document.createElement('div');
    head.className = 'gp-rec-head';
    head.innerHTML = '<span class="gp-rec-name">' + esc(rec.name) + '</span>'
      + '<span class="gp-rec-len">' + fmtBp(rec.length) + ' · '
      + rec.features.length + ' 个特征</span>';
    wrap.appendChild(head);

    const box = document.createElement('div');
    box.className = 'gp-track-box';
    wrap.appendChild(box);
    host.appendChild(wrap);

    const feats = rec.features || [];
    const lanes = packRows(feats);
    const W = Math.max(320, (box.clientWidth || host.clientWidth || 640) - 2 * PAD);
    const hasGc = (rec.gc || []).length > 1;
    const H = PAD + lanes * ROW_H + 26 + (hasGc ? GC_H + 8 : 0);

    const svg = d3.select(box).append('svg')
      .attr('width', W + 2 * PAD).attr('height', H)
      .attr('class', 'gp-track-svg')
      .attr('viewBox', '0 0 ' + (W + 2 * PAD) + ' ' + H);

    const g = svg.append('g').attr('transform', 'translate(' + PAD + ',' + PAD + ')');
    const x0 = d3.scaleLinear().domain([0, Math.max(1, rec.length)]).range([0, W]);
    const gcTop = lanes * ROW_H + 26;
    const yGc = d3.scaleLinear().domain([0, 1]).range([gcTop + GC_H, gcTop]);

    const gAxis = g.append('g').attr('class', 'gp-axis');
    const gFeat = g.append('g').attr('class', 'gp-feats');
    const gGc = g.append('g').attr('class', 'gp-gc');
    const gGcAxis = g.append('g').attr('class', 'gp-gc-axis');

    // GC 底纹（不随缩放的静态部分）
    if (hasGc) {
      const gcArea = d3.area()
        .x(d => x0(d[0])).y0(gcTop + GC_H).y1(d => yGc(d[1]));
      gGc.append('path').datum(rec.gc).attr('d', gcArea)
        .attr('fill', 'rgba(44,127,184,.18)')
        .attr('stroke', '#2c7fb8').attr('stroke-width', 1);
      gGc.append('text').attr('x', 0).attr('y', gcTop + 12)
        .attr('class', 'gp-gc-label').text('GC');
      gGcAxis.append('line')
        .attr('x1', 0).attr('x2', W).attr('y1', gcTop + GC_H).attr('y2', gcTop + GC_H)
        .attr('stroke', '#d7dee6');
    }

    // 悬停提示
    const tip = document.createElement('div');
    tip.className = 'gp-tip';
    box.appendChild(tip);

    function draw(zx) {
      gAxis.attr('transform', 'translate(0,' + (lanes * ROW_H + 2) + ')')
        .call(d3.axisBottom(zx).ticks(Math.max(3, Math.floor(W / 90)))
          .tickFormat(v => v >= 1000 ? (v / 1000).toFixed(v % 1000 ? 1 : 0) + 'k' : v));

      const sel = gFeat.selectAll('path.gp-feat')
        .data(feats, f => f.type + ':' + f.start + ':' + f.end + ':' + (f.label || ''));
      sel.enter().append('path')
        .attr('class', 'gp-feat')
        .merge(sel)
        .attr('d', f => {
          const x = zx(f.start - 1);
          const w = Math.max(0.6, zx(f.end) - x);
          return featurePath(x, f._row * ROW_H + 3, w, ROW_H - 7, f.strand);
        })
        .attr('fill', f => colorOf(f.type))
        .attr('opacity', 0.88)
        .on('mousemove', function (ev, f) {
          tip.innerHTML = tipFor(f);
          tip.style.display = 'block';
          const r = box.getBoundingClientRect();
          tip.style.left = Math.min(ev.clientX - r.left + 12, r.width - 260) + 'px';
          tip.style.top = (ev.clientY - r.top + 12) + 'px';
        })
        .on('mouseleave', () => { tip.style.display = 'none'; });

      // 标签：只在屏幕上有足够宽度时画，避免缩放后糊成一团
      const lbl = gFeat.selectAll('text.gp-feat-label')
        .data(feats.filter(f => f.label), f => f.type + f.start + f.end);
      lbl.enter().append('text').attr('class', 'gp-feat-label').merge(lbl)
        .attr('x', f => (zx(f.start - 1) + zx(f.end)) / 2)
        .attr('y', f => f._row * ROW_H + ROW_H - 3)
        .attr('text-anchor', 'middle')
        .attr('display', f => (zx(f.end) - zx(f.start - 1)) > 46 ? null : 'none')
        .text(f => f.label);
    }

    const zoom = d3.zoom().scaleExtent([1, 400])
      .translateExtent([[0, 0], [W, H]])
      .on('zoom', ev => draw(ev.transform.rescaleX(x0)));
    svg.call(zoom).on('dblclick.zoom', null);
    svg.on('dblclick', () => svg.transition().duration(220)
      .call(zoom.transform, d3.zoomIdentity));
    draw(x0);

    // 单条导出：把当前视图（含缩放后的原点数据）存成独立 SVG
    const btn = document.createElement('button');
    btn.className = 'btn small';
    btn.textContent = '⤓ SVG';
    btn.title = '导出这条记录（当前缩放视图）';
    btn.onclick = () => {
      const blob = new Blob([svg.node().outerHTML],
        { type: 'image/svg+xml;charset=utf-8' });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = (rec.name || ('record_' + idx)) + '_track.svg';
      a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 4000);
    };
    head.appendChild(btn);
  }

  function legend(host, records) {
    const types = [];
    records.forEach(r => (r.features || []).forEach(f => {
      if (types.indexOf(f.type) < 0) types.push(f.type);
    }));
    if (!types.length) return;
    const el = document.createElement('div');
    el.className = 'gp-legend';
    el.innerHTML = types.slice(0, 12).map(tp =>
      '<span class="gp-lg"><i style="background:' + colorOf(tp) + '"></i>'
      + esc(tp) + '</span>').join('');
    host.appendChild(el);
  }

  window.gpTrackRender = function (host, data) {
    host.innerHTML = '';
    const records = (data && data.records) || [];
    if (!records.length) {
      host.innerHTML = '<p class="hint">没有可显示的记录</p>';
      return;
    }
    legend(host, records);
    records.forEach((rec, i) => renderRecord(host, rec, i));
    if (data.truncated) {
      const n = document.createElement('p');
      n.className = 'hint';
      n.textContent = '输入较大，已按上限截断（记录 '
        + (data.limits && data.limits.records) + ' 条 / 每条 '
        + (data.limits && data.limits.features) + ' 个特征）；完整内容请用静态图或命令行导出。';
      host.appendChild(n);
    }
    // 容器宽度变化时重排（窗口缩放、侧栏收起）
    let t = null;
    const onResize = () => {
      clearTimeout(t);
      t = setTimeout(() => window.gpTrackRender(host, data), 250);
    };
    window.addEventListener('resize', onResize, { once: true });
  };
})();
