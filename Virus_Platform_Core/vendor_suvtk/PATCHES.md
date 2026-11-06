# vendor_suvtk PATCHES

本目录是 [suvtk 0.2.0](https://github.com/LanderDC/suvtk)（commit `d8ef68f`）
中 `comments.py` / `table2asn.py` / `gbk2tbl.py` / `utils.py` 的仓库内副本。
上游许可为 GNU GPL v3（见 `LICENSE`）。本文件逐条登记与上游的差异，每处
差异在源码里都有 `# [ncbisub-vendor]` 行注释，便于 `diff -u` 对照。

提取日期：2026-11-02
提取者：28.ncbi_submission（ncbisub）

## 差异总表

| ID | 文件 | 上游位置 | 修改 | 原因 |
|---|---|---|---|---|
| V01 | 全部 | 文件头 | 增加 GPL v3 + provenance 注释块 | 明确来源、版本、提取日期与许可 |
| V02 | `comments.py` | 17-20 | 删除 `import rich_click as click`；`from suvtk import utils` → `from . import utils`（直接运行脚本时回退 `from vendor_suvtk import utils`） | 去掉 rich-click 依赖；vendor 包内相对导入 |
| V03 | `comments.py` | 100-148 | 删除 `@click.command` / 6 个 `@click.option` 装饰器，函数改为普通 `comments(taxonomy, features, miuvig, assembly, checkv, output)` | 进程内直调；参数名与上游 CLI 选项一致 |
| V04 | `comments.py` | 164, 215, 219, 234, 244 | `click.ClickException` → `CommentsError(ValueError)` | 不依赖 click 的异常类型 |
| V05 | `comments.py` | 330 | `click.echo(...)` → `print(...)` | 同上 |
| V06 | `comments.py` | 函数末尾 | 返回写出的 `.cmt` 路径 | 引擎需要拿到产物路径 |
| V07 | `comments.py` | 333-334 | `if __name__ == "__main__": comments()` → argparse `_build_parser()` / `_cli()` | 保留独立调试入口（`python -m vendor_suvtk.comments`） |
| V08 | `table2asn.py` | 22-24 | **补 `import pandas as pd`**（上游 0.2.0 缺失 import，但 `process_comments`/`table2asn` 直接调用 `pd.read_csv`） | 修复上游 bug；不补则必 `NameError` |
| V09 | `table2asn.py` | 22-24 | 删除 `import rich_click as click`；增加 `os` / `shutil` / `subprocess`；`from suvtk import utils` → `from . import utils`（含直接运行回退） | 去 rich-click；参数化二进制与 PATH |
| V10 | `table2asn.py` | 50, 64, 66, 102, 111, 115, 146 | `click.echo(...)` → `print(...)` | 同上 |
| V11 | `table2asn.py` | 149-197 | 删除 `@click.command` / 6 个 `@click.option` 装饰器，函数改为普通 `table2asn(input, output, src_file, features, template, comments, table2asn_bin=None)` | 进程内直调 + 新增 `table2asn_bin` 参数 |
| V12 | `table2asn.py` | 204-214 | 上游用 `utils.Exec("table2asn ...", shell=True)` 调 PATH；改为 `resolve_table2asn()`（参数 → `TABLE2ASN_BIN` env → `shutil.which`）+ `subprocess.run(argv, cwd=bindir, env=PATH 前置 bindir, shell=False)` | 参数化二进制；路径含空格（中文/桌面）不再被 shell 拆坏；Windows 同目录 DLL 可加载 |
| V13 | `table2asn.py` | 216-228 | `.val` 扫描忽略空行（上游把空行也当 `UNEXPECTED ERROR`）；返回 `{rc, cmd, stdout, stderr, sqn, val, unexpected, ok}` | 避免误报；引擎需要结构化结果 |
| V14 | `table2asn.py` | 228 | 上游 `print(...)` 改为仅在无 unexpected 且 rc=0 且 `.sqn` 非空时打印成功信息；CLI 退出码由 `_cli()` 返回 | 行为更严格，便于引擎判断 |
| V15 | `table2asn.py` | 新增 | `resolve_table2asn()` 公开辅助函数；argparse `_build_parser()` / `_cli()`（含 `--table2asn-bin`） | 二进制解析可复用；独立调试入口 |
| V16 | `gbk2tbl.py` | 54-56 | 删除 `import rich_click as click`；保留 `from Bio import SeqIO`；新增 `os` | 去 rich-click |
| V17 | `gbk2tbl.py` | 58-81 | 删除 `@click.command` / 3 个 `@click.option` 装饰器，函数改为普通 `gbk2tbl(input, mincontigsize=0, prefix="seq")` | 进程内直调 |
| V18 | `gbk2tbl.py` | 82-181 | 统计并返回 `{records_total, contigs_written, contigs_skipped, feature_counts, cds, tbl_path, fsa_path, ..._bytes}`；`mincontigsize` 强制 `int()` | `/api/gbk2tbl` 的 `X-Gbk2tbl-Stats` 摘要；表单值容错 |
| V19 | `gbk2tbl.py` | 184-186 | `if __name__ == "__main__": gbk2tbl()` → argparse `_build_parser()` / `_cli()` | 保留独立调试入口 |
| V20 | `utils.py` | 25-26 | 删除未使用的 `import rich_click as click`（`Exec` / `get_available_cpus` 逻辑不变） | 去 rich-click 依赖 |
| V21 | `__init__.py` | 上游 `__init__.py` | 只暴露 `utils` / `comments` / `gbk2tbl` / `table2asn`（PEP 562 惰性 `__getattr__`），不再导入未 vendor 的 `co_occurrence` / `features` / `taxonomy` 等；`python -m vendor_suvtk.<name>` 不再触发 runpy 预导入警告 | vendor 子集不包含其余子命令；gbk2tbl 可不依赖 pandas 单独使用 |
| V22 | 全部 | Python 语法 | 不使用 3.9+ 语法（`dict \| dict`、内置泛型注解、`str.removeprefix` 等） | 服务器 gunicorn 为 `/usr/bin/python3.8` |

## 与上游做 diff 的方法

```bash
# 上游 0.2.0 源码（本机参考路径）
UP=/d/桌面/延伸基因组/suvtk/suvtk
# vendor 副本
VD=28.ncbi_submission/vendor_suvtk
diff -u "$UP/comments.py"  "$VD/comments.py"
diff -u "$UP/table2asn.py" "$VD/table2asn.py"
diff -u "$UP/gbk2tbl.py"   "$VD/gbk2tbl.py"
diff -u "$UP/utils.py"     "$VD/utils.py"
```

上游 `table2asn.py` 在 0.2.0 缺失 `import pandas as pd`；本副本已补（V08）。
若上游后续修复，合并时注意 V08 可删除，其余 `[ncbisub-vendor]` 标记仍适用。

## 未改动的上游行为（有意保留）

- `comments.py` 的列校验、允许值列表、列排序与 `MIUVIG:5.0-Data` 前缀。
- `table2asn.process_comments()` 的 isolate 分组、多数 `pred_genome_type`、
  `number_contig` / `pred_genome_struc=segmented` 逻辑。
- `gbk2tbl` 的 23 个 allowed qualifiers、partial 坐标处理、负链 start>end 写法。
- `utils.Exec()` 仍保留 `shell=True`（本仓库引擎不调用它；table2asn 已改为 argv 列表）。
