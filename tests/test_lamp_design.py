# -*- coding: utf-8 -*-
"""lamp_design 内核纯函数单测 + 合成数据端到端 + UI 契约。

合成数据：固定随机种子的 450bp 序列（已知能出组）；anchor 用中部
20nt 区间验证「F2-B2 产物包住锚区」约束（方案 A 的预演）。

MAFFT 缺失时端到端自动跳过；其余用例为纯函数。
"""
import os
import shutil
import uuid

import pytest

from Virus_Platform_Core import lamp_design as ld

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
TOOLS = os.path.join(_ROOT, 'webapp', 'templates', 'tools.html')
I18N = os.path.join(_ROOT, 'webapp', 'static', 'i18n.js')
_PLAT_TEST_DIR = os.path.join(_ROOT, 'run', 'tests_lamp')


@pytest.fixture()
def plat_dir():
    d = os.path.join(_PLAT_TEST_DIR, uuid.uuid4().hex[:10])
    os.makedirs(d, exist_ok=True)
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _rand_seq(n, seed=42):
    import random
    rng = random.Random(seed)
    return ''.join(rng.choice('ACGT') for _ in range(n))


# ---------------------------------------------------------------
# 纯函数
# ---------------------------------------------------------------
def test_seg_candidates_filters():
    seq = _rand_seq(450)
    cands = ld._seg_candidates(seq, 'F2')
    assert cands, '随机 450bp 应有 F2 候选'
    sp = ld._SEG['F2']
    for c in cands:
        assert sp['lo'] <= c['len'] <= sp['hi']
        assert sp['tm'][0] <= c['tm'] <= sp['tm'][1]
        assert ld._GC_LO <= c['gc'] <= ld._GC_HI
        assert ld.max_homo_run(c['seq']) < 5
        assert not ld.three_prime_gc(c['seq'])
    # dev 升序（_pick 依赖按 dev 排序）
    devs = [c['dev'] for c in cands]
    assert devs == sorted(devs)


def test_design_lamp_set_structure():
    seq = _rand_seq(450)
    sets = ld.design_lamp_set(seq)
    assert sets, '应出组'
    for s in sets:
        # 坐标：1 基，段窗内可回取
        for key in ('f3', 'f2', 'f1', 'b1', 'b2', 'b3'):
            d = s[key]
            st0 = d['start'] - 1
            assert seq[st0:st0 + d['len']] == d['seq'], key
        # FIP = revcomp(F1区) + TTTT + F2区；BIP = B1区 + TTTT + revcomp(B2区)
        f1 = seq[s['f1']['start'] - 1:s['f1']['start'] - 1 + s['f1']['len']]
        b2 = seq[s['b2']['start'] - 1:s['b2']['start'] - 1 + s['b2']['len']]
        assert s['fip'] == ld.revcomp(f1) + 'TTTT' + s['f2']['seq']
        assert s['bip'] == s['b1']['seq'] + 'TTTT' + ld.revcomp(b2)
        # 间距约束
        # 间距断言动态读内核常量（防口径调整后断言漂移）
        g_f3f2 = s['f2']['start'] - (s['f3']['start'] + s['f3']['len'])
        assert ld._GAP_F3F2[0] <= g_f3f2 <= ld._GAP_F3F2[1]
        g_f2f1 = s['f1']['start'] - (s['f2']['start'] + s['f2']['len'])
        assert ld._GAP_F2F1[0] <= g_f2f1 <= ld._GAP_F2F1[1]
        g_core = s['b1']['start'] - (s['f1']['start'] + s['f1']['len'])
        assert ld._GAP_CORE[0] <= g_core <= ld._GAP_CORE[1]
        g_b1b2 = s['b2']['start'] - (s['b1']['start'] + s['b1']['len'])
        assert ld._GAP_B1B2[0] <= g_b1b2 <= ld._GAP_B1B2[1]
        g_b2b3 = s['b3']['start'] - (s['b2']['start'] + s['b2']['len'])
        assert ld._GAP_B2B3[0] <= g_b2b3 <= ld._GAP_B2B3[1]
        # product = b2_start - f2_end + 1（两 3' 端所夹）
        assert s['product_bp'] == (s['b2']['start'] - 1) - (s['f2']['start'] - 1 + s['f2']['len']) + 1
        assert ld._PRODUCT[0] <= s['product_bp'] <= ld._PRODUCT[1]
    # 按 score 降序
    scores = [s['score'] for s in sets]
    assert scores == sorted(scores, reverse=True)


