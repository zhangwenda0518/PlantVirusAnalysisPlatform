# -*- coding: utf-8 -*-
"""下载进度/日志显示守卫（2026-11-02）。

用户实测：宿主基因组下载卡里日志只有「[INFO]   [0A[2K」这类 ANSI 控制序列，
看不到速度/已下载/剩余。本用例守住四件事：
  A. 日志清洗：utils.strip_ansi / clean_log_line 去控制序列、取 \\r 末帧、
     TaskLogger 出口丢弃纯控制行；任务日志文件不再写双时间戳。
  B. 下载进度：DownloadWatcher 按落盘大小算 速度/已下载/总量/剩余；
     datasets 包下载从「NN MB valid data package」取总量。
  C. 传参链路：build.py 三个下载入口 → host_genome/datasets_cli 的 prog。
  D. 前端：app-jobs.js cleanLogText 用于所有日志渲染；宿主下载卡显示 msg。
另含运行时行为断言（不联网、不写盘）：清洗结果、格式化、watcher 估算。
"""
import os
import re
import sys
import time
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

FAILS = []
N = [0]


def check(cond, what):
    N[0] += 1
    if cond:
        print(f"  ✔ {what}")
    else:
        print(f"  ✘ {what}")
        FAILS.append(what)


def read(p):
    with open(os.path.join(ROOT, p), encoding='utf-8', errors='replace') as f:
        return f.read()


