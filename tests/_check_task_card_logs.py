#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""任务卡「进度条 + 实时日志」覆盖面守卫（静态，秒级）。

背景
----
2026-11-02 用户报「点了没日志、没进度条」，第一轮修的是数据资源页三张
下载卡（见 tests/_check_refdl_logs.py）。随后按同一口径全量审了十个发起
任务的模板，发现**同类缺口还有一批**——都是「后端起了任务、页面没有
容器 / 容器键与任务名对不上 / 任务名过不了前缀门」：

  - tools.html 「快速建树」任务名 `工具·快速建树` 命中 quicktree，
    但容器叫 toolrun-treebuild → 卡内恒空；「集合建树」（refs.py:356，
    任务名无「工具·」前缀）压根没登记。
  - tools.html 病毒变异分析复用 kvsuite + stage=variant，任务名却是
    `工具·已知病毒识别与定量` → 日志灌进识别卡，toolrun-variant 是死容器。
  - tools.html ICTV 下载（refs.py:219）、GenBank 集合下载、BEAST 作业包
    导出：要么没登记，要么登记了却没容器。
  - build.html 构建鉴定库（build.py:1245 `病毒鉴定库构建`）、数据集行的
    构建分类库（build.py:1409 `病毒库构建`）：键/容器全缺。
  - cds_export.html 容器 toolrun-cdsexport 死：任务名
    `CDS/PEP 导出·…`（cds_export.py:86）过不了「工具·」前缀门。

修法
----
标签表取值支持对象写法 {labels, prefix:false, excl:[...], box:'其它键'}：
  - prefix:false 放行后端自起名（集合建树 / ICTV下载 / CDS/PEP 导出）；
  - excl 让「病毒库构建」不被「通用病毒库构建 (RefSeq Viral)」包含；
  - box 让同卡两个按钮共用一个输出区（集合建树 → quicktree 容器）。

本测试守的是「修完不许回退」：
  A. labelSpec/specHit 的三种语义都在，三张表都走它；
  B. 三张表里新增/修正的条目仍在，且前缀门未放宽到影响池阶段任务；
  C. 每个新增条目都有对应容器，且旧键（toolrun-treebuild）不再被引用；
  D. 后端任务名与前端标签仍然对得上（改后端名字必须同步改标签）；
  E. kvsuite 变异分析的任务名由 tools_api.py 按 stage 改写。

