# -*- coding: utf-8 -*-
"""⑥b 注释层选择与 PVDB 引擎降级的最小回归（2026-11-06）。

① `--libs prot` / `--libs seq` 必须**报错**，不能静默回退为全部四层
   （此前 normalize_libs 认不出 'prot' → picked 为空 → 返回 ANNOT_LIBS
   全量，用户以为只跑了序列层，实际四层全开）。
② diamond 选中但 .dmnd 缺失时，`_run_engine` 走的是 mmseqs easy-search
   语法 —— 必须重新解析 **mmseqs 可执行文件**，不能拿 DIAMOND 的 exe 去跑；
   取不到 mmseqs 再降级 blastp（同样必须换成 blastp 的 exe），都不可用
   时给中文报错列出现状。
③ summary 的 PVDB 门槛与判定同源（不再写死第二份 40/60）。
"""
import os

import pytest

from Virus_Platform_Core import orf_annot, pvdb_search


# ---------------------------------------------------------------- ① 层选择
def test_libs_all_by_default():
    assert orf_annot.normalize_libs(None) == (tuple(orf_annot.ANNOT_LIBS), [])
    assert orf_annot.normalize_libs('') == (tuple(orf_annot.ANNOT_LIBS), [])


def test_libs_valid_subset_keeps_order():
    sel, ignored = orf_annot.normalize_libs('bfvd,pvdb')
    assert sel == ('pvdb', 'bfvd')
    assert ignored == []


def test_libs_archived_prot_raises_not_expand():
    """prot / seq 是已归档层：必须报错并列出可用层，绝不回退全层。"""
    for bad in ('prot', 'seq', 'blast', 'pfam', 'pfam-a'):
        with pytest.raises(RuntimeError) as ei:
            orf_annot.normalize_libs(bad)
        msg = str(ei.value)
        assert '无效' in msg and 'pvdb' in msg and 'hmm' in msg, msg
    # 已归档层要给出「已归档」提示（prot / 原 Pfam-A-Viruses）
    with pytest.raises(RuntimeError) as ei:
        orf_annot.normalize_libs('prot')
    assert '已归档' in str(ei.value)


def test_libs_mixed_keeps_valid_and_reports_ignored():
    sel, ignored = orf_annot.normalize_libs('pvdb,prot,nope')
    assert sel == ('pvdb',)
    assert ignored == ['prot', 'nope']


def test_libs_unknown_only_raises():
    with pytest.raises(RuntimeError):
        orf_annot.normalize_libs('nope')


# ------------------------------------------------------- ② 引擎降级链
def _fake_p(tmp_path):
    faa = tmp_path / 'prot.faa'
    faa.write_text('>a\nMKV\n', encoding='utf-8')
    return {'prot_dmnd': str(tmp_path / 'missing.dmnd'),
            'prot_faa': str(faa)}


def test_diamond_without_dmnd_uses_mmseqs_exe(tmp_path, monkeypatch):
    """engine=diamond 且缺 .dmnd：argv[0] 必须是 mmseqs，不能是 diamond。"""
    calls = []

    def _pick(prefer=None):
        return {'mmseqs': ('mmseqs', 'C:/tools/mmseqs.exe'),
                'blastp': ('blastp', 'C:/tools/blastp.exe')}.get(prefer)

    def _run(cmd, **kw):
        calls.append(list(cmd))

    monkeypatch.setattr(pvdb_search, '_pick_engine', _pick)
    monkeypatch.setattr(pvdb_search, 'run_cmd', _run)
    p = _fake_p(tmp_path)
    used = pvdb_search._run_engine('diamond', 'C:/tools/diamond.exe',
                                   'q.faa', p, str(tmp_path / 'out.tsv'), 2)
    assert used == 'mmseqs'
    assert calls and calls[0][0] == 'C:/tools/mmseqs.exe'
    assert calls[0][1] == 'easy-search'


def test_diamond_without_dmnd_falls_back_to_blastp_exe(tmp_path, monkeypatch):
    """mmseqs 不可用 → 降级 blastp；exe 也必须是 blastp 而不是 diamond。"""
    calls = []

    def _pick(prefer=None):
        if prefer == 'blastp':
            return ('blastp', 'C:/tools/blastp.exe')
        return None

    def _run(cmd, **kw):
        calls.append(list(cmd))

    monkeypatch.setattr(pvdb_search, '_pick_engine', _pick)
    monkeypatch.setattr(pvdb_search, 'run_cmd', _run)
    p = _fake_p(tmp_path)
    used = pvdb_search._run_engine('diamond', 'C:/tools/diamond.exe',
                                   'q.faa', p, str(tmp_path / 'out.tsv'), 2)
    assert used == 'blastp'
    assert calls and calls[0][0] == 'C:/tools/blastp.exe'
    assert '-query' in calls[0]


def test_no_engine_available_raises_chinese(tmp_path, monkeypatch):
    def _pick(prefer=None):
        return None

    monkeypatch.setattr(pvdb_search, '_pick_engine', _pick)
    p = _fake_p(tmp_path)
    with pytest.raises(RuntimeError) as ei:
        pvdb_search._run_engine('diamond', 'C:/tools/diamond.exe',
                                'q.faa', p, str(tmp_path / 'out.tsv'), 2)
    msg = str(ei.value)
    assert 'PVDB' in msg and 'dmnd' in msg and 'BLAST' in msg.upper()


