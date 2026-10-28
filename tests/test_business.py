import pytest
from meli_mcp.business import decimal_value, get_product_rule, list_memory, save_policy, save_product_rule

@pytest.fixture(autouse=True)
def private_state(tmp_path, monkeypatch):
    monkeypatch.setenv('MELI_MCP_STATE_DIR', str(tmp_path))

@pytest.mark.parametrize('value', ['NaN', 'Infinity', '-1', 'not-a-price'])
def test_invalid_prices_are_rejected(value):
    with pytest.raises(ValueError): decimal_value(value)

@pytest.mark.parametrize('discount', ['-1', '101', 'NaN'])
def test_invalid_discount_is_rejected(discount):
    with pytest.raises(ValueError): save_product_rule(1, 'MLA1', 'ARS', '50', discount, None, '')

def test_business_memory_is_partitioned_by_seller():
    save_product_rule(1, 'MLA1', 'ARS', '50.00', '20', '30.50', 'nota privada')
    save_policy(1, 'facturacion', 'Emitimos factura A')
    assert get_product_rule(1, 'MLA1')['minimum_advertised_price'] == '50.00'
    assert get_product_rule(2, 'MLA1') is None
    assert list_memory(2) == {'seller_id': 2, 'product_rules': [], 'policies': []}
    assert list_memory(1)['policies'][0]['rule_text'] == 'Emitimos factura A'
    save_product_rule(1, 'MLA1', 'ARS', '55', '10', None, None)
    assert get_product_rule(1, 'MLA1')['cost'] == '30.50'
    assert get_product_rule(1, 'MLA1')['notes'] == 'nota privada'

def test_ledger_database_is_private(tmp_path):
    import os
    save_policy(1, 'horarios', 'Lunes a viernes')
    if os.name != 'nt': assert (tmp_path / 'business.sqlite3').stat().st_mode & 0o777 == 0o600
