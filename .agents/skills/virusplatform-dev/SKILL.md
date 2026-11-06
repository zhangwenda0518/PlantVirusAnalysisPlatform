---
name: virusplatform-dev
description: >
  改 VirusPlatform（植物病毒分析平台 / PVAP）平台本身的开发手册：
  加工具箱工具、加流水线阶段、加 API 与页面、改任务管理器、补测试、打包发布。
  Use when extending or fixing the platform code: adding a toolbox tool, adding a
  pipeline stage, adding a Flask route or page, touching the task manager,
  writing tests, or building the Windows package.
  触发词：开发平台、加工具、加阶段、加页面、改后端、改前端、加接口、任务管理器、
  写测试、跑测试、打包 exe、发版、部署、重构。
  只管怎么改与怎么验；**不代替用户决定改什么**，也不绕过执行单与确认机制。
agent_created: true
---

# VirusPlatform 开发手册（任务型）

## 0. 四条铁律

1. **先看工作树，再动手**。这个仓库长期有人（或别的 agent）并发改，
   `git status` 里几十个未提交文件是常态。只改你要改的文件，绝不 `git add -A`、
   绝不 revert 别人的改动，也别把「本该你改」和「别人正在改」混在一个提交里。
2. **改完立刻跑最小验证**：语法（`python -m py_compile` / `node --check`）→
   针对性测试 → 相关全量（`tests/_run_all.py`）。
3. **新增路由要更新基线**：`python tests/_route_inventory.py --save`，
   否则下一个人跑巡检会看到「新增 N 条」的假警报。
4. **破坏性/不可逆操作先问**（`db-migrate`、`tool-runs clean`、删运行、全局清理）。

## 1. 代码地图

| 位置 | 作用 |
|---|---|
| `app.py` | Flask 入口：`create_app()`、蓝图注册、`--web` 启动 |
| `main.py` | CLI 入口：40+ 子命令（`analyze`/`report`/`agent-*`/`submit-*`…） |
| `mcp_server.py` | MCP 入口：`vp_*` 工具（stdio/HTTP） |
| `Virus_Platform_Core/pipeline.py` | 15 阶段：`STAGE_ORDER`(:30)、`STAGE_NAMES`(:37)、`STAGE_GROUPS`(:65)、`STAGE_REGISTRY`(:899)、`STAGE_DEPS`(:922)、`STAGE_SUMMARIES`(:1706)、`STAGE_OUTPUTS`(:1801)、`STAGE_VIEW`(:1825) |
| `Virus_Platform_Core/web/` | 每个模块一个蓝图：`tasks.py`/`tasks_api.py`/`tools_api.py`/`meta.py`/`pages.py`… |
| `Virus_Platform_Core/agent_catalog.py` | 操作目录：54 条 + `TOOL_PARAM_SCHEMAS`（参数唯一权威） |
| `Virus_Platform_Core/agent_guide.py` | `AGENTS.md` 与指针技能卡（`_SKILL` 常量、`read_skill()`） |
| `Virus_Platform_Core/config.py` | `DIRS`(:57)、`LEGACY_TOP_DIRS`(:88) |
| `webapp/templates/` | Jinja 页面（`meta.html`/`tasks.html`/`tools.html`…） |
| `webapp/static/` | `app.js`（全局：上下文/运行条/锚条）、`app-*.js`（分模块）、`i18n.js`、`app.css` |
| `tests/` | `_run_all.py` 批量入口 + `_check_*`/`_audit_*`/`_it_*` 用例（当前 107 条） |
| `docs/DEVELOPMENT_NOTES.md` | 决策与踩坑史（**改行为就补一节**） |
| `dev_tools/package.py` + `VirusPlatform.spec` | 打包（onedir） |

## 2. 加一个工具箱工具（三步，少一步就「能跑但页面上没有」）

1. **参数 schema**：`Virus_Platform_Core/agent_catalog.py` 的 `TOOL_PARAM_SCHEMAS`
   加一条（`required` / 可选键 / 默认值 / 说明）。这是唯一权威，MCP 也读它。
