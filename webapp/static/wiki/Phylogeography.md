# Phylogeography · 系统地理分析

> 返回 [Home](Home) ｜ 上一页：[BEAST](BEAST) ｜ 下一页：[Public-Data](Public-Data)

对应引擎：`phylogeo.py`（~3500 行，本地实现主引擎）、`phylogeo_gif.py`（动画导出）、`rdp5_engine.py`（重组）、`align_qc.py`（预筛）。对标远端 Phylogeography Dash 服务的**本地化版本**——离线、不出网。

---

## 系统地理主链路（phylogeo）

地理/时间元数据驱动的迁移重构 + 分子钟定年：

```
序列 FASTA + 采样地点/日期元数据
  → MAFFT 比对（复用平台链路）
  → 建树（复用平台 NJ / FastTree）
  → 离散性状（地点）祖先重构 + 迁移计数
  → 分子钟定年（root-to-tip / TreeTime 口径）
  → 迁移走廊表 + 时间轴
```

- 与 [Phylodynamics](Phylodynamics) 的 mugration 同口径（ML/Fitch 双口径走廊并列、采样偏倚提示），但按「地理分析」场景组织：输入更简单、直接出图；
- 前端交互：时间滑杆动画（迁移弧线随时间出现）、地点分布统计、逐走廊计数表。

## 迁移弧线动画 GIF（phylogeo_gif）

网页里的时间滑杆动画只能在浏览器里看；本功能把它导出成 **GIF 进 PPT/补充材料**：

- plotly + kaleido + Pillow，**全离线**逐帧渲染经纬度地图上的迁移弧线动画；
- 帧序 = 采样时间轴；弧线粗细/颜色 = 迁移强度。

## RDP5 重组分析（rdp5_engine）

- 集成 **RDP5 原生 Windows 命令行版**（RDP5CL.exe）——RDP5 本体是 Windows 程序，平台直接原生调用（服务器版反而要 Wine）；
- 检测重组事件（RDP/GENECONV/Bootscan/MaxChi/Chimaera/SISCAN/3SEQ 等方法组），输出重组断点与亲本推断。

## 重组前序列预筛（align_qc）

对齐参照管道同一套判据、同一组文件名（`align_qc_report.tsv` / `clean.fasta` / `removed.fasta`）：剔除不完整片段与低质量序列——重组检测前先保证输入质量。

## 界面与产物

- 专项分析页「系统地理」卡片区；结果面板含动画预览、走廊表、地图点位、重组事件表；
- 产物：比对/树/迁移表/GIF 动画/重组报告（任务预览面板直接列出可下载）。

## 相关

- 分子钟与走廊的完整方法学（含对拍验证）→ [Phylodynamics](Phylodynamics)
- BEAST 贝叶斯轨道 → [BEAST](BEAST)
- 时空采样设计（降采样）→ [Phylodynamics](Phylodynamics) GeoSubsampler
