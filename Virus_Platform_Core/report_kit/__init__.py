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

import hashlib
import json
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

    if not force and _is_fresh(html_path, model, meta_path):
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

    with open(meta_path, 'w', encoding='utf-8') as fh:
        json.dump(_meta_of(model), fh, ensure_ascii=False, indent=1)

    if logger:
        logger.log(f"增强报告: {html_path} "
                   f"（{os.path.getsize(html_path) / 1024:.0f} KB）")
    return {'html': html_path, 'meta': meta_path, 'model': model,
            'reused': False}


#: 本模块自己的产物名：判定「输入是否变过」时必须排除，否则
#: report_v2_meta.json 比 report_v2.html 晚写 1 毫秒，会让自己永远「不新鲜」。
_SELF_OUT = (OUT_NAME, META_NAME)


def _file_digest(path):
    """文件内容 SHA-1（读失败 → ''，调用方按"不认识"处理）。"""
    h = hashlib.sha1()
    try:
        with open(path, 'rb') as fh:
            for chunk in iter(lambda: fh.read(256 * 1024), b''):
                h.update(chunk)
    except OSError:
        return ''
    return h.hexdigest()


def _under(path, base):
    """path 是否在 base 目录内（跨盘符 → False，不抛异常）。"""
    try:
        return (os.path.commonpath([os.path.abspath(str(path)),
                                    os.path.abspath(str(base))])
                == os.path.abspath(str(base)))
    except ValueError:
        return False


def _report_out_dir(model):
    lay = model.get('layout') or {}
    return str(lay.get('out_dir') or lay.get('root') or '')


def _out_digests(model):
    """报告输出目录里「报告步骤自己重画」的输入 → 内容指纹。

    viz.build_report 每次都会重画 stage_funnel*.png/svg/tsv（内容相同，
    mtime 一定变新）。只按 mtime 判新鲜，report_v2.html 永远比它们旧，
    永远重建——`main.py report` 的复用分支实际不可达。这里记下报告产出
    目录内输入的内容指纹，下一轮重画后指纹相同就视作未变。
    只对样品报告生效（out_dir 在 07_report/）；工具运行目录 out_dir=root，
    输入不是本模块重画的，继续按 mtime 判。
    """
    lay = model.get('layout') or {}
    root = str(lay.get('root') or '')
    out_dir = _report_out_dir(model)
    if (not root or not out_dir
            or os.path.normcase(os.path.abspath(root))
            == os.path.normcase(os.path.abspath(out_dir))):
        return {}
    digs = {}
    for p in lay.get('inputs') or []:
        p = str(p)
        if not _under(p, out_dir) or not os.path.isfile(p):
            continue
        dig = _file_digest(p)
        if dig:
            digs[os.path.relpath(p, out_dir).replace('\\', '/')] = dig
    return digs


def _is_fresh(html_path, model, meta_path=None):
    if not os.path.isfile(html_path):
        return False
    try:
        out_mt = os.path.getmtime(html_path)
    except OSError:
        return False

    meta = {}
    if meta_path and os.path.isfile(meta_path):
        try:
            with open(meta_path, encoding='utf-8') as fh:
                meta = json.load(fh) or {}
        except (OSError, ValueError):
            meta = {}
    out_digests = meta.get('out_digests') or {}
    lay = model.get('layout') or {}
    root = str(lay.get('root') or '')
    out_dir = _report_out_dir(model)
    check_out = (bool(root) and bool(out_dir)
                 and os.path.normcase(os.path.abspath(root))
                 != os.path.normcase(os.path.abspath(out_dir)))

    for p in lay.get('inputs') or []:
        base = os.path.basename(str(p))
        if base in _SELF_OUT or base.startswith(OUT_NAME + '.'):
            continue                      # 自己的产物不算输入
        try:
            mt = os.path.getmtime(p)
        except OSError:
            continue
        if mt <= out_mt:
            continue
        if check_out and _under(p, out_dir):
            rel = os.path.relpath(str(p), out_dir).replace('\\', '/')
            cur = _file_digest(p)
            if out_digests.get(rel) and out_digests.get(rel) == cur:
                continue                  # 本轮重画但内容没变
            # SVG 里 matplotlib 会写随机 id：重画后内容指纹必变。有同名位图
            # 孪生且位图指纹未变时，按「同一张图」放过（report_v2 内嵌的是位图）。
            if str(p).lower().endswith('.svg'):
                for ext in ('.png', '.jpg', '.jpeg', '.webp'):
                    twin = os.path.splitext(str(p))[0] + ext
                    if not os.path.isfile(twin):
                        continue
                    trel = os.path.relpath(twin, out_dir).replace('\\', '/')
                    if (out_digests.get(trel)
                            and out_digests.get(trel) == _file_digest(twin)):
                        break
                else:
                    return False
                continue
        return False
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
        # 输入未变判定用的内容指纹（只含报告输出目录内、本步骤重画的产物）
        'out_digests': _out_digests(model),
    }


def main(argv=None):
    """`python -m Virus_Platform_Core.report_kit <dir> [--force] [--open] [--pdf]`"""
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ('-h', '--help'):
        print(__doc__)
        return 0
    target = argv[0]
    force = '--force' in argv
    want_pdf = '--pdf' in argv
    try:
        out = build_enhanced_report(target, force=force)
    except Exception as e:                      # CLI 边界：原文转述，不美化
        print(f'[失败] {type(e).__name__}: {e}', file=sys.stderr)
        return 1
    print(out['html'])
    if want_pdf:
        try:
            from Virus_Platform_Core.pdf_export import html_to_pdf
            print(html_to_pdf(out['html']))
        except Exception as e:
            print(f'[PDF 失败] {type(e).__name__}: {e}', file=sys.stderr)
            return 1
    if '--open' in argv:
        try:
            os.startfile(out['html'])           # Windows 双击等效
        except Exception:
            pass
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
