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

# 平台版本号**唯一权威源**（2026-11-05 自动更新功能引入）：打包时写入各包
# build_info.json 的 version 字段，运行时 updater.current_version() 优先读
# build_info、缺失时回退本值。发版只在此处 bump（对齐 CITATION.cff/.zenodo.json）。
# 1.3.0（2026-11-06）：全面审查修复版——5 个 P0（更新链重装循环/更新校验
# fail-closed+签名/QA 工具泄漏/档案 Key 泄露/A3 崩溃）+ 16 个 P1 + 24 个 P2，
# 新增 module-report、自动更新、sysmon 等。
__version__ = "1.3.0"
