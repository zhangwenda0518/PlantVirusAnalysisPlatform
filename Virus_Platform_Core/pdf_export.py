# -*- coding: utf-8 -*-
"""HTML → PDF：零依赖，借本机 Chrome/Edge 的 headless 打印能力。

为什么不引 weasyprint / reportlab：平台的报告是**自包含 HTML + 内联 SVG/PNG**
（`report_v2.html` 动辄 2–3 MB，大半是图）。换渲染器等于换一套排版引擎，
版式会走样；headless 浏览器打印的是同一份 DOM，视觉与网页逐像素一致，
且不增加任何打包体积。

实测约束（2026-11-05，本机 Edge 155）：

- Edge 的 `--headless` 会把命令转发给已在运行的浏览器实例，**既不输出
  DOM 也不落 PDF，还返回 exit 0**；Chrome 同参数正常（2.3 MB 报告 →
  3.5 MB PDF，3.6 s）。
- 所以候选浏览器逐个尝试，**以「产出非空 PDF」为唯一成功判据**，
  不看进程退出码。

对外只暴露两个函数：

    html_to_pdf(html_path, pdf_path=None, timeout=180) -> str
    find_browser() -> str | None

CLI：
    python -m Virus_Platform_Core.pdf_export <report.html> [-o out.pdf]
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

__all__ = ['html_to_pdf', 'find_browser']

#: 小于这个字节数视为「没真正打印」（空文件或只有一张空白页）
_MIN_PDF = 1024


def _win_bases() -> list[str]:
    out = []
    for env in ('PROGRAMFILES', 'PROGRAMFILES(X86)', 'LOCALAPPDATA'):
        v = os.environ.get(env)
        if v:
            out.append(v)
    return out


def _candidates() -> list[str]:
    """候选浏览器，按「本机实测可用性」排序。

    环境变量 `VP_PDF_BROWSER` 置顶，便于用户指向便携版 Chromium。
    """
    seen, out = set(), []

    def add(p):
        if p and os.path.isfile(p):
            key = os.path.normcase(os.path.abspath(p))
            if key not in seen:
                seen.add(key)
                out.append(p)

    add(os.environ.get('VP_PDF_BROWSER'))
    rels = (
        r'Google\Chrome\Application\chrome.exe',
        r'Microsoft\Edge\Application\msedge.exe',
        r'Chromium\Application\chrome.exe',
    )
    for base in _win_bases():
        for rel in rels:
            add(os.path.join(base, rel))
    for name in ('chrome', 'chromium', 'msedge', 'google-chrome',
                 'chrome.exe', 'msedge.exe'):
        add(shutil.which(name))
    return out


def find_browser() -> str | None:
    """第一个存在的候选（不代表一定能打印，见模块注释）。"""
    c = _candidates()
    return c[0] if c else None


def _print_once(exe: str, url: str, dst: Path, timeout: int) -> subprocess.CompletedProcess:
    # 独立 profile：Chrome 复用已运行实例时会丢弃命令行开关，必须隔离
    prof = tempfile.mkdtemp(prefix='vp_pdf_')
    try:
        cmd = [
            exe,
            '--headless=new',
            '--disable-gpu',
            '--no-first-run',
            '--no-default-browser-check',
            '--no-pdf-header-footer',        # 新版开关：去掉页眉页脚
            '--print-to-pdf-no-header',      # 旧版同名开关，多余参数会被忽略
            '--user-data-dir=' + prof,
            '--print-to-pdf=' + str(dst),
            url,
        ]
        return subprocess.run(
            cmd, capture_output=True, timeout=timeout,
            text=True, encoding='utf-8', errors='replace')
    finally:
        shutil.rmtree(prof, ignore_errors=True)


def _pdf_ok(path) -> bool:
    """产出是否算「真正打印成功」：存在且不小于最小体积。"""
    try:
        return os.path.isfile(path) and os.path.getsize(path) >= _MIN_PDF
    except OSError:
        return False


def _remove_quiet(path) -> None:
    """尽力清理半成品；删不掉（被占用/无权限）不抛异常。"""
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


def html_to_pdf(html_path, pdf_path=None, timeout: int = 180) -> str:
    """把本地 HTML 打印成 PDF，返回 PDF 绝对路径。

    失败时抛 RuntimeError，消息里带每个候选浏览器的实际表现（原文转述）。

    写法：浏览器只往 `dst.tmp` 打，达标后才 os.replace 覆盖目标——
    失败不会毁掉上一份好 PDF，目标被阅读器占用时也走友好报错而不是
    PermissionError 直接穿透。
    """
    src = Path(html_path).expanduser().resolve()
    if not src.is_file():
        raise FileNotFoundError(f'HTML 不存在：{src}')
    dst = Path(pdf_path).expanduser().resolve() if pdf_path \
        else src.with_suffix('.pdf')
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + '.tmp')
    _remove_quiet(str(tmp))              # 上一轮残留的半成品不能当成本次成功

    url = src.as_uri()
    tried = []
    env_exe = os.environ.get('VP_PDF_BROWSER')
    if env_exe and not os.path.isfile(env_exe):
        tried.append(f'VP_PDF_BROWSER={env_exe}: 不是可执行文件')

    for exe in _candidates():
        _remove_quiet(str(tmp))
        try:
            p = _print_once(exe, url, tmp, timeout)
        except subprocess.TimeoutExpired:
            # 超时前浏览器可能已经把完整 PDF 写出来了：存在且达标即成功，
            # 不能只因为进程被 kill 就把好文件当失败。
            if _pdf_ok(str(tmp)):
                try:
                    os.replace(str(tmp), str(dst))
                except OSError as e:
                    tried.append(f'{exe}: 目标文件被占用（'
                                 f'{type(e).__name__}: {e}）')
                    _remove_quiet(str(tmp))
                    continue
                return str(dst)
            tried.append(f'{exe}: 超时（>{timeout}s）')
            _remove_quiet(str(tmp))
            continue
        except Exception as e:                      # 启动失败（权限/被杀）
            tried.append(f'{exe}: {type(e).__name__}: {e}')
            _remove_quiet(str(tmp))
            continue
        if _pdf_ok(str(tmp)):
            try:
                os.replace(str(tmp), str(dst))
            except OSError as e:
                tried.append(f'{exe}: 目标文件被占用（'
                             f'{type(e).__name__}: {e}）')
                _remove_quiet(str(tmp))
                continue
            return str(dst)
        err = (p.stderr or '').strip().splitlines()
        tail = err[-1] if err else f'exit={p.returncode}，无输出文件'
        tried.append(f'{exe}: {tail}')
        _remove_quiet(str(tmp))

    _remove_quiet(str(tmp))               # 候选全败：不留半成品
    detail = '\n  '.join(tried) if tried else '未找到 chrome.exe / msedge.exe'
    raise RuntimeError(
        '没有可用的无头浏览器（已尝试）：\n  ' + detail
        + '\n可用环境变量 VP_PDF_BROWSER 指向 Chrome/Chromium 可执行文件。')


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ('-h', '--help'):
        print(__doc__)
        return 0
    src = argv[0]
    out = None
    if '-o' in argv:
        i = argv.index('-o')
        if i + 1 < len(argv):
            out = argv[i + 1]
    try:
        pdf = html_to_pdf(src, out)
    except Exception as e:
        print(f'[失败] {type(e).__name__}: {e}', file=sys.stderr)
        return 1
    print(pdf)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
