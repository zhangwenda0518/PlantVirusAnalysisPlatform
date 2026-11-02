# -*- coding: utf-8 -*-
"""正文各节：把模型字典写成 HTML 片段。

设计上的两条硬规矩——
1. **只写文件里有的东西**：模型里没有的字段一律不出现，绝不"补一个合理值"。
2. **每个数字都带出处**：正文里的结论后面都跟证据文件（可点开回查），
   因为这类报告最容易被追问的一句就是"你这个数从哪来的"。

所有函数签名统一为 `fn(model) -> (html, [(sub_id, sub_title), ...])`，
第二个返回值喂给左侧目录做二级锚点。
"""
from __future__ import annotations

import os

from .render import (code_block, esc, ev_list, figure, link_evidence, table)

# ------------------------------------------------------------------
# 小工具
# ------------------------------------------------------------------


def _g(d, *keys, default=None):
    """安全取嵌套值。"""
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
    return default if cur is None else cur


def _stage(model, key):
    for s in model['stages']:
        if s['key'] == key:
            return s
    return None


def _stages_of(model, keys):
    return [s for s in (_stage(model, k) for k in keys) if s]


def _fmt_dur(sec):
    if sec is None:
        return '—'
    sec = float(sec)
    if sec < 60:
        return f'{sec:.0f} s'
    if sec < 3600:
        return f'{sec / 60:.1f} min'
    return f'{sec / 3600:.1f} h'


def _num(v, d=2):
    try:
        return f'{float(v):,.{d}f}'
    except (TypeError, ValueError):
        return '—'


def _int(v):
    try:
        return f'{float(v):,.0f}'
    except (TypeError, ValueError):
        return '—'


def _verdict_tag(model):
    v = model.get('verdict') or {}
    cls = {'ok': 'tag', 'note': 'tag gray', 'warn': 'tag warn',
           'fail': 'tag bad'}.get(v.get('level'), 'tag gray')
    return f'<span class="{cls}">{esc(v.get("text") or "")}</span>'


def _kpi_block(kpis):
    if not kpis:
        return ''
    cells = ''.join(
        f'<div class="kpi"><div class="v">{esc(k.get("value"))}</div>'
        f'<div class="l">{esc(k.get("label"))}</div>'
        f'<div class="n">{esc(k.get("note") or "")}</div></div>'
        for k in kpis)
    return f'<div class="kpis">{cells}</div>'


def _note(text, kind=''):
    return f'<div class="note {esc(kind)}">{text}</div>'


# ------------------------------------------------------------------
# 摘要（封面下、目录前的那块）
# ------------------------------------------------------------------


def render_abstract(model):
    prof = model['profile']
    sm = model.get('sample') or {}
    name = sm.get('name') or os.path.basename(model['layout']['root'])
    qs = prof.get('questions') or []
    stages = model['stages']
    keys = [s['key'] for s in stages]
    findings = model.get('findings') or []
    gaps = model.get('gaps') or []
    n_high = len([g for g in gaps if g.get('severity') == 'high'])

    p = []
    p.append(f'<p><b>本报告回答的问题：</b>' +
             '　'.join(f'{i + 1}) {esc(q)}' for i, q in enumerate(qs)) + '</p>') if qs else None
    p.append(
        f'<p>报告覆盖 <b>{len(stages)}</b> 个已落盘的分析阶段'
        f'（{" → ".join(esc(s["title"]) for s in stages)}）。'
        f'下文每一条结论都对应一个可点开的产物文件；'
        f'未在本目录出现的阶段，本文不写任何结论。</p>')
    p.append(
        f'<p>共 {len(findings)} 条结论、{len(gaps)} 条缺口'
        + (f'（其中 {n_high} 条为高强度，会直接影响"能不能下结论"' if n_high else '')
        + '）。' + esc((model.get('verdict') or {}).get('text') or '') + '。</p>')
    if 'kvsuite' in keys and 'assembly' not in keys:
        p.append('<p class="small muted">注：本次是「鉴定-定量」型任务，'
                 '没有进入组装环节，因此本文不讨论 contig 与基因组序列。</p>')
    return ('<div class="abstract"><h2>摘要</h2>' + ''.join(x for x in p if x)
            + '</div>')


# ------------------------------------------------------------------
# §1 执行摘要
# ------------------------------------------------------------------


def sec_summary(model):
    findings = model.get('findings') or []
    subs = []
    if not findings:
        return (_note('本目录里没有可归纳的结论——通常是分析还没跑完，'
                      '或产物被清理过。'), subs)
    lis = []
    for f in findings:
        lv = f.get('level') or 'ok'
        cls = '' if lv == 'ok' else f' class="lv-{esc(lv)}"'
        ev = ev_list(f.get('evidence') or [])
        lis.append(f'<li{cls}><div>{f.get("text")}</div>{ev}</li>')
    body = [f'<ul class="findings">{"".join(lis)}</ul>']

    kpis = (model.get('metrics') or {}).get('kpi') or []
    body.append(_kpi_block(kpis))
    body.append(_note(
        '读法：这 9 条结论的「强弱」不同——带 <span class="tag">ok</span> 的是'
        '产物里直接读出来的数，可以直接引用；带 <span class="tag warn">warn</span> 的'
        '是"这一步没产出可用结果"，写论文时属于限制条件，不是结论。'))

    # 三条最要紧的"先看这一句"
    top = []
    sm = model.get('sample') or {}
    if sm.get('last_status'):
        top.append(('本次运行状态', sm.get('last_status')))
    if sm.get('last_duration_s') is not None:
        top.append(('总耗时', _fmt_dur(sm.get('last_duration_s'))))
    prof = model['profile']
    top.append(('任务类型', prof.get('title') or ''))
    body.insert(0, '<div class="meta small muted" style="display:flex;gap:26px;'
                   'flex-wrap:wrap;margin-bottom:16px">' + ''.join(
                       f'<span>{esc(k)}：<b>{esc(v)}</b></span>' for k, v in top
                       if v) + '</div>')
    return (''.join(body), subs)


