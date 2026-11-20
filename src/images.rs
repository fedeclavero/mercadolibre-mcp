use crate::client::Api;
use anyhow::{Context, Result, anyhow, bail};
use base64::{Engine as _, engine::general_purpose::STANDARD};
use image::{ImageFormat, ImageReader, Limits};
use reqwest::{Url, header};
use serde_json::{Value, json};
use std::{
    io::Cursor,
    net::{IpAddr, SocketAddr},
    path::{Path, PathBuf},
    time::Duration,
};
use tokio::io::AsyncReadExt;

const MAX_BYTES: usize = 10 * 1024 * 1024;
const MIN_SIDE: u32 = 500;
const RECOMMENDED_SIDE: u32 = 1200;
const MAX_SIDE: u32 = 1920;
const UPLOAD_PATH: &str = "/pictures/items/upload";
const TOO_LARGE: &str = "La imagen supera 10 MB. Próximo paso: reducila a menos de 10 MB.";
const PUBLIC_URL_REQUIRED: &str = "La URL de la imagen debe ser HTTPS pública. Próximo paso: pasá una URL https:// o usá `file_path`/`base64`.";
const PUBLIC_ADDRESS_REQUIRED: &str = "La URL de la imagen apunta a una dirección no pública y se rechazó por seguridad. Próximo paso: usá una URL pública o un archivo local (`file_path`).";
const DOWNLOAD_FAILED: &str =
    "Falló la descarga de la imagen. Próximo paso: reintentá o usá `file_path`.";

pub(crate) const PICTURE_RULES: &[&str] = &[
    "Formato JPG, JPEG o PNG; hasta 10 MB; mínimo 500x500 px; recomendado 1200x1200 px (cuadrada); máximo 1920x1920 px (más grande se reduce).",
    "Portada: producto o kit completo sobre fondo blanco puro (RGB 255,255,255), centrado y ocupando cerca del 80% del cuadro (la guía operativa indica 80%; la documentación oficial habla de ocupar el 95% del espacio).",
    "Sin sombras proyectadas, bordes, logos, marcas de agua ni textos superpuestos.",
    "Usá fotos reales de los productos del vendedor; no inventes renders de productos que no existen ni uses fotos de terceros. Para un kit, mostrá todos los componentes y cantidades exactas que incluye.",
    "Galería: la guía recomienda 10 o más fotos con ángulos, escala y contenido del paquete; para un kit nuevo alcanza con una buena portada y se agregan más después con meli_set_item_pictures.",
];

struct ImageInfo {
    format: ImageFormat,
    width: u32,
    height: u32,
    size: usize,
}

impl ImageInfo {
    fn inspect(data: &[u8]) -> Result<Self> {
        let format = match image::guess_format(data) {
            Ok(format @ (ImageFormat::Png | ImageFormat::Jpeg)) => format,
            _ => bail!(
                "El archivo no es una imagen JPG ni PNG (Mercado Libre solo acepta JPG, JPEG y PNG). Próximo paso: convertí la imagen a PNG o JPG y reintentá."
            ),
        };
        let mut limits = Limits::default();
        limits.max_image_width = Some(8192);
        limits.max_image_height = Some(8192);
        limits.max_alloc = Some(64 * 1024 * 1024);
        let mut reader = ImageReader::with_format(Cursor::new(data), format);
        reader.limits(limits);
        let image = reader.decode().map_err(|_| {
            anyhow!(
                "La imagen JPG o PNG está dañada o excede el tamaño de decodificación. Próximo paso: guardala nuevamente en 1200x1200 px."
            )
        })?;
        Ok(Self {
            format,
            width: image.width(),
            height: image.height(),
            size: data.len(),
        })
    }

    fn warnings(&self) -> Result<Vec<String>> {
        let mut warnings = Vec::new();
        let Self { width, height, .. } = *self;
        if width.min(height) < MIN_SIDE {
            bail!(
                "La imagen no cumple las reglas de Mercado Libre: Mide {width}x{height} px y el mínimo es {MIN_SIDE}x{MIN_SIDE}. Próximo paso: generá la imagen de nuevo (cuadrada, 1200x1200 px, JPG o PNG) y reintentá."
            );
        }
        if width != height {
            warnings.push(format!(
                "No es cuadrada ({width}x{height}); para la portada usá una imagen cuadrada de {RECOMMENDED_SIDE}x{RECOMMENDED_SIDE}."
            ));
        } else if width < RECOMMENDED_SIDE {
            warnings.push(format!(
                "Mide {width}x{height}; lo recomendado es {RECOMMENDED_SIDE}x{RECOMMENDED_SIDE}."
            ));
        }
        if width.max(height) > MAX_SIDE {
            warnings.push(format!(
                "Supera {MAX_SIDE}x{MAX_SIDE}; Mercado Libre la va a reducir."
            ));
        }
        Ok(warnings)
    }

