#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""「发起的页面自己能看到进度条 + 实时日志」不变量守卫（静态，秒级）。

为什么需要这个测试
----------------
2026-11-02 用户报：「怎么下载这里，没有日志和进度条显示呢」。实测根因不是
「缺容器」而是**整条可见性链断了**——数据资源页 refdata.html:133-134 只加载
i18n.js + app.js，没加载 app-jobs.js；而 `taskLogOpen`（app-jobs.js:9）、
`startPolling`（:21）、`watchTaskBtn` 全仓只在 app-jobs.js 定义。于是
app.js:2056 与 refdata.html:148/215 三处调用直接 ReferenceError：
后台任务其实已经建了（/tasks 可见），页面上既不锁按钮、不轮询、也不写结果，
用户看到的就是「点了没反应、没有日志和进度条」。

补脚本还不够：任务名 `宿主基因组下载 …`（build.py:672）、
`病毒数据集下载 …`（:783）、`datasets genome 下载 …`（:857）既不在
TOOL_LABELS（app-jobs.js:194-239）里，也不含 :289-292 那道闸门要求的
「工具·/Tool·/NCBI/GenBank/同属/Synteny」，所以卡内永远不会被注入。

这条链有五个环，缺一环就回到「点了没反馈」：
  A. 页面必须加载 app-jobs.js（否则轮询/日志 API 全是 undefined）；
  B. 每张发起任务的卡必须有容器（.toolrun/.buildrun#toolrun-<key>）；
  C. 任务名必须命中一张表（REFDL_LABELS / BUILD_TASK_LABELS）；
  D. 渲染器必须同时画出进度条和日志（inlineTaskHtml），大文件下载无 ETA 时
     走不确定扫条（.bar.indet + @keyframes barIndet），不许显示假进度；
  E. meta 页的任务名白名单也要覆盖它真正会起的任务（AI 总结 曾漏）。

