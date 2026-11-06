# -*- coding: utf-8 -*-
"""阶段二：**下载批次出现在任务中心**的真实浏览器验证。

阶段一里下载批次完全不在任务中心（DownloadManager 自建线程、自管状态，
从不调用 tm.start）。阶段二给它挂了「外部任务」：执行仍归 DownloadManager，
任务中心只做状态镜像与「停止」转发。本测试在真浏览器里验证这条桥：

  1. 下载批次卡片出现在 /tasks，名字/状态/进度条/进展文案正确
  2. 进展文案含「已下载 / 速度 / 剩余 / 文件数」（就是阶段一修出来的那些量）
  3. 卡片不占并发名额、不属于样品类——默认筛选下必须可见
  4. 点「停止」→ 转发到 DownloadManager.cancel → 批次真的变 cancelled
  5. 已完成批次的卡片带「前往页面」链接，指向下载页
  6. 展开日志能读到 batch.log 的内容
  7. 批次被删除（真身消失）后卡片自动消失；provider 报错时卡片不凭空消失

无浏览器/无 selenium 时自动 SKIP 并返回 0。
用法: python tests/_it_taskcenter_dl_ui.py [--port 8795] [--headed]
"""
import argparse
import os
import re
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


def fe(acc, out, status, done, total, progress, speed=0, eta=0):
    return {'acc': acc, 'url': '', 'out': out, 'size': total,
            'expect_bytes': total, 'done_bytes': done, 'progress': progress,
            'status': status, 'md5': '', 'verified': False, 'error': '',
            'db': 'ENA', 'organism': '', 'speed': speed, 'eta': eta}


