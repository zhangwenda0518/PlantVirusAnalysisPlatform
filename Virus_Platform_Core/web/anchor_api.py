# -*- coding: utf-8 -*-
"""样品锚的只读 API（第一阶段：只展示，不做任何拦截）。

自 app.py 拆出（app.py 顶部注释禁止追加路由，新端点一律走蓝图）。锚本体在
Virus_Platform_Core/anchor.py，落盘在 run/anchors/<slug>/；这里只做读取，
不触发任何计算——除了 /verify，它按用户显式请求复算输入摘要。

三个端点：
    GET /api/anchor/active            正在分析 / 最近结束的样品（底部常驻条轮询）
    GET /api/anchor/<slug>            单样品锚快照 + 账本尾部
    GET /api/anchor/<slug>/verify     复算 sha256 与账本 hash 链，报一致/不一致
"""
from flask import Blueprint, jsonify, request

bp = Blueprint('anchor', __name__)


@bp.route('/api/anchor/active')
def api_anchor_active():
    """常驻条用：当前 running 的锚 + 最近若干条已结束的。

    参数 limit：最多返回多少个样品（默认 6，上限 40）。返回
    {anchor_root, active_n, samples:[summary...]}。
    """
    from Virus_Platform_Core import anchor
    try:
        n = int(request.args.get('limit') or 6)
    except (TypeError, ValueError):
        n = 6
    return jsonify(anchor.active(limit=n))


@bp.route('/api/anchor/<slug>')
def api_anchor_detail(slug):
    """单样品锚快照。参数 tail 控制账本返回最后多少行（默认 40）。"""
    from Virus_Platform_Core import anchor
    try:
        tail = int(request.args.get('tail') or 40)
    except (TypeError, ValueError):
        tail = 40
    d = anchor.snapshot(slug, ledger_tail=tail)
    if not d:
        return jsonify({'error': 'anchor not found', 'slug': slug}), 404
    return jsonify(d)


@bp.route('/api/anchor/<slug>/verify')
def api_anchor_verify(slug):
    """显式核对：复算输入文件 sha256 + 校验账本 hash 链。

    大文件会被 anchor.HASH_CAP_BYTES 上限跳过（rehash 字段给出 true/false），
    因此这里可能返回 status='skipped' 或 'mismatch'，均不代表运行失败。
    """
    from Virus_Platform_Core import anchor
    return jsonify(anchor.verify(slug))
