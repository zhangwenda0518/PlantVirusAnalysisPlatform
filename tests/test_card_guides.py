# -*- coding: utf-8 -*-
"""卡片内「📖 使用引导」（app-guide.js）的守护测试。

五件事必须一直成立：
  1. 模板里每个 `data-guide="Slug"` 的 Slug 都有 wiki 文档
     （wiki/<Slug>.md 存在且 index.json 有映射——含 global 空组）；
  2. 每个 Slug 用与 app-guide.js **相同口径**的抽取至少得到 1 条「流程步骤」
     （两边规则同改；文档删掉流程节时这里红）；
  3. 「下一步」链接的 slug 都真实存在（防文档互链漂移把引导卡点成 404）；
  4. app-guide.js 被 _nav.html 引入（所有完整页面共享该 include）；
  5. webapp/static/wiki/ 副本与 wiki/ 源一致（改文档忘了 sync 时这里红——
     引导与组概览读的都是静态副本，源改了副本没改 = 用户看到的还是旧文案）。

抽取口径（与 app-guide.js extract() 对齐，两边一起改）：
  · h2 节名前缀：步骤=流程/步骤/怎么用/使用/工作流程/链路/快速上手；
    输入=输入/数据输入；产物=产物/输出/示例输出；下一步=相关/下一步/衔接
  · 步骤 = 完整步骤概览（与 app-guide.js stepsOf 同口径）：有 ### 子节 →
    每子节一步（标题 + 前 2 条要点以「；」连接，≤8 步）；无子节 →
    平铺 bullet ≤8 条
"""
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WIKI = os.path.join(ROOT, 'wiki')
STATIC = os.path.join(ROOT, 'webapp', 'static', 'wiki')
TPL = os.path.join(ROOT, 'webapp', 'templates')

STEP_H = re.compile(r'^(流程|步骤|怎么用|使用|工作流程|链路|快速上手)')
IN_H = re.compile(r'^(输入|数据输入)')
OUT_H = re.compile(r'^(产物|输出|示例输出)')
NEXT_H = re.compile(r'^(相关|下一步|衔接)')
LIST_IT = re.compile(r'^\s*(?:[-*]|\d+[.、])\s+')


def _sections(txt):
    out, cur, buf = {}, '__pre__', []
    for ln in txt.splitlines():
        m = re.match(r'^##\s+(.*)', ln)
        if m:
            if cur != '__pre__':
                out[cur] = buf
            cur, buf = m.group(1).strip(), []
        else:
            buf.append(ln)
    out[cur] = buf
    return out


def _strip_md(s):
    s = re.sub(r'\*\*(.+?)\*\*', r'\1', s)
    s = re.sub(r'`([^`]*)`', r'\1', s)
    s = re.sub(r'\[([^\]]*)\]\(([^)]*)\)', r'\1', s)
    return re.sub(r'\s+', ' ', s).strip()


def _steps_of(lines, cap=8):
    """与 app-guide.js stepsOf 同口径（两侧同改）。

    · 有 ### 子节 → 每子节一步：(子节标题, 前2条要点以"；"连接)
      —— 完整步骤概览，覆盖 步骤一…步骤N/配套/失败处理 全部子节；
    · 无 ### 子节 → 平铺 bullet：(bullet, '')，≤cap 条。"""
    subs, plain, cur = [], [], None
    for l in lines:
        mh = re.match(r'^###\s+(.*)$', l)
        if mh:
            cur = {'title': _strip_md(mh.group(1))[:60], 'bullets': [],
                   'para': ''}
            if cur['title']:
                subs.append(cur)
            continue
        if LIST_IT.match(l):
            b = _strip_md(LIST_IT.sub('', l))[:120]
            if not b:
                continue
            if cur is not None:
                if len(cur['bullets']) < 2:
                    cur['bullets'].append(b)
            elif not subs:
                plain.append(b)
        elif (cur is not None and not cur['bullets'] and not cur['para']
              and l.strip() and not re.match(r'^[|`>#-]', l.strip())):
            # 子节没有 bullet 时取首个正文段落兜底（如「五段顺序」的链式说明），
            # 不让步骤只剩裸标题；表格/代码块/引用/子标题不算。
            cur['para'] = _strip_md(l)[:120]
    if subs:
        return [(u['title'], ('；'.join(u['bullets']) or u['para'])[:190])
                for u in subs[:cap]]
    return [(b, '') for b in plain[:cap]]