    fn filename_and_media_type(&self, name: &str) -> (String, &'static str, &'static str) {
        let stem: String = Path::new(name)
            .file_stem()
            .and_then(|name| name.to_str())
            .unwrap_or("imagen")
            .chars()
            .take(60)
            .map(|character| {
                if character.is_alphanumeric() || matches!(character, '-' | '_') {
                    character
                } else {
                    '_'
                }
            })
            .collect();
        let stem = if stem.is_empty() { "imagen" } else { &stem };
        let (extension, format, mime) = if self.format == ImageFormat::Png {
            ("png", "png", "image/png")
        } else {
            ("jpg", "jpeg", "image/jpeg")
        };
        (format!("{stem}.{extension}"), format, mime)
    }
}

fn public_ip(address: IpAddr) -> bool {
    match address.to_canonical() {
        IpAddr::V4(address) => {
            let [first, second, third, _] = address.octets();
            !(address.is_private()
                || address.is_loopback()
                || address.is_link_local()
                || address.is_documentation()
                || first == 0
                || first >= 224
                || first == 100 && (64..=127).contains(&second)
                || first == 192 && (second, third) == (0, 0)
                || first == 192 && (second, third) == (88, 99)
                || first == 198 && matches!(second, 18 | 19))
        }
        IpAddr::V6(address) => {
            let [first, second, ..] = address.segments();
            (0x2000..0x4000).contains(&first)
                && !(first == 0x2001 && (second < 0x0200 || second == 0x0db8))
                && first != 0x2002
                && !(first == 0x3fff && second < 0x1000)
        }
    }
}

fn image_url(text: &str) -> Result<Url> {
    let url = Url::parse(text).map_err(|_| anyhow!(PUBLIC_URL_REQUIRED))?;
    if url.scheme() != "https"
        || !url.has_host()
        || !url.username().is_empty()
        || url.password().is_some()
    {
        bail!(PUBLIC_URL_REQUIRED);
    }
    Ok(url)
}

async fn download(mut url: Url) -> Result<Vec<u8>> {
    for _ in 0..4 {
        let hostname = url.host_str().context(PUBLIC_URL_REQUIRED)?;
        let port = url.port().unwrap_or(443);
        let address = hostname.trim_matches(['[', ']']).parse::<IpAddr>();
        let addresses: Vec<SocketAddr> = match address {
            Ok(address) => vec![SocketAddr::new(address, port)],
            Err(_) => tokio::net::lookup_host((hostname, port))
                .await
                .map_err(|_| {
                    anyhow!(
                        "No se pudo resolver el dominio de la imagen. Próximo paso: revisá la URL o guardá la imagen en un archivo local y usá `file_path`."
                    )
                })?
                .collect(),
        };
        if addresses.is_empty() || addresses.iter().any(|address| !public_ip(address.ip())) {
            bail!(PUBLIC_ADDRESS_REQUIRED);
        }
        let mut builder = reqwest::Client::builder()
            .https_only(true)
            .no_proxy()
            .redirect(reqwest::redirect::Policy::none())
            .timeout(Duration::from_secs(30));
        if address.is_err() {
            // Keep certificate validation against the host while connecting only to the verified IPs.
            builder = builder.resolve_to_addrs(hostname, &addresses);
        }
        let mut response = builder
            .build()
            .map_err(|_| anyhow!(DOWNLOAD_FAILED))?
            .get(url.clone())
            .header(header::ACCEPT, "image/png,image/jpeg")
            .send()
            .await
            .map_err(|_| anyhow!(DOWNLOAD_FAILED))?;
        let status = response.status();
        if matches!(status.as_u16(), 301 | 302 | 303 | 307 | 308)
            && let Some(location) = response.headers().get(header::LOCATION)
        {
            let location = location
                .to_str()
                .map_err(|_| anyhow!(PUBLIC_URL_REQUIRED))?;
            url = image_url(
                url.join(location)
                    .map_err(|_| anyhow!(PUBLIC_URL_REQUIRED))?
                    .as_str(),
            )?;
            continue;
        }
        if status != reqwest::StatusCode::OK {
            bail!(
                "No se pudo descargar la imagen (HTTP {}). Próximo paso: verificá que la URL abra la imagen directamente o usá `file_path`.",
                status.as_u16()
            );
        }
        if response
            .content_length()
            .is_some_and(|size| size > MAX_BYTES as u64)
        {
            bail!(TOO_LARGE);
        }
        let mut bytes = Vec::new();
        while let Some(chunk) = response
            .chunk()
            .await
            .map_err(|_| anyhow!(DOWNLOAD_FAILED))?
        {
            if bytes.len() + chunk.len() > MAX_BYTES {
                bail!(TOO_LARGE);
            }
            bytes.extend_from_slice(&chunk);
        }
        return Ok(bytes);
    }
    bail!(
        "La URL de la imagen redirige demasiadas veces. Próximo paso: pasá la URL final de la imagen."
    )
}

