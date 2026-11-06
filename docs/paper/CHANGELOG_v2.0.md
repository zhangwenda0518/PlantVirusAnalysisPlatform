# CHANGELOG：manuscript_v1.2 → manuscript_v2.0

生成：2026-10-27。本文档记录 **整篇重写**（非补丁）的差异、每个头条数字的来源文件，以及三处仍需作者确认的口径。

---

## 1. 为什么重写而不是打补丁

v1.1/v1.2 的脊柱是从 git 历史恢复的 2026-09-16 基准；用户指出 `D:\桌面\文章写作\manuscripts\03_植物病毒分析平台\benchmark\` 才是 2026-10-19 定稿的第二代基准（四轮优化：口径校准 → 判定分层 → 精确率/工程 → 参考缺口修复）。以 v1.2 为底座逐条替换数字会留下"方法讲三层判定、结果讲两级"的混杂，因此按 10-19 定稿重写全文。

## 2. 结构对照

| v1.2（426 行） | v2.0（471 行） | 说明 |
|---|---|---|
| 标题："VirusPlatform: an integrated, offline-deployable web platform…" | "Which reference library and which decision tier decide discovery sensitivity: a benchmarked, offline desktop platform…" | 主命题换成"库完整性 + 判定层级决定灵敏度" |
| Abstract 按 工具/演示/基准 | Abstract 按 配置分离/库审计/三层判定/上界 | 全部重写 |
| §2.3 已知病毒鉴定 | 拆为 2.3.1 参考库 / 2.3.2 双引擎 / 2.3.3 三层判定 | 新增 |
| §2.13 基准设计 | §2.13 基准设计 **+ 库审计协议** | 新增逐 accession 审计、缺库日志、重建索引 |
| — | §2.14 统计 | 新增：纯精确计数，不给 p 值 |
| §3.1–3.5 | §3.1–3.8 | 拆出 3.3 库完整性、3.4 分级检测、3.5 reference-present 上界、3.6 Bari、3.7 吞吐 |
| Table 1–7 | Table 1–9 | 新增 Table 4（四轮优化）、Table 5（四层判定）、Table 6（逐集）、Table 7（reference-present 上界）、Table 8（Bari）、Table 9（吞吐） |
| References 1–57 | 1–57（未变） | 54 Tamisier 2021 / 55 Massart 2019 / 56 Dryad / 57 GitLab |

## 3. 数字 → 来源映射

| 论文中的数字 | 来源文件 |
|---|---|
| 库 12,161 → **12,196**；30 条缺失（D11×2/D12×2/D13×5/D14×5/D15×3/D16×4/D18×5） | `docs/paper/benchmark_20261019/BENCHMARK_FINAL.md` §四；`D:\vp_retest\gap_fix.log`（2026-10-19 01:10） |
| D17 五个 LChV-1 分离物、14% → 100% | `D:\vp_retest\step_d17_fix.py`、`d110_score_final.json` |
| D18 实际 reads 比组成表多 16.8%（46,917 vs 40,185） | `docs/paper/benchmark_20261019/BENCHMARK_FINAL.md` §8 数据审计 |
| confirmed **23/28（82.1%，precision 30.7%）**、8/8、合计 31/36（86.1%） | `docs/paper/benchmark_rerun_20261027/score.json`（2026-10-27 重跑，交付版 HEAD `964f0bf` + plant_ref 10-19 库） |
| review 档 **26/28（92.9%）**；GLRaV-2 2.73%/0.05×/0.314、GRVFV 6.7%/0.74×/0.088、HSVd 45.3%/1.52×/0.492 | 同上重跑的 `artifacts/*/identify/all_viruses.review.tsv`，匹配脚本 `D:\PVAP\work\_tier_check.py` |
| assembly rescue **27/28**（D3 371 bp contig、59.6×、94.5%） | `docs/paper/benchmark_20261019/BENCHMARK_FINAL.md` §四 + `D:\vp_retest\d3dis\` |
| **28/28**（D10 PPV，泊松 0.045 < 0.05，需 `--review-ratio 0.04`） | 同上 §四 + `results/contig_sweep.json` 口径 |
| **46/46（100%）**、101.8% 回收、MAE 0.00–1.15（salmon）/ 0.00–1.78（minibwa）、pooled 0.18 / 0.44 | `docs/paper/benchmark_20261019/BENCHMARK_FINAL.md` §三（**reference-present 配置，61 条 viromock_kv**） |
| D1 CTV 70.16/28.56/1.28 与 69.70/28.83/1.47 vs ILVO 69.79/29.18/1.03 / USDA 69.64/29.24/1.07 / 设计 69.75/29.22/1.03 | 同上 |
| 共识 100% identity、0 SNV、可呼叫宽度 99.5–99.9%、200 次模拟 | 同上 |
| Bari k15 6/11（54.5%）、两级 **11/11**、k31 0/11、组装+BLAST 4/11、S9 44.4%→33.3%、21 家均值 76%、r=0.968（n=17）、中位比 0.43、截距 −0.56 | `docs/paper/benchmark_20261019/BENCHMARK_FINAL.md` §4.2 与 `BENCHMARK_zh.md` |
| 18 集 **8.4 min**、D1 90.1 s/349.5 MB、D6 32.6 s/1,271.7 MB、0.23→0.35 GB | `docs/paper/benchmark_rerun_20261027/results.json`（2026-10-27，salmon） |
| alignment engine 302.4 s/1,276.0 MB（D1）、187.0 s/2,795.1 MB（D6）、30.4 min、3.1× 总比 | `docs/paper/benchmark_rerun_20261027/results.json`（2026-10-27 minibwa 18 集重跑） |
| alignment engine 物种级确认 20/28（71.4%）、D11–18 8/8 | 同上 + `score.py`（`compare.md` §6） |
| 20 样本链路 31/36（86.1%）、2.2 GB | `docs/paper/benchmark_20261019/results/chain_results.json` |
| 248 路由（35 页级 + 213 程序化） | `python tests/_route_inventory.py` 实测 251（含我本地未提交的 3 条 anchor 路由） |
| 发行包 2.90 GB、85 测试脚本、219 pytest | `D:\PVAP\PlantVirusAnalysisPlatform_with_Plugin` 实测；`tests/` |
| ~199,000 条流行病学记录 | 平台 `/api/` 实测（v1.2 §2.10 沿用） |

## 4. 待作者确认的三处口径（已在正文以括号或脚注披露，未擅自统一）

1. **FP 52 vs 76**：v2.0 用 52（我的 10-27 重跑，D1–10 确诊档、12,196 库）；10-19 定稿 `d110_score_final.json` 记 `conf_tp 23 / review 1 / fp 76`，未注明是否含 D11–18 或含 review 行。若作者确认 76 为更宽口径，需在 §3.3 加一句说明。
2. ~~alignment engine 的 discovery 数字仍是 9 月库~~ —— **已于 2026-10-27 解决**：minibwa 18 集对交付库重跑完成（30.4 min，全部 ok），Table 9 换成实测值、新增 Dataset 3 行、§3.4 增 "Engine comparison" 段（20/28 vs 23/28）、§4 Limitations 第四条改写。
3. **D3 CEVd-like 99.81% ANI / 41,132 reads** 仍只存在于 `viromock_benchmark_manuscript_EN.md` 的叙述里，10-19 结构化结果中无对应字段；v2.0 未引用该数字。

## 5. 已从 v1.2 删除的表述

- "8,464-reference shipped library" 作为头条库规模（仅保留为 2026-09 历史对照，见 §3.2）。
- "absence of a biological validation campaign" / "not claimed here"（已被 10-19 的 46 实验取代）。
- 旧 20/28（71.4%）作为 headline（改为 23/28 确诊 + 26/28 两级）。
- 二次重跑归档作为独立章节（内容并入 §3.3 与 Availability）。

## 6. 校验

- 行数 474、词数 13,789（2026-10-27 第三轮，含 engine comparison 段与实测吞吐）。
- 9 张表格列数一致；参考文献 1–57 连续；所有 Section/Table/Figure 交叉引用可解析。
- 残留扫描：无 `1.54`、`not claimed here`、`absence of a biological`。

## 7. 2026-10-27 的两处工具修复（记录在 archive，不影响论文口径）

- `docs/paper/benchmark_rerun_20261027/rerun.py`：`--force` 会丢历史记录（`done` 仅在非 force 时载入），已改为总是载入 `existing`；补跑 15 集 salmon 后重评，23/28 与 8/8 复现一致。
- `docs/paper/benchmark_rerun_20261027/score.py`：`VOID = {'vm_d5_bwa'}` 原为无条件作废，改为按记录判定（`wall_s < 5 s`）；本次 minibwa D5 正常完成，计入后 D1–10 为 20/28（原 19/27 口径作废）。归档旧结果的 35/35 自证不受影响。
