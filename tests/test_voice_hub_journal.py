"""Tests de las correcciones de journaling de voz → Hub (5-oct-2026).

1. ``_record_transcript`` alimenta los buffers del journal (voz hablada).
2. ``_hub_flush_turn`` une chunks de transcript en UN evento por dirección.
3. ``send_text(journal=False)`` no journaliza el saludo sintético.
4. ``_call_mcp_tool`` fuerza modo conversacional y aplica tope de tamaño.
"""
from __future__ import annotations

import asyncio
import os
import pathlib
import sys
import tempfile
import types
import unittest
from collections import defaultdict
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bridge  # noqa: E402


def _bare_bridge():
    """GeminiLiveBridge sin __init__ (solo lo que tocan estas rutas)."""
    b = bridge.GeminiLiveBridge.__new__(bridge.GeminiLiveBridge)
    b._hub_in_buf = []
    b._hub_out_buf = []
    b._hub_lock = None
    b._hub_conv_id = "conv:TEST"
    b._hub_endpoint_id = "discord-voice:TEST:TEST"
    b._user_profile = None
    b.metrics = defaultdict(int)
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    os.close(fd)
    b._notes_file = pathlib.Path(path)
    b._maybe_handle_voice_leave_request = lambda text: None
    return b


class _FakeWS:
    def __init__(self):
        self.sent = []

    async def send(self, data):
        self.sent.append(data)


class _FakeMCPClient:
    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    async def call_tool(self, name, args):
        self.calls.append((name, dict(args or {})))
        return self.reply


_WEBHOOK_STUB = types.SimpleNamespace(
    emit_voice_input=lambda text: None,
    emit_voice_output=lambda text: None,
)


class VoiceHubJournalTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls._prev_enabled = bridge.HUB_VOICE_ENABLED
        cls._prev_append = bridge._hub_append_turns
        bridge.HUB_VOICE_ENABLED = True
        if bridge._hub_append_turns is None:
            bridge._hub_append_turns = lambda *a, **k: True

    @classmethod
    def tearDownClass(cls):
        bridge.HUB_VOICE_ENABLED = cls._prev_enabled
        bridge._hub_append_turns = cls._prev_append

    async def test_record_transcript_feeds_hub_buffers(self):
        b = _bare_bridge()
        with mock.patch.dict(sys.modules, {"webhook_dispatcher": _WEBHOOK_STUB}):
            b._record_transcript("input", {"text": "  ¿me escuchas?  "})
            b._record_transcript("output", {"text": "Te escucho."})
        self.assertEqual(b._hub_in_buf, ["¿me escuchas?"])
        self.assertEqual(b._hub_out_buf, ["Te escucho."])

    async def test_flush_joins_chunks_single_turn(self):
        b = _bare_bridge()
        captured = {}

        async def fake_send(inp, out):
            captured["inp"], captured["out"] = inp, out

        b._hub_send_turn = fake_send
        b._hub_note("input", "Hola,")
        b._hub_note("input", "prueba de voz.")
        b._hub_note("output", "¡Hola!")
        b._hub_note("output", "Te escucho.")
        b._hub_flush_turn()
        await asyncio.sleep(0.05)
        self.assertEqual(captured.get("inp"), ["Hola, prueba de voz."])
        self.assertEqual(captured.get("out"), ["¡Hola! Te escucho."])
        self.assertEqual(b._hub_in_buf, [])
        self.assertEqual(b._hub_out_buf, [])

    async def test_greeting_not_journaled(self):
        b = _bare_bridge()
        b._ws = _FakeWS()
        await b.send_text("I'm here.", journal=False)
        self.assertEqual(b._hub_in_buf, [])
        await b.send_text("hola de verdad")
        self.assertEqual(b._hub_in_buf, ["hola de verdad"])

    async def test_mcp_forces_conversacional_and_caps(self):
        b = _bare_bridge()
        prefix = bridge.MCP_TOOL_PREFIX or "mcp_"
        fake = _FakeMCPClient("x" * (bridge.MCP_RESULT_MAX_CHARS + 500))
        b._mcp_client = fake
        res = await b._call_mcp_tool(
            prefix + "get_session_messages",
            {"session_id": "s1", "modo": "completo"},
        )
        name, args = fake.calls[0]
        self.assertEqual(name, "get_session_messages")
        self.assertEqual(args.get("modo"), "conversacional")
        self.assertIn("truncado", res["result"])
        self.assertLess(len(res["result"]), bridge.MCP_RESULT_MAX_CHARS + 200)

    async def test_mcp_small_result_untouched(self):
        b = _bare_bridge()
        prefix = bridge.MCP_TOOL_PREFIX or "mcp_"
        fake = _FakeMCPClient('{"ok": true}')
        b._mcp_client = fake
        res = await b._call_mcp_tool(prefix + "list_sessions", {})
        self.assertEqual(res["result"], '{"ok": true}')


if __name__ == "__main__":
    unittest.main()
