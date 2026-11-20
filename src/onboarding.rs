use std::{
    env,
    io::{self, IsTerminal as _, Read as _, Write as _},
    time::Duration,
};

use anyhow::{Result, bail};
use clap::{Args, ValueEnum};
use serde_json::{Value, json};

use crate::{
    auth::{self, Attempt, COUNTRIES, CallbackServer, DEFAULT_REDIRECT, PENDING_TTL},
    state::{CLI, load_private, lock, write_private},
};

const MANUAL_START: &str = "Abrí authorize_url (si no se abrió), pedile al usuario que inicie sesión con la cuenta principal y autorice, y que te pase la URL completa de la barra de direcciones; luego ejecutá `mercadolibre-mcp authorize --finish --callback-url '<URL>'`.";
const FINISH_HINT: &str = "Falta la URL de retorno. Próximo paso: ejecutá `mercadolibre-mcp authorize --finish --callback-url '<URL completa>'`.";

#[derive(Clone, Copy, ValueEnum)]
pub enum Mode {
    Full,
    ReadOnly,
}

impl Mode {
    fn stored(self) -> &'static str {
        match self {
            Self::Full => "full",
            Self::ReadOnly => "read_only",
        }
    }
}

#[derive(Args)]
pub struct SetupArgs {
    #[arg(long)]
    client_id: Option<String>,
    #[arg(
        long,
        help = "Visible en la lista de procesos. Preferí --client-secret-stdin."
    )]
    client_secret: Option<String>,
    #[arg(long, help = "Lee el Client Secret desde la entrada estándar.")]
    client_secret_stdin: bool,
    #[arg(long)]
    redirect_uri: Option<String>,
    #[arg(long, value_parser = auth::parse_country)]
    country: Option<String>,
    #[arg(long, overrides_with = "no_pkce")]
    pkce: bool,
    #[arg(long, overrides_with = "pkce")]
    no_pkce: bool,
    #[arg(long)]
    mode: Option<Mode>,
    #[arg(long)]
    pub json: bool,
}

