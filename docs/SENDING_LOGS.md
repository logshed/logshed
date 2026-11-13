# Forwarding Logs to LogShed

This guide provides step-by-step instructions for forwarding syslog traffic and container logs from network appliances, hypervisors, NAS devices, Linux servers, and Docker containers into LogShed.

---

> [!IMPORTANT]
> **Default Ports & Port Mapping:**
> LogShed listens on UDP port `1514` and TCP port `1514` by default.
>
> Many appliances and network devices forward to the standard syslog port `514` by default. If your forwarders cannot specify a custom destination port, map host port `514` to container port `1514` in your `docker-compose.yml` or `docker run` command:
>
> ```yaml
> ports:
>   - "514:1514/udp"
>   - "514:1514/tcp"
> ```
>
> Both listening ports can also be modified directly using the `SYSLOG_PORT` environment variable. See [CONFIGURATION.md](CONFIGURATION.md) for full details.

---

## Table of Contents

- [UDP or TCP Syslog](#udp-or-tcp-syslog)
- [Reading the Navbar Health Figures](#reading-the-navbar-health-figures)
- [OPNsense](#opnsense)
- [Proxmox VE](#proxmox-ve)
- [Synology DSM](#synology-dsm)
- [UniFi Network](#unifi-network)
- [pfSense](#pfsense)
- [Home Assistant](#home-assistant)
- [Generic Linux with rsyslog](#generic-linux-with-rsyslog)
- [Generic Linux with syslog-ng](#generic-linux-with-syslog-ng)
- [Docker Containers (via DockerTailer)](#docker-containers-via-dockertailer)
- [HTTP Ingest API](#http-ingest-api)

---

## UDP or TCP Syslog

Most devices let you choose UDP or TCP when you add LogShed as a syslog target. The choice matters when a lot of logs arrive at once.

- **UDP can lose messages in bursts.** UDP has no delivery guarantee. If a burst arrives faster than LogShed reads it, the kernel discards the datagrams that do not fit in the socket receive buffer. The sender is not told, and LogShed never sees those messages, so they are not part of the **Lost** figure. LogShed counts them separately and shows **UDP lost: N** in the navbar (only when above 0). The same count is `udp_receive_errors` in `GET /api/health` and `GET /api/v1/system/metrics`.
- **TCP does not lose messages this way.** TCP resends data and slows the sender down instead of discarding it, so nothing is lost in the kernel buffer. LogShed also slows TCP senders down while its own queues are full, for up to 2 seconds at a time when storage falls behind, instead of discarding their messages. Only a longer stall (such as a large delete) loses TCP messages, and those count in **Lost**. A TCP sender that keeps sending faster than LogShed can store is held back for as long as it does, so give such a sender a queue of its own (rsyslog and syslog-ng have one) rather than letting it block the program that writes the logs. Use TCP for sources that must not lose lines (firewalls, authentication logs), when the device supports it. Persistent TCP connections count against `SYSLOG_MAX_TCP_CONNECTIONS` (250) and `SYSLOG_MAX_TCP_CONNECTIONS_PER_IP` (20).
- **Raise the UDP buffer for busy UDP sources.** LogShed asks for an 8 MiB receive buffer by default (`SYSLOG_UDP_RECEIVE_BUFFER_BYTES`), but the kernel caps the request at `net.core.rmem_max`, which is only 212992 bytes on many Linux hosts. The startup log reports the size LogShed actually got and logs a warning with the fix when it is smaller than requested. On the Docker host, run:

  ```bash
  sudo sysctl -w net.core.rmem_max=8388608                           # takes effect at once
  echo "net.core.rmem_max=8388608" | sudo tee -a /etc/sysctl.conf    # keeps it after a reboot
  ```

  Then restart the LogShed container. Do not try to set this with a `sysctls:` entry in `docker-compose.yml`: Docker refuses `net.core.rmem_max` there because it is a host-wide setting. If you cannot change the host, set `SYSLOG_UDP_RECEIVE_BUFFER_BYTES` to the size the startup log reports to silence the warning (the buffer then stays small).
- **Watch the processing load.** When LogShed gets close to the most it can handle, UDP messages start to be lost before anything else goes wrong. The navbar shows how close it is: see [Reading the Navbar Health Figures](#reading-the-navbar-health-figures).
- **What "UDP lost" counts.** It is the kernel's receive-buffer error counter for UDP since the syslog listener started. The counter belongs to the network namespace: in a normal container it counts only LogShed's own sockets, but with `network_mode: host` it counts every UDP socket on the host, including ones that have nothing to do with syslog. It is blank (and the navbar hides it) when LogShed cannot read it, for example on a host that is not Linux.

---

## Reading the Navbar Health Figures

The figures next to the LogShed logo show how LogShed is keeping up with the logs it receives. Rest the mouse on a figure to see what it means. They refresh every 10 seconds while the tab is open.

| Figure | What it means | What is normal |
|---|---|---|
| **Rate** | Logs received per second, averaged over the last 5 seconds. | Whatever your senders produce. A sudden jump usually means a device is in trouble or has become noisy. |
| **Queue** | Logs received but not yet stored. The queue holds up to 10,000. | Close to 0. It may climb briefly in a burst and then drain. |
| **Lost** (only when above 0) | Logs LogShed had to turn away because the queue was full, since it last started. Logs removed by drop rules are not counted here. | Not shown. |
| **UDP lost** (only when above 0) | Syslog UDP messages the operating system threw away before LogShed could read them, since it last started. They are not part of Lost. See [UDP or TCP Syslog](#udp-or-tcp-syslog). | Not shown. |
| **Amber dot** | LogShed is near its processing limit, or the health check failed. Rest the mouse on it for the reason. | Not shown. |

### Event loop lag (in the Rate tooltip)

LogShed does nearly all its work in one place, called the event loop: reading syslog, applying drop and alert rules, streaming to open Console tabs and answering the web interface. The loop works through one small job at a time. When there is more to do than it can keep up with, every job waits a little longer for its turn.

To measure this, LogShed asks to be woken every 100 ms and records how late each wake-up actually is. That delay is the **loop lag**. The Rate tooltip shows three figures for the last 60 seconds:

- **now:** the lag of the latest wake-up.
- **95th percentile:** 95 out of 100 wake-ups were at least this punctual. This is the figure to watch: it shows how LogShed is coping in general and ignores rare spikes.
- **max:** the single worst wake-up. A spike of a few hundred milliseconds is normal during a large search, a delete or the nightly prune.

A healthy LogShed shows a 95th percentile of a few milliseconds. While the lag is high, LogShed reads its syslog sockets late, and UDP messages that arrive in the meantime can overflow the receive buffer and show up as **UDP lost**.

### When the amber dot turns on

The amber dot appears when one of these has held for 30 seconds (three health checks in a row):

- the 95th percentile loop lag is 50 ms or more, or
- the queue holds at least 5,000 logs (half full), or the Lost count keeps growing.

Its tooltip says which. The dot clears at the first check where neither holds. If it stays on:

- send fewer logs: drop noisy sources at the sender, or with [drop rules](RULES_GUIDE.md#ingestion-drop-rules) in LogShed;
- simplify or remove regex alert and drop rules;
- move busy senders to TCP, which slows them down instead of losing messages.

The same figures are in `GET /api/health` and `GET /api/v1/system/metrics`: `ingest_rate`, `queue_depth`, `dropped_logs` (Lost), `udp_receive_errors` (UDP lost), and `loop_lag_ms`, `loop_lag_p95_ms` and `loop_lag_max_ms`.

---

## OPNsense

OPNsense includes native remote syslog forwarding support for system processes, daemon events, and packet filter logs (`filterlog`).

### Configuration Steps

1. In the OPNsense web management interface, navigate to:
   **System > Settings > Logging > Remote Syslog**
   *(On newer releases, navigate to **System > Settings > Logging > Targets** and click **+ Add**)*.
2. Configure the connection fields:
   - **Enable**: Check the box to enable remote syslog forwarding.
   - **Transport**: Select **UDP** (or **TCP** if supported on your version).
   - **IP / Hostname**: Enter the IP address of your LogShed server.
   - **Port**: Enter `514` (if port 514 is mapped to 1514) or `1514`.
3. Configure log facilities:
   - We recommend checking all facilities to capture complete firewall and system state.
   - At a minimum, select:
     - `kern` (Kernel messages, firewall state events)
     - `auth` (Authentication and login audits)
     - `authpriv` (Privileged authentication actions)
     - `daemon` (System services, Unbound DNS, DHCP)
4. Click **Save** and **Apply changes**.

---

## Proxmox VE

Proxmox VE runs on Debian GNU/Linux and uses `rsyslog` for system event logging. You can forward host events, cluster events (`corosync`), web management logs (`pveproxy`), and virtual machine lifecycle actions to LogShed.

### Configuration Steps

1. Log into your Proxmox VE host via SSH or the web console as `root`.
2. Create a dedicated rsyslog configuration file:
   ```bash
   nano /etc/rsyslog.d/logshed.conf
   ```
3. Add the forwarding directive depending on your preferred transport:

   **For UDP forwarding:**
   ```text
   *.* @<LOGSHED_IP>:1514
   ```

   **For TCP forwarding:**
   ```text
   *.* @@<LOGSHED_IP>:1514
   ```

   *(Replace `<LOGSHED_IP>` with the IP address of your LogShed instance, and adjust the port to `514` if host port mapping is in use).*

4. Restart the rsyslog daemon:
   ```bash
   systemctl restart rsyslog
   ```

### Useful Log Sources in Proxmox

Once connected, watch for the following key application tags in LogShed:
- `pve-manager`: VM and container creation, migration, and backup tasks.
- `pveproxy`: REST API access logs and web management authentication attempts.
- `pvedaemon`: Background cluster worker and task execution.
- `qemu-server` / `lxc`: Virtual machine and Linux container runtime messages.
- `corosync`: Multi-node cluster quorum and heartbeat state.

---

## Synology DSM

Synology DiskStation Manager (DSM) provides a built-in Log Center application that forwards system events, connection audits, and file transfer logs to remote syslog collectors.

### Configuration Steps

1. Open Synology DSM and open **Control Panel**.
2. Select **Log Center** (under the Applications group).
3. In the left navigation sidebar, select **Log Sending**.
4. Check the box marked **Send logs to a syslog server**.
5. Enter the following parameters:
   - **Server**: Enter the IP address of your LogShed server.
   - **Port**: Enter `514` (or `1514` depending on your port mapping).
   - **Transfer protocol**: Select **UDP**.
   - **Log format**: Select **BSD (RFC 3164)** or **IETF (RFC 5424)**. LogShed automatically parses both standards.
6. Under the **Filter** section, select which categories to forward (System, Connection, File Transfer).
7. Click **Apply**.
8. Optional: Click **Send test log** to verify that LogShed receives the test message immediately in the live stream.

---

## UniFi Network

UniFi Network controllers and gateways (UniFi Dream Machine, Dream Router, Cloud Gateway, or self-hosted UniFi Network Application) forward access point, switch, and security gateway events using standard syslog.

### Configuration Steps

1. Log into your UniFi Network application interface.
2. Navigate to:
   **Settings > System > Advanced > Remote Logging**
3. Enable the **Syslog** toggle.
4. Fill in the connection settings:
   - **IP Address**: Enter the IP address of your LogShed server.
   - **Port**: Enter `514` (or `1514`).
5. Choose your desired logging level (e.g. **Normal** or **Debug**).
6. Click **Apply Changes**.

### UniFi Log Facility Note

UniFi gateways, switches, and access points typically dispatch syslog messages under the `local0` or `daemon` facilities. Events from UniFi include Wi-Fi client association handshakes, rogue access point detections, port link state transitions, and gateway threat detections.

---

## pfSense

pfSense includes remote syslog capabilities to transmit packet filter activity, authentication attempts, and system events over RFC 3164 syslog.

### Configuration Steps

1. In the pfSense web dashboard, navigate to:
   **Status > System Logs > Settings**
2. Scroll down to the **Remote Logging Options** section.
3. Check **Enable Remote Logging**.
4. Configure the destination:
   - **Remote log servers**: Enter the IP address and port of your LogShed server (for example, `<LOGSHED_IP>:514` or `<LOGSHED_IP>:1514`).
5. In the **Remote Syslog Contents** section, check the event categories you wish to forward:
   - **System Events** (Kernel, system service messages)
   - **Firewall Events** (Filter rules and state table messages)
   - **DHCP Service** (Lease grants, renewals)
   - **DNS Events** (Unbound / DNS resolver queries)
   - **Authentication Events** (SSH and web UI logins)
6. Click **Save**.

### Format Note

pfSense generates BSD-style (RFC 3164) log payloads. LogShed natively extracts the originating hostname, application name, and severity code from pfSense frames.

---

## Home Assistant

Home Assistant does not natively stream internal application logs to a remote syslog daemon out of the box. Using the **Remote Logger** integration from the Home Assistant [Marketplace](https://www.home-assistant.io/integrations/marketplace/) (or HACS on releases before 2026.11) alongside Home Assistant's native event bus, you can forward Core warnings, errors, and custom integration issues directly to your syslog server with real-time dispatch and zero device telemetry spam.

### Configuration Steps

1. Enable log event broadcasting in `configuration.yaml`:
   - Open your `configuration.yaml` file and add the following entry:
     ```yaml
     system_log:
       fire_event: true
     ```
   - *(By default, Home Assistant does not broadcast UI log entries to the internal event bus; this directive allows the integration to intercept them).*
2. Install the **Remote Logger** integration via the Home Assistant Marketplace (or HACS):
   - In Home Assistant, navigate to **Settings > Marketplace** (or **HACS** in the sidebar on releases before 2026.11).
   - Search for **Remote Logger** (by Rhizomatics) and click **Download**.
   - Restart Home Assistant under **Settings > System > Restart** to load both the integration and the YAML changes.
3. In the Home Assistant web interface, navigate to:
   **Settings > Devices & Services > Add Integration**
   *(Search for **Remote Logger**, select it, and choose **Syslog** as the output type)*.
4. Configure the connection fields:
   - **Host / IP**: Enter the IP address of your syslog server.
   - **Port**: Enter `1514` (or your target syslog listener port).
   - **Transport**: Select **UDP**.
   - **Syslog Facility**: Select `local0` (recommended user-defined facility) or `daemon`.
   - **Batch Max Size**: Set to `1` *(forces immediate dispatch, preventing sporadic warnings and errors from waiting on the default ~60-second batch timer)*.
   - Click **Submit**.
5. Configure event subscriptions:
   - **Use System Log events in place of Python root logger**: Toggle **ON** (captures the exact warnings and errors visible in **Settings > System > Logs**, including custom integration errors).
   - **Home Assistant lifecycle events**: Optional (enable to capture start and stop events).
   - **Home Assistant core change events**: Toggle **OFF**.
   - **Home Assistant core activity**: Toggle **OFF**.
   - **Home Assistant state changes**: Toggle **OFF** (ensures sensor telemetry and entity state updates are excluded).
6. Click **Submit** to apply and save changes.

---

## Generic Linux with rsyslog

Most Linux distributions (Debian, Ubuntu, Fedora, AlmaLinux, Rocky Linux) come pre-installed with `rsyslog`.

### Configuration Steps

1. Create a configuration file in `/etc/rsyslog.d/`:
   ```bash
   sudo nano /etc/rsyslog.d/10-logshed.conf
   ```

2. Add a forwarding rule:

   **For UDP forwarding (single `@`):**
   ```text
   *.* @<LOGSHED_IP>:514
   ```

   **For TCP forwarding (double `@@`):**
   ```text
   *.* @@<LOGSHED_IP>:514
   ```

   *(Replace `<LOGSHED_IP>` with your LogShed host IP, and replace `514` with `1514` if using the direct container port).*

3. Restart the rsyslog service:
   ```bash
   sudo systemctl restart rsyslog
   ```

---

## Generic Linux with syslog-ng

If your distribution uses `syslog-ng` instead of `rsyslog`, configure a destination block and link it to your primary system log source.

### Configuration Steps

1. Create a configuration drop-in file:
   ```bash
   sudo nano /etc/syslog-ng/conf.d/logshed.conf
   ```

2. Add the destination and log blocks:

   **For UDP forwarding:**
   ```text
   destination d_logshed {
       udp("<LOGSHED_IP>" port(514));
   };

   log {
       source(s_src);
       destination(d_logshed);
   };
   ```

   **For TCP forwarding:**
   ```text
   destination d_logshed {
       tcp("<LOGSHED_IP>" port(514));
   };

   log {
       source(s_src);
       destination(d_logshed);
   };
   ```

3. Restart the syslog-ng service:
   ```bash
   sudo systemctl restart syslog-ng
   ```

---

## Docker Containers (via DockerTailer)

LogShed includes a built-in Docker tailing engine (`DockerTailer`) that connects directly to the Docker Engine API.

### How It Works

- LogShed communicates with the Docker Engine through the local Unix socket (`/var/run/docker.sock`) or a remote Docker socket proxy specified by `DOCKER_HOST`.
- **Zero Configuration Required**: Individual containers do not require agents, logging drivers, or special configuration. Any container writing to standard output (`stdout`) or standard error (`stderr`) is automatically discovered and streamed.

### How Container Logs Are Tagged

LogShed automatically structures container log records as follows:
- **Application (`app_name`)**: The first of these that is set: the `logshed.alias` or `logshed.name` container label, the Compose service name (`com.docker.compose.service`), the Swarm service name (`com.docker.swarm.service.name`), the container name (without the leading slash), or the first 12 characters of the container ID.
- **Source (`source` / `source_alias`)**: The Docker host name or the value configured in `DOCKER_SOURCE_ALIAS` (defaults to `docker`) becomes the log source.
- **Severity**: Output written to `stderr` is assigned severity level `3 - Error` or `4 - Warning`, while output to `stdout` is assigned severity level `6 - Info`.

### Excluding Containers

If you run high-volume containers whose logs you do not want in LogShed, exclude them using either:
- The `DOCKER_EXCLUDE_CONTAINERS` environment variable (comma-separated list of container names).
- The web interface under **Settings > Advanced > Exclude Containers**.

---

## HTTP Ingest API

Tools that cannot send syslog (scripts, Vector, Fluent Bit, edge services) can push logs over HTTP.

### Token

Create an API token in **Settings > API & MCP Access** with the `logs:write` scope (the "Edge Shipper / Ingest" preset has exactly that; choose **Custom** to tick the scopes yourself when no preset fits; see the [API Token Scopes Guide](API_SCOPES.md)). The raw token (`ls_live_...`) is shown once. Send it in the `Authorization: Bearer <token>` header (the word `Bearer` is accepted in any letter case). Each token may send up to 6,000 requests per minute; responses carry `X-RateLimit-Limit`, `X-RateLimit-Remaining` and `X-RateLimit-Reset`, and a `429` response also carries `Retry-After`. The name and scopes of a token can be changed later with **Edit** in the token list, without creating a new token: the token string stays the same, so Vector, Fluent Bit or script configurations need no update, and the change applies to the next request.

### Request

`POST /api/v1/ingest` with a JSON body in one of three shapes:
- a JSON array of entries: `[{"message": "a"}, {"message": "b"}]`;
- a single JSON object: `{"message": "a"}`, on one line or spread over several (pretty-printed);
- newline-delimited JSON (NDJSON), one entry per line (a line may also hold an array of entries).

A malformed body is refused with `400` naming the line, and nothing from it is stored. So is a body or NDJSON line that is a bare value instead of an object (a number, a string, `true`, `false` or `null`): "Log entry on line N is not a JSON object." Inside an array, values that are not objects are skipped, so one bad item does not drop the rest of the batch.

| Field | Meaning | Default |
|---|---|---|
| `message` (or `msg`) | The log text | empty |
| `timestamp` (or `time`, `ts`) | When the event happened: an ISO 8601 string, or Unix time in seconds or milliseconds, as a number or as a string of exactly 10 (seconds) or 13 (milliseconds) digits | the time LogShed received the entry |
| `severity` (or `level`) | `0` to `7`, or a name such as `error`, `warning`, `info` or `debug` | `6` (info) |
| `app_name` (or `app`) | Application or service name | `app` |
| `source_ip` | Address or name of the sending host | the address the request came from (behind a proxy listed in `TRUSTED_PROXIES`, the client address it passes on in `X-Forwarded-For`) |
| `source_alias` | Display name of the source host | `source_ip` |
| `raw` | The original unparsed line | `message` |
| `facility` | Syslog facility `0` to `23` | `1` |

Each entry is stored as one log entry. Entries are not merged with the previous one (indented lines or lines starting with `at ...` stay separate), and ANSI and control characters are removed from the text. Text that is not valid Unicode, such as a lone surrogate escape like `\ud800` in the JSON, is stored with the replacement character U+FFFD instead. Your drop rules and any maintenance `drop_errors` session apply to ingested entries as they do to syslog.

### Limits

- **Body size:** at most `INGEST_MAX_BODY_BYTES` (5 MiB by default); a larger body returns `413`.
- **Entries:** at most 10,000 per request; more returns `413`.
- **Text length:** `message` and `raw` are cut to 64 KB (65,536 characters) each.
- **Field length:** `source_ip`, `source_alias` and `app_name` are cut to 255 characters each. The entry is stored, not refused.
- **Timestamps:** an entry timestamp that is unreadable, older than your retention limit (`MAX_RETENTION_DAYS`, default 30 days), or more than 5 minutes ahead of the server clock is replaced with the time LogShed received the entry. A device with a clock up to 5 minutes fast keeps its own timestamps. This 5 minute window applies to every source, not only HTTP ingest.
- **Reserved identities:** LogShed's own identities cannot be used. If any entry has `source_ip` `docker` or starting with `docker:`, or `source_ip` `127.0.0.1` together with `source_alias` `logshed` (in any letter case), the **whole request** is refused with `422` and nothing is stored.

### Response

```json
{"status": "ok", "accepted": 2, "dropped": 0}
```

`accepted` is the number of entries queued for storage. `dropped` counts entries removed by a drop rule or a maintenance `drop_errors` session, or lost because LogShed's in-memory queue was full. Other status codes: `400` (the body is not valid JSON), `401` (missing or invalid token), `403` (the token lacks `logs:write`), `413`, `422` and `429` as above.

### Example

```bash
curl -X POST http://<LOGSHED_IP>:8080/api/v1/ingest \
  -H "Authorization: Bearer ls_live_REPLACE_WITH_YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '[
    {"message": "backup finished", "app_name": "backup", "source_alias": "nas", "severity": "info"},
    {"message": "disk almost full", "app_name": "backup", "source_alias": "nas", "severity": "warning", "timestamp": 1760000000}
  ]'
```

An NDJSON body works the same way; send one JSON object per line and use `Content-Type: application/x-ndjson`. To explore the other endpoints, open `/docs` in the browser after logging in to LogShed.
