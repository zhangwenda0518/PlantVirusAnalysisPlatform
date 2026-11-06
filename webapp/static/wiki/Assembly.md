# Assembly · 病毒组装

> 返回 [Home](Home) ｜ 上一页：[Virchain](Virchain) ｜ 下一页：[Contigs](Contigs)

把去宿主 reads 或病毒 reads 做 de novo 组装，长度过滤后产出 `contigs.filtered.fasta`。
该文件供病毒鉴定、下游注释与建树使用。

对应引擎：`Virus_Platform_Core/assembly.py`（`choose_assembler` / `run_spades` / `run_megahit` / `filter_contigs`）；工具卡 `t-assemble`（工具箱「病毒识别和分类分析」组）；操作目录 id `assemble`。

---

## 输入

### 输入格式

- 管线阶段三种输入，按优先级：病毒 reads（默认）/ 去宿主 reads / 原始 reads。
- 病毒 reads 是 ②b 比对上的 reads，宿主与杂菌污染最少、最聚焦。
- 病毒 reads 不足 500 对时，自动回退去宿主 reads 或原始 reads，并写日志。
- 独立卡 `t-assemble` 直接给 R1 / R2（或单端 R1），不走这层回退逻辑。
- FASTA 输入时 SPAdes 自动加 `--only-assembler`，跳过纠错只做组装。
- [Virchain](Virchain) 的 FASTA 输入即走 `--only-assembler` 路径。

### 参数

| 参数 | 默认 | 说明 |
|---|---|---|
| R1 / R2 | R1 必填 | 双端给 R1+R2；`srna` 或 MEGAHIT 可单端 |
| 组装引擎 | `auto` | `auto` / `spades` / `megahit` |
| 组装模式 | `rnaviral` | SPAdes 模式；MEGAHIT 用独立模式键 |
| 内存 (GB) | 物理内存一半 | 上限 60% 物理内存 |
| 最短 contig (bp) | 200 | 过滤阈值；小 RNA 可降到 ≤100 |
| 线程 | 自动 | 传给组装器 |

## 流程

### 步骤一：选择组装引擎

- `auto`（默认）：有 MEGAHIT 就用，快一个量级。
- `spades`：低深度更灵敏；`megahit`：最快。
- `srna` 小 RNA 固定用 SPAdes。
- SPAdes 模式：`rnaviral`（默认）/ `metaviral` / `meta` / `rna` / `isolate`，工具卡下拉 + `--assembly-mode`。
- `srna` 模式：`--only-assembler` + 奇数 k 阶梯 + 单端 `-s`，支持只给 R1。
- MEGAHIT 模式仅在引擎为 MEGAHIT 时生效：
  - `default`：官方 k 阶梯 21→141；
  - `sensitive`：`--presets meta-sensitive`，min-count 1，低深度更敏感；
  - `large`：`--presets meta-large`，k 27→127，适合复杂宏基因组。
- SPAdes 的 `rnaviral` 等模式名对 MEGAHIT 不适用。

### 步骤二：自动降级与资源控制

- SPAdes `metaviral` 低深度拼不出 contigs → 自动改 `rna` 模式重试。
- 单端数据 `metaviral` / `meta` → 自动转 `rna`，宏基因组模式不用单端 reads。
- 超短 reads（最长 <32 bp，小 RNA / miRNA 典型）→ 自动切 `srna`。
- 内存上限 `--memory`：管线 CLI 默认 64GB。
- 工具卡默认物理内存的一半，上限物理内存的 60%，超出自动钳制；MEGAHIT 同样按 60% 钳制。

### 步骤三：长度过滤与断点复用

- `filter_contigs` 按最短 contig 过滤，写 `contigs.filtered.fasta`。
- 过滤后 0 条直接报错，并给出提示。
- 超短 reads 建议调低最短长度、确认 `srna`；否则检查数据质量 / 深度。
- 断点复用：输入指纹与 `assembly_mode` / `min_contig_len` / `assembly_input` 一致时复用已有结果。
- 换输入或改参数会重跑。
- **中文路径兼容**：SPAdes 经 `%TEMP%\vp_spades`（纯 ASCII）中转，完成后拷回；MEGAHIT 同样经 ASCII 临时目录中转。

### 配套：管线 ③c 共识序列与变异

- reads 回贴参考，逐参考位置计数（ACGT / 覆盖度），生成共识序列与变异位点表。
- 管线 ③c 的参考固定取 `03_assembly/viral_contigs.fasta`。
- reads 固定取 `01_host_removal/`（去宿主后全量）。
- 由 `consensus.py` 的 `consensus_and_variants` 生成，供进化分析（⑦）与提交准备使用。
- 工具卡视角（换参考、换 reads、调阈值重跑）见 [Consensus](Consensus) / [Variant](Variant)。

### 配套：contig 深度注释

- `contig_annot.py` 输出 NCBI Taxonomy 8 级谱系（realm→kingdom→…→species）与覆盖度 / 深度汇总。
- 属于工具箱「contig 分类与深度分析」四件套之一。
- 本地版由 `local_search.py` 提供同一套命中结构（BLASTN / DIAMOND BLASTX），前端无需区分数据来源。

