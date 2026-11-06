# -*- coding: utf-8 -*-
"""公共数据下载页的**真实浏览器**验证（Selenium + 本机 Edge/Chrome，headless）。

锁定的三个前端缺陷（都是「进度条/大小/速度好像没啥用」的直接成因，
后端测试覆盖不到，只能在真浏览器里验）：

  1. 逐文件「已下载 / 总量」倒挂：size 曾用下载器的近似总量（aria2c 只报
     0.1MiB 精度），8.8 MB / 8.7 MB。现在必须以 expect_bytes 为分母。
  2. 展开的逐文件表格被轮询抹掉：refresh() 每 2 秒整体重建 #batchList，
     刚展开的 📁 列表连同进度条一起被销毁；且明细只取一次、永不刷新。
     本测试展开后**跨两轮轮询**断言表格仍在**且数字已更新**。
  3. 重试/续传入口：interrupted 批次必须有「续传」按钮，
     convert_failed 必须有「重试转换」按钮。

无浏览器/无 selenium 时自动跳过（退出码 0），不阻塞批量测试。
用法: python tests/_it_download_ui.py [--port 8799] [--keep] [--headed]
"""
import argparse
import os
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
SKIPPED = []


def check(label, ok, extra=''):
    print(f'  [{"PASS" if ok else "FAIL"}] {label}' + (f'  {extra}' if extra else ''),
          flush=True)
    if not ok:
        FAIL.append(label)


def skip(label, why):
    print(f'  [SKIP] {label}  {why}', flush=True)
    SKIPPED.append(label)