2. **注册表 + 执行体**：`Virus_Platform_Core/web/tools_api.py:73` 的
   `TOOL_REGISTRY` 加一项（`id`/名称/`job`/输出目录前缀/是否 light），
   并在 `tool_jobs.py` 写 `_tool_job_<name>(...)`（入参 dict → 返回结果 dict）。
   轻量工具（秒级）要加进 `LIGHT_TOOLS`（当前 `convert/genoplot/primer/mirna/beastexport`），
   否则会占 heavy 槽、把长任务堵住。
3. **页面与文案**：`webapp/templates/tools.html` 的工具卡 + `i18n.js` 中英成对文案；
   产物落 `run/tool_runs/<tool>_<ts>_<uuid>/`，历史盒（`app-runhistory.js`）靠
   `/api/tool/runs` 自动出现，不用额外接线。

验证：`python -c "import app"` → `agent-ops --id <tool> --schema` → 跑一次看产物 →
`tests/_check_js_syntax.py`（页面内联 JS）。

## 3. 加一个流水线阶段（`pipeline.py` 要动**六到八处**）

`STAGE_ORDER`（顺序）→ `STAGE_NAMES`（中英名）→ `STAGE_GROUPS`（前端分组）→
`STAGE_REGISTRY`（执行函数）→ `STAGE_DEPS`（依赖）→ `STAGE_SUMMARIES`（日志摘要）→
`STAGE_OUTPUTS`（产物清单）→ `STAGE_VIEW`（视图/展示）。漏一个的典型症状：
阶段能跑但页面不显示、或显示但没有产物链接、或依赖判断失效导致顺序错乱。

产物目录在 `run/results/<样品>/0X_*/`，**完成标记写 `.done` 隐藏文件**（如
`.assembly.done`，`PhaseResult` 或阶段函数末尾）。新增阶段后：`agent_catalog` 的 stage 条目、`AGENTS.md`/技能卡阶段表、
`docs/DEVELOPMENT_NOTES.md` 三处都要跟上。

## 4. 加 API / 页面

- 路由：在对应蓝图加 `@bp.route`，`app.py` 里注册蓝图（新模块才需要）。
- **confirm 令牌**：批量/删除/重置类接口必须要求显式令牌，缺了就 400：
  `{"confirm":"archive"}`（批量归档）、`{"confirm":"purge"}`（永久删除归档）、
  `{"confirm":"reset"}`（全局清理）。历史事故：空 POST 归档删掉 74 个运行目录。
- 页面：模板 + 导航（`_nav.html`）+ i18n 双语文案（`i18n.js` zh/en 成对，
  缺键会被 `tests/_audit_contract.py` 抓到）。
- 改完 `python tests/_route_inventory.py --save` 更新 `tests/_route_baseline.json`，
  再 `python tests/_audit_routes.py` 确认没有路由 500。

## 4b. 加一个 MCP 工具（`mcp_server.py`）

1. **薄包装 + 实现分离**：`@mcp.tool(name='vp_xxx')` 只做参数校验与
   `anyio.to_thread.run_sync(_impl, ...)`；真正的活写 `_xxx_impl()`（同文件），
   便于在测试里直接调、不用起 MCP 进程。
2. **注解选对**：只读 `_RO`、实时只读 `_RO_LIVE`、可逆写 `_WRITE`
   （`destructiveHint=False`）、真删除 `_DESTR`。网页控制台
   `Virus_Platform_Core/web/mcp_console.py` 的 `_BLOCKED_TOOLS` 只放真破坏面工具
   （当前仅 `vp_delete_tool_run`）；可逆的归档/恢复**不要**加进去。
3. **返回信封统一** `_fmt(status, summary, result=..., interpret=..., nxt=...)`；
   失败抛 `ValueError('中文原因')`——`check_path` 的 `FileNotFoundError` 会绕开
   agent 友好文案（归档/恢复踩过）。
4. **升位与清单**：`SERVER_VERSION` 升小版本（能力指纹 `sha256(version|排序工具名)`
   会随之变化，客户端据此感知工具面变更）；同步 `docs/DEVELOPMENT_NOTES.md` 与
   `.agents/skills/virusplatform-ops/SKILL.md` 的 MCP 段（写「当前 N 个」时
   必须与 `tools/list` 实测一致）。
