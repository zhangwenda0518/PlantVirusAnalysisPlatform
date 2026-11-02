# -*- coding: utf-8 -*-
"""对话编排：系统提示 + 平台上下文注入 + 工具循环（全流式）。

双层设计（兼容性关键）：
  - **上下文注入（必开）**：每次请求把"平台简况 + 所选样品 digest"组装进
    系统提示。90% 的结果指导提问（这个样品检出了什么/下一步做什么）只靠
    这一层就能回答，不依赖提供方的 tools 能力；
  - **工具循环（进阶）**：提供方支持 function calling 时，代理可主动调用
    tools.py 注册的能力（跨样品查、看任务、翻表）。上游对 tools 参数报错
    时自动降级为纯上下文模式重试一次——各家兼容端点的 tools 支持度不一，
    不能让一次 400 砸掉整场对话。

防幻觉铁律（写进系统提示）：所有数字只允许来自上下文/工具结果，禁止编造；
结论须附证据出处（阶段目录/表名）；超出数据范围时明说"数据不支持"。
"""
import json
import os
import re
import time

from . import digest, tools as ai_tools
from .provider import AIConfigError, AIProviderError, ai_ready, chat_stream

# 2026-10-30 DSML 泄漏防护：个别异常场景（如请求被上游 422 后降级、或供应商
# 偶发故障）会让模型把**内部工具调用标记**（<|DSML|invoke name="…"> 等）
# 当正文吐出来——正常情况这些走结构化 tool_calls 通道，不会出现在 content。
# 入库前剥离，防止污染对话历史形成"越聊越多"的自我强化。
DSML_RE = re.compile(r'</?\|DSML\|[^>]{0,160}>')


def _strip_dsml(text):
    if not text or '<|DSML|' not in text:
        return text
    return DSML_RE.sub('', text)


class _DsmlStreamGuard:
    """正文**流式出口**的 DSML 兜底（2026-11-02）。

    2026-10-30 加的 _strip_dsml 只覆盖了「入库」的三个出口，**没覆盖 delta
    流**：模型一旦把内部标记当正文吐出来，前端逐字渲染，用户看到的就是原始
    `<|DSML|invoke name="propose_action">`（用户截图）。这里在流式出口做同款
    清洗，并把「跨 chunk 的半个标记」先扣住，凑齐再决定剥掉还是放行——
    逐片 strip 会把 `<|DSML|inv` 这种半截直接漏到前端。
    """

    PREFIX = '<|DSML|'                     # 开始标记；闭合形式另算
    MARKERS = ('<|DSML|', '</|DSML|')

    def __init__(self):
        self.buf = ''
        self.stripped = False

    def _holds(self, tail):
        """tail 是否是「可能是标记开头」的半截（含 '</|DSML|' 的闭合形式）。"""
        for p in self.MARKERS:
            if tail.startswith(p) or p.startswith(tail):
                return True
        return False

    def feed(self, piece):
        if not piece:
            return ''
        self.buf += piece
        text = DSML_RE.sub('', self.buf)
        if text != self.buf:
            self.stripped = True
        i = text.rfind('<')
        if i >= 0:
            tail = text[i:]
            # 可能是半个标记：要么是标记的前缀（"<"、"</|DS"…），要么已带
            # 前缀但 '>' 还没到（'<|DSML|invoke name="x"' / '</|DSML|'）
            if self._holds(tail):
                if tail.startswith(self.MARKERS):
                    self.stripped = True
                self.buf = tail
                return text[:i]
        self.buf = ''
        return text

    def flush(self):
        """本轮结束：扣住的尾巴按原样吐出（真半截标记直接丢弃）。"""
        tail, self.buf = self.buf, ''
        if not tail:
            return ''
        if tail.startswith(self.MARKERS):
            self.stripped = True
            return ''
        return tail


# 工具循环上限：防"工具→再工具"失控。
# 2026-10-30：3 轮太紧 —— 模型查两次状态、准备提交预筛时就被截断（用户截图
# 里的「工具调用轮次超限」）。长任务排查常要"列项目→读结果→再查一次→提交"，
# 预算放到 10 轮；真正的失控由「重复调用检测」和轮次上限双保险。
MAX_TOOL_ROUNDS = max(1, min(30, int(os.environ.get('VP_AI_MAX_TOOL_ROUNDS', '10'))))
# 同一工具+参数重复到该次数后不再执行，用工具结果提醒模型停止轮询
MAX_SAME_CALL = 2
# 随请求携带的历史条数上限（服务端无状态，前端传全量，这里裁剪）
MAX_HISTORY = 12
MAX_MSG_CHARS = 6000

