# -*- coding: utf-8 -*-
"""工具注册表：把平台能力暴露成 LLM 可调用的函数（OpenAI tools 协议）。

安全边界（工具是代理唯一的"动手"通道，必须收紧）：
  - 全部工具**只读**：读样品列表 / 读 digest / 读任务状态 / 读结果表 / 读预筛产物；
  - 读表工具各有一个**单一根白名单**，且共用同一份路径校验实现
    （`_safe_read_table_under`）：`read_result_table` → `run/results/<样品>/`，
    `read_tool_run_file` → `run/tool_runs/<任务>/`，
    `read_prescreen_file` → `run/meta_search/<项目>/prescreen/`（**只到
    prescreen/**，不放开整个 meta_search —— 那里还有原始下载件与检索缓存）。
    根与目标都过 check_path（in_platform）+ 规范化前缀比对（防符号链接/`..`
    逃逸）+ 64MB 体积闸，行/列/单元格三重截断，防上下文炸弹与路径穿越；
  - 不提供任何"重跑阶段 / 改配置 / 删数据"的工具——分析动作仍由用户在
    界面上亲手发起（顾问层边界，见包 docstring）。
"""
import json
import os
import re
import threading
import time

# OpenAI tools 协议的 schema 声明（provider 层原样透传）
TOOLS_SPEC = [
    {'type': 'function', 'function': {
        'name': 'list_samples',
        'description': '列出平台全部样品及各阶段完成度（done/total）',
        'parameters': {'type': 'object', 'properties': {}, 'required': []}}},
    {'type': 'function', 'function': {
        'name': 'get_sample_digest',
        'description': '读取指定样品的结果摘要：各阶段状态、检出病毒、组装/注释/'
                       '宿主预测/进化/引物的关键数字与表格头',
        'parameters': {'type': 'object', 'properties': {
            'sample': {'type': 'string',
                       'description': '样品名（results/ 下的目录名）'}},
            'required': ['sample']}}},
    {'type': 'function', 'function': {
        'name': 'get_task_overview',
        'description': '查看任务中心：运行中/最近完成的分析任务及进度',
        'parameters': {'type': 'object', 'properties': {
            'limit': {'type': 'integer', 'description': '最多返回条数，默认 12'}},
            'required': []}}},
    {'type': 'function', 'function': {
        'name': 'get_anchor_overview',
        'description': '样品锚总览：当前正在分析/疑似中断（30 分钟无进展，'
                       '多半是平台重启留下的运行记录）/最近完成的样品清单。'
                       '回答「现在在跑什么」「有没有卡住的」「刚跑完什么」'
                       '时优先用这里——比任务中心多了「疑似中断」状态。',
        'parameters': {'type': 'object', 'properties': {
            'limit': {'type': 'integer', 'description': '最多返回条数，默认 6'}},
            'required': []}}},
    {'type': 'function', 'function': {
        'name': 'read_result_file',
        'description': '读取结果目录里的**任意文本文件**（不只表格）：报告'
                       ' HTML 的源文本、.md/.txt/.log/.json 说明、FASTA 头'
                       ' 预览等。与 read_result_table 互补——表格用后者，'
                       '其余文本用本工具。返回文件片段（默认前 8000 字符）；'
                       'offset=跳过前 N 个字符（翻页用）。',
        'parameters': {'type': 'object', 'properties': {
            'kind': {'type': 'string', 'enum': ['sample', 'run'],
                     'description': 'sample=样品结果目录 / run=工具箱任务目录'},
            'name': {'type': 'string',
                     'description': '样品名（kind=sample）或任务目录名（kind=run）'},
            'relpath': {'type': 'string',
                        'description': '相对目录的文件路径，如 '
                                       '09_report/report_v2_meta.json'},
            'max_chars': {'type': 'integer',
                          'description': '最多返回字符数（默认 8000，上限 24000）'},
            'offset': {'type': 'integer',
                       'description': 'offset=跳过前 N 个字符（翻页用）'}},
            'required': ['kind', 'name', 'relpath']}}},
    {'type': 'function', 'function': {
        'name': 'get_env_brief',
        'description': '平台环境简报（轻量，毫秒级）：线程数、数据库就绪状态'
                       '（taxonomy/宿主/病毒）、磁盘剩余、输出根、样品总数。'
                       '回答「环境怎么样」「库就绪没」「磁盘够不够」「结果'
                       '落在哪」用本工具，不要跑全量 selfcheck。',
        'parameters': {'type': 'object', 'properties': {}, 'required': []}}},
    {'type': 'function', 'function': {
        'name': 'read_result_table',
        'description': '读取样品结果目录下某个 TSV/CSV 表的表头与前若干行'
                       '（只限 results/ 内的文本表格，不适用于序列/BAM）；'
                       'offset=跳过前 N 行（翻页用）。',
        'parameters': {'type': 'object', 'properties': {
            'sample': {'type': 'string', 'description': '样品名'},
            'relpath': {'type': 'string',
                        'description': '相对样品目录的路径，如 '
                                       '02b_kvsuite/identify/all_viruses.summary.tsv'},
            'max_rows': {'type': 'integer',
                         'description': '最多数据行数（默认 12，上限 30）'},
            'offset': {'type': 'integer',
                       'description': 'offset=跳过前 N 行（翻页用）'}},
            'required': ['sample', 'relpath']}}},
    {'type': 'function', 'function': {
        'name': 'read_tool_run_file',
        'description': '读取某次工具箱/扩展分析任务目录下的 TSV/CSV 文本表，'
                       '返回表头与前若干行。任务名从平台上下文的 tool_runs 里'
                       '取（如 align_20261004_042206_4e2f3e）。只限该任务目录'
                       '内的文本表格，不适用于序列/BAM；offset=跳过前 N 行'
                       '（翻页用）。',
        'parameters': {'type': 'object', 'properties': {
            'run': {'type': 'string',
                    'description': '任务目录名（run/tool_runs/ 下的一级目录）'},
            'relpath': {'type': 'string',
                        'description': '相对该任务目录的路径'},
            'max_rows': {'type': 'integer',
                         'description': '最多数据行数（默认 12，上限 30）'},
            'offset': {'type': 'integer',
                       'description': 'offset=跳过前 N 行（翻页用）'}},
            'required': ['run', 'relpath']}}},
    {'type': 'function', 'function': {
        'name': 'read_prescreen_file',
        'description': '读取病毒样本预筛（/prescreen）项目产物目录内的表：'
                       'run/meta_search/<项目>/prescreen/。常用文件：'
                       'stat_run_summary.tsv（权威判定，一行一个 run，'
                       'pass 列 = 判定通过）、stat_viral_taxa.tsv（检出的病毒分类）、'
                       'prescreen_pass.tsv（通过的 run）、unresolved.tsv（未决）。'
                       '项目名从平台上下文的 prescreen 段取。返回表头与前若干行；'
                       'offset=跳过前 N 行（翻页用）。',
        'parameters': {'type': 'object', 'properties': {
            'project': {'type': 'string',
                        'description': '项目目录名（run/meta_search/ 下的一级目录）'},
            'relpath': {'type': 'string',
                        'description': '相对该项目 prescreen/ 目录的路径，如 '
                                       'stat_run_summary.tsv'},
            'max_rows': {'type': 'integer',
                         'description': '最多数据行数（默认 12，上限 30）'},
            'offset': {'type': 'integer',
                       'description': 'offset=跳过前 N 行（翻页用）'}},
            'required': ['project', 'relpath']}}},
    {'type': 'function', 'function': {
        'name': 'wait_task',
        'description': '等待一个后台任务完成（有界阻塞，最长 240 秒），'
                       '完成后返回终态与结果摘要。适合"任务快完成了"的收尾——'
                       '等完直接给结果；可能更久的任务不要等，提交后告知用户'
                       '在后台运行即可。tid 从 get_task_overview 拿。',
        'parameters': {'type': 'object', 'properties': {
            'tid': {'type': 'string', 'description': '任务 id'},
            'timeout_sec': {'type': 'integer',
                            'description': '最长等待秒数（10~240，默认 240）'}},
            'required': ['tid']}}},
    {'type': 'function', 'function': {
        'name': 'propose_action',
        'description': '发起「AI 执行单」：你**不能直接执行**任何动作，'
                       '只能起草待批执行单（用户在对话坞批准后才会运行）。'
                       '动作：rerun_stage=按给定参数重跑样品管道阶段；'
                       'retry_failed=按上一轮原参数重跑未完成阶段；'
                       'download_runs=把公共数据编号（SRR/ERR/DRR/CRR）下载'
                       '到平台；run_tool=运行工具箱任一工具（比对/SDT/RDP/'
                       '引物设计/共识/ORF/系统地理等 ~30 个，params.tool 必填，'
                       '其余键=该工具界面表单的参数名，文件参数传绝对路径）；'
                       'create_sample=从已落盘的 FASTQ 创建样品；'
                       'build_host_db=用已下载的宿主基因组 FASTA 构建宿主基因组'
                       '数据库（params.genome + params.taxid，可选 out_dir/'
                       'threads/rebuild）；'
                       'plan=多步计划（params.steps 数组，按顺序执行，前一步'
                       '任务成功结束才进入下一步，最多 6 步；每步形如 '
                       '{"action":"create_sample","params":{…}} 或 '
                       '{"action":"run_tool","sample":"…","params":{"tool":"assemble",…}}，'
                       '支持的动作同本列表但不含 download_runs）。'
                       '跨模块的链路（建样品→组装→建宿主库）用 plan 一次提出。'
                       '起草≠执行：发出本调用后对用户只能说「已起草待批执行单」，'
                       '不得宣称已开始/已完成/已出结果——完成与否以任务中心'
                       '真实任务终态与落盘产物为准。'
                       '必须在 reason 里写清为什么。',
        'parameters': {'type': 'object', 'properties': {
            'action': {'type': 'string',
                       'enum': ['rerun_stage', 'retry_failed',
                                'download_runs', 'run_tool',
                                'create_sample', 'build_host_db', 'plan']},
            'sample': {'type': 'string',
                       'description': '样品名（download_runs / build_host_db 不需要）'},
            'stages': {'type': 'array', 'items': {'type': 'string'},
                       'description': '要重跑的阶段键（如 assembly/kvsuite/'
                                      'primer），rerun_stage 必填'},
            'params': {'type': 'object',
                       'description': '按动作填写：rerun_stage/retry_failed='
                                      'run_analysis 白名单键；download_runs='
                                      '{"runs": ["SRR…","CRR…"], "name": "…", '
                                      '"convert_sra": true}；run_tool='
                                      '{"tool": "align", …该工具表单参数，'
                                      '文件键推荐平台根相对路径，如 '
                                      'run/results/<样品>/03_assembly/'
                                      'viral_contigs.fasta}；create_sample='
                                      '{"sample": "名", "r1": "FASTQ 路径", '
                                      '"r2": "可选", "project": "可选"}；'
                                      'build_host_db={"genome": "宿主基因组 '
                                      'FASTA 绝对路径", "taxid": 3702, '
                                      '"out_dir": "可选"}; '
                                      'plan={"steps": [{"action": …, "sample": …, '
                                      '"stages": […], "params": {…}}, …]}',
                       },
            'reason': {'type': 'string', 'description': '提案理由（必填）'}},
            'required': ['action', 'reason']}}},
    {'type': 'function', 'function': {
        'name': 'list_platform_tools',
        'description': '列出工具箱全部可运行工具（名字/标题/是否需病毒库）——'
                       '这些都能通过 propose_action(run_tool) 起草执行单运行。'
                       '用户想做比对、SDT、RDP、引物设计、共识、ORF、系统地理、'
                       'dsRNA、miRNA 等操作时，先查这里拿 tool 名',
        'parameters': {'type': 'object', 'properties': {}, 'required': []}}},
    {'type': 'function', 'function': {
        'name': 'search_public_data',
        'description': '在公共测序库检索数据（NCBI SRA + NGDC GSA，双引擎）。'
                       '转录组/RNA-seq、基因组、病毒组等测序策略都能查——'
                       '用户想"找某物种的测序数据/转录组数据"就用本工具，'
                       '**不要**说平台查不了。species 传拉丁名'
                       '（如 Lycium barbarum）。检索是后台任务（数分钟），'
                       '启动后立即返回集合名；完成后平台自动播报结果，'
                       '期间可用 get_meta_results 查询进度。',
        'parameters': {'type': 'object', 'properties': {
            'species': {'type': 'string', 'description': '物种拉丁名'},
            'source': {'type': 'string',
                       'enum': ['TRANSCRIPTOMIC', 'GENOMIC', 'ALL'],
                       'description': '测序策略，默认 TRANSCRIPTOMIC（转录组）'},
            'db': {'type': 'string', 'enum': ['both', 'sra', 'gsa'],
                   'description': '检索库，默认 both（SRA+GSA）'}},
            'required': ['species']}}},
    {'type': 'function', 'function': {
        'name': 'list_meta_collections',
        'description': '列出平台上已有的公共检索集合（名字 + run 总数）。'
                       '平台上下文的 meta_collections 段通常已含此信息；'
                       '本工具用于按需刷新',
        'parameters': {'type': 'object', 'properties': {}, 'required': []}}},
    {'type': 'function', 'function': {
        'name': 'get_meta_results',
        'description': '读取一次公共检索的结果表（SRA+GSA 合并清单：'
                       'run 编号、物种、测序策略、标题等），返回表头与前若干行',
        'parameters': {'type': 'object', 'properties': {
            'collection': {'type': 'string',
                           'description': '检索集合名（search_public_data '
                                          '返回的 collection）'},
            'limit': {'type': 'integer',
                      'description': '最多数据行数（默认12，上限30）'}},
            'required': ['collection'],
        }}},
    {'type': 'function', 'function': {
        'name': 'mcp_tool_catalog',
        'description': '列出 MCP 执行层全部 vp_* 工具（名称/必填参数/键清单）：'
                       '工具箱执行 vp_run_tool、流水线 vp_pipeline_start、'
                       'CLI vp_cli_run·vp_cli_submit（建库/公共数据/NCBI提交/'
                       'LOGAN/ICTV/维护）、预筛 vp_prescreen_run、任务与运行'
                       '查询 vp_list_tasks·vp_list_tool_runs、结果读取 '
                       'vp_read_sample_file·vp_read_run_file·vp_sample_stages、'
                       '自检 vp_selfcheck。填参数前先调本工具查口径',
        'parameters': {'type': 'object', 'properties': {}, 'required': []}}},
    {'type': 'function', 'function': {
        'name': 'mcp_call',
        'description': '执行一个 MCP 层 vp_* 工具。任务类(vp_run_tool/'
                       'vp_pipeline_start/vp_cli_submit/vp_prescreen_run)提交即'
                       '返回 task_id，随后用 mcp_call 调 vp_wait_task 等到终态'
                       '（或 vp_get_task 轮询），vp_read_sample_file / '
                       'vp_read_run_file / vp_sample_stages 读产物；信封里有 '
                       'summary/interpretation/next。归档是手动动作：'
                       'vp_archive_task / vp_archive_tool_run 只收已结束的对象，'
                       '归档后仍可读、可 vp_unarchive_task / vp_restore_tool_run '
                       '恢复。注意：vp_delete_tool_run 被禁用',
        'parameters': {'type': 'object', 'properties': {
            'tool': {'type': 'string', 'description': 'vp_* 工具名'},
            'arguments': {'type': 'object',
                          'description': '该工具的参数字典'}},
            'required': ['tool']}}},
]


