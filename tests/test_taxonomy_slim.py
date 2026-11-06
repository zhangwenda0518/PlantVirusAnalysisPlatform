# -*- coding: utf-8 -*-
"""build_slim_taxonomy 精简裁剪回归测试（合成小 dmp，不触网、不碰真实库）。

背景（2026-11-05）：NCBI taxonomy 全库 ~303 万 taxon（nodes+names 合计
~578MB），本平台真正吃谱系的只有病毒域。精简口径：病毒域(10239)子树 +
到根祖先链 + merged 整份；宿主 taxid 查无时由 kunpeng 建库入口在线核对
兜底（taxid_online_check），本测试只锁裁剪正确性与缓存读取兜底。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from Virus_Platform_Core.taxonomy import (  # noqa: E402
    build_slim_taxonomy, taxid_lookup, taxid_name, taxid_online_check,
    TaxonomyGraph,
)

_FAIL = []


def check(name, cond, detail=''):
    if cond:
        print('  ok   %s' % name)
    else:
        print('  FAIL %s  %s' % (name, detail))
        _FAIL.append(name)


def _make_full_dmp(d):
    """合成一份微型「全量」taxonomy（病毒链 + 植物链 + 细菌 + merged）。"""
    os.makedirs(d, exist_ok=True)
    nodes = [
        (1, 1, 'no rank'),
        (131567, 1, 'no rank'),          # cellular organisms（不保留）
        (10239, 1, 'superkingdom'),      # Viruses（保留）
        (20001, 10239, 'family'),
        (20002, 20001, 'genus'),
        (20003, 20002, 'species'),
        (30001, 131567, 'kingdom'),      # 植物界替身（不保留）
        (30002, 30001, 'species'),
        (40001, 131567, 'species'),      # 细菌替身（不保留）
    ]
    names = [
        (1, 'root', 'scientific name'),
        (131567, 'cellular organisms', 'scientific name'),
        (10239, 'Viruses', 'scientific name'),
        (20001, 'Testviridae', 'scientific name'),
        (20002, 'Testvirus', 'scientific name'),
        (20002, 'Oldtestvirus', 'equivalent name'),      # 非 sci，应被裁掉
        (20003, 'Test virus species', 'scientific name'),
        (30001, 'Plant stand-in', 'scientific name'),
        (30002, 'Test plant', 'scientific name'),
        (40001, 'Test bacterium', 'scientific name'),
    ]
    merged = [(50001, 20003), (50002, 30002)]    # 一条病毒合并 + 一条植物合并
    with open(os.path.join(d, 'nodes.dmp'), 'w', encoding='utf-8',
              newline='') as f:
        f.write(''.join(f'{t}\t|\t{p}\t|\t{r}\t|\n' for t, p, r in nodes))
    with open(os.path.join(d, 'names.dmp'), 'w', encoding='utf-8',
              newline='') as f:
        f.write(''.join(f'{t}\t|\t{n}\t|\t\t|\t{c}\t|\n' for t, n, c in names))
    with open(os.path.join(d, 'merged.dmp'), 'w', encoding='utf-8',
              newline='') as f:
        f.write(''.join(f'{o}\t|\t{n}\t|\n' for o, n in merged))
    return nodes, names, merged


def test_slim_build_and_load(tmp_path, monkeypatch):
    """裁剪口径：病毒子树+祖先链保留，其余全裁；merged 整份；图可加载。"""
    src = str(tmp_path / 'full')
    dst = str(tmp_path / 'slim')
    nodes, names, merged = _make_full_dmp(src)
    # check_path 平台封闭会拒 tmp 目录——测试期把 write_roots 指向 tmp
    import Virus_Platform_Core.config as cfg
    monkeypatch.setattr(cfg, 'write_roots', lambda: [str(tmp_path)])
    info = build_slim_taxonomy(src_dir=src, dst_dir=dst)

    check('全库计数', info['n_total'] == len(nodes), str(info['n_total']))
    kept = []
    with open(os.path.join(dst, 'nodes.dmp'), encoding='utf-8') as f:
        kept = [int(l.split('\t', 1)[0]) for l in f]
    check('保留集=病毒子树+root', kept == [1, 10239, 20001, 20002, 20003],
          str(kept))

    with open(os.path.join(dst, 'names.dmp'), encoding='utf-8') as f:
        nlines = [l.rstrip('\n') for l in f]
    check('names 只留 scientific name',
          all('scientific name' in l for l in nlines) and len(nlines) == 5,
          str(nlines))

    with open(os.path.join(dst, 'merged.dmp'), encoding='utf-8') as f:
        merged_out = f.read()
    check('merged 整份复制',
          merged_out == ''.join(f'{o}\t|\t{n}\t|\n' for o, n in merged),
          repr(merged_out[:80]))

    # 精简库可被 TaxonomyGraph 正常加载并回答谱系
    g = TaxonomyGraph(dst)
    g.load()
    check('病毒物种名', g.name(20003) == 'Test virus species')
    check('谱系链',
          [t for t, _r, _n in g.lineage(20003)] == [20003, 20002, 20001,
                                                    10239, 1],
          str(g.lineage(20003)))
    check('植物已裁掉', 30002 not in g.parent)
    check('植物 merged 仍在（整份）', 50002 in g.merged)

    st, extra = taxid_lookup(20003, tax_dir=dst)
    check('taxid_lookup 病毒 ok', st == 'ok')
    st, extra = taxid_lookup(30002, tax_dir=dst)
    check('taxid_lookup 植物 missing', st == 'missing')
    st, extra = taxid_lookup(50001, tax_dir=dst)
    check('taxid_lookup 合并 taxid', st == 'merged' and extra == 20003,
          f'{st}/{extra}')


def test_taxid_name_cache_fallback(tmp_path, monkeypatch):
    """taxid_name 本地查无 → 读在线核对缓存；缓存缺失给 default。"""
    src = str(tmp_path / 'full')
    dst = str(tmp_path / 'slim')
    _make_full_dmp(src)
    build_slim_taxonomy(src_dir=src, dst_dir=dst)

    cache_file = tmp_path / 'online_taxid_cache.json'
    import json
    cache_file.write_text(json.dumps({
        '30002': {'ok': True, 'name': 'Cached Plantus', 'checked_at': 'x'},
        '40001': {'ok': False, 'name': '', 'checked_at': 'x'},
    }), encoding='utf-8')
    import Virus_Platform_Core.taxonomy as tx
    monkeypatch.setattr(tx, '_online_cache_path', lambda: str(cache_file))

    check('本地有 → 学名', taxid_name(20003, tax_dir=dst)
          == 'Test virus species')
    check('本地无 → 缓存兜底', taxid_name(30002, tax_dir=dst)
          == 'Cached Plantus')
    check('缓存 ok=False → default', taxid_name(40001, tax_dir=dst) == '')

    # taxid_online_check 缓存命中：不触网直接返回
    ok, name = taxid_online_check(30002)
    check('online_check 缓存命中', ok is True and name == 'Cached Plantus',
          f'{ok}/{name}')


def test_pack_and_prepare_roundtrip(tmp_path, monkeypatch):
    """精简档打包 → 档位选择（精简优先）→ prepare_taxonomy 从档解压回环。"""
    import shutil
    import Virus_Platform_Core.config as cfg
    from Virus_Platform_Core.taxonomy import (pack_slim_archive,
                                              _pick_taxdump_tar,
                                              prepare_taxonomy,
                                              taxonomy_ready, SLIM_TAR_NAME)

    monkeypatch.setattr(cfg, 'write_roots', lambda: [str(tmp_path)])
    monkeypatch.setitem(cfg.DIRS, 'databases', str(tmp_path / 'db'))
    monkeypatch.setitem(cfg.DIRS, 'taxonomy',
                        str(tmp_path / 'db' / 'tax_db'))
    os.makedirs(cfg.DIRS['databases'], exist_ok=True)

    src = str(tmp_path / 'full')
    _make_full_dmp(src)
    build_slim_taxonomy(src_dir=src, dst_dir=cfg.DIRS['taxonomy'])

    out = pack_slim_archive()
    check('打包产物名', os.path.basename(out) == SLIM_TAR_NAME
          and os.path.isfile(out))

    # 档位选择：精简档优先于官方全量档
    full = os.path.join(cfg.DIRS['databases'], 'new_taxdump.tar.gz')
    with open(full, 'wb') as f:
        f.write(b'not a real tar')
    check('精简档优先', _pick_taxdump_tar() == out)

    # 删当前库 → prepare_taxonomy 从本地精简档纯离线回环（不触网）
    shutil.rmtree(cfg.DIRS['taxonomy'])
    check('库已删未就绪', not taxonomy_ready())
    tax_dir = prepare_taxonomy()
    check('回环解压后就绪', taxonomy_ready())
    g = TaxonomyGraph(tax_dir)
    g.load()
    check('回环查询', g.name(20003) == 'Test virus species'
          and 30002 not in g.parent)


if __name__ == '__main__':
    print('请用 pytest 运行: python -m pytest tests/test_taxonomy_slim.py -q')
    raise SystemExit(2)