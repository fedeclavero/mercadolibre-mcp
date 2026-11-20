use std::{
    io,
    net::{IpAddr, Ipv4Addr, SocketAddr},
    sync::{Arc, Mutex},
    time::Duration,
};

use anyhow::{Context, Result, anyhow, bail};
use base64::{Engine as _, engine::general_purpose::URL_SAFE_NO_PAD};
use http_body_util::Full;
use hyper::{
    Request, Response, StatusCode,
    body::{Bytes, Incoming},
    header,
    server::conn::http1,
    service::service_fn,
};
use hyper_util::rt::TokioIo;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use subtle::ConstantTimeEq;
use tokio::{net::TcpListener, sync::mpsc, task::JoinSet, time::timeout};
use tokio_rustls::{
    TlsAcceptor,
    rustls::{ServerConfig, crypto::ring, pki_types::PrivatePkcs8KeyDer},
};
use url::{Host, Url};

use crate::{
    client::{API_ORIGIN, http_client},
    state::{CLI, ensure_state_dir, load_private, lock, now, write_private},
};

pub const DEFAULT_REDIRECT: &str = "https://localhost:8765/callback";
pub const COUNTRIES: &str = "ar/br/mx/cl/co/uy/pe/ec/ve";
pub const PENDING_TTL: u64 = 900;
const REQUIRED_SCOPES: [&str; 3] = ["read", "write", "offline_access"];

pub(crate) fn oauth_scopes(token: &Value) -> Option<Vec<String>> {
    let mut scopes: Vec<_> = match &token["scope"] {
        Value::String(scope) => scope
            .split(|c: char| c == ',' || c.is_whitespace())
            .filter(|scope| !scope.is_empty())
            .map(str::to_owned)
            .collect(),
        Value::Array(scopes) => scopes
            .iter()
            .filter_map(Value::as_str)
            .map(str::to_owned)
            .collect(),
        _ => return None,
    };
    scopes.sort_unstable();
    scopes.dedup();
    Some(scopes)
}

pub(crate) fn missing_scopes(scopes: &[String]) -> Vec<&'static str> {
    REQUIRED_SCOPES
        .into_iter()
        .filter(|required| !scopes.iter().any(|scope| scope == required))
        .collect()
}

const PENDING_FILE: &str = "pending-auth.json";
const BAD_REDIRECT: &str = "La URL de redirección debe ser HTTPS, exacta y sin usuario ni fragmento (el DevCenter exige HTTPS). Próximo paso: usá `https://localhost:8765/callback` o la que registraste en la app.";
const BAD_CALLBACK: &str = "La URL de retorno no corresponde a la configurada. Próximo paso: copiá la URL completa que quedó en la barra del navegador después de autorizar (empieza con la URL de redirección).";
const BAD_STATE: &str = "No se recibió un código válido o el state no coincide. Próximo paso: ejecutá `mercadolibre-mcp authorize` otra vez y usá la URL de esa vuelta.";
const DENIED: &str = "Mercado Libre no concedió el permiso. Próximo paso: ejecutá `mercadolibre-mcp authorize` otra vez y aprobá el acceso con la cuenta titular.";
const CHANGED_CONFIG: &str = "La configuración cambió durante la autorización. Próximo paso: ejecutá `mercadolibre-mcp authorize` otra vez.";
const EXCHANGE_FAILED: &str =
    "Falló el intercambio OAuth. Próximo paso: ejecutá `mercadolibre-mcp authorize` otra vez.";

fn country_domain(country: &str) -> Option<&'static str> {
    Some(match country {
        "ar" => "mercadolibre.com.ar",
        "br" => "mercadolivre.com.br",
        "mx" => "mercadolibre.com.mx",
        "cl" => "mercadolibre.cl",
        "co" => "mercadolibre.com.co",
        "uy" => "mercadolibre.com.uy",
        "pe" => "mercadolibre.com.pe",
        "ec" => "mercadolibre.com.ec",
        "ve" => "mercadolibre.com.ve",
        _ => return None,
    })
}

