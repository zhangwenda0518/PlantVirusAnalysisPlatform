# -*- coding: utf-8 -*-
"""技能卡一致性检查（`.agents/skills/virusplatform*`）。

为什么需要这个测试：技能卡是**给人/给 agent 读的文档**，不是代码——
平台加了工具、改了阶段、挪了文档，卡片不会自己报错，只会在下次会话里
把 agent 带偏（说错 op id、指到不存在的文件、引用已被删掉的 `.done`）。
本测试把「卡片 ↔ 代码」的几条硬契约钉住：

  [1] 三张卡都在、frontmatter 正常（name 与目录一致、description 中英双语）
  [2] 指针卡（`virusplatform`）与 `agent_guide._SKILL` 同步（重新生成不会回退）
  [3] 指针卡指向另两张卡（分工表不是装饰，缺一张就找不到入口）
  [4] ops 卡覆盖目录全集：32 工具 / 15 阶段 / 7 动作 一个都不能漏
  [5] ops 卡写的条数与 `list_operations()` 实际条数一致
  [6] 卡片里引用的 docs/ tests/ dev_tools/ 路径真实存在
  [7] 卡片里引用的 `.done` 标记在真实样品里出现过（没有样品时跳过）

用法：
    python tests/_check_skills.py
"""
from __future__ import annotations

import io
import os
import re
import sys

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors='replace')
    except (AttributeError, OSError):
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

SKILLS_DIR = os.path.join(ROOT, '.agents', 'skills')
CARDS = ('virusplatform', 'virusplatform-ops', 'virusplatform-dev')

FAILS = []
SKIPS = []


def check(ok, msg):
    print(('  ✔ ' if ok else '  ✘ ') + msg)
    if not ok:
        FAILS.append(msg)


def skip(msg):
    print('  · SKIP ' + msg)
    SKIPS.append(msg)


def read(path):
    return io.open(path, encoding='utf-8', errors='replace').read()


def parse_front(text):
    """返回 (dict, 正文)。只认开头的 `---` 块，够用。"""
    if not text.startswith('---'):
        return {}, text
    end = text.find('\n---', 3)
    if end < 0:
        return {}, text
    head = text[3:end]
    data = {}
    key = None
    for line in head.splitlines():
        if not line.strip():
            continue
        m = re.match(r'^([A-Za-z_][\w-]*)\s*:\s*(.*)$', line)
        if m:
            key = m.group(1)
            data[key] = m.group(2).strip()
        elif key and line.startswith((' ', '\t')):
            data[key] = (data[key] + ' ' + line.strip()).strip()
    return data, text[end + 4:]


CJK = re.compile(r'[\u4e00-\u9fff]')
LATIN = re.compile(r'[A-Za-z]{3,}')


