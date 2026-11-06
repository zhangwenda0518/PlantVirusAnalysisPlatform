# Primer · ⑧ 引物设计

> 返回 [Home](Home) ｜ 上一页：[Detect-Intro](Detect-Intro) ｜ 下一页：[CRISPR-Design](CRISPR-Design)

把验证通过的病毒序列变成可下单的 PCR / qPCR 引物。两种设计模式（保守区通用引物、单序列快速设计）、热力学质量评分、可选的宿主特异性筛查。输入是一条目标序列或一份已比对 MSA，产物是一张带评分与建议的引物表。

对应引擎：`primer.py`（编排）、`primer_design.py`（从序列出发）、`primer_thermo.py`（热力学评分）、`primer3_runtime.py`（primer3-py 安全接入层）。管线阶段 ⑧，无独立工具卡；操作目录 id `primer`。

## 输入

### 输入格式

| 模式 | 输入 | 说明 |
|---|---|---|
| `conserved` | 已比对 MSA | 取 ⑦ `05_phylo/<组>/aln.fasta`，在一致序列上找保守区 |
| `plain` | 单一目标序列 | FASTA，未比对也可；按 PCR / qPCR 参数直接设计 |

- `conserved` 需要 ⑦ 的 `05_phylo/summary.json`；缺失时自动降级为 `plain`，并在日志写 WARN。

### 参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `mode` | 管线 `conserved`，独立工具 `plain` | `conserved` 保守区通用引物 / `plain` 单序列快速设计 |
| `num_return` | 3 | 每条序列返回的引物对数（1–20） |
| `specificity` | 关 | 宿主特异性 BLAST 检查，需先建宿主 BLAST 库 |
| `--primer-mode` | `conserved` | CLI 侧模式参数（`analyze` 阶段） |
| `--specificity` | — | CLI 开关，等价于 `specificity` |

## 流程

### 模式一：conserved（保守区通用引物）

1. 读入 ⑦ 的多序列比对，逐列统计保守性并取一致序列。
2. 在保守区上设计引物，一对引物可跨多个近缘分离物通用。
3. 适用于跨近缘参考的广谱检测引物。

### 模式二：plain（单序列快速设计）

1. 读入单条目标序列。
2. 按 PCR / qPCR 参数直接设计引物对。
3. 适用于单一序列的快速出引物场景。

### 热力学评估与评分

`primer_thermo.py` 独立成层，给出 Tm、GC%、发夹 / 自二聚 / 异二聚体 ΔG 与产物大小。

- 每条引物对带质量评分 `score` 与建议 `recommendation`：≥80 为推荐（`RECOMMENDED`），≥60 为可用（`USABLE`），更低为不建议（`NOT_RECOMMENDED`）。
- 二聚体风险在表中直接标注，不隐藏在总分里。

### 宿主特异性检查（可选）

1. 对 1.8 GB 宿主基因组建 BLAST 库——首次 10–30 分钟，此后复用。
2. 每条引物 BLAST 宿主基因组，命中即标警示，写入 `host_hit` 列。

### primer3 安全接入层

primer3-py 的 C 扩展在 import 时要一次性预计算热力学表，内存峰值大。

平台把 primer3 导入收敛到 `primer3_runtime.py` 唯一入口：延迟初始化加子进程隔离，GUI 主进程不会被撑爆。

## 产物

### 目录结构

```text
run/results/<样品>/06_primer/
├── primers.tsv      引物表（每对一行）
└── summary.json     小结（模式 / 引物对数 / 特异性检查状态 / 分组）
```

### 关键文件与字段

`primers.tsv` 的列：

| 列 | 含义 |
|---|---|
| `pair` | 引物对 id（组_保守区_序号） |
| `target` | 设计目标（保守区编号与比对列区间） |
| `F_primer(5'-3')` / `R_primer(5'-3')` | 正向 / 反向引物序列 |
| `F_pos` / `R_pos` | 引物在模板上的位置 |
| `F_Tm` / `R_Tm` | 解链温度 |
| `F_GC(%)` / `R_GC(%)` | GC 含量 |
| `product(bp)` | 产物长度 |
| `penalty` | 罚分 |
| `self_dimer_Th` / `self_hairpin_Th` | 自二聚 / 发夹阈值指标 |
| `score` | 质量评分（0–100） |
| `recommendation` | `RECOMMENDED` / `USABLE` / `NOT_RECOMMENDED` |
| `host_hit` | 宿主命中；未做特异性检查时为 `NA` |

## 示例输入与输出

### 示例输入

`examples/results/primer/05_phylo/G1/aln.fasta`：1 株参考 `PZ530881.1|Odontoglossum_ringspot_virus` 加 4 株合成分离物（blockwise 突变，`seed=20261006`），共 5 条序列的 MSA。分组 `G1`，conserved 模式设计。

### 示例输出

`examples/results/primer/06_primer/primers.tsv` 共 9 对引物，覆盖 3 个保守区：

| pair | 保守区 | 产物 | score | recommendation |
|---|---|---|---|---|
| `G1_C1_P1` | 保守区 1（比对列 1–487） | 367 bp | 77.8 | `USABLE` |
| `G1_C2_P1` | 保守区 2（比对列 2286–2774） | 345 bp | 95.6 | `RECOMMENDED` |
| `G1_C3_P2` | 保守区 3（比对列 6191–6613） | 303 bp | 91.1 | `RECOMMENDED` |
| `G1_C3_P3` | 保守区 3（比对列 6191–6613） | 329 bp | 73.3 | `USABLE` |

`summary.json` 记录 `mode=conserved`、`n_primers=9`、`specificity_checked=false`、`specificity_amplicon=skipped`。

## 界面与 CLI

### 界面

分析管道 ⑧ 卡片：引物模式下拉 + 宿主特异性检查开关 + `📊 查看`（引物表就地浏览与下载）。

### CLI 与 agent

```bat
python main.py analyze --r1 ... --sample S1 --stages primer ^
       --primer-mode conserved --specificity
```

- 独立工具：操作目录 id `primer`，参数 `fasta` / `mode` / `num_return` / `specificity`，可经 MCP `vp_run_tool` 调用。
- 参数 schema 查询：`python main.py agent-ops --id primer --schema`。

## 相关

- dsRNA 侧的引物（siRNA 表达载体）→ [DsRNA](DsRNA)
- 保守区来自哪 → [Phylo](Phylo) ⑦ 多比对
