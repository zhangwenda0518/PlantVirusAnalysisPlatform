# -*- coding: utf-8 -*-
"""AI 代理自举指南（对标 organelleverse 的 agent_guide）——只读、零副作用。

为什么要有它（2026-11-01）：外部 agent 拿到平台的第一件事是"学会用"：
`python main.py agent-guide` 打印完整指南（仓库根 AGENTS.md；源码模式读平台根，
冻结版读随包副本），`--skill` 打印一张可存为
`.agents/skills/virusplatform/SKILL.md` 的指针技能卡。

只读承诺：本模块不发现操作、不启动任务、不装依赖、不改任何设置——
"读指南"不等于"已学会干活"，更不等于任何权限。
"""
from __future__ import annotations

import os
import sys

__all__ = ['read_guide', 'read_skill', 'main']

_FALLBACK_GUIDE = """\
# VirusPlatform — AI 代理指南（内嵌兜底版）

盘上指南文件（AGENTS.md）未找到——正常应随包分发（冻结版在 _internal/）或
在仓库根（源码模式）。以下是最小口径，完整版以随包/仓库的 AGENTS.md 为准。

- 三个入口共享同一引擎：Web GUI（app.py / exe）、CLI（main.py，40+ 子命令）、
  MCP（mcp_server.py / exe --mcp-stdio，vp_* 工具）。
- 干活前先查操作目录：`python main.py agent-ops`（54+ 条操作，含参数 schema），
  `agent-ops --id <op> --schema` 查单条；不要猜参数。
- 执行单：AI 只能起草待批执行单（propose_action），批准后才会运行；
  create_sample 是同步动作（返回样品名、无任务号）。
- 计划 ≠ 已执行：只有任务中心出现真实任务 id 且终态 done、或亲自读到落盘
  产物，才能说「已开始/已完成」；结果判读只依据真实文件。
"""


def _guide_path():
    """AGENTS.md 定位：冻结版读 _MEIPASS（= _internal），源码模式读平台根。"""
    if getattr(sys, 'frozen', False):
        base = getattr(sys, '_MEIPASS', '') or ''
    else:
        from Virus_Platform_Core.config import PLATFORM_ROOT
        base = PLATFORM_ROOT
    return os.path.join(base, 'AGENTS.md')


def read_guide() -> str:
    """返回完整代理指南；盘上文件缺失时回退内嵌兜底版（不抛异常）。"""
    try:
        with open(_guide_path(), encoding='utf-8') as f:
            t = f.read().strip()
        if t:
            return t
    except OSError:
        pass
    return _FALLBACK_GUIDE


_SKILL = """\
---
name: virusplatform
description: >
  在本机用 VirusPlatform（植物病毒分析平台 / PVAP）干活：建样品、跑 15 阶段
  流水线、调工具箱、公共数据检索下载、NCBI 提交、读结果与排障；也用于改这个
  平台本身（加工具/加阶段/加页面/测试/打包）。
  Use the local VirusPlatform (plant-virus analysis platform) for sample
  pipelines, toolbox analyses, public-data retrieval, NCBI submission, result
  interpretation, troubleshooting, or extending the platform itself.
  触发词：植物病毒、病毒分析平台、PVAP、VirusPlatform、建样品、跑组装、跑流水线、
  跑个鉴定、工具箱、公共检索、NCBI 提交、任务中心、历史归档、结果在哪、为什么失败、
  排障、加工具、加阶段、打包、部署。
  三张卡分工：本卡=能干吗/该用哪张；`virusplatform-ops`=怎么干与怎么判读；
  `virusplatform-dev`=怎么改平台。
agent_created: true
---

# VirusPlatform 技能导航（指针卡）

改平台之前先读 `virusplatform-dev`；干活（跑分析、查任务、判读结果）读
`virusplatform-ops`。本卡只回答「这平台能干什么、权限到哪、先做哪四步」。

## 60 秒上手（四步，别跳）

1. **读指南**：`python main.py agent-guide`（打包版 `VirusPlatform.exe --cli agent-guide`）。
2. **查目录**：`python main.py agent-ops`；单条 schema `agent-ops --id <op> --schema`。
   条数与 id 一律以输出为准，不要记死数字。
3. **校环境**：`python main.py selfcheck`；外部工具 `python main.py tools`。
4. **认实例**：同一台机可能同时跑**源码实例**与**打包实例**，`run/` 目录不同；
   改动没生效先确认 8765 端口对应哪个进程、哪个工作目录。

## 你的需求 → 该读哪张卡

| 需求 | 入口 | 卡片 |
|---|---|---|
| 建样品、跑全链路/单阶段、工具箱、公共检索→下载、NCBI 提交 | CLI / MCP / Web | `virusplatform-ops` |
| 结果在哪、数字对不对、任务失败怎么查 | CLI / MCP | `virusplatform-ops` |
| 改代码：加工具箱工具、加管线阶段、加页面/API、跑测试、打包发布 | 源码 + `tests/` | `virusplatform-dev` |
| 这平台能不能替我做 X、权限边界在哪 | — | 本卡 |

## 五条红线

1. **计划 ≠ 已执行**：执行单起草成功只代表「待批」；只有真实任务 id 终态
   `done`，或你亲自读到落盘产物，才能说「已开始/已完成」。
2. **结果只依据真实文件**：`run/results/<样品>/0X_*/` 下的产物才是结论来源；
   不许由执行单、日志摘要或上一次结果推断数字。
3. **不编造指标、不改结果与 anchor 台账**；失败按原文转述，缺依赖就说缺依赖。
4. **不可逆操作先确认**：删除归档、全局重置、批量归档都有确认令牌
   （`{"confirm": "archive"|"purge"|"reset"}`）；工具运行目录一旦永久删除不可恢复。
5. **先查目录再用**：不猜参数、不假设 op id 等于函数名。

## 权限与边界

- 坞内 AI **只能起草**执行单（`propose_action`），用户点「批准执行」才运行；
  删除类/全局重置类动作永不进白名单。
- 外部 agent 经 MCP 能否直接执行，取决于宿主权限策略；平台侧不因本卡放宽。
- 平台只监听本机回环；下载只走 NCBI/ENA/NGDC 官方域名白名单。
- 本卡不授予任何执行或下载权限；宿主策略永远优先。

## 找不到答案时按顺序查

`docs/DEVELOPMENT_NOTES.md`（决策与踩坑，按日期倒序）→ `docs/mcp_server.md`（MCP 口径）
→ `python main.py agent-ops`（操作与参数）→ `docs/HANDOFF_*.md`（阶段性交接）。
"""


def read_skill() -> str:
    """返回指针技能卡（随包内置文本，不依赖盘上文件）。"""
    return _SKILL


def main(argv=None) -> int:
    """CLI：默认打印指南，--skill 打印技能卡。返回退出码 0。"""
    import argparse
    ap = argparse.ArgumentParser(
        description='打印 AI 代理使用指南 / 技能卡（只读，零副作用）')
    ap.add_argument('--skill', action='store_true',
                    help='打印可存为 .agents/skills/virusplatform/SKILL.md '
                         '的技能卡')
    a = ap.parse_args(argv)
    sys.stdout.write(read_skill() if a.skill else read_guide())
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
