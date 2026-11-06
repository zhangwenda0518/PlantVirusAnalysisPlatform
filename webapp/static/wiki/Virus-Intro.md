# Virus-Intro · 病毒识别和分类分析组导读

> 返回 [Home](Home) ｜ 上一页：[Variant](Variant) ｜ 下一页：[Virchain](Virchain)

病毒识别和分类分析组共 4 个模块 + 1 篇管线 ④ 视角文档（本页做导览，模块细节见各专页）。
本组在 **contig 层**回答「组装出来的东西里哪些是病毒、是什么病毒、可信吗」。

## 模块清单

1. [Virchain](Virchain) · ⚡ 一键分析 —— 病毒组装 → 病毒鉴定 → 候选序列验证三步一键（只收 FASTA）。
2. [Assembly](Assembly) · 病毒组装 —— SPAdes rnaviral / MEGAHIT 组装 + contigs 过滤（管线 ③ 同引擎）。
3. [Contigs](Contigs) · 病毒鉴定 —— contig 病毒分类 + 谱系注释（管线 ③ 的鉴定半部）。
4. [Verify](Verify) · 候选序列验证 —— 批量验证（宿主筛选 → 长度分流 → blastx / BFVD / CDD 三路证据 + 类病毒 blastn）＋ 单条深查。深查为 BLASTN / BLASTX 同源比对。
5. [Host-Prediction](Host-Prediction) · 宿主预测（管线 ④）—— 病毒 contig 的宿主概率级联判定，与验证互补的宿主侧证据。

## 链路

**⚡ 一键**（Virchain）或分步 **组装**（Assembly）→ **鉴定**（Contigs）→ **验证**（Verify）→ **宿主预测**（Host-Prediction）。
输入是「样本处理」组的 `viral_sequences*.fasta` 或去宿主 FASTA。
验证通过的 contigs 交「病毒注释分析」组（Annochain / ORF）做功能注释与提交准备。
