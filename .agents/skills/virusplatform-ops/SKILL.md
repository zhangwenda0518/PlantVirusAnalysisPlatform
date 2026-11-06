---
name: virusplatform-ops
description: >
  驱动本机 VirusPlatform（植物病毒分析平台 / PVAP）干活的操作规程：
  建样品、跑 15 阶段流水线、调工具箱、公共数据检索下载、NCBI 提交、读结果、排障。
  Use when the task is to build a sample, run assembly / virus identification /
  quantification / consensus / phylogeny / primer design on this platform, re-run
  a stage, run a toolbox tool, check tasks, or figure out where results landed.
  触发词：植物病毒、PVAP、VirusPlatform、建样品、跑组装、跑流水线、跑个鉴定、
  重跑阶段、重试失败、工具箱、公共检索、NCBI 提交、病毒定量、共识序列、
  进化树、引物设计、任务中心、历史归档、结果在哪、为什么失败、报错排查。
  只讲怎么用与怎么判读，不授予任何执行或下载权限。
agent_created: true
---

# VirusPlatform 操作规程（任务型手册）

## 0. 三条铁律（先看这个）

1. **计划 ≠ 已执行**。起草执行单成功只代表「待批」。只有任务中心出现真实
   任务 id 且终态 `done`，或你亲自读到落盘产物，才能说「已开始/已完成」。
2. **结果只依据真实文件**。不许由执行单、日志摘要或上次结果推断数字。
   引用任何指标都要能指到 `run/results/<样品>/...` 下的具体文件。
3. **先查目录，再动手**。不熟悉的操作先 `agent-ops --id <op> --schema`，
   id 与条数一律以目录输出为准（当前 57 条 = 35 工具 / 15 阶段 / 7 动作）。

## 1. 三个入口怎么选

| 入口 | 启动 | 适合 | 注意 |
|---|---|---|---|
| Web | `启动平台-网页.bat`（= `python app.py --web`），127.0.0.1:8765 起顺延 | 人；agent 查界面状态 | 只监听回环 |
| CLI | `python main.py <子命令>` / 打包版 `VirusPlatform.exe --cli <子命令>` | agent 批量、可复现 | 中文路径自动转 ASCII 中转 |
| MCP | `python mcp_server.py`（或 exe `--mcp-stdio`） | agent 宿主结构化调用 | 权限由宿主策略决定 |

源码模式改前端刷新即生效，**改 Python 必须重开进程**。注意：同一台机器上可能
同时跑着源码实例与打包实例，两边 `run/` 目录不同——「接口/改动明明没生效」多半
是打到了另一个实例，先确认端口对应哪个进程、哪个工作目录。

## 2. 查操作目录（唯一权威）

```bash
python main.py agent-ops                      # 全部操作（tool / stage / action）
python main.py agent-ops 组装                  # 关键词搜索
python main.py agent-ops --id assemble --schema   # 单条参数与必填项
python main.py agent-guide                    # 完整指南（= AGENTS.md）
```

Python 层四件套（`Virus_Platform_Core.agent_catalog`）：
`list_operations()` / `search(kw)` / `describe(op_id)` / `parameter_schema(op_id)`。
`list_operations()` 返回 `{'total': 58, 'items': [...]}`（条数以实际返回为准，
平台升级会增删；当前 = 36 工具 + 15 阶段 + 7 动作）。

MCP 侧（当前 32 个 `vp_*` 工具，清单以客户端 tools/list 或 `vp_platform_info` 为准）：
`vp_platform_info` / `vp_selfcheck`、`vp_search_operations` / `vp_describe_operation`、
`vp_list_tools`（可按关键词 / heavy|light / need_db / limit+offset 过滤）、`vp_run_tool`、
`vp_pipeline_start` / `vp_pipeline_stages`、`vp_list_samples` / `vp_sample_stages`、
`vp_list_tasks`（`include_archived` / `status` / `name` 过滤）/ `vp_get_task` /
`vp_wait_task`（阻塞到终态）/ `vp_get_task_log` / `vp_cancel_task` /
`vp_archive_task` / `vp_unarchive_task`、`vp_list_tool_runs`（`include_archived`）、
`vp_read_run_file`（`archived=true` 读已归档运行）/ `vp_read_sample_file`、
`vp_archive_tool_run` /
`vp_restore_tool_run`、`vp_delete_tool_run`、`vp_cli_help` / `vp_cli_run` /
`vp_cli_submit`、`vp_prescreen_*`、`vp_ov_*`。

