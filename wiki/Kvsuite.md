# Kvsuite · 已知病毒识别与定量

> 返回 [Home](Home) ｜ 上一页：[Kvchain](Kvchain) ｜ 下一页：[Consensus](Consensus)

把去宿主 reads 比对到**已知病毒参考索引**，回答「样品里有哪些已知病毒、各有多少 reads」。
本工具卡默认只跑 **identify + filter 两段**（识别/定量 + 二次过滤）；共识、深度绘图、变异注释归下游模块（[Consensus](Consensus) / [Variant](Variant)）或 [Kvchain](Kvchain) 全流程。
对应引擎 `kv_stage.py` + `Virus_Platform_Core/known_virus_suite/`（自包含引擎包）；工具卡 `t-kvsuite`（工具箱「病毒定量与共识」组）；管线阶段 ②b；操作目录 id `kvsuite`。

## 输入

### 输入格式

- 去宿主 reads：`01_host_removal/kept_R1/R2.fastq.gz`；单端只给 R1。
- 鉴定库：`databases/virusref_db/plant_ref/`，自包含参考库——`reference.fasta` + `reference.ref_info.tsv` + 引擎索引。
- 参考库更新：替换 4 件套（两个 FASTA + 两个 Info.tsv + `DATA_VERSION`）后平台自动重建元数据缓存与 BLAST 库（`dev_tools/build_virus_db.py` 可重建）。

### 参数

| 参数 | 默认 | 说明 |
|---|---|---|
| 鉴定库 `kv_lib` | `databases/virusref_db/plant_ref/` | 选库优先；库与引擎正交 |
| `engine` | `salmon` | `salmon`（EM 定量）/ `minibwa`（真比对计数） |
| 识别/过滤阈值 `kv_*` | 10% / 0.5 / 10 / 0.3 | `min_cov` / `min_depth` / `min_reads` / `min_poisson` |

## 流程

### 定量引擎

| 引擎 | 口径 | 说明 |
|---|---|---|
| **salmon**（默认） | EM 定量 | 对已知病毒参考做选择比对 + EM 丰度估计；共识段内部固定 minibwa |
| minibwa | 真比对计数 | 逐 read 计数口径 |

### 步骤一 识别

reads 对 `databases/virusref_db/plant_ref/` 做比对/映射，产出逐参考鉴定表。

### 步骤二 二次过滤定量

对鉴定结果过滤（低支持度参考剔除），产出定量表与 discarded 表。

### 步骤三 比对与深度

对保留的病毒参考产出比对 BAM + pandepth 深度。

### 步骤四 提取病毒 reads

比对上的 reads 存为 `viral_R1/R2.fastq.gz`，供 ③ 组装（默认组装输入）。

## 产物

### 目录结构

```text
02b_kvsuite/
├── identify/all_viruses.summary.tsv   逐参考鉴定：物种/taxid/Uniq_Reads/覆盖度/深度
├── filter/filtered.tsv                二次过滤后定量表
├── filter/discarded.tsv               过滤剔除明细
├── align/*.sorted.bam(+.bai)          比对 BAM 与索引
├── align/pandepth…                    逐参考深度
├── viral_R1/R2.fastq.gz               比对上的病毒 reads（供 ③ 组装）
└── summary.json  kvsuite.log          汇总与日志
```

- 目录名口径：`02b_kvsuite/` 是**管线阶段**目录；工具卡 `t-kvsuite` 的实体产物在同布局的 `run/tool_runs/kvsuite_<时间戳>_<id>/kvsuite/` 下。

### 关键文件与字段

| 文件 | 内容 |
|---|---|
| `identify/all_viruses.summary.tsv` | 逐参考鉴定：物种 / taxid / Uniq_Reads / 覆盖度 / 深度 |
| `filter/filtered.tsv` | 二次过滤后的定量表 |
| `filter/discarded.tsv` | 过滤剔除明细 |
| `align/` | 比对 BAM、索引与 pandepth 深度 |
| `viral_R1/R2.fastq.gz` | 比对上的病毒 reads，供 ③ 组装 |
| `summary.json` / `kvsuite.log` | 汇总与日志 |

- 鉴定表是 ⑦ 建树分组、⑩ 报告分类树的共同数据源。

### 分类树来源

报告的科属种层级来自 ②b 鉴定表（逐参考 Uniq_Reads 按种/属/科累计）+ 参考库 `final.cluster.ref_info.tsv` 的 `VMR_Family` / `VMR_Genus`。**不加载全量 NCBI taxonomy**：避免内存压力，未收录 VMR 谱系的参考只画到种。

## 示例输入与输出

### 示例输入

① 去宿主后的双端 FASTQ；② 平台已建好的病毒参考库目录。

```text
@A00123:1:FC:1:1101:1000:2000 1:N:0:ACGT
GTACGTACGTACGTACGTACGTACGTACGTACGTACGT
+
FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF
```

```text
databases/virusref_db/plant_ref/
├── reference.fasta            # 病毒参考核酸序列
├── reference.ref_info.tsv     # 物种/科属/片段等注释
├── salmon_k31/                # salmon 索引（首次运行未建时自动建）
└── DATA_VERSION               # 库版本标记
```

### 示例输出

`identify/all_viruses.summary.tsv`（宽召回鉴定表）+ `filter/filtered.tsv`（高置信定量表），以及下方多指标对比图。

![示例输出：已知病毒平均深度/RPM/FPKM/TPM 多指标对比](assets/kvsuite-freq.png)

*图注：每行一条参考序列（按 MeanDepth 中位数排序），四个面板依次为 MeanDepth（平均深度）、RPM、FPKM、TPM，横轴为 log10 数值；同一行的点在四个面板里的相对位置可看出「测序深度高但丰度低」或「深度低但 TPM 高」等口径差异。*

## 界面与 CLI

### 界面

- 分析管道页 ②b 工具卡：参数 + `📊 查看`（鉴定表就地弹窗浏览、可下载）。
- 结果中心：已分析样品列表 + 专项结果入口。

### CLI 与 agent

```bat
python main.py analyze --r1 ... --r2 ... --sample S1 --stages kvsuite
python main.py build-virus-db --fasta databases\virusref_db\final.cluster.ref.fasta ^
       --info databases\virusref_db\final.cluster.ref_info.tsv    :: 重建病毒分类库
python main.py analyze ... --confidence 0.0                        :: 分类置信度
```

- 外部 agent 走操作目录 id `kvsuite`（`python main.py agent-ops --id kvsuite --schema`，MCP `vp_run_tool`）；全流程见 [Kvchain](Kvchain)。

## 相关

- 一键全流程（识别→过滤→共识→绘图→变异）→ [Kvchain](Kvchain)
- 共识序列重跑/补跑 → [Consensus](Consensus)
- 变异注释与出图 → [Variant](Variant)
- 病毒 reads 下一步 → [Assembly](Assembly)
- 未知的、参考库里没有的病毒 → ③ 组装 + ⑥/⑥b 从头发现
