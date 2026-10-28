import pytest
from meli_mcp import server

class Fake:
    def __init__(self): self.calls = []
    def request(self, method, path, query=None, body=None, extra_headers=None):
        self.calls.append((method, path, body))
        if path == "/users/me": return {"id": 123}
        if path == "/items/MLA1": return {"price": 10, "user_product_id": "UP1"}
        if path in ("/questions/1", "/questions/456"): return {"id": 456, "seller_id": 123, "status": "UNANSWERED", "buyer_email": "private@example.invalid"}
        return {"ok": True}

def test_preview_is_read_only(monkeypatch):
    fake = Fake(); monkeypatch.setattr(server, "api", fake)
    result = server.meli_preview_item_changes("MLA1", {"price": 20})
    assert result["current"] == {"price": 10}
    assert result["proposed"] == {"price": 20}
    assert fake.calls == [("GET", "/items/MLA1", None)]

def test_catalog_requires_confirmation(monkeypatch):
    fake = Fake(); monkeypatch.setattr(server, "api", fake)
    with pytest.raises(ValueError, match="confirmación"):
        server.meli_api_request("POST", "/items/catalog_listings", body={"item_id": "MLA1"})
    assert fake.calls == []
    server.meli_api_request("POST", "/items/catalog_listings", body={"item_id": "MLA1"}, catalog_confirmed=True)
    assert len(fake.calls) == 1

def test_up_stock_is_specific_read(monkeypatch):
    fake = Fake(); monkeypatch.setattr(server, "api", fake)
    server.meli_user_product_stock("UP1")
    assert fake.calls == [("GET", "/user-products/UP1/stock", None)]

def test_questions_and_answer_require_explicit_request(monkeypatch, tmp_path):
    monkeypatch.setenv("MELI_MCP_STATE_DIR", str(tmp_path))
    fake = Fake(); monkeypatch.setattr(server, "api", fake)
    server.meli_unanswered_questions(123)
    with pytest.raises(ValueError): server.meli_answer_question(456, "Respuesta", False)
    server.meli_answer_question(456, "Respuesta", True)
    assert fake.calls == [("GET", "/questions/search", None), ("GET", "/questions/456", None), ("GET", "/users/me", None), ("POST", "/answers", {"question_id": 456, "text": "Respuesta"})]
    assert "buyer_email" not in server.meli_question_detail(456)

def test_answer_does_not_send_if_already_answered(monkeypatch, tmp_path):
    monkeypatch.setenv("MELI_MCP_STATE_DIR", str(tmp_path))
    class Answered(Fake):
        def request(self, method, path, query=None, body=None, extra_headers=None):
            self.calls.append((method, path, body))
            return {"id": 456, "status": "ANSWERED"}
    fake = Answered(); monkeypatch.setattr(server, "api", fake)
    with pytest.raises(ValueError, match="ya no figura"):
        server.meli_answer_question(456, "Respuesta", True)
    assert fake.calls == [("GET", "/questions/456", None)]

def test_promotions_are_explicit_and_mocked(monkeypatch, tmp_path):
    monkeypatch.setenv("MELI_MCP_STATE_DIR", str(tmp_path))
    class Promotion(Fake):
        def request(self, method, path, query=None, body=None, extra_headers=None):
            self.calls.append((method, path, body))
            if path == "/users/me": return {"id": 123}
            if path == "/items/MLA1": return {"id":"MLA1", "seller_id":123, "currency_id":"ARS", "price":100}
            if path == "/seller-promotions/items/MLA1" and method == "GET": return [{"type":"DOD", "status":"candidate"}]
            return {"ok":True}
    fake = Promotion(); monkeypatch.setattr(server, "api", fake)
    with pytest.raises(ValueError): server.meli_apply_deal_of_day("MLA1", 50, False)
    with pytest.raises(ValueError, match="SIN_REGLA_LOCAL"): server.meli_apply_deal_of_day("MLA1", 50, True)
    server.meli_save_product_rule("MLA1", "60", "30")
    with pytest.raises(ValueError, match="PERFORA_PISO_PUBLICO"): server.meli_apply_deal_of_day("MLA1", 50, True)
    with pytest.raises(ValueError, match="SUPERA_DESCUENTO_MAXIMO"): server.meli_apply_deal_of_day("MLA1", 65, True)
    server.meli_apply_deal_of_day("MLA1", 70, True)
    assert fake.calls[-1] == ("POST", "/seller-promotions/items/MLA1", {"deal_price":70, "promotion_type":"DOD"})
    with pytest.raises(ValueError, match="aún no tiene validación"):
        server.meli_api_request("POST", "/seller-promotions/items/MLA1", body={"promotion_type":"DEAL"}, explicitly_requested=True)