MCP 与网页的归档口径一致：**归档是手动动作**——切模块、重连、刷新都不会归档；
`vp_archive_task` 只收已结束的任务（运行中先 `vp_cancel_task`；外部下载批次请在
来源模块处理），归档后 `vp_get_task` / `vp_get_task_log` 仍可读，
`vp_unarchive_task` 可恢复；工具箱运行同理由 `vp_archive_tool_run` /
`vp_restore_tool_run` 搬进/搬出 `run/tool_runs/_archive/`（只搬目录，不删产物）。

`vp_sample_stages` 是**纯磁盘判定**（只读 summary / report.html / 产物，不探测外部
引擎），因此没有网页那种 `unavailable`：装没装工具看 `vp_selfcheck` 或网页「工具
探测」，其余 `done/skipped/failed/ready/blocked` 与网页逐阶段同口径。

参数口径：键名与网页表单同源，**未列出的键被忽略**，缺必填项提交时当场报中文原因。

## 3. 15 阶段流水线（stage）

样品结果在 `run/results/<样品>/`。完成标记是目录下的隐藏文件 `.<名>.done`——
**看 `.done` 比看日志可靠**（日志可能带着历史内容）。目录名以实际样品为准；
`⓪` 系列（子采样/fastp/fq2fa）都落在 `00_prep`。

| stage id | 名称 | 目录 | 完成标记 | 关键产物 |
|---|---|---|---|---|
| `subsample` | 预处理(子采样) | 00_prep | — | conv_R*.fa.gz |
| `fastp` | ⓪ Fastp 质控 | 00_prep | 视配置 | 质控报告 |
| `fq2fa` | ⓪b FASTQ→FASTA | 00_prep | `.fq2fa.done` | conv_R1.fa.gz |
| `host` | ① 宿主去除 | 01_host_removal | `.host_removal.done` | kept_R*.fastq.gz、output_1.kreport2 |
| `kvsuite` | ②b 已知病毒识别与定量 | 02b_kvsuite | — | identify/all_viruses.summary.tsv、summary.json |
| `assembly` | ③ 组装·分类·提取 | 03_assembly | `.assembly.done` | contigs.filtered.fasta、contig_blast.tsv |
| `verify` | ③b 候选序列验证 | 03b_verify(+`_unclassified`) | — | 验证报告 |
| `consensus` | ③c 共识序列与变异 | 03c_consensus | — | consensus.fa、coverage.tsv |
| `hostana` | ④ 宿主预测(ICTV) | 08_host_analysis | `.host_analysis.done` | 宿主预测表 |
| `orf` | ⑥ ORF 预测 | 04_orf | `.orf.done` | ORF 位置与序列 |
| `orfa` | ⑥b ORF 功能注释 | 04b_orf_annot | `.orfa.done` | 注释表 |
| `phylo` | ⑦ 进化树与 SDT | 05_phylo | `.phylo.done` | summary.json、`<病毒>/aln.fasta`、refs.tsv |
| `primer` | ⑧ 引物设计 | 06_primer | `.primer.done` | 引物表 |
| `gbdraw` | ⑨ 基因组图(gbdraw/DFV) | 09_genome_plots | `.gbdraw.done` | 基因组图 |
| `report` | ⑩ 可视化报告 | 07_report | — | fig_manifest.json、genome_*.png |

`vp_pipeline_stages` 返回各阶段键、中英文名、默认全流程序列与分组。

## 4. 工具箱（tool，35 个，按用途分组）

