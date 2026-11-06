# -*- coding: utf-8 -*-
"""Virus_Platform_Core/updater.py — 平台程序本体自动更新。

面向**分发包用户**：启动后台静默检查 -> 发现新版本自动下载校验 -> 无活动任务
时自动换装重启（全自动，可配置关闭）。源码模式（git 仓库内运行）不支持自更新，
用 git pull。

设计要点（2026-11-05）：

- 更新单元 = VirusPlatform.exe + _internal/ + mcp_server.py + build_info.json
  （打包端 dev_tools/build_update.py 产出，按 minimal/full 两档各一包）。
  其余一切（platform.json / run/ / 3rd/ / databases/ / host-db/ / plugins/ /
  primer3_pkg/）一律不动——它们是用户数据与外部工具，不属于程序本体。
- 版本真源：安装根 build_info.json 的 version（打包时由
  dev_tools/package.py _build_info() 从 Virus_Platform_Core.__version__ 写入），
  缺失时回退 Virus_Platform_Core.__version__。
- 换装机制：Windows 运行中 exe/_internal 被锁定，无法原地覆盖。流程：
  下载校验解压到 staged.tmp（全通过后原子改名 staged）-> 生成
  apply_update.bat（等待当前进程退出 -> 旧 exe/_internal/mcp_server.py/
  build_info.json 改名 .old 备份 -> 换入新件 -> 重启 -> 任一步失败先删
  已换入的新件再换回 .old）-> 主进程退出后 bat 完成换装。.old 备份保留
  一代，下次启动成功后清理。
- 安全：更新通道独立域白名单（不碰 public_data.ALLOWED_HOSTS）+ 解析
  IP 非私网 + 重定向逐跳校验 + zip **强制** sha256（缺哈希即拒绝）+
  Ed25519 清单签名（配置公钥后 fail-closed）+ 防 zip-slip 解压 +
  安装前活动任务守卫（重启会把 running/queued 任务判 failed）。
"""
import ipaddress
import json
import logging
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import zipfile
import urllib.error
import urllib.request

from Virus_Platform_Core.config import PLATFORM_ROOT, RUN_ROOT, DIRS

# ------------------------------------------------------------------
# 更新源与白名单
# ------------------------------------------------------------------
# 默认清单地址：只保留 https（GitHub raw，随仓库提交
# updates-repo/manifest.json）。2026-11-06 起**不再内置明文 HTTP 清单源**：
# 自建/内网 http 源必须由用户在 platform.json 的 update_manifest_url 显式
# 配置（该域名自动进更新白名单，属用户显式选择），不做静默回退。
DEFAULT_MANIFEST_URLS = [
    'https://raw.githubusercontent.com/zhangwenda0518/'
    'PlantVirusAnalysisPlatform/main/updates-repo/manifest.json',
]
DEFAULT_MANIFEST_URL = DEFAULT_MANIFEST_URLS[0]

# 可嵌入的 Ed25519 公钥（hex 或 base64，32 字节；空 = 未启用签名校验）。
# 发布方可由 dev_tools/sign_update.py keygen 生成后填在这里；内网/自建源
# 也可在 platform.json 写 update_sign_pubkey 覆盖（无需改代码）。
# 一旦配置公钥：清单必须带合法 signature，否则拒绝（fail-closed）。
UPDATE_SIGN_PUBKEY = ''

# 更新通道独立白名单：host == 条目，或 host 以「.条目」结尾（子域）。
# IP 条目必须精确相等。
UPDATE_HOSTS = {
    '39.106.101.94',                   # 自有镜像（更新包下载；清单默认 https）
    'github.com',
    'githubusercontent.com',           # raw.githubusercontent.com
    'objects.githubusercontent.com',   # GitHub Releases 附件重定向
}

_UA = 'PVAP-Updater/1.0'
_MANIFEST_TTL = 600          # 清单进程内缓存 10 分钟（照插件索引同款）
_CHECK_INTERVAL = 6 * 3600   # 启动首查之后，长驻实例每 6 小时复查一次
_APPLY_WAIT_IDLE = 600       # 任务未空闲时重查间隔（秒）
_WAIT_PID_MAX = 180          # bat 等主进程退出的上限（秒）

_ACTIVE_STATUSES = ('running', 'queued', 'paused')
_TASK_ID_RE = re.compile(r'^[0-9a-f]{12}$')   # run/tasks/ 下合法任务 id
_UPDATES_DIR = os.path.join(RUN_ROOT, 'updates')


# ------------------------------------------------------------------
# 清单签名（Ed25519，2026-11-06）
# ------------------------------------------------------------------
def canonical_manifest_bytes(mf):
    """清单签名口径：剔除 signature 字段后按 key 排序、无空白的 UTF-8 JSON。

    签名端（dev_tools/sign_update.py）与校验端共用本函数——两端重算的字节
    必须逐字节一致，因此不允许缩进/空格/键序差异。"""
    body = {k: v for k, v in (mf or {}).items() if k != 'signature'}
    return json.dumps(body, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':')).encode('utf-8')


def _sign_pubkey():
    """验签公钥：config 属性 -> platform.json -> 模块常量，空 = 未启用。

    platform.json 的 update_sign_pubkey 是本版本新增键；config.save() 会
    「保留不认识的键」（见 config.py save 的 setdefault 段），所以直接读文件
    即可长期生效，无需改 config 模块。"""
    try:
        from Virus_Platform_Core.config import get_config
        v = str(getattr(get_config(), 'update_sign_pubkey', '') or '').strip()
        if v:
            return v
    except Exception as e:                              # noqa: BLE001
        logging.getLogger(__name__).warning(
            '配置层读取失败（回退直接读 platform.json）: %s: %s',
            type(e).__name__, e)
    try:
        with open(os.path.join(PLATFORM_ROOT, 'platform.json'),
                  encoding='utf-8') as f:
            v = str((json.load(f) or {}).get('update_sign_pubkey')
                    or '').strip()
        if v:
            return v
    except (OSError, ValueError):
        pass
    return str(UPDATE_SIGN_PUBKEY or '').strip()


def _decode_key_material(raw, nbytes, what):
    """把 hex / base64（可带 ed25519: 前缀）解码成定长字节；非法抛 ValueError。"""
    s = str(raw or '').strip()
    if s.lower().startswith('ed25519:'):
        s = s.split(':', 1)[1].strip()
    s = re.sub(r'\s+', '', s)
    if not s:
        raise ValueError(f'{what}为空')
    if len(s) == nbytes * 2 and re.fullmatch(r'[0-9a-fA-F]+', s):
        return bytes.fromhex(s)
    import base64
    try:
        b = base64.b64decode(s + '=' * (-len(s) % 4), validate=True)
    except Exception:                                   # noqa: BLE001
        try:
            b = base64.urlsafe_b64decode(s + '=' * (-len(s) % 4))
        except Exception:                               # noqa: BLE001
            raise ValueError(f'{what}不是合法的 hex/base64')
    if len(b) != nbytes:
        raise ValueError(f'{what}长度应为 {nbytes} 字节（实际 {len(b)} 字节）')
    return b


