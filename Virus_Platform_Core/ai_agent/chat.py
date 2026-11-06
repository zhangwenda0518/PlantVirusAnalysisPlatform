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
import logging
import os
import re
import time

from . import artifacts as _artifacts, digest, tools as ai_tools
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
# 2026-11-04：10 → 24。真实长链路（列表→读表→翻页→查任务→起草→验收）
# 十几轮很常见，10 轮仍会提前收尾；重复调用检测仍是防空转的第一道闸。
MAX_TOOL_ROUNDS = max(1, min(30, int(os.environ.get('VP_AI_MAX_TOOL_ROUNDS', '24'))))
# 同一工具+参数重复到该次数后不再执行，用工具结果提醒模型停止轮询
MAX_SAME_CALL = 2
# 随请求携带的历史条数上限（服务端无状态，前端传全量，这里裁剪）
MAX_HISTORY = 32   # 2026-11-04：20→32（配合 _history_digest：被裁掉的更早消息压成前情摘要，不再静默丢记忆）
MAX_MSG_CHARS = 6000
# 生成长度预算上限（2026-11-04：8192 → 16384）。推理型模型会把预算先花在
# reasoning_content 上，触顶时 run_chat 会自动加倍；上限跟着默认 max_tokens
# （8192）抬高，给长回答/长思考留一次加倍空间。
_BUDGET_MAX = 16384

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

14. **产物直接给路径，别让人自己翻**：报告、图、表、共识序列这些已经落盘的
    东西，回答里写出**绝对路径**（对话坞会自动渲染成可点开的卡片：图片内联、
    HTML 可预览/新标签打开、表格给前几行）。不要写「去结果中心点看」这类
    指路话；也不要给相对路径。路径必须是真读到的（工具返回或列目录得到），
    不许凭命名规则拼一个「应该在那」的路径。

14. **平台状态只能来自本次注入或工具返回（2026-11-03 反编造红线）**：
   下方 `platform` 字段（samples / tool_runs / extensions / prescreen /
   databases）是平台状态的**唯一权威**。历史对话里的样品名、阶段进度、
   任务号、计数一律视为过期，**不得当作当前状态复述**。
   - `samples` 为空 → 必须说"当前没有样品"；
   - **禁止列举任何未被 `platform`/工具返回的样品名**（如 Dataset_11、A、
     MY-77）或"x/15 阶段、停在 fastq"这类细节——没有来源就是编造；
   - 需要最新状态时调 list_samples / get_task_overview 现查，把返回原样
     转述；查不到就说"当前上下文没有这项数据"。
   自我介绍/欢迎语同样适用：只讲能力，不附带任何未查实的平台状态数字。

17. **澄清优先（2026-11-04）**：缺关键信息时（样品名指代不明、多个候选路径
    只能二选一、参数缺失且无法从上下文推断），先问**一个**最关键的问题再
    动手；禁止猜测。能从上下文确定的就不要问（例如样品在选中态、路径在
    workspace/attachments 里已给出）。
18. **回答脚手架（2026-11-04）**：解读分析结果的回答按「结论 → 证据 →
    下一步」三段组织；证据必须给出落盘产物的**绝对路径**或表名；下一步给
    可执行建议（哪个模块/阶段、带什么参数）。
19. **next_actions 输出（2026-11-04）**：若回答里建议了后续动作，在回答
    **最末尾**另起一段输出一个 JSON 代码块：
    ```next_actions
    [{{"label":"按钮文案(中文≤12字)","action":"动作名","sample":"样品名(可选)","stages":["阶段(可选)"],"params":{{...}},"reason":"一句话理由"}}]
    ```
    action 取值与 propose_action 的 enum 一致；最多 3 个；每项必须填
    reason；没有建议就完全不输出这个块；禁止把该块写进正文中间、禁止在
    块外再解释 JSON 本身。

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

14. **Hand over artifacts by absolute path, not by pointing at the UI**: for
    reports, figures, tables and consensus sequences that already exist on
    disk, write the **absolute path** in the answer — the dock renders it as a
    clickable card (images inline, HTML previewable / openable in a new tab,
    tables with their first rows). Do not answer "go to the results center";
    do not give relative paths. The path must be one you actually read (tool
    result or directory listing) — never assemble a "should be there" path
    from a naming convention.

