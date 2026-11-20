use std::env;
use std::fs;
use std::io::{ErrorKind, Write};
use std::path::{Path, PathBuf};
use std::process::{ExitCode, Stdio};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use anyhow::{Context, Result, anyhow, bail, ensure};
use clap::{Args, ValueEnum};
use jsonc_parser::cst::{CstInputValue, CstRootNode};
use jsonc_parser::{ParseOptions, parse_to_serde_value};
use rmcp::{ServiceExt, transport::TokioChildProcess};
use serde::Serialize;
use serde_json::{Value, json};
use tempfile::NamedTempFile;
use toml_edit::{Array, DocumentMut, Item, Table, TableLike, value};

pub(crate) const DEFAULT_NAME: &str = "mercadolibre";
const LEGACY_SOURCE: &str = "git+https://github.com/fedeclavero/mercadolibre-mcp";

#[derive(Args)]
pub(crate) struct InstallArgs {
    /// Claude Code (claude-code), Codex (codex) o all para detectar ambos.
    #[arg(long)]
    client: Vec<String>,
    /// Guardar el servidor en la configuración del usuario o del proyecto.
    #[arg(long, value_enum, default_value_t = Scope::User)]
    scope: Scope,
    #[arg(long)]
    project_dir: Option<PathBuf>,
    #[arg(long, default_value = DEFAULT_NAME)]
    name: String,
    /// Ejecutable alternativo del servidor.
    #[arg(long)]
    command: Option<String>,
    #[arg(long, hide = true)]
    source: Option<String>,
    #[arg(long = "local", hide = true)]
    _local: bool,
    /// Mostrar el resultado sin editar archivos ni arrancar el servidor.
    #[arg(long)]
    dry_run: bool,
    #[arg(long)]
    force: bool,
    #[arg(long)]
    no_verify_launch: bool,
    #[arg(long)]
    pub(crate) json: bool,
}

#[derive(Clone, Copy, PartialEq, Eq, ValueEnum)]
enum Scope {
    User,
    Project,
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum Client {
    ClaudeCode,
    Codex,
}

const CLIENTS: [Client; 2] = [Client::ClaudeCode, Client::Codex];

struct Environment {
    claude_override: Option<PathBuf>,
    codex: PathBuf,
    home: PathBuf,
}

impl Environment {
    fn current() -> Result<Self> {
        let home = dirs::home_dir().context(
            "No se encontró tu carpeta personal. Próximo paso: revisá la configuración de tu usuario.",
        )?;
        Ok(Self {
            claude_override: env_path("CLAUDE_CONFIG_DIR"),
            codex: env_path("CODEX_HOME").unwrap_or_else(|| home.join(".codex")),
            home,
        })
    }
}

fn env_path(name: &str) -> Option<PathBuf> {
    env::var_os(name)
        .filter(|p| !p.is_empty())
        .map(PathBuf::from)
}

impl Client {
    fn id(self) -> &'static str {
        match self {
            Self::ClaudeCode => "claude-code",
            Self::Codex => "codex",
        }
    }

    fn label(self) -> &'static str {
        match self {
            Self::ClaudeCode => "Claude Code",
            Self::Codex => "Codex",
        }
    }

    fn user_path(self, env: &Environment) -> PathBuf {
        match self {
            Self::ClaudeCode => env
                .claude_override
                .as_ref()
                .unwrap_or(&env.home)
                .join(".claude.json"),
            Self::Codex => env.codex.join("config.toml"),
        }
    }

    fn detected(self, env: &Environment) -> bool {
        if self.user_path(env).is_file() {
            return true;
        }
        match self {
            Self::ClaudeCode => {
                which::which("claude").is_ok()
                    || env
                        .claude_override
                        .as_ref()
                        .map_or_else(|| env.home.join(".claude").is_dir(), |path| path.is_dir())
            }
            Self::Codex => which::which("codex").is_ok() || env.codex.is_dir(),
        }
    }

    fn target_path(self, env: &Environment, scope: Scope, project: &Path) -> PathBuf {
        match scope {
            Scope::User => self.user_path(env),
            Scope::Project => project.join(match self {
                Self::ClaudeCode => ".mcp.json",
                Self::Codex => ".codex/config.toml",
            }),
        }
    }
}

struct ServerSpec {
    name: String,
    command: String,
    state_dir: Option<String>,
}

impl ServerSpec {
    fn argv(&self) -> [&str; 2] {
        [&self.command, "serve"]
    }
}