pub fn parse_country(value: &str) -> Result<String, String> {
    if country_domain(value).is_some() {
        Ok(value.to_owned())
    } else {
        Err(format!(
            "País inválido. Próximo paso: usá uno de {COUNTRIES}."
        ))
    }
}

pub fn devcenter_url(country: &str) -> Result<String> {
    let domain = country_domain(country)
        .ok_or_else(|| anyhow!("País inválido. Próximo paso: usá uno de {COUNTRIES}."))?;
    Ok(format!("https://developers.{domain}/devcenter/"))
}

pub fn validate_redirect(redirect: &str) -> Result<Url> {
    let uri = Url::parse(redirect).map_err(|_| anyhow!(BAD_REDIRECT))?;
    let authority = redirect
        .get(8..)
        .and_then(|tail| tail.split(['/', '?', '#']).next());
    if !redirect
        .get(..8)
        .is_some_and(|prefix| prefix.eq_ignore_ascii_case("https://"))
        || authority.is_none_or(|value| value.is_empty() || value.contains('@'))
        || redirect
            .chars()
            .any(|character| character.is_whitespace() || character.is_control())
        || redirect.contains('\\')
        || uri.host().is_none()
        || uri.fragment().is_some()
    {
        bail!(BAD_REDIRECT);
    }
    Ok(uri)
}

fn loopback_address(uri: &Url) -> Option<IpAddr> {
    match uri.host() {
        Some(Host::Domain("localhost")) => Some(Ipv4Addr::LOCALHOST.into()),
        Some(Host::Ipv4(ip)) if ip.is_loopback() => Some(ip.into()),
        Some(Host::Ipv6(ip)) if ip.is_loopback() => Some(ip.into()),
        _ => None,
    }
}

pub fn is_loopback(uri: &Url) -> bool {
    loopback_address(uri).is_some()
}

fn pkce_challenge(verifier: &str) -> String {
    URL_SAFE_NO_PAD.encode(Sha256::digest(verifier.as_bytes()))
}

pub fn random_token<const N: usize>() -> Result<String> {
    let mut bytes = [0u8; N];
    getrandom::fill(&mut bytes).map_err(|_| {
        anyhow!("No se pudo generar un valor aleatorio seguro. Próximo paso: reintentá.")
    })?;
    Ok(URL_SAFE_NO_PAD.encode(bytes))
}

#[derive(Clone)]
pub struct Attempt {
    state: String,
    verifier: String,
}

impl Attempt {
    pub fn new() -> Result<Self> {
        Ok(Self {
            state: random_token::<32>()?,
            verifier: random_token::<64>()?,
        })
    }

    pub fn url(&self, config: &Value) -> Result<String> {
        let country = config_text(config, "country")?;
        let domain = country_domain(country).ok_or_else(|| {
            anyhow!("País de la app inválido. Próximo paso: ejecutá `mercadolibre-mcp setup`.")
        })?;
        let redirect = config_text(config, "redirect_uri")?;
        let mut url = Url::parse(&format!("https://auth.{domain}/authorization"))?;
        {
            let mut query = url.query_pairs_mut();
            query
                .append_pair("response_type", "code")
                .append_pair("client_id", config_text(config, "client_id")?)
                .append_pair("redirect_uri", redirect)
                .append_pair("state", &self.state);
            if config["pkce"].as_bool().unwrap_or(false) {
                query
                    .append_pair("code_challenge", &pkce_challenge(&self.verifier))
                    .append_pair("code_challenge_method", "S256");
            }
        }
        Ok(url.into())
    }

    pub async fn save(&self, config: &Value) -> Result<()> {
        let _lock = lock("token").await?;
        if load_private("config.json")? != *config {
            bail!(CHANGED_CONFIG);
        }
        write_private(
            PENDING_FILE,
            &json!({
                "state": self.state,
                "verifier": self.verifier,
                "client_id": config["client_id"],
                "redirect_uri": config["redirect_uri"],
                "created_at": now(),
            }),
        )
    }

