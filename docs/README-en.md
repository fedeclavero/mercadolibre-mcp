# Mercado Libre MCP server for sellers

A local, open-source Model Context Protocol server that connects Codex and other compatible MCP clients to the Mercado Libre seller API. Use it to inspect listings, product catalog, inventory, questions and promotions through an AI assistant.

The server includes OAuth with token refresh, local seller policies, question context and a local answer-attempt ledger. For Deal of the Day (DOD), it can compare a proposed public price and discount against seller-defined limits. It does not calculate net profit or guarantee that an offer is profitable.

## Setup

Download the native package from [GitHub Releases](https://github.com/fedeclavero/mercadolibre-mcp/releases/latest). On Windows 11, extract the ZIP and open `Instalar.cmd`. On Linux and macOS, run `mercadolibre-mcp onboard` from a stable location. Choose Claude Code or the local Codex agent in the ChatGPT desktop app, CLI or IDE.

Grant all Mercado Libre functional permissions and offline access during setup. The MCP exposes 48 tools, including an account check, a tool finder, recipes, and listing and kit operations. For upgrading or assisted installation, see the [Spanish setup guide](INSTALACION.md) and the [agent guide](AGENT_INSTALL.md).
