# Preprocess · 数据预处理（⓪ 质控 · ⓪b 转换 · ① 宿主去除）

> 返回 [Home](Home) ｜ 上一页：[Pipeline-Overview](Pipeline-Overview) ｜ 下一页：[Kvsuite](Kvsuite)

对应引擎：`preprocess.py`（fastp）、`kunpeng.py`（封装：建库 classify）、`host_removal.py`（① 阶段编排）。

---

## ⓪ Fastp 质控（可选）

- 自动去接头 + 低质量裁剪，双端/单端皆可；
- fastp 未安装时该步**自动跳过**（SPAdes 自带 BayesHammer 纠错，质控非必需），卡片灰显「可跳过」；
- 产物：`00_prep/fastp_R1/R2.fastq.gz` + `fastp_report.html`（可在线打开）。

## ⓪b FASTQ→FASTA 转换（可选，推荐）

- seqkit fq2fa 生成 `conv_R1/R2.fa.gz`，供 ① kunpeng 分类**直接复用**（分类输入 FASTA 比 FASTQ 快）；
- CLI `--no-fq2fa` 关闭（默认开启）；
- 有 crabz 时 .gz 读写自动走多线程管道（快 ~10 倍）。

## ① 宿主序列去除

**原理**：宿主库只含宿主单一物种，因此 kunpeng classify 输出中 C 行（已分类）即宿主 read 对——直接剔除，U 行保留。

- 输入：原始/质控后 reads（自动衔接 ⓪⓪b 产物）；单端只处理 R1；
- 宿主库：`host-db/<TaxID>_<源目录名>_host_db/`（多库共存，`platform.json` 的 `active_host_db` 指定当前库，数据库页一键切换）；
- 产物（`01_host_removal/`）：`kept_R1/R2.fastq.gz`、`stats.json`（宿主占比统计）、`host.kreport2`（分类报告）。

### 宿主库构建要点（见 [Databases](Databases)）

- 需要 NCBI TaxID（建库前自动校验：不存在/已被合并直接提示，不白跑）；
- FASTA 头任意格式均可（自动清理并注入 `|kraken:taxid|N` 标签）；
- **大基因组自动切片**：每条序列切 ≤1MB 片段、相邻重叠 34bp（k-1）保留跨切口 k-mer——全量实测 k-mer 数与整条建库完全一致（286,327,790），2GB 内存即可完成 1.8GB 基因组建库（~1.5 分钟）；
- 建库为替换式：重试自动清理旧 library，不累积重复数据；
- `--clean-mid` 建库后清理中间文件（library/prep/taxonomy，省 3-4GB/库，重建时自动再生成）。

## 输入格式

- FASTQ / FASTQ.gz（`.fastq` `.fq` `.fastq.gz` `.fq.gz`），双端选 R1/R2、单端只选 R1；
- FASTA / FASTA.gz（建库输入）；
- `.zip/.rar/.tar` 需先解压；文件浏览对话框只显示数据文件类型，浏览范围限平台目录内。

## CLI

```bat
python main.py analyze --r1 R1.fastq.gz --r2 R2.fastq.gz --sample S1 ^
       --stages fastp,fq2fa,host        :: 只跑预处理三步
python main.py build-host-db --genome host-db\genome.fa --taxid 4081 ^
       [--threads 8] [--out-dir ...] [--clean-mid] [--rebuild]
```

## 相关

- 宿主库详情与 TaxID 校验 → [Databases](Databases)
- 去宿主 reads 去向 → [Kvsuite](Kvsuite)（识别定量）与 [Assembly](Assembly)（组装输入三选一）
