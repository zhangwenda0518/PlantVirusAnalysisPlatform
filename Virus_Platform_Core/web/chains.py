# -*- coding: utf-8 -*-
"""链式一键分析：把多个任务工厂串成一次运行（自 app.py 拆出）。

设计：每步仍调用 Virus_Platform_Core/web/tool_jobs.py 的现有 _tool_job_*，各建
自己的 run_dir，一步不改；汇总目录只放「符号链接 + 清单」，
把一次多步运行的结果收拢成一份。
"""
import json
import os
import re
import time
import traceback
import uuid
from pathlib import Path

from flask import abort

from Virus_Platform_Core.config import PLATFORM_ROOT
from Virus_Platform_Core.utils import (TaskLogger, check_path, safe_open)
from Virus_Platform_Core.web.state import cfg, tool_runs_root as _tool_runs_root
from Virus_Platform_Core.web.tool_jobs import (
    _tool_job_identify,
    _tool_job_assemble,
    _tool_job_contigs,
    _tool_job_verify,
    _tool_job_kvsuite,
    _tool_job_convert,
    _tool_job_fastp,
    _tool_job_hostremoval,
    _tool_job_orf,
    _tool_job_orfa,
    _tool_job_genoplot,
)


_CHAIN_LINKABLE = ('.fasta', '.fa', '.fna', '.tsv', '.json', '.txt',
                   '.vcf', '.gz', '.html', '.pdf', '.png')


def _chain_link(dst, src, logger=None, target_is_dir=None):
    """在汇总目录建链接指向 src。

    Windows 下 os.symlink 需开发者模式/管理员；不可用时按序降级：
    目录 → 硬链接不可用则跳过并记录；文件 → 硬链接；再不行则跳过。
    返回实际使用的方式（'symlink' / 'hardlink' / None），失败不抛异常。
    """
    if not os.path.exists(src):
        return None
    if target_is_dir is None:
        target_is_dir = os.path.isdir(src)
    try:
        os.symlink(src, dst, target_is_directory=bool(target_is_dir))
        return 'symlink'
    except (OSError, NotImplementedError, AttributeError):
        pass
    if not target_is_dir:
        try:
            os.link(src, dst)
            return 'hardlink'
        except OSError:
            pass
    if logger:
        logger.log(f'链接创建跳过（源或权限不可用）: {os.path.basename(dst)}')
    return None


def _chain_write_manifest(chain_dir, meta):
    p = os.path.join(chain_dir, '_chain.json')
    with safe_open(p, 'wt') as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    return p


def _chain_run_step(chain_dir, root, step_key, step_name, job_factory,
                    p, threads, db_virus, logger, prog, prog_lo, prog_hi,
                    cancel=None, collect=None, optional=()):
    """跑链中一步：新建 run_dir、构造 ctx、调用 job，返回 (run_name, result)。

    prog 为阶段名；进度在 [prog_lo, prog_hi] 区间内线性映射。
    collect: 该步要链接进汇总目录的产物文件名列表（可选）。
    optional: 这些 key 就算下游 job 用 ctx.req 取也允许为空（返回 None），
              用于兼容现有 job 把可选输入写成必填的情况。
    """
    from types import SimpleNamespace
    ts = time.strftime('%Y%m%d_%H%M%S')
    # 秒级时间戳会撞车：同一秒内两次链（双击/多标签页）会复用同一步
    # 运行目录并互相覆盖产物。追加 4 位随机后缀（口径同 tools_api 的
    # /api/tool/run 建运行名）。
    run_name = f'{step_key}_{ts}_{uuid.uuid4().hex[:4]}'
    run_dir = check_path(os.path.join(root, run_name), must_exist=False,
                         in_platform=True)
    os.makedirs(run_dir, exist_ok=True)

    def _input_abs(v):
        return check_path(v if os.path.isabs(v)
                          else os.path.join(PLATFORM_ROOT, v),
                          must_exist=True)

    def _req(key, what):
        v = (p.get(key) or '').strip()
        if not v:
            if key in optional:
                return None
            abort(400, f'请选择{what}')
        return _input_abs(v)

    def _opt(key):
        v = (p.get(key) or '').strip()
        return _input_abs(v) if v else None

    ctx = SimpleNamespace(p=p, run_dir=run_dir, threads=threads,
                          db_virus=db_virus, req=_req, opt=_opt)
    job = job_factory(ctx)

    def _sub_prog(_stage, frac, msg):
        f = prog_lo + max(0.0, min(1.0, float(frac))) * (prog_hi - prog_lo)
        prog(step_key, f, msg)

    prog(step_key, prog_lo, f'{step_name}：开始')
    res = job(lambda m: logger.log(m), _sub_prog, cancel)
    logger.log(f'{step_name} 完成: run={run_name}')

    # 目录链接（整步产物）
    _chain_link(os.path.join(chain_dir, f'step_{step_key}'), run_dir,
                logger=logger, target_is_dir=True)
    # 关键文件链接
    linked = []
    for fn in (collect or ()):
        src = os.path.join(run_dir, fn)
        if os.path.isfile(src):
            how = _chain_link(os.path.join(chain_dir, f'{step_key}__{fn}'),
                              src, logger=logger, target_is_dir=False)
            if how:
                linked.append(fn)
    return run_name, res, linked


_VIR_STEP_KEYS = ('1', '2', '3', '4')
_VIR_STEP_LABEL = {
    '1': '① 病毒组装',
    '2': '② 病毒鉴定',
    '3': '③ 候选序列验证',
}
# 2026-11-03：① 病毒序列提取（样本分析）移出本链（挪入样本处理组，作为
# 独立的快筛工具）；本链 = ①组装 → ②病毒鉴定 → ③验证，输入固定为去宿主 FASTA。
_VIR_STEP_W = {'1': 0.40, '2': 0.30, '3': 0.30}
_VIR_KEY = {'1': 'assembly', '2': 'contigs', '3': 'verify'}


def _vir_steps_arg(ctx):
    """解析「跑哪几步」：steps='1,2,3'（也接受列表），未传 = 全链三步。

    旧编号（1识别/2组装/3再鉴定/4验证）已废止：过滤后若只剩新 2/3 会因
    缺前置被 _vir_validate_steps 拦下，报错文案里带新编号对照。
    """
    raw = ctx.p.get('steps')
    if raw is None:
        return {'1', '2', '3'}
    if isinstance(raw, str):
        raw = re.split(r'[;,\s]+', raw)
    sel = set()
    for x in raw:
        s = str(x).strip()
        if s in _VIR_STEP_KEYS:
            sel.add(s)
    return sel


def _vir_validate_steps(sel):
    if not sel:
        abort(400, '请至少勾选一步：① 病毒组装 / ② 病毒鉴定 / '
                   '③ 候选序列验证')
    if '2' in sel and '1' not in sel:
        abort(400, '② 病毒鉴定 需要 ① 病毒组装 的 contigs，'
                   '请一并勾选 ①')
    if '3' in sel and '2' not in sel:
        abort(400, '③ 候选序列验证 需要 ② 病毒鉴定 产出的 viral_contigs，'
                   '请一并勾选 ②')
    return sel


