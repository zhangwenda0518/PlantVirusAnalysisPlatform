# 开发笔记 · 踩坑记录与目录规范

> 本文档面向平台维护者：汇总开发过程中踩过的坑（及修复方式）、
> 目录规范约定、以及一轮全面回顾后的验证基线。使用手册见根目录 README.md。

## 一、踩坑记录（重要，改代码前先看）

### 环境与路径
1. **中文路径**：SPAdes 与 BLAST(LMDB) 不支持含中文/非 ASCII 的路径。
   平台的对策：SPAdes 经 `%TEMP%\vp_spades`（纯 ASCII）中转后拷回；
   BLAST 库建在 `%TEMP%\vp_blast`。新增外部工具调用时必须考虑这一点。
2. **Windows 端口保留段**：Hyper-V/WSL 会动态保留端口段，固定端口可能
   bind 失败（"访问权限不允许"）。`app.py::_pick_port` 逐个探测候选端口。
3. **控制台编码**：所有入口（app.py/main.py/Virus_Platform_Core.config）在 win32 下
   `sys.stdout.reconfigure(encoding='utf-8')`，否则日志中文乱码/报错。
4. **CRLF**：FASTA/FASTQ 写出一律 LF（`safe_open` 已统一），
   CRLF 会导致 SPAdes 等解析失败。
5. **orfipy 日志目录**：orfipy 默认把日志写到输入文件旁边的
   `<输入名>_out/` 目录（曾在平台根目录留下 `orfipy_viral_contigs.fasta_out`）。
   修复：调用时显式传 `--outdir` 到样品目录内（见 Virus_Platform_Core/orf.py）。
6. **kunpeng 大基因组建库 OOM**：convert 阶段 60 条/批整批载入，
   多条大染色体同批会触发 ~20.8GB 确定性巨型分配。对策：注入 taxid 时
   把每条序列切 ≤1MB 片段（相邻 34bp 重叠保 k-mer），见 Virus_Platform_Core/kunpeng.py。
7. **线程数与建库内存**：kunpeng 建库内存峰值 ≈ 1GB/线程，GUI 默认限 8 线程。

### 任务与并发
8. **幽灵任务**：服务重启后 running 状态任务永远不结束。对策：
   `TaskManager._recover_interrupted` 启动时把遗留 running 标记为 failed。
   **2026-09-11 补第二层坑**：`run/tasks/` 里除了任务记录还住着
   `queue.json`（SampleQueue 的批处理队列）。恢复逻辑原先只按 `.json`
   后缀筛文件，于是 ① 修剪到最近 100 个时会把最老的 `queue.json` 当旧任务
   删掉（整条队列静默丢失）② `json.load` 得到 list，`d.get()` 抛
   AttributeError 被外层 except 吞掉，**整个恢复循环中断、幽灵任务照样留着**。
   对策：文件列表加 `self._tid_ok(n[:-5])`（12 位十六进制才算任务）。
   `cancel_all/purge_all` 同样用 `_tid_ok` 挡 `queue.json`。
9. **同样品并发**：同一样品并发分类会写爆磁盘。对策：`/api/pipeline/<s>/run`
   拒绝同名样品的第二个运行中任务（任务名以 `<样品> ·` 前缀识别）。
10. **任务结果丢失**：早期 `snapshot()` 不返回 `result` 字段，任务完成后
    前端拿不到任何结果摘要（用户第 6 条痛点的根源）。已修复：
    snapshot/持久化均含 `result` 与 `result_preview`。

### 数据与安全
11. **路径穿越**：所有文件读写必须经 `utils.check_path` / `safe_open`
    （拒绝 `..`、输出限定平台根内）。新增 API 时禁止绕过直接 open()。
12. **NCBI 下载**：域名白名单（eutils.ncbi.nlm.nih.gov）+ 限速重试 +
    会话式翻页（借鉴 PhyloSuite），改 Virus_Platform_Core/ncbi_download.py 时保持这些约束。
13. **GUI 仅监听 127.0.0.1**，不要改成 0.0.0.0。

### 前端
14. **服务失联诊断**：连续 3 次轮询失败显示红色横幅（app.js `setConnBanner`），
    恢复后自动消失——修改轮询逻辑时保留该机制。
15. **LOGAN 批量面板**用 SSE（`/api/task/<id>/stream`）实时刷新，
    不整页重渲染（防止打断正在输入的邮箱框）。新增面板交互时同样
    避免全量 innerHTML 覆盖正在编辑的区域。
16. **进化树查看器用 Archaeopteryx.js**（`webapp/static/vendor/archaeopteryx/`，
    LGPL-3.0，npm 包 `archaeopteryx`）。渲染入口是 app.js `renderTreeTo()`：
    同一容器重新 `launchArchaeopteryx` 即换树，换树前先 `viewer.destroy()`；
    一页只能有一个 viewer。FastTree/RAxML-NG（以及 IQ-TREE 旧产物）把支持值
    写成内部节点名，
    解析需 `nhConfidenceValuesAsInternalNames: true`（此时必须同时
    `nhConfidenceValuesInBrackets: false`，两者互斥，见 app.js
    `_treeInternalLabelsAllNumeric()`）。面板自带布局/支持值/搜索/导出，
    PNG 导出依赖 `window.Canvg`（ESM 模块桥接），PDF 依赖 `window.jspdf` + svg2pdf。
17. **切模块就失忆**：一级导航全是裸 `<a href>`（无 SPA 路由），每次切模块都是
    整页刷新，页面级 JS 变量必然归零。表现最重的是 `/pipeline` 的当前样品：
    只有 URL 带 `?sample=` 才恢复，而导航栏的 `/pipeline` 不带参数；曾经写进
    `localStorage['vp_last_sample']` 的"上次样品"**全仓没有任何读取点**，
    于是"切回来忘了在分析哪个样品"。对策：`app.js` 新增全局上下文
    （`VP_CTX`，键 `vp_ctx_sample` / `vp_ctx_project`）+ 顶部导航常驻胶囊，
    变更经 `document` 上的 `vp-ctx` 事件广播给各页重挂自己。
    **改这一带时注意**：项目下拉的变更必须走模板 `onchange="gxProjChanged(this)"`，
    不能只在 `DOMContentLoaded` 里 `addEventListener('change')`——属性处理器在
    解析期就注册，页面自身的 onchange（会重刷下拉）先跑，会把用户刚选的值刷回去。
18. **`applyI18n` 会覆盖动态文案**：`i18n.js` 在 `DOMContentLoaded` 里对所有
    `[data-i18n]` 元素重写 `innerHTML`。想在静态标签后拼动态内容（例如
    「只看当前样品（NX-1）」）时，动态部分要放进**不带 `data-i18n` 的独立元素**，
    否则会被整段覆盖掉。
19. **顶部导航必须能"跟着内容长高"，且不能让它把品牌挤走**（2026-09-11 用户截图报障）。
    `.topnav` 原是 `height: 54px` 固定高度，而 `.navlinks` 是 `flex-wrap: wrap`；
    13 个一级链接约占 1150px，窗口一窄（实测 ≲1500px，尤其加了右侧全局上下文条
    之后）链接内部就换行，可绿色横条仍是 54px —— **第二行直接溢出到横条外的白底上**。
    `height: auto` 的兜底只写在 ≤720px，远低于实际窗口宽度，所以平时必然踩到。
    修的时候踩到两个反直觉点，都靠真浏览器量出来（`tests/_it_nav_responsive.py`）：
    - 横条自己**不能**用 `flex-wrap: wrap`：宽度不够时它会把整个 `.navlinks`
      挤到第二行（品牌独占一行、高度直接 72~80px），比原来更丑。正确做法是
      `flex-wrap: nowrap` + `.navlinks{flex:1 1 auto; min-width:0}`，
      让品牌与导航同处一行、由 `.navlinks` 内部消化换行；
      到 ≤1180px 再显式切成"品牌一行 + 导航整行"。
    - **末尾控制簇要绑成一组**：13 个链接刚好差几十像素时，flex 换行只会把
      最后的 📂 一个图标挤到第二行，孤零零挂在右边像坏了（实测 1884px 正是
      这个症状）。故 `app.js` 把「上下文条 + #langBtn + 📂」收进 `.navtail`
      （`flex-wrap: nowrap` 的单个 flex 项），要么整簇留第一行、要么整簇换行。
    - 量"行数"不能用 `round(top)` 去重：`align-items: center` 下同一行里不同
      高度的元素 `top` 本来就不同（把单行数成 5 行）。要按"中心 Y 分簇"。
    - 上下文胶囊宽度有限会截断长样品名，`title` 里必须给出**完整**名字。

### 打包
20. **PyInstaller**：webapp 模板/静态资源经 `_webapp_dir()` 多路径探测
    （源码同级 → 上级 → `sys._MEIPASS`）。新增静态目录时同步改
    package.py / VirusPlatform.spec。
21. `dist/VirusPlatform/` 是打包产物（含运行时 results/logs/tasks），
    不要把开发期临时文件混进去；`build/` 为 PyInstaller 中间目录，可随时删除。

### 破坏性操作与巡检工具
22. **破坏性接口必须要求显式确认，且巡检工具必须跳过它**（2026-09-11 实测事故）。
    `POST /api/global/reset` 最初只用请求体里的 `include_archive` 做可选项，
    空 POST 就会执行「取消全部任务 + 清队列 + 清任务记录」。而
    `tests/_audit_routes.py` 的设计是"对每条路由发一次空 POST"来找 500，
    于是它把 `run/tasks/*.json` 里约 100 条历史任务记录**全部删掉了**。
    该脚本当时的 docstring 写着"全程只读，不删除任何文件"——**这句话没有实现**：
    它只比对 tasks/ 目录的"新增"，完全不检查"删除"。
    两条修复，缺一不可：
    - 接口侧：加 `confirm: 'reset'` 必填令牌（**防误触，不是鉴权**），
      空 body 一律 400；
    - 工具侧：`_audit_routes.DESTRUCTIVE_POST` 显式列出跳过的破坏性路由
      （`/api/global/reset`、`/api/tasks/clear_finished`、
      `/api/queue/clear_finished` —— 后两条早就是空 POST 即删的状态），
      并把快照比对从"只查新增"改成"新增与删除都比对"。
    **新增任何破坏性路由时，必须同时登记到 `DESTRUCTIVE_POST`。**
    损失范围：只丢了任务中心的记录索引（名称/状态/日志尾部/时间），
    任务**日志文件本体**（`run/logs/tasks/` 698 个）与全部科研结果
    （results/ tool_runs/）都在——删记录不删日志是 `tasks.py` 的既有设计。

## 二、目录规范

**2026-09-10 目录整理**：顶层按「代码 / 数据库 / 运行期数据 / 外部依赖」四层
收敛，运行期数据进 `run/`、外部二进制与第三方进 `3rd/`，数据库保持原位。

```
<平台根>/
├─ app.py / main.py        GUI 与 CLI 入口
├─ Virus_Platform_Core/    核心 pipeline 包（GUI 与 CLI 共用，勿在 app.py 写业务逻辑）；
│  │                       2026-09-10 由 vp/ 更名而来（原 vp = Virus Platform 缩写）
│  ├─ known_virus_suite/   已知病毒识别与定量引擎（五段整合，自带 CLI）；
│  │                       同日由顶层 engines/known_virus_suite/ 迁入包内，
│  │                       模块间改用相对导入（from .kv_xxx）
│  ├─ web/                 HTTP 边界与任务工厂（Flask blueprint）
│  ├─ public_meta/         公共数据检索引擎
│  └─ ncbi_submit/         NCBI 提交准备
├─ webapp/
│  ├─ templates/           页面模板（Jinja2）
│  └─ static/              app.css / app.js / i18n.js / examples.js / vendor/
├─ databases/              kunpeng 库、taxonomy、tree_db、annot、misc（大数据）
├─ examples/               内置各工具示例数据 + results/（示例运行结果，与真实结果隔离）；
│                          2026-09-10 由 databases/examples/ 迁到根目录，整体纳管 git
├─ host-db/                建库源数据（宿主源基因组）
├─ databases/virusref_db/  病毒参考 + 派生索引（kv_index/ 鉴定库：salmon_k31/ + minibwa/）
│                          2026-09-11 由顶层 virus-db/ 迁入 databases/（命名随 _db 惯例）
├─ run/                    运行期数据（可清理重建，git 忽略）
│  ├─ results/<样品>/       每样品产物（00_prep … 09_genome_plots、logs）
│  ├─ tool_runs/           工具箱独立运行产物
│  ├─ logan/<查询名>/       LOGAN 溯源任务
│  ├─ tasks/ · logs/       GUI 任务状态与运行日志
│  ├─ uploads/ · submissions/ · meta_search/ · fastq/ · downloads/
├─ tests/                  自检/造数脚本与烟测数据
├─ docs/                   开发文档（本文档）
├─ scripts/                辅助脚本（build_virus_db.py / package.py / VirusPlatform.spec）
├─ 3rd/                    外部依赖（git 忽略）
│  ├─ bin/                 单文件外部可执行（kunpeng / seqkit / crabz / clustalw2 /
│  │                       muscle / aria2c / sracha；*.orig.exe 备份同放）
│  ├─ tools/               带目录结构的工具套件（Blast / mafft-win / FastTree /
│  │                       iQtree / trimAl / Gblocks / diamond / mmseqs /
│  │                       fastp / SPAdes / salmon2 等）
│  └─ open-virome/         Open-Virome 前端构建源（/virome 页 `_FRONTEND_BUILD`
│                          引用，勿改名/搬动）
├─ git-repo/               第三方仓库镜像（ViralConsensus 等，仅供参考）
└─ _archive/               归档区（历史产物、外平台工具、迁移备份）
```

**旧路径兼容（重要）**：`Virus_Platform_Core/utils.py::check_path` 会把旧的平台相对顶层目录
自动重定向到新位置（`tool_runs/`、`results/`、`uploads/`… → `run/`；
`tools/`、`bin/`、`vendor/`、`open-virome/` → `3rd/`），映射表见
`Virus_Platform_Core/config.py::LEGACY_TOP_DIRS`。因此历史数据、前端拼接的路径、旧文档示例
都无需手工改；**新增代码请直接用 `DIRS[...]`，不要再写字面量路径**。

工具路径探测集中在 `Virus_Platform_Core/config.py::detect_tools()`：先 `3rd/bin/`、
`3rd/tools/`，根目录平铺布局保留为回退（兼容已分发的 exe 平台）；
`platform.json` 的 `tools` 覆盖优先级最高。

**`platform.json` 的 tools 路径约定（2026-09-09 起）**：平台根内的工具写
**相对路径**（如 `bin/minibwa.exe`），由 `Virus_Platform_Core/config.py::Config._load()`
按 `PLATFORM_ROOT` 解析；平台根外的工具（Python Scripts、外部 SPAdes）
仍写绝对路径。这样整个平台目录搬到别的盘或改名，工具探测不会连带失效。

约定：
- **版本管理改用 git（2026-09-09 起）**：本仓库已 `git init` 并推送
  GitHub（`zhangwenda0518/plant-virus-analysis-platform`）。
  改代码前不要复制 `*.bak_<改动名>_<日期>`——历史用
  `git log -p -- <path>` 看演进、`git show <sha>:<path>` 取回旧版；
  `.gitignore` 已禁止提交 `*.bak_*` / `*.bak` / `*.orig`。
  2026-09-09 一次性清理了 74 个历史手工备份（含 `tools.html` 的 8 个版本），
  内容全部在 git 历史里（如 `git show 38243d2:webapp/templates/tools.html.bak_nodeid_20260909`）。
  回退：`git revert <sha>` / `git checkout <sha> -- <path>`。
- 临时/中间文件一律写进对应任务目录（results/<样品>/… 或 tool_runs/<run>/），
  禁止落在平台根目录（历史上曾出现 `C:` 空目录、`orfipy_*_out` 等遗留物）。
- `*.orig.exe` 等二进制备份保留在原处需注明用途，长期不用应移入 docs/ 归档说明。
  **当前唯一备份**：`bin/kun_peng-*.orig.exe` 是 kunpeng 打补丁前的原版
  （MD5 与在用 exe 不同，勿当重复文件删）。
  `bin/sracha.exe` 与 `bin/sracha-0.6.0-local-patch.exe` 也不是同一文件，
  前者为在用版本。
- minibwa 单文件 exe 统一放 `bin/minibwa.exe`（2026-09-09 前曾重复放在
  `minibwa_win_build/`，已合并）；构建说明在 `docs/minibwa-build/`。
- **`tools/strawberry-perl/` 不要清理**：它是 SNPGenie 的 Perl 解释器
  （便携版 5.42.3，`perl/bin/perl.exe`），被
  `Virus_Platform_Core/known_virus_suite/kv_variant_evo.py::_snpgenie_exe()` 引用。
  2026-09-09 曾因目录瘦身把它归档到 `_archive/`，导致 SNPGenie 链路断掉，
  现已恢复。使用要点见 `tools/snpgenie/DEPLOY_NOTE.md`（工作目录必须纯
  英文路径，且要显式传 `--workdir` 避开 MSYS 的 `pwd` 污染）。
- 日志只增不改；排查问题先看 `logs/` 与 `results/<样品>/logs/`。

## 三、验证基线（2026-09-05 全面回归）

- `python main.py tools`：全部内置工具探测通过（单文件工具在 bin/）。
- `python -c "import app"` / 各 vp 模块 import：无错误。
- Flask test client 冒烟：`/` `/pipeline` `/build` `/results` `/tools` `/logan`
  `/virome` `/help` `/settings` `/hostremoval` 均 200；核心 API
  `/api/tools` `/api/dbs` `/api/samples` `/api/tasks` `/api/settings` 正常。
- 独立模块烟测：`Virus_Platform_Core.msa_view`、`Virus_Platform_Core.phylo._safe_name`、`Virus_Platform_Core.pipeline` 视图函数、
  设置中心读写（platform.json `defaults`/`language`）。
- 上下游依赖链：fq2fa→host→virus→assembly→(hostana/orf)→orfa→
  phylo→primer→gbdraw→report 的依赖表见 `Virus_Platform_Core/pipeline.py::STAGE_DEPS`，
  管道页卡片按依赖解锁；工具缺失自动降级（fastp/seqkit/gbdraw/orfa）。

## 四、更新日志（第一轮 · 凌晨）

- 清理：根目录 `C:` 空目录、`orfipy_viral_contigs.fasta_out`、`__pycache__`、
  PyInstaller `build/` 中间目录、散落的 `.mimosa` 插件状态目录。
- 新增：设置中心（/settings，语言/线程/邮箱/默认参数/路径）、
  任务结果预览与完成通知、前端 i18n（zh/en）、orfipy --outdir 修复、
  `python main.py selfcheck` 环境自检命令。
- 修复（浏览器实机验证发现）：
  - settings.html 内联脚本重复声明 `const $`（与 app.js 冲突）导致整页脚本
    失效、默认参数网格空白——内联脚本一律复用全局 `$`，禁止重复声明；
  - LOGAN 页 `.step b` 选择器误伤正文内联 `<b>`（渲染成巨型绿色梭形），
    改为 `.step > b` 仅命中步骤编号，并删除模板内重复的本地样式块；
  - 静态资源缓存：app.css/app.js/i18n.js 引用统一带 `?v={{ asset_v }}`
    （app.js 的 mtime），升级后浏览器不会再用旧缓存（本次 LOGAN 修复
    曾因缓存迟迟不生效，即为该问题的实证）。
- 验证基线补充：Flask test client 全 9 页 + 9 API 通过；设置读写往返通过；
  真实任务（contigs 分类）完成后任务卡结果预览面板渲染通过（统计 chips +
  7 个产物下载链接）；`python main.py selfcheck` 全绿。

## 五、第二轮深度检查（2026-09-05 下午）

### 关键发现：坏修复被实测拦截
- **orfipy `--outdir` 修复本身是坏的**：相对路径输出目录 + 绝对路径输入时
  orfipy 直接退出码 1（首轮"验证"只测了绝对路径场景，漏掉了
  `predict_orfs → run_orfipy(viral_fa, out_dir)` 传相对路径的真实调用形态）。
  修复：`run_orfipy` 内部 `os.path.abspath(out_dir)` 后再拼参。
  **教训**：改外部工具调用参数后，必须用生产调用形态（相对路径）实测。
- **端到端全流程**：新建样品 E2E-VERIFY 用 CLI 跑完 13 阶段
  （fastp→fq2fa→host→virus→assembly→hostana→virome→orf→orfa→phylo→
  primer→gbdraw→report）全部 done、日志 0 ERROR，保留作为回归基线。

### 新增能力
- **阶段结果预览**：`STAGE_VIEW` 从仅 report 扩展到 9 个阶段
  （fastp 报告/host stats/virus 汇总/组装 contigs/宿主桑基/RdRP 汇总/
  ORF 注释表/引物表/主报告）；管道卡片「查看」按钮打开预览弹窗
  （TSV→分页表格、JSON→格式化、HTML→新窗口），带下载按钮。
- **样品管理**：结果页每行加「🗑 删除」；后端 `/api/samples/<s>/delete`
  三重防护（样品名规范化 + 必须 results/ 一级子目录 + 有运行中任务时拒绝）。
- **参数开放**：⑦ 卡片暴露 `tree_sampling`（blast/macro/genus/lineage）、
  ⑨ 卡片暴露 `plot_engine`（auto/gbdraw/dfv）——此前仅 CLI 可用。
- **任务状态文件自动修剪**：服务启动时 tasks/*.json 只保留最近 100 个。
- **i18n 补盲**：结果页 MSA/树视图说明与下拉/复选框标签接词典（16→30 处）。

### 目录清整
- 删除 9 个昨日开发期 tool_runs（保留最新 1 个作预览面板示例）与
  results/NX-5（0 文件的空骨架）。
- **results/NX-6（61GB）是真实样品数据，永不清整**；01_host_removal 52GB
  为其宿主去除中间产物，属用户数据。
- `.zcode/ui_shots/` 为验收截图存档，保留供对照。

## 六、布局重构（2026-09-05 晚）

问题：结果入口分散在 4 个页面（样品报告在"结果"、共线性图在"工具箱"、
溯源报告在"LOGAN"）；工具箱是杂物抽屉（单步工具与进阶比较混排）；
导航顺序不符合工作流。

调整（URL 全部不变，仅重组导航与页面内容）：
- 导航按工作流重排：总览 → 分析流程 → 结果中心 → 专项分析 → 数据与库
  → 公共病毒组 → LOGAN 溯源 → 设置 → 手册（更名三处）。
- 总览"分析工作流"入口按 建库→分析→结果→专项→溯源 排序。
- 结果中心新增"专项结果入口"卡：聚合共线性比较图（gb_collections）与
  LOGAN 溯源报告（logan jobs），一页可达全部结果面。
- 专项分析分两区：单步工具 / 参考数据与比较分析。
- 细节：.btn 补 text-decoration:none（聚合入口链接按钮带下划线）。

## 七、公共数据闭环 + 项目批处理（2026-09-05 下午）

新增两大能力（对应路线图第 7、8 项）：

### 公共数据下载（/download，导航"数据下载"）
- **解析双通道**：SRR/ERR/DRR → ENA filereport API（直接 FASTQ.GZ + 字节数）；
  CRR → NGDC 搜索页 → browse 详情页提取下载链接（cncb/big 双镜像按主站去重，
  ftp 统一改写 https）。CRA 项目级编号不支持，需先取得 CRR 列表。
- **引擎**：平台根目录内置 aria2c 1.37.0（-x4 -s4 -c 断点续传，解析进度百分比）；
  缺失时回退内置 HTTP（Range 续传，经 .aria_part 临时文件 + 完成后改名，
  避免 safe_open 对 .gz 后缀二次压缩）。并发默认 2（NGDC 防封控，吸收自
  MMPV-RNA public_metadata_pipeline/gsa_sra.down.py）。
- **安全**（Mimosa 多轮拦截后的最终形态）：出站 URL 逐次校验（仅 http/https、
  主机白名单、解析 IP 非私有/保留、重定向逐跳校验）；落盘路径全部经
  check_path；**写入一律 safe_open**（代码库强制约定：raw open 变量路径
  写入会被扫描器拦截；读取可原生 open）。
- **完整性**：ENA 字节数比对 + gzip 魔数/抽样解压（md5 串仅存档展示，
  不做弱哈希运算——Mimosa 禁 hashlib.md5）。
- **.sra→FASTQ**：fasterq-dump（平台根目录或 PATH）自动转换；未装则保留
  .sra 并在批次信息中提示。
- **独立进度窗口**：`/download?pop=1`（无导航精简视图，window.open 弹出）。
- **闭环**：批次完成后「🧪 进分析流程」→ 按 R1/R2 配对建样品（项目标签）→
  可选直接入批处理队列。已实测：ERR7586041（SARS-CoV-2，2×8.8MB）下载
  60s 完成、校验通过、队列自动跑完 13 阶段全 done。

### 项目管理与批处理队列
- **项目标签**：input.json 增 project 字段；/api/samples 返回；
  分析流程页样品列表显示 🏷 项目 + 下拉筛选。
- **批量导入**：分析流程页左栏"批量导入"卡，TSV（样品名/R1/R2）逐行建样，
  可选建后自动入队。
- **批处理队列**：SampleQueue 顺序执行（同一时间仅跑一个样品，防资源打满），
  状态持久化 tasks/queue.json；页面队列面板 4s 轮询；无 stages 时按
  "依次运行剩余步骤"语义自动补全；运行中条目不可删（先取消任务）。
- **避坑记录**：本模块开发中被 Mimosa 反复拦截 6 次——最终结论是三条硬规则：
  ① 出站请求必须"白名单 + 解析 IP + 重定向逐跳"三层校验；② 写文件一律
  safe_open（raw open 变量路径=拦截）；③ hashlib.md5 不可出现（完整性
  校验用"字节数 + gzip 抽样"替代）。后续写网络/文件类新模块请直接按此写。

## 八、.sra 转换引擎选型：sracha（2026-09-05 晚）

对比结论（针对 GSA .sra → FASTQ 的本地转换需求）：
- **xsra（ArcInstitute）不选**：CI 只测 ubuntu/macOS，从未支持 Windows；
  依赖 ncbi-vdb-sys（C 库捆绑编译），README 自述"构建系统相关、不可移植"。
- **sracha（rnabioco）选定**：纯 Rust、零 C 依赖、零 unsafe，单文件静态
  二进制。**2026-09-05 起改用官方 0.7.0 Windows 版**（bin/sracha.exe，
  CLI 与旧版兼容：fetch -O/-f/-q/--no-progress/--prefer-ena、
  fastq -O/-t/-f/-q、split-3 数字后缀、默认 gzip level 1 均保留；
  SRR28588231 实测 fetch 24s / fastq 0.2s，产物命名 `_1/_2.fastq.gz` 一致）。
- **历史：本地 0.6.0 Windows 补丁版**（已退役，备份于
  bin/sracha-0.6.0-local-patch.exe）：官方早期 release 只发 linux/macOS，
  曾自行在 Windows 编译（cargo 1.98 + MSVC）；唯一补丁是
  `crates/sracha-core/src/download/mod.rs` 的 Unix `write_all_at` →
  Windows `seek_write`（OVERLAPPED 定位写）。补丁源码曾存于
  thirdparty/git-repo/sracha-rs（该目录 2026-09-06 已清理删除，
  官方 0.7.0 后无需本地补丁）。
- **性能实测**（SRR28588231，23MiB，66220 spots/132440 reads）：
  sracha fastq → 0.26s 输出 `_1/_2.fastq.gz`（gzip level 1），
  命名与 fasterq-dump/ENA 惯例一致，直接对接 ready_files 配对。
- **平台接入**：`sra_convert_engine()` 优先 sracha.exe（bin/，探测已兼容旧根目录布局）→
  回退 fasterq-dump；下载批次收尾自动转换，并把 .sra 条目替换为产物
  FASTQ 条目（修复过 ready_files 看不到产物的 bug）。
- **附带修复**：cancel 落在解析阶段时 resolver 会把 cancelled 覆盖回
  downloading 并并发触发转换——加状态守卫（仅 resolving 才转 downloading）。
- sracha 还自带 `get/fetch`（直连 NCBI 下载 .sra，URL 模式
  sra-pub-run-odp.s3.amazonaws.com/sra/{acc}/{acc}），未来若要
  "NCBI 直下 .sra" 可用它；当前 ENA 直下 FASTQ 路径更快更省，暂不启用。

## 九、下载引擎三级回退（aria2c → 内置 HTTP → sracha）

sracha 本身是"下载+转换"一体工具：`fetch` 走 NCBI S3
（sra-pub-run-odp.s3.amazonaws.com/sra/{acc}/{acc}）+ 可 `--prefer-ena`
优先拿 ENA 预生成 FASTQ.GZ（无则自动回落 NCBI .sra，由收尾链转 FASTQ）。
自带断点续传与 MD5 校验。

- 适用范围：仅 INSDC run 编号（SRR/ERR/DRR）；GSA CRR 与任意 URL 不适用
  （NCBI 无此数据），这两类维持内置 HTTP 回退。
- 接入点：`_download_file` 中 aria2c/内置 HTTP 均失败后，若条目 acc 匹配
  `[SED]RR\d+` 且 sracha 存在 → `_dl_sracha`。产物识别：acc.sra →
  条目 out 指向 .sra（收尾转换链接管）；acc*.fastq.gz → 条目替换（同
  转换链逻辑）。verified=True（sracha 已做 MD5）。
- 实测：禁用 aria2c + 主 URL 404（白名单主机）→ 自动 sracha 回退，
  ENA 三个文件全部取回、配对正确。
- 引擎链全貌：下载 aria2c → 内置 HTTP → sracha fetch（仅 INSDC）；
  转换 sracha fastq → fasterq-dump。

## 十、BioAider 展示结构借鉴落地（2026-09-05 深夜，6 项全做）

数据流导向范式保持不变，落地 6 项展示增强（详见 BIOAIDER_REFERENCE.md 第五节）：

1. **专项分析目录侧栏**：`.tools-layout` 双栏 + 粘性 `.toc`（MENU 锚点跳转，
   scroll-margin-top 已设），组内条目覆盖 ①-⑥/序列查看器/最近运行。
2. **常用工具收藏**：工具卡标题 ⭐（localStorage `vp_fav_tools`）；
   总览页"⭐ 常用工具"条（favSection，无收藏时自动隐藏）。
   工具注册表 TOOL_REGISTRY 在 app.js（图标/中英名/href，跨页共享）。
3. **拖拽文件上传**：所有 `.filerow` 输入框接 drop 事件 → `/api/upload`
   流式落盘到 `uploads/`（重名加序号不覆盖）→ 自动填路径。
   注意：浏览器出于安全不给完整路径，所以方案是"拖拽=上传到平台再引用"。
4. **工具卡内嵌运行状态**：`injectStageLogs` 扩展 TOOL_LABELS 匹配
   （工具·/Tool·/NCBI/GenBank/同属 前缀）→ 各工具卡内 `#toolrun-<key>`
   容器（状态徽标+进度条+日志尾+取消+结果预览）。
5. **深色主题**：CSS 全量令牌化（surface/input 令牌补齐）后，
   `html[data-theme=dark]` 覆盖调色板；设置页 浅色/深色 分段切换
   （localStorage `vp_theme`）；12 个模板 head 加防闪脚本
   （注意：Flask 非 debug 模板有缓存，改模板要重启服务才可见）。
6. **轻量序列查看器**：`/api/seqview` 流式统计（FASTA/.gz，序列数/总碱基/
   GC%/简并%，分页 300bp 预览，2 万条封顶）+ 专项分析"📄 序列查看器"卡
   （svPath + 分页器）。

实测截图：tools_sidebar_fav / home_favstrip / settings_dark / tools_dark /
seqview / toolrun_inline（.zcode/ui_shots/）。
最终回归：12 页面 + 6 API 全 200；selfcheck 全绿。

## 十一、RdRP 模块移除 + trimAl 开关（2026-09-05 深夜，用户确认）

- **⑤ Open-Virome（RdRP/palmdb）彻底删除**：STAGE_ORDER/名称表/分组/权重/
  估时/PIPELINE_STAGES/STAGE_DEPS/STAGE_OUTPUTS/STAGE_VIEW/汇总函数（中英）
  全部移除；viz 报告章节、main.py CLI（cmd_virome + 子命令 + selfcheck 清单）、
  app.py 工具④的 rdrp 佐证块、tools.html 复选框、Virus_Platform_Core/virome.py 模块文件均删。
  README 同步（功能表/CLI/目录树/整章）。
  注意：导航"公共病毒组"（Open-Virome 雷达网页）是独立数据挖掘入口，保留；
  历史样品的 09_virome 目录已于 2026-09-08 用户确认后彻底删除
  （ERR7586041 / _archive:E2E-VERIFY / REGRESS 三处，各 5 文件）。
- **trimAl 开关**：⑦卡片新增 do_trim 复选（默认开）。关闭后直接用
  aln.fasta 建树（跳过清剪，日志明确提示）；开启时保持原行为（automated1
  + 过度修剪回退）。全链打通：卡片 → collectParams → _analysis_kwargs
  （_chk 默认 True）→ run_analysis(do_trim) → build_phylo(do_trim)。
  设置页"分析默认参数"新增 do_trim（共 18 项），可改全局默认。

## 五、模块化架构（2026-09-05 深夜改造）

三层接入点全部声明式，新增功能/调整顺序不再改核心代码：

1. **导航栏组件化**：所有页面 `{% include "_nav.html" %}`（唯一来源），
   当前页高亮由 `request.path` 自动判定。改导航 = 改 `_nav.html` 一处
   + i18n.js 两行。
2. **工具箱注册表（app.py `TOOL_REGISTRY`）**：
   `{'tool': {'title', 'title_en', 'need_db', 'job'}}`，
   job 构造器 `_tool_job_<name>(ctx)` 返回 `job(log, prog, cancel)`；
   ctx 提供 `p / run_dir / threads / db_virus / req / opt`。
   新增工具 = 实现一个函数 + 登记一行 + 前端卡片。
3. **管道阶段注册表（Virus_Platform_Core/pipeline.py `STAGE_REGISTRY`）**：
   `{key: {'deps': [...], 'fn': _stage_xxx}}`，阶段函数签名 `(C)`，
   通过 `C`（_Ctx）读写跨阶段产物（`C.cur_r1 / C.host_stats / C.vs / C.asm`）。
   执行器 `_stage_execution_order()` 对已选阶段做**稳定拓扑排序**——
   改变阶段顺序/插入新阶段只改注册表；未选中的依赖不补跑，
   沿用断点衔接（阶段函数读上一轮产物摘要）。
   `STAGE_DEPS` 兼容导出给管道页卡片解锁。

约定：
- 阶段函数只经 `C` 传数据，禁止模块级可变状态；输入解析（如
  `_assembly_inputs` 的 病毒reads→去宿主→原始 回退链）放在消费阶段内。
- 工具 job 内新增产物请写入 `ctx.run_dir` 并放进返回 dict（前端
  「文件」标签自动列出）。

## 十二、宿主去除独立模块化（2026-09-06，用户要求）

- **导航不再跳管道**：侧栏「宿主去除与序列提取（kunpeng 宿主库分类）」原
  `href: '/pipeline'`，点击落在自动化分析流程页，用户要求改为独立模块。
  现在 NAV_GROUPS 指向新页 `/hostremoval`（`_PATH_TO_GROUP` 归入 sample 组，
  二级侧栏高亮沿用 request.path 精确匹配）。
- **独立页** `webapp/templates/host_removal.html`：输入类型（双端/单端）、
  R1/R2 选择、置信度、线程、可选自备宿主库目录（留空=平台库），
  库状态徽章复用 `loadDbs()` 的 `#hostStat`。
- **后端**：TOOL_REGISTRY 新增 `hostremoval`（`_tool_job_hostremoval`），
  复用 `Virus_Platform_Core.host_removal.remove_host`（run_dir 充当 sample_dir，
  产物在 `<run>/01_host_removal/`：kept_R1/R2.fastq.gz + stats.json +
  分类报告）。启动前校验宿主库就绪（`Virus_Platform_Core.kunpeng.db_ready`），未就绪 400。
  样品管道的 ① 宿主去除阶段不受影响，两处共存。
- **前端匹配**：app.js `TOOL_LABELS` 增加 `hostremoval: ['宿主去除与序列提取',
  'Host removal']` → 模块卡 `#toolrun-hostremoval` 内嵌状态/结果；
  注意任务名含「工具·」前缀才参与 TOOL_LABELS 匹配，与管道阶段
  `① 宿主去除`（STAGE_LABELS，进 `#log-host`）不冲突。
- **回归**：py_compile 通过；test client `/hostremoval` 200 且侧栏正确指向；
  合成 50 对 reads 端到端跑通（任务 done、kept=50/50、stats.json 正确，
  随机序列不命中宿主库属预期），测试产物已清理。

### 十二（续）：其余管道入口全部独立模块化（2026-09-06，用户确认）

NAV_GROUPS 中剩余 4 个 `href: '/pipeline'` 条目全部改为独立页，
`_PATH_TO_GROUP` 同步归组，侧栏不再有任何跳管道的入口（管道页仅从
顶部导航 / 总览进入）：

| 模块 | 页面 | tool key | 复用后端 |
|------|------|----------|----------|
| 样品创建 / 批量导入 | `/samples` | —（纯前端，复用 `/api/pipeline/create` `/api/samples` `/api/queue/add`） | — |
| 宿主预测（ICTV 级联） | `/hostpredict` | `hostpredict` | `Virus_Platform_Core.host_analysis.predict_hosts` |
| ORF 预测 / 功能注释 | `/orf` | `orf` | `Virus_Platform_Core.orf.predict_orfs` + `Virus_Platform_Core.orf_annot.run_orf_annotation` |
| 基因组图谱 | `/genome` | `genoplot` | `gbdraw_plot.run_genome_plots` / `dfv_plot.run_dfv_plots`（fasta_in/ann_in 原生 standalone 模式） |
| 引物设计 | `/primer` | `primer` | `Virus_Platform_Core.primer.design_primers` |

实现约定（与 hostremoval 相同）：
- **伪样品目录**：stage 函数都是 sample_dir 口径，tool job 在 run_dir 下
  摆出最小布局再调原函数——orf/hostpredict 造 `03_assembly/`
  （summary.json+viral_contigs.fasta+contigs.filtered.fasta）；
  引物 conserved 模式造 `05_phylo/G1/aln.fasta`+summary.json，plain 模式
  造 `03_assembly/viral_contigs.fasta`。
- **分类表归一**：hostpredict 输入自动识别列名；工具④ metabuli 风格
  （taxid/taxon）自动转管道③口径（kunpeng_flag/kunpeng_taxid）。
- **引擎回退**：genoplot auto=gbdraw 优先、缺则 DFV；显式指定缺引擎时报
  可读错误。
- **TOOL_LABELS 新增** hostpredict/orf/genoplot/primer（任务名带「工具·」
  前缀才参与匹配，与管道阶段 `⑥ ORF 预测`/`⑧ 引物设计` 等无冲突）。
- **坑**：`extract_viral_contigs` 除 viral_contigs.fasta 外还需要
  `contigs.filtered.fasta` 兜底（首次端到端即被拦下），伪目录两件都要写。
- **回归**：16 页全 200；四工具合成数据端到端全 done（ORF 2 contigs/14
  ORFs、引物 2 对、宿主预测③④两种格式、图谱 gbdraw 出图）；内联 JS
  node --check 通过；测试产物已清理。

## 十三、检索页修复 + 全站按钮防重复 + 任务硬停止（2026-09-06 凌晨，用户反馈）

- **「开始检索」无反应根因**：meta.html 的 doSearch 调用全局 `val()`，
  但该函数不存在（app.js/i18n.js 均未定义，页面也没本地副本）→
  ReferenceError，fetch 根本没发出。download.html / submit.html 同病。
  修复：app.js 增加全局 `val(id)`。
- **任务硬停止（真停）**：原先 TaskManager.cancel 只置 Event，无任何任务
  检查，外部工具子进程照跑。现在 Virus_Platform_Core/utils 新增线程级 `task_bind /
  task_check_cancel` 上下文，run_cmd / run_cmd_redirect 启动的子进程自动
  注册到任务 rec['procs']；TaskManager.cancel 置位事件 + taskkill /F /T
  杀进程树；_run 结束统一解绑。取消后的任务 status=cancelled、
  error=「任务已停止（用户取消）」。单元验证：run_cmd 起长 ping →
  cancel → 进程树被杀、无残留。
- **按钮防重复点击**：app.js 新增 lockBtn / withBtn / watchTaskBtn
  （锁定按钮直到后台任务终态）。全站接线：meta（开始检索/提取/转入下载，
  任务卡新增「⏹ 停止」）、tools 工作台（runTool 五卡 + ncbi/gb/比较，
  _watchTask 扩展 btn 参数）、build 三按钮、管道页创建/批量、五个独立
  模块页全部运行按钮。app.css 增加 .btn:disabled/.btn-busy 禁用态。
- **回归**：py_compile 通过；16 页 200 + 接线抽查通过；全部模板内联 JS
  node --check 通过（help/tools 的 {{ tojson }} Jinja 表达式误报除外）；
  硬停止单元测试通过。启动服务后生效。

## 十四、检索页任务卡不显示 + 日志不流动修复（2026-09-06 凌晨，浏览器实测）

- **任务卡从未显示的根因（历史遗留）**：meta.html refreshTasks 渲染用
  `mine.map(t => …)`，参数 `t`（任务对象）**遮蔽全局 i18n 函数 `t()`**，
  回调内 `t('c.st.running')` 抛 TypeError 被 `catch(e){}` 静默吞掉。
  浏览器实测复现 → 参数改名 `task`。该页任务卡自上线以来从未渲染过。
- **运行日志空白根因**：engine_cmd 以 `python search_engine.py` 起子进程，
  stdout 接管道默认**块缓冲**，print 全攒在缓冲区。修复：源码模式加
  `-u`；_meta_stream_task 子进程 env 注入 PYTHONUNBUFFERED=1（冻结分发
  也覆盖）；Popen 登记进任务进程表（task_register_proc，取消即硬停止）。
- **停止按钮生效验证**（真实浏览器）：检索 7323 runs 任务 → 卡内
  「⏹ 停止」→ 立刻终止，日志尾 `[ERROR] 任务已停止（用户取消）`；
  状态映射补 `cancelled → 已停止`（原先误显示为失败）。
- 回归：py_compile 通过；live 服务重启后 /meta 200；任务卡 + 实时日志 +
  停止 全链路浏览器验证通过。

## 十五、全站任务日志审计：上屏 + 落盘（2026-09-06 凌晨，双代理并行审计）

**落盘（用户要求：日志保留到输出目录，重启/出错可查）**：
- TaskManager.start 新增 `log_file` 参数：任务每行日志（带 [HH:MM:SS] 前缀）
  同步追加写盘；缺省落 `logs/tasks/<任务名>_<时间>_<tid>.log`（文件名支持中文）。
- 显式路径：工具任务 → `tool_runs/<run>/run.log`；管道运行/样品队列 →
  `results/<样品>/logs/run_<ts>.log` / `queue_<ts>.log`；样品分析 →
  `results/<样品>/logs/analyze_<ts>.log`；公共检索 →
  `meta_search/<项目>/search/run.log`；元数据提取 → `.../info/run.log`。
  tasks/<id>.json 增记 log_file 路径。至此全部 15 类任务日志均落盘
  （此前只有 LOGAN 批量自带 run.log，其余重启即丢）。

**上屏（审计发现并修复）**：
- /api/tasks 日志尾 5 → 30 行（此前阶段卡/工具卡只能看到 5 行）。
- app.js：日志框运行中自动跟随尾部（autoscrollLogs，阶段卡/工具卡/任务卡
  统一生效）；refreshTasks 渲染体包 try/catch（单次渲染异常不再打断轮询）；
  断连横幅自愈探针（无 #tasks 的页面横幅也能自动消失）；
  STAGE_LABELS 补 `subsample`（子采样阶段卡此前永远无实时日志）。
- logan.html：任务名匹配正则补英文前缀（英文界面下批量任务完全不可见）。
- download.html：refresh 轮询链一次瞬时失败即永久停止 → 重排移入 finally。
- build.html：`</main>` 误入文件选择弹窗内部的标签嵌套修正。

**已知遗留（低优先，未改）**：多阶段管道任务名会同时命中链上多张阶段卡
（日志串卡）；下载批次无过程日志（aria2c 百分比被丢弃，仅 batch.json 状态）；
若干良性变量遮蔽（`const t =`）与硬编码中文待清理；settings 页"打开目录"
按钮全部只开平台根目录。

回归：py_compile + node --check + 16 页 200 + ORF 端到端（任务卡 14 行日志、
run.log 带时间戳落盘）+ 默认路径单测全部通过。

## 十六、三项遗留修复（2026-09-06 凌晨，用户点名 + 并行代理实施）

1. **多阶段管道日志串卡**：injectStageLogs 阶段卡注入从"任务名标签匹配"
   （多阶段任务名含链上每个阶段标签 → 整条链每张卡都塞同一任务）改为
   **按后端 prog 上报的当前阶段（task.stage）精确归属**——日志只进正在跑
   的那张卡，跑完自动挪到下一阶段卡；stage 未上报的启动瞬间退化为名称
   匹配且仅单阶段命中才注入；另按 curSample 过滤，其它样品的管道任务不再
   串到当前样品的卡上。附带修复：队列·样品 / 样品分析 任务此前不进任何
   阶段卡，现在同样按 stage 正确归属。
2. **下载批次过程日志**（并行代理实施，仅改 Virus_Platform_Core/public_data.py +
   download.html）：DownloadManager 新增 `_blog`（[HH:MM:SS] 前缀追加写
   `downloads/<bid>/batch.log`，OSError 静默）；创建/解析成功失败/开始下载/
   回退链失败原因（区分取消）/完成/转换/取消/重试 全程落盘；
   `list_snapshots()` 每批带 `log_tail`（40 行）；下载页每批卡片新增
   「📋 日志」展开按钮（展开状态跨轮询保持）。
3. **变量遮蔽清理**：app.js（watchTaskBtn snap、toolName/renderFavStrip
   tool）、tools.html（showRows ty、三处 watcher snap）、logan.html
   （bt/snap/task/prev 共 6 处）、results.html（tr）全部改名；app.js 中
   `let/const t =` 已清零。results.html 原有"良性"遮蔽（回调内 t 属性访问
   + 回调外 t() 混用）消除。

回归：py_compile（app/public_data/config/utils）+ 全部模板内联 JS
node --check + 16 页 200。服务重启后生效。

## 十七、全站结果表格分页（2026-09-06 凌晨，用户要求：每页 20 行 + 翻页）

- **共享分页器**：app.js 新增 `PER_PAGE=20` + `pagerHtml(page, pages, fnExpr)`
  （fnExpr 支持 `{p}` 占位，适配按批/按样品的翻页函数；省略号折叠长页码）。
- **meta 检索/元数据表（服务端分页）**：/api/meta/table 接受 page/per_page
  （默认 20，上限 200），返回 page/pages/total；废除原"仅显示前 500 行"
  截断（15851 行实测 → 793 页正确翻页）；Run 勾选跨页保留（SEL 按表
  身份重置）。
- **客户端分页 20 行**：工具④病毒 contigs 表（vcGo）、下载批次文件列表
  （dlFileGo，页码跨 2s 轮询保持）、LOGAN contigs 表（ctGo + ctSel Set
  勾选跨页保留，全选=全部页）、阶段文件预览 TSV/CSV（pvGo，废除 500 行
  截断）。
- **缓存版本号补齐**：meta/download/submit 三页 script 标签补 ?v={{asset_v}}
  （此前浏览器会拿旧缓存 app.js，本次及以往前端修复可能"不生效"即此因）。
- 序列查看器/引物/BLAST/CDD 等小表已自带分页或行数封顶，不重复处理。
- 回归：py_compile + 全模板 JS node --check + 16 页 200 + meta 分页端到端。

## 十八、导航调整 + 默认样品命名 + 跨模块文件交接（2026-09-06 凌晨，用户要求，双代理并行）

- **导航**：「公共数据下载」从数据资源组移入样本处理组（首位）——
  工作流顺序：拿数据 → 建样品 → 质控/去宿主 → 分析。_PATH_TO_GROUP 同步。
- **默认样品命名**：新增 `_default_sample_name()`，不填样品名时按文件名
  推导：去 .fastq(.gz) 等扩展名 + 循环剥技术后缀（_R1/_R2/_1/_2/_001/
  _L001，仅下划线数字避免误删 -01 真名）。/api/pipeline/create 与
  /api/analyze 两处旧 `split('_')[0]`（Lycium_barbarum 会变成 Lycium）
  统一替换。samples.html 补自动命名规则提示。
- **跨模块文件交接（并行代理实施）**：app.js 新增 sendTo/takePrefill
  （sessionStorage 暂存 → 目标页预填后销毁）；下载页每个完成文件行加
  「🧾 样品 / 🚫 去宿主 / 🧹 质控 / 🦠 鉴定」四按钮，按 acc 自动配对
  R1/R2（支持 _1/_2 与 _r1/_r2 形态）；hostremoval/samples/tools 三页
  支持预填。反斜杠路径统一转正斜杠避免 JS 转义破坏。
- **results 深链**：compare 组两条 /results 改 /results#msa、#sdt，
  结果页卡片补 id 锚点。
- **审计遗留（低优先待办，见报告）**：产物 files 页签加"发送到模块"
  按钮（toolrun files / 模块页最近运行）；t-contigs 独立成页；
  /api/analyze 孤儿端点去留；_safe_sample 双实现合并；管道阶段产物
  补下载链接。
- 回归：py_compile + 全模板 JS + 16 页 200 + 命名 7 用例 + 建样品端到端。

## 十九、文件浏览「此电脑」盘符修复（2026-09-06 上午，用户报告）

- 现象：文件浏览对话框停在「此电脑」盘符列表，点 C:\ / D:\ / E:\ 一律
  alert「无法打开目录」。
- 根因：`_joinBrowse(cwd, name)` 在 cwd='此电脑' 时仍执行 `cwd + name`，
  盘符被拼成 `此电脑D:\` → 服务端 400。盘符本身就是绝对路径，应直接返回。
- 修复：`_joinBrowse` 对 '此电脑' 层级直接返回子项名；盘根（尾反斜杠）
  直接拼接；其余 / 连接。node 单测 5 用例通过。

## 二十、contig 分类结果报告（对标 32-server）+ 下载失败排查（2026-09-06 凌晨）

- **文件下不下来**：实测 /tool_runs/<run>/<file> 路由 200 且内容完整——
  用户遇到的「无法从网站上提取文件」发生在服务重启窗口期（多次重启期间
  点了下载），重试即可，非代码问题。
- **contig 分类结果报告（对标 32-server 的 Metabuli 结果页）**：
  Virus_Platform_Core/viz.py 新增 `build_contig_report(run_dir)`：解析运行目录 kreport →
  分类谱系桑基图（root→界→…→种，按片段数）+ 分类旭日图 + 分类表
  （级别/分类单元/TaxID/%/片段数）+ contig 明细表（近完整绿色）+ 产物
  下载按钮；离线 plotly.min.js，固定浅色背景。写入 <run>/report.html。
- 端点 GET /api/tool/report?run=<name>：生成（每次重生成）并 302 到
  report.html；tools.html 工具④卡片新增「📊 分类报告」按钮。
- 浏览器实测：桑基/旭日 plotly 渲染正常、分类表 59 行、下载按钮齐全。

## 二十一、全站结果表格排序（2026-09-06 上午，用户要求）

点击表头切换升/降序（▲/▼ 指示，再次点击同一列反向）：
- **meta 检索/元数据表（服务端排序）**：/api/meta/table 接受 sort/dir；
  数值列全为数字时按数值排（pd.to_numeric），否则字典序；排序后再分页，
  翻页保持排序。前端表头 class=sortable，列下标定位（避免列名转义问题）。
- **客户端排序**：工具④病毒 contigs 表（contig/taxon/length/genus_avg_len/
  ratio/score）、LOGAN contigs 表（contig/length/species/family，勾选
  跨页跨排序保留）、阶段文件预览 TSV/CSV（任意列）。数字优先 parseFloat、
  否则 localeCompare('zh')。
- 样式：th.sortable（手型 + hover 提亮）。
- 未排（小表/编辑器）：提交网格（可编辑）、引物/CDD/BLAST 结果（≤20 行）、
  队列表。回归：py_compile + 全模板 JS + 16 页 200 + meta 排序端到端
  （asc/desc 首行不同且 asc 与 sorted 一致）。

## 二十二、工具页布局重排 + 结果中心整合（2026-09-06 凌晨，用户反馈，双代理并行）

- **工具卡布局重排**：toolRunHtml 从「结果/日志/文件」三标签切换改为纵向
  平铺：状态+进度条 → 📜 运行日志（常显、自动滚尾）→ 📦 结果 → 🗂 产物
  文件。refreshTasks 容错：无 #tasks 的页面跳过面板渲染但通知/
  injectStageLogs 照常。
- **工具页去乱**：删除底部「🗂 最近专项运行」与「⏳ 任务」区块；
  空 #moduleTree 左栏移入 main 顶部改横向切换条（消除空白列）。
- **独立模块页（5 页）**：删除右列「任务」+「本模块最近运行」卡片，
  合并为单列全宽。
- **结果中心新增「🧪 专项分析运行」区块**：/api/tool/runs 的 30 条记录
  迁移展示，文件行可展开下载（/tool_runs/...?dl=1），contigs_ 运行带
  「📊 分类报告」按钮。
- **内嵌分类报告**：工具④卡片新增 #contigReport iframe 内嵌
  report.html（运行完成自动内嵌/切换运行自动内嵌/📊 按钮手动内嵌，
  去重防轮询闪烁）。**坑**：/tool_runs 全局 Content-Disposition:attachment
  会让 iframe 空白——改为仅 ?dl=1 时 attachment，默认 inline。
- **下载失败说明**：/tool_runs 路由实测 200 完整；用户遇到的
  「无法从网站上提取文件」集中在服务重启窗口期（下载进行中服务被重启），
  重试即可；现已加 attachment 头使浏览器下载行为更明确。
- 回归：py_compile + 全模板 JS + 16 页 200 + 浏览器实测（工具页单列
  无空白栏、卡内日志平铺、内嵌报告 6 个 plotly SVG + 347 行表格渲染）。

## 二十三、桑基/旭日对标 32-server 实现（2026-09-06 上午，用户提供本地管线源码）

对照 `C-host_classify/plant_virus_db_pipeline/9.metabuli`（metabuli_api.py +
metabuli_page.html，即 <DEMO-IP> 服务器同源代码）重写报告图表：
- **数据流一致**：kreport 行（name/rank/count/depth）内嵌为 KROWS JSON，
  页面 JS 渲染（32-server 同款：SANKEY_DATA + limitSpeciesPerGenus）。
- **过滤规则一致**：剔除 no-rank 层级与 unclassified 大灰流（可勾选显示）；
  「每属仅显示前 5 的种」开关（默认开，丢弃种的深层后代）。
- **渲染细节一致**：plotly 桑键横向 pad20/thickness25、重复边合并累加、
  固定配色表（root/Viruses/Riboviria/…）+ 默认蓝、链接淡蓝 0.15 透明度、
  字号 11；旭日图同数据源 depth 栈链。
- 旭日图用 plotly 替代服务器端 KronaTools（Windows 无 kronagraph），
  交互能力等价（点击下钻/缩放）。
- 坑位记录：占位符 __KROWS__ 在 head 末尾，需对 (head+tail) 整体替换。
- 回归：report 90KB、KROWS 内嵌、浏览器实测桑基 188 链接/旭日正常、
  无 unclassified 灰流。

## 二十四、报告树形分类表 + 宿主预测整合（2026-09-06 上午，用户要求）

- **分类表树形展示**：kreport 本身就是树（kunpeng 输出）——分类表按
  缩进深度渲染层级，有子节点的行显示 ▾/▸ 可点击折叠/展开全部后代。
- **宿主预测整合进报告**：若运行目录存在
  08_host_analysis/host_prediction.tsv，contig 明细表自动在 taxon 后
  插入「宿主(预测)」列（final_host + 置信度），并在桑基图前绘制
  「宿主预测统计」柱状图（按 final_host 类别计数）；无宿主数据时显示
  引导提示。
- **原地宿主预测端点**：POST /api/tool/hostpredict_run {run}——对既有
  contigs 运行目录原地做 ICTV 宿主预测（含工具④ metabuli 风格 TSV →
  ③ 口径归一），日志落 <run>/hostpredict.log。tools.html 工具④卡片新增
  「🧲 宿主预测」按钮（运行完成自动刷新表格与报告）。
- **viral_contigs API 合并 host 列**：/api/tool/viral_contigs 若存在
  host_prediction.tsv 自动并入 host（final_host (置信度)），
  工具④页面表格新增「宿主(预测)」可排序列。
- 实测：148 条 contigs 全部判定（如 TRINITY_DN71 → Plant (Medium)），
  报告含树形分类表 + 宿主列 + 宿主统计柱状图。

## 二十五、分类报告 1:1 对齐 <DEMO-IP>/metabuli（2026-09-06 上午，用户明确要求"别自己发挥"）

- 已核对：线上页面与本地 `C-host_classify/plant_virus_db_pipeline/9.metabuli/metabuli_page.html`
  完全一致（空白差异除外），全部照本地源码逐行移植。
- build_contig_report v4 全面重写，报告结构/交互与 32-server 结果页一致：
  1. Taxonomy Sankey（"Only top 5 species per genus" 开关；过滤 no-rank
     保留 root/unclassified；重复边合并累加；固定配色 CM + 淡蓝链接）
  2. Classification Table（Rank 彩色 no-rank 灰/std 深蓝、Taxon 按深度
     缩进 8px 且 std 加粗、TaxID 灰、% / Reads；440px 滚动盒）
  3. Virus Sequence Classification（"Total N contigs, M classified as
     Virus"；列 Contig + Realm→Species + Length + Score + E-value +
     Genus Avg Len + Analyze；近完整(0.8≤len/avg≤1.2)绿底/长度绿粗；
     genus/species 加粗；sortVirusTable 点击表头排序默认降序；
     Analyze 按钮 CDD/BLASTN/BLASTX/Primer 经 parent.runAnalyze 调
     工具④四件套）
  4. Krona Taxonomy Plot（"Show unclassified" 开关；Windows 无
     KronaTools，用 plotly 旭日图等价替代，位置/交互一致）
  5. Downloads 底部按钮组（各产物 ?dl=1 强制下载）
- 保留平台自有增强：宿主预测 host 列 + 宿主统计柱状图（有
  08_host_analysis 时）；无则提示引导。
- 工具②（identify_*，测序 reads）报告：Classified Sequences 表
  （Seq ID/TaxID/Score）+ 相同桑基/旭日/分类表；tools.html 工具②卡片
  新增「📊 分类报告」按钮 + identRun 输入 + 内嵌 iframe + 完成自动打开。
- 实测：contigs 运行（148 contigs/199 谱系节点）与 identify 运行（真实
  病毒参考序列）报告均正常。

补充（同日）：分类报告已从 iframe 嵌套改为**页面内联渲染**——
/api/tool/report_data 返回 JSON，tools.html 用自带 plotly.min.js 页内绘制
桑基/分类表/宿主统计；工具④的「🔬 Contig 深度分析四件套」表格与 contig
明细合并（每行 CDD/BLASTN/BLASTX/Primer 按钮 + 宿主列 + 表头排序 +
每页 20 行分页）；iframe 全部移除；宿主预测完成后表格与报告自动刷新。
工具②（测序 reads）同样支持（Classified Sequences 表）。

## 二十六、工具④报告区合并重构（2026-09-06 上午，用户反馈）

- 移除重复的「🔬 Contig 深度分析四件套」独立标题区；工具④卡片统一为
  一条流：运行分类 → 输出面板 → 📊 分类报告（桑基 / 分类表 / contig
  明细+四件套按钮 / 宿主统计 / 旭日图 / 下载按钮组）。
- 宿主预测统计不再绘图，改为**表格**（宿主类别 / Contigs 数）。
- 补上内联报告缺失的**旭日图**（plotly sunburst，点击下钻）与
  **Downloads 下载按钮组**（各产物 ?dl=1）。
- report_data 接口增加 downloads 清单（按运行类型生成、校验文件存在）。

## 二十七、Krona 旭日图改用 taxburst（2026-09-06 上午，用户指定 github.com/taxburst/taxburst）

- `pip install taxburst`（离线 d3 内嵌，无 CDN 依赖）。
- Virus_Platform_Core/viz.py 新增 `_taxburst_nodes(krows)`（kreport 累计行 → name/rank/count/
  children 节点树）与 `build_taxburst(run_dir, krows)`：生成 taxburst.html
  （仅已分类）与 taxburst_unc.html（含未分类）两个离线交互 HTML。
- report.html 的 Krona 区块改为 taxburst iframe（Show unclassified 开关
  切换两个变体）；taxburst 不可用时回退 plotly 旭日图（renderSun）。
- 工具页内联（renderSunC）同样优先 taxburst iframe（report_data 附带
  taxburst 标记并负责生成文件），无文件时回退 plotly。
- 实测：浏览器内 taxburst 正常渲染（Viruses / Count: 148 / 交互控件齐全）。

## 二十八、组导航改顶部横向切换条（2026-09-06 上午，用户选择）

- 全部 11 个带组导航的页面：左侧栏移除，`_group_nav.html` 重写为
  顶部横向切换条（🏠 组名 + 各二级功能 pill，当前页高亮，悬停显示说明）。
- app.css：.group-layout 改 block、新增 .group-topbar 样式；
  .group-toc 旧规则弃用（保留不删）。
- 条目本身不变（用户确认）。
- 回归：16 页 200、全部页 topbar 在/旧侧栏无。

## 二十九、全量自查报告（2026-09-06 上午，用户休息前最终检查）

13 项全量检查全部通过：编译（6 文件）/ JS 语法（全部模板）/ 16 页 200 /
侧栏（顶部切换条全覆盖，旧侧栏归零，无管道跳转）/ 无嵌套 iframe /
report_data 双模式 / meta 排序+分页（793 页）/ 日志落盘（run.log +
hostpredict.log）/ 下载响应头（?dl=1 attachment）/ 工具②报告按钮/
宿主预测按钮/树形分类表/旭日图/下载容器/sendTo 跨模块/report.html
（taxburst iframe + 客户端渲染 + Analyze 按钮）。

## 三十、UI 精修（2026-09-06 上午，用户要求）

- **Toast 改屏幕中央**：CSS `#toastBox` 从 `right/bottom` 改为
  `left:50%; top:40%; transform:translate(-50%,-50%)`，动画从平移
  改缩放，更醒目。
- **线程数入各工具卡参数区**：移除工具页顶部全局线程独立区；每张
  工具卡（fastp/identify/assemble/contigs）新增各自的「线程数」输入框。
- **min_hit_groups 暴露**：工具②鉴定卡新增「最小命中组数」参数
  （kunpeng -g），不再硬编码 2。
- **E-value 列**：viral_contigs API 和 report_data 均从 contig_blast.tsv
  合并最优命中 E-value。
- 回归：py_compile + 全模板 JS + 16 页 200 + 全部标记。

## 三十、粘贴序列输入（2026-09-06 上午，用户要求）

- 后端 POST /api/paste_input：粘贴文本 → 写 uploads/paste_<ts>.<ext>，
  支持 .fasta/.fa/.fna/.fas/.fastq/.fq/.tsv/.txt；FASTA 校验 > 开头、
  FASTQ 校验 @ 开头；空内容 400。
- app.js 新增 pasteSeq(inputId, ext)：弹出粘贴对话框（modal），确认后
  POST 写文件并回填 input 路径。全站各序列输入框旁加 📋 按钮。
- 覆盖：工具④contigs FASTA、工具②鉴定输入、工具①质控 R1/R2、
  工具③组装 R1/R2、管道页样品创建 R1/R2、宿主去除 R1/R2、宿主预测
  TSV+FASTA、ORF FASTA、图谱引物 FASTA/GenBank。

## 三十一、通用病毒参考库（ref-virus / RVDB）（2026-09-06 下午）

> **2026-09-10 更新**：RVDB 库（databases/virus/rvdb/，1.6GB Kraken2 库）与
> 构建入口已移除——病毒验证/蛋白注释两条链路只依赖 nnot/prot + nnot/cdd
> + nnot/hmm/pfam，与 RVDB 无关；保留 RefSeq 通用库（databases/virus/ref/）。
> 以下为该功能的实现记录，留作历史参考。

用户在 databases/virus_ref/ 补充：NCBI RefSeq Viral
（viral.1.1.genomic.fna.gz，1.9 万条）、RVDB C-RVDBv32.1（132 万条，
头格式 acc|GENBANK|ACC|desc）、RVDB_Taxon_Current.tab.gz（1100 万行
accession→taxid+谱系，覆盖 RefSeq/RVDB accession）。

新增 Virus_Platform_Core/universal_ref.py：
- build_universal_meta(source)：流式扫 RVDB_Taxon 表 + FASTA 内 accession
  交集 → virus_ref/universal/<source>_acc2taxid.tsv（复用缓存）。
- build_universal_db(source)：注入 |kraken:taxid|N → kunpeng build_db →
  独立库 databases/refvirus_db（~1 分钟建完）或 rvdb_db（大库）。
- 端点 POST /api/build_universal_db {source}；/api/dbs 增加 refvirus/rvdb
  就绪徽章；数据库构建页新增「🌐 通用病毒参考库」卡片（两按钮 + 状态）。
- 用法：分析时把工具②/管道的病毒库路径指向 databases/refvirus_db 或
  rvdb_db 即切换到通用参考；与植物病毒库并存可按样品选择。
- 实测：RefSeq 库端到端建库成功（12,105/19,625 有 taxid 映射，其余为
  MAP/WT 未有序列条目属正常），库文件齐全、/api/dbs ready=True。
  RVDB 库按钮已就位（构建约 132 万条，用户按需启动）。

## 三十二、kunpeng 三种建库方式全支持 + Kraken2 库转换（2026-09-06 下午）

用户下载 Kraken2 官方预构建病毒库（databases/k2_viral_20260626.tar.gz，
含 hash.k2d/opts.k2d/taxo.k2d/nodes.dmp/names.dmp）。kunpeng 三种建库
方式现状与本平台支持：

- **A. 从基因组下载建库**（build --download-dir）：本平台等同能力为
  数据库构建页 Taxonomy + 宿主库/病毒库/通用库（走 add-library+build-db）。
- **B. add-library 自备 FASTA**：已有（build_virus_db / build_host_db /
  build_universal_db）。
- **C. Kraken2 库转换（hashshard）**：新增 Virus_Platform_Core/kunpeng.convert_kraken2()：
  支持 .tar.gz 包或已解包目录；解包（自动定位 hash.k2d）→
  `kunpeng hashshard --db <k2目录>`（**就地转换**，输出在 k2 目录内）→
  移动 hash_*.k2d/hash_config.k2d/opts.k2d/taxo.k2d 到目标库目录 +
  复制 nodes/names.dmp。端点 POST /api/convert_kraken2 {tar,name,
  hash_capacity}；数据库构建页「🌐 通用病毒参考库」卡片新增
  「🔄 从 Kraken2 库转换」按钮 + k2Stat 徽章 + /api/dbs k2viral 条目。
- **坑**：hashshard 不能重复跑（hash_config 已存在即 panic）——重试前
  需清理 k2 目录内的 hash_*.k2d/hash_config.k2d；输出位置是 k2 目录
  而非目标目录，需自行搬运。
- 实测：k2_viral tar.gz → k2viral_db 转换成功（/api/dbs ready=True）；
  用库内 RefSeq 真实序列分类冒烟：C t1 265522（Ichnoviriform fugitivi），
  kreport 层级完整（root→Viruses→…→Species）。

### 三十一（补）：refvirus 映射覆盖率 61.7% → 100%（2026-09-06 下午）

- **用户质疑 12,105/19,625 不合理 —— 属实**。根因：RVDB_Taxon_Current 表
  只含 RVDB 自身收录集，RefSeq 大量官方基因组（噬菌体/卫星等，7,520 条）
  不在其中。
- **修复（三源优先级）**：
  0. **NCBI 官方 nucl_gb.accession2taxid**（权威，覆盖全部 RefSeq/GenBank；
     aria2c 8 线程下载 2.7GB gz 到 virus_ref/universal/（80 分钟→约 12 分钟）；
     流式解析 4 亿行按 want 过滤）
  1. RVDB_Taxon（12,105）2. Kraken2 库包 seqid2taxid（18,567）兜底。
- 服务器 <DEMO-IP> 无 /home/USER（已核实；其 taxonomy 目录仅
  dmp，无 accession2taxid）。
- 实测：NCBI 官方源命中 19,625/19,625 = **100%**；库重建（24s，
  hash 0.71GB）；分类冒烟：原无映射的 NC_010393.1 Phage Gifsy-2 正确
  分类到种级 Essonnevirus Gifsy2（C t1 129862，kreport 全层级）。
- 注意：nucl_gb.accession2taxid.gz 为 NCBI 周更文件，DATA_VERSION 里
  可记录下载日期；过期只影响新增 accession，已有映射不受影响。

## 三十三、246 服务器三库构建 + 拉回本地（2026-09-06 下午，用户指定 246）

- **246**（zhangwenda@<HPC-IP>，256 核/629GB/12TB）：kunpeng 0.7.11
  已装（~/.cargo/bin/kun_peng）；源数据由本地上传（RVDB 919MB + RefSeq
  170MB + Taxon 表 102MB + Plant_Virus complete_ref + NCBI 映射用服务器
  现成的 /home/USER/database/taxonomy/nucl_gb.accession2taxid.gz，
  2026-03 版）。
- scripts/build_virus_dbs.py（纯标准库独立脚本）上传 246 构建三库：
  - plant_db：5,772 条，覆盖率 100%（NCBI 源），44s
  - refvirus_db：19,145/19,625 = 97.6%（3 月版 NCBI 映射缺 480 条新序列；
    上传本地 9 月版映射后复用重建 → 100%）
  - rvdb_db：1,321,456/1,321,608 = 100%，991s，hash 1.55GB
  - 坑：kunpeng build-db 需要库目录内 taxonomy/nodes.dmp+names.dmp
    （246 脚本从 /home/USER/database/taxonomy/ 复制）。
- 三库 tar 包拉回本地解压至 databases/{plant_db,refvirus_db,rvdb_db}，
  db_ready 全 True；分类冒烟三库各自参考序列全部种级命中
  （Janusivirus portis / Essonnevirus Gifsy2 / Salisharnavirus britensis）。
- 246 端三库保留在 /home/USER/kunpeng_dbs/（含 tar 包），可独立使用。
- 本地库全家福：virus_db（植物 55MB）+ plant_db（44MB）+ refvirus_db
  （713MB，100% 映射）+ rvdb_db（1.65GB，100% 映射）全部 ready。

## 三十四、外部审查修复（2026-09-06 下午，4 路并行审查 + 用户要求修复）

**已修复（高危 4 + 中危 6 + 低危 2）**：
1. 🔴 app.py /api/tool/viral_contigs NameError（run_dir 未定义，接口 500，
   contig 分类表全挂）——系 E-value 合并补丁引入，改回 _tool_runs_root 路径。
2. 🔴 Virus_Platform_Core/config.py tool() 放行 bundled: 哨兵（冻结分发 gbdraw 引擎恢复）。
3. 🔴 /api/seqview 允许平台外绝对路径（只读统计；与文件浏览对话框对齐）。
4. 🔴 app.js watchTaskBtn 轮询加 10 次不可达上限（服务重启后按钮不再永久
   卡「运行中」）。
5. 🟡 platform.json / queue.json 原子写（tmp + os.replace），断电不再丢配置。
6. 🟡 样品删除守卫改用 _task_matches_sample（前缀匹配对不上任务名的漏洞）。
7. 🟡 run_cmd timeout 看门狗线程（阻塞读 stdout 时也能超时杀进程）。
8. 🟡 工具运行 threads 钳制（1 ~ cpu*4）。
9. 🟡 esc() 补单引号转义；plotly.min.js 双加载去重（带 ?v=）；
   universal_ref NCBI 下载改 urlopen timeout=60 + 分块写。
10. 🟢 results.html checkbox data-i18n 误用改 span；暗色模式
    badge/sbadge/tstat 三组配色固定深底亮字；宿主去除 kept=0 早停；
    ORF 0 contigs 早停；selfcheck 失败退出码 1。

**核实无问题**：pyrodigal 3.7.1（v3 坐标，审查担心的 v2 移码不存在）；
STAGE_DEFS 重复已不存在；路径穿越防护/subprocess 参数/safe_open 均完好。

**遗留（择机，记录在案）**：orf_annot 无命中 ORF 被丢弃（n_orfs 语义，
需产品决策）；MAFFT/trimAl 中文路径 ASCII 中转（需实机 MAFFT 测试）；
API key 走命令行参数；assembly BLAST 库版本戳；i18n 硬编码中文若干；
kaleido 未使用。

## 三十五、审查遗留四项修复（2026-09-06 下午续）

1. **MAFFT/trimAl 中文路径**：Virus_Platform_Core/phylo.py 新增 _ascii_stage/_ascii_fetch
   （复用 assembly._ascii_work_base）；_run_mafft 输入非 ASCII 时复制到
   %TEMP%/vp_mafft 中转后运行；_run_trimal in/out 均非 ASCII 时中转并取回。
   实机验证：中文目录下 MAFFT 比对成功（mafft.bat 退出码 0）。
2. **API key**：/api/meta/info 的 DeepSeek key 优先级改为
   请求体 > 环境变量 DEEPSEEK_API_KEY > local 模式，密钥不再必须走命令行。
3. **BLAST 库版本戳**：ensure_virus_blast_db 记录参考 FASTA mtime+大小到
   virus.stamp，源更新后自动删除重建（此前一旦建好永不更新）。
4. **i18n 硬编码清理**：app.js 运行日志/结果/产物文件/位点/共识/页码/
   比全长/无变异/暂无数据等 10 处改 t()；i18n.js 补 c.runlog 等 9 键
   （中英各一份）。曾把英文键误插中文段——已修复（node --check 通过）。

## 三十六、注释组模块归位 + 保守域/同源复核独立成卡（2026-09-06，用户反馈）

用户指出：①「保守域 / 同源搜索复核」不应只是工具④的导航别名，应像
ORF/图谱/引物设计一样独立成模块；②「结构比较」放病毒注释组不合适，
应归比较基因组组。

- **NAV_GROUPS 调整**（app.py）：
  - annotate 组：`t-contigs-annot`（ref 别名 → 工具④）删除，新增真实
    条目 `t-annot`（独立卡片）；`t-contigs-struct`（结构比较）移出。
  - compare 组：`t-contigs-struct` 插到 `t-sdt` 之前（同为 identity
    矩阵/热图口径，SDT 卡紧随其后）。
- **tools.html**：新增 `<section id="t-annot">` 独立工作区——选 contigs_
  运行 → 病毒 contig 表（近完整绿底）→ 逐条 CDD/BLASTN/BLASTX/Primer；
  结构比较卡片原样移到 SDT 卡前。四件套 JS 改造：`runAnalyze/showCached/
  showAnaResult` 增加 scope 参数（`ANA_SCOPES.ana`=工具④内嵌表格 /
  `.ca`=独立卡片，共用 /api/tool/analyze 与结果缓存），结果 HTML 组装
  抽成 `anaResultHtml()` 两处共用；新增 `loadCaRuns/loadCaContigs/
  renderCaTable`（30s 轮询与工具④一致）。
- **参数钳制补齐**（后端兜底，此前只有前端 min/max）：orf min_aa
  1~5000、genoplot max_plots 1~200、primer num_return 1~20。
- **文档修正**：上文模块表「引物设计」页面误写 `/genome`，实为 `/primer`。
- **回归**：py_compile 通过；test client 全页 200；tools 页内联 JS
  node --check 通过；旧别名 `#t-contigs-annot` 链接失效属预期（落地卡片
  兜底），工具④卡内四件套按钮不受影响（scope 默认 ana）。

## 三十七、246 三库清理重建版拉回本地 + ②卡复选框归位（2026-09-07 凌晨）

- 246 端三库清理重建（零残留确认）后重新打包拉回（plant_db 107MB /
  refvirus_db 752MB / rvdb_db 3.0GB，构建时间戳 09-07 00:27-30）。
- 本地替换：`databases/{plant_db,refvirus_db,rvdb_db}` 三库 db_ready 全
  True（hash: 42MB / 676MB / 1.5GB）；旧 rvdb_db 已删除。
- 分类冒烟（Virus_Platform_Core.kunpeng.classify，NC_116488.1 前 20kb 切 150bp reads×15）：
  三库全部 C=15 种级命中——plant_db→Janusivirus portis、refvirus_db、
  rvdb_db 符合 §33 判据。注意 plant_db 是「植物病毒参考库」（4,286
  taxid 中 3,806 为植物病毒，源 plant_tagged.fa=Plant_Virus complete_ref），
  不是宿主库，勿用于宿主去除。
- tar 包移回 /tmp/dbpull/（246 端 /home/USER/kunpeng_dbs/ 另有全套）。
- 工具②卡内嵌报告复选框归位：`skTop5I` 移到「Taxonomy Sankey」标题下、
  `skUncI` 移到「Classification Table」标题下（此前堆在报告区顶部）。
- 服务端已验证：curl /tools?g=virus 返回新版布局；/api/dbs 六项全 ready。

## 三十八、公共数据检索页三问题修复（2026-09-07 凌晨，用户反馈）

**现象**：Core14/Full 元数据"都显示 1 个"；检索结果点击没反应；绘图没有
分开的图。**根因与修复**：
1. **陈旧元数据误导**：Lycium_chinense 的 Core14/Full 是 09-05 旧检索
   （1 条 Run）的产物，09-07 01:02 重新检索得 69 条后未作废 → 界面照常
   显示"1 个"。修复：`/api/meta/collections` 返回 `n_core14/n_full/n_plots/
   stale`（快算 CSV 数据行数，stale=元数据条数≠检索条数），卡片显示条数
   徽章 + 「⚠ 请重新提取」黄色警示。
2. **点击无反应**：showTable 无 catch，结果文件被新一轮检索重建期间
   404 → 静默失败。修复：所有请求失败在卡片内显示红色错误条 + 「刷新
   项目列表」按钮；空项目（如 Nicotiana_tabacum，search 目录为空）显示
   「无产物」提示。
3. **绘图拆分**：landscape_plot.py 面板重构为 `_pnl_a~f` 六个独立绘制
   函数（合并图与单图共用），`plot_sci_landscape` 现输出 3×2 合并总览图
   + A~F 六张独立图（各 PNG 600dpi + PDF 矢量，Panel_A_temporal /
   B_database / C_organizations / D_tissues / E_regions / F_stages）。
   Lycium_barbarum 已实跑验证（14 个文件）。
4. **布局重构**：meta.html ② 检索项目由"扁平行 + 全局结果卡"改为每项目
   一张独立卡片——头部统计徽章、操作按钮、内嵌结果表（排序/分页/勾选/
   下载，✕ 收起）、内嵌图库（fig-grid 网格缩略 + PDF 下载行）；排序状态
   按 `name:which` 独立存储；全局 plotPanel/tblCard 移除。
- 回归：py_compile / node --check 通过；/api/meta/collections 新字段、
  /meta 新布局、/api/meta/table 均实测正常；服务已重启。

## 三十九、注释组四卡拆分 + 全模块内置示例（2026-09-07 凌晨，用户反馈）

- **拆分**：「ORF 预测 / 功能注释」→「ORF 预测」(/orf) +「功能注释」
  (/annotation 新页)；「保守域 / 同源搜索复核」(t-annot) → 「保守域搜索
  (CDD)」(t-cdd) + 「同源搜索复核 (BLAST)」(t-hom)。注释组现为六条独立
  入口：ORF 预测 / 功能注释 / CDD / BLAST / 基因组图谱 / 引物设计。
- **后端**：TOOL_REGISTRY 新增 `orfa`（_tool_job_orfa）——参数 run=已有
  orf_ 运行（校验 `orf_` 前缀 + 目录存在）直接 run_orf_annotation；
  参数 fasta=输入 FASTA 时复用 _tool_job_orf（annotate=True）一步预测+注释。
  'orf' 标题改为「ORF 预测」；新增 /annotation 路由；
  NAV_GROUPS + _PATH_TO_GROUP 同步拆分。
- **annotation.html**：方式 A=选 orf_ 运行注释（列表按 files 含
  03_assembly 过滤）；方式 B=FASTA 预测+注释；均带 ✨示例。
- **t-cdd / t-hom**：ANA_SCOPES 扩展 cdd/hom 两作用域（cdd 卡只出 CDD
  按钮、hom 卡只出 BLASTN/BLASTX 按钮），loadCaRuns/loadCaContigs/
  renderCaTable 全部 scope 参数化（表格 id 由 run select id 推导），
  caExample(scope) 自动选最新 contigs_ 运行载入表。
- **内置示例**：databases/examples/example_viral_contigs.fasta（NC_077216.1
  6096bp + NC_027131.1 6988bp 两条植物病毒完整基因组）；app.js 新增
  EXAMPLE_FASTA + fillExample(id)；orf / annotation / genome / primer
  四页输入行均有「✨ 示例」按钮（t-cdd/t-hom 为运行选择型示例）。
- 回归：py_compile、tools/annotation/orf 三页内联 JS node --check 通过；
  /orf、/annotation、/tools?g=annotate 实测新布局；orfa 空参与非法 run
  名均 400 带明确错误；服务已重启。

## 四十、SDT exe 移除 → 纯 Python 精确复刻（2026-09-07 凌晨，移植 MMPV-RNA）

- **来源**：D:\桌面\延伸基因组\MMPV-RNA（sdt_genus_matrix.py +
  virus_auto_pipeline.py 的 build_mat_sdt_exact / plot_heatmap /
  plot_distribution / get_safe_leaf_order），SDT v1.3（Brejnev & Muhire
  2014）口径完全复刻：**每对序列 MAFFT 独立全局比对（--localpair）→
  Get_Similarity 公式**（分母只计两序列均有碱基的列），而非单次 MSA 近似。
- **Virus_Platform_Core/sdt_exact.py（新）**：_sdt_pair_worker（临时 FASTA → mafft.bat
  --quiet --localpair → 解析 → 原公式）经 ProcessPoolExecutor 并行
  （initializer 传序列全局，避免逐对 pickle）；.resume_cache 原子刷盘
  断点续传（缓存键 = n + 全序列 sha1 前 12 位）；21-mer 自动定向
  （与最长 3 条参考比 k-mer 共享，rev> fwd×1.1 翻正，revcomp 手写不依赖
  Biopython）；cluster_order（scipy average linkage，NaN 按 100 距离）；
  plot_heatmap（SDT 经典红黄 10 档 ListedColormap + BoundaryNorm 硬分界、
  自适应下限 95/90/85/80/十位、三角模式、手绘网格、cividis 色盲安全可选，
  PNG 300dpi + PDF fonttype42）；plot_distribution（SDT 原版红色分布曲线）；
  run_sdt_exact 主入口（≤max_n 截断、矩阵 CSV/JSON、六产物）。
  注意 matplotlib set_xticklabels 不接受 pad（用 tick_params(pad=)）。
- **接线**：TOOL_REGISTRY 新增 'sdt'（_tool_job_sdt，参数 seqs/max_n/
  orient/palette，threads=并行进程数）；t-sdt 卡片重写——「▶ 运行 SDT
  精确分析」跑新工具，结果区嵌入热图/分布图 PNG + 矩阵表 + 6 个下载，
  保留「查看已有矩阵」plotly 视图。
- **SDT exe 移除**：config.py 'sdt' 探测项、/api/open_sdt 端点、
  openSDT() JS、results.html「启动 SDT v1.3」按钮、i18n rs.sdtBtn 全部
  删除；results/tools 相关提示文本改为在线查看口径。
  identityMatrixTable 兼容 null（SDT JSON 对角与缺失为 null）。
- **回归**：6 条病毒基因组长序列实测（15 对，近缘 90.85% / 远缘 57.84%，
  聚类排序正常，热图/分布图质量目检通过）；续传缓存命中生效；API 端到端
  跑通 example FASTA（sdt_20260907_024615，产物齐全）；py_compile +
  app.js/tools 页 JS node --check 通过；服务已重启。

## 四十一、SDT 已比对直算 + 多色阶 + NT+AA 同一性表（2026-09-07，用户需求）

- **已比对输入**：SDT 卡新增「输入已比对 MSA」勾选——跳过 MAFFT 与自动
  定向，每对直接从 MSA 两行按 Get_Similarity 公式计算（_msa_pair_worker，
  同等并行/续传）；长度不一致时报错提示。API 参数 aligned。
- **热图色阶可选**：_cmap_colors(name) 把任意 matplotlib colormap 采样成
  10 档硬分界色阶；支持 sdt / cividis / viridis / RdYlBu / Spectral /
  YlGnBu / coolwarm / magma，SDT 与同一性表两卡共用下拉。
- **NT+AA 同一性表**（t-identity 新卡，tool 'identity'，BioAider
  Sequence Identity Matrix 口径）：run_identity_table——
  - 输入：核苷酸 FASTA（必选）+ 氨基酸 FASTA（可选，按名称匹配，缺失条
    自动 6-frame 最长 ORF 翻译兜底，Virus_Platform_Core/contig_annot.longest_orf_protein）；
  - NT / AA 两套矩阵（同一 aligned 参数口径，AA 长度不齐自动退回逐对
    MAFFT）；产物：identity_table.csv（逐对长表，NT 降序）、nt/aa_matrix.csv、
    identity_composite.png/pdf（**复合热图：上三角 NT / 下三角 AA**）、
    nt_heatmap.png、identity.json（前端逐对表直读）；
  - 前端：复合热图预览 + 逐对同一性表（# / 序列 A / B / NT% / AA%）+ 下载区。
- **缓存污染修复**：build_mat_sdt_exact——比对器缺失/不可达时快速失败；
  全部新算对均 NaN 时抛错且不写续传缓存（避免一次 mafft 故障把 NaN 固化，
  之后永远复用）。
- **回归**：4 条基因组建 MSA 后 aligned 模式 5.5s 出 NT+AA 全表（6/6 对
  有 AA 值，近缘 90.92/97.81）；未比对模式 10.1s；复合热图 viridis 目检
  通过；API 端到端 identity 任务完成；服务已重启。
- **合并（用户反馈）**：NT+AA 本就是 SDT 同源结果，独立 t-identity 卡撤销
  并回 t-sdt——「模式」下拉切换 NT（SDT 矩阵/热图/分布图）与 NT+AA
  （同一性表/复合热图），AA FASTA 输入行仅在 NT+AA 模式使用；前端
  sdtRunMatrix 按模式派发 tool 'sdt' / 'identity'，结果区共用一张卡
  （#sdExact / #idtResult 二选一显示）。NAV_GROUPS 恢复单项；工具
  'identity' 后端保留不变；API 端到端复测通过。
- **双轨补齐（用户指出 MMPV 原生支持核酸+蛋白）**：detect_seqtype——
  统计蛋白特征残基 E/F/I/L/P/Q 占比（核酸歧义码中不存在，蛋白序列通常
  >20%），>3% 判蛋白；run_sdt_exact 增加 seqtype='auto'|'nt'|'aa'——
  AA 模式自动跳过 21-mer 定向，标题/JSON 标注 AA Identity；SDT 卡模式
  下拉扩为三档 NT / AA / NT+AA（前端 seqtype 透传，结果 meta 显示
  序列类型与直算标记）；run_identity_table 增加蛋白输入守卫（NT+AA
  模式①误投蛋白序列时报错并提示切 AA 模式）。AA 实跑：2 条 ORF 蛋白
  3.2s 完成（detect=aa）；API 端到端 NT 自动判别正常。

## 四十二、全模块示例补齐 + 两轮集成实测揪出五个真 bug（2026-09-07，用户通宵验证）

### 修复的高危 bug

1. 🔴 **pyrodigal 坐标口径错位（Virus_Platform_Core/orf.py）**——`g.begin/g.end` 是 1-based
   closed（v2/v3 文档一致，本次实测 3.7.1 实证），代码却按 0-based 半开
   区间切片 `seq[begin:end]`：正链基因整体移码，faa 全是含几十个内部 `*`
   的垃圾序列，DIAMOND 命中为 0 → ⑥b 功能注释空表。负链基因因
   revcomp 方向巧合不受影响，历史 E2E（全是负链）因此没暴露——
   第三十四节外部审查"核实无问题"结论系误判，已在实机推翻。
   修复：`begin0 = begin-1` 切片；GFF 写回 `begin..end`（原 `begin+1` 也
   偏 1）。实测 2 条示例 contig：修复前注释 0 行，修复后 7/7 ORF 全部
   命中产物+类别。
2. 🔴 **gbdraw 纯 FASTA 模式坏死 + 假成功**——gbdraw CLI 要求 `--fasta`
   必须配 `--gff`（空 GFF 又报 No valid records），裸骨架路径逐 contig
   失败后任务仍报"完成 0 张图"。修复：`_write_skeleton_gff` 合成跨全长
   `region` 特征的骨架 GFF（实测 gbdraw 接受）；`_tool_job_genoplot`
   auto 引擎 0 张图时自动回退 DFV，仍为 0 则报错。
3. 🔴 **比对定向缺失（Virus_Platform_Core/phylo.py `_run_mafft`）**——de novo 组装常出
   反向互补 contig，样品流程比对/树/SDT 矩阵不翻正：IT-E2E 的自身
   contig 对自己的参考只有 36.4% identity、⑧保守区引物 0 对。
   平台 MAFFT 是 v6.864b（2011）无 `--adjustdirection`；新增
   `_orient_normalize`（比对前 21-mer 定向翻正，复用 sdt_exact）。
   修复后 identity 100.0%、保守区引物 3 对。
4. 🔴 **`auto_orient` 自锚失效（Virus_Platform_Core/sdt_exact.py）**——旧实现把全部序列
   k-mer 并集当锚：反向序列自身贡献的 k-mer 恒在锚集内（fwd≈rev），
   永远判不出反向——SDT 卡的「21-mer 自动定向」实际从未生效。
   改为固定全局锚（ref_seqs 最长者，否则 recs 内最长者）。
5. 🟡 **`/api/tool/runs` 500**——os.walk 遍历遇 MMseqs2 临时库特殊文件
   `*.mmseqs_tmp/latest`（WinError 1920 不可 stat）直接炸接口；
   getsize 加 OSError 兜底跳过。顺手 `_run_search`（mmseqs 分支）补
   `shutil.rmtree(tmp)` 清残留临时目录（存量 10 处已清理）。
   另：该接口原按目录名倒序取 30 条，字母序挤掉真正新运行
   （structcmp > genoplot），改按 mtime 排序。
6. 🟡 **冻结分发 multiprocessing**——SDT 精确矩阵/同一性表用
   ProcessPoolExecutor；打包 exe 子进程会重启 exe，app.py 顶部补
   `multiprocessing.freeze_support()`（源码模式空操作）。

### 测试与示例

- **tests/_it_annotate.py**（新增）：注释组全链路集成——orf → orfa
  （run 模式写回 orf_ 目录 + FASTA 一步法）→ genoplot（FASTA 骨架图 +
  GenBank 输入）→ primer（plain + conserved）→ contigs 分类 →
  viral_contigs/report_data/analyze(primer, blastn 在线) → runs 列表。
  实测 BLASTN 在线命中 20 条。修复前该脚本抓出 bug 1/2/5。
- **tests/_it_compare.py**（新增）：比较组全链路集成——structcmp（近缘
  98.5% / 跨参考 34.4% 校验）→ msa_data → SDT 精确矩阵（15 对 +
  对角线 100 + 近缘>跨参考）→ NT+AA 同一性表 → quicktree（NJ/FastTree）
  → 树文件 API + 路径穿越拒绝 → Entrez 在线检索（1030 条）→ 示例
  GenBank 集合导入 → 共线性比较（html/svg/png/clusters/similarity/
  faa/m8）→ 集合建树 → gb: 伪样品 MSA/树/SDT API。**测试体必须收进
  main()+__name__ 保护**——sdt/identity 的 ProcessPoolExecutor 在
  Windows spawn 下会重导入 __main__，无保护递归崩溃（早期运行即踩）。
- **tests/_it_platform.py**（新增）：21 个页面全 200 + 渲染含各示例按钮
  + 示例文件齐全 + 15 个关键 API + 样品报告可访问 + 静态资源。
- **tests/_it_submit.py**：修复过时 API（create_table(demo=True) →
  sample='demo'；demo 数据现为 6 行），改受控 3 行表续跑。
- **databases/examples/** 新增（tests/make_examples.py 确定性生成）：
  example_virus_set.fasta（3 条真实 tobamovirus 参考 + 3 条 2%/8%/15%
  受控突变衍生，MSA/结构比较/SDT/快速建树示例）、
  example_conserved_set.fasta（同种近缘 0.1%~1.2%，保守区引物示例——
  保守区设计要求全表 ≥90% 一致连续列 ≥400，跨物种集达不到）、
  example_tree.nwk（上集 FastTree 树）、example_genome.gb（pyrodigal
  坐标 + 占位 product，基因组图谱 GenBank 模式）、
  example_synteny_A/B/C.gb（3 条同属小基因组各 6 CDS，共线性离线示例）。
- **示例按钮补位**（tools.html / genome.html / primer.html + app.js）：
  t-msa、t-tree（快速建树 + 本机树文件）、t-contigs-struct、t-sdt 指向
  近缘集、t-ncbi（ncbiExample 预填检索式）、t-synteny（syntenyExample
  预填内置 3 条 .gb 离线导入）、genome 页「✨ 示例 .gb」、primer 页按
  模式切换示例文件（app.js 新增 EXAMPLE_SET_FASTA /
  EXAMPLE_CONSERVED_FASTA / EXAMPLE_TREE_NWK / EXAMPLE_GENBANK_GB /
  EXAMPLE_SYNTENY_GBS 常量 + ncbiExample/syntenyExample）。

### 全平台验证结论（模拟 + 真实数据）

- **主流程 E2E**：tests/make_synthetic.py 重生成 PMMoV 8000 对高 Q reads
  （旧数据 Q 全 ≥60 触发 SPAdes "Failed to determine offset"，fake_qual
  下限改 55；fastp/SPAdes 对合成数据的边界又踩清一次）→ IT-E2E 全 12
  阶段（fastp→fq2fa→宿主→筛查→组装→宿主预测→ORF→注释→树→引物→图→
  报告）一次通过；8000/8000 reads 分类命中，宿主预测 Tombusviridae/
  Plant 0.998 High；报告 200KB 内嵌 SVG/plotly。
- **公共检索**：meta-search 真查 5 条 Run（SRA_GSA_Merged_Final.csv）。
- **下载**：/api/dl/create→cancel→delete 生命周期实测通过（真实大批量
  下载此前已有 downloads/ 证据）。
- **LOGAN**：logan-create（2 片段切分）→ import_result（ fabricated
  Logan-Search 表）→ trace_report.html 28KB 生成；真实外网提交不代测。
- **回归**：_it_msa / _it_synteny / _it_phylo / _it_submit / _smoke_phylo
  / _it_annotate / _it_compare / _it_platform 全部 PASSED。

## 四十三、比较基因组组整改：删「结构比较」、拆「GenBank 集合管理」（2026-09-07，用户反馈）

- **删除「结构比较」卡**（t-contigs-struct）：与 ⑨ SDT 卡功能重叠（同为
  两两 identity 矩阵/热图，仅"一次共享 MSA"与"逐对独立比对"之差），按用户
  要求整卡移除——NAV_GROUPS 条目、tools.html `<section>`、专用 JS
  （loadStructRuns/scUseRun/loadStructData/renderStruct/SC_DATA）、runTool
  structcmp 分支与初始化调用全部清除。**后端 TOOL_REGISTRY 的
  'structcmp' 工具与 /api/tool/structcmp_data、/api/tool/msa_data 保留**：
  ⑦ MSA 卡「运行比对并查看」仍以 tool=structcmp 出比对（msaRunAlign），
  集成测试也经 API 复用。
- **「GenBank 集合下载 / 导入 / 巡检」拆为独立卡片**（t-synteny-gb）：
  原 t-synteny-gb 是 `ref: t-synteny` 别名菜单（点击落在共线性卡，用户
  明确不要）——现改为落地卡片：集合名/检索式/accession/条数 + ⬇ 下载 +
  ✨ 示例（预填内置 3 条 .gb）+ 📥 本机 .gb 导入 + 输出区 +
  **#gbCollectionsMgmt 集合列表（🔍 巡检）** + #gbWarnings 警告明细。
  loadGbCollections 改为双渲染：同一份 /api/gb/collections 数据，
  共线性卡 #gbCollections 出 ▶ 比较 / 🌳 建树，管理卡 #gbCollectionsMgmt
  出 🔍 巡检。共线性卡（t-synteny）只留比较参数（一致性/覆盖度/着色/
  LoVis4u）+ 建树工具 + 集合列表；失去入口的 runCompare（读 s_name 的
  手动按钮）删除——集合名输入移去管理卡，比较统一走列表「▶ 比较」。
- **回归**：app.js node --check 通过；tools 页渲染后两段内联 JS
  node --check 通过；py_compile 通过；导航 JSON 确认 compare 组 =
  t-ncbi / t-synteny-gb / t-msa / t-tree / t-sdt / t-synteny 且页面
  无 t-contigs-struct 残留；_it_platform 断言同步更新（结构比较移除 +
  双卡存在 + 双列表渲染标记）后 PASSED。

## 四十四、比较基因组组按分析目的重组 + ICTV 科属选参 + 旧项目归档（2026-09-07，用户反馈）

用户定调四模块（按分析目的而非工具堆叠）：
**参考序列获取 → 进化树构建（科/属级）→ SDT 同一性（属级）→ 同属共线性（LoVis4u）**。

- **t-seqprep「参考序列获取」**（合并原 t-ncbi + t-synteny-gb，都是序列准备）：
  - 新增 <b>ICTV 科/属选参下载</b>：GET /api/ictv/taxa（taxa.txt 按宿主含
    plant 过滤，科/属下拉含 accession 数，实测 236 属）；POST /api/ictv/preview
    （select_refs 本地优先预览，不下载）；POST /api/ictv/download（后台任务：
    select_refs → download_gb_collection(accessions) → 同步 FASTA 参考集到
    databases/ncbi_refs/<coll>/refs.fa）。实测 Dianthovirus 属：6 条 GenBank +
    6 条 FASTA，直接喂「进化树构建」建树成功。
  - accession 列表 / Entrez 检索式下载（/api/gb/download 也补了 FASTA 同步）；
    本机 .gb 导入（✨ 示例 .gb 预填 3 条内置文件 + 集合名）；
    集合列表（🔍 巡检）+ FASTA 参考集列表。
  - 原 t-ncbi 的"仅 FASTA 集合"下载入口撤销——现在所有来源统一产
    GenBank 集合 + FASTA 双产物（ncbi_refs 集合名即样品流程可用的参考集合名）。
- **t-treebuild「进化树构建（科/属级）」**（合并原 t-msa + t-tree）：
  ① 集合建树（tbColl 下拉 + NJ/FastTree/RAxML-NG，/api/gb/phylo）；
  ② FASTA 直接建树（quicktree）；③ 树查看（本机 Newick / 样品与集合树）；
  ④ MSA 查看（SNP-only）。所有元素 id 原样保留（qt_fa/tv_*/ms_*），
  msaToolInit/treeToolInit 等 JS 零改动。
- **t-sdt**：标题改「SDT 同一性分析（属级）」。
- **t-synteny「同属共线性比较（LoVis4u）」**：/api/compare/run 的 lovis4u 默认
  True（前端不再有开关/着色风格选择）；内置引擎降级为 LoVis4u 不可用时的回退
  （clusters/similarity 表照出）。实测 Dianthovirus 集合 → lovis4u.pdf 51KB。
  集合列表按钮 = ▶ 比较 + 🌳 建树（tbBuildFor 跳建树卡并选中集合）。
- **旧项目归档**：E2E-VERIFY/REFACTOR-E2E/REGRESS/SYN-smoke/IT-E2E/dfv_demo/
  _it_* 等 9 个测试样品 → results/_archive/（保留 NX-6、ERR7586041、
  SRR39909438 三个真实样品）；gb_collections 的 _it_synteny/_example_synteny/
  tobamo_test → databases/gb_collections/_archive/。列表口径统一过滤下划线
  前缀（api_samples / api_msa_samples / api_gb_collections /
  api_ncbi_collections / page_results）；新增 /api/samples/archived +
  /archive_report/<sample>/，结果中心样品表下新增折叠「📁 归档项目」区
  （可打开报告，提示如何恢复）。meta_search 测试项目 Tobacco_mosaic_virus
  删除。
- **坑**：tm.start 的 prog 是 (stage, pct, msg) 三参，与
  download_gb_collection 内部 prog 约定一致——中间包一层 2 参 _prog 必炸；
  collection_records 返回 (acc, organism, seq) 三元组而非 SeqRecord。
- **回归**：_it_platform（含新模块断言 + 归档断言）/ _it_msa / _it_synteny /
  _it_submit / _smoke_phylo / _it_compare / _it_annotate 全部 PASSED；
  渲染内联 JS node --check 通过；ICTV 预览/下载/建树/LoVis4u 比较真实链路实测。

## 四十五、ICTV 级联选参 + 基因级建树 + 独立序列比对模块（2026-09-07，用户反馈）

用户三点需求：① ICTV 科属选择改为界门纲目科属种**递进级联**（并核实计数）；
② GenBank 集合支持提取 CDS/PEP 做比对建树；③ 比对+trimAl+查看编辑独立成模块
（参照本机 PhyloSuite——其为 PyInstaller+PyQt，内置 Lg_msa_view 彩色表格
查看器支持编辑保存，无外部 Jalview 依赖）。

- **计数核实**：taxa.txt 每行=1 个 accession（多 accession 已拆行），各级
  计数即 accession 条数：Geminiviridae 986（植物口径）/988（全部宿主）、
  Begomovirus 846、属 236、种 2478——与 select_refs 实取数一致。旧版预览
  按植物过滤而下载不过滤的不一致已统一为植物口径（plant_only）。
- **级联（Virus_Platform_Core/ictv_db）**：RANK_COLS 九级（含 Kingdom/Subfamily 列，主级
  Realm/Phylum/Class/Order/Family/Genus/Species）；rank_options(levels) 给
  已选过滤下每级的 [{name,n}]；select_refs 增加 ranks={列:值} 交集过滤 +
  plant_only。API：/api/ictv/cascade（GET，已选级作过滤返回全部级选项）、
  preview/download 改吃任意级组合（_ictv_levels 取最深层定位）。前端
  spCascade 七个下拉逐级联动（变更即清更深级并重取），示例预填
  Riboviria→Tombusviridae→Dianthovirus。
- **基因级建树（Virus_Platform_Core/gb_collection）**：_gene_feature_seqs 按 product/gene/
  note 关键词匹配 CDS（每记录取首个命中），PEP 优先 translation 限定符、
  缺则按 transl_table 翻译；build_collection_phylo 增加 molecule=
  genome|cds|pep + gene，基因级产物落 gene_trees/<slug>_<molecule>/；
  PEP 的 FastTree 用蛋白模式（-gtr 不适用）。/api/gb/phylo 与建树卡同步
  （分子下拉 + 关键词输入，genome 时禁用）。实测示例集合 coat protein：
  PEP/CDS 各 3 条提取建树通过；无关键词/无命中均有清晰报错。
- **独立序列比对模块（t-align，导航第 2 位）**：TOOL_REGISTRY 新增
  'align'——_run_mafft 增加 strategy=auto|linsi|fast（L-INS-i=--localpair
  --maxiterate 1000；fast=FFT-NS-1），trimAl automated1/gappyout/strict/
  none，产物 input/aln/aln.trim。查看器 API：/api/align/file（平台内或
  绝对路径只读，返回 names/seqs/aligned/type）与 /api/align/save（FASTA
  校验后写 <名>.edit.fasta 副本；平台外源落 tool_runs/align_edit_*/）。
  前端：彩色等宽查看器（核酸/蛋白双配色 + 分页 + 图例 + 长度不一致警示）、
  ✏️ 编辑模式（textarea FASTA 化）→ 💾 保存副本 → 自动重载，结果可
  一键「送进化树构建 / 送 SDT」。t-treebuild 的 MSA 查看保留（建树上下文）。
- **测试**：tests/_it_align.py（级联/比对/查看/编辑/基因树全链路，PASSED）；
  _it_platform 断言更新（五模块导航 + 级联/比对/分子标记）；_it_compare/
  _it_msa/_it_synteny 回归通过；渲染内联 JS node --check 通过。

## 四十六、参考序列获取卡：CDS/PEP 提取 + ICTV 抽样上限（2026-09-07，用户反馈）

- **CDS/PEP 提取（PhyloSuite 布局）**：集合管理列表新增「🧬 提取」按钮 →
  后台任务 extract_collection_features →
  gb_collections/<名>/extract/{genome.fa, CDS.fa, PEP.fa, genes.tsv,
  CDS/<基因>.fa, PEP/<基因>.fa}——分类分目录 + 按基因拆分（同源基因每
  基因组一份，序列 id=<acc>|<基因>；PEP 优先 translation 限定符）。
  产物面板（集合下拉 + 清单表）每项带 ⬇下载 / →送比对 / →送建树；
  /api/gb/collections 增加 has_extract 标记；/api/gb/extract(_files)
  端点。实测示例集合：3 基因组 × 6 CDS/PEP、6 个按基因文件、
  genes.tsv 7 行。
- **ICTV 每属/每种抽样上限**：ictv_db.cap_per_rank(rows, per_genus,
  per_species)（保持本地优先序封顶）；preview/download 接受
  per_genus(0-50)/per_species(0-20)，先封顶再取 limit（select_refs 内层
  limit 放大到 max(limit*20, 2000) 保证封顶前有足够候选）。UI 两个下拉
  （每属 不限/3/5/10，每种 不限/1/2/3），预览标注抽样口径与丢弃数。
  实测：Tombusviridae 101→64（每属≤5）→62（+每种≤1）；Potyviridae
  290→42（max/属=5）。
- **测试**：tests/_it_extract.py（抽样效果 + 提取布局 + 清单 API + 404）
  PASSED；_it_platform / _it_align / _it_compare 回归通过。

## 四十七、下载批次删除拆分 + 数据库构建页去任务面板（2026-09-07，用户反馈）

- **公共数据下载 · 三模式删除**：原单个 🗑（记录+文件一并删）拆为
  📂 删文件（清数据文件，保留 batch.json 记录与 batch.log 日志，文件
  条目标记 deleted 仍可在列表追溯）/ 🧾 删记录（删 batch.json+batch.log，
  数据文件保留在 downloads/<批次>/ 磁盘原处）/ 🗑 全删（原行为）。
  后端 DownloadManager.delete_files / delete_record（运行中批次先自动
  cancel；已完结批次不再被误标 cancelled）；delete/delete_record 对
  不存在批次改返回 False（API 400）。确认弹窗按模式区分文案。
  测试：tests/_it_dl_delete.py（三模式目录断言 + 日志落笔 + API 链路 +
  400 守卫）PASSED。
- **数据库构建页**：底部「任务」汇总卡移除——各构建卡下方 buildrun 区
  已实时输出日志，不再重复展示；refreshTasks 的空面板守卫保证连接
  横幅/toast/卡内日志注入不受影响。

## 四十八、公共数据检索 ↔ 下载联动补齐（2026-09-07，用户反馈）

- **Info 元数据表（Core14/Full，ib 视图）**：勾选栏原只有计数，补
  「⬇ 勾选 Run 转入数据下载」——intoDownload 泛化 which 参数
  （缺省 'search' 兼容原 S-浏览调用），runsOfRows 本就支持任意 which。
- **行内单 Run 快捷下载**：通用表格渲染（renderView，sb/ib/local 三视图
  共用）在 Run 列追加 ⬇ 锚点（正则校验 SRR/ERR/DRR/CRR 形态才显示），
  dlOneRun 单 Run 直接 POST /api/dl/create（批次名 <项目>_runs）并跳转
  /download。实测单 Run 建批 → 取消 → 全删链路 200。
- 回归：/meta 渲染含新按钮与 dlOneRun；两段内联 JS node --check 通过。

## 四十九、工具卡历史口径统一说明（2026-09-07，用户问询）

用户问：下载/样品/格式转换/质控/宿主去除页为何有的有旧记录有的没有。
根源与口径：
- **公共数据下载**：批次落盘 downloads/<id>/batch.json，服务重启也在 → 一直有记录（该页本身就是批次管理器）。
- **样品创建/批量导入**：列表 = results/ 目录；旧测试样品已按需求归档进
  结果中心「归档项目」，故只剩真实样品（NX-6 / ERR7586041 / SRR39909438）。
- **格式转换 / 序列质控 / 宿主去除（及识别 / 组装）**：卡片 .toolrun 输出区
  只渲染 TaskManager 内存中的本会话任务（injectStageLogs），服务重启即清空；
  历史一直都在 **结果中心 → 专项运行**（/api/tool/runs 按目录 mtime 取
  最近 30 条，含全部产物文件下载与打开目录）。
统一处理：五处输出区下追加固定提示行「🕘 本输出区仅显示本次会话任务；
历史运行与产物 → 结果中心 · 专项运行（重启后到此查看）」（tools.html
四卡 + host_removal.html 独立页），口径对齐"模块页干净、历史集中结果中心"。

## 五十、全模块历史运行区：持久保留 + 上下游衔接 + 折叠/删除（2026-09-07，用户需求）

用户定调：不只下载数据，**所有模块都保留运行记录**，且支持**上下游衔接
复用**；为防过多只要求**可折叠、可删除**。方案（取代第四十九节的"结果中心
指引"静态提示）：

- **数据源**：/api/tool/runs（tool_runs/ 目录持久，重启不丢），按每卡
  prefix 过滤（convert_/fastp_/hostremoval_/identify_/assemble_/contigs_/
  align_/structcmp_+quicktree_/sdt_+identity_/orf_/orfa_/genoplot_/primer_/
  hostpredict→contigs_）。
- **组件**（app.js 通用 RH_DEFS/rhRefresh/rhToggle/rhFiles/rhDel/rhGo）：
  卡输出区下方插入 🕘 历史运行（N）——默认折叠、计数徽标；条目 = 运行名 +
  文件数 + **衔接按钮组** + 📁 文件（展开逐文件下载）+ 🗑 删除
  （POST /api/tool/runs/<run>/delete，名称白名单 + 平台内限定 + 不存在 400）。
- **上下游衔接**：每工具定义 chains(files)，按产物文件名（fastp_R1/R2、
  kept_R1/R2、viral_sequences、contigs.filtered、viral_contigs、
  pyrodigal.faa/ffn、orf_annotation.gff3、aln(.trim)、tree/nj.nwk、
  virus_classification.tsv）生成"→ 下游模块"按钮；点击经 sessionStorage
  vp_runfill 暂存 {输入框id: 路径} → 跳目标页 → rhApplyStashed 回填
  （支持 input/select/checkbox，如 i_type=fasta、sd_aligned 勾选、
  oa_run 选运行）；同页目标立即填充不跳转。
- **宿主预测页**无自有运行目录，历史 = 有分类结果的 contigs_ 运行，
  一键「载入分类表」（hp_tsv + hp_fa）。
- 任务刚结束时（refreshTasks 钩子 __rhOnTasks）自动刷新各历史区。
- **回归**：_it_platform 新增容器数量与删除 API 断言 PASSED；全部页面
  内联 JS node --check 通过；删除 API 实测（建目录→删→400 守卫）。

## 五十一、④分类卡重排 + 宿主自动化 + ID 复制 + 四件套跳转与 metabuli 风格结果（2026-09-07，用户需求）

- **布局**：Krona Taxonomy Plot（旭日图）移到 Virus Sequence Classification
  表之前（Sankey → 分类表 → 宿主统计 → Krona → 分类明细表）。
- **宿主预测自动化**：新增「宿主预测自动运行」开关（默认开）——分类表加载
  后若无宿主列（autoHostIfMissing，按运行去重）→ 自动 POST
  /api/tool/hostpredict_run → 完成后重载表格，宿主列并入（实测
  'Plant (High)'）；宿主库未就绪时静默跳过（手动 🧲 按钮会提示）。
  表头新增**宿主筛选下拉**（vcHostF，从行数据去重生成，按 "类别 (置信)"
  前缀匹配），计数行显示筛选前后条数。
- **ID 复制序列**：分类表 Contig ID 单元格可点（⧉ 前缀 + 虚线下划线）→
  copyContigSeq 从运行目录 viral_contigs.fasta（回退 contigs.filtered）
  取该记录，clipboard.writeText（execCommand 兜底）写 FASTA，toast 报长度。
- **四件套跳转病毒注释分析**：行内按钮改为 🧩CDD / N / X / 🧪引物 →
  anaJump：cdd→#t-cdd，blastn/blastx/primer→#t-hom；sessionStorage
  vp_anajump 暂存（60s 有效），processAnaJump 在 tools 页加载/跳转后
  自动选运行（loadCaRuns→set→loadCaContigs）→ runAnalyze(action,
  contig, scope) → 滚动到卡片；t-hom 卡升级为
  「同源搜索复核与引物（BLASTN·BLASTX·Primer）」+ metabuli 式 tab 徽标，
  CA_ACTIONS.hom 增加 primer。
- **结果 metabuli 风格**（anaResultHtml 全作用域共用重写，参考
  <DEMO-IP>/metabuli）：BLAST 命中表 #/Accession(链接 NCBI nuccore|
  protein)/Description/Organism(斜体)/Identity(≥90/60/40 分档着色)/
  Align Len/E-value/Bit Score + 顶部 Best E-value/Best identity；
  CDD 命中前加**域条可视化**（按 from/to 定位到最长 ORF，10 色段，
  段名内嵌）+ 表 #/Accession(cddsrv 链接)/Short Name/E/Bit/From/To/
  Superfamily；Primer 表 #/引物对/F与R序列(等宽+⧉复制按钮+Tm/GC)/
  产物/罚分。表头 sticky、行 hover。
- **验证**：伪造 contigs 运行实测宿主自动补列与筛选候选、primer 字段
  （name/F_seq/F_tm/F_gc/R_*/product/penalty 全齐）；_it_platform 新增
  布局顺序/控件/跳转/metabuli 标记断言 PASSED；各页内联 JS
  node --check 通过。

## 五十二、Metabuli 示例病毒引入（2026-09-07，用户："看 metabuli 的示例病毒，我们的用"）

- 从 <DEMO-IP>/metabuli /examples 端点固化五个示例到
  databases/examples/：TMV（NC_001367.1，6395nt）、PVY（NC_001616.1，
  9704nt）、CMV（NC_002034/2035/1440 三分体 RNA 共 8623nt）、
  PSTVd（NC_002030.1 类病毒 359nt）、Mix All（四毒 6 条混合 25081nt）。
- ④「病毒 contig 深度分析」卡输入框下新增 metabuli 同款小按钮行
  [TMV][PVY][CMV][PSTVd][Mix All]（vEx，app.js EXAMPLE_METABULI 映射；
  PSTVd 自动把最短 contig 调到 200——359nt 低于默认 500 过滤线）。
- 端到端实测（TMV）：kunpeng+BLAST 分类 → Virgaviridae / Tobamovirus /
  Tobamovirus tabaci（E=0）→ 宿主自动补列 Plant (High) → 四件套 primer
  5 对 → 运行清理。_it_platform 补示例按钮/文件断言 PASSED。

## 五十三、分类表去除「选择运行」下拉（2026-09-07，用户："很不明白，去除"）

- Virus Sequence Classification 的手动「选择运行」下拉移除——表格自动
  跟随**最新一次**含 virus_classification.tsv 的 contigs_ 运行
  （loadAnaRuns 重写：anaCurRun + #anaRunLabel 标签显示当前运行名；
  无新运行不重复加载；30s 轮询/任务完成回调触发）。
- 所有 val('anaRun') 改读 anaCurRun；runAnalyze 的 ana 作用域动态取值
  特判（sc.run==='anaRun' → anaCurRun，cdd/hom 不受影响）；宿主自动/
  ID 复制/四件套跳转全部随最新运行。历史运行区（rh-contigs）仍可
  复用/删除旧运行。实测：TMV 示例跑完 → 最新运行即表格显示对象。

## 五十四、输出区 UX 三修：日志滚动/折叠、Downloads 胶囊、历史运行沉底（2026-09-07，用户反馈）

- **日志滚动不再跳顶**：injectStageLogs 每 2.5s 重绘是根因——重绘前
  snapshotLogScroll 记录每个 pre.logbox 的 scrollTop 与"是否贴底"，
  重绘后 restoreLogScroll：运行中且原本贴底 → 跟随新日志滚底；
  其余（已结束任务、用户上翻阅读中）→ 精确恢复原位置（WeakMap 记忆，
  取代旧的全局 data-autoscroll 一刀切）。
- **日志可折叠**：日志头新增 ▾/▸ 日志 按钮（logToggle，
  sessionStorage vp_logclosed 记忆状态；i18n c.logBtn 中英）。
- **Downloads 胶囊化**：taskResultHtml 的文件链接加 btn small 胶囊样式
  （tr-links 本就 flex wrap）。
- **历史运行沉底**：tools.html 八卡的 rh 容器从 tool-out 内移到各
  </section> 前（卡片最底部）；orf/annotation/host_removal/host_predict
  独立页移到产物 hint 之后；genome/primer 的 tool-out 本就是卡片末块。
- 回归：_it_platform PASSED（rh 数量断言不受移动影响）；app.js/i18n.js/
  各页内联 JS node --check 通过。

## 五十五、④分类报告区顺序调整（2026-09-07，用户："宿主预测统计放到病毒序列分类(contig明细)下面"）

④卡分类报告折叠区最终顺序：桑基流向图 → 分类表 → **病毒序列分类
（contig 明细，含宿主筛选/四件套跳转）→ 宿主预测统计 → 分类旭日图**。
中途曾把宿主统计误插进 ② identify 卡（sections 全在同一渲染页，需按
源码 section 边界核对），已纠正为唯一副本。_it_platform 顺序断言
（明细 < 宿主 < 旭日）+ 唯一性断言 PASSED。

## 五十六、并发闸门 + 存储面板 + 数据库分离（2026-09-07 晚，用户评估后要求实施）

### 并发闸门（P0-1，app.py TaskManager）
- heavy/light 两档 Semaphore（缺省 2/4，platform.json defaults 可调
  max_heavy_tasks / max_light_tasks，钳制 1-8/1-16）。
- start(name, fn, log_file=None, weight='heavy')：默认 heavy 保守；
  _run() 先 0.5s 轮询 acquire 排队，期间响应 cancel。
- **排队状态对外映射 running**（snapshot()），否则前端 status==='running'
  判断会让取消按钮消失。排队提示走 msg（"排队中 · 前面还有 N 个任务"）。
- LIGHT_TOOLS={'convert','genoplot','primer'} + 10 处下载类标 light，其余默认 heavy。
- cancel() 支持 queued；_recover_interrupted 把遗留 queued 一并标记失败。
- _trim_memory()：_run finally 调用；>60 个终态任务清 log/result/thread，
  >300 移出内存（磁盘 tasks/*.json 仍可回溯）。running/queued 跳过。
- list_all(limit=120)：running 30 行日志，终态 5 行。

### 轮询瘦身（app.js）
- setInterval(2.5s) → setTimeout 递归自适应：有任务 2.5s、空闲 8s、
  断连 2.5s 快速重试（connDown 标志）。
- **坑**：递归调度丢 setInterval 的异常免疫，schedulePoll 必须 try/catch
  兜 refreshTasks，否则一次异常永久断链。

### 存储与磁盘（P0-4）
- /api/storage（盘容量实时 + 目录占用后台扫描 + 10 分钟缓存）；
  /api/storage/rescan 强制重扫。目录统计走后台线程，绝不阻塞请求路径
  （databases 47GB/6 万文件 os.walk 实测数十秒）。
- /settings 新增「存储与磁盘」卡（storagePanel）；盘剩余 <20GB 全站顶部
  黄横幅（checkStorageBanner，插在 conn-banner 之后）。

### 数据库分离（任务：软件与数据库分开分发）
- 基础设施原本就有：config.apply_database_root + platform.json database_root +
  设置页「数据库目录」卡。本轮补：
- **Virus_Platform_Core/db_migrate.py + main.py db-migrate**：robocopy(/E /MT /Z 断点) →
  文件数+总字节校验 → 通过才 set_database_root。--mode copy|move、--dry-run、
  --check。目标须在平台根外、盘剩余 ≥ 源+10GB。写失败配置不动源目录原样。
- /api/settings 返回 dbs 就绪状态，前端切换 database_root 后即时提示缺哪个库。
- **打包改造**：scripts/package.py 默认不预置数据库（~1.9GB 软件包），
  --with-db 恢复开箱即用，--db-only 对已有发布目录补库。
  **坑**：argparse 曾写在执行路径中部，--help 会真的触发清理+打包（误删
  dist/）。参数解析必须放文件最前、任何副作用之前。
- **config threads=0 bug**：_load 曾 `max(1, int(threads))`，干净配置写 0 →
  新机被钳成 1 线程。已改 0/缺省=自动探测，显式 >0 才采用。

### 依赖审计（scripts/audit_deps.py + selfcheck ⑥）
- ast 扫描全部 import，对比 requirements.txt 与 installed，输出
  未装/未覆盖/可选三表，退出码非 0 表示缺必需依赖。打包前必跑。
- requirements.txt 补齐直接依赖声明：numpy/matplotlib/scipy/Jinja2/
  Werkzeug/urllib3/PyYAML/defusedxml（此前靠传递依赖）；可选注释加
  distinctipy/taxburst。
- 排除 docs/（bioaider_ref_src 是他项目参考源码）与 tests/。

### 回归
- tests/_it_concurrency.py：heavy 峰值≤2、light≤4、排队取消、名额归还
  无死锁、日志 30/5 行 —— ALL PASS。
- 28 项页面/API 冒烟 200；全量 py_compile + node --check 通过。
- dist/VirusPlatform 重新打包 1.9GB（无库），含 数据库对接说明.txt。

## 五十七、tool_runs 输出目录规范化（2026-09-07）

### 背景
tool_runs/ 积累 100+ 条目，混有补丁脚本、冒烟测试目录、工具日志残留
（orfipy_..._out），GUI 列表噪音大，无归档/清理机制。

### 设计
- 固定目录（`_` 前缀）：`_archive`（按工具分层归档）、`_tmp`（冒烟/
  一次性实验）、`_scripts`（运维脚本）、`_reports`（跨运行汇总）。
  各含 README.txt 说明用途与规则；GUI `api_tool_runs()` 跳过 `_` 前缀。
- 动态活动区：扁平 `<工具>_<YYYYMMDD_HHMMSS>`，正则
  `^([a-zA-Z0-9]+)_(\d{8})_(\d{6})$` 判定；不匹配者 organize 按规则归位
  （`.py`→_scripts，`_xxx`/`*_out`/含 test|smoke→_tmp，其余保留人工判断）。

### 落地
- `Virus_Platform_Core/tool_runs_admin.py`：ensure_fixed_dirs / status / organize /
  archive / clean；clean 的删除不可逆（shutil.rmtree），CLI 强制提示
  先 --dry-run。
- `main.py tool-runs <status|organize|archive|clean>` 四个动作。
- `Virus_Platform_Core/config.py`：默认 DIRS 补 `'tool_runs'` 键（此前只有设置自定义
  输出根时 apply_output_root 才写入该键，app.py 靠 `DIRS.get(...) or`
  兜底——隐性缺口）；apply_output_root 内懒导入 ensure_fixed_dirs，
  切换输出根时在新根自动预建固定目录。
- 存量归位：3 个补丁脚本→_scripts/，_k2_smoke2/_sracha_test/
  orfipy_viral_contigs.fasta_out→_tmp/。

### 验证
- `tool-runs status`：活动区 101 个/434.1MB，固定区四类统计正常。
- organize/archive/clean --dry-run 均 0 误报（存量已规范）；
  clean 缺期限参数正确报错退出码 1；--help 正常。

## 五十八、CDD / BLAST 语义归位（第一步：文案）+ 验证模块规划（2026-09-08，用户提问）

**起因**：用户问「保守域搜索（CDD）/ 同源搜索复核（BLAST）本质是病毒序列的
验证，应整合到『病毒识别和分类分析』还是保留在『病毒注释分析』」。

**结论：不二选一，按「点击目的」分流。** CDD / BLAST 天然横跨「验证」与
「注释」两阶段，按工具归类必丢一半语义。识别组做批量裁决，注释组做单条
深挖，共用同一套 `/api/tool/analyze` 与 `analysis/` 缓存，仅入口语义与结果
视图不同。规划文档：`docs/候选序列验证模块规划.md`。

**佐证（代码自陈）**：`tools.html` `anaJump()` 内注释明写「t-cdd / t-hom 属于
annotate 组，而 contig 明细表在 virus 组……必须把 g 参数也改成 annotate」——
用户看分类表想复核可疑 contig 却被弹到另一组，靠改 URL 参数才能跳过去，
正是归属错位的证据。

### 第一步落地（已完成）

- **app.py NAV_GROUPS**（annotate 组两条）：
  - `t-cdd` 「保守域搜索（CDD）」→「保守域与功能元件注释（CDD）」
  - `t-hom` 「同源搜索复核（BLAST）」→「同源比对与引物（BLASTN · BLASTX · Primer）」
  - desc 同步改为注释语义。「复核」是识别阶段的词，出现在注释组制造歧义。
- **tools.html**：`t-cdd` / `t-hom` 两卡 h2、卡片说明、两处 JS 注释同步改名；
  交叉引用文案一并更新；hom 卡补注「identity 分档仅反映最近同源水平，正式
  物种界定用 compare 组 SDT」。
- **未动（留第二步）**：`anaJump()` 跳转目标、分类表批量验证按钮——依赖
  验证模块存在，现阶段改动会成半成品。
- **回归**：`py_compile app.py` 通过；tools.html 内联 JS（13 个 block）
  `node --check` 通过。

### HMM 现状核实（规划输入）

- 三库齐备：VOG 4.7GB/49,116 + RVDB 2.7GB/13,679 + vFam 679MB/5,585，
  VOG 元数据与索引就位；本地 CDD 库 419MB + 精选病毒域表 1,580 条。
- pyhmmer **0.12.1 已装**（`C:/Python312/python.exe`，即 `启动平台.bat` 所用
  环境；managed 3.13.12 未装，不影响平台运行）。mmseqs2 已配置
  `tools/mmseqs/bin/mmseqs.exe`。
- `Virus_Platform_Core/hmm_annot.py` 完整（26KB），但**仅被 `orf_annot.py:705` 调用做层 2 功能
  兜底，无独立路由**。
- 关键提示：`annotate_orfs_hmm` 吃 **ORF 蛋白 faa**，不是 contig 核酸，
  验证模块需先补一步 ORF 预测（口径对齐现有「6-frame 最长 ORF ≥50aa」）。

### 第二步方案要点（待用户确认后实施）

**v2 修正（用户指认参照系）**：本模块等价于 MMPV-RNA
`virome_discovery_pipeline/filter_virus.py` 的 **02b_Filter**
（流程位：`02a_Identification → 02b_Filter → 03a_COBRA`，见
`virome_pipeline.py:11`、`:2630`）。据此重写 `docs/候选序列验证模块规划.md`。

**最重要修正：这是「过滤器」不是「裁决器」。** v1 的"判定四档含排除档"思路
作废——02b 哲学是**不确定时倾向保留**，保留规则四条：①已知病毒 taxid 命中
②关键词抢救（phage/virus/capsid/terminase/integrase/prophage…）③比对不可信
抢救（length<50 或 pident<30 或 evalue>1e-5，比对差不足以据此排除）④**全局
no-hit 也保留**。第④条是关键：no-hit contig 恰恰是新病毒来源，是最高价值
候选而非"排除"。改为 02b 口径 filter/strict 双模式。

- 证据链：DIAMOND vs viral_prot（本地）→ HMM 三库 → mmseqs2 CDD → NCBI 按需。
- 宿主归属走平台自有路线，比 02b 更直接：DIAMOND 命中 → `organism_tax.tsv`
  → family → `ictv_family_host.tsv` → plants/fungi/bacteria/invertebrates。
  02b 需 `cdd_classified_taxid.tsv`（从 CDD 域间接推断），平台无此表且
  `cddid_all.tbl` 无分类字段无法自生成 → **决定不补，用 ICTV 路线替代**。
- call 八类：viral_plant / viral_fungi / viral_bacteria / viral_invertebrate /
  viral / ambiguous / non_viral / unclassified；filter 模式保留除 non_viral
  外全部，strict 仅保留 viral*。unclassified 界面单独高亮（新病毒候选池）。
- 三步实施：后端 `Virus_Platform_Core/verify.py` → 前端 `t-verify` 卡 → 打通跳转（消除跨组 hack）。

### 参照系资产现状（核实）

- 02b 依赖 vs 平台：UniRef90 → `viral_prot.dmnd` 239MB（已有，更聚焦）；
  virus.taxid.txt → `taxonomy/nodes.dmp` 可生成；CDD 库+白名单 1,580 条已有；
  `cdd_classified_taxid.tsv` 缺失（走 ICTV 路线替代）。
- diamond `tools/diamond/diamond.exe`、mmseqs2 `tools/mmseqs/bin/mmseqs.exe`
  均已配置；pyhmmer 0.12.1 已装于 `C:/Python312`。
- 平台特有增强：02b 无 HMM 层，平台三库齐备，可捕获远源病毒。

### v3 补入 09b 严格层（用户："09b 还有 cdd 的过滤更严格些"）

MMPV-RNA 有**两个**过滤点，本模块需同时覆盖：
- **L1 = 02b_Filter**（`filter_virus.py`）：早期粗筛，CDD tier + 宿主归属，
  倾向保留（含 ambiguous / unclassified）。
- **L2 = 09b_Analysis_Verify**（`validate_rescue_cdd.py`，见
  `virome_pipeline.py:2244` Part V，由 9a 迁入）：后期精验，**严格在多了
  「属/科核心域」交叉验证**——不只问"像不像病毒"，还问"像不像它声称的那个属"。

L2 六档 EVIDENCE（`validate_rescue_cdd.py:259-330`）：PASS_CORE（属核心命中）/
PASS_CORE_FAM（仅科核心）/ PASS_VIRAL（有 T1 无核心，可能片段）/ AMBIGUOUS /
REVIEW / NON_VIRAL（疑似假阳性宿主）。核心域表含 core100 / core90 / aux 三集合。

**实现必带的坑**：CDD 多标签效应使同一家族多模型重复计数（**实测膨胀 ~6×**），
且跨界同源超家族（RT / 解旋酶 / pol / HSP70）有 viral 与宿主双版本模型，制造
虚假外源域信号。09b v2 解法：`convertalis` 取 qstart/qend 核酸坐标 → 聚类成
位点 → 按成员构成分 viral_only / dual / proximal_dual / confirmed_capture /
pure_clean 五类。

**资产现成，无需自建**（用户指认"应该有的"后核实——此前找漏了，
v3 初稿误判为"缺失需自建"）。全部在 `D:\桌面\延伸基因组\Cenote-Taker3\`：
- `docs/genera_core_aux_species_v3.tsv` 199 属 / 46 科，188 属有 core100
- `docs/family_core_aux_species_v1.tsv`（科级兜底）、`genera_core90_domains.tsv`、
  `all_211_genera_domains.tsv`、`suprafam_sites_final.tsv`（2.6MB，超家族位点）
- `results/cdd_classified_taxid.tsv` 43.9MB / 98,863 域（列 accession/category/
  taxname/taxid/title/div/smp_lineage）← **此前误判为平台缺失**
- `results/cdd_virus_final_v4.txt` 31,636 条白名单（比平台现有 1,580 条全 20×）

**编号兼容性已验证**：核心域表用 `pfam02407`/`cd23247`，而平台 `cddid_all.tbl`
确有 `425413  pfam00001  ...` 记录（NCBI CDD 整合 Pfam，accession 保留 pfam
前缀）→ 与 mmseqs2 CDD 搜索结果直接对得上，无需编号映射。

覆盖实测：Potexvirus(51) / Tobamovirus(34) / Potyvirus(199) / Tombusvirus(15) /
Cucumovirus(4) 均有 core100；**Carlavirus(71) core100 为空 → 必须回退 core90**。
注意 n_species 中位数仅 3（最大 467），≥5 的属仅 72 个 → 低样本属 core100 统计
意义有限，按 09b 原逻辑取 core100|core90 并集，界面标注 n_species 提示参考级。
落地：复制到 `databases/cdd/` 与 `databases/viral_prot/`，约 45MB。
实施步骤：第 0 步资产就位 → 第 1 步 L1 → 第 2 步 L2 → 第 3 步前端 → 第 4 步跳转。

## 五十九、类病毒库下载 + 验证模块按宿主/长度分流（2026-09-08，用户需求）

### 类病毒库就位

从 246 下载 11 个文件到 `databases/viroids-db/`（约 11MB），源
`/home/USER/database/virus-db/viroids-db/`：
`viroids.fasta`（9,353 条，header 带描述）/ `viroids.acc.fasta`（仅 accession）/
`viroids.taxonomy_info.tsv`（2.1MB，accession → realm..species 完整分类）/
`viroids.fasta.blast.db.*` 8 文件；另写 `db_info.json`。
打包 tar 传输（远程 753KB），非逐个 scp。

### 踩坑：BLAST v5 库在中文路径下不可用（平台级，全局适用）

- **现象**：`blastn -db databases/viroids-db/viroids.fasta.blast.db` →
  `LMDB runtime error: mdb_env_open: 系统找不到指定的路径`
- **排查链**：① 复制到 `C:\temp\` 纯 ASCII 路径 → **可用**（100% self-hit）
  ② 移除 `.njs` → 仍失败（不是 .njs 单独的问题）
  ③ 用平台 `makeblastdb 2.15 -blastdb_version 4` 本地重建 → **中文路径下可用**
- **根因：BLAST v5 库的 LMDB 后端无法在含中文的路径下打开。**
- **结论**：平台根目录 `D:\桌面\植物病毒分析平台\` 含中文，**所有 v5 格式的
  blast 库都不能直接用**，必须 `makeblastdb -blastdb_version 4` 重建。
  后续接入任何外部 blast 库一律照此处理。
- 已建 `viroids_v4`（9,353 条，耗时 0.43s），验证：self-hit
  `NC_039241.1 → ref|NC_039241.1| 100.000/333`、近缘 `gb|KT901877.1| 99.700/333`。
- 附：`makeblastdb 2.15` 即使指定 `-blastdb_version 4` 仍会生成 `.njs`，
  本例带不带均可用，最终保留为 `.njs.lmdb-disabled`（保险）。
  另 `blastdbcmd.exe` 存在于 `tools/Blast/bin/` 但未登记进 config。

### 验证模块设计变更（规划 v4）

用户需求：① 宿主分类选择（默认全部）② 按长度分流。

| 长度 | 归属 | 方法 |
|---|---|---|
| ≥1000 bp | 病毒 | DIAMOND **blastp** + mmseqs2 **CDD**（单选/双跑，并集或交集） |
| 200–1000 bp | 类病毒 | **blastn** vs `viroids_v4` |
| <200 bp | 不处理 | — |

分流理由：类病毒全长仅 246–401 nt、**无蛋白编码能力**，blastp/CDD 蛋白层方法
对其完全无效；反之 blastn 查类病毒库对 ≥1000bp 病毒 contig 无意义。

前端：宿主下拉（默认全部）→ 方法勾选 → 并集/交集 → 运行；结果分病毒 /
类病毒两个 Tab。后端 `Virus_Platform_Core/verify.py` 的 `filter_contigs(run_dir, host='all')`
先筛选后分流，两支分别 `run_virus_arm()` / `run_viroid_arm()`。
类病毒判据建议：identity ≥80% 且覆盖 ≥60%（200–300bp 放宽到 ≥40%）。

## 六十、候选序列验证模块 L1 实施（2026-09-08，用户需求）

验证模块（对齐 02b/09b）的 L1 后端 + 前端已落地，L2（09b 核心域精验）待实施。

### 关键设计决策（用户拍板）

- **不预测 ORF**：DIAMOND blastx + mmseqs 均自带翻译，直接吃核酸 contig
  （与 02b 同构，不落 ORF 中间文件）。
- **宿主归属唯一来源 `virus_classification.tsv`**（工具④产物，8 级谱系）；
  **不引入 organism_tax.tsv**——用户："完全不需要，不兜底没意义"。
  `ictv_family_host.tsv` 做科→宿主映射。筛选语义为「含」匹配
  （host.source 是复合值如 `algae+fungi+plants`）。
- 长度分流：≥1000bp 病毒支（blastx+CDD，并集/交集）；250–1000bp 类病毒支
  （blastn vs viroids_v4）；<250bp 跳过。下限 250 由用户从 200 提上来。

### 实现

- **Virus_Platform_Core/verify.py**（新增，约 450 行）：`verify(run_dir, fasta, host, methods,
  combine)` 主编排；`split_by_length`（重复 header 追加 `_2` 去重）/
  `run_blastx` / `run_cdd` / `run_viroid_blastn` / `viroid_call` / `virus_call` /
  `combine_pass` / `_load_cls_family` / `_filter_by_host`。
  过滤器语义：no-hit 保留为 `unclassified`（新病毒/新类病毒候选池）。
- **app.py**：`_tool_job_verify`（run 引用优先 viral_contigs.fasta，否则独立
  fasta；引用 run 时复制其 virus_classification.tsv 供宿主归属）；
  `TOOL_REGISTRY['verify']`；`NAV_GROUPS.virus` 第 4 条；新增
  `/api/tool/verify_result` 路由（calls.tsv + summary.json）。
- **tools.html**：`<section id="t-verify">` 卡（宿主下拉/方法/并集交集 + 结果
  分色表）+ JS（loadVerifyRuns / runVerify / loadVerifyResult / renderVerifyTable）。

### 坑与验证

- blastn 的 `-outfmt '6 qseqid sseqid ...'` 必须作为**单个参数**传（否则
  "Too many positional arguments"）。
- DIAMOND blastx stitle 的 organism 名在方括号 `[Tobacco mosaic virus]` 里——
  本想据此推 family，后因 organism_tax 弃用而移除（见上）。
- `ictv_family_host.tsv` 的 host.source 是复合值，精确 `==` 会漏判，改子串包含。
- **端到端通过**：example_mix.fasta（5 病毒 + 1 类病毒 PSTVd 359bp）正确分流，
  病毒支 blastx/CDD 双跑、宿主筛选 plants、类病毒支 blastn 均验证。
- 回归：py_compile app.py + Virus_Platform_Core/verify.py 通过；tools.html 内联 JS（13 block）
  node --check 通过；test client `/tools?g=virus` 200 + t-verify 卡 + 导航条目。

### 待办（L2 + 跳转）

- L2：`validate_cdd`（CDD tier + 属/科核心域 → 6 级 EVIDENCE）+ 超家族归并。
- 第 4 步：`anaJump()` 跳本组 `#t-verify`；分类表加「批量验证」按钮。
- 第 0 步收尾：复制 Cenote-Taker3 七个文件（L2 需要）。

## 六十一、验证模块收尾：跳转 + 粘贴 + 桑基/旭日重绘（2026-09-08，用户需求）

用户决策：**L2 不做**（09b 核心域精验 + 超家族归并取消）；第 4 步跳转 + 分类表
批量验证按钮 + 独立 fasta/粘贴输入 + 结果重绘桑基图和旭日图。

### 实现

- **批量验证按钮**：contig 分类报告区顶部「🛡 候选序列验证」→ `jumpVerify()`，
  跳本组（virus）`#t-verify`，无跨组 hack；`processVerifyJump` 自动选中该运行。
  （四件套的 `anaJump` 保持跳注释组不动——那是单条深挖，语义不同。）
- **输入三方式**：① 选 contigs 运行（viral_contigs.fasta）② 独立 FASTA ③ 粘贴
  （`pasteSeq` → `/api/paste_input` 已有，卡片直接复用）。
- **结果重绘**：`#t-verify` 结果区加两张 Plotly 图——桑基图（宿主→判定，
  含 unclassified 独立流）+ 旭日图（分支→判定→科）。

### 全流程验证（真实 contigs 运行）

用真实运行 `contigs_20260906_040347`（148 条 viral contig）端到端验证：
- 分流正确：121 条（27 条 <250bp 跳过），110 类病毒支 + 11 病毒支。
- **重要观察（非 bug）**：该批数据 94% 判 unclassified——因为 kunpeng 分类
  score 仅 0.001-0.003 的低置信短片段，blastx（viral_prot 库对远源覆盖弱，仅
  2 条命中 identity 45%/33%）与 CDD（白名单模式，PRK/COG/KOG 细菌/真核域不在
  病毒白名单内，正确被过滤）都拿不到强病毒域证据。只有 2 条命中 Potexvirus
  核心域（cd23246/47、pfam01443）判 viral。符合「过滤器不裁决」——unclassified
  保留给人看，不强判。
- 宿主筛选：host=plants 时 host_dropped=0 正确——该批数据所有科的 host.source
  都含 plants 或复合值（Rhabdoviridae=fungi+invertebrates+plants+soil+vertebrates），
  无纯真菌/细菌科可剔除。「含」匹配语义符合"找植物病毒"。
- 类病毒链路：完整 PSTVd 359bp → 正确判 viroid/Pospiviroidae；180bp 短序列
  正确被 <250bp 跳过。
- 粘贴链路：paste_input 200 → verify 全通。
- 回归：py_compile + node --check + test client 全绿。

### 遗留认知（重要，后续可参考）

- `viral_prot.dmnd` 对远源病毒灵敏度有限（blastx 只命中 identity 33-45% 的
  极近缘）；真正远源的要用 HMM 层（本模块默认关，未接）。若后续需要，把
  `annotate_orfs_hmm` 作为第三路接入即可。
- ICTv `host.source` 是"该科可感染宿主范围"而非"实际宿主"，复合值居多，
  宿主筛选是粗筛而非精确。
- 低置信 contig（kunpeng score<0.01）大量存在时，验证模块会如实返回
  unclassified——这是对的，但用户需理解 unclassified ≠ 非病毒。

---

## 2026-09-08 · 阶段 ③b verify 接入管道（闭环闭合）

### 背景与决策

此前 kunpeng contig 分类（③ 组装）与 verify 双证据验证是两个独立工具，
未串联。本轮把 verify 整合为管道阶段，置于 `03_assembly` 之后：

- **产物目录 `03b_verify`**（大王拍板，紧邻上游便于对照）
- **orf 输入收窄**：只吃 verify 判定 `known`/`novel` 的 contig（大王拍板）
- `domain_only` / `unclassified` 不下传注释，但 `unclassified` 保留为
  新病毒候选池，不删除
- 管道 ③ 补产 `virus_classification.tsv`，使 verify 不再依赖工具④
- verify 缺工具（DIAMOND / MMseqs2）时优雅剔除：WARN 跳过，不阻断下游

### 本轮修复的 3 个真 bug

**① `STAGE_VIEW` / `STAGE_OUTPUTS` 路径多写一层阶段目录前缀**

原写 `'verify': '03b_verify/calls.tsv'`，但 `_list_outputs()` 与 `view_ok`
已按 `os.path.join(sample_dir, dirname, view)` 拼路径（`dirname` 本身即
`03b_verify`），实际变成 `03b_verify/03b_verify/calls.tsv` → 文件不
存在 → 卡片 `outputs=[]`、`view=null`。
**修复**：改为 `'verify': 'calls.tsv'` / `['calls.tsv', 'summary.json']`。
教训：`STAGE_OUTPUTS`/`STAGE_VIEW` 的所有路径一律**相对阶段目录**。

**② `n_calls` / `out_dir` 在写盘之后才赋值**

`Virus_Platform_Core/verify.py` 里 `summary['out_dir']`、`summary['n_calls']` 原在
`json.dump` 之后 → 落盘 `summary.json` 缺这两键 → `_sum_verify` 显示
「共 0 条：known 1」。内存对象（C.verify）有值，落盘文件没有，这个
差异非常隐蔽。
**修复**：把两个赋值移到 `json.dump` 之前。

**③ `_load_cls(run_dir)` 只读根目录，管道布局下谱系全空**

分类表在管道模式下落于 `<run_dir>/03_assembly/virus_classification.tsv`，
而 `_load_cls` 只找 `<run_dir>/virus_classification.tsv` → 所有谱系字段
（taxid / taxon / realm..species）与 `host_source` 全空。
这个 bug 不报错、不阻断，只让 verify 的核心信息静默损失。
**修复（追溯版）**：`verify()` 与 `_load_cls()` 均加显式 `assembly_dir`
参数，管道调用点传 `a_dir`（`<sample_dir>/03_assembly`）。
与平台既有约定一致——`orf.py:L217` / `phylo.py:L492` / `primer.py:L182`
全用 `assembly_dir or os.path.join(sample_dir, '03_assembly')` 模式，
verify 是唯一的例外，本轮归队。`assembly_dir` 缺省时仍探测
`run_dir/` → `run_dir/03_assembly/`，兼容未传参的旧调用。

**两种入口的语义差异（关键背景）**

| 入口 | 分类表位置 | 谁负责放置 |
|---|---|---|
| 管道 ③b | `<sample_dir>/03_assembly/` | ③ 组装阶段产出 |
| 工具④ | `<run_dir>/` 根 | `app.py:2028` 主动复制 |

因此该表位置无法靠猜统一，必须由调用者声明。

**同类读取点全景（4 处，另 3 处本就正确、不动）**

| 位置 | 拼法 | 布局 | 判定 |
|---|---|---|---|
| `verify.py:248` | 显式参数 + 缺省探测 | 双 | 本轮修 |
| `phylo.py:511` | `a_dir/` | 管道 | 正确 |
| `viz.py:782` | `run_dir/` | 工具 | 正确 |
| `ncbi_submit/store.py:823` | `root/run/` | 工具 | 正确 |

### 实测证据

- `_stage_verify` 端到端实跑（真实 contig，118.9s / 165.4s 两次）：
  blastx 命中 NP_619711.1、CDD 命中 `pfam00998;pfam00729` tier=1 →
  判定 `known`。修复③后 calls.tsv 谱系完整：
  `taxid=12268 / taxon=Carnation ringspot virus / family=Tombusviridae /
  genus=Dianthovirus / species=Dianthovirus dianthi / host_source=plants`
- `_load_cls` **五场景单测**：①显式 assembly_dir（管道）②缺省探测
  （工具根目录）③缺省探测（03_assembly 兜底）④显式时优先于根、
  不误读根目录的表 ⑤两处皆无 → 返回空 dict
- **纯管道布局端到端**（77.3s）：分类表**仅在** `03_assembly/`、根目录
  无副本时，谱系 / host_source / n_calls 全部正确
- **工具模式端到端**（76.9s）：分类表在 `run_dir` 根时未受影响，谱系完整
- `verified_contigs()` + `extract_viral_contigs(only=...)` 收窄四场景：
  4 条 contig（known/novel/domain_only/unclassified）→ 收窄 2 条，
  fasta 实际内容仅 `NODE_1`/`NODE_2`
- HTTP API（中英双语）：14 阶段、verify 紧跟 assembly、
  `summary=共 1 条：known 1` / `1 total: known 1`、`outputs` 2 项、
  `view` 实测 HTTP 200 + `text/tab-separated-values` + 35 列
- 三个真实样品回归（ERR7586041 / NX-6 / SRR39909438）中英双语全绿
- `py_compile` + `node --check` + 登记项一致性检查全绿

### 已知未改（记录）

- 约 50 个任务归档 json（9/6-9/8 批次）丢失，不在回收站
- `results/ERR7586041/03_assembly/contig_classify` 是空目录，`spades/` 含
  10 个真实产物（spades.log / contigs.fasta / GFA 等）；两者均为 ③ 组装
  阶段的工作目录（`assembly.py:203/217` 写 spades、`:362` 写
  contig_classify），**不是残留**，未删。
- 管道页 `STAGE_PARAMS.subsample` 卡片是死控件：`collectParams()` 不读
  `#subsample` 元素，填值不生效（后端永远用设置页默认值，默认 0）。

---

## 2026-09-08 · 完整注释档去除子采样

### 需求

大王指令：「完整注释 去除子采样」。

### 改动

`webapp/static/app.js:1795` `PIPE_TEMPLATES` 的 `full` 档由
`stages: null`（全部可用阶段）改为保留 `stages: null` +
新增 `exclude: ['subsample']` 字段；`runTemplate()`（L1800）在展开前
按 `exclude` 过滤 `usable`。

用排除法而非显式全列表，好处是将来新增阶段仍自动进 full 档，
只有明确该排除的才写进 `exclude`。

```js
{ id: 'full', label: '🔬 完整注释',
  tip: '全部可用阶段（含宿主预测 / 功能注释 / 引物设计 / 基因组图），不含子采样',
  stages: null, exclude: ['subsample'] },
```

### 为什么前端改就够

`subsample` 阶段的执行体 `_stage_subsample`（`Virus_Platform_Core/pipeline.py:269`）是
参数驱动的：`if not (C.subsample and C.subsample > 0): 跳过`。
执行循环是 `for key in _stage_execution_order(stages)`，只跑**已选阶段**；
full 档排除后 `subsample` 不在 `stages` 里，函数根本不被调用。

`C.subsample` 的全部读取点（`pipeline.py:272/284/290/292/294/298`）
都在 `_stage_subsample` 内部，无其他阶段消费该参数，因此无需后端配合。

### 实测证据

- 展开验证（复刻 `runTemplate` 逻辑）：
  - `fast` 4 阶段、`std` 9 阶段、`full` **14 → 13 阶段**
  - full 展开为 `fastp → fq2fa → host → virus → assembly → verify →
    hostana → orf → orfa → phylo → primer → gbdraw → report`
  - 三档均不含 `subsample`；full 的 `hostana`/`orfa`/`primer`/`gbdraw` 保留
  - 模拟 `hostana` 不可用：full 降为 12 阶段，仍不含 subsample
- 后端 `_stage_execution_order` 三档实算：阶段数与传入一致，
  无 `subsample`，顺序为稳定拓扑序
- `node --check webapp/static/app.js` 通过

### 顺带查清（未改）

- 管道页 `STAGE_PARAMS.subsample` 卡片是死控件：`collectParams()`
  （`app.js:2048`）不含 `subsample` 键，页面填的数值不会传到后端；
  后端 `app.py:6129` 用 `_val('subsample', 0)` 取设置页默认值（默认 0）。
  三档现已全不含 subsample，该卡片只在手动单阶段运行时出现。
- 快速筛查档 `report` 声明 deps `['virus','assembly']`，档内无 assembly。
  后端不补齐依赖（deps 只约束已选集合内先后），report 照跑且
  `build_report` 各处有 `os.path.isfile` 保护可降级；但跑完后卡片
  `deps_ok` 判定会显示 blocked（`pipeline.py:1193`）。待大王定夺是否
  改 deps 或调整档位定义。

---

## 2026-09-08 · 修复 subsample 死控件（collectParams 未接线）

### 问题

管道页 `subsample` 阶段卡片里的数字输入框是死控件：`collectParams()`
（`app.js:2048`）不读 `#subsample` 元素，页面填的数值永远传不到后端，
后端一律用设置页默认值。

### 差集审计（精确口径）

抠出 `STAGE_PARAMS`（`app.js:1926`）声明的全部 `id` 与 `collectParams()`
实际读取的键做双向差集：

- 声明 24 个 → 收集 27 个
- **死控件：仅 `subsample` 一个**
- 反向 4 个（`threads` / `confidence` / `chunk_dir` / `force`）是全局参数，
  不在阶段卡片里，正常

### 改动

`webapp/static/app.js:2052` 在 `collectParams()` 补一行：

```js
subsample: +($('subsample')?.value) || undefined,
```

沿用平台既有惯例（`+($('id')?.value) || undefined`）：元素不存在或值为空
时归 `undefined`，`runStages()`（L2083）的 `filter(([_k, v]) => v !== undefined)`
会剔除该键，后端 `_val('subsample', 0)` 回落到设置页默认值。

### 实测证据

**行为矩阵**（构造受控 DOM 执行 `collectParams`）：

| 场景 | 输出 | 发送 |
|---|---|---|
| 卡片存在，值 100000 | `100000` | 发送 100000 |
| 卡片存在，值 50000 | `50000` | 发送 50000 |
| 卡片存在，值清空 | `undefined` | 不发，用后端默认 |
| 卡片不存在（阶段未渲染） | `undefined` | 不发，用后端默认 |

**端到端四场景**（真实 `fastp_R1.fastq.gz` 4.42MB，直调 `_stage_subsample`）：

- `n=0` → 正确跳过，零产物
- `n=20000` → 实际截取 4.42MB → 0.70MB，0.48s，`subsample.json` 记 `n_pairs=20000`
- 同参数复跑 → 断点复用，0.00s，日志「子采样已完成（20,000 对），复用」
- 改参数 `n=5000` → 触发重跑，0.17MB，0.12s

**API 层解析**（`_analysis_kwargs`，设置页默认 `subsample=0`）：

| 请求体 | `run_analysis(subsample=)` |
|---|---|
| `{'subsample': 100000}` | 100000 |
| `{'subsample': 5000}` | 5000 |
| `{'subsample': 0}` | 0 |
| `{}` / `null` / `''` | 0（回落默认） |

`node --check webapp/static/app.js` + `py_compile` 全绿。

### 关联

三档模板现已全部不含 `subsample` 阶段（详见上一节），该卡片只在手动
单阶段运行时出现；接线后手动运行时填值才真正生效。

---

## 2026-09-08 · 修复 report 阶段依赖声明过宽

### 问题

`STAGE_REGISTRY` 里 `report` 的 deps 声明为 `['virus', 'assembly']`，
但快速筛查档（`fastp → host → virus → report`）不含 assembly。

后果：跑完快筛后，若 report 尚未执行，`pipeline_overview`
（`Virus_Platform_Core/pipeline.py:1259`）判定 `status = 'ready' if deps_ok else 'blocked'`，
而 `deps_ok = all(ran_map.get(d) for d in STAGE_DEPS[key])`
（L1232，`ran_map` 按 PIPELINE_STAGES 顺序累积），assembly 未跑
→ `deps_ok=False` → report 卡片显示 **blocked**，用户误以为不可运行。

### 审计：全表 deps 逐个复核

| 阶段 | deps | 判定 |
|---|---|---|
| verify / consensus / hostana / orf / phylo / primer / gbdraw | `['assembly']` | 合理（输入都是 contig） |
| orfa | `['orf']` | 合理 |
| **report** | ~~`['virus','assembly']`~~ → `['virus']` | **过宽** |

报告是汇总展示层，核心输入是病毒筛查结果；assembly 产物只是让其更丰富
（多几个卡片）。`build_report`（`Virus_Platform_Core/viz.py:446`）对每个数据源都有独立
`os.path.isfile` 保护，缺 assembly 时逐项降级，不会失败。

### 改动

`Virus_Platform_Core/pipeline.py:635` 一行：

```python
'report':    {'deps': ['virus'],              'fn': _stage_report},
```

### 实测证据

**状态判定**（`pipeline_overview` 完整路径调用，NX-6：virus 已 done、
assembly 未跑）：

| 阶段 | 改动后 |
|---|---|
| virus | `done`（检出物种 2064，提取病毒 reads 14,826,661 对） |
| assembly | `ready` |
| **report** | **`ready`**（改动前 blocked） |
| verify/hostana/orf/phylo/primer/gbdraw | 仍正确 `blocked` |

**HTTP API**（服务重启后，中英双语全绿）：

| 样品 | virus | assembly | report |
|---|---|---|---|
| NX-6 | done | ready | **ready** |
| ERR7586041 | done | done | done |
| SRR39909438 | ready | ready | blocked（依赖确未满足，正确） |

**降级实测**：构造只含 `02_virus_screen`（无 assembly、无 host_removal）
的测试样品，`build_report` 0.45s 生成 50KB `report.html`，含病毒卡片与
分类树、HTML 结构完整；桑基图因缺 `01_host_removal/stats.json` 自动跳过。

**闭环实测**：同一测试样品跑 report 前 `ready` → 跑后 `done`
（摘要「报告已生成」、产物 `07_report/report.html`），全程 assembly 未跑。

### 附带清理

- 停掉两个测试遗留实例（PID 284256→8989、286292→8900）
- 重启主服务（8765）以加载新代码：Flask `debug=False` 不热重载，
  源码改动必须重启才生效（本轮一度因未重启导致 API 仍返回旧值）

### 排查教训

`pipeline_overview(sample_dir, ...)` 形参是**完整路径**，传样品名
（如 `'NX-6'`）会静默拼成相对路径并全部返回 `ready`，不报错。
调试时须传绝对路径，否则得到假阴性。

---

## 2026-09-08 · 修复 FASTQ read ID 后缀不匹配导致的宿主去除/病毒提取静默失效

### 问题

用 MGI 数据（`fastq/GQMIX.R1.fq.gz` + `GQMIX.R2.fq.gz`）建测试样品
跑全流程，出现一组自相矛盾的症状：

| 阶段 | 症状 |
|---|---|
| ① 宿主去除 | `kept_R1` 507,368 reads = 输入 reads（**一条都没剔除**），但 `stats.json` 声称 `dropped_pairs: 452400`、`host_ratio: 0.471364` |
| ② 病毒筛查 | `viral_R1/R2.fastq.gz` 均 **0 字节**，但 `summary.json` 声称 `viral_pairs: 11104` |
| ③ 组装 | SPAdes 报 `== Error == file is empty: ...\in_R1.fastq.gz`，退出码 64 |

三个阶段都「声称成功、产物为空」，属于典型的静默失效。

### 根因：read ID 后缀在两条比对路径上不一致

FASTQ 头有三种格式，平台此前只遇过第一种：

| 来源 | FASTQ 头 | 首 token 有无 `/1` `/2` |
|---|---|---|
| 纯 Illumina | `@A00151:1303:H527CDSXF:1:1101:4901:1597032` | 无（NX-6 即此类，故从未暴露） |
| **MGI/华大** | `@LH00278:577:23VC2NLT4:8:1101:1205:26992/1` | **有**（后缀与 ID 同 token） |
| SRA 下载 | `@SRR39909438.9 A00459:...:1000/1` | 无（后缀在第二 token，天然被丢弃） |

kunpeng/kraken 输出的 read 名一律**无后缀**
（`LH00278:577:23VC2NLT4:8:1368:3171:24708`），而 MGI 原始 FASTQ 头带
`/1` `/2`。seqkit `-f` 精确匹配是整行/整 token 匹配，两边对不上：
**实测前 100,001 个 read 名与 keep_ids 交集为 0**。

两个 bug 叠加掩盖了彼此：

1. `_seqkit_filter` 用 `-v` 反选，匹配失败 = 全部保留，`kept` 数看着正常；
   而 `dropped` 直接拿 `len(ids)` 充数，掩盖了「一条都没剔除」。
2. `_extract_reads_seqkit` 返回 `len(keep_ids)`（输入 ID 数）而非实际
   提取条数；seqkit 无匹配**不报错**，静默产出空文件 + 虚假计数，
   直到 SPAdes 才炸出来。

### 审计：匹配方案实测选型

修复方向有两个候选，用真实数据（452,400 个宿主 ID）实测对比：

| 方案 | 5,000 个模式 | 452,400 个模式 |
|---|---|---|
| **精确匹配 + 写进后缀** | 2.4s | **2.4s** |
| `seqkit grep -r` 锚定正则 | 197.6s | 卡死（实测 15 分钟未结束被 kill） |

精确匹配是 hash 查表 O(1)，耗时与模式数无关；`-r` 是逐 read 逐模式的正则
扫描 O(n×m)，45 万条必然灾难。**结论：放弃 `-r`，改用「探测输入后缀 +
把后缀写进模式文件 + 精确匹配」**。

顺带实测否掉的两个选项：

- 子串模式（不带 `-r` 的裸 ID）会**误匹**：`READ:1:100` 匹到 `READ:1:1000`
- 原样精确（不带后缀）：**0/5 命中**，输出 30 字节空 gz

### 改动

**`Virus_Platform_Core/host_removal.py`** — 新增 ID 规范化与后缀探测：

| 函数 | 作用 |
|---|---|
| `_strip_pe_suffix(s)` | 剥掉尾部的 `/1` `/2` |
| `_norm_read_id(name_line)` | 取首 token 并剥后缀，**两条比对路径共用** |
| `_id_patterns(ids, pe_suffix='')` | 生成 seqkit 模式行（逐行一个，精确匹配用） |
| `detect_pe_suffix(fastq_path)` | 读首条头探测实际后缀，返回 `''`/`'/1'`/`'/2'` |

`_seqkit_filter` 重写为「探测后缀 → 写模式 → 精确匹配（去掉 `-r`）」，
`dropped` 改为 `n_in1 - kept1`（seqkit `stats` 实测差值），不再用
`len(ids)` 充数；`filter_paired_fastq` / `filter_single_fastq` 的内置
Python 回退路径统一走 `_norm_read_id`。

**`Virus_Platform_Core/virus_screen.py`** — `_extract_reads_seqkit` 同样改为后缀探测 +
精确匹配（去掉 `-r`），并新增 `_count_reads_seqkit` 用 `seqkit stats`
实测产物条数，无匹配/配对不等时抛异常，不再静默虚报。内置回退路径
改用 `_norm_read_id`。

### 实测证据

**单元级**（真实数据，452,400 宿主 ID / 2,448 病毒 ID）：

| 环节 | 输入 | 结果 | 耗时 |
|---|---|---|---|
| 宿主过滤 | 507,368 reads | kept 54,968，**实测剔除 452,400**（与 stats 一致） | 13.2s |
| 病毒提取 | 507,368 reads | 返回 2,448 对 = 实测 R1/R2 条数 = 目标 ID 数 | 5.3s |
| 后缀探测 | — | MGI R1→`/1`、R2→`/2`、Illumina/SRA→`(无)` | — |

**全流程**（`/api/pipeline/GQMIX/run`，host → virus → assembly）：

| 指标 | 修复前 | 修复后 |
|---|---|---|
| 宿主剔除 | 0 条（声称 452,400） | **452,400 条**（实测一致） |
| `host_ratio` | 0.471364（虚报） | **0.89166**（真实） |
| viral fastq | 0 字节（声称 11,104） | **2,448 对**（实测一致，0.083MB） |
| 组装 | SPAdes 退出码 64 | **11 条 contig**，12,611 bp |

宿主占比从虚报 47.1% 修正到 89.2%——枸杞数据宿主 reads 占绝大多数，
后者才合理。三个阶段状态全部 `done`。

### 关联

- 修复前所有样品均为 Illumina 数据（无 `/1` 后缀），故此 bug 长期未暴露
- 新建 GQMIX 测试样品而非覆盖 NX-6：NX-6 的 virus 产物是真实跑出的
  （占 2.6GB），且 GQMIX 是不同数据
- NX-6 `input.json` 相对路径指向的原始 fastq 已删，无法实跑；
  `/api/pipeline/NX-6/run` 报 HTTP 400「路径不存在」

### 排查教训

- **同一份数据在两条比对路径上必须共用同一个规范化函数**。此前
  `_read_id`（不剥后缀）散落在多处，新增 `_norm_read_id` 后已全量替换
  （`grep _read_id\(` 复核确认无遗漏）。
- **统计值必须来自实测**。`dropped = len(ids)`、`return len(keep_ids)`
  这类「拿输入数当输出数」的写法，在工具静默失败时会产出完美自洽的
  假报告，比直接报错危险得多。
- **seqkit 无匹配不报错**，任何 `grep` 提取后都应实测产物条数。
- **性能方案要看复杂度而不只是能不能跑通**。`-r` 在小样本上只是「慢」，
  在真实规模上是「卡死」；5,000 vs 452,400 的耗时对比（197.6s vs 2.4s）
  才是有决策力的数据。
- **`force` 不会清 checkpoint 标志**。重跑前须手动删除 `.host_removal.done`
  / `.virus_screen.done` 与旧产物，否则阶段会被 skip 而显示 `done`。

---

## 全局上下文（当前样品 / 项目）+ 全局清理（2026-09-11，用户要求）

### 需求与背景

用户提出两点：①「样品创建 / 批量导入」里的**样品名 / 项目**如何与下游关联，
怎么更好地做成全局使用；②为避免切换模块导致各模块运行进度与关联丢失，
要一个**全局清理**按钮，一次清掉所有模块的相互关联与运行。

### 现状勘察结论（改造前）

- **样品名 = 唯一主键**：`_safe_sample_name()` 归一化后即目录名
  `run/results/<样品名>/`，全平台靠"扫目录 + 拼路径"找它，**没有注册表/数据库**。
  真正的消费者只有：管道页、结果中心、批处理队列、任务结果预览、
  **kvsuite 工具卡选样**（`tool_jobs.py` 拼 `sample_sheet.tsv`）、
  MSA/建树下拉（`refs.py`）、LOGAN 溯源（`logan_trace.py`）。
  其余 21 个专项工具只认显式文件路径，与样品名无关。
- **项目名 = 纯展示标签**：只写进 `00_prep/input.json` 与 `project.json`，
  仅在样品卡片 🏷、项目筛选下拉、kvsuite 选样弹窗里显示；
  **不参与任何路径拼接/计算/模块关联**，且改造前没有任何改名接口。
- **切模块丢的不是任务**：任务在服务端线程里跑，`startPolling()` 会在每页重新接上；
  真正丢的是页面级 UI 上下文（`curSample` / `curStages` / `curBranch` 等），
  因为一级导航是整页刷新。`vp_last_sample` 写了却从不读，是关键缺口。
- **清理入口全是局部的**：`/api/queue/clear_finished`（只清终态）、
  `/api/tasks/clear_finished`（不碰磁盘归档）、逐样品 `/clear` `/delete`、
  逐运行 `/api/tool/runs/<run>/delete`。全仓搜 `cancel_all|purge|stop_all` → 0 命中。

### 实现（用户选定口径）

用户明确选择：**非破坏性**清理（永不删结果文件）+ 样品与项目**都做全局** +
按钮放**顶部导航常驻**。

**后端**

| 文件 | 改动 |
|---|---|
| `web/tasks.py` | 新增 `active_ids()` / `cancel_all()` / `purge_all(include_archive=)`；并修 `_recover_interrupted` 误删 `queue.json` 的坑（见坑记录 8） |
| `web/samples.py` | `SampleQueue.reset_all()`：连 queued/running 一起清空并落盘 |
| `web/tasks_api.py` | 新增 `POST /api/global/reset`：cancel_all → queue.reset_all → 等待收敛 ≤2s → purge_all；返回各项计数 + `active_left`，并显式回 `kept_results: true` |
| `web/pages.py` | `/results` 每行补 `project`（新增 `_sample_project()`，口径与 `/api/samples` 一致：`input.json` 优先、清单兜底） |

`purge_all` 的边界写死：**只动任务状态**（内存记录 + `tasks/<tid>.json`），
`results/` `tool_runs/` `downloads/` `submissions/` 一律不碰；
`include_archive=False` 时复用 `delete()` 是错的（它会顺手 unlink json），
故改为直接 `pop` 内存 + 单独遍历文件。

**前端**

- `app.js` 新增 `VP_CTX`（真源 = `localStorage['vp_ctx_sample'/'vp_ctx_project']`）、
  `gxModal()`、`gxSyncProjSelect()`、`gxPickSample()`、`gxPickProject()`、
  `gxGlobalReset()`、`gxProjChanged()`、`gxApplyResultsFilter()`、`gxApplyPage()`；
  `document` 上的 `vp-ctx` 事件做跨页广播。
- 顶部导航由 JS 注入（与 `injectLangSwitch` 同法，零模板改动）：样品胶囊 /
  项目胶囊 / 🧹 全局清理。清理确认后会清浏览器关联键 + 整页刷新复位 DOM 态，
  结果经 `sessionStorage['vp_reset_result']` 带到刷新后以 toast 回显。
- 接入点：`/pipeline`（无 `?sample=` 时用全局样品自动接上，这是用户主诉）、
  `/samples` 与 `/pipeline` 的项目下拉、`/results`（新增项目列 + 筛选）、
  `/tasks`（新增「只看当前样品（<样品名>）」）、kvsuite 选样弹窗
  （默认勾选全局样品、按全局项目筛选、全选只作用于可见行）。
- `/results` 样品表新增项目列，`filerow-tr` 的 `colspan` 4 → 5。

### 验证

- `tests/_it_global_reset.py`：临时目录造数据（沙箱要求路径必须在平台内，
  故用 `run/_it_global_reset_tmp/`，跑完整体删除），覆盖 `_recover_interrupted`
  防幽灵与 `queue.json` 存活、`cancel_all`、`reset_all`、`purge_all` 边界
  （含 `include_archive=False` 真正保留归档，以及 `results/`/`tool_runs/` 分毫不动）、
  `/api/global/reset` 端到端、`/results` 项目字段。
- `tests/_it_global_ctx_ui.py`：起临时实例 + selenium(Edge headless) 做 39 项
  浏览器断言（导航注入、样品/项目弹窗、管道页自动接上并刷新后仍恢复、
  任务中心筛选、结果中心筛选、清理按钮 fetch 打桩并校验请求体 + 关联键清除 +
  刷新后 toast 回显、kvsuite 选样弹窗接入）。
  **坑**：服务日志不能挂 `subprocess.PIPE`——werkzeug 每请求一行访问日志，
  64KB 管道写满后服务阻塞在 write 上，页面加载随之超时（排查花了很久）。
- `python tests/_route_inventory.py`：新增 1 条路由，已 `--save` 更新基线（192 条）。

### 遗留 / 未做

- **项目名仍不可改**：没有 `POST /api/samples/<s>/project`，创建后只能手改
  `00_prep/input.json`。全局项目筛选因此只能筛选"创建时填对"的样品。
- `/api/tool/runs` 硬截断 30 条，第 31 条以后无接口可清（全局清理刻意不含它）。
- 另有独立发现（本次未改）：`consensus` 缺在 `PIPELINE_STAGES` /
  `STAGE_SUMMARIES` / `STAGE_OUTPUTS` / `STAGE_VIEW` 里，虽在 `STAGE_ORDER`
  且会写 `03c_consensus/`，但永不计入 `stages_done`、不显示进度卡；
  `_safe_sample_name`（建目录）与 `web/common._safe_sample`（读路由）两套
  归一化口径不一致，中文样品名会被压成 `A`/`1` 之类甚至撞名；
  `backfill_manifest_from_products` 无任何调用点（死代码）。
- **运行中的平台需重启一次**：前端静态资源会热更新，但新路由
  `/api/global/reset` 要重启服务才加载；未重启时点清理会收到 404，
  前端已给出「请重启平台」的定向提示。

---

## 切换模块不归档任务 + 全局运行条 + 手动归档（2026-11-02，用户要求）

### 需求

用户看着「公共检索 → ④ 运行任务」里两张已完成的卡片说：切模块 / 切功能时任务
被自动收进历史，**希望切模块时原来的运行继续留着不归档**，只有自己手动点
「归档」才把旧任务回收。追问后确认复现路径是 **工具页**：任务跑完 / 切到别的
功能后，运行卡片只剩在「🕘 历史运行」盒子里；要的口径是：

1. **任何模块页都能看到所有正在跑的任务**，切模块不丢；
2. 任务跑完也留在列表里，**只有点「📦 归档」才进历史归档，且归档可恢复**。

### 勘察结论（改造前）

- 后端**没有**「切模块即归档」的逻辑：任务跑在服务端线程里，`tm.list_all()`
  按内存返回；页面切换只是整页刷新，`startPolling()` 每页重新接上。
- 真正的问题是**「在哪里看」**：各模块页各自按任务名前缀过滤（`meta.html`
  的 `refreshTasks()` 只认 `/^(元数据|公共检索|AI 元数据补全|检索绘图)/`），
  工具页早已移除底部任务面板（`app-jobs.js`：「工具页已移除底部任务面板」），
  跑完的工具运行只出现在 `app-runhistory.js` 的「🕘 历史运行」盒（数据源
  `/api/tool/runs`，与 `/api/tasks` 是两套账）。跨模块看不见 ≠ 被归档。
- 「历史归档」的旧定义 = **磁盘 `tasks/*.json` 里不在内存的记录**（服务重启前
  或内存回收淘汰的），任务中心小标题原本写「服务重启前的任务记录」。

### 实现

**后端**

| 文件 | 改动 |
|---|---|
| `web/tasks.py` | 抽出 `_rec_json()`（`_persist` 与归档共用同一 JSON 结构）；新增 `_archive_dir()`（`run/tasks/_archive/`）、`_archive_path()`、`archive(tid)`（终态才可归档，写 `_archive/<tid>.json` 并给 `archived/archived_at`，随后从内存与顶层 `tasks/<tid>.json` 移走，避免重复计数）、`unarchive(tid)`（读归档 JSON 重建内存记录、按 `started` 插回 `order`、重新 `_persist`）；`delete` / `purge_all` / `count_archived` / `_read_archived` 同时覆盖两个目录（手动归档的记录带 `archived=True`） |
| `web/tasks_api.py` | 新增 `POST /api/task/<tid>/archive`、`POST /api/task/<tid>/unarchive`（失败 400 + 中文原因） |
| （不变） | `_trim_memory()` 的 60/300 稳态回收仍保留：任务只增不减会吃内存，超过 300 条的记录落到磁盘、在任务中心按归档展示；正常使用量级不会触发 |

**前端**

- `app.js` 新增**全局运行条** `#vpRunBar`（右下角、AI 按钮正上方，`z-index: 88`
  低于 `ai-dock` 的 95）：`runBarMount/Render/Tick/Init`，5s 轮询
  `/api/tasks?logs=0`（后台标签页不轮询）；运行中/排队中全列 + 进度条 + 停止，
  已结束的按 `finished` 倒序最多 6 条，行内「📦 归档」；`gxApplyPage()` 里接线。
- `tasks.html`：终态任务卡加「📦 归档」，归档卡加「↩ 恢复」，小标题改为
  「（手动归档 / 重启前记录）」；`archiveTask()` / `restoreTask()` 成功后刷新
  两块列表并调 `window.runBarTick()`。
- `meta.html`：④ 运行任务卡片同样加「📦 归档」（`mtArchiveTask()`）。
- `app.css`：`.runbar` / `.rb-*` 样式（收起是胶囊，展开 380px，暗色主题兼容，
  窄屏收边）。
- `i18n.js`：`tk.archive*` / `tk.restore*` / `tk.archNote` / `rb.*` 中英两份。

### 验证

- `tests/_it_task_archive.py`：`archive` → 内存移除 + `_archive/<tid>.json`
  出现 + `list_archived()` 带 `archived=True` → 重复归档报「不存在」→
  `unarchive` 回到 active 且归档文件删除 → 运行中拒绝归档 → `delete`/`purge_all`
  两目录都清（只在进程内改 `DIRS['tasks']`，不碰真实 `run/tasks`）。
- `tests/_it_task_archive_api.py`：test_client 打 archive/unarchive
  两个接口（200/400 分支）、`/api/tasks?full=1` 的 active/archived 归属、
  `/tasks` `/meta` `/` `/pipeline` 渲染。
- `tests/_it_runbar_ui.py`（selenium + Edge headless，端口 8899）：真实浏览器里
  展开运行条 → 进度条宽度 > 0 → 外部任务（下载批次）不出现「归档」按钮 →
  点「📦 归档」并确认后行消失且 `/api/tasks?full=1` 的 archived 命中 → 任务中心
  历史归档点「↩ 恢复」回到 active → 再从列表卡点「📦 归档」回到 archived。
  截图写 `run/_it_runbar/`（`run/` 已被 .gitignore 忽略）。
- `tests/_audit_routes.py`（`VP_NO_RECOVER=1`）：264 条路由 0 个 500、0 未捕获异常。
- `tests/_route_inventory.py`：新增 2 条路由，已 `--save` 更新基线（261 条；含
  之前未提交的 `/api/fs/{roots,read,raw}`）。
- 渲染后抽内联 `<script>` 过 `node --check`：`/tasks`、`/meta` 全通过。
- AI 对话坞「换模块内容没了」实为坞默认收起：历史在 `localStorage`
  `vp_ai_msgs:<convId>`，但要 `AI.toggle(true)` → `ensureCtx` →
  `_restoreHistory` 才会渲染。已加 `AI_OPEN_KEY='vp_ai_open'`：`AI.toggle`
  记住开合，`boot()` 里按上次状态自动开坞（Edge headless 实测：坞开着 →
  `/meta` 切 `/tools` → 仍是开着且历史 2 条都在）。
- AI 对话坞「清空后切模块又冒出来」：`AI.clear()` 清的是本地消息，但换页后
  `AI.hydratePending()` 会按 `GET /api/ai/actions` 的 `recent` 名单把「最近已决、
  界面没渲染过」的执行单补成只读历史卡，SSE 也会按水位重放。已加
  `AI_CLEAR_KEY='vp_ai_clear_at'`：`AI.clear()` 记下清空时刻并把事件水位推到
  服务端当前最大 id（`AI._dropReplay`），`hydratePending` 只补 `ts > clearedAt`
  的执行单（pending 不受影响）。实测：清空后 page1 卡 0 张 → 切 `/tools` 坞自动
  开着、卡 0 张、消息 0 条、只剩问候语；stub 造 old1/new1 时清空后只剩 new1。
- 同一处还有个「一直出现」的坑，最后是**把「已决执行单自动补卡」整条去掉**解决：
  只比清空时刻的话，服务端 `recent` 里那几条老执行单会在每次开坞再补一遍；
  加过水位（`vp_ai_act_seen`）与 6 小时时效两道闸，但用户重启成 `app.py --web`
  后，仓库 `run/ai_audit` 里测试脚本造的 `reason=并发测试 / unit test` 记录仍是
  「6 小时内、没渲染过」，于是又凭空冒出「AI 执行单·格式转换 / 多步分析计划」。
  结论：这套 backfill 的收益（回头查上次批准跑没跑）远小于代价，`hydratePending`
  只保留 ①补 pending 按钮 ②状态对账；已决的单去任务中心（`/tasks`）或运行条看，
  那里有真任务 id、进度、产物。实测（仓库实例 8765，坞自动开）：首页/切页各
  0 张、清空后 0 张、消息 0 条。
- `GET /api/task/<tid>` 在内存里查不到时回落磁盘（`tm.archived_snapshot()`，
  读 `tasks/_archive/<tid>.json` 或重启前留下的 `tasks/<tid>.json`，返回带
  `archived: True` 的快照）：否则坞里挂着的任务卡、任务中心刚归档的卡一刷新
  只能 404，前端写成「任务不存在或已清理」，把「能恢复」说成「没了」。
  `tests/_it_task_archive_api.py`、`_audit_routes.py`（264 路由）复跑通过。
  这条是 Python 侧改动，**要重打包/重启才在 8765 上生效**（静态 JS 已就地同步）。

### 遗留

- 工具页「🕘 历史运行」盒仍按 `/api/tool/runs` 展示最近运行（那是运行历史，
  不是任务归档，本来就带手动 `rhArchive`/`rhClean`）；跨模块的可见性由运行条兜。
- `count_archived()` 语义改为与 `list_archived()` 同口径（只算不在内存的记录）。

---

## 阶段表治理 · 样品名归一化 · 清单回填（2026-09-11，承接上一节）

用户要求「扩大改动面，做好检查，好好修复」上一节勘察出的三处独立问题。
实际修的过程又牵出**四个同族 bug**——都是"改了一处、漏了另一处"。

### 1. ③c 共识阶段"半接入"（真 bug）

`consensus` 早已在 `STAGE_ORDER` / `STAGE_REGISTRY` / `STAGE_GROUPS` /
`DEFAULT_ANALYZE_STAGES` / `DEFAULT_STAGE_EST` 里，前端 `PIPE_FANOUT`
与 `STAGE_LABELS` 也早等着它——**唯独漏了 `PIPELINE_STAGES`**（以及配套的
`PIPELINE_STAGE_NAMES_EN` / `STAGE_SUMMARIES(_EN)` / `STAGE_OUTPUTS` /
`STAGE_VIEW` / `_MISSING_TOOL_MSG`）。后果：`pipeline_overview` 从不产出该卡、
`/api/samples` 的 `done/total` 漏算、`stages_done` 永不记录、跑完看不见结果。
磁盘上至今没有任何样品有 `03c_consensus/`，所以一直没暴露。

修法：补全上述 6 张表；`STAGE_VIEW` 指向 `coverage.tsv`（逐参考覆盖 + 是否
判定存在，最可读）；`_MISSING_TOOL_MSG` 加 minibwa 探测
（`consensus.consensus_engine()`），缺 minibwa 时显示 `unavailable` 并自动
排除出"依次运行剩余步骤"。
**刻意不给 consensus 加 skipped 规则**：`present` 为空是"真实阴性"结论，
不是"跑失败"，标成 skipped 会把它踢出 `stages_done`。

### 2. 前端仍用退役阶段键 `virus`（同族，两个真实故障）

② 病毒筛查（kraken2）2026-09-10 退役，阶段键由 `virus` 换成 `kvsuite`，
但 `app.js` 三处漏改：

| 位置 | 后果 |
|---|---|
| `renderPipe` 的 `known` 集合 + `byKey.virus` | ②b 卡片既不匹配显式分支、又被判为 extras → 渲染到 **⑩报告之后**，顺序错乱 |
| `STAGE_PARAMS.virus` 持有 `db_virus` 输入 | 没有任何阶段叫 `virus` → **病毒参考库目录输入框从不渲染**，`collectParams` 取到 null，用户无法在管道页指定病毒库 |

修法：`known` 用 `'kvsuite'`、改 `byKey.kvsuite`、`STAGE_PARAMS.db_virus` 挂到
`kvsuite`。浏览器测试断言顶层卡顺序为
`预处理 → kvsuite → assembly → report` 且 `kvsuite` 卡内有 `#db_virus`。

### 3. 样品名归一化：4 套实现收敛成 1 套 + 按磁盘真实目录名解析（真 bug）

历史上有 4 份各自为政的转换：

| 实现 | 规则 | 用途 |
|---|---|---|
| `pipeline._safe_sample_name` | 连续非法字符折叠成一个 `_`、去首尾 `._-`、截断 50 | 建目录 |
| `web/common._safe_sample` | 逐字符替换，不去首尾、不截断 | **读路径** |
| `logan_trace.safe_name` | 同 `web/common` | LOGAN 读路径 |
| `utils.sample_name_from_fastq` | 由文件名推导 | 生成候选名 |

前两者在中文/连续特殊字符/超长名下不一致：「样品A」建成目录 `A`，
读路径却去找 `__A`；带空格的手工目录、`_` 开头的目录同理读不到。
且规范化是**静默**的——前端 toast 显示用户输入的名字，磁盘上却是另一个。

修法：
- `utils.safe_sample_name()` = 唯一权威纯转换（**逐字节保持旧规则**，
  保证已有目录名不漂移）；`pipeline._safe_sample_name` 变成它的别名；
  `logan_trace.safe_name` 同样委托。
- 新增 `utils.resolve_sample_name(name, base=None)`：**以磁盘真实目录名为准**
  ——① 含分隔符/上跳的名字不原样接受 ② 原名命中 `os.listdir(base)` 就用原名
  ③ 否则退回规范名。读路径一律改用它，于是手工创建/历史遗留/中文/带空格的
  目录都能正常读写；第 ② 步的名字直接来自 listdir，天然不含分隔符，
  比字符替换更安全；越界仍由 `check_path(in_platform=True)` 兜底。
- 改动点：`web/common._safe_sample`（加 `base` 参数供 `results/_archive` 用）、
  `web/samples._sample_dir` / `api_queue_add` / `SampleQueue._run_entry` /
  `api_analyze`、`pipeline.run_analysis` / `run_report_only`、
  `web/tasks._sample_result_preview`、`web/refs._msa_group_dir`、
  `web/tool_jobs` 的 kvsuite 选样、`logan_trace` 的四处路径拼装与
  `meta.sample` 快照。
- **不保留中文**是刻意决定（`safe_sample_name` docstring 写明理由）：样品名
  要当目录名进 SPAdes/BLAST(LMDB)/minibwa 等不支持非 ASCII 路径的工具。
  代价是"用户填的名字 ≠ 实际目录名"，所以改为**显式告知**：
  `POST /api/pipeline/create` 回 `{sample, typed, note}`，撞名时报
  「样品名「样品A」会被规范化为「A」，而「A」已存在」；前端
  `createSample`/`batchCreate`/`scCreate`/`scBatch` 全部改用后端返回的
  **实际目录名**展示与入队（原先用用户输入的原文）。
- 刻意**不动** `gbdraw_plot` / `cds_export` / `gb_collection` / `phylo._safe_name`
  / `tool_results` 里同形的 `re.sub`：它们处理的是 FASTA 头 / contig ID /
  分类单元 / 基因名，不是样品目录，统一会改掉别人 glob 的文件名。

### 4. `backfill_manifest_from_products` 是死代码 → 接成显式 CLI（真 bug）

`project.json` 只由 `update_project_manifest` 在跑流程时写；老样品（或手工
拷进 `results/` 的目录）没有清单，项目名/输入/stages_done 全缺。反推函数早就
写好，grep 全仓**零调用点**，且注释里承诺的 `ensure_project_manifest()`
**根本不存在**（名字都是错的）。

修法：新增 `python main.py samples-backfill [--sample X] [--dry-run]`
→ `pipeline.backfill_all_manifests()`，只处理"清单缺失或来自回填"的样品，
已有真实清单的一律不动；`backfill_manifest_from_products` 加 `dry_run` 参数。
**刻意不在 `GET /api/samples` 里自动触发**：读接口不该写盘，且回填会用目录名
兜底出"项目名"，静默给所有老样品造出伪项目会污染项目筛选器。
边界如实写明：只填 `project` / `input` / `stages_done`，**不猜
`last_run` / `last_status`**（凭产物推断运行状态属臆测）。

### 5. 顺带清掉的测试残留（同族：假样品污染 UI）

`tests/_it_synteny.py` 把工作目录建在 `results/it_synteny_work`（不带 `_`
前缀），而 `/api/samples`、`/results`、项目筛选器、kvsuite 选样弹窗**只跳过
`_` 开头的目录** → 每次跑测试都在用户样品列表里多一个假样品
（本项目其它测试用的是 `_it_phylo` / `_smoke_phylo` 这种规范命名）。
改为 `_it_synteny_work` 并在结束时 `rmtree`；磁盘上遗留的那个（5 个合成
fixture、33KB、无任何产物）已删除。

### 6. `STAGE_WEIGHTS` 是没有读取点的死表（§4.4 更正）

它只有定义、没有任何调用（`_RunState.est` 走 `_est_stage_sec` →
`DEFAULT_STAGE_EST`），且只列了 14 项（漏 `subsample`），而
`docs/全链路复核报告_20260910_1545.md` §4.4 曾声称它"覆盖全部 15 阶段"。
已删除该表、把 `subsample` 补进 `DEFAULT_STAGE_EST`（原值靠 `('plain', 60)`
兜底，补上后取值不变），并更正该文档。

### 7. 新增的不变量守卫：`tests/_it_stage_tables.py`

阶段信息散落在后端 9 张表 + 前端 3 张表，靠人工对照必然漏。本测试用集合
相等/子集断言把这类漏项变成可执行检查：

- 后端 8 张表（含 `PIPELINE_STAGES`）与 `STAGE_ORDER` 双向相等；
  `STAGE_SUMMARIES(_EN)` 等价于 `STAGE_ORDER - {report}`（report 走特殊路径）
- `STAGE_GROUPS(_EN)` 覆盖且不重复、中英同构；`STAGE_VIEW` /
  `DEFAULT_ANALYZE_STAGES` / `STAGE_DEPS` 只引用已知阶段
- `PIPELINE_STAGES` 里 deps 必须先出现（拓扑顺序）
- 前端 `STAGE_LABELS` 必须覆盖全部阶段；`PIPE_FANOUT` 里的阶段都必须存在；
  `renderPipe` 的 `known` 集合必须覆盖全部阶段（否则有卡掉进 extras 乱序）
- **代码行里不得再出现 `virus` 阶段键 / `byKey.virus`**
  （注释与"数据库名 virus"不算）
- 实测 `pipeline_overview` 对含 `03c_consensus/` 的样品产出 done 卡
  （摘要 / 查看入口 / 产物齐全），且 `/api/samples` 的 `total` == 阶段数

### 8. 本轮新增/修改的测试

| 文件 | 覆盖 |
|---|---|
| `tests/_it_stage_tables.py` | 阶段表不变量 + ③c 可见性（新） |
| `tests/_it_sample_names.py` | 归一化规则/幂等/磁盘解析/防穿越 + 非规范命名端到端读写 + note 与撞名文案（新） |
| `tests/_it_manifest_backfill.py` | 回填 dry_run / 写盘 / 跳过已有清单 / `--sample` 过滤 / CLI smoke（新） |
| `tests/_it_global_ctx_ui.py` | 追加 [9] 管道卡顺序与 `#db_virus`、[10] 规范化提示（51 项） |
| `tests/_it_synteny.py` | 工作目录改 `_` 前缀 + 结束时清理 |
| `tests/_it_nav_responsive.py` | 顶部导航在 12 档宽度（1920→640）× 3 个页面下的自适应：横条不纵向溢出、无子元素跑到条外、页面无横向溢出、上下文/语言/📂 都可见、13 个一级链接都在、链接最多 2 行；宽屏必须回到单行 54px（新） |

回归结果：`_it_platform.py` PASSED、`_verify_fixes.py` 全通过、
`_verify_fixes_b.py` 通过、`_audit_routes.py` 193 条路由 0 个 500、
23 个页面全 200、5 个新增测试全通过、路由基线与 192 条一致（本轮未新增路由）。

### 遗留（本轮仍未动）

- 项目名依旧不可改（无 `POST /api/samples/<s>/project`）。
- `/api/tool/runs` 硬截断 30 条。
- 样品目录名改名后，目录内的**内嵌旧名副本**
  （`input.json.sample`、`project.json.project`、报告标题、
  `02b_kvsuite/plots/<样品>/`、`tool_runs/*/sample_sheet.tsv`、
  `logan/<job>/meta.json.sample`）不会跟着更新。
  `resolve_sample_name` 只让读路径能命中目录，消不掉这些陈旧副本。

### 补充：顶部导航宽度自适应（2026-09-11，用户截图报障）

用户反馈"页面上面的导航目录没有自动适应窗口"。根因与修法见坑记录 19。
实测结果（`tests/_it_nav_responsive.py`，真浏览器量 12 档宽度）：

| 窗口宽度 | 横条高度 | 排布 |
|---|---|---|
| ≥1900px | 54px | 品牌 + 13 链接 + 上下文簇，全在一行 |
| 1680–1180px | 80px | 链接一行，上下文簇（胶囊+EN+📂）整体换到第二行 |
| ≤1180px | 103–106px | 品牌独占一行，导航整行起第二行 |
| ≤720px | 139px | 链接 2 行 |

任何宽度下：横条不纵向溢出、无子元素跑到条外、页面无横向溢出、
上下文/语言/📂 入口都仍可见可点。

### 事故记录：本轮我自己造成的任务记录清空

跑 `tests/_audit_routes.py` 做回归时，它按设计对每条路由发了一次空 POST，
其中包含新加的 `POST /api/global/reset` → 200，
于是 `run/tasks/*.json` 里约 100 条历史任务记录被删除（详见坑记录 21）。
- 未受影响：`results/`（样品与产物）、`tool_runs/`、`run/logs/tasks/`
  下的 698 个任务日志文件、队列文件本身。
- 已受影响：任务中心的"历史归档"列表（记录索引），无法恢复。
- 已修复：接口加 `confirm` 必填令牌（空 body → 400）；巡检脚本加
  `DESTRUCTIVE_POST` 跳过表，并把快照比对补上"删除检测"。
  `tests/_it_global_reset.py` 新增「无 confirm → 400 且不清任何记录」断言。

## 一键分析（全流程）卡补齐输入 / 参数 / 输出（2026-09-11，用户截图报障）

用户截图报障：「一键分析（全流程）页面没有输入，也没有输出，也没有参数，啥都没」。

### 根因（三条叠加，缺一都不会是"整页空白"的观感）

1. **没有输入、没有参数**：`t-kvchain` 卡只有「⚡ 一键运行 / 📂 最近汇总」两个按钮。
   `runKvchain()` 读的是 **t-kvsuite 卡的 `kv_*` 字段**（`kv_samples` / `kv_ref` /
   `kv_min_cov` …）——而那张卡属于同一模块组的**另一个二级模块**，`showModule()`
   一次只显示一个卡片，所以站在本页既看不到那些字段、也无从修改；表单值全靠
   另一张卡里"上次填过什么"，本页表现为"没有任何参数"。
2. **没有输出**：本卡没有 `.toolrun` 容器（`#toolrun-kvchain`）。平台的口径是
   "运行日志/结果/产物平铺在工具卡内"（app.js `injectStageLogs` + `toolRunHtml`），
   而 `TOOL_LABELS` 里**从未登记 kvsuite / kvchain（virchain 同样漏了）**，
   于是即便有容器也匹配不到任务名，日志只会进全局任务抽屉，卡片输出区恒空。
3. **没有结果**：跑完只有 `#kvChainResult` 里的"流程各段"表 + 汇总目录链接
   （且 `_chain.json` 里 `steps[0].summary` 只有 stage/engine/n_confirmed 等标量），
   看不到任何鉴定/过滤/共识/变异表与图——相比另一张卡"什么都没有"。

### 修法

- **本卡自带一整套表单**（`kvc_*` 命名，17 个 input + 2 个 select）：样品（📋 多选，
  `kvPickSamples(targetId)` 支持指定回填框）、参考库、参考注释 TSV、比对引擎、
  鉴定库、覆盖率/深度/reads/泊松四阈值、变异 QUAL/AF/氨基酸标签、NCBI 邮箱、
  线程数、扩展变异 / SNPGenie / 不下载注释 / 画全部变异开关。
  默认值与 t-kvsuite 卡一致；`runKvchain()` 只读本卡 `kvc_*`。
- **参数互取**：`kvchainPullKvsuite()`（按钮「⤓ 取该卡参数」）按
  `KVCHAIN_FIELD_MAP` 把 t-kvsuite 卡当前表单值整份搬过来。两套表单不做静默同步，
  避免"改了这边没改那边"的隐性漂移；固定跑全五段（`stage=all`）由后端
  `_tool_job_kvchain` 保证，前端以文案明示，不设"跑哪些段"下拉。
- **输出区**：新增 `<div class="tool-out">` + `#toolrun-kvchain`；app.js
  `TOOL_LABELS` 补 `kvsuite` / `kvchain` / `virchain` 三个键，
  任务名（工具·已知病毒识别与定量 / 工具·病毒定量与共识·一键）即可匹配到卡内输出区，
  实时日志与产物下载就地可见（此前 virchain 卡同样是空的）。
- **内联结果**：`renderKvsuite` / `renderKvVariantPlots` / `renderKvEvo` 增加
  `ids` 参数（默认 `KV_IDS` 指向 t-kvsuite 卡的 `kv*` 容器）。一键流程跑完或点
  「📂 最近汇总」时，`kvchainLoadInline()` 从 `_chain.json` 的
  `steps[0].run`（= kvsuite 子运行）拉 `/api/tool/kvsuite_result`，
  用 `KVC_IDS` 渲染进本卡的 `kvc*` 容器：计量徽标 + 鉴定表 + 过滤表 + 共识 QC +
  变异注释 + 变异可视化 + 演化分析 + 深度图，并给「📊 在已知病毒识别与定量卡中打开」
  （`kvchainOpenKvsuite()` 走左侧模块条点击 → `loadKvsuiteResult(run)`）。
- `loadKvIndexOptions()` 同时填充两个鉴定库下拉（`kv_index_dir` / `kvc_index_dir`）。
- 示例体系：`examples.js` 的 `fillKvsuiteExample()` 同时填 `kv_samples` 与
  `kvc_samples`，本卡点「✨ 示例」不再毫无反应。
- i18n：新增/改写 `tk.kvChainHint`、`tk.kvChainStages`、`tk.kvChainPull(_Title)`、
  `tk.kvChainRunHint`、`tk.kvChainStepsSec`、`tk.kvChainSubRunLbl`、
  `tk.kvChainOpenKv`、`tk.kvChainNoSub`（zh/en 同步）。

### 回归

- `tests/_it_platform.py` 新增 **3c-2** 段：断言本卡含样品/参考/注释/鉴定库/引擎/
  阈值/开关/线程数 9 类输入、`#toolrun-kvchain` 输出区、两张结果表容器、
  运行与参数互取按钮；断言 `runKvchain` 只读 `kvc_*`（不再出现 `val('kv_samples')`）、
  内联走 `KVC_IDS`、`TOOL_LABELS` 登记 kvchain/kvsuite。
- `tests/_check_kvchain_ui.py`（真浏览器，自起临时实例，已挂进 `_run_all.py`，新）：
  本卡可见输入 ≥15 项、14 个容器齐全、「✨ 示例」落到 `#kvc_samples`、
  「⤓ 取该卡参数」搬运值/开关、未选样品点运行有提示；并用 `page.route` 桩住
  `chain_result` / `kvsuite_result`，验证「汇总清单 → 子运行 → 内联 kvc* 表格」
  这条链路渲染正确且**不污染 t-kvsuite 卡容器**，全程无 pageerror。
  （也可 `--port 8765` 直接对已在运行的平台跑。）
- `tests/_audit_contract.py`：i18n zh/en 1718 键对齐、使用键全部有定义，无硬性差异。
- 渲染回归：`/tools` 各组页面内联 JS 经 `node --check` 全部通过；
  `app.js` / `examples.js` / `i18n.js` 同步通过。
- 注：本机 `run/results/` 无样品（用户已归档），`_it_platform.py` 的
  "样品列表 > 0" 断言属既有数据依赖项，非本轮引入；补一个临时样品目录后
  全量 PASSED。

## 裁剪版系统地理移植 + 两个"永远报绿"的巡检陷阱（2026-09-16）

完整清单/证据见 `docs/主平台同步_裁剪移植_20260916.md`；这里只记**踩坑**。

### 1. 有组无入口 = 卡片到不了（组落地路由的隐含前提）

09-15 拆分把 `compare` / `phylodyn` 从 `NAV_GROUPS` 摘掉后，`#t-rdp` / `#t-rtt` /
`#t-phylogeo` 三张卡变成"路由在、卡片在、界面上到不了"：tools 页的
`currentGroupFromURL()` → `showLanding/showModule` 要求组在 `NAV_GROUPS` 里，
否则 `?g=phylodyn` 掉回 sample 组、`#t-phylogeo` 深链落不到卡片。
**只补组还不够** —— `_nav.html` 的一级导航项是硬编码列表，不补就是"只能手敲 URL"。

### 2. `tests/_check_pages_console.py` 的判据曾经恒真（巡检静默全绿）

两个缺陷叠加，缺一都不会"全绿"：

1. 单页模式末尾写的是 `main()` 而不是 `sys.exit(main())` → 子进程**永远 exit 0**，
   而父进程只看 `returncode` ⇒ 页面坏成什么样都打 ✔。
2. 父进程 `subprocess.run(..., text=True)` 按本地代码页（GBK）解码子进程 stdout，
   而平台自己的模块会把**子进程** stdout 重配成 UTF-8（`/settings` 加载全量数据时
   打印的自检报告就是 UTF-8）→ 读线程抛 `UnicodeDecodeError`、stdout 变空 ⇒
   连"结论行"都读不到，而缺陷 1 又把空输出当成功。

现在：`sys.exit(main())` + `encoding='utf-8', errors='replace'` + **以子进程自报的
末行结论（`OK ` / `BAD `）为主判据**、`returncode` 兜底，两者不满足一律 ✘。
**负对照**（把子进程结论强改 `BAD` 的临时副本）：修前"全部 ✔"，修后 `rc=1` 且逐页 ✘。

### 3. 脚本化整文件写入会静默改换行符（CRLF → LF）

用 Python 脚本做批量文本替换时，`io.open(..., newline='')` 读进来是 `\n`，
写回就把 CRLF 文件整篇变成 LF ⇒ `git diff` 出现上千行噪声（正是 `.gitattributes`
里 `* -text` 那段注释警告的事）。**对策**：改完用字节比对核一遍 CRLF/LF 计数，
必要时 `.replace(b'\n', b'\r\n')` 还原。本次涉及 `i18n.js` / `tools.html` / `phylogeo.py`。


## 2026-09-17 dsRNA 设计全链路（工具箱 t-dsrna）改造

原 `_tool_job_dsrna` 只是 dsRNAmax_det 裸调（产出 result.csv），本次升级为
完整链路，引擎侧三个新模块（均为函数内惰性 import，**打包 spec 的
hiddenimports 必须登记**，已加）：

| 模块 | 职责 |
|---|---|
| `dsrna_pipeline.py` | 编排：exe 探测/内存守卫/maximin 选窗/面板扫描/引物/成品 QC |
| `dsrna_scoring.py` | dsRIP 效价 + 安全合成评分（**平台唯一口径**，合并 C-host 管线的 dsrip_scoring/safety_scoring 两份复制实现） |
| `dsrna_offtarget.py` | uint64 排序数组 21-mer 脱靶扫描器（双链入索引、0→1→2mm 分层、磁盘缓存） |

数据资产（发行包需带）：
- `databases/dsrna/lookup.db` + `siRNA_parameters.txt`（dsRIP 权重/热力学表）
- `databases/dsrna/lethal_lists/*.tsv`（5 物种致死基因，按**裸种名**匹配，
  面板文件名里的 `_refseq_rna` 后缀会被剥掉再匹配）
- `databases/dsrna/panel/*.fna`（RefSeq mRNA 脱靶面板，8 物种约 750MB，
  首扫建索引并缓存到 `panel/_cache/`，之后秒级；**冻结分发可不带**，
  空面板时工具自动降级为纯效价选窗并在日志说明）

链路设计要点（背景：2026-09 对 C-host dsRNAmax 管线的审计）：
1. **打分对象=交付分子**：引物限定在效价×安全最优窗内设计（scope=
   best_window），引物定稿后对真实扩增子重跑效价 + 面板脱靶复扫（QC）。
   旧控制台/管线在 300nt 臂上打分、却交付 Primer3 随手挑的 215bp 产物，
   两者的脱靶面完全不同。
2. **median 遮蔽离群株**：多候选按（最差目标，中位，总和）maximin 重排；
   命中 0 的"盲目标"与 <25% 覆盖的目标显式告警（单臂只能覆盖共享 21-mer
   的同源目标，异源目标须各自出臂做鸡尾酒）。
3. **绝对红线**：致死基因 0 错配命中一票否决（`red_lines`），不随臂内
   相对归一化被稀释；安全分仍是 dsRIP 口径的臂内归一（最安全=100）。
4. exe 兼容四件套：`-otKmerLen` 恒显式传（kmerLen<21 不传直接 fatal）；
   `-seed` 经 `probe_exe()` 探测后才传（stock exe 会 exit 2）；`-csv` 写
   失败时 exe 退出码仍为 0 且不再输出臂序列，靠解析失败兜底；内存守卫按
   **160B × 去重 k-mer × iterations** 估算（iterations=100 实测 13-16KB/
   kmer 反推），超预算抛 `MemoryBudgetExceeded` 而不是等 OOM。iterations
   默认从 1000 回落到 100——每个迭代完整拷贝一份 k-mer 表且全部并发存活，
   1000 次 ≈ 10 倍内存（18 条高分化 15kb 靶标 ≈ 40GB）。
5. 同 seed 两次运行臂逐字节一致（det 构建，tag `v1.1.15-det.1`，源码在
   C-host dsRNAmax 仓库 commit 1e0c19e）。

测试：`python tests/_it_dsrna.py`（端到端/确定性/内存守卫/与 C-host 管线
v4 口径对拍 w933=7..296）；致死红线阳性用例见该次改造的会话记录
（Apis NM_001160052.1 → 280/280 lethal mm0 → red_line）。

已知边界：
- 植物特有生物学未建模：DCL2 22nt / RDR6 transitivity 会次级放大脱靶，
  21-mer 模型在植物里偏乐观（报告未声明，属已知科学局限）。
- `offtarget_lethal.tsv` 只含致死命中行（全量命中行体积大，扫描器返回值
  里 `sirna_tiers` 已含逐 siRNA 分层，需要时可再导出）。

### dsRNA 测试夹具：论文已发表序列（tests/fixtures/dsrna/）

`_it_dsrna.py` 第 5–7 节用 dsRNAmax 原论文（Fletcher et al. 2025, NAR Genom
Bioinform, lqaf064）补充材料做真实数据夹具：

- `tef17_tef21_arms.fa` = Suppl. Table 1 的两条已发表臂（300nt，有三种
  基础生测数据背书），经 PyMuPDF 从 PDF 文本层抽取，非手工抄写；
- `ce_ef1a_alnpartners.fa` = Suppl. Figure 3 的 C. elegans EF1a 比对段
  （图无文本层，来自图像 OCR）。**保真锚点是同源度**：TEF-17 前 168nt
  对 Ce-EF1a 实测 85.12%（图注 85.1%，几乎精确），TEF-21 实测 79.00%
  （图注 79.3%，差 1 个碱基，容差内）。

⚠ 论文内部口径差异（勿当 bug 修）：Figure 1/4 截图里的运行产物与
Table 1 最终发表序列**不是同一分子**——共享保守前缀，但 GC 不同
（图内 45.9%/45.7% vs Table 1 的 45.00%/42.67%）。测试断言只用
Table 1 长度 + Figure 3 同源度，不用图注 GC。

语义断言：发表设计 = 与 C. elegans 无连续 ≥17nt 精确匹配 → 我们的
扫描器在 21-mer 口径下 mm0 必为 0（TEF-17/21 实测均 0，mm1 9/18）。
第 7 节把 TEF-17 喂回全链路：v1.1.15 的 constructLen==目标长 边界
（off-by-one 修复的标志性场景）出臂为目标精确子串，引物/扩增子 QC 全通过。

#### 2026-09-17 代码审查修复（第二轮回归）

审查发现并修复 4 个 P1（隔离株覆盖日志 TypeError——`isolate_coverage` 返回
结构改为 `{overall, per_isolate}` 后 `min(m.values())` 会在 float/dict 间比较；
空面板选择语义——`panel=''` 此前被归一化成 None 而误扫全部 750MB 面板，
现三态化：不给=全部 / ''或'none'=跳过 / 列表=所选；JS `+val('ds_mm')||1`
把"0 错配"吞成 1；result.csv 固定拷 0 号候选的 CSV 而非 maximin 中选者——
候选现在携带 `orig` 原始编号）+ 2 个口径问题（报告里面板 bp/n_records 恒 0
——indexes 未透传；lethal_rows 的 sirna_idx 0 基与 TSV 契约不一致，改 1 基）
+ 卫生项（删 `extract_amplicon` 死代码与 `assign_safety_scores` 未用形参；
`build_lethal_index` 进程内缓存——同任务臂扫描+扩增子复扫免整文件重读；
`scan_arms` 支持 cancel 逐 siRNA 中断；revcomp 文档注明仅 DNA 字母表）。
`tests/_it_dsrna.py` 增至 9 节：新增 isolates 分支端到端（含 5% 突变衍生株
的逃逸信号断言）、`panel=''` 跳过、result.csv 中选臂校验、max_mm=0 分层
（11 跨突变窗 None + 869 精确窗 0）。

## 2026-09-17 病毒浏览器「输入包」导出：两个按钮、一份信封

需求（用户）：「停止，还是本地部署吧，修改 explore 导出 fasta 和 VirPhyKit 需要的
元数据信息文件，并且进行预过滤」→ 随后追问「我们之前的脚本呢，导出我们之前流程
支持的格式，是不是更好些」→ 定案**拆两个按钮**、**不信 GenBank 标记、按长度判完整**。

### 唯一的真冲突是 date 列编码

信封本来就已经一样了 —— 我们自己的 BEAST 交接包（进化平台
`beast_handoff.py:471`）写的表头也是 `name,date,location`，FASTA 头也与 `name`
列逐字相等。冲突只在 `date` 这一列的**写法**，而且**单向不可兼容**：

| `date` 写法 | VirPhyKit/TreeTime | 自家 `virome_phylo_pipeline` |
|---|---|---|
| `2018-06-29` | ✔ 2018.49178 | ✔ 2018.49444 |
| `2018.49589` | ✔ 2018.49589 | ✘ **2018.95145，偏 0.46 年且不报错** |

原因：`utils/decimal_year.py` 的 `to_decimal_year(strict=True)` 只认
`YYYY[-MM[-DD]]`，把小数年当成「年=2018.58 + 6 月 15 日」再折一次；越界年份还会
直接 raise，让服务器整个 BEAST 作业中止。TreeTime 的 `parse_dates()` 两种都收。
→ `YYYY-MM-DD` 是唯一两边都读对的写法，但 VirPhyKit 的 Example 用的是小数年
（`2007.569473`），所以**各出一个按钮，不混用**：

| 按钮 | 路由 | date 列 | 产物名 |
|---|---|---|---|
| 导出 VirPhyKit 输入包 | `POST /api/explorer/export/virphykit` | 小数年 `2007.56947` | `virphykit_*.zip` |
| 导出管线输入包 | `POST /api/explorer/export/pipeline` | `YYYY[-MM[-DD]]` | `pipeline_*.zip` |

两包**除 date 列外逐字相同**（name/location/FASTA 全等），巡检里有断言锁这条，
因为它是「两个按钮」这个设计的全部前提。

### 完整性：不信 `Nuc_Completeness` 标记，按长度判

标记 `complete` 直接放行（这信号只在「说完整」时可靠）；标记非 `complete` 时按长度判：
落在同物种 `complete` 记录长度**中位数**的 `[0.90, 1.20]` 倍内就留。
该物种一条 `complete` 都没有时（全库 6,508 个物种里有 1,985 个），退化为
**它自己最长序列**的长度。

数据依据：PVY 标 `complete` 的 685 条长度 9376–9800（很紧），而标 `partial`
的最长到 9762 —— 只认标记会白丢 269 条本该留的全长序列。全库同口径 66,552 →
81,137（+14,585）。跑在真实筛选上：PVY 588 → **805 条**（靠长度救回 217 条）。
档位取的是本平台既有判据：下界 0.90 = `align_qc.py:38` 的 `DEFAULT_MIN_LENGTH`，
上界 1.20 = `contig_annot.py:245` 的「近完整」。
**不能用属平均做基准**：`databases/genus_lens.tsv` 的 Potyvirus 平均只有 5292 bp
（真值 ~9700，被片段拉低），拿它判只命中 2 条。

### 顺手修掉一个互通缺陷：`metadata.csv` 里不能有引号

TreeTime 读元数据走 `treetime/utils.py:251` 的 `parse_dates()`，对非 `.tsv` 用
`sep=r'\s*,\s*'` + `engine='python'`，**不按引号包裹切分**。所以 `"Anshun, Guizhou"`
这种字段会被从逗号劈开 —— PVY 那个包里 43/588 条 `location` 就这样被截断（还带个
孤立引号），截断后的区域在 Mapping.txt 里永远匹配不上 → 全被标成 Unknown。
现在所有会破坏 CSV 的字符（逗号/引号/制表/换行）一律压成空格（`csv_safe`），
写出来**一个引号都没有**，TreeTime 的正则、pandas 默认、R `read.csv`、
最朴素的 `split(',')` 读出来完全一致。巡检里既有断言也有相应的灵敏度负控。

### 契约来源（外部目录，只按方法对齐，不复用代码）

VirPhyKit 是 GPL v3，本平台**只做方法对齐、不引入其代码**。三个约束从源码读出：
`Treetime/function_treetime.py:131-134` 读 `name/date/location` 并在 `:141-148`
**硬性中止**于「叶缺日期/缺地点」；`Treedater/function_treedater.py:25` 用 R
`read.csv`；`Subsample/fuction_subsample.py:37` 的
`re.search(r"(?:^|>|_)([A-Za-z0-9]+)(?:_|$)")` 从头里取**第一个** alnum token。
所以序列名口径定为 `{区域}_{登录号}_{小数年}`、区域去掉全部非 ASCII 字母数字字符。

### 巡检与验证

新增 `tests/_check_virphykit_export.py`（8 节，已登记进 `_run_all`）：小数年解析
（8 正 8 负）、区域/name token、长度判据（中位数 vs 均值 + 闭区间边界 + 基准缺失负控）、
预过滤夹具（含「标记 partial 但长度到位必须留下」的正控与 6 条负控）、
两种日期口径对拍真下家、契约灵敏度自检、两路由端到端真库、可选 GeoSubsampler 实跑。

**负控（证明会红）**：A 把 `statistics.median` 改成 `mean` → 1 项失败；
B 让 `_metadata_text` 忽略 `date_mode` → 4 项失败、退出码 1。改回后 md5 逐字节复原。
`tests/_check_explorer.py` 重跑全绿（证明 `filter_records` 抽取与路由重构没扰动
原有两个导出与 5000 行表格口径）。浏览器端真点击两个按钮，提示条数字与
`Content-Disposition` 文件名均正确（4,806 → 3,867；PVY 805），
再把两个 zip 从**运行中的实例**上取回来复检：契约 0 问题、两包除 date 列外全等。

### 环境提醒（本次踩到）

- `dev_tools/run_sanitized.py` **只存在于进化平台**（`D:\桌面\植物病毒进化分析平台`），
  所以 8900 是**进化平台**的实例；主平台在 **8765**（`python -u app.py --web`）。
  改主平台代码后要重启 8765 才生效（Flask 无 reload），别对着 8900 测主平台。
- 主平台的 `/api/explorer/query` 响应里 `rows_total` 是**全库行数**（`len(df)`），
  不是筛选后的条数 —— 别拿它当筛选结果计数（筛选后的量看 `stats.total` / `table`）。
- 前端 `filters()` 会把上一次的 `state.table` 一并回传，但服务端按筛选条件重跑，
  不吃这张表（导出尤其如此，见 `explorer.py` 模块头「已知差异 1」）。

## 2026-09-17 更正：比较基因组分析撤错了，已复原（导航层）

**谁改的**：2026-09-15 的「三块业务迁出」拆分（本次会话之外的操作，见
`docs/平台拆分记录_20260915.md`）。**为什么错**：拆分把「比较基因组分析」与
「进化动力学分析」当成同一件事捆着撤出导航，但两者是不同的业务对象 ——
比较基因组 = **不同病毒之间**的比较（跨科/属取参考序列 → MAFFT 比对 → 建树 / SDT
同一性）；进化动力学 = **同一个基因 / 序列集内部**的时间与地理信号
（重组 / 根到尾回归 / 系统地理）。09-16 恢复 `phylodyn` 时又用「覆盖」而不是
「并列」，把 `compare` 的一级导航位吞了，于是它落到「**有组、有卡、有路由，
但界面上到不了**」的状态。

**复原改动（三处，仍仅导航层）**：

| 文件 | 改动 |
|---|---|
| `Virus_Platform_Core/web/pages.py` | `NAV_GROUPS` 在 `annotate` 与 `phylodyn` **之间**插回 `compare` 组（5 张卡：`t-seqprep` / `/cds-export` / `t-align` / `t-treebuild` / `t-sdt`）；`_PATH_TO_GROUP` 恢复 `'/cds-export': 'compare'` |
| `webapp/templates/_nav.html` | `_nav_items` 在 `annotate` 之后、`phylodyn` 之前补回 `('/tools?g=compare', 'nav.g.compare', '比较基因组分析', 'compare')` |
| `webapp/templates/home.html` | 模块卡区补回「🧬 比较基因组分析」 |

`i18n.js` 的 `nav.g.compare` / `hm.entry.compare.d`（中英）**一直在**，无需补。
**未动 `phylodyn`** —— 09-16 的裁剪移植保持原样，两组现在并列。
`/explorer` **未恢复**进导航：病毒浏览器确实迁走了，与本次更正无关。

**关键澄清（推翻 09-16 文档里的一条说法）**：compare 的**后端在本站一直都好**，
从未迁走。`TOOL_REGISTRY`（`web/tools_api.py`）里的 `align` / `quicktree` / `sdt` /
`identity` / `structcmp` 与 `Virus_Platform_Core/{align_qc,sdt_exact,gb_collection,
cds_export,msa_view,phylo}.py` 都在本站；`tools.html` 里 4 张卡的 `section.card[id]`
（`:1322` / `:1430` / `:1546` / `:1649`）也都在。09-16 那条「比较基因组仍只在
进化平台」对本站不准确 —— 拆掉的只有导航入口。

**验证（全绿）**：
- 真浏览器点击链路：首页模块卡 → 一级导航（该项高亮）→ `/tools?g=compare` 组树 →
  4 个 hash 条目逐一点击，每次**只**该卡可见（`getComputedStyle(s).display`），
  `/cds-export` 可达；控制台仅 1 条 ArchaeopteryxJS 常规 log，无 pageerror。
- `tests/_it_compare.py`（SDT 精确矩阵 / NT+AA 同一性 / NJ+FastTree / GenBank 集合
  离线全流程）、`tests/_it_platform.py`（含「`NAV_GROUPS` 每个 id 必须是 `main > *`
  直接子元素」的结构断言 `:246`）、`tests/_it_align.py`、`tests/_it_msa.py`、
  `tests/_check_pages_console.py`（22 页）—— 全部通过。

**已定位但未修（与本次改动无关）**：`tests/_check_restore.py` 步 [3]「通用卡恢复」
会红 —— 它等 `#toolrun-contigs` 出现「上次运行」，而 `/api/tool/runs` 是
**按 mtime 倒序只取前 30 条**（`tools_api.py:190` 的 `names[:30]`）；最新一条
`contigs_*` 运行在 222 个运行目录里排第 **168** 位（2026-09-12），永远进不了这个窗口。
它是独立诊断脚本、**未登记进 `_run_all.py`**，不是门禁；修它属于改测试语义，
本次未动（步 [1][2] 走的正是 compare 组，均通过）。

**顺带发现并已定案（2026-09-17）**：主平台的 `/explorer` 也是「有页面、导航里没有」，
但**与 compare 性质不同** —— 它是**活的 7 页签浏览器**（trends / mutation / table /
primers / host / vector / profile），且这个「服务器版 7 页签」是**已提交**的工作
（`6c59773` 引入、`ef07b55` 修跨站深链 + 真点击验证）。用户明确决定**暂不恢复**
该入口、保持现状（只作深链访问）。所以：**compare 要进导航，explorer 不进** ——
别看形态一样就一起改。

## 2026-09-17 进化树 + 基因组叠加上线：补丁早就在，缺的是「接线」

**用户的观察是对的**：进化树查看器里确实做过「叶标签之外的蛋白结构与相似性连线」，
但 ③ 树查看的示例里没有。定位结论：能力在、**数据链路没有**。

**能力在哪**：`webapp/static/vendor/archaeopteryx/archaeopteryx.js` 里的自研补丁
「Comparative Synteny & Homology as tree overlays」（`~:5507` 起）—— 只画在矩形布局
（rectangular），三层叠加：属色带（`g.aptx-genus-bands`）、每叶基因轨道
（`g.aptx-genome`，叶标签右侧的箭头 gene box + 底部 `Genome Coordinates (bp)` 标尺）、
同源连线（`g.aptx-genome-links` 的半透明 ribbon，按蛋白同一性走 5 档色阶
30/55/75/90/100，右上角 `Protein Identity (%)` 色标）。数据入口是
`launchArchaeopteryx(box, name, nwk, {genomeOverlay: {...}})` 或句柄 `setGenomeOverlay()`；
面板里自动多出 `Genome Tracks` / `Genus Bands` / `Homology Links` / `Identity %` 开关。

**为什么示例没有**：`genomeOverlay` 在库和 `demo-synteny.html` **之外零引用** —— 主平台、
进化平台、`dist/` 全都没有生产方，两个渲染入口
（`webapp/static/app-compare.js: renderTreeTo`、`webapp/templates/results.html: treeRender`）
都只传 `{layout:'rectangular'}`。没有数据，补丁就永远不画。

**接线（本轮）**：

| 层 | 改动 |
|---|---|
| 数据约定 | `Virus_Platform_Core/web/common.py` 新增 `tree_overlay_for(nwk_path)`：叠加 JSON 放在**与树同目录同名**的位置（`tree.nwk` → `tree.overlay.json`）。缺失 / 损坏 / 非 dict / 空 tracks / 超 16MB **一律返回 None**，树照常渲染（叠加是可选产物，不能把树查看卡变成报错） |
| 后端 | 三个树入口都带上 `overlay` 字段：`web/tool_results.py` 的 `/api/tree/file`、`/api/tool/quicktree_data`（取运行目录里实际选中的那棵树）；`web/refs.py` 的 `/api/tree/data` |
| 前端 | `renderTreeTo(box, newick, opts)` 透传 `opts.genomeOverlay`；`treeToolLoadFile` / `treeToolView` / `quickTreeLoad` 与 `results.html treeLoad/treeRender` 都把 `d.overlay` 带上；`tvMeta` / `treeMeta` 多一行「基因组叠加：N 条轨道 / M 个基因 / K 条同源连线（按蛋白同一性着色）（仅矩形布局绘制）」 |
| 示例 | `tests/make_example_overlay.py` 产出 `examples/example_synteny_tree.nwk` + `examples/example_synteny_tree.overlay.json`（3 轨道 × 6 CDS，18 条蛋白同一性连线 68.6%~93.2%，NJ 树走平台自己的 `phylo._nj_distance_matrix` + `_neighbor_joining`）；③ 树查看新增按钮「✨ 示例·基因组叠加」（`data-i18n-title="tk.fillExampleOverlayTitle"`，两个按钮的 onclick 改成 `fillExample(...).then(treeToolLoadFile)`，否则示例根外置时相对路径会落空） |

**巡检**：`tests/_check_tree_overlay.py`（已登记 `_run_all.py`）—— 后端契约 +
**五类坏 overlay 的降级负控**（无同名 JSON / JSON 损坏 / JSON 是数组 / 空 tracks /
17MB 超限：都必须 overlay=null 且树仍 200）+ 真浏览器点示例数 SVG
（属色带 3 / 基因框 18 / 同源连线 18 / 标尺 1 / 色标 1，注意宽度 ≥7px 的 gene 画成
箭头 `polygon`、窄的才退化成 `rect`）+ **换纯树示例后四类元素必须清零**的负控 +
i18n 键 + 无 pageerror。已按纪律做**断电负控**：临时让 `tree_overlay_for` 直接
`return None` → 巡检 10 项变红，恢复后全绿。

**补丁留档（2026-09-17 已做）**：`webapp/static/vendor/` 在 `.gitignore:39` —— 这个补丁
**从未入库**，只存在于本机与该机 `dist/` 副本，而丢了之后平台只会「静默不画叠加」
（没有数据不算错，所以不会报错）。排查确认**本机已无上游原件**（源码、`dist/.../_internal/`、
进化平台三份 `archaeopteryx.js` md5 全同 `18e59d72…`），做不出可信 diff，
故按用户选择留**字节级副本**：`docs/vendor_patches/archaeopteryx/{archaeopteryx.js,
demo-synteny.html}` + `README.md`（md5、上游版本与 LGPL-3.0 说明、补丁三层与代码锚点、
恢复步骤与冒烟方法）。**`.gitignore` 未改** —— vendor 整体仍不纳管，只有这两个文件的
副本入库。

**本次不做（用户已定案）**：① `dev_tools/package.py` **暂不重打包** `dist/` —— 源码模式
8765 已带接线与新示例，打包版留待改动攒批再打；② 进化平台 8900 **暂不同步**这套接线
（那边有同一份补丁与 `demo-synteny.html`，接线时逐文件留 `.bak`，因为它无 git）。

**附带发现（2026-09-17 已修，见下一节）**：`examples/example_synteny_{A,B,C}.gb` 的 CDS 曾是
840bp @ 500bp 间隔（互相重叠），且第 6 个 CDS 结束在 3490 > `LOCUS` 声明的 3300。
生成器因此把标尺取 `max(len(seq), max(end))`，避免最右一个基因框被裁掉。

## 2026-09-17 「全部修复」：夹具自洽性 / 行尾 / 残留进程 / 一个被砸坏的表单标签

用户对上一轮报告的三个知情项 + 排查中新发现的缺陷做了统一修复。

**① 示例夹具 `example_synteny_{A,B,C}.gb` 改自洽（真实缺陷）**

| 缺陷 | 修法 |
|---|---|
| 6 个 CDS 按 500bp 间距排但每个 840bp → 相邻重叠 340bp（40%） | `tests/make_examples.py:make_synteny_trio()` 改为 840bp 编码 + 60bp 间隔、首尾各留 150bp |
| 第 6 个 CDS 收在 **3490** 而 `LOCUS`/序列只有 **3300bp** → 越界，按坐标切片被静默截断 | 基因组长度显式推导：`2*150 + 6*840 + 5*60 = ` **5640bp**（< 6.8kb 仍贴近 Potexvirus 口径） |
| 生成器用 `max(len(seq), max(end))` 把越界**兜住**了（缺陷被掩盖，没人再看得见） | `tests/make_example_overlay.py` 改为**断言**：CDS 越界直接报错并点名文件/基因 |
| 巡检不检查这条不变量 | `tests/_check_tree_overlay.py` 新增两条：每个 CDS 落在轨道长度内、同轨 CDS 互不重叠 |

重生成链（幂等）：`tests/make_examples.py:make_synteny_trio` → `tests/make_example_synteny_gb.py`
（ORIGIN 写真序列，5640bp / GC≈58% / EXAMPLE_SET 集合重建）→ `tests/make_example_overlay.py`
（树 + `overlay.json`）。**蛋白层面完全不变**：3 轨道 × 6 CDS、18 条连线 68.6%~93.2%（中位 75.0%）、
树拓扑 `(C,(A,B))` 照旧 —— 变的是坐标、轨道长度与（DNA 派生的）枝长。
`examples/results/synteny/summary.json` 里的 `length` 同步为 5640，并把指向已删工作目录
（`databases/examples/_work/synteny`）的绝对路径改为相对路径、补 `note` 说明该快照由已摘除的
LoVis4U 口径引擎产出（故不可原样重跑）；`ident` 数值来自蛋白、未受影响，保持原样。

**② 行尾归一化（9 个文件，只动行终止符）**
工作区里 9 个改动文件的行尾与 `HEAD` 约定不一致，`git diff` 被整文件噪音淹没（`app.js`
4058 行、`tools.html` 4489 行）。按各自 `HEAD` 约定重写：`app.js`/`assembly.py`/`db_migrate.py`/
`kv_engines.py`/`kv_stage.py`/`io_api.py` → LF；`kv_config.yaml`/`pages.py`/`tools.html` → CRLF
（后两个原本混入 45 / 2 行纯 LF）。改写前后以「按 LF 归一后内容逐字节一致」断言把关，原文件
备份在 `run/_eol_backup_20260917/`。归一后 `app.js` 的 diff 降到 110 行（只剩真实改动）。
**`.gitattributes` 的 `* -text` 未改** —— 纪律仍是「编辑时保持文件原有行尾」。

**③ 清理 6 个残留进程**（只留 8765 主平台 / 8900 进化平台）
`:8791` `:8792`（进化平台临时实例，父进程已退）、`:8818`（主平台临时实例）、
`:8931`（`http.server` 目录列表 —— 会暴露整个平台目录）、`:8766`（nohup 拉起的进化平台副本）、
PID 409424（`tests/_diag_pages.py` 跑了两天，占 `:18777`）。
**`:_pick_port` 的连带发现**：`app.py:_pick_port()` 按 `[8765, 8900, 8989, 9600, 8888, 5050, 5000]`
顺序取第一个可绑定端口，所以 8765/8900 被占用时，用户双击 `启动平台-网页.bat` 会静默落到
8989/9600 —— `:8989`、`:9600` 正是这样来的用户实例，**未动**（用户自己关）。

**④ 排查中新发现并修掉的真实 UI 缺陷：`tools.html` 定年卡的 `<label>` 被砸坏**
`tests/_it_platform.py` 的「导航项均有 `<main>` 直接子卡片」断言报 15 个 id 缺失
（`t-dsrna` / `t-pd*` / `t-phylogeo`）。根因不是行尾也非本次改动，而是 `tools.html:1795`：

```html
<label data-i18n="tk.rttMetaLbl"
<button ... onclick="pdPickDataset('rtt_input','rtt_meta','date')" ...>📦 从数据接入 run 选择</button> data-page-node-id="ew5WnP90Z0UaA8mckngklu">元数据 CSV / TSV（必选：seq_id + 日期列）</label>
```

按 `webapp/templates/tools.html.bak_20260916_geo移植前` 的原文可知是 09-16 那次
`data-page-node-id` 注入把「📦 从数据接入 run 选择」按钮**插进了 `<label>` 的属性区**：元数据标签
文本丢失、「选择」按钮退化成一串文字、页面上还漏出裸的 `data-page-node-id="…">`，并且未闭合的
`<label>` 让后面 15 张卡片在 DOM 里**嵌套错位**（点导航项即空白页 —— 正是该断言防的那类 bug）。
已按原文恢复 label + 按钮为兄弟节点。全模板扫描（「行内标签未闭合且下一行直接开新标签」）
**只此一处**；`_it_platform.py` 恢复 `PLATFORM CHECKS PASSED`，真浏览器复核：标签文本回来、
按钮存在可见可点、无游离文本、`main > #t-*` 抽查全 True、22 页无 JS 报错。

**⑤ `tools.html` 又被改回混行尾 → 再次归一（并更正归因）**
归一化完成后复核，`tools.html` 又变成 `worktree=MIXED`（CRLF 6318 + **恰好 4 行**纯 LF，
mtime 15:26:01）：`pdltt` 卡片里的 `<b id="pdlttTitle">` / `<span style="flex:1">` /
`<a id="pdlttDated">` / `<a id="pdlttPhyloPdf">`。已按 CRLF 再次归一（备份
`run/_eol_backup_20260917/tools.html.mixed_20260917b`），全仓 130 个改动文件复核 **0 处不一致**。

**归因更正（当日稍后查明）**：这 4 行不是"树外 IDE 插件"写的，而是**另一个会话在同一工作区
就地编辑**时新加的行 —— 当天有人在给 `t-pdltt` 卡加「真 TreeDater 引擎」，`pdlttTitle` /
`pdlttDated` / `pdlttPhyloPdf` 正是那批新元素（同一小时里 `run/` 下出现 7 个就地改写
`phylodyn_trees.py` 的补丁脚本，还有两个 `_check_phylodyn_example.py` 并行在跑）。
**教训**：看到 MIXED 先查时间线与"有没有别人的 python 在跑"，别抢在别人正在写的文件上做行尾
归一化（会互相覆盖）；09-16 那次 `data-page-node-id` 砸坏 `<label>` 的来源仍未证实。
真正的守卫仍是 `tests/_it_platform.py` 的「导航项均为 `<main>` 直接子元素」断言
—— 它上次就是这样抓出 `<label>` 被砸坏的。

**⑥ 示例结果清单里 26 处 `source` 指向已不存在的路径（改对）**
`examples/results/manifest.json` 的 `source` 全部写着 `databases/examples/…`，而该目录
2026-09-10 起已不存在（示例根搬到了 `examples/`）；生成器 `tests/make_example_results.py:179`
早就是 `'source': 'examples/'`，只有这份旧快照留在旧值。`source` 不经
`/api/examples` 暴露（只出 title / run / files / generated_at），故无用户可见影响，属
"死元数据指错路"。已按 26 处文本替换为 `examples/`（备份
`run/_eol_backup_20260917/manifest.json.before_source_fix`），JSON 仍可解析、条目数仍 26。
**未删任何示例结果目录** —— `identity` / `structcmp` / `synteny` 三套产物是"卡片已下架的
历史快照"（前者现为 SDT 卡模式二、中者降为 MSA 卡内部比对、后者是已摘除的 LoVis4U 引擎），
它们在「👁 示例结果总览」里仍能正常打开（后端按目录列产物，与有没有卡片无关），
所以**删不删是用户的决定**，本轮只报告不动手。

**验证**：`_check_tree_overlay.py`（含两条新断言，exit 0）、`_it_platform.py`（PASSED）、
`_check_pages_console.py`（22 页 ✔）、`_check_examples_manifest.py`（✔ 无硬性差异）、
`_audit_contract.py`（✔ 无硬性差异）。两条新守卫都做了**负控**：把 ORF6 改成 `4651..6490`
→ `make_example_overlay.py` 以「CDS 超出记录长度 5640 bp」退出 1；把 overlay 首轨基因改成
越界+重叠 → 巡检恰好多出 2 条 FAIL。两处还原后 md5 与改动前一致。**未改 `dist/`、未同步 8900、未提交 git。**

## 2026-09-18 引物设计 / dsRNA 设计从「病毒注释分析」拆出，新组「检测与防治」

**用户提出的判据**：「引物设计、dsRNA 设计是不是独立功能？从功能注释移除，归到检测与防治。」
核过一遍，**是独立的** —— 而且判据不止"看起来不像注释"，有三条硬理由：

1. **回答的问题不同**。注释组（`annotate`）回答的是「这条序列**是什么**」——
   ORF / 数据库比对 / 保守域 / 图谱，产物都是**对序列的描述**；
   引物设计回答「怎么把它**检出来**」（产物 = 引物对，服务 PCR / qPCR），
   dsRNA 设计回答「怎么把它**防住**」（产物 = RNAi 分子，服务防治）。
2. **产物的消费方不同**。注释产物喂给「结果中心 / 提交」这条链（读、看、投稿）；
   这两项的产物是**可下单的湿实验材料**，必须真做实验才能闭环，与注释不是一条链。
3. **依赖与参数集完全不相交**。注释靠病毒库 / CDD / nt·nr；引物靠 primer3 热力学
   与模板保守性；dsRNA 靠 dsRNAmax 选窗 + dsRIP 效价 + 脱靶面板 → T7 引物。
   三者没有共享输入，挂在一个组下只是历史偶然（旧页面把它们放在注释页侧栏）。

**改动（5 处，仅导航/文案层，**不动任何分析代码**）**：

| 文件 | 改动 |
|---|---|
| `Virus_Platform_Core/web/pages.py` | `NAV_GROUPS` 里 `annotate` 组从 7 项减到 5 项（移出 `/primer` 与 `t-dsrna`），紧随其后新建 `{'id':'detect','label':'检测与防治'}`（2 项）；`_PATH_TO_GROUP['/primer']` 由 `annotate` 改为 `detect` |
| `webapp/templates/_nav.html` | `_nav_items` 在 annotate 与 compare 之间插 `('/tools?g=detect','nav.g.detect','检测与防治','detect')` |
| `webapp/templates/home.html` | 「病毒注释分析」卡 desc 去掉「引物设计」；新增「🎯 检测与防治」卡 |
| `webapp/static/i18n.js` | 中英各加 `nav.g.detect`（检测与防治 / Detection & control）与 `hm.entry.detect.d`；`hm.entry.annotate.d` 中英同步删掉引物（**中英必须同改，`_audit_contract.py` 会卡 zh/en 键数一致**） |
| `tests/` | `_it_platform.py` 加 `/tools?g=detect` 到 PAGES + 4 条新断言；`_it_nav_responsive.py` 把写死的「13 个一级链接」改成按 `NAV_GROUPS` 派生 |

**`_PATH_TO_GROUP` 是这层最容易漏的一行**：`_group_nav.html` 用 `current_group`
拼 `/tools?g=<组id>#<卡id>`，漏改则 `/primer` 侧栏仍列注释组、dsRNA 入口指错组。
故新断言直接切 `/primer` 渲染出的 `class="group-topbar"` 区块来验，
而不是照抄 `NAV_GROUPS` 自证（那等于把测试绑在实现上）。

**验证**：`_it_platform.py`（PASSED，含 4 条新断言）、`_audit_contract.py`
（✔ 无硬性差异，i18n zh/en 各 2154 键一致）、`_check_pages_console.py`
（20 页真浏览器 ✔，含 `/primer`）、`_it_nav_responsive.py`（✔ 全部通过；**首次跑
`/pipeline 640px` 报过一条 linkRows=3，复跑即 2 行** —— 640px 属极窄窗口、
折行处在临界，与本次 +1 项无关：真浏览器里在同一页面内逐档摘掉 detect / compare /
phylodyn 三个组，15 / 14 / 13 / 12 个链接在 640px **一律折 2 行**，1920px 一律 1 行）。
线上实例已重启（8765）实测：首页导航 15 项、`/primer` 组顶栏「🏠 检测与防治」。
**未改 `dist/`、未同步 8900（该平台无 annotate 组，无需镜像）、未提交 git。**

**没做但可以做的**（留给用户定）：本组目前只有这 2 项。同属"检测"语义、现挂在别组的
还有 `t-variant`（病毒变异分析，现属「病毒定量与共识」，产物是 SNP/变异图 = 检测）
与 `/hostpredict`（现属「病毒识别和分类分析」）—— 要不要一并搬进来，等用户口径。


## 2026-09-30 导航：「公共数据下载」回归「数据资源」组 + GSA 反爬坑

### 导航调整（用户提出）

「公共数据下载」（/download）从「样本处理」组移回「数据资源」组，排在
「公共数据检索」正后。历史上它本来就在数据资源组，2026-09-06（本笔记
第十八节）应要求移入样本处理组首位，理由是工作流顺序"拿数据→建样品→
质控→分析"——但该衔接靠的是下载页文件行的跨模块按钮（🧾样品/🚫去宿主/
🧹质控/🦠鉴定），并不依赖导航分组。语义上样本处理组其余各卡都是"处理
本地已有文件"，只有下载是"从公共库获取"；且"公共数据检索/公共数据下载"
这对名字对仗的页面分居两组确实别扭。

改动两处（`Virus_Platform_Core/web/pages.py`）：NAV_GROUPS 的 download 卡
从 sample.items 移入 resource.items（检索之后），`_PATH_TO_GROUP` 的
`'/download': 'sample'` 改 `'resource'`（漏改则 /download 侧栏仍列样本组）。
`webapp/templates/download.html` 无需动（无写死组名）。

验证：test client 实测 /download 组顶栏=数据资源、resource 工作台 4 卡含
下载、sample 5 卡不含；`_it_platform.py` PASSED（含导航↔卡对应检查）。
`_it_nav_responsive.py` 报的 `/pipeline 640px` 折行与本次无关——stash 回退
旧版复跑同样失败（甚至多一项 1920px，属真浏览器测量波动），见 2026-09-18
节"640px 属极窄窗口、折行处在临界"的既有结论。

### GSA（CRR）下载解析失败根因：NGDC 反爬验证页（重要踩坑）

用户报告"CRR708330 解析失败（SSL EOF）"，排查出**两层**：

1. **SSL 瞬断**（04:41 那次直接原因）：`_http_get` 原先一次失败即判死。
   现已加指数退避重试 3 次（URLError/SSLError/TimeoutError/ConnectionError）。
2. **根本原因**：NGDC 于 **2026-09-17 上线 JS 反爬验证页**（nginx 静态页，
   ETag/Last-Modified 可证），对**无 Referer** 的脚本请求，
   `/gsa/browse/` 详情页一律返回"正在进行安全检查"页。GSA 解析流程
   （搜索页→详情页→提取下载链接）中搜索页能过、详情页被拦 → 症状是
   "NGDC 未找到公开下载文件"。实测放行条件：**带站内 Referer 即可**
   （`Referer: https://ngdc.cncb.ac.cn/gsa/search`）；页内 JS 算的
   `auth_token` Cookie（base64(salt+UTC日期)，salt 从页内 `var secret=`
   提取）作为兜底二次重放。**注意**：MMPV-RNA `gsa_sra.down.py` 的
   requests.Session 写法（无 Referer）2026-09-17 之后同样会被拦。
   修复在 `public_data.py::_http_get`：NGDC 域自动带 Referer，命中验证页
   按页内 salt 算 token 重放。下载域名 download.cncb.ac.cn **不需要**
   任何 Cookie，aria2c 直接下。

另：meta 页「④ 运行任务」卡片加了日志折叠块（懒加载
`/api/task/<id>/log`，展开状态跨 10s 轮询重绘保持）与「任务中心 →」
入口——此前已完成的历史任务只有状态没有回看入口；完整历史（含重启前
归档）一直在 /tasks 任务中心页。浏览器验收脚本 `tests/_check_meta_tasklog.py`。

## 2026-09-30 续：公共数据下载支持自定义输出位置

用户要求：下载任务可指定输出位置（不填 = 默认），下载后的批次卡能看到
数据在哪。

- **创建时**：`DownloadManager.create(..., out_dir=None)`（public_data.py）。
  out_dir 非空 → `check_path(in_platform=True)` 校验后**该目录即批次目录
  本身**（不再嵌 `<批次id>` 一层，用户指定哪里数据就在哪里）；目录自动
  创建，已存在且非空时批次日志提醒"同名文件将被覆盖/续传"。越界（平台外
  且未挂载输出根）抛 ValueError，`/api/dl/create` 捕获后 400 原因透出
  （想用平台外目录：设置页挂载输出根即可，check_path 的 write_roots 自动
  放行）。worker 落盘全走 `fe['out']`/`b['dir']`，自定义目录零适配。
- **可见性**：snapshot 原先**不带 dir**（前端根本拿不到输出目录），已补
  `'dir'` 字段；批次卡进度条下方显示 📁 完整路径 + 「📂 打开」按钮
  （openOutDir，app-jobs.js 已有）。
- **表单**：download.html 任务名/并发行下加「输出位置（可选）」输入 +
  📁 browseDir 浏览按钮；创建成功后清空（每批一目录心智，防下一批误混）。
  **download.html 原先没 include `_browse_dlg.html`** —— 页面一加
  browseDir 就会踩"死键"坑（见 2026-09-11 节），已补 include。
- **样品链路无需改**：文件行「🧾 样品」按钮预填的是文件实路径
  （sendTo→sessionStorage→samples 页 R1/R2），自定义位置自动适配；
  样品目录本身仍固定 `results/<样品名>/`（全平台样品发现/分析/结果的
  根基假设，不宜随单次下载漂移）。
- 验证：离线 create 4 行为（默认/自定义/越界 400/空白回落）+
  API test client 5 断言 + 浏览器 6 断言（tests/_check_dl_outdir.py）+
  `_it_dl_progress` / `_it_download_ui` / `_it_platform` 全 PASS。

## 2026-09-30 续②：工具卡输出区被旧任务回显覆盖（格式转换实测）

用户报告：格式转换连跑两次，第二次已完成（右上「任务完成」弹窗都是新
run），输出区的运行日志却还停在第一次（旧输入文件的 CMD）。

**根因**：`injectStageLogs`（app-jobs.js）对 `/api/tasks` 里**每个**名称
匹配「格式转换」的任务都执行 `toolrun-convert 容器.innerHTML = ...`，而
任务列表是**新→旧**序（TaskManager.order.insert(0)）——同卡旧任务数组
靠后、后写覆盖新任务。凡是能对同名任务跑多次的卡（转换/质控/鉴定…全部
TOOL_LABELS 卡、管道阶段日志 log-<key>、建库日志 buildrun-<key>）都有此
隐患，转换只是最先被撞见。

**修复**：injectStageLogs 里加 `claimed` Set，每个容器只吃数组里**第一个**
（=最新）匹配任务；三处注入（stage/toolrun/buildrun）统一。运行中优先于
已完成（顺序天然保证：新任务在前）。

**测试**：tests/_check_toolrun_latest.py —— 真浏览器连跑两次转换
（example_R1 → example_R2，seqkit 本地秒级），断言输出区最终是第二次的
run 名与日志；回退修复复跑 → 第二项 TimeoutException（=用户所见 bug 复现），
修复后 4 项全 PASS。测试基建备忘：
- 工作台卡片默认 display:none，需 `showModule('t-convert')` 激活；
- headless 全新 profile 必中 examples.js「首访自动弹示例结果」
  （.dlgmask z-index 320），JS 直调 `runTool('convert', btn)` 可绕开一切
  遮挡/点击拦截。

## 2026-09-30 续③：格式转换支持双端（R1/R2 分别转换输出）

用户需求：格式转换卡支持双端数据，双端则分别转换输出。

- **前端**（tools.html t-convert 卡）：`cv_input` 下新增可选 `cv_input2`
  （R2 文件）；`runTool('convert')` params 带 `input2`（trim，空则不传）。
- **后端**（tool_jobs.py `_tool_job_convert`）：
  - FASTQ→FASTA：`inputs = [inp, inp2] if inp2 else [inp]` 逐条独立跑
    seqkit fq2fa，产物按输入文件名各自命名（`GQMIX.R1.fq.gz →
    GQMIX.R1.fa.gz`），不合并；进度阶段 R1=0.3 / R2=0.7。
  - `.sra` 输入自含双端（sracha 解码天然出 _1/_2），带 input2 直接 400
    「.sra 文件自含双端信息，无需另选 R2」。
  - R2 后缀与 R1 不一致 → 400。`ctx.opt('input2')` 自带存在性校验。
- **验证**：API 层双端（R1/R2 两个 .fa.gz）/单端（行为不变）/
  sra+R2 400 / R2 格式不符 400 全过；浏览器 5 断言
  （tests/_check_convert_paired.py）含输出区双产物与两条命令日志；
  `_check_toolrun_latest`、`_it_platform` 回归通过。

## 2026-09-30 续④：样本处理四卡支持自定义输出目录（可选，不填默认）

用户需求：⚡一键前处理 / 格式转换 / 序列质控 / 宿主去除与序列提取 都加
输出目录设置（不填默认）。

**方案（工具运行框架层统一，四卡一次覆盖，全 registry 工具同样受益）**：
- 创建层（tools_api `/api/tool/run`）：body 顶层可选 `out_dir`，非空 →
  `check_path(in_platform=True)` 校验（平台内/挂载输出根内），产物写
  **<out_dir>/<run_name>/**（换父目录不换结构，多次运行不互混）；
  `tool_runs/<run_name>/` 仍创建并写 `out_link.json` 指针登记实际目录。
- 寻址层：`state.run_real_dir(run)` 统一解析（有指针→外移目录，无→原
  目录）；`/api/tool/runs` 列表、产物下载 `/tool_runs/<run>/<file>`、
  打开目录、删除运行、rdp/phylogeo/quicktree/contig 报告等 run 寻址全部
  换用。**没有指针登记，外移运行在历史区就是失联卡片**——这是本设计
  的关键不变量。
- 签名刷新：`/api/tool/runs` 的缓存签名基于 tool_runs/<run> 的 mtime，
  外移运行产物写在外部目录时列表会停在"只剩指针"——任务结束
  `_job_with_run` 里 `os.utime(base_dir)` touch 一下。
- 删除：清外移目录 + 指针目录两处；`_rmtree_retry`（Windows 下刚被
  send_file 下载过的文件句柄未释放时，gc + 0.3s 后重试一趟）。
- job 零改动：`ctx.run_dir` 已是外移后目录。
- 前端：四卡各加「输出目录（可选）」输入 + browseDir（cv_outdir /
  f_outdir / pc_outdir / hr_outdir）；runTool 的 convert/fastp/prepchain
  分支与 hostremoval 页提交带 `out_dir`。
- 验证：API 层 14 断言（tests/_check_tool_outdir.py：外移/指针/列表可见/
  下载可达/touch 刷新/删除双清 + 四工具各跑）；浏览器 7 断言
  （tests/_check_tool_outdir_ui.py：四卡字段渲染 + convert 端到端外部
  out_dir 显示 + 产物落盘）；`_check_toolrun_latest`、`_it_platform`
  回归通过。宿主库未建时 hostremoval 创建即 400（既有 db_ready 拦截，
  与本功能无关）。test client 教训：send_file 的 response 不 close 会
  占住文件句柄，让后续删目录在 Windows 上静默残留。

## 2026-10-21：主导航改版（顶部横条 → 左侧常驻栏）+ 首页分层架构总览

用户提供了一张智能辅助育种平台的架构图与工作台截图，要求借鉴其布局。
可迁移的三点里，落地了两点；第三点（右侧常驻文件面板）暂不做，理由见下。

**① 一级导航：顶部 14 项横条 → 左侧固定栏 + 顶部工具条**
- 旧方案的固有缺陷：14 个一级链接在 ≤1500px 就把横条撑成两行，
  `tests/_it_nav_responsive.py` 从 2026-09-11 起一直在守这个换行/溢出；
  用 CDP 实测 1920px 下内容宽 1674px 对盒宽 1675px，只差个位数像素，
  浏览器字体度量一抖动就掉行。竖排后该约束从根上消失。
- `_nav.html` 重写：`.sidenav`（品牌 / 一级链接 / 组内二级内联展开 /
  底部任务·结果·设置·手册）+ `.topbar`（☰ / 当前组标题 / 上下文胶囊 /
  语言 / 📂）。数据源仍是 `pages.py` 的 NAV_GROUPS，与 /tools 组落地、
  `_group_nav.html` 共用同一份定义。
- **结果中心组不在侧栏渲染**：底部已有直达入口（旧导航同口径），且该组
  没有 `nav.g.result` 的 i18n 键，渲染出来会显示成裸 key。
- 组内二级入口改为**内联展开**（`.sn-caret` 点击 + 桌面悬停），不再依赖
  悬停浮层菜单——触屏与窄窗口同样可用。展开态存 localStorage
  （`vp_nav_open`），跨页面保持。
- 窄屏 ≤1024px 收成抽屉：☰ 展开、遮罩/Esc/侧栏内 ✕ 收起、点链接自动关。
- 新增 `webapp/static/app-nav.js`（手风琴 / 抽屉 / 顶栏标题）；不依赖
  `$`/`t`/`esc`，纯原生 API，先于 app.js 执行也安全。
- `app.js injectGlobalCtx` 挂载点从 `.navlinks` 改为 `.navctx-host`；
  `i18n.js injectLangSwitch` 从 `.navlinks` 改为 `.navtail`。两处都保留
  旧选择器兜底，避免第三方模板漏改时胶囊/language 按钮凭空消失。
- **DOM 契约未变**：`.navlinks` / `.nav-drop > a[data-group]` /
  `#navTasksBadge` / `#navCtx*` / `#langBtn` / `.navtail` / `.iconbtn`
  全部保留，故 `_check_phylogeo_ui.py`、`_it_global_ctx_ui.py`、
  `_check_ai_dock_ui.py` 零改动通过。
- `tests/_it_nav_responsive.py` 按新语义重写：桌面档断言侧栏常驻/不遮挡
  主区/无横向溢出/链接数（NAV_GROUPS - result + 总览）/顶栏入口可见；
  窄屏档断言默认收起/主区全宽/☰ 与遮罩交互。旧口径的"横条单行"已无意义。

**② 首页新增「平台架构总览」（借鉴其分层架构图）**
- 位置：四张叙事卡之后、数据库模块之前。
- 结构：数据底座 → 分析引擎（五条业务线，底色加重作为视觉重心）→
  结果解读，右侧一列竖排「贯穿能力」（AI 助手 / 任务中心）。
- **每一块都是真实链接**，不是静态配图——点卡片直接进对应模块。
- 网格显式定位（`.arch-lane/.arch-arrow` 锁第 1 列，`.arch-side` 占第 2 列
  全部行）：不写死的话 auto-placement 会把第二条泳道塞进右侧列，整图错位
  （实测高度从 587px 涨到 1676px）。
- ≤1180px 时右侧贯穿列改为横排落到图下方，避免窄窗口"架构图看不全"。
- i18n 键 `hm.arch*` 中英成对（`_check_i18n.py` 1405 锚点全通过）。

**③ 未落地：右侧常驻文件/数据面板**
- 参考平台的右栏是"个人文件 / 公共文件"目录树，与其数据底座强绑定。
- 本平台现有 `/api/sample_files/<sample>` 已能列样品产物，但各模块的
  产物寻址是"运行目录 + tool_runs"而非统一文件树；做成全局右栏会与
  「结果中心」「任务中心」功能重叠，且挤压主区宽度。留待确有需求时再议。

**回归**：`_route_inventory` 246 条零差异 / `_check_js_syntax` 59 块通过 /
`_check_i18n` 1405 锚点全通过 / `_check_pages_console --isolated` 22 页
零 JS 错误 / `_it_nav_responsive` 全通过 / `_it_global_ctx_ui` 全通过 /
`_check_ai_dock_ui`、`_check_ai_global_ui` 全通过 / `_check_home_cover` 通过 /
`_check_phylogeo_ui` 通过。

### 2026-10-21 修复：分组点击后收不起来（悬停规则压过折叠态）

现象：点分组箭头折叠后，二级菜单仍然显示。

原因：CSS 里同时有 `.sn-group.open > .sn-sub{display:block}` 和
`.sn-group:hover > .sn-sub{display:block}`。鼠标必然停在分组上才点得到箭头，
此时 `:hover` 恒成立，把刚被 `.open` 移除的菜单又强制显示出来 —— 点击等于
没生效。:hover 那条是"桌面悬停也能看"的顺手便利，与点击开合语义直接冲突，
已删除；展开态现在只由 `.open` 类决定。

回归：`tests/_it_nav_responsive.py` 新增 `check_accordion()`，在 4 个页面 ×
1600px 下用**真实鼠标**先 `move_to_element`（让 :hover 激活）再点击，断言
1) 收起后 `.open` 为假且 `.sn-sub` 无渲染盒；2) 再点一次能重新展开。
必须用真实鼠标：只调 `element.click()` 不会让 :hover 成立，测不出这个缺陷。

### 2026-10-23 首页精简：移除静态封面叙事与重复架构图

按需求删除两块「只占版面、不产生操作」的内容：

- **四张叙事卡（Ⅰ 背景 / Ⅱ 方法 / Ⅲ 流程 / Ⅳ 意义）**：纯静态文案，
  与 hero 的科研定位句、下方模块入口信息重复。
- **平台架构总览（2026-10-21 新增）**：其节点与下方模块入口卡、
  左侧一级导航三处同源，属于第三份重复；删掉后模块入口仍由
  `hm.entry.*` 卡片承担。

同步清理：`hm.arch*` / `hm.cover.*` 中英各 22 / 8 条 i18n 键、
`.arch*` 与 `.cover-card/.cover-grid` CSS。

顺手修的四处首页缺陷：

- **深色主题白卡片**：`home.html` 内联样式用了 `var(--bg2)` / `var(--line)`，
  两个变量在 `app.css` 字典里根本不存在，深色主题下回落到 `#f8fafc` /
  `#cbd5e1` 浅色兜底值，快速开始与 MCP 控制台整块发白。现改用
  `--surface-2 / --line-200 / --input-bg / --blue-100` 等真实令牌。
- **回车不扫描**：输入框 placeholder 承诺「回车扫描」，但此前只有按钮绑定
  `QS.scan()`；补 `onkeydown` 后与文案一致。
- **首页漏掉进化动力学**：架构图删除后，模块入口卡是主区唯一入口，
  但卡片清单里没有 `phylodyn`（只有左栏有）。已按 `NAV_GROUPS` 顺序
  补卡与 `hm.entry.phylodyn.d` 中英键；现为 10 卡。
- **MCP 控制台移位**：原位置卡在「快速开始」与模块入口之间，把
  「粘贴路径 → 选模块」的主线截断；移到模块入口之后、最近样品之前。
  它是开发者调试面板，不该占据首屏主线位置。

`tests/_check_home_cover.py` 判据同步反转：断言 `.hero.cover` 存在、
`.cover-card` / `.arch` 不存在、封面文案非空、无横向溢出。
回归：`_check_i18n` 1376 锚点全通过 / `_check_js_syntax` 59 块通过 /
`_check_home_cover` 浅色+深色通过 / 1440·1280·1024·900·768 五档无横向滚动 /
回车触发 `scan_folder` 实测 1 次请求 / 首页控制台零 JS 错误。


### 2026-10-21 修复②：分组改为「整行标题」开关（箭头热区太小）

用户反馈：只有右侧小箭头能点，热区太小；应当点标题一次展开、再点收起。

改动：
- `_nav.html`：箭头从 `<button>` 降级为 `<span aria-hidden>`（纯装饰）；
  `aria-expanded` 移到 `a.sn-link` 上（整行才是真正的开关）。
- `app-nav.js`：点击整行标题 → `preventDefault()` + 切换 `.open`。
  href 仍保留组概览地址（JS 失效时退化为正常跳转）；有 JS 时不跳转，
  组概览走展开后的「🏠 组概览」项 —— 保持"点标题=开合"的单一语义。
- `app.css`：`.sn-caret{pointer-events:none}` 让点击穿透到整行。

**两个连带踩到的坑（都记在 CSS 注释里）**：
1. 箭头 `top:50%` 是相对 `.sn-group` 算的，而分组盒子 = 标题行 + 展开的
   二级菜单，高度随开合变化 —— 实测箭头偏出 104px、压到下一个分组上。
   改为相对分组顶部固定 `top:14px`（标题行 ≈38px 高，取半）。
2. 箭头**不能**挪进 `<a data-i18n>` 里：`applyI18n` 用 `el.innerHTML = v`
   覆盖，切语言时会把箭头整段抹掉。

回归：`tests/_it_nav_responsive.py` 的 `check_accordion()` 改为点 `a.sn-link`
（整行，不是 .sn-caret），并新增一条「点标题不跳转」断言 —— 若日后改回
只有箭头能点，或点标题误触发跳转，这里都会红。

### 2026-10-21 修复③：设置页侧栏点击跳转错位

用户报障：点左侧功能，跳转位置很乱、没有对齐。实测是**三个独立缺陷叠加**，
每一个单独看都像"跳转不准"：

1. **多列网格**：`.settings-grid` 是 `repeat(auto-fit, minmax(320px,1fr))`
   三列，而侧栏按文档顺序逐个列 —— 「输出/输入/数据库目录」三张卡在同一行，
   `top` 完全相同（实测都是 167），点哪个都滚到同一处，分不出目标。
2. **锚点被顶栏压住**：`.se-main section{scroll-margin-top:14px}`，而吸顶
   顶栏高 54px —— 滚动后卡片顶边贴在视口 0 处，标题被吃掉 40px
   （实测 `top=14` vs `topbarBottom=54`）。
3. **侧栏顺序 ≠ 文档顺序**：`sec-storage` / `sec-paths` 在 DOM 里排在
   「通用设置 / 数据库 / 分析默认参数」之后，侧栏却按 GROUPS 的书写顺序
   把它们归在「目录与存储」组前面 —— 点「平台目录」时按位置算出的当前
   区块是夹在中间的 `sec-defaults`，高亮跳到别人身上。

修复：
- 设置卡片改**单列流式**（`.se-main > .settings-grid{display:block}`），
  卡片内部的小网格 `#defaultsGrid` 不受影响。
- 新增 `--topbar-h: 54px`（app.css 根变量），`scroll-margin-top` 与
  sticky 侧栏 `top` 都引用它 —— 以后改顶栏高度只改一处。
- 侧栏条目改为**按 DOM 实际位置排序**（`docTop()` 累加 offsetParent），
  并把模板里 `sec-storage` / `sec-paths` 的物理位置前移到「通用设置」之前，
  让「目录与存储」组连续。侧栏顺序 = 视觉顺序 = 高亮判定顺序，三者同源。
- 高亮判定从 IntersectionObserver 改为**按滚动位置算**：观察器在多个区块
  同时进入观察带时后触发者覆盖前者（实测点「输入目录」高亮跳到
  「数据库目录」）。另加 700ms 点击锁 —— 平滑滚动途中 scroll 事件会连续
  触发，按位置算出的仍是上一屏的区块，会把刚点亮的目标覆盖掉。
- 点击时 `history.replaceState` 同步 `#hash`（不新增历史），带 hash 直接
  进入也按同一套偏移滚一次。

**过程中发现的两个坑（都写进了代码注释）**：
- `pickActive` 里"后面的都在下方就 break"是错的：从下往上跳时（sec-plugins
  → sec-paths）目标上方仍有多个区块越过顶栏，提前退出会停在上一个错项上。
  必须扫完全部再取最后一个。
- 首页 `home.html` 的 `VC` 对象被一行多余的 `};` 提前闭合（第 481 行），
  导致 `models: []` 变成非法语句、整个首页脚本 `Unexpected identifier`。
  该行是另一会话编辑首页时留下的残缺，已删。

回归：新增 `tests/_check_settings_nav.py`（已加入 `_run_all`）——13 个条目
逐个点击，断言落点偏移在 12~30px、高亮正确、各条目滚动位置互不相同、
侧栏顺序 == 文档顺序、`#hash` 深链也对齐、全程无 JS 报错。
`_check_pages_console` 23 页零报错、`_check_js_syntax`、`_check_i18n` 全通过。

### 2026-10-24 发布前 P0 批量修复（三档分发就绪）

按发布前体检结论逐项修复，全部有实测对照：

1. **分发说明文件损坏（打包脚本 bug）**：`package.py` 顶层说明的
   `'=' * 60 + '\n'` 写在隐式字符串拼接块里，Python 把它解析成「对前面
   整个字面量重复 60 遍」——产物 `程序与数据说明.txt` 首行重复 60 次。
   改为行列表 `'\n'.join()` 拼接，并顺手补 MCP / 插件两条说明。
2. **MCP 控制台在分发版静默失效**：`mcp_console._ensure` 固定
   `[python, mcp_server.py]`，冻结分发没有独立 python（exe 会把参数当网页
   参数）；`mcp_server.py` 不被 app.py 静态 import，PyInstaller 也扫不到，
   `_internal` 里连 mcp SDK 都没有——exe 版 MCP 控制台「连接」永远失败。
   修复三件套：app.py 顶部新增 `--mcp-stdio` 入口（在 Flask 启动前接管
   进程）；`mcp_console` 冻结模式改拉 `[exe, --mcp-stdio]`；spec 对 mcp
   `collect_all` + `hiddenimports += ['mcp_server']`。源码模式行为不变。
   `mcp_server.py` 同时随程序包/源码包各放一份。
3. **t-mirna 分发版必 500**：`databases/mirna_lib`（~13MB）从未入包，
   `tools_api._mirna_lib_rows` 缺库直接 abort。现随程序包拷贝，
   `dist_completeness.DEFAULT_PAIRS` 同步登记（+`__pycache__` 过滤）。
4. **数据库包混入 357MB 备份**：`_skip_engine_index` 的 `backup_*` 匹配
   不到带前导下划线的 `_backup_d17_20261019_*`（7 个目录），补
   `_backup_*` / `_bak_*` 两个模式。
5. **插件完整版打包入口**：`package.py --with-plugins` —— 32 个插件包
   （本地 `plugins-dist/` 优先，缺的按索引多地址下载并 sha256 校验）经
   `plugin_manager.install_zip(root=产物)` 预装进产物 `plugins/`。
   连带修 `install_zip` 的 `install_to` 基准 bug：原写死
   `PLATFORM_ROOT`，以 root=产物 调用时 6 个硬编码路径工具会写进**开发机
   仓库**而不是产物——改为 `dirname(root)`（默认调用两者等价）。
6. **测试两红 + 一挂死**：
   - `_it_platform`：detect 组期望集合补 `t-conan`（10-20 新增卡未同步断言）；
   - `_it_async_ui`：headless Edge 同会话连跑 4 个重页面在流程 4 原生崩溃
     （与 `_check_pages_console` 的 chromium 段错误同因）。改为**每流程一个
     全新会话**，首个会话失败仍 SKIP；实测批量满载下会话切换还会踩两个坑，
     一并修掉：流程 2 换会话后必须重新导航 /submit 并恢复页面级 CUR 状态
     （否则 no such element）；切换用 `_recycle()`——旧会话 quit 容错
     （批量下第 4 次切换 quit 会抛空消息 WebDriverException）、新会话 5 次
     递增退避重试，main() 的重试信息带异常类型（原来是空 Message，没法排）。
     VP_TEST_WAIT=90 批量口径连跑 3 次全绿；
   - `_check_pages_console`：父进程 `capture_output` 管道被 playwright 的
     chrome 孙进程继承，子进程超时被 kill 后管道写端仍被握着，
     `subprocess.run` 等 EOF **永久挂死**（实测卡 6 分钟无输出）。子进程
     输出改落文件 + `taskkill /F /T` 收树；18766 被占自动换临时端口；
     goto 错误截断 120→400 字符（原截断点恰好在 URL 端口前，误导排查）。
7. **依赖声明补齐**：requirements.txt 显式声明 mcp<2 / anyio / httpx /
   pydantic / uvicorn / cycler（此前仅靠传递依赖，干净克隆后 MCP 全断）；
   requirements.lock 以 3rd/python 绿色运行时 `pip freeze` 重新生成。
8. **RDP5 运行态文件出库**：`LastSave.rdp5` / `tempfile` 是用户会话快照，
   加入 .gitignore 并 `git rm --cached`（工具本体照常纳管）。
9. **第三方许可索引**：新增 `dev_tools/third_party_notices.py` 扫描
   3rd/bin+3rd/tools 随附许可文件与 3rd/python 全部 dist-info，生成
   `THIRD_PARTY_NOTICES.md`（33 工具 / 129 包）；README 分发形态章节改写
   为三档口径。

回归：见当日测试记录（目标测试 + 全量 quick + 产物自检）。

### 2026-10-24 补②：三档分层轴改定「插件当轴」+ 体积二轮瘦身

发布形态讨论定案（方案 B）：**"插件"从"完整版的标志"改为真正的分层轴**——

- **① 独立版** `--slim-tools`：3rd/tools 剔除 6 个大件进阶工具
  （srna-target / strawberry-perl / snpeff / jre-snpeff / snpgenie / datasets，
  ≈275MB）。它们的权威分发形态本就是 install_to 插件包，用户用到时
  「插件管理」一键补齐（模块运行前依赖自愈也会自动装）；
- **② +数据库版** = ① + 数据库包；
- **③ 完整版** = 全量 26 工具 + 数据库包，**不再预装 plugins/**——程序包
  自带全量 3rd 时插件层永远休眠（解析优先级 platform.json > 3rd 探测 > 插件），
  旧口径"完整版预装 32 包"实测是 1GB 纯冗余。`--with-plugins` 保留但降级为
  非默认的离线修复口径。
- 三档共用同一 exe/_internal，只有 3rd/tools 不同 → 档位切换用 `--no-exe`
  补拷，不必重跑 PyInstaller。

体积二轮瘦身（全档受益）：
1. **polars/_polars_runtime_32 剔除（-183MB）**：2026-09-11 它是 kv 引擎的
   必要成本（spec 旧注释）；f02736e 起 kv 全套迁 pandas（对拍
   tests/test_kv_pandas.py），全库 0 处 import——构建机残留的 polars 1.41
   连带把 pyd 拖进冻结包。spec excludes 落闸，注释同步改写；改动后须重跑
   test_kv_pandas + 已知病毒定量冒烟。
2. **OpenVirome SPA 双份收敛（-71MB）**：virome.py 优先级
   frontend/build(25MB 生产构建) > webapp/static/virome(71MB 开发副本)，
   冻结版此前两份都带、71MB 那份永远读不到。spec._tree 排除 static/virome，
   package.py 继续拷 frontend/build（两种运行模式的实际服务源都不变）。

### 2026-10-24 补③：独立版定稿「主流程最小集」（--minimal-tools）

分层轴继续收紧：独立版不再保留 20 个工具，改为**只内置主流程最小集**——
⓪质控(fastp) ⓪b宿主去除(minibwa+blast) ①组装(spades) ②鉴定(kunpeng)
⑦比对(mafft+trimal) ⑧建树默认链(fasttree) + kvsuite 定量/共识/变异
(minimap2/samtools/bcftools/salmon) + bin/ 小件（seqkit/sracha/crabz/aria2c/
muscle）。⓪~⑩ 双击即跑零联网；rdp5 / table2asn / mmseqs / iQtree /
raxml-ng / diamond / pandepth / viral_consensus / lsd2 / dsrnamax 等专项
工具全部走插件按需装（均有现成插件包，缺失时模块自愈自动恢复）。

保留集逐项依据：bcftools/salmon 为 kvsuite 真实依赖（known_virus_suite/
kv_common/kv_variant、kv_stage 实测引用）；trimal 在 pipeline/phylo 主线。
`--slim-tools`（口径 A，剔六件）保留兼容，`--minimal-tools`（口径 B）为
独立版默认推荐；两者都给定时 B 覆盖 A。

### 2026-10-24 补④：主流程保留集补 diamond/mmseqs/datasets（用户裁定）

「病毒识别和分类分析」缺 t-verify（diamond+mmseqs）、「数据资源」缺
/refdata datasets CLI——用户裁定这三件（合计 ~50MB）随独立版内置，
两组升到全开箱。_MAINLINE_KEEP 同步；插件轴剩 RDP5CL / table2asn /
iqtree3 / raxml-ng / lsd2 / pandepth / viral_consensus / dsRNAmax /
srna-target / strawberry-perl / snpeff / jre-snpeff / snpgenie。

### 2026-10-25 补⑤：病毒库命名统一 virus_db，ref 改可选下载件（用户裁定）

- **命名**：kunpeng_db/{plant,ref} 双槽位取消——默认病毒库统一叫
  **virus_db**（DB_LAYOUT 子键 + 目录 + platform.json + VIRUS_DB_PRESETS
  + build 页 kv_index 配对名同步改名）；随包内置的植物库直接占用该槽位。
- **ref（RefSeq 通用库 680M）不再随数据包分发**：改为可选下载件
  **ref-db**（kind=database 插件，install_to=databases/kunpeng_db/ref，
  712MB zip，sha256 校验）——插件源/GitHub Release 已上线
  （gh release upload plugin-packs 完成；镜像站 docs/plugins/ 待用户自传）。
  安装后数据库页扫描自动列出，工具②/④下拉/构建页选用；卸载只删 ref 本级。
- 数据库包 2.3G→**1.6G**；包内陈旧 kunpeng_db/{plant,ref} 由打包脚本清除。
- 连带修 **plugin_manager.uninstall 硬编码 PLATFORM_ROOT**：自定义 root
  调用（打包预装/干测）会误删开发机仓库同名目录（本次实测误删
  databases/kunpeng_db/ref，靠 ref-db zip 恢复）——改为 dirname(root)，
  与 install_zip 的 install_to 基准同源；干测三断言全绿（落位/不伤
  virus_db/只删 ref）。
- ref-db 包构建器：dev_tools/build_ref_db_pack.py（--check 校验索引）。
  布局坑：zip 内文件必须平铺插件根（install_to 平铺语义），包一层子目录
  会嵌成 kunpeng_db/ref/ref/。

### 2026-10-25 补⑥：tax_db 瘦身——随包只带官方 tar.gz（546M→155M，用户裁定）

回答三个问题的落地方案：taxonomy **数据**不可省（宿主库只有一个宿主
taxid，顶不了科/属谱系/旭日图/宿主预测统计/ICTV 对照/merged 修正，约
10 模块在用）；但**解压态不必随包**——官方 new_taxdump.tar.gz（现为
155M，README 旧文案 ~57MB 已校准）就是 nodes/names/merged 的源，且
prepare_taxonomy 本就"有本地 tar 则纯解压零联网"。

- 数据库包：tax_db 三件 546M → new_taxdump.tar.gz 155M（DB 包 1.6G→1.2G）
- app.py 首启后台线程自解压（不阻塞启动；数据库页一键按钮兜底）
- **修 `_detect_database_root` 鸡生蛋**：探测标记原本只认
  tax_db/nodes.dmp——tar-only 新包永远探测不到，exe 回落程序内
  databases 并重新联网下载（v13 --verify 实测复现）。标记扩展为
  nodes.dmp / tar.gz / 预建 virus_db / annot_db 任一。**该修复在
  config.py，必须重打 exe 才进冻结版**（v14 已重打验证）。
- --verify 增 taxonomy 验证步：exe --cli init-taxonomy 本地解压验证
  tar 完整性 → selfcheck → 清理验证用解压态，分发包只留 tar。

### 2026-10-25 补⑦：数据库包冗余清除（1.2G→536M，用户裁定"不要冗余"）

逐目录核查"数据集 vs 派生索引/可选场景"，四刀：

1. **annot_db/prot 550M→103M**：只带 viral_prot.faa.gz（102M）。faa 是 gz
   的解压态、dmnd 是 faa 的 DIAMOND 索引、organism_tax.tsv 由 faa 重建——
   ensure_viral_prot_db 的自愈链全在（gz→gunzip 出 faa → 建 tax_tsv →
   _ensure_search_db 建 dmnd；safe_open 读 gz 自动解压）。首用重建约
   2~5 分钟，一次性。
2. **misc_db/suvtk 212M→0（转可选件 suvtk-db）**：只有 NCBI 提交准备的
   suvtk 注释阶段用它，而提交本就必须联网。dev_tools/build_suvtk_db_pack.py
   打包（222M，11 文件，索引登记，GitHub Release 已传）；install_to=
   databases/misc_db/suvtk（指本级，_swap_into_place 整目录替换不伤同级
   viroids/prob——干测三断言验证）。
3. **virusref_db/blast 预置 v4 索引 12M→0**：assembly._rebuild_if_stale
   缺失即就地重建 v4（中文路径可建可用，秒级）。
4. **misc_db/viroids v4 索引 5M→0**：acvirus.ensure_blast_db 同口径自建；
   fasta/acc/注释表照发。

不可再砍项：annot_db/hmm 119M 已是 press 产物最小形态（.hmm 主文件本就
未随包）；plant_ref reference.fasta 43.7M 是唯一参考真相（gz 化需改多个
读取方，收益 24M 不值）；prob/viroids 数据表本体保留。
DB_TREES 升级为三元组（第三项=该树的 fnmatch ignore 清单）；陈旧清理
清单扩 suvtk/blast。DB 包四轮轨迹：2.3G→1.6G→1.2G→536M（-77%）。

### 2026-10-25 补⑧：suvtk-db 撤销 + prot 改随包 dmnd（用户二调）

1. **suvtk-db 可选件整体撤销**（索引 34→33、本地 zip/构建脚本删除、
   GitHub Release 资产删除）。依据：bfvd 三处调用点（⑥b orf_annot 层2、
   hmm_annot 病毒域层、③b verify 分支）缺库时全部优雅降级（跳过+WARN），
   RefSeq 蛋白 + Pfam 两层注释不受影响；确需 bfvd 证据层的自备用户可经
   platform.json databases.bfvd 登记自己的库。
2. **annot_db/prot 改随包 viral_prot.dmnd（228M）**，原始序列 faa/faa.gz
   均出包（536M→663M，+127M 换注释开箱零重建）。ensure_viral_prot_db
   新增就绪短路：dmnd + organism_tax.tsv 在位且非 force/local_faa 时直接
   返回（不触发 faa 还原/RefSeq 下载）；实测 0.32s 返回、trace 完整。
   faa 从此只是"重建源"——切引擎（mmseqs/blastp）或库损坏时才需要，
   届时自动从 RefSeq 下载。调用方仅用 engine/db 键，短路返回安全。
   DB 包轨迹：2.3G→1.6G→1.2G→536M→663M（dmnd 预置回填）。

### 2026-10-25 补⑨：NCBI 提交链去 suvtk-db 依赖（用户裁定）

问题：`build_sqn` 的 `_check_ready` 无条件要求 suvtk 模块 + mmseqs + BFVD
库——连「平台自家 feature table」模式都被拦，分发版（无开发机 editable
suvtk、无 bfvd 库）生成 .sqn 必失败。

解耦三层：
1. **自检按模式**：`_check_ready(use_features=)`——table2asn 是唯一硬依赖
   （两种 tbl 模式都要）；suvtk 模块/mmseqs/BFVD 仅 features(BFVD) 链检查。
   实测：模拟分发态下 use_features=False 零错误、True 精确报 2 项。
2. **comments 可跳**：`suvtk comments` 产出的 .cmt 只是 table2asn 可选的
   `-w` 输入——suvtk 模块不可用时跳过并 WARN，.sqn 照常生成（少 MIUVIG
   结构化注释块）；用户自备 sqn.cmt 放项目目录后重新生成即走复用分支。
3. **web 自动回退**：请求 BFVD 链但 `suvtk_chain_ready()` 为假时，自动切
   平台自家 tbl（不存在则现场 `generate_feature_tbl(name)` 生成），异步
   任务的进度 marks 也按实际模式生成（回退时不再显示 suvtk 阶段名）。
   新增 `suvtk_module_available()`（带缓存）/ `suvtk_chain_ready()`。

注意：改动在 suvtk_submit.py + web/submit.py（冻结在 PYZ）——必须重打
exe 才进分发版（v18 已重打验证；_it_submit 集成测试通过）。

### 2026-10-25 补⑩：suvtk-db 恢复为可选下载件（用户三调：实用能力要留）

撤销上一轮"完全不提供"：suvtk-db（BFVD 222M）重新上架为**可选下载件**
（索引 34 个包、GitHub Release plugin-packs、干测三断言过）。定位澄清：

- **装 addon 即得（平台原生，无需 suvtk 模块）**：⑥b ORF 注释层2 的 BFVD
  病毒域证据（bfvd_search 与 suvtk 提交链同库同命名）、③b 候选验证 BFVD
  分支、提交准备用平台 tbl 时 product 含 BFVD 证据（merge_layer2 →
  orf_annotation.tsv → generate_feature_tbl）。
- **仍需 suvtk python 模块（开发机 editable，分发版没有）**：`suvtk
  features`（其 tbl 由平台 generate_feature_tbl 等价承接）、`suvtk
  comments`（.cmt 可自备后复用）。提交链保持上一轮解耦：缺模块跳过+WARN，
  缺库自动回退平台 tbl，不阻塞 .sqn。

### 2026-10-25 补⑪：BFVD 定案「优雅降级」+ 发布硬化 + 全面质检

1. **BFVD 层不硬删，改为优雅降级**（用户裁定）：保留 ⑥b 层2 BFVD 证据与
   ③b BFVD 分支的完整代码；缺 bfvd 库/mmseqs 时自动跳过并 WARN（实测
   run_bfvd 缺库 → None + "未找到 BFVD 库…跳过"，不阻断）。suvtk-db 恢复
   为可选下载件（索引 34 包 / GitHub Release 已上传）；提交链维持解耦
   （平台 ⑥b tbl 默认、suvtk 模块缺失时 comments 跳过）。
2. **发布硬化（体积污染门禁）**：构建机装有 torch/transformers/sklearn/
   opencv/dask（平台源码零引用），v19 构建被 hooks-contrib 的
   sklearn.array_api_compat.torch 等边卷进 _internal（+600MB，冻结自检 ⑥
   误报数百项可选依赖）。三处硬化：
   · spec excludes 显式排除上述重型 ML 栈（构建可复现）；
   · audit_deps 源码遍历跳过 `_internal`（冻结态自检 ⑥ 回到"不适用"）；
   · package.py --verify 新增体积门禁：_internal 出现 torch/transformers/
     sklearn/cv2/dask 即失败。
   实测 v20：程序 888M（回到基线）、selfcheck rc0、门禁 ✔。
3. **dist_completeness 档位化**：新增 --minimal-tools/--slim-tools，与
   package.py 同源清单过滤 3rd/tools 源码侧；webapp 比对忽略 static/virome
   （spec 主动排除的死重副本）。独立版口径复跑：全部 ✔ 无缺件。
4. 本轮质检项：compileall 全树 PASS；audit_deps 43 项全覆盖；许可清单
   最新；两个 addon 索引一致且安装/卸载干测通过；GitHub 资产 34 个；
   exe MCP stdio 握手 22 工具；全量 quick 回归见 _release_quick.log。

### 2026-10-25 补⑫：插件源三源齐备 + 镜像/仓库实更新

- plugin_manager.DEFAULT_INDEX_URLS 增加 **Gitee raw 第三兜底**
  （镜像未更新 / GitHub raw 不可达时仍能取索引）。
- 自建镜像 39.106.101.94 实传：index.json（34 条）+ ref-db-plugin.zip
  （712M）+ suvtk-db-plugin.zip（222M）到 docs/plugins/。
- Git 推送：origin main + tag v1.1.0（含全部 24+ 提交）。
- Gitee：main 同步 index.json（仅源码镜像，>100MB addon 走镜像/GitHub）。

### 2026-10-25 补⑬：GitHub 仓库收敛为一个（用户裁定）

保留 **plant-virus-analysis-platform**（本次发布/34 插件资产/索引 URL/Gitee
镜像同名的唯一主仓）；VirusPlatform 旧仓（9-28 独立历史 + 唯一 wiki）归档
（archived=true，只读保留，不删历史）。
- wiki 内容本就在主仓 wiki/ 目录（28 篇）——README/README_EN/全部 wiki 页
  与 mcp_server 里的 VirusPlatform 链接统一改为主仓路径（页面级链接指向
  blob/main/wiki/<Page>.md，目录链接指向 tree/main/wiki）。
- 归档而非删除：旧仓 wiki/历史仍只读可访问；如确认彻底删除可再执行。

### 2026-10-25 补⑭：GitHub 主仓改名 PlantVirusPlatform

`plant-virus-analysis-platform` → **PlantVirusPlatform**（GitHub PATCH name，
web 链接自动 301；raw 不跟随重定向，必须同步改代码）：
- plugin_manager.DEFAULT_INDEX_URLS 的 GitHub raw 索引（拆分字面量）；
- 两个 addon 构建脚本的 Release 兜底 URL；plugins-repo/index.json 全量
  GitHub 资产 URL；README/README_EN/wiki 全页/mcp_server UA/克隆指引；
- 镜像站 index.json 需重传（含新 URL）；Gitee 仓库名不动（Gitee raw 索引
  与资产 URL 仍指向 gitee.com/zhang-wenda/plant-virus-analysis-platform）；
- 本地 git remote origin 切 SSH 新名；移除指向已归档旧仓的 virusplatform
  remote。

### 2026-10-25 补⑮：插件下载源可选（用户裁定）

新增「下载源偏好」设置（platform.json `plugin_source_pref`，取值
auto / mirror / gitee / github）：
- `plugin_manager.order_urls()`：稳定重排——指定源提到最前，其余按原顺序
  兜底（auto = 原顺序不变）；作用于**索引拉取**（fetch_index/fetch_modules）
  与**每个包的 urls 回退链**（install_url/install_urls，模块安装共用）。
- 设置页「插件源」区新增下拉，onchange 即时保存并强制重取索引（缓存按
  首地址区分，换源自动失效）；索引 URL 覆盖（plugin_index_url）仍可用。
- 单测 test_order_urls_source_pref（auto/三源优先/兜底顺序/非法值回落）；
  回归：插件单测 35 项、i18n 校验、JS 语法、设置页锚点全过。


### 2026-10-26 续④：打包清单两处修正（db-only 覆盖程序清单 / 构建机路径泄露）

- `--db-only` 以前会把 `APP/EX/SRC` 的 `build_info.json` 一并重写，程序包里
  因此留下数据库构建的 `argv` 和源树 7GB 的 `db_dirs` 统计。现在只给本次
  真正重建的包写清单（`--db-only` 只写数据库包）。
- `_clean_probe` 原先只按 `DIST`/`ROOT` 做字符串替换，仓库改名前的旧绝对
  路径（`D:\<旧名>\3rd\...`）会漏进产物。新增通用盘符路径抹除，只认到
  `3rd` / `databases` / `host-db` 为止。
- 每个包只统计自己带的 `databases`/`host-db`；清单新增 `tool_tier`
  （full / slim / minimal）。

产物验证：两版 `build_info.json` 均为纯 ASCII、无盘符路径。


### 2026-10-26 续⑤：外置数据库根下程序内置小数据失联（miRNA 页 500）

三分离分发时 `_detect_database_root()` 会把 `DIRS['databases']` 重定向到同级
`VirusPlatform-Database`，而 `databases/mirna_lib`（13MB 候选 miRNA 库）与
`databases/dsrna`（热力学查找表 + siRNA 参数）是随**程序**走的小数据，不在
数据库包内：

- `/api/tool/mirna_lib*` 只查 `DIRS['databases']` → 分发版恒 500
  「候选 miRNA 库缺失」；
- `/api/tool/dsrna_panel` 面板清单恒空；`dsrna_pipeline` 与 API 取的不是
  同一个根（前者 PLATFORM_ROOT、后者数据库根）。

修法：新增 `config.db_asset(*parts)` —— 数据库根优先、程序内置兜底；两处都
没有时返回数据库根路径（可选面板数据可以放进数据库包）。`tools_api`、
`dsrna_pipeline`、`dsrna_scoring` 统一改走它。

验证：`tests/_check_db_paths.py` rc=0（148 文件 0 命中）；程序包重建后两版
selfcheck rc=0、dist_completeness rc=0；体积不变（1.88 / 2.57 GB）。


### 2026-10-26 续⑥：分发版布局再简化（启动脚本上移 / 小数据并入数据库包）

用户裁定两处：

- `Start-Desktop.bat` / `Start-Web.bat` 从程序目录上移到**版本根目录**，双击
  即启动 `VirusPlatform/VirusPlatform.exe`（`cd` 目标不带尾部反斜杠，避免
  cmd 把 `\"` 当转义引号）；`--no-split` 布局仍写在程序根。
- `databases/dsrna`（热力学查找表 + siRNA 参数）与 `databases/mirna_lib`
  （候选 miRNA 库）从程序包移入数据库包 `VirusPlatform-Database/databases/`。
  程序目录不再有 `databases/`，与数据库包不再同名混淆；运行时经
  `config.db_asset()` 在数据库根下解析（数据库根优先、程序内置兜底）。
  dsRNA 面板 `panel/` 与 `_cache/` 是用户自备的 GB 级数据，不入包。

同步改动：`package.py` 的 `_DB_TREES` 增加两项、移除程序内副本、启动脚本写到
版本根；`dist_completeness.py` 的比对项改指数据库包（`--no-split` 自动回退）；
`help.html` 安装说明与版本列表更新为两版口径。

验证：程序包 1.81 GB / 数据库包 0.71 GB（+13 MB 小数据、程序 −13 MB，总量
不变）；两版 selfcheck rc=0、dist_completeness rc=0（dsrna 2 / mirna 6 文件
全部就位）；taxonomy 仍只带 tar，首跑自动解包。


### 2026-10-26 续⑦：发布目录收敛为 Release/（删除根目录中间产物）

用户指出 PVAP 根目录的三件产物与 Release/ 并存，「越来越混乱」。澄清：
根目录 `VirusPlatform / VirusPlatform-Examples / VirusPlatform-Database` 是
package.py 的**构建中间态**；Release/ 才是对外发布目录（两版硬链接克隆）。

新增 `dev_tools/finalize_release.py`：
- 从三件中间产物组装 `Release/PlantVirusPlatform`（13 工具）与
  `Release/PlantVirusPlatform_with_Plugin`（26 工具），写启动脚本与
  README、按版补 build_info.edition；
- 组装成功后默认**删除根目录三件中间产物**及根目录启动脚本/说明
  （--keep-build 可保留）；
- 中间产物已删时幂等退出（Release 已在则不报错）。

`package.py` 文件头补「发布最终化」口径。当前 PVAP 根目录只剩 Release/
与用户自备文件；Release 逻辑总量 4.45 GB（两版各 1.88 / 2.57 GB），
硬链接共享后实际占盘 2.2 GB。两版 selfcheck rc=0。


### 2026-10-26 续⑧：两版直接放 PVAP 根目录，取消 Release/

用户要求去掉 `Release/` 这一层：两版直接位于 `D:\桌面\PVAP\` 下。

- `finalize_release.py` 的 `REL` 改为 `DIST`（PVAP 根）；`README.txt` 写到根；
  组装成功后顺带删除旧布局的 `Release/`；中间产物清理逻辑不变。
- 现有两版用同卷 `shutil.move` 迁移（保留硬链接，不复制 GB 级数据），
  删除空 `Release/`。
- `package.py` 文件头「发布最终化」口径同步：最终只保留
  `PlantVirusPlatform/`、`PlantVirusPlatform_with_Plugin/`、`README.txt`。

验证：两版 selfcheck rc=0；体积不变（1.88 / 2.57 GB，磁盘共享后约 2.2 GB）；
PVAP 根目录只剩两版 + README + 用户自备文件。

注：从仓库目录作 cwd 启动 exe 时，宿主沙箱会让 PyInstaller 的 matplotlib
临时目录探测失败（`No usable temporary directory`）；从版本目录启动或在
`Start-*.bat` 下（cwd=程序目录）正常，属于测试环境沙箱行为，与产物无关。


### 2026-10-26 续⑨：清理 PVAP 根目录的开发残留

删除（约 1.7 GB）：
- `_audit/`：历次测试结果（tests/results、_b9/_b11_results）、dist12 构建、
  249 个一次性脚本与日志；
- `_dbtest/`（空目录）、`_orf_root_probe/`（orfipy 探针日志）、
  `show_left.py`、`_handoff10.py`（一次性扫描/追加脚本）。

保留方式：`_audit/HANDOFF.md`（25 KB，双语 CLI/日志等交接记录）先复制为
`docs/HANDOFF_2026-10-26.md` 再删目录，避免丢失未入库的交接信息。

`package.py` 中引用 `_audit/make_launchers.py` 的注释同步去掉。PVAP 根目录
现在只剩两版 + `README.txt` + 用户自备的 `git-backup-pre-filter-20261011.bundle`
（592 MB，未动）。


### 2026-10-27 样品锚（第一阶段：只读）+ 左下常驻条

背景：用户提出「步骤很多，怎么固定全流程分析相关的样品是同一个？每个任务
有独立运行空间」。诊断结论：流水线内部靠同一 `run/results/<样品>/` + `_Ctx`
强绑定；但工具箱任务（`run/tool_runs/<tool>_<ts>_<uuid6>/`）没有样品字段，
续跑只看 `.done` 标记，`project.json` 只记路径不记内容 —— 三处都可能悄悄
换掉样品。方案是给「一件样品」立一个**可核对的锚**，先只做记录与显示，
不动任何判定。

新增（本阶段只读，任何异常都只写 `run/anchors/_anchor.log`，绝不外抛）：
- `Virus_Platform_Core/anchor.py`：`run/anchors/<slug>/anchor.json`（输入
  size/mtime/sha256、每轮 run、阶段、产物清单）+ `ledger.jsonl`（每行带
  `prev`，hash 链可校验）。`slug_of` 确定性：`safe_sample_name` 有损折叠时
  补原名 sha1 前 6 位，避免中文样品名塌成 `1`/`A` 互相撞目录。首次提交不阻塞
  ——先拿 size/mtime，sha256 交给后台线程，算完回填。输入变了只记
  `input.change`（`input_set_count++`），**不拦**。
- `Virus_Platform_Core/web/anchor_api.py`：`/api/anchor/active`、
  `/api/anchor/<slug>`、`/api/anchor/<slug>/verify`（三条都只读）。
- `app.py` 注册蓝图；`web/samples.py` 每行带 `anchor` 摘要；路由基线
  248 → 251。
- `pipeline.py` 三处接线：`run_analysis` 起跑 `start_run`、阶段前后
  `stage_begin/stage_done`、失败/正常收尾 `end_run`，全部 try/except。
- 前端：`app.js` 左下常驻条（`/api/anchor/active` 6s 轮询，显示
  `样品 · 项目 · 阶段 N/总 · 当前阶段 · R1 指纹`；「核对」直达 verify
  接口；× 可收起且轮询不会重新弹出；接口缺失/无锚时整条静默隐藏）。
  `app.css` `.anchorbar`（左下 `left:12px;bottom:12px`，避开右下 AI Dock）。
  `i18n.js` 增 `anc.*` 中英词条。

过程中修掉的两个真问题（都会让常驻条「停在上一阶段」）：
1. `_hash_async` 后台线程与 `stage_begin/stage_done` 各自读改写
   `anchor.json`，后写者用旧快照覆盖新事件 → 所有写入统一走
   `_mut()`（RLock + 每次重新读盘）。
2. `_write_json` 的临时文件名固定 `.tmp`，两线程并发时先替换者把 tmp
   拿走、后写者 `os.replace` 报 FileNotFoundError → 临时名加随机后缀。

验证：
- `python tests/_check_anchor.py`（约 40 条断言：sha256 正确性、账本链、
  篡改检测、换输入只记账、4 条 API 与 `/api/samples` 字段）；
- `python tests/_check_anchor_ui.py`（Edge headless：条子出现/位置/阶段/
  指纹/结束态/× 收起/删锚后静默隐藏/页面无未捕获错误）；
- `python -m pytest tests -q` → 219 passed；
  `tests/_check_js_syntax.py` → 59/59；`tests/_check_i18n.py` → 缺键 0。

下一步（待用户拍板，本阶段刻意未做）：把锚从「记录者」升级为「判定者」——
续跑前 `verify` 输入、阶段产物 `register` 到锚、输入不一致时按选项
拦截/警告/另起 run。

## 2026-10-28（补）：冻结版启动崩溃 —— 摘掉 `pyi_rth_mplconfig`

**症状**：分发版双击后控制台打 `FileNotFoundError: [Errno 2] No usable
temporary directory found in [...]`（`pyi_rth_mplconfig.py`），或换成新
PyInstaller 后变成**满 CPU 空转、永不开 8765**（`py-spy dump` 栈：
`secure_mkdtemp (tempfile.py) → _pyi_rthhook (pyi_rth_mplconfig.py:35)`）。

**根因**：这条 runtime hook 是 PyInstaller 为 **onefile** 写的（每次
`_MEIxxxx` 变化会让 matplotlib 的 `fontList.cache` 指向已删目录）。它是进程
**第一条指令**，无条件建临时目录：

- 旧版：`tempfile.mkdtemp()` → `gettempdir()` 逐个候选目录试写，`%TEMP%`
  不可写时还能退到 exe 目录，所以「有时能起」；
- 6.22：`_pyi_rth_utils.tempfile.secure_mkdtemp()` 直接对 `%TEMP%` 调带受限
  DACL 的 `CreateDirectoryW`；被拒后其 `except PermissionError: continue`
  会重试 `TMP_MAX`(1e4) 次 —— 就是那个空转。

**修法**（两层，已随 2026-10-28 的包发出）：

1. `dev_tools/VirusPlatform.spec` 里 `a.scripts` 过滤掉该钩子：
   `a.scripts = [s for s in a.scripts if s[0] != 'pyi_rth_mplconfig']`。
   onedir 的 `_MEIPASS` 固定，这条钩子本来就无收益，摘掉即根治。
2. `dev_tools/rna_early_import_hook.py`（排在所有自带钩子之前）改成**真探测**：
   逐个候选（`<程序目录>/run/tmp` → `%LOCALAPPDATA%/VirusPlatform/tmp` →
   `%TEMP%/VirusPlatform` → 程序目录）做 `makedirs` + 建删探针目录，第一个
   真能用的才写进 `tempfile.tempdir` 和 TMPDIR/TEMP/TMP；探测结果追加到
   `run/_boot.log`（≤16KB，覆盖写）。同时保留 `secure_mkdtemp` 替换兜底。
   RNA 预载改为 try/except：失败只降级 dsRNA/miRNA，不再拖死整个进程。

**排查副作用（重要）**：本机 `D:\PVAP` 与 `%LOCALAPPDATA%` 对**工具进程**
（受限令牌）的写权限不同 —— `run/` 能建目录、建不了文件，`%LOCALAPPDATA%`
直接 `PermissionError 13`。所以「我这边起不来」不等于用户那边起不来：判断
启动问题时以 `run/_boot.log` 和用户双击的结果为准，别拿沙箱里的现象当结论。

**验证**：`tests/_check_frozen_build.py <安装目录>` 两个版本全绿（含
`/api/dbs` 的 bytes/n_refs/meta、selfcheck usage、首页三块标记、webapp 六个
文件与源码逐字节一致）。

## 2026-10-28（补二）：工具结果「刷新/切页就没了」+ 手动归档

**现象**：跑完一个工具、看结果，切到别的模块或刷新浏览器，结果区空了，
只能翻「历史运行」或重跑。

**两个真因**（都实测复现，不是猜）：

1. 恢复逻辑只认 `/tools` 的 `section.card[id^="t-"]`。独立模块页
   （`/hostremoval`、`/genome`、`/orf`、`/cds-export`）根本没有这种
   section → `restoreLastResults()` 第一行 `if (!cards.length) return;`
   直接退出，一张卡都不恢复。
2. 它拉的是 `/api/tool/runs` 默认列表，**只有全局最近 30 条**。而
   `run/tool_runs` 已有 372 个运行，align（14 次）、hostremoval（11 次）、
   genoplot 这些老模块的最近一次早被新模块挤出 30 名 → 查不到就不恢复。
   历史区用同一份数据，所以老模块展开也是空的。

**改法**：

- `/api/tool/runs` 新增 `per=N`（每个模块前缀只取最近 N 次）、
  `limit`、`prefix=`；响应多一个 `prefix` 字段。恢复用 `per=1`，
  历史区用 `per=8`，全局列表默认仍是 30 条（其余调用点行为不变）。
  缓存键换成 `(签名, limit, per, prefix)`。
- `restoreLastResults()` 改为按页面实际可见的 `.toolrun[id^="toolrun-"]`
  收集目标，不再依赖 `section.card`；并给 17 个富结果卡补上
  `TOOL_RESTORE`（kvsuite / consensus / verify / kvchain / virchain /
  rdp / rtt / phylogeo / pdprep / phylodyn / dsrna / crispr / conan /
  sdt / identity / mirna），刷新后**重放专用渲染函数**（表格/图/度量），
  而不是只给一个下载列表。`cardResultFilled()` 用各自的结果容器判断
  「眼前已有结果」，不会覆盖正在跑的内容；`pageshow`（bfcache 后退）
  也触发一次。旧结果只在新任务开始产出时被替换。
- 归档（新增 5 条路由）：`POST /api/tool/runs/<run>/archive`、
  `POST /api/tool/runs/archive`（`{keep, prefixes}` 按模块保留最近 N 次）、
  `GET /api/tool/runs/archived`、`POST /api/tool/runs/<run>/unarchive`、
  `POST /api/tool/runs/archived/purge`。归档 = 把登记目录移进
  `run/tool_runs/_archive/`（`_runs_signature` 只扫非下划线目录），
  数据还在、可恢复；外移到用户目录的运行（`out_link.json`）只归档登记项，
  产物原地不动。有 running/queued 任务占用同一目录时 409。
  `GET /archived` 在归档区不存在/不可写时返回 `[]`（不 403）。
- 前端历史区（`app-runhistory.js`）：每行加「📦 归档」，头部加
  「🧹 清理…」（prompt 保留次数 → 批量归档）与「📦 归档区」（列表 +
  恢复 + 永久删除 + 清空归档）。中英词条各 22 个键。

**验证**：
- `tests/test_run_archive.py`（5 条，pytest，临时目录 + monkeypatch，
  不碰真实运行）：`per=1` 能带回被挤出 30 名的老模块；归档/恢复/永久
  删除；批量 keep=1/0；非法名与运行中任务 409。
- `tests/_check_run_restore.py`（7 条真浏览器）：`/hostremoval`、
  `/genome`、`/tools#t-align` 刷新后都有「上次运行」；kvsuite 重放出
  22KB 表格/图并带运行名；历史区列出 8 条且两个新按钮在。
- `python -m pytest tests -q` → 224 passed；i18n 1387 锚点全绿；
  JS 语法 59/59；`tests/_route_inventory.py --save` 基线更新（+5 条路由）。

## 2026-10-28（补三）：组装结果 → BLASTN/BLASTX/Primer 跳转全链路

接上一节「未动（留第二步）：anaJump() 跳转目标」，本次补齐。

**现象**：在「④ 病毒 contig 深度分析」的分类表点 BLASTN / BLASTX / Primer，
页面切到「病毒注释分析」组，然后什么都不发生（没有任务、没有结果）；
有时只闪一句「启动失败: 无效的运行名」。

**四个真因**（真数据 + 浏览器逐帧记录）：

1. `anaJump()` 目标写死 `#t-hom`，`processAnaJump()` 却取 `#homRun` 下拉并
   `if (!sel) return` —— `#homRun` / `#homTable` 在「同源比对卡改为序列输入」
   时就删掉了 → **静默退出**：URL 变了、组切了、卡也显示了，什么都没运行。
2. Primer 按钮走同一条路落在 BLAST 卡；独立模块 `/primer` 根本没被访问
   （hom 卡自己的说明就写着「引物设计请使用独立模块 /primer」）。
3. 「📊 分类报告」只存在于文案里：`openContigReport()` 零调用点，且
   `loadAnaRuns(true)` 在 `anaCurRun === latest` 时提前 return —— 刷新后
   分类表（连同那些按钮）再也回不来。
4. 底层：直接输入序列模式用 contig 名派生缓存目录，而 `_analysis_out()`
   的运行名白名单只允许 `[A-Za-z0-9_-]`；组装 contig 名普遍带点
   （`NODE_15048_length_278_cov_1.892683`）→ 一律 **400「无效的运行名」**。
   所以 1~3 修好也跑不起来。
5. 附带：`examples.js` 的 `autoFill` 在 DOMContentLoaded 后 400ms 把
   `/primer` 的 `#pd_fa` 改写成示例路径，覆盖刚带过去的序列。

**改法**：

- `anaJump` 先取 contig FASTA（`contigFastaOf()`，与「点 ID 复制序列」复用），
  再分路：`blastn/blastx` → 切 annotate 组、填 `#homSeqText`、
  `runCaSeqInput('hom', action)` 直接运行；`primer` →
  `sessionStorage.vp_primerjump` + `/primer`，落页后在 `window.load` 里回填
  `#pd_fa`（`pdRun()` 口径：含换行即 FASTA 文本）。
- `examples.js::autoFill` 增加 `window.__vpNoAutoFill` 让路开关（仅带参跳转
  时置位，其它页面示例填充不变）。
- 新增「📊 分类报告」按钮；`openContigReport()` 同时 `loadViralContigs()`；
  `loadAnaRuns` 提前返回改为 `latest === anaCurRun && !render`。
- `_seq_cache_name()`：读写两处统一规范成 `[A-Za-z0-9_-]{1,30}`（点→下划线），
  `api_tool_analyze` / `api_tool_analysis` 共用，缓存路径一致。
- CDD 按钮已于 2026-09-29 随本地库下线，确认列表只剩 BLASTN / BLASTX / Primer。

**验证**：
- `tests/_check_anajump.py`（8 条真浏览器）：按钮存在 → 点它把 20 行分类表
  拉回来 → BLASTN 切组、序列 384bp 填入、标题带 contig、结果区渲染出
  「Local DB (offline) no hit」→ Primer 跳 `/primer` 且 `#pd_fa` 以
  `>NODE_…` 开头（379 字符）。
- `tests/test_seq_input_cache.py`（6 条 pytest）：带点 contig 的
  blastn/blastx/primer 直接序列请求均 200；读路径 404 而非 400；
  `run=..\evil` 仍被拒绝。
- `python -m pytest tests -q` → 230 passed；i18n 1389 锚点全绿；JS 59/59。

## 2026-10-28（补四）：移除单实例锁（并发保护）

**起因**：启动时打印
`[提示] 实例锁创建失败（[Errno 13] Permission denied: run\.platform.lock），跳过并发保护。`
用户要求直接去掉并发保护。

**改动**：

- `app.py`：删除 `_pid_alive()` / `_acquire_instance_lock()` /
  `_release_instance_lock()` 及 `main()` 里的抢锁与三处释放；`main()` 不再
  因「已有实例」`SystemExit(3)`。原注释位置留了一句说明，指向本条记录。
- `Virus_Platform_Core/i18n_cli_catalog.py`：删掉 4 条与锁相关的 CLI 译文
  （残留锁接管 / 锁不可用 / 锁创建失败 / 已检测到平台运行）。
- 两个分发版的 `Start-Web.bat` / `Start-Desktop.bat`（共 4 个）：去掉
  `set "LOCK=..."`、读锁判 pid、`exit /b 3` 的「another instance already
  running」分支，以及退出时的 `del /q "%LOCK%"`；`run\tmp*` 的启动清理保留。
- 清掉两版 `run\.platform.lock` 残留。

**行为变化（需要知道）**：

- 现在可以同时开两个实例：实测第一个占 8765、第二个自动落到 8900
  （8766/8767 落在系统保留端口段）。**代价**：两个实例共享同一份 `run/`
  —— `queue.json`、`tasks/*.json`、`results/<样品>/` 会互相覆盖；批量
  跑样品时不要同时开两份。
- `启动平台-*.bat` 的退出码不再有 3（「被单实例锁拒绝」）这一种。

**验证**：`tests/_check_start_modes.py` 24/24 通过；`pytest tests -q` →
230 passed；冻结版起两个实例实测并存、无 `.platform.lock` 生成。

## 2026-10-29（补五）：浏览器没自动打开 / 没看到页面

用户反馈：双击启动后「没看到浏览器的启动页面」。查下来是**两条互不相干**
的静默路径，都在这次修掉了：

1. **旧的单实例锁**：`Start-Web.bat` 读到 `run\.platform.lock` 且里面的
   pid 仍存活时，会打印一行 `[info] another instance is already running`
   然后 `exit /b 3` —— 窗口 4 秒后自己关掉，浏览器根本不启动。锁已按用户
   要求在补四里整体移除，这条路径不存在了。
2. **`webbrowser.open()` 的返回值被丢掉**：Windows 下它走 `os.startfile`
   （默认浏览器关联），失败时**只返回 False、不抛异常**。旧代码既没看返回
   值也没有兜底，于是"没有页面"时控制台一声不响。

**改法**（`app.py::_open_browser`）：默认浏览器 → `os.startfile` →
`cmd /c start` 三级兜底；每一步结果打印到控制台并追加到
`run/_open_browser.log`（含 URL）。全部失败时明确提示"请手动在浏览器访问
<url>"。

**验证**：
- 用 `BROWSER` 环境变量拦截（把 `webbrowser` 换成写文件的 .cmd）跑冻结版：
  拦截到 `http://127.0.0.1:8765` —— 证明 exe 确实调用了浏览器打开。
- 源码模式跑 `python app.py --web`：`run/_open_browser.log` 出现
  `opened: http://127.0.0.1:8765`。
- 注意：`Start-Desktop.bat` 或不带参数的 exe 是**桌面窗口模式**
  （pywebview），本来就不会开浏览器标签；要浏览器就用 `Start-Web.bat`
  / `--web`。若已有实例占着 8765，新实例会改用 8900/8989 并在控制台打印
  实际地址。

## 2026-10-29（补六）：组装引擎增加 MEGAHIT（更快）

需求：`3rd/tools/megahit-1.2.9-win64/` 已放好，让组装能选 MEGAHIT。

**接线**：

- `config.detect_tools()` 增 `megahit`（`3rd/tools/megahit*/megahit.bat`，
  目录名带版本号，用 glob）。
- `assembly.py` 新增 `choose_assembler()` / `build_megahit_args()` /
  `run_megahit()`：
  - `auto`（默认）有 MEGAHIT 就用，`spades` 强制 SPAdes，`srna` 小RNA
    固定 SPAdes（MEGAHIT k 下限 15/默认 21，21-24nt 拼不出）；
  - `-m` 传字节（MEGAHIT 只在 0~1 之间才按内存比例）、内存按物理 60% 钳制；
  - 与 SPAdes 同款「路径含中文 → ASCII 临时目录中转」；MEGAHIT 拒绝已存在的
    `-o` 目录，所以先 rmtree、不预建；`--tmp-dir` 放同级；
  - 进度从 MEGAHIT 的 `log` 里数 k 阶段（8 档）。
- `tool_jobs._tool_job_assemble` 按 `params.assembler` 分派，`result.json`
  多一个 `assembler` 字段；选 `megahit` 但没装时给人话报错。
- 前端组装卡加「组装引擎」下拉（auto / SPAdes / MEGAHIT），单次运行与
  「批量组装」都带 `assembler`；勾「小RNA」自动把引擎拨回 SPAdes。
- `dev_tools/package.py` 的 `_MAINLINE_KEEP` 增 `megahit-1.2.9-win64`，
  否则标准版（minimal 口径）打包时会把 `3rd/tools` 里的 MEGAHIT 剔掉。

**关键坑：分发包不带 `3rd/python`**。`megahit.bat` 要先找一个 python.exe
才能跑它的驱动（找不到就 `exit /b 9009`），冻结版里必然失败。改法：
新增 `Virus_Platform_Core/megahit_engine.py`，用平台自己的解释器
`runpy.run_path()` 执行 `3rd/tools/megahit*/bin/megahit.py`（驱动只依赖
标准库，真正计算在同目录 `megahit_core.exe`），经既有引擎机制调用：

    冻结：VirusPlatform.exe --run-engine megahit_engine.py -1 R1 -2 R2 -o OUT ...
    源码：python -u Virus_Platform_Core/megahit_engine.py <同样的参数>

两个随之而来的坑：① 驱动里直接写 `exit(0)`（依赖 site 注入的内建名），
PyInstaller 不跑 site → `NameError: name 'exit' is not defined`，在
`megahit_engine` 里补 `builtins.exit/quit = sys.exit`；② 源码模式下
engine 脚本是**直接运行**的，包内相对导入不可用，`_get_config()` 先试
绝对导入、失败再把平台根塞进 sys.path。spec 的 hiddenimports 登记
`Virus_Platform_Core.megahit_engine` 与 `getopt`。

**验证**：
- 单测 `tests/test_assembly_engine.py`（8 条）：引擎选择矩阵（含 srna、
  未安装、非法值）、参数拼装（单/双端、字节内存、tmp-dir）、进度解析、
  `--run-engine` 注册守卫。
- 源码态 API 全链路：`POST /api/tool/run {tool:assemble, assembler:megahit}`
  → 日志「组装引擎：MEGAHIT」→ 9 contigs / N50 283bp → 长度过滤 9 条。
- 冻结版：`VirusPlatform.exe --run-engine megahit_engine.py --version` →
  `MEGAHIT v1.2.9`、退出码 0；真实组装 9 contigs / 1.8s。
- `pytest tests -q` → 238 passed；i18n 1393 锚点全绿；JS 59/59。
- 两个分发版已拷入 `3rd/tools/megahit-1.2.9-win64`（16MB）并换新包。

## 2026-10-29（补七）：控制台说"已在浏览器打开"，屏幕上看不到

用户实测：`Start-Web.bat` 打印
`已在浏览器打开: http://127.0.0.1:8765`，但浏览器里找不到页面。

**取证**：枚举 Chrome 窗口标题，发现早就存在
`功能树 - 植物病毒分析平台 - Google Chrome` —— 页面**确实打开过**，
只是那个窗口被控制台压住（或不是当前活动窗口），看起来就像"没反应"。
`webbrowser.open` / `os.startfile` 只是把标签投递给最近使用的窗口，
谁在前台它不管。

**改法**（`app.py`）：

1. `_default_browser_exe()`：从注册表解析默认浏览器 exe
   （`HKCU\...\UrlAssociations\http\UserChoice` 的 ProgId →
   `HKCR\<ProgId>\shell\open\command`）。拿到路径后**直接以新窗口启动**
   （Chromium 系 `--new-window`，Firefox `-new-window`），不再依赖 DDE 投递。
2. `_focus_platform_window()`：打开后按窗口标题（`植物病毒分析平台` /
   `Plant Virus Analysis Platform`）EnumWindows 找平台窗口，
   `ShowWindow(SW_RESTORE)` + `SetForegroundWindow` 提到前台，最多等 6 秒。
   控制台打印「已把平台窗口切到前台。」
3. 兜底顺序：webbrowser（系统关联，历来有效，放第一位）→ 直接起浏览器新窗口
   → startfile → `cmd start` → `explorer.exe`；每一步结果写
   `run/_open_browser.log`（含方法名），失败时明确给出手动地址。
   注意：新窗口方式不能放主路——受限令牌 / 浏览器处于另一完整性级别时它
   会"启动了但没有窗口"，实测把主路改成新窗口后反而完全不开。

**验证**：源码模式调用后控制台打印「已用新窗口打开 …」+「已把平台窗口切到
前台。」，`run/_open_browser.log` 记录
`opened (new-window: C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe --new-window http://127.0.0.1:8765)`
与 `focused existing platform window`；`pytest tests -q` → 238 passed。
两个分发版已换包（exe md5 `f1e11935262a33e7e7f027925fc5599e`）。

**续（同日）：用户机器上 `webbrowser.open` 返回 False，而"直接开新窗口"
只是拉起了进程、窗口没出现，程序当成成功 → 既不出窗口也不兜底。**
改为**每一步都验证窗口是否真的出现**：按 webbrowser → 新窗口 → startfile
→ `cmd start` → **Edge + 独立 user-data-dir** → explorer 依次尝试，每步之后
用 `_focus_platform_window(timeout=5)` 抓平台窗口；抓到就置前并停止，
抓不到就记日志继续下一条。最后一条 Edge 独立配置不加入任何已运行实例，
Singleton/完整性级别冲突都绕开，几乎必定出窗口。控制台会明确打印
「XXX 已调用但没看到窗口，换下一种方式…」，`run/_open_browser.log` 记录
每一步的返回值。

**再续（同日，真凶）**：控制台已经打印「已在浏览器打开（窗口已切到前台）」，
用户仍看不到页面。枚举窗口取证发现：机器上**唯一的匹配窗口是控制台自己** ——
启动脚本 `title Plant Virus Analysis Platform - Web mode (browser)` 的标题里
含 `Plant Virus Analysis Platform`，`_focus_platform_window` 按平台名匹配就
命中了它，于是「置前」把**控制台**提到前台并返回成功，浏览器窗口留在后面。

修法：
1. 匹配条件改成「平台名 **且** 浏览器名」（`Google Chrome` / `Microsoft Edge`
   / `Mozilla Firefox` / `Chromium` / `Brave` / `Opera` / `Vivaldi`），
   控制台标题不含浏览器名，天然排除；
2. 真正的置前用 `AttachThreadInput(当前线程, 前台窗口线程)` + `BringWindowToTop`
   + `SetForegroundWindow`，否则后台进程的 `SetForegroundWindow` 常被前台锁
   拒绝（只闪任务栏）。

实测：修复前枚举到的唯一匹配是 `hwnd=0x3D0242 pid=55092 title=Plant Virus
Analysis Platform - Web mode (browser)`（控制台）；修复后 `_focus_platform_window()`
命中 `功能树 - 植物病毒分析平台 - Google Chrome`

**补充（同日）：兜底不再写死 Edge。** 用户问"没有 Chrome 时是不是只能用 Edge"。
实际链路：`webbrowser`（系统默认浏览器，Chrome/Edge/Firefox/360/QQ/搜狗…都行）
→ 直接起默认浏览器新窗口（按家族选参：Chromium 系 `--new-window`、Firefox
`-new-window`、不认识的只给 URL）→ `startfile` → `cmd start` →
**独立配置窗口：默认浏览器 → Edge → Chrome → Firefox**（Chromium 用
`--user-data-dir`，Firefox 用 `-profile`，都不加入已运行实例）→ `explorer`。
窗口标题匹配同时补上国产浏览器后缀（360 / QQ浏览器 / 搜狗 / UC / 傲游 / 猎豹 / 2345）。
。冻结版启动后 8765 在监听、
Chrome 里存在平台窗口。`pytest tests -q` → 238 passed，两版换包
（exe md5 `979c68a9a66482bc5fbd41fd81631738`）。

## 2026-10-29（补八）：组装引擎 ↔ 组装模式联动（MEGAHIT 不再显示 SPAdes 模式）

用户指出：引擎选 MEGAHIT 时，右边「组装模式」还列着 rnaviral/metaviral/rna/meta/srna
——那些是 SPAdes 的参数，MEGAHIT 根本不认。

**改法**：

- 两套模式互斥，前端 `ASM_MODES` 统一维护：
  - SPAdes：rnaviral / metaviral / rna / meta / srna；
  - MEGAHIT：default（默认 k 21→141）/ sensitive（`--presets meta-sensitive`，
    min-count 1，低深度更敏感）/ large（`--presets meta-large`，k 27→127）。
- 切引擎时自动换成该引擎的默认模式（`asmSyncModes(true)`），页面加载时也同步一次；
- `auto` 的真实含义取决于机器上有没有 MEGAHIT：`page_tools` 注入
  `has_megahit` → 模板 `window.VP_HAS_MEGAHIT`，据此决定 auto 下显示哪套模式；
- MEGAHIT 下隐藏「小RNA (siRNA)」勾选框（它只对 SPAdes 有意义）；勾了小RNA
  会自动把引擎拨回 SPAdes；
- 后端 `run_megahit(preset=...)` → `build_megahit_args` 追加 `--presets`；
  `normalize_megahit_mode()` 把非法值（例如误传 rnaviral）收敛成 default；
- **一键分析（②→③→④）卡**同样加了引擎下拉并按同一套模式联动，`chains.py`
  把 `assembler` / `megahit_mode` 透传给它内部复用的那个组装 job —— 否则那张卡
  列着 SPAdes 模式、实际却按 auto 走了 MEGAHIT，模式被静默忽略；
- MEGAHIT 支持单端（`-r`），单端样品不再被前端/后端拦；SPAdes 的
  rnaviral/metaviral/rna/meta 仍要求双端；
- 顺带修一个隐患：`cfg.tool()` 缺工具时**抛 FileNotFoundError**，之前
  `me = cfg.tool('megahit')` 会让没装 MEGAHIT 的机器直接失败，而不是回退 SPAdes。

**验证**：
- `tests/_check_assemble_engine_ui.py`（13 条真浏览器）：auto 下只出 3 档
  MEGAHIT 模式且默认 default、SPAdes 下出 5 档且默认 rnaviral、小RNA 切回
  SPAdes、`asmParams()` 不混入对方模式、链式卡同样联动；
- `tests/test_assembly_engine.py` +2 条：`normalize_megahit_mode` 与
  `--presets` 映射（含非法值收敛）；
- 真实任务（单端 FASTA + assembler=megahit + megahit_mode=sensitive）：日志
  `MEGAHIT 组装 (mode=sensitive, …, 单端 -r)`，k 阶梯按 meta-sensitive 走，
  4 contigs；`final.contigs.fa` 落盘；
- `pytest tests -q` → 240 passed；i18n 1396 锚点全绿；JS 59/59。

**再补（同日，最终版）：取消"验证窗口"这一步，Chromium 系改用 `--app` 独立窗口。**

- 上一版用"窗口标题是否出现"判断打开成败是错的口径：Chrome 的窗口标题反映
  **当前活动标签**，新开的平台标签常常不是活动标签（窗口里可能是别的页面），
  于是每一步都被判"没看到窗口"，连开 5~6 个标签、最后还打印"未能自动打开"。
  浏览器其实一直在开，是我的判定错了。
- 现在：**打开调用返回成功即成功，第一次成功就停**；置前只做尽力而为，
  失败只提示"看任务栏/手动访问"，不再升级成失败。
- Chromium 系（Chrome / Edge / 360 / QQ / 搜狗 / UC / 傲游…）第一步就用
  `--app=<url>` 开**独立应用窗口**：标题就是页面标题，不会被塞进别的窗口，
  置前也能准确命中；非 Chromium（Firefox 等）仍走普通标签页。
- 置前匹配改成按**进程名**认浏览器（`chrome.exe`/`msedge.exe`/`firefox.exe`/…），
  不再依赖窗口标题里的浏览器后缀；同时移除了误加的 `Plant Virus Explorer`
  （那不是本平台页面的标题）。
- `_open_browser.log` 改写到第一个**可写**位置：`<平台>/run/` 或
  `%LOCALAPPDATA%\VirusPlatform/open_browser.log` —— 冻结版写 run/ 可能被拒，
  之前这条日志一直没落盘，正是它缺失导致我误判。

## 2026-10-29（补九）：浏览器打不开的最终结论（含两个我引入的回归）

用户追问"为什么之前可以、现在不行"。逐项查清后，结论是：**浏览器一直在开**
（页面轮询请求、Chrome 里的平台标签都能看到），打不开/看不到是我这两天三次
改动引入的：

1. **用窗口标题判定"开没开成"（回归 1）**：Chrome 的窗口标题反映**当前活动
   标签**，新开的平台标签常常不在前台（窗口里显示别的页面）→ 每一步都被判
   "没看到窗口" → 连开 5~6 个标签、最后打印"未能自动打开浏览器"。改动前
   只调用一次并报成功，所以从没出现这种现象。
2. **`--app` 用直接 `CreateProcess` 启动（回归 2）**：用 `--browser-selftest`
   在冻结版里做对照实验，直接 `Popen([chrome, '--app=...'])` 时 Chrome 立刻
   以 `2147483651 (0x80000003 STATUS_BREAKPOINT)` 退出（带
   `CREATE_NO_WINDOW`、断开 std 句柄、完全不带 flags 三种都失败），而
   `cmd /c start "" chrome --app=...` 能正常出窗口 → 改为后者
   （`_spawn_detached()`）。
3. **日志写不进去（放大问题）**：`run/_open_browser.log` 在冻结版里始终没
   落盘，`%LOCALAPPDATA%` 兜底也没写成，导致前两轮缺少证据。

**最终实现**（`app.py::_open_browser`）：
- 首选 `cmd /c start "" <chromium> --app=<url>` 开独立应用窗口（标题=页面标题，
  可被准确置前），启动后 3 秒内验证窗口；找不到就退下一条（最多两轮）；
- 其次 `webbrowser.open`（历史路径，开标签页），**返回成功即视为成功**，
  不再做窗口验证（否则又是误判）；
- 其余兜底：startfile → `cmd start <url>` → 独立配置窗口 → explorer；
- 置前按「进程名属于浏览器 + 标题含平台名」匹配，失败只提示看任务栏。

**排查边界（重要，写下来免得下次再绕）**：我用计划任务以真实用户身份复现时，
**任务本身把进程放进了一个作业对象**，Chrome 在该作业里一律自杀（0x80000003），
所以"计划任务里测浏览器"永远是失败的，不能用它判断浏览器逻辑。对照实验：
同一个上下文里由 .bat 直接 `start chrome --app=...` 能出窗口。
新增 `VirusPlatform.exe --browser-selftest` 诊断开关：打印冻结态、PLATFORM_ROOT、
默认浏览器/候选、`--app` 的四种启动方式结果，并演练一遍 `_open_browser`。

`pytest tests -q` → 240 passed；两版 exe md5 `fdaac5dc2449e4b607cacbaf249c062b`。

## 2026-10-29（补十）：组装内存自动检测 / 默认一半 / 超量告警 / OOM 提示

用户问：内存会自动检测吗、默认一半行不行、用满会不会卡、内存类报错能不能提示。

**改法**：

- `assembly.py` 新增 `physical_ram_gb()` / `default_assembler_memory_gb()`
  （= 物理内存的一半，下限 4GB）/ `assembler_memory_cap_gb()`（= 60%，下限 2GB），
  两处钳制统一走 `assembler_memory_cap_gb()`；
- 前端默认值由 `page_tools` 注入（`ram_gb` / `asm_mem_default` / `asm_mem_cap`，
  同时写进 `window.VP_RAM_GB` 等）：③ 组装卡与一键分析卡的空都改成
  「物理内存的一半」，`max` 设为物理内存；
- 输入框下方实时提示：`物理内存约 64GB，建议不超过 38GB（60%）`；超过上限变成
  「已超过平台上限 38GB（物理内存 60%），运行时会被自动钳制；再高会拖慢整机」；
- 提交前若内存 > 物理内存的 75%，弹确认（默认拒绝）；
- 后端：不传 `memory` 时用「物理内存的一半」（`_default_asm_mem()`；一键流程的
  `chains.py` 同口径），不再写死 64；
- 失败提示：`looks_like_oom()` 识别 `bad_alloc / out of memory / MemoryError /
  ERROR_NOT_ENOUGH_MEMORY / cannot allocate / 页面文件太小 / (1455)` 等标记，
  命中就把原始报错包装成：内存不足 + 建议改成一半 + 上限 60% + 附原始错误前 300 字。

**验证**：`tests/_check_asm_memory.py`（9 条：注入值/默认一半/max/提示文案/
超上限文案/确认框默认拒绝/默认值不弹确认/后端默认 32GB 实测日志）；
`pytest tests -q` → 240 passed。本机 64GB → 默认 32GB、上限 38GB。

## 2026-10-29（补十一）：左侧功能栏在中等宽度下不该覆盖内容

**现象**：功能栏位置设为「左侧」时，点开/展开后侧栏盖住了总览内容。

**原因**：`app.css` 里 `@media (max-width: 1024px)` 把左侧栏整体当成**抽屉**
（`translateX(-100%)` 默认收起，点开浮在内容上 + 半透明遮罩）。窗口小于
1024 CSS px（例如未最大化、或 150% 缩放下 1536px 的屏）就会命中这条，
于是"打开侧栏"= 覆盖页面。

**改法**：抽屉只在真正窄的窗口出现——
- 新增 `@media (min-width: 821px) and (max-width: 1024px)`：侧栏**常驻**但收窄到
  `--sidenav-w: 204px`，`body` 左内边距同步 204px，内容不被覆盖；
- 抽屉断点从 1024 降到 820（含顶部形态那条 `html[data-nav="top"]` 的覆盖同步改，
  否则 821~1024 之间顶部形态会被 `translateX(-100%)` 移出视口）。

**验证**（`tests/_it_nav_responsive.py` 已按新口径加断言并全绿）：
- 1600/1440/1200：侧栏 236 常驻，内容左边界 > 侧栏右边界（不重叠）；
- 1000/900：侧栏 204 常驻、`body` padding 204、`pageLeft(224) > navRight(204)`；
- 820/768/600：抽屉默认收起、☰ 可见、点开有遮罩、点遮罩收起、主区全宽；
- 冻结版实测：`?v=1793263151`，服务出的 `app.css` 含 `max-width: 820px` 与
  `--sidenav-w: 204px`（资源版本号按 static 目录 mtime 计算，替换文件即自动破缓存）。

## 2026-10-29（补十二）：浏览器打不开的最终修法——让启动脚本去开

前面几版（补七/补九）都在平台进程里折腾"怎么把浏览器拉起来"，全部失败。
最后一轮对照实验把机制钉死了：

| 做法 | 结果 |
|---|---|
| `Popen([chrome, '--app=…'])`（直接 CreateProcess） | Chrome 秒退 `0x80000003`，无窗口 |
| `cmd /c start "" URL` **带** `CREATE_NO_WINDOW` | 页面加载了，**窗口不出现** |
| `cmd /c start "" URL` **不带** flags | 页面加载 + **窗口出现并置前** |
| 由 .bat 里的 `start "" URL`（有控制台的 cmd）发起 | 同上，稳定 |

结论：**窗口能不能到前台，取决于发起进程有没有控制台 / 有没有被作业对象包住**。
`Start-Web.bat` 在 Windows Terminal 里跑时进程被放进 ConPTY 的作业对象，平台
再拉浏览器就永远出不来；而"有控制台的 cmd 执行 start"这条路一直有效。

**改法（不再依赖平台进程）**：
- 新增版本根目录下的 `open-browser.bat`：等端口就绪（netstat 轮询 8765/8766/
  8900/8989…）后用 `start "" <url>` 打开页面；
- `Start-Web.bat` 在启动 exe 前先 `start "" /min open-browser.bat`，并给 exe 加
  `--no-open`（平台不再自己开浏览器，只打印地址）；
- `app.py` 支持 `--no-open`；`dev_tools/package.py` 的启动模板同步生成
  `open-browser.bat`，以后重打包不会丢。

**验证**（我这边的 shell 同样带作业限制，等价 Windows Terminal 场景）：
`cmd /c Start-Web.bat` → **第 4 秒弹出 `植物病毒分析平台 - Google Chrome` 窗口**；
`pytest tests -q` → 240 passed；两版 exe md5 `630597679c206025cdaa30c2d14233bb`。

## 2026-10-29（补十三）：拖拽上传失败——服务端没有可用临时目录

**现象**：把 FASTQ 拖到输入框，弹出「上传失败 / Error: [Errno 2] No usable
temporary directory found in [...]」。

**根因**：不是拖拽没实现，而是 Werkzeug 解析 multipart 时先把上传体 spool 到
**临时文件**；平台进程的 `tempfile.tempdir` 为空、系统 TEMP 又全不可写（这台机器
上冻结进程访问 %LOCALAPPDATA%\Temp 被拒），于是 spool 阶段就抛错，500 原样透传
到前端。引导钩子虽然钉过一次临时目录，但那份探测在某些环境没生效。

**改法**：
1. `config.py` 增加 `ensure_usable_tempdir()`，**导入 config 时**就校准一次：
   依次试 `<平台>/run/tmp` → `<平台>/tmp` → `%LOCALAPPDATA%/VirusPlatform/tmp`
   → `%TEMP%/VirusPlatform` → 平台根，用「建目录再删」验真，第一个通的写进
   `tempfile.tempdir` 与 TMPDIR/TEMP/TMP。几乎所有入口都会 import config，
   所以这一道覆盖源码/冻结两种模式。
2. `web/pages.py` 的 400 处理器识别 `No usable temporary directory`，换成
   「服务器临时目录不可用：系统 TEMP 与 <程序目录>\run\tmp 都写不进去…」的
   中文说明，不再只丢英文原文。

**验证**：冻结版 `POST /api/upload`（模拟拖拽的 multipart）——修复前直接报
`No usable temporary directory`；修复后该错误消失（我这边的沙箱进程随后卡在
`run\uploads` 的写入权限上，那是沙箱令牌限制，正常账号不受影响）。
`pytest tests -q` → 240 passed；两版 exe md5 `09759aac694d855e6301a0666d66a465`。

## 2026-10-29（补十四）：顶部功能栏折行时，分组下拉压住「总览」

**现象**（用户两次截图）：顶部形态下点开「数据资源」，下拉浮层把「总览」盖住。

**根因**：功能栏在 1280~1600px 会**折成两行**（实测栏高 82px），而 `.sn-sub`
是按「单个分组」绝对定位的（`top: calc(100% + 6px)`），于是落在 y=46 ——
正好压在第二行的一级项上（总览 / 结果中心 / 设置 / MCP 控制台 / 手册）。
1920px 单行时不明显，所以之前在 1600/1280 的用例只测了"不撑高功能栏"，
没测"不压一级项"。

**改法**（`app-nav.js`）：
- 新增 `positionTopSub(group)`：展开时按整条功能栏的 `bottom + 6` 反算
  `.sn-sub` 的 `top`（相对分组的偏移），单行时结果与原来一致（51px），
  折行时自动落到栏下方（88px）；
- `resize`（沿用已有 80ms debounce）与 `vp:i18n` 事件后重新定位已展开的下拉。

**验证**：
- 实测 1600/1440/1280：栏高 82、下拉 top=88、与「总览」(y 5~40) **无交集**；
  1920：栏高 45、下拉 top=51、同样无交集；
- `tests/_it_nav_responsive.py` 增两条断言（`浮层挂在整条功能栏下方 6px`、
  `浮层不压一级导航项`，后者把 `.sn-foot` 的四个底部入口也算进来），全绿；
- 只改静态 JS，直接替换两版 `_internal/webapp/static/app-nav.js`，`?v=` 自动
  变化，F5 即可（md5 `00445b0fc39ae39b041563bbeb210456`）。

## 2026-10-29（补十五）：挂载数据目录.bat 为什么失效 + 重写

**现象**：双击 `挂载数据目录.bat` 直接报「未找到分发版平台」。

**两个原因**：
1. 脚本把路径写死成 `%~dp0..\PVAP\VirusPlatform`（= `D:\桌面\PVAP\VirusPlatform`），
   而分发版现在在 `D:\PVAP\PlantVirusPlatform\VirusPlatform`；源码平台的
   `run\fastq` 也可能还没建。
2. 更深一层：脚本里 `chcp 65001` 之后又用中文 `echo`，cmd 会按"打开文件时的
   代码页"逐行读批处理，遇到多字节行会**从中间切断**——实测最后一行被拆成
   `'…制、不占额外空间）。' is not recognized`。这和其它启动脚本坚持纯
   ASCII 是同一个原因（`Start-Web.bat` 早年就踩过）。

**重写**（源码根 + 两个分发根各一份，自定位）：
- 程序目录候选：`<此处>\VirusPlatform.exe`（所在目录就是程序目录）→
  `<此处>\VirusPlatform\`（版本根）→ 旧布局 `..\PVAP\VirusPlatform` →
  `..\PVAP\PlantVirusPlatform\VirusPlatform` → 扫 C~G 盘的
  `\PVAP\PlantVirusPlatform\VirusPlatform`；都没有就**让你粘贴路径**；
- 数据源：`<此处>\run\fastq`，没有就让你粘贴（例如 `D:\seq\fastq`）；
- **全文 ASCII**，不再被代码页切断；
- 已存在时区分「是指向 X 的联接（给 rd 换挂载的提示）」与「普通目录别动」；
- 联接判定调 `%SystemRoot%\System32\find.exe`——直接写 `find` 在装了 Git Bash
  的机器上会被 GNU find 抢走（实测报了 `find: "<JUNCTION>": No such file`）；
- `mklink` 的原始报错不再丢进 `>nul`，失败时直接显示。

**验证**：造一个 `run\fastq` 测试目录 → 双击（源码根那份）→ 输出
`Mounted: ...\run\fastq -> ...\run\fastq`，PowerShell 查
`Attributes=Directory, ReparsePoint`、`Target` 正确、透过联接能列到文件；
随后删除测试联接与测试文件，恢复原状。

## 2026-10-29（补十六）：挂载数据目录改成「一次配置，启动自动」

用户问：还要不要单独跑 `挂载数据目录.bat`。改成两层：

- **交互层**（`挂载数据目录.bat`，仍需手动跑一次）：选分发版目录与数据目录 →
  建 junction → **把数据目录记进版本根目录的 `mount_source.txt`**；
- **自动层**（新增 `mount-data.bat`，纯非交互）：`Start-Web.bat` / `Start-Desktop.bat`
  在 `cd /d "%PRG%"` 之后 `call "%ROOT%mount-data.bat"`；它读 `mount_source.txt`，
  只在这三种情况下静默退出：没配置 / 源目录不存在 / junction 已存在（或是普通
  目录，绝不覆盖）。确实需要重建时才 `mklink /J`，完成后打一行 `[mount] … -> …`。
  启动流程永远不会被它卡住或打断。

其它：
- `dev_tools/package.py` 的启动模板同步生成 `mount-data.bat` 并在两个 Start
  模板里插入调用（模板用 raw 字符串，避免 `\V` 这类无效转义告警）；
- 端到端实测：`mount_source.txt` 记 `D:\PVAP\_mnt_src` → 删掉 junction →
  跑 `mount-data.bat` → 输出 `[mount] …\run\fastq -> D:\PVAP\_mnt_src`，
  PowerShell 确认 `ReparsePoint`、target 正确、透过联接能列到文件；随后清理
  测试联接与测试目录。


## 2026-10-29（补十七）：左侧栏展开分组时「总览」被压成一条、下面几项看不到

**现象**（用户两张截图）：左侧功能栏展开「数据资源」后，顶部「总览」的高亮条
只剩几像素高的一截，其下各分组标题也看不到（折叠态 720p 一进页面就少几项）。

**实测**（selenium 真浏览器，四个窗口尺寸）：展开后 `.navlinks.sn-links`
可视高度 483/383/303/283，而内容 575 —— `scrollTop` 始终是 0；`总览` 的
盒子高度从 38px 掉到 2px，其余项保持原高、照旧溢出被裁。1280×720 下即使
**不展开**，折叠内容 366 也大于可视区 303。

**两层根因**：
1. `.navlinks.sn-links` 是 `display:flex; flex-direction:column`，而
   `.navlinks a` 为了省略号写了 `overflow:hidden` —— 这会让 flex 子项的
   `min-height:auto` 退化成 0。内容超出容器时唯一可压缩的直接子项「总览」
   被整条吸成残条，而不是让容器滚动。`tests/_it_nav_responsive.py` 原有的
   `squeezed` 探针测不出来：它比较"盒子高 vs 内容高"，被压扁时两者同为 2px。
2. `.sidenav` 自己也是 `overflow-y:auto`，与内层 `.sn-links` 形成双滚动容器，
   整栏可被卷走（品牌头卷出视口、首项切在边沿），外观更像"被盖住"。

**改法**：
- CSS：`.navlinks.sn-links > * { flex: 0 0 auto; }` 禁止收缩；
  `.sidenav` 改 `overflow: hidden`（滚动只留给内层链接列；顶部形态自己
  覆盖成 `overflow: visible`，不受影响）。
- 矮屏收紧（`@media (min-width:821px) and (max-height:800px)`）：品牌头
  61→38px、底部区 205→173px、行高 38→32px。720p 折叠态 11 项从「必滚」
  变成正好放得下（实测四档 `scrollHeight == clientHeight`）。
- JS（`app-nav.js`）：新增 `revealGroup()` / `revealCurrent()` / `syncMore()`。展开分组后
  把该组滚进 `.sn-links` 可视区（组比可视区高时以组头贴顶）；进页面时把
  当前高亮项（或服务端标记的当前分组）带进可视区。只在这两个时机滚，
  不接管用户自己的滚轮。

**验证**：
- 四档实测：每行高度 32px（原 2px）、折叠态 `scrollHeight == clientHeight`；
  1100×560 展开最后一个分组后 `scrollTop=74`、子项完整在可视区。
- `tests/_it_nav_responsive.py`：NAV_OVERLAP 增加行高下限 `thin` 断言
  （<24px 判失败，专抓 flex 压缩），新增 `check_reveal()` 在
  1280×600 / 1100×560 下展开最后一个分组，断言子项自动可见、确实发生滚动、
  组标题仍在可视区、首项未被压扁；四个页面全绿。
- `pytest tests -q`：240 passed（132.9s）。
- 静态资源已同步两版 `_internal/webapp/static/`：
  `app.css` sha256[:16]=0166c29d75f00c5e、`app-nav.js` = d2ce4239063e7721；
  `?v=` 由静态文件最大 mtime 生成，F5 即生效，无需重建 exe。


## 2026-10-29（补十八）：启动后留下一个空的黑窗口（cmd /K 没关）

**现象**（用户截图）：双击 `Start-Web.bat` 后，桌面上多出一个标题为「命令提示符」、
停在 `D:\PVAP\PlantVirusPlatform\VirusPlatform>` 的空白控制台。平台本身是好的
（`VirusPlatform.exe --web --no-open` 在听 8765）。

**根因**：`Start-Web.bat` 里写的是 `start "" /min "%ROOT%open-browser.bat"`。
`start` 执行 `.bat` 时走的是 **`cmd /K`**（跑完保留窗口），不是 `/C`。helper 脚本
自己 `exit /b 0` 只退出批处理，`/K` 的命令解释器还在，于是留下一个空提示符。
实测进程：`cmd.exe /K "D:\PVAP\PlantVirusPlatform\open-browser.bat"`（PID 9204）。

**改法**：
- `Start-Web.bat`：改为 `start "" /min cmd /c ""%ROOT%open-browser.bat""`，
  helper 跑完窗口即关；即使用旧写法启动，helper 末尾也改成 `exit`（不是
  `exit /b`）——`exit` 会结束整个 `cmd /K`。
- `open-browser.bat` 重写：
  - 端口来源优先读平台落盘的 `run\.port` / `_internal
un\.port`
    （覆盖非默认/随机端口），没有再退回 8765/8766/… 候选表；
  - 轮询上限 40s → **180s**（首次启动要扫库，40s 内起不来就永远不开页，
    正是之前"没有浏览器页面"的老毛病）；
  - 每次打开都写一行 `run\_browser_open.log`（`opened http://127.0.0.1:PORT`），
    超时也写一行，下次排查不用猜；
  - 全文 ASCII + CRLF（cmd 会按打开时的代码页切多字节行）。

**连带修掉**（`dev_tools/package.py`）：
- 启动脚本模板与两版实际文件早已不一致（实际文件里有 `%ROOT%`/`%PRG%` 与
  清理循环，模板里没有）。现在模板就是实发内容，下次 `python dev_tools/package.py`
  生成的脚本与这两版一致。
- `_LAUNCH_MOUNT` 的注释里有中文，而 `_write_launchers` 用 `encode("ascii")`
  落盘 —— 下次打包会在写 `mount-data.bat` 时直接 UnicodeEncodeError。已改成
  纯 ASCII。
- ⚠ `dev_tools/package.py` **没有 `__main__` 保护**，`import` 它会真的开始
  一次 PyInstaller 构建（本次实测：import 后立刻出现 `Building Analysis`，
  在仓库根留下 `build/VirusPlatform/base_library.zip`… 已删除）。要复用其中的
  模板，用 `ast` 读常量，不要 import。

**验证**：
- 用与启动脚本完全相同的命令跑 helper：
  `cmd /c start "" /min cmd /c "…\open-browser.bat"` → 4 秒后
  `run/_browser_open.log` 出现 `[2026-10-29 21:07:01.36] opened http://127.0.0.1:8765`，
  `Get-CimInstance` 查不到任何 `open-browser` 的 cmd 进程（窗口已关闭）；
- 两版四个启动脚本逐字节一致（1906 / 1222 / 1940 / 1923 B，全 ASCII）；
  改动前原件备份在 `%TEMP%\vp_launchers_20261029-210605\`；
- exe 未重新打包（两版 `VirusPlatform.exe` mtime 仍是 17:13），本次只改启动脚本。


## 2026-10-29（补十九）：把 wiki/ 的模块文档放进「组概览」

用户裁定：组概览太空，把 `wiki/` 下按模块写的文档直接放进去（不做第二套手册）。

**内容与映射**
- `wiki/*.md`（28 篇）复制为 `webapp/static/wiki/*.md`：浏览器直接 fetch，静态目录
  在 onedir 分发版里是外部数据文件，改文档不用重打 exe。`_Sidebar.md` 是 GitHub
  wiki 的目录条，不是正文，跳过 —— 实际 27 篇。
- `webapp/static/wiki/index.json`：`groups`（每组 1~3 篇）+ `global`（8 篇跨组
  通用文档：Home / Quick-Start / Pipeline-Overview / Architecture / AI-Assistant /
  CLI-Reference / Settings-Ops / FAQ）。27 篇恰好每篇一次，无重复。
- 映射：数据资源=Public-Data+Databases；样本处理=Preprocess+Toolbox；定量=Kvsuite；
  识别分类=Assembly+Host-Prediction；注释=ORF+Genome-Plots；检测=Primer+
  CRISPR-Design；比较基因组=Phylo；进化动力学=Phylodynamics+Phylogeography+BEAST；
  结果中心=Result-Interpretation+Report；溯源与提交=Logan+NCBI-Submission。

**页面**
- 组概览卡片下方新增 `#groupDocs`（`tools.html`），由新脚本 `app-wiki.js` 填充；
  组内文档默认全展开，「平台通用文档」折叠、首次展开才 fetch。
- Markdown 渲染器从 `help.html` 内联代码抽成 `webapp/static/app-md.js`
  （`window.VPMD.render`），`/help` 与组概览共用同一套语法规则。抽出前后 `/help`
  的 `#md` 文本 12090 字、text sha `797a228d7d861d91`、html sha
  `b37f59f5abc9c97c` **完全一致**（真浏览器比对）。
- 两处清理：① 每篇开头那行 `> 返回 Home ｜ 上一页 ｜ 下一页` 是 wiki 翻页条，
  在应用里是死链，渲染前去掉；② 正文里 156 个 `[模块](Slug)` 互链改写成组内
  锚点 `#gd-Slug` 或跨组 `/tools?g=<组>#gd-Slug`，未知 slug 原样交给渲染器
  （不再出现 `href="#"` 的假链接）。跨组跳转后文档是异步渲染的，`app-wiki.js`
  在渲染完成后按 `location.hash` 再滚一次。
- 深色主题下容器/摘要配色已补（`html[data-theme="dark"] .gd-sec` 等）。

**坑**
- `index()` 里 `fetch(...).then(JSON.parse)` 改成 `then(function (txt) { ... })` 时
  漏了 `JSON.parse`，`idx` 变成字符串 → `idx.groups` undefined → 静默走"没有文档"
  分支，页面只剩卡片、控制台无报错。已修，并用 `VPWiki._index()` 直接断言返回对象。

**维护与验证**
- 改 `wiki/*.md` 后跑 `python dev_tools/sync_wiki.py`；`tests/test_wiki_docs.py`
  校验静态副本逐字节一致、每篇恰好映射一次、每个导航组都有映射。
- `pytest tests -q`：245 passed（新增 5 项）。
- 冻结版实测（另起实例，端口 8900）：`/tools?g=phylodyn` 渲染 3 篇、6713 字符、
  翻页条已去掉；两版 `_internal/webapp` 与源码同哈希
  （`app-wiki.js=9d620abefb0a3d3b`、`tools.html=88318d043cfc1c27`）。exe 未重打包；
  注意运行中的实例会缓存 Jinja 模板，**重启后才看到组概览的新区块**。


## 2026-10-29（补二十）：样品创建 / 批量导入做到总览页

用户追问「样品创建 / 批量导入放到总览，上次我们提的，要做什么，你做了嘛」。
上次（2026-10-28）只把**入口按钮**提到首屏，点了仍然跳到 `/samples`，创建表单
并不在总览 —— 这次把功能本体搬过去。

**做法：一份表单，两个页面共用**
- 从 `samples.html` 抽出创建/批量导入的标记 → `webapp/templates/_samples_new.html`；
  逻辑（val、子采样开关、scScan/scScanRender/scScanCount/scScanAll/scScanCreate/
  scCreate）→ `webapp/static/app-samples.js`。`/samples` 与总览都 include/加载
  同一份，避免两套表单和校验逻辑漂移。
- 创建完成后的刷新抽成 `scAfterCreate()`：`/samples` 刷新样品表，总览刷新
  「最近样品」`loadHome()`（按页面元素存在与否分派）。
- `home.html`：封面下新增 `<details id="homeNewSamplePanel">`（标题 + 一句说明 +
  「管理样品 ›」链接），默认折叠；hero 的「➕ 新建样品 / 批量导入」按钮改为
  展开该面板并聚焦第一个输入框（无 JS 时仍是 `/samples` 链接）。**没有样品时
  自动展开**（/api/samples 返回空且文案是「尚无样品」），首次使用直接看到表单。
- `home.html` 补 `{% include "_browse_dlg.html" %}`：面板里的 📂/📁 按钮依赖
  `#dlgMask/#dlgPath/#dlgList/#dlgPickDir`，漏了就是"点了没反应"（该片段注释里
  记着 settings/results/submit 三页的历史事故）。
- 新增 i18n：`hm.newSamplePanel` / `hm.newSamplePanelHint`（中英）。

**验证**
- 主站实测：总览面板有 scanDir/scanProject/sample/r1/r2/s_project/subsample 等
  全部输入与 6 个按钮；hero 点击后 `panel.open=true`；用临时目录
  (`DemoA_R1.fastq.gz` + `DemoA_R2.fastq.gz`) 扫描 → 预览 1 行、类型 PE、
  「已选 1 / 1 个样品」、无报错；`/samples` 表单与列表（10 个样品）仍正常。
- 冻结版实测（另起实例，8900）：面板存在、`open=true`（该实例样品表为空，
  自动展开生效）、扫描同样得到 DemoA。
- `pytest tests -q`：245 passed；`tests/_check_home_dashboard.py` 24 条全绿。
- 两版 `_internal/webapp` 已同步（home.html f4447de1dac3、
  _samples_new.html e7cbd5baac98、app-samples.js da4d8d0eeb54），exe 未重打包；
  运行中的实例缓存了 Jinja 模板，**重启后**可见。


## 2026-10-29（补二十一）：总览「AI 分析台」+ 宿主库构建动作 + 多步计划

用户要求三件事一起补：①总览缺一个能下命令的 AI 对话框（指定数据/样品 → 直接
建样品、组装、建库）；②「数据库构建」特指**宿主基因组数据库**；③要能多步编排。
并问：AI 是否自动计入 MCP 与 skills。

**① 总览 AI 分析台（前端，复用现有引擎）**
- 不复制聊天引擎：`_nav.html` 注入的 `#aiDock` 在总览被搬进
  `home.html#aiConsoleHost`，加 `.ai-inline` 改成文档流（`position: static`、
  1214×520），隐藏 FAB 与坞的 ✕；其它页面仍是右下角浮坞（`app-ai.js` 不动）。
- 输入框、样品/模型选择器、快捷指令、执行单卡片全部沿用；一句话即可提案
  建样品/组装/建库，批准后走与界面按钮相同的执行路径。
- `tests/_check_ai_dock_ui.py` 改为双场景：总览断言「内嵌/常开/FAB 隐藏」，
  `/samples` 断言「初始隐藏 → FAB 展开 → ✕ 收起」。

**② build_host_db 动作（宿主基因组数据库）**
- `Virus_Platform_Core/web/build.py` 抽出 `start_host_db_build(body)`：
  校验（genome 存在、taxid 数字、输出目录在允许根内）+ `tm.start`，
  `/api/build_host_db` 与 AI 执行单共用同一条路径（路由只负责把
  ValueError 转 400）。
- `executors.ACTIONS['build_host_db']`（risk=confirm），参数
  `genome / taxid / out_dir / threads / hash_capacity / rebuild / clean_mid`；
  提案阶段就挡缺参、文件不存在、taxid 非数字。
- `tools.py` 的 `propose_action` 枚举与说明同步加入。

**③ 多步计划 plan**
- `ACTIONS['plan']`：`params.steps` 1~6 步，允许
  rerun_stage / retry_failed / run_tool / create_sample / build_host_db；
  **不允许 download_runs**（它归下载管理器管，没有任务中心 tid 可等）。
- 执行：批准后逐步骤 `_dispatch` → `_wait_task`（`tm.snapshot` 轮询，2s 一次，
  上限 6h）→ done 才进下一步；任何一步 failed/cancelled/超时立即停止，
  `error` 写明「第 i/n 步（动作）」。
- 事件与卡片：`_snapshot` 增加 steps/step/step_total/step_action/step_title；
  `app-ai.js` 的执行单卡片按 aid **原地更新**（旧逻辑要求旧卡仍是 pending，
  计划每步上报 running 会刷出一串重复卡片），并在标题下列出步骤清单；
  状态显示「步骤 2/3 · 运行工具箱工具」。

**MCP / skills 的结论**
- MCP：**已经自动计入**。对话模型可调 `mcp_tool_catalog` 列全部 vp_* 工具、
  `mcp_call` 执行；破坏面 `vp_delete_tool_run` 被硬编码挡掉。
- skills：平台没有这一层（全仓 grep 无 skill 注册/加载）；等价能力是
  「工具箱 32 个工具」+「插件包」，都已通过 `list_platform_tools` /
  `run_tool` 暴露给 AI。

**验证**
- `pytest tests -q`：257 passed（新增 `tests/test_ai_actions.py` 12 项：
  动作注册、build_host_db 参数校验、plan 步骤校验/顺序执行/失败即停/启动异常）。
- `tests/_it_ai_actions.py` 三级执行权全绿；`_check_ai_global_ui.py` 全绿；
  `_check_ai_dock_ui.py` 改造后 11 项全绿。
- 浏览器实测执行单卡片：pending 显示步骤清单与批准/驳回；running 原地更新为
  「步骤 2/3」且只有 1 张卡；failed 错误只追加一次。
- 后端改动需要重打 exe：已用 `PyInstaller dev_tools/VirusPlatform.spec` 构建到
  `D:/PVAP/_stage` 暂存，验证后再替换两版（见构建日志 `D:/PVAP/_build.log`）。


**落地与实测（同日）**
- 用 `PyInstaller dev_tools/VirusPlatform.spec` 构建到 `D:/PVAP/_stage`（2.4 分钟，
  产物 exe 56.7MB / _internal 128 项），先在暂存目录起实例验证：`/`、`/api/selfcheck`、
  `/api/dbs`、`/api/ai/actions` 全部 200，总览分析台 `position=static`、FAB 隐藏。
- 停掉旧实例 → 两版 `VirusPlatform.exe` 与 `_internal` 备份后整体替换 →
  `Start-Web.bat` 重启 → 8765 起来；验证失败会自动回滚（脚本内置）。
- **发现并修掉一个部署缺口**：两版 `platform.json` 里 `ai.enabled=false`、
  `base_url/model/api_key` 全空（AI 从未在分发版配置过，分析台会显示“未配置”）。
  已把源码平台 `platform.json` 的 ai 段与 `run/ai_local.json` 的 key 同步到两版
  （原文件备份为 `platform.json.bak_*`，验证后删除）。
- 冻结版实测新动作：让模型起草 `build_host_db` → `act_1` 带
  `{genome: …/example_acmv.fasta, taxid: 3702}`；起草 `plan` → `act_2` 两步
  `['create_sample','run_tool']`；两条都 reject 掉，未执行。AI 对话实测 78 个
  delta、回复正常。


## 2026-10-29（补二十二）：总览版面回调——样品只留入口，AI 分析台移到数据库模块下面

用户定案：①「新建样品 / 批量导入」内嵌在总览太占地方，只保留封面入口；
②AI 分析台放到「数据库模块」下面。

- `home.html` 删掉 `#homeNewSamplePanel`（含 `_samples_new.html` 的 include）、
  页内的 `_browse_dlg.html` 与 `app-samples.js`；封面按钮恢复成普通链接
  → `/samples`，不再拦截点击/自动展开。`_samples_new.html` + `app-samples.js`
  仍是 `/samples` 的实现，未改动。
- AI 分析台整段移到 `#dbCards` 之后、`#favSection` 之前；内嵌方式不变
  （`#aiDock` 移入 `#aiConsoleHost`、`position: static`、FAB 隐藏）。
- 清掉随之无用的 `.home-newsample` CSS（943B）与 `hm.newSamplePanel*` 两条
  i18n 键（中英各一）。
- 验证（源码 + 冻结版）：页面顺序 = hero → 运行中任务 → 数据库模块 →
  AI 分析台 → 常用工具 → 最近样品；`#homeNewSamplePanel`/`#scanDir` 不存在；
  封面按钮 href=/samples；分析台 `position=static`、无控制台错误。
  两版 `_internal/webapp` 已同步并重启。


## 2026-10-30：数据库模块并入封面（同底色）

用户要求：把「数据库模块」放进封面那块深绿区域，卡片用同样的背景色。

- `home.html`：`#dbCards` 与它的分区标题整体移进 `.hero.cover`，外面包一层
  `.cover-db`（上边线与封面口径行同色，做分区）。DOM 契约不变——数据仍由
  `app.js::loadHome()` 填 `#dbCards`，`_check_home_dashboard.py` 原样通过。
- `app.css`：新增 `.cover-db` 段。卡片不再用白底，改成同色系半透明
  `rgba(255,255,255,.07)`（悬停 .12 + 浅绿描边），文字用 `#f2fbf6` /
  `#b9d8c8`；`st-ok` / `st-no` 药丸改成深底浅字。分区标题的竖条与分隔线
  也换成浅色，避免在深绿上看不见。
  ⚠ 颜色选择器必须写到 `.cover-db .module-card .mc-desc`：基础规则
  `.module-card .mc-desc` 与本段同优先级时后者靠"定义更晚"胜出，只写
  `.cover-db .mc-desc` 会被压回灰色（实测 rgb(123,138,153)）。
- 布局顺序变为：封面（标题 + 口径行 + 按钮 + **数据库模块**）→ 运行中任务 →
  AI 分析台 → 常用工具 → 最近样品。
- 验证：1400px 下封面高 524px、4 卡一行；900px 下自动换两行、封面 759px，
  两种宽度都无横向溢出；冻结版重启后同样生效。`pytest tests -q` 257 passed，
  `_check_home_dashboard.py` 全绿。


## 2026-10-30（补二）：总览收敛为 AI 工作台 + 附件/工作空间/文件预览

用户定案：数据库模块不在总览展示；封面的两行说明与三个行动按钮全部去掉；
把空间让给 AI 工作台。同时要求输入区支持"加文件 / 加工作目录"，文件可直接
粘贴，选了目录后右侧浏览目录内容（参考 OmicoOS 的三栏布局）。

**版面**
- `home.html`：封面只留标题 + 一句定位（高 99px）；删除 `hm.heroSub` /
  `hm.heroEng` 两行、三个 `.cover-act` 按钮、整个 `#dbCards` 区块。
- AI 工作台两栏：左对话（坞内嵌，高度 520→640px），右「工作区文件」320px；
  ≤1100px 自动上下堆叠。
- `app.js`：`loadHome()` 的触发条件从 `#dbCards` 改为 `#dbCards || #homeSamples`
  （否则移除库卡后"最近样品"永远停在"加载中…"）；库卡渲染改为存在才写，
  避免每页一条 TypeError。`_check_home_dashboard.py` 同步改为"库卡存在才检查"，
  并新增"总览是 AI 工作台""封面不再放行动按钮"两条。

**附件（＋ / 粘贴 / 拖入）**
- `＋` 走全站文件对话框（app-browse.js 选中后派发 `vp:filepicked`）。
- 输入框粘贴：整段是 Windows 路径（含"复制为路径"的引号、file:///）→ 直接
  变附件；剪贴板里是文件 → 走 `/api/upload` 落到 `run/uploads/` 再附加。
- 拖文件进对话坞同样上传附加（`.ai-drop` 虚线高亮）。
- 发送时 `attachments: [绝对路径]` 随 `/api/ai/chat` 提交；`chat.py` 把它们
  整理成 `context.attachments`（名字/大小/文本前 2000 字）注入系统提示，模型
  可直接在 `run_tool` 等动作里使用这些路径。上限 8 个、文本预览 8MB 内。

**工作空间与文件面板**
- 输入框下新增「📁 选择工作空间」（仅内嵌工作台显示）：选目录后记入
  `localStorage.vp_ai_ws`，右侧面板插一个「工作空间」根并切过去，下次进总览沿用。
- 右侧面板：根目录由 `/api/fs/roots` 给出（当前样品 / 工具运行 / 示例数据 /
  下载数据），示例用 `_detect_examples_root()` 兼容三分离分发；列表走既有
  `/api/browse?all=1`；点文件弹预览浮层：文本 `<pre>`、TSV/CSV 表格、图片
  `<img>`、HTML `<iframe>`，其它给"打开原文件"。
- 新增只读接口 `/api/fs/read`（文本片段，>8MB 拒绝，最多 1MB 返回）与
  `/api/fs/raw`（原样字节）；可读根 = 平台根 + results + tool_runs + downloads
  + 示例根，越界 400。**不提供写/删/上传**（上传只走既有的 `/api/upload`）。

**验证**
- `pytest tests -q`：264 passed（新增 `tests/test_fs_preview.py` 7 项：
  文本读取、越界拒绝、目录拒绝、raw、roots、附件摘要与上限）。
- 浏览器实测：封面 99px、无按钮、无库卡；坞 640px；粘贴路径 → 1 个附件芯片；
  粘贴文件 → 上传为 `run/uploads/Paste_R1.fastq` 并附加；`AI.setWorkspace()`
  → 面板根切到该目录并列出内容；`/api/fs/roots` 返回 4 个绝对路径。
- 冻结版（重打 exe 后）实测同样通过；`_check_ai_dock_ui.py` 11 项全绿，
  `_check_home_dashboard.py` 全绿。


## 2026-10-30（补三）：工作台面板高度跟随视口

承接"给 AI 工作台足够空间"：上一轮只删了封面元素，但两栏面板仍是固定
640px —— 高分屏上删掉的空间并没有真正还给工作台。改成
`.ai-console { --wb-h: clamp(560px, calc(100vh - 330px), 900px) }`，
对话坞与文件面板共用 `var(--wb-h)`。实测（冻结版，纯 CSS 无需重启）：
1100 高 → 770px；900 → 570；720 → 560（下限）。两版已同步。


## 2026-10-30（补四）：去掉坞里的样品下拉、改名「AI 工作台」、设置卡重排

**① 坞内去掉「（不指定样品）」下拉**（用户要求）
- `_nav.html` 删除 `#aiSampleSel`，只留模型选择器。
- `app-ai.js`：`ensureCtx()` 不再拉样品清单、不再默认选"完成度最高"的样品；
  `AI.sample` 改为跟随顶栏全局样品胶囊（`VP_CTX.sample`），并监听 `vp-ctx`
  事件 —— 顶栏换样品后下一轮对话自动带上，不再有两处状态打架。
- 删除 `AI.onSampleChange` / `_selFilled`。

**② 「AI 助手」→「AI 工作台」**
- i18n：`ai.dock.title/open/notice/greeting`、`ai.se.title/enable`、
  `se.nav.ai`、`ui.qs.noModelHint` 中英同步改名；`_nav.html` 的 FAB
  title 兜底文案同步。设置页侧栏条目现在显示「AI 工作台」。
- `tests/_check_ai_dock_ui.py`：原「样品下拉已填充」断言改为
  「坞内不再有独立样品下拉」+「标题已改为 AI 工作台」。

**③ 设置页 AI 卡片重排（参考 ZCode 模型设置页）**
- 两栏：左侧「供应商档案」竖排卡片列表（圆点=激活、模型名、删除），
  右侧「连接 / 生成 / 主动能力 / 执行权限」四个分组卡片；档案录入框在左栏底部。
- 六个开/关下拉全部换成拨动开关（`.sw`），配套 `aiSwGet/aiSwSet` 读写；
  `aiBody()` 的六个字段相应改为读 checkbox，保存/测连路径不变。
- 新增 i18n：`ai.se.secConn/Gen/Proactive/Exec`（中英）。

**④ 顺带修掉一个既有缺陷：设置页锚点被顶栏压住**
- 顶部导航形态下吸顶的是「功能栏(可折行 82px) + 工具条(54px)」= 136px，
  而设置页用 `scroll-margin-top: calc(var(--topbar-h) + 12px)`（54+12），
  JS 跳转还写死 `const TOPBAR = 54` —— 实测所有锚点落点偏移 -68px。
- 改为 `calc(var(--head-h) + 12px)`；JS 新增 `headOffset()`，每次滚动时
  取 `.topbar` 实际底边（侧栏形态仍是 54）。`tests/_check_settings_nav.py`
  由 12 项红变为全绿。

**验证**：`pytest tests -q` 264 passed；`_check_settings_nav.py`、
`_check_ai_dock_ui.py`、`_check_ai_global_ui.py` 全绿；冻结版实测设置卡
两栏 260+694、6 个拨动开关、坞标题「AI 工作台」且无样品下拉；保存按钮
实测返回「✔ AI 设置已保存并生效」。


## 2026-10-30（补五）：工作空间没传给 AI —— 选了目录模型仍说"没有样品"

**现象**（用户截图）：右侧选了 `G:\植物病毒分析平台
un\fastq`（GQMIX R1/R2
都在），问"查看目录下的样本进行已知病毒分析"，AI 回答"平台没有任何样品，
请提供 FASTQ 路径"。也就是说工作空间只影响了**文件浏览**，模型完全不知道它。

**根因**：前端把选择存在 `localStorage.vp_ai_ws` 只用于右侧列表；`/api/ai/chat`
的请求体里没有它，`chat.py` 的上下文自然也没有。AI 的工具（list_samples 等）
又只能看平台已建样品，于是只能说"没有"。

**修法**
- `chat.py`：新增 `_workspace_digest(path)` —— 列一层目录内容（名字/类型/
  大小/绝对路径，最多 200 条，标 total/truncated）；`_system_prompt` 把它注入
  `context.workspace`，`run_chat(..., workspace=...)` 透传。
- `ai_assist.py`：`/api/ai/chat` 接受 `workspace`（字符串），传给 run_chat。
- `app-ai.js`：新增 `AI.workspacePath()`，发送时带 `workspace: <localStorage
  vp_ai_ws>`。模型现在能直接从这些路径起草 `create_sample` / `run_tool`。

**验证**
- 单元：`_workspace_digest` 列文件/目录、缺失目录返回 error、空路径 None
  （`tests/test_fs_preview.py`，9 项）。
- 端到端（源码 + 冻结版各一次，真实模型）：带 `workspace=G:\...
un\fastq`
  提问 → 回复列出 4 个文件（GQMIX.R1/R2 30.7MB、Trinity.fasta、
  spades.virus.fasta），并指出 R1/R2 可成对建样品。
- UI：设置 `vp_ai_ws` 后 `AI.send()` 的请求体确实带 `workspace`，右侧列表 4 项。
- `pytest tests -q`：266 passed。两版已重打 exe 并替换、重启。


## 2026-10-30（补六）：工具轮次 3 太紧 —— 查到一半被掐断，只回一句「轮次超限」

**现象**（用户截图）：AI 查到新检索仍在运行、历史集合已就绪、准备提交预筛时，
回复末尾是红字「工具调用轮次超限，已截断本次回答」。用户问"为什么没有一直
运行到出结果"。

**根因**：`chat.py` 的 `MAX_TOOL_ROUNDS = 3`。一次正常的"查状态 → 再确认 →
提交"就要 3 轮以上；第 4 次模型调用被直接截断，且截断路径只发一条 error
事件，模型已查到的结论全部丢掉。

**修法**
1. 预算 3 → **10**（`VP_AI_MAX_TOOL_ROUNDS` 可覆盖，上限 30）。
2. **重复调用拦截**：同一工具+同一参数第 3 次起不再执行，改把工具结果换成
   "STOP: 已调用 N 次，结果不会变化，请停止轮询"——查任务状态最容易烧轮次。
3. **轮次用完也要出结论**：不再发"已截断"，而是追加一条"不要调用工具、
   基于已有结果给结论"的用户消息，做一次无工具收尾生成；失败才报错。
4. **系统规则第 12 条**（中英）：提交类 mcp_call 成功后立即收尾，告知用户
   "已提交、后台运行、完成后播报"，不要原地轮询。
5. **让"完成后播报"成立**：`tools._mcp_call` 识别返回里的 `task_id/task`，
   起守护线程轮询任务中心，done/failed/cancelled 时 `broadcast.publish('ai_read')`
   —— 预筛 / 检索 / CLI 建库这类 MCP 任务以前没有任何播报，模型承诺了却没
   人通知。

**验证**
- 新增 `tests/test_ai_chat_loop.py`（3 项，脚本化 chat_stream）：重复调用只执行
  2 次且出现 STOP；轮次用满后仍输出"收尾总结"且无"轮次超限"事件；预算 ≥6。
- 源码 + 冻结版实测同一道三连问（样品 / 工具数量 / 预筛项目）：3 次工具调用、
  0 错误、958 字完整回答（旧预算下必然截断）。
- `pytest tests -q`：269 passed。两版已重打 exe 并替换、重启。


## 2026-10-30（补七）：AI 回复里的表格 / 图片渲染

**现象**（用户截图）：模型输出的 Markdown 表格（`| 文件 | 大小 | 说明 |`）
被当成纯文本，一整行挤在气泡里；图片同理。

**根因**：`app-ai.js` 的 `aiFmt()` 只处理加粗 / 行内代码 / 无序列表，是
最早的极简版；平台里另有 `VPMD`（wiki/手册）能渲染表格，但对话没接它。

**改法**：重写为按块解析的 `aiFmt()` + `aiInline()`（仍在 `app-ai.js`，
不引入第三方库，先 esc 再放行）：
- 表格：首行含 `|` 且下一行是 `|---|` 分隔行 → `<table class="ai-tbl">`
  （表头、斑马纹、横向滚动容器；单元格内仍支持加粗/代码）；流式中间态
  （分隔行未到）先按普通行显示，到位后自动变表格。
- 图片：`![alt](url)` → `<img class="ai-img" loading="lazy">`，只允许
  `http(s)://`、`/static/`、`/api/`；`javascript:` / `data:` / `vbscript:`
  先整段删除（含嵌套括号残留）。链接同样只放行 http(s)、站内路径与锚点。
- 标题 `#~####` 分四级、引用 `>`、有序列表、分隔线。
- 渲染器同时挂到 `AI.fmt`，播报（ai_read）与消息共用同一套规则。
- CSS：`.ai-msg .ai-tbl/.ai-img/.ai-h*/.ai-quote/.ai-hr`。

**验证**：浏览器注入一段含表格+图片+引用+有序/无序列表的 Markdown——
生成 1 张表（2 表头、2 数据行、单元格加粗生效）、1 张图、引用 1 处、
有序 2 项、无序 2 项，`javascript:` 未进入 DOM；流式中间态不误判为表格。
冻结版同步后同样通过；`pytest tests -q` 269 passed（仅静态改动，未重打包）。


## 2026-10-30（补八）：思考过程可折叠 + 坞内去掉样品下拉、改名「AI 工作台」

用户要求：展示思考过程，拿到结果后自动折叠；去掉坞里「（不指定样品）」下拉
（跟随顶栏全局样品胶囊）；「AI 助手」改名「AI 工作台」。

- `provider.py`：解析流里的 `reasoning_content`（deepseek-reasoner 等）→
  `reasoning` 事件；`chat.py` 原样透传（不计入正文与历史）。
- `app-ai.js`：答案气泡拆成「思考过程（可折叠 details）+ 正文」两块。推理
  文字与工具轨迹芯片进思考块；第一条正文增量到达或结束时自动折叠，
  摘要显示「思考过程（N 步）」；被跳过的重复调用芯片划线计数。
- 换用 deepseek-reasoner 等"会思考"的模型时，思维链实时可见；deepseek-chat
  没有思维链字段，思考块里是工具轨迹（照样折叠）。
- 验证：脚本化事件序列（reasoning + tool×2 + delta）实测折叠行为与步数统计；
  冻结版同步后同样通过。`pytest tests -q` 269 passed。


## 2026-10-30（补九）：锚点「核对」不再甩裸 JSON + 锚状态注入 AI 上下文

**现象**（用户截图）：点锚条的「核对」跳到 `/api/anchor/<slug>/verify`，
浏览器里是一坨裸 JSON。用户反馈："逻辑很好，但上下游有问题，没能处理好"
——链路（执行单批准 → 建样品 → 自动锚定 → 可复核）都在，但结果没有以
人能读的方式呈现，AI 也不知道锚的存在。

**修法**
1. **核对结果浮层**（`app.js::anchorVerifyShow`）：点「核对」改为拉取
   verify 结果渲染成中文报告浮层——通过 = "✔ 输入与创建时一致"，附核对
   时间 / 账本头指纹（sha256 前 16）/ 账本条目数；problems 非空逐条红字。
   Esc / 点遮罩关闭。裸 JSON 端点保留（程序可读），人看的入口换成浮层。
2. **锚状态注入 AI 上下文**：`chat.build_context` 增加 `anchors`
   （`anchor.samples_meta()` 的只读摘要：sample/status/seq/finished）。
   模型现在能回答"GQMIX 的输入有没有被动过""哪些样品已锚定"，也能在
   建议里把"锚定"当作链路一环。核对（复算 sha256）仍是显式动作，不随
   每条消息自动跑。

**验证**：冻结版实测——锚条显示 `GQMIX · 阶段 0/5 · fastp`；点「核对」
浮层出「✔ 校验通过：输入与创建时一致 — GQMIX」（核对时间/指纹/条目 6）；
问模型"GQMIX 的锚状态" → 正确回答 running；`pytest tests -q` 269 passed。
两版 exe 已重打替换并重启。


## 2026-10-30（补十）：wait_task（AI 可等任务完成）+ /tasks?focus= 直达任务卡

用户要求：AI 工作台能**等任务完成**（直到给出结果），或者**跳转到任务进行页**。

- 新增 AI 工具 `wait_task(tid, timeout_sec)`：有界阻塞等待（10~240 秒，
  上限压在 provider 读超时 300s 之内），终态返回 status/name/stage/pct 与
  result（预览截 2000 字）；超时返回 running + 提示。模型用法（系统规则
  第 12 条改写为"长任务两条路"）：预计 ≤4 分钟或接近完成 → wait_task 等
  完直接给结果；可能更久 → 提交后立即收尾（后台跑、完成播报），并可给
  用户 `/tasks?focus=<tid>` 直达任务卡。
- `digest.task_overview` 每条补 `tid`（模型要引用任务得先有 id）。
- `tasks.html` 支持 `?focus=<tid>`：列表异步渲染后滚动到该任务卡并高亮
  （3 秒渐隐）。
- `app-ai.js` 思考块底部加「任务进度 ›」链接（新窗口开任务中心）。

**验证**：`tests/test_wait_task.py` 4 项（done 返回结果、超时报运行中、
缺 tid 报错、终态后停止轮询，tm/时钟均为脚本化替身）；源码实测
`/tasks?focus=ab97a024687d` → 102 张任务卡中目标卡滚入视口并高亮；
`pytest tests -q` 273 passed。两版已重打 exe 替换并重启。


## 2026-10-30（补十一）：工作台布局重排——模型选择/清空/设置挪到底部右下

用户草图：顶部只留「AI 工作台」标题；「当前配置 · deepseek-chat」整条和
右上角 ⟲/⚙ 两个图标挪到底部右下（参考 OmicoOS：输入行右侧是模型下拉）。

- `_nav.html`：坞头只剩 标题 + 就绪点 + ✕（浮坞收起用；总览内嵌态由既有
  CSS 隐藏）。模型下拉（`.ai-model-sel`，150px 紧凑宽）移入输入行、发送键
  左侧；新增 `.ai-dock-foot`：左侧「📁 选择工作空间」+ 工作空间芯片，右下
  ⟲（清空对话）+ ⚙（AI 设置）。`.ai-dock-ctx` 整行删除。
- `app.css`：`.ai-inputrow .ai-model-sel`、`.ai-dock-foot(-ops)` 样式；
  `.ai-workspace` 内嵌进 foot 后去掉自身 padding。
- 功能不变：模型切换/清空对话/设置入口/工作空间选择全部可用，只是位置随
  布局变化；`AI.loadModels/onModelChange/clear/pickWorkspace` 无需改动。

**验证**：`_check_ai_dock_ui.py` 11 项全绿；`pytest tests -q` 273 passed；
冻结版实测：坞头仅标题（1 个 ✕，总览隐藏）、模型下拉在输入行右下、
foot 左工作空间/右 ⟲⚙、无 `.ai-dock-ctx`、无 `#aiSampleSel`。
两版 `_internal/webapp` 已同步并重启。


## 2026-10-30（补十二）：顶栏「总览/样品/项目/全局清理」右对齐

用户要求：顶栏左侧那簇（总览 + 样品胶囊 + 项目胶囊 + 全局清理）挪到右侧。

实现：`.topbar { justify-content: flex-end }` + `.navctx-host { justify-content:
flex-end }`（host 是 flex:1 的弹性容器，内部还要右对齐才能把胶囊推到边上）。
左侧只留 `#tbTitle` 页名。EN / 📂 顺序不变，仍在其后。

验证：冻结版实测 chips 右缘 1089/1191/1289（紧贴 EN 区），无横向溢出；
`_check_home_dashboard.py` 全绿；`pytest tests -q` 273 passed。


## 2026-10-30（补十三）：模型下拉再挪——foot 行右端、清空/设置左侧

用户指定：模型下拉从输入行挪到下一行右下角，放在 ⟲/⚙ 图标左侧。

- `_nav.html`：`#aiModelSel` 从 `.ai-inputrow` 移入 `.ai-dock-foot-ops`
  （顺序：模型下拉 → ⟲ 清空 → ⚙ 设置）。输入行现在只有
  ＋ / 输入框 / 发送键，更清爽。
- `app.css`：`.ai-dock-foot-ops .ai-model-sel` 紧凑样式（200px、12px），
  原 `.ai-inputrow .ai-model-sel` 规则删除。
- 功能零变化：`AI.loadModels/onModelChange` 引用的还是同一个 id。

**验证**：DOM 断言（foot-ops 子元素顺序 = aiModelSel → ⟲ → ⚙；输入行内
无残留）；`_check_ai_dock_ui.py` 全绿；两版已同步并重启标准版。


## 2026-10-30（补十三）：标语进侧栏品牌位 + 环境状态进顶栏 + 封面整块删除

用户定案：侧栏顶部的品牌位改放「看见样本里的病毒世界。」；页面上的
「植物病毒分析平台」「看见样本里的病毒世界。」两行删掉；环境状态
（模块/工具/数据库/磁盘）搬进顶栏；右侧入口只保留「环境自检」。

- `_nav.html`：侧栏品牌文字改为标语（`data-i18n="hm.heroLead"`，中英随
  语言切换）；顶栏 `tb-title` 之后插入 `#envStrip`（`env-strip-top` 变体，
  扁平无卡片底），链接改指 `/settings#sec-selfcheck`、文案「环境自检」。
- `home.html`：封面（标题 + 定位语）与页内 envStrip 整块删除 —— 总览页
  从此只有 AI 工作台 + 运行中任务 + 最近样品。环境条的填充脚本保留在
  home 内联（其它页面 envStrip 保持隐藏）。
- `app.css`：新增 `.env-strip-top` 扁平样式（无卡片底、12px、防溢出）；
  删除不再使用的 `.hero.cover.hero-slim` 块。
- i18n：新增 `hm.env.selfcheck`（中英）；`hm.heroLead` 既有键复用。

**验证**：冻结版实测——侧栏品牌显示标语；封面消失；顶栏出现 4 位环境
状态（✔模块 141/141 · 外部工具 32/32 · 数据库 就绪 · 磁盘 34.3 GB）与
「环境自检」链接；`pytest tests -q` 273 passed。仅模板/静态改动，
两版已同步并重启标准版（Jinja 模板缓存需重启）。


## 2026-10-30（补十四）：侧栏品牌位改两行 —— 平台名 + 定位语小字

承接上一条（用户澄清）：不是用定位语**替换**平台名，而是两行一起作为
侧栏顶部品牌 —— 第一行「植物病毒分析平台」，第二行「看见样本里的病毒
世界。」小字。

- `_nav.html`：`.sn-brand-txt` 内改为 `b.sn-brand-name`（NI.brand()，
  服务端按语言输出）+ `small.sn-brand-tag`（`data-i18n="hm.heroLead"`，
  中英随语言切换）。
- `app.css`：`.sn-brand-txt` 改纵向 flex；`.sn-brand-name` 13.5px/700
  nowrap+ellipsis；`.sn-brand-tag` 10.5px `#9dc4ae`；821~1024px 档与
  顶部导航档分别给小一号的字。原 `.sn-brand-txt { font-size: … }`
  容器规则不再影响子元素（子元素均带显式字号）。
- 顶栏右侧状态簇（上一条）不变；冻结版实测两行显示、无溢出；
  `_check_home_dashboard.py` 全绿。仅模板/CSS 改动，两版已同步并重启。


## 2026-10-30（补十五）：顶栏环境状态一体化 —— 状态位改胶囊、消除中段空洞

用户反馈：环境状态位「不是完整的整合在顶部，好像嵌套的」——状态是裸文本
挤在左段，环境自检是下划线链接，右侧样品/项目/清理却是胶囊芯片，中间一段
大空白，读起来像两截。

- `env-strip-top` 改为 `flex:1` 的连续容器：四个状态位（模块/外部工具/
  数据库/磁盘）与「环境自检」统一做成**胶囊**（圆角 999、surface 底、
  状态色描边 ok/warn/bad）；
- `navctx-host` 不再生长（`flex: 0 0 auto; margin-left: auto`），右簇
  （样品/项目/清理/EN/📂）被推到最右，中段只留一段干净的分隔空隙。

**验证**：冻结版实测——四个状态胶囊 + 环境自检胶囊与右簇同排同高，
`gapToRight=17px` 为自然间距；`_check_home_dashboard.py` 全绿。


## 2026-10-30（补十六）：锚条链接改向 —— 「正在分析」去任务中心、「核对」去结果页

用户要求：左下常驻锚条（`#vpAnchorBar`）上「正在分析」应跳任务中心（原为
`/results`），「核对」应直接打开该样品的结果页（原为弹校验浮层）。

- `app.js anchorBarPaint()`：`abOpen.href = '/tasks'`（title「打开任务中心
  查看进度」，完成态文案仍为「刚完成」）；`abVerify.href = '/results?sample='
  + encodeURIComponent(a.sample || a.slug)`（title「打开该样品的结果页」）。
- 顺带清理死代码：`anchorVerifyShow()` 浮层函数及导出删除（app.js）、
  `.ancv` 样式块删除（app.css）、`anc.cvChecked/cvHead/cvSeq` 中英键删除
  （i18n.js）。锚校验数据本身仍由 `/api/anchor/verify` 提供，可随时复用。
- 冻结版实测：`abOpen`→`/tasks`、「正在分析」；`abVerify`→
  `/results?sample=GQMIX`、「核对」；无控制台报错。

**任务中心分组现状（回答用户提问）**：`tasks.html` 当前是**按提交时间倒序
的平铺列表**，过滤器只有「只看当前样品」（`curOnlyChk`）；没有按项目、
也没有按任务模块分组的视图。任务概览接口（`get_task_overview`）每条已带
`tid/name/sample/stage`，前端加分组器即可实现「按项目/按模块」折叠视图，
后端无需改动。


## 2026-10-30（补十七）：任务中心分组视图 —— 按时间 / 按样品 / 按模块三态切换

承接用户提问「任务中心有按照项目分组展示嘛，还是只按照的提交时间呢，或者
任务模块」，用户拍板「两种都支持」。

- `tasks.html` 工具栏新增 `#gtabs` 分组切换（按时间=平铺默认 / 按样品 /
  按模块），选择存 `localStorage('vp_tasks_group')`，与状态过滤标签
  （全部/运行中/…）正交叠加。
- 渲染重构：原 `render()`/`renderArch()` 的增量卡片逻辑抽成
  `placeCard()`（cardSigs 签名防闪烁）+ `layoutList()`（按 GROUP 决定
  卡片进列表根还是组体）；组容器 `.tk-group` 带可折叠头（组名 + 数量 +
  运行数徽标，折叠态存页面内存 `GRP_CLOSED`，跨轮询保持）。归档区同样
  生效。
- 归类规则（前端正则，中英任务名都覆盖；依据各 `tm.start()/tm.attach()`
  调用点的真实命名）：
  - 样品分析：`队列·/Queue·`、`排队·/Queued·`、`样品分析 X`、
    `AI执行·X`（负向先行排除 `AI执行·工具·`）、`<样品> · 阶段标签…`
    （samples.py 流水线任务唯一的空格间隔写法）；
  - 工具运行：`工具·`、`AI执行·工具·`、`AI tool·`、`宿主预测·`、
    `Contig分析·/Contig `、`导出迁移 GIF·`；
  - 数据库构建：宿主库/病毒库/病毒鉴定库/通用病毒库/RVDB 库构建；
  - 下载与参考集：`下载·`、ICTV/NCBI/GenBank 下载导入、`CDS/PEP 提取`、
    `GenBank巡检`、`集合建树`、`下载/准备 Taxonomy`、`病毒数据集下载`、
    `datasets … 下载`；
  - 检索与鉴定：`检索绘图·`、`AI 元数据补全·`、`AI 总结·`、`LOGAN 批量`、
    `公共检索`（meta.py SRA/GSA 检索）；
  - 导出与提交：`CDS/PEP 导出·`、`物种名校验·`、`提交 .sqn 生成·`；
  - 系统任务：`存储占用扫描`；其余落「其他任务」。
- 组排序：按样品 = 组内最新任务时间倒序（未关联样品沉底）；按模块 =
  固定顺序（样品分析→工具运行→数据库构建→下载与参考集→检索与鉴定→
  导出与提交→系统任务→其他）。
- i18n：`tk.gtime/gsample/gmodule/gnosample` + `tk.mod.*` 八个模块名，
  中英双语。

**验证**：归类器 33 例单测全过；冻结版实测——按样品把 2 条「公共检索」
归入「未关联样品」、按模块归入「检索与鉴定」，组折叠跨轮询（7s）保持、
再点开恢复，刷新后 module 模式持久化，切回按时间平铺正常，控制台 0
SEVERE；`pytest tests -q` 273 passed。模板/静态改动，两版已同步并重启
标准版。


## 2026-10-30（补十八）：pdprep 接自建在线库 —— 「在线库圈定 accession → NCBI 取正文」

用户问 pdprep（进化动力学数据接入，/tools?g=phylodyn#t-pdprep）需要的病毒
基因组「能不能直接通过 api 获取」。探测 39.106.101.94 网关（48 数据集在线，
CORS 全开、GET-only、120 req/min/IP）后确认：网关 reference/sequences
（GenBank 记录级元数据，含 Species_NCBI/Length/Geo_Location/Collection_Date
列）**没有序列正文**，日期地点也稀疏 —— 不能当独立数据源；但它圈定
accession 又快又准（国内直连）。方案：**网关圈定 → 既有 Entrez efetch
管道取正文与特征表**。

- 新模块 `Virus_Platform_Core/gateway_client.py`：`GW_BASE` 默认
  `http://39.106.101.94/api/v1`（env `VP_GATEWAY_BASE` 可覆盖）；`health()`；
  `resolve_accessions(species/taxid, full_only, max_records)` —— 学名等值
  `?Species_NCBI=` → 模糊 `?q=` → `?Taxid=` 三级回退；full_only 用
  sequences 数据集 + `Nuc_Completeness=complete` 组合过滤（reference/complete
  数据集只有少量代表记录，实测 TMV 2 条 vs 组合过滤 98 条）；超时/失败统一
  RuntimeError 带可操作文案（提示取消勾选回退 NCBI 检索）。
- `tool_jobs._tool_job_pdprep` genbank 分支：`gateway=1` 且未手填 accessions
  时先 `resolve_accessions(...)` 圈定清单 → `import_from_genbank(accessions=…)`
  走既有 efetch 管道（phylodyn_kit.py:1190 accessions 分支复用，日期/地点
  仍由 GenBank 特征表提供）。
- UI（tools.html pdprep 卡）：「在线库圈定」checkbox `pd_gw`（默认不勾）+
  说明 hint；`runPdprep` 传 `params.gateway`；校验「勾了网关就要填物种名」。
- i18n：`tk.pdprepGwOn/GwHint/GwNeedSpecies` 中英。
- 测试：`tests/test_gateway_client.py` 8 例（mock urllib；exact/fuzzy/taxid
  三策略、full_only 组合过滤 URL 断言、分页、上限截断、无匹配可操作报错）。

**验证**：单测 8/8；真机链路 TMV full_only —— 网关命中 10 条全长
（AB369275.1 韩国株…）→ `import_from_genbank(accessions=…)` NCBI efetch →
FR878069.1（2012-01-10/USA）产出合格标准元数据行，NC_001557.1 RefSeq 缺
日期地点被正确剔除（与 pdprep 文档口径一致）；tools.html 内联脚本 node
--check 过。exe 重打后两版同步。


## 2026-10-30（补十九）：AI 工具调用 DSML 泄漏 —— 根因是 propose_action spec 缺 name

用户截图：AI 工作台里模型回复「我先查一下当前可用的工具口径…」后，正文
直接打出 `<|DSML|calls><|DSML|invoke name="mcp_tool_catalog"></|DSML|invoke>
</|DSML|calls>` —— 工具调用没走结构化通道。

**完整因果链（全部实测确认）**：
1. `TOOLS_SPEC` 里 `propose_action` 条目**缺 `'name'` 字段**（只有
   description/parameters，tools.py 曾被改坏）；
2. deepseek 官方 API 严格校验 → 整个请求 **422**（tools[7].function:
   missing field `name`，复现两次）；
3. `chat.py` 的兼容降级逻辑（`AIProviderError` 且 status in 400/404/422 →
   `use_tools=False` 整轮禁用工具重试）把这次 422 当成了「端点不支持
   tools」→ 降级生效；
4. 降级后的模型没了 tools 定义，但 system prompt 里大量描述了平台工具
   （mcp_tool_catalog/propose_action 等）→ 它凭记忆"演"工具调用，把
   DeepSeek 内部标记（DSML）直接当正文吐出 → 用户看到泄漏。

**修复（三处）**：
- `tools.py`：补回 `'name': 'propose_action'`（一行）；真机验证——修复后
  同样的消息模型正常发起结构化调用（list_samples + get_task_overview），
  无 422、无降级、无泄漏；
- `chat.py`：新增 `_strip_dsml()`（DSML_RE 剥离 `</?\|DSML\|…>`），三个
  出口全部过滤（工具轮 assistant 入库、message 分支入库、轮次用完收尾流）
  ——就算上游再泄漏，也不污染对话历史（防"越聊越多"自我强化）；
- `tests/test_ai_spec_sanity.py`（新）：6 例——TOOLS_SPEC 每条必须有
  function.name/parameters.object（防再改坏）、propose_action enum 动作
  齐全、_strip_dsml 三态。**以后任何改动 TOOLS_SPEC 的提交都必须过这组
  结构自检**。

## 2026-11-01（补二十）：AI 代理自举层 —— agent-guide / agent-ops / 计划≠已执行

背景：调研 github.com/forageseed/organelleverse 的「AI 驱动」——它自己不调
任何 LLM，而是把 Python 库按"给外部 AI agent 当工具"设计（agent_guide 自举
+ operations 注册表 + 参数 schema + 诚实结果契约 + 唯一写入边界）。取其三件
落到本平台：

**① 代理自举指南**
- 仓库根新增 `AGENTS.md`（完整指南：三入口/自举三步/发现边界/执行纪律/
  权限边界/环境事实）；agent 宿主与人都直接读它，盘上单一事实源。
- 新增 `Virus_Platform_Core/agent_guide.py`：`read_guide()`（源码模式读
  PLATFORM_ROOT/AGENTS.md，冻结版读 _MEIPASS 副本，缺失回退内嵌兜底版，
  不抛异常）+ `read_skill()`（指针技能卡，可存为
  `.agents/skills/virusplatform/SKILL.md`）。
- `main.py` 新增 `agent-guide [--skill]` 子命令；spec 里 AGENTS.md 随包
  （`datas += [(ROOT/'AGENTS.md', '.')]`，紧挨 README.md 那行）。

**② 操作发现层（单一事实源重构）**
- `_TOOL_PARAM_SCHEMAS`（mcp_server.py 内 330 行参数表）**原样迁出**为
  `Virus_Platform_Core/agent_catalog.py` 的 `TOOL_PARAM_SCHEMAS`；
  mcp_server 改为 `import … as _TOOL_PARAM_SCHEMAS`（行为零变化，导入点
  在文件头部 import 区）。新增 kvchain 的运行时扩展仍发生在同一 dict 上，
  catalog 与 MCP 看到同一份。
- `agent_catalog.py` 四件套：`list_operations(kind)` / `search(q, limit)` /
  `describe(op_id)` / `parameter_schema(op_id)`——统一目录 = 工具箱
  （TOOL_REGISTRY×参数表）+ 15 管道阶段（STAGE_ORDER×STAGE_NAMES）+
  7 执行单动作（executors.ACTIONS），共 54 条，含调用口径（vp_run_tool /
  vp_pipeline_start / propose_action / HTTP / CLI）。
- `main.py` 新增 `agent-ops [关键词] [--id <op>] [--schema] [--kind] [--json]`。
- mcp_server 新增 `vp_search_operations` / `vp_describe_operation`（_RO，
  只读），工具数 22 → **24**；实测 `_tool_manager.list_tools()` 两工具在位。

**③ 「计划 ≠ 已执行」措辞纪律（反幻觉红线）**
- `chat.py` 系统提示：中文第 12 条 / 英文第 12 条（原 12 顺延 13）——
  propose_action 成功 ≠ 已开始 ≠ 已完成；只有三种口径可说「已开始/已完成」：
  任务中心真实 tid 且终态 done / 亲自读到落盘产物 / create_sample 同步
  done；禁止由计划推断结果、禁止编造数字。
- `tools.py` propose_action description 追加同义约束（起草≠执行……）——
  该描述每轮随 tools 传入模型，是最有效的约束位。

**测试**：`tests/test_agent_guide.py`（新，10 例：指南/技能卡/兜底/目录
形状/已知操作/中英搜索/工具·阶段·动作 schema/未知安全/参数表单一事实源）；
AI 全家桶回归 38 passed。CLI 实测：agent-ops 三种用法与 agent-guide 两种
输出全通。

**遗留**：README「40 个子命令」等处数字未同步（现 42）；wiki 未加页
（指南已覆盖，后续可挂 FAQ）。frozen 版生效需下次重打包（AGENTS.md 进
_internal、agent-guide/agent-ops 两个子命令进 CLI）。

## 2026-11-02（补二十一）：AI 工作台「跑不完整 / 改了没效果」三处根治

用户现场：① 对话坞里模型把内部标记 `<|DSML|invoke name="propose_action">`
当正文整段打出（截图）；② 在设置页把「模型」改成 deepseek-flash、点绿色
「保存设置」弹「✔ 已保存并生效」，回到坞里仍显示 `deepseek-chat`；
③ 让 AI 跑 kvsuite 定量+共识，任务中心一律 failed。

三条互不相干，逐一查实（全部实测，不靠推测）：

**① kvsuite 共识段每次必崩（这才是"跑不完整"的主因）**
- 任务 `d025e6171c74`（打包版实盘）`status=failed`，
  `RuntimeError: known_virus_suite 退出码 1`，栈底是
  `kv_consensus.py:559 → pandas/core/generic.py __getattr__`：
  `AttributeError: 'DataFrame' object has no attribute 'iter_rows'`。
- 根因：`kv_identify.py` / `kv_filter.py` 于 **2026-10-20 由 polars 迁回
  pandas**（省 167.7MB 的 polars-runtime-32），但 `kv_consensus.py` 的
  `passed_df.iter_rows(named=True)` 没跟着改 —— 过滤表已是 pandas，共识段
  却按 polars 取行。跑该次任务的日志可证：identify 17 命中 → filter 保留 6
  行 → 进共识段当场崩，`consensus/` 目录为空。
- 修复：新增模块级 `iter_passed_rows(df)`（有 `iter_rows` 走 polars，
  否则 `to_dict('records')`），共识段改调它；两种上游都能吃。
- 修完再跑，**又暴露第二处**：`kv_consensus.py` 的 `_run()` 日志写完直接
  结束、**没有 `return`**，恒返回 None，而 `_bam_mapped_count()` 拿它读
  `.stdout`（`check=False` 的 `samtools view -c`）→
  `AttributeError: 'NoneType' object has no attribute 'stdout'`，
  崩在"比对完统计映射数"这一步。补 `return result`。
- 实跑验证：同一 GQMIX 双端（G:\…\run\fastq）`stage=all` 全跑通，
  identify 17 命中 / filter 6 行 / 共识段产出参考 + minibwa 索引 + BAM。

**② 设置页「保存设置」根本不提交 AI 卡片**
- 现象：改模型 → 点绿色「保存设置」→ toast「✔ 已保存并生效」→ 坞里
  「当前配置 · deepseek-chat」不变。
- 根因：`webapp/templates/settings.html` 的 `saveSettings()` 只 POST
  `/api/settings`（language/threads/email/defaults），而 AI 字段由另一颗
  按钮 `aiSave()` POST `/api/ai/config`；同一页两颗按钮，主按钮静默忽略
  AI 卡片，toast 却报成功。
- 实测确认后端无辜：直接 POST `/api/ai/config {"model":"deepseek-flash"}`
  → 200，`platform.json` 的 `ai.model` 立刻变 deepseek-flash，
  `GET /api/ai/config` 也回 deepseek-flash。
- 修复：`saveSettings()` 在常规设置存好后，若本页有 AI 卡片
  （`typeof aiBody==='function' && $('aiModel')`）再补一次
  `/api/ai/config`（传 `aiBody(false)` → 不回写已存 Key）；失败时 toast
  改口径「常规设置已保存；AI 设置保存失败」，不再假报成功。

**③ 流式正文出口漏过滤 DSML（截图的直接原因）**
- 根因：2026-10-30 的 `_strip_dsml()` 只覆盖三个**入库**出口，**没覆盖
  `delta` 流**——模型一旦把内部标记当正文吐出来，前端逐字渲染，
  用户就看到原始 DSML；而历史里是干净的，形成"截图很脏、复现不出来"的
  错觉（本轮实测：正常请求 200、无降级、无泄漏）。
- 修复：`chat.py` 新增 `_DsmlStreamGuard`（有状态缓冲）：完整标记直接剥，
  **跨 chunk 的半截标记先扣住**（`<`、`<|DS`、`</|DSML|` 这些尾巴不下发），
  凑齐再定去留；三个正文出口（工具轮 delta / 正常收尾 / 轮次用尽的总结）
  全部改走它，`done` 事件附带 `dsml_stripped` 便于前端与审计观测。
- 反面案例也锁进测试：普通正文 `a < b 且 c > d` 不得被吃掉，
  行尾孤立的 `<` 必须照常吐出。

**④ 参考库 `reference.fasta.fai` 自身损坏（第三处崩溃，纯数据问题）**
- 修完上面两处后共识段一路跑到绘图（6 张深度图已出），变异段再崩：
  `RuntimeError: bcftools mpileup 失败(rc=1): [E::fai_insert_index] Malformed
  line: width (39) less than base count (60) | [E::fai_load3_core] Failed to
  read FASTA index ...\databases\virusref_db\plant_ref\reference.fasta.fai`。
- 根因：索引首三行 `NC_116488.1 2302 13 60 39` / `NC_083851.1 13626 2367 60 228`
  / `NC_077216.1 6096 16234 60 102` —— linebases=60 而 linewidth=39，
  **物理上不可能**（行宽 < 碱基数）。offset=13 说明索引是在 LF 换行的
  fasta 上建的，而当前 fasta 是 CRLF（换行宽度被改写过、索引没重建）。
  两者 mtime 同为 2026-10-19 01:10:14。
- 影响面：源码库 `databases/virusref_db/plant_ref/` 与用户打包库
  `D:\PVAP\PlantVirusPlatform_with_Plugin\VirusPlatform-Database\databases\
  virusref_db\plant_ref\` 两份坏 .fai **sha256 完全相同**
  （`560093E4…D03031437`），即分发时就带着这张坏索引。
- 数据修复（两边都做，先备份 `.bak_20261102`）：
  `3rd/tools/samtools/bin/samtools.exe faidx reference.fasta`，
  首行变 `NC_116488.1 2302 13 60 61`（linewidth=61=60+\r）。
- 代码自愈：`kv_common.py` 新增
  `ensure_fai(ref_fasta, samtools=None, logger=None, env=None)` —— 读 `.fai`
  首行做物理约束校验（`linewidth < linebases or linewidth > linebases + 2`
  即判坏 → 重建），缺 samtools 时返回 False 不抛；`known_virus_suite.py`
  的 `main()` 在工具探测后、`args.reference` 存在时调用一次（异常只
  warning，不拦分析），`kv_consensus.py` 处理单条 ref 时也改走它。
- 验证：`stage=all` 全链路实跑（`run/tool_runs/kvfix_verify3_20261102`，
  GQMIX 双端、salmon、threads=8、jobs=4）**exit 0，1m53s 跑完**：
  17 命中 → filter 保留 6 → 共识 6/6 + 蛋白 QC 2/6 → 深度图 6 张 →
  变异段 4 参考 / 60 图 / 4 个 SNPGenie。

**测试**：`tests/test_ai_spec_sanity.py` 6 → 11 例（新增 StreamGuardTests
4 例：单块泄漏 / 跨 chunk 切片 / 尖括号正文 / 半截标记 flush）；
`tests/test_kv_pandas.py` 新增 8 例（pandas 入参、polars 形状兼容、
`_run` 必须返回 CompletedProcess、`_bam_mapped_count` 解析与 -1 兜底、
`ensure_fai` 判坏重建 / 好索引不动、`iter_passed_rows` 两种入参）。
两文件 **24 passed**。

**遗留**：修复分层如下——
（1）`.fai` 数据修复与设置页模板**已直接落到用户机器**（模板
`_internal/webapp/templates/settings.html` 已就地更新并留 `.bak_20261102`，
sha256 `535504AE…B615049261`），**重启一次服务即生效**；
（2）`chat.py` / `kv_consensus.py` / `kv_common.py` / `known_virus_suite.py`
四处 Python 改动在 exe 归档内，**必须重打包**才进打包版；源码模式
（`python app.py`）已即时生效。
另：设置页两颗保存按钮的分工建议后续做「AI 卡片未保存」脏标记提示，
本轮只做了行为补齐。

**部署落地（2026-11-02 01:00）**：按 `dev_tools/package.py --verify`
重打包（`D:\桌面\PVAP\VirusPlatform`，1.745 GB / 7161 文件，PyInstaller
6.22.2 + Python 3.12.10，git 5b27fe07），随后只替换用户安装的程序层——
`VirusPlatform.exe` 与整棵 `_internal`（277 MB / 1478 文件），
**保留** `run/`、`platform.json`、`3rd/`、`VirusPlatform-Database/`、
`primer3_pkg/`。改动前备份：`VirusPlatform.exe.bak_20261102`、
`_internal.bak_20261102`（回滚只需把两者换回）。

替换后实测（打包版，非源码）：
- `GET /settings` → 200，页面已含 `aiBody(false)`（保存设置会带上 AI 卡片）；
- `GET /api/ai/config` → `model=deepseek-flash`、`api_key_set=true`、
  `ready=true`（用户原设置原样保留）；
- `--run-engine known_virus_suite.py all`（GQMIX 双端，salmon，
  threads=8，jobs=4）→ **exit 0，1m53s**，产物
  `run/tool_runs/kvfix_pkg_20261102/`：identify 17 命中 → filter 保留 6 →
  `consensus/` 6 个参考目录 + `consensus_qc.tsv` → 变异段 4 参考 /
  40 图 / 4 个 SNPGenie，与源码模式结果一致。
- 单独跑 `consensus` 子命令会「过滤结果为空，跳共识段」——这是既有设计
  （`known_virus_suite.py:498` 起，standalone consensus 的 `confirmed`
  为 None，只有 `all` / 管道内联才带 in-memory 过滤表），不是本次引入的
  缺陷；验证必须用 `all`，不要用 `consensus` 单段。
- 打包版工具探测里 `[MISS] mafft`：`kv_consensus.py` 全文不引用 mafft，
  该缺席不影响共识段（本就不消费）。

## 2026-11-02（补二十三）：AI 工作台全面审查 —— 静默假成功、预算被思维链吃掉、坞里看不到进度

用户要求「全面审查、增强、优化，实现真正的可用性和落地」。做法：两个只读
子代理分头审 `ai_agent/{tools,executors,agent_catalog}.py` 与前端
`webapp/static/app-ai.js` + `web/templates`，Lead 逐条核实（grep / 实测 /
真实任务复跑）后统一改，避免并发写冲突。

**审查认定的 P0（全部已修）**

① **单动作执行单永远停在 launched（静默假成功）**：`executors.py` 非
`create_sample` 的动作批准后只 `_snapshot_publish('launched')`，全仓没有
watcher 推到终态——`'launched'` 只出现在白名单与这一处赋值。卡片一直「✓
已启动运行」，任务早崩了也不变色、不写 error，AI 只能照旧说「还在跑」。
修：新增 `_watcher_kind()` / `_watch_to_terminal()`（任务型轮询
`web.tasks.tm`，下载型轮询 `get_dl_manager()`，终态回写 done/failed 并把
任务 error 带进 `rec['error']`），`_execute` 落完任务后为每条单动作起守护
线程；`create_sample` 不再把**样品名**写进 `tid`（此前 AI 拿它去
`wait_task` 会空转到 240s 超时），改存 `result_sample`。
实测（`tmp_diag/e2e_action.py`）：A 输入不存在 → `executing → failed` 且
error 为真实的 `FileNotFoundError: 路径不存在…`；B 真转小 FASTQ →
`executing → launched → done`（tid 落盘）。

② **「原参数重试」实际按默认值重跑**：`_dispatch` 只用 `rec['params']`，
`base_params` 全库只写不读。修：analysis 分支 `body = dict(base_params);
body.update(params)`，与「body > 设置页默认」同向。

③ **模型可绕过审批触发不可逆操作**：`mcp_call` 只黑名单了
`vp_delete_tool_run`，但 `vp_cli_submit` 能发 `tool-runs action=clean` 与
`db-migrate action=apply`（`_cli_check_confirm` 只认 confirm），违反
AGENTS.md §5/§7。修：新增 `_CLI_DESTRUCTIVE` + `_mcp_denied()`，前置拦截
并回 `{'error':…, 'blocked':True}`。

④ **给模型的表格只有前 8 列**：`digest._tsv_head` 砍到 8 列、单元格 80 字，
而 kvsuite 汇总表 22 列，Coverage(%)/MeanDepth/EM_Reads/Avg_Read_ANI 都在
第 9-16 列——被问覆盖度只能编。修：`MAX_COLS` 8→24，`.csv` 按逗号解析，
超宽时显式给 `columns_total` / `dropped_columns` / `note`。

⑤ **多步计划卡看不到步骤**：`_publish` 的字段白名单漏了
`steps/step/step_total/step_action/step_title`（`_snapshot` 里有），而
对话坞只从事件取这些字段 → 计划卡不列步骤、批准后永远没有「第 2/3 步」，
用户判定卡死。修：`_publish` 白名单与 `_snapshot` 取并集（含
`result_sample`）。

**P1 / P2 一并处理**

- **推理型模型的预算被思维链吃掉**：deepseek-flash 在 max_tokens=2048 时
  reasoning 4225 字符、正文只剩 536 且半截（`tmp_diag/probe_budget.py`
  实测；4096 时正文 2410 完整）。修：`chat.py` 加预算游标
  （`_BUDGET_MAX=8192`）——`finish=='length'` 且正文为空时自动加倍重试并
  发 notice；有正文触顶则补「_（回答触到最大生成长度上限被截断…）_」+
  warn notice；空正文的错误按 finish/reasoning_chars 分因，不再一律
  「模型未返回内容」。
- **降级不再静默**：400/404/422 关工具时发 warn notice（「已降级为纯上下文
  模式」+ 上游原文摘要）——这正是 10-30 那次 DSML 泄漏的上游条件。
- **工具参数口径**：新增 `_tool_params(tool, job)` 以
  `TOOL_PARAM_SCHEMAS` 为主、job 源码校验的键补为必填（`source='job'`），
  去掉 `[:8]` 截断；kvsuite 以前报 6 个「必填」（实际三选一）；
  `agent_catalog.py` 里 `kvchain: []` 改为继承 kvsuite 的 25 条（一键流程
  参数口径完全相同）。
- **stages 非法项不再静默丢弃**：`create_action` 直接报错并列出 15 个合法
  阶段键；`propose_action` 返回体回显归一化后的 action/sample/stages/
  params/steps；reason 空则拒收（原来必填只写在 spec 里，执行层不校验）。
- **`_mcp_call` 超长返回**：4000 字符裸切会把 JSON 从中间截断、task_id
  丢失；改为结构化裁剪（保 task_id/status/summary/next + 截断标记）。
- **工具名重复拼名**：兼容网关每片都重发完整 name（`mcp_callmcp_call`）时
  新增 `provider._join_name()` 归一。
- **SSE 静默期**：`/api/ai/chat` 以前长推理/长工具轮（`mcp_call` 最长
  330s）期间零字节，前端只有「思考中…」。修：`ai_assist.py` 把
  `run_chat` 放到工作线程喂队列，主生成器每 5s 无数据就发一帧
  `{'type':'tick','elapsed':N}`，前端把提示改成「思考中… 已等待 N 秒」
  （`tmp_diag/test_heartbeat.py` 实测 12s 的打桩响应 → ticks=[5,10]，
  随后 delta/done 正常）。
- **前端一批「点了没反应」**：批准/驳回/闸门按钮失败后不恢复（现在
  cards()+paint()+fail()，失败恢复并在卡内写 `.ai-err`，成功 4s 未翻转也
  放回可点）；error 帧不再与「思考中」并存；`AI.clear` 生成中先 abort；
  发送中按回车给「先点 ■ 停止」提示；流结束 flush decoder 与残余 buf
  （末帧中途断开不再丢最后一句）；`pageshow(persisted)` 重建 SSE（bfcache
  回来不再静默）；abort 的半截回答只留界面、不进历史（否则下一轮模型接着
  残句写）；播报/闸门文本也过 `_strip_dsml`。
- **批准竞态**：`resolve()` 的「是否 pending」判定与置 executing 原分两段
  加锁，双击「批准」会各起一个执行线程（同一动作跑两遍）。改为同一把锁内
  完成翻转，并发上限命中时把状态放回 pending。

**测试**：新增 `tests/test_ai_hardening.py`（17 例：事件步骤字段、
base_params 合并、破坏面 CLI 拦截、`_tsv_head` 列数/CSV/超宽、
kvsuite+pdprep 参数必填、`_join_name`、watcher 终态、批准竞态/驳回终态）。
`test_ai_hardening.py + test_ai_spec_sanity.py + test_kv_pandas.py` →
**41 passed**。

**端到端实测（源码模式，真 provider）**：`tmp_diag/e2e_chat_route.py`
用 Flask test_client 打 `/api/ai/chat`（deepseek-flash）→ 4.8s、
`counts={'reasoning':79,'tool':1,'delta':529,'done':1}`、工具
`list_samples`、正文 988 字符、无 error；`propose_action` 三条分支实测：
缺 reason → 明确报错；非法 stage → 报错并列出合法键；正常 → `created:
true` + 回显 stages。

**仍未做（记录在案，非阻塞）**：坞历史 localStorage 单键 30 条多标签互覆、
闸门卡不显示 15 分钟倒计时、`settings.html` 档案名拼进 inline onclick、
每标签一条 SSE（6 标签触顶 HTTP/1.1 连接）、`tool_runs` 空目录残留、
非中文界面下并发闸按任务名前缀计数失效。

## 2026-11-02（补二十四）：P2 全清 —— 多标签、刷新丢卡、闸门倒计时、设置页注入面

上一节（补二十三）修完五个 P0/P1 后留下的 P2 清单，本节逐条清掉。分工：`app-ai.js`
+ `app.css` 由主 agent 改；`broadcast.py` / `executors.py` / `ai_assist.py` /
`settings.html` / 新测试由子 agent 改（写作用域不重叠）。

**前端（`webapp/static/app-ai.js`）**

- **多标签互覆**：历史原写单一 `localStorage['vp_ai_msgs']`（30 条），两个标签
  各聊一段就互相覆盖。改为「会话 id + 分键」：`AI._initConv()` / `AI.msgsKey()`
  用 sessionStorage 存 `vp_ai_conv`（刷新不变、新标签新会话），键名
  `vp_ai_msgs:<convId>`，内容 `{t, msgs}`；`_pruneHistory()` 只留最近 12 段会话。
  首启检测到旧单键 `vp_ai_msgs` 数组则迁到 `vp_ai_msgs:main` 后删旧键（已在
  jsdom 冒烟里验证迁移路径）。
- **刷新堆旧卡**：`lastEventId` 原每页从 0 起，服务端重放整个环形缓冲。改为
  持久化到 sessionStorage（`vp_ai_eid`），并在 `ai_assist.py` 侧的事件查询支持
  `?since=`；`AI._foldBacklog()` 把回放事件（`ev.backlog`）除「待决闸门/
  待批执行单」外全部折进 `<details class="ai-backlog">`（默认收起，summary 写
  「此前会话的 N 条事件」）。
- **服务端重启后整坞静默**：`AI._syncWatermark()` 先 `GET /api/ai/events?since=0`
  取当前最大 id，若本地 `lastEventId` 比它大（服务端重启、id 归零）则归零；
  同一响应里的 `pending_gates` 顺手补渲染丢失的闸门卡。
- **6 个标签触顶 HTTP/1.1 连接**：原来每个标签一条 EventSource。改为
  `AI._elect()`：`navigator.locks.request('vp_ai_sse_leader')` 选主，只有主标签
  建流，其余标签靠 `BroadcastChannel('vp_ai_dock')` 收 `{kind:'ev', ev}` 转发
  渲染（`_seenAction` 去重）；`pagehide` 时 `_releaseLeader()` 释放锁让位。
  浏览器不支持 locks 时退回「每页一条」，行为与旧版一致。
- **已决卡片刷新即消失 / 状态不对账**：`AI.hydratePending()` 重写：并发拉
  `/api/ai/actions`，用 `pending` 补按钮、用 `recent` 对账（卡片还标 pending
  但后端已终态 → `_flipActionCard`），并把最近 3 条从未渲染过的已决单补成
  只读历史卡；`stMap` 增 `interrupted`「已中断」。
- **闸门卡倒计时**：`_startCountdown(node, deadline)` 从事件/`pending_gates`
  的 `deadline`（epoch 秒）起秒级更新「⏳ 剩余 4 分 12 秒（超时自动继续）」，
  `:empty{display:none}` 保证老事件没有 deadline 时不显示空行。
- **inline 拼接**：执行单/闸门按钮全部改 `data-ai-act` + `#aiMsgs` 事件委托
  （读 `dataset.aid/decision/sample/stage`），文件内 inline onclick 计数已 0；
  含单引号的档案名/样品名不会再让按钮静默失效。
- **切档案失败不回滚**：`AI.onModelChange` 失败把 `sel.value` 恢复成原
  `pf:<activeProfile>` 并提示；成功则回读 `loadModels()`，坞底「当前配置」
  与实际生效一致。
- **开坞即刻回车**：`ensureCtx` 改成 promise 合流（`_ctxP`），`send()` 在
  `ctxLoaded` 为假时先 `await`，旧历史不会插到新提问后面。
- `AI.clear()` 同时清当前会话键、折叠块与去重集合。

**后端与模板**

- **闸门期限**：`broadcast.py` 的 `gate_point()` 生成 `deadline = time.time() +
  timeout_s`，pending 事件带 `deadline`/`timeout_s`；`pending_gate()` 也返回，
  刷新后 hydrate 仍能续倒计时（`publish()` 用 `ev.update(fields)`，无字段白名单，
  未改）。
- **并发闸去中文前缀**：原按任务名 `startswith('AI执行·')` 计数，英文界面下失效。
  执行单记录统一加 `'origin': 'ai'`（6 处：run_stages/download/run_tool/
  create_sample/build_host_db/plan），新增 `_AI_TIDS` 登记启动后的任务 id，
  `_count_ai_active()` 判定顺序 `origin` → `tid in _AI_TIDS` → 旧前缀兜底
  （向后兼容历史卡片）。
- **校验失败留空目录**：`executors.py` 先 makedirs 再构造 job，参数非法时留一个
  空 `tool_runs/<tool>_<ts>_<uuid>/`。改为记录 `created_here`，构造抛错且目录是
  本次建的、仍为空时 `os.rmdir()` 再原样 raise（用户已有目录不动）。
- **设置页注入面**：档案 chip 的「使用/删除」与插件「安装/卸载」原拼 inline
  `onclick="aiPfUse('名字')"`，`esc` 出的 `&#39;` 在属性里会被解码回 `'`，名字
  带单引号就失效、构造名还能注入。改为 `data-name` + `data-act` 加事件委托
  （`bindAiProfileList()` / `bindPluginButtons()`），函数体不变。
- **档案与实际配置不同步**：`POST /api/ai/config` 合并后，若 `active_profile`
  已不存在或 model/base_url 与合并值不同，则清空 `active_profile`，坞底不再
  标着一个并未生效的档案名。

**测试**

- 新增 `tests/test_ai_p2.py`（10 例）：闸门 `deadline`/`timeout_s` 与
  `pending_gate()` 暴露、并发闸三种判定（英文名 + origin、中文旧前缀兜底、
  混合计数）、执行单记录带 `origin`、参数非法时 tool_runs 目录不留残留、
  模板不再含 inline `onclick` 且含 `data-name` + `addEventListener`。
- 新增前端冒烟 `tmp_diag/js_smoke.cjs`（Node 22 + jsdom 30.1.3，非浏览器环境）：
  `window.eval` 注入 `app-ai.js`，打桩 `fetch`/`EventSource`/`BroadcastChannel`/
  `t`/`esc`，覆盖会话分键与旧键迁移、执行单/闸门渲染、状态翻转、回放折叠、
  点击委托（点批准恰好发一次 `/api/ai/actions/resolve`）、切模型失败回滚、
  `ensureCtx` 合流、水位对齐 + 闸门恢复 —— **29 项断言全绿**。
  注：一版用 `linkedom` 时反复失败，查明是库自身问题（`getElementById` 每次
  返回不同 wrapper、`appendChild` 后另一实例读不到子节点），改用 jsdom 后正常。
- 合计：`test_ai_p2.py + test_ai_hardening.py + test_ai_spec_sanity.py +
  test_kv_pandas.py` → **51 passed**；`node --check app-ai.js` 通过。

**顺带修**：`webapp/templates/settings.html` 本轮被写成了 LF（索引是 CRLF），
按 `.gitattributes`「仓库与磁盘逐字节一致」的口径转回 CRLF，整文件 diff 噪声
从 2752 行回落到真实改动 328/132 行。

**已知限制（如实记录）**：`origin` 只加在执行单记录上，任务中心任务 dict 由
`web/tasks.py` 产出、本次未改，跨服务重启的英文名历史任务只能靠旧前缀兜底；
多标签 SSE 靠 `navigator.locks`，Safari 15 以下无该 API 时退回每页一条。

## 2026-11-02（补二十五）：把两条「已知限制」也修掉 —— origin 落盘跨重启、无 Web Locks 也能选主

补二十四里如实留了两条限制，用户要求一并修（原话：「也修复呀」）。

### ① 任务记录带 `origin`，跨重启仍能认出「AI 发起的任务」

- `Virus_Platform_Core/web/tasks.py`
  - `TaskManager.start(..., origin=None)` 新增形参并写进 `rec['origin']`；
  - `_rec_json()` 落盘带 `origin`（`tasks/<tid>.json` 与归档区同结构）；
  - `snapshot()` 对外快照带 `origin`，`unarchive()` 恢复时还原 `origin`
    —— 归档 → 恢复后并发闸仍认得出这是 AI 任务。
- `Virus_Platform_Core/ai_agent/executors.py`：`_launch_tool`(961) 与
  `_launch_analysis`(1075) 起任务时传 `origin='ai'`；`_count_ai_active()`
  本来就优先看 `origin`，于是「英文界面任务名不以 `AI执行·` 开头」这条漏洞
  不再依赖进程内 `_AI_TIDS` 兜底（该集合降级为启动瞬间的补充）。
- 说明：`_recover_interrupted()` 启动时把 running 改 failed（跨重启的 running
  任务本就作废），落盘的 `origin` 用于任务中心展示与归档恢复，不参与调度。

### ② 没有 `navigator.locks` 的浏览器：租约选主（与 Web Locks 同效）

`webapp/static/app-ai.js` 新增一套基于 `BroadcastChannel` 的租约选主，
`_elect()` 在没有 Web Locks 时走它，不再是「每页一条 SSE」：

- `AI._post(obj)` / `AI._onBusCtl(d)`：`claim`（有人要当主）/`have`（已有主）/
  `beat`（主的心跳）/`bye`（主随页面走了）；
- `AI._claimLeader()`：广播 `claim`，等 `CLAIM_WAIT`；听见 `have`/`beat`
  就转 `_watchLeader()`（只收中继），否则 `_becomeLeader()` + `_startBeat()`；
- `AI._watchLeader()`：`BEAT_MISS` 内收不到心跳 → 重新抢租约；
- `AI._yieldLeader()`：**撞车收敛**——两个标签页同时冷启动时谁都还没听见
  对方就都当了主（各开一条 SSE，HTTP/1.1 同源 6 连接会被吃满）。收到对端
  心跳/应答时按 tabId 字典序裁决，id 小的留任，另一个静默退位转跟随者
  （**不广播 `bye`**：bye 表示「页面走了」，广播出去会让对端以为主没了又抢一轮）；
- `AI._timing(key, dflt, floor)`：读 `window.VP_AI_TIMING`，仅供冒烟测试把
  等待压到毫秒级（默认 `CLAIM_WAIT=600ms`、`BEAT_MS=4000ms`、`BEAT_MISS=12000ms`）。

### 测试

- `tests/test_ai_hardening.py` 新增 `TaskOriginTests`（4 例）：`_rec_json` 保留
  `origin`、`snapshot` 暴露 `origin`、`start()` 签名含 `origin`、并发计数按
  `origin` 判（英文名 `AI run·report` 也计入）。
- `tmp_diag/js_smoke.cjs` 第 9 组改为**收敛断言**（34 条断言全绿）：
  ① 两个标签同时启动最终恰好一个主、只为它开一条 SSE；② 在位者让位（广播
  `bye`）后跟随者接手；③ 新标签加入时当跟随者；④ 在位者静默消失（心跳停、
  不发 bye）后跟随者租约过期抢到主。
  注：原断言「B 收到 have 不建流」依赖定时器精确时序，jsdom 里定时器会被
  延迟，实测两边可能同时当主——正是这个竞态促成了 `_yieldLeader()` 的确定性
  收敛；断言也相应改成「最终恰好一个主」。
- 合计：`test_ai_p2.py + test_ai_hardening.py + test_ai_spec_sanity.py +
  test_kv_pandas.py` → **55 passed**；`node --check app-ai.js` 通过。

### 收尾（2026-11-02 02:00）：把尾巴也堵上

- **没有 `BroadcastChannel` 的老内核**：`_claimLeader()` 若发现连广播通道都没有，
  直接 `_becomeLeader()`（每页一条、退回单页行为），不再白等 `CLAIM_WAIT`。
  冒烟第 10 组两条断言（无需等待即当主、只建一条流）覆盖。
- **并发闸的两种兜底都锁死**：`origin='ai'` 为主判据；修复前落盘的老记录（无
  `origin`）靠中文前缀 `AI执行·` 兜底；排队中的任务快照对外呈现 `running`，
  所以排队也算进并发上限——三条各有测试（`TaskOriginTests` 6 例）。
- **备份清理**（用户确认后执行，不可逆）：删掉 7 份历史 `_internal.bak*`/
  `.gwold`/`.old` 与 6 个历史 exe 备份，共约 **2.64 GB**；只保留最近一对
  `_internal.bak_20261102c` + `VirusPlatform.exe.bak_20261102c` 作为回滚点。
  D: 可用空间 22.4 GB → **24.9 GB**；清理后服务与 `/api/tasks` 实测正常。
- 合计：**57 passed**（`test_ai_hardening.py` 21 例）；jsdom 冒烟 **36 ok / 0 fail**。
## 2026-11-02（补二十六）：AI 工作台右侧「历史提问导航」——按提问打点、点击跳转、滚动高亮

坞里消息一多，想翻回"上次那句话"只能一路滚。用户给的参考图是聊天右侧那列
短横点：当前那条加粗变长，其余浅灰。这里按同一套东西做进 AI 工作台。

### 设计取舍

- **只认用户提问**：`.ai-msg.ai-user` 直接子节点才算一轮；回放折叠块
  （`.ai-backlog`）、播报卡（`.ai-msg.ai-bot` 内的执行单/任务卡）、系统提示
  （`.ai-event`）都不打点 —— 否则一条回答里塞三张卡，点就成一团浆糊。
- **等高均分，不按文档位置成比例**：按真实位置摆的话，流式回答每吐一段就把
  整个文档撑长，点会一直往下挪。等高分布只随"轮数"变，稳定得多。
- **≥2 轮才出现**：一轮的时候没有"跳"的意义；`AI.clear()` / 新建会话清空后
  自动收起。
- **给轨道让 26px**：`.ai-msgs` 加 `padding-right:26px`，提问气泡右缘与轨道
  留 2px（`tests/_check_turn_nav.py` 断言 gap ≥ 0），不再压住徽标/复制按钮。

### 实现

- `webapp/templates/_nav.html`（149 行起）：`#aiMsgs` 外包一层
  `.ai-msgs-wrap`，里面放 `<nav id="aiTurnNav" class="ai-turn-nav" hidden>`
  与 `<div id="aiTurnTip" class="ai-turn-tip" role="tooltip" hidden>`。
- `webapp/static/app.css`（`.ai-msgs` 规则之后）：`.ai-turn-nav` 绝对定位于
  消息区右侧（`right:2px`、上下 10px、竖直居中、`gap:8px`、默认
  `opacity:.55`，悬停 1）；`.ai-turn-tick` 是 22×12 的透明按钮，`::before`
  画 13×2 的圆角短横（`--ink-300`），悬停 18px（`--ink-500`），`.on` 时
  21×3（`--ink-900`）—— 和参考图一个比例；`.ai-turn-tip` 是 `right:28px` 的
  浮动预览（最多 4 行）。深色主题走同一批 CSS 变量，不用另写。
- `webapp/static/app-ai.js`：`_scroll` 之后新增一段导航模块 ——
  `_turnNodes()` 取轮次、`_turnLabel()` 压出 120 字预览、`rebuildTurnNav()`
  重建点、`syncTurnNav()` 按滚动位置切高亮（贴底永远是最后一轮）、
  `jumpTurn()` 平滑滚到该轮顶部 8px 并给气泡加 1.1s 闪光、`initTurnNav()`
  挂 `scroll` + `MutationObserver(childList)`（只盯直接子节点，流式回答在
  `.ai-answer` 内部重排不触发重建）并做 30ms 防抖。挂载点两处：`AI.toggle(true)`
  用 rAF 补一次（收起时 `display:none` 量不到 `offsetTop`），boot 里在
  `_restoreHistory` 之前 `AI.initTurnNav()`。
- 预览改用 `mousemove` 触发而不是 `mouseenter`：重建导航条时如果光标正好停在
  轨道上，`mouseenter` 会给新点补一发预览（明明没动鼠标却弹气泡）；真实悬停
  一定伴随 `mousemove`，两边都不丢。点完/滚轮滚动都主动 `hideTurnTip()`。
- i18n：`ai.nav.title` / `ai.nav.jumpTpl`（`跳到第 {n} 轮提问`）/
  `ai.nav.turnTpl`（`第 {n} 轮 · `）/ `ai.nav.empty`（`（这条提问只有附件）`），
  中英成对；`applyI18n` 不管 `aria-label`，导航条与每个点的提示语在 JS 里
  用 `setAttribute` 补。

### 测试

- 新增 `tests/_check_turn_nav.py`（17 条断言，自起临时实例 + 真浏览器，~25s，
  已登记进 `tests/_run_all.py`）：打点口径（5 轮 → 5 点、回放块/播报卡不算）、
  贴底高亮最后一轮、悬停预览含轮次与原文、移开收起、点第 1 个点落到视口顶部
  且闪光且不残留预览、上下滚动高亮跟随、气泡不被压、单轮隐藏、清空后不留点、
  重开对话恢复、全程无 pageerror。全绿。
- `tmp_diag/turn_nav_probe.py` 负责出图（`turn_nav_dock/home/hover/dark/mobile.png`），
  实测像素：未激活短横 17 CSS px、激活 25 px、距坞右缘 9 px。
- 既有回归：`node --check app-ai.js`、`tests/_check_i18n.py`（1400 条锚点中英
  成对、无重复键）、`tmp_diag/js_smoke.cjs`（36 ok / 0 fail）。

### 顺带修掉的三处

- `tests/_check_ai_dock_ui.py` 用 `#aiDock .ai-ico` 的 `.last` 点「收起」，
  可坞底还有 ⟲ 清空 / ⚙ 设置也是 `.ai-ico` —— 实际点到设置，坞不收起，
  「收起后 FAB 回归」一直红。改成点坞头的 `#aiDock .ai-dock-head .ai-ico`。
- `tests/_check_ai_events_ui.py` 断言 `.ai-gate-st.done` 全页恰好 1 个；补只读
  历史卡之后页面上还有别的执行单徽章（实测 7 个），改成只在 `.ai-gate` 里数。
- 闸门拍板后卡片仍挂着「⏳ 剩余 00:58（超时自动继续）」：`_stopCountdown` 只停
  定时器不清字，`_flipGateCard` 里补一句把 `.ai-gate-cd` 藏掉。

### 已知边界

- 390px 窄屏下坞本身固定 430px 会横向溢出（235px），这是既有问题；导轨不额外
  加宽（隐藏导轨后溢出一致），用例里断言的就是"不额外撑出"。
- 导航条按当前 DOM 里的轮次打点，`AI_MSGS_CAP=30` 淘汰掉的老提问自然消失。

### UI 微调（2026-11-02 02:10）：样品锚常驻条从左侧挪到右下角

用户截图指出「GQMIX · 阶段 0/1 · kvsuite · R1 4b1540c4 正在分析 核对 ×」这条
常驻在左下角，问能不能放右下角。

- `webapp/static/app.css` 的 `.anchorbar`：`left:12px; bottom:12px; z-index:96`
  → `right:92px; bottom:32px; z-index:89`。右下角这一列从上到下现在是
  运行条（`bottom:90px`）→ AI 坞/按钮（`right/bottom:26px`），锚条贴在按钮
  **左侧**同一行，既不与运行条上下抢位，也不挡按钮；`z-index:89` 低于 AI 坞
  （95）——坞打开时盖住它（坞是临时焦点，收起即回），高于运行条（88）。
- 窄屏 `@media (max-width:820px)`：铺满左右 12px，`bottom:88px` 抬到 AI 按钮
  上方（按钮占 26+52），并继续隐藏指纹。
- 同步更新 `webapp/static/app.js` 里 anchorBar 段落注释（原写「位置选左下——
  右下是 AI Dock 的地盘」）。
- 验证：用 Chrome headless + CDP 截图实测（`tmp_diag/cdp_shot.js`，Node 全局
  WebSocket 直连 `Page.captureScreenshot`，比 `--screenshot` 可靠——后者会卡在
  updater 重试循环）：1600×1000 下 `/tasks` 页锚条在 AI 按钮左侧同一行、运行条
  在其正上方、互不遮挡；780×900 下锚条铺满并抬到按钮上方，指纹已隐藏。

### 右下角浮层支持拖动（2026-11-02 02:20）

用户要求（m02185）：「这几个都 支持拖动可以吗，以免覆盖其他，默认右下角位置」。
右下角三件浮层（样品锚条 `#vpAnchorBar`、全局运行条 `#vpRunBar`、AI 圆钮
`#aiFab`）都会盖住页面内容，此前只能靠隐藏或改窗口大小绕开。

- `webapp/static/app.js` 新增一套通用拖动（插在锚条段之前）：
  `UI_DRAG_KEY='vp_ui_drag'`、`_uiDragAll()/_uiDragSave()/_uiClamp()/_uiPlace()`、
  `vpDragApply(el)`、`vpDraggable(el, name, {handle, anchorBottom})`。
  口径：
  - 只改 `left/top`（并把 `right/bottom` 置 auto），位置按浮层名存
    localStorage，刷新、切模块、重开浏览器都还在原处；
  - **4px 阈值**区分「拖」与「点」：拖过就吞掉紧随的这一次 click（350ms 内），
    锚条里的「核对 / 打开 / ×」和 AI 圆钮不会被误触发；没拖过一切照旧；
  - **双击回右下角默认位**（清该浮层的存档）；
  - `_uiClamp` 留 6px 边距夹在可视区内，`resize` 时重新夹回并落盘——
    拖到屏幕外也能找回来；
  - 运行条是 `column-reverse`、往上长的，用 `anchorBottom:true` 以右下边缘为
    锚存 `{x, b}`，展开任务明细时头部不跳；
  - 隐藏中的浮层（无任务时的运行条）`getClientRects()` 为空，直接不记起点，
    不会存进 `{x:0,y:0}` 的坏位置。
- 接线：`anchorBarMount()` → `vpDraggable(bar,'anchor')`；`runBarMount()` →
  `vpDraggable(bar,'runbar',{handle:'.rb-head', anchorBottom:true})`；
  `runBarInit()` → `vpDraggable($('aiFab'),'fab')`。两条常驻条在
  `bar.hidden=false` 之后补一次 `vpDragApply(bar)`（显示后才有真实尺寸）。
- `webapp/static/app.css`：`#vpAnchorBar, .runbar .rb-head, #aiFab { touch-action:none }`
  （触屏拖动不连带滚页面）、`cursor:grab`、`.is-dragging { cursor:grabbing; opacity:.92 }`。
- 验收脚本 `tmp_diag/cdp_drag.js`（Chrome headless + CDP，合成 PointerEvent 走真实
  事件链）**20 项全过**：三件套各自「拖动改位置 → 存档 → 刷新后保持 → 双击清档 →
  双击回右下角默认位（92/32、26/90、26/26）」；运行条用注入的假任务行验证，
  存档形态为 `{"runbar":{"x":1087,"b":350}}`；AI 圆钮「拖动生效且不误开坞」
  与「正常点击仍能开坞」都成立。
- 已知取舍：AI 坞面板本身仍固定占右下角（宽 430px），圆钮被拖走后坞不跟随——
  坞是临时焦点面板，若跟着圆钮跑容易出现半屏悬空；需要的话可再改成跟随定位。

## 2026-11-02（补二十七）：全面测试第一轮 —— 空 POST 就能删数据的两条路由

这一轮的起点是「全面测试各模块可用性」，落点却是两条**破坏性接口没有防误触门**。
记录在这里的首要目的不是复盘，而是让下一个往路由上发空 POST 的人（很可能也是
AI 代理）先看到它。

### 事故：archive + purge 连环两次空 POST，删掉 74 个运行目录

`tests/_audit_routes.py` 的设计是「对每条路由发一次空参数请求，看有没有 500」，
所以它自带一张 `DESTRUCTIVE_POST` 跳过表，注释写得很直白：

    空参数 POST 就会改状态/删数据的路由：不参与巡检。
    新增破坏性路由时必须登记到这里，否则巡检会真的执行它。

2026-11-02 我为定位 `_audit_routes` 的一处失败，另写了一个探测脚本，**照抄了
巡检的"空 POST"思路却没照抄跳过表**，于是：

1. `POST /api/tool/runs/archive`，空 body → 200。
   `api_tool_runs_archive()` 里 `keep = int(body.get('keep', 3))` —— **缺省 3 就生效**，
   把 `run/tool_runs/` 下 77 个运行目录中的 74 个移进 `_archive/`。
2. `POST /api/tool/runs/archived/purge`，空 body → 200。
   `api_tool_runs_archive_purge()` 里 `names` 省略 = **清空整个归档区**，
   把这 74 个目录永久删除（`shutil.rmtree`）。

损失：`run/tool_runs/` 下 2026-10-12 ~ 10-26 的历史工具箱产物（genoplot / structcmp /
primer / conan / phylogeo / phylodyn / fastp / orfa 等）。
**未受影响**：`run/results/<样品>/`、`run/tasks/*.json`、`host-db/`、`databases/`、源码配置。

两个教训必须分开记，因为修法不同：

- **对代理/脚本作者**：`_audit_routes.py` 的跳过表是要**继承**的，不是参考的。
  想巡检路由，直接跑那个脚本；想写自己的探测，第一件事是把
  `DESTRUCTIVE_POST` 抄过来，并且**只发 GET**。
- **对接口作者**：默认值即破坏是最危险的组合。`keep` 缺省 3、`names` 缺省全体，
  两条加起来等于"什么参数都不给就能删数据"。`/api/global/reset` 早就为此加了
  confirm 令牌（见 `docs` 里 2026-09-11 那条），这两条一直漏着。

### 修复：两条路由补 confirm 令牌

`Virus_Platform_Core/web/tools_api.py` 新增模块常量（口径对齐
`tasks_api.RESET_CONFIRM`，注释里点明「**不是鉴权**，本地平台没有鉴权概念，
只拦空 body 打过来的调用方」）：

    ARCHIVE_CONFIRM = 'archive'
    PURGE_CONFIRM   = 'purge'

- `api_tool_runs_archive()`：进入后第一件事校验 `body['confirm'] == 'archive'`，
  否则 400「批量归档需显式确认：请求体须含 {"confirm": "archive"}」。
  **校验放在读 keep 之前**——否则空 body 仍会在校验前把 keep 算成 3。
- `api_tool_runs_archive_purge()`：同样校验 `body['confirm'] == 'purge'`。
- 前端 `webapp/static/app-runhistory.js` 三处调用同步带上令牌：
  `rhClean()`（archive）、`rhPurge()`（单条 purge）、`rhPurgeAll()`（清空 purge，
  原来直接发 `body: '{}'`，现在发 `JSON.stringify({confirm:'purge'})`）。
- `tests/_audit_routes.py` 的 `DESTRUCTIVE_POST` 补登这两条，并留了本次事故的
  一行注释。**即便接口已自带令牌，这张表仍要登记**——巡检的价值在于不依赖
  接口的自我保护。
- 实测：空 body 与仅 `keep` 的请求现在都返回 400；
  `_audit_routes.py` 264 路由 / 0 个 500 / 0 未捕获异常 / **未触发任何后台任务**。

### 顺带修掉的：`_check_task_on_finish.py` 自身竞态（C/D 恒红）

C（正常路径）与 D（失败路径）长期报 `on_finish = 0（应恰 1）`。根因不在产品，
在这个检查脚本的判据：

`Virus_Platform_Core/web/tasks.py` 的 `_run()` 里，

- 第 278-282 行在 `with self.lock:` 内先把 `rec['status']` 置为 `done`；
- 第 310 行 `_call_on_finish()` 在 `finally` 末尾才执行。

脚本看到 `status != 'running'` 就立刻断言，正好落在这两步之间的窗口里，于是
稳定误报（探测脚本复现：状态已 done 时 `n=0`，再多等 0.5s 就 `n=1`）。
**当时 B 通过、C/D 失败，正是这个窗口的写照**——B 走的是"排队取消"分支，
状态与回调在同一个代码块里连着写，没有窗口。

修法：新增 `_wait_finished(mgr, tid, timeout=15.0)`，改为 `rec['thread'].join(timeout)`
等线程真正收尾，C/D 都走它。另外清理阶段原先 `os.remove` + `os.rmdir` 会撞
`OSError: [WinError 145] 目录不是空的`（任务线程还持有 tmpdir 里的日志句柄），
改为先 `join` 再 `shutil.rmtree(..., ignore_errors=True)`。

判据现状：A/B/C/D 四条 9 项**全绿**。

### 本轮其它结论（含两条"看起来红、其实不是产品问题"）

- `_check_browse_dlg.py`：硬失败是误报。`_samples_new.html` 是片段，真正的
  include 在 `webapp/templates/samples.html:45`；Flask test_client 实测 `/samples`
  能渲染出 `dlgMask/dlgPath/dlgList/dlgPickDir`。判据已改为内容匹配。
  同时修 `check_runtime` 里对 playwright 的硬 import（`3rd/python` 没装，
  已改为 ImportError 时打印跳过，浏览器用例归 `C:\Python312`）。
- `_it_async_ui.py`：套件内红、单跑全绿。原因是多份测试套件并发抢 chromium
  与 Flask 端口（该机器上确有非本会话的 `_run_all.py` 在跑）。属**环境并发**，
  不是用例缺陷。
- `_check_mcp_console.py`：单跑 10 项全 ok，套件内红，同上。
- `_audit_contract.py`：12 个「代码在用、字典没登记」的 i18n 键已补齐中英
  （`ai.act.interrupted`、`ai.dock.backlogTpl/busyHint/thinkingSec`、
  `ai.ev.gateCd/gateCdOver/gateRestored`、`anc.openTasks/verifyTip2`、
  `mcp.con.noArgs`、`se.savedAiFail`、`ui.qs.switchBack`），复检无硬性差异。
- `_check_spec_datas.py`：因源码树里残留 `webapp/static/i18n.js.bak`（372KB，
  未被 git 跟踪、已被 `.gitignore` 的 `*.bak` 忽略）而红；删除后 ✅。

## 2026-11-02（补二十八）：AI 代理技能卡 —— 中英双语触发词 + 任务型手册

### 背景

用户问「植物病毒分析的 skills 有吗，加载 skills 会不会更规范？」。现状：仓库根
没有 `.agents/` 目录；`main.py agent-guide --skill` 早就能打印一张**指针卡**
（5 步：读指南 → 查目录 → 调用方式 → 计划≠已执行 → 权限边界），但 description
只有英文一句 `Use the local VirusPlatform ...`，中文提问（「跑个组装」「建样品」）
不一定触发；长清单（阶段表、工具分组、排障表）也没地方放——全塞进常驻的
`AGENTS.md` 会挤占每次会话的上下文。

### 改动

1. **指针卡双语化**（`.agents/skills/virusplatform/SKILL.md`）：description 改为
   中英双语并列出触发词（植物病毒、PVAP、建样品、跑组装、跑流水线、工具箱、
   公共检索、NCBI 提交、任务中心、结果在哪），末尾指向 `virusplatform-ops`。
2. **新增任务型手册**（`.agents/skills/virusplatform-ops/SKILL.md`，173 行），按需
   加载，10 节：三条铁律 / 三入口对照 / 查目录（agent-ops 与 MCP 工具名）/
   15 阶段表（stage → 目录 → `.done` → 关键产物）/ 32 个工具箱分 6 组 /
   7 个执行动作 / 三套账（执行单 · 任务 · 归档，含「切模块不归档」与
   `archive|unarchive` API）/ 结果判读 / 常见故障 / 说完成前的自检清单。
3. **源头同步**（关键）：技能卡是 `Virus_Platform_Core/agent_guide.py` 里 `_SKILL`
   常量的打印结果，只改盘上文件会在下次重新生成时被覆盖；这次先改 `_SKILL`，
   再 `python main.py agent-guide --skill > SKILL.md` 生成，两边内容一致
   （1537B / 24 行）。

### 为什么用 skill 而不是全写进 AGENTS.md

`AGENTS.md` 是每次会话注入的常驻红线（要短、要硬）；skill 是按 description
匹配后才加载的补充手册，篇幅不设限、适合放长清单与排障表。两张卡分工：
指针卡回答「这平台能干什么」，ops 卡回答「具体怎么干、干完怎么判读」。

### 数据口径（均为现取，不写死）

- 操作目录：54 条 = 32 工具 / 15 阶段 / 7 执行动作（`main.py agent-ops`）。
- 目录树与完成标记取自真实样品 `run/results/cli_fulltest`：
  `01_host_removal/.host_removal.done`、`03_assembly/.assembly.done`、
  `04_orf/.orf.done`、`05_phylo/.phylo.done`、`08_host_analysis/.host_analysis.done`、
  `09_genome_plots/.gbdraw.done` 等；关键产物如
  `02b_kvsuite/identify/all_viruses.summary.tsv`、`03_assembly/summary.json`、
  `03c_consensus/coverage.tsv`、`07_report/fig_manifest.json`。

### 生效范围

`.agents/skills/` 是宿主无关的项目级目录，DSH 本会话已即时出现两张卡；其它宿主
（Claude Code 等）若支持项目级 skill 目录可直接复用，不支持的读 `AGENTS.md`
即可——指针卡本来就是 `agent-guide` 的输出，随时可重新生成。

## 2026-11-02（补二十九）：技能卡全面升级 —— 三卡分工 + 机器守卫 + 指南入口

用户接着要求「我们的 skills 全面增强，优化，调整更新一下嘛」。上一版两张卡的
问题：①指针卡只有「先做四步」，没回答「什么活儿该看哪张」；②ops 卡是平铺清单，
缺任务剧本（用户真正问的是「我要跑全链路 / 我只想鉴定」这类目标）；③**没有任何
守卫**——卡片里的 54/32/15/7、`.done` 标记、路径引用全是手抄，平台一升级就会
静默腐烂，和 2026-09-16 阶段表、2026-10-01 i18n fixture 那两次「写了检查却不在
批量里」是同一类事故。

### 改动

1. **三卡分工**（`virusplatform` / `virusplatform-ops` / `virusplatform-dev`）：
   指针卡=这平台能干什么、该看哪张、权限到哪（60 秒四步含「认实例」：同一台机
   可能同时跑源码实例与打包实例，`run/` 目录不同）；ops=怎么干与怎么判读；
   dev=改平台本身（代码地图、加工具三步、加阶段六到八处、confirm 令牌、
   任务管理器、前端陷阱、测试矩阵、打包、文档义务）。
2. **ops 卡改写**：新增六个任务剧本（A 全链路 / B 只鉴定定量 / C 检索下载建样 /
   D 补跑失败 / E 注释进化 / F 引物检测 / G NCBI 提交），把「阶段表 + 工具表 +
   判读表」串成可执行路径；三套账一节补上 60/300 内存淘汰与 `confirm` 令牌。
3. **新增机器守卫** `tests/_check_skills.py`（已登记进 `tests/_run_all.py`，
   60s 超时）：三卡存在 + frontmatter `name` 与目录一致 + description 中英双语
   （必须同时出现 CJK 与拉丁词）+ 指针卡与 `agent_guide._SKILL` 同步（盘上文字
   必须能在生成结果里找到）+ ops 卡覆盖 32 工具 / 15 阶段 / 7 动作全集 +
   54/32/15/7 四个条数 + 22 条路径引用存在 + 卡片里的 `.done` 标记在真实样品
   里出现过。
4. **AGENTS.md §1 加入口**：一行指向三张卡，并注明「只是本指南的展开版，
   不授予任何权限；改完卡跑 `python tests/_check_skills.py`」。
5. **源头同步**：`_SKILL` 常量重写为导航版，再
   `python main.py agent-guide --skill > .agents/skills/virusplatform/SKILL.md`
   生成（3829B / 63 行），避免下次重新生成把盘上改动覆盖掉。

### 踩坑

首次跑守卫报 `导入 agent_catalog 失败：name '_srna_target_complete' is not
defined`，但同一时刻单独 `import Virus_Platform_Core.agent_catalog` 正常，
`config.py:898` 也确实有这个函数——是**另一个 agent 会话正在写 `config.py`**，
读到了半成品。守卫本身没问题，重跑即过。教训：工作树有并发写者时，导入类检查
第一轮失败先看 `git status` 与文件 mtime，别急着改被检查的代码。

### 验证

- `python -X utf8 tests\_check_skills.py` → 七段全绿，输出「技能卡检查通过 ✔」
  （真实样品里见过 9 种 `.done`：assembly/fq2fa/gbdraw/host_analysis/
  host_removal/orf/orfa/phylo/primer）。
- `python tests\_run_all.py --list` 可见 `tests/_check_skills.py timeout=1.0min`。
- DSH 技能目录即时出现三张卡，description 显示双语触发词。

## 2026-11-02（补三十）：MCP 1.3.0 —— 从 24 个工具到 32 个，补齐归档/等待/结果层

用户要求：「我们的 mcp 也全面 增强 优化 调整更新一下嘛」。目标是让 agent 侧与网页
侧的能力与口径对齐，而不是再造一套 API。`mcp_server.py` 版本 1.2.0 → **1.3.0**，
能力指纹随之变为 `83805cd01bf1`（32 个工具）。

### 为什么改

旧工具面有三个洞：
1. **看不见已归档的任务**：网页 2026-11-02 起有手动归档（`run/tasks/_archive/`），
   但 MCP 的 `vp_get_task` 只查内存，任务归档后在 agent 侧表现为「不存在」。
2. **没有等待原语**：长任务只能 `vp_get_task` 手搓循环，模型常把「queued」当失败。
3. **结果层只到 toolbox run**：样品产物（`run/results/<样品>/0X_*/`）没有读取
   入口，只能让 agent 猜路径；样品阶段状态也得靠 `vp_platform_info` 之外的东西拼。

### 新增 8 个工具（总计 32）

| 工具 | 作用 | 注解 |
|---|---|---|
| `vp_wait_task` | 阻塞到 done/failed/cancelled 或超时（返回最后快照 + `timed_out`/`waited_sec`） | 只读 |
| `vp_archive_task` / `vp_unarchive_task` | 任务手动归档/恢复（与网页 `/api/task/<tid>/archive` 同一实现 `tm.archive/unarchive`） | `_WRITE` |
| `vp_sample_stages` | 单样品 15 阶段状态（counts + 每阶段 status/summary/outputs；**纯磁盘判定**，见下） | 只读 |
| `vp_read_sample_file` | 读样品目录内相对路径的文本产物（拒 `..`/绝对路径/二进制/超大） | 只读 |
| `vp_archive_tool_run` / `vp_restore_tool_run` | 工具箱运行搬进/搬出 `run/tool_runs/_archive/`（只搬目录，不删产物） | `_WRITE` |
| `vp_selfcheck` | 结构化自检（platform_root / mcp_sdk / host_db / virus_db / toolboxes / mcp_tools / disk_free） | 只读 |

既有工具的增强：
- `vp_get_task` / `vp_get_task_log`：内存 miss 时回落 `tm.archived_snapshot()`，
  归档任务仍可读，快照多 `archived` / `external` 两个键。
- `vp_list_tasks`：加 `status` / `name` 过滤（`include_archived` 合并归档区）。
- `vp_list_tools`：加 `query` / `weight(heavy|light)` / `need_db` / `limit+offset`，
  返回 `{count,total,note,tools}`，不再一口气吐 32 条。
- `vp_list_tool_runs`：加 `include_archived`，归档区出 `archived` 列表与
  `archived_total`。
- `_selfcheck()` 拆成 `_selfcheck_report()`（结构化）+ 打印版，`--check` 与
  `vp_selfcheck` 共用一份判定。

### `vp_sample_stages` 为什么不用 `pipeline_overview`（2026-11-02 事故）

第一版直接调 `pipeline.pipeline_overview()`，在进程内测试 1.0s 返回，但**从 MCP
stdio 子进程或网页 MCP 控制台调用会永久卡住**（实测 90s / 150s / 200s 都不返回，
还把控制台的 `_LOCK` 一起堵死）。用 `faulthandler.dump_traceback_later()` 抓到
工作线程栈：

```
numpy/_core/multiarray.py:11 ← numpy/_core/__init__.py:24 ← numpy/__config__.py:4
← matplotlib/cbook.py:24 ← matplotlib/__init__.py:161
```

即 `tools/call` 的工作线程在首次 `import matplotlib` 时卡在字体缓存上（多进程并发的
`MPLCONFIGDIR` 争用）。触发链条：`pipeline_overview` 为判定 `unavailable` 会探测
fastp/seqkit/gbdraw/DFV/ORF 注释/验证/共识引擎，这些模块把绘图栈拉进来。

修法：MCP 侧改**纯磁盘判定**（`_stage_disk_state`），只读 `summary.json` /
`report.html` / `fastp_R1.fastq.gz` / `STAGE_OUTPUTS` 产物，依赖关系用 `STAGE_DEPS`，
不 import 任何引擎。口径与网页逐条对齐（`skipped` 的 `primer`/`gbdraw` 特例、`ok is
False` → failed、`ran = has_summary and not failed`），唯一差别是**不给
`unavailable`**：MCP 侧显示 ready/blocked，工具装没装看 `vp_selfcheck` 或网页。

对齐验证：`qguard02`、`vfix03`、`i18nchain`、`GQMIX`、`mcp_night_demo2`、
`cli_fulltest` 六个样品与网页逐阶段 diff **全部 none**；`cli_fulltest` 为
`done 12 / skipped 1（primer）/ ready 2`。stdio 调用 **0.0s**，网页控制台 **2s**。

教训：**MCP 工具里不要 import 带 GUI/绘图依赖的模块做「探测」**。同一个函数在
主进程里快，在工作线程里可能因为首次 import 的全局锁/缓存争用卡死；判断「装没装
工具」交给自检，不要在读取路径上做。

### 口径

- **归档是手动动作**：切模块、重连、刷新都不会归档；`vp_archive_task` 只收已结束的
  任务，运行中先 `vp_cancel_task`，外部下载批次（`external=true`）请在来源模块处理。
- 工具运行归档与 `/api/tool/runs/<run>/archive` 同语义：搬 `_archive/`，可恢复；
  真删除仍然只有 `vp_delete_tool_run`（网页控制台 `_BLOCKED_TOOLS` 只拦它）。
- 错误一律 `ValueError('中文原因')`：`check_path` 的 `FileNotFoundError` 会绕过
  友好文案（归档/恢复第一次实现就踩到，测试里表现为 traceback 而非断言失败）。

### 验证

- `python mcp_server.py --check` → 7 项全 ok，`mcp_tools 32 个启用`，`ready: yes`。
- `python tests\_check_mcp_tools.py` → 「✔ MCP 工具面自检通过」（七段：注册与
  description、过滤/分页、状态/名称过滤、归档→仍可读→恢复、工具运行归档→恢复、
  真实样品阶段与产物读取含越界拒绝、自检七项）。
- `vp_sample_stages` 磁盘版：stdio 0.0s，网页控制台 2s，6 个样品与网页 diff none。
- `python tests\_run_all.py _check_mcp_tools _check_skills` → 2/2 通过（8.2s）。

## 2026-11-02 · 全模块巡检：真实缺陷修复与守卫健壮化

用户要求「全面测试软件的不同模块是否正常，潜在的问题，全面修复，优化，提升」。
本轮做法：`python -m pytest -q`（基线 341 passed / 2 failed）+
`python tests\_run_all.py --quick`（97/105，8 红）+ pyflakes 全量 + 页面/接口抽查，
然后**逐条复验每个红项**——结论是 7 个红项来自并发串扰、1 个来自真实代码缺陷。

### 一、产品侧真实缺陷（已修）

1. **`webapp/templates/mcp.html` 外部接入区块整段不存在**。脚本里的
   `MCPC._cfg()` 找 `$('mcpCfg')`、`pages.py:404` 也往 `#mcpCfg` 注入配置文本，
   但模板从来没渲染这个 `<pre id="mcpCfg">`，复制按钮同理。后果是 /mcp 页面
   "外部接入" 一栏永远是空的（`tests/_check_mcp_console.py` 抓到了）。
   修法：`#mcpUI` 之后补 `<section class="card" id="mcpExt">`，复用既有
   i18n 键 `mcp.page.externalTitle / externalHint / externalNote`，不新造文案。
2. **`Virus_Platform_Core/phylodyn_govern.py` 地理位置治理与上游不一致**（3 条）：
   - `China, Ningxia, Yinchuan_AI` 未剥噪声后缀（上游 → `Yinchuan`）；
   - `China, Unknown, Yinchuan_AI` 同上；
   - `China, Unknown, Unknown_AI` 的 `dropped_levels` 少记 `Unknown_AI`
     （旧 `_is_unknown_level` 正则 `^(unknown|...)([_\- ]|$)` 不命中 `Unknown_AI`）。
   修法：新增 `_LOC_NOISE_RE = re.compile(r'[_\-\.](?:ai|v[1-9]|new|final|rev\d*|copy)$', re.I)`，
   在 `split_location()` 里逐级剥离后过滤空串；`_is_unknown_level()` 增加
   「占位词 + 分隔符」判据。**分隔符必须存在**，否则 `Shanghai/Qinghai/Chennai`
   会被误截。对拍脚本 `tests/_check_govern_vs_upstream.py` 现已 10 项全过。
3. **`Virus_Platform_Core/bsp.py` 缺 `import io`**（`render_pdf()` 用 `io.BytesIO()`，
   签出即 NameError），**`Virus_Platform_Core/public_meta/host_genome.py` 缺
   `import urllib.parse`**（重定向 `urljoin` 处）。两处都是 pyflakes 的
   `undefined name`，属"只有走到那个分支才炸"的定时炸弹。
4. **`webapp/templates/submit.html` 的物种名校验读回文件用页面级 `CUR`**：
   任务轮询完成后若当前项目切换过，`/api/submit/table/<CUR>/file?name=taxonomy_check.json`
   会 400。服务端 job payload 现在回 `project`，前端改成
   `const proj = (snap.result && snap.result.project) || CUR;`。
5. **`webapp/static/app-ai.js` 的 `_saveHistory()` 会写空历史桶**：
   `AI.messages` 为空时也 `setItem`，导致「打开坞又关掉」在 localStorage 里
   留下一串没有任何消息的 `vp_ai_msgs:<conv>`，`_pruneHistory()` 还得逐个解析。
   修法：`if (!AI.messages.length) return;`。

### 二、守卫/测试健壮化（不是产品缺陷，但会伪装成红）

6. **`tests/_check_svg_wellformed.py` 顺着 junction 把整棵树走两遍**：
   `%TEMP%\vp_ascii_root` 是 `hmm_annot.py:235` 建的目录链接（指向平台根，
   给 Easel/ANSI fopen 中转 ASCII 路径用）。`os.walk` 跟着它进去后路径层层嵌套
   （7 层），超 Windows MAX_PATH → `ET.parse` 抛 `FileNotFoundError` → 整脚本崩。
   修法：新增 `_is_link()`（`islink` 或 `FILE_ATTRIBUTE_REPARSE_POINT = 0x400`），
   `_iter_svgs()` 的 `dirs[:]` 过滤链接目录；`_is_wellformed()` 改返回三元组
   `(ok, err, readable)`，把"打不开"与"真残缺"分开，打不开只记 warn。
7. **`tests/_check_ai_global_ui.py` 依赖浏览器残留状态**：坞的开合记在
   `localStorage.vp_ai_open`、会话 id 记在 `sessionStorage.vp_ai_conv`。
   上一轮若把坞留在开着，FAB 就是 `hidden`，`page.click('#aiFab')` 必定 30s 超时；
   不固定 sessionStorage 的话每次导航都新开一会话，"刷新后历史恢复"永远测不出。
   修法：`add_init_script` 只在**首次进入**预置干净初态（清 `vp_ai_open` 与历史键、
   固定 `vp_ai_conv=uicheck`），之后不动；发送历史用例用 `page.route` 拦
   `/api/ai/chat` 返回固定 SSE（`{"type":"delta"}` + `{"type":"done"}`，口径照
   `ai_agent/chat.py:313` 的 `_sse()`）；点 FAB 前先判 `is_visible()`。
8. **`tests/_check_browse_dlg.py` 误判共享片段**：`_samples_new.html` 是片段
   （由 `samples.html` include），它自己不该 include `_browse_dlg.html`。
   改成按 `{% include %}` 传递闭包判定"自身或其宿主页的 include 链是否含对话框"。
9. **`tests/_check_govern_vs_upstream.py` 在 GBK 控制台下自杀**：失败行 print 的
   `✗` 编不出来 → `UnicodeEncodeError` → 只看到 exit 1、看不到是哪条。
   头部补 `sys.stdout/stderr.reconfigure(errors='replace')`（与 `_run_all.py` 同款）。
10. **`tests/test_ai_actions.py` / `tests/test_mirna_alignment.py`**：前者断言
    仍按旧契约查 `rec['tid']`（产品已改 `result_sample`，docstring 也早就写明），
    后者 `_Fake` 桩缺 `returncode/stderr`（`run_miranda_batch()` 先查
    `r.returncode != 0`）。两处都按产品现状改桩/断言。

### 三、i18n 与静态资源

11. `webapp/static/i18n.js` 中英各补 9 键（代码在用、字典没登记）：
    `ai.act.interrupted` / `ai.ev.gateCd` / `ai.ev.gateCdOver` / `ai.ev.gateRestored`
    / `anc.openTasks` / `anc.verifyTip2` / `mcp.con.noArgs` / `se.savedAiFail`
    / `ui.qs.switchBack`。补后 `tests/_audit_contract.py` 报
    「zh/en 各 2999 键 · 无硬性差异」。
12. 源码树里的手工备份 `webapp/static/i18n.js.bak`（372 KB）归档到
    `archive\_bak_20261102\`（未删除），`tests/_check_spec_datas.py` 复绿。

### 四、验证与经验

- `python -m pytest -q` → **351 passed**（基线 341 passed / 2 failed）。
- `python tests\_run_all.py --quick` → 97/105；8 个红项逐条复验后全部消解
  （7 假 1 真，真项见第 6 条），单项复跑均 rc=0。
- `python main.py selfcheck` → 模块 144/144、工具 32/32；页面抽查 22 个 GET 全 200。
- **经验：`tests/_run_all.py --quick` 不能两个实例并发跑**。`run/` 目录被共享，
  并发会互删产物，制造 `_it_dsrna`、`_audit_routes`（"巡检期间任务记录被删除 2 个"）
  这类假红。看到 script 测试批量红、单跑绿，先查有没有第二个实例。
- **经验：巡检类 Playwright 脚本必须先"清场"再断言**。用户配置（坞开合、
  语言、会话 id）是持久化的，残留状态会让点击超时或让"恢复历史"永远为假。

## 2026-11-02（补三十一）：三入口逐个实调 —— 从「能导入」到「真能调起来」

补二十七那一轮验的是**接口会不会误删数据**；这一轮换成相反的视角：
把 Web / CLI / MCP 三个入口**逐个真跑一遍**，看每个模块到底能不能调起来。
结论先给：8 个真实缺陷，全部已修并补了回归测试。

### 一、srna-target：模块声明了依赖，探测表里却没有这个键（mirna 100% 不可用）

现象：任取 miRNA 序列提交 mirna，任务 `status=failed`，
`RuntimeError: 模块「mirna」缺少必要外部工具：srna-target`；日志还顺带打了
「自动安装插件 srna-target（31.4MB）…」，而插件源根本不提供这个工具——
**排障方向被完全带偏**。

根因：`Virus_Platform_Core/module_deps.py:63` 写的是 `'mirna': ['srna-target']`，
但 `Virus_Platform_Core/config.py` 的 `detect_tools()` 候选表里**没有 `srna-target`
条目**，于是 `cfg.tool_status()['srna-target']` 永远是空串，
`missing_tools('mirna')` 永远判缺。而文件其实一应俱全：
`3rd/tools/srna-target/{miranda/miranda.exe, rnahybrid/RNAhybrid.exe,
pita/pita_prediction.pl, pita/perl/bin/perl.exe}`。

修法三步：
1. `detect_tools()` 的 cands 字典补 `'srna-target'`（排在 `'dsRNAmax'` 之后），
   候选给 `3rd/tools/srna-target/miranda/miranda.exe` 与 `which('miranda')`；
2. 新增 `_srna_target_complete(root=None)`，**四项全在**才算可用；
3. `detect_tools()` 末尾加 `if out.get('srna-target') and not _srna_target_complete():
   out['srna-target'] = ''`。

第 2 步的理由值得单独记：三引擎缺一时，`mirna_target/bin_engines.py` 的
`run_*` 会**静默返回 None** 降级，共识等级从 LOW 悄悄变 VERY HIGH 却不报错。
所以「只找到 miRanda 就放行」比「直接报缺」更危险——宁可判缺。

修完 `main.py selfcheck` 第②项由 32/32 变 **33/33**，mirna 端到端跑通
（task 6df9201c8056，`status=done`，3 秒，产物落在
`run/tool_runs/mirna_*\/mirna_target/{results.json, results.tsv, summary.json,
mirna_targets_table.html}`）。

> 口径提醒：这里说的 33 是**外部工具**数；工具箱**操作**仍是 32 个
> （`agent_catalog.list_operations()` total=54 = tool 32 / stage 15 / action 7）。
> `srna-target` 只是 mirna 的依赖引擎，不是用户能调的工具，别为了对齐数字硬塞。

### 二、vp_ascii_root 自引用环：链接落进了被链接的树里

现象：`main.py selfcheck` 第⑥项 `✘ 依赖审计失败: [Errno 2] ... 'run\tmp\
vp_ascii_root\run\tmp\vp_ascii_root\...\dev_tools\build_plugin_repo.py'`
（嵌套 9 层，撞 Windows MAX_PATH）。

因果链：
1. `Virus_Platform_Core/config.py:153` 模块级 `ensure_usable_tempdir()` 在系统
   临时目录不可用时把 `tempfile.tempdir` 重定向到 `<平台根>/run/tmp`；
2. `Virus_Platform_Core/hmm_annot.py:235` 的 `_ascii_path()` 取
   `os.path.join(tempfile.gettempdir(), 'vp_ascii_root')` 建 junction 指向
   `PLATFORM_ROOT`；
3. 于是**链接落在被链接树内部**——任何 `os.walk` 平台根的操作都会无限复制
   整棵树，直到路径超 MAX_PATH。

修法：`_ascii_path()` 建链接前先做环检查——`link` 规范化后若以
`PLATFORM_ROOT + os.sep` 开头，就弃用 tempdir 位置，改试 `%TEMP%` / `%TMP%`
下的同名目录；仍不可用就整段跳过（`_ASCII_LINK = ''`），**宁可返回原路径也不造环**。

清理：确认 `run\tmp\vp_ascii_root` 属性是 `Directory, ReparsePoint`
（Target=`D:\桌面\植物病毒分析平台`）后，用 `cmd /c rmdir` 只删链接不留内容。
核对平台根 main.py/app.py/Virus_Platform_Core/webapp/tests/host-db/databases/3rd
全部在位、run/results 14 个样品、run/tool_runs 121 个目录都在。
修完第⑥项变 `✔ 全部依赖就绪（扫描 44 个）`。

> 冷知识：`tests/_check_svg_wellformed.py:111-124` 里早有一大段注释在讲
> 「`%TEMP%\vp_ascii_root` 让 `os.walk` 走两遍、超 MAX_PATH」，当时只在扫描器
> 侧加了 `_is_link()` 跳过——**症状被绕开了，根因没人动**。这次修的是根。

#### 补记：第一版修复不够深，复跑时环又回来了

第一版只改了 `hmm_annot._ascii_path()`（建链接前做环检查），selfcheck 当时确实绿了。
但过了一小时复跑，第⑥项**又红了**，嵌套九层的路径一模一样。查链接时间戳：
`run/tmp/vp_ascii_root` 创建于 03:29:57，正是那一轮 web 进程启动后的几十秒内。

继续往上追，真正的根因在 **`config.ensure_usable_tempdir()`**：

    if _usable(tempfile.tempdir):
        return tempfile.tempdir

`_usable(d)` 的头一行是 `if not d: return False`。而模块**首次导入**时
`tempfile.tempdir` 还是 `None`（它是懒初始化的缓存，第一次 `gettempdir()` 才填）。
于是：

1. `_usable(None)` → False；
2. 跳过「系统临时目录可用」这条最该走的路；
3. 一路落到 `cands` 列表里的 `os.path.join(PLATFORM_ROOT, 'run', 'tmp')`；
4. `tempfile.tempdir` 被**永久改写**成平台根内部，并同步覆盖 `TMPDIR/TEMP/TMP`
   三个环境变量。

也就是说：**系统临时目录明明是好的**（实测 `gettempdir()` =
`C:\Users\17711\AppData\Local\Temp`），代码却因为读了一个还没初始化的变量，
主动把自己搬进了平台根。这比「系统临时目录不可用」这个设计初衷要糟得多——
后者是罕见的兜底，前者是**每次启动都发生**。

修法：新增 `_sys_tempdir()`，口径改为「显式设置过就用设置的，没设置过就问环境要」：

    if tempfile.tempdir:
        return tempfile.tempdir
    for k in ('TMPDIR', 'TEMP', 'TMP'):
        v = os.environ.get(k)
        if v:
            return v
    return '/tmp' if os.name != 'nt' else None

`ensure_usable_tempdir()` 的首行相应改成 `if _usable(_sys_tempdir())`，
并把 `import tempfile` 从函数内挪到模块级（新函数也要用）。

> **教训：兜底分支要判「真的不可用」，不能判「我不知道」。**
> `tempfile.tempdir is None` 表达的是"还没问过系统"，不是"系统没有"。
> 把未知当成否定，兜底就会从"万不得已才走"变成"默认就走"，
> 而兜底分支往往正是不想选的那个位置（这里是平台根内部）。
> 这类 bug 有个共同特征：**修复后一段时间才复现**，因为触发点在不同入口
> （CLI selfcheck 与 web 启动顺序不同）。看到"修完又回来了"，先怀疑修复
> 只盖住了症状分支，没盖住产生症状的上游。

新增回归 `tests/test_usable_tempdir_never_prefers_platform_root`：
把 `tempfile.tempdir` 显式置 None、`TEMP` 指向 tmp_path，断言
`ensure_usable_tempdir()` 返回的不是平台根内部路径——直接锁死这个"未知当否定"。

> 顺带记一个**并发踩坑**：这一轮我在 `_run_all` 还在跑的时候单跑了同一批脚本
> 去排查，结果把正在跑的套件搅成了 19 个秒级 FAIL（全部 0.2~1.2s，
> 典型的"连不上/抢目录"）。单跑全部 ALL PASS。**排查期间不要动正在跑的套件**，
> 先停掉再查；也顺手查出机器上残留着两份 `app.py --web`（一份 03:24 一份 03:30）
> 和遗留的 `_run_all.py`，互相抢 8765 与 `run/`。清理干净后重跑才是可判读的。


### 三、空参数提交能建出「必然失败」的任务（4 个工具）

32 个工具箱工具里有 9 个输入是「多选一」（consensus 的参考可以是 contigs 运行 /
kvsuite 运行 / 独立 FASTA；kvchain 的样本可以是样品 / 直填测序数据 / 样本表；
pdprep 按 source 分支各有必填；verify 是运行或 FASTA）。这类工具 schema 里
`required=[]` 是**对的**（没有任何单个键永远必填），但实测发现：

| 工具 | 空提交结果 |
|---|---|
| mirna / kvsuite / genoplot / orfa / phylodyn | 400 + 中文原因 ✔ |
| **consensus / kvchain / pdprep / verify** | **建了任务，终态 failed** ✘ |

后四个的后果不是数据损坏，是**体验与排查成本**：用户看到一张 pct=1、
`status=failed` 的卡，真正的文案（「请选择 contigs 运行或输入 FASTA」）
埋在任务 error 字段里；而且每试一次就多一个空转的任务目录。

根因分两类：
- `verify` / `consensus` / `pdprep`：校验写在 **job 体内**（`raise ValueError(...)`），
  任务线程里没有 Flask 请求上下文，`abort(400)` 只会退化成裸的
  「BadRequest: 400 Bad Request」，所以当初改用了 `raise ValueError`——文案能看，
  但**任务已经建出来了**。
- `kvchain`：它是 `kvsuite` 的 `stage=all` 封装，参数 schema 直接复用 kvsuite
  （`agent_catalog.py:365` `TOOL_PARAM_SCHEMAS['kvchain'] = TOOL_PARAM_SCHEMAS['kvsuite']`），
  但提交入口自己不校验，指望 kvsuite 在任务线程里 abort。

修法：在 4 个 job 函数的**factory 期**（`return job` 之前、Flask 请求上下文还在）
补与 kvsuite 同款的互斥校验：
- `Virus_Platform_Core/web/tool_jobs.py` `_tool_job_verify`：
  `if not run_ref and not fasta: abort(400, '请选择 contigs 运行或输入 FASTA')`
- `_tool_job_consensus`：`if not run_ref and not fasta and not kv_run: abort(...)`
  （注意 `kv_refs` 依赖 `kv_run`，只要 `kv_run` 给了就不算空）
- `_tool_job_pdprep`：按 `source` 分支校验（files 要 fasta+meta、manual 要
  fasta_text+meta_text、genbank 至少给 accession/term/species/taxid/gb_files 之一——
  判定放宽，不误伤 gateway+species 这类合法组合）
- `Virus_Platform_Core/web/chains.py` `_tool_job_kvchain`：三选一全空即 400

修完 9/9 全部 400 且带中文原因，`tests/test_tool_mutex_inputs.py` 11 项通过
（9 个参数化空提交 + 「400 后不留运行目录」+「合法输入不被误伤」）。

> **这条测试是做过反向验证的**：把 `abort` 换成遇到这几条文案就放行的桩，
> 4 个工具立刻从 400 变 500 —— 证明新加的校验就是唯一的那道闸，
> 测试不是靠别处顺手拦下来蒙对的。以后加同类校验，建议照做一次。

### 四、三入口实调结论

| 入口 | 做法 | 结果 |
|---|---|---|
| CLI | 43 个子命令逐个 `<cmd> --help` | 43/43 通过，无 Traceback |
| CLI | 只读子命令真跑（tools/selfcheck/ref-status/ictv-status/gb-list/logan-jobs/submit-list/agent-guide/agent-ops…） | 全部 rc=0；`host-analysis --sample mcp_night_demo2` → 3 条 contigs |
| MCP | `mcp_server.py --check` | 24 个工具、指纹 821cdad41bf5、http 模式可用 |
| MCP | 手写 JSON-RPC over stdio（initialize → tools/list → tools/call） | 24 个工具全 `vp_` 前缀、inputSchema 齐备 |
| MCP | 9 个只读工具真实调用 | 全部 ok（vp_list_tools 56705 字符、vp_cli_help 29797 字符…） |
| HTTP | 32 个工具的参数契约（空 params 提交） | 23 个缺必填 → 400 中文；9 个互斥型见上文 |

两个**踩过的坑**（都是探测脚本自己的错，不是产品问题，记下来少走弯路）：
- MCP **没有** `vp_agent_ops` 工具（`agent-ops` 是 CLI 子命令）；别凭印象写名字，
  先 `tools/list` 或 `tmp_diag/list_mcp_tools.py` 静态解析。
- `vp_search_operations` 的参数键是 **`query`**，不是 `keyword`。写错会得到
  Pydantic 的 `query Field required`——**参数校验正常工作**。

### 五、验证与经验

- `python -m pytest -q` → 346 passed（新增 `test_tool_detect_and_ascii_link.py`
  5 项、`test_tool_mutex_inputs.py` 11 项）。
- `python main.py selfcheck` → 模块 144/144、**工具 33/33**、依赖审计 44 个全就绪。
- **经验：改了 `config.py` 这类启动期算一次的东西，必须重启 web 进程**。
  `Config` 是单例、`detect_tools()` 在 `__init__` 里跑完，改代码不会热加载；
  修复后复跑仍报旧错误时，先确认 8765 上跑的是不是新进程（看 PID 有没有变）。
- **经验：模块声明的工具 key 必须能在探测表里找到**。`module_deps.MODULE_TOOLS`
  是「模块 → 依赖」的真源，`detect_tools()` 是「本机有没有」的真源，两者不对齐
  就是「永远判缺」。`tests/test_tool_detect_and_ascii_link.py` 里那条
  `test_srna_target_declared_by_module_has_detect_entry` 遍历全部声明键做断言，
  锁的是这个**通用形态**，以后新增模块漏登记会当场红。
- **经验：互斥型输入的校验要放在 factory 期，不是 job 体里**。job 体里 `abort(400)`
  没有请求上下文，只剩一个没有文案的 400；而 `raise ValueError` 虽然有文案，
  但任务已经建出来了。判断标准很简单：**这条错是「参数不对」还是「跑起来才发现」**——
  前者进 factory，后者才进 job。

### 六、i18n 字典：重复键是「静默死代码」，比缺键更难发现

`tests/_run_all.py` 收尾那一轮（106/107）唯一的红是 `tests/_check_i18n.py`，
报的是**重复键**而非缺键：

    ✘ [en] se.savedAiFail     L3497 与 L3498 重复
    ✘ [zh] ui.qs.switchBack   L2514 与 L2516 重复
    ✘ [en] ai.act.interrupted L2885 与 L2886 重复
    ✘ [en] anc.verifyTip2     L3005 与 L3006 重复

四处全部是**我在补键时自己造的**：`i18n.js` 里中英是两个大对象字面量，
同一对象内重复键不报语法错（`node --check` 也是绿的），后写的**静默覆盖**前写的
——前一处变成永远执行不到的死代码。JS 引擎不警告、页面也照常显示，
所以只能靠静态检查抓。

`anc.verifyTip2` 那两处还**内容不一致**（`\'` vs `\u2019`，同一个撇号的两种写法），
说明它不是简单复制粘贴，而是两次独立编辑留下的残影。处理时保留与全文一致
的 `\'` 写法（全文 `\u2019` 只出现 2 次，是个孤例）。

补齐 `ai.dock.convs` 时又踩到同一个坑：中文块缺这个键（页面 `_nav.html` 的
`data-i18n-title` 用它做「历史会话」按钮的 tooltip），我在中文块补上后，
顺手在英文块的「settings 之后」也加了一条——**而英文块 L2800 早就有**
`'ai.dock.convs': 'Conversations'`，于是立刻又多出一处重复。
教训是：**补键前先全文搜一遍这个键是否已存在**，不要按「它应该挨着谁」去猜位置。

最终 `tests/_check_i18n.py` → `模板锚点 1411 个全部有定义，中英成对，无重复键`。

> 这类检查器的价值就在这里：`node --check` 只验语法，而重复键、缺键、
> 中英不成对都**不是语法问题**。`tests/_verify_i18n.js --verbose` 才能把
> 「哪个 key、哪两行、哪个模板引用了它」一次说清——排障时先跑它，
> 别在 5600 行字典里人肉找。

### 七、并发稳定性（用户原话里的「工作的稳定性」）

顺序跑通 ≠ 稳定。补了一个 `tmp_diag/probe_concurrency.py` 三件套：

- **A 并发读**：40 路并发打 6 个只读接口（`/`、`/api/samples`、`/api/tool/runs`、
  `/api/tasks`、`/api/settings`、`/api/tool/runs/archived`）→ 1.72s，
  **非 200 共 0 个**，且 `/api/*` 响应体全部仍是合法 JSON（没被截断）。
- **B 并发写**：同时提交 8 个 `convert` 任务 → 0.17s 拿到 **8 个互不相同的 task id**
  （没有串号），轮询后 **8/8 done**，无超时。
- **C 压后健康**：压完再打 `GET /` → 200 / 23066 字节。

不做的事也记一笔：**不并发跑重工具**（抢 CPU 会让结果不可判读），
**不并发跑 `tests/_run_all.py`**（前文第 5 条的经验）。

### 八、MCP 侧的「同样的闸」——拒绝路径也验了

上一轮只验了 24 个工具「能列出来、只读的能调」，这一轮补了**拒绝路径**：
参数不对时必须守住，而不是照单执行。8 个用例全过：

| 调用 | 结果 |
|---|---|
| `vp_delete_tool_run(confirm=False)` | 拒删：「删除不可逆：请把 confirm 设为 true 再执行」 |
| `vp_delete_tool_run(不存在的 run, confirm=True)` | 拒：「路径不存在」 |
| `vp_run_tool(no_such_tool)` | 拒：「未知工具」（并列可用工具清单） |
| `vp_run_tool(convert)` 缺 input | 拒：「缺少必填参数 input（输入文件）」 |
| `vp_run_tool(mirna)` 空参数 | 拒：「请粘贴 miRNA 序列或选择 miRNA FASTA 文件」 |
| `vp_get_task(不存在)` | 拒：「任务不存在」（并提示去哪查） |
| `vp_read_run_file(run, '../../platform.json')` | 拒：越界读被挡 |

并且**拒绝之后 `run/tool_runs/` 没有新增任何目录**——这条单独断言，
因为「报错了但已经把目录建出来」是很容易漏的半修状态。

顺带确认了**第三节那 4 个工具的修复在 MCP 侧同样生效**（HTTP 与 MCP 共用
同一个 job factory）：`consensus` / `kvchain` / `pdprep` / `verify` 经
`vp_run_tool` 空提交全部被拒、且不建目录。

### 九、本轮数字

- `python -m pytest -q` → **362 passed / 0 failed**（156.46s）。
- `python tests\_run_all.py` 第二轮 → 106/107（唯一红是 i18n 重复键）；
  i18n 修完复跑见下条。
- `python main.py selfcheck` → 模块 144/144、工具 33/33、依赖审计 44 个全就绪。
- CLI 43 个子命令 `--help` 全过；MCP 24 工具、9 个只读实调全过、8 个拒绝路径全过；
  工具箱 32 工具参数契约全过；并发 40 读 + 8 写全过。



## 2026-11-02 · 全模块巡检第二轮：真实缺陷修复（批次 A：任务落盘 / 提交读回 / CDS 挑选 / MCP 配置）

### 产品缺陷

1. **任务记录落盘被自己的路径守卫拦掉**（镜像/自测/自定义任务目录全中招）
   - 现象：`任务状态保存失败 <tid>: 路径越界（仅允许平台或输出目录内）: <临时目录>\<tid>.json`，
     它只是 warning，于是任务记录**静默不落盘**，重启/归档演练全部失真。
   - 根因：`Virus_Platform_Core/utils.py` 的 `check_path()` 写模式按
     `config.write_roots()` 判越界；`TaskManager._persist()` 允许 `DIRS['tasks']`
     指向自定义目录（测试/镜像），随后 `safe_open(p, 'wt')` 内部又
     `check_path(path, in_platform=True)` 再判一次 → 必失败。
   - 修法：`check_path(path, must_exist=False, in_platform=False)` 支持
     `in_platform='tasks'`（额外放行 `web.tasks.DIRS['tasks']`，统一
     `os.path.abspath` 后比较）；`safe_open(path, mode, compress_level=6,
     allow_outside=False)` 写模式改用 `check_path(path, in_platform=not allow_outside)`；
     `web/tasks.py` 的 `_persist` / `_archive_dir` / `archive` / `_recover` /
     `_prune` / `purge_*` / `_archive_path`（已更名 `_archive_rec_path`）统一走
     `in_platform='tasks'` + `allow_outside=True`。
   - 教训：**「先改 DIRS 再写文件」这种测试友好设计，必须让守卫认识那个 DIRS**，
     否则守卫和调用方各说各话，错误还只是个 warning。

2. **物种名校验结果读回踩页面级 `CUR`**
   - 根因：`webapp/templates/submit.html` 的 `doTaxCheck()` 想用
     `snap.result.project` 拼读回地址，但 `TaskManager.snapshot()` 下发的是
     `result_preview = build_result_preview(result)`，把 dict 折成
     `{'kind':'tool','stats':{...}}`，`project` 被压进 stats；而 job 返回值
     `{'n_checked','n_missing','result'}` 本身也没带 project。前端只好退回 `CUR`，
     切模块/清空后 400。
   - 修法：`build_result_preview` 透传标识类标量 `('project','run','out_dir','sample')`；
     `web/submit.py::api_submit_table_taxonomy_check()` 的 job 返回值加 `'project': name`。

3. **`/cds-export` 选中集合被列表异步刷掉**
   - 现象：刚选中集合、列表请求返回，选中项被打回占位项，`cdsPickOpen()` 只报
     「先选择集合」就 return（用户表现为：点「挑选」没反应）。
   - 根因：`cdsLoadCollections()` 用 `sel.innerHTML = ...` 整段重写选项。
   - 修法（`webapp/static/cds_export.js`）：改为**原地增删 `<option>`**，占位项固定首位，
     重载后 `sel.value = cur` 保住选中；`cdsPickOpen()` 落值前若目标 option 不存在，
     先 `document.createElement('option')` 补上再赋值（浏览器对不存在的 value 是**静默忽略**的）。

4. **`/mcp` 页「外部接入配置」区块整段缺失**
   - `mcp.html` 的 `MCPC._cfg()` 找 `$('mcpCfg')`、`MCPC.copyCfg()` 有复制按钮，
     `web/pages.py` 也会往 `#mcpCfg` 注入 `dataset.root/py/script`，但模板从未渲染
     这个 `<pre id="mcpCfg">`。已补 `#mcpExt` 区块（复用既有 i18n 键）。

### 测试健壮化

5. `tests/_it_async_ui.py` 第 4 段不再「扫磁盘上现成集合」，改为**自带夹具集合**
   `uitest_async_cdscoll`（180 bp、`cp`/`movement protein` 两条 CDS，均非假想蛋白）。
   以前借用别的集合，一旦那个集合恰好全是 hypothetical（实测 `it_compare_coll`
   18 条全中招），挑选表就只剩「无匹配记录」，而超时是以空消息的
   `WebDriver` TimeoutException 冒泡，看不出原因。选集合改为**竞态容错**
   （`cdsLoadCollections` 重写选项期间 value 会短暂为空，重复设置直到勾选框出现）。
6. `tests/_check_ai_global_ui.py`：坞开合存在 `localStorage.vp_ai_open`、会话 id 存在
   `sessionStorage.vp_ai_conv`，不清场则「点 FAB」永远 30s 超时、「刷新恢复历史」
   永远为假；改为首次进入才清场，并用 `page.route('**/api/ai/chat')` 桩 SSE
   （事件形态是 `{"type":"delta","content":...}` + `{"type":"done"}`）。
7. `tests/_check_svg_wellformed.py`：`%TEMP%\vp_ascii_root` 是指向平台根的**目录链接**
   （`hmm_annot.py` 给 Easel 中转 ASCII 路径用），`os.walk` 会跟着走进去 → 路径
   层层嵌套 7 层 → 超 MAX_PATH → 整个守卫崩掉。改为跳过 reparse point，
   并把「打不开」与「真残缺」分开（`_is_wellformed` 返回三元组）。
8. `tests/_it_task_archive.py` / `_it_task_archive_api.py` / `_it_async_ui.py` /
   `_check_pages_console.py` 批量红、单独跑全绿——都是**并行实例互抢 `run/` 目录**，
   不是产品缺陷；巡检时务必确认只有一个 `_run_all` 在跑。

### 顺带清理
9. `Virus_Platform_Core/bsp.py` 补 `import io`、`public_meta/host_genome.py` 补
   `import urllib.parse`（`undefined name` 实测会抛 NameError）。
10. `viz.py` 去掉函数内重复的 `from .utils import count_fasta_seqs`；
    `mcp_server.py` 的两处「先试装再 import」重复 import 改为 `importlib.import_module`。
11. `webapp/static/app-ai.js` 的 `AI._saveHistory` 空对话直接返回，不再写空历史桶。

### 验证基线（本轮）
- `python -m pytest -q` → **362 passed, 1 skipped**（基线 341 passed / 2 failed）。
- `python tests/_run_all.py --quick` → **107/107 通过，总耗时 1419.7s**（此前同类批量跑 97/105、99/107，红项均已定位）。
- `python mcp_server.py --check` → `ready: yes`、`mcp_tools: ok 32 个启用`。

## 2026-11-02（补三十二）：AI 对话坞跨页持久化 —— 恢复不再等网络、镜像兜底、离开即落盘

### 用户现场
「我运行的 AI 工作台的问答，切换其他页面，再回来又没了……有时候正常，有时候再修又错了」。
截图是首页内嵌的 AI 工作台：一条提问「对 Dataset_1 ~ Dataset_18 进行已知病毒识别和定量分析」
加一张进行中的「Dataset_11 · 阶段 0/4 · fastp 正在分析」卡片——切页时 18 个数据集正在跑。

### 根因（不是存储丢了，是恢复被网络卡住）
`webapp/static/app-ai.js` 的 `AI._ensureCtx()` 里，历史恢复写在
`await fetch('/api/ai/context')` 成功之后；服务端跑重活时该请求排队/超时，
一旦抛异常就整段跳过恢复（`catch` 只打一句「上下文加载失败」）：

    const d = await (await fetch('/api/ai/context' + q)).json();
    ...
    if (!AI.ctxLoaded) { AI.ctxLoaded = true; if (!AI._restoreHistory()) AI.greet(); }
    } catch (e) { AI.notice('上下文加载失败'); }   // ← 不进恢复分支

于是画面上干干净净，而 localStorage 里的消息一条没丢。「有时候正常」= 服务端闲时
这条 fetch 几十毫秒就回来。次要口子：会话 id 存在 sessionStorage（新标签页/隐私模式
换 id 就接不上旧键），以及流式回答发出后 2s 内切页时只落了提问气泡。

### 修法（`webapp/static/app-ai.js`，4 处）
1. 新增 `AI.restoreOnce()`：只跑一次、**只读 localStorage、不碰网络**；在 boot、
   `_ensureCtx()` 开头、上下文成功/失败分支各调一次（`_restored` 标记防重复渲染）。
2. 镜像键 `AI_LAST_KEY = 'vp_ai_msgs:__last'`：`_saveHistory()` 每次连同
   `{t, conv, msgs}` 写一份；`_restoreHistory()` 当前会话键为空/损坏时回落镜像，
   并把 `conv` 认回 `AI.convId` + sessionStorage —— 新标签页也能接上最近一段。
3. `pagehide` 里补 `AI._saveHistory()`：刚发出去就切页的那条也落盘。
4. 配套：`AI.clear()` 连镜像一起清（否则清空后换页又「复活」）；`_pruneHistory()`
   与 `_listConvs()` 跳过镜像键（它不算一段会话）。

### 验证
`tests/_it_ai_persist_ui.py`（Edge headless 打 8765）：① 正常换页保留；② 会话 id
换掉走镜像恢复；③ 只进内存、不等 2s 定时器就导航也能落盘；④ `fetch` 直接 reject
仍恢复历史；⑤ 清空后换页不复活。5/5 通过，已登记进 `tests/_run_all.py`（300s，
缺 Edge/selenium 时 SKIP 返回 0）。
### 守卫：把「这条链」写成断言（2026-11-02）
这处故障被反复「修好又坏」，不是因为同一个 bug 没修对，而是每次修别的问题时，
这条链上的某一环被顺手改回去了（恢复挪到 `await fetch` 之后、镜像键没写、clear 没清镜像…）。
所以除了行为测试，又加了 `tests/_check_ai_dock_persist.py`（静态、0.1s、不用浏览器）：
把 A 恢复不依赖网络（`restoreOnce`/`_restoreHistory` 体内无 `fetch`/`await`，且
`_ensureCtx` 里 `restoreOnce()` 必须先于 `/api/ai/context`）、B 两处落盘、C 镜像兜底与
会话 id 认回、D clear 连镜像清 + `_pruneHistory`/`_listConvs` 跳过镜像 + `pagehide` 落盘 +
boot 先恢复，共 25 条断言，已登记进 `tests/_run_all.py`（60s）。

变异验证过：把 `_ensureCtx` 开头那句 `AI.restoreOnce()` 删掉（模拟「又被挪回网络之后」），
守卫立刻 `FAILED: 1 项不变量被改坏`；恢复文件后哈希一致、重新全绿。
今后改这一带代码，先跑它；红了就是有人把某一环改回去了，不必再等用户切页踩出来。
