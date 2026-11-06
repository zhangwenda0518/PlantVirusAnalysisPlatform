# Seq-Prep · 参考序列获取

> 返回 [Home](Home) ｜ 上一页：[Cmpchain](Cmpchain) ｜ 下一页：[Cds-Export](Cds-Export)

把「要比较的那一批病毒序列」取到本地：ICTV 科属级联、accession 列表、Entrez 检索式、本机 `.gb` 四条路。产物分两套：带 CDS 注释的 GenBank 集合供建树与 CDS 提取，只要 FASTA 的参考集供样品流程 ⑦ 追加参考。

对应引擎 `Virus_Platform_Core/gb_collection.py`（GenBank 集合：下载 / 导入 / 清单 / 提取），FASTA 参考集一侧走 `ncbi_download.py`；工具卡 `t-seqprep`；无独立操作目录 id。

## 输入

### 输入格式

| 来源 | 入口 | 引擎 | 是否需联网 |
|---|---|---|---|
| ① ICTV 级联 | 卡内「① ICTV 递进选参」+ 🔍 预览选参 / ⬇ 下载 | `ictv_db.select_refs` + `gb_collection.download_gb_collection` | 本地优先（`plant_tree.db` / `gb_cache`），缺的走 NCBI |
| ② accession 列表 | 卡内「② accession 列表」 | `download_gb_collection(accessions=…)` | 是（efetch 直取） |
| ③ Entrez 检索式 | 卡内「② Entrez 检索式」 | `download_gb_collection(term=…)` | 是（esearch 会话 + 分批 efetch） |
| ④ 本机导入 | 卡内「③ 导入本机 .gb / .gbk（离线）」 | `import_local_gb` | 否 |

- ICTV 级联按**界 → 门 → 纲 → 目 → 科 → 属 → 种**逐级递进；`plant` 仅植物病毒，`ictv` 为全病毒界。
- 基因完整度两档：`complete` 只取完整基因组；不限完整度时取全部记录。计数 = accession 条数。
- accession 列表支持带版本号；Entrez 检索式交给 esearch 会话分批取。
- 本机导入支持 `.gz`，文件内多记录自动拆分。

### 参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `limit` | 200 | 最多条数 |
| `per_genus` / `per_species` | 0 | 每属 / 每种上限（0=不限），防大属淹没整科集合 |
| 集合名 | 自动生成 | 留空自动命名；白名单 = 中英文 / 数字 / `_` / `-`（≤64 字符），非法名当场报错（防路径注入） |

- 抽样顺序：先按每属 / 每种封顶，再截前 `limit` 条。
- 下载**幂等**：已存在的 accession 直接跳过（续传语义），重复跑不会重复落盘；`query.json` 每次按本次来源重写。
- FASTA 参考集（`ncbi-dl`）另收 Entrez 检索式与 `--db nucleotide|protein`，同样幂等续传。

## 流程

### ICTV 递进选参

- 选参预览**离线可用**；下载时先查本地 `plant_tree.db` 与 `gb_cache`，缺的记录才走 NCBI。
- 预览与下载是**同一批选参行**：预览里 `Source` 列标明该条来自 `plant_tree.db` / `gb_cache`（本地）还是 `ncbi`（需联网），据此判断这次下载要不要出网。
- 选参 → 每属 / 每种封顶 → 截前 `limit` 条 → 下载 → 写清单与来源快照。

### accession 列表与 Entrez 检索式

- accession 模式用 efetch 直取，支持带版本号，去重后落盘。
- 检索式模式走 esearch 会话 + 分批 efetch，适合按 `[ORGN]` / `[TITL]` 圈一批记录。
- 两条路都落到同一个 GenBank 集合目录，清单与 `query.json` 口径一致。

### 本机导入

- 收 `.gb` / `.gbk`，支持 `.gz`；单文件多记录自动拆成 `<acc>.gb` 单记录。
- 全程不出网，适合手上有现成 `.gb` 注释文件的场景。

### 集合管理与下游

- 卡内「🧬 GenBank 集合」列表（列：集合 / 记录数 / CDS 数 / 来源 / 日期 / 结果）每行两个动作：
  1. **🧬 提取**：集合级全量提取 → `extract/genome.fa` / `CDS.fa` / `PEP.fa` / `CDS/` / `PEP/` / `genes.tsv` / `marker/`；
  2. **🔍 巡检**：`inspect_collection` 重解析全部 `.gb`、重建清单与警告；列表下方按集合折叠展示巡检警告。
