# -*- coding: utf-8 -*-
"""组装引擎选择 / MEGAHIT 命令拼装 的单测（纯函数，不跑真组装）。

覆盖的坑：
- `auto` 必须是「有 MEGAHIT 才用」——不能假装装了；
- `srna` 小RNA 固定 SPAdes（MEGAHIT k 下限 15/默认 21，21-24nt 读长拼不出）；
- 显式选 megahit 但没装时，choose 返回 spades，由调用方给出人话报错；
- `-m` 是**字节**（MEGAHIT 只在 0~1 之间才按内存比例）；
- 单端 `-r`、双端 `-1/-2`，不能同时给。
"""
import os

from Virus_Platform_Core.assembly import (ASSEMBLER_CHOICES, MEGAHIT_MODES,
                                          _megahit_monitor, build_megahit_args,
                                          choose_assembler,
                                          normalize_megahit_mode)

ME = r'D:\x\3rd\tools\megahit-1.2.9-win64\megahit.bat'


def test_choices_tuple():
    assert set(ASSEMBLER_CHOICES) == {'auto', 'spades', 'megahit'}


def test_choose_auto_prefers_megahit():
    assert choose_assembler('auto', 'rnaviral', ME) == 'megahit'
    assert choose_assembler('auto', 'metaviral', ME) == 'megahit'
    assert choose_assembler('auto', 'rnaviral', None) == 'spades'


def test_choose_srna_always_spades():
    for req in ('auto', 'megahit', 'spades'):
        assert choose_assembler(req, 'srna', ME) == 'spades'


def test_choose_explicit():
    assert choose_assembler('spades', 'rnaviral', ME) == 'spades'
    assert choose_assembler('MEGAHIT', 'rnaviral', ME) == 'megahit'
    # 显式要 MEGAHIT 但没装 → 由调用方决定报错，这里只兜底成 spades
    assert choose_assembler('megahit', 'rnaviral', None) == 'spades'
    # 非法值按 auto 处理
    assert choose_assembler('megahit2', 'rnaviral', ME) == 'megahit'
    assert choose_assembler('', 'rnaviral', None) == 'spades'


def test_cmd_paired():
    cmd = build_megahit_args('r1.fq.gz', 'r2.fq.gz', 'out', threads=8,
                             memory_gb=16, min_len=300, tmp_dir='tmpd')
    assert cmd[0:4] == ['-1', 'r1.fq.gz', '-2', 'r2.fq.gz']
    assert '-r' not in cmd
    i = cmd.index('-o')
    assert cmd[i + 1] == 'out'
    i = cmd.index('--min-contig-len')
    assert cmd[i + 1] == '300'
    i = cmd.index('-t')
    assert cmd[i + 1] == '8'
    i = cmd.index('-m')
    assert cmd[i + 1] == str(16 * 1024 ** 3)      # 字节，不是 GB 也不是 0.6
    i = cmd.index('--tmp-dir')
    assert cmd[i + 1] == 'tmpd'


def test_cmd_single_end():
    cmd = build_megahit_args('r1.fa', None, 'out', memory_gb=0, threads=None)
    assert cmd[0:2] == ['-r', 'r1.fa']
    assert '-1' not in cmd and '-2' not in cmd
    assert '-t' not in cmd and '-m' not in cmd     # 0/None 不传，交给 MEGAHIT 默认
    # --tmp-dir 缺省不出现（否则会指到不存在的目录）
    assert '--tmp-dir' not in cmd


def test_monitor_progress_from_log(tmp_path):
    seen = []
    me_out = str(tmp_path)
    mon = _megahit_monitor(me_out, lambda p, m: seen.append((round(p, 3), m)))
    assert mon is not None
    mon()                                   # 还没有 log → 不报错、不推进度
    assert seen == []
    with open(os.path.join(me_out, 'log'), 'w', encoding='utf-8') as fh:
        fh.write('k list: 21,29,39,59,79,99,119,141\n'
                 'Build graph for k = 21\nBuild graph for k = 29\n'
                 'Build graph for k = 39\n')
    mon()
    assert seen and seen[-1][0] > 0.1
    assert 'k 阶段 3/' in seen[-1][1]
    # 重复调用不重复推进（同一批 k 只报一次）
    before = len(seen)
    mon()
    assert len(seen) == before


def test_engine_is_registered():
    """--run-engine 必须认识 megahit_engine.py，否则冻结版跑不了组装。"""
    from Virus_Platform_Core import engine_entry
    assert (engine_entry._ENGINES.get('megahit_engine.py')
            == 'Virus_Platform_Core.megahit_engine')
    import Virus_Platform_Core.megahit_engine as me
    assert callable(me.main)
    assert callable(me.megahit_driver_path)


def test_megahit_mode_normalization():
    assert set(MEGAHIT_MODES) == {'default', 'sensitive', 'large'}
    assert normalize_megahit_mode(None) == 'default'
    assert normalize_megahit_mode('') == 'default'
    assert normalize_megahit_mode('SENSITIVE') == 'sensitive'
    assert normalize_megahit_mode('large') == 'large'
    # 旧版/非法值（比如把 SPAdes 的 rnaviral 传进来）一律回默认，不报错
    assert normalize_megahit_mode('rnaviral') == 'default'
    assert normalize_megahit_mode('bogus') == 'default'


def test_preset_flags():
    base = build_megahit_args('r1.fq', 'r2.fq', 'out')
    assert '--presets' not in base
    sens = build_megahit_args('r1.fq', 'r2.fq', 'out', preset='sensitive')
    i = sens.index('--presets')
    assert sens[i + 1] == 'meta-sensitive'
    large = build_megahit_args('r1.fq', 'r2.fq', 'out', preset='large')
    i = large.index('--presets')
    assert large[i + 1] == 'meta-large'
    # 非法 preset 当作 default，不加任何 --presets
    assert '--presets' not in build_megahit_args('r1.fq', 'r2.fq', 'out',
                                                 preset='rnaviral')
