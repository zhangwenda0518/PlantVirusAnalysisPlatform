# -*- coding: utf-8 -*-
"""「进化树 + 基因组叠加」接线巡检（2026-09-17）。

被巡的对象是**接线**，不是补丁本身：Archaeopteryx.js 里的自研补丁
（Comparative genome overlay：属色带 / 每叶基因轨道 / 同源连线 ribbon）
一直在 vendor 里，但平台从来没有把 overlay 数据传给查看器 —— 本轮把它接上
（树文件同名 `<stem>.overlay.json` 由后端按名带上，前端 renderTreeTo 透传）。

  [1] 后端：/api/tree/file 读 examples/example_synteny_tree.nwk → 带 overlay
      （3 轨道 / 每轨 6 基因 / 18 条同源连线），叶名与轨道名对齐；
  [2] 负控（后端，证明 [1] 不是恒真）：无同名 JSON / JSON 损坏 / 非 dict /
      空 tracks / 超限 → overlay 为 null，且**树本身照常返回**；
  [3] 真浏览器：③ 树查看点「✨ 示例·基因组叠加」→ 属色带 / 基因框 /
      同源连线真的画进 SVG（数元素），tvMeta 带叠加摘要；
  [4] 真浏览器负控：点「✨ 示例」（纯树）→ 三类叠加元素全为 0（证明 [3] 的
      计数断言会红，也证明换树时旧叠加被清干净）；
  [5] 新增 i18n 键 zh / en 双份，且模板确实引用；
  [6] 无 pageerror。

用法：python tests/_check_tree_overlay.py            （自起临时实例）
      python tests/_check_tree_overlay.py --port N   （用已在运行的平台）
无 playwright 时 [3][4][6] SKIP（[1][2][5] 仍跑，退出码照旧）。
"""
import argparse
import io
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

try:
    from playwright.sync_api import sync_playwright
except ImportError:                     # pragma: no cover - 取决于本机环境
    sync_playwright = None

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
SHOT = os.path.join(ROOT, 'run', '_check_tree_overlay.png')
FIX = os.path.join(ROOT, 'run', '_check_tree_overlay_fix')
EX_NWK = 'examples/example_synteny_tree.nwk'
EX_OVL = 'examples/example_synteny_tree.overlay.json'
PLAIN_NWK = 'examples/example_tree.nwk'
WANT_TRACKS, WANT_LINKS = 3, 18
WANT_KEYS = ['tk.fillExampleOverlay', 'tk.fillExampleOverlayTitle']

FAILS = []


def check(cond, msg):
    print(('  ok   ' if cond else '  FAIL ') + msg, flush=True)
    if not cond:
        FAILS.append(msg)


def get(port, path):
    """GET → (status, 解析后的 JSON 或原文)。"""
    url = f'http://127.0.0.1:{port}{path}'
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            body = r.read().decode('utf-8', 'replace')
            status = r.status
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode('utf-8', 'replace')
    try:
        return status, json.loads(body)
    except ValueError:
        return status, body


def free_port(start=8901):
    for p in range(start, start + 40):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            # connect 探测先行（2026-11-05）：Windows 上 0.0.0.0 已被别的
            # 进程绑定的端口，bind('127.0.0.1') 探测会误判空闲——起上去后
            # 请求被 wildcard 持有者截走，测试打到别人的服务上（教训：用户
            # 常驻的 docs http.server 恰占 8901+ 段，套件实例全 404）。
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as c:
                if c.connect_ex(('127.0.0.1', p)) == 0:
                    continue
            try:
                s.bind(('127.0.0.1', p))
                return p
            except OSError:
                continue
    raise RuntimeError('没有可用端口')


def start_server(port):
    logpath = os.path.join(tempfile.gettempdir(), f'vp_tree_overlay_{port}.log')
    logf = io.open(logpath, 'w', encoding='utf-8', errors='replace')
    env = dict(os.environ, VP_NO_RECOVER='1')
    proc = subprocess.Popen(
        [sys.executable, '-c',
         'import sys\n'
         f'sys.path.insert(0, {ROOT!r})\n'
         'from werkzeug.serving import make_server\n'
         'import app\n'
         f"s = make_server('127.0.0.1', {port}, app.app, threaded=True)\n"
         "print('ready', flush=True)\n"
         's.serve_forever()'],
        cwd=ROOT, env=env, stdout=logf, stderr=subprocess.STDOUT)
    for _ in range(120):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(('127.0.0.1', port)) == 0:
                return proc, logpath
        if proc.poll() is not None:
            break
        time.sleep(0.3)
    proc.kill()
    print('服务启动失败，日志:', logpath)
    return None, logpath


