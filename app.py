# -*- coding: utf-8 -*-
"""植物病毒分析平台 - 本地 Web GUI（组合根）。

启动（两种模式，可显式选）:
    python app.py            或 双击 启动平台-桌面窗口.bat → 默认独立桌面窗口
    python app.py --gui      或 双击 启动平台-桌面窗口.bat → 强制桌面窗口
    python app.py --web      或 双击 启动平台-网页.bat     → 强制系统浏览器
  桌面窗口由 pywebview(WebView2) 壳住本地页面，无地址栏，观感即本地应用；
  未安装 pywebview / 缺 WebView2 / 窗口启动失败时**自动回退**浏览器。
  兼容旧写法：环境变量 VP_GUI=browser|web 等价于 --web，VP_GUI=gui 等价于 --gui；
  命令行参数优先于环境变量。打包版同理：VirusPlatform.exe --web 即浏览器模式。
  仅监听本机回环地址；长任务在后台线程执行，页面实时轮询进度。
  所有文件写入经由 utils.safe_open（路径校验+限平台内），os.startfile 前一律 check_path。

本文件只做「组装」——创建 Flask 应用、注册 blueprint、启动服务。
业务逻辑分两层：
  - Virus_Platform_Core/        计算与流程（pipeline / kunpeng / phylo / orf_annot …）
  - Virus_Platform_Core/web/    HTTP 边界与任务工厂（各 blueprint）
**请勿在 app.py 新增路由**；新路由写进 Virus_Platform_Core/web/ 对应模块，在此登记 blueprint。
"""
import os
import sys
import threading
import time
import webbrowser
# Windows 注册表把 .svg 关联成 MIME 类型 "image/svg"，而标准类型是
# "image/svg+xml"；Python 的 mimetypes 会照搬注册表，Flask 便以 image/svg
# 发出去，Chromium 直接拒绝解码（<img> 拿到 naturalWidth=0 的裂图）。
# 这里在造 Flask app 之前显式注册，全站 SVG 才能当图片显示
# （首页封面主视觉、⑨ 基因组图、示例结果都用 <img> 引 SVG）。
import mimetypes
mimetypes.add_type('image/svg+xml', '.svg')

from flask import Flask, abort, request
from flask.json.provider import DefaultJSONProvider

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

# 冻结分发下 multiprocessing（SDT 精确矩阵的 ProcessPoolExecutor 等）子进程
# 会重新启动 exe，必须在任何业务代码前调用 freeze_support 拦截子进程引导；
# 非冻结环境是空操作
if __name__ == '__main__':
    import multiprocessing
    multiprocessing.freeze_support()

# 冻结分发：primer3-py 的 pyd 在非 ASCII 安装路径下构造 ThermoAnalysis 会
# 直接段错误（同 sitecustomize 的镜像机制口径：源码模式把包镜像到
# %ALLUSERSPROFILE%\VirusPlatformPrimer3）。程序包自带 primer3_pkg/primer3
# 副本，启动时镜像到纯 ASCII 的用户公共目录并插入 sys.path——必须在任何
# 业务模块可能触发 primer3 导入之前完成。镜像失败仅影响引物热力学功能。
# ⚠️ 位置必须在下方 --run-engine / --cli 派发之前：--run-engine 跑 dsRNA T7
# 引物设计、--cli importprobe 子进程探测 primer3，两者都依赖镜像路径已入 sys.path。
if getattr(sys, 'frozen', False):
    try:
        _p3_src = os.path.join(os.path.dirname(sys.executable),
                               'primer3_pkg', 'primer3')
        if os.path.isdir(_p3_src):
            import shutil as _sh
            _p3_base = os.path.join(os.environ.get('ALLUSERSPROFILE',
                                                   r'C:\ProgramData'),
                                    'VirusPlatformPrimer3App')
            _p3_dst = os.path.join(_p3_base, 'primer3')
            _ver_src = os.path.join(os.path.dirname(_p3_src), 'VERSION.txt')
            _ver_dst = os.path.join(_p3_base, 'VERSION.txt')
            _need = not os.path.isdir(_p3_dst)
            # 旧版程序包漏带 primer3_config（热力学参数表）时镜像会完整但
            # 段错误 —— 检测到配置目录缺失也强制重镜像（自愈升级路径）
            if not _need and not os.path.isdir(
                    os.path.join(_p3_dst, 'src', 'libprimer3',
                                 'primer3_config')):
                _need = True
            try:
                if os.path.isfile(_ver_src) and os.path.isfile(_ver_dst) and \
                   open(_ver_src, 'rb').read() != open(_ver_dst, 'rb').read():
                    _need = True
            except OSError:
                pass
            if _need:
                _sh.rmtree(_p3_dst, ignore_errors=True)
                os.makedirs(_p3_base, exist_ok=True)
                _sh.copytree(_p3_src, _p3_dst)
                if os.path.isfile(_ver_src):
                    _sh.copyfile(_ver_src, _ver_dst)
            if _p3_base not in sys.path:
                sys.path.insert(0, _p3_base)
    except Exception:
        pass  # 镜像失败仅引物热力学功能降级，平台其余功能不受影响

# 冻结分发：`VirusPlatform.exe --run-engine <脚本名> [args...]` → 进程内执行引擎
if getattr(sys, 'frozen', False) and len(sys.argv) > 2 and sys.argv[1] == '--run-engine':
    from Virus_Platform_Core.engine_entry import run_engine
    raise SystemExit(run_engine(sys.argv[2]))