def dispatch(name, args):
    """执行一次工具调用，返回（给 LLM 的）字符串结果。"""
    from . import digest
    args = args or {}
    try:
        if name == 'list_samples':
            return json.dumps(digest.samples_brief(), ensure_ascii=False)
        if name == 'get_sample_digest':
            d = digest.sample_digest(str(args.get('sample') or ''))
            if d is None:
                return json.dumps({'error': '样品不存在'}, ensure_ascii=False)
            return json.dumps(d, ensure_ascii=False)
        if name == 'wait_task':
            return _wait_task_tool(args)
        if name == 'get_task_overview':
            try:
                limit = max(1, min(30, int(args.get('limit') or 12)))
            except (TypeError, ValueError):
                limit = 12
            return json.dumps(digest.task_overview(limit), ensure_ascii=False)
        if name == 'get_anchor_overview':
            try:
                limit = max(1, min(20, int(args.get('limit') or 6)))
            except (TypeError, ValueError):
                limit = 6
            from .. import anchor as _anchor
            a = _anchor.active(limit=limit)
            # 精简到对话友好的字段；stale = 30 分钟无进展（多半是重启遗留）
            slim = [{'sample': s.get('sample'), 'status': s.get('status'),
                     'stage': s.get('stage') or s.get('stage_done') or '',
                     'stages_done': s.get('stages_done'),
                     'stages_total': s.get('stages_total'),
                     'started': s.get('started'),
                     'finished': s.get('finished') or ''}
                    for s in (a.get('running') or [])]
            rec = [{'sample': s.get('sample'), 'status': s.get('status'),
                    'finished': s.get('finished') or '',
                    'dur_s': s.get('dur_s')}
                   for s in (a.get('recent') or [])]
            return json.dumps(
                {'running_or_stale': slim, 'recent': rec,
                 'hint': 'stale=长时间无进展（多半是平台重启遗留，重启平台即收尾）'},
                ensure_ascii=False)
        if name == 'read_result_table':
            return _read_result_table(args)
        if name == 'read_result_file':
            return _read_result_file(args)
        if name == 'get_env_brief':
            return _env_brief()
        if name == 'read_tool_run_file':
            return _read_tool_run_file(args)
        if name == 'read_prescreen_file':
            return _read_prescreen_file(args)
        if name == 'propose_action':
            from . import executors
            reason = str(args.get('reason') or '').strip()
            if not reason:
                # 审批卡片上「理由」是用户判断的唯一依据，空着等于让用户
                # 盲批（spec 里 reason 是必填，这里补上执行层校验）。
                return json.dumps(
                    {'error': '需要 reason：用一两句话写清「为什么这么做、'
                              '依据是什么」，用户在对话坞凭它决定批不批'},
                    ensure_ascii=False)
            rec, err = executors.create_action(
                str(args.get('action') or ''),
                str(args.get('sample') or ''),
                stages=args.get('stages') or None,
                params=args.get('params') or None,
                reason=reason,
                source='chat')
            if err:
                return json.dumps({'error': err}, ensure_ascii=False)
            # 回显归一化后的动作口径：stages/params 可能被校验改写或拒绝，
            # 不回显会让模型以为「A+B+C」都排上了（实际只跑了 A）。
            echo = {'created': True, 'id': rec['id'], 'status': rec['status'],
                    'action': rec.get('action'),
                    'sample': rec.get('sample') or '',
                    'reason': rec.get('reason') or '',
                    'note': '执行单已创建，等待用户在对话坞批准；批准前不会运行'}
            if rec.get('stages'):
                echo['stages'] = rec['stages']
            if rec.get('params'):
                echo['params'] = rec['params']
            if rec.get('steps'):
                echo['steps'] = [{'action': s.get('action'),
                                  'sample': s.get('sample') or '',
                                  'stages': s.get('stages'),
                                  'params': s.get('params')}
                                 for s in rec['steps']]
            return json.dumps(echo, ensure_ascii=False)
        if name == 'list_platform_tools':
            return _list_platform_tools()
        if name == 'list_meta_collections':
            from . import digest
            return json.dumps(digest.meta_collections_brief(),
                              ensure_ascii=False)
        if name == 'search_public_data':
            return _search_public_data(args)
        if name == 'get_meta_results':
            return _get_meta_results(args)
        if name == 'mcp_tool_catalog':
            return _mcp_catalog()
        if name == 'mcp_call':
            return _mcp_call(args)
        return json.dumps({'error': f'未知工具 {name}'}, ensure_ascii=False)
    except Exception as e:                      # 工具失败应作为结果回给 LLM
        return json.dumps({'error': f'{type(e).__name__}: {e}'},
                          ensure_ascii=False)


