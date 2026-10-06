"""Fase 5 (parte 2): voz de Discord -> Hub de conversaciones canónicas.

La conversación de un canal de voz entra al Hub (endpoints + journal) y se
refleja a los canales enlazados (telegram/discord), mismo patrón que las
sesiones de voz de la webapp (voice-session-own-conversation):

- ensure_conversation(): claim rápido en sqlite — conv dedicada + endpoint
  ``discord-voice:{guild}:{channel}`` (idempotente; la conv nunca se pisa).
- provision_surfaces(): best-effort, en background — topic de Telegram y
  thread de Discord para el espejo (mismo camino del bootstrap de la webapp).
- append_turns(): journal (human + voice_agent) + encola deliveries para los
  demás endpoints (el service eko-hub-deliverer los manda).

Fail-open SIEMPRE: sin Hub, la voz sigue exactamente igual.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_REPO = os.getenv("EKO_REPO_PATH", "/home/admin/eko-livekit")
# Override para tests (None = default del repo: EKO_HUB_DB o ~/.eko/hub.db).
_HUB_DB: Optional[str] = os.getenv("DISCORD_VOICE_HUB_DB") or None


def _load_hub():
    """Importa eko.hub.schema (stdlib-only) del repo; None si no está."""
    try:
        import sys
        if _REPO not in sys.path:
            sys.path.insert(0, _REPO)
        from eko.hub import schema as hub_schema  # type: ignore
        return hub_schema
    except Exception as exc:
        logger.debug("canonical_hub: no pude importar eko.hub.schema: %s", exc)
        return None


def _connect(schema):
    return schema.connect_hub(_HUB_DB) if _HUB_DB else schema.connect_hub()


def endpoint_id_for(guild_id: Any, channel_id: Any) -> str:
    """Endpoint del canal de voz en el Hub (formato propio, no colisiona
    con los threads de texto ``discord:{parent}:{thread}``)."""
    return f"discord-voice:{guild_id}:{channel_id}"


def ensure_conversation(
    guild_id: Any,
    channel_id: Any,
    title: str,
    tenant_id: str = "t1",
) -> Optional[str]:
    """Conv dedicada del canal de voz (claim rápido, sin red). Idempotente.

    Devuelve el canonical_conversation_id o None (fail-open)."""
    schema = _load_hub()
    if schema is None:
        return None
    ep_id = endpoint_id_for(guild_id, channel_id)
    try:
        conn = _connect(schema)
        try:
            existing = schema.get_endpoint(conn, ep_id)
            if existing:
                return str(existing["canonical_conversation_id"])
            conv = schema.create_conversation(
                conn, tenant_id=tenant_id, title=str(title)[:100]
            )
            schema.upsert_endpoint(
                conn,
                endpoint_id=ep_id,
                canonical_conversation_id=conv,
                channel="discord",
                hermes_session_id=None,
                channel_account_id=str(channel_id),
                external_conversation_id=None,
                metadata={
                    "kind": "discord_voice",
                    "guild_id": str(guild_id),
                    "channel_id": str(channel_id),
                    "version": 1,
                },
                tenant_id=tenant_id,
            )
            logger.info("canonical_hub: conv %s creada para %s", conv, ep_id)
            return conv
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("canonical_hub.ensure_conversation failed: %s", exc)
        return None


def provision_surfaces(conv_id: str, title: str, tenant_id: str = "t1") -> None:
    """Best-effort (red): topic TG + thread Discord del espejo. Nunca lanza."""
    try:
        import sys
        if _REPO not in sys.path:
            sys.path.insert(0, _REPO)
        from eko.hub.resolve_canonical import _provision_dedicated_topic  # type: ignore
        schema = _load_hub()
        if schema is None:
            return
        conn = _connect(schema)
        try:
            _provision_dedicated_topic(conn, conv_id, str(title)[:100], tenant_id)
            logger.info("canonical_hub: surfaces provisionadas para %s", conv_id)
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("canonical_hub.provision_surfaces failed (best-effort): %s", exc)


def append_turns(
    endpoint_id: str,
    conv_id: str,
    turns: List[Tuple[str, str, float]],
    session_key: str = "",
    user_name: str = "",
) -> bool:
    """Journal + deliveries. ``turns`` = [(actor_type, text, ts), ...]. """
    if not turns:
        return True
    schema = _load_hub()
    if schema is None:
        return False
    try:
        conn = _connect(schema)
        try:
            for idx, (actor, text, ts) in enumerate(turns):
                text = str(text or "").strip()
                if not text:
                    continue
                meta: Dict[str, Any] = {
                    "bridge": "discord_voice",
                    "session_key": session_key,
                    "actor_type": actor,
                }
                if actor == "human" and user_name:
                    meta["display_name"] = user_name
                schema.append_event(
                    conn,
                    event_id=f"dcv:{endpoint_id}:{actor}:{int(ts * 1000)}:{idx}",
                    canonical_conversation_id=conv_id,
                    origin_endpoint_id=endpoint_id,
                    source_message_id=None,
                    actor_type=actor,
                    content=text,
                    created_at=ts,
                    metadata=meta,
                )
            # Short-circuit del espejo (mismo patrón de bridge v2): encolar
            # para los DEMÁS endpoints — no esperar la pasada del deliverer.
            try:
                eps = schema.endpoints_for_conversation(conn, conv_id)
                for ep in eps:
                    if ep.get("endpoint_id") == endpoint_id:
                        continue
                    schema.enqueue_deliveries(conn, ep["endpoint_id"], limit=10)
            except Exception as exc:
                logger.debug("canonical_hub: enqueue failed: %s", exc)
            return True
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("canonical_hub.append_turns failed: %s", exc)
        return False


# ── conversation-follow (5-oct-2026): catch-up de contexto + retomar ──────
# El motor de voz de Discord consume el MISMO contrato del puente webrtc v2
# (F4.16): cursor propio (last_voice_context_seq), filtro de eventos ajenos,
# ventaneo por recencia y ACK SOLO tras envío OK (at-least-once).

_CTX_FETCH_LIMIT = 200
_CTX_MAX_EVENTS = 20
_CTX_MAX_CHARS = 3600
_SEED_EVENTS = 14


def _trim_block(block: str) -> str:
    if len(block) > _CTX_MAX_CHARS:
        block = "…(contexto recortado)…\n" + block[-_CTX_MAX_CHARS:]
        cut = block.find("\n", 20)
        if cut > 0:
            block = block[cut + 1:]
    return block


def voice_context_block(endpoint_id: str):
    """(bloque de contexto AJENO, through_seq) desde el cursor de voz.

    Recencia sobre exhaustividad (misma política F4.16). ("", through)
    cuando solo hay eventos propios — el caller puede ACK. ("", 0) cuando no
    hay nada nuevo. Fail-open: cualquier error → ("", 0).
    """
    schema = _load_hub()
    if schema is None:
        return ("", 0)
    try:
        conn = _connect(schema)
        try:
            ep = schema.get_endpoint(conn, endpoint_id)
            if not ep:
                return ("", 0)
            cursor = int(ep.get("last_voice_context_seq") or 0)
            row = conn.execute(
                "SELECT COALESCE(MAX(canonical_seq), 0) FROM canonical_events "
                "WHERE canonical_conversation_id = ?",
                (ep["canonical_conversation_id"],),
            ).fetchone()
            max_seq = int(row[0]) if row and row[0] else 0
            if max_seq <= cursor:
                return ("", 0)
            # min_seq EXPLÍCITO: pending_context sin min_seq usa el cursor del
            # WORKER (last_context_seq, =0 en estos endpoints) y re-leería
            # desde el inicio del journal. Aquí manda el cursor de VOZ, con
            # ventana de recencia si el backlog excede el límite.
            min_seq = max(cursor, max_seq - _CTX_FETCH_LIMIT)
            events = schema.pending_context(
                conn, endpoint_id, limit=_CTX_FETCH_LIMIT + 1, min_seq=min_seq
            )
            if not events:
                return ("", 0)
            through = max(int(ev.get("canonical_seq") or 0) for ev in events)
            foreign = [
                ev for ev in events
                if (ev.get("origin_endpoint_id") or "") != endpoint_id
            ]
            if not foreign:
                return ("", through)
            return (_trim_block(schema.render_context_block(foreign, max_events=_CTX_MAX_EVENTS)), through)
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("canonical_hub.voice_context_block failed: %s", exc)
        return ("", 0)


def voice_seed_block(endpoint_id: str):
    """Tail reciente de la conv actual — para SEMBRAR al retomar (attach).

    Independiente del cursor: últimos _SEED_EVENTS eventos, filtrados a
    ajenos con contenido. ("", through) si no hay ajenos (el caller ACK para
    no repetir). Fail-open.
    """
    schema = _load_hub()
    if schema is None:
        return ("", 0)
    try:
        conn = _connect(schema)
        try:
            ep = schema.get_endpoint(conn, endpoint_id)
            if not ep:
                return ("", 0)
            conv = ep["canonical_conversation_id"]
            row = conn.execute(
                "SELECT COALESCE(MAX(canonical_seq), 0) FROM canonical_events "
                "WHERE canonical_conversation_id = ?",
                (conv,),
            ).fetchone()
            max_seq = int(row[0]) if row and row[0] else 0
            if max_seq <= 0:
                return ("", 0)
            after = max(0, max_seq - _SEED_EVENTS)
            events = schema.events_for_conversation(
                conn, conv, after_seq=after, limit=_SEED_EVENTS + 1
            )
            foreign = [
                ev for ev in events
                if (ev.get("origin_endpoint_id") or "") != endpoint_id
                and (ev.get("content") or "").strip()
            ]
            if not foreign:
                return ("", max_seq)
            return (_trim_block(schema.render_context_block(foreign, max_events=_SEED_EVENTS)), max_seq)
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("canonical_hub.voice_seed_block failed: %s", exc)
        return ("", 0)


def acknowledge_voice_context(endpoint_id: str, through: int) -> int:
    """ACK monotónico del cursor de voz (llamar SOLO tras envío OK)."""
    if not through:
        return 0
    schema = _load_hub()
    if schema is None:
        return 0
    try:
        conn = _connect(schema)
        try:
            return schema.advance_voice_context(conn, endpoint_id, up_to_seq=int(through))
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("canonical_hub.acknowledge_voice_context failed: %s", exc)
        return 0