def _guide_slugs():
    """模板里出现的全部 data-guide slug → 挂载点列表。"""
    found = {}
    for name in os.listdir(TPL):
        if not name.endswith('.html'):
            continue
        txt = open(os.path.join(TPL, name), encoding='utf-8').read()
        for m in re.finditer(r'data-guide="([A-Za-z][A-Za-z0-9-]*)"', txt):
            found.setdefault(m.group(1), []).append(name)
    return found


def _index_map():
    with open(os.path.join(STATIC, 'index.json'), encoding='utf-8') as fh:
        idx = json.load(fh)
    mp = {}
    for g, slugs in (idx.get('groups') or {}).items():
        for s in slugs:
            mp[s] = g
    for s in idx.get('global') or []:
        mp[s] = ''
    return mp


def test_every_data_guide_slug_has_wiki_doc():
    found = _guide_slugs()
    assert found, '模板里没有任何 data-guide 挂点（app-guide.js 全空转）'
    mp = _index_map()
    for slug, mounts in found.items():
        assert os.path.isfile(os.path.join(WIKI, slug + '.md')), \
            f'data-guide={slug!r}（挂于 {mounts}）没有 wiki/{slug}.md'
        assert slug in mp, \
            f'data-guide={slug!r} 不在 index.json 映射里（完整文档链接会 404）'


def test_every_mapped_slug_extracts_steps():
    found = _guide_slugs()
    for slug in found:
        txt = open(os.path.join(WIKI, slug + '.md'), encoding='utf-8').read()
        secs = _sections(txt)
        steps = []
        for h, lines in secs.items():
            if STEP_H.match(h) and not steps:
                # 语义与 app-guide.js 一致：匹配节产出为空时继续找下一个
                # 匹配节（如「链路」无 list 时落到后面的「快速上手」）。
                steps = _steps_of(lines)
                continue
        assert steps, \
            f'wiki/{slug}.md 抽不出流程步骤（缺「流程/步骤/链路/快速上手」节，' \
            '或该节既无 list 也无 ### 子标题）—— 引导卡展开会是空流程'


def test_next_links_resolve():
    found = _guide_slugs()
    names = {n[:-3] for n in os.listdir(WIKI) if n.endswith('.md')}
    for slug in found:
        txt = open(os.path.join(WIKI, slug + '.md'), encoding='utf-8').read()
        secs = _sections(txt)
        for h, lines in secs.items():
            if NEXT_H.match(h):
                joined = '\n'.join(lines)
                for mm in re.finditer(
                        r'\[([^\]]+)\]\(([A-Za-z][A-Za-z0-9_-]*)\)', joined):
                    assert mm.group(2) in names, \
                        f'wiki/{slug}.md「{h}」节的互链 {mm.group(2)} 不存在'


def test_guide_script_included_in_shared_nav():
    nav = open(os.path.join(TPL, '_nav.html'), encoding='utf-8').read()
    assert 'app-guide.js' in nav, '_nav.html 未引入 app-guide.js'