async fn read_file(input: &str) -> Result<(Vec<u8>, String)> {
    let path = if let Some(subpath) = input.strip_prefix("~/") {
        dirs::home_dir()
            .context("No se pudo localizar la carpeta personal.")?
            .join(subpath)
    } else {
        PathBuf::from(input)
    };
    let path = tokio::fs::canonicalize(path).await.with_context(|| {
        format!("No existe el archivo {input}. Próximo paso: verificá la ruta absoluta de la imagen que generaste.")
    })?;
    let metadata = tokio::fs::metadata(&path).await?;
    if !metadata.is_file() {
        bail!("{input} no es un archivo. Próximo paso: pasá la ruta de la imagen.");
    }
    if metadata.len() > MAX_BYTES as u64 {
        bail!(TOO_LARGE);
    }
    let file = tokio::fs::File::open(&path).await?;
    let mut bytes = Vec::new();
    file.take(MAX_BYTES as u64 + 1)
        .read_to_end(&mut bytes)
        .await?;
    let filename = path
        .file_name()
        .and_then(|name| name.to_str())
        .unwrap_or("imagen")
        .to_string();
    Ok((bytes, filename))
}

fn decode_base64(input: &str) -> Result<Vec<u8>> {
    let text = input.trim();
    let data = if text.starts_with("data:") {
        text.split_once(',').map_or("", |(_, encoded)| encoded)
    } else {
        text
    };
    let encoded: Vec<u8> = data
        .bytes()
        .filter(|byte| !byte.is_ascii_whitespace())
        .take(MAX_BYTES * 4 / 3 + 17)
        .collect();
    if encoded.len() > MAX_BYTES * 4 / 3 + 16 {
        bail!(TOO_LARGE);
    }
    STANDARD.decode(encoded).map_err(|_| {
        anyhow!(
            "El base64 de la imagen es inválido. Próximo paso: pasá el contenido base64 sin saltos extraños o usá `file_path`."
        )
    })
}

