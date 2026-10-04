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

- [OPNsense](#opnsense)
- [Proxmox VE](#proxmox-ve)
- [Synology DSM](#synology-dsm)
- [UniFi Network](#unifi-network)
- [pfSense](#pfsense)
- [Home Assistant](#home-assistant)
- [Generic Linux with rsyslog](#generic-linux-with-rsyslog)
- [Generic Linux with syslog-ng](#generic-linux-with-syslog-ng)
- [Docker Containers (via DockerTailer)](#docker-containers-via-dockertailer)

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

Home Assistant does not natively stream internal application logs to a remote syslog daemon out of the box. Using the **Remote Logger** integration via HACS alongside Home Assistant's native event bus, you can forward Core warnings, errors, and custom integration issues directly to your syslog server with real-time dispatch and zero device telemetry spam.

### Configuration Steps

1. Enable log event broadcasting in `configuration.yaml`:
   - Open your `configuration.yaml` file and add the following entry:
     ```yaml
     system_log:
       fire_event: true
     ```
   - *(By default, Home Assistant does not broadcast UI log entries to the internal event bus; this directive allows the integration to intercept them).*
2. Install the **Remote Logger** integration via HACS:
   - In the Home Assistant sidebar, navigate to: **HACS**.
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
- **Application (`app_name`)**: The container name (stripped of the leading slash) becomes the application name.
- **Source (`source` / `source_alias`)**: The Docker host name or the value configured in `DOCKER_SOURCE_ALIAS` (defaults to `docker`) becomes the log source.
- **Severity**: Output written to `stderr` is assigned severity level `3 - Error` or `4 - Warning`, while output to `stdout` is assigned severity level `6 - Info`.

### Excluding Containers

If you run high-volume containers whose logs you do not want in LogShed, exclude them using either:
- The `DOCKER_EXCLUDE_CONTAINERS` environment variable (comma-separated list of container names).
- The web interface under **Settings > Advanced > Exclude Containers**.
