# Compare · 比较基因组分析组导读

> 返回 [Home](Home) ｜ 上一页：[Mirna](Mirna) ｜ 下一页：[Cmpchain](Cmpchain)

本组做**不同病毒之间**的比较：跨科 / 属取参考序列 → 比对 → 建树 / SDT 同一性。导航组 `compare` 共 **6 张卡**，拆成 5 篇（进化树 + SDT 同流程，合一篇），每篇一份专页。

## 模块清单

1. [Cmpchain](Cmpchain) · 比较基因组一键链 —— `t-cmpchain` 一条任务串起取参考 → 提取 → 比对 → 建树 → SDT，三种输入皆可。
2. [Seq-Prep](Seq-Prep) · 参考序列获取 —— `t-seqprep` 四条路取参考，产带注释的 GenBank 集合与 FASTA 参考集。
3. [Cds-Export](Cds-Export) · CDS / PEP 提取产物 —— 独立页 `/cds-export`：明细表 → 挑选改名 → 按基因归组导出 CDS + PEP。
4. [Align](Align) · 序列比对（MAFFT + trimAl） —— `t-align` 做 MAFFT 比对与 trimAl 清剪，查看器可看可编辑。
5. [Phylo](Phylo) · ⑦ 进化树与 SDT —— 两卡一篇（`t-treebuild` + `t-sdt`）：三档建树 + 交互树查看，SDT 出矩阵与热图。

## 链路

**参考获取 → 提取 → 比对 → 建树 / SDT**。取哪条看手上有什么。没有参考就从 [Seq-Prep](Seq-Prep) 起，已有集合就挑 [Cds-Export](Cds-Export) 或 [Align](Align)。只想一把跑完，用 [Cmpchain](Cmpchain)。

与样品流程 ⑦ 的分工：本组比**不同病毒之间**，产物在 `run/tool_runs/<cmpchain_run>/0X_*/`。样品流程比**本样品 contigs + 近缘参考**，产物在 `run/results/<样品>/05_phylo/<组>/`。两者共用 MAFFT + trimAl、NJ / FastTree / RAxML-NG 与 SDT 精确引擎。

- 外部 agent：一键链 / 比对 / 建树 / SDT 都**没有** CLI 子命令，走工具卡或操作目录 id（`cmpchain` / `align` / `quicktree` / `sdt`）；取参考有 `gb-dl` / `gb-import` / `gb-list` / `gb-check` / `ncbi-dl` / `ncbi-list` 六个子命令（见 [Seq-Prep](Seq-Prep)）。
- 查参数用 `python main.py agent-ops --id <id> --schema`，执行用 MCP `vp_run_tool`。
- 相邻方向：公共数据检索与批量下载 → [Public-Data](Public-Data)；进化方向的分子钟 / 地理 / 重组 → [Phylodynamics](Phylodynamics)；单步工具箱其他工具 → [Toolbox](Toolbox)。
