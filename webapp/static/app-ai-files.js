/* AI 分析台右侧「工作区文件」面板 + 文件预览（2026-10-30）
 *
 * 只读浏览平台目录内的文件：列表走 /api/browse?all=1（已有接口），预览走
 * /api/fs/read（文本片段）与 /api/fs/raw（图片/HTML 原样）。服务端
 * check_path(in_platform=True) 兜底，前端不做任何路径拼接假设。
 *
 * 与对话的关系：文件面板只负责"看"；想让 AI 用某个文件，点输入框的「＋」
 * 附加（AI.attachments），路径会随对话注入上下文。
 */
window.VPFiles = (function () {
  'use strict';

  // 兜底根（/api/fs/roots 不可用时）：相对路径，服务端按平台根解析。
  // 正常情况下 init() 会用服务端返回的绝对路径替换它们 —— 分发版把示例
  // 放在外置的 VirusPlatform-Examples 里，只有服务端知道真实位置。
  var ROOTS = [
    {id: 'sample', label: '当前样品', path: function () {
      var s = (window.VP_CTX && VP_CTX.sample) || '';
      return s ? 'run/results/' + s : 'run/results';
    }},
    {id: 'tool', label: '工具运行', path: function () { return 'run/tool_runs'; }},
    {id: 'examples', label: '示例数据', path: function () { return 'examples'; }},
    {id: 'downloads', label: '下载数据', path: function () { return 'run/downloads'; }}
  ];

  var TEXT_EXT = ['txt', 'tsv', 'csv', 'tab', 'json', 'log', 'md', 'fa', 'fasta',
                  'fna', 'fas', 'fq', 'fastq', 'nwk', 'tree', 'gff', 'gtf',
                  'yaml', 'yml', 'py', 'sh', 'r', 'ini', 'cfg'];
  var TABLE_EXT = ['tsv', 'csv', 'tab'];
  var IMG_EXT = ['png', 'jpg', 'jpeg', 'gif', 'svg', 'webp', 'bmp'];
  var HTML_EXT = ['html', 'htm'];

  var cur = {rootId: 'sample', cwd: '', filter: ''};

  function t(k, fb) {
    try { if (typeof window.t === 'function') { var v = window.t(k, fb); return v || fb; } } catch (e) {}
    return fb;
  }
  function esc(s) {
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }
  function ext(name) {
    var m = String(name || '').toLowerCase().match(/\.([a-z0-9]+)$/);
    return m ? m[1] : '';
  }
  function fmtSize(n) {
    n = Number(n) || 0;
    if (n < 1024) return n + ' B';
    if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB';
    if (n < 1024 * 1024 * 1024) return (n / 1024 / 1024).toFixed(1) + ' MB';
    return (n / 1024 / 1024 / 1024).toFixed(2) + ' GB';
  }

  function root() {
    return ROOTS.filter(function (r) { return r.id === cur.rootId; })[0] || ROOTS[0];
  }

  /* 当前根的路径：服务端给的 base（绝对路径）优先；sample 根再拼样品名 */
  function rootPath() {
    var r = root();
    var base = r.base || (typeof r.path === 'function' ? r.path() : '');
    if (r.id === 'sample') {
      var s = (window.VP_CTX && VP_CTX.sample) || '';
      if (s) return joinPath(base || 'run/results', s);
    }
    return base;
  }

  function load(path) {
    var q = path || rootPath();
    fetch('/api/browse?all=1&path=' + encodeURIComponent(q))
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d || d.error) throw new Error((d && d.error) || 'list failed');
        cur.cwd = d.cwd || q;
        render(d);
      })
      .catch(function (e) {
        var list = document.getElementById('aiFilesList');
        if (list) list.innerHTML = '<p class="hint">' + esc(String(e.message || e)) + '</p>';
      });
  }

  function render(d) {
    var pathBox = document.getElementById('aiFilesPath');
    if (pathBox) pathBox.textContent = cur.cwd || '';
    var items = [];
    (d.dirs || []).forEach(function (name) {
      items.push({name: name, dir: true});
    });
    (d.files || []).forEach(function (f) {
      items.push({name: f.name, dir: false, size: f.size});
    });
    var f = (cur.filter || '').toLowerCase();
    if (f) items = items.filter(function (x) { return x.name.toLowerCase().indexOf(f) >= 0; });
    var list = document.getElementById('aiFilesList');
    if (!list) return;
    if (!items.length) {
      list.innerHTML = '<p class="hint">' + esc(t('ai.files.empty', '（没有可显示的文件）')) + '</p>';
      return;
    }
    list.innerHTML = items.map(function (x) {
      return '<div class="ai-file' + (x.dir ? ' dir' : '') + '"' +
        ' data-name="' + esc(x.name) + '" data-dir="' + (x.dir ? '1' : '0') + '">' +
        '<span class="ai-file-ico">' + (x.dir ? '📁' : '📄') + '</span>' +
        '<span class="ai-file-nm" title="' + esc(x.name) + '">' + esc(x.name) + '</span>' +
        '<span class="ai-file-sz">' + esc(x.size || '') + '</span></div>';
    }).join('');
    Array.prototype.forEach.call(list.querySelectorAll('.ai-file'), function (el) {
      el.addEventListener('click', function () {
        var name = el.dataset.name;
        if (el.dataset.dir === '1') {
          load(joinPath(cur.cwd, name));
        } else {
          preview(joinPath(cur.cwd, name));
        }
      });
    });
  }

  function joinPath(dir, name) {
    var d = String(dir || '').replace(/[\\/]+$/, '');
    return d + '/' + name;
  }

  /* ---------------- 预览 ---------------- */
  function preview(path) {
    var name = path.split(/[\\/]/).pop();
    var e = ext(name);
    var box = document.getElementById('fsPreview');
    var body = document.getElementById('fsPreviewBody');
    if (!box || !body) return;
    document.getElementById('fsPreviewName').textContent = name;
    document.getElementById('fsPreviewMeta').textContent = path;
    box.hidden = false;
    body.innerHTML = '<p class="hint">' + esc(t('c.loading', '加载中…')) + '</p>';

    if (IMG_EXT.indexOf(e) >= 0) {
      body.innerHTML = '<img class="fs-img" alt="" src="/api/fs/raw?path=' +
        encodeURIComponent(path) + '">';
      return;
    }
    if (HTML_EXT.indexOf(e) >= 0) {
      body.innerHTML = '<iframe class="fs-frame" src="/api/fs/raw?path=' +
        encodeURIComponent(path) + '"></iframe>';
      return;
    }
    if (TEXT_EXT.indexOf(e) < 0) {
      body.innerHTML = '<p class="hint">' +
        esc(t('ai.files.noPreview', '这类文件不支持内嵌预览。')) + '</p>' +
        '<p><a class="btn small" target="_blank" rel="noopener" href="/api/fs/raw?path=' +
        encodeURIComponent(path) + '">' + esc(t('ai.files.open', '打开原文件')) + '</a></p>';
      return;
    }
    fetch('/api/fs/read?path=' + encodeURIComponent(path) + '&max_bytes=300000')
      .then(function (r) {
        if (!r.ok) {
          return r.json().catch(function () { return {}; }).then(function (e) {
            throw new Error((e && (e.error || e.message)) || ('HTTP ' + r.status));
          });
        }
        return r.json();
      })
      .then(function (d) {
        if (TABLE_EXT.indexOf(e) >= 0) {
          body.innerHTML = renderTable(d.text);
        } else {
          body.innerHTML = '<pre class="fs-pre">' + esc(d.text) + '</pre>';
        }
        if (d.truncated) {
          body.insertAdjacentHTML('beforeend',
            '<p class="hint">' + esc(t('ai.files.truncated', '（已截断，仅显示前 300KB）')) + '</p>');
        }
      })
      .catch(function (err) {
        body.innerHTML = '<p class="hint">' + esc(String(err.message || err)) + '</p>';
      });
  }

  function renderTable(text) {
    var lines = String(text || '').split(/\r?\n/).filter(function (l) { return l.trim(); });
    var head = lines.shift() || '';
    var sep = /^[\s|:-]+$/.test(lines[0] || '') ? lines.shift() : null;  // markdown 分隔行
    function cells(s) {
      var parts = s.indexOf('\t') >= 0 ? s.split('\t') : s.split(/[,\t]/);
      return parts.map(function (c) { return c.replace(/^\s*\|/, '').replace(/\|\s*$/, '').trim(); });
    }
    var rows = lines.slice(0, 200).map(cells);
    var h = '<div class="fs-tbl-wrap"><table class="tbl fs-tbl"><thead><tr>' +
      cells(head).map(function (c) { return '<th>' + esc(c) + '</th>'; }).join('') +
      '</tr></thead><tbody>';
    rows.forEach(function (r) {
      h += '<tr>' + r.map(function (c) { return '<td>' + esc(c) + '</td>'; }).join('') + '</tr>';
    });
    h += '</tbody></table></div>';
    if (lines.length > 200) {
      h += '<p class="hint">' + esc(t('ai.files.rowsLimit', '（仅显示前 200 行）')) + '</p>';
    }
    return h;
  }

  function close() {
    var box = document.getElementById('fsPreview');
    if (box) box.hidden = true;
  }

  /* 工作空间：AI 工作台选了目录后，把它作为一个根插到最前面并切过去 */
  function setRoot(path) {
    var p = String(path || '').trim();
    if (!p) return;
    var ws = ROOTS.filter(function (r) { return r.id === 'ws'; })[0];
    if (ws) ws.base = p;
    else ROOTS.unshift({id: 'ws', label: '工作空间', base: p});
    cur.rootId = 'ws';
    cur.filter = '';
    var sel = document.getElementById('aiFilesRoot');
    if (sel) { fillRoots(sel); sel.value = 'ws'; }
    var f = document.getElementById('aiFilesFilter');
    if (f) f.value = '';
    load();
  }

  function clearRoot() {
    ROOTS = ROOTS.filter(function (r) { return r.id !== 'ws'; });
    if (cur.rootId === 'ws') cur.rootId = (ROOTS[0] && ROOTS[0].id) || 'sample';
    var sel = document.getElementById('aiFilesRoot');
    if (sel) { fillRoots(sel); sel.value = cur.rootId; }
    load();
  }

  /* ---------------- 初始化 ---------------- */
  function fillRoots(sel) {
    sel.innerHTML = ROOTS.map(function (r) {
      return '<option value="' + r.id + '">' +
        esc(t('ai.files.root.' + r.id, r.label)) + '</option>';
    }).join('');
  }

  function init() {
    var sel = document.getElementById('aiFilesRoot');
    var list = document.getElementById('aiFilesList');
    if (!sel || !list) return;
    fillRoots(sel);
    sel.addEventListener('change', function () {
      cur.rootId = sel.value;
      cur.filter = '';
      var f = document.getElementById('aiFilesFilter');
      if (f) f.value = '';
      load();
    });
    var filter = document.getElementById('aiFilesFilter');
    if (filter) {
      filter.addEventListener('input', function () {
        cur.filter = filter.value;
        load(cur.cwd);
      });
    }
    var up = document.getElementById('aiFilesUp');
    if (up) {
      up.addEventListener('click', function () {
        var base = String(cur.cwd || '').replace(/[\\/]+$/, '');
        var parent = base.replace(/[\\/][^\\/]+$/, '');
        if (parent && parent.length > 3) load(parent);
      });
    }
    var mask = document.getElementById('fsPreview');
    if (mask) {
      mask.addEventListener('click', function (e) {
        if (e.target === mask) close();
      });
      document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') close();
      });
    }
    // 样品切换后「当前样品」根要跟着变
    if (window.VP_CTX && typeof VP_CTX.onChange === 'function') {
      try { VP_CTX.onChange(function () { if (cur.rootId === 'sample') load(); }); } catch (e) {}
    }
    // 根目录以服务端为准：分发版把示例放在外置的 VirusPlatform-Examples 里
    fetch('/api/fs/roots')
      .then(function (r) { return r.json(); })
      .then(function (arr) {
        if (Array.isArray(arr) && arr.length) {
          ROOTS = arr.map(function (r) {
            return {id: r.id, label: r.label, base: r.path};
          });
          fillRoots(sel);
        }
      })
      .catch(function () {})
      .then(function () {
        // 工作空间：上次选过的目录优先显示
        var ws = '';
        try { ws = localStorage.getItem('vp_ai_ws') || ''; } catch (e) {}
        if (ws) {
          ROOTS.unshift({id: 'ws', label: '工作空间', base: ws});
          cur.rootId = 'ws';
          fillRoots(sel);
          sel.value = 'ws';
        }
        load();
      });
  }

  if (document.readyState === 'complete') init();
  else window.addEventListener('load', init);

  return {load: load, preview: preview, close: close,
          setRoot: setRoot, clearRoot: clearRoot, _roots: ROOTS};
})();