# 冻结分发：`VirusPlatform.exe --cli <子命令> [args...]` → 进程内跑 CLI
# （打包后没有 python，无法 `python main.py selfcheck`；这条给打包验证和
#  用户自检用，例如 `VirusPlatform.exe --cli selfcheck`）
if getattr(sys, 'frozen', False) and len(sys.argv) > 2 and sys.argv[1] == '--cli':
    import main as _cli
    sys.argv = ['main.py'] + sys.argv[2:]
    _cli.main()
    raise SystemExit(0)

# MCP 控制台的服务器入口：`VirusPlatform.exe --mcp-stdio`（或源码模式
# `python app.py --mcp-stdio`）→ 进程内跑 mcp_server 的 stdio 服务。
# 冻结分发没有独立 python 可执行脚本，mcp_console._ensure 在 frozen 下
# 就是用本入口拉起（mcp_server 模块与 mcp SDK 已随 PyInstaller 入包）。
# 必须在 Flask / 网页启动之前接管进程：stdout 是 MCP 协议通道，本分支
# 之后不允许再有任何非协议输出。
if len(sys.argv) > 1 and sys.argv[1] == '--mcp-stdio':
    import mcp_server as _mcp_srv
    _mcp_srv.mcp.run()
    raise SystemExit(0)

# webapp 目录 / 配置单例 / tool_runs 根统一由 Virus_Platform_Core/web/state.py 提供，
# 各 blueprint 也从那里取，避免 app ↔ blueprint 循环导入。
from Virus_Platform_Core.web.state import _WWW  # noqa: E402

def _pause_before_exit():
    """退出前暂停（有控制台时等回车）；无 stdin 的场景（pythonw/计划任务/
    测试子进程）input 会抛 EOFError 直接崩栈——吞掉，安静退出。"""
    try:
        input('按回车键退出...')
    except (EOFError, OSError):
        pass


app = Flask(__name__, template_folder=os.path.join(_WWW, 'templates'),
            static_folder=os.path.join(_WWW, 'static'))
app.config['JSON_AS_ASCII'] = False
# 上传请求体硬上限（16GB）：此前未设，submit/meta 等上传点在请求线程整表
# 读进内存，误传超大文件会把本机内存/磁盘打满（2026-10-04 审查 E4）。
# 常规 FASTQ/FASTA（宿主基因组 ~2GB）覆盖足够；更大文件走「挂载数据目录」。
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 ** 3
# 浏览器固定请求站点根的 /favicon.ico；不挂路由的话每次页面加载都有一条
# 404 噪音（真身在 static/brand/favicon.ico）
@app.route('/favicon.ico')
def _favicon():
    from flask import send_file
    return send_file(os.path.join(_WWW, 'static', 'brand', 'favicon.ico'),
                     mimetype='image/x-icon')
# 模板热重载：debug=False 下 Jinja2 默认缓存模板，改 .html 后需重启才生效；
# 打开后每次请求按 mtime 检测，本地平台开销可忽略，改模板即刷新可见。
app.config['TEMPLATES_AUTO_RELOAD'] = True

# NaN/±Inf 清洗的唯一实现（原随病毒浏览器归档，现收编进 utils.py）；
# 非 JSON 出口（/tool_runs 原样发文件）的落盘清洗见 web/tool_jobs._dump_json。
from Virus_Platform_Core.utils import nan_to_null as _nan_to_null  # noqa: E402


class _CleanJSONProvider(DefaultJSONProvider):
    """全局 JSON 出口：清洗 NaN/±Inf 后再序列化（平台 JSON 铁律）。

    Flask 默认放行裸 `NaN`（`allow_nan=True`）——那是**非法 JSON**：
    Python 端 json.loads 能容忍（本地自测假通过），浏览器 JSON.parse 直接
    报错、整份响应被丢弃。来源多为空集合求均值/占比（pandas 聚合）。
    这里统一把 NaN/±Inf 换成 null，并以 `allow_nan=False` 兜底：若还有
    漏网之鱼会带类型报错，而不是静默产出非法 JSON。
    """

    ensure_ascii = False

    def default(self, o):
        try:
            return super().default(o)
        except TypeError:
            if hasattr(o, 'tolist'):        # numpy 数组
                return o.tolist()
            if hasattr(o, 'item'):          # numpy 标量
                return o.item()
            raise

    def dumps(self, obj, **kwargs):
        kwargs.setdefault('allow_nan', False)
        return super().dumps(_nan_to_null(obj), **kwargs)


app.json = _CleanJSONProvider(app)


# ------------------------------------------------------------------
# 本地 CSRF / DNS-rebinding 防护
# 服务只监听 127.0.0.1，但这挡不住两类远程攻击：
#   1) CSRF：用户浏览器里的任意恶意网页可静默 POST http://127.0.0.1:<port>
#      （删样品、全局重置、改设置…），响应虽不可读，破坏已经生效；
#   2) DNS rebinding：攻击者域名解析到 127.0.0.1 后，同源策略即被绕过，
#      恶意页面可读取响应（任意文件浏览/序列查看 → 数据外泄）。
# 两道校验（对每个请求，纯头部判断，开销可忽略）：
#   ① Host 必须是回环主机名 —— rebinding 时 Host 是攻击者域名，直接 403；
#   ② 带 Origin/Referer 的请求必须同源 —— 浏览器跨站 POST 必带 Origin，
#      恶意网页驱动不了任何端点。非浏览器客户端（curl/本机脚本）不带
#      Origin/Referer，正常放行。
# ------------------------------------------------------------------
_LOOPBACK_HOSTS = {'127.0.0.1', 'localhost', '::1'}

# 服务实际绑定的端口，由 main() 在 _pick_port() 之后、_start_server() 之前写入。
# None = 尚未起服务（test_client / 嵌入调用 / 打包自检），此时 Origin 校验
# 退化为主机名校验，保证既有测试与本地脚本行为不变（见下）。
_BOUND_PORT = None