fn known_command(command: &str) -> bool {
    let executable = command.rsplit(['/', '\\']).next().unwrap_or(command);
    matches!(
        executable,
        "mercadolibre-mcp" | "mercadolibre-mcp.exe" | "mercadolibre_mcp" | "mercadolibre_mcp.exe"
    )
}

fn our_invocation(command: &str, args: impl Iterator<Item = impl AsRef<str>>) -> bool {
    let args: Vec<_> = args.collect();
    let args: Vec<_> = args.iter().map(AsRef::as_ref).collect();
    let executable = command.rsplit(['/', '\\']).next().unwrap_or(command);
    known_command(command)
        || (matches!(executable, "uvx" | "uvx.exe" | "uv" | "uv.exe")
            && args.contains(&"mercadolibre-mcp")
            && args.last() == Some(&"serve"))
        || (executable.starts_with("python")
            && args.windows(2).any(|pair| pair == ["-m", "meli_mcp"]))
}

fn json_invocation(entry: &Value) -> (&str, Vec<&str>) {
    if let Some(command) = entry["command"].as_array() {
        let mut args = command.iter().filter_map(Value::as_str);
        return (args.next().unwrap_or(""), args.collect());
    }
    (
        entry["command"].as_str().unwrap_or(""),
        entry["args"]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(Value::as_str)
            .collect(),
    )
}

fn toml_invocation(entry: &Item) -> (&str, Vec<&str>) {
    let table = entry.as_table_like();
    (
        table
            .and_then(|t| t.get("command"))
            .and_then(Item::as_str)
            .unwrap_or(""),
        table
            .and_then(|t| t.get("args"))
            .and_then(Item::as_array)
            .into_iter()
            .flat_map(Array::iter)
            .filter_map(toml_edit::Value::as_str)
            .collect(),
    )
}

#[derive(Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
enum Status {
    Created,
    Added,
    Updated,
    Unchanged,
    Conflict,
    Error,
}

impl Status {
    fn label(self) -> &'static str {
        match self {
            Self::Created => "created",
            Self::Added => "added",
            Self::Updated => "updated",
            Self::Unchanged => "unchanged",
            Self::Conflict => "conflict",
            Self::Error => "error",
        }
    }

    fn success(self) -> bool {
        !matches!(self, Self::Conflict | Self::Error)
    }
}

#[derive(Serialize)]
struct Outcome {
    #[serde(skip)]
    client_kind: Client,
    client: &'static str,
    file: PathBuf,
    status: Status,
    #[serde(skip_serializing_if = "Option::is_none")]
    backup: Option<PathBuf>,
    verified: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    detail: Option<String>,
}

impl Outcome {
    fn new(client: Client, file: PathBuf, status: Status) -> Self {
        Self {
            client_kind: client,
            client: client.id(),
            file,
            status,
            backup: None,
            verified: status == Status::Unchanged,
            detail: None,
        }
    }

    fn with_detail(mut self, detail: impl Into<String>) -> Self {
        self.detail = Some(detail.into());
        self
    }
}

enum Edit {
    Conflict,
    Unchanged,
    Write { text: String, exists: bool },
}

fn jsonc_options() -> ParseOptions {
    ParseOptions {
        allow_comments: true,
        allow_trailing_commas: true,
        allow_loose_object_property_names: false,
        allow_missing_commas: false,
        allow_single_quoted_strings: false,
        allow_hexadecimal_numbers: false,
        allow_unary_plus_numbers: false,
        allow_bare_decimal_point_numbers: false,
        allow_non_finite_numbers: false,
        allow_extended_string_escapes: false,
    }
}

fn cst_value(value: &Value) -> CstInputValue {
    match value {
        Value::Null => CstInputValue::Null,
        Value::Bool(b) => CstInputValue::Bool(*b),
        Value::Number(n) => CstInputValue::Number(n.to_string()),
        Value::String(s) => CstInputValue::String(s.clone()),
        Value::Array(values) => CstInputValue::Array(values.iter().map(cst_value).collect()),
        Value::Object(values) => CstInputValue::Object(
            values
                .iter()
                .map(|(k, v)| (k.clone(), cst_value(v)))
                .collect(),
        ),
    }
}

