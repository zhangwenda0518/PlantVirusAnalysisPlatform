# -*- coding: utf-8 -*-
"""把 package.py 的三件产物组装成两版发布目录，并清掉中间产物。

为什么需要（2026-10-26）：package.py 默认把
  <PVAP>/VirusPlatform、VirusPlatform-Examples、VirusPlatform-Database
作为构建产物直接摊在 PVAP 根目录。发布实际只要两版：

  <PVAP>/PlantVirusAnalysisPlatform/                 主流程 13 工具
  <PVAP>/PlantVirusAnalysisPlatform_with_Plugin/     全量 26 工具

两版都由上面三件**硬链接克隆**而来（几乎不额外占盘），因此组装完成后
根目录那三件就是纯中间产物——留着只会让人分不清哪个是要发的。本脚本
组装完默认删除它们，两版直接放在 PVAP 根目录。

用法：
  python dev_tools/finalize_release.py              # 组装 + 清理中间产物
  python dev_tools/finalize_release.py --keep-build # 只组装，保留三件产物

前置：先跑 dev_tools/package.py（需要 --with-db --with-built-virusdb）。
"""
import argparse
import json
import os
import shutil
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _resolve_dist():
    """产物根：--dist / VP_PVAP_DIST > 默认「仓库上一级/PVAP」（2026-11-03）。"""
    _argv = sys.argv
    for _i, _a in enumerate(_argv):
        if _a == '--dist' and _i + 1 < len(_argv):
            return os.path.abspath(_argv[_i + 1])
        if _a.startswith('--dist='):
            return os.path.abspath(_a.split('=', 1)[1])
    _env = (os.environ.get('VP_PVAP_DIST') or '').strip()
    if _env:
        return os.path.abspath(_env)
    return os.path.join(os.path.dirname(REPO), 'PVAP')


DIST = _resolve_dist()
REL = DIST
APP = os.path.join(DIST, 'VirusPlatform')
EX_DIR = os.path.join(DIST, 'VirusPlatform-Examples')
DB_DIR = os.path.join(DIST, 'VirusPlatform-Database')
BS = chr(92)

EDITIONS = (
    ('PlantVirusAnalysisPlatform', 'minimal'),
    ('PlantVirusAnalysisPlatform_with_Plugin', 'full'),
)

# 主流程保留集（与 package.py --minimal-tools / dist_completeness.py 同源）
KEEP = {'Blast', 'FastTree', 'SPAdes-Windows', 'bcftools', 'fastp',
        'mafft-win', 'minimap2', 'salmon2', 'samtools', 'trimAl',
        'diamond', 'mmseqs', 'datasets'}

MISSING_MINIMAL = ['RDP5CL', 'dsRNAmax', 'iqtree3', 'lsd2', 'pandepth',
                   'raxml-ng', 'table2asn', 'viral_consensus']


def clone(src, dst):
    """硬链接克隆（跨卷/不支持时退回复制），返回文件数。"""
    n = 0
    for cur, _dirs, files in os.walk(src):
        rel = os.path.relpath(cur, src)
        tgt = dst if rel == '.' else os.path.join(dst, rel)
        os.makedirs(tgt, exist_ok=True)
        for f in files:
            s = os.path.join(cur, f)
            d = os.path.join(tgt, f)
            try:
                os.link(s, d)
            except OSError:
                shutil.copy2(s, d)
            n += 1
    return n


def dir_size(p):
    return sum(os.path.getsize(os.path.join(dp, f))
               for dp, _d, fs in os.walk(p) for f in fs)