def _url_hostname(v):
    try:
        from urllib.parse import urlparse
        return (urlparse(v).hostname or '').lower()
    except ValueError:
        return ''


def _origin_same_platform(value):
    """Origin/Referer 是否来自本平台源（http://<回环主机>:<绑定端口>）。

    只比 hostname 不够：本机**其它端口**上的任意页面同样是 127.0.0.1，
    浏览器视角与平台并不同源，却照样能驱动平台的破坏性端点（本地 CSRF）。
    端口在启动时已知，这里按「完整源」比对（scheme + host + port 三项）；
    未绑定端口（_BOUND_PORT is None）时退回主机名校验，避免 test_client
    与本地冒烟脚本被误伤。
    """
    try:
        from urllib.parse import urlparse
        u = urlparse(value)
    except ValueError:
        return False
    if (u.hostname or '').lower() not in _LOOPBACK_HOSTS:
        return False
    if _BOUND_PORT is None:
        return True                 # 未起服务：仅回环主机名校验
    if (u.scheme or '').lower() != 'http':
        return False                # 平台只跑 http，其它协议一律拒绝
    try:
        return u.port == _BOUND_PORT
    except ValueError:              # 端口非法（:abc / 超界）
        return False


@app.before_request
def _guard_local_origin():
    host_hdr = (request.host or '') if request else ''
    if host_hdr:
        # 尾部圆点（"localhost.:8765"）是合法 DNS 写法，剥点后再比对
        hostname = host_hdr.rsplit(':', 1)[0].strip('[]').lower().rstrip('.')
        if hostname and hostname not in _LOOPBACK_HOSTS:
            abort(403, '仅允许通过本机回环地址访问平台')
    origin = request.headers.get('Origin') or ''
    if origin:
        # 浏览器对同源 POST 也带 Origin（必须是本平台**完整源**才放行）
        if not _origin_same_platform(origin):
            abort(403, '已拦截跨站请求（Origin 校验失败）')
    else:
        referer = request.headers.get('Referer') or ''
        # 无 Origin 且非浏览器客户端（curl / 本机脚本 / 拖拽代理，都不发
        # Origin）保持既有放行逻辑——拖拽代理依赖它。
        # 威胁模型核对（2026-10-04 审查遗留项复核）：① 浏览器跨站请求
        # （含 CSRF）fetch/form POST 必带 Origin，已在上方完整源校验拦截；
        # ② 无 Origin 的只可能是本机进程，其权限等价于登录用户，回环
        # 绑定已是边界（进一步收紧对 CSRF 无增益，只会打断 test_client
        # 与 CLI）；③ 破坏性路由 14 个全为 POST、无 GET 型（否则跨站
        # 导航不带 Origin 可直达）——三项成立，本放行是安全决策而非疏漏。
        if referer and not _origin_same_platform(referer):
            abort(403, '已拦截跨站请求（Referer 校验失败）')

# ------------------------------------------------------------------
# Blueprint 注册
# 路由清单守卫：python tests/_route_inventory.py
#（锁定 229 条 rule + methods，拆分/新增后必须零差异）
# ------------------------------------------------------------------
from Virus_Platform_Core.web import (  # noqa: E402
    ai_assist as _bp_ai,
    anchor_api as _bp_anchor,
    build as _bp_build,
    cds_export as _bp_cds_export,
    download as _bp_download,
    examples_api as _bp_examples,
    io_api as _bp_io,
    logan as _bp_logan,
    mcp_console as _bp_mcp,
    meta as _bp_meta,
    pages as _bp_pages,
    prescreen as _bp_prescreen,
    refs as _bp_refs,
    samples as _bp_samples,
    settings as _bp_settings,
    submit as _bp_submit,
    tasks_api as _bp_tasks,
    tool_results as _bp_tool_results,
    tools_api as _bp_tools,
    virome as _bp_virome,
)

# 顺序与拆分前的 app.py 区块顺序一致（便于对照回溯）
#
# 2026-09-17：病毒浏览器（explorer）蓝图停止挂载 —— 平台不再提供 /explorer
# 页面与 /api/explorer/* 接口。
# 2026-09-27：**彻底归档** —— 引擎（Virus_Platform_Core/explorer/）、蓝图
# （web/explorer.py）、页面（explorer.html / app-explorer.js）、专属测试与
# 运行数据（run/explorer_exports、run/explorer_variation）全部移入
# archive/_retire_20260927/explorer/，代码与数据在本仓不再保留活动副本。
# 注意：app.py 的 JSON 清洗器原从 web/explorer.py 借 _nan_to_null（每个
# JSON 响应都走它）——现已内联为本文件顶部的 _nan_to_null，无外部依赖。
# 2026-10-27：新增 anchor_api（样品锚只读 API，3 条路由），见
# Virus_Platform_Core/anchor.py 与 docs/DEVELOPMENT_NOTES.md 的「样品锚」一节。
for _bp in (_bp_pages, _bp_ai, _bp_anchor, _bp_tool_results, _bp_refs, _bp_logan, _bp_io,
            _bp_mcp,
            _bp_samples, _bp_download, _bp_submit, _bp_meta, _bp_prescreen,
            _bp_settings, _bp_virome, _bp_tools, _bp_tasks, _bp_build,
            _bp_examples, _bp_cds_export):
    app.register_blueprint(_bp.bp)
del _bp


# 把不带 TaskManager 执行的子系统接进任务中心（统一列表/进度/停止/前往）：
# 下载批次（DownloadManager 自建线程）。执行仍归各自子系统，这里只是
# 注册 provider 回调做状态镜像；详见 web/download.py:bind_downloads。
from Virus_Platform_Core.web.download import bind_downloads as _bind_downloads  # noqa: E402
_bind_downloads()

