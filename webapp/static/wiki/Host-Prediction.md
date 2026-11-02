# Host-Prediction · ④ 宿主预测（ICTV 级联）

> 返回 [Home](Home) ｜ 上一页：[Assembly](Assembly) ｜ 下一页：[ORF](ORF)

对应引擎：`host_analysis.py`（方法吸收自 MMPV-RNA virome_discovery_pipeline 的 C9 方法）。对 ③ 组装产出的病毒 contigs 判定**感染宿主类别**。

---

## 方法（四步级联）

1. **kunpeng 分类判定**：contig → kunpeng 分类（kraken2 同构 C 行）→ 病毒库 info.tsv 同 taxid 的 ICTV 分类（种/属/科）；LCA 判到属/科级时经 nodes/names.dmp 谱系解析还原完整分类（学名优先、跨样品缓存）；BLAST top hit 仅作最后回退（数据来自 ③ 的 `virus_contigs.tsv` blast 六列，缺列自动补齐）；
2. **级联查宿主概率表**：`databases/misc_db/host_prob/{species,genus,family,order}_host_probability.tsv`（共 **5.9 万条目**，源自 ICTV 分类 × 宿主记录交叉统计）——种→属→科→目**逐级回退** + 列错位容错，取层级最深、置信度最高者；
3. **交叉证据**：info.tsv 的 NCBI 宿主元数据（accession 官方宿主记录）经 NCBI taxonomy 归类到宿主类别（带缓存）；
4. **决策**：两者一致 = **Agree**；不一致时 NCBI 元数据（一手证据）优先；仅其一用其一；全无 = **Unknown**。

## 产物（08_host_analysis/）

| 文件 | 内容 |
|---|---|
| `host_prediction.tsv` | 逐 contig 明细（分类、宿主预测、置信度、证据来源、host_check） |
| `host_summary.tsv` | 按宿主类别汇总 |
| `{类别}.classified.fasta` | 按预测宿主拆分的 contig 子集 |
| `sankey_host.html` | 病毒科 → 宿主类别桑基图 |
| `sunburst_host.html` | 科 → 属 → 种旭日图 |

桑基/旭日图自动嵌入 ⑩ 报告。

## 与参考库联动

部署可选参考库 `databases/virus_ref/`（非冗余植物病毒库，见 [Databases](Databases)）后，④ 用其 ICTV 谱系 + 已知宿主**交叉验证**：预测宿主 ∉ 已知宿主类别 → `host_check=WARN`，报告标黄。该目录不存在时回退自带数据，平台照常运行。

## 界面与 CLI

- 分析管道 ④ 卡片：`📊 查看` + 桑基/旭日图在线打开；
- 逐 contig 明细在结果中心可浏览。

```bat
python main.py host-analysis --sample S1 [--force]
python main.py analyze --r1 ... --sample S1 --stages hostana
```

## 相关

- 感染证据的另一路印证 → [ORF](ORF) ⑥b 功能注释（命中物种的 ICTV 宿主类别交叉）
- 病毒来源追溯（公开数据里还出现在哪）→ [Logan](Logan)
