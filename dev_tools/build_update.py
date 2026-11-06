# -*- coding: utf-8 -*-
"""dev_tools/build_update.py — 打更新包 + 生成 updates-repo/manifest.json。

发版流程（与 package.py / finalize_release.py 衔接）：

    python dev_tools/package.py ...          # 打出程序包（build_info.json 带 version）
    python dev_tools/finalize_release.py     # 组装两档版本目录
    python dev_tools/build_update.py \
        --src "D:/PVAP/PlantVirusPlatform" \
        --src "D:/PVAP/PlantVirusPlatform_with_Plugin" \
        --out updates-dist                   # 打更新 zip + 合并生成 manifest

    # 上传：zip -> 自有服务器 updates/ 目录 + GitHub Releases；
    # manifest.json -> 仓库 updates-repo/manifest.json（客户端**默认**只从
    # GitHub raw 的 https 地址拉清单，2026-11-06 起不再内置明文 HTTP 源）；
    # 自有服务器 updates/manifest.json 仅供显式配置 update_manifest_url 的
    # 用户（内网/自建源）使用。

更新包内容 = 程序本体（VirusPlatform.exe + _internal/ + mcp_server.py +
build_info.json）——与 updater.validate_staged 的布局约定一致。zip 用固定
时间戳（1980-01-01）保证同内容同哈希（照 build_plugin_repo.py 同款）。
"""
import argparse
import hashlib
import json
import os
import sys
import time
import zipfile

# 固定时间戳：同内容同哈希
_FIXED_TS = (1980, 1, 1, 0, 0, 0)

CORE_ITEMS = ('VirusPlatform.exe', '_internal', 'mcp_server.py',
              'build_info.json')


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def build_update_zip(src_dir, out_dir):
    """从版本目录打更新 zip。返回 (zip路径, sha256, size, version, edition)。"""
    version = edition = ''
    try:
        with open(os.path.join(src_dir, 'VirusPlatform', 'build_info.json'),
                  encoding='utf-8') as f:
            bi = json.load(f)
        version = str(bi.get('version') or '')
        edition = str(bi.get('edition') or '') or ('full' if 'with_Plugin' in src_dir else 'minimal')
    except (OSError, ValueError):
        pass
    if not version:
        raise SystemExit(f'✘ {src_dir}: build_info.json 缺 version —— 请先用新 package.py 打包')

    os.makedirs(out_dir, exist_ok=True)
    zip_path = os.path.join(out_dir, f'vp-core-{version}-{edition}.zip')
    prog = 'VirusPlatform'   # 程序目录名
    prog_dir = os.path.join(src_dir, prog)
    n = 0
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as z:
        for item in CORE_ITEMS:
            p = os.path.join(prog_dir, item)
            if not os.path.exists(p):
                raise SystemExit(f'✘ {prog_dir} 缺 {item}（更新单元必须齐全）')
            if os.path.isfile(p):
                info = zipfile.ZipInfo.from_file(p, item)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.date_time = _FIXED_TS
                with open(p, 'rb') as f:
                    z.writestr(info, f.read())
                n += 1
            else:
                for root, _dirs, files in os.walk(p):
                    for fn in sorted(files):
                        fp = os.path.join(root, fn)
                        rel = os.path.relpath(fp, prog_dir).replace(os.sep, '/')
                        zi = zipfile.ZipInfo.from_file(fp, f'{item}/{rel}')
                        zi.compress_type = zipfile.ZIP_DEFLATED
                        zi.date_time = _FIXED_TS
                        with open(fp, 'rb') as f:
                            z.writestr(zi, f.read())
                        n += 1
    size = os.path.getsize(zip_path)
    sha = _sha256_file(zip_path)
    print(f'✔ {zip_path}  {size:,} B  sha256={sha[:16]}…  ({n} files, v{version}/{edition})')
    return {'zip': zip_path, 'sha256': sha, 'size': size,
            'version': version, 'edition': edition}