# 子进程事件回传（2026-11-02）：对话坞发起的跑批跑在 MCP 子进程里，它的
# 阶段事件（📖 开始 / ✔ 完成 / 🏁 全部完成）写进 run/ai_audit/spool/，这里起
# 一个 daemon 线程 tail 回本进程的广播缓冲，SSE 才推得到对话坞。
# VP_EVENT_RELAY=0 可关（纯测试/排查用）。
if os.environ.get('VP_EVENT_RELAY', '1') != '0':
    try:
        from Virus_Platform_Core.ai_agent.spool_relay import (
            start_relay as _start_ai_relay)
        _start_ai_relay()
    except Exception as _e:                       # 回传坏了不影响网页
        sys.stderr.write(f'[app] AI 事件回传启动失败（忽略）: {_e}\n')


# ------------------------------------------------------------------
# 兼容别名：app.py 拆分到 Virus_Platform_Core/web/ 之前，这些名字挂在 app 模块上，
# 仓库内测试（tests/_it_*.py、test_logan_trace.py）与外部脚本仍按老写法
# 引用（app.tm / app._KEEP_TOTAL / app._resolve_virus_db）。
# 这里只做转发，真实定义仍在各自模块，避免复制状态。
# ------------------------------------------------------------------
from Virus_Platform_Core.web.tasks import tm as tm  # noqa: E402,F401
from Virus_Platform_Core.web.tasks import _KEEP_TOTAL as _KEEP_TOTAL  # noqa: E402,F401
from Virus_Platform_Core.web.tools_api import _resolve_virus_db as _resolve_virus_db  # noqa: E402,F401


# ------------------------------------------------------------------
# 启动
# ------------------------------------------------------------------
def _pick_port():
    """选择可绑定的端口。

    Windows 上 Hyper-V/WSL 会动态保留端口段（netsh 可见），
    固定端口可能被系统排除导致 bind 被拒（访问权限不允许），
    因此逐个探测候选端口，全部失败再随机尝试。
    """
    import socket
    candidates = [8765, 8900, 8989, 9600, 8888, 5050, 5000]
    for p in candidates:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(('127.0.0.1', p))
                return p
        except OSError:
            continue
    import random
    for _ in range(30):
        p = random.randint(10000, 19999)
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(('127.0.0.1', p))
                return p
        except OSError:
            continue
    raise RuntimeError('未找到可用的本地端口，请关闭占用端口的程序后重试')


_LOG_PATH = {'path': None}


def _log_open_browser(msg):
    """把「打开浏览器」的结果追加到日志（顺序找第一个可写位置）。

    冻结版实测写 <平台>/run/_open_browser.log 可能被权限拒绝，所以再兜
    %LOCALAPPDATA%\\VirusPlatform\\open_browser.log。都写不了就静默——
    它只是诊断信息，不能影响启动。
    """
    import os as _os
    line = '%s %s\n' % (time.strftime('%Y-%m-%d %H:%M:%S'), msg)
    cands = []
    if _LOG_PATH['path']:
        cands.append(_LOG_PATH['path'])
    try:
        from Virus_Platform_Core.config import PLATFORM_ROOT
        cands.append(_os.path.join(PLATFORM_ROOT, 'run', '_open_browser.log'))
    except Exception:                                   # noqa: BLE001
        pass
    local = _os.environ.get('LOCALAPPDATA') or _os.path.expanduser('~')
    cands.append(_os.path.join(local, 'VirusPlatform', 'open_browser.log'))
    for path in cands:
        try:
            _os.makedirs(_os.path.dirname(path), exist_ok=True)
            with open(path, 'a', encoding='utf-8') as fh:
                fh.write(line)
            _LOG_PATH['path'] = path
            return
        except OSError:
            continue


def _default_browser_exe():
    r"""从注册表解析默认浏览器的可执行文件路径（失败返回 None）。

    HKCU\...\UrlAssociations\http\UserChoice 给出 ProgId（如 ChromeHTML），
    再到 HKCR\<ProgId>\shell\open\command 取命令行，形如
        "C:\Program Files\Google\Chrome\Application\chrome.exe" -- "%1"
    """
    if sys.platform != 'win32':
        return None
    try:
        import winreg
    except ImportError:
        return None
    prog = None
    try:
        with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r'Software\Microsoft\Windows\Shell\Associations'
                r'\UrlAssociations\http\UserChoice') as k:
            prog = winreg.QueryValueEx(k, 'ProgId')[0]
    except OSError:
        prog = None
    cmd = None
    for sub in ((prog + r'\shell\open\command') if prog else None,
                r'http\shell\open\command'):
        if not sub:
            continue
        try:
            with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, sub) as k:
                cmd = winreg.QueryValueEx(k, '')[0]
                break
        except OSError:
            continue
    if not cmd:
        return None
    # 命令行里 exe 通常带引号；取第一个引号段，否则取到第一个空格
    if cmd.startswith('"'):
        end = cmd.find('"', 1)
        exe = cmd[1:end] if end > 1 else cmd.strip('"')
    else:
        exe = cmd.split(' ')[0]
    return exe if exe and os.path.isfile(exe) else None


# Chromium 内核的浏览器（含国产）：都认 --new-window / --user-data-dir。
# 名字命中只用于"怎么传参"，不影响能不能用 —— 不认识的浏览器仍然会被打开。
CHROMIUM_HINTS = ('chrome', 'msedge', 'brave', 'chromium', 'vivaldi', 'opera',
                  '360se', '360chrome', '360browser', 'qqbrowser', 'sogou',
                  'ucbrowser', 'maxthon', 'liebao', '2345explorer')


def _is_chromium(exe):
    low = (exe or '').lower()
    return any(t in low for t in CHROMIUM_HINTS)


def _is_firefox(exe):
    return 'firefox' in (exe or '').lower()