def test_sensitive_guards_are_consistent(monkeypatch, tmp_path):
    monkeypatch.setenv("MELI_MCP_STATE_DIR", str(tmp_path))
    fake = Fake(); monkeypatch.setattr(server, "api", fake)
    with pytest.raises(ValueError, match="catálogo"):
        server.meli_update_item("MLA1", {"catalog_listing": True})
    with pytest.raises(ValueError, match="explicitly_requested"):
        server.meli_api_request("POST", "/answers", body={"question_id": 1, "text": "Hola"})
    with pytest.raises(ValueError, match="explicitly_requested"):
        server.meli_api_request("POST", "/seller-promotions/items/MLA1", body={"promotion_type": "DOD"})
    assert fake.calls == []
    server.meli_api_request("POST", "/answers", body={"question_id": 1, "text": "Hola"}, explicitly_requested=True)
    assert fake.calls[-3] == ("GET", "/questions/1", None)
    assert fake.calls[-1] == ("POST", "/answers", {"question_id": 1, "text": "Hola"})


def test_promotion_pagination_passes_cursor(monkeypatch):
    class Pages:
        def __init__(self): self.queries = []
        def request(self, method, path, query=None, **kwargs):
            self.queries.append((path, query))
            return {"results": ["second"] if query.get("search_after") else ["first"],
                    "paging": {} if query.get("search_after") else {"searchAfter": "next"}}
    api = Pages(); monkeypatch.setattr(server, "api", api)
    first = server.meli_promotion_items("P-MLA1", "DOD", limit=1)
    second = server.meli_promotion_items("P-MLA1", "DOD", limit=1, search_after=first["paging"]["searchAfter"])
    assert first["results"] == ["first"] and second["results"] == ["second"]
    assert api.queries[-1][1]["search_after"] == "next"
    server.meli_seller_promotions(123, promotion_type="DOD", search_after="next")
    assert api.queries[-1] == ("/seller-promotions/users/123", {"app_version": "v2", "limit": 50, "promotion_type": "DOD", "search_after": "next"})
    with pytest.raises(ValueError): server.meli_promotion_items("P-MLA1", "DOD", limit=51)


def test_uncertain_answer_is_not_retried_automatically(monkeypatch, tmp_path):
    from meli_mcp.client import MeliError
    monkeypatch.setenv("MELI_MCP_STATE_DIR", str(tmp_path))
    class Timeout(Fake):
        def request(self, method, path, query=None, body=None, extra_headers=None):
            if path == "/answers":
                self.calls.append((method, path, body))
                raise MeliError("Resultado incierto")
            return super().request(method, path, query, body, extra_headers)
    fake = Timeout(); monkeypatch.setattr(server, "api", fake)
    with pytest.raises(MeliError): server.meli_answer_question(456, "Respuesta", True)
    assert server.meli_question_attempt(456)["local_attempt"] == "uncertain"
    with pytest.raises(ValueError, match="intento anterior"):
        server.meli_answer_question(456, "Respuesta", True)
    assert sum(path == "/answers" for _, path, _ in fake.calls) == 1
    assert server.meli_clear_question_attempt(456, True)["cleared"]
    assert server.meli_question_attempt(456)["local_attempt"] is None