# ------------------------------------------------------------------
# MCP 桥（2026-10-16）：让对话坞 LLM 直接驱动 virusplatform_mcp 的
# 19 个 vp_* 工具（执行/查询/读结果全量）。只开两座桥而非注册 19 个
# 重叠工具：mcp_tool_catalog 负责「发现+参数口径」，mcp_call 负责「执行」。
# 破坏面收敛：vp_delete_tool_run 在对话坞禁用（删除走网页任务中心）；
# 任务类工具提交即返回 task_id，LLM 用 mcp_call(vp_get_task) 轮询。
# ------------------------------------------------------------------
_MCP_BLOCKED = {'vp_delete_tool_run'}
# 2026-11-02：只拦 vp_delete_tool_run 不够——mcp_call 还能用 vp_cli_submit/
# vp_cli_run 把 confirm=true 传给 CLI，从而触发 tool-runs clean（删输出目录）
# 与 db-migrate 的 copy/move 落盘迁移。这些是不可逆操作，按 AGENTS.md §5/§7
# 必须先由用户在网页端亲自确认，绝不能让模型一句话跑掉。
_CLI_DESTRUCTIVE = {
    'tool-runs': ('clean', 'archive', 'organize'),
    'db-migrate': ('apply', 'copy', 'move', 'migrate'),
}
_MCP_TEXT_CAP = 4000          # 给 LLM 的结果上限（防撑爆上下文）


