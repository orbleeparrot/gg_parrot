"""Edited signature blocks remain a supported file-origin classification."""
import secrets

import pytest
from fastapi.testclient import TestClient

from app import macro_signing
from app.engine import Macro
from app.main import app

MACRO = {
    "symbol": "BTCUSDT", "rule_type": "A", "position_side": "long",
    "params": {"take_profit_pct": 3.0, "initial_capital": 1000},
    "risk": {"invest_ratio": 0.5, "stop_loss_pct": 2.0},
    "period": {"preset": "3m"},
}


@pytest.mark.parametrize("value", ["변경", "가" * 64, "z" * 64, "0" * 63, None, 123, [], {}])
def test_malformed_hmac_is_classified_as_modified(value):
    macro = Macro.model_validate(MACRO)
    signature = {"v": 1, "hmac": value}
    assert not macro_signing.verify(macro, signature)
    assert macro_signing.classify_origin(macro, user_macro_id=None, sig=signature) == "file_modified"


def test_non_ascii_file_signature_does_not_fail_runner_start():
    with TestClient(app) as client:
        name = "sig_input_" + secrets.token_hex(4)
        signup = client.post("/api/auth/signup", json={
            "email": f"{name}@example.invalid", "username": name, "password": "password123",
        })
        assert signup.status_code == 200, signup.text
        token = signup.json()["token"]
        key = client.get("/api/me/runner/key", headers={"Authorization": f"Bearer {token}"}).json()["key"]
        response = client.post("/api/runner/start", headers={"X-Runner-Key": key}, json={
            "symbol": "BTCUSDT", "macro": MACRO, "testnet": True,
            "runner_version": "7", "macro_sig": {"v": 1, "hmac": "변경"},
        })
        assert response.status_code == 200, response.text
        assert response.json()["macro_origin"] == "file_modified"
