# -*- coding: utf-8 -*-
"""运行前依赖自愈：模块要用的外部工具缺失时，自动从插件源安装。

设计要点
--------
1. **每个模块声明自己要用哪些工具键**（MODULE_TOOLS），这是唯一真源：
   工具页与管道阶段都从这张表取需求，不再各自散落判断。
2. **先检测、再自愈、后运行**：`ensure_tools()` 在任务真正启动前调用；
   缺失项若能由插件源提供，就当场下载安装（带 sha256 校验），装完重扫
   `Cfg.tools` 再复核一遍。装不上才抛错——错误信息里给出可操作指引，
   而不是让任务跑到一半才 FileNotFoundError。
3. **全局可见**：自愈结果写入 `run/plugin_autofix.json`，`selfcheck` 的
   「外部工具」段会读它，把「本次会话自动装过什么」显式列给用户，
   避免"插件被悄悄装上、用户不知道平台改了什么"。

为什么不放在每个 job 里各写一遍
------------------------------
30 个 job × 各自的引擎，散着判断必然漏。集中在一张表 + 一个入口，
新增工具页时只加一行映射；漏声明由 tests/test_module_preflight.py 兜住。
"""
import json
import os
import threading
import time

from Virus_Platform_Core import plugin_manager as pm
from Virus_Platform_Core.config import DIRS, PLATFORM_ROOT, RUN_ROOT

