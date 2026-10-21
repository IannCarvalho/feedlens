"""Teste de contrato: a API real bate com openapi.yaml.

Tres garantias:

* (a) ``openapi.yaml`` e um documento OpenAPI 3.1 valido
  (``openapi-spec-validator``);
* (b) para cada endpoint coberto, chama a API real pelo test client (com a
  LLM mockada e chaves falsas) e valida corpo + status contra o esquema da
  resposta declarada na spec (``jsonschema``, dialeto 2020-12);
* (c) se uma rota coberta sumir ou mudar de forma, o teste falha: cada caso
  afirma o status e o corpo que a spec promete, e um conjunto de rotas
  esperadas e conferido contra os ``paths`` do documento.

Nenhuma chamada paga: a unica analise exercitada e mockada em
``app.routes.api.feedback_analyzer.analyze_feedback`` e nos pontos do servico.
"""
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import best_match
from openapi_spec_validator import validate as validate_spec
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from app import create_app, db
from tests.helpers import csrf_token, login

SPEC_PATH = Path(__file__).resolve().parent.parent / "openapi.yaml"

PASSWORD = "segredo-do-painel-contrato"

POSITIVE = {"sentiment": "POSITIVO", "is_spam": False, "requested_features": []}
WITH_FEATURE = {
    "sentiment": "NEGATIVO",
    "is_spam": False,
    "requested_features": [{"code": "MODO_ESCURO", "reason": "pediu modo escuro"}],
    "analyzed_by": "openai/gpt-4o-mini",
}

# Rotas (metodo, caminho da spec) que este teste cobre de ponta a ponta. A
# igualdade com os paths do documento garante (c): uma rota coberta que sair
# da spec, ou uma rota nova sem cobertura, quebram o teste.
COVERED = {
    ("post", "/api/feedbacks"),
    ("get", "/api/feedbacks"),
    ("get", "/api/feedbacks/{feedback_id}"),
    ("patch", "/api/feedbacks/{feedback_id}"),
    ("post", "/api/feedbacks/{feedback_id}/reanalyze"),
    ("post", "/api/feedbacks/reanalyze-pending"),
    ("get", "/api/stats"),
    ("get", "/auth/session"),
    ("post", "/login"),
    ("post", "/logout"),
    ("get", "/logout"),
    ("get", "/health"),
    ("get", "/api/settings/providers"),
}


@pytest.fixture(scope="module")
def spec():
    with open(SPEC_PATH, encoding="utf-8") as handle:
        return yaml.safe_load(handle)


SPEC_URI = "urn:feedlens-openapi"


@pytest.fixture(scope="module")
def registry(spec):
    """Registry que resolve os ``$ref`` internos (#/components/...) da spec."""
    resource = Resource(contents=spec, specification=DRAFT202012)
    return Registry().with_resource(uri=SPEC_URI, resource=resource)


def assert_matches(spec, registry, method, path, status, body):
    """Valida ``body`` contra o schema da resposta (metodo, path, status)."""
    try:
        operation = spec["paths"][path][method]
    except KeyError:  # pragma: no cover - erro de configuracao do proprio teste
        pytest.fail(f"spec nao cobre {method.upper()} {path}")
    responses = operation["responses"]
    assert str(status) in responses, (
        f"{method.upper()} {path} devolveu {status}, nao declarado na spec"
    )
    content = responses[str(status)].get("content")
    if content is None:
        assert body in (None, b"", ""), f"{method.upper()} {path} {status}: corpo inesperado"
        return
    schema = content["application/json"]["schema"]
    # A spec esta registrada sob SPEC_URI; apontar o $ref de topo para la faz
    # o validador resolver este schema e todos os #/components aninhados contra
    # o documento inteiro (a base da resource e SPEC_URI).
    if "$ref" in schema and schema["$ref"].startswith("#/"):
        schema = {"$ref": SPEC_URI + schema["$ref"]}
    validator = Draft202012Validator(schema, registry=registry)
    error = best_match(validator.iter_errors(body))
    assert error is None, (
        f"{method.upper()} {path} {status}: corpo fora do contrato -> {error.message}"
    )


# --------------------------------------------------------------- (a) spec valida

def test_spec_is_valid_openapi(spec):
    validate_spec(spec)
    assert spec["openapi"].startswith("3.1")


