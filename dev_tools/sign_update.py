# -*- coding: utf-8 -*-
"""dev_tools/sign_update.py — 更新清单 Ed25519 签名 / 验签 / 密钥对生成。

背景（2026-11-06）：更新清单此前只有 sha256 + https，没有发布者身份校验。
本工具生成密钥对并对 updates-repo/manifest.json 签名；客户端 updater 在
配置了验签公钥（platform.json 的 update_sign_pubkey，或
updater.UPDATE_SIGN_PUBKEY）后强制验签：清单未签名 / 签名不符 / 公钥配置
但运行环境缺 cryptography 与 nacl 时一律拒绝（fail-closed）。

用法：
    python dev_tools/sign_update.py keygen
    python dev_tools/sign_update.py sign \
        --manifest updates-repo/manifest.json \
        --key dev_tools/update_signing_key.json
    python dev_tools/sign_update.py verify \
        --manifest updates-repo/manifest.json \
        --key dev_tools/update_signing_key.json
    # 也可只给公钥（hex 或 base64）：
    python dev_tools/sign_update.py verify --manifest ... --pubkey <hex|base64>

签名口径与 updater.canonical_manifest_bytes 完全一致：剔除 signature 字段
后按 key 排序、无空白的 UTF-8 JSON。清单写盘用临时文件 + os.replace 原子
替换（LF 行尾）。依赖 cryptography；缺失时提示 pip install cryptography。
"""
import argparse
import base64
import json
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from Virus_Platform_Core import updater  # noqa: E402

_DEFAULT_KEY = os.path.join(_ROOT, 'dev_tools', 'update_signing_key.json')


def _require_crypto():
    """导入 cryptography 的 Ed25519 组件；缺失则给出安装提示并退出。"""
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey)
    except Exception:                                   # noqa: BLE001
        sys.stderr.write('✘ 检测到缺失依赖 cryptography；'
                         'pip install cryptography 后可用\n')
        raise SystemExit(2)
    return serialization, Ed25519PrivateKey


def _atomic_write(path, text):
    """临时文件 + os.replace 原子写（同目录保证同卷）。"""
    path = os.path.abspath(path)
    d = os.path.dirname(path) or '.'
    os.makedirs(d, exist_ok=True)
    tmp = os.path.join(d, f'.{os.path.basename(path)}.{os.getpid()}.tmp')
    try:
        with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
            f.write(text)
        os.replace(tmp, path)
    finally:
        if os.path.isfile(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def _load_key_file(path):
    """读密钥文件（keygen 产出的 JSON），返回 dict。"""
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
    except OSError as e:
        raise SystemExit(f'✘ 读不到密钥文件 {path}: {e}')
    except ValueError as e:
        raise SystemExit(f'✘ 密钥文件不是合法 JSON {path}: {e}')
    if not isinstance(data, dict):
        raise SystemExit(f'✘ 密钥文件格式不对（应为 JSON 对象）: {path}')
    return data


def _pub_from_key(path):
    data = _load_key_file(path)
    pub = str(data.get('public_key_hex') or '').strip()
    if not pub:
        pub = str(data.get('public_key_base64') or '').strip()
    if not pub:
        raise SystemExit(f'✘ 密钥文件里没有公钥: {path}')
    return pub


# ------------------------------------------------------------------
# keygen / sign / verify
# ------------------------------------------------------------------
def cmd_keygen(args):
    serialization, Ed25519PrivateKey = _require_crypto()
    out = os.path.abspath(args.out)
    if os.path.exists(out) and not args.force:
        sys.stderr.write(f'✘ 私钥文件已存在: {out}\n'
                         '  确需覆盖请加 --force（覆盖后旧签名全部失效）\n')
        return 1
    key = Ed25519PrivateKey.generate()
    seed = key.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption())
    pub = key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw)
    payload = {
        'alg': 'ed25519',
        'created_at': time.strftime('%Y-%m-%d %H:%M:%S'),
        'private_key_hex': seed.hex(),
        'public_key_hex': pub.hex(),
        'public_key_base64': base64.b64encode(pub).decode('ascii'),
        'note': '私钥仅保存在本机，务必妥善保管；切勿提交仓库或随包分发。',
    }
    _atomic_write(out, json.dumps(payload, ensure_ascii=False, indent=1) + '\n')
    try:
        os.chmod(out, 0o600)
    except OSError:
        pass                 # Windows 上 chmod 语义有限，尽力而为
    print(f'✔ 已生成 Ed25519 密钥对: {out}')
    print('  私钥仅保存在本机，务必妥善保管，切勿提交仓库或随包分发。')
    print('  可嵌入的验签公钥（hex，填 updater.UPDATE_SIGN_PUBKEY 或 '
          'platform.json 的 update_sign_pubkey）：')
    print(f'    {pub.hex()}')
    print(f'  公钥（base64）: {base64.b64encode(pub).decode("ascii")}')
    return 0


