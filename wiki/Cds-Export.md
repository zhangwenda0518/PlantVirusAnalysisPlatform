# Cds-Export · CDS / PEP 提取产物

> 返回 [Home](Home) ｜ 上一页：[Seq-Prep](Seq-Prep) ｜ 下一页：[Align](Align)

把集合里**全部 CDS** 摊成可筛选的明细表，人工勾选并改名。再按基因名归组导出两套 FASTA：CDS（核酸）与 PEP（蛋白）。导出文件直接喂比对与建树。

对应引擎 `Virus_Platform_Core/cds_export.py`（明细表 / 挑选状态 / 按基因归组导出），解析基础设施复用 `gb_collection.py` 的集合布局与 `_gene_tag_of` / `_taxonomy_ranks`。独立页面 `/cds-export`（导航 `compare` 组）；无操作目录 id。

## 输入

### 输入格式

- 输入是一个 GenBank 集合目录 `run/gb_collections/<集合名>/`，目录里至少 1 个 `.gb`。
- 集合来源有四条路（ICTV / accession / 检索式 / 本机导入），见 [Seq-Prep](Seq-Prep)。
- 明细表每行字段（`build_cds_table`）：

| 字段 | 含义 |
|---|---|
| `rid` | 行级稳定 id = `<accession>#<特征序号>`，勾选与存盘都用它 |
| `acc` / `virus` / `family` / `genus` | 归属信息 |
| `gene` | 归一后的基因名 |
| `product` | GenBank 产物名 |
| `start` / `end` / `strand` | 坐标；一律 **1-based 闭区间**（与 GenBank 惯例一致），`strand` 为 `+` / `-` / `.` |
| `length` | CDS 长度 |
| `has_translation` | 为 False 则导出时按 `transl_table` 翻译 |
| `protein_id` | GenBank `protein_id` |

### 参数

- 基因名归一 `normalize_gene`：只留 `[A-Za-z0-9_\-.]`，其余替成 `_`，截断 40 字符，空则 `gene`。
- **改名就是归组**：同名即同组，导出时按 `gene` 分文件。
- 底表可选「显示假想蛋白」；不勾时假想蛋白不进列表。
- 挑选状态存 `selection.json`（`rid` → 基因名），供下次接着改。

## 流程

### 明细表解析与缓存

- 逐文件容错：单个坏 `.gb` 只记 WARN 并跳过，不让整个集合的产物变空。
- 明细表带缓存 `cds_table_cache.json`，键 = 集合 `.gb` 文件的「文件名 + 大小 + mtime」摘要。
- 缓存失效只在集合增删记录、重下集合、导入新 `.gb` 时发生；重开页面完全命中。
- 需要缓存的原因：一个科级集合可达数百 MB / 数千条记录，全量重解析要几十秒。

### 人工挑选与改名

- 按**科 / 属**下拉过滤，勾选需要的行，双击基因名改写。
- 改名后同名行并为一组：改名就是归组。
- 右侧汇总选中结果后「📤 导出」，同时可「存挑选状态」写 `selection.json`。

### 按基因归组导出

- 导出写 `extract/selected/`：`CDS.fa` / `PEP.fa` 是汇总，`CDS/<基因>.fa` / `PEP/<基因>.fa` 是按基因归组的单文件。
- 序列 id = `<accession>|<基因名>`。
- 翻译口径（`export_selected`）：有 `translation` 直接用；没有则按 `transl_table`（缺省 11）翻译。
- **先按 `codon_start`（1/2/3）切片再翻译**：CDS 起点不在首个完整密码子时，不切会整条蛋白移码。
- 选中的 `rid` 在集合里找不到（记录已变动）会汇总成 `missing`，并以 WARN 提示前 5 条，不静默吞掉。

### 两套提取的分工

| | 本页（`/cds-export`） | 集合级全量提取（`t-seqprep` → 🧬 提取） |
|---|---|---|
| 选序 | **人工**勾选 + 改名 | 全部 CDS，不挑 |
| 目录 | `extract/selected/` | `extract/` |
| 额外产物 | `selected.tsv`（选中明细） | `genes.tsv`（基因 × 基因组矩阵）+ `marker/`（保守基因 RdRp/CP/MP… 归组）+ `marker_report.tsv` |
| 典型用途 | 手工把同源基因对齐到统一命名后再比对 / 建树 | 一键链的 `01_extract/`、批量保守基因树 |