    pub async fn load(config: &Value) -> Result<Self> {
        let _lock = lock("token").await?;
        let pending = load_private(PENDING_FILE).map_err(|_| anyhow!("No hay una autorización iniciada. Próximo paso: ejecutá `mercadolibre-mcp authorize --start` y luego `authorize --finish`."))?;
        let state = pending["state"].as_str().filter(|value| !value.is_empty());
        let verifier = pending["verifier"]
            .as_str()
            .filter(|value| !value.is_empty());
        let valid_time = pending["created_at"]
            .as_f64()
            .or_else(|| pending["created_at"].as_str()?.parse().ok())
            .is_some_and(|created| created.is_finite() && now() - created <= PENDING_TTL as f64);
        if let (Some(state), Some(verifier)) = (state, verifier)
            && valid_time
            && pending["client_id"] == config["client_id"]
            && pending["redirect_uri"] == config["redirect_uri"]
        {
            return Ok(Self {
                state: state.to_owned(),
                verifier: verifier.to_owned(),
            });
        }

        clear_pending()?;
        bail!(
            "La autorización iniciada venció o es de otra configuración. Próximo paso: ejecutá `mercadolibre-mcp authorize --start` otra vez."
        );
    }

    pub fn code(&self, callback: &str, redirect: &Url, allow_bare_code: bool) -> Result<String> {
        let value = callback.trim();
        if allow_bare_code && !value.contains("://") && !value.contains("code=") {
            if value.is_empty()
                || value
                    .chars()
                    .any(|character| character.is_whitespace() || character.is_control())
            {
                bail!(
                    "El código es inválido. Próximo paso: pegá la URL completa de la barra de direcciones."
                );
            }
            return Ok(value.to_owned());
        }
        let actual = validate_redirect(value).map_err(|_| anyhow!(BAD_CALLBACK))?;
        if actual.origin() != redirect.origin() || actual.path() != redirect.path() {
            bail!(BAD_CALLBACK);
        }
        self.parse_callback(&actual)?.ok_or_else(|| anyhow!(DENIED))
    }

    /// Ok(None) represents a denial for this OAuth attempt. Invalid callbacks return an error.
    fn parse_callback(&self, actual: &Url) -> Result<Option<String>> {
        let mut code = None;
        let mut state = None;
        let mut denial = None;
        for (key, value) in actual.query_pairs() {
            match key.as_ref() {
                "code" if code.is_none() => code = Some(value),
                "state" if state.is_none() => state = Some(value),
                "error" if denial.is_none() => denial = Some(value),
                "code" | "state" | "error" => bail!(BAD_STATE),
                _ => {}
            }
        }
        if !state.is_some_and(|value| value.as_bytes().ct_eq(self.state.as_bytes()).into()) {
            bail!(BAD_STATE);
        }
        match (code, denial) {
            (Some(code), None) if !code.is_empty() => Ok(Some(code.into_owned())),
            (None, Some(denial)) if !denial.is_empty() => Ok(None),
            _ => bail!(BAD_STATE),
        }
    }

    pub async fn exchange(&self, config: &Value, code: &str) -> Result<()> {
        let client = http_client().map_err(|_| anyhow!(EXCHANGE_FAILED))?;
        self.exchange_with(config, code, client, &format!("{API_ORIGIN}/oauth/token"))
            .await
    }

