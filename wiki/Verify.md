# Verify · 候选序列验证

> 返回 [Home](Home) ｜ 上一页：[Contigs](Contigs) ｜ 下一页：[Host-Prediction](Host-Prediction)

对候选 contig 做宿主筛选 → 长度分流 → blastx / BFVD / CDD / 类病毒 blastn 多路判定。
结果把「已知病毒 / 新病毒候选 / 仅域证据 / 未分类」分开。

对应引擎：`Virus_Platform_Core/verify.py`（`verify` / `split_by_length` / `virus_call`）+ `web/tool_jobs.py`（`_tool_job_verify`，单条深查走 `_tool_job_hom`）；工具卡 `t-verify`（工具箱「病毒识别和分类分析」组）；操作目录 id `verify` / `hom`。

---

## 输入

### 输入格式

- 候选来源二选一。
- 来源一：一次 contig 分类运行的 `viral_contigs.fasta`。
- 同时从该 run 复制 `virus_classification.tsv`，作谱系 / 宿主归属。
- 来源二：直接给候选 FASTA。

### 参数

| 参数 | 默认 | 说明 |
|---|---|---|
| 宿主筛选 | 全部 | 全部 / 植物 / 真菌 / 细菌（噬菌体）/ 无脊椎（媒介昆虫）/ 脊椎动物 |
| 过滤方法 | `blastx,bfvd,cdd` | `blastx,bfvd,cdd`（默认三路）/ `blastx,bfvd` / 仅 blastx / 仅 BFVD / 仅 CDD |
| 组合方式 | `union` | `union` / `intersection` |
| 线程 | 自动 | 搜索线程 |

- 类病毒阈值固定，不在卡面暴露。

## 流程

对齐 MMPV-RNA 02b_Filter / 09b_Analysis_Verify（`verify.py`）。

### 步骤一：长度分流

- ≥1,000 bp 走病毒支；200–1,000 bp 走类病毒支；<200 bp 跳过。
- 例外：kunpeng 已判为病毒（taxid > 0）的 contig 无论长度都走病毒支。
- 该例外避免 500–1,000 bp 的病毒 contig 被误路由到类病毒支。

### 步骤二：宿主筛选（病毒支）

- 只作用于病毒支，按 ICTV 科 → 宿主来源（`ictv_family_host.tsv`）匹配。
- `host=all` 不筛。
- 分类唯一来源是 `virus_classification.tsv`。
- 无标注的 contig 保留，避免缺信息误删。

### 步骤三：病毒支证据与 call 四档

- DIAMOND blastx 对病毒蛋白库，evalue ≤1e-3。
- mmseqs2 对 BFVD 病毒域库，evalue ≤1e-3，与 NCBI 提交链同库。
- mmseqs2 对 PVDB CDD 结构域库（455 个植物病毒相关 profile，自动翻译），evalue ≤1e-3。
- 多路按 `union`（任一通过，倾向保留）或 `intersection`（都通过，严格）组合，也可只开单路。
- call 四档：
  - `known`：blastx identity ≥85% 且 subject 覆盖 ≥85%；
  - `novel`：identity ≥40% 且覆盖 ≥50%；
  - `domain_only`：仅 BFVD / CDD 域证据；
  - `unclassified`：no-hit，保留为新病毒候选池。

### 步骤四：类病毒支

- blastn 对 `viroids_v4`，该支为环状 RNA 病原专项。
- identity ≥80% 且覆盖 ≥60% 判 `viroid`，250–300 bp 时覆盖放宽到 ≥40%。
- 否则 `unclassified`，no-hit 同样保留。

### 步骤五：引擎缺失降级

- 缺 DIAMOND 跳过 blastx，缺 mmseqs2 跳过 BFVD/CDD，缺库跳过对应分支与类病毒支。
- 全部证据路都缺才报「无可用引擎」。

### 模式二：单条深查

- 对单条 / 少量 contig 做 BLASTN 或 BLASTX，为 GenBank 提交注释提供同源依据。
- BLASTN 为核酸对病毒参考核酸库；BLASTX 为 DIAMOND 对 PVDB 蛋白库。
- 引擎二选一：`local`（离线，秒级）或 `online`（NCBI nt/nr 限病毒，分钟级，最多 5 条）。
- 产物 `input.fasta`、`blastn_local.m8` / `blastx_local.m8`、`bfvd.tsv`、`hom_hits.json` / `hom_hits.tsv`。
- 正式运行目录为 `tool_runs/hom_*`。

## 产物

### 目录结构

