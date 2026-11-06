# Phylodyn · 进化动力学一键链（A0→A5）

> 返回 [Home](Home) ｜ 上一页：[Phylodynamics](Phylodynamics) ｜ 下一页：[Pd-Prep](Pd-Prep)

比对 FASTA + 元数据进，**本地一条链、零 MCMC** 出「时间树 → 地理迁移走廊 → 天际线 → 祖先序列 → 汇总溯源」。接口产物固定为 `clock/timetree.nwk`（枝长＝年），下游阶段都靠它衔接。

对应引擎 `Virus_Platform_Core/phylodyn_local.py`（A0–A5 编排）、`phylodyn_trees.py`（树类引擎）、`treetime_ml.py`（TreeTime ML）、`bsp.py`（天际线）、`rtt_drt.py`（时间信号显著性）。工具卡 `t-phylodyn`；操作目录 id `phylodyn`。

## 输入

### 输入格式

- 比对 FASTA：等长 MSA，序列名与元数据 `name` 列、树 tip 一致。
- 元数据 CSV：含日期列与地点（性状）列；区划列留空时由 A0 自动识别。
- Newick 树：可选，缺则由 A0 现场建。
- 重命名映射 TSV：A0 之前对 FASTA / 元数据 / 树 tip 三处同改。
- 续跑：给上一轮运行名（只认 `basename` + `[A-Za-z0-9_-]` 白名单）。上一轮必须已有 `prep/dates.csv` 与 `prep/states.csv`，否则提交时当场报中文原因。

### 参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `mode` | `pipeline` | `pipeline`（全链）/ `stage`（单阶段） |
| `stage` | 空 | `stage` 模式的阶段键（prep / clock / phylogeo / coalescent / ancestral / report） |
| `from_run` | 空 | 上一轮专项运行名（断点续跑） |
| `input` | 必填（pipeline） | 比对 FASTA |
| `meta` / `tree` | 空 | 元数据 CSV / Newick 树（缺树时 A0 现场建） |
| `trait` | 空 = A0 自动识别区划列 | 离散性状列；**不预填 `region`**，A2 消费 A0 的识别结果 |
| `date_col` / `date_trait` | 空 | 日期列名 / 日期解析口径 |
| `rename_map` | 空 | 重命名映射 TSV（A0 之前 FASTA / 元数据 / 树 tip 三处同改） |
| `coord_table` | 空 | 区划坐标表；**只影响 A2 迁移弧线地图的落点**，不参与统计量 |
| `methods` | `treetime` | 定年方法（只认 `CLOCK_METHODS`） |
| `reroot` / `keep_root` | `least-squares` / 关 | 重根方式 / 保留原根 |
| `relax_sigma` / `relax_coupling` | 0 / 平台默认 | 松弛钟 sigma（0=关）与耦合 |
| `drt` / `drt_perm` | 开 / 200 | 时间信号 DRT 检验与置换次数（A1 内 `clock_qc/drt.json`） |
| `skyline` / `n_skyline` / `gen_per_year` | 关 / 10 / 50 | A3 天际线与每年世代数 |
| `ancestral` / `homoplasy` | 关 / 关 | A4 祖先序列与同塑性检查 |
| `prune` 系列 | 关 | 剪枝（`prune_method` fps、`prune_date_resolution` month、`max_reps` 3、`min_snp_diff` 2、`clade_cutoff` 5） |
| `sampling_bias_correction` | `auto` | 采样偏倚校正（默认开） |
| `cross_check` | 开 | mugration 对拍交叉验证 |
| `clock_rate` | 空 | 固定速率（与松弛钟互斥） |
| `timeout` | 3600 | 单步超时（秒） |

## 流程

### 阶段契约（A0→A5）

