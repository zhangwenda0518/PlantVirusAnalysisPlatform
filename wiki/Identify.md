# Identify · 病毒序列提取（样本分析）

> 返回 [Home](Home) ｜ 上一页：[Host-Removal](Host-Removal) ｜ 下一页：[Kvsuite-Intro](Kvsuite-Intro)

用病毒参考库给 reads / contigs 分类，把判为病毒的记录提取成 FASTA，用于含病毒样本快筛与组装备选输入。
对应引擎 `Virus_Platform_Core/web/tool_jobs.py`（`_tool_job_identify`）+ `kunpeng.py`（`classify` / `parse_classify_output`）；工具卡 `t-identify`（工具箱「样本处理」组）；操作目录 id `identify`。

## 输入

### 输入格式

- 三选一：双端 FASTQ（R1 + R2）、单端 FASTQ、FASTA（contigs / 序列）。
- 目录批量按 `_R1` / `_R1_001` / `_1` 自动配对单双端，逐样本提交；FASTQ / `.sra` 之外的样本会跳过（页面提示）。

### 参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `input` | 必填 | 双端给 R1；单端给唯一 FASTQ；FASTA 模式给 contigs FASTA |
| `input_type` | `pe` | `pe` / `single` / `fasta`；`pe` 时 `input2` 必填 |
| `input2` | — | R2 FASTQ（仅 `pe`） |
| `confidence` | 0 | kunpeng `-T`，分值 < 阈值的 read 不判 C |
| `db_virus` | 平台主病毒库 | 内置键 `virus` / `refvirus` 或库目录路径；换库只影响分类与提取 |
| `threads` | 自动 | 分类线程 |

- 工具卡上另有「最小命中组数」（默认 2）；`_tool_job_identify` 未把它透传给 kunpeng，实际使用 kunpeng 默认 `-g 2`。

## 流程

### 与宿主去除的分工

同一引擎（kunpeng classify）对 reads 的一对互补集合运算：

| | 宿主去除 | 病毒序列提取（本工具卡） |
|---|---|---|
| 运算 | 样本 − 宿主库 | 样本 ∩ 病毒库 |
| 保留 | `kept_*.fastq.gz`（非宿主 reads） | `viral_sequences*.fasta`（病毒候选序列） |
| 用途 | 组装/定量的干净背景 | 含病毒样本快筛；组装备选输入 |

### 步骤一 分类

1. kunpeng classify 读入 `inputs`（PE 传两文件并加 `-P`），产物落 `<run>/classify/output_1.txt` 与 `output_1.kreport2`。
2. 只取 C 行（已分类）；按 kraken2 口径算分值：支持该 taxid 的 k-mer 片段数 / 映射列总片段数（0~1），写入 `viral_ids.tsv` 的 `score`。

### 步骤二 提取病毒记录

- 逐输入文件按 read ID 提取原始记录：单端/FASTA 输出 `viral_sequences.fasta`；双端分别输出 `viral_sequences_1.fasta` / `viral_sequences_2.fasta`（不合并）。
- 一条都没命中时**不产 FASTA**（kreport 仍保留供判读）；任务摘要仍返回 `n_classified` / `n_extracted` / `kreport`。

## 产物

### 目录结构

```text
run/tool_runs/identify_<时间戳>_<id>/
├── viral_sequences.fasta            # 单端 / FASTA 输入
├── viral_sequences_1.fasta          # 双端 R1
├── viral_sequences_2.fasta          # 双端 R2
├── viral_ids.tsv
└── classify/
    ├── output_1.txt
    └── output_1.kreport2
```

批量模式为每个样本各建一个运行目录。

### 关键文件与字段

| 文件 | 内容 |
|---|---|
| `viral_sequences*.fasta` | 病毒库命中的 reads 序列；双端 `_1` / `_2` 各一份，不合并 |
| `viral_ids.tsv` | 列 `seq_id` / `taxid` / `score` |
| `classify/` | kreport 分类报告 |

### 解读

- 只按病毒库判读，**不要求宿主库**；一条都没命中时不产 FASTA（kreport 仍保留供判读）。
- `score` 是分类可靠性参考（≥0.9 极可靠 · 0.5~0.9 高可信 · 0.1~0.5 需复核），不是丰度；丰度请走 [Kvsuite](Kvsuite)。
- 产物 FASTA 可直喂 [Virchain](Virchain)（只收 FASTA）或 [Assembly](Assembly)；不能直接做宿主去除（kept reads 必须从 FASTQ 提取）。

## 示例输入与输出

### 示例输入

单端 FASTQ，或双端 R1 + R2，或 contigs FASTA。单条 read 形如：

```text
@A00123:1:FC:1:1101:1000:2000 1:N:0:ACGT
GTACGTACGTACGTACGTACGTACGTACGTACGTACGT
+
FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF
```

### 示例输出

- 单端 / FASTA 输入：`viral_sequences.fasta` + `viral_ids.tsv` + `classify/output_1.kreport2`。
- 双端输入：`viral_sequences_1.fasta` / `viral_sequences_2.fasta` 两份，同样附 `viral_ids.tsv`。
- 无命中时只有 `classify/` 下的 kreport 与摘要，不产 FASTA。

## 界面与 CLI

### 界面

- 工具卡 `t-identify`：输入类型三选一、单样本/目录批量、分类置信度、最小命中组数、线程、病毒库下拉；「📊 分类报告」按最近一次 identify 运行内联渲染分类表 / 桑基图 / 旭日图。
- 换库说明：分类与病毒序列提取使用所选库；BLAST 注释与属长缓存仍基于主病毒参考库。

### CLI 与 agent

- `main.py` 没有 identify 子命令；外部 agent 走操作目录 id `identify`（`python main.py agent-ops --id identify --schema`，MCP `vp_run_tool`）。

## 相关

- 互补的宿主去除 → [Host-Removal](Host-Removal)
- 定量与共识 → [Kvsuite](Kvsuite) · [Kvchain](Kvchain)
- 提取的 FASTA 做组装与鉴定 → [Virchain](Virchain)