def merge_manifest(results, notes_zh='', notes_en='', min_version='',
                   out_path='', paused=None, rollout_percent=None):
    """合并各档结果生成 manifest.json。

    有既有 manifest 时保留其 notes/min_version 与发布控制字段
    （paused / rollout_percent）；显式传参则覆盖。
    """
    if not results:
        raise SystemExit('✘ 没有任何可打包的版本目录')
    version = results[0]['version']
    m = {
        'version': version,
        'released_at': time.strftime('%Y-%m-%d'),
        'notes_zh': notes_zh,
        'notes_en': notes_en,
        'min_version': min_version or version,
        # 发布控制（2026-11-05）：paused = 全局暂停自动更新；
        # rollout_percent = 灰度比例（0-100，缺省全量）。
        'paused': False,
        'rollout_percent': 100,
        'packages': {},
    }
    # 既有 manifest 的 notes/min_version 优先（--notes 留空则不覆盖）
    m_old = None
    if out_path and os.path.isfile(out_path):
        try:
            with open(out_path, encoding='utf-8') as f:
                m_old = json.load(f)
        except (OSError, ValueError):
            m_old = None
    if m_old and not notes_zh:
        m['notes_zh'] = str(m_old.get('notes_zh') or '')
        m['notes_en'] = str(m_old.get('notes_en') or '')
    if m_old and not min_version:
        m['min_version'] = str(m_old.get('min_version') or version)
    if m_old and m_old.get('released_at'):
        m['released_at'] = str(m_old.get('released_at')
                               or m['released_at'])
    if m_old and 'paused' in m_old:
        m['paused'] = bool(m_old.get('paused'))
    if m_old and m_old.get('rollout_percent') is not None:
        try:
            m['rollout_percent'] = max(0, min(100,
                                    int(m_old['rollout_percent'])))
        except (TypeError, ValueError):
            pass
    if paused is not None:
        m['paused'] = bool(paused)
    if rollout_percent is not None:
        m['rollout_percent'] = max(0, min(100, int(rollout_percent)))
    for r in results:
        m['packages'][r['edition']] = {
            'version': r['version'],
            'size': r['size'],
            'sha256': r['sha256'],
            'urls': [
                'http://39.106.101.94/updates/'
                + os.path.basename(r['zip']),
                'https://github.com/zhangwenda0518/PlantVirusPlatform/'
                'releases/download/v' + r['version'] + '/'
                + os.path.basename(r['zip']),
            ],
        }
    text = json.dumps(m, ensure_ascii=False, indent=2) + '\n'
    if out_path:
        os.makedirs(os.path.dirname(out_path) or '.', exist_ok=True)
        with open(out_path, 'w', encoding='utf-8', newline='\n') as f:
            f.write(text)
        print(f'✔ manifest -> {out_path}')
    return m, text


def main():
    ap = argparse.ArgumentParser(
        description='打更新包并生成 updates-repo/manifest.json')
    ap.add_argument('--src', action='append', required=True,
                    help='版本目录（含 VirusPlatform/ 程序目录），可多次')
    ap.add_argument('--out', default='updates-dist',
                    help='更新 zip 输出目录（默认 updates-dist/）')
    ap.add_argument('--repo', default='updates-repo',
                    help='manifest 落盘目录（默认 updates-repo/，随仓库提交）')
    ap.add_argument('--notes-zh', default='', help='更新说明（中文，留空沿用既有 manifest）')
    ap.add_argument('--notes-en', default='', help='更新说明（英文）')
    ap.add_argument('--min-version', default='',
                    help='低于此版本不建议直跳（缺省 = 当前版本）')
    ap.add_argument('--paused', action='store_true',
                    help='清单标记 paused=true：客户端停止自动下载/换装'
                         '（手动安装不受限）。不带该参数则沿用既有清单的值。')
    ap.add_argument('--rollout-percent', type=int, default=None,
                    help='灰度比例 0-100（缺省沿用既有清单，初始 100=全量）')
    args = ap.parse_args()

    results = []
    for s in args.src:
        results.append(build_update_zip(s, args.out))
    versions = {r['version'] for r in results}
    if len(versions) > 1:
        raise SystemExit(f'✘ 多个 --src 版本不一致: {sorted(versions)}')
    out_path = os.path.join(args.repo, 'manifest.json')
    m, _t = merge_manifest(results, notes_zh=args.notes_zh,
                           notes_en=args.notes_en,
                           min_version=args.min_version,
                           out_path=out_path,
                           paused=(True if args.paused else None),
                           rollout_percent=args.rollout_percent)
    print('发版步骤提醒：')
    print('  1. zip 上传自有服务器 updates/ 目录 + GitHub Releases')
    print('  2. updates-repo/manifest.json 随仓库提交（客户端默认清单源，'
          'GitHub raw https）')
    print('  3. 自有服务器 updates/manifest.json：仅显式配置 update_manifest_url '
          '的用户（内网/自建源）会读，可选')
    print(f'  4. 启用清单签名时：python dev_tools/sign_update.py sign '
          f'--manifest {out_path}')


if __name__ == '__main__':
    main()
