# -*- coding: utf-8 -*-
"""内置 HTTP 回退下载（_dl_python）与完整性闸门的回归测试（离线）。

`_dl_python` 是 aria2c 缺失/失败时的兜底路径（自定义 URL 条目、NGDC 场景
都走它），但此前**没有任何测试覆盖**。本文件用本机 HTTP 服务端把它的
四条分支全部跑一遍：

  1. 全新下载：字节完全一致、进度按精确总量算
  2. **断点续传**：`.aria_part` 已有前半段时，必须发出 Range 请求并把
     后半段**追加**上去 —— 结果必须与完整文件逐字节相同
     （原实现用 safe_open(part,'wb') 打开，'wb' 会**截断**已下载的前缀，
      于是续传产出的是一个"只有尾巴"的坏文件；expect_bytes 有值时会被
      完整性检查判失败，无值时则**静默交付坏文件**）
  3. 服务端 416（本地已完整）：直接改名到位
  4. 精确总量（expect_bytes）不得被 content-length 覆盖
  5. 404 等失败：返回 False 且 error 可读
  6. 完整性闸门：字节数不一致 / gzip 损坏 / 无精确值时偏差过大
     三种情况都必须判 failed，不能把坏文件标成 done

纯离线：服务端是本地 ThreadingHTTPServer，不访问外网。
用法: python tests/_it_dl_http_fallback.py
"""
import gzip
import http.server
import os
import shutil
import sys
import tempfile
import threading

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors='replace')
    except (AttributeError, OSError):
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ['VP_NO_REPROCESS'] = '1'

FAIL = []


def check(label, ok, extra=''):
    print(f'  [{"PASS" if ok else "FAIL"}] {label}' + (f'  {extra}' if extra else ''),
          flush=True)
    if not ok:
        FAIL.append(label)


from Virus_Platform_Core import public_data                       # noqa: E402
from Virus_Platform_Core.public_data import DownloadManager, _gzip_ok  # noqa: E402

WORKROOT = os.path.join(ROOT, 'run', '_it_dl_http_fallback')
shutil.rmtree(WORKROOT, ignore_errors=True)
os.makedirs(WORKROOT, exist_ok=True)

PAYLOAD = bytes(range(256)) * 4096          # 1 MiB，内容可判别
assert len(PAYLOAD) == 1048576
REQUESTS = []                                # 记录服务端收到的 Range


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/missing':
            self.send_error(404, 'nope')
            return
        data = PAYLOAD
        rng = self.headers.get('Range') or ''
        REQUESTS.append(rng)
        if rng.startswith('bytes='):
            try:
                start = int(rng.split('=', 1)[1].split('-')[0])
            except (ValueError, IndexError):
                start = 0
            if start >= len(data):
                self.send_response(416)
                self.send_header('Content-Range', f'bytes */{len(data)}')
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
            body = data[start:]
            self.send_response(206)
            self.send_header('Content-Range',
                             f'bytes {start}-{len(data)-1}/{len(data)}')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(200)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
PORT = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f'http://127.0.0.1:{PORT}'
print(f'本机测试服务端: {BASE}（离线，不访问外网）', flush=True)


def mgr():
    m = DownloadManager.__new__(DownloadManager)
    m.lock = threading.RLock()
    m.procs = {}
    m._spd = {}
    m.batches = {}
    return m


def fe(out, url=f'{BASE}/data', expect=0, size=0):
    return {'acc': 'X', 'url': url, 'out': out, 'size': size,
            'expect_bytes': expect, 'done_bytes': 0, 'progress': 0.0,
            'status': 'downloading', 'md5': '', 'verified': False,
            'error': '', 'db': 'URL', 'organism': ''}


def read(p):
    with open(p, 'rb') as f:
        return f.read()


# _dl_python 现按批次状态响应暂停/取消（2026-11-06 签名变更）：测试用最小
# 批次 dict 传入；状态恒为 downloading，读循环不会被暂停分支中止。
B = {'status': 'downloading'}


D = os.path.join(WORKROOT, 'dl')
os.makedirs(D, exist_ok=True)

# ── 1) 全新下载 ─────────────────────────────────────────────────
print('\n== 1) 全新下载 ==', flush=True)
out1 = os.path.join(D, 'fresh.bin')
f1 = fe(out1, expect=len(PAYLOAD))
ok = mgr()._dl_python(B, f1)
check('（1）返回成功', ok is True, str(f1.get('error')))
check('（1）字节与源文件完全一致',
      os.path.isfile(out1) and read(out1) == PAYLOAD,
      f'{os.path.getsize(out1) if os.path.isfile(out1) else -1} B')
