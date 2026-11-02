# -*- coding: utf-8 -*-
"""report_kit — 通用「学术论文风」结果报告生成器。

一份样品目录（`run/results/<样品>/`）或一个工具箱运行目录
（`run/tool_runs/<tool>_<ts>_<id>/`）走完任意阶段组合后，本包都能吐出一份
**自包含、离线可开、带结论**的 HTML 报告（`report_v2.html`）。

设计取向（与 viz.build_report 的分工）：

- viz.build_report 出的是「图 + 表 + 统计卡」的堆叠视图，看图说话；
- report_kit 出的是**结论文档**：先给判定，再给证据，再给口径边界与缺口，
  版式为学术论文风（白底、衬线标题、克制的墨绿/墨蓝）。

数据纪律（红线）：

- 所有数字都从落盘产物读，读不到就写「无」或整节省略，**绝不估**；
- 每个结论都带 `evidence`（文件相对路径），读者可逐条回查；
- 口径冲突（如「记录位点数」与「真实变异数」）一律并列呈现并注明，
  不替用户抹平。

对外只暴露两个函数：

    build_enhanced_report(target, logger=None, force=False) -> dict
    detect_profile(target) -> dict

CLI 单跑：
    python -m Virus_Platform_Core.report_kit <样品目录|工具运行目录> [--open]
"""
from __future__ import annotations

import os
import sys

from .model import collect, detect_profile           # noqa: F401
from .render import render_full, write_report         # noqa: F401

__all__ = ['build_enhanced_report', 'detect_profile', 'collect',
           'render_full', 'write_report', 'OUT_NAME']

#: 报告文件名（与 viz 的 report.html 并存，互不覆盖）
OUT_NAME = 'report_v2.html'
#: 报告元数据（供平台页面与二次渲染复用）
META_NAME = 'report_v2_meta.json'


def build_enhanced_report(target, logger=None, force=False, out_path=None):
    """生成增强报告，返回 {'html': 路径, 'meta': 路径, 'model': 模型}。

    target：样品结果目录或工具箱运行目录（绝对路径或相对 CWD）。
    force=False 且 report_v2.html 比全部输入新时直接复用（与 viz 的
    「输入未变更就复用」语义保持一致）。
    """
    target = os.path.abspath(str(target))
    if not os.path.isdir(target):
        raise FileNotFoundError(f'目录不存在: {target}')

    model = collect(target, logger=logger)

    out_dir = model['layout']['out_dir']
    html_path = out_path or os.path.join(out_dir, OUT_NAME)
    meta_path = os.path.join(os.path.dirname(html_path), META_NAME)

    if not force and _is_fresh(html_path, model):
        if logger:
            logger.log('增强报告复用既有 report_v2.html（输入未变更）')
        return {'html': html_path, 'meta': meta_path, 'model': model,
                'reused': True}

    os.makedirs(os.path.dirname(html_path), exist_ok=True)
    html = render_full(model)
    tmp = html_path + '.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write(html)
    os.replace(tmp, html_path)

    import json
    with open(meta_path, 'w', encoding='utf-8') as fh:
        json.dump(_meta_of(model), fh, ensure_ascii=False, indent=1)

    if logger:
        logger.log(f"增强报告: {html_path} "
                   f"（{os.path.getsize(html_path) / 1024:.0f} KB）")
    return {'html': html_path, 'meta': meta_path, 'model': model,
            'reused': False}


def _is_fresh(html_path, model):
    if not os.path.isfile(html_path):
        return False
    try:
        out_mt = os.path.getmtime(html_path)
    except OSError:
        return False
    for p in model['layout'].get('inputs') or []:
        try:
            if os.path.getmtime(p) > out_mt:
                return False
        except OSError:
            continue
    return True


def _meta_of(model):
    """报告元数据：任务档案 + 计数 + 证据文件清单（不含正文）。"""
    return {
        'schema': 1,
        'kind': model['layout']['kind'],
        'target': model['layout']['target'],
        'sample': model['sample'],
        'profile': model['profile'],
        'n_stages': len(model['stages']),
        'n_evidence': len(model['evidence']),
        'n_findings': len(model['findings']),
        'n_gaps': len(model['gaps']),
        'verdict': model['verdict'],
        'generated': model['generated'],
        'evidence': model['evidence'],
    }


def main(argv=None):
    """`python -m Virus_Platform_Core.report_kit <dir> [--force] [--open]`"""
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ('-h', '--help'):
        print(__doc__)
        return 0
    target = argv[0]
    force = '--force' in argv
    try:
        out = build_enhanced_report(target, force=force)
    except Exception as e:                      # CLI 边界：原文转述，不美化
        print(f'[失败] {type(e).__name__}: {e}', file=sys.stderr)
        return 1
    print(out['html'])
    if '--open' in argv:
        try:
            os.startfile(out['html'])           # Windows 双击等效
        except Exception:
            pass
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
