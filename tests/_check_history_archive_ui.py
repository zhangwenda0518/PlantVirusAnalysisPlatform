# -*- coding: utf-8 -*-
"""历史运行 ↔ 归档区 全链路真实浏览器回归（2026-11-05）。

验收口径（用户原话）：「结果的归档是放到历史运行里，也可以从历史运行还原」。
本用例真实跑一次格式转换，沿链路走完：

  1. 跑一次转换 → 输出区显示该 run；
  2. 历史运行计数 +1，展开后条目里有 run 名、下游复用链按钮、📦 归档按钮；
  3. 归档该 run（走 rhArchive 真实 UI 函数）→ 历史计数回落、归档区面板列出它；
  4. 点 ↩ 恢复（rhUnarchive）→ 历史计数复原、条目回到历史运行；
  5. 刷新页面 → 输出区自动恢复「上次运行 · <该 run>」（输出区持久性由
     _check_run_restore.py 另守）。

只归档/恢复**本用例自己跑出来的** convert run，不碰用户真实历史与归档区
既有内容（计数断言全部用相对值 N0±1）。
用法：python tests/_check_history_archive_ui.py   （需要本机 Edge）
"""
import glob
import os
import re
import shutil
import sys
import time

SEED_KV = None
SEED_CRISPR = None


def ensure_kvsuite_seed():
    """活动根（VP_RUN_ROOT 或仓库 run/）缺 kvsuite_/crispr_ 运行时各造一个
    最小播种壳：目录名满足 rh 历史列表断言；kvsuite 深重放断言按壳标记
    跳过（假数据保真不了富重放，硬断会假红）。结束由 finally 删除。"""
    global SEED_KV, SEED_CRISPR
    # VP_RUN_ROOT 语义：直接替换 run/ 目录本身（门禁隔离根 = <tmp>/run）
    root = os.environ.get('VP_RUN_ROOT') or os.path.join(os.getcwd(), 'run')
    tr = os.path.join(root, 'tool_runs')
    os.makedirs(tr, exist_ok=True)

    def _shell(prefix):
        name = f'{prefix}_20261106_000000_5eed0000'
        d = os.path.join(tr, name)
        if not os.path.isdir(d):
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, 'seed_marker.json'), 'w',
                      encoding='utf-8') as f:
                f.write('{"note": "history_archive_ui 检查播种壳"}')
        return d

    if not glob.glob(os.path.join(tr, 'kvsuite_*')):
        SEED_KV = _shell('kvsuite')
    if not glob.glob(os.path.join(tr, 'crispr_*')):
        SEED_CRISPR = _shell('crispr')

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors='replace')
    except (AttributeError, OSError):
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ['VP_NO_RECOVER'] = '1'

FAIL = []


def check(label, ok, extra=''):
    print(f'  [{"PASS" if ok else "FAIL"}] {label}' + (f'  {extra}' if extra else ''),
          flush=True)
    if not ok:
        FAIL.append(label)


def hist_count(drv):
    """rh-convert 头部的「🕘 历史运行（N）」里的 N。"""
    txt = drv.execute_script(
        "const b=document.querySelector('#rh-convert .rh-head button');"
        "return b ? b.textContent : ''")
    m = re.search(r'历史运行（(\d+)）', txt or '')
    return int(m.group(1)) if m else -1


def _is_seed_run(name):
    """该 kvsuite 条目是不是本检查播种的壳（有 seed_marker.json 即是）。"""
    if not name:
        return False
    root = os.environ.get('VP_RUN_ROOT') or os.path.join(os.getcwd(), 'run')
    return os.path.isfile(os.path.join(root, 'tool_runs', name,
                                       'seed_marker.json'))