| 阶段 | 输入 | 产物 |
|---|---|---|
| **A0 prep** | 比对 FASTA（必需）+ 元数据（可选）+ 树（可选，缺则 FastTree 现场建） | `prep/dates.csv`、`prep/states.csv`、`prep/prep.fasta`、`prep/prep.nwk`、`prep/prep_report.json` |
| **A1 clock** | A0 全部 | **`clock/timetree.nwk`**、`clock/clock_stats.tsv`（**带方法列**）、`clock/rtt_scatter.tsv`、`clock/clock_qc/drt.json`、`clock/divergence_tree.nwk` |
| **A2 phylogeo** | `clock/timetree.nwk` + `prep/states.csv` | `phylogeo/GTR.txt`、`confidence.csv`、`annotated_tree.nexus`、**`corridors.tsv`**（ML 与 Fitch 并列）、**`events_year.tsv`**、`phylogeo_report.json` |
| **A3 coalescent** | `clock/timetree.nwk` + `prep/prep.fasta` | `coalescent/skyline.tsv`、`coalescent_report.json` |
| **A4 ancestral** | `prep/prep.fasta` + `clock/divergence_tree.nwk` | `ancestral/ancestral_sequences.fasta`、`branch_mutations.txt`、`auspice_tree.json` |
| **A5 report** | A0–A4 的产物 | **`run_summary.json`**、**`config.resolved.json`**、`RUN_REPORT.md` |

- 阶段键（`STAGE_ORDER`）：`prep` / `clock` / `phylogeo` / `coalescent` / `ancestral` / `report`。
- 时间与地理是分开的阶段，靠**时间树**这一个接口产物衔接：`clock/timetree.nwk` 一变，等于改协议。
- 本卡是 A0→A5 的**编排入口**；数据接入的准备卡（[Pd-Prep](Pd-Prep)）、时间信号（[Rtt](Rtt)）、重组（[Rdp](Rdp)）是它的上游 / 旁路能力，不是它的替代。
- 需要贝叶斯后验的（BSP 的 95% HPD、BSSVS 的 BF）走 BEAST 轨道（[BEAST](BEAST)）。两边**分开出结果、不混表**。

### 运行模式与续跑

- `pipeline`：一键全链，默认跑 `prep+clock+phylogeo+report`；勾 `skyline` 插 A3、勾 `ancestral` 插 A4。
- `stage`：只跑一个阶段，外加 A5 汇总。
- 配 `from_run`（上一轮运行名）可**接着上一轮产物续跑**：上轮只跑 prep+clock，这轮直接跑 phylogeo。

### 定年通路：只认 TreeTime

- `CLOCK_METHODS = ('treetime',)`；LSD2（严格钟最小二乘）与 treedater（非相关松弛钟）不在接线上。
- 理由：三法速率互不可比，维护三套结果口径的成本高于收益。下游（A2 地理 / A3 天际线 / A4 祖先序列）本来就只吃 TreeTime 的节点年代。
- 底层实现仍在 `phylogeo.py` / `treedater.py` 里，未接线。

### A3 重新跑一次 clock 的原因

- A1 与 A3 是**两次独立的 TreeTime 调用**，不是同一次。
- 原因：`--coalescent skyline` 会改变速率估计（RSV 209 实测：不带 3.07e-4，带 2.45e-4）。把两个模型的数字混在一行里，就没法说清「这个速率是哪个模型的」。
- 所以 `clock_stats.tsv` 只记 A1；A3 的天际线连同它自己那次的速率，单独落在 `coalescent/` 下。

### 树类引擎（phylodyn_trees）

`phylodyn_trees.py` 与 VirPhyKit（Yin et al., 2025, Ecol Evol）方法学对齐、代码自研。一键链用到的几项：

| 引擎 | 功能 |
|---|---|
| MCC 注释树解析 | BEAST MCC 树的节点状态 / 高度读取（`mot.py` 口径） |
| TempMig | 离散性状迁移时序 |
| RSPP | 序列抽样代表性 |
| **BSP**（Bayesian skyline plot） | 种群动态历史（`bsp.py`，含 ESS 有效样本量诊断） |
| LTT | lineage-through-time 曲线 |

- RRT / DRT（时间信号与定年检验）已独立成篇 → [Rtt](Rtt)。
- BSP-Viz 的口径差异（记在模块 docstring）：Tracer 式 TSV（Time/Median/Lower/Upper）为精确口径；BEAST `.log`（popSizes/groupSizes/rootHeight）为**近似口径**（组界按等区间数近似，前端明确标注）。

### TreeTime ML 引擎（本地 ML）

