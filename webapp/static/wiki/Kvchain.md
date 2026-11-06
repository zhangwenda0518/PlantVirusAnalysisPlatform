# Kvchain · 一键分析（全流程）

> 返回 [Home](Home) ｜ 上一页：[Kvsuite-Intro](Kvsuite-Intro) ｜ 下一页：[Kvsuite](Kvsuite)

鉴定 → 过滤 → 共识 → 深度绘图 → 变异注释五段一次跑完，产物收拢到汇总目录。
对应引擎 `Virus_Platform_Core/web/chains.py`（`_tool_job_kvchain`，内部复用 `_tool_job_kvsuite` 并把 `stage` 固定为 `all`）；工具卡 `t-kvchain`（工具箱「病毒定量与共识」组）；操作目录 id `kvchain`。

## 输入

### 输入格式

- 勾选样品、直接输入 FASTQ、样本表 TSV，或输入目录批量。
- 目录里的 `.sra` 不接受，先转换；批量逐样本建 `<样本>/` 子目录与 `input.tsv`，单样本失败不影响其余。
- `reads` 支持目录或文件，R1/R2 分开给也会自动配对；样品名取 R1 文件名去 `_R1` 与扩展名。

### 参数

| 参数 | 说明 |
|---|---|
| `samples` / `reads` / `sample_sheet` | 三选一；`reads` 支持目录或文件、R1/R2 分开给也会自动配对；样品名取 R1 文件名去 `_R1` 与扩展名 |
| `input_dir` | 目录批量（与上面三项互斥）；只收 FASTQ，`.sra` 会 400 并提示先转换 |
| `kv_lib` | 鉴定库（选库优先；库与引擎正交） |
| `engine` | `salmon`（EM 定量，默认）/ `minibwa`（真比对计数） |
| `min_cov` / `min_depth` / `min_reads` / `min_poisson` | 识别/过滤阈值：默认 10% / 0.5 / 10 / 0.3 |
| `variant_qual` / `min_freq` | 变异段：默认 3.5 / 0.05 |
| `aa_label_cutoff` / `max_aa_labels` | 变异图标签：默认 0.5 / 40 |
| `no_genes` / `no_variant_evo` / `all_variants` / `no_snpgenie` | 绘图/变异开关 |
| `reference` / `ref_info` / `index_dir` | 未选库时的零散参考与索引（选库后忽略） |

- 工具卡「⤓ 取该卡参数」把 `t-kvsuite` 的 `kv_*` 字段抄到本工具卡 `kvc_*`，省去重填。
- 提交时先做互斥校验：目录批量与 `reads` / `sample_sheet` 只能选一种；单样本至少给样品/测序数据/样本表之一。

## 流程

### 五段顺序

「病毒定量与共识」组的整链入口：等同把 kvsuite 卡 stage 固定为 `all`，五段一次跑完。

```text
鉴定（identify）→ 过滤（filter）→ 共识（consensus）→ 深度绘图（plot）→ 变异注释（variant）
```

### 与逐卡跑的分工

- 只回答「有哪些已知病毒、各多少」→ 用 kvsuite 卡的「已知病毒识别与定量」（默认 identify + filter 两段，最省时）。
- 要换参考/换 reads/调阈值重跑共识或变异 → 用「共识序列分析」「病毒变异分析」单卡，参数可控、可补跑。
- 想一次全出、不做中间调整 → 用本链。
- 管道 `python main.py analyze --stages kvsuite` 对应的是 ②b **两段**口径（识别 + 过滤），不是本链的 `stage=all`。

### 批量与失败处理

- 批量模式每个样本一个子目录 `<chain>/<样本>/`，内写 `input.tsv`（`name` / `r1` / `r2`）。
- 单样本失败记 `status=failed` + `error` 继续跑；全部失败才让整条任务失败。

## 产物

### 目录结构

```text
run/tool_runs/kvchain_<时间戳>_<id>/
├── step_kvsuite             # 指向实际 kvsuite 运行目录的链接
├── kvsuite__run.log
├── samples.tsv              # 批量清单
└── _chain.json              # 参数/步骤台账
```

实体产物仍在实际运行目录的 `kvsuite/` 下：`identify/`、`filter/`、`consensus/`、`plots/`、`align/`、`bam/`、`vcf/`、`annotated/`、`variant_plots/`、`variant_evo/`、`variant_summary.json`。

批量模式另在每个 `<样本>/` 子目录里把 `filter/filtered.tsv`、`identify/all_viruses.summary.tsv`、`variant_summary.json` 链成 `filtered.tsv` / `identify_summary.tsv` / `variant_summary.json`。

### 关键文件与字段

| 文件 | 内容 |
|---|---|
| `_chain.json` | `chain` / `mode` / `input_dir` / `n_samples` / `params` / `steps` / `samples` / `ok_samples` / `failed_samples` |
| `samples.tsv` | 列 `sample` / `type` / `status` / `bytes` / `confirmed` / `R1` / `R2` / `files` / `error` |
| `<样本>/input.tsv` | 该样本的 `name` / `r1` / `r2` |

- 单样本返回里额外有 `kvsuite_run`。

### 五段产物对照

| 段 | 主要产物（实际 kvsuite 运行目录下） |
|---|---|
| identify | `identify/all_viruses.summary.tsv`、`identify/all_viruses.best.summary.tsv`、`align/*.sorted.bam`、pandepth 深度 |
| filter | `filter/filtered.tsv`、`filter/discarded.tsv` |
| consensus | `consensus/consensus.fa`、逐参考 `*.poscounts.tsv` / `*.inscounts.json`、`coverage.tsv`、`variants.tsv` |
| plot | `plots/<样本>/<样本>_<参考>_depth.{png,svg,pdf}` |
| variant | `vcf/`、`annotated/`、`variant_plots/`、`variant_evo/`、`variant_summary.json` |

## 示例输入与输出

### 示例输入

样本表 TSV（`name` / `r1` / `r2` 三列，单端 `r2` 留空）：

```tsv
name	r1	r2
S1	S1_R1.fastq.gz	S1_R2.fastq.gz
```

### 示例输出

- 汇总目录：`step_kvsuite` 链接、`kvsuite__run.log`、`_chain.json`；批量另有 `samples.tsv`。
- 链接目标目录下按五段分目录给出鉴定表、过滤表、共识 FASTA、深度图与变异产物（见「五段产物对照」）。

## 界面与 CLI

### 界面

- 工具卡 `t-kvchain`：单样本 / 目录批量、样品或测序数据、鉴定库与引擎、阈值、变异/绘图开关 → ⚡ 一键运行；输出区给各步汇总与汇总目录链接。

### CLI 与 agent

- `main.py` 没有 kvchain 子命令；外部 agent 走操作目录 id `kvchain`（`python main.py agent-ops --id kvchain --schema`，MCP `vp_run_tool`）。
- 只跑识别 + 过滤：`python main.py analyze --r1 ... --r2 ... --sample S1 --stages kvsuite`。

## 相关

- 单卡详解 → [Kvsuite](Kvsuite) · [Consensus](Consensus) · [Variant](Variant)
- 上游样本处理 → [Prepchain](Prepchain)
- 下游组装/鉴定 → [Virchain](Virchain)
