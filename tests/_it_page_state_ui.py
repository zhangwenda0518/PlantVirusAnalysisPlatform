# -*- coding: utf-8 -*-
"""页面状态快照（app-state.js）真实浏览器回归（Edge headless，打 8765）。

为什么需要这个测试：
    一级导航是整页刷新，页面级状态只活在内存里；用户 2026-11-02 抱怨
    「切换页面再回来就都看不到了」。app-state.js 用 sessionStorage 做快照，
    但这类「看着像修好、实际没生效」的问题最容易复发（面板没标 data-ps、
    页面键把 ?t= 也算进去、快照层被改成会发请求……）。
    静态契约在 tests/_check_page_state.py；本测试守真实行为：
    填表 + 渲染结果 -> 换页 -> 回来：表单值、结果表、提示条都要在；
    点「丢弃本页快照」后必须真的不再恢复。

跑法：python tests/_it_page_state_ui.py
      需要平台在 127.0.0.1:8765 运行、本机有 Edge 与 selenium；缺浏览器则 SKIP。
"""
import json
import sys
import time

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

BASE = 'http://127.0.0.1:8765'
FAILS = []


def check(ok, msg):
    print(('  OK   ' if ok else '  FAIL ') + msg)
    if not ok:
        FAILS.append(msg)


def main():
    try:
        from selenium import webdriver
        from selenium.webdriver.edge.options import Options
    except Exception as e:                       # noqa: BLE001
        print('SKIP: 没有 selenium（%s）' % e)
        return 0
    opt = Options()
    opt.add_argument('--headless=new')
    opt.add_argument('--disable-gpu')
    opt.add_argument('--window-size=1400,900')
    try:
        d = webdriver.Edge(options=opt)
    except Exception as e:                       # noqa: BLE001
        print('SKIP: 没有可用的 Edge（%s）' % e)
        return 0

    def js(code):
        return d.execute_script("return (function(){" + code + "})();")

    try:
        d.get(BASE + '/refdata?t=' + str(int(time.time())))
        time.sleep(1.5)
        st = js("return {ps: !!window.PS};")
        check(st['ps'], 'PS 已挂载')

        js("var t=document.getElementById('dsGenTaxon'); t.value='Oryza sativa';"
           "t.dispatchEvent(new Event('input',{bubbles:true}));"
           "var a=document.getElementById('dsGenAcc'); a.value='GCA_001648735.1';"
           "a.dispatchEvent(new Event('input',{bubbles:true}));"
           "document.getElementById('dsGenQryResult').innerHTML="
           "'<div class=\"ps-note\">✅ 共 200 条匹配</div><table><tr><td>GCA_001648735.1</td></tr></table>';"
           "return 1;")
        time.sleep(1.2)   # 等表单去抖 + 面板去抖

        d.get(BASE + '/tasks'); time.sleep(1.0)
        d.get(BASE + '/refdata?t=' + str(int(time.time()))); time.sleep(2.0)

        st = js("return {taxon: document.getElementById('dsGenTaxon').value,"
                "acc: document.getElementById('dsGenAcc').value,"
                "pane: document.getElementById('dsGenQryResult').innerText||'',"
                "notice: !!document.querySelector('.ps-restored')};")
        check(st['taxon'] == 'Oryza sativa', '表单 taxon 恢复: ' + st['taxon'])
        check(st['acc'] == 'GCA_001648735.1', '表单 accession 恢复: ' + st['acc'])
        check('200 条匹配' in st['pane'], '查询结果面板恢复')
        check(st['notice'], '有「已恢复」提示条')

        # 丢弃快照
        js("PS.forget(); return 1;")
        time.sleep(0.3)
        d.get(BASE + '/refdata?t=' + str(int(time.time()))); time.sleep(1.8)
        st = js("return {taxon: document.getElementById('dsGenTaxon').value,"
                "pane: (document.getElementById('dsGenQryResult').innerText||'').trim(),"
                "notice: !!document.querySelector('.ps-restored')};")
        check(st['taxon'] == '' and st['pane'] == '' and not st['notice'],
              '丢弃后不再恢复: ' + json.dumps(st, ensure_ascii=False))
    finally:
        d.quit()

    if FAILS:
        print('FAILED: %d' % len(FAILS))
        return 1
    print('页面状态快照 6 项通过')
    return 0


if __name__ == '__main__':
    sys.exit(main())
