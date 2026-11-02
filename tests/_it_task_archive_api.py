# -*- coding: utf-8 -*-
"""归档/恢复 API + 页面渲染自测（只在本进程内改 DIRS['tasks']）。"""
import os
import sys
import time
import shutil
import tempfile
from collections import deque
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ['VP_NO_RECOVER'] = '1'

import app as APP
from Virus_Platform_Core.web import tasks as T

tmp = tempfile.mkdtemp(prefix='vp_arch_api_')
T.DIRS['tasks'] = tmp
m = T.tm
tid = 'deadbeef0002'

rec = {'id': tid, 'name': '公共检索 API 自测', 'status': 'done', 'stage': '结束',
       'pct': 1.0, 'msg': '完成', 'error': '',
       'log': deque(['a', 'b'], maxlen=500),
       'started': time.time() - 20, 'finished': time.time() - 3,
       'cancel': threading.Event(), 'procs': [], 'weight': 'light',
       'log_file': None, 'link': None, 'out_dir': None, 'restart': None,
       'result': {'n': 1}}
m.tasks[tid] = rec
m.order.insert(0, tid)
m._persist(rec)

c = APP.app.test_client()

r = c.post(f'/api/task/{tid}/archive')
assert r.status_code == 200, (r.status_code, r.data[:300])
d = c.get('/api/tasks?full=1&logs=0').get_json()
assert any(x['id'] == tid for x in d['archived']), d['archived'][:3]
assert d['archived_total'] >= 1, d['archived_total']
assert not any(x['id'] == tid for x in d['active']), '归档后还在 active'

r = c.post(f'/api/task/{tid}/unarchive')
assert r.status_code == 200, (r.status_code, r.data[:300])
d = c.get('/api/tasks?full=1&logs=0').get_json()
assert any(x['id'] == tid for x in d['active']), '恢复后不在 active'
assert not any(x['id'] == tid for x in d['archived']), '恢复后仍在 archived'

# 运行中不可归档
m.tasks[tid]['status'] = 'running'
r = c.post(f'/api/task/{tid}/archive')
assert r.status_code == 400, r.status_code
m.tasks[tid]['status'] = 'done'

# 未知任务 400
r = c.post('/api/task/aaaabbbbcccc/archive')
assert r.status_code == 400, r.status_code

# 页面渲染
for url in ('/tasks', '/meta', '/', '/pipeline'):
    rr = c.get(url)
    assert rr.status_code in (200, 302), (url, rr.status_code)

# 干净收尾：把测试任务从内存与磁盘清掉
m.tasks.pop(tid, None)
try:
    m.order.remove(tid)
except ValueError:
    pass
for p in (os.path.join(tmp, tid + '.json'), os.path.join(tmp, '_archive', tid + '.json')):
    if os.path.isfile(p):
        os.remove(p)
shutil.rmtree(tmp, ignore_errors=True)
print('API + 页面渲染全部通过')

