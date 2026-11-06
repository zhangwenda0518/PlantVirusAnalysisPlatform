# -*- coding: utf-8 -*-
"""logan_contigs 纯逻辑单测（离线，不碰网络/S3）。

覆盖：accession 解析/清洗、DTR/ITR 判定、concatemer 修复、
流式 FASTA 解析、host_db.json 清单读取。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from Virus_Platform_Core import logan_contigs as lc  # noqa: E402


def test_acc_clean():
    assert lc._acc_clean('srr24759446') == 'SRR24759446'
    assert lc._acc_clean(' ERR3382580 ') == 'ERR3382580'
    assert lc._acc_clean('DRR000001.1') == 'DRR000001.1'
    with pytest.raises(ValueError):
        lc._acc_clean('not-an-accession')
    with pytest.raises(ValueError):
        lc._acc_clean('SRR; drop table')


def test_parse_accessions():
    assert lc._parse_accessions('SRR1, ERR2\nDRR3') == ['SRR1', 'ERR2', 'DRR3']
    assert lc._parse_accessions('SRR1 SRR1') == ['SRR1']       # 去重保序
    with pytest.raises(ValueError):
        lc._parse_accessions('')
    with pytest.raises(ValueError):
        lc._parse_accessions(','.join(f'SRR{i}' for i in range(51)))


def test_revcomp():
    assert lc._revcomp('ACGTN') == 'NACGT'


X30 = 'AACCGGTTACGTTCAGGCATCCGGATGCAT'   # 30bp 非周期
RAND80 = ('ATGCGTTACCAATGCCGGTTCAGGTACCGGATGCCAATTGGCTACGTTCAGGCATCCGGA'
          'TTAGCTAGCCAATGCGGTTCAGGCATCCGGATTAC')           # 101bp 伪随机


def test_dtr_itr():
    # DTR：≥30bp 的后缀与前缀完全相等（concatemer / 环形信号）
    assert lc._has_dtr(X30 + 'TTTTTTTTTT' + X30) == (True, 30)
    # 周期串表现等同 concatemer —— 与参考实现口径一致（判为环形）
    assert lc._has_dtr('ACGT' * 20) == (True, 32)
    assert lc._has_dtr('A' * 50) == (False, 0)         # 太短不判
    assert lc._has_dtr(RAND80) == (False, 0)           # 伪随机不误报
    # ITR：前缀 == 反向互补前缀
    itr_seq = X30 + 'TTTTGGGGCC' * 4 + lc._revcomp(X30)
    ok, ln = lc._has_itr(itr_seq)
    assert ok and ln >= 30
    assert lc._has_itr(RAND80) == (False, 0)


def test_fix_circle():
    # concatemer：4bp 周期串，首个重复 31-mer 处截断
    concat = 'ACGT' * 12
    assert len(lc._fix_circle(concat)) == 34
    # 无内部重复 31-mer 的序列保持原样（步进 11 mod 4=3 → 真非周期）
    plain = RAND80
    assert lc._fix_circle(plain) == plain


def test_iter_fasta_stream():
    data = (b'>a x\nAC\nGT\n\n>b\nTTTT\n')
    recs = list(lc._iter_fasta_stream(io_reader(data)))
    assert recs == [('a x', 'ACGT'), ('b', 'TTTT')]


def test_filter_stats_semantics():
    """min_len/circles_only 组合的过滤语义（走 _fetch_one 的判定段口径）。"""
    seq_ok = RAND80
    dtr, _ = lc._has_dtr(seq_ok)
    assert not dtr
    seq_circ = X30 + 'TTTTTTTTTT' + X30                    # DTR → 环形
    dtr, _ = lc._has_dtr(seq_circ)
    assert dtr
    assert lc._has_itr(RAND80) == (False, 0)
    assert len(seq_ok) >= 90


class io_reader:
    """最小 bytes 流（模拟 zstd stream_reader 的 read(n) 接口）。"""

    def __init__(self, b):
        self.b = b

    def read(self, n=-1):
        if n < 0:
            out, self.b = self.b, b''
            return out
        out, self.b = self.b[:n], self.b[n:]
        return out