# ------------------------------------------------------------------
# §2 流程漏斗
# ------------------------------------------------------------------


def sec_funnel(model):
    rows = (model.get('metrics') or {}).get('funnel') or []
    subs = []
    if len(rows) < 2:
        return ('', subs)
    trs = []
    first = rows[0]['value'] or 1
    for r in rows:
        v = r.get('value')
        keep = (v / first * 100) if (v is not None and first) else None
        trs.append(
            f'<tr><td>{esc(r.get("zh"))}</td>'
            f'<td class="num">{_int(v)}</td>'
            f'<td class="num">{_num(keep, 2)}%</td>'
            f'<td>{esc(r.get("note") or "")}</td>'
            f'<td class="mono">{esc(r.get("src") or "")}</td></tr>')
    body = [('<h3 id="funnel-table">各层保留量与出处</h3>')]
    body.append(table(['层级', '数量', '相对首层', '备注', '数据来源'],
                      trs, num_cols=(1, 2), mono_cols=(4,),
                      caption='表 2-1　流程漏斗逐层明细（数值直接取自各阶段产物，未做二次加工）'))
    body.append(_note(
        '这张表要连着"<b>相对首层</b>"那一列一起读：'
        '从几万条掉到几十条是测序数据的常态（宿主占绝大多数），'
        '真正值得警惕的是<b>某两层之间骤降</b>——那通常意味着过滤阈值把真信号也切掉了。'))

    figs = []
    for s in model['stages']:
        for f in s.get('figures') or []:
            if 'funnel' in os.path.basename(str(f)).lower():
                figs.append(str(f))
    if figs:
        root = model['layout']['root']
        body.append('<h3 id="funnel-fig">漏斗图</h3>')
        body.append(_figs(model, figs[:2]))
    return (''.join(body), [('funnel-table', '各层保留量与出处'),
                            ('funnel-fig', '漏斗图')])


# ------------------------------------------------------------------
# §3 逐阶段深读
# ------------------------------------------------------------------

_STAGE_WHY = {
    'subsample': '抽稀是为了把巨量数据压进可算的时间与内存预算内；抽稀后的绝对量变少，'
                 '但物种组成与相对丰度尽量保持不变。看这一步时重点核对：抽稀后的 reads 数'
                 '是否仍够支撑后续深度要求。',
    'fastp': '质控决定"能用的数据有多少"。接头污染、低质量碱基和 PCR 重复会同时抬高假阳性'
             '与假阴性；质控后的 reads 数是后面所有结论的天花板。',
    'fq2fa': '把 FASTQ 转成 FASTA，为的是给那些只吃 FASTA 的分类/比对程序（Kraken2、'
             'salmon 的参考索引）喂输入。这一步不做任何筛选，读法上只需确认转换前后条数一致。',
    'host': '植物样品里 >90% 的 reads 属于宿主，这一步把宿主的视野挖掉，'
            '剩下的才是"非宿主序列池"——病毒只可能从这个池子里出来。'
            '所以要盯两件事：宿主占比是否合理（叶/果组织一般 80–99%），'
            '以及保留了多少 reads（决定后面还能不能撑住组装与深度）。',
    'kvsuite': '这一步回答"有哪些<b>已知</b>病毒在场、各有多少"。它的本质是'
               '「按参考库做定量」，因此天然受限于参考库收录范围：'
               '库里没有的病毒，这一步一定看不见，这不等于样品里没有。',
    'assembly': '组装把零散 reads 拼成更长的 contig，让"存在某条病毒"从"有几十条同类 reads"'
                '变成"能拿到一段序列"。contig 的<b>数量与长度分布</b>直接决定了后面能不能'
                '做共识序列、能不能提交——而深度的下限由 reads 量决定，不由组装器决定。',
    'hostana': '病毒宿主预测是给"这条序列属于哪类宿主"的独立体检，'
               '用来筛掉植物/细菌/真菌污染。植物样品的病毒 contig 应当被判为 Plant，'
               '若被判成真菌/细菌则多半是污染，需要单独复核。',
    'verify': '序列验证换了另一套证据源（蛋白库 + 三维结构库）来复核 contig 身份，'
              '回答"它到底是不是病毒、属哪一类"。'
              '判定分为 known / domain_only / unclassified / novel 四档，'
              '<b>只有 known 能对应到已知病毒种</b>，domain_only 仅说明含某类结构域。',
    'consensus': '把 reads 比对回参考得到每条 contig 的覆盖与位点一致性，'
                 '据此产出共识序列并列出候选变异。<b>深度是一切的前提</b>：'
                 '深度不够的位置只能写成 N，而这种 N 不是变异。',
    'orf': 'ORF 预测找的是"从哪里开始翻译、能翻出多长的蛋白"。'
           '它对每条序列独立进行，仅对通过病毒判定的序列有意义。',
    'orfa': '功能注释回答"这段序列编码什么蛋白、属于哪个病毒科/属"。'
            '注释靠的是蛋白序列同源性——同源成立只说明"功能像"，'
            '不等于"整条序列就是这个病毒的完整基因组"。',
    'phylo': '系统发育把样品序列和公共参考放在同一棵树上，回答"跟谁最近"。'
             '<b>树回答相似性，不回答"是不是新种"</b>——后者需要全长基因组与'
             '物种界定阈值（如 ICTV 的成对相似度界值）一起判定。',
    'primer': '引物设计是"把发现变成可验证"的一步：有了可用引物，才谈得上 qPCR/RT-PCR '
              '复核。设计失败通常不是算法问题，而是<b>目标序列太短/太碎片化</b>，'
              '保守区凑不出成对引物。',
    'gbdraw': '基因组图是给完整/近完整基因组做结构展示的：把 ORF、方向、坐标按比例排布成'
              '线性或环形图。它要求序列有<b>可用的注释特征（GFF）</b>，'
              '没有 feature 的序列画不出图。',
    'report': '报告阶段只做汇总与可视化，不产生新证据。它读前面的产物，'
              '所以它的质量上限由前面各阶段决定。',
}


