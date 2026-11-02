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

- `tasks.html` 的整页脚本包在 IIFE 里：**内联 `onclick` 能调到的函数必须在
  IIFE 尾部显式 `window.xxx = xxx;`**，否则点击报 `xxx is not defined`
  （2026-11-02 实测踩过：恢复按钮首次失效）。
- `meta.html` 等模板的脚本不在 IIFE 里，函数天然全局。
- **整页刷新 ≠ 丢状态**：一级导航是整页刷新，页面级变量（`curSample`/`curStages`）
  会丢；持久状态要放 `localStorage`（`VP_CTX`：`vp_ctx_sample`/`vp_ctx_project`）。
- **恢复类逻辑不要挂在网络请求之后**（2026-11-02 实测踩过）：`app-ai.js` 的历史恢复
  原来写在 `await fetch('/api/ai/context')` 成功分支里，服务端跑重活时该请求超时 →
  整段历史不渲染，用户以为「对话没了」。凡是「本地本来就有的东西」，页面加载就
  同步恢复（`AI.restoreOnce()`），网络只负责补充信息。
- 对话历史还有两处兜底：镜像键 `vp_ai_msgs:__last`（会话键空了就回落并把会话 id
  认回来）、`pagehide` 强制 `_saveHistory()`；`AI.clear()` 要连镜像一起清。
- 全局运行条在 `app.js`：`RUNBAR_POLL_MS=5000`，挂载在 `gxApplyPage()` 里
  `anchorBarInit()` 之后；轮询 `/api/tasks?logs=0`，`document.hidden` 时跳过。
- CSS 用现有变量（`--green-*`/`--blue-*`/`--red-*`/`--surface-2`/`--shadow-*`），
  新增颜色先看 `app.css` 顶部是否有对应变量，别写生色值。

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

## 9. 文档义务

改行为 → `docs/DEVELOPMENT_NOTES.md` 加一节（背景/改动/为什么/验证/遗留）；
改架构或口径 → 同步 `AGENTS.md` 与三张技能卡（`tests/_check_skills.py` 会查覆盖）；
改 `agent_guide._SKILL` 后必须重新生成：
`python main.py agent-guide --skill | Set-Content -Encoding utf8 .agents\skills\virusplatform\SKILL.md`。