fn patch_json(filename: &Path, raw: &str, spec: &ServerSpec, force: bool) -> Result<Edit> {
    let options = jsonc_options();
    let invalid = || {
        anyhow!(
            "{} no es un JSON válido, así que no se tocó. Próximo paso: corregí o renombrá el archivo y reintentá.",
            filename.display()
        )
    };
    let blank = "{}";
    let raw = if raw.trim().is_empty() { blank } else { raw };
    let current: Value = parse_to_serde_value(raw, &options).map_err(|_| invalid())?;
    ensure!(
        current.is_object(),
        "{} no tiene un objeto JSON en la raíz. Próximo paso: corregí el archivo y reintentá.",
        filename.display()
    );
    let key = "mcpServers";
    ensure!(
        current[key].is_null() || current[key].is_object(),
        "En {} la clave \"{key}\" no es un objeto. Próximo paso: corregí el archivo y reintentá.",
        filename.display()
    );

    let entry = current[key].get(&spec.name);
    let exists = entry.is_some_and(|entry| !entry.is_null());
    let mut desired = json!({"type": "stdio", "command": spec.command, "args": ["serve"]});
    if let Some(dir) = &spec.state_dir {
        desired["env"] = json!({"MELI_MCP_STATE_DIR": dir});
    }
    let foreign = entry.filter(|other| !other.is_null()).is_some_and(|other| {
        let (command, args) = json_invocation(other);
        command != spec.command && !our_invocation(command, args.into_iter())
    });
    if foreign && !force {
        return Ok(Edit::Conflict);
    }

    let root = CstRootNode::parse(raw, &options).map_err(|_| invalid())?;
    let servers = root.object_value_or_set().object_value_or_set(key);
    if foreign && let Some(prop) = servers.get(&spec.name) {
        prop.set_value(CstInputValue::Object(Vec::new()));
    }
    let server = servers.object_value_or_set(&spec.name);
    let mut expected = entry
        .filter(|value| !foreign && value.is_object())
        .cloned()
        .unwrap_or_else(|| json!({}));
    let fields = desired
        .as_object()
        .context("La entrada de configuración debe ser un objeto.")?;
    for (name, value) in fields {
        if expected[name] == *value {
            continue;
        }
        if name == "env"
            && let (Some(old), Some(values)) = (expected[name].as_object_mut(), value.as_object())
        {
            let node = server.object_value_or_set(name);
            for (key, value) in values {
                if old.get(key) != Some(value) {
                    if let Some(current) = node.get(key) {
                        current.set_value(cst_value(value));
                    } else {
                        node.append(key, cst_value(value));
                    }
                    old.insert(key.clone(), value.clone());
                }
            }
        } else {
            if let Some(current) = server.get(name) {
                current.set_value(cst_value(value));
            } else {
                server.append(name, cst_value(value));
            }
            expected[name] = value.clone();
        }
    }
    if entry == Some(&expected) {
        return Ok(Edit::Unchanged);
    }

    let mut text = root.to_string();
    if !text.ends_with('\n') {
        text.push('\n');
    }
    let parsed: Value = parse_to_serde_value(&text, &options).map_err(|_| invalid())?;
    let mut want = current;
    if want[key].is_null() {
        want[key] = json!({});
    }
    want[key][&spec.name] = expected;
    ensure!(
        parsed == want,
        "No se pudo editar {} sin alterar otros ajustes. Próximo paso: revisá si hay claves duplicadas.",
        filename.display()
    );
    Ok(Edit::Write { text, exists })
}

fn ensure_toml_table<'a>(
    parent: &'a mut dyn TableLike,
    key: &str,
    implicit: bool,
) -> Result<&'a mut dyn TableLike> {
    if parent.get(key).is_none() {
        let mut table = Table::new();
        table.set_implicit(implicit);
        parent.insert(key, Item::Table(table));
    }
    parent.get_mut(key).and_then(Item::as_table_like_mut).with_context(|| {
        format!("La clave \"{key}\" no es una tabla TOML. Próximo paso: corregí el archivo y reintentá.")
    })
}

fn replace_toml(table: &mut dyn TableLike, key: &str, mut next: Item) {
    if let Some(previous) = table.get(key).and_then(Item::as_value)
        && let Some(new) = next.as_value_mut()
    {
        *new.decor_mut() = previous.decor().clone();
    }
    table.insert(key, next);
}