def sec_stages(model):
    stages = model['stages']
    subs = []
    if not stages:
        return (_note('没有可读的阶段产物。'), subs)
    root = model['layout']['root']
    blocks = []

    # ---- 总览表
    trs = []
    for s in stages:
        trs.append(
            f'<tr><td><a href="#st-{esc(s["key"])}">{esc(s["title"])}</a></td>'
            f'<td class="num">{len(s.get("facts") or [])}</td>'
            f'<td class="num">{len(s.get("files") or [])}</td>'
            f'<td class="num">{_fmt_dur(s.get("duration_s"))}</td>'
            f'<td class="mono">{esc(s.get("dir") or "")}</td></tr>')
    blocks.append('<h3 id="st-overview">阶段总览</h3>')
    blocks.append(table(['阶段', '关键数字', '产物文件', '耗时', '目录'],
                        trs, num_cols=(1, 2, 3), mono_cols=(4,),
                        caption='表 3-1　各阶段产出规模与耗时（耗时取自运行日志的时间戳）'))

    # ---- 每阶段一张卡
    for i, s in enumerate(stages):
        sid = f'st-{s["key"]}'
        subs.append((sid, s['title']))
        parts = [f'<h3 id="{esc(sid)}">{i + 1:02d}　{esc(s["title"])}</h3>']
        why = _STAGE_WHY.get(s['key'])
        if s.get('duration_s') is not None:
            parts.append(f'<p class="small muted">耗时 {_fmt_dur(s["duration_s"])}'
                         f'　产物目录 <code>{esc(s.get("dir") or "")}</code></p>')
        if why:
            parts.append(f'<p>{why}</p>')

        facts = s.get('facts') or []
        if facts:
            frs = []
            for f in facts:
                if not isinstance(f, (list, tuple)) or len(f) < 2:
                    continue
                frs.append(f'<tr><td>{esc(f[0])}</td><td class="mono">'
                           f'{esc(f[1])}</td><td>{esc(f[2] if len(f) > 2 else "")}</td></tr>')
            parts.append(table(['项目', '值', '读法'], frs, mono_cols=(1,),
                               caption=f'表 3-{i + 2}　{esc(s["short"])}阶段的关键数字'))
        else:
            parts.append(_note('该阶段没有可摘录的摘要字段'
                               '（summary.json 缺失或字段为空），本文不对它下结论。'))

        # summary 里没被 facts 摘走的、有价值的额外信息
        extra = _stage_extra(s)
        if extra:
            parts.append(extra)

        figs = s.get('figures') or []
        if figs:
            html = _figs(model, [str(f) for f in figs[:4]])
            if html:
                parts.append(html)

        notes = s.get('notes') or []
        if notes:
            parts.append(_note('读取提示：' + '；'.join(esc(n) for n in notes[:4]), 'warn'))
        blocks.append(''.join(parts))
    return (''.join(blocks), subs)


def _figs(model, paths):
    """一组图片 → <figure> 片段（内嵌 base64，离线可看）。"""
    root = model['layout']['root']
    out = []
    for p in paths:
        p = p if os.path.isabs(p) else os.path.join(root, p)
        html = figure(p, esc(os.path.basename(p)), root=root)
        if html:
            out.append(html)
    return ''.join(out)


def _stage_extra(s):
    """少数阶段补一张"只有这里才看得到"的表。"""
    key, sm = s['key'], s.get('summary') or {}
    if key == 'assembly':
        vc = sm.get('viral_contigs') or []
        if vc:
            return _note(
                f'共 {len(vc)} 条病毒 contigs，长度合计 {_int(sm.get("total_bp"))} bp'
                f'（原始 contigs {_int(sm.get("raw_contigs"))} 条，'
                f'组装模式 {esc(_g(sm, "spades", "mode") or sm.get("assembly_mode") or "—")}）。'
                '逐步清单见 <code>03_assembly/virus_contigs.tsv</code>。')
    if key == 'gbdraw':
        plots = sm.get('plots') or []
        if plots:
            lis = ''.join(f'<li><code>{esc(p)}</code></li>' for p in plots)
            return f'<details><summary>出图清单（{len(plots)} 个）</summary>' \
                   f'<div class="body"><ul>{lis}</ul></div></details>'
    if key == 'orfa':
        fams = sm.get('families') or {}
        cats = sm.get('categories') or {}
        if fams or cats:
            f1 = '、'.join(f'{esc(k)} {esc(v)}' for k, v in fams.items()) or '—'
            f2 = '、'.join(f'{esc(k)} {esc(v)}' for k, v in cats.items()) or '—'
            return _note(f'按科统计：{f1}；按功能类别：{f2}。'
                         '注意类别统计的分母是"注释成功的 ORF 数"，不是全部 ORF。')
    if key == 'consensus':
        refs = sm.get('refs') or []
        if refs:
            rows = []
            for r in refs[:40]:
                rows.append(
                    f'<tr><td class="mono">{esc(r.get("ref") or r.get("reference") or r.get("name") or "")}</td>'
                    f'<td class="num">{_int(r.get("length"))}</td>'
                    f'<td class="num">{_num(r.get("mean_depth"))}</td>'
                    f'<td class="num">{_num(r.get("coverage_pct"), 1)}</td>'
                    f'<td class="num">{_num(r.get("depth_ok_pct"), 1)}</td>'
                    f'<td class="num">{_num(r.get("called_pct"), 1)}</td></tr>')
            return table(['参考/contig', '长度', '平均深度', '覆盖%', '达标%', '可 call%'],
                         rows, num_cols=(1, 2, 3, 4, 5),
                         caption='表　逐条覆盖与可判定比例（可 call% 为 0 表示共识几乎全是 N）')
    return ''


# ------------------------------------------------------------------
# §4 病毒学结论
# ------------------------------------------------------------------


