# Cmpchain · 比较基因组一键链

> 返回 [Home](Home) ｜ 上一页：[Compare](Compare) ｜ 下一页：[Seq-Prep](Seq-Prep)

一条任务跑完五步：参考集 → CDS/PEP 与保守基因 → 比对 → 基因组树与基因树 → SDT 同一性。用于**不同病毒之间**的比较。输入是已有 GenBank 集合、ICTV 科属选择或 accession 列表之一。产物落在 `run/tool_runs/<cmpchain_run>/` 的 `00_refs/` 至 `04_sdt/`。

对应引擎 `Virus_Platform_Core/compare_suite.py`（五阶段编排，`STAGE_ORDER`）；工具卡 `t-cmpchain`；操作目录 id `cmpchain`。

## 输入

### 输入格式

三种来源三选一：

| 输入 | 参数 | 说明 |
|---|---|---|
| 已有 GenBank 集合 | `coll` | 推荐；集合目录里至少 1 个 `.gb`。**只读**既有集合，不改动 `databases/` |
| ICTV 分类过滤 | `ranks` | dict，如 `{"Family":"Geminiviridae"}` / `{"Genus":"Potyvirus"}`；MCP 端可传 JSON 字符串 |
| accession 列表 | `accessions` | 逗号 / 空格 / 分号分隔，支持带版本号，去重保序 |

- `mode`（`collection` / `ictv` / `accessions`）留空时**按顺序自动推断**：集合里确有 `.gb` 记录 > `ranks` 非空 > `accessions` 非空。
- 工具卡的三个单选「已有集合 / ICTV 科属 / accession」分别对应 `mode=collection` / `ictv` / `accessions`。ICTV 与 accession 两种模式在卡上只露 `limit` / `per_genus`，`per_species` 走 MCP / Python 层。
- 未给 `coll` 时自动命名集合：ictv 模式取 `ranks` 里**最深一层**分类值 → `cmp_<最深值>`；accessions 模式 → `cmp_acc_<unix 时间戳>`。
- `ictv` / `accessions` 模式会**重建**同名集合（`rmtree` 后重新下载，与参考序列获取页的下载口径一致，避免新旧记录混在一起）；`collection` 模式只读不写。

### 参数

| 参数 | 默认 | 取值 / 说明 |
|---|---|---|
| `db` | `plant` | `plant`（仅植物病毒）/ `ictv`（全病毒界，不过滤宿主） |
| `genome` | `complete` | `complete`（仅完整基因组）/ `any`（全部记录） |
| `limit` | 200 | ICTV 模式最多条数（工具卡允许 1–10000） |
| `per_genus` / `per_species` | 0 | 每属 / 每种上限（0=不限） |
| `tree_tool` | `fasttree` | `nj` / `fasttree` / `raxml-ng` |
| `mafft_strategy` | `auto` | `auto` / `linsi`（精确）/ `fast`（极速） |
| `trimal` | `automated1` | `automated1` / `gappyout` / `strict` / `none` |
| `sdt_max_n` | 30 | SDT 最多条数（允许 3–200） |
| `do_cds` | 开 | 关掉 = 只出参考集 + 基因组比对 / 树 / SDT（cds 是后续阶段的唯一输入） |
| `do_gene_cds` / `do_gene_pep` | 开 / 开 | 保守基因树（CDS 核酸 / PEP 蛋白）各自开关 |
| `do_sdt` | 开 | 单独关掉 SDT 阶段 |
| `threads` | 平台默认 | 未给时取 `platform.json` 的线程数 |
| `stages` | 全部 | 引擎级续跑参数：只跑指定阶段（工具卡不暴露，MCP / Python 层可用） |

- ICTV 模式先按 `max(limit × 20, 2000)` 取候选行，再按 `per_genus` / `per_species` 封顶，最后截前 `limit` 条。抽样上限**先于** `limit` 生效。

## 流程

### 阶段编排

| # | 阶段键 | 中文名 | 产物目录 | 做什么 |
|---|---|---|---|---|
| B0 | `refs` | 参考集获取 | `00_refs/` | 按模式取参考：ictv 走 `ictv_db.select_refs` + 下载集合；accessions 直取；collection 读既有集合 |
| B1 | `cds` | CDS/PEP + 保守基因提取 | `01_extract/` | `extract_collection_features` 的 PhyloSuite 布局（见 [Cds-Export](Cds-Export)） |
| B2 | `align` | MAFFT + trimAl | `02_align/` | 基因组 MSA（`genome/`）+ 保守基因 MSA（`gene/<基因>_<cds\|pep>/`） |
| B3 | `tree` | 进化树 | `03_tree/` | 基因组树 + 保守基因树 |
| B4 | `sdt` | SDT 同一性矩阵 | `04_sdt/` | SDT 精确引擎（`sdt_exact.run_sdt_exact`） |