fn patch_toml(filename: &Path, raw: &str, spec: &ServerSpec, force: bool) -> Result<Edit> {
    let mut document: DocumentMut = raw.parse().map_err(|_| anyhow!(
        "{} no es un TOML válido, así que no se tocó. Próximo paso: corregí o renombrá el archivo y reintentá.",
        filename.display()
    ))?;
    let servers = ensure_toml_table(document.as_table_mut(), "mcp_servers", true)?;
    let exists = servers.get(&spec.name).is_some();
    if let Some(other) = servers.get(&spec.name) {
        let (command, args) = toml_invocation(other);
        if command != spec.command && !our_invocation(command, args.into_iter()) {
            if !force {
                return Ok(Edit::Conflict);
            }
            servers.remove(&spec.name);
        }
    }
    let entry = ensure_toml_table(servers, &spec.name, false)?;
    // Work directly with the parsed table so comments and extra settings survive.
    let command_matches = entry.get("command").and_then(Item::as_str) == Some(&spec.command);
    let old_args: Vec<&str> = entry
        .get("args")
        .and_then(Item::as_array)
        .into_iter()
        .flat_map(Array::iter)
        .filter_map(toml_edit::Value::as_str)
        .collect();
    let mut changed = !exists;
    if old_args.as_slice() != ["serve"]
        || entry
            .get("args")
            .and_then(Item::as_array)
            .is_some_and(|a| a.len() != 1)
    {
        let new_args: Array = ["serve"].into_iter().collect();
        replace_toml(entry, "args", value(new_args));
        changed = true;
    }
    if !command_matches {
        replace_toml(entry, "command", value(&spec.command));
        changed = true;
    }
    if entry.get("startup_timeout_sec").is_none() {
        entry.insert("startup_timeout_sec", value(120));
        changed = true;
    }
    if let Some(dir) = &spec.state_dir {
        let server_env = ensure_toml_table(entry, "env", false)?;
        if server_env.get("MELI_MCP_STATE_DIR").and_then(Item::as_str) != Some(dir) {
            replace_toml(server_env, "MELI_MCP_STATE_DIR", value(dir));
            changed = true;
        }
    }
    if !changed {
        return Ok(Edit::Unchanged);
    }
    let text = document.to_string();
    let _: DocumentMut = text
        .parse()
        .context("La configuración TOML generada no es válida.")?;
    Ok(Edit::Write { text, exists })
}

fn read_file(path: &Path) -> Result<Option<String>> {
    match fs::read_to_string(path) {
        Ok(text) => Ok(Some(text)),
        Err(e) if e.kind() == ErrorKind::NotFound => Ok(None),
        Err(e) => Err(e.into()),
    }
}

struct ConfigUpdate {
    before: Option<String>,
    after: String,
}

impl ConfigUpdate {
    fn apply(self, outcome: &mut Outcome) -> Result<()> {
        let path = &outcome.file;
        let parent = path
            .parent()
            .context("No se encontró la carpeta del archivo.")?;
        fs::create_dir_all(parent)?;

        let destination = if self.before.is_some() {
            path.canonicalize()?
        } else {
            path.clone()
        };
        let target_parent = destination
            .parent()
            .context("No se encontró la carpeta de destino.")?;
        // Re-read before persisting so a configuration modified while verification was
        // running is never silently overwritten.
        ensure!(
            read_file(path)? == self.before,
            "El archivo cambió durante la instalación. Próximo paso: volvé a ejecutar el comando."
        );
        if let Some(old) = &self.before {
            outcome.backup = Some(backup(path, old.as_bytes())?);
        }
        let mut temp = NamedTempFile::with_prefix_in(".meli-tmp-", target_parent)?;
        if let Ok(metadata) = fs::metadata(&destination) {
            temp.as_file().set_permissions(metadata.permissions())?;
        }
        temp.write_all(self.after.as_bytes())?;
        temp.as_file().sync_all()?;
        if self.before.is_some() {
            ensure!(
                read_file(path)? == self.before && path.canonicalize()? == destination,
                "El archivo cambió durante la instalación. Próximo paso: volvé a ejecutar el comando."
            );
            temp.persist(&destination)?;
        } else {
            temp.persist_noclobber(&destination)?;
        }
        outcome.verified = read_file(path)?.as_deref() == Some(&self.after);
        ensure!(
            outcome.verified,
            "La configuración cambió después de guardarla. Próximo paso: revisá el archivo y reintentá."
        );
        Ok(())
    }
}