5. **验证**：`python -m py_compile mcp_server.py` → `python mcp_server.py --check`
   （看 mcp_tools 计数与指纹）→ `python tests/_check_mcp_tools.py`（工具注册、
   过滤分页、归档→仍可读→恢复、越界路径、自检七项）。
6. **别在工具实现里 import 探测型模块**：`pipeline_overview()` 会探测 fastp/seqkit/
   gbdraw/DFV/ORF/共识引擎，把 matplotlib 拉进 anyio 工作线程，在 Windows 上首次
   `import matplotlib` 可卡到永不返回（`vp_sample_stages` 2026-11-02 事故，见
   `docs/DEVELOPMENT_NOTES.md` 补三十）。读取路径只做磁盘判定；「装没装工具」交给
   `vp_selfcheck`，需要引擎探测的语义留在网页侧。

## 5. 任务管理器（`Virus_Platform_Core/web/tasks.py`）

- 结构：`TaskManager`（内存 dict + order + heavy/light 信号量，默认 heavy 2 / light 4，
  `platform.json` 的 `max_heavy_tasks`/`max_light_tasks` 可调）。
- 生命周期：`start(name, fn, log_file, weight, link, out_dir, on_finish)` →
  `queued → running → done/failed/cancelled`；`_persist()` 写 `run/tasks/<tid>.json`。
- 容器口径：`snapshot()` 是前端唯一入口——新增对外字段（如现在的 `external`）
  要**同时**加进 `snapshot()` 与 `_ext_snapshot()`，否则前端拿不到。
- 归档语义：切模块/刷新**不归档**；`archive(tid)` 写 `run/tasks/_archive/<tid>.json`
  并移出内存，`unarchive(tid)` 反向恢复；运行中与外部任务拒绝归档。
- 内存淘汰：`_KEEP_FULL=60`（超了清日志/结果引用）、`_KEEP_TOTAL=300`
  （超了移出内存 → 表现为已归档）。改这两个数会改变用户对「历史」的直觉，慎动。
- 重启：`_recover_interrupted()` 把遗留 running/queued 改 failed
  （「服务重启导致任务中断，请重新运行」），`VP_NO_RECOVER=1` 可跳过（测试里常用）。

## 6. 前端（踩过的坑都在这里）

- **新工具卡/新页面要挂「📖 使用引导」**（2026-11-06）：目标元素加
  `data-guide="<WikiSlug>"`（slug 必须在 `wiki/` 有同名 .md 且登记进
  `webapp/static/wiki/index.json`，文档要有「输入/流程/产物」节——
  `tests/test_card_guides.py` 会抽不出来就红）；关键挂点还要登记进该测试的
  `EXPECTED_MOUNTS` 防漏表。组件实现在 `webapp/static/app-guide.js`，
  经 `_nav.html` 分发到所有完整页面，无需页面单独引脚本。
- `tasks.html` 的整页脚本包在 IIFE 里：**内联 `onclick` 能调到的函数必须在
  IIFE 尾部显式 `window.xxx = xxx;`**，否则点击报 `xxx is not defined`
  （2026-11-02 实测踩过：恢复按钮首次失效）。
- `meta.html` 等模板的脚本不在 IIFE 里，函数天然全局。
- **CSS 变量会被人后写覆盖**：`html[data-nav="top"]{--head-h:…}` 与
  `html.vp-frame{--head-h:…}` 同权重，谁在后谁赢——框架内例外规则必须带
  `[data-nav]` 提优先级并 `!important`（2026-11-03 实测：iframe 里顶栏被顶到
  46px 下、上方漏缝，就是这么失效的）。
- **外壳模式（app-shell.js）的 iframe 落点写死过 `left:236px/top:0`**：顶部
  导航形态下 iframe 会盖住导航、还与父层各画一条磁盘横幅/快照条。正确口径：
  `html[data-nav="top"] body.shell-mod > .vp-shell-host { left:0; top:var(--nav-top-h) }`，
  父层横幅/快照条在壳模式下 `display:none`（横幅交给 iframe 自己出）。
