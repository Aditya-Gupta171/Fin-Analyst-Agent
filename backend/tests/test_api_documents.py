"""Document upload and lookup, exercised through the real ingestion pipeline (a genuine NSE XBRL filing)
against an app wired to a throwaway SQLite database — no network calls, since ``kb`` and ``gateway`` are
both explicitly disabled for these tests (only ingestion, not analysis, is under test here)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.main import build_app
from app.engine.catalog import Catalog
from app.settings import Settings

XBRL_FIXTURE = Path(__file__).parent / "fixtures" / "xbrl" / "nocil_q2fy25_standalone.xml"


@pytest.fixture
def client(tmp_path: Path, catalog: Catalog) -> Iterator[TestClient]:
    settings = Settings(database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    app = build_app(settings, catalog=catalog, kb=None, gateway=None, eager_jobs=True)
    with TestClient(app) as test_client:
        yield test_client


def test_upload_ingests_a_real_filing_and_the_document_is_then_listable_and_fetchable(
    client: TestClient,
) -> None:
    content = XBRL_FIXTURE.read_bytes()
    response = client.post(
        "/documents",
        files={"file": ("nocil.xml", content, "application/xml")},
        data={"sector": "chemicals"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    document = body["document"]
    assert document["company_name"]
    assert document["periods"]
    document_id = document["id"]

    listing = client.get("/documents")
    assert listing.status_code == 200
    assert any(d["id"] == document_id for d in listing.json())

    detail = client.get(f"/documents/{document_id}")
    assert detail.status_code == 200
    detail_body = detail.json()
    assert detail_body["fact_count"] > 0
    assert detail_body["dataset"]["facts"]

    raw = client.get(f"/documents/{document_id}/file")
    assert raw.status_code == 200
    assert raw.content == content
    # only PDFs render inline; anything else downloads, so uploaded bytes can never run as a page here
    assert raw.headers["content-type"] == "application/octet-stream"
    assert raw.headers["content-disposition"] == "attachment"
    assert raw.headers["x-content-type-options"] == "nosniff"


def test_upload_rejects_a_file_no_parser_understands(client: TestClient) -> None:
    response = client.post("/documents", files={"file": ("notes.txt", b"just some text", "text/plain")})
    assert response.status_code == 422
    assert "expected a PDF" in response.json()["detail"]


def test_get_unknown_document_is_404(client: TestClient) -> None:
    assert client.get("/documents/does-not-exist").status_code == 404


def test_get_unknown_document_file_is_404(client: TestClient) -> None:
    assert client.get("/documents/does-not-exist/file").status_code == 404


def test_upload_larger_than_the_limit_is_refused(tmp_path: Path, catalog: Catalog) -> None:
    settings = Settings(database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}", max_upload_mb=1)
    app = build_app(settings, catalog=catalog, kb=None, gateway=None, eager_jobs=True)
    with TestClient(app) as client:
        big = b"<" + b" " * (2 * 1024 * 1024)
        response = client.post("/documents", files={"file": ("big.xml", big, "application/xml")})
    assert response.status_code == 413


@pytest.fixture
def keyed_client(tmp_path: Path, catalog: Catalog) -> Iterator[TestClient]:
    settings = Settings(database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}", api_key="s3cret")
    app = build_app(settings, catalog=catalog, kb=None, gateway=None, eager_jobs=True)
    with TestClient(app) as test_client:
        yield test_client


def _upload(client: TestClient, headers: dict | None = None):
    return client.post(
        "/documents",
        files={"file": ("nocil.xml", XBRL_FIXTURE.read_bytes(), "application/xml")},
        headers=headers or {},
    )


def test_with_an_api_key_mutating_requests_need_it(keyed_client: TestClient) -> None:
    assert _upload(keyed_client).status_code == 401
    assert _upload(keyed_client, {"Authorization": "Bearer wrong"}).status_code == 401
    assert _upload(keyed_client, {"Authorization": "Bearer s3cret"}).status_code == 201


def test_with_an_api_key_reads_stay_open(keyed_client: TestClient) -> None:
    # EventSource (live progress) and the PDF viewer can't send an Authorization header
    document_id = _upload(keyed_client, {"Authorization": "Bearer s3cret"}).json()["document"]["id"]
    assert keyed_client.get("/documents").status_code == 200
    assert keyed_client.get(f"/documents/{document_id}/file").status_code == 200
