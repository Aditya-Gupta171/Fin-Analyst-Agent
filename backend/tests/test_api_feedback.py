"""An analyst confirming or dismissing a finding: the finding's stored status updates and a feedback
row is recorded — the audit trail step 6's learning service will read to calibrate rule confidence."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.main import build_app
from app.engine.catalog import Catalog
from app.settings import Settings
from tests.api_seed import seed_document
from tests.builders import load_fixture


@pytest.fixture
def client(tmp_path: Path, catalog: Catalog) -> Iterator[TestClient]:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"
    seed_document(database_url, load_fixture("annual_report_manufacturing"))
    settings = Settings(database_url=database_url)
    app = build_app(settings, catalog=catalog, kb=None, gateway=None, eager_jobs=True)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def analysis_id(client: TestClient) -> str:
    document_id = client.get("/documents").json()[0]["id"]
    return client.post(f"/documents/{document_id}/analyses").json()["id"]


def test_confirming_a_finding_updates_its_status_and_records_feedback(
    client: TestClient, analysis_id: str
) -> None:
    findings = client.get(f"/analyses/{analysis_id}/findings").json()
    finding = findings[0]
    assert finding["status"] == "open"

    response = client.post(
        f"/analyses/{analysis_id}/findings/{finding['finding_id']}/feedback",
        json={
            "verdict": "confirm",
            "reason": "Checked against the filing, agreed.",
            "created_by": "analyst_1",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "confirmed"

    refreshed = client.get(f"/analyses/{analysis_id}/findings").json()
    assert next(f for f in refreshed if f["finding_id"] == finding["finding_id"])["status"] == "confirmed"


def test_dismissing_a_finding(client: TestClient, analysis_id: str) -> None:
    finding = client.get(f"/analyses/{analysis_id}/findings").json()[0]
    response = client.post(
        f"/analyses/{analysis_id}/findings/{finding['finding_id']}/feedback",
        json={"verdict": "dismiss", "reason": "Benign once seasonality is accounted for."},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "dismissed"


def test_feedback_on_unknown_finding_is_404(client: TestClient, analysis_id: str) -> None:
    response = client.post(
        f"/analyses/{analysis_id}/findings/does-not-exist/feedback", json={"verdict": "confirm"}
    )
    assert response.status_code == 404
