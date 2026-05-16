"""Pytest fixtures shared across the suite."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_txns(name: str) -> list[dict]:
    data = json.loads((REPO_ROOT / name).read_text(encoding="utf-8"))
    return data["transactions"]


@pytest.fixture(scope="session")
def revolut_txns() -> list[dict]:
    return _load_txns("mytestrevolut.json")


@pytest.fixture(scope="session")
def fineco_txns() -> list[dict]:
    return _load_txns("mytestfineco.json")


@pytest.fixture
def rsa_pem(tmp_path: Path):
    """Generate an RSA keypair, write the private PEM, return (path, public_key)."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem_path = tmp_path / "private.pem"
    pem_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    return pem_path, key.public_key()
