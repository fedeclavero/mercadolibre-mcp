use std::{
    fs,
    path::Path,
    process::{Command, Output},
};

use serde_json::{Value, json};

const BINARY: &str = env!("CARGO_BIN_EXE_mercadolibre-mcp");

fn read_report(output: &Output) -> Value {
    serde_json::from_slice(&output.stdout).unwrap_or_else(|error| {
        panic!(
            "{error}\nstdout: {}\nstderr: {}",
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        )
    })
}

fn install(project: &Path, command: Option<&Path>) -> Value {
    let mut binary = Command::new(BINARY);
    binary
        .env("MELI_MCP_STATE_DIR", project.join("state"))
        .args(["install", "--client", "cursor", "--scope", "project"])
        .arg("--project-dir")
        .arg(project)
        .arg("--json");
    if let Some(command) = command {
        binary.arg("--command").arg(command);
    }
    let output = binary.output().expect("run the compiled installer");
    let report = read_report(&output);
    assert_eq!(output.status.success(), command.is_none(), "{report}");
    report
}

#[test]
fn installer_registers_the_native_binary_and_verifies_it_over_stdio() {
    let project = tempfile::tempdir().expect("create isolated MCP client config");
    let initial = install(project.path(), None);
    assert_eq!(initial["launch_check"]["has_start_here"], true);
    assert!(
        initial["launch_check"]["tools"]
            .as_u64()
            .unwrap_or_default()
            >= 48
    );
    assert_eq!(initial["installed"][0]["status"], "created");
    assert_eq!(initial["installed"][0]["verified"], true);

    let again = install(project.path(), None);
    assert_eq!(again["installed"][0]["status"], "unchanged");
    let config: Value = serde_json::from_slice(
        &fs::read(project.path().join(".cursor/mcp.json")).expect("read client config"),
    )
    .expect("parse client config");
    assert_eq!(
        config["mcpServers"]["mercadolibre"]["args"],
        json!(["serve"])
    );
    let command = config["mcpServers"]["mercadolibre"]["command"]
        .as_str()
        .unwrap();
    assert!(Path::new(command).is_absolute());
}

#[test]
fn installer_does_not_write_client_configuration_when_handshake_fails() {
    let project = tempfile::tempdir().expect("create isolated MCP client config");
    let missing = project.path().join("missing-mcp");
    let result = install(project.path(), Some(&missing));
    assert_eq!(result["launch_check"]["ok"], false);
    assert_eq!(result["problems"][0]["status"], "error");
    assert!(!project.path().join(".cursor/mcp.json").exists());
}

#[test]
fn offline_doctor_accepts_read_only_scopes_and_does_not_print_credentials() {
    let state = tempfile::tempdir().expect("create isolated state");
    let secret = "isolated-test-secret-should-not-be-printed";
    fs::write(
        state.path().join("config.json"),
        json!({
            "client_id": "1234", "client_secret": secret,
            "redirect_uri": "https://localhost:8765/callback", "country": "ar",
            "pkce": true, "mode": "read_only"
        })
        .to_string(),
    )
    .expect("write dummy configuration");
    fs::write(
        state.path().join("token.json"),
        json!({
            "access_token": "isolated-access-token",
            "refresh_token": "isolated-refresh-token",
            "expires_at": 0, "scope": "read offline_access"
        })
        .to_string(),
    )
    .expect("write dummy authorization");

    let output = Command::new(BINARY)
        .env("MELI_MCP_STATE_DIR", state.path())
        .env_remove("MELI_MCP_MODE")
        .args(["doctor", "--offline", "--json"])
        .output()
        .expect("run the compiled doctor");
    let report = read_report(&output);
    assert!(output.status.success(), "{report}");
    let checks = report["checks"].as_array().expect("checks in report");
    assert!(
        checks
            .iter()
            .any(|check| check["check"] == "permisos" && check["ok"] == true)
    );
    assert!(!checks.iter().any(|check| check["check"] == "api"));
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(!stdout.contains(secret));
    assert!(!stdout.contains("isolated-access-token"));
    assert!(!stdout.contains("isolated-refresh-token"));
}