def _virchain_run_sample(chain_dir, root, sample, sel, *, asm_mode,
                         asm_assembler, asm_megahit_mode, mem, min_len, conf,
                         host, combine, methods, threads, db_virus, logger,
                         prog, prog_lo, prog_hi, cancel=None, prefix='',
                         label=''):
    """跑一个样本的选中步骤（①组装 → ②病毒鉴定 → ③验证，自由勾选）。

    产物链接进 chain_dir（批量时是 <chain>/<样本>/），返回
    {'steps': [...], 'links': [...], 'pe': bool}。步骤失败直接抛异常，
    由调用方决定是整链失败（单样本）还是记 failed 继续（批量）。
    """
    def _title(t):
        return ('%s · %s' % (label, t)) if label else t

    steps, links = [], []
    tot = sum(_VIR_STEP_W[k] for k in sel)
    cur = 0.0

    def _band(step):
        nonlocal cur
        lo = cur
        w = (_VIR_STEP_W[step] / tot) if tot else 0.0
        cur = lo + w
        return (prog_lo + lo * (prog_hi - prog_lo),
                prog_lo + cur * (prog_hi - prog_lo))

    asm_in1 = sample['r1']
    asm_in2 = sample.get('r2') or None

    # ---- ① 病毒组装 ----
    if '1' in sel:
        lo, hi = _band('1')
        p2 = {'r1': asm_in1, 'mode': asm_mode, 'memory': mem,
              'min_len': min_len, 'assembler': asm_assembler,
              'megahit_mode': asm_megahit_mode}
        if asm_in2:
            p2['r2'] = asm_in2
        run1, res1, _ = _chain_run_step(
            chain_dir, root, prefix + 'assembly', _title(_VIR_STEP_LABEL['1']),
            _tool_job_assemble, p2, threads, db_virus, logger, prog, lo, hi,
            cancel=cancel, collect=('contigs.filtered.fasta',),
            optional=('r2',))
        contigs = (res1 or {}).get('contigs')
        steps.append({'key': prefix + 'assembly', 'run': run1,
                      'title': _title(_VIR_STEP_LABEL['1']),
                      'summary': _pc_summary(res1)})
        if not contigs or not os.path.isfile(contigs):
            raise RuntimeError('① 组装未产出 contigs，后续步骤无法继续')
        if '2' not in sel:
            logger.log('未勾选 ②，组装到此结束（contigs: %s）'
                       % os.path.basename(contigs))
    else:
        contigs = None

    # ---- ② 病毒鉴定 ----
    viral_fa = None
    run2 = None
    if '2' in sel:
        lo, hi = _band('2')
        p3 = {'contigs': contigs, 'min_len': min_len, 'confidence': conf}
        run2, res2, _ = _chain_run_step(
            chain_dir, root, prefix + 'contigs', _title(_VIR_STEP_LABEL['2']),
            _tool_job_contigs, p3, threads, db_virus, logger, prog, lo, hi,
            cancel=cancel,
            collect=('virus_classification.tsv', 'viral_contigs.fasta'))
        viral_fa = (res2 or {}).get('viral_fasta')
        steps.append({'key': prefix + 'contigs', 'run': run2,
                      'title': _title(_VIR_STEP_LABEL['2']),
                      'summary': _pc_summary(res2)})

    # ---- ③ 候选序列验证（依赖 ② 的产物） ----
    if '3' in sel:
        if not viral_fa or not os.path.isfile(viral_fa):
            logger.log('② 未提取到病毒 contig，跳过 ③ 候选序列验证')
            steps.append({'key': prefix + 'verify', 'run': None,
                          'title': _title(_VIR_STEP_LABEL['3']),
                          'skipped': '② 未产出 viral_contigs.fasta'})
        else:
            lo, hi = _band('3')
            p4 = {'run': run2, 'host': host, 'combine': combine,
                  'methods': methods}
            run3, res3, _ = _chain_run_step(
                chain_dir, root, prefix + 'verify',
                _title(_VIR_STEP_LABEL['3']), _tool_job_verify, p4,
                threads, db_virus, logger, prog, lo, hi, cancel=cancel,
                collect=('verify_summary.json',))
            steps.append({'key': prefix + 'verify', 'run': run3,
                          'title': _title(_VIR_STEP_LABEL['3']),
                          'summary': _pc_summary(res3)})

    # 汇总样本目录里的产物（_chain_run_step 的 collect 链接）
    wanted = tuple(prefix + _VIR_KEY[k] + '__' for k in sel)
    try:
        for fn in sorted(os.listdir(chain_dir)):
            if (wanted and fn.startswith(wanted)
                    and os.path.isfile(os.path.join(chain_dir, fn))
                    and fn not in links):
                links.append(fn)
    except OSError:
        pass
    return {'steps': steps, 'links': links, 'pe': bool(sample.get('r2'))}


def _tool_job_virchain(ctx):
    """病毒识别和分类分析 · 一键：①组装 → ②病毒鉴定 → ③验证 自由组合。

    输入：去宿主序列 FASTA（单/双端），或输入目录批量（只收 FASTA）。
    FASTQ 请先到「样本处理」组：非宿主序列提取（宿主去除）→ 转 FASTA，
    或病毒序列提取（样本分析）提取病毒序列。
    2026-11-03：① 病毒序列提取（样本分析）移出本链（挪入样本处理组），
    步骤重编号 ①组装 ②病毒鉴定 ③验证；③ 依赖 ②、② 依赖 ①（缺前置直接
    400 说明）。每步各建 run_dir，汇总目录放符号链接 + _chain.json。
    """
    root = _tool_runs_root()
    p = ctx.p
    inp = ctx.opt('input')
    inp2 = ctx.opt('input2')
    indir = ctx.opt('input_dir') or ctx.opt('folder')
    picks = p.get('samples') or []
    if isinstance(picks, str):
        picks = [s for s in re.split(r'[;,\s]+', picks) if s.strip()]
    picks = [str(s).strip() for s in picks if str(s).strip()]
    sel = _vir_validate_steps(_vir_steps_arg(ctx))

    asm_mode = (p.get('mode') or 'rnaviral').strip()
    asm_assembler = (p.get('assembler') or 'auto').strip().lower()
    asm_megahit_mode = (p.get('megahit_mode') or 'default').strip().lower()
    mem = int(p.get('memory') or 0)
    if not mem:
        from Virus_Platform_Core.assembly import default_assembler_memory_gb
        mem = default_assembler_memory_gb()
    min_len = int(p.get('min_len') or 200)
    conf = float(p.get('confidence') or 0)
    host = (p.get('host') or 'all').strip() or 'all'
    combine = (p.get('combine') or 'union').strip()
    methods = p.get('methods') or ['blastx', 'bfvd', 'cdd']
    if isinstance(methods, str):
        methods = [m.strip() for m in methods.split(',') if m.strip()]
    methods = [m for m in methods if m in ('blastx', 'bfvd', 'cdd')]

    mode = 'single'
    if indir:
        scan = _prep_pick_samples(indir, picks, with_fasta=True,
                                  allow='FASTA / FASTQ[.gz] / .sra')
        samples = scan['samples']
        mode = 'batch'
        bad = [s['name'] for s in samples if s.get('kind') != 'fasta']
        if bad:
            abort(400, '本链只收去宿主 FASTA；这些样本是 FASTQ/.sra，'
                       '请先到「样本处理」组：非宿主序列提取（宿主去除）'
                       '→ 转 FASTA，或病毒序列提取（样本分析）：%s'
                       % '、'.join(bad[:5]))
    else:
        if not inp:
            abort(400, '请选择输入文件（去宿主序列 FASTA）或输入目录')
        name = _pc_sample_name(os.path.basename(inp))
        is_fa = inp.lower().endswith(_FASTA_EXTS)
        if not is_fa:
            abort(400, '本链只收去宿主 FASTA（FASTQ 请先到「样本处理」组'
                       '做非宿主序列提取或病毒序列提取）')
        samples = [{'name': name, 'kind': ('fasta' if is_fa else 'fastq'),
                    'pe': bool(inp2), 'r1': inp, 'r2': inp2 or '',
                    'size': _pc_fsize(inp) + (_pc_fsize(inp2) if inp2 else 0),
                    'status': 'done', 'links': []}]

    def job(log, prog, cancel):
        logger = TaskLogger(callback=log)
        chain_dir = ctx.run_dir
        chain_name = os.path.basename(chain_dir)
        meta = {'chain': 'virchain', 'run': chain_name,
                'label': '病毒识别和分类分析 一键流程',
                'mode': mode, 'input_dir': indir or '',
                'n_samples': len(samples),
                'input': inp or '', 'input2': inp2 or '',
                'params': {
                    'steps': ','.join(sorted(sel)),
                    'mode': asm_mode, 'assembler': asm_assembler,
                    'megahit_mode': asm_megahit_mode, 'memory_gb': mem,
                    'min_len': min_len, 'confidence': conf,
                    'host': host, 'combine': combine,
                    'methods': ', '.join(methods) if methods else '—',
                },
                'created': time.strftime('%Y-%m-%d %H:%M:%S'),
                'steps': [], 'samples': []}
        n = len(samples)
        ok = 0
        rows = []
        tsv = os.path.join(chain_dir, 'samples.tsv')
        try:
            fh = safe_open(tsv, 'wt')
            fh.write('sample\ttype\tstatus\tbytes\tsteps\tR1\tR2\tfiles\terror\n')
            fh.close()
        except OSError:
            tsv = ''
        for i, s in enumerate(samples):
            name = s['name']
            single = (mode == 'single')
            sdir = chain_dir if single else os.path.join(chain_dir, name)
            os.makedirs(sdir, exist_ok=True)
            lo = float(i) / n
            hi = float(i + 1) / n
            prog('virchain', lo, '样本 %s（%d/%d）'
                 % (name, i + 1, n)) if not single else None
            rec = {'name': name, 'kind': s.get('kind'),
                   'pe': bool(s.get('r2')), 'r1': s.get('r1') or '',
                   'r2': s.get('r2') or '', 'size': s.get('size') or 0,
                   'steps': ','.join(sorted(sel)), 'status': 'done'}
            try:
                r = _virchain_run_sample(
                    sdir, root, s, sel, asm_mode=asm_mode,
                    asm_assembler=asm_assembler,
                    asm_megahit_mode=asm_megahit_mode, mem=mem,
                    min_len=min_len, conf=conf, host=host, combine=combine,
                    methods=methods, threads=ctx.threads,
                    db_virus=ctx.db_virus, logger=logger, prog=prog,
                    prog_lo=lo, prog_hi=hi, cancel=cancel,
                    prefix=(name + '__') if not single else '',
                    label='' if single else name)
                meta['steps'].extend(r['steps'])
                rec['links'] = r['links']
                ok += 1
            except Exception as e:                    # noqa: BLE001
                logger.log(traceback.format_exc())
                rec['status'] = 'failed'
                rec['error'] = '%s: %s' % (type(e).__name__, e)
                meta['failed_samples'] = meta.get('failed_samples', 0) + 1
                if single or _pc_cancelled(cancel):
                    rows.append(rec)
                    meta['samples'] = [rec]
                    try:
                        _chain_write_manifest(chain_dir, meta)
                    except Exception:
                        pass
                    raise
            meta['samples'].append(rec)
            rows.append(rec)

        meta['ok_samples'] = ok
        meta.setdefault('failed_samples', 0)
        meta['done'] = True
        prog('chain', 1.0, '一键流程完成')
        logger.close()
        _chain_write_manifest(chain_dir, meta)
        if tsv:
            try:
                fh = safe_open(tsv, 'wt')
                fh.write('sample\ttype\tstatus\tbytes\tsteps\tR1\tR2\t'
                         'files\terror\n')
                for r in rows:
                    fh.write('\t'.join([
                        r.get('name') or '', r.get('kind') or '',
                        r.get('status') or '', str(r.get('size') or 0),
                        r.get('steps') or '', r.get('r1') or '',
                        r.get('r2') or '',
                        ';'.join(r.get('links') or []),
                        (r.get('error') or '').replace('\t', ' '),
                    ]) + '\n')
                fh.close()
            except OSError:
                pass
        if ok == 0:
            raise RuntimeError('批量处理全部失败（%d 个样本，详见运行日志）' % n)
        out = {'chain': chain_name, 'run': chain_name, 'mode': mode,
               'n_samples': n, 'ok_samples': ok,
               'failed_samples': meta.get('failed_samples', 0),
               'n_steps': len(meta['steps']), 'steps': meta['steps'],
               'samples': meta['samples'], 'chain_json': os.path.join(
                   chain_dir, '_chain.json')}
        if tsv:
            out['samples_tsv'] = tsv
        if ok < n:
            out['note'] = '部分样本失败 %d 个（详见 samples.tsv）' % (n - ok)
        return out
    return job