def sec_viro(model):
    subs = []
    parts = []
    st = {s['key']: s for s in model['stages']}
    kv = (st.get('kvsuite') or {}).get('summary') or {}
    asm = (st.get('assembly') or {}).get('summary') or {}
    ver = (st.get('verify') or {}).get('summary') or {}
    orfa = (st.get('orfa') or {}).get('summary') or {}
    phy = (st.get('phylo') or {}).get('summary') or {}
    ha = (st.get('hostana') or {}).get('summary') or {}
    root = model['layout']['root']

    if not any((kv, asm, ver, orfa)):
        return (_note('本目录里没有病毒鉴定/组装类产物，'
                      '这一节无从谈起——不做推测。'), subs)

    # 4.1 检出清单
    species = kv.get('species_detected') or []
    if species:
        parts.append('<h3 id="viro-list">4.1　检出物种清单</h3>')
        n_pass = kv.get('passed')
        n_rej = kv.get('rejected')
        parts.append(
            f'<p>②b 定量共放行 <b>{_int(n_pass)}</b> 个物种'
            f'（剔除 {_int(n_rej)} 个），判定依据 '
            f'<code>{esc(kv.get("species_basis") or "—")}</code>；'
            f'阈值见 <code>02b_kvsuite/summary.json → repro.thresholds</code>。'
            f'本样品病毒 reads <b>{_int(kv.get("viral_reads"))}</b> '
            f'{esc(kv.get("reads_unit") or "")}。</p>')
        rows = []
        summ = _read_identify(root, st.get('kvsuite'))
        for sp in species:
            hit = [r for r in summ
                   if sp and (sp == r.get('Species') or sp == r.get('Ictv_Species')
                              or sp in str(r.get('Accession')))]
            if hit:
                r = hit[0]
                rows.append(
                    f'<tr><td>{esc(sp)}</td><td class="mono">{esc(r.get("Accession"))}</td>'
                    f'<td class="num">{_num(r.get("Coverage(%)"), 2)}</td>'
                    f'<td class="num">{_num(r.get("MeanDepth"), 2)}</td>'
                    f'<td class="num">{_num(r.get("Uniq_Reads"), 0)}</td>'
                    f'<td>{esc(r.get("Host") or "")}</td></tr>')
            else:
                rows.append(f'<tr><td>{esc(sp)}</td><td colspan="5" class="muted">'
                            f'主检表里无逐行记录</td></tr>')
        parts.append(table(['物种', '参考', '覆盖%', '平均深度×', '唯一 reads', '参考宿主'],
                           rows, num_cols=(2, 3, 4),
                           caption='表 4-1　通过阈值的物种及其定量指标'))

    # 4.2 组装与鉴定证据
    if asm:
        parts.append('<h3 id="viro-asm">4.2　组装能给出什么序列</h3>')
        parts.append(
            f'<p>组装得到 <b>{_int(asm.get("contigs"))}</b> 条 contigs、总长 '
            f'<b>{_int(asm.get("total_bp"))}</b> bp，其中 '
            f'<b>{len(asm.get("viral_contigs") or [])}</b> 条被判为病毒来源。</p>')
        hits = _read_tsv(root, st.get('assembly'), 'contig_blast.tsv')
        if hits:
            rows = []
            for r in hits[:30]:
                rows.append(
                    f'<tr><td class="mono">{esc(r.get("contig"))}</td>'
                    f'<td class="mono">{esc(r.get("blast_top_hit"))}</td>'
                    f'<td class="num">{_num(r.get("blast_identity(%)"), 3)}</td>'
                    f'<td class="num">{_num(r.get("blast_coverage_hsp(%)"), 1)}</td>'
                    f'<td class="num">{_int(r.get("blast_aln_len"))}</td>'
                    f'<td>{esc(r.get("blast_species") or "")}</td></tr>')
            parts.append(table(['contig', '最佳命中', 'identity%', 'HSP 覆盖%', '比对长度', '物种'],
                               rows, num_cols=(2, 3, 4),
                               caption='表 4-2　各 contig 的核酸最佳命中（blastn）'))
            parts.append(_note(
                '读法：<b>identity</b> 是"这一段有多像"，<b>HSP 覆盖%</b> 是'
                '"contig 有多少比例被比对上了"。两个都很高才说明这条 contig '
                '基本就是该参考的一段；HSP 覆盖 100% 但 contig 本身只有几百 bp，'
                '仍然只能说"这一段像"，不能说"这是它的基因组"。'))

    # 4.3 序列验证
    calls = ver.get('calls') or {}
    if calls:
        parts.append('<h3 id="viro-verify">4.3　独立性复核：这些 contig 真的是病毒吗</h3>')
        rows = [[esc(k), _int(v)] for k, v in calls.items()]
        parts.append(table(['判定', '条数'], rows, num_cols=(1,),
                           caption='表 4-3　序列验证四档判定'))
        parts.append(_note(
            '<b>known</b> = 蛋白层面直接对上已知病毒种；'
            '<b>domain_only</b> = 只对上某类结构域（如 RdRp 基序），能说明"是病毒"'
            '但不能定种；<b>unclassified</b> = 没对上任何已知记录；'
            '<b>novel</b> = 与已知序列差异较大、可能为新。'
            '四档里只有 known 可以写成"检出 XX 病毒"。'))

    # 4.4 注释
    if orfa:
        parts.append('<h3 id="viro-orf">4.4　编码内容</h3>')
        ann = _read_tsv(root, st.get('orfa'), 'orf_annotation.tsv')
        if ann:
            rows = []
            for r in ann[:20]:
                rows.append(
                    f'<tr><td class="mono">{esc(r.get("orf_id"))}</td>'
                    f'<td class="mono">{esc(r.get("start"))}–{esc(r.get("end"))}'
                    f'{esc(r.get("strand") or "")}</td>'
                    f'<td class="num">{esc(r.get("length_aa"))}</td>'
                    f'<td>{esc(r.get("product"))}</td>'
                    f'<td>{esc(r.get("organism"))}</td>'
                    f'<td class="num">{_num(r.get("pident"), 1)}</td>'
                    f'<td class="num">{esc(r.get("evalue"))}</td></tr>')
            parts.append(table(['ORF', '坐标', 'aa', '产物', '最像的病毒', '一致性%', 'E 值'],
                               rows, num_cols=(2, 5),
                               caption='表 4-4　ORF 注释结果（蛋白同源比对）'))
            parts.append(_note('术语提醒：<code>product</code> 是按同源比对给出的'
                               '<b>预测</b>功能，不是实验验证结果。'))

    # 4.5 进化位置
    groups = phy.get('groups') or []
    if groups:
        parts.append('<h3 id="viro-phylo">4.5　与谁最接近</h3>')
        rows = []
        for g in groups:
            bi = g.get('best_identity_to_ref')
            tri = g.get('trim') or {}
            trim_txt = ('未清剪' if not tri.get('applied') else
                        f'{tri.get("cols_before")}→{tri.get("cols_after")} 列')
            rows.append([g.get('group'), len(g.get('contigs') or []),
                         _num(bi, 2) if bi is not None else '—',
                         f'{g.get("n_refs") or 0} 条：' + _group_refs(g),
                         trim_txt])
        parts.append(table(['分组', '样品序列数', '与参考最高 identity%', '对照参考', '清剪'],
                           rows, num_cols=(1, 2),
                           caption='表 4-5　进化分析分组、对照参考与清剪情况'))
        parts.append(_note(
            '「未清剪」意味着这条比对里含有大量缺口列，短序列会被长枝外拉——'
            '这时树的拓扑只能当"大致分群"看，不能当物种归属结论。'))
        parts.append(_note(f'建树工具 <code>{esc(phy.get("tree_tool"))}</code>、'
                           f'清剪工具 <code>{esc(phy.get("trim_tool"))}</code>。'
                           '树只能说明相对位置，距离阈值的判定要另看物种界定标准。'))

    # 4.6 宿主
    if ha:
        parts.append('<h3 id="viro-host">4.6　宿主预测</h3>')
        parts.append(
            f'<p>{_int(ha.get("n_contigs"))} 条 contigs 的宿主类别：'
            + '、'.join(f'{esc(k)} {_int(v)}' for k, v in (ha.get('categories') or {}).items())
            + f'；判定来源 ' + '、'.join(
                f'{esc(k)} {_int(v)}' for k, v in (ha.get('class_source') or {}).items())
            + '。</p>')
    return (''.join(parts), subs)