# ----------------------------------------------------------------------
# 模块 → 工具键（唯一真源）
# 键与 Cfg.tools / 插件 provides 一致；工具页键与 pipeline 阶段键都在这。
# ----------------------------------------------------------------------
MODULE_TOOLS = {
    # ── 工具页（TOOL_REGISTRY 的键）──────────────────────────────
    'convert': ['seqkit'],
    'fastp': ['fastp'],
    'hostremoval': ['seqkit', 'minibwa', 'kunpeng'],
    'orf': ['orfipy'],
    # 2026-11-05 核对：功能注释默认 DIAMOND → MMseqs2 → BLASTp 自动降级
    # （见 orf_annot.py 文件头）。嵌套列表 = 「任一组」：组内**任一**工具
    # 可用即满足（旧声明 ['diamond'] 会把只有 MMseqs2/BLASTp 的机器硬拦在
    # 预检外，与引擎降级链矛盾）。blastp 分支建库用的 makeblastdb 仍是
    # 可选工具（只在选中 blastp 时需要，见 OPTIONAL_TOOLS）。
    'orfa': [['diamond', 'mmseqs', 'blastp']],
    'genoplot': ['gbdraw'],
    'primer': ['blastn', 'makeblastdb'],
    'identify': ['kunpeng', 'seqkit'],
    'assemble': ['spades', 'makeblastdb', 'blastn'],
    'contigs': ['kunpeng', 'seqkit'],
    'sdt': ['mafft'],
    'identity': ['mafft'],
    'structcmp': ['mafft', 'trimal', 'fasttree'],
    'verify': ['blastn', 'diamond', 'mmseqs'],
    # hom（单条深查，2026-11-03 自 t-hom 卡升级为正式工具）：引擎键与 verify
    # 同源——blastn 走病毒参考核酸库，diamond 走 PVDB 蛋白库；mmseqs 只喂
    # BFVD 可选域证据（库缺失 job 内自动降级，不失败）。
    'hom': ['blastn', 'diamond', 'mmseqs'],
    # consensus（共识序列页）只有 minibwa 一个硬依赖：回贴由 minibwa 承担，
    # 共识调用是 Python 重写的 ViralConsensus 算法，既不用 samtools 也不用
    # viral_consensus（见 consensus.py 文件头）。
    'consensus': ['minibwa'],
    # kvsuite / kvchain 的回贴与计数引擎是 minibwa（salmon 是可选定量
    # 引擎，见 OPTIONAL_TOOLS）。这里曾写 minimap2 —— 2026-09-08 换引擎后
    # 忘了改，导致 preflight 会去要一个全平台都没有调用点的工具。
    # 注意：变异段（bcftools/SnpEff）不在本键里 —— t-variant 与 t-kvsuite
    # 共用 'kvsuite' 注册键，但本卡/管道阶段只跑 identify+filter；变异段的
    # 工具走 CARD_TOOLS（展示口径）与 kvchain（一键全流程，真的会跑变异）。
    'kvsuite': ['minibwa', 'samtools', 'seqkit'],
    'align': ['mafft', 'trimal'],
    'quicktree': ['mafft', 'trimal', 'fasttree', 'raxml-ng'],
    'rdp': ['RDP5CL'],
    'phylogeo': ['lsd2'],
    'dsrna': ['dsRNAmax'],
    'crispr': ['blastn', 'makeblastdb'],
    'lamp': [],   # 纯 Python 枚举 + ViennaRNA 可选
    'mirna': ['srna-target'],
    'phylodyn': ['lsd2'],          # rscript 是 TreeDater 可选分支，见 OPTIONAL_TOOLS
    'beastexport': [],
    'rtt': [],
    'loganfetch': [],   # 2026-11-05 代并行开发补声明（test_module_preflight 契约）
    'pdprep': [],
    # pdrename / pdgroup 已于 2026-09-21 归档（注册表已摘），不再登记
    # ── 一键链式（web/chains.py）：各自是上面若干工具页的组合 ─────
    # virchain = ①病毒组装 + ②病毒鉴定 + ③候选验证
    # （2026-11-03 起 ① 识别提取已移出本链、归样本处理组；kunpeng/seqkit
    #   仍保留 —— ② 病毒鉴定（contigs）用）
    'virchain': ['kunpeng', 'seqkit', 'spades', 'makeblastdb',
                 'blastn', 'diamond', 'mmseqs'],
    # kvchain = kvsuite 全流程（鉴定→过滤→共识→绘图→变异，固定 stage=all）
    # 变异段工具与 kvsuite 同源，必须一并声明（2026-11-05 核对）。
    'kvchain': ['minibwa', 'samtools', 'seqkit', 'bcftools', 'snpeff'],
    # prepchain = 转换 → 质控 → 转 FASTA → 宿主去除 → 病毒序列提取（2026-11-03 并入第⑤步；kunpeng/seqkit 已在依赖里）
    'prepchain': ['seqkit', 'fastp', 'minibwa', 'kunpeng'],
    # annochain = ORF 预测（pyrodigal 默认；orfipy 历史兜底）→ 功能注释
    # （orf_annot 默认 DIAMOND）→ 基因组图谱（gbdraw）。2026-11-05 核对：
    # 旧声明的 blastn/makeblastdb 与注释实现不符，改为 diamond。
    'annochain': ['orfipy', 'diamond', 'gbdraw'],
    # cmpchain = 比较基因组一键链（参考获取→CDS/PEP+保守基因提取→
    # MAFFT+trimAl→建树→SDT），引擎在 compare_suite.py
    'cmpchain': ['mafft', 'trimal', 'fasttree', 'raxml-ng'],
    # ── 管道阶段（pipeline.STAGE_NAMES 的键）─────────────────────
    'subsample': ['seqkit'],
    'fq2fa': ['seqkit'],
    'host': ['seqkit', 'minibwa', 'kunpeng'],
    'hostana': ['kunpeng'],
    # assembly 阶段内部还会用 makeblastdb 建骨架库（见 assembly.py）
    'assembly': ['spades', 'makeblastdb', 'blastn'],
    'phylo': ['mafft', 'trimal', 'fasttree', 'raxml-ng'],
    'gbdraw': ['gbdraw'],
    'report': [],
}

