use crate::state::{ensure_state_dir, now, private_options, restrict};
use anyhow::{Result, bail};
use rusqlite::{Connection, OptionalExtension, Row, params};
use rust_decimal::Decimal;
use serde_json::{Value, json};
use std::fs::File;
use std::io::ErrorKind;
use std::path::Path;
use std::sync::Arc;
use std::time::Duration;

pub(crate) const CENT: Decimal = Decimal::from_parts(1, 0, 0, false, 2);

pub(crate) fn decimal(
    value: &Value,
    minimum: Decimal,
    maximum: Option<Decimal>,
) -> Result<Decimal> {
    let text = match value {
        Value::String(text) => text.clone(),
        Value::Number(number) => number.to_string(),
        _ => bail!("Se necesita un número válido."),
    };
    let text = text.trim();
    if matches!(
        text.to_ascii_lowercase().trim_start_matches(['+', '-']),
        "nan" | "snan" | "inf" | "infinity"
    ) {
        bail!("Valor numérico fuera de los límites permitidos.");
    }
    let number = if text.contains(['e', 'E']) {
        Decimal::from_scientific(text)
    } else {
        Decimal::from_str_exact(text)
    }
    .map_err(|_| anyhow::anyhow!("Se necesita un número válido."))?;
    if number < minimum || maximum.is_some_and(|max| number > max) {
        bail!("Valor numérico fuera de los límites permitidos.");
    }
    Ok(number)
}

fn open_database(directory: &Path, name: &str) -> Result<Connection> {
    let path = directory.join(name);
    match private_options().write(true).create_new(true).open(&path) {
        Ok(_) => {}
        Err(error) if error.kind() == ErrorKind::AlreadyExists => {
            if !path.symlink_metadata()?.file_type().is_file() {
                bail!("El archivo {name} debe ser un archivo regular de la instalación.");
            }
        }
        Err(error) => return Err(error.into()),
    }
    restrict(&path, 0o600)?;
    let connection = Connection::open(path)?;
    connection.busy_timeout(Duration::from_secs(15))?;
    Ok(connection)
}

struct Memory(Connection);

