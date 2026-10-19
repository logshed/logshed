"""
Tests for packaging, deployment, container privilege dropping, Unraid template, and GitHub Actions workflow.
"""

import os
import pathlib
from pathlib import Path
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
import pytest
import pytest_asyncio
import yaml
import httpx
from httpx import ASGITransport, AsyncClient

from app.core.migrations import run_migrations
from app.main import create_app

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
DOCKERFILE_PATH = REPO_ROOT / "Dockerfile"
ENTRYPOINT_PATH = REPO_ROOT / "entrypoint.sh"
UNRAID_TEMPLATE_PATH = REPO_ROOT / "unraid-template.xml"
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "build-publish.yml"


def is_docker_daemon_running() -> bool:
    try:
        res = subprocess.run(
            ["docker", "info"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        return res.returncode == 0
    except Exception:
        return False


# ===================================================================
# 1. Runtime Sanity & Skeleton
# ===================================================================

class TestRuntimeSanity:

    def test_python_version(self):
        assert sys.version_info >= (3, 12), f"Expected Python >= 3.12, got {sys.version_info}"

    def test_directory_skeleton(self):
        backend_dir = REPO_ROOT / "backend"
        app_dir = backend_dir / "app"

        expected_directories = [
            app_dir / "api",
            app_dir / "core",
            app_dir / "collectors",
            app_dir / "services",
            backend_dir / "tests",
        ]

        expected_files = [
            app_dir / "main.py",
            app_dir / "models.py",
        ]

        for d in expected_directories:
            assert d.is_dir(), f"Expected directory {d} to exist"

        for f in expected_files:
            assert f.is_file(), f"Expected file {f} to exist"


# ===================================================================
# 2. SPA Static File Serving
# ===================================================================

class TestSpaStaticServing:

    @pytest_asyncio.fixture
    async def client(self, tmp_path: Path, monkeypatch):
        db_file = tmp_path / "logs.db"
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        monkeypatch.setenv("DB_PATH", str(db_file))
        run_migrations(db_file)

        app = create_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"X-Requested-With": "XMLHttpRequest"},
        ) as ac:
            yield ac

    @pytest.mark.asyncio
    async def test_spa_serves_index_html_for_frontend_routes(self, client: AsyncClient):
        res = await client.get("/stream")
        assert res.status_code == 200
        assert "text/html" in res.headers["content-type"]
        assert "<!doctype html>" in res.text or "<html" in res.text

    @pytest.mark.asyncio
    async def test_api_404_does_not_serve_spa_html(self, client: AsyncClient):
        res = await client.get("/api/nonexistent_route")
        assert res.status_code == 404
        assert res.headers["content-type"].startswith("application/json")

    @pytest.mark.asyncio
    async def test_spa_path_traversal_rejected(self, client: AsyncClient):
        res = await client.get("/..%2f..%2frequirements.txt")
        assert res.status_code == 404

    @pytest.mark.asyncio
    async def test_security_headers_present(self, client: AsyncClient):
        res = await client.get("/stream")
        assert res.headers.get("X-Frame-Options") == "DENY"
        assert res.headers.get("X-Content-Type-Options") == "nosniff"
        assert res.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"


# ===================================================================
# 3. Unraid Community Applications Template
# ===================================================================

