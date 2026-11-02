# manuscript_v1.1 — 变更记录

**基线：** `manuscript_final.md`（v1.0，2026-09-15，340 行）
**本版：** `manuscript_v1.1.md`（2026-10-27，约 422 行 / 12,100 词）
**目的：** 把已完成的 VIROMOCK（株级 + 物种级）与 Bari 小 RNA 两套基准合入平台稿，消除"尚无生物学验证"的自相矛盾表述，并把每个数字的来源写清。

每一节都给出**改动位置**、**改动内容**、**数字来源**（便于审稿或自查时逐条回查）。

---

## 0. 前置事实（必须先记住，正文的定调都基于此）

| 项 | 事实 | 证据位置 |
|---|---|---|
| `docs/paper/` 曾在工作区被删 | 提交 `597c3c2`（发布前 P0 批量修复）删除整棵树；`4c72f50` 内容完好 | `git log --diff-filter=D -- docs/paper` |
| 本版来源 | `git restore --source=597c3c2^ --worktree -- docs/paper`（82 文件 / 7.23 MB） | `git status` 显示 `?? docs/paper/` |
| 平台稿标题 | "…an integrated, offline-deployable web platform for **end-to-end plant virus detection, quantification, and evolutionary analysis** on desktop workstations" | v1.0 §Title |
| 基准跑的引擎 | v1.0 引擎 = 本稿描述的引擎；基准起跑日 2026-09-16（平台稿定稿次日） | `docs/paper/benchmark/BENCHMARK_zh.md` 头部 |

---

## 1. 文件头

- 增加 v1.1 版本块：说明本版并入 `docs/paper/benchmark/` 与 `viromock_benchmark_manuscript_EN.md` 的结果，变更逐条记录于本文件。
- `**Software version:**` 行补一句：基准结果来自 v1.0 引擎与 §2.13 / §3.3–3.4 所述参考库。

## 2. Abstract

| 段 | 改动 |
|---|---|
| Results | 原以 `…ships with the distribution.` 结束。**追加一整段**基准数字（18 数据集；默认库物种级 20/28 = 71.4%；参考全在库 46/46 标为 upper bound，对照实验室 86.7%；D1 CTV 三株与 ILVO / USDA-APHIS 差 ≤ 0.7 pp；纯净混合 MAE 0.18 pp；共识 100% identity / 0 SNV / 99.5–99.9% 可呼叫宽度；3.4 min vs 26.5 min、≤0.64 vs ≤1.95 GB；Bari 小 RNA 6/11 = 54.5% 对 21 家实验室均值 76%）。 |
| Conclusions | 删除 `Independent biological benchmarking … principal outstanding evaluation step`，改为"capacity 已被 inter-laboratory panels 检验；小 RNA 是设计边界；田间 RT-PCR/qPCR 一致性是剩余 gate"。 |

**数字来源**

- `docs/paper/benchmark/results/results.json`（逐实验判定）
- `docs/paper/benchmark/BENCHMARK_zh.md`（20/28、8/8、6/11、MAE、r=0.968 等正文口径）
- `docs/paper/viromock_benchmark_manuscript_EN.md`（46/46、39 分离物 MAE、D1 CTV 行、吞吐 3.4 / 26.5 min、0.64 / 1.95 GB）
- `docs/paper/benchmark/results/perf.csv`（D1/D3/D6 逐行耗时与峰值内存）
- `docs/paper/benchmark/results/chain_results.json`（端到端 23/28；peak_rss_mb = 2201.6）

## 3. §1 Introduction

- 贡献列表**新增第 7 条**：public benchmark（VIROMOCK + Bari），two reference configurations，Tables 4–7，Sections 2.13 / 3.3–3.4。
- 贡献段后的引导句：原 `…absence … of an independent biological validation campaign` 删除；改为"report … the independent benchmark"，并明确"与田间诊断金标准的一致性尚未建立"。

## 4. §2.13（新增）

标题：**Benchmark design**（插在 §2.12 之后、`## 3. Results` 之前）。内容四段：

