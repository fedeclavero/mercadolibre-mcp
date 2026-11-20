use anyhow::{Result, anyhow, bail};
use serde_json::{Map, Value, json};

use crate::client::{Api, checked_path, segment};
use crate::{guidance, state, workflows};

pub async fn call(api: &Api, name: &str, a: &Value, local_files: bool) -> Result<Value> {
    let text = |key: &str| a[key].as_str().unwrap_or_default();
    let flag = |key: &str| a[key].as_bool().unwrap_or_default();
    let integer = |key: &str| {
        a[key].as_i64().ok_or_else(|| {
            anyhow!("{key} está fuera de los límites admitidos. Próximo paso: revisá el valor.")
        })
    };
    let id = |key: &str| segment(text(key));
    let send = |method: &'static str, path: String, body: Value| async move {
        api.request(method, &path, Value::Null, body, Value::Null)
            .await
    };
    match name {
        "meli_start_here" => Ok(guidance::start_here()),
        "meli_find_tool" => {
            guidance::find(text("query"), integer("max_results")?.clamp(1, 10) as usize)
        }
        "meli_get_recipe" => guidance::get_recipe(text("recipe_id")),
        "meli_guia_indice" => Ok(guidance::guide_index()),
        "meli_guia_seccion" => guidance::guide_section(text("seccion")),
        "meli_status" => Ok(status(api).await),
        "meli_account" => Ok(select(
            &api.get("/users/me", Value::Null).await?,
            &["id", "nickname", "site_id"],
        )),
        "meli_list_items" => {
            let query = workflows::listing_query(
                integer("limit")?,
                integer("offset")?,
                a["status"].as_str(),
                flag("scan"),
                a["scroll_id"].as_str(),
            )?;
            api.get(
                &format!("/users/{}/items/search", integer("seller_id")?),
                query,
            )
            .await
        }
        "meli_get_item" => {
            api.get(&format!("/items/{}", id("item_id")?), Value::Null)
                .await
        }
        "meli_get_items" => {
            let ids = strings(&a["item_ids"]);
            if !(1..=20).contains(&ids.len()) {
                bail!("Indica entre 1 y 20 IDs");
            }
            let checked = ids.into_iter().map(segment).collect::<Result<Vec<_>>>()?;
            api.get("/items/bulk", json!({"ids": checked.join(",")}))
                .await
        }
        "meli_create_item" => {
            validate_catalog("/items", &a["data"], flag("catalog_confirmed"))?;
            send("POST", "/items".into(), a["data"].clone()).await
        }
        "meli_update_item" => {
            let path = format!("/items/{}", id("item_id")?);
            require_changes(&a["changes"])?;
            validate_catalog(&path, &a["changes"], flag("catalog_confirmed"))?;
            send("PUT", path, a["changes"].clone()).await
        }
        "meli_get_description" => {
            api.get(
                &format!("/items/{}/description", id("item_id")?),
                Value::Null,
            )
            .await
        }
        "meli_update_description" => {
            send(
                "PUT",
                format!("/items/{}/description", id("item_id")?),
                json!({"plain_text":text("plain_text")}),
            )
            .await
        }
        "meli_category" => {
            let suffix = if flag("attributes") {
                "/attributes"
            } else {
                ""
            };
            api.get(
                &format!("/categories/{}{suffix}", id("category_id")?),
                Value::Null,
            )
            .await
        }
        "meli_search_catalog" => {
            pagination(integer("limit")?, integer("offset")?)?;
            api.get(
                "/products/search",
                select(a, &["site_id", "q", "limit", "offset"]),
            )
            .await
        }
        "meli_catalog_product" => {
            api.get(&format!("/products/{}", id("product_id")?), Value::Null)
                .await
        }
        "meli_catalog_eligibility" => {
            api.get(
                &format!("/items/{}/catalog_listing_eligibility", id("item_id")?),
                Value::Null,
            )
            .await
        }
        "meli_search_orders" => {
            pagination(integer("limit")?, integer("offset")?)?;
            let mut query = select(a, &["limit", "offset", "sort"]);
            query["seller"] = a["seller_id"].clone();
            api.get("/orders/search", query).await
        }
        "meli_get_order" => {
            api.get(&format!("/orders/{}", id("order_id")?), Value::Null)
                .await
        }
        "meli_get_shipment" => {
            api.request(
                "GET",
                &format!("/shipments/{}", id("shipment_id")?),
                Value::Null,
                Value::Null,
                json!({"x-format-new": "true"}),
            )
            .await
        }
        "meli_api_request" => generic(api, a).await,
        "meli_set_item_pictures" => {
            let urls = strings(&a["picture_urls"]);
            if urls.is_empty() || urls.iter().any(|url| !url.starts_with("https://")) {
                bail!("Indica una o más URLs HTTPS");
            }
            let pictures: Vec<_> = urls.into_iter().map(|url| json!({"source":url})).collect();
            send(
                "PUT",
                format!("/items/{}", id("item_id")?),
                json!({"pictures":pictures}),
            )
            .await
        }
        "meli_user_product" => {
            api.get(
                &format!("/user-products/{}", id("user_product_id")?),
                Value::Null,
            )
            .await
        }
        "meli_user_product_stock" => {
            api.get(
                &format!("/user-products/{}/stock", id("user_product_id")?),
                Value::Null,
            )
            .await
        }
        "meli_item_performance" => {
            api.get(
                &format!("/item/{}/performance", id("item_id")?),
                Value::Null,
            )
            .await
        }
        "meli_picture_errors" => {
            api.get(
                &format!("/pictures/{}/errors", id("picture_id")?),
                Value::Null,
            )
            .await
        }
        "meli_preview_item_changes" => {
            let changes = require_changes(&a["changes"])?;
            let item_id = id("item_id")?;
            let item = api.get(&format!("/items/{item_id}"), Value::Null).await?;
            if !item.is_object() || item.get("data").is_some() {
                bail!("Respuesta inesperada; consulta el ítem antes de preparar cambios");
            }
            let values: Map<_, _> = changes
                .keys()
                .map(|key| (key.clone(), item[key].clone()))
                .collect();
            Ok(
                json!({"item_id":item_id,"user_product_id":item["user_product_id"],"current":values,"proposed":changes,
                "warning":"No es una validación. Verifica categoría, stock por ubicación, publicaciones asociadas y fuente de verdad antes de escribir."}),
            )
        }
        "meli_compare_catalog" => {
            let item_id = id("item_id")?;
            let product_id = id("product_id")?;
            let item = api.get(&format!("/items/{item_id}"), Value::Null).await?;
            let eligibility = api
                .get(
                    &format!("/items/{item_id}/catalog_listing_eligibility"),
                    Value::Null,
                )
                .await?;
            let product = api
                .get(&format!("/products/{product_id}"), Value::Null)
                .await?;
            Ok(
                json!({"item":item,"eligibility":eligibility,"product":product,"warning":"Comprueba que la variante y los atributos coincidan exactamente y el producto esté activo. Opt-in requiere instrucción explícita y contrato oficial vigente."}),
            )
        }
        "meli_unanswered_questions" => {
            pagination(integer("limit")?, integer("offset")?)
                .map_err(|_| anyhow!("Paginación inválida"))?;
            let mut query = select(a, &["seller_id", "limit", "offset"]);
            query["status"] = json!("UNANSWERED");
            query["api_version"] = json!(4);
            api.get("/questions/search", query).await
        }
        "meli_question_detail" => workflows::question_detail(api, integer("question_id")?).await,
        "meli_item_promotions" => {
            api.get(
                &format!("/seller-promotions/items/{}", id("item_id")?),
                json!({"app_version":"v2"}),
            )
            .await
        }
        "meli_promotion_items" => {
            if !(1..=50).contains(&integer("limit")?) {
                bail!("limit debe estar entre 1 y 50");
            }
            let mut query =
                populated_options(a, &["promotion_type", "limit", "status", "search_after"]);
            query["app_version"] = json!("v2");
            api.get(
                &format!(
                    "/seller-promotions/promotions/{}/items",
                    id("promotion_id")?
                ),
                query,
            )
            .await
        }
        "meli_seller_promotions" => {
            let seller = integer("seller_id")?;
            if seller <= 0 || !(1..=50).contains(&integer("limit")?) {
                bail!("Vendedor o límite inválido");
            }
            let mut query = populated_options(a, &["limit", "promotion_type", "search_after"]);
            query["app_version"] = json!("v2");
            api.get(&format!("/seller-promotions/users/{seller}"), query)
                .await
        }
        "meli_answer_question"
        | "meli_question_attempt"
        | "meli_clear_question_attempt"
        | "meli_save_product_rule"
        | "meli_save_business_policy"
        | "meli_business_memory"
        | "meli_active_rules_status"
        | "meli_question_context"
        | "meli_analyze_deal_of_day"
        | "meli_apply_deal_of_day"
        | "meli_my_items"
        | "meli_combo_plan"
        | "meli_upload_picture" => workflows::call(api, name, a, local_files).await,
        _ => bail!("Herramienta desconocida. Próximo paso: llamá meli_start_here."),
    }
}