def write_fixtures():
    """负控夹具：真树 + 各种坏 overlay，全部落在 run/ 下（不碰仓库既有文件）。"""
    os.makedirs(FIX, exist_ok=True)
    src = os.path.join(ROOT, PLAIN_NWK)
    with io.open(src, encoding='utf-8') as f:
        nwk = f.read()
    out = {}
    cases = {
        'none': None,                                        # 无同名 JSON
        'broken': '{ this is not json',                      # 损坏
        'list': '[1, 2, 3]',                                 # 非 dict
        'notracks': '{"links": [{"q": "a", "t": "b"}]}',     # 空 tracks
        'huge': None,                                        # 超限（下面写）
    }
    for name, body in cases.items():
        p = os.path.join(FIX, f'{name}.nwk')
        with io.open(p, 'w', encoding='utf-8', newline='') as f:
            f.write(nwk)
        if name == 'huge':
            with io.open(os.path.join(FIX, 'huge.overlay.json'), 'w',
                         encoding='utf-8', newline='') as f:
                f.write('x' * (17 * 1024 * 1024))
        elif body is not None:
            with io.open(os.path.join(FIX, f'{name}.overlay.json'), 'w',
                         encoding='utf-8', newline='') as f:
                f.write(body)
        out[name] = p
    return out


def phase_backend(port):
    print('\n[1] 后端：示例树带 overlay')
    st, d = get(port, f'/api/tree/file?path={urllib.parse.quote(EX_NWK)}')
    check(st == 200 and isinstance(d, dict), f'/api/tree/file 200（{st}）')
    if not isinstance(d, dict):
        return
    check(bool(d.get('newick')), 'newick 非空')
    ov = d.get('overlay')
    check(isinstance(ov, dict), f'overlay 是对象（{type(ov).__name__}）')
    if not isinstance(ov, dict):
        return
    tracks, links = ov.get('tracks') or [], ov.get('links') or []
    check(len(tracks) == WANT_TRACKS,
          f'{len(tracks)} 条基因组轨道（期望 {WANT_TRACKS}）')
    check(len(links) == WANT_LINKS,
          f'{len(links)} 条同源连线（期望 {WANT_LINKS}）')
    n_gene = sum(len(t.get('genes') or []) for t in tracks)
    check(n_gene == WANT_TRACKS * 6, f'{n_gene} 个基因（期望 18）')
    check(all(t.get('genus') for t in tracks), '每条轨道都有属名（供属色带）')
    check(all(any(g.get('cid') for g in (t.get('genes') or [])) for t in tracks),
          '基因带 cid（供同源连线两端引用）')
    # 夹具自洽性：CDS 必须落在轨道长度内、且互不重叠 —— 两者都曾是这套示例的
    # 真实缺陷（第 6 个 CDS 收在 3490 而记录只有 3300bp；相邻 CDS 重叠 340bp）。
    oor = [(t.get('name'), g.get('gene') or g.get('id'), g.get('end'))
           for t in tracks for g in (t.get('genes') or [])
           if not (1 <= g.get('start', 0) < g.get('end', 0) <= t.get('length', 0))]
    check(not oor, f'每个 CDS 都落在轨道长度内（越界 {len(oor)} 个: {oor[:3]}）')
    bad_ov = []
    for t in tracks:
        gs = sorted(t.get('genes') or [], key=lambda g: g.get('start', 0))
        bad_ov += [(t.get('name'), a.get('id'), b.get('id'))
                   for a, b in zip(gs, gs[1:]) if b.get('start', 0) <= a.get('end', 0)]
    check(not bad_ov, f'同轨 CDS 互不重叠（重叠 {len(bad_ov)} 对: {bad_ov[:3]}）')
    tips = re.findall(r'[(,]([^(),:]+):', d.get('newick') or '')
    tnames = {str(t.get('name')) for t in tracks}
    check(set(tips) == tnames, f'叶名与轨道名一致（叶 {sorted(tips)}）')
    idents = [l.get('ident') for l in links if l.get('ident') is not None]
    check(len(idents) == len(links) and all(0 <= i <= 100 for i in idents),
          f'{len(idents)} 条连线都带 0-100 的蛋白同一性（'
          f'{min(idents, default=0):.1f}%~{max(idents, default=0):.1f}%）')

    print('\n[1b] 对照：纯树示例（无同名 JSON）不该凭空长出叠加')
    st2, d2 = get(port, f'/api/tree/file?path={urllib.parse.quote(PLAIN_NWK)}')
    check(st2 == 200 and isinstance(d2, dict) and bool(d2.get('newick')),
          '纯树示例仍可读')
    check(isinstance(d2, dict) and d2.get('overlay') is None,
          '纯树示例 overlay 为 null（不是编造的假叠加）')


