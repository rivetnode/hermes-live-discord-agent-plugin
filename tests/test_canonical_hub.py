"""Tests for canonical_hub (Fase 5 parte 2): stub + integración real local."""
import os
import sqlite3
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import canonical_hub  # noqa: E402


class _StubSchema:
    """Stub de eko.hub.schema con sqlite real (tablas mínimas)."""

    def __init__(self, path):
        self._path = path
        self.calls = {"append": [], "enqueued": []}
        c = self.connect_hub()
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS canonical_conversations (
                id TEXT PRIMARY KEY, tenant_id TEXT, title TEXT, state TEXT,
                created_at REAL, updated_at REAL, archived_at REAL);
            CREATE TABLE IF NOT EXISTS conversation_endpoints (
                endpoint_id TEXT PRIMARY KEY, canonical_conversation_id TEXT,
                channel TEXT, channel_account_id TEXT, external_conversation_id TEXT,
                hermes_session_id TEXT, source_db TEXT, backfill_policy TEXT,
                metadata_json TEXT, tenant_id TEXT, created_at REAL, updated_at REAL);
            """
        )
        c.commit()
        c.close()

    def connect_hub(self, hub_db=None):
        c = sqlite3.connect(self._path)
        c.row_factory = sqlite3.Row
        return c

    def get_endpoint(self, conn, ep_id):
        r = conn.execute(
            "SELECT * FROM conversation_endpoints WHERE endpoint_id=?", (ep_id,)
        ).fetchone()
        return dict(r) if r else None

    def create_conversation(self, conn, *, tenant_id, title):
        conv = f"conv:{tenant_id}:{int(time.time() * 1000) % 100000}"
        now = time.time()
        conn.execute(
            "INSERT INTO canonical_conversations (id, tenant_id, title, state, created_at, updated_at) VALUES (?,?,?,'active',?,?)",
            (conv, tenant_id, title, now, now),
        )
        conn.commit()
        return conv

    def upsert_endpoint(self, conn, **kw):
        now = time.time()
        conn.execute(
            "INSERT OR REPLACE INTO conversation_endpoints "
            "(endpoint_id, canonical_conversation_id, channel, channel_account_id, "
            " external_conversation_id, hermes_session_id, metadata_json, tenant_id, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                kw["endpoint_id"], kw["canonical_conversation_id"], kw["channel"],
                kw.get("channel_account_id"), kw.get("external_conversation_id"),
                kw.get("hermes_session_id"), "{}", kw.get("tenant_id"), now, now,
            ),
        )
        conn.commit()

    def append_event(self, conn, **kw):
        self.calls["append"].append(kw)
        return "inserted"

    def endpoints_for_conversation(self, conn, conv):
        rows = conn.execute(
            "SELECT * FROM conversation_endpoints WHERE canonical_conversation_id=?", (conv,)
        ).fetchall()
        return [dict(r) for r in rows]

    def enqueue_deliveries(self, conn, ep_id, limit=10):
        self.calls["enqueued"].append(ep_id)
        return 1


class TestCanonicalHubStub(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="chub_stub_")
        self.stub = _StubSchema(os.path.join(self.tmp, "hub.db"))
        canonical_hub._load_hub = lambda: self.stub  # type: ignore
        canonical_hub._HUB_DB = None

    def test_ensure_creates_and_reuses(self):
        conv = canonical_hub.ensure_conversation(111, 222, "Voz Discord · general", "t1")
        self.assertTrue(conv and conv.startswith("conv:t1:"))
        conv2 = canonical_hub.ensure_conversation(111, 222, "Voz Discord · general", "t1")
        self.assertEqual(conv, conv2)
        conn = self.stub.connect_hub()
        row = conn.execute(
            "SELECT endpoint_id, channel, channel_account_id, external_conversation_id FROM conversation_endpoints"
        ).fetchall()
        conn.close()
        self.assertEqual(len(row), 1)
        self.assertEqual(row[0][0], "discord-voice:111:222")
        self.assertEqual(row[0][1], "discord")
        self.assertEqual(row[0][2], "222")
        self.assertIsNone(row[0][3])

    def test_append_turns_journal_and_enqueue(self):
        conv = canonical_hub.ensure_conversation(1, 2, "t", "t1")
        conn = self.stub.connect_hub()
        self.stub.upsert_endpoint(
            conn, endpoint_id="telegram:1:2", canonical_conversation_id=conv, channel="telegram"
        )
        conn.close()
        ok = canonical_hub.append_turns(
            "discord-voice:1:2", conv,
            [("human", "hola", 1.0), ("voice_agent", "qué tal", 2.0)],
            session_key="k", user_name="Darío",
        )
        self.assertTrue(ok)
        ap = self.stub.calls["append"]
        self.assertEqual([a["actor_type"] for a in ap], ["human", "voice_agent"])
        self.assertTrue(ap[0]["event_id"].startswith("dcv:discord-voice:1:2:human:"))
        self.assertEqual(ap[0]["metadata"]["display_name"], "Darío")
        self.assertEqual(self.stub.calls["enqueued"], ["telegram:1:2"])

    def test_empty_turns_and_fail_open(self):
        self.assertTrue(canonical_hub.append_turns("ep", "conv", []))
        canonical_hub._load_hub = lambda: None  # type: ignore
        self.assertIsNone(canonical_hub.ensure_conversation(1, 1, "t"))
        self.assertFalse(canonical_hub.append_turns("ep", "conv", [("human", "x", 1.0)]))


class TestCanonicalHubIntegration(unittest.TestCase):
    """Camino REAL contra eko.hub.schema del repo (skip si no está)."""

    @classmethod
    def setUpClass(cls):
        repo = os.getenv("EKO_REPO_PATH", "/home/admin/eko-livekit")
        if not os.path.isdir(os.path.join(repo, "eko", "hub")):
            raise unittest.SkipTest("eko-livekit no presente (CI remoto)")

    def test_real_schema_roundtrip(self):
        import importlib
        importlib.reload(canonical_hub)
        tmp = tempfile.mkdtemp(prefix="chub_real_")
        canonical_hub._HUB_DB = os.path.join(tmp, "hub.db")
        conv = canonical_hub.ensure_conversation(999, 888, "Voz Discord · test", "ztest")
        self.assertTrue(conv)
        ok = canonical_hub.append_turns(
            "discord-voice:999:888", conv,
            [("human", "prueba", time.time()), ("voice_agent", "ok", time.time())],
            session_key="t",
        )
        self.assertTrue(ok)
        c = sqlite3.connect(os.path.join(tmp, "hub.db"))
        n = c.execute(
            "SELECT COUNT(*) FROM canonical_events WHERE canonical_conversation_id=?", (conv,)
        ).fetchone()[0]
        c.close()
        self.assertEqual(n, 2)
        conv2 = canonical_hub.ensure_conversation(999, 888, "Voz Discord · test", "ztest")
        self.assertEqual(conv, conv2)


if __name__ == "__main__":
    unittest.main()
