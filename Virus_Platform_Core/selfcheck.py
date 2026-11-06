# -*- coding: utf-8 -*-
"""环境自检（结构化版）：模块导入 / 外部工具 / 数据库 / 磁盘空间。

collect() 返回四段结构化结果，供两处共用：
  - web API /api/selfcheck（设置页「环境自检」卡片 + 总览页环境状态条）
  - main.py selfcheck（CLI 控制台版，另带 ⑤样品 / ⑥依赖 两段 CLI 专属检查）

结果按进程缓存（CACHE_TTL 秒内直接复用）：首次收集要递归 import 全部 80+
子模块，实测数秒；此后模块都已进 sys.modules，force=True 重跑也很快。
整体 ok 的口径与 CLI 一致：模块导入与数据库失败才算失败；工具缺失只算
降级（warn），磁盘不足只提示。
"""
import importlib
import os
import shutil
import threading
import time

from Virus_Platform_Core.config import DIRS, PLATFORM_ROOT, get_config
from Virus_Platform_Core.module_deps import (CARD_TOOLS, OPTIONAL_TOOLS,
                                             tools_for)

CACHE_TTL = 300.0          # 缓存有效期（秒）；设置页按钮走 force=1 强制重跑
DISK_WARN_BYTES = 10e9     # 磁盘剩余低于此值提示（与 CLI 自检同一口径）
_CACHE = {'ts': 0.0, 'data': None}


def invalidate():
    """清掉进程内自检缓存，下次 collect() 立即重算。

    数据库状态在进程内发生变化（建库完成 / 加载宿主库 / 切换当前库 /
    登记病毒库）后应由改动方调一下：否则总览页环境状态条会把加载前的
    "数据库 未就绪"一直展示到缓存过期，看起来像"加载了但没同步"
    （2026-09-30 手动加载宿主库后即此现象）。模块探测较贵（子进程逐个
    import），所以不做短 TTL 轮询，只在状态变化点精确失效。"""
    _CACHE['ts'] = 0.0
    _CACHE['data'] = None


def iter_platform_modules():
    """枚举 Virus_Platform_Core 下的全部子模块（递归含子包）。

    返回 (模块名列表, 遍历期导入失败的子包名列表)。

    为什么遍历而不是硬编码清单：原先自检只 import 16 个写死的模块，而平台有
    80+ 个。**走子进程调用的引擎不在其中** —— kv 引擎由 kv_stage 以子进程启动
    （`--run-engine`），kv_stage 本身只 import config/utils，于是 kv_filter /
    kv_identify 在模块级的 `import polars` 缺失时，自检完全看不见。
    2026-09-11 就是这个盲区让「已知病毒识别与定量」整段静默失效。
    （2026-10-20 起该模块已改用 pandas + numpy，不再依赖 polars；
     这条历史教训仍成立——子进程调用的引擎必须在自检里覆盖到。）

    冻结分发下靠 PyInstaller 的 `pyi_rth_pkgutil` 运行时钩子让 pkgutil 可用，
    打包后同样能遍历。遍历本身若失败（返回空列表），调用方回退到手工清单。
    """
    import pkgutil
    mods, errs = [], []
    try:
        import Virus_Platform_Core as _pkg
        # 逐子包显式导入（带追踪）：pkgutil.walk_packages 遍历时会导入各
        # 子包 __init__，那里崩了（冻结版 C 扩展 0xC0000005）连一行痕迹
        # 都没有 —— 先自己按序导入并打印，肇事包一目了然。
        import os as _os
        import sys as _sys
        _trace = _os.environ.get('VP_IMPORT_TRACE') == '1'
        _subpkgs = []
        for _e in _pkg.__path__:
            for _n in sorted(_os.listdir(_e)):
                _p = _os.path.join(_e, _n)
                if _n.startswith('_'):
                    continue
                if _os.path.isfile(_os.path.join(_p, '__init__.py')):
                    _subpkgs.append(_pkg.__name__ + '.' + _n)
                elif _n.endswith('.py'):
                    _subpkgs.append(_pkg.__name__ + '.' + _n[:-3])
        for _full in _subpkgs:
            if _trace:
                print('[pkgimport] ' + _full, file=_sys.stderr, flush=True)
            try:
                importlib.import_module(_full)
            except Exception as e:
                errs.append(_full)
                if _trace:
                    print('[pkgimport FAILED] ' + repr(e), file=_sys.stderr,
                          flush=True)
        for info in pkgutil.walk_packages(
                _pkg.__path__, _pkg.__name__ + '.',
                onerror=lambda n: errs.append(n)):
            if info.ispkg:
                continue
            # 跳过**私有脚本**（`_smoke_test` 这类）。它们是"跑一次"的脚本，
            # 不是平台模块，且模块级代码带副作用 —— explorer/_smoke_test.py
            # 一被 import 就会完整跑一遍 explorer 冒烟测试（加载 19.9 万条
            # 元数据 + 457MB FASTA，冷启动分钟级）。自检本意只是"导入看能不能
            # 进来"，却因此把整个冒烟测试跑了一遍：既拖慢自检，又在 explorer
            # 数据移出 databases/ 后让这一项必然报错（2026-09-17 查实，调用栈
            # 为 api_selfcheck → _check_modules → importlib.import_module）。
            # `__init__.py` 不在 walk_packages 的返回里；这里仍显式放行，
            # 以免将来遍历实现变化时误伤包初始化。
            _leaf = info.name.rsplit('.', 1)[-1]
            if _leaf.startswith('_') and _leaf != '__init__':
                continue
            mods.append(info.name)
    except Exception as e:
        errs.append(f'<遍历失败: {e!r}>')
    return sorted(set(mods)), errs


# 遍历不可用时的兜底清单（原 CLI 内联清单，保持一致）
FALLBACK_MODULES = [
    'Virus_Platform_Core.config', 'Virus_Platform_Core.utils',
    'Virus_Platform_Core.taxonomy', 'Virus_Platform_Core.kunpeng',
    'Virus_Platform_Core.preprocess',
    'Virus_Platform_Core.host_removal',
    'Virus_Platform_Core.kv_stage', 'Virus_Platform_Core.assembly',
    'Virus_Platform_Core.host_analysis', 'Virus_Platform_Core.orf',
    'Virus_Platform_Core.orf_annot', 'Virus_Platform_Core.phylo',
    'Virus_Platform_Core.primer', 'Virus_Platform_Core.viz',
    'Virus_Platform_Core.pipeline', 'Virus_Platform_Core.msa_view',
]


