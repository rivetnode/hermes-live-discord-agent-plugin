# Change: discord-voice-text-chat

## Por qué
Hoy el puente de voz (Gemini Live) solo escucha audio del canal de voz: si escribes
en el chat de texto del canal de voz, el bot no lo ve. Queremos que ese mismo chat
alimente la MISMA sesión conversacional — como la web app (texto y voz mezclados) —
con la respuesta saliendo por voz (y a futuro, también por texto).

## Estado actual (verificado en código)
- `adapter.py` (hermes-agent): el canal ligado a voz ya es "free-response" para el
  agente de Hermes (~líneas 5976-5984) — pero eso es del agente de TEXTO, no del
  puente Gemini. El adapter no conoce al puente.
- `bridge.py`: `GeminiLiveSession.send_text()` (~línea 4577, `realtimeInput.text`)
  ya inyecta texto a la sesión viva — se usa para recordatorios, avisos de video,
  notificaciones del watcher. El patrón está probado.
- El plugin NO necesita tocar hermes-agent: puede escuchar `on_message` con
  `client.add_listener()` (API pública de discord.py) sobre el mismo cliente del
  adapter, y reenviar al puente activo.

## Cambio propuesto
1. Listener `_on_voice_text_message` en `__init__.py`, instalado al arrancar el puente
   (`_install_voice_text_listener`): idempotente por carga de módulo; en reload quita el
   listener anterior y registra el nuevo (sin apilar callbacks muertos).
2. Filtro estricto: solo mensajes del usuario objetivo del puente, solo en el canal de
   voz exacto (chat incorporado del VC), ignora bots y mensajes vacíos.
3. Reenvío: `bridge._gemini.send_text(content)` (mismo event loop del gateway);
   log INFO con autor y longitud; errores a debug (nunca tumban el listener).
   Nota: `realtimeInput.text` es la vía documentada para texto en vivo (Google, WebSockets
   guide). Si en la práctica el modelo no respondiera por sí solo, el plan-B es
   `clientContent` con `turnComplete=true` sobre la MISMA sesión (mismo reenvío, otra
   primitiva) — se evalúa solo si la prueba en vivo lo pide.
4. Fase 2 (no en esta tanda): eco de las respuestas del bot como texto en el chat
   (agregar transcripciones de salida por turno y publicarlas al cerrar turno).

## Verificación
- [ ] `py_compile` de ambos archivos sin errores.
- [ ] Reload del plugin: log "text-chat listener installed" sin errores nuevos.
- [ ] Prueba en vivo: entrar al canal de voz → arrancar puente → escribir en el chat
      del canal → respuesta por voz + log "text-chat message forwarded to Gemini".

## No-objetivos
- Eco de texto de las respuestas (fase 2).
- Adjuntos/imágenes por chat de texto (solo texto plano).
- Multi-usuario: solo el usuario objetivo del puente.
- Cambios en hermes-agent (todo vive en el plugin).