def _new_window_args(exe, url):
    """按浏览器家族选「新窗口」参数；不认识的浏览器只给 URL。"""
    if _is_chromium(exe):
        return [exe, '--new-window', url]
    if _is_firefox(exe):
        return [exe, '-new-window', url]
    return [exe, url]


# 浏览器进程名（按进程认，比窗口标题可靠：Chrome 的窗口标题反映的是
# 当前活动标签，新开的平台标签可能不在前台）
BROWSER_PROCS = ('chrome.exe', 'msedge.exe', 'firefox.exe', 'brave.exe',
                 'chromium.exe', 'opera.exe', 'vivaldi.exe', '360se.exe',
                 '360chrome.exe', '360browser.exe', 'qqbrowser.exe',
                 'sogouexplorer.exe', 'ucbrowser.exe', 'maxthon.exe',
                 'liebao.exe', '2345explorer.exe')


def _window_proc_name(hwnd):
    """窗口所属进程的可执行文件名（取不到返回空串）。"""
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.windll.kernel32
        user32 = ctypes.windll.user32
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return ''
        h = k32.OpenProcess(0x1000, False, pid.value)    # QUERY_LIMITED_INFORMATION
        if not h:
            return ''
        try:
            buf = ctypes.create_unicode_buffer(512)
            size = wintypes.DWORD(512)
            if not k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                return ''
            return os.path.basename(buf.value).lower()
        finally:
            k32.CloseHandle(h)
    except Exception:                                   # noqa: BLE001
        return ''


def _focus_platform_window(timeout=3.0):
    """把平台页面所在浏览器窗口提到前台（尽力而为，返回是否找到）。

    命中条件：窗口可见、标题含平台名、且**所属进程是浏览器**（或标题里带
    浏览器名兜底）。不能用"只要标题含平台名"——启动脚本的控制台标题就是
    "Plant Virus Analysis Platform - Web mode (browser)"。
    """
    if sys.platform != 'win32':
        return False
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
    except Exception:                                   # noqa: BLE001
        return False
    keys = ('植物病毒分析平台', 'Plant Virus Analysis Platform')
    browsers = ('Google Chrome', 'Microsoft Edge', 'Mozilla Firefox',
                'Chromium', 'Brave', 'Opera', 'Vivaldi',
                '360', 'QQ浏览器', 'QQBrowser', '搜狗', 'Sogou', 'UC浏览器',
                'Maxthon', '傲游', '猎豹', '2345')
    hit = {'hwnd': None}
    CB = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def _cb(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        title = buf.value or ''
        if not any(k in title for k in keys):
            return True
        proc = _window_proc_name(hwnd)
        if (proc in BROWSER_PROCS) or any(b in title for b in browsers):
            hit['hwnd'] = hwnd
            return False
        return True

    # 第二手：标题里没有平台名（Chrome 收到 URL 后可能没切换活动标签，
    # 窗口标题还是别的页面）时，退而求其次把 z-order 最靠前的浏览器窗口提上来
    # —— 它通常就是刚接收标签页的那个窗口。
    fallback = {'hwnd': None}

    def _cb_first_browser(hwnd, _lparam):
        if fallback['hwnd']:
            return True
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return True
        if _window_proc_name(hwnd) in BROWSER_PROCS:
            fallback['hwnd'] = hwnd
            return False
        return True

    cb_first = CB(_cb_first_browser)

    cb = CB(_cb)
    end = time.time() + timeout
    while time.time() < end:
        user32.EnumWindows(cb, 0)
        if not hit['hwnd']:
            user32.EnumWindows(cb_first, 0)
            if fallback['hwnd']:
                hit['hwnd'] = fallback['hwnd']
                _log_open_browser('no titled platform window; raising topmost '
                                  'browser window instead')
        if hit['hwnd']:
            h = hit['hwnd']
            try:
                user32.ShowWindow(h, 9)                 # SW_RESTORE
                # 前台锁：后台进程直接 SetForegroundWindow 常被拒绝（只闪任务栏）
                k32 = ctypes.windll.kernel32
                fg = user32.GetForegroundWindow()
                fg_tid = user32.GetWindowThreadProcessId(fg, None) if fg else 0
                cur_tid = k32.GetCurrentThreadId()
                attached = False
                if fg_tid and fg_tid != cur_tid:
                    attached = bool(user32.AttachThreadInput(
                        cur_tid, fg_tid, True))
                try:
                    user32.BringWindowToTop(h)
                    user32.SetForegroundWindow(h)
                finally:
                    if attached:
                        user32.AttachThreadInput(cur_tid, fg_tid, False)
            except Exception:                           # noqa: BLE001
                return False
            return True
        time.sleep(0.3)
    return False


def _which_browser(names):
    """在常见安装位置 + PATH + 用户级安装里找一个浏览器 exe。"""
    import glob as _glob
    import shutil as _sh
    home_local = os.path.join(os.path.expanduser('~'), 'AppData', 'Local')
    fixed = {
        'msedge': [os.path.join(os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)'),
                                'Microsoft', 'Edge', 'Application', 'msedge.exe'),
                   os.path.join(os.environ.get('ProgramFiles', r'C:\Program Files'),
                                'Microsoft', 'Edge', 'Application', 'msedge.exe')],
        'chrome': [os.path.join(os.environ.get('ProgramFiles', r'C:\Program Files'),
                                'Google', 'Chrome', 'Application', 'chrome.exe'),
                   os.path.join(os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)'),
                                'Google', 'Chrome', 'Application', 'chrome.exe')],
        'firefox': [os.path.join(os.environ.get('ProgramFiles', r'C:\Program Files'),
                                 'Mozilla Firefox', 'firefox.exe'),
                    os.path.join(os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)'),
                                 'Mozilla Firefox', 'firefox.exe')],
    }
    for key in names:
        for p in fixed.get(key, ()):
            if os.path.isfile(p):
                return p
        hit = _sh.which(key + '.exe') or _sh.which(key)
        if hit:
            return hit
        for pat in (os.path.join(home_local, '*', 'Application', key + '.exe'),
                    os.path.join(home_local, 'Programs', key, key + '.exe')):
            hits = sorted(_glob.glob(pat))
            if hits:
                return hits[0]
    return None