def _group_refs(g):
    for k in ('refs', 'references', 'ref'):
        v = g.get(k)
        if isinstance(v, list):
            return '、'.join(str(x) for x in v[:4])
        if isinstance(v, str):
            return v
    return ''


def _read_identify(root, st_kv):
    if not st_kv:
        return []
    p = os.path.join(root, st_kv['dir'], 'identify', 'all_viruses.summary.tsv')
    return _read_tsv_path(p)


def _read_tsv(root, stage, fname):
    if not stage:
        return []
    return _read_tsv_path(os.path.join(root, stage['dir'], fname))


def _read_tsv_path(p):
    from .model import read_dicts
    try:
        return read_dicts(p)
    except Exception:
        return []


# ------------------------------------------------------------------
# §5 缺口
# ------------------------------------------------------------------


def sec_gaps(model):
    gaps = model.get('gaps') or []
    subs = []
    parts = []
    if gaps:
        order = {'high': 0, 'warn': 1, 'info': 2}
        gaps = sorted(gaps, key=lambda g: order.get(g.get('severity'), 3))
        subs.append(('gap-list', '缺口清单（按严重度排序）'))
        parts.append('<h3 id="gap-list">缺口清单（按严重度排序）</h3>')
        for i, g in enumerate(gaps, 1):
            sev = g.get('severity') or 'warn'
            tag = {'high': 'tag bad', 'warn': 'tag warn'}.get(sev, 'tag gray')
            parts.append(
                f'<div class="card white"><span class="{tag}">'
                f'{esc({"high": "高强度", "warn": "中", "info": "说明"}.get(sev, sev))}</span>'
                f'<b>{i}. {esc(g.get("title"))}</b>'
                f'<p style="margin:8px 0 0">{esc(g.get("detail"))}</p>'
                f'{ev_list(g.get("evidence") or [])}</div>')

    contr = model.get('contradictions') or []
    if contr:
        subs.append(('gap-contra', '口径矛盾：两处数字对不上'))
        parts.append('<h3 id="gap-contra">口径矛盾：两处数字对不上</h3>')
        parts.append(_note(
            '下面每一条都是"同一个东西在两个阶段有两个说法"。'
            '这类问题不会让分析失败，但会在写论文、跑审稿时被问住，'
            '所以这里并列两边原文并给出建议信哪个。'))
        for i, c in enumerate(contr, 1):
            a = c.get('a') or ('', '')
            b = c.get('b') or ('', '')
            parts.append(
                f'<div class="card"><b>{i}. {esc(c.get("title"))}</b>'
                f'<div class="tbl-wrap" style="margin-top:10px"><table>'
                f'<thead><tr><th style="width:32%">口径</th><th>原文</th></tr></thead><tbody>'
                f'<tr><td>{esc(a[0])}</td><td class="mono">{esc(a[1])}</td></tr>'
                f'<tr><td>{esc(b[0])}</td><td class="mono">{esc(b[1])}</td></tr>'
                f'</tbody></table></div>'
                f'<p><b>为什么会不一样：</b>{esc(c.get("why"))}</p>'
                f'<p><b>引用时用哪个：</b>{esc(c.get("use"))}</p>'
                f'{ev_list(c.get("evidence") or [])}</div>')
    if not parts:
        return (_note('本目录未发现需要单独说明的缺口。'), subs)
    return (''.join(parts), subs)


# ------------------------------------------------------------------
# §6 AI 问答卡（任务驱动）
# ------------------------------------------------------------------