impl Memory {
    fn open(directory: &Path) -> Result<Self> {
        let db = open_database(directory, "business.sqlite3")?;
        db.execute_batch(
            "CREATE TABLE IF NOT EXISTS product_rules (
               seller_id INTEGER NOT NULL, item_id TEXT NOT NULL, currency TEXT NOT NULL,
               minimum_advertised_price TEXT NOT NULL, max_discount_pct TEXT NOT NULL,
               cost TEXT, notes TEXT NOT NULL, updated_at INTEGER NOT NULL,
               PRIMARY KEY(seller_id,item_id)
             );
             CREATE TABLE IF NOT EXISTS policies (
               seller_id INTEGER NOT NULL, topic TEXT NOT NULL, rule_text TEXT NOT NULL,
               updated_at INTEGER NOT NULL, PRIMARY KEY(seller_id,topic)
             );",
        )?;
        Ok(Self(db))
    }

    fn rule_row(row: &Row<'_>) -> rusqlite::Result<Value> {
        Ok(json!({
            "seller_id": row.get::<_, i64>("seller_id")?,
            "item_id": row.get::<_, String>("item_id")?,
            "currency": row.get::<_, String>("currency")?,
            "minimum_advertised_price": row.get::<_, String>("minimum_advertised_price")?,
            "max_discount_pct": row.get::<_, String>("max_discount_pct")?,
            "cost": row.get::<_, Option<String>>("cost")?,
            "notes": row.get::<_, String>("notes")?,
            "updated_at": row.get::<_, i64>("updated_at")?,
        }))
    }

    fn get_rule(&self, seller: i64, item: &str) -> Result<Option<Value>> {
        Ok(self
            .0
            .query_row(
                "SELECT * FROM product_rules WHERE seller_id=?1 AND item_id=?2",
                params![seller, item],
                Self::rule_row,
            )
            .optional()?)
    }

    fn list(&self, seller: i64) -> Result<Value> {
        let mut rules = self
            .0
            .prepare("SELECT * FROM product_rules WHERE seller_id=?1 ORDER BY item_id")?;
        let product_rules = rules
            .query_map([seller], Self::rule_row)?
            .collect::<rusqlite::Result<Vec<_>>>()?;
        let mut policies = self
            .0
            .prepare("SELECT * FROM policies WHERE seller_id=?1 ORDER BY topic")?;
        let policies = policies
            .query_map([seller], |row| {
                Ok(json!({
                    "seller_id": seller,
                    "topic": row.get::<_, String>("topic")?,
                    "rule_text": row.get::<_, String>("rule_text")?,
                    "updated_at": row.get::<_, i64>("updated_at")?,
                }))
            })?
            .collect::<rusqlite::Result<Vec<_>>>()?;
        Ok(json!({"seller_id": seller, "product_rules": product_rules, "policies": policies}))
    }

    fn save_rule(&self, seller: i64, item: &str, currency: &str, fields: &Value) -> Result<Value> {
        if currency.len() != 3 || !currency.bytes().all(|byte| byte.is_ascii_alphabetic()) {
            bail!("Moneda inválida.");
        }
        let floor = decimal(&fields["minimum_advertised_price"], CENT, None)?;
        let discount = decimal(
            &fields["max_discount_pct"],
            Decimal::ZERO,
            Some(Decimal::ONE_HUNDRED),
        )?;
        let cost = (!fields["cost"].is_null())
            .then(|| decimal(&fields["cost"], Decimal::ZERO, None))
            .transpose()?
            .map(|amount| amount.to_string());
        let notes = fields["notes"].as_str();
        if notes.is_some_and(|text| text.chars().count() > 4000) {
            bail!("Las notas no pueden superar 4000 caracteres.");
        }
        // COALESCE preserves existing optional fields atomically, including writes from other MCP clients.
        Ok(self.0.query_row(
            "INSERT INTO product_rules (
                seller_id,item_id,currency,minimum_advertised_price,max_discount_pct,cost,notes,updated_at
             ) VALUES(?1,?2,?3,?4,?5,?6,COALESCE(?7,''),?8)
             ON CONFLICT(seller_id,item_id) DO UPDATE SET
                currency=excluded.currency, minimum_advertised_price=excluded.minimum_advertised_price,
                max_discount_pct=excluded.max_discount_pct, cost=COALESCE(excluded.cost,product_rules.cost),
                notes=COALESCE(?7,product_rules.notes), updated_at=excluded.updated_at
             RETURNING *",
            params![
                seller,
                item,
                currency.to_ascii_uppercase(),
                floor.to_string(),
                discount.to_string(),
                cost,
                notes,
                now() as i64
            ],
            Self::rule_row,
        )?)
    }

    fn save_policy(&self, seller: i64, topic: &str, rule_text: &str) -> Result<Value> {
        let topic = topic.trim().to_lowercase();
        let rule_text = rule_text.trim();
        if topic.is_empty()
            || topic.chars().count() > 100
            || rule_text.is_empty()
            || rule_text.chars().count() > 5000
        {
            bail!("Tema o texto de política inválido.");
        }
        self.0.execute(
            "INSERT INTO policies (seller_id,topic,rule_text,updated_at) VALUES(?1,?2,?3,?4)
             ON CONFLICT(seller_id,topic) DO UPDATE SET rule_text=excluded.rule_text,
                 updated_at=excluded.updated_at",
            params![seller, topic, rule_text, now() as i64],
        )?;
        Ok(json!({"seller_id": seller, "topic": topic, "rule_text": rule_text}))
    }
}

pub(crate) async fn get_rule(seller: i64, item: String) -> Result<Option<Value>> {
    tokio::task::spawn_blocking(move || Memory::open(&ensure_state_dir()?)?.get_rule(seller, &item))
        .await?
}

pub(crate) async fn list_memory(seller: i64) -> Result<Value> {
    tokio::task::spawn_blocking(move || Memory::open(&ensure_state_dir()?)?.list(seller)).await?
}

pub(crate) async fn save_rule(
    lock: File,
    seller: i64,
    item: String,
    currency: String,
    fields: Value,
) -> Result<Value> {
    tokio::task::spawn_blocking(move || {
        let _lock = lock;
        Memory::open(&ensure_state_dir()?)?.save_rule(seller, &item, &currency, &fields)
    })
    .await?
}