def phase_backend_negative(port):
    print('\n[2] 负控（后端）：坏 overlay 一律降级为 null，树照常返回')
    fx = write_fixtures()
    for name, why in [('none', '无同名 JSON'), ('broken', 'JSON 损坏'),
                      ('list', 'JSON 是数组'), ('notracks', 'tracks 为空'),
                      ('huge', '文件 17MB 超 16MB 上限')]:
        st, d = get(port, f'/api/tree/file?path={urllib.parse.quote(fx[name])}')
        ok_tree = st == 200 and isinstance(d, dict) and bool(d.get('newick'))
        check(ok_tree, f'{why}：树仍返回（{st}）')
        check(isinstance(d, dict) and d.get('overlay') is None,
              f'{why}：overlay 降级为 null')


def phase_static():
    print('\n[5] i18n 键与模板引用')
    p = os.path.join(ROOT, 'webapp', 'static', 'i18n.js')
    src = io.open(p, encoding='utf-8').read()
    for k in WANT_KEYS:
        n = src.count(f"'{k}'")
        check(n == 2, f'{k} zh/en 各一份（实际 {n}）')
    tpl = io.open(os.path.join(ROOT, 'webapp', 'templates', 'tools.html'),
                  encoding='utf-8').read()
    check('EXAMPLE_SYNTENY_TREE_NWK' in tpl, '③ 树查看用了示例常量')
    check('data-i18n-title="tk.fillExampleOverlayTitle"' in tpl,
          '新示例按钮挂了 i18n title')
    js = io.open(os.path.join(ROOT, 'webapp', 'static', 'app.js'),
                 encoding='utf-8').read()
    check('EXAMPLE_SYNTENY_TREE_NWK' in js, '示例常量定义在 app.js')
    cmp_js = io.open(os.path.join(ROOT, 'webapp', 'static', 'app-compare.js'),
                     encoding='utf-8').read()
    check(cmp_js.count('genomeOverlay') >= 3,
          f'renderTreeTo / 三处入口都透传 genomeOverlay（{cmp_js.count("genomeOverlay")}）')


