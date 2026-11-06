# -*- coding: utf-8 -*-
"""程序化生成 UI 教程 GIF（Playwright 截帧 + Pillow 拼装）。

第一条样片：工具卡「✨ 示例 → ▶ 运行 → 📤 结果」完整闭环（t-convert 卡）。
复用方式：在 SCENARIOS 里加一个新场景函数（入参 page + shot()，shot 负责
截帧与定帧时长），装配与拼装逻辑全部共用。

用法：
    python dev_tools/make_tutorial_gif.py                     # 默认场景 tool-loop
    python dev_tools/make_tutorial_gif.py --width 1000        # 调整 GIF 宽度
    python dev_tools/make_tutorial_gif.py --keep-frames       # 保留中间 PNG 便于调帧

依赖：playwright（本机已装；chromium 不可用时自动回落 msedge 通道）、Pillow。
产物：wiki/assets/gif/<name>.gif（dev_tools/sync_wiki.py 会连子目录一起同步）。
"""
import argparse
import os
import shutil
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PIL import Image  # noqa: E402

FRAME_DIR = os.path.join(ROOT, 'run', '_gif_frames')   # 中间 PNG（默认用完即删）
DEFAULT_OUT = os.path.join(ROOT, 'wiki', 'assets', 'gif', 'tool-run-demo.gif')
BASE = 'http://127.0.0.1:8765'


# ---------- 场景：每个场景一个函数（page, shot）-> None ----------

def sc_tool_loop(page, shot):
    """工具卡闭环：打开 t-convert 卡 → 清空输入 → ✨ 示例 → ▶ 运行 → 结果出现。"""
    page.goto(BASE + '/tools#t-convert', wait_until='load', timeout=30000)
    page.wait_for_selector('#t-convert', state='visible', timeout=15000)
    time.sleep(1.2)                       # 等页面自己的 autoFill(400ms) 先落完
    # 画面清理（只作用于本次录制浏览器会话，不改任何持久设置）：
    # ① 点掉「磁盘空间不足」横幅（写的是录制会话自己的 localStorage）；
    # ② 清掉「上次运行」历史恢复残留与旧输出显示，让教学帧从干净卡开始。
    try:
        page.click('button:has-text("知道了")', timeout=3000)
        time.sleep(0.3)
    except Exception:
        pass                              # 没有横幅就算了
    page.evaluate(
        "() => ['toolrun-convert','rh-convert'].forEach("
        "id => { const el = document.getElementById(id); if (el) el.innerHTML=''; })")
    # 演示从「空表单」开始：清掉 autoFill 填的值（仅本浏览器会话，无副作用）
    page.evaluate(
        "() => ['cv_input','cv_input2','cv_outdir'].forEach("
        "id => { const el = document.getElementById(id); if (el) el.value=''; })")
    shot('01-empty-form', 1500)           # 帧1：空表单

    page.click('#t-convert h2 .vp-ex-btn', timeout=8000)   # ✨ 示例
    time.sleep(0.8)                       # toast「已填入示例数据」可见
    shot('02-example-filled', 1600)       # 帧2：示例已填充（带 toast）

    page.click('#t-convert button.btn.primary', timeout=8000)  # ▶ 运行转换
    time.sleep(1.5)                       # 任务已提交，运行日志开始滚动
    shot('03-running', 1600)              # 帧3：运行中（徽章+实时日志）

    # 轻量工具秒级完成；等输出区出现任务结果卡
    page.wait_for_selector('#toolrun-convert .task-result', timeout=120000)
    time.sleep(0.6)
    page.evaluate(
        "() => document.getElementById('toolrun-convert')"
        ".scrollIntoView({block:'center'})")
    time.sleep(0.4)
    shot('04-result', 2200)               # 帧4：完成态（chips+日志+下载）

    page.evaluate(
        "() => document.getElementById('t-convert')"
        ".scrollIntoView({block:'start'})")
    time.sleep(0.5)
    shot('05-overview', 1500)             # 帧5：整卡总览（结果在卡内可见）


SCENARIOS = {
    'tool-loop': (sc_tool_loop, DEFAULT_OUT),
}


# ---------- 装配 ----------

def build_gif(pngs, durations, out_path, width):
    frames = []
    for p in pngs:
        im = Image.open(p)
        if im.width != width:
            h = round(im.height * width / im.width)
            im = im.resize((width, h), Image.LANCZOS)
        frames.append(im.convert('RGB'))
    durations = [max(200, d) for d in durations]
    # 逐帧自适应 256 色量化，压体积；全帧替换式播放
    frames = [f.quantize(colors=256, method=Image.MEDIANCUT) for f in frames]
    frames[0].save(out_path, save_all=True, append_images=frames[1:],
                   duration=durations, loop=0, optimize=True)
    return durations


def main():
    global BASE
    ap = argparse.ArgumentParser(
        description='程序化生成 UI 教程 GIF（Playwright 截帧 + Pillow 拼装）')
    ap.add_argument('--base', default=BASE,
                    help='平台地址（默认 http://127.0.0.1:8765）')
    ap.add_argument('--scenario', default='tool-loop', choices=sorted(SCENARIOS))
    ap.add_argument('--out', default=None, help='输出 GIF 路径（默认按场景）')
    ap.add_argument('--width', type=int, default=900, help='GIF 宽度（默认 900px）')
    ap.add_argument('--keep-frames', action='store_true',
                    help='保留中间 PNG 于 run/_gif_frames/ 便于调帧')
    args = ap.parse_args()
    BASE = args.base.rstrip('/')

    from playwright.sync_api import sync_playwright
    if os.path.isdir(FRAME_DIR):
        shutil.rmtree(FRAME_DIR)
    os.makedirs(FRAME_DIR, exist_ok=True)

    shots = []                            # [(png_path, duration_ms)]

    def shot(name, duration_ms=1200):
        png = os.path.join(FRAME_DIR, name + '.png')
        page.screenshot(path=png)
        shots.append((png, duration_ms))
        print('  shot %s (%dms)' % (name, duration_ms))
        return png

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 1280, 'height': 860})
        fn, default_out = SCENARIOS[args.scenario]
        try:
            fn(page, shot)
        finally:
            browser.close()

    out = args.out or default_out
    os.makedirs(os.path.dirname(out), exist_ok=True)
    durations = build_gif([p for p, _ in shots], [d for _, d in shots],
                          out, args.width)
    if not args.keep_frames:
        shutil.rmtree(FRAME_DIR)
    kb = os.path.getsize(out) / 1024
    total = sum(durations) / 1000
    print('GIF 完成：%s' % out)
    print('  帧数 %d ｜ 总时长 %.1fs ｜ 体积 %.0fKB ｜ 每帧时长 %s'
          % (len(shots), total, kb, durations))


if __name__ == '__main__':
    main()