    async fn exchange_with(
        &self,
        config: &Value,
        code: &str,
        client: reqwest::Client,
        endpoint: &str,
    ) -> Result<()> {
        let redirect = config_text(config, "redirect_uri")?;
        let mut payload = vec![
            ("grant_type", "authorization_code"),
            ("client_id", config_text(config, "client_id")?),
            ("client_secret", config_text(config, "client_secret")?),
            ("redirect_uri", redirect),
            ("code", code),
        ];
        if config["pkce"].as_bool().unwrap_or(false) {
            payload.push(("code_verifier", &self.verifier));
        }
        let _lock = lock("token").await?;
        if load_private("config.json")? != *config {
            bail!(CHANGED_CONFIG);
        }
        let pending = load_private(PENDING_FILE).unwrap_or_default();
        if pending["state"] != self.state || pending["verifier"] != self.verifier {
            bail!(
                "El intento de autorización cambió. Próximo paso: usá la última URL de retorno o ejecutá `mercadolibre-mcp authorize --start` de nuevo."
            );
        }

        let response = client
            .post(endpoint)
            .form(&payload)
            .send()
            .await
            .map_err(|_| anyhow!(EXCHANGE_FAILED))?;
        if !response.status().is_success() {
            bail!(
                "Mercado Libre rechazó la autorización (HTTP {}). Próximo paso: verificá que la URL de redirección de la app sea exactamente {redirect}, que el APP ID y el Secret sean los de esa app y que hayas entrado con la cuenta principal; después ejecutá `mercadolibre-mcp authorize` otra vez.",
                response.status().as_u16()
            );
        }
        let mut token: Value = response
            .json()
            .await
            .map_err(|_| anyhow!(EXCHANGE_FAILED))?;
        if ["access_token", "refresh_token"]
            .into_iter()
            .any(|name| token[name].as_str().is_none_or(str::is_empty))
        {
            bail!(
                "No se recibió refresh token. Próximo paso: en el DevCenter habilitá el acceso offline (offline_access) de la app y ejecutá `mercadolibre-mcp authorize` otra vez."
            );
        }
        let expires_in = token["expires_in"]
            .as_f64()
            .or_else(|| token["expires_in"].as_str()?.parse().ok())
            .filter(|seconds| seconds.is_finite() && *seconds > 0.0)
            .ok_or_else(|| anyhow!(EXCHANGE_FAILED))?;
        token["expires_at"] = json!(now() + expires_in);
        if let Some(scopes) = oauth_scopes(&token) {
            let missing = missing_scopes(&scopes);
            if !missing.is_empty() {
                bail!(
                    "Mercado Libre no concedió estos permisos: {}. Próximo paso: habilitá todos los permisos funcionales en el DevCenter y ejecutá `{CLI} authorize` otra vez.",
                    missing.join(", ")
                );
            }
        }
        write_private("token.json", &token)?;
        clear_pending()
    }
}

pub fn config_text<'a>(config: &'a Value, field: &str) -> Result<&'a str> {
    config[field].as_str().filter(|value| !value.is_empty()).ok_or_else(|| {
        anyhow!("Falta {field} en la configuración. Próximo paso: ejecutá `mercadolibre-mcp setup` otra vez.")
    })
}

pub fn remove_state_file(name: &str) -> Result<bool> {
    match std::fs::remove_file(ensure_state_dir()?.join(name)) {
        Ok(()) => Ok(true),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(false),
        Err(error) => Err(error).context("No se pudo limpiar el estado de autorización. Próximo paso: ejecutá `mercadolibre-mcp doctor`."),
    }
}

fn clear_pending() -> Result<()> {
    remove_state_file(PENDING_FILE)?;
    Ok(())
}

const HTML_START: &str = "<!doctype html><meta charset='utf-8'><title>Mercado Libre MCP</title><body style='font-family:sans-serif;max-width:32em;margin:4em auto'>";
const RECEIVED_PAGE: &str =
    "<h2>Permiso recibido</h2><p>Volvé al asistente para comprobar el resultado.</p></body>";
const ERROR_PAGE: &str = "<h2>No se pudo leer la autorización</h2><p>Volvé al asistente y comprobá la URL que abriste.</p></body>";

pub struct CallbackServer {
    listener: TcpListener,
    acceptor: TlsAcceptor,
    redirect: Url,
}

impl CallbackServer {
    pub async fn bind(redirect: Url) -> Result<Self> {
        let address = loopback_address(&redirect).ok_or_else(|| {
            anyhow!("Esa URL de redirección no apunta a este equipo. Próximo paso: usá `authorize --start` y `authorize --finish --callback-url <URL>`.")
        })?;
        let port = redirect.port_or_known_default().unwrap_or(443);
        let listener = TcpListener::bind(SocketAddr::new(address, port)).await.map_err(|_| {
            anyhow!("No se pudo abrir el puerto {port} en este equipo (¿está ocupado?). Próximo paso: cerrá lo que lo usa o registrá en la app otra URL de redirección con otro puerto y ejecutá `mercadolibre-mcp setup` con ella.")
        })?;
        // Generate the certificate in memory so no system keychain or temporary private key is involved.
        let host = redirect
            .host_str()
            .unwrap_or("localhost")
            .trim_matches(['[', ']'])
            .to_owned();
        let identity = rcgen::generate_simple_self_signed(vec![host])?;
        let key = PrivatePkcs8KeyDer::from(identity.signing_key.serialize_der());
        let tls = ServerConfig::builder_with_provider(Arc::new(ring::default_provider()))
            .with_safe_default_protocol_versions()?
            .with_no_client_auth()
            .with_single_cert(vec![identity.cert.der().clone()], key.into())?;
        Ok(Self {
            listener,
            acceptor: TlsAcceptor::from(Arc::new(tls)),
            redirect,
        })
    }