def _check_modules():
    """逐模块导入探测。**必须走子进程**（importprobe 子命令）：

    冻结分发里某个 C 扩展的 import 可能直接段错误（0xC0000005，进程即死、
    无任何 Python 异常）。在 web 服务进程内做导入扫描时，一个坏模块就能把
    整个平台拖死（2026-09-19 实测）；放子进程里跑，段错误最多杀死探测进程，
    父进程靠协议行数定位肇事模块，平台自身毫发无损。
    """
    mods, walk_errs = iter_platform_modules()
    if not mods:
        mods = FALLBACK_MODULES
    total = len(mods) + len(walk_errs)
    t0 = time.time()
    import os
    import subprocess as _sub
    import sys as _sys
    if getattr(_sys, 'frozen', False):
        argv = [_sys.executable, '--cli', 'importprobe']
    else:
        argv = [_sys.executable, os.path.join(PLATFORM_ROOT, 'main.py'),
                'importprobe']
    mods_csv = ','.join(mods)
    fails = []
    crashed_at = None
    seen_p = 0
    _env = os.environ.copy()
    _env['VP_IMPORT_TRACE'] = '1'      # 子进程 stderr 留下逐包导入痕迹，崩溃可溯源
    try:
        # 显式 encoding='utf-8'：text=True 缺省走系统 ANSI 代码页（中文
        # Windows = GBK），探测子进程输出含非 GBK 字节时会解码报错/乱码
        proc = _sub.run(argv + [mods_csv], capture_output=True,
                        text=True, encoding='utf-8', errors='replace',
                        timeout=900, env=_env)
    except Exception as e:
        fails.append(('<importprobe>', f'探测子进程启动失败: {e!r}'))
        proc = None

    if proc is not None:
        done = False
        pending = None
        for ln in (proc.stdout or '').splitlines():
            if ln.startswith('P '):
                pending = ln[2:]
                seen_p += 1
            elif ln.startswith('F '):
                _m, _, _e = ln[2:].partition(' ')
                fails.append((_m, _e))
                pending = None
            elif ln.startswith('E '):
                fails.append((ln[2:], '子包导入失败，其下模块未能枚举'))
                pending = None
            elif ln.startswith('DONE'):
                done = True
                pending = None
        if pending:
            crashed_at = pending
        elif not done and seen_p == 0:
            crashed_at = '<枚举阶段>'
        if crashed_at:
            # 探测子进程被原生段错误杀死：肇事模块 + 其后未探测的都标失败
            _err_tail = ((proc.stderr or '').strip().splitlines() or [''])[-1]
            fails.append((crashed_at,
                          f'导入时进程崩溃（原生段错误）· 崩溃前最后痕迹: '
                          f'{_err_tail[:120]}'))
            idx = mods.index(crashed_at) if crashed_at in mods else 0
            for m in mods[idx + 1:]:
                fails.append((m, '未探测（探测进程在先前的模块崩溃后中止）'))

    return {'id': 'modules', 'title': '模块导入',
            'ok': not fails, 'warn': False,
            'summary': f'{total - len(fails)}/{total}',
            'seconds': round(time.time() - t0, 1),
            # 只列失败明细：86 个模块名全量回传没有信息量
            'items': [{'name': m, 'ok': False, 'detail': e}
                      for m, e in fails]}


def _check_tools():
    cfg = get_config()
    st = cfg.tool_status()
    missing = sorted(k for k, v in st.items() if not v)

    # 插件源能不能补：把缺失键和插件索引对一遍，能补的标出来。
    # 用户看到「缺 X」时最想知道的是「点一下能不能好」，而不是自己去
    # 翻插件列表找对应关系。索引不可达时降级为不标注（不报错）。
    fixable, unfixable = {}, []
    try:
        from Virus_Platform_Core import plugin_manager as pm
        items, _err = pm.available_from_index(cfg)
        if items:
            for it in items:
                prov = it.get('provides') or {}
                hit = [k for k in missing if k in prov]
                if hit:
                    fixable[it['name']] = {
                        'provides': hit,
                        'size': it.get('size', 0),
                        'installed': it.get('installed', False)}
            covered = {k for v in fixable.values() for k in v['provides']}
            unfixable = [k for k in missing if k not in covered]
        else:
            unfixable = list(missing)
    except Exception:
        unfixable = list(missing)

    # 本次会话自动装过什么（module_deps 落盘）——插件被悄悄装上时用户
    # 必须看得见，否则会以为"平台自己改了我的环境"。
    autofixed = {}
    try:
        from Virus_Platform_Core.module_deps import read_autofix_log
        autofixed = read_autofix_log()
    except Exception:
        pass

    return {'id': 'tools', 'title': '外部工具',
            'ok': not missing, 'warn': bool(missing),
            'summary': f'{len(st) - len(missing)}/{len(st)}',
            'missing': missing,
            # 插件源可一键补齐的（前端据此显示「可自动安装」按钮）
            'fixable': fixable,
            'unfixable': unfixable,
            'autofixed': autofixed}
    # 工具逐项明细不重复给：设置页上方「工具探测」卡片已有同源列表


def _check_databases():
    from Virus_Platform_Core.config import host_db_info
    from Virus_Platform_Core.kunpeng import db_ready
    from Virus_Platform_Core.taxonomy import taxonomy_ready
    cfg = get_config()
    _tax_ok = bool(taxonomy_ready())
    items = [{'name': 'NCBI Taxonomy', 'ok': _tax_ok}]
    if not _tax_ok:
        # 单独版不含数据库包：✘ 之外必须给出口，否则买家只会看到一个红叉
        items[0]['detail'] = ('未配置——「数据库构建」页可一键下载；离线时把 '
                              'new_taxdump.tar.gz 放到 databases/ 下，'
                              '任一条命令首次运行会自动解压')
    for key, label in (('host', '宿主库 host_db'),
                       ('virus', '病毒库 virus_db')):
        ok = bool(db_ready(cfg.databases[key]))
        item = {'name': label, 'ok': ok, 'detail': cfg.databases[key]}
        if key == 'host':
            # 宿主库"是谁"必须可见：库本体不记录物种，历史上出过
            # "目录名是枸杞、库却按 4081 番茄建"的事故，用错宿主是静默的。
            h = host_db_info(cfg.databases[key])
            if h:
                who = (f"{h.get('species')} (taxid={h.get('taxid')})"
                       if h.get('taxid') else '身份未记录')
                item['detail'] = f"{h.get('name')} | 物种 = {who}"
                if h.get('conflicted'):
                    item['detail'] += (' | ⚠ 元数据冲突 '
                                       f"{h.get('taxids_in_map')}")
                # 分发口径（2026-10-24）：宿主库按物种而异、任何安装包都
                # 不随包——legacy 固定槽位 host/classify 上"没有库"是全新
                # 部署的**预期状态**，判 ✘ 会让分发版永远过不了自检。降级
                # 为 warn（首次建库或「加载已构建宿主库」后自然转绿）；
                # 非 legacy 槽位的真实库不完整仍判失败。
                if (not ok and h.get('legacy') and not h.get('ready')
                        and not h.get('has_manifest')):
                    item['ok'] = True
                    item['warn'] = True
                    item['detail'] += ('（未配置宿主库——分发版默认不带，'
                                       '属预期；到「数据库构建」页自建，'
                                       '或「📂 加载已构建宿主库」）')
        items.append(item)
    items.append(_check_kv_ref_db())
    items.append(_check_homolog_dbs())
    return {'id': 'databases', 'title': '数据库',
            'ok': all(i['ok'] for i in items),
            'warn': any(i.get('warn') for i in items),
            'summary': f"{sum(i['ok'] for i in items)}/{len(items)}"
                       + ('（含未配置宿主库提示）'
                          if any(i.get('warn') for i in items) else ''),
            'items': items}


