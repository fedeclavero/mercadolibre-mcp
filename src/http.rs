use std::io::ErrorKind;

use anyhow::{Result, anyhow, bail};
use axum::{
    Json, Router,
    extract::Request,
    http::{StatusCode, header},
    middleware::{self, Next},
    response::{IntoResponse, Response},
};
use base64::{Engine, engine::general_purpose::URL_SAFE_NO_PAD};
use clap::Args;
use rmcp::transport::streamable_http_server::{
    StreamableHttpServerConfig, StreamableHttpService, session::local::LocalSessionManager,
};
use serde_json::json;
use subtle::ConstantTimeEq;
use url::Url;

use crate::{
    server::Server,
    state::{self, CLI},
};

const TOKEN_FILE: &str = "http-token";

fn read_token() -> Result<Option<String>> {
    let raw = match state::read_bytes(TOKEN_FILE) {
        Ok(raw) => raw,
        Err(error) if error.kind() == ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(error.into()),
    };
    let token = std::str::from_utf8(&raw)?.trim();
    if token.len() < 32
        || !token
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || b"_-".contains(&byte))
    {
        bail!("El token HTTP es inválido. Próximo paso: ejecutá `{CLI} http-token --rotate`.");
    }
    Ok(Some(token.to_owned()))
}

fn get_token(rotate: bool) -> Result<String> {
    let _lock = state::operation_lock(TOKEN_FILE)?;
    if !rotate && let Some(token) = read_token()? {
        return Ok(token);
    }
    let mut secret = [0; 32];
    getrandom::fill(&mut secret).map_err(|_| anyhow!("No se pudo generar un token seguro"))?;
    let token = URL_SAFE_NO_PAD.encode(secret);
    state::write_bytes(TOKEN_FILE, format!("{token}\n").as_bytes())?;
    Ok(token)
}

#[derive(Args)]
pub struct TokenArgs {
    #[arg(long)]
    rotate: bool,
    #[arg(long)]
    public_url: Option<String>,
    #[arg(long)]
    pub json: bool,
}

impl TokenArgs {
    pub fn run(self) -> Result<()> {
        let base = if let Some(ref base) = self.public_url {
            let url = Url::parse(base).map_err(|_| {
                anyhow!("La URL pública debe usar HTTPS y tener un dominio válido.")
            })?;
            if url.scheme() != "https"
                || url.host().is_none()
                || !url.username().is_empty()
                || url.password().is_some()
                || url.query().is_some()
                || url.fragment().is_some()
            {
                bail!(
                    "La URL pública debe usar HTTPS y tener un dominio válido, sin credenciales, parámetros ni fragmento."
                );
            }
            Some(base.trim_end_matches('/'))
        } else {
            None
        };
        let token = get_token(self.rotate)?;
        let mut result = json!({
            "ok": true,
            "token": token,
            "bearer_header": format!("Authorization: Bearer {token}"),
            "local_url": "http://127.0.0.1:8765/mcp",
        });
        let connector = base.map(|base| format!("{base}/t/{token}/mcp"));
        if let Some(url) = &connector {
            result["connector_url"] = json!(url);
        }
        if self.json {
            println!("{}", serde_json::to_string_pretty(&result)?);
        } else {
            println!(
                "Token: {token}\nCabecera: Authorization: Bearer <token>\nURL local: http://127.0.0.1:8765/mcp (arrancá el servidor con `serve --transport http`)"
            );
            if let Some(url) = connector {
                println!("URL para ChatGPT: {url}");
            }
        }
        Ok(())
    }
}

async fn require_token(request: Request, next: Next) -> Response {
    // Re-read the atomically replaced file so that rotation revokes the old token immediately.
    let token = read_token().ok().flatten();
    let bearer = request
        .headers()
        .get(header::AUTHORIZATION)
        .and_then(|value| value.to_str().ok())
        .and_then(|value| value.split_once(' '))
        .filter(|(kind, _)| kind.eq_ignore_ascii_case("Bearer"))
        .map(|(_, token)| token.trim());
    let path_token = request
        .uri()
        .path()
        .strip_prefix("/t/")
        .and_then(|rest| rest.strip_suffix("/mcp"));
    if let Some(token) = token
        && [bearer, path_token]
            .into_iter()
            .flatten()
            .any(|other| bool::from(token.as_bytes().ct_eq(other.as_bytes())))
    {
        let close_session = request.method() == axum::http::Method::DELETE;
        let response = next.run(request).await;
        // Older MCP clients expect a completed 200/204 for DELETE.
        if close_session && response.status() == StatusCode::ACCEPTED {
            return StatusCode::OK.into_response();
        }
        return response;
    }
    let body = json!({
        "error": "unauthorized",
        "next_step": format!("Enviá el token generado por `{CLI} http-token` como `Authorization: Bearer <token>` o usá la URL que imprime `{CLI} http-token --public-url <URL>`."),
    });
    (
        StatusCode::UNAUTHORIZED,
        [(header::WWW_AUTHENTICATE, "Bearer")],
        Json(body),
    )
        .into_response()
}

pub async fn serve(host: &str, port: u16, allowed_hosts: Vec<String>) -> Result<()> {
    let address = match host {
        "localhost" | "127.0.0.1" => "127.0.0.1",
        "::1" => "::1",
        _ => bail!(
            "Por seguridad el transporte HTTP solo escucha en loopback. Próximo paso: usá --host 127.0.0.1 y, si necesitás URL pública, un túnel HTTPS con --allowed-host <dominio-del-túnel>."
        ),
    };
    let mut hosts = vec![
        "127.0.0.1".to_owned(),
        "localhost".to_owned(),
        "[::1]".to_owned(),
    ];
    let mut origins = vec![
        "http://127.0.0.1:*".to_owned(),
        "http://localhost:*".to_owned(),
        "http://[::1]:*".to_owned(),
    ];
    for name in allowed_hosts {
        let host: axum::http::uri::Authority = name
            .trim()
            .parse()
            .map_err(|_| anyhow!("--allowed-host admite un dominio o dominio:puerto"))?;
        if host.as_str().contains('@') || host.as_str().contains('*') {
            bail!("--allowed-host admite un dominio exacto");
        }
        hosts.push(host.to_string());
        origins.push(format!("https://{host}"));
        origins.push(format!("http://{host}"));
    }
    get_token(false)?;
    let server = Server::new(false)?;
    let config = StreamableHttpServerConfig::default()
        .with_allowed_hosts(hosts)
        .with_allowed_origins(origins)
        .with_legacy_session_mode(true)
        .with_max_request_body_bytes(15 * 1024 * 1024);
    let service = StreamableHttpService::new(
        move || Ok(server.clone()),
        LocalSessionManager::default().into(),
        config,
    );
    let router = Router::new()
        .route_service("/mcp", service.clone())
        .route_service("/t/{token}/mcp", service)
        .layer(middleware::from_fn(require_token));
    let listener = tokio::net::TcpListener::bind((address, port)).await?;
    axum::serve(listener, router).await?;
    Ok(())
}
