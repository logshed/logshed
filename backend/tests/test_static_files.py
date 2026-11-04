"""Serving the built frontend: precompressed copies, caching headers and conditional requests."""

import gzip
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

import app.main as main_mod
from app.core.migrations import run_migrations
from app.core.static_files import IMMUTABLE_CACHE, REVALIDATE_CACHE, accepted_encodings

ASSET = "index-abc123.js"
ASSET_BODY = b"console.log('plain');\n" * 50
ASSET_BR = b"pretend brotli bytes"
INDEX_BODY = b"<!doctype html><html><body><div id='root'></div></body></html>\n" * 10


def vary(res) -> set[str]:
    """The header names in Vary (the CORS middleware adds Origin)."""
    return {name.strip().lower() for name in res.headers.get("vary", "").split(",") if name.strip()}


@pytest.fixture
def static_dir(tmp_path: Path) -> Path:
    """A built frontend with Brotli and gzip copies, as frontend/vite.config.ts writes them."""
    root = tmp_path / "static"
    (root / "assets").mkdir(parents=True)
    (root / "assets" / ASSET).write_bytes(ASSET_BODY)
    (root / "assets" / (ASSET + ".br")).write_bytes(ASSET_BR)
    (root / "assets" / (ASSET + ".gz")).write_bytes(gzip.compress(ASSET_BODY))
    (root / "assets" / "small-def456.js").write_bytes(b"export{};\n")
    (root / "index.html").write_bytes(INDEX_BODY)
    (root / "index.html.gz").write_bytes(gzip.compress(INDEX_BODY))
    (root / "favicon.svg").write_bytes(b"<svg xmlns='http://www.w3.org/2000/svg'/>")
    return root


@pytest_asyncio.fixture
async def client(static_dir: Path, tmp_path: Path, monkeypatch):
    db_file = tmp_path / "logs.db"
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DB_PATH", str(db_file))
    run_migrations(db_file)
    monkeypatch.setattr(main_mod, "STATIC_DIR", static_dir)
    app = main_mod.create_app.uncached()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


def test_accepted_encodings_reads_quality_values():
    assert accepted_encodings("gzip, deflate, br, zstd") == {"gzip", "deflate", "br", "zstd"}
    assert accepted_encodings("gzip;q=1.0, br;q=0") == {"gzip"}
    assert accepted_encodings("BR ; Q=0.5") == {"br"}
    assert accepted_encodings("") == set()
    assert accepted_encodings("identity") == {"identity"}
    assert accepted_encodings("*") == {"br", "gzip"}
    assert accepted_encodings("*;q=0.1, br;q=0") == {"gzip"}
    assert accepted_encodings("gzip;q=bad") == set()


@pytest.mark.asyncio
async def test_hashed_asset_is_sent_as_brotli_and_kept_for_a_year(client: AsyncClient):
    res = await client.get(f"/assets/{ASSET}", headers={"Accept-Encoding": "gzip, deflate, br"})
    assert res.status_code == 200
    assert res.headers["content-encoding"] == "br"
    assert res.headers["content-type"].startswith("text/javascript")
    assert "accept-encoding" in vary(res)
    assert res.headers["cache-control"] == IMMUTABLE_CACHE
    assert res.headers["content-length"] == str(len(ASSET_BR))


@pytest.mark.asyncio
async def test_hashed_asset_falls_back_to_gzip_then_to_the_plain_file(client: AsyncClient):
    gz = await client.get(f"/assets/{ASSET}", headers={"Accept-Encoding": "gzip, deflate"})
    assert gz.headers["content-encoding"] == "gzip"
    assert gz.content == ASSET_BODY  # httpx decodes gzip
    assert gz.headers["cache-control"] == IMMUTABLE_CACHE

    plain = await client.get(f"/assets/{ASSET}", headers={"Accept-Encoding": "identity"})
    assert "content-encoding" not in plain.headers
    assert plain.content == ASSET_BODY
    assert "accept-encoding" in vary(plain)
    assert plain.headers["cache-control"] == IMMUTABLE_CACHE

    refused = await client.get(f"/assets/{ASSET}", headers={"Accept-Encoding": "br;q=0, gzip;q=0"})
    assert "content-encoding" not in refused.headers
    assert refused.content == ASSET_BODY


@pytest.mark.asyncio
async def test_each_copy_has_its_own_etag_and_answers_304(client: AsyncClient):
    br = await client.get(f"/assets/{ASSET}", headers={"Accept-Encoding": "br"})
    plain = await client.get(f"/assets/{ASSET}", headers={"Accept-Encoding": "identity"})
    assert br.headers["etag"] != plain.headers["etag"]

    again = await client.get(
        f"/assets/{ASSET}", headers={"Accept-Encoding": "br", "If-None-Match": br.headers["etag"]}
    )
    assert again.status_code == 304
    assert again.headers["cache-control"] == IMMUTABLE_CACHE
    assert "accept-encoding" in vary(again)


@pytest.mark.asyncio
async def test_file_without_compressed_copies_has_no_vary(client: AsyncClient):
    res = await client.get("/assets/small-def456.js", headers={"Accept-Encoding": "br, gzip"})
    assert res.status_code == 200
    assert "content-encoding" not in res.headers
    assert "accept-encoding" not in vary(res)
    assert res.headers["cache-control"] == IMMUTABLE_CACHE


@pytest.mark.asyncio
async def test_missing_asset_is_404_without_long_caching(client: AsyncClient):
    res = await client.get("/assets/index-old999.js")
    assert res.status_code == 404
    assert "cache-control" not in res.headers


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/", "/rules", "/index.html"])
async def test_index_html_is_revalidated_and_compressed(client: AsyncClient, path: str):
    res = await client.get(path, headers={"Accept-Encoding": "gzip, br"})
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/html")
    assert res.headers["cache-control"] == REVALIDATE_CACHE
    # Only a gzip copy exists for index.html in this build
    assert res.headers["content-encoding"] == "gzip"
    assert res.content == INDEX_BODY

    again = await client.get(path, headers={"Accept-Encoding": "gzip, br", "If-None-Match": res.headers["etag"]})
    assert again.status_code == 304
    assert again.headers["cache-control"] == REVALIDATE_CACHE


@pytest.mark.asyncio
async def test_other_unhashed_files_are_revalidated(client: AsyncClient):
    res = await client.get("/favicon.svg")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("image/svg+xml")
    assert res.headers["cache-control"] == REVALIDATE_CACHE


@pytest.mark.asyncio
@pytest.mark.parametrize("path", [f"/assets/{ASSET}", "/", "/rules"])
async def test_security_headers_apply_to_static_files_and_304s(client: AsyncClient, path: str):
    first = await client.get(path, headers={"Accept-Encoding": "br"})
    again = await client.get(path, headers={"Accept-Encoding": "br", "If-None-Match": first.headers["etag"]})
    for res in (first, again):
        assert res.headers["content-security-policy"] == main_mod._CONTENT_SECURITY_POLICY
        assert res.headers["x-frame-options"] == "DENY"
        assert res.headers["x-content-type-options"] == "nosniff"
        assert res.headers["referrer-policy"] == "strict-origin-when-cross-origin"


@pytest.mark.asyncio
async def test_api_responses_are_not_compressed_or_cached(client: AsyncClient):
    res = await client.get("/api/health", headers={"Accept-Encoding": "gzip, br"})
    assert "content-encoding" not in res.headers
    assert res.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_path_traversal_is_still_rejected(client: AsyncClient, static_dir: Path):
    (static_dir.parent / "secret.txt").write_text("not for the web")
    res = await client.get("/..%2fsecret.txt")
    assert res.status_code == 404
