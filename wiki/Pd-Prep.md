# Pd-Prep · 进化动力学数据接入

> 返回 [Home](Home) ｜ 上一页：[Phylodyn](Phylodyn) ｜ 下一页：[Rtt](Rtt)

把「比对 FASTA + 时间地点元数据」变成**标准化数据集**并当场体检。链路是导入校验 → 治理 → 去重 → 比对 QC → 坐标表 → 时空降采样 → 概览。产物路径可直接「接力 A0」回填给 [Phylodyn](Phylodyn)。

对应引擎 `Virus_Platform_Core/phylodyn_kit.py`（序列/元数据工具箱 + 数据接入）、`phylodyn_govern.py`（元数据治理）、`phylodyn_alignqc.py`（比对 QC）；工具卡 `t-pdprep`；操作目录 id `pdprep`。

## 输入

### 输入格式

三种来源，`source` 三选一：

| 来源 | 输入 | 说明 |
|---|---|---|
| `files` | 序列 FASTA + 元数据 CSV/TSV | 两份文件直喂；explorer 导出即为两份文件 |
| `manual` | 粘贴 FASTA + 粘贴元数据表 | 时间地点同检 |
| `genbank` | accession / Entrez 检索式 / 物种或 taxid / 本地 `gb_files` / 既有 GenBank 集合（`collection`） | 在线下载或本地 GB |

- `genbank` 可先 `mode=preview` 只出**清单**（`pdprep/preview.json`，含每条记录日期 + 地点是否齐全），先看再勾选导入。
- `genbank` 可选「在线库圈定」（`gateway=1`）：自建库只当**圈定器**（按物种 / taxid 圈 accession），正文与完整特征表仍走 Entrez efetch。
- 元数据规范列是 `name` / `date` / `location`，可加 `lat` / `lon` 直接画逐样本点位。

### 参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `source` | `manual` | `files` / `manual` / `genbank` |
| `mode` | 空 | `preview` = 只检索出清单不导入（写 `pdprep/preview.json`） |
| `fasta` / `meta` | — | `source=files` 必填 |
| `fasta_text` / `meta_text` | — | `source=manual` 必填 |
| `accessions` / `term` / `species` / `taxid` / `gb_files` / `collection` | — | `source=genbank` 至少给一个 |
| `full_only` | 关 | 仅完整基因组 |
| `date_from` / `date_to` | 空 | 日期区间 |
| `max_records` | 200 | 最多条数 |
| `strict` / `govern` / `allow_noloc` | 关 / 开 / 关 | 严格校验 / 治理规则 / 允许无定位 |
| `date_mode` | `mid` | 小数年折算口径 |
| `dedup` | 关 | 三元组去重 |
| `qc` | `off` | `off` / `qc` / `align_qc`（先比对） |
| `qc_max_gap` / `qc_max_n` / `qc_min_len` | 0.10 / 0.05 / 0.90 | QC 阈值 |
| `coords` | 空 | 区域坐标表 |
| `sub` / `sub_mode` / `sub_n` / `sub_region` / `sub_seed` | 关 / `random` / 50 / 空 / 0 | 时空降采样 |

## 流程

### 数据接入与 preview

- `files`：两份文件直喂，`import_from_files` 读入后即做时间地点同检。
- `manual`：`import_manual` 接收粘贴的 FASTA 与元数据表，同样做同检。
- `genbank`：`import_from_genbank` 按 accession / 检索式 / 物种 / taxid / 本地文件 / 既有集合取记录；`mode=preview` 时只写清单 `pdprep/preview.json`。
- 分组（SeqGrouper）属导入 / 收集阶段的职责：在线收集时自动跑，不单独成卡。

### 元数据治理

- **phylodyn_govern**：时间 + 地理元数据归一 / 标注 / 体检，原则是**不猜、不静默、不改语义**。
- 平台统一日期口径为 `YYYY` / `YYYY-MM` / `YYYY-MM-DD`；小数年在入数据集时折算为 ISO。
- 治理针对四类「静默出错」：
  1. 坐标占位符（`XX.XX N XXX.XX E`）；
  2. 日期占位符（字面量 `YYYY-MM-DD`，任何格式校验都会放行）；
  3. 地理层级 `Unknown`（静默降级成国家质心，误差上千公里）；
  4. 只给年，或 `DD-Mon-YYYY`（这类写法会被格式校验判不合格并剔行）。
