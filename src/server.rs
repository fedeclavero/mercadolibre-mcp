use std::collections::HashMap;
use std::sync::Arc;

use anyhow::{Context, Result, anyhow};
use base64::{Engine, engine::general_purpose::STANDARD};
use rmcp::{ErrorData, RoleServer, ServerHandler, ServiceExt, model::*, service::RequestContext};
use serde::Deserialize;
use serde_json::{Value, json};

use crate::{client::Api, guidance, tools};

#[derive(Deserialize)]
struct Contract {
    instructions: String,
    tools: Vec<Tool>,
    resources: Vec<Resource>,
    resource_templates: Vec<ResourceTemplate>,
    prompts: Vec<Prompt>,
}

#[derive(Clone)]
pub struct Server {
    contract: Arc<Contract>,
    inputs: Arc<HashMap<String, jsonschema::Validator>>,
    api: Api,
    local_files: bool,
}

impl Server {
    pub fn new(local_files: bool) -> Result<Self> {
        let contract: Contract = serde_json::from_str(include_str!("data/mcp_contract.json"))
            .context("Contrato MCP inválido")?;
        let inputs = contract
            .tools
            .iter()
            .map(|tool| {
                let schema = Value::Object((*tool.input_schema).clone());
                let validator = jsonschema::validator_for(&schema)
                    .map_err(|error| anyhow!("Esquema inválido para {}: {error}", tool.name))?;
                Ok((tool.name.to_string(), validator))
            })
            .collect::<Result<HashMap<_, _>>>()?;
        Ok(Self {
            contract: Arc::new(contract),
            inputs: Arc::new(inputs),
            api: Api::new(),
            local_files,
        })
    }

    pub async fn stdio(self) -> Result<()> {
        self.serve(rmcp::transport::stdio())
            .await?
            .waiting()
            .await?;
        Ok(())
    }

    fn arguments(&self, name: &str, args: Option<JsonObject>) -> Result<Value> {
        let schema = self.inputs.get(name).ok_or_else(|| {
            anyhow!("Herramienta desconocida. Próximo paso: llamá meli_start_here.")
        })?;
        let mut args = Value::Object(args.unwrap_or_default());
        if let Err(error) = schema.validate(&args) {
            let path = error.instance_path().to_string();
            let field = if path.is_empty() {
                "del objeto de entrada"
            } else {
                &path
            };
            return Err(anyhow!(
                "Argumentos inválidos ({field}). Próximo paso: revisá los campos requeridos y sus tipos en la descripción de la herramienta."
            ));
        }
        if let Some(tool) = self.contract.tools.iter().find(|tool| tool.name == name)
            && let Some(props) = tool
                .input_schema
                .get("properties")
                .and_then(Value::as_object)
            && let Some(args) = args.as_object_mut()
        {
            for (key, property) in props {
                if let Some(default) = property.get("default") {
                    args.entry(key).or_insert_with(|| default.clone());
                }
            }
        }
        Ok(args)
    }

    async fn invoke(&self, name: &str, args: Option<JsonObject>) -> Result<CallToolResult> {
        let args = self.arguments(name, args)?;
        if name == "meli_guia_imagen" {
            let number = args["numero"]
                .as_i64()
                .ok_or_else(|| anyhow!("La imagen indicada no existe en la guía."))?;
            let bytes = guidance::guide_image(number)?;
            return Ok(CallToolResult::success(vec![ContentBlock::image(
                STANDARD.encode(bytes),
                "image/png",
            )]));
        }
        let result = tools::call(&self.api, name, &args, self.local_files).await?;
        if self
            .contract
            .tools
            .iter()
            .any(|tool| tool.name == name && tool.output_schema.is_some())
        {
            return Ok(CallToolResult::structured(result));
        }
        let content = match result {
            Value::Null => Vec::new(),
            Value::Array(list) => list.into_iter().map(json_content).collect(),
            data => vec![json_content(data)],
        };
        Ok(CallToolResult::success(content))
    }
}

fn json_content(value: Value) -> ContentBlock {
    let text = match value {
        Value::String(text) => text,
        data => serde_json::to_string_pretty(&data).unwrap_or_else(|_| data.to_string()),
    };
    ContentBlock::text(text)
}

impl ServerHandler for Server {
    fn get_info(&self) -> ServerConfig {
        ServerConfig::new(
            ServerCapabilities::builder()
                .enable_tools()
                .enable_resources()
                .enable_prompts()
                .build(),
        )
        .with_server_info(Implementation::new(
            "mercadolibre_mcp",
            env!("CARGO_PKG_VERSION"),
        ))
        .with_instructions(self.contract.instructions.clone())
    }

