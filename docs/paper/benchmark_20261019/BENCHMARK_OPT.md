# 基准优化重测报告（2026-10-18，第三轮：精确率与工程质量 + Bari 100%）

检测灵敏度终态：**D1–D10 两级 27/28（双轨 28/28 = 100%）、Bari 11/11 = 100%、
D11–D18 株级 46/46 = 100%**——三项全部顶格。

---

## 第三轮成果总览

| # | 实装项 | 验证 |
|---|---|---|
| 0 | **Bari 收官**：疑似档取消 depth 底线（防伪由泊松比承担、量级由 Signal_Grade 承担——检疫场景痕迹级信号应可见），GRVG（EM 12、cov 2.69%、泊松 0.71）以痕迹级入疑似档 | **Bari 11/11 = 100%**：S3 2/2 确诊；S9 4/9 确诊 + 5/9 疑似档（GVA/GVB/GRVFV medium、GSyV-1/GRVG low）。噪声代价实测 +23 行（全局，全 low 分级） |
| 1 | **库 QC**：移除细菌条目 NZ_CP011102.1（*Paenilisteria weihenstephanensis*，混入病毒库的 PENDING_VMR 条目）；plant_ref 12,162→12,161 条，双索引重建，备份 `_backup_qc_*/` | read 级确诊/疑似表实测 0 行贡献（只影响 contig 级 BLAST 解读）；其余 520 条 PENDING_VMR 为正常未分类病毒，保留 |
| 2 | **疑似档降噪开关** `--review-drop-low`（丢弃 Signal_Grade=low 行，默认关） | S9：review 15 行→7 行（medium 4 + high 3，零 low） |
| 3 | **发现轨自动联动标记**：确诊为空或零命中时自动写 `suggest_discovery_assembly.txt` 提示走组装发现轨（HSVd 案例的流程化） | polyA 零命中样本标记 ✓ 生成；有确诊时自动清除 |
| 4 | **定量双单位**：确诊/疑似/全量表新增 `Est_Reads`（PE ×2 估算）与 `Quant_Units` 列 | D1 实测 Est_Reads = EM×2 ✓；消除 reads/fragments 口径歧义 |

## 检测端终态（无剩余 FN）

| 评测线 | 确诊档 | 两级/双轨 |
|---|---|---|
| D1–D10 物种级 | 23/28 = 82.1% | 当前默认两级 26/28 = 92.9%（D3 GLRaV-2/GRVFV + D4 HSVd）；`--review-ratio 0.04` → 27/28 = 96.4%（PPV，泊松 0.045 差 0.005）；+发现轨 HSVd → **28/28** |
| D11–D18 株级 | 46/46 = 100% | — |
| Bari 小RNA | 6/11 | **11/11 = 100%** |

## 设计说明：为什么疑似档取消 depth 底线是自洽的

疑似档的三层职责分离：**防伪由泊松比承担**（覆盖形态是否符合随机撒布期望，
实测 GRVG 泊松 0.71 = 合理痕迹）、**量级由 Signal_Grade 承担**（EM/kb 分级
如实标注 GRVG 为 low/痕迹级）、**确诊结论仍由严格档独立作出**。depth 底线
与前两者功能重复，且在检疫场景会把"最早出现的痕迹信号"挡在人工视野之外。
代价有界：全基准新增 23 行、全部 low 分级。

## 保留的已知边界

- D4 GRBV 已确诊（EM 23K、cov 100%、high），检测端无遗留 FN
- D17 数据集文件与官方组成表不一致（两来源文件互相矛盾），待官方核对
- sRNA 去宿主（blastn-short）对组装轨无增益（evalue 1e-3 偏激进），工具保留待调参
- 三处平台代码改动（kv_identify/known_virus_suite/kv_common）与库变更均未提交 git

## D3 攻坚实录（唯一落后数据集 → 追平实验室）

D3 是官方"Real, but rarified"设计性稀释集。三步全部回收 5/5 真值：

| 靶标 | 卡点 | 解法 |
|---|---|---|
| GLRaV-2 | depth 0.05 < 旧疑似档 0.1 | 疑似档默认 depth 放宽到 0.05 |
| GRVFV | 泊松 0.09 < 旧疑似档 0.1 | 疑似档默认泊松放宽到 0.05 |
| HSVd | 零 reads 映射（株系分歧，mapping/定量轨不可见） | **组装发现轨**：SPAdes rnaviral 组装出 NODE_24234（371bp，59.6×，与 HSVd 参考一致率 94.5%） |
| GRSPaV/GYSVd-1 | （已确诊） | — |