# ------------------------------------------------------- ③ 门槛同源
def test_pvdb_thresholds_single_source():
    """orf_annot 的 summary 门槛必须来自 pvdb_search 常量（不写第二份）。"""
    assert orf_annot._PVDB_MIN_PIDENT == pvdb_search.SEQ_MIN_PIDENT
    assert orf_annot._PVDB_MIN_QCOV == pvdb_search.SEQ_MIN_QCOV
    src = os.path.join(os.path.dirname(orf_annot.__file__), 'orf_annot.py')
    with open(src, encoding='utf-8') as f:
        text = f.read()
    # 那段 summary 里不得再出现硬编码 40.0 / 60.0 作为 PVDB 默认门槛
    sec = text.split("'pvdb_thresholds':", 1)[1][:600]
    assert '40.0' not in sec and '60.0' not in sec, sec


# ------------------------------------------------ ④ 分段投影档位口径
def _polyprotein_fixture():
    """100aa query，参考多聚蛋白切点 20/50；55 位是 Q|X motif。"""
    seq = 'A' * 100
    seq = seq[:54] + 'QS' + seq[56:]
    ref_cuts = {'ref1': {'cut_positions': [20, 50], 'protein_len': 100,
                         'canonical_function': 'polyprotein',
                         'products': ['P1', 'P2', 'P3']}}
    hits = [{'target': 'ref1', 'pident': 90, 'qcov': 90,
             'qstart': 1, 'qend': 100, 'sstart': 1, 'send': 100,
             'bits': 500, 'canonical_function': 'polyprotein'}]
    return seq, ref_cuts, hits


def test_strong_projection_uses_cuts_directly():
    """strong 档切点直接采用（不吸附 ±10aa motif，模块头口径）。"""
    seq, ref_cuts, hits = _polyprotein_fixture()
    res = pvdb_search.project_segments(100, seq, hits, {}, ref_cuts)
    assert res['tier'] == 'strong' and res['method'] == 'projection'
    # segments 为 1 基闭区间
    assert [(s['qstart'], s['qend']) for s in res['segments']] == \
        [(1, 20), (21, 50), (51, 100)]


def test_mid_projection_snaps_to_motif():
    """mid 档保留 motif 吸附（50 → 55），与 strong 形成可区分行为。"""
    seq, ref_cuts, hits = _polyprotein_fixture()
    hits = [dict(hits[0], pident=55)]
    res = pvdb_search.project_segments(100, seq, hits, {}, ref_cuts)
    assert res['tier'] == 'mid' and res['method'] == 'projection+motif'
    assert [(s['qstart'], s['qend']) for s in res['segments']] == \
        [(1, 20), (21, 55), (56, 100)]


# ------------------------------------------------ ⑤ HMM 各库 Z 口径
def test_hmm_lib_z_uses_manifest_per_library(tmp_path, monkeypatch):
    """三个 HMM 子库各用 manifest 的模型数，不再共用 6538。"""
    from Virus_Platform_Core import hmm_annot
    pvdb = tmp_path / 'pvdb'
    (pvdb / 'meta').mkdir(parents=True)
    (pvdb / 'meta' / 'pvdb-v1.0.manifest.json').write_text(
        '{"layers": {"pfam": [{"file": "hmm/pvdb-v1.0.pfam.hmm",'
        ' "kept": 387}], "vog_vfam": ['
        '{"file": "hmm/pvdb-v1.0.vog.hmm", "kept": 150},'
        '{"file": "hmm/pvdb-v1.0.vfam.hmm", "kept": 395}]}}',
        encoding='utf-8')
    monkeypatch.setattr(hmm_annot, 'PVDB_HMM_DIR', str(pvdb / 'hmm'))
    hmm_annot._HMM_MANIFEST_CACHE.clear()
    monkeypatch.delenv('PVDB_HMM_REF_Z', raising=False)
    assert hmm_annot.lib_z('pfam', str(pvdb / 'hmm' / 'pvdb-v1.0.pfam.hmm')) \
        == (387, 'manifest')
    assert hmm_annot.lib_z('vog', str(pvdb / 'hmm' / 'pvdb-v1.0.vog.hmm')) \
        == (150, 'manifest')
    assert hmm_annot.lib_z('vfam', str(pvdb / 'hmm' / 'pvdb-v1.0.vfam.hmm')) \
        == (395, 'manifest')
    # manifest 缺该库 → 退回默认 Z（历史口径）
    assert hmm_annot.lib_z('x', str(pvdb / 'hmm' / 'other.hmm'))[1] == 'default'
    hmm_annot._HMM_MANIFEST_CACHE.clear()


# ------------------------------------------------ ⑥ selfcheck 库组口径
def test_selfcheck_homolog_group_ok_requires_pvdb_protein(
        tmp_path, monkeypatch):
    """同源验证库组 ok 只看 PVDB 蛋白库（与 docstring 同口径）。"""
    from Virus_Platform_Core import selfcheck
    import Virus_Platform_Core.local_search as ls
    import Virus_Platform_Core.bfvd_search as bf
    import Virus_Platform_Core.cdd_search as cd
    monkeypatch.setattr(ls, 'PROT_DMND', str(tmp_path / 'missing.dmnd'))
    monkeypatch.setattr(bf, 'bfvd_db', lambda: 'bfvd_db_present')
    monkeypatch.setattr(cd, 'cdd_db', lambda: None)
    r = selfcheck._check_homolog_dbs()
    assert r['ok'] is False, r
    assert 'PVDB蛋白' in r['detail']
    # PVDB 蛋白库在位 → ok True（其余库仍只进 detail）
    faa = tmp_path / 'prot.dmnd'
    faa.write_bytes(b'x')
    monkeypatch.setattr(ls, 'PROT_DMND', str(faa))
    r2 = selfcheck._check_homolog_dbs()
    assert r2['ok'] is True, r2