| 组 | 工具 id |
|---|---|
| 预处理与转换 | `fastp` 质控、`hostremoval` 非宿主序列提取（宿主去除）、`convert` 格式转换、`loganfetch`(Logan 预组装 contigs 获取)、`prepchain` 一键前处理 |
| 组装与鉴定 | `assemble`、`contigs`、`identify`、`verify`、`hom`(同源比对·单条深查)、`kvsuite`、`virchain`(组装验证·一键)、`kvchain`(定量与共识·一键)、`annochain`(注释三连·一键；2026-11-04 卡片已摘，agent 经 MCP/对话坞仍可调) |
| 定量与同一性 | `consensus`、`identity`(NT+AA 同一性)、`sdt`(精确分析) |
| 注释与结构 | `orf`、`orfa`、`genoplot`、`structcmp` |
| 进化与系统地理 | `align`、`quicktree`、`rtt`(时间信号)、`rdp`(重组)、`phylogeo`、`phylodyn`(一键定年+迁移)、`pdprep`、`beastexport` |
| 引物与检测设计 | `primer`、`crispr`(Cas12a/Cas13a 检测试剂设计)、`lamp`(LAMP 引物组设计)、`dsrna`、`mirna` |
| 比较基因组 | `cmpchain`(⚡ 一键分析；2026-11-04 并行开发引入，工具可经 MCP/agent 调用) |

一键链（`prepchain`/`virchain`/`kvchain`/`annochain`）内部顺序调用多个单步工具，
产物落在各自的 `run/tool_runs/<tool>_<ts>_<uuid>/`；**单跑某步失败不自动回滚**，
重跑要自己判断从哪一步接。`prepchain`、`kvchain`、`virchain` 都支持目录批量（`input_dir`）：
自动按 `_R1`/`_R1_001`/`_1` 命名配对单双端，逐样本跑、失败隔离，可加 `samples`
只跑勾选样本；汇总见 `_chain.json` 与 `samples.tsv`，并可就地生成
`chain_report.html`（材料/方法/结果）。`kvchain` 的目录只接受 FASTQ
（`.sra` 先用 `prepchain`/`convert` 转）；`virchain` 的目录只接受
FASTA（FASTQ 请先在样本处理组做宿主去除/病毒序列提取）。
`virchain` 的三步用 `steps` 自由组合（默认 `1,2,3`）：1=病毒组装、
2=病毒鉴定、3=候选序列验证；2 依赖 1、3 依赖 2。

## 5. 七个执行动作（action，7 个）

| id | 作用 | 口径 |
|---|---|---|
| `create_sample` | 建样品 | **同步动作**：批准后立即完成，返回**样品名**，没有任务 id |
| `run_tool` | 跑单个工具箱工具 | `{tool, params}`；任务型，返回任务 id |
| `plan` | 多步计划 | `steps` 1~6 步，每步 `{"action":…,"params":…}`；前一步 done 才进下一步 |
| `rerun_stage` | 重跑指定阶段 | 覆盖式重跑，先想清下游要不要一起重跑 |
| `retry_failed` | 原参数重试失败阶段 | 唯一 `risk=auto_ok` 的二级白名单动作 |
| `download_runs` | 下载公共数据 | 任务型；只走 NCBI/ENA/NGDC 官方域名白名单 |
| `build_host_db` | 构建宿主基因组库 | 任务型；耗时长，先查 `host-db/` 是否已有 |

## 6. 六条任务剧本（先认场景，再按剧本走）

**A. 原始 FASTQ → 出报告（全链路）**
`analyze`（CLI）或 `vp_pipeline_start`（MCP）→ 任务 id → 等 `done` → 读
`02b_kvsuite/identify/all_viruses.summary.tsv`、`03_assembly/summary.json`、
`07_report/fig_manifest.json`。重活前先 `selfcheck` + 确认磁盘（下载/组装很吃空间）。

**B. 只想知道「检出了什么病毒、多少」**
工具箱 `kvsuite`（或一键 `kvchain`）：已知病毒识别 + 定量，不组装。产物在
`02b_kvsuite/`。适合先摸底再决定要不要组装。