def phase_browser(port):
    if sync_playwright is None:
        print('\n[3][4][6] [SKIP] 未安装 playwright，跳过真浏览器验证')
        print('       安装: python -m pip install -r requirements-dev.txt')
        return
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True,
                           args=['--no-proxy-server'])
        pg = b.new_page(viewport={'width': 1700, 'height': 1200})
        errs = []
        pg.on('pageerror', lambda e: errs.append(str(e)))
        pg.goto(f'http://127.0.0.1:{port}/tools?g=compare',
                wait_until='domcontentloaded')
        pg.evaluate("try{localStorage.setItem('vp_example_seen','1')}catch(e){}")
        pg.reload(wait_until='domcontentloaded')
        pg.wait_for_selector('#groupLanding', timeout=30000)
        pg.evaluate("document.querySelectorAll('.dlgmask').forEach(m => m.remove())")
        pg.locator('#groupLanding .landing-item[data-item="t-treebuild"]').click()
        pg.wait_for_selector('#t-treebuild', state='visible', timeout=15000)

        btn = '#t-treebuild button[data-i18n-title="tk.fillExampleOverlayTitle"]'
        plain = '#t-treebuild button[data-i18n-title="tk.fillExampleTreeTitle"]'
        check(pg.locator(btn).count() == 1, '「✨ 示例·基因组叠加」按钮在页上（一枚）')
        check(pg.locator(plain).count() == 1, '「✨ 示例」（纯树）按钮仍在')

        print('\n[3] 真浏览器：点示例 → 叠加真的画进 SVG')
        pg.locator(btn).click()
        try:
            pg.wait_for_selector('#tvBox g.aptx-genome-links polygon',
                                 timeout=30000)
        except Exception as e:
            check(False, f'叠加未出现（{str(e)[:70]}）；tvMeta='
                         + pg.inner_text('#tvMeta')[:90])
        bands = pg.locator('#tvBox g.aptx-genus-bands rect').count()
        # 基因框：宽度 ≥7px 时画成带方向的箭头 polygon，窄的才退化成 rect
        genes = pg.locator('#tvBox g.aptx-genome polygon').count()
        nrect = pg.locator('#tvBox g.aptx-genome rect').count()
        links = pg.locator('#tvBox g.aptx-genome-links polygon').count()
        axis = pg.locator('#tvBox g.aptx-genome-axis').count()
        legend = pg.locator('#tvBox g.aptx-genome-legend rect').count()
        check(bands == WANT_TRACKS, f'属色带 {bands} 条（期望 {WANT_TRACKS}）')
        check(genes == WANT_TRACKS * 6,
              f'基因框 {genes} 个（期望 18；窄框退化 rect 另计 {nrect} 个）')
        check(links == WANT_LINKS, f'同源连线 {links} 条（期望 {WANT_LINKS}）')
        check(axis == 1, f'坐标标尺 {axis} 条')
        check(legend == 1, '同一性色标图例 1 条（0-100% 渐变色条）')
        meta = pg.inner_text('#tvMeta')
        check('基因组叠加' in meta and f'{WANT_LINKS} 条同源连线' in meta,
              f'tvMeta 带叠加摘要：{meta[:110]}')
        # 面板里的叠加开关（补丁自带）
        check(pg.locator('#tvBox #genometracks_cb').count() == 1,
              '面板出现「Genome Tracks」开关')
        check(pg.locator('#tvBox #homologyident_cb').count() == 1,
              '面板出现「Identity %」开关（clinker 式同一性标签）')

        print('\n[4] 真浏览器负控：换纯树示例 → 叠加元素必须清零')
        pg.locator(plain).click()
        ok = False
        for _ in range(40):
            if '个序列' in pg.inner_text('#tvMeta'):
                ok = True
                break
            pg.wait_for_timeout(500)
        check(ok, '纯树示例已渲染（tvMeta 出现「N 个序列」）')
        pg.wait_for_timeout(500)
        for sel, name in [('#tvBox g.aptx-genus-bands rect', '属色带'),
                          ('#tvBox g.aptx-genome polygon', '基因框'),
                          ('#tvBox g.aptx-genome-legend rect', '同一性色标'),
                          ('#tvBox g.aptx-genome-links polygon', '同源连线')]:
            n = pg.locator(sel).count()
            check(n == 0, f'负控：纯树时{name}为 0（实际 {n}）')
        check('基因组叠加' not in pg.inner_text('#tvMeta'),
              '负控：纯树时 tvMeta 不提叠加')

        print('\n[6] 无 JS 报错')
        check(not errs, f'无 pageerror（实际 {errs[:2]}）')

        os.makedirs(os.path.dirname(SHOT), exist_ok=True)
        pg.screenshot(path=SHOT, full_page=False)
        b.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=0)
    ap.add_argument('--keep', action='store_true')
    args = ap.parse_args()

    proc, logpath = None, ''
    port = args.port or free_port()
    if not args.port:
        proc, logpath = start_server(port)
        if proc is None:
            return 2
        print(f'临时实例: http://127.0.0.1:{port}  (服务日志 {logpath})')

    try:
        phase_backend(port)
        phase_backend_negative(port)
        phase_static()
        phase_browser(port)
    finally:
        if proc is not None and not args.keep:
            proc.kill()
            proc.wait(timeout=10)

    print('\n' + '=' * 52)
    if FAILS:
        print(f'FAILED: {len(FAILS)} 项')
        for f in FAILS:
            print('  - ' + f)
        return 1
    print('「进化树 + 基因组叠加」接线检查通过 ✔  截图:', os.path.relpath(SHOT, ROOT))
    return 0


if __name__ == '__main__':
    sys.exit(main())
