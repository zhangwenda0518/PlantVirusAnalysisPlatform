# -*- coding: utf-8 -*-
"""任务手动归档 / 恢复往返自测（只在本进程内改 DIRS['tasks']，不碰真实 run/tasks）。"""
import os
import sys
import time
import shutil
import tempfile
from collections import deque
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ['VP_NO_RECOVER'] = '1'

from Virus_Platform_Core.web import tasks as T

tmp = tempfile.mkdtemp(prefix='vp_arch_')
T.DIRS['tasks'] = tmp                 # 只改本进程内的落盘目录
m = T.TaskManager()
tid = 'deadbeef0001'

rec = {'id': tid, 'name': '公共检索 自测', 'status': 'done', 'stage': '结束',
       'pct': 1.0, 'msg': '完成', 'error': '',
       'log': deque(['line-1', 'line-2'], maxlen=500),
       'started': time.time() - 30, 'finished': time.time() - 5,
       'cancel': threading.Event(), 'procs': [], 'weight': 'light',
       'log_file': None, 'link': None, 'out_dir': None, 'restart': None,
       'result': {'n': 1}}
m.tasks[tid] = rec
m.order.insert(0, tid)
m._persist(rec)

assert os.path.isfile(os.path.join(tmp, tid + '.json')), 'persist 未落盘'
assert [x['id'] for x in m.list_all(logs=False)] == [tid], m.list_all(logs=False)
assert m.count_archived() == 0

ok, msg = m.archive(tid)
assert ok, msg
assert tid not in m.tasks and tid not in m.order, '归档后仍留在内存'
assert os.path.isfile(os.path.join(tmp, '_archive', tid + '.json')), '归档文件缺失'
assert not os.path.isfile(os.path.join(tmp, tid + '.json')), '顶层记录未删'
arch = m.list_archived()
assert len(arch) == 1 and arch[0]['id'] == tid, arch
assert arch[0].get('archived') is True, arch
assert m.count_archived() == 1

ok, msg = m.archive(tid)
assert not ok and '不存在' in msg, (ok, msg)

ok, msg = m.unarchive(tid)
assert ok, msg
assert tid in m.tasks and tid in m.order, '恢复后未回到内存'
assert [x['id'] for x in m.list_all(logs=False)] == [tid]
assert m.list_archived() == [], '恢复后归档里仍有记录'
assert os.path.isfile(os.path.join(tmp, tid + '.json')), '恢复后未重新落盘'
assert not os.path.isfile(os.path.join(tmp, '_archive', tid + '.json')), '恢复后归档文件未删'

# 运行中的任务不允许归档
m.tasks[tid]['status'] = 'running'
ok, msg = m.archive(tid)
assert not ok and '运行中' in msg, (ok, msg)
m.tasks[tid]['status'] = 'done'

# delete 两个目录都清
m.archive(tid)
ok, msg = m.delete(tid)
assert ok, msg
assert not os.path.exists(os.path.join(tmp, '_archive', tid + '.json'))
assert not os.path.exists(os.path.join(tmp, tid + '.json'))

# purge_all 清 _archive
m2 = T.TaskManager()
m2.tasks[tid] = rec
m2.order = [tid]
m2._persist(rec)
m2.archive(tid)
assert os.path.isfile(os.path.join(tmp, '_archive', tid + '.json'))
m2.purge_all()
assert not os.listdir(os.path.join(tmp, '_archive')), os.listdir(os.path.join(tmp, '_archive'))

shutil.rmtree(tmp, ignore_errors=True)
print('archive/unarchive/delete/purge 全部通过')

