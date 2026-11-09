"""
Unit and integration tests for Alert Presets and Drop Presets discovery and API endpoints.
"""

import json
from pathlib import Path
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core import pipeline as pipeline_mod
from app.core.migrations import run_migrations
from app.core.security import (
    SESSION_COOKIE_NAME,
    create_session_token,
    get_or_create_master_key,
    reset_crypto_cache,
)
from app.core.sse import sse_manager
from app.main import create_app
from app.services.alert_evaluator import init_alert_evaluator
from app.services.alert_presets import get_alert_preset_by_id, get_alert_presets
from app.core.rate_limiter import login_rate_limiter
from app.services.drop_filter import init_drop_filter
from app.services.drop_presets import get_drop_preset_by_id, get_drop_presets


@pytest.fixture(autouse=True)
def reset_test_env(tmp_path: Path, monkeypatch):
    pipeline_mod._log_queue = None
    pipeline_mod._dropped_logs_total = 0
    pipeline_mod._dropped_by_filter_total = 0
    login_rate_limiter.reset()
    reset_crypto_cache()
    sse_manager.reset()

    db_file = tmp_path / "logs.db"
    key_file = tmp_path / ".secret_key"
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DB_PATH", str(db_file))
    monkeypatch.setenv("SECRET_KEY_PATH", str(key_file))
    monkeypatch.delenv("LOGSHED_SECRET_KEY", raising=False)

    run_migrations(db_file)
    get_or_create_master_key(key_file)
    init_drop_filter(db_file)
    init_alert_evaluator(db_file)

    yield

    pipeline_mod._log_queue = None
    login_rate_limiter.reset()
    reset_crypto_cache()
    sse_manager.reset()


@pytest_asyncio.fixture
async def client():
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        headers={"x-requested-with": "XMLHttpRequest"},
    ) as ac:
        yield ac


@pytest.fixture
def auth_headers():
    token = create_session_token(user_id=1)
    return {
        "Cookie": f"{SESSION_COOKIE_NAME}={token}",
        "x-requested-with": "XMLHttpRequest",
    }


class TestAlertPresetsDiscovery:
    """Tests for standalone JSON alert preset discovery, merging, and overrides."""

    def test_builtin_alert_presets_loaded(self):
        presets = get_alert_presets()
        assert len(presets) >= 4
        ids = {p["id"] for p in presets}
        assert {"ssh_bruteforce", "proxy_auth_flood", "sudo_escalation", "oom_killer"}.issubset(ids)

        for p in presets:
            assert p["is_custom"] is False
            assert "name" in p
            assert "description" in p
            assert "rule_type" in p

    def test_get_alert_preset_by_id(self):
        ssh = get_alert_preset_by_id("ssh_bruteforce")
        assert ssh is not None
        assert ssh["id"] == "ssh_bruteforce"
        assert ssh["rule_type"] == "threshold"
        assert ssh["is_custom"] is False

        # Case insensitive
        ssh_upper = get_alert_preset_by_id("SSH_BRUTEFORCE")
        assert ssh_upper is not None
        assert ssh_upper["id"] == "ssh_bruteforce"

        # Unknown id
        unknown = get_alert_preset_by_id("non_existent_preset")
        assert unknown is None

    def test_user_alert_preset_discovery_and_override(self, tmp_path, monkeypatch):
        # Point DATA_DIR to tmp_path
        monkeypatch.setenv("DATA_DIR", str(tmp_path))

        user_presets_dir = tmp_path / "presets" / "alerts"
        user_presets_dir.mkdir(parents=True)

        # 1. Override an existing built-in preset
        override_data = {
            "id": "ssh_bruteforce",
            "name": "Custom SSH Protection",
            "description": "Overridden threshold for SSH brute-force.",
            "rule_type": "threshold",
            "threshold_count": 10,
            "window_seconds": 120,
            "cooldown_seconds": 600,
            "ai_enrichment": False,
        }
        (user_presets_dir / "ssh_bruteforce.json").write_text(json.dumps(override_data))

        # 2. Add a new custom community preset
        new_preset_data = {
            "id": "redis_unauthorized",
            "name": "Redis Unauthorized Access",
            "description": "Detects unauthorized attempts to access unprotected Redis instances.",
            "rule_type": "pattern",
            "filter_app": "redis",
            "match_pattern": "NOAUTH Authentication required",
            "threshold_count": 1,
            "window_seconds": 60,
            "cooldown_seconds": 60,
            "ai_enrichment": True,
        }
        (user_presets_dir / "redis_unauth.json").write_text(json.dumps(new_preset_data))

        presets = get_alert_presets()
        preset_map = {p["id"]: p for p in presets}

        # Check override
        assert "ssh_bruteforce" in preset_map
        assert preset_map["ssh_bruteforce"]["name"] == "Custom SSH Protection"
        assert preset_map["ssh_bruteforce"]["threshold_count"] == 10
        assert preset_map["ssh_bruteforce"]["is_custom"] is True

        # Check new preset
        assert "redis_unauthorized" in preset_map
        assert preset_map["redis_unauthorized"]["name"] == "Redis Unauthorized Access"
        assert preset_map["redis_unauthorized"]["is_custom"] is True

        # Check untouched built-ins remain is_custom=False
        assert preset_map["sudo_escalation"]["is_custom"] is False