- **设置页大列表（33 工具 + 34 插件）单列要滚很久**：用 `.tools-grid`（2/3 列
  网格，≤1100px 退单列）；插件行把 `provides` 工具键渲染成 ✔/✘ 芯片，
  检测与安装同行（数据源 `/api/tools` 存 `TOOL_PROBE` 全局）。
- **整页刷新 ≠ 丢状态**：一级导航是整页刷新，页面级变量（`curSample`/`curStages`）
  会丢；持久状态要放 `localStorage`（`VP_CTX`：`vp_ctx_sample`/`vp_ctx_project`）。
- **页面自己的表单与结果也要存**（2026-11-02）：`webapp/static/app-state.js`（`window.PS`）
  把表单值（全站自动）与**页面自己写过内容的带 id 面板**存进 `sessionStorage`，进页面还原
  并在 `main` 顶部顶一行「⏱ 已恢复 …」。
  - 面板是**自动跟踪**的（劫持 `Element.prototype.innerHTML` 的 setter，去抖 600ms），
    新页面通常不用做任何事；个别要精确指定/强制覆盖的容器加 `data-ps`，
    要排除的加 `data-ps-off`，下拉框恢复后需重跑 `onchange` 的加 `data-ps-onchange="1"`。
  - 恢复只填空/占位容器，页面自己渲染的内容永远优先（快照只接住「页面不会再渲染」的那种）。
  - 铁律：这层**不许发请求**（恢复只做显示）；页面键要排除 `t/_/ts/v` 这类缓存击穿参数；
    单页 800KB / 单面板 200KB 上限。
  - **页面键里不能放页内会改写的选中项**（`p=` 当前集合、`#hash` 分页签）：`meta.html`
    自己用 `replaceState` 改地址，同一页于是出现两个键，回来读到旧键 → 参数「自己变了」。
    `p` 已进 `VOLATILE`；页面特有的再加 `window.PS_VOLATILE_PARAMS = ['xxx']`。
  - **示例自动填充会盖掉恢复值**：`webapp/static/examples.js` 的 `autoFill()` 在 400ms 后
    把空表单填成示例值，比快照恢复晚一步。`app-state.js` 已在本页有快照时置
    `window.__vpNoAutoFill = true` 让路；新页面若自带类似的「空表单填默认值」逻辑，
    也要认这个开关。
  - 改完跑 `python tests/_check_page_state.py`，浏览器行为由 `tests/_it_page_state_ui.py`
    与 `tests/_it_page_state_ui_pages.py` 守（后者跨 5 个页面抽查；被测容器必须挑
    「加载完仍为空」的结果面板，拿侧栏/设置行/状态区去测必是假红）。
  - 唯一不含 `app.js` 的页面 `logan.html` 自己引了一行 `app-state.js`；新增独立页面
    （不引 app.js）别忘补这一行。
  - **数据驱动的列表别塞回旧 HTML，记「位置」再重查**（2026-11-02 补）：公共检索页
    （`webapp/templates/meta.html`）的表是 `/api/meta/*` 现查的，回来必须按真实数据
    重渲染；它丢的是「哪个集合 + 哪个分页签」——导航链接是裸 `/meta`，`init()` 默认
    回第一个集合 + 检索页。修法是 `setProject()` 记 `vp_meta_proj`、`mtShow()` 记
    `vp_meta_tab`，`init()` 无 `?p=`/`#tab` 时回落。判断口径：**纯展示面板靠快照，
    数据列表靠记住位置重新查**。
