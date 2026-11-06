# Public-Data · 数据资源组导读

> 返回 [Home](Home) ｜ 上一页：[Architecture](Architecture) ｜ 下一页：[Meta-Search](Meta-Search)

本组回答「公共数据如何变成可分析的输入」：从按物种检索、预筛含病毒、批量下载，到参考件获取与本地建库，共 8 个模块。每个模块一篇专页，本页只做导览。

## 模块清单

1. [Meta-Search](Meta-Search) · 公共数据检索 —— SRA + GSA 双引擎按物种检索 Run，出 Core14 / Full 元数据与出版级图组。
2. [Prescreen](Prescreen) · 病毒样本预筛 —— 不下载测序数据，用预计算分类判断哪些 Run 含病毒，出通过 / 未决清单。
3. [Open-Virome](Open-Virome) · 公共病毒组浏览 —— 内嵌 SPA，浏览公共病毒组数据并导出 Run。
4. [Logan](Logan) · LOGAN 溯源 —— 把病毒 contig 提交 Logan-Search，反查还出现在哪些公开样本。
5. [Logan-Fetch](Logan-Fetch) · LOGAN 数据下载 —— 按 SRA accession 直取 Logan 公共库的预组装 contigs。
6. [Download](Download) · 公共数据下载 —— SRR / ERR / DRR / CRR 或 URL 批量取 FASTQ，一键转入分析。
7. [Ref-Data](Ref-Data) · 基因组查询下载 —— 宿主基因组（NCBI / gget · Ensembl）加 datasets 全模式（含病毒数据集）。
8. [Databases](Databases) · 数据库与参考库 —— Taxonomy / 宿主库 / 病毒库，把下载物变成可检索的索引。

## 链路

**检索**（Meta-Search）→ **预筛**（Prescreen）→ **下载**（Download）→ **建库**（Ref-Data → Databases）。Open-Virome 与 Logan / Logan-Fetch 是并行的只读取数入口，取到的 Run 可随时插回这条链；下游接样品分析与结果解读。
