# Rtt · 时间信号与定年

> 返回 [Home](Home) ｜ 上一页：[Pd-Prep](Pd-Prep) ｜ 下一页：[Rdp](Rdp)

**根到尾回归**回答「这些序列里到底有没有可用的时间信号、速率是多少」：R² 高＝时间信号强，斜率＝每位点每年替换数。显著性用**日期随机化的零分布**判，不用固定阈值。

对应引擎 `Virus_Platform_Core/phylodyn_trees.py`（`treetime_rtt` / `inhouse_rtt` / `_rtt_points_and_fit` / `_perm_test_r2`）。同口径实现见 `treetime_rtt.py`（VirPhyKit「TreeTime-RTT」），DRT 零分布检验在 `rtt_drt.py`。工具卡 `t-rtt`；操作目录 id `rtt`。

## 输入

### 输入格式

- `input`：等长比对 FASTA（MSA）。
- `meta`：元数据 CSV，含日期列。
- 树可不给：卡内先建（`method=nj` / `fasttree`），再在该树上算根到尾距离。
- 序列名需与元数据一一对应；元数据里多出来的叶会如实计数上报。

### 参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `input` | 必填 | 比对 FASTA（等长 MSA） |
| `meta` | 必填（卡） | 元数据 CSV，含日期列 |
| `date_trait` | 自动识别 | 日期列；`date` > `year` > 常见日期列名 |
| `method` | `nj` | 卡内建树工具：`nj` / `fasttree` |
| `n_perm` | 100 / 999 | 置换次数（`phylodyn_trees.inhouse_rtt` 默认 100；DRT 引擎默认 999） |
| `rooting` | `optimize` | DRT 定根口径 `optimize` / `asis` / `optimize_once` |
| `seed` | 42 | 置换随机种子 |

- 日期列不写死 `year`：显式 `date_trait` > 表头里第一个日期形列（`date` / `collection_date` / `year` / `decimal_year` / `采样日期`…）。[Pd-Prep](Pd-Prep) 标准数据集列名是 `date`，直喂可直接命中。

## 流程

### RRT（root-to-tip 回归）

- 实现是 TreeTime ML 同口径重实现：`treetime_rtt.py` 走 `treetime_ml.run_clock_api`，速率 / R² 直接读 TreeTime 收敛产物 `molecular_clock.txt`。
- 刻意**不**照抄上游「正则抠日志 β/R² + scipy.linregress 自算 p」：实测那是数学恒等式（R²≡1、p≡0，见模块 docstring ①）。
- 上游 p 值为何是恒等式：`tt.run()` 之后 `tt.tree` 的枝长已等于 `clock_rate × 时间`。沿根到叶求和再与采样年回归，必然严格线性。
- 实测该回归的 `slope` 与 `tt.date2dist.clock_rate` 一模一样、`R²=1.00000000`、`p=0.000e+00`。换任何数据都这样，所以**不能**当显著性证据。
- 显著性改由 `rtt_drt.py` 的零分布检验给出，双口径修正 AUDIT 缺陷。
- 与上游的三处刻意差异（都在 `treetime_rtt.py` 里写死并随结果输出）：
  1. 不输出上述 p 值；
  2. β / R² 取 **TreeTime 收敛值**。上游正则与 treetime 0.12.1 的 `rate=` 格式不兼容，抓到的是第 1 轮迭代值，实测与收敛值差 0.7%；
  3. 固定 `rng_seed=0`，同输入同结果（上游不设种子，两次实测差 1.0%）。
- 显式**对齐**上游的开关：
  - `clock_filter=0`（CLI 默认 4.0 会筛叶，上游 API 不筛）；
  - `gtr=JC69` + `infer_gtr=False`（上游固定 Jukes-Cantor，实测与 `Jukes-Cantor` 的 W/mu/Pi 全等且产物逐字节相同）；
  - `pre_reroot=True`（上游在 `run()` 之前先 reroot，实测差 0.84%）；
  - `verbose=4`、`reroot=least-squares`。