- **切模块不再整页重载（同源 iframe 保活外壳）**（2026-11-02 补四十三）：
  `webapp/static/app-shell.js`（`window.VP_SHELL`）把目标页装进
  `<iframe src="/x?__frame=1">`，切走只切 `on` 类不卸载；`app.js` 里 `mountShell()`
  自动挂载，模板不用改。写新页面时注意：
  - 页面链接用普通 `<a href="/x">` 就能被接管；`target="_blank"`、`download`、
    `javascript:` 一律放行给浏览器；组标题那种「只开合不带跳转」的元素若用
    `preventDefault`，外壳也不会拦（`app-nav.js:186`）。
  - 页面里若用 `history.replaceState` 改地址（如 `meta.html` 改 `?p=`），外壳不会
    跟进了——它记的是 iframe 的原始 src；要「切回来还是那一条」就让页面自己记位置。
  - `?__frame=1` 时 `_nav.html` 给 `<html>` 加 `vp-frame`，CSS 隐藏侧栏/运行条/对话坞，
    新页面别把这些元素硬塞进自己的布局假设里。
  - 守卫：`tests/_check_page_shell.py`（静态 20 条）与 `tests/_it_page_shell_ui.py`
    （真浏览器：点导航后父文档没重载、iframe 里的变量切走再切回仍在）。
  - **归档 = 记录 + 能还原**：历史归档卡片的「↗ 在页面中打开」由
    `tasks.html:restoreHref()` 从 `result.msg`/`log_file` 里的
    `meta_search/<集合>/search` 推回 `/meta?p=<集合>#sb`；新任务类型要给
    「结果装回哪个页面」留线索（`link` 或 `result` 里的路径），别只存一句「完成」。
  - 三类落点（2026-11-02 补四十四）：公共检索 → `/meta?p=<集合>#sb`；
    工具运行（`tool_runs/<run>/`）→ `/results?run=<run>#runs`；样品流水线
    （`run/results/<样品>/`）→ `/results?sample=<样品>#samples`。读取方分别是
    `results.html` 的 `RS_WANT_SAMPLE/rsPickSample/rsFocusSample` 与
    `RS_WANT_RUN/rsFocusRun`，以及 `app-runhistory.js:rhDeepLink()`（`/tools?run=`）。
  - **页面里切模块别写 `location.href`**：iframe 里那等于把帧再载一遍，外壳救不了。
    统一用 `app.js` 的 `gotoModule(href)`（有外壳就 `VP_SHELL.open`，在帧里就
    `postMessage`，普通页才 `location.href`）；下载文件类跳转保持 `location.href`。
  - 全站巡检：`tests/_it_page_shell_all.py`（21 条模块路由 + 页面内 `gotoModule` +
    收回自身页面）与 `tests/_it_page_deeplink_ui.py`（三条还原落点 + 渲染后内联脚本语法）。
- **子进程/裸脚本也要认用户配的目录**（2026-11-02 崩过）：`config.write_roots()` 现在
  用 `_persisted_roots()` 直接读 `platform.json`（mtime 缓存，不构造 Config，避免
  `apply_*_root → write_roots` 递归）。页面（引擎脚本别名）在子进程里 `safe_open`
  写「设置 → 存储」配的输出目录时，靠的就是这条。回归：`tests/_check_write_roots.py`。
- **INSDC 编号是 5~9 位不定长**（2026-11-02 崩过）：`DRX1144321` / `DRR1175147` 是
  7 位，`DRX\d{6}` 会截成 `DRX114432` 拼出 404 的 DDBJ 目录；解析失败时别用
  `except Exception:` 吞掉上游原因（用户会看到「run 可能不存在」，其实数据在）。
  三库都解析不出时 `[SED]RR` 回落 `sracha`。回归：`tests/_check_public_data_resolve.py`。
- **恢复类逻辑不要挂在网络请求之后**（2026-11-02 实测踩过）：`app-ai.js` 的历史恢复
  原来写在 `await fetch('/api/ai/context')` 成功分支里，服务端跑重活时该请求超时 →
  整段历史不渲染，用户以为「对话没了」。凡是「本地本来就有的东西」，页面加载就
  同步恢复（`AI.restoreOnce()`），网络只负责补充信息。
- 对话历史还有两处兜底：镜像键 `vp_ai_msgs:__last`（会话键空了就回落并把会话 id
  认回来）、`pagehide` 强制 `_saveHistory()`；`AI.clear()` 要连镜像一起清。
- 全局运行条在 `app.js`：`RUNBAR_POLL_MS=5000`，挂载在 `gxApplyPage()` 里
  `anchorBarInit()` 之后；轮询 `/api/tasks?logs=0`，`document.hidden` 时跳过。
