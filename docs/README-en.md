# Mercado Libre MCP server for sellers

A local, open-source Model Context Protocol server that connects Codex and other compatible MCP clients to the Mercado Libre seller API. Use it to inspect listings, product catalog, inventory, questions and promotions through an AI assistant.

The server includes OAuth with token refresh, local seller policies, question context and a local answer-attempt ledger. For Deal of the Day (DOD), it can compare a proposed public price and discount against seller-defined limits. It does not calculate net profit or guarantee that an offer is profitable.

## Setup

AI agents: follow [AGENT_INSTALL.md](AGENT_INSTALL.md) (autonomous setup: ask permission once, never hand-edit client configs). Nothing to clone; you need [uv](https://docs.astral.sh/uv/getting-started/installation/) and Git. `ML` below is `uvx --from git+https://github.com/fedeclavero/mercadolibre-mcp mercadolibre-mcp`.

```text
ML devcenter --country ar --open   # exact steps to create the DevCenter app (all permissions, read and write)
ML setup --client-id APP_ID --client-secret-stdin --redirect-uri https://localhost:8765/callback --country ar
ML authorize                       # opens the browser and captures the grant
ML install --client all            # Claude Code/Desktop, Codex, Gemini CLI, Cursor, Windsurf, VS Code, OpenCode, Antigravity, Cline, Zed
ML doctor                          # diagnosis with the next step
```

The default mode is read and write; `ML mode read-only` is optional. The server exposes 48 tools whose descriptions start with "USE THIS TOOL TO ..." (in Spanish), an entry tool `meli_start_here`, a natural-language `meli_find_tool`, recipes, and virtual kit (combo) planning with local image upload. ChatGPT web needs a remote HTTPS MCP server; see [AGENT_INSTALL.md](AGENT_INSTALL.md). The detailed [Spanish setup guide](INSTALACION.md) covers Windows, Linux and macOS.

Scheduled work requires an external scheduler or agent: the MCP server does not run unattended by itself. The [automation guide](AUTOMATIZACIONES.md) provides editable Spanish examples for reviewing promotions and handling questions. Answers and commercial changes affect real users and prices when authorized.

This is an independent project, not an official Mercado Libre product. Credentials and local business data are stored unencrypted on the user's device. See the [main README](../README.md) for scope, limitations and development instructions.
