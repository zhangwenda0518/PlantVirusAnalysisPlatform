# -*- coding: utf-8 -*-
"""公共检索/下载的 accession 解析（不联网，用假的 HTTP 层）。

守三件在 2026-11-02 现场炸过的事：
1. **7 位编号**：DRX1144321 / DRR1175147 这种新号，旧代码用 `DRX\\d{6}` 抓成
   DRX114432，拼出来的 DDBJ 目录必然 404，用户看到「DDBJ 目录不可达」。
   这里用假 XML 断言拼出的 URL 里是**完整**的 DRX。
2. **报错不能互相盖掉**：DRR 先走 DDBJ 再回落 ENA，旧代码 `except Exception`
   把 DDBJ 的真实原因丢了，只剩「ENA 未返回…（run 可能不存在或未公开）」，
   明明数据是有的。
3. **三库都没有时要能走 sracha**：NCBI SRA 有 run 记录就说明数据在，回落到
   sracha 直接拉，并把理由写进 note（不静默替换）。
"""
import os
import sys

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAILS = []


def check(ok, msg):
    print(('  ✔ ' if ok else '  ✘ ') + msg)
    if not ok:
        FAILS.append(msg)


FAKE_XML = '''<EXPERIMENT_PACKAGE_SET><EXPERIMENT_PACKAGE>
<EXPERIMENT accession="DRX1144321"/>
<STUDY_REF refname="DRP020748" accession="DRP020748"/>
<RUN alias="CRR1079210" accession="DRR1175147" is_public="true">
<EXPERIMENT_REF refname="DRX1144321" accession="DRX1144321"/>
</RUN></EXPERIMENT_PACKAGE>
<EXPERIMENT_PACKAGE><STUDY_REF accession="DRA031173"/></EXPERIMENT_PACKAGE>
</EXPERIMENT_PACKAGE_SET>'''

FAKE_INDEX = ('<a href="DRR1175147_1.fastq.bz2">1</a>'
              '<a href="DRR1175147_2.fastq.bz2">2</a>')


def main():
    sys.path.insert(0, ROOT)
    from Virus_Platform_Core import public_data as pd

    # ① 7 位 DRX / DRA 不被截断
    calls = []

    def fake_get(url, *a, **kw):
        calls.append(url)
        if 'esearch' in url:
            return '<eSearchResult><Id>47251987</Id></eSearchResult>'
        if 'efetch' in url:
            return FAKE_XML
        return FAKE_INDEX

    old_get = pd._http_get
    pd._http_get = fake_get
    try:
        d = pd._resolve_ddbj('DRR1175147')
        urls = [f['url'] for f in d['files']]
        check(d['db'] == 'DDBJ' and len(urls) == 2, 'DDBJ 解析出 2 个文件')
        check(all('/DRX1144321/' in u for u in urls),
              '7 位 DRX 未被截断（URL 含 /DRX1144321/）')
        check(all(u.endswith(('_1.fastq.bz2', '_2.fastq.bz2')) for u in urls),
              '文件名按 run 号精确匹配')
    finally:
        pd._http_get = old_get

    # ② 两库都失败时，错误信息要合并；有 sracha 就回落
    old_ddbj, old_ena, old_sracha = pd._resolve_ddbj, pd._resolve_ena, pd.sracha_path
    try:
        pd._resolve_ddbj = lambda a: (_ for _ in ()).throw(ValueError('DDBJ-DIR-404'))
        pd._resolve_ena = lambda a: (_ for _ in ()).throw(ValueError('ENA-EMPTY'))
        pd.sracha_path = lambda: r'C:\fake\sracha.exe'
        d = pd.resolve_accession('DRR1175147')
        check(d['db'] == 'SRA(sracha)' and d['files'][0].get('sracha') is True,
              '两库都失败 → 回落到 sracha')
        check('DDBJ-DIR-404' in d.get('note', '') and 'ENA-EMPTY' in d.get('note', ''),
              '回落理由里带着两边的原因（不吞错）')

        pd.sracha_path = lambda: None
        try:
            pd.resolve_accession('DRR1175147')
            check(False, '没有 sracha 时应当报错')
        except ValueError as e:
            msg = str(e)
            check('DDBJ-DIR-404' in msg and 'ENA-EMPTY' in msg,
                  '没有 sracha 时报错同时列出两边原因')
        # SRR/ERR 也一样：ENA 空 → sracha
        pd._resolve_ena = lambda a: (_ for _ in ()).throw(ValueError('ENA-EMPTY'))
        pd.sracha_path = lambda: r'C:\fake\sracha.exe'
        d = pd.resolve_accession('SRR1234567')
        check(d['db'] == 'SRA(sracha)', 'SRR 在 ENA 无行时也走 sracha')
        # CRR 不参与这条回落（走 NGDC）
        check(pd._sracha_fallback('CRR1079210', 'x') is None,
              'CRR 不走 sracha 回落（留给 NGDC）')
    finally:
        pd._resolve_ddbj, pd._resolve_ena, pd.sracha_path = old_ddbj, old_ena, old_sracha

    if FAILS:
        print('FAILED: %d 项' % len(FAILS))
        return 1
    print('公共检索 accession 解析检查通过 ✔')
    return 0


if __name__ == '__main__':
    sys.exit(main())
