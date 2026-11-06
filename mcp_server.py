# -*- coding: utf-8 -*-
"""virusplatform_mcp —— 植物病毒分析平台 MCP 服务器（stdio）。

把平台三大能力原样暴露为 MCP 工具，供 Claude Desktop / ZCode 等
客户端直接驱动：
  1. 工具箱全量可执行（web/tools_api.py 的 TOOL_REGISTRY）
  2. 样品流水线全流程驱动（pipeline.run_analysis，⓪~⑩ 15 阶段任选）
  3. 任务/运行/产物查询（TaskManager 快照、run 目录文件读取）

实现口径与网页端完全同源：工具运行走同一套 job 工厂 + TaskManager
（heavy/light 并发闸门、run.log、结果面板可视化补全都保留），流水线
走同一套 run_analysis 编排。长任务一律「提交返回 task_id +
vp_get_task 轮询」两个动作，绝不同步阻塞。

启动（与平台同一解释器，缺 mcp 包自动安装）：
    3rd\\python\\python.exe mcp_server.py              # stdio 服务（默认）
    3rd\\python\\python.exe mcp_server.py --http       # streamable-HTTP 模式
    3rd\\python\\python.exe mcp_server.py --http 9000  # HTTP 自定义端口
    3rd\\python\\python.exe mcp_server.py --http --token s3cret
                                                       # HTTP + Bearer 鉴权
    3rd\\python\\python.exe mcp_server.py --check      # 自检（不进服务）

下线指定工具（不删代码）：环境变量 VP_MCP_DISABLED_TOOLS=vp_a,vp_b 或
平台根 deferred.json {"disabled_tools": ["vp_a"]}；文件存在但解析失败
会拒绝启动（fail-closed）。

stdio：任务随客户端进程存活；HTTP（127.0.0.1:8765/mcp）：任务与客户端
解耦，断开照跑、重连可查，局域网内多客户端可共享。两种模式任务都在
本进程执行；与网页端同时运行时并发闸门各自计数。
"""
import json
import logging
import os
import re
import sys
import time
import uuid
import importlib                     # 依赖按需探测/安装（importlib.import_module）
from typing import Annotated, Optional

# 嵌入式 Python（3rd/python，_pth 布局）不自动注入脚本目录——与 app.py /
# main.py 同口径手动补平台根，Virus_Platform_Core 才可导入
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ---- stdio 纪律：协议通道独占 stdout，一切输出走 stderr ----
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding='utf-8', errors='replace')
    except (AttributeError, ValueError):
        pass


def _ensure_mcp_sdk():
    """缺 mcp 包自动安装一次（与启动平台-网页.bat 的依赖兜底同口径）。

    全程只写 stderr：stdio 传输下 stdout 是协议通道，pip 的任何输出
    混进去都会让客户端握手失败。
    """
    try:
        import mcp  # noqa: F401
        return
    except ImportError:
        pass
    import subprocess
    sys.stderr.write('[virusplatform_mcp] 首次运行：安装 mcp SDK v1（约 1 分钟）...\n')
    for mirror in ('https://pypi.tuna.tsinghua.edu.cn/simple', None):
        # 锁 mcp<2：2.x 把 FastMCP 改名为 MCPServer 且 API 不兼容（本服务器
        # 按 v1 官方 SDK 编写；已有 v2 环境会被 pip 自动降级）
        cmd = [sys.executable, '-m', 'pip', 'install', '--quiet', 'mcp<2']
        if mirror:
            cmd += ['-i', mirror]
        try:
            if subprocess.run(cmd,
                              creationflags=getattr(
                                  subprocess, 'CREATE_NO_WINDOW', 0)
                              ).returncode == 0:
                break
        except OSError:
            continue
    importlib.import_module('mcp')   # 再试一次；仍缺就原样抛 ImportError


_ensure_mcp_sdk()

import anyio                                                     # noqa: E402
from mcp.server.fastmcp import FastMCP                           # noqa: E402
from mcp.types import ToolAnnotations                            # noqa: E402
from pydantic import Field                                       # noqa: E402

# ---- 平台模块（脚本位于平台根，sys.path[0] 即根）----
from Virus_Platform_Core.agent_catalog import TOOL_PARAM_SCHEMAS as _TOOL_PARAM_SCHEMAS
from Virus_Platform_Core.config import DIRS, PLATFORM_ROOT       # noqa: E402
from Virus_Platform_Core.utils import (TaskLogger, check_path,   # noqa: E402
                                       safe_open)
from Virus_Platform_Core.web.state import (cfg, run_real_dir,    # noqa: E402
                                           tool_runs_root, RUN_OUT_LINK)
from Virus_Platform_Core.web.tasks import tm                     # noqa: E402
from Virus_Platform_Core.web.tools_api import (LIGHT_TOOLS,      # noqa: E402
                                               TOOL_REGISTRY,
                                               VIRUS_DB_PRESETS,
                                               _resolve_virus_db)
from Virus_Platform_Core.pipeline import (DEFAULT_ANALYZE_STAGES,  # noqa: E402
                                          STAGE_GROUPS,
                                          STAGE_NAMES,
                                          STAGE_NAMES_EN,
                                          STAGE_ORDER,
                                          run_analysis)

# 工厂期缺参/选库失败走 flask.abort——统一转成带原文案的 ValueError
from werkzeug.exceptions import HTTPException                    # noqa: E402

_PLATFORM_OK = None

SERVER_VERSION = '1.4.0'   # 2026-11-03：两域行为修复 + schema 补登记（能力口径变更）


def _gated_tools():
    """启动下线清单（借鉴 wisp gate 的 fail-closed 思路）。

    两个来源并集：环境变量 VP_MCP_DISABLED_TOOLS（逗号分隔）与平台根
    deferred.json（{"disabled_tools": [...]}）。deferred.json 存在但解析
    失败 → 直接抛错拒启：写了闸门文件的运维预期是它必须生效，静默忽略
    会把该摘除的工具放出去。
    """
    names = set()
    env = os.environ.get('VP_MCP_DISABLED_TOOLS', '')
    names.update(t.strip() for t in env.replace(';', ',').split(',') if t.strip())
    dj = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      'deferred.json')
    if os.path.isfile(dj):
        try:
            with open(dj, encoding='utf-8') as f:
                data = json.load(f)
        except (OSError, ValueError) as e:
            raise SystemExit(f'[virusplatform_mcp] deferred.json 解析失败，'
                             f'拒绝启动（fail-closed）: {e}')
        items = data.get('disabled_tools') if isinstance(data, dict) else data
        if isinstance(items, list):
            names.update(str(t).strip() for t in items if str(t).strip())
    return names


def _capability_fingerprint(enabled_tools):
    """能力指纹：版本 + 启用工具清单的 sha256 前 12 位。

    agent 会话内可据此感知「服务端能力变了」（升级/摘除工具后指纹必变）。
    """
    import hashlib
    payload = SERVER_VERSION + '|' + ','.join(sorted(enabled_tools))
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()[:12]


def _platform_check():
    """平台自检一次并缓存结论（--check 与提交类工具共用）。"""
    global _PLATFORM_OK
    if _PLATFORM_OK is not None:
        return _PLATFORM_OK
    problems = []
    if not TOOL_REGISTRY:
        problems.append('TOOL_REGISTRY 为空')
    _PLATFORM_OK = problems
    return problems


mcp = FastMCP(
    'virusplatform_mcp',
    instructions=(
        '植物病毒分析平台（Windows 本地）的 MCP 入口。'
        '典型流程：vp_platform_info 看环境 → vp_list_samples / vp_list_tools '
        '选目标 → vp_run_tool（工具箱）或 vp_pipeline_start（全流程）提交 → '
        'vp_wait_task 等终态（或 vp_get_task 轮询）→ vp_read_run_file / '
        'vp_read_sample_file 读产物文本，vp_sample_stages 看某样品 15 阶段'
        '到哪一步。'
        '任务归档是手工动作：vp_archive_task 移入历史归档（可 '
        'vp_unarchive_task 恢复），切模块/重连都不会自动归档；'
        'vp_list_tasks(include_archived=true) 可连历史一起看。'
        '数据库构建、公共数据检索（NCBI/GenBank/宿主基因组）、NCBI 提交、'
        'LOGAN 溯源、ICTV 库等 CLI 能力：vp_cli_help 查清单与参数，快查用 '
        'vp_cli_run，长任务用 vp_cli_submit。'
        '所有执行类工具提交即返回 task_id，长任务（组装/建树/定量/下载）'
        '必须等待/轮询，不要重复提交。'
    ),
)

_RO = ToolAnnotations(readOnlyHint=True, idempotentHint=True)
_RO_LIVE = ToolAnnotations(readOnlyHint=True)
_DESTR = ToolAnnotations(destructiveHint=True)
# 可逆写操作（手动归档/恢复、目录改名）：不是只读，但不动数据内容
_WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False,
                         idempotentHint=True)


# ======================================================================
# 内部实现（同步函数；工具层经 anyio.to_thread 调用，不卡事件循环）
# ======================================================================

def _input_abs(v):
    """输入数据文件：相对路径按平台根解析；绝对路径允许电脑任意位置（只读）。"""
    return check_path(v if os.path.isabs(v)
                      else os.path.join(PLATFORM_ROOT, v), must_exist=True)


def _clamp_threads(threads):
    """数字参数钳制，与 /api/tool/run 同口径。"""
    if threads is None:
        return None
    try:
        return max(1, min(int(threads), (os.cpu_count() or 4) * 4))
    except (TypeError, ValueError):
        return None


def _submit_tool_run(tool, params, threads, out_dir):
    """复刻 /api/tool/run 的完整提交链（tools_api.api_tool_run）。"""
    problems = _platform_check()
    if problems:
        raise ValueError('平台模块未就绪（' + '; '.join(problems) +
                         '）；先在平台根跑 python mcp_server.py --check 排查')
    spec = TOOL_REGISTRY.get(tool)
    if not spec:
        raise ValueError('未知工具: %s（可用: %s）'
                         % (tool, ', '.join(sorted(TOOL_REGISTRY))))
    p = dict(params or {})
    threads = _clamp_threads(threads)

    def _req(key, what):
        v = (p.get(key) or '').strip()
        if not v:
            raise ValueError('缺少必填参数 %s（%s）' % (key, what))
        return _input_abs(v)

    def _opt(key):
        v = (p.get(key) or '').strip()
        return _input_abs(v) if v else None

    try:
        db_virus = _resolve_virus_db(p.get('db_virus'))
    except HTTPException as e:
        raise ValueError(e.description or str(e)) from e
    if spec['need_db']:
        from Virus_Platform_Core.kunpeng import db_ready
        if not db_ready(db_virus):
            raise ValueError('病毒库未就绪: %s（先在平台「数据库构建」页构建，'
                             '或 db_virus 换选其他内置库: %s）'
                             % (db_virus, ', '.join(VIRUS_DB_PRESETS)))

    ts = time.strftime('%Y%m%d_%H%M%S')
    run_name = f'{tool}_{ts}_{uuid.uuid4().hex[:6]}'
    base_dir = check_path(os.path.join(tool_runs_root(), run_name),
                          must_exist=False, in_platform=True)
    run_dir = base_dir
    if out_dir:
        out_root = check_path(out_dir, must_exist=False, in_platform=True)
        _out_key = os.path.normcase(os.path.normpath(os.path.abspath(out_root)))
        _bad_roots = (
            ('样品结果目录', DIRS['results']),
            ('数据库目录', DIRS['databases']),
            ('任务状态目录', DIRS['tasks']),
            ('前端资源目录', os.path.join(PLATFORM_ROOT, 'webapp')),
            ('第三方依赖目录', os.path.join(PLATFORM_ROOT, '3rd')),
        )
        for _label, _bad in _bad_roots:
            _bad_key = os.path.normcase(
                os.path.normpath(os.path.abspath(str(_bad))))
            if _out_key == _bad_key or _out_key.startswith(_bad_key + os.sep):
                raise ValueError('输出目录不能位于%s内（%s）；请换平台 run/ '
                                 '下或其他目录' % (_label, _bad))
        run_dir = check_path(os.path.join(out_root, run_name),
                             must_exist=False, in_platform=True)
    os.makedirs(run_dir, exist_ok=True)
    if run_dir != base_dir:
        os.makedirs(base_dir, exist_ok=True)
        try:
            with safe_open(os.path.join(base_dir, RUN_OUT_LINK), 'wt') as f:
                json.dump({'out_dir': run_dir, 'tool': tool}, f,
                          ensure_ascii=False, indent=2)
        except OSError:
            pass

    from types import SimpleNamespace
    ctx = SimpleNamespace(p=p, run_dir=run_dir, threads=threads,
                          db_virus=db_virus, req=_req, opt=_opt)
    try:
        job = spec['job'](ctx)
    except HTTPException as e:
        # 工厂期报错不留下空运行目录（与 api_tool_run 同口径）
        import shutil as _sh
        _sh.rmtree(run_dir, ignore_errors=True)
        if run_dir != base_dir:
            _sh.rmtree(base_dir, ignore_errors=True)
        raise ValueError(e.description or str(e)) from e
    except Exception:
        import shutil as _sh
        _sh.rmtree(run_dir, ignore_errors=True)
        if run_dir != base_dir:
            _sh.rmtree(base_dir, ignore_errors=True)
        raise

    name = cfg.tr(f"工具·{spec['title']} {ts}",
                  f"Tool·{spec['title_en']} {ts}")

    def _job_with_run(log, prog, cancel):
        """与 tools_api._job_with_run 同口径：结果注入 run/out_dir、
        表格产物自动补图、外移运行 touch 指针目录。"""
        try:
            res = job(log, prog, cancel)
        except HTTPException as e:
            raise RuntimeError(str(e.description or e)) from e
        if isinstance(res, dict):
            res.setdefault('run', run_name)
            res['out_dir'] = str(run_dir)
        try:
            from Virus_Platform_Core.toolbox_plots import add_plots
            for _fp in add_plots(str(run_dir)):
                log(f"图 -> {_fp}")
        except Exception as _pl_e:
            log(f"图表补绘失败（跳过）: {type(_pl_e).__name__}: {_pl_e}")
        # 模块结果页（与 tools_api 同口径）：MCP 发起的工具运行完成时
        # 也出 results.html。失败只跳过，不影响任务。
        try:
            from Virus_Platform_Core.report_kit import module_page
            module_page.build_tool_run_page(str(run_dir), logger=log)
        except Exception as _mp_e:
            log(f"模块结果页生成失败（跳过）: {type(_mp_e).__name__}: {_mp_e}")
        try:
            os.utime(base_dir)
        except OSError:
            pass
        return res

    weight = 'light' if tool in LIGHT_TOOLS else 'heavy'
    tid = tm.start(name, _job_with_run,
                   log_file=os.path.join(run_dir, 'run.log'),
                   weight=weight, out_dir=str(run_dir))
    return _fmt(
        'submitted', f"工具 {tool} 已提交（{weight} 档），任务 {tid}",
        result={'task_id': tid, 'run': run_name, 'out_dir': str(run_dir),
                'tool': tool, 'weight': weight},
        nxt=[f"vp_get_task(task_id='{tid}') 轮询到 done/failed",
             f"vp_read_run_file(run='{run_name}', path=…) 读产物文本"],
        hint='用 vp_get_task(task_id=...) 轮询；产物文本用 '
             'vp_read_run_file(run=%s, path=...) 读取' % run_name)


def _pipeline_job(sample, r1, r2, stages, kw):
    """流水线任务体：run_analysis 全参数透传，日志/进度接 TaskManager。"""

    def job(log, prog, cancel):
        logger = TaskLogger(callback=log)
        try:
            return run_analysis(
                sample=sample, r1=r1, r2=(r2 or None),
                stages=stages, logger=logger,
                progress=lambda stage, pct, msg, *eta: prog(
                    str(stage), float(pct), str(msg)),
                **kw)
        finally:
            logger.close()
    return job


def _submit_pipeline(args):
    from Virus_Platform_Core.pipeline import _safe_sample_name
    sample = args['sample']
    r1 = _input_abs(args['r1'])
    r2 = _input_abs(args['r2']) if args.get('r2') else None
    stages = args.get('stages') or list(DEFAULT_ANALYZE_STAGES)
    _bad = [s for s in stages if s not in STAGE_ORDER]
    if _bad:
        raise ValueError('未知阶段: %s（可用: %s；默认全流程: %s）'
                         % (', '.join(_bad), ', '.join(STAGE_ORDER),
                            ', '.join(DEFAULT_ANALYZE_STAGES)))
    vm = str(args.get('verify_methods') or 'blastx,bfvd,cdd')
    kw = {'db_host': args.get('db_host'), 'db_virus': args.get('db_virus'),
          'kv_lib': args.get('kv_lib'),
          'threads': _clamp_threads(args.get('threads')),
          'confidence': float(args.get('confidence') or 0.0),
          'assembly_mode': args.get('assembly_mode') or 'rnaviral',
          'memory_gb': int(args.get('memory_gb') or 64),
          'min_contig_len': int(args.get('min_contig_len') or 200),
          # chunk_dir 为内部分块目录，不对 MCP 暴露
          'assembly_input': args.get('assembly_input') or 'virus',
          'min_viral_pairs': int(args.get('min_viral_pairs') or 500),
          'do_trim': args.get('do_trim', True) is not False,
          'subsample': int(args.get('subsample') or 0),
          'subsample_mode': args.get('subsample_mode') or 'prefix',
          'subsample_seed': int(args.get('subsample_seed') or 42),
          'subsample_prop': float(args.get('subsample_prop') or 0.0),
          'tree_tool': args.get('tree_tool') or 'fasttree',
          'tree_sampling': args.get('tree_sampling') or 'blast',
          'top_n_refs': int(args.get('top_n_refs') or 10),
          'min_pident': args.get('min_pident'),
          'min_qcov': args.get('min_qcov'),
          'primer_mode': args.get('primer_mode') or 'conserved',
          'min_orf_aa': int(args.get('min_orf_aa') or 100),
          'fastp_dedup': bool(args.get('fastp_dedup')),
          'do_fq2fa': args.get('do_fq2fa', True) is not False,
          'gbdraw_max': int(args.get('gbdraw_max') or 12),
          'gbdraw_fasta': args.get('gbdraw_fasta'),
          'gbdraw_ann': args.get('gbdraw_ann'),
          'plot_engine': args.get('plot_engine') or 'auto',
          'verify_host': args.get('verify_host') or 'all',
          'verify_methods': tuple(
              m.strip() for m in vm.split(',') if m.strip()),
          'verify_combine': args.get('verify_combine') or 'union',
          'force': bool(args.get('force')),
          'do_specificity': bool(args.get('specificity'))}
    if args.get('ncbi_refs'):
        kw['ncbi_refs'] = args['ncbi_refs']

    safe = _safe_sample_name(sample)
    sd = check_path(os.path.join(DIRS['results'], safe),
                    must_exist=False, in_platform=True)
    os.makedirs(sd, exist_ok=True)
    ts = time.strftime('%Y%m%d_%H%M%S')
    name = cfg.tr(f'流水线·{safe} {ts}', f'Pipeline·{safe} {ts}')
    tid = tm.start(name, _pipeline_job(sample, r1, r2, stages, kw),
                   log_file=os.path.join(sd, 'pipeline_mcp.log'),
                   weight='heavy', out_dir=str(sd))
    return _fmt(
        'submitted', f"样品 {safe} 流水线已提交（{len(stages)} 个阶段）",
        result={'task_id': tid, 'sample': safe, 'sample_dir': str(sd),
                'stages': stages},
        nxt=[f"vp_get_task(task_id='{tid}') 轮询各阶段进度",
             f"完成后样品结果在 {sd}（网页端结果中心同一份）"],
        hint='轮询 vp_get_task；阶段产物在 sample_dir 下'
             '（00_prep/01_host_removal/03_assembly/...）')