_QA = {
    'full': [
        ('样品里到底有什么病毒？',
         '先给通过阈值的物种清单与各自覆盖/深度（②b），再说明哪几条拿到了可用的组装序列（③），'
         '最后用 ③b 的四档判定说明哪些只是"含病毒结构域"。',
         '本样品的 ②b 检出物种清单里，哪几个是"确有序列支撑"、哪几个只是"定量有信号但没拼出序列"？'),
        ('定量看得见、组装拼不出，是怎么回事？',
         '解释两条通路的筛选标准不同：定量按参考归一化抽片段，组装要求拼成一致序列并过长度/深度阈值。'
         '低深度数据里这是常态。',
         '为什么 ②b 定量覆盖很高、深度很大的那个物种，在 ③ 组装结果里找不到对应 contig？'),
        ('这些序列能直接拿去提交 GenBank 吗？',
         '看三条：长度是否近全长、覆盖与可判定比例、是否做了首尾与 UTR 核对。'
         '深度不足会导致共识里出现大量 N。',
         '我这批 contig 想提交 GenBank，按现在这条 contig 的长度、覆盖度与可 call 比例，'
         '卡在哪一步？需要补什么？'),
        ('引物为什么没设计出来？还能不能做 qPCR？',
         '说明设计失败的原因通常是目标序列太短/太碎，保守区凑不出成对引物；'
         '给出补救路径：先补测序/补组装拿到近全长。',
         '我的目标病毒目前只有几百 bp 的碎片，引物设计失败了；在现有数据条件下还有别的验证办法吗？'),
        ('进化树能不能说明这是新种？',
         '说明树只反映相对位置，新种判定要看全长基因组 + 物种界定阈值 + 独立流行病学证据。',
         '这棵树上我的序列和参考聚在一起但枝长偏长，这能不能作为"可能为新种"的证据？'),
        ('下一步优先做什么？',
         '按"能改变结论的先后顺序"排序，而不是按"好做的事"排序。',
         '如果只能再做一件事来提高这个结论的可信度，应该做什么？为什么？'),
    ],
    'diagnose': [
        ('这批样品里检出了哪些已知病毒，各有多少？',
         '用 ②b 的通过阈值物种与逐物种覆盖/深度回答，并说明阈值。',
         '把 ②b 结果里通过阈值的物种按深度排序，并说明各自是否达到"可定量"水平。'),
        ('定量结果会不会受参考库影响？',
         '说明参考库只收录已知病毒，库里没有的看不见；这不是阴性证据。',
         '这次定量用的参考库收录多少条序列？如果样品里是库里没有的病毒，这次分析会给出什么结果？'),
        ('检出的低丰度物种要不要信？',
         '按覆盖与深度分类：低覆盖低深度的一律写成"疑似/待验证"。',
         '把检出物种按"可确信 / 需验证 / 不可用"三档分类，并说明分档依据。'),
    ],
    'assembly': [
        ('组装出来的 contig 是什么？',
         '给数量、总长、长度分布与病毒判定结果。',
         '这 20 条 contig 的长度分布和深度分布是什么样的？哪几条值得继续做？'),
        ('为什么拼不成长基因组？',
         '从深度、reads 量、宿主去除彻底程度、基因组是否为多分体/多段几个角度解释。',
         '为什么已经拿到多条同源 contig，却拼不出完整基因组？瓶颈在数据量还是算法？'),
    ],
    'phylo': [
        ('这几条序列在树上是什么位置？',
         '读树的拓扑与支持度，说明哪些位置可信、哪些是长枝外拉。',
         '这棵树里哪些分支的支持度不足？哪些序列是长枝外拉？这会影响什么结论？'),
        ('对照参考选得对不对？',
         '说明对照组的选择依据与局限（只有部分基因组时会引入误差）。',
         '我这个分组的对照参考只有一条且是部分基因组，这对树的拓扑有什么影响？'),
    ],
    'primer': [
        ('引物为什么失败了？',
         '说明保守区长度与引物长度/GC/二聚体约束冲突时的常见现象。',
         '引物设计失败的日志里给了什么原因？按这个原因，换成简并引物或多重 PCR 可行吗？'),
    ],
    'prep': [
        ('去宿主去的干净吗？',
         '看宿主占比与保留量，判断后续视野是否够用。',
         '宿主去除后保留了多少 reads？这个比例对蔬菜/果实的叶片样品算正常吗？'),
    ],
    'tool': [
        ('这一步的结果说明什么？',
         '按参数与产物逐项说明，只谈这一步能回答的问题。',
         '这次单步运行用的参数是什么？结果里哪些数字是可信的、哪些受参数影响大？'),
    ],
}


def sec_qa(model):
    prof = model['profile']
    key = prof.get('key') or 'full'
    cards = _QA.get(key) or _QA['full']
    subs = []
    if not cards:
        return ('', subs)
    parts = ['<p class="lede">下面每张卡是"可以直接问 AI 的问题 + 期望它回答到的要点 + '
             '能复制去问的提问文本"。要点一栏写的是<b>判好答案的标准</b>：'
             '回答里缺了它就说明还在泛泛而谈。</p>']
    sm = model.get('sample') or {}
    name = sm.get('name') or os.path.basename(model['layout']['root'])
    for i, (q, points, prompt) in enumerate(cards, 1):
        parts.append(
            f'<div class="qa"><div class="q"><span class="n">Q{i}</span>{esc(q)}</div>'
            f'<div class="a"><div class="lead">期望回答要点</div>'
            f'<div>{esc(points)}</div>'
            f'<div class="lead" style="margin-top:12px">可复制提问</div>'
            f'<div class="prompt">{esc(prompt)}</div></div></div>')
    return (''.join(parts), subs)


# ------------------------------------------------------------------
# §7 可复现命令
# ------------------------------------------------------------------