def _check_kv_ref_db():
    """病毒参考库（已知病毒鉴定的 kv 库，databases/virusref_db/plant_ref）。

    2026-11-05 补：kvsuite/kvchain/consensus/variant 都以它为参考真相
    （解析顺序见 kv_stage.default_reference：库内 reference.fasta →
    manifest 登记路径 → 旧布局 final.cluster.ref.fasta）。缺库时这些
    任务无法启动，属硬依赖；旧版自检没有这一项，顶栏一直显示「数据库 —」。
    """
    import os as _os
    from Virus_Platform_Core.kv_stage import default_reference, default_ref_info
    ref = default_reference()
    info = default_ref_info()
    ok = bool(ref and _os.path.isfile(ref))
    detail = ref or ('未配置——到「数据库构建」页建鉴定库（plant_ref），'
                     '或把已建库放进 databases/virusref_db/')
    if ok and not info:
        detail += ' | 缺注释表 reference.ref_info.tsv（鉴定可跑、注释列缺失）'
    return {'name': '病毒参考库 virusref', 'ok': ok, 'detail': detail}


def _check_homolog_dbs():
    """同源验证库组（t-verify 批量验证 + 单条深查共用的五库，2026-11-06）。

    ok 判定 = PVDB 蛋白库就绪（验证 blastx 分支与深查 blastx 的核心证据源）；
    其余四库（virusref 核酸库 / BFVD / PVDB CDD / 类病毒库）缺失只进
    detail 提示——对应分支都会自动降级跳过，不判失败。
    """
    import os as _os
    from Virus_Platform_Core.config import db_path, DIRS
    have, miss = [], []
    # ① PVDB 蛋白库（DIAMOND）
    from Virus_Platform_Core.local_search import PROT_DMND
    if _os.path.isfile(PROT_DMND):
        have.append('PVDB蛋白')
    else:
        miss.append('PVDB蛋白库')
    # ② 病毒参考核酸库（预置 v4 BLAST：virusref_db/blast/virus.*）
    blast_dir = _os.path.join(DIRS.get('virus_src') or
                              _os.path.join('databases', 'virusref_db'), 'blast')
    if _os.path.isdir(blast_dir) and any(
            f.startswith('virus.') for f in _os.listdir(blast_dir)):
        have.append('病毒核酸')
    else:
        miss.append('病毒核酸库')
    # ③ BFVD 域库
    try:
        from Virus_Platform_Core.bfvd_search import bfvd_db
        if bfvd_db():
            have.append('BFVD')
        else:
            miss.append('BFVD域库')
    except Exception:
        miss.append('BFVD域库')
    # ④ PVDB CDD 结构域库（③b C 路 / ⑥b CDD 层共用）
    try:
        from Virus_Platform_Core.cdd_search import cdd_db
        if cdd_db():
            have.append('CDD')
        else:
            miss.append('CDD域库')
    except Exception:
        miss.append('CDD域库')
    # ⑤ 类病毒库（viroids_v4）
    viroid_dir = db_path('misc', 'viroids')
    if _os.path.isfile(_os.path.join(viroid_dir, 'viroids_v4.nin')) or \
            _os.path.isfile(_os.path.join(viroid_dir, 'viroids_v4.njs')):
        have.append('类病毒')
    else:
        miss.append('类病毒库')
    detail = ('已就绪: ' + ' / '.join(have)) if have else '全部缺失'
    if miss:
        detail += ' | 缺: ' + ' / '.join(miss) + '（对应分支自动降级跳过）'
    # 2026-11-06 修复：ok 必须与 docstring/自述同口径——只看 PVDB 蛋白库
    # （blastx 主证据源）。此前写 bool(have)，只要 BFVD/CDD/核酸/类病毒
    # 任一在位就判「同源验证库组就绪」，与对外说明不符。
    return {'name': '同源验证库组', 'ok': 'PVDB蛋白' in have,
            'detail': detail}


_USAGE_TTL = 600.0         # 目录占用缓存（结果/运行产物可能上万文件，比自检本身还慢）
_USAGE = {'ts': 0.0, 'data': None}


def _dir_usage(path, cap_files=200000):
    """目录占用（递归）。文件数封顶，防止误指到超大目录时拖住自检。"""
    total = n = 0
    for dp, _dn, fn in os.walk(path):
        for f in fn:
            try:
                total += os.path.getsize(os.path.join(dp, f))
            except OSError:
                pass
            n += 1
            if n >= cap_files:
                return total, n
    return total, n


def _usage_items():
    """按目录统计占用：数据库 / 结果 / 运行产物 / 公共下载。

    总览页要回答"空间被谁吃了"，只有剩余空间不够用 —— 建库、跑分析、
    下数据三条路径都会吃盘，得分开看。"""
    now = time.time()
    if _USAGE['data'] is not None and now - _USAGE['ts'] < _USAGE_TTL:
        return _USAGE['data']
    items = []
    for label, key in (('数据库', 'databases'), ('结果', 'results'),
                       ('运行产物', 'tool_runs'), ('公共下载', 'downloads')):
        d = DIRS.get(key)
        if not d or not os.path.isdir(d):
            continue
        try:
            b, n = _dir_usage(d)
        except OSError:
            continue
        items.append({'name': label, 'path': d, 'bytes': b, 'files': n})
    _USAGE['data'] = items
    _USAGE['ts'] = now
    return items


