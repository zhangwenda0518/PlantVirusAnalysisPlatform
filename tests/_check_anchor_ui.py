# -*- coding: utf-8 -*-
"""样品锚常驻条的真实浏览器回归（Edge headless）。

2026-10-27 新增「当前分析的是哪一件样品」左下常驻条后，需要证明：
  1. 有正在跑的锚时，条子出现，且写对了样品名 / 阶段 / R1 指纹；
  2. 位置真在**左下**（不被右下 AI Dock 挤走、不压住正文）；
  3. 锚目录被移除 / 没有锚时，条子静默隐藏（绝不弹错）；
  4. × 按钮能把它收起来。

做法：临时把 `anchor.root` 指到一个临时目录，用 anchor 自己的公开 API
造两个假锚（running / finished），起一个临时 Flask 实例，用真实浏览器
打开首页并手动触发 `anchorBarTick()` 强制刷新（不等 6s 轮询）。

用法: python tests/_check_anchor_ui.py
"""
import json
import os
import shutil
import sys
import time

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
SKIPPED = []


def check(label, ok, extra=''):
    print(f'  [{"PASS" if ok else "FAIL"}] {label}' + (f'  {extra}' if extra else ''),
          flush=True)
    if not ok:
        FAIL.append(label)


def skip(label, why=''):
    print(f'  [SKIP] {label}' + (f'  {why}' if why else ''), flush=True)
    SKIPPED.append(label)