- **用户可见的按钮别写死在模板里**（2026-11-02 实测踩过）：对话坞快捷提示词原来是
  4 个 `<button onclick="AI.chip('interpret')">`，既不能按处境出现/消失，加一条还得动模板。
  现在模板只留 `<div id="aiChips">`，由 `AI.renderChips()` 按上下文（有没有选样品 /
  有没有在跑 / 有没有失败）排序生成，默认 6 条 + 「更多」。新增一条要同时改三处：
  `AI._chipDefs()`（`id/k/z/w/when/q`）、`i18n.js` 的 zh 与 en 各一组
  `ai.dock.chip<X>` + `ai.dock.q<X>`，然后跑 `python tests/_check_ai_chips.py`
  （静态契约测试，漏了中文/英文/重画时机都会红）。
- CSS 用现有变量（`--green-*`/`--blue-*`/`--red-*`/`--surface-2`/`--shadow-*`），
  新增颜色先看 `app.css` 顶部是否有对应变量，别写生色值。
- **事件播报是进程内的**（`ai_agent/broadcast.py`，环形缓冲 100 条）。流水线若是
  在 MCP 子进程里跑的（坞里发起的跑批走 `mcp_console` stdio），事件进不了网页进程 →
  靠 `VP_EVENT_SPOOL` 目录 + `spool_relay.start_relay()`（`app.py` 启动）回灌；
  `ingest()` 保留原 `ts`、打 `relay=True`、不重复写审计、不回写 spool。
  跨进程的 `ai_gate` 按钮传不回去 → 前端对 `relayed` 只渲染说明（`ai.ev.gateRelayNote`）。
  改了 `app.py` 的 relay 启动要**重开窗口**才生效（刷新页面不够）。

## 7. 测试矩阵（改什么跑什么）

| 改动 | 必跑 |
|---|---|
| 任何 Python | `python -m py_compile <file>`，再 `python -c "import app"` |
| 任何前端 JS | `node --check webapp/static/<file>`；模板内联用 `tests/_check_js_syntax.py` |
| 路由 | `python tests/_audit_routes.py`；新增路由先 `_route_inventory.py --save` |
| i18n | `python tests/_audit_contract.py`、`tests/_check_i18n.py` |
| 任务中心/归档 | `tests/_it_task_archive.py`、`_it_task_archive_api.py`、【`_it_runbar_ui.py`（需 Edge）】 |
| 技能卡 | `python tests/_check_skills.py` |
| 收尾 | `python tests/_run_all.py`（登记在 `TESTS` 列表；浏览器用例缺 Edge 会 SKIP） |

测试脚本的通用约定：`sys.path.insert(0, ROOT)`（本机 `3rd/python` 的 `._pth`
会把 `sys.path[0]` 指到别处）、打印 `✔/✘`、失败返回 1、临时目录自己 `rmtree`、
不碰真实 `run/`（`VP_NO_RECOVER=1` + 改 `DIRS['tasks']` 到临时目录）。

## 8. 打包与部署

```powershell
# 重打（onedir：exe + _internal）
python -m PyInstaller dev_tools\VirusPlatform.spec --noconfirm --distpath build_dist --workpath build
```

- 正式产物由 `dev_tools/package.py` 产出到 `DIST`（仓库旁的 `PVAP/`）；
  手工重打用上面的命令即可，产物在 `build_dist/VirusPlatform/`。
- **替换前先确认没有 running/queued 任务**（重启会把它们判 failed），
  停进程 → 旧 exe/`_internal` 改名备份 → 复制新件 → `--web --no-open` 起 →
  核对 `/api/tasks?full=1` 与关键新路由（如 400 报错原文）。
- 前端单文件改动可以直接覆盖到 `_internal/webapp/static/` 后 `Ctrl+F5`，
  不必重打包；**Python 改动必须重打或跑源码模式**。
- 源码模式的日常启动是 `启动平台-网页.bat`（`"%PYTHON%" app.py --web`）——
  改完 Python 重开窗口即可，不用打包。

### 8.1 自动更新（2026-11-05，updater.py + build_update.py）

