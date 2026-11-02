# -*- coding: utf-8 -*-
"""Virus_Platform_Core — 植物病毒分析平台核心包。

平台的**计算与流程层**（GUI 与 CLI 共用）：
  - 主流程编排 : pipeline
  - 分类与建库 : kunpeng / taxonomy
  - 组装与注释 : assembly / orf / orf_annot / contig_annot / hmm_annot
  - 进化与比较 : phylo / sdt_exact / msa_view
  - 可视化报告 : viz / gbdraw_plot / dfv_plot / genome_diag
  - 结论文档   : report_kit（学术论文风 report_v2.html，任务无关）
  - 路径与工具 : config
子包：
  - web/            HTTP 边界与任务工厂（Flask blueprint）
  - public_meta/    公共数据检索引擎（SRA/GSA 元数据）
  - ncbi_submit/    NCBI 序列提交准备

入口壳（app.py / main.py，均为本包的同级文件）只做组装，业务逻辑写在本包内。
"""

__version__ = "1.0.0"
