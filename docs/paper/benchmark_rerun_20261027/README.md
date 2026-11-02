# VIROMOCK discovery 重跑归档（2026-10-27）

本目录回答一个问题：**论文里的 benchmark 数字，换用当前交付的参考库之后，还成立吗？**

## 为什么要有这个目录

`docs/paper/benchmark/` 里 2026-09 的那批结果，是在一棵**已经被删除**的库快照上跑出来的：

| 问题 | 具体事实 |
|---|---|
| 库已不存在 | 旧 harness 默认库 `databases/virusref_db/kv_index/` 于 2026-09-29 被 `plant_ref/` 取代并删除 |
| 快照缺 TSWV | `compute_and_plot.py:52-53` 注释即写明"D7：TSWV 在评测快照库内完全缺失"，D7 的两个真值被判 FN |
| 产物未归档 | `_bench_kvs2/`、`_bench_cons/` 从未进入仓库，`benchmark/results/artifacts/` 也不存在 → 旧数字无法用产物复核，只能重跑 |
| harness 有潜伏 bug | `benchmark/run_one.py:158` 原为 `load_ref_info()`（无参）→ 恒读已删的 `kv_index` 路径；任何 `spec['db_dir']` / `VP_BENCH_DB` 覆盖都会 `FileNotFoundError`。本轮已修（`load_ref_info(kv_info)`），并在 `compare.md` 里如实披露 |

所以本轮做两件事：**用当前交付库重跑**，并把"库指纹 + 每集输入 sha256 + 关键产物"一起归档。

## 目录内容

| 文件 | 作用 |
|---|---|
| `rerun.py` | 驱动：逐集调用 `../benchmark/run_one.py`（保证被测对象、命令、`--align-threads 4` 与旧基准完全一致），只是把 `db_dir` 指向 `plant_ref`，并记录输入摘要、峰值内存、耗时 |
| `score.py` | 评分：复制 `compute_and_plot.py` 的同一套匹配逻辑；**先对归档旧 `results.json` 重算，必须复现 20/28 与 8/8** 才继续，否则说明口径不等价 |
| `manifest.json` | 库/环境指纹：`reference.fasta` 与 `ref_info.tsv` 的 sha256、12,196 条参考计数、三个索引目录的文件数/字节/最新 mtime、git rev、Python/psutil 版本、CPU/内存 |
| `_input_digests.json` | 每个输入 FASTQ 的 size / mtime_ns / sha256（按 mtime 缓存，避免重复哈希 GB 级文件） |
| `results.json` | 每集结果（沿用 `run_one.py` 的 `confirmed` / `species_em` / `species_mapped` schema），另加 `inputs`、`library_sha256`、`peak_rss_mb`、`driver_wall_s` |
| `score.json` | 新旧逐集与聚合指标、评分自证明细、定量 MAE |
| `compare.md` | 人读版对照表（逐集、聚合、MAE、已知差异） |
| `artifacts/<exp>/` | 每集关键产物：确诊表 `identify/all_viruses.best.summary.tsv`、`quant/*/quant.sf`、`stats/*.refstats.tsv`、运行日志 |
| `logs/`、`_specs/` | 每集驱动 stdout 与 spec JSON（复现用） |

## 复现步骤

```powershell
cd 'D:\桌面\植物病毒分析平台'

# 1) 只打印计划（不跑）
python docs\paper\benchmark_rerun_20261027\rerun.py --dry-run

# 2) 先冒烟一集（约 10 s）
python docs\paper\benchmark_rerun_20261027\rerun.py --datasets D11 --force

# 3) discovery 18 集 × salmon（本目录的结果即此命令产出）
python docs\paper\benchmark_rerun_20261027\rerun.py --engine salmon

# 4) 可选：minibwa 引擎（首个数据集会因参考指纹不一致自动重建索引，约 8 s）
python docs\paper\benchmark_rerun_20261027\rerun.py --engine minibwa

# 5) 评分 + 新旧对照
python docs\paper\benchmark_rerun_20261027\score.py
```

输入数据来自 `E:\谷歌下载\测试数据验证`（可用环境变量 `VP_BENCH_VM` 覆盖），
对应 Dryad 归档 `10.5061/dryad.0zpc866z8`（14,614,047,052 bytes，CC0-1.0）。

## 评分口径（与论文一致）

- 物种级匹配：`norm()` 小写去非字母数字 → 双向子串；真值组来自
  `benchmark/truth_viromock_d110_groups.json`（D1–D10，含别名）与
  `truth_viromock.json`（D11–D18，按 `species` + 别名表 `VM_ALIAS`）。
- TP 按真值组去重；FP = 未匹配到任何真值组的确认物种；FN = 未命中的真值组。
- D5 的 minibwa 运行（原记录"提前退出 0.9 s / 2 MB"）按原口径记为作废，不进分母。

## 结果

见 [`compare.md`](compare.md)。要点：

- 评分自证：归档 `metrics_summary.csv` **35 行逐行比对全数一致**，旧
  salmon 20/28（71.4%）、8/8（100%）与 minibwa 19/27、7/8 均能复现。
- 换库后 **D7 由 0/1 变为 1/1**（TSWV 已在新库内），D8 由 1/2 变为 2/2，
  D3 由 1/5 变为 2/5；代价是 D1/D2 等集的 FP 上升（近缘参考增多），精确率下降。
- 峰值内存：D5/D6 单端大集升至约 1.27 GB（旧数字 0.23 GB 是旧库规模下的值），
  其余集合约 0.35 GB——写 Limitations 时不能照抄旧的 0.64 GB 上限。

> 口径提醒：`plant_ref` 是**当前交付库**，比 2026-09 的快照库大（12,196 vs 12,159 条）。
> 本轮数字回答的是"交付版能不能复现并更好"，不是"2026-09 那批数字对不对"。
