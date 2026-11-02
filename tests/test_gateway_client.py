# -*- coding: utf-8 -*-
"""gateway_client.resolve_accessions 的离线单测（mock urllib，不打真网关）。

2026-10-30 pdprep 接自建在线库（39.106.101.94）时新增。
"""
import json
import sys
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from Virus_Platform_Core import gateway_client as gw


def _resp(payload):
    m = mock.MagicMock()
    m.read.return_value = json.dumps(payload).encode('utf-8')
    m.__enter__ = mock.MagicMock(return_value=m)
    m.__exit__ = mock.MagicMock(return_value=False)
    return m


class GatewayClientTests(unittest.TestCase):

    def test_resolve_species_exact_hit(self):
        rows = [{'Accession': 'A1.1', 'Species_NCBI': 'Tobacco mosaic virus'},
                {'Accession': 'A2.1', 'Species_NCBI': 'Tobacco mosaic virus'}]
        with mock.patch.object(gw.urllib.request, 'urlopen',
                               return_value=_resp({'data': rows, 'meta': {'total': 2}})):
            accs, info = gw.resolve_accessions(species='Tobacco mosaic virus', max_records=5)
        self.assertEqual(accs, ['A1.1', 'A2.1'])
        self.assertEqual(info['strategy'], '学名等值')
        self.assertEqual(info['dataset'], 'reference/sequences')

    def test_full_only_filters_completeness(self):
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured['url'] = req.full_url
            return _resp({'data': [{'Accession': 'NC_1.1'}], 'meta': {'total': 1}})

        with mock.patch.object(gw.urllib.request, 'urlopen', side_effect=fake_urlopen):
            accs, info = gw.resolve_accessions(species='X virus', full_only=True)
        self.assertIn('Nuc_Completeness=complete', captured['url'])
        self.assertEqual(info['dataset'], 'reference/sequences')

    def test_exact_miss_falls_back_to_fuzzy(self):
        # 学名等值 0 条 → 模糊 q= 命中
        payload_exact = {'data': [], 'meta': {'total': 0}}
        payload_fuzzy = {'data': [{'Accession': 'F1.1'}], 'meta': {'total': 1}}
        with mock.patch.object(
                gw.urllib.request, 'urlopen',
                side_effect=[_resp(payload_exact), _resp(payload_fuzzy)]):
            accs, info = gw.resolve_accessions(species='TMV', max_records=5)
        self.assertEqual(accs, ['F1.1'])
        self.assertEqual(info['strategy'], '模糊检索')

    def test_no_match_raises_actionable(self):
        with mock.patch.object(gw.urllib.request, 'urlopen',
                               return_value=_resp({'data': [], 'meta': {'total': 0}})):
            with self.assertRaises(RuntimeError) as ctx:
                gw.resolve_accessions(species='不存在的病毒', max_records=5)
        self.assertIn('NCBI 检索', str(ctx.exception))

    def test_requires_species_or_taxid(self):
        with self.assertRaises(ValueError):
            gw.resolve_accessions()

    def test_taxid_strategy(self):
        with mock.patch.object(gw.urllib.request, 'urlopen',
                               return_value=_resp({'data': [
                                   {'Accession': 'T1.1'}], 'meta': {'total': 1}})):
            accs, info = gw.resolve_accessions(taxid='12242')
        self.assertEqual(accs, ['T1.1'])
        self.assertEqual(info['strategy'], 'taxid 等值')

    def test_max_records_caps(self):
        rows = [{'Accession': f'A{i}.1'} for i in range(50)]
        with mock.patch.object(gw.urllib.request, 'urlopen',
                               return_value=_resp({'data': rows, 'meta': {'total': 50}})):
            accs, _ = gw.resolve_accessions(species='X virus', max_records=3)
        self.assertEqual(len(accs), 3)

    def test_pagination(self):
        ps = gw.PAGE_SIZE
        p1 = {'data': [{'Accession': f'A{i}.1'} for i in range(ps)], 'meta': {'total': ps * 2}}
        p2 = {'data': [{'Accession': f'B{i}.1'} for i in range(ps)], 'meta': {'total': ps * 2}}
        with mock.patch.object(gw.urllib.request, 'urlopen',
                               side_effect=[_resp(p1), _resp(p2)]):
            accs, _ = gw.resolve_accessions(species='X virus', max_records=ps * 2)
        self.assertEqual(len(accs), ps * 2)


if __name__ == '__main__':
    unittest.main()
