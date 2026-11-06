# VirusPlatform — AI 代理使用指南（AGENTS.md）

> 本文件是外部 AI 代理（Claude Code、ZCode、Cursor 等 agent 宿主）驱动本平台的
> **单一事实源**。人也可以直接读；命令行同款：`python main.py agent-guide`（本文件）
> 与 `python main.py agent-guide --skill`（技能卡，可存为
> `.agents/skills/virusplatform/SKILL.md`）。
> 本指南只提供用法与口径，**不授予任何执行或下载权限**；宿主的权限策略永远优先。

## 0. 平台是什么

VirusPlatform（植物病毒分析平台，PVAP）——Windows 本地的植物病毒诊断与深度
分析平台：从原始 FASTQ 到 NCBI 提交文件，15 阶段流水线 + ~35 个工具箱工具 +
公共数据检索/下载 + NCBI 提交 + LOGAN 溯源，中英双语，本地优先、离线可用。

三个入口共享同一引擎（`Virus_Platform_Core/`）：

| 入口 | 形态 | 适用 |
|---|---|---|
| `VirusPlatform.exe --web` / `python app.py` | Web GUI（127.0.0.1:8765） | 人 |
| `python main.py <子命令>` | CLI（40+ 子命令） | 人 + agent |
| `python mcp_server.py`（或 exe `--mcp-stdio`） | MCP（`vp_*` 工具，stdio/HTTP 双模） | agent |

## 1. 干活前必做（自举三步）

1. **读本指南**：`python main.py agent-guide`（打包版
   `VirusPlatform.exe --cli agent-guide`）。
2. **查操作目录**：`python main.py agent-ops`（工具箱 + 管线阶段 + 执行单动作；
   **条数以目录输出为准，不要记死数字**——平台升级会增删）；`agent-ops <关键词>` 搜索，
   `agent-ops --id <op> [--schema]` 查详情与参数 schema。
3. **校环境**：`python main.py selfcheck`；工具探测 `python main.py tools`。

> **项目级技能卡（`.agents/skills/`，宿主支持时按关键词自动加载）**：
> `virusplatform`=导航（能干什么 / 该看哪张）· `virusplatform-ops`=操作规程
> （建样品、跑阶段、工具箱、检索下载、提交、判读、排障）·
> `virusplatform-dev`=改平台本身（加工具 / 加阶段 / 加接口 / 测试 / 打包）。
> 它们只是本指南的展开版，**不授予任何权限**；改完卡要跑
> `python tests/_check_skills.py`（已入 `tests/_run_all.py`）确认没有过期。

## 2. 三种驱动方式

### 方式一：MCP（推荐给 agent 宿主）

```json
{ "mcpServers": { "virusplatform": {
    "command": "python", "args": ["mcp_server.py"],
    "cwd": "<平台根目录>" } } }
```

- 工具清单与参数口径**以 `mcp_tool_catalog`（坞内 AI）或 MCP 客户端的
  tools/list 为准**，不要凭记忆报数字；平台升级会增删。
- 要「结论文档」（学术论文风单文件 HTML，含结论/漏斗/逐阶段深读/缺口/问答/
  命令/溯源）用 `vp_build_report`：target 给样品名、工具运行名或平台内目录，
  同一调用顺带回正文纯文本（`result.digest`），不必再取文件。它只写
  `report_v2.html` + `report_v2_meta.json`，不动任何分析产物。
- 破坏面工具（如 `vp_delete_tool_run`）在网页控制台与内置对话坞中不开放；
  外部 agent 是否受限取决于宿主权限策略，请自行把关。

### 方式二：CLI

`python main.py <子命令> --help` 逐个看；常用：
`analyze`（全链路）· `report` · `build-host-db` · `datasets` · `meta-search` ·
`submit-*`（NCBI 提交）· `logan-*`（溯源）· `agent-guide` / `agent-ops`。

`report` 会同时刷新图表报告 `report.html` 与结论文档 `report_v2.html`；只要
结论文档时用 `python -m Virus_Platform_Core.report_kit <目录>`（更快，不碰
matplotlib）。加 `--pdf` 顺带打印一份 PDF（`report_v2.pdf`）：借本机
Chrome/Edge 无头模式打印同一份 DOM，版式与网页一致、不新增依赖；
`module-report --pdf` 对 `results.html` 同理（实现在
`Virus_Platform_Core/pdf_export.py`，可用 `VP_PDF_BROWSER` 指定浏览器）。

`module-report` 生成**模块结果页**：每个阶段目录/工具运行目录一份
`results.html`（输入 → 图表 → 表格 → 数据/序列 → 交互页 → 各类文件，
图配图例读法、PNG/SVG 可切换、点击放大、文件全链接）。页内文件链接**默认
本页预览**（不跳转），预览弹层里再给「新标签打开 / 下载」；平台内各「模块结果」
入口同样在本页弹层预览 `results.html`，预览头有「新标签打开」。用法：
`python main.py module-report --sample <样品> [--stage <阶段键|目录>] [--pdf]`
或 `--run <工具运行名>`；流水线每个阶段完成时也会自动刷新对应页。
Web 端按需生成走 `/api/module_report?sample=&stage=` / `?run=`。

### 方式三：HTTP（平台在跑时）

