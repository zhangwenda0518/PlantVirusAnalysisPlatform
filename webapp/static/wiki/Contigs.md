# Contigs · 病毒鉴定

> 返回 [Home](Home) ｜ 上一页：[Assembly](Assembly) ｜ 下一页：[Verify](Verify)

任意来源的 contigs FASTA → 长度过滤 → kunpeng 病毒库分类 → 8 级谱系表。
最后提取病毒 contigs，并顺带跑宿主预测。

对应引擎：`Virus_Platform_Core/web/tool_jobs.py`（`_tool_job_contigs`）+ `kunpeng.py`（`classify`）+ `contig_annot.py`（`classify_rows`）；工具卡 `t-contigs`（工具箱「病毒识别和分类分析」组）；操作目录 id `contigs`。

---

## 输入

### 输入格式

- contigs FASTA：`.fasta` / `.fa` / `.fna`，可粘贴。
- 目录批量只解析 contigs FASTA；FASTQ / `.sra` 样本跳过。

### 参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `min_len` | 200 bp | 最短 contig；低于阈值先被过滤，过滤后 0 条直接报错 |
| `confidence` | `0` | kunpeng `-T` 分类置信度；`0` 最灵敏，0.05–0.2 常规，≥0.3 严格 |
| `db_virus` | 平台主库 | kunpeng 分类用库：内置键 `virus` / `refvirus` 或库目录 |
| `threads` | 自动 | 分类 / 注释线程 |

- BLAST 注释与属长缓存仍基于主病毒参考库。

## 流程

### 步骤一：统计属平均基因组长度

- 统计属平均基因组长度，存 `genus_avg_map`，首跑建缓存。
- 该值用于 `ratio = length / genus_avg_len` 与近完整判定。

### 步骤二：长度过滤

- `filter_contigs` 按 `min_len` 过滤，写 `contigs.filtered.fasta`。

### 步骤三：kunpeng 分类

- kunpeng 对 contigs 分类，输出与 kraken2 同构的 C 行。
- LCA 判到上级节点时，经 `nodes/names.dmp` 谱系还原完整分类。
- 产物落 `classify/output_1.txt` 与 `output_1.kreport2`，只取 C 行。

> 范围说明：工具卡 `t-contigs` 只跑 kunpeng 分类与谱系表，**不跑 BLAST**。
> 管线阶段 ③（`assemble_and_classify`）在分类后补跑本地 BLASTN。
> 参考库为 `databases/virusref_db/`，结果写进 `03_assembly/virus_contigs.tsv` 的 blast 六列。
> 卡面提示仍写「BLAST 注释」，以本条源码口径为准。

### 步骤四：生成 8 级谱系表

- `classify_rows` 生成 8 级谱系：`realm` → `kingdom` → `phylum` → `class` → `order` → `family` → `genus` → `species`。
- 同时给出 `taxon` / `taxid` / `length` / `genus_avg_len` / `ratio` / `near_complete` / `score`。
- `near_complete` 判定为 0.8 ≤ ratio ≤ 1.2。
- `score` 为支持该 taxid 的 k-mer 片段数 / 映射总片段数，另带 `kmer_support` / `kmer_total`。

### 步骤五：提取病毒 contigs

- 按 C 行提取病毒 contigs，header 带分类信息。
- 格式 `>contig taxid=<N> taxon=<种名> lineage=<realm;...;species>`。
- 产物 `viral_contigs.fasta`。

### 步骤六：自动宿主预测

- 有病毒 contig 且宿主库就绪时，同一任务内自动跑 ICTV 宿主预测，不再单独弹任务。
- 产物落 `08_host_analysis/`。
- 无病毒 contig 则跳过并记日志。
- 宿主预测失败只 WARN，不拖垮分类。

## 产物

### 目录结构

工具卡运行目录（`run/tool_runs/contigs_<时间戳>_<id>/`）：

```text
contigs.filtered.fasta          长度过滤后的 contigs
classify/output_1.txt           kunpeng 逐 contig 分类（kraken2 同构）
classify/output_1.kreport2      分类报告
virus_classification.tsv        8 级谱系 + 属长比 + 可靠性分值
viral_contigs.fasta             病毒子集（带 taxid/taxon/lineage header）
08_host_analysis/               自动宿主预测（有病毒 contig 且宿主库就绪时）
  host_prediction.tsv / host_summary.tsv / sankey_host.html / sunburst_host.html
  <类别>.classified.fasta
run.log                         任务日志
```

### 关键文件与字段

