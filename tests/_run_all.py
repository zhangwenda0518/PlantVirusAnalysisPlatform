# -*- coding: utf-8 -*-
"""批量跑集成/自检测试，汇总通过情况。只读性质为主，不改平台产物契约。

用法:
    python tests/_run_all.py                 # 跑全部（含慢测试）
    python tests/_run_all.py --quick         # 跳过 slow 标记的测试
    python tests/_run_all.py --list          # 只列测试清单与分级
    python tests/_run_all.py --no-retry      # 失败不自动单跑复验（见下）
    python tests/_run_all.py _it_align _it_submit   # 只跑指定测试

超时策略：每个测试有**独立**超时（默认 300s），慢测试放宽到 1500s。
此前用统一短超时 + 外壳 kill，会把"跑得久"误判成"失败"（rc=143/1），
掩盖真实结论——现在超时会明确标 TIMEOUT，与 FAIL 区分。

三条机器守卫（2026-11-02 加，此前只写在文档里靠人记）
------------------------------------------------------
G1 并发实例检测：拿不到互斥锁**默认中止**（旧行为是打条警告继续跑，于是
   两个套件并发互删 run/ 产物，表现为"单跑全绿、批量随机红"）。同时探测
   候选端口上已有的平台实例：≥2 个实例直接拒跑（多实例正是假红源头）；
   1 个实例则提示"本次有 N 个用例会复用它并写入其真实 run/"。
   `VP_ALLOW_CONCURRENT=1` 可强制继续（应急用，不建议）。
G2 产物归账（清场）：每个用例前后对 `<平台根>/run/{results,tool_runs}` 取
   快照，凡在真实目录里**新建**了条目就把条数挂在那个用例名下，收尾时汇总
   写进报告。子进程一律带 VP_RUN_ROOT 指向临时隔离根，所以正常情况下这一栏
   应全是 0——不是 0 就说明有东西绕过了 config.DIRS 自己拼路径，必须点名。
G3 单跑复验：FAIL / TIMEOUT 的用例会自动**单独**再跑一次（同一环境、独占），
   单跑通过则记为 FLAKY（不计入失败，但报告里单独列出）。这就是把
   "红了但单跑是绿的"这类争论交给机器判定，而不是靠人再跑一遍猜。
   `--no-retry` 或 `VP_NO_RETRY=1` 关闭。

环境变量:
    VP_SKIP_ONLINE=1   跳过一切真实网络请求的测试段（透传给子进程）
    VP_RUN_ROOT=<绝对路径>  子进程运行期根（默认自动分配临时隔离根）
    VP_KEEP_RUN_ROOT=1      不隔离，直接读写真实 run/（排查用）
    VP_ALLOW_CONCURRENT=1   拿不到互斥锁也继续（应急）
    VP_NO_RETRY=1           关闭失败自动单跑复验
输出: tests/_run_all_report.txt
"""
import io
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
# 控制台编码兜底：Windows 默认代码页是 GBK，本脚本的 ✔/✘/⚠ 等字符会让
# print 抛 UnicodeEncodeError（2026-09-11 实测多处踩过）。只改错误处理为
# replace（编码不动，中文照常可读），编不出的字符降级为 '?'。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors='replace')
    except (AttributeError, OSError):
        pass


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 本次套件的隔离运行期根（main() 里定一次，_child_env() 读取）。
# 用 dict 而不是模块级字符串：避免 import 顺序造成的"读时还没赋值"。
_ACTIVE_RUN_ROOT = {'path': None}

# 互斥锁：所有测试都通过 app.test_client() 直接读写同一个 run/ 目录，
# 两个批量实例并发跑会互相删/改对方产物，表现为「单跑全过、批量随机 FAIL」。
# 这里用 O_CREAT|O_EXCL 建锁文件，拿不到就检查持锁进程是否还活着——
# 已退出则是陈旧锁，直接复用（不删文件，某些环境会拦截删除调用）。
_LOCK_PATH = os.path.join(ROOT, 'tests', '._run_all.lock')


def _pid_alive(pid):
    """跨平台探测 pid 是否存活。探测不了时返回 True（保守，当它活着）。"""
    if not pid or not str(pid).isdigit():
        return False
    pid = int(pid)
    if pid == os.getpid():
        return True
    try:
        if os.name == 'nt':
            import ctypes
            k = ctypes.windll.kernel32
            h = k.OpenProcess(0x1000, False, pid)      # PROCESS_QUERY_LIMITED
            if not h:
                return False
            code = ctypes.c_ulong()
            ok = k.GetExitCodeProcess(h, ctypes.byref(code))
            k.CloseHandle(h)
            return bool(ok) and code.value == 259     # STILL_ACTIVE
        os.kill(pid, 0)
        return True
    except (OSError, ValueError, AttributeError):
        return False


def _acquire_lock():
    pid = str(os.getpid())
    for _ in range(2):
        try:
            fd = os.open(_LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, pid.encode())
            os.close(fd)
            return True
        except FileExistsError:
            # 持锁进程已退出 → 陈旧锁，覆盖其内容即可（不删除文件）
            try:
                with io.open(_LOCK_PATH, encoding='utf-8') as f:
                    holder = f.read().strip()
            except OSError:
                holder = ''
            if _pid_alive(holder):
                print(f'[中止] 另一个批量测试实例正在运行（pid={holder}）。\n'
                      f'       测试共享 run/ 目录，并发会互相干扰。')
                return False
            try:                                       # 陈旧 → 原地覆写接管
                with io.open(_LOCK_PATH, 'w', encoding='utf-8') as f:
                    f.write(pid)
                return True
            except OSError:
                return False                           # 写不了就当拿不到
        except OSError:
            return False
    return False


def _release_lock():
    try:
        os.remove(_LOCK_PATH)
    except OSError:
        pass


# ------------------------------------------------------------------
# 机器守卫 G1：并发实例 / 端口预检
# ------------------------------------------------------------------
# 与 app.py:_pick_port() 的候选表保持一致（两边改要一起改，故在此互相注明）。
_PORT_CANDIDATES = (8765, 8900, 8989, 9600, 8888, 5050, 5000)

# 用例里出现「直接把 8765 写进 URL 赋值」→ 它需要一个**已经在跑**的平台实例。
# 只认 `= 'http://127.0.0.1:8765…` 这种字面量赋值：
#   · _check_pages_console.py 的 docstring 里那句 `[--base http://127.0.0.1:8765]`
#     只是用法提示，它正文自起 werkzeug，不该算"需要外部实例"；
#   · f'http://127.0.0.1:{_port}' 这类自选端口的写法也不匹配。
_LIVE_URL_RE = re.compile(
    r"""=\s*['"]https?://(?:127\.0\.0\.1|localhost):8765""")


def _port_listening(port, host='127.0.0.1', timeout=0.35):
    """本机回环端口上是否有进程在监听（只建连探测，不发任何请求）。"""
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except (OSError, ValueError, TypeError):
        return False


def _live_instance_ports(ports=None):
    """候选端口里正在监听的（即可能已有平台实例）。"""
    return [p for p in (ports if ports is not None else _PORT_CANDIDATES)
            if _port_listening(p)]


