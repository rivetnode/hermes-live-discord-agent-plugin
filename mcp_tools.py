"""MCP de Hermes para la voz de Discord (Fase 5, cross-channel-voice-settings).

Paridad con la webapp: la voz de Discord descubre las tools del MCP local de
Hermes (lecturas: memoria, skills, busqueda, sesiones; + chat) y las expone a
Gemini Live como functionDeclarations — "sin ser algo aparte".

- HermesMCPClient: cliente minimo streamable-HTTP (JSON-RPC sobre POST, parseo
  SSE). Vendorizado de eko-livekit bridge/adapters/hermes.py — mantener en
  sync si el protocolo cambia.
- mcp_tools_to_gemini_declarations: convierte descriptores de tools/list al
  shape de Gemini Live: inputSchema -> parameters + saneo de llaves que
  Gemini rechaza (additionalProperties, $schema, ...).
- Prefijo de superficie "mcp_": las tools viajan como mcp_<name> para que el
  allowlist por perfil (user_profiles) las gobierne con UNA regla (agregar o
  negar el prefijo "mcp_"); al invocar se quita el prefijo y se llama el
  nombre real contra el server.

Fail-open SIEMPRE: sin MCP (server caido), la voz sigue con sus tools de
siempre y el LLM simplemente no ve las nuevas.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_MCP_URL = "http://127.0.0.1:9999/mcp"
MCP_TOOL_PREFIX = "mcp_"
# Nunca exponer a la voz (admin): se filtra aunque el server lo anuncie.
MCP_DENY = frozenset({"restart_worker"})


class HermesMCPClient:
    """Cliente MCP over Streamable HTTP (vendor: eko-livekit bridge/adapters/hermes.py)."""

    def __init__(self, server_url: str, token: Optional[str] = None):
        self._url = (server_url or DEFAULT_MCP_URL).rstrip("/")
        if not self._url.endswith("/mcp"):
            self._url += "/mcp"
        self._token = token or ""
        self._session_id: Optional[str] = None
        self._connected = False
        self._client: Optional[Any] = None
        self._request_id = 0

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    def _headers(self) -> Dict[str, str]:
        h = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self._session_id:
            h["Mcp-Session-Id"] = self._session_id
        if self._token:
            h["Authorization"] = f"Bearer {self._token}"
        return h

    def _parse_sse(self, text: str) -> Optional[Dict]:
        for line in text.split("\n"):
            if line.startswith("data: "):
                try:
                    return json.loads(line[6:])
                except json.JSONDecodeError:
                    continue
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None

    async def connect(self) -> bool:
        if self._connected:
            return True
        try:
            import httpx

            self._client = httpx.AsyncClient(timeout=60.0)
            resp = await self._client.post(
                self._url,
                headers=self._headers(),
                json={
                    "jsonrpc": "2.0",
                    "id": self._next_id(),
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-03-26",
                        "capabilities": {},
                        "clientInfo": {"name": "discord-voice", "version": "1.0"},
                    },
                },
            )
            sid = resp.headers.get("mcp-session-id")
            if sid:
                self._session_id = sid
            data = self._parse_sse(resp.text)
            if data and "result" in data:
                self._connected = True
                await self._notify("notifications/initialized", {})
                logger.info(
                    "Hermes MCP conectado (discord-voice): session=%s",
                    (self._session_id or "none")[:8],
                )
                return True
            logger.warning("Hermes MCP initialize failed: %s", resp.text[:200])
        except Exception as exc:
            logger.warning("Hermes MCP connection failed: %s", exc)
        return False

    async def _notify(self, method: str, params: Dict) -> None:
        if not self._client:
            return
        try:
            await self._client.post(
                self._url,
                headers=self._headers(),
                json={"jsonrpc": "2.0", "method": method, "params": params},
            )
        except Exception:
            pass

    async def _call(self, method: str, params: Dict) -> Optional[Dict]:
        if not self._client:
            return None
        try:
            resp = await self._client.post(
                self._url,
                headers=self._headers(),
                json={
                    "jsonrpc": "2.0",
                    "id": self._next_id(),
                    "method": method,
                    "params": params,
                },
            )
            sid = resp.headers.get("mcp-session-id")
            if sid:
                self._session_id = sid
            data = self._parse_sse(resp.text)
            if data and "result" in data:
                return data["result"]
            if data and "error" in data:
                logger.warning("Hermes MCP error: %s", str(data["error"])[:200])
            return None
        except Exception as exc:
            logger.warning("Hermes MCP call failed (%s): %s", method, exc)
            return None

    async def call_tool(self, name: str, arguments: Optional[Dict] = None) -> Optional[str]:
        if not self._connected and not await self.connect():
            return None
        result = await self._call(
            "tools/call", {"name": name, "arguments": arguments or {}}
        )
        if result and "content" in result:
            texts = [
                item.get("text", "")
                for item in result["content"]
                if item.get("type") == "text"
            ]
            return "\n".join(t for t in texts if t) or None
        return None

    async def list_tools(self) -> List[Dict[str, Any]]:
        if not self._connected and not await self.connect():
            return []
        result = await self._call("tools/list", {})
        if not result or "tools" not in result:
            return []
        return list(result["tools"])

    async def close(self) -> None:
        if self._client:
            try:
                await self._client.aclose()
            except Exception:
                pass
        self._client = None
        self._connected = False


_SCHEMA_DROP_KEYS = frozenset(
    {"additionalProperties", "$schema", "$id", "examples", "$defs", "definitions"}
)


def _sanitize_schema(node: Any) -> Any:
    """Quita llaves que Gemini rechaza; recursivo (dicts/listas)."""
    if isinstance(node, dict):
        return {
            k: _sanitize_schema(v)
            for k, v in node.items()
            if k not in _SCHEMA_DROP_KEYS
        }
    if isinstance(node, list):
        return [_sanitize_schema(x) for x in node]
    return node


def mcp_tools_to_gemini_declarations(
    tools: List[Dict[str, Any]],
    *,
    prefix: str = MCP_TOOL_PREFIX,
    deny: Optional[frozenset] = None,
) -> List[Dict[str, Any]]:
    """tools/list de MCP -> functionDeclarations de Gemini Live (con prefijo)."""
    deny = deny if deny is not None else MCP_DENY
    out: List[Dict[str, Any]] = []
    for t in tools or []:
        if not isinstance(t, dict):
            continue
        name = str(t.get("name") or "").strip()
        if not name or name in deny:
            continue
        schema = t.get("inputSchema")
        if not isinstance(schema, dict):
            schema = {}
        params = _sanitize_schema(schema)
        if not isinstance(params, dict) or "type" not in params:
            params = {
                "type": "object",
                "properties": (params.get("properties") if isinstance(params, dict) else None) or {},
            }
        out.append(
            {
                "name": f"{prefix}{name}",
                "description": (str(t.get("description") or "")[:1024])
                or f"Herramienta {name} de Hermes",
                "parameters": params,
            }
        )
    return out
