"""Tests for mcp_tools (Fase 5): conversor a Gemini + normalización de URL."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mcp_tools import (  # noqa: E402
    HermesMCPClient,
    MCP_DENY,
    mcp_tools_to_gemini_declarations,
)


class TestConverter(unittest.TestCase):
    def test_prefix_and_rename(self):
        tools = [{
            "name": "read_memory",
            "description": "Lee la memoria",
            "inputSchema": {
                "type": "object",
                "properties": {"x": {"type": "string"}},
                "additionalProperties": False,
            },
        }]
        out = mcp_tools_to_gemini_declarations(tools)
        self.assertEqual(len(out), 1)
        d = out[0]
        self.assertEqual(d["name"], "mcp_read_memory")
        self.assertEqual(d["parameters"]["type"], "object")
        self.assertNotIn("additionalProperties", d["parameters"])
        self.assertNotIn("inputSchema", d)

    def test_deny_list(self):
        tools = [{"name": "restart_worker"}, {"name": "chat"}]
        out = mcp_tools_to_gemini_declarations(tools)
        self.assertEqual([d["name"] for d in out], ["mcp_chat"])
        self.assertIn("restart_worker", MCP_DENY)

    def test_empty_and_bad_input(self):
        self.assertEqual(mcp_tools_to_gemini_declarations([]), [])
        self.assertEqual(mcp_tools_to_gemini_declarations(None), [])
        self.assertEqual(
            mcp_tools_to_gemini_declarations([{"description": "sin nombre"}]),
            [],
        )

    def test_nested_sanitize(self):
        tools = [{
            "name": "t",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "a": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {},
                        },
                    }
                },
            },
        }]
        out = mcp_tools_to_gemini_declarations(tools)
        items = out[0]["parameters"]["properties"]["a"]["items"]
        self.assertNotIn("additionalProperties", items)


class TestClientUrl(unittest.TestCase):
    def test_url_normalization(self):
        self.assertEqual(HermesMCPClient("http://x:1")._url, "http://x:1/mcp")
        self.assertEqual(HermesMCPClient("http://x:1/mcp")._url, "http://x:1/mcp")
        self.assertEqual(HermesMCPClient("http://x:1/")._url, "http://x:1/mcp")


if __name__ == "__main__":
    unittest.main()