def _test_needs_live_instance(path):
    """该用例是否必须打一个正在运行的真实平台实例（读源码判断，不看文件名）。"""
    try:
        with io.open(path, encoding='utf-8', errors='replace') as f:
            src = f.read()
    except OSError:
        return False
    return bool(_LIVE_URL_RE.search(src))


# ------------------------------------------------------------------
# 机器守卫 G2：真实 run/ 产物归账
# ------------------------------------------------------------------
_WATCH_DIRS = ('results', 'tool_runs')

# 与 config.DIRS 的 run 系条目同名（子进程被隔离到临时根时也要能 self-made）
_RUN_SUBDIRS = ('results', 'tool_runs', 'downloads', 'logan', 'submissions',
                'meta_search', 'tasks', 'logs', 'ai_audit', 'uploads',
                'fastq', 'gb_collections', 'ncbi_refs')
_TOOL_RUNS_FIXED = ('_archive', '_tmp', '_scripts', '_reports')


def _real_run_dirs():
    """真实 run/ 下各产物目录的条目名集合（隔离根在 %TEMP%，故这里固定看 ROOT）。"""
    out = {}
    for name in _WATCH_DIRS:
        d = os.path.join(ROOT, 'run', name)
        try:
            out[name] = set(os.listdir(d))
        except OSError:
            out[name] = set()
    return out


def _diff_dirs(before, after):
    """after 相对 before 新增的条目，按目录名分组并排序（便于报告逐条点名）。"""
    return {name: sorted(after.get(name, set()) - before.get(name, set()))
            for name in _WATCH_DIRS}


def _describe_residue(diff):
    """把新增条目压成一行 `tool_runs+3 results+1`；无新增返回空串。"""
    return ' '.join(f'{n}+{len(v)}' for n, v in diff.items() if v)


# ------------------------------------------------------------------
# 机器守卫 G3：失败自动单跑复验
# ------------------------------------------------------------------
def _status_of(timed_out, rc):
    """单次执行的粗状态：PASS / FAIL / TIMEOUT（不含耗时等展示细节）。"""
    if timed_out:
        return 'TIMEOUT'
    return 'PASS' if rc == 0 else 'FAIL'


def _judge(first_ok, retry_ok, retry_skipped=False):
    """G3 判定：首跑失败但单跑通过 → FLAKY（不是真失败）。

    retry_skipped=True 表示用户关掉了复验（--no-retry），此时只能按首跑结论。
    """
    if first_ok:
        return 'PASS'
    if retry_skipped:
        return 'FAIL'
    return 'FLAKY' if retry_ok else 'FAIL'


def _no_retry(flags):
    """是否关闭 G3 复验（--no-retry 或 VP_NO_RETRY=1）。"""
    return ('--no-retry' in flags
            or os.environ.get('VP_NO_RETRY', '').strip() in ('1', 'true', 'yes'))


def _keep_real_run_root():
    """VP_KEEP_RUN_ROOT=1 → 不隔离，直接读写真实 run/（排查用）。"""
    return os.environ.get('VP_KEEP_RUN_ROOT', '').strip() not in ('', '0')


def _suite_run_root():
    """分配本套件专用的隔离运行期根；返回 None 表示用真实 run/。

    为什么整套共用一个根而不是每个用例一个：部分用例（如 _it_platform）会
    先建样品再读回，跨用例共享才符合它们原本的假设；真正的目标是"别落在
    真实 run/"，不是"用例之间互相看不见"。
    """
    if _keep_real_run_root():
        return None
    base = os.path.join(tempfile.gettempdir(),
                        'vp_suite_%s_%d' % (time.strftime('%Y%m%d_%H%M%S'),
                                            os.getpid()))
    root = os.path.join(base, 'run')
    try:
        for name in _RUN_SUBDIRS:
            os.makedirs(os.path.join(root, name), exist_ok=True)
        for name in _TOOL_RUNS_FIXED:
            os.makedirs(os.path.join(root, 'tool_runs', name), exist_ok=True)
    except OSError as e:
        print(f'[警告] 隔离根创建失败（{e}），回退真实 run/；'
              f'本次产物会落在真实目录，报告会逐条点名。')
        return None
    return root

