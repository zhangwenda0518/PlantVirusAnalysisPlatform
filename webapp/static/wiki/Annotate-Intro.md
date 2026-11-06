# Annotate-Intro · 病毒注释分析组导读

> 返回 [Home](Home) ｜ 上一页：[Host-Prediction](Host-Prediction) ｜ 下一页：[Annochain](Annochain)

病毒注释分析组共 4 个模块（本页做导览，模块细节见各专页）。
本组回答「这条序列是什么、长什么样、怎么交上去」——对序列的描述与交付。

## 模块清单

1. [Annochain](Annochain) · ⚡ 一键分析 —— 输入 FASTA →（可选 kunpeng 分类）→ 功能注释 → 基因组图谱 → NCBI 提交准备。一条任务跑完。
2. [ORF](ORF) · 功能注释 —— ORF 预测（pyrodigal-rv）＋ 三层功能注释。层 1 用 DIAMOND / MMseqs2 / blastp 对照 PVDB 蛋白库，再做类别投票与 ICTV 映射。
3. [Genome-Plots](Genome-Plots) · 基因组图谱 —— gbdraw / DFV 双引擎圈图 + 线图（独立模块，自备文件也能出图）。
4. [NCBI-Submission](NCBI-Submission) · 数据提交 —— 在线编辑器 + 一键生成提交产物（表行 / sequences.fsa / featuretable / 提交包）。

## 链路

**⚡ 一键**（Annochain）或分步 **注释**（ORF）→ **图谱**（Genome-Plots）→ **提交**（NCBI-Submission）。
输入是「病毒识别和分类分析」组验证通过的病毒 contigs。
判读与写作口径见 [Result-Interpretation](Result-Interpretation)。

## 快速上手

- 输入：「病毒识别和分类分析」组验证通过的病毒 contigs FASTA（也可用任意核酸 FASTA，自备文件同样能跑）。
- 步骤一：进「⚡ 一键分析」（[Annochain](Annochain)），选 FASTA → 一条任务跑完 注释 → 图谱 → 提交准备；
- 步骤二：要分步细看就单跑 [ORF](ORF) 功能注释、[Genome-Plots](Genome-Plots) 图谱；
- 步骤三：到 [NCBI-Submission](NCBI-Submission) 在线编辑元数据表，一键生成提交产物。
- 产物：注释明细与 ORF 表、基因组圈图/线图（`run/results/<样品>/` 或 `run/tool_runs/<运行名>/`）、NCBI 提交包（`run/submissions/<项目名>/`）。
- 判读与写作口径见 [Result-Interpretation](Result-Interpretation)。