15. **Platform status may come only from this turn's context or a tool result
    (2026-11-03 anti-fabrication rule)**: the `platform` field below
    (samples / tool_runs / extensions / prescreen / databases) is the ONLY
    authority. Sample names, stage progress, task ids and counts from earlier
    turns are stale — never restate them as current status. If `samples` is
    empty, say "there are no samples". **Never list sample names** (e.g.
    Dataset_11, A, MY-77) or details like "x/15 stages, stopped at fastq"
    that are not present in the context or a tool result — without a source
    that is fabrication. Query list_samples / get_task_overview when you need
    fresh state and relay the result verbatim; if unavailable, say the
    context does not contain it. The same applies to greetings: describe
    capabilities only, never attach unverified status numbers.

17. **Clarify first (澄清优先, 2026-11-04)**: when key information is missing
    (an ambiguous sample name, two candidate paths to choose between, a
    required parameter that cannot be inferred from context), ask **one**
    most critical question before acting; never guess. Do not ask when the
    context already answers it (selected sample, paths in workspace or
    attachments).
18. **Answer scaffold (2026-11-04)**: when interpreting results, organise
    the answer as **Conclusion → Evidence → Next step**; evidence must cite
    the **absolute path** of an on-disk artifact or a table name; the next
    step must be actionable (which module/stage, with which parameters).
19. **next_actions output (2026-11-04)**: if your answer suggests follow-up
    actions, append at the very end a single JSON code block:
    ```next_actions
    [{{"label":"button text (<=12 chars)","action":"action name","sample":"sample (optional)","stages":["stage (optional)"],"params":{{...}},"reason":"one-line reason"}}]
    ```
    `action` must match propose_action's enum; at most 3 items; every item
    needs `reason`; if you suggest nothing, do not output the block at all;
    never put it in the middle of the body and never explain the JSON
    outside the block.

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
    except Exception as e:
        logging.getLogger(__name__).warning(
            'AI 上下文锚点摘要生成失败: %s: %s', type(e).__name__, e)
    # 用户反馈钩子（2026-11-04 T7）：feedback.py 由反馈模块提供
    # recent(n) -> list[str]；模块/接口尚不存在时静默跳过，绝不拦对话。
    try:
        from .feedback import recent as _fb_recent
        _fb = _fb_recent(8)
        if _fb:
            ctx['user_feedback'] = _fb
    except ImportError:
        pass
    return ctx


def _plan_tools(allow_plan=True):
    """任务分解可选（2026-11-03）：plan_mode='off' 时从工具契约里**物理移除**
    propose_action 的 plan 动作——强制单步执行，不依赖提示词自觉。其余模式
    直接复用模块级 TOOLS_SPEC（零拷贝）。"""
    if allow_plan:
        return ai_tools.TOOLS_SPEC
    import copy as _copy
    spec = _copy.deepcopy(ai_tools.TOOLS_SPEC)
    for t in spec:
        fn = t.get('function') or {}
        if fn.get('name') != 'propose_action':
            continue
        enum = (((fn.get('parameters') or {}).get('properties') or {})
                .get('action') or {}).get('enum') or []
        if 'plan' in enum:
            enum.remove('plan')
        fn['description'] = str(fn.get('description') or '').replace(
            'plan=多步计划', '（plan 动作已被用户关闭——只允许单步动作）')
    return spec


def _clip_history(messages):
    out = []
    for m in messages[-MAX_HISTORY:]:
        role = m.get('role')
        if role not in ('user', 'assistant'):
            continue
        content = str(m.get('content') or '')
        out.append({'role': role, 'content': content[:MAX_MSG_CHARS]})
    return out


def _history_digest(messages):
    """把 _clip_history 丢弃的更早消息压成一段「前情摘要」（2026-11-04）。

    之前超出 MAX_HISTORY 的对话被静默丢弃，用户换几轮再问开头的事，模型
    就"失忆"了。这里取 messages[:-MAX_HISTORY]，每条只留 role + 正文前
    80 字（压成单行），用 '；' 连接，总量截 1200 字符，塞进系统提示的
    history_digest 字段——告诉模型"前面聊过什么"，但不撑爆上下文。
    没有可压缩内容时返回 ''（调用方据此不注入该字段）。
    """
    msgs = list(messages or [])
    dropped = msgs[:-MAX_HISTORY] if len(msgs) > MAX_HISTORY else []
    parts = []
    for m in dropped:
        if not isinstance(m, dict):
            continue
        role = str(m.get('role') or '')
        if role not in ('user', 'assistant'):
            continue
        content = re.sub(r'\s+', ' ', str(m.get('content') or '')).strip()
        if not content:
            continue
        parts.append(f'{role}: {content[:80]}')
    if not parts:
        return ''
    return '；'.join(parts)[:1200]