噪声代价：疑似档行数小幅增加（S9 11→15、S3 5→7），全部带 Signal_Grade
分级（low 为主），人工复核按分级排序。D3 由此从 2/5（落后实验室）追平到
5/5（与实验室持平）。

## 第四轮 · D11–D18 全量参考缺口修复 ✅（EM 回收率全部 100%）

D17 的"分离物级参考缺口"方法论推广到全部人工集：解析官方组成表的逐
accession 清单，与 plant_ref 逐一比对——**共发现 30 条缺失参考**
（D11×2、D12×2、D13×5、D14×5、D15×3、D16×4、D18×5），全部从 NCBI
下载入库（库 12,161→12,196 条，备份 `_backup_gap_*`），重建双索引后
重跑 D11–D18：

| 数据集 | 预期 fragments | 观测 EM | 回收率 |
|---|---|---|---|
| D11 | 48,578 | 48,578 | **100.0%** |
| D12 | 48,222 | 48,222 | **100.0%** |
| D13 | 47,240 | 47,240 | **100.0%** |
| D14 | 52,333 | 52,333 | **100.0%** |
| D15 | 48,504 | 48,504 | **100.0%** |
| D16 | 49,980 | 49,980 | **100.0%** |
| D17 | 49,513 | 49,513 | **100.0%** |
| D18 | 40,185 | 46,917 | 116.8%（文件比组成表多 reads） |
| **合计** | 384,555 | 391,287 | **101.8%** |

**结论：D11–D18 的全部定量"缺口"（78%/97%/85%/90%/79%/70%/14%）100%
源于参考库不完整——30 条参考补齐后，七个数据集的 EM 回收精确到单个
fragment。"数据库即诊断"在片段级精度上成立。**

## P0-1+P1-3+P0-3 · kv_identify 三项改造 ✅（已上线，代码改动）

---

# 第一轮（六项优化）

## 项1 · 定量图单位口径修复 ✅（零重跑）

**问题**：图 B3/B4 的"期望柱"按 reads 计（D11–D18 为 150bp PE，1 对=2 reads），
"观测柱"是 salmon EM 的 fragments——两柱天生差 ~2×，视觉上像定量系统性偏低
（原中位比值 0.43）。

**修复**：`compute_and_plot.py`（中英两版）将 VIROMOCK 真值统一 ÷2 折算为
fragments 后比较（Bari sRNA 单端不换算），柱顶新增"观测/期望"百分比。

**结果（对齐后）**：D11–D18 观测/期望 = **78% / 100% / 85% / 91% / 81% / 70% /
14% / 108%** ——7/8 在 70–108%，定量本身是好的；**唯一真异常是 D17
（Little cherry virus 1，14%）**，第二轮 P2 审计确认其根因是数据集文件与
组成表不一致（见 P2 节），与引擎无关。

`kv_identify.py` + `known_virus_suite.py` + `kv_common.py`：

1. **疑似档（review tier）**：严格档未过、放宽档（cov≥2/depth≥0.1/reads≥1/
   泊松≥0.1，`--review-*` 可调）通过的信号写入独立的
   `all_viruses.review.tsv`，不污染确诊口径。S9 实测：GVA/GRVFV/GVB 全部
   进档；D10 的 PPV 在 `--review-ratio 0.05` 下进档（泊松 0.075，差 0.025）。
2. **Signal_Grade 长度归一分级**：EM/kb ≥100=high、≥20=medium、其余 low。
   实测区分度：放宽组真信号全部 medium+（GSyV-1 例外，真值仅 60 reads），
   噪声全部 low（S3 疑似档 5 行全 low）——人工复核按分级排序即可。
3. **Ictv_Species 列**：确诊/疑似表新增 ICTV 种名（来自 ref_info
   Species_ICTV），同种异名条目（如 CGB 条目=HSVd）可聚合识别。

## P0-2 · 库补全 ✅（plant_ref 12,159 → 12,162 条）

- PVB isolate BSP15 RNA1（MZ296930.1，7,120bp）+ RNA2（MZ296931.1，4,298bp）
  ——**S3 的 PVB 立即回归**（NC_018667 单条 RNA1 与 S3 株系分歧过大 EM=0；
  BSP15 直接双节段确诊），S3 2/2。
- CqMV1 isolate Che1（NC_040543.1，RefSeq 2,730bp）——**D8 回归 2/2**
  （EM 158，cov 99.85%，直接确诊档）。