**C. 公共数据检索 → 下载 → 建样品 → 分析**
`meta-search`（SRA+GSA 检索）→ `meta-info`（拉元数据）→ 平台下载（`download_runs`，
外部任务）→ `create_sample` 建样品 → 剧本 A。下载批次是**外部任务**，不落
`run/tasks/*.json`，也不能归档（卡片只能「隐藏」）。

**D. 某阶段失败要补跑**
先读任务日志（`vp_get_task_log` 或 `/api/task/<tid>/log?lines=200`）确认失败原因，
再用 `retry_failed`（原参数）或 `rerun_stage`（可能改参数）。**下游阶段要不要
跟着重跑自己判定**，平台不会自动帮你判断。

**E. 只做注释 / 进化（已有 contigs 或序列）**
`orf` → `orfa` → `align` → `quicktree`；要做精确系统发育/SDT 用 `phylo` 阶段或
`identity`/`sdt` 工具；时间信号 `rtt`，重组 `rdp`，系统地理 `phylogeo`/`phylodyn`。

**F. 引物与检测设计**
`primer`（PCR 引物）、`crispr`（Cas12a/Cas13a 检测试剂设计，`--amp LAMP`
为 CRISPR-LAMP 档）、`lamp`（LAMP 六引物组独立设计）、`dsrna`（dsRNA
片段）、`mirna`（miRNA 靶标）。输入通常是 FASTA 或上一步产物。

**G. NCBI 提交**
`submit-init` → `submit-validate` → `submit-fill` → `submit-export` → `submit-sbt`；
必填字段校验不过会退出码 1，别跳过。清单见 `docs/SUBMISSION_CHECKLIST.md`。

## 7. 三套账（最容易搞混）

- **执行单**：AI 只能起草（`propose_action`），用户在对话坞点「批准执行」才运行。
  删除类/全局重置类动作永不进白名单。外部 agent 经 MCP 能否直接执行取决于宿主策略。
- **任务**（`/api/tasks`，内存 + `run/tasks/<tid>.json`）：
  状态 `queued → running → done / failed / cancelled`；切模块、切功能、刷新页面
  **都不会归档任务**——后台线程继续跑，右下角全局运行条在任何页面都能看见。
  内存里超过 60 条会清日志引用、超过 300 条会滑出内存（于是表现为「已归档」）。
- **归档**（手动、可恢复）：任务跑完仍留在列表，只有点「📦 归档」才移到
  `run/tasks/_archive/<tid>.json`，「↩ 恢复」可回来。
  API：`POST /api/task/<tid>/archive`、`POST /api/task/<tid>/unarchive`。
  约束：运行中不能归档（先停止）；外部任务不能归档（去来源模块处理）。
  服务重启时遗留的 running/queued 会被改判 failed，提示
  「服务重启导致任务中断，请重新运行」——**重启会打断在跑任务**。

工具单步是另一套账：`/api/tool/runs`（`run/tool_runs/`），模块页「🕘 历史运行」
读的是它。批量归档/永久删除有确认令牌：
`{"confirm": "archive"}`（可带 `keep`）与 `{"confirm": "purge"}`；全局清理是
`{"confirm": "reset"}`。**缺 confirm 一律 400**，脚本调用要带上。

坞里的阶段卡（`📖 阶段开始 / ✔ 阶段完成 / 🏁 全部完成`）来自
`ai_agent/broadcast.py` 的进程内缓冲，默认只播 `kvsuite`/`assembly`/`report`
（`ai.broadcast_stages`）。坞里发起的跑批跑在 MCP 子进程，事件经
`run/ai_audit/spool/*.jsonl` 回灌到网页进程——所以坞里现在也能看到这些卡。
**跨进程的闸门卡是只读的**：卡片上不会给「继续/终止」按钮（传不回子进程），
要中止请到任务中心停止该任务，或等它按超时自动继续。

## 8. 结果判读（读哪些文件）

