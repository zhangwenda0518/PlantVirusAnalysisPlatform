# -*- coding: utf-8 -*-
"""全路由健壮性巡检：对每条已注册路由发一次「空参数」请求。

目的：把全部路由触达一遍，找出**未捕获异常导致的 500**。
空参数下 400/404/405 属正常（参数校验拦住了），只有 500 说明服务端崩了。

安全约束：
- 只发一次请求，不重试；
- **破坏性路由必须跳过**：见下面 DESTRUCTIVE_POST。这些路由在「空参数 POST」
  下就会改状态甚至删数据，巡检绝不能碰。（2026-09-11 实测踩坑：本脚本
  对空 POST 的 /api/global/reset 拿到了 200，把 run/tasks/*.json 里的
  历史任务记录全部清空了——"全程只读"当时只是一句没有实现的承诺。
  该接口现已加 confirm 令牌兜底，但巡检这边也必须显式跳过。）
- 请求前后各取一次 tasks/ 目录快照，**新增与删除都比对**并告警——
  只查新增会漏掉删数据的路由。

用法: python tests/_audit_routes.py [--verbose]
退出码: 0 = 无 500 / 无未捕获异常 / 无任务目录增删；1 = 存在问题。
"""
import io
import os
import sys
import traceback
# 控制台编码兜底：Windows 默认代码页是 GBK，本脚本的 ✔/✘/⚠ 等字符会让
# print 抛 UnicodeEncodeError（2026-09-11 实测多处踩过）。只改错误处理为
# replace（编码不动，中文照常可读），编不出的字符降级为 '?'。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors='replace')
    except (AttributeError, OSError):
        pass


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

REPORT = os.path.join(ROOT, 'tests', '_audit_routes_report.txt')

# 空参数 POST 就会改状态/删数据的路由：不参与巡检。
# 新增破坏性路由时必须登记到这里，否则巡检会真的执行它。
DESTRUCTIVE_POST = {
    '/api/global/reset',            # 取消全部任务 + 清队列 + 清任务记录
    '/api/tasks/clear_finished',    # 删 tasks/*.json
    '/api/queue/clear_finished',    # 清批处理队列
    '/api/tasks/stop_all',          # 空参数 POST 就会取消全部运行中任务
    '/api/queue/pause',             # 空参数 POST 就会暂停批处理队列
    '/api/queue/resume',            # 空参数 POST 就会恢复批处理队列
    '/api/queue/start_all',         # 空参数 POST 就会启动全部待启动样品任务
    '/api/build_taxonomy',          # 空参数 POST 会真启动建库任务（2026-09-20 巡检实
    '/api/build_universal_db',      # 测：新增了两个 tasks/*.json）——一律跳过
    '/api/set_host_db',             # 空参数 POST 改宿主库配置
    '/api/settings',                # 空参数 POST 保存设置
    '/api/submit/table/<name>/delete',  # 空参数 POST 删提交表数据
    # 2026-11-02 实测踩坑补登：这两条当时**不在表里**，巡检之外的一个
    # 「照抄巡检思路、却漏抄本表」的探测脚本空 POST 打过来，先把
    # run/tool_runs/ 下 74 个运行目录移进归档区（keep 缺省 3 即生效），
    # 紧接一个空 POST purge 就把它们永久删了。两个接口现已要求 confirm 令牌，
    # 但这张表仍然要登记——巡检的价值在于「不依赖接口自我保护」。
    '/api/tool/runs/archive',          # 批量移入归档区（keep 缺省 3）
    '/api/tool/runs/archived/purge',    # 空 names = 清空整个归档区，不可恢复
}


def _snapshot_tasks():
    p = os.path.join(ROOT, 'run', 'tasks')
    if not os.path.isdir(p):
        return set()
    return set(os.listdir(p))


def main():
    verbose = '--verbose' in sys.argv
    import app as appmod
    app = appmod.app
    app.config['TESTING'] = True

    rules = []
    for r in app.url_map.iter_rules():
        if r.endpoint == 'static':
            continue
        methods = sorted(m for m in r.methods
                         if m not in ('HEAD', 'OPTIONS'))
        rules.append((r.rule, methods))
    rules.sort()

    before = _snapshot_tasks()
    c = app.test_client()

    errors = []
    server_errors = []
    lines = []
    for rule, methods in rules:
        # 把 <conv:name> 换成占位值，尽量走到 view 内部
        path = rule
        import re as _re
        path = _re.sub(r'<path:(\w+)>', 'nonexistent_probe.txt', path)
        path = _re.sub(r'<int:(\w+)>', '0', path)
        path = _re.sub(r'<(\w+:)?(\w+)>', 'nonexistent_probe', path)
        for m in methods:
            if m not in ('GET', 'POST'):
                continue
            if m == 'POST' and rule in DESTRUCTIVE_POST:
                lines.append(f'{m:4} {path:60} -> SKIP(破坏性，不巡检)')
                continue
            try:
                if m == 'GET':
                    resp = c.get(path)
                else:
                    resp = c.post(path, json={})
                code = resp.status_code
            except Exception:
                code = 'EXC'
                errors.append((m, path, traceback.format_exc(limit=6)))
            if code == 500:
                server_errors.append((m, path))
            lines.append(f'{m:4} {path:60} -> {code}')
    after = _snapshot_tasks()
    new_tasks = sorted(after - before)
    gone_tasks = sorted(before - after)

    with io.open(REPORT, 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(lines) + '\n')
        if errors:
            f.write('\n\n未捕获异常:\n')
            for m, p, tb in errors:
                f.write(f'\n--- {m} {p}\n{tb}\n')

    print(f'路由数: {len(rules)}  请求数: {len(lines)}')
    print(f'500 错误: {len(server_errors)}')
    for m, p in server_errors:
        print(f'    {m} {p}')
    print(f'未捕获异常: {len(errors)}')
    for m, p, _tb in errors:
        print(f'    {m} {p}')
    if new_tasks:
        print(f'⚠ 巡检期间新增任务目录 {len(new_tasks)} 个: {new_tasks}')
    else:
        print('✔ 巡检未触发任何后台任务')
    if gone_tasks:
        print(f'✘ 巡检期间任务记录被删除 {len(gone_tasks)} 个——'
              f'某条路由在空参数下会删数据，必须加入 DESTRUCTIVE_POST 跳过表')
        print(f'    {gone_tasks[:10]}{" …" if len(gone_tasks) > 10 else ""}')
    if verbose:
        for ln in lines:
            print(ln)
    print(f'明细: {REPORT}')
    return 1 if (server_errors or errors or new_tasks or gone_tasks) else 0


if __name__ == '__main__':
    raise SystemExit(main())