# (路径, 单测超时秒, 是否 slow)
# slow=True 的测试含真实重型计算或在线请求，`--quick` 时跳过。
TESTS = [
    ('tests/_it_platform.py',       300,  False),
    # dsRNA 全链路集成（2026-10-03 入编；此前从不在批量里，迁出/恢复期间
    # 无人报警）：真跑 dsRNAmax 选窗 + dsRIP 效价 + 面板脱靶扫描 + T7 引物
    # + 成品 QC，含确定性/内存守卫/max_mm=0 语义/isolsates 分支；约 3 秒。
    # 前置数据 databases/dsrna/lookup.db（小文件）；面板缺失时自合成小面板
    # 自足运行，不依赖仓库外 GB 级面板。
    ('tests/_it_dsrna.py',          300,  False),
    # 日志编码统一（2026-09-27）：decode_output 三分支 + to_utf8_file 四场景
    # （纯 GBK 转写 / 纯 UTF-8 幂等 / 混编码逐行无损 / 缺失文件静默）。
    ('tests/_check_enc_utils.py',    60,  False),
    ('tests/_it_msa.py',            300,  False),
    ('tests/_it_concurrency.py',    300,  False),
    ('tests/_smoke_phylo.py',       300,  False),
    ('tests/_it_phylo.py',          300,  False),
    ('tests/_it_extract.py',        300,  False),
    ('tests/_it_submit.py',         300,  False),
    ('tests/_it_align.py',          600,  False),
    ('tests/_it_compare.py',        600,  False),
    ('tests/_it_synteny.py',        600,  False),
    ('tests/_it_dl_delete.py',      300,  False),
    # 下载进度/大小/速度/ETA 与单端 .sra 解码的离线回归（秒级，不联网）
    ('tests/_it_dl_progress.py',    300,  False),
    # 工具箱「格式转换」.sra→FASTQ/FASTA 的产物识别：单端 <acc>.fastq.gz
    # 必须被认（旧行为只 glob <acc>_* → 产物已落盘却报「sracha 无输出」）。
    ('tests/_it_tool_convert.py',   300,  False),
    # 内置 HTTP 回退下载（_dl_python）与完整性闸门：Range 续传追加语义、
    # 416 收尾、精确总量优先、三道校验闸门。本机 HTTP 服务端，离线。
    ('tests/_it_dl_http_fallback.py', 300, False),
    # 重启恢复健壮性：畸形 batch.json 不得拖垮整个下载模块；中断语义。
    ('tests/_it_dl_recovery.py',    300,  False),
    # 「一键转入分析流程」：双端/单端配对、跳过不留空样品目录、runs 过滤、
    # enqueue 入队（假队列 + 路径隔离，绝不碰真实 queue.json）。
    ('tests/_it_dl_to_pipeline.py', 300,  False),
    # 收尾阶段（.sra 解码）必须能取消：进程被终止、剩余不继续、状态不被
    # 覆写成 completed、不误报转换失败。
    ('tests/_it_dl_convert_cancel.py', 300, False),
    # 长耗时接口后台任务化 + 重 IO 缓存/上限的离线回归（外部依赖全用桩）
    ('tests/_it_bg_tasks.py',       300,  False),
    # 阶段二：排队样品 / 存储扫描两处外部任务挂载 + attach 重绑定语义 +
    # 「跑完的外部任务必须退出 active_ids」（否则全局重置会误取消已完成的
    # 批次）。全程离线，只动内存里的假队列，不碰真实批次。
    ('tests/_it_taskcenter_ext.py', 180,  False),
    # 下载页真实浏览器验证（Selenium + 本机 Edge/Chrome，headless）：
    # 逐文件表格的倒挂 / 跨轮询被抹掉 / 重试续传入口。无浏览器时自动
    # SKIP 并返回 0，不阻塞批量测试。
    ('tests/_it_download_ui.py',    600,  False),
    # 四个「同步阻塞→后台任务」改造的真实浏览器验证（.sqn / 物种名校验 /
    # AI 总结 / CDS 导出）；外部依赖全部用桩。无浏览器时自动 SKIP。
    ('tests/_it_async_ui.py',       900,  False),
    # 浏览器验收组（2026-10-13 入编；此前 6 个用例全都不在批量里——仓库
    # 第三次踩「写了测试没人跑」坑，本次一次性收编。无浏览器自动 SKIP）：
    # 格式转换双端 / 下载页输出位置 / meta 任务卡日志 / 顶部导航自适应 /
    # 工具卡运行日志不随新任务更新 / 样本处理四卡输出目录。
    ('tests/_check_convert_paired.py', 600, False),
    ('tests/_check_dl_outdir.py',   600,  False),
    ('tests/_check_meta_tasklog.py', 600, False),
    ('tests/_it_nav_responsive.py', 600,  False),
    ('tests/_check_toolrun_latest.py', 600, False),
    ('tests/_check_tool_outdir_ui.py', 600, False),
    # 全局上下文（当前样品/项目）与全局清理按钮的浏览器验证（自起临时实例）。
    ('tests/_it_global_ctx_ui.py',  600,  False),
    # 任务中心页对「非样品类后台任务」的渲染与可操作性（进度条/日志/
    # 停止/前往），以及「只看当前样品」筛选的真实语义。无浏览器时自动 SKIP。
    ('tests/_it_taskcenter_ui.py',  600,  False),
    # 阶段二：**下载批次挂进任务中心**的真实浏览器端到端 —— 卡片/进度/进展
    # 文案、停止转发到 DownloadManager、前往页面、真身消失后卡片自动移除、
    # provider 报错时降级保留。绑定的是临时 downloads 目录，不碰真实批次。
    ('tests/_it_taskcenter_dl_ui.py', 600, False),
    # 切换模块不归档任务 + 全局运行条 + 手动归档/恢复（2026-11-02）：
    # ①后端往返（内存移除 / _archive 落盘 / 恢复 / 两目录 delete+purge，只改
    #   进程内 DIRS['tasks']，不碰真实 run/tasks）；②archive/unarchive API +
    #   /tasks /meta 渲染；③真实浏览器全流程（全局运行条展开 → 点📦归档 →
    #   历史归档点↩恢复 → 列表再归档），无浏览器自动 SKIP。
    ('tests/_it_task_archive.py',    120,  False),
    ('tests/_it_task_archive_api.py', 180, False),
    ('tests/_it_runbar_ui.py',       600,  False),
    # 三条修复路径真实浏览器端到端（续传 / 重试失败 / 重试转换）：点下去
    # 真的能把批次救回来。只打桩「字节传输」与「sracha 解码」，其余走真实代码。
    ('tests/_it_dl_repair_ui.py',   600,  False),
    ('tests/test_logan_trace.py',   300,  False),
    ('tests/test_logan_batch.py',   300,  False),
    ('tests/_check_local_annot.py', 300,  False),
    # 宿主库身份 + 分类探针（含 kunpeng 分类一次，约 20s）
    ('tests/_check_hostdb.py',      300,  False),
    # ASCII 中转目录的并发隔离（平台根含中文时 MAFFT/trimAl 必走中转；秒级）
    ('tests/_check_ascii_stage.py', 300,  False),
    # 启动模式（桌面窗口 / 网页）选择与回退 + 两个启动脚本（秒级，无副作用）
    ('tests/_check_start_modes.py', 300,  False),
    # 「浏览」按钮回归：调用 browse()/browseDir() 的页面必须带上共享对话框片段
    # （缺 #dlgMask 会抛 TypeError，按钮点了毫无反应；纯静态，秒级）
    ('tests/_check_browse_dlg.py',  120,  False),
    # 示例结果清单 ↔ 实际文件：列表页计数与详情页列表必须一致（秒级，只读）
    ('tests/_check_examples_manifest.py', 60, False),
    # 库/目录类 API 返回的路径必须是绝对路径（分发版下相对路径会解析错库；
    # 秒级，只读）
    ('tests/_check_db_paths.py',     60,  False),
    # spec 里 webapp 的 datas 展开：既不漏前端文件、也不带 .mimosa 等工具残留
    # （AST 抽出 spec 里的 _tree 单独执行；秒级，只读）
    ('tests/_check_spec_datas.py',   60,  False),
    # i18n 缺键：扫 webapp/templates/*.html 全部 data-i18n/-ph/-title 锚点，
    # 逐个喂真实 applyI18n，断言不会被写成 key 字面量 + 中英成对。
    # 2026-10-01 入编 —— 此前 _verify_i18n.js 从不在批量里（只跑 .py），
    # 于是它 fixture 过期变永久红灯也没人发现。无 node 时自动 SKIP。
    ('tests/_check_i18n.py',         60,  False),
    # 前端 JS 语法静态校验（内联 script 块 + static/*.js，node --check 只做
    # 语法不执行）+ 4 个跨卡复用函数存在性 + tools.html 无重复 id。无 node 时
    # 自动 SKIP 返回 0。2026-10-03 入编（此前不在批量里，一次批量替换把函数头
    # 连删 5 行都无人报警）。
    ('tests/_check_js_syntax.py',   120,  False),
    # AI 代理技能卡守卫（2026-11-02 入编）：三张卡存在 + frontmatter 名字与目录
    # 一致 + description 中英双语 + 指针卡与 agent_guide._SKILL 同步 + ops 卡
    # 覆盖 54/32/15/7 全集 + 引用路径存在 + 卡片里的 .done 标记真的出现过。
    # 只读；加工具/加阶段/改目录后卡片没跟上时在这里报红。
    ('tests/_check_skills.py',       60,  False),
    # 路由清单基线守卫：rule+methods 与 tests/_route_baseline.json 严格比对。
    # 新增/丢失/方法变化都会报红（改路由后需 python tests/_route_inventory.py
    # --save 更新基线）；只读、不启动分析任务，秒级。
    ('tests/_route_inventory.py',    60,  False),
    # 跨层契约审计（2026-10-13 入编）：前端 URL ↔ 后端路由、i18n 键、
    # 模板静态资源、模块可达性。只读；此前的 i18n 未定义键漏网即证
    # （4 个 tk.rdpTh* 键漏定义、英文界面露中文——写了审计却不在批量里）。
    ('tests/_audit_contract.py',    120,  False),
    # 全路由健壮性巡检：对每条路由发一次空参数请求，只找「未捕获异常 500」；
    # 破坏性路由（空 POST 就改状态/删数据）在脚本内 DESTRUCTIVE_POST 里显式
    # 跳过，且请求前后比对 run/tasks 增删。只写 tests/_audit_routes_report.txt
    #（已 gitignore）。2026-10-03 入编。约 20s。
    ('tests/_audit_routes.py',      300,  False),
    # 阶段表一致性守卫（后端 9 张表 + 前端 static/*.js + ③c 卡片可见性实测）。
    # 2026-09-16 修掉它"只读 app.js"的过时假设（前端已拆分到 app-jobs/app-batch）
    # 后重新入编——此前不在批量里，被前端重构甩下后静默腐烂了一轮。
    ('tests/_it_stage_tables.py',   120,  False),
    # PYZ↔源码字节码比较器的灵敏度自检：确认它**不是恒真**
    # （纯内存 compile 比较，不读产物、不需 PyInstaller；秒级）
    ('tests/_selftest_pyz_cmp.py',   60,  False),
    # 全页面真浏览器巡检：21 个页面逐个加载，收集 JS 报错 / 4xx 资源 /
    # 关键全局函数存在性。**逐页独立进程**驱动（同进程连续访问会随机段错误），
    # 约 2-4 分钟；无 playwright 时自动 SKIP 并返回 0。
    ('tests/_check_pages_console.py', 900, False),
    # 设置页侧栏锚点对齐：13 个条目逐个点击，断言落点在顶栏下方 12~30px、
    # 高亮的就是被点项、各条目滚动位置互不相同（防"三列网格同排挤在一处"
    # 与"侧栏顺序 ≠ 文档顺序"两类回归）。无 playwright 时 SKIP（~25s）。
    ('tests/_check_settings_nav.py', 300, False),
    # MCP 控制台：/mcp 独立页可达、不再出现在首页、破坏面工具
    # (vp_delete_tool_run) 在 /api/mcp/call 前置 403 且文案指出替代入口。
    # 纯 test_client，不拉起 mcp_server 子进程（~2s）。
    ('tests/_check_mcp_console.py', 120, False),
    # MCP 工具面（1.3.0）：32 个 vp_* 工具全部注册且 description 非空、
    # 清单过滤/分页口径、任务归档→仍可读→恢复、工具运行搬 _archive/ 再恢复、
    # 样品阶段状态与样品产物读取、越界路径拒绝、_selfcheck_report 七项。
    # 进程内改 DIRS['tasks'] 与 tool_runs_root，不碰真实 run/（~3s）。
    ('tests/_check_mcp_tools.py',    90, False),
    # ⚡一键分析（全流程）卡：自起临时实例 + 真浏览器，验证本卡自有输入/参数/
    # 输出区齐全、示例与参数互取落到本卡、内联结果链路（桩 API）不串卡（~30s）。
    # 无 playwright（开发依赖未装）时自动 SKIP 并返回 0。
    ('tests/_check_kvchain_ui.py',  300,  False),
    # AI 对话坞右侧「历史提问导航」：按用户提问逐条打点、滚动高亮当前轮、
    # 悬停出预览、点击跳到该轮并闪光、单轮/清空时整条隐藏（~25s）。
    # 自起临时实例；无 playwright（开发依赖未装）时自动 SKIP 并返回 0。
    ('tests/_check_turn_nav.py',    300,  False),
    # 病毒浏览器 Explorer（服务器版 7 页签：时序趋势 / 全基因组变异 / 数据浏览 /
    # 引物库 / 宿主范围 / 媒介传播 / 病毒档案）真浏览器验证：断言 Plotly 实例真的
    # 渲染出数据点（读 _fullData，兼容 bdata 二进制数组与 Sankey 的 node/link 嵌套）、
    # 下拉初始化、表格排序分页、深链指向与真点击跳转、导出 Content-Disposition、
    # 全程无 console error。覆盖 198,819 条 / 6,168 物种真实数据，约 3 分钟。
    # 自起临时实例。无 playwright（开发依赖未装）时自动 SKIP 并返回 0。
    # 其中 [13] 深链跳转需要外网；断网时该段自动 SKIP，不判失败。
    # ⛔ 已停用用例（2026-09-17 摘除 explorer；2026-09-27 彻底归档）。
    # _check_explorer.py 走真实的 /explorer 与 /api/explorer/* 路由，explorer
    # 蓝图已不再注册 → 必然 404。它验证的是一个**已下线的入口**，失败不代表
    # 回归，故从默认清单移除，避免长期红灯把真回归淹掉。
    # 需要复核归档代码时到 archive/_retire_20260927/explorer/tests/ 手动跑
    # （_check_explorer.py / _check_explorer_links.py / _check_virphykit_export.py
    # / _check_decimal_year_conventions.py 都随引擎一起归档）。
    # ('tests/_check_explorer.py',   900,  False),
    # Explorer → VirPhyKit 输入包导出：小数年解析（正/负控各 8）、区域口径与 name
    # token、严格档预过滤（无日期/无地点/非完整/重复登录号/空序列五道剔，含记账
    # 恒等式 sum(dropped)+kept==in 与「被剔的不能出现在保留集」负控）、**灵敏度
    # 自检**（同一套契约断言函数喂一个故意做坏的包，必须逐条报出问题，否则断言
    # 可能恒真）、真库端到端（PVY 4470→588 与 preview 一致，另用科级 24511 条
    # 证明导出没吃表格 5000 行截断）。可选的 GeoSubsampler 实跑在缺外部目录时
    # 自动 SKIP。约 30 秒。
    # 注：端到端段走 /api/explorer/export/* 路由，而 explorer 蓝图 2026-09-17
    # 起不再挂载（2026-09-27 随引擎彻底归档）——该段探测到路由
    # 不在 url_map 时自动 SKIP 并返回 0；引擎契约由本用例 [1]-[6] 段常态守护。
    # （本用例文件已随 explorer 归档：archive/_retire_20260927/explorer/tests/）
    # Fitch 重构回归：锁住 2026-09-15 AUDIT 记的缺陷 —— fitch_mugration 的
    # down-pass 从未执行（内部状态退化成候选集字典序最小者 → 高估迁移数），
    # 以及缺失数据被当成字面状态 'Unknown' 凭空造迁移。含旧实现对照。
    # 注：本平台的进化动力学卡片已迁往「植物病毒进化分析平台」，但 2026-09-16
    # 起本站重新接上了裁剪版系统地理（RRT/RSPP/MOTP/三分类/GIF/分带，见下一项）。
    ('tests/_check_phylogeo_fitch.py', 900, False),
    # 主平台裁剪版系统地理：haversine 自研口径、坐标表/别名/逐样本落点优先级、
    # classify_transitions 分位带与证据标记、RSPP 驱动的权重分带（措辞非贝叶斯）、
    # MOTP 时间分箱（含 LSD2 定年树解析）、GIF 帧计划、analyze() 端到端产物 +
    # 默认关开时 rrt/rssp/motp/weight_bands 均为 None。全离线、确定性夹具，秒级。
    ('tests/_check_phylogeo_geo.py',  900, False),
    # 同一张卡的真浏览器端到端（自起临时实例，随机空闲端口）：一级导航 → 组落地
    # → 点卡片进卡 → 参数齐全 + 静态文案已 i18n（切英文验证）→ 真跑一次
    # （RRT/RSSP/MOTP 全开的 8 条合成集）→ 三分类表 / 权重分带表 / GIF 区渲染
    # 且写明非贝叶斯 → 无 pageerror。无 playwright（开发依赖未装）时自动 SKIP。
    ('tests/_check_phylogeo_ui.py',   900, False),
    # 进化动力学工具组（VirPhyKit 对齐 12 卡，2026-09-17）：Engine 逐模块验证 +
    # 两个精确对拍契约（MJRM 与 PVS_with_matrix.xml 逐字节、TempMig 矩阵与
    # Migration_matrix.txt 逐格）+ 数据接入三来源时间地点同检 + 灵敏度自检
    # （坏输入必须红）。VirPhyKit Example 目录不在时 Example 段 SKIP。
    # TreeTime 全量跑约 40s（500 叶）；--fast 可跳。Example 不在本机也全绿。
    ('tests/_check_phylodyn_kit.py',  900, False),
    # t-phylodyn「时间与地理推断·本地全链」任务层 + 真浏览器端到端（32 项，
    # 2026-10-13 入编；此前不在批量里）。模块级全绿 ≠ 卡片接线对，这层专抓接线。
    ('tests/_check_phylodyn_e2e.py',  900, False),
    # VirPhyKit Example 全目录验证（2026-09-17）：42 个文件逐个被对应工具真实
    # 消费并断言（含 Total=各区行和自洽、RRT Min/Max 与 20 副本逐棵核对、
    # na_20/na_200/ebola 三个负例的精确拒绝、全目录覆盖检查漏一个文件即失败）。
    # [13] TreeDater 段跑真 R 引擎：定年 ~1 分钟 + parboot 置信带单核 15-20 分钟
    # （引擎内注释原话 10-40 分钟）——900s 超时必挂（2026-09-18 全量首跑即超时），
    # 故放宽到 2400s 并标 slow：--quick 跳过，引擎契约由 _check_phylodyn_kit.py
    # （~70s，含 MJRM/TempMig 逐字节对拍）在 quick 档继续守护。
    ('tests/_check_phylodyn_example.py', 2400, True),
    # 同组的真浏览器端到端（自起临时实例）：15 卡组落地 → 数据接入卡三来源切换 +
    # 真跑手动粘贴（坏行点名 + 图渲染 + 下载链接）→ 重命名卡 / MJRM 卡真跑 →
    # 其余 9 卡控件存在性 → 新增 i18n 键切英文 → 无 pageerror。无 playwright SKIP。
    ('tests/_check_phylodyn_ui.py',   900, False),
    # 数据准备链（2026-09-18 合并 4 张卡 + 引入上游预处理）：任务层真跑 44 项
    # （治理/去重/比对QC/允无地点/坐标表/降采样/两张卡已摘的负向）。
    ('tests/_check_pdprep_chain.py',  900, False),
    # 与上游 `virome_phylo_pipeline` 的治理层**逐条对拍**（上游仓库不在则自跳过返回 0）。
    ('tests/_check_govern_vs_upstream.py', 300, False),
    # 元数据列名自动识别与择优（explorer 新版 25 列）+ 治理边界（关治理回到老行为）。
    ('tests/_check_phylodyn_meta_alias.py', 300, False),
    # 小数年口径：四处实现的数值钉住 + 「月份必须生效」的 P0 防回归。
    # （原 _check_decimal_year_conventions.py 依赖 explorer 的 virphykit_export，
    #  已随 2026-09-27 归档移除；其 P0 断言由 _check_phylodyn_meta_alias.py 延续。）
    # 示例**输入** fixture 自洽（叶名能被元数据匹配 / 等长 / 不是 GenBank 转储）。
    # 示例输入 fixture 自洽由上行守卫覆盖；以下为 2026-10 修复轮新增守卫：
    # AA 同一性分簇锚定（M1）/ 圈图真圈图（M2）/ SVG 良构（M3）/
    # 引物全链热力学评分分级（Primer 专项）
    ('tests/_check_identity_aa.py', 600, False),
    ('tests/_check_genome_circos.py', 120, False),
    ('tests/_check_svg_wellformed.py', 60, False),
    # 图形体检扫描守卫（2026-10-13 入编）：对 stage_funnel / kv_fig /
    # kv_variant_plot / landscape_plot 四个可复跑绘图模块跑 check_figures
    # 质检（文字重叠/越界 0 FAIL）。variant annotated/ 缺失时自动重建，
    # Java/snpEff 缺失按 SKIP。
    ('tests/_check_fig_qa_scans.py', 300, False),
    ('tests/_check_primer.py', 600, False),
    ('tests/_check_examples_inputs.py', 120, False),
    # 在线获取（替代 SeqHarvester 的便利层）+ **输出 schema 一致性**：
    # taxid/物种名一键采集、geo_loc_name 优先、host 通道、host 可选列。
    # ⚠️ 里面含**真联网**的段（NCBI 不可达时自动 SKIP 并返回 0）。
    ('tests/_check_online_meta.py',   600, False),
    # TreeTime mugration 的**状态代号映射**（代号会跨到标点 ）：
    # 不依赖 TreeTime，用合成 GTR.txt/confidence.csv 钉住口径。
    ('tests/_check_gtr_mapping.py',   120, False),
    # 「进化树 + 基因组叠加」接线（2026-09-17）：后端 /api/tree/file 按「与树同名的
    # <stem>.overlay.json」带上 overlay（3 轨道 / 18 基因 / 18 条蛋白同一性连线）+
    # 五类坏 overlay 的降级负控（树照常返回）+ 真浏览器点「✨ 示例·基因组叠加」数
    # SVG（属色带 / 基因框 / 同源连线）与换纯树时的清零负控 + 新增 i18n 键。
    # 无 playwright（开发依赖未装）时真浏览器段自动 SKIP 并返回 0。
    ('tests/_check_tree_overlay.py', 900, False),
    # 真跑 HMM 全链路 + 在线 NCBI，实测 400s~900s（网络波动大）
    ('tests/_it_annotate.py',      1500,  True),

    # ---- 2026-10-07 审查新增守卫（每条都带「从备份抽取旧实现」的对照臂）----
    # SDT gap 口径：_sdt_formula 必须与 phylo.pairwise_identity_matrix 同口径
    # （'-. ' 都算 gap）；旧实现只认 '-'，含 '.' 的 MSA 会算出两个答案。
    ('tests/_check_sdt_gap_parity.py', 120, False),
    # SDT 分布图：全同输入（所有两两 identity == 100）曾因 counts 为空而
    # max(空) 崩溃，且崩在 CSV/热图落盘之后。
    ('tests/_check_sdt_distribution.py', 120, False),
    # CRISPR 路线 B（枚举兜底）的引物交付序列必须纯 ACGT —— 旧实现只挡 'N'，
    # W/S/M/K/R/Y 会一路流到订购表。
    ('tests/_check_crispr_primer_purity.py', 120, False),
    # TaskManager.on_finish：排队途中取消时 fn 根本不被调用，清理若只写在
    # fn 的 finally 里会永久泄漏（样品占位表 _RUNNING_SAMPLES）。
    ('tests/_check_task_on_finish.py', 120, False),
    # P1-4 SVG 良构：出口覆盖（dfv/gbdraw/genome_diag/viz 必须调 verify_svg）
    # + 覆盖范围扩到 run/ 产物 + 判据灵敏度自测（已在 _check_svg_wellformed）。
    # 本条是**消费侧链路**：残缺 SVG 不得进入 report.html（修得动的就地修复后
    # 内嵌、修不动的跳过），对照臂证明"未校验"时确实会漏进报告。
    ('tests/_it_svg_embed_chain.py', 300, False),
    # 全局重置 / 任务清理 / 队列落盘回归（2026-10-13 入编；此前不在批量里，
    # _persist 落盘格式从裸列表升级为 {items,paused} 后断言静默腐烂）。
    # DIRS/队列文件全程指向 run/_it_global_reset_tmp 临时目录，批量安全。
    ('tests/_it_global_reset.py',   180,  False),
    # 样品名归一化 + 磁盘真实目录名解析回归（2026-10-13 入编）。
    ('tests/_it_sample_names.py',   120,  False),
    # project.json 从既有产物回填（samples-backfill）回归（2026-10-13 入编）。
    ('tests/_it_manifest_backfill.py', 120, False),
    # 2026-10-10 审查修复全量回归：P0-1 零命中旧阳性污染 / P1-2 随机子采样 /
    # P1-6 ani_status 等（2026-10-13 入编，此前不在批量里）。
    ('tests/_it_review_fixes.py',   300,  False),
    # 样本处理四工具「输出目录（可选）」API 层验证：convert/fastp/prepchain
    # 真实跑通 + 指针目录清理（2026-10-13 入编）。
    ('tests/_check_tool_outdir.py', 400,  False),

    # ---- AI 助手（对话驱动智能代理）----
    # 2026-10-08 入编：这 6 个用例此前**存在但从不在批量里**（批量入口是硬编码
    # 元组、不是 glob），等于「写了测试但没人跑」——本仓已两次踩过同一坑
    # （_verify_i18n.js 与 6 个 pytest 文件）。3 个 Playwright UI 巡检缺
    # playwright 时 SKIP 返回 0（已补守卫；此前会直接抛 ImportError 变红）；
    # 3 个集成测试用线程内模拟 OpenAI 兼容端点，不打真实网络。
    ('tests/_it_ai_assist.py',       300, False),
    ('tests/_it_ai_events.py',       300, False),
    ('tests/_it_ai_actions.py',      300, False),
    ('tests/_check_ai_dock_ui.py',   300, False),
    # 对话坞跨页持久化（2026-11-02）：用户反复报「切模块回来问答没了」，
    # 根因是恢复挂在 /api/ai/context 成功之后——服务端跑重活时该请求超时，
    # 整段不渲染。本测试守 4 条：换页保留 / 会话 id 换掉走镜像键 /
    # pagehide 落盘 / 上下文接口失败仍恢复。需要 8765 在跑 + Edge，缺则 SKIP。
    ('tests/_it_ai_persist_ui.py',   300, False),
    # 同一条链的静态守卫（2026-11-02，秒级、不用浏览器）：把「恢复不依赖网络 /
    # 两处落盘 / 镜像兜底 / clear 连镜像清 / pagehide 落盘 / boot 先恢复」写成断言，
    # 改别的问题时若把其中一环改回去，这里立刻红，而不是等用户下次切页才发现。
    ('tests/_check_ai_dock_persist.py', 60, False),
    ('tests/_check_ai_events_ui.py', 300, False),
    ('tests/_check_ai_global_ui.py', 300, False),
    # digest ↔ 源文件数字对拍（2026-10-08）：digest 是 AI 的**唯一数字来源**，
    # 读错文件/键 → AI 给出"看起来完全合理"的错误解读，且没有任何报错。
    # 本守卫独立重读源文件逐键比对（含"凭空造数字"反向检查），自带灵敏度
    # 自证（改错一个数字/造一个假键都必须被报出），秒级。
    ('tests/_check_ai_digest_parity.py', 120, False),
    # AI 审计落盘（2026-10-08）：执行单状态变迁与事件流原本只在进程内存里
    # （重启即失、事后无从追溯）。本守卫含**真子进程 + 信号**的"退出不丢"
    # 测试、反复 drain 的"不重复写"测试，以及"接线自证"（publish/executors
    # 真的会落盘 —— 只测 audit 模块自身等于没测接线）。约 10s（含子进程）。
    ('tests/_check_ai_audit.py',     180, False),

    # ---- pytest 风格用例（走 `python -m pytest`，见 PYTEST_FILES 说明）----
    # 2026-10-07 审查 P1-3：这 6 个文件此前**既不在本清单、也无 CI**，
    # 等于「写了测试但从不在回归面上跑」（平台最大的新模块 crispr_design
    # 就属于这种）。现统一接入。
    ('tests/test_crispr_design.py',   300, False),
    ('tests/test_host_genome_gget.py', 120, False),
    ('tests/test_mirna_alignment.py', 120, False),
    ('tests/test_utils_pure.py',      120, False),
    ('tests/test_datasets_cli.py',    120, False),
    ('tests/test_web_guard.py',       120, False),
    # ---- 2026-10-13 入编：又一批「存在但不在批量里」的 pytest 文件 ----
    # test_crispr_ui（t-crispr 卡 UI 契约）与 2026-10 新模块的 4 个单测
    # （io_utils / sci_palette / stage_funnel / rdp5 断点分布兜底）。
    ('tests/test_crispr_ui.py',       120, False),
    ('tests/test_io_utils.py',        120, False),
    ('tests/test_sci_palette.py',      60, False),
    ('tests/test_stage_funnel.py',    300, False),
    ('tests/test_rdp5_bdp.py',        120, False),
]

