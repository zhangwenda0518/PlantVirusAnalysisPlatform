# Virchain · 一键分析

> 返回 [Home](Home) ｜ 上一页：[Virus-Intro](Virus-Intro) ｜ 下一页：[Assembly](Assembly)

对去宿主 FASTA 一次跑完「病毒组装 → 病毒鉴定 → 候选序列验证」。
三步可自由勾选，链内自动传产物。

对应引擎：`Virus_Platform_Core/web/chains.py`（`_tool_job_virchain` / `_virchain_run_sample`）；工具卡 `t-virchain`（工具箱「病毒识别和分类分析」组）；操作目录 id `virchain`。

---

## 输入

### 输入格式

- 只收去宿主 FASTA：`.fa` / `.fasta` / `.fna` / `.fas`，可带 `.gz`。
- 支持单端或双端、单文件或输入目录批量。
- 目录批量按样本建子目录，逐样本跑同一套步骤。
- FASTQ / `.sra` 直接拒绝，并提示先做宿主去除或病毒序列提取，再转 FASTA。

### 参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `steps` | `1,2,3` | 勾选步骤：`1` 组装、`2` 鉴定、`3` 验证 |
| 组装引擎 | `auto` | `auto` / `spades` / `megahit` |
| MEGAHIT 模式 | `default` | `default` / `sensitive` / `large` |
| 组装模式 | `rnaviral` | `rnaviral` / `metaviral` / `meta` / `rna` / `srna` |
| 内存上限 | — | 传给组装步 |
| 最小 contig 长度 | `200` | 长度过滤阈值 |
| 分类置信度 | `0` | kunpeng `-T` 置信度 |
| 验证宿主筛选 | `all` | `all` / 植物 / 真菌 / 细菌 / 无脊椎 / 脊椎 |
| 验证方法 | `blastx,bfvd,cdd` | `blastx,bfvd` / 仅 blastx / 仅 BFVD / 仅 CDD |
| 合并方式 | `union` | `union` / `intersection` |
| 线程 | 平台默认 | 传给各步 |

- `host` / `combine` / `methods` 只对验证步生效。
- 组装与分类的其余参数，分别沿用 [Assembly](Assembly) 与 [Contigs](Contigs) 的口径。

## 流程

步骤编号为 ① 组装、② 病毒鉴定、③ 验证。
默认全选，依赖固定，缺失前置直接拒绝提交。

### 步骤一：病毒组装

- 内部任务 `_tool_job_assemble`，输入 `r1` 与可选 `r2`（FASTA）。
- 产物 `contigs.filtered.fasta`。
- 未产出该文件时整链失败，后续步骤无输入。

### 步骤二：病毒鉴定

- 内部任务 `_tool_job_contigs`，读 ① 的 `contigs.filtered.fasta`。
- 产物 `virus_classification.tsv` 与 `viral_contigs.fasta`。
- 同一任务内自动跑宿主预测，产物落 `08_host_analysis/`。
- 未提取到病毒 contig 时，③ 记 `skipped` 并继续收尾。

### 步骤三：候选序列验证

- 内部任务 `_tool_job_verify`。
- 输入为 ② 的 `viral_contigs.fasta` 与同 run 的 `virus_classification.tsv`。
- 产物 `verify/calls.tsv` 与 `verify/summary.json`。

### 链内传参与目录

- 每步各建自己的 run 目录，实体产物留在各步目录。
- 汇总目录只放步骤目录链接、关键文件链接与清单。
- 汇总目录为 `run/tool_runs/virchain_<时间戳>_<id>/`。
- 批量时单样本失败不影响其余样本；全部失败才抛错。

## 产物

### 目录结构

```text
run/tool_runs/virchain_<时间戳>_<id>/
├── step_assembly -> ...          单样本：① 的 run 目录
├── step_contigs  -> ...          单样本：② 的 run 目录
├── step_verify   -> ...          单样本：③ 的 run 目录
├── assembly__* / contigs__* / verify__*   已链接的关键文件
├── samples.tsv                   批量样本表
├── _chain.json                   链清单
└── <样本>/                       批量：该样本的关键文件链接
```

- 批量时每步链接名为 `step_<样本>__assembly` 等。
- 单样本用 `step_assembly` / `step_contigs` / `step_verify`。

### 关键文件与字段

| 产物 | 位置 | 说明 |
|---|---|---|
| `contigs.filtered.fasta` | ① run 目录 | 长度过滤后的 contigs |
| `virus_classification.tsv` | ② run 目录 | 8 级谱系分类表 |
| `viral_contigs.fasta` | ② run 目录 | 病毒子集，③ 的输入 |
| `08_host_analysis/` | ② run 目录 | 自动宿主预测 |
| `verify/calls.tsv` | ③ run 目录 | 逐 contig 判定表 |
| `verify/summary.json` | ③ run 目录 | 验证计数与参数 |
| `samples.tsv` | 汇总目录 | 列：`sample` / `type` / `status` / `bytes` / `steps` / `R1` / `R2` / `files` / `error` |
| `_chain.json` | 汇总目录 | `chain` / `mode` / `input` / `input_dir` / `n_samples` / `params` / `steps[]` / `samples[]` |

- `params` 记 `steps` / `mode` / `assembler` / `megahit_mode` / `memory_gb` / `min_len` / `confidence` / `host` / `combine` / `methods`。
- 失败样本记 `status=failed` 与 `error`。
- 各步实体产物：① `assembly/` 与 `contigs.filtered.fasta`；② `classify/`、`virus_classification.tsv`、`viral_contigs.fasta`、`08_host_analysis/`；③ `verify/calls.tsv`、`verify/summary.json`。

## 示例输入与输出

### 示例输入

目录批量时，每个样本一对去宿主 FASTA，单端只给 R1。

```text
D:/data/virchain_in/
├── S1_R1.fasta      去宿主 R1
├── S1_R2.fasta      双端时的 R2
└── S2_R1.fasta      单端样本
```

- 单文件模式把 `S1_R1.fasta` 作 `input`，R2 作 `input2`。

### 示例输出

```text
run/tool_runs/virchain_20261105_120000_ab12cd/
├── step_assembly -> assemble_20261105_120000_ab12
├── step_contigs  -> contigs_20261105_120000_cd34
├── step_verify   -> verify_20261105_120000_ef56
├── assembly__*  ① 的关键文件链接
├── contigs__*   ② 的关键文件链接
├── verify__*    ③ 的关键文件链接
└── _chain.json
```

- 链接按实际存在的关键文件生成，缺哪件就不出现哪一条。

## 界面与 CLI

### 界面

- 工具卡 `t-virchain`：单样本 / 目录批量、去宿主 FASTA（可带 R2）。
- 同卡可选组装引擎 / 模式 / 内存 / 最小长度 / 置信度、三步勾选、验证宿主 / 方法 / 合并。
- 点「⚡ 一键运行」；输出区给各步汇总与汇总目录链接。

### CLI 与 agent

- `main.py` 没有 virchain 子命令。
- 外部 agent 走操作目录 id `virchain`（`python main.py agent-ops --id virchain --schema`，MCP `vp_run_tool`）。
- 手工分步走管线口径，不含工具卡的分类 / 提取：

```bat
python main.py analyze --r1 ... --sample S1 --stages assembly,verify
```

## 相关

- 上游 FASTA 来源 → [Host-Removal](Host-Removal) · [Identify](Identify) · [Prepchain](Prepchain)
- 单卡详解 → [Assembly](Assembly) · [Contigs](Contigs) · [Verify](Verify)
- 后续注释与建树 → [ORF](ORF) · [Phylo](Phylo)