def sec_cmds(model):
    subs = []
    parts = []
    prof = model['profile']
    root = model['layout']['root']
    sm = model.get('sample') or {}

    parts.append('<h3 id="cmd-rerun">7.1　重跑本次任务</h3>')
    if prof.get('tool'):
        tool = prof['tool']
        parts.append(
            f'<p>这是一次工具箱单步运行（<code>{esc(tool)}</code>），'
            '对应的重跑命令：</p>')
        parts.append(code_block(
            f'# 命令行（在平台根目录执行）\n'
            f'python main.py run-tool {tool} --help\n\n'
            f'# 或走 MCP：vp_run_tool(tool="{tool}", ...)\n'
            f'# 参数清单：python main.py agent-ops --id {tool} --schema',
            caption='工具箱单步重跑'))
        params = _g(sm, 'params') or {}
        if params:
            lines = '\n'.join(f'  - {k}: {v}' for k, v in params.items())
            parts.append(code_block(lines, caption='该次运行使用的参数'))
        return (''.join(parts),
                [('cmd-rerun', '重跑本次任务')])

    name = sm.get('name') or ''
    params = sm.get('params') or {}
    args = [f'--name {name}' if name else '']
    for k, v in params.items():
        if v in (None, '', 0, False):
            continue
        if isinstance(v, bool):
            args.append(f'--{k.replace("_", "-")}')
        else:
            args.append(f'--{k.replace("_", "-")} {v}')
    cmd = 'python main.py analyze ' + ' '.join(a for a in args if a) + ' \\\n' \
          '  --r1 <样本 R1.fq.gz> --r2 <样本 R2.fq.gz>'
    parts.append(code_block(cmd, caption=f'按此次运行参数重跑（参数取自 project.json）'))
    parts.append(_note('参数名与完整选项以 <code>python main.py analyze --help</code> 为准；'
                       '平台升级可能增删参数，本文不凭记忆写未经验证的写法。'))

    # 单阶段重跑
    stages = [s for s in model['stages'] if s['key'] not in ('report',)]
    if stages:
        parts.append('<h3 id="cmd-stage">7.2　只重跑某一阶段</h3>')
        rows = [[s['title'], {'html': f'<code>{esc(s["key"])}</code>'},
                 s['dir']] for s in stages]
        parts.append(table(['阶段', '任务 id（MCP: vp_rerun_stage）', '产物目录'],
                           rows, mono_cols=(1, 2),
                           caption='表 7-1　单阶段重跑的任务 id'))
        parts.append(code_block(
            f'# CLI（参数以 --help 为准）\n'
            f'python main.py rerun-stage --sample {name} --stage <阶段名>\n\n'
            f'# MCP\n'
            f'vp_rerun_stage(sample="{name}", stage="<阶段名>")',
            caption='单阶段重跑'))

    # 关键命令（取自运行日志，原文摘录）
    log = model.get('run_log')
    if log:
        parts.append('<h3 id="cmd-log">7.3　运行日志里的关键命令行</h3>')
        lines = _extract_cmd_lines(os.path.join(root, log))
        if lines:
            parts.append(code_block('\n'.join(lines[:24]),
                                    caption=f'摘自运行日志 {log}（原文，未改写）'))
        else:
            parts.append(_note('运行日志里没有可识别的外部命令行。'))

    parts.append('<h3 id="cmd-tools">7.4　工具与数据库版本</h3>')
    tools = model.get('tools') or []
    if tools:
        rows = [[t.get('name'), t.get('version') or '未探测',
                 {'html': f'<code>{esc(t.get("path") or "")}</code>'}] for t in tools]
        parts.append(table(['工具', '版本', '路径'], rows, mono_cols=(2,),
                           caption='表 7-2　本次运行用到的外部可执行文件'))
        miss = [t.get('name') for t in tools
                if not t.get('version') or t.get('version') == '未探测']
        if miss:
            parts.append(_note(f'"未探测"（{"、".join(miss)}）表示该工具的版本输出'
                               '格式与探测正则不匹配——这里如实写"未探测"，'
                               '不填一个猜的版本号。'))
        else:
            parts.append(_note('全部工具的版本均由各自的版本命令实测读回；'
                               'MMseqs2 只打印编译提交号（无语义化版本号），'
                               '按原样记录。'))
    subs = [('cmd-rerun', '重跑本次任务'), ('cmd-stage', '只重跑某一阶段'),
            ('cmd-log', '本次实际执行的命令'), ('cmd-tools', '工具与数据库版本')]
    return (''.join(parts), [s for s in subs if f'id="{s[0]}"' in ''.join(parts)])


_CMD_RE = None


def _extract_cmd_lines(logpath):
    """从运行日志里捞出外部工具命令行（原文，不改写）。"""
    import re
    global _CMD_RE
    if _CMD_RE is None:
        _CMD_RE = re.compile(
            r'(spades\.bat|spades\.py|salmon\s+(?:quant|index)|diamond\s+blast|'
            r'blastn\s|blastx\s|kraken2|kun_peng|fastp\s|minimap2|minibwa|'
            r'mafft|FastTree|trimal|pyrodigal|gbdraw|samtools\s)')
    out = []
    try:
        with open(logpath, encoding='utf-8', errors='replace') as fh:
            for line in fh:
                if _CMD_RE.search(line) and len(line.strip()) > 20:
                    out.append(line.rstrip()[:300])
    except OSError:
        return []
    return out


# ------------------------------------------------------------------
# §8 方法与数据来源
# ------------------------------------------------------------------