只监听 `127.0.0.1`（8765 起顺延）。执行单决策等少数端点只在本机开放；
agent 走 MCP/CLI 即可，HTTP 仅调试用。

## 3. 发现与调用边界

- 操作目录四件套（Python 层：`Virus_Platform_Core.agent_catalog`）：
  `list_operations()` / `search(关键词)` / `describe(op_id)` /
  `parameter_schema(op_id)`。
- 工具箱参数 schema 的**唯一权威** = `TOOL_PARAM_SCHEMAS`
  （agent_catalog.py，mcp_server 亦从这里 import）；未列出的键被忽略，
  缺必填项提交时当场报中文原因。
- 不猜参数：不熟悉的操作先 `agent-ops --id <op> --schema`；
  工具/阶段/动作的 id 一律以目录为准，不要假设 id 等于函数名。

## 4. 执行与结果纪律（反幻觉红线）

- **计划 ≠ 已执行**：执行单起草成功只代表"待批"，不代表已开始，更不代表
  已完成。只有当任务中心出现真实任务 id（`vp_get_task` / `get_task_overview`
  / `wait_task` 可见）且终态 done，或你亲自读到落盘产物，才能说
  「已开始 / 已完成」；结果判读只依据真实文件，不许由计划推断结果。
- 同步动作与任务型动作的返回口径不同：`create_sample` 返回**样品名**
  （无任务 id，完成即返回）；任务型动作返回任务 id。两者不可混为一谈。
- 不许编造指标；引用关键数字必须来自落盘产物（如
  `02b_kvsuite/identify/all_viruses.summary.tsv`）或 API 返回原文。
- 失败按原文转述，不美化；缺依赖就报缺依赖，不静默替换。

## 5. 执行单机制与权限边界

- AI（坞内模型）**只能起草**执行单（propose_action），用户在对话坞点
  「批准执行」后才运行；删除类/全局重置类动作永不进白名单。
- 多步计划（plan）一次批准按顺序执行，前一步任务 done 才进下一步；
  create_sample 是同步动作（起草→批准→立即完成），不会挂任务号。
- 外部 agent 经 MCP 的 `vp_run_tool` / `vp_pipeline_start` 等直接执行类工具
  是否放行，由宿主权限策略决定；平台侧不因本指南而放宽。

## 6. 环境事实（少踩坑）

- Windows 10+，Python 3.12（`3rd/python/` 自带）；中文路径已自动处理
  （SPAdes/BLAST/MEGAHIT 经 ASCII 临时目录中转）。
- 外部工具在 `3rd/`（探测：`python main.py tools`）；数据库在 `databases/`、
  `host-db/`；样品结果在 `run/results/<样品>/0X_*/`；工具箱单步产物在
  `run/tool_runs/<tool>_<ts>_<uuid>/`；任务状态 `run/tasks/`，日志 `run/logs/`。
  每个阶段目录与工具运行目录下有 `results.html` 模块结果页（自动生成，
  整理该模块全部图表/表格/文件并附输入说明与图例读法）；平台静态图统一
  PNG+SVG 双格式（gbdraw/genome_diag 的 SVG 在装了可选依赖 cairosvg 时
  才补 PNG）。
  这些目录都跟着**输出目录**设置走：`platform.json` 的 `output_root` 一旦被改，
  `results` / `tool_runs` 等全部平移到新根下（默认空 = 平台根 `run/`）。
  例外：AI 对话会话镜像 `run/ai_sessions/<conv>.jsonl`（只追加，2026-11-03 起）
  与 API Key 真身 `run/ai_local.json` 固定在平台根，不随输出根迁移。
- API Key 支持环境变量引用：`ai_local.json` 里写 `${DEEPSEEK_API_KEY}` 这类
  **整值占位符**，运行时从环境变量展开——配置文件零密钥，可随意备份/同步。
- 对话坞会把你回答里写的**绝对路径**渲染成可点开的产物卡片（报告可内嵌预览、
  图片给缩略图、表格给前几十行）。所以给路径时写绝对路径、别写「去结果中心点看」；
  工作空间条上的「📥 输出到此」等于把 `output_root` 设成该工作空间。
  整轮跑完时的那条「本轮分析全部完成」也会自动带卡片（结论文档 → 图表报告 →
  漏斗图 → 进化树 → 基因组图），不必等 AI 再说一遍；**每个阶段完成**（图/表/
  交互页，最多 3 张）和**执行单完成**（所跑阶段的产物，最多 4 张）同样自带卡片。
- 平台只监听本机回环；下载仅限 NCBI/ENA/NGDC 官方域名白名单
  （**例外**：分发包自更新走独立更新源白名单，机制与发版流程见
  virusplatform-dev 技能卡 §8.1；用户在 platform.json 显式配置的
  `update_manifest_url` 域名也进更新白名单，可指向内网服务器）。
- 界面中英双语；CLI/日志中文为主（`--lang en` 可切，覆盖以
  `i18n_cli_catalog` 词表为准，未登记串原样保留）。

## 7. 明确不做

- 不绕过执行单批准机制；不修改/伪造结果文件与 anchor 台账；
- 不在对话里编造检测数字或"预测"分析输出；
- 不可逆操作（db-migrate / tool-runs clean / 删除运行）先向用户确认。