- 三条原则写死在模块里：
  - **不猜**：解析不出就标 `issue` 并置空；
  - **不静默**：任何降精度 / 丢弃都进报告，可统计条数；
  - **不改语义**：默认只清空字段不丢行，丢行必须显式开开关。
- 小数年口径**唯一权威**在 `phylodyn_govern.decimal_year`：`mode='mid'`（默认，只给年时取 6 月 15 日）/ `mode='start'`（年初）。
- 单一实现的理由：同一份 `2013` 若各处自行换算，能得出 2013.0 与 2013.5366 两个答案；对以年为尺度的定年，这就是「同一份数据两个答案」。
- 首词重名不覆盖原有序列：第二条起改名 `名字.2` / `名字.3`，重名明细可查。否则序列条数凭空变少、剔除集对不上。

### 比对 QC

- **phylodyn_alignqc**：检查长度 / gap% / 简并字符% / identity，产出干净集 + 剔除集 + 报告。
- 阈值默认 = 上游 `align_qc` 口径，**全部可配**并写进报告：gap ≤10%、N ≤5%、长度 ≥参考 90%、identity 跌幅 ≤2%。
- identity 默认对**多数票共有序列**算（没给参考也能跑）；给了 `reference` 才按上游口径对该参考算。
- 与 `align_qc.py`（重组预筛，见 [Rdp](Rdp)）的分工：
  - `phylodyn_alignqc` 面向「A0 的等长比对前置」，能给**是否等长**与长度分布，可自己跑 MAFFT（复用 `phylo._run_mafft`，含中文路径 ASCII 中转与迭代法降级）；
  - `align_qc.py` 面向「RDP5 前的片段剔除」，只吃已比对 FASTA。
- QC 把**全部**序列都剔光时当场报错，并说清最常见原因（这批序列本身不同源、或阈值太严），不让空数据集带着往下跑。

### 预处理链

导入之后依次：

1. **元数据治理**（默认开；已在 import 内完成，必须早于校验，否则 `DD-Mon-YYYY` 会在校验阶段先被剔掉）。
2. **三元组去重**（可选，`dedup=1`）：按 `(date, location, sequence)` 剔完全重复记录，FASTA 与元数据同步过滤。
3. **比对 QC**（可选，`qc=off|qc|align_qc`）：`align_qc` 会先 MAFFT 比对再 QC；有剔除时**后续步骤与交付数据集都只含干净集**。
4. **区域坐标表**（可选，`coords`）：并进元数据让地图有真落点；未落点区划逐条告警。
5. **时空降采样**（可选，`sub=1`，**有损**）：另出 `sequences.subsampled.fasta` + `metadata.subsampled.csv`。
6. **概览出图**（自动）：按年直方 / 按地点计数 / 地图点位。

- 降采样三模式（`subsample_fasta`）：
  - `equal`：按**最小区划**条数取样，可能大幅缩水；
  - `region`：指定区划剔除；
  - `random`：随机抽 N 条，带 seed。
- 没有区划信息的样本无法参与「分区」口径，会被显式计数排除并告警。

### 序列/元数据工具箱（phylodyn_kit）

| 能力 | 说明 |
|---|---|
| SeqIDRenamer | 按 TSV 映射表批量重命名 FASTA 序列 ID |
| SeqGrouper | GenBank 记录表 + 分组映射表 → Group 列 / 分组计数 |
| **GeoSubsampler** | FASTA 时空降采样：均等采样 / 定区剔除 / 随机抽取（控制采样偏倚） |
| VirSpaceTime | Temporal.txt / Spatial.txt 解析 → 前端 plotly 绘图（时间轴 / 地点分布 / 地图点位） |
| 数据接入 | 三来源（explorer 导出 / 手动输入 / 在线下载）+ 时间地点同检 + 标准化数据集 |

- 对应函数：`rename_sequences` / `apply_grouping` + `load_group_mapping` / `subsample_fasta` / `parse_temporal` + `parse_spatial` + `attach_coords` / `import_from_files` + `import_manual` + `import_from_genbank`。
- 卡里的形态：SeqIDRenamer 已并入 `t-phylodyn` 的「🏷 重命名映射」（A0 之前 FASTA / 元数据 / 树 tip 三处同改）。

## 产物

### 目录结构