- TreeTime 自己把 R² 只写到 2 位小数（`molecular_clock.txt` 写死格式），所以除 `rate` / `r2` 原值外另给 `rate_precise` / `r2_precise`。
- 全精度值从 TreeTime 自己写的 `divergence_tree.nexus` 把同一回归重算；两者差 >0.05 进 `warnings`（说明散点树与定年树不是同一棵）。
- 回归前三条守卫：
  1. 树 × 元数据只取**两边都有**的叶（元数据里多出来的叶计数上报 `n_meta_not_on_tree`，不会因多出来的叶中断整条链）；
  2. 可比叶 <3 直接拒绝（2 个点必给出 R²=1.0 的完美假象）；
  3. 采样日期全相同（x 无变化）直接拒绝。

### DRT（date-randomization test）

- 把日期在 tip 之间随机置换 N 次，每次用同一个统计量重算，看真实值在零分布中的**百分位**（≥95% 才通过）。阈值是数据自己的分位数，不再依赖叶数。
- 为什么必须有它：「取最大 R²」等价于在 ~2n−3 个候选根位上取**极值**，零假设下这个最大值同样被抬高（AUDIT 实测放大 4.4–5.4 倍）。
- 按固定阈值 0.5 判的误判率：8 叶 23.5% / 12 叶 7.0% / 20 叶 0.5% / 30 叶 0%，而 8–20 叶正是病毒数据集的家常便饭。
- 三种定根口径（`rooting=`，语义不同且都自洽）：
  - `optimize`（默认）：每轮（含真实数据）各自取 R² 最大的根位。零分布 = 「随机日期下能达到的最好 R²」，与真实统计量同构，比较公平（发布版管道行为）。
  - `asis`：全部轮次固定用树**自带**的根位，不搜索。只动日期一个变量，语义最干净；但树是无根 NJ/FastTree 产物时根位本身任意 → 可能低估（假阴性）。
  - `optimize_once`：真实数据上挑一次最优根位后冻结给所有轮次。⚠️ **反保守**——真实值被最大化、零分布没有 → 通过率被抬高；仅供诊断 / 复现历史编号，结果里带 `warnings`。
- 默认参数：`DEFAULT_N_PERM = 999`（20 次置换时 p 值分辨率下限 1/21≈0.048，「显著与否」几乎由最极端一次决定）、`DEFAULT_SEED = 42`、`DEFAULT_MIN_TIPS = 5`、显著性水平 `DRT_ALPHA = 0.05`。
- 置换流用 `np.random.default_rng(seed).permutation(n)`，与参照管道同一个整数置换序列（同数据同种子可逐轮对拍）。只依赖 numpy，不引入 TreeTime / scipy。
- 出处：Duchêne et al. 2015, *Mol Biol Evol* 32:1895–1906；两版实现（`temporal_signal.py` 与 `_drt_tt3.py`）的口径差异就是上面的 `rooting` 参数。

### 卡与链的分工

| 入口 | 树从哪来 | 产物 | 用途 |
|---|---|---|---|
| 工具卡 `t-rtt` | 卡内先建（`method=nj` / `fasttree`） | `rtt/rtt.json`、`rtt/rtt_scatter.tsv`、`rtt/tree.nwk`、`rtt/tree.date.nexus`、`rtt/summary.json` | 单次时间信号体检 + 给系统地理一张带年代的树 |
| 一键链 A1（[Phylodyn](Phylodyn)） | A0 的 `prep.nwk` | `clock/clock_qc/drt.json`、`clock/clock_stats.tsv`、`clock/rtt_scatter.tsv` | 定年前闸门（红线 5：DRT 不通过不输出定年结论） |

- 卡上 `summary.json` 的口径：`slope` / `intercept` / `r2` / `n` / `method` / `trait` / `strong_signal`（`r2 >= 0.5`，仅作快速浏览的粗筛，**不是**显著性判据）/ `dated_nexus` / `warnings`。
- 实现在哪一层（源码核实）：工具卡走 `phylogeo.analyze` 建树 + `phylogeo.root_to_tip`（在候选根位上**取最大 R²**），再自己写 `rtt.json` / `rtt_scatter.tsv` / `tree.date.nexus`。
- 「取最大 R² + 固定阈值」正是卡上口径的缺陷，所以**卡上的 R² 只当描述量**，显著性判据在 A1 的 DRT 里。
- 卡会把 RTT 的斜率 / 截距写成 **LSD2 兼容的带年代 NEXUS**（`<树名>.date.nexus`），供 [Phylogeography](Phylogeography) 的 `dated_tree` 直连。
- 卡上 `analyze()` 的区划类告警会被滤掉（RTT 是把日期列当 trait 传进去共用一次建树，日期值不是区划）；其余告警逐条写任务日志。

