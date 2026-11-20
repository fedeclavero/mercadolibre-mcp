use std::io::Write;
use std::time::Duration;

use anyhow::{Context, Result, anyhow, bail};
use reqwest::{Client, Method, Response, Url, multipart};
use serde_json::{Value, json};
use time::{OffsetDateTime, format_description::well_known::Rfc3339};

use crate::state::{self, CLI};

pub const API_ORIGIN: &str = "https://api.mercadolibre.com";

const INVALID_PATH: &str = "Ruta inválida. Próximo paso: pasá los parámetros en `query` y no uses URLs ni rutas relativas.";
const NETWORK_ERROR: &str = "Falló la conexión a Mercado Libre. Próximo paso: si era una consulta, reintentá en unos segundos; si era una escritura, consultá primero el estado remoto (meli_get_item u otra tool de lectura) antes de reintentar.";
const AUDIT_WARNING: &str =
    "La operación respondió, pero no se pudo guardar en el registro de auditoría.";
const MAX_RESPONSE: usize = 16 * 1024 * 1024;

pub fn segment(value: &str) -> Result<&str> {
    if value.is_empty()
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || b"_-".contains(&byte))
    {
        bail!(
            "ID inválido: usá solo letras, números, guion o guion bajo (por ejemplo MLA123456789). Próximo paso: corregí el ID y reintentá."
        );
    }
    Ok(value)
}

pub fn checked_path(path: &str) -> Result<&str> {
    if !path.starts_with('/') || path.starts_with("//") {
        bail!(
            "La ruta debe empezar con / y no debe ser una URL (ejemplo: /users/me). Próximo paso: pasá solo la ruta."
        );
    }
    if path.bytes().any(|byte| b"%?#\\\x7f".contains(&byte))
        || path
            .split('/')
            .skip(1)
            .any(|part| part == "." || part == "..")
    {
        bail!("{INVALID_PATH}");
    }
    if path
        .trim_end_matches('/')
        .eq_ignore_ascii_case("/oauth/token")
    {
        bail!(
            "OAuth solo se administra con el comando `{CLI} authorize`. Próximo paso: ejecutá ese comando en la terminal."
        );
    }
    if path.chars().any(|ch| ch.is_control() || ch.is_whitespace()) {
        bail!("La ruta contiene caracteres inválidos. Próximo paso: corregí la ruta.");
    }
    // Keep endpoint and confirmation checks consistent with proxies that collapse slash pairs.
    if path.contains("//") {
        bail!("{INVALID_PATH}");
    }
    Ok(path)
}

pub fn http_client() -> Result<Client> {
    Client::builder()
        .https_only(true)
        .redirect(reqwest::redirect::Policy::none())
        .no_proxy()
        .timeout(Duration::from_secs(30))
        .build()
        .context("No se pudo iniciar la conexión segura a Mercado Libre")
}

#[derive(Clone)]
pub struct Api {
    client: std::result::Result<Client, String>,
    origin: String,
    #[cfg(test)]
    mock_token: Option<String>,
}

impl Api {
    pub fn new() -> Self {
        Self {
            client: http_client().map_err(|e| e.to_string()),
            origin: API_ORIGIN.into(),
            #[cfg(test)]
            mock_token: None,
        }
    }

    #[cfg(test)]
    pub(crate) fn with_origin(origin: &str) -> Self {
        Self {
            client: Client::builder()
                .redirect(reqwest::redirect::Policy::none())
                .no_proxy()
                .build()
                .map_err(|e| e.to_string()),
            origin: origin.into(),
            mock_token: None,
        }
    }

    #[cfg(test)]
    pub(crate) fn mock(origin: &str, token: &str) -> Self {
        Self {
            mock_token: Some(token.into()),
            ..Self::with_origin(origin)
        }
    }

    fn client(&self) -> Result<&Client> {
        self.client.as_ref().map_err(|e| anyhow!("{e}"))
    }

    pub async fn get(&self, path: &str, query: Value) -> Result<Value> {
        self.request("GET", path, query, Value::Null, Value::Null)
            .await
    }