fn backup(path: &Path, snapshot: &[u8]) -> Result<PathBuf> {
    let filename = path
        .file_name()
        .context("El archivo de configuración no tiene nombre.")?;
    let stamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis();
    let parent = path
        .parent()
        .context("No se encontró la carpeta para la copia de respaldo.")?;
    let mut temp = NamedTempFile::with_prefix_in(".meli-tmp-", parent)?;
    temp.write_all(snapshot)?;
    temp.as_file().sync_all()?;
    for n in 0..256 {
        let mut name = filename.to_os_string();
        name.push(format!(".bak-meli-{stamp}-{n}"));
        let out = path.with_file_name(name);
        match temp.persist_noclobber(&out) {
            Ok(_) => return Ok(out),
            Err(error) if error.error.kind() == ErrorKind::AlreadyExists => temp = error.file,
            Err(error) => return Err(error.into()),
        }
    }
    bail!("No se pudo reservar un nombre para la copia de respaldo. Próximo paso: reintentá.")
}

fn plan_file(
    client: Client,
    path: &Path,
    spec: &ServerSpec,
    force: bool,
) -> Result<(Outcome, Option<ConfigUpdate>)> {
    let previous = read_file(path)?;
    let raw = previous.as_deref().unwrap_or_default();
    let text = raw.strip_prefix('\u{feff}').unwrap_or(raw);
    let edit = if client == Client::Codex {
        patch_toml(path, text, spec, force)?
    } else {
        patch_json(path, text, spec, force)?
    };
    let report = |status| Outcome::new(client, path.to_path_buf(), status);
    Ok(match edit {
        Edit::Conflict => (
            report(Status::Conflict).with_detail(format!(
                "Ya hay un servidor distinto llamado \"{}\". Próximo paso: usá --name otro-nombre, o --force si querés reemplazarlo (se guarda copia de respaldo).",
                spec.name
            )),
            None,
        ),
        Edit::Unchanged => (report(Status::Unchanged), None),
        Edit::Write { mut text, exists } => {
            let status = if previous.is_none() {
                Status::Created
            } else if exists {
                Status::Updated
            } else {
                Status::Added
            };
            if raw.starts_with('\u{feff}') {
                text.insert(0, '\u{feff}');
            }
            (report(status), Some(ConfigUpdate { before: previous, after: text }))
        }
    })
}

fn selected_clients(raw: &[String], env: &Environment) -> Result<(Vec<Client>, Vec<&'static str>)> {
    let selection: Vec<String> = raw
        .iter()
        .flat_map(|s| s.split(','))
        .map(|part| part.trim().to_lowercase())
        .filter(|s| !s.is_empty())
        .collect();
    if selection.is_empty() || selection.iter().any(|id| id == "all") {
        let (found, missing): (Vec<_>, Vec<_>) =
            CLIENTS.into_iter().partition(|client| client.detected(env));
        return Ok((found, missing.into_iter().map(Client::id).collect()));
    }
    let mut chosen = Vec::new();
    for id in &selection {
        let client = CLIENTS
            .iter()
            .find(|entry| entry.id() == id)
            .with_context(|| {
                format!(
                    "Cliente desconocido: {id}. Próximo paso: usá uno de {} o `all`.",
                    client_choices()
                )
            })?;
        if !chosen.contains(client) {
            chosen.push(*client);
        }
    }
    Ok((chosen, Vec::new()))
}

fn client_choices() -> String {
    CLIENTS
        .iter()
        .map(|client| client.id())
        .collect::<Vec<_>>()
        .join(", ")
}

async fn handshake(spec: &ServerSpec) -> Result<Value> {
    let mut command = tokio::process::Command::new(&spec.command);
    command.arg("serve");
    if let Some(dir) = &spec.state_dir {
        command.env("MELI_MCP_STATE_DIR", dir);
    }
    let (transport, _) = TokioChildProcess::builder(command)
        .stderr(Stdio::null())
        .spawn()?;
    let running = ().serve(transport).await?;
    let result = async {
        let info = running
            .peer_info()
            .context("El servidor no devolvió información MCP.")?;
        let server = info
            .server_info
            .as_ref()
            .context("El servidor no se identificó durante el inicio MCP.")?;
        let tools = running.list_all_tools().await?;
        ensure!(
            tools.iter().any(|tool| tool.name == "meli_start_here"),
            "El servidor no anuncia la herramienta meli_start_here."
        );
        Ok(json!({
            "ok": true,
            "server": server.name,
            "version": server.version,
            "tools": tools.len(),
            "has_start_here": true,
        }))
    }
    .await;
    let _ = running.cancel().await;
    result
}

