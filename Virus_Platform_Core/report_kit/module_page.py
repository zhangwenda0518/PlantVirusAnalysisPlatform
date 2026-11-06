# -*- coding: utf-8 -*-
"""module_page — 每个模块（阶段/工具运行）独立的结果整理页 results.html。

把一个产物目录（流水线模块目录）里的全部产物整理成一份纯相对路径引用的
HTML：输入 → 图表 → 表格 → 数据/序列 → 交互页 → 各类文件，六区结构；
图配图例与读法（figdocs 图鉴），文件全部超链接，支持预览（点击放大、
表格内嵌、交互页 iframe 懒加载）。

三种打开上下文全部成立：
1. 离线双击（file://，相对路径直接成立）；
2. Web 服务（/api/samples/<样品>/<阶段目录>/results.html 与
   /tool_runs/<run>/results.html，两个路由都服务目录内任意文件）；
3. AI 对话坞产物卡片 iframe（/api/fs/raw?path=…，页内 JS 检测后把
   data-rel 引用改写为绝对路径 URL，三种上下文全通）。

入口：
    build_stage_page(sample_dir, stage=None)   # 单个/全部阶段目录页
    build_all_for_sample(sample_dir)           # 一次生成全部阶段页
    build_tool_run_page(run_dir)               # 工具运行页
    main(argv)                                 # CLI：--sample/--run/--dir

纪律：
- 只写"目录里真实存在的文件"，未登记读法的图退化成"文件名当标题"；
- 单区失败只降级一句提示，不拖垮整页；
- 原子写（tmp + os.replace）。
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import time
import glob as _glob

from .figdocs import (STAGE_INPUTS, STAGE_PURPOSE, ext_desc, figure_doc,
                      interactive_doc, tool_info)

#: 结果页文件名（每模块一份，与 report.html / report_v2.html 并存不冲突）
OUT_NAME = 'results.html'

#: 跳过的文件（自身、外部 js、内部输入元数据）
_SKIP_FILES = {OUT_NAME, 'plotly.min.js', 'run_meta.json'}
#: 跳过的目录（临时/日志/工作目录）
_SKIP_DIRS = {'logs', '_tmp', '_scripts', '_reports', '_work', '.git',
              '__pycache__', '.resume_cache'}
#: 膨胀文件后缀（临时与断点标记）
_SKIP_SUFFIX = ('.tmp', '.done', '.part')

# 防超大目录撑爆页面；超出时页面注明"仅显示前 N 个"
MAX_FIGS = 120
MAX_TABLES = 80
MAX_DATA = 60
MAX_LINKS = 240
TABLE_ROWS = 30
TABLE_COLS = 24
TABLE_CELL = 80
TEXT_PREVIEW_LINES = 40
MAX_JSON_LINES = 120

# 序列/文本类扩展（折叠预览线数上限见 TEXT_PREVIEW_LINES）
_TEXT_EXTS = ('.fasta', '.fna', '.faa', '.ffn', '.nwk', '.nhx', '.aln',
              '.gff', '.gff3', '.gff2', '.vcf', '.bed', '.m8')

# ==================================================================
# 页面样式（自包含，无外部依赖）
# ==================================================================

_CSS = """
:root{--green:#17513c;--blue:#23415c;--ink:#1c2430;--mut:#6b7683;
--line:#dfe3e8;--bg:#f4f5f3;--card:#fff}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.65 "Microsoft YaHei","PingFang SC","Segoe UI",Arial,sans-serif}
a{color:var(--blue);text-decoration:none}
a:hover{text-decoration:underline}
.mp-top{background:linear-gradient(135deg,#0f3d2e,#17513c 60%,#23415c);
color:#fff;padding:26px 28px 18px}
.mp-top h1{margin:6px 0 4px;font-size:24px}
.mp-kicker{font-size:12px;letter-spacing:2px;opacity:.75;text-transform:uppercase}
.mp-purpose{margin:4px 0 10px;opacity:.92;max-width:920px}
.mp-meta{font-size:12.5px;opacity:.8;display:flex;gap:22px;flex-wrap:wrap}
.mp-nav{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}
.mp-nav a{color:#fff;background:rgba(255,255,255,.14);border:1px solid
rgba(255,255,255,.28);padding:3px 11px;border-radius:999px;font-size:12.5px}
.mp-nav a:hover{background:rgba(255,255,255,.26);text-decoration:none}
.mp-nav a.self{background:rgba(255,255,255,.34);font-weight:600}
.mp-nav a.ghost{opacity:.55}
main{max-width:1280px;margin:0 auto;padding:18px 20px 40px}
section{margin:20px 0}
section>h2{font-size:18px;margin:0 0 10px;color:var(--green);
border-left:4px solid var(--green);padding-left:10px}
section>h2 small{color:var(--mut);font-weight:400;margin-left:8px}
.mp-card{background:var(--card);border:1px solid var(--line);
border-radius:10px;padding:14px 16px;margin:10px 0;
box-shadow:0 1px 3px rgba(20,40,30,.06)}
.mp-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(430px,1fr));gap:14px}
.mp-fig{background:#fff;border:1px solid var(--line);border-radius:10px;
padding:10px 12px;margin:0;overflow:hidden}
.mp-imgbox{background:#fbfbfa;border:1px solid var(--line);border-radius:6px;
padding:6px;text-align:center;cursor:zoom-in;min-height:120px;
display:flex;align-items:center;justify-content:center}
.mp-imgbox img{max-width:100%;height:auto}
.mp-fig figcaption{margin-top:8px;font-size:13px}
.mp-fig figcaption b{font-size:14px;color:var(--green)}
.mp-fig figcaption .mp-read{display:block;color:var(--mut);font-size:12.5px;margin-top:3px}
.mp-figbar{display:flex;gap:6px;align-items:center;margin-top:8px;flex-wrap:wrap}
.mp-fmt,.mp-act{font-size:12px;border:1px solid var(--line);background:#fff;
color:var(--ink);padding:2px 10px;border-radius:999px;cursor:pointer}
.mp-fmt.active{background:var(--green);color:#fff;border-color:var(--green)}
.mp-act:hover,.mp-fmt:hover{border-color:var(--green)}
.mp-path{font-size:11.5px;color:var(--mut);margin-left:auto;
white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:46%}
.mp-sub{margin:14px 0 6px;font-size:14px;color:var(--blue);font-weight:600}
table.mp-tbl{border-collapse:collapse;width:100%;font-size:12.5px;background:#fff}
table.mp-tbl th,table.mp-tbl td{border:1px solid var(--line);padding:4px 8px;
text-align:left;max-width:340px;overflow:hidden;text-overflow:ellipsis;
white-space:nowrap}
table.mp-tbl th{background:#eef4f0;color:var(--green);position:sticky;top:0}
.mp-tbl-wrap{overflow:auto;max-height:520px;border:1px solid var(--line);border-radius:8px}
.mp-chip{display:inline-block;background:#fff;border:1px solid var(--line);
border-radius:999px;padding:3px 12px;margin:3px 6px 3px 0;font-size:12.5px}
.mp-chip:hover{border-color:var(--green);text-decoration:none}
.mp-chip .mp-size{color:var(--mut);font-size:11px;margin-left:6px}
.mp-note{color:var(--mut);font-size:13px}
.mp-bad{background:#fdf1f0;border:1px solid #e6b8b3;color:#8a2c22;
border-radius:8px;padding:10px 14px;margin:10px 0;font-size:13px}
details.mp-prev{margin:8px 0 0}
details.mp-prev summary{cursor:pointer;color:var(--blue);font-size:12.5px}
details.mp-prev pre{background:#f7f7f5;border:1px solid var(--line);
border-radius:8px;padding:10px;font-size:12px;line-height:1.5;
max-height:360px;overflow:auto;white-space:pre;word-break:break-all}
.mp-load iframe{width:100%;height:560px;border:1px solid var(--line);
border-radius:8px;margin-top:8px;background:#fff}
/* ---- 统一文件预览弹层：默认本页预览，不跳转；弹层头再给跳转/下载 ---- */
#mp-preview{position:fixed;inset:0;background:rgba(12,18,14,.72);display:none;
z-index:98;align-items:center;justify-content:center;padding:18px}
#mp-preview.show{display:flex}
.mp-pv-box{background:#fff;border-radius:10px;width:min(1200px,96vw);
height:min(88vh,900px);display:flex;flex-direction:column;overflow:hidden;
box-shadow:0 20px 60px rgba(0,0,0,.35)}
.mp-pv-head{display:flex;align-items:center;gap:10px;padding:9px 14px;
border-bottom:1px solid var(--line);background:#f7f9f7}
.mp-pv-head b{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;
white-space:nowrap;font-size:13.5px}
.mp-pv-head .mp-pv-tools{display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end}
.mp-pv-body{flex:1;overflow:auto;padding:12px 14px;background:#fff;min-height:0}
.mp-pv-body img{max-width:100%;display:block;margin:0 auto;background:#fff}
.mp-pv-body iframe.mp-pv-frame{width:100%;height:100%;border:0;background:#fff}
.mp-pv-body pre{background:#f7f7f5;border:1px solid var(--line);border-radius:8px;
padding:10px;font-size:12px;line-height:1.5;max-height:none;overflow:auto;
white-space:pre;word-break:break-all;margin:0}
.mp-pv-note{color:var(--mut);font-size:13px}
#mp-lightbox .mp-lb-tools{margin-top:12px;display:flex;gap:10px}
#mp-lightbox .mp-lb-tools a{color:#fff;border:1px solid rgba(255,255,255,.45);
border-radius:999px;padding:4px 14px;font-size:13px;background:rgba(255,255,255,.12)}
#mp-lightbox .mp-lb-tools a:hover{background:rgba(255,255,255,.26);text-decoration:none}
.mp-files{display:grid;grid-template-columns:repeat(auto-fill,minmax(360px,1fr));gap:4px 18px}
.mp-file{font-size:13px;padding:2px 0;border-bottom:1px dashed #edf0ee}
.mp-file .mp-size{color:var(--mut);font-size:11.5px;margin-left:8px}
.mp-file .mp-desc{color:var(--mut);font-size:12px;margin-left:8px}
#mp-lightbox{position:fixed;inset:0;background:rgba(12,18,14,.92);display:none;
z-index:99;align-items:center;justify-content:center;flex-direction:column;padding:24px}
#mp-lightbox.show{display:flex}
#mp-lightbox img{max-width:96vw;max-height:88vh;background:#fff;padding:8px;border-radius:6px}
#mp-lightbox .mp-lb-cap{color:#dfe3e8;font-size:13px;margin-top:10px;max-width:90vw;text-align:center}
#mp-lightbox .mp-lb-close{position:fixed;top:14px;right:20px;color:#fff;
font-size:30px;cursor:pointer;line-height:1}
@media (max-width:760px){.mp-grid{grid-template-columns:1fr}}
/* ---- 页内目录（左栏 sticky；移动端折叠成横向 chips）---- */
.mp-wrap{display:grid;grid-template-columns:216px minmax(0,1fr);
max-width:1440px;margin:0 auto;align-items:start}
/* 无页内目录的页面（如批量汇总页）只留正文一列；否则 216px 空列会
   把正文挤到固定宽度之外（小屏直接溢出）。 */
.mp-wrap.mp-solo{grid-template-columns:minmax(0,1fr)}
.mp-toc{position:sticky;top:0;max-height:100vh;overflow:auto;
padding:16px 10px 24px 18px;border-right:1px solid var(--line);
background:#fbfaf8;min-height:calc(100vh - 40px)}
.mp-toc h3{font-size:11.5px;color:var(--mut);letter-spacing:2px;
margin:0 0 6px;text-transform:uppercase}
.mp-toc a{display:flex;justify-content:space-between;align-items:center;
gap:6px;padding:4px 9px;border-radius:6px;font-size:13px;color:var(--ink)}
.mp-toc a:hover{background:#eef2ee;text-decoration:none}
.mp-toc a.active{background:#e3efe8;color:var(--green);font-weight:600}
.mp-toc a .n{color:var(--mut);font-size:11px;font-weight:400}
.mp-toc .mp-toc-sub{margin-top:12px;border-top:1px dashed var(--line);padding-top:10px}
.mp-toc .mp-toc-sub a{font-size:12px;color:var(--blue)}
.mp-main{padding:18px 22px 40px;min-width:0}
@media (max-width:900px){.mp-wrap{grid-template-columns:1fr}
.mp-toc{position:static;min-height:0;max-height:none;border-right:none;
border-bottom:1px solid var(--line);display:flex;flex-wrap:wrap;gap:2px 6px;
padding:10px 14px}
.mp-toc h3{width:100%}
.mp-toc a{padding:3px 9px}
.mp-toc .mp-toc-sub{border-top:none;padding-top:2px;margin-top:4px}}
"""

# ==================================================================
# 页面脚本：灯箱放大 / PNG-SVG 切换 / iframe 懒加载 / fs-raw 上下文改写
# ==================================================================

_JS = r"""
(function(){
var BASE=__VP_BASE__;
function inRaw(){return location.protocol.indexOf('http')===0&&
location.pathname.indexOf('/api/fs/raw')>=0}
function rawUrl(rel){return '/api/fs/raw?path='+encodeURIComponent(BASE+'/'+rel)}
if(inRaw()){
  document.querySelectorAll('[data-rel]').forEach(function(el){
    var rel=el.getAttribute('data-rel');
    if(el.tagName==='IMG'){el.setAttribute('src',rawUrl(rel));}
    else{el.setAttribute('href',rawUrl(rel));}
  });
}
function imgRel(img){return img.getAttribute('data-rel')||img.getAttribute('src')||'';}
function curSrc(img){var r=imgRel(img);
  return inRaw()?rawUrl(r):img.getAttribute('src')||r;}
function fileUrl(rel){return inRaw()?rawUrl(rel):rel;}
document.querySelectorAll('.mp-fmt').forEach(function(btn){
  btn.addEventListener('click',function(){
    var box=btn.closest('.mp-fig');if(!box)return;
    var img=box.querySelector('.mp-imgbox img');if(!img)return;
    var src=btn.getAttribute('data-src');
    img.setAttribute('src',inRaw()?rawUrl(src):src);
    img.setAttribute('data-rel',src);
    box.querySelectorAll('.mp-fmt').forEach(function(b){b.classList.remove('active')});
    btn.classList.add('active');
  });
});
/* ---- 统一文件预览：所有文件链接默认本页弹层，不跳转 ----
   弹层头再给「新标签打开 / 下载」两个额外出口；Esc 或点遮罩关闭。 */
var pv=document.getElementById('mp-preview');
function pvBase(rel){var s=String(rel||'').replace(/\\/g,'/');return s.split('/').pop();}
function pvKind(rel){
  var m=String(rel||'').toLowerCase().match(/\.([a-z0-9]+)$/);
  var e=m?m[1]:'';
  if(/^(png|jpe?g|webp|gif|svg|bmp|ico)$/.test(e))return 'img';
  if(/^(html?|pdf)$/.test(e))return 'frame';
  if(/^(txt|log|md|markdown|csv|tsv|tab|json|jsonl|ndjson|xml|yml|yaml|ini|cfg|conf|py|r|sh|bat|ps1|faa|ffn|fna|fasta|fa|fas|aln|nwk|nhx|gff|gff2|gff3|gtf|vcf|bed|m8|sbt|src|cmt|list|out|err|phy|nex|tre|newick)$/.test(e))return 'text';
  return 'other';
}
function pvSizeTxt(n){
  n=Number(n)||0;if(n<=0)return '';
  if(n<1024)return n+' B';
  if(n<1048576)return (n/1024).toFixed(1)+' KB';
  if(n<1073741824)return (n/1048576).toFixed(1)+' MB';
  return (n/1073741824).toFixed(2)+' GB';
}
var PV_TEXT_MAX=2*1048576;   /* >2MB 不做整文件 fetch，只给新标签/下载 */
var pvSeq=0;                 /* 预览请求序号：旧响应不得覆盖后来打开的文件 */
function pvOpen(rel,url,size){
  if(!pv)return;
  var seq=++pvSeq;
  var name=pvBase(rel)||'预览';
  var nm=pv.querySelector('.mp-pv-name');
  nm.textContent=(rel===name?name:rel);nm.title=(rel===name?name:rel);
  var op=pv.querySelector('.mp-pv-open'),dl=pv.querySelector('.mp-pv-dl');
  op.setAttribute('href',url);dl.setAttribute('href',url);dl.setAttribute('download',name);
  var body=pv.querySelector('.mp-pv-body'),kind=pvKind(rel);
  if(kind==='img'){
    body.innerHTML='<img alt="" src="'+url.replace(/"/g,'%22')+'">';
  }else if(kind==='frame'){
    body.innerHTML='<iframe class="mp-pv-frame" loading="lazy" src="'
      +url.replace(/"/g,'%22')+'"></iframe>';
  }else if(kind==='text'){
    var n=Number(size)||0;
    if(n>PV_TEXT_MAX){
      /* 大文件不在本页整文件下载（曾实测 25MB FASTA 全量拉取后只截断） */
      body.innerHTML='<p class="mp-pv-note">文件较大（'+pvSizeTxt(n)
        +'），不在本页读取全文。请用上方「新标签打开」查看，'
        +'或「下载」保存到本地。</p>';
    }else{
      body.innerHTML='<p class="mp-pv-note">加载中…</p>';
      fetch(url,{cache:'no-store'}).then(function(r){
        if(!r.ok)throw new Error('HTTP '+r.status);return r.text();
      }).then(function(txt){
        if(seq!==pvSeq)return;          /* 已被更晚的预览取代，丢弃旧响应 */
        var cap=800000,cut=txt.length>cap,s=cut?txt.slice(0,cap):txt;
        body.innerHTML='<pre>'+s.replace(/&/g,'&amp;').replace(/</g,'&lt;')
          .replace(/>/g,'&gt;')+'</pre>'+(cut
          ?'<p class="mp-pv-note">仅显示前 800 KB，完整内容请用「新标签打开」或「下载」。</p>':'');
      }).catch(function(err){
        if(seq!==pvSeq)return;
        body.innerHTML='<p class="mp-pv-note">本页读取失败（离线双击打开时浏览器会拦截文本读取）：'
          +String(err&&err.message||err).replace(/</g,'&lt;')
          +'。请用上方「新标签打开」或「下载」。</p>';
      });
    }
  }else{
    body.innerHTML='<p class="mp-pv-note">这类文件不支持内嵌预览。'
      +'可用上方「新标签打开」查看，或「下载」保存到本地。</p>';
  }
  pv.classList.add('show');
}
function pvClose(){pvSeq++;if(pv)pv.classList.remove('show');}
if(pv){
  pv.addEventListener('click',function(e){
    if(e.target===pv||e.target.closest('.mp-pv-close'))pvClose();
  });
}
/* 输入/表格/数据/交互页/文件清单：默认本页预览；左侧目录与「其他模块」跨页导航保持跳转。 */
document.querySelectorAll('a[data-rel]').forEach(function(a){
  if(a.closest('.mp-toc'))return;
  a.addEventListener('click',function(ev){
    ev.preventDefault();
    var rel=a.getAttribute('data-rel')||'';
    pvOpen(rel,a.getAttribute('href')||fileUrl(rel),
           parseInt(a.getAttribute('data-size')||'0',10)||0);
  });
});
document.querySelectorAll('.mp-imgbox').forEach(function(box){
  box.addEventListener('click',function(){
    var img=box.querySelector('img');if(!img)return;
    var fig=box.closest('.mp-fig');
    var cap=fig?(fig.querySelector('figcaption b')||{}).textContent||'':'';
    var lb=document.getElementById('mp-lightbox');if(!lb)return;
    var rel=imgRel(img),url=curSrc(img);
    lb.querySelector('img').setAttribute('src',url);
    lb.querySelector('.mp-lb-cap').textContent=cap;
    var op=lb.querySelector('.mp-lb-open'),dl=lb.querySelector('.mp-lb-dl');
    if(op)op.setAttribute('href',url);
    if(dl){dl.setAttribute('href',url);dl.setAttribute('download',pvBase(rel));}
    lb.classList.add('show');
    return false;
  });
});
var lb=document.getElementById('mp-lightbox');
if(lb){
  lb.addEventListener('click',function(){lb.classList.remove('show')});
  var lbt=lb.querySelector('.mp-lb-tools');
  if(lbt)lbt.addEventListener('click',function(e){e.stopPropagation()});
}
document.addEventListener('keydown',function(e){
  if(e.key==='Escape'){if(lb)lb.classList.remove('show');pvClose();}});
/* 旧版交互页卡上的「预览」按钮（历史页面兼容）：同样走本页弹层 */
document.querySelectorAll('.mp-load button[data-src]').forEach(function(btn){
  btn.addEventListener('click',function(ev){
    ev.preventDefault();
    var rel=btn.getAttribute('data-src')||'';
    pvOpen(rel,fileUrl(rel),
           parseInt(btn.getAttribute('data-size')||'0',10)||0);
  });
});
/* --- 目录滚动高亮 --- */
var tocLinks=[].slice.call(document.querySelectorAll('.mp-toc a[data-sec]'));
function tocSpy(){
  var cur='';
  tocLinks.forEach(function(a){
    var el=document.getElementById(a.getAttribute('data-sec'));
    if(el&&el.getBoundingClientRect().top<130)cur=a.getAttribute('data-sec');
  });
  if(!cur&&tocLinks.length)cur=tocLinks[0].getAttribute('data-sec');
  tocLinks.forEach(function(a){
    a.classList.toggle('active',a.getAttribute('data-sec')===cur)});
}
if(tocLinks.length){
  window.addEventListener('scroll',tocSpy,{passive:true});
  tocSpy();
}
})();
"""

# ==================================================================
# 目录扫描与格式化辅助
# ==================================================================


def _walk(root):
    """递归列目录 → 文件条目列表。

    返回 {rel, name, ext, size, mtime, kind}；kind ∈
    figure/table/data/interactive/seq/other；rel 相对 root，统一 / 分隔。
    """
    out = []
    root = os.path.abspath(root)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames
                             if d not in _SKIP_DIRS and not d.startswith('.'))
        for fn in sorted(filenames):
            low = fn.lower()
            if (low in _SKIP_FILES or fn.startswith('.')
                    or low.endswith(_SKIP_SUFFIX)):
                continue
            p = os.path.join(dirpath, fn)
            try:
                st = os.stat(p)
            except OSError:
                continue
            rel = os.path.relpath(p, root).replace('\\', '/')
            ext = os.path.splitext(fn)[1].lower()
            if ext in ('.png', '.jpg', '.jpeg', '.webp', '.svg', '.gif'):
                kind = 'figure'
            elif ext in ('.tsv', '.csv') and not low.endswith(('.tsv.gz', '.csv.gz')):
                kind = 'table'
            elif ext == '.json' or low.endswith('.json.gz'):
                kind = 'data'
            elif ext in ('.html', '.htm'):
                kind = 'interactive'
            elif ext in _TEXT_EXTS or low.endswith(('.tsv.gz', '.csv.gz', '.fa.gz')):
                kind = 'seq'
            else:
                kind = 'other'
            out.append({'rel': rel, 'name': fn, 'ext': ext, 'size': st.st_size,
                        'mtime': st.st_mtime, 'mtime_ns': st.st_mtime_ns,
                        'kind': kind})
    out.sort(key=lambda f: (f['rel'].count('/'), f['rel']))
    return out


def _fmt_size(n):
    try:
        n = float(n)
    except (TypeError, ValueError):
        return ''
    if n < 1024:
        return f'{n:.0f} B'
    if n < 1024 ** 2:
        return f'{n / 1024:.1f} KB'
    if n < 1024 ** 3:
        return f'{n / 1024 ** 2:.1f} MB'
    return f'{n / 1024 ** 3:.2f} GB'


def _fmt_ts(ts):
    try:
        return time.strftime('%Y-%m-%d %H:%M', time.localtime(float(ts)))
    except (TypeError, ValueError):
        return ''


def _esc(s):
    return _html_mod().escape(str(s if s is not None else ''), quote=True)


def _html_mod():
    import html as _h
    return _h


def _json_script(obj):
    """可安全内嵌 <script> 的 JSON（防 '</' 提前闭合脚本块）。"""
    return json.dumps(obj, ensure_ascii=False).replace('</', '<\\/')


# ==================================================================
# 输入区
# ==================================================================


def _resolve_inputs(sample_root, stage_keys, page_dir):
    """解析阶段的输入条目 → [(标签, rel-from-page-dir, title 提示)]。

    patterns 里的 'project:r1'/'project:r2' 从 project.json 读原始输入；
    其余按相对样品根的候选路径/glob 逐个试，取第一个存在的；都没找到的
    条目不显示（不写"应有而未有"的输入）。
    """
    out = []
    pj = _read_project(sample_root)
    seen = set()
    for key in stage_keys:
        for label, pats in STAGE_INPUTS.get(key, []):
            rel_from_page = None
            tip = str(label)
            if pats:
                for pat in pats:
                    if pat in seen:
                        continue
                    if str(pat).startswith('project:'):
                        slot = str(pat).split(':', 1)[1]
                        p = (pj.get('input') or {}).get(slot)
                        if p and os.path.exists(str(p)):
                            rel_from_page = None
                            tip = f"{label}：{p}"
                            seen.add(pat)
                            break
                    else:
                        hits = _glob.glob(os.path.join(sample_root,
                                                       str(pat).replace('/', os.sep)))
                        if hits:
                            hit = hits[0]
                            rel_from_page = os.path.relpath(hit, page_dir).replace('\\', '/')
                            tip = os.path.relpath(hit, sample_root).replace('\\', '/')
                            seen.add(pat)
                            break
            if rel_from_page:
                out.append((label, rel_from_page, tip))
            elif pats is None:
                out.append((label, None, tip))
            elif any(str(p).startswith('project:') for p in (pats or [])):
                out.append((label, None, tip))
    return out


def _read_project(sample_root):
    """读 project.json（缺省 {}）。"""
    p = os.path.join(sample_root, 'project.json')
    try:
        with open(p, encoding='utf-8') as fh:
            return json.load(fh) or {}
    except Exception:
        return {}


def _sec_inputs(entries, page_dir=None):
    """输入区。entries: [(标签, rel-from-page 或 None, tip)]。

    page_dir 给了就把文件大小写进 data-size：大 FASTA 预览时前端只给
    「新标签打开 / 下载」，不再整文件 fetch（与其它文件链接同口径）。
    """
    if not entries:
        return ''
    chips = []
    for label, rel, tip in entries:
        if rel:
            size = 0
            if page_dir:
                try:
                    size = os.path.getsize(
                        os.path.join(page_dir, str(rel).replace('/', os.sep)))
                except OSError:
                    size = 0
            chips.append(
                f'<a class="mp-chip" data-rel="{_esc(rel)}" '
                f'data-size="{int(size)}" href="{_esc(rel)}"'
                f' title="{_esc(tip)}">{_esc(label)}'
                f'<span class="mp-size">{_esc(os.path.basename(rel))}</span></a>')
        else:
            chips.append(f'<span class="mp-chip" style="opacity:.72"'
                         f' title="{_esc(tip)}">{_esc(label)}</span>')
    return (f'<section id="sec-inputs"><h2>输入 <small>这一步吃的是什么</small></h2>'
            f'<div class="mp-card">{"".join(chips)}</div></section>')


# ==================================================================
# 图表区：分组 + 孪生格式配对 + 卡片
# ==================================================================


def _pair_twins(figs):
    """给每张图找同名异格式孪生（foo.png ↔ foo.svg）。

    返回 [(fig, twin_rel_or_None)]；已配对的另一侧不再单独出卡。
    """
    by_rel = {f['rel'] for f in figs}
    out, used = [], set()
    # PNG 侧优先做主卡（默认展示），SVG 作为孪生
    order = sorted(figs, key=lambda f: (not f['rel'].lower().endswith('.png'),
                                        f['rel']))
    for f in order:
        rel = f['rel']
        if rel in used:
            continue
        stem = rel[: rel.rfind('.')] if '.' in rel else rel
        twin = None
        for cand in (stem + '.svg', stem + '.png'):
            if cand != rel and cand in by_rel and cand not in used:
                twin = cand
                break
        if twin:
            used.add(twin)
        out.append((f, twin))
    return out


def _fig_card(f, twin, base_dir=None):
    """单张图卡片：标题+读法（figdocs）+ PNG/SVG 切换 + 点击放大。"""
    rel = f['rel']
    name = os.path.basename(rel)
    title, read = figure_doc(name)
    show = rel if twin is None else (
        rel if rel.lower().endswith('.png') else twin)
    fmt_btns = ''
    if twin:
        btn1 = (f'<button class="mp-fmt" data-src="{_esc(rel)}">'
                f'{_esc(os.path.splitext(rel)[1].upper().lstrip("."))}</button>')
        btn2 = (f'<button class="mp-fmt" data-src="{_esc(twin)}">'
                f'{_esc(os.path.splitext(twin)[1].upper().lstrip("."))}</button>')
        fmt_btns = btn1 + btn2
    cap_s = ''
    d = os.path.dirname(rel)
    if d not in ('', '.'):
        cap_s = f'<span class="mp-path" title="{_esc(rel)}">{_esc(d)}</span>'
    return (f'<figure class="mp-fig">'
            f'<div class="mp-imgbox"><img loading="lazy" data-rel="{_esc(show)}" '
            f'src="{_esc(show)}" alt="{_esc(name)}"></div>'
            f'<figcaption><b>{_esc(title)}</b>'
            f'<span class="mp-read">{_esc(read)}</span></figcaption>'
            f'<div class="mp-figbar">{fmt_btns}{cap_s}</div></figure>')


def _sec_figures(figs):
    """图表区：按一级子目录分组出卡。"""
    if not figs:
        return ''
    pairs = _pair_twins(figs)
    n = min(len(pairs), MAX_FIGS)
    out = [f'<section id="sec-figs"><h2>图表 <small>{n} 张 · '
           f'同名 PNG/SVG 可切换 · 点击放大</small></h2>']
    groups = {}
    for f, twin in pairs[:MAX_FIGS]:
        parent = os.path.dirname(f['rel'])
        groups.setdefault(parent if parent not in ('', '.') else '', []).append((f, twin))
    for parent in sorted(groups, key=lambda p: (p != '', p)):
        if parent:
            out.append(f'<div class="mp-sub">子目录 {_esc(parent)}</div>')
        out.append('<div class="mp-grid">')
        for f, twin in groups[parent]:
            out.append(_fig_card(f, twin))
        out.append('</div>')
    if len(pairs) > MAX_FIGS:
        out.append(f'<p class="mp-note">仅显示前 {MAX_FIGS} 张（共 {len(pairs)} 张）。</p>')
    out.append('</section>')
    return ''.join(out)


# ==================================================================
# 表格 / 数据 / 交互页 / 文件清单
# ==================================================================


def _read_head(path, n_lines):
    """读文本头 n 行（gzip 感知、replace 解码，绝不抛错）。"""
    lines = []
    try:
        import gzip
        opener = gzip.open if str(path).lower().endswith('.gz') else open
        with opener(path, 'rt', encoding='utf-8', errors='replace') as fh:
            for i, line in enumerate(fh):
                lines.append(line.rstrip('\n'))
                if i + 1 >= n_lines:
                    break
    except Exception:
        return []
    return lines


def _table_preview(f, page_dir):
    """表格条目 → 预览卡（前 N 行内嵌 + 完整文件链接）。"""
    rel = f['rel']
    path = os.path.join(page_dir, rel.replace('/', os.sep))
    delim = '\t' if f['name'].lower().endswith(('.tsv', '.tsv.gz')) else ','
    rows = []
    lines = _read_head(path, TABLE_ROWS + 1)
    for line in lines:
        # csv.reader 认引号：字段里含逗号（如 "a, b"）时整行不会被拆错位。
        try:
            cells = next(csv.reader(io.StringIO(line), delimiter=delim))
        except (csv.Error, StopIteration):
            cells = line.split(delim)
        cells = [c[:TABLE_CELL] for c in cells]
        if len(cells) > TABLE_COLS:
            cells = cells[:TABLE_COLS] + ['…']
        rows.append(cells)
    if not rows:
        return ''
    note = ''
    if len(lines) > TABLE_ROWS:
        note = (f'<div class="mp-note">仅预览前 {TABLE_ROWS} 行 · '
                f'完整文件请点下方链接。</div>')
    thead = ('<thead><tr>' +
             ''.join(f'<th>{_esc(c)}</th>' for c in rows[0]) + '</tr></thead>')
    trs = []
    for r in rows[1:]:
        trs.append('<tr>' + ''.join(f'<td>{_esc(c)}</td>' for c in r) + '</tr>')
    tbl = (f'<div class="mp-tbl-wrap"><table class="mp-tbl">{thead}<tbody>'
           + ''.join(trs) + '</tbody></table></div>')
    doc = ext_desc(rel)
    doc_s = (f'<div><span class="mp-desc">{_esc(doc)}</span></div>') if doc else ''
    return (f'<div class="mp-card">'
            f'<div style="display:flex;align-items:baseline;gap:10px;flex-wrap:wrap">'
            f'<b>{_esc(f["name"])}</b>'
            f'<span class="mp-note">{_esc(_fmt_size(f["size"]))}</span>'
            f'<span class="mp-path">{_esc(rel)}</span></div>{doc_s}{tbl}{note}'
            f'<div class="mp-figbar"><a class="mp-act" data-rel="{_esc(rel)}" '
            f'data-size="{int(f["size"] or 0)}" href="{_esc(rel)}">'
            f'🔍 预览完整表格</a></div></div>')


def _data_card(f, page_dir, json_mode):
    """JSON / 序列类 → 折叠预览卡。"""
    rel = f['rel']
    path = os.path.join(page_dir, f['rel'].replace('/', os.sep))
    if json_mode:
        body = '\n'.join(ln[:220] for ln in _read_head(path, MAX_JSON_LINES))
        if len(body.splitlines()) >= MAX_JSON_LINES:
            body += '\n…（仅前 120 行）'
        label = '预览 JSON（前 120 行）'
    else:
        body = '\n'.join(ln[:220] for ln in _read_head(path, TEXT_PREVIEW_LINES))
        if len(body.splitlines()) >= TEXT_PREVIEW_LINES:
            body += f'\n…（仅前 {TEXT_PREVIEW_LINES} 行）'
        label = f'预览前 {TEXT_PREVIEW_LINES} 行'
    doc = ext_desc(rel)
    doc_s = (f'<div><span class="mp-desc">{_esc(doc)}</span></div>') if doc else ''
    return (f'<div class="mp-card">'
            f'<div style="display:flex;align-items:baseline;gap:10px;flex-wrap:wrap">'
            f'<b>{_esc(f["name"])}</b>'
            f'<span class="mp-note">{_esc(_fmt_size(f["size"]))} · '
            f'{_esc(_fmt_ts(f["mtime"]))}</span>'
            f'<span class="mp-path">{_esc(rel)}</span></div>{doc_s}'
            f'<details class="mp-prev"><summary>{_esc(label)}</summary>'
            f'<pre>{_esc(body)}</pre></details>'
            f'<div class="mp-figbar"><a class="mp-act" data-rel="{_esc(rel)}" '
            f'data-size="{int(f["size"] or 0)}" href="{_esc(rel)}">'
            f'🔍 预览原文件</a></div></div>')


def _page_card(f):
    """交互页条目 → 预览卡（iframe 懒加载 + 新窗口直开）。"""
    rel = f['rel']
    desc = interactive_doc(rel) or interactive_doc(os.path.basename(rel))
    desc_s = f'<div><span class="mp-note">{_esc(desc)}</span></div>' if desc else ''
    return (f'<div class="mp-card">'
            f'<div style="display:flex;align-items:baseline;gap:10px;flex-wrap:wrap">'
            f'<b>🖱 {_esc(f["name"])}</b>'
            f'<span class="mp-note">{_esc(_fmt_size(f["size"]))} · '
            f'{_esc(_fmt_ts(f["mtime"]))}</span></div>{desc_s}'
            f'<div class="mp-figbar">'
            f'<a class="mp-act" data-rel="{_esc(rel)}" '
            f'data-size="{int(f["size"] or 0)}" href="{_esc(rel)}">🔍 预览</a>'
            f'<span class="mp-note">预览内可新标签打开 / 下载</span></div></div>')


def _sec_files(files):
    """其余文件清单（按扩展名分组）。"""
    if not files:
        return ''
    out = [f'<section id="sec-files"><h2>各类文件 <small>{len(files)} 个 · '
           f'点击本页预览（预览内可新标签打开 / 下载）</small></h2>']
    groups = {}
    for f in files:
        ext = f['ext'] or '(无扩展名)'
        groups.setdefault(ext, []).append(f)
    for ext in sorted(groups, key=lambda e: (-len(groups[e]), e)):
        items = groups[ext]
        shown = items[:MAX_LINKS]
        lis = []
        for f in shown:
            doc = ext_desc(f['name'])
            doc_s = (f'<span class="mp-desc">{_esc(doc)}</span>') if doc else ''
            lis.append(
                f'<div class="mp-file"><a data-rel="{_esc(f["rel"])}" '
                f'data-size="{int(f["size"] or 0)}" '
                f'href="{_esc(f["rel"])}">{_esc(f["name"])}</a>'
                f'<span class="mp-size">{_esc(_fmt_size(f["size"]))}</span>{doc_s}</div>')
        out.append(f'<div class="mp-sub">{_esc(ext)} '
                   f'<span class="mp-note">（{len(items)} 个）</span></div>')
        out.append(f'<div class="mp-files">{"".join(lis)}</div>')
        if len(items) > MAX_LINKS:
            out.append(f'<p class="mp-note">仅显示前 {MAX_LINKS} 个。</p>')
    out.append('</section>')
    return ''.join(out)


# ==================================================================
# 页面骨架
# ==================================================================


def _page(title, purpose, meta_items, nav_chips, sections_html, base_dir,
          toc_items=None, sig=None):
    """拼整页：头部 + 左栏目录 + 六区正文 + footer。

    toc_items: [(anchor_id, 标签, 数量徽标)]——只列实际渲染出的区；
    nav_chips（跨模块）挪进左栏底部，正文区与目录一起横向排布。
    sig: 目录页内容签名（输入未变则跳过重写，见 _write_dir_page）。
    """
    nav = ''
    if nav_chips:
        nav = ('<div class="mp-toc-sub"><h3>其他模块</h3>' + ''.join(
            f'<a class="{_esc(cls)}" data-rel="{_esc(href)}" href="{_esc(href)}">'
            f'{_esc(label)}</a>' for label, href, cls in nav_chips) + '</div>')
    toc_rows = ''.join(
        f'<a href="#{_esc(a)}" data-sec="{_esc(a)}">{_esc(label)}'
        f'<span class="n">{_esc(n)}</span></a>'
        for a, label, n in (toc_items or []))
    toc = (f'<nav class="mp-toc"><h3>本页目录</h3>{toc_rows}{nav}</nav>'
           if (toc_rows or nav) else '')
    meta = ''.join(
        f'<span>{_esc(k)}　<b>{_esc(v)}</b></span>' for k, v in meta_items)
    sig_tag = f'<!-- vp-page-sig:{_esc(sig)} -->\n' if sig else ''
    # 无页内目录时用单列，避免 216px 空列挤压正文（批量汇总页等）
    wrap_cls = 'mp-wrap' if toc else 'mp-wrap mp-solo'
    return ('<!DOCTYPE html>\n<html lang="zh-CN">\n<head>\n'
            '<meta charset="utf-8">\n' + sig_tag +
            '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            '<meta name="generator" content="VirusPlatform module_page">\n'
            f'<title>{_esc(title)} · 模块结果 · 植物病毒分析平台</title>\n'
            f'<style>{_CSS}</style>\n</head>\n<body>\n'
            '<header class="mp-top">'
            '<div class="mp-kicker">VirusPlatform · 模块结果页</div>'
            f'<h1>{_esc(title)}</h1>'
            f'<p class="mp-purpose">{_esc(purpose)}</p>'
            f'<div class="mp-meta">{meta}</div></header>\n'
            f'<div class="{wrap_cls}">{toc}'
            f'<main class="mp-main">\n{sections_html}\n'
            '<footer class="mp-note" style="margin-top:30px">'
            f'由 module_page 自 <b>{_esc(base_dir)}</b> 的落盘产物自动生成，'
            f'{_esc(time.strftime("%Y-%m-%d %H:%M"))}。'
            '产物均为相对链接，磁盘双击与平台内预览均可打开。</footer>\n'
            '</main></div>\n'
            '<div id="mp-preview"><div class="mp-pv-box">'
            '<div class="mp-pv-head"><b class="mp-pv-name"></b>'
            '<span class="mp-pv-tools">'
            '<a class="mp-act mp-pv-open" target="_blank" rel="noopener">↗ 新标签打开</a>'
            '<a class="mp-act mp-pv-dl">⬇ 下载</a>'
            '<button class="mp-act mp-pv-close" type="button">✕ 关闭</button>'
            '</span></div>'
            '<div class="mp-pv-body"><p class="mp-pv-note">加载中…</p></div>'
            '</div></div>\n'
            '<div id="mp-lightbox"><span class="mp-lb-close">×</span>'
            '<img src="" alt=""><div class="mp-lb-cap"></div>'
            '<div class="mp-lb-tools">'
            '<a class="mp-lb-open" target="_blank" rel="noopener">↗ 新标签打开</a>'
            '<a class="mp-lb-dl">⬇ 下载</a></div></div>\n'
            f'<script>var __VP_BASE__={_json_script(os.path.abspath(base_dir))};'
            f'{_JS}</script>\n</body>\n</html>\n')


# ==================================================================
# 模块页构建器
# ==================================================================

#: 目录名 → 页面标题
_DIR_TITLES = {
    '00_prep': '⓪ 预处理（下采样 · 质控 · 序列转换）',
    '01_host_removal': '① 宿主去除',
    '02b_kvsuite': '②b 已知病毒识别与定量',
    '03_assembly': '③ 组装·分类·提取',
    '08_host_analysis': '④ 宿主预测（ICTV）',
    '03b_verify': '③b 候选序列验证',
    '03c_consensus': '③c 共识序列与变异',
    '04_orf': '⑥ ORF 预测',
    '04b_orf_annot': '⑥b ORF 功能注释',
    '05_phylo': '⑦ 进化树与 SDT',
    '06_primer': '⑧ 引物设计',
    '09_genome_plots': '⑨ 基因组图',
    '07_report': '⑩ 报告与图表',
}


def _dir_groups(sample_root):
    """样品根下存在的阶段目录 → [(目录名, [阶段键, ...])]，按流程顺序。"""
    from .model import STAGE_DIR, STAGE_ORDER
    keys_by_dir = {}
    order = {}
    for i, key in enumerate(STAGE_ORDER):
        d = STAGE_DIR.get(key)
        if not d:
            continue
        keys_by_dir.setdefault(d, []).append(key)
        order.setdefault(d, i)
    out = []
    for d, keys in keys_by_dir.items():
        if os.path.isdir(os.path.join(sample_root, d)):
            out.append((d, keys))
    out.sort(key=lambda x: order.get(x[0], 99))
    return out


def _fig_count(figs, cap=None):
    """图表卡片的实际张数：同名 PNG/SVG 是一张图（孪生），不重复计数。"""
    n = len(_pair_twins(figs))
    return min(n, cap) if cap is not None else n


def _counts_meta(files):
    n_f = _fig_count([f for f in files if f['kind'] == 'figure'])
    n_t = sum(1 for f in files if f['kind'] == 'table')
    n_d = sum(1 for f in files if f['kind'] in ('data', 'seq'))
    n_p = sum(1 for f in files if f['kind'] == 'interactive')
    return (f'图表 {n_f} · 表格 {n_t} · 数据/序列 {n_d} · 交互页 {n_p} · '
            f'文件总数 {len(files)}')


#: 页面模板版本。改 _CSS / _JS / 版面结构后 +1，否则旧页面签名会把
#: 新模板的页面误判成"未变"而跳过重写。
_PAGE_SIG_VERSION = 1
_SIG_OPEN = '<!-- vp-page-sig:'
_SIG_CLOSE = '-->'


def _dir_page_sig(dir_rel, keys, files, inputs, nav):
    """阶段目录页的内容签名：文件清单（名/大小/mtime_ns）+ 输入 + 导航 + 模板版本。

    大样品逐阶段刷新时，若输入与产物都没变，重复写同一份 HTML 只是白白
    消耗 I/O；签名一致即跳过（item 14）。页面内容里唯一的时间戳是"生成
    时间"，跳过意味着它保留上一轮的值——这正是"页面未变"的语义。
    """
    payload = repr((
        _PAGE_SIG_VERSION, dir_rel, list(keys),
        [(f['rel'], f['size'], f.get('mtime_ns'))
         for f in files],
        [(str(a), str(b), str(c)) for a, b, c in inputs],
        [(str(a), str(b), str(c)) for a, b, c in nav],
    ))
    return hashlib.sha1(payload.encode('utf-8', 'replace')).hexdigest()


def _read_page_sig(path):
    """读既有 results.html 里的签名（没有/读不了 → 空串，按需重建）。"""
    try:
        with open(path, encoding='utf-8', errors='replace') as fh:
            head = fh.read(4096)
    except OSError:
        return ''
    i = head.find(_SIG_OPEN)
    if i < 0:
        return ''
    j = head.find(_SIG_CLOSE, i)
    return head[i + len(_SIG_OPEN):j].strip() if j > 0 else ''


def _write_dir_page(sample_root, dir_rel, keys):
    """给一个阶段目录写 results.html（原子写）。返回绝对路径。

    输入与产物均未变时直接复用既有页面（签名一致即跳过重写）。
    """
    page_dir = os.path.join(sample_root, dir_rel)
    files = _walk(page_dir)
    title = _DIR_TITLES.get(dir_rel, dir_rel)
    ps = [STAGE_PURPOSE.get(k, '') for k in keys]
    ps = [p for p in ps if p]
    purpose = '；'.join(dict.fromkeys(ps))
    inputs = _resolve_inputs(sample_root, keys, page_dir)
    figs = [f for f in files if f['kind'] == 'figure']
    tables = [f for f in files if f['kind'] == 'table']
    datas = [f for f in files if f['kind'] == 'data']
    seqs = [f for f in files if f['kind'] == 'seq']
    pages = [f for f in files if f['kind'] == 'interactive']
    shown = set()
    for grp in (figs, tables, datas, seqs, pages):
        for f in grp:
            shown.add(f['rel'])
    others = [f for f in files if f['rel'] not in shown]
    meta = [('模块目录', dir_rel), ('生成时间', time.strftime('%Y-%m-%d %H:%M')),
            ('产物规模', _counts_meta(files))]
    nav = _nav_chips(sample_root, dir_rel)
    toc = []
    if inputs:
        toc.append(('sec-inputs', '输入', len(inputs)))
    if figs:
        toc.append(('sec-figs', '图表', _fig_count(figs, MAX_FIGS)))
    if tables:
        toc.append(('sec-tables', '表格', min(len(tables), MAX_TABLES)))
    if datas or seqs:
        toc.append(('sec-data', '数据与序列', len(datas) + len(seqs)))
    if pages:
        toc.append(('sec-pages', '交互页', len(pages)))
    if others:
        toc.append(('sec-files', '各类文件', len(others)))
    out = os.path.join(page_dir, OUT_NAME)
    os.makedirs(page_dir, exist_ok=True)
    sig = _dir_page_sig(dir_rel, keys, files, inputs, nav)
    # 签名检查放在渲染各区之前：输入与产物都没变时，连表头预览/JSON 头
    # 这些读盘渲染都省掉，而不是渲染完再把写盘省掉（item 14）。
    if os.path.isfile(out) and _read_page_sig(out) == sig:
        return out
    secs = []
    if inputs:
        secs.append(_sec_inputs(inputs, page_dir))
    try:
        secs.append(_sec_figures(figs))
    except Exception as e:
        secs.append(f'<div class="mp-bad">图表区渲染失败：{_esc(e)}</div>')
    try:
        if tables:
            secs.append('<section id="sec-tables"><h2>表格 <small>'
                        f'{len(tables)} 张 · 前 30 行内嵌预览</small></h2>')
            for f in tables[:MAX_TABLES]:
                secs.append(_table_preview(f, page_dir))
            if len(tables) > MAX_TABLES:
                secs.append(f'<p class="mp-note">仅显示前 {MAX_TABLES} 张。</p>')
            secs.append('</section>')
    except Exception as e:
        secs.append(f'<div class="mp-bad">表格区渲染失败：{_esc(e)}</div>')
    try:
        cards = []
        for f in datas[:MAX_DATA]:
            cards.append(_data_card(f, page_dir, json_mode=True))
        for f in seqs[:MAX_DATA]:
            cards.append(_data_card(f, page_dir, json_mode=False))
        if cards:
            secs.append('<section id="sec-data"><h2>数据与序列 <small>'
                        f'{len(datas) + len(seqs)} 个 · 折叠预览</small></h2>')
            secs.extend(cards)
            secs.append('</section>')
    except Exception as e:
        secs.append(f'<div class="mp-bad">数据区渲染失败：{_esc(e)}</div>')
    try:
        if pages:
            secs.append('<section id="sec-pages"><h2>交互页 <small>'
                        f'{len(pages)} 个 · 点击本页预览（预览内可新标签打开）</small></h2>')
            for f in pages[:24]:
                secs.append(_page_card(f))
            secs.append('</section>')
    except Exception as e:
        secs.append(f'<div class="mp-bad">交互页区渲染失败：{_esc(e)}</div>')
    try:
        secs.append(_sec_files(others))
    except Exception as e:
        secs.append(f'<div class="mp-bad">文件区渲染失败：{_esc(e)}</div>')
    html = _page(title, purpose, meta, nav, ''.join(secs), page_dir, toc,
                 sig=sig)
    tmp = out + '.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write(html)
    os.replace(tmp, out)
    return out


def _nav_chips(sample_root, cur_dir):
    """跨模块导航 chips：其他阶段目录的 results.html（存在才给链接）。"""
    chips = []
    for d, keys in _dir_groups(sample_root):
        p = os.path.join(sample_root, d, OUT_NAME)
        cls = 'self' if d == cur_dir else ('ghost' if not os.path.isfile(p) else '')
        chips.append((_DIR_TITLES.get(d, d), f'../{d}/{OUT_NAME}', cls))
    return chips


class _LoggerAdapter:
    """把裸函数 log(msg) 适配成 .log(msg)（TaskManager/工具 job 传的是函数）。"""

    def __init__(self, fn):
        self._fn = fn

    def log(self, msg, *a):
        try:
            self._fn(str(msg))
        except Exception:
            pass


def _norm_logger(logger):
    if logger is None or hasattr(logger, 'log'):
        return logger
    return _LoggerAdapter(logger)


def build_stage_page(sample_dir, stage=None, logger=None):
    """给样品的阶段目录生成结果页（原子写，输入未变则跳过重写）。

    stage 可为阶段键（如 kvsuite）或目录名（如 02b_kvsuite）：
    - 指定 stage：只写目标阶段页——流水线每完成一个阶段调它一次，若顺带
      重写全部兄弟页就是 O(阶段²) 的重复 I/O；兄弟页留给整轮结束时的
      build_all_for_sample()（⑩ 阶段与 CLI report）统一补齐。
    - stage=None：写全部阶段页。
    返回目标页的绝对路径；样品没有可识别的阶段目录时返回 None。
    """
    logger = _norm_logger(logger)
    sample_dir = os.path.abspath(str(sample_dir))
    if not os.path.isdir(sample_dir):
        raise FileNotFoundError(f'目录不存在: {sample_dir}')
    groups = _dir_groups(sample_dir)
    if not groups:
        if logger:
            logger.log(f'样品目录无阶段产物，跳过模块结果页: {sample_dir}')
        return None
    if stage:
        for d, keys in groups:
            if d == stage or stage in keys:
                return _write_dir_page(sample_root=sample_dir, dir_rel=d,
                                       keys=keys)
        raise ValueError(f'未识别的阶段/目录: {stage}（可用: '
                         + '、'.join(d for d, _k in groups) + '）')
    first = None
    for d, keys in groups:
        p = _write_dir_page(sample_root=sample_dir, dir_rel=d, keys=keys)
        if first is None:
            first = p
    return first


# ==================================================================
# 工具运行页
# ==================================================================


def _read_run_meta(run_dir):
    """读工具运行目录的 run_meta.json（样本/原始输入；缺省 {}）。"""
    try:
        with open(os.path.join(run_dir, 'run_meta.json'),
                  encoding='utf-8') as fh:
            return json.load(fh) or {}
    except Exception:
        return {}


def build_tool_run_page(run_dir, logger=None):
    logger = _norm_logger(logger)
    """给工具箱运行目录生成 results.html（原子写）。返回绝对路径。"""
    run_dir = os.path.abspath(str(run_dir))
    if not os.path.isdir(run_dir):
        raise FileNotFoundError(f'目录不存在: {run_dir}')
    files = _walk(run_dir)
    if not files:
        if logger:
            logger.log(f'运行目录为空，跳过模块结果页: {run_dir}')
        return None
    name = os.path.basename(run_dir)
    t_title, t_desc = tool_info(name)
    meta = _read_run_meta(run_dir)
    sample = str(meta.get('sample') or '').strip()
    title = f'工具箱 · {t_title}（{name}）'
    if sample:
        title = f'工具箱 · {t_title} · {sample}（{name}）'
    purpose = (f'{t_desc}。本页整理该次运行的全部产物：'
               '输入/参数见 run.log，图配读法，文件全部可点开。')
    inputs = [(f['name'], f['rel'], '本次运行的输入文件')
              for f in files if os.path.dirname(f['rel']) == ''
              and f['name'].lower().startswith('input')]
    # run_meta.json：目录批量逐样本提交时记下的样本名与原始 R1/R2。
    # 运行目录里只有 ASCII 临时副本，事后靠这份元数据才能认出是哪件样本。
    for _label, _key in (('R1', 'r1'), ('R2', 'r2'), ('输入', 'input'),
                         ('输入 2', 'input2'), ('contigs', 'contigs'),
                         ('FASTA', 'fasta'), ('注释', 'ann'),
                         ('输入目录', 'input_dir')):
        _v = str(meta.get(_key) or '').strip()
        if not _v:
            continue
        _bn = os.path.basename(_v.rstrip('/\\')) or _v
        inputs.append((f'{_label}：{_bn}', None, _v))
    figs = [f for f in files if f['kind'] == 'figure']
    tables = [f for f in files if f['kind'] == 'table']
    datas = [f for f in files if f['kind'] in ('data', 'seq')]
    pages = [f for f in files if f['kind'] == 'interactive']
    others = [f for f in files
              if f['rel'] not in {x['rel'] for x in figs + tables + datas + pages}]
    secs = []
    if inputs:
        secs.append(_sec_inputs(inputs, run_dir))
    try:
        secs.append(_sec_figures(figs))
    except Exception as e:
        secs.append(f'<div class="mp-bad">图表区渲染失败：{_esc(e)}</div>')
    try:
        if tables:
            secs.append('<section id="sec-tables"><h2>表格 <small>'
                        f'{len(tables)} 张 · 前 30 行内嵌预览</small></h2>')
            for f in tables[:MAX_TABLES]:
                secs.append(_table_preview(f, run_dir))
            if len(tables) > MAX_TABLES:
                secs.append(f'<p class="mp-note">仅显示前 {MAX_TABLES} 张。</p>')
            secs.append('</section>')
    except Exception as e:
        secs.append(f'<div class="mp-bad">表格区渲染失败：{_esc(e)}</div>')
    try:
        cards = []
        for f in datas[:MAX_DATA]:
            cards.append(_data_card(f, run_dir, json_mode=f['kind'] == 'data'))
        if cards:
            secs.append('<section id="sec-data"><h2>数据与序列 <small>'
                        f'{len(datas)} 个 · 折叠预览</small></h2>')
            secs.extend(cards)
            secs.append('</section>')
    except Exception as e:
        secs.append(f'<div class="mp-bad">数据区渲染失败：{_esc(e)}</div>')
    try:
        if pages:
            secs.append('<section id="sec-pages"><h2>交互页 <small>'
                        f'{len(pages)} 个 · 点击本页预览（预览内可新标签打开）</small></h2>')
            for f in pages[:24]:
                secs.append(_page_card(f))
            secs.append('</section>')
    except Exception as e:
        secs.append(f'<div class="mp-bad">交互页区渲染失败：{_esc(e)}</div>')
    try:
        secs.append(_sec_files(others))
    except Exception as e:
        secs.append(f'<div class="mp-bad">文件区渲染失败：{_esc(e)}</div>')
    meta = [('运行目录', name), ('生成时间', time.strftime('%Y-%m-%d %H:%M')),
            ('产物规模', _counts_meta(files))]
    toc = []
    if inputs:
        toc.append(('sec-inputs', '输入', len(inputs)))
    if figs:
        toc.append(('sec-figs', '图表', _fig_count(figs, MAX_FIGS)))
    if tables:
        toc.append(('sec-tables', '表格', min(len(tables), MAX_TABLES)))
    if datas:
        toc.append(('sec-data', '数据与序列', len(datas)))
    if pages:
        toc.append(('sec-pages', '交互页', len(pages)))
    if others:
        toc.append(('sec-files', '各类文件', len(others)))
    html = _page(title, purpose, meta, [], ''.join(secs), run_dir, toc)
    out = os.path.join(run_dir, OUT_NAME)
    tmp = out + '.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write(html)
    os.replace(tmp, out)
    return out


def build_batch_html(entries, title='批量结果'):
    """批量合并模块结果页：把同一次批量提交的多个运行汇成一页。

    entries: [{'run': 运行名, 'dir': 运行目录, 'sample': 样本名(可空)}]
    返回自包含 HTML（图/表/文件全部绝对链接 /tool_runs/<run>/...）。
    页面结构：批量汇总表（每样本关键数字 + 模块结果页）→ 每样本图表与文件。
    只读磁盘；不写任何文件。
    """
    import html as _h
    rows = []
    for e in entries or []:
        run = str(e.get('run') or '')
        d = str(e.get('dir') or '')
        sample = str(e.get('sample') or '').strip()
        meta = {}
        try:
            with open(os.path.join(d, 'run_meta.json'), encoding='utf-8') as fh:
                meta = json.load(fh) or {}
        except Exception:
            pass
        if not sample:
            sample = str(meta.get('sample') or '').strip() or run
        files = _walk(d) if os.path.isdir(d) else []
        stats = {}
        sp = os.path.join(d, 'contigs_stats.json')
        if os.path.isfile(sp):
            try:
                with open(sp, encoding='utf-8') as fh:
                    stats = (json.load(fh) or {}).get('stats') or {}
            except Exception:
                stats = {}
        if not stats:
            # 鉴定 / 识别等没有 contigs_stats：从 summary.json 取几个关键标量
            for _fn in ('summary.json', 'run_summary.json'):
                _p = os.path.join(d, _fn)
                if not os.path.isfile(_p):
                    continue
                try:
                    with open(_p, encoding='utf-8') as fh:
                        _m = json.load(fh) or {}
                except Exception:
                    continue
                if isinstance(_m, dict):
                    for _k in ('n_contigs', 'n_viral', 'n_extracted',
                               'n_classified', 'total_bp', 'N50', 'GC(%)'):
                        if isinstance(_m.get(_k), (int, float, str)):
                            stats[_k] = _m[_k]
                if stats:
                    break
        figs = [f for f in files if f['kind'] == 'figure'][:6]
        rows.append({'run': run, 'dir': d, 'sample': sample,
                     'files': files, 'stats': stats, 'figs': figs})
    n = len(rows)
    esc = lambda x: _h.escape(str(x if x is not None else ''), quote=True)
    from urllib.parse import quote as _q

    def href(run, rel=''):
        segs = [x for x in str(rel).replace(chr(92), '/').split('/') if x]
        base = '/tool_runs/' + _q(str(run), safe='')
        return base + ('/' + '/'.join(_q(x, safe='') for x in segs) if segs else '')
    now = time.strftime('%Y-%m-%d %H:%M')
    head = (f'<title>{esc(title)} · 批量结果 · 植物病毒分析平台</title>'
            f'<style>{_CSS}</style>')
    body = [f'<header class="mp-top"><div class="mp-kicker">VirusPlatform · 批量模块结果页</div>'
            f'<h1>{esc(title)}</h1>'
            f'<p class="mp-purpose">同一次批量提交的 {n} 个样本，逐样本结果与产物汇总；'
            f'每个样本仍保留自己的运行目录与模块结果页。</p>'
            f'<div class="mp-meta"><span>样本数　<b>{n}</b></span>'
            f'<span>生成时间　<b>{esc(now)}</b></span></div></header>'
            '<div class="mp-wrap mp-solo"><main class="mp-main">']
    # 汇总表
    body.append('<section id="sec-sum"><h2>样本汇总 <small>关键数字 · 模块结果页 · 产物</small></h2>')
    body.append('<div class="mp-card"><div class="mp-tbl-wrap"><table class="mp-tbl"><thead><tr>'
                '<th>样本</th><th>运行</th><th>关键数字</th><th>模块结果页</th><th>产物</th>'
                '</tr></thead><tbody>')
    for r in rows:
        bits = []
        st = r['stats']
        for k, lab in (('num_seqs', '序列'), ('sum_len', '总长'),
                       ('N50', 'N50'), ('GC(%)', 'GC%')):
            if st.get(k) not in (None, ''):
                bits.append(f'{lab} {esc(st[k])}')
        files = r['files']
        main = [f for f in files if f['ext'] in ('.fasta', '.fa', '.fna',
                                                  '.fastq', '.fastq.gz',
                                                  '.tsv', '.csv', '.gz')][:4]
        links = ''.join(
            f'<a class="mp-act" href="{esc(href(r["run"], f["rel"]))}" '
            f'target="_blank" rel="noopener">⬇ {esc(os.path.basename(f["rel"]))}</a> '
            for f in main)
        body.append(f'<tr><td><b>{esc(r["sample"])}</b></td>'
                    f'<td class="mono">{esc(r["run"])}</td>'
                    f'<td>{esc(" · ".join(bits) or "—")}</td>'
                    f'<td><a class="mp-act" href="/api/module_report?run={esc(r["run"])}" '
                    f'target="_blank" rel="noopener">🧩 打开</a></td>'
                    f'<td>{links or "—"}</td></tr>')
    body.append('</tbody></table></div></div></section>')
    # 逐样本
    for i, r in enumerate(rows, 1):
        body.append(f'<section id="sec-{i}"><h2>{esc(r["sample"])} '
                    f'<small>{esc(r["run"])}</small></h2>')
        st = r['stats']
        if st:
            chips = ''.join(f'<span class="mp-chip">{esc(k)}<b>{esc(v)}</b></span>'
                            for k, v in st.items()
                            if k in ('num_seqs', 'sum_len', 'min_len', 'avg_len',
                                     'max_len', 'N50', 'N50_num', 'GC(%)', 'sum_n'))
            if chips:
                body.append(f'<div class="mp-card">{chips}</div>')
        if r['figs']:
            body.append('<div class="mp-grid">')
            for f in r['figs']:
                url = href(r["run"], f["rel"])
                body.append(f'<figure class="mp-fig"><a href="{url}" target="_blank" '
                            f'rel="noopener"><img loading="lazy" src="{url}" '
                            f'alt="{esc(f["name"])}" style="max-width:100%"></a>'
                            f'<figcaption><b>{esc(f["name"])}</b></figcaption></figure>')
            body.append('</div>')
        if r['files']:
            body.append(f'<div class="mp-card"><div class="mp-files">')
            for f in r['files'][:60]:
                url = href(r["run"], f["rel"])
                body.append(f'<div class="mp-file"><a href="{url}" target="_blank" '
                            f'rel="noopener">{esc(f["name"])}</a>'
                            f'<span class="mp-size">{esc(_fmt_size(f["size"]))}</span></div>')
            body.append('</div></div>')
        body.append('</section>')
    body.append('</main></div>')
    nl = chr(10)
    return ('<!DOCTYPE html>' + nl + '<html lang="zh-CN">' + nl
            + '<head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            + head + '</head><body>' + ''.join(body) + '</body></html>')


def build_all_for_sample(sample_dir, logger=None):
    logger = _norm_logger(logger)
    """生成样品全部阶段页（build_stage_page 的全量别名）。"""
    return build_stage_page(sample_dir, stage=None, logger=logger)


def main(argv=None):
    """CLI：python -m Virus_Platform_Core.report_kit.module_page ..."""
    import sys
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ('-h', '--help'):
        print(__doc__)
        return 0
    sample = run = dir_arg = stage = None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == '--sample':
            sample = argv[i + 1] if i + 1 < len(argv) else None
            i += 2
        elif a == '--run':
            run = argv[i + 1] if i + 1 < len(argv) else None
            i += 2
        elif a == '--dir':
            dir_arg = argv[i + 1] if i + 1 < len(argv) else None
            i += 2
        elif a == '--stage':
            stage = argv[i + 1] if i + 1 < len(argv) else None
            i += 2
        else:
            i += 1
    try:
        if run:
            from Virus_Platform_Core.config import DIRS
            p = os.path.join(DIRS['tool_runs'], run)
            if not os.path.isdir(p):
                p = os.path.join(DIRS['tool_runs'], '_archive', run)
            p = build_tool_run_page(p)
        elif dir_arg:
            p = build_tool_run_page(dir_arg)
        else:
            from Virus_Platform_Core.config import DIRS
            sd = os.path.join(DIRS['results'], sample or '')
            p = build_stage_page(sd, stage=stage)
        if not p:
            print('没有可整理的产物目录', file=sys.stderr)
            return 1
        print(p)
        return 0
    except Exception as e:
        print(f'[失败] {type(e).__name__}: {e}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    # docstring 宣称 `python -m ...module_page` 可用；没有这个守卫时直接
    # `python -m` 什么也不发生（main() 永远不会被调用）。
    raise SystemExit(main())