fn select(value: &Value, keys: &[&str]) -> Value {
    Value::Object(
        keys.iter()
            .filter_map(|key| {
                value
                    .get(key)
                    .map(|value| ((*key).to_owned(), value.clone()))
            })
            .collect(),
    )
}

fn populated_options(value: &Value, keys: &[&str]) -> Value {
    Value::Object(
        keys.iter()
            .filter_map(|key| {
                value
                    .get(key)
                    .filter(|value| !value.is_null() && value.as_str() != Some(""))
                    .map(|value| ((*key).to_owned(), value.clone()))
            })
            .collect(),
    )
}

fn strings(value: &Value) -> Vec<&str> {
    value
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .collect()
}

fn pagination(limit: i64, offset: i64) -> Result<()> {
    if !(1..=50).contains(&limit) || offset < 0 {
        bail!("limit debe estar entre 1 y 50 y offset no puede ser negativo");
    }
    Ok(())
}

fn require_changes(value: &Value) -> Result<&Map<String, Value>> {
    value
        .as_object()
        .filter(|changes| !changes.is_empty())
        .ok_or_else(|| anyhow!("No se indicaron cambios"))
}

fn validate_catalog(path: &str, body: &Value, confirmed: bool) -> Result<()> {
    let catalog = path.to_ascii_lowercase().contains("catalog")
        || body["catalog_listing"].as_bool() == Some(true)
        || body.get("catalog_product_id").is_some();
    if catalog && !confirmed {
        bail!("Escritura de catálogo requiere confirmación explícita y catalog_confirmed=true");
    }
    Ok(())
}