# ===== next_actions（2026-11-04 T5）====================================
# 模型在回答最末尾用 ```next_actions [...] ``` 声明「建议的后续动作」，
# 后端解析后下发给前端渲染成按钮，并把该围栏块从正文里删掉——否则用户会
# 在正文里看到一坨裸 JSON，产物卡片解析也会把它当普通文本。
_NEXT_ACTIONS_RE = re.compile(r'```next_actions\b(.*?)```',
                              re.DOTALL | re.IGNORECASE)


def _propose_action_enum():
    """从 TOOLS_SPEC 现取 propose_action 的 action 枚举（不硬编码）。

    平台增删动作时这里自动跟随；取不到时返回空表（调用方按"无合法动作"
    处理——宁可不下发按钮，也不放过一个平台不认识的动作名）。
    """
    for t in ai_tools.TOOLS_SPEC:
        fn = t.get('function') or {}
        if fn.get('name') != 'propose_action':
            continue
        props = (fn.get('parameters') or {}).get('properties') or {}
        return list((props.get('action') or {}).get('enum') or [])
    return []


def _parse_next_actions(text):
    """从回答正文提取**最后一个** ```next_actions``` 块。

    返回 (actions, cleaned_text)：
      * 合法 → actions 为非空 list[dict]（最多 3 项），cleaned_text 是删掉
        该围栏块（含 ``` 标记）后的正文；
      * 不合法/解析失败/没有该块 → actions=[]，cleaned_text 原样返回
        （静默保留原文，不向用户暴露错误）。
    合法性：list、0<len<=3、每项 dict、action 属于 propose_action 的 enum、
    reason 非空。合法时保留模型给的字段；缺 label 时用 action 补默认值。
    """
    raw_text = str(text or '')
    if not raw_text:
        return [], raw_text
    matches = list(_NEXT_ACTIONS_RE.finditer(raw_text))
    if not matches:
        return [], raw_text
    m = matches[-1]                     # 只认最后一个块
    try:
        data = json.loads(m.group(1).strip())
    except (TypeError, ValueError):
        return [], raw_text
    if not isinstance(data, list) or not (0 < len(data) <= 3):
        return [], raw_text
    enum = _propose_action_enum()
    if not enum:
        return [], raw_text
    actions = []
    for item in data:
        if not isinstance(item, dict):
            return [], raw_text
        action = str(item.get('action') or '').strip()
        reason = str(item.get('reason') or '').strip()
        if action not in enum or not reason:
            return [], raw_text
        item = dict(item)
        if not str(item.get('label') or '').strip():
            item['label'] = action
        actions.append(item)
    cleaned = (raw_text[:m.start()] + raw_text[m.end():]).strip()
    return actions, cleaned


def _result_scale(result):
    """给工具事件算「结果规模」（2026-11-04 T6）：表 N 行 / 文件 N 字 / JSON N 键。"""
    try:
        data = json.loads(result) if isinstance(result, str) else result
    except (TypeError, ValueError):
        data = None
    if isinstance(data, dict):
        rows = data.get('rows')
        if isinstance(rows, list):
            return f'{len(rows)} 行'
        head = data.get('head')
        if isinstance(head, str):
            return f'{len(head)} 字'
        return f'{len(data)} 键'
    if isinstance(data, list):
        return f'{len(data)} 项'
    if isinstance(result, str):
        return f'{len(result)} 字'
    return '0'


def _tool_detail(name, args, result):
    """工具事件的一行明细（2026-11-04 T6，≤120 字符）。

    形如 ``read_result_table(sample=…, relpath=…) · 12 行``：前 2 个参数键值
    （每值截 40 字）+ 结果规模，让用户在「思考过程」里一眼看出这次调用
    到底读了什么、拿到多少，而不是只看到工具名。
    """
    pairs = []
    for k, v in list((args or {}).items())[:2]:
        s = re.sub(r'\s+', ' ', str(v))
        if len(s) > 40:
            s = s[:40] + '…'
        pairs.append(f'{k}={s}')
    detail = f"{name}({', '.join(pairs)}) · {_result_scale(result)}"
    return detail[:120]


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


