"""Enable Banking HTTP client.

One class, one responsibility: talk to the Enable Banking API. Authentication
(RS256 JWT, signed per request with a 1 h expiry) is handled internally.
"""
import datetime
import logging
import time
import uuid
from pathlib import Path

import jwt as pyjwt
import requests
from cryptography.hazmat.primitives.serialization import load_pem_private_key

from bank_connector.settings import EB_API

log = logging.getLogger("connector")


class ConsentExpiredError(RuntimeError):
    """Enable Banking rejected a request because the consent/session expired."""


class EnableBankingClient:
    def __init__(
        self,
        *,
        application_id: str,
        pem_path: Path,
        redirect_url: str,
        api_url: str = EB_API,
        timeout: int = 30,
    ) -> None:
        self.application_id = application_id
        self.pem_path = Path(pem_path)
        self.redirect_url = redirect_url
        self.api_url = api_url.rstrip("/")
        self.timeout = timeout
        self._key = load_pem_private_key(self.pem_path.read_bytes(), password=None)

    def _headers(self) -> dict:
        now = int(time.time())
        payload = {
            "iss": "enablebanking.com",
            "aud": "api.enablebanking.com",
            "iat": now,
            "exp": now + 3600,
            "jti": str(uuid.uuid4()),
            "sub": self.application_id,
        }
        token = pyjwt.encode(
            payload, self._key, algorithm="RS256", headers={"kid": self.application_id}
        )
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    def start_auth(
        self, bank_name: str, country: str, psu_type: str = "personal"
    ) -> tuple[str, str, str]:
        state_val = str(uuid.uuid4())
        valid_until = time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 180 * 24 * 3600)
        )
        body = {
            "access": {"valid_until": valid_until},
            "aspsp": {"name": bank_name, "country": country},
            "state": state_val,
            "redirect_url": self.redirect_url,
            "psu_type": psu_type,
        }
        r = requests.post(
            f"{self.api_url}/auth", json=body, headers=self._headers(), timeout=self.timeout
        )
        r.raise_for_status()
        return state_val, valid_until, r.json()["url"]

    def complete_auth(self, code: str, state: str) -> dict:
        r = requests.post(
            f"{self.api_url}/sessions",
            json={"code": code, "state": state},
            headers=self._headers(),
            timeout=self.timeout,
        )
        r.raise_for_status()
        return r.json()

    def list_banks(self) -> list[dict]:
        r = requests.get(
            f"{self.api_url}/aspsps", headers=self._headers(), timeout=self.timeout
        )
        r.raise_for_status()
        return [
            {"name": b["name"], "country": b["country"]}
            for b in r.json().get("aspsps", [])
        ]

    def fetch_transactions(
        self,
        account_uid: str,
        date_from: datetime.date,
        date_to: datetime.date | None = None,
    ) -> list[dict]:
        headers = self._headers()
        end_date = date_to or datetime.date.today()
        url = f"{self.api_url}/accounts/{account_uid}/transactions"
        txns: list[dict] = []
        window_start = date_from

        # Some banks reject broad transaction ranges; keep every provider query
        # to 30 inclusive calendar days. A continuation key remains valid only
        # when the original date parameters are repeated on every page.
        while window_start <= end_date:
            window_end = min(window_start + datetime.timedelta(days=29), end_date)
            period_params = {
                "date_from": window_start.isoformat(),
                "date_to": window_end.isoformat(),
            }
            continuation_key = None
            page = 0

            while True:
                if page > 0:
                    time.sleep(1)
                params = {
                    **period_params,
                    **(
                        {"continuation_key": continuation_key}
                        if continuation_key
                        else {}
                    ),
                }
                r = None
                for attempt in range(4):
                    r = requests.get(url, headers=headers, params=params, timeout=self.timeout)
                    if r.status_code == 429:
                        wait = min(2**attempt * 5, 60)
                        log.warning("Rate limited (429), retrying in %ds", wait)
                        time.sleep(wait)
                        continue
                    break
                assert r is not None
                if r.status_code in (401, 403):
                    raise ConsentExpiredError(
                        "Enable Banking consent is expired or no longer valid"
                    )
                r.raise_for_status()
                data = r.json()
                txns.extend(data.get("transactions", []))
                continuation_key = data.get("continuation_key")
                if not continuation_key:
                    break
                page += 1

            window_start = window_end + datetime.timedelta(days=1)

        log.info("Fetched %d transactions for account=%s", len(txns), account_uid)
        return txns