def _ed25519_verify(pub_bytes, msg, sig_bytes):
    """Ed25519 验签。返回 (True, '') 或 (False, 原因)。

    优先 cryptography（分发包 3rd/python 已带）；缺失时尝试 PyNaCl；
    两者都没有则返回不可用原因，由调用方按 fail-closed 处理。"""
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PublicKey)
    except Exception as e:                              # noqa: BLE001
        logging.getLogger(__name__).warning(
            'cryptography 不可用，Ed25519 验签改走 PyNaCl 兜底: %s: %s',
            type(e).__name__, e)
    else:
        try:
            Ed25519PublicKey.from_public_bytes(pub_bytes).verify(
                sig_bytes, msg)
            return True, ''
        except InvalidSignature:
            return False, '清单签名不匹配'
        except Exception as e:                          # noqa: BLE001
            return False, f'清单验签异常: {type(e).__name__}: {e}'
    try:
        from nacl.exceptions import BadSignatureError
        from nacl.signing import VerifyKey
    except Exception:                                   # noqa: BLE001
        return False, ('缺少 cryptography/nacl 依赖，无法验签'
                       '（pip install cryptography 后重试）')
    try:
        VerifyKey(pub_bytes).verify(msg, sig_bytes)
        return True, ''
    except BadSignatureError:
        return False, '清单签名不匹配'
    except Exception as e:                              # noqa: BLE001
        return False, f'清单验签异常: {type(e).__name__}: {e}'


def _verify_manifest_signature(mf):
    """校验更新清单的 Ed25519 签名。返回 (ok, 消息, 状态)。

    状态：
      'verified'    公钥已配置且签名合法；
      'unsigned'    未配置公钥（维持 sha256+https 门槛，消息作为 warning）；
      'invalid'     公钥已配置但清单未签名/签名不符/格式非法（拒绝）；
      'unavailable' 公钥已配置但运行环境缺 cryptography/nacl（拒绝）。
    """
    pub_raw = _sign_pubkey()
    if not pub_raw:
        return True, '未启用签名校验（未配置 update_sign_pubkey）', 'unsigned'
    sig_raw = (mf or {}).get('signature')
    if isinstance(sig_raw, dict):
        alg = str(sig_raw.get('alg') or 'ed25519').strip().lower()
        if alg != 'ed25519':
            return False, f'清单签名算法不受支持: {alg}', 'invalid'
        sig_raw = sig_raw.get('value') or sig_raw.get('sig') or ''
    if not str(sig_raw or '').strip():
        return (False, '清单缺少 signature 字段，拒绝使用（已配置验签公钥）',
                'invalid')
    try:
        pub = _decode_key_material(pub_raw, 32, '更新公钥')
        sig = _decode_key_material(sig_raw, 64, '清单签名')
    except ValueError as e:
        return False, str(e), 'invalid'
    ok, why = _ed25519_verify(pub, canonical_manifest_bytes(mf), sig)
    if ok:
        return True, '', 'verified'
    return False, why, ('unavailable' if '缺少 cryptography/nacl' in why
                        else 'invalid')


# ------------------------------------------------------------------
# 版本读取与比较
# ------------------------------------------------------------------
def _is_frozen():
    return bool(getattr(sys, 'frozen', False))


def current_version():
    """当前版本：优先安装根 build_info.json 的 version，缺失回退源码常量。"""
    try:
        with open(os.path.join(PLATFORM_ROOT, 'build_info.json'),
                  encoding='utf-8') as f:
            v = str((json.load(f) or {}).get('version') or '').strip()
        if v:
            return v
    except (OSError, ValueError):
        pass
    try:
        from Virus_Platform_Core import __version__
        return str(__version__)
    except Exception:                                   # noqa: BLE001
        return '0.0.0'


def current_edition():
    """当前版本档（minimal / full）：build_info.json 的 edition，缺失回退 minimal。"""
    try:
        with open(os.path.join(PLATFORM_ROOT, 'build_info.json'),
                  encoding='utf-8') as f:
            ed = str((json.load(f) or {}).get('edition') or '').strip()
        return ed or 'minimal'
    except (OSError, ValueError):
        return 'minimal'


def _staged_edition(stage):
    """暂存包 build_info.json 的 edition；缺失按 minimal（与 current_edition
    的回退口径一致），文件损坏也按 minimal 兜底——真正的缺失由 validate_staged
    报错，这里只服务于换装前的跨档一致性检查。"""
    try:
        with open(os.path.join(stage, 'build_info.json'),
                  encoding='utf-8') as f:
            ed = str((json.load(f) or {}).get('edition') or '').strip()
        return ed or 'minimal'
    except (OSError, ValueError):
        return 'minimal'


def _semver(v):
    """'1.2.3' -> (1,2,3)；非数字段忽略，短侧补 0。"""
    nums = tuple(int(x) for x in re.findall(r'\d+', str(v or ''))[:3])
    return nums + (0,) * (3 - len(nums))


def _version_newer(a, b):
    """a > b（语义比较：1.10 > 1.9）。"""
    return _semver(a) > _semver(b)


# ------------------------------------------------------------------
# URL 校验（更新通道独立白名单，照 public_data.check_url_host 三层校验）
# ------------------------------------------------------------------
def _extra_hosts():
    """platform.json update_manifest_url 的域名也进白名单（用户自建源）。"""
    try:
        from Virus_Platform_Core.config import get_config
        u = str(getattr(get_config(), 'update_manifest_url', '') or '').strip()
    except Exception:                                   # noqa: BLE001
        return set()
    try:
        from urllib.parse import urlsplit
        h = urlsplit(u).hostname or ''
        return {h.lower()} if h else set()
    except Exception:                                   # noqa: BLE001
        return set()


def _allowed_hosts():
    return UPDATE_HOSTS | _extra_hosts()


def _ip_public(host):
    """域名解析结果必须全部为公网地址（防 SSRF / DNS rebinding）。"""
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if (ip.is_private or ip.is_loopback or ip.is_reserved
                or ip.is_link_local or ip.is_multicast):
            return False
    return True


def check_update_url(url):
    """更新通道 URL 校验。返回 (True, '') 或 (False, 原因)。

    三层：http/https 协议 -> 域名在更新白名单（精确或子域，IP 精确）->
    解析 IP 全公网。更新源信任域不扩散进 public_data.ALLOWED_HOSTS。
    """
    u = str(url or '').strip()
    if not u.lower().startswith(('http://', 'https://')):
        return False, f'URL 非法（仅支持 http/https）: {u!r}'
    try:
        from urllib.parse import urlsplit
        host = (urlsplit(u).hostname or '').lower()
    except ValueError:
        return False, f'URL 无法解析: {u!r}'
    if not host:
        return False, f'URL 缺少主机名: {u!r}'
    ok = False
    for entry in _allowed_hosts():
        # IP 条目精确相等；域名条目允许子域
        if host == entry or (not entry.replace('.', '').isdigit()
                             and host.endswith('.' + entry)):
            ok = True
            break
    if not ok:
        return False, (f'主机不在更新源官方白名单内: {host}'
                       f'（允许: {", ".join(sorted(_allowed_hosts()))}）')
    if not _ip_public(host) and host not in _extra_hosts():
        # 私网/环回默认拒绝（防 SSRF）；唯一豁免 = 用户在 platform.json
        # 显式配置的 update_manifest_url 域名——内网自建更新源是合法
        # 部署形态（内网客户），显式配置即信任声明。内置官方源仍强制公网。
        return False, f'主机解析到私网/保留地址，拒绝: {host}'
    return True, ''


