# Phylodynamics · 进化动力学分析组导读

> 返回 [Home](Home) ｜ 上一页：[Phylo](Phylo) ｜ 下一页：[Phylodyn](Phylodyn)

进化动力学是平台在主管道（⑦ 建树）之上的扩展层：**分子钟定年、祖先状态与地理重构、种群动态、时空采样设计**。比较对象是**同一个基因 / 序列集内部**的时间与地理信号；重组先掩蔽，显著性先检验。

方法学与 VirPhyKit（Yin et al., 2025, Ecol Evol）对齐，代码全部自研。VirPhyKit 为 GPL-3.0，不复制其代码，只对齐输入 / 输出契约与算法口径。本组共 6 个模块，每个模块一篇专页。

## 模块清单

1. [Phylodyn](Phylodyn) · 进化动力学一键链 —— `t-phylodyn` 跑 A0→A5 全链（准备 → 定年 → 迁移 → 天际线 → 溯源），本地不跑 MCMC。
2. [Pd-Prep](Pd-Prep) · 数据接入 —— `t-pdprep` 三来源建标准数据集，附元数据治理、比对 QC、坐标表与时空降采样。
3. [Rtt](Rtt) · 时间信号与定年 —— `t-rtt` 做根到尾回归（R² / 斜率）+ DRT 零分布检验。
4. [Rdp](Rdp) · RDP5 重组分析 —— `t-rdp` 九方法逐方法 p 值，可导出掩蔽重组区后的干净比对。
5. [Phylogeography](Phylogeography) · 系统地理 —— `t-phylogeo` 做 Fitch 迁移重构 + 距离三分类 / 权重分带 / 迁移 GIF。
6. [BEAST](BEAST) · 贝叶斯轨道 —— 本地只打包与读回（MCMC 在服务器跑），产物由系统地理卡导入。

## 链路

**接入 → 定年 → 迁移 → 溯源**。重组掩蔽插在定年之前；判读口径见 [Phylodyn](Phylodyn) 的五条红线与 [Rtt](Rtt) 的显著性判读。

- 组内工具卡在「专项分析 → 进化动力学分析」页，任务中心统一调度；结果预览面板展示统计值 + 产物清单。
- 上述工具卡都**没有** CLI 子命令，走工具卡或操作目录 id（`phylodyn` / `pdprep` / `rtt` / `rdp` / `phylogeo`）；查参数用 `python main.py agent-ops --id <id> --schema`，执行用 MCP `vp_run_tool`。
- 相邻方向：建树基础与 MSA 查看 → [Phylo](Phylo)；比较基因组（不同病毒之间）→ [Compare](Compare)；贝叶斯 MCMC 交接包另见 [BEAST](BEAST)。
