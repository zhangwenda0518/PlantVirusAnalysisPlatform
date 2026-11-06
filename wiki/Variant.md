# Variant · 病毒变异分析

> 返回 [Home](Home) ｜ 上一页：[Consensus](Consensus) ｜ 下一页：[Virus-Intro](Virus-Intro)

BAM 或 VCF → 变异检出（可选）→ 功能注释 → 变异图与选择压力分析，回答「哪些位点变了、变成什么、可能有什么影响」。
对应引擎 `Virus_Platform_Core/known_virus_suite/kv_variant.py`（bcftools caller + SnpEff 5.4c）+ `kv_variant_plot.py` / `kv_variant_evo.py`（出图与 SNPGenie）；工具卡 `t-variant`（工具箱「病毒定量与共识」组，后端复用 `kvsuite` 工具 + `stage=variant`）；操作目录 id `kvsuite`（传 `stage=variant`）。

## 输入

### 输入格式

输入三选一：

- **与上游衔接（默认）**：留空则取最近一次 kvsuite 运行的 `kvsuite/bam/*.bam`。
- **直接给 BAM 目录**：可多样本混放，`collect_bams` 按文件名收集 `*.bam`，多 BAM 一起 `bcftools mpileup`（每个样本一列）。
- **直接给 VCF**：适合外部 caller（freebayes / lofreq / GATK）已产出的结果；此路径不跑 `bcftools`，`variant_qual` / `min_freq` / 深度阈值都不生效。

参考序列必须是变异所在参考；工具卡留空时走 `kv_stage.default_reference()`（默认鉴定库 `plant_ref/reference.fasta`）。

> 源码口径：「与上游衔接」字段要填上游运行名，前端把它拼成 `tool_runs/<run>/kvsuite/bam` 传给后端；留空时后端只在本次运行目录的 `kvsuite/bam` 找 BAM，不会自动搜索最近一次 kvsuite 运行。建议显式填写上游 run（或改走「直接给 BAM」）。

### 参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `reference` | 默认鉴定库 | 变异所在参考 FASTA |
| `run` / `bam_dir` | — | 上游运行名（前端转 bam 目录）/ 直接 BAM 目录 |
| `input_vcf` | — | 外部 VCF（优先于 `bam_dir`，跳过 caller） |
| `variant_qual` | 3.5 | 变异 QUAL 下限 |
| `min_freq` | 0.05 | 最小等位频率 |
| `aa_label_cutoff` | 0.5 | 变异图上标注氨基酸变化的 AF 下限（设 1 关闭标签） |
| `max_aa_labels` | 40 | 单张图的氨基酸标签上限（超限优先保留高 AF） |
| `all_variants` | 关 | 画全部变异；不勾只画非同义 |
| `no_variant_evo` | 关 | 勾选后跳过扩展变异分析（分子谱 / 密度 / 群体遗传） |
| `no_snpgenie` | 关 | 勾选后跳过 SNPGenie（依赖 Perl） |
| `ncbi_email` / `ncbi_api_key` | — | 下载 GenBank 注释用；API key 只走环境变量，不进日志 |

## 流程

### 步骤一 变异检出

- BAM → `bcftools mpileup + call`，动态 dp/saf 阈值；未打 FILTER 的原始变异另存 `all.variants.vcf`。
- 按参考拆 VCF，逐参考进入注释。

### 步骤二 功能注释

- **SnpEff 5.4c** 功能注释（需 Java 21+，平台自带 `jre-snpeff`）。
- 注释表只保留编码区注释（`coding_only=True`）。
- 无编码基因的参考（如类病毒）建库会失败并被记入 `skipped`，属正常跳过，不是任务失败。

### 步骤三 出图与选择压力分析

- 变异图（基础四图 + 扩展图 + 跨样本分型）与 SNPGenie 进化指标。
- `virus-annotations/` 与 `virus-fasta/` 即使独立跑 variant 也会补导出，避免 SNPGenie 因缺 FASTA 被静默跳过。

### 阈值与降级

- 动态 dp/saf：优先从 `identify/all_viruses.summary.tsv` 或 `filter/filtered.tsv` 的 `MeanDepth` 逐参考分档；表不可用时回退 `samtools depth -a` 全局均值，并打 WARN。
- `no_variant_evo` / `no_snpgenie` 可分别跳过扩展分析与 SNPGenie；`all_variants` 控制是否画全部变异。

## 产物

### 目录结构

```text
<运行目录>/
├── vcf/                     all.variants.vcf 与逐参考拆分 VCF
├── annotated/<参考>.ann.tsv
├── variant_summary.json
├── virus-annotations/       .gb / .gtf
├── virus-fasta/             单参考 FASTA（SNPGenie 输入）
├── variant_plots/
└── variant_evo/<参考>.snpgenie/
```

### 关键文件与字段

| 文件 | 内容 |
|---|---|
| `vcf/` | `all.variants.vcf` 与逐参考拆分 VCF |
| `annotated/<参考>.ann.tsv` | 编码区注释结果 |
| `variant_summary.json` | 逐参考变异数/跳过原因 |
| `variant_plots/` | 基础四图、扩展图与跨样本分型图；每图 PNG + SVG + PDF |
| `variant_evo/<参考>.snpgenie/` | SNPGenie 原始产物 |
| `virus-annotations/` / `virus-fasta/` | GenBank 注释与单参考 FASTA |

- `variant_plots/` 图族：基础四图 `<参考>_variant_map` / `_variant_effect` / `_variant_af_dp` / `_variant_overview`；扩展图 `<参考>_evo_spectrum` / `_evo_landscape` / `_evo_density` / `_evo_popgen` / `_evo_snpgenie`；跨样本分型 `<参考>_snipit`（snipit 可用时）。逐参考实际产物以 `variant_plots/variant_plot_manifest.json` 为准（工具卡上「8 张变异图」是概称）。
- `variant_summary.json` 结构：`n_success` / `n_skipped` / `results[]`（accession、genome、chrom、vcf、n_variants、n_coding_ann、ann_vcf、ann_tsv）/ `skipped[]`（genome、reason）。

### 判读

判读规则见 [Result-Interpretation](Result-Interpretation)。

## 示例输入与输出

### 示例输入

上游 kvsuite 运行产出的 BAM 目录（或外部 VCF）：

```text
run/tool_runs/kvsuite_<时间戳>_<id>/kvsuite/bam/
├── <参考>.sorted.bam
└── <参考>.sorted.bam.bai
```

### 示例输出

- `vcf/all.variants.vcf` 与逐参考拆分 VCF；`annotated/<参考>.ann.tsv`。
- `variant_plots/` 下的基础四图与扩展图；`variant_evo/<参考>.snpgenie/` 原始产物。
- `variant_summary.json` 汇总 `n_success` / `n_skipped` 与逐参考变异数。

## 界面与 CLI

### 界面

- 工具卡 `t-variant`：参考序列库、输入方式三选一（上游衔接 / BAM 目录 / VCF）、阈值与绘图开关 → 运行；结果区给注释表、基础四图、扩展四图与 SNPGenie 汇总。

### CLI 与 agent

- `main.py` 没有 variant 子命令；外部 agent 走 `kvsuite` 操作目录并传 `stage=variant`（`python main.py agent-ops --id kvsuite --schema`，MCP `vp_run_tool`）。
- 全流程一次跑：[Kvchain](Kvchain) 的 `stage=all` 已包含变异段。

## 相关

- 上游共识与覆盖 → [Consensus](Consensus)
- 定量与参考来源 → [Kvsuite](Kvsuite)
- 全流程 → [Kvchain](Kvchain)