跑法：python tests/_check_task_card_logs.py   （退出码 0 = 通过）
"""
import os
import re
import sys

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JOBS = os.path.join(ROOT, 'webapp', 'static', 'app-jobs.js')
TOOLS = os.path.join(ROOT, 'webapp', 'templates', 'tools.html')
BUILD_TPL = os.path.join(ROOT, 'webapp', 'templates', 'build.html')
CDS_TPL = os.path.join(ROOT, 'webapp', 'templates', 'cds_export.html')
TOOLS_API = os.path.join(ROOT, 'Virus_Platform_Core', 'web', 'tools_api.py')
BUILD_PY = os.path.join(ROOT, 'Virus_Platform_Core', 'web', 'build.py')
REFS_PY = os.path.join(ROOT, 'Virus_Platform_Core', 'web', 'refs.py')
CDS_PY = os.path.join(ROOT, 'Virus_Platform_Core', 'web', 'cds_export.py')

FAILS = []


def check(ok, msg):
    print(('  ✔ ' if ok else '  ✘ ') + msg)
    if not ok:
        FAILS.append(msg)


def read(path):
    with open(path, encoding='utf-8') as f:
        return f.read()


def labels_block(js, name):
    """截出 `const NAME = {` 到匹配的 `};` 之间的文本。"""
    m = re.search(r'const %s = \{(.*?)\n\};' % re.escape(name), js, re.S)
    return m.group(1) if m else ''


def main():
    js = read(JOBS)
    tools = read(TOOLS)
    build_tpl = read(BUILD_TPL)
    cds_tpl = read(CDS_TPL)
    tools_api = read(TOOLS_API)
    build_py = read(BUILD_PY)
    refs_py = read(REFS_PY)
    cds_py = read(CDS_PY)

    print('A. 标签表取值语义（labelSpec / specHit）')
    check('function labelSpec(' in js, 'labelSpec 存在')
    check('function specHit(' in js, 'specHit 存在')
    check('prefix: spec.prefix !== undefined ? !!spec.prefix : gate' in js,
          '对象写法支持 prefix:false，且未写时继承该表默认门')
    check("excl: spec.excl || []" in js, '对象写法支持 excl 排除表')
    check("box: pre + (spec.box || key)" in js, '对象写法支持 box 重指容器')
    check('const gate = defaultGate !== false;' in js,
          '数组写法仍默认过前缀门（池阶段任务不会被误注入工具卡）')
    check("const spec = labelSpec(raw, key, 'buildrun-', false);" in js,
          '建库表默认不设前缀门（任务名本就不带「工具·」）')
    check("const spec = labelSpec(raw, key, 'toolrun-', false);" in js,
          '数据资源页下载表不设前缀门')
    check('if (box && specHit(spec, task.name || \'\'))' in js,
          '三张表统一走 specHit')
    check("nm.includes('工具·') || nm.includes('Tool·')" in js,
          '前缀门原文仍在（工具·/Tool· 两条）')

    print('B. TOOL_LABELS 新增条目')
    tl = labels_block(js, 'TOOL_LABELS')
    check("treebuild:{ labels: ['集合建树', 'Collection phylo'" in tl,
          'treebuild 登记「集合建树」（refs.py:356 的任务名）')
    check("prefix: false, box: 'quicktree' }" in tl,
          'treebuild 不过前缀门、输出写进 quicktree 容器')
    check("ictv:     { labels: ['ICTV下载', 'ICTV download'], prefix: false }"
          in tl, 'ictv 登记且不过前缀门')
    check("cdsexport:{ labels: ['CDS/PEP 导出', 'CDS/PEP export'], prefix: false }"
          in tl, 'cdsexport 登记且不过前缀门')
    check("variant:  ['病毒变异分析', 'Variant analysis']," in tl,
          'variant 卡按新任务名登记')
    # 2026-11-04 起 hostpredict 并入分类卡同一输出块（box: 'contigs'），
    # 不再有自己的 toolrun 容器；守卫按现行设计断言「不过前缀门 + 并入 contigs」。
    check("hostpredict: { labels: ['宿主预测', 'Host prediction'], prefix: false,"
          in tl, 'hostpredict 不过前缀门（自动触发的任务名不带「工具·」）')
    check("box: 'contigs' }," in tl,
          'hostpredict 输出并入分类卡 toolrun-contigs（追加通道）')
    check("quicktree:['快速建树', 'Quick tree']," in tl,
          'quicktree 原条目保留（快速建树按钮）')

    print('C. BUILD_TASK_LABELS 新增条目')
    bl = labels_block(js, 'BUILD_TASK_LABELS')
    check("kvidx:    ['病毒鉴定库构建', 'Virus index build']," in bl,
          'kvidx 登记「病毒鉴定库构建」')
    check("kunpeng:  { labels: ['病毒库构建', 'Virus DB build']," in bl,
          'kunpeng 登记「病毒库构建」')
    check("excl: ['RefSeq', 'RVDB', 'C-RVDB'] }" in bl,
          'kunpeng 排除 RefSeq / RVDB（避免与 refvirus/rvdb 双写）')

    print('D. 模板容器与脚本')
    check('id="toolrun-quicktree"' in tools, 'tools.html 有 toolrun-quicktree')
    check('id="toolrun-treebuild"' not in tools,
          'tools.html 不再引用已改名的 toolrun-treebuild')
    check('id="toolrun-ictv"' in tools, 'tools.html 有 toolrun-ictv 容器')
    check('id="toolrun-gbdown"' in tools, 'tools.html 有 toolrun-gbdown 容器')
    check('id="toolrun-beastexport"' in tools,
          'tools.html 有 toolrun-beastexport 容器')
    check('id="toolrun-variant"' in tools, 'tools.html 有 toolrun-variant 容器')
    check('id="toolrun-contigs"' in tools,
          'tools.html 分类卡容器存在（hostpredict 自动触发进度并入该卡可见）')
    submit_tpl = read(os.path.join(ROOT, 'webapp', 'templates', 'submit.html'))
    check('const tail = (snap.log || []).slice(-4);' in submit_tpl,
          'submit 物种名校验显示日志尾（不只看 msg）')
    check('app-jobs.js' in tools, 'tools.html 加载 app-jobs.js')
    check('id="buildrun-kunpeng"' in build_tpl,
          'build.html 有 buildrun-kunpeng 容器（数据集行构建分类库）')
    check('id="buildrun-kvidx"' in build_tpl, 'build.html 有 buildrun-kvidx 容器')
    check('app-jobs.js' in build_tpl, 'build.html 加载 app-jobs.js')
    check('id="toolrun-cdsexport"' in cds_tpl, 'cds_export.html 有 cdsexport 容器')
    check('app-jobs.js' in cds_tpl, 'cds_export.html 加载 app-jobs.js')

    print('E. 后端任务名 ↔ 前端标签')
    check("f'集合建树 {name}', f'Collection phylo {name}'" in refs_py,
          'refs.py 集合建树任务名未变（与 treebuild 标签一致）')
    check("f'ICTV下载 {coll}', f'ICTV download {coll}'" in refs_py,
          'refs.py ICTV 下载任务名未变')
    check("f'CDS/PEP 导出·{name}', f'CDS/PEP export·{name}'" in cds_py,
          'cds_export.py 任务名未变')
    check("f'病毒鉴定库构建 {name}', f'Virus index build {name}'" in build_py,
          'build.py 鉴定库任务名未变')
    check("f'病毒库构建 {name}', f'Virus DB build {name}'" in build_py,
          'build.py kunpeng 任务名未变')
    check("title, title_en = '病毒变异分析', 'Variant analysis'" in tools_api,
          'tools_api.py 对 kvsuite stage=variant 改写任务名')
    check('str(p.get(\'stage\') or \'\').strip().lower() == \'variant\'' in tools_api,
          '改写条件按 stage 精确判断（不误伤识别/定量）')
    check('cfg.tr(f"工具·{title}{_suffix} {ts}",' in tools_api,
          '任务名改用 title 变量（原 spec 标题不再写死，可带样品后缀）')

    print('F. 上次运行恢复的容器键一致性')
    check("quicktree: 'quicktree'" in js,
          'CARD_RUN_PREFIX 用 quicktree 键（恢复进现存容器）')
    check("treebuild: 'quicktree'" not in js,
          'CARD_RUN_PREFIX 不再用已失效的 treebuild 键')

    print('')
    if FAILS:
        print('✘ %d 项失败' % len(FAILS))
        for f in FAILS:
            print('   - ' + f)
        return 1
    print('✔ 全部通过')
    return 0


if __name__ == '__main__':
    sys.exit(main())
