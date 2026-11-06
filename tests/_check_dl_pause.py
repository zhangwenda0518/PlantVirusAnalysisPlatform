# -*- coding: utf-8 -*-
"""下载「暂停 / 继续 / 取消」守卫（2026-11-02）。

用户实测：点「取消」后 datasets.exe 继续在下（任务卡停在
「已请求取消（当前步骤结束后停止）」），且提出要「暂停还能继续，
取消就是直接停止」。本用例守住：
  A. 取消可感知：datasets_cli 的 _run_and_stream 注册子进程 + 轮询取消/暂停，
     不再阻塞在 for line in proc.stdout。
  B. 任务级暂停：TaskManager 有 pause/resume、TaskPaused 终态、端点、
     状态口径（paused 视为活跃）、取消对 paused 立刻生效。
  C. 断点续传：host_genome 的 _http().download 支持 resume/416/206 回退，
     内置 HTTP 保留半截文件供续传。
  D. 批次级暂停：DownloadManager.pause/resume + 路由分支 + 前端按钮。
  E. 前端：任务卡暂停/继续按钮、paused 状态文案与徽标、i18n 中英成对。
不联网、不写盘（只读源码 + 少量纯函数断言）。
"""
import os
import re
import sys

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
    dsc = read('Virus_Platform_Core/public_meta/datasets_cli.py')
    hg = read('Virus_Platform_Core/public_meta/host_genome.py')
    tasks = read('Virus_Platform_Core/web/tasks.py')
    tapi = read('Virus_Platform_Core/web/tasks_api.py')
    pd = read('Virus_Platform_Core/public_data.py')
    dlpy = read('Virus_Platform_Core/web/download.py')
    jobs = read('webapp/static/app-jobs.js')
    dltpl = read('webapp/templates/download.html')
    i18n = read('webapp/static/i18n.js')

    print("A. 取消/暂停能被 datasets 子进程感知")
    check('task_register_proc(proc)' in dsc, '_run_and_stream 注册子进程')
    check('_kill_proc_tree(proc)' in dsc, '取消时杀掉子进程树')
    check("raise TaskPaused(" in dsc, '暂停时抛 TaskPaused（工作线程立即收手）')
    check("任务已停止（用户取消）" in dsc, '取消异常文案一致')
    check('queue.Queue()' in dsc and 'proc.stdout' in dsc,
          '读线程 + 队列：主循环不再阻塞在管道迭代上')
    check(re.search(r"timeout\s*[<>=]+\s*0|time\.time\(\)\s*-\s*t0", dsc) is not None,
          '主循环带超时判定')

    print("B. 任务级暂停/继续")
    from Virus_Platform_Core.utils import (TaskPaused, task_bind,
                                           task_pause_event)
    check(issubclass(TaskPaused, Exception), 'TaskPaused 是异常')
    import threading
    ev = threading.Event()
    task_bind(None, None, ev)
    check(task_pause_event() is ev, 'task_bind 绑定 pause 事件后可取回')
    ev.set()
    try:
        from Virus_Platform_Core.utils import task_check_pause
        task_check_pause()
        ok = False
    except TaskPaused:
        ok = True
    finally:
        ev.clear()
    check(ok, 'task_check_pause 在暂停时抛 TaskPaused')
    check("'pause': threading.Event()" in tasks, '任务记录带 pause 事件')
    check('def pause(self, tid)' in tasks, 'TaskManager.pause 存在')
    check('def resume(self, tid)' in tasks, 'TaskManager.resume 存在')
    check("_relaunch" in tasks, 'resume 靠重跑同一 job 实现')
    check("rec['status'] = 'paused'" in tasks, '_run 捕获 TaskPaused 置 paused')
    check('pausable' in tasks and "'pausable': bool(rec.get('pausable'))" in tasks,
          'snapshot 下发 pausable')
    check("'paused': rec['status'] == 'paused'" in tasks, 'snapshot 下发 paused')
    check("rec['status'] in ('running', 'queued', 'paused')" in tasks,
          'cancel 受理 paused')
    check(re.search(r"rec\['pause'\]\.clear\(\)", tasks) is not None,
          '取消时清掉暂停标记（否则暂停态取消不掉）')
    check("d.get('status') in ('running', 'queued', 'paused')" in tasks,
          '重启恢复：paused 也按中断处理')

    print("C. 断点续传（内置 HTTP）")
    check('resume=False' in hg, '_http().download 支持 resume')
    check("'Range': f'bytes={start}-'" in hg, '续传带 Range 头')
    check('status_code == 416' in hg, '416 视为已下完')
    check('status_code != 206' in hg, '服务器不认 Range 时从 0 重下')
    check("open(dest, 'ab' if start else 'wb')" in hg, '续传追加写')
    check(hg.count('resume=True') >= 2, '宿主基因组两个下载点都开 resume')
    check('_task_paused()' in hg, '分块循环里能感知暂停')

    print("D. 批次级暂停/继续")
    check('def pause(self, bid)' in pd, 'DownloadManager.pause 存在')
    check('def resume(self, bid)' in pd, 'DownloadManager.resume 存在')
    check("if b.get('status') == 'paused':" in pd, '_finalize 不覆写暂停态')
    check("b.get('status') == 'paused'" in pd, 'worker 暂停时把文件退回 pending')
    check("elif action == 'pause':" in dlpy and "elif action == 'resume':" in dlpy,
          '批次路由接 pause/resume')
    check("paused: t('dl.stPaused')" in dltpl, '批次状态文案含 paused')
    check("batchAction('${esc(b.id)}','pause')" in dltpl, '批次卡有暂停按钮')
    check("batchAction('${esc(b.id)}','resume')" in dltpl, '批次卡有继续按钮')
    check("'paused'].includes(b.status)" in dltpl, '暂停中仍保持轮询')

    print("E. 任务卡前端")
    check('function taskButtons(task)' in jobs, 'taskButtons 统一出按钮')
    check("t('c.pause')" in jobs and "t('c.resume')" in jobs,
          '任务卡有暂停/继续文案')
    check("'/api/task/' + tid + '/pause'" in jobs.replace('"', "'"),
          '前端调 pause 端点')
    check("'/api/task/' + tid + '/resume'" in jobs.replace('"', "'"),
          '前端调 resume 端点')
    check("paused: t('c.st.paused')" in jobs, '任务状态文案含 paused')
    check("'paused', 'cancelled'" in jobs.replace('"', "'"),
          '暂停/取消时卡内不被清空')
    check('.sbadge.paused' in read('webapp/static/app.css'), '有 paused 徽标样式')
    # 交替重绘（A,B,A,B…）绕得过「只比上一行」：datasets/aria2 的进度帧就是
    # 两条交替，同一秒能刷十几行。守 3 秒窗口去重那一小段。
    check("_recent" in tasks and 'now - ts < 3.0' in tasks,
          '日志按 3 秒窗口去重（交替重绘不再刷屏）')
    for k in ('c.pause', 'c.resume', 'c.pauseNo', 'c.resumeNo', 'c.st.paused',
              'dl.stPaused', 'dl.pauseTip', 'dl.resumePausedTip'):
        n = len(re.findall(r"'%s':" % re.escape(k), i18n))
        check(n == 2, f'i18n 键 {k} 中英各一次（实际 {n}）')

    print(f"\n结果：{N[0] - len(FAILS)}/{N[0]} 通过")
    if FAILS:
        print("失败项：")
        for f in FAILS:
            print("  -", f)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