def _tool_job_kvchain(ctx):
    """病毒定量与共识 · 一键：调用 kvsuite 全流程（鉴定→过滤→共识→绘图→变异）。

    kvsuite 卡里的 stage 下拉默认已是 all，本 job 把「一次跑完并收拢产物」
    做成显式入口：建汇总目录 + 链接 kvsuite run_dir + 关键产物。

    2026-11-02：与「一键前处理」对齐，支持输入目录批量——目录里的 FASTQ
    按 `_R1`/`_R1_001`/`_1` 命名解析单双端（复用 prepchain 的扫描器），
    逐样本写 sample_sheet 各跑一次 kvsuite，单样本失败不影响其余样本。
    """
    # kvchain 是 kvsuite 的 stage=all 封装，参数 schema 直接复用 kvsuite
    # （agent_catalog 里 TOOL_PARAM_SCHEMAS['kvchain'] = kvsuite 的那份）。
    # stage=all 走的 samples/reads/sample_sheet 分支在**任务线程内**才 abort，
    # 且 JobCtx 只在有 Flask 请求上下文时才转成真正的 HTTP 400——所以这里在
    # 提交时先做一次同款互斥校验，让一键入口的表现和其它工具箱工具一致。
    p = ctx.p
    indir = ctx.opt('input_dir') or ctx.opt('folder')
    if indir:
        if any((p.get(k) or '').strip() for k in ('reads', 'sample_sheet')):
            abort(400, '目录批量与「直接输入测序数据」/「样本表 TSV」只能选一种')
        picks = p.get('samples') or []
        if isinstance(picks, str):
            picks = [s for s in re.split(r'[;,\s]+', picks) if s.strip()]
        scan = _prep_pick_samples(indir, picks)
        sras = [s['name'] for s in scan['samples'] if s['kind'] == 'sra']
        if sras:
            abort(400, '目录里的 .sra 需要先转 FASTQ（可用「一键前处理」或'
                       '「格式转换」），本卡只接受 FASTQ：%s' % '、'.join(sras[:5]))
        mode = 'batch'
        samples = scan['samples']
    else:
        if not any((p.get(k) or '').strip()
                   for k in ('samples', 'reads', 'sample_sheet')):
            abort(400, '请选择样品、直接输入测序数据（FASTQ）、样本表 TSV，或输入目录')
        mode = 'single'
        samples = []
    root = _tool_runs_root()

    def job(log, prog, cancel):
        logger = TaskLogger(callback=log)
        chain_dir = ctx.run_dir
        chain_name = os.path.basename(str(chain_dir))
        # kvsuite 的 stage 固定为 all（一键语义）；目录/勾选参数不下传
        base_p = dict(p)
        base_p['stage'] = 'all'
        for k in ('input_dir', 'folder', 'samples'):
            base_p.pop(k, None)
        meta = {'chain': 'kvchain', 'run': chain_name,
                'label': '病毒定量与共识 一键流程',
                'mode': mode, 'input_dir': indir or '',
                'n_samples': len(samples) or 1,
                'params': {k: base_p.get(k) for k in
                           ('engine', 'kv_lib', 'reference', 'min_cov',
                            'min_depth', 'min_reads', 'min_poisson',
                            'variant_qual', 'min_freq', 'aa_label_cutoff',
                            'max_aa_labels', 'no_genes', 'no_variant_evo',
                            'all_variants', 'no_snpgenie')
                           if base_p.get(k) not in (None, '')},
                'created': time.strftime('%Y-%m-%d %H:%M:%S'),
                'steps': [], 'samples': []}

        if mode == 'single':
            run1, res1, _linked = _chain_run_step(
                chain_dir, root, 'kvsuite', '已知病毒识别与定量（全流程）',
                _tool_job_kvsuite, base_p, ctx.threads, ctx.db_virus,
                logger, prog, 0.0, 1.0, cancel=cancel,
                collect=('run.log',))
            meta['steps'].append({'key': 'kvsuite', 'run': run1,
                                  'title': '已知病毒识别与定量（全流程）',
                                  'summary': {k: v for k, v in (res1 or {}).items()
                                              if not isinstance(v, (list, dict))}})
            prog('chain', 1.0, '一键流程完成')
            logger.close()
            meta['done'] = True
            _chain_write_manifest(chain_dir, meta)
            return {'chain': chain_name, 'run': chain_name, 'mode': 'single',
                    'n_samples': 1, 'n_steps': 1, 'steps': meta['steps'],
                    'kvsuite_run': run1,
                    'chain_json': str(os.path.join(str(chain_dir), '_chain.json'))}

        n = len(samples)
        ok = 0
        for i, s in enumerate(samples):
            name = s['name']
            sdir = os.path.join(str(chain_dir), name)
            os.makedirs(sdir, exist_ok=True)
            # kvsuite 的批量入口认 sample_sheet.tsv（name/r1/r2），逐样本写一份
            sheet = os.path.join(sdir, 'input.tsv')
            with safe_open(sheet, 'wt') as f:
                f.write('name\tr1\tr2\n')
                f.write('%s\t%s\t%s\n' % (name, s['r1'], s.get('r2') or ''))
            sp = dict(base_p)
            sp['sample_sheet'] = sheet
            lo = float(i) / n
            hi = float(i + 1) / n
            prog('chain', lo, '[%d/%d] %s（%s）'
                 % (i + 1, n, name, _pc_kind_label(s)))
            t0 = time.time()
            try:
                run_i, res_i, _linked = _chain_run_step(
                    sdir, root, name + '__kvsuite',
                    '%s · 已知病毒识别与定量（全流程）' % name,
                    _tool_job_kvsuite, sp, ctx.threads, ctx.db_virus,
                    logger, prog, lo, hi, cancel=cancel, collect=('run.log',))
            except Exception as e:
                if _pc_cancelled(cancel):
                    logger.log('已取消：批量处理停止')
                    raise
                msg = str(e) or e.__class__.__name__
                logger.log('[%s] 失败：%s: %s' % (name, e.__class__.__name__, msg))
                logger.log(traceback.format_exc())
                meta['samples'].append({
                    'name': name, 'kind': s['kind'], 'pe': bool(s.get('pe')),
                    'r1': s['r1'], 'r2': s.get('r2') or '',
                    'size': s.get('size') or 0,
                    'status': 'failed', 'error': msg,
                    'steps': [], 'links': [],
                })
                continue
            links = []
            kvout = os.path.join(root, run_i, 'kvsuite')
            for rel, lab in (('filter/filtered.tsv', 'filtered.tsv'),
                             ('identify/all_viruses.summary.tsv',
                              'identify_summary.tsv'),
                             ('variant_summary.json', 'variant_summary.json')):
                src = os.path.join(kvout, rel)
                if os.path.isfile(src) and _chain_link(
                        os.path.join(sdir, lab), src, logger):
                    links.append(lab)
            meta['steps'].append({
                'key': name + '__kvsuite', 'run': run_i,
                'title': '%s · 已知病毒识别与定量（全流程）' % name,
                'summary': {k: v for k, v in (res_i or {}).items()
                            if not isinstance(v, (list, dict))}})
            meta['samples'].append({
                'name': name, 'kind': s['kind'], 'pe': bool(s.get('pe')),
                'r1': s['r1'], 'r2': s.get('r2') or '',
                'size': s.get('size') or 0,
                'status': 'done', 'error': '',
                'duration_s': round(time.time() - t0, 1),
                'run': run_i, 'n_confirmed': (res_i or {}).get('n_confirmed'),
                'links': links,
            })
            ok += 1
        if ok == 0:
            raise RuntimeError('批量处理全部失败（%d 个样本，详见运行日志）' % n)
        tsv = os.path.join(str(chain_dir), 'samples.tsv')
        with safe_open(tsv, 'wt') as f:
            f.write('sample\ttype\tstatus\tbytes\tconfirmed\tR1\tR2\tfiles\terror\n')
            for smp in meta['samples']:
                f.write('%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' % (
                    smp['name'], _pc_kind_label(smp), smp['status'],
                    smp.get('size') or 0, smp.get('n_confirmed') or '',
                    smp['r1'], smp['r2'], ';'.join(smp.get('links') or []),
                    (smp['error'] or '').replace('\t', ' ')))
        meta['ok_samples'] = ok
        meta['failed_samples'] = n - ok
        meta['done'] = True
        logger.log('一键流程完成：成功 %d/%d' % (ok, n))
        prog('chain', 1.0, '批量处理完成：成功 %d/%d' % (ok, n))
        logger.close()
        _chain_write_manifest(chain_dir, meta)
        out = {
            'chain': chain_name, 'run': chain_name, 'mode': 'batch',
            'n_samples': n, 'ok_samples': ok,
            'n_steps': len(meta['steps']), 'steps': meta['steps'],
            'samples': meta['samples'], 'samples_tsv': tsv,
            'total_bytes': sum(s.get('size') or 0 for s in samples),
            'chain_json': str(os.path.join(str(chain_dir), '_chain.json')),
        }
        if ok < n:
            out['note'] = '部分样本失败 %d 个（详见 samples.tsv）' % (n - ok)
        return out
    return job


