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
        self, account_uid: str, date_from: datetime.date
    ) -> list[dict]:
        headers = self._headers()
        params = {
            "date_from": date_from.isoformat(),
            "date_to": datetime.date.today().isoformat(),
        }
        url = f"{self.api_url}/accounts/{account_uid}/transactions"
        txns: list[dict] = []
        page = 0
        while url:
            if page > 0:
                time.sleep(1)
            for attempt in range(4):
                r = requests.get(url, headers=headers, params=params, timeout=self.timeout)
                if r.status_code == 429:
                    wait = min(2**attempt * 5, 60)
                    log.warning("Rate limited (429), retrying in %ds", wait)
                    time.sleep(wait)
                    continue
                break
            r.raise_for_status()
            data = r.json()
            txns.extend(data.get("transactions", []))
            ck = data.get("continuation_key")
            url = (
                f"{self.api_url}/accounts/{account_uid}/transactions" if ck else None
            )
            params = {"continuation_key": ck} if ck else {}
            page += 1
        log.info("Fetched %d transactions for %s", len(txns), account_uid)
        return txns