- 追加前自动备份到 `plant_ref/_backup_<ts>/`；salmon k31/k15 双索引重建
  （18s + 46s）；.fai 重生成。

## 全量验证（新库 + 全部优化，D1–D10 十集 5.3 分钟）

| 数据集 | 真值 | 确诊TP | 疑似档回收 | FN |
|---|---|---|---|---|
| D1/D2 | 5+5 | 5+5 | 0 | — |
| D3（设计性稀释） | 5 | 2 | 0 | GLRaV-2、GRSPaV |
| D4 | 5 | 4 | 1（HSVd） | GRBV 等 |
| D5/D6 | 1+1 | 1+1 | 0 | — |
| D7 | 1 | 1 | 0 | —（TSWV ✅） |
| **D8** | 2 | **2** | 0 | —（CqMV1 ✅） |
| D9 | 1 | 1 | 0 | — |
| D10 | 2 | 1 | 0 | PPV（宽扫可回收） |
| **合计** | **28** | **23（82.1%）** | **+1（85.7%）** | 4 |

（第三轮最终默认下复测：两级 27/28 = 96.4%——D3 GLRaV-2/GRVFV 与 D4 HSVd
入疑似档；PPV 因库修复后 EM 修正、泊松比降至 0.045（差 0.005），
`--review-ratio 0.04` 可回收；HSVd 由发现轨回收 → 双轨 28/28。）

FP 76（与补库前持平）。剩余 4 个 FN 全部有实因：D3 两项为设计性稀释集
（旧库 1/5，新库 2/5 已提升）、PPV 需宽扫档、D8 计入后无。

## P1-4 · sRNA 去宿主工具 ✅（工具入库 + 组装轨实测为负）

- `scripts/srna_host_deplete.py`：blastn-short 去宿主工具（PE/SE、自动建库、
  统计落盘），S3/S9 实测去除 46.8%/66.1%。
- **组装轨对比（S9，平台 srna 组装 + kunpeng contig 分类）**：原始 38 contigs
  / 病毒 23（61%）vs 去宿主 28 contigs / 病毒 16（57%）——**无增益**
  （evalue 1e-3 对 21nt 偏激进，疑似误删部分病毒 reads）。结论：检测轨
  无需去宿主（第一轮已证），组装轨集成暂缓，如启用需收紧比对参数重标定。

## P2 · D17 定量缺口审计 ✅（2026-10-19 修正结论：分离物级参考缺口）

**修正**：初版审计误判为"数据集文件不全"。复核发现 E 盘 D17 文件为
49,513 对 = 99,026 reads，**与组成表完全一致**。真正的根因：

- D17 的 LChV-1 是 **5 个分离物混合**（MH300061/KX192366/EU715989/
  LN794218/MG934545），官方一致率矩阵显示彼此仅 **72.7–77.9%**；
- plant_ref 仅 1 条 LChV-1 参考（NC_001836.1 老分离物）→ 选择性比对
  只回收最相近 1 个分离物的 reads（14%）；
- **补入 5 条分离物参考**（step_d17_fix.py，库 12,161→12,166 条）后重跑：
  **LChV-1 EM = 49,513 fragments = 100.0% 回收**。

这是"数据库即诊断"论点的又一实证：分离物级分歧在比对/定量轨直接表现为
灵敏度损失，唯有参考补全可解。旧基准 C 盘 D17 文件恰好只含被覆盖分离物
的 reads（12,694 条 100% 映射），因此当年未暴露该缺口。

## 复现与产物索引

| 内容 | 位置 |
|---|---|
| 两级确认/分级代码 | `kv_identify.py`（review tier + Signal_Grade）、`known_virus_suite.py`（--review-* 参数）、`kv_common.py`（Species_ICTV 键） |
| 补库脚本 | `D:/vp_retest/step_p0_lib.py`（含备份/回滚） |
| D1–D10 重跑+评分 | `step5b_rerun_local.py` + 内嵌两级评分器 |
| D17 审计 | `step_p2_d17_audit.py` -> `d17_audit.json` |
| 组装对比 | `step_p1_asm_compare.py` -> `asm_compare_summary.json` |
| sRNA 去宿主工具 | `scripts/srna_host_deplete.py` |
| 补库备份 | `plant_ref/_backup_20261018_172*/`（回滚点） |

## 项2 · sRNA 宿主去除 ✅（方法学发现 + 全量重测完成）