impl SetupArgs {
    pub async fn run(self) -> Result<()> {
        let mut client_id = input_or_env(self.client_id, "MELI_CLIENT_ID");
        let mut secret = self.client_secret;
        if self.client_secret_stdin {
            let mut input = String::new();
            io::stdin().read_to_string(&mut input)?;
            secret = Some(input.trim().to_owned());
        }
        let mut secret = input_or_env(secret, "MELI_CLIENT_SECRET");
        let mut redirect = input_or_env(self.redirect_uri, "MELI_REDIRECT_URI");
        let mut country = input_or_env(self.country, "MELI_COUNTRY");
        let mut pkce = if self.pkce {
            Some(true)
        } else if self.no_pkce {
            Some(false)
        } else {
            env::var("MELI_PKCE")
                .ok()
                .filter(|value| !value.is_empty())
                .map(|value| {
                    matches!(
                        value.trim().to_lowercase().as_str(),
                        "1" | "true" | "yes" | "y" | "s" | "si" | "sí" | "on"
                    )
                })
        };
        if io::stdin().is_terminal() && !self.client_secret_stdin {
            if client_id.is_none() {
                client_id = Some(prompt("APP ID: ")?);
            }
            if secret.is_none() {
                secret = Some(rpassword::prompt_password("Secret Key (no se mostrará): ")?);
            }
            if redirect.is_none() {
                redirect = nonempty(Some(prompt(&format!(
                    "Redirect URI HTTPS exacta configurada en la aplicación [{DEFAULT_REDIRECT}]: "
                ))?));
            }
            if country.is_none() {
                country = nonempty(Some(prompt(&format!("País ({COUNTRIES}) [ar]: "))?));
            }
            if pkce.is_none() {
                pkce = Some(!matches!(
                    prompt("¿Activaste PKCE en la aplicación? [S/n]: ")?
                        .to_lowercase()
                        .as_str(),
                    "n" | "no"
                ));
            }
        } else {
            let mut missing = Vec::new();
            if client_id.is_none() {
                missing.push("--client-id");
            }
            if secret.is_none() {
                missing.push("--client-secret (o --client-secret-stdin)");
            }
            if !missing.is_empty() {
                bail!(
                    "Faltan datos de la app: {}. Próximo paso: pedile al usuario el APP ID y el Client Secret (guía: `{CLI} devcenter --country ar`) y ejecutá `{CLI} setup --client-id <APP_ID> --client-secret-stdin --redirect-uri {DEFAULT_REDIRECT} --country ar`, pasando el secreto por stdin.",
                    missing.join(", ")
                );
            }
        }

        let client_id = client_id.unwrap_or_default();
        let secret = secret.unwrap_or_default();
        let redirect = redirect.unwrap_or_else(|| DEFAULT_REDIRECT.to_owned());
        let country = country.unwrap_or_else(|| "ar".to_owned()).to_lowercase();
        let pkce = pkce.unwrap_or(true);
        if client_id.trim().is_empty()
            || secret.trim().is_empty()
            || auth::parse_country(&country).is_err()
        {
            bail!(
                "Datos inválidos: el APP ID y el Secret no pueden estar vacíos y el país debe ser uno de {COUNTRIES}. Próximo paso: corregí el dato y ejecutá `{CLI} setup` otra vez."
            );
        }
        let uri = auth::validate_redirect(&redirect)?;

        let (mode, removed) = {
            let _lock = lock("token").await?;
            let previous = load_private("config.json").unwrap_or_else(|_| json!({}));
            let mut config = previous.clone();
            config["client_id"] = json!(client_id.trim());
            config["client_secret"] = json!(secret.trim());
            config["redirect_uri"] = json!(redirect);
            config["country"] = json!(country);
            config["pkce"] = json!(pkce);
            if let Some(mode) = self.mode {
                config["mode"] = json!(mode.stored());
            }

            // If the app changed, make its old token inaccessible before writing the new config.
            let removed = previous["client_id"] != config["client_id"]
                && auth::remove_state_file("token.json")?;
            if [
                "client_id",
                "client_secret",
                "redirect_uri",
                "country",
                "pkce",
            ]
            .into_iter()
            .any(|key| previous[key] != config[key])
            {
                auth::remove_state_file("pending-auth.json")?;
            }
            write_private("config.json", &config)?;
            (
                config["mode"]
                    .as_str()
                    .filter(|value| !value.is_empty())
                    .unwrap_or("full")
                    .to_owned(),
                removed,
            )
        };
        let next_step = format!(
            "Ejecutá `{CLI} authorize`{}",
            if auth::is_loopback(&uri) {
                " (abre el navegador y captura el permiso solo)."
            } else {
                " (abre el navegador; después pegá la URL de retorno o usá --start/--finish)."
            }
        );
        emit(
            self.json,
            &json!({
                "ok": true,
                "country": country,
                "redirect_uri": redirect,
                "pkce": pkce,
                "mode": mode,
                "authorization_removed": removed,
                "next_step": next_step
            }),
            &[
                "Configuración guardada (el secreto no se muestra).",
                &format!("Próximo paso: {next_step}"),
            ],
        )
    }
}

#[derive(Args)]
pub struct AuthorizeArgs {
    #[arg(
        long,
        conflicts_with = "finish",
        help = "Imprime la URL y guarda el intento."
    )]
    start: bool,
    #[arg(long, help = "Completa la autorización con la URL de retorno.")]
    finish: bool,
    #[arg(long, requires = "finish", conflicts_with = "code")]
    callback_url: Option<String>,
    #[arg(
        long,
        requires = "finish",
        help = "Código de retorno explícito. Preferí la URL completa."
    )]
    code: Option<String>,
    #[arg(long, help = "Pide la URL de retorno sin abrir el receptor local.")]
    no_listen: bool,
    #[arg(long, help = "No abre el navegador.")]
    no_open: bool,
    #[arg(long, default_value_t = 300.0)]
    timeout: f64,
    #[arg(long)]
    pub json: bool,
}

