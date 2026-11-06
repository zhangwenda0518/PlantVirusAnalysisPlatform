# -*- coding: utf-8 -*-
"""t-phylodyn「时间与地理推断·本地全链」**任务层 + 真浏览器**端到端验证。

为什么必须有这一层：模块级测试全绿 ≠ 卡片接线对了。这层能一次抓住
ctx 参数名错、注册名错、产物路径错、A/B 侧口径没搬全 四类问题
（skill `add-platform-tool-card` 的硬要求）。

  [1] 起临时实例（本进程内 make_server），POST /api/tool/run 真跑一条
      `mode=pipeline` 轻量全链（prep → clock → phylogeo → report），
      输入用平台自带示例 examples/example_phylogeo.fasta + .meta.csv + example_treedater.nwk；
  [2] 轮询到 done，读 `phylodyn_summary.json`：4 个阶段 ran & ok、
      report 出产物清单、clock 时间树落盘；
  [3] 真浏览器：进卡断言阶段流 #pdFlow 画出 6 格（A0–A5）；
      再 `loadPhylodynResult('<run>')`，读 `#pdTimeTree` 的 Plotly `_fullData`
      断言**真的画了**（不是"元素存在"）、x 轴为日历年；
      并断言 A2 迁移弧线地图 `#pdGeoArcPlot` 真的画出 scattergeo 图元与
      至少一条弧线，且说明行写明用的是 TreeTime-ML 一列（ml/fitch 不可相加）；
  [4] 收尾关实例。

⚠️ 未登记进 `_run_all.py`：要真跑一条流水线 + 开浏览器，约 1–3 分钟。

用法: python tests/_check_phylodyn_e2e.py
"""
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

try:
    from playwright.sync_api import sync_playwright
except ImportError:                                     # pragma: no cover
    sync_playwright = None

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from Virus_Platform_Core.config import DIRS  # noqa: E402

FASTA = os.path.join(ROOT, 'examples', 'example_phylogeo.fasta')
META = os.path.join(ROOT, 'examples', 'example_phylogeo.meta.csv')
TREE = os.path.join(ROOT, 'examples', 'example_treedater.nwk')

PASS, FAIL = [], []


def check(ok, msg):
    (PASS if ok else FAIL).append(msg)
    print(('  ok   ' if ok else '  FAIL ') + msg, flush=True)


def free_port(start=8881):
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
    logpath = os.path.join(tempfile.gettempdir(), f'vp_phylodyn_e2e_{port}.log')
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
    for _ in range(200):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(('127.0.0.1', port)) == 0:
                return proc, logpath
        if proc.poll() is not None:
            break
        time.sleep(0.3)
    proc.kill()
    print('服务启动失败，日志:', logpath)
    return None, logpath


def post_json(port, path, payload, timeout=120):
    req = urllib.request.Request(
        f'http://127.0.0.1:{port}{path}',
        data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        raw = e.read().decode('utf-8', 'replace')
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, {'error': raw[:300]}
    except Exception as e:                                          # noqa: BLE001
        return 0, {'error': str(e)}


def get_json(port, path, timeout=120):
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}{path}',
                                    timeout=timeout) as r:
            return r.status, json.loads(r.read().decode('utf-8'))
    except Exception:                                               # noqa: BLE001
        return 0, {}


PARAMS = {
    'mode': 'pipeline', 'input': FASTA, 'meta': META,
    # ⚠️ 这里**不给 tree**：A0 会现场建树。给错树会静默错配叶名
    # （实测把 example_treedater.nwk 配 example_phylogeo.fasta → TreeTime 报
    #  "NO SEQUENCE FOR LEAF"，最后 report 阶段 ok=False）。
    'date_col': 'year', 'trait': 'region',
    'methods': 'treetime', 'reroot': 'least-squares', 'keep_root': 'off',
    'relax_sigma': '0', 'drt': 'on', 'drt_perm': '200',
    'ltt': 'on',
    'skyline': 'off', 'gen_per_year': '50', 'ancestral': 'off',
    'homoplasy': 'off', 'sampling_bias_correction': 'auto', 'cross_check': 'off',
    'seed': '0', 'prune': 'off', 'prune_method': 'fps',
    'prune_date_resolution': 'month', 'prune_max_reps': '3',
    'prune_min_snp_diff': '2', 'prune_clade_cutoff': '5',
}


