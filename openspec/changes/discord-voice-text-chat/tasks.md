# Tasks: discord-voice-text-chat

- [x] Listener `_on_voice_text_message` + instalación idempotente (`_install_voice_text_listener`)
- [x] Gancho en `voice_live()` al registrar el puente activo
- [x] Filtros: usuario objetivo, canal exacto del VC, bots/vacíos
- [x] Reload del plugin + verificación de carga (sin errores; la instalación del listener se registra al arrancar el puente)
- [ ] Prueba en vivo: escribir en el chat del canal de voz → respuesta por voz
- [ ] (Fase 2) Eco de respuestas del bot como texto en el chat