# ------------------------------------------------------------------
# 清单拉取与检查
# ------------------------------------------------------------------
_manifest_cache = {'ts': 0.0, 'data': None}
_cache_lock = threading.Lock()


def _manifest_urls():
    try:
        from Virus_Platform_Core.config import get_config
        u = str(getattr(get_config(), 'update_manifest_url', '') or '').strip()
        if u:
            return [u]
    except Exception as e:                              # noqa: BLE001
        logging.getLogger(__name__).warning(
            '配置层读取失败（回退内置清单源）: %s: %s', type(e).__name__, e)
    return list(DEFAULT_MANIFEST_URLS)


def fetch_manifest(force=False):
    """拉更新清单（按 _manifest_urls 顺序回退 + 10 分钟缓存）。

    默认只有一个 https 源；配置了 update_manifest_url 时只试该地址。
    返回 (dict, None) 或 (None, 错误)。"""
    with _cache_lock:
        if (not force and _manifest_cache['data'] is not None
                and time.time() - _manifest_cache['ts'] < _MANIFEST_TTL):
            return _manifest_cache['data'], None
    errors = []
    for u in _manifest_urls():
        for attempt in (1, 2):                          # 每地址重试一次
            try:
                req = urllib.request.Request(
                    u, headers={'User-Agent': _UA})
                with _opener().open(req, timeout=15) as resp:
                    raw = resp.read().decode('utf-8', 'replace')
                data = json.loads(raw)
                if not isinstance(data, dict):
                    raise ValueError('清单不是 JSON 对象')
                with _cache_lock:
                    _manifest_cache['ts'] = time.time()
                    _manifest_cache['data'] = data
                return data, None
            except Exception as e:                      # noqa: BLE001
                if attempt == 2:
                    errors.append(f'{u} → {type(e).__name__}: {e}')
                else:
                    time.sleep(1.5)
    return None, '所有更新源均不可达：\n' + '\n'.join(errors)


def _machine_bucket():
    """本机灰度桶号（0-99，稳定）：MAC(node) 的 sha256 前 8 位取模。

    用于 manifest 的 rollout_percent 灰度：桶号 < 比例才参与本轮自动更新。
    用 uuid.getnode()（MAC）而非主机名/路径——同一台机器搬目录、换用户
    不变；克隆 VM 可能同 MAC，对灰度场景可接受。"""
    import hashlib
    import uuid
    try:
        node = str(uuid.getnode())
    except Exception:                                   # noqa: BLE001
        node = ''
    return int(hashlib.sha256(node.encode('utf-8')).hexdigest()[:8], 16) % 100


def check_update(force=False):
    """检查更新。返回 dict：

    {ok, has_update, current, latest, edition, package{size,sha256,urls},
     notes_zh, notes_en, released_at, skipped, paused, rollout_percent,
     in_rollout, signature, warning, error, checked_at}。
    网络失败时 ok=False 且 error 有原因；signature 为 verified/unsigned/
    invalid/unavailable（见 _verify_manifest_signature）。

    发布控制（2026-11-05，manifest 级）：
    - paused=true：全局暂停自动更新（自动循环不下载不换装；手动安装不受限）；
    - rollout_percent=N：灰度比例，本机桶号 < N 才参与自动更新；
      缺省 100 = 全量。手动安装同样不受灰度限制（显式点击即视为确认）。

    安全门槛（2026-11-06）：
    - 配置了 update_sign_pubkey 就必须有合法 Ed25519 签名，否则直接拒绝；
    - 清单缺该版本档的包或 sha256 时 ok=False（sha256 不再可跳过）；
    - full 档没有 full 包时拒绝，绝不回退成 minimal（防静默降级）。
    """
    cur = current_version()
    ed = current_edition()
    mf, err = fetch_manifest(force=force)
    if mf is None:
        return {'ok': False, 'has_update': False, 'current': cur,
                'edition': ed, 'error': err}
    latest = str(mf.get('version') or '').strip()
    if not latest:
        return {'ok': False, 'has_update': False, 'current': cur,
                'edition': ed, 'error': '清单缺少 version 字段'}
    # 签名先于一切字段判断：清单不可信时连"有没有新版"都不下结论。
    sig_ok, sig_msg, sig_state = _verify_manifest_signature(mf)
    if not sig_ok:
        return {'ok': False, 'has_update': False, 'current': cur,
                'edition': ed, 'latest': latest,
                'signature': sig_state, 'error': sig_msg,
                'checked_at': time.strftime('%Y-%m-%d %H:%M:%S')}
    warning = sig_msg if sig_state == 'unsigned' else ''
    pkgs = mf.get('packages') or {}
    if not isinstance(pkgs, dict):
        pkgs = {}
    # 只认当前版本档的包：不再 `or pkgs.get('minimal')` 回退——full 用户
    # 被静默降级成 minimal 的根因（2026-11-06）。
    pkg = pkgs.get(ed)
    if not isinstance(pkg, dict):
        pkg = {}
    pkg_sha = str(pkg.get('sha256') or '').strip()
    has = _version_newer(latest, cur)
    st = _read_state_file()
    skipped = str(st.get('skip_version') or '') == latest
    paused = bool(mf.get('paused'))
    try:
        rollout = int(mf.get('rollout_percent')
                      if mf.get('rollout_percent') is not None else 100)
    except (TypeError, ValueError):
        rollout = 100
    rollout = max(0, min(100, rollout))
    in_rollout = _machine_bucket() < rollout
    pkg_err = ''
    if has:
        if ed == 'full' and not isinstance(pkgs.get('full'), dict):
            pkg_err = ('清单缺少 full 版本档的更新包，拒绝把 full 版'
                       '静默降级为 minimal')
        elif not pkg:
            pkg_err = f'清单缺少 {ed} 版本档的更新包'
        elif not pkg_sha:
            pkg_err = (f'清单缺少 {ed} 版本档更新包的 sha256，'
                       '拒绝下载安装（防篡改校验不可跳过）')
        elif not re.fullmatch(r'[0-9a-fA-F]{64}', pkg_sha):
            pkg_err = (f'清单 {ed} 版本档更新包的 sha256 格式非法，'
                       '拒绝下载安装')
    return {
        'ok': not pkg_err,
        # 包不可用时不算"有更新"：前端只看 has_update 就展示安装按钮，
        # 否则用户看到"新版本可用"却永远装不上，也看不到中文错误原因。
        'has_update': has and not pkg_err,
        'current': cur,
        'latest': latest,
        'edition': ed,
        'package': {
            'size': int(pkg.get('size') or 0),
            'sha256': pkg_sha,
            'urls': [u for u in (pkg.get('urls') or []) if u],
        },
        'notes_zh': str(mf.get('notes_zh') or ''),
        'notes_en': str(mf.get('notes_en') or ''),
        'released_at': str(mf.get('released_at') or ''),
        'skipped': skipped,
        'paused': paused,
        'rollout_percent': rollout,
        'in_rollout': in_rollout,
        'signature': sig_state,
        'warning': warning,
        'error': pkg_err,
        'checked_at': time.strftime('%Y-%m-%d %H:%M:%S'),
    }