def test_successful_answer_has_durable_local_receipt(monkeypatch, tmp_path):
    monkeypatch.setenv("MELI_MCP_STATE_DIR", str(tmp_path))
    fake = Fake(); monkeypatch.setattr(server, "api", fake)
    server.meli_answer_question(456, "Respuesta", True)
    with pytest.raises(ValueError, match="intento anterior"):
        server.meli_answer_question(456, "Respuesta", True)
    assert server.meli_question_attempt(456)["local_attempt"] == "sent"
    assert sum(path == "/answers" for _, path, _ in fake.calls) == 1


def test_answer_lock_and_ledger_across_processes(monkeypatch, tmp_path):
    import os, subprocess, sys
    code = r'''
import os, time
from pathlib import Path
from meli_mcp import server
class API:
    def request(self, method, path, query=None, body=None, extra_headers=None):
        if path == "/users/me": return {"id": 123}
        if method == "GET": return {"id": 456, "seller_id": 123, "status": "UNANSWERED"}
        with (Path(os.environ["MELI_MCP_STATE_DIR"]) / "sent.log").open("a") as f: f.write("send\n")
        time.sleep(0.2)
        return {"status": "ok"}
server.api = API()
try:
    server.meli_answer_question(456, "Hola", True)
    print("sent")
except ValueError:
    print("blocked")
'''
    env = os.environ.copy(); env["MELI_MCP_STATE_DIR"] = str(tmp_path)
    processes = [subprocess.Popen([sys.executable, "-c", code], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
    results = []
    for process in processes:
        out, err = process.communicate(timeout=30)
        assert process.returncode == 0, err
        results.append(out.strip())
    assert sorted(results) == ["blocked", "sent"]
    assert (tmp_path / "sent.log").read_text().splitlines() == ["send"]


def test_question_context_enriches_without_buyer_contact(monkeypatch, tmp_path):
    monkeypatch.setenv('MELI_MCP_STATE_DIR', str(tmp_path))
    class ContextAPI:
        def request(self, method, path, query=None, body=None, extra_headers=None):
            if path == '/users/me': return {'id': 123}
            if path == '/questions/1': return {'id': 1, 'seller_id': 123, 'item_id': 'MLA1', 'status':'UNANSWERED', 'text':'Consulta', 'buyer_email':'secret@example.invalid'}
            if path == '/items/MLA1': return {'id':'MLA1','seller_id':123,'price':100,'currency_id':'ARS','title':'Producto'}
            if path == '/items/MLA1/description': return {'plain_text':'Datos del producto'}
            raise AssertionError(path)
    monkeypatch.setattr(server, 'api', ContextAPI())
    server.meli_save_business_policy('facturacion', 'Emito factura A')
    result=server.meli_question_context(1)
    assert result['item']['title']=='Producto'
    assert result['policies'][0]['rule_text']=='Emito factura A'
    assert 'buyer_email' not in result['question']


def test_generic_dod_uses_floor_guard(monkeypatch, tmp_path):
    monkeypatch.setenv('MELI_MCP_STATE_DIR', str(tmp_path))
    class API:
        def __init__(self): self.calls=[]
        def request(self, method, path, query=None, body=None, extra_headers=None):
            self.calls.append((method,path))
            if path == '/users/me': return {'id': 123}
            if path == '/items/MLA1': return {'seller_id':123,'currency_id':'ARS','price':100}
            return {'ok':True}
    fake=API();monkeypatch.setattr(server, 'api', fake)
    with pytest.raises(ValueError, match='SIN_REGLA_LOCAL'):
        server.meli_api_request('POST','/seller-promotions/items/MLA1',body={'promotion_type':'DOD','deal_price':50},explicitly_requested=True)
    assert ('POST','/seller-promotions/items/MLA1') not in fake.calls


@pytest.mark.parametrize('question', [
    {'id':456,'seller_id':999,'status':'UNANSWERED'},
    {'id':456,'seller_id':123,'status':'UNANSWERED','hold':True},
    {'id':456,'seller_id':123,'status':'UNANSWERED','suspected_spam':True},
])
def test_answer_rejects_other_seller_or_held_question(monkeypatch, tmp_path, question):
    monkeypatch.setenv('MELI_MCP_STATE_DIR', str(tmp_path))
    class API(Fake):
        def request(self, method, path, query=None, body=None, extra_headers=None):
            if path == '/questions/456':
                self.calls.append((method,path,body)); return question
            return super().request(method,path,query,body,extra_headers)
    fake=API(); monkeypatch.setattr(server,'api',fake)
    with pytest.raises(ValueError): server.meli_answer_question(456,'Hola',True)
    assert all(path != '/answers' for _,path,_ in fake.calls)


@pytest.mark.parametrize('text', ['a' * 2001, 'Escribinos a a@example.invalid', 'Visita https://example.invalid'])
def test_answer_rejects_long_or_obvious_contact(monkeypatch, tmp_path, text):
    monkeypatch.setenv('MELI_MCP_STATE_DIR', str(tmp_path))
    fake=Fake();monkeypatch.setattr(server, 'api', fake)
    with pytest.raises(ValueError): server.meli_answer_question(456,text,True)
    assert fake.calls == []


def test_active_catalog_rule_reconciliation_is_paginated(monkeypatch, tmp_path):
    monkeypatch.setenv('MELI_MCP_STATE_DIR', str(tmp_path))
    class API:
        def request(self, method, path, query=None, body=None, extra_headers=None):
            if path == '/users/me': return {'id':123}
            if path == '/users/123/items/search': return {'results':['MLA1','MLA2'],'paging':{'offset':0,'limit':2,'total':3}}
            if path == '/items/bulk': return [{'code':200,'body':{'id':'MLA1','seller_id':123,'price':100,'currency_id':'ARS','title':'P1'}},{'code':200,'body':{'id':'MLA2','seller_id':123,'price':200,'currency_id':'ARS','title':'P2'}}]
            if path == '/items/MLA1': return {'id':'MLA1','seller_id':123,'currency_id':'ARS'}
            raise AssertionError(path)
    monkeypatch.setattr(server,'api',API())
    server.meli_save_product_rule('MLA1','50','20')
    page=server.meli_active_rules_status(limit=2)
    assert page['paging']['total']==3 and len(page['items'])==2
    assert page['items'][0]['rule']['minimum_advertised_price']=='50'
    assert page['items'][1]['rule'] is None


def test_nested_operation_and_token_locks_with_real_client(tmp_path):
    import os, subprocess, sys
    code = r'''
import os, time
from pathlib import Path
import httpx
from meli_mcp import server
from meli_mcp.client import MeliClient, write_private
state=Path(os.environ['MELI_MCP_STATE_DIR'])
write_private(state/'token.json', {'access_token':'test','expires_at':time.time()+3600})
def handler(req):
    p=req.url.path
    if p=='/users/me': return httpx.Response(200,json={'id':123})
    if p=='/items/MLA1': return httpx.Response(200,json={'id':'MLA1','seller_id':123,'currency_id':'ARS','price':100})
    if p=='/seller-promotions/items/MLA1' and req.method=='GET': return httpx.Response(200,json=[{'type':'DOD','status':'candidate'}])
    if p=='/questions/456': return httpx.Response(200,json={'id':456,'seller_id':123,'status':'UNANSWERED'})
    return httpx.Response(200,json={'ok':True})
server.api=MeliClient(httpx.MockTransport(handler))
server.meli_save_product_rule('MLA1','50','40')
server.meli_apply_deal_of_day('MLA1',70,True)
server.meli_answer_question(456,'Hola',True)
print('ok')
'''
    env = os.environ.copy(); env['MELI_MCP_STATE_DIR'] = str(tmp_path)
    done = subprocess.run([sys.executable, '-c', code], env=env, capture_output=True, text=True, timeout=20)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == 'ok'
