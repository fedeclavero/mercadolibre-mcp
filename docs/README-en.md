# Mercado Libre MCP server for sellers

A local, open-source Model Context Protocol server that connects Codex and other compatible MCP clients to the Mercado Libre seller API. Use it to inspect listings, product catalog, inventory, questions and promotions through an AI assistant.

The server includes OAuth with token refresh, local seller policies, question context and a local answer-attempt ledger. For Deal of the Day (DOD), it can compare a proposed public price and discount against seller-defined limits. It does not calculate net profit or guarantee that an offer is profitable.

## Setup

Install Git, Python 3.11 or later, [uv](https://docs.astral.sh/uv/getting-started/installation/) and an MCP client. Clone the repository and install dependencies:

```text
git clone https://github.com/fedeclavero/mercadolibre-mcp.git
cd mercadolibre-mcp
uv sync --frozen
uv run mercadolibre-mcp setup
uv run mercadolibre-mcp authorize
uv run mercadolibre-mcp check
```

The seller needs a Mercado Libre application, appropriate permissions and an HTTPS redirect URI they control. Never paste application secrets or the OAuth return URL into a chat. The detailed [Spanish setup guide](INSTALACION.md) covers Windows, Linux, macOS and Codex registration.

Scheduled work requires an external scheduler or agent: the MCP server does not run unattended by itself. The [automation guide](AUTOMATIZACIONES.md) provides editable Spanish examples for reviewing promotions and handling questions. Answers and commercial changes affect real users and prices when authorized.

This is an independent project, not an official Mercado Libre product. Credentials and local business data are stored unencrypted on the user's device. See the [main README](../README.md) for scope, limitations and development instructions.
