# NCBI-Submission · NCBI 提交准备

> 返回 [Home](Home) ｜ 上一页：[Toolbox](Toolbox) ｜ 下一页：[Logan](Logan)

对应引擎：`Virus_Platform_Core/ncbi_submit/`（unified_metadata / store / report_html / adopt_mmpv / templates）+ `suvtk_submit.py`（.sqn 生成编排）。

导航「提交准备」页：以 **`unified_metadata.csv` 一张表驱动 GenBank + BioSample 提交准备**，最终走 NCBI BankIt 网页向导或 Sequin 桌面程序。

---

## 在线编辑器

- **双击单元格编辑**、占位符橙色高亮（一眼看出哪些还没填）、Ctrl/Shift 多选删行、点表头列排序、每列填充进度；
- **批量填充 / 快速填充**：按列把占位符统一替换成真实值；
- **必填字段校验**（submit-validate）：提交前逐项检查；
- 内置示例：6 行 Demo / 公共数据样例 / 自测样例（快速上手）；
- 项目另存为；提交文件预览在线编辑（.sqn 二进制锁定）；BioSample 导出可选含占位符行；物种名 NCBI Taxonomy 在线校验；序列 FASTA 提取（sequences.fsa）与整包 zip 下载。

## 一键生成提交产物

| 产物 | 用途 |
|---|---|
| `source.src` | GenBank source modifiers（含 source_individual/ 按病毒拆分） |
| `biosample_template.tsv` | BioSample 批量注册 |
| `miuvig.tsv` / `assembly.tsv` | MIUViG 结构化注释 / 组装元数据 |
| `authorset/template.sbt` | 作者 ASN.1 |
| `report.html` | 交互式提交报告（可编辑） |
| `validation_report.txt` | 校验结果 |
| `sequences.fsa` | 序列 FASTA |

## 建表方式（submit-init）

- `--taxonomy taxonomy.tsv`（contig↔taxonomy）自动生成初表；
- `--metadata` 公共元数据表（Core14/Full，自动填日期/地点等，与 [Public-Data](Public-Data) 打通）；
- `--import-csv` 从已有 unified CSV/TSV/Excel 导入；
- `--demo` 示例数据建表。

## 一键收编（submit-adopt）

从 MMPV-RNA discovery 产物（`09b_Analysis_Verify/` 或 `08_Rescue/`）一键建提交项目：本地 `--out-dir` 或 `--from-server`（ssh/scp 拉取），自动以数据集名作 Isolate 前缀、按样本元数据表匹配 contig、兜底元数据单样本或多样本逐项可指定；`--dry-run` 只报会生成什么。

## CLI 全家桶

```bat
python main.py submit-list
python main.py submit-init --name nx6 --taxonomy taxonomy.tsv --authors "Zhang, Wenda" --title "..."
python main.py submit-init --name demo --demo
python main.py submit-fill --name nx6 --column bioproject --value PRJNA123456
python main.py submit-validate --name nx6
python main.py submit-export --name nx6 [--assembler "SPAdes;4.3.0;rnaviral" --sequencer "Illumina NovaSeq 6000" --enrichment "rRNA depletion"]
python main.py submit-adopt --name nx6 --out-dir <discovery输出根> [--dry-run] [--no-export]
python main.py submit-sbt --name nx6 --last Zhang --first Wenda --affil "Ningxia University" --city Yinchuan --country China --email me@x.com
```

产物在 `run/submissions/<项目名>/`。

## Windows 适配说明

- 不引入原管线的 suvtk/tbl2asn（Linux 依赖）；**特征表 (.tbl) 与 Sequin 包构建不在本模块范围**——用 BankIt 网页向导提交 source.src + 序列即可；
- 若后续需要 .sqn：本地 `.sqn` 生成编排已就绪（`suvtk_submit.py`），在装有 suvtk 的环境补跑。

## 相关

- 元数据来源 → [Public-Data](Public-Data)（Core14/Full）
- 共识序列来源 → [Assembly](Assembly) ③c