# ---------------------------------------------------------------------------
# 样本处理 · 一键前处理：单文件 / 输入目录批量
# ---------------------------------------------------------------------------
# 目录批量先按命名规则解析单双端，再逐样本顺序跑
# （转换 → 质控 → FASTA → 宿主去除）；单个样本失败不影响其余样本。
# 每个样本的产物链接进 <chain>/<sample>/，汇总写 _chain.json
# （mode / input_dir / n_samples / samples[]）与 samples.tsv。
_PC_FQ_EXTS = ('.fastq', '.fq', '.fastq.gz', '.fq.gz')
_PC_FQ_EXT_RX = r'\.(?:fastq|fq)(?:\.gz)?$'
# 病毒识别和分类「一键」支持直接吃 FASTA（去宿主序列 / contigs）
_FASTA_EXTS = ('.fa', '.fasta', '.fna', '.fas', '.fa.gz', '.fasta.gz',
               '.fna.gz', '.fas.gz')
_FSTA_EXT_RX = r'\.(?:fa|fasta|fna|fas)(?:\.gz)?$'
# R1 识别规则（按顺序尝试；命中即用 _pc_mate_name 推 R2 名）：
#   通用 `_R1`/`.R1`/`-R1` → Illumina `_R1_001`（带 _L001/_S1 前缀也无妨）
#   → ENA/DDBJ `_1`（如 DRR1175147_1.fastq.gz）
_PC_R1_RULES = (
    re.compile(r'^(?P<stem>.+?)[_.\-]R1' + _PC_FQ_EXT_RX, re.I),
    re.compile(r'^(?P<stem>.+?)[_.\-]R1[_.\-]\d{3}' + _PC_FQ_EXT_RX, re.I),
    re.compile(r'^(?P<stem>.+?)[_.\-]1' + _PC_FQ_EXT_RX, re.I),
)
# 找不到 R1 的 R2 形态（孤儿，跳过而不是当单端跑）
_PC_R2_LIKE = (
    re.compile(r'^.+?[_.\-]R2(?:[_.\-]\d{3})?' + _PC_FQ_EXT_RX, re.I),
    re.compile(r'^.+?[_.\-]2' + _PC_FQ_EXT_RX, re.I),
)
# 样本名：剥掉 R1/R2 与 lane/set 尾巴（NX-5_S2_L001_R1_001 → NX-5_S2）
# 注意 ENA 的 `_1`/`_2` 只有在「它本身就是配对标记」时才剥：`Dataset_1_R1`
# 里那个 `_1` 是样本名的一部分，剥掉会让 Dataset_1/Dataset_2 都变成 Dataset。
_PC_R_SUFFIX = re.compile(r'[_.\-](?:L00\d[_.\-])?R[12](?:[_.\-]\d{3})?$', re.I)
_PC_ENA_SUFFIX = re.compile(r'[_.\-][12]$')
_PC_ENA_PAIR = re.compile(r'[_.\-]1' + _PC_FQ_EXT_RX, re.I)


def _pc_mate_name(fn, rx):
    """按 R1 规则推出 R2 文件名（前缀/后缀原样保留）；不是 R1 形态返回 None。"""
    m = rx.match(fn or '')
    if not m:
        return None
    stem_end = m.end('stem')
    suffix = fn[stem_end:]
    pos = suffix.find('R1')
    if pos < 0:
        pos = suffix.find('r1')
    if pos >= 0:
        tag = 'R2' if suffix[pos] == 'R' else 'r2'
        return fn[:stem_end] + suffix[:pos] + tag + suffix[pos + 2:]
    # ENA/DDBJ 风格：`_1.fastq.gz` → `_2.fastq.gz`
    m2 = re.match(r'([_.\-])1(\..*)$', suffix)
    if m2:
        return fn[:stem_end] + m2.group(1) + '2' + m2.group(2)
    return None


def _pc_pair_fastqs(names):
    """同目录 FASTQ 文件名 → (pairs, singles, orphans)，均为文件名列表。"""
    lower = {n.lower(): n for n in names}
    used, pairs, singles, orphans = set(), [], [], []
    for n in sorted(names):
        if n in used:
            continue
        mate = None
        for rx in _PC_R1_RULES:
            cand = _pc_mate_name(n, rx)
            if cand:
                real = lower.get(cand.lower())
                if real and real != n and real not in used:
                    mate = real
                break
        if mate:
            pairs.append((n, mate))
            used.add(n)
            used.add(mate)
        elif any(rx.match(n) for rx in _PC_R2_LIKE):
            orphans.append(n)
        else:
            singles.append(n)
    return pairs, singles, orphans


def _pc_sample_name(fn, is_sra=False, ena=False, is_fasta=False):
    """文件名 → 样本名（剥 pe/技术后缀 + 非法字符清洗）。

    ena=True 表示这个文件按 ENA 的 `_1`/`_2` 规则配上了对，此时才把 `_1`/`_2`
    当配对标记剥掉（`Dataset_1_R1` 的 `_1` 属于样本名，不能剥）。
    """
    base = fn or ''
    if is_sra:
        base = re.sub(r'\.sra$', '', base, flags=re.I)
    elif is_fasta:
        base = re.sub(_FSTA_EXT_RX, '', base, flags=re.I)
    else:
        base = re.sub(_PC_FQ_EXT_RX, '', base, flags=re.I)
        if ena:
            base = _PC_ENA_SUFFIX.sub('', base)
        for _ in range(3):
            nxt = _PC_R_SUFFIX.sub('', base)
            if nxt == base:
                break
            base = nxt
    base = re.sub(r'[^\w\-.]+', '_', base).strip('_-.')
    return base or 'sample'


def _pc_dedupe(samples):
    """样本重名加 _2/_3 后缀（不同子目录里同名样本很常见）。

    Windows 文件系统大小写不敏感，`A` 与 `a` 的样本目录会撞在一起，故按
    lower() 判重。
    """
    seen = {}
    for s in samples:
        n = s['name']
        key = n.lower()
        if key in seen:
            seen[key] += 1
            s['name'] = '%s_%d' % (n, seen[key])
            while s['name'].lower() in seen:
                seen[key] += 1
                s['name'] = '%s_%d' % (n, seen[key])
        seen[s['name'].lower()] = 1
    return samples