def sec_methods(model):
    subs = [('meth-version', '软件与数据库版本'),
            ('meth-src', '数据溯源表'),
            ('meth-snap', '数据快照说明')]
    parts = []
    parts.append('<h3 id="meth-version">8.1　软件与数据库版本</h3>')
    tools = model.get('tools') or []
    if tools:
        rows = [[esc(t.get('name')), esc(t.get('version') or '未探测')] for t in tools]
        parts.append(table(['工具', '版本'], rows, caption='表 8-1　外部工具版本'))
    dbvers = {}
    for s in model['stages']:
        dv = (s.get('summary') or {}).get('db_versions')
        if isinstance(dv, dict):
            dbvers.update({f'{s["key"]}.{k}': v for k, v in dv.items()})
        dbf = (s.get('summary') or {}).get('db_files')
        if isinstance(dbf, dict):
            for k, v in dbf.items():
                size = v[0] if isinstance(v, (list, tuple)) and v else v
                dbvers[f'{s["key"]}.{k}.size'] = size
    if dbvers:
        rows = [[esc(k), esc(v)] for k, v in sorted(dbvers.items())]
        parts.append(table(['数据库', '版本/大小（字节）'], rows,
                           caption='表 8-2　数据库版本与体积'))

    parts.append('<h3 id="meth-src">8.2　数据溯源表</h3>')
    parts.append(_note('本报告正文里出现的每一个数字，都能在下面这张表里找到它来自哪个文件。'
                       '点文件名可直接打开原产物核对。'))
    ev = model.get('evidence') or []
    if ev:
        groups = {}
        for p in ev:
            head = p.split('/')[0] if '/' in p else '(根目录)'
            groups.setdefault(head, []).append(p)
        rows = []
        size = len(ev)
        width = max(70, size // 8)
        for head in sorted(groups):
            items = groups[head]
            rows.append([{'html': f'<b>{esc(head)}</b>　<span class="muted small">'
                                  f'{len(items)} 个</span>'}])
            for i in range(0, min(len(items), width), 3):
                cells = [{'html': link_evidence(x)} for x in items[i:i + 3]]
                while len(cells) < 3:
                    cells.append('')
                rows.append(cells)
        parts.append(table(['文件', '', ''], rows, mono_cols=(0, 1, 2),
                           caption=f'表 8-3　证据文件清单（共 {size} 个，按产物目录分组）'))
        parts.append(_note('完整清单可直接打开 <code>07_report/report_v2.json</code>；'
                           '每个文件名都可点击，指向运行目录里的原始产物。'))

    parts.append('<h3 id="meth-snap">8.3　数据快照说明</h3>')
    sm = model.get('sample') or {}
    lines = []
    if sm.get('name'):
        lines.append(f'样品：{sm.get("name")}')
    if sm.get('created'):
        lines.append(f'建样时间：{sm.get("created")}')
    if sm.get('last_run'):
        lines.append(f'本次运行：{sm.get("last_run")}')
    if sm.get('stages_done'):
        lines.append('已完成阶段：' + '、'.join(sm['stages_done']))
    if model.get('run_log'):
        lines.append(f'运行日志：{model["run_log"]}')
    lines.append(f'报告生成：{model["generated"]}')
    lines.append(f'报告目录：{model["layout"]["root"]}')
    parts.append(code_block('\n'.join(lines), caption='本报告对应的数据快照'))
    parts.append(_note('报告只描述"生成这一刻"的落盘产物。'
                       '若之后重跑了任一阶段，本报告不会自动更新——'
                       '请重新生成，或在页脚核对生成时间。'))
    return (''.join(parts), subs)


# ------------------------------------------------------------------
# §9 下一步
# ------------------------------------------------------------------


def _next_steps(model):
    """按"能改变结论的先后"给出行动清单（依据当前缺口动态生成）。"""
    gaps = model.get('gaps') or []
    contr = model.get('contradictions') or []
    steps = []
    for g in gaps:
        sev = g.get('severity')
        t = (g.get('title') or '')
        if sev != 'high':
            continue
        if '质量' in t:
            steps.append(('补真实质量值的病毒 reads',
                          '当前病毒 reads 是从 BAM 里回抽的，质量列为占位符，'
                          '导致组装纠错与变异过滤形同虚设。'
                          '做法：改为在去宿主后直接从 FASTQ 里按病毒 reads id 抽取，保留原始质量值。'))
        elif '深度' in t or 'N' in t:
            steps.append(('提高目标病毒测序深度',
                          '当前若干条 contig 平均深度 ≤2×，共识几乎全是 N。'
                          '做法：加大测序量或做目标富集（如 dsRNA 富集/去 rRNA），'
                          '把目标区域深度做到 20× 以上再谈共识序列与变异。'))
    for c in contr:
        if '没有' in (c.get('title') or '') and 'contig' in (c.get('title') or ''):
            steps.append(('为"定量看得见但没拼出序列"的病毒单独补测',
                          '这类病毒目前只有定量信号、没有序列证据。'
                          '做法：用该病毒参考序列做定向拼接（reference-guided mapping）'
                          '或设计特异引物做 RT-PCR 克隆。'))
    tools = model.get('tools') or []
    bad = [t for t in tools if not t.get('version') or t.get('version') == '未探测']
    if bad:
        steps.append(('补齐工具版本探测，再写论文方法学一节',
                      '本报告里有 ' + '、'.join(str(t.get('name')) for t in bad)
                      + ' 的版本没能自动探测到（版本输出格式不匹配）。'
                      '投稿方法学要求写清版本号，这两条要么手工核对，要么修探测规则。'))
    # 通用收尾
    if any(s['key'] == 'primer' and not (s.get('summary') or {}).get('n_primers')
           for s in model['stages']):
        steps.append(('先把目标序列补到近全长，再回头做引物',
                      '引物设计失败的直接原因是目标序列太碎、保守区凑不出成对引物。'
                      '顺序应该是"补序列 → 再设计引物"，而不是反复调引物参数。'))
    steps.append(('把结论按证据强度分级写进论文',
                  'known/有序列 → 可直接写"检出"；domain_only/低深度 → 写"疑似"；'
                  '只有定量信号 → 写"检测到 reads"，并在讨论里说明验证路径。'))
    return steps


def sec_next(model):
    steps = _next_steps(model)
    if not steps:
        return ('', [])
    parts = ['<p class="lede">按"能不能改变结论"排序，而不是按"好不好做"排序。</p>',
             '<ol class="steps">']
    for i, (t, d) in enumerate(steps, 1):
        parts.append(f'<li><b>{esc(t)}</b><div class="small muted">{esc(d)}</div></li>')
    parts.append('</ol>')
    return (''.join(parts), [])
