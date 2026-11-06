# -*- coding: utf-8 -*-
"""Virus_Platform_Core/web/update.py — 自动更新 HTTP 边界（blueprint）。

业务全部在 updater.py；本模块只做 HTTP 边界（包约定：web/ 只做 HTTP 边界
+ 任务工厂的胶水）。全自动静默更新由 app.py 起的 daemon 线程跑
updater.auto_update_loop；这里提供手动检查/状态轮询/手动安装/跳过版本。
"""
import threading

from flask import Blueprint, jsonify, request

from Virus_Platform_Core import updater

bp = Blueprint('update', __name__)


@bp.route('/api/update/check', methods=['GET'])
def api_update_check():
    """手动「检查更新」：立即拉清单比较（绕过缓存）。只读。"""
    try:
        return jsonify(updater.check_update(force=True))
    except Exception as e:          # 检查器自身出错也要给前端明确信号
        return jsonify({'ok': False, 'has_update': False,
                        'error': f'{type(e).__name__}: {e}'}), 500


@bp.route('/api/update/status', methods=['GET'])
def api_update_status():
    """自动更新后台状态（app.py daemon 线程的状态视图）+ 暂存区现状。

    phase: idle / checking / download(带 progress) / staged / awaiting_idle /
    applying / error。"""
    try:
        return jsonify(updater.get_status())
    except Exception as e:
        idle = {'phase': 'idle', 'detail': '', 'progress': None,
                'current': updater.current_version()}
        idle['error'] = f'{type(e).__name__}: {e}'
        return jsonify(idle), 500


@bp.route('/api/update/install', methods=['POST'])
def api_update_install():
    """手动「立即安装」：活动任务守卫 -> 生成换装脚本 -> 停机重启。

    写入类接口，须显式确认令牌 {"confirm": "install"}（平台惯例，
    同 archive/purge/reset）。"""
    body = request.get_json(force=True, silent=True) or {}
    if body.get('confirm') != 'install':
        return jsonify({'ok': False,
                        'error': '缺少确认令牌 {"confirm":"install"}'}), 400
    mode = str(body.get('mode') or updater._detect_mode())
    # request_install：暂存已就绪 -> 同步换装；没暂存 -> 后台下载后自动换装
    # （自动安装关闭时手动点安装也能用，2026-11-05）。
    r = updater.request_install(mode=mode)
    if not r.get('ok'):
        code = 409 if r.get('awaiting_idle') else 400
        return jsonify(r), code
    if r.get('downloading'):
        # 后台线程负责下载 -> 换装 -> 停机；前端轮询 /api/update/status 看进度。
        return jsonify(r)
    # 暂存就绪路径：让响应先送达浏览器再停机（Timer 延迟），随后硬退，
    # 由 update 脚本完成换装。手动安装不受暂停/灰度限制（显式点击即确认）。
    threading.Timer(0.8, updater.request_shutdown).start()
    return jsonify(r)


@bp.route('/api/update/skip', methods=['POST'])
def api_update_skip():
    """「跳过此版本」：state.json 记 skip_version，徽标/自动流程不再提示。"""
    body = request.get_json(force=True, silent=True) or {}
    ver = str(body.get('version') or '').strip()
    if not ver:
        return jsonify({'ok': False, 'error': '缺少 version'}), 400
    updater.set_skip_version(ver)
    return jsonify({'ok': True, 'skipped': ver})