check('（1）done_bytes == 精确总量', f1['done_bytes'] == len(PAYLOAD),
      str(f1['done_bytes']))
check('（1）下载中进度 <1.0（收尾由调用方置 1.0）', f1['progress'] < 1.0,
      str(f1['progress']))
check('（1）未留下 .aria_part',
      not os.path.exists(out1 + '.aria_part'))

# ── 2) 断点续传（本轮修的真 bug） ───────────────────────────────
print('\n== 2) 断点续传（必须追加，不能截断前缀）==', flush=True)
out2 = os.path.join(D, 'resume.bin')
half = len(PAYLOAD) // 2
with open(out2 + '.aria_part', 'wb') as f:
    f.write(PAYLOAD[:half])                 # 模拟上次中断留下的半截
REQUESTS.clear()
f2 = fe(out2, expect=len(PAYLOAD))
ok = mgr()._dl_python(B, f2)
check('（2）返回成功', ok is True, str(f2.get('error')))
check('（2）确实发出了 Range 请求（走了续传分支）',
      any(r.startswith('bytes=') for r in REQUESTS), str(REQUESTS[:2]))
got = read(out2) if os.path.isfile(out2) else b''
check('（2）续传结果与完整文件逐字节一致（不能被截断前缀）',
      got == PAYLOAD,
      f'got {len(got)} B / want {len(PAYLOAD)} B'
      + ('（只有尾巴 —— 前缀被 wb 截断了）'
         if len(got) == len(PAYLOAD) - half else ''))
check('（2）done_bytes 为完整长度', f2['done_bytes'] == len(PAYLOAD),
      str(f2['done_bytes']))
check('（2）磁盘实际大小与上报的 done_bytes 一致（核心不变式）',
      os.path.getsize(out2) == f2['done_bytes'],
      f"disk={os.path.getsize(out2) if os.path.isfile(out2) else -1} "
      f"reported={f2['done_bytes']}")
check('（2）未留下 .aria_part',
      not os.path.exists(out2 + '.aria_part'))

# ── 3) 服务端 416：本地已完整 ───────────────────────────────────
print('\n== 3) 服务端 416（本地已完整）==', flush=True)
out3 = os.path.join(D, 'done416.bin')
with open(out3 + '.aria_part', 'wb') as f:
    f.write(PAYLOAD)                        # 本地已是完整文件
f3 = fe(out3, expect=len(PAYLOAD))
ok = mgr()._dl_python(B, f3)
check('（3）返回成功', ok is True, str(f3.get('error')))
check('（3）直接改名到位且内容完整',
      os.path.isfile(out3) and read(out3) == PAYLOAD)

# ── 4) 精确总量不被 content-length 覆盖 ─────────────────────────
print('\n== 4) 精确总量（expect_bytes）优先 ==', flush=True)
out4 = os.path.join(D, 'keepsize.bin')
f4 = fe(out4, expect=len(PAYLOAD), size=999)   # size 故意是错的近似值
mgr()._dl_python(B, f4)
check('（4）size 未被 content-length 覆盖（仍是 999）', f4['size'] == 999,
      str(f4['size']))
check('（4）文件仍然完整', read(out4) == PAYLOAD)
out4b = os.path.join(D, 'nosize.bin')
f4b = fe(out4b, expect=0, size=0)              # 无精确值 → 用 content-length
mgr()._dl_python(B, f4b)
check('（4）无精确值时用 content-length 填 size',
      f4b['size'] == len(PAYLOAD), str(f4b['size']))

# ── 5) 失败可读 ─────────────────────────────────────────────────
print('\n== 5) 404 失败 ==', flush=True)
out5 = os.path.join(D, 'missing.bin')
f5 = fe(out5, url=f'{BASE}/missing')
ok = mgr()._dl_python(B, f5)
check('（5）返回失败', ok is False)
check('（5）error 可读', 'HTTP 下载失败' in (f5.get('error') or ''),
      str(f5.get('error'))[:80])
check('（5）失败时不留下成品文件（只有 .aria_part）',
      not os.path.isfile(out5))

# ── 6) gzip 完整性检查 ──────────────────────────────────────────
print('\n== 6) gzip 完整性检查 ==', flush=True)
bad = os.path.join(D, 'bad.fastq.gz')
with open(bad, 'wb') as f:
    f.write(b'NOT A GZIP FILE' * 100)
