# Align · 序列比对（MAFFT + trimAl）

> 返回 [Home](Home) ｜ 上一页：[Cds-Export](Cds-Export) ｜ 下一页：[Phylo](Phylo)

多序列比对的独立模块，口径沿用 PhyloSuite 工作流。链路：MAFFT 比对 → trimAl 清剪 → 页内**彩色比对查看器**浏览与编辑。产物可直接送建树与 SDT。

对应引擎 `Virus_Platform_Core/msa_view.py`（SNP-only 查看数据），比对与清剪复用 `phylo._run_mafft` / `phylo._run_trimal`；工具卡 `t-align`；操作目录 id `align`。

## 输入

### 输入格式

- `seqs`：序列 FASTA，≥2 条；核酸 / 蛋白自动判别。
- `seqs_extra`：可选第二 FASTA，与 `seqs` **合并**后一起参与比对；同名自动去重改名。
- 页内可选三种来源：「手填 / 粘贴」「全长序列」「挑选的序列集（CDS / PEP）」。

### 参数

| 参数 | 默认 | 取值 / 说明 |
|---|---|---|
| `seqs` | 必填 | 序列 FASTA |
| `seqs_extra` | 空 | 第二 FASTA，与 `seqs` 合并后参与比对 |
| `strategy` | `auto` | `auto` / `linsi`（精确）/ `fast`（极速）；非法值提交时当场 400 |
| `trimal` | `automated1` | `automated1` / `gappyout` / `strict` / `none`（`none` = 不清剪） |
| `max_n` | 200 | 工具卡夹到 3–500；超出截断 |

- `gappyout` / `strict` 原样透传给 `_run_trimal`，不统一按 `automated1` 跑；`none` 跳过清剪。

## 流程

### 比对与清剪

1. 读入 `seqs`，有 `seqs_extra` 时先合并（合并条数写任务日志）。
2. 序列条数 `<2` 直接报错；随后按 `max_n` 截断。
3. 写 `input.fasta`（实际参与比对的序列）。
4. `_run_mafft` 按 `strategy` 比对，写 `aln.fasta`。
5. `trimal` 非 `none` 时 `_run_trimal` 清剪，写 `aln.trim.fasta`，并返回下游该用的那一份（`aln_used`）。

- 一键链里的同一套口径：基因组 MSA 与保守基因 MSA 也产 `aln.fasta` + `aln.trim.fasta`。
- 下游一律**优先 `aln.trim.fasta`**，缺则回落 `aln.fasta`（见 [Cmpchain](Cmpchain) 的 B2）。

### 名称净化与类型判定

- 序列类型判定用 `sdt_exact.detect_seqtype`，任务日志写明「蛋白(aa)」还是「核酸(nt)」。
- 名称净化：头信息按空格 / `|` 取首 token，非 `[A-Za-z0-9_\-.]` 替成 `_`。
- 做**防碰撞去重**：`A` → `A_1`，并保证候选名未被占用。合并第二份 FASTA 时同样走这套规则。
- 空名称回落到 `seq<序号>`。

### 彩色比对查看器

- `msa_view.snp_view_data` 只保留**变异位点**：`positions` / `rows` / `consensus` / `diversity`。
- 按 ACGT 配色分块渲染（A 绿 / C 蓝 / G 橙 / T 红），大比对分页，上限 80 条。
- 可改名 / 改序列 / 删行，保存为 `<原名>.edit.fasta` 后直接送建树 / SDT。
- 数据口径：`positions` 是 1-based 的原比对列号，`rows` 是每条序列在这些列上的字符，`consensus` 是每列多数碱基，`diversity = 1 − 最高频率`（前端画变异度柱）。
- 两条硬校验：序列 <2 条直接报错；各序列**长度不一致**（= 输入其实不是多序列比对）直接报错，不静默按最短长度截断。超过 `MAX_SEQS = 80` 条才截断并置 `seqs_truncated`。
- 查看器可直接挑样品流程或集合建树的已有比对，也可看 `quicktree` / `structcmp` 等运行目录里的 `aln.fasta`（接口 `api_tool_msa_data`），不必手填路径。

## 产物

### 目录结构

```text
run/tool_runs/<align_run>/
├── input.fasta      实际参与比对的序列（已截断到 max_n、已改名去重）
├── aln.fasta        MAFFT 比对结果
└── aln.trim.fasta   trimAl 清剪结果（trimal=none 时不存在）
```

### 关键文件与字段

| 文件 | 内容 |
|---|---|
| `input.fasta` | 实际参与比对的序列，已截断、已改名去重 |
| `aln.fasta` | MAFFT 比对结果 |
| `aln.trim.fasta` | trimAl 清剪结果；`trimal=none` 时不存在 |

- **不产 `summary.json`**。
- 返回摘要（任务卡展示）：`n_seqs` / `seqtype` / `strategy` / `trimal` / `trim`（清剪是否生效及方式）/ `aln` / `aln_used`（下游该用哪一份）。
- 编辑保存走 `/api/align/save`：提交内容先按 FASTA 校验（≥2 条、字符集），写入源文件旁 `<stem>.edit.fasta`。
- 源不在平台（含自定义输出根）内时，编辑结果落到 `tool_runs/align_edit_<时间戳>/edited.fa`；提交体积有上限（超过直接 400）。

## 示例输入与输出

### 示例输入

待比对序列 FASTA（≥2 条；`max_n` 控制最多取几条，界面可调 3–500）：

```text
>PZ530881.1
ATGCGTACGTACGTACGTACGTACGTACGTACGTACGT
>PZ512725.1
ATGCGTACGTACGTACGTACGTACGTACGTACGTACGA
```

勾选 trimAl 时对 MAFFT 结果做清剪；选 `none` 则跳过清剪。

### 示例输出

产物只三件：`input.fasta`、`aln.fasta`、`aln.trim.fasta`；**不产 `summary.json`**。

比对结果可在结果中心用「比对查看器」彩色浏览与编辑（[Results](Results) → MSA 查看）。`aln.trim.fasta` 可直接送建树（[Phylo](Phylo) 的 `t-treebuild`）或 SDT（[Phylo](Phylo) 的 `t-sdt`）。

## 界面与 CLI

### 界面

- 工具卡 `t-align`「序列比对（MAFFT + trimAl）」分两段：
  1. 运行比对：集合 + 来源 + FASTA / 额外序列 + 策略 + trimAl；
  2. 页内彩色比对查看器：全列渲染 / 分页 / 核酸·蛋白配色 / ✏️ 编辑。
- 输入可用内置示例、集合基因提取产物或任意比对 FASTA。

### CLI 与 agent

- `main.py` **没有 align 子命令**（`add_parser` 核实）。
- 外部 agent 走操作目录 id `align`：`python main.py agent-ops --id align --schema` 查参数，MCP `vp_run_tool` 执行。
- 建树 / SDT 的卡与参数口径 → [Phylo](Phylo)；一键链里的比对阶段 → [Cmpchain](Cmpchain)。

## 相关

- 组导读 → [Compare](Compare)
- 用集合（全基因组 / CDS / PEP）直接建树 → [Phylo](Phylo)
- 挑 CDS 再比对 → [Cds-Export](Cds-Export)；取参考序列 → [Seq-Prep](Seq-Prep)
- 一条链跑完 → [Cmpchain](Cmpchain)