def _task_snapshot(tid, log_lines, result_max_kb=256):
    """任务快照的稳定投影（借鉴 wisp records 层：固定键序、不透原始 dict）。

    键集合固定，便于 agent 结构化消费；result 超 result_max_kb 时截断为
    {_truncated, _note, _head}，完整结果指回 run/tasks/<tid>.json。

    内存里查不到时回落到「磁盘记录」（手动归档 / 服务重启前的历史）：
    否则 agent 会以为任务没了，而其实它只是被归档、随时能恢复。
    """
    n = max(0, min(int(log_lines), 500))
    snap = tm.snapshot(tid, log_lines=n)
    archived = False
    if not snap:
        snap = tm.archived_snapshot(tid, log_lines=n)
        archived = bool(snap)
    if not snap:
        raise ValueError('任务不存在: %s（内存与归档目录都没有这条记录；'
                         '用 vp_list_tasks(include_archived=true) 查看现有任务）'
                         % tid)
    snap.pop('restart', None)            # 网页端专用，MCP 无意义
    snap['archived'] = bool(archived)
    if snap.get('started'):
        snap['started_h'] = time.strftime(
            '%Y-%m-%d %H:%M:%S', time.localtime(snap['started']))
    if snap.get('finished'):
        snap['finished_h'] = time.strftime(
            '%Y-%m-%d %H:%M:%S', time.localtime(snap['finished']))
    kb = max(1, min(int(result_max_kb), 4096))
    res = snap.get('result')
    if res is not None:
        text = json.dumps(res, ensure_ascii=False)
        if len(text.encode('utf-8', 'replace')) > kb * 1024:
            snap['result'] = {'_truncated': True,
                              '_note': f'终态结果超 {kb} KB；完整结果在 '
                                       f'run/tasks/{tid}.json 或产物目录 '
                                       f'{snap.get("out_dir") or ""}',
                              '_head': text[:kb * 1024]}
    keys = ('id', 'name', 'status', 'stage', 'pct', 'msg', 'error', 'eta',
            'out_dir', 'link', 'started_h', 'finished_h', 'log', 'result',
            'archived', 'external')
    out = {k: snap.get(k) for k in keys}
    # ---- 统一信封：摘要 / 解读 / 下一步（规则生成）----
    st, stage = out['status'], out['stage'] or '—'
    pct = int(round((out['pct'] or 0) * 100))
    out['summary'] = f"{st} · {stage} {pct}% · {out['msg'] or ''}".rstrip()
    interp, nxt = [], []
    if out['archived']:
        interp.append('该任务已归档（历史记录，产物与日志仍在磁盘）。')
        nxt.append(f"需要它回到在册列表就调 vp_unarchive_task("
                   f"task_id='{tid}')")
    if st == 'running':
        interp.append(f"当前阶段「{stage}」进行到 {pct}%。")
        nxt.append('稍后再次调用 vp_get_task 轮询，或直接 vp_wait_task '
                   '等它到终态')
    elif st == 'done':
        interp.append('任务成功完成；result 字段为各步摘要，产物在 out_dir。')
        nxt.append('用 vp_read_run_file(run, path) 读取关键文本产物')
        nxt.append('在网页端「任务中心/结果中心」可看同一份结果')
    elif st == 'failed':
        err = out['error'] or ''
        interp.append(f"失败原因：{err[:300]}")
        hits = _interpret_error(err)
        interp.extend(hits or ['按错误信息修正后重新提交即可'])
        nxt.append('修正参数/环境后重新提交（vp_run_tool / vp_pipeline_start /'
                   ' vp_cli_submit）')
    elif st == 'cancelled':
        interp.append('任务已被取消；产物目录保留已生成的部分。')
    out['interpretation'] = interp
    out['next'] = nxt
    return out


_TASK_STATUSES = ('queued', 'running', 'done', 'failed', 'cancelled')


def _task_list(limit, include_archived=False, status=None, name=None):
    limit = max(1, min(int(limit), 100))
    st = str(status or '').strip().lower()
    kw = str(name or '').strip().lower()
    if st and st not in _TASK_STATUSES:
        raise ValueError('status 只能是 %s 之一' % '/'.join(_TASK_STATUSES))
    # 过滤后再截断：多取一些，否则按状态/关键词筛会拿到不足 limit 的页
    outs = [x for x in tm.list_all(limit=max(limit, 120), logs=False) if x]
    for o in outs:
        o.pop('restart', None)
        o.pop('log', None)
    if st:
        outs = [o for o in outs if str(o.get('status')) == st]
    if kw:
        outs = [o for o in outs if kw in str(o.get('name') or '').lower()]
    outs = outs[:limit]
    archived, archived_count = [], 0
    if include_archived:
        # 内存之外的历史（手动归档 / 平台重启后只剩 tasks/*.json）：
        # 投影成与在册任务同口径的稳定键，剔除 log_tail 大字段
        mem_ids = {o.get('id') for o in outs}
        for d in tm.list_archived(limit=max(limit, 120)):
            if d.get('id') in mem_ids:
                continue
            if st and str(d.get('status')) != st:
                continue
            if kw and kw not in str(d.get('name') or '').lower():
                continue
            archived.append({k: d.get(k) for k in
                             ('id', 'name', 'status', 'stage', 'pct', 'msg',
                              'error', 'out_dir', 'started', 'finished')})
            if len(archived) >= limit:
                break
        archived_count = tm.count_archived()
    return {'count': len(outs), 'tasks': outs,
            'archived_count': archived_count, 'archived': archived}


# ======================================================================
# 工具卡参数 schema（键, 类型, 是否必填, 说明）——口径源自
# web/tool_jobs.py / web/chains.py 各工厂的取参与校验逻辑，与网页端同源。
# 未列出的键会被工厂忽略；缺必填项在提交时当场报中文原因。
# ======================================================================

# kvchain = kvsuite 参数 + 目录批量（input_dir/samples），口径由
# agent_catalog 维护；这里只给 stage 补一句「固定 all」说明。
# （旧实现是从 kvsuite 复制一份，目录批量上线后 MCP 清单漏了 input_dir。）
_TOOL_PARAM_SCHEMAS['kvchain'] = [
    (k, t, r, ('stage 固定 all；' + d if k == 'stage' else d))
    for (k, t, r, d) in _TOOL_PARAM_SCHEMAS['kvchain']]


def _tool_list_impl(query=None, weight=None, need_db=None,
                    limit=None, offset=0):
    """工具箱清单（可按关键词/档位/是否依赖病毒库过滤、分页）。

    过滤在服务端做：32 张卡的 params 全量返回体积不小，agent 通常只找
    一两张卡，按关键词筛比整包拉回去再过滤省上下文。
    """
    kw = str(query or '').strip().lower()
    w = str(weight or '').strip().lower()
    if w and w not in ('heavy', 'light'):
        raise ValueError('weight 只能是 heavy 或 light')
    rows = []
    for name in sorted(TOOL_REGISTRY):
        spec = TOOL_REGISTRY[name]
        doc = (spec['job'].__doc__ or '').strip()
        params = [{'key': k, 'type': t, 'required': bool(r), 'desc': d}
                  for (k, t, r, d) in _TOOL_PARAM_SCHEMAS.get(name, ())]
        row = {'tool': name, 'title': spec['title'],
               'title_en': spec['title_en'],
               'need_db': bool(spec['need_db']),
               'weight': 'light' if name in LIGHT_TOOLS else 'heavy',
               'gated': name in _GATED,
               'required_params': [p['key'] for p in params
                                   if p['required']],
               'params': params, 'doc': doc[:400]}
        if kw:
            blob = ' '.join(str(row.get(k) or '') for k in
                            ('tool', 'title', 'title_en', 'doc')).lower()
            blob += ' ' + ' '.join((p['key'] + ' ' + p['desc']).lower()
                                   for p in params)
            if kw not in blob:
                continue
        if w and row['weight'] != w:
            continue
        if need_db is not None and row['need_db'] != bool(need_db):
            continue
        rows.append(row)
    total = len(rows)
    lo = max(0, int(offset or 0))
    cap = max(0, int(limit or 0))
    page = rows[lo:lo + cap] if cap else rows[lo:]
    return {'count': len(page), 'total': total,
            'note': 'params 键值口径与网页端同源；未列出的键被忽略，'
                    '缺必填项提交时当场报中文原因。gated=true 的卡已被'
                    'deferred.json/环境变量下线，调用会被拒，勿选',
            'tools': page}


def _mtime_sorted_dirs(root, skip_underscore=True):
    """目录名按 mtime 倒序（与 /api/tool/runs 同口径）。"""
    try:
        names = [n for n in os.listdir(root)
                 if not n.startswith('.')
                 and not (skip_underscore and n.startswith('_'))
                 and os.path.isdir(os.path.join(root, n))]
    except OSError:
        return []
    mt = []
    for n in names:
        try:
            mt.append((os.path.getmtime(os.path.join(root, n)), n))
        except OSError:
            mt.append((0, n))
    mt.sort(reverse=True)
    return [n for _m, n in mt]


def _sample_list_impl(limit, offset):
    root = DIRS['results']
    names = _mtime_sorted_dirs(root, skip_underscore=False)
    total = len(names)
    lo = max(0, int(offset))
    page = names[lo:lo + max(1, min(int(limit), 100))]
    from Virus_Platform_Core.pipeline import load_sample_input
    out = []
    for n in page:
        d = os.path.join(root, n)
        info = {'sample': n, 'dir': d}
        try:
            r1, r2, project = load_sample_input(d)
            info['r1'] = r1 or ''
            info['paired'] = bool(r2)
            if project:
                info['project'] = project
        except Exception as e:
            # 单个样品输入缺失/损坏只降级该条目（列表不 500），但原因必须
            # 随条目可见——不再纯静默 pass（2026-11-06 异常吞噬审查）。
            info['input_error'] = f'{type(e).__name__}: {e}'
        info['has_manifest'] = os.path.isfile(os.path.join(d, 'project.json'))
        out.append(info)
    return {'total': total, 'count': len(out), 'samples': out}


def _run_brief(name, base=None):
    """一次运行目录的简报（活区先按 run_real_dir 解析，落空再回落根目录）。"""
    root = base or tool_runs_root()
    if base:
        d = os.path.join(root, name)
    else:
        try:
            d = run_real_dir(name)
        except Exception:                        # noqa: BLE001
            d = None
        if not d or not os.path.isdir(d):
            d = os.path.join(root, name)
    if not os.path.isdir(d):
        return None
    files = []
    for cur, _sub, fns in os.walk(d):
        rel = os.path.relpath(cur, d)
        for fn in fns:
            files.append(fn if rel == '.' else
                         os.path.join(rel, fn).replace(os.sep, '/'))
    return {'run': name, 'out_dir': d, 'n_files': len(files),
            'files': sorted(files)[:40],
            'files_truncated': len(files) > 40}


def _run_list_impl(limit, offset, include_archived=False):
    root = tool_runs_root()
    names = _mtime_sorted_dirs(root)
    total = len(names)
    lo = max(0, int(offset))
    page = names[lo:lo + max(1, min(int(limit), 30))]
    out = []
    for n in page:
        out.append(_run_brief(n))
    d = {'total': total, 'count': len(out), 'runs': [r for r in out if r]}
    if include_archived:
        arch = []
        anames = []
        try:
            aroot = _tool_archive_root(create=False)
        except Exception:                        # noqa: BLE001
            aroot = None
        if aroot and os.path.isdir(aroot):
            anames = _mtime_sorted_dirs(aroot)
            for n in anames[:max(1, min(int(limit), 30))]:
                brief = _run_brief(n, base=aroot)
                if brief:
                    brief['archived'] = True
                    arch.append(brief)
        d['archived'] = arch
        d['archived_total'] = len(anames)
        d['note'] = ('archived 是手动归档区（run/tool_runs/_archive/，'
                     '可用 vp_restore_tool_run 恢复）')
    return d


_BINARY_EXT = ('.gz', '.zip', '.png', '.jpg', '.jpeg', '.pdf', '.bam',
               '.bai', '.ttf', '.exe', '.dll', '.woff', '.woff2',
               '.7z', '.tar', '.bin', '.h5')


def _safe_rel(path):
    """相对路径的安全化：去首尾斜杠、反斜杠归一，拒绝空值与 .. 段。"""
    rel = (path or '').strip().replace('\\', '/').lstrip('/')
    if not rel or '..' in rel.split('/'):
        raise ValueError('无效的相对路径: %r（只接受目录内相对路径，'
                         '不允许绝对路径与 ..）' % path)
    return rel


