# Logan-Fetch · LOGAN 数据下载

> 返回 [Home](Home) ｜ 上一页：[Logan](Logan) ｜ 下一页：[Download](Download)

按 SRA accession 从 Logan 公共桶直接取公共库里已组装好的 contigs，不下载原始 reads，也不自己组装。输入是 accession 列表与四个过滤器参数，产物是按 accession 命名的 FASTA 与多 accession 的汇总文件。

对应引擎：`logan_contigs.py`（S3 拉取 + 过滤）；工具卡 `loganfetch`（`Virus_Platform_Core/web/tools_api.py` 注册、`web/tool_jobs.py` 的 `_tool_job_loganfetch` 驱动），导航 id `t-loganfetch`；操作目录 id `loganfetch`。

---

## 输入

### 输入格式

- accession 列表：SRR / ERR / DRR，最多 50 个；逗号 / 分号 / 空格 / 换行混排均可。
- 清洗规则：去空白、转大写、正则校验（三位字母 + 数字，可带 `.版本`），去重保序。
- 超过 50 个直接拒绝，防误贴整列。
- 未收录的 accession 明确报错（不在 2,340 万覆盖内或拼写有误）。

### 参数

参数定义以 `TOOL_PARAM_SCHEMAS` 为准。

| 参数 | 说明 |
|---|---|
| `accessions` | 必填，SRR / ERR / DRR，逗号 / 空格 / 换行分隔，最多 50 个 |
| `min_len` | 最短 contig 长度 |
| `circles_only` | 仅环形序列 |
| `fix_circles` | 修 concatemeric 环形序列 |
| `host_filter` | 宿主 contig 过滤，需已建宿主库 |
| `db_host` | 接口参数：另指宿主库目录；不填时 `host_filter` 用当前主宿主库 |

## 流程

### 步骤一 · 匿名拉取

- 数据源为 AWS Open Data 的 Logan 公共 S3 桶（`logan-pub`，匿名访问，约 2,340 万公共样本的统一组装）。
- 对象键为 `c/<acc>/<acc>.contigs.fa.zst`，每样本一个 zstd 压缩的 FASTA。
- boto3 以 `signature_version=UNSIGNED` 访问，无需 AWS 凭据。

### 步骤二 · 出站校验

- 只连 `logan-pub.s3.amazonaws.com`。
- 出站前先做 DNS 解析；解析到私网 / 环回 / 保留地址直接拒绝，与公共数据下载同口径，防 SSRF / DNS rebinding。

### 步骤三 · 流式解压与解析

- zstd 流式解压 → FASTA 流式解析，内存占用约等于单条序列加 4KB 读缓冲；压缩原件不落盘。
- `boto3` 与 `zstandard` 在函数内按需导入；环境缺包时该次运行报导入错误，装包后重跑即可。

### 步骤四 · 过滤

| 过滤器 | 判定 | 说明 |
|---|---|---|
| `min_len` | 序列长度 | `0` = 不过滤 |
| `circles_only` | **DTR**：长度 ≥60bp 时，前 30bp 在序列后半段再次出现，且该位置起的整段后缀与序列开头相同；或 **ITR**：前缀与其反向互补前缀完美匹配 | 任一成立即视为环形 |
| `fix_circles` | 找首个重复的 31-mer，截断到其结束位置 | 修 concatemeric 环形序列；正常环形（无内部重复）保持原样 |
| `host_filter` | kunpeng 主宿主库分类，丢弃判定为宿主 taxid 的 contig | 宿主 taxid 读宿主库 `host_db.json`；清单缺失时退化为不过滤并 WARN，不静默误删 |

## 产物

### 目录结构

```text
run/tool_runs/loganfetch_<YYYYmmdd_HHMMSS>_<随机 6 位>/
  <acc>.logan_contigs.fasta
  <acc>.logan_contigs_hostclean.fasta   勾 host_filter 时
  all.logan_contigs.fasta               多个 accession 时
  classify_host/                        宿主过滤中间文件
```

### 关键文件与字段

| 产物 | 内容 |
|---|---|
| `<acc>.logan_contigs.fasta` | 每 accession 一份 contigs |
| `all.logan_contigs.fasta` | 多 accession 的汇总，单 accession 不生成 |
| `<acc>.logan_contigs_hostclean.fasta` | 宿主过滤后的版本 |
| `classify_host/` | 宿主分类中间文件 |
| 任务结果统计 | 每 accession：总条数 / 保留条数 / 环形条数 / 碱基数 / 移除条数 |

- 产出的 contigs 可直接喂宿主去除、病毒序列提取（FASTA）、ORF 预测与溯源查询。

## 示例输入与输出

### 示例输入

```text
SRR24759446
SRR24759447
```

### 示例输出

```text
run/tool_runs/loganfetch_<YYYYmmdd_HHMMSS>_<随机 6 位>/
  SRR24759446.logan_contigs.fasta
  SRR24759447.logan_contigs.fasta
  all.logan_contigs.fasta          两个以上 accession 才生成
```

## 界面与 CLI

### 界面

- 工具箱卡：数据资源组「LOGAN 数据下载」（`/tools?g=resource#t-loganfetch`），填 accession 列表、勾过滤器直接跑。
- 卡面参数：accession 文本框加 `min_len` / `circles_only` / `fix_circles` / `host_filter` 四个控件。
- 跨模块带入：检索表 / 预筛结果 / 行级 🧬 按钮先调 `/api/logan/has_assembly`（匿名 S3 HEAD，一次 ≤50 个）探测有无组装；有则把 accession 预填进本卡，无则提示不在覆盖范围（CRR 等非 SRA 编号必然没有）。
- 结果可在任务中心看进度，失败原因原样透出；产物可在运行详情里下载 / 预览。

### CLI 与 agent

```bat
:: 无 main.py 独立子命令；入口是工具箱卡（工具 id = loganfetch）
:: 看参数 schema：
python main.py agent-ops --id loganfetch --schema
:: 按关键词搜操作目录：
python main.py agent-ops logan
```

```text
MCP：vp_run_tool(tool="loganfetch", params={
        "accessions": "SRR24759446,SRR24759447",
        "min_len": 500, "circles_only": true,
        "fix_circles": true, "host_filter": false})
```

## 相关

- 反向操作（提交自己的序列去比对） → [Logan](Logan)
- 去掉 contig 里的宿主序列 → [Preprocess](Preprocess)（① 宿主去除）
- 从 contig 提取病毒序列 / 预测 ORF → [ORF](ORF)、[Pipeline-Overview](Pipeline-Overview)
- 宿主库没建好（`host_filter` 需要） → [Databases](Databases)
- 本组总览 → [Public-Data](Public-Data)