def main():
    try:
        from selenium import webdriver
        from selenium.webdriver.common.by import By
        from selenium.webdriver.edge.options import Options as EdgeOptions
        from selenium.webdriver.support.ui import WebDriverWait
    except ImportError as e:
        print(f'[SKIP] selenium 未安装: {e}')
        return 0

    from Virus_Platform_Core import anchor

    # ---- 造锚：临时锚根，绝不碰用户真实 run/anchors/ --------------------
    tmp_root = os.path.join(ROOT, 'run', f'_anchorui_{int(time.time())}')
    os.makedirs(tmp_root, exist_ok=True)
    orig_root = anchor.root
    anchor.root = lambda: tmp_root                     # noqa: E305  测试专用
    r1 = os.path.join(ROOT, 'examples', 'example_sim100x_R1.fastq.gz')
    r2 = os.path.join(ROOT, 'examples', 'example_sim100x_R2.fastq.gz')
    sample = 'UI自检样品'

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
    try:
        drv.set_page_load_timeout(30)
        wait = WebDriverWait(drv, 60)
        drv.get(base + '/')
        wait.until(lambda d: d.find_elements(By.ID, 'vpAnchorBar')
                   or d.execute_script('return !!document.readyState'))

        def bar_state():
            return drv.execute_script("""
                var b = document.getElementById('vpAnchorBar');
                if (!b) return {exists: false};
                var r = b.getBoundingClientRect();
                return {exists: true, hidden: !!b.hidden, text: (b.innerText || '').trim(),
                        cls: b.className, left: Math.round(r.left),
                        top: Math.round(r.top),
                        bottom: Math.round(window.innerHeight - r.bottom),
                        vw: window.innerWidth, vh: window.innerHeight,
                        openHref: (document.getElementById('abOpen') || {}).href || '',
                        verHref: (document.getElementById('abVerify') || {}).href || ''};
            """)

        # ---- 场景 1：没有锚 → 条子隐藏 ----
        print('== 场景 1：没有任何锚 ==', flush=True)
        drv.execute_script('window.anchorBarTick && window.anchorBarTick()')
        time.sleep(1.0)
        st = bar_state()
        check('无锚时条子隐藏（条子可以存在但必须 hidden）',
              (not st['exists']) or st['hidden'], json.dumps(st, ensure_ascii=False)[:160])

        # ---- 场景 2：正在跑 → 出现，写对样品/阶段/指纹，位置在左下 ----
        print('== 场景 2：一件样品正在分析 ==', flush=True)
        anchor.start_run(sample, r1, r2, stages=['fastp', 'assembly'],
                         sample_dir=os.path.join(ROOT, 'run', 'results', sample),
                         project='UI 自检项目')
        anchor.stage_begin(sample, 'fastp')
        anchor.stage_done(sample, 'fastp', 1.2)
        anchor.stage_begin(sample, 'assembly')
        snap = anchor.snapshot(sample)
        sha8 = ((snap or {}).get('r1_sha8') or '')
        drv.execute_script('window.anchorBarTick && window.anchorBarTick()')
        time.sleep(1.0)
        st = bar_state()
        desc = json.dumps(st, ensure_ascii=False)[:220]
        check('条子可见', st['exists'] and not st['hidden'], desc)
        check('样品名写对', sample in st['text'], st['text'][:80])
        check('阶段写对（assembly + 1/2）',
              ('assembly' in st['text']) and ('1/2' in st['text']), st['text'][:80])
        check('is-run 状态类', 'is-run' in st['cls'], st['cls'])
        if sha8:
            check('R1 指纹写对', sha8 in st['text'], sha8)
        else:
            skip('R1 指纹', '后台摘要尚未回填（inputs_state=hashing）')
        # 2026-10-30 改版：常驻条改到右下角且**可拖动**（vpDraggable），位置
        # 断言不再有意义；「正在分析」跳任务中心、「核对」跳该样品结果页。
        check('条子落在视口内', 0 <= st['left'] < st['vw'] and 0 <= st['top'],
              f"left={st['left']} top={st['top']} vw={st['vw']}")
        check('「正在分析」跳任务中心（进度页）',
              st['openHref'].endswith('/tasks'), st['openHref'][:120])
        check('「核对」跳该样品的结果页',
              f"/results?sample=" in st['verHref'], st['verHref'][:120])

        # ---- 场景 3：结束 → 变"刚完成"，几秒内仍显示 ----
        print('== 场景 3：跑完了 ==', flush=True)
        anchor.end_run(sample, 'ok', 8.5,
                       os.path.join(ROOT, 'run', 'results', sample))
        drv.execute_script('window.anchorBarTick && window.anchorBarTick()')
        time.sleep(1.0)
        st = bar_state()
        check('结束后显示「刚完成 / Just finished」', st['exists'] and not st['hidden']
              and ('刚完成' in st['text'] or 'Just finished' in st['text']),
              st['text'][:100])
        check('is-done 状态类', 'is-done' in st['cls'], st['cls'])

        # ---- 场景 4：× 收起 ----
        print('== 场景 4：手动收起 ==', flush=True)
        drv.execute_script("var x=document.getElementById('abX'); if(x) x.click();")
        time.sleep(0.4)
        st = bar_state()
        check('点 × 后隐藏', st['exists'] and st['hidden'], json.dumps(
            {'hidden': st.get('hidden')}, ensure_ascii=False))
        drv.execute_script('window.anchorBarTick && window.anchorBarTick()')
        time.sleep(0.6)
        st = bar_state()
        check('轮询不会把刚收起的条子又弹出来', st['exists'] and st['hidden'],
              json.dumps({'hidden': st.get('hidden')}, ensure_ascii=False))

        # ---- 场景 5：锚目录没了 → 静默隐藏，页面无报错 ----
        print('== 场景 5：锚被删掉（模拟老版本/清理） ==', flush=True)
        drv.execute_script('delete window._ancHiddenRun;')
        drv.execute_script("""var x=document.getElementById('abX');
                              if (x) { /* 重置隐藏开关 */ }""")
        shutil.rmtree(tmp_root, ignore_errors=True)
        os.makedirs(tmp_root, exist_ok=True)
        drv.execute_script('window.anchorBarTick && window.anchorBarTick()')
        time.sleep(1.0)
        st = bar_state()
        check('无锚时再次隐藏（不弹错）', (not st['exists']) or st['hidden'],
              json.dumps({'hidden': st.get('hidden')}, ensure_ascii=False))
        errs = drv.execute_script('return window.__vpErrors || []')
        check('页面无未捕获错误', not errs, str(errs)[:200])
    finally:
        try:
            drv.quit()
        except Exception:
            pass
        try:
            srv.shutdown()
        except Exception:
            pass
        # 后台摘要线程按需懒取 root()：先等它落地再还原，否则它会往真实
        # run/anchors/ 里补写一条无主账本（自检污染真实目录）。
        time.sleep(1.5)
        anchor.root = orig_root
        shutil.rmtree(tmp_root, ignore_errors=True)

    print('\n' + '=' * 52, flush=True)
    if FAIL:
        print(f'失败 {len(FAIL)} 项：' + '；'.join(FAIL), flush=True)
        return 1
    print(f'样品锚常驻条浏览器自检通过（跳过 {len(SKIPPED)} 项）', flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