跑法：python tests/_check_refdl_logs.py   （退出码 0 = 通过）
"""
import os
import re
import sys

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REFDATA = os.path.join(ROOT, 'webapp', 'templates', 'refdata.html')
META = os.path.join(ROOT, 'webapp', 'templates', 'meta.html')
JOBS = os.path.join(ROOT, 'webapp', 'static', 'app-jobs.js')
CSS = os.path.join(ROOT, 'webapp', 'static', 'app.css')
BUILD_PY = os.path.join(ROOT, 'Virus_Platform_Core', 'web', 'build.py')
META_PY = os.path.join(ROOT, 'Virus_Platform_Core', 'web', 'meta.py')

FAILS = []


def check(ok, msg):
    print(('  ✔ ' if ok else '  ✘ ') + msg)
    if not ok:
        FAILS.append(msg)


def read(path):
    with open(path, encoding='utf-8') as f:
        return f.read()


def main():
    tpl = read(REFDATA)
    jobs = read(JOBS)
    css = read(CSS)
    py = read(BUILD_PY)
    meta = read(META)
    meta_py = read(META_PY)

    print('[A] refdata 页加载 app-jobs.js（否则 taskLogOpen/startPolling 未定义）')
    check('app-jobs.js' in tpl, 'refdata.html 引用了 app-jobs.js')
    check(tpl.index('app.js') < tpl.index('app-jobs.js'),
          'app-jobs.js 在 app.js 之后加载（它要用 app.js 的 $/esc/fmtDur）')
    check(re.search(r'<script src="/static/app-jobs\.js\?v=\{\{\s*asset_v\s*\}\}">', tpl)
          is not None, 'app-jobs.js 带 asset_v 版本号（避免旧缓存）')

    print('[B] 三张下载卡各有卡内容器')
    for key in ('hostdl', 'virusds', 'dsgen'):
        check(re.search(r'id="toolrun-%s"' % key, tpl) is not None,
              'refdata.html 有 #toolrun-%s' % key)
    check('class="toolrun"' in tpl,
          '容器带 .toolrun 类（injectStageLogs 每轮按类清空后重填）')

    print('[C] 任务名 → 容器 的映射表存在且覆盖三类任务')
    check('REFDL_LABELS' in jobs, 'app-jobs.js 定义了 REFDL_LABELS')
    # 2026-11-02 起取值统一成对象写法（{labels, prefix:false}）：这些任务名
    # 不带「工具·」前缀，必须显式关掉前缀门，否则又回到「点了没反馈」。
    for key in ('hostdl', 'virusds', 'dsgen'):
        check(re.search(r'\b%s:\s*\{\s*labels:\s*\[' % key, jobs) is not None,
              'REFDL_LABELS 含 %s 键（对象写法）' % key)
    for needle in ('宿主基因组下载', 'Host genome download',
                   '病毒数据集下载', 'Virus dataset download',
                   'datasets genome 下载', 'datasets genome download'):
        check("'%s'" % needle in jobs, '映射表含任务名 %r' % needle)
    # 后端真的会这么命名，否则前端映射是空转
    for needle in ("f'宿主基因组下载 {label}'", "f'病毒数据集下载 {taxon or accession}'",
                   "f'datasets {mode} 下载 {slug}'"):
        check(needle in py, '后端任务名与映射表对得上: %s' % needle)
    check("'toolrun-' + key" in jobs and 'REFDL_LABELS' in jobs,
          '注入循环把命中任务写进 toolrun-<key>')
    check(re.search(r'for \(const \[key, raw\] of Object\.entries\(REFDL_LABELS\)\)',
                    jobs) is not None, 'REFDL_LABELS 走 Object.entries 遍历注入')
    check(re.search(r'REFDL_LABELS = \{(?:.*?)prefix: false', jobs, re.S) is not None,
          'REFDL 三类任务都关了前缀门（prefix:false）')

    print('[D] 渲染器同时给进度条与日志，且不谎报百分比')
    check('function inlineTaskHtml(task)' in jobs, '存在 inlineTaskHtml(task)')
    body = jobs[jobs.index('function inlineTaskHtml(task)'):]
    _nxt = body.find('\nfunction ', 10)
    body = body[:_nxt] if _nxt > 0 else body
    check('class="bar' in body or "class=\"bar${" in body,
          'inlineTaskHtml 画进度条')
    check('class="logbox"' in body, 'inlineTaskHtml 画运行日志')
    check('c.runlog' in body, '日志区有标题（不是一坨裸文本）')
    check("task.eta == null" in body, '无 ETA 时才走不确定扫条（有 ETA 仍给真百分比）')
    check('indet' in body, '运行中无 ETA → .bar.indet 不确定态')
    check('outFilesHtml' not in body,
          '不套 Downloads 区（这些产物不在 tool_runs/，否则恒显示「暂无可下载文件」）')
    check(re.search(r'\.bar\.indet i\s*\{[^}]*animation:\s*barIndet', css) is not None,
          'app.css 定义了 .bar.indet 的扫条动画')
    check('@keyframes barIndet' in css, 'app.css 定义了 @keyframes barIndet')

    print('[E] 建库页建库任务也走同一渲染（原来只有日志、没有进度条）')
    _bl = jobs.find('Object.entries(BUILD_TASK_LABELS)')
    _seg = jobs[_bl:_bl + 500] if _bl > 0 else ''
    check('inlineTaskHtml' in _seg,
          'buildrun 循环改用 inlineTaskHtml（带进度条）')

    print('[F] meta 页任务白名单覆盖它真正会起的任务')
    line = [ln for ln in meta.splitlines() if 'const mine = list.filter' in ln]
    check(len(line) == 1, 'meta.html 有且只有一处任务名白名单')
    if line:
        check('AI 总结' in line[0], '白名单含「AI 总结」（原先漏掉，只剩按钮文字）')
        check('AI summary' in line[0], '白名单含英文名 AI summary（EN 界面不再是空面板）')
        for zh in ('元数据', '公共检索', 'AI 元数据补全', '检索绘图'):
            check(zh in line[0], '白名单仍含 %s（没有回退旧覆盖）' % zh)
    check("f'AI 总结·{name}/{which}'" in meta_py,
          'meta.py 确实会起「AI 总结」任务（白名单不是凭空加）')

    print()
    if FAILS:
        print('✘ %d 项不通过' % len(FAILS))
        return 1
    print('✔ 全部通过')
    return 0


if __name__ == '__main__':
    sys.exit(main())
