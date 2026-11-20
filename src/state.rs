use std::fs::{self, File, OpenOptions};
use std::io::{ErrorKind, Write};
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use anyhow::{Context, Result, bail};
use fs2::FileExt;
use serde_json::Value;

pub const CLI: &str = "mercadolibre-mcp";

pub fn now() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs_f64()
}

pub fn state_dir() -> PathBuf {
    if let Some(path) = std::env::var_os("MELI_MCP_STATE_DIR") {
        let path = PathBuf::from(path);
        if let Ok(relative) = path.strip_prefix("~")
            && let Some(home) = dirs::home_dir()
        {
            return home.join(relative);
        }
        return path;
    }
    let home = dirs::home_dir().unwrap_or_else(|| PathBuf::from("."));
    let base = if cfg!(windows) {
        std::env::var_os("LOCALAPPDATA").map_or(home, PathBuf::from)
    } else {
        home.join(".local/share")
    };
    base.join(CLI)
}

pub fn ensure_state_dir() -> Result<PathBuf> {
    let path = state_dir();
    #[cfg(unix)]
    {
        use std::os::unix::fs::DirBuilderExt;
        fs::DirBuilder::new()
            .mode(0o700)
            .recursive(true)
            .create(&path)?;
    }
    #[cfg(not(unix))]
    fs::create_dir_all(&path)?;
    if fs::symlink_metadata(&path)?.file_type().is_symlink() {
        bail!(
            "El directorio de credenciales no puede ser un enlace simbólico. Próximo paso: borrá ese enlace o elegí otra carpeta con la variable MELI_MCP_STATE_DIR."
        );
    }
    restrict(&path, 0o700)?;
    Ok(path)
}

pub fn restrict(path: &Path, mode: u32) -> Result<()> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(path, fs::Permissions::from_mode(mode))?;
    }
    #[cfg(not(unix))]
    let _ = (path, mode);
    Ok(())
}

pub fn private_options() -> OpenOptions {
    let mut options = OpenOptions::new();
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600).custom_flags(libc::O_NOFOLLOW);
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::OpenOptionsExt;
        options.custom_flags(0x00200000); // FILE_FLAG_OPEN_REPARSE_POINT
    }
    options
}

pub fn read_bytes(name: &str) -> std::io::Result<Vec<u8>> {
    let path = state_dir().join(name);
    let mut file = private_options().read(true).open(path)?;
    if !file.metadata()?.is_file() {
        return Err(std::io::Error::new(
            ErrorKind::InvalidData,
            "archivo de estado inválido",
        ));
    }
    let mut bytes = vec![];
    std::io::Read::read_to_end(&mut file, &mut bytes)?;
    Ok(bytes)
}

pub fn write_bytes(name: &str, data: &[u8]) -> Result<()> {
    let dir = ensure_state_dir()?;
    let mut file = tempfile::NamedTempFile::new_in(&dir)?;
    restrict(file.path(), 0o600)?;
    file.write_all(data)?;
    file.as_file().sync_all()?;
    file.persist(dir.join(name))?;
    #[cfg(unix)]
    File::open(dir)?.sync_all()?;
    Ok(())
}

pub fn load_private(name: &str) -> Result<Value> {
    let raw = match read_bytes(name) {
        Ok(raw) => raw,
        Err(e) if e.kind() == ErrorKind::NotFound => bail!(
            "Falta la configuración o autorización de Mercado Libre ({name}). Próximo paso: ejecutá `{CLI} doctor` para ver qué falta; normalmente es `{CLI} setup` y luego `{CLI} authorize` (guía: docs/AGENT_INSTALL.md)."
        ),
        Err(_) => bail!(
            "No se pudo leer {name}. Próximo paso: ejecutá `{CLI} doctor`; si el archivo está dañado, volvé a correr `{CLI} setup` y `{CLI} authorize`."
        ),
    };
    let value: Value = serde_json::from_slice(&raw).map_err(|_| anyhow::anyhow!("No se pudo leer {name}. Próximo paso: ejecutá `{CLI} doctor`; si el archivo está dañado, volvé a correr `{CLI} setup` y `{CLI} authorize`."))?;
    if !value.is_object() {
        bail!("El archivo {name} es inválido. Próximo paso: ejecutá `{CLI} doctor`.");
    }
    Ok(value)
}

pub fn write_private(name: &str, value: &Value) -> Result<()> {
    write_bytes(name, &serde_json::to_vec(value)?)
}

pub fn access_mode() -> &'static str {
    match std::env::var("MELI_MCP_MODE")
        .unwrap_or_default()
        .trim()
        .to_ascii_lowercase()
        .replace('-', "_")
        .as_str()
    {
        "read_only" | "readonly" | "ro" => return "read_only",
        "full" | "read_write" | "rw" => return "full",
        _ => {}
    }
    let config = load_private("config.json").unwrap_or_default();
    match config["mode"]
        .as_str()
        .unwrap_or("full")
        .trim()
        .to_ascii_lowercase()
        .replace('-', "_")
        .as_str()
    {
        "read_only" | "readonly" => "read_only",
        _ => "full",
    }
}

pub fn operation_lock(name: &str) -> Result<File> {
    let path = ensure_state_dir()?.join(format!("{name}.lock"));
    let file = private_options()
        .read(true)
        .write(true)
        .create(true)
        .open(path)?;
    let deadline = Instant::now() + Duration::from_secs(30);
    loop {
        match FileExt::try_lock_exclusive(&file) {
            Ok(()) => return Ok(file),
            Err(error) if error.raw_os_error() == fs2::lock_contended_error().raw_os_error() => {
                if Instant::now() >= deadline {
                    bail!(
                        "No se pudo adquirir el bloqueo en 30 segundos. Próximo paso: cerrá otros procesos de mercadolibre-mcp y reintentá."
                    );
                }
                std::thread::sleep(Duration::from_millis(50));
            }
            Err(error) => {
                return Err(error).context("No se pudo bloquear el estado de Mercado Libre");
            }
        }
    }
}

pub async fn lock(name: &'static str) -> Result<File> {
    tokio::task::spawn_blocking(move || operation_lock(name)).await?
}
