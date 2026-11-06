# -*- coding: utf-8 -*-
"""工具版本探测回归：_parse_tool_version 只认版本号行（2026-11-03 GQMIX）。

背景：seqkit 不支持 `--version`，stderr 首行 "Error: unknown flag: --version"
被当版本串记进 tool_versions.json；fastp 与 19 线程正式进程同秒冷启动，
10s 探测超时误记 unknown 且被进程内缓存固化。

覆盖：
  1. fastp 正常输出（含版本号行）→ 原样返回该行；
  2. 纯错误文本（无版本号）→ 'unknown'（不再把 Error 行当版本串）；
  3. 多行混合（错误行在前、版本行在后）→ 取版本行；
  4. 空输出 → 'unknown'；
  5. unknown 不写入进程内缓存（临时态可重试，见 probe_tool_versions）。
全程离线，秒级，不碰真实 run/。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from Virus_Platform_Core import utils  # noqa: E402


def check(cond, msg):
    print(('  ok ' if cond else '  FAIL ') + msg, flush=True)
    assert cond, msg


def main():
    parse = utils._parse_tool_version

    check(parse(b'fastp 1.3.6\n') == 'fastp 1.3.6',
          'fastp 正常输出 → 返回版本行')

    check(parse(b'Error: unknown flag: --version\n'
                b'Usage:\n  seqkit [command]\n') == 'unknown',
          '纯错误文本（无版本号）→ unknown')

    check(parse(b'Error: unknown flag: --version\n'
                b'fastp 0.23.4\n') == 'fastp 0.23.4',
          '错误行在前、版本行在后 → 取版本行')

    check(parse(b'') == 'unknown', '空输出 → unknown')
    check(parse(b'   \n\n') == 'unknown', '空白输出 → unknown')

    # unknown 不固化进缓存：probe_tool_versions 只缓存成功解析的版本
    probe_tools = {'x': ''}          # 空 exe 路径 → 直接 unknown
    r = utils.probe_tool_versions(probe_tools)
    check(r['x'] == 'unknown', '空路径 → unknown')
    check(not utils._TOOL_VERSION_CACHE,
          'unknown 不写入进程内缓存（可下次重试）')

    print('tool version probe: all ok')


if __name__ == '__main__':
    main()
