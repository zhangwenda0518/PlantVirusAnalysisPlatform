# -*- coding: utf-8 -*-
"""任务中心页对「非样品类后台任务」的**真实浏览器**验证
（Selenium + 本机 Edge/Chrome，headless）。

阶段一第 ② 项说的是「改为**任务中心**后台任务」——光在后端建出 task 不够，
必须确认任务中心页真的能把它渲染出来并可操作：

  1. 卡片出现，名字/状态正确
  2. 有进度条且宽度在 0~100 之间（不是空条也不是假满）
  3. 阶段/进展文案非空
  4. 展开「日志」能读到 suvtk 的实时输出
  5. 「前往页面」链接指向发起页（/submit）
  6. 点「停止」真的能停（任务收敛为已停止）
  7. 「只看当前样品」筛选的**真实语义**：sampleMatches 是显式白名单
     （`<样品> · ` / `队列·<样品>` / `样品分析 <样品>`），所以按样品筛时
     项目级任务（.sqn / 校验 / 导出 / AI 总结）会被隐藏 —— 这是设计如此，
     本测试把它钉成事实，供阶段二接入下载/扫描时参考。

无浏览器/无 selenium 时自动 SKIP 并返回 0。
用法: python tests/_it_taskcenter_ui.py [--port 8797] [--headed]
"""
import argparse
import os
import shutil
import socket
import sys
import threading
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
_CLEAN = []


def check(label, ok, extra=''):
    print(f'  [{"PASS" if ok else "FAIL"}] {label}' + (f'  {extra}' if extra else ''),
          flush=True)
    if not ok:
        FAIL.append(label)


def skip(label, why):
    print(f'  [SKIP] {label}  {why}', flush=True)


def free_port(preferred):
    for p in (preferred, 0):
        # connect 探测先行（2026-11-05）：wildcard(0.0.0.0) 已被占用的端口
        # 用 bind('127.0.0.1') 探测会误判空闲，起上去后请求被截走（实测教训：
        # 用户常驻的 docs http.server 在 8901+ 段，套件实例全 404）。
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as c:
            if c.connect_ex(('127.0.0.1', p)) == 0:
                continue
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(('127.0.0.1', p))
                return s.getsockname()[1]
        except OSError:
            continue
    raise RuntimeError('无可用端口')


