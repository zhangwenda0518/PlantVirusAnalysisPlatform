# Fastp · 序列质控

> 返回 [Home](Home) ｜ 上一页：[Samples](Samples) ｜ 下一页：[Convert](Convert)

双端/单端 FASTQ → 接头去除、低质量裁剪、可选去重，产出 clean FASTQ 与 fastp 报告。
对应引擎 `Virus_Platform_Core/preprocess.py`（`run_fastp` / `fastp_available`）；工具卡 `t-fastp`（工具箱「样本处理」组）；管线阶段 ⓪；操作目录 id `fastp`。

## 输入

### 输入格式

- 双端 R1 + R2，或单端 R1（FASTQ / FASTQ.gz）。
- 已接入样品管线的 ⓪ 阶段；独立工具卡 `t-fastp` 默认落 `run/tool_runs/fastp_<时间戳>_<id>/`。

### 参数

| 参数 | 默认 | 说明 |
|---|---|---|
| 质量阈值 `--qualified_quality_phred` | 20（Q20） | 固定值 |
| 最短保留读长 `--length_required` | 15 bp（配置 `fastp_min_len`） | 兼容 18–30 nt 的 sRNA 测序；50 bp 会把小 RNA reads 滤光 |
| `--dedup` | 关 | 按序列去 PCR 重复；高深度宏数据慎用，病毒鉴定/组装默认关闭 |
| 双端接头检测 `--detect_adapter_for_pe` | 双端自动 | 接头检测阶段可能数分钟无日志，属正常 |
| 线程数 | 自动 | 传给 fastp |
| 输出目录 | `run/tool_runs/fastp_<时间戳>_<id>/` | 工具卡可改 |

## 流程

### 步骤一 去接头与质量裁剪

- 自动去接头 + 低质量裁剪，双端/单端皆可；质量阈值固定 Q20，最短保留读长取 `fastp_min_len`，默认 15 bp。
- 双端额外加 `-I R2 -O fastp_R2.fastq.gz --detect_adapter_for_pe`。
- `--dedup` 为可选开关：按序列去重，高深度宏数据慎用（真实重复可能被误删）。

### 步骤二 写产物与摘要

- 产物落 `00_prep/fastp_R1/R2.fastq.gz`，同目录给 HTML / JSON 报告与断点标记。
- 摘要解析出 `reads_before` / `reads_after` / `q30_rate` / `lowq_removed`，供上游工具卡与阶段视图展示。

### 断点复用

- 复用前核对输入指纹（路径/大小/mtime）与 `dedup` / `min_len`；换输入或改参数会重跑。

### 失败处理

- 质控后 `reads_after == 0` 直接报错（阈值过严或输入为空），不会把空数据放给下游；报告缺失/损坏同样报错。
- fastp 未安装时该步自动跳过（SPAdes 自带 BayesHammer 纠错，质控非必需）：管线阶段 ⓪ 直接跳过；独立工具卡 `t-fastp` 会明确抛「未检测到 fastp.exe，无法运行质控」（工具卡灰显「可跳过」）。

## 产物

### 目录结构

```text
run/results/<样品>/00_prep/
├── fastp_R1.fastq.gz
├── fastp_R2.fastq.gz        # 双端才有
├── fastp_report.html
├── fastp_report.json
├── fastp.json
└── .fastp.done
```

### 关键文件与字段

| 文件 | 内容 |
|---|---|
| `fastp_R1.fastq.gz` / `fastp_R2.fastq.gz` | 质控后的 clean reads |
| `fastp_report.html` | 可视化报告，可在线打开 |
| `fastp_report.json` | 原始报告 |
| `fastp.json` | 源输入指纹 + 生效参数 `dedup` / `min_len` |
| `.fastp.done` | 断点标记 |

## 示例输入与输出

### 示例输入

双端/单端 FASTQ，每条 read 固定四行：`@` 头、序列、`+`、逐碱基质量。

```text
@A00123:1:FC:1:1101:1000:2000 1:N:0:ACGT
GTACGTACGTACGTACGTACGTACGTACGTACGTACGT
+
FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF
```

### 示例输出

- `00_prep/fastp_R1.fastq.gz`（双端另有 `fastp_R2.fastq.gz`）。
- `fastp_report.html` / `fastp_report.json`；报告里 `after_filtering.total_reads` 即 `reads_after`。

## 界面与 CLI

### 界面

- 工具卡 `t-fastp`：单样本 / 目录批量、R1 / R2、`--dedup`、线程数、输出目录 → 运行；输出区列 FASTQ 与 HTML/JSON 报告。

### CLI 与 agent

- 管线阶段：`python main.py analyze --r1 ... --r2 ... --sample S1 --stages fastp`（只跑 ⓪；`--stages fastp,fq2fa,host` 跑预处理三步）。
- `main.py` 没有 fastp 子命令；外部 agent 走操作目录 id `fastp`（`python main.py agent-ops --id fastp --schema`，MCP `vp_run_tool`）。

## 相关

- 下一步 FASTQ→FASTA → [Convert](Convert)
- 去宿主前是否质控 → [Host-Removal](Host-Removal)
- 一次串完全部前处理 → [Prepchain](Prepchain)
