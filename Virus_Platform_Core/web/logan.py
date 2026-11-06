# -*- coding: utf-8 -*-
"""LOGAN 溯源（自 app.py 拆出）。

病毒序列 → Logan-Search → 物种/样本溯源；含 Selenium 批量提交。"""
import os

from flask import (Blueprint, abort, jsonify, render_template, request,
                   send_file)

from Virus_Platform_Core.utils import (TaskLogger, check_path)
from Virus_Platform_Core.web.state import cfg
from Virus_Platform_Core.web.tasks import tm
from Virus_Platform_Core.web.common import _safe_sample

bp = Blueprint('logan', __name__)


@bp.route('/logan')
def page_logan():
    return render_template('logan.html')


@bp.route('/logan/report/<name>/')
def page_logan_report(name):
    from Virus_Platform_Core.logan_trace import _job_dir
    p = os.path.join(_job_dir(name), 'trace_report.html')
    if not os.path.isfile(p):
        abort(404, '溯源报告尚未生成（请先导入结果文件）')
    resp = send_file(check_path(p, must_exist=True, in_platform=True))
    # 报告内容由样本数据（contig 名等）拼成，属于半可信输入：加 CSP 兜底，
    # 即使未来某处转义遗漏，注入的脚本也无法读取平台 API 响应
    resp.headers['Content-Security-Policy'] = (
        "default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
        "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
        "connect-src 'self'; frame-ancestors 'self'")
    return resp


@bp.route('/logan/report/<name>/<path:filename>')
def page_logan_report_file(name, filename):
    """溯源报告目录内静态文件（plotly.min.js / 原始结果表 / 查询 FASTA）。"""
    from Virus_Platform_Core.logan_trace import _job_dir
    # base 归一 + 前缀包含校验：filename 传绝对路径时 os.path.join 会整段
    # 替换 base，check_path(in_platform=True) 拦不住读平台内其它文件。
    base = os.path.abspath(_job_dir(name))
    p = check_path(os.path.join(base, filename),
                   must_exist=True, in_platform=True)
    if not p.startswith(base + os.sep):
        abort(400, '非法路径')
    return send_file(p)


@bp.route('/api/logan/samples')
def api_logan_samples():
    """可选来源样品：已有 ③组装 病毒 contigs 产出的样品。"""
    from Virus_Platform_Core.logan_trace import list_query_samples
    return jsonify(list_query_samples())


@bp.route('/api/logan/contigs/<sample>')
def api_logan_contigs(sample):
    from Virus_Platform_Core.logan_trace import list_virus_contigs
    return jsonify(list_virus_contigs(_safe_sample(sample)))


@bp.route('/api/logan/contigs_seq', methods=['POST'])
def api_logan_contigs_seq():
    """按 样品+contig 名 批量返回序列正文（只读；溯源页已改为粘贴/文件来源，
    本端点保留给 MCP/agent 使用）。"""
    from Virus_Platform_Core.logan_trace import _load_source_fasta
    body = request.get_json(force=True) or {}
    sample = (body.get('sample') or '').strip()
    contigs = body.get('contigs') or []
    if not sample or not contigs:
        abort(400, '缺少 sample 或 contigs')
    try:
        recs = _load_source_fasta(sample=_safe_sample(sample), contig_ids=list(contigs))
    except (ValueError, OSError) as e:
        abort(400, str(e))
    return jsonify([{'contig': cid, 'seq': seq} for cid, seq in recs])


