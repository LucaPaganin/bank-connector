"""Flask routes with injected mock dependencies."""
import json
from unittest.mock import MagicMock

import pytest

from bank_connector.web import create_app
from helpers import raw_txn


@pytest.fixture
def deps():
    config_repo = MagicMock()
    state_repo = MagicMock()
    eb_client = MagicMock()
    sync_service = MagicMock()
    return config_repo, state_repo, eb_client, sync_service


@pytest.fixture
def client(deps):
    config_repo, state_repo, eb_client, sync_service = deps
    app = create_app(
        config_repo=config_repo,
        state_repo=state_repo,
        eb_client=eb_client,
        sync_service=sync_service,
    )
    app.config.update(TESTING=True)
    return app.test_client()


def test_status_json(client, deps):
    config_repo, state_repo, *_ = deps
    config_repo.load.return_value = {"accounts": [{"id": 1, "actual_account": "Main"}]}
    state_repo.load.return_value = {"last_run": "2026-05-16T10:00:00"}
    r = client.get("/", headers={"Accept": "application/json"})
    assert r.status_code == 200
    assert r.json["last_run"] == "2026-05-16T10:00:00"


def test_banks(client, deps):
    *_, eb_client, _ = deps
    eb_client.list_banks.return_value = [{"name": "Revolut", "country": "IT"}]
    assert client.get("/banks").json == [{"name": "Revolut", "country": "IT"}]


def test_transactions_404(client, deps):
    config_repo, *_ = deps
    config_repo.load.return_value = {"accounts": []}
    assert client.get("/transactions/99").status_code == 404


def test_transactions_bad_date(client, deps):
    config_repo, *_ = deps
    config_repo.load.return_value = {"accounts": [{"id": 1, "account_uid": "u"}]}
    assert client.get("/transactions/1?date_from=nope").status_code == 400


def test_transactions_success(client, deps):
    config_repo, _s, eb_client, _ = deps
    config_repo.load.return_value = {"accounts": [{"id": 1, "account_uid": "u"}]}
    eb_client.fetch_transactions.return_value = [{"id": 1}]
    r = client.get("/transactions/1")
    assert r.json["count"] == 1


def test_connect_action_persists_pending_oauth(client, deps):
    config_repo, state_repo, eb_client, _ = deps
    eb_client.start_auth.return_value = ("state-xyz", "2026-11-01T00:00:00Z", "https://bank/go")
    state_repo.load.return_value = {}
    r = client.get(
        "/connect?bank_name=Revolut&country=IT&actual_account=Main",
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert r.headers["Location"] == "https://bank/go"
    saved = state_repo.save.call_args[0][0]
    assert saved["pending_oauth"]["state-xyz"]["bank_name"] == "Revolut"


def test_callback_missing_params(client):
    assert client.get("/callback").status_code == 400


def test_callback_unknown_state(client, deps):
    _c, state_repo, *_ = deps
    state_repo.load.return_value = {"pending_oauth": {}}
    assert client.get("/callback?code=c&state=unknown").status_code == 400


def test_callback_success_appends_account(client, deps):
    config_repo, state_repo, eb_client, _ = deps
    state_repo.load.return_value = {
        "pending_oauth": {
            "st": {
                "bank_name": "Revolut",
                "country": "IT",
                "actual_account": "Main",
                "start_sync_date": "2026-05-01",
                "valid_until": "2026-11-01T00:00:00Z",
            }
        }
    }
    eb_client.complete_auth.return_value = {
        "session_id": "sess",
        "accounts": [{"uid": "acc-uid", "name": "Checking"}],
    }
    config_repo.load.return_value = {"accounts": []}
    r = client.get("/callback?code=c&state=st", headers={"Accept": "application/json"})
    assert r.status_code == 200
    saved_cfg = config_repo.save.call_args[0][0]
    assert saved_cfg["accounts"][0]["account_uid"] == "acc-uid"
    assert saved_cfg["accounts"][0]["session_id"] == "sess"


def test_manual_sync_starts_thread(client, deps, monkeypatch):
    started = {}

    class FakeThread:
        def __init__(self, *a, **k):
            started["target"] = k.get("target")

        def start(self):
            started["ran"] = True

    monkeypatch.setattr("bank_connector.web.threading.Thread", FakeThread)
    r = client.post("/sync")
    assert r.json == {"started": True}
    assert started["ran"] is True


def test_export_json(client, deps):
    config_repo, _s, eb_client, _ = deps
    config_repo.load.return_value = {"accounts": [{"id": 1, "account_uid": "u"}]}
    eb_client.fetch_transactions.return_value = [raw_txn()]
    r = client.get("/export/1?format=json")
    assert r.status_code == 200
    assert r.mimetype == "application/json"
    assert json.loads(r.data)["count"] == 1


def test_export_csv(client, deps):
    config_repo, _s, eb_client, _ = deps
    config_repo.load.return_value = {
        "accounts": [{"id": 1, "account_uid": "u"}],
        "account_holder_name": "",
    }
    eb_client.fetch_transactions.return_value = [raw_txn()]
    r = client.get("/export/1")
    assert r.status_code == 200
    assert r.mimetype == "text/csv"
    assert "Date,Payee,Notes,Amount" in r.get_data(as_text=True)


def test_export_404(client, deps):
    config_repo, *_ = deps
    config_repo.load.return_value = {"accounts": []}
    assert client.get("/export/7").status_code == 404


def test_convert_valid_list(client, deps):
    config_repo, *_ = deps
    config_repo.load.return_value = {}
    r = client.post("/convert", data=json.dumps([raw_txn()]))
    assert r.status_code == 200
    assert r.mimetype == "text/csv"


def test_convert_object_with_transactions(client, deps):
    config_repo, *_ = deps
    config_repo.load.return_value = {}
    r = client.post("/convert", data=json.dumps({"transactions": [raw_txn()]}))
    assert r.status_code == 200


def test_convert_invalid_json(client, deps):
    config_repo, *_ = deps
    config_repo.load.return_value = {}
    assert client.post("/convert", data="not json").status_code == 400