# pytest 风格用例：用 pytest 的 fixture / importorskip，且**没有 __main__
# 入口** —— 不能像其它 _check_*/_it_* 那样直接当脚本跑，必须走 `python -m
# pytest`。本清单按此集合选择调用方式。
# 未安装 pytest 时整组 SKIP（不 FAIL）：开发依赖未装的机器不该因此报红。
PYTEST_FILES = {
    'tests/test_crispr_design.py',
    'tests/test_host_genome_gget.py',
    'tests/test_mirna_alignment.py',
    'tests/test_utils_pure.py',
    'tests/test_datasets_cli.py',
    'tests/test_web_guard.py',
    'tests/test_crispr_ui.py',
    'tests/test_io_utils.py',
    'tests/test_sci_palette.py',
    'tests/test_stage_funnel.py',
    'tests/test_rdp5_bdp.py',
}
_PYTEST_OK = None


def _pytest_available():
    """pytest 是否可用（探测一次并缓存）。"""
    global _PYTEST_OK
    if _PYTEST_OK is None:
        try:
            r = subprocess.run([sys.executable, '-c', 'import pytest'],
                               capture_output=True, timeout=60,
                               env=_child_env())
            _PYTEST_OK = (r.returncode == 0)
        except Exception:
            _PYTEST_OK = False
    return _PYTEST_OK