def _run_once():
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=8795)
    ap.add_argument('--headed', action='store_true')
    a = ap.parse_args()

    try:
        from selenium import webdriver
        from selenium.common.exceptions import StaleElementReferenceException
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.edge.options import Options as EdgeOptions
    except ImportError as e:
        skip('selenium', f'未安装（{e}）')
        return 0

    from Virus_Platform_Core.config import DIRS
    from Virus_Platform_Core import public_data

    DLROOT = os.path.join(ROOT, 'run', '_uitest_tc_dl')
    shutil.rmtree(DLROOT, ignore_errors=True)
    os.makedirs(DLROOT, exist_ok=True)
    _prev = DIRS.get('downloads')
    DIRS['downloads'] = DLROOT
    public_data._manager = None

    import app as appmod                    # 触发 bind_downloads()
    from Virus_Platform_Core.web.tasks import tm
    from Virus_Platform_Core.web.download import bind_downloads
    mgr = public_data.get_manager()
    # app 导入时已对**当时那个** manager 调过 add_listener；本测试在 finally
    # 里把 _manager 置空，重试时会拿到新实例 → 必须重新绑定一次
    # （add_listener 幂等：重复注册同一函数会去重，并对已有批次补挂）。
    bind_downloads()

    def mk(bid, status, files, started, finished=None):
        d = os.path.join(DLROOT, bid)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, 'batch.log'), 'w', encoding='utf-8') as f:
            f.write(f'[{bid}] 创建批次\n[{bid}] 开始下载 SRR1\n')
        return {'id': bid, 'name': bid, 'status': status, 'created': '',
                'dir': d, 'runs': {}, 'error': '', 'converting': False,
                'convert_msg': '', 'started': started, 'finished': finished,
                'files': files}

    now = time.time()
    mgr.batches['dlrun'] = mk('dlrun', 'downloading', [
        fe('ERR1', os.path.join(DLROOT, 'dlrun', 'a_1.fastq.gz'),
           'downloading', 4_000_000, 8_772_679, 0.456, 2_200_000, 3)], now - 12)
    mgr.batches['dldone'] = mk('dldone', 'completed', [
        fe('ERR2', os.path.join(DLROOT, 'dldone', 'b.fastq.gz'),
           'done', 8_772_679, 8_772_679, 1.0)], now - 60, now - 5)
    # 让监听器补挂这两张卡（等价于 create() 里的 _notify）
    for _b in ('dlrun', 'dldone'):
        mgr._notify(_b)

    from werkzeug.serving import make_server
    port = free_port(a.port)
    srv = make_server('127.0.0.1', port, appmod.app, threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{port}'
    print(f'临时实例: {base}  （用户的平台未被触碰）', flush=True)

    drv = None
    try:
        opts = EdgeOptions()
        if not a.headed:
            opts.add_argument('--headless=new')
        opts.add_argument('--no-sandbox')
        opts.add_argument('--disable-gpu')
        opts.add_argument('--window-size=1440,1100')
        _last = None
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

        def card_by_tid(tid):
            """任务中心卡片按 data-tid 取（注意：不是下载页的 id="b-…"）。"""
            els = drv.find_elements(
                By.CSS_SELECTOR, f'.task-card[data-tid="{tid}"]')
            return els[0] if els else None

        def _txt(el):
            """读 .text —— 页面自身轮询会重渲染卡片，容忍 stale reference。"""
            if el is None:
                return ''
            try:
                return el.text
            except StaleElementReferenceException:
                return ''

        def card_info(tid):
            """(txt, pct, style) —— 每次都重新取元素，避免读到半旧节点。"""
            els = drv.find_elements(
                By.CSS_SELECTOR, f'.task-card[data-tid="{tid}"]')
            if not els:
                return None, None, ''
            try:
                txt = els[0].text
                bars = els[0].find_elements(By.CSS_SELECTOR, '.tk-bar i')
                style = (bars[0].get_attribute('style') or '') if bars else ''
            except StaleElementReferenceException:
                return '', None, ''
            pct = None
            if 'width:' in style:
                try:
                    pct = float(style.split('width:')[1].split('%')[0])
                except ValueError:
                    pct = None
            return txt, pct, style

        def card_text(tid):
            return _txt(card_by_tid(tid))

        def tid_of(bid):
            for x in tm.list_all(logs=False):
                if x and x['name'] == f'下载·{bid}':
                    return x['id']
            return None

        print('\n== 1) 下载批次出现在任务中心 ==', flush=True)
        drv.get(base + '/tasks')
        tid_run = tid_of('dlrun')
        check('（1）运行中的下载批次已挂进任务中心', bool(tid_run), str(tid_run))
        if not tid_run:
            print('\n1 FAIL: （1）运行中的下载批次已挂进任务中心', flush=True)
            return 1
        wait.until(lambda d: d.find_elements(
            By.CSS_SELECTOR, f'.task-card[data-tid="{tid_run}"]'))
        txt, pct, style = '', None, ''
        t0 = time.time()
        while time.time() - t0 < 20:
            txt, pct, style = card_info(tid_run)
            if txt and pct is not None:
                break
            time.sleep(0.5)
        check('（1）卡片出现且名字为「下载·<批次>」',
              '下载·dlrun' in txt, txt.replace('\n', ' | ')[:110])
        check('（1）状态为运行中', '运行中' in txt,
              txt.replace('\n', ' | ')[:80])
        check('（1）进度条 0 < 宽度 < 100（映射的是批次 overall）',
              pct is not None and 0 < pct < 100, f'style={style!r}')
        check('（1）进展文案含「已下载 / 速度 / 剩余」',
              '已下载' in txt and '速度' in txt and '剩余' in txt,
              txt.replace('\n', ' | ')[:140])
        check('（1）文案里带文件数', '/1 文件' in txt or '文件' in txt,
              txt.replace('\n', ' | ')[:140])

        print('\n== 2) 日志可展开 ==', flush=True)
        det = None
        for _ in range(20):
            det = card_by_tid(tid_run)
            if det is not None:
                try:
                    det = det.find_element(By.CSS_SELECTOR, '.tk-log-wrap')
                    break
                except StaleElementReferenceException:
                    det = None
            time.sleep(0.5)
        if det is None:
            check('（2）日志可展开', False, '找不到 .tk-log-wrap')
        else:
            drv.execute_script("arguments[0].open = true;", det)
            drv.execute_script(
                "if (typeof onLogToggle === 'function') "
                "  onLogToggle(arguments[0], arguments[1], false);", det, tid_run)
            t0 = time.time()
            log_txt = ''
            while time.time() - t0 < 15:
                boxes = drv.find_elements(By.ID, f'log-{tid_run}')
                log_txt = _txt(boxes[0]) if boxes else ''
                if 'dlrun' in log_txt:
                    break
                time.sleep(0.5)
            check('（2）展开日志读到 batch.log 内容', 'dlrun' in log_txt,
                  f'{log_txt[:100]!r}')

        print('\n== 3) 点「停止」转发到 DownloadManager ==', flush=True)
        drv.execute_script("window.confirm = function(){return true;};")
        stop = []
        t0 = time.time()
        while time.time() - t0 < 10:
            c = card_by_tid(tid_run)
            if c is not None:
                try:
                    stop = c.find_elements(
                        By.XPATH, ".//button[contains(., '停止')]")
                except StaleElementReferenceException:
                    stop = []
            if stop:
                break
            time.sleep(0.5)
        check('（3）运行中卡片有「停止」按钮', bool(stop), f'{len(stop)} 个')
        if stop:
            drv.execute_script("arguments[0].click();", stop[0])
            t0 = time.time()
            bstat = ''
            while time.time() - t0 < 20:
                bstat = (mgr.snapshot('dlrun') or {}).get('status') or ''
                if bstat != 'downloading':
                    break
                time.sleep(0.4)
            check('（3）批次真的被置为 cancelled（不是只改卡片）',
                  bstat == 'cancelled', f'status={bstat}')
            t0 = time.time()
            card_txt = ''
            while time.time() - t0 < 20:
                card_txt = card_text(tid_run)
                if '已停止' in card_txt:
                    break
                drv.execute_script("if (typeof refreshAll==='function')"
                                   " refreshAll(true);")
                time.sleep(1.0)
            check('（3）任务中心卡片显示「已停止」', '已停止' in card_txt,
                  card_txt.replace('\n', ' | ')[:90])

        print('\n== 4) 已完成批次带「前往页面」链接 ==', flush=True)
        tid_done = tid_of('dldone')
        check('（4）已完成批次也挂了卡', bool(tid_done), str(tid_done))
        if tid_done:
            t0 = time.time()
            hrefs = []
            while time.time() - t0 < 20:
                cards = drv.find_elements(
                    By.CSS_SELECTOR, f'.task-card[data-tid="{tid_done}"]')
                if cards:
                    try:
                        hrefs = [x.get_attribute('href') or ''
                                 for x in cards[0].find_elements(
                                     By.CSS_SELECTOR, '.tk-actions a')]
                    except StaleElementReferenceException:
                        hrefs = []
                    if hrefs:
                        break
                drv.execute_script("if (typeof refreshAll==='function')"
                                   " refreshAll(true);")
                time.sleep(1.0)
            check('（4）「前往页面」指向下载页',
                  any('/download' in h for h in hrefs), str(hrefs))

        print('\n== 5) 真身消失/报错时的卡片行为 ==', flush=True)
        mgr.batches.pop('dldone', None)      # 模拟批次被删除
        drv.execute_script("if (typeof refreshAll==='function') refreshAll(true);")
        t0 = time.time()
        gone = False
        while time.time() - t0 < 20:
            if not drv.find_elements(
                    By.CSS_SELECTOR, f'.task-card[data-tid="{tid_done}"]'):
                gone = True
                break
            drv.execute_script("if (typeof refreshAll==='function')"
                               " refreshAll(true);")
            time.sleep(1.0)
        check('（5）批次删除后卡片自动消失', gone)
        # provider 报错 → 卡片降级保留，不该凭空消失
        _orig = mgr.snapshot
        mgr.snapshot = lambda *x, **k: (_ for _ in ()).throw(
            RuntimeError('boom'))
        try:
            drv.execute_script("if (typeof refreshAll==='function')"
                               " refreshAll(true);")
            t0 = time.time()
            txt2 = ''
            while time.time() - t0 < 20:
                txt2 = card_text(tid_run)
                if '状态读取失败' in txt2:
                    break
                drv.execute_script("if (typeof refreshAll==='function')"
                                   " refreshAll(true);")
                time.sleep(1.0)
            check('（5）provider 报错时卡片降级保留（不凭空消失）',
                  '状态读取失败' in txt2,
                  txt2.replace('\n', ' | ')[:110] if txt2 else '卡片不存在')
            # 降级时 provider 没给 started，必须回落到记录里的挂载时间；
            # 丢了的话前端会把 started 当 0 → 「耗时 496971 h」这种荒唐值。
            _hm = re.search(r'耗时\s+(\d+)\s*h', txt2 or '')
            check('（5）降级卡片的「耗时」不荒唐（started 未被当成 0）',
                  _hm is None or int(_hm.group(1)) < 100,
                  (txt2 or '').replace('\n', ' | ')[:110])
        finally:
            mgr.snapshot = _orig

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
        if drv is not None:
            try:
                drv.quit()
            except Exception:
                pass
        try:
            srv.shutdown()
        except Exception:
            pass
        for b in ('dlrun', 'dldone'):
            mgr.batches.pop(b, None)
            tm.detach(f'dl:{b}')
        public_data._manager = None
        DIRS['downloads'] = _prev
        shutil.rmtree(DLROOT, ignore_errors=True)

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