impl InstallArgs {
    pub(crate) fn for_clients(client: Vec<String>) -> Self {
        Self {
            client,
            scope: Scope::User,
            project_dir: None,
            name: DEFAULT_NAME.to_owned(),
            command: None,
            source: None,
            _local: false,
            dry_run: false,
            force: false,
            no_verify_launch: false,
            json: false,
        }
    }

    pub(crate) async fn run(self) -> Result<ExitCode> {
        let env = Environment::current()?;
        ensure!(
            !self.name.trim().is_empty() && !self.name.chars().any(char::is_control),
            "Nombre de servidor inválido. Próximo paso: elegí otro valor para --name."
        );
        if let Some(source) = &self.source
            && source != LEGACY_SOURCE
            && self.command.is_none()
        {
            bail!(
                "--source identificaba un paquete de Python. Próximo paso: instalá el binario del repositorio que elegiste y pasá su ruta con --command."
            );
        }
        let command = match self.command {
            Some(command) => which::which(&command).unwrap_or_else(|_| PathBuf::from(command)),
            None => env::current_exe()
                .context("No se pudo localizar el binario de Mercado Libre MCP.")?,
        };
        let command = command.to_str().context("La ruta al binario debe poder representarse en UTF-8. Próximo paso: instalalo en otra carpeta.")?.to_owned();
        let state_dir = env_path("MELI_MCP_STATE_DIR")
            .map(|_| std::path::absolute(crate::state::state_dir()))
            .transpose()?
            .map(|path| path.into_os_string().into_string())
            .transpose()
            .map_err(|_| anyhow!("La ruta al estado debe poder representarse en UTF-8. Próximo paso: elegí otra carpeta con MELI_MCP_STATE_DIR."))?;
        let spec = ServerSpec {
            name: self.name,
            command,
            state_dir,
        };
        let project = std::path::absolute(self.project_dir.unwrap_or_else(|| PathBuf::from(".")))?;
        let (clients, not_detected) = selected_clients(&self.client, &env)?;
        let mut entries = Vec::new();

        for client in &clients {
            let path = client.target_path(&env, self.scope, &project);
            entries.push(match plan_file(*client, &path, &spec, self.force) {
                Ok(entry) => entry,
                Err(error) => (
                    Outcome::new(*client, path, Status::Error).with_detail(file_error(error)),
                    None,
                ),
            });
        }
        let mut launch = None;
        let mut launch_failed = false;
        if entries.iter().any(|(outcome, _)| outcome.status.success())
            && !self.dry_run
            && !self.no_verify_launch
        {
            launch = Some(
                match tokio::time::timeout(Duration::from_secs(60), handshake(&spec)).await {
                    Ok(Ok(result)) => result,
                    _ => {
                        launch_failed = true;
                        json!({"ok": false, "error": format!(
                            "No se pudo verificar el arranque del servidor por MCP. Próximo paso: ejecutá `{} serve` en la terminal y confirmá la ruta al binario.", spec.command
                        )})
                    }
                },
            );
        }
        for (outcome, edit) in &mut entries {
            if let Some(update) = edit.take() {
                if self.dry_run {
                    outcome.detail = Some("dry-run: no se escribió nada".into());
                } else if launch_failed {
                    outcome.status = Status::Error;
                    outcome.detail = Some("La verificación del servidor falló. Próximo paso: corregí el arranque y volvé a ejecutar install.".into());
                } else if let Err(error) = update.apply(outcome) {
                    outcome.status = Status::Error;
                    outcome.detail = Some(file_error(error));
                }
            }
        }

        let mut next_steps = Vec::new();
        let (installed, problems): (Vec<_>, Vec<_>) = entries
            .into_iter()
            .map(|(outcome, _)| outcome)
            .partition(|outcome| outcome.status.success());
        if clients.is_empty() {
            next_steps.push(format!("No se detectó Claude Code ni Codex. Abrí tu cliente una vez y ejecutá `mercadolibre-mcp install --client <nombre>` (nombres: {}).", client_choices()));
        }
        next_steps.extend(problems.iter().filter_map(|problem| problem.detail.clone()));
        if !installed.is_empty() && !self.dry_run && !launch_failed {
            next_steps.push("Cerrá y volvé a abrir tu cliente. Pedile que llame meli_start_here y después meli_status.".into());
        }
        if let Some(message) = launch.as_ref().and_then(|launch| launch["error"].as_str()) {
            next_steps.push(message.into());
        }

        let ok = !installed.is_empty()
            && problems.is_empty()
            && !launch_failed
            && installed.iter().all(|entry| entry.verified || self.dry_run);
        if self.json {
            let report = json!({
                "ok": ok, "dry_run": self.dry_run, "command": spec.argv(), "installed": installed,
                "problems": problems, "not_detected": not_detected, "launch_check": launch,
                "next_steps": next_steps,
            });
            println!("{}", serde_json::to_string_pretty(&report)?);
        } else {
            let prefix = if self.dry_run { "[dry-run] " } else { "" };
            println!("{prefix}Servidor: {} serve", spec.command);
            for entry in installed.iter().chain(&problems) {
                print!(
                    "- {}: {} ({})",
                    entry.client_kind.label(),
                    entry.status.label(),
                    entry.file.display()
                );
                if let Some(backup) = &entry.backup {
                    print!(" copia de respaldo: {}", backup.display());
                }
                if let Some(detail) = entry.detail.as_deref().filter(|_| !entry.status.success()) {
                    print!(" {detail}");
                }
                println!();
            }
            if let Some(launch) = &launch {
                if let Some(error) = launch["error"].as_str() {
                    println!("- Arranque del servidor: {error}");
                } else {
                    println!(
                        "- Arranque del servidor: OK, {} tools (versión {})",
                        launch["tools"],
                        launch["version"].as_str().unwrap_or("?")
                    );
                }
            }
            if !not_detected.is_empty() && self.client.is_empty() {
                println!("No detectados: {}", not_detected.join(", "));
            }
            println!("Próximos pasos:");
            for step in next_steps {
                println!("  {step}");
            }
        }
        Ok(if ok {
            ExitCode::SUCCESS
        } else {
            ExitCode::FAILURE
        })
    }
}