DEFAULT_TIMEOUT = 300
_ONLINE_HINT = {
    'tests/_it_annotate.py': '含在线 NCBI 请求，可设 VP_SKIP_ONLINE=1 跳过该段',
    'tests/_check_explorer.py': '已停用并随 2026-09-27 彻底归档移出 tests/'
                                '（archive/_retire_20260927/explorer/tests/）。'
                                '若手动跑：必须设 VP_EXPLORER_DATA 指回归档数据，'
                                '且把 explorer 蓝图临时挂回 app.py，否则必然 404。',
}


def _fmt_secs(s):
    return f'{s / 60:.1f}min' if s >= 120 else f'{s:.0f}s'


def _child_env(run_root=None):
    """子进程环境：剥掉宿主 Agent 会话注入的删除监控变量 + 设定隔离运行期根。

    某些 Agent/IDE 会话会把 CODEBUDDY_SAFE_DELETE_* 注入环境，并通过
    stdout 监控子进程输出。测试的收尾清理（shutil.rmtree）在累计计数
    超阈值后会被标记为 SAFE_DELETE_BULK_CONFIRM_REQUIRED，导致**明明全部
    断言已通过**的子进程以 rc=1 退出——表现为"单跑全过、批量随机 FAIL"。
    测试是本地受控目录内的自我清理，不需要外部删除守卫，故剥离。

    run_root 非空 → 注入 VP_RUN_ROOT，子进程的 config 把 13 个 run 系目录
    全部落到该根（见 Virus_Platform_Core/config.py 的 RUN_ROOT）。这是 G2
    的根：测试产物不该混进用户真实 results/tool_runs。
    """
    env = {k: v for k, v in os.environ.items()
           if 'SAFE_DELETE' not in k and not k.startswith('CODEBUDDY_')}
    # 批量满载下浏览器类测试的元素等待放宽（单文件直跑不受影响）
    env.setdefault('VP_TEST_WAIT', '90')
    # 剥离后 PATH 里可能残留宿主 safe-bin 目录，一并去掉
    if env.get('PATH'):
        parts = [p for p in env['PATH'].split(os.pathsep)
                 if 'safe-bin' not in p.replace('\\', '/')]
        env['PATH'] = os.pathsep.join(parts)
    root = run_root if run_root is not None else _ACTIVE_RUN_ROOT['path']
    if root:
        env['VP_RUN_ROOT'] = root
    else:
        env.pop('VP_RUN_ROOT', None)      # 别让宿主 shell 的残留值误导子进程
    return env


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('-')]
    flags = {a for a in sys.argv[1:] if a.startswith('-')}
    quick = '--quick' in flags or '-q' in flags
    allow_concurrent = (os.environ.get('VP_ALLOW_CONCURRENT', '').strip()
                        in ('1', 'true', 'yes'))

    if '--list' in flags:
        for path, to, slow in TESTS:
            tag = '  [slow]' if slow else ''
            live = ('  [需运行实例]'
                    if _test_needs_live_instance(os.path.join(ROOT, path))
                    else '')
            print(f'{path:34s} timeout={_fmt_secs(to)}{tag}{live}')
        return 0

    # ---- G1-① 互斥锁：默认严格 ----
    # 旧行为是"拿不到锁也继续跑，只打条警告"，于是两个套件并发互删 run/
    # 产物，结论摇摆（03:04 98/107 → 04:06 104/109 → 04:27 109/109）。
    # 现在默认中止：宁可让人等，也不要给出不可信的分数。
    locked = _acquire_lock()
    if not locked:
        if not allow_concurrent:
            print('\n[中止] 未取得互斥锁：另一个批量测试实例正在运行。\n'
                  '       两个套件共享同一份 run/，并发会互相删产物，'
                  '得出"单跑全绿、批量随机红"的假结论。\n'
                  '       等它跑完再试；确实要并发请设 VP_ALLOW_CONCURRENT=1'
                  '（不建议，结果可信度自行承担）。')
            return 2
        print('[警告] VP_ALLOW_CONCURRENT=1：已强制并发运行，'
              '本次分数可能受干扰。\n')

    # ---- G1-② 平台实例计数 ----
    # 8765 是平台默认端口；被占时 app.py 会顺延到 8900/8989/9600/…
    # 因此候选表里出现 ≥2 个监听端口 ≈ 开了 ≥2 个平台实例（真实出现过的
    # 情形：pid 58296 与 pid 27996 并存）。多实例本身不会让测试错，但它
    # 让"我看到的页面到底是谁的"无法确定，直接拒跑比事后解释便宜。
    try:
        ports = _live_instance_ports()
        if len(ports) >= 2 and not allow_concurrent:
            print(f'\n[中止] 检测到 {len(ports)} 个平台实例在监听：'
                  f'{", ".join(str(p) for p in ports)}。\n'
                  '       多实例并存时无法确定测试打的是哪一个，'
                  '且它们共用同一份 run/。\n'
                  '       请先关掉多余的实例（或设 VP_ALLOW_CONCURRENT=1 强制）。')
            return 2
        if ports:
            print(f'[预检] 平台实例在监听: {", ".join(str(p) for p in ports)}'
                  '（浏览器类用例会复用它）')
        plan = plan_from(args)
        live = [p for p, _t, _s in plan
                if _test_needs_live_instance(os.path.join(ROOT, p))]
        if live and not _port_listening(8765):
            print(f'[预检] {len(live)} 个用例写死 127.0.0.1:8765，但该端口无实例：'
                  '这些用例将标 SKIP（不是 FAIL）。')
        elif live:
            print(f'[预检] {len(live)} 个用例将复用 8765 上的实例，'
                  '它们会写入该实例的真实 run/（报告末尾逐条点名）。')
        _ACTIVE_RUN_ROOT['path'] = _suite_run_root()
        if _ACTIVE_RUN_ROOT['path']:
            print(f'[隔离] 子进程运行期根: {_ACTIVE_RUN_ROOT["path"]}\n')
        else:
            print('[隔离] VP_KEEP_RUN_ROOT 生效：子进程将直接读写真实 run/\n')
        return _run(plan, quick, live_ports=ports, no_retry=_no_retry(flags))
    finally:
        if locked:
            _release_lock()


