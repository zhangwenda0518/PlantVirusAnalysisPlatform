# 投稿合规清单（C 线，2026-10-27）

> 状态标记：`[x]` 已具备 / `[ ]` 待办 / `[?]` 需作者本人决定或提供信息。
> 本清单只覆盖"能不能安全投出去"的硬门槛，不评价学术水平。

---

## 1. 代码与许可（软件侧）

| 项 | 状态 | 证据 / 落地位置 |
|---|---|---|
| 公开仓库 | `[x]` | `https://github.com/zhangwenda0518/PlantVirusAnalysisPlatform`（2026-10-27 实测 HTTP 200，公开可访问） |
| 版本 tag | `[x]` | 远端已有 `v1.1.0`、`v1.2.0`；`v1.0.0` **只存在于本地**，未推送 |
| 根 LICENSE | `[x]`（2026-10-27 新增） | `LICENSE`：MIT，覆盖本项目自有源码；**3rd/ 与 databases/ 明确排除在外** |
| 第三方组件索引 | `[x]` | `THIRD_PARTY_NOTICES.md`（33 个工具 + 129 个 Python 包，由 `dev_tools/third_party_notices.py` 生成） |
| CITATION.cff | `[x]`（2026-10-27 新增） | 版本 1.2.0；ORCID/affiliation 留 TODO |
| .zenodo.json | `[x]`（2026-10-27 新增） | 供 Zenodo GitHub 集成在打 release 时读取 |
| Zenodo DOI（软件） | `[ ]` | 需作者用 ORCID 登录 Zenodo → Settings → GitHub 关联本仓库 → 打新 release 自动 mint DOI |
| Zenodo DOI（基准归档） | `[ ]` | `docs/paper/benchmark_rerun_2026*/` 应作为**独立 dataset 记录**上传（含 manifest.json 库指纹） |

### 1.1 第三方二进制再分发（这条最容易在审稿/法务环节被卡）

`3rd/` 是**纳管进 git 的**（`.gitignore` 第 26–47 行：2026-09-28 起全量纳管，仅排除 RDP5 的 105 MB `3seqTable` 等少数文件）。也就是说公开仓库本身在分发第三方二进制：

- 许可类型混杂：SPAdes(GPLv2)、IQ-TREE(GPLv2)、salmon(GPLv3)、ViennaRNA(自定义)、samtools(Expat/MIT)、minibwa(MIT)、fastp(MIT)、BLAST(public domain/NCBI)、JRE( GPLv2+CE )、Strawberry Perl(GPL/Artistic) 等。
- 义务：copyleft 组件需**提供对应源码的获取方式**（上游 tag/链接即可满足"书面报价"式义务，但必须写明）。
- 待办 `[x]`（2026-10-27 完成）：在 `THIRD_PARTY_NOTICES.md` 增加第 4 节 **"Source code offer"**：逐个 copyleft 工具（SPAdes、JRE、Strawberry Perl、SnpGenie、srna-target、FastTree、IQ-TREE、RAxML-NG、salmon、Diamond、trimAl、pandepth、aria2c、muscle；RDP5 单列）列出证据文件与"未修改上游二进制 + 书面源码提供承诺"。若将来打补丁（例如 `KNOWN: LoVis4u 运行器修补 Windows 兼容 bug`），须在该节逐条补上补丁与补丁后源码。
- 待办 `[ ]`：确认 `3rd/tools/rdp5/3seqTable` 通过 Release（tag `external-tools`）分发的做法在论文"Code availability"里有一句话说明。

---

## 2. 稿件（当前 v1.2）

| 项 | 状态 | 说明 |
|---|---|---|
| 稿件文件 | `[x]` | **`docs/paper/manuscript_v2.0.md`**（471 行 / 13,449 词，2026-10-27 按 10-19 定稿整篇重写；v1.1 423 行、v1.2 426 行保留作对照） |
| 变更记录 | `[x]` | `docs/paper/CHANGELOG_v2.0.md`（整篇重写说明 + 数字→来源文件映射表 + 3 处待确认口径）、`CHANGELOG_v1.2.md`（v1.2 的补丁记录） |
| 基准并入正文 | `[x]` | §2.13 设计+库审计、§3.3 库完整性、§3.4 分级检测、§3.5 reference-present 上界、§3.6 Bari、§3.7 吞吐、Tables 4–9 |
| 数字可复核 | `[x]` | `docs/paper/benchmark_rerun_20261027/`：salmon 18/18 重跑完成，`score.py` 与归档 CSV 逐行 35/35 一致 |
| 作者/单位/通讯 | `[x]` | 单作者 **Wenda Zhang**，Peking Union Medical College, Chinese Academy of Medical Sciences, Beijing, China；通讯邮箱与 ORCID 待补（2026-10-27 作者确认） |
| 作者贡献（CRediT） | `[x]` | 单作者 CRediT 全项已写入稿内（2026-10-27） |
| 基金 | `[?]` | 有无资助项目号 |
| 利益冲突声明 | `[x]` | 稿内已写 "The author declares ... no potential conflict of interest" |
| 数据可用性 | `[x]` | 稿内 Availability and requirements 已列仓库/tag/两个归档目录/数据集 DOI；软件 DOI 待 Zenodo mint |
| 代码可用性 | `[x]` | 同上，含 LICENSE(MIT) 与运行环境；仓库 https://github.com/zhangwenda0518/PlantVirusAnalysisPlatform |
| AI 使用声明 | `[?]` | 若写作过程用过 LLM 辅助，按目标刊政策声明；平台内置的可选 AI 引导功能也应在 Methods 说明其默认关闭 |
| 图件规格 | `[ ]` | 目标刊一般要求 300 dpi TIFF/EPS；现有图为 `docs/paper/benchmark/figs/*.png|svg`，需按刊转格式 |
| 参考文献核验 | 基本完成 | [54] Tamisier 2021 *Peer Community Journal* 1:e53（EN 稿误写 e38，已改）；[55] Massart 2019 Phytopathology 109:488-497, doi:10.1094/PHYTO-02-18-0067-R；[56] Dryad `doi:10.5061/dryad.0zpc866z8`（DataCite 实测：15 位创建者、2021、14,614,047,052 bytes、CC0-1.0）；[57] GitLab VIROMOCK 仓库 HTTP 200 |
| 待核实数字 | 剩 2 条 | ①D16 MAE → 已定 **1.78 pp**（原正文 1.54 是陈旧值）；②Dryad DOI → 已验证；③D3 CEVd-like 99.81%/41,132 reads 仍仅存于 EN 稿叙述；④Bari 21 家均值 76% 口径待对 Massart 2019 原文 |
| 未重跑部分 | 已知缺口 | minibwa 引擎 discovery 配置（≈2 h）与 reference-present 配置未重跑；Table 4 相应单元格带 †，§4 Limitations 第四条已写明 |

