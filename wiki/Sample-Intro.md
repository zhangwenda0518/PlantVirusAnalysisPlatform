# Sample-Intro · 样本处理组导读

> 返回 [Home](Home) ｜ 上一页：[Databases](Databases) ｜ 下一页：[Preprocess](Preprocess)

样本处理组共 6 个模块 + 1 篇管线总览，本页做导览，模块细节见各专页。本组回答「原始测序数据怎样变成可供鉴定、定量与组装的干净 reads」：输入是 FASTQ 或 `.sra`，产物收在 `run/results/<样品>/` 下。

## 模块清单

1. [Prepchain](Prepchain) · ⚡ 一键前处理 —— `.sra` / FASTQ 自适应：转换 → 质控 → 转 FASTA → 宿主去除 → 病毒序列提取，一次跑完。
2. [Samples](Samples) · 样品创建 / 批量导入 —— 样品名 + R1/R2 手工创建，或文件夹扫描批量导入；样品是全平台的组织单位。
3. [Convert](Convert) · 格式转换 —— `.sra` → FASTQ / FASTA，FASTQ → FASTA（seqkit / sracha）。
4. [Fastp](Fastp) · 序列质控 —— fastp 去接头、质量过滤、去重（可选；SPAdes 自带纠错时质控非必需）。
5. [Host-Removal](Host-Removal) · 非宿主序列提取 —— kunpeng 宿主库分类，剔掉宿主 reads，保留 kept 非宿主集合。
6. [Identify](Identify) · 病毒序列提取 —— 与宿主去除同一引擎的互补运算：样本 ∩ 病毒库 = 病毒候选序列（`viral_sequences*.fasta`）。
7. [Preprocess](Preprocess) · 管线总览 —— ⓪ 质控 · ⓪b 转换 · ① 宿主去除作为**样品分析管线阶段**的编排视角（`.done` 断点、`--stages` 组合），与上面各卡是同一引擎的两条入口。

## 链路

建样品（Samples）→ 一键（Prepchain）或分步（Convert → Fastp → Host-Removal）→ 病毒序列提取（Identify）。`viral_sequences*.fasta` 直喂「病毒识别和分类分析」组 ⚡ 一键（[Virchain](Virchain)），kept reads 供组装备选。

上游数据来自「数据资源」组（[Download](Download) / [Logan-Fetch](Logan-Fetch)）；去宿主 reads 的定量去向见 [Kvsuite](Kvsuite) 所在的定量与共识组。