class TestUnraidTemplate:

    def test_unraid_template_valid_xml(self):
        assert UNRAID_TEMPLATE_PATH.exists(), "unraid-template.xml must exist"
        tree = ET.parse(UNRAID_TEMPLATE_PATH)
        root = tree.getroot()
        assert root.tag == "Container"
        assert root.attrib.get("version") == "2"

    def test_unraid_container_identity_and_network(self):
        tree = ET.parse(UNRAID_TEMPLATE_PATH)
        root = tree.getroot()

        name = root.find("Name")
        assert name is not None and name.text == "logshed"

        network = root.find("Network")
        assert network is not None and network.text == "bridge"

        webui = root.find("WebUI")
        assert webui is not None and "8080" in webui.text

    def test_unraid_port_mappings(self):
        tree = ET.parse(UNRAID_TEMPLATE_PATH)
        root = tree.getroot()

        configs = root.findall("Config")
        ports = [c for c in configs if c.attrib.get("Type") == "Port"]

        port_8080 = next((p for p in ports if p.attrib.get("Target") == "8080"), None)
        assert port_8080 is not None
        assert port_8080.attrib.get("Mode") == "tcp"

        port_1514_udp = next((p for p in ports if p.attrib.get("Target") == "1514" and p.attrib.get("Mode") == "udp"), None)
        assert port_1514_udp is not None

        port_1514_tcp = next((p for p in ports if p.attrib.get("Target") == "1514" and p.attrib.get("Mode") == "tcp"), None)
        assert port_1514_tcp is not None

    def test_unraid_path_mappings(self):
        tree = ET.parse(UNRAID_TEMPLATE_PATH)
        root = tree.getroot()

        configs = root.findall("Config")
        paths = [c for c in configs if c.attrib.get("Type") == "Path"]

        data_path = next((p for p in paths if p.attrib.get("Target") == "/data"), None)
        assert data_path is not None
        assert data_path.attrib.get("Default") == "/mnt/cache/appdata/logshed"
        assert data_path.attrib.get("Mode") == "rw"

        sock_path = next((p for p in paths if p.attrib.get("Target") == "/var/run/docker.sock"), None)
        assert sock_path is not None
        assert sock_path.attrib.get("Default") == "/var/run/docker.sock"
        assert sock_path.attrib.get("Mode") == "ro"

    def test_unraid_variables_and_no_docker_host(self):
        tree = ET.parse(UNRAID_TEMPLATE_PATH)
        root = tree.getroot()

        configs = root.findall("Config")
        variables = [c for c in configs if c.attrib.get("Type") == "Variable"]
        var_targets = {v.attrib.get("Target"): v.attrib.get("Default") for v in variables}

        assert "PUID" in var_targets and var_targets["PUID"] == "99"
        assert "PGID" in var_targets and var_targets["PGID"] == "100"
        assert "TZ" in var_targets and var_targets["TZ"] == "UTC"
        assert "DOCKER_HOST" not in var_targets


# ===================================================================
# 4. Dockerfile & Entrypoint Static Checks
# ===================================================================

class TestDockerfileStatic:

    def test_dockerfile_exists_and_multistage(self):
        assert DOCKERFILE_PATH.exists(), "Dockerfile must exist"
        content = DOCKERFILE_PATH.read_text()
        assert "node:20-alpine" in content and "frontend-builder" in content
        assert "RUN npm ci" in content
        assert "npm run build" in content
        assert "FROM python:3.12-slim" in content
        assert "tini" in content
        assert "curl" in content
        assert "gosu" in content

    def test_dockerfile_ports_volumes_and_entrypoint(self):
        content = DOCKERFILE_PATH.read_text()
        assert "EXPOSE 8080/tcp 1514/tcp 1514/udp" in content or ("8080" in content and "1514" in content)
        assert 'VOLUME ["/data"]' in content or "VOLUME /data" in content
        assert "HEALTHCHECK" in content
        assert "/api/health" in content
        assert 'ENTRYPOINT ["/entrypoint.sh"]' in content


class TestEntrypointStatic:

    def test_entrypoint_syntax(self):
        assert ENTRYPOINT_PATH.exists(), "entrypoint.sh must exist"
        res = subprocess.run(["sh", "-n", str(ENTRYPOINT_PATH)], capture_output=True, text=True)
        assert res.returncode == 0, f"entrypoint.sh syntax error: {res.stderr}"

    def test_entrypoint_privilege_dropping_logic(self):
        content = ENTRYPOINT_PATH.read_text()
        assert "PUID" in content and "PGID" in content
        assert "1000" in content
        assert "docker.sock" in content
        assert "stat" in content
        assert "usermod -aG root appuser" in content
        assert "CURRENT_OWNER" in content
        assert "chown -R appuser:appuser /data" in content
        assert "--workers 1" in content
        assert "gosu" in content
        assert "tini" in content
        assert "uvicorn" in content


