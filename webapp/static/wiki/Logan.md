# Logan · LOGAN 溯源

> 返回 [Home](Home) ｜ 上一页：[Open-Virome](Open-Virome) ｜ 下一页：[Logan-Fetch](Logan-Fetch)

回答一个问题：这条病毒序列还出现在哪些公开数据里。输入是自样品 ③ 组装出的病毒 contig，或任意 FASTA。报告给出命中样本的物种组成、k-mer × ANI 关系、样本类型构成与逐片段 Run 清单。

对应引擎：`logan_trace.py`（模块编排与报告）、`logan_submit.py`（Selenium 批量提交）；页面：导航「LOGAN 溯源」；操作目录 id 无（agent 走 CLI 子命令）。

---

## 输入

### 输入格式

- **来源样品**：自动取 ③ 组装的 `viral_contigs.fasta`，可指定 contig 子集。
- **任意 FASTA**：直接粘贴或给文件路径。
- **切片规则**：每条 contig 切成 ≤2.5kb 查询片段（Logan-Search 单条上限）；`--segments 1-4` 控制每条切几段，≤2.5kb 恒为 1 段。

### 参数

| 参数 | CLI | 说明 |
|---|---|---|
| 查询名 | `--name` | 决定任务目录 `run/logan/<查询名>/` |
| 来源样品 | `--sample` | 取该样品 ③ 组装的 `viral_contigs.fasta` |
| contig 子集 | `--contigs c1,c2` | 只提交指定 contig |
| 直接 FASTA | `--fasta seq.fa` | 不走样品，提交任意 FASTA |
| 切片段数 | `--segments 2` | 每条 contig 切几段；≤2.5kb 恒为 1 段 |
| 通知邮箱 | `--email` | 逗号分隔多邮箱，自动轮换防限额 |
| Groups | `--group` | All / All_No_viral_human / Fast / Fast_No_human（默认）/ Fast_No_RefSeq / Transcriptomic / Metatranscriptomic / Metagenomic / GenBank_RefSeq |
| 显示浏览器 | `--show-browser` | 批量提交时显示浏览器窗口 |
| 导入结果 | `--segment` + `--result` | 手动方式回填某片段的结果表（CSV / TSV） |

## 流程

### 步骤一 · 提交到 Logan-Search

- 把病毒 contig 提交到 [Logan-Search](https://logan-search.org/dashboard)。该服务属 IndexThePlanet 计划：对整个 NCBI SRA 全量组装后建立 k-mer 索引，覆盖约 2,340 万公开样本。
- 索引 k=31，返回每个样本的共享 k-mer 比例与 ANI 估计。
- 引用：Chikhi et al. 2025, bioRxiv 10.1101/2024.07.30.605881。

### 步骤二 · 一键批量提交

- 在任务卡填通知邮箱、选 Groups，点「开始批量提交」。
- 平台以子进程调用内置 `logan_submit.py`（Selenium 驱动本机 Edge / Chrome，默认 headless），逐条提交全部未导入片段。
- 自动轮询结果、下载结果表、导入并生成溯源报告。
- SSE 实时推送：提交脚本在每个关键节点输出结构化进度事件，包括正在提交、已提交 session、等待服务器结果、下载中、已完成 n 条。页面 EventSource 订阅即时刷新，进度条按已完成条数驱动、永不回退。
- 等待期每 30s 轻量探测，刷新已等秒数与 HTTP 状态；结果提前就绪即提前下载。
- 断点续跑与补漏：个别片段超时未落定，再点一次批量提交即只补漏未导入片段。
- 邮箱池轮换、每 10 条重启浏览器防 session 失效。
- 依赖 selenium（`requirements.txt` 已含）；未安装时该面板灰显，手动方式不受影响。

### 步骤三 · 手动半自动

- 复制片段序列，在本地浏览器打开 Logan-Search 提交（平台不发任何外部请求）。下载结果表（CSV / TSV）后，回页面点对应片段「导入结果」，自动聚合出报告。
- 两种提交方式产物一致。

## 产物

### 目录结构

```text
run/logan/<查询名>/
  batch_out/    批量提交下载的原始结果表
```

### 关键文件与字段

| 产物 | 内容 |
|---|---|
| `batch_out/` | 批量提交下载的原始结果表 |
| 导入结果表 | 手动方式下由 `--result result.tsv` 提供（CSV / TSV） |

### 溯源报告

| 图表 | 内容 |
|---|---|
| 物种分布条形图 | 命中样本的物种组成 |
| k-mer × ANI 散点 | 共享 k-mer 比例 vs ANI 估计（近缘程度一目了然） |
| 样本类型分布 | metagenomic / transcriptomic 等构成 |
| 逐片段样本清单 | 可复制 Run 列表（转 [Download](Download) 直接下载） |
| 交叉对照 | 来源为平台样品时自动附 ④ ICTV 宿主预测 × LOGAN 实测物种交叉对照 |

## 示例输入与输出

### 示例输入

```text
查询名：Q1
来源样品：S1（③ 组装的 viral_contigs.fasta）
切片：每条 contig 切 2 段（≤2.5kb 恒 1 段）
Groups：Fast_No_human（默认）
通知邮箱：a@qq.com
```

### 示例输出

```text
run/logan/Q1/
  batch_out/                    批量提交下载的原始结果表
  溯源报告：物种分布条形图 / k-mer × ANI 散点 / 样本类型分布 /
            逐片段样本清单 / ④ ICTV 宿主预测 × LOGAN 实测物种交叉对照
```

## 界面与 CLI

### 界面

- 导航「LOGAN 溯源」页；两种提交方式在同一页并列：批量面板与手动面板，批量进度由 SSE 推送。

### CLI 与 agent

```bat
python main.py logan-create --name Q1 --sample S1 [--contigs c1,c2] [--segments 2]
python main.py logan-create --name Q2 --fasta seq.fa
python main.py logan-batch --name Q1 --email a@qq.com[,b@qq.com] [--group Fast_No_human] [--show-browser]
python main.py logan-import --name Q1 --segment 1 --result result.tsv
python main.py logan-jobs
```

- Groups 取值见「参数」表。
- agent：无操作目录 id，走上述 `logan-create` / `logan-batch` / `logan-import` / `logan-jobs` 子命令。

## 相关

- 交叉对照的 ④ → [Host-Prediction](Host-Prediction)
- 命中样本的下载 → [Download](Download)
- 反向操作（按 accession 直取预组装 contigs） → [Logan-Fetch](Logan-Fetch)
- 本组总览 → [Public-Data](Public-Data)
