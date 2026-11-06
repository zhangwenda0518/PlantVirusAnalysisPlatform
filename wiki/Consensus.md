# Consensus · 共识序列分析

> 返回 [Home](Home) ｜ 上一页：[Kvsuite](Kvsuite) ｜ 下一页：[Variant](Variant)

把 reads 回贴到参考序列，逐位统计碱基并生成共识序列、覆盖统计与变异位点表。
对应引擎 `Virus_Platform_Core/consensus.py`（`consensus_and_variants`，minibwa 回贴 + Python 逐位判读）；工具卡 `t-consensus`（工具箱「病毒定量与共识」组）；操作目录 id `consensus`。

## 输入

### 输入格式

- 参考三选一：
  - contig 分类运行的 `viral_contigs.fasta`；
  - kvsuite 运行里勾选的参考（`kvsuite/virus-fasta/ref_<acc>/ref_<acc>.ref.fasta`，多选按 accession 合并为临时参考库）；
  - 直接给参考 FASTA。
- reads：R1/R2（或目录）。不填时自动取——kvsuite 运行的输入序列（`sample_sheet.tsv`），或 contig 运行的 `01_host_removal/`（去宿主后全量）。
- 双端按 `_R1` / `_R2` 配对回贴。

### 参数

| 参数 | 工具卡默认 | 引擎默认 | 说明 |
|---|---|---|---|
| `min_qual` | 20 | 20 | 碱基质量下限；minibwa 输出无质量值时会记 `min_qual_effective=false` |
| `min_depth` | 10 | 4 | 低于此深度的位点共识给简并符；4 即「深度 >3」 |
| `min_freq` | 0.5 | 0.5 | 碱基频率达到此值才写入共识 |
| `ambig` | `N` | `N` | 低覆盖/不确定位的简并符号 |
| `min_mapq` | 10 | 10 | 比对质量下限，低于丢弃（防多重比对污染） |
| `min_minor_freq` | 0.02 | 0.02 | 次等位频率 ≥ 此值记 iSNV |
| —（内部） | — | `min_minor_count=2` | iSNV 至少 2 条 reads，避免单条错误被当准种 |
| `min_cov_pct` | 10 | 10 | 覆盖度达到此百分比才判「真实存在」 |

## 流程

### 步骤一 回贴参考

- 固定重新比对：minibwa `-x sr` 把 reads 回贴参考、逐位统计碱基。
- **不复用 kvsuite 的 BAM**——那份已按参考筛过，有比对偏好，会系统性低估变异/准种。
- 超短 reads（最长 <30 bp，如 21–24 nt 小 RNA）minibwa 比对率为 0，所有参考的存在性记 `unknown`（不是「不存在」），并在日志/摘要里 WARN。

### 步骤二 逐位判读共识

- 深度 ≥ `min_depth` 且频率 ≥ `min_freq` 才 call 共识，否则给简并符。
- 逐位独立判读，同时产出变异位点表与覆盖统计。
- 中间 `aligned.sam` 默认删除（`keep_sam=False`），不占运行目录。

### 与管线 ③c 的分工

- 同一段引擎：管线产物落 `03c_consensus/`，参考固定取 `03_assembly/viral_contigs.fasta`、reads 固定取 `01_host_removal/`。管道跑到 ③c 已自动生成。
- 单卡用于**手动重跑/补跑**（换参考、换 reads、调阈值），详见 [Assembly](Assembly) ③c。
- 引擎 = `consensus.py` 的 `consensus_and_variants`（minibwa 回贴 + Python 重写的 ViralConsensus 逐位判读，不需要 `viral_consensus` 可执行文件）。

## 产物

### 目录结构

```text
<运行目录>/consensus/
├── consensus.fa
├── <参考>.poscounts.tsv
├── <参考>.inscounts.json
├── variants.tsv
├── coverage.tsv
└── summary.json
```

### 关键文件与字段

| 文件 | 内容 |
|---|---|
| `consensus.fa` | 每条参考一条共识，与参考等长，低覆盖区为简并符 |
| `<参考>.poscounts.tsv` | 逐位碱基计数，列 `Pos` / `A` / `C` / `G` / `T` / `N` / `-` / `Total`，逐参考一份 |
| `<参考>.inscounts.json` | 逐位插入计数，逐参考一份 |
| `variants.tsv` | 变异位点（SNV / iSNV / 简并 / 低覆盖） |
| `coverage.tsv` | 覆盖统计 + 是否判定真实存在 |
| `summary.json` | 摘要 |

- `summary.json` 关键字段：`n_refs` / `total_len` / `n_mapped_reads` / `n_variants` / `n_snv` / `n_isnv` / `present` / `low_support` / `unknown` / `min_cov_pct` / `min_qual_effective` / `params` / `refs`。

### 判读

- `coverage.tsv.present`：`yes`（覆盖 ≥ `min_cov_pct`）/ `no` / `unknown`（超短 reads 无法比对，不能当阴性）。
- `variants.tsv` 的 `type` 用 `;` 分隔可多标签；SNV 与 iSNV 按 `type` 精确拆分，不把 `iSNV` 误计成 `SNV`。
- 共识是**逐位独立判读**，不是相位（phasing）；混合毒株/准种看 iSNV 表，不要从共识序列推断单倍型。

## 示例输入与输出

### 示例输入

去宿主 R1/R2，加一份参考 FASTA（或直接从 kvsuite 运行里挑参考）：

```text
run/results/<样品>/01_host_removal/
├── kept_R1.fastq.gz
└── kept_R2.fastq.gz
```

### 示例输出

- `consensus/consensus.fa`：每条参考一条共识。
- `consensus/coverage.tsv`、`variants.tsv` 与逐参考 `*.poscounts.tsv` / `*.inscounts.json`。
- `consensus/summary.json`：`n_refs` / `n_mapped_reads` / `n_variants` / `present` 等摘要字段。

## 界面与 CLI

### 界面

- 工具卡 `t-consensus`：参考来源（contig 运行 / kvsuite 参考多选 / 独立 FASTA）、reads（R1/R2）、阈值七项 → 运行；结果区给覆盖统计表、覆盖深度曲线、变异位点表。

### CLI 与 agent

- 管线 ③c：`python main.py analyze --r1 ... --sample S1 --stages consensus`（依赖 `assembly` + `host`，参考/reads 固定）。
- `main.py` 没有 consensus 子命令；外部 agent 走操作目录 id `consensus`（`python main.py agent-ops --id consensus --schema`，MCP `vp_run_tool`）。

## 相关

- 上游定量 → [Kvsuite](Kvsuite) / [Kvchain](Kvchain)
- 变异功能注释与出图 → [Variant](Variant)
- 管线 ③c 视角 → [Assembly](Assembly)
