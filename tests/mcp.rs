use std::process::{Child, Command, Stdio};
use std::time::Duration;

use reqwest::{Client, StatusCode};
use serde_json::{Value, json};

const BINARY: &str = env!("CARGO_BIN_EXE_mercadolibre-mcp");
const TOKEN: &str = "synthetic-initial-token-abcdefghijklmnopqrstuvwxyz";
const VERSION: &str = "2025-03-26";

struct RunningServer(Child);

impl Drop for RunningServer {
    fn drop(&mut self) {
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}

async fn rpc(response: reqwest::Response) -> anyhow::Result<Value> {
    let response = response.error_for_status()?;
    let is_event_stream = response
        .headers()
        .get("content-type")
        .is_some_and(|value| value.as_bytes().starts_with(b"text/event-stream"));
    if !is_event_stream {
        return Ok(response.json().await?);
    }
    let raw = response.text().await?;
    let data = raw
        .lines()
        .find_map(|line| line.strip_prefix("data: ").filter(|body| !body.is_empty()))
        .ok_or_else(|| anyhow::anyhow!("RPC data missing in response"))?;
    Ok(serde_json::from_str(data)?)
}

#[tokio::test]
async fn streamable_http_can_discover_and_call_mcp_and_revokes_rotated_tokens() -> anyhow::Result<()>
{
    let state = tempfile::tempdir()?;
    std::fs::write(state.path().join("http-token"), TOKEN)?;
    let port = std::net::TcpListener::bind("127.0.0.1:0")?
        .local_addr()?
        .port();
    let _server = RunningServer(
        Command::new(BINARY)
            .env("MELI_MCP_STATE_DIR", state.path())
            .env("MELI_MCP_MODE", "read-only")
            .args(["serve", "--transport", "http", "--port", &port.to_string()])
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()?,
    );
    let client = Client::builder()
        .no_proxy()
        .timeout(Duration::from_secs(6))
        .build()?;
    let endpoint = format!("http://127.0.0.1:{port}/mcp");
    let mut status = None;
    for _ in 0..150 {
        if let Ok(response) = client.get(&endpoint).send().await {
            status = Some(response.status());
            break;
        }
        tokio::time::sleep(Duration::from_millis(40)).await;
    }
    assert_eq!(status, Some(StatusCode::UNAUTHORIZED));
    assert_eq!(
        client
            .get(&endpoint)
            .bearer_auth(TOKEN)
            .header("Origin", "https://unrelated.invalid")
            .send()
            .await?
            .status(),
        StatusCode::FORBIDDEN
    );
    assert_eq!(
        client
            .get(&endpoint)
            .bearer_auth(TOKEN)
            .header("Host", "unrelated.invalid")
            .send()
            .await?
            .status(),
        StatusCode::FORBIDDEN
    );

    let protected = format!("http://127.0.0.1:{port}/t/{TOKEN}/mcp");
    let response = client.post(&protected)
        .header("Accept", "application/json, text/event-stream")
        .json(&json!({
            "jsonrpc":"2.0","id":1,"method":"initialize","params":{
                "protocolVersion":VERSION,"capabilities":{},"clientInfo":{"name":"test","version":"1"}
            }
        })).send().await?;
    let session = response
        .headers()
        .get("mcp-session-id")
        .cloned()
        .ok_or_else(|| anyhow::anyhow!("session id missing"))?;
    assert_eq!(
        rpc(response).await?["result"]["serverInfo"]["name"],
        "mercadolibre_mcp"
    );
    let request = |data: Value| {
        client
            .post(&protected)
            .header("Accept", "application/json, text/event-stream")
            .header("Mcp-Session-Id", session.clone())
            .header("Mcp-Protocol-Version", VERSION)
            .json(&data)
    };
    let initialized = request(json!({"jsonrpc":"2.0","method":"notifications/initialized"}))
        .send()
        .await?;
    assert_eq!(initialized.status(), StatusCode::ACCEPTED);
    let list = rpc(
        request(json!({"jsonrpc":"2.0","id":2,"method":"tools/list"}))
            .send()
            .await?,
    )
    .await?;
    assert_eq!(list["result"]["tools"].as_array().map(Vec::len), Some(48));
    let start = rpc(request(json!({"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"meli_start_here","arguments":{}}})).send().await?).await?;
    assert!(
        start["result"]["structuredContent"]["recetas"]
            .get("combos")
            .is_some()
    );

    for (name, arguments) in [
        (
            "meli_save_business_policy",
            json!({"topic":"envíos","rule_text":"test"}),
        ),
        (
            "meli_save_product_rule",
            json!({"item_id":"MLA1","minimum_advertised_price":"100","max_discount_pct":"10"}),
        ),
        (
            "meli_clear_question_attempt",
            json!({"question_id":42,"explicitly_requested":true}),
        ),
    ] {
        let response = rpc(request(json!({"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":name,"arguments":arguments}})).send().await?).await?;
        assert_eq!(response["result"]["isError"], true);
        assert!(
            response["result"]["content"][0]["text"]
                .as_str()
                .unwrap_or_default()
                .contains("SOLO LECTURA")
        );
    }
    assert_eq!(
        client
            .delete(&protected)
            .header("Mcp-Session-Id", session)
            .header("Mcp-Protocol-Version", VERSION)
            .send()
            .await?
            .status(),
        StatusCode::OK
    );

    let rotated = Command::new(BINARY)
        .env("MELI_MCP_STATE_DIR", state.path())
        .args(["http-token", "--rotate", "--json"])
        .output()?;
    assert!(rotated.status.success());
    let result: Value = serde_json::from_slice(&rotated.stdout)?;
    assert_ne!(result["token"], TOKEN);
    assert_eq!(
        client.get(&protected).send().await?.status(),
        StatusCode::UNAUTHORIZED
    );
    assert_eq!(
        client
            .get(&endpoint)
            .bearer_auth(TOKEN)
            .send()
            .await?
            .status(),
        StatusCode::UNAUTHORIZED
    );
    Ok(())
}
