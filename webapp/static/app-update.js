/* app-update.js — 软件更新卡片 + 顶栏更新徽标（2026-11-05 自动更新功能）。
   数据全部来自 /api/update/*；设置读写走 /api/settings（三键）。
   app.py 起 daemon 线程跑全自动静默更新，本脚本轮询 /api/update/status
   展示后台线程状态；顶栏徽标（#updBadge）有新版且未跳过时显示。 */
'use strict';

var UPD = {
  latest: '', current: '', hasUpdate: false, skipped: false,
  pollTimer: null
};

function updEsc(s) {
  return String(s || '').replace(/[&<>"']/g, function (c) {
    return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
  });
}

function updInit() {
  var box = document.getElementById('updateBox');
  if (!box) {
    // 非 /settings 页：只挂顶栏徽标——静默检查一次（后端 10 分钟缓存，
    // 不轮询，避免全站 5s 一次的请求噪音），徽标常驻显示直到处理/跳过
    checkUpdate(false);
    return;
  }
  updLoadSettings();
  checkUpdate(false);
  UPD.pollTimer = setInterval(updPollStatus, 5000);
  updPollStatus();
}

// —— 设置三键读写（走 /api/settings，服务端三段式校验） ——
function updLoadSettings() {
  fetch('/api/settings')
    .then(function (r) { return r.json(); })
    .then(function (d) {
      if (!d) return;
      var a = document.getElementById('updAutoCheck');
      var b = document.getElementById('updAutoApply');
      var s = document.getElementById('updSrc');
      if (a) a.checked = d.update_auto_check !== false;
      if (b) b.checked = d.update_auto_apply !== false;
      if (s) s.value = d.update_manifest_url || '';
    })
    .catch(function () {});
}

function saveUpdateSettings() {
  var body = {
    update_auto_check: document.getElementById('updAutoCheck').checked,
    update_auto_apply: document.getElementById('updAutoApply').checked,
    update_manifest_url: (document.getElementById('updSrc') || {}).value || ''
  };
  fetch('/api/settings', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body)
  }).then(function (r) { return r.json().catch(function () { return {}; })
      .then(function (j) { return [r.ok, j]; }); })
    .then(function (res) {
      if (res[0]) { toast('更新设置已保存', 'ok'); }
      else { toast((res[1] && res[1].error) || '保存失败', 'err'); }
    })
    .catch(function () { toast('网络异常', 'err'); });
}

// —— 检查更新（force=true 手动按钮；false 进页静默） ——
function checkUpdate(force) {
  var box = document.getElementById('updateBox');
  if (!box) return;
  if (force === undefined) force = true;
  if (force === true) box.innerHTML = '<span class="hint">检查中…</span>';
  fetch('/api/update/check')
    .then(function (r) { return r.json(); })
    .then(function (d) {
      UPD.latest = d.latest || '';
      UPD.current = d.current || '';
      UPD.hasUpdate = !!d.has_update && !d.skipped;
      UPD.skipped = !!d.skipped;
      var cv = document.getElementById('updCurVer');
      var ed = document.getElementById('updEdition');
      if (cv) cv.textContent = d.current || '—';
      if (ed) ed.textContent = d.edition === 'full' ? 'full' : 'minimal';
      if (d.has_update) {
        var skipTag = d.skipped ? '（已跳过）' : '';
        // 发布控制（2026-11-05）：paused / 灰度未轮到 只拦自动更新，
        // 手动「立即安装」仍可用（显式点击即确认）。
        var controlTag = '';
        if (d.paused) {
          controlTag = '<div class="hint" style="margin-top:6px; color:var(--warn,#c0392b)">⏸ ' +
            updEsc(t('se.upd.pausedNote', '该版本已暂停自动更新（仍可手动安装）')) + '</div>';
        } else if (d.in_rollout === false) {
          controlTag = '<div class="hint" style="margin-top:6px">🌓 ' +
            updEsc(t('se.upd.rolloutNote', '灰度发布中') + '（' +
                   (d.rollout_percent != null ? d.rollout_percent : 100) + '%）' +
                   t('se.upd.notInRollout', '，本机暂未轮到')) + '</div>';
        }
        box.innerHTML =
          '<span style="color:var(--ok,#2a9d3f)">↑ 新版本 ' + updEsc(d.latest) +
          ' 可用' + skipTag + '</span>' + controlTag +
          (d.notes_zh ? '<div class="hint" style="margin-top:6px; white-space:pre-wrap">' +
            updEsc(d.notes_zh) + '</div>' : '');
        var ib = document.getElementById('updInstallBtn');
        var sb = document.getElementById('updSkipBtn');
        if (ib) ib.hidden = false;
        if (sb) sb.hidden = d.skipped;
      } else if (d.ok) {
        box.innerHTML = '<span style="color:var(--ok,#2a9d3f)">✓ 已是最新（' +
          updEsc(d.current) + '）</span>';
        var ib2 = document.getElementById('updInstallBtn');
        var sb2 = document.getElementById('updSkipBtn');
        if (ib2) ib2.hidden = true;
        if (sb2) sb2.hidden = true;
      } else {
        box.innerHTML = '<span style="color:var(--warn,#c0392b)">检查失败：' +
          updEsc(d.error || '未知原因') + '</span>';
      }
      updBadgeShow();
    })
    .catch(function () {
      box.innerHTML = '<span style="color:var(--warn,#c0392b)">网络异常，检查失败</span>';
    });
}

