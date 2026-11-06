# CONAN-SWIFT（Cas3 / I 型 I-E）crRNA 设计报告

## 结果概览

| 片段组 | 目标记录 | 负组记录 | 候选(过滤前) | 过滤后 |
|---|---|---|---|---|
| main | 1 | 未提供 | 108 | 52 |

## Top 候选（全表见 candidates.tsv）

| # | 候选 | 链 | 位点 | spacer(向导=RC列) | GC%% | 覆盖%% | leak%% | score |
|---|---|---|---|---|---|---|---|---|
| 1 | CNmain_00005 | + | 225-259 | `AAGTATATAGTGGCCCACCACTAATACATAAC` | 37.5 | 100.00 |  | 200.00 |
| 2 | CNmain_00006 | + | 267-301 | `CCAGTGGATCCCACATTGCGCACTAGCCACAA` | 56.25 | 100.00 |  | 200.00 |
| 3 | CNmain_00007 | + | 452-486 | `CCTGGAGTGGAAATGATGATATCTCCTGGTCG` | 50.0 | 100.00 |  | 200.00 |
| 4 | CNmain_00008 | + | 491-525 | `TATGGGCTGTCGAAGTTCAGTCTTCGACGAAC` | 50.0 | 100.00 |  | 200.00 |
| 5 | CNmain_00009 | + | 502-536 | `CACGGTTCCTGTATGGGCTGTCGAAGTTCAGT` | 53.12 | 100.00 |  | 200.00 |
| 6 | CNmain_00013 | + | 665-699 | `TTCACATCATCCCTCTGCTCAAACGACTGGAC` | 50.0 | 100.00 |  | 200.00 |
| 7 | CNmain_00014 | + | 698-732 | `ACATCACTAATCACCTTACAGATACCAAGGTG` | 40.62 | 100.00 |  | 200.00 |
| 8 | CNmain_00015 | + | 716-750 | `AGCCCAGGCCCACGCGTCACATCACTAATCAC` | 59.38 | 100.00 |  | 200.00 |
| 9 | CNmain_00024 | + | 1031-1065 | `AACCTGTAAAACCTTTTCACCAACGCCTGCTC` | 46.88 | 100.00 |  | 200.00 |
| 10 | CNmain_00026 | + | 1103-1137 | `AGAAGCAAAGCATTCTCTGTGTGATTCTCATA` | 37.5 | 100.00 |  | 200.00 |
| 11 | CNmain_00030 | + | 1381-1415 | `AGATCTGGACAACCTTACAGCCTCAGACTGGT` | 50.0 | 100.00 |  | 200.00 |
| 12 | CNmain_00031 | + | 1400-1434 | `AAATGTTTTCTCAACTTCCAGATCTGGACAAC` | 37.5 | 100.00 |  | 200.00 |
| 13 | CNmain_00033 | + | 1629-1663 | `TGTGGATGTTCATATTACCTCCATATCAACTG` | 37.5 | 100.00 |  | 200.00 |
| 14 | CNmain_00034 | + | 1667-1701 | `AGAAGCGAGCCATCCGGCGTAGGAGAGTGGAT` | 59.38 | 100.00 |  | 200.00 |
| 15 | CNmain_00035 | + | 1762-1796 | `GCGCTAAAGGCCTGGGCATTAAAGAATGCAAT` | 46.88 | 100.00 |  | 200.00 |
| 16 | CNmain_00039 | + | 2022-2056 | `GCCCACATAATTACCTGTGTGGACACCTGGAC` | 53.12 | 100.00 |  | 200.00 |
| 17 | CNmain_00040 | + | 2060-2094 | `TAGCAGAACAGGGAAGACGATATGGGCCAGAT` | 50.0 | 100.00 |  | 200.00 |
| 18 | CNmain_00041 | + | 2194-2228 | `GCTCCTTATGTTTCTCCCTTCCCATGTTCTTC` | 46.88 | 100.00 |  | 200.00 |
| 19 | CNmain_00044 | + | 2222-2256 | `TTTAGATAGGATTTTCCAAGAGCCACCAGCTC` | 43.75 | 100.00 |  | 200.00 |
| 20 | CNmain_00047 | + | 2319-2353 | `AAGCGCTTAACAGCGGCAGTAAGTCAGAAGCT` | 50.0 | 100.00 |  | 200.00 |

## 参数快照

- PAM：`AAG`（5′ 前置）；spacer：32 nt；种子：PAM+1 起 8 nt（忽略位 6）
- 目标非种子错配上限：4；末端排除：150 bp；GC 区间：35.0–65.0%；
  种子同碱基 run ≥3 剔除
- 负组 FASTA：未提供（负组评分按 0 计）
- 引擎：VirusPlatform conan_design 1.0.0

## 方法学与规则来源

净室实现（代码未复制，仅方法学与参数口径对齐）：
- CONAN-SWIFT 论文（bioRxiv 2026.08.25.746999）：AAG PAM / 32nt spacer /
  种子 8nt 忽略 +6 / 负组同源位点双重失配规则 / 四项评分公式
- 参考开源 conan-swift-designer / conan-crrna-webui（MIT）：
  过滤参数（末端 150bp / GC 35–65 / 种子 run≥3）

**评分使用边界**：score 为率差线性组合，数值依赖本次目标/负组条数，
仅限单次输入内的候选排序比较，跨数据集不可比。

**负组同源性要求**：负组应为近缘非目标病毒的全基因组序列（与目标整体
同源 ≥70–80% 为宜）。位点块嵌于无关序列时，比对列配准可能漂移，
负组核查结果不可信。

## 证据边界

计算设计仅支持候选优先级排序，不构成 crRNA 活性、错配区分度、扩增兼容性
或基质性能的证据；实验结论须按平台 L1–L5 验证清单逐级建立。

**订购提示**：本表只输出 spacer 向导序列（guide_spacer_5to3_RC 列）。
I 型 CRISPR crRNA 需 5′ 端接直接重复（DR）骨架构成完整 crRNA，DR 序列
属所用 Cas3 系统的实验构造件——订购前按所用系统的 Protocol 补齐
DR-spacer 完整序列并复核方向。
