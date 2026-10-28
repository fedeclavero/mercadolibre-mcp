"""Local seller-owned rules; money is stored as decimal strings, never floats."""
from __future__ import annotations
from contextlib import closing
from decimal import Decimal, InvalidOperation
import os
import sqlite3
import time
from typing import Any
from .client import ensure_state_dir


def decimal_value(value: Any, *, minimum: Decimal = Decimal('0'), maximum: Decimal | None = None) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError('Se necesita un número válido.') from None
    if not result.is_finite() or result < minimum or (maximum is not None and result > maximum):
        raise ValueError('Valor numérico fuera de los límites permitidos.')
    return result


def _connect() -> sqlite3.Connection:
    path = ensure_state_dir() / 'business.sqlite3'
    db = sqlite3.connect(path, timeout=15)
    if os.name != 'nt': os.chmod(path, 0o600)
    db.row_factory = sqlite3.Row
    db.executescript('''
      CREATE TABLE IF NOT EXISTS product_rules (
        seller_id INTEGER NOT NULL, item_id TEXT NOT NULL, currency TEXT NOT NULL,
        minimum_advertised_price TEXT NOT NULL, max_discount_pct TEXT NOT NULL,
        cost TEXT, notes TEXT NOT NULL, updated_at INTEGER NOT NULL,
        PRIMARY KEY(seller_id,item_id));
      CREATE TABLE IF NOT EXISTS policies (
        seller_id INTEGER NOT NULL, topic TEXT NOT NULL, rule_text TEXT NOT NULL,
        updated_at INTEGER NOT NULL, PRIMARY KEY(seller_id,topic));
    ''')
    return db


def save_product_rule(seller_id: int, item_id: str, currency: str, minimum: Any, max_discount: Any, cost: Any | None, notes: str | None) -> dict[str, Any]:
    floor = decimal_value(minimum, minimum=Decimal('0.01'))
    discount = decimal_value(max_discount, maximum=Decimal('100'))
    previous = get_product_rule(seller_id, item_id)
    value_cost = str(decimal_value(cost)) if cost is not None else (previous.get('cost') if previous else None)
    notes = notes if notes is not None else (previous.get('notes', '') if previous else '')
    if not isinstance(currency, str) or len(currency) != 3 or not currency.isascii() or not currency.isalpha(): raise ValueError('Moneda inválida.')
    if len(notes) > 4000: raise ValueError('Las notas no pueden superar 4000 caracteres.')
    with closing(_connect()) as db, db:
        db.execute('''INSERT INTO product_rules VALUES(?,?,?,?,?,?,?,?)
        ON CONFLICT(seller_id,item_id) DO UPDATE SET currency=excluded.currency, minimum_advertised_price=excluded.minimum_advertised_price,
        max_discount_pct=excluded.max_discount_pct,cost=excluded.cost,notes=excluded.notes,updated_at=excluded.updated_at''',
        (seller_id,item_id,currency.upper(),str(floor),str(discount),value_cost,notes,int(time.time())))
    return get_product_rule(seller_id,item_id) or {}


def get_product_rule(seller_id: int, item_id: str) -> dict[str, Any] | None:
    with closing(_connect()) as db:
        row = db.execute('SELECT * FROM product_rules WHERE seller_id=? AND item_id=?', (seller_id,item_id)).fetchone()
        return dict(row) if row else None


def save_policy(seller_id: int, topic: str, rule_text: str) -> dict[str, Any]:
    topic = topic.strip().lower(); rule_text = rule_text.strip()
    if not topic or len(topic) > 100 or not rule_text or len(rule_text) > 5000: raise ValueError('Tema o texto de política inválido.')
    with closing(_connect()) as db, db:
        db.execute('''INSERT INTO policies VALUES(?,?,?,?) ON CONFLICT(seller_id,topic)
                      DO UPDATE SET rule_text=excluded.rule_text, updated_at=excluded.updated_at''', (seller_id,topic,rule_text,int(time.time())))
    return {'seller_id': seller_id, 'topic': topic, 'rule_text': rule_text}


def list_memory(seller_id: int) -> dict[str, Any]:
    with closing(_connect()) as db:
        rules = [dict(row) for row in db.execute('SELECT * FROM product_rules WHERE seller_id=? ORDER BY item_id', (seller_id,))]
        policies = [dict(row) for row in db.execute('SELECT * FROM policies WHERE seller_id=? ORDER BY topic', (seller_id,))]
    return {'seller_id': seller_id, 'product_rules': rules, 'policies': policies}