impl AuthorizeArgs {
    pub async fn run(self) -> Result<()> {
        if self.finish
            && self.callback_url.is_none()
            && self.code.is_none()
            && !io::stdin().is_terminal()
        {
            bail!(FINISH_HINT);
        }
        let config = load_private("config.json")?;
        let redirect = auth::config_text(&config, "redirect_uri")?;
        let uri = auth::validate_redirect(redirect)?;

        if self.finish {
            let attempt = Attempt::load(&config).await?;
            let allow_bare = self.callback_url.is_none();
            let callback = match self.callback_url.or(self.code) {
                Some(callback) => callback,
                None => rpassword::prompt_password(
                    "Pega la URL de retorno o el código (no se mostrará): ",
                )?,
            };
            let code = attempt.code(&callback, &uri, allow_bare)?;
            attempt.exchange(&config, &code).await?;
            return authorized(self.json);
        }

        let attempt = Attempt::new()?;
        let url = attempt.url(&config)?;
        if self.start {
            attempt.save(&config).await?;
            let opened = !self.no_open && open::that_detached(&url).is_ok();
            return emit(
                self.json,
                &json!({
                    "ok": true, "authorize_url": url, "browser_opened": opened, "redirect_uri": redirect,
                    "expires_in": PENDING_TTL, "next_step": MANUAL_START
                }),
                &[
                    "Abrí este enlace, inicia sesión con la cuenta principal y autoriza la aplicación:",
                    &url,
                    "",
                    "Luego copiá la URL completa de la barra de direcciones (aunque la página no cargue) y ejecutá:",
                    "  mercadolibre-mcp authorize --finish --callback-url '<URL>'",
                ],
            );
        }

        let listener = if !self.no_listen && auth::is_loopback(&uri) {
            Some(CallbackServer::bind(uri.clone()).await?)
        } else {
            None
        };
        if listener.is_none() && !io::stdin().is_terminal() {
            bail!(
                "Se necesita una terminal para pegar la URL de retorno. Próximo paso: ejecutá `{CLI} authorize --start` y luego `{CLI} authorize --finish --callback-url '<URL completa>'`."
            );
        }
        let wait = Duration::try_from_secs_f64(self.timeout).map_err(|_| {
            anyhow::anyhow!("El tiempo de espera debe ser un número finito mayor o igual a cero. Próximo paso: corregí --timeout.")
        })?;
        attempt.save(&config).await?;
        eprintln!(
            "Abrí este enlace, iniciá sesión con la cuenta principal y autorizá la aplicación:\n{url}"
        );
        if !self.no_open {
            let _ = open::that_detached(&url);
        }
        let code = if let Some(listener) = listener {
            eprintln!(
                "El navegador va a mostrar una advertencia de certificado para localhost: es normal; tocá Avanzado y Continuar."
            );
            listener.receive(attempt.clone(), wait).await?
        } else {
            eprintln!(
                "Luego copiá la URL completa de la barra de direcciones, aunque la página no cargue."
            );
            let callback = rpassword::prompt_password("Pega la URL de retorno (no se mostrará): ")?;
            attempt.code(&callback, &uri, false)?
        };
        attempt.exchange(&config, &code).await?;
        authorized(self.json)
    }
}

fn authorized(as_json: bool) -> Result<()> {
    let next_step = format!("Ejecutá `{CLI} doctor` y reiniciá tu cliente de IA.");
    emit(
        as_json,
        &json!({"ok": true, "authorized": true, "next_step": next_step}),
        &[
            "Cuenta autorizada. Podés cerrar la ventana del navegador.",
            &format!("Próximo paso: {next_step}"),
        ],
    )
}

#[derive(Args)]
pub struct ModeArgs {
    mode: Option<Mode>,
    #[arg(long)]
    pub json: bool,
}

impl ModeArgs {
    pub async fn run(self) -> Result<()> {
        let _lock = lock("token").await?;
        let mut config = load_private("config.json")?;
        if let Some(mode) = self.mode {
            config["mode"] = json!(mode.stored());
            write_private("config.json", &config)?;
        }
        let mode = if config["mode"] == "read_only" {
            Mode::ReadOnly
        } else {
            Mode::Full
        };
        let message = match mode {
            Mode::Full => "lectura y escritura (completo)",
            Mode::ReadOnly => "SOLO LECTURA",
        };
        let note = if self.mode.is_some() {
            "Reiniciá tu cliente de IA para que el cambio se aplique."
        } else {
            "Para cambiarlo: `mode full` o `mode read-only`."
        };
        emit(
            self.json,
            &json!({"ok": true, "mode": mode.stored(), "note": note}),
            &[&format!("Modo: {message}. {note}")],
        )
    }
}

#[derive(Args)]
pub struct DevcenterArgs {
    #[arg(long, default_value = "ar", value_parser = auth::parse_country)]
    country: String,
    #[arg(long, default_value = DEFAULT_REDIRECT)]
    redirect_uri: String,
    #[arg(long)]
    open: bool,
    #[arg(long)]
    pub json: bool,
}

