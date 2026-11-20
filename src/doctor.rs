use std::{fs, path::Path, process::ExitCode};

use anyhow::Result;
use clap::Args;
use serde::Serialize;
use serde_json::{Value, json};

use crate::{auth, client::Api, installer, state};

const VERSION: &str = env!("CARGO_PKG_VERSION");

#[derive(Args)]
pub(crate) struct DoctorArgs {
    /// No consultar la API de Mercado Libre.
    #[arg(long)]
    offline: bool,
    #[arg(long)]
    pub(crate) json: bool,
}

#[derive(Serialize)]
struct Check {
    check: &'static str,
    ok: bool,
    level: &'static str,
    detail: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    next_step: Option<String>,
}

impl Check {
    fn new(check: &'static str, detail: impl Into<String>) -> Self {
        Self {
            check,
            ok: true,
            level: "ok",
            detail: detail.into(),
            next_step: None,
        }
    }

    fn fail(mut self, level: &'static str, next: impl Into<String>) -> Self {
        self.ok = false;
        self.level = level;
        self.next_step = Some(next.into());
        self
    }
}

fn check_folder(path: &Path) -> Check {
    let metadata = match fs::symlink_metadata(path) {
        Ok(metadata) => metadata,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            return Check::new(
                "carpeta_de_estado",
                "todavía no existe (se crea al ejecutar setup)",
            );
        }
        Err(_) => {
            return Check::new(
                "carpeta_de_estado",
                "No se pudo revisar la carpeta de estado.",
            )
            .fail("warn", "Revisá el acceso a la carpeta MELI_MCP_STATE_DIR.");
        }
    };
    if !metadata.is_dir() {
        return Check::new(
            "carpeta_de_estado",
            "La ruta de estado debe ser una carpeta real.",
        )
        .fail("error", "Elegí una carpeta privada con MELI_MCP_STATE_DIR.");
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        if metadata.permissions().mode() & 0o077 != 0 {
            return Check::new(
                "carpeta_de_estado",
                "La carpeta de estado admite acceso de otros usuarios.",
            )
            .fail("warn", format!("Ejecutá `chmod 700 {:?}`.", path));
        }
    }
    Check::new("carpeta_de_estado", "carpeta privada")
}

fn check_config(config: Option<&Value>) -> Check {
    let key = "configuracion";
    let Some(config) = config else {
        return Check::new(key, "No hay configuración de la app.").fail(
            "error",
            "Creá la app en el DevCenter (`mercadolibre-mcp devcenter --country ar`) y ejecutá `mercadolibre-mcp setup`.",
        );
    };
    let missing = ["client_id", "client_secret", "redirect_uri", "country"]
        .into_iter()
        .filter(|key| config[key].as_str().is_none_or(str::is_empty))
        .collect::<Vec<_>>();
    if !missing.is_empty() {
        return Check::new(key, format!("Faltan campos: {}.", missing.join(", "))).fail(
            "error",
            "Ejecutá `mercadolibre-mcp setup` con todos los datos de la app (docs/AGENT_INSTALL.md, paso 5).",
        );
    }
    let country = config["country"].as_str().unwrap_or_default();
    let redirect = config["redirect_uri"].as_str().unwrap_or_default();
    if auth::devcenter_url(country).is_err() || auth::validate_redirect(redirect).is_err() {
        return Check::new(key, "País o URL de redirección inválidos.").fail(
            "error",
            "Ejecutá `mercadolibre-mcp setup` otra vez con --country y --redirect-uri correctos.",
        );
    }
    let pkce = if config["pkce"].as_bool().unwrap_or(false) {
        "sí"
    } else {
        "no"
    };
    let id = config["client_id"].as_str().unwrap_or_default();
    Check::new(
        key,
        format!("app {id}, país {country}, PKCE {pkce}, redirección {redirect}"),
    )
}

fn check_token(token: Option<&Value>, checks: &mut Vec<Check>, read_only: bool) -> bool {
    let key = "autorizacion";
    let Some(token) = token else {
        checks.push(
            Check::new(key, "La cuenta todavía no está autorizada.").fail(
                "error",
                "Ejecutá `mercadolibre-mcp authorize` (docs/AGENT_INSTALL.md, paso 6).",
            ),
        );
        return false;
    };
    if token["refresh_token"].as_str().is_none_or(str::is_empty) {
        checks.push(
            Check::new(key, "La autorización no trae refresh token.").fail(
                "error",
                "Habilitá offline_access en la app y ejecutá `mercadolibre-mcp authorize`.",
            ),
        );
        return false;
    }
    let expires = token["expires_at"]
        .as_f64()
        .or_else(|| token["expires_at"].as_str().and_then(|v| v.parse().ok()))
        .filter(|value| value.is_finite());
    let Some(expires) = expires else {
        checks.push(
            Check::new(key, "La expiración del acceso es inválida.").fail(
                "error",
                "Ejecutá `mercadolibre-mcp authorize` para volver a autorizar.",
            ),
        );
        return false;
    };
    let left = expires - state::now();
    checks.push(Check::new(
        key,
        if left < 60.0 {
            "cuenta autorizada (el acceso se renueva solo)".into()
        } else {
            format!(
                "cuenta autorizada (acceso vigente {} min más, se renueva solo)",
                (left / 60.0).floor() as u64
            )
        },
    ));
    if let Some(scope) = token["scope"].as_str() {
        let scopes = scope.replace(',', " ");
        let mut have: Vec<&str> = scopes.split_whitespace().collect();
        have.sort_unstable();
        have.dedup();
        let required = ["read", "write", "offline_access"];
        let lacking: Vec<_> = required
            .into_iter()
            .filter(|scope| !(*scope == "write" && read_only) && !have.contains(scope))
            .collect();
        let permissions = if lacking.is_empty() {
            Check::new(
                "permisos",
                format!("permisos autorizados: {}", have.join(", ")),
            )
        } else {
            Check::new("permisos", format!("Faltan permisos: {}.", lacking.join(", "))).fail(
                "error",
                "Revisá en el DevCenter los permisos para las operaciones que necesitás y el acceso offline. Ejecutá `mercadolibre-mcp authorize` otra vez.",
            )
        };
        checks.push(permissions);
    }
    true
}