_SYSTEM_RULES_ZH = """你是「植物病毒分析平台」的内置 AI 助手，负责帮用户解读
分析结果、指导下一步。你的身份是一位懂病毒基因组学与生信流程的顾问。

铁律：
1. **数字必须来自上下文或工具结果**，禁止编造任何数值、病毒名、百分比；
2. 结论要**附证据出处**（如"02b_kvsuite/identify 表第 2 行"、"05_phylo 树"）；
3. 数据不支持的问题要明说"当前结果不足以判断"，并说明还缺哪一步分析；
4. 建议**可执行**：指出具体该用平台的哪个模块/阶段（如"③b 验证"、"⑧ 引物
   设计"、"LOGAN 溯源"、"NCBI 提交准备"），以及为什么；
5. 涉及分类判定（如 SDT 一致性与种/株阈值）时给出依据并提醒以 ICTV 最新
   分类与原始文献为准；你是顾问，最终科学结论由使用者负责。
6. 回答使用用户当前界面语言，简洁分点，先结论后证据。
7. **执行权边界**：你不能直接执行任何分析动作；当用户要求"重跑/换参数再试/
   修复"时，用 propose_action 工具起草执行单（写清动作、阶段、参数与理由），
   用户批准后平台才会运行。参数变更必须给出依据，不确定时先问用户。
8. **上下文里的三块平台级信息各有边界**：`tool_runs` 是工具箱/扩展分析任务
   时间线（按工具去重；`metrics` 只对有稳定契约的工具出现，其余只有存在性）；
   `extensions` 是 LOGAN 溯源 / NCBI 提交准备等一次性任务的存在性；
   `prescreen` 是**病毒样本预筛**项目的计数（`n_runs`/`n_pass`/`n_ok`，与当前
   所选样品无关，别混为一谈）。要读某次任务的具体表格，用 `read_tool_run_file`；
   读样品结果目录内的表用 `read_result_table`。**没有数就别猜**——上下文里
   没给的，明说"当前上下文没有这项数据"。
9. **找数据是平台的本职能力**：用户想找任何物种的测序数据（转录组/RNA-seq、
   基因组、病毒组……），**直接调 `search_public_data` 工具检索 SRA+GSA**，
   绝不要回答"平台查不了"或把用户推去外部数据库——那是平台「公共数据检索」
   模块的本职工作。检索启动后告知用户"已完成会自动播报"；出结果后主动摘要，
   并用 propose_action(download_runs) 提议把选中的 run 下载进平台。
10. **自我介绍要全量**：用户问"你能做什么/介绍一下功能"时，按四段呈现——
   ① 解读与判读（读样品/工具箱/预筛结果）；② **找数据**（search_public_data
   检索 SRA+GSA，完成后自动播报）；③ **可执行的动作**（propose_action：
   重跑管道阶段、原参数重试、下载公共数据、**运行工具箱全部 ~30 个工具**
   ——比对/SDT/RDP/引物设计/共识/ORF/系统地理/dsRNA/miRNA 等，清单用
   list_platform_tools 查、创建样品）——批准后由平台真实运行；
   ④ 边界（只能起草执行单，批准权在用户）。**不要把自己说成只会解读**
   ——平台的模块能力（见下方清单）原则上都能通过执行单驱动。

11. **MCP 执行层（你可直接干活，不必事事走执行单）**：你有
   mcp_tool_catalog（列出**当前全部** vp_* 工具与参数口径）与 mcp_call
   （执行任意 vp_* 工具）两个工具，覆盖：工具箱执行(vp_run_tool)、流水线
   (vp_pipeline_start)、CLI(vp_cli_run/vp_cli_submit：建库/公共数据检索
   /NCBI提交/LOGAN溯源/ICTV/病毒预筛)、任务与产物查询(vp_get_task/
   vp_read_run_file/vp_list_*)、OpenVirome 云端检索(vp_ov_search_runs/
   counts/mwas)。**工具总数与可用工具以 mcp_tool_catalog 的返回为准**，
   不要凭记忆报数字或假设某工具一定存在（平台升级会增删）。
   用户说「帮我跑/查/提交/搜数据」时：先 mcp_tool_catalog
   查口径 → mcp_call **直接执行**（无需执行单）→ 把返回信封的
   summary/interpretation/next 讲给用户。任务类提交后告知 task_id 即可，
   用户追问进度再轮询，不要在对话里空转等待。例外：vp_delete_tool_run
   已禁用；db-migrate 落盘迁移、tool-runs clean 等不可逆操作先向用户
   确认再执行；propose_action 继续用于样品创建与下载批次等原生流程。

12. **计划 ≠ 已执行（反幻觉红线）**：propose_action 返回成功只代表「已起草
    待批」，不代表已开始、更不代表已完成；任务提交成功 ≠ 有结果。
    只有三种情况可以对用户说「已开始/已完成」：① 任务中心出现真实任务 id
    且终态 done（get_task_overview / wait_task 可见）；② 你亲自读到了落盘
    产物（read_result_table / read_tool_run_file 等）；③ 同步动作
    create_sample 在执行单 done 后。禁止由计划推断结果、禁止编造检测数字；
    用户催进度时照实说「还在后台跑」或「执行单还没批准」。

13. **长任务两条路**（mcp_call / 执行单提交任务成功后）：
    ① 预计 ≤4 分钟或已接近完成 → 调 wait_task(tid, timeout_sec≤240) 有界等待，
       等到终态直接给结果与解读；
    ② 可能更久 → 立即收尾：告知用户"已提交、在后台运行、完成后会播报"，
       本轮结束。不要空转轮询；用户问进度时用 get_task_overview 查一次，
       并可给用户链接 /tasks?focus=<tid> 直达该任务卡。

平台能力清单（本平台**自带**以下模块，回答"平台能不能做 X"以此为准）：
{capabilities}

平台上下文（{lang_name}，由平台自动注入，数字均为代码从结果文件算出）：
```json
{context}
```"""