def _run_once():
    global _CLEAN
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=8797)
    ap.add_argument('--headed', action='store_true')
    a = ap.parse_args()

    try:
        from selenium import webdriver
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.edge.options import Options as EdgeOptions
    except ImportError as e:
        skip('selenium', f'未安装（{e}）')
        return 0

    import app as appmod
    from Virus_Platform_Core.config import DIRS
    from Virus_Platform_Core.ncbi_submit import store as sstore
    from Virus_Platform_Core import suvtk_submit as ss
    from Virus_Platform_Core.web.tasks import tm

    PROJ = 'uitest_tc_proj'
    RUN_NAME = 'orf_uitest_tc'
    tool_runs = DIRS.get('tool_runs')
    run_dir = os.path.join(tool_runs, RUN_NAME)
    _CLEAN += [sstore.table_dir(PROJ), run_dir]

    shutil.rmtree(sstore.table_dir(PROJ), ignore_errors=True)
    shutil.rmtree(run_dir, ignore_errors=True)
    os.makedirs(os.path.join(run_dir, '03_assembly'), exist_ok=True)
    with open(os.path.join(run_dir, '03_assembly', 'viral_contigs.fasta'),
              'w', encoding='utf-8') as f:
        f.write('>contig1 taxid=11001 taxon=Test_virus\n' + 'ACGT' * 120 + '\n')
    sstore.create_table(PROJ, sample='demo')
    df = sstore.load_table(PROJ)
    df.loc[df.index[0], 'sequence_name'] = 'contig1'
    sstore.save_table(PROJ, df)
    sstore._write_link(PROJ, 'orf', {'run': RUN_NAME})

    # 慢一点的桩：留出观察进度/日志/停止的时间窗。
    # 必须**响应取消**（真实 build_sqn 每个 suvtk 步骤边界都会查取消事件），
    # 否则「点停止」只能等桩自己跑完才收敛，测不出及时停止。
    _orig = ss.build_sqn
    _orig_ready = ss.suvtk_chain_ready

    def slow_build_sqn(*args, **kw):
        from Virus_Platform_Core.utils import task_check_cancel
        log = kw.get('log') or (lambda m: None)
        for w in ('suvtk features', 'suvtk comments', 'table2asn'):
            log(f'  [stub] {w} running ...')
            for _ in range(40):                 # 2s，每 50ms 查一次取消
                time.sleep(0.05)
                task_check_cancel()
        return {'sqn': 'x.sqn', 'stats': {'error': 0, 'warning': 0, 'info': 0},
                'val_head': []}

    def fast_build_sqn(*args, **kw):
        log = kw.get('log') or (lambda m: None)
        log('  [stub] fast build done')
        return {'sqn': 'x.sqn', 'stats': {'error': 0, 'warning': 0, 'info': 0},
                'val_head': []}

    ss.build_sqn = slow_build_sqn
    # 就绪门一并桩掉（2026-11-03）：suvtk/BFVD 是开发机专属链、分发/测试
    # 环境不装。不桩时端点走平台 feature-table 回退，而测试的假 orf run
    # 目录没有真产物 → 回退失败 → 400，任务根本起不来。桩就绪门=直通
    # build_sqn 桩，与下方 build_sqn 桩同一口径。
    ss.suvtk_chain_ready = lambda: True

    from werkzeug.serving import make_server
    port = free_port(a.port)
    srv = make_server('127.0.0.1', port, appmod.app, threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{port}'
    print(f'临时实例: {base}  （用户的 8989 未被触碰）', flush=True)

    drv = None
    tid = None
    try:
        # 直接用 API 起任务（前端发起路径已由 _it_async_ui 验证过）
        client = appmod.app.test_client()
        r = client.post(f'/api/submit/table/{PROJ}/sqn',
                        json={'async': True, 'author': {}})
        tid = (r.get_json() or {}).get('task')
        check('已起一个 .sqn 后台任务', bool(tid),
              f'status={r.status_code} {str(r.get_json())[:80]}')
        if not tid:
            return 1

        opts = EdgeOptions()
        if not a.headed:
            opts.add_argument('--headless=new')
        opts.add_argument('--no-sandbox')
        opts.add_argument('--disable-gpu')
        opts.add_argument('--window-size=1440,1100')
        drv = None
        _last = None
        # Selenium Manager 偶发拿不到驱动（网络抖动/正在自更新）。原来的写法
        # 会直接 SKIP 并 exit 0 —— 批量测试里表现为 PASS，浏览器覆盖**静默
        # 消失**。这里先重试 3 次，仍失败才 SKIP（无浏览器的机器不阻塞套件）。
        for _att in range(3):
            try:
                drv = webdriver.Edge(options=opts)
                break
            except Exception as e:                       # noqa: BLE001
                _last = e
                time.sleep(1.5)
        if drv is None:
            skip('Edge 驱动', f'{type(_last).__name__}: {str(_last)[:80]}'
                              f'（已重试 3 次）')
            return 0
        drv.set_page_load_timeout(40)
        wait = WebDriverWait(drv, 30)

        print('\n== 任务中心渲染 ==', flush=True)
        drv.get(base + '/tasks')
        wait.until(lambda d: d.find_elements(
            By.CSS_SELECTOR, f'.task-card[data-tid="{tid}"]'))
        card = drv.find_element(By.CSS_SELECTOR,
                               f'.task-card[data-tid="{tid}"]')
        txt = card.text
        check('（1）卡片出现且名字正确',
              '提交 .sqn 生成' in txt, f'{txt.replace(chr(10), " | ")[:120]}')
        check('（2）状态为运行中', '运行中' in txt,
              f'{txt.replace(chr(10), " | ")[:80]}')
        bars = card.find_elements(By.CSS_SELECTOR, '.tk-bar i')
        width = bars[0].get_attribute('style') if bars else ''
        pct = None
        if width and 'width:' in width:
            try:
                pct = float(width.split('width:')[1].split('%')[0].strip())
            except ValueError:
                pct = None
        check('（3）有进度条且 0 < 宽度 < 100（不是空条/假满）',
              pct is not None and 0 < pct < 100, f'style={width!r}')
        check('（4）阶段/进展文案非空（能看到在做什么）',
              any(k in txt for k in ('suvtk', 'features', 'comments',
                                     'table2asn', '准备')),
              f'{txt.replace(chr(10), " | ")[:150]}')

        print('\n== 日志与前往链接 ==', flush=True)
        det = card.find_element(By.CSS_SELECTOR, '.tk-log-wrap')
        drv.execute_script("arguments[0].open = true;", det)
        # ontoggle 只在用户交互时触发；直接 JS 改 open 不触发，手动调一次
        drv.execute_script(
            "if (typeof onLogToggle === 'function') "
            "  onLogToggle(arguments[0], arguments[1], false);",
            det, tid)
        t0 = time.time()
        log_txt = ''
        while time.time() - t0 < 20:
            log_txt = drv.find_element(By.ID, f'log-{tid}').text
            if 'stub' in log_txt or 'suvtk' in log_txt:
                break
            time.sleep(0.5)
        check('（5）展开日志能读到 suvtk 实时输出',
              'suvtk' in log_txt or 'stub' in log_txt,
              f'{log_txt[:120]!r}')
        # 注意：tasks.html 的「前往页面」只在 status==='done' 时渲染
        # （resultLink 有值 + task.status === 'done'），运行中/已停止都不显示。
        # 所以这条只能在**已完成**的卡上验 —— 下面用第二个快速任务来验。

        print('\n== 点「停止」真的能停 ==', flush=True)
        drv.execute_script("window.confirm = function(){return true;};")
        stop = card.find_elements(By.XPATH, ".//button[contains(., '停止')]")
        check('（7）运行中卡片有「停止」按钮', bool(stop), f'{len(stop)} 个')
        if stop:
            drv.execute_script("arguments[0].click();", stop[0])
            t0 = time.time()
            status = ''
            while time.time() - t0 < 25:
                snap = tm.snapshot(tid, log_lines=0)
                status = (snap or {}).get('status', '')
                if status != 'running':
                    break
                time.sleep(0.5)
            check('（7）任务收敛为已停止（不是继续跑到完成）',
                  status == 'cancelled', f'status={status}')
            # 卡片仍在，且显示已停止
            t0 = time.time()
            card_txt = ''
            while time.time() - t0 < 15:
                cards = drv.find_elements(
                    By.CSS_SELECTOR, f'.task-card[data-tid="{tid}"]')
                if cards:
                    card_txt = cards[0].text
                    if '已停止' in card_txt:
                        break
                time.sleep(0.5)
            check('（7）任务中心卡片显示「已停止」',
                  '已停止' in card_txt, f'{card_txt.replace(chr(10), " | ")[:90]}')

        print('\n== 「前往页面」链接（仅在已完成卡上渲染）==', flush=True)
        ss.build_sqn = fast_build_sqn
        r2 = client.post(f'/api/submit/table/{PROJ}/sqn',
                         json={'async': True, 'author': {}})
        tid2 = (r2.get_json() or {}).get('task')
        check('（6）起了第二个（快速）任务用于验完成态', bool(tid2), str(tid2))
        if tid2:
            t0 = time.time()
            while time.time() - t0 < 25:
                snap = tm.snapshot(tid2, log_lines=0)
                if (snap or {}).get('status') != 'running':
                    break
                time.sleep(0.4)
            # 让页面轮询一轮（进行中 2.5s / 空闲 10s，这里等到出现或超时）
            t0 = time.time()
            hrefs = []
            while time.time() - t0 < 20:
                cards = drv.find_elements(
                    By.CSS_SELECTOR, f'.task-card[data-tid="{tid2}"]')
                if cards:
                    links = cards[0].find_elements(By.CSS_SELECTOR,
                                                   '.tk-actions a')
                    hrefs = [x.get_attribute('href') or '' for x in links]
                    if hrefs:
                        break
                drv.execute_script("if (typeof refreshAll==='function') "
                                   "refreshAll(true);")
                time.sleep(1.0)
            check('（6）已完成卡上有「前往页面」且指向 /submit',
                  any('/submit' in h for h in hrefs), str(hrefs))
            try:
                tm.delete(tid2)
            except Exception:
                pass
        ss.build_sqn = slow_build_sqn

        print('\n== 「只看当前样品」筛选的真实语义 ==', flush=True)
        drv.execute_script(
            "window.VP_CTX.setSample('NX-6');"
            "const c=document.getElementById('curOnlyChk');"
            "if(!c) throw new Error('未找到只看当前样品复选框');"
            "c.checked=true; c.dispatchEvent(new Event('change'));")
        time.sleep(1.5)
        present = drv.find_elements(By.CSS_SELECTOR,
                                    f'.task-card[data-tid="{tid}"]')
        check('（8）开启「只看当前样品」后，项目级任务被隐藏（白名单语义）',
              not present,
              'samplesMatches 只认「<样品> · 」/「队列·<样品>」/「样品分析 <样品>」')
        # 阶段二新增的第 4 条白名单：排队镜像卡「排队·<样品>」属于该样品。
        # 它由 web/samples.py 的 bind_queue_entry 挂出，若不认它，用户一勾
        # 「只看当前样品」，刚入队的样品就从任务中心凭空消失。
        # 注意 sampleMatches/render 都被包在闭包里（不是 window 全局），
        # 所以只能走**真实 DOM** 断言，不能直接 execute_script 调它。
        def has_card(t):
            return bool(drv.find_elements(
                By.CSS_SELECTOR, f'.task-card[data-tid="{t}"]'))

        t_q = tm.attach('排队·NX-6', lambda: {'status': 'running', 'pct': 0.0,
                                              'stage': '排队中', 'msg': 'q'},
                        key='test:cur-only-q')
        t_d = tm.attach('下载·probe', lambda: {'status': 'running', 'pct': 0.5,
                                               'msg': 'd'},
                        key='test:cur-only-d')
        try:
            t0 = time.time()
            while time.time() - t0 < 20 and not has_card(t_q):
                drv.execute_script("if (typeof refreshAll==='function')"
                                   " refreshAll(true);")
                time.sleep(0.5)
            check('（8）排队镜像卡「排队·<样品>」属于当前样品，仍可见',
                  has_card(t_q), f'排队={has_card(t_q)}')
            check('（8）下载批次无样品归属，按设计被隐藏', not has_card(t_d),
                  f'下载={has_card(t_d)}')

            # 撤掉排队卡 → 列表应清空 → 空态必须说明是「被过滤」
            tm.detach('test:cur-only-q')
            t0 = time.time()
            empty = ''
            while time.time() - t0 < 20:
                drv.execute_script("if (typeof refreshAll==='function')"
                                   " refreshAll(true);")
                empty = drv.execute_script(
                    "const e=document.querySelector('#taskList .tk-empty');"
                    "return e ? e.textContent : '';")
                if empty:
                    break
                time.sleep(0.5)
            check('（8）空态说明「按当前样品过滤、隐藏了 N 个」而不是「什么都没有」',
                  bool(empty) and ('隐藏' in empty or 'hidden' in empty),
                  repr(empty)[:110])
            _m = drv.execute_script(
                "return t('tk.emptyFiltered','__fallback__');")
            check('（8）空态文案走 i18n 且带条数占位符（不是硬编码兜底）',
                  _m not in ('__fallback__', None) and '{n}' in str(_m),
                  repr(_m)[:110])

            # 关掉筛选 → 被隐藏的下载卡必须**立刻回来**（证明它只是被过滤，
            # 不是没挂上/没渲染 —— 否则上面那条「被隐藏」可能是假阳性）
            drv.execute_script(
                "const c=document.getElementById('curOnlyChk');"
                "c.checked=false; c.dispatchEvent(new Event('change'));")
            t0 = time.time()
            while time.time() - t0 < 20 and not has_card(t_d):
                drv.execute_script("if (typeof refreshAll==='function')"
                                   " refreshAll(true);")
                time.sleep(0.5)
            check('（8）取消筛选后下载卡出现（说明它只是被过滤，不是没挂上）',
                  has_card(t_d), f'下载={has_card(t_d)}')
        finally:
            tm.detach('test:cur-only-q')
            tm.detach('test:cur-only-d')
        time.sleep(1.5)
        present2 = drv.find_elements(By.CSS_SELECTOR,
                                     f'.task-card[data-tid="{tid}"]')
        check('（8）取消筛选后卡片回来', bool(present2))

        print('\n== 控制台无 JS 报错 ==', flush=True)
        try:
            logs = drv.get_log('browser')
            js_err = [l for l in logs if l.get('level') == 'SEVERE'
                      and l.get('source') == 'javascript']
            check('无 SEVERE 级 JS 错误', not js_err,
                  str([l.get('message', '')[:120] for l in js_err])[:200])
        except Exception as e:
            skip('控制台日志', f'{type(e).__name__}')

    finally:
        ss.build_sqn = _orig
        ss.suvtk_chain_ready = _orig_ready
        if tid:
            try:
                tm.cancel(tid)
                tm.delete(tid)
            except Exception:
                pass
        if drv is not None:
            try:
                drv.quit()
            except Exception:
                pass
        try:
            srv.shutdown()
        except Exception:
            pass
        for p in _CLEAN:
            shutil.rmtree(p, ignore_errors=True)

    print('\n' + ('ALL PASS' if not FAIL
                  else f'{len(FAIL)} FAIL: {FAIL}'), flush=True)
    return 0 if not FAIL else 1


def main():
    """跑一次；WebDriver 会话偶发原生崩溃时重试一次（见 _it_download_ui）。"""
    try:
        from selenium.common.exceptions import WebDriverException
    except ImportError:
        return _run_once()
    for attempt in (1, 2):
        try:
            return _run_once()
        except WebDriverException as e:
            if attempt == 2:
                print(f'  [FAIL] WebDriver 会话连续两次中断：{str(e)[:120]}',
                      flush=True)
                return 1
            print(f'  [RETRY] WebDriver 会话中断（{str(e)[:80]}），重试一次…',
                  flush=True)
            FAIL.clear()
    return 1


if __name__ == '__main__':
    sys.exit(main())
