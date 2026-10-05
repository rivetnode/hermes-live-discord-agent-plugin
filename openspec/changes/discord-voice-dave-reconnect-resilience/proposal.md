# Change: discord-voice-dave-reconnect-resilience

## Por qué
La recepción de voz por Discord (E2EE/DAVE) se rompe en silencio tras un micro-corte
de la conexión de voz: el bot reconecta bien ("voice connection restored"), pero
TODOS los paquetes entrantes del usuario fallan al descifrar con
`NoValidCryptorFound` → cada frame se convierte en silencio → el bot queda sordo
hasta reiniciar el puente.

Evidencia (2026-10-05, sesión real):
- 09:20:56 sesión fresca: `decrypt OK` y conversación fluida (3511 frames, 622 decodificados, 0 errores).
- 09:29:42 micro-corte: "Discord voice disconnected — waiting" → 09:29:45 "restored after 2.0s".
- 09:29:56+: `decrypt_exc ... NoValidCryptorFound` en TODOS los frames (~60/s), `input_transcript_events=0`.
- Sesiones frescas posteriores vuelven a descifrar bien → el problema es del RE-enganche, no del cifrado base.

## Causa raíz
Un blip de voz reconstruye el grupo MLS (DAVE). discord.py solo reinicializa la
sesión davey en conexiones NUEVAS (`reinit_dave_session`); en una sesión retomada
(resume) la sesión davey conserva llaves de una época (epoch) vieja y ningún frame
nuevo tiene criptor válido. Referencias de la comunidad confirman el patrón:
- py-cord #3139 / #3201: `NoValidCryptorFound` en ventanas de reconexión/rekey; esperan `dave.ready` antes de recibir.
- AlexFlipnote/discord.http: entrada/salida de miembros reconstruye el grupo DAVE (WS 4006).

## Cambio propuesto
1. En `_connection_watchdog` del puente, al detectar "voice connection restored":
   reinicializar la sesión DAVE (`voice_client._connection.reinit_dave_session()`):
   reenvía nuestro key package y restaura los criptores de recepción. Sin blip visible.
2. Guardia de salud DAVE: si hay fallos `NoValidCryptorFound` sostenidos (rate ≥10/s
   en ventana de 15s) aunque no se haya detectado disconnect (p.ej. rekey por
   entrada/salida de miembros), reintentar reinit (cooldown 30s, máx 3 intentos).
3. Instrumentación v2: contadores separados `nvc` (NoValidCryptorFound — señal de
   sordera) vs `passthrough_exc` (benigno, frames de silencio).

## Verificación
- [ ] Reload del plugin aplica el parche v2 (log "hardening applied (pid=…)").
- [ ] Arranque limpio: decrypt OK en los primeros frames al hablar.
- [ ] Micro-corte: tras "restored" → log "DAVE session re-initialized" → decrypt OK de nuevo.
- [ ] Sin regresión: frames de silencio (passthrough) siguen sin tumbar el router.

## No-objetivos
- Recuperar el ~5% de frames passthrough (mejora aparte).
- Auto-restart total del puente (solo si el reinit no basta; se evaluará después).