def _system_prompt(sample, attachments=None, workspace=None, plan_mode='',
                   history_digest=''):
    from Virus_Platform_Core.config import get_config
    lang = get_config().lang
    try:
        ctx = build_context(sample)
        # 前情摘要（2026-11-04 T2）：被 _clip_history 裁掉的更早对话压成的
        # 摘要，让模型知道"前面聊过什么"，避免多轮后静默失忆。
        if history_digest:
            ctx['history_digest'] = history_digest
        if attachments:
            # 用户在本轮附加的文件（2026-10-30）：路径可直接用于 run_tool 等动作
            ctx['attachments'] = _attach_digest(attachments)
        ws = _workspace_digest(workspace)
        if ws:
            # 用户在总览右侧选的工作空间目录（2026-10-30）：列出内容，
            # 模型可以直接从这些文件路径起草 create_sample / run_tool
            ctx['workspace'] = ws
        # 输出目录（2026-11-02）：用户可能把输出根设到了别处（含对话坞里选的
        # 工作空间），模型必须知道结果落在哪，否则给出的路径会指错地方。
        try:
            ctx['output_root'] = _artifacts.output_root_brief()
        except Exception:                      # noqa: BLE001
            pass
        ctx['ui_artifacts'] = (
            '回答里直接给产物的**绝对路径**即可：对话坞会自动把它渲染成可点开的'
            '卡片（图片内联、HTML 可预览或新标签打开、表格给前几行）。'
            '不要只说「去结果中心点」，也不要用相对路径。')
        ctx_json = json.dumps(ctx, ensure_ascii=False)
    except Exception as e:                     # 上下文失败不拦对话
        ctx_json = json.dumps({'error': f'context unavailable: {e}'},
                              ensure_ascii=False)
    if len(ctx_json) > 60000:                  # 极端大样品：截断保底
        ctx_json = ctx_json[:60000] + '…(truncated)'
    tpl = _SYSTEM_RULES_ZH if lang == 'zh' else _SYSTEM_RULES_EN
    base = tpl.format(context=ctx_json, capabilities=_capability_map(),
                      lang_name='简体中文' if lang == 'zh' else 'English')
    # 任务分解口径（2026-11-03 用户定案「任务分解可选」）：随请求逐轮注入，
    # 不改全局模板（deepcopy 大模板代价高且容易漂移）。
    pm = str(plan_mode or '').strip().lower() or 'auto'
    if pm == 'off':
        base += ('\n\n16. **任务分解已由用户关闭**：不要提出 plan 多步计划'
                 '（该动作已从你的工具清单移除）——复杂请求也拆成**单步动作**'
                 '逐步起草，每步等用户批准后再谈下一步。')
    elif pm == 'always':
        base += ('\n\n16. **任务分解优先**：用户开启了「任务分解」——涉及 '
                 '2 个以上阶段的链路（如建样品→组装→鉴定→建树）一律用 '
                 'plan 多步计划一次提出（≤6 步，逐步执行）；单步请求仍直接'
                 '起草单个动作。')
    else:
        base += ('\n\n16. **任务分解自判（auto）**：简单请求直接起草单个动作，'
                 '不要拆计划；只有当请求确实涉及 ≥2 个有依赖的分析阶段链路时'
                 '才用 plan 多步计划。拿不准时先问用户一句「要不要拆成多步'
                 '计划」。')
    return base


# ===== 问答平台（2026-11-02）：与 AI 工作台彻底分开的第二条通道 ==========
# 用户定案：「悬浮机器人的功能独立是问答平台，不是 AI 工作台，不驱动任务
# 进行，全面分开」，并且「完全隔离：只答平台用法/概念」。
# 所以这条通道：
#   ① 不注入任何当前数据（样品 digest / 附件 / 工作空间 / 输出目录都不给）；
#   ② 不发 tools（模型物理上无法 propose_action / mcp_call）；
#   ③ 不产出产物卡片（没有工具就不会有新产物，避免把正文里的路径渲染成
#      「结果卡」误导用户以为问答平台在跑分析）。
# 三道锁都在服务端，前端改错也越不过去。
_QA_RULES_ZH = """你是「植物病毒分析平台」的**问答助手**，与「AI 工作台」是两个
完全独立的入口。你只负责答疑，不参与任何分析执行。

职责边界（用户明确定案，违反即错）：
1. 只回答**平台用法与概念**：某个页面/入口/按钮是干什么的、术语什么意思、
   操作步骤，执行单/任务/结果目录的机制，常见报错的排查方向，导出/提交的
   一般流程。
2. **不读取、不推断任何当前数据**：你不知道用户此刻选了哪个样品、任务跑到
   第几步、结果里检出了什么、有哪些文件。被问到这类问题时，直接说明「具体
   样品/任务数据请到 AI 工作台或结果中心查看，我这边只讲平台用法」——
   绝不猜测、绝不编造数字或文件名。
3. **不驱动任何任务**：你没有工具，不能起草执行单，不能启动/停止/重跑任何
   分析。用户让你「帮我跑一下」时，告诉他去 AI 工作台（首页分析台或顶栏
   「AI 工作台」按钮）起草执行单，由他本人批准。
4. 文档里没有的就说不确定，不要编造页面名、参数名或命令。

回答风格：中文，简短，分点；提到入口时写清页面名称与位置（例如「设置 →
数据库构建」「结果中心 → 表 → 列筛选」）；能一句话说清就别写三段。

下面是平台官方使用指南（仓库根的 AGENTS.md），作为你回答用法问题的依据：
---
{doc}
---"""