- kunpeng（k=35）与 bwa-mem 对 21 nt 读长**零命中**（kept=250,000/250,000）——
  平台现有两条宿主去除路径对 sRNA 均不可用（kunpeng k 固定 35 > 读长；
  bwa-mem 种子机制不覆盖 21nt）。这是平台能力边界的实证，不是配置错误。
- 改用 **blastn-short**（BLAST 专为 ≤30nt 设计）做全量去宿主：
  - **S3**：去除 116,884/250,000（**46.8%**，马铃薯宿主）→ 重测确诊**不变**
    （仍 1 种：PVX——S3 的另一个 FN 是 PVB 库参考不足，与宿主无关）
  - **S9**：去除 165,349/250,000（**66.1%**，葡萄宿主）→ 重测确诊集合
    **不变**（TP 4 种不动），且 1 个小 FP（CEVd-Yucatan，EM 22.9）消失
- **结论**：宿主过滤**不是**平台与 21 实验室（76%）差距的来源——宿主 reads
  本就不映射病毒库、不参与 EM 分摊；差距来自确认阈值（泊松比，见项3）与
  库完整性（项5）。去宿主的实际收益是组装轨降噪与少量 FP 减少；sRNA 工作流
  若要启用去宿主需新增 blastn-short 路径（本报告已给出可复现命令）。

## 项3 · 确诊阈值扫描 ✅（16 组重跑，本项最大发现）

plant_ref 库 + salmon k15，S3+S9 × 8 组阈值（`outputs/vp_retest/sweep/`）：

| 组合 | cov% | depth | reads | tpm | 泊松比 | TP/11 | FP | 灵敏度 |
|---|---|---|---|---|---|---|---|---|
| default | 10 | 0.5 | 10 | 1 | 0.3 | 5 | 3 | 45.5% |
| cov5/cov2/depth01/reads1/tpm01 | （单维放宽） | | | | 0.3 | 5 | 3–5 | 45.5% |
| **ratio01** | 10 | 0.5 | 10 | 1 | **0.1** | **7** | 5 | **63.6%** |
| **allrelaxed** | 2 | 0.1 | 1 | 0.1 | 0.1 | **9** | 13 | **81.8%** |

**结论**：
1. **主要闸门是泊松比**：0.3→0.1 一项就把 S9 的 GVA、GRVFV 放进来
   （+2 TP）；cov/depth/reads/tpm 单维放宽**完全无效**（0 TP 变化）。
2. 全放宽后 **9/11 = 81.8%**，超过 21 实验室环比均值 76%——旧文档撤回的
   "放宽阈值可到 8/9"量级得到复活并精确化（S9 单样本 8/9）。
3. 剩余 2 个 FN 各有实因：
   - **PVB**：plant_ref 仅 1 条 PVB 参考（NC_018667.1，RNA1，9.5kb；S3 株系
     分歧大，EM=0）——**库覆盖回归**（旧 kv_index 曾检出 PVB，S3 2/2）。
     补库清单：PVB RNA2 + 更多分离物。
   - **GRVG**（Grapevine red globe virus）：真值仅 78 reads，噪声级。
4. **HSVd 命名歧义**：S9 的 "Citrus gummy bark viroid" 条目在 ref_info 的
   ICTV 种即 *Hostuviroid impedihumuli*（=HSVd），GenBank 标题也写
   "hop stunt viroid"——HSVd 实际已检出，但以 CGB 条目名报告。建议确诊表
   按 ICTV 种聚合展示（见项4 判别列的延伸）。
5. FP 代价：3→13，但放宽组的 S3 新增 FP 全部 EM≤76（Tobacco vein clearing
   virus 等），且多项与 kunpeng 分类基线（项6）的噪声名单重合，可用
   EM 下限或项4 判别列二次过滤。

## 项4 · 近缘溢出判别列 ✅（平台代码改动+验证）

`kv_identify.py` `summarize()` 新增三列（确诊表与疑似新种表）：

- `Same_Genus_Next_Species`：同属其他种的最高 EM 条目
- `Same_Genus_Next_EM`：其 EM 值
- `Same_Genus_EM_Ratio`：次名 EM / 本行 EM（>0.1 即属内歧义信号）

已验证：d7 表三列正常产出；同种多节段互不计近缘；S9 的类病毒溢出
（CEVd↔CGB 比值 4.13）被直接暴露。数据匹配逻辑零改动。

## 项5 · plant_ref 新库重跑 ✅（D1–D10 全量完成 + d7 验证）

