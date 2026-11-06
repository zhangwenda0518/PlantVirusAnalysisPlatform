# Rdp · RDP5 重组分析

> 返回 [Home](Home) ｜ 上一页：[Rtt](Rtt) ｜ 下一页：[Phylogeography](Phylogeography)

进 RDP5 前先剔掉不完整 / 低质量序列，再用 **RDP5 九方法**检出重组事件（断点 + 亲本 + 逐方法 p 值）。还可导出**掩蔽重组区后的干净比对**，送去建树与定年。

对应引擎 `Virus_Platform_Core/rdp5_engine.py`（RDP5CL 调用 + 事件解析 + 掩蔽），预筛走 `align_qc.py`；工具卡 `t-rdp`；操作目录 id `rdp`。

## 输入

### 输入格式

- `input`：**比对好的** FASTA（MAFFT 产物），序列等长。
- 预筛后不少于 **4 条**（RDP5 官方下限 `MIN_SEQS`）。
- 可选 `orf_csv`：Rgrapher 风格 CSV（`Gene.symbol,Start,Stop`）或 GenBank 文件（直接取 CDS 特征），供断点分布图画基因轨道。
- 长度不一致直接 `ValueError`，并提示先用 [Align](Align) 的「序列比对」卡。

### 参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `input` | 必填 | **比对好的** FASTA（MAFFT 产物） |
| `qc` | 开 | 预筛开关（`qc=0` 只用于排障） |
| `qc_ref` | 空 | 预筛参考序列 ID（子串匹配）；给了才启用 identity 判据 |
| `qc_min_len` | 0.90 | 最小覆盖长度比例（夹到 0.5–1.0） |
| `orf_csv` | 空 | ORF 注释 CSV 或 GenBank 文件（可选，供断点分布图基因轨道） |

## 流程

### 序列预筛（align_qc）

- 对齐参照管道同一套判据、同一组文件名（`align_qc_report.tsv` / `clean.fasta` / `removed.fasta`）。
- 判据（默认值与参照管道一致，且可配）：
  - `len_ratio` = 去 gap 长度 / 参考长度 < **0.90** → 不完整片段；
  - `gap_ratio` = gap 数 / 比对列数 > **0.10** → 比对质量差；
  - `n_ratio` = N 数 / 比对列数 > **0.05** → 低质量；
  - `identity` = 与参考的成对 identity（SDT 公式，双方非 gap 才计入）低于 `(1 − 0.02) × 100` = **98%** → 群体外离群。
- 参考序列：显式给 ID（子串匹配）则用它；否则取**去 gap 长度中位数**那条（与参照管道同）。
- ⚠️ identity 一栏**只在显式参考命中时才参与判定**。98% 这条线是给「同一变异株 / 同一次爆发」的近等序列用的；拿它去卡一个病毒种的全部株系会误杀（实测 CMV RNA3 十条跨亚组序列会被剔掉 7/10）。
- 预筛后不足 **4 条**当场报错并给建议（调低最小长度比 / 指定参考序列 / 补更多完整序列），不静默带着不够的序列往下跑。
- 为什么必须先预筛：RDP5 检测的是同源等长序列间的重组。片段混进来会让全比对出现大片 gap，断点坐标与 p 值都被带偏；片段本身是缺失数据，任何重组信号都不可信。
- 与 `phylodyn_alignqc`（[Pd-Prep](Pd-Prep)）的差别：本模块**只吃已比对 FASTA**（平台上 RDP5 与建树都要求已比对输入，不做比对）；参照管道自带 MAFFT。

### RDP5 调用与事件解析

- 集成 **RDP5 原生 Windows 命令行版**（`RDP5CL.exe`）：RDP5 本体是 Windows 程序，平台直接原生调用（服务器版反而要 Wine）。
- 调用形态对齐服务器封装：`RDP5CL.exe -f <input.fasta> -ofp <prefix> -ds -rbdp`。
- 输入先复制进 RDP5 目录、`cd` 到该目录运行（RDP5CL 把产物写在自己所在目录），结束后把 `<prefix>*` 产物拷回运行目录。
- 发行版已在 `3rd/tools/rdp5/`（含 `3seqTable`、`clustalw2`、`FastTree` 等伴生工具）。
- 九方法 p 值列（紧跟 recombinant / minor / major 之后）：`RDP` / `GENECONV` / `Bootscan` / `Maxchi` / `Chimaera` / `SiScan` / `PhylPro` / `LARD` / `3Seq`。
- 事件表里 `methods` 只留 **p < 0.05** 的方法（按 p 升序）；`method_p` 给逐方法 p 值，`pvalue` 取最小值，`n_methods` = 显著方法数。
- 亲本推断：`minor_parent` / `major_parent`，值为 `Unknown` 时解析括注里的候选名（真实病毒数据里 Unknown 极常见）。

### 断点分布与基因轨道

- 断点分布兜底：`-rbdp` 只统计**双亲本都已鉴定**的事件。主表事件只要带 Unknown 亲本，`<prefix> breakpoint distribution.csv` 会静默全 0、`…BreakpointPositions.csv` 为空或缺失。
- `breakpoint_distribution_degenerate()` 检出该退化，`synthesize_breakpoint_distribution()` 用事件断点重建 200nt 滑窗分布（格式与原产 CSV 兼容）；摘要里 `note` 注明「由平台重建」。
- 基因轨道（可选）：`orf_csv` 收 Rgrapher 风格 CSV 或 GenBank；平台经参考行的 gap 轮廓把参考坐标映射到比对坐标。
- 产物 `gene_map.json` 供断点分布图画基因轨道。坐标全映射不上时，摘要 `note` 明确说明「应按 `qc_ref` 参考序列给 GenBank 坐标」。