def _profile_browser_candidates():
    """独立配置兜底用的浏览器候选（去重，只留真实存在的）。

    顺序：系统默认浏览器（若为已知浏览器）→ Edge → Chrome → Firefox。
    国产套壳浏览器由「默认浏览器」那一档覆盖，这里不猜它们的安装路径。
    """
    out = []
    try:
        exe = _default_browser_exe()
    except Exception:                                   # noqa: BLE001
        exe = None
    if exe and (_is_chromium(exe) or _is_firefox(exe)):
        out.append(exe)
    for key in ('msedge', 'chrome', 'firefox'):
        p = _which_browser([key])
        if p:
            out.append(p)
    seen, uniq = set(), []
    for p in out:
        k = os.path.normcase(os.path.abspath(p))
        if k not in seen:
            seen.add(k)
            uniq.append(p)
    return uniq


def _app_window_candidates():
    """--app 独立窗口的浏览器候选：默认浏览器（可解析时）→ Chrome → Edge。"""
    out = []
    try:
        exe = _default_browser_exe()
    except Exception:                                   # noqa: BLE001
        exe = None
    if exe:
        out.append(exe)
    for key in ('chrome', 'msedge'):
        p = _which_browser([key])
        if p:
            out.append(p)
    seen, uniq = set(), []
    for p in out:
        k = os.path.normcase(os.path.abspath(p))
        if k not in seen:
            seen.add(k)
            uniq.append(p)
    return uniq


def _spawn_detached(cmd):
    """经 `cmd /c start` 启动外部程序（不要直接 CreateProcess）。

    两条都是 2026-10-29 实测出来的（冻结版 + 作业对象场景）：
      1. 直接 `Popen([chrome.exe, ...])` → Chrome 立刻退出
         （poll = 0x80000003 STATUS_BREAKPOINT），一个窗口都没有；
         经 `cmd /c start` 才正常。
      2. **绝对不要加 CREATE_NO_WINDOW**：带上它时页面照样加载，但浏览器
         窗口**不会到前台**（Windows 前台权限只给"有界面"的启动者）；去掉
         以后同一个 URL 立刻弹出并置前的窗口。这就是"开了但看不到"的根因。
    """
    import subprocess
    subprocess.Popen(['cmd', '/c', 'start', ''] + list(cmd))


def _open_browser(url):
    """打开平台页面：优先独立应用窗口，其次普通标签页。

    顺序与判据（2026-10-29 收敛后的最终版）：
      1. `--app=<url>` 独立窗口（Chromium 系）—— 标题就是页面标题，不会被
         塞进别的窗口；启动后 3 秒内用 `_focus_platform_window()` 确认窗口
         真的出现并置前。找不到就继续下一条（最多两轮，不会连环开标签）。
      2. `webbrowser`（系统默认浏览器，普通标签页）—— 历史上一直有效的那条路；
         返回成功即视为成功，只提示地址，不再做窗口验证（否则又会误判）。
      3. 其余兜底：startfile → cmd start(url) → 独立配置窗口 → explorer。
    每一步都写 `run/_open_browser.log`（写不进去会退到 %LOCALAPPDATA%）。
    """
    time.sleep(1.2)
    import subprocess

    def _try_app_window():
        for exe in _app_window_candidates():
            if not _is_chromium(exe):
                continue
            try:
                _spawn_detached([exe, '--app=' + url])
                return True
            except OSError as e:
                _log_open_browser('app-window failed (%s): %s' % (exe, e))
        return False

    def _try_webbrowser():
        return bool(webbrowser.open(url, new=2))

    def _try_new_window():
        exe = _default_browser_exe()
        if not exe:
            return False
        _spawn_detached(_new_window_args(exe, url))
        return True

    def _try_startfile():
        os.startfile(url)                       # noqa: S606
        return True

    def _try_cmd_start():
        # 同样不能带 CREATE_NO_WINDOW（见 _spawn_detached 的注释）
        import subprocess
        subprocess.Popen(['cmd', '/c', 'start', '', url])
        return True

    def _try_profile():
        root = os.path.join(os.path.expanduser('~'), 'AppData', 'Local',
                            'VirusPlatform')
        for exe in _profile_browser_candidates():
            try:
                if _is_chromium(exe):
                    prof = os.path.join(root, 'browser-profile')
                    os.makedirs(prof, exist_ok=True)
                    _spawn_detached([exe, '--user-data-dir=' + prof,
                                     '--no-first-run', '--new-window', url])
                    return True
                if _is_firefox(exe):
                    prof = os.path.join(root, 'firefox-profile')
                    os.makedirs(prof, exist_ok=True)
                    _spawn_detached([exe, '-profile', prof, '-new-window', url])
                    return True
            except OSError as e:
                _log_open_browser('profile start failed (%s): %s' % (exe, e))
        return False

    def _try_explorer():
        subprocess.Popen(['explorer.exe', url])
        return True

    # (名称, 函数, 是否验证窗口) —— 只有 app-window 验证：它是"看得见"的那条，
    # 不可靠时立刻退到标签页；标签页这条不再验证，避免再次连环误判。
    # 顺序（2026-10-29 定稿）：先走**shell 路径**——`cmd /c start "" <url>` 由
    # cmd 转交 shell 打开，实测在「作业对象」里（Windows Terminal 启动的就是）
    # 也能正常加载页面；而直接 CreateProcess 拉 chrome.exe 在作业里必死
    # （0x80000003），所以 --app 只能排后面、给没有作业限制的场合用。
    steps = [
        ('cmd-start', _try_cmd_start, False),
        ('webbrowser', _try_webbrowser, False),
        ('app-window', _try_app_window, True),
        ('new-window', _try_new_window, False),
        ('startfile', _try_startfile, False),
        ('profile', _try_profile, True),
        ('explorer', _try_explorer, False),
    ]
    for name, fn, verify in steps:
        try:
            ok = fn()
        except Exception as e:                          # noqa: BLE001
            print(f'  [提示] {name} 失败（{type(e).__name__}: {e}）')
            _log_open_browser('%s raised %s: %s' % (name, type(e).__name__, e))
            continue
        if not ok:
            _log_open_browser('%s returned False: %s' % (name, url))
            continue
        if verify and not _focus_platform_window(timeout=3.0):
            _log_open_browser('%s spawned but no platform window yet: %s'
                              % (name, url))
            continue
        print(f'  已打开平台页面: {url}（方式: {name}）')
        _log_open_browser('opened via %s: %s' % (name, url))
        if _focus_platform_window(timeout=2.0):
            print('  已把平台窗口切到前台。')
            _log_open_browser('focused platform window')
        else:
            print('  若没看到页面，请查看任务栏，或手动在浏览器访问上面的地址。')
        return

    print(f'  [提示] 未能自动打开浏览器，请手动在浏览器访问: {url}')
    _log_open_browser('ALL FAILED, manual URL: ' + url)