def _check_disk():
    items = []
    for key, d in (('平台根', PLATFORM_ROOT), ('结果', DIRS['results'])):
        try:
            free = shutil.disk_usage(d).free
            items.append({'name': key, 'ok': free > DISK_WARN_BYTES,
                          'detail': f'剩余 {free / 1e9:.1f} GB'
                                    + ('' if free > DISK_WARN_BYTES
                                       else '（建议保留 >10GB）')})
        except OSError as e:
            items.append({'name': key, 'ok': False, 'detail': str(e)})
    return {'id': 'disk', 'title': '磁盘空间',
            'ok': all(i['ok'] for i in items),
            'warn': any(not i['ok'] for i in items),
            'summary': f"{sum(i['ok'] for i in items)}/{len(items)}",
            'items': items,
            # 按目录占用（总览页「谁吃了盘」用）；取不到就是空列表，不报错
            'usage': _usage_items()}


_REFRESH = {'busy': False}      # stale-while-revalidate 后台重算的去重旗标


def collect(force=False):
    """跑一遍四段自检并返回结构化 dict；进程内缓存 CACHE_TTL 秒。

    返回形如：{'ok': bool, 'seconds': 3.2, 'cached': False,
               'sections': [ {id,title,ok,warn,summary,items?}, ... ]}

    2026-11-06 启动性能：缓存过期时不再同步重算——**旧值即时返回**
    （标 cached=True, stale=True），重算挪到后台线程（busy 旗标防线程
    风暴）。首屏状态条不再被「插件索引联网 3×30s / importprobe 子进程 /
    全盘扫描」卡住；设置页「重新检测」仍走 force=1 同步路径。
    """
    now = time.time()
    have = _CACHE['data'] is not None
    if (not force and have
            and now - _CACHE['ts'] < CACHE_TTL):
        data = dict(_CACHE['data'])
        data['cached'] = True
        return data
    if (not force and have and not _REFRESH['busy']):
        # 缓存过期：旧值即时返回，重算进后台
        def _bg():
            _REFRESH['busy'] = True
            try:
                collect(force=True)
            except Exception:                           # noqa: BLE001
                pass
            finally:
                _REFRESH['busy'] = False
        threading.Thread(target=_bg, daemon=True,
                         name='selfcheck-swrv').start()
        data = dict(_CACHE['data'])
        data['cached'] = True
        data['stale'] = True
        return data
    t0 = time.time()
    sections = [_check_modules(), _check_tools(),
                _check_databases(), _check_disk()]
    # 整体口径与 CLI 一致：模块导入与数据库失败才算失败（工具缺失=降级、
    # 磁盘不足=提示，都不算失败）
    modules, databases = sections[0], sections[2]
    data = {'ok': bool(modules['ok'] and databases['ok']),
            'seconds': round(time.time() - t0, 1),
            'ts': now, 'cached': False, 'sections': sections}
    _CACHE['ts'], _CACHE['data'] = now, data
    return data


# ======================================================================
# Python 依赖包位（2026-11-06 用户要求：顶栏补在「外部工具」位之前）。
#
# 数据源 = dev_tools/audit_deps.py（CLI 自检 ⑥「第三方依赖」同源），不另立
# 清单。口径：
#   · n/n = 已安装 / 源码树扫描到的第三方依赖总数；
#   · 缺必需依赖 → ✘ 红；仅缺可选依赖 → △ 琥珀（对应功能降级，不阻断）；
#   · 冻结分发无源码树（analyze() 扫到 0）或审计器失效 → '—'：依赖已在
#     打包环节固化，以「模块」位为准（与 CLI 自检 ⑥ 同口径）。
# 缓存与 collect() 同策略：独立 5 分钟 TTL；?force=1 / 重新检测一并重算。
# ======================================================================
_PYDEPS_CACHE = {'ts': 0.0, 'data': None}
_PYDEPS_TTL = 300.0


def pydeps_status(force=False):
    """Python 第三方依赖包状态：{'bit': 位数据, 'detail': 弹层明细}。"""
    now = time.time()
    if (not force and _PYDEPS_CACHE['data'] is not None
            and now - _PYDEPS_CACHE['ts'] < _PYDEPS_TTL):
        return _PYDEPS_CACHE['data']
    hard, opt, scanned, na = [], [], 0, False
    try:
        from dev_tools.audit_deps import analyze as _dep_analyze
        dep = _dep_analyze()
        third = dep.get('third') or {}
        scanned = len(third)
        if not scanned:
            na = True                 # 冻结分发：无源码树可扫描
        else:
            hard = list(dep.get('hard_missing') or [])
            opt = [m for m in (dep.get('missing') or []) if m not in hard]
    except Exception:                 # 审计器坏了不能带崩状态条
        na = True
    if na:
        bit = {'id': 'pydeps', 'ok': None, 'summary': '—', 'warn': False}
    else:
        miss = hard + opt
        bit = {'id': 'pydeps', 'ok': not hard, 'warn': bool(opt),
               'summary': f'{scanned - len(miss)}/{scanned}'}
    data = {'bit': bit,
            'detail': {'scanned': scanned, 'na': na,
                       'hard_missing': hard, 'optional_missing': opt}}
    _PYDEPS_CACHE['ts'], _PYDEPS_CACHE['data'] = now, data
    return data


_SCOPE_PYDEPS_CACHE = {}     # 作用域 Python 依赖位缓存：scope_key -> (ts, data)
_SCOPE_PYDEPS_TTL = 300.0    # 与全局位同策略：5 分钟进程内缓存


