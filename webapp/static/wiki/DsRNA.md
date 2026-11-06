# DsRNA · dsRNA 全链路设计

> 返回 [Home](Home) ｜ 上一页：[LAMP-Design](LAMP-Design) ｜ 下一页：[Mirna](Mirna)

从病毒序列到可体外转录的 dsRNA 成品。链路：dsRNAmax 确定性选窗 → dsRIP 效价 → 面板脱靶扫描 → T7 引物 → 成品扩增子 QC。输入是病毒基因组或片段 FASTA（可选分离株集与脱靶面板），产物是 T7 引物对与成品扩增子。

打分对象始终是交付分子。引物限定在最优窗内设计；引物定稿后对真实扩增子重跑效价与脱靶扫描，避免「300 nt 臂上打分、215 bp 产物交付」。

对应引擎：`Virus_Platform_Core/dsrna_pipeline.py`（编排）、`dsrna_scoring.py`（效价 / 安全评分）、`dsrna_offtarget.py`（面板脱靶扫描）。工具卡 `t-dsrna`（检测与防治 → dsRNA 设计）；无 CLI 子命令；操作目录 id `dsrna`。外部依赖：dsRNAmax（`3rd/tools/dsrnamax/`，可在 `platform.json` 的 `dsRNAmax` 登记路径）、primer3、ViennaRNA。

## 输入

### 输入格式

- 目标 FASTA：病毒基因组 / 片段，可多条（示例为 3 条）。
- 可选分离株 FASTA：对成品扩增子做 21-mer 双向覆盖度核查。
- 可选脱靶排除 FASTA 与面板库：`databases/dsrna/panel/`（配 `lethal_lists/<物种>_all_lethals.tsv`）。
- 面板不随软件分发；不放置时自动跳过脱靶扫描与安全评分（效价照常），放入即启用。

### 参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `construct_len` | 300 | 臂长 nt |
| `kmer_len` | 21 | 选窗 k-mer 长度 |
| `iterations` | 100 | dsRNAmax 迭代次数 |
| `candidates` | 3 | 每窗候选数 |
| `n_windows` | 1 | 臂条数（鸡尾酒多臂时加大） |
| `seed` | 42 | 随机种子，保证确定性构建可复现 |
| `max_mm` | 1 | 脱靶扫描错配档（界面可选 0 / 1 / 2） |
| `efficiency_priority` / `safety_priority` | 50 / 50 | 效价与安全的合成权重 |
| `safety_essential_coeff` | 20 | 致死基因命中的加权系数 |
| `primer_scope` | `best_window` | 引物范围，可切 `full_arm` |
| `primer_product_min` / `primer_product_max` | 200 / 300 | 产物上下限 bp |
| `primer_num_return` | 3 | 每臂返回的引物对数 |

## 流程

### 步骤一：确定性选窗（dsRNAmax）

1. 每窗跑多候选，按「最差目标命中、中位、总和」做 maximin 重排——中位数会遮蔽离群株。
2. 命中 0、或覆盖不足最优目标 25% 的目标显式告警。
3. 已选臂对应区段屏蔽后取下一窗。
4. 内存按 160 B × 去重 k-mer × iterations 预估，超预算 fail-fast，不等 OOM。

### 步骤二：效价评分（dsRIP）

- dsRIP 论文公式（Cedden et al., BMC Biology 2025, 23:114 忠实移植）：asymScore（四核苷酸热力学不对称，权重 5）+ 自折叠 MFE（ViennaRNA，权重 3）+ 各 bonus。
- 数据取 `databases/dsrna/lookup.db` 与 `siRNA_parameters.txt`。

### 步骤三：面板脱靶扫描

1. 对面板物种 RefSeq mRNA 建 2-bit k-mer 排序 uint64 数组。
2. 逐 siRNA 按 0→1→2 错配档扫描（`max_mm` 默认 1）。
3. 致死基因命中按 `safety_essential_coeff`（默认 20）加权；0 错配致死基因命中一票否决。
4. 臂内相对归一化后与效价按 `efficiency_priority` / `safety_priority` 合成，滑窗（250–350 siRNA）取 combined 最优窗。

### 步骤四：T7 引物设计

