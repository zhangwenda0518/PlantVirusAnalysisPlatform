#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
kv_common.py — known_virus_suite 公共层
=====================================
参考加载、日志、I/O、外部工具探测。

设计来源: virome_analysis_pipeline/batch_virus_depth.py 的
_load_reference_lengths / _load_ref_info_smart / setup_logging 等
（复制后改造，原文件不动）
"""

import gzip
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

# ── 平台适配 ──────────────────────────────────────────────
IS_WINDOWS = os.name == 'nt'


def smart_open(path, mode='rt'):
    """透明处理 .gz"""
    path = str(path)
    return gzip.open(path, mode, encoding=None if 'b' in mode else 'utf-8') if path.endswith('.gz') else open(path, mode, encoding=None if 'b' in mode else 'utf-8')


def which_tool(name, extra_dirs=None):
    """
    工具探测，兼顾 Windows 上的 .exe 和平台自带 tools 目录。
    返回绝对路径，找不到返回 None。
    """
    p = shutil.which(name)
    if p:
        return p
    # Windows: shutil.which 对没有 .exe 后缀的名字也能找到，但为稳妥再试一次
    if IS_WINDOWS:
        for suffix in ('.exe', '.bat', '.cmd'):
            p = shutil.which(name + suffix)
            if p:
                return p
    for d in (extra_dirs or []):
        d = Path(d)
        for cand in (d / name, d / f"{name}.exe"):
            if cand.is_file():
                return str(cand)
    return None


def platform_root() -> Path:
    """平台根目录（本引擎位于 <PLATFORM_ROOT>/Virus_Platform_Core/known_virus_suite/）。

    冻结分发（PyInstaller exe）：平台根 = **exe 所在目录**，与
    `config._detect_platform_root()` 同口径。这一支必须先判 —— 打包后本模块的
    `__file__` 落在 `<exe目录>/_internal/Virus_Platform_Core/...` 里，上溯既
    找不到 `Virus_Platform_Core/` 也找不到 `app.py`，最终兜底返回 `_internal`，
    于是 `ToolRegistry.probe()` 去找 `_internal/3rd/tools`（真实位置是
    `<exe目录>/3rd/tools`）→ 8 个外部工具全部探测失败、`tools.require()` 直接
    拒绝运行。注意：开发机上 dist/ 嵌在源码树内时，上溯会"碰巧"命中源码根而
    掩盖此问题，只有在用户机器（dist 单独拷走）才暴露 —— 实测于 2026-09-11。

    源码运行：从本文件所在目录逐级上溯，取同时含 Virus_Platform_Core/ 与 app.py
    的那一层。这样引擎换目录（包内子目录、或退回旧的顶层布局）都无需再改这里。
    """
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent
    p = Path(__file__).resolve().parent
    for _ in range(6):
        if (p / 'Virus_Platform_Core').is_dir() and (p / 'app.py').is_file():
            return p
        if p.parent == p:
            break
        p = p.parent
    return Path(__file__).resolve().parent.parent.parent


class ToolRegistry:
    """
    记录本模块需要的所有外部工具，支持缺件降级判断。

    用法:
        reg = ToolRegistry(logger)
        reg.probe(extra_dirs=[...])
        if reg.has('minibwa'): ...
    """

    NEEDED = {
        'salmon': 'salmon 定量引擎（identify；--writeBam 出映射位点）',
        'minibwa': 'minibwa 真比对（共识段内部固定使用）',
        'mafft': 'mafft 多序列比对（共识迭代）',
        'samtools': 'samtools（SAM/BAM 处理与覆盖度统计）',
        'pandepth': 'pandepth（覆盖度/深度统计，口径金标准）',
        'viral_consensus': 'viral_consensus（共识序列构建，对齐原管线）',
        'bcftools': 'bcftools（变异检出 caller，替代 freebayes/lofreq/ivar）',
    }

    # 工具可执行文件名与所在子目录（Windows 本机 tools\ 布局）
    TOOL_HINTS = {
        'salmon':   ('salmon',   r'salmon2'),
        'minibwa':  ('minibwa',  r'bin'),
        'mafft':    ('mafft',    r'mafft-win\ms\bin'),
        'samtools': ('samtools', r'samtools\bin'),
        'pandepth': ('pandepth', r'pandepth'),
        'viral_consensus': ('viral_consensus', r'viral_consensus'),
        'bcftools': ('bcftools', r'bcftools\bin'),
    }

    def __init__(self, logger=None):
        self.logger = logger or logging.getLogger('kv')
        self.paths = {}
        self.env = None

    def probe(self, extra_dirs=None):
        # 外部工具搜索目录：新布局 3rd/tools、3rd/bin 优先，旧根目录布局回退
        # （2026-09-10 目录整理把 tools/ bin/ 收进 3rd/）
        root = platform_root()
        bases = []
        for rel in ('3rd/tools', '3rd/bin', 'tools', 'bin'):
            d = root / rel
            if d.is_dir():
                bases.append(d)
        auto = []
        for base in bases:
            auto.append(str(base))
            for _exe, sub in self.TOOL_HINTS.values():
                d = base / sub
                if d.exists():
                    auto.append(str(d))
        dirs = list(dict.fromkeys(auto + [str(Path(d)) for d in (extra_dirs or [])]))
        for name in self.NEEDED:
            exe = self.TOOL_HINTS.get(name, (name, ''))[0]
            self.paths[name] = which_tool(exe, dirs)
        self.env = self._build_env(root)
        return dict(self.paths)

    def _build_env(self, root):
        """构造子进程环境：bcftools 插件目录 + 工具目录入 PATH。

        bcftools 的 +插件 与某些子命令需要 BCFTOOLS_PLUGINS 才能找到
        libexec/bcftools 下的插件；Windows 下不设会直接报插件未找到。
        PATH 里追加工具目录，让 samtools/bcftools 能互相调用。
        """
        env = dict(os.environ)
        for rel in ('3rd/tools', 'tools'):
            plugins = Path(root) / rel / 'bcftools' / 'libexec' / 'bcftools'
            if plugins.is_dir():
                env['BCFTOOLS_PLUGINS'] = str(plugins)
                break
        extra = [str(Path(p).parent) for p in self.paths.values() if p]
        if extra:
            env['PATH'] = os.pathsep.join(
                list(dict.fromkeys(extra)) + [env.get('PATH', '')])
        return env

    def has(self, name):
        return bool(self.paths.get(name))

    def require(self, *names):
        """缺件直接报错，附安装提示"""
        missing = [n for n in names if not self.has(n)]
        if missing:
            lines = [f"缺少必要工具: {', '.join(missing)}"]
            for m in missing:
                lines.append(f"  - {m}  ({self.NEEDED.get(m, '')})")
            raise RuntimeError('\n'.join(lines))
        return True

    def report(self):
        rows = []
        for name, desc in self.NEEDED.items():
            p = self.paths.get(name)
            rows.append(f"  [{'OK' if p else 'MISS':>4}] {name:<12} {desc}")
            if p:
                rows[-1] += f"\n         -> {p}"
        return '\n'.join(rows)


# ── 日志 ──────────────────────────────────────────────────
def setup_logger(out_dir, verbose=False, tag='kv'):
    """文件 + 控制台双通道日志"""
    out_dir = Path(out_dir)
    logs_dir = out_dir / 'logs'
    logs_dir.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(tag)
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    logger.handlers = []
    logger.propagate = False

    fh = logging.FileHandler(logs_dir / f"{tag}_{time.strftime('%Y%m%d_%H%M%S')}.log",
                             mode='a', encoding='utf-8')
    fh.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    logger.addHandler(fh)

    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(logging.Formatter('[%(asctime)s] %(levelname)s - %(message)s', '%H:%M:%S'))
    logger.addHandler(ch)

    return logger, logs_dir


def fmt_time(seconds):
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{int(h)}h {int(m)}m {int(s)}s"
    if m > 0:
        return f"{int(m)}m {int(s)}s"
    return f"{s:.1f}s"


# ── 参考库加载 ────────────────────────────────────────────
def load_ref_lengths(ref_fasta, logger=None):
    """读参考 FASTA，返回 {accession: length}"""
    lengths = {}
    cur_id, cur_len = None, 0
    with smart_open(ref_fasta, 'rt') as f:
        for line in f:
            if line.startswith('>'):
                if cur_id:
                    lengths[cur_id] = cur_len
                cur_id = line.strip().split()[0][1:]
                cur_len = 0
            else:
                cur_len += len(line.strip())
        if cur_id:
            lengths[cur_id] = cur_len
    if logger:
        logger.info(f"参考库载入: {len(lengths)} 条序列, 合计 {sum(lengths.values())/1e6:.2f} Mb")
    return lengths


def ensure_fai(ref_fasta, samtools=None, logger=None, env=None):
    """确保 ref_fasta 的 .fai 存在**且与当前换行宽度自洽**；必要时重建。

    2026-11-02：参考库曾带着一份对不上号的 .fai（首行
    `NC_116488.1 2302 13 60 39` —— linebases=60 却 linewidth=39，典型是
    FASTA 换行宽度被改写过、索引没重建），变异段 bcftools mpileup 直接
    `[E::fai_insert_index] Malformed line: width (39) less than base count
    (60)` 整段失败。samtools 只会"不存在才建"，所以坏索引会一直留着；
    这里补一层自检：宽度不合物理常识（行宽 < 碱基数，或 > 碱基数+2）就重建。

    返回 True 表示 .fai 可用（或已重建成功）；samtools 缺失且索引也不可用
    时返回 False（调用方自行决定是否致命）。
    """
    ref_p = Path(str(ref_fasta))
    fai = Path(str(ref_fasta) + '.fai')
    bad = not fai.exists()
    if not bad:
        try:
            with open(fai, 'r', encoding='utf-8', errors='replace') as fh:
                first = fh.readline().rstrip('\n').split('\t')
            if len(first) < 5:
                bad = True            # 空表/半截写入
            else:
                linebases = int(first[3])
                linewidth = int(first[4])
                # 物理约束：每行字节数 = 碱基数 + 1(LF) 或 +2(CRLF)
                bad = (linebases <= 0 or linewidth < linebases
                       or linewidth > linebases + 2)
        except (OSError, ValueError):
            bad = True
    if not bad:
        return True
    if not samtools:
        samtools = which_tool('samtools') or 'samtools'
    if logger:
        logger.warning('参考库 .fai 缺失或与 FASTA 换行不自洽，重建: %s', ref_p)
    try:
        p = subprocess.run([str(samtools), 'faidx', str(ref_p)],
                           capture_output=True, text=True,
                           encoding='utf-8', errors='replace', env=env,
                           creationflags=getattr(
                               subprocess, 'CREATE_NO_WINDOW', 0))
    except OSError as e:
        if logger:
            logger.error('samtools faidx 无法执行（%s），.fai 仍不可用', e)
        return False
    if p.returncode != 0:
        if logger:
            logger.error('samtools faidx 失败(rc=%s): %s',
                         p.returncode, ((p.stderr or '').strip() or '')[-300:])
        return False
    if logger:
        logger.info('  参考库 .fai 已重建: %s', fai)
    return fai.exists()


def load_ref_sequences(ref_fasta, only=None):
    """
    读参考 FASTA 序列本体。only 为 accession 集合时只保留这些。
    返回 {accession: sequence}
    """
    seqs = {}
    cur_id, buf = None, []
    with smart_open(ref_fasta, 'rt') as f:
        for line in f:
            if line.startswith('>'):
                if cur_id and (only is None or cur_id in only):
                    seqs[cur_id] = ''.join(buf).upper()
                cur_id = line.strip().split()[0][1:]
                buf = []
            else:
                buf.append(line.strip())
        if cur_id and (only is None or cur_id in only):
            seqs[cur_id] = ''.join(buf).upper()
    return seqs


# ref_info 列名同义词表（对齐原管线 _load_ref_info_smart）
ACC_SYNONYMS = ['Accession', 'accession', 'Virus GENBANK accession', 'ID']
TAX_SYNONYMS = ['Taxid', 'taxonomy_id', 'taxid']
SP_SYNONYMS = ['Species_NCBI', 'Species_ICTV', 'taxonomy', 'Species', 'description', 'Virus name(s)']
SEG_SYNONYMS = ['Segment', 'segment']

# ── 物种名消毒 ────────────────────────────────────────────
# 背景（2026-11-02 实测）：virusref_db 的 reference.ref_info.tsv 里有 30 条
# 「按 accession 直接下载、未经 taxon 注释」的 GenBank 记录，它们的
# Species_NCBI 与 GenBank_Title 两列填的是同一串标题，形如
#   "DQ000985.1 Pepino mosaic virus isolate Ch2, complete genome"
# Species_ICTV 为空。旧实现把它原样当作物种名写进 Species 列，导致
#   · 同一物种（Pepino mosaic virus）被拆成 6 个「物种」；
#   · 下游 _genus_of / 同属近缘种比较全部失配；
#   · 汇总表按 Species 分组会把 159 行拆成上百个伪物种。
# 这里在建表阶段就把标题还原成物种名，并补回空着的 ICTV 列（同种其它
# 条目里能查到 ICTV 名的，直接借过来；查不到就置空，绝不编造）。
_TITLE_ACC_PREFIX = re.compile(r'^[A-Z]{1,2}_?\d{4,}\.\d+\s+')
# 标题里出现即视为「物种名到此为止」的分隔词
_BREAK_WORDS = re.compile(
    r'(?:'
    r'\bisolate\b|\bstrain\b|\bvariant\b|\bclone\b|\bsegment\b|'
    r'\bcomplete\b|\bnearly\b|\bpartial\b|\bfull\b|\bgenomic\b|\bgenome\b|'
    r'\bsequence\b|\bcds\b|\bgene\b|\bgenes\b|\bRNA\b|\bmRNA\b|'
    r'\bsegment\b|\bDNA[- ]?[AB]\b'
    r')',
    re.IGNORECASE,
)


def clean_species_name(raw, accession=''):
    """把「accession + 描述 + 株系 + complete genome」样式的串还原成物种名。

    做法：去掉开头的 accession，然后在第一个「株系/完整性/序列类型」
    分隔词处截断。这样既处理 'Pepino mosaic virus isolate Ch2, complete
    genome'，也处理 'Pepino mosaic virus complete genome'（后者没有
    逗号，靠 complete 直接截断）。

    只在 raw 明显是 GenBank 标题时动手；正常物种名（如 'Citrus exocortis
    viroid'、'Pepino mosaic virus'）原样返回，绝不改写。
    无法安全还原时返回 ''，由调用方决定回退到 accession。
    """
    if not raw:
        return ''
    s = str(raw).strip()
    if not s:
        return ''
    # 只有 accession、没有任何描述 → 不是物种名
    if re.match(r'^[A-Z]{1,2}_?\d{4,}\.\d+$', s):
        return ''
    looks_like_title = bool(
        _TITLE_ACC_PREFIX.match(s)
        or _BREAK_WORDS.search(s)
        or ',' in s
    )
    if not looks_like_title:
        return s
    s = _TITLE_ACC_PREFIX.sub('', s)
    m = _BREAK_WORDS.search(s)
    if m and m.start() > 0:
        s = s[:m.start()]
    # 标题常以基因名收尾（"... dwarf virus G gene for glycoprotein" 截断后
    # 剩 "Eggplant mottled dwarf virus G"）。物种名末尾的单个大写字母是
    # 基因符号，不是物种名的一部分。
    # 但 Potato virus Y / Barley yellow dwarf virus - PAV 这类「种名本身以
    # 大写字母结尾」的必须保住：只在该字母是独立单词时才认为是基因名。
    s = re.sub(r'(?<=\s)([A-Z])(?=\s|$)', '', s)
    s = re.sub(r'\s{2,}', ' ', s)
    s = s.strip().rstrip(' ,;-')
    if not s or re.match(r'^[A-Z]{1,2}_?\d{4,}\.\d+$', s):
        return ''
    return s


def read_text_lossless(path):
    """读文本文件，优先 UTF-8，其次 GBK，都失败才用 replace 兜底。

    同 kv_variant.read_text_lossless 口径（Windows 中文环境下外部工具/人工
    整理的表可能是系统 ANSI 代码页 936/GBK；只按 UTF-8 读 + replace 会把
    GBK 字节不可逆地替换成 U+FFFD，物种名「柑橘」变一串替换符）。
    """
    raw = Path(path).read_bytes()
    for enc in ('utf-8-sig', 'utf-8', 'gbk'):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode('utf-8', errors='replace')


def load_ref_info(ref_info_tsv, logger=None):
    """
    读参考注释表，返回
      {accession: {'taxid':..,'species':..,'segment':..,'molecule':..}}
    同时建立去版本号的别名索引。
    """
    info = {}
    if not ref_info_tsv or not os.path.exists(ref_info_tsv):
        if logger:
            logger.warning("未提供 ref_info，物种名将回退为 accession")
        return info

    idx = {}
    header = None
    bad_species = []
    repaired_species = []
    # 编码兜底：GBK 表按 UTF-8+replace 读会把中文列变 U+FFFD（不可逆），
    # 这里用无损解码（utf-8 → gbk 回退，同 kv_variant.read_text_lossless 口径）
    for line in read_text_lossless(ref_info_tsv).splitlines():
        if line.startswith('####') or not line.strip():
            continue
        parts = line.rstrip('\n').split('\t')
        if header is None:
            header = [h.strip() for h in parts]

            def find(syns):
                return next((header.index(s) for s in syns if s in header), -1)

            idx = {
                'acc': find(ACC_SYNONYMS), 'tax': find(TAX_SYNONYMS),
                'sp': find(SP_SYNONYMS), 'seg': find(SEG_SYNONYMS),
                'mol': find(['Molecule_type2', 'Molecule_Type2', 'Molecule_type']),
                'host': find(['Host', 'host']),
                'geo': find(['Geo_Location', 'Geo_Location/Year', 'geo']),
                'gbtitle': find(['GenBank_Title', 'GenBank Title']),
                'mol1': find(['Molecule_type', 'Molecule_Type']),
                # ICTV 种名（Species_ICTV）：同 NCBI 种不同条目名的聚合键，
                # 例如 HSVd 条目 "Citrus gummy bark viroid" 的
                # Species_ICTV = Hostuviroid impedihumuli（=HSVd 本尊）
                'ictv': find(['Species_ICTV', 'Ictv_Species', 'ICTV_Species']),
            }
            if idx['acc'] == -1:
                if logger:
                    logger.error(f"ref_info 找不到 Accession 列，实际列: {header[:8]}")
                return {}
            continue

        if len(parts) <= idx['acc']:
            continue
        acc = parts[idx['acc']].strip()
        if not acc:
            continue

        def get(key):
            i = idx.get(key, -1)
            return parts[i].strip() if i != -1 and len(parts) > i else ''

        _sp_raw = get('sp')
        _gbtitle = get('gbtitle')
        # 物种名消毒：Species_NCBI 被填成 GenBank 标题时还原成物种名
        # （见 clean_species_name 的实测背景）。Species 为空的条目不消音，
        # 记下来供收尾统计。
        _sp = clean_species_name(_sp_raw, acc)
        if _sp_raw and not _sp:
            bad_species.append(acc)
        elif _sp and _sp != _sp_raw:
            repaired_species.append((acc, _sp_raw, _sp))

        rec = {
            'taxid': get('tax') or 'Unannotated',
            'species': _sp or acc,
            'segment': get('seg'),
            'molecule': get('mol'),
            'ictv': get('ictv'),
            # 宿主/地理/GenBank 标题：识别表一并提供（此前只导出 4 列，
            # 用户反馈「提取信息不全」——库里 26 列注释大多被丢掉）
            'host': get('host'),
            'geo': get('geo'),
            'genbank_title': _gbtitle,
            'molecule_type': get('mol1'),
        }
        info[acc] = rec
        # 去版本号别名：同一 accession 多版本时后写入者胜出，
        # 会让物种名静默错配。检测到冲突就报警。
        bare = acc.split('.')[0]
        if bare in info and info[bare].get('species') != rec.get('species'):
            if logger:
                logger.warning(f"ref_info 多版本冲突: {bare} "
                               f"({info[bare].get('species')} vs "
                               f"{rec.get('species')})，使用后者")
        info[bare] = rec  # 去版本号别名

    # ICTV 种名回填：上一步只处理了「标题冒充物种名」的 30 条，它们
    # 的 Species_ICTV 本来就是空；同种其它正常条目里有 ICTV 名的，借过来。
    # 借不到就保持空，绝不按属名或标题臆造种名。
    _ictv_by_species = {}
    for rec in info.values():
        _s, _i = (rec or {}).get('species'), (rec or {}).get('ictv')
        if _s and _i:
            _ictv_by_species.setdefault(_s.lower(), _i)
    filled_ictv = 0
    for rec in info.values():
        if rec and not rec.get('ictv') and rec.get('species'):
            _i = _ictv_by_species.get(rec['species'].lower())
            if _i:
                rec['ictv'] = _i
                filled_ictv += 1

    if logger:
        logger.info(f"参考注释载入: {len(info)//2} 条（含去版本号别名）")
        if repaired_species:
            logger.warning(
                f"ref_info 有 {len(repaired_species)} 条 Species_NCBI 填的是 GenBank "
                f"标题，已还原为物种名（例: {repaired_species[0][0]} "
                f"'{repaired_species[0][1][:40]}...' -> '{repaired_species[0][2]}'）")
        if bad_species:
            logger.warning(
                f"ref_info 有 {len(bad_species)} 条记录无法还原物种名"
                f"（Species 列将留空，不再用标题顶替）: {bad_species[:5]}")
        if filled_ictv:
            logger.info(f"ICTV 种名回填: {filled_ictv} 条（同种其它条目借用）")
    return info


def ref_lookup(ref_info, accession):
    """按 accession 查注释，容错版本号差异"""
    if not ref_info:
        return {'taxid': 'Unannotated', 'species': accession, 'segment': '', 'molecule': ''}
    rec = ref_info.get(accession) or ref_info.get(accession.split('.')[0])
    return rec or {'taxid': 'Unannotated', 'species': accession, 'segment': '', 'molecule': ''}