1. 两套面板的来源与规模（VIROMOCK 18 数据集 / 13.6 GB 压缩 / 113 条实验室记录，98 条达标 = 86.7%；Bari 两个 250,000 read 小 RNA 库 + 21 家实验室 mapping counts）。
2. **两种参考配置的定义**——discovery（出厂 8,464 库）vs reference-present（`viromock_kv` 61 条）——并写明后者"按构造是 upper bound"，是全文最重要的口径约定。
3. 指标定义：检测按靶标；定量为"同种参考池内份额对设计份额"的绝对偏差（MAE, pp）；共识为"回贴真值参考后 callable breadth（depth ≥ 1× 且 Q20）/ identity / SNV"；时间与峰值内存 1 Hz 采样。
4. harness（`tests/bench/`）与归档（`docs/paper/benchmark/`）位置；"阈值全部默认，未在基准上调参"。

## 5. §3.2（原 §3.2 Worked demonstrations）

- 标题去掉 `and reproducible benchmarks`（基准已独立成节）。
- 原"Section 3 reports implementation-level evidence only … not claimed here"整段替换：说明 3.2 是工程测量，诊断性能见 3.3–3.4，田间 RT-PCR/qPCR 一致性见 §4 Limitations。

## 6. §3.3（新增）Benchmark against the VIROMOCK challenge

七段正文 + 四张表。**口径决策**（正文已明写，勿再改）：

1. headline 用 **discovery 配置 20/28 (71.4%)**，因为这是"打开出厂平台"的真实召回；precision 32.8%；minibwa 19/27（D5 一次运行 0.9 s / 2 MB 提前退出，记录作废）。D11–18：salmon 8/8、minibwa 7/8（漏 D17）。
2. **46/46 明标 reference-present upper bound**，与实验室 86.7% 并列呈现；同时写明 D6 人工新株、PiVB 8 段、CqMV1（~29×，0.5%）、D4 GYSVd-2 的恢复情况。
3. 两个"官方列表之外"的发现单列：D3 的 CEVd-like 类病毒（99.81% ANI，41,132 reads；葡萄是 CEVd 天然寄主）；GRVFV 极端稀释（52 reads，76% breadth）过不了定量门槛 → 界定 read-based 方法的下界，引出 assembly 路线。
4. 定量：39 分离物 MAE 0.00–1.15 / 0.00–1.54 pp；剔除 D5 后 pooled ≈0.18 / ≈0.44 pp；D1 CTV 三株逐株对比（我们 / ILVO / USDA-APHIS / 设计值）；D5 定性为"信息论上限而非缺陷"（背景株 ~99% 同源，实验室之间也不一致）。
5. 共识：39 基因组 + D4 类病毒 100% identity / 0 SNV / 99.5–99.9% breadth；1.03% 低频株 149 SNV、breadth 99.6%；天然分离物与参考的差异按生物学解释；D7 TSWV L 缺失段 760–7060（321× vs 10,216×，3.1% 阶跃）；人工株自比 95.6%/99.84%、97.4%/99.73% → 全新株需 assembly 路线。
6. 鲁棒性与**我们主动报告的缺陷**：200 次无错模拟 100% 一致；但小 RNA 零映射时共识返回全 N 且不报错（无 zero-mapping guard）——"报告而不是隐藏"，且本稿无任何结论依赖它。
7. 吞吐（Table 7）与端到端链（23/28 = 82.1%、31/36 = 86.1%、2.2 GB、无失败）；结论：**salmon 为默认、minibwa 为可计数审计路径**，把"双引擎"从设计选择变成有数据支持的决策。

表：

- **Table 4** 检测（dispatch 配置 × 数据集 × 引擎 × 实验室帧）
- **Table 5** 定量 MAE（含 D5 的 61.95 / 51.43，明确标为 confounding）
- **Table 6** 共识重建（四层：纯净高质量 / 有同源背景 / 低频株 / 人工株自比）
- **Table 7** 吞吐与峰值内存

**数字来源**：`benchmark/results/results.json`、`perf.csv`、`cons_results.json`、`BENCHMARK_zh.md`（2026-10-03 修订版）、`viromock_benchmark_manuscript_EN.md`。

## 7. §3.4（新增）Benchmark against the Bari small-RNA panel

