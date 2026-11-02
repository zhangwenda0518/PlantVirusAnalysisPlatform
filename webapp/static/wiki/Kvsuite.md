# Kvsuite · ②b 已知病毒识别与定量

> 返回 [Home](Home) ｜ 上一页：[Preprocess](Preprocess) ｜ 下一页：[Assembly](Assembly)

对应引擎：`kv_stage.py` + `Virus_Platform_Core/known_virus_suite/`（自包含引擎包）。

把去宿主 reads 比对到**已知病毒参考索引**，回答「样品里有哪些已知病毒、各有多少 reads」。

---

## 做什么

1. **识别**：reads 对 `databases/virusref_db/kv_index/`（自包含参考库：reference.fasta + reference.ref_info.tsv + salmon_k31/ 索引）做比对/映射，产出逐参考鉴定表；
2. **二次过滤定量**：对鉴定结果过滤（低支持度参考剔除），产出定量表与 discarded 表；
3. **比对与深度**：对保留的病毒参考产出比对 BAM + pandepth 深度；
4. **提取病毒 reads**：比对上的 reads 存为 `viral_R1/R2.fastq.gz`，供 ③ 组装（默认组装输入）。

## 定量引擎（二选一）

| 引擎 | 口径 | 说明 |
|---|---|---|
| **salmon**（默认） | EM 定量 | 对已知病毒参考做选择比对 + EM 丰度估计；共识段内部固定 minibwa |
| minibwa | 真比对计数 | 逐 read 计数口径 |

## 产物（02b_kvsuite/）

```
identify/all_viruses.summary.tsv   逐参考鉴定：物种/taxid/Uniq_Reads/覆盖度/深度
filter/filtered.tsv                二次过滤后定量表
filter/discarded.tsv               过滤剔除明细
align/*.sorted.bam(+.bai)          比对 BAM 与索引
align/pandepth…                    逐参考深度
viral_R1/R2.fastq.gz               比对上的病毒 reads（供 ③ 组装）
summary.json  kvsuite.log          汇总与日志
```

**分类树来源**：报告的科属种层级来自 ②b 鉴定表（逐参考 Uniq_Reads 按种/属/科累计）+ 参考库 `final.cluster.ref_info.tsv` 的 `VMR_Family/VMR_Genus`——**不加载全量 NCBI taxonomy**（避免内存压力；未收录 VMR 谱系的参考只画到种）。

## 界面

- 分析管道页 ②b 卡片：参数 + `📊 查看`（鉴定表就地弹窗浏览、可下载）；
- 结果中心：已分析样品列表 + 专项结果入口。

## CLI

```bat
python main.py analyze --r1 ... --r2 ... --sample S1 --stages kvsuite
python main.py build-virus-db --fasta databases\virusref_db\final.cluster.ref.fasta ^
       --info databases\virusref_db\final.cluster.ref_info.tsv    :: 重建病毒分类库
python main.py analyze ... --confidence 0.0                        :: 分类置信度
```

## 注意

- 参考库更新：替换 4 件套（两个 FASTA + 两个 Info.tsv + DATA_VERSION）后平台自动重建元数据缓存与 BLAST 库（`dev_tools/build_virus_db.py` 可重建）；
- 鉴定表是 ⑦ 建树分组、⑩ 报告分类树的共同数据源。

## 相关

- 病毒 reads 下一步 → [Assembly](Assembly)
- 未知的、参考库里没有的病毒 → ③ 组装 + ⑥/⑥b 从头发现
