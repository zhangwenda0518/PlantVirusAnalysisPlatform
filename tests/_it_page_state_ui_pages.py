# -*- coding: utf-8 -*-
"""页面状态快照（PS）跨页抽查：任意页面「页面 JS 写过的面板」切走再回来是否还在。

为什么需要：PS 改成「劫持 innerHTML 自动跟踪」以后，功劳不再是模板里那 8 个
data-ps 标记，而是任何被页面 JS 写过内容的带 id 容器。这条用例不再挑 refdata，
而是在四个不同页面各挑一个容器，模拟页面渲染写内容 → 跳 /tasks → 回来验内容。
同时收集控制台报错，确认钩子没有把页面弄坏。

跑法：python tests/_it_page_state_ui_pages.py
需要 selenium + 本机 Edge；缺了打 SKIP 并返回 0（与本仓库其它浏览器用例同口径）。
"""
import os
import sys
import time

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = 'http://127.0.0.1:8765'

PAGES = [
    ('/logan', 'LOGAN 溯源页'),
    ('/settings', '设置页'),
    ('/submit', 'NCBI 提交页'),
    ('/results', '结果中心'),
    ('/prescreen', '病毒样本预筛页'),
]

MARK = 'PS-CROSS-PAGE-MARK-42'


def check_meta_location(drv):
    """公共检索页不靠快照回填（表是数据驱动的，回来要按真实数据重查），
    所以单测「上次的集合 + 分页签」是否记住：/meta?p=X#sb -> /tasks -> 裸 /meta。"""
    drv.get(BASE + '/meta')
    time.sleep(2.0)
    names = drv.execute_script(
        "return (typeof MT !== 'undefined' && MT.colls || []).map(function(c){return c.name;});")
    if not names:
        print('  - 公共检索页：没有检索集合，跳过')
        return 0
    target = names[-1] if len(names) > 1 else names[0]
    drv.get(BASE + '/meta?p=' + target + '#sb')
    time.sleep(2.0)
    drv.get(BASE + '/tasks')
    time.sleep(0.6)
    drv.get(BASE + '/meta')
    time.sleep(2.0)
    got = drv.execute_script(
        "return {proj: (typeof MT !== 'undefined' && MT.project) || '',"
        " hash: location.hash || '',"
        " sel: (document.getElementById('mtProjectSel') || {}).value || ''};")
    ok = got.get('proj') == target and got.get('hash') == '#sb' and got.get('sel') == target
    if ok:
        print('  ✔ 公共检索页：回到上次的集合 %s 与分页签 #sb' % target)
        return 0
    print('  ✘ 公共检索页：没回到上次位置（现 proj=%s hash=%s sel=%s，期望 %s/#sb）'
          % (got.get('proj'), got.get('hash'), got.get('sel'), target))
    return 1


def check_meta_form(drv):
    """公共检索页的**检索参数**（物种/测序类型/GSA 并发/数据库/详细模式）也要留住。

    2026-11-02 用户第二次报：切走再回来，测序类型和数据库变成了别的值。根因不是
    表单快照没做，而是页面键里带了 `p=`（当前集合）：页面自己用
    history.replaceState 把地址从 /meta 改成 /meta?p=X，下次回来命中的是**另一个
    旧键**，于是读到的是更早那份参数。app-state.js 已把 p 列为 volatile（页内选中
    项不算页面身份），这条用例守「改参数 → 切走 → 回来仍是改后的值」。
    """
    drv.get(BASE + '/meta')
    time.sleep(2.5)
    drv.execute_script("""
      function set(id, v) {
        var e = document.getElementById(id);
        if (!e) return;
        e.value = v;
        e.dispatchEvent(new Event('input',  {bubbles: true}));
        e.dispatchEvent(new Event('change', {bubbles: true}));
      }
      set('mtSpecies', 'PS FORM PROBE');
      set('mtSource', 'All');
      set('mtDb', 'both');
      set('mtWorkers', '7');
      var d = document.getElementById('mtDetailed');
      if (d) { d.checked = false; d.dispatchEvent(new Event('change', {bubbles: true})); }
    """)
    time.sleep(1.0)
    drv.get(BASE + '/tasks')
    time.sleep(0.6)
    drv.get(BASE + '/meta')
    time.sleep(2.5)
    got = drv.execute_script("""
      return {
        species: (document.getElementById('mtSpecies') || {}).value || '',
        source:  (document.getElementById('mtSource') || {}).value || '',
        db:      (document.getElementById('mtDb') || {}).value || '',
        workers: (document.getElementById('mtWorkers') || {}).value || '',
        detailed: !!(document.getElementById('mtDetailed') || {}).checked
      };
    """)
    ok = (got.get('species') == 'PS FORM PROBE' and got.get('source') == 'All'
          and got.get('db') == 'both' and got.get('workers') == '7'
          and got.get('detailed') is False)
    if ok:
        print('  ✔ 公共检索页：检索参数（物种/测序类型/并发/数据库/详细）都还在')
        return 0
    print('  ✘ 公共检索页：检索参数被改了（%s）' % got)
    return 1