@bp.route('/api/logan/read_fasta', methods=['POST'])
def api_logan_read_fasta():
    """读入用户选择的 FASTA / 纯序列文本文件（溯源页「从文件选择」用，只读）。

    请求体 {"path": "..."} → {"name": 文件名, "text": 全文}。
    路径口径（2026-11-06 加固）：相对路径按平台根解析；绝对路径必须落在
    平台根 / 输出根 / 示例目录内（realpath 解析 junction/符号链接后再判，
    复用 check_path 的越界语义）。此前任意绝对路径都能读（≤32MB），
    包括 run/ai_local.json 里的 API Key。
    上限 32MB；前 4KB 含 NUL 判为二进制拒绝。"""
    import os as _os
    from Virus_Platform_Core.config import (
        DIRS, PLATFORM_ROOT, RUN_ROOT, _ai_local_path, _detect_examples_root)
    body = request.get_json(force=True) or {}
    raw = (body.get('path') or '').strip()
    if not raw:
        abort(400, '缺少文件路径')
    p0 = raw if _os.path.isabs(raw) else _os.path.join(PLATFORM_ROOT, raw)
    try:
        # check_path：拒绝含 .. 段 + 必须位于平台写入根内（平台根/自定义
        # 输出根/运行根等）；realpath 再解析软链接，防"根内链接指向根外"。
        p = _os.path.realpath(
            check_path(p0, must_exist=True, in_platform=True))
    except (ValueError, OSError) as e:
        abort(400, f'路径不合法: {e}')
    # 显式白名单：平台根 / 输出根 / 示例目录。check_path 的 in_platform
    # 还会放行输入根与数据库根，这里按溯源读文件的最小需要再收一层。
    roots = [PLATFORM_ROOT, DIRS.get('results'), DIRS.get('tool_runs'),
             DIRS.get('downloads'), DIRS.get('examples')]
    try:
        if _detect_examples_root():
            roots.append(_detect_examples_root())
    except Exception:                       # noqa: BLE001 - 探测失败不影响主判
        pass
    np = _os.path.normcase(p)
    ok = False
    for r in roots:
        if not r:
            continue
        try:
            nr = _os.path.normcase(_os.path.realpath(r))
        except OSError:
            continue
        if np == nr or np.startswith(nr + _os.sep):
            ok = True
            break
    if not ok:
        abort(400, '路径越界（只允许平台目录、输出目录与示例目录内的文件）')
    # 敏感路径：AI Key 真身与会话镜像永远不允许从「读 FASTA」通道外泄。
    sensitive = [_os.path.realpath(_ai_local_path()),
                 _os.path.realpath(_os.path.join(RUN_ROOT, 'ai_sessions'))]
    for sp in sensitive:
        nsp = _os.path.normcase(sp)
        if np == nsp or np.startswith(nsp + _os.sep):
            abort(400, '该路径属于平台敏感文件，不允许读取')
    if not _os.path.isfile(p):
        abort(400, f'文件不存在: {p}')
    if _os.path.getsize(p) > 32 * 1024 * 1024:
        abort(400, '文件超过 32MB 上限（请先截取需要的序列）')
    try:
        with open(p, 'rb') as f:
            head = f.read(4096)
            if b'\x00' in head:
                abort(400, '不是文本文件（含二进制字节），请选择 FASTA / 纯文本')
            rest = f.read()
    except OSError as e:
        abort(400, f'读取失败: {e}')
    return jsonify({'name': _os.path.basename(p),
                    'text': (head + rest).decode('utf-8', errors='replace')})


@bp.route('/api/logan/has_assembly', methods=['POST'])
def api_logan_has_assembly():
    """批量探测 accession 在 Logan 数据集有无组装（检索/预筛/Open-Virome
    行级「LOGAN 数据下载」按钮的存在性检查，只读）。

    请求体 {"accessions": ["SRR...", ...]}（≤50 个）→
    {"SRR...": true/false, ...}；S3 HEAD 匿名查询，非 SRA 系（如 GSA/CRR）
    必然 false。"""
    from Virus_Platform_Core.logan_contigs import (_check_host, ACC_MAX,
                                                   _acc_clean)
    body = request.get_json(force=True) or {}
    raw = body.get('accessions') or []
    if not isinstance(raw, list):
        abort(400, 'accessions 应为数组')
    try:
        accs = [_acc_clean(a) for a in raw][:ACC_MAX]
    except ValueError as e:
        abort(400, str(e))
    if not accs:
        abort(400, '缺少 accessions')
    _check_host()
    try:
        import boto3
        from botocore import UNSIGNED
        from botocore.config import Config
    except ModuleNotFoundError:
        abort(400, '缺少 Python 依赖 boto3（Logan S3 匿名访问用）——'
                   '请在平台 Python 里执行: python -m pip install boto3'
                   '（requirements.txt 已声明，可 pip install -r requirements.txt）')
    s3 = boto3.client('s3', endpoint_url='https://s3.amazonaws.com',
                      config=Config(signature_version=UNSIGNED,
                                    retries={'max_attempts': 1}))
    out = {}
    from Virus_Platform_Core.logan_contigs import BUCKET
    for a in accs:
        if a in out:
            continue
        try:
            s3.head_object(Bucket=BUCKET, Key=f'c/{a}/{a}.contigs.fa.zst')
            out[a] = True
        except Exception:
            out[a] = False
    return jsonify(out)


@bp.route('/api/logan/jobs')
def api_logan_jobs():
    from Virus_Platform_Core.logan_trace import list_jobs
    return jsonify(list_jobs())


@bp.route('/api/logan/job/<name>')
def api_logan_job(name):
    from Virus_Platform_Core.logan_trace import job_detail
    return jsonify(job_detail(name))


@bp.route('/api/logan/job/<name>/segment/<int:idx>')
def api_logan_segment(name, idx):
    """片段序列（前端复制提交用）。"""
    from Virus_Platform_Core.logan_trace import get_segment_fasta
    header, seq = get_segment_fasta(name, idx)
    return jsonify({'header': header, 'seq': seq})