_SYSTEM_RULES_EN = """You are the built-in AI assistant of the VirusPlatform
(plant virus analysis). You interpret analysis results and guide next steps
like a virology/bioinformatics consultant.

Hard rules:
1. Numbers MUST come from the injected context or tool results — never invent
   values, virus names or percentages;
2. Cite evidence for every conclusion (e.g. "02b_kvsuite/identify table",
   "05_phylo tree");
3. If the data cannot support a question, say so and name the missing step;
4. Give actionable advice mapped to concrete platform modules/stages
   (e.g. stage ③b verify, ⑧ primer design, LOGAN trace, NCBI submission);
5. For taxonomy calls (SDT identity vs species thresholds) state the basis
   and remind that ICTV taxonomy and primary literature are authoritative;
   you advise, the user owns the scientific conclusion;
6. Answer in the user's interface language, concise and structured,
   conclusion first.
7. **Execution boundary**: you cannot execute anything yourself. When the
   user asks to "rerun / retry with different parameters / fix", draft an
   action order with the propose_action tool (action, stages, parameters,
   and a clear reason); it runs only after the user approves it in the dock.
   Parameter changes need evidence — ask first when unsure.
8. **The three platform-level blocks have different scopes**: `tool_runs` is
   the toolbox / extension-analysis timeline (deduplicated by tool; `metrics`
   appears only for tools with a stable contract, others are existence-only);
   `extensions` is existence-only for one-off jobs (LOGAN trace, NCBI
   submission prep); `prescreen` is the **virus pre-screening** project
   counters (`n_runs`/`n_pass`/`n_ok`) and is NOT about the selected sample —
   do not conflate them. To read a specific task's table use
   `read_tool_run_file`; for tables inside a sample's result dir use
   `read_result_table`. **Never guess a number that is not in the context** —
   say the data is unavailable instead.
9. **Finding data is a core platform capability**: when the user wants
   sequencing data of any species (transcriptome/RNA-seq, genomes, viromes…),
   call the `search_public_data` tool to search SRA+GSA directly. NEVER say
   "the platform cannot search" or push the user to external databases —
   that is exactly what the built-in public-data search module does. After
   starting, tell the user results will be broadcast automatically; when
   results arrive, summarise them and propose downloading selected runs via
   propose_action(download_runs).
10. **Self-description must be complete**: when asked "what can you do",
   present four sections — ① interpretation (samples/toolbox/prescreen
   results); ② **data discovery** (search_public_data over SRA+GSA with
   auto-broadcast); ③ **executable actions** via propose_action (pipeline
   stage re-runs, same-param retry, public-data download, **any of the ~30
   toolbox tools** — alignment/SDT/RDP/primers/consensus/ORF/
   phylogeography/dsRNA/miRNA, see list_platform_tools — and sample
   creation), all running for real after user approval; ④ boundary (you
   only draft action orders; approval belongs to the user). Never undersell
   yourself as interpretation-only — platform modules (map below) are
   drivable through action orders.

11. **MCP execution layer (you CAN do real work now — no action order
   needed for these)**: you have mcp_tool_catalog (lists ALL currently
   registered vp_* tools and their parameter contracts) and mcp_call
   (execute any vp_* tool). Coverage: toolbox execution (vp_run_tool),
   pipeline (vp_pipeline_start), CLI (vp_cli_run / vp_cli_submit: DB
   builds / public-data / NCBI submission / LOGAN trace / ICTV / virus
   pre-screening), task & artifact queries (vp_get_task /
   vp_read_run_file / vp_list_*), OpenVirome cloud search
   (vp_ov_search_runs / counts / mwas). **The authoritative tool list and
   count come from mcp_tool_catalog — never recall a number or assume a
   tool exists from memory (tools are added/removed across upgrades).**
   When the user says "run / check / submit / find data for me": look up
   the contract with mcp_tool_catalog, then execute DIRECTLY via mcp_call
   (no action order), and report the envelope's
   summary/interpretation/next. For task-submitting calls, tell the user
   the task_id; only poll when they ask for progress — do not idle-wait
   inside the conversation. Exceptions: vp_delete_tool_run is disabled;
   for irreversible ops (db-migrate apply, tool-runs clean) confirm with
   the user first; propose_action remains for native flows (sample
   creation, download batches).

12. **A plan is not an execution (anti-hallucination red line)**: a successful
    propose_action only means "drafted, awaiting approval" — not started,
    let alone finished; a successful task submission is not a result. Say
    "started/finished" only when: (a) a real task id exists in the task
    center and reached done (get_task_overview / wait_task); (b) you read
    the on-disk artifacts yourself (read_result_table / read_tool_run_file);
    (c) the synchronous create_sample action finished (order done). Never
    infer results from a plan; never invent numbers; if the user pushes for
    progress, say honestly "still running in background" or "not approved yet".

13. **Long tasks, two routes** (after a task-submitting mcp_call / action):
    (a) if it should finish within ~4 minutes or is nearly done → call
    wait_task(tid, timeout_sec<=240) and report the final state directly;
    (b) if it may take longer → stop immediately: tell the user it is
    submitted, running in the background, and the platform will broadcast
    when it finishes. Do not idle-poll; on progress questions use
    get_task_overview once, and link the user to /tasks?focus=<tid>.

Platform capability map (the platform ships all of the following modules —
use this to answer "can the platform do X"):
{capabilities}

Platform context (auto-injected; all numbers computed from result files):
```json
{context}
```"""