# ===================================================================
# 5. GitHub Actions Workflow
# ===================================================================

class TestGitHubWorkflow:

    def test_workflow_valid_yaml(self):
        assert WORKFLOW_PATH.exists(), ".github/workflows/build-publish.yml must exist"
        content = WORKFLOW_PATH.read_text()
        parsed = yaml.safe_load(content)
        assert parsed is not None
        assert "name" in parsed
        assert "jobs" in parsed

    def test_workflow_triggers_and_permissions(self):
        content = WORKFLOW_PATH.read_text()
        parsed = yaml.safe_load(content)

        triggers = parsed.get("on") or parsed.get(True) or {}
        if isinstance(triggers, dict):
            push = triggers.get("push", {})
            assert any("v*" in tag for tag in push.get("tags", []))

        permissions = parsed.get("permissions", {})
        assert permissions.get("packages") == "write"

    def test_workflow_multiarch_and_ghcr(self):
        content = WORKFLOW_PATH.read_text()
        assert "ghcr.io" in content
        assert "linux/amd64" in content
        assert "linux/arm64" in content


# ===================================================================
# 6. Live Container Execution (Requires Docker Daemon)
# ===================================================================

@pytest.fixture(scope="module")
def docker_available():
    if not is_docker_daemon_running():
        pytest.skip("Docker daemon is not running or accessible in this test environment.")
    return True