- **机制**：分发包启动后 daemon 线程跑 `updater.auto_update_loop`——静默检查
  manifest（内置默认仅 https 源：仓库 `updates-repo/manifest.json` 的 GitHub
  raw；自建/内网源经 platform.json `update_manifest_url` 显式配置，域名进
  更新白名单，http 允许但仅限用户显式配置）→ 有新版自动下载
  （**sha256 必填，缺失 fail-closed**；可选 Ed25519 签名校验，配置
  `update_sign_pubkey` 后未签名的清单一律拒绝；签名工具 `dev_tools/sign_update.py`）+
  断点续传 → 安全解压暂存 `run/updates/<ver>/staged.tmp/`（校验通过后原子
  落为 staged/）→
  **等任务空闲** → 生成 `apply_update.bat`（等主进程退出 → 旧 exe/_internal
  改名 .old 备份 → 换入 → 按原模式重启 → 失败自动还原）→ 主进程退出，
  bat 完成换装。`.old` 保留一代，下次启动成功后清理。
- **更新单元** = `VirusPlatform.exe` + `_internal/` + `mcp_server.py` +
  `build_info.json`；platform.json / run/ / 3rd/ / databases/ / host-db/ /
  plugins/ 一律不碰。两档（minimal/full）各一个更新包，按 build_info.json
  的 edition 选包。
- **版本真源** = `Virus_Platform_Core/__init__.py` 的 `__version__`（发版在此
  bump，当前 1.2.0）；package.py 写入各包 build_info.json 的 version 字段。
- **发版流程**：`package.py` 打包 → `finalize_release.py` 组两档 →
  `python dev_tools/build_update.py --src <版本目录> [--src <另一档>]` 产
  更新 zip + `updates-repo/manifest.json` → zip 上传自有服务器 `updates/` +
  GitHub Releases，manifest 同步自有服务器 `updates/manifest.json` 并随仓库
  提交。
- **手动通道**：CLI `VirusPlatform.exe --cli update [--check|--yes|--json]`；
  Web 设置页「软件更新」卡片（/api/update/check|status|install|skip），
  install 需确认令牌 `{"confirm":"install"}`。配置三键
  `update_manifest_url / update_auto_check / update_auto_apply`
  （platform.json，设置页可改；用户显式配置的源域名豁免私网 IP 检查，
  内网自建更新源可用）。源码模式不自更新（git pull）。
- **发布控制（manifest 级，2026-11-05）**：触发更新只看 manifest 的
  `version` 是否比用户 build_info 新——**传 zip 不触发，传 manifest 才是
  放行**。两个阀门：
  `"paused": true`（全局暂停自动下载/换装；手动安装不受限）、
  `"rollout_percent": N`（灰度，客户端按 MAC 哈希桶 0-99 与 N 比较，
  只有桶号 < N 的机器参与自动更新；手动安装同样不受限）。
  用 `build_update.py --paused / --rollout-percent N` 写入，或在服务器上
  直接改 manifest（改完即生效，客户端下个检查周期读取）。
  边界：版本比较只升不降，坏版本发出去无法远程回退，只能快速发修复版。
- **主源上传（2026-11-05 实测路径）**：服务器 `root@39.106.101.94` 可用密钥
  登录（本机 `~/.ssh` 已配）；nginx 站点根 =
  `/opt/plant_virus_db/plant_virus_db_pipeline/docs`（`/plugins/...` 同根）。
  清单上传：
  `scp updates-repo/manifest.json root@39.106.101.94:/opt/plant_virus_db/plant_virus_db_pipeline/docs/updates/manifest.json`
  （目录已建）。验证：`curl -s -o /dev/null -w "%{http_code}" http://39.106.101.94/updates/manifest.json` → 200。
  更新 zip 同样传到该 `updates/` 目录；manifest 里的 urls 已按
  `http://39.106.101.94/updates/<zip名>` 生成。

## 9. 文档义务

改行为 → `docs/DEVELOPMENT_NOTES.md` 加一节（背景/改动/为什么/验证/遗留）；
改架构或口径 → 同步 `AGENTS.md` 与三张技能卡（`tests/_check_skills.py` 会查覆盖）；
改 `agent_guide._SKILL` 后必须重新生成：
`python main.py agent-guide --skill | Set-Content -Encoding utf8 .agents\skills\virusplatform\SKILL.md`。

