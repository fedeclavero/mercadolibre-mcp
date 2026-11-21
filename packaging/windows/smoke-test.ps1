param([Parameter(Mandatory)][string]$Zip)
$ErrorActionPreference = 'Stop'
$utf8 = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
$root = Join-Path $env:RUNNER_TEMP ('Meli Windows ' + [char]0x00f1 + ' ' + [guid]::NewGuid())
$env:PATH = "$env:SystemRoot\System32;$env:SystemRoot"
$env:LOCALAPPDATA = Join-Path $root 'AppData Local'
$env:CLAUDE_CONFIG_DIR = Join-Path $root 'Claude'
$env:CODEX_HOME = Join-Path $root 'Codex'
$env:ANTIGRAVITY_CONFIG_DIR = Join-Path $root 'Antigravity'
$env:AGY_CONFIG_DIR = Join-Path $root 'Agy'
$env:MELI_MCP_STATE_DIR = Join-Path $root 'Estado'
$package = Join-Path $root 'Archivo extraido'
Expand-Archive -LiteralPath $Zip -DestinationPath $package
$exe = Join-Path $package 'mercadolibre-mcp.exe'
$setup = 'fixture-secret' | & $exe setup --client-id 42 --client-secret-stdin --json
if ($LASTEXITCODE -ne 0 -or -not ($setup | Out-String | ConvertFrom-Json).ok) { throw 'Setup failed' }
# Use a synthetic prior authorization to run the installer without contacting Mercado Libre.
$token = '{"access_token":"fixture-access","refresh_token":"fixture-refresh","expires_at":4102444800,"scope":"read write offline_access"}'
[IO.File]::WriteAllText((Join-Path $env:MELI_MCP_STATE_DIR 'token.json'), $token, $utf8)
Push-Location -LiteralPath $package
try {
    & "$env:SystemRoot\System32\cmd.exe" /d /c 'echo 4 | Instalar.cmd'
    if ($LASTEXITCODE -ne 0) { throw "Guided installer exited with code $LASTEXITCODE" }
} finally {
    Pop-Location
}
$installed = Join-Path $env:LOCALAPPDATA 'Programs\mercadolibre-mcp\mercadolibre-mcp.exe'
if (-not (Test-Path -LiteralPath $installed -PathType Leaf)) { throw 'Native executable was not installed' }
$claude = Get-Content -Encoding UTF8 -Raw (Join-Path $env:CLAUDE_CONFIG_DIR '.claude.json') | ConvertFrom-Json
if ($claude.mcpServers.mercadolibre.command -ne $installed -or $claude.mcpServers.mercadolibre.args[0] -ne 'serve') { throw 'Invalid Claude Code entry' }
$codex = Get-Content -Encoding UTF8 -Raw (Join-Path $env:CODEX_HOME 'config.toml')
if (-not $codex.Contains('[mcp_servers.mercadolibre]') -or -not $codex.Contains('mercadolibre-mcp.exe')) { throw 'Invalid Codex entry' }
$antigravity = Get-Content -Encoding UTF8 -Raw (Join-Path $env:ANTIGRAVITY_CONFIG_DIR 'mcp_config.json') | ConvertFrom-Json
if ($antigravity.mcpServers.mercadolibre.command -ne $installed -or $antigravity.mcpServers.mercadolibre.args[0] -ne 'serve') { throw 'Invalid Antigravity entry' }
$report = & $installed install --client all --json | Out-String | ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or -not $report.ok -or $report.launch_check.tools -ne 48 -or $report.installed.Count -lt 3) {
    throw 'MCP startup, discovery, or client registration failed'
}
