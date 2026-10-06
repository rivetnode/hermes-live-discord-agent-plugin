"""Anti-truncación de transcripción (6-oct): el fin de turno por inactividad
NO debe dispararse en pausas naturales (~0.3s). Docs Live API: umbrales
cliente de fin-de-voz deben ser >=500ms; default local = 1.0s.

Caso real: "Bueno, tío, que" — la frase se cortó porque el audioStreamEnd
salió 0.25s después de la última sílaba (pausa de pensar), el turno cerró y
el resto de la frase se perdió.

Run:  ~/.hermes/hermes-agent/venv/bin/python -m pytest tests/test_audio_idle_end.py -q
"""
import asyncio
import time
import unittest

from bridge import GeminiLiveBridge, LiveAudioSource


class _FakeWS:
    def __init__(self):
        self.sent = []

    async def send(self, payload):
        self.sent.append(payload)


class TestAudioIdleEnd(unittest.TestCase):
    def _bridge(self):
        b = GeminiLiveBridge(output_source=LiveAudioSource())
        b._ws = _FakeWS()
        b.metrics.setdefault("audio_stream_end_events", 0)
        return b

    def test_pausa_natural_no_cierra_turno(self):
        b = self._bridge()
        b._audio_stream_open = True
        b._last_audio_sent_at = time.monotonic() - 0.3  # pausa de pensar
        asyncio.run(b._maybe_end_idle_audio_stream())
        self.assertEqual(b._ws.sent, [], "no debe mandar audioStreamEnd en pausa de 0.3s")
        self.assertTrue(b._audio_stream_open)

    def test_idle_largo_cierra_turno(self):
        b = self._bridge()
        b._audio_stream_open = True
        b._last_audio_sent_at = time.monotonic() - (GeminiLiveBridge.AUDIO_STREAM_IDLE_END_SECONDS + 0.2)
        asyncio.run(b._maybe_end_idle_audio_stream())
        self.assertEqual(len(b._ws.sent), 1, "debe mandar audioStreamEnd tras inactividad")
        self.assertIn("audioStreamEnd", b._ws.sent[0])
        self.assertFalse(b._audio_stream_open)

    def test_umbral_minimo_documentado(self):
        # docs: umbral cliente >= 500ms para no fragmentar la transcripción
        self.assertGreaterEqual(GeminiLiveBridge.AUDIO_STREAM_IDLE_END_SECONDS, 0.5)


if __name__ == "__main__":
    unittest.main()