def build_context(sample=None):
    """组装注入系统提示的上下文 dict（控制体积：样品 digest 已天然有界）。"""
    ctx = {'platform': digest.platform_brief()}
    if sample:
        d = digest.sample_digest(sample)
        if d is not None:
            ctx['selected_sample'] = d
    # 样品锚（2026-10-30）：让模型知道"哪些样品已锚定、输入有没有被动过"。
    # 只读 anchor.json 的摘要，很便宜；不复算 sha256（核对是显式动作）。
    try:
        from Virus_Platform_Core.anchor import samples_meta
        meta = samples_meta()
        if meta:
            ctx['anchors'] = {
                slug: {'sample': v.get('sample') or slug,
                       'status': v.get('status'),
                       'seq': v.get('seq'),
                       'finished': v.get('finished')}
                for slug, v in meta.items()}
    except Exception:
        pass
    return ctx


def _clip_history(messages):
    out = []
    for m in messages[-MAX_HISTORY:]:
        role = m.get('role')
        if role not in ('user', 'assistant'):
            continue
        content = str(m.get('content') or '')
        out.append({'role': role, 'content': content[:MAX_MSG_CHARS]})
    return out


def _sse(obj):
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


def _capability_map():
    """平台能力清单：从导航单一真源（web/pages.py 的 NAV_GROUPS）生成，
    供系统提示回答"平台能不能做 X"。这是修复"AI 说平台查不了转录组"的
    根治手段——能力感知来自平台自身的模块清单，而不是模型猜测。"""
    try:
        from Virus_Platform_Core.web.pages import NAV_GROUPS
        lines = []
        for g in NAV_GROUPS:
            items = []
            for it in g.get('items', [])[:8]:
                t = str(it.get('title') or '').strip()
                d = str(it.get('desc') or '').strip()[:36]
                items.append(f"{t}（{d}…）" if d else t)
            lines.append(f"- {g.get('label')}: {'；'.join(items)}")
        return '\n'.join(lines)
    except Exception as e:
        return f'（能力清单生成失败: {e}）'


