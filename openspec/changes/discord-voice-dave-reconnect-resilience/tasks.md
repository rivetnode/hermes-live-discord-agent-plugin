# Tasks: discord-voice-dave-reconnect-resilience

- [ ] Instrumentación v2 (contadores `nvc`/`passthrough_exc`; flag `_hermes_dave_hardened_v2`)
- [ ] Reinit DAVE al detectar "voice connection restored"
- [ ] Guardia de salud DAVE en `_connection_watchdog` (rate + cooldown + reintentos)
- [ ] Reload del plugin + verificación de arranque limpio
- [ ] Prueba en vivo: conversación normal + (si ocurre) micro-corte auto-recuperado