`treetime_ml.py`（~1900 行）封装 **TreeTime 0.12.1**：

| 子能力 | 内容 |
|---|---|
| **clock 分子钟定年** | 时间树、进化速率、R²；可选**天际线**（coalescent 模型 + 置信带）；`audit_clock` 自动体检（叶数 / 速率 / R² 阈值判定） |
| **mugration 地理重构** | 离散性状 ML 祖先态重构 + GTR 迁移模型 + 置信度；`audit_mugration` 体检；**采样偏倚校正系数**建议 `(1−Σpᵢ²)/(1−Σtᵢ²)`；**对拍交叉验证**（对拍树状态重算走廊，rel_diff > 0.2 判 divergent）；ML 与 Fitch 两条口径走廊并列成表 |
| **ancestral 祖先序列** | 祖先序列重构 + 逐枝替换 |
| **homoplasy 同塑性** | 替换饱和 / 同塑性检验 |
| 环境治理 | 逐级找 treetime 入口（env → platform.json → 3rd/tools → Scripts → PATH）、locale 剥离、不抛异常的超时封装、注释 Newick 解析 |

### 五条写死的红线

1. **每个数字都带方法名**（`clock_stats.tsv` 有 `method` 列，走廊表分 `ml` / `fitch` 两列）。
2. **点估计与后验分开列表**，不混排名。
3. **ML 边际概率 ≠ 贝叶斯后验**，措辞不简化。
4. **采样偏倚检查默认开启**（`sampling_bias_correction='auto'`）。
5. **时间信号未过 DRT 时不输出定年结论**。RSV 209 实测 R²=0.02–0.04；DRT 不显著时，速率 / tMRCA 只是把噪声拟合出的斜率。

- A1 若 DRT 未通过，`clock_qc/drt.json` 记 `passed=false` 并把警告写进该阶段；A3 读到不通过时把天际线降级为「仅作参考输出」，结果里带 `drt_failed=true`。
- A2 的 `corridors.tsv` 里 `ml`（ML 点估计）与 `fitch`（最少变化数的一种悬挂）**不是同一个统计量**。总数可以接近，但逐走廊方向差很多——汇总里两列都给。
- skyline 的上下界是 TreeTime 的近似区间（±2 SD of the LH），**不是**贝叶斯 95% HPD；汇总里带 `is_hpd=False` 标记。

### 典型工作流

```text
FASTA + 时间地点元数据
  → 元数据治理（govern：归一/体检）
  → 比对 QC（alignqc：剔除坏序列）
  → （可选）GeoSubsampler 时空降采样
  → 建树（复用平台 NJ/FastTree/RAxML-NG）
  → RRT/DRT 分子钟检验 → TreeTime clock 定年（+天际线 BSP）
  → TreeTime mugration 地理重构（迁移走廊 ML/Fitch 双口径 + 对拍验证）
  → 迁移弧线动画 GIF（见 Phylogeography）
```

- 前四步在 [Pd-Prep](Pd-Prep) 里做（数据接入 + 治理 + QC + 降采样）；一键链本身从 A0 起跑。
- 定年检验的判读见 [Rtt](Rtt)。

## 产物

### 目录结构

```text
run/tool_runs/<phylodyn_run>/
├── prep/         dates.csv · states.csv · prep.fasta · prep.nwk · prep_report.json
│                 （勾了剪枝另有剪枝产物；给了重命名映射时三处同改的结果也落这里）
├── clock/        timetree.nwk · clock_stats.tsv · rtt_scatter.tsv
│                 divergence_tree.nwk · clock_qc/drt.json
├── phylogeo/     GTR.txt · confidence.csv · annotated_tree.nexus
│                 corridors.tsv（ml / fitch 两列）· events_year.tsv · phylogeo_report.json
├── coalescent/   skyline.tsv · coalescent_report.json
├── ancestral/    ancestral_sequences.fasta · branch_mutations.txt · auspice_tree.json
├── report/       run_summary.json（逐产物 sha256 前 16 位）· config.resolved.json · RUN_REPORT.md
├── extras/       homoplasy.tsv（勾了同塑性检查才有；TreeTime 只在 stdout 出结果，平台替它落盘）
└── phylodyn_summary.json   前端结果面板的数据源（阶段状态 / clock / geo / skyline / warnings / 文件清单）
```