### 与管线 ③ 的分工

- 管线 `python main.py analyze --stages assembly` 在 `run/results/<样品>/03_assembly/` 下继续做四件事。
- 四件事为 contig 分类、BLAST 最近参考、`viral_contigs.fasta` 提取与谱系表。
- 这些分类侧产物见 [Contigs](Contigs)。
- 工具卡 `t-assemble` 只负责「组装 + 长度过滤」，产物是 `contigs.filtered.fasta`。
- 要接着鉴定，把该文件送 `t-contigs` 或 [Virchain](Virchain)。

## 产物

### 目录结构

```text
run/tool_runs/assemble_<时间戳>_<id>/
├── assembly/
│   ├── spades/                 # SPAdes 产物（contigs.fasta / transcripts*.fasta 等）
│   └── megahit/final.contigs.fa # 或 MEGAHIT 产物
├── contigs.filtered.fasta      # 长度过滤后的 contigs（主交付）
└── run.log
```

### 关键文件与字段

| 文件 / 目录 | 内容 |
|---|---|
| `contigs.filtered.fasta` | 长度过滤后的 contigs，主交付 |
| `assembly/spades/` | SPAdes 原始产物，文件名随模式变化 |
| `assembly/megahit/final.contigs.fa` | MEGAHIT 产物 |
| `run.log` | 运行日志 |
| 管线 `03_assembly/` | 分类侧产物（`viral_contigs.fasta` 等），见 [Contigs](Contigs) |
| 管线 `03c_consensus/` | 共识 FASTA 与变异表 |

- SPAdes 产物名不固定：常规 / rnaviral / metaviral / meta 为 `contigs.fasta`。
- 低覆盖降级 `rna` 模式为 `transcripts*.fasta` / `hard_filtered_transcripts.fasta`。
- 代码按实际返回路径过滤，不硬编码文件名。

## 示例输入与输出

### 示例输入

- 去宿主双端 FASTQ（`kept_R1/R2.fastq.gz`，格式同 [Preprocess](Preprocess)）。
- 或 ②b 提取的 `viral_R1/R2.fastq.gz`（默认组装输入，不足 500 对时自动回退）。

```text
@A00123:1:FC:1:1101:1000:2000 1:N:0:ACGT
GTACGTACGTACGTACGTACGTACGTACGTACGTACGT
+
FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF
```

### 示例输出

SPAdes contigs 写在 `contigs.filtered.fasta`。
头格式 `>NODE_<编号>_length_<长度>_cov_<平均覆盖度>`，与下游 ⑦ 建树、⑨ 出图读取的头一致。

```text
>NODE_1_length_2418_cov_4.587207
TGACAATACTGTTAAGCAAGTGTATGCCGGTGACGACATGGCATTAGATGGAGTTGTGAG
TGAAAAGAAATCATTCAGGAAGTTGCAAAATCTACTAAAACTCACTTCAAAAACTCTGTA
CCCAAAACAGGTTAAAGGGGATTACGCTGAATTTTGCGGTTGGACATTCACCCCAGGGGG
TATAATTAAAAATCCACTTAAAATGCATGCCTCAATTATGCTGCAAGAAGCCATTGGCAA
```

- 该文件可直接送 [Contigs](Contigs) 做分类 / 提取，或作 [Virchain](Virchain) 的 ① 输入。

## 界面与 CLI

### 界面

- 工具卡 `t-assemble`：R1/R2、组装引擎、组装模式（MEGAHIT 模式另选）、内存、最短 contig、线程 → 运行。
- 输出区列 `contigs.filtered.fasta` 与组装目录。

### CLI 与 agent

- 管线阶段命令如下，同时包含 contig 分类与 `viral_contigs.fasta` 提取，见 [Contigs](Contigs)。

```bat
python main.py analyze --r1 ... --sample S1 --stages assembly ^
       --assembly-mode rnaviral --memory 64 --min-contig-len 500
```

- 管线默认 `--min-contig-len 200`；上例的 500 是更严格的常用取值。
- `main.py` 没有 assemble 子命令。
- 外部 agent 走操作目录 id `assemble`（`python main.py agent-ops --id assemble --schema`，MCP `vp_run_tool`）。

## 相关

- 病毒 contigs 的去向 → [Contigs](Contigs)（鉴定）· [Host-Prediction](Host-Prediction)（④）
- 注释与建树 → [ORF](ORF)（⑥/⑥b）· [Phylo](Phylo)（⑦）
- 出图与引物 → [Genome-Plots](Genome-Plots)（⑨）· [Primer](Primer)（⑧）
- 溯源 → [Logan](Logan)
- 候选序列验证（③b）→ [Verify](Verify)
- 共识 / 变异工具卡 → [Consensus](Consensus) · [Variant](Variant)
- 一键组装 → 鉴定 → 验证 → [Virchain](Virchain)