# 「声称挂了就得真挂着」防漏表：文件名 → {slug: 该文件里应出现的最少条数}。
# 历史教训（2026-11-06 审稿自查）：总结里写了 submit/samples，实际补丁漏挂
# ——当时只断言「出现的 slug 合法」，漏挂不报红。本表钉死关键挂点，
# 新增挂点往这里登记；删挂点必须连本表一起改并说明理由。
EXPECTED_MOUNTS = {
    'tools.html': {'Fastp': 1, 'Convert': 1, 'Identify': 1, 'Prepchain': 1,
                   'Assembly': 1, 'Contigs': 1, 'Virchain': 1, 'Verify': 1,
                   'Kvchain': 1, 'Kvsuite': 1, 'Consensus': 1, 'Variant': 1,
                   'Annochain': 1, 'CRISPR-Design': 1, 'LAMP-Design': 1,
                   'DsRNA': 1, 'Mirna': 1, 'Cmpchain': 1, 'Seq-Prep': 1,
                   'Align': 1, 'Phylo': 2, 'Phylodyn': 1, 'Pd-Prep': 1,
                   'Rtt': 1, 'Rdp': 1, 'Phylogeography': 1, 'Logan-Fetch': 1},
    'refdata.html': {'Ref-Data': 3},
    'build.html': {'Databases': 4},
    'samples.html': {'Samples': 1},
    'submit.html': {'NCBI-Submission': 1},
    'logan.html': {'Logan': 1},
    'download.html': {'Download': 1},
    'prescreen.html': {'Prescreen': 1},
    'meta.html': {'Meta-Search': 1},
    'host_removal.html': {'Host-Removal': 1},
    'orf.html': {'ORF': 1},
    'annotation.html': {'Annotate-Intro': 1},
    'genome.html': {'Genome-Plots': 1},
    'cds_export.html': {'Cds-Export': 1},
    'primer.html': {'Primer': 1},
    'settings.html': {'Settings-Ops': 1},
    'results.html': {'Results': 1},
}


def test_expected_mounts_present():
    for fname, want in EXPECTED_MOUNTS.items():
        txt = open(os.path.join(TPL, fname), encoding='utf-8').read()
        got = {}
        for m in re.finditer(r'data-guide="([A-Za-z][A-Za-z0-9-]*)"', txt):
            got[m.group(1)] = got.get(m.group(1), 0) + 1
        for slug, n in want.items():
            assert got.get(slug, 0) >= n, \
                f'{fname} 的 data-guide="{slug}" 挂点缺失或减少' \
                f'（期望 ≥{n}，实际 {got.get(slug, 0)}）'


def test_static_wiki_copies_fresh():
    for slug in _guide_slugs():
        src = os.path.join(WIKI, slug + '.md')
        dst = os.path.join(STATIC, slug + '.md')
        assert os.path.isfile(dst), f'静态副本缺 {slug}.md（跑 dev_tools/sync_wiki.py）'
        assert open(src, 'rb').read() == open(dst, 'rb').read(), \
            f'{slug}.md 静态副本过期（跑 python dev_tools/sync_wiki.py）'

def test_steps_shape_contract():
    """合成样例锁定 stepsOf 契约（JS/Python 两侧同口径的锚）：子节模式 + 平铺模式。"""
    # 形态一：### 子节 → 每子节一步，标题+前2条要点；导语丢弃；表格不是 bullet
    lines = ['导语（丢弃）', '',
             '### 步骤一 去接头', '- 自动去接头 + 低质量裁剪', '- Q20 / 最短 15bp',
             '- 第三条要点不收（>2 条截断）', '',
             '### 步骤二 写产物', '- 产物落 00_prep/', '',
             '### 配套', '| 表格不是 bullet |']
    st = _steps_of(lines)
    assert st == [('步骤一 去接头', '自动去接头 + 低质量裁剪；Q20 / 最短 15bp'),
                  ('步骤二 写产物', '产物落 00_prep/'),
                  ('配套', '')], st
    # 形态二：无子节 → 平铺 bullet
    st2 = _steps_of(['- 第一步', '- 第二步', '', '- 第三步'])
    assert st2 == [('第一步', ''), ('第二步', ''), ('第三步', '')], st2
    # cap：子节超 8 截断
    many = []
    for i in range(10):
        many += ['### 步骤%d' % i, '- 要点%d' % i]
    assert len(_steps_of(many)) == 8
    # 形态三：子节无 bullet → 首个正文段落兜底（表格/代码块不算）
    st3 = _steps_of(['### 五段顺序', '',
                     '五段一次跑完：鉴定→过滤→共识→深度绘图→变异注释。',
                     '', '```text', 'a -> b', '```'])
    assert st3 == [('五段顺序',
                    '五段一次跑完：鉴定→过滤→共识→深度绘图→变异注释。')], st3
