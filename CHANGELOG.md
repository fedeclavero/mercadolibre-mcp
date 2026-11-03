# Changelog

## 0.2.0

### Instalación guiada por agentes
- `docs/AGENT_INSTALL.md`: guía única, imperativa y numerada; `AGENTS.md`, `CLAUDE.md`, `llms.txt` y el README apuntan a ella. El agente pide permiso una vez y hace todo el proceso.
- Sin clonar: `uvx --from git+https://github.com/fedeclavero/mercadolibre-mcp mercadolibre-mcp <comando>`.
- Comandos nuevos: `install` (detecta y registra en Claude Code, Claude Desktop, Codex, Gemini CLI, Cursor, Windsurf, VS Code, OpenCode, Antigravity, Cline y Zed; copia de respaldo, mezcla sin pisar, idempotente, verifica), `doctor` (diagnóstico con próximo paso), `devcenter` (valores exactos para crear la app), `mode` (lectura y escritura / solo lectura), `http-token`, `--version`.
- `setup` y `authorize` sin preguntas obligatorias: flags, variables `MELI_*` o stdin; `--json`; `authorize` captura el permiso en `https://localhost` o usa `--start`/`--finish`.
- Transporte HTTP opcional (`serve --transport http`): solo 127.0.0.1, token generado obligatorio, protección de Host/Origin. stdio sigue siendo el predeterminado.
- Modo por defecto de lectura y escritura; solo lectura opcional.

### Que las IAs usen las tools
- Instrucciones del servidor reescritas (usar las tools, no el navegador). Descripciones de todas las tools con "USÁ ESTA TOOL PARA ...".
- Tools nuevas: `meli_start_here`, `meli_find_tool`, `meli_get_recipe`, `meli_status`, `meli_my_items`, `meli_combo_plan` (lectura) y `meli_upload_picture` (sube una imagen verificada desde archivo local, base64 o URL HTTPS). Recetas también como prompts y recursos MCP.
- Los errores terminan con un "Próximo paso"; un 403 nombra el grupo de permisos del DevCenter que falta.

### Compatibilidad
- Mismos nombres y formatos de `config.json`, `token.json` y SQLite; solo se agregan campos opcionales (`mode`). Las tools existentes conservan nombre y parámetros.
