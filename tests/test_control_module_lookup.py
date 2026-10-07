"""Tests del lookup del módulo del plugin para el control API (Patch 9+).

El loader de Hermes importa los plugins de directorio como
``hermes_plugins.<slug>`` (p. ej. ``hermes_plugins.discord_voice``). El
bridge debe encontrar el módulo que expone ``CONTROL_API_SECRET`` bajo
cualquiera de los nombres (loader real, sufijo __home_<digest>, o los
specs legacy usados por los tests).
"""

import sys
import types
import unittest


class TestControlSecretModuleLookup(unittest.TestCase):
    def setUp(self):
        # Guardar/limpiar cualquier módulo "discord_voice*" cargado
        # (incluido el que test_control_secret.py deja como "discord_voice_live").
        self._saved = {}
        for key in list(sys.modules.keys()):
            if "discord_voice" in key:
                self._saved[key] = sys.modules.pop(key)

    def tearDown(self):
        for key in list(sys.modules.keys()):
            if "discord_voice" in key:
                del sys.modules[key]
        sys.modules.update(self._saved)

    def _plant(self, name, secret="s" * 40):
        mod = types.ModuleType(name)
        if secret is not None:
            mod.CONTROL_API_SECRET = secret
        sys.modules[name] = mod
        return mod

    def test_finds_loader_namespace_name(self):
        from bridge import _resolve_control_secret_mod

        planted = self._plant("hermes_plugins.discord_voice")
        self.assertIs(_resolve_control_secret_mod(), planted)

    def test_finds_home_suffixed_namespace(self):
        from bridge import _resolve_control_secret_mod

        planted = self._plant("hermes_plugins.discord_voice__home_abc123")
        self.assertIs(_resolve_control_secret_mod(), planted)

    def test_finds_legacy_spec_name(self):
        from bridge import _resolve_control_secret_mod

        planted = self._plant("discord_voice_live")
        self.assertIs(_resolve_control_secret_mod(), planted)

    def test_skips_module_without_secret(self):
        from bridge import _resolve_control_secret_mod

        self._plant("hermes_plugins.discord_voice", secret=None)
        self.assertIsNone(_resolve_control_secret_mod())

    def test_returns_none_when_no_module(self):
        from bridge import _resolve_control_secret_mod

        self.assertIsNone(_resolve_control_secret_mod())


if __name__ == "__main__":
    unittest.main()