## 产物

### 目录结构

```text
run/tool_runs/<rtt_run>/rtt/
├── tree.nwk           卡内建的树（NJ / FastTree）
├── tree.date.nexus    带年代的 NEXUS（LSD2 兼容，给系统地理 dated_tree）
├── rtt.json           散点 + 回归（points / slope / intercept / r2 / n）
├── rtt_scatter.tsv    name · root_dist · year
└── summary.json       摘要（trait / method / strong_signal / warnings / dated_nexus）
```

### 关键文件与字段

| 文件 | 内容 |
|---|---|
| `rtt.json` | 散点与回归：`points` / `slope` / `intercept` / `r2` / `n` |
| `rtt_scatter.tsv` | 三列：`name` · `root_dist` · `year` |
| `tree.date.nexus` | 带年代的 NEXUS，LSD2 兼容，可直接拖进系统地理卡 |
| `summary.json` | `trait` / `method` / `strong_signal` / `warnings` / `dated_nexus` |

- TreeTime 轨道（`treetime_rtt.run_treetime_rtt`）另产：`timetree.nwk`（枝长＝年）、`timetree_segments.json`（面板直角坐标）、`treetime_log.txt`（完整 stdout，含逐轮 rate 可核对收敛）、`dates.csv` / `aln.norm.fasta`（喂引擎的规范化输入），以及 TreeTime 自己写的 `divergence_tree.nexus`。
- 一键链的 DRT 结果固定落 `clock/clock_qc/drt.json`，字段含 `real_r2` / `real_slope` / `r2_percentile` / `p_value` / `passed` / `passed_percentile95` / `conclusion` / `verdict`。

## 示例输入与输出

### 示例输入

等长比对 FASTA（`input`，≥3 条可比叶）+ 元数据 CSV（`meta`，`name` 与序列名一致，`date` 为日期列）：

```csv
name,date,location
SIM_Asia_1|Asia|2001,2001,Asia
SIM_Asia_2|Asia|2003,2003,Asia
SIM_Europe_1|Europe|2006,2006,Europe
SIM_Americas_1|Americas|2010,2010,Americas
```

### 示例输出

```text
run/tool_runs/rtt_20261105_101500_ab12cd34/rtt/
├── tree.nwk · tree.date.nexus
├── rtt.json · rtt_scatter.tsv
└── summary.json
```

- 各文件的字段口径见「产物」节；`strong_signal` 只是粗筛，判读看 DRT。
- DRT 不通过时，速率与 tMRCA 只能作记录，不得作定年结论（[Phylodyn](Phylodyn) 红线 5）。

## 界面与 CLI

### 界面

- 工具卡 `t-rtt`「时间信号与定年」：输入比对 FASTA + 元数据 CSV → 运行。
- 结果区给 R² / 斜率 / 散点 / 判读提示；`tree.date.nexus` 可直接拖进系统地理卡。

### CLI 与 agent

- `main.py` **没有 rtt 子命令**（`add_parser` 核实）。
- 外部 agent 走操作目录 id `rtt`：`python main.py agent-ops --id rtt --schema` 查参数，MCP `vp_run_tool` 执行。
- 判读纪律：R² 只说明散点贴合程度，**显著性看 DRT**。

## 相关

- 组导读 → [Phylodynamics](Phylodynamics)
- 全链定年与地理重构 → [Phylodyn](Phylodyn)
- 数据接入与 QC（时间信号差的常见根因）→ [Pd-Prep](Pd-Prep)
- 重组会污染时间信号：先掩蔽再定年 → [Rdp](Rdp)