async fn generic(api: &Api, a: &Value) -> Result<Value> {
    let method = a["method"].as_str().unwrap_or_default();
    let path = checked_path(a["path"].as_str().unwrap_or_default())?;
    let normalized = path.trim_end_matches('/').to_ascii_lowercase();
    let body = &a["body"];
    let query = &a["query"];
    let headers = &a["headers"];
    let explicit = a["explicitly_requested"].as_bool() == Some(true);
    let empty = |value: &Value| value.is_null() || value.as_object().is_some_and(Map::is_empty);
    if method != "GET" {
        validate_catalog(path, body, a["catalog_confirmed"].as_bool() == Some(true))?;
        let answers = normalized == "/answers" || normalized.starts_with("/answers/");
        let communication = answers || normalized.split('/').any(|part| part == "messages");
        let promotion = matches!(
            normalized.split('/').nth(1),
            Some("seller-promotions" | "promotions" | "offers" | "deals")
        );
        if (communication || promotion) && !explicit {
            bail!(
                "Enviar una respuesta o modificar una promoción requiere explicitly_requested=true"
            );
        }
        if answers {
            if normalized != "/answers"
                || method != "POST"
                || !empty(query)
                || !empty(headers)
                || body.as_object().map_or(0, Map::len) != 2
            {
                bail!("Para responder, usa question_id y text sin parámetros adicionales");
            }
            let (Some(question_id), Some(text)) =
                (body["question_id"].as_i64(), body["text"].as_str())
            else {
                bail!("Pregunta o texto inválido");
            };
            return workflows::answer_question(api, question_id, text, explicit).await;
        }
        if promotion {
            if method == "POST"
                && body.as_object().map_or(0, Map::len) == 2
                && body["promotion_type"] == "DOD"
                && !body["deal_price"].is_null()
                && empty(headers)
                && (empty(query) || *query == json!({"app_version":"v2"}))
                && let Some(item) = path.strip_prefix("/seller-promotions/items/")
            {
                return workflows::apply_dod(api, item, &body["deal_price"], explicit).await;
            }
            bail!(
                "Esta operación de promoción aún no tiene validación de reglas implementada. Solo se admite DOD mediante la herramienta dedicada."
            );
        }
    }
    api.request(method, path, query.clone(), body.clone(), headers.clone())
        .await
}

