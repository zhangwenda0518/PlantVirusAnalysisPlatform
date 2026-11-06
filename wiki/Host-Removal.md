# Host-Removal · 非宿主序列提取

> 返回 [Home](Home) ｜ 上一页：[Convert](Convert) ｜ 下一页：[Identify](Identify)

用宿主单一物种库给 reads 分类，C 行（判为宿主）整对剔除，留下非宿主 reads 作为组装/定量的干净背景。
对应引擎 `Virus_Platform_Core/host_removal.py`（`remove_host` / `filter_host_fastq`）+ `kunpeng.py`（`classify`）；页面 `/hostremoval`（导航「样本处理」组），操作目录 id `hostremoval`。

## 输入

### 输入格式

- 原始/质控后 reads（自动衔接 ⓪/⓪b 产物）；单端只处理 R1。
- 可预转换 FASTA（`classify_r1` / `classify_r2`）作为分类输入，省质量行解压；kept reads 仍从 FASTQ 提取。
- 宿主库：`host-db/<TaxID>_<源目录名>_host_db/`（多库共存，`platform.json` 的 `active_host_db` 指定当前库，数据库页一键切换）。

**宿主库构建要点**（见 [Databases](Databases)）：

- 需要 NCBI TaxID（建库前自动校验：不存在/已被合并直接提示，不白跑）。
- FASTA 头任意格式均可（自动清理并注入 `|kraken:taxid|N` 标签）。
- **大基因组自动切片**：每条序列切 ≤1MB 片段、相邻重叠 34bp（k-1）保留跨切口 k-mer——全量实测 k-mer 数与整条建库完全一致（286,327,790），2GB 内存即可完成 1.8GB 基因组建库（~1.5 分钟）。
- 建库为替换式：重试自动清理旧 library，不累积重复数据。
- `--clean-mid` 建库后清理中间文件（library / prep / taxonomy，省 3-4GB/库，重建时自动再生成）。

### 参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `r1` | 必填 | R1 FASTQ(.gz)；单端只给 R1 |
| `r2` | 可选 | 双端时给；给了就按 `-P` 成对分类与成对过滤 |
| `confidence` | 页面 0.2 / 引擎 0 | kunpeng `-T` 分类置信度，越大越严格 |
| `db` | 平台主宿主库 | 宿主库目录；缺省取 `config.databases['host']`（受 `active_host_db` 影响） |
| `classify_r1` / `classify_r2` | 可选 | 预转换 FASTA，分类阶段直接读 |
| `threads` | 自动 | 分类与 seqkit 过滤线程 |
| `min_hit_groups` | 2 | kunpeng `-g`：至少 N 个分类组命中才判 C（内部参数） |

## 流程

### 步骤一 库校验与分类

1. `db_ready(db)` 先校验库（`hash_*.k2d` + `opts/taxo/hash_config.k2d`），不齐直接 400，提示去「数据库构建」页。
2. kunpeng classify 输出 `output_1.txt`（逐 read 分类）与 `output_1.kreport2`。
3. 日志会打印本次宿主库身份（物种 / TaxID）；库的 `seqid2taxid.map` 含多 taxid 或无 `host_db.json` 清单时给 WARN。

### 步骤二 判定宿主 read 集合

- 原理：宿主库只含宿主单一物种，因此 classify 输出中 C 行（已分类）即宿主 read 对，U 行保留。
- C 行 read ID 经 `collect_host_read_ids` 归一化（去 `/1` `/2`、Illumina 尾缀）后成为剔除集合。

### 步骤三 过滤与断点复用

1. 过滤优先 `seqkit grep -v` 多线程反选（`filter_mode=seqkit`），异常回退纯 Python 流式（`filter_mode=python`）；双端同步遍历，R1/R2 条数不一致或 FASTQ 记录截断直接报错。
2. 断点复用前核对分类输入指纹、`confidence` / `min_hit_groups` 与宿主库路径；换库/换输入会重跑，不沿用旧 `stats.json`。

## 产物

### 目录结构

```text
run/results/<样品>/01_host_removal/
├── kept_R1.fastq.gz
├── kept_R2.fastq.gz      # 双端才有
├── stats.json
├── host.kreport2
└── .host_removal.done
```

### 关键文件与字段

| 文件 | 内容 |
|---|---|
| `kept_R1/R2.fastq.gz` | 宿主去除后的非宿主 reads |
| `stats.json` | 宿主占比统计与本次参数、库身份 |
| `host.kreport2` | 分类报告 |
| `.host_removal.done` | 断点标记 |

- `stats.json` 关键字段：`single_end`、`input_r1` / `input_r2`、`classify_inputs`（真实分类输入的文件指纹）、`classify_params`、`kraken_out` / `kreport`、`host_db`（name / path / taxid / species / has_manifest）、`total_pairs` / `kept_pairs` / `dropped_pairs` / `filter_mode`、`host_ratio`。
- `kept_pairs == 0` 时写 `ok=false` 与 `error` 并抛错（不落 `.done`，避免下游吃空输入）。

### 解读

- 宿主占比 = `dropped_pairs / total_pairs`；一条都没去掉通常是宿主库与样品宿主不匹配（纯化病毒样品也会如此），日志只告警不报错。
- 结果异常时先核对 `stats.json.host_db`：用错库（例如用细菌库处理植物样品）会静默产生错误的 kept reads。

## 示例输入与输出

### 示例输入

质控后的双端 FASTQ，加一个已建好的宿主库：

```text
run/results/<样品>/00_prep/
├── fastp_R1.fastq.gz
└── fastp_R2.fastq.gz

host-db/<TaxID>_<源目录名>_host_db/
├── hash_*.k2d
└── opts/taxo/hash_config.k2d
```

### 示例输出

- `01_host_removal/kept_R1.fastq.gz`、`kept_R2.fastq.gz`：剔除宿主后的 reads。
- `stats.json` 给出 `total_pairs` / `kept_pairs` / `dropped_pairs` / `host_ratio`；`host.kreport2` 供分类判读。

## 界面与 CLI

### 界面

- 页面 `/hostremoval`：输入类型（双端/单端）、单样本 / 目录批量、分类置信度、线程、宿主库目录、输出目录 → 运行；输出区列 kept FASTQ 与统计。

### CLI 与 agent

- 管线阶段：`python main.py analyze --r1 ... --r2 ... --sample S1 --stages host`。
- 外部 agent：操作目录 id `hostremoval`（`python main.py agent-ops --id hostremoval --schema`，MCP `vp_run_tool`）。
- 宿主库构建：`python main.py build-host-db --genome host-db\genome.fa --taxid 4081 [--threads 8] [--clean-mid] [--rebuild]`。

## 相关

- 宿主库详情与 TaxID 校验 → [Databases](Databases)
- 互补运算（样本 ∩ 病毒库）→ [Identify](Identify)
- kept reads 的去向 → [Assembly](Assembly)（组装）· [Kvsuite](Kvsuite)（定量）