def test_spec_covers_the_expected_routes(spec):
    """(c) as rotas cobertas sao exatamente as que a spec declara."""
    in_spec = {
        (method, path)
        for path, item in spec["paths"].items()
        for method in item
        if method in {"get", "post", "patch", "delete", "put"}
    }
    missing_from_spec = COVERED - in_spec
    assert not missing_from_spec, f"cobertas mas fora da spec: {missing_from_spec}"
    not_covered = in_spec - COVERED
    # Rotas na spec sem cobertura de corpo aqui: aceitaveis (login exige front,
    # provedores exigem o cofre), mas listadas para quem le a falha.
    assert not_covered == {
        ("delete", "/api/settings/providers/{credential_id}"),
        ("patch", "/api/settings/providers/{credential_id}"),
        ("post", "/api/settings/providers"),
        ("post", "/api/settings/providers/reorder"),
        ("post", "/api/settings/providers/{credential_id}/test"),
    }, f"cobertura divergente: {not_covered}"


# ------------------------------------------------------- fixtures de app/clientes

@pytest.fixture()
def open_client():
    """App sem senha (API aberta), tabelas criadas, LLM nunca chamada de verdade."""
    application = create_app()
    application.config.update(TESTING=True)
    ctx = application.app_context()
    ctx.push()
    db.create_all()
    yield application.test_client()
    db.session.remove()
    db.drop_all()
    ctx.pop()


@pytest.fixture()
def auth_client():
    """App com DASHBOARD_PASSWORD; devolve (client, logado=False)."""
    application = create_app()
    application.config.update(TESTING=True, DASHBOARD_PASSWORD=PASSWORD)
    ctx = application.app_context()
    ctx.push()
    db.create_all()
    yield application.test_client()
    db.session.remove()
    db.drop_all()
    ctx.pop()


def _ingest(client, feedback_id, text, analysis, **kwargs):
    with patch(
        "app.routes.api.feedback_analyzer.analyze_feedback",
        return_value=analysis,
    ):
        return client.post(
            "/api/feedbacks", json={"id": feedback_id, "feedback": text}, **kwargs
        )


# ------------------------------------------------------------- (b) corpo + status

def test_health_contract(spec, registry, open_client):
    resp = open_client.get("/health")
    assert resp.status_code == 200
    assert_matches(spec, registry, "get", "/health", 200, resp.get_json())


def test_session_contract(spec, registry, open_client):
    resp = open_client.get("/auth/session")
    assert resp.status_code == 200
    assert_matches(spec, registry, "get", "/auth/session", 200, resp.get_json())


def test_logout_contract(spec, registry, open_client):
    for method in ("post", "get"):
        resp = getattr(open_client, method)("/logout")
        assert resp.status_code == 302
        assert_matches(spec, registry, method, "/logout", 302, None)


def test_ingest_created_contract(spec, registry, open_client):
    resp = _ingest(
        open_client, "11111111-1111-1111-1111-111111111111", "otimo", POSITIVE
    )
    assert resp.status_code == 201
    assert_matches(spec, registry, "post", "/api/feedbacks", 201, resp.get_json())


def test_ingest_bad_request_contract(spec, registry, open_client):
    resp = open_client.post("/api/feedbacks", json={})
    assert resp.status_code == 400
    assert_matches(spec, registry, "post", "/api/feedbacks", 400, resp.get_json())


def test_ingest_conflict_contract(spec, registry, open_client):
    fid = "22222222-2222-2222-2222-222222222222"
    _ingest(open_client, fid, "primeiro", POSITIVE)
    resp = _ingest(open_client, fid, "duplicado", POSITIVE)
    assert resp.status_code == 409
    assert_matches(spec, registry, "post", "/api/feedbacks", 409, resp.get_json())


def test_list_contract(spec, registry, open_client):
    _ingest(open_client, "33333333-3333-3333-3333-333333333333", "bom", POSITIVE)
    resp = open_client.get("/api/feedbacks?page=1&per_page=20")
    assert resp.status_code == 200
    assert_matches(spec, registry, "get", "/api/feedbacks", 200, resp.get_json())


def test_list_bad_request_contract(spec, registry, open_client):
    resp = open_client.get("/api/feedbacks?spam=invalido")
    assert resp.status_code == 400
    assert_matches(spec, registry, "get", "/api/feedbacks", 400, resp.get_json())


def test_get_one_contract(spec, registry, open_client):
    fid = "44444444-4444-4444-4444-444444444444"
    _ingest(open_client, fid, "detalhe", WITH_FEATURE)
    resp = open_client.get(f"/api/feedbacks/{fid}")
    assert resp.status_code == 200
    assert_matches(
        spec, registry, "get", "/api/feedbacks/{feedback_id}", 200, resp.get_json()
    )


def test_get_one_bad_uuid_contract(spec, registry, open_client):
    resp = open_client.get("/api/feedbacks/nao-e-uuid")
    assert resp.status_code == 400
    assert_matches(
        spec, registry, "get", "/api/feedbacks/{feedback_id}", 400, resp.get_json()
    )