async fn status(api: &Api) -> Value {
    let readonly = state::access_mode() == "read_only";
    let scopes = state::load_private("token.json").ok().and_then(|token| {
        let source = &token["scope"];
        let mut scopes: Vec<String> = match source {
            Value::String(text) => text
                .split(|c: char| c == ',' || c.is_whitespace())
                .filter(|x| !x.is_empty())
                .map(str::to_owned)
                .collect(),
            Value::Array(list) => list
                .iter()
                .map(|item| {
                    item.as_str()
                        .map_or_else(|| item.to_string(), str::to_owned)
                })
                .collect(),
            _ => return None,
        };
        scopes.sort();
        Some(scopes)
    });
    let mut notices = Vec::new();
    if let Some(scopes) = &scopes {
        for needed in ["read", "write", "offline_access"] {
            if !(scopes.iter().any(|scope| scope == needed) || readonly && needed == "write") {
                notices.push(format!("Falta el permiso {needed} en la autorización."));
            }
        }
    }
    let missing_perms = !notices.is_empty();
    if readonly {
        notices.push("El modo es solo lectura: los cambios en la cuenta y en la memoria local están bloqueados. Para habilitarlas: `mercadolibre-mcp mode full`.".to_owned());
    }
    let mut result = json!({
        "version": env!("CARGO_PKG_VERSION"),
        "modo": if readonly { "solo lectura" } else { "lectura y escritura" },
        "permisos_autorizados": scopes.map_or_else(|| json!("desconocidos"), |scopes| json!(scopes)),
    });
    match api.get("/users/me", Value::Null).await {
        Ok(profile) => {
            result["ok"] = json!(true);
            result["cuenta"] = select(&profile, &["id", "nickname", "site_id"]);
            if missing_perms {
                result["proximo_paso"] = json!(
                    "En el DevCenter revisá los permisos para las operaciones que necesitás y ejecutá `mercadolibre-mcp authorize`."
                );
            }
        }
        Err(error) => {
            result["ok"] = json!(false);
            result["error"] = json!(error.to_string());
            result["proximo_paso"] = json!(
                "Ejecutá `mercadolibre-mcp doctor --json` en la terminal y seguí su próximo paso (guía: docs/AGENT_INSTALL.md)."
            );
        }
    }
    if !notices.is_empty() {
        result["avisos"] = json!(notices);
    }
    result
}
