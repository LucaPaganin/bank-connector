"""EnableBankingClient with HTTP mocked via `responses`."""
import json

import jwt as pyjwt
import pytest
import responses

from bank_connector.enable_banking import EnableBankingClient

API = "https://api.test"


@pytest.fixture
def client(rsa_pem):
    pem_path, _pub = rsa_pem
    return EnableBankingClient(
        application_id="app-123",
        pem_path=pem_path,
        redirect_url="https://cb.test/callback",
        api_url=API + "/",  # trailing slash should be stripped
    )


def test_api_url_normalised(client):
    assert client.api_url == API


def test_headers_emit_valid_rs256_jwt(client, rsa_pem):
    _pem, pub = rsa_pem
    token = client._headers()["Authorization"].removeprefix("Bearer ")
    claims = pyjwt.decode(
        token, pub, algorithms=["RS256"], audience="api.enablebanking.com"
    )
    assert claims["iss"] == "enablebanking.com"
    assert claims["sub"] == "app-123"
    assert claims["exp"] - claims["iat"] == 3600
    assert pyjwt.get_unverified_header(token)["kid"] == "app-123"


@responses.activate
def test_start_auth(client):
    responses.add(
        responses.POST, f"{API}/auth", json={"url": "https://bank/authorize"}, status=200
    )
    state_val, valid_until, url = client.start_auth("Revolut", "IT")
    assert url == "https://bank/authorize"
    assert state_val and valid_until
    body = json.loads(responses.calls[0].request.body)
    assert body["aspsp"] == {"name": "Revolut", "country": "IT"}
    assert body["redirect_url"] == "https://cb.test/callback"
    assert body["state"] == state_val


@responses.activate
def test_complete_auth(client):
    responses.add(
        responses.POST,
        f"{API}/sessions",
        json={"session_id": "s1", "accounts": [{"uid": "u1"}]},
        status=200,
    )
    out = client.complete_auth("code-1", "state-1")
    assert out["session_id"] == "s1"


@responses.activate
def test_list_banks_maps_fields(client):
    responses.add(
        responses.GET,
        f"{API}/aspsps",
        json={"aspsps": [{"name": "Revolut", "country": "IT", "logo": "x"}]},
        status=200,
    )
    assert client.list_banks() == [{"name": "Revolut", "country": "IT"}]


@responses.activate
def test_fetch_transactions_paginates(client, monkeypatch):
    import bank_connector.enable_banking as eb

    monkeypatch.setattr(eb.time, "sleep", lambda *_: None)
    url = f"{API}/accounts/uid-1/transactions"
    responses.add(
        responses.GET,
        url,
        json={"transactions": [{"id": 1}], "continuation_key": "ck-1"},
        status=200,
    )
    responses.add(
        responses.GET, url, json={"transactions": [{"id": 2}]}, status=200
    )
    import datetime

    txns = client.fetch_transactions("uid-1", datetime.date(2026, 1, 1))
    assert [t["id"] for t in txns] == [1, 2]


@responses.activate
def test_fetch_transactions_retries_on_429(client, monkeypatch):
    import bank_connector.enable_banking as eb

    monkeypatch.setattr(eb.time, "sleep", lambda *_: None)
    url = f"{API}/accounts/uid-1/transactions"
    responses.add(responses.GET, url, status=429)
    responses.add(responses.GET, url, json={"transactions": [{"id": 9}]}, status=200)
    import datetime

    txns = client.fetch_transactions("uid-1", datetime.date(2026, 1, 1))
    assert [t["id"] for t in txns] == [9]
