#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
toolbox_plots.py — 专项工具结果可视化补全（内容感知，纯附加层）

背景（2026-10-12 审计）：以下工具此前只落表格/JSON，结果面板没有配图——
引物设计、CRISPR 设计、dsRNA 设计、结构比较(structcmp)、RDP5 重组、
重组预筛(align_qc)、候选验证分类、miRNA 靶标、MOT 迁移矩阵、元数据治理。

本模块不改变任何工具的产物：run 目录扫到已知表格产物时，就地补出
配图（PNG 300dpi + PDF），工具结果面板按目录扫描列产物，图自动出现。
所有函数防御式实现：列名模糊匹配、缺列/空表/解析失败一律跳过，绝不抛错
拖垮工具任务本身（与 io_utils.safe_plot 同纪律）。

入口：add_plots(run_dir, logger=None) -> list[str]（写出的图片路径）
"""

from __future__ import annotations

import glob
from collections import Counter
import json
import os
import sys

if sys.platform == "win32" and sys.stdout is not None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import numpy as np
import pandas as pd
import textwrap

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

try:                                    # 包内
    from . import sci_palette as sp
except ImportError:                     # 独立兜底
    _ROOT = os.path.dirname(os.path.abspath(__file__))
    if _ROOT not in sys.path:
        sys.path.insert(0, _ROOT)
    from Virus_Platform_Core import sci_palette as sp

sp.apply_sci_style()

# Windows 上 arialbd.ttf 常不在 matplotlib 默认索引里（只有 Arial regular），
# fontweight='bold' 会退回 400 并打 findfont 警告；显式注册一次。
try:
    import matplotlib.font_manager as _fm
    _bd = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts",
                       "arialbd.ttf")
    if os.path.isfile(_bd):
        _fm.fontManager.addfont(_bd)
except Exception:
    pass

_GREY = sp.OKABE["grey"]
_BLUE = sp.OKABE["blue"]
_VERM = sp.OKABE["vermillion"]


# ------------------------------------------------------------------
# 公共小件
# ------------------------------------------------------------------
def _find(run_dir: str, *patterns: str) -> str | None:
    for pat in patterns:
        hits = sorted(glob.glob(os.path.join(run_dir, pat)))
        if hits:
            return hits[0]
    return None


def _read_tab(path: str) -> pd.DataFrame:
    sep = "\t" if path.lower().endswith((".tsv", ".txt")) else ","
    return pd.read_csv(path, sep=sep, dtype=str, keep_default_na=False)


def _num(df: pd.DataFrame, *keys: str) -> pd.Series | None:
    """按子串模糊找数值列（不区分大小写）。"""
    for c in df.columns:
        lc = c.lower()
        if any(k in lc for k in keys):
            v = pd.to_numeric(df[c], errors="coerce")
            if v.notna().sum() >= 2:
                return v
    return None


def _is_text(s: pd.Series) -> bool:
    """pandas ≥3 的字符串列是专用 str dtype，不再等于 object——按
    「非数值且非布尔」判文本，两个版本通吃。"""
    try:
        return not (pd.api.types.is_numeric_dtype(s)
                    or pd.api.types.is_bool_dtype(s))
    except (TypeError, ValueError):
        return False


def _text(df: pd.DataFrame, *keys: str, min_uniq: int = 2,
          max_uniq: int = 200) -> pd.Series | None:
    for c in df.columns:
        lc = c.lower()
        if any(k in lc for k in keys) and _is_text(df[c]):
            u = df[c].nunique()
            if min_uniq <= u <= max_uniq:
                return df[c]
    return None


def _save(fig, run_dir: str, stem: str) -> list:
    """PNG + SVG + PDF 三格式落盘（SVG 供结果图二次排版）。"""
    paths = []
    for ext, kw in (("png", dict(dpi=300)), ("svg", {}), ("pdf", {})):
        fp = os.path.join(run_dir, f"{stem}.{ext}")
        try:
            fig.savefig(fp, bbox_inches="tight", **kw)
            paths.append(fp)
        except Exception:
            pass
    plt.close(fig)
    return paths


def _wrap(s, width: int = 26) -> str:
    return textwrap.fill(str(s), width=width)


def _multi_hist(ax, series: pd.Series, title: str, xlabel: str,
                color: str) -> None:
    v = series.dropna()
    if len(v) == 0:
        ax.text(0.5, 0.5, "No data", ha="center", va="center",
                transform=ax.transAxes, color=_GREY)
        ax.set_title(title, fontsize=9)
        return
    ax.hist(v, bins=min(30, max(8, int(np.sqrt(len(v))) * 2)),
            color=color, edgecolor="white", linewidth=0.4)
    ax.axvline(float(v.median()), color="black", ls="--", lw=0.9,
               label=f"median {v.median():.2f}")
    ax.legend(frameon=True, framealpha=0.85, edgecolor="none", fontsize=7)
    ax.set_title(title, fontsize=9)
    ax.set_xlabel(xlabel, fontsize=8)
    # 剪掉两端最外侧刻度：多面板并排时，左面板最右刻度与右面板最左刻度
    # 都悬在面板边界外的同一行（tight_layout 的横向间距不算底部刻度的
    # 横向外溢），必相撞——体检实抓 "65"×"-0.5" 161px²（2026-10-13）。
    ax.xaxis.set_major_locator(
        matplotlib.ticker.MaxNLocator(nbins=7, prune='both'))


# ------------------------------------------------------------------
# 1. structcmp：同一性矩阵热图
# ------------------------------------------------------------------
def plot_identity_heatmap(run_dir: str) -> list:
    p = _find(run_dir, "identity_matrix.tsv")
    if not p:
        return []
    df = _read_tab(p)
    names = list(df.columns[1:])
    m = df.iloc[:, 1:].apply(pd.to_numeric, errors="coerce").to_numpy()
    if m.size == 0 or not np.isfinite(m).any():
        return []
    n = len(names)
    size = max(4.0, min(12.0, 0.42 * n + 2.4))
    fig, ax = plt.subplots(figsize=(size, size * 0.86))
    im = ax.imshow(m, cmap="viridis", vmin=np.nanmin(m), vmax=np.nanmax(m))
    ax.set_xticks(range(n)); ax.set_yticks(range(n))
    rot = 45 if n >= 4 else 0
    ax.set_xticklabels([_wrap(x, 14) for x in names],
                       rotation=rot, ha="right" if rot else "center", fontsize=7)
    ax.set_yticklabels([_wrap(x, 22) for x in names], fontsize=7)
    if n <= 14:
        for i in range(n):
            for j in range(n):
                ax.text(j, i, f"{m[i, j]:.1f}", ha="center", va="center",
                        fontsize=6, color="white" if m[i, j] < np.nanmean(m) else "black")
    fig.colorbar(im, ax=ax, shrink=0.8, label="Identity (%)")
    ax.set_title("Pairwise identity matrix (structcmp)", fontsize=11)
    return _save(fig, run_dir, "identity_matrix_heatmap")


# ------------------------------------------------------------------
# 2. RDP5 重组事件概览
# ------------------------------------------------------------------
def plot_rdp_events(run_dir: str) -> list:
    p = _find(run_dir, "events.tsv")
    if not p:
        return []
    df = _read_tab(p)
    if df.empty:
        return []
    startc = next((c for c in df.columns
                   if any(k in c.lower() for k in ("start", "begin", "left"))), None)
    endc = next((c for c in df.columns
                 if any(k in c.lower() for k in ("end", "stop", "right"))), None)
    namec = next((c for c in df.columns
                  if _is_text(df[c]) and df[c].nunique() >= 2
                  and not any(k in c.lower() for k in ("start", "end", "begin", "stop",
                                                       "left", "right", "pval", "p-"))), None)
    if not (startc and endc and namec):
        return []
    df = df.copy()
    df["_s"] = pd.to_numeric(df[startc], errors="coerce")
    df["_e"] = pd.to_numeric(df[endc], errors="coerce")
    df = df.dropna(subset=["_s", "_e"])
    if df.empty:
        return []
    counts = df.groupby(namec).size().sort_values(ascending=True).tail(30)
    fig, (ax1, ax2) = plt.subplots(
        1, 2, figsize=(11.5, max(3.0, 0.30 * len(counts) + 1.2)),
        gridspec_kw={"width_ratios": [1, 2.1], "wspace": 0.42})
    ax1.barh([_wrap(x, 20) for x in counts.index], counts.values,
             color=_BLUE, edgecolor="white", linewidth=0.4)
    for i, v in enumerate(counts.values):
        ax1.text(v, i, f" {v}", va="center", fontsize=8)
    ax1.set_xlabel("Recombination events")
    ax1.set_title("Events per sequence", fontsize=10)
    sp.despine()
    # 断点区间条带
    span = float(df[["_s", "_e"]].to_numpy().max()) or 1.0
    ymap = {name: i for i, name in enumerate(counts.index)}
    for _, r in df.iterrows():
        if r[namec] in ymap:
            ax2.broken_barh([(min(r["_s"], r["_e"]),
                              abs(r["_e"] - r["_s"]) or span * 0.002)],
                            (ymap[r[namec]] - 0.32, 0.64),
                            facecolors=_VERM, edgecolors="none", alpha=0.75)
    ax2.set_yticks(range(len(counts)))
    ax2.set_yticklabels([_wrap(x, 20) for x in counts.index], fontsize=7)
    ax2.set_xlim(0, span * 1.02)
    ax2.set_xlabel("Genome position (recombinant segment)")
    ax2.set_title("Breakpoint intervals", fontsize=10)
    sp.despine()
    return _save(fig, run_dir, "rdp_events_overview")


# ------------------------------------------------------------------
# 2b. RDP5 断点分布图（复刻参考管线 RDP5_RBDP_Rgrapher/MainFile.R）
# ------------------------------------------------------------------
def plot_rdp_breakpoint_distribution(run_dir: str) -> list:
    """断点分布图：RBN 曲线 + 99% CI 分层填充 + 梯子状基因轨道 + 断点位置条。

    数据源（rdp/ 子目录）：
      rdp5 breakpoint distribution.csv          Position/RBN(+4 CI 列)
      …BreakpointPositions.csv                  断点位置（单列，画顶部刻度条）
      gene_map.json（可选）                     比对坐标基因轨道
    RDP5 对含 Unknown 亲本的事件不产出分布（见 rdp5_engine 兜底说明）：
    2 列合成版没有 CI 列 → 只画 RBN 层，不画 CI 带。
    """
    p = _find(run_dir, "*breakpoint distribution.csv")
    if not p:
        return []
    # 只认 RBN 版（"breakpoint number"）；cluster probability 版是 log(p) 分布
    try:
        df = _read_tab(p)
        if df.empty:
            return []
        posc = next((c for c in df.columns if "position" in c.lower()), None)
        rbnc = next((c for c in df.columns
                     if "breakpoint number" in c.lower()), None)
        upc = next((c for c in df.columns if "upper 99" in c.lower()), None)
        loc = next((c for c in df.columns if "lower 99" in c.lower()), None)
        if not (posc and rbnc):
            return []
        x = pd.to_numeric(df[posc], errors="coerce")
        rbn = pd.to_numeric(df[rbnc], errors="coerce")
        ok = x.notna() & rbn.notna()
        x, rbn = x[ok].to_numpy(), rbn[ok].to_numpy()
        if len(x) < 2:
            return []
        # upper/lower CI 有列才画（2 列合成版没有）
        upper = (pd.to_numeric(df[upc], errors="coerce").to_numpy()
                 if upc else None)
        lower = (pd.to_numeric(df[loc], errors="coerce").to_numpy()
                 if loc else None)
    except Exception:
        return []
    # 断点位置（顶部刻度条）
    bps = []
    try:
        # RDP5 的产物名是 "<prefix>*BreakpointPositions*.csv"
        # （rdp5_engine.py 模块注释）。旧 glob 把断点分布文件名与
        # "*BreakpointPositions.csv" 直接粘在一起，永远匹配不到 → 刻度条不画。
        pp = _find(run_dir, "*BreakpointPositions*.csv")
        if pp and os.path.isfile(pp):
            with open(pp, encoding="utf-8-sig", errors="replace") as f:
                bps = [float(ln.split(",")[-1].strip())
                       for ln in f if ln.strip() and
                       ln.strip().lstrip(" +-.").replace(".", "", 1).isdigit()]
    except Exception:
        bps = []
    # 基因轨道（可选）
    genes = []
    try:
        gj = os.path.join(run_dir, "gene_map.json")
        if os.path.isfile(gj):
            with open(gj, encoding="utf-8") as f:
                genes = (json.load(f).get("genes") or [])
            genes = [g for g in genes
                     if isinstance(g.get("start"), (int, float))
                     and isinstance(g.get("end"), (int, float))
                     and g["end"] > g["start"]]
    except Exception:
        genes = []

    # ── 布局参数（比例对齐 MainFile.R）─────────────────────────
    vals = [rbn.max()]
    if upper is not None:
        vals.append(np.nanmax(upper))
    if lower is not None:
        vals.append(np.nanmax(lower))
    highest = float(max(vals))
    if not np.isfinite(highest) or highest <= 0:
        highest = 1.0
    gstep = highest * 0.10            # geneMapIndividualHeight
    gbase = highest * 1.155           # geneLabelBase = highest*1.1*1.05

    fig, ax = plt.subplots(figsize=(11.5, 4.6))

    # ── 分层 polygon（ggplot 层序：rbn → upper → lower → bottom 白挖空）──
    ax.fill_between(x, rbn, 0, color=_VERM, alpha=0.85, linewidth=0)
    if upper is not None:
        ax.fill_between(x, upper, 0, color=sp.OKABE["sky"], alpha=1.0,
                        linewidth=0)
        ax.fill_between(x, lower, 0, color=sp.OKABE["blue"], alpha=0.85,
                        linewidth=0)
        # CI 边界灰线
        for arr in (upper, lower):
            good = np.isfinite(arr)
            ax.plot(x[good], arr[good], color=sp.OKABE["grey"], lw=0.8)
        # bottom = min(全部列) → 白色挖空重叠区
        cols = [rbn]
        for arr in (upper, lower):
            cols.append(np.nan_to_num(arr, nan=0.0))
        bottom = np.vstack(cols).min(axis=0)
        ax.fill_between(x, bottom, 0, color="white", linewidth=0)
    ax.plot(x, rbn, color="black", lw=1.05)

    # ── 基因轨道：梯子状错落矩形（assign_gene_positions 复刻）──
    # 链方向（2026-11-06）：gene_map 的 genes 带 strand（'+'/'-'/'?'，来自
    # GenBank f.location.strand）。'-' 画左向箭头并把标签放左端，'+' 画
    # 右向箭头（原行为），'?' 只画矩形不画箭头。
    if genes:
        genes = sorted(genes, key=lambda g: g["start"])
        used = set()
        for i, g in enumerate(genes):
            y = gbase
            while y in used:
                y += gstep
            used.add(y)
            gx0, gx1 = float(g["start"]), float(g["end"])
            ax.add_patch(plt.Rectangle(
                (gx0, y), gx1 - gx0, gstep,
                facecolor=sp.OKABE["blue"], alpha=0.7,
                edgecolor="black", linewidth=0.8, zorder=5))
            strand = str(g.get("strand") or "+")
            yc = y + gstep / 2
            label = str(g.get("name", "")).upper()
            if strand == "-":
                if gx1 > gx0:
                    ax.annotate(
                        "", xy=(gx0, yc),
                        xytext=(gx0 + (gx1 - gx0) * 0.3, yc),
                        arrowprops=dict(arrowstyle="-|>", color="black",
                                        lw=0.8, shrinkA=0, shrinkB=0),
                        zorder=7)
                ax.text(gx0, yc, label, ha="left", va="center", fontsize=8,
                        fontweight="bold", color="black", zorder=6)
            else:
                if strand == "+" and gx1 > gx0:
                    ax.annotate(
                        "", xy=(gx1, yc),
                        xytext=(gx1 - (gx1 - gx0) * 0.3, yc),
                        arrowprops=dict(arrowstyle="-|>", color="black",
                                        lw=0.8, shrinkA=0, shrinkB=0),
                        zorder=7)
                ax.text(gx1, yc, label, ha="right", va="center", fontsize=8,
                        fontweight="bold", color="black", zorder=6)
        band_top = max(used) + gstep
    else:
        band_top = gbase

    # ── 断点位置刻度条（基因轨道上方）──
    if bps:
        bar_y0 = band_top + gstep * 0.5
        h = gstep
        for bp in bps:
            ax.add_patch(plt.Rectangle(
                (bp, bar_y0), max(1.0, x.max() * 0.0008), h,
                facecolor="black", alpha=0.7, edgecolor="none", zorder=5))
        ax.set_ylim(0, bar_y0 + h + highest * 0.18)
    else:
        ax.set_ylim(0, band_top + highest * 0.18)

    ax.set_xlim(x.min(), x.max())
    ax.margins(x=0, y=0)
    ax.set_xlabel(f"Nucleotide position in the genome alignment",
                  fontsize=9)
    ax.set_ylabel("Recombination breakpoint number\n(200nt window)",
                  fontsize=9)
    # 标题：数据名（原比对文件名 stem），贴近参考图病毒名标题
    title = "RDP5 recombination breakpoint distribution"
    try:
        sj = os.path.join(run_dir, "summary.json")
        if os.path.isfile(sj):
            with open(sj, encoding="utf-8") as f:
                src = (json.load(f).get("source_input") or "")
            stem = os.path.splitext(os.path.basename(src))[0]
            if stem:
                title = stem
    except Exception:
        pass
    ax.set_title(title, fontsize=11, fontweight="bold")
    sp.despine()
    fig.tight_layout()
    return _save(fig, run_dir, "rdp_breakpoint_distribution")


# ------------------------------------------------------------------
# 3. align_qc 重组预筛：QC 四联分布
# ------------------------------------------------------------------
def plot_align_qc(run_dir: str) -> list:
    p = _find(run_dir, "align_qc_report.tsv")
    if not p:
        return []
    df = _read_tab(p)
    if df.empty:
        return []
    cols = []
    for keys, title, xl in ((("length", "len"), "Sequence length", "bp"),
                            (("gap",), "Gap %", "%"),
                            (("degener", "ambig", "n_"), "Degenerate/ambiguous %", "%"),
                            (("ident", "pid"), "Pairwise identity", "%")):
        v = _num(df, *keys)
        if (v is not None and v.notna().sum() >= 3
                and v.nunique() >= 3):      # 唯一值 <3 直方图退化（2026-11-06）
            cols.append((v, title, xl))
    if not cols:
        return []
    removed = _text(df, "removed", "verdict", "pass", "status", "flag")
    fig, axes = plt.subplots(1, len(cols), figsize=(3.1 * len(cols), 3.4))
    if len(cols) == 1:
        axes = [axes]
    for ax, (v, title, xl) in zip(axes, cols):
        _multi_hist(ax, v, title, xl, _BLUE)
        if removed is not None:
            bad = v[removed.astype(str).str.strip().str.lower()
                    .isin(("true", "yes", "removed", "fail", "failed", "1"))]
            if len(bad):
                ax.hist(bad, bins=min(20, max(6, len(bad))), color=_VERM,
                        alpha=0.6, label=f"removed {len(bad)}")
                ax.legend(frameon=True, framealpha=0.85, edgecolor="none", fontsize=7)
    fig.suptitle("Alignment prescreen QC (align_qc)", fontsize=11)
    # w_pad 拉开面板间距：多面板直方图的相邻 x 刻度（如左图 "8" 与
    # 右图 "-1"）会贴到同一像素列（check_figures 实抓 80px² 重叠）
    fig.tight_layout(rect=(0, 0, 1, 0.94), w_pad=2.2)
    return _save(fig, run_dir, "align_qc_overview")


# ------------------------------------------------------------------
# 4. 引物设计：Tm / GC / 产物大小 + 每靶标对数
# ------------------------------------------------------------------
def plot_primers(run_dir: str) -> list:
    p = _find(run_dir, "primers*.tsv", "primer*.tsv")
    if not p:
        return []
    df = _read_tab(p)
    if df.empty:
        return []
    panels = []
    for keys, title, xl, color in ((("tm", "t_m"), "Melting temp Tm", "°C", _BLUE),
                                   (("gc",), "GC content", "%", _VERM),
                                   (("size", "length", "amplicon"), "Product/target length", "bp",
                                    sp.OKABE["green"]),
                                   (("penalty", "score", "specific"), "Penalty/score", "", _GREY)):
        v = _num(df, *keys)
        # 信息量门槛（2026-11-06）：除"非空值 ≥3"外还要求"唯一值 ≥3"——
        # dsRNA 的 T7 primers.tsv 只有 2 条引物时，Tm 直方图退化成 2 根柱
        # （59.80/59.90、轴宽 0.1°C）毫无信息量，不再出图
        if (v is not None and v.notna().sum() >= 3
                and v.nunique() >= 3):
            panels.append((v, title, xl, color))
    # 每组仅 1 个 target 时「每靶标引物对数」条形图也是单行，无意义。
    # 2026-11-06：去掉 'name' 候选——引物名（T7-F/T7-R…）不是靶标分组，
    # 只有 2 条引物时会把退化条形图误画出来。
    target = _text(df, "target", "contig", "region", "group",
                   min_uniq=2, max_uniq=60)
    if not panels and target is None:
        return []
    nrow = 2 if target is not None else 1
    ncol = max(1, len(panels))
    fig, axes = plt.subplots(nrow, ncol, figsize=(3.1 * ncol, 3.0 * nrow),
                             squeeze=False)
    for j, (v, title, xl, color) in enumerate(panels):
        _multi_hist(axes[0][j], v, title, xl, color)
    if target is not None:
        tc = target.value_counts().sort_values(ascending=True).tail(15)
        # 第二行整行合并给「每靶标引物对数」，避免右侧留大片空白
        # （注意：第二行原有轴要全部移除，否则与新轴同槽叠加）
        gs = axes[1][0].get_gridspec()
        for jj in range(ncol):
            axes[1][jj].remove()
        ax = fig.add_subplot(gs[1, :])
        ax.barh([_wrap(x, 22) for x in tc.index], tc.values, color=_BLUE,
                edgecolor="white", linewidth=0.4)
        for i, v in enumerate(tc.values):
            ax.text(v, i, f" {v}", va="center", fontsize=8)
        ax.set_title("Primer pairs per target", fontsize=9)
        ax.set_xlabel("Pairs")
        sp.despine()
    fig.suptitle("Primer design overview", fontsize=11)
    fig.tight_layout(rect=(0, 0, 1, 0.95), w_pad=2.2)
    return _save(fig, run_dir, "primer_design_overview")


# ------------------------------------------------------------------
# 5. CRISPR 设计：候选评分 + Top 组合
# ------------------------------------------------------------------
def plot_crispr(run_dir: str) -> list:
    p = _find(run_dir, "crrna_candidates.tsv")
    if not p:
        return []
    df = _read_tab(p)
    if df.empty:
        return []
    score = _num(df, "score", "efficiency", "rating", "rank_value")
    gc = _num(df, "gc")
    offt = _num(df, "offtarget", "off_target", "mismatch")
    panels = [(v, t, x, c) for v, t, x, c in (
        (score, "crRNA candidate score", "Score", _BLUE),
        (gc, "GC content", "%", _VERM),
        (offt, "Off-target/mismatch count", "count", _GREY))
        if v is not None and v.notna().sum() >= 3 and v.nunique() >= 3]
    if not panels:
        return []
    fig, axes = plt.subplots(1, len(panels), figsize=(3.3 * len(panels), 3.4))
    if len(panels) == 1:
        axes = [axes]
    for ax, (v, t, x, c) in zip(axes, panels):
        _multi_hist(ax, v, t, x, c)
    fig.suptitle("CRISPR assay design overview", fontsize=11)
    fig.tight_layout(rect=(0, 0, 1, 0.94), w_pad=2.2)
    paths = _save(fig, run_dir, "crispr_design_overview")
    # Top 组合条形图（ranked_sets.tsv）
    p2 = _find(run_dir, "ranked_sets.tsv")
    if p2:
        df2 = _read_tab(p2)
        s2 = _num(df2, "score", "rating") if not df2.empty else None
        id2 = _text(df2, "set", "id", "name", min_uniq=1, max_uniq=40) \
            if not df2.empty else None
        if s2 is not None and id2 is not None:
            d = pd.DataFrame({"id": id2, "s": s2}).dropna()
            d = d.reindex(d["s"].abs().sort_values(ascending=True).index).tail(15)
            fig2, ax = plt.subplots(figsize=(6.4, max(2.8, 0.28 * len(d) + 1.0)))
            ax.barh([_wrap(x, 26) for x in d["id"]], d["s"].abs(),
                    color=_BLUE, edgecolor="white", linewidth=0.4)
            for i, v in enumerate(d["s"].abs()):
                ax.text(v, i, f" {v:.3g}", va="center", fontsize=8)
            ax.set_xlabel("Set score (absolute)")
            ax.set_title("Top guide sets (ranked_sets)", fontsize=10)
            sp.despine()
            paths += _save(fig2, run_dir, "crispr_top_sets")
    return paths


# ------------------------------------------------------------------
# 6. miRNA 靶标：引擎共识 + Top 靶病毒
# ------------------------------------------------------------------
def plot_mirna(run_dir: str) -> list:
    p = _find(run_dir, "mirna_targets_merged.csv")
    if not p:
        return []
    df = _read_tab(p)
    if df.empty:
        return []
    nalg = _num(df, "num_algorithms")
    energy = _num(df, "best_energy")
    tv = _text(df, "target_virus_name", min_uniq=1, max_uniq=80)
    if tv is None:
        tv = _text(df, "target_virus_id", "target_virus", min_uniq=1, max_uniq=80)
    mi = _text(df, "mirna_id", min_uniq=1, max_uniq=80)
    # 面板数必须与实际要画的内容一致：energy 列存在但有效值 <3 时画不了
    # 直方图，旧实现把它计入 npanel → 第三面板留一块空坐标轴（2026-11-04）。
    draw_energy = (energy is not None and energy.notna().sum() >= 3
                   and energy.nunique() >= 3)    # 唯一值 <3 退化（2026-11-06）
    npanel = 1 + (1 if tv is not None else 0) + (1 if draw_energy else 0)
    fig, axes = plt.subplots(1, npanel, figsize=(3.5 * npanel, 3.6))
    if npanel == 1:
        axes = [axes]
    k = 0
    if nalg is not None and nalg.notna().any():
        vc = nalg.dropna().astype(int).value_counts().sort_index()
        axes[k].bar(vc.index.astype(str), vc.values, color=_BLUE,
                    edgecolor="white", linewidth=0.4)
        axes[k].set_xlabel("Supporting engines")
        axes[k].set_ylabel("Hits")
        axes[k].set_title("Consensus strength", fontsize=9)
        k += 1
    if tv is not None:
        tc = tv.value_counts().sort_values(ascending=True).tail(12)
        axes[k].barh([_wrap(x, 24) for x in tc.index], tc.values,
                     color=_VERM, edgecolor="white", linewidth=0.4)
        for i, v in enumerate(tc.values):
            axes[k].text(v, i, f" {v}", va="center", fontsize=8)
        axes[k].set_xlabel("Hits")
        axes[k].set_title("Top target viruses", fontsize=9)
        k += 1
    if draw_energy:
        _multi_hist(axes[k], energy, "Best MFE energy", "kcal/mol", _GREY)
    fig.suptitle("miRNA target prediction overview", fontsize=11)
    fig.tight_layout(rect=(0, 0, 1, 0.94), w_pad=2.2)
    return _save(fig, run_dir, "mirna_targets_overview")


# ------------------------------------------------------------------
# 6b. miRNA 关系图：Sankey + 二部网络（PAmiRDB 风格）
# ------------------------------------------------------------------
def _mirna_pairs(run_dir):
    """(miRNA, 靶) 计数对：优先 results.json 的 target_protein（PAmiRDB 口径），
    回退合并 CSV 的 target_virus_name。"""
    rj = _find(run_dir, "results.json", os.path.join("mirna_target", "results.json"))
    if rj:
        try:
            rows = json.load(open(rj, encoding="utf-8"))
            if (isinstance(rows, list) and rows and isinstance(rows[0], dict)
                    and rows[0].get("target_protein")):
                return (Counter((str(r.get("mirna_id")), str(r["target_protein"]))
                                for r in rows), True)
        except Exception:
            pass
    p = _find(run_dir, "mirna_targets_merged.csv")
    if not p:
        return None, False
    df = _read_tab(p)
    mi = _text(df, "mirna_id", min_uniq=1, max_uniq=60)
    tv = _text(df, "target_virus_name", min_uniq=1, max_uniq=60)
    if tv is None:
        tv = _text(df, "target_virus_id", "target_virus", min_uniq=1, max_uniq=60)
    if mi is None or tv is None:
        return None, False
    return Counter(zip(mi.astype(str), tv.astype(str))), False


def plot_mirna_relations(run_dir: str) -> list:
    pairs, protein_level = _mirna_pairs(run_dir)
    if not pairs:
        return []
    sm = Counter({m: sum(w for (a, _), w in pairs.items() if a == m)
                  for (m, _) in pairs})
    st = Counter({t: sum(w for (_, b), w in pairs.items() if b == t)
                  for (_, t) in pairs})
    top_m = [k for k, _ in sm.most_common(8)]
    top_t = [k for k, _ in st.most_common(6)]
    edges = [(m, t, w) for (m, t), w in pairs.items()
             if m in top_m and t in top_t]
    fig, (axs, axn) = plt.subplots(1, 2, figsize=(13.2, 4.6),
                                   gridspec_kw={"wspace": 0.18})
    GREEN, RED = "#2E7D5B", "#E15759"

    def layout(names):
        y, pos, pad = 0.0, {}, 0.4
        for n in sorted(names, key=lambda x: -sm.get(x, st.get(x, 0))):
            tot = sum(w for (a, b), w in pairs.items() if n in (a, b))
            pos[n] = (y, y + tot)
            y += tot + pad
        return pos, y

    lp, lh = layout(top_m)
    rp, rh = layout(top_t)
    H = max(lh, rh)
    for n, (y0, y1) in lp.items():
        axs.fill_betweenx([H - y1, H - y0], 0.06, 0.10, color=GREEN)
        axs.text(0.048, H - (y0 + y1) / 2, _wrap(n, 20), ha="right",
                 va="center", fontsize=8)
    for n, (y0, y1) in rp.items():
        axs.fill_betweenx([H - y1, H - y0], 0.90, 0.94, color=RED)
        axs.text(0.952, H - (y0 + y1) / 2, _wrap(n, 20), ha="left",
                 va="center", fontsize=8)
    from matplotlib.path import Path as _P
    from matplotlib.patches import PathPatch as _PP
    ycur = {n: lp[n][0] for n in top_m}
    rcur = {n: rp[n][0] for n in top_t}
    for m, t, w in sorted(edges, key=lambda e: -e[2]):
        y0 = ycur[m]
        y1 = rcur[t]
        ycur[m] += w
        rcur[t] += w
        xa, xb = 0.10, 0.90
        xm = (xa + xb) / 2
        yl0, yl1 = H - (y0 + w), H - y0
        yr0, yr1 = H - (y1 + w), H - y1
        poly = _P([(xa, yl0), (xm, yl0), (xm, yr0), (xb, yr0),
                   (xb, yr1), (xm, yr1), (xm, yl1), (xa, yl1), (xa, yl0)],
                  [_P.MOVETO, _P.CURVE4, _P.CURVE4, _P.CURVE4,
                   _P.LINETO, _P.CURVE4, _P.CURVE4, _P.CURVE4, _P.CLOSEPOLY])
        axs.add_patch(_PP(poly, facecolor=GREEN, alpha=0.28, edgecolor="none"))
    axs.set_xlim(-0.30, 1.32)
    axs.set_ylim(-0.4, H + 0.4)
    axs.axis("off")
    left_t = "Viral protein" if protein_level else "Target virus"
    axs.set_title(f"miRNA -> {left_t} Sankey", fontsize=10, loc="left")
    yl = np.linspace(0.85, 0.15, len(top_m))
    yr = np.linspace(0.85, 0.15, len(top_t))
    lpos = {n_: (0.22, y) for n_, y in zip(top_m, yl)}
    rpos = {n_: (0.78, y) for n_, y in zip(top_t, yr)}
    wmax = max(w for _, _, w in edges) or 1
    for m, t, w in edges:
        (x0, y0), (x1, y1) = lpos[m], rpos[t]
        axn.annotate("", xy=(x1, y1), xytext=(x0, y0),
                     arrowprops=dict(arrowstyle="-", color=GREEN, alpha=0.30,
                                     lw=0.8 + 2.6 * w / wmax,
                                     connectionstyle="arc3,rad=0.18"))
    for n_, (x, y) in lpos.items():
        axn.scatter([x], [y], s=240, color=GREEN, zorder=3)
        axn.text(x + 0.035, y, _wrap(n_, 18), fontsize=8, va="center")
    for n_, (x, y) in rpos.items():
        axn.scatter([x], [y], s=240, color=RED, zorder=3)
        axn.text(x - 0.035, y, _wrap(n_, 18), fontsize=8, va="center", ha="right")
    axn.set_xlim(0, 1)
    axn.set_ylim(0, 1)
    axn.axis("off")
    axn.set_title(f"miRNA <-> {left_t} network", fontsize=10, loc="left")
    axn.scatter([], [], s=60, color=GREEN, label="miRNA")
    axn.scatter([], [], s=60, color=RED, label=left_t)
    axn.legend(frameon=False, fontsize=8, loc="lower center",
               bbox_to_anchor=(0.5, -0.06), ncol=2)
    return _save(fig, run_dir, "mirna_relations")


# ------------------------------------------------------------------
# 6c. miRNA 双链配对结构图（PAmiRDB duplex：彩色碱基 + 配对连线 + seed 区）
# ------------------------------------------------------------------
_BASE_COLOR = {"A": "#D64545", "U": "#E69F00", "T": "#E69F00",
               "C": "#3B74C4", "G": "#3E9C5C"}


def _duplex_row(r: dict):
    """从 results.json 的一条记录解析双链配对数据 → (mseq, tseq, pb) 或 None。

    pb: {int 位置: 'wc'|'wobble'|'mismatch'}；mseq/tseq 统一成 miRNA 5'->3' 视角。
    两种数据形态：
    - position_binding 字典（直给）；
    - alignment 三行比对串（真实引擎形态）：
      miRNA  3' SEQ 5' / 配对行(|=WC, .=wobble, 空格=错配) / target 5' SEQ 3'
    """
    mseq = str(r.get("mirna_seq") or r.get("mirna_sequence") or "")
    tseq = str(r.get("target_site_seq") or r.get("site_seq")
               or r.get("target_seq") or "")
    pb = r.get("position_binding")
    if not (len(mseq) >= 12 and len(tseq) >= 12
            and isinstance(pb, dict) and pb):
        aln = r.get("alignment")
        if isinstance(aln, str) and "|" in aln:
            try:
                import re
                ls = aln.splitlines()
                mi = next(l for l in ls if "miRNA" in l)
                ta = next(l for l in ls if "target" in l)
                pl = next(l for l in ls
                          if l.strip() and set(l.strip()) <= set("|. "))

                def _s(line):
                    m_ = re.search(r"[ACGUTNacgutn\-]{10,}", line)
                    return (m_.group(0).upper().replace("T", "U")
                            if m_ else "")
                mrow, trow = _s(mi), _s(ta)          # 含 '-' 缺口
                if mrow and trow:
                    mrow, prow, trow = (mrow[::-1], pl.rstrip()[::-1],
                                        trow[::-1])  # 统一成 miRNA 5'->3'
                    pb, ms, ts = {}, [], []
                    i_ = j_ = 0
                    for mb, pc, tb in zip(mrow, prow, trow):
                        if mb == "-" or tb == "-":
                            i_ += mb != "-"
                            j_ += tb != "-"
                            continue
                        i_ += 1
                        j_ += 1
                        ms.append(mb)
                        ts.append(tb)
                        pb[i_] = ("wc" if pc == "|" else
                                  "wobble" if pc == "." else "mismatch")
                    mseq, tseq = "".join(ms), "".join(ts)
            except Exception:
                return None
    if len(mseq) >= 12 and len(tseq) >= 12 and isinstance(pb, dict) and pb:
        return mseq, tseq, pb
    return None


def plot_mirna_duplex(run_dir: str) -> list:
    rj = _find(run_dir, "results.json", os.path.join("mirna_target", "results.json"))
    if not rj:
        return []
    try:
        rows = json.load(open(rj, encoding="utf-8"))
    except Exception:
        return []
    hits = []
    for r in (rows if isinstance(rows, list) else []):
        if not isinstance(r, dict):
            continue
        d = _duplex_row(r)
        if d:
            hits.append((r,) + d)
    if not hits:
        return []
    try:
        hits.sort(key=lambda h: -abs(float(h[0].get("best_energy")
                                            or h[0].get("energy") or 0)))
    except Exception:
        pass
    hits = hits[:4]
    n = len(hits)
    W = max(7.0, 0.30 * max(len(h[2]) for h in hits) + 2.5)
    fig, axes = plt.subplots(n, 1, figsize=(W, 2.35 * n), squeeze=False)
    from matplotlib.patches import Rectangle
    for ax, (r, mseq, tseq, pb) in zip(axes[:, 0], hits):
        L = max(len(mseq), len(tseq))
        for x, b in enumerate(mseq, 1):
            ax.scatter([x], [1.15], s=230, color=_BASE_COLOR.get(b, _GREY), zorder=3)
            ax.text(x, 1.15, b, ha="center", va="center", fontsize=7,
                    color="white", zorder=4)
        for x, b in enumerate(tseq, 1):
            ax.scatter([x], [0.05], s=230, color=_BASE_COLOR.get(b, _GREY), zorder=3)
            ax.text(x, 0.05, b, ha="center", va="center", fontsize=7,
                    color="white", zorder=4)
        for k in range(1, L + 1):
            st = str(pb.get(k, pb.get(str(k), ""))).lower()
            if st in ("wc", "watson", "pair", "wc_pair"):
                ax.plot([k, k], [0.28, 0.92], color="#2E8B57", lw=1.8, zorder=2)
            elif st in ("wobble", "gu", "g_u"):
                ax.plot([k, k], [0.28, 0.92], color="#8C8C8C", lw=1.4,
                        ls=(0, (1, 1.6)), zorder=2)
            elif st in ("mismatch", "mm"):
                ax.text(k, 0.60, "X", ha="center", va="center", fontsize=9,
                        color="#D64545", fontweight="bold", zorder=4)
        ax.add_patch(Rectangle((1.5, -0.22), 7, 1.74, fill=True,
                               facecolor="#F5C542", alpha=0.16,
                               edgecolor="#B8860B", ls="--", lw=1.1))
        ax.text(5.0, 1.52, "seed (2-8)", ha="center", fontsize=7, color="#8A6D1A")
        ax.text(0.45, 1.15, "5' miRNA", ha="right", va="center", fontsize=8)
        ax.text(0.45, 0.05, "3' target", ha="right", va="center", fontsize=8)
        name = (f'{r.get("mirna_id", "?")} -> '
                f'{r.get("target_virus_name") or r.get("target_protein") or r.get("target_id", "?")}')
        ax.set_xlim(-0.2, L + 1.2)
        ax.set_ylim(-0.45, 1.72)
        ax.set_title(_wrap(name, 70), fontsize=9, loc="left")
        ax.axis("off")
    fig.suptitle("miRNA : target duplex structure (PAmiRDB style)", fontsize=11)
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    return _save(fig, run_dir, "mirna_duplex")

# ------------------------------------------------------------------
# 6d. miRNA 明细表（PAmiRDB 风格：可排序 HTML，徽章/seed n/7/靶基因/详情）
# ------------------------------------------------------------------
def plot_mirna_table(run_dir: str) -> list:
    rj = _find(run_dir, "results.json", os.path.join("mirna_target", "results.json"))
    rows = None
    if rj:
        try:
            rows = json.load(open(rj, encoding="utf-8"))
        except Exception:
            rows = None
    if not (isinstance(rows, list) and rows and isinstance(rows[0], dict)):
        p = _find(run_dir, "mirna_targets_merged.csv")
        if not p:
            return []
        df = _read_tab(p)
        rows = df.to_dict("records")
    rows = [r for r in rows if isinstance(r, dict) and r.get("mirna_id")]

    def fnum(r, *keys, default=None):
        for k in keys:
            try:
                v = r.get(k)
                if v not in (None, ""):
                    return float(v)
            except (TypeError, ValueError):
                pass
        return default

    def ftxt(r, *keys):
        for k in keys:
            v = str(r.get(k) or "").strip()
            if v:
                return v
        return ""

    recs = []
    for r in rows:
        lvl = ftxt(r, "consensus_level").upper() or (
            "HIGH" if fnum(r, "num_algorithms", default=0) >= 3 else "MEDIUM")
        recs.append({
            "miRNA": ftxt(r, "mirna_id"),
            "level": lvl,
            "energy": fnum(r, "best_energy", "energy", "miranda_energy"),
            "identity": fnum(r, "best_identity", "miranda_identity", "identity"),
            "mm": fnum(r, "num_mismatches", "mismatches"),
            "seed_wc": fnum(r, "seed_perfect_wc", "seed_wc"),
            "seed_wob": fnum(r, "seed_wobble_count", "seed_wob"),
            "algs": ftxt(r, "consensus_detail", "algorithm", "algorithms"),
            "gene": ftxt(r, "target_protein", "target_gene", "target_virus_name"),
            "mseq": ftxt(r, "mirna_seq", "mirna_sequence"),
            "tseq": ftxt(r, "target_site_seq", "site_seq", "target_seq"),
            "site": f'{ftxt(r, "target_virus_name")} @{int(fnum(r, "target_start", default=0))}'
                    f'-{int(fnum(r, "target_end", default=0))}',
        })
    recs.sort(key=lambda x: (x["energy"] if x["energy"] is not None else 0))

    def pairline(a, b):
        out = []
        for ca, cb in zip(a, b):
            comp = {"A": "U", "U": "A", "T": "A", "C": "G", "G": "C"}
            out.append("|" if comp.get(ca.upper()) == cb.upper() else
                       (":" if {ca.upper(), cb.upper()} in
                        ({"G", "U"}, {"G", "T"}) else " "))
        return "".join(out)

    trs, details = [], []
    for i, r in enumerate(recs):
        badge = {"HIGH": "#DFF0E3", "MEDIUM": "#DDF0F7", "LOW": "#F2F2F2"}.get(
            r["level"], "#F2F2F2")
        seed = (f'{int(r["seed_wc"])}/{int(r["seed_wc"] + (r["seed_wob"] or 0))}'
                if r["seed_wc"] is not None else "-")
        mseq, tseq = r["mseq"], r["tseq"]
        detail = ""
        if mseq and tseq:
            detail = (f"<pre>miRNA 5' {mseq} 3'\n"
                      f"       {pairline(mseq, tseq)}\n"
                      f"target 3' {tseq} 5'</pre>")
        ecell = f'{r["energy"]:.1f}' if r["energy"] is not None else "-"
        icell = f'{r["identity"]:.0f}%' if r["identity"] is not None else "-"
        mmcell = int(r["mm"]) if r["mm"] is not None else 0
        trs.append(
            f'<tr data-e="{r["energy"] or 0}">'
            f'<td>{r["miRNA"]}</td>'
            f'<td><span class="b" style="background:{badge}">{r["level"]}</span></td>'
            f'<td class="n">{ecell}</td>'
            f'<td class="n">{icell}</td>'
            f'<td class="n">{mmcell}</td>'
            f'<td class="n s">{seed}</td>'
            f'<td>{r["algs"]}</td>'
            f'<td class="g">{r["gene"]}</td>'
            f'<td><button class="v" data-i="{i}">View</button></td></tr>')
        details.append(f'<div class="d" id="d{i}" hidden><h4>{r["miRNA"]} &rarr; '
                       f'{r["site"]}</h4>{detail or "无序列数据"}</div>')
    html = """<!doctype html><meta charset="utf-8">