### 掩蔽与唯一引擎

- **掩蔽**（吸收 `mask_recombination.py`）：检出事件的重叠区间合并后，把比对对应列掩蔽为 `N`，产出可进建树 / BEAST 的「干净比对」。
- 这就是参考管线「重组必须在建树前」的语义。
- 掩蔽坐标基于**RDP5 实际吃到的那份比对**（预筛后的 `clean.fasta`）；摘要里 `input` 记它、`source_input` 另存原始输入备查。
- **为什么只有 RDP5**：自研三序列法（MaxChi / Chimaera / Bootscan）在合成嵌合数据（6 条 1kb，真重组体 + 真断点 500）上 18 报 1 真。
- 自研法失效的原因：Bootscan 分支只看 support 不看 p、start/end 取三方法断点极值而非片段区间、全组合扫描无多重校正。与 RDP5 并存只会给出假阳性事件表，故 RDP5 为唯一引擎。

### 并发与取消

- 并发安全：进程内 `_RDP5_LOCK` 串行化（RDP5CL 产物落共享目录，并发会互删互覆）+ 每次调用独立前缀 `rdp5_<uuid8>_`。
- 回拷只搬本前缀文件，去掉 uuid 段后仍是 `rdp5*` 命名。
- cancel / 超时不会因 RDP5CL 长时间静默而失效：stdout 交给独立读取线程，主线程 0.5s 轮询排队并照常检查取消与 deadline，命中立刻 kill。

## 产物

### 目录结构

```text
run/tool_runs/<rdp_run>/rdp/
├── rdp5.csv                         RDP5 主结果表
├── rdp5 breakpoint distribution.csv 断点分布（必要时由平台按事件重建）
├── rdp5*BreakpointPositions*.csv    断点位置
├── events.json                      前端事件表数据源（含 note / gene_map）
├── events.tsv                       扁平事件表：recombinant / major / minor /
│                                    start / end / n_methods / methods / best_pvalue
├── summary.json                     摘要（n_seqs / aln_len / variable / n_events /
│                                    qc / input / source_input / gene_map / note）
├── align_qc_report.tsv              预筛逐条判据与结论（KEEP / REMOVE + 理由）
├── clean.fasta                      通过预筛的序列（= RDP5 实际输入）
├── removed.fasta                    被剔除的序列
└── gene_map.json                    （给了 orf_csv 才有）基因 → 比对坐标
```

### 关键文件与字段

| 文件 | 内容 |
|---|---|
| `rdp5.csv` | RDP5 主结果表，含九方法 p 值列 |
| `events.tsv` | 扁平事件表；`methods` 只列 p < 0.05 的方法 |
| `events.json` | 前端事件表数据源，含 `note` / `gene_map` |
| `summary.json` | 条数 / 比对长 / 变异位点数 / 事件数 / 预筛口径 / 输入来源 / 备注 |
| `align_qc_report.tsv` | 逐条判据与结论（KEEP / REMOVE + 理由） |
| `clean.fasta` / `removed.fasta` | 通过预筛 / 被剔除的序列 |
| `gene_map.json` | 基因 → 比对坐标（给了 `orf_csv` 才有） |

- `summary.json` 的 `note` 会在两种情况下给出说明：断点分布由平台重建、基因坐标映射不上。

## 示例输入与输出

### 示例输入

输入是一份已比对 FASTA（例如 [Align](Align) 跑出的 `aln.trim.fasta`），序列等长；预筛后需 ≥4 条。需要基因轨道时再给一份 ORF 注释：

```csv
Gene.symbol,Start,Stop
CP,1,750
RdRp,800,2400
```

### 示例输出

```text
run/tool_runs/rdp_20261105_101500_ab12cd34/rdp/
├── rdp5.csv · rdp5 breakpoint distribution.csv · rdp5*BreakpointPositions*.csv
├── events.tsv · events.json · summary.json
├── align_qc_report.tsv · clean.fasta · removed.fasta
└── gene_map.json
```

- 先看 `align_qc_report.tsv`：REMOVE 的行会写明理由；不足 4 条时任务会直接失败。
- 再看 `events.tsv`：`n_methods` 越大、`best_pvalue` 越小，事件越可信。
- 断点分布全 0 属于亲本 Unknown 时的已知退化，`summary.json` 的 `note` 会标注「由平台重建」。

## 界面与 CLI

### 界面

- 工具卡 `t-rdp`「RDP5 重组分析」：输入比对 FASTA + 预筛开关 / 参考序列 / 长度比 + 可选基因注释 → 运行。
- 结果区给事件表（逐方法 p 值）、断点分布（含基因轨道）、可下载 `events.tsv` 与掩蔽后的干净比对（`/api/tool/rdp_masked`）。

### CLI 与 agent

- `main.py` **没有 rdp 子命令**（`add_parser` 核实）。
- 外部 agent 走操作目录 id `rdp`：`python main.py agent-ops --id rdp --schema` 查参数，MCP `vp_run_tool` 执行。
- 在组里的位置：重组会污染定年与迁移信号，标准顺序是「比对 → 预筛 → 掩蔽 → 建树 / 定年」，见 [Phylodyn](Phylodyn) / [Rtt](Rtt)。

## 相关

- 组导读 → [Phylodynamics](Phylodynamics)
- 掩蔽后建树 / 定年 → [Phylodyn](Phylodyn) / [Rtt](Rtt)
- 比对产物从哪来 → [Align](Align)（比较基因组组）；系统地理主链路 → [Phylogeography](Phylogeography)
- 上游元数据与等长比对 QC → [Pd-Prep](Pd-Prep)