| 文件 | 内容 |
|---|---|
| `contigs.filtered.fasta` | 长度过滤后的 contigs |
| `classify/output_1.txt` | kunpeng 逐 contig 分类，kraken2 同构 |
| `classify/output_1.kreport2` | 分类报告 |
| `virus_classification.tsv` | 8 级谱系 + 属长比 + 可靠性分值 |
| `viral_contigs.fasta` | 病毒子集，header 带 taxid / taxon / lineage |
| `08_host_analysis/` | 宿主预测明细、汇总、桑基图、旭日图、分类子集 |
| `run.log` | 任务日志 |

管线阶段 ③ 的 `03_assembly/`（同目录另有组装产物）：

| 文件 | 内容 |
|---|---|
| `spades/` | 组装产物 |
| `contigs.filtered.fasta` | 长度过滤后的 contigs，`--min-contig-len` 默认 200 |
| `viral_contigs.fasta` | 病毒子集，④⑦⑧⑨ / LOGAN 共用 |
| `virus_contigs.tsv` | 11 列 = kunpeng 5 列 + blast 6 列 |
| `contig_blast.tsv` | BLAST 明细 |
| `virus_classification.tsv` | 8 级谱系表 |
| `unclassified_contigs.fasta` | 未分类但长度合格的候选池 |

> **契约**：`virus_contigs.tsv` 是 ④ 宿主预测的输入。
> blast 六列供 kunpeng 未判到 taxid 的 contig 走「最近参考 → 物种 / 科 → 宿主概率」回退。
> 手工表缺列时 ④ 自动补齐。

### 判读要点

- `score` 是分类可靠性，不是丰度：≥0.9 极可靠 · 0.5–0.9 高可信 · 0.1–0.5 需复核。
- `near_complete` 只按属均长比 0.8–1.2 判，跨属比较无意义。
- 未分类 contig 不是「无病毒」：管线会保留长度合格的 `unclassified_contigs.fasta`，供 ③b 独立验证。
- 工具卡不产该候选池，需要时用 [Verify](Verify) 直接对 FASTA 跑候选验证。
- 换库后属均长覆盖度估计可能部分缺失（按主库 reference 计算），卡面有提示。

## 示例输入与输出

### 示例输入

组装后的 contigs FASTA；头里的长度与覆盖度供选图与标注。

```text
>NODE_1_length_2418_cov_4.587207
TGACAATACTGTTAAGCAAGTGTATGCCGGTGACGACATGGCATTAGATGGAGTTGTGAG
TGAAAAGAAATCATTCAGGAAGTTGCAAAATCTACTAAAACTCACTTCAAAAACTCTGTA
CCCAAAACAGGTTAAAGGGGATTACGCTGAATTTTGCGGTTGGACATTCACCCCAGGGGG
TATAATTAAAAATCCACTTAAAATGCATGCCTCAATTATGCTGCAAGAAGCCATTGGCAA
```

### 示例输出

运行目录产出以下文件：

```text
run/tool_runs/contigs_20261105_120000_ab12cd/
├── contigs.filtered.fasta
├── classify/output_1.txt
├── classify/output_1.kreport2
├── virus_classification.tsv
├── viral_contigs.fasta
├── 08_host_analysis/
│   ├── host_prediction.tsv
│   ├── host_summary.tsv
│   ├── sankey_host.html
│   ├── sunburst_host.html
│   └── <类别>.classified.fasta
└── run.log
```

- `viral_contigs.fasta` 的 header 已带 `taxid` / `taxon` / `lineage`，可直接送 [Verify](Verify)。
- 有病毒 contig 且宿主库就绪时，`08_host_analysis/` 才会出现。

## 界面与 CLI

### 界面

- 工具卡 `t-contigs`：contigs FASTA（文件 / 粘贴）、示例病毒按钮、最短长度、分类置信度、线程、病毒库 → 运行。
- 示例病毒按钮为 TMV / PVY / CMV / PSTVd / Mix All。
- 「📊 分类报告」内联渲染 contig 明细、宿主筛选、宿主统计、桑基图、分类表、旭日图。

### CLI 与 agent

- 管线 ③ 的同一套分类在 `python main.py analyze --r1 ... --sample S1 --stages assembly` 内自动完成，还包含组装与 BLAST 六列。
- `main.py` 没有 contigs 子命令。
- 外部 agent 走操作目录 id `contigs`（`python main.py agent-ops --id contigs --schema`，MCP `vp_run_tool`）。

## 相关

- 上游组装 → [Assembly](Assembly)
- 下游候选验证 → [Verify](Verify)
- 宿主预测（④ 管线视角）→ [Host-Prediction](Host-Prediction)