_ATTACH_TEXT_EXT = {'.txt', '.tsv', '.csv', '.tab', '.json', '.log', '.md',
                    '.fa', '.fasta', '.fna', '.fas', '.fq', '.fastq',
                    '.nwk', '.tree', '.gff', '.gtf', '.yaml', '.yml',
                    '.py', '.sh', '.r', '.tab'}
_ATTACH_MAX = 8                       # 一次最多带 8 个附件
_ATTACH_PREVIEW_CHARS = 2000          # 每个文本附件给模型的预览长度


def _attach_digest(paths):
    """把用户附加的文件整理成给模型的清单：名字 / 绝对路径 / 大小 / 文本预览。

    只读用户显式附加的文件（不遍历目录、不扩大权限）；二进制附件只报
    名字与大小，文本附件给前 2000 字符，够模型判断"这是什么、能不能直接
    喂给某个工具"，又不至于把上下文撑爆。
    """
    out = []
    for raw in (paths or [])[:_ATTACH_MAX]:
        p = str(raw or '').strip()
        if not p:
            continue
        try:
            ap = os.path.abspath(p)
            if not os.path.isfile(ap):
                out.append({'path': p, 'error': '文件不存在'})
                continue
            size = os.path.getsize(ap)
            item = {'name': os.path.basename(ap), 'path': ap, 'size': size}
            ext = os.path.splitext(ap)[1].lower()
            if ext in _ATTACH_TEXT_EXT and size <= 8 * 1024 * 1024:
                with open(ap, 'r', encoding='utf-8', errors='replace') as fh:
                    item['preview'] = fh.read(_ATTACH_PREVIEW_CHARS)
            out.append(item)
        except OSError as e:
            out.append({'path': p, 'error': f'{type(e).__name__}: {e}'})
    return out


_WORKSPACE_MAX_ENTRIES = 200


def _workspace_digest(path):
    """把用户选中的「工作空间」目录整理成给模型的清单。

    2026-10-30：右侧文件面板选了目录后，模型原本完全不知道它存在
    （list_samples 为空 → 只会让用户重报路径）。这里列一层目录内容
    （名字/类型/大小/绝对路径），模型据此直接起草 create_sample 或
    run_tool。只列一层、不递归：用户选的工作空间通常就是"一批 FASTQ"
    或"一次工具运行目录"。
    """
    p = str(path or '').strip()
    if not p:
        return None
    if not os.path.isdir(p):
        return {'path': p, 'error': '目录不存在或不是文件夹'}
    try:
        names = sorted(os.listdir(p))
    except OSError as e:
        return {'path': p, 'error': f'{type(e).__name__}: {e}'}
    entries = []
    for name in names:
        if len(entries) >= _WORKSPACE_MAX_ENTRIES:
            break
        full = os.path.join(p, name)
        try:
            if os.path.isdir(full):
                entries.append({'name': name, 'type': 'dir'})
            elif os.path.isfile(full):
                entries.append({'name': name, 'type': 'file',
                                'size': os.path.getsize(full), 'path': full})
        except OSError:
            continue
    return {'path': p, 'entries': entries, 'total': len(names),
            'truncated': len(names) > len(entries)}