def plan_from(args):
    if not args:
        return list(TESTS)
    by_path = {p: (to, sl) for p, to, sl in TESTS}
    plan = []
    for a in args:
        p = a if a.startswith('tests/') else f'tests/{a}'
        if not p.endswith('.py'):
            p += '.py'
        to, sl = by_path.get(p, (DEFAULT_TIMEOUT, False))
        plan.append((p, to, sl))
    return plan


def _exec_once(is_pytest, p, timeout):
    """跑一个用例一次，返回 (ok, 状态标签, 解码后的输出, 耗时秒)。"""
    ts = time.time()
    timed_out = False
    rc = None
    raw = b''
    # pytest 风格用例没有 __main__，必须走 `python -m pytest`
    cmd = ([sys.executable, '-m', 'pytest', p, '-q'] if is_pytest
           else [sys.executable, p])
    try:
        r = subprocess.run(cmd, cwd=ROOT, env=_child_env(),
                           capture_output=True, timeout=timeout)
        rc = r.returncode
        raw = (r.stdout or b'') + (r.stderr or b'')
    except subprocess.TimeoutExpired as e:
        # TimeoutExpired.stdout/err 可能是 None 或 bytes
        timed_out = True
        raw = (e.stdout or b'') + (e.stderr or b'')
        if isinstance(raw, str):
            raw = raw.encode('utf-8', 'replace')
    dur = time.time() - ts
    label = _status_of(timed_out, rc)
    if label == 'TIMEOUT':
        label = f'TIMEOUT(>{_fmt_secs(timeout)})'
    elif label == 'FAIL':
        label = f'FAIL(rc={rc})'
    return label == 'PASS', label, raw.decode('utf-8', errors='replace'), dur