fn file_error(error: anyhow::Error) -> String {
    let message = error.to_string();
    if message.contains("Próximo paso") {
        message
    } else {
        format!(
            "No se pudo escribir ({message}). Próximo paso: revisá los permisos de esa carpeta y reintentá."
        )
    }
}

pub(crate) fn installed_status() -> Result<Vec<(&'static str, bool)>> {
    let env = Environment::current()?;
    let mut entries = Vec::new();
    for client in CLIENTS {
        if !client.detected(&env) {
            continue;
        }
        let path = client.user_path(&env);
        let found = read_file(&path).ok().flatten().is_some_and(|text| {
            let text = text.strip_prefix('\u{feff}').unwrap_or(&text);
            if client == Client::Codex {
                return text.parse::<DocumentMut>().ok().is_some_and(|doc| {
                    doc.get("mcp_servers")
                        .and_then(Item::as_table_like)
                        .and_then(|table| table.get(DEFAULT_NAME))
                        .is_some()
                });
            }
            parse_to_serde_value::<Value>(text, &jsonc_options())
                .ok()
                .is_some_and(|value| !value["mcpServers"][DEFAULT_NAME].is_null())
        });
        entries.push((client.label(), found));
    }
    Ok(entries)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn spec() -> ServerSpec {
        ServerSpec {
            name: DEFAULT_NAME.into(),
            command: "/ruta con espacios/mercadolibre-mcp".into(),
            state_dir: Some("/datos/meli".into()),
        }
    }

    #[test]
    fn all_clients_write_valid_configuration_idempotently() -> Result<()> {
        let dir = tempfile::tempdir()?;
        for client in CLIENTS {
            let path = dir.path().join(client.id());
            let (mut outcome, update) = plan_file(client, &path, &spec(), false)?;
            assert!(outcome.status == Status::Created);
            update.context("no config update")?.apply(&mut outcome)?;
            assert!(outcome.verified && outcome.backup.is_none());
            let (second, update) = plan_file(client, &path, &spec(), false)?;
            assert!(second.status == Status::Unchanged && update.is_none());
        }
        Ok(())
    }

    #[test]
    fn migrates_uvx_without_losing_jsonc_strings_comments_or_env() -> Result<()> {
        let dir = tempfile::tempdir()?;
        let path = dir.path().join("mcp.json");
        let original = r#"{
    // don't discard comments
    "mcpServers": {
        "mercadolibre": {
            "command": "uvx",
            "args": ["--from", "git+https://github.com/fedeclavero/mercadolibre-mcp", "mercadolibre-mcp", "serve"],
            "env": {"MY_SETTING": "literal ,}",},
        },
        "other": {"command": "other://example", "flags": [1, 2,],},
    },
}"#;
        fs::write(&path, original)?;
        let (mut outcome, update) = plan_file(Client::ClaudeCode, &path, &spec(), false)?;
        assert!(outcome.status == Status::Updated);
        update.context("no config update")?.apply(&mut outcome)?;
        assert!(outcome.verified);
        assert_eq!(
            fs::read_to_string(outcome.backup.context("no backup")?)?,
            original
        );
        let text = fs::read_to_string(path)?;
        assert!(text.contains("// don't discard comments"));
        let parsed: Value = parse_to_serde_value(&text, &jsonc_options())?;
        assert_eq!(
            parsed["mcpServers"][DEFAULT_NAME]["command"],
            spec().command
        );
        assert_eq!(
            parsed["mcpServers"][DEFAULT_NAME]["env"]["MY_SETTING"],
            "literal ,}"
        );
        assert_eq!(
            parsed["mcpServers"][DEFAULT_NAME]["env"]["MELI_MCP_STATE_DIR"],
            "/datos/meli"
        );
        assert_eq!(parsed["mcpServers"]["other"]["command"], "other://example");
        Ok(())
    }

    #[test]
    fn preserves_existing_toml_tables_and_settings_including_inline_env() -> Result<()> {
        let original = r#"model = "x"
# This belongs to the unrelated server.
[mcp_servers.other]
command = "node"

[mcp_servers.mercadolibre]
command = "/viejo/.venv/bin/mercadolibre-mcp" # launch location
args = ["serve"]
enabled = false
startup_timeout_sec = 25
env = { MY_SETTING = "literal" }
enabled_tools = ["meli_status"]
"#;
        let Edit::Write { text, exists: true } =
            patch_toml(Path::new("config.toml"), original, &spec(), false)?
        else {
            bail!("expected update");
        };
        assert!(text.contains("# launch location"));
        assert!(text.contains("# This belongs to the unrelated server."));
        let updated: DocumentMut = text.parse()?;
        let entry = &updated["mcp_servers"][DEFAULT_NAME];
        assert_eq!(entry["env"]["MY_SETTING"].as_str(), Some("literal"));
        assert_eq!(
            entry["env"]["MELI_MCP_STATE_DIR"].as_str(),
            Some("/datos/meli")
        );
        assert_eq!(entry["enabled"].as_bool(), Some(false));
        assert_eq!(entry["startup_timeout_sec"].as_integer(), Some(25));
        assert_eq!(
            entry["enabled_tools"]
                .as_array()
                .and_then(|a| a.get(0))
                .and_then(toml_edit::Value::as_str),
            Some("meli_status")
        );
        assert_eq!(
            updated["mcp_servers"]["other"]["command"].as_str(),
            Some("node")
        );
        Ok(())
    }

    #[test]
    fn only_force_replaces_remote_entry_and_drops_remote_only_fields() -> Result<()> {
        let path = Path::new("settings.json");
        let old = r#"{"mcpServers":{"mercadolibre":{"serverUrl":"https://example.org/mcp","headers":{"Authorization":"Bearer test"}}}}"#;
        assert!(matches!(
            patch_json(path, old, &spec(), false)?,
            Edit::Conflict
        ));
        let Edit::Write { text, .. } = patch_json(path, old, &spec(), true)? else {
            bail!("expected replacement");
        };
        let parsed: Value = parse_to_serde_value(&text, &jsonc_options())?;
        assert!(parsed["mcpServers"][DEFAULT_NAME]["serverUrl"].is_null());
        assert!(parsed["mcpServers"][DEFAULT_NAME]["headers"].is_null());
        Ok(())
    }

    #[test]
    fn keeps_concurrent_changes_when_planned_snapshot_is_stale() -> Result<()> {
        let dir = tempfile::tempdir()?;
        let path = dir.path().join("mcp.json");
        let (mut outcome, update) = plan_file(Client::ClaudeCode, &path, &spec(), false)?;
        fs::write(&path, r#"{"new-setting":true}"#)?;
        assert!(
            update
                .context("no config update")?
                .apply(&mut outcome)
                .is_err()
        );
        assert_eq!(fs::read_to_string(path)?, r#"{"new-setting":true}"#);
        Ok(())
    }
}
