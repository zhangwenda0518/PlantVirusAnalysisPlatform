# Kvsuite-Intro · 病毒定量与共识组导读

> 返回 [Home](Home) ｜ 上一页：[Identify](Identify) ｜ 下一页：[Kvchain](Kvchain)

病毒定量与共识组共 4 个模块，本页做导览，模块细节见各专页。本组回答「样品里有哪些**已知**病毒、各有多少 reads、共识长什么样、变异有哪些」——全部对参考库比对，按 read 层定量口径。

## 模块清单

1. [Kvchain](Kvchain) · ⚡ 一键分析 —— 鉴定 → 过滤 → 共识 → 深度绘图 → 变异注释一次跑完，产物收拢到汇总目录。
2. [Kvsuite](Kvsuite) · 已知病毒识别与定量 —— 对病毒参考库比对/映射（salmon 或 minibwa），出逐参考鉴定表 + 过滤定量表 + BAM/深度 + 病毒 reads。
3. [Consensus](Consensus) · 共识序列分析 —— 比对到参考构建共识序列（管线 ③c 同引擎），出 `consensus.fa` 与逐参考位点计数。
4. [Variant](Variant) · 病毒变异分析 —— BAM/VCF 直入 → bcftools caller（可选）→ SnpEff 注释 → 变异图族 + SNPGenie 进化指标。

## 链路

⚡ 一键（Kvchain）或分步 **鉴定**（Kvsuite）→ **共识**（Consensus）→ **变异**（Variant）。

输入是「样本处理」组产出的 FASTQ / 去宿主 reads；判读规则见 [Result-Interpretation](Result-Interpretation)（known/novel 阈值、过滤口径）。
