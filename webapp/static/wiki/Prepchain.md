# Prepchain · 一键前处理

> 返回 [Home](Home) ｜ 上一页：[Preprocess](Preprocess) ｜ 下一页：[Samples](Samples)

`.sra` 与 FASTQ 自适应，把「转换 → 质控 → FASTQ→FASTA → 宿主去除 → 病毒序列提取」一次串完，各步产物收拢到一个汇总目录。
对应引擎 `Virus_Platform_Core/web/chains.py`（`_tool_job_prepchain` / `_prepchain_run_sample`）；工具卡 `t-prepchain`（工具箱「样本处理」组）；操作目录 id `prepchain`。

## 输入

### 输入格式

- 单文件 `.sra` 或 FASTQ（可带 R2）；`.sra` 输入不接受 R2，单双端由 sracha 解码决定。
- 输入目录批量：按 `_R1` / `_R1_001` / `_1` 配对单双端，孤儿 R2 跳过；逐样本执行，单样本失败不影响其余。
- 目录模式先「🔍 解析样本」预览，可勾选要跑的样本；顶层没有样本文件时自动下探一层子目录；`.sra` 各自成一个样本。
- FASTA 不能直接进本链：kept reads 必须从 FASTQ 提取。

### 参数

| 参数 | 默认 | 说明 |
|---|---|---|
| 输入文件 / `input` | 必填 | 单样本 R1，或 `.sra` |
| R2 / `input2` | — | 双端 R2；`.sra` 输入忽略 |
| 输入目录 / `input_dir` | — | 目录批量 |
| `samples` | — | 目录模式下勾选的样本 |
| 分类置信度 / `confidence` | 0 | 病毒序列提取的 kunpeng `-T` |
| `--dedup` | 关 | fastp 去 PCR 重复 |
| `db` | 平台主宿主库 | 宿主库目录 |
| `do_identify` | 开 | 置 `false` 关掉第⑤步病毒序列提取 |

- 线程数与输出目录由工具卡与接口参数给出，两者共用上表口径。

## 流程

文件级子链，把「样本处理」组各卡一次串起来：`.sra` 多一步解码，FASTQ 直接进质控。

```text
.sra ：SRA→FASTQ → 质控 → FASTQ→FASTA → 宿主去除 → 病毒序列提取（样本分析）
FASTQ：           质控 → FASTQ→FASTA → 宿主去除 → 病毒序列提取（样本分析）
```

| 步骤 | 运行键 | 输入 | 关键产物 |
|---|---|---|---|
| SRA→FASTQ | `sra2fq` | `.sra` | `_1` / `_2` FASTQ（单端无下划线） |
| fastp 质控 | `fastp` | 上一步 FASTQ | `00_prep/fastp_R1/R2.fastq.gz` |
| FASTQ→FASTA | `fq2fa` / `fq2fa_r2` | 质控后 R1/R2 | 各步 run 目录里的 `*.fa.gz` |
| 宿主去除 | `host` | 质控后 FASTQ + 预转换 FASTA | `01_host_removal/kept_R1/R2.fastq.gz` |
| 病毒序列提取 | `identify` | kept 非宿主 reads（宿主步骤跳过时退回质控后 FASTQ） | `viral_sequences*.fasta`、`viral_ids.tsv` |

### 步骤一 SRA→FASTQ（`sra2fq`）

仅 `.sra` 输入执行：sracha 解出 `_1` / `_2` FASTQ，单端产物无下划线后缀。FASTQ 输入跳过本步。

### 步骤二 质控（`fastp`）

fastp 去接头、低质量裁剪，产物 `00_prep/fastp_R1/R2.fastq.gz`；`--dedup` 可选去 PCR 重复。fastp 未安装时整步跳过。

### 步骤三 FASTQ→FASTA（`fq2fa`）

按 R1 / R2 分别转换出 `*.fa.gz`，只作 kunpeng 分类的加速输入，不是最终交付物。

### 步骤四 宿主去除（`host`）

用宿主库给质控后 reads 分类，剔除判为宿主的 read 对，保留非宿主 reads 到 `01_host_removal/kept_R1/R2.fastq.gz`；宿主库未就绪时跳过。

### 步骤五 病毒序列提取（`identify`）

用病毒库给 kept reads 分类，命中序列写入 `viral_sequences*.fasta` 与 `viral_ids.tsv`。病毒库未就绪时跳过；`do_identify=false` 可整体关掉。

### 降级与就绪检查