def test_anchor_span_containement():
    """方案 A 预演：给了 anchor 时 F2-B2 产物必须包住锚区。"""
    seq = _rand_seq(450)
    a0, a1 = 200, 220        # 0 基半开锚区间
    sets = ld.design_lamp_set(seq, anchor_span=(a0, a1))
    assert sets
    for s in sets:
        f2_end0 = (s['f2']['start'] - 1) + s['f2']['len']
        b2_start0 = s['b2']['start'] - 1
        assert f2_end0 < a0 and a1 < b2_start0


def test_use_loop_off():
    seq = _rand_seq(450)
    sets = ld.design_lamp_set(seq, params={'use_loop': '0'})
    assert sets
    for s in sets:
        assert 'lf_seq' not in s and 'lb_seq' not in s


def test_cons_filter():
    """保守分传参：全 0 保守 -> 无候选（段窗 min 约束）。"""
    seq = _rand_seq(450)
    cons = [0.1] * len(seq)
    assert ld.design_lamp_set(seq, cons=cons) == []


# ---------------------------------------------------------------
# 端到端（MAFFT 缺失自动 skip）
# ---------------------------------------------------------------

def test_presets_harmless():
    """预设只扩不缩（设计意图层）：预设的段候选数是默认的超集。

    注：分桶 cap（每 100bp 取 dev 最优 15 个）会在候选变多时改变桶内
    选择，出组数可能抖动；候选超集性才是预设语义的准确不变量。
    """
    import random
    rng = random.Random(20261105)
    seq = ''.join(rng.choice('ACGTACGTAC') for _ in range(450))

    def n_cands(params):
        preset = str(params.get('preset') or '') or None
        kw = {}
        if preset == 'high_gc':
            kw = {'gc_lo': ld._GC_LO, 'gc_hi': 75.0, 'tm_off': 2.0}
        elif preset == 'high_at':
            kw = {'gc_lo': 20.0, 'gc_hi': ld._GC_HI, 'tm_off': 0.0}
        return len(ld._seg_candidates(seq, 'F2', **kw))

    base = len(ld._seg_candidates(seq, 'F2'))
    assert base > 0
    for preset in ('high_gc', 'high_at'):
        n_p = n_cands({'preset': preset})
        assert n_p >= base, f'{preset} 收窄了段候选（{n_p} < {base}）'


def test_gc_tm_override():
    """自定义 GC/Tm 覆盖：高GC序列默认 0 组，放宽 GC 上限后可出组。"""
    import random
    rng = random.Random(7)
    seq = ''.join(rng.choice('GGCCGGCGAT') for _ in range(450))
    assert len(ld.design_lamp_set(seq)) == 0      # 默认 GC 40-60 挡
    n = len(ld.design_lamp_set(seq, {'gc_min': 30, 'gc_max': 80}))
    assert n > 0, '放宽 GC 上限后应出组'

def test_run_chain_synthetic(plat_dir):
    from Virus_Platform_Core.config import get_config
    if not os.path.isfile(str(get_config().tool('mafft'))):
        pytest.skip('MAFFT 不可用')
    seq = _rand_seq(450)
    tgt = os.path.join(plat_dir, 't.fa')
    with open(tgt, 'w', encoding='utf-8') as f:
        f.write('>isoA\n' + seq + '\n>isoB\n' + seq + '\n')
    summary, result = ld.run_chain(tgt, {'top_n': 5}, plat_dir)
    assert summary['tool'] == 'lamp'
    assert summary['n_sets_output'] > 0
    root = os.path.join(plat_dir, 'lamp')
    for fn in ('lamp_sets.tsv', 'lamp_order.tsv', 'report.md',
               'summary.json', 'sequence_view.html',
               'primer_layout.png'):
        assert os.path.isfile(os.path.join(root, fn)), fn
    head = open(os.path.join(root, 'lamp_sets.tsv'),
                encoding='utf-8').readline()
    for col in ('f3_seq', 'fip', 'bip', 'product_bp', 'score'):
        assert col in head.split('\t'), col
    order_head = open(os.path.join(root, 'lamp_order.tsv'),
                      encoding='utf-8').readline()
    assert 'role' in order_head and 'seq' in order_head