```text
run/tool_runs/<pdprep_run>/pdprep/
├── sequences.fasta        标准化序列（QC 后 = 干净集）
├── metadata.csv           标准五列 name / date / location / lat / lon（有 host 才追加 host）
├── align_qc/              align_qc_report.tsv（可含 alignment.mafft.fasta）
├── sequences.subsampled.fasta / metadata.subsampled.csv     （勾了降采样才有）
├── preview.json           （mode=preview：检索清单）
└── summary.json           治理 / 去重 / QC / 坐标 / 降采样各段结果 + warnings +
                           abs（绝对路径，供「接力 A0」一键回填）+ 概览统计
```

### 关键文件与字段

| 文件 / 字段 | 内容 |
|---|---|
| `sequences.fasta` | 标准化序列；有剔除时只含干净集 |
| `metadata.csv` | 标准五列 `name` / `date` / `location` / `lat` / `lon`；有 `host` 才追加 |
| `align_qc/align_qc_report.tsv` | 逐条判据与结论；可选 `alignment.mafft.fasta` |
| `preview.json` | `mode=preview` 的检索清单，含每条记录日期 + 地点是否齐全 |
| `summary.json` → `abs.seq` / `abs.meta` | 回填给 [Phylodyn](Phylodyn) A0 的两个输入路径 |
| `summary.json` → `abs.sub_seq` / `abs.sub_meta` | 降采样版本的回填路径 |
| `summary.json` → `meta_pick` | 日期列名择优结果 |

- 日期列名会被自动识别并**择优**（`Collection_Date` ↔ `Release_Date`、`Geo_Location` ↔ `Country` 等），选了什么记在 `meta_pick`。

## 示例输入与输出

### 示例输入

已比对 FASTA（`input`）+ 元数据 CSV（规范列 `name` / `date` / `location`，可带 `lat` / `lon`；日期接受 `YYYY`、`YYYY-MM`、`YYYY-MM-DD` 或小数年）：

```csv
name,date,location,region,year
SIM_Asia_1|Asia|2001,2001,Asia,Asia,2001
SIM_Asia_2|Asia|2003,2003,Asia,Asia,2003
SIM_Europe_1|Europe|2006,2006,Europe,Europe,2006
SIM_Americas_1|Americas|2010,2010,Americas,Americas,2010
```

```text
>SIM_Asia_1|Asia|2001
ATGCGTACGTACGTACGTACGTACGTACGTACGTACGT
>SIM_Europe_1|Europe|2006
ATGCGTACGTACGTACGTACGTACGTACGTACGTACGT
```

### 示例输出

`pdprep/` 标准化数据集（结构与字段见「产物」节）：QC 后的干净序列 + 标准五列元数据。结果面板同时给出数据集概览（按年直方 / 地点计数 / 地图点位，前端绘图，不单独落盘）。

```text
run/tool_runs/pdprep_20261105_093000_ab12cd34/pdprep/
├── sequences.fasta          QC 后干净集（不剔行时 = 全集）
├── metadata.csv             name / date / location / lat / lon 五列
├── align_qc/                勾 qc=align_qc 才有：align_qc_report.tsv (+ alignment.mafft.fasta)
└── summary.json             治理 / 去重 / QC / 坐标 / 降采样各段统计 + warnings + 接力路径
```

- `summary.json` 的 `abs.seq` / `abs.meta` 直接「接力 A0」回填给 [Phylodyn](Phylodyn)。
- 剔除条数、日期降精度明细与重名改名记录都以 `summary.json` 为准，不靠日志猜。

## 界面与 CLI

### 界面

- 工具卡 `t-pdprep`「进化动力学数据接入」：来源切换（files / manual / genbank）→ 预处理勾选项（治理 / 去重 / QC / 坐标表 / 降采样）→ 运行。
- 结果区给数据集概览（时间轴 / 地点分布 / 地图）与 `issues` 明细；摘要给绝对路径，供「接力 A0」。

### CLI 与 agent

- `main.py` **没有 pdprep 子命令**（`add_parser` 核实）。
- 外部 agent 走操作目录 id `pdprep`：`python main.py agent-ops --id pdprep --schema` 查参数，MCP `vp_run_tool` 执行。
- 接上后续阶段：一键链 [Phylodyn](Phylodyn)；时间信号判读 [Rtt](Rtt)；重组预筛 [Rdp](Rdp)。

## 相关

- 组导读 → [Phylodynamics](Phylodynamics)
- 一键全链 → [Phylodyn](Phylodyn)
- 时间信号与定年 → [Rtt](Rtt)
- 重组前的预筛与掩蔽 → [Rdp](Rdp)