def _pc_fsize(p):
    """文件字节数（读不到按 0，仅供预览显示，不让大小统计打断解析）。"""
    try:
        return os.path.getsize(p)
    except OSError:
        return 0


def _pc_kind_label(s):
    if s.get('kind') == 'sra':
        return 'SRA'
    if s.get('kind') == 'fasta':
        return 'FASTA'
    return '双端' if s.get('r2') else '单端'


def _pc_cancelled(cancel):
    try:
        return bool(cancel is not None and cancel.is_set())
    except Exception:
        return False


def _pc_summary(res):
    """步骤摘要：只留标量，避免把 files 之类大列表塞进 _chain.json。"""
    if not isinstance(res, dict):
        return {}
    return {k: v for k, v in res.items()
            if k != 'files' and isinstance(v, (str, int, float, bool))}


def _scan_prep_inputs(folder, _depth=0, with_fasta=False):
    """扫描目录 → {'folder','samples','orphan_r2','files','scanned_dirs'}。

    - 支持 .sra、FASTQ(.fastq/.fq[.gz])；with_fasta 时也把 FASTA
      (.fa/.fasta/.fna/.fas[.gz]) 当单端样本（病毒识别一键用）；
    - 顶层没有样本文件时下探一层子目录（常见「一个项目一个子文件夹」的排法）；
    - 配对优先级：`_R1`/`.R1`/`-R1` → Illumina `_R1_001` → ENA/DDBJ `_1`；
    - 单独 FASTQ 视为单端；没有 R1 的 `_R2`/`_2` 记为孤儿并跳过；
    - .sra / FASTA 各自成样本（.sra 单双端由 sracha 解码决定）。
    """
    try:
        base = check_path(folder, must_exist=True)
    except Exception as e:                     # 文件不存在/越界/不可读
        abort(400, '输入目录不存在或不可读：%s（%s）' % (folder, e))
    if not os.path.isdir(base):
        abort(400, '输入目录不是文件夹：%s' % folder)
    try:
        entries = sorted(os.listdir(base))
    except OSError as e:
        abort(400, '无法读取输入目录：%s' % e)
    files = [fn for fn in entries
             if os.path.isfile(os.path.join(base, fn))
             and (fn.lower().endswith('.sra')
                  or fn.lower().endswith(_PC_FQ_EXTS)
                  or (with_fasta and fn.lower().endswith(_FASTA_EXTS)))]
    out = {'folder': base, 'samples': [], 'orphan_r2': [],
           'files': len(files), 'scanned_dirs': 0}
    if not files and _depth < 1:
        for sub in entries:
            sp = os.path.join(base, sub)
            if not os.path.isdir(sp):
                continue
            child = _scan_prep_inputs(sp, _depth + 1, with_fasta=with_fasta)
            out['samples'].extend(child['samples'])
            out['orphan_r2'].extend(child['orphan_r2'])
            out['files'] += child['files']
            out['scanned_dirs'] += child['scanned_dirs'] + 1
        out['samples'] = _pc_dedupe(out['samples'])
        out['total_bytes'] = sum(s.get('size') or 0 for s in out['samples'])
        return out
    fqs = [f for f in files
           if not f.lower().endswith('.sra')
           and not f.lower().endswith(_FASTA_EXTS)]
    sras = [f for f in files if f.lower().endswith('.sra')]
    fastas = [f for f in files if f.lower().endswith(_FASTA_EXTS)] \
        if with_fasta else []
    pairs, singles, orphans = _pc_pair_fastqs(fqs)
    for r1, r2 in pairs:
        p1 = os.path.join(base, r1)
        p2 = os.path.join(base, r2)
        s1, s2 = _pc_fsize(p1), _pc_fsize(p2)
        out['samples'].append({
            'name': _pc_sample_name(
                r1, ena=bool(_PC_ENA_PAIR.search(r1))),
            'r1': p1, 'r2': p2, 'kind': 'fastq', 'pe': True, 'src': 'dir',
            'r1_size': s1, 'r2_size': s2, 'size': s1 + s2})
    for r1 in singles:
        p1 = os.path.join(base, r1)
        s1 = _pc_fsize(p1)
        out['samples'].append({
            'name': _pc_sample_name(r1), 'r1': p1,
            'r2': '', 'kind': 'fastq', 'pe': False, 'src': 'dir',
            'r1_size': s1, 'r2_size': 0, 'size': s1})
    for sra in sras:
        p1 = os.path.join(base, sra)
        s1 = _pc_fsize(p1)
        out['samples'].append({
            'name': _pc_sample_name(sra, is_sra=True),
            'r1': p1, 'r2': '', 'kind': 'sra', 'pe': False, 'src': 'dir',
            'r1_size': s1, 'r2_size': 0, 'size': s1})
    for fa in fastas:
        p1 = os.path.join(base, fa)
        s1 = _pc_fsize(p1)
        out['samples'].append({
            'name': _pc_sample_name(fa, is_fasta=True), 'r1': p1,
            'r2': '', 'kind': 'fasta', 'pe': False, 'src': 'dir',
            'r1_size': s1, 'r2_size': 0, 'size': s1})
    out['orphan_r2'] = orphans
    out['samples'].sort(key=lambda s: s['name'].lower())
    out['total_bytes'] = sum(s.get('size') or 0 for s in out['samples'])
    return out


def _prep_pick_samples(folder, picks=None, with_fasta=False, allow=''):
    """扫描目录并按用户在预览表里的勾选过滤（picks 为空 = 全部）。"""
    scan = _scan_prep_inputs(folder, with_fasta=with_fasta)
    samples = scan['samples']
    want = [str(x).strip() for x in (picks or []) if str(x).strip()]
    if want:
        got = {s['name'] for s in samples}
        missing = [n for n in want if n not in got]
        if missing:
            abort(400, '这些样本在目录里找不到（文件可能已变动，请重新解析）：%s'
                       % '、'.join(missing[:5]))
        scan['samples'] = [s for s in samples if s['name'] in set(want)]
    if not scan['samples']:
        abort(400, '目录内未解析到样本（支持 %s）：%s'
                   % (allow or '.sra 与 FASTQ[.gz]', folder))
    return scan