def _mcp_denied(name, a):
    """对话坞对 MCP 破坏面调用的拦截文案（None = 放行）。"""
    if name not in ('vp_cli_run', 'vp_cli_submit'):
        return None
    cmd = str(a.get('command') or '').strip()
    sub = a.get('args') if isinstance(a.get('args'), dict) else {}
    action = str(sub.get('action') or a.get('action') or '').strip().lower()
    confirm = (a.get('confirm') is True or sub.get('confirm') is True)
    bad = _CLI_DESTRUCTIVE.get(cmd) or ()
    if action in bad or (cmd in _CLI_DESTRUCTIVE and confirm):
        return (f'{cmd} 的「{action or "落盘"}」属不可逆/高破坏面操作，'
                '对话坞不开放执行。请把这一步交给用户：到「任务中心 → 输出'
                '目录」或「数据库」页面亲自确认；你可以先把要清理/迁移的'
                '清单和理由讲清楚。')
    return None


_TASK_WATCH = set()


def _watch_task(tid, tool_name):
    """MCP 提交的后台任务（预筛/检索/建库…）在结束时播报一次。

    2026-10-30：模型被要求"提交后结束本轮、由平台播报"，但此前只有检索
    （search_public_data）有播报，预筛/CLI 任务没有 —— 承诺落空。这里对
    mcp_call 返回里的 task_id/task 起一个守护线程轮询，done/failed 时发
    ai_read 事件（对话坞里就是一条 AI 播报，带未读红点）。
    """
    if not tid or tid in _TASK_WATCH:
        return
    _TASK_WATCH.add(tid)

    def run():
        from . import broadcast
        from Virus_Platform_Core.web.tasks import tm
        deadline = time.time() + 24 * 3600
        while time.time() < deadline:
            try:
                snap = tm.snapshot(str(tid), log_lines=0,
                                   include_result=False) or {}
            except Exception:
                snap = {}
            st = str(snap.get('status') or '')
            if st in ('done', 'failed', 'cancelled'):
                name = snap.get('name') or tool_name
                zh = ('✔ 后台任务完成：' if st == 'done'
                      else ('✘ 后台任务失败：' if st == 'failed'
                            else '后台任务已取消：')) + str(name)
                en = ('Background task done: ' if st == 'done'
                      else ('Background task failed: ' if st == 'failed'
                            else 'Background task cancelled: ')) + str(name)
                try:
                    broadcast.publish(
                        'ai_read', sample='', stage='task', name=str(name),
                        text=zh + '（用对应 vp_* 工具读取结果，或直接问我下一步）'
                             if _is_zh() else
                             en + ' (read the result with the matching vp_* tool,'
                                  ' or ask me what to do next)',
                        failure=(st == 'failed'))
                except Exception:
                    pass
                return
            time.sleep(3)

    threading.Thread(target=run, daemon=True,
                     name='ai-task-watch-%s' % tid).start()


def _is_zh():
    try:
        from Virus_Platform_Core.config import get_config
        return get_config().lang == 'zh'
    except Exception:
        return True


def _wait_task_tool(args):
    """有界等待一个任务结束（2026-10-30）。

    SSE 生成器里没有字节流动会吃读超时（provider 300s），所以等待上限
    压在 240 秒；更长的任务请提交后结束本轮，平台完成时会播报。
    """
    from Virus_Platform_Core.web.tasks import tm
    tid = str(args.get('tid') or '').strip()
    if not tid:
        return json.dumps({'error': '需要 tid（可从 get_task_overview 拿到）'},
                          ensure_ascii=False)
    try:
        timeout = max(10, min(int(args.get('timeout_sec') or 240), 240))
    except (TypeError, ValueError):
        timeout = 240
    end = time.time() + timeout
    while True:
        try:
            snap = tm.snapshot(tid, log_lines=0, include_result=True) or {}
        except Exception as e:
            return json.dumps({'error': f'{type(e).__name__}: {e}'},
                              ensure_ascii=False)
        st = str(snap.get('status') or '')
        if st in ('done', 'failed', 'cancelled'):
            out = {'tid': tid, 'status': st, 'name': snap.get('name'),
                   'stage': snap.get('stage'), 'pct': snap.get('pct')}
            res = snap.get('result')
            if isinstance(res, str):
                out['result_preview'] = res[:2000]
            elif res is not None:
                out['result'] = res
            return json.dumps(out, ensure_ascii=False)
        if time.time() >= end:
            return json.dumps(
                {'tid': tid, 'status': st or 'running',
                 'hint': '等待超时（上限 240 秒），任务仍在后台运行；'
                         '可再调一次 wait_task 继续等，或让用户去任务中心查看'},
                ensure_ascii=False)
        time.sleep(2)


def _mcp_catalog():
    from Virus_Platform_Core.web.mcp_console import _request
    try:
        res = _request('tools/list', timeout=150)
    except Exception as e:
        return json.dumps({'error': f'MCP 服务器不可用: {e}'},
                          ensure_ascii=False)
    out = [{'name': t.get('name'),
            'description': (t.get('description') or '').split(chr(10))[0][:160],
            'required': (t.get('inputSchema') or {}).get('required') or [],
            'properties': sorted((t.get('inputSchema') or {})
                                 .get('properties') or {})}
           for t in (res.get('tools') or [])]
    return json.dumps({'count': len(out), 'tools': out}, ensure_ascii=False)