输入是上一步的臂与最优窗坐标。

- 复用平台统一引物引擎 `primer_design.design_for_sequence`（PCR 档），限定在上一步的最优窗。
- T7 启动子 `TAATACGACTCACTATAGGG` 加在正反引物上：PCR 产物两端各一个启动子，IVT 双向转录自发退火成 dsRNA。
- primer3 不可用时该臂无引物，但整链不失败。

### 步骤五：成品扩增子 QC

1. 对真实扩增子重跑效价（`amplicon_qc`）与面板扫描。
2. 计算 `window_overlap`：扩增子落在最优窗内的占比。
3. 可选分离株 FASTA 做 21-mer 双向覆盖度；最低分离株 <90% 时告警逃逸风险。

## 产物

### 目录结构

```text
run/tool_runs/<dsrna_run>/dsrnamax/
├── arms.fa              中选 dsRNA 臂（sense）
├── result.csv           dsRNAmax 中选候选原生表
├── primers.tsv          T7 引物对（产物长度 / Tm / 臂内位置 / 扩增子）
├── amplicons.fa         成品扩增子序列
├── offtarget_lethal.tsv 致死基因命中明细（有面板时）
├── report.json          全量报告（参数 / 评分 / 脱靶 / 引物 / QC / 覆盖度）
├── summary.json         摘要（产物路径与计数）
└── targets_w*.fa / cand_w*_c*.csv   各窗目标与候选中间文件
```

### 关键文件与字段

| 文件 | 内容与关键字段 |
|---|---|
| `arms.fa` | 中选臂序列，命名 `arm_<n>` |
| `result.csv` | dsRNAmax 原生候选表：目标 header、21 nt 匹配数、SWG 相似度、k-mer 平均 GC、5′ 端 U / A / C 占比 |
| `primers.tsv` | `arm` / `idx` / `forward_T7` / `reverse_T7` / `product_bp` / `f_tm` / `r_tm` / `amp_start` / `amp_end` / `amplicon` |
| `amplicons.fa` | 成品扩增子，命名 `arm_<n>_p<k>` |
| `report.json` | 参数、内存守卫、逐臂评分、脱靶汇总、引物、`amplicon_qc`、`isolate_coverage`、`red_lines` |
| `summary.json` | 目标数、臂数、引物对数、面板物种数、耗时 |

## 示例输入与输出

### 示例输入

`examples/results/dsrna/` 对应的运行 `dsrna_20261006_162703_fab169`：目标 FASTA 3 条（`NC_002034` / `NC_002035` / `NC_001440`），臂长 300 nt、k-mer 21、迭代 100、候选 3、臂条数 1、`seed` 42，面板 0 个物种（不做脱靶扫描）。

### 示例输出

- 1 条臂 `arm_1`（300 nt，280 个 siRNA），效价中位数 73.05、均值 72.73；最优窗 nt 16–285，combined 均值 87.08（效价 74.17 / 安全 100）。
- 3 对 T7 引物，产物 224 / 209 / 240 bp，Tm 59.9 / 59.2 / 59.1。
- 扩增子 QC 全部 `ok`：首对扩增子 224 bp，效价中位数 75.65，最优窗 combined 均值 74.81。
- 整链耗时 1.4 s；dsRNAmax 版本 1.1.15。

## 界面与 CLI

### 界面

工具卡 `t-dsrna`：目标 FASTA + 可选脱靶排除 FASTA + 面板勾选 + 臂长 / k-mer / 迭代 / 候选 / 臂条数 / seed / 错配档 + 引物范围 / 产物上下限 + 可选分离株 FASTA；结果区给摘要与臂表。

### CLI 与 agent

`main.py` 没有 `dsrna` 子命令，CLI 入口不存在。

- agent：操作目录 id `dsrna`，schema 查询 `python main.py agent-ops --id dsrna --schema`，执行走 MCP `vp_run_tool`。

## 相关

- 宿主 miRNA 侧的天然防治线索 → [Mirna](Mirna)
- 检测试剂（crRNA × RPA 引物）→ [CRISPR-Design](CRISPR-Design)
- 其他单步工具与产物目录 → [Toolbox](Toolbox)