class TestDropPresetsDiscovery:
    """Tests for standalone JSON drop rule preset discovery, merging, and overrides."""

    def test_builtin_drop_presets_loaded(self):
        presets = get_drop_presets()
        assert len(presets) >= 3
        ids = {p["id"] for p in presets}
        assert {"docker_healthcheck", "systemd_session", "cron_noise"}.issubset(ids)

        for p in presets:
            assert p["is_custom"] is False
            assert "name" in p
            assert "description" in p
            assert "message_pattern" in p

    def test_get_drop_preset_by_id(self):
        preset = get_drop_preset_by_id("docker_healthcheck")
        assert preset is not None
        assert preset["id"] == "docker_healthcheck"
        assert preset["is_regex"] is True
        assert preset["is_custom"] is False

        # Unknown id
        assert get_drop_preset_by_id("invalid_id") is None

    def test_user_drop_preset_discovery_and_override(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DATA_DIR", str(tmp_path))

        user_presets_dir = tmp_path / "presets" / "drops"
        user_presets_dir.mkdir(parents=True)

        # 1. Override built-in drop preset
        override_data = {
            "id": "docker_healthcheck",
            "name": "Custom Healthcheck Filter",
            "description": "Customized docker health check filter.",
            "source_pattern": "docker*",
            "app_pattern": None,
            "message_pattern": "healthcheck|status_check",
            "is_regex": True,
            "severity_threshold": 6,
        }
        (user_presets_dir / "docker_healthcheck.json").write_text(json.dumps(override_data))

        # 2. Add new user drop preset
        new_data = {
            "id": "nginx_ping",
            "name": "Nginx Ping Noise",
            "description": "Drops frequent ALB health check pings.",
            "source_pattern": None,
            "app_pattern": "nginx",
            "message_pattern": "GET /healthz HTTP/1.1",
            "is_regex": False,
            "severity_threshold": None,
        }
        (user_presets_dir / "nginx_ping.json").write_text(json.dumps(new_data))

        presets = get_drop_presets()
        preset_map = {p["id"]: p for p in presets}

        assert preset_map["docker_healthcheck"]["name"] == "Custom Healthcheck Filter"
        assert preset_map["docker_healthcheck"]["is_custom"] is True
        assert preset_map["docker_healthcheck"]["severity_threshold"] == 6

        assert "nginx_ping" in preset_map
        assert preset_map["nginx_ping"]["is_custom"] is True

        assert preset_map["cron_noise"]["is_custom"] is False


class TestDropPresetsApi:
    """API endpoint tests for drop presets."""

    @pytest.mark.asyncio
    async def test_list_drop_presets_endpoint(self, client: AsyncClient, auth_headers: dict):
        res = await client.get("/api/drop-rules/presets", headers=auth_headers)
        assert res.status_code == 200
        presets = res.json()
        assert len(presets) >= 3
        ids = [p["id"] for p in presets]
        assert "docker_healthcheck" in ids
        assert "systemd_session" in ids
        assert "cron_noise" in ids

    @pytest.mark.asyncio
    async def test_install_drop_preset_success_and_404(self, client: AsyncClient, auth_headers: dict):
        # 1. Install built-in drop preset
        res = await client.post(
            "/api/drop-rules/presets/docker_healthcheck/install",
            headers=auth_headers,
        )
        assert res.status_code == 201
        data = res.json()
        assert data["message_pattern"] == "healthcheck|health_status"
        assert data["is_regex"] is True
        assert data["is_enabled"] is True
        assert data["dropped_count"] == 0

        # Verify it appears in active drop rules list
        list_res = await client.get("/api/drop-rules", headers=auth_headers)
        assert list_res.status_code == 200
        active_rules = list_res.json()
        assert any(r["id"] == data["id"] for r in active_rules)

        # 2. Unknown preset 404
        bad_res = await client.post(
            "/api/drop-rules/presets/non_existent_preset/install",
            headers=auth_headers,
        )
        assert bad_res.status_code == 404
        assert "not found" in bad_res.json()["detail"].lower()


class TestPresetEdgeCasesAndConventions:
    """Tests covering preset file naming conventions, non-parseable files, and deleted preset files."""

    def test_file_naming_conventions(self):
        builtin_alerts_dir = Path(__file__).resolve().parent.parent / "app" / "presets" / "alerts"
        alert_files = [f.name for f in builtin_alerts_dir.glob("*.json")]
        assert len(alert_files) >= 4
        for fname in alert_files:
            assert fname.startswith("alert_"), f"Alert preset file {fname} should start with 'alert_'"

        builtin_drops_dir = Path(__file__).resolve().parent.parent / "app" / "presets" / "drops"
        drop_files = [f.name for f in builtin_drops_dir.glob("*.json")]
        assert len(drop_files) >= 3
        for fname in drop_files:
            assert fname.startswith("drop_"), f"Drop preset file {fname} should start with 'drop_'"

    def test_non_parseable_alert_preset_files_are_gracefully_ignored(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        user_alerts_dir = tmp_path / "presets" / "alerts"
        user_alerts_dir.mkdir(parents=True)

        # 1. Corrupted / invalid JSON
        (user_alerts_dir / "alert_broken.json").write_text("{ this is not valid json :(")

        # 2. JSON array instead of dict
        (user_alerts_dir / "alert_array.json").write_text("[\"item1\", \"item2\"]")

        # 3. Missing required fields (e.g. no 'id' or 'rule_type')
        (user_alerts_dir / "alert_incomplete.json").write_text(json.dumps({"name": "No ID Rule"}))

        # 4. Valid custom rule
        valid_custom = {
            "id": "valid_user_rule",
            "name": "Valid User Rule",
            "rule_type": "pattern",
            "match_pattern": "FATAL",
        }
        (user_alerts_dir / "alert_valid.json").write_text(json.dumps(valid_custom))

        # Loading must not raise any exceptions
        presets = get_alert_presets()
        preset_ids = {p["id"] for p in presets}

        # Built-ins still loaded
        assert "ssh_bruteforce" in preset_ids
        # Valid custom rule loaded
        assert "valid_user_rule" in preset_ids
        # Bad rules not included
        assert "alert_broken" not in preset_ids

    def test_non_parseable_drop_preset_files_are_gracefully_ignored(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        user_drops_dir = tmp_path / "presets" / "drops"
        user_drops_dir.mkdir(parents=True)

        # Corrupted JSON
        (user_drops_dir / "drop_corrupt.json").write_text("NOT JSON CONTENT")

        # Missing required field 'message_pattern'
        (user_drops_dir / "drop_incomplete.json").write_text(json.dumps({"id": "no_msg", "name": "No Pattern"}))

        # Loading must not raise any exceptions
        presets = get_drop_presets()
        preset_ids = {p["id"] for p in presets}

        assert "docker_healthcheck" in preset_ids
        assert "no_msg" not in preset_ids

    @pytest.mark.asyncio
    async def test_installed_rule_continues_functioning_when_preset_file_deleted(
        self, client: AsyncClient, auth_headers: dict, tmp_path, monkeypatch
    ):
        """
        Verify that installing a rule creates an independent database record.
        If the original preset file is deleted or removed from disk, the installed rule
        remains active, evaluated, and completely functional.
        """
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        user_drops_dir = tmp_path / "presets" / "drops"
        user_drops_dir.mkdir(parents=True)

        temp_preset_file = user_drops_dir / "drop_temporary.json"
        temp_preset_file.write_text(
            json.dumps({
                "id": "temporary_service_drop",
                "name": "Temporary Service Noise",
                "description": "Noise from a temporary decommissioned service.",
                "source_pattern": "10.0.0.99",
                "app_pattern": "temp-service",
                "message_pattern": "periodic heartbeat",
                "is_regex": False,
                "severity_threshold": None,
            })
        )

        # 1. Verify preset is discovered
        assert get_drop_preset_by_id("temporary_service_drop") is not None

        # 2. Install the preset into active drop rules
        install_res = await client.post(
            "/api/drop-rules/presets/temporary_service_drop/install",
            headers=auth_headers,
        )
        assert install_res.status_code == 201
        installed_rule = install_res.json()
        rule_id = installed_rule["id"]

        # 3. Delete the preset file from disk
        temp_preset_file.unlink()
        assert not temp_preset_file.exists()

        # Preset is no longer in preset catalog
        assert get_drop_preset_by_id("temporary_service_drop") is None

        # 4. Verify installed rule is still present, active, and functioning in drop_rules
        list_res = await client.get("/api/drop-rules", headers=auth_headers)
        assert list_res.status_code == 200
        active_rules = {r["id"]: r for r in list_res.json()}
        assert rule_id in active_rules
        assert active_rules[rule_id]["app_pattern"] == "temp-service"
        assert active_rules[rule_id]["is_enabled"] is True

        # Test evaluation still matches
        from app.services.drop_filter import get_drop_filter
        filter_engine = get_drop_filter()
        assert filter_engine.should_drop(
            source_alias=None,
            source_ip="10.0.0.99",
            app_name="temp-service",
            message="periodic heartbeat 123",
        ) == rule_id

    @pytest.mark.asyncio
    async def test_lifespan_creates_presets_directories(self, tmp_path, monkeypatch):
        """Verify that application lifespan startup automatically creates presets/alerts and presets/drops."""
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        monkeypatch.setenv("DB_PATH", str(tmp_path / "logs.db"))
        monkeypatch.setenv("SECRET_KEY_PATH", str(tmp_path / ".secret_key"))

        alerts_dir = tmp_path / "presets" / "alerts"
        drops_dir = tmp_path / "presets" / "drops"

        assert not alerts_dir.exists()
        assert not drops_dir.exists()

        from app.main import create_app, lifespan
        test_app = create_app()

        async with lifespan(test_app):
            assert alerts_dir.is_dir()
            assert drops_dir.is_dir()




# Real lines (formats from each tool's source or documentation) that each shipped alert preset must
# match ("match") and must not match ("miss"). Every preset with a pattern has an entry.
ALERT_PRESET_SAMPLES = {
    "oom_killer": {
        "match": ["Out of memory: Killed process 1234 (node) total-vm:1024kB"],
        "miss": [],
    },
    "proxy_auth_flood": {
        "match": ['192.168.1.1 - - [18/Sep/2026:12:00:00 +0000] "GET /admin HTTP/1.1" 401 123'],
        "miss": [],
    },
    "ssh_bruteforce": {
        "match": ["FAILED PASSWORD for invalid user admin from 10.0.0.5 port 22 ssh2"],
        "miss": [],
    },
    "sudo_escalation": {
        "match": ["alice : TTY=pts/0 ; PWD=/home/alice ; USER=root ; COMMAND=/bin/bash"],
        "miss": [],
    },
    "disk_smart_failure": {
        "match": [
            "Device: /dev/sda [SAT], FAILED SMART self-check. BACK UP DATA NOW!",
            "Device: /dev/sdb [SAT], 8 Currently unreadable (pending) sectors",
            "Device: /dev/sdb [SAT], 2 Offline uncorrectable sectors",
            "Device: /dev/sdc [SAT], SMART Prefailure Attribute: 5 Reallocated_Sector_Ct changed from 100 to 99",
            "Device: /dev/sda [SAT], Self-Test Log error count increased from 0 to 1",
            "Device: /dev/sda [SAT], ATA error count increased from 0 to 3",
        ],
        "miss": [
            "Device: /dev/sda [SAT], SMART Usage Attribute: 194 Temperature_Celsius changed from 64 to 63",
            "Device: /dev/sda [SAT], opened",
            'Device: /dev/sda [SAT], is SMART capable. Adding to "monitor" list.',
        ],
    },
    "zfs_pool_errors": {
        "match": [
            "eid=42 class=checksum pool='tank' vdev=sdb1 algorithm=fletcher4 size=131072 offset=4194304 priority=0 err=52 flags=0x180880",
            "eid=9 class=io pool='backup' vdev=sdd1 size=4096 offset=270336 priority=0 err=5 flags=0x8881",
            "eid=7 class=statechange pool='tank' vdev=sdc1 vdev_state=FAULTED",
            "eid=8 class=statechange pool='tank' vdev=sdc1 vdev_state=DEGRADED",
        ],
        "miss": [
            "eid=12 class=statechange pool='tank' vdev=sdc1 vdev_state=ONLINE",
            "eid=3 class=scrub_finish pool='tank'",
            "eid=1 class=history_event pool='tank'",
            "eid=5 class=config_sync pool='tank'",
        ],
    },
    "mdadm_degraded": {
        "match": [
            "DegradedArray event detected on md device /dev/md/0",
            "Fail event detected on md device /dev/md0, component device /dev/sdb1",
            "md/raid1:md0: Disk failure on sdb1, disabling device.",
            "md/raid:md1: Disk failure on sdc1, disabling device.",
        ],
        "miss": [
            "NewArray event detected on md device /dev/md0",
            "RebuildFinished event detected on md device /dev/md0",
            "SparesMissing event detected on md device /dev/md0",
            "md/raid1:md0: active with 2 out of 2 mirrors",
        ],
    },
    "filesystem_errors": {
        "match": [
            "EXT4-fs error (device sda1): ext4_find_entry:1455: inode #2: comm ls: reading directory lblock 0",
            'XFS (sdb1): metadata I/O error in "xfs_imap_to_bp+0x5c/0xa0" at daddr 0x3c0 len 32 error 5',
            "XFS (dm-0): Corruption detected. Unmount and run xfs_repair",
            "BTRFS error (device sdc1): bdev /dev/sdc1 errs: wr 0, rd 1, flush 0, corrupt 0, gen 0",
            "Buffer I/O error on dev sda1, logical block 0, async page read",
            "I/O error, dev sda, sector 2048 op 0x0:(READ) flags 0x0 phys_seg 1 prio class 2",
        ],
        "miss": [
            "EXT4-fs (sda1): mounted filesystem with ordered data mode. Quota mode: none.",
            "XFS (sdb1): Mounting V5 Filesystem",
            "BTRFS info (device sdc1): disk space caching is enabled",
        ],
    },
    "backup_failure": {
        "match": [
            "ERROR: Backup of VM 101 failed - unable to create temporary directory '/mnt/pve/nas/dump/vzdump-qemu-101.tmp'",
            "INFO: Backup job finished with errors",
            "terminating with error status, rc 2",
            "Fatal: wrong password or no key found",
            "Fatal: unable to open config file: Stat: stat /srv/restic/config: no such file or directory",
            "Fatal: unable to create lock in backend: repository is already locked by PID 1234",
        ],
        "miss": [
            "INFO: Finished Backup of VM 101 (00:01:10)",
            "INFO: Backup job finished successfully",
            "terminating with success status, rc 0",
            "terminating with warning status, rc 1",
        ],
    },
    "ups_power": {
        "match": [
            "Power failure.",
            "Running on UPS batteries.",
            "UPS ups@localhost on battery",
            "UPS ups@localhost battery is low",
            "UPS ups@localhost battery needs to be replaced",
            "Communications with UPS ups@localhost lost",
        ],
        "miss": [
            "UPS ups@localhost on line power",
            "Power is back. UPS running on mains.",
            "Communications with UPS ups@localhost established",
        ],
    },
    "cert_renewal_failure": {
        "match": [
            "Failed to renew certificate example.com with error: Some challenges have failed.",
            "All renewals failed. The following certificates could not be renewed:",
            'ERR Unable to obtain ACME certificate for domains error="acme: error: 429" domains=["a.example.com"]',
            "[Mon Nov  9 03:00:01 UTC 2026] Error renewing example.com.",
            '{"level":"error","ts":1762689600.1,"logger":"tls.obtain","msg":"could not get certificate from issuer","identifier":"example.com"}',
        ],
        "miss": [
            "Congratulations, all renewals succeeded:",
            '{"level":"info","logger":"tls.renew","msg":"certificate renewed successfully","identifier":"example.com"}',
            "Certificate not yet due for renewal",
        ],
    },
    "proxmox_cluster": {
        "match": [
            "[KNET  ] link: host: 2 link: 0 is down",
            "[status] notice: node lost quorum",
            "[QUORUM] This node is within the non-primary component and will NOT provide any services.",
            "[TOTEM ] A processor failed, forming new configuration: token timed out (3000ms)",
            "node 'pve2': state changed from 'unknown' => 'fence'",
            "fencing: acknowledged - got agent lock for node 'pve2'",
        ],
        "miss": [
            "[KNET  ] host: host: 2 (passive) best link: 0 (pri: 1)",
            "[status] notice: node has quorum",
            "[QUORUM] This node is within the primary component and will provide service.",
            "node 'pve2': state changed from 'unknown' => 'online'",
        ],
    },
    "wan_gateway_alarm": {
        "match": [
            "WAN_DHCP 203.0.113.1: Alarm latency 612000us stddev 4500us loss 0%",
            "WAN_DHCP is down, omitting from routing group WANGROUP; 203.0.113.1|203.0.113.10|down|0ms|0ms|100%",
            "WAN_DHCP has packet loss, omitting from routing group WANGROUP; 203.0.113.1|203.0.113.10|loss|0ms|0ms|25%",
            "ALERT: WAN_DHCP (Addr: 203.0.113.1 Alarm: none -> down RTT: 0.0 ms RTTd: 0.0 ms Loss: 100.0 %)",
            "INFO [Gateway Monitoring] >>> Gateway alarm: WAN_DHCP (Addr:203.0.113.1 Alarm:1 RTT:612.0ms RTTsd:4.5ms Loss:0%)",
        ],
        "miss": [
            "WAN_DHCP 203.0.113.1: Clear latency 18000us stddev 900us loss 0%",
            "WAN_DHCP is available now, adding to routing group WANGROUP",
            "MONITOR: WAN_DHCP (Addr: 203.0.113.1 Alarm: down -> none RTT: 18.0 ms RTTd: 0.9 ms Loss: 0.0 %)",
            "INFO [Gateway Monitoring] >>> Gateway alarm: WAN_DHCP (Addr:203.0.113.1 Alarm:0 RTT:18.0ms RTTsd:0.9ms Loss:0%)",
        ],
    },
    "segfault": {
        "match": [
            "node[2345]: segfault at 0 ip 00007f1c2b3d4e5f sp 00007ffd1a2b3c40 error 4 in libc.so.6[7f1c2b200000+195000]",
            "traps: python3[812] general protection fault ip:7f4e5c1d2e3f sp:7ffc9a8b7c60 error:0 in libpython3.11.so",
        ],
        "miss": ["Installed segfault handler for worker threads"],
    },
    "systemd_unit_failed": {
        "match": [
            "nginx.service: Failed with result 'exit-code'.",
            "mnt-nas.mount: Failed with result 'exit-code'.",
        ],
        "miss": [
            "nginx.service: Deactivated successfully.",
            "nginx.service: Scheduled restart job, restart counter is at 1.",
        ],
    },
    "fail2ban_bans": {
        "match": [
            "fail2ban.actions        [1234]: NOTICE  [sshd] Ban 192.0.2.10",
            "NOTICE  [nginx-http-auth] Ban 2001:db8::1",
        ],
        "miss": [
            "NOTICE  [sshd] Unban 192.0.2.10",
            "NOTICE  [sshd] Restore Ban 192.0.2.10",
            "INFO    [sshd] Found 192.0.2.10 - 2026-11-09 12:00:00",
        ],
    },
    "hardware_errors": {
        "match": [
            "mce: [Hardware Error]: Machine check events logged",
            "EDAC MC0: 1 CE memory read error on CPU_SrcID#0_Ha#0_Chan#1_DIMM#0 (channel:1 slot:0 page:0x12345)",
            "EDAC MC0: 1 UE memory read error on CPU_SrcID#0_Ha#0_Chan#1_DIMM#0 (channel:1 slot:0 page:0x12345)",
            "pcieport 0000:00:1c.0: AER: Uncorrected (Fatal) error received: 0000:01:00.0",
        ],
        "miss": [
            "pcieport 0000:00:1c.0: AER: Corrected error received: 0000:01:00.0",
            "EDAC MC0: Giving out device to module ie31200_edac controller IE31200: DEV 0000:00:00.0 (POLLED)",
            "mce: CPU0: Thermal monitoring enabled (TM1)",
        ],
    },
    "temperature_warning": {
        "match": [
            "CPU3: Core temperature above threshold, cpu clock throttled (total events = 1)",
            "CPU0: Package temperature above threshold, cpu clock throttled (total events = 5)",
            "Device: /dev/sda [SAT], Temperature 56 Celsius reached limit of 55 Celsius (Min/Max 24/56)",
            "Device: /dev/sda [SAT], Temperature 61 Celsius reached critical limit of 60 Celsius (Min/Max 24/61)",
        ],
        "miss": [
            "CPU3: Core temperature/speed normal",
            "Device: /dev/sda [SAT], SMART Usage Attribute: 194 Temperature_Celsius changed from 64 to 63",
        ],
    },
}

# Lines each shipped drop preset must drop and must keep, as (app name, message, severity).
# "keep" includes the warnings and errors a preset must never drop.
DROP_PRESET_SAMPLES = {
    "cron_noise": {
        "drop": [("CRON", "(root) CMD (run-parts /etc/cron.hourly)", None)],
        "keep": [("sshd", "(root) CMD (run-parts /etc/cron.hourly)", None)],
    },
    "docker_healthcheck": {
        "drop": [(None, "container 3f2a health_status: healthy", None)],
        "keep": [(None, "routine heartbeat ok", None)],
    },
    "systemd_session": {
        "drop": [("systemd", "pam_unix(systemd-user:session): session closed for user bob", None)],
        "keep": [("systemd", "pam_unix(sshd:session): session opened for user ben", None)],
    },
    "dns_query_chatter": {
        "drop": [
            ("dnsmasq", "query[A] example.com from 192.168.1.20", 6),
            ("dnsmasq", "query[AAAA] example.com from 192.168.1.20", 6),
            ("pihole-FTL", "forwarded example.com to 1.1.1.1", 6),
            ("dnsmasq", "reply example.com is 93.184.215.14", 6),
            ("dnsmasq", "cached example.com is 93.184.215.14", 6),
            ("dnsmasq", "config example.lan is NXDOMAIN", 6),
        ],
        "keep": [
            ("dnsmasq", "gravity blocked ads.example.com is 0.0.0.0", 6),
            ("dnsmasq", "failed to send packet: Network is unreachable", 3),
            ("dnsmasq", "Maximum number of concurrent DNS queries reached (max: 150)", 4),
            ("dnsmasq", "reply example.com is 93.184.215.14", 4),
        ],
    },
    "firewall_pass": {
        "drop": [
            ("filterlog", "5,,,1000000103,igb0,match,pass,in,4,0x0,,64,0,0,DF,6,tcp,60,192.168.1.20,203.0.113.5,51514,443,0,S", 6),
            ("filterlog", "77,,,fae559338f65e11c53669fc3642c93c2,vtnet0,match,pass,out,4,0x0,,127,61953,0,none,17,udp,76", 6),
        ],
        "keep": [
            ("filterlog", "5,,,1000000103,igb0,match,block,in,4,0x0,,64,0,0,DF,6,tcp,60,198.51.100.7,192.168.1.1,4444,22,0,S", 6),
            ("kernel", "5,,,1000000103,igb0,match,pass,in,4,0x0,,64", 6),
        ],
    },
    "health_endpoint_access": {
        "drop": [
            ("nginx", '172.18.0.1 - - [09/Nov/2026:12:00:00 +0000] "GET /health HTTP/1.1" 200 2 "-" "curl/8.5.0"', 6),
            ("traefik", '10.0.0.2 - - [09/Nov/2026:12:00:00 +0000] "GET /ping HTTP/1.1" 200 2 "-" "Go-http-client/1.1" 42 "ping@internal" "-" 0ms', 6),
            ("nginx", '172.18.0.1 - - [09/Nov/2026:12:00:00 +0000] "HEAD /healthz HTTP/1.1" 204 0 "-" "kube-probe"', 6),
            ("prometheus", '172.18.0.5 - - [09/Nov/2026:12:00:00 +0000] "GET /metrics?format=prometheus HTTP/1.1" 200 1234 "-" "Prometheus/2.53"', 6),
            ("caddy", '{"level":"info","ts":1762689600.5,"logger":"http.log.access","msg":"handled request","request":{"remote_ip":"172.18.0.1","method":"GET","host":"app.example.com","uri":"/health","headers":{"User-Agent":["curl/8.5.0"]}},"size":2,"status":200}', 6),
        ],
        "keep": [
            ("nginx", '172.18.0.1 - - [09/Nov/2026:12:00:00 +0000] "GET /health HTTP/1.1" 503 19 "-" "curl/8.5.0"', 6),
            ("nginx", '172.18.0.1 - - [09/Nov/2026:12:00:00 +0000] "GET /healthy-recipes HTTP/1.1" 200 512 "-" "Firefox"', 6),
            ("nginx", '172.18.0.1 - - [09/Nov/2026:12:00:00 +0000] "POST /api/ping HTTP/1.1" 200 2 "-" "curl/8.5.0"', 6),
            ("caddy", '{"level":"error","logger":"http.log.access","msg":"handled request","request":{"method":"GET","uri":"/health"},"status":503}', 3),
        ],
    },
    "proxmox_status_chatter": {
        "drop": [
            ("pveproxy", "worker 12345 started", 6),
            ("pveproxy", "worker 12340 finished", 6),
            ("pvedaemon", "starting 1 worker(s)", 6),
            ("pvestatd", "status update time (6.123 seconds)", 6),
        ],
        "keep": [
            ("pvedaemon", "<root@pam> successful auth for user 'root@pam'", 6),
            ("pveproxy", "proxy detected vanished client connection", 6),
            ("nginx", "worker 12345 started", 6),
        ],
    },
    "systemd_timer_chatter": {
        "drop": [
            ("systemd", "Created slice user-1000.slice - User Slice of UID 1000.", 6),
            ("systemd", "Removed slice user-1000.slice - User Slice of UID 1000.", 6),
            ("systemd", "Started session-12.scope - Session 12 of User root.", 6),
            ("systemd", "Started Session 12 of user root.", 6),
            ("systemd", "Finished logrotate.service - Rotate log files.", 6),
            ("systemd", "phpsessionclean.service: Deactivated successfully.", 6),
            ("systemd", "logrotate.service: Consumed 1.234s CPU time, 12.3M memory peak.", 6),
        ],
        "keep": [
            ("systemd", "Failed to start nginx.service - A high performance web server and a reverse proxy server.", 3),
            ("systemd", "nginx.service: Failed with result 'exit-code'.", 4),
            ("systemd", "Started nginx.service - A high performance web server and a reverse proxy server.", 6),
            ("systemd", "Stopped nginx.service - A high performance web server and a reverse proxy server.", 6),
        ],
    },
    "rsyslog_mark": {
        "drop": [("rsyslogd", "-- MARK --", 6)],
        "keep": [("kernel", "Reboot requested", 6)],
    },
    "smartd_attribute_chatter": {
        "drop": [
            ("smartd", "Device: /dev/sda [SAT], SMART Usage Attribute: 194 Temperature_Celsius changed from 64 to 63", 6),
            ("smartd", "Device: /dev/sdb [SAT], SMART Prefailure Attribute: 1 Raw_Read_Error_Rate changed from 118 to 117", 6),
        ],
        "keep": [
            ("smartd", "Device: /dev/sdc [SAT], SMART Prefailure Attribute: 5 Reallocated_Sector_Ct changed from 100 to 99", 6),
            ("smartd", "Device: /dev/sdc [SAT], SMART Usage Attribute: 197 Current_Pending_Sector changed from 100 to 99", 6),
            ("smartd", "Device: /dev/sda [SAT], 8 Currently unreadable (pending) sectors", 2),
        ],
    },
    "arr_chatter": {
        "drop": [
            ("radarr", "[Info] RefreshMovieService: Updating info for The Matrix", 6),
            ("sonarr", "[Info] RefreshSeriesService: Updating Example Show", 6),
            ("radarr", "[Info] DiskScanService: Scanning disk for The Matrix", 6),
            ("radarr", "[Info] DiskScanService: Completed scanning disk for The Matrix", 6),
            ("sonarr", "[Info] DiskScanService: Scanning Example Show", 6),
            ("radarr", "[Info] ExistingMetadataImporter: Found 0 existing metadata files", 6),
            ("sonarr", "[Info] ExistingSubtitleImporter: Found 0 existing subtitle files", 6),
            ("radarr", "[Info] ExistingOtherExtraImporter: Found 0 existing other extra files", 6),
            ("radarr", "[Info] ExistingExtraFileService: Found 0 possible extra files, imported 0 files.", 6),
        ],
        "keep": [
            ("radarr", "[Warn] DiskScanService: Movie folder doesn't exist: /movies/The Matrix", 4),
            ("sonarr", "[Info] ExistingSubtitleImporter: Found 2 existing subtitle files", 6),
            ("radarr", "[Error] RefreshMovieService: Couldn't refresh info for The Matrix", 3),
            ("radarr", "[Info] DownloadDecisionMaker: Processing 100 releases", 6),
        ],
    },
    "ha_custom_integration": {
        "drop": [
            ("homeassistant", "We found a custom integration hacs which has not been tested by Home Assistant. This component might cause stability problems, be sure to disable it if you experience issues with Home Assistant", 4),
            ("homeassistant", "2026-11-09 12:00:00.123 WARNING (SyncWorker_0) [homeassistant.loader] We found a custom integration logshed which has not been tested by Home Assistant. This component might cause stability problems, be sure to disable it if you experience issues with Home Assistant", 4),
        ],
        "keep": [
            ("homeassistant", "Error setting up entry for hacs", 3),
            ("homeassistant", "We found a custom integration hacs which has not been tested by Home Assistant", 3),
        ],
    },
}

PRESET_CATEGORIES = {
    "Security", "Storage and disks", "Backups", "Power", "Network", "VMs and containers", "System", "Apps",
}


def _compiled_alert_rule(preset: dict):
    from app.services.alert_evaluator import CompiledAlertRule

    return CompiledAlertRule(
        id=1, name=preset["name"], rule_type=preset["rule_type"], channel_id=None,
        filter_app=preset.get("filter_app"), filter_severity=preset.get("filter_severity"),
        match_pattern=preset.get("match_pattern"), threshold_count=preset["threshold_count"],
        window_seconds=preset["window_seconds"], cooldown_seconds=preset["cooldown_seconds"],
        ai_enrichment=False, is_enabled=True,
    )


def _drop_filter_for(preset: dict, tmp_path: Path):
    """A drop filter holding the preset as installed: its hosts, apps, pattern and severity range."""
    from app.core.migrations import get_connection
    from app.services.drop_filter import DropFilter

    db_file = tmp_path / "preset_drop.db"
    run_migrations(db_file)
    conn = get_connection(db_file)
    conn.execute(
        "INSERT INTO drop_rules (name, source_pattern, app_pattern, message_pattern, is_regex, is_enabled, "
        "severity_threshold, dropped_count, created_at) VALUES (?, ?, ?, ?, ?, 1, ?, 0, datetime('now'))",
        (
            preset["name"], preset.get("source_pattern"), preset.get("app_pattern"), preset["message_pattern"],
            1 if preset.get("is_regex") else 0, preset.get("severity_threshold"),
        ),
    )
    conn.commit()
    conn.close()
    flt = DropFilter(db_file)
    assert len(flt._rules) == 1, "preset must compile on the drop rule engine"
    return flt


class TestShippedPresetsWithUserRegexEngine:
    """Every shipped preset validates, and matches its real sample lines (and only those) on the `regex` engine."""

    def test_every_preset_with_a_pattern_has_samples(self):
        with_pattern = {p["id"] for p in get_alert_presets() if p.get("match_pattern")}
        assert with_pattern == set(ALERT_PRESET_SAMPLES)
        drop_ids = {p["id"] for p in get_drop_presets()}
        assert drop_ids == set(DROP_PRESET_SAMPLES)

    def test_every_shipped_preset_has_a_known_category_and_plain_text(self):
        for preset in get_alert_presets() + get_drop_presets():
            assert preset.get("category") in PRESET_CATEGORIES, preset["id"]
            for field in ("name", "description"):
                assert preset[field].isascii(), f"{preset['id']} {field} must be plain ASCII"

    def test_existing_preset_ids_are_kept(self):
        alert_ids = {p["id"] for p in get_alert_presets()}
        drop_ids = {p["id"] for p in get_drop_presets()}
        assert {"ssh_bruteforce", "proxy_auth_flood", "sudo_escalation", "oom_killer", "log_storm_detection"} <= alert_ids
        assert {"cron_noise", "docker_healthcheck", "systemd_session"} <= drop_ids

    @pytest.mark.parametrize("preset_id", sorted(ALERT_PRESET_SAMPLES))
    def test_alert_preset_validates_and_matches_samples(self, preset_id: str):
        from app.core.regex_validator import check_regex_safety

        preset = get_alert_preset_by_id(preset_id)
        assert check_regex_safety(preset["match_pattern"]) == (True, None)
        rule = _compiled_alert_rule(preset)
        samples = ALERT_PRESET_SAMPLES[preset_id]
        app = preset.get("filter_app") or "app"
        for line in samples["match"]:
            assert rule.matches({"message": line, "raw": line, "app_name": app, "severity": 6}) is True, line
        for line in samples["miss"] + ["routine heartbeat ok"]:
            assert rule.matches({"message": line, "raw": line, "app_name": app, "severity": 6}) is False, line

    def test_critical_severity_preset_matches_by_severity_only(self):
        rule = _compiled_alert_rule(get_alert_preset_by_id("critical_severity"))
        for severity in (0, 1, 2):
            assert rule.matches({"message": "anything", "app_name": "kernel", "severity": severity}) is True
        for severity in (3, 4, 6):
            assert rule.matches({"message": "anything", "app_name": "kernel", "severity": severity}) is False

    def test_fail2ban_preset_waits_for_several_bans(self):
        preset = get_alert_preset_by_id("fail2ban_bans")
        assert preset["rule_type"] == "threshold"
        assert preset["threshold_count"] > 1

    @pytest.mark.parametrize("preset_id", sorted(DROP_PRESET_SAMPLES))
    def test_drop_preset_validates_and_drops_only_its_samples(self, preset_id: str, tmp_path: Path):
        from app.core.regex_validator import check_regex_safety

        preset = get_drop_preset_by_id(preset_id)
        if preset.get("is_regex"):
            assert check_regex_safety(preset["message_pattern"]) == (True, None)
        flt = _drop_filter_for(preset, tmp_path)
        samples = DROP_PRESET_SAMPLES[preset_id]
        for app, line, severity in samples["drop"]:
            assert flt.should_drop(None, "192.168.1.10", app, line, severity) is not None, line
        for app, line, severity in samples["keep"] + [("app", "routine heartbeat ok", 6)]:
            assert flt.should_drop(None, "192.168.1.10", app, line, severity) is None, line

    @pytest.mark.parametrize("preset_id", sorted(DROP_PRESET_SAMPLES))
    def test_drop_preset_never_drops_errors(self, preset_id: str, tmp_path: Path):
        """A preset with a severity range keeps its own lines when they arrive as errors."""
        preset = get_drop_preset_by_id(preset_id)
        if preset.get("severity_threshold") is None:
            pytest.skip("preset drops at any severity")
        flt = _drop_filter_for(preset, tmp_path)
        for app, line, _ in DROP_PRESET_SAMPLES[preset_id]["drop"]:
            assert flt.should_drop(None, "192.168.1.10", app, line, 3) is None, line

    @pytest.mark.asyncio
    @pytest.mark.parametrize("preset_id", sorted(ALERT_PRESET_SAMPLES) + ["log_storm_detection", "critical_severity"])
    async def test_every_alert_preset_installs(self, client: AsyncClient, auth_headers: dict, preset_id: str):
        res = await client.post(f"/api/alerts/presets/{preset_id}/install", headers=auth_headers)
        assert res.status_code == 201

    @pytest.mark.asyncio
    @pytest.mark.parametrize("preset_id", sorted(DROP_PRESET_SAMPLES))
    async def test_every_drop_preset_installs(self, client: AsyncClient, auth_headers: dict, preset_id: str):
        res = await client.post(f"/api/drop-rules/presets/{preset_id}/install", headers=auth_headers)
        assert res.status_code == 201

    @pytest.mark.asyncio
    async def test_presets_list_carries_categories(self, client: AsyncClient, auth_headers: dict):
        alerts = (await client.get("/api/alerts/presets", headers=auth_headers)).json()
        drops = (await client.get("/api/drop-rules/presets", headers=auth_headers)).json()
        assert {p["id"]: p["category"] for p in alerts}["ups_power"] == "Power"
        assert {p["id"]: p["category"] for p in drops}["arr_chatter"] == "Apps"

    @pytest.mark.asyncio
    async def test_installing_a_preset_twice_adds_a_second_rule(self, client: AsyncClient, auth_headers: dict):
        for _ in range(2):
            assert (await client.post("/api/drop-rules/presets/rsyslog_mark/install", headers=auth_headers)).status_code == 201
            assert (await client.post("/api/alerts/presets/ups_power/install", headers=auth_headers)).status_code == 201
        drops = (await client.get("/api/drop-rules", headers=auth_headers)).json()
        assert sum(1 for r in drops if r["message_pattern"] == "-- MARK --") == 2
        alerts = (await client.get("/api/alerts/rules", headers=auth_headers)).json()
        assert sum(1 for r in alerts if r["name"] == "UPS On Battery or Low Battery") == 2

    @pytest.mark.asyncio
    async def test_custom_alert_preset_with_dangerous_pattern_is_refused(
        self, client: AsyncClient, auth_headers: dict, tmp_path: Path
    ):
        user_dir = tmp_path / "presets" / "alerts"
        user_dir.mkdir(parents=True, exist_ok=True)
        (user_dir / "alert_custom_bad.json").write_text(
            json.dumps({
                "id": "custom_bad",
                "name": "Custom Bad",
                "description": "Overlapping alternation",
                "rule_type": "pattern",
                "match_pattern": r"(\w|\d)+$",
                "threshold_count": 1,
                "window_seconds": 60,
                "cooldown_seconds": 300,
                "ai_enrichment": False,
            }),
            encoding="utf-8",
        )
        res = await client.post("/api/alerts/presets/custom_bad/install", headers=auth_headers)
        assert res.status_code == 400
        assert "catastrophic backtracking" in res.json()["detail"].lower()


class TestSystemdSessionDropPreset:
    """The systemd session preset drops exactly the PAM systemd-user session lines."""

    @staticmethod
    def _filter(tmp_path: Path):
        from app.core.migrations import get_connection
        from app.services.drop_filter import DropFilter

        preset = get_drop_preset_by_id("systemd_session")
        db_file = tmp_path / "systemd_preset.db"
        run_migrations(db_file)
        conn = get_connection(db_file)
        conn.execute(
            "INSERT INTO drop_rules (name, source_pattern, app_pattern, message_pattern, is_regex, is_enabled, dropped_count, created_at) "
            "VALUES (?, NULL, NULL, ?, 1, 1, 0, datetime('now'))",
            (preset["name"], preset["message_pattern"]),
        )
        conn.commit()
        conn.close()
        return DropFilter(db_file)

    def test_drops_session_opened_and_closed_lines(self, tmp_path: Path):
        flt = self._filter(tmp_path)
        opened = "pam_unix(systemd-user:session): session opened for user ben(uid=1000) by (uid=0)"
        closed = "pam_unix(systemd-user:session): session closed for user ben"
        assert flt.should_drop(None, None, None, opened) is not None
        assert flt.should_drop(None, None, None, closed) is not None

    def test_does_not_drop_unrelated_session_closed_lines(self, tmp_path: Path):
        flt = self._filter(tmp_path)
        assert flt.should_drop(None, None, None, "sshd: session closed for user ben") is None
        assert flt.should_drop(None, None, None, "pam_unix(sshd:session): session opened for user ben") is None

    def test_pattern_passes_the_user_pattern_validator(self):
        from app.core.regex_validator import compile_user_pattern
        import re

        preset = get_drop_preset_by_id("systemd_session")
        compile_user_pattern(preset["message_pattern"], re.IGNORECASE)
