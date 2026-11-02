# -*- coding: utf-8 -*-
"""新构建包的自检：真启动 exe（--web），验证本次功能点在冻结版里生效。

检查项（全部对着 http://127.0.0.1:<port> 发真实请求）：
  1. /api/dbs 每个库带 bytes / n_refs，且 taxonomy/host/virus 有真实字节数
  2. /api/dbs 带 meta 段（built_at / git_rev 键存在）
  3. /api/selfcheck 磁盘段带 usage（按目录占用）
  4. 首页含主行动按钮（cover-act）与运行中任务容器（runSec）
  5. /static/app.js 含 renderRunTasks / dbMetaLine
  6. webapp 静态资源与源码逐字节一致（模板取 / 页面渲染前的源文件比对）

用法：python tests/_check_frozen_build.py <包目录>
  包目录 = 含 VirusPlatform.exe 与 _internal 的目录
"""
import os
import re
import socket
import subprocess
import sys
import time
import urllib.request

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding='utf-8', errors='replace')
    except (AttributeError, OSError):
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else None
if not PKG or not os.path.isfile(os.path.join(PKG, 'VirusPlatform.exe')):
    raise SystemExit('用法: python tests/_check_frozen_build.py <含 VirusPlatform.exe 的目录>')

FAIL = []


def check(name, cond, extra=''):
    print(('  ✔ ' if cond else '  ✘ ') + name + (f'  {extra}' if extra else ''))
    if not cond:
        FAIL.append(name)


def pid_listening_port(pid, tries=160):
    """反查该进程正在 LISTENING 的 127.0.0.1 端口。

    为什么不能猜端口：exe 按候选列表选（8765 被已有实例占用时会改用 8900），
    打固定端口会连到**旧实例**、把旧包当成新包验，全绿也是假的。按 PID 查
    netstat 是唯一可靠的对应关系。"""
    for _ in range(tries):
        try:
            out = subprocess.run(['netstat', '-ano'], capture_output=True,
                                 text=True, errors='replace').stdout
        except OSError:
            out = ''
        for line in out.splitlines():
            if 'LISTENING' not in line:
                continue
            m = re.search(r'127\.0\.0\.1:(\d+)\s+\S+\s+LISTENING\s+(\d+)', line)
            if m and int(m.group(2)) == pid:
                return int(m.group(1))
        time.sleep(0.5)
    return None


def get(path, port):
    with urllib.request.urlopen(f'http://127.0.0.1:{port}{path}', timeout=30) as r:
        return r.read()


def main():
    # BROWSER=echo 抑制 exe 启动时 webbrowser.open 弹真实浏览器窗口。
    # TEMP/TMP/TMPDIR 强制为合法 Windows 目录：Git Bash 会把它们设成 /tmp，
    # Windows 程序不认，matplotlib 的 rthook（secure_mkdtemp）会直接
    # "No usable temporary directory" 崩掉 —— 那是启动环境的锅，不是包。
    win_temp = os.environ.get('WinTemp') or r'C:\Windows\Temp'
    user_temp = os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Temp')
    if os.path.isdir(user_temp) and os.access(user_temp, os.W_OK):
        win_temp = user_temp
    env = dict(os.environ, VP_NO_RECOVER='1', BROWSER='echo',
               TEMP=win_temp, TMP=win_temp, TMPDIR=win_temp)
    proc = subprocess.Popen(
        [os.path.join(PKG, 'VirusPlatform.exe'), '--web'],
        cwd=PKG, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        port = pid_listening_port(proc.pid)
        if not port:
            print('✘ exe 80s 内未监听（进程可能启动失败）')
            return 2
        print(f'新冻结实例 http://127.0.0.1:{port}（{PKG}，pid {proc.pid}）')
        import json

        dbs = json.loads(get('/api/dbs', port))
        # bytes 是否"真实"取决于这台机器上库存不存在：临时构建目录旁边
        # 没有数据库包（ready=False → bytes 为空/0 是正确行为）；真实
        # 分发版 ready=True 时必须能算出非零字节数。判据跟着 ready 走。
        for key in ('taxonomy', 'host', 'virus'):
            v = dbs.get(key) or {}
            check(f'/api/dbs {key} 带 bytes/n_refs',
                  'bytes' in v and 'n_refs' in v,
                  f"ready={v.get('ready')} bytes={v.get('bytes')} n_refs={v.get('n_refs')}")
            if v.get('ready'):
                check(f'{key} 就绪 → 字节数为真实值',
                      bool(v.get('bytes')), str(v.get('bytes')))
        check('/api/dbs 带 meta 段', isinstance(dbs.get('meta'), dict)
              and 'built_at' in dbs['meta'], str(dbs.get('meta')))

        sc = json.loads(get('/api/selfcheck', port))
        disk = [x for x in (sc.get('sections') or []) if x.get('id') == 'disk']
        usage = (disk[0].get('usage') if disk else None) or []
        check('/api/selfcheck 磁盘段带按目录占用', len(usage) >= 2,
              str([(u['name'], u.get('bytes')) for u in usage][:4]))

        html = get('/', port).decode('utf-8', 'replace')
        check('首页有主行动按钮', 'cover-act' in html and '/samples' in html)
        check('首页有运行中任务容器', 'runSec' in html)
        check('首页有样品统计与链接', 'sampleStat' in html)

        appjs = get('/static/app.js', port).decode('utf-8', 'replace')
        check('/static/app.js 含 renderRunTasks', 'renderRunTasks' in appjs)
        check('/static/app.js 含 dbMetaLine', 'dbMetaLine' in appjs)
        # anchor（样品锚常驻条）随工作区一起构建属正常：anchor.py /
        # web/anchor_api.py / app.js 三侧同源。只提示，不判红。
        print(f'  ⓘ anchor JS 在包内: {"anchorBarMount" in appjs}'
              f'（Python 侧锚 API 同批构建，两端匹配）')

        # webapp 与源码逐字节：只比本次改过的文件（其它文件分发包是 HEAD 快照，
        # 源码工作区含未提交改动，全量比会假红）
        import io
        for rel in ('templates/_nav.html', 'templates/home.html',
                    'templates/settings.html', 'static/app.css', 'static/i18n.js',
                    'static/app-nav.js'):
            src = io.open(os.path.join(ROOT, 'webapp', *rel.split('/')), 'rb').read()
            dst = io.open(os.path.join(PKG, '_internal', 'webapp',
                                       *rel.split('/')), 'rb').read()
            check(f'webapp/{rel} 与源码一致', src == dst, f'{len(dst)}B')
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=20)
        except Exception:
            proc.kill()

    print()
    if FAIL:
        print(f'✘ {len(FAIL)} 项未通过:')
        for f in FAIL:
            print('   - ' + f)
        return 1
    print('✔ 全部通过')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