- 保守基因归组（`marker/`）走 `MARKER_GENES` 别名表：RdRp / CP / MP / TGBp1–3 / HC-Pro / CI / Helicase / Protease / VPg。
- 匹配规则：长别名优先、按词边界匹配（防 `capsid` 误吞 `nucleocapsid`）；同一记录同组多条取最长 PEP。

## 产物

### 目录结构

```text
run/gb_collections/<集合名>/
├── cds_table_cache.json     明细表缓存（GB 指纹失效）
├── selection.json           人工挑选状态（rid → 基因名）
└── extract/selected/
    ├── CDS.fa               全部选中 CDS 核酸（汇总）
    ├── PEP.fa               全部选中 CDS 蛋白（汇总）
    ├── CDS/<基因>.fa        按基因名归组的核酸
    ├── PEP/<基因>.fa        按基因名归组的蛋白
    └── selected.tsv         选中明细（rid / accession / virus / family / genus /
                             gene / product / cds_length / aa_length）
```

### 关键文件与字段

| 文件 | 内容 |
|---|---|
| `CDS.fa` / `PEP.fa` | 汇总 FASTA，序列 id = `<accession>\|<基因名>` |
| `CDS/<基因>.fa` / `PEP/<基因>.fa` | 按基因名归组，同源基因每基因组一条 |
| `selected.tsv` | 选中明细：`rid` / accession / virus / family / genus / gene / product / cds_length / aa_length |
| `selection.json` | 人工挑选状态，`rid` → 基因名 |
| `cds_table_cache.json` | 明细表缓存，键为 `.gb` 文件名 + 大小 + mtime |

- 集合级**全量**提取的产物在 `extract/` 下：`genome.fa` / `CDS.fa` / `PEP.fa` / `CDS/` / `PEP/` / `genes.tsv` / `marker/`（含 `marker_report.tsv`）。
- 一键链的 `01_extract/` 是同一套布局，见 [Cmpchain](Cmpchain)。

## 示例输入与输出

### 示例输入

```text
run/gb_collections/PotyvirusSet/     ← 打开展示页后选这个集合
├── NC_001367.gb                     ← 至少 1 个 .gb
├── NC_002692.gb
└── collection.tsv
```

页面操作顺序：选集合 → 🔬 解析并挑选 → 勾行、双击改基因名 → 📤 导出。

### 示例输出

```text
run/gb_collections/PotyvirusSet/extract/selected/
├── CDS.fa
├── PEP.fa
├── CDS/
│   ├── RdRp.fa
│   ├── CP.fa
│   └── …（每个归一名一个文件）
├── PEP/
│   ├── RdRp.fa
│   ├── CP.fa
│   └── …
└── selected.tsv
```

`selected.tsv` 沿 `rid` 逐行列出选中的 CDS；`CDS/<基因>.fa` 与 `PEP/<基因>.fa` 同名成对，可直接成对送 [Align](Align)。

## 界面与 CLI

### 界面

- 独立页 `/cds-export`：选集合 → 🔬 解析并挑选 → 按**科 / 属**下拉过滤、可勾「显示假想蛋白」→ 勾选行、双击基因名改写 → 右侧汇总后「📤 导出」。
- 页面接口：`/api/cds/table`（明细表）、`/api/cds/selection/save`（存挑选状态）、`/api/cds/export`（按基因归组导出）。
- 在 compare 组导航里它是**子页链接**（`{'href': '/cds-export'}`），不是工具卡，所以不进任务中心的工具卡列表；导出是同步动作（明细表与导出都在请求内完成）。

### CLI 与 agent

- `main.py` **没有 cds-export 子命令**（`add_parser` 核实）。
- 外部 agent 也没有对应操作 id；需要脚本化时直接调 `Virus_Platform_Core.cds_export` 的三个函数：`build_cds_table` / `save_selection` / `export_selected`。

## 相关

- 组导读 → [Compare](Compare)
- 集合从哪来（ICTV / accession / 检索式 / 本机导入）→ [Seq-Prep](Seq-Prep)
- 导出后接着比对 → [Align](Align)；建树 / SDT → [Phylo](Phylo)
- 一条链跑完 → [Cmpchain](Cmpchain)