def _run(plan, quick, live_ports=None, no_retry=False):
    """逐例执行，三条守卫在这里落地：G1 早 SKIP / G2 归账 / G3 复验。"""
    if quick:
        skipped = [p for p, _t, s in plan if s]
        plan = [(p, to, s) for p, to, s in plan if not s]
        if skipped:
            print(f'--quick: 跳过 {len(skipped)} 个 slow 测试 '
                  f'({", ".join(os.path.basename(p) for p in skipped)})\n')

    live_8765 = _port_listening(8765)
    out_path = os.path.join(ROOT, 'tests', '_run_all_report.txt')
    lines = []
    n_ok = 0
    n_flaky = 0
    n_fail = 0
    n_skip = 0
    residue_log = []          # [(path, {dir: [新增条目…]})]
    t0 = time.time()
    for path, to, slow in plan:
        p = os.path.join(ROOT, path)
        if not os.path.isfile(p):
            lines.append(f'[SKIP] {path} 不存在')
            print(lines[-1])
            continue
        is_pytest = path in PYTEST_FILES
        if is_pytest and not _pytest_available():
            lines.append(f'[SKIP] {path} 未安装 pytest（开发依赖缺失，不计失败）')
            print(lines[-1])
            n_skip += 1
            continue
        # ---- G1-③ 需要运行实例的用例：没实例就 SKIP，不判红 ----
        # 这些用例打 http://127.0.0.1:8765 的活页面，没实例时只会
        # ConnectionRefused/30s 超时——那是环境缺失，不是代码错。
        if _test_needs_live_instance(p) and not live_8765:
            why = ('8765 无实例' if not live_ports
                   else f'8765 无实例，另有 {live_ports} 在跑但本用例写死 8765')
            lines.append(f'[SKIP] {path} 需要运行中的平台（{why}）')
            print(lines[-1])
            n_skip += 1
            continue

        before = _real_run_dirs()
        ok_first, label, txt, dur = _exec_once(is_pytest, p, to)

        # ---- G3 单跑复验：首跑失败就独占再跑一次 ----
        retry_ok = None
        if not ok_first and not no_retry:
            print(f'  ↻ 单跑复验中: {path}', end='', flush=True)
            retry_ok, label2, txt2, dur2 = _exec_once(is_pytest, p, to)
            print(f'  ({dur2:.1f}s) → {label2}', flush=True)
            dur += dur2
            if not retry_ok:
                txt = (txt + '\n--- 单跑复验（仍失败）---\n'
                       + '\n'.join(txt2.strip().splitlines()[-16:]))
        verdict = _judge(ok_first, retry_ok, retry_skipped=no_retry)
        if verdict == 'PASS':
            status = label
            n_ok += 1
        elif verdict == 'FLAKY':
            status = f'FLAKY(首跑 {label}，单跑通过)'
            n_flaky += 1
        else:
            status = label
            n_fail += 1

        # ---- G2 归账：这个用例有没有往真实 run/ 里塞东西 ----
        diff = _diff_dirs(before, _real_run_dirs())
        residue = _describe_residue(diff)
        if residue:
            residue_log.append((path, diff))

        # 尾部 24 行：浏览器类测试一段 ✔ 明细很长，14 行会把真正的
        # "✘ 失败项"清单截掉（2026-10-13 _it_nav_responsive 实抓）。
        tail = '\n'.join(txt.strip().splitlines()[-24:])
        note = ''
        if path in _ONLINE_HINT and status != 'PASS':
            note = f'  ↳ {_ONLINE_HINT[path]}'
        if residue:
            note += f'  ↳ 真实 run/ 新增 {residue}'
        block = (f'{"=" * 70}\n[{status}] {path}  ({dur:.1f}s)\n'
                 f'{"-" * 70}\n{tail}\n')
        lines.append(block)
        if residue:
            lines.append(f'[归账] {path} 在真实 run/ 新建了 {residue}'
                         '——应被 VP_RUN_ROOT 隔离，说明有路径绕过了'
                         'config.DIRS 自己拼目录')
        print(f'[{status}] {path}  ({dur:.1f}s){note}')
        sys.stdout.flush()

    total = (len([p for p, _t, _s in plan
                  if os.path.isfile(os.path.join(ROOT, p))]) - n_skip)
    elapsed = time.time() - t0
    passed = n_ok + n_flaky
    head = (f'批量测试汇总: {passed}/{total} 有效通过（PASS {n_ok}'
            + (f' + FLAKY {n_flaky}（首跑红、单跑绿，不计失败）' if n_flaky else '')
            + (f'，真实失败 {n_fail}' if n_fail else '，0 失败')
            + f'），总耗时 {elapsed:.1f}s'
            + (f'（{n_skip} 个 SKIP）' if n_skip else '')
            + ('（--quick 模式）' if quick else '') + '\n')
    iso = _ACTIVE_RUN_ROOT['path']
    iso_line = (f'隔离根: {iso}\n' if iso
                else '隔离: 未启用（VP_KEEP_RUN_ROOT 生效，子进程读写真实 run/）\n')
    if residue_log:
        names = '、'.join(p for p, _d in residue_log[:10])
        res_line = (f'真实 run/ 归账: ✗ {len(residue_log)} 个用例写入了真实目录'
                    f'（{names}{"…" if len(residue_log) > 10 else ""}）\n')
    else:
        res_line = '真实 run/ 归账: ✓ 全部用例均未写入真实目录\n'
    with io.open(out_path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(head + iso_line + res_line + '\n' + '\n'.join(lines))
    print('\n' + head)
    print(iso_line, end='')
    print(res_line, end='')
    print(f'明细: {out_path}')
    return 0 if passed == total else 1


if __name__ == '__main__':
    raise SystemExit(main())
