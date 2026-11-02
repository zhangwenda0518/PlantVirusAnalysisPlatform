# Logan · LOGAN 溯源

> 返回 [Home](Home) ｜ 上一页：[NCBI-Submission](NCBI-Submission) ｜ 下一页：[Databases](Databases)

对应引擎：`logan_trace.py`（模块编排与报告）、`logan_submit.py`（Selenium 批量提交）。

回答一个问题：**「这条病毒序列还出现在哪些公开数据里？」**

把病毒 contig 提交到 [Logan-Search](https://logan-search.org/dashboard)（IndexThePlanet 计划：对**整个 NCBI SRA 全量组装**后建立的 k-mer 索引，覆盖 **~2340 万公开样本**；k=31，返回每个样本的共享 k-mer 比例与 ANI 估计）。

引用：Chikhi et al. 2025, bioRxiv 10.1101/2024.07.30.605881。

---

## 两种提交方式

### 方式一 · 一键批量（推荐）

任务卡片填通知邮箱（逗号分隔多邮箱**自动轮换防限额**）、选 Groups → 点「开始批量提交」：

- 平台以子进程调用内置 `logan_submit.py`（Selenium 驱动本机 Edge/Chrome，默认 headless）逐条提交全部未导入片段；
- 自动轮询结果、下载结果表、**导入并生成溯源报告**；
- **SSE 实时推送**：提交脚本在每个关键节点输出结构化进度事件（正在提交 / 已提交 session / 等待服务器结果——含已等秒数与 HTTP 状态每 30s 刷新 / 下载中 / 已完成 n 条），页面 EventSource 订阅即时刷新，进度条按已完成条数驱动、永不回退；
- 等待期从整段盲睡改为每 30s 轻量探测，**结果提前就绪即提前下载**；
- **断点续跑与补漏**：个别片段超时未落定，再点一次批量提交即只补漏未导入片段；
- 邮箱池轮换、每 10 条重启浏览器防 session 失效；
- 依赖 selenium（requirements.txt 已含）；未安装时该面板灰显、手动方式不受影响。

### 方式二 · 手动半自动

复制片段序列 → 自己浏览器打开 Logan-Search 提交（**平台不发任何外部请求**）→ 下载结果表（CSV/TSV）→ 回页面点对应片段「导入结果」→ 自动聚合出报告。

两种方式产物一致。

## 查询构建

- 来源样品：自动取 ③ 组装的 `viral_contigs.fasta`（可指定 contig 子集）；
- **自动切片**：每条 contig 切成 ≤2.5kb 查询片段（Logan-Search 单条上限；`--segments 1-4` 控制每条切几段，≤2.5kb 恒为 1 段）；
- 或直接粘贴任意 FASTA。

## 溯源报告

| 图表 | 内容 |
|---|---|
| 物种分布条形图 | 命中样本的物种组成 |
| k-mer × ANI 散点 | 共享 k-mer 比例 vs ANI 估计（近缘程度一目了然） |
| 样本类型分布 | metagenomic / transcriptomic 等构成 |
| 逐片段样本清单 | 可复制 Run 列表（转 [Public-Data](Public-Data) 直接下载） |
| **交叉对照** | 来源为平台样品时自动附 **④ ICTV 宿主预测 × LOGAN 实测物种交叉对照** |

## 界面与 CLI

- 导航「LOGAN 溯源」页；任务存于 `run/logan/<查询名>/`（batch_out/ 为批量下载的原始结果表）。

```bat
python main.py logan-create --name Q1 --sample S1 [--contigs c1,c2] [--segments 2]
python main.py logan-create --name Q2 --fasta seq.fa
python main.py logan-batch --name Q1 --email a@qq.com[,b@qq.com] [--group Fast_No_human] [--show-browser]
python main.py logan-import --name Q1 --segment 1 --result result.tsv
python main.py logan-jobs
```

Groups 可选：All / All_No_viral_human / Fast / Fast_No_human（默认）/ Fast_No_RefSeq / Transcriptomic / Metatranscriptomic / Metagenomic / GenBank_RefSeq。

## 相关

- 交叉对照的 ④ → [Host-Prediction](Host-Prediction)
- 命中样本的下载 → [Public-Data](Public-Data)
