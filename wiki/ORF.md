# ORF · ORF 预测与功能注释

> 返回 [Home](Home) ｜ 上一页：[Annochain](Annochain) ｜ 下一页：[Genome-Plots](Genome-Plots)

回答「每个 ORF 是什么蛋白、属于哪个科属、基因组什么类型」。
输入核酸 FASTA，先做基因预测，再做三层功能注释。

对应引擎：`orf.py`（⑥）、`orf_annot.py` + `hmm_annot.py` + `bfvd_search.py` + `genome_diag.py`（⑥b）；操作目录 id `orf` / `orfa`。

---

## 输入

### 输入格式

- 核酸 FASTA（contigs / 基因组）。
- 管线 ⑥ 在组装后的 contigs 上跑基因预测，产物落 `04_orf/`。
- 一键注释链 [Annochain](Annochain) 把同一条 FASTA 直接传给 ① ORF 步。

### 参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `--min-orf-aa` | `100` | 最短 ORF 氨基酸数 |
| `--libs` | `pvdb,hmm,cdd,bfvd` | 启用注释层（逗号分隔；无效/已归档层名直接报错） |
| `--threads` | 平台默认 | 搜索线程 |
| `--force` | 关 | 忽略缓存重跑 |

## 流程

### 步骤一：基因预测（⑥）

- **pyrodigal**（meta 模式）是基因预测主引擎，输出 faa / ffn / gff。
- **pyrodigal-rv** 是 RNA 病毒优化模型，处理核糖体滑移、重叠 ORF 等，产物 `pyrodigal_rv.*`。
- **orfipy** 仅在显式指定时运行（pep / nt / bed）。
- 参数 `--min-orf-aa` 默认 100 aa，产物在 `04_orf/`。

### 步骤二：层 1 · 序列同源（主策略）

- **DIAMOND（首选）→ MMseqs2 → BLAST+ blastp** 自动降级。
- 对照 **NCBI RefSeq 病毒蛋白库**（release viral 全量）。
- 首次运行自动从 NCBI 官方域名下载 ~107MB 并建库，之后全缓存。
- **E-value 加权类别投票**，依据 LanderDC/annotation_benchmark 基准。
- 基准结论：序列同源是病毒蛋白注释最准确的主策略。
- 准确率依次为 BLASTp 78.4% > 结构折叠 72.2% > 蛋白语言模型 63.6%。
- 有效类别权重须 ≥ 最佳「假想蛋白」类别的 50%。
- 命中物种经 NCBI taxonomy 谱系解析到属 / 科。
- 再查 **ICTV 科级对照表**（265 科，源自 LazypipeX）。
- 得到**宿主类别**与**基因组类型**（ssRNA(+)/dsDNA/…）。
- 该结果与 ④ 宿主预测交叉印证。

### 步骤三：层 2 · HMM / 结构域（远缘兜底）

- **pyhmmer 流式扫描 Pfam 病毒库**（`Pfam-A-Viruses.hmm`，virsorter2 病毒子集 1,074 profiles）。
- 内存恒定 ~50MB。
- 双门槛过滤：域级 i-Evalue ≤1e-3 **且** 模型覆盖率 ≥0.5。
- HMM 内嵌 NAME / ACC / DESC，直接得可读产物名。
- 序列层已注释 ORF 保留层 1 结果，HMM 命中记入 `hmm_hits` 列。
- 未命中 ORF 由 HMM 兜底（evidence=hmm）。
- 库缺失时该层整体跳过，不报错。
- 可用 `--libs pvdb,hmm,cdd,bfvd` 指定启用层（缺省=全部）。
  `prot`（原 RefSeq 序列同源层）与原 `Pfam-A-Viruses` 层已归档；
  PVDB 的 Pfam/VOG/VFAM 三库请用 `hmm`。无效或已归档层名会直接报错并列出可用层，
  不会静默回退为全部层。

### 步骤四：BFVD 病毒域层

- mmseqs2 搜索 BFVD：`bfvd_search.py`，-s 7.5，evalue<1e-3。
- BFVD 为病毒专属域库，命中即病毒证据，免白名单。
- product / Organism 取库内 `bfvd_uniprot_names.tsv`。
- 与 SUVTK 提交链 featuretable 的 product **同库同命名**。

### 步骤五：汇总与出图

