# Primer · ⑧ 引物设计

> 返回 [Home](Home) ｜ 上一页：[Phylo](Phylo) ｜ 下一页：[Genome-Plots](Genome-Plots)

对应引擎：`primer.py`（编排）、`primer_design.py`（从序列出发）、`primer_thermo.py`（热力学评分）、`primer3_runtime.py`（primer3-py 安全接入层）。

---

## 两种模式

| 模式 | 场景 | 做法 |
|---|---|---|
| **conserved**（默认） | 跨近缘参考通用引物 | 基于 05_phylo 多比对找**保守区**，在一致序列上设计 |
| **plain** | 单一序列快速设计 | 直接在目标序列上设计（PCR / qPCR 参数） |

## 热力学评估与评分

- `primer_thermo.py` 独立模块：Tm、GC%、发夹/自二聚/异二聚体 ΔG、产物大小；
- 输出带**质量评分 score + recommendation**（优/可用/慎用），二聚体警示直接标注。

## 宿主特异性检查（可选）

- 对 1.8GB 宿主基因组建 BLAST 库（**首次 10-30 分钟，此后复用**）；
- 每条引物 BLAST 宿主基因组，命中即标警示。

## primer3_runtime 安全接入层

primer3-py 的 C 扩展在 import 时要一次性预计算热力学表（大内存峰值）——平台把 primer3 导入收敛到唯一入口，延迟初始化 + 子进程隔离，避免 GUI 主进程被撑爆。

## 产物（06_primer/）

`primers.tsv`：引物序列/位置/Tm/GC/产物大小/二聚体警示/质量评分 + recommendation。

## 界面与 CLI

- 分析管道 ⑧ 卡片：引物模式/特异性检查开关 + `📊 查看`（引物表就地浏览下载）。

```bat
python main.py analyze --r1 ... --sample S1 --stages primer ^
       --primer-mode conserved --specificity
```

## 相关

- dsRNA 侧的引物（siRNA 表达载体）→ [Toolbox](Toolbox) dsRNA 全链路
- 保守区来自哪 → [Phylo](Phylo) ⑦ 多比对