def scope_pydeps_status(scope_modules, scope_key='', force=False):
    """作用域「Python 依赖」位：本作用域引擎闭包真正 import 的第三方包。

    2026-11-06 用户反馈：作用域页原来恒显全局口径（全环境 48/50），用户
    指出应显示本模块依赖的 python 数。数据源仍是 dev_tools/audit_deps.py
    （file_import_index / scope_third_party，同一 AST 扫描同一分类规则），
    不另立清单：
      作用域 → SCOPE_MODULES 引擎模块 → 平台内 import 闭包（到共享底座
      web/ai_agent/pipeline/selfcheck/app/main/mcp_server 为止）→ 第三方
      依赖名 → find_spec 判装没装。缺必需红、缺可选（OPTIONAL 表）琥珀。
    审计器失效或作用域无引擎模块 → None（调用方回退全局口径）。
    """
    if not scope_modules:
        return None
    now = time.time()
    key = scope_key or '|'.join(sorted(set(scope_modules)))
    hit = _SCOPE_PYDEPS_CACHE.get(key)
    if hit and not force and now - hit[0] < _SCOPE_PYDEPS_TTL:
        return hit[1]
    try:
        from dev_tools.audit_deps import (scope_third_party,
                                          OPTIONAL as _OPT)
        names, _ = scope_third_party(scope_modules)
    except Exception:
        return None
    missing = []
    for m in sorted(names):
        if importlib.util.find_spec(m) is None:
            missing.append(m)
    hard = [m for m in missing if m not in _OPT]
    opt = [m for m in missing if m in _OPT]
    total = len(names)
    bit = {'id': 'pydeps', 'ok': not hard, 'warn': bool(opt),
           'summary': f'{total - len(missing)}/{total}'}
    data = {'bit': bit,
            'detail': {'scanned': total, 'na': False,
                       'hard_missing': hard, 'optional_missing': opt}}
    _SCOPE_PYDEPS_CACHE[key] = (now, data)
    return data


# ======================================================================
# 模块级环境状态（2026-11-03）：顶栏状态条按当前页面所属模块出数。
#
# 背景：顶栏那颗「环境自检」胶囊原来是全局数字 + 点去设置页，放在每个
# 页面顶部没有针对性（且 .env-strip 的 display:flex 压过了 [hidden]，
# 非总览页会漏出一个空胶囊）。现在顶栏出「本模块」的环境四件套：
#   模块（全局导入健康）/ 外部工具（本模块声明的键）/ 数据库（本模块
#   真正依赖的库）/ 磁盘（全局剩余）。
# 数据源（都走现成真源，不另立清单）：
#   * MODULE_TOOLS / OPTIONAL_TOOLS（module_deps.py，模块→工具键唯一真源）
#   * TOOL_REGISTRY（web/tools_api.py，lazy import 取标题，坏了不致命）
#   * collect()（全局四段，5 分钟进程内缓存）
# ======================================================================

# 依赖哪些库：键 = 模块键；值 = [(库项, 是否软依赖)]。
# 软依赖 = 缺了会自动降级跳过（如 prepchain 宿主去除），只提示不算红。
# 库项名与 _check_databases 的 items 一一对应（virus/host/taxonomy/homolog）。
SCOPE_DBS = {
    'identify':    [('virus', False)],
    'contigs':     [('virus', False)],
    'virchain':    [('virus', False)],
    'hostremoval': [('host', False)],
    'prepchain':   [('host', True)],       # 宿主库未就绪自动跳过宿主去除
    'host':        [('host', False)],
    'hostana':     [('host', False)],
    'prescreen':   [('taxonomy', False)],  # 预筛用平台 databases/tax_db 精确判定
    'build':       [('taxonomy', True), ('host', True), ('virus', True)],
    # 2026-11-03：t-hom 并入 t-verify 后，验证卡覆盖同源验证库组
    # （virusref 核酸库 / PVDB 蛋白库 / BFVD / 类病毒库，全部软依赖：
    #   单缺只降级跳过对应分支，不判红）
    'verify':      [('homolog', True)],
    # 2026-11-05 核对补全（此前这些组一律显示「数据库 —」，与实际不符）：
    # kvsuite/kvchain/consensus 全部按 databases/virusref_db 的鉴定库
    # （plant_ref：reference.fasta + ref_info）回贴/定量，缺库直接跑不了。
    'kvsuite':     [('kvref', False)],
    'kvchain':     [('kvref', False)],
    'consensus':   [('kvref', False)],
    # 功能注释：物种→科属谱系解析硬依赖 taxonomy；PVDB 蛋白库是搜索主库
    # （同源验证库组里按 PVDB 就绪判定），缺库只降级证据层。
    'orfa':        [('taxonomy', False), ('homolog', True)],
    'annochain':   [('taxonomy', False), ('homolog', True)],
    # 引物 / CRISPR 的宿主特异性筛查读 host-db；未配置时跳过筛查并告警，
    # 属软依赖（不阻断设计）。
    'primer':      [('host', True)],
    'crispr':      [('host', True)],
}

# 顶栏 URL → 模块键。两条入口：
#   * 独立页（request.path 直接映射）
#   * 工具页卡片（/tools?g=…#t-xxx，前端把 hash 原样传 ?scope=t-xxx）
# hash → None = 在线/远程卡（无本地依赖）——仍出标题与磁盘，工具/库位显 '—'。
PATH_SCOPE = {
    '/hostremoval': 'hostremoval', '/orf': 'orf', '/annotation': 'orfa',
    '/genome': 'genoplot', '/primer': 'primer',
    '/prescreen': 'prescreen', '/build': 'build',
    '/meta': 'meta', '/virome': 'virome', '/download': 'download',
    '/refdata': 'refdata', '/samples': 'samples', '/cds-export': 'cds_export',
    '/logan': 'logan', '/submit': 'submit',
    # 2026-11-05：结果中心两张卡也补登记（无本地引擎，纯展示页）。
    # 不登记时点卡片/直接打开页面都会回退成全局口径。
    '/results': 'results', '/svguest': 'svguest',
}
HASH_TO_SCOPE = {
    't-prepchain': 'prepchain', 't-convert': 'convert', 't-fastp': 'fastp',
    't-kvchain': 'kvchain', 't-kvsuite': 'kvsuite', 't-consensus': 'consensus',
    't-variant': 'kvsuite',            # 变异卡 = kvsuite 的 variant 阶段（工具键同源）
    't-virchain': 'virchain', 't-identify': 'identify', 't-assemble': 'assemble',
    't-contigs': 'contigs', 't-verify': 'verify',
    # 2026-11-04：t-annochain 卡摘除，HASH_TO_SCOPE 条目随之移除（链本体保留，
    # agent 仍可跑；MODULE_TOOLS / MODULE_LIBS 的 annochain 行不动，供 agent
    # 一键链与历史运行的环境探测兜底）。
    't-crispr': 'crispr', 't-lamp': 'lamp', 't-dsrna': 'dsrna',
    't-mirna': 'mirna',
    't-align': 'align', 't-treebuild': 'quicktree', 't-sdt': 'sdt',
    't-cmpchain': 'cmpchain',
    't-phylodyn': 'phylodyn', 't-pdprep': 'pdprep', 't-rdp': 'rdp',
    't-rtt': 'rtt', 't-phylogeo': 'phylogeo',
    't-seqprep': None,   # 在线下载，无本地依赖
    # 2026-11-05：loganfetch 卡（LOGAN 数据下载）早已存在但没登记 ——
    # 点它时作用域回退成整个「数据资源」组，标题与计数都不是这张卡的。
    't-loganfetch': 'loganfetch',
    # 2026-11-04 曾按「卡已摘除」移除 t-annochain；实际 tools.html 仍有该卡
    # （id="t-annochain"），不登记同样会回退到组口径，现恢复。
    't-annochain': 'annochain',
    # 2026-11-03：t-hom 卡已并入 t-verify（同源库组检查随 verify 作用域）
}