### 关键文件与字段

| 文件 | 内容 |
|---|---|
| `clock/timetree.nwk` | 时间树，枝长＝年；A2 / A3 的接口产物 |
| `clock/clock_stats.tsv` | A1 定年统计，**带 `method` 列** |
| `clock/clock_qc/drt.json` | DRT 检验结果（`passed` / 分位 / 结论） |
| `phylogeo/corridors.tsv` | 迁移走廊，`ml` 与 `fitch` 两列并列 |
| `phylogeo/events_year.tsv` | 逐年的迁移事件 |
| `coalescent/skyline.tsv` | 天际线；上下界带 `is_hpd=False` |
| `ancestral/ancestral_sequences.fasta` | 祖先序列 |
| `report/run_summary.json` | **引擎级**溯源：每产物 size + sha256 前 16 位 + 实际生效配置 |
| 根目录 `phylodyn_summary.json` | **任务级**面板摘要 |

- 产物目录名 = 阶段键（`prep` / `clock` / `phylogeo` / `coalescent` / `ancestral` / `report`）；`out_dir` 就是本轮运行目录 `run/tool_runs/<phylodyn_run>/`。
- `report/run_summary.json` 与根目录的 `phylodyn_summary.json` **不是同一个文件**，前者溯源、后者供面板。

## 示例输入与输出

### 示例输入

接入 [Pd-Prep](Pd-Prep) 交付数据集后，A0 的两个入参就是 `summary.json` 里的 `abs.seq` / `abs.meta`：

```json
{
  "mode": "pipeline",
  "input": "run/tool_runs/pdprep_20261105_093000_ab12cd34/pdprep/sequences.fasta",
  "meta": "run/tool_runs/pdprep_20261105_093000_ab12cd34/pdprep/metadata.csv",
  "methods": "treetime",
  "drt": true,
  "skyline": true,
  "ancestral": true
}
```

只补跑地理阶段时把 `mode` 改成 `stage`、`stage` 填 `phylogeo`，`from_run` 填上一轮的运行名。

### 示例输出

```text
run/tool_runs/phylodyn_20261105_101500_ab12cd34/
├── prep/ · clock/ · phylogeo/ · coalescent/ · ancestral/ · report/
└── phylodyn_summary.json
```

- 阶段目录内各文件的清单见「产物」节；本轮实际跑了哪些阶段，看面板的阶段状态表。
- 跑完读根目录 `phylodyn_summary.json` 渲染结果面板；阶段状态、速率 / R² / DRT 判定与走廊两列都在里面。

## 界面与 CLI

### 界面

- 工具卡 `t-phylodyn`「⚡ 一键分析」：`pipeline` / `stage` 两种模式 + 从上一轮续跑。
- 跑完渲染结果面板：阶段状态表、剪枝、clock 速率 / R² / DRT、迁移走廊 ML / Fitch、天际线、祖先序列、warnings、文件清单。
- 任务日志收尾另打印「阶段状态 / 时间树来源 / tMRCA / DRT 判定 / 走廊两列不可相加 / 采样偏倚校正」。
- 迁移弧线动画另见 [Phylogeography](Phylogeography)。

### CLI 与 agent

- `main.py` **没有 phylodyn 子命令**（`add_parser` 核实）。
- 外部 agent 走操作目录 id `phylodyn`：`python main.py agent-ops --id phylodyn --schema` 查参数，MCP `vp_run_tool` 执行。
- 只想单跑某一步：数据接入 / 治理 / QC / 降采样 → [Pd-Prep](Pd-Prep)；时间信号 → [Rtt](Rtt)；重组掩蔽 → [Rdp](Rdp)。

## 相关

- 组导读 → [Phylodynamics](Phylodynamics)
- 数据接入与元数据治理 → [Pd-Prep](Pd-Prep)
- 时间信号与定年判读 → [Rtt](Rtt)
- 贝叶斯轨道（MCMC 外包）→ [BEAST](BEAST)；地图与迁移 GIF → [Phylogeography](Phylogeography)
