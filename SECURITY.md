# Security Policy

## Supported Versions

Security fixes are applied to the latest stable minor release series.

| Version | Supported |
| :--- | :--- |
| 1.3.x | Yes |
| < 1.3.0 | No (please upgrade to 1.3.0+) |

## Reporting a Vulnerability

**Please do not report security vulnerabilities through public GitHub issues.**

Instead, please report them privately using [GitHub Private Vulnerability Reporting](https://github.com/logshed/logshed/security/advisories/new) on the **Security** tab of this repository.

When submitting a report, please include:
- The version of LogShed affected.
- A clear description of the vulnerability and its potential impact.
- Step-by-step instructions or a minimal proof-of-concept to reproduce the issue.

We will acknowledge receipt of your report within 7 days and work with you to validate and remediate the issue before public disclosure.

## Deployment & Scope Notes

LogShed is built for personal servers and home networks, not multi-tenant enterprise environments. Keep the following design boundaries in mind when evaluating security:

- **Network Exposure:** Do not expose port `8080` or syslog port `1514` directly to the public internet without a reverse proxy, TLS termination, or a VPN (such as Tailscale or WireGuard).
- **Docker Socket Access:** Mounting `/var/run/docker.sock`, even with `:ro`, gives the container full control of the Docker daemon, which is equivalent to root on the host. For stricter isolation, connect LogShed through `tecnativa/docker-socket-proxy` with `CONTAINERS=1` and `POST=0`, or to a remote Docker host over mutual TLS.
- **AI Secret Redaction:** Built-in secret masking before AI prompt dispatch and MCP output is a best-effort safety net for common token and password formats. It cannot guarantee detection of every custom or unstructured secret.