# ------------------------------------------------------------------
# 状态存取（内存态 + run/updates/state.json 落盘态）
# ------------------------------------------------------------------
_state_lock = threading.Lock()
_state = {
    'phase': 'idle',      # idle | checking | download | staged | awaiting_idle
                          # | applying | error
    'detail': '',
    'progress': None,     # 0-100（下载进度）
    'latest': '',
    'ts': 0.0,
}
_state_file = os.path.join(_UPDATES_DIR, 'state.json')


def _set_state(phase, detail='', progress=None):
    with _state_lock:
        _state.update(phase=phase, detail=detail, progress=progress,
                      ts=time.time())
        snap = dict(_state)
    try:
        os.makedirs(_UPDATES_DIR, exist_ok=True)
        with open(_state_file, 'w', encoding='utf-8') as f:
            json.dump(snap, f, ensure_ascii=False, indent=1)
    except OSError:
        pass
    return snap


def get_status():
    """给 /api/update/status 的合并视图：内存态 + 暂存区现状 + 版本信息。"""
    with _state_lock:
        snap = dict(_state)
    snap['current'] = current_version()
    snap['edition'] = current_edition()
    snap['frozen'] = _is_frozen()
    stage, ver = current_staged()
    if stage:
        snap['staged_version'] = ver
        if snap['phase'] in ('idle', 'error'):
            snap['phase'] = 'staged'
    return snap