| 想知道 | 读这里 |
|---|---|
| 检出了哪些病毒 | `02b_kvsuite/identify/all_viruses.summary.tsv`（或 `.best.summary.tsv`） |
| 定量/覆盖度 | `02b_kvsuite/stats/<样品>.refstats.tsv`、`02b_kvsuite/quant/` |
| 组装质量 | `03_assembly/summary.json`、`contigs.filtered.fasta` |
| 共识序列 | `03c_consensus/consensus.fa` + `coverage.tsv` |
| 进化树 | `05_phylo/summary.json`、`05_phylo/<病毒>/aln.fasta` |
| 报告与图 | `07_report/fig_manifest.json`、`genome_*.png` |
| 任务日志 | `vp_get_task_log` 或 `GET /api/task/<tid>/log?lines=200`；落盘在 `run/logs/` |
| 一键流程规范报告 | 工具运行目录的 `chain_report.html`（材料/方法/结果五章）；`vp_build_report(target=<运行名>)`、`GET /api/tool/chain_report?run=<运行名>` 或 `python -m Virus_Platform_Core.chain_report <运行名>` 生成/复用 |
| 某个模块的全部产物一眼看完 | 该阶段目录（或工具运行目录）的 `results.html` 模块结果页：输入说明 + 图表（PNG/SVG 切换、点击放大、配图例读法）+ 表格前 30 行预览 + 交互页 iframe + 全部文件链接。文件链接默认本页预览（不跳转），弹层内再给「新标签打开 / 下载」；平台各「模块结果」入口也是本页弹层预览。生成：`python main.py module-report --sample <样品> [--stage <阶段>]` / `--run <运行名>`；Web `/api/module_report?sample=&stage=` / `?run=`；流水线阶段完成时自动刷新 |

判读纪律：先看 `.done` 与 `summary.json`，再引具体行；表格用 pandas 读、**按列名取**
（别按列序号猜）；样品里没有的物种/数字不许「按经验补」。

## 9. 常见故障（含真实报错原文）

| 症状 / 报错 | 原因 | 处理 |
|---|---|---|
| 提示缺依赖、缺外部工具 | `3rd/` 未就位或探测失败 | `python main.py selfcheck`、`python main.py tools` |
| 外部工具报路径错 | 中文路径 | 平台已自动转 ASCII 中转；自己写脚本时要照做 |
| 任务变 failed，提示「服务重启导致任务中断，请重新运行」 | 平台重启 | 看日志确认能续跑的阶段，按需重跑 |
| 归档 400「运行中的任务请先停止再归档」 | 还在跑 | 先停止再归档 |
| 归档 400「外部任务请在来源模块处理」 | 下载批次等外部任务 | 去来源模块（下载中心）处理 |
| 归档 200 但提示「任务不存在或已归档」 | tid 不存在/已归档过 | 查 `/api/tasks?full=1` 的 archived 列表 |
| 恢复 400「归档记录不存在」 | 归档文件已被清理或 tid 错 | 确认 `run/tasks/_archive/` 下有该 json |
| 删除 400「运行中的任务请先停止再删除」 | 还在跑 | 先停止 |
| 批量归档/清理 400「需显式确认」 | 缺 confirm 令牌 | body 带 `{"confirm": "archive"}` / `{"confirm": "purge"}` |
| 前端改了没变化 | 打到另一个实例 / 浏览器缓存 | 确认端口对应进程；`Ctrl+F5` |
| 长任务一直排队 | heavy 槽 2 / light 槽 4 限流（`platform.json` 可调） | 等，别并发硬塞 |
| 界面常驻条一直显示「正在分析」但任务中心没有任务 | 平台重启/进程被杀，anchor.json 留下永不关闭的运行记录 | 已自动收尾（启动时 `reconcile_interrupted`，安静 >30 分钟的 running 一律关成 interrupted，账本补 `run.interrupted` 事件）；MCP 用 `vp_anchor_active` 查、`vp_anchor_verify` 核对账本链 |
| AI 填好 Key 仍提示「未配置完整」 | 旧版本有默认关闭的总开关 | 2026-11-03 已取消总开关：`ai_ready` 只看 base_url/model/Key；填好即用 |
| 想让 AI 别拆多步计划（或强制拆） | 任务分解口径需要切换 | 对话坞底部 🧩 按钮三态切换 auto/off/always；off 时后端从工具契约**物理移除** plan 动作（强制单步，非提示词自觉） |