@pytest.fixture(scope="module")
def built_image(docker_available):
    image_name = "logshed:test-deployment"
    build_cmd = ["docker", "build", "-t", image_name, str(REPO_ROOT)]
    res = subprocess.run(build_cmd, capture_output=True, text=True, timeout=300)
    assert res.returncode == 0, f"Docker build failed: {res.stderr}\n{res.stdout}"
    yield image_name
    subprocess.run(["docker", "rmi", "-f", image_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


@pytest.mark.e2e
class TestDockerLiveContainer:

    def test_container_default_port_and_healthcheck(self, built_image):
        container_name = "test-logshed-default-port"
        host_port = "18080"

        run_cmd = [
            "docker", "run", "-d",
            "--name", container_name,
            "-p", f"{host_port}:8080",
            built_image,
        ]
        res = subprocess.run(run_cmd, capture_output=True, text=True)
        assert res.returncode == 0, f"Failed to start container: {res.stderr}"

        try:
            health_url = f"http://localhost:{host_port}/api/health"
            healthy = False
            for _ in range(30):
                time.sleep(1)
                try:
                    r = httpx.get(health_url, timeout=2.0)
                    if r.status_code == 200 and r.json().get("status") == "ok":
                        healthy = True
                        break
                except Exception:
                    continue

            assert healthy, "Container failed to pass GET /api/health on default port 8080 within 30 seconds."

            ps_res = subprocess.run(
                ["docker", "exec", container_name, "ps", "aux"],
                capture_output=True,
                text=True,
            )
            assert ps_res.returncode == 0
            python_uvicorn_lines = [
                line for line in ps_res.stdout.splitlines()
                if "python" in line and "uvicorn" in line
            ]
            assert len(python_uvicorn_lines) == 1, f"Expected 1 uvicorn worker process, found: {python_uvicorn_lines}"
            assert "--workers 1" in python_uvicorn_lines[0]

        finally:
            subprocess.run(["docker", "rm", "-f", container_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def test_container_custom_port_override(self, built_image):
        container_name = "test-logshed-custom-port"
        host_port = "19090"
        target_port = "9090"

        run_cmd = [
            "docker", "run", "-d",
            "--name", container_name,
            "-e", f"PORT={target_port}",
            "-p", f"{host_port}:{target_port}",
            built_image,
        ]
        res = subprocess.run(run_cmd, capture_output=True, text=True)
        assert res.returncode == 0, f"Failed to start container: {res.stderr}"

        try:
            health_url = f"http://localhost:{host_port}/api/health"
            healthy = False
            for _ in range(30):
                time.sleep(1)
                try:
                    r = httpx.get(health_url, timeout=2.0)
                    if r.status_code == 200 and r.json().get("status") == "ok":
                        healthy = True
                        break
                except Exception:
                    continue

            assert healthy, f"Container failed to pass GET /api/health on overridden port {target_port} within 30 seconds."

        finally:
            subprocess.run(["docker", "rm", "-f", container_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def test_container_privilege_drop_and_permissions(self, built_image):
        container_name = "test-logshed-privilege-test"

        run_cmd = [
            "docker", "run", "-d",
            "--name", container_name,
            "-e", "PUID=1001",
            "-e", "PGID=1001",
            built_image,
        ]
        res = subprocess.run(run_cmd, capture_output=True, text=True)
        assert res.returncode == 0, f"Failed to start container: {res.stderr}"

        try:
            time.sleep(3)
            id_res = subprocess.run(
                ["docker", "exec", container_name, "id", "-u", "appuser"],
                capture_output=True,
                text=True,
            )
            assert id_res.returncode == 0
            assert id_res.stdout.strip() == "1001"

            stat_res = subprocess.run(
                ["docker", "exec", container_name, "stat", "-c", "%u:%g", "/data"],
                capture_output=True,
                text=True,
            )
            assert stat_res.returncode == 0
            assert stat_res.stdout.strip() == "1001:1001"

        finally:
            subprocess.run(["docker", "rm", "-f", container_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# ===================================================================
# Entrypoint hardening (non-e2e: sh plus stub commands, no root, no Docker)
# ===================================================================

class TestEntrypointHardening:

    @staticmethod
    def _run_entrypoint(tmp_path: Path, env_extra: dict, socket_gid: str = "999"):
        """Run entrypoint.sh with stub commands first on PATH; return (result, calls list)."""
        stubs = tmp_path / "stubs"
        stubs.mkdir()
        log = tmp_path / "calls.log"
        sock = tmp_path / "docker.sock"
        sock.write_text("")

        def stub(name: str, body: str):
            path = stubs / name
            path.write_text("#!/bin/sh\n" + body + "\n")
            path.chmod(0o755)

        record = f'echo "{{name}} $*" >> "{log}"'
        for name in ("usermod", "groupmod", "groupadd", "useradd", "chown", "mkdir", "tini"):
            stub(name, record.format(name=name))
        stub("getent", "exit 2")
        stub("stat", f'echo "{socket_gid}"')
        stub("gosu", f'echo "gosu $*" >> "{log}"\necho "umask $(umask)" >> "{log}"')

        env = {
            "PATH": f"{stubs}:/usr/bin:/bin",
            "DOCKER_HOST": f"unix://{sock}",
        }
        env.update(env_extra)
        res = subprocess.run(
            ["sh", str(ENTRYPOINT_PATH)], env=env, capture_output=True, text=True, timeout=30
        )
        calls = log.read_text().splitlines() if log.exists() else []
        return res, calls

    @pytest.mark.parametrize("var", ["PUID", "PGID"])
    def test_root_ids_start_with_a_loud_warning(self, tmp_path: Path, var: str):
        res, calls = self._run_entrypoint(tmp_path, {var: "0"})
        assert res.returncode == 0, res.stderr
        assert any(c.startswith("gosu") for c in calls)
        warning = [line for line in res.stderr.splitlines() if f"{var}=0" in line]
        assert warning, res.stderr
        assert "WARNING" in warning[0]
        assert "non-root" in res.stderr
        assert "later release will refuse to start" in res.stderr
        assert "ALLOW_ROOT" not in res.stderr

    def test_root_ids_warn_for_both_variables(self, tmp_path: Path):
        res, calls = self._run_entrypoint(tmp_path, {"PUID": "0", "PGID": "0"})
        assert res.returncode == 0, res.stderr
        assert "PUID=0" in res.stderr and "PGID=0" in res.stderr
        assert any(c.startswith("gosu") for c in calls)

    def test_allow_root_is_no_longer_a_setting(self, tmp_path: Path):
        assert "ALLOW_ROOT" not in ENTRYPOINT_PATH.read_text()
        res, calls = self._run_entrypoint(tmp_path, {"PUID": "0", "PGID": "0", "ALLOW_ROOT": "true"})
        assert res.returncode == 0, res.stderr
        assert "PUID=0" in res.stderr
        assert any(c.startswith("gosu") for c in calls)

    def test_non_root_ids_print_no_root_warning(self, tmp_path: Path):
        res, calls = self._run_entrypoint(tmp_path, {"PUID": "1000", "PGID": "1000"})
        assert res.returncode == 0, res.stderr
        assert "runs LogShed as root" not in res.stderr

    def test_root_socket_gid_adds_root_group_and_recommends_a_proxy(self, tmp_path: Path):
        res, calls = self._run_entrypoint(tmp_path, {}, socket_gid="0")
        assert res.returncode == 0, res.stderr
        assert "usermod -aG root appuser" in calls
        assert "WARNING" in res.stderr
        assert "socket proxy" in res.stderr.lower()
        assert any(c.startswith("gosu") for c in calls)

    def test_other_socket_gid_does_not_add_root_group(self, tmp_path: Path):
        res, calls = self._run_entrypoint(tmp_path, {}, socket_gid="999")
        assert res.returncode == 0, res.stderr
        assert "usermod -aG root appuser" not in calls
        assert "WARNING: Docker socket" not in res.stderr

    def test_umask_027_applied_before_exec(self, tmp_path: Path):
        res, calls = self._run_entrypoint(tmp_path, {})
        assert res.returncode == 0, res.stderr
        assert "umask 0027" in calls


# ===================================================================
# HTTP perimeter: CSP, no-store and API docs toggle (S-21, S-22)
# ===================================================================

STRICT_CSP = (
    "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; "
    "connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'self'"
)


class TestHttpPerimeterHeaders:

    @pytest.fixture
    def perimeter_env(self, tmp_path: Path, monkeypatch):
        from app.core.security import get_or_create_master_key, reset_crypto_cache

        db_file = tmp_path / "logs.db"
        key_file = tmp_path / ".secret_key"
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        monkeypatch.setenv("DB_PATH", str(db_file))
        monkeypatch.setenv("SECRET_KEY_PATH", str(key_file))
        monkeypatch.delenv("LOGSHED_SECRET_KEY", raising=False)
        monkeypatch.delenv("ENABLE_API_DOCS", raising=False)
        reset_crypto_cache()
        run_migrations(db_file)
        get_or_create_master_key(key_file)
        yield
        reset_crypto_cache()

    @staticmethod
    def _client(app) -> AsyncClient:
        return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

    @staticmethod
    def _assert_security_headers(res) -> None:
        assert res.headers.get("X-Frame-Options") == "DENY"
        assert res.headers.get("X-Content-Type-Options") == "nosniff"
        assert res.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"
        assert res.headers.get("Content-Security-Policy") == STRICT_CSP

    @pytest.mark.asyncio
    async def test_spa_html_carries_content_security_policy(self, perimeter_env):
        static_index = Path(__file__).resolve().parent.parent / "app" / "static" / "index.html"
        if not static_index.is_file():
            pytest.skip("frontend is not built")
        async with self._client(create_app()) as ac:
            res = await ac.get("/stream")
        assert res.status_code == 200
        assert "text/html" in res.headers["content-type"]
        self._assert_security_headers(res)

    @pytest.mark.asyncio
    async def test_api_responses_are_not_stored(self, perimeter_env):
        async with self._client(create_app()) as ac:
            res = await ac.get("/api/health")
        assert res.status_code == 200
        assert res.headers.get("Cache-Control") == "no-store"
        self._assert_security_headers(res)

    @pytest.mark.asyncio
    async def test_created_api_token_response_is_not_stored(self, perimeter_env):
        from app.core.security import SESSION_COOKIE_NAME, create_session_token

        async with self._client(create_app()) as ac:
            ac.cookies.set(SESSION_COOKIE_NAME, create_session_token(user_id=1))
            res = await ac.post(
                "/api/tokens",
                json={"name": "Runner", "scopes": ["logs:read"]},
                headers={"X-Requested-With": "XMLHttpRequest"},
            )
        assert res.status_code in (200, 201)
        assert "raw_token" in res.json()
        assert res.headers.get("Cache-Control") == "no-store"

    @pytest.mark.asyncio
    async def test_csrf_rejection_carries_security_headers(self, perimeter_env):
        async with self._client(create_app()) as ac:
            res = await ac.post("/api/auth/logout")
        assert res.status_code == 403
        assert "X-Requested-With" in res.json()["detail"]
        self._assert_security_headers(res)
        assert res.headers.get("Cache-Control") == "no-store"

    @pytest.mark.asyncio
    async def test_api_docs_are_served_by_default_with_a_docs_policy(self, perimeter_env):
        from app.core.security import SESSION_COOKIE_NAME, create_session_token

        async with self._client(create_app.uncached()) as ac:
            ac.cookies.set(SESSION_COOKIE_NAME, create_session_token(user_id=1))
            docs = await ac.get("/docs")
            redoc = await ac.get("/redoc")
            spec = await ac.get("/openapi.json")
        assert docs.status_code == 200 and "swagger-ui" in docs.text
        assert redoc.status_code == 200 and "redoc" in redoc.text.lower()
        assert spec.status_code == 200 and "openapi" in spec.json()
        for res in (docs, redoc):
            csp = res.headers.get("Content-Security-Policy", "")
            assert "cdn.jsdelivr.net" not in csp
            assert "frame-ancestors 'none'" in csp
            assert "object-src 'none'" in csp
            assert res.headers.get("X-Frame-Options") == "DENY"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("value", ["false", "0", "no", "off"])
    async def test_api_docs_can_be_turned_off(self, perimeter_env, monkeypatch, value):
        monkeypatch.setenv("ENABLE_API_DOCS", value)
        async with self._client(create_app.uncached()) as ac:
            docs = await ac.get("/docs")
            redoc = await ac.get("/redoc")
            spec = await ac.get("/openapi.json")
        assert "swagger-ui" not in docs.text
        assert "redoc.standalone" not in redoc.text
        assert spec.status_code == 404 or "application/json" not in spec.headers.get("content-type", "")
        for res in (docs, redoc, spec):
            assert "cdn.jsdelivr.net" not in res.headers.get("Content-Security-Policy", "")


# ===================================================================
# API docs behind the login, served from local assets (N05-4, N05-5; D-09, D-10)
# ===================================================================

STATIC_DIR = REPO_ROOT / "backend" / "app" / "static"
DOCS_ASSET_FILES = (
    "swagger-ui-bundle.js",
    "swagger-ui.css",
    "redoc.standalone.js",
)


class TestApiDocsAccess:

    @pytest.fixture
    def docs_env(self, tmp_path: Path, monkeypatch):
        from app.core.security import get_or_create_master_key, reset_crypto_cache

        db_file = tmp_path / "logs.db"
        key_file = tmp_path / ".secret_key"
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        monkeypatch.setenv("DB_PATH", str(db_file))
        monkeypatch.setenv("SECRET_KEY_PATH", str(key_file))
        monkeypatch.delenv("LOGSHED_SECRET_KEY", raising=False)
        monkeypatch.delenv("ENABLE_API_DOCS", raising=False)
        reset_crypto_cache()
        run_migrations(db_file)
        get_or_create_master_key(key_file)
        yield
        reset_crypto_cache()

    @staticmethod
    def _client(app, logged_in: bool = False) -> AsyncClient:
        from app.core.security import SESSION_COOKIE_NAME, create_session_token

        ac = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
        if logged_in:
            ac.cookies.set(SESSION_COOKIE_NAME, create_session_token(user_id=1))
        return ac

    @staticmethod
    def _inline_scripts(html: str) -> list[str]:
        import re

        return re.findall(r"<script>(.*?)</script>", html, re.DOTALL)

    @staticmethod
    def _csp_directives(csp: str) -> dict[str, list[str]]:
        out = {}
        for part in csp.split(";"):
            tokens = part.split()
            if tokens:
                out[tokens[0]] = tokens[1:]
        return out

    @pytest.mark.asyncio
    async def test_anonymous_docs_pages_redirect_to_the_login(self, docs_env):
        async with self._client(create_app.uncached()) as ac:
            docs = await ac.get("/docs")
            redoc = await ac.get("/redoc")
        for res in (docs, redoc):
            assert res.status_code == 302
            assert res.headers["location"] == "/"
            assert "swagger-ui" not in res.text

    @pytest.mark.asyncio
    async def test_anonymous_openapi_json_is_refused(self, docs_env):
        async with self._client(create_app.uncached()) as ac:
            res = await ac.get("/openapi.json")
        assert res.status_code == 401
        assert "paths" not in res.text

    @pytest.mark.asyncio
    async def test_logged_in_user_gets_all_three_with_the_docs_policy(self, docs_env):
        async with self._client(create_app.uncached(), logged_in=True) as ac:
            docs = await ac.get("/docs")
            redoc = await ac.get("/redoc")
            spec = await ac.get("/openapi.json")
        assert docs.status_code == 200 and "SwaggerUIBundle" in docs.text
        assert redoc.status_code == 200 and "<redoc" in redoc.text
        assert spec.status_code == 200 and "/api/v1/logs" in spec.json()["paths"]
        for res in (docs, redoc, spec):
            csp = res.headers.get("Content-Security-Policy", "")
            assert "'sha256-" in csp
            assert "frame-ancestors 'none'" in csp
            assert "object-src 'none'" in csp
            assert res.headers.get("X-Frame-Options") == "DENY"

    @pytest.mark.asyncio
    async def test_docs_policy_is_strict_and_hashes_the_served_inline_script(self, docs_env):
        import base64
        import hashlib

        async with self._client(create_app.uncached(), logged_in=True) as ac:
            docs = await ac.get("/docs")
        csp = docs.headers["Content-Security-Policy"]
        assert "cdn.jsdelivr.net" not in csp
        assert "fonts.googleapis.com" not in csp
        assert "fonts.gstatic.com" not in csp
        directives = self._csp_directives(csp)
        assert "'unsafe-inline'" not in directives["script-src"]
        assert "'unsafe-eval'" not in directives["script-src"]
        assert "https:" not in directives.get("img-src", [])
        assert directives["default-src"] == ["'self'"]

        scripts = self._inline_scripts(docs.text)
        assert len(scripts) == 1
        digest = base64.b64encode(hashlib.sha256(scripts[0].encode("utf-8")).digest()).decode("ascii")
        assert f"'sha256-{digest}'" in directives["script-src"]

    @pytest.mark.asyncio
    async def test_docs_html_references_only_local_assets(self, docs_env):
        import re

        async with self._client(create_app.uncached(), logged_in=True) as ac:
            docs = await ac.get("/docs")
            redoc = await ac.get("/redoc")
        for res in (docs, redoc):
            urls = re.findall(r'(?:src|href)="([^"]*)"', res.text)
            assert urls
            for url in urls:
                assert url.startswith("/") and not url.startswith("//"), url
            assert "http://" not in res.text and "https://" not in res.text
            assert 'href="/favicon.svg"' in res.text
        assert 'src="/docs-assets/swagger-ui-bundle.js"' in docs.text
        assert 'href="/docs-assets/swagger-ui.css"' in docs.text
        assert 'src="/docs-assets/redoc.standalone.js"' in redoc.text
        # The online validator badge would send the spec URL to validator.swagger.io
        assert '"validatorUrl": null' in docs.text

    @pytest.mark.asyncio
    async def test_any_valid_api_token_reads_openapi_json(self, docs_env):
        async with self._client(create_app.uncached(), logged_in=True) as ac:
            created = await ac.post(
                "/api/tokens",
                json={"name": "Docs reader", "scopes": ["logs:read"]},
                headers={"X-Requested-With": "XMLHttpRequest"},
            )
        assert created.status_code in (200, 201)
        raw_token = created.json()["raw_token"]

        async with self._client(create_app.uncached()) as ac:
            ok = await ac.get("/openapi.json", headers={"Authorization": f"Bearer {raw_token}"})
            lower = await ac.get("/openapi.json", headers={"Authorization": f"bearer {raw_token}"})
            bad = await ac.get("/openapi.json", headers={"Authorization": "Bearer ls_live_bogus"})
            docs_with_token = await ac.get("/docs", headers={"Authorization": f"Bearer {raw_token}"})
        assert ok.status_code == 200 and "paths" in ok.json()
        assert lower.status_code == 200
        assert bad.status_code == 401
        # The docs pages themselves are for the web login only
        assert docs_with_token.status_code == 302

    @pytest.mark.asyncio
    async def test_invalid_token_is_not_rescued_by_the_session_cookie(self, docs_env):
        async with self._client(create_app.uncached(), logged_in=True) as ac:
            res = await ac.get("/openapi.json", headers={"Authorization": "Bearer ls_live_bogus"})
        assert res.status_code == 401

    @pytest.mark.asyncio
    @pytest.mark.parametrize("value", ["false", "0"])
    async def test_docs_turned_off_stay_off_with_a_session(self, docs_env, monkeypatch, value):
        monkeypatch.setenv("ENABLE_API_DOCS", value)
        async with self._client(create_app.uncached(), logged_in=True) as ac:
            docs = await ac.get("/docs")
            redoc = await ac.get("/redoc")
            spec = await ac.get("/openapi.json")
        assert "SwaggerUIBundle" not in docs.text
        assert "<redoc" not in redoc.text
        assert spec.status_code == 404 or "application/json" not in spec.headers.get("content-type", "")
        for res in (docs, redoc, spec):
            assert "'sha256-" not in res.headers.get("Content-Security-Policy", "")

    @pytest.mark.asyncio
    @pytest.mark.parametrize("name", DOCS_ASSET_FILES)
    async def test_docs_assets_are_served_after_a_build(self, docs_env, name):
        asset = STATIC_DIR / "docs-assets" / name
        if not asset.is_file():
            pytest.skip("frontend is not built (run `cd frontend && npm run build`)")
        async with self._client(create_app.uncached()) as ac:
            res = await ac.get(f"/docs-assets/{name}")
        assert res.status_code == 200
        expected = "text/css" if name.endswith(".css") else "javascript"
        assert expected in res.headers["content-type"]
        assert len(res.content) == asset.stat().st_size

    def test_docs_packages_are_pinned_dev_dependencies(self):
        import json

        package = json.loads((REPO_ROOT / "frontend" / "package.json").read_text())
        lock = json.loads((REPO_ROOT / "frontend" / "package-lock.json").read_text())
        for name in ("swagger-ui-dist", "redoc"):
            assert name in package["devDependencies"], name
            assert lock["packages"][f"node_modules/{name}"]["version"], name
        vite_config = (REPO_ROOT / "frontend" / "vite.config.ts").read_text()
        for name in DOCS_ASSET_FILES:
            assert name in vite_config, name
