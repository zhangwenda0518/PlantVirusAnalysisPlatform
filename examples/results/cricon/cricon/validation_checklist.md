# 联合检测设计验证清单（L1-L5）

目标：example_acmv.fasta
设计时间：2026-11-05 02:13:02
routes：both；crispr 最优评分：79.3；conan 候选数：20

> 证据边界（ACS Sensors 2026 综述口径，双路线同一口径）：本结果为序列级候选优先级排序，不构成 crRNA 活性、错配区分度、扩增兼容性或基质性能的证据；实验结论须按 cricon/validation_checklist.md 的 L1-L5 逐级建立。

## L1 纯化靶标
- [ ] 合成含 protospacer 的 dsDNA（或体外转录 RNA），梯度稀释测 aLoD（cas12a/cas13a 路线）
- [ ] time-to-positive：记录不同拷贝数下荧光/侧流出峰时间
- [ ] crRNA 活性初筛：与 1-2 个备选 crRNA 并排比较
- [ ] 容错 PAM（如有）：确认非 TTTV PAM 位点的实际活性
- [ ] （CONAN）Cascade-crRNA 复合体结合 + Cas3 降解活性（ATP 依赖）：阳性靶标出信号

## L2 加标基质
- [ ] 健康植株粗提液（纸片提取 / 碱裂解两种前处理各一版）加标梯度
- [ ] 测回收率与抑制情况（与纯化靶标 aLoD 对比）
- [ ] 近缘交叉反应：近缘病毒核酸加标，验证不检出（对应 conan 负组判据与 crispr 近缘库筛查）
- [ ] 宿主核酸背景：健康植株提取液不检出（对应 crispr 宿主筛查；CONAN 路线宿主筛查未做，必须在此补验）
- [ ] （CONAN）RT-LAMP 产物与 Cascade/Cas3 反应兼容性（稀释/纯化/直接加样三方案）

## L3 回顾性田间样本
- [ ] 用平台历史阳性样品（02b_kvsuite 定量结果召回清单）盲测
- [ ] 以 RT-qPCR 为对照，算一致性（敏感度/特异度/kappa）
- [ ] 低滴度样本单独统计（Ct>30 段）
- [ ] （CONAN）真实近缘病毒样本（非仅序列）测试近缘不检出

## L4 前瞻性田间试验
- [ ] 多地点 / 多品种 / 不同生育期
- [ ] 操作者为非开发人员；记录单样 <20min 出结果比例
- [ ] 现场温度（非恒温实验室条件）下复测

## L5 实际使用
- [ ] 田间站 / 检测点实际人员操作
- [ ] 储存稳定性：37 C 加速老化 + 常温运输模拟
- [ ] 批次一致性（>=3 个独立合成批次）
- [ ] 单样有效结果成本核算
- [ ] 结果的处置决策记录（诊断结论如何指导防控）

## 双路线互证（cricon 特有）
- [ ] 同批样本两路线平行检测，统计一致性（阳性符合率 / 阴性符合率 / kappa）
- [ ] 收敛位点（见 summary.json convergence）优先入组：两算法同位点独立命中为增强证据，非活性保证

引用：Wang H. et al. ACS Sensors 2026（框架）；Low S.J. et al. Commun Biol 2025 PathoGD（覆盖口径）；CASPER（评分权重）；Yoshimi et al. 2022（CONAN Cas3）；CONAN-SWIFT bioRxiv 2026.08.25.746999。