# 标题补充表（TOOL_REGISTRY 覆盖不到的）：独立页 / 网络页 / hash 特有标题。
SCOPE_TITLES = {
    'prescreen': '病毒样本预筛', 'build': '数据库构建',
    'meta': '公共数据检索', 'virome': 'Open-Virome',
    'download': '公共数据下载', 'refdata': '基因组查询下载',
    'samples': '样品创建 / 批量导入', 'cds_export': 'CDS / PEP 提取产物',
    'logan': 'LOGAN 溯源 / 批量提交', 'submit': '数据提交',
    'results': '样品结果 / 专项结果', 'svguest': '结果图整理（svguest）',
    # hash 卡片标题优先于其映射模块的注册表标题（如 t-variant ≠ kvsuite 标题）
    't-variant': '病毒变异分析', 't-treebuild': '进化树构建（科/属级）',
    't-sdt': 'SDT 同一性分析（属级）', 't-identify': '病毒序列提取（样本分析）',
    't-seqprep': '参考序列获取',
}


def _registry_titles():
    """TOOL_REGISTRY 的键→标题（lazy import 防循环依赖；失败不致命）。"""
    try:
        from Virus_Platform_Core.web.tools_api import TOOL_REGISTRY
        return {k: (v.get('title') or k) for k, v in TOOL_REGISTRY.items()}
    except Exception:
        return {}


def _group_scope(gid):
    """导航组 → (是否识别, 模块键列表)。

    NAV_GROUPS 是单一数据源：组内 item 带 id 的走 HASH_TO_SCOPE（在线卡
    映射为 None，跳过），带 href 的走 PATH_SCOPE；组依赖 = 各卡依赖的并集
    （保持首现顺序）。组概览页（/tools?g=xxx 无卡片 hash）走这条通路。
    """
    try:
        from Virus_Platform_Core.web.pages import NAV_GROUPS
    except Exception:
        return False, []
    g = next((x for x in NAV_GROUPS if x['id'] == gid), None)
    if g is None:
        return False, []
    mods, seen = [], set()
    for it in g.get('items', []):
        href = (it.get('href') or '').rstrip('/') or '/'
        m = (HASH_TO_SCOPE.get(it['id'])
             if it.get('id') else PATH_SCOPE.get(href))
        if m and m not in seen:
            seen.add(m)
            mods.append(m)
    return True, mods


def _group_cards(gid):
    """导航组 → [(卡片标题, 模块键或 None), ...]。

    与 _group_scope 同源（NAV_GROUPS 单一数据源），但保留**每一张卡**：
    顶栏「模块 n/n」按卡片数出分（2026-11-05 用户反馈：样本处理 6 张卡
    却显示 3/3，与页面可见模块对不上）。在线/纯链接卡的键为 None。
    """
    try:
        from Virus_Platform_Core.web.pages import NAV_GROUPS
    except Exception:
        return []
    g = next((x for x in NAV_GROUPS if x['id'] == gid), None)
    if g is None:
        return []
    out = []
    for it in g.get('items', []):
        href = (it.get('href') or '').rstrip('/') or '/'
        m = (HASH_TO_SCOPE.get(it['id'])
             if it.get('id') else PATH_SCOPE.get(href))
        label = it.get('title') or it.get('id') or it.get('href') or ''
        out.append((label, m))
    return out


def _group_card_tools(gid):
    """组内各卡的卡片专属工具（CARD_TOOLS）并集，保持首现顺序。"""
    try:
        from Virus_Platform_Core.web.pages import NAV_GROUPS
    except Exception:
        return []
    g = next((x for x in NAV_GROUPS if x['id'] == gid), None)
    if g is None:
        return []
    out = []
    for it in g.get('items', []):
        for k in CARD_TOOLS.get(it.get('id') or '', []):
            if k not in out:
                out.append(k)
    return out


def _group_label(gid):
    try:
        from Virus_Platform_Core.web.pages import NAV_GROUPS
        return next((x.get('label') for x in NAV_GROUPS if x['id'] == gid), None)
    except Exception:
        return None


def resolve_scope(path=None, hash_id=None, group=None):
    """把页面位置解析成 (模块键列表, 标题, 组id)。

    * 卡片 hash 识别成功 → 单模块列表（在线卡 = 空列表 + 卡片标题）
    * 组 id 识别成功（组概览 /tools?g=xxx）→ 组内各卡模块并集 + 组名
    * 独立页（path ∈ PATH_SCOPE）→ 单模块列表（网络页 = 空列表 + 页名）
    * 全局页（工作台/任务中心/设置…）→ 空列表 + title=None
    title 为 None 且列表为空 = 全局口径；title 非 None = 有名字的作用域
    （工具/库位按依赖出，无依赖显 '—'）。
    """
    gid = (group or '').strip()
    if gid and (path or '').rstrip('/') == '/tools':
        # 组口径只对工具页生效（组概览的唯一入口），防止任意页面带 g 改口径。
        # 2026-11-06 用户反馈：组页（/tools?g=xxx）下切到任何一张卡，顶栏
        # 环境条仍应显示**本组**口径（用户在病毒定量与共识页的变异卡上看
        # 到 1/1 · 48/50 与页面 3 个模块对不上）。组概览与卡页现在同口径：
        # 组内各卡依赖并集；单卡细粒度口径只保留给不带 g 的直链。
        found, mods = _group_scope(gid)
        if found:
            return mods, _group_label(gid), gid
    if hash_id and hash_id in HASH_TO_SCOPE:
        module = HASH_TO_SCOPE[hash_id]
        title = SCOPE_TITLES.get(hash_id)
        if title is None and module:
            title = _registry_titles().get(module)
        return ([module] if module else []), title, None
    p = (path or '').rstrip('/') or '/'
    if p in PATH_SCOPE:
        module = PATH_SCOPE[p]
        title = SCOPE_TITLES.get(module)
        if title is None and module:
            title = _registry_titles().get(module)
        return ([module] if module else []), title, None
    return [], None, None