_QA_RULES_EN = """You are the **Q&A assistant** of VirusPlatform — a separate
entry point, completely independent of the "AI workbench". You answer
questions only; you never take part in any analysis.

Boundaries (fixed by the user; violating them is a bug):
1. Answer **how the platform works / what things mean** only: what a page,
   entry or button does, terminology, step-by-step usage, how execution
   orders / tasks / output directories work, where to start when something
   fails.
2. **Never read or infer current data**: you do not know which sample the
   user has selected, how far a task has progressed, what was detected, or
   which files exist. For such questions say: "concrete sample/task data
   lives in the AI workbench or the Results center — I only explain usage",
   and never guess numbers or file names.
3. **Never drive tasks**: you have no tools, cannot draft execution orders and
   cannot start/stop/rerun anything. If the user asks you to run something,
   point them to the AI workbench (home console or the "AI workbench" button
   in the top bar) where they can draft and approve it themselves.
4. If the docs do not cover it, say you are not sure. Never invent page
   names, parameter names or commands.

Style: short, bulleted, no filler. Name the page and its location.

Official usage guide (AGENTS.md at the repository root), your only source of
truth for usage questions:
---
{doc}
---"""

_QA_DOC_FALLBACK = """VirusPlatform（植物病毒分析平台）三个入口共用同一引擎：
网页 http://127.0.0.1:8765（首次启动自动开浏览器）、CLI `python main.py`、
MCP `python mcp_server.py`。
常用页面：工作台/（总览，含内嵌 AI 分析台）· 样品（/samples，建样品与样品详情）
· 工具箱（/tools，单步工具）· 任务中心（/tasks）· 结果中心（/results）
· 下载（/download）· NCBI 提交 · 系统发育 · 引物设计 · 设置（/settings，
含 AI 助手/数据库构建/环境自检）· 手册（/help）。
执行单机制：AI 只能起草，用户在对话坞点「批准执行」后才真正运行；
删除类/全局重置类动作永不进白名单。结果默认落在 run/results/<样品>/0X_*/，
工具箱单步产物在 run/tool_runs/<tool>_<ts>_<uuid>/；输出目录可在设置里改，
改后 results/tool_runs 等整体平移到新根下。
环境：Windows 10+，Python 3.12，外部工具在 3rd/，数据库在 databases/、host-db/。"""


def _doc_sections(text):
    """AGENTS.md → [(标题, 正文)]（按 '## ' 切分；无标题文档返回空表）。"""
    secs, cur_t, cur = [], '', []
    for line in str(text or '').splitlines():
        if line.startswith('## '):
            if cur_t and cur:
                secs.append((cur_t, '\n'.join(cur).strip()))
            cur_t, cur = line[3:].strip(), []
        elif cur_t:
            cur.append(line)
    if cur_t and cur:
        secs.append((cur_t, '\n'.join(cur).strip()))
    return secs


def _shingles(s):
    s = re.sub(r'\s+', '', str(s or ''))
    return {s[i:i + 2] for i in range(len(s) - 1)} if len(s) > 1 else set()


