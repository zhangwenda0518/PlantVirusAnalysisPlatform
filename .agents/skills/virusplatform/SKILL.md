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
   （网页侧：设置 → 「🧰 工具探测与插件」合一卡——上半 3 列探测总览、
   下半插件源 2 列，检测芯片与安装按钮同行；缺什么点什么补齐。）
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