def _mcp_call(args):
    from Virus_Platform_Core.web.mcp_console import _request
    name = str(args.get('tool') or '').strip()
    if not name.startswith('vp_'):
        return json.dumps({'error': '只允许调用 vp_* MCP 工具'},
                          ensure_ascii=False)
    if name in _MCP_BLOCKED:
        return json.dumps(
            {'error': name + ' 属破坏面工具，对话坞不开放；'
                         '删除运行请用网页端任务中心'},
            ensure_ascii=False)
    a = args.get('arguments')
    if not isinstance(a, dict):
        a = {}
    deny = _mcp_denied(name, a)
    if deny:
        return json.dumps({'error': deny, 'blocked': True},
                          ensure_ascii=False)
    try:
        res = _request('tools/call', {'name': name, 'arguments': a},
                       timeout=330)
    except Exception as e:
        return json.dumps({'error': f'MCP 调用失败: {e}'}, ensure_ascii=False)
    text = chr(10).join(
        c.get('text', '') for c in (res.get('content') or [])
        if isinstance(c, dict) and c.get('type') == 'text')
    data = res.get('structuredContent')
    if data is None:
        try:
            data = json.loads(text)
        except ValueError:
            data = None
    body = data if data is not None else text
    # 任务类工具返回 {task_id, ...}：挂一个 watcher，完成时播报到对话坞
    if isinstance(body, dict) and not res.get('isError'):
        tid = body.get('task_id') or body.get('task')
        if isinstance(tid, str) and tid.strip():
            _watch_task(tid.strip(), name)
    out = json.dumps({'isError': bool(res.get('isError')), 'result': body},
                     ensure_ascii=False)
    if len(out) > _MCP_TEXT_CAP:
        # 2026-11-02：以前裸切字符串，JSON 从中间断掉——模型收到半截 JSON，
        # task_id / status / summary 全可能丢，容易误判成"没结果"。改为
        # 结构化裁剪：先保住关键字段，再给正文开头的片段，并显式标截断。
        keep = {}
        if isinstance(body, dict):
            for k in ('task_id', 'task', 'status', 'ok', 'isError',
                      'summary', 'interpretation', 'next', 'count', 'total',
                      'out_dir', 'run'):
                if k in body:
                    keep[k] = body[k]
        head_text = text[:1200] if text else ''
        out = json.dumps(
            {'isError': bool(res.get('isError')),
             'truncated': True,
             'full_chars': len(out),
             'result': keep or None,
             'result_text_head': head_text or None,
             'note': '原始结果过长已截断；以上保留了任务号/状态/摘要等关键'
                     '字段。需要细节请改用 vp_read_run_file / vp_get_task '
                     '按文件或按任务读，或收紧查询参数。'},
            ensure_ascii=False)
    return out


def _table_total_rows(path):
    """表的数据行数（不含表头，按换行计数，与 digest._tsv_head 同口径）。

    2026-11-04 分页需要：_tsv_head 只返回 truncated 布尔，无法区分"刚好
    读满"和"后面还有"；这里数一遍总行数，has_more 才算得准。文件已过
    64MB 体积闸，额外一遍扫描可接受。
    """
    n = 0
    try:
        with open(path, encoding='utf-8', errors='replace') as f:
            for _line in f:
                n += 1
    except OSError:
        return 0
    return max(0, n - 1)


def _relpath_is_absolute(relpath):
    """relpath 是否实际是绝对路径（POSIX 或 Windows 盘符/UNC 形式）。

    两个读文件入口都先 `replace('\\\\','/').lstrip('/')`，这会把手写的
    绝对路径"伪装"成相对路径；而 os.path.join(root, 'C:/x') 在 Windows
    会整段替换 root，于是越界检查失效。必须在拼接前显式拒绝。
    """
    s = str(relpath or '').replace('\\', '/').strip()
    return bool(os.path.isabs(s) or re.match(r'^[A-Za-z]:', s)
                or s.startswith('//'))


def _path_within(target, root):
    """target 是否真的在 root 内（realpath + normcase + 分隔符边界）。

    以前用 `normcase(abspath(target)).find(normcase(abspath(root))) == 0`：
    同前缀的兄弟目录可互读（root=`…/results/S1`，target=`…/results/S1evil/x`
    的 find 也是 0），且不解析符号链接。改为 realpath 后要求
    `target == root` 或 `target.startswith(root + os.sep)`。
    """
    try:
        t = os.path.normcase(os.path.realpath(os.path.abspath(target)))
        r = os.path.normcase(os.path.realpath(os.path.abspath(root)))
    except (OSError, ValueError):
        return False
    return t == r or t.startswith(r + os.sep)


def _safe_read_table_under(root, relpath, max_rows, offset=0):
    """在**给定白名单根**下安全读一张文本表，返回给 LLM 的 JSON 串。

    三重防线（两个入口共用同一份实现，避免"只修一处"）：
      1. `check_path(..., in_platform=True)` 两次 —— 根与目标都必须在平台内；
      2. realpath + 规范化后按分隔符边界比对 —— 防符号链接 / `..` 相对段
         / 同前缀兄弟目录逃逸出根；
      3. 体积闸 64MB —— 防上下文炸弹。

    offset（2026-11-04 T3）：跳过前 N 行数据（表头始终返回），用于翻页。
    _tsv_head 本身不支持 offset，这里多读 offset+max_rows 行再切片；
    返回体带 offset / n_total / has_more。
    """
    from Virus_Platform_Core.utils import check_path
    from . import digest
    raw_rel = str(relpath or '')
    if _relpath_is_absolute(raw_rel):
        return json.dumps({'error': 'relpath 不能是绝对路径'},
                          ensure_ascii=False)
    relpath = raw_rel.replace('\\', '/').lstrip('/')
    if not relpath:
        return json.dumps({'error': '需要 relpath'}, ensure_ascii=False)
    try:
        root = check_path(root, must_exist=True, in_platform=True)
        target = check_path(os.path.join(root, relpath),
                            must_exist=True, in_platform=True)
    except (ValueError, OSError) as e:
        return json.dumps({'error': f'路径不合法: {e}'}, ensure_ascii=False)
    if not _path_within(target, root):
        return json.dumps({'error': '路径越界'}, ensure_ascii=False)
    if os.path.getsize(target) > 64 * 1024 * 1024:
        return json.dumps({'error': '文件过大，不予读取'}, ensure_ascii=False)
    try:
        max_rows = max(1, min(30, int(max_rows or 12)))
    except (TypeError, ValueError):
        max_rows = 12
    try:
        offset = max(0, min(1000000, int(offset or 0)))
    except (TypeError, ValueError):
        offset = 0
    t = digest._tsv_head(target, max_rows=offset + max_rows)
    if t is None:
        return json.dumps({'error': '文件不存在或为空'}, ensure_ascii=False)
    total = _table_total_rows(target)
    t['rows'] = (t.get('rows') or [])[offset:offset + max_rows]
    t['offset'] = offset
    t['n_total'] = total
    t['has_more'] = total > offset + max_rows
    # truncated 改成更精确的"还有下一页"（旧值在刚好读满时也会误报 True）
    t['truncated'] = t['has_more']
    return json.dumps(t, ensure_ascii=False)


def _read_result_table(args):
    """读样品结果目录（run/results/<sample>/）内的表。根 = results/<sample>。"""
    from Virus_Platform_Core.config import DIRS
    sample = str(args.get('sample') or '')
    if not sample:
        return json.dumps({'error': '需要 sample 与 relpath'},
                          ensure_ascii=False)
    return _safe_read_table_under(os.path.join(DIRS['results'], sample),
                                  args.get('relpath'), args.get('max_rows'),
                                  offset=args.get('offset'))