    pub async fn receive(self, attempt: Attempt, wait: Duration) -> Result<String> {
        match timeout(wait, self.accept(attempt)).await {
            Ok(result) => result,
            Err(_) => bail!(
                "Pasaron {} segundos sin recibir la autorización. Próximo paso: ejecutá `mercadolibre-mcp authorize` otra vez y completá el permiso en el navegador; si el navegador no puede abrir la URL local, usá `authorize --start` y `--finish`.",
                wait.as_secs()
            ),
        }
    }

    async fn accept(self, attempt: Attempt) -> Result<String> {
        let (sender, mut incoming) = mpsc::channel(1);
        let mut connections = JoinSet::new();
        loop {
            tokio::select! {
                Some(result) = incoming.recv() => return result,
                Some(_) = connections.join_next(), if !connections.is_empty() => {},
                connection = self.listener.accept(), if connections.len() < 8 => {
                    let (stream, _) = connection?;
                    let acceptor = self.acceptor.clone();
                    let redirect = self.redirect.clone();
                    let attempt = attempt.clone();
                    let sender = sender.clone();
                    connections.spawn(async move {
                        let Ok(Ok(tls)) = timeout(Duration::from_secs(5), acceptor.accept(stream)).await else {
                            return;
                        };
                        let captured = Mutex::new(None);
                        let service = service_fn(|request| {
                            let (response, result) = callback_response(&request, &redirect, &attempt);
                            if let Some(result) = result && let Ok(mut captured) = captured.lock() {
                                *captured = Some(result);
                            }
                            async move { Ok::<_, std::convert::Infallible>(response) }
                        });
                        let _ = timeout(Duration::from_secs(5), http1::Builder::new()
                            .keep_alive(false)
                            .serve_connection(TokioIo::new(tls), service)).await;
                        if let Ok(Some(result)) = captured.into_inner() {
                            let _ = sender.try_send(result);
                        }
                    });
                }
            }
        }
    }
}