# ── 1) 起一个临时实例（本进程内，不碰用户的 8989） ────────────────
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
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=8799)
    ap.add_argument('--keep', action='store_true')
    ap.add_argument('--headed', action='store_true')
    a = ap.parse_args()

    try:
        from selenium import webdriver
        from selenium.webdriver.common.by import By
        from selenium.webdriver.edge.options import Options as EdgeOptions
        from selenium.webdriver.support.ui import WebDriverWait
    except ImportError as e:
        skip('selenium', f'未安装（{e}）')
        return 0

    import app as appmod
    from Virus_Platform_Core import public_data
    from Virus_Platform_Core.config import DIRS

    # 独立 downloads 根，避免污染真实批次
    DLROOT = os.path.join(ROOT, 'run', '_uitest_downloads')
    import shutil
    shutil.rmtree(DLROOT, ignore_errors=True)
    os.makedirs(DLROOT, exist_ok=True)
    DIRS['downloads'] = DLROOT
    public_data._manager = None
    mgr = public_data.get_manager()

    # ── 2) 注入三种批次（不真下载） ──────────────────────────────
    # A 下载中：故意让 size 是「下载器近似值」而 expect_bytes 是精确值，
    #   两者格式化后都进 8.x MB —— 若前端用 size 做分母就会显示 8.8/8.7。
    EXACT, COARSE = 8772679, 8703180
    bdirA = os.path.join(DLROOT, 'uiA')
    os.makedirs(bdirA, exist_ok=True)
    with open(os.path.join(bdirA, 'batch.log'), 'w', encoding='utf-8') as f:
        f.write('[00:00:01] 测试批次 A\n')
    filesA = [
        {'acc': 'ERR7586041', 'url': 'https://x/a_1.fastq.gz',
         'out': os.path.join(bdirA, 'ERR7586041_1.fastq.gz'),
         'size': COARSE, 'expect_bytes': EXACT, 'done_bytes': EXACT,
         'progress': 1.0, 'status': 'downloading', 'md5': '', 'verified': False,
         'error': '', 'db': 'ENA', 'organism': '', 'speed': 1_500_000,
         'eta': 30},
        {'acc': 'ERR7586041', 'url': 'https://x/a_2.fastq.gz',
         'out': os.path.join(bdirA, 'ERR7586041_2.fastq.gz'),
         'size': 8912896, 'expect_bytes': 8913341, 'done_bytes': 4_456_670,
         'progress': 0.5, 'status': 'downloading', 'md5': '', 'verified': False,
         'error': '', 'db': 'ENA', 'organism': '', 'speed': 800_000, 'eta': 6},
        {'acc': 'SRR999', 'url': 'https://x/c.fastq.gz',
         'out': os.path.join(bdirA, 'c.fastq.gz'),
         'size': 1000000, 'expect_bytes': 1000000, 'done_bytes': 0,
         'progress': 0.0, 'status': 'pending', 'md5': '', 'verified': False,
         'error': '', 'db': 'ENA', 'organism': '', 'speed': 0},
    ]
    mgr.batches['uiA'] = {
        'id': 'uiA', 'name': 'uitest_downloading', 'status': 'downloading',
        'created': '2026-01-01 00:00', 'dir': bdirA, 'files': filesA,
        'runs': {}, 'error': '', 'converting': False, 'convert_msg': ''}

    # B 中断：必须有「续传」按钮
    bdirB = os.path.join(DLROOT, 'uiB')
    os.makedirs(bdirB, exist_ok=True)
    mgr.batches['uiB'] = {
        'id': 'uiB', 'name': 'uitest_interrupted', 'status': 'interrupted',
        'created': '2026-01-01 00:00', 'dir': bdirB, 'runs': {}, 'error': '',
        'converting': False, 'convert_msg': '', 'files': [
            {'acc': 'SRR1', 'url': 'https://x/d.fq.gz',
             'out': os.path.join(bdirB, 'd.fq.gz'), 'size': 10,
             'expect_bytes': 10, 'done_bytes': 4, 'progress': 0.4,
             'status': 'pending', 'md5': '', 'verified': False, 'error': '',
             'db': 'ENA', 'organism': ''}]}

    # C 转换失败：必须有「重试转换」按钮
    bdirC = os.path.join(DLROOT, 'uiC')
    os.makedirs(bdirC, exist_ok=True)
    mgr.batches['uiC'] = {
        'id': 'uiC', 'name': 'uitest_convfail', 'status': 'completed',
        'created': '2026-01-01 00:00', 'dir': bdirC, 'runs': {},
        'error': '1 个 .sra 转换失败，本批次暂无可用 FASTQ',
        'converting': False, 'convert_failed': 1,
        'convert_msg': '⚠ 1 个 .sra 未转成 FASTQ（可点「重试转换」）', 'files': [
            {'acc': 'SRR2', 'url': '', 'out': os.path.join(bdirC, 'e.sra'),
             'size': 20, 'expect_bytes': 20, 'done_bytes': 20, 'progress': 1.0,
             'status': 'done', 'md5': '', 'verified': True, 'error': '',
             'db': 'ENA', 'organism': ''}]}

    # ── 3) 起服务 ────────────────────────────────────────────────
    from werkzeug.serving import make_server
    port = free_port(a.port)
    srv = make_server('127.0.0.1', port, appmod.app, threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{port}'
    print(f'临时实例: {base}  （用户的 8989 未被触碰）\n', flush=True)

    drv = None
    try:
        opts = EdgeOptions()
        if not a.headed:
            opts.add_argument('--headless=new')
        opts.add_argument('--no-sandbox')
        opts.add_argument('--disable-gpu')
        opts.add_argument('--window-size=1440,1000')
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
        drv.set_page_load_timeout(30)
        wait = WebDriverWait(drv, 20)

        print('== 页面与批次卡 ==', flush=True)
        drv.get(base + '/download')
        wait.until(lambda d: d.find_elements(By.CSS_SELECTOR, '#batchList .card'))
        cards = drv.find_elements(By.CSS_SELECTOR, '#batchList .card')
        ids = [c.get_attribute('id') for c in cards]
        check('三张批次卡都渲染出来',
              {'b-uiA', 'b-uiB', 'b-uiC'} <= set(ids), str(ids))
        cardA = drv.find_element(By.ID, 'b-uiA')

        print('\n== 指标条：速度 / 已下载·总量 / 剩余 ==', flush=True)
        spd = cardA.find_element(
            By.CSS_SELECTOR, '[data-dl-metric="speed"]').text.strip()
        dl = cardA.find_element(
            By.CSS_SELECTOR, '[data-dl-metric="downloaded"]').text.strip()
        rem = cardA.find_element(
            By.CSS_SELECTOR, '[data-dl-metric="remaining"]').text.strip()
        check('速度不是「—」（速度监测真的在动）', spd not in ('—', '', '-'),
              f'speed={spd!r}')
        check('已下载/总量是 X / Y 形式', ' / ' in dl, f'{dl!r}')
        check('剩余时间不是「计算中…」', '计算中' not in rem and rem, f'{rem!r}')
        # 批次总量必须 = 三个条目 expect_bytes 之和（而非近似 size 之和）
        exp_total = EXACT + 8913341 + 1000000
        exp_done = EXACT + 4_456_670
        check('批次「已下载」按精确总量计算',
              f'{exp_done / 1e6:.1f} MB' in dl and f'{exp_total / 1e6:.1f} MB' in dl,
              f'渲染={dl!r} 期望 {exp_done/1e6:.1f} MB / {exp_total/1e6:.1f} MB')
        # 字节加权进度：条目均值=0.5，字节加权≈0.57
        pct = cardA.find_element(By.CSS_SELECTOR, '[data-dl-overall]') \
            .get_attribute('data-dl-overall')
        check('批次进度是字节加权值（非条目均值 50%）', int(pct) != 50,
              f'overall={pct}%（条目均值会是 50%）')

        print('\n== 逐文件表格：倒挂与「展开后被抹掉」 ==', flush=True)
        files_btn = cardA.find_element(
            By.XPATH, ".//button[contains(., '文件')]")
        files_btn.click()
        wait.until(lambda d: d.find_elements(
            By.CSS_SELECTOR, '#b-uiA .filelist [data-dl-file]'))

        def row_state(d):
            """逐文件行快照。**按 DOM 序号 + acc 做键**：同一条 acc 会拆成
            _1/_2 两个文件行（acc 相同），只用 acc 做键会互相覆盖。"""
            out = {}
            for i, r in enumerate(d.find_elements(
                    By.CSS_SELECTOR, '#b-uiA .filelist [data-dl-file]')):
                acc = r.get_attribute('data-dl-file')
                out[f'{i}|{acc}'] = {
                    'acc': acc,
                    'progress': r.get_attribute('data-progress'),
                    'done': int(r.get_attribute('data-done')),
                    'total': int(r.get_attribute('data-total')),
                    'speed': r.get_attribute('data-speed'),
                    'size_cell': r.find_element(
                        By.CSS_SELECTOR, '[data-dl-size-cell]').text.strip(),
                    'visible': r.is_displayed()}
            return out

        st1 = row_state(drv)
        check('展开后逐文件行可见（下载中条目有进度条/大小/速度）',
              len(st1) >= 3 and all(v['visible'] for v in st1.values()),
              f'{len(st1)} 行: '
              f'{[(v["acc"], v["size_cell"], v["speed"]) for v in st1.values()]}')
        bad = [k for k, v in st1.items() if v['done'] > v['total']]
        check('每条 done <= total（无「已下载 > 总量」）', not bad, str(bad))

        # 文本层再判一次倒挂：把 "8.8 MB / 8.7 MB" 解析成数字比大小
        def parse_pair(txt):
            import re as _re
            m = _re.findall(r'([\d.]+)\s*(GB|MB|KB|B)', txt)
            return [float(x) for x, _u in m][:2]

        inv = []
        for k, v in st1.items():
            nums = parse_pair(v['size_cell'])
            if len(nums) == 2 and nums[0] > nums[1] + 1e-9:
                inv.append((k, v['size_cell']))
        check('渲染文本无倒挂（8.8 MB / 8.8 MB 而不是 8.8 / 8.7）',
              not inv, str(inv))
        # 该条目 size=8703180（近似，→8.7 MB）、expect_bytes=8772679、
        # done=8772679（→8.8 MB）。若拿 size 当分母就会渲染成 8.8 / 8.7。
        exact=[v for v in st1.values() if v['total'] == EXACT]
        check('下载中条目以 expect_bytes 为分母（非近似 size）',
              len(exact) == 1
              and parse_pair(exact[0]['size_cell']) == [8.8, 8.8],
              f"size_cell={exact[0]['size_cell'] if exact else None!r}"
              f"（用 size 会渲染成 8.8 MB / 8.7 MB）")
        check('下载中条目显示真实速度（非 —）',
              len(exact) == 1 and exact[0]['speed'] not in ('0', ''),
              f"speed={exact[0]['speed'] if exact else None}")

        print('\n== 跨两轮轮询：表格仍在 + 数字已更新 ==', flush=True)
        # 轮询在进行中批次上是 2s 一次；改数据后等 6s 保证刷新过 2 次以上
        time.sleep(2.5)
        with mgr.lock:
            filesA[0]['done_bytes'] = 4_000_000
            filesA[0]['progress'] = 0.456
            filesA[0]['speed'] = 2_200_000
            filesA[1]['done_bytes'] = 8_000_000
            filesA[1]['progress'] = 0.897
            filesA[1]['speed'] = 1_100_000
        time.sleep(6.0)
        st2 = row_state(drv)
        still = [k for k, v in st2.items() if v['visible']]
        check('跨轮询后逐文件表格**仍然展开可见**（不再被整体重建抹掉）',
              len(still) >= 3, f'可见行={len(still)}/{len(st2)}')
        k0 = [k for k, v in st2.items() if v['total'] == EXACT]
        check('跨轮询后数字**已更新**（明细不再只取一次）',
              bool(k0) and st2[k0[0]]['done'] == 4_000_000,
              f'改后 done={st2[k0[0]]["done"] if k0 else None}（期望 4000000）')
        check('进度条宽度随新数据变化',
              bool(k0) and st2[k0[0]]['progress'] == '46',
              f'progress={st2[k0[0]]["progress"] if k0 else None}%（期望 46）')
        check('速度也随新数据变化',
              bool(k0) and st2[k0[0]]['speed'] == '2200000',
              f'speed={st2[k0[0]]["speed"] if k0 else None}（期望 2200000）')

        print('\n== 重试 / 续传 / 重试转换 入口 ==', flush=True)
        cardB = drv.find_element(By.ID, 'b-uiB')
        check('interrupted 批次有「续传」按钮',
              bool(cardB.find_elements(By.XPATH, ".//button[contains(., '续传')]")),
              cardB.text.replace('\n', ' | ')[:90])
        cardC = drv.find_element(By.ID, 'b-uiC')
        check('convert_failed 批次有「重试转换」按钮',
              bool(cardC.find_elements(
                  By.XPATH, ".//button[contains(., '重试转换')]")),
              cardC.text.replace('\n', ' | ')[:90])
        check('转换失败批次显示红色告警文案与错误',
              '未转成 FASTQ' in cardC.text or '转换失败' in cardC.text,
              cardC.text.replace('\n', ' | ')[:120])

        print('\n== 浏览器控制台无 JS 报错 ==', flush=True)
        try:
            logs = drv.get_log('browser')
            # 只关心真正的脚本错误：favicon/静态资源 404 不是页面缺陷
            js_err = [l for l in logs
                      if l.get('level') == 'SEVERE'
                      and l.get('source') == 'javascript']
            check('无 SEVERE 级 JS 错误', not js_err,
                  str([l.get('message', '')[:120] for l in js_err])[:200])
            noise = [l for l in logs if l.get('level') == 'SEVERE'
                     and l.get('source') != 'javascript']
            if noise:
                print(f'    （忽略 {len(noise)} 条非 JS 的 SEVERE，'
                      f'如 favicon 404）', flush=True)
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
        public_data._manager = None
        for b in ('uiA', 'uiB', 'uiC'):
            mgr.batches.pop(b, None)
        if not a.keep:
            shutil.rmtree(DLROOT, ignore_errors=True)
        else:
            print('保留:', DLROOT, flush=True)

    print('\n' + ('ALL PASS' if not FAIL
                  else f'{len(FAIL)} FAIL: {FAIL}'), flush=True)
    return 0 if not FAIL else 1


def main():
    """跑一次；WebDriver 会话偶发原生崩溃时重试一次。

    4 个浏览器测试在同一批里连跑时实测出现过 msedgedriver 原生崩溃
    （单独复跑即通过），属会话级抖动、不是页面缺陷。这里只对
    WebDriverException 重试一次，断言级失败照旧原样返回。
    """
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