def _qa_pick_sections(doc, question, k=2, cap=6000):
    """两级注入的检索端（借鉴 shim 的 lazy SKILL.md）。

    系统提示只常驻【文档目录】（每节一行），正文按问题相关性取 top-k 节
    （中文 2-gram 重叠打分，标题+节首 800 字为样本），总量 cap 截断。
    章节不足 3 节（内置简版 fallback）→ 原样返回全文，行为与旧版一致。
    """
    secs = _doc_sections(doc)
    if len(secs) < 3:
        return doc
    qsh = _shingles(question)
    scored = []
    for i, (t, body) in enumerate(secs):
        sample = (t + ' ') * 3 + body[:800]
        score = len(qsh & _shingles(sample)) if qsh else 0
        scored.append((score, i, t, body))
    scored.sort(key=lambda x: (-x[0], x[1]))
    picked = [x for x in scored[:k] if x[0] > 0]
    if not picked:
        # 没有命中的节（问题太泛）：给目录 + 总览两节，模型至少有落脚点
        picked = scored[:2]
    out = ['【文档目录】']
    for t, body in secs:
        hint = re.sub(r'\s+', ' ', body[:60])
        out.append(f'- {t}：{hint}…')
    out.append('')
    out.append('【与本问题最相关的章节（其余见目录）】')
    used = 0
    for _s, _i, t, body in picked:
        out.append(f'## {t}')
        out.append(body)
        used += len(body) + len(t) + 4
        if used >= cap:
            break
    return '\n'.join(out)[:cap + 1200]


def _qa_doc():
    """平台用法文档：优先读仓库根 AGENTS.md，打包后缺失则用内置简版。"""
    cands = []
    try:
        root = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))))
        cands.append(os.path.join(root, 'AGENTS.md'))
    except (OSError, ValueError):           # noqa: BLE001
        pass
    cands.append(os.path.join(os.getcwd(), 'AGENTS.md'))
    for p in cands:
        try:
            with open(p, 'r', encoding='utf-8') as f:
                txt = f.read()
            if txt.strip():
                return txt[:12000]
        except OSError:
            continue
    return _QA_DOC_FALLBACK


def _qa_system_prompt(question=''):
    from Virus_Platform_Core.config import get_config
    lang = get_config().lang
    tpl = _QA_RULES_ZH if lang == 'zh' else _QA_RULES_EN
    doc = _qa_pick_sections(_qa_doc(), question) if question else _qa_doc()
    try:
        return tpl.format(doc=doc)
    except (KeyError, IndexError):
        return tpl