- 阶段契约与 `phylodyn_local.run_pipeline` 同款：每阶段返回 `{'ok','outputs','error','warnings'}`。
- **阶段间只认文件**：重跑某一阶段直接给 `stages`。
- **取消只在阶段之间检查**：单个 MAFFT / RAxML 子进程自带超时，不硬中断。
- 任一阶段失败即 `break`，后续阶段不再执行，错误写进 `run_summary.json`。
- 进度口径：`progress(stage, frac, msg)` 的 `stage` 恒为 `STAGE_ORDER` 之一；`STAGE_SPAN` 给出各阶段在总进度里的占比（refs 0–0.10 / cds 0.10–0.30 / align 0.30–0.55 / tree 0.55–0.73 / sdt 0.73–1.00）。

### 参考集与提取（B0 / B1）

- ictv 模式：`ictv_db.available()` 未就绪直接报中文原因（先去参考序列获取页更新 ICTV 谱系表）。随后选参 → 封顶 → 下载 → 写 `taxonomy.tsv`（accession / species / genus / family / coverage）与 `provenance.json`（含 `n_matched` / `n_target` / `dropped_by_cap` / 下载计数）。
- accessions 模式：`provenance.json` 记 `accessions` 清单与下载计数。
- collection 模式：`provenance.json` 记集合目录、`.gb` 文件数、FASTA 条数。
- 三种模式都会把集合记录导出成 `00_refs/refs.fa`。
- B1 用 `extract_collection_features` 产出 PhyloSuite 布局，布局与挑选口径见 [Cds-Export](Cds-Export)。

### 比对与建树（B2 / B3）

- 基因组 MSA 至少 2 条；长度 `max/min > 3` 时告警「基因组树拓扑可能失真，科/属级比较建议用保守基因树」。
- 保守基因 MSA **只做 ≥4 条**的组（`_MIN_ALIGN_N`），过少记 warning 跳过。
- `trimal=none` 时不产 `aln.trim.fasta`，后续建树自动回落 `aln.fasta`。
- 下游取比对一律**优先 `aln.trim.fasta`**，缺则 `aln.fasta`（`_pick_aln`）。
- 建树分支与 `gb_collection.build_collection_phylo` 完全一致：
  - `raxml-ng` 且序列 <4 条 → 降级 `fasttree`（RAxML-NG 自举要求 ≥4 条）；
  - `raxml-ng` 抛异常 → 降级 `fasttree` 兜底保树（无信息比对会让底层 pll 断言崩溃）；
  - `nj` → 距离法；`fasttree`：PEP 走蛋白模式（默认 LG，`-gamma`），nt 走 `-nt -gtr -gamma`；
  - RAxML-NG 模型：PEP `LG+G`、nt `GTR+G`；最优模型与对数似然由 `_parse_raxml_log` 写进 outputs。
- **基因组树失败 = 整阶段失败**；保守基因树失败只记 warning 并跳过，不拖垮整条链。

### SDT 矩阵（B4）

- 输入是 `01_extract/genome.fa`（**未比对**序列）；`orient=True` 自动定向，`aligned=False`、`seqtype='nt'`。
- 少于 2 条直接失败。
- 产物为 `sdt_matrix.csv`、`sdt_matrix.json`、`sdt_heatmap.png`、`sdt_distribution.png`。

### 失败与告警

- 任务起跑前先过 `module_deps.ensure_tools(cfg, 'cmpchain')`：外部工具缺失直接判任务失败，不静默降级。
- 阶段内异常也落成失败结果（写进 `run_summary.json` 的 `stages[key].error`），保证回溯有据。
- 常见告警：保守基因样本不足 4 条跳过、基因组长度差异过大、保守基因建树失败（已跳过）。

## 产物

### 目录结构