pub(crate) async fn upload(api: &Api, source: &Value, local_files: bool) -> Result<Value> {
    let source = source
        .as_object()
        .context("`image` debe ser un objeto con una sola clave: file_path, base64 o url.")?;
    let accepted = ["file_path", "base64", "url"];
    let mut sources = accepted.into_iter().filter_map(|kind| {
        let value = source.get(kind)?;
        (!value.is_null() && value.as_str() != Some("")).then_some((kind, value))
    });
    let choice = sources.next().filter(|_| sources.next().is_none());
    let Some((kind, value)) = choice.filter(|_| {
        source
            .keys()
            .all(|key| key == "filename" || accepted.contains(&key.as_str()))
    }) else {
        bail!(
            "`image` debe tener exactamente una clave entre file_path, base64 y url. Ejemplo: {{\"file_path\": \"/ruta/combo.png\"}}."
        );
    };
    let value = value
        .as_str()
        .with_context(|| format!("`image.{kind}` debe ser texto."))?;
    if kind == "file_path" && !local_files {
        bail!(
            "Las rutas locales están deshabilitadas para MCP por HTTP. Próximo paso: usá base64 o una URL HTTPS pública."
        );
    }
    let (data, original_name) = match kind {
        "file_path" => read_file(value).await?,
        "url" => {
            let url = image_url(value)?;
            let name = url
                .path_segments()
                .and_then(|mut segments| segments.next_back())
                .filter(|name| !name.is_empty())
                .unwrap_or("imagen")
                .to_string();
            let data = tokio::time::timeout(Duration::from_secs(30), download(url))
                .await
                .map_err(|_| anyhow!(DOWNLOAD_FAILED))??;
            (data, name)
        }
        _ => (decode_base64(value)?, "imagen".into()),
    };
    if data.len() > MAX_BYTES {
        bail!(TOO_LARGE);
    }
    let (info, data) = tokio::task::spawn_blocking(move || -> Result<_> {
        Ok((ImageInfo::inspect(&data)?, data))
    })
    .await??;
    let warnings = info.warnings()?;
    let selected_name = source
        .get("filename")
        .and_then(Value::as_str)
        .filter(|name| !name.is_empty())
        .unwrap_or(&original_name);
    let (filename, format, mime) = info.filename_and_media_type(selected_name);
    let response = api.upload(UPLOAD_PATH, &filename, mime, data).await?;
    let payload = response.get("data").unwrap_or(&response);
    let picture_id = payload.get("id").filter(|id| {
        id.as_str().is_some_and(|id| !id.is_empty()) || id.as_i64().is_some_and(|id| id != 0)
    }).context(
        "Mercado Libre no devolvió el id de la imagen. Próximo paso: reintentá; si persiste, ejecutá `mercadolibre-mcp doctor`.",
    )?;
    let secure_url = payload["variations"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(|variation| variation["secure_url"].as_str())
        .find(|url| !url.is_empty());
    Ok(json!({
        "picture_id": picture_id,
        "secure_url": secure_url,
        "image": {"format": format, "width": info.width, "height": info.height, "bytes": info.size},
        "warnings": warnings,
        "uploaded": true,
    }))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn image_hosts_must_resolve_to_global_unicast() {
        for address in [
            "0.0.0.0",
            "127.0.0.1",
            "100.64.0.1",
            "169.254.169.254",
            "192.0.2.1",
            "198.18.1.1",
            "224.0.1.1",
            "::1",
            "::ffff:127.0.0.1",
            "fc00::1",
            "fe80::1",
            "ff02::1",
            "2001:db8::1",
            "2002:a00::1",
        ] {
            assert!(!public_ip(address.parse().unwrap()), "{address}");
        }
        assert!(public_ip("1.1.1.1".parse().unwrap()));
        assert!(public_ip("2606:4700:4700::1111".parse().unwrap()));
        assert!(image_url("http://example.com/image").is_err());
        assert!(image_url("https://user@example.com/image").is_err());
    }

    #[test]
    fn image_requires_real_picture_data() -> Result<()> {
        let mut data = Cursor::new(Vec::new());
        image::DynamicImage::new_rgb8(600, 600).write_to(&mut data, ImageFormat::Png)?;
        let data = data.into_inner();
        let info = ImageInfo::inspect(&data)?;
        assert_eq!(info.width, 600);
        assert_eq!(info.height, 600);
        assert_eq!(info.warnings()?.len(), 1);
        assert!(ImageInfo::inspect(&data[..24]).is_err());
        Ok(())
    }

    #[tokio::test]
    async fn http_uploads_cannot_read_local_files() {
        let api = Api::mock("http://127.0.0.1:1", "fake");
        let error = upload(&api, &json!({"file_path": "/no-existe.png"}), false)
            .await
            .unwrap_err();
        assert!(
            error
                .to_string()
                .contains("deshabilitadas para MCP por HTTP")
        );
    }
    #[tokio::test]
    async fn picture_upload_uses_a_multipart_file_and_authorization() -> Result<()> {
        use axum::{Json, Router, body::Bytes, http::HeaderMap, routing::post};
        let server = Router::new().route(
            UPLOAD_PATH,
            post(|headers: HeaderMap, body: Bytes| async move {
                assert_eq!(headers[header::AUTHORIZATION], "Bearer fake");
                assert!(
                    headers[header::CONTENT_TYPE]
                        .to_str()
                        .unwrap()
                        .starts_with("multipart/form-data; boundary=")
                );
                for part in [
                    &b"name=\"file\""[..],
                    &b"filename=\"combo.png\""[..],
                    b"image/png",
                    b"\x89PNG",
                ] {
                    assert!(body.windows(part.len()).any(|bytes| bytes == part));
                }
                Json(json!({
                    "id": "fixture-picture",
                    "variations": [{"secure_url": "https://example.invalid/result.png"}]
                }))
            }),
        );
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await?;
        let api = Api::mock(&format!("http://{}", listener.local_addr()?), "fake");
        let worker = tokio::spawn(async move { axum::serve(listener, server).await });
        let mut bytes = Cursor::new(Vec::new());
        image::DynamicImage::new_rgb8(520, 520).write_to(&mut bytes, ImageFormat::Png)?;
        let data = STANDARD.encode(bytes.into_inner());
        let result = upload(
            &api,
            &json!({"base64": data, "filename": "combo.jpg"}),
            false,
        )
        .await?;
        assert_eq!(result["picture_id"], "fixture-picture");
        assert_eq!(result["secure_url"], "https://example.invalid/result.png");
        assert_eq!(result["image"]["width"], 520);
        assert_eq!(result["uploaded"], true);
        worker.abort();
        Ok(())
    }
}