    pub async fn request(
        &self,
        method: &str,
        path: &str,
        query: Value,
        body: Value,
        headers: Value,
    ) -> Result<Value> {
        self.send(method, path, query, body, headers, None).await
    }

    pub async fn upload(
        &self,
        path: &str,
        file_name: &str,
        mime: &str,
        bytes: Vec<u8>,
    ) -> Result<Value> {
        let part = multipart::Part::bytes(bytes)
            .file_name(file_name.to_owned())
            .mime_str(mime)?;
        self.send(
            "POST",
            path,
            Value::Null,
            Value::Null,
            Value::Null,
            Some(multipart::Form::new().part("file", part)),
        )
        .await
    }

    async fn access_token(&self, stale: Option<&str>) -> Result<String> {
        #[cfg(test)]
        if let Some(token) = &self.mock_token {
            return Ok(token.clone());
        }
        let _lock = state::lock("token").await?;
        let token = state::load_private("token.json")?;
        let expires = match token.get("expires_at") {
            None => Some(0.0),
            Some(value) => number(value),
        }.filter(|value| value.is_finite()).ok_or_else(|| anyhow!("El archivo token.json tiene una expiración inválida. Próximo paso: ejecutá `{CLI} authorize` para volver a autorizar."))?;
        if let Some(access) = text(&token["access_token"])
            && expires > state::now() + 60.0
            && stale != Some(access)
        {
            return Ok(access.to_owned());
        }
        let refresh = text(&token["refresh_token"]).ok_or_else(|| anyhow!("No hay refresh token. Próximo paso: en el DevCenter verificá que la app tenga el acceso offline (offline_access) y ejecutá `{CLI} authorize`."))?;
        let config = state::load_private("config.json")?;
        let client_id = config["client_id"]
            .as_str()
            .ok_or_else(|| anyhow!("Falta Client ID. Próximo paso: ejecutá `{CLI} setup`."))?;
        let secret = config["client_secret"]
            .as_str()
            .ok_or_else(|| anyhow!("Falta Client Secret. Próximo paso: ejecutá `{CLI} setup`."))?;
        let response = self.client()?.post(format!("{}/oauth/token", self.origin)).form(&[
            ("grant_type", "refresh_token"), ("client_id", client_id), ("client_secret", secret), ("refresh_token", refresh),
        ]).send().await.map_err(|_| anyhow!("No se pudo completar la renovación (error de red). El token podría haberse consumido. Próximo paso: reintentá en unos segundos; si persiste, ejecutá `{CLI} authorize`."))?;
        if !response.status().is_success() {
            bail!(
                "Renovación OAuth rechazada (HTTP {}). Próximo paso: ejecutá `{CLI} authorize` para volver a autorizar y `{CLI} doctor` para revisar la app.",
                response.status().as_u16()
            );
        }
        let incomplete =
            || anyhow!("Respuesta OAuth incompleta. Próximo paso: ejecutá `{CLI} authorize`.");
        let mut updated: Value = response.json().await.map_err(|_| incomplete())?;
        let access = text(&updated["access_token"])
            .ok_or_else(incomplete)?
            .to_owned();
        text(&updated["refresh_token"]).ok_or_else(incomplete)?;
        let ttl = number(&updated["expires_in"])
            .filter(|v| v.is_finite() && *v > 0.0)
            .ok_or_else(incomplete)?;
        updated["expires_at"] = json!(state::now() + ttl);
        state::write_private("token.json", &updated).context("No se pudo guardar el token renovado. Próximo paso: revisá los permisos del directorio y ejecutá authorize.")?;
        Ok(access)
    }