- 6/11 = 54.5%（6 TP / 4 FP / 5 FN；两类库合计 precision 60%）；组装 + BLAST（60 bp 门槛）4/11 = 36.4%；k31 对照 0/11；minibwa 因 50 bp read 下限拒跑。
- 深度效应：S9 250K → 50K，44.4% → 33.3%。
- 与 21 家实验室均值 76% 对照时明确"量级比较，非同口径"（他们有宿主过滤、逐研究阈值与专家复核）。
- 定量：r = 0.968（n = 17）但中位 obs/exp = 0.43、log–log 截距 −0.56；**同时报 r 与截距**，并解释单位差（TPM 按 PE reads 归一 vs 参考按 viral reads）——"r 只表征方向与强度，不表征绝对水平"。

## 8. §3.5（原 §3.3）

`Quality assurance and distribution footprint`：仅改小节号，内容未动。

## 9. §4 Discussion

| 段落 | 改动 |
|---|---|
| **Positioning**（段末） | 补：双引擎是"基准直接裁决的设计决策"（约 1/8 时间、约 1/3 内存 → salmon 默认，minibwa 留作可计数审计）；并补一句"平台类论文很少报告这种粒度的共享真值，我们把配置显式化当作贡献的一部分"。 |
| **Design trade-offs**（Third 段末） | 补一句：§3.3 的基准跑的就是这些 shipped defaults，因此数字描述的是"交付态"而非调参态。 |
| **Limitations** | **整段重写为四条边界**：(1) 这是 inter-laboratory benchmark，不是田间诊断研究——无 RT-PCR/qPCR 田间一致性，LOD 未测；(2) 两配置不可混谈——46/46 是 upper bound，20/28 才是"出厂态"数字，已发表摘要须写明配置；(3) 灵敏度受库覆盖约束（D7 是库快照缺口），阈值/ANI 仍是筛查启发式；(4) 基准自身可复现性不完整——harness 与库可重建，但株级/共识套件的原始 run 目录未归档，数字靠重跑而非靠查存档；本稿把 archive 一起投出去以保持缺口可见。原有 ONT 与 AI 元数据风险两条保留在段末。 |
| **Future work** | 原"独立生物学验证研究"改为：① 田间 RT-PCR/qPCR 一致性研究；② **用当前库重跑两套面板 + 归档每次 run（输入 digest / 库指纹 / 产物 hash）**；③ 其余（ONT、时间校准、server mode、ICTV 月度对账、可用性评估）保留。 |

## 10. §5 Conclusion

末句 `independent biological validation is the identified gate …` 改为：基准把检测与定量放到与参与实验室同一水平线上，并显式标注配置；**田间 RT-PCR/qPCR 一致性是 implementation 与 diagnostic use 之间的识别 gate**。

## 11. Figure legends

Figure 4 之后新增 **Supplementary figures** 段：10 张基准附图（S1–S10，PNG + SVG）位于 `docs/paper/benchmark/figs/`，不嵌入正文。

## 12. Availability and requirements

- `Source-code availability`：写明代码含 analysis core / web shell / test suite / `tests/bench/` harness，准备投公开仓库 + Zenodo DOI。
- 新增 `Benchmark data and materials`：数据集 [56,57]、Bari 面板 [55]、归档 `docs/paper/benchmark/`、`viromock_kv`（61 条，manifest 2026-09-15）。
- 新增 `Evaluation environment`：v1.0 引擎、19 线程、1 Hz 采样。

## 13. References

原 53 条不变；**新增 54–57**（已用 Crossref / HTTP 实查核过作者、卷期、页码、DOI）：

| 号 | 文献 | 核实情况 |
|---|---|---|
| 54 | Tamisier L et al. Semi-artificial datasets as a resource for validation of bioinformatics pipelines for plant virus detection. Peer Community Journal. 2021;1:**e53**. doi:10.24072/pcjournal.62 | Crossref 确认。**注意：原 EN 稿写的是 e38，系错误**。 |
| 55 | Massart S et al. Virus detection by high-throughput sequencing of small RNAs… Phytopathology. 2019;109(3):488-497. doi:10.1094/PHYTO-02-18-0067-R | Crossref 确认（28 位作者全列）。 |
| 56 | Tamisier L et al. VIROMOCK challenge datasets [data set]. Dryad. doi:10.5061/dryad.0zpc866z8 | **DOI 未验证成功**（HEAD 请求 SSL 连接失败）；投稿前需手工在浏览器确认 DOI 可解析。 |
| 57 | ILVO. VIROMOCK challenge: datasets, truth tables, and scoring rules. GitLab. https://gitlab.com/ilvo/VIROMOCKchallenge | HEAD 200 OK（公开可访问）。 |

