/* 植物病毒分析平台 · 统一 Markdown 渲染器（离线、零依赖）
 *
 * 2026-11-02 重写（用户反馈：AI 输出的 ``` 代码块被当普通文字逐行拆开）。
 * 两个入口共用本文件：
 *   - AI 对话坞 / 事件卡：VPMD.render(src, {mode:'chat'})  （app-ai.js 调用）
 *   - 帮助手册 / Wiki：   VPMD.render(src)               （默认 mode:'doc'）
 *
 * 能力：
 *   ```/~~~ 围栏代码块（语言标签、复制、横向滚动、流式未闭合容错）
 *   行内代码（多反引号）、粗体/斜体/删除线、链接/图片安全白名单、裸 URL
 *   段落智能合并（中文之间不插空格，行尾两空格/反斜杠保留硬换行）
 *   无序/有序/嵌套/任务列表（有序列表保留起始号）
 *   表格（对齐、单元格换行、转义竖线、行内代码里的竖线不拆列）
 *   标题 h1-h6 / setext、引用（可嵌套）、分隔线
 *   doc 模式额外放行 details/summary/b/kbd/sub/sup/br，丢弃布局 div，
 *   badge 图片居中；chat 模式不解释原始 HTML（防注入）。
 *
 * 安全：所有文本先转义；只放行 http(s)、站内 /path、#锚点、mailto；
 *       javascript:/data:/vbscript:/file: 一律拒绝；原始 HTML 只走白名单。
 */