def main():
    try:
        from selenium import webdriver
        from selenium.webdriver.edge.options import Options
    except Exception as e:
        print('SKIP: 没有 selenium（%s）' % e)
        return 0

    opt = Options()
    opt.add_argument('--headless=new')
    opt.add_argument('--window-size=1400,900')
    try:
        drv = webdriver.Edge(options=opt)
    except Exception as e:
        print('SKIP: 起不来 Edge（%s）' % e)
        return 0

    fails = 0
    try:
        for path, label, *rest in PAGES:
            fixed = rest[0] if rest else ''
            drv.get(BASE + path)
            time.sleep(3.0)
            ok_ps = drv.execute_script('return !!window.PS;')
            if not ok_ps:
                print('  ✘ %s：PS 未挂载' % label)
                fails += 1
                continue

            cid = fixed or drv.execute_script("""
              var main = document.querySelector('main') || document.body;
              var els = main.querySelectorAll('[id]');
              for (var i = 0; i < els.length; i++) {
                var e = els[i];
                if (!e.id || e.dataset.psOff !== undefined) continue;
                if (e.closest('nav,aside,header,footer')) continue;
                var tag = e.tagName.toLowerCase();
                if (/^(script|style|input|select|textarea|canvas|svg)$/.test(tag)) continue;
                /* 只挑「加载完仍然是空的」容器：这才是用户真会丢的那类
                   （查询结果 / 单次运行输出）；加载即重绘的侧栏、设置行
                   本来就不靠快照，拿它们测只会红。 */
                if (e.innerHTML.trim() !== '') continue;
                return e.id;
              }
              return '';
            """)
            if not cid:
                print('  - %s：没有可测的带 id 容器，跳过' % label)
                continue

            drv.execute_script(
                "document.getElementById(arguments[0]).innerHTML = %r;"
                % ('<div class="tbl">' + MARK + '</div>'), cid)
            time.sleep(1.2)
            cashed = drv.execute_script(
                "return Object.keys(sessionStorage).some(function(k){"
                "  return k.indexOf('vp_ps:') === 0 && k.slice(-5) === ':pane'; });")
            drv.get(BASE + '/tasks')
            time.sleep(0.6)
            drv.get(BASE + path)
            time.sleep(1.6)
            got = drv.execute_script(
                "var e = document.getElementById(arguments[0]);"
                "return e ? e.innerHTML.indexOf(arguments[1]) >= 0 : false;", cid, MARK)
            notice = drv.execute_script(
                "return !!document.querySelector('.ps-restored');")
            if cashed and got:
                print('  ✔ %s：%s 恢复（提示条 %s）' % (label, cid, '有' if notice else '无'))
            else:
                print('  ✘ %s：%s 未恢复（落盘=%s 命中=%s）' % (label, cid, cashed, got))
                fails += 1

        fails += check_meta_location(drv)
        fails += check_meta_form(drv)

        # 清掉本次造的快照，别把测试痕迹留用户浏览器会话里
        drv.execute_script('try{ window.PS && PS.forget(); }catch(e){}')
    finally:
        try:
            drv.quit()
        except Exception:
            pass

    if fails:
        print('FAILED: %d 页' % fails)
        return 1
    print('跨页快照抽查通过 ✔')
    return 0


if __name__ == '__main__':
    sys.exit(main())