def _read_result_file(args):
    """读结果目录里的任意**文本**文件（2026-11-03 AI 工作台扩展）。

    与 _safe_read_table_under 同款三重防线，但放宽内容形态：
    - 白名单根：kind=sample → run/results/<样品>/；kind=run → run/tool_runs/<任务>/
    - 扩展名白名单（纯文本形态）：.txt .md .log .json .tsv .csv .njs .seq
      .fa .fasta .fna .faa .vcf .gb .gbf .bed .sam 头部预览也走这里
      （sam/vcf 只回头部，不给全量）
    - 体积闸 256KB + 返回片段（默认 8000 字符，上限 24000）——上下文
      炸弹防线；超长返回 has_more 标记。
    - offset（2026-11-04 T3）：字符偏移，跳过前 N 个字符再取 cap 个，用于
      翻页。文本模式直接 seek 有编码风险，改为整读后切片（文件本身 ≤256KB）。
    """
    from Virus_Platform_Core.config import DIRS
    from Virus_Platform_Core.utils import check_path
    kind = str(args.get('kind') or 'sample').strip().lower()
    nm = str(args.get('name') or '').strip()
    raw_rel = str(args.get('relpath') or '')
    if _relpath_is_absolute(raw_rel):
        return json.dumps({'error': 'relpath 不能是绝对路径'},
                          ensure_ascii=False)
    relpath = raw_rel.replace('\\', '/').lstrip('/')
    if not nm or not relpath:
        return json.dumps({'error': '需要 kind / name / relpath'},
                          ensure_ascii=False)
    if kind == 'sample':
        root = os.path.join(DIRS['results'], nm)
    elif kind == 'run':
        root = os.path.join(DIRS['tool_runs'], nm)
    else:
        return json.dumps({'error': "kind 只支持 'sample' 或 'run'"},
                          ensure_ascii=False)
    ext = os.path.splitext(relpath)[1].lower()
    if ext not in ('.txt', '.md', '.log', '.json', '.tsv', '.csv', '.njs',
                   '.seq', '.fa', '.fasta', '.fna', '.faa', '.vcf', '.gb',
                   '.gbf', '.bed', '.sam'):
        return json.dumps({'error': f'不支持的文件类型: {ext or "(无扩展名)"}'
                                    '（只允许文本形态；序列/BAM 全量请走文件选择）'},
                          ensure_ascii=False)
    try:
        root = check_path(root, must_exist=True, in_platform=True)
        target = check_path(os.path.join(root, relpath),
                            must_exist=True, in_platform=True)
    except (ValueError, OSError) as e:
        return json.dumps({'error': f'路径不合法: {e}'}, ensure_ascii=False)
    if not _path_within(target, root):
        return json.dumps({'error': '路径越界'}, ensure_ascii=False)
    size = os.path.getsize(target)
    if size > 256 * 1024:
        return json.dumps({'error': f'文件过大（{size // 1024}KB > 256KB 上限），'
                                    '请改读其中的表格（read_result_table）'},
                          ensure_ascii=False)
    try:
        cap = max(500, min(24000, int(args.get('max_chars') or 8000)))
    except (TypeError, ValueError):
        cap = 8000
    try:
        offset = max(0, int(args.get('offset') or 0))
    except (TypeError, ValueError):
        offset = 0
    try:
        with open(target, 'r', encoding='utf-8', errors='replace') as f:
            text = f.read()
    except OSError as e:
        return json.dumps({'error': f'读取失败: {e}'}, ensure_ascii=False)
    total = len(text)
    has_more = total > offset + cap
    # 'head' 沿用旧字段名（模型与前端已按它取值），语义变为"offset 起的片段"
    return json.dumps({'path': relpath, 'size': size,
                       'total_chars': total, 'offset': offset,
                       'truncated': has_more, 'has_more': has_more,
                       'head': text[offset:offset + cap]}, ensure_ascii=False)


def _env_brief():
    """平台环境简报（毫秒级，无子进程）：库就绪 / 磁盘 / 线程 / 输出根。"""
    from Virus_Platform_Core import config as _cfg
    from Virus_Platform_Core.kunpeng import db_ready
    import shutil as _sh
    out = {}
    try:
        out['threads'] = int(_cfg.cfg.threads or 0)
    except Exception:                                   # noqa: BLE001
        out['threads'] = None
    try:
        dbs = _cfg.cfg.databases or {}
        out['virus_db_ready'] = bool(dbs.get('virus')) and db_ready(dbs['virus'])
        out['host_db_path'] = str(dbs.get('host') or '')
        out['host_db_exists'] = bool(dbs.get('host')) and os.path.isdir(
            str(dbs.get('host') or ''))
    except Exception:                                   # noqa: BLE001
        out['virus_db_ready'] = None
        out['host_db_exists'] = None
    try:
        tax = os.path.join(_cfg.DIRS['databases'], 'tax_db', 'nodes.dmp')
        out['taxonomy_ready'] = os.path.isfile(tax)
    except Exception:                                   # noqa: BLE001
        out['taxonomy_ready'] = None
    try:
        u = _sh.disk_usage(_cfg.DIRS['results'])
        out['disk_free_gb'] = round(u.free / 1e9, 1)
    except Exception:                                   # noqa: BLE001
        out['disk_free_gb'] = None
    try:
        out['output_root'] = _cfg.get_output_root() or '(平台目录 run/)'
    except Exception:                                   # noqa: BLE001
        out['output_root'] = ''
    try:
        from Virus_Platform_Core.config import DIRS
        rd = DIRS['results']
        out['samples_n'] = sum(
            1 for x in os.listdir(rd)
            if os.path.isdir(os.path.join(rd, x)) and not x.startswith('_'))
    except Exception:                                   # noqa: BLE001
        out['samples_n'] = None
    return json.dumps(out, ensure_ascii=False)


def _read_tool_run_file(args):
    """读某次工具箱任务目录（run/tool_runs/<run>/）内的表。

    2026-10-08 新增：扩 digest 覆盖面时，主链之外的扩展分析（比对 / 组装 /
    ORF / dsRNA / miRNA / CRISPR…）产物都落在 tool_runs 下，没有这条通道，
    AI 只能知道"跑过"，答不了"结果是什么"。根白名单仍收紧在 tool_runs/。
    """
    from Virus_Platform_Core.config import DIRS
    run = str(args.get('run') or '')
    if not run:
        return json.dumps({'error': '需要 run（任务目录名）'},
                          ensure_ascii=False)
    return _safe_read_table_under(os.path.join(DIRS['tool_runs'], run),
                                  args.get('relpath'), args.get('max_rows'),
                                  offset=args.get('offset'))


# 项目目录名（与 web/prescreen.py 的 _NAME_RE 同口径）：挡掉分隔符与 `..`
_PRESCREEN_NAME_RE = re.compile(r'[A-Za-z0-9_\-]+')


