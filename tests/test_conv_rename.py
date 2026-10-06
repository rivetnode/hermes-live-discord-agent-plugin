"""Test del tool conv_rename (6-oct-2026): renombrar la conversación por voz.

Caso real que lo motivó: Darío pidió por voz renombrar la conversación
("que se llame C Eco 17") y no existía herramienta; el modelo intentó Spotify
y luego delegó a Hermes (lento) y el pedido quedó sin hacer.
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bridge  # noqa: E402


def _bare_bridge():
    """GeminiLiveBridge sin __init__, con hub_ensure stub."""
    b = bridge.GeminiLiveBridge.__new__(bridge.GeminiLiveBridge)
    b._hub_ensure = lambda: ("conv:TEST", "telegram:-100:55")
    return b


class ConvRenameToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_conv_rename_calls_control_plane_with_name(self):
        b = _bare_bridge()
        calls = []

        def fake_cp(text, endpoint_id, timeout=15.0):
            calls.append((text, endpoint_id))
            return {"ok": True, "text": "Renombrada a 'C Eco 17'."}

        with mock.patch.object(bridge, "conv_control_plane", side_effect=fake_cp):
            res = await b._run_conv_tool("conv_rename", {"nombre": "C Eco 17"})
        self.assertEqual(calls, [("/conv rename C Eco 17", "telegram:-100:55")])
        self.assertEqual(res, {"result": "Renombrada a 'C Eco 17'."})

    async def test_conv_rename_empty_name_errors(self):
        b = _bare_bridge()
        res = await b._run_conv_tool("conv_rename", {"nombre": "   "})
        self.assertIn("error", res)

    async def test_conv_rename_control_plane_failure_maps_to_error(self):
        b = _bare_bridge()

        def fake_cp(text, endpoint_id, timeout=15.0):
            return {"ok": False, "text": "Uso: /conv rename <nombre>"}

        with mock.patch.object(bridge, "conv_control_plane", side_effect=fake_cp):
            res = await b._run_conv_tool("conv_rename", {"nombre": "X"})
        self.assertIn("error", res)
        self.assertIn("rename", res["error"])


if __name__ == "__main__":
    unittest.main()