def _prepchain_run_sample(chain_dir, root, sample, host_ok, fastp_ok, id_ok,
                          conf, dedup, db_arg, threads, db_virus, logger, prog,
                          prog_lo, prog_hi, cancel=None, prefix='', label=''):
    """跑单个样本的 转换→质控→FASTA→宿主去除→病毒序列提取。

    2026-11-03 起第⑤步 = 病毒序列提取（样本分析，kunpeng 病毒库）：输入用
    宿主去除的 kept 非宿主 reads（宿主步骤跳过时退回质控后的 FASTQ），
    产物 viral_sequences*.fasta / viral_ids.tsv 就是「病毒 reads」交付物，
    可直喂「病毒识别和分类分析」组 ⚡ 一键（组装→病毒鉴定→验证，只收
    FASTA）或样品管道。返回 {steps,kept,fasta,links,pe}。
    chain_dir 是该样本的产物汇总目录（单文件=chain 根，批量=<chain>/<sample>）；
    prefix 给步骤运行目录加样本前缀；label 用于进度标题。
    """
    is_sra = sample['kind'] == 'sra'
    inp = sample['r1']
    inp2 = sample.get('r2') or None

    def _title(t):
        return '%s · %s' % (label, t) if label else t

    plan = []
    if is_sra:
        plan.append(('sra2fq', 0.15))
    if fastp_ok:
        plan.append(('fastp', 0.25))
    plan.append(('fq2fa', 0.20))
    if host_ok:
        plan.append(('host', 0.40))
    if id_ok:
        plan.append(('identify', 0.35))
    tot = sum(w for _k, w in plan) or 1.0
    cur = [0.0]

    def _band(w):
        """本步骤在整批进度里的绝对区间（prog_lo..prog_hi 是样本的份额）。"""
        lo = prog_lo + (cur[0] / tot) * (prog_hi - prog_lo)
        cur[0] = min(tot, cur[0] + w)
        hi = prog_lo + (cur[0] / tot) * (prog_hi - prog_lo)
        return lo, hi

    steps, kept, fasta, links = [], [], [], []
    fq1 = None if is_sra else inp
    fq2 = None if is_sra else inp2

    if is_sra:
        lo, hi = _band(0.15)
        run1, res1, _linked = _chain_run_step(
            chain_dir, root, prefix + 'sra2fq', _title('SRA→FASTQ 转换'),
            _tool_job_convert, {'input': inp, 'target': 'fastq'},
            threads, db_virus, logger, prog, lo, hi, cancel=cancel)
        outs = [os.path.join(root, run1, f) for f in (res1.get('files') or [])]
        pair2 = [f for f in outs
                 if re.search(r'_2\.(fastq|fq)\.gz$',
                              os.path.basename(f), re.I)]
        rest = [f for f in outs if f not in pair2]
        if not rest:
            raise RuntimeError('SRA→FASTQ 未产出 FASTQ（见运行日志）')
        fq1 = rest[0]
        fq2 = pair2[0] if pair2 else None
        steps.append({'key': 'sra2fq', 'run': run1,
                      'title': _title('SRA→FASTQ 转换'),
                      'output': [os.path.basename(f) for f in outs],
                      'pe': bool(fq2)})
        logger.log('sracha 解码为%s FASTQ' % ('双端' if fq2 else '单端'))

    if fastp_ok:
        lo, hi = _band(0.25)
        p2 = {'r1': fq1, 'dedup': dedup}
        if fq2:
            p2['r2'] = fq2
        run2, res2, _linked = _chain_run_step(
            chain_dir, root, prefix + 'fastp', _title('fastp 质控'),
            _tool_job_fastp, p2, threads, db_virus, logger, prog, lo, hi,
            cancel=cancel, optional=('r2',))
        qc1 = os.path.join(root, run2, '00_prep', 'fastp_R1.fastq.gz')
        qc2 = os.path.join(root, run2, '00_prep', 'fastp_R2.fastq.gz')
        missing = [os.path.basename(x)
                   for x in ([qc1] + ([qc2] if fq2 else []))
                   if not os.path.exists(x)]
        if missing:
            raise RuntimeError('质控未产出 %s' % ', '.join(missing))
        fq1 = qc1
        fq2 = qc2 if fq2 else None
        steps.append({'key': 'fastp', 'run': run2,
                      'title': _title('fastp 质控'),
                      'summary': _pc_summary(res2)})

    mates = [('fq2fa', 'FASTQ→FASTA（R1）', fq1)]
    if fq2:
        mates.append(('fq2fa_r2', 'FASTQ→FASTA（R2）', fq2))
    fa1 = None
    for key, nm, src in mates:
        lo, hi = _band(0.20 / len(mates))
        rr, rs, _linked = _chain_run_step(
            chain_dir, root, prefix + key, _title(nm), _tool_job_convert,
            {'input': src, 'target': 'fasta'}, threads, db_virus,
            logger, prog, lo, hi, cancel=cancel)
        rels = rs.get('files') or []
        if not rels:
            raise RuntimeError('%s 未产出 FASTA（见运行日志）' % nm)
        f = os.path.join(root, rr, rels[0])
        fasta.append(f)
        if _chain_link(os.path.join(str(chain_dir), os.path.basename(f)),
                       f, logger):
            links.append(os.path.basename(f))
        if key == 'fq2fa':
            fa1 = f
        steps.append({'key': key, 'run': rr, 'title': _title(nm),
                      'output': [os.path.basename(f)]})

    if host_ok:
        lo, hi = _band(0.40)
        p4 = {'r1': fq1, 'classify_r1': fa1, 'confidence': conf}
        if fq2:
            p4['r2'] = fq2
            p4['classify_r2'] = fasta[-1]
        if db_arg:
            p4['db'] = db_arg
        run4, res4, _linked = _chain_run_step(
            chain_dir, root, prefix + 'host', _title('宿主去除（kunpeng 分类）'),
            _tool_job_hostremoval, p4, threads, db_virus, logger, prog, lo, hi,
            cancel=cancel, optional=('r2', 'classify_r1', 'classify_r2', 'db'))
        hr = os.path.join(root, run4, '01_host_removal')
        try:
            kept_names = sorted(fn for fn in os.listdir(hr)
                                if fn.startswith('kept_'))
        except OSError:
            kept_names = []
        for fn in kept_names:
            src = os.path.join(hr, fn)
            if _chain_link(os.path.join(str(chain_dir), fn), src, logger):
                links.append(fn)
            kept.append(src)
        steps.append({'key': 'host', 'run': run4,
                      'title': _title('宿主去除（kunpeng 分类）'),
                      'summary': _pc_summary(res4)})
    else:
        steps.append({'key': 'host', 'run': None,
                      'title': _title('宿主去除（kunpeng 分类）'),
                      'skipped': '宿主库未就绪（「数据库构建」页构建后可单独运行）'})

    # ---- ⑤ 病毒序列提取（样本分析；输入 = kept 非宿主 reads，宿主跳过时
    #         退回质控后的 FASTQ；病毒库未就绪自动跳过） ----
    if id_ok:
        lo, hi = _band(0.35)
        if kept:
            _kr2 = [k for k in kept if '_R2' in os.path.basename(k)]
            _kr1 = [k for k in kept if k not in _kr2]
            src1 = _kr1[0] if _kr1 else fq1
            src2 = _kr2[0] if _kr2 else None
        else:
            src1, src2 = fq1, fq2
        p5 = {'input': src1, 'input_type': ('pe' if src2 else 'single'),
              'confidence': conf}
        if src2:
            p5['input2'] = src2
        run5, res5, _l5 = _chain_run_step(
            chain_dir, root, prefix + 'identify',
            _title('病毒序列提取（样本分析）'), _tool_job_identify, p5,
            threads, db_virus, logger, prog, lo, hi, cancel=cancel,
            optional=('input2',))
        id_dir = os.path.join(root, run5)
        try:
            outs = sorted(fn for fn in os.listdir(id_dir)
                          if fn.startswith('viral_'))
        except OSError:
            outs = []
        for fn in outs:
            src = os.path.join(id_dir, fn)
            if _chain_link(os.path.join(str(chain_dir), fn), src, logger):
                links.append(fn)
        steps.append({'key': 'identify', 'run': run5,
                      'title': _title('病毒序列提取（样本分析）'),
                      'summary': _pc_summary(res5)})
        logger.log('⑤ 病毒序列提取完成：%d 个产物（viral_*.fasta/tsv）'
                   % len(outs))
    else:
        steps.append({'key': 'identify', 'run': None,
                      'title': _title('病毒序列提取（样本分析）'),
                      'skipped': '病毒库未就绪（「数据库构建」页构建后可单独运行）'})

    return {'steps': steps, 'kept': kept, 'fasta': fasta, 'links': links,
            'pe': bool(fq2)}