def _read_prescreen_file(args):
    """读病毒预筛项目目录（run/meta_search/<项目>/prescreen/）内的表。

    2026-10-08 新增：预筛产物**不在** run/tool_runs/ 下（`web/prescreen.py`
    只写 `DIRS['meta_search']`），故 `read_tool_run_file` 的白名单覆盖不到 ——
    此前 `prescreen_brief()` 只能给计数（n_runs/n_pass/n_ok），答不了
    「哪些 run 通过、检出哪些病毒」。根白名单**收紧到 prescreen/**：
    不放开整个 meta_search（那里还有原始下载件 `stat_raw/`、`gsa_raw/`
    与检索缓存 `.cache/`）。
    """
    from Virus_Platform_Core.config import DIRS
    project = str(args.get('project') or '').strip()
    if not project or not _PRESCREEN_NAME_RE.fullmatch(project):
        return json.dumps(
            {'error': '需要 project（项目目录名，只允许字母/数字/_/-）'},
            ensure_ascii=False)
    root = os.path.join(DIRS['meta_search'], project, 'prescreen')
    return _safe_read_table_under(root, args.get('relpath'),
                                  args.get('max_rows'),
                                  offset=args.get('offset'))


# ------------------------------------------------------------------
# 公共数据检索（SRA + GSA）——"AI 驱动找数据"的核心工具
# 复用与「公共数据检索」页面完全相同的引擎与任务通道
# （web/meta.py 的 _meta_stream_task + public_meta/search_engine.py），
# 结果落 run/meta_search/<集合>/search/SRA_GSA_Merged_Final.csv。
# ------------------------------------------------------------------
def _meta_collection_name(species):
    """物种名 → 合法集合名（与 web/meta.py 同口径：<slug>_<时间戳>）。"""
    import time as _time
    slug = re.sub(r'[^A-Za-z0-9_\-]', '_', str(species))[:40].strip('_') \
        or 'search'
    ts = _time.strftime('%Y%m%d_%H%M')
    from Virus_Platform_Core.config import DIRS
    name = f'{slug}_{ts}'
    n = 2
    while os.path.isdir(os.path.join(DIRS['meta_search'], name)):
        name = f'{slug}_{ts}_{n}'
        n += 1
    return name


def _tool_param_hints(job):
    """从 job 工厂源码提取 `_req(...)` 必填与 `_opt(...)` 可选（源码正则）。

    注意：这**不是**参数口径的权威来源——同一 job 里互斥分支的必填项会被
    全算成必填（kvsuite 实测会给出 6 个互斥必填），因此只作为「job 实际
    校验了什么」的旁证，权威口径用 agent_catalog.TOOL_PARAM_SCHEMAS。
    """
    import inspect as _inspect
    import re as _re
    try:
        src = _inspect.getsource(job)
    except (TypeError, OSError):
        return {}
    # job 工厂取参有两种形态：ctx.req('key','what')（tools_api 闭包）与
    # _job_req(ctx,'key','what')（tool_jobs 助手）；可选取参同理。
    req = _re.findall(r"ctx\.req\('(\w+)',\s*'([^']+)'", src)
    req += _re.findall(r"_job_req\(ctx,\s*'(\w+)',\s*'([^']+)'", src)
    opt = _re.findall(r"ctx\.opt\('(\w+)'", src)
    opt += _re.findall(r"_job_opt\(ctx,\s*'(\w+)'", src)
    seen, req2 = set(), []
    for k, w in req:
        if k not in seen:
            seen.add(k)
            req2.append((k, w))
    opt = list(dict.fromkeys(opt))
    out = {}
    if req2:
        out['required'] = [{'key': k, 'what': w} for k, w in req2]
    if opt:
        out['optional'] = opt
    return out


def _tool_params(tool, job):
    """工具参数口径：**以 agent_catalog.TOOL_PARAM_SCHEMAS 为准**。

    2026-11-02：以前直接把源码正则结果当参数表给模型，导致
      * kvsuite 报 6 个「必填」（实际全可选、三选一）→ 模型按 hint 传互斥
        参数，起草必然失败；
      * 第 9 个以后的必填项被 `[:8]` 切掉；
      * pdprep 的 catalog 写 fasta_text/meta_text，job 实际要 fasta/meta
        文件路径，两边对不上时模型无从判断。
    这里改为：catalog 条目为主（key/type/required/desc），再用 job 源码
    双向核对——job 要而 catalog 没有的键显式补上（标 source=job），
    catalog 说必填而 job 不校验的保留但降级说明，全部截断上限取消。
    """
    rows = []
    try:
        from Virus_Platform_Core.agent_catalog import TOOL_PARAM_SCHEMAS
        rows = TOOL_PARAM_SCHEMAS.get(tool) or []
    except Exception:
        rows = []
    hints = _tool_param_hints(job)
    job_req = [h['key'] for h in hints.get('required', [])]
    out = []
    have = set()
    for row in rows:
        try:
            key, typ, req, desc = row[0], row[1], row[2], row[3]
        except (IndexError, TypeError):
            continue
        out.append({'key': str(key), 'type': str(typ),
                    'required': bool(req), 'desc': str(desc)})
        have.add(str(key))
    for h in hints.get('required', []):
        if h['key'] not in have:
            # catalog 漏登记的、job 却硬校验的键：补上，避免模型按 catalog
            # 传参后被「缺少必填参数」打回。
            out.append({'key': h['key'], 'type': 'str', 'required': True,
                        'desc': str(h['what']), 'source': 'job'})
            have.add(h['key'])
    if not out:
        # catalog 无此工具：退回源码口径（至少给模型的比没有强）
        for h in hints.get('required', []):
            out.append({'key': h['key'], 'type': 'str', 'required': True,
                        'desc': str(h['what']), 'source': 'job'})
        for k in hints.get('optional', []):
            out.append({'key': k, 'type': 'str', 'required': False,
                        'source': 'job'})
    return out


def _list_platform_tools():
    try:
        from Virus_Platform_Core.web.tools_api import TOOL_REGISTRY,             LIGHT_TOOLS
        out = []
        for k, v in sorted(TOOL_REGISTRY.items()):
            entry = {'tool': k, 'title': v['title'],
                     'need_db': bool(v.get('need_db')),
                     'light': k in LIGHT_TOOLS}
            params = _tool_params(k, v.get('job'))
            if params:
                entry['params'] = params
                req = [x['key'] for x in params if x.get('required')]
                if req:
                    entry['required'] = req
            out.append(entry)
        return json.dumps({'count': len(out), 'tools': out,
                           'note': '用 propose_action(action=run_tool, '
                                   'params={tool:…, 其余键见 params}) 起草'
                                   '执行单；params 里除本工具自己声明外，'
                                   '不认识的键会被忽略。很多工具的参数是'
                                   '「三选一」而不是全部必填（例如 kvsuite 的 '
                                   'reads / sample_sheet / index_dir），'
                                   '从 required 列表判断哪些真的必须给；'
                                   '文件键优先用平台根相对路径'},
                          ensure_ascii=False)
    except Exception as e:
        return json.dumps({'error': f'{type(e).__name__}: {e}'},
                          ensure_ascii=False)