# ----------------------------------------------------------------------
# 独立页面（不在 TOOL_REGISTRY / STAGE_REGISTRY 里的导航页）→ 工具键
# 只用于顶栏「本模块环境」的展示口径（selfcheck.scoped_status），
# 不参与运行前 preflight（这些页面的入口各自做显式检查）。
# 2026-11-05 核对：数据资源组的下载/查询/建库页此前完全没有声明，
# 顶栏一直显示「外部工具 —」，与实际依赖不符。
# ----------------------------------------------------------------------
PAGE_TOOLS = {
    # 公共数据下载：SRA .sra → FASTQ 用随包 sracha（缺了才回退 fasterq-dump，
    # 后者不在探测表）；aria2c 见 OPTIONAL_TOOLS。
    'download': ['sracha'],
    # 基因组查询下载：NCBI datasets CLI（不可用时自动走 E-utilities 回退）
    'refdata': ['datasets'],
    # 数据库构建：宿主库 / 病毒库都走 kunpeng build_db
    'build': ['kunpeng'],
    # 数据提交：.sqn 生成硬依赖 table2asn（suvtk_submit 预检会点名）
    'submit': ['table2asn'],
}


def _iter_groups(spec):
    """把模块的工具声明拆成「组」：每一项要么是单键，要么是任一组。

    MODULE_TOOLS 里嵌套的 list/tuple 表示「组内任一可用即可」（如 orfa 的
    DIAMOND/MMseqs2/BLASTp 降级链）。所有消费方都走这里，避免各自猜结构。
    """
    for item in spec or ():
        if isinstance(item, (list, tuple, set)):
            group = [str(k) for k in item if k]
            if group:
                yield group
        elif item:
            yield [str(item)]


def _flat_keys(spec):
    """声明里的全部工具键（去重、保序）——供需要「逐键」的展示/守卫使用。"""
    out = []
    for group in _iter_groups(spec):
        for k in group:
            if k not in out:
                out.append(k)
    return out


def tools_for(module):
    """模块/页面的工具依赖（顶栏环境条口径：工具页 + 独立页面）。

    「任一组」只返回组内首选引擎：顶栏按逐键探活展示，若把三个备选全列出来，
    只有 MMseqs2 的机器会被误报成「缺 diamond、blastp」。预检是否放行由
    missing_tools 按组判定，不受这里影响。
    """
    spec = MODULE_TOOLS.get(module) if module in MODULE_TOOLS \
        else PAGE_TOOLS.get(module)
    return [g[0] for g in _iter_groups(spec)]


# 卡片专属工具（展示口径）：同一个注册键按 stage 分流时，注册键级的
# MODULE_TOOLS 只能声明公共部分，卡片额外的依赖走这里。
#   t-variant / t-kvsuite 共用 'kvsuite' 注册键，但只有变异卡需要
#   bcftools（mpileup+call）与 SnpEff（缺任一变异段直接空结果）；
#   放进 MODULE_TOOLS 会让 kvsuite 阶段（identify+filter）的 preflight
#   错误地要求这两个工具。kvchain（一键全流程）真的会跑变异，已在
#   MODULE_TOOLS['kvchain'] 里声明。
CARD_TOOLS = {
    't-variant': ['bcftools', 'snpeff'],
}


# 可选工具：缺了只降级对应子功能，不该阻断整个模块。
# 与 MODULE_TOOLS 分开：那里是「没有就跑不了」，这里是「没有就少一个分支」。
# 格式 {模块: {工具键: 缺了之后的影响}}——影响文案直接进日志/自检，让用户
# 知道少了什么能力，而不是莫名其妙看不到某个结果。
OPTIONAL_TOOLS = {
    'phylodyn': {
        'rscript': 'TreeDater 定年分支不可用（需 R + treedater 包）；'
                   'LSD2 定年与其余分析不受影响',
    },
    'genoplot': {
        'gbdraw': 'gbdraw 引擎不可用，自动回退纯 Python 引擎 dna_features_viewer'
                  '（出图样式略简，功能不受影响）',
    },
    'orfa': {
        # mmseqs/blastp 已进 MODULE_TOOLS 的「任一组」，不再是可选降级项；
        # 只剩 blastp 分支建库要用的 makeblastdb 仍按可选处理。
        'makeblastdb': 'BLASTp 兜底分支无法建库（只影响选中 blastp 引擎时）',
    },
    'download': {
        'aria2c': '多线程分段下载不可用，回退内置 HTTP 单线程（大文件慢一些）',
    },
    'assemble': {
        'megahit': 'MEGAHIT 组装引擎不可用，只能用 SPAdes',
    },
    'virchain': {
        'megahit': 'MEGAHIT 组装引擎不可用，一键分析只能用 SPAdes',
    },
    'kvsuite': {
        'salmon': 'salmon 定量引擎不可用，需改用 minibwa 比对引擎',
        'pandepth': '覆盖度/深度后端不可用：不产出比对 BAM 与深度表',
        'strawberry-perl': 'SNPGenie 群体遗传学分支不可用（需 Perl 运行时）',
        'snpgenie': 'SNPGenie 群体遗传学分支不可用',
        'jre-snpeff': 'SnpEff 需要 Java 21+；随包 jre 缺失时回退系统 JDK/PATH',
    },
    'kvchain': {
        'salmon': 'salmon 定量引擎不可用，需改用 minibwa 比对引擎',
        'pandepth': '覆盖度/深度后端不可用：不产出比对 BAM 与深度表',
        'strawberry-perl': 'SNPGenie 群体遗传学分支不可用（需 Perl 运行时）',
        'snpgenie': 'SNPGenie 群体遗传学分支不可用',
        'jre-snpeff': 'SnpEff 需要 Java 21+；随包 jre 缺失时回退系统 JDK/PATH',
    },
}