<title>miRNA targets (PAmiRDB style)</title>
<style>body{{font:14px/1.5 "Segoe UI",sans-serif;margin:24px;color:#222}}
table{{border-collapse:collapse;width:100%}}
th,td{{padding:9px 12px;border-bottom:1px solid #eee;text-align:left}}
th{{font-size:11px;letter-spacing:.08em;color:#666;cursor:pointer;user-select:none}}
th:hover{{color:#111}} td.n{{text-align:right;font-variant-numeric:tabular-nums}}
td.s{{color:#1c7c4c;font-weight:600}} td.g{{color:#c96a1b}}
.b{{padding:3px 10px;border-radius:10px;font-size:11px;font-weight:600}}
.v{{border:1px solid #1c7c4c;color:#1c7c4c;background:#fff;border-radius:6px;
padding:3px 12px;cursor:pointer}}
.d{{background:#181c24;color:#dfe6ee;border-radius:10px;padding:16px 20px;margin:8px 0}}
.d pre{{font-family:Consolas,monospace;font-size:13px;white-space:pre-wrap}}
h4{{margin:0 0 10px;color:#fff}}</style>
<h3>miRNA &harr; viral targets (__N__ hits) &mdash; click header to sort</h3>
<table><thead><tr>
<th data-k="miRNA">MIRNA</th><th data-k="level">CONSENSUS</th>
<th data-k="energy">ENERGY</th><th data-k="identity">IDENTITY</th>
<th data-k="mm">MM</th><th data-k="seed">SEED</th>
<th data-k="algs">ALGORITHM</th><th data-k="gene">TARGET GENE</th><th>DETAIL</th>
</tr></thead><tbody>__TRS__</tbody></table>
__DETAILS__
<script>
const tb=document.querySelector("tbody");let dir=1,col="";
document.querySelectorAll("th[data-k]").forEach(th=>th.onclick=()=>{
 const k=th.dataset.k;dir=(col===k)?-dir:1;col=k;
 const num=(k==="energy"||k==="identity"||k==="mm");
 [...tb.rows].sort((a,b)=>{const x=parseFloat(a.dataset.e),y=parseFloat(b.dataset.e);
  if(num)return (x-y)*dir;
  return a.cells[k==="miRNA"?0:1].textContent.localeCompare(
   b.cells[k==="miRNA"?0:1].textContent)*dir;})
 .forEach(r=>tb.appendChild(r));});
document.querySelectorAll(".v").forEach(btn=>btn.onclick=()=>{
 const d=document.getElementById("d"+btn.dataset.i);d.hidden=!d.hidden;});
</script>"""
    html = (html.replace("{{", "{").replace("}}", "}")
                .replace("__N__", str(len(recs)))
                .replace("__TRS__", "".join(trs))
                .replace("__DETAILS__", "".join(details)))
    out = os.path.join(run_dir, "mirna_targets_table.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    return [out]


# ------------------------------------------------------------------
# 6e. miRNA 交互图表页（自包含单文件 HTML：duplex / Sankey / 网络 / 概览，
#     hover 高亮 + 原生 tooltip；matplotlib 静态图保留供下载/报告，交互看这里）
# ------------------------------------------------------------------
def _fnum(v, default=None):
    try:
        f = float(v)
        return default if f != f else f        # NaN -> default
    except (TypeError, ValueError):
        return default


def _mirna_interactive_data(run_dir: str):
    """交互页数据包：hits（含解析后的 duplex 序列/配对）+ (miRNA,靶) 计数对。"""
    rj = _find(run_dir, "results.json", os.path.join("mirna_target", "results.json"))
    rows = None
    if rj:
        try:
            rows = json.load(open(rj, encoding="utf-8"))
        except Exception:
            rows = None
    if not (isinstance(rows, list) and rows and isinstance(rows[0], dict)):
        p = _find(run_dir, "mirna_targets_merged.csv")
        if not p:
            return None
        df = _read_tab(p)
        rows = df.to_dict("records")
    rows = [r for r in rows if isinstance(r, dict) and r.get("mirna_id")]
    if not rows:
        return None
    hits = []
    for r in rows:
        d = _duplex_row(r)
        mseq, tseq, pb = d if d else ("", "", {})
        lvl = (str(r.get("consensus_level") or "").upper()
               or ("HIGH" if (r.get("num_algorithms") or 0) >= 3 else "MEDIUM"))
        hits.append({
            "mi": str(r.get("mirna_id") or "?"),
            "gene": str(r.get("target_protein") or ""),
            "virus": str(r.get("target_virus_name")
                         or r.get("target_virus_id") or "?"),
            "energy": _fnum(r.get("best_energy") or r.get("energy")),
            "identity": _fnum(r.get("best_identity") or r.get("identity")),
            "mm": _fnum(r.get("num_mismatches") or r.get("mismatches")),
            "eng": _fnum(r.get("num_algorithms")),
            "seed_wc": _fnum(r.get("seed_perfect_wc")),
            "seed_wob": _fnum(r.get("seed_wobble_count")),
            "algs": str(r.get("consensus_detail") or r.get("algorithms") or ""),
            "level": lvl,
            "mseq": mseq, "tseq": tseq, "pb": pb,
            "site": (f'{r.get("target_virus_name") or r.get("target_virus_id") or "?"}'
                     f' @{int(_fnum(r.get("target_start"), 0.0))}'
                     f'-{int(_fnum(r.get("target_end"), 0.0))}'),
        })
    pairs = {}
    for h in hits:
        tgt = h["gene"] or h["virus"]
        pairs[(h["mi"], tgt)] = pairs.get((h["mi"], tgt), 0) + 1
    # summary.json 里带着统计（引擎/耗时/对数），带上让页头能显示上下文
    stats = {}
    sj = _find(run_dir, "summary.json", os.path.join("mirna_target", "summary.json"))
    if sj:
        try:
            sjd = json.load(open(sj, encoding="utf-8"))
            if isinstance(sjd, dict):
                stats = {k: sjd.get(k) for k in
                         ("n_mirna", "n_virus", "n_pairs", "n_hits",
                          "elapsed_sec", "engines_actual")}
        except Exception:
            stats = {}
    return {"hits": hits,
            "pairs": [[m, t, n] for (m, t), n in pairs.items()],
            "stats": stats}


def plot_mirna_interactive(run_dir: str) -> list:
    """mirna_interactive.html：单文件交互图页（无外部库，离线可用）。

    duplex（彩色碱基 + 配对线 + seed 区，悬停看位置）· Sankey / 二部网络
    （悬停高亮流带/边）· 概览条形图。数据源 results.json（回退 merged.csv）。
    """
    data = _mirna_interactive_data(run_dir)
    if not data:
        return []
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    html = """<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<title>miRNA interactive charts (PAmiRDB style)</title>
<style>
body{font:14px/1.5 "Segoe UI","Microsoft YaHei",sans-serif;margin:0;
  background:#f6f8fa;color:#22303f}
.wrap{max-width:1180px;margin:0 auto;padding:20px 24px 60px}
h1{font-size:19px;margin:4px 0 2px}
.sub{color:#68788c;font-size:12.5px;margin-bottom:12px}
.chip{display:inline-block;background:#eef2f6;border-radius:12px;padding:3px 11px;
  font-size:12px;color:#44586e;margin:0 6px 6px 0}
.tabs{display:flex;gap:6px;margin:14px 0 0;flex-wrap:wrap}
.tab{padding:7px 18px;border:1px solid #d6dee8;border-bottom:none;
  border-radius:8px 8px 0 0;background:#eef2f6;cursor:pointer;font-size:13px;
  user-select:none}
.tab.on{background:#fff;font-weight:700;color:#1c7c4c;border-color:#b9c6d4}
.pane{display:none;background:#fff;border:1px solid #d6dee8;
  border-radius:0 10px 10px 10px;padding:18px;box-shadow:0 1px 4px rgba(30,40,60,.06)}
.pane.on{display:block}
.dcard{border:1px solid #e3e8ef;border-radius:10px;padding:12px 14px;margin:14px 0;
  background:#fff}
.dhead{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:6px}
.dhead b{color:#2c6e49}
.bdg{font-size:11px;border-radius:10px;padding:2px 9px;font-weight:600}
.bdg.e{background:#e8f1ea;color:#1c7c4c}
.bdg.s{background:#fdf3d7;color:#8a6d1a}
.bdg.l{background:#eef2f6;color:#44586e}
.hint{color:#8892a6;font-size:12px}
.svgbox{overflow-x:auto}
.legend{font-size:12px;color:#44586e;margin:6px 0 0}
</style></head><body><div class="wrap">
<h1>miRNA &harr; target interactive charts</h1>
<div class="sub">PAmiRDB style &middot; hover 图元看数值 &middot; 单文件离线可用</div>
<div id="chips"></div>
<div class="tabs" id="tabs">
  <div class="tab on" data-t="duplex">双链结构 Duplex</div>
  <div class="tab" data-t="sankey">关系 Sankey</div>
  <div class="tab" data-t="net">网络 Network</div>
  <div class="tab" data-t="ov">概览 Overview</div>
</div>
<div class="pane on" id="p-duplex"></div>
<div class="pane" id="p-sankey"></div>
<div class="pane" id="p-net"></div>
<div class="pane" id="p-ov"></div>
</div>
<script>const DATA=__DATA__;</script>
<script>
"use strict";
const esc = s => String(s == null ? "" : s)
  .replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;")
  .replace(/"/g,"&quot;");
const BC = {A:"#D64545", U:"#E69F00", C:"#3B74C4", G:"#3E9C5C", N:"#9aa4b0"};
const LEVEL_COLOR = {NONE:"#9aa4b0", LOW:"#f0c36d", "LOW+":"#e8a94e",
  MEDIUM:"#6db3d8", "MEDIUM+":"#4a94c0", HIGH:"#5aab77", "HIGH+":"#3d8f61",
  "VERY HIGH":"#1a4a30"};
const LEVEL_DARK = ["VERY HIGH","HIGH+","HIGH","MEDIUM+","MEDIUM"];
function $(id){return document.getElementById(id);}

/* ---------- chips ---------- */
(function(){
  const st = DATA.stats || {};
  const chips = [];
  if(st.n_hits != null) chips.push("命中 " + st.n_hits + " hit(s)");
  if(st.n_pairs != null) chips.push(st.n_mirna + " miRNA × " + st.n_virus
    + " 病毒 = " + st.n_pairs + " 对");
  if(st.engines_actual) chips.push("引擎 " + st.engines_actual.join(", "));
  if(st.elapsed_sec != null) chips.push(st.elapsed_sec + "s");
  $("chips").innerHTML = chips.map(c=>'<span class="chip">'+esc(c)+'</span>').join("");
})();

/* ---------- duplex ---------- */
function duplexSVG(h){
  const L = Math.max(h.mseq.length, h.tseq.length);
  if(!L) return "";
  const CELL=24, Y_MI=46, Y_TG=104, x0=34, W=x0+L*CELL+20, H=150;
  const seedX1=x0+1.5*CELL, seedW=7*CELL;
  let s = '<svg xmlns="http://www.w3.org/2000/svg" width="'+W+'" height="'+H+'" '
    + 'viewBox="0 0 '+W+' '+H+'" style="min-width:'+W+'px">';
  s += '<rect x="'+seedX1+'" y="6" width="'+seedW+'" height="138" fill="#F5C542" '
    + 'fill-opacity="0.10" stroke="#B8860B" stroke-dasharray="4,3" rx="6"/>';
  s += '<text x="'+(seedX1+seedW/2)+'" y="18" text-anchor="middle" font-size="10" '
    + 'fill="#8A6D1A">seed (2-8)</text>';
  s += '<text x="4" y="'+(Y_MI+4)+'" font-size="11" fill="#44586e">5&#39; miRNA</text>';
  s += '<text x="4" y="'+(Y_TG+4)+'" font-size="11" fill="#44586e">3&#39; target</text>';
  for(let k=1;k<=L;k++){
    const mb=h.mseq[k-1], tb=h.tseq[k-1];
    const st=(h.pb[k]||"")+"";
    const x=x0+k*CELL;
    if(mb) s += '<circle cx="'+x+'" cy="'+Y_MI+'" r="10" fill="'+(BC[mb]||BC.N)+'">'
      + '<title>'+esc(h.mi)+' pos '+k+' = '+esc(mb)+'</title></circle>'
      + '<text x="'+x+'" y="'+(Y_MI+3.5)+'" text-anchor="middle" font-size="10" '
      + 'fill="#fff" pointer-events="none">'+esc(mb)+'</text>';
    if(tb) s += '<circle cx="'+x+'" cy="'+Y_TG+'" r="10" fill="'+(BC[tb]||BC.N)+'">'
      + '<title>'+esc(h.virus)+' site pos '+k+' = '+esc(tb)+'</title></circle>'
      + '<text x="'+x+'" y="'+(Y_TG+3.5)+'" text-anchor="middle" font-size="10" '
      + 'fill="#fff" pointer-events="none">'+esc(tb)+'</text>';
    if(st==="wc") s += '<line x1="'+x+'" y1="'+(Y_MI+12)+'" x2="'+x+'" y2="'+(Y_TG-12)+'" '
      + 'stroke="#2E8B57" stroke-width="2"><title>pos '+k+' WC pair</title></line>';
    else if(st==="wobble") s += '<line x1="'+x+'" y1="'+(Y_MI+12)+'" x2="'+x+'" y2="'+(Y_TG-12)+'" '
      + 'stroke="#8C8C8C" stroke-width="1.4" stroke-dasharray="2,2.5">'
      + '<title>pos '+k+' wobble (G·U)</title></line>';
    else if(st==="mismatch") s += '<text x="'+x+'" y="'+((Y_MI+Y_TG)/2+4)+'" '
      + 'text-anchor="middle" font-size="11" fill="#D64545" font-weight="bold">X'
      + '<title>pos '+k+' mismatch</title></text>';
  }
  return s + "</svg>";
}

function renderDuplex(){
  const box=$("p-duplex");
  const hits=(DATA.hits||[]).filter(h=>h.mseq&&h.tseq);
  if(!hits.length){
    box.innerHTML='<p class="hint">无比对/序列数据（结果行缺 mseq/tseq/pb 字段；'
      + '悬停图表不可用时看明细表）</p>';
    return;
  }
  const show=hits.slice(0,12);
  box.innerHTML = show.map(h=>{
    const e=h.energy!=null
      ? '<span class="bdg e">MFE '+h.energy.toFixed(1)+' kcal/mol</span>' : "";
    const sd=h.seed_wc!=null
      ? '<span class="bdg s">Seed '+Math.round(h.seed_wc)+'/7 WC</span>' : "";
    const dark=LEVEL_DARK.indexOf(h.level)>=0;
    const lv='<span class="bdg l" style="background:'+(LEVEL_COLOR[h.level]||"#eef2f6")
      +';color:'+(dark?"#fff":"#44586e")+'">'+esc(h.level)+'</span>';
    return '<div class="dcard"><div class="dhead"><b>'+esc(h.mi)+'</b>'
      + '<span style="color:#c96a1b;font-size:12.5px">&rarr; '+esc(h.gene||h.virus)+'</span>'
      + lv + e + sd + '</div>'
      + '<div class="svgbox">'+duplexSVG(h)+'</div>'
      + '<div class="legend">| WC &nbsp; : wobble (G·U) &nbsp; X 错配'
      + ' &nbsp;·&nbsp; 悬停碱基/配对线看位置</div>'
      + '<div class="hint">位点 ' + esc(h.site) + (h.algs?" · "+esc(h.algs):"") + '</div></div>';
  }).join("");
  if(hits.length>show.length)
    box.innerHTML += '<p class="hint">仅展示 MFE 最强的前 12 个；'
      + '完整清单看明细表（mirna_targets_table.html）或 CSV。</p>';
}

/* ---------- sankey / network 共用聚合 ---------- */
function agg(){
  const sm={},st={};
  (DATA.pairs||[]).forEach(function(p){
    sm[p[0]]=(sm[p[0]]||0)+p[2];
    st[p[1]]=(st[p[1]]||0)+p[2];
  });
  const lm=Object.keys(sm).sort(function(a,b){return sm[b]-sm[a];}).slice(0,8);
  const lt=Object.keys(st).sort(function(a,b){return st[b]-st[a];}).slice(0,6);
  const edges=(DATA.pairs||[]).filter(function(p){
    return lm.indexOf(p[0])>=0 && lt.indexOf(p[1])>=0;
  });
  return {sm:sm, st:st, lm:lm, lt:lt, edges:edges};
}

/* ---------- sankey ---------- */
function renderSankey(){
  const box=$("p-sankey");
  const a=agg(); if(!a.edges.length){box.innerHTML='<p class="hint">无关系数据</p>';return;}
  const pad=14, nodeH=function(w){return Math.max(16, w*18);};
  const H=Math.max(240, a.lm.reduce(function(s,n){return s+nodeH(a.sm[n])+pad;},0)+pad);
  const X0=175, X1=725, NBW=16;
  let y=pad; const lpos={};
  a.lm.forEach(function(n){lpos[n]=y; y+=nodeH(a.sm[n])+pad;});
  y=pad; const rpos={};
  a.lt.forEach(function(n){rpos[n]=y; y+=nodeH(a.st[n])+pad;});
  let s='<svg xmlns="http://www.w3.org/2000/svg" width="900" height="'+H+'" '
    + 'style="min-width:760px;width:100%">';
  const ycur={},rcur={};
  a.lm.forEach(function(n){ycur[n]=lpos[n];});
  a.lt.forEach(function(n){rcur[n]=rpos[n];});
  a.edges.slice().sort(function(x,y){return y[2]-x[2];}).forEach(function(p){
    const m=p[0], t=p[1], w=p[2];
    const y0=ycur[m], y1=rcur[t];
    ycur[m]+=nodeH(w); rcur[t]+=nodeH(w);
    const x0=X0+NBW, x1=X1, xm=(x0+x1)/2;
    s += '<path d="M'+x0+','+y0+' C'+xm+','+y0+' '+xm+','+y1+' '+x1+','+y1+'" '
      + 'stroke="#2E7D5B" stroke-opacity="0.25" fill="none" stroke-width="'
      + (nodeH(w)-2).toFixed(1)+'" class="fl" data-m="'+esc(m)+'" data-t="'+esc(t)+'">'
      + '<title>'+esc(m)+' &rarr; '+esc(t)+' : '+w+' hit(s)</title></path>';
  });
  a.lm.forEach(function(n){
    s += '<rect x="'+(X0-NBW)+'" y="'+lpos[n]+'" width="'+NBW+'" height="'
      + nodeH(a.sm[n])+'" fill="#2E7D5B" rx="2">'
      + '<title>'+esc(n)+' : '+a.sm[n]+' hit(s)</title></rect>'
      + '<text x="'+(X0-NBW-6)+'" y="'+(lpos[n]+nodeH(a.sm[n])/2+4)+'" '
      + 'text-anchor="end" font-size="11" fill="#44586e">'+esc(n)+'</text>';
  });
  a.lt.forEach(function(n){
    s += '<rect x="'+X1+'" y="'+rpos[n]+'" width="'+NBW+'" height="'
      + nodeH(a.st[n])+'" fill="#E15759" rx="2">'
      + '<title>'+esc(n)+' : '+a.st[n]+' hit(s)</title></rect>'
      + '<text x="'+(X1+NBW+6)+'" y="'+(rpos[n]+nodeH(a.st[n])/2+4)+'" '
      + 'font-size="11" fill="#44586e">'+esc(n)+'</text>';
  });
  box.innerHTML = s + "</svg>";
}

/* ---------- network ---------- */
function renderNet(){
  const box=$("p-net");
  const a=agg(); if(!a.edges.length){box.innerHTML='<p class="hint">无关系数据</p>';return;}
  const W=900, H=Math.max(300, Math.max(a.lm.length,a.lt.length)*52+60);
  const lpos={},rpos={};
  a.lm.forEach(function(n,i){lpos[n]=40+i*52;});
  a.lt.forEach(function(n,i){rpos[n]=40+i*52;});
  let s='<svg xmlns="http://www.w3.org/2000/svg" width="'+W+'" height="'+H+'" '
    + 'style="min-width:760px;width:100%">';
  const wmax=Math.max.apply(null, a.edges.map(function(e){return e[2];}))||1;
  a.edges.forEach(function(p){
    const y0=lpos[p[0]], y1=rpos[p[1]];
    s += '<path class="ne" data-m="'+esc(p[0])+'" data-t="'+esc(p[1])+'" '
      + 'd="M200,'+y0+' C330,'+y0+' 570,'+y1+' 700,'+y1+'" '
      + 'stroke="#2E7D5B" stroke-opacity="0.30" fill="none" stroke-width="'
      + (0.8+2.6*p[2]/wmax).toFixed(2)+'">'
      + '<title>'+esc(p[0])+' &harr; '+esc(p[1])+' : '+p[2]+' hit(s)</title></path>';
  });
  a.lm.forEach(function(n,i){
    s += '<g class="nd" data-n="'+esc(n)+'"><circle cx="200" cy="'+(40+i*52)
      + '" r="9" fill="#2E7D5B"><title>'+esc(n)+' : '+a.sm[n]+' hit(s)</title></circle>'
      + '<text x="188" y="'+(40+i*52+4)+'" text-anchor="end" font-size="11" '
      + 'fill="#44586e">'+esc(n)+'</text></g>';
  });
  a.lt.forEach(function(n,i){
    s += '<g class="nd" data-n="'+esc(n)+'"><circle cx="700" cy="'+(40+i*52)
      + '" r="9" fill="#E15759"><title>'+esc(n)+' : '+a.st[n]+' hit(s)</title></circle>'
      + '<text x="712" y="'+(40+i*52+4)+'" font-size="11" '
      + 'fill="#44586e">'+esc(n)+'</text></g>';
  });
  s += '</svg>';
  /* hover 高亮：悬停节点只亮相连边与对侧节点，悬停边加亮 */
  const svg=box; // 占位，真实逻辑在 innerHTML 之后绑定
  box.innerHTML=s;
  const paths=box.querySelectorAll(".ne");
  const nodes=box.querySelectorAll(".nd");
  nodes.forEach(function(g){
    const n=g.getAttribute("data-n");
    g.addEventListener("mouseenter",function(){
      paths.forEach(function(p){
        const hit=(p.getAttribute("data-m")===n||p.getAttribute("data-t")===n);
        p.style.opacity=hit?"1":"0.12";
        p.setAttribute("stroke-opacity", hit?"0.55":"0.08");
      });
      nodes.forEach(function(g2){
        const n2=g2.getAttribute("data-n");
        const linked=a.edges.some(function(p){
          return (p[0]===n&&p[1]===n2)||(p[0]===n2&&p[1]===n);
        });
        g2.style.opacity=(n2===n||linked)?"1":"0.25";
      });
    });
    g.addEventListener("mouseleave",function(){
      paths.forEach(function(p){
        p.style.opacity=""; p.setAttribute("stroke-opacity","0.30");
      });
      nodes.forEach(function(g2){g2.style.opacity="";});
    });
  });
}

/* ---------- overview ---------- */
function ovBars(items, color, title){
  if(!items.length) return "";
  const max=Math.max.apply(null, items.map(function(d){return d[1];}))||1;
  const rw=Math.max.apply(null, items.map(function(d){
    return String(d[0]).length*6.8+10;}),80);
  const BW=440, BH=Math.max(64, items.length*24+40);
  let s='<div style="flex:1;min-width:300px">'
    + '<div style="font-size:12.5px;font-weight:700;margin-bottom:6px">'+esc(title)+'</div>'
    + '<svg xmlns="http://www.w3.org/2000/svg" width="'+BW+'" height="'+BH+'" '
    + 'viewBox="0 0 '+BW+' '+BH+'" style="width:100%;height:auto">';
  items.forEach(function(d,i){
    const lab=d[0], v=d[1];
    const y=26+i*24;
    const bw=Math.max(2,(BW-rw-44)*v/max);
    s += '<text x="0" y="'+(y+10)+'" font-size="11" fill="#44586e">'+esc(lab)+'</text>'
      + '<rect x="'+rw+'" y="'+y+'" width="'+bw.toFixed(1)+'" height="14" fill="'
      + color+'" rx="3"><title>'+esc(lab)+' : '+v+'</title></rect>'
      + '<text x="'+(rw+bw+6)+'" y="'+(y+10)+'" font-size="10.5" '
      + 'fill="#68788c">'+v+'</text>';
  });
  return s+"</svg></div>";
}
function renderOv(){
  const box=$("p-ov");
  const cnt={};
  (DATA.hits||[]).forEach(function(h){cnt[h.level]=(cnt[h.level]||0)+1;});
  const lvOrder=["VERY HIGH","HIGH+","HIGH","MEDIUM+","MEDIUM","LOW+","LOW","NONE"];
  const lvData=lvOrder.filter(function(l){return cnt[l];})
    .map(function(l){return [l,cnt[l]];});
  const eng={};
  (DATA.hits||[]).forEach(function(h){
    const e=Math.round(h.eng||0); eng[e]=(eng[e]||0)+1;});
  const engData=Object.keys(eng).sort(function(a,b){return a-b;})
    .map(function(k){return [k+" engines",eng[k]];});
  const tg={};
  (DATA.hits||[]).forEach(function(h){
    const k=h.gene||h.virus; tg[k]=(tg[k]||0)+1;});
  const tgData=Object.keys(tg).sort(function(a,b){return tg[b]-tg[a];})
    .slice(0,8).map(function(k){return [k,tg[k]];});
  const parts=[ovBars(lvData,"#4a94c0","Consensus levels (hits)"),
    ovBars(engData,"#3E9C5C","Supporting engines (hits)"),
    ovBars(tgData,"#E15759","Top targets (hits)")].filter(Boolean);
  box.innerHTML = parts.length
    ? '<div style="display:flex;gap:26px;flex-wrap:wrap">'+parts.join("")+'</div>'
    : '<p class="hint">无统计数据</p>';
}

renderDuplex(); renderSankey(); renderNet(); renderOv();
/* ---------- tabs ---------- */
document.querySelectorAll(".tab").forEach(function(el){
  el.onclick=function(){
    document.querySelectorAll(".tab").forEach(function(x){x.classList.remove("on");});
    el.classList.add("on");
    document.querySelectorAll(".pane").forEach(function(x){x.classList.remove("on");});
    $("p-"+el.dataset.t).classList.add("on");
  };
});
</script></body></html>"""
    html = html.replace("__DATA__", payload)
    out = os.path.join(run_dir, "mirna_interactive.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    return [out]


# ------------------------------------------------------------------
# 7. MOT：route × year 迁移矩阵热图
# ------------------------------------------------------------------
def plot_mot(run_dir: str) -> list:
    p = _find(run_dir, "migration_over_time.csv", "*over_time.csv")
    if not p:
        return []
    df = _read_tab(p)
    if df.empty or df.shape[1] < 2:
        return []
    years = df.columns[1:]
    m = df.iloc[:, 1:].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
    if not np.isfinite(m).any() or m.shape[0] == 0:
        return []
    show = np.log1p(np.clip(m, 0, None))
    fig, ax = plt.subplots(figsize=(max(5.0, 0.55 * len(years) + 2.0),
                                    max(3.0, 0.38 * m.shape[0] + 1.4)))
    im = ax.imshow(show, cmap="viridis", aspect="auto")
    ax.set_xticks(range(len(years)))
    ax.set_xticklabels(years, rotation=45, ha="right", fontsize=8)
    ax.set_yticks(range(m.shape[0]))
    ax.set_yticklabels([_wrap(str(r), 26) for r in df.iloc[:, 0]], fontsize=7)
    if m.shape[0] <= 15 and len(years) <= 20:
        for i in range(m.shape[0]):
            for j in range(len(years)):
                if m[i, j] > 0:
                    rel = (show[i, j] - np.nanmin(show)) / max(
                        np.nanmax(show) - np.nanmin(show), 1e-9)
                    ax.text(j, i, f"{int(m[i, j])}", ha="center", va="center",
                            fontsize=6, color="black" if rel > 0.55 else "white")
                else:
                    # 零值也要标——不标会被当成缺失数据
                    ax.text(j, i, "0", ha="center", va="center", fontsize=6,
                            color="0.55", alpha=0.9)
    fig.colorbar(im, ax=ax, shrink=0.8, label="log1p(migration count)")
    ax.set_title("Migration intensity, route x year (MOT)", fontsize=11)
    return _save(fig, run_dir, "mot_route_year_heatmap")


# ------------------------------------------------------------------
# 9. 元数据治理 / 降采样：时间-地理覆盖
# ------------------------------------------------------------------
def plot_metadata_coverage(run_dir: str) -> list:
    p = _find(run_dir, "*metadata*.csv", "*subsampled*.csv", "*govern*.csv")
    if not p:
        return []
    try:
        df = _read_tab(p)
    except Exception:
        return []
    if df.empty:
        return []
    # 先按列名过滤，再尝试日期解析（避免对每个文本列空跑 to_datetime）
    datec = next((c for c in df.columns
                  if any(k in c.lower() for k in ("date", "time", "year",
                                                  "采集", "日期"))
                  and pd.to_datetime(df[c], errors="coerce",
                                     format="mixed").notna().mean() > 0.7), None)
    geoc = _text(df, "location", "country", "region", "province", "geo",
                 "地点", "国家", min_uniq=2, max_uniq=60)
    if datec is None and geoc is None:
        return []
    npanel = 1 + (1 if geoc is not None else 0)
    fig, axes = plt.subplots(1, npanel, figsize=(4.6 * npanel, 3.5))
    if npanel == 1:
        axes = [axes]
    k = 0
    if datec is not None:
        yr = pd.to_datetime(df[datec], errors="coerce").dt.year.dropna().astype(int)
        vc = yr.value_counts().sort_index()
        if len(vc) >= 1:
            axes[k].bar(vc.index.astype(str), vc.values, color=_BLUE,
                        edgecolor="white", linewidth=0.4)
            axes[k].set_xlabel("Year")
            axes[k].set_ylabel("Sequences")
            axes[k].set_title("Sampling time coverage", fontsize=10)
            axes[k].tick_params(axis="x", rotation=45, labelsize=7)
            k += 1
    if geoc is not None and k < npanel:
        gc = geoc.value_counts().sort_values(ascending=True).tail(12)
        axes[k].barh([_wrap(x, 24) for x in gc.index], gc.values,
                     color=_VERM, edgecolor="white", linewidth=0.4)
        for i, v in enumerate(gc.values):
            axes[k].text(v, i, f" {v}", va="center", fontsize=8)
        axes[k].set_xlabel("Sequences")
        axes[k].set_title("Geographic coverage (top N)", fontsize=10)
    fig.suptitle("Metadata coverage overview", fontsize=11)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    return _save(fig, run_dir, "metadata_coverage_overview")


# ------------------------------------------------------------------
# 10. 系统地理迁移概览（mugration：迁移矩阵热图 + 迁移路线计数）
# ------------------------------------------------------------------
def plot_phylogeo_migration(run_dir: str) -> list:
    mp = _find(run_dir, "migration_matrix.tsv")
    tp = _find(run_dir, "transitions.tsv")
    if not mp and not tp:
        return []
    fig, axes = plt.subplots(1, 2, figsize=(11.6, 4.4),
                             gridspec_kw={"wspace": 0.34})
    drew = False
    if mp:
        df = _read_tab(mp)
        if not df.empty and {"from", "to", "count"} <= {c.lower() for c in df.columns}:
            df.columns = [c.lower() for c in df.columns]
            df["count"] = pd.to_numeric(df["count"], errors="coerce")
            pv = df.pivot_table(index="from", columns="to",
                                values="count", aggfunc="sum", fill_value=0)
            show = pv.to_numpy(dtype=float)
            im = axes[0].imshow(show, cmap="viridis")
            axes[0].set_xticks(range(len(pv.columns)))
            axes[0].set_xticklabels(pv.columns, rotation=35, ha="right", fontsize=8)
            axes[0].set_yticks(range(len(pv.index)))
            axes[0].set_yticklabels(pv.index, fontsize=8)
            for i in range(show.shape[0]):
                for j in range(show.shape[1]):
                    if show[i, j] > 0:
                        rel = ((show[i, j] - show.min())
                               / max(show.max() - show.min(), 1e-9))
                        axes[0].text(j, i, f"{int(show[i, j])}", ha="center",
                                     va="center", fontsize=8,
                                     color="black" if rel > 0.55 else "white")
            fig.colorbar(im, ax=axes[0], shrink=0.8, label="Migration events")
            axes[0].set_title("Migration matrix (from x to)", fontsize=10)
            drew = True
    if tp:
        df = _read_tab(tp)
        if not df.empty and {"from", "to"} <= {c.lower() for c in df.columns}:
            df.columns = [c.lower() for c in df.columns]
            rc = (df["from"].astype(str) + " → " + df["to"].astype(str))
            vc = rc.value_counts().sort_values(ascending=True).tail(15)
            axes[1].barh([_wrap(x, 24) for x in vc.index], vc.values,
                         color=_VERM, edgecolor="white", linewidth=0.4)
            for i, v in enumerate(vc.values):
                axes[1].text(v, i, f" {v}", va="center", fontsize=8)
            axes[1].set_xlabel("Migration events")
            axes[1].set_title("Top migration routes (phylogeny)", fontsize=10)
            sp.despine()
            drew = True
    if not drew:
        plt.close(fig)
        return []
    fig.suptitle("Phylogeographic migration overview (mugration)", fontsize=11)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    return _save(fig, run_dir, "phylogeo_migration_overview")

# ------------------------------------------------------------------
# 入口：内容感知分发
# ------------------------------------------------------------------
_RULES = (
    ("identity_matrix.tsv", plot_identity_heatmap),
    ("events.tsv", plot_rdp_events),
    ("*breakpoint distribution.csv", plot_rdp_breakpoint_distribution),
    ("align_qc_report.tsv", plot_align_qc),
    ("primers*.tsv", plot_primers),
    ("crrna_candidates.tsv", plot_crispr),
    ("mirna_targets_merged.csv", (plot_mirna, plot_mirna_relations,
                             plot_mirna_duplex, plot_mirna_table,
                             plot_mirna_interactive)),
    ("migration_over_time.csv", plot_mot),
    ("migration_matrix.tsv", plot_phylogeo_migration),
    # （virus_classification.tsv 的 classification_overview 图已于 2026-11-03
    #   移除：单样本 1 条 bar 没有信息量，svg 预览卡还把下载区撑乱）
    ("*metadata*.csv", plot_metadata_coverage),
)


def add_plots(run_dir: str, logger=None) -> list:
    """扫 run 目录，给已知表格产物就地补配图；返回写出的图片路径。"""
    if not run_dir or not os.path.isdir(run_dir):
        return []
    paths: list = []
    for pat, fn in _RULES:
        base = run_dir
        # 真实工具产物常落在 run_dir/mirna_target/ 等子目录——规则未命中时下探一层
        if not _find(base, pat):
            for sub in sorted(os.listdir(base)):
                sd = os.path.join(base, sub)
                if os.path.isdir(sd) and _find(sd, pat):
                    base = sd
                    break
        hit = _find(base, pat)
        if not hit:
            continue
        try:
            out = []
            for f2 in (fn if isinstance(fn, tuple) else (fn,)):
                out += f2(base)
            for fp in out:
                if fp not in paths:
                    paths.append(fp)
        except Exception as e:                                  # noqa: BLE001
            if logger is not None:
                try:
                    logger.log(f"{fn.__name__} 补图失败（跳过）: {e}", "WARN")
                except Exception:
                    pass
    return paths


if __name__ == "__main__":
    d = sys.argv[1] if len(sys.argv) > 1 else "."
    for fp in add_plots(d):
        print(fp)