def _search_public_data(args):
    """启动一次公共检索（后台任务），返回任务号与集合名。

    完成播报由 _watch_search 守护线程负责：检索完 → ai_read 事件自动到账
    （带 Top 结果摘要），用户不用盯着任务中心。
    """
    import threading
    import time as _time
    from Virus_Platform_Core.config import DIRS, engine_cmd
    from Virus_Platform_Core.utils import check_path
    species = str(args.get('species') or '').strip()
    if not species:
        return json.dumps({'error': '需要 species（物种拉丁名）'},
                          ensure_ascii=False)
    source = str(args.get('source') or 'TRANSCRIPTOMIC')
    if source not in ('TRANSCRIPTOMIC', 'GENOMIC', 'ALL'):
        source = 'TRANSCRIPTOMIC'
    db = str(args.get('db') or 'both')
    if db not in ('both', 'sra', 'gsa'):
        db = 'both'
    name = _meta_collection_name(species)
    out_dir = check_path(os.path.join(DIRS['meta_search'], name, 'search'),
                         must_exist=False, in_platform=True)
    os.makedirs(out_dir, exist_ok=True)
    from Virus_Platform_Core.public_meta import search_engine
    cmd = engine_cmd(search_engine.__file__,
                     '-q', species, '-s', source, '--db', db,
                     '--workers', '5', '-o', out_dir)
    try:
        from Virus_Platform_Core.web.meta import _meta_stream_task
        tid = _meta_stream_task(
            f'公共检索 {species}', cmd, f'检索完成: {out_dir}',
            log_file=os.path.join(out_dir, 'run.log'), link='/meta')
    except Exception as e:
        return json.dumps({'error': f'检索任务启动失败: {e}'},
                          ensure_ascii=False)
    threading.Thread(target=_watch_search,
                     args=(tid, name, species), daemon=True,
                     name=f'ai-meta-watch-{name}').start()
    # 已有同物种历史集合：秒级可读，主动告诉 AI（不必干等网络检索）
    existing = _existing_collections(species)
    return json.dumps(
        {'started': True, 'task': tid, 'collection': name,
         'source': source, 'db': db,
         'existing_collections': existing,
         'note': '检索任务已启动（数分钟）；完成后平台自动播报 Top 结果，'
                 '也可随时用 get_meta_results 查询'
                 + ('。注意：平台已有同物种历史检索集合 '
                    + '、'.join(existing)
                    + '，可先用 get_meta_results 直接读取，无需等本次检索'
                    if existing else '')},
        ensure_ascii=False)


def _existing_collections(species, cap=5):
    """同物种的历史检索集合（名字以物种 slug 开头且结果表有数据）。"""
    from Virus_Platform_Core.config import DIRS
    slug = re.sub(r'[^A-Za-z0-9_\-]', '_', str(species))[:40].strip('_')
    if not slug:
        return []
    root = DIRS['meta_search']
    out = []
    try:
        for nm in sorted(os.listdir(root), reverse=True):
            if not nm.startswith(slug):
                continue
            if os.path.isfile(os.path.join(root, nm, 'search',
                                           'SRA_GSA_Merged_Final.csv')):
                out.append(nm)
            if len(out) >= cap:
                break
    except OSError:
        pass
    return out


def _watch_search(tid, collection, species):
    """检索任务完成 → 自动播报结果摘要（ai_read 事件）。最长守 30 分钟。"""
    import time
    from . import broadcast
    try:
        from Virus_Platform_Core.web.tasks import tm
    except Exception:
        return
    deadline = time.time() + 1800
    while time.time() < deadline:
        try:
            snap = tm.snapshot(tid, log_lines=0, include_result=False)
        except Exception:
            return
        if snap is None:
            return
        st = snap.get('status')
        if st != 'running':
            if st == 'done':
                res = _read_meta_csv(collection, 5)
                if res and res.get('rows'):
                    head = res['header'] or []
                    lines = []
                    for row in res['rows'][:5]:
                        cell = ' | '.join(
                            f'{head[i]}: {v}' for i, v in enumerate(row)
                            if v) [:200]
                        lines.append(f'- {cell}')
                    text = (f'公共检索完成：**{species}**（集合 `{collection}`）'
                            f'共 **{res.get("n_total", "?")}** 条 run。'
                            f'Top 预览：\n' + '\n'.join(lines))
                else:
                    text = (f'公共检索完成：**{species}**（集合 `{collection}`），'
                            f'结果表暂无法读取，请到「公共数据检索」页查看。')
                broadcast.publish('ai_read', sample='', stage='meta_search',
                                  name=f'公共检索 · {species}', text=text)
            else:
                broadcast.publish('stage_failed', sample='',
                                  stage='meta_search',
                                  name=f'公共检索 · {species}',
                                  error=f'检索任务状态: {st}')
            return
        time.sleep(3.0)


def _read_meta_csv(collection, max_rows=12):
    """读检索结果 CSV（逗号分隔，与 digest._tsv_head 的 TSV 口径不同）。

    返回 {file, header, rows, n_total, truncated}；无结果返回 None。
    """
    import csv as _csv
    from Virus_Platform_Core.config import DIRS
    from Virus_Platform_Core.utils import check_path
    collection = str(collection or '').strip()
    if not collection or not _PRESCREEN_NAME_RE.fullmatch(collection):
        return None
    try:
        root = check_path(DIRS['meta_search'], must_exist=True,
                          in_platform=True)
        path = check_path(os.path.join(root, collection, 'search',
                                       'SRA_GSA_Merged_Final.csv'),
                          must_exist=True, in_platform=True)
    except (ValueError, OSError):
        return None
    if os.path.getsize(path) > 64 * 1024 * 1024:
        return None
    try:
        max_rows = max(1, min(30, int(max_rows or 12)))
    except (TypeError, ValueError):
        max_rows = 12
    header, rows, n_total = None, [], 0
    # utf-8-sig：Excel 导出的 CSV 常带 BOM，否则表头首列会出现 ﻿
    with open(path, encoding='utf-8-sig', errors='replace', newline='') as f:
        reader = _csv.reader(f)
        for i, row in enumerate(reader):
            if i == 0:
                header = [c[:80] for c in row][:10]
                continue
            if row and any(c.strip() for c in row):
                n_total += 1
                if len(rows) < max_rows:
                    rows.append([c[:80] for c in row][:10])
    if header is None:
        return None
    return {'file': 'SRA_GSA_Merged_Final.csv', 'header': header,
            'rows': rows, 'n_total': n_total,
            'truncated': n_total > len(rows)}


def _get_meta_results(args):
    res = _read_meta_csv(str(args.get('collection') or ''),
                         args.get('max_rows') or args.get('limit'))
    if res is None:
        return json.dumps(
            {'error': '结果表不存在或尚无可读结果（检索可能仍在进行）'},
            ensure_ascii=False)
    return json.dumps(res, ensure_ascii=False)