    async fn send(
        &self,
        method: &str,
        path: &str,
        query: Value,
        body: Value,
        headers: Value,
        form: Option<multipart::Form>,
    ) -> Result<Value> {
        let method = match method.to_ascii_uppercase().as_str() {
            "GET" => Method::GET,
            "POST" => Method::POST,
            "PUT" => Method::PUT,
            "PATCH" => Method::PATCH,
            "DELETE" => Method::DELETE,
            _ => bail!("Método no admitido. Próximo paso: usá GET, POST, PUT, PATCH o DELETE."),
        };
        let path = checked_path(path)?;
        #[cfg(test)]
        let simulated = self.mock_token.is_some();
        #[cfg(not(test))]
        let simulated = false;
        let url = request_url(&self.origin, path, &query)?;
        let headers = checked_headers(&headers)?;
        let current = self.access_token(None).await?;
        let request = |token: &str| {
            let request = self
                .client()?
                .request(method.clone(), url.clone())
                .bearer_auth(token)
                .header(reqwest::header::ACCEPT, "application/json")
                .headers(headers.clone());
            Ok::<_, anyhow::Error>(if body.is_null() {
                request
            } else {
                request.json(&body)
            })
        };
        let initial = request(&current)?;
        let sent = match form {
            Some(form) => initial.multipart(form).send().await,
            None => initial.send().await,
        };
        let sent = match sent {
            Ok(response)
                if response.status() == reqwest::StatusCode::UNAUTHORIZED
                    && method == Method::GET =>
            {
                let refreshed = self.access_token(Some(&current)).await?;
                request(&refreshed)?.send().await
            }
            result => result,
        };
        let response = sent.map_err(|_| {
            if !simulated {
                let _ = audit(method.as_str(), path, json!("network_error"));
            }
            anyhow!(NETWORK_ERROR)
        })?;
        let status = response.status().as_u16();
        let warning = !simulated && audit(method.as_str(), path, json!(status)).is_err();
        response_data(response, warning, path).await
    }
}

fn number(value: &Value) -> Option<f64> {
    value.as_f64().or_else(|| value.as_str()?.parse().ok())
}

fn text(value: &Value) -> Option<&str> {
    value.as_str().filter(|text| !text.is_empty())
}

fn request_url(origin: &str, path: &str, query: &Value) -> Result<Url> {
    let mut url = Url::parse(origin)?;
    url.set_path(path);
    if let Some(entries) = query.as_object() {
        for (key, value) in entries {
            if [
                "access_token",
                "client_secret",
                "refresh_token",
                "authorization",
            ]
            .iter()
            .any(|item| key.eq_ignore_ascii_case(item))
            {
                bail!(
                    "No envíes credenciales en los parámetros. Próximo paso: quitá access_token/client_secret de `query`; el servidor ya autentica solo."
                );
            }
            let items = value
                .as_array()
                .map_or(std::slice::from_ref(value), Vec::as_slice);
            for item in items {
                let value = match item {
                    Value::Null => String::new(),
                    Value::String(text) => text.clone(),
                    value => value.to_string(),
                };
                url.query_pairs_mut().append_pair(key, &value);
            }
        }
    }
    Ok(url)
}

fn checked_headers(value: &Value) -> Result<reqwest::header::HeaderMap> {
    use reqwest::header::{HeaderMap, HeaderName, HeaderValue};
    const ERROR: &str = "Solo se admiten las cabeceras x-version y x-format-new. Próximo paso: quitá las demás cabeceras.";
    let mut headers = HeaderMap::new();
    if let Some(entries) = value.as_object() {
        for (key, value) in entries {
            let key = key.to_ascii_lowercase();
            if !matches!(key.as_str(), "x-version" | "x-format-new") {
                bail!("{ERROR}");
            }
            let name = HeaderName::from_bytes(key.as_bytes())?;
            let value = value.as_str().ok_or_else(|| anyhow!(ERROR))?;
            headers.insert(
                name,
                HeaderValue::from_str(value).map_err(|_| anyhow!(ERROR))?,
            );
        }
    }
    Ok(headers)
}