def run_chat(ai_cfg, messages, sample=None, attachments=None, workspace=None,
             mode='', plan_mode=''):
    """跑一轮对话，yield SSE 事件 dict（调用方逐个 `_sse()` 下发）。

    事件：delta（正文增量）/ tool（告知前端的工具活动）/ error（可读错误）/
    done（结束标记，附带本次是否降级过工具）。

    attachments：用户在本轮附加的本地文件绝对路径（2026-10-30）。只注入
    上下文（名字/大小/文本预览），不改变工具权限。

    mode：'' = AI 工作台（原有行为）；'qa' = 问答平台（2026-11-02）——
    不注入任何当前数据、不发 tools、不产出产物卡片。

    plan_mode（2026-11-03 用户定案「任务分解可选」）：'auto'（默认）=由模型
    按请求复杂度自判是否拆多步计划；'off' =从工具契约里**物理移除** plan
    动作（强制单步，不靠提示词自觉）；'always' =多阶段链路优先拆 plan。
    """
    """跑一轮对话，yield SSE 事件 dict（调用方逐个 `_sse()` 下发）。

    事件：delta（正文增量）/ tool（告知前端的工具活动）/ error（可读错误）/
    done（结束标记，附带本次是否降级过工具）。

    attachments：用户在本轮附加的本地文件绝对路径（2026-10-30）。只注入
    上下文（名字/大小/文本预览），不改变工具权限。

    mode：'' = AI 工作台（原有行为）；'qa' = 问答平台（2026-11-02）——
    不注入任何当前数据、不发 tools、不产出产物卡片。
    """
    qa = str(mode or '').strip().lower() == 'qa'
    if qa:
        # 两级注入（2026-11-03）：按本轮提问检索最相关的文档章节，不再
        # 每轮注入整份 AGENTS.md——固定成本从 O(全文) 降到 O(目录+2节)
        q = ''
        for _m in reversed(messages or []):
            if isinstance(_m, dict) and _m.get('role') == 'user':
                q = str(_m.get('content') or '')
                break
        sys_prompt = _qa_system_prompt(question=q)
        sample = attachments = workspace = None   # 三道锁之一：不给上下文
        history_digest = ''
    else:
        # 前情摘要（2026-11-04 T2）：被 _clip_history 裁掉的更早消息不再
        # 静默消失，压缩成 history_digest 注入系统提示（问答平台不注入）。
        history_digest = _history_digest(messages)
        sys_prompt = _system_prompt(sample, attachments, workspace,
                                    plan_mode=plan_mode,
                                    history_digest=history_digest)
    convo = [{'role': 'system', 'content': sys_prompt}]
    convo += _clip_history(messages)
    # 长对话静默丢记忆是"AI 变傻"的常见抱怨源：坞里存 30 条、服务端只带
    # 最近 MAX_HISTORY 条，用户以为它还记得开头。2026-11-04 起裁掉的更早
    # 消息会压成前情摘要（history_digest）带入，notice 同步改口径。
    dropped_n = max(0, len(messages or []) - MAX_HISTORY)
    if dropped_n > 0:
        if history_digest:
            msg = (f'更早的 {dropped_n} 条已压缩为前情摘要带入'
                   f'（最近 {MAX_HISTORY} 条保留原文）')
        else:
            msg = (f'更早的 {dropped_n} 条对话未带入本轮上下文'
                   f'（只带最近 {MAX_HISTORY} 条）')
        yield _sse({'type': 'notice', 'level': 'info', 'message': msg})
    if not convo or convo[-1]['role'] != 'user':
        yield _sse({'type': 'error',
                    'message': '消息为空，请先输入问题'})
        yield _sse({'type': 'done'})
        return

    degraded = False
    use_tools = not qa            # 问答平台（qa）物理上拿不到 tools
    call_sigs = []          # 本轮出现过的 (工具, 参数) 签名，用于拦重复轮询
    # 产物卡片（2026-11-02）：工具返回的 JSON 里出现的 html/图片/表格路径，
    # 以及正文里写出来的裸路径，都收进来，最后一次性下发给前端渲染成卡片。
    turn_paths = []
    # 本轮真正发给用户的正文。产物卡片要从**这一轮**的回答里认路径，
    # 不能从 convo 里倒着找：流式回复的末帧 message.content 是 None
    # （provider.py 只回 content=None），convo 里的 assistant 往往是空串，
    # 倒着找会拿到上一轮的回答。
    last_text = ''

    def _emit_artifacts():
        """把本轮真实落盘的产物整理成一个 artifacts 事件。

        必须在**每一条**结束路径上下发，而不是只在"工具轮次用完"那条兜底
        路径上——正常回答（模型直接给结论）才是最常见的收尾方式，漏掉它
        用户就永远看不到卡片。这里吞掉一切异常：卡片失败不能影响回答。
        """
        nonlocal last_text
        if qa:
            return      # 问答平台不产出「结果卡」（它本来就不跑任何东西）
        # next_actions（2026-11-04 T5）：last_text 已定稿，先把末尾的
        # ```next_actions``` 块解析并下发，再从正文里删掉它——保证产物
        # 卡片解析与入库正文都看不到裸 JSON。不合法时静默保留原文。
        if last_text:
            _acts, _clean = _parse_next_actions(last_text)
            if _acts:
                _old = last_text
                last_text = _clean
                # convo 里同一轮的 assistant 正文同步（非流式端点会带全文）
                for _m in reversed(convo):
                    if _m.get('role') == 'assistant':
                        _c = str(_m.get('content') or '')
                        if _old and _old in _c:
                            _m['content'] = _c.replace(_old, _clean, 1)
                        break
                yield _sse({'type': 'next_actions', 'actions': _acts})
        try:
            text = last_text
            if not text:
                for _m in reversed(convo):
                    if (_m.get('role') == 'assistant'
                            and str(_m.get('content') or '').strip()):
                        text = str(_m.get('content') or '')
                        break
            paths = list(turn_paths)
            paths.extend(_artifacts.from_text(text))
            cards = _artifacts.finish(paths)
        except Exception:                      # noqa: BLE001
            cards = []
        if cards:
            yield _sse({'type': 'artifacts', 'items': cards,
                        'summary': _artifacts.describe(cards)})

    # 生成长度预算：推理型模型（deepseek-reasoner/flash 等）会把预算先花在
    # reasoning_content 上，2048 常常只够"想一想"，正文为空或半截。这里给
    # 一个可自动加倍的预算游标，并在触顶时明确告知用户。上限 _BUDGET_MAX
    # 已提升到模块级（2026-11-04：16384），便于测试与统一调整。
    budget = int(ai_cfg.get('max_tokens') or 32768)
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
                        # 2026-11-06 P0：qa 模式必须**真的**不发 tools。
                        # 以前把 use_tools 当作 _plan_tools 的第一个参数
                        # （allow_plan）：_plan_tools(False) 只删 plan 动作、
                        # 仍返回 17 个工具（含 propose_action / mcp_call），
                        # 问答平台物理上仍能驱动任务。allow_plan=False 的
                        # 语义是"禁止多步计划"，不是"不给工具"，两件事必须分开。
                        tools=(_plan_tools(plan_mode != 'off')
                               if use_tools else None),
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
                            # 2026-11-06 P0：qa 模式（或上游拒绝 tools 后的降级
                            # 轮）即使模型自己吐出 tool_calls，也绝不进入
                            # ai_tools.dispatch——否则"不发 tools"只是不提示，
                            # 模型仍可通过幻觉/注入驱动任务。直接回一条中文
                            # 工具结果，让它改用文档知识作答。
                            if not use_tools:
                                yield _sse({'type': 'tool', 'name': name,
                                            'args': {}, 'skipped': True})
                                convo.append({
                                    'role': 'tool',
                                    'tool_call_id': tc.get('id') or '',
                                    'content': ('工具调用已被禁用：当前是问答平台'
                                                '模式（qa），不提供任何工具。'
                                                '请直接基于文档知识用中文回答，'
                                                '不要调用工具、不要起草执行单。')})
                                continue
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
                            # 工具事件带明细（2026-11-04 T6）：放在执行完成后，
                            # detail 才能带上真实结果规模（表 N 行 / 文件 N 字
                            # / JSON N 键），前端「思考过程」里一眼能看出这次
                            # 调用读了什么、拿到多少，总长 ≤120 字符。
                            yield _sse({'type': 'tool', 'name': name,
                                        'args': fargs,
                                        'detail': _tool_detail(name, fargs,
                                                               result)})
                            try:
                                turn_paths.extend(_artifacts.from_json_blob(result))
                            except (OSError, ValueError):      # noqa: BLE001 - 卡片是锦上添花
                                pass
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
                            if text:
                                last_text = text        # 非流式/末帧带正文
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
                if emit_parts:                 # 卡片从"用户实际看到的那段话"里认路径
                    last_text = ''.join(emit_parts)
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
                yield from _emit_artifacts()
                yield _sse({'type': 'done', 'tools_degraded': degraded,
                            'dsml_stripped': guard.stripped,
                            'truncated': finish == 'length',
                            'max_tokens': budget})
                return
            except AIConfigError as e:
                yield _sse({'type': 'error',
                            'message': f'{e}（请在 设置 → AI 助手 中配置）'})
                yield from _emit_artifacts()
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
                yield from _emit_artifacts()
                yield _sse({'type': 'done', 'tools_degraded': degraded})
                return
            except Exception as e:           # 兜底：不让生成器半途崩掉
                yield _sse({'type': 'error',
                            'message': f'{type(e).__name__}: {e}'})
                yield from _emit_artifacts()
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
        closing = []
        for ev in chat_stream(ai_cfg,
                              convo + [{'role': 'user', 'content': final_note}],
                              tools=None):
            if ev.get('type') == 'delta' and ev.get('content'):
                piece = guard.feed(ev['content'])
                if piece:
                    emitted = True
                    closing.append(piece)
                    yield _sse({'type': 'delta', 'content': piece})
        tail = guard.flush()
        if tail:
            emitted = True
            closing.append(tail)
            yield _sse({'type': 'delta', 'content': tail})
        if closing:
            last_text = ''.join(closing)
        if not emitted:
            yield _sse({'type': 'error',
                        'message': '工具调用轮次超限，且未能生成收尾总结；请换个更具体的问法重试'})
    except Exception as e:
        yield _sse({'type': 'error',
                    'message': f'工具调用轮次超限，收尾总结失败: {type(e).__name__}: {e}'})
    # 产物卡片（2026-11-02）：先收正文里的裸路径（模型最常这么写），再统一去重排序。
    # 只认真实存在、且在平台目录内的文件，所以卡片点开不会是 404。
    yield from _emit_artifacts()
    yield _sse({'type': 'done', 'tools_degraded': degraded})


def chat_ready():
    from Virus_Platform_Core.config import get_config
    return ai_ready(get_config().ai)