# ---------------------------------------------------------------
# 回归：报告口径必须与引擎常量同源（2026-11-06）
# ---------------------------------------------------------------
def _fmt_num(v):
    """与 lamp_design._num_text 同规则：整数无小数点，浮点 %g。"""
    return ('%g' % v) if isinstance(v, float) else str(v)


def test_method_lines_match_engine_constants():
    """方法与参数逐值等于 _SEG/_GAP/_LINKER/_PRODUCT 实值。

    期望值全部从实现常量生成，不写死数字——常量调整后本用例自动跟随，
    只有报告文本再次偏离实现时才会失败（防旧版写死参数的口径漂移复发）。
    """
    text = '\n'.join(ld._method_lines({}))
    for key, sp in ld._SEG.items():
        lo, hi, _opt = sp['tm']
        expect = (f"{key} {sp['lo']}-{sp['hi']}nt "
                  f"Tm {_fmt_num(lo)}-{_fmt_num(hi)}")
        assert expect in text, expect
    for label, gap in (
            ('F3-F2', ld._GAP_F3F2), ('F2-F1', ld._GAP_F2F1),
            ('F1-B1', ld._GAP_CORE), ('B1-B2', ld._GAP_B1B2),
            ('B2-B3', ld._GAP_B2B3)):
        assert f'{label} {gap[0]}-{gap[1]}' in text, label
    assert (f"F2-B2 产物 {ld._PRODUCT[0]}-{ld._PRODUCT[1]}bp"
            in text)
    assert (f"GC：默认 {_fmt_num(ld._GC_LO)}-{_fmt_num(ld._GC_HI)}%"
            in text)
    assert f"FIP=F1c+{ld._LINKER}+F2" in text
    assert f"BIP=B1+{ld._LINKER}+B2c" in text
    # 旧版写死且与实现相反的口径不得残留
    assert 'B1c+TTTT' not in text
    assert 'Tm 59.5-62.5' not in text
    # 自定义产物区间按显式值渲染（空白串视为未填）
    custom = '\n'.join(ld._method_lines(
        {'product_min': '150', 'product_max': ''}))
    assert f"F2-B2 产物 150-{ld._PRODUCT[1]}bp" in custom


def test_report_written_with_engine_parameters(plat_dir):
    """report.md 的「方法与参数」段确实来自 _method_lines（无第二份文案）。"""
    files = {}
    ld.write_outputs(str(plat_dir), [], {}, {}, files)
    with open(files['report'], encoding='utf-8') as f:
        txt = f.read()
    assert '## 方法与参数' in txt
    for line in ld._method_lines({}):
        assert line in txt, line


def _fake_segments(monkeypatch, seq, table):
    """用受控候选表替换 _seg_candidates（坐标/Tm/dev 全由用例指定）。"""
    def fake(seq_, key, **kw):
        return [dict(c) for c in table.get(key, [])]
    monkeypatch.setattr(ld, '_seg_candidates', fake)


def _cand(seq, start, length, dev):
    return {'start': start, 'len': length, 'end': start + length,
            'seq': seq[start:start + length], 'tm': 60.0, 'gc': 50.0,
            'dev': dev}


def test_explicit_zero_params_not_swallowed(monkeypatch):
    """显式 0 是有效值：tm_offset=0 不被 preset 的 2.0 覆盖；'' 视为未填。

    旧实现 `params.get('tm_offset') or _pre.get(...) or 0.0` 会把 0 当假值，
    high_gc 预设下用户显式关掉 Tm 偏移（0）反而被抬成 +2。GC 同理。
    """
    seq = ('ACGT' * 120)[:450]
    seen = {}

    def fake(seq_, key, **kw):
        seen[key] = dict(kw)
        return []
    monkeypatch.setattr(ld, '_seg_candidates', fake)

    ld.design_lamp_set(seq, {'preset': 'high_gc', 'tm_offset': 0})
    assert seen['F3']['tm_off'] == 0.0
    ld.design_lamp_set(seq, {'preset': 'high_gc', 'tm_offset': ''})
    assert seen['F3']['tm_off'] == 2.0        # 空串 = 未填 → 用 preset
    ld.design_lamp_set(seq, {'preset': 'high_gc', 'tm_offset': '0'})
    assert seen['F3']['tm_off'] == 0.0        # web 表单字符串 0
    ld.design_lamp_set(seq, {'gc_min': 0, 'gc_max': ''})
    assert seen['F3']['gc_lo'] == 0.0
    assert seen['F3']['gc_hi'] == ld._GC_HI