def test_get_one_not_found_contract(spec, registry, open_client):
    resp = open_client.get("/api/feedbacks/55555555-5555-5555-5555-555555555555")
    assert resp.status_code == 404
    assert_matches(
        spec, registry, "get", "/api/feedbacks/{feedback_id}", 404, resp.get_json()
    )


def test_stats_empty_contract(spec, registry, open_client):
    resp = open_client.get("/api/stats")
    assert resp.status_code == 200
    assert_matches(spec, registry, "get", "/api/stats", 200, resp.get_json())


def test_stats_with_data_contract(spec, registry, open_client):
    _ingest(open_client, "66666666-6666-6666-6666-666666666666", "otimo", POSITIVE)
    _ingest(open_client, "77777777-7777-7777-7777-777777777777", "falta algo", WITH_FEATURE)
    resp = open_client.get("/api/stats")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["has_data"] is True
    assert_matches(spec, registry, "get", "/api/stats", 200, body)


def test_patch_review_contract(spec, registry, auth_client):
    """PATCH exige sessao + CSRF; valida 200 e o 401 sem sessao."""
    fid = "88888888-8888-8888-8888-888888888888"
    login(auth_client, PASSWORD)
    _ingest(auth_client, fid, "revisar", POSITIVE)
    token = csrf_token(auth_client)
    resp = auth_client.patch(
        f"/api/feedbacks/{fid}", json={"is_spam": True}, headers={"X-CSRF-Token": token}
    )
    assert resp.status_code == 200
    assert_matches(
        spec, registry, "patch", "/api/feedbacks/{feedback_id}", 200, resp.get_json()
    )


def test_patch_unauthenticated_contract(spec, registry, auth_client):
    fid = "99999999-9999-9999-9999-999999999999"
    resp = auth_client.patch(f"/api/feedbacks/{fid}", json={"is_spam": True})
    assert resp.status_code == 401
    assert_matches(
        spec, registry, "patch", "/api/feedbacks/{feedback_id}", 401, resp.get_json()
    )


def test_reanalyze_one_contract(spec, registry, auth_client):
    """POST /reanalyze: 200 quando a IA (mockada) responde."""
    fid = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    login(auth_client, PASSWORD)
    # Grava como nao analisado (failure), depois reanalisa com sucesso.
    _ingest(auth_client, fid, "reanalisar", {"failure": "timeout"})
    token = csrf_token(auth_client)
    with patch(
        "app.routes.api.feedback_analyzer.analyze_feedback",
        return_value=POSITIVE,
    ):
        resp = auth_client.post(
            f"/api/feedbacks/{fid}/reanalyze", headers={"X-CSRF-Token": token}
        )
    assert resp.status_code == 200
    assert_matches(
        spec,
        registry,
        "post",
        "/api/feedbacks/{feedback_id}/reanalyze",
        200,
        resp.get_json(),
    )


def test_reanalyze_pending_contract(spec, registry, auth_client):
    """POST /reanalyze-pending: 200 com o resumo do lote (LLM mockada)."""
    login(auth_client, PASSWORD)
    _ingest(
        auth_client, "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", "pendente", {"failure": "timeout"}
    )
    token = csrf_token(auth_client)
    with patch(
        "app.routes.api.feedback_analyzer.analyze_feedback",
        return_value=POSITIVE,
    ):
        resp = auth_client.post(
            "/api/feedbacks/reanalyze-pending",
            json={"limit": 5},
            headers={"X-CSRF-Token": token},
        )
    assert resp.status_code == 200
    assert_matches(
        spec, registry, "post", "/api/feedbacks/reanalyze-pending", 200, resp.get_json()
    )


def test_reanalyze_pending_bad_limit_contract(spec, registry, auth_client):
    login(auth_client, PASSWORD)
    token = csrf_token(auth_client)
    resp = auth_client.post(
        "/api/feedbacks/reanalyze-pending",
        json={"limit": 999},
        headers={"X-CSRF-Token": token},
    )
    assert resp.status_code == 400
    assert_matches(
        spec, registry, "post", "/api/feedbacks/reanalyze-pending", 400, resp.get_json()
    )


def test_providers_auth_not_configured_contract(spec, registry, open_client):
    """Sem DASHBOARD_PASSWORD, GET providers responde 403 no formato Error."""
    resp = open_client.get("/api/settings/providers")
    assert resp.status_code == 403
    assert_matches(spec, registry, "get", "/api/settings/providers", 403, resp.get_json())


def test_providers_list_contract(spec, registry, auth_client):
    """Logado e com o cofre desligado, GET providers devolve o estado base."""
    login(auth_client, PASSWORD)
    resp = auth_client.get("/api/settings/providers")
    assert resp.status_code == 200
    assert_matches(spec, registry, "get", "/api/settings/providers", 200, resp.get_json())