def main():
    print('=' * 60)
    print('[1] 三张卡存在 + frontmatter 契约')
    texts = {}
    for name in CARDS:
        path = os.path.join(SKILLS_DIR, name, 'SKILL.md')
        exists = os.path.isfile(path)
        check(exists, '%s/SKILL.md 存在' % name)
        if not exists:
            continue
        text = read(path)
        texts[name] = text
        meta, body = parse_front(text)
        check(bool(meta.get('name')), '%s: frontmatter 有 name' % name)
        check(meta.get('name') == name,
              '%s: name(%s) 与目录名一致' % (name, meta.get('name')))
        desc = meta.get('description') or ''
        check(len(desc) > 40, '%s: description 有实质内容（%d 字符）' % (name, len(desc)))
        check(bool(LATIN.search(desc)), '%s: description 含英文触发词（其它宿主可读）' % name)
        check(bool(CJK.search(desc)), '%s: description 含中文触发词（中文提问也能命中）' % name)
        check(len(body.strip()) > 400, '%s: 正文非空（%d 字符）' % (name, len(body.strip())))

    print('\n[2] 指针卡与 agent_guide._SKILL 同步')
    try:
        from Virus_Platform_Core import agent_guide
        regen = agent_guide.read_skill()
    except Exception as exc:  # noqa: BLE001
        check(False, '导入 agent_guide 失败：%s' % exc)
        regen = None
    if regen is not None:
        check('三张卡分工' in regen or 'virusplatform-ops' in regen,
              'agent_guide._SKILL 提到分工/ops 卡（重新生成不会退回旧版）')
        on_disk = texts.get('virusplatform')
        if on_disk is not None:
            check(regen.strip() in on_disk or on_disk.strip() in regen,
                  '磁盘上的指针卡与 _SKILL 输出一致（改了源头就要重新生成）')

    print('\n[3] 指针卡指向另两张卡')
    pointer = texts.get('virusplatform') or ''
    for other in ('virusplatform-ops', 'virusplatform-dev'):
        check(other in pointer, '指针卡提到 `%s`' % other)

    print('\n[4] ops 卡覆盖目录全集')
    try:
        from Virus_Platform_Core import agent_catalog as ac
        ops = ac.list_operations()
        items = ops.get('items') or []
    except Exception as exc:  # noqa: BLE001
        check(False, '导入 agent_catalog 失败：%s' % exc)
        items = []
    by_kind = {}
    for it in items:
        by_kind.setdefault(it.get('kind'), []).append(it.get('id'))
    ops_card = texts.get('virusplatform-ops') or ''
    for kind, label in (('tool', '工具'), ('stage', '阶段'), ('action', '动作')):
        ids = by_kind.get(kind) or []
        missing = [i for i in ids if i not in ops_card]
        check(not missing, 'ops 卡覆盖 %d 个%s（缺：%s）' % (len(ids), label, missing or '无'))

    print('\n[5] 卡片条数与实际一致')
    total = ops.get('total') if items else None
    counts = {'total': total, 'tool': len(by_kind.get('tool') or []),
              'stage': len(by_kind.get('stage') or []),
              'action': len(by_kind.get('action') or [])}
    if total:
        for key, n in counts.items():
            check(str(n) in ops_card, 'ops 卡出现条数 %s=%d' % (key, n))

    print('\n[6] 引用的仓库路径存在')
    path_re = re.compile(r'`((?:docs|tests|dev_tools|Virus_Platform_Core|webapp)/[\w./\-\u4e00-\u9fff]+)`')
    seen = set()
    for name, text in texts.items():
        for m in path_re.finditer(text):
            rel = m.group(1).rstrip('/')
            if rel in seen:
                continue
            seen.add(rel)
            full = os.path.join(ROOT, *rel.split('/'))
            check(os.path.exists(full), '%s 引用 `%s` 存在' % (name, rel))
    print('  · 共检查 %d 条路径引用' % len(seen))

    print('\n[7] 引用的 .done 标记真实出现过')
    done_re = re.compile(r'\.([a-z][a-z0-9_]*)\.done')
    claimed = set()
    for text in texts.values():
        claimed.update(done_re.findall(text))
    real = set()
    results = os.path.join(ROOT, 'run', 'results')
    if os.path.isdir(results):
        for sample in os.listdir(results):
            base = os.path.join(results, sample)
            if not os.path.isdir(base):
                continue
            for dirpath, _dirs, files in os.walk(base):
                depth = dirpath[len(base):].count(os.sep)
                if depth > 2:
                    continue
                for f in files:
                    if f.startswith('.') and f.endswith('.done'):
                        real.add(f[1:-len('.done')])
                if real and len(real) > 200:
                    break
    if not real:
        skip('没找到任何 .done 标记（run/results 为空），跳过')
    else:
        unknown = sorted(x for x in claimed if x not in real)
        check(not unknown, '卡片里的 .done 标记都存在（未见过：%s）' % (unknown or '无'))
        print('  · 真实样品里见过 %d 种标记：%s' % (len(real), sorted(real)[:12]))

    print('\n' + '=' * 60)
    if SKIPS:
        print('跳过 %d 项（不判红）' % len(SKIPS))
    if FAILS:
        print('FAILED: %d 项' % len(FAILS))
        for f in FAILS:
            print('  - ' + f)
        return 1
    print('技能卡检查通过 ✔')
    return 0


if __name__ == '__main__':
    sys.exit(main())
