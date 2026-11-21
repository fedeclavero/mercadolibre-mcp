# Mercado Libre MCP server for sellers

A local, open-source Model Context Protocol server written in Rust that connects **Claude Code**, **Codex (ChatGPT desktop app / CLI / IDE)**, and **Antigravity (Desktop / `agy`)** to the Mercado Libre seller API with **full read and write access from day one** (there is no read-only mode). Use it to inspect and manage listings, prices, stock, product catalog, orders, questions, promotions, and combos through an AI assistant.

The server includes OAuth with automatic token refresh, local seller policies, question context, and a local answer-attempt ledger. For Deal of the Day (DOD), it can compare a proposed public price and discount against seller-defined limits. It does not calculate net profit or guarantee that an offer is profitable.

## Setup (Zero dependencies on Windows 11, macOS, and Linux)

Download the native package from [GitHub Releases](https://github.com/fedeclavero/mercadolibre-mcp/releases/latest) — no Python, Node.js, Git, Rust, or Visual C++ Redistributable required. On Windows 11, extract the ZIP and open `Instalar.cmd`. On Linux and macOS, run `mercadolibre-mcp onboard` from a stable location. Choose **Claude Code**, **Codex**, **Antigravity**, or **All**.

Grant **all** Mercado Libre functional permissions and offline access (`offline_access`) during setup. The MCP exposes 48 tools, including an account check, a tool finder, recipes, and listing and kit operations.

## Upgrading without rotating credentials

If you already have Mercado Libre MCP installed, **do not rotate your keys, create a new app, or re-authorize**:
1. Close your AI client, replace the binary (or re-run `Instalar.cmd` on Windows), and run `mercadolibre-mcp install --client <claude-code|codex|antigravity|all>`.
2. Your existing `config.json` and `token.json` are preserved and reused automatically.

For full step-by-step instructions, see the [Spanish setup guide](INSTALACION.md) and the [agent install guide](AGENT_INSTALL.md).