def _tool_job_prepchain(ctx):
    """样本处理 · 一键前处理：单文件或输入目录批量（自动解析单/双端）。

    2026-11-03 起五步链：转换→质控→FASTA→宿主去除→**病毒序列提取（样本
    分析）**——第⑤步输入用宿主去除的 kept 非宿主 reads（宿主跳过时退回
    质控后 FASTQ），产物 viral_sequences*.fasta / viral_ids.tsv 即「病毒
    reads」交付物，可直喂「病毒识别和分类分析」组 ⚡ 一键（组装→病毒鉴定→
    验证）。病毒库未就绪自动跳过第⑤步；`do_identify=false` 可整体关闭。
    单文件：.sra（单/双端由 sracha 解码决定）或 FASTQ（可带 R2）。
    目录批量：顶层无样本文件时下探一层子目录；按 `_R1`/`_R1_001`/`_1`
    命名配对，单独 FASTQ 视为单端，无 R1 的 R2 记为孤儿跳过，.sra 各自成
    样本；逐样本顺序执行，单样本失败不影响其余样本。
    """
    root = _tool_runs_root()
    p = ctx.p
    inp = ctx.opt('input')
    inp2 = ctx.opt('input2')
    indir = ctx.opt('input_dir') or ctx.opt('folder')
    picks = p.get('samples') or []
    if isinstance(picks, str):
        picks = [s for s in re.split(r'[;,\s]+', picks) if s.strip()]
    picks = [str(s).strip() for s in picks if str(s).strip()]

    conf = float(p.get('confidence') or 0)
    dedup = bool(p.get('dedup'))
    # ⑤ 病毒序列提取（样本分析）默认开；传 false/0/no 关闭（批量回归测试用）
    do_identify = str(p.get('do_identify', True)).strip().lower() \
        not in ('false', '0', 'no')
    db_arg = ctx.opt('db')
    db_host = db_arg or cfg.databases['host']

    if inp and indir:
        abort(400, '「输入文件」与「输入目录」只能二选一'
                   '（目录模式会自动解析样本，无需再填单文件）')
    if not inp and not indir:
        abort(400, '请选择输入文件（.sra / FASTQ）或输入目录')
    if inp2 and inp and inp.lower().endswith('.sra'):
        abort(400, '.sra 输入不需要 R2（单/双端由 sracha 解码结果决定）')
    if inp and (not inp.lower().endswith('.sra')
                and not inp.lower().endswith(_PC_FQ_EXTS)):
        abort(400, '输入需为 .sra 或 FASTQ(.fastq/.fq[.gz])；'
                   'FASTA 不能直接宿主去除（kept reads 必须从 FASTQ 提取）')

    scan = None
    if inp:
        mode = 'single'
        is_sra = inp.lower().endswith('.sra')
        s1 = _pc_fsize(inp)
        s2 = _pc_fsize(inp2) if inp2 else 0
        samples = _pc_dedupe([{
            'name': _pc_sample_name(os.path.basename(inp), is_sra=is_sra),
            'r1': inp, 'r2': inp2 or '',
            'kind': 'sra' if is_sra else 'fastq',
            'pe': bool(inp2), 'src': 'file',
            'r1_size': s1, 'r2_size': s2, 'size': s1 + s2}])
    else:
        mode = 'batch'
        scan = _prep_pick_samples(indir, picks)
        samples = scan['samples']

    def job(log, prog, cancel):
        from Virus_Platform_Core.kunpeng import db_ready
        from Virus_Platform_Core.preprocess import fastp_available
        logger = TaskLogger(callback=log)
        chain_dir = ctx.run_dir
        chain_name = os.path.basename(str(chain_dir))
        host_ok = db_ready(db_host)
        fastp_ok = fastp_available()
        if not host_ok:
            logger.log('宿主数据库未就绪：宿主去除步骤将跳过'
                       '（可在「数据库构建」页构建后单独运行）')
        if not fastp_ok:
            logger.log('未检测到 fastp：质控步骤将跳过')
        # ---- ⑤ 病毒序列提取（样本分析）开关与库就绪检查 ----
        id_db = getattr(ctx, 'db_virus', None)
        id_ok = do_identify and bool(id_db) and db_ready(id_db)
        if do_identify and not id_ok:
            logger.log('病毒库未就绪：病毒序列提取步骤将跳过'
                       '（可在「数据库构建」页构建，或在卡片上选库）')
        meta = {
            'chain': 'prepchain',
            'run': chain_name,
            'label': '样本处理 一键前处理',
            'mode': mode,
            'input': inp or '',
            'input2': inp2 or '',
            'input_dir': indir or '',
            'n_samples': len(samples),
            'host_db_ready': host_ok,
            'fastp_available': fastp_ok,
            'do_identify': do_identify,
            'identify_db_ready': id_ok,
            'params': {
                'confidence': conf, 'dedup': dedup,
                'host_db': db_arg or '（平台主库）',
                'host_db_ready': host_ok, 'fastp_available': fastp_ok,
                'do_identify': do_identify, 'identify_db_ready': id_ok,
            },
            'created': time.strftime('%Y-%m-%d %H:%M:%S'),
            'steps': [],
            'samples': [],
        }
        if mode == 'batch':
            meta['orphan_r2'] = list((scan or {}).get('orphan_r2') or [])
            if meta['orphan_r2']:
                logger.log('已跳过 %d 个找不到 R1 的 R2 文件：%s'
                           % (len(meta['orphan_r2']),
                              '、'.join(meta['orphan_r2'][:5])))
        kept, fasta, ok_samples = [], [], 0
        n = len(samples)
        for i, s in enumerate(samples):
            name = s['name']
            sample_dir = (str(chain_dir) if mode == 'single'
                          else os.path.join(str(chain_dir), name))
            os.makedirs(sample_dir, exist_ok=True)
            prefix = '' if mode == 'single' else name + '__'
            label = '' if mode == 'single' else name
            lo = float(i) / n
            hi = float(i + 1) / n
            if mode == 'batch':
                prog('chain', lo, '[%d/%d] %s（%s）'
                     % (i + 1, n, name, _pc_kind_label(s)))
            t0 = time.time()
            try:
                r = _prepchain_run_sample(
                    sample_dir, root, s, host_ok, fastp_ok, id_ok, conf, dedup,
                    db_arg, ctx.threads, ctx.db_virus, logger, prog, lo, hi,
                    cancel=cancel, prefix=prefix, label=label)
            except Exception as e:
                if _pc_cancelled(cancel):
                    logger.log('已取消：批量处理停止')
                    raise
                msg = str(e) or e.__class__.__name__
                logger.log('[%s] 失败：%s: %s'
                           % (name, e.__class__.__name__, msg))
                logger.log(traceback.format_exc())
                meta['samples'].append({
                    'name': name, 'kind': s['kind'], 'pe': bool(s.get('pe')),
                    'r1': s['r1'], 'r2': s.get('r2') or '',
                    'size': s.get('size') or 0,
                    'status': 'failed', 'error': msg,
                    'steps': [], 'fastq': [], 'fasta': [], 'links': [],
                })
                if mode == 'single':
                    raise
                continue
            meta['steps'].extend(r['steps'])
            kept.extend(r['kept'])
            fasta.extend(r['fasta'])
            ok_samples += 1
            meta['samples'].append({
                'name': name, 'kind': s['kind'], 'pe': bool(r['pe']),
                'r1': s['r1'], 'r2': s.get('r2') or '',
                'size': s.get('size') or 0,
                'status': 'done', 'error': '',
                'duration_s': round(time.time() - t0, 1),
                'steps': r['steps'], 'fastq': r['kept'], 'fasta': r['fasta'],
                'links': r['links'],
            })
        if ok_samples == 0:
            raise RuntimeError('批量处理全部失败（%d 个样本，详见运行日志）' % n)
        tsv = os.path.join(str(chain_dir), 'samples.tsv')
        if mode == 'batch':
            with safe_open(tsv, 'wt') as f:
                f.write('sample\ttype\tstatus\tbytes\tR1\tR2\tkept_fastq\t'
                        'fasta\terror\n')
                for smp in meta['samples']:
                    f.write('%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' % (
                        smp['name'], _pc_kind_label(smp), smp['status'],
                        smp.get('size') or 0,
                        smp['r1'], smp['r2'],
                        ';'.join(smp['fastq']), ';'.join(smp['fasta']),
                        (smp['error'] or '').replace('\t', ' ')))
        meta['ok_samples'] = ok_samples
        meta['failed_samples'] = n - ok_samples
        meta['done'] = True
        logger.log('一键前处理完成：成功 %d/%d' % (ok_samples, n)
                   if mode == 'batch' else '一键前处理完成')
        prog('chain', 1.0, '一键前处理完成' if mode == 'single'
             else '批量处理完成：成功 %d/%d' % (ok_samples, n))
        logger.close()
        _chain_write_manifest(chain_dir, meta)
        out = {
            'chain': chain_name, 'run': chain_name, 'mode': mode,
            'n_samples': n, 'ok_samples': ok_samples,
            'n_steps': len(meta['steps']), 'steps': meta['steps'],
            'samples': meta['samples'],
            'kept_fastq': kept, 'fasta': fasta,
            'chain_json': str(os.path.join(str(chain_dir), '_chain.json')),
            'total_bytes': sum(s.get('size') or 0 for s in samples),
        }
        if mode == 'batch':
            out['samples_tsv'] = tsv
        notes = []
        if not host_ok:
            notes.append('宿主库未就绪，宿主去除已跳过')
        if not fastp_ok:
            notes.append('fastp 未安装，质控已跳过')
        if mode == 'batch' and ok_samples < n:
            notes.append('部分样本失败 %d 个（详见 samples.tsv）' % (n - ok_samples))
        if notes:
            out['note'] = '；'.join(notes)
        return out

    return job


