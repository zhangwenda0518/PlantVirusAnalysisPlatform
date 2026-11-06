#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""宿主基因组「选用 accession → 自动切 NCBI 源」不变量守卫（静态，秒级）。

为什么需要这个测试
-----------------
2026-11-02 用户实测踩坑：在「数据资源」页先点「🔍 查询 NCBI 组装」，从表里点
「选用」填进 `GCA_003865215.1`，再点「⬇ 下载到 host-db/」，得到的是一句
`启动失败: gget/Ensembl 源不支持 accession（那是 NCBI 组装号）；请留空来源或改用 NCBI`。

根因不在后端（那道 400 是对的：gget 按物种名取 Ensembl 官方组装，静默忽略
accession 会下错组装），而在前端：「选用」只回填输入框、不动「下载来源」，
来源停在默认的 gget，矛盾只能等用户点下载时才由后端抛出来。修法是把选择收敛在
用户做决定的那一刻（选用即切 NCBI），并在手工填号点下载时兜底切换。

这条链有三个环，改其中任何一环都会让用户重新看到那句报错：
  A. pickHostAcc：回填 accession 的同时把 #hostDlSource 切到 ncbi，并说明；
  B. dlHostGenome：accession 与 gget 源并存时自动改投 ncbi（不再撞 400）；
  C. 后端守卫不许因此放宽：build.py 里 gget + accession 仍是 400。

跑法：python tests/_check_host_dl_source.py   （退出码 0 = 通过）
"""
import io
import os
import re
import sys

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_JS = os.path.join(ROOT, 'webapp', 'static', 'app.js')
I18N_JS = os.path.join(ROOT, 'webapp', 'static', 'i18n.js')
TPL = os.path.join(ROOT, 'webapp', 'templates', 'refdata.html')
BUILD_PY = os.path.join(ROOT, 'Virus_Platform_Core', 'web', 'build.py')

FAILS = []


def check(ok, msg):
    print(('  ✔ ' if ok else '  ✘ ') + msg)
    if not ok:
        FAILS.append(msg)


def read(path):
    with io.open(path, encoding='utf-8', errors='replace') as f:
        return f.read()


def func_body(src, header):
    """从函数头取到下一个顶层 `function ` / `async function ` 之前。"""
    i = src.find(header)
    if i < 0:
        return ''
    rest = src[i + len(header):]
    nxt = re.search(r'\n(?:async )?function [A-Za-z_]', rest)
    return rest[:nxt.start()] if nxt else rest


def main():
    print('宿主基因组「选用 accession → NCBI 源」检查')
    for p in (APP_JS, I18N_JS, TPL, BUILD_PY):
        if not os.path.isfile(p):
            print('  ✘ 找不到 ' + p)
            return 1
    app = read(APP_JS)
    i18n = read(I18N_JS)
    tpl = read(TPL)
    py = read(BUILD_PY)

    # ---------- A. 选用即切源 ----------
    pick = func_body(app, 'function pickHostAcc(acc) {')
    check(bool(pick), 'pickHostAcc 函数存在')
    check("$('hostDlSource')" in pick,
          "pickHostAcc 读取了 #hostDlSource（不再只回填 accession）")
    check(re.search(r"sel\.value\s*=\s*'ncbi'", pick) is not None,
          "pickHostAcc 把来源切到 ncbi")
    check("$('hostDlAcc').value = acc" in pick,
          "pickHostAcc 仍然回填 accession 输入框")
    check('rd.qryPickedSrc' in pick,
          "pickHostAcc 的提示里带 rd.qryPickedSrc（告诉用户来源变了）")

    # ---------- B. 下载前兜底切换 ----------
    dl = func_body(app, 'async function dlHostGenome(btn) {')
    check(bool(dl), 'dlHostGenome 函数存在')
    check("let source = $('hostDlSource')" in dl,
          "dlHostGenome 的 source 是 let/可改写（const 会让兜底切换写不进去）")
    check(re.search(r"accession\s*&&\s*source\s*!==\s*'ncbi'", dl) is not None,
          "dlHostGenome 有「accession 存在但来源不是 ncbi」判据")
    check(re.search(r"source\s*=\s*'ncbi'", dl) is not None,
          "dlHostGenome 在这种组合下把来源切成 ncbi")
    check('rd.hostDlAccSrc' in dl,
          "dlHostGenome 的兜底提示带 rd.hostDlAccSrc（说明为什么切、怎么改回）")
    # 先查后下闸门必须在切换之后仍然只对「无 accession」生效
    check(re.search(r"if \(source === 'ncbi' && !accession\)", dl) is not None,
          "「无 accession 时先查再下」闸门保持原样（不被兜底切换误触发）")

    # ---------- C. 中英文案成对 ----------
    for key in ('rd.qryPickedSrc', 'rd.hostDlAccSrc'):
        hits = re.findall(r"'%s':\s*'([^']*)'" % re.escape(key), i18n)
        check(len(hits) == 2, '%s 在中英各定义一次（实际 %d 处）' % (key, len(hits)))
        if len(hits) == 2:
            check(hits[0] != hits[1], '%s 中英文案不相同（不是英文照抄中文）' % key)
            check(all(h.strip() for h in hits), '%s 文案非空' % key)

    # ---------- D. 后端守卫不许放宽 ----------
    check(re.search(r"if source == 'gget' and accession:", py) is not None,
          "后端仍拒绝「gget 源 + accession」（前端便捷不等于放宽校验）")
    check('gget/Ensembl 源不支持 accession' in py,
          "后端 400 原文保留（CLI/API 侧的用户仍靠它定位问题）")

    # ---------- E. 模板默认值没被偷偷改成 NCBI ----------
    check(re.search(r'<option value="gget"', tpl) is not None,
          '模板默认来源仍是 gget（切源是选用时的一次性动作，不是改默认值）')
    check(re.search(r'<option value="ncbi"', tpl) is not None,
          '模板仍提供 ncbi 选项')

    print()
    if FAILS:
        print('✘ %d 项不通过' % len(FAILS))
        return 1
    print('✔ 全部通过')
    return 0


if __name__ == '__main__':
    sys.exit(main())