def _disk_free_gb():
    try:
        return shutil.disk_usage(PLATFORM_ROOT).free / 1e9
    except OSError:
        return None


# 作用域 → 引擎 Python 模块（模块位的口径：本作用域引擎模块可导入）。
# 挑引擎主模块；子包用点路径。全局 collect() 本就递归导入全部平台模块，
# 这里按作用域取子集逐个 import（都已在 sys.modules，几乎零开销）。
SCOPE_MODULES = {
    'convert': ['preprocess'],
    'fastp': ['preprocess'],
    'hostremoval': ['host_removal'],
    'orf': ['orf'],
    'orfa': ['orf_annot'],
    'genoplot': ['gbdraw_plot', 'dfv_plot'],
    'primer': ['primer', 'primer_design', 'primer3_runtime', 'primer_thermo'],
    'identify': ['kunpeng'],
    'assemble': ['assembly', 'megahit_engine'],
    'contigs': ['kunpeng'],
    'sdt': ['sdt_exact'],
    'identity': ['sdt_exact', 'phylo'],
    'structcmp': ['phylo'],
    'verify': ['verify', 'bfvd_search', 'pvdb_search'],
    'consensus': ['consensus'],
    'kvsuite': ['kv_stage'],
    'align': ['phylo'],
    'quicktree': ['phylo'],
    'rdp': ['rdp5_engine'],
    'phylogeo': ['phylogeo', 'phylogeo_gif'],
    'dsrna': ['dsrna_pipeline', 'dsrna_scoring', 'dsrna_offtarget'],
    'crispr': ['crispr_design'],
    'lamp': ['lamp_design'],
    'rtt': ['treetime_rtt', 'rtt_drt'],
    'phylodyn': ['phylodyn_kit', 'phylodyn_trees', 'phylodyn_local',
                 'phylodyn_alignqc', 'phylodyn_govern'],
    'pdprep': ['phylodyn_kit'],
    'mirna': ['mirna_target'],
    'beastexport': ['beast_handoff', 'beast_io'],
    'hom': ['local_search', 'contig_annot', 'bfvd_search', 'pvdb_search'],
    # 一键链 = 成员工具引擎模块的并集
    'virchain': ['assembly', 'megahit_engine', 'kunpeng', 'verify',
                 'bfvd_search'],
    'kvchain': ['kv_stage', 'consensus'],
    'prepchain': ['preprocess', 'host_removal', 'kunpeng'],
    'annochain': ['orf', 'orf_annot', 'gbdraw_plot'],
    'cmpchain': ['compare_suite', 'gb_collection', 'phylo', 'sdt_exact'],
    # 独立页 / 网络页（web 层引擎模块）
    'prescreen': ['public_meta.prescreen_engine', 'public_meta.prescreen_plot'],
    'build': ['taxonomy', 'virus_ref', 'universal_ref'],
    'meta': ['public_meta.search_engine'],
    'virome': ['public_meta.virome'],
    'download': ['public_data'],
    'refdata': ['public_meta.host_genome', 'ncbi_download'],
    'samples': [],
    'cds_export': ['cds_export'],
    'logan': ['logan_trace', 'logan_submit'],
    'submit': ['suvtk_submit'],
    'loganfetch': [],       # 纯 HTTP 拉取预组装 contigs，无本地引擎
    'results': [],          # 结果中心（纯展示，无本地引擎）
    'svguest': [],          # 结果图整理外壳（纯展示）
}


def _scope_modules_ok(smods):
    """逐个导入作用域引擎模块，返回失败名单（全过 = 空列表）。

    全局 collect() 已把全部平台模块导进 sys.modules，这里基本是查缓存；
    个别模块首导也只花毫秒级。
    """
    bad = []
    for mn in smods:
        try:
            importlib.import_module('Virus_Platform_Core.' + mn)
        except Exception:
            bad.append(mn)
    return bad