def write_readme(d, edition, full):
    lines = [
        'Plant Virus Analysis Platform - Distribution Guide',
        '(Program / Database / Examples are kept separate)',
        'Edition: ' + edition,
        '=' * 60, '',
        'Start-Desktop.bat / Start-Web.bat',
        '   - Double-click one of these (in this folder) to launch the',
        '     platform in a desktop window or in your browser.',
        '',
        '1) VirusPlatform' + BS + '            The program itself',
        '   - exe + webapp front end + external tools in bin/ and tools/ +',
        '     mcp_server.py (the MCP console runs in-process via --mcp-stdio)',
        '   - Command-line equivalents: VirusPlatform.exe --gui / --web',
    ]
    if full:
        lines += [
            '   - FULL external tool set (26 tool suites / 31 tool keys): QC, host removal,',
            '     assembly, identification, alignment, tree building, recombination (RDP5),',
            '     evolutionary dynamics, NCBI submission, quantification/consensus,',
            '     candidate validation, plus the bin utilities.',
            '   - Nothing is fetched at run time; Settings -> Plugins can still install',
            '     offline plugin zips to add or update a tool.',
        ]
    else:
        lines += [
            '   - MAIN WORKFLOW tool set (13 tool suites): QC (fastp, seqkit), host removal',
            '     (minibwa, blast), assembly (spades), identification (kunpeng),',
            '     alignment (mafft, trimal), tree building (fasttree), quantification',
            '     (salmon or minibwa, samtools, bcftools), validation (diamond, mmseqs)',
            '     and the datasets CLI.',
            '   - Specialised tools (RDP5, iQtree, RAxML-NG, lsd2, table2asn, pandepth,',
            '     viral_consensus, dsRNAmax, snpEff, SNPGenie, srna-target, strawberry-perl)',
            '     are installed on demand: Settings -> Plugins -> "Install by module" or',
            '     "Fill missing", or import offline plugin zips. They are also auto-installed',
            '     before a module that needs them starts.',
        ]
    lines += [
        '',
        '2) VirusPlatform-Examples' + BS + '   Example data (read-only)',
        "   - Inputs/products used by each tool's Example button and the Example results page",
        '   - Detected automatically when placed next to the program; otherwise set the',
        '     absolute path under Settings -> Example data directory',
        '',
        '3) VirusPlatform-Database' + BS + '   Database package (shipped with this edition)',
        '   - Prebuilt virus classification library (kunpeng_db/virus_db), annotation',
        '     libraries (RefSeq viral protein DIAMOND DB + Pfam HMM press files),',
        '     virus reference dataset (virusref_db), tree references (tree_db),',
        '     misc_db (viroid refs, submission annotation, host probability tables),',
        '     dsRNA design data (thermodynamic lookup table + siRNA parameters) and',
        '     the t-mirna candidate miRNA library.',
        '   - NCBI Taxonomy ships as the slim virus-subtree archive',
        '     (new_taxdump_slim.tar.gz) and is unpacked automatically on the',
        '     first run (offline, no download needed)',
        '   - Derived indexes (BLAST/DMND/salmon/minibwa) are generated on first use',
        '   - No host classification library (species-specific): build one on the DB',
        '     building page or load a prebuilt one with one click',
        '   - Zero-config hook-up: placed at the same level as the program it is',
        '     detected automatically; or set it under Settings -> Database directory',
        '',
        'Self-check: VirusPlatform' + BS + 'VirusPlatform.exe --cli selfcheck',
        '',
    ]
    with open(os.path.join(d, 'README-Distribution.txt'), 'w',
              encoding='ascii', errors='replace', newline='\r\n') as f:
        f.write('\n'.join(lines))


def patch_build_info(path, edition, tier, tools):
    with open(path, encoding='utf-8') as f:
        bi = json.load(f)
    bi['edition'] = edition
    if tier == 'minimal':
        bi['tool_tier'] = 'minimal'
        bi['tools'] = {'available': 23, 'total': 31,
                       'missing': list(MISSING_MINIMAL)}
    else:
        bi['tools'] = tools
    os.remove(path)
    with open(path, 'w', encoding='ascii', newline='\n') as f:
        json.dump(bi, f, ensure_ascii=True, indent=1)
        f.write('\n')


def build_edition(edition, tier):
    d = os.path.join(REL, edition)
    os.makedirs(d, exist_ok=True)

    tgt = os.path.join(d, 'VirusPlatform')
    if os.path.isdir(tgt):
        shutil.rmtree(tgt)
    clone(APP, tgt)
    tools_dir = os.path.join(tgt, '3rd', 'tools')
    if tier == 'minimal':
        for t in sorted(os.listdir(tools_dir)):
            if t not in KEEP:
                shutil.rmtree(os.path.join(tools_dir, t), ignore_errors=True)
    for dp, _dirs, _files in os.walk(tools_dir):
        if os.path.basename(dp) == '__pycache__':
            shutil.rmtree(dp, ignore_errors=True)
    shutil.rmtree(os.path.join(tgt, 'databases'), ignore_errors=True)

    with open(os.path.join(tgt, 'build_info.json'), encoding='utf-8') as f:
        tools = json.load(f).get('tools') or {'available': 31, 'total': 31,
                                              'missing': []}
    patch_build_info(os.path.join(tgt, 'build_info.json'), edition, tier, tools)

    # 断开 platform.json 硬链接：两版各自改语言/路径互不影响
    pj = os.path.join(tgt, 'platform.json')
    if os.stat(pj).st_nlink > 1:
        tmp = pj + '.tmpcopy'
        shutil.copy2(pj, tmp)
        os.replace(tmp, pj)

    # 示例包（2026-11-03 补）：README 与平台自动探测都要求三子目录同层
    # （program / examples / database），旧实现只克隆了程序与数据库，
    # 两版目录里根本没有示例 → 示例按钮全部失效。
    ex_d = os.path.join(d, 'VirusPlatform-Examples')
    if os.path.isdir(ex_d):
        shutil.rmtree(ex_d)
    if os.path.isdir(EX_DIR):
        clone(EX_DIR, ex_d)
    dbt = os.path.join(d, 'VirusPlatform-Database')
    if os.path.isdir(dbt):
        shutil.rmtree(dbt)
    clone(DB_DIR, dbt)
    bdb = os.path.join(dbt, 'build_info.json')
    with open(bdb, encoding='utf-8') as f:
        j = json.load(f)
    j['edition'] = edition
    os.remove(bdb)
    with open(bdb, 'w', encoding='ascii', newline='\n') as f:
        json.dump(j, f, ensure_ascii=True, indent=1)
        f.write('\n')

    for b in ('Start-Web.bat', 'Start-Desktop.bat'):
        src = os.path.join(DIST, b)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(d, b))
    write_readme(d, edition, tier == 'full')
    print(f'  + {edition}: program {dir_size(tgt) / 1e9:.2f} GB, '
          f'database {dir_size(dbt) / 1e9:.2f} GB, '
          f'tools {len(os.listdir(tools_dir))}')
    return d


