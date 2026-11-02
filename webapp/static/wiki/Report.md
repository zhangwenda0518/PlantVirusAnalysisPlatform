# Report · ⑩ 可视化报告

> 返回 [Home](Home) ｜ 上一页：[Genome-Plots](Genome-Plots) ｜ 下一页：[Phylodynamics](Phylodynamics)

对应引擎：`viz.py`。把一个样品的全部阶段产物汇总成**单文件交互式报告** `results/<样品>/07_report/report.html`（双击浏览器打开，plotly 全离线）。

---

## 报告内容

| 板块 | 图表 |
|---|---|
| 样品概览 | 测序量、宿主占比、病毒 reads、组装/注释关键数字 |
| 分类组成 | **桑基图**（reads → 宿主/病毒科属种流向）、**旭日图**（分类组成，pycirclize） |
| 已知病毒 | ②b 鉴定表/定量表 Top、覆盖度-深度 |
| 组装 | contig 长度分布、N50、病毒 contig 一览 |
| 宿主预测 | ④ 的桑基/旭日（科→宿主、科→属→种）嵌入 |
| ORF 注释 | 功能类别分布、科分布、Top 注释表 |
| 进化 | ⑦ 树图（tree.png）与 SDT 热图嵌入 |
| 基因组图 | ⑨ 圈图/线图 SVG 内嵌 |
| 引物 | ⑧ 引物表 |

## 特性

- **全离线单文件**：plotly/pycirclize/matplotlib 图全部内嵌，无需联网、可直接归档或发合作者；
- **结果预览面板**：任务结束后任务卡自动展开——各阶段关键数字 + 「打开报告」；页面在后台时发系统通知；
- **阶段产物在线预览**：9 个阶段卡片带 `📊 查看`——表格就地弹窗（含下载），HTML 报告新窗口；
- **重新生成**：产物未变只想换报告样式时，`report` 子命令/卡片单独重跑（`--force`）。

## CLI

```bat
python main.py report --sample S1 [--force]
python main.py analyze --r1 ... --sample S1 --stages report
```

## 相关

- 每个阶段的产物路径 → [Architecture](Architecture) 样品结果目录
- 交互查看器（树/MSA/SDT）→ [Phylo](Phylo)