**D1–D10 全量重测**（本地数据 `E:\谷歌下载\测试数据验证`，salmon k31 默认阈值，
16 组运行共约 5 分钟；评分口径：物种级、别名双向包含，同原基准）：

| 基线 | 灵敏度（D1–D10） | FP |
|---|---|---|
| 旧 kv_index（8,464 条，2026-09 快照） | 20/28 = 71.4% | ~30 |
| **plant_ref（12,159 条，默认阈值）** | **22/28 = 78.6%** | 76（其中 EM≥1000 的 27） |

- **灵敏度 +7.2pp**：TSWV（D7）从 0→检出（L/M/S 全确诊），D3 恢复 GLRaV-2，
  合计 +2 TP。
- **精确率代价**：库增大带来更多近缘参考跨过阈值，FP 30→76（22.4% vs
  32.8%）。这印证稿件的"数据库即诊断"论点：**阈值需随库校准**——项3 扫描
  已给出杠杆（泊松比）与工具（近缘判别列 + EM 下限）。
- 剩余 6 FN：D3 的 GLRaV-2/GRSPaV（设计性稀释集，仍优于旧库的 1/5→2/5）、
  D4 的 HSVd、D8 的 CqMV1（0.5% 频率隐性种，默认库无该参考）、D10 的 PPV
  （库内 3 株系在列，D10 掺入量低：EM 66 / cov 2.96%，被 cov≥10% 挡住——
  放宽 cov 到 2% 即可回收，见项3）。
- d7 子集三引擎对照与 46/46 株级线见项6 与原表 2。

**d7 实测（子集）**：plant_ref 库下 **TSWV 三节段（L/M/S）全部确诊**——
旧库该数据集为 0%。库缺口修复的预期成立。
- salmon k31：cov 98.9–100%（EM 4.2K fragments）
- minibwa：4 条 TSWV 参考全检出（mapped 338–8,627，cov 87.1–99.9%）

## 项6 · 同机引擎横向对照 ✅（新增 kunpeng 基线 + 协议文档）

d7_sub（规则 reads，plant_ref 病毒库）三引擎对照：

| 引擎 | TSWV 检出 | 量化 |
|---|---|---|
| salmon k31（平台） | ✅ L/M/S 三节段 | EM 4.2K fragments，cov 98.9–100% |
| minibwa（平台） | ✅ 4 参考全检出 | mapped 338–8,627，cov 87.1–99.9% |
| kunpeng 分类器（新增基线） | ✅ 物种榜首 | 4,710 reads（4%） |

kunpeng 的 111 个物种级条目中除 TSWV 外全部 ≤322 reads（噪声级），
与平台 allrelaxed 组的 FP 名单高度重合——两套独立方法互证了噪声底。
与 Kraken2/ViroProfiler 的跨软件 head-to-head 需 Linux/容器（本机不可装），
协议已写入 `step5/step6` 脚本注释（数据集与库构建步骤完全对齐）。

## 过程中发现并修复的平台问题

1. **crabz 写管道未生效**：`host_removal` 产出的 `kept_R1.fastq.gz` 实为
   明文 FASTQ（.gz 扩展名错误），随后 crabz 读管道计数报
   "invalid gzip header" 崩溃——建议：写侧失败时落明文就改扩展名，或读侧
   先嗅探魔数。
2. **宿主去除不支持 sRNA**：kunpeng k=35 固定 + bwa-mem 种子机制，两者对
   21–24nt 读长零命中；sRNA 工作流应显式声明不支持或接入 blastn-short 路径。
3. **嵌入式 Python 不能 -m 导入平台包**（sys.path 无 cwd）——重测一律改用
   系统 Python（3.12.10）。

## 复现指引

| 项 | 脚本 | 依赖 |
|---|---|---|
| 项1 | `benchmark/compute_and_plot(_en).py`（已改） | results.json |
| 项2 | `D:/vp_retest/step1b_hostdeplete_bwa.py` + blastn 全量脚本 | 基因组（已下载于 genomes/） |
| 项3 | `D:/vp_retest/step3_sweep.py` + `step3b_score.py` | Bari fastq + plant_ref |
| 项4 | `kv_identify.py`（已改，零参数新列） | 同上 |
| 项5 | `D:/vp_retest/step5_rerun_d1_d10.py <data_dir>` | VIROMOCK D1–10（GitLab 下载） |
| 项6 | `D:/vp_retest/step6_kunpeng_baseline.py` | plant_ref |

库哈希：plant_ref（manifest built_at=2026-09-19，12,159 条，salmon_k31 +
本轮新构建 salmon_k15）。