pub(crate) async fn save_policy(seller: i64, topic: String, rule_text: String) -> Result<Value> {
    tokio::task::spawn_blocking(move || {
        Memory::open(&ensure_state_dir()?)?.save_policy(seller, &topic, &rule_text)
    })
    .await?
}

fn ledger(directory: &Path) -> Result<Connection> {
    let db = open_database(directory, "question-attempts.sqlite3")?;
    db.execute(
        "CREATE TABLE IF NOT EXISTS attempts (
            question_id INTEGER PRIMARY KEY, state TEXT NOT NULL, updated_at INTEGER NOT NULL
        )",
        [],
    )?;
    Ok(db)
}

pub(crate) async fn get_attempt(question: i64) -> Result<Option<String>> {
    tokio::task::spawn_blocking(move || {
        Ok(ledger(&ensure_state_dir()?)?
            .query_row(
                "SELECT state FROM attempts WHERE question_id=?1",
                [question],
                |row| row.get(0),
            )
            .optional()?)
    })
    .await?
}

pub(crate) async fn set_attempt(lock: Arc<File>, question: i64, state: &'static str) -> Result<()> {
    tokio::task::spawn_blocking(move || {
        let _lock = lock;
        ledger(&ensure_state_dir()?)?.execute(
            "INSERT INTO attempts (question_id,state,updated_at) VALUES(?1,?2,?3)
             ON CONFLICT(question_id) DO UPDATE SET state=excluded.state, updated_at=excluded.updated_at",
            params![question, state, now() as i64],
        )?;
        Ok(())
    })
    .await?
}

pub(crate) async fn clear_attempt(lock: File, question: i64) -> Result<()> {
    tokio::task::spawn_blocking(move || {
        let _lock = lock;
        ledger(&ensure_state_dir()?)?
            .execute("DELETE FROM attempts WHERE question_id=?1", [question])?;
        Ok(())
    })
    .await?
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn seller_memory_is_partitioned_and_can_update_legacy_rows() -> Result<()> {
        let temporary = tempfile::tempdir()?;
        let database = temporary.path().join("business.sqlite3");
        let old = Connection::open(&database)?;
        old.execute_batch(
            "CREATE TABLE product_rules (
                seller_id INTEGER NOT NULL, item_id TEXT NOT NULL, currency TEXT NOT NULL,
                minimum_advertised_price TEXT NOT NULL, max_discount_pct TEXT NOT NULL,
                cost TEXT, notes TEXT NOT NULL, updated_at INTEGER NOT NULL,
                PRIMARY KEY(seller_id,item_id)
             );
             INSERT INTO product_rules VALUES (1,'MLA1','ARS','50.00','20','30.50','nota privada',0);",
        )?;
        drop(old);

        let memory = Memory::open(temporary.path())?;
        let fields = json!({"minimum_advertised_price": "55.00", "max_discount_pct": "10"});
        let update = memory.save_rule(1, "MLA1", "ARS", &fields)?;
        assert_eq!(update["minimum_advertised_price"], "55.00");
        assert_eq!(update["cost"], "30.50");
        assert_eq!(update["notes"], "nota privada");
        memory.save_policy(1, "  Facturación  ", "  Factura A  ")?;
        assert_eq!(memory.get_rule(2, "MLA1")?, None);
        assert_eq!(
            memory.list(2)?,
            json!({"seller_id": 2, "product_rules": [], "policies":[]})
        );
        assert_eq!(memory.list(1)?["policies"][0]["topic"], "facturación");

        #[cfg(unix)]
        assert_eq!(
            {
                use std::os::unix::fs::PermissionsExt;
                database.metadata()?.permissions().mode() & 0o777
            },
            0o600
        );
        Ok(())
    }

    #[test]
    fn decimal_limits_reject_invalid_and_imprecise_prices() {
        for text in ["NaN", "Infinity", "-1", "not-a-price", "101"] {
            assert!(decimal(&json!(text), Decimal::ZERO, Some(Decimal::ONE_HUNDRED)).is_err());
        }
        assert!(decimal(&json!("0.001"), CENT, None).is_err());
        assert!(decimal(&json!("1.12345678901234567890123456789"), CENT, None).is_err());
    }
}