def _read_text_under(base, rel, max_kb):
    """读 base 目录下 rel 的文本内容（越界/二进制/超限统一在这里拦）。"""
    target = os.path.normpath(os.path.join(base, rel))
    base_n = os.path.normcase(os.path.abspath(base))
    if not os.path.normcase(os.path.abspath(target)).startswith(base_n + os.sep):
        raise ValueError('路径越界（必须在该目录内）: %s' % rel)
    target = check_path(target, must_exist=True, in_platform=True)
    if str(target).lower().endswith(_BINARY_EXT):
        raise ValueError('%s 是二进制产物，本工具只读文本（tsv/json/log/'
                         'fasta/nwk/svg 等）；二进制请直接取用磁盘路径: %s'
                         % (os.path.basename(target), target))
    cap = max(1, min(int(max_kb), 4096)) * 1024
    size = os.path.getsize(target)
    with open(target, 'r', encoding='utf-8', errors='replace') as f:
        text = f.read(cap)
    truncated = size > cap
    if truncated:
        text += '\n...[已截断：文件 %d 字节，上限 %d KB]' % (size, cap // 1024)
    return target, size, text, truncated


def _read_run_file_impl(run, path, max_kb, archived=False):
    if not re.fullmatch(r'[A-Za-z0-9_\-]+', run):
        raise ValueError('无效的运行名: %r' % run)
    if archived:
        aroot = _tool_archive_root(create=False)
        base = os.path.join(aroot, run) if aroot else ''
        if not base or not os.path.isdir(base):
            raise ValueError('归档运行不存在: %s（用 vp_list_tool_runs'
                             '(include_archived=true) 查归档区）' % run)
    else:
        base = run_real_dir(run)
        if not os.path.isdir(base):
            raise ValueError('运行不存在: %s（用 vp_list_tool_runs 查看现有'
                             '运行；已归档的运行加 archived=true）' % run)
    rel = _safe_rel(path)
    target, size, text, truncated = _read_text_under(base, rel, max_kb)
    return _fmt(
        'ok', f"{rel}（{size} 字节{'，已截断' if truncated else ''}）",
        result={'run': run, 'path': rel, 'abs': target, 'size': size,
                'archived': bool(archived),
                'truncated': truncated, 'text': text})


def _sample_dir(sample):
    """样品名 → run/results/<样品> 目录（不存在时给相近名提示）。"""
    name = str(sample or '').strip()
    if (not name or name in ('.', '..') or '/' in name or '\\' in name
            or ':' in name):
        raise ValueError('无效的样品名: %r（用 vp_list_samples 查看现有样品）'
                         % sample)
    d = os.path.join(DIRS['results'], name)
    if not os.path.isdir(d):
        near = [n for n in _mtime_sorted_dirs(DIRS['results'],
                                              skip_underscore=False)
                if name.lower() in n.lower()][:5]
        raise ValueError('样品不存在: %s（用 vp_list_samples 查看现有样品%s）'
                         % (name, ('；相近：' + '、'.join(near)) if near else ''))
    return d


def _read_sample_file_impl(sample, path, max_kb):
    base = _sample_dir(sample)
    rel = _safe_rel(path)
    target, size, text, truncated = _read_text_under(base, rel, max_kb)
    return _fmt(
        'ok', f"{sample}/{rel}（{size} 字节"
              f"{'，已截断' if truncated else ''}）",
        result={'sample': sample, 'path': rel, 'abs': target, 'size': size,
                'truncated': truncated, 'text': text},
        nxt=['阶段性产物也可以用 vp_sample_stages 先看哪些阶段已 done'])


def _stage_disk_state(sample_dir, key, dirname, sfile):
    """按磁盘判定一个阶段，口径与 `pipeline.pipeline_overview` 一致。

    刻意不 import `pipeline_overview`：它会探测 fastp/seqkit/gbdraw/ORF/共识等
    引擎，从而把 matplotlib 拉进工作线程——MCP 是异步 server，工作线程里首次
    import matplotlib 在 Windows 上会卡在字体缓存上（2026-11-02 实测 stdio 调用
    90s 不返回）。这里只读 summary/html/产物文件，不碰引擎。
    """
    import glob as _glob
    import io as _io
    from Virus_Platform_Core.pipeline import STAGE_OUTPUTS
    sdir = os.path.join(sample_dir, dirname)
    summary, has_summary = None, False
    if key == 'report':
        if _glob.glob(os.path.join(sdir, 'report.html')):
            summary, has_summary = {'report': 'report.html'}, True
    elif key == 'fastp':
        if os.path.isfile(os.path.join(sdir, 'fastp_R1.fastq.gz')):
            summary, has_summary = {}, True
    elif sfile or key in ('verify', 'consensus', 'hostana', 'orf', 'orfa',
                          'phylo', 'primer', 'gbdraw', 'subsample', 'fq2fa',
                          'host', 'kvsuite', 'assembly'):
        sp = os.path.join(sdir, sfile or 'summary.json')
        if os.path.isfile(sp):
            try:
                with _io.open(sp, encoding='utf-8', errors='replace') as f:
                    summary = json.load(f)
                has_summary = isinstance(summary, dict)
            except (OSError, ValueError):
                summary, has_summary = None, False
    skipped_reason = ''
    failed_reason = ''
    if has_summary:
        if summary.get('skipped'):
            skipped_reason = str(summary.get('reason') or '')
        if key == 'primer' and not summary.get('n_primers'):
            skipped_reason = skipped_reason or str(summary.get('reason') or '')
        if key == 'gbdraw' and not summary.get('plots'):
            skipped_reason = skipped_reason or str(
                summary.get('reason') or '未生成基因组图（无可用 contig）')
        if summary.get('ok') is False:
            failed_reason = str(summary.get('error') or '阶段失败（详见日志）')
    is_skipped = bool(has_summary and skipped_reason)
    ran = bool(has_summary and not failed_reason)
    outs = []
    if has_summary:
        outs = [rel for rel in (STAGE_OUTPUTS.get(key) or [])
                if os.path.isfile(os.path.join(sdir, rel))]
    return {'summary': summary if has_summary else None,
            'has_summary': has_summary, 'ran': ran,
            'skipped': is_skipped, 'skipped_reason': skipped_reason,
            'failed': bool(failed_reason), 'failed_reason': failed_reason,
            'outputs': outs[:8], 'n_outputs': len(outs)}


def _sample_stages_impl(sample):
    """样品 15 阶段状态（纯磁盘判定，与网页同口径，但不探测外部引擎）。

    回答「这个样品跑到哪了 / 还差哪几步 / 为什么卡住」。判定口径与网页
    `pipeline_overview` 一致（summary 文件 / report.html / fastp 产物 / 依赖表），
    只是不下探「外部工具装没装」：网页里的 unavailable 在 MCP 侧显示为
    ready/blocked，工具状态用 vp_selfcheck 或网页「设置 → 工具探测」看。
    """
    from Virus_Platform_Core.pipeline import (
        PIPELINE_STAGES, STAGE_DEPS, STAGE_OUTPUTS, STAGE_VIEW)
    base = _sample_dir(sample)
    rows, ran_ids = [], set()
    for key, name, dirname, sfile in PIPELINE_STAGES:
        st = _stage_disk_state(base, key, dirname, sfile)
        if st['ran']:
            ran_ids.add(key)
        rows.append({'stage': key, 'name': name, 'dir': dirname, **st,
                     'view': STAGE_VIEW.get(key)})
    for r in rows:
        if r['skipped']:
            r['status'] = 'skipped'
        elif r['failed']:
            r['status'] = 'failed'
        elif r['has_summary']:
            r['status'] = 'done'
        else:
            need = STAGE_DEPS.get(r['stage']) or []
            r['status'] = ('ready' if all(d in ran_ids for d in need)
                           else 'blocked')
    counts = {}
    for r in rows:
        counts[r['status']] = counts.get(r['status'], 0) + 1
    done = [r['stage'] for r in rows if r['status'] == 'done']
    ready = [r['stage'] for r in rows if r['status'] == 'ready']
    blocked = [r['stage'] for r in rows if r['status'] == 'blocked']
    failed = [r['stage'] for r in rows if r['status'] == 'failed']
    skipped = [r['stage'] for r in rows if r['status'] == 'skipped']
    interp = [f"共 {len(rows)} 个阶段：done {len(done)}、failed {len(failed)}、"
              f"skipped {len(skipped)}、ready {len(ready)}、"
              f"blocked {len(blocked)}。"]
    if skipped:
        interp.append('skipped 是「跑过但没结果」（如无可组装 contig / 无引物）：'
                      + '、'.join(skipped) + '。')
    if failed:
        detail = '；'.join(
            f"{r['stage']}：{str(r.get('failed_reason'))[:120]}"
            for r in rows if r['status'] == 'failed')
        interp.append('失败阶段：' + '、'.join(failed) + '（' + detail +
                      '）——先看对应目录的 summary/stats 与任务日志。')
    if blocked:
        interp.append('blocked 表示上游未跑或上游失败；先补上游，别直接跑下游。')
    interp.append('判定只按磁盘标记（summary 文件 / report.html / 产物）；'
                  '不探测外部工具是否安装——网页里的 unavailable 在 MCP 侧'
                  '显示为 ready/blocked。')
    nxt = []
    if ready:
        nxt.append('可续跑：' + '、'.join(ready) +
                   f" → vp_pipeline_start(sample='{sample}', r1=..., "
                   f"stages={ready[:6]})")
    if failed:
        nxt.append('失败阶段按报错修正后重跑；CLI 侧 retry_failed 可整批补跑')
    if not ready and not failed:
        nxt.append('没有可续跑阶段：要么已跑完，要么被 skipped 卡住（看上面 interp）')
    return _fmt(
        'ok', f"{sample}：done {len(done)}/{len(rows)}"
              f"{' · failed ' + str(len(failed)) if failed else ''}"
              f"{' · ready ' + str(len(ready)) if ready else ''}",
        result={'sample': sample, 'dir': base, 'counts': counts,
                'stages': rows},
        interpret=interp, nxt=nxt,
        hint='view 是网页端预览文件（相对阶段目录）；outputs 为已落盘产物名')


def _wait_task_impl(task_id, timeout_sec, poll_sec, log_lines):
    """阻塞等任务到终态（agent 侧最省事的轮询替代品）。"""
    # 上限 900s：网页控制台一次工具调用最多等 960s（mcp_console._CALL_TIMEOUT），
    # 再长会让控制台先超时、而 agent 仍卡在循环里。
    timeout_sec = min(max(1, int(timeout_sec)), 900)
    poll_sec = min(max(0.5, float(poll_sec)), 30.0)
    deadline = time.monotonic() + timeout_sec
    last = None
    while True:
        last = _task_snapshot(task_id, log_lines=log_lines,
                              result_max_kb=128)
        if last.get('status') in ('done', 'failed', 'cancelled'):
            last['timed_out'] = False
            last['waited_sec'] = round(max(0.0, int(timeout_sec) -
                                           (deadline - time.monotonic())), 1)
            return last
        if time.monotonic() >= deadline:
            last['timed_out'] = True
            last['next'] = list(last.get('next') or []) + [
                f'超时未到终态；继续 vp_wait_task(task_id="{task_id}") 或 '
                f'vp_get_task 轮询']
            return last
        time.sleep(max(1, min(int(poll_sec), 15)))


def _archive_task_impl(task_id):
    ok, msg = tm.archive(task_id)
    if not ok:
        raise ValueError(msg)
    return _fmt('ok', f"任务 {task_id} 已归档", result={
        'task_id': task_id, 'archived': True},
        nxt=['vp_list_tasks(include_archived=true) 可在历史里看到它',
             f"要恢复就调 vp_unarchive_task(task_id='{task_id}')"])


def _unarchive_task_impl(task_id):
    ok, msg = tm.unarchive(task_id)
    if not ok:
        raise ValueError(msg)
    return _fmt('ok', f"任务 {task_id} 已从归档恢复到运行列表",
                result={'task_id': task_id, 'archived': False},
                nxt=['vp_list_tasks 里可再次看到它'])


# ---- 工具箱运行目录的归档/恢复（网页「历史运行」盒同一套语义）----

_TOOL_ARCHIVE_DIR = '_archive'


def _tool_archive_root(create=True):
    p = check_path(os.path.join(tool_runs_root(), _TOOL_ARCHIVE_DIR),
                   must_exist=False, in_platform=True)
    if create:
        os.makedirs(p, exist_ok=True)
    return p


def _busy_on_dir(path):
    """该目录上有 running/queued 任务时返回那条记录。"""
    key = os.path.normcase(os.path.abspath(path))
    with tm.lock:
        for r in tm.tasks.values():
            if (r.get('status') in ('running', 'queued') and r.get('out_dir')
                    and os.path.normcase(
                        os.path.abspath(str(r['out_dir']))) == key):
                return r
    return None


def _move_dir_retry(src, dst):
    import gc as _gc
    import shutil as _sh
    for _ in range(2):
        try:
            _sh.move(src, dst)
            return
        except OSError:
            _gc.collect()
            time.sleep(0.3)
    _sh.move(src, dst)


def _archive_tool_run_impl(run):
    if not re.fullmatch(r'[A-Za-z0-9_\-]+', run) or run.startswith('_'):
        raise ValueError('无效的运行名: %r' % run)
    raw = os.path.join(tool_runs_root(), run)
    if not os.path.isdir(raw):
        raise ValueError('运行不存在: %s（用 vp_list_tool_runs 查看活动区）'
                         % run)
    src = check_path(raw, must_exist=True, in_platform=True)
    dst = os.path.join(_tool_archive_root(), run)
    if os.path.exists(dst):
        raise ValueError(f'归档区已有同名运行：{run}（先 vp_list_tool_runs '
                         'include_archived=true 看清现状）')
    busy = _busy_on_dir(src)
    if busy:
        raise ValueError('该运行目录上有任务正在跑（%s %s）；请先 '
                         'vp_cancel_task' % (busy.get('id'), busy.get('name')))
    _move_dir_retry(src, dst)
    return _fmt('ok', f"运行 {run} 已移入归档区（可恢复，不删数据）",
                result={'run': run, 'archived_to': dst},
                nxt=['vp_list_tool_runs(include_archived=true) 查看归档区',
                     f"恢复用 vp_restore_tool_run(run='{run}')"])


def _restore_tool_run_impl(run):
    if not re.fullmatch(r'[A-Za-z0-9_\-]+', run) or run.startswith('_'):
        raise ValueError('无效的运行名: %r' % run)
    aroot = _tool_archive_root(create=False)
    raw = os.path.join(aroot, run) if aroot else ''
    if not raw or not os.path.isdir(raw):
        raise ValueError('归档记录不存在: %s（用 vp_list_tool_runs'
                         '(include_archived=true) 查看归档区）' % run)
    src = check_path(raw, must_exist=True, in_platform=True)
    dst = os.path.join(tool_runs_root(), run)
    if os.path.exists(dst):
        raise ValueError(f'活区已有同名运行：{run}（改不了名，先处理活区那份）')
    _move_dir_retry(src, dst)
    return _fmt('ok', f"运行 {run} 已恢复回活区",
                result={'run': run, 'restored_to': dst})


def _platform_info_impl():
    info = {'server': 'virusplatform_mcp', 'version': SERVER_VERSION,
            'platform_root': PLATFORM_ROOT,
            'python': sys.executable, 'results_dir': DIRS['results'],
            'tool_runs_dir': tool_runs_root(),
            'databases_dir': DIRS['databases'],
            'threads_default': getattr(cfg, 'threads', None),
            'language': getattr(cfg, 'lang', 'zh')}
    enabled = _enabled_tool_names()
    info['tools_enabled'] = enabled
    info['tools_count'] = len(enabled)
    info['tools_gated'] = sorted(_GATED)
    info['fingerprint'] = _capability_fingerprint(enabled)
    try:
        from Virus_Platform_Core.kunpeng import db_ready
        info['host_db_ready'] = bool(db_ready(cfg.databases['host']))
        info['virus_db_ready'] = bool(db_ready(cfg.databases['virus']))
    except Exception as e:
        info['db_check_error'] = repr(e)
    problems = _platform_check()
    info['ready'] = not problems
    if problems:
        info['problems'] = problems
    if problems:
        info['summary'] = '平台模块异常：' + '; '.join(problems)
    else:
        db_ok = info.get('host_db_ready') and info.get('virus_db_ready')
        info['summary'] = (f"平台就绪（MCP 工具 {info['tools_count']} 个；"
                           f"{'库全部就绪' if db_ok else '注意：库未就绪'}）")
    return info


# ======================================================================
# MCP 工具层（签名即 schema；Annotated+Field 出约束，docstring 即描述）
# ======================================================================

@mcp.tool(name='vp_platform_info', annotations=_RO)
async def vp_platform_info() -> dict:
    """平台环境总览：根目录、结果/运行/数据库目录、宿主与病毒库就绪状态、
    默认线程数、就绪体检结论；并带版本号、启用工具清单与能力指纹
    （fingerprint 变了即服务端能力有增删）。

    返回 {server, version, platform_root, results_dir, tool_runs_dir,
    databases_dir, host_db_ready, virus_db_ready, threads_default,
    language, ready, tools_enabled, tools_count, tools_gated,
    fingerprint}。首次接入先调本工具确认环境，再选后续动作。
    """
    return await anyio.to_thread.run_sync(_platform_info_impl)

@mcp.tool(name='vp_selfcheck', annotations=_RO)
async def vp_selfcheck() -> dict:
    """平台自检（等价 CLI selfcheck）：模块/外部工具/数据库/磁盘/依赖。

    返回 {status, checks:[{name, ok, detail}], problems:[...], summary}。
    接不上任何东西时先跑本工具，比逐个试工具省事；problems 非空即是
    需要先修的环境问题（缺依赖/库未构建/磁盘不足）。
    """
    def _impl():
        rep = _selfcheck_report()
        return _fmt('ok' if rep['ok'] else 'warn', rep['summary'],
                    result={'checks': rep['checks']},
                    problems=rep['problems'],
                    nxt=['problems 非空先修环境（缺外部工具看「设置 → 工具探测」）'
                         if rep['problems'] else ''])
    return await anyio.to_thread.run_sync(_impl)

@mcp.tool(name='vp_anchor_active', annotations=_RO)
async def vp_anchor_active(
        limit: Annotated[Optional[int], Field(
            ge=1, le=40, description='最多返回多少个样品（默认 6）')] = 6
) -> dict:
    """样品锚总览（只读）：正在分析 / 疑似中断（30 分钟无账本事件，多半是
    平台重启留下的运行记录）/ 最近完成的样品。回答「现在在跑什么」「有没有
    卡住的样品」比任务中心更准——running/stale 来自 anchor.json 的运行记录。

    返回 {running:[{sample,status,stage,stages_done/total,started}...],
    recent:[{sample,status,finished,dur_s}...], now}。
    """
    def _impl():
        from Virus_Platform_Core import anchor as _anchor
        a = _anchor.active(limit=int(limit or 6))
        slim = lambda lst: [{'sample': s.get('sample'),
                             'status': s.get('status'),
                             'stage': s.get('stage') or s.get('stage_done') or '',
                             'stages_done': s.get('stages_done'),
                             'stages_total': s.get('stages_total'),
                             'started': s.get('started') or '',
                             'finished': s.get('finished') or ''}
                            for s in (lst or [])]
        return _fmt('ok', f"running/stale {len(a.get('running') or [])} · "
                          f"recent {len(a.get('recent') or [])}",
                    result={'running': slim(a.get('running')),
                            'recent': slim(a.get('recent'))[:3],
                            'now': a.get('now')})
    return await anyio.to_thread.run_sync(_impl)

@mcp.tool(name='vp_anchor_verify', annotations=_RO)
async def vp_anchor_verify(
        sample: Annotated[str, Field(
            min_length=1, max_length=120,
            description='样品名或锚 slug')] ) -> dict:
    """样品锚核对（只读，不改任何文件）：重算输入文件 sha256 与账本 hash
    链，报一致/不一致与具体问题清单。回答「结果有没有被改过」「输入文件
    是否被替换」用本工具；大文件按 HASH_CAP 上限跳过（rehash 字段标注）。

    返回 {ok, slug, seq, head, problems:[...], checked}；ok=false 时 problems
    逐条给出（账本 seq 不连续 / prev 断链 / 输入摘要已变 / 文件读不到）。
    """
    def _impl():
        from Virus_Platform_Core import anchor as _anchor
        v = _anchor.verify(str(sample or '').strip())
        if v.get('error'):
            return _fmt('fail', str(v['error']), result=v)
        return _fmt('ok' if v.get('ok') else 'warn',
                    ('账本链完整 · seq=%s' % v.get('seq'))
                    if v.get('ok') else '发现 %d 处不一致' % len(v.get('problems') or []),
                    result=v)
    return await anyio.to_thread.run_sync(_impl)

@mcp.tool(name='vp_list_tools', annotations=_RO)
async def vp_list_tools(
        query: Annotated[Optional[str], Field(
            max_length=80,
            description='关键词过滤（工具名/中英标题/说明/参数键）')] = None,
        weight: Annotated[Optional[str], Field(
            description='只留 heavy 或 light 档位')] = None,
        need_db: Annotated[Optional[bool], Field(
            description='true=只要依赖病毒库的工具；false=只要不依赖的')] =
        None,
        limit: Annotated[Optional[int], Field(
            ge=1, le=64, description='最多返回条数（默认全部）')] = None,
        offset: Annotated[int, Field(ge=0)] = 0) -> dict:
    """工具箱清单：名称、中英文标题、是否依赖病毒库、heavy/light 档位、
    必填参数与参数口径说明（doc）。

    返回 {count, total, note, tools:[{tool, title, title_en, need_db,
    weight, gated, required_params, params, doc}]}；count 是本页条数、
    total 是过滤后总数。提交运行用 vp_run_tool(tool=..., params={...})。
    """
    return await anyio.to_thread.run_sync(
        _tool_list_impl, query, weight, need_db, limit, offset)

@mcp.tool(name='vp_search_operations', annotations=_RO)
async def vp_search_operations(
        query: Annotated[str, Field(min_length=1, max_length=80,
                                    description='搜索关键词（中英皆可：'
                                                '组装 / kvsuite / 引物 / 提交）')],
        limit: Annotated[Optional[int], Field(
            ge=1, le=30, description='最多返回条数，默认 8')] = None,
) -> dict:
    """在平台操作目录里搜索（工具箱工具 + 管道阶段 + 执行单动作，54+ 条）。

    返回 {query, total, items:[…]}，条目含 id/kind/中英标题/一句话说明/调用
    口径。不熟悉的操作先搜再调，不要猜 id；单条参数口径用
    vp_describe_operation(op_id, schema=true)。
    """
    from Virus_Platform_Core import agent_catalog
    d = await anyio.to_thread.run_sync(agent_catalog.search, query,
                                       int(limit or 8))
    d['note'] = ('kind=tool→vp_run_tool；kind=stage→vp_pipeline_start；'
                 'kind=action→坞内 propose_action 起草待批（计划≠已执行）')
    return d

@mcp.tool(name='vp_describe_operation', annotations=_RO)
async def vp_describe_operation(
        op_id: Annotated[str, Field(min_length=1, max_length=60,
                                    description='操作 id（来自 '
                                                'vp_search_operations）')],
        schema: Annotated[Optional[bool], Field(
            description='true=附参数 schema')] = None,
) -> dict:
    """单条操作详情：参数、必填、怎么调（MCP/执行单/HTTP/CLI）。

    schema=true 时附 parameter_schema（工具箱=真实表；阶段=分析请求体
    白名单+可调键边界；执行单动作=必填键说明）。未知 op_id 返回
    {found:false, hint}，不报错。
    """
    from Virus_Platform_Core import agent_catalog

    def _impl():
        d = agent_catalog.describe(op_id)
        if d is None:
            return {'found': False, 'op_id': op_id,
                    'hint': '用 vp_search_operations 搜索，'
                            '或 CLI：python main.py agent-ops'}
        out = dict(d)
        out['found'] = True
        if schema:
            out['parameter_schema'] = agent_catalog.parameter_schema(op_id)
        return out

    return await anyio.to_thread.run_sync(_impl)

@mcp.tool(name='vp_run_tool')
async def vp_run_tool(
        tool: Annotated[str, Field(min_length=1, max_length=40,
                                   description='工具名（vp_list_tools 的 '
                                               'tool 键）')],
        params: Annotated[Optional[dict], Field(
            description='工具参数字典（键值口径见 vp_list_tools 的 doc；'
                        '文件路径可用绝对路径或平台根相对路径）')] = None,
        threads: Annotated[Optional[int], Field(
            ge=1, le=256, description='线程数（缺省用平台默认）')] = None,
        out_dir: Annotated[Optional[str], Field(
            max_length=500,
            description='可选自定义产物父目录（产物写 <out_dir>/<run_name>/）'
                        '；缺省写 run/tool_runs/<run_name>/')] = None) -> dict:
    """执行工具箱任一工具（格式转换/质控/宿主去除/组装/鉴定定量/建树/SDT/
    引物/基因组图/dsRNA/CRISPR/一键链……全量）。

    提交即返回 {task_id, run, out_dir, weight, hint}，不同步等待。
    必填参数缺失/库未就绪当场报中文原因。轮询用 vp_get_task，
    产物文本用 vp_read_run_file(run=...)。
    """
    return await anyio.to_thread.run_sync(
        _submit_tool_run, tool, params or {}, threads, out_dir)

@mcp.tool(name='vp_pipeline_stages', annotations=_RO)
async def vp_pipeline_stages() -> dict:
    """流水线阶段清单：15 个阶段键、中英文名、默认全流程序列、功能分组。

    返回 {stage_order, default_stages, names, names_en,
    groups:[{group, stages}]}。阶段键喂 vp_pipeline_start 的 stages。
    """
    return {'stage_order': list(STAGE_ORDER),
            'default_stages': list(DEFAULT_ANALYZE_STAGES),
            'names': dict(STAGE_NAMES), 'names_en': dict(STAGE_NAMES_EN),
            'groups': [{'group': g, 'stages': list(ss)}
                       for g, ss in STAGE_GROUPS]}


@mcp.tool(name='vp_pipeline_start')
async def vp_pipeline_start(
        sample: Annotated[str, Field(min_length=1, max_length=120,
                                     description='样品名（自动安全化；产物'
                                                 '目录 run/results/<样品名>/）')],
        r1: Annotated[str, Field(min_length=1, max_length=1000,
                                 description='R1 FASTQ(.gz) 路径（绝对或平台'
                                             '根相对）')],
        r2: Annotated[Optional[str], Field(
            max_length=1000, description='R2 FASTQ(.gz)（双端必填）')] = None,
        stages: Annotated[Optional[list[str]], Field(
            description='阶段键列表（vp_pipeline_stages 的键）；缺省跑默认 '
                        '13 步全流程')] = None,
        threads: Annotated[Optional[int], Field(ge=1, le=256)] = None,
        db_host: Annotated[Optional[str], Field(
            max_length=1000, description='宿主库目录（缺省平台主宿主库）')] = None,
        db_virus: Annotated[Optional[str], Field(
            max_length=1000, description='病毒库目录（缺省平台主病毒库）')] = None,
        confidence: Annotated[float, Field(
            ge=0, le=1, description='kunpeng 分类置信度')] = 0.0,
        assembly_mode: Annotated[str, Field(
            max_length=20, description='SPAdes 模式 '
                                       '(rnaviral/srna/metaviral/rna...)')] =
        'rnaviral',
        memory_gb: Annotated[int, Field(
            ge=1, le=1024, description='SPAdes 内存上限 GB')] = 64,
        min_contig_len: Annotated[int, Field(ge=1, le=100000)] = 200,
        assembly_input: Annotated[str, Field(
            max_length=20,
            description='③ 组装输入 virus=病毒筛选后 reads（默认）|'
                        'all=全部去宿主 reads')] = 'virus',
        min_viral_pairs: Annotated[int, Field(
            ge=0, le=10**9,
            description='③ 病毒 reads 低于该对数时回退全部去宿主 reads，'
                        '默认 500')] = 500,
        do_trim: Annotated[bool, Field(
            description='⑧ 引物设计前比对清剪，默认开')] = True,
        subsample: Annotated[int, Field(
            ge=0, description='子采样读对数（0=不子采样）')] = 0,
        tree_tool: Annotated[str, Field(max_length=20)] = 'fasttree',
        primer_mode: Annotated[str, Field(max_length=20)] = 'conserved',
        min_orf_aa: Annotated[int, Field(ge=1, le=5000)] = 100,
        ncbi_refs: Annotated[Optional[str], Field(
            max_length=100,
            description='NCBI 参考集合名（main.py ncbi-dl 先下载；进化树'
                        '扩充用）')] = None,
        kv_lib: Annotated[Optional[str], Field(
            max_length=300, description='已知病毒鉴定库键或库目录（②b 段；'
                                        '缺省平台默认库）')] = None,
        subsample_mode: Annotated[str, Field(max_length=20)] = 'prefix',
        subsample_seed: Annotated[int, Field(ge=0)] = 42,
        subsample_prop: Annotated[float, Field(ge=0.0, le=1.0)] = 0.0,
        tree_sampling: Annotated[str, Field(
            max_length=20,
            description='建树序列取样 blast 等，默认 blast')] = 'blast',
        top_n_refs: Annotated[int, Field(
            ge=1, le=100, description='进化分析取近缘参考数，默认 10')] = 10,
        min_pident: Annotated[Optional[float], Field(
            ge=0, le=100, description='⑥b 层1 Identity% 门槛')] = None,
        min_qcov: Annotated[Optional[float], Field(
            ge=0, le=100, description='⑥b 层1 覆盖% 门槛')] = None,
        fastp_dedup: Annotated[bool, Field(description='⓪ 质控去重')] = False,
        do_fq2fa: Annotated[bool, Field(
            description='⓪b FASTQ→FASTA 转换，默认开')] = True,
        gbdraw_max: Annotated[int, Field(
            ge=1, le=200, description='⑨ 最多出图数，默认 12')] = 12,
        gbdraw_fasta: Annotated[Optional[str], Field(
            max_length=1000, description='⑨ 覆盖出图 FASTA（缺省用 ③ 产物）')] = None,
        gbdraw_ann: Annotated[Optional[str], Field(
            max_length=1000, description='⑨ 覆盖注释 GFF3/GenBank')] = None,
        plot_engine: Annotated[str, Field(
            max_length=20, description='绘图引擎 auto/gbdraw/dfv')] = 'auto',
        verify_host: Annotated[str, Field(
            max_length=60, description='③b 宿主筛选 all 等，默认 all')] = 'all',
        verify_methods: Annotated[str, Field(
            max_length=60,
            description='③b 证据方法 blastx,bfvd,cdd（逗号分隔）')] =
        'blastx,bfvd,cdd',
        verify_combine: Annotated[str, Field(
            max_length=20, description='③b 合并方式 union/intersection')] =
        'union',
        force: Annotated[bool, Field(description='强制重跑全部阶段')] = False,
        specificity: Annotated[bool, Field(
            description='引物宿主特异性 BLAST 检查')] = False) -> dict:
    """启动样品全流程分析（⓪质控→①宿主去除→②b识别定量→③组装→…→⑩报告）。

    与网页端/CLI 同一编排引擎（断点续跑、可选阶段自动降级都保留）。
    提交即返回 {task_id, sample, sample_dir, stages, hint}；轮询
    vp_get_task 看阶段进度（stage/pct/msg），终态 result 为各阶段摘要。
    """
    args = {k: v for k, v in dict(
        sample=sample, r1=r1, r2=r2, stages=stages, threads=threads,
        db_host=db_host, db_virus=db_virus, confidence=confidence,
        assembly_mode=assembly_mode, memory_gb=memory_gb,
        min_contig_len=min_contig_len, assembly_input=assembly_input,
        min_viral_pairs=min_viral_pairs, do_trim=do_trim,
        subsample=subsample,
        tree_tool=tree_tool, primer_mode=primer_mode,
        min_orf_aa=min_orf_aa, ncbi_refs=ncbi_refs, force=force,
        specificity=specificity).items() if v is not None}
    return await anyio.to_thread.run_sync(_submit_pipeline, args)

@mcp.tool(name='vp_get_task', annotations=_RO_LIVE)
async def vp_get_task(
        task_id: Annotated[str, Field(
            min_length=4, max_length=32,
            description='vp_run_tool / vp_pipeline_start 返回的 task_id')],
        log_lines: Annotated[int, Field(
            ge=0, le=500, description='随快照返回的日志尾行数')] = 30,
        result_max_kb: Annotated[int, Field(
            ge=1, le=4096,
            description='终态 result 的文本上限 KB，超出截断为 '
                        '{_truncated,_note,_head}')] = 256) -> dict:
    """查单个任务快照：状态（queued/running→终态 done/failed/cancelled）、
    阶段、百分比、消息、日志尾、终态结果。

    返回固定键集合 {id, name, status, stage, pct, msg, error, eta,
    out_dir, link, started_h, finished_h, log, result, archived,
    external}——稳定投影，键恒在；error 非空即失败原因。result 超限时
    截断并标注完整位置。已归档（archived=true）与平台重启前的任务同样
    可查：内存没有就回落到磁盘归档记录。
    """
    return await anyio.to_thread.run_sync(
        _task_snapshot, task_id, log_lines, result_max_kb)

@mcp.tool(name='vp_wait_task', annotations=_RO_LIVE)
async def vp_wait_task(
        task_id: Annotated[str, Field(min_length=4, max_length=32)],
        timeout_sec: Annotated[int, Field(
            ge=1, le=3600,
            description='最长等待秒数；到期返回最后快照并 timed_out=true')] =
        120,
        poll_sec: Annotated[int, Field(ge=1, le=60,
                                       description='轮询间隔秒')] = 3,
        log_lines: Annotated[int, Field(ge=0, le=500)] = 30) -> dict:
    """等任务到终态再返回（省掉 agent 侧自己轮询）。

    返回与 vp_get_task 同构的快照，另加 timed_out（bool）与
    waited_sec。timed_out=false 表示到了 done/failed/cancelled；true 表示
    超时仍在跑，继续调一次即可，或改 vp_get_task 做非阻塞查看。
    """
    return await anyio.to_thread.run_sync(
        _wait_task_impl, task_id, timeout_sec, poll_sec, log_lines)

@mcp.tool(name='vp_list_tasks', annotations=_RO)
async def vp_list_tasks(
        limit: Annotated[int, Field(ge=1, le=100)] = 20,
        include_archived: Annotated[bool, Field(
            description='并入磁盘归档任务（手动归档 + 平台重启前的历史，'
                        'run/tasks/*.json 与 run/tasks/_archive）')] = False,
        status: Annotated[Optional[str], Field(
            description='只留该状态：queued/running/done/failed/cancelled')] =
        None,
        name: Annotated[Optional[str], Field(
            max_length=80,
            description='任务名关键词（不区分大小写，如「公共检索」）')] = None,
        ) -> dict:
    """近期任务列表（新到旧，分页）：状态/进度/产物目录，不带日志。

    返回 {count, tasks:[...]}；include_archived=true 时另带 archived
    （稳定键投影）与 archived_count。status/name 过滤对 active 与
    archived 同时生效。逐任务日志用 vp_get_task / vp_get_task_log
    （两者对归档任务均可用）。
    """
    return await anyio.to_thread.run_sync(
        _task_list, limit, include_archived, status, name)

@mcp.tool(name='vp_cancel_task', annotations=_DESTR)
async def vp_cancel_task(
        task_id: Annotated[str, Field(min_length=4, max_length=32)]) -> dict:
    """取消运行中/排队中的任务（对当前外部步骤硬停止进程树）。

    返回 {task_id, accepted, status, msg}。accepted=false 表示任务
    不存在或已结束；取消后用 vp_get_task 确认终态。
    """

    def _impl():
        ok = tm.cancel(task_id)
        snap = tm.snapshot(task_id, log_lines=0, include_result=False) or {}
        return {'task_id': task_id, 'accepted': bool(ok),
                'status': snap.get('status') or 'unknown',
                'msg': snap.get('msg') or ''}
    return await anyio.to_thread.run_sync(_impl)

@mcp.tool(name='vp_archive_task', annotations=_WRITE)
async def vp_archive_task(
        task_id: Annotated[str, Field(min_length=4, max_length=32,
                                      description='已结束的任务 id')]) -> dict:
    """把已结束的任务移入手动归档（可恢复，不删日志与结果文件）。

    归档是**手动动作**：切模块、重连、整页刷新都不会归档任何任务。
    守卫：运行中/排队中的任务拒绝（先 vp_cancel_task）；外部任务（下载
    批次等）请在来源模块处理。返回 {task_id, archived}；之后
    vp_list_tasks(include_archived=true) 可见，vp_get_task 仍可读。
    """
    return await anyio.to_thread.run_sync(_archive_task_impl, task_id)

@mcp.tool(name='vp_unarchive_task', annotations=_WRITE)
async def vp_unarchive_task(
        task_id: Annotated[str, Field(min_length=4, max_length=32,
                                      description='已归档任务 id')]) -> dict:
    """把归档任务恢复到运行列表（重写任务记录，日志/结果文件不动）。

    返回 {task_id, archived: false}；恢复后 vp_get_task /
    vp_get_task_log 正常可读。
    """
    return await anyio.to_thread.run_sync(_unarchive_task_impl, task_id)

@mcp.tool(name='vp_get_task_log', annotations=_RO)
async def vp_get_task_log(
        task_id: Annotated[str, Field(min_length=4, max_length=32)],
        lines: Annotated[int, Field(ge=1, le=2000)] = 400) -> dict:
    """任务全量日志尾部（含已完成/归档任务：内存→磁盘 log→归档 json
    三级回源）。

    返回 {task_id, lines, log:[...]}。
    """

    def _impl():
        got = tm.full_log(task_id, lines=max(1, min(int(lines), 2000)))
        return {'task_id': task_id, 'lines': len(got or []),
                'log': got or []}
    return await anyio.to_thread.run_sync(_impl)

@mcp.tool(name='vp_list_samples', annotations=_RO)
async def vp_list_samples(
        limit: Annotated[int, Field(ge=1, le=100)] = 20,
        offset: Annotated[int, Field(ge=0)] = 0) -> dict:
    """已接入样品清单（run/results/，按最近变动倒序，分页）：输入文件、
    是否双端、所属项目、是否有 project.json 清单。

    返回 {total, count, samples:[{sample, dir, r1, paired, project,
    has_manifest}]}。样品输入即 vp_pipeline_start 所需的 r1/r2 参考。
    """
    return await anyio.to_thread.run_sync(
        _sample_list_impl, limit, offset)

@mcp.tool(name='vp_list_tool_runs', annotations=_RO)
async def vp_list_tool_runs(
        limit: Annotated[int, Field(ge=1, le=30)] = 10,
        offset: Annotated[int, Field(ge=0)] = 0,
        include_archived: Annotated[bool, Field(
            description='并入手动归档区（run/tool_runs/_archive/）的清单')] =
        False) -> dict:
    """近期工具/链式运行清单（run/tool_runs/，按最近变动倒序，分页）：
    产物目录与文件清单（最多 40 个文件名）。

    返回 {total, count, runs:[{run, out_dir, n_files, files,
    files_truncated}]}；include_archived=true 时另带 archived 与
    archived_total。读具体文件内容用 vp_read_run_file。
    """
    return await anyio.to_thread.run_sync(
        _run_list_impl, limit, offset, include_archived)

@mcp.tool(name='vp_read_run_file', annotations=_RO)
async def vp_read_run_file(
        run: Annotated[str, Field(min_length=1, max_length=80,
                                  description='运行名（vp_list_tool_runs '
                                              '返回的 run）')],
        path: Annotated[str, Field(min_length=1, max_length=500,
                                   description='运行目录内相对路径，如 '
                                               'kvsuite/filter/filtered.tsv')],
        max_kb: Annotated[int, Field(
            ge=1, le=4096, description='返回文本上限 KB（超出截断并标注）')] =
        512,
        archived: Annotated[bool, Field(
            description='true 时从 run/tool_runs/_archive/ 读取已归档的运行')] =
        False) -> dict:
    """读某次运行的文本产物（tsv/json/log/fasta/nwk/svg 等）内容。

    路径限定在该运行目录内（防越界）；二进制产物明确拒收并提示磁盘
    路径。运行被手动归档后用 archived=true 仍可读。
    返回 {run, path, abs, size, archived, truncated, text}。
    """
    return await anyio.to_thread.run_sync(
        _read_run_file_impl, run, path, max_kb, archived)

@mcp.tool(name='vp_read_sample_file', annotations=_RO)
async def vp_read_sample_file(
        sample: Annotated[str, Field(min_length=1, max_length=120,
                                     description='样品名（vp_list_samples 的 '
                                                 'sample）')],
        path: Annotated[str, Field(
            min_length=1, max_length=500,
            description='样品结果目录内相对路径，如 '
                        '02b_kvsuite/identify/all_viruses.summary.tsv')],
        max_kb: Annotated[int, Field(
            ge=1, le=4096, description='返回文本上限 KB（超出截断并标注）')] =
        512) -> dict:
    """读样品结果目录（run/results/<样品>/）里的文本产物内容。

    与网页结果中心读的是同一份落盘文件；路径限定在样品目录内（防
    越界），二进制产物（图片/压缩包/BAM 等）明确拒收并给磁盘路径。
    返回 {sample, path, abs, size, truncated, text}。先
    vp_sample_stages 看哪些阶段已 done，能少走弯路。
    """
    return await anyio.to_thread.run_sync(
        _read_sample_file_impl, sample, path, max_kb)

@mcp.tool(name='vp_sample_stages', annotations=_RO)
async def vp_sample_stages(
        sample: Annotated[str, Field(min_length=1, max_length=120,
                                     description='样品名（vp_list_samples 的 '
                                                 'sample）')]) -> dict:
    """样品 15 阶段状态表：done/failed/skipped/ready/blocked、判定摘要、
    产物名与网页预览文件。

    回答「跑到哪了 / 还差哪步 / 为什么卡住」。返回 {sample, dir,
    counts, stages:[{stage, name, dir, status, summary, outputs,
    n_outputs, view}]}；blocked=上游没跑（含上游失败）。判定只按磁盘标记
    （.done / summary 文件 / 依赖表），不探测外部工具：网页里的
    unavailable 在 MCP 侧会显示为 ready/blocked，工具装没装用
    vp_selfcheck 看。可续跑阶段直接给在 next 里。
    """
    return await anyio.to_thread.run_sync(_sample_stages_impl, sample)

def _resolve_report_target(target, kind):
    """target → (真实目录, 规范化 kind)。kind=auto 时先当样品再当工具运行。"""
    name = str(target or '').strip()
    if not name:
        raise ValueError('target 不能为空（样品名或工具运行名）')
    k = str(kind or 'auto').strip().lower()
    if k not in ('auto', 'sample', 'tool_run'):
        raise ValueError("kind 只允许 auto/sample/tool_run，收到 %r" % kind)
    # 也允许直接给「平台内的目录路径」：工具自定义 out_dir、手工摆放的产物
    # 目录都能直接出一份结论文档；平台根之外的路径一律拒绝。
    if os.path.isabs(name) or '/' in name or '\\' in name:
        cand = os.path.abspath(name)
        if not os.path.isdir(cand):
            raise ValueError('目录不存在: %s（也可以只给样品名或运行名）' % name)
        base = os.path.abspath(PLATFORM_ROOT)
        if not (cand == base or cand.startswith(base + os.sep)):
            raise ValueError('目录不在平台根内，拒绝生成: %s' % cand)
        return cand, ('sample' if os.path.isfile(
            os.path.join(cand, 'project.json')) else 'dir')
    sample_dir = None
    if k in ('auto', 'sample'):
        try:
            sample_dir = _sample_dir(name)
        except ValueError as e:
            if k == 'sample':
                raise
            sample_err = str(e)
    if sample_dir:
        return sample_dir, 'sample'
    # 工具运行：run/tool_runs/<run> 或归档 run/tool_runs/_archive/<run>
    roots = [DIRS['tool_runs'], _tool_archive_root(create=False)]
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        d = os.path.join(root, name)
        if os.path.isdir(d) and os.path.abspath(d).startswith(
                os.path.abspath(root) + os.sep):
            return d, 'tool_run'
        # 允许「未带时间戳后缀」的前缀式简写，唯一命中才接受
        try:
            cands = [n for n in os.listdir(root)
                     if n.startswith(name) and os.path.isdir(
                         os.path.join(root, n))]
        except OSError:
            cands = []
        if len(cands) == 1:
            d = os.path.join(root, cands[0])
            if os.path.abspath(d).startswith(os.path.abspath(root) + os.sep):
                return d, 'tool_run'
        if len(cands) > 1:
            raise ValueError('工具运行名 %r 不唯一，候选：%s（用 '
                             'vp_list_tool_runs 取完整 run 名）'
                             % (name, '、'.join(sorted(cands)[:6])))
    if k == 'tool_run':
        raise ValueError('工具运行不存在: %s（用 vp_list_tool_runs 查看）'
                         % name)
    raise ValueError(sample_err if sample_err else
                     '目标不存在: %s（样品用 vp_list_samples，工具运行用 '
                     'vp_list_tool_runs）' % name)


def _html_digest(path, max_kb):
    """report_v2.html → 纯文本摘要（去 script/style/标签，折叠空行）。"""
    if not max_kb:
        return ''
    import io as _io
    import re as _re
    try:
        with _io.open(path, encoding='utf-8', errors='replace') as f:
            html = f.read()
    except OSError:
        return ''
    html = _re.sub(r'(?is)<(script|style)\b.*?</\1>', ' ', html)
    html = _re.sub(r'(?is)<head\b.*?</head>', ' ', html)
    html = _re.sub(r'(?s)<!--.*?-->', ' ', html)
    html = _re.sub(r'(?i)</(p|div|section|tr|li|h[1-6]|table|pre)>', '\n', html)
    html = _re.sub(r'(?i)<br\s*/?>', '\n', html)
    html = _re.sub(r'(?s)<[^>]+>', '', html)
    text = (html.replace('&nbsp;', ' ').replace('&amp;', '&')
                .replace('&lt;', '<').replace('&gt;', '>')
                .replace('&quot;', '"').replace('&#x27;', "'"))
    text = _re.sub(r'[ \t\u3000]+', ' ', text)
    text = _re.sub(r'\n\s*\n+', '\n', text).strip()
    limit = int(max_kb) * 1024
    if len(text) > limit:
        text = text[:limit] + '\n…（摘要已截断，全文见 html 路径）'
    return text


def _build_chain_report_impl(root, force=False, digest_kb=24):
    """一键流程（_chain.json）→「材料 / 方法 / 结果」规范报告。"""
    t0 = time.time()
    try:
        from Virus_Platform_Core.chain_report import build_chain_report
    except Exception as e:                      # noqa: BLE001
        return _fmt('error', '规范报告生成器不可用: %s: %s'
                             % (type(e).__name__, e),
                    nxt=['python main.py selfcheck 检查核心模块完整性'])
    try:
        out = build_chain_report(root, force=bool(force))
    except Exception as e:                      # noqa: BLE001
        return _fmt('error', '规范报告生成失败（%s）: %s: %s'
                             % (os.path.basename(root), type(e).__name__, e),
                    result={'target': os.path.basename(root),
                            'kind': 'chain', 'dir': root},
                    nxt=['先用 vp_get_task / vp_list_tool_runs 确认运行已完成'])
    model = out.get('model') or {}
    html_path = out.get('html') or ''
    size = 0
    try:
        size = os.path.getsize(html_path)
    except OSError:
        pass
    digest = _html_digest(html_path, digest_kb) if digest_kb else ''
    return _fmt(
        'ok',
        '规范报告已就绪：%s（%.2f MB，%s，%d 个样本，%d 条命中，%.1fs）'
        % (os.path.basename(html_path) or '(无)', size / 1048576.0,
           model.get('title') or model.get('chain') or '?',
           model.get('n_samples') or 0, len(model.get('hits') or []),
           time.time() - t0),
        result={'target': os.path.basename(root), 'kind': 'chain',
                'chain': model.get('chain'), 'run': model.get('run'),
                'dir': root, 'html': html_path, 'size': size,
                'mode': model.get('mode'), 'n_samples': model.get('n_samples'),
                'n_ok': model.get('n_ok'), 'n_failed': model.get('n_failed'),
                'n_hits': len(model.get('hits') or []),
                'species_n': model.get('species_n'),
                'n_figures': len(model.get('figures') or []),
                'gaps': model.get('gaps') or [],
                'reused': bool(out.get('reused')), 'digest': digest},
        interpret=['章节：一、材料（样本/数据量/环境）；二、方法（步骤与参数）；'
                   '三、结果（检出病毒/计数/深度图）；四、产物与可追溯；'
                   '五、局限与口径',
                   '报告只写 chain_report.html / chain_report_meta.json，'
                   '不动任何分析产物'],
        nxt=['正文纯文本已在 result.digest；要逐节读用 '
             'vp_read_sample_file（工具运行目录）读 chain_report.html',
             '网页入口：/api/tool/chain_report?run=<run>'])


def _build_report_impl(target, kind='auto', force=False, digest_kb=24):
    """生成结论文档 report_v2.html（只写报告文件，不碰分析产物）。

    刻意不 import viz / pipeline_overview：它们在模块层会拉起
    matplotlib，MCP 工作线程首次 import 会在 Windows 字体缓存上挂死
    （见 _stage_disk_state 的注释）。report_kit 只依赖 io_utils /
    stage_funnel 的纯逻辑部分，实测 import 0.06s 且不加载 matplotlib。
    """
    t0 = time.time()
    try:
        root, real_kind = _resolve_report_target(target, kind)
    except ValueError as e:
        return _fmt('error', str(e), nxt=[
            'vp_list_samples 查样品名；vp_list_tool_runs 查工具运行名'])
    # 一键流程目录带 _chain.json：走「材料/方法/结果」规范报告
    if os.path.isfile(os.path.join(root, '_chain.json')):
        return _build_chain_report_impl(root, force=force,
                                        digest_kb=digest_kb)
    try:
        from Virus_Platform_Core.report_kit import build_enhanced_report
    except Exception as e:                      # noqa: BLE001 - 依赖缺失要报清楚
        return _fmt('error', '结论文档生成器不可用: %s: %s'
                             % (type(e).__name__, e),
                    nxt=['python main.py selfcheck 检查核心模块完整性'])
    try:
        out = build_enhanced_report(root, force=bool(force))
    except Exception as e:                      # noqa: BLE001
        return _fmt('error', '生成失败（%s）: %s: %s'
                             % (os.path.basename(root), type(e).__name__, e),
                    result={'target': os.path.basename(root),
                            'kind': real_kind, 'dir': root},
                    nxt=['先用 vp_sample_stages 确认阶段产物齐全；'
                         '日志见 vp_read_sample_file logs/'])
    model = out.get('model') or {}
    html_path = out.get('html') or ''
    size = 0
    try:
        size = os.path.getsize(html_path)
    except OSError:
        pass
    # 模块结果页顺带刷新（纯文件整理，不拉 matplotlib；失败不影响报告本身）
    pages_n = 0
    try:
        from Virus_Platform_Core.report_kit import module_page
        if real_kind == 'tool_run':
            module_page.build_tool_run_page(root)
            pages_n = 1
        else:
            module_page.build_all_for_sample(root)
            pages_n = len(module_page._dir_groups(root))
    except Exception as _mp_e:                  # 页面非关键交付，但原因可见
        logging.getLogger(__name__).warning(
            '结论文档模块结果页刷新失败（不影响报告）: %s: %s',
            type(_mp_e).__name__, _mp_e)
    profile = (model.get('profile') or {})
    stages = model.get('stages') or []
    findings = model.get('findings') or []
    gaps = model.get('gaps') or []
    contra = model.get('contradictions') or []
    verdict = model.get('verdict') or {}
    high = [g for g in gaps if str(g.get('severity')) == 'high']
    digest = _html_digest(html_path, digest_kb) if digest_kb else ''
    interp = [
        '任务档案判为 %s（%s），%d 个阶段进报告'
        % (profile.get('key') or '?', profile.get('title') or '?', len(stages)),
        '结论 %d 条 / 缺口 %d 条（高强度 %d）/ 口径矛盾 %d 条'
        % (len(findings), len(gaps), len(high), len(contra)),
        '判定：%s' % (verdict.get('text') or '未给出'),
        ('报告已复用（输入未变；force=true 可强重建）' if out.get('reused')
         else '报告已重建'),
    ]
    return _fmt(
        'ok',
        '结论文档已就绪：%s（%.2f MB，%s 档案，%d 阶段，%.1fs）'
        % (os.path.basename(html_path) or '(无)', size / 1048576.0,
           profile.get('key') or '?', len(stages), time.time() - t0),
        result={'target': os.path.basename(root), 'kind': real_kind,
                'dir': root, 'html': html_path, 'size': size,
                'profile': profile.get('key'), 'profile_title':
                profile.get('title'), 'n_stages': len(stages),
                'n_findings': len(findings), 'n_gaps': len(gaps),
                'n_gaps_high': len(high), 'n_contradictions': len(contra),
                'verdict': verdict.get('text'), 'reused':
                bool(out.get('reused')), 'digest': digest,
                'module_pages': pages_n},
        interpret=interp,
        nxt=['正文纯文本已在 result.digest（去标签），要结构化字段用 '
             'vp_read_sample_file 07_report/report_v2_meta.json',
             '要详细读某一节：vp_read_sample_file '
             '07_report/report_v2.html（可截断读取）',
             '各模块结果页（results.html）已顺带刷新；网页入口：'
             '/api/module_report?sample=<样品>&stage=<阶段>'])


@mcp.tool(name='vp_build_report', annotations=_WRITE)
async def vp_build_report(
        target: Annotated[str, Field(min_length=1, max_length=200,
                                     description='样品名（vp_list_samples）'
                                                 '或工具运行名'
                                                 '（vp_list_tool_runs）')],
        kind: Annotated[str, Field(
            description='auto|sample|tool_run，默认 auto 先按样品解析再按'
                        '工具运行解析')] = 'auto',
        force: Annotated[bool, Field(
            description='true 时忽略缓存强重建（默认输入未变就复用）')] = False,
        digest_kb: Annotated[int, Field(
            ge=0, le=256,
            description='顺带返回正文纯文本摘要的 KB 上限（0=不要摘要，'
                        '默认 24）')] = 24) -> dict:
    """生成/读取某次分析的「结论文档」report_v2.html（学术论文风，任务无关）。

    只读落盘产物（summary.json / tsv / 日志）→ 生成自包含单文件 HTML：
    执行摘要（结论+证据）、流程漏斗、逐阶段深读、病毒学结论、证据缺口与
    口径矛盾、AI 问答卡、可复现命令、方法与溯源、下一步。**只写
    report_v2.html / report_v2_meta.json 两个文件，不动任何分析产物。**

    传入 digest_kb>0 时同一调用内返回正文纯文本（去标签），可直接读结论，
    不必再取文件。返回 {target, kind, html, size, profile, sections,
    n_stages, n_findings, n_gaps, n_contradictions, verdict, digest}。
    """
    return await anyio.to_thread.run_sync(
        _build_report_impl, target, kind, force, digest_kb)

@mcp.tool(name='vp_delete_tool_run', annotations=_DESTR)
async def vp_delete_tool_run(
        run: Annotated[str, Field(min_length=1, max_length=80,
                                  description='运行名（vp_list_tool_runs 的 '
                                              'run）')],
        confirm: Annotated[bool, Field(
            description='必须显式 true 才执行删除（不可逆，连同全部产物）')]) -> dict:
    """删除一次工具/链式运行目录（含全部产物，不可逆）。

    守卫：confirm 不为 true 拒删；该运行上有任务在跑时拒删（先
    vp_cancel_task）。返回 {deleted, run, out_dir}。
    """
    def _impl():
        if not confirm:
            raise ValueError('删除不可逆：请把 confirm 设为 true 再执行'
                             '（将删除该运行目录下全部产物）')
        if not re.fullmatch(r'[A-Za-z0-9_\-]+', run):
            raise ValueError('无效的运行名: %r' % run)
        p = check_path(run_real_dir(run), must_exist=True, in_platform=True)
        _key = os.path.normcase(os.path.abspath(p))
        with tm.lock:
            _busy = [r for r in tm.tasks.values()
                     if r.get('status') in ('running', 'queued')
                     and r.get('out_dir')
                     and os.path.normcase(os.path.abspath(str(r['out_dir'])))
                     == _key]
        if _busy:
            raise ValueError('该运行目录上有任务正在跑（%s %s）；'
                             '请先 vp_cancel_task 再删除'
                             % (_busy[0].get('id'), _busy[0].get('name')))
        import gc as _gc
        import shutil as _sh
        for _ in range(2):          # Windows 句柄回收重试（同网页端口径）
            _sh.rmtree(p, ignore_errors=True)
            if not os.path.isdir(p):
                break
            _gc.collect()
            time.sleep(0.3)
        if os.path.isdir(p):
            raise RuntimeError('删除失败（产物文件被占用）：%s' % p)
        base = os.path.join(tool_runs_root(), run)
        if os.path.abspath(base) != os.path.abspath(p):
            _sh.rmtree(base, ignore_errors=True)
        return {'deleted': True, 'run': run, 'out_dir': str(p)}
    return await anyio.to_thread.run_sync(_impl)

@mcp.tool(name='vp_archive_tool_run', annotations=_WRITE)
async def vp_archive_tool_run(
        run: Annotated[str, Field(min_length=1, max_length=80,
                                  description='运行名（vp_list_tool_runs 的 '
                                              'run）')]) -> dict:
    """把一次工具运行目录移入归档区（run/tool_runs/_archive/，可恢复）。

    与网页「历史运行」盒同一语义：只搬目录，不删产物；归档后
    vp_list_tool_runs(include_archived=true) 可见，恢复用
    vp_restore_tool_run。守卫：该目录上有任务在跑时拒绝（先取消）。
    返回 {run, archived_to}。
    """
    return await anyio.to_thread.run_sync(_archive_tool_run_impl, run)

@mcp.tool(name='vp_restore_tool_run', annotations=_WRITE)
async def vp_restore_tool_run(
        run: Annotated[str, Field(min_length=1, max_length=80,
                                  description='归档区里的运行名')]) -> dict:
    """把归档区里的运行恢复回活区（run/tool_runs/<run>）。

    返回 {run, restored_to}；活区已有同名运行时报错（不覆盖）。
    """
    return await anyio.to_thread.run_sync(_restore_tool_run_impl, run)


# ======================================================================
# CLI 能力面（数据库构建 / 公共数据 / NCBI 提交 / LOGAN / ICTV / 维护）：
# 两个通用工具 vp_cli_run（快查，同步）+ vp_cli_submit（长任务进 tm），
# 逐命令参数 schema 与 main.py argparse 同源。规格：
#   (键, 类型, 是否必填, 说明)；键以下划线书写，转 argv 时转 --中划线。
# 类型：str/int/float/bool(store_true)/list(重复旗标)/raw(整段透传)
# ======================================================================

CLI_SENSITIVE = {'ncbi_api', 'deepseek_api', 'ncbi_api_key'}


def _C(desc, mode, args=(), positional=(), weight='heavy', confirm=False,
       timeout=180):
    return {'desc': desc, 'mode': mode, 'args': list(args),
            'positional': list(positional), 'weight': weight,
            'confirm': confirm, 'timeout': timeout}


COMMAND_SPECS = {
    # ---- 数据库构建 ----
    'init-taxonomy': _C('下载/准备 NCBI taxonomy（首次必做，耗时长）', 'task', [
        ('force', 'bool', False, '强制重新下载')]),
    'build-host-db': _C('kunpeng 宿主数据库构建（GB 级 I/O，耗时长）', 'task', [
        ('genome', 'path', True, '宿主基因组 FASTA'),
        ('taxid', 'int', True, '宿主 NCBI TaxID'),
        ('hash_capacity', 'str', False, '哈希容量，默认 256M'),
        ('threads', 'int', False, '线程数（默认自动）'),
        ('out_dir', 'path', False, '输出库目录（默认 host-db/<taxid>_<名>_host_db）'),
        ('clean_mid', 'bool', False, '建库后清理中间文件（约省 3GB）'),
        ('rebuild', 'bool', False, '强制重建')]),
    'build-virus-db': _C('kunpeng 病毒数据库构建', 'task', [
        ('fasta', 'path', True, '病毒参考 FASTA'),
        ('info', 'path', True, 'Accession/Taxid 信息表 TSV'),
        ('hash_capacity', 'str', False, '哈希容量，默认 64M'),
        ('threads', 'int', False, '线程数'),
        ('rebuild', 'bool', False, '强制重建')]),
    # ---- 参考数据采集 ----
    'ncbi-dl': _C('NCBI Entrez 批量下载参考序列（进化树扩充；集合名供 '
                  'vp_pipeline_start 的 ncbi_refs 引用）', 'task', [
        ('name', 'str', True, '集合名'),
        ('db', 'str', False, 'nucleotide|protein，默认 nucleotide'),
        ('max', 'int', False, '最多下载条数，默认 100')],
        positional=[('term', 'Entrez 检索式，如 "Tobamovirus[ORGN] AND '
                             'complete genome[TITL]"')]),
    'ncbi-list': _C('列出已下载的 NCBI 参考集合', 'sync', [], timeout=60),
    'gb-dl': _C('下载 GenBank 集合（同属共线性比较输入）', 'task', [
        ('name', 'str', True, '集合名'),
        ('term', 'str', False, 'Entrez 检索式（与 acc 二选一）'),
        ('acc', 'str', False, 'accession 列表（逗号分隔，支持版本号）'),
        ('max', 'int', False, '最多下载数，默认 50')]),
    'gb-import': _C('导入本机 GenBank 文件为集合（多记录自动拆分）', 'task', [
        ('name', 'str', True, '集合名'),
        ('files', 'path', True, '.gb/.gbk 文件路径（逗号分隔，支持 .gz）')]),
    'gb-list': _C('列出 GenBank 集合', 'sync', [], timeout=60),
    'gb-check': _C('巡检 GenBank 集合（记录数/CDS 数/警告）', 'sync', [
        ('name', 'str', True, '集合名')], timeout=120),
    'host-genome': _C('宿主参考基因组下载（NCBI / gget·Ensembl，供建宿主库）',
                      'task', [
        ('species', 'str', False, '物种拉丁名（与 accession 二选一）'),
        ('accession', 'str', False, '组装 accession（GCF_/GCA_）'),
        ('chromosomes', 'str', False, '染色体过滤（如 X,Y；仅 accession 模式）'),
        ('source', 'str', False, 'ncbi|gget，默认 ncbi'),
        ('out', 'path', False, '输出目录'),
        ('release', 'int', False, 'gget: Ensembl release'),
        ('taxid', 'int', False, '指定 TaxID（省去在线查询）'),
        ('include_organelles', 'bool', False, '含叶绿体/线粒体（仅 NCBI 源）'),
        ('ncbi_api', 'str', False, 'NCBI API Key（日志中打码）'),
        ('min_length', 'int', False, '最短序列过滤'),
        ('verify_only', 'bool', False, '仅检查已有下载'),
        ('skip_datasets', 'bool', False, '跳过 datasets CLI 直接 E-utilities')]),
    'ref-status': _C('查看病毒参考库版本与统计', 'sync', [], timeout=60),
    'ictv-update': _C('更新 ICTV VMR 参考库（在线下载或本地 xlsx）', 'task', [
        ('xlsx', 'path', False, '本地 VMR xlsx（缺省在线下载当前版）')]),
    'ictv-status': _C('查看 ICTV VMR 参考库状态', 'sync', [], timeout=60),
    'ictv-refs': _C('ICTV VMR 选参：按属/科/种挑参考（download=true 时'
                    '联网按需下载，耗时更长）', 'sync', [
        ('genus', 'str', False, '属名（如 Tobamovirus）'),
        ('family', 'str', False, '科名（如 Potyviridae）'),
        ('db', 'str', False, 'plant|ictv，默认 plant'),
        ('species', 'str', False, '种名'),
        ('limit', 'int', False, '最多挑多少条，默认 20'),
        ('any', 'bool', False, '不限 Complete genome'),
        ('download', 'bool', False, '本地没有的 accession 联网下载')], timeout=300),
    'datasets': _C('NCBI datasets CLI 下载（genome|gene|taxonomy|virus，'
                   '参数整段透传）', 'task', [], positional=[
        ('raw', '透传参数串，如 "genome --accession GCF_000001405.40 '
                '--chromosomes X,Y"')]),
    'datasets-setup': _C('安装/更新 NCBI datasets CLI（官方 exe）', 'task', []),
    # ---- 公共数据检索 ----
    'meta-search': _C('公共数据检索：SRA+GSA 双引擎按物种检索 Run', 'task', [
        ('species', 'str', True, '物种拉丁名（如 "Lycium barbarum"）'),
        ('source', 'str', False, 'TRANSCRIPTOMIC|GENOMIC|All，默认前者'),
        ('db', 'str', False, 'sra|gsa|both，默认 both'),
        ('out', 'path', False, '输出目录（默认 meta_search/<物种>/search）'),
        ('no_detailed', 'bool', False, '关闭详细模式（Tissue/Location 深提取）'),
        ('ncbi_api', 'str', False, 'NCBI API Key（日志中打码）'),
        ('deepseek_api', 'str', False, 'DeepSeek API Key（AI 元数据清洗；日志打码）')]),
    'meta-info': _C('Run 列表 → Core14/Full 统一元数据', 'task', [
        ('runs', 'str', True, 'Run 编号列表文件或单个编号（SRR/CRR…）'),
        ('out', 'path', False, '输出目录'),
        ('mode', 'str', False, 'local|api|both，默认 both'),
        ('threads', 'int', False, '默认 4'),
        ('fill_date', 'bool', False, '采集日期缺失用发布日期兜底'),
        ('ncbi_api', 'str', False, 'NCBI API Key（打码）'),
        ('deepseek_api', 'str', False, 'DeepSeek API Key（打码）')], weight='light'),
    'meta-plot': _C('元数据 SCI 可视化（时间/机构/组织/地理）', 'task', [
        ('input', 'path', True, '检索结果或 Core14/Full 元数据 CSV'),
        ('out', 'path', False, '图表输出目录，默认 SCI_Figures_Output')],
        weight='light'),
    # ---- NCBI 提交 ----
    'submit-list': _C('提交准备：列出提交项目', 'sync', [], timeout=60),
    'submit-init': _C('提交准备：新建提交项目（unified_metadata.csv）', 'sync', [
        ('name', 'str', True, '项目名（字母/数字/_/-）'),
        ('taxonomy', 'path', False, 'taxonomy.tsv → 自动生成初表'),
        ('metadata', 'path', False, '公共元数据表（Core14/Full）'),
        ('import_csv', 'path', False, '从已有 unified CSV/TSV/Excel 导入'),
        ('demo', 'bool', False, '用示例数据建表'),
        ('authors', 'str', False, '作者 "Last, First; ..."'),
        ('title', 'str', False, '提交标题'),
        ('bioproject', 'str', False, 'BioProject ID'),
        ('host', 'str', False, '宿主物种名'),
        ('lat_lon', 'str', False, '经纬度 "38.47 N 106.27 E"'),
        ('sequencer', 'str', False, '测序平台'),
        ('assembler', 'str', False, '组装方法串'),
        ('coverage', 'str', False, '覆盖度（如 42.5x）')], timeout=120),
    'submit-validate': _C('提交准备：必填字段校验（存在未解决问题时退出码 1）',
                          'sync', [
        ('name', 'str', True, '项目名')], timeout=120),
    'submit-fill': _C('提交准备：批量填充某列占位符/替换旧值', 'sync', [
        ('name', 'str', True, '项目名'),
        ('column', 'str', True, '列名'),
        ('value', 'str', True, '新值'),
        ('old', 'str', False, '旧值（缺省=填充所有占位符）')], timeout=120),
    'submit-export': _C('提交准备：生成 source.src/miuvig/assembly/BioSample/'
                        'report', 'task', [
        ('name', 'str', True, '项目名'),
        ('assembler', 'str', False, '默认 SPAdes;4.3.0;rnaviral'),
        ('sequencer', 'str', False, '默认 Illumina NovaSeq 6000'),
        ('enrichment', 'str', False, '默认 rRNA depletion')], weight='light'),
    'submit-adopt': _C('从 MMPV-RNA discovery 产物一键建提交项目', 'task', [
        ('name', 'str', True, '提交项目名'),
        ('out_dir', 'path', False, 'discovery 输出根'),
        ('from_server', 'str', False, '从服务器拉取（数据集名，需免密 ssh）'),
        ('server_host', 'str', False, 'ssh 目标'),
        ('server_data_root', 'str', False, '服务器数据根'),
        ('dataset', 'str', False, '数据集名（Isolate 前缀）'),
        ('samples', 'path', False, '样本元数据表（需 match 列）'),
        ('run_name', 'str', False, '平台分类运行名'),
        ('force', 'bool', False, '项目已存在时清空重建'),
        ('dry_run', 'bool', False, '只报会生成什么'),
        ('no_export', 'bool', False, '只出 unified_metadata.csv'),
        ('min_length', 'int', False, '过滤短序列'),
        ('isolate_prefix', 'str', False, 'Isolate 前缀'),
        ('public_metadata', 'path', False, '公共元数据表'),
        ('geo_loc', 'str', False, '采集地点 "China:Ningxia"'),
        ('lat_lon', 'str', False, '经纬度'),
        ('collection_date', 'str', False, '采集日期 YYYY[-MM[-DD]]'),
        ('host', 'str', False, '宿主物种名'),
        ('tissue', 'str', False, '组织'), ('cultivar', 'str', False, '品种'),
        ('dev_stage', 'str', False, '发育阶段'),
        ('collected_by', 'str', False, '采集人'),
        ('isolation_source', 'str', False, '分离来源'),
        ('authors', 'str', False, '作者'), ('title', 'str', False, '标题'),
        ('bioproject', 'str', False, 'BioProject'),
        ('biosample_prefix', 'str', False, 'BioSample 前缀'),
        ('assembler', 'str', False, '组装方法'), ('sequencer', 'str', False, '平台'),
        ('coverage', 'str', False, '覆盖度'),
        ('annotation_pipeline', 'str', False, '注释流程串'),
        ('enrichment', 'str', False, '富集方式')]),
    'submit-sbt': _C('提交准备：生成 template.sbt（作者/机构）', 'sync', [
        ('name', 'str', True, '项目名'), ('last', 'str', True, '姓'),
        ('first', 'str', True, '名'), ('middle', 'str', False, '中间名'),
        ('affil', 'str', True, '机构'), ('div', 'str', False, '院系'),
        ('city', 'str', True, '城市'), ('sub', 'str', False, '省/州'),
        ('country', 'str', True, '国家'), ('street', 'str', False, '街道'),
        ('email', 'str', True, '邮箱'), ('postal', 'str', False, '邮编'),
        ('title', 'str', False, '提交标题'),
        ('extra_authors', 'str', False, '其他作者（\\n 分隔 "Last, First"）')],
        timeout=60),
    # ---- LOGAN 溯源 ----
    'logan-create': _C('LOGAN 溯源：生成待提交 Logan-Search 的查询片段', 'task', [
        ('name', 'str', True, '查询名称'),
        ('sample', 'str', False, '来源样品（需已有 ③ 组装 contigs）'),
        ('contigs', 'str', False, 'contig 名，逗号分隔（默认全部）'),
        ('fasta', 'path', False, '或直接给 FASTA 文件'),
        ('segments', 'int', False, '每条 contig 切片段数 1-4，默认 2')]),
    'logan-import': _C('LOGAN 溯源：导入结果表并生成报告', 'task', [
        ('name', 'str', True, '查询名称'),
        ('segment', 'int', False, '片段编号，从 1 起'),
        ('result', 'path', True, '结果表 CSV/TSV')], weight='light'),
    'logan-jobs': _C('LOGAN 溯源：列出查询任务', 'sync', [], timeout=60),
    'logan-batch': _C('LOGAN 溯源：Selenium 批量提交并自动收结果（耗时长）',
                      'task', [
        ('name', 'str', True, '查询名称'),
        ('email', 'str', True, '通知邮箱（逗号分隔轮换）'),
        ('group', 'str', False, 'Fast_No_human 等 9 档，默认 Fast_No_human'),
        ('show_browser', 'bool', False, '显示浏览器（默认 headless）'),
        ('first_wait', 'int', False, '默认 300'),
        ('max_wait', 'int', False, '默认 1800')]),
    # ---- 专项重跑（对已有样品结果，免流水线）----
    'report': _C('对已有样品结果重新生成报告：图表报告 report.html + 结论文档 '
                 'report_v2.html（学术论文风；只要结论文档用 vp_build_report）',
                 'task', [
        ('sample', 'str', True, '样品名'), ('force', 'bool', False, '强制重跑')],
        weight='light'),
    'host-analysis': _C('对已有 ③ 组装结果做 ICTV 宿主预测', 'task', [
        ('sample', 'str', True, '样品名'),
        ('force', 'bool', False, '强制重跑'),
        ('min_confidence', 'float', False, 'ICTV 置信度门槛 0-1（默认不过滤）')],
        weight='light'),
    'orfa': _C('ORF 功能注释重跑（⑥b，对已有样品）', 'task', [
        ('sample', 'str', True, '样品名'), ('threads', 'int', False, '线程'),
        ('libs', 'str', False, 'pvdb|hmm|cdd|bfvd 组合，逗号分隔'),
        ('profile', 'str', False, 'PVDB 投影档位 strict|balanced|recall（默认 balanced）'),
        ('min_pident', 'float', False, '层1 Identity% 门槛'),
        ('min_qcov', 'float', False, '层1 覆盖% 门槛'),
        ('force', 'bool', False, '强制重跑')], weight='light'),
    # ---- 维护 ----
    'db-migrate': _C('数据库迁移/对接（软件与数据库分离部署；copy/move 落盘'
                     '都会切换平台库根，必须 confirm=true）', 'task', [
        ('to', 'path', True, '目标数据库根目录（平台目录之外）'),
        ('mode', 'str', False, 'copy|move，默认 copy'),
        ('check', 'bool', False, '只校验目标是否已有完整库'),
        ('dry_run', 'bool', False, '只预检（空间/路径）')],
        confirm='apply'),
    'tool-runs': _C('输出目录管理：status/organize/archive/clean（clean 不可逆，'
                    '无 dry_run 时必须 confirm=true）', 'task', [
        ('before', 'str', False, 'archive: 归档该日期 YYYYMMDD 之前的运行'),
        ('older_days', 'int', False, 'archive: 归档早于 N 天的运行'),
        ('active_days', 'int', False, 'clean: 删除活动区早于 N 天的运行'),
        ('archive_days', 'int', False, 'clean: 删除归档区早于 N 天的归档'),
        ('dry_run', 'bool', False, '只预演，不动盘')],
        positional=[('action', 'status|organize|archive|clean')],
        confirm='clean'),
    'samples-backfill': _C('给缺 project.json 的老样品回填清单', 'task', [
        ('sample', 'list', False, '只处理指定样品（可重复）'),
        ('dry_run', 'bool', False, '只报告不落盘')], weight='light'),
    'selfcheck': _C('平台环境自检（模块/工具/数据库/磁盘/依赖）', 'sync', [],
                    timeout=300),
}

def _fmt(status, summary, result=None, interpret=None, nxt=None, **extra):
    """统一输出信封：一句话摘要 + 结构化结果 + 规则化解读 + 下一步指引。

    解读全部由真实字段**规则生成**（不经 LLM，杜绝编造）；extra 原样并入，
    保证既有键不丢。"""
    out = {'status': status, 'summary': summary}
    if result is not None:
        out['result'] = result
    if interpret:
        out['interpretation'] = [x for x in interpret if x]
    if nxt:
        out['next'] = [x for x in nxt if x]
    out.update(extra)
    return out


_ERR_RULES = (
    ('未就绪', '对应库未构建——先在「数据库构建」页构建，或换选其他内置库'),
    ('缺少必填', '补齐必填参数（口径见 vp_list_tools / vp_cli_help）'),
    ('路径不存在', '确认输入路径存在（绝对路径或平台根相对路径均可）'),
    ('未检测到', '外部依赖缺失——到「设置 → 工具探测」检查'),
    ('超时', '加大超时或分批；长任务改用异步提交（vp_cli_submit / vp_run_tool）'),
    ('已存在', '样品重名——换名提交，或按提示续跑剩余阶段'),
    ('限流', '降低并发（jobs/gsa_jobs）后重试'),
)


def _interpret_error(err):
    """失败原因 → 可操作建议（关键词命中规则，命中几条给几条）。"""
    err = str(err or '')
    return [msg for kw, msg in _ERR_RULES if kw in err]


def _cli_spec(command):
    spec = COMMAND_SPECS.get(command)
    if not spec:
        raise ValueError('未知 CLI 命令: %s（可用: %s；analyze/tools/orfa/'
                         'report/host-analysis/tool-runs 的日常路径已由'
                         ' vp_run_tool / vp_pipeline_start / vp_list_tool_runs '
                         '覆盖）' % (command, ', '.join(sorted(COMMAND_SPECS))))
    return spec


def _cli_argv(command, args):
    """args 字典 → main.py argv（含必填校验与未知名拒绝）。"""
    spec = _cli_spec(command)
    a = dict(args or {})
    unknown = set(a) - {k for k, *_ in spec['args']} - \
        {p for p, _ in spec['positional']} - {'confirm'}
    if unknown:
        raise ValueError('未知参数: %s（%s 可用参数见 vp_cli_help）'
                         % (', '.join(sorted(unknown)), command))
    argv = [command]
    for pname, pdesc in spec['positional']:
        v = a.get(pname)
        if v in (None, ''):
            raise ValueError('缺少必填参数 %s（%s）' % (pname, pdesc))
        if command == 'datasets' and pname == 'raw':
            import shlex
            argv += shlex.split(str(v))
        else:
            argv.append(str(v))
    for k, t, req, d in spec['args']:
        v = a.get(k)
        if v is None or v is False or v == '':
            if req:
                raise ValueError('缺少必填参数 %s（%s）' % (k, d))
            continue
        flag = '--' + k.replace('_', '-')
        if t == 'bool':
            argv.append(flag)
        elif t == 'list':
            items = v if isinstance(v, (list, tuple)) else [v]
            for x in items:
                argv += [flag, str(x)]
        else:
            argv += [flag, str(v)]
    return argv


def _cli_display(argv):
    out = []
    redact = False
    for tok in argv:
        if redact:
            out.append('***')
            redact = False
            continue
        out.append(str(tok))
        if tok.replace('-', '').replace('_', '').lower() in \
                {s.lower() for s in CLI_SENSITIVE}:
            redact = True
    return ' '.join(out)


def _cli_env():
    env = dict(os.environ)
    env.setdefault('PYTHONIOENCODING', 'utf-8')
    return env


def _cli_check_confirm(command, spec, args):
    if not spec['confirm']:
        return
    a = args or {}
    if spec['confirm'] == 'apply':        # db-migrate：预检类免确认
        if a.get('check') or a.get('dry_run'):
            return
    elif spec['confirm'] == 'clean':      # tool-runs：仅 clean 落盘要确认
        if str(a.get('action') or '') != 'clean' or a.get('dry_run'):
            return
    if a.get('confirm') is not True:
        raise ValueError('%s 属于有破坏面的操作（%s）：请把 confirm 设为 '
                         'true 再执行' % (command, spec['desc'][:40]))


def _cli_run_impl(command, args, timeout_sec):
    spec = _cli_spec(command)
    if spec['mode'] != 'sync':
        raise ValueError('%s 是长任务命令（%s）：请用 vp_cli_submit 提交，'
                         '再用 vp_get_task 轮询' % (command, spec['desc']))
    _cli_check_confirm(command, spec, args)
    argv = [sys.executable, os.path.join(PLATFORM_ROOT, 'main.py')] + \
        _cli_argv(command, args)
    disp = _cli_display(argv)
    timeout = max(10, min(int(timeout_sec), 3600))
    import subprocess as _sp
    try:
        r = _sp.run(argv, cwd=PLATFORM_ROOT, capture_output=True, text=True,
                    encoding='utf-8', errors='replace', timeout=timeout,
                    stdin=_sp.DEVNULL,      # 不继承 MCP 协议管道（Windows 句柄继承会卡死子进程）
                    env=_cli_env())
        out = (r.stdout or '') + (r.stderr or '')
        rc = r.returncode
        timed_out = False
    except _sp.TimeoutExpired as e:
        out = ((e.stdout or b'').decode('utf-8', 'replace')
               if isinstance(e.stdout, bytes) else (e.stdout or '')) + \
              ((e.stderr or b'').decode('utf-8', 'replace')
               if isinstance(e.stderr, bytes) else (e.stderr or ''))
        rc, timed_out = None, True
    cap = 64 * 1024
    out = out[-cap:]
    ok = (rc == 0) and not timed_out
    return _fmt(
        'ok' if ok else 'failed',
        f"{command} {'完成' if ok else f'退出码 {rc}'}",
        result={'command': command, 'argv_display': disp, 'exit_code': rc,
                'timed_out': timed_out,
                'output': out if not timed_out else
                          out + f'\n[超时] 超过 {timeout}s 被终止；长耗时请'
                                f'改用 vp_cli_submit'},
        interpret=([] if ok else
                   ['非零退出/超时：看 output 尾部错误行；参数口径见 '
                    'vp_cli_help']),
        exit_code=rc, timed_out=timed_out,
        output=out if not timed_out else
               out + f'\n[超时] 超过 {timeout}s 被终止；长耗时请改用 '
                     f'vp_cli_submit')



def _cli_task_job(command, argv, disp):
    def job(log, prog, cancel):
        logger = TaskLogger(callback=log)
        log('$ ' + disp)
        prog('run', 0.3, f'{command} 运行中')
        import threading as _th
        import subprocess as _sp
        from Virus_Platform_Core.utils import (DEFAULT_CMD_TIMEOUT_SEC,
                                               task_register_proc)
        env = _cli_env()
        proc = _sp.Popen(argv, cwd=PLATFORM_ROOT, stdout=_sp.PIPE,
                         stderr=_sp.STDOUT, text=True, encoding='utf-8',
                         errors='replace', bufsize=1, env=env,
                         stdin=_sp.DEVNULL)   # 不继承 MCP 协议管道
        task_register_proc(proc)
        tail = []

        def _kill():
            _sp.run(['taskkill', '/F', '/T', '/PID', str(proc.pid)],
                    capture_output=True)
        try:
            for line in proc.stdout:
                line = line.rstrip('\n')
                if line:
                    log(line)
                    tail.append(line)
                    if len(tail) > 30:
                        tail.pop(0)
                if cancel is not None and cancel.is_set():
                    _kill()
                    proc.wait()
                    raise RuntimeError('用户取消')
        finally:
            rc = proc.wait()
        if rc != 0:
            raise RuntimeError(f'{command} 退出码 {rc}；末几行：\n'
                               + '\n'.join(tail[-8:]))
        prog('done', 1.0, f'{command} 完成')
        logger.close()
        return {'command': command, 'exit_code': 0,
                'output_tail': tail[-10:]}
    return job


def _cli_submit_impl(command, args, confirm):
    spec = _cli_spec(command)
    if spec['mode'] != 'task':
        raise ValueError('%s 是快查命令（%s）：请用 vp_cli_run 直接执行'
                         % (command, spec['desc']))
    a = dict(args or {})
    a['confirm'] = confirm
    _cli_check_confirm(command, spec, a)
    argv = [sys.executable, os.path.join(PLATFORM_ROOT, 'main.py')] + \
        _cli_argv(command, a)
    disp = _cli_display(argv)
    ts = time.strftime('%Y%m%d_%H%M%S')
    tid = tm.start(f'CLI·{command} {ts}', _cli_task_job(command, argv, disp),
                   weight=spec['weight'], out_dir=None)
    return _fmt(
        'submitted', f"CLI {command} 已提交",
        result={'task_id': tid, 'command': command, 'argv_display': disp},
        nxt=[f"vp_get_task(task_id='{tid}') 轮询",
             f"vp_get_task_log(task_id='{tid}', lines=200) 看实时输出"],
        hint='vp_get_task 轮询；长输出在任务日志（vp_get_task_log）')


@mcp.tool(name='vp_cli_help', annotations=_RO)
async def vp_cli_help() -> dict:
    """CLI 能力清单：37 个 main.py 子命令的用途、模式（sync=vp_cli_run /
    task=vp_cli_submit）、逐参数 schema（键/类型/必填/说明）。

    返回 {count, note, commands:{命令: {desc, mode, weight, positional,
    args:[{key,type,required,desc}], confirm}}}。
    """
    def _impl():
        out = {}
        for name in sorted(COMMAND_SPECS):
            s = COMMAND_SPECS[name]
            out[name] = {
                'desc': s['desc'], 'mode': s['mode'], 'weight': s['weight'],
                'positional': [{'key': p, 'desc': d} for p, d in
                               s['positional']],
                'args': [{'key': k, 'type': t, 'required': bool(r), 'desc': d}
                         for k, t, r, d in s['args']],
                'confirm': s['confirm'] or None}
        return {'count': len(out),
                'note': '参数键下划线书写自动转 --中划线；bool 传 true 即'
                        '加旗标；ncbi_api/deepseek_api 等键值在任务日志中'
                        '打码',
                'commands': out}
    return await anyio.to_thread.run_sync(_impl)


@mcp.tool(name='vp_cli_run', annotations=_RO)
async def vp_cli_run(
        command: Annotated[str, Field(min_length=1, max_length=40,
                                      description='vp_cli_help 里的 sync 命令')],
        args: Annotated[Optional[dict], Field(
            description='参数字典（键见 vp_cli_help；下划线自动转 --中划线）')] =
        None,
        timeout_sec: Annotated[int, Field(
            ge=10, le=3600, description='超时秒（默认 180；超时提示改用 '
                                        'vp_cli_submit）')] = 180) -> dict:
    """执行 CLI 快查命令（同步，秒~分钟级）：ncbi-list/gb-list/gb-check/
    ictv-status/ictv-refs/ref-status/submit-list/submit-init/submit-validate/
    submit-fill/submit-sbt/logan-jobs/selfcheck。

    返回 {ok, exit_code, output（尾部 ≤64KB）, argv_display}。
    长任务命令（下载/建库/提交批量）会拒收并提示改用 vp_cli_submit。
    """
    return await anyio.to_thread.run_sync(
        _cli_run_impl, command, args or {}, timeout_sec)


@mcp.tool(name='vp_cli_submit')
async def vp_cli_submit(
        command: Annotated[str, Field(min_length=1, max_length=40,
                                      description='vp_cli_help 里的 task 命令')],
        args: Annotated[Optional[dict], Field(
            description='参数字典（键见 vp_cli_help）')] = None,
        confirm: Annotated[bool, Field(
            description='db-migrate 落盘迁移 / tool-runs clean 等破坏面操作'
                        '必须 true')] = False) -> dict:
    """提交 CLI 长任务（异步）：数据库构建 / ncbi-dl / gb-dl / 公共数据检索
    (meta-search) / host-genome / ICTV 更新 / LOGAN 批量 / submit-export·adopt /
    db-migrate / tool-runs / samples-backfill 等全部耗命令。

    提交即返回 {task_id, command, argv_display, hint}；vp_get_task 轮询，
    实时日志 vp_get_task_log。敏感键（API key）在日志中打码。
    """
    return await anyio.to_thread.run_sync(
        _cli_submit_impl, command, args or {}, confirm)




# ======================================================================
# 病毒样本预筛（prescreen）：与 /api/prescreen/* 同源——提交（tm 流式
# 任务，日志实时落 out_dir/run.log）/ 项目列表 / 结果读取。
# ======================================================================

def _prescreen_submit_impl(a):
    from Virus_Platform_Core.web.prescreen import (_valid_name,
                                                   _meta_table_path,
                                                   _detect_run_col, _out_dir)
    name = str(a.get('name') or '').strip()
    if not _valid_name(name):
        raise ValueError('项目名只能含字母/数字/下划线/连字符')
    source = str(a.get('source') or 'paste').strip().lower()
    if source not in ('paste', 'project', 'file'):
        raise ValueError('无效的输入来源（paste|project|file）')
    out_dir = _out_dir(name)
    os.makedirs(out_dir, exist_ok=True)
    cmd_args = []
    if source == 'paste':
        runs = a.get('runs') or []
        if isinstance(runs, str):
            runs = runs.splitlines()
        runs = [str(x).strip() for x in runs if str(x).strip()]
        if not runs:
            raise ValueError('请至少给一条 Run 编号（runs）')
        if len(runs) > 20000:
            raise ValueError('单次最多 20000 条（更大批量请分批）')
        list_p = check_path(os.path.join(out_dir, 'input.list'),
                            must_exist=False, in_platform=True)
        with open(list_p, 'w', encoding='utf-8') as f:
            f.write('\n'.join(runs) + '\n')
        cmd_args += ['--runs', list_p]
    elif source == 'project':
        src_name = str(a.get('project') or '').strip()
        if not _valid_name(src_name):
            raise ValueError('无效的来源项目名')
        which = str(a.get('table') or '').strip()
        cands = ([which] if which in ('core14', 'search', 'full') else []) + \
            ['core14', 'search', 'full']
        csv_p = run_col = None
        for w in cands:
            mp = _meta_table_path(src_name, w)
            if mp and os.path.isfile(mp):
                col = _detect_run_col(mp)
                if col:
                    csv_p, run_col = mp, col
                    break
        if not csv_p:
            raise ValueError(f'项目 {src_name} 下没有可用的元数据表；'
                             '先跑公共数据检索，或改用 runs 粘贴')
        cmd_args += ['--csv', csv_p, '--run-col', run_col]
    else:
        rel = str(a.get('path') or '').strip()
        if not rel:
            raise ValueError('source=file 时须给平台内列表/CSV 路径 path')
        fp = check_path(rel, must_exist=True, in_platform=True)
        if not os.path.isfile(fp):
            raise ValueError('指定路径不是文件')
        ext = os.path.splitext(fp)[1].lower()
        if ext in ('.csv', '.tsv'):
            col = _detect_run_col(fp) or 'Run'
            cmd_args += ['--csv', fp, '--run-col', col]
        else:
            cmd_args += ['--runs', fp]

    vp = a.get('virus_pct')
    if vp not in (None, ''):
        try:
            vp = float(vp)
        except (TypeError, ValueError):
            raise ValueError('virus_pct 须为 0–100 的数字')
        if not (0 <= vp <= 100):
            raise ValueError('病毒占比阈值应在 0–100 之间')
        cmd_args += ['--virus-pct', str(vp)]

    def _int(k, default, lo, hi, label):
        try:
            v = int(a.get(k) or default)
        except (TypeError, ValueError):
            raise ValueError(f'{label} 须为 {lo}–{hi} 的整数')
        if not (lo <= v <= hi):
            raise ValueError(f'{label} 应在 {lo}–{hi} 之间（过高会被限流）')
        return v

    cmd_args += ['-j', str(_int('jobs', 6, 1, 20, 'NCBI 并发')),
                 '--gsa-jobs', str(_int('gsa_jobs', 3, 1, 6, 'GSA 并发'))]
    if a.get('no_taxdump'):
        cmd_args.append('--no-taxdump')
    if a.get('no_gsa'):
        cmd_args.append('--no-gsa')
    if a.get('recheck_nodata'):
        cmd_args.append('--recheck-nodata')

    from Virus_Platform_Core.config import engine_cmd
    from Virus_Platform_Core.public_meta import prescreen_engine
    cmd = engine_cmd(prescreen_engine.__file__, '-o', out_dir, *cmd_args)
    disp = ' '.join(str(x) for x in cmd)
    ts = time.strftime('%Y%m%d_%H%M%S')
    tid = tm.start(f'病毒预筛 {name} (MCP {ts})',
                   _cli_task_job('prescreen', cmd, disp),
                   log_file=os.path.join(out_dir, 'run.log'),
                   weight='light', out_dir=str(out_dir), link='/prescreen')
    return _fmt(
        'submitted', f"预筛项目 {name} 已提交",
        result={'task_id': tid, 'name': name, 'out_dir': str(out_dir)},
        nxt=[f"vp_get_task(task_id='{tid}') 轮询",
             f"完成后 vp_prescreen_result(name='{name}') 读 PASS 统计"],
        hint='vp_get_task 轮询；完成后 vp_prescreen_result 读结果')


def _prescreen_projects_impl():
    from Virus_Platform_Core.web.prescreen import _proj_dir
    root = DIRS['meta_search']
    try:
        names = sorted(os.listdir(root))
    except OSError:
        names = []
    out = []
    for n in names:
        if n.startswith('.') or n.startswith('_'):
            continue
        out.append({'project': n,
                    'prescreen_done': os.path.isfile(os.path.join(
                        _proj_dir(n), 'prescreen', 'stat_run_summary.tsv'))})
    return {'count': len(out), 'projects': out}


def _prescreen_result_impl(name):
    from Virus_Platform_Core.web.prescreen import (_valid_name, _out_dir,
                                                   _read_tsv, _coerce,
                                                   _stats, ARTIFACTS)
    if not _valid_name(name):
        raise ValueError('无效的项目名')
    out_dir = _out_dir(name)
    sum_p = os.path.join(out_dir, 'stat_run_summary.tsv')
    if not os.path.isfile(sum_p):
        return {'exists': False, 'name': name,
                'hint': '尚无结果；先用 vp_prescreen_run 提交'}
    summary = [_coerce(r) for r in _read_tsv(sum_p)]
    passed = [_coerce(r) for r in
              _read_tsv(os.path.join(out_dir, 'prescreen_pass.tsv'))]
    unresolved = [_coerce(r) for r in
                  _read_tsv(os.path.join(out_dir, 'unresolved.tsv'))]
    files = [{'name': fn, 'size': os.path.getsize(os.path.join(out_dir, fn))}
             for fn in ARTIFACTS
             if os.path.isfile(os.path.join(out_dir, fn))]
    st = _stats(summary)
    interp = [f"共 {st['total']} 条 Run：成功分析 {st['ok']}，"
              f"PASS {st['pass']}，检出病毒 {st['with_virus']}，"
              f"未解析 {st['unresolved']}（NCBI {st['ncbi_ok']}/{st['ncbi_total']}"
              f" · GSA {st['gsa_ok']}/{st['gsa_total']}）。"]
    if st['pass']:
        interp.append(f"PASS 清单 {st['pass']} 条已就绪，可作下载/流水线输入。")
    if st['unresolved']:
        interp.append('未解析多为源站无数据或超时，可用 recheck_nodata 复查。')
    return _fmt(
        'ok', f"预筛 {name}：PASS {st['pass']}/{st['total']}",
        result={'exists': True, 'name': name, 'out_dir': out_dir,
                'stats': st,
                'summary_rows': len(summary), 'summary_head': summary[:50],
                'passed_rows': len(passed), 'passed_head': passed[:50],
                'unresolved_rows': len(unresolved),
                'unresolved_head': unresolved[:30],
                'files': files},
        interpret=interp,
        nxt=(["PASS 明细按 passed_head / files 里的 prescreen_pass.tsv 取用"]
             if st['pass'] else []),
        hint='全量 TSV 按 abs 路径直接读取（prescreen 不在 tool_runs）')


@mcp.tool(name='vp_prescreen_run')
async def vp_prescreen_run(
        name: Annotated[str, Field(min_length=1, max_length=40,
                                   description='预筛项目名（字母/数字/_/-）')],
        source: Annotated[str, Field(
            max_length=10, description='paste|project|file，默认 paste')] =
        'paste',
        runs: Annotated[Optional[list[str]], Field(
            description='source=paste：Run 编号列表（SRR/CRR…，≤20000 条）')] =
        None,
        project: Annotated[Optional[str], Field(
            max_length=80,
            description='source=project：公共数据检索项目名')] = None,
        table: Annotated[Optional[str], Field(
            max_length=10,
            description='project 模式的元数据表 core14|search|full')] = None,
        path: Annotated[Optional[str], Field(
            max_length=1000,
            description='source=file：平台内 Run 列表或 CSV 路径')] = None,
        virus_pct: Annotated[Optional[float], Field(
            ge=0, le=100, description='病毒占比阈值 %（默认不过滤）')] = None,
        jobs: Annotated[int, Field(ge=1, le=20,
                                   description='NCBI 并发，默认 6')] = 6,
        gsa_jobs: Annotated[int, Field(ge=1, le=6,
                                       description='GSA 并发，默认 3')] = 3,
        no_taxdump: Annotated[bool, Field(description='跳过 taxdump 下载')] = False,
        no_gsa: Annotated[bool, Field(description='跳过 GSA（只查 NCBI）')] = False,
        recheck_nodata: Annotated[bool, Field(description='重查历史无数据记录')] = False,
) -> dict:
    """提交病毒样本预筛（SRA+GSA 在线核查病毒占比 → PASS 清单 + 汇总报告）。

    三种输入二选一：runs（粘贴 Run 列表）/ project（复用公共数据检索的
    元数据表）/ path（平台内列表或 CSV）。提交即返回 {task_id, name,
    out_dir}；vp_get_task 轮询、完成后 vp_prescreen_result 读结果。
    """
    a = {k: v for k, v in dict(
        name=name, source=source, runs=runs, project=project, table=table,
        path=path, virus_pct=virus_pct, jobs=jobs, gsa_jobs=gsa_jobs,
        no_taxdump=no_taxdump, no_gsa=no_gsa,
        recheck_nodata=recheck_nodata).items() if v is not None}
    return await anyio.to_thread.run_sync(_prescreen_submit_impl, a)


@mcp.tool(name='vp_prescreen_projects', annotations=_RO)
async def vp_prescreen_projects() -> dict:
    """列出可复用的公共数据检索项目（含预筛完成标记）。

    返回 {count, projects:[{project, prescreen_done}]}；project 名可作
    vp_prescreen_run(source='project') 的输入。
    """
    return await anyio.to_thread.run_sync(_prescreen_projects_impl)


@mcp.tool(name='vp_prescreen_result', annotations=_RO)
async def vp_prescreen_result(
        name: Annotated[str, Field(min_length=1, max_length=40,
                                   description='预筛项目名')]) -> dict:
    """读预筛结果：PASS/未解析统计、汇总与清单头部（各 ≤50 行）、产物
    文件清单与磁盘路径。

    返回 {exists, stats:{total,ok,with_virus,pass,unresolved,ncbi_ok,gsa_ok},
    summary_head, passed_head, files, ...}；不存在时 exists=false。
    """
    return await anyio.to_thread.run_sync(_prescreen_result_impl, name)



# ======================================================================
# OpenVirome（Serratus 云端病毒组数据）：公开 API 直连，无需凭据。
# 价值：按病毒物种/组织/地理等过滤，直接查出 **palmprint 阳性** 的
# SRA Run（Serratus 已在云端跑过 RdRp palmprint 的阳性记录）——比按
# 物种元数据检索精准一个量级。查到的 Run 列表可喂平台下载/预筛/流水线。
# 参考：github.com/serratus-bio/open-virome-mcp（我们只取其公开 API 层；
# 其 psql/Neo4j/Azure 工具需私有凭据，不纳入）。
# ======================================================================

_OV_API = 'https://zrdbegawce.execute-api.us-east-1.amazonaws.com/prod'
_OV_ROUTES = ('/identifiers', '/counts', '/results', '/mwas')
_OV_FILTER_HINT = ('organism, tissue, disease, sex, geo_attribute, biome, '
                   'virus_family, stat_organism')


def _ov_post(route, data, timeout=120):
    import httpx
    if route not in _OV_ROUTES:
        raise ValueError(f'非法 OpenVirome 路由: {route}')
    headers = {'Content-Type': 'application/json',
               'Accept': 'application/json',
               'User-Agent': 'VirusPlatform-MCP/1.2 (local virome platform; '
                             'github.com/zhangwenda0518/PlantVirusAnalysisPlatform)'}
    r = None
    for attempt in (1, 2):          # 上游网关偶发 5xx，重试一次
        r = httpx.post(_OV_API + route, json=data, timeout=timeout,
                       headers=headers)
        if r.status_code < 500:
            break
        if attempt == 1:
            time.sleep(2)
    if r.status_code >= 500:
        raise RuntimeError(
            'OpenVirome 上游返回 ' + str(r.status_code)
            + '——该过滤维度/取值可能不被 /identifiers 支持'
              '（已验证可用: organism、tissue）；换维度或取值重试')
    try:
        return r.json()
    except ValueError:
        import base64
        return json.loads(base64.b64decode(r.text).decode('utf-8'))


@mcp.tool(name='vp_ov_search_runs')
async def vp_ov_search_runs(
        filter_type: Annotated[str, Field(
            min_length=2, max_length=40,
            description=f'过滤维度，常见: {_OV_FILTER_HINT}')],
        filter_value: Annotated[str, Field(
            min_length=1, max_length=200,
            description='过滤值，如 "Tobacco mosaic virus" / "rhizosphere"')],
        palmprint_only: Annotated[bool, Field(
            description='只返回 palmprint 阳性 Run（推荐，默认开）')] = True,
        max_runs: Annotated[int, Field(
            ge=1, le=2000, description='返回 Run 编号上限，默认 200')] = 200,
        timeout_sec: Annotated[int, Field(
            ge=10, le=600, description='API 超时秒，默认 120')] = 120,
) -> dict:
    """OpenVirome：按元数据过滤查 **palmprint 阳性** 的 SRA Run 列表。

    这是比按物种元数据检索更强的入口——Serratus 已在云端对千万级 SRA
    跑过 RdRp palmprint，命中的 Run 是**确认有 RNA 病毒信号**的样本。
    返回 {total, runs(截断), truncated}；Run 列表可喂网页「数据下载」
    批次、或 meta-info 生成元数据、或逐条进预筛/流水线。
    """
    def _impl():
        resp = _ov_post('/identifiers', {
            'filters': [{'filterType': filter_type,
                         'filterValue': filter_value,
                         'groupByKey': filter_type}],
            'palmprintOnly': bool(palmprint_only)}, timeout=timeout_sec)
        run = (resp.get('run') or {}) if isinstance(resp, dict) else {}
        total = run.get('totalCount', 0)
        ids = run.get('single') or []
        cap = max(1, min(int(max_runs), 2000))
        return _fmt(
            'ok', f"OpenVirome 检出 palmprint 阳性 Run {total} 条"
                  f"（维度 {filter_type}={filter_value}）",
            result={'total': total, 'returned': min(len(ids), cap),
                    'runs': ids[:cap],
                    'truncated': len(ids) > cap,
                    'filter': f'{filter_type}={filter_value}',
                    'palmprint_only': bool(palmprint_only)},
            interpret=[f"这些 Run 在 Serratus 云端已确认含 RNA 病毒信号"
                       f"（palmprint 阳性={'是' if palmprint_only else '否'}）。"]
                   if total else ['该维度下没有命中的 Run——换过滤值或关闭 '
                                  'palmprint_only 试试。'],
            nxt=['把 runs 列表粘到网页「数据下载」建批次下载',
                 '或逐条 vp_run_tool(tool="kvsuite") 前先跑预筛确认',
                 f"用 vp_ov_counts 按 organism/tissue 等维度看这批 Run 的"
                 f"元数据分布"])
    return await anyio.to_thread.run_sync(_impl)


@mcp.tool(name='vp_ov_counts')
async def vp_ov_counts(
        run_ids: Annotated[list[str], Field(
            min_length=1, max_length=5000,
            description='SRA Run 编号列表（如 vp_ov_search_runs 的结果）')],
        group_by: Annotated[str, Field(
            min_length=2, max_length=40,
            description=f'分组维度，常见: {_OV_FILTER_HINT} | sra')],
        max_rows: Annotated[int, Field(
            ge=1, le=500, description='返回分组行数上限，默认 50')] = 50,
        timeout_sec: Annotated[int, Field(ge=10, le=600)] = 120,
) -> dict:
    """OpenVirome：对一批 Run 做元数据分组统计（宿主物种/组织/疾病/
    地理/生态位/病毒科…的分布）。

    返回 {rows:[{key,count}...]}；用于研判这批样本的价值与多样性。
    """
    def _impl():
        resp = _ov_post('/counts', {
            'table': group_by if group_by != 'sra' else 'sra',
            'groupBy': group_by, 'idColumn': 'run',
            'ids': [str(x).strip() for x in run_ids if str(x).strip()],
            'sortByColumn': 'count', 'sortByDirection': 'desc',
            'palmprintOnly': False,
            'pageStart': 0, 'pageEnd': 1000}, timeout=timeout_sec)
        rows = resp if isinstance(resp, list) else \
            (resp.get('results') or resp.get('data') or [])
        clean = []
        for r in rows[:max_rows]:
            if isinstance(r, dict):
                k = r.get('key') or r.get(group_by) or r.get('name') or ''
                v = r.get('count', '')
                clean.append({'key': k, 'count': v})
        return _fmt(
            'ok', f"元数据分组（{group_by}）共 {len(rows)} 组，返回前 "
                  f"{len(clean)} 组",
            result={'group_by': group_by, 'rows': clean,
                    'rows_total': len(rows)},
            nxt=['结合分布挑高价值 Run：vp_ov_search_runs 精筛，'
                 '或直接进预筛/流水线'])
    return await anyio.to_thread.run_sync(_impl)


@mcp.tool(name='vp_ov_mwas')
async def vp_ov_mwas(
        run_ids: Annotated[list[str], Field(
            min_length=1, max_length=5000,
            description='SRA Run 编号列表')],
        virus_families: Annotated[Optional[list[str]], Field(
            description='按病毒科过滤（如 ["Virgaviridae"]）；缺省=全部')] = None,
        max_rows: Annotated[int, Field(ge=1, le=500, description='默认 50')] = 50,
        timeout_sec: Annotated[int, Field(ge=10, le=600)] = 120,
) -> dict:
    """OpenVirome：对一批 Run 做 MWAS（元数据 × 病毒丰度关联分析）。

    返回 {rows:[{bioproject,family,metadata_field,metadata_value,fold_change,
    p_value}...]}——哪些宿主/组织/地理因素与病毒丰度显著相关。
    """
    def _impl():
        resp = _ov_post('/mwas', {
            'idColumn': 'run',
            'ids': [str(x).strip() for x in run_ids if str(x).strip()],
            'virusFamilies': virus_families,
            'pageStart': 0, 'pageEnd': 1000}, timeout=timeout_sec)
        rows = resp if isinstance(resp, list) else \
            (resp.get('results') or resp.get('data') or [])
        return _fmt(
            'ok', f"MWAS 关联分析返回 {len(rows)} 行",
            result={'rows_total': len(rows), 'rows_head': rows[:max_rows]},
            interpret=(['行按 fold_change/p_value 研判：|fold_change| 越大、'
                        'p_value 越小，关联越强。'] if rows else
                       ['该批 Run 无显著关联结果。']))
    return await anyio.to_thread.run_sync(_impl)


# ======================================================================
# 启动 gate：deferred.json / VP_MCP_DISABLED_TOOLS 摘除工具（fail-closed）
# ======================================================================
# ======================================================================
# 启动 gate：deferred.json / VP_MCP_DISABLED_TOOLS 摘除工具（fail-closed）
# ======================================================================

def _apply_gate():
    """从注册表摘除下线工具；返回 (已摘除, 未知名列表)。"""
    known = {t.name for t in mcp._tool_manager.list_tools()}
    wanted = {t for t in _gated_tools() if t.startswith('vp_')}
    unknown = sorted(wanted - known)
    gated = wanted & known
    for t in gated:
        mcp._tool_manager._tools.pop(t, None)
    if gated:
        sys.stderr.write('[virusplatform_mcp] 已下线工具: '
                         + ', '.join(sorted(gated)) + '\n')
    if unknown:
        sys.stderr.write('[virusplatform_mcp] 忽略下线清单里的未知工具名: '
                         + ', '.join(unknown) + '\n')
    return gated, unknown


_GATED, _GATE_UNKNOWN = _apply_gate()


def _enabled_tool_names():
    return sorted(t.name for t in mcp._tool_manager.list_tools())


# ======================================================================
# 入口
# ======================================================================

def _run_http(port, token=None):
    """--http [port] [--token X]：streamable-HTTP 模式（默认 127.0.0.1:8765）。

    参考 awesome-mcp-fastapi 的核心形态：MCP 挂到 HTTP 服务上——
    任务存活与客户端解耦（客户端断开任务照跑、重连可查），
    局域网内多个客户端可共享同一服务端。stdio 模式仍是默认。
    给 --token 时所有请求须带 Authorization: Bearer <token>（401 拦截）。
    """
    try:
        import uvicorn  # noqa: F401
    except ImportError:
        import subprocess
        subprocess.run([sys.executable, '-m', 'pip', 'install', '--quiet',
                        'uvicorn', '-i',
                        'https://pypi.tuna.tsinghua.edu.cn/simple'],
                       creationflags=getattr(
                           subprocess, 'CREATE_NO_WINDOW', 0))
    app_inner = mcp.streamable_http_app()

    if token:
        async def _bearer_shield(scope, receive, send):
            if scope.get('type') == 'http':
                headers = {k.lower(): v for k, v in scope.get('headers') or []}
                got = headers.get(b'authorization', b'').decode('latin-1')
                if got != f'Bearer {token}':
                    await send({'type': 'http.response.start', 'status': 401,
                                'headers': [(b'content-type',
                                             b'text/plain; charset=utf-8')]})
                    await send({'type': 'http.response.body',
                                'body': b'unauthorized'})
                    return
            await app_inner(scope, receive, send)
        app_to_run = _bearer_shield
    else:
        app_to_run = app_inner

    importlib.import_module('uvicorn')
    sys.stderr.write(f'[virusplatform_mcp] streamable-HTTP: '
                     f'http://127.0.0.1:{port}/mcp'
                     f'{"（Bearer 鉴权已开）" if token else ""}\n')
    uvicorn.run(app_to_run, host='127.0.0.1', port=int(port), log_level='warning')


def _selfcheck_report():
    """结构化自检（vp_selfcheck 与 --check 共用同一份判定）。"""
    checks = []

    def add(name, ok, detail=''):
        checks.append({'name': name, 'ok': bool(ok),
                       'detail': str(detail)[:400]})

    add('platform_root', os.path.isdir(PLATFORM_ROOT), PLATFORM_ROOT)
    try:
        import mcp as _m
        add('mcp_sdk', True, getattr(_m, '__version__', 'installed'))
    except Exception as e:                       # noqa: BLE001
        add('mcp_sdk', False, repr(e))
    info = _platform_info_impl()
    add('host_db', info.get('host_db_ready'),
        'databases/host' if info.get('host_db_ready') else '未构建或未挂载')
    add('virus_db', info.get('virus_db_ready'),
        'databases/virus' if info.get('virus_db_ready') else '未构建')
    add('toolboxes', len(TOOL_REGISTRY) >= 30,
        '%d 张工具箱工具卡' % len(TOOL_REGISTRY))
    add('mcp_tools', info.get('tools_count', 0) > 0,
        '%d 个启用（指纹 %s）' % (info.get('tools_count'),
                                  info.get('fingerprint')))
    for p in (_platform_check() or []):
        add('platform_check', False, p)
    try:
        import shutil as _sh
        free_gb = _sh.disk_usage(PLATFORM_ROOT).free / 1024 ** 3
        add('disk_free', free_gb >= 5, '%.1f GB 可用' % free_gb)
    except Exception as e:                       # noqa: BLE001
        add('disk_free', False, repr(e))
    problems = [c['detail'] or c['name'] for c in checks if not c['ok']]
    return {'checks': checks, 'problems': problems,
            'ok': not problems,
            'summary': '自检 %d/%d 项通过' % (
                len(checks) - len(problems), len(checks))}


def _selfcheck():
    """--check：不进服务，打印环境体检结论（stdout）。"""
    print('virusplatform_mcp 自检')
    rep = _selfcheck_report()
    for c in rep['checks']:
        print('  %-14s: %s%s' % (c['name'], 'ok' if c['ok'] else 'FAIL',
                                 ('  ' + c['detail']) if c['detail'] else ''))
    try:
        import uvicorn
        print('  http模式      : 可用（uvicorn', uvicorn.__version__ + '；'
              '启动加 --http [--token X]）')
    except ImportError:
        print('  http模式      : 需先 pip install uvicorn（--http 首跑自动装）')
    if rep['problems']:
        print('  !! 问题:', '; '.join(rep['problems']))
        return 1
    print('  ready         : yes（可直接挂载 stdio 服务）')
    return 0


if __name__ == '__main__':
    _argv = sys.argv[1:]
    if '--check' in _argv:
        sys.exit(_selfcheck())
    if '--http' in _argv:
        _i = _argv.index('--http')
        _port = int(_argv[_i + 1]) if len(_argv) > _i + 1 \
            and _argv[_i + 1].isdigit() else 8765
        _token = _argv[_argv.index('--token') + 1] \
            if '--token' in _argv else None
        _run_http(_port, _token)
    else:
        mcp.run()          # stdio 传输（默认）