@bp.route('/api/logan/job/<name>/query')
def api_logan_query_dl(name):
    """下载查询 FASTA（全部片段）。"""
    from Virus_Platform_Core.logan_trace import _job_dir
    p = os.path.join(_job_dir(name), 'query_all.fasta')
    if not os.path.isfile(p):
        abort(404, '查询 FASTA 不存在')
    return send_file(check_path(p, must_exist=True, in_platform=True),
                     as_attachment=True)


@bp.route('/api/logan/create', methods=['POST'])
def api_logan_create():
    from Virus_Platform_Core.logan_trace import create_job
    body = request.get_json(force=True) or {}
    name = (body.get('name') or '').strip()
    if not name:
        abort(400, '请填写查询名称')
    pasted = (body.get('fasta') or '').strip()
    sample = (body.get('sample') or '').strip()
    contigs = body.get('contigs') or []
    if not pasted and not (sample and contigs):
        abort(400, '请勾选样品的病毒 contigs，或粘贴序列')
    try:
        # ⚠️ sample 为空时必须给 None：_safe_sample('') 会回退成默认名
        # 'sample'（truthy），`or None` 兜不住，粘贴模式也会被拽去读
        # results/sample/03_assembly/virus_contigs.tsv → 必 400。
        # max_len：网页版一键批量用——服务端按 ≤max_len 全量均分切片
        # （与页面片段生成同口径）；不给则维持旧 n_seg 采样窗口径。
        max_len = body.get('max_len')
        max_len = min(100000, max(100, int(max_len))) if max_len else None
        return jsonify(create_job(name, sample=_safe_sample(sample) if sample else None,
                                  contig_ids=contigs or None,
                                  pasted=pasted or None,
                                  n_seg=int(body.get('n_seg') or 2),
                                  max_len=max_len))
    except ValueError as e:
        abort(400, str(e))


@bp.route('/api/logan/job/<name>/import/<int:idx>', methods=['POST'])
def api_logan_import(name, idx):
    """导入某片段的 Logan-Search 结果表（CSV/TSV），解析并自动生成报告。"""
    from Virus_Platform_Core.logan_trace import import_result, MAX_UPLOAD_BYTES
    f = request.files.get('file')
    if not f:
        abort(400, '缺少结果文件')
    raw = f.read(MAX_UPLOAD_BYTES + 1)
    if len(raw) > MAX_UPLOAD_BYTES:
        abort(400, '结果文件超过 64MB 上限')
    if not raw.strip():
        abort(400, '结果文件为空')
    try:
        return jsonify(import_result(name, idx, f.filename or '', raw))
    except ValueError as e:
        abort(400, str(e))


@bp.route('/api/logan/job/<name>/delete', methods=['POST'])
def api_logan_delete(name):
    from Virus_Platform_Core.logan_trace import delete_job
    delete_job(name)
    return jsonify({'ok': True})


@bp.route('/api/logan/batch_ready')
def api_logan_batch_ready():
    """批量模式可用性（selenium 是否已安装）。"""
    from Virus_Platform_Core.logan_trace import selenium_ready, GROUP_HINTS
    return jsonify({'selenium': selenium_ready(), 'groups': GROUP_HINTS})


@bp.route('/api/logan/job/<name>/batch', methods=['POST'])
def api_logan_batch(name):
    """后台批量提交任务的全部未导入片段，完成后自动导入并生成报告。"""
    from Virus_Platform_Core.logan_trace import batch_submit, _load_job
    _jdir, meta, segs = _load_job(name)
    if not any(not s.get('imported') for s in segs):
        abort(400, '该任务所有片段均已导入结果')
    for t in tm.list_all():
        if t.get('status') == 'running' and t.get('name') in {
            f'LOGAN 批量 {meta["name"]}',
            f'LOGAN batch {meta["name"]}',
        }:
            abort(400, f'任务 {meta["name"]} 已有批量提交在运行，请等它结束')
    body = request.get_json(force=True) or {}
    emails = [e.strip() for e in (body.get('emails') or '').split(',') if e.strip()]
    if not emails:
        abort(400, '请至少填写一个通知邮箱（用于接收 Logan-Search 完成通知）')
    group = body.get('group') or 'Fast_No_human'

    def job(log, prog, cancel):
        logger = TaskLogger(callback=log)
        batch_submit(meta['name'], emails, group=group,
                     headless=bool(body.get('headless', True)),
                     first_wait=int(body.get('first_wait') or 300),
                     max_wait=int(body.get('max_wait') or 1800),
                     logger=logger, progress=prog, cancel=cancel)
        logger.close()
        return 'ok'

    tid = tm.start(cfg.tr(f'LOGAN 批量 {meta["name"]}',
                         f'LOGAN batch {meta["name"]}'), job,
                   weight='light')
    return jsonify({'task': tid})