def _tool_job_annochain(ctx):
    """病毒注释分析 · 一键：① ORF 预测 → ② 功能注释 → ③ 基因组图谱 →
    ④ NCBI 提交准备；可选 kunpeng 分类步（classify='1'，排最前）。

    输入 FASTA（核酸 contigs / 基因组）。分类口径（对齐 ncbisub 远端
    pipeline 的 metabuli 口径）：
      - classify='1'：链首跑 kunpeng 病毒库分类（contigs 步），④ 建档时
        organism/family 从 virus_classification.tsv 自动推断回填；
        kunpeng 病毒库未就绪时自动降级为手动口径（表行 organism 留空，
        日志如实说明），不炸链。
      - 默认（classify 关）：organism 留空，「数据提交」页表格手动填写。
    前三步各建 run_dir：
      ① 步骤键固定为 'orf'（run 名 orf_*，布局 03_assembly + 04_orf）——
        ② 复用 _tool_job_orfa 的 run 模式直接对该运行注释（04b_orf_annot
        落在 ① 的运行目录，与样品管道 / 独立注释同一套产物口径），
        ③ 从中取 GFF3 给 gbdraw 画带注释的图谱。
    ④ 提交准备（不建 run_dir，直接调用 ncbi_submit.store 建档）：
      建与链运行同名的提交项目 → 分类表/FASTA 头建表行 → 关联 ① 的 orf
      运行 → 导出 sequences.fsa → 生成 featuretable.tbl → 打包
      submission_package.zip。作者/测序信息等其余元数据仍需人工填写。
    """
    root = _tool_runs_root()
    fasta = ctx.req('fasta', '输入 FASTA（核酸 contigs / 基因组）')
    min_aa = max(1, min(int(ctx.p.get('min_aa') or 100), 5000))
    classify_on = str(ctx.p.get('classify') or '').strip().lower() in (
        '1', 'true', 'yes', 'on')

    def job(log, prog, cancel):
        logger = TaskLogger(callback=log)
        chain_dir = ctx.run_dir
        chain_name = os.path.basename(chain_dir)
        meta = {'chain': 'annochain', 'run': chain_name,
                'label': '病毒注释分析 一键：注释→图谱→NCBI 提交准备',
                'params': {'min_aa': min_aa, 'classify': classify_on,
                           'input': os.path.basename(fasta)},
                'created': time.strftime('%Y-%m-%d %H:%M:%S'), 'steps': []}
        cur = 0.0

        def _band(w):
            nonlocal cur
            lo, hi = cur, cur + w
            cur = hi
            return lo, hi

        # 动态步骤序号（classify 开时 contigs 步占 ①，其余顺延）
        _CN = '①②③④⑤⑥⑦'

        def _nx(title):
            nonlocal seq_i
            seq_i += 1
            return f'{_CN[seq_i - 1]} {title}'

        seq_i = 0
        cls_run = None

        # ---- ⓪/① kunpeng 分类（可选步：classify='1' 且病毒库就绪）----
        if classify_on:
            from Virus_Platform_Core.kunpeng import db_ready as _kp_ready
            if _kp_ready(ctx.db_virus):
                t0 = _nx('kunpeng 病毒库分类')
                lo, hi = _band(0.15)
                run0, res0, _ = _chain_run_step(
                    chain_dir, root, 'contigs', t0, _tool_job_contigs,
                    {'contigs': fasta, 'min_len': 200, 'confidence': 0},
                    ctx.threads, ctx.db_virus, logger, prog, lo, hi,
                    cancel=cancel)
                cls_run = run0
                meta['steps'].append({
                    'key': 'contigs', 'run': run0, 'title': t0,
                    'summary': {k: v for k, v in (res0 or {}).items()
                                if not isinstance(v, (list, dict))}})
            else:
                logger.log('分类降级：kunpeng 病毒库未就绪，organism 需在'
                           '「数据提交」页手动填写（链继续）')

        # ---- ORF 预测（键名固定 'orf'：run 名 orf_*，功能注释步依赖该命名）----
        t_orf = _nx('ORF 预测')
        lo, hi = _band(0.30)
        run1, res1, _ = _chain_run_step(
            chain_dir, root, 'orf', t_orf, _tool_job_orf,
            {'fasta': fasta, 'min_aa': min_aa, 'annotate': False},
            ctx.threads, ctx.db_virus, logger, prog, lo, hi, cancel=cancel)
        n_ctg = len((res1 or {}).get('viral_contigs') or
                    (res1 or {}).get('contigs') or []) or None
        meta['steps'].append({
            'key': 'orf', 'run': run1, 'title': t_orf,
            'summary': {k: v for k, v in (res1 or {}).items()
                        if not isinstance(v, (list, dict))}})
        logger.log(f'{t_orf}完成: run={run1}'
                   + (f'（{n_ctg} 条 contigs）' if n_ctg else ''))

        # ---- ORF 功能注释（复用 orfa 的 run 模式，产物写回预测步运行目录）----
        t_ann = _nx('ORF 功能注释')
        lo, hi = _band(0.45)
        run2, res2, _ = _chain_run_step(
            chain_dir, root, 'orfa', t_ann, _tool_job_orfa,
            {'run': run1},
            ctx.threads, ctx.db_virus, logger, prog, lo, hi, cancel=cancel)
        ann_tsv = os.path.join(root, run1, '04b_orf_annot',
                               'orf_annotation.tsv')
        ann_linked = None
        if os.path.isfile(ann_tsv):
            ann_linked = os.path.basename(ann_tsv)
            _chain_link(os.path.join(chain_dir, ann_linked), ann_tsv,
                        logger=logger, target_is_dir=False)
        meta['steps'].append({
            'key': 'orfa', 'run': run2, 'title': t_ann,
            'target_run': run1,
            'output': [ann_linked] if ann_linked else [],
            'summary': {k: v for k, v in (res2 or {}).items()
                        if not isinstance(v, (list, dict))}})

        # ---- ③ 基因组图谱（FASTA + ①/② 的 GFF3）----
        gff = None
        d1 = os.path.join(root, run1)
        for pat in ('04b_orf_annot/*.gff3', '04b_orf_annot/*.gff',
                    '04_orf/*.gff'):
            hits = sorted(Path(d1).glob(pat))
            if hits:
                gff = str(hits[0])
                break
        t_plot = _nx('基因组图谱')
        lo, hi = _band(0.25)
        p3 = {'fasta': fasta, 'max_plots': 12, 'mode': 'both'}
        if gff:
            p3['ann'] = gff
            logger.log(f'{t_plot}注释来源: {os.path.basename(gff)}')
        run3, res3, _ = _chain_run_step(
            chain_dir, root, 'genoplot', t_plot, _tool_job_genoplot,
            p3, ctx.threads, ctx.db_virus, logger, prog, lo, hi,
            cancel=cancel, optional=('ann',))
        plots = []
        for pth in (res3 or {}).get('plots') or []:
            pth = str(pth)
            if os.path.isfile(pth):
                _chain_link(os.path.join(chain_dir, os.path.basename(pth)),
                            pth, logger=logger, target_is_dir=False)
                plots.append(os.path.basename(pth))
        meta['steps'].append({
            'key': 'genoplot', 'run': run3, 'title': t_plot,
            'output': plots,
            'summary': {k: v for k, v in (res3 or {}).items()
                        if not isinstance(v, (list, dict))}})

        # ---- ④ NCBI 提交准备（建档，不建 run_dir）----
        lo, hi = _band(0.10)
        t_sub = _nx('NCBI 提交准备')
        prog('submit', lo, f'{t_sub}：建项目 / 表行 / 关联注释')
        from Virus_Platform_Core.ncbi_submit import store as _store
        _store.create_table(chain_name)                # 已存在则复用（幂等）
        # organism 来源（分类口径）：kunpeng 分类步成功 → 从分类表自动推断
        # 回填；否则表行 organism 留空（手动口径，提交页表格填写）
        if cls_run:
            _store.import_from_run(chain_name, cls_run)
            logger.log(f'{t_sub}: organism 由 kunpeng 分类自动回填 '
                       f'(run={cls_run})')
        _store.import_from_fasta(chain_name, fasta)    # 兜底补行（幂等）
        lk = _store.link_orf_run(chain_name, run1)
        prog('submit', lo + (hi - lo) * 0.5, f'{t_sub}：导出 sequences.fsa'
             ' / featuretable')
        fsa_rep = _store.export_submission_fasta(chain_name, fasta)
        try:
            tbl_rep = _store.generate_feature_tbl(chain_name)
        except ValueError as e:
            tbl_rep = None
            logger.log(f'④ featuretable 生成跳过: {e}')
        pkg_path, pkg_n = _store.package_zip(chain_name)
        prog('submit', hi, '④ NCBI 提交准备完成')
        sub_files = ['unified_metadata.csv', 'sequences.fsa',
                     'featuretable.tbl', 'submission_package.zip']
        meta['steps'].append({
            'key': 'submit', 'run': None, 'title': t_sub,
            'output': [f for f in sub_files if os.path.isfile(
                os.path.join(_store.table_dir(chain_name), f))],
            'summary': {
                'project': chain_name,
                'rows': (fsa_rep or {}).get('written'),
                'n_cds': (lk or {}).get('n_cds'),
                'missing_contigs': (lk or {}).get('n_missing'),
                'package_files': pkg_n,
            },
            'submission': {'project': chain_name, 'dir': _store.table_dir(chain_name),
                           'featuretable': bool(tbl_rep),
                           'missing': (lk or {}).get('missing', [])[:20]}})

        prog('chain', 1.0, '一键完成：注释→图谱→NCBI 提交准备')
        logger.close()
        meta['done'] = True
        _chain_write_manifest(chain_dir, meta)
        return {'chain': chain_name, 'run': chain_name,
                'n_steps': len(meta['steps']), 'steps': meta['steps'],
                'orf_run': run1,
                'annotation_tsv': ann_tsv if os.path.isfile(ann_tsv) else None,
                'plots': plots,
                'submission': {'project': chain_name,
                               'dir': _store.table_dir(chain_name),
                               'featuretable': bool(tbl_rep),
                               'package': pkg_path,
                               'package_files': pkg_n,
                               'missing': (lk or {}).get('missing', [])[:50]},
                'submit_hint': f'提交项目 {chain_name} 已建档（submissions/{chain_name}/）：'
                               f'去「数据提交」页填写 organism/作者等元数据后即可生成 .sqn 提交 NCBI',
                'chain_json': os.path.join(chain_dir, '_chain.json')}
    return job


def _tool_job_chainoverview(ctx):
    """占位：链式汇总目录的读取走 /api/tool/chain_result，不注册为工具。"""
    raise NotImplementedError