check('（6）假 gzip 被判损坏', _gzip_ok(bad) is False)
good = os.path.join(D, 'good.fastq.gz')
with open(good, 'wb') as f:
    f.write(gzip.compress(b'@r1\nACGT\n+\nIIII\n' * 5000))
check('（6）真 gzip 通过', _gzip_ok(good) is True)

# ── 7) _download_file 的三道完整性闸门 ───────────────────────────
# 打桩字节传输，专测「下完了但校验不过」必须判 failed 而不是 done。
print('\n== 7) 完整性闸门 ==', flush=True)
_real_dl = DownloadManager._dl_aria2
_real_host = public_data.check_url_host
_real_aria = public_data.aria2c_path
WRONG = b'\x1f\x8b' + b'\x00' * 30            # gzip 魔数对但内容解不开


def make_batch(out_path):
    return {'id': 't', 'name': 't', 'status': 'downloading',
            'created': '', 'dir': os.path.dirname(out_path), 'runs': {},
            'error': '', 'converting': False, 'convert_msg': '', 'files': []}


def gate_case(tag, write_bytes, expect, size, outname, want_err):
    out = os.path.join(D, outname)
    b = make_batch(out)
    fex = {'acc': 'X', 'url': f'{BASE}/data', 'out': out, 'size': size,
           'expect_bytes': expect, 'done_bytes': 0, 'progress': 0.0,
           'status': 'downloading', 'md5': '', 'verified': False,
           'error': '', 'db': 'URL', 'organism': ''}
    b['files'].append(fex)
    mg = mgr()
    mg.batches['t'] = b
    DownloadManager._dl_aria2 = lambda self, a2, b_, f_: (
        open(f_['out'], 'wb').write(write_bytes) and True)
    try:
        mg._download_file(b, fex)
    finally:
        DownloadManager._dl_aria2 = _real_dl
    check(f'（7）{tag}', fex['status'] == 'failed'
          and want_err in (fex.get('error') or ''),
          f"status={fex['status']} error={str(fex.get('error'))[:60]!r}")
    return out


public_data.check_url_host = lambda u: (True, '')
public_data.aria2c_path = lambda: 'aria2c'      # 让分支走 _dl_aria2（已打桩）
try:
    # a) 有精确总量但字节数不一致
    gate_case('字节数不一致 → failed', b'X' * 10, 100, 0, 'g1.bin',
              '字节数不一致')
    # b) 无精确总量、size 与实际偏差 >1MiB
    gate_case('无精确值且偏差过大 → failed', b'X' * 10, 0, 10 << 20,
              'g2.bin', '偏差过大')
    # c) 字节数对得上但 gzip 损坏
    gate_case('gzip 损坏 → failed', WRONG, len(WRONG), len(WRONG),
              'g3.fastq.gz', 'gzip 文件损坏')
    # d) 一切正常 → done
    goodg = gzip.compress(b'@r1\nACGT\n+\nIIII\n' * 100)
    out_d = os.path.join(D, 'g4.fastq.gz')
    b = make_batch(out_d)
    fed = {'acc': 'X', 'url': f'{BASE}/data', 'out': out_d,
           'size': len(goodg), 'expect_bytes': len(goodg), 'done_bytes': 0,
           'progress': 0.0, 'status': 'downloading', 'md5': '',
           'verified': False, 'error': '', 'db': 'URL', 'organism': ''}
    b['files'].append(fed)
    mg = mgr()
    mg.batches['t'] = b
    DownloadManager._dl_aria2 = lambda self, a2, b_, f_: (
        open(f_['out'], 'wb').write(goodg) and True)
    try:
        mg._download_file(b, fed)
    finally:
        DownloadManager._dl_aria2 = _real_dl
    check('（7）正常文件 → done 且 verified',
          fed['status'] == 'done' and fed['verified'] is True
          and fed['progress'] == 1.0,
          f"status={fed['status']} verified={fed['verified']}")
finally:
    DownloadManager._dl_aria2 = _real_dl
    public_data.check_url_host = _real_host
    public_data.aria2c_path = _real_aria

srv.shutdown()
shutil.rmtree(WORKROOT, ignore_errors=True)
print('\n' + ('ALL PASS' if not FAIL else f'{len(FAIL)} FAIL: {FAIL}'), flush=True)
sys.exit(0 if not FAIL else 1)