## 10. 参数速查（必填字段，权威仍是 `--schema`）

| 工具 | 必填 | 备注 |
|---|---|---|
| `identify` / `convert` / `fastp` / `hostremoval` / `assemble` | `input` 或 `r1` | `assemble` 另有 `r2`（srna 模式可空）、`mode`（默认 rnaviral）、`memory`（默认 64）、`min_len`（默认 200） |
| `primer` | `fasta` | 可加 `mode`、`num_return`、`specificity` |
| `align` / `quicktree` / `sdt` / `structcmp` | `seqs` | align 可加 `seqs_extra` 合并第二份 FASTA |
| `orf` | `fasta` | |
| `identity` | `nt_seqs` | |
| `rtt` | `input` + `meta` | 有效日期叶 <5 时明确拒绝（不会退化）；日期列不传时自动识别（date > year） |
| `rdp` / `phylogeo` / `crispr` / `dsrna` / `virchain` / `beastexport` | `input` | |
| `prepchain` | `input`（单文件）**或** `input_dir`（目录批量） | 二选一；目录模式自动解析单/双端，可加 `samples`（逗号分隔）只跑勾选样本；单样本失败不拖垮整批 |
| `kvchain` | `input_dir`（目录批量，可加 `samples`）或 `samples`/`reads`/`sample_sheet` 三选一 | 目录只收 FASTQ（`.sra` 先用 `prepchain`/`convert`）；逐样本跑全流程、失败隔离 |
| `virchain` | `input`（去宿主 FASTA）**或** `input_dir`（目录批量，可加 `samples`） | 四步用 `steps` 组合（默认 `2,3,4`，3 依赖 2、4 依赖 3）；目录接受 FASTA/FASTQ/`.sra`，FASTQ 需 steps 含 1；仍可用 `run_identify`/`do_verify` 旧开关 |
| `annochain` | `fasta` | |
| `contigs` | `contigs` | |
| `consensus` / `kvsuite` / `genoplot` / `orfa` / `mirna` | 无必填 | 靠运行上下文（样品、上一步产物、`run`/`fasta` 字段） |
| `pdprep` | 按 source | files=fasta+meta、manual=fasta_text+meta_text、genbank=accessions/term/species/taxid/gb_files 至少其一 |
| `phylodyn` | `input`（pipeline） | stage 模式配 `from_run` 时可省；无 input 无 from_run 会 400；A1 自带 **LTT 谱系-时间曲线**（`ltt` 默认开，对齐 VirPhyKit TreeDater-LTT 免 MCMC 口径，产物 `clock/ltt_curve.tsv`，斜率≈净多样化率） |

## 11. 说「完成」之前（自检清单）

- [ ] 任务 id 真实存在，`vp_get_task` 终态 `done`（不是 running/queued）
- [ ] 对应目录有 `.done` 标记或 `summary.json`
- [ ] 关键数字从文件读出，能贴出具体路径
- [ ] 只起草了执行单 → 明确说「待批，尚未运行」
- [ ] 涉及删除/归档 → 说明可恢复性与确认令牌

## 12. 文档索引

| 想知道 | 看 |
|---|---|
| 完整代理指南 | `AGENTS.md`（`python main.py agent-guide`） |
| 决策与踩坑史（按日期） | `docs/DEVELOPMENT_NOTES.md` |
| MCP 口径 | `docs/mcp_server.md` |
| NCBI 提交清单 | `docs/SUBMISSION_CHECKLIST.md` |
| 阶段性交接 | `docs/HANDOFF_2026-10-26.md` |
| BioAider 参考实现 | `docs/BIOAIDER_REFERENCE.md` |