# 插件源里没有、只能人工装或本机自带的键（自愈时直接给出指引）
_NO_PLUGIN_HINT = {
    'rscript': 'R 脚本（LSD2 定年辅助）：请安装 R 并加入 PATH，'
               '或在 platform.json 的 tools.rscript 指定路径',
    'gbdraw': 'gbdraw：pip install git+https://github.com/satoshikawato/gbdraw.git'
              '（未装时基因组图会自动回退纯 Python 引擎，不影响运行）',
    'orfipy': 'orfipy：pip install orfipy（随 requirements.txt 安装）',
}

# 落点必须跟 RUN_ROOT 走：DIRS 里没有 'run' 这个键（只有 results/tool_runs
# 等子目录），旧写法 `DIRS.get('run') or <平台根>/run` 的 or 分支**永远生效**，
# 于是 VP_RUN_ROOT 隔离时它照样往真实 run/ 写（测试产物污染用户目录的漏点）。
_AUTOFIX_LOG = os.path.join(RUN_ROOT, 'plugin_autofix.json')
_lock = threading.Lock()


def _log_path():
    d = os.path.dirname(_AUTOFIX_LOG)
    if d:
        os.makedirs(d, exist_ok=True)
    return _AUTOFIX_LOG


def record_autofix(module, installed, failed):
    """把一次自愈结果落盘（供 selfcheck / 设置页展示）。"""
    if not installed and not failed:
        return
    with _lock:
        p = _log_path()
        try:
            data = json.load(open(p, encoding='utf-8')) if os.path.isfile(p) else {}
        except (OSError, ValueError):
            data = {}
        rec = data.setdefault('modules', {})
        rec[module] = {'installed': installed, 'failed': failed,
                       'at': time.strftime('%Y-%m-%d %H:%M:%S')}
        data['last'] = time.strftime('%Y-%m-%d %H:%M:%S')
        try:
            tmp = p + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
            os.replace(tmp, p)
        except OSError:
            pass


def read_autofix_log():
    """读自愈记录（selfcheck / 设置页用）。返回 {module: {...}} 或 {}。"""
    try:
        with open(_log_path(), encoding='utf-8') as f:
            return (json.load(f) or {}).get('modules') or {}
    except (OSError, ValueError):
        return {}


def missing_tools(cfg, module):
    """模块当前缺哪些**必需**工具键（可选工具不计入，见 OPTIONAL_TOOLS）。

    「任一组」按组判定：组内任一可用即不报缺；整组都缺时把组内键全部
    报出（错误文案才能点名 DIAMOND / MMseqs2 / BLASTp 三个备选）。
    """
    st = cfg.tool_status()
    missing = []
    for group in _iter_groups(MODULE_TOOLS.get(module, [])):
        if not any(st.get(k) for k in group):
            missing.extend(k for k in group if k not in missing)
    return missing