- 类别 / 科分布图与 Top 注释表自动嵌入 ⑩ 报告。
- `genome_diagrams/<contig>.svg` 为 littlegenomes 风格线性示意图。
- ORF 按功能类别着色方向箭头，叠加 HMM / BFVD 结构域窄条与比例尺。
- 示意图为零依赖 SVG。

### 参考库维护

- 替换 `databases/annot/prot/viral_prot.faa` 后，删除 `organism_tax.tsv`、`db_info.json`，重跑即自动重建索引与搜索库。
- BFVD 库随包在 `databases/misc_db/suvtk/`，与 SUVTK 提交链共用。
- 可用 `platform.json` 的 `databases.bfvd` 指定其他前缀。

## 产物

### 目录结构

```text
run/results/<样品>/
├── 04_orf/                 ⑥ 基因预测：faa / ffn / gff，pyrodigal_rv.*
└── 04b_orf_annot/          ⑥b 功能注释（见下表）
    └── genome_diagrams/<contig>.svg
```

### 关键文件与字段

| 文件 | 内容 |
|---|---|
| `orf_annotation.tsv` | 逐 ORF：产物 / 物种 / identity / 覆盖度 / E-value / 科属 / 宿主 / 基因组类型 / 功能类别 / 证据 / pvdb_hits / hmm_hits / cdd_hits / bfvd_hits / segments / seg_method / cs_support |
| `orf_function_summary.tsv` / `orf_family_summary.tsv` | 功能类别与科分布汇总 |
| `contig_function_profile.tsv` | 逐 contig 功能画像 |
| `orf_annotation.gff3` | pyrodigal GFF 追加 product / organism / category / evidence 属性 |
| `genome_diagrams/<contig>.svg` | littlegenomes 风格线性示意图 |

- `genome_diagrams` 的图包含 ORF 方向箭头、结构域窄条与比例尺。
- `segments` 格式 `起始-终止:段名;…`，坐标为**蛋白序列 1 基闭区间**
  （`1-244:P1 protein;245-704:HC-Pro protein;…`）；`seg_method` 取值
  `projection`（strong 档，切点直接采用）或 `projection+motif`（mid 档，
  切点按 ±10aa 内的 Q|X motif 吸附），`cs_support` 为采用的切点数。

## 示例输入与输出

### 示例输入

一条组装 contig；⑥ 在它上面预测 ORF，⑥b 再逐 ORF 注释。

```text
>NODE_1_length_2418_cov_4.587207
TGACAATACTGTTAAGCAAGTGTATGCCGGTGACGACATGGCATTAGATGGAGTTGTGAG
TGAAAAGAAATCATTCAGGAAGTTGCAAAATCTACTAAAACTCACTTCAAAAACTCTGTA
CCCAAAACAGGTTAAAGGGGATTACGCTGAATTTTGCGGTTGGACATTCACCCCAGGGGG
TATAATTAAAAATCCACTTAAAATGCATGCCTCAATTATGCTGCAAGAAGCCATTGGCAA
```

### 示例输出

```text
run/results/<样品>/04b_orf_annot/
├── orf_annotation.tsv
├── orf_function_summary.tsv
├── orf_family_summary.tsv
├── contig_function_profile.tsv
├── orf_annotation.gff3
└── genome_diagrams/
    └── <contig>.svg
```

- `orf_annotation.tsv` 是逐 ORF 的主表，`evidence` 列标明注释层。
- `orf_annotation.gff3` 可直接送 [Genome-Plots](Genome-Plots) 出图。

## 界面与 CLI

### 界面

- 分析管道 ⑥ / ⑥b 阶段卡：`📊 查看`，注释表就地浏览。
- 结果中心可看功能示意图。

### CLI 与 agent

```bat
python main.py orfa --sample S1 [--threads 8] [--libs pvdb,hmm,bfvd] [--profile balanced] [--force]
python main.py analyze --r1 ... --sample S1 --stages orf,orfa --min-orf-aa 100
```

- 外部 agent 走操作目录 id `orf` / `orfa`（MCP `vp_run_tool`）。
- 单独出图走 [Genome-Plots](Genome-Plots)。

## 相关

- 基因组类型 / 宿主类别 ↔ ④ → [Host-Prediction](Host-Prediction)
- ORF 注释如何被用 → [Phylo](Phylo)（PEP / CDS 建树）、[Genome-Plots](Genome-Plots)（着色注释）
