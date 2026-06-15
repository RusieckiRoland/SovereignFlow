from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_web_client_javascript_has_valid_syntax() -> None:
    script = ROOT / "sovereignflow/interfaces/web/assets/app.js"

    completed = subprocess.run(
        ["node", "--check", str(script)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr


def test_keycloak_realm_exposes_pkce_web_client_without_password_grant() -> None:
    realm = json.loads(
        (ROOT / "infra/keycloak/sovereignflow-realm.json").read_text(encoding="utf-8")
    )
    client = next(
        item for item in realm["clients"] if item["clientId"] == "sovereignflow-web-client"
    )

    assert client["publicClient"] is True
    assert client["standardFlowEnabled"] is True
    assert client["directAccessGrantsEnabled"] is False
    assert client["attributes"]["pkce.code.challenge.method"] == "S256"
    assert "http://127.0.0.1:8000/app/" in client["redirectUris"]
    assert any(
        mapper["config"].get("included.client.audience") == "sovereignflow-api"
        for mapper in client["protocolMappers"]
    )