```text
run/tool_runs/<cmpchain_run>/
├── 00_refs/          provenance.json / taxonomy.tsv（ictv 模式）/ refs.fa
├── 01_extract/       genome.fa / CDS.fa / PEP.fa / CDS/ / PEP/ / genes.tsv
│                     marker/（<组>_CDS.fa · <组>_PEP.fa）+ marker_report.tsv
├── 02_align/         genome/aln.fasta + aln.trim.fasta
│                     gene/<基因>_<cds|pep>/aln.fasta + aln.trim.fasta
├── 03_tree/          genome/tree.nwk；gene/<基因>_<cds|pep>/tree.nwk
├── 04_sdt/           sdt_matrix.csv / sdt_heatmap.png / sdt_distribution.png / sdt_matrix.json
├── run_summary.json  阶段结果原文（含 warnings / error）
└── config.resolved.json  全部入参快照（含实际生效的 threads）
```

### 关键文件与字段

| 路径 | 关键文件 | 内容 |
|---|---|---|
| `00_refs/` | `provenance.json` | 参考集来源与下载计数 |
| `00_refs/` | `taxonomy.tsv` | accession / species / genus / family / coverage（ictv 模式） |
| `01_extract/` | `genes.tsv` | 基因 × 基因组矩阵 |
| `01_extract/` | `marker_report.tsv` | 保守基因归组报告 |
| `02_align/` | `aln.trim.fasta` | 清剪后比对，下游默认取它 |
| `03_tree/` | `tree.nwk` | Newick；基因组树与各基因树分开存放 |
| `04_sdt/` | `sdt_matrix.csv` | 成对 identity 矩阵 |
| 根目录 | `run_summary.json` | 阶段结果原文（`stages[key].error` 在此） |
| 根目录 | `config.resolved.json` | 全部入参快照（含实际生效的 `threads`） |

- `outputs` 里的路径一律相对 `out_dir` + 正斜杠；跨盘符时退化为绝对路径。

## 示例输入与输出

### 示例输入

ICTV 模式的一次调用（参数名即操作目录 id `cmpchain` 的 schema）：

```json
{
  "mode": "ictv",
  "ranks": {"Family": "Geminiviridae"},
  "db": "plant",
  "genome": "complete",
  "limit": 200,
  "per_genus": 50,
  "tree_tool": "fasttree",
  "mafft_strategy": "auto",
  "trimal": "automated1",
  "sdt_max_n": 30
}
```

已有集合时等价写法是 `{"mode": "collection", "coll": "PotyvirusSet"}`，集合名与 [Seq-Prep](Seq-Prep) 的 `gb-dl -n` 一致。

### 示例输出

```text
run/tool_runs/cmpchain_20261105_101500_ab12cd34/
├── 00_refs/      provenance.json · taxonomy.tsv · refs.fa
├── 01_extract/   genome.fa · CDS.fa · PEP.fa · CDS/ · PEP/ · genes.tsv · marker/
├── 02_align/     genome/aln.fasta · genome/aln.trim.fasta · gene/<基因>_<cds|pep>/
├── 03_tree/      genome/tree.nwk · gene/<基因>_<cds|pep>/tree.nwk
├── 04_sdt/       sdt_matrix.csv · sdt_matrix.json · sdt_heatmap.png · sdt_distribution.png
├── run_summary.json
└── config.resolved.json
```

跑完读 `run_summary.json` 渲染 **B0–B4 五阶段表**。每阶段一行 ok / failed + 原因 + 关键计数；同时给 warnings 汇总与产物直链。

## 界面与 CLI

### 界面

- 工具卡 `t-cmpchain`「⚡ 一键分析」：三选一输入 + 参数区 → 运行。
- 结果区给五阶段表、warnings 汇总与产物直链（保守基因清单 TSV、基因组树 Newick、各基因树、SDT 矩阵 CSV / 热图 / 分布图、`run_summary.json`、「打开模块结果页」）。

### CLI 与 agent

- `main.py` **没有 cmpchain 子命令**（`add_parser` 核实）。
- 外部 agent 走操作目录 id `cmpchain`：`python main.py agent-ops --id cmpchain --schema` 查参数，MCP `vp_run_tool` 执行。
- 只想要其中一段？单步卡更合适：[Seq-Prep](Seq-Prep)（取参考）、[Cds-Export](Cds-Export)（挑选导出）、[Align](Align)（比对）、[Phylo](Phylo)（建树 / SDT）。

## 相关

- 组导读与定位口径 → [Compare](Compare)
- 参考集来源与集合管理 → [Seq-Prep](Seq-Prep)
- CDS/PEP 产物布局 → [Cds-Export](Cds-Export)
- 建树工具口径、SDT 公式、MSA 查看 → [Phylo](Phylo)