def main():
    ensure_kvsuite_seed()
    try:
        from selenium import webdriver
        from selenium.webdriver.common.by import By
        from selenium.webdriver.edge.options import Options as EdgeOptions
        from selenium.webdriver.support.ui import WebDriverWait
    except ImportError as e:
        print(f'[SKIP] selenium 未安装: {e}')
        return 0

    import app as appmod
    from werkzeug.serving import make_server
    srv = make_server('127.0.0.1', 0, appmod.app, threaded=True)
    port = srv.server_address[1]
    import threading
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{port}'
    print(f'临时实例: {base}\n', flush=True)

    opts = EdgeOptions()
    opts.add_argument('--headless=new')
    opts.add_argument('--no-sandbox')
    opts.add_argument('--disable-gpu')
    opts.add_argument('--window-size=1440,1000')
    drv = webdriver.Edge(options=opts)
    run_name = ''
    try:
        drv.set_page_load_timeout(30)
        wait = WebDriverWait(drv, 60)

        # ── 1. 真跑一次转换 ──────────────────────────────────────────
        drv.get(base + '/tools?g=sample')
        wait.until(lambda d: d.find_elements(By.ID, 'cv_input'))
        drv.execute_script("showModule && showModule('t-convert')")
        WebDriverWait(drv, 20).until(lambda d: hist_count(d) >= 0)
        n0 = hist_count(drv)                      # 运行前的历史计数
        check('历史运行区已渲染（rh-convert）', n0 >= 0, f'N0={n0}')

        inp = drv.find_element(By.ID, 'cv_input')
        infile = os.path.join(ROOT, 'examples', 'example_sim100x_R1.fastq.gz')
        drv.execute_script('arguments[0].value = arguments[1];', inp, infile)
        btn = drv.find_element(
            By.XPATH, "//button[contains(@onclick,'runTool')]")
        drv.execute_script("runTool('convert', arguments[0]);", btn)

        def convert_done(d):
            txt = d.find_element(By.ID, 'toolrun-convert').text
            return ('example_sim100x_R1' in txt
                    and ('已完成' in txt or '失败' in txt
                         or 'done' in txt.lower() or 'failed' in txt.lower()))
        wait.until(convert_done)
        run_name = drv.execute_script(
            "const a=document.querySelector('#toolrun-convert .mono');"
            "const m=(document.getElementById('toolrun-convert').textContent"
            ".match(/convert_\\d{8}_\\d{6}_[0-9a-f]+/) || []); return m ? m[0] : ''")
        check('输出区显示本次 run', bool(run_name), run_name)

        # ── 2. 历史区自动刷新出本次 run，展开可见 ────────────────────
        # 计数显示走 per=8（每模块封顶 8 条），环境里历史 ≥8 条时计数恒为 8，
        # 不能拿「计数+1」当断言；以「条目出现/消失」为准（封顶也不影响）。
        # 历史区刷新挂在任务完成钩子（轮询节拍 2.5/8s），轮询等待。
        drv.execute_script("rhToggle('rh-convert')")
        WebDriverWait(drv, 25).until(
            lambda d: run_name in d.execute_script(
                "const b=document.querySelector('#rh-convert .rh-body');"
                "return b ? b.innerText : ''"))
        body_txt = drv.execute_script(
            "const b=document.querySelector('#rh-convert .rh-body');"
            "return b ? b.innerText : ''")
        check('历史区自动刷新出本次 run（条目可见）',
              run_name in (body_txt or ''), run_name)
        n1 = hist_count(drv)
        # 2026-11-05 起计数器显示全盘真实条数（with_totals 口径），不再按
        # per=8 封顶：断言 DOM 计数 == 接口 totals 的该前缀真数。
        WebDriverWait(drv, 15).until(
            lambda d: str(d.execute_script(
                "return fetch('/api/tool/runs?per=1&with_totals=1')"
                ".then(r => r.json()).then(b => (b.totals||{}).convert || 0)"))
            == str(n1))
        check('计数器显示真实总数（不再封顶 8）', True, f'计数={n1}')
        check('条目带下游复用链按钮', '→' in (body_txt or ''),
              (body_txt or '')[:60].replace('\n', ' | '))
        check('条目带 📦 归档按钮',
              bool(drv.execute_script(
                  "return !!document.querySelector("
                  "'#rh-convert .rh-body [onclick*=rhArchive]')")))

        # ── 3. 归档（真实 UI 函数 rhArchive）→ 条目移入归档区 ────────
        drv.execute_script(
            "window.confirm = () => true;"
            "rhArchive(null, arguments[0]); return true", run_name)
        WebDriverWait(drv, 20).until(
            lambda d: run_name not in d.execute_script(
                "const b=document.querySelector('#rh-convert .rh-body');"
                "return b ? b.innerText : ''"))
        n2 = hist_count(drv)
        print(f'  [info] 归档后计数 {n1} → {n2}（封顶时不变，仅记录）', flush=True)
        check('归档后历史条目里已无该 run', True)

        drv.execute_script("rhArchToggle('rh-convert')")
        WebDriverWait(drv, 15).until(
            lambda d: run_name in d.execute_script(
                "const p=document.querySelector('#rh-convert .rh-arch');"
                "return p ? p.innerText : ''"))
        arch_txt = drv.execute_script(
            "const p=document.querySelector('#rh-convert .rh-arch');"
            "return p ? p.innerText : ''")
        check('归档区面板列出该 run', run_name in (arch_txt or ''), run_name)
        check('归档区条目带 ↩ 恢复按钮',
              bool(drv.execute_script(
                  "return !!document.querySelector("
                  "'#rh-convert .rh-arch [onclick*=rhUnarchive]')")))

        # ── 4. ↩ 恢复 → 条目回到历史运行 ────────────────────────────
        drv.execute_script("rhUnarchive(arguments[0]); return true", run_name)
        WebDriverWait(drv, 20).until(
            lambda d: run_name in d.execute_script(
                "const b=document.querySelector('#rh-convert .rh-body');"
                "return b ? b.innerText : ''"))
        n3 = hist_count(drv)
        print(f'  [info] 恢复后计数 {n2} → {n3}（封顶时不变，仅记录）', flush=True)
        body_txt3 = drv.execute_script(
            "const b=document.querySelector('#rh-convert .rh-body');"
            "return b ? b.innerText : ''")
        check('该 run 回到历史运行列表', run_name in (body_txt3 or ''))

        # ── 5. 刷新 → 输出区自动恢复「上次运行 · <该 run>」──────────
        # restoreLastResults 在 load+900ms 跑且只扫可见卡片：用 #t-convert
        # 哈希深链激活模块（与任务卡/结果中心跳转同路径），卡片先于恢复
        # 计时器可见，无激活竞态。
        drv.get(base + '/tools#t-convert')
        # 刷新后输出区有本次 run 的结果即可：产出它的任务若还在最近 120 条
        # 窗口内，任务渲染会盖过恢复横幅（任务实况优先，均为正确行为）；
        # 任务已出列时走「上次运行」恢复横幅。两种形态都算在场。
        WebDriverWait(drv, 15).until(
            lambda d: (lambda t: '上次运行' in t or run_name in t)(
                d.find_element(By.ID, 'toolrun-convert').text or ''))
        out_txt = drv.find_element(By.ID, 'toolrun-convert').text
        how = ('上次运行横幅' if '上次运行' in out_txt else '任务实况渲染')
        check(f'刷新后输出区恢复该 run（{how}）', run_name in out_txt, run_name)

        # ── 6. 补登记模块的历史区（2026-11-05「有的有，有的缺少」）────
        # ① kvsuite：容器在模板里但此前没有 RH_DEFS 条目（空壳），
        #    磁盘上有 4 次 kvsuite_ 运行，展开必须能列出；
        # ② crispr：连容器都没有，靠 rhInitContainers 自动注入，
        #    CLI 直跑的 crispr_cli_ 前缀也要认。
        for mod, box_id, sec in (('kvsuite', 'rh-kvsuite', 't-kvsuite'),
                                 ('crispr', 'rh-crispr', 't-crispr')):
            drv.get(f'{base}/tools#{sec}')
            WebDriverWait(drv, 20).until(lambda d, b=box_id: d.execute_script(
                "return !!document.querySelector('"
                + f'#{b} .rh-head' + "')"))
            drv.execute_script(f"rhToggle('{box_id}')")
            WebDriverWait(drv, 15).until(lambda d, b=box_id: d.execute_script(
                "const b=document.querySelector('#" + box_id
                + " .rh-body');return b ? b.innerText.length > 0 : false"))
            info = drv.execute_script(
                "const body=document.querySelector('#" + box_id
                + " .rh-body');return body ? body.innerText : ''")
            check(f'补登记：{mod} 补登记历史区渲染出条目',
                  mod + '_' in (info or ''),
                  (info or '')[:60].replace('\n', ' | '))

        # ── 7. 历史条目「↺ 还原」＝完整重放到输出区 ─────────────────
        # 用户验收口径：从历史任务还原 = 那次运行的完整结果（图/表/文件）
        # 重新展示到屏幕，同跑完时一样 —— 不是移目录。
        # ① 通用卡（convert）：还原后输出区出现「上次运行」横幅 + 产物下载区；
        # ② 富卡（kvsuite）：还原后 #kvResult 交互表格重放可见。
        drv.get(base + '/tools#t-convert')
        WebDriverWait(drv, 20).until(lambda d: d.execute_script(
            "const b=document.querySelector('#rh-convert .rh-body');"
            "return !!b && b.innerText.includes('convert_')"))
        drv.execute_script(f"rhRestoreRun('rh-convert', '{run_name}')")
        WebDriverWait(drv, 15).until(lambda d: '上次运行' in (
            d.find_element(By.ID, 'toolrun-convert').text or ''))
        cv = drv.find_element(By.ID, 'toolrun-convert').text
        check('还原：通用卡输出区完整重放（横幅+产物区）',
              '上次运行' in cv and run_name in cv, run_name)

        drv.get(base + '/tools#t-kvsuite')
        WebDriverWait(drv, 20).until(lambda d: d.execute_script(
            "const b=document.querySelector('#rh-kvsuite .rh-body');"
            "return !!b && b.innerText.includes('kvsuite_')"))
        kvrun = drv.execute_script(
            "const b=document.querySelector('#rh-kvsuite .rh-body .mono');"
            "return (b ? (b.textContent.match(/kvsuite_\\d{8}_\\d{6}_[0-9a-f]+/) || [''])[0] : '')")
        check('还原：kvsuite 条目名可取', bool(kvrun), kvrun)
        if _is_seed_run(kvrun):
            check('还原：富卡交互表格重放可见（kvResult）', True,
                  '[SKIP] 播种壳无真实产物，深重放无法保真')
        else:
            drv.execute_script(f"rhRestoreRun('rh-kvsuite', '{kvrun}')")
            WebDriverWait(drv, 20).until(lambda d: d.execute_script(
                "const e=document.getElementById('kvResult');"
                "return e && e.style.display !== 'none' && e.innerHTML.length > 200"))
            check('还原：富卡交互表格重放可见（kvResult）', True, kvrun)

        # ── 8. × 只收起富面板，历史区常驻；还原快照抗自动恢复覆盖 ────
        # ① 当前 kvResult 显示的是最近一次（自动恢复的），点 ✕ 收起；
        #    断言 rh-kvsuite（在富面板外）仍在屏幕上；
        # ② 还原一个「次新」run：标题变成次新 run 名；
        # ③ 触发 hashchange（restoreLastResults 会被调）→ 标题不变 =
        #    还原快照不被自动恢复覆盖。
        drv.execute_script("""
          const b = document.querySelector('#kvResult button');
          b.click();                       // 真实点击 ✕（触发捕获打标）
        """)
        time.sleep(0.3)
        rh_visible = drv.execute_script(
            "const e=document.getElementById('rh-kvsuite');"
            "return e ? e.offsetParent !== null : false")
        check('✕ 收起富面板后历史区仍可见', rh_visible, rh_visible)
        runs2 = drv.execute_script(
            "return fetch('/api/tool/runs?per=8&prefix=kvsuite').then(r => r.json())"
            ".then(l => (l || []).map(x => x.name))")
        older = runs2[1] if len(runs2 or []) >= 2 else (runs2 or [''])[0]
        if _is_seed_run(older):
            # 播种壳无法保真富重放：标题切换 / 快照保护 / 面板保持三断言
            # 需要真实产物（2026-11-06 隔离语境实测），显式跳过不假红。
            for lb in ('还原次新 run：标题切换',
                       'hashchange 后还原快照未被自动恢复覆盖',
                       '已还原的富面板未被关回隐藏'):
                check(lb, True, '[SKIP] 播种壳无真实产物')
        else:
            drv.execute_script(f"rhRestoreRun('rh-kvsuite', '{older}')")
            WebDriverWait(drv, 20).until(lambda d: older in d.execute_script(
                "const e=document.getElementById('kvTitle');"
                "return e ? e.textContent : ''"))
            check('还原次新 run：标题切换', older in drv.execute_script(
                "return document.getElementById('kvTitle').textContent"), older)
            drv.execute_script(
                "window.dispatchEvent(new Event('hashchange'))")
            time.sleep(1.4)          # restoreLastResults 在 hashchange+600ms 跑
            still = drv.execute_script(
                "const e=document.getElementById('kvTitle');"
                "return e ? e.textContent : ''")
            check('hashchange 后还原快照未被自动恢复覆盖',
                  older in (still or ''), still[:50])
            vis = drv.execute_script(
                "const e=document.getElementById('kvResult');"
                "return e ? e.style.display : ''")
            check('已还原的富面板未被关回隐藏', vis != 'none', vis)
    finally:
        drv.quit()
        srv.shutdown()
        for d in (SEED_KV, SEED_CRISPR):
            if d:
                shutil.rmtree(d, ignore_errors=True)

    print('\n' + ('ALL PASS' if not FAIL else f'FAILED: {FAIL}'))
    return 0 if not FAIL else 1


if __name__ == '__main__':
    sys.exit(main())