// —— 状态轮询：后台自动更新线程的进度视图 ——
function updPollStatus() {
  fetch('/api/update/status')
    .then(function (r) { return r.json(); })
    .then(function (d) {
      var box = document.getElementById('updateBox');
      if (!box) return;
      var line = '';
      if (d.phase === 'download') {
        line = '正在下载 ' + (d.staged_version || UPD.latest) + ' 更新包' +
          (d.progress != null ? ' ' + d.progress + '%' : '');
      } else if (d.phase === 'staged') {
        line = (d.staged_version || UPD.latest) + ' 已暂存待换装';
      } else if (d.phase === 'awaiting_idle') {
        line = '更新包就绪，等待运行中/排队任务结束后自动换装';
      } else if (d.phase === 'applying') {
        line = '正在换装重启…';
      } else if (d.phase === 'error') {
        line = '自动更新出错：' + (d.detail || '');
      }
      if (line) box.innerHTML = '<span class="hint">' + updEsc(line) + '</span>';
    })
    .catch(function () {});
}

// —— 立即安装（消费暂存包，服务端活动任务守卫 + 停机重启） ——
function installUpdate() {
  if (!window.confirm(t('se.upd.confirmMsg'))) return;
  var btn = document.getElementById('updInstallBtn');
  if (btn) btn.disabled = true;
  fetch('/api/update/install', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ confirm: 'install' })
  })
    .then(function (r) { return r.json().catch(function () { return {}; }); })
    .then(function (d) {
      if (d.ok && d.downloading) {
        // 自动安装关闭时「立即安装」= 先后台下载，完成自动换装
        toast(t('se.upd.downloading',
                '正在下载更新包，完成后自动换装…'), 'ok');
      } else if (d.ok) {
        toast('换装开始，平台即将重启…', 'ok');
      } else if (d.awaiting_idle) {
        if (btn) btn.disabled = false;
        toast('有任务在跑，空闲后自动继续', 'warn');
      } else {
        if (btn) btn.disabled = false;
        toast(d.error || '安装失败', 'err');
      }
    })
    .catch(function () { toast('网络异常', 'err'); });
}

// —— 跳过此版本 ——
function skipUpdate() {
  fetch('/api/update/skip', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ version: UPD.latest })
  }).then(function (r) { return r.json(); })
    .then(function (d) {
      if (d.ok) {
        UPD.skipped = true;
        var b = document.getElementById('updSkipBtn');
        if (b) b.hidden = true;
        toast('已跳过 ' + UPD.latest + '，下个版本仍会提醒', 'ok');
      }
    })
    .catch(function () {});
}

// —— 顶栏徽标（_nav.html 的 #updBadge，所有页面可见） ——
function updBadgeShow() {
  var b = document.getElementById('updBadge');
  if (!b) return;
  if (UPD.hasUpdate && !UPD.skipped) {
    b.textContent = '↑ ' + UPD.latest;
    b.hidden = false;
  } else {
    b.hidden = true;
  }
}

document.addEventListener('DOMContentLoaded', updInit);