fn callback_response(
    request: &Request<Incoming>,
    redirect: &Url,
    attempt: &Attempt,
) -> (Response<Full<Bytes>>, Option<Result<String>>) {
    let expected_host = &redirect[url::Position::BeforeHost..url::Position::AfterPort];
    let valid_host = request
        .headers()
        .get(header::HOST)
        .and_then(|value| value.to_str().ok())
        .is_some_and(|value| value.eq_ignore_ascii_case(expected_host));
    if request.method() != hyper::Method::GET
        || request.uri().path() != redirect.path()
        || !valid_host
    {
        let mut response = Response::new(Full::new(Bytes::new()));
        *response.status_mut() = StatusCode::NOT_FOUND;
        return (response, None);
    }
    let mut callback = redirect.clone();
    callback.set_query(request.uri().query());
    let (status, page, result) = match attempt.parse_callback(&callback) {
        Ok(Some(code)) => (StatusCode::OK, RECEIVED_PAGE, Some(Ok(code))),
        Ok(None) => (StatusCode::OK, ERROR_PAGE, Some(Err(anyhow!(DENIED)))),
        Err(_) => (StatusCode::BAD_REQUEST, ERROR_PAGE, None),
    };
    let mut response = Response::new(Full::new(Bytes::from(format!("{HTML_START}{page}"))));
    *response.status_mut() = status;
    response.headers_mut().insert(
        header::CONTENT_TYPE,
        header::HeaderValue::from_static("text/html; charset=utf-8"),
    );
    response.headers_mut().insert(
        header::CACHE_CONTROL,
        header::HeaderValue::from_static("no-store"),
    );
    response.headers_mut().insert(
        header::REFERRER_POLICY,
        header::HeaderValue::from_static("no-referrer"),
    );
    (response, result)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn defaults_and_pkce_match_existing_oauth() -> Result<()> {
        assert_eq!(
            pkce_challenge("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"),
            "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
        );
        let config = json!({
            "client_id": "123",
            "client_secret": "dummy-secret",
            "redirect_uri": DEFAULT_REDIRECT,
            "country": "br",
            "pkce": true
        });
        let attempt = Attempt::new()?;
        let address = attempt.url(&config)?;
        let url = Url::parse(&address)?;
        let query = url
            .query_pairs()
            .into_owned()
            .collect::<std::collections::HashMap<_, _>>();
        assert_eq!(url.host_str(), Some("auth.mercadolivre.com.br"));
        assert_eq!(query["redirect_uri"], DEFAULT_REDIRECT);
        assert_eq!(query["code_challenge_method"], "S256");
        assert!(!address.contains("dummy-secret"));
        assert!(!address.contains(&attempt.verifier));
        Ok(())
    }

    #[test]
    fn validates_callback_origin_path_and_state() -> Result<()> {
        let attempt = Attempt::new()?;
        let redirect = validate_redirect(DEFAULT_REDIRECT)?;
        for invalid in [
            "nada",
            "http://localhost:8765/callback",
            "https://user:secret@example.org/callback",
            "https://@example.org/callback",
            "https:///example.org/callback",
            "https://example.org/callback#fragment",
            "https://example.org/a b",
        ] {
            assert!(validate_redirect(invalid).is_err());
        }
        for invalid in [
            format!("{redirect}?code=x&state=wrong"),
            format!("{redirect}?code=x&state={0}&state={0}", attempt.state),
            format!("{redirect}?code=x&code=y&state={}", attempt.state),
            format!("{redirect}?state={}", attempt.state),
            format!(
                "https://example.org/callback?code=x&state={}",
                attempt.state
            ),
            format!("https://localhost:8765/otro?code=x&state={}", attempt.state),
        ] {
            assert!(
                attempt.code(&invalid, &redirect, false).is_err(),
                "{invalid}"
            );
        }
        assert_eq!(
            attempt.code(
                &format!("{redirect}?code=foo%2Bbar&state={}", attempt.state),
                &redirect,
                false
            )?,
            "foo+bar"
        );
        assert_eq!(attempt.code("TG-bare", &redirect, true)?, "TG-bare");
        assert!(attempt.code("TG-bare", &redirect, false).is_err());
        let denied = format!("{redirect}?error=access_denied&state={}", attempt.state);
        let result = attempt.code(&denied, &redirect, false);
        assert!(result.is_err_and(|error| error.to_string().contains("no concedió el permiso")));

        Ok(())
    }

    #[tokio::test]
    async fn exchanges_code_once_and_preserves_authorization_state() -> Result<()> {
        let state_dir = tempfile::tempdir()?;
        let listener = TcpListener::bind("127.0.0.1:0").await?;
        let endpoint = format!("http://{}/oauth/token", listener.local_addr()?);
        let fixture = tokio::spawn(async move {
            let (stream, _) = timeout(Duration::from_secs(10), listener.accept()).await??;
            let service = service_fn(|request: Request<Incoming>| async move {
                use http_body_util::BodyExt as _;
                assert_eq!(request.method(), hyper::Method::POST);
                assert_eq!(request.uri().path(), "/oauth/token");
                let body = request.into_body().collect().await?.to_bytes();
                let form = url::form_urlencoded::parse(&body)
                    .into_owned()
                    .collect::<std::collections::HashMap<_, _>>();
                assert_eq!(form["grant_type"], "authorization_code");
                assert_eq!(form["client_id"], "123");
                assert_eq!(form["client_secret"], "fixture-secret");
                assert_eq!(form["redirect_uri"], DEFAULT_REDIRECT);
                assert_eq!(form["code"], "fixture-code");
                assert_eq!(form["code_verifier"], "fixture-verifier");
                let mut response = Response::new(Full::new(Bytes::from_static(
                    br#"{"access_token":"fixture-access","refresh_token":"fixture-refresh","expires_in":3600}"#,
                )));
                response.headers_mut().insert(
                    header::CONTENT_TYPE,
                    header::HeaderValue::from_static("application/json"),
                );
                Ok::<_, hyper::Error>(response)
            });
            http1::Builder::new()
                .keep_alive(false)
                .serve_connection(TokioIo::new(stream), service)
                .await?;
            Ok::<(), anyhow::Error>(())
        });

        let report = tokio::process::Command::new(std::env::current_exe()?)
            .args(["--exact", "auth::tests::mock_exchange_child", "--ignored"])
            .env("MELI_MCP_STATE_DIR", state_dir.path())
            .env("MELI_OAUTH_TEST_ENDPOINT", endpoint)
            .kill_on_drop(true)
            .output();
        let report = timeout(Duration::from_secs(15), report).await??;
        assert!(
            report.status.success(),
            "nested OAuth test failed: {} {}",
            String::from_utf8_lossy(&report.stdout),
            String::from_utf8_lossy(&report.stderr)
        );
        fixture.await??;
        Ok(())
    }

    #[tokio::test]
    #[ignore = "se ejecuta en el proceso aislado de la prueba OAuth"]
    async fn mock_exchange_child() -> Result<()> {
        let Ok(endpoint) = std::env::var("MELI_OAUTH_TEST_ENDPOINT") else {
            return Ok(());
        };
        let config = json!({
            "client_id": "123", "client_secret": "fixture-secret",
            "redirect_uri": DEFAULT_REDIRECT, "country": "ar", "pkce": true
        });
        write_private("config.json", &config)?;
        let attempt = Attempt {
            state: "fixture-state".into(),
            verifier: "fixture-verifier".into(),
        };
        attempt.save(&config).await?;
        let client = reqwest::Client::builder().no_proxy().build()?;
        let stale = Attempt::new()?;
        let error = stale
            .exchange_with(&config, "unused", client.clone(), &endpoint)
            .await;
        assert!(
            error.is_err_and(|error| error.to_string().contains("intento de autorización cambió"))
        );
        attempt
            .exchange_with(&config, "fixture-code", client, &endpoint)
            .await?;
        let token = load_private("token.json")?;
        assert_eq!(token["access_token"], "fixture-access");
        assert_eq!(token["refresh_token"], "fixture-refresh");
        assert!(
            token["expires_at"]
                .as_f64()
                .is_some_and(|value| value > now())
        );
        assert!(load_private(PENDING_FILE).is_err());
        Ok(())
    }

    #[tokio::test]
    async fn captures_oauth_from_loopback_https_without_leaking_code() -> Result<()> {
        let mut server =
            CallbackServer::bind(validate_redirect("https://127.0.0.1:0/callback")?).await?;
        server
            .redirect
            .set_port(Some(server.listener.local_addr()?.port()))
            .map_err(|()| anyhow!("Puerto de prueba inválido."))?;
        let redirect = server.redirect.clone();
        let attempt = Attempt::new()?;
        let state = attempt.state.clone();
        let listener = tokio::spawn(server.receive(attempt, Duration::from_secs(10)));
        let http = reqwest::Client::builder()
            .tls_danger_accept_invalid_certs(true)
            .no_proxy()
            .timeout(Duration::from_secs(5))
            .build()?;

        let wrong_path = http.get(redirect.join("otra-ruta")?).send().await?;
        assert_eq!(wrong_path.status(), StatusCode::NOT_FOUND);
        let wrong_state = http
            .get(format!("{redirect}?state=wrong&code=codigo-descartado"))
            .send()
            .await?;
        assert_eq!(wrong_state.status(), StatusCode::BAD_REQUEST);
        let response = http
            .get(format!("{redirect}?state={state}&code=codigo-privado"))
            .send()
            .await?;
        assert_eq!(response.status(), StatusCode::OK);
        assert_eq!(response.headers()[header::CACHE_CONTROL], "no-store");
        assert!(!response.text().await?.contains("codigo-privado"));
        assert_eq!(listener.await??, "codigo-privado");
        Ok(())
    }
}