fn check_clients() -> Check {
    let name = "clientes_mcp";
    let entries = match installer::installed_status() {
        Ok(entries) => entries,
        Err(error) => {
            return Check::new(name, error.to_string()).fail(
                "warn",
                "Revisá los archivos de configuración de tus clientes MCP.",
            );
        }
    };
    let registered: Vec<_> = entries
        .iter()
        .filter_map(|(name, found)| (*found).then_some(*name))
        .collect();
    if !registered.is_empty() {
        return Check::new(name, format!("registrado en: {}", registered.join(", ")));
    }
    if entries.is_empty() {
        return Check::new(name, "No se detectó ningún cliente MCP conocido.").fail(
            "warn",
            "Instalá/abrí tu cliente de IA y ejecutá `mercadolibre-mcp install --client <nombre>` (nombres en docs/AGENT_INSTALL.md).",
        );
    }
    let names: Vec<_> = entries.iter().map(|(name, _)| *name).collect();
    Check::new(
        name,
        format!("Clientes detectados sin registrar: {}.", names.join(", ")),
    )
    .fail(
        "warn",
        "Ejecutá `mercadolibre-mcp install --client all` y reiniciá el cliente.",
    )
}

async fn check_api() -> Check {
    match Api::new().get("/users/me", Value::Null).await {
        Ok(result) => {
            let profile = result.get("data").unwrap_or(&result);
            let nickname = profile["nickname"].as_str().unwrap_or("?");
            let id = profile["id"].as_str().map_or_else(
                || profile["id"].as_i64().map_or_else(|| "?".into(), |id| id.to_string()),
                str::to_owned,
            );
            Check::new("api", format!("Mercado Libre responde: usuario {nickname} (id {id})"))
        }
        Err(error) => Check::new("api", error.to_string()).fail(
            "error",
            "Seguí el próximo paso del mensaje. Si dice 401 o 403, ejecutá `mercadolibre-mcp authorize`.",
        ),
    }
}

impl DoctorArgs {
    pub(crate) async fn run(self) -> Result<ExitCode> {
        let mut checks = vec![
            Check::new("version", format!("mercadolibre-mcp {VERSION} (Rust)")),
            check_folder(&state::state_dir()),
        ];
        let config = state::load_private("config.json").ok();
        let config_check = check_config(config.as_ref());
        let config_valid = config_check.ok;
        checks.push(config_check);

        let read_only = state::access_mode() == "read_only";
        let token = state::load_private("token.json").ok();
        let token_valid = check_token(token.as_ref(), &mut checks, read_only);
        checks.push(Check::new(
            "modo",
            if read_only {
                "SOLO LECTURA (las escrituras están bloqueadas; `mode full` las habilita)"
            } else {
                "lectura y escritura (completo)"
            },
        ));
        if !self.offline && config_valid && token_valid {
            checks.push(check_api().await);
        }
        checks.push(check_clients());
        let first_error = checks.iter().find(|c| c.level == "error");
        let next_step = first_error.or_else(|| checks.iter().find(|c| c.level == "warn"))
            .and_then(|c| c.next_step.as_deref())
            .unwrap_or("Todo listo: reiniciá tu cliente de IA y pedile que llame meli_start_here y meli_status.");
        let ok = first_error.is_none();

        if self.json {
            let result =
                json!({"ok": ok, "version": VERSION, "checks": checks, "next_step": next_step});
            println!("{}", serde_json::to_string_pretty(&result)?);
        } else {
            println!("mercadolibre-mcp {VERSION}");
            for check in &checks {
                println!(
                    "[{:5}] {}: {}",
                    check.level.to_uppercase(),
                    check.check,
                    check.detail
                );
            }
            println!("\nPróximo paso: {next_step}");
        }
        Ok(if ok {
            ExitCode::SUCCESS
        } else {
            ExitCode::FAILURE
        })
    }
}