def _start_server(port):
    """在后台线程起 Flask，返回已绑定端口的 server 句柄。

    为什么不用 app.run：它会阻塞主线程，而 pywebview 的窗口必须开在主线程；
    换到后台线程后主线程才能建窗口，且拿到 server 句柄，窗口关闭时可停机。
    make_server 同步完成 bind，返回即意味着地址可访问，窗口首开不会白屏。
    """
    from werkzeug.serving import make_server
    srv = make_server('127.0.0.1', port, app, threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _gui_mode():
    """启动模式：'gui'（独立桌面窗口）| 'web'（系统浏览器）。

    优先级：命令行 `--web`/`--browser` 或 `--gui` > 环境变量 VP_GUI > 默认 gui。
    为什么要有命令行开关：原先只能用 `set VP_GUI=browser` 切模式，双击 .bat
    的人没有入口；打包后的 exe 也一样（`VirusPlatform.exe --web` 即可）。
    gui 模式下若 pywebview/WebView2 不可用，仍会自动回退浏览器（见 _open_window）。
    """
    for raw in sys.argv[1:]:
        a = raw.strip().lower()
        if a in ('--web', '--browser', '--no-gui'):
            return 'web'
        if a in ('--gui', '--window', '--webview'):
            return 'gui'
    v = os.environ.get('VP_GUI', '').strip().lower()
    if v in ('browser', 'web'):
        return 'web'
    if v in ('gui', 'window', 'webview'):
        return 'gui'
    return 'gui'


def _open_window(url, mode='gui'):
    """pywebview 独立桌面窗口（壳住本地页面，无浏览器地址栏）。

    返回 True=窗口已开过并关闭（进程应退出）；False=pywebview 不可用或
    指定了网页模式，调用方回退浏览器。Windows 固定 edgechromium 后端：
    系统缺 WebView2 运行时就走回退，绝不静默掉到老旧的 mshtml
    （IE 内核渲染会烂）。
    """
    if mode == 'web':
        return False
    try:
        import webview
    except Exception:
        print('  [提示] 未安装 pywebview，回退浏览器模式'
              '（pip install pywebview 后即为独立窗口）')
        return False
    try:
        webview.create_window('植物病毒分析平台', url,
                              width=1440, height=900, min_size=(1080, 700))
        webview.start(gui='edgechromium' if sys.platform == 'win32' else None)
        return True
    except Exception as e:
        print(f'  [提示] 桌面窗口启动失败（{type(e).__name__}: {e}），'
              f'回退浏览器模式')
        return False


def main():
    # 诊断开关（2026-10-29）：冻结版里"浏览器到底卡在哪一步"只能靠它，
    # 因为 run/ 日志在部分环境写不进去、控制台输出又会被缓冲吞掉。
    if '--browser-selftest' in sys.argv:
        import subprocess as _sp
        from Virus_Platform_Core.config import PLATFORM_ROOT as _PR
        print('frozen =', getattr(sys, 'frozen', False))
        print('executable =', sys.executable)
        print('PLATFORM_ROOT =', _PR)
        print('__file__ =', __file__)
        for label, fn in (('default_browser_exe', _default_browser_exe),
                          ('app_window_candidates', _app_window_candidates),
                          ('profile_browser_candidates',
                           _profile_browser_candidates)):
            try:
                print(label, '=', fn())
            except Exception as e:                      # noqa: BLE001
                print(label, 'FAILED:', type(e).__name__, e)
        exe = (_app_window_candidates() or [None])[0]
        print('spawn target =', exe)
        no_win = getattr(_sp, 'CREATE_NO_WINDOW', 0)
        variants = (
            ('A no_win', lambda: _sp.Popen([exe, '--app=about:blank#a'],
                                           creationflags=no_win)),
            ('B devnull', lambda: _sp.Popen([exe, '--app=about:blank#b'],
                                            stdin=_sp.DEVNULL, stdout=_sp.DEVNULL,
                                            stderr=_sp.DEVNULL, close_fds=True)),
            ('C cmd start', lambda: _sp.Popen(
                ['cmd', '/c', 'start', '', exe, '--app=about:blank#c'],
                creationflags=no_win)),
            ('D plain', lambda: _sp.Popen([exe, '--app=about:blank#d'])),
        )
        print('--- full _open_browser 演练 ---')
        try:
            _open_browser('http://127.0.0.1:8765')
            print('open_browser returned')
        except Exception as e:                          # noqa: BLE001
            print('open_browser raised:', type(e).__name__, e)
        for p in (os.path.join(_PR, 'run', '_open_browser.log'),
                  os.path.join(os.environ.get('LOCALAPPDATA') or '',
                               'VirusPlatform', 'open_browser.log')):
            print('log', p, 'exists =', os.path.exists(p))
        for label, fn in variants:
            try:
                p = fn()
                time.sleep(3)
                print('%-12s pid=%s poll=%s' % (label, p.pid, p.poll()))
            except Exception as e:                      # noqa: BLE001
                print('%-12s FAILED %s: %s' % (label, type(e).__name__, e))
        return
    # 接管 SIGINT/SIGTERM/SIGBREAK：退出前把 AI 审计队列排空。Windows 下
    # SIGTERM **不触发 atexit**（实测），只靠 atexit 会丢最后几秒的记录。
    # 必须在主线程且尽早装（signal.signal 有线程限制，且装晚了会漏掉信号）。
    try:
        from Virus_Platform_Core.ai_agent import audit as _ai_audit
        _ai_audit.install_signals()
    except Exception:
        pass
    # 单实例锁已按用户要求移除（2026-10-28）：不再拦截第二个实例。
    # 代价：两个实例共享同一份 run/（queue.json / tasks/*.json / results）
    # 时可能互相覆盖，请自行避免同时开两份。
    mode = _gui_mode()
    try:
        port = _pick_port()
    except RuntimeError as e:
        print(f'  [错误] {e}')
        _pause_before_exit()
        return
    # CSRF 端口校验的基准：起服务前写入，_guard_local_origin 按「完整源」
    # 比对 Origin/Referer（见 _origin_same_platform）。
    global _BOUND_PORT
    _BOUND_PORT = port
    # 实际端口落盘供拖拽代理等外部组件读取（此前代理只扫 8760-8789，
    # 平台备选端口 8900/9600/随机段全在扫描区间外——2026-10-04 审查 E4）
    try:
        import pathlib as _pl
        # 必须跟 RUN_ROOT 走：VP_RUN_ROOT 隔离（测试套件/多实例并存）时，
        # 硬拼 `<平台根>/run` 会把 .port 写进别人的真实目录。
        from Virus_Platform_Core.config import RUN_ROOT as _RR
        _port_file = _pl.Path(_RR) / '.port'
        _port_file.parent.mkdir(parents=True, exist_ok=True)
        _port_file.write_text(str(port), encoding='ascii')
    except OSError:
        pass
    url = f'http://127.0.0.1:{port}'
    print('=' * 56)
    print('  植物病毒分析平台 启动中...'
          f'（模式: {"独立桌面窗口" if mode == "gui" else "系统浏览器"}）')
    print(f'  本机地址: {url}')
    if port != 8765:
        print(f'  （默认端口 8765 被系统占用/保留，已自动改用 {port}）')
    print('  关闭窗口/控制台即退出平台'
          if mode == 'gui' else
          '  平台在本控制台窗口内运行，关闭它即退出')
    print('=' * 56)
    try:
        srv = _start_server(port)
    except OSError as e:
        print(f'\n  [错误] 端口 {port} 监听失败: {e}')
        print('  可在管理员 PowerShell 运行以下命令查看被系统保留的端口段:')
        print('     netsh interface ipv4 show excludedportrange protocol=tcp')
        _pause_before_exit()
        return
    # Taxonomy 首启自解压（2026-10-25）：数据库包只随附官方 new_taxdump.tar.gz
    # （155M）；检测到本地 tar 且未解压时，后台线程解压（约 1 分钟，纯本地
    # 零联网——prepare_taxonomy 优先用本地 tar），不阻塞启动。失败静默：
    # 数据库页的一键按钮仍是兜底出口。
    try:
        from Virus_Platform_Core.config import DIRS
        from Virus_Platform_Core.taxonomy import (prepare_taxonomy,
                                                  taxonomy_ready)
        if not taxonomy_ready() and os.path.isfile(
                os.path.join(DIRS['databases'], 'new_taxdump.tar.gz')):
            def _bg_taxonomy():
                try:
                    prepare_taxonomy()
                except Exception:                       # noqa: BLE001
                    pass
            threading.Thread(target=_bg_taxonomy, daemon=True).start()
    except Exception:                                   # noqa: BLE001
        pass
    try:
        if _open_window(url, mode):
            print('  桌面窗口已关闭，平台退出。')
            return
        # 浏览器模式：显式 --web / VP_GUI=browser，或 gui 模式但窗口不可用时回退
        if mode == 'gui':
            print('  （已回退浏览器模式；如需固定用网页模式，'
                  '下次用 启动平台-网页.bat 或 python app.py --web）')
        # --no-open：由启动脚本负责开浏览器（Start-Web.bat 里那条
        # `start "" <url>` 从有控制台的 cmd 发起，前台权限正常，窗口一定会
        # 弹出来；平台进程自己拉浏览器在部分上下文里窗口不到前台）。
        if '--no-open' not in sys.argv:
            threading.Thread(target=_open_browser, args=(url,), daemon=True).start()
        while True:
            time.sleep(3600)    # 关闭本控制台窗口即退出（守护线程随之终止）
    except KeyboardInterrupt:
        print('\n  收到退出信号，平台关闭。')
    finally:
        try:
            srv.shutdown()
        except Exception:
            pass


if __name__ == '__main__':
    main()
