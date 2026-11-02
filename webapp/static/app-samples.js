/* 样品创建 / 批量导入的共享逻辑（2026-10-29）
 *
 * 这段代码原先内联在 samples.html。用户要求「样品创建 / 批量导入」直接出现在
 * 总览，而它同时仍是 /samples 的主功能 —— 于是标记抽到 _samples_new.html、
 * 逻辑抽到本文件，两个页面加载同一份，避免表单/校验两处漂移。
 *
 * 依赖（加载顺序在它们之后）：app.js 的 $ / t / esc / toast / withBtn /
 * apiCreateSample；app-browse.js 的 browse / browseDir + _browse_dlg.html。
 */
function val(id) { return ($(id)?.value || '').trim(); }

$('subsample_on')?.addEventListener('change', () => {
  $('sub_row').style.display = $('subsample_on').checked ? '' : 'none';
});
/* 比例模式只用"比例"输入，条数输入仅 prefix/random 有意义 */
$('sub_mode')?.addEventListener('change', () => {
  $('sub_prop_row').style.display = $('sub_mode').value === 'proportion' ? '' : 'none';
});

/* ---- 从文件夹批量导入：扫描 → 预览 → 一键创建 ---- */
let SCAN_ROWS = [];

async function scScan(btn) {
  return withBtn(btn, async () => {
    const errBox = $('scanErr');
    errBox.textContent = '';
    if (!val('scanDir')) { errBox.textContent = t('sc.needFolder', '请先选择文件夹'); return; }
    const r = await fetch('/api/samples/scan_folder', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ folder: val('scanDir') })});
    const d = await r.json().catch(() => ({}));
    if (!r.ok) { errBox.textContent = d.error || t('c.loadFail', '加载失败'); return; }
    SCAN_ROWS = d.samples || [];
    if (!$('scanProject').value) $('scanProject').value = d.project || '';
    $('scanBox').style.display = '';
    scScanRender();
  }, t('c.loading', '加载中…'));
}

function scScanRender() {
  const cut = s => esc(String(s || '').split(/[\\/]/).pop());
  const rows = SCAN_ROWS.map((x, i) =>
    `<tr><td><input type="checkbox" class="scan-pick" data-i="${i}" checked style="width:auto"></td>` +
    // 样品名可编辑：创建前纠正自动提取的名字（同名冲突会在创建时报错提示）
    `<td><input class="scan-name" data-i="${i}" value="${esc(x.name)}" ` +
    `style="width:130px;font-size:12px" title="${esc(x.name)}"></td>` +
    `<td>${x.r2 ? 'PE' : 'SE'}</td>` +
    `<td class="mono" style="max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(x.r1)}">${cut(x.r1)}</td>` +
    `<td class="mono" style="max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(x.r2)}">${cut(x.r2)}</td></tr>`).join('');
  $('scanTable').innerHTML =
    '<table style="width:100%;border-collapse:collapse;font-size:12px">' +
    `<tr><th></th><th style="text-align:left">${t('sc.colName', '样品名（可改）')}</th>` +
    `<th style="text-align:left">${t('sc.colType', '类型')}</th>` +
    '<th style="text-align:left">R1</th><th style="text-align:left">R2</th></tr>' + rows + '</table>';
  scScanCount();
}

function scScanCount() {
  const n = document.querySelectorAll('.scan-pick:checked').length;
  $('scanCount').textContent =
    t('sc.pickedN', '已选 {n} / {m} 个样品').replace('{n}', n).replace('{m}', SCAN_ROWS.length);
}

function scScanAll(on) {
  document.querySelectorAll('.scan-pick').forEach(c => { c.checked = !!on; });
  scScanCount();
}

document.addEventListener('change', e => {
  if (e.target && e.target.classList && e.target.classList.contains('scan-pick')) scScanCount();
});

async function scScanCreate(btn) {
  return withBtn(btn, async () => {
    const errBox = $('scanErr');
    errBox.textContent = '';
    const idx = [...document.querySelectorAll('.scan-pick:checked')].map(c => +c.dataset.i);
    if (!idx.length) { errBox.textContent = t('sc.pickFirst', '请先勾选要创建的样品'); return; }
    // 先收回编辑过的样品名（空值退回自动提取名）
    document.querySelectorAll('#scanTable .scan-name').forEach(inp => {
      const i = +inp.dataset.i;
      const v = (inp.value || '').trim();
      if (i < SCAN_ROWS.length && v) SCAN_ROWS[i].name = v;
    });
    // 项目名留空 → 自动按时间生成（一个项目可含多样本、单双端混合）
    let project = val('scanProject');
    if (!project) {
      const d = new Date(), pad = n => String(n).padStart(2, '0');
      project = '项目_' + d.getFullYear() + pad(d.getMonth() + 1)
        + pad(d.getDate()) + '_' + pad(d.getHours()) + pad(d.getMinutes());
    }
    const created = [], adjusted = [];
    for (const i of idx) {
      const x = SCAN_ROWS[i];
      const res = await apiCreateSample({
        sample: x.name, r1: x.r1, r2: x.r2 || null, project });
      if (!res.ok) {
        if (res.conn) setConnBanner(true);
        errBox.textContent = `${x.name}: ${res.error}`;
        return;
      }
      created.push(res.data.sample);
      if (res.data.note) adjusted.push(`${x.name} → ${res.data.sample}`);
    }
    toast(t('sc.batchImport', '批量导入'),
      t('sc.batchCreated', '已创建 {n} 个样品').replace('{n}', created.length),
      { ttl: 4000 });
    if (adjusted.length) errBox.textContent = adjusted.join('，');
    scAfterCreate();
  }, t('sc.creating', '⏳ 创建中…'));
}

async function scCreate(btn) {
  return withBtn(btn, async () => {
  const errBox = $('cErr');
  errBox.textContent = '';
  if (!val('r1')) { errBox.textContent = t('sc.needR1','请选择 R1 FASTQ 文件'); return; }
  const body = {
    sample: val('sample'), r1: val('r1'), r2: val('r2') || null,
    project: val('s_project') || null,
    subsample: $('subsample_on')?.checked ? (+val('subsample') || 100000) : 0,
    subsample_mode: $('sub_mode')?.value || 'prefix',
    subsample_prop: ($('subsample_on')?.checked && $('sub_mode')?.value === 'proportion')
      ? (+val('sub_prop') || 0.5) : 0,
  };
  const res = await apiCreateSample(body);
  if (!res.ok) {
    if (res.conn) setConnBanner(true);
    errBox.textContent = res.error; return;
  }
  $('r1').value = ''; $('r2').value = ''; $('sample').value = '';
  // 后端可能规范化过样品名（中文/空格 → '_'）：显式告知实际登记名
  const actual = (res.data && res.data.sample) || body.sample;
  toast(t('sc.createSample', '创建样品'), `✔ ${actual}`, {ttl: 4000});
  if (res.data && res.data.note) errBox.textContent = res.data.note;
  scAfterCreate();
  }, t('sc.creating','⏳ 创建中…'));
}
/* 创建完成后的刷新：/samples 刷新样品表；总览刷新「最近样品」。
   两个页面共用同一套创建逻辑，刷新目标按页面存在性分派。 */
function scAfterCreate() {
  if (typeof scLoadSamples === 'function') scLoadSamples();
  if (typeof loadHome === 'function' && document.getElementById('homeSamples')) {
    loadHome();
  }
}