- 集合**建树**在 [Phylo](Phylo) 的 `t-treebuild` 卡（全基因组 / 挑选的序列集 / 已比对三种来源，`build_collection_phylo`），不在本卡。
- 集合级提取是**不做人工挑选**的全量版；要挑、要改名、要按基因归组导出，走 [Cds-Export](Cds-Export)。
- 想一条任务串起「取参考 → 提取 → 比对 → 建树 → SDT」，用 [Cmpchain](Cmpchain)；一键链的 `00_refs/` 就是本页产物的一枚快照。

### 下载与安全口径

- 域名白名单：仅 `eutils.ncbi.nlm.nih.gov`。
- 解析出的 IP 若落在私网 / 环回段直接阻断。
- 限速 + 指数退避重试。
- 文件读写经 `check_path` 限制在平台（含自定义输出根）之内。

## 产物

### 目录结构

```text
run/gb_collections/<集合名>/
├── <acc>.gb        每记录一个 GenBank 平文（保留 CDS / mat_peptide 注释）
├── collection.tsv  清单（accession / 物种 / 长度 / CDS 数 / 宿主 / 国家 / 日期…）
└── query.json      来源参数（检索式或 accession 列表 + 日期，供续传与溯源）
```

```text
run/ncbi_refs/<集合名>/
├── refs.fa         参考序列
├── refs_meta.tsv   序列元数据
└── query.json      来源参数（同样幂等续传）
```

### 关键文件与字段

| 文件 | 内容 |
|---|---|
| `<acc>.gb` | NCBI 平文**原样落盘**，CDS / mat_peptide 注释完整保留 |
| `collection.tsv` | 逐条清单：accession / 物种 / 长度 / CDS 数 / 宿主 / 国家 / 日期 |
| `query.json` | 本次检索式或 accession 列表 + 日期，供续传与溯源 |
| `refs.fa` / `refs_meta.tsv` | FASTA 参考集只留序列 + 元数据，无注释 |

- 关键差异：GenBank 集合能直接喂 CDS/PEP 提取与建树；FASTA 参考集没有注释，只用于建树与样品流程 ⑦ 追加参考。
- 两个根目录都跟着**输出目录**设置走：`gb_collections` 与 `ncbi_refs` 是 `DIRS` 的项，默认在 `run/` 下。

## 示例输入与输出

### 示例输入

```bat
python main.py gb-dl -n PotyvirusSet --term "Potyvirus[ORGN] AND complete genome[TITL]" --max 50
python main.py gb-dl -n MySet --acc "NC_001367, NC_002692"
python main.py gb-import -n MySet --files a.gb,b.gb
python main.py gb-list
python main.py gb-check -n PotyvirusSet

python main.py ncbi-dl "Tobamovirus[ORGN] AND complete genome[TITL]" -n TobamoRefs [--db nucleotide|protein] [--max 100]
python main.py ncbi-list
```

界面上等价的操作是：选库与基因完整度 → 🔍 预览选参 → ⬇ 下载。

### 示例输出

```text
run/gb_collections/MySet/
├── NC_001367.gb
├── NC_002692.gb
├── collection.tsv
└── query.json
```

`gb-list` 按集合列记录数、CDS 数与来源；`gb-check -n <集合>` 重解析全部 `.gb`，把警告打在对应集合名下。

## 界面与 CLI

### 界面

- 工具卡 `t-seqprep`「参考序列获取」分三段：
  1. ① ICTV 递进选参（参考库 / 基因完整度 / 最多条数 / 每属·每种上限）+ 🔍 预览（不下载）/ ⬇ 下载；
  2. ② accession 列表与 Entrez 检索式（二选一）+ ⬇ 下载 GenBank 集合；
  3. ③ 本机 `.gb/.gbk` 导入（含内置示例）。
- 底部两张清单：GenBank 集合、FASTA 参考集。

### CLI 与 agent

- 六个子命令（`main.py` 核实）：`gb-dl` / `gb-import` / `gb-list` / `gb-check` / `ncbi-dl` / `ncbi-list`。
- agent 侧无独立操作 id：本卡是纯前端 + 已注册 API，`agent-ops` 目录里不单列。批量取参考直接用上述 CLI 子命令。

## 相关

- 一键串起整组 → [Cmpchain](Cmpchain)
- 提取产物的挑选与导出 → [Cds-Export](Cds-Export)
- 公共数据检索（SRA/GSA、元数据）→ [Public-Data](Public-Data)
- 参考库 / ICTV 谱系表与更新 → [Databases](Databases)