def _read_state_file():
    try:
        with open(_state_file, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def set_skip_version(ver):
    """「跳过此版本」：落 state.json，徽标/自动流程不再提示（下次发版自动失效）。"""
    st = _read_state_file()
    st['skip_version'] = str(ver or '').strip()
    st['skip_at'] = time.strftime('%Y-%m-%d %H:%M:%S')
    try:
        os.makedirs(_UPDATES_DIR, exist_ok=True)
        with open(_state_file, 'w', encoding='utf-8') as f:
            json.dump(st, f, ensure_ascii=False, indent=1)
    except OSError:
        pass


# ------------------------------------------------------------------
# 下载 -> 校验 -> 暂存
# ------------------------------------------------------------------
class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    """重定向逐跳校验（照 public_data._SafeRedirectHandler 同款思路）。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        ok, why = check_update_url(newurl)
        if not ok:
            raise urllib.error.URLError(f'重定向目标不在更新源白名单: {why}')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _opener():
    return urllib.request.build_opener(_SafeRedirect())


def _sha256_file(path):
    import hashlib
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def _ver_dir(ver):
    return os.path.join(_UPDATES_DIR, re.sub(r'[^0-9A-Za-z._-]', '_', str(ver)))


def staged_dir(ver):
    return os.path.join(_ver_dir(ver), 'staged')


def current_staged():
    """暂存区现状：返回 (staged路径, 版本) 或 (None, '')。

    以 staged 内 build_info.json 的 version 为权威（防目录名与内容不符），
    目录名只做定位。"""
    try:
        names = sorted(os.listdir(_UPDATES_DIR))
    except OSError:
        return None, ''
    for n in names:
        d = os.path.join(_UPDATES_DIR, n)
        if not os.path.isdir(d):
            continue
        st = os.path.join(d, 'staged')
        if not os.path.isdir(st):
            continue
        v = n
        try:
            with open(os.path.join(st, 'build_info.json'),
                      encoding='utf-8') as f:
                v = str((json.load(f) or {}).get('version') or '').strip() or n
        except (OSError, ValueError):
            pass
        if os.path.isfile(os.path.join(st, 'VirusPlatform.exe')) and \
                os.path.isdir(os.path.join(st, '_internal')):
            return st, v
    return None, ''


def validate_staged(stage, ver=None):
    """暂存内容合法性检查。返回 (True, '') 或 (False, 原因)。

    staged 布局 = build_update.py 产出的更新包根：VirusPlatform.exe +
    _internal/（非空）+ mcp_server.py + build_info.json（version 需一致）。
    """
    if not stage or not os.path.isdir(stage):
        return False, '暂存目录不存在'
    if not os.path.isfile(os.path.join(stage, 'VirusPlatform.exe')):
        return False, '暂存区缺少 VirusPlatform.exe'
    internal = os.path.join(stage, '_internal')
    if not os.path.isdir(internal):
        return False, '暂存区缺少 _internal/'
    try:
        next(os.scandir(internal))
    except StopIteration:
        return False, '暂存区 _internal/ 为空'
    if not os.path.isfile(os.path.join(stage, 'mcp_server.py')):
        return False, '暂存区缺少 mcp_server.py'
    try:
        with open(os.path.join(stage, 'build_info.json'),
                  encoding='utf-8') as f:
            v = str((json.load(f) or {}).get('version') or '').strip()
    except (OSError, ValueError):
        return False, '暂存区缺少/损坏 build_info.json'
    if ver and v != str(ver):
        return False, f'暂存版本 {v} 与目标版本 {ver} 不一致'
    return True, ''


# ------------------------------------------------------------------
# 下载（多地址回退 + Range 断点续传 + sha256）
# ------------------------------------------------------------------
def _download_one(url, dest, progress=None):
    """单地址下载到 dest（.part 续传），成功后原子改名。失败抛异常。"""
    part = dest + '.part'
    pos = os.path.getsize(part) if os.path.isfile(part) else 0
    headers = {'User-Agent': _UA}
    if pos:
        headers['Range'] = f'bytes={pos}-'
    req = urllib.request.Request(url, headers=headers)
    with _opener().open(req, timeout=60) as resp, \
            open(part, 'ab' if pos and getattr(resp, 'status', None) == 206
                 else 'wb') as f:
        total = pos + int(resp.headers.get('Content-Length') or 0)
        done = pos if f.mode == 'ab' else 0
        while True:
            chunk = resp.read(1024 * 1024)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)
    os.replace(part, dest)
    return done, total


def _download_package(urls, dest, progress=None, expected_sha256=''):
    """多地址下载 + sha256 校验。成功返回 dest，全失败抛 RuntimeError。

    sha256 **强制**（2026-11-06）：清单没给哈希就不下载——旧实现
    `if expected_sha256 and ...` 让缺字段等同于"免校验"，是可静默跳过
    的校验缺口。"""
    expected = str(expected_sha256 or '').strip()
    if not expected:
        raise RuntimeError('清单未提供 sha256，拒绝下载安装')
    if not re.fullmatch(r'[0-9a-fA-F]{64}', expected):
        raise RuntimeError(f'清单 sha256 格式非法: {expected[:16]}…')
    urls = [u for u in (urls or []) if u]
    if not urls:
        raise RuntimeError('未提供下载地址')
    errors = []
    for u in urls:
        ok, why = check_update_url(u)    # 白名单不通过的地址直接记失败
        if not ok:
            errors.append(f'{u} → 白名单拒绝: {why}')
            continue
        for attempt in (1, 2):                      # 每地址重试一次
            try:
                _download_one(u, dest, progress=progress)
                got = _sha256_file(dest)
                if got.lower() != expected.lower():
                    raise RuntimeError(
                        f'更新包 sha256 不匹配（期望 {expected[:12]}…，'
                        f'实际 {got[:12]}…）——下载可能损坏，请重试')
                return dest
            except Exception as e:                  # noqa: BLE001
                try: os.remove(dest) if os.path.exists(dest) else None
                except OSError:
                    pass
                if attempt == 2:
                    errors.append(f'{u} → {type(e).__name__}: {e}')
                else:
                    time.sleep(1.5)
    raise RuntimeError('所有下载地址均失败：\n' + '\n'.join(errors))


def download_package(info=None, progress=None):
    """下载并暂存更新包。info 缺省时自动 check_update()。

    流程：校验 -> 下载到 run/updates/<ver>/package.zip -> sha256 ->
    安全解压到 staged.tmp -> validate_staged -> 原子改名为 staged/。
    返回 (staged_path, ver)。
    """
    if info is None:
        info = check_update(force=True)
    if not info.get('ok'):
        raise RuntimeError(info.get('error') or '更新清单校验未通过，拒绝下载')
    if not info.get('has_update'):
        raise RuntimeError(f'已是最新版本 {info.get("current")}')
    pkg = info.get('package') or {}
    ver = str(info.get('latest') or '').strip()
    if not ver:
        raise RuntimeError('目标版本未知')
    d = _ver_dir(ver)
    os.makedirs(d, exist_ok=True)
    dest = os.path.join(d, 'package.zip')
    _download_package(pkg.get('urls'), dest,
                      progress=progress,
                      expected_sha256=str(pkg.get('sha256') or ''))
    stage = staged_dir(ver)
    # 先解到同卷的 staged.tmp，全部校验通过后才原子改成 staged：
    # 解压中途失败/进程退出都不会留下"看起来已暂存"的半成品
    # （current_staged 只认 staged/ 目录，2026-11-06）。
    tmp_stage = stage + '.tmp'
    _rmtree_force(tmp_stage)
    try:
        _safe_extract_zip(dest, tmp_stage)
        ok, why = validate_staged(tmp_stage, ver=ver)
    except Exception:
        _rmtree_force(tmp_stage)
        raise
    if not ok:
        _rmtree_force(tmp_stage)
        _rmtree_force(stage)
        _rmtree_force(d)
        raise RuntimeError(f'暂存校验失败：{why}')
    _rmtree_force(stage)        # 旧暂存（正常不存在）让位给原子替换
    try:
        os.replace(tmp_stage, stage)
    except OSError:
        _rmtree_force(tmp_stage)
        raise
    return stage, ver


# ------------------------------------------------------------------


# ------------------------------------------------------------------
# 安全解压（防 zip-slip；照 plugin_manager._safe_extract 同款防御）
# ------------------------------------------------------------------
def _safe_extract_zip(zip_path, dest):
    """先逐成员校验路径，全部合法再 extractall（拒绝绝对路径/盘符/..穿越/NUL）。"""
    with zipfile.ZipFile(zip_path) as z:
        for m in z.namelist():
            if m.endswith('/'):
                continue
            _check_member_path(m)
        z.extractall(dest)


def _check_member_path(member):
    """zip 成员路径校验。"""
    if '\x00' in member:
        raise RuntimeError(f'zip 内含非法路径（NUL）: {member!r}')
    norm = member.replace('\\', '/')
    if re.match(r'^[A-Za-z]:', norm):
        raise RuntimeError(f'zip 内含绝对路径（盘符）: {member!r}')
    if norm.startswith('/'):
        raise RuntimeError(f'zip 内含绝对路径: {member!r}')
    parts = [p for p in norm.split('/') if p not in ('', '.')]
    if any(p == '..' for p in parts):
        raise RuntimeError(f'zip 内含 .. 路径穿越: {member!r}')


def _rmtree_force(path):
    """shutil.rmtree 只读位兜底版（Windows 上 zip 解出的文件常带只读位）。"""
    import stat

    def _onerr(func, p, _exc):
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except OSError:
            pass

    shutil.rmtree(path, onerror=_onerr)


# ------------------------------------------------------------------
# 安装（生成换装脚本 + 主进程退出由 bat 接手）
# ------------------------------------------------------------------
def _write_apply_bat(staged, ver, mode, pid=None):
    """生成 apply_update.bat（UTF-8 无 BOM + chcp 65001，中文路径可用）。

    流程：等主进程退出 -> 旧 exe/_internal/mcp_server.py/build_info.json
    改名 .old 备份 -> 防御性检查 staged 完整 -> 换入 -> 按模式重启 ->
    任一步失败自动删除已换入的新件并把 .old 换回。

    2026-11-06 修复：build_info.json 是版本真源（current_version/
    current_edition 都读安装根这一份），旧脚本换入时漏掉它、又 `rd /s /q`
    把 staged 里的副本删掉，导致版本永远不变 → 每次启动都判定有新版本 →
    自动重装重启死循环。mcp_server.py 也补了 .old 备份（原先换入后失败
    无法还原）。失败步骤写进 last_apply.txt 的 FAILED 行，便于事后定位。
    """
    pid = pid or os.getpid()
    bat_path = os.path.join(_UPDATES_DIR, 'apply_update.bat')
    root_q = PLATFORM_ROOT.replace('/', '\\')
    staged_q = staged.replace('/', '\\')
    updir_q = _UPDATES_DIR.replace('/', '\\')

    lines = [
        '@echo off',
        '>nul 2>&1 chcp 65001',
        'setlocal enableextensions',
        f'set "ROOT={root_q}"',
        f'set "STAGED={staged_q}"',
        f'set "UPDIR={updir_q}"',
        f'set "WAIT_PID={pid}"',
        f'set "MODE={mode}"',
        'set "EXE=%ROOT%\\VirusPlatform.exe"',
        'set "INTERNAL=%ROOT%\\_internal"',
        'set "MCP=%ROOT%\\mcp_server.py"',
        'set "BUILDINFO=%ROOT%\\build_info.json"',
        'set "FAILSTEP=unknown"',
        'set "BACKED_EXE=0"',
        'set "BACKED_INTERNAL=0"',
        'set "BACKED_MCP=0"',
        'set "BACKED_BUILDINFO=0"',
        '',
        'rem ---- 1. 等主进程退出（上限 180 秒）----',
        'set /a N=0',
        ':waitloop',
        'tasklist /FI "PID eq %WAIT_PID%" 2>nul | find /I "%WAIT_PID%" >nul 2>nul',
        'if errorlevel 1 goto swap',
        'set /a N+=1',
        f'if %N% GEQ {_WAIT_PID_MAX} (',
        '    set "FAILSTEP=wait_timeout"',
        '    goto restore',
        ')',
        'ping -n 2 127.0.0.1 >nul',
        'goto waitloop',
        '',
        'rem ---- 2. 旧件备份改名（改名即备份，同卷 rename 瞬时完成）----',
        ':swap',
        'rem 先清上一轮遗留的 .old：当前正在运行的版本才是可信新件，陈旧备份',
        'rem 会让下面的 ren 因重名失败。BACKED_* 记录本轮备份是否真的成功，',
        'rem 还原段只处理本轮备份过的项（防止误删仍在使用的原件）。',
        'if exist "%EXE%.old" del /f /q "%EXE%.old" 2>nul',
        'if exist "%EXE%.old" rd /s /q "%EXE%.old" 2>nul',
        'if exist "%INTERNAL%.old" rd /s /q "%INTERNAL%.old" 2>nul',
        'if exist "%INTERNAL%.old" del /f /q "%INTERNAL%.old" 2>nul',
        'if exist "%MCP%.old" del /f /q "%MCP%.old" 2>nul',
        'if exist "%MCP%.old" rd /s /q "%MCP%.old" 2>nul',
        'if exist "%BUILDINFO%.old" del /f /q "%BUILDINFO%.old" 2>nul',
        'if exist "%BUILDINFO%.old" rd /s /q "%BUILDINFO%.old" 2>nul',
        'if exist "%EXE%" (',
        '    ren "%EXE%" "VirusPlatform.exe.old"',
        '    if errorlevel 1 (',
        '        set "FAILSTEP=backup_exe"',
        '        goto restore',
        '    )',
        '    set "BACKED_EXE=1"',
        ')',
        'if exist "%INTERNAL%" (',
        '    ren "%INTERNAL%" "_internal.old"',
        '    if errorlevel 1 (',
        '        set "FAILSTEP=backup_internal"',
        '        goto restore',
        '    )',
        '    set "BACKED_INTERNAL=1"',
        ')',
        'if exist "%MCP%" (',
        '    ren "%MCP%" "mcp_server.py.old"',
        '    if errorlevel 1 (',
        '        set "FAILSTEP=backup_mcp_server"',
        '        goto restore',
        '    )',
        '    set "BACKED_MCP=1"',
        ')',
        'if exist "%BUILDINFO%" (',
        '    ren "%BUILDINFO%" "build_info.json.old"',
        '    if errorlevel 1 (',
        '        set "FAILSTEP=backup_build_info"',
        '        goto restore',
        '    )',
        '    set "BACKED_BUILDINFO=1"',
        ')',
        '',
        'rem ---- 3. 防御检查：staged 完整（缺件不换装，直接还原）----',
        'set "FAILSTEP=staged_incomplete"',
        'if not exist "%STAGED%\\VirusPlatform.exe" goto restore',
        'if not exist "%STAGED%\\_internal" goto restore',
        'if not exist "%STAGED%\\mcp_server.py" goto restore',
        'if not exist "%STAGED%\\build_info.json" goto restore',
        '',
        'rem ---- 4. 换入（move 同卷瞬时，跨卷整树复制）----',
        'set "FAILSTEP=swap_exe"',
        'move /y "%STAGED%\\VirusPlatform.exe" "%EXE%" >nul',
        'if errorlevel 1 goto restore',
        'set "FAILSTEP=swap_internal"',
        'move /y "%STAGED%\\_internal" "%INTERNAL%" >nul',
        'if errorlevel 1 goto restore',
        'set "FAILSTEP=swap_mcp_server"',
        'move /y "%STAGED%\\mcp_server.py" "%MCP%" >nul',
        'if errorlevel 1 goto restore',
        'set "FAILSTEP=swap_build_info"',
        'move /y "%STAGED%\\build_info.json" "%BUILDINFO%" >nul',
        'if errorlevel 1 goto restore',
        '',
        'rem ---- 5. 清理 staged 与 zip 残件 ----',
        'rd /s /q "%STAGED%" 2>nul',
        'del /f /q "%UPDIR%\\package.zip" 2>nul',
        'del /f /q "%UPDIR%\\package.zip.part" 2>nul',
        'echo OK > "%UPDIR%\\last_apply.txt"',
        '',
        'rem ---- 6. 重启（web -> --web --no-open；gui -> 默认；none -> 不启动）----',
        'if /I "%MODE%"=="web" (',
        '    start "" /D "%ROOT%" "%EXE%" --web --no-open',
        ') else if /I "%MODE%"=="gui" (',
        '    start "" /D "%ROOT%" "%EXE%"',
        ') else if /I "%MODE%"=="none" (',
        '    rem CLI 场景：换装完成不自动重启，用户手动再启动',
        ')',
        'exit /b 0',
        '',
        'rem ---- R. 失败还原（先清掉已换入的新件，再把 .old 换回；----',
        'rem ----    staged 保留供排查。仅处理本轮真正备份成功的项 ----',
        'rem ----    （BACKED_*=1），避免备份失败时误删仍在用的原件。）----',
        ':restore',
        'if "%BACKED_EXE%"=="1" (',
        '    if exist "%EXE%" del /f /q "%EXE%" 2>nul',
        '    ren "%EXE%.old" "VirusPlatform.exe" 2>nul',
        ')',
        'if "%BACKED_INTERNAL%"=="1" (',
        '    if exist "%INTERNAL%" rd /s /q "%INTERNAL%" 2>nul',
        '    ren "%INTERNAL%.old" "_internal" 2>nul',
        ')',
        'if "%BACKED_MCP%"=="1" (',
        '    if exist "%MCP%" del /f /q "%MCP%" 2>nul',
        '    ren "%MCP%.old" "mcp_server.py" 2>nul',
        ')',
        'if "%BACKED_BUILDINFO%"=="1" (',
        '    if exist "%BUILDINFO%" del /f /q "%BUILDINFO%" 2>nul',
        '    ren "%BUILDINFO%.old" "build_info.json" 2>nul',
        ')',
        'echo FAILED [%FAILSTEP%] > "%UPDIR%\\last_apply.txt"',
        'exit /b 1',
    ]
    os.makedirs(_UPDATES_DIR, exist_ok=True)
    # newline='' 禁用换行翻译：lines 已用 '\r\n' 拼接，再让文本层翻译一次
    # 会写出 '\r\r\n'（cmd 虽能容忍，但行尾不干净，回读/比对也容易踩坑）。
    with open(bat_path, 'w', encoding='utf-8', newline='') as f:
        f.write('\r\n'.join(lines) + '\r\n')
    with open(bat_path, encoding='utf-8') as f:
        content = f.read()
    for token in ('%EXE%.old', ':restore', ':swap', 'tasklist',
                  ':waitloop', 'move /y', '%STAGED%\\build_info.json',
                  'build_info.json.old', 'mcp_server.py.old', 'FAILSTEP',
                  'BACKED_BUILDINFO'):
        if token not in content:
            raise RuntimeError(f'apply_update.bat 生成校验失败（缺 {token}）')
    return bat_path


def request_shutdown():
    """请求主进程退出（供换装）：先走 app.py 注册的 srv.shutdown（不再收
    新请求，正在响应的请求尽快收尾），**随后必须 os._exit 硬退**。

    为什么不能只靠回调：srv.shutdown 只停 werkzeug 的 serve_forever 线程；
    app.py 的主线程要么阻塞在 pywebview 窗口消息循环、要么停在
    `while True: time.sleep(3600)` 心跳循环——进程不会自然退出。而换装
    bat 在等本进程消失，等不到 180 秒后会回滚更新。实机审查发现的坑
    （2026-11-05）：沙箱测试里等待目标是不存在的假 PID 直接放行，测不出
    这个问题。换装时无活动任务（install_update 已守卫），硬退安全。"""
    cb = _shutdown_fn
    if cb:
        try:
            cb()
        except Exception as e:                          # noqa: BLE001
            logging.getLogger(__name__).warning(
                '更新换装停机回调失败（马上强制退出，仅记录）: %s: %s',
                type(e).__name__, e)
    os._exit(0)


def set_shutdown_callback(fn):
    """app.py 启动服务后注册 srv.shutdown；换装时优雅停机。"""
    global _shutdown_fn
    _shutdown_fn = fn


_shutdown_fn = None


def install_update(mode='web'):
    """执行换装：活动任务守卫 -> 生成 bat -> spawn 隐藏 cmd -> 请求停机。

    mode: 'web'（重启 --web --no-open）/ 'gui'（重启桌面窗口）/
    'none'（只换装不重启，CLI 用）。返回 dict（ok/错误原因）。
    """
    if not _is_frozen():
        return {'ok': False, 'error': '源码模式不支持自更新（请用 git pull）'}
    stage, ver = current_staged()
    if not stage:
        return {'ok': False, 'error': '暂存区没有已校验的更新包'}
    ok, why = validate_staged(stage, ver=ver)
    if not ok:
        return {'ok': False, 'error': f'暂存校验失败：{why}'}
    cur_ed = current_edition()
    staged_ed = _staged_edition(stage)
    if staged_ed != cur_ed:
        return {'ok': False,
                'error': (f'暂存包版本档 {staged_ed} 与当前版本档 {cur_ed} '
                          '不一致，拒绝换装（避免 full/minimal 跨档降级）')}
    active = tasks_active()
    if active:
        return {'ok': False, 'awaiting_idle': True,
                'error': '存在运行中/排队任务，将在任务结束后自动安装',
                'active_tasks': active}
    bat = _write_apply_bat(stage, ver, mode)
    _set_state('applying', f'正在换装到 {ver}，平台即将重启')
    try:
        subprocess.Popen(
            ['cmd', '/c', bat],
            creationflags=subprocess.CREATE_NO_WINDOW,
            cwd=_UPDATES_DIR)
    except OSError as e:
        _set_state('error', f'换装脚本启动失败: {e}')
        return {'ok': False,
                'error': f'换装脚本启动失败: {e}（staged 保留，可重试）'}
    # 注意：**不在这里停机**——调用方（web 端点 / CLI / 自动循环）在把
    # 反馈送出（HTTP 响应 / CLI 输出）之后再调 request_shutdown()。
    # 同理实机审查发现：install_update 内部 os._exit 会让 web 的 JSON 响应
    # 与 CLI 的"换装开始"提示都来不及送出，浏览器只看到连接断开。
    return {'ok': True, 'restarting': True, 'version': ver}


_install_lock = threading.Lock()
_install_thread = None


def _wait_idle_then_install(mode):
    """等任务空闲后换装（手动安装遇忙的后台续跑，2026-11-06）。

    旧实现只在 install_update 返回 awaiting_idle 时把"等待空闲后自动继续"
    发给前端，却没有任何线程真的继续——提示永远不兑现。这里把等待登记成
    后台循环：每 _APPLY_WAIT_IDLE 秒复检一次，空闲即换装停机。"""
    while True:
        r = install_update(mode=mode)
        if r.get('ok'):
            request_shutdown()          # 换装脚本已起，硬退让 bat 接手
            return r
        if r.get('awaiting_idle'):
            _set_state('awaiting_idle',
                       '等待运行中/排队任务结束后自动安装')
            time.sleep(_APPLY_WAIT_IDLE)
            continue
        _set_state('error', r.get('error') or '换装未执行')
        return r


def request_install(mode='web'):
    """手动「立即安装」入口：有暂存直接换装；没有则后台下载后自动换装。

    背景（2026-11-05）：自动安装关闭时，Web 手动点「立即安装」不会经过
    自动循环的下载，暂存区为空 → 旧实现直接报"暂存区没有更新包"。这里
    补上"缺包先下载"的后台线程，下载完成后自行换装停机。

    返回 {ok, downloading|restarting, version?, error?}：
    - 暂存已就绪且空闲：同步走 install_update（快速返回）→ 调用方负责停机；
    - 暂存已就绪但有活动任务：登记后台等待线程，空闲后自动换装；返回
      {"ok": True, "downloading": True, "awaiting_idle": True}（下载中
      字段表示"后台在处理"，前端轮询状态即可，调用方**不要**停机）；
    - 需要下载：起 daemon 线程下载 -> 等空闲 -> 换装 -> request_shutdown，
      调用方直接返回 {"ok": True, "downloading": True} 给前端轮询进度。
    """
    global _install_thread
    # 已有后台线程在下载/等待/换装：直接返回"处理中"，避免二次生成
    # 换装脚本、起两个 cmd 争同一批文件（2026-11-06）。
    with _install_lock:
        if _install_thread is not None and _install_thread.is_alive():
            return {'ok': True, 'downloading': True}
    stage, ver = current_staged()
    if stage:
        r = install_update(mode=mode)
        if not r.get('awaiting_idle'):
            return r
        with _install_lock:
            if _install_thread is not None and _install_thread.is_alive():
                return {'ok': True, 'downloading': True,
                        'awaiting_idle': True, 'version': ver,
                        'detail': '等待运行中/排队任务结束后自动安装'}
            _set_state('awaiting_idle',
                       '等待运行中/排队任务结束后自动安装')
            _install_thread = threading.Thread(
                target=_wait_idle_then_install, args=(mode,), daemon=True)
            _install_thread.start()
        return {'ok': True, 'downloading': True, 'awaiting_idle': True,
                'version': ver,
                'detail': '等待运行中/排队任务结束后自动安装'}
    with _install_lock:
        if _install_thread is not None and _install_thread.is_alive():
            return {'ok': True, 'downloading': True}
        info = check_update(force=True)
        if not info.get('ok'):
            return {'ok': False, 'error': info.get('error') or '检查更新失败'}
        if not info.get('has_update'):
            return {'ok': False, 'error': '已是最新版本'}

        def _bg():
            try:
                download_package(info)
                _wait_idle_then_install(mode)
            except Exception as e:              # noqa: BLE001
                _set_state('error', f'{type(e).__name__}: {e}')

        _set_state('download', '正在下载更新包（手动安装）', progress=0)
        _install_thread = threading.Thread(target=_bg, daemon=True)
        _install_thread.start()
        return {'ok': True, 'downloading': True}


# ------------------------------------------------------------------
# 活动任务守卫
# ------------------------------------------------------------------
def tasks_active():
    """扫描 run/tasks/*.json（12 位十六进制 id，排除 queue.json），
    返回活动任务列表 [{id,status,name}]；空列表 = 空闲。"""
    tdir = DIRS['tasks']
    out = []
    try:
        names = os.listdir(tdir)
    except OSError:
        return out
    for n in names:
        if not (n.endswith('.json') and _TASK_ID_RE.fullmatch(n[:-5])):
            continue    # 排除 queue.json 等非任务文件
        try:
            with open(os.path.join(tdir, n), encoding='utf-8') as f:
                d = json.load(f)
        except (OSError, ValueError):
            continue    # 正被并发写入的半截文件：视为非活动
        if str(d.get('status') or '') in _ACTIVE_STATUSES:
            out.append({'id': n[:-5], 'status': str(d.get('status')),
                        'name': str(d.get('name') or '')})
    return out


def _detect_mode():
    """从当前进程启动参数推断重启模式：--web 系 -> web，否则 gui。"""
    for a in sys.argv[1:]:
        if a.strip().lower() in ('--web', '--browser', '--no-gui'):
            return 'web'
    return 'gui'


def _cleanup_stale():
    """启动清理：成功换装后的 .old 备份与过期 staged 目录。

    - last_apply.txt=OK 且新 exe 已在跑 → 删 .old 备份（保留一代语义）
    - last_apply.txt=FAILED → bat 已自行还原，清标记即可
    - staged 版本 <= 当前版本 → 过期暂存，整目录删除
    - package.zip / .part 残件清理
    """
    os.makedirs(_UPDATES_DIR, exist_ok=True)
    try:
        marker = os.path.join(_UPDATES_DIR, 'last_apply.txt')
        txt = ''
        try:
            with open(marker, encoding='utf-8', errors='replace') as f:
                txt = f.read().strip()
        except OSError:
            pass
        if txt == 'OK':
            # 换装脚本会为全部换入件留一代 .old（新增 mcp_server.py /
            # build_info.json 的备份，2026-11-06）；启动成功后统一清理。
            for n in ('VirusPlatform.exe.old', '_internal.old',
                      'mcp_server.py.old', 'build_info.json.old'):
                p = os.path.join(PLATFORM_ROOT, n)
                if os.path.isdir(p):
                    _rmtree_force(p)
                elif os.path.isfile(p):
                    try:
                        os.remove(p)
                    except OSError:
                        pass
            try:
                os.remove(marker)
            except OSError:
                pass
        elif txt.startswith('FAILED'):
            # 2026-11-06 起格式为 'FAILED [失败步骤]'，前缀匹配兼容旧格式
            # 与新的带步骤格式（bat 已自行还原，清标记即可）。
            try:
                os.remove(marker)
            except OSError:
                pass
    except (OSError, ValueError):                       # noqa: BLE001
        pass
    cur = _semver(current_version())
    try:
        for n in os.listdir(_UPDATES_DIR):
            d = os.path.join(_UPDATES_DIR, n)
            if os.path.isdir(d):
                # 注意：不在这里清理 staged.tmp——多实例并存时，另一实例
                # 可能正在解压；download_package 自己会在解压前/失败时清，
                # 中断残留对 current_staged 不可见，交给下次同版本下载覆盖。
                if _semver(n) <= cur:
                    _rmtree_force(d)  # 过期暂存目录（staged + zip）整删
            elif os.path.isfile(d) and (d.endswith('.zip') or '.part' in d):
                try:
                    os.remove(d)
                except OSError:
                    pass
    except OSError:                                     # noqa: BLE001
        pass


# ------------------------------------------------------------------
# 自动更新后台循环（app.py 起 daemon 线程跑）
# ------------------------------------------------------------------
def auto_update_loop():
    """全自动更新守护循环：启动 30 秒后检查 -> 有新版且开启自动安装 ->
    下载暂存 -> 等任务空闲 -> 换装重启。全程异常静默（只写状态），不崩溃。"""
    try:
        _cleanup_stale()
        if not _is_frozen():
            _set_state('idle', '源码模式不自更新（git pull）')
            return
        try:
            from Virus_Platform_Core.config import get_config
            cfg = get_config()
            if not getattr(cfg, 'update_auto_check', True):
                return      # 关了自动检查：什么都不做（手动检查仍可用）
        except Exception:                               # noqa: BLE001
            cfg = None
        time.sleep(30)      # 启动稳定后再查，避开启动高峰
        while True:
            try:
                info = check_update(force=True)
                latest = str(info.get('latest') or '')
                # ok=False（清单缺 sha256/签名不符/full 包缺失等）一律不下载：
                # 旧实现只看 has_update，会把不可信清单带进下载流程。
                if info.get('ok') and info.get('has_update') and latest:
                    st = _read_state_file()
                    if str(st.get('skip_version') or '') == latest:
                        time.sleep(_CHECK_INTERVAL)
                        continue
                    # 发布控制（manifest 级）：暂停 -> 不下载不换装；
                    # 灰度 -> 本机桶号不在本轮范围则不参与自动更新。
                    if info.get('paused'):
                        _set_state('idle',
                                   f'{latest} 已暂停自动更新（清单 paused）')
                    elif not info.get('in_rollout', True):
                        _set_state(
                            'idle',
                            f"灰度发布中（{info.get('rollout_percent')}%），"
                            f'本机暂未轮到 {latest}')
                    elif getattr(cfg, 'update_auto_apply', True):
                        _auto_apply(cfg, info)
                    else:
                        _set_state('idle',
                                   f'发现新版本 {latest}（自动安装已关闭）')
                else:
                    _set_state('idle', info.get('error')
                               or info.get('checked_at') or '')
            except Exception as e:                      # noqa: BLE001
                try:
                    _set_state('error', f'{type(e).__name__}: {e}')
                except Exception:                       # noqa: BLE001
                    pass
            time.sleep(_CHECK_INTERVAL)                 # 长驻实例每 6 小时复查
    except Exception as e:                              # noqa: BLE001
        # 守护循环永不崩（2026-11-06 审查补充）：但异常必须可见——写进更新
        # 状态（设置页「软件更新」卡可见），不再纯静默 pass 掩盖真实故障。
        try:
            _set_state('error', f'守护循环异常: {type(e).__name__}: {e}')
        except Exception:                               # noqa: BLE001
            pass


def _auto_apply(cfg, info):
    """下载 -> 暂存 -> 等任务空闲 -> 安装。"""
    ver = str(info.get('latest') or '')
    try:
        _set_state('download', f'正在下载 {ver} 更新包',
                   progress=0)
        stage, ver = download_package(info, progress=lambda done, total:
                                      _set_state(
                                          'download',
                                          f'正在下载 {ver} 更新包',
                                          progress=round(done / max(total, 1) * 100, 1)))
        _set_state('staged', f'{ver} 已暂存待换装')
        # 等空闲/重试/停机与手动安装共用同一套逻辑（2026-11-06）。
        _wait_idle_then_install(_detect_mode())
    except Exception as _e:                             # noqa: BLE001
        _set_state('error', f'{type(_e).__name__}: {_e}')
