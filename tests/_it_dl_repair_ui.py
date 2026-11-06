# -*- coding: utf-8 -*-
"""三个「修复按钮」的**真实浏览器**端到端验证：点下去真的能把批次救回来。

阶段一里为「批次卡死/假完成」加的三条修复路径，此前只验到
「管理器方法正确 + 路由返回 retried=N + 按钮存在」，
唯一没验的是**在页面上点下去之后，整条链路真的走通、界面真的更新**：

  1. `interrupted` 批次 → 点「▶ 续传」 → 真的重新下载并完成
  2. 有失败条目 → 点「↻ 重试失败(N)」 → 真的重下并完成
  3. `.sra` 转换失败 → 点「↻ 重试转换(N)」 → 真的解出 FASTQ、
     告警消失、`ready_files` 变得可用（这正是不用整批重下的关键）

为了可离线、可重复：**只把「字节传输」与「sracha 解码」两步打桩**
（真实传输已在第 1 轮用 175MB 真实下载验证过），其余（路由、管理器
状态机、转码收尾、前端轮询与渲染）全部走真实代码。
无浏览器/无 selenium 时自动 SKIP 并返回 0。
用法: python tests/_it_dl_repair_ui.py [--port 8796] [--headed]
"""
import argparse
import gzip
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
PAYLOAD = gzip.compress(b'@r1\nACGTACGT\n+\nIIIIIIII\n')
PLEN = len(PAYLOAD)


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


def entry(acc, out, status, done=0, seq=1):
    return {'acc': acc, 'url': f'https://ftp.sra.ebi.ac.uk/vol1/fastq/{acc}.fastq.gz',
            'out': out, 'size': PLEN, 'expect_bytes': PLEN,
            'done_bytes': done, 'progress': (done / PLEN if PLEN else 0.0),
            'status': status, 'md5': '', 'verified': False, 'error': '',
            'db': 'ENA', 'organism': ''}