(function () {
  'use strict';

  if (window.VPMD && window.VPMD.version === '2') return;

  /* ---------- 基础工具 ---------- */

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function isEn() {
    const lang = (document.documentElement.getAttribute('lang') || '').toLowerCase();
    return lang.indexOf('en') === 0;
  }

  function tSafe(key, zh, en) {
    try {
      if (typeof window.t === 'function') return window.t(key, isEn() ? en : zh);
    } catch (e) { /* i18n 尚未就绪 */ }
    return isEn() ? en : zh;
  }

  function isBlank(s) { return !s || !/\S/.test(s); }

  const CJK = /[\u3400-\u9fff\uf900-\ufaff]/;
  const CJK_PUNCT = /[\u3000-\u303f\uff00-\uffef]/;

  /* 段落合并：中文/中文标点之间直接拼接；中英之间补一个空格；
   * 两个西文词之间补空格。 */
  function joinSep(prev, next) {
    if (!prev) return '';
    const a = prev.charAt(prev.length - 1);
    const b = next.charAt(0);
    if (CJK_PUNCT.test(a) || CJK_PUNCT.test(b)) return '';
    if (CJK.test(a) !== CJK.test(b)) return ' ';
    if (CJK.test(a) && CJK.test(b)) return '';
    return ' ';
  }

  function plainText(s) {
    return String(s)
      .replace(/`([^`]*)`/g, '$1')
      .replace(/\*\*([^*]*)\*\*/g, '$1')
      .replace(/[*_~]/g, '')
      .replace(/\s+/g, ' ')
      .trim();
  }

  function safeHref(u) {
    u = String(u || '').trim();
    if (!u) return '';
    if (/^(javascript|data|vbscript|file):/i.test(u)) return '';
    if (/^https?:\/\//i.test(u)) return u;
    if (/^mailto:[^\s]+$/i.test(u)) return u;
    if (u.charAt(0) === '#' && u.length > 1) return u;
    if (u.charAt(0) === '/' && u.charAt(1) !== '/' && u.charAt(1) !== '\\') return u;
    return '';
  }

  function safeImg(u) {
    u = String(u || '').trim();
    if (!u) return '';
    if (/^(javascript|data|vbscript|file):/i.test(u)) return '';
    if (/^https?:\/\//i.test(u)) return u;
    if (/^\/(?:static|api)\//i.test(u)) return u;
    return '';
  }

  /* [ ] 配对（允许嵌套、反斜杠转义） */
  function readBracket(s, i) {
    let depth = 0;
    for (let j = i; j < s.length; j++) {
      const c = s.charAt(j);
      if (c === '\\') { j++; continue; }
      if (c === '[') depth++;
      else if (c === ']') {
        depth--;
        if (depth === 0) return j;
      }
    }
    return -1;
  }

  /* ( ) 配对（链接目标里可能有括号） */
  function readParen(s, i) {
    let depth = 0;
    for (let j = i; j < s.length; j++) {
      const c = s.charAt(j);
      if (c === '\\') { j++; continue; }
      if (c === '(') depth++;
      else if (c === ')') {
        depth--;
        if (depth === 0) return j;
      }
    }
    return -1;
  }

  function parseDest(dest) {
    dest = String(dest || '').trim();
    if (/^<[^>]*>$/.test(dest)) dest = dest.slice(1, -1);
    let url = dest;
    let title = '';
    const sp = dest.search(/\s/);
    if (sp >= 0) {
      url = dest.slice(0, sp);
      title = dest.slice(sp).trim().replace(/^["'(]+/, '').replace(/["')]+$/, '');
    }
    return { url: url, title: title };
  }

  function findClose(s, from, marker) {
    let j = from;
    while ((j = s.indexOf(marker, j)) >= 0) {
      if (j > 0 && s.charAt(j - 1) === '\\') { j += marker.length; continue; }
      return j;
    }
    return -1;
  }

  /* ---------- 行内解析 ---------- */

  const RAW_INLINE = { b: 'b', strong: 'b', i: 'em', em: 'em', kbd: 'kbd',
                       sub: 'sub', sup: 'sup', code: 'code', br: 'br' };

  function readTag(raw, i) {
    const rest = raw.slice(i);
    const m = /^<\/?([A-Za-z][\w-]*)((?:\s[^<>]*)?)\s*\/?>/.exec(rest);
    if (!m) {
      const auto = /^<((?:https?:\/\/|mailto:)[^<>\s]+)>/.exec(rest);
      if (auto) return { kind: 'auto', url: auto[1], end: i + auto[0].length };
      return null;
    }
    const name = m[1].toLowerCase();
    const closing = m[0].charAt(1) === '/';
    return { kind: 'tag', name: name, closing: closing,
             attrs: m[2] || '', end: i + m[0].length, raw: m[0] };
  }

  function inline(raw, st, depth) {
    raw = String(raw == null ? '' : raw);
    if (depth > 8) return esc(raw);
    st = st || { mode: 'doc' };
    let out = '';
    let i = 0;
    const n = raw.length;
    while (i < n) {
      const c = raw.charAt(i);

      /* 反斜杠转义 */
      if (c === '\\') {
        const nx = raw.charAt(i + 1);
        if (nx && '\\`*_{}[]()#+-.!|~<>'.indexOf(nx) >= 0) {
          out += esc(nx); i += 2; continue;
        }
        out += '\\'; i++; continue;
      }

      /* 硬换行（段落收集时写入的哨兵） */
      if (c === '\n') { out += '<br>'; i++; continue; }

      /* 行内代码：支持多反引号 */
      if (c === '`') {
        let run = 1;
        while (raw.charAt(i + run) === '`') run++;
        const fence = new Array(run + 1).join('`');
        const end = raw.indexOf(fence, i + run);
        if (end >= 0) {
          let code = raw.slice(i + run, end);
          if (/^ .* $/.test(code) && code.trim() !== '') code = code.slice(1, -1);
          out += '<code class="md-icode">' + esc(code) + '</code>';
          i = end + run; continue;
        }
        out += esc(fence); i += run; continue;
      }

      /* 图片 */
      if (c === '!' && raw.charAt(i + 1) === '[') {
        const close = readBracket(raw, i + 1);
        if (close > 0 && raw.charAt(close + 1) === '(') {
          const pEnd = readParen(raw, close + 1);
          if (pEnd > 0) {
            const alt = raw.slice(i + 2, close);
            const d = parseDest(raw.slice(close + 2, pEnd));
            const url = safeImg(d.url);
            if (url) {
              out += '<img class="md-img" src="' + esc(url) + '" alt="' +
                esc(plainText(alt)) + '" loading="lazy" decoding="async">';
            } else {
              out += esc(plainText(alt));
            }
            i = pEnd + 1; continue;
          }
        }
      }

      /* 链接 */
      if (c === '[') {
        const close = readBracket(raw, i);
        if (close > 0 && raw.charAt(close + 1) === '(') {
          const pEnd = readParen(raw, close + 1);
          if (pEnd > 0) {
            const txt = raw.slice(i + 1, close);
            const d = parseDest(raw.slice(close + 2, pEnd));
            const href = safeHref(d.url);
            if (href) {
              const title = d.title ? ' title="' + esc(d.title) + '"' : '';
              out += '<a class="md-link" href="' + esc(href) +
                '" target="_blank" rel="noopener noreferrer"' + title + '>' +
                inline(txt, st, depth + 1) + '</a>';
            } else {
              out += inline(txt, st, depth + 1);
            }
            i = pEnd + 1; continue;
          }
        }
      }

      /* 原始 HTML：chat 模式一律转义；doc 模式走白名单 */
      if (c === '<') {
        const tag = readTag(raw, i);
        if (tag) {
          if (tag.kind === 'auto') {
            const href = safeHref(tag.url);
            if (href) {
              out += '<a class="md-link" href="' + esc(href) +
                '" target="_blank" rel="noopener noreferrer">' +
                esc(tag.url) + '</a>';
              i = tag.end; continue;
            }
          } else if (st.mode === 'doc' && RAW_INLINE[tag.name] &&
                     (!tag.attrs || !tag.attrs.trim())) {
            out += '<' + (tag.closing ? '/' : '') + RAW_INLINE[tag.name] + '>';
            i = tag.end; continue;
          } else if (st.mode === 'doc' && tag.name === 'br' && !tag.attrs.trim()) {
            out += '<br>'; i = tag.end; continue;
          }
        }
        out += '&lt;'; i++; continue;
      }

      /* 强调：**粗体** / *斜体* / __ / _ / ~~删除线~~ */
      if (c === '*' || c === '_') {
        const run = raw.charAt(i + 1) === c ? 2 : 1;
        if (run === 2) {
          const end = findClose(raw, i + 2, c + c);
          if (end > i + 2 && raw.charAt(end - 1) !== ' ' &&
              !(c === '_' && /[A-Za-z0-9]/.test(raw.charAt(i - 1)))) {
            out += '<strong>' + inline(raw.slice(i + 2, end), st, depth + 1) +
              '</strong>';
            i = end + 2; continue;
          }
        }
        if (raw.charAt(i + 1) !== c && raw.charAt(i + 1) !== ' ' &&
            !(c === '_' && /[A-Za-z0-9]/.test(raw.charAt(i - 1)))) {
          const end = findClose(raw, i + 1, c);
          if (end > i + 1 && raw.charAt(end - 1) !== ' ' &&
              !(c === '_' && /[A-Za-z0-9]/.test(raw.charAt(end + 1)))) {
            out += '<em>' + inline(raw.slice(i + 1, end), st, depth + 1) + '</em>';
            i = end + 1; continue;
          }
        }
        out += esc(c); i++; continue;
      }

      /* 删除线 */
      if (c === '~' && raw.charAt(i + 1) === '~' && raw.charAt(i + 2) !== '~') {
        const end = findClose(raw, i + 2, '~~');
        if (end > i + 2 && raw.charAt(end - 1) !== ' ') {
          out += '<del>' + inline(raw.slice(i + 2, end), st, depth + 1) + '</del>';
          i = end + 2; continue;
        }
      }

      /* 裸 URL 自动链接 */
      if (c === 'h' || c === 'H') {
        const m = /^(https?:\/\/[^\s<>"'`]+)/i.exec(raw.slice(i));
        if (m) {
          let url = m[1];
          while (/[.,;:!?]$/.test(url)) url = url.slice(0, -1);
          while (url.charAt(url.length - 1) === ')' &&
                 (url.match(/\(/g) || []).length < (url.match(/\)/g) || []).length) {
            url = url.slice(0, -1);
          }
          if (url.length > 8) {
            out += '<a class="md-link" href="' + esc(url) +
              '" target="_blank" rel="noopener noreferrer">' + esc(url) + '</a>';
            i += url.length; continue;
          }
        }
      }

      out += esc(c); i++;
    }
    return out;
  }

  /* ---------- 表格 ---------- */

  function splitRow(line) {
    let s = String(line).trim();
    if (s.charAt(0) === '|') s = s.slice(1);
    if (s.charAt(s.length - 1) === '|' && s.charAt(s.length - 2) !== '\\') {
      s = s.slice(0, -1);
    }
    const cells = [];
    let cur = '';
    let tick = 0;
    for (let i = 0; i < s.length; i++) {
      const ch = s.charAt(i);
      if (ch === '\\' && s.charAt(i + 1) === '|') { cur += '|'; i++; continue; }
      if (ch === '`') {
        let run = 1;
        while (s.charAt(i + run) === '`') run++;
        if (tick === 0) tick = run;
        else if (tick === run) tick = 0;
        cur += new Array(run + 1).join('`');
        i += run - 1;
        continue;
      }
      if (ch === '|' && tick === 0) { cells.push(cur.trim()); cur = ''; continue; }
      cur += ch;
    }
    cells.push(cur.trim());
    return cells;
  }

  function isSepRow(line) {
    if (String(line).indexOf('-') < 0) return false;
    const cells = splitRow(line);
    if (!cells.length) return false;
    for (let i = 0; i < cells.length; i++) {
      if (!/^:?-+:?$/.test(cells[i])) return false;
    }
    return true;
  }

  function isTableStart(lines, i) {
    if (i + 1 >= lines.length) return false;
    if (lines[i].indexOf('|') < 0) return false;
    if (lines[i + 1].indexOf('|') < 0 || lines[i + 1].indexOf('-') < 0) return false;
    if (splitRow(lines[i]).length < 2) return false;
    return isSepRow(lines[i + 1]);
  }

  function renderTable(lines, i, st) {
    const head = splitRow(lines[i]);
    const seps = splitRow(lines[i + 1]);
    const align = [];
    for (let k = 0; k < head.length; k++) {
      const s = (seps[k] || '').trim();
      if (/^:-+:$/.test(s)) align.push('md-ta-c');
      else if (/-+:$/.test(s)) align.push('md-ta-r');
      else align.push('');
    }
    let j = i + 2;
    const rows = [];
    while (j < lines.length && !isBlank(lines[j]) &&
           lines[j].indexOf('|') >= 0 && !isFenceStart(lines[j]) &&
           !isHeadingStart(lines[j])) {
      rows.push(splitRow(lines[j]));
      j++;
    }
    const cell = function (txt, tag, k) {
      const cls = align[k] ? ' class="' + align[k] + '"' : '';
      const body = tag === 'th' ? inline(txt, st, 1) : inline(txt, st, 1);
      return '<' + tag + cls + '>' + body + '</' + tag + '>';
    };
    let html = '<div class="md-table-wrap"><table class="md-table"><thead><tr>';
    html += head.map(function (c, k) { return cell(c, 'th', k); }).join('');
    html += '</tr></thead><tbody>';
    rows.forEach(function (r) {
      const cells = [];
      for (let k = 0; k < head.length; k++) cells.push(cell(r[k] || '', 'td', k));
      html += '<tr>' + cells.join('') + '</tr>';
    });
    html += '</tbody></table></div>';
    return { html: html, next: j };
  }

  /* ---------- 代码高亮（轻量，只为可读性；不追求完整语法） ---------- */

  function hlRules(code, rules) {
    let out = '';
    let last = 0;
    let guard = 0;
    while (guard++ < 20000) {
      let best = null;
      for (let k = 0; k < rules.length; k++) {
        const r = rules[k];
        r.re.lastIndex = last;
        const m = r.re.exec(code);
        if (m && (!best || m.index < best.index)) {
          best = { m: m, cls: r.cls, re: r.re };
        }
      }
      if (!best || best.m.index < last) break;
      out += esc(code.slice(last, best.m.index));
      out += '<span class="md-tok ' + best.cls + '">' + esc(best.m[0]) + '</span>';
      last = best.m.index + best.m[0].length;
      if (!best.m[0].length) last++;
    }
    return out + esc(code.slice(last));
  }

  const KEYWORDS = {
    py: 'and|as|assert|async|await|break|class|continue|def|del|elif|else|except|finally|for|from|global|if|import|in|is|lambda|None|nonlocal|not|or|pass|raise|return|True|False|try|while|with|yield',
    r: 'if|else|repeat|while|function|for|in|next|break|TRUE|FALSE|NULL|NA|Inf|NaN|library|require|return',
    js: 'var|let|const|function|return|if|else|for|while|do|switch|case|break|continue|new|class|extends|try|catch|finally|throw|async|await|import|export|from|of|in|typeof|instanceof|this|null|undefined|true|false',
    sh: 'if|then|else|elif|fi|for|in|do|done|while|case|esac|function|export|local|return|echo|cd|set|source'
  };

  function highlight(code, lang) {
    lang = String(lang || '').toLowerCase();
    if (lang === 'json' || lang === 'jsonc') {
      const re = /"(?:\\.|[^"\\])*"|-?\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b|\b(?:true|false|null)\b/g;
      let out = '', last = 0, m;
      while ((m = re.exec(code))) {
        out += esc(code.slice(last, m.index));
        const tok = m[0];
        let cls = 'md-tok-num';
        if (tok.charAt(0) === '"') {
          cls = /^\s*:/.test(code.slice(re.lastIndex)) ? 'md-tok-key' : 'md-tok-str';
        } else if (/^(true|false|null)$/.test(tok)) {
          cls = 'md-tok-kw';
        }
        out += '<span class="md-tok ' + cls + '">' + esc(tok) + '</span>';
        last = re.lastIndex;
      }
      return out + esc(code.slice(last));
    }
    if (lang === 'py' || lang === 'python' || lang === 'r' || lang === 'js' ||
        lang === 'javascript' || lang === 'ts' || lang === 'typescript') {
      const key = lang === 'r' ? 'r' : (lang === 'py' || lang === 'python' ? 'py' : 'js');
      return hlRules(code, [
        { re: /#[^\n]*/g, cls: 'md-tok-com' },
        { re: /'''[\s\S]*?'''|"""[\s\S]*?"""|'(?:\\.|[^'\\\n])*'|"(?:\\.|[^"\\\n])*"/g, cls: 'md-tok-str' },
        { re: new RegExp('\\b(?:' + KEYWORDS[key] + ')\\b', 'g'), cls: 'md-tok-kw' },
        { re: /\b\d+(?:\.\d+)?\b/g, cls: 'md-tok-num' }
      ]);
    }
    if (lang === 'sh' || lang === 'bash' || lang === 'shell' || lang === 'zsh' ||
        lang === 'console' || lang === 'cmd' || lang === 'bat' ||
        lang === 'powershell' || lang === 'ps1') {
      return hlRules(code, [
        { re: /(?:^|\s)(#|::|REM\b)[^\n]*/gi, cls: 'md-tok-com' },
        { re: /"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*'/g, cls: 'md-tok-str' },
        { re: /(?:^|\s)--?[A-Za-z][\w-]*/g, cls: 'md-tok-opt' },
        { re: new RegExp('\\b(?:' + KEYWORDS.sh + ')\\b', 'g'), cls: 'md-tok-kw' }
      ]);
    }
    if (lang === 'yaml' || lang === 'yml') {
      return hlRules(code, [
        { re: /#[^\n]*/g, cls: 'md-tok-com' },
        { re: /^[ \t-]*[\w.-]+(?=\s*:)/gm, cls: 'md-tok-key' },
        { re: /"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*'/g, cls: 'md-tok-str' },
        { re: /\b(?:true|false|null|yes|no)\b/gi, cls: 'md-tok-kw' },
        { re: /\b\d+(?:\.\d+)?\b/g, cls: 'md-tok-num' }
      ]);
    }
    if (lang === 'fasta' || lang === 'fa' || lang === 'fna') {
      return hlRules(code, [
        { re: /^>.*$/gm, cls: 'md-tok-key' }
      ]);
    }
    return esc(code);
  }

  /* ---------- 代码块 ---------- */

  function codeBlock(code, lang, st) {
    const clean = String(lang || '').toLowerCase().replace(/[^a-z0-9+#._-]/g, '');
    const label = clean || tSafe('md.code', '代码', 'code');
    const copy = tSafe('md.copy', '复制', 'Copy');
    const copyTitle = tSafe('md.copyCode', '复制代码', 'Copy code');
    return '<div class="md-code md-code-' + st.mode + '">' +
      '<div class="md-code-head"><span class="md-code-lang">' + esc(label) +
      '</span><button type="button" class="md-code-copy" data-vp-copy="code" ' +
      'title="' + esc(copyTitle) + '" aria-label="' + esc(copyTitle) + '">' +
      esc(copy) + '</button></div>' +
      '<pre class="md-pre"><code class="md-code-body">' +
      highlight(code, clean) + '</code></pre></div>';
  }

  /* ---------- 块级判断 ---------- */

  function isFenceStart(line) {
    const m = /^ {0,3}(`{3,}|~{3,})(.*)$/.exec(line);
    if (!m) return false;
    if (m[1].charAt(0) === '`' && m[2].indexOf('`') >= 0) return false;
    return true;
  }

  function fenceInfo(line) {
    const m = /^ {0,3}(`{3,}|~{3,})(.*)$/.exec(line);
    if (!m) return null;
    return { ch: m[1].charAt(0), len: m[1].length, info: m[2].trim() };
  }

  function isFenceClose(line, ch, len) {
    const m = /^ {0,3}(`{3,}|~{3,})\s*$/.exec(line);
    return !!m && m[1].charAt(0) === ch && m[1].length >= len;
  }

  function isHeadingStart(line) { return /^ {0,3}#{1,6}(?:\s|$)/.test(line); }

  function isHrStart(line) {
    return /^ {0,3}(?:(?:\*\s*){3,}|(?:-\s*){3,}|(?:_\s*){3,})$/.test(line);
  }

  function isQuoteStart(line) { return /^ {0,3}>/.test(line); }

  function listItem(line) {
    const m = /^([ \t]*)([-+*]|\d{1,9}[.)])([ \t]+)([\s\S]*)$/.exec(line);
    if (!m) return null;
    const indent = m[1].replace(/\t/g, '    ').length;
    const marker = m[2];
    return {
      indent: indent,
      ordered: /\d/.test(marker),
      num: parseInt(marker, 10) || 1,
      markerLen: marker.length,
      spaces: m[3].replace(/\t/g, '    ').length,
      rest: m[4],
      line: line
    };
  }

  function isListStart(line) {
    const it = listItem(line);
    return !!it && it.indent <= 3;
  }

  function isRawDocStart(line) {
    return /^\s*<\/?(?:div|details|summary)\b/i.test(line) ||
           /^\s*<br\s*\/?>\s*$/i.test(line) ||
           /^\s*<a\s+[^>]*id\s*=\s*["'][^"']+["'][^>]*>\s*<\/a>\s*$/i.test(line);
  }

  function isBlockStart(lines, i, st) {
    const line = lines[i];
    return isFenceStart(line) || isHeadingStart(line) || isHrStart(line) ||
      isQuoteStart(line) || isListStart(line) || isTableStart(lines, i) ||
      (st.mode === 'doc' && isRawDocStart(line));
  }

  /* ---------- 列表 ---------- */

  function parseList(lines, i, st) {
    const first = listItem(lines[i]);
    const ordered = first.ordered;
    const base = first.indent;
    const items = [];
    let loose = false;

    while (i < lines.length) {
      const m = listItem(lines[i]);
      if (!m || m.indent !== base || m.ordered !== ordered) break;
      const contentIndent = base + m.markerLen + 1;
      const itemLines = [m.rest];
      i++;
      let itemLoose = false;

      while (i < lines.length) {
        const L = lines[i];
        if (isBlank(L)) {
          const nxt = i + 1 < lines.length ? lines[i + 1] : '';
          const nm = listItem(nxt);
          const nIndent = (nxt.match(/^[ \t]*/) || [''])[0].replace(/\t/g, '    ').length;
          if (nm && nm.indent === base && nm.ordered === ordered) { itemLoose = true; i++; break; }
          if (nxt && nIndent >= contentIndent) {
            itemLines.push(''); itemLoose = true; i++; continue;
          }
          break;
        }
        const nm = listItem(L);
        if (nm && nm.indent === base && nm.ordered === ordered) break;
        if (nm && nm.indent < base) break;
        const ind = (L.match(/^[ \t]*/) || [''])[0].replace(/\t/g, '    ').length;
        if (ind >= contentIndent) {
          itemLines.push(L.slice(Math.min(ind, contentIndent)));
          i++; continue;
        }
        if (ind > base && !isBlockStart(lines, i, st)) {
          itemLines.push(L.slice(base));
          i++; continue;
        }
        /* 懒续行：紧跟在项目文字后的普通行 */
        if (!isBlockStart(lines, i, st) && itemLines.length &&
            itemLines[itemLines.length - 1] !== '') {
          itemLines.push(L.trim());
          i++; continue;
        }
        break;
      }

      while (itemLines.length && isBlank(itemLines[itemLines.length - 1])) itemLines.pop();
      let task = null;
      if (itemLines.length) {
        const tm = /^\[([ xX])\]\s+([\s\S]*)$/.exec(itemLines[0]);
        if (tm) { task = tm[1].toLowerCase() === 'x'; itemLines[0] = tm[2]; }
      }
      let html = renderBlocks(itemLines, st);
      if (!itemLoose) {
        const single = /^<p class="md-p">([\s\S]*)<\/p>$/.exec(html);
        if (single && single[1].indexOf('<p class="md-p">') < 0) html = single[1];
      }
      items.push({ html: html, task: task });
      loose = loose || itemLoose;
    }

    const tag = ordered ? 'ol' : 'ul';
    const start = (ordered && first.num !== 1) ? ' start="' + first.num + '"' : '';
    const body = items.map(function (it) {
      const cls = it.task !== null ? ' class="md-task' + (it.task ? ' md-task-done' : '') + '"' : '';
      const box = it.task !== null
        ? '<input type="checkbox" disabled' + (it.task ? ' checked' : '') + '> '
        : '';
      return '<li' + cls + '>' + box + it.html + '</li>';
    }).join('');
    return {
      html: '<' + tag + ' class="md-list' + (loose ? ' md-loose' : '') + '"' +
        start + '>' + body + '</' + tag + '>',
      next: i
    };
  }

  /* ---------- 段落 ---------- */

  function renderParagraph(lines, i, st) {
    let text = '';
    let prev = '';
    let hard = false;
    while (i < lines.length && !isBlank(lines[i]) && !isBlockStart(lines, i, st)) {
      let line = lines[i];
      const isHard = / {2,}$/.test(line) || /\\$/.test(line);
      if (isHard) line = line.replace(/ {2,}$/, '').replace(/\\$/, '');
      if (text) {
        text += hard ? '\n' : joinSep(prev, line);
      }
      text += line;
      prev = line;
      hard = isHard;
      i++;
    }
    const onlyImage = /^\s*!\[[^\]]*\]\([^)]*\)\s*$/.test(text);
    const cls = onlyImage ? 'md-p md-figure' : 'md-p';
    return { html: '<p class="' + cls + '">' + inline(text, st, 1) + '</p>', next: i };
  }

  /* ---------- 文档模式的原始 HTML 白名单 ---------- */

  function renderRawDoc(line) {
    const s = line.trim();
    if (/^<div\b[^>]*>$/i.test(s) || /^<\/div>$/i.test(s)) return '';
    if (/^<br\s*\/?>$/i.test(s)) return '<br>';
    if (/^<details\b/i.test(s)) {
      const open = /\sopen(?:\s|=|>|\/)/i.test(s) ? ' open' : '';
      return '<details class="md-details"' + open + '>';
    }
    if (/^<\/details>$/i.test(s)) return '</details>';
    const sum = /^<summary>([\s\S]*)<\/summary>$/i.exec(s);
    if (sum) return '<summary>' + inline(sum[1], { mode: 'doc' }, 1) + '</summary>';
    const anchor = /^<a\s+[^>]*id\s*=\s*["']([\w:.-]+)["'][^>]*>\s*<\/a>$/i.exec(s);
    if (anchor) return '<span class="md-anchor" id="' + esc(anchor[1]) + '"></span>';
    return esc(s);
  }

  /* ---------- 块级渲染 ---------- */

  function renderBlocks(lines, st) {
    const out = [];
    let i = 0;
    while (i < lines.length) {
      const line = lines[i];
      if (isBlank(line)) { i++; continue; }

      /* 文档模式：布局 div / details / summary / 锚点 */
      if (st.mode === 'doc' && isRawDocStart(line)) {
        out.push(renderRawDoc(line));
        i++;
        continue;
      }

      /* 围栏代码块 */
      if (isFenceStart(line)) {
        const info = fenceInfo(line);
        const lang = info ? info.info.split(/\s+/)[0] : '';
        let j = i + 1;
        while (j < lines.length && !isFenceClose(lines[j], info.ch, info.len)) j++;
        const code = lines.slice(i + 1, j).join('\n');
        out.push(codeBlock(code, lang, st));
        i = j < lines.length ? j + 1 : j;
        continue;
      }

      /* 标题 */
      let m = /^ {0,3}(#{1,6})(?:\s+|$)(.*)$/.exec(line);
      if (m) {
        const level = m[1].length;
        const txt = m[2].replace(/\s+#+\s*$/, '').trim();
        out.push('<h' + level + ' class="md-h md-h' + level + '">' +
          inline(txt, st, 1) + '</h' + level + '>');
        i++;
        continue;
      }

      /* setext 标题：文字下一行 === / --- */
      if (i + 1 < lines.length && /^ {0,3}=+\s*$/.test(lines[i + 1]) &&
          !isBlockStart(lines, i, st)) {
        out.push('<h1 class="md-h md-h1">' + inline(line.trim(), st, 1) + '</h1>');
        i += 2; continue;
      }
      if (i + 1 < lines.length && /^ {0,3}-+\s*$/.test(lines[i + 1]) &&
          !isBlockStart(lines, i, st)) {
        out.push('<h2 class="md-h md-h2">' + inline(line.trim(), st, 1) + '</h2>');
        i += 2; continue;
      }

      /* 分隔线 */
      if (isHrStart(line)) {
        out.push('<hr class="md-hr">');
        i++;
        continue;
      }

      /* 引用（可嵌套） */
      if (isQuoteStart(line)) {
        const buf = [];
        while (i < lines.length && isQuoteStart(lines[i])) {
          buf.push(lines[i].replace(/^ {0,3}>\s?/, ''));
          i++;
        }
        out.push('<blockquote class="md-quote">' + renderBlocks(buf, st) +
          '</blockquote>');
        continue;
      }

      /* 表格 */
      if (isTableStart(lines, i)) {
        const t = renderTable(lines, i, st);
        out.push(t.html);
        i = t.next;
        continue;
      }

      /* 列表 */
      const it = listItem(line);
      if (it && it.indent <= 3) {
        const l = parseList(lines, i, st);
        out.push(l.html);
        i = l.next;
        continue;
      }

      /* 段落 */
      const p = renderParagraph(lines, i, st);
      out.push(p.html);
      i = p.next;
    }
    return out.join('');
  }

  /* ---------- 复制 ---------- */

  function copyText(text, cb) {
    function fallback() {
      let ok = false;
      try {
        const ta = document.createElement('textarea');
        ta.value = text;
        ta.setAttribute('readonly', '');
        ta.style.position = 'fixed';
        ta.style.top = '-2000px';
        ta.style.left = '-2000px';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.select();
        ta.setSelectionRange(0, ta.value.length);
        ok = document.execCommand('copy');
        document.body.removeChild(ta);
      } catch (e) { ok = false; }
      if (cb) cb(ok);
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(String(text)).then(function () {
        if (cb) cb(true);
      }, fallback);
    } else {
      fallback();
    }
  }

  if (!window.__vpMdCopyBound) {
    window.__vpMdCopyBound = true;
    document.addEventListener('click', function (e) {
      const btn = e.target && e.target.closest
        ? e.target.closest('[data-vp-copy]') : null;
      if (!btn) return;
      e.preventDefault();
      e.stopPropagation();
      const box = btn.closest('.md-code') || btn.closest('.ai-msg');
      const pre = box ? box.querySelector('pre') : null;
      const text = pre ? pre.innerText : (box ? box.innerText : '');
      const old = btn.getAttribute('data-label') || btn.textContent;
      btn.setAttribute('data-label', old);
      copyText(text, function (ok) {
        btn.textContent = ok ? tSafe('md.copied', '已复制', 'Copied')
                             : tSafe('md.copyFail', '复制失败', 'Copy failed');
        btn.classList.toggle('ok', !!ok);
        clearTimeout(btn.__vpT);
        btn.__vpT = setTimeout(function () {
          btn.textContent = old;
          btn.classList.remove('ok');
        }, 1400);
      });
    });
  }

  /* ---------- 对外 API ---------- */

  function render(src, opts) {
    const st = { mode: (opts && opts.mode) === 'chat' ? 'chat' : 'doc' };
    let text = String(src == null ? '' : src).replace(/\r\n?/g, '\n');
    /* 流式输出时末尾可能只有一个未闭合围栏，交给 renderBlocks 容错 */
    if (text.charAt(text.length - 1) === '\n') text = text.slice(0, -1);
    return renderBlocks(text.split('\n'), st);
  }

  window.VPMD = {
    version: '2',
    esc: esc,
    inline: function (s, opts) {
      const st = { mode: (opts && opts.mode) === 'chat' ? 'chat' : 'doc' };
      return inline(String(s == null ? '' : s), st, 1);
    },
    render: render,
    copy: copyText
  };
})();