工具卡运行目录 `run/tool_runs/verify_<时间戳>_<id>/verify/`：

```text
verify/
├── calls.tsv              逐 contig 判定表（主交付）
├── blastx.tsv             blastx 原始命中
├── bfvd_hits.tsv          BFVD 原始命中
├── cdd_hits.tsv           CDD 结构域原始命中
├── viroid_blastn.tsv      类病毒 blastn 原始命中
├── virus_input.fasta      分流后的病毒支输入
├── viroid_input.fasta     分流后的类病毒支输入
└── summary.json           计数与参数
```

### 关键文件与字段

| 文件 | 内容 |
|---|---|
| `calls.tsv` | 逐 contig 一行：`contig` / `branch`（virus / viroid）/ `call` / `len` / 8 级谱系 / `taxid` / `taxon` / `near_complete` / `score` / `host_source` / `blastx_*` / `bfvd_*` / `cdd_*` / `viroid_*` 证据列 |
| `blastx.tsv` | blastx 原始命中 |
| `bfvd_hits.tsv` | BFVD 原始命中 |
| `cdd_hits.tsv` | CDD 结构域原始命中 |
| `viroid_blastn.tsv` | 类病毒 blastn 原始命中 |
| `virus_input.fasta` | 分流后的病毒支输入 |
| `viroid_input.fasta` | 分流后的类病毒支输入 |
| `summary.json` | `virus` / `viroid` / `skipped` / `host_dropped` / `calls` / `combine` / `methods` / `host_filter` / `out_dir` |

- 管线阶段 ③b 的产物落 `03b_verify/`，口径相同；工具卡落独立运行目录。

### 判定结果怎么读

- 结果页分两张表：**病毒支**（≥1000 bp，blastx / BFVD / CDD 证据）与
  **类病毒命中**（200–1000 bp，blastn vs viroids_v4）各自独立展示。
- 每张表的分类谱系按「域 → 界 → 门 → 纲 → 目 → 科 → 属 → 种」逐级
  展开为独立列，放在表尾；类病毒表展示其可用的科 / 属 / 种三级。
- 验证通过的候选集是 `calls.tsv` 中 `call != unclassified` 的行。
- `unclassified` 行也留在表里，供新病毒 / 新类病毒候选池复核。
- 证据明细即同目录各路原始命中表。

## 示例输入与输出

### 示例输入

一条 2,418 bp 的组装 contig；≥1,000 bp，走病毒支。

```text
>NODE_1_length_2418_cov_4.587207
TGACAATACTGTTAAGCAAGTGTATGCCGGTGACGACATGGCATTAGATGGAGTTGTGAG
TGAAAAGAAATCATTCAGGAAGTTGCAAAATCTACTAAAACTCACTTCAAAAACTCTGTA
CCCAAAACAGGTTAAAGGGGATTACGCTGAATTTTGCGGTTGGACATTCACCCCAGGGGG
TATAATTAAAAATCCACTTAAAATGCATGCCTCAATTATGCTGCAAGAAGCCATTGGCAA
```

### 示例输出

```text
run/tool_runs/verify_20261105_120000_ab12cd/
├── verify/calls.tsv
├── verify/blastx.tsv
├── verify/bfvd_hits.tsv
├── verify/viroid_blastn.tsv
├── verify/virus_input.fasta
├── verify/viroid_input.fasta
└── summary.json
```

- `calls.tsv` 逐 contig 一行；`call` 列为 `known` / `novel` / `domain_only` / `unclassified`。
- `branch` 列标明走了 virus 还是 viroid 支。

## 界面与 CLI

### 界面

- 工具卡 `t-verify`：批量验证（来源 / 宿主筛选 / 方法 / 合并 / 线程）。
- 同卡另有单条深查模式：粘贴序列或 FASTA、本地 / 在线引擎、BLASTN / BLASTX。
- 结果区给验证表与分类谱系桑基图。

### CLI 与 agent

- 管线 ③b：`verify` 依赖 `assembly`。

```bat
python main.py analyze --r1 ... --sample S1 --stages assembly,verify
```

- `main.py` 没有 verify 子命令。
- 外部 agent 走操作目录 id `verify`（`python main.py agent-ops --id verify --schema`，MCP `vp_run_tool`）。
- 单条深查用操作目录 id `hom`。

## 相关

- 上游 contig 分类 → [Contigs](Contigs)
- 宿主预测 → [Host-Prediction](Host-Prediction)
- 一步串完组装 → 鉴定 → 验证 → [Virchain](Virchain)