def missing_optional(cfg, module):
    """缺哪些**可选**工具键，返回 {键: 影响说明}（只提示，不阻断）。"""
    opts = OPTIONAL_TOOLS.get(module, {})
    st = cfg.tool_status()
    return {k: why for k, why in opts.items() if not st.get(k)}


def ensure_tools(cfg, module, auto_install=True, log=None, timeout_s=600):
    """运行前自愈：缺的工具尽量用插件源补齐。

    返回 {'missing': 原始缺失键, 'installed': 装成功的插件,
          'still_missing': 装完仍缺的键, 'hints': {键: 指引}}。

    auto_install=False 时只检测不安装（供「仅检查」入口与单测用）。
    安装失败不抛异常——把结果如实返回，由调用方决定是否中止。
    """
    miss = missing_tools(cfg, module)
    result = {'missing': miss, 'installed': [], 'still_missing': [],
              'hints': {}, 'optional_missing': missing_optional(cfg, module)}
    if not miss:
        return result

    # 有指引的键（插件源里没有）先记下，避免白跑一趟下载
    hints = {k: _NO_PLUGIN_HINT[k] for k in miss if k in _NO_PLUGIN_HINT}

    # log 可能是 TaskLogger（有 .log）也可能是普通 callable（CLI/测试传进来
    # 的函数），2026-10-26：此前一律当 callable 调用，传 TaskLogger 时
    # 抛 TypeError: 'TaskLogger' object is not callable，并把原始错误盖住。
    def _emit(msg):
        if not log:
            return
        try:
            _f = getattr(log, 'log', None) or log
            _f(msg)
        except Exception:
            pass

    if auto_install:
        try:
            items, err = pm.available_from_index(cfg, force=True)
            if err:
                _emit(f'插件源不可用（{err}），跳过自动安装')
            else:
                # 键 → 提供它的插件（一个插件可能提供多个键，如 blast 4 个）
                need_plugins = []
                for it in items:
                    prov = it.get('provides') or {}
                    if any(k in miss for k in prov):
                        need_plugins.append(it)
                for p in need_plugins:
                    if p.get('installed') and not p.get('needed'):
                        continue
                    try:
                        _emit(f'自动安装插件 {p["name"]}'
                              f'（{p.get("size", 0)/1048576:.1f}MB）…')
                        pm.install_urls(p.get('urls') or [p.get('url')],
                                        expected_sha256=p.get('sha256'))
                        result['installed'].append(p['name'])
                    except Exception as e:      # 单包失败不拦其他包
                        _emit(f'插件 {p["name"]} 安装失败: {e}')
        except Exception as e:
            _emit(f'自动安装流程异常: {e}')

    # 装完重扫（refresh_plugins 会更新 Cfg.tools 的填缺层）
    if result['installed']:
        try:
            cfg.refresh_plugins()
        except Exception:
            pass

    still = missing_tools(cfg, module)
    result['still_missing'] = still
    result['hints'] = {k: hints.get(k) or
                       '插件源未提供该工具，请手动安装或到设置页指定路径'
                       for k in still}
    record_autofix(module, result['installed'], still)
    return result


def format_error(module, result):
    """自愈失败时的可操作错误文案（直接给用户看）。

    2026-11-02：此前无条件追加「已自动安装：…」，而 installed 为空时
    会打印「已自动安装：srna-target」——实际插件源根本没提供该工具、
    什么都没装，文案却让人以为装上了只是没生效（用户排障时按这条查了
    半天插件）。现在只在 installed 非空时才输出这一行。
    """
    lines = [f'模块「{module}」缺少必要外部工具：'
             + '、'.join(result['still_missing'])]
    groups = [g for g in _iter_groups(MODULE_TOOLS.get(module, []))
              if len(g) > 1]
    if groups:
        lines.append('  以下任一组内任一工具可用即可（引擎会自动降级）：'
                     + '；'.join(' / '.join(g) for g in groups))
    for k, hint in result['hints'].items():
        lines.append(f'  · {k}：{hint}')
    if result['installed']:
        lines.append('  已自动安装：' + '、'.join(result['installed']))
    lines.append('  可在「设置 → 插件管理 → 插件源」查看与手动安装。')
    return '\n'.join(lines)