## 14. 本版**没有**做的事（有意为之）

1. 没有写"样品锚 / ledger"作为论文卖点——v1.0 发布版不含该功能，写进去是不可辩护的声明。它留在 §4 Future work 的"归档输入 digest / 库指纹 / 产物 hash"里，作为可复现性的实现手段，而不是独立贡献。
2. 没有把株级 46/46 当作 headline，也没有删掉它——降级为 "reference-present upper bound" 并解释其有效性边界。
3. 没有修掉"sRNA 零映射返回全 N"这个缺陷（属代码改动），只在 §3.3 如实登记。
4. 没有重跑基准（属 B 路线，见下）。

## 15. 复核清单（已执行）

- `Select-String` 全文检查：无 `absence of a biological validation campaign` / `not claimed here` / `implementation-level evidence only` 等自相矛盾残留。
- 交叉引用：§2.13、§3.2–3.5、Tables 1–7 均为连续编号；Table 4–7 只在 §3.3 出现；Table 1–3 仍在 §3.1。
- 统计：422 行 / 88,950 字符 / 约 12,146 词（v1.0：340 行 / 67,038 字符）。
- 参考文献使用：54–57 在正文均被引用（§2.13 ×4、§3.3、§3.4 ×2、§4 Limitations、Availability）。

## 16. 待作者核实的数字瑕疵（本版**未**擅自改数）

1. **D16 minibwa MAE 与表中区间不一致。** `viromock_benchmark_manuscript_EN.md` Table 1 给 D11–18 的区间是 `0.00–1.15 / 0.00–1.54`，但同稿正文列 per-dataset 值时写 D16 minibwa **1.78**（>1.54）。v1.1 的 Table 5 与 §3.3 正文采用**表值 0.00–1.54**（表是归档产物）。投稿前建议用 `tests/bench/compare_engines.py` + 设计份额重算一遍，二者取其一并同步三处（Table 5、§3.3、Abstract 都不含该区间，Abstract 只用了 pooled 0.18）。

2. **Dryad DOI（[56]）未验证。** `10.5061/dryad.0zpc866z8` 未能通过脚本解析（SSL）；VIROMOCK 数据的正式归档 DOI 需在浏览器手工确认，若不符则改用论文中的数据可得性声明原文。

3. **D3 的 CEVd-like 类病毒**在 v1.0 原始记录里出现过，但完整 ANI 数值（99.81%）与 read 数（41,132）来自 `viromock_benchmark_manuscript_EN.md` 的叙述而非结构化结果文件；若审稿要求逐条溯源，需要回到 `_bench_kvs2/D3` 的 `kv_confirmed` 表核对（该目录当时未归档，见 §4 Limitations 第四条）。

4. **Bari 面板的 21 家实验室均值 76%** 来自 `BENCHMARK_zh.md` 的叙述，原始出处应为 Massart 2019（[55]）的汇总统计；投稿前请对照该文正文确认是"mean detection rate 76% @250K reads"还是"最高/中位"，措辞需与其一致。

## 17. 下一步（需要作者拍板）

- **C 合规包**：license 选择、`v1.0.0` tag、公开 GitHub 仓库、Zenodo DOI、作者信息与单位（现为占位）。
- **B 重跑归档**：用当前 `plant_ref`（2026-09-19，已含 TSWV L/M/S）重跑 VIROMOCK 两配置 + 共识套件，归档 run 目录；同时可把"样品锚"作为归档元数据落地（输入 sha256 / 库指纹 / 产物 hash）。
- **期刊形态**：平台 + 基准合并投 BMC Bioinformatics / Plant Methods / Phytopathology Research / Virology Journal；若投 Bioinformatics Application Note 需把 Tables 4–7 全转入 supplement（2 页放不下）。