async fn response_data(mut response: Response, warning: bool, path: &str) -> Result<Value> {
    let status = response.status().as_u16();
    let content_type = response
        .headers()
        .get("content-type")
        .and_then(|value| value.to_str().ok())
        .unwrap_or_default()
        .to_owned();
    let version = response
        .headers()
        .get("x-version")
        .and_then(|value| value.to_str().ok())
        .map(str::to_owned);
    let mut bytes = Vec::new();
    while let Some(chunk) = response.chunk().await.map_err(|_| anyhow!(NETWORK_ERROR))? {
        if bytes.len() + chunk.len() > MAX_RESPONSE {
            bail!(
                "La respuesta supera 16 MiB. Próximo paso: reducí el límite de paginación o pedí menos campos."
            );
        }
        bytes.extend_from_slice(&chunk);
    }
    let parsed = serde_json::from_slice::<Value>(&bytes).ok();
    if status >= 300 {
        let code = parsed
            .as_ref()
            .and_then(|value| value["error"].as_str())
            .filter(|code| {
                !code.is_empty()
                    && code.len() <= 100
                    && code
                        .bytes()
                        .all(|byte| byte.is_ascii_alphanumeric() || byte == b'_')
            })
            .unwrap_or_default();
        let explanation = match status {
            401 => format!("Token inválido o vencido. Próximo paso: ejecutá `{CLI} authorize` para volver a autorizar y reintentá."),
            403 => format!("Acceso denegado.{}", permission_hint(path)),
            404 => "Recurso inexistente o no accesible. Próximo paso: verificá el ID con una tool de lectura (meli_my_items, meli_get_item) y que pertenezca a la cuenta autorizada.".into(),
            429 => "Límite de solicitudes. Próximo paso: esperá unos segundos y reintentá más despacio.".into(),
            _ => "Próximo paso: revisá los datos enviados (meli_api_request POST /items/validate sirve para publicaciones) o consultá la documentación oficial del recurso.".into(),
        };
        let audit = if warning {
            format!(" {AUDIT_WARNING}")
        } else {
            String::new()
        };
        bail!("Mercado Libre respondió HTTP {status} {code}. {explanation}{audit}");
    }
    let data = if status == 204 || bytes.is_empty() {
        json!({"status": status})
    } else {
        parsed.unwrap_or_else(|| json!({"status": status, "content_type": content_type, "message": "Respuesta no JSON; usa un cliente adecuado para descargar este contenido."}))
    };
    if warning || version.is_some() {
        let mut result = json!({"data":data});
        if warning {
            result["warning"] = json!(AUDIT_WARNING);
        }
        if let Some(version) = version {
            result["response_headers"] = json!({"x-version": version});
        }
        return Ok(result);
    }
    Ok(data)
}

fn permission_hint(path: &str) -> String {
    let base = path.trim_matches('/').to_ascii_lowercase();
    let first = base.split('/').next().unwrap_or_default();
    let group = match first {
        "items" | "item" | "pictures" | "prices" | "categories" | "user-products" | "sites"
        | "domains" | "products" => "Publicación y sincronización",
        "questions" | "answers" | "messages" | "claims" | "post-purchase" | "returns" => {
            "Comunicación pre y postventa"
        }
        "orders" | "shipments" | "shipping" | "packs" | "payments" => "Ventas y envíos",
        "seller-promotions" | "promotions" | "deals" | "offers" => {
            "Promociones, cupones y descuentos"
        }
        "advertising" | "marketplace" => "Publicidad",
        "trends" | "highlights" | "visits" | "reputation" => "Métricas del negocio",
        "billing" | "invoices" => "Facturación",
        _ if base.starts_with("users/me/invoices") => "Facturación",
        _ => "el grupo de permisos correspondiente a este recurso",
    };
    format!(
        " Posible causa: falta el permiso \"{group}\" con nivel \"Lectura y escritura\" en la app. Próximo paso: en el DevCenter abrí la app, tildá \"{group}\" con \"Lectura y escritura\", guardá y ejecutá `{CLI} authorize` para volver a autorizar; si ya lo tenía, revisá que la cuenta sea la titular y no un colaborador."
    )
}