def main():
    for f in (FASTA, META, TREE):
        if not os.path.isfile(f):
            print(f'✘ 缺示例文件 {f}（应由 run/_zz_port_apply.py 拷入）')
            return 2

    port = free_port()
    proc, logpath = start_server(port)
    if proc is None:
        return 2
    print(f'临时实例: http://127.0.0.1:{port}  (日志 {logpath})')
    run = None
    try:
        print('\n[1] 任务层真跑：POST /api/tool/run（phylodyn · pipeline）')
        st, body = post_json(port, '/api/tool/run',
                             {'tool': 'phylodyn', 'threads': 4, 'params': PARAMS})
        check(st == 200, f'提交返回 200（实际 {st} {str(body)[:110]}）')
        if st != 200:
            return 1
        run, tid = body.get('run'), body.get('task')
        print(f'  run={run} task={tid}')

        print('\n[2] 轮询到完成 …')
        t0, state = time.time(), None
        while time.time() - t0 < 900:
            _, j = get_json(port, f'/api/task/{tid}?log_lines=0')
            state = (j or {}).get('state') or (j or {}).get('status')
            if state in ('done', 'failed', 'error', 'cancelled'):
                break
            time.sleep(3)
        check(state == 'done', f'任务终态 done（实际 {state}，用时 {time.time()-t0:.0f}s）')

        rdir = os.path.join(DIRS['tool_runs'], run)   # 跟随 VP_RUN_ROOT 隔离
        cand = []
        for dp, _dns, fns in os.walk(rdir):
            for fn in fns:
                if fn == 'phylodyn_summary.json':
                    cand.append(os.path.join(dp, fn))
        check(bool(cand), f'产出 phylodyn_summary.json（{cand[:1]}）')
        if not cand:
            return 1
        summ = json.load(io.open(cand[0], encoding='utf-8'))
        check(summ.get('tool') == 'phylodyn', f"summary.tool = {summ.get('tool')}")
        st_map = {s['key']: s for s in summ.get('stages') or []}
        for k in ('prep', 'clock', 'phylogeo', 'report'):
            s = st_map.get(k) or {}
            check(bool(s.get('ran')) and s.get('ok') is not False,
                  f'阶段 {k} 已跑且未报错（ran={s.get("ran")} ok={s.get("ok")} err={s.get("error")}）')
        # ⚠️ `clock.timetree` 是**给前端画图的坐标 dict**（{x:[...], y:[...]}），
        # 不是文件路径。它在 phylogeo 换演进版之前是 None —— 因为
        # `phylodyn_local` 要调 `phylogeo.time_tree_segments`，那是演进版才有的函数
        # （实测 warning 原文："时间树坐标未产出：AttributeError: module
        #  'Virus_Platform_Core.phylogeo' has no attribute 'time_tree_segments'"）。
        tt = (summ.get('clock') or {}).get('timetree')
        n_tt = len((tt or {}).get('x') or []) if isinstance(tt, dict) else 0
        check(isinstance(tt, dict) and n_tt > 0,
              f'clock 时间树坐标已产出（{n_tt} 个点；需演进版 phylogeo）')
        check((summ.get('clock') or {}).get('timetree_source') == 'TreeTime',
              f"timetree_source = {(summ.get('clock') or {}).get('timetree_source')}")

        # LTT 谱系-时间曲线（2026-11-06，对齐 VirPhyKit「TreeDater-LTT」
        # 免 MCMC 口径）：A1 用 clock/timetree.nwk + prep/dates.csv 直接算出，
        # 随 summary 上卡；斜率 ≈ 净多样化率（birth−death 近似）。
        lt = (summ.get('clock') or {}).get('ltt') or {}
        check(lt.get('ok') is True,
              f'clock.ltt ok（ok={lt.get("ok")} err={lt.get("error")}）')
        check(lt.get('axis') == 'calendar_year',
              f'LTT 横轴＝日历年（{lt.get("axis")}）')
        n_curve = len(lt.get('curve') or [])
        check(n_curve >= 2, f'LTT 曲线点 ≥2（{n_curve}）')
        check(bool(lt.get('curve_file_abs'))
              and os.path.isfile(lt.get('curve_file_abs') or ''),
              'clock/ltt_curve.tsv 已落盘')

        # A2 的区域坐标（2026-09-18 新增，只为迁移弧线地图的落点）：
        # 只读样例的元数据/坐标表 → 必须解析出≥2 个区划，否则地图画不出弧线。
        pg_sec = summ.get('phylogeo') or {}
        coords = pg_sec.get('coords') or {}
        csrc = pg_sec.get('coord_src') or {}
        cmiss = pg_sec.get('coord_missing') or []
        check(len(coords) >= 2,
              f'A2 解析出区域坐标（{len(coords)} 个：{sorted(coords)[:5]}）')
        check(set(coords) == set(csrc),
              'coords 与 coord_src 键一致（每个坐标都有出处）')
        # 出处取值必须落在这五档里：多出一个未定义的值，前端会显示"未知来源"，
        # 等于把粗坐标混成准坐标 —— 这里钉死取值范围。
        _SRC_OK = {'samples', 'user', 'builtin', 'user_prefix', 'builtin_prefix'}
        _bad = {k: v for k, v in csrc.items() if v not in _SRC_OK}
        check(not _bad, f'坐标出处取值合法（越界的：{_bad or "无"}）')
        n_prefix = sum(1 for v in csrc.values() if v.endswith('_prefix'))
        print(f'  · 坐标出处分布：{csrc}'
              + (f'（其中按国家前缀兜底 {n_prefix} 个）' if n_prefix else ''))
        if cmiss:
            check(bool(cmiss), f'缺坐标区划显式列出（{cmiss[:5]}）')
        check('coord_regions' in pg_sec, 'summary 带 coord_regions（锚点大小用）')

        if sync_playwright is None:
            print('\n[3] 跳过真浏览器（没装 playwright）')
        else:
            print('\n[3] 真浏览器：阶段流 + 时间树')
            with sync_playwright() as pw:
                b = pw.chromium.launch(headless=True,
                           args=['--no-proxy-server'])
                pg = b.new_page(viewport={'width': 1600, 'height': 1100})
                errs = []
                pg.on('pageerror', lambda e: errs.append(str(e)))
                pg.goto(f'http://127.0.0.1:{port}/tools?g=phylodyn#t-phylodyn',
                        wait_until='domcontentloaded')
                pg.evaluate("try{localStorage.setItem('vp_example_seen','1')}catch(e){}")
                pg.reload(wait_until='domcontentloaded')
                pg.wait_for_selector('#t-phylodyn', state='visible', timeout=30000)
                check(pg.locator('#t-phylodyn').count() == 1, '卡片 t-phylodyn 在页面里')
                for sel in ('#pd_input', '#pd_mode', '#pd_meta', '#toolrun-phylodyn',
                            '#pdFlow', '#pdResult'):
                    check(pg.locator(sel).count() == 1, f'控件存在: {sel}')
                n_pd = pg.eval_on_selector_all('#pdFlow .pds', 'els => els.length')
                check(n_pd == 0, f'加载结果前阶段流是空的（实际 {n_pd}；'
                                 '图纸由 loadPhylodynResult 填）')
                pg.evaluate(f'loadPhylodynResult({run!r})')
                pg.wait_for_timeout(2500)
                vis = pg.eval_on_selector(
                    '#pdResult', "e => getComputedStyle(e).display") != 'none'
                check(vis, '结果面板已展开')
                n_pd = pg.eval_on_selector_all('#pdFlow .pds', 'els => els.length')
                check(n_pd == 6, f'阶段流画出 A0–A5 共 6 格（实际 {n_pd}）')
                nm = pg.eval_on_selector_all(
                    '#pdFlow .pds b', 'els => els.map(e => e.textContent.trim())')
                check(nm == ['A0', 'A1', 'A2', 'A3', 'A4', 'A5'],
                      f'阶段编号顺序正确（{nm}）')
                # ⚠️ 必须断言**内层**画布：drawTimeTree 是先 `box.innerHTML = '<div
                # id="{divId}Plot">'` 再 Plotly.newPlot 到那个内层 div 上，
                # 所以 `_fullData` 在 #pdTimeTreePlot 上，不在 #pdTimeTree 上。
                n_tr = pg.evaluate(
                    "() => { const e = document.querySelector('#pdTimeTreePlot');"
                    " return (e && e._fullData) ? e._fullData.length : 0; }")
                check(n_tr > 0,
                      f'#pdTimeTreePlot 真画出来了（_fullData {n_tr} 条 trace）')
                xs = pg.evaluate(
                    "() => { const e = document.querySelector('#pdTimeTreePlot');"
                    " if (!e || !e._fullData) return []; const s = e._fullData[0];"
                    " return [Math.min(...s.x.filter(v => v != null)),"
                    " Math.max(...s.x.filter(v => v != null))]; }")
                check(len(xs) == 2 and 1900 < xs[0] < 2100 and 1900 < xs[1] < 2100,
                      f'时间树横轴是日历年（{xs}）')

                # LTT 曲线（2026-11-06）：pdRenderLtt 画在内层 #pdLttPlot
                n_ltt = pg.evaluate(
                    "() => { const e = document.querySelector('#pdLttPlot');"
                    " return (e && e._fullData) ? e._fullData.length : 0; }")
                check(n_ltt > 0,
                      f'#pdLttPlot 真画出来了（_fullData {n_ltt} 条 trace）')
                ly = pg.evaluate(
                    "() => { const e = document.querySelector('#pdLttPlot');"
                    " if (!e || !e._fullData) return null;"
                    " const y = e._fullData[0].y || []; return y[y.length - 1]; }")
                check(isinstance(ly, (int, float)) and ly > 0,
                      f'LTT 曲线终点为正谱系数（{ly}）')

                # A2 迁移弧线地图（2026-09-18 新增）：与系统地理卡共用
                # drawPgGeo / _pgArc。断言**图真的画出来了**（读内层
                # #pdGeoArcPlot 的 plotly `_fullData`），不是断言 DOM 存在。
                n_geo = pg.evaluate(
                    "() => { const e = document.querySelector('#pdGeoArcPlot');"
                    " return (e && e._fullData) ? e._fullData.length : 0; }")
                check(n_geo >= 2,
                      f'#pdGeoArcPlot 真画出来了（_fullData {n_geo} 条 trace）')
                arc = pg.evaluate(
                    "() => { const e = document.querySelector('#pdGeoArcPlot');"
                    " if (!e || !e._fullData) return {g: 0, l: 0};"
                    " const geos = e._fullData.filter(x => x.type === 'scattergeo');"
                    " return {g: geos.length,"
                    " l: geos.filter(x => x.mode === 'lines').length}; }")
                check(arc['l'] >= 1,
                      f"至少一条迁移弧线（scattergeo {arc['g']} 个 / 线 {arc['l']} 条）")
                # 线型口径：A2 只画 TreeTime-ML 一列（ml 与 fitch 不是同一个
                # 统计量），未跑 RSPP → 不能出现"支持率过半"的说法。
                gnote = (pg.eval_on_selector('#pdGeoArc', 'e => e.innerText')
                         if pg.locator('#pdGeoArc').count() else '')
                check('TreeTime-ML' in gnote,
                      f'说明行写明弧线用的是 ML 一列（{gnote[:50]}…）')
                check('不可相加' in gnote,
                      '说明行重申 ml/fitch 不可相加（口径红线）')
                check(not errs, f'无 JS 报错（{errs[:2]}）')
                b.close()
    finally:
        if proc is not None:
            proc.kill()
            proc.wait(timeout=10)

    print('\n' + '=' * 52)
    if FAIL:
        print(f'FAILED: {len(FAIL)} 项')
        for m in FAIL:
            print('  - ' + m)
        return 1
    print(f'PHYLODYN E2E PASSED（{len(PASS)} 项）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
