"""Regresión del deadlock en sfx.register_active_source (6-oct).

Bug encontrado: al reemplazar una entrada del registry cuyo source anterior
ya era basura, el dealloc del ejecutado disparaba el callback weakref DENTRO
del `with _ACTIVE_LOCK` del mismo hilo → autodeadlock con threading.Lock.
En producción podía colgar un hilo del gateway al reconectar voces.

El test corre el reemplazo en un hilo con timeout: si vuelve a deadlockear,
falla limpio (hilo daemon) en vez de colgar la suite.

Run:  python3 -m pytest tests/test_sfx_registry.py -v
"""
import threading
import unittest

import sfx
from bridge import LiveAudioSource


class TestSfxRegistry(unittest.TestCase):
    def test_reemplazo_con_source_muerto_no_cuelga(self):
        src_old = LiveAudioSource()
        sfx.register_active_source("pytest-regression-sid", src_old)
        del src_old  # solo queda la ref fuerte del registry

        done = threading.Event()

        def _worker():
            src_new = LiveAudioSource()
            sfx.register_active_source("pytest-regression-sid", src_new)
            done.set()

        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        t.join(5)
        self.assertTrue(done.is_set(), "register_active_source se colgó (deadlock)")

    def test_entrada_nueva_no_se_pierde_al_reemplazar(self):
        a = LiveAudioSource()
        sfx.register_active_source("pytest-regression-sid-2", a)
        del a
        b = LiveAudioSource()
        sfx.register_active_source("pytest-regression-sid-2", b)
        self.assertIsNotNone(sfx.pick_active_source())


if __name__ == "__main__":
    unittest.main()