def write_selector():
    sel = [
        'Plant Virus Analysis Platform - choose your edition',
        '=' * 60, '',
        '1) PlantVirusAnalysisPlatform' + BS,
        '   Program (13 main-workflow tool suites) + Examples + Database.',
        '   Specialised tools install on demand via Settings -> Plugins.',
        '   Total size: about 1.3 GB  (program 0.90 + examples 0.10 + database 0.28)',
        '',
        '2) PlantVirusAnalysisPlatform_with_Plugin' + BS,
        '   Program (full 26-tool suite) + Examples + Database.',
        '   Everything bundled; nothing to install on demand.',
        '   Total size: about 2.0 GB  (program 1.60 + examples 0.10 + database 0.28)',
        '',
        'In each edition folder, double-click Start-Desktop.bat or Start-Web.bat',
        'to launch the platform. Keep the three sub-folders (program / examples /',
        'database) at the same level - the platform detects them automatically.',
        '',
        'First run: the bundled slim NCBI taxonomy archive',
        '(new_taxdump_slim.tar.gz, virus-subtree-only) is unpacked',
        'automatically (seconds, no network needed).',
        '',
        'Self-check: VirusPlatform' + BS + 'VirusPlatform.exe --cli selfcheck',
        '',
    ]
    with open(os.path.join(REL, 'README.txt'), 'w', encoding='ascii',
              errors='replace', newline='\r\n') as f:
        f.write('\n'.join(sel))


def clean_legacy_release():
    """删除旧布局的 Release/（两版已迁到 PVAP 根目录后）。"""
    old = os.path.join(DIST, 'Release')
    if os.path.isdir(old):
        shutil.rmtree(old)
        return True
    return False


def clean_build_root():
    """删除根目录中间产物（Release 已是硬链接克隆，数据不受影响）。"""
    removed = []
    for p in (APP, EX_DIR, DB_DIR):
        if os.path.isdir(p):
            shutil.rmtree(p)
            removed.append(os.path.basename(p))
    for name in ('Start-Web.bat', 'Start-Desktop.bat',
                 # 2026-11-05：两个助手已内联进入口脚本；旧构建残留一并清掉
                 'open-browser.bat', 'mount-data.bat',
                 'README-Distribution.txt'):
        p = os.path.join(DIST, name)
        if os.path.isfile(p):
            os.remove(p)
            removed.append(name)
    return removed


def main(argv=None):
    ap = argparse.ArgumentParser(description='组装两版发布目录')
    ap.add_argument('--keep-build', action='store_true',
                    help='保留根目录三件中间产物（默认组装后删除）')
    ap.add_argument('--dist', metavar='PATH', default='',
                    help='产物根（默认「仓库上一级/PVAP」；用户机正式位置 D:\\PVAP）')
    a = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

    missing = [p for p in (APP, EX_DIR, DB_DIR) if not os.path.isdir(p)]
    if missing:
        if all(os.path.isdir(os.path.join(REL, e)) for e, _t in EDITIONS):
            print('根目录中间产物不存在；两版已在 PVAP 根目录，无需组装。')
            print('（重新打包请先运行 dev_tools/package.py --with-db '
                  '--with-built-virusdb）')
            return 0
        print('✘ 缺少构建产物：' + ', '.join(os.path.basename(p)
                                             for p in missing))
        return 1

    print('== 组装两版 ==')
    for edition, tier in EDITIONS:
        build_edition(edition, tier)
    write_selector()
    print('  + README.txt')

    # 组装成功校验：两版程序 exe 与数据库包都在
    for edition, _tier in EDITIONS:
        d = os.path.join(REL, edition)
        for rel in (os.path.join('VirusPlatform', 'VirusPlatform.exe'),
                    os.path.join('VirusPlatform-Database', 'databases',
                                 'new_taxdump_slim.tar.gz')):
            if not os.path.isfile(os.path.join(d, rel)):
                print(f'✘ {edition} 缺少 {rel}，保留中间产物以便排查')
                return 1

    if clean_legacy_release():
        print('== 已删除旧布局 Release/ ==')
    if a.keep_build:
        print('== 保留中间产物（--keep-build）==')
    else:
        removed = clean_build_root()
        print('== 已清理根目录中间产物 ==')
        for name in removed:
            print(f'  - {name}')
    print('完成：' + REL)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