def scoped_status(path=None, hash_id=None, group=None, base=None,
                  force=False):
    """顶栏「本模块环境」状态条 + 弹层明细。

    base: collect() 结果（省得重算）；只取其模块/数据库两段。
    全局页（模块列表与标题均空）→ 各位回退全局段，与总览页一致。
    组概览（group 识别成功）→ 依赖 = 组内各卡依赖的并集。
    Python 依赖位（2026-11-06 第二轮）：作用域口径 —— 本作用域引擎闭包
    真正 import 的第三方包（scope_pydeps_status）；无引擎或审计器失效才
    回退全局口径。force 同时传给两级。
    """
    modules, title, gid = resolve_scope(path, hash_id, group)
    data = base if base is not None else collect()
    sec = {s['id']: s for s in data.get('sections', [])}
    sec_modules, sec_dbs = sec.get('modules'), sec.get('databases')

    # ── 磁盘位（永远全局） ──
    free = _disk_free_gb()
    disk_bit = {'ok': None if free is None else free > DISK_WARN_BYTES / 1e9,
                'summary': '—' if free is None else f'{free:.1f} GB'}

    # ── Python 依赖包位（永远全局，2026-11-06 补充） ──
    py = pydeps_status(force=force)

    # ── 全局页：整行回退全局段 ──
    if not modules and title is None:
        tools_sec = sec.get('tools') or {}
        return {
            'scope': hash_id or None, 'module': None, 'title': None,
            'group': None,
            'known': True, 'ok': bool(data.get('ok')), 'warn': False,
            'bits': [
                {'id': 'modules', 'ok': bool(sec_modules and sec_modules['ok']),
                 'summary': sec_modules['summary'] if sec_modules else '—'},
                dict(py['bit']),
                {'id': 'tools', 'ok': (None if not tools_sec else not tools_sec.get('missing')),
                 'summary': tools_sec.get('summary', '—')},
                {'id': 'db', 'ok': bool(sec_dbs and sec_dbs['ok']),
                 'summary': (('就绪' if sec_dbs['ok'] else '未就绪')
                             if sec_dbs else '—')},
                {'id': 'disk', 'ok': disk_bit['ok'], 'summary': disk_bit['summary']},
            ],
            'detail': {
                'tools_list': [], 'tools_missing': tools_sec.get('missing', []),
                'fixable': tools_sec.get('fixable', {}),
                'optional': {}, 'opt_missing': [],
                'db_items': (sec_dbs or {}).get('items', []),
                'modules_missing': [i['name'] for i in (sec_modules or {}).get('items', [])],
                'pydeps': py['detail'],
                'disk_items': (sec.get('disk') or {}).get('items', []),
            },
        }

    # ── 模块位（2026-11-05 口径改为「卡片」；2026-11-06 再改：组口径只数
    #    功能卡，⚡一键链卡是包装层不算模块 —— 病毒定量与共识页可见模块
    #    = 3 张功能卡，不是带链卡的 4）──
    # 更早的口径是「去重后的引擎 Python 模块数」：样本处理 6 张卡只显示 3/3、
    # 数据资源 8 张卡显示 12/12、检测与防治 5 张卡显示 10/10 —— 用户拿页面
    # 一数就对不上。现在：
    #   · 组概览：分母 = 本组卡片数，分子 = 引擎可导入的卡片数；
    #   · 单卡 / 独立页：1/1（本模块可用），无本地引擎的页面仍显 '—'；
    #   · 全局页：保持全局导入健康口径。
    smods, _seen = [], set()
    for m in modules:
        for x in SCOPE_MODULES.get(m, []):
            if x not in _seen:
                _seen.add(x)
                smods.append(x)
    # ── Python 依赖位（2026-11-06 第二轮）：作用域口径优先 —— 本作用域
    #    引擎闭包真正 import 的第三方包（scope_pydeps_status），无引擎或
    #    审计器失效才回退全局口径。 ──
    py_scope = scope_pydeps_status(
        smods, scope_key=(gid or hash_id or (modules[0] if modules else '')),
        force=force)
    if py_scope:
        py = py_scope
    if gid:
        cards = [(lb, m) for (lb, m) in _group_cards(gid)
                 if not str(lb).startswith('⚡')]
        bad_cards = []
        for label, mkey in cards:
            engines = SCOPE_MODULES.get(mkey, []) if mkey else []
            if engines and _scope_modules_ok(engines):
                bad_cards.append(label)
        n = len(cards) or 1
        scope_bad = bad_cards
        mod_bit = {'ok': not bad_cards,
                   'summary': '%d/%d' % (n - len(bad_cards), n)}
        mod_scanned = n
    elif title is not None:
        scope_bad = _scope_modules_ok(smods) if smods else []
        mod_bit = ({'ok': None, 'summary': '—'} if not smods else
                   {'ok': not scope_bad,
                    'summary': '1/1' if not scope_bad else '0/1'})
        mod_scanned = 1 if smods else 0
    else:
        scope_bad = []
        mod_bit = {'ok': bool(sec_modules and sec_modules['ok']),
                   'summary': sec_modules['summary'] if sec_modules else '—'}
        mod_scanned = len(smods)
    global_missing = [i['name'] for i in (sec_modules or {}).get('items', [])]

    # ── 外部工具位：作用域内各模块/页面声明的工具键并集，逐一探状态 ──
    cfg = get_config()
    st = cfg.tool_status()
    keys, seen = [], set()
    for m in modules:
        for k in tools_for(m):
            if k not in seen:
                seen.add(k)
                keys.append(k)
    # 卡片专属工具（如 t-variant 的 bcftools/SnpEff；注册键级表里不能声明，
    # 否则 kvsuite 阶段会被错误地要求这两个工具）
    for k in (_group_card_tools(gid) if gid
              else CARD_TOOLS.get(hash_id or '', [])):
        if k not in seen:
            seen.add(k)
            keys.append(k)
    tools_list = [{'key': k, 'ok': bool(st.get(k))} for k in keys]
    missing = [k for k, it in zip(keys, tools_list) if not it['ok']]
    optional = {}
    for m in modules:
        optional.update(OPTIONAL_TOOLS.get(m, {}))
    opt_missing = [k for k in optional if not st.get(k)]
    tools_bit = ({'ok': None, 'summary': '—'} if not keys
                 else {'ok': not missing,
                       'summary': f'{len(keys) - len(missing)}/{len(keys)}'})

    # ── 数据库位：作用域内各模块 SCOPE_DBS 的并集（软依赖只在「全部来源
    #    都是软」时保留软判定；一处硬依赖即按硬算，如 sample 组里
    #    hostremoval 的宿主库是硬的、prepchain 是软的 → 组口径按硬算） ──
    db_items_all = (sec_dbs or {}).get('items', [])
    by_name = {'taxonomy': 'NCBI Taxonomy', 'host': '宿主库 host_db',
               'virus': '病毒库 virus_db', 'kvref': '病毒参考库 virusref',
               'homolog': '同源验证库组'}
    deps_map = {}
    for m in modules:
        for dbk, soft in SCOPE_DBS.get(m, []):
            if dbk in deps_map:
                deps_map[dbk] = deps_map[dbk] and soft
            else:
                deps_map[dbk] = soft
    db_items, db_ok, db_warn = [], True, False
    if not deps_map:
        db_bit = {'ok': None, 'summary': '—'}
    else:
        for dbk, soft in deps_map.items():
            it = next((x for x in db_items_all if x['name'] == by_name[dbk]), None)
            if it is None:
                continue
            it = dict(it)
            it['soft'] = soft
            db_items.append(it)
            if not it.get('ok'):
                if soft:
                    db_warn = True
                else:
                    db_ok = False
        db_bit = {'ok': None if (not db_items) else (True if db_ok else False),
                  'summary': ('—' if not db_items else
                              ('就绪' if db_ok else '未就绪'))}

    ok = bool(mod_bit['ok'] and tools_bit.get('ok') is not False
              and db_bit.get('ok') is not False)
    warn = bool((tools_bit.get('ok') is False) or db_warn
                or opt_missing or (not mod_bit['ok']))
    mod_bit = dict(mod_bit, id='modules')
    tools_bit = dict(tools_bit, id='tools')
    db_bit = dict(db_bit, id='db')
    disk_bit = dict(disk_bit, id='disk')
    return {
        'scope': hash_id or gid or (modules[0] if modules else None),
        'module': modules[0] if modules else None,
        'title': title, 'group': gid,
        'known': True, 'ok': ok, 'warn': warn,
        'bits': [mod_bit, dict(py['bit']), tools_bit, db_bit, disk_bit],
        'detail': {
            'tools_list': tools_list, 'tools_missing': missing,
            'fixable': {}, 'optional': optional, 'opt_missing': opt_missing,
            'db_items': db_items,
            # 模块位按作用域出：scope_bad = 本作用域引擎模块失败名单；
            # 全局仍有失败时弹层补一条说明（不影响本模块的也如实带出）
            'modules_missing': scope_bad,
            'modules_global_missing': global_missing,
            'modules_scanned': mod_scanned,
            'pydeps': py['detail'],
            'disk_items': (sec.get('disk') or {}).get('items', []),
        },
    }