impl DevcenterArgs {
    pub async fn run(self) -> Result<()> {
        auth::validate_redirect(&self.redirect_uri)?;
        let url = auth::devcenter_url(&self.country)?;
        let before = [
            "Entrá con la cuenta TITULAR (no con un colaborador/operador): esa cuenta será la dueña de la app.",
            "En Argentina, México, Brasil y Chile el DevCenter solo deja crear apps cuando los datos del titular de la cuenta están cargados y validados; si pide validar datos, completalos primero.",
            "Mercado Libre exige una app por unidad (Mercado Libre y Mercado Pago por separado, desde el 30/08/2026): creá esta app en el DevCenter de Mercado Libre y no tildes permisos de Mercado Pago.",
        ];
        let first = format!("1. Abrí {url} e iniciá sesión.");
        let redirect_step = format!(
            "6. URI de redirección (obligatoriamente HTTPS): {}",
            self.redirect_uri
        );
        let steps = [
            &first,
            "2. Tocá \"Crear nueva aplicación\".",
            "3. Nombre: uno único, por ejemplo \"Mi tienda MCP\" (si dice que ya existe, agregale un número).",
            "4. Descripción (hasta 150 caracteres): \"Gestión de mi cuenta con un asistente de IA\".",
            "5. Logo: subí cualquier imagen que pida el formulario (puede ser el logo de tu tienda).",
            &redirect_step,
            "7. Marcá \"Use PKCE\" (recomendado).",
            "8. NO marques \"Device Grant\".",
            concat!(
                "9. Scopes y permisos funcionales. Usuarios ya viene activo. Habilitá solo los grupos que necesitás: ",
                "Publicación y sincronización, Comunicación pre y postventa, Publicidad, Métricas del negocio, ",
                "Ventas y envíos, Promociones, cupones y descuentos, y Facturación. Elegí \"Solo lectura\" para consultas ",
                "y \"Lectura y escritura\" en los grupos donde el agente deba hacer cambios. Marcá \"acceso offline\" ",
                "(offline_access) si aparece en el formulario para renovar la conexión."
            ),
            "10. Tópicos: no marques ninguno (este servidor no recibe notificaciones). Si el formulario igual exige una URL de notificaciones, poné la misma URI de redirección.",
            "11. Guardá. La app aparece en \"Mis aplicaciones\": copiá el APP ID (Client ID) y el Client Secret (Secret Key; mostralo con el ojo/\"Mostrar\").",
        ];
        let alternatives = [
            "Probá con https://127.0.0.1:8765/callback",
            "Probá con https://www.mercadolibre.com.ar/",
            "O usá una URL HTTPS de un dominio tuyo. Después pasá esa misma URI exacta a `mercadolibre-mcp setup --redirect-uri`.",
        ];
        let copy = ["APP ID (Client ID)", "Client Secret (Secret Key)"];
        let note = "La documentación oficial exige HTTPS en la URI de redirección pero no dice si acepta localhost; si el formulario la acepta, `mercadolibre-mcp authorize` captura el permiso solo (el navegador mostrará una advertencia de certificado: es normal en localhost, tocá Avanzado y Continuar).";
        if self.open {
            let _ = open::that_detached(&url);
        }
        let heading = format!("DevCenter: {url}");
        let values = format!("Copiá: {}", copy.join(" y "));
        let mut lines: Vec<&str> = vec![&heading, ""];
        lines.extend(before);
        lines.push("");
        lines.extend(steps);
        lines.extend(["", "Si el formulario rechaza la URI de redirección:"]);
        lines.extend(alternatives);
        lines.extend(["", &values, "", note]);
        emit(
            self.json,
            &json!({
                "ok": true, "devcenter_url": url, "antes_de_empezar": before, "pasos": steps,
                "si_el_formulario_rechaza_la_uri": alternatives, "valores_a_copiar": copy,
                "redirect_uri_sugerida": self.redirect_uri, "nota_localhost": note,
            }),
            &lines,
        )
    }
}

fn input_or_env(input: Option<String>, name: &str) -> Option<String> {
    nonempty(input).or_else(|| nonempty(env::var(name).ok()))
}

fn nonempty(input: Option<String>) -> Option<String> {
    input
        .map(|value| value.trim().to_owned())
        .filter(|value| !value.is_empty())
}

fn prompt(label: &str) -> Result<String> {
    print!("{label}");
    io::stdout().flush()?;
    let mut input = String::new();
    io::stdin().read_line(&mut input)?;
    Ok(input.trim().to_owned())
}

fn emit(as_json: bool, value: &Value, human: &[&str]) -> Result<()> {
    if as_json {
        println!("{}", serde_json::to_string_pretty(value)?);
    } else {
        println!("{}", human.join("\n"));
    }
    Ok(())
}