def _run_once():
    global _CLEAN
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=8796)
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
    from Virus_Platform_Core import public_data
    from Virus_Platform_Core.config import DIRS

    DLROOT = os.path.join(ROOT, 'run', '_uitest_repair')
    shutil.rmtree(DLROOT, ignore_errors=True)
    os.makedirs(DLROOT, exist_ok=True)
    _CLEAN.append(DLROOT)
    DIRS['downloads'] = DLROOT
    public_data._manager = None
    mgr = public_data.get_manager()

    # ── 窄打桩：只替换「字节传输」与「sracha 解码」 ────────────────
    _real_dl = public_data.DownloadManager._dl_aria2
    _real_run = public_data.subprocess.run
    _real_popen = public_data.subprocess.Popen
    _real_host = public_data.check_url_host
    calls = {'dl': [], 'fastq': []}

    def fake_dl_aria2(self, a2, b, fe):
        """真实传输的替身：按 expect_bytes 精确落盘一份合法 gzip。"""
        calls['dl'].append(fe.get('acc'))
        os.makedirs(os.path.dirname(fe['out']), exist_ok=True)
        with open(fe['out'], 'wb') as f:
            f.write(PAYLOAD)
        return True

    def _decode_output(cmd):
        """若是 `sracha fastq ...` 调用，产出 FASTQ 并记一笔，返回是否命中。"""
        if isinstance(cmd, (list, tuple)) and len(cmd) > 2 and cmd[1] == 'fastq':
            sra = cmd[2]
            outdir = cmd[cmd.index('-O') + 1]
            base = os.path.basename(sra)[:-4]
            calls['fastq'].append(base)
            dst = os.path.join(outdir, base + '.fastq.gz')
            with open(dst, 'wb') as f:
                f.write(PAYLOAD)
            return True
        return False

    def fake_run(cmd, *args, **kw):
        """只拦 sracha fastq；其余子进程调用照旧。"""
        if _decode_output(cmd):
            return type('CP', (), {'returncode': 0})()
        return _real_run(cmd, *args, **kw)

    class FakeDecodeProc:
        """`_run_decode_cmd()` 用 Popen 起解码进程（会把句柄登记进 self.procs
        以便取消能终止）。这里做替身：直接产出 FASTQ、rc=0。"""

        def __init__(self, cmd, *a, **kw):
            self.cmd = cmd
            self.returncode = 0
            self._hit = _decode_output(cmd)

        def communicate(self, timeout=None):
            return b'', b''

        def kill(self):
            self.returncode = -9

        def poll(self):
            return self.returncode

        terminate = kill

    def fake_host(url):
        # 主机的白名单/私网校验不是本测试的对象；这里放行以免依赖 DNS
        return True, ''

    public_data.DownloadManager._dl_aria2 = fake_dl_aria2
    public_data.subprocess.run = fake_run
    public_data.subprocess.Popen = FakeDecodeProc
    public_data.check_url_host = fake_host

    # ── 三个待修批次 ───────────────────────────────────────────
    bdirs = {}
    for bid in ('repA', 'repB', 'repC'):
        bdirs[bid] = os.path.join(DLROOT, bid)
        os.makedirs(bdirs[bid], exist_ok=True)
        with open(os.path.join(bdirs[bid], 'batch.log'), 'w', encoding='utf-8') as f:
            f.write(f'[{bid}] 测试批次\n')

    # A) 服务重启中断：条目退回 pending、failed=0（原本界面无任何出路）
    mgr.batches['repA'] = {
        'id': 'repA', 'name': 'uitest_resume', 'status': 'interrupted',
        'created': '2026-01-01 00:00', 'dir': bdirs['repA'], 'runs': {},
        'error': '', 'converting': False, 'convert_msg': '', 'files': [
            entry('ERR7586041', os.path.join(bdirs['repA'], 'a_1.fastq.gz'),
                  'pending')]}
    # B) 下载失败
    mgr.batches['repB'] = {
        'id': 'repB', 'name': 'uitest_retryfail', 'status': 'failed',
        'created': '2026-01-01 00:00', 'dir': bdirs['repB'], 'runs': {},
        'error': '', 'converting': False, 'convert_msg': '', 'files': [
            entry('ERR999', os.path.join(bdirs['repB'], 'b.fastq.gz'),
                  'failed', seq=2)]}
    mgr.batches['repB']['files'][0]['error'] = '模拟下载失败'
    # C) .sra 转换失败（批次已 completed，但只有一个没用的 .sra）
    sra = os.path.join(bdirs['repC'], 'SRR39909446.sra')
    with open(sra, 'wb') as f:
        f.write(b'\x00' * 32)
    mgr.batches['repC'] = {
        'id': 'repC', 'name': 'uitest_convfail', 'status': 'completed',
        'created': '2026-01-01 00:00', 'dir': bdirs['repC'], 'runs': {},
        'error': '1 个 .sra 转换失败，本批次暂无可用 FASTQ',
        'converting': False, 'convert_failed': 1,
        'convert_msg': '⚠ 1 个 .sra 未转成 FASTQ（可点「重试转换」）', 'files': [
            {'acc': 'SRR39909446', 'url': '', 'out': sra, 'size': 32,
             'expect_bytes': 32, 'done_bytes': 32, 'progress': 1.0,
             'status': 'done', 'md5': '', 'verified': True, 'error': '',
             'db': 'ENA', 'organism': ''}]}

    from werkzeug.serving import make_server
    port = free_port(a.port)
    srv = make_server('127.0.0.1', port, appmod.app, threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{port}'
    print(f'临时实例: {base}  （用户的 8989 未被触碰）', flush=True)

    drv = None
    try:
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
        wait = WebDriverWait(drv, 40)
        # 确认对话框一律通过
        drv.get(base + '/download')
        wait.until(lambda d: d.find_elements(By.CSS_SELECTOR, '#batchList .card'))
        drv.execute_script("window.confirm = function(){return true;};"
                           "window.alert = function(){};")

        def card_text(bid):
            els = drv.find_elements(By.CSS_SELECTOR, f'#b-{bid}')
            return els[0].text if els else ''

        def click_btn(bid, label):
            el = drv.find_element(By.ID, f'b-{bid}')
            btns = el.find_elements(
                By.XPATH, f".//button[contains(., '{label}')]")
            if not btns:
                return False
            drv.execute_script("arguments[0].click();", btns[0])
            return True

        def wait_status(bid, want, timeout=40):
            t0 = time.time()
            while time.time() - t0 < timeout:
                s = mgr.snapshot(bid) or {}
                if s.get('status') == want:
                    return s
                time.sleep(0.3)
            return mgr.snapshot(bid) or {}

        # ═══ 1) interrupted → 续传 ═══
        print('\n== 1) interrupted 批次点「续传」==', flush=True)
        txt0 = card_text('repA')
        check('（1）初始为已中断且显示「续传」按钮',
              '已中断' in txt0 and '续传' in txt0,
              txt0.replace('\n', ' | ')[:110])
        check('（1）点到了「续传」', click_btn('repA', '续传'))
        snap = wait_status('repA', 'completed', timeout=45)
        check('（1）批次真的重新下载并完成',
              snap.get('status') == 'completed' and snap.get('done') == 1,
              f"status={snap.get('status')} done={snap.get('done')} "
              f"failed={snap.get('failed')}")
        check('（1）确实调用了下载（打桩的传输被触发）',
              'ERR7586041' in calls['dl'], str(calls['dl']))
        check('（1）文件真的落盘',
              os.path.isfile(os.path.join(bdirs['repA'], 'a_1.fastq.gz')))
        # 界面：续传按钮消失、显示已完成
        t0 = time.time()
        txtA = ''
        while time.time() - t0 < 15:
            txtA = card_text('repA')
            if '已完成' in txtA and '续传' not in txtA:
                break
            time.sleep(0.5)
        check('（1）界面更新为已完成且续传按钮消失',
              '已完成' in txtA and '续传' not in txtA,
              txtA.replace('\n', ' | ')[:110])

        # ═══ 2) 失败条目 → 重试失败 ═══
        print('\n== 2) 失败条目点「重试失败」==', flush=True)
        txt0 = card_text('repB')
        check('（2）初始显示「重试失败(1)」按钮',
              '重试失败' in txt0, txt0.replace('\n', ' | ')[:110])
        check('（2）点到了「重试失败」', click_btn('repB', '重试失败'))
        snap = wait_status('repB', 'completed', timeout=45)
        check('（2）重试后真的下载完成',
              snap.get('status') == 'completed' and snap.get('done') == 1
              and snap.get('failed') == 0,
              f"status={snap.get('status')} done={snap.get('done')} "
              f"failed={snap.get('failed')}")
        check('（2）失败条目的 error 已清空',
              not (snap.get('files') or [{}])[0].get('error'),
              str((snap.get('files') or [{}])[0].get('error'))[:60])

        # ═══ 3) 转换失败 → 重试转换 ═══
        print('\n== 3) 转换失败点「重试转换」（不用整批重下）==', flush=True)
        txt0 = card_text('repC')
        check('（3）初始显示告警与「重试转换」按钮',
              '重试转换' in txt0 and '未转成 FASTQ' in txt0,
              txt0.replace('\n', ' | ')[:130])
        rf0 = mgr.ready_files('repC')
        check('（3）修复前 ready_files 为空（无法转入分析流程）', not rf0,
              str(rf0))
        check('（3）点到了「重试转换」', click_btn('repC', '重试转换'))
        t0 = time.time()
        snap = {}
        while time.time() - t0 < 60:
            snap = mgr.snapshot('repC') or {}
            if not snap.get('convert_failed'):
                break
            time.sleep(0.4)
        check('（3）转换成功（convert_failed 归零）',
              not snap.get('convert_failed'),
              f"convert_failed={snap.get('convert_failed')} "
              f"msg={snap.get('convert_msg')!r}")
        check('（3）sracha 解码被真实调用', 'SRR39909446' in calls['fastq'],
              str(calls['fastq']))
        check('（3）产物 FASTQ 落盘且 .sra 已清理',
              os.path.isfile(os.path.join(bdirs['repC'],
                                          'SRR39909446.fastq.gz'))
              and not os.path.isfile(sra))
        rf = mgr.ready_files('repC')
        check('（3）修复后 ready_files 可用（能转入分析流程）',
              bool(rf) and 'single' in (rf.get('SRR39909446') or {}),
              str({k: list(v) for k, v in rf.items()}))
        t0 = time.time()
        txtC = ''
        while time.time() - t0 < 15:
            txtC = card_text('repC')
            if '重试转换' not in txtC:
                break
            time.sleep(0.5)
        check('（3）界面告警与按钮消失、显示转换成功',
              '重试转换' not in txtC and 'FASTQ' in txtC,
              txtC.replace('\n', ' | ')[:130])

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
        public_data.DownloadManager._dl_aria2 = _real_dl
        public_data.subprocess.run = _real_run
        public_data.subprocess.Popen = _real_popen
        public_data.check_url_host = _real_host
        if drv is not None:
            try:
                drv.quit()
            except Exception:
                pass
        try:
            srv.shutdown()
        except Exception:
            pass
        for b in ('repA', 'repB', 'repC'):
            mgr.batches.pop(b, None)
        public_data._manager = None
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