fn audit(method: &str, path: &str, status: Value) -> Result<()> {
    let path = path
        .split('/')
        .map(|part| {
            if part.chars().any(char::is_numeric) {
                "{id}"
            } else {
                part
            }
        })
        .collect::<Vec<_>>()
        .join("/");
    let time = OffsetDateTime::now_utc()
        .replace_nanosecond(0)?
        .format(&Rfc3339)?;
    let mut record = serde_json::to_vec(
        &json!({"time": time, "method": method, "path": path, "status": status}),
    )?;
    record.push(b'\n');
    let mut file = state::private_options()
        .append(true)
        .create(true)
        .open(state::ensure_state_dir()?.join("audit.jsonl"))?;
    file.write_all(&record)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use std::sync::{
        Arc,
        atomic::{AtomicUsize, Ordering},
    };

    use super::*;
    use axum::{
        Json, Router,
        body::Bytes,
        http::{HeaderMap, StatusCode},
        routing::{get, post},
    };

    #[tokio::test]
    async fn concurrent_processes_refresh_legacy_token_once() -> Result<()> {
        let state = tempfile::tempdir()?;
        std::fs::write(
            state.path().join("config.json"),
            r#"{"client_id":"42","client_secret":"local-fixture"}"#,
        )?;
        std::fs::write(
            state.path().join("token.json"),
            r#"{"access_token":"expired","refresh_token":"old-one-use","expires_at":0}"#,
        )?;

        let calls = Arc::new(AtomicUsize::new(0));
        let refreshes = calls.clone();
        let app = Router::new().route("/oauth/token", post(move |body: Bytes| {
            let refreshes = refreshes.clone();
            async move {
                let params: std::collections::HashMap<_, _> = url::form_urlencoded::parse(&body).into_owned().collect();
                assert_eq!(params.get("refresh_token").map(String::as_str), Some("old-one-use"));
                assert_eq!(params.get("client_id").map(String::as_str), Some("42"));
                assert_eq!(params.get("grant_type").map(String::as_str), Some("refresh_token"));
                refreshes.fetch_add(1, Ordering::SeqCst);
                tokio::time::sleep(Duration::from_millis(200)).await;
                Json(json!({"access_token":"new-access","refresh_token":"new-one-use","expires_in":3600}))
            }
        })).route("/users/me", get(|headers: HeaderMap| async move {
            if headers.get("Authorization").and_then(|header| header.to_str().ok()) != Some("Bearer new-access") {
                return (StatusCode::UNAUTHORIZED, Json(json!({})));
            }
            (StatusCode::OK, Json(json!({"id":42})))
        }));
        let listener = tokio::net::TcpListener::bind(("127.0.0.1", 0)).await?;
        let url = format!("http://{}", listener.local_addr()?);
        let service = tokio::spawn(async move { axum::serve(listener, app).await });

        let child = || -> Result<tokio::process::Child> {
            Ok(tokio::process::Command::new(std::env::current_exe()?)
                .args(["--exact", "client::tests::refresh_child", "--ignored"])
                .env("MELI_MCP_STATE_DIR", state.path())
                .env("MELI_REFRESH_TEST_FIXTURE", &url)
                .stdout(std::process::Stdio::piped())
                .stderr(std::process::Stdio::piped())
                .kill_on_drop(true)
                .spawn()?)
        };
        let (first, second) = (child()?, child()?);
        let (first, second) = tokio::time::timeout(Duration::from_secs(15), async {
            tokio::try_join!(first.wait_with_output(), second.wait_with_output())
        })
        .await??;
        service.abort();
        assert!(
            first.status.success(),
            "{}",
            String::from_utf8_lossy(&first.stdout)
        );
        assert!(
            second.status.success(),
            "{}",
            String::from_utf8_lossy(&second.stdout)
        );
        assert_eq!(calls.load(Ordering::SeqCst), 1);
        let token: Value =
            serde_json::from_slice(&std::fs::read(state.path().join("token.json"))?)?;
        assert_eq!(token["refresh_token"], "new-one-use");
        Ok(())
    }

    #[tokio::test]
    #[ignore = "se ejecuta como proceso aislado de la prueba de renovación"]
    async fn refresh_child() -> Result<()> {
        let Some(url) = std::env::var_os("MELI_REFRESH_TEST_FIXTURE") else {
            return Ok(());
        };
        let url = url.to_str().context("fixture URL UTF-8")?;
        assert_eq!(
            Api::with_origin(url).get("/users/me", Value::Null).await?["id"],
            42
        );
        Ok(())
    }
}