def _system_prompt(sample, attachments=None, workspace=None):
    from Virus_Platform_Core.config import get_config
    lang = get_config().lang
    try:
        ctx = build_context(sample)
        if attachments:
            # 用户在本轮附加的文件（2026-10-30）：路径可直接用于 run_tool 等动作
            ctx['attachments'] = _attach_digest(attachments)
        ws = _workspace_digest(workspace)
        if ws:
            # 用户在总览右侧选的工作空间目录（2026-10-30）：列出内容，
            # 模型可以直接从这些文件路径起草 create_sample / run_tool
            ctx['workspace'] = ws
        ctx_json = json.dumps(ctx, ensure_ascii=False)
    except Exception as e:                     # 上下文失败不拦对话
        ctx_json = json.dumps({'error': f'context unavailable: {e}'},
                              ensure_ascii=False)
    if len(ctx_json) > 60000:                  # 极端大样品：截断保底
        ctx_json = ctx_json[:60000] + '…(truncated)'
    tpl = _SYSTEM_RULES_ZH if lang == 'zh' else _SYSTEM_RULES_EN
    return tpl.format(context=ctx_json, capabilities=_capability_map(),
                      lang_name='简体中文' if lang == 'zh' else 'English')


def run_chat(ai_cfg, messages, sample=None, attachments=None, workspace=None):
    """跑一轮对话，yield SSE 事件 dict（调用方逐个 `_sse()` 下发）。

    事件：delta（正文增量）/ tool（告知前端的工具活动）/ error（可读错误）/
    done（结束标记，附带本次是否降级过工具）。

    attachments：用户在本轮附加的本地文件绝对路径（2026-10-30）。只注入
    上下文（名字/大小/文本预览），不改变工具权限。
    """
    sys_prompt = _system_prompt(sample, attachments, workspace)
    convo = [{'role': 'system', 'content': sys_prompt}]
    convo += _clip_history(messages)
    # 长对话静默丢记忆是"AI 变傻"的常见抱怨源：坞里存 30 条、服务端只带
    # 最近 MAX_HISTORY 条，用户以为它还记得开头。这里明说一句。
    dropped_n = max(0, len(messages or []) - MAX_HISTORY)
    if dropped_n > 0:
        yield _sse({'type': 'notice', 'level': 'info',
                    'message': f'更早的 {dropped_n} 条对话未带入本轮上下文'
                               f'（只带最近 {MAX_HISTORY} 条）'})
    if not convo or convo[-1]['role'] != 'user':
        yield _sse({'type': 'error',
                    'message': '消息为空，请先输入问题'})
        yield _sse({'type': 'done'})
        return

    degraded = False
    use_tools = True
    call_sigs = []          # 本轮出现过的 (工具, 参数) 签名，用于拦重复轮询
    # 生成长度预算：推理型模型（deepseek-reasoner/flash 等）会把预算先花在
    # reasoning_content 上，2048 常常只够"想一想"，正文为空或半截。这里给
    # 一个可自动加倍的预算游标，并在触顶时明确告知用户。
    _BUDGET_MAX = 8192
    budget = int(ai_cfg.get('max_tokens') or 2048)
    budget_bumped = False
    for _round in range(MAX_TOOL_ROUNDS + 1):
        emit_parts = []
        tool_called = False
        tries = 0
        guard = _DsmlStreamGuard()   # 本轮正文的流式 DSML 兜底
        finish = ''
        reasoning_chars = 0
        while True:
            tries += 1
            hit_tools = False
            try:
                for ev in chat_stream(
                        ai_cfg, convo,
                        tools=ai_tools.TOOLS_SPEC if use_tools else None,
                        max_tokens=budget):
                    if ev['type'] == 'reasoning':
                        # 思维链单独下发：前端放在可折叠的「思考过程」里，
                        # 不计入正文（也不进对话历史）
                        yield _sse({'type': 'reasoning',
                                    'content': ev['content']})
                    elif ev['type'] == 'delta':
                        piece = guard.feed(ev['content'])
                        if piece:
                            emit_parts.append(piece)
                            yield _sse({'type': 'delta', 'content': piece})
                    elif ev['type'] == 'finish':
                        finish = ev.get('reason') or ''
                        reasoning_chars = int(ev.get('reasoning_chars') or 0)
                    elif ev['type'] == 'tool_calls':
                        hit_tools = True
                        tcs = ev['tool_calls']
                        tail = guard.flush()
                        if tail:
                            emit_parts.append(tail)
                            yield _sse({'type': 'delta', 'content': tail})
                        # 协议要求工具调用轮的 assistant 消息带上已流出正文
                        # （content 用空串而非 null——部分国产端点不收 null）
                        convo.append({'role': 'assistant',
                                      'content': _strip_dsml(''.join(emit_parts)),
                                      'tool_calls': tcs})
                        for tc in tcs:
                            fn = (tc.get('function') or {})
                            name = fn.get('name') or ''
                            raw_args = fn.get('arguments') or ''
                            arg_err = ''
                            try:
                                fargs = json.loads(raw_args or '{}')
                                if not isinstance(fargs, dict):
                                    raise ValueError('arguments 不是 JSON 对象')
                            except ValueError as _e:
                                # 2026-11-02：以前直接把它当空参数执行，模型
                                # 收到「缺少必填参数」却不知道是自己的 JSON
                                # 坏了，往往原地重试同一个坏串。这里把解析
                                # 失败原文回给它，便于修正。
                                fargs = {}
                                arg_err = str(_e)
                            # 重复调用拦截（2026-10-30）：模型查任务状态时容易
                            # 反复调同一工具同一参数，把轮次预算烧光。第 3 次起
                            # 不再执行，直接把"别再轮询"作为工具结果回给它。
                            sig = json.dumps([name, fargs], sort_keys=True,
                                             ensure_ascii=False)
                            seen = call_sigs.count(sig)
                            call_sigs.append(sig)
                            if seen >= MAX_SAME_CALL:
                                yield _sse({'type': 'tool', 'name': name,
                                            'args': fargs, 'skipped': True})
                                convo.append({
                                    'role': 'tool',
                                    'tool_call_id': tc.get('id') or '',
                                    'content': (
                                        'STOP: 相同工具与参数已调用 %d 次，结果不会变化。'
                                        '请停止轮询，基于已有结果直接给出结论；'
                                        '若要启动长任务，调用一次提交类工具'
                                        '（propose_action / mcp_call）后立即结束本轮，'
                                        '由平台后台在完成后播报。' % (seen + 1))})
                                continue
                            yield _sse({'type': 'tool', 'name': name,
                                        'args': fargs})
                            if arg_err:
                                result = json.dumps({
                                    'error': '工具参数不是合法 JSON：'
                                             f'{arg_err}。请重新生成**紧凑的'
                                             '合法 JSON**（字符串里的引号要'
                                             '转义，不要带注释/尾逗号）',
                                    'raw_arguments_head': raw_args[:200]},
                                    ensure_ascii=False)
                            else:
                                result = ai_tools.dispatch(name, fargs)
                            convo.append({'role': 'tool',
                                          'tool_call_id': tc.get('id') or '',
                                          'content': result[:8000]})
                        break                # 工具结果已入列，进入下一轮
                    elif ev['type'] == 'message':
                        msg = ev.get('message') or {}
                        if msg and msg.get('tool_calls') is None:
                            text = _strip_dsml(msg.get('content'))
                            if text and not emit_parts:
                                emit_parts.append(text)
                                yield _sse({'type': 'delta', 'content': text})
                            convo.append({'role': 'assistant',
                                          'content': text or ''})
                if hit_tools:
                    tool_called = True
                    break                    # → 下一轮（带工具结果）
                # 思考吃光预算、正文一个字都没出来：先把预算加倍重试一次
                # （推理型模型在 max_tokens=2048 时很常见），而不是直接告诉
                # 用户「模型未返回内容」。
                if (not emit_parts and finish == 'length'
                        and budget < _BUDGET_MAX and tries < 3):
                    budget = min(_BUDGET_MAX, max(budget * 2, 2048))
                    if not budget_bumped:
                        budget_bumped = True
                        yield _sse({'type': 'notice', 'level': 'info',
                                    'message': f'思考过程占满了生成长度上限，'
                                               f'已自动把预算调到 {budget} '
                                               f'tokens 重试一次（可在 设置 → '
                                               f'AI 助手 里调大「最大生成长度」）'})
                    guard = _DsmlStreamGuard()   # 上一轮没有任何正文吐出
                    continue
                # 没遇到工具调用：正常结束
                tail = guard.flush()
                if tail:
                    emit_parts.append(tail)
                    yield _sse({'type': 'delta', 'content': tail})
                if not emit_parts:
                    msg = '模型未返回内容'
                    if finish == 'length':
                        msg += ('（思考占满了 max_tokens，正文没能生成）——'
                                '请在 设置 → AI 助手 把「最大生成长度」调到 '
                                '4096 以上，或换用非推理型模型')
                    elif reasoning_chars:
                        msg += (f'（只输出了 {reasoning_chars} 字符思考过程，'
                                f'正文为空）——把「最大生成长度」调大再试')
                    else:
                        msg += '，请稍后重试'
                    yield _sse({'type': 'error', 'message': msg})
                elif finish == 'length':
                    # 有正文但触顶：把"被截断"说清楚，别让用户以为答完了
                    yield _sse({'type': 'delta', 'content':
                                '\n\n_（回答触到「最大生成长度」上限被截断：'
                                '可在 设置 → AI 助手 调大 max_tokens，'
                                '或说「继续」让我接着写。）_'})
                    yield _sse({'type': 'notice', 'level': 'warn',
                                'message': '本次回答达到长度上限，内容可能不完整'})
                yield _sse({'type': 'done', 'tools_degraded': degraded,
                            'dsml_stripped': guard.stripped,
                            'truncated': finish == 'length',
                            'max_tokens': budget})
                return
            except AIConfigError as e:
                yield _sse({'type': 'error',
                            'message': f'{e}（请在 设置 → AI 助手 中配置）'})
                yield _sse({'type': 'done'})
                return
            except AIProviderError as e:
                transient = (
                    e.status in (0, 429, 500, 502, 503, 504)
                    or 'connection' in str(e).lower()
                    or 'timed out' in str(e).lower())
                # 瞬态网络故障（连接被重置/限流/超时）且**尚未吐出任何字**
                # → 同一轮自动重试一次（已吐字重试会造成正文重复，只能报错）
                if not emit_parts and transient and tries < 2:
                    time.sleep(1.5)
                    continue
                # tools 参数不被支持是各家端点最常见差异：降级重试一次。
                # 2026-11-02：降级必须**告诉用户**（以前只放在 done 事件里，
                # 前端没渲染，用户只看到模型"变笨、不干活"，不知道该修什么）。
                if use_tools and e.status in (400, 404, 422) and tries < 2:
                    use_tools = False
                    degraded = True
                    yield _sse({'type': 'notice', 'level': 'warn',
                                'message': (
                                    f'上游拒绝了 tools 参数（HTTP {e.status}），'
                                    f'本轮已降级为「纯上下文」模式：我不能调用'
                                    f'工具、也不能起草执行单。原文：'
                                    f'{str(e)[:240]}')})
                    continue
                yield _sse({'type': 'error', 'message': str(e)})
                yield _sse({'type': 'done', 'tools_degraded': degraded})
                return
            except Exception as e:           # 兜底：不让生成器半途崩掉
                yield _sse({'type': 'error',
                            'message': f'{type(e).__name__}: {e}'})
                yield _sse({'type': 'done', 'tools_degraded': degraded})
                return
        if not tool_called:                  # 理论不可达，保险丝
            break

    # 轮次用完：强制一次「不带工具」的收尾，让模型把已查到的东西讲清楚，
    # 而不是只丢一句"已截断"（2026-10-30 用户截图）。
    final_note = ('（工具调用轮次已用完）请不要再调用工具。基于上面的工具结果，'
                  '用简洁中文给出：已确认的事实、当前结论、建议的下一步'
                  '（需要执行的动作写成"建议提交 X"，不要假装已执行）。')
    try:
        emitted = False
        guard = _DsmlStreamGuard()
        for ev in chat_stream(ai_cfg,
                              convo + [{'role': 'user', 'content': final_note}],
                              tools=None):
            if ev.get('type') == 'delta' and ev.get('content'):
                piece = guard.feed(ev['content'])
                if piece:
                    emitted = True
                    yield _sse({'type': 'delta', 'content': piece})
        tail = guard.flush()
        if tail:
            emitted = True
            yield _sse({'type': 'delta', 'content': tail})
        if not emitted:
            yield _sse({'type': 'error',
                        'message': '工具调用轮次超限，且未能生成收尾总结；请换个更具体的问法重试'})
    except Exception as e:
        yield _sse({'type': 'error',
                    'message': f'工具调用轮次超限，收尾总结失败: {type(e).__name__}: {e}'})
    yield _sse({'type': 'done', 'tools_degraded': degraded})


def chat_ready():
    from Virus_Platform_Core.config import get_config
    return ai_ready(get_config().ai)
