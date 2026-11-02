# -*- coding: utf-8 -*-
"""AI 工具 spec 结构自检 + DSML 泄漏清洗单测。

2026-10-30：propose_action 的 spec 曾缺 `'name'` 字段 → deepseek 官方 API
对整个请求 422 → chat 层「降级重试」把工具整轮禁用 → 模型凭 system prompt
里的工具描述"演"工具调用，把内部 DSML 标记当正文吐出（用户截图的
`<|DSML|invoke name="mcp_tool_catalog">`）。本文件防止两件事复发：
  1) TOOLS_SPEC 结构再被改坏（缺 name/parameters → 上游 422 → 降级泄漏）；
  2) 泄漏文本进对话历史（_strip_dsml 入库前剥离）。
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from Virus_Platform_Core.ai_agent import chat, tools as ai_tools


class ToolSpecSanityTests(unittest.TestCase):
    """任何上游 OpenAI 协议端点都要求 tools[].function 至少有 name。"""

    def test_every_tool_has_name(self):
        for i, entry in enumerate(ai_tools.TOOLS_SPEC):
            fn = (entry or {}).get('function') or {}
            self.assertTrue(fn.get('name'),
                            f'TOOLS_SPEC[{i}] 缺 function.name → 上游会 422')
            self.assertEqual(entry.get('type'), 'function',
                             f'TOOLS_SPEC[{i}] type 必须是 function')

    def test_every_tool_has_parameters_object(self):
        for i, entry in enumerate(ai_tools.TOOLS_SPEC):
            fn = (entry or {}).get('function') or {}
            params = fn.get('parameters')
            self.assertIsInstance(params, dict,
                                  f'TOOLS_SPEC[{i}].parameters 必须是 object schema')
            self.assertEqual(params.get('type'), 'object',
                             f'TOOLS_SPEC[{i}].parameters.type 必须 object')

    def test_propose_action_enum_actions(self):
        fn = next(e['function'] for e in ai_tools.TOOLS_SPEC
                  if e['function'].get('name') == 'propose_action')
        actions = fn['parameters']['properties']['action'].get('enum') or []
        for a in ('rerun_stage', 'retry_failed', 'download_runs', 'run_tool',
                  'create_sample', 'build_host_db', 'plan'):
            self.assertIn(a, actions)


class StripDsmlTests(unittest.TestCase):

    def test_strips_invoke_block(self):
        text = ('我先查一下当前可用的工具口径。\n'
                '<|DSML|calls><|DSML|invoke name="mcp_tool_catalog">'
                '</|DSML|invoke></|DSML|calls>')
        out = chat._strip_dsml(text)
        self.assertNotIn('DSML', out)
        self.assertIn('我先查一下', out)

    def test_plain_text_untouched(self):
        self.assertEqual(chat._strip_dsml('正常正文没有标记'), '正常正文没有标记')
        self.assertEqual(chat._strip_dsml(''), '')
        self.assertEqual(chat._strip_dsml(None), None)

    def test_nested_or_long_tag(self):
        text = 'a<|DSML|invoke name="mcp_call">{"x":1}</|DSML|invoke>b'
        self.assertNotIn('DSML', chat._strip_dsml(text))


class StreamGuardTests(unittest.TestCase):
    """流式出口必须也不漏标记（逐字渲染，入库剥干净没用）。"""

    def _run(self, chunks):
        g = chat._DsmlStreamGuard()
        out = ''.join(g.feed(c) for c in chunks) + g.flush()
        return out, g

    def test_single_chunk_leak_removed(self):
        out, g = self._run(['已收到确认，起草执行单\n'
                            '<|DSML|invoke name="propose_action">'
                            '</|DSML|invoke>'])
        self.assertNotIn('DSML', out)
        self.assertIn('已收到确认', out)
        self.assertTrue(g.stripped)

    def test_marker_split_across_chunks(self):
        # 标记被 token 切成多片（真实流式就是这样）——一片都不许漏到前端
        chunks = ['已收到确认', '<|DS', 'ML|invoke name="prop', 'ose_action">',
                  '</|DSML|', 'invoke>', '，需要你批准。']
        out, g = self._run(chunks)
        self.assertNotIn('DSML', out)
        self.assertNotIn('<|', out)
        self.assertIn('已收到确认', out)
        self.assertIn('需要你批准', out)
        self.assertTrue(g.stripped)

    def test_plain_angle_bracket_not_eaten(self):
        out, g = self._run(['a < b 且 ', 'c > d'])
        self.assertEqual(out, 'a < b 且 c > d')
        self.assertFalse(g.stripped)

    def test_trailing_lone_lt_survives_flush(self):
        out, _ = self._run(['比较 a ', '<'])
        self.assertEqual(out, '比较 a <')

    def test_incomplete_marker_dropped_on_flush(self):
        out, g = self._run(['正文', '<|DSML|invoke name="x"'])
        self.assertEqual(out, '正文')
        self.assertTrue(g.stripped)


if __name__ == '__main__':
    unittest.main()
