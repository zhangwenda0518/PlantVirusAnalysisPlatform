# Convert · 格式转换

> 返回 [Home](Home) ｜ 上一页：[Fastp](Fastp) ｜ 下一页：[Host-Removal](Host-Removal)

`.sra` → FASTQ / FASTA（sracha），FASTQ → FASTA（seqkit fq2fa）；下载数据与下游分类/组装之间的格式桥。
对应引擎 `Virus_Platform_Core/web/tool_jobs.py`（`_tool_job_convert`）+ `public_data.sra_convert_engine` / `_sra_fastq_outputs`；工具卡 `t-convert`（工具箱「样本处理」组）；管线阶段 ⓪b；操作目录 id `convert`。

## 输入

### 输入格式

- `.sra` 单文件；或 FASTQ(.gz)（可带 R2）。
- `.sra` 自含双端信息，不接受 `input2`；输入已是 FASTA 会被拒绝（「输入已是 FASTA，无需转换」）。
- 文件浏览对话框只显示数据文件类型；`.zip` / `.rar` / `.tar` 需先解压。

### 参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `input` | 必填 | `.sra` 或 R1 FASTQ |
| `input2` | — | R2 FASTQ，仅 FASTQ 输入可用 |
| `target` | — | 只接受 `fastq` / `fasta`；FASTQ 输入选 `fastq` 不会做任何事 |
| 线程数 | 自动 | sracha / seqkit 线程 |
| 输出目录 | `run/tool_runs/convert_<时间戳>_<id>/` | 工具卡可改 |

## 流程

### 模式一 SRA 解码

- sracha 解码 `.sra`，单端出 `<acc>.fastq.gz`，双端出 `<acc>_1.fastq.gz` / `<acc>_2.fastq.gz`。
- 目标为 FASTA 时加 `--fasta`，后缀换成 `.fa.gz` / `.fasta.gz`。
- sracha 未就绪直接报错；`_sra_fastq_outputs` 统一判定单端/双端产物名，避免把已落盘的 GB 级 FASTQ 当成「无产物」丢掉。

### 模式二 FASTQ→FASTA

- `seqkit fq2fa -w 0 -j N`，每个输入各一份 `<原名>.fa.gz`。
- 双端 FASTQ（`input` + `input2`）**分别转换、分别输出**，不合并；文件名自带 `_R1` / `_R2` 时产物自然分开。
- 有 crabz 时 `.gz` 读写自动走多线程管道（快 ~10 倍）。

### 与管线 ⓪b 的分工

- 管线阶段 `fq2fa` 固定把 `conv_R1.fa.gz` / `conv_R2.fa.gz` 写进 `run/results/<样品>/00_prep/`，供 ① 宿主去除的 kunpeng 分类直接读（分类输入 FASTA 比 FASTQ 快）；`--no-fq2fa` 可关。
- 工具卡 `t-convert` 是独立运行目录（`run/tool_runs/convert_*`），额外承担 `.sra` 解码与按需 FASTA 输出；两者共用 seqkit / sracha 口径，产物位置不同。
- 转换出的 FASTA 只用于分类加速；宿主去除的 kept reads 仍从 FASTQ 提取。

## 产物

### 目录结构

```text
run/results/<样品>/00_prep/
├── conv_R1.fa.gz
└── conv_R2.fa.gz        # 双端才有
```

独立卡产物落 `run/tool_runs/convert_<时间戳>_<id>/`：`.sra` 解码出 `<acc>*.fastq.gz` / `<acc>*.fa.gz`；FASTQ 转换出每个输入一份 `<原名>.fa.gz`。

### 关键文件与字段

| 产物 | 来源 | 用途 |
|---|---|---|
| `conv_R1.fa.gz` / `conv_R2.fa.gz` | 管线 ⓪b `fq2fa` | ① 宿主去除的 kunpeng 分类输入 |
| `<acc>_1.fastq.gz` / `<acc>_2.fastq.gz` | `.sra` → `fastq` | 下游质控/宿主去除 |
| `<acc>*.fa.gz` / `<原名>.fa.gz` | `.sra` → `fasta` 或 `fq2fa` | 分类加速，不作最终交付 |

## 示例输入与输出

### 示例输入

双端 FASTQ（`.sra` 同样可作输入，单双端由文件自含信息决定）：

```text
@A00123:1:FC:1:1101:1000:2000 1:N:0:ACGT
GTACGTACGTACGTACGTACGTACGTACGTACGTACGT
+
FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF
```

### 示例输出

- 管线 ⓪b：`00_prep/conv_R1.fa.gz` 与 `00_prep/conv_R2.fa.gz`。
- 独立卡：`convert_<时间戳>_<id>/` 下 `.sra` 解码的 `<acc>*.fastq.gz` / `<acc>*.fa.gz`，或 FASTQ 转换的 `<原名>.fa.gz`。

## 界面与 CLI

### 界面

- 工具卡 `t-convert`：单样本 / 目录批量、输入文件、可选 R2、目标格式（FASTQ 仅 `.sra` 可用；FASTA 两者皆可）、输出目录。

### CLI 与 agent

- 管线阶段：`python main.py analyze --r1 ... --sample S1 --stages fq2fa`（写 `00_prep/conv_R1/R2.fa.gz`）。
- `main.py` 没有 convert 子命令；外部 agent 走操作目录 id `convert`（`python main.py agent-ops --id convert --schema`，MCP `vp_run_tool`）。

## 相关

- 转换前的质控 → [Fastp](Fastp)
- FASTA 的下游用途 → [Host-Removal](Host-Removal)（分类加速）· [Identify](Identify)（病毒序列提取）
- 一次串完前处理 → [Prepchain](Prepchain)