def main():
    print("A. 日志清洗")
    from Virus_Platform_Core.utils import (
        strip_ansi, clean_log_line, fmt_bytes, fmt_speed, fmt_eta,
        DownloadWatcher, TaskLogger)
    check(clean_log_line('\x1b[0A\x1b[2K') == '', 'ANSI 空行清完为空串')
    check(strip_ansi('ab\x1b[2Kcd') == 'abcd', '行内 CSI 序列被删除')
    check(strip_ansi('x\rDownloading 12%\rDownloading 50%') == 'Downloading 50%',
          '\\r 原地重绘只保留最后一帧')
    check(clean_log_line('  \x1b[0A\x1b[2K  ') == '', '首尾空白 + 控制序列 → 空')
    check(clean_log_line('✓ 数据包: genome.zip (90.6 MB)').startswith('✓'),
          '正常日志行原样保留')

    utils_src = read('Virus_Platform_Core/utils.py')
    check('msg = clean_log_line(msg)' in utils_src,
          'TaskLogger.log 走 clean_log_line')
    check('if not msg:\n            return' in utils_src
          or 'if not msg:' in utils_src,
          '清洗后为空的行不落盘')
    tasks_src = read('Virus_Platform_Core/web/tasks.py')
    check("re.match(r'^\\[\\d{2}:\\d{2}:\\d{2}\\]', line)" in tasks_src,
          '任务日志文件不再叠加第二个时间戳')

    print("B. 进度估算")
    check(fmt_bytes(1024 ** 2) == '1.0 MB', 'fmt_bytes 1MB')
    check(fmt_bytes(int(1.9 * 1024 ** 2)) == '1.9 MB', 'fmt_bytes 1.9MB')
    check(fmt_speed(606 * 1024) == '606.0 KB/s', 'fmt_speed KB/s')
    check(fmt_eta(2177) == '36分17秒', 'fmt_eta 36分17秒')
    check(fmt_eta(4.5 * 3600) == '4小时30分', 'fmt_eta 4小时30分')
    check(fmt_eta(None) == '', 'fmt_eta 未知 → 空')

    tmp = tempfile.mkdtemp(prefix='vp_dlchk_')
    p = os.path.join(tmp, 'fake.zip')
    with open(p, 'wb') as f:
        f.write(b'0' * 64)
    ticks = []

    def on_tick(d):
        ticks.append(d)

    w = DownloadWatcher(lambda: os.path.getsize(p), on_tick=on_tick,
                        total=1024 * 1024, interval=0.3)
    w.start()
    for _ in range(6):
        time.sleep(0.25)
        with open(p, 'ab') as f:
            f.write(b'0' * (32 * 1024))
    w.stop()
    check(bool(ticks), 'watcher 有 tick 回调')
    if ticks:
        last = ticks[-1]
        check(last['downloaded'] > 0 and last['total'] == 1024 * 1024,
              'tick 带已下载/总量')
        check(last['speed'] > 0, 'tick 算出速度')
        check('速度' in last['msg'] and '已下载' in last['msg']
              and '剩余' in last['msg'], '进度文案含 速度/已下载/剩余')
        check(0 < last['pct'] <= 0.95, '进度百分比在 (0, 0.95]')
    try:
        os.remove(p)
        os.rmdir(tmp)
    except OSError:
        pass

    print("C. 传参链路")
    dsc = read('Virus_Platform_Core/public_meta/datasets_cli.py')
    check('_PKG_SIZE_RE' in dsc and 'valid data package' in dsc,
          'datasets 包大小从 "valid data package" 行解析')
    check('def _download_package(' in dsc and
          'log=None, prog=None' in dsc,
          '_download_package 接受 prog')
    check('watcher.set_total(' in dsc, '下载中拿到总量后回填 watcher')
    for fn in ('download_genome', 'download_gene', 'download_taxonomy',
               'download_virus'):
        check(re.search(rf'def {fn}\(.*?prog=None', dsc, re.S) is not None,
              f'{fn} 接受 prog')
    check('return table[mode](log=log, prog=prog, **kw)' in dsc,
          'download() 透传 prog')

    hg = read('Virus_Platform_Core/public_meta/host_genome.py')
    for fn in ('download_genome_accession', 'download_genome_gget',
               'download_genome_https'):
        check(re.search(rf'def {fn}\(.*?prog=None', hg, re.S) is not None,
              f'{fn} 接受 prog')
    check('old_dir = out_dir' in hg and
          "merged_fasta = os.path.join(out_dir,\n"
          "                                            'all.genome.uniq.fasta')" in hg,
          '目录按 TaxID 改名后重定基 merged_fasta（否则成功下载被记成失败）')

    build = read('Virus_Platform_Core/web/build.py')
    check(build.count('prog=prog') >= 4, 'build.py 下载 job 全部传 prog')

    check('清理上次未完成的下载包' in dsc,
          '下载前清掉上次残留的半成品包（避免进度条先满后倒退）')
    check('total_hint: int = 0' in dsc, '_download_package 接受总量预估')
    check('total=int(total_hint or 0)' in dsc,
          '总量预估作为 watcher 初值（不等 datasets 收尾报数）')
    check('_ZIP_RATIO_BP = 0.32' in dsc, 'zip≈0.32×bp 的经验比例在案')
    util2 = read('Virus_Platform_Core/utils.py')
    check('and pct >= 0.02' in util2,
          '进度 <2% 不报 ETA（首个采样点速度未稳，会报出 30 分钟）')
    check('if total and size > total:' in util2,
          '实测超出预估时总量自适应上抬（不出现已下载>总量）')
    check('_zip_size_hint(accession)' in dsc,
          'download_genome 按组装长度预估 zip 总量')
    sys.path.insert(0, ROOT)
    from Virus_Platform_Core.public_meta import datasets_cli as _dsc
    check(_dsc._zip_size_hint('') == 0, '无 accession 不给预估')

    print("D. 前端")
    js = read('webapp/static/app-jobs.js')
    check('function cleanLogText(' in js, 'app-jobs.js 有 cleanLogText')
    check(js.count('cleanLogText(task.log)') >= 4,
          '所有日志渲染都走 cleanLogText')
    check("esc(trTaskMsg(task.msg))" in js, '卡内显示任务 msg（速度/剩余）')
    check("class=\"bar${indet ? ' indet' : ''}\"" in js,
          '无 ETA 时走不确定扫条')
    check('没有 ETA（大文件下载只能上报阶段' in js or 'indet' in js,
          'indet 注释/用法在案')

    print()
    if FAILS:
        print(f"✘ {len(FAILS)}/{N[0]} 项失败")
        for f in FAILS:
            print('   -', f)
        return 1
    print(f"✔ 全部 {N[0]} 项通过")
    return 0


if __name__ == '__main__':
    sys.exit(main())
