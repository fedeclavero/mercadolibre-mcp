mod auth;
mod business;
mod client;
mod doctor;
mod guidance;
mod http;
mod images;
mod installer;
mod onboarding;
mod server;
mod state;
mod tools;
mod workflows;

use std::process::ExitCode;

use anyhow::Result;
use clap::{Args, Parser, Subcommand, ValueEnum};
use serde_json::{Value, json};

#[derive(Parser)]
#[command(
    version,
    about = "Servidor MCP de Mercado Libre y su instalación guiada (docs/AGENT_INSTALL.md)"
)]
struct Cli {
    #[command(subcommand)]
    command: Command,
}

#[derive(Subcommand)]
enum Command {
    /// Guarda las credenciales de la aplicación.
    Setup(onboarding::SetupArgs),
    /// Autoriza la cuenta mediante OAuth.
    #[command(visible_alias = "login")]
    Authorize(onboarding::AuthorizeArgs),
    /// Arranca el servidor MCP.
    Serve(ServeArgs),
    /// Consulta la cuenta conectada.
    Check,
    /// Diagnostica la instalación y muestra el próximo paso.
    Doctor(doctor::DoctorArgs),
    /// Muestra o cambia el modo de acceso.
    Mode(onboarding::ModeArgs),
    /// Registra el servidor en tus clientes MCP.
    Install(installer::InstallArgs),
    /// Guía para crear la aplicación en el DevCenter.
    Devcenter(onboarding::DevcenterArgs),
    /// Muestra o rota el token del transporte HTTP.
    HttpToken(http::TokenArgs),
}

#[derive(Clone, ValueEnum)]
enum Transport {
    Stdio,
    Http,
}

#[derive(Args)]
struct ServeArgs {
    #[arg(long, default_value = "stdio")]
    transport: Transport,
    #[arg(long, default_value = "127.0.0.1")]
    host: String,
    #[arg(long, default_value_t = 8765)]
    port: u16,
    #[arg(long = "allowed-host")]
    allowed_hosts: Vec<String>,
}

impl Command {
    fn json(&self) -> bool {
        match self {
            Self::Setup(args) => args.json,
            Self::Authorize(args) => args.json,
            Self::Doctor(args) => args.json,
            Self::Mode(args) => args.json,
            Self::Install(args) => args.json,
            Self::Devcenter(args) => args.json,
            Self::HttpToken(args) => args.json,
            Self::Check | Self::Serve(_) => false,
        }
    }

    async fn run(self) -> Result<ExitCode> {
        match self {
            Self::Setup(args) => args.run().await?,
            Self::Authorize(args) => args.run().await?,
            Self::Serve(args) => match args.transport {
                Transport::Stdio => server::Server::new(true)?.stdio().await?,
                Transport::Http => http::serve(&args.host, args.port, args.allowed_hosts).await?,
            },
            Self::Check => {
                let profile = client::Api::new().get("/users/me", Value::Null).await?;
                let nickname = profile["nickname"].as_str().unwrap_or("(sin nombre)");
                let id = profile
                    .get("id")
                    .map(Value::to_string)
                    .unwrap_or_else(|| "(desconocido)".into());
                println!("Conectado: usuario {nickname}, ID {id}.");
            }
            Self::Doctor(args) => return args.run().await,
            Self::Mode(args) => args.run().await?,
            Self::Install(args) => return args.run().await,
            Self::Devcenter(args) => args.run().await?,
            Self::HttpToken(args) => args.run()?,
        }
        Ok(ExitCode::SUCCESS)
    }
}

#[tokio::main]
async fn main() -> ExitCode {
    let command = Cli::parse().command;
    let as_json = command.json();
    match command.run().await {
        Ok(code) => code,
        Err(error) => {
            if as_json {
                println!("{}", json!({"ok":false,"error":error.to_string()}));
            } else {
                eprintln!("{error}");
            }
            ExitCode::FAILURE
        }
    }
}