def test_f3_prefilter_uses_end_semantics(monkeypatch):
    """F3 间距按 end 判定：起点在窗内但终点越界的候选不得挤掉合法候选。

    旧实现按 start 筛 F3 窗，dev 最优的 3 个候选（end 使间距 >60）占满
    top-3，合法候选排在第 4 位被截掉 → 组装 0 组。
    """
    seq = ('ACGT' * 120)[:450]
    table = {
        # 合法：f2.start(150) - end(110) = 40，dev 最差（排第 4）
        'F3': [_cand(seq, 66, 20, 0.1), _cand(seq, 67, 20, 0.2),
               _cand(seq, 68, 20, 0.3), _cand(seq, 90, 20, 1.0)],
        'F2': [_cand(seq, 150, 20, 0.5)],
        'F1': [_cand(seq, 180, 22, 0.5)],
        'B1': [_cand(seq, 242, 22, 0.5)],
        'B2': [_cand(seq, 290, 20, 0.5)],
        'B3': [_cand(seq, 310, 20, 0.5)],
    }
    _fake_segments(monkeypatch, seq, table)
    sets = ld.design_lamp_set(seq, {'use_loop': '0'})
    assert len(sets) == 1
    s = sets[0]
    assert s['f3']['start'] == 91                 # 1 基 = 0 基 90
    assert ld._GAP_F3F2[0] <= s['f2']['start'] - (s['f3']['start']
                                                 - 1 + s['f3']['len']) \
        <= ld._GAP_F3F2[1]


def test_b1_candidates_per_f1_variant(monkeypatch):
    """B1 必须按每个 F1 变体分别取：只按 f1s[0] 会让其余变体无组可出。

    F1 变体 a（dev 最优）的核心区间够不到唯一 B1 候选；变体 b 够得到。
    旧实现只用 f1s[0] 建 B1 池，骨架被预筛误杀 → 0 组。
    """
    seq = ('ACGT' * 120)[:450]
    table = {
        'F3': [_cand(seq, 110, 20, 0.5)],
        'F2': [_cand(seq, 150, 20, 0.5)],
        # a: end=202；b: end=232（两者间距都在 F2-F1 窗内）
        'F1': [_cand(seq, 180, 22, 0.1), _cand(seq, 210, 22, 0.2)],
        # 仅对 b 合法：330-232=98（a 为 128 > core_max 120）
        'B1': [_cand(seq, 330, 20, 0.5)],
        # B1B2: 365-350=15；产物 365-170+1=196
        'B2': [_cand(seq, 365, 20, 0.5)],
        'B3': [_cand(seq, 385, 20, 0.5)],
    }
    _fake_segments(monkeypatch, seq, table)
    sets = ld.design_lamp_set(seq, {'use_loop': '0'})
    assert len(sets) == 1
    s = sets[0]
    assert s['f1']['start'] == 211 and s['b1']['start'] == 331
    assert ld._GAP_CORE[0] <= s['b1']['start'] - (s['f1']['start']
                                                 - 1 + s['f1']['len']) \
        <= ld._GAP_CORE[1]


# ---------------------------------------------------------------
# UI 契约（方案 B 工具卡）
# ---------------------------------------------------------------
def test_ui_contract():
    src = open(TOOLS, encoding='utf-8').read()
    assert 'id="t-lamp"' in src
    for cid in ('lp_input', 'lp_product_min', 'lp_product_max',
                'lp_strict', 'lp_loop', 'lp_topn', 'toolrun-lamp',
                'lp_gc_min', 'lp_gc_max', 'lp_tm_offset'):
        assert f'id="{cid}"' in src, cid
    for fn in ('runLamp', 'loadLampResult'):
        assert fn + '(' in src, fn
    assert "_submitTool('lamp'" in src
    i18n = open(I18N, encoding='utf-8').read()
    for k in ('tk.lpH2', 'tk.lpHint', 'tk.lpRun', 'tk.lpPresetGc',
              'nv.t-lamp.t', 'nv.t-lamp.d'):
        assert i18n.count(k + "'") >= 2, k   # 中英各一份
    from Virus_Platform_Core.web import tools_api
    assert 'lamp' in tools_api.TOOL_REGISTRY
    from Virus_Platform_Core.web.pages import NAV_GROUPS
    nav = {it.get('id') or it.get('href')
           for g in NAV_GROUPS if g['id'] == 'detect' for it in g['items']}
    assert 't-lamp' in nav