---

## 3. 数据与基准

| 项 | 状态 | 说明 |
|---|---|---|
| VIROMOCK 原始数据 | `[x]` 本地 | `E:\谷歌下载\测试数据验证`（13.6 GB 压缩 / ~70 GB 解压）——**不在仓库内**，需 DOI 化归档或引用上游 |
| Bari 小RNA 数据 | 需确认 | 上游为 Massart 2019 配套数据；仓库内只有 `Sample_3.fastq`/`Sample_9.fastq` 的处理结果，原始文件未入库 |
| 重跑归档 | `[x]` | `docs/paper/benchmark_rerun_20261027/`：`rerun.py` / `score.py` / `manifest.json`（库指纹）/ `results.json`（含每集输入 sha256）/ `score.json` / `compare.md` / `README.md` / `artifacts/<exp>/`（确诊表、quant.sf、refstats、日志）；salmon 18 集 8.4 min 跑完，结果已并入 v1.2 |
| 旧快照库 | 已无法恢复 | `databases/virusref_db/kv_index` 已删；本轮用当前交付库 `plant_ref`（12,196 条参考，含 TSWV）重跑 |

---

## 4. 目标刊硬门槛（投前逐条打勾）

建议按"平台+基准"合并稿投 **Plant Methods / BMC Bioinformatics / Virology Journal / Phytopathology Research**；若投 Bioinformatics 只能是 Application Note（约 2 页、≤4 图），当前 46 实验 + 7 表放不下，需大幅压缩。

- `[ ]` 目标刊最新 Author Guidelines（**注意：bioinformatics.oup.com 与 gigascience 的 instructions 页 2026-10-27 直连 403，需用浏览器/机构网络再核对一次字数与图数上限**）
- `[ ]` 摘要字数、正文字数、图表数、参考文献数上限
- `[ ]` 是否要求结构化摘要、Key points/Highlights
- `[ ]` 是否强制数据可用性单独小节、是否要求 Zenodo/Figshare DOI
- `[ ]` Cover letter（卖点：完全离线可部署 + 端到端 15 阶段 + 两套公开基准 + 诚实报告精度瓶颈）
- `[ ]` 推荐审稿人 3–5 位（植物病毒检测/生信方向，避免利益冲突）

---

## 5. 交付前最后一遍自检命令

```powershell
cd 'D:\桌面\植物病毒分析平台'
python -m pytest tests -q                    # 代码侧回归（当前 219 passed）
python tests/_check_anchor.py                # 样品锚自检
python docs\paper\benchmark_rerun_20261027\score.py   # 基准评分自检（35/35 行一致）
git status --short                           # 确认没有把 run/ databases/ 误提交
```

---

## 6. 需要作者本人决策/提供的信息（一次性问清）

1. **LICENSE 版权人写法**：已定为 `Copyright (c) 2026 Wenda Zhang`（MIT），2026-10-27 作者确认。
2. **作者列表**：已定为单作者 Wenda Zhang / PUMC-CAMS；**仍需**通讯邮箱与 ORCID（CITATION.cff 与 .zenodo.json 里留了 TODO）。
3. **基金与致谢**：有无项目号。
4. **是否现在推送**：本地 `main` 与远端同步于 `964f0bf`，但本轮产生了大量**未提交**内容——样品锚（`anchor.py`、`anchor_api.py`、`app.js/css/i18n`、`pipeline.py`、两个自检脚本、`tests/_route_baseline.json`、`docs/DEVELOPMENT_NOTES.md`）、恢复的 `docs/paper/`（含 v1.1/v1.2/CHANGELOG/基准归档/投稿清单）、`LICENSE`/`CITATION.cff`/`.zenodo.json`/`THIRD_PARTY_NOTICES.md` 第 4 节、`run_one.py` 修复。建议分三次提交：①样品锚功能 ②基准重跑归档+v1.2 稿 ③合规文件；`v1.0.0` tag 仍未推送。
