"""
Unit and integration tests for LogShed version management and GHCR update checking.
"""

from unittest.mock import AsyncMock, patch
import pytest
from fastapi.testclient import TestClient

from app.version import (
    APP_VERSION,
    SemVer,
    compare_semver,
    is_newer_stable_version,
    parse_semver,
)
from app.services.version_service import (
    _extract_latest_stable_version,
    check_for_updates,
    clear_version_cache,
    is_deprecated_image_repo,
)
from app.main import create_app


@pytest.fixture(autouse=True)
def reset_version_service_env():
    clear_version_cache()
    yield
    clear_version_cache()


class TestSemVerUtilities:
    """Test suite for semantic version parsing and comparison."""

    def test_parse_valid_stable_versions(self):
        v1 = parse_semver("1.0.0")
        assert v1 == SemVer(1, 0, 0, None)
        assert v1.is_stable() is True

        v2 = parse_semver("v2.14.3")
        assert v2 == SemVer(2, 14, 3, None)
        assert v2.is_stable() is True

    def test_parse_prerelease_versions(self):
        v = parse_semver("1.1.0-beta.3")
        assert v == SemVer(1, 1, 0, "beta.3")
        assert v.is_stable() is False

        v2 = parse_semver("v1.0.0-rc.1")
        assert v2 == SemVer(1, 0, 0, "rc.1")
        assert v2.is_stable() is False

    def test_parse_invalid_versions_return_none(self):
        assert parse_semver("") is None
        assert parse_semver("latest") is None
        assert parse_semver("beta") is None
        assert parse_semver("v1.0") is None
        assert parse_semver("not-a-version") is None

    def test_compare_semver_numeric(self):
        v1 = parse_semver("1.0.0")
        v2 = parse_semver("1.1.0")
        v3 = parse_semver("2.0.0")

        assert compare_semver(v2, v1) == 1
        assert compare_semver(v1, v2) == -1
        assert compare_semver(v1, v1) == 0
        assert compare_semver(v3, v2) == 1

    def test_compare_stable_vs_prerelease_precedence(self):
        stable = parse_semver("1.1.0")
        beta = parse_semver("1.1.0-beta.3")

        # Stable 1.1.0 has higher precedence than 1.1.0-beta.3
        assert compare_semver(stable, beta) == 1
        assert compare_semver(beta, stable) == -1

    def test_compare_prerelease_sequence(self):
        beta1 = parse_semver("1.1.0-beta.1")
        beta2 = parse_semver("1.1.0-beta.2")
        beta3 = parse_semver("1.1.0-beta.3")

        assert compare_semver(beta2, beta1) == 1
        assert compare_semver(beta3, beta2) == 1
        assert compare_semver(beta1, beta3) == -1

    def test_is_newer_stable_version_filters_prereleases(self):
        # Betas must never trigger update_available
        assert is_newer_stable_version("1.2.0-beta.1", "1.1.0") is False
        assert is_newer_stable_version("1.1.0-beta.3", "1.1.0-beta.2") is False

        # Newer stable releases
        assert is_newer_stable_version("1.1.0", "1.0.0") is True
        assert is_newer_stable_version("1.1.0", "1.1.0-beta.3") is True
        assert is_newer_stable_version("1.0.0", "1.1.0-beta.3") is False
        assert is_newer_stable_version("1.1.0", "1.1.0") is False


