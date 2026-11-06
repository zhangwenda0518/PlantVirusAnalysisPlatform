# cricon 双路线联合检测设计报告 — example_acmv.fasta

- 时间：2026-11-05 02:13:03，耗时 44.8s
- routes：both；失败路线：无
- 输入：1 条序列 / 1 个片段组

## 预检警告
- ⚠ 输入仅 1 条序列：群体覆盖率平凡（n=1，覆盖率恒 100%），真实设计建议 >=5-10 条分离物。
- ⚠ CONAN 路线未提供负组（near_fasta）：负组同源位点双重失配判据缺位，本轮仅通用 Cas3 设计，无近缘区分能力。

## 路线概览

### crispr（Cas12a/Cas13a crRNA×引物协同）

| 指标 | 值 |
|---|---|
| 组合数 | 20（PAM 位点漏斗 81 -> 59） |
| 最优评分 | 79.3 |
| 效应酶 | LbCas12a（amp=RPA，profile=strict，宿主筛查=开） |
| 产物 | crispr/（report.html 交互报告 + ranked_sets.tsv + 订购表） |

### conan（Cas3/I-E 负组双重失配）

| 指标 | 值 |
|---|---|
| 候选数 | 20；Top 中 leak=0 20/20 |
| 负组 | 未应用（同源位点双重失配判据缺位） |
| 口径 | PAM AAG + spacer 32nt，种子 +6 忽略位（I-E 口径） |
| 产物 | conan/（report.md + candidates.tsv + target_checks.tsv + negative_checks.tsv） |

## 覆盖矩阵（片段 x 路线）

| 片段 | crispr 组合 | conan 位点 | status |
|---|---|---|---|
| main | 20 | 20 | 双路线覆盖 |

- 坐标不可比跳过片段：main（两路线共识长度不一致）

## 场景建议表（只给建议，不自动重跑）

| 场景 | 路线 | 推荐组合/候选 | 配套 | 理由 |
|---|---|---|---|---|
| 种内通用筛查 | crispr | ATTGGAGAGAAAGTGTTTGA（PAM TTTG，位点 2640-2663） | F CGATTGTTCGTAATCGTGATTTTACCCTCG / R GCCAAGAATGTCTTTCTCACATACCCAAAG（产物 191bp） | crRNA+引物成套试剂 + 群体覆盖因子，双库筛查后评分最高 |
| 现场侧流（LFD） | crispr | rank1 组合（本轮未设计探针） | 需 LFD 时用 cr_probe_type=nfo 重跑 | EXO=荧光 / NFO=侧流试纸读出 |
| 现场免仪器（CONAN-SWIFT 论文路线） | conan | RT-LAMP + Cas3 反式切割 + 侧流层析 | RT-LAMP 兼容性/ATP/LFD 配对需实验建立（见验证清单 CONAN 段） | SWIFT = Simple Workflow for Isothermal Field Testing |

## 0 组合排障（历史真实教训）
- 混分子输入：不同分子混进一次 MAFFT -> 共识 N 列稀释 -> PAM/引物扫描被跳过。对策：按分子分文件或 header 打片段标签（RNA1/S2）。
- 宿主库 mm0 误杀：大宿主库高灵敏筛查对类病毒等小分子病原过严 -> cr_use_host=0，并在 L2 实验段补宿主背景验证。
- strict 产物窗口 vs 小分子：432nt 级小分子上 strict 档（100-200bp + 25bp 边距）引物难落位 -> cr_profile=relaxed（90-300bp）。

## 语义注记

- near_fasta 双喂语义：crispr 路线当排除库（k-mer/BLAST 序列级筛查）；conan 路线当负组（进合并 MSA 做同源位点双重失配判定）。同一文件两条语义、两套独立判定，不合并成单一"特异性"数字。
- CONAN 向导宿主筛查未做：CONAN 特异性判据是负组双重失配；序列级筛查仅对近缘文件补位（若提供）。
- 同位点收敛标记：两路线共识长度不一致时跳过（坐标不可比）。

## 证据边界

> 证据边界（ACS Sensors 2026 综述口径，双路线同一口径）：本结果为序列级候选优先级排序，不构成 crRNA 活性、错配区分度、扩增兼容性或基质性能的证据；实验结论须按 cricon/validation_checklist.md 的 L1-L5 逐级建立。

## 引用
- Wang H. et al., ACS Sensors 2026（证据边界框架）
- Low S.J. et al., Commun Biol 2025 PathoGD（群体覆盖与近缘筛查口径）
- CASPER, iGEM 2025 Lambert GA（crRNA x 引物复合评分特征体系）
- AutoCORDSv2 / RPA-Primer-Design / CRISPRPrimerDesigner（参数口径参考）
- Yoshimi et al. 2022, Commun Biol（CONAN：CRISPR-Cas3 诊断）
- CONAN-SWIFT, bioRxiv 2026.08.25.746999（RT-LAMP + Cas3 反式切割 + 侧流层析现场路线）