def cmd_sign(args):
    serialization, Ed25519PrivateKey = _require_crypto()
    data = _load_key_file(args.key)
    try:
        seed = bytes.fromhex(str(data.get('private_key_hex') or '').strip())
    except ValueError:
        raise SystemExit(f'✘ 密钥文件 private_key_hex 不是合法 hex: {args.key}')
    if len(seed) != 32:
        raise SystemExit(f'✘ 私钥长度应为 32 字节（实际 {len(seed)}）: {args.key}')
    try:
        with open(args.manifest, encoding='utf-8') as f:
            mf = json.load(f)
    except OSError as e:
        raise SystemExit(f'✘ 读不到清单 {args.manifest}: {e}')
    except ValueError as e:
        raise SystemExit(f'✘ 清单不是合法 JSON {args.manifest}: {e}')
    if not isinstance(mf, dict):
        raise SystemExit(f'✘ 清单不是 JSON 对象: {args.manifest}')

    key = Ed25519PrivateKey.from_private_bytes(seed)
    sig = key.sign(updater.canonical_manifest_bytes(mf))
    mf['signature'] = {
        'alg': 'ed25519',
        'value': base64.b64encode(sig).decode('ascii'),
        'signed_at': time.strftime('%Y-%m-%d %H:%M:%S'),
    }
    _atomic_write(args.manifest,
                  json.dumps(mf, ensure_ascii=False, indent=2) + '\n')

    pub = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    ok, why = updater._ed25519_verify(
        pub, updater.canonical_manifest_bytes(mf), sig)
    print(f'✔ 已签名 {args.manifest}（ed25519）')
    print(f'  自校验: {"通过" if ok else "失败 → " + why}')
    print(f'  验签公钥（hex）: {pub.hex()}')
    return 0 if ok else 1


def cmd_verify(args):
    _require_crypto()          # 统一依赖口径：缺 cryptography 时给安装提示
    pub_raw = args.pubkey or _pub_from_key(args.key)
    try:
        with open(args.manifest, encoding='utf-8') as f:
            mf = json.load(f)
    except OSError as e:
        raise SystemExit(f'✘ 读不到清单 {args.manifest}: {e}')
    except ValueError as e:
        raise SystemExit(f'✘ 清单不是合法 JSON {args.manifest}: {e}')
    if not isinstance(mf, dict):
        raise SystemExit(f'✘ 清单不是 JSON 对象: {args.manifest}')
    sig_raw = mf.get('signature')
    if isinstance(sig_raw, dict):
        sig_raw = sig_raw.get('value') or sig_raw.get('sig') or ''
    try:
        pub = updater._decode_key_material(pub_raw, 32, '公钥')
        sig = updater._decode_key_material(sig_raw, 64, '清单签名')
    except ValueError as e:
        print(f'✘ 验签失败: {e}')
        return 1
    ok, why = updater._ed25519_verify(
        pub, updater.canonical_manifest_bytes(mf), sig)
    if ok:
        print(f'✔ 验签通过: {args.manifest}')
        return 0
    print(f'✘ 验签失败: {why}')
    return 1


def main():
    ap = argparse.ArgumentParser(
        description='更新清单 Ed25519 签名工具（配合 Virus_Platform_Core/'
                    'updater.py）')
    sub = ap.add_subparsers(dest='cmd', required=True)

    kp = sub.add_parser('keygen', help='生成 Ed25519 密钥对')
    kp.add_argument('--out', default=_DEFAULT_KEY,
                    help=f'私钥文件路径（默认 {_DEFAULT_KEY}）')
    kp.add_argument('--force', action='store_true',
                    help='覆盖已存在的私钥文件（危险）')
    kp.set_defaults(func=cmd_keygen)

    sp = sub.add_parser('sign', help='对 manifest.json 签名（就地写回）')
    sp.add_argument('--manifest', required=True, help='manifest.json 路径')
    sp.add_argument('--key', default=_DEFAULT_KEY, help='私钥文件路径')
    sp.set_defaults(func=cmd_sign)

    vp = sub.add_parser('verify', help='校验 manifest.json 签名')
    vp.add_argument('--manifest', required=True, help='manifest.json 路径')
    grp = vp.add_mutually_exclusive_group(required=True)
    grp.add_argument('--pubkey', help='公钥（hex 或 base64）')
    grp.add_argument('--key', help='密钥文件路径（读其中公钥）')
    vp.set_defaults(func=cmd_verify)

    args = ap.parse_args()
    return args.func(args)


if __name__ == '__main__':
    sys.exit(main())