class TestGHCRVersionService:
    """Test suite for GHCR registry queries and tag extraction."""

    def test_extract_latest_stable_version_ignores_betas_and_aliases(self):
        tags = [
            "1.0.0",
            "1.0",
            "latest",
            "1.1.0-beta.1",
            "beta",
            "1.1.0-beta.2",
            "1.1.0-beta.3",
        ]
        assert _extract_latest_stable_version(tags) == "1.0.0"

    def test_extract_latest_stable_version_picks_highest(self):
        tags = ["1.0.0", "1.1.0", "1.0.1", "1.2.0-beta.1", "latest"]
        assert _extract_latest_stable_version(tags) == "1.1.0"

    def test_extract_latest_stable_version_empty_when_no_stables(self):
        tags = ["latest", "beta", "1.1.0-beta.1"]
        assert _extract_latest_stable_version(tags) is None

    @pytest.mark.asyncio
    async def test_check_for_updates_available(self):
        with patch("app.services.version_service.APP_VERSION", "1.1.0-beta.3"):
            with patch("app.services.version_service.fetch_ghcr_tags", new_callable=AsyncMock) as mock_fetch:
                mock_fetch.return_value = ["1.0.0", "1.2.0", "1.1.0", "latest"]
                res = await check_for_updates(force_refresh=True)

                assert res["latest_version"] == "1.2.0"
                assert res["update_available"] is True
                assert res["current_version"] == "1.1.0-beta.3"

    @pytest.mark.asyncio
    async def test_check_for_updates_up_to_date(self):
        with patch("app.services.version_service.APP_VERSION", "1.1.0-beta.3"):
            with patch("app.services.version_service.fetch_ghcr_tags", new_callable=AsyncMock) as mock_fetch:
                # GHCR has 1.0.0, current is 1.1.0-beta.3
                mock_fetch.return_value = ["1.0.0", "latest", "1.1.0-beta.3"]
                res = await check_for_updates(force_refresh=True)

                assert res["latest_version"] == "1.0.0"
                assert res["update_available"] is False

    @pytest.mark.asyncio
    async def test_check_for_updates_handles_network_failure_gracefully(self):
        clear_version_cache()
        with patch("app.services.version_service.fetch_ghcr_tags", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.side_effect = Exception("Connection timeout")
            res = await check_for_updates(force_refresh=True)

            assert res["current_version"] == APP_VERSION
            assert res["latest_version"] is None
            assert res["update_available"] is False

    @pytest.mark.asyncio
    async def test_check_for_updates_returns_stale_cache_on_subsequent_failure(self):
        clear_version_cache()
        with patch("app.services.version_service.fetch_ghcr_tags", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = ["1.0.0", "1.2.0"]
            res = await check_for_updates(force_refresh=True)
            assert res["latest_version"] == "1.2.0"

        with patch("app.services.version_service.fetch_ghcr_tags", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.side_effect = Exception("Registry offline")
            stale_res = await check_for_updates(force_refresh=True)
            assert stale_res["latest_version"] == "1.2.0"

    @pytest.mark.asyncio
    async def test_check_for_updates_disabled_by_setting(self):
        clear_version_cache()
        with patch("app.services.version_service._is_update_check_enabled_in_db", return_value=False):
            with patch("app.api.deps.run_db_query", new_callable=AsyncMock) as mock_query:
                mock_query.return_value = False
                res = await check_for_updates(force_refresh=True)
                assert res["check_enabled"] is False
                assert res["update_available"] is False
                assert res["latest_version"] is None
                assert res["repo_deprecated"] is False

    def test_is_deprecated_image_repo(self):
        assert is_deprecated_image_repo("benhornertech/logshed") is True
        assert is_deprecated_image_repo("BenHornerTech/logshed") is True
        assert is_deprecated_image_repo("logshed/logshed") is False
        assert is_deprecated_image_repo("other/logshed") is False

    @pytest.mark.asyncio
    async def test_check_for_updates_detects_deprecated_repo_and_queries_logshed(self):
        clear_version_cache()
        with patch("app.services.version_service.fetch_ghcr_tags", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = ["1.0.0", "1.2.0"]
            res = await check_for_updates(force_refresh=True, image_repo="benhornertech/logshed")
            assert res["repo_deprecated"] is True
            # Ensures query falls back to official logshed/logshed repository
            mock_fetch.assert_called_once_with(image_repo="logshed/logshed")



    @pytest.mark.asyncio
    async def test_check_for_updates_enforces_minimum_interval_between_live_queries(self):
        with patch("app.services.version_service.fetch_ghcr_tags", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = ["1.0.0", "1.2.0"]
            res1 = await check_for_updates(force_refresh=True)
            assert res1["latest_version"] == "1.2.0"
            assert mock_fetch.call_count == 1

            # Second call within 60 seconds should NOT trigger another live fetch
            res2 = await check_for_updates(force_refresh=True)
            assert res2["latest_version"] == "1.2.0"
            assert mock_fetch.call_count == 1


class TestVersionEndpoint:
    """Test suite for /api/system/version API route."""

    def test_get_version_endpoint_returns_200(self):
        app = create_app()
        client = TestClient(app)

        with patch("app.api.system.check_for_updates", new_callable=AsyncMock) as mock_check:
            mock_check.return_value = {
                "current_version": "1.1.0-beta.3",
                "latest_version": "1.2.0",
                "update_available": True,
                "checked_at": 1700000000.0,
                "repo_deprecated": True,
            }

            response = client.get("/api/system/version")
            assert response.status_code == 200
            data = response.json()
            assert data["current_version"] == "1.1.0-beta.3"
            assert data["latest_version"] == "1.2.0"
            assert data["update_available"] is True
            assert data["repo_deprecated"] is True

    def test_unauthenticated_refresh_returns_401(self):
        app = create_app()
        client = TestClient(app)
        response = client.get("/api/system/version?refresh=true")
        assert response.status_code == 401
        assert "Authentication required" in response.json()["detail"]

    def test_authenticated_refresh_returns_200(self):
        from app.api.deps import get_optional_user
        app = create_app()
        app.dependency_overrides[get_optional_user] = lambda: {"user_id": 1}
        client = TestClient(app)

        with patch("app.api.system.check_for_updates", new_callable=AsyncMock) as mock_check:
            mock_check.return_value = {
                "current_version": "1.1.0-beta.3",
                "latest_version": "1.2.0",
                "update_available": True,
                "checked_at": 1700000000.0,
                "repo_deprecated": False,
            }
            response = client.get("/api/system/version?refresh=true")
            assert response.status_code == 200