- **降级不报错**：fastp 未安装跳过质控；宿主库未就绪跳过宿主去除；病毒库未就绪跳过病毒序列提取——其余步骤照常，日志逐条写明哪步被跳过、为什么。
- 步骤就绪检查在任务开始时一次做完：`kunpeng.db_ready(host_db)`、`preprocess.fastp_available()`、`ctx.db_virus`；不可用就写进 `_chain.json` 的 `params` 与日志，不会跑到一半才失败。
- 每步仍各建自己的 `tool_runs/<步骤>_<时间戳>_<id>/` 运行目录；汇总目录只放链接，单样本为 `step_<步骤>`，批量为 `step_<样本>__<步骤>`。
- 病毒序列提取的 FASTA 可直喂「病毒识别和分类分析」组 ⚡ 一键（该链只收 FASTA）；kept reads 是 FASTQ，走样品管道或定量链。

## 产物

### 目录结构

```text
run/tool_runs/prepchain_<时间戳>_<id>/
├── step_<步骤>/              # 单样本：指向该步运行目录的链接
├── step_<样本>__<步骤>/       # 批量：按样本分组的链接
├── samples.tsv               # 批量清单
└── _chain.json               # 参数与步骤台账
```

实体文件在各步自己的 `run/tool_runs/<步骤>_<时间戳>_<id>/` 下，汇总目录不是第二份拷贝。

### 关键文件与字段

| 文件 / 目录 | 内容 |
|---|---|
| `01_host_removal/kept_R1/R2.fastq.gz` | 宿主去除后的非宿主 reads，供组装/定量 |
| `viral_sequences*.fasta` | 病毒库命中的 reads 序列，双端为 `_1` / `_2` 两份 |
| `viral_ids.tsv` | 命中序列的 taxid / score 清单 |
| `*.fa.gz` | 格式转换产物，只作 kunpeng 分类加速输入 |
| `samples.tsv` | 批量清单 |
| `_chain.json` | 参数与步骤台账 |

- `samples.tsv` 列为 `sample` / `type` / `status` / `bytes` / `R1` / `R2` / `kept_fastq` / `fasta` / `error`。
- `_chain.json` 记 `chain` / `mode` / `input` / `input_dir` / `n_samples` / `host_db_ready` / `fastp_available` / `do_identify` / `identify_db_ready` / `params` / `steps` / `samples`。
- 汇总目录里是符号链接，Windows 无权限时退化硬链接，仍不可用则跳过并记日志。
- 单样本失败直接抛错；批量逐样本 `try`，失败记 `status=failed` 与 `error`，全部失败才让整条任务失败。

## 示例输入与输出

### 示例输入

目录批量（单双端按 `_R1` / `_R1_001` / `_1` 命名配对；`.sra` 各自成一个样本）：

```text
prep_demo/
├── sampleA_R1.fastq.gz
├── sampleA_R2.fastq.gz
├── sampleB_R1_001.fastq.gz
└── sampleB_R2_001.fastq.gz
```

页面选「输入目录」→「🔍 解析样本」，预览里得到 `sampleA` / `sampleB` 两个样本，勾选后运行。

### 示例输出

- 汇总目录下每样本每步一个 `step_<样本>__<步骤>` 链接，目标是对应步骤的 `run/tool_runs/<步骤>_<时间戳>_<id>/`。
- `samples.tsv` 每个跑过的样本一行；五步全部走完的样本 `status=done`，被跳过的步骤对应 `kept_fastq` / `fasta` 列为空。
- `_chain.json` 的 `host_db_ready` / `fastp_available` / `identify_db_ready` 三项记录本次哪几步具备运行条件。

## 界面与 CLI

### 界面

- 工具卡 `t-prepchain`：单样本 / 目录批量两页签；目录模式「解析样本」后勾选；输出区显示各步汇总与链接。

### CLI 与 agent

- `main.py` 没有 prepchain 子命令；外部 agent 走操作目录 id `prepchain`（`python main.py agent-ops --id prepchain --schema`，MCP `vp_run_tool`）。
- 等价的手工分步：`python main.py analyze --r1 ... --r2 ... --sample S1 --stages fastp,fq2fa,host`（不含病毒序列提取，后者用 `t-identify`）。

## 相关

- 各步骤的引擎细节 → [Fastp](Fastp) · [Convert](Convert) · [Host-Removal](Host-Removal) · [Identify](Identify)
- 宿主库构建与切换 → [Databases](Databases)
- 产物去向：定量 → [Kvchain](Kvchain)；组装 → [Virchain](Virchain)