    async fn list_tools(
        &self,
        _: Option<PaginatedRequestParams>,
        _: RequestContext<RoleServer>,
    ) -> Result<ListToolsResult, ErrorData> {
        Ok(ListToolsResult::with_all_items(self.contract.tools.clone()))
    }

    async fn call_tool(
        &self,
        request: CallToolRequestParams,
        _: RequestContext<RoleServer>,
    ) -> Result<CallToolResponse, ErrorData> {
        let result = match self.invoke(&request.name, request.arguments).await {
            Ok(result) => result,
            Err(error) => CallToolResult::error(vec![ContentBlock::text(format!(
                "Error executing tool {}: {error}",
                request.name
            ))]),
        };
        Ok(result.into())
    }

    async fn list_resources(
        &self,
        _: Option<PaginatedRequestParams>,
        _: RequestContext<RoleServer>,
    ) -> Result<ListResourcesResult, ErrorData> {
        Ok(ListResourcesResult::with_all_items(
            self.contract.resources.clone(),
        ))
    }

    async fn list_resource_templates(
        &self,
        _: Option<PaginatedRequestParams>,
        _: RequestContext<RoleServer>,
    ) -> Result<ListResourceTemplatesResult, ErrorData> {
        Ok(ListResourceTemplatesResult::with_all_items(
            self.contract.resource_templates.clone(),
        ))
    }

    async fn read_resource(
        &self,
        request: ReadResourceRequestParams,
        _: RequestContext<RoleServer>,
    ) -> Result<ReadResourceResponse, ErrorData> {
        let uri = request.uri.as_str();
        let text = match uri {
            "mercadolibre://guia/operativa" => guidance::guide_resource(),
            "mercadolibre://recetas" => guidance::recipe_index(),
            uri => {
                if let Some(number) = uri.strip_prefix("mercadolibre://guia/imagen/") {
                    let number = number
                        .parse::<i64>()
                        .ok()
                        .filter(|_| number.bytes().all(|byte| byte.is_ascii_digit()))
                        .ok_or_else(|| {
                            ErrorData::invalid_params("Número de imagen inválido.", None)
                        })?;
                    let bytes = guidance::guide_image(number)
                        .map_err(|error| ErrorData::invalid_params(error.to_string(), None))?;
                    return Ok(ReadResourceResult::new(vec![
                        ResourceContents::blob(STANDARD.encode(bytes), uri)
                            .with_mime_type("image/png"),
                    ])
                    .into());
                }
                if let Some(id) = uri.strip_prefix("mercadolibre://recetas/") {
                    guidance::recipe_markdown(id)
                        .map_err(|error| ErrorData::invalid_params(error.to_string(), None))?
                } else {
                    return Err(ErrorData::resource_not_found(
                        "Recurso de Mercado Libre desconocido",
                        Some(json!({"uri":uri})),
                    ));
                }
            }
        };
        Ok(ReadResourceResult::new(vec![
            ResourceContents::text(text, uri).with_mime_type("text/markdown"),
        ])
        .into())
    }

    async fn list_prompts(
        &self,
        _: Option<PaginatedRequestParams>,
        _: RequestContext<RoleServer>,
    ) -> Result<ListPromptsResult, ErrorData> {
        Ok(ListPromptsResult::with_all_items(
            self.contract.prompts.clone(),
        ))
    }

    async fn get_prompt(
        &self,
        request: GetPromptRequestParams,
        _: RequestContext<RoleServer>,
    ) -> Result<GetPromptResponse, ErrorData> {
        let prompt = self
            .contract
            .prompts
            .iter()
            .find(|prompt| prompt.name == request.name)
            .ok_or_else(|| {
                ErrorData::invalid_params(
                    "Receta desconocida. Próximo paso: llamá meli_start_here.",
                    None,
                )
            })?;
        let default = request.name.trim_start_matches("receta_").replace('_', "-");
        let args = request.arguments.unwrap_or_default();
        let id = args
            .get("recipe_id")
            .and_then(Value::as_str)
            .unwrap_or(&default);
        let recipe = guidance::recipe_markdown(id)
            .map_err(|error| ErrorData::invalid_params(error.to_string(), None))?;
        let message = PromptMessage::new_text(
            Role::User,
            format!(
                "Seguí esta receta con las tools de este servidor (no uses el navegador):\n\n{recipe}"
            ),
        );
        let mut result = GetPromptResult::new(vec![message]);
        result.description.clone_from(&prompt.description);
        Ok(result.into())
    }
}
