import type { LogEntry } from '../types.ts';

// Made-up homelab hosts and messages. Every address is private and every name is invented.
interface Host { alias: string; ip: string }
const HOSTS: Record<string, Host> = {
  pve1: { alias: 'pve-node1', ip: '192.168.10.11' },
  pve2: { alias: 'pve-node2', ip: '192.168.10.12' },
  nas: { alias: 'truenas', ip: '192.168.10.20' },
  router: { alias: 'opnsense', ip: '192.168.10.1' },
  sw: { alias: 'core-switch', ip: '192.168.10.2' },
  docker: { alias: 'docker-vm', ip: '192.168.10.30' },
  ha: { alias: 'homeassistant', ip: '192.168.10.40' },
  ups: { alias: 'ups', ip: '192.168.10.5' },
};

type Template = [host: keyof typeof HOSTS, app: string, severity: number, weight: number, message: () => string];

const pick = <T,>(items: T[]): T => items[Math.floor(Math.random() * items.length)];
const n = (lo: number, hi: number) => lo + Math.floor(Math.random() * (hi - lo + 1));
const lanIp = () => `192.168.10.${n(50, 199)}`;
const wanIp = () => `${pick([45, 61, 89, 103, 185, 193])}.${n(1, 254)}.${n(1, 254)}.${n(1, 254)}`;
const users = ['root', 'admin', 'ubuntu', 'pi', 'test', 'oracle'];
const paths = ['/', '/api/states', '/web/index.html', '/library/sections/2/all', '/socket.io/', '/favicon.ico', '/api/v1/status'];

const TEMPLATES: Template[] = [
  ['docker', 'nginx-proxy', 6, 14, () => `${lanIp()} - - "GET ${pick(paths)} HTTP/2.0" ${pick([200, 200, 200, 304, 101])} ${n(120, 48000)} "-" "Mozilla/5.0"`],
  ['docker', 'nginx-proxy', 4, 2, () => `${wanIp()} - - "GET /.env HTTP/1.1" 404 153 "-" "curl/8.5.0"`],
  ['docker', 'jellyfin', 6, 5, () => pick(['[INF] Playback started: "The Expanse S02E04" on Living Room TV', '[INF] Transcoding stopped for session 4f2a', '[INF] Scheduled task "Scan Media Library" completed after 0 minute(s) and 41 seconds'])],
  ['docker', 'jellyfin', 4, 1, () => '[WRN] Slow HTTP response: GET /Items/8a1/Images/Primary took 2341ms'],
  ['docker', 'paperless', 6, 3, () => `[INFO] [paperless.consumer] Document "scan_${n(1000, 9999)}.pdf" consumption finished`],
  ['docker', 'vaultwarden', 6, 2, () => `[INFO] [vaultwarden::api::identity] User admin@home.lan logged in successfully. IP: ${lanIp()}`],
  ['docker', 'postgres', 6, 2, () => `LOG:  checkpoint complete: wrote ${n(10, 900)} buffers (${(Math.random() * 3).toFixed(1)}%); 0 WAL file(s) added`],
  ['router', 'filterlog', 6, 10, () => `${n(60, 120)},,,1000000103,igb0,match,block,in,4,0x0,,${n(30, 250)},0,0,none,6,tcp,40,${wanIp()},203.0.113.7,${n(1024, 65000)},${pick([22, 23, 3389, 445, 8080])},0,S`],
  ['router', 'unbound', 6, 4, () => `[${n(1000, 9999)}:0] info: ${lanIp()} ${pick(['github.com', 'api.weather.gov', 'updates.jellyfin.org', 'time.cloudflare.com'])}. A IN`],
  ['router', 'sshd', 5, 2, () => `Invalid user ${pick(users)} from ${wanIp()} port ${n(30000, 65000)}`],
  ['router', 'dhcpd', 6, 3, () => `DHCPACK on ${lanIp()} to ${Array.from({ length: 6 }, () => n(16, 255).toString(16)).join(':')} via igb1`],
  ['pve1', 'pvedaemon', 6, 3, () => `<root@pam> successful auth for user 'root@pam'`],
  ['pve1', 'pveproxy', 6, 2, () => 'worker 3121 finished'],
  ['pve2', 'kernel', 6, 2, () => `vmbr0: port ${n(2, 6)}(tap10${n(0, 3)}i0) entered forwarding state`],
  ['pve2', 'pmxcfs', 6, 1, () => '[status] notice: received log'],
  ['nas', 'smartd', 6, 2, () => `Device: /dev/sd${pick(['a', 'b', 'c', 'd'])} [SAT], SMART Usage Attribute: 194 Temperature_Celsius changed from ${n(33, 36)} to ${n(34, 38)}`],
  ['nas', 'smartd', 4, 1, () => 'Device: /dev/sdc [SAT], 8 Currently unreadable (pending) sectors'],
  ['nas', 'middlewared', 6, 2, () => `[middlewared.plugins.cloud_sync] Cloud sync task "photos-b2" finished: ${n(3, 120)} files transferred`],
  ['nas', 'nfsd', 4, 1, () => `nfsd: client ${HOSTS.pve2.ip} did not reply to RPC, retrying`],
  ['sw', 'switch', 5, 1, () => `Port ${n(1, 24)} link up, 1Gbps full duplex`],
  ['ha', 'homeassistant', 6, 4, () => pick(['[homeassistant.components.automation.hallway_motion] Hallway motion: Running automation actions', '[homeassistant.components.mqtt] Reconnected to MQTT broker', '[homeassistant.core] Bus:Handling <Event state_changed[L]: entity_id=sensor.office_temperature>'])],
  ['ha', 'homeassistant', 4, 1, () => '[homeassistant.components.zha] Device 0x3f2a (Garage door sensor) did not respond to ping'],
  ['ups', 'upsmon', 6, 1, () => `UPS ups@localhost: battery charge ${n(97, 100)}%, load ${n(18, 31)}%`],
];

// Rare events, so Errors and Criticals show up in the history and drive the alert rules
const BACKUP_FAILURE: Template = ['pve2', 'vzdump', 3, 1, () => 'ERROR: Backup of VM 102 failed - unable to open file \'/mnt/pve/truenas-nfs/dump/vzdump-qemu-102.vma.zst.tmp\': Stale file handle'];
const INCIDENTS: Template[] = [
  ['docker', 'postgres', 3, 1, () => 'FATAL:  password authentication failed for user "paperless"'],
  ['nas', 'smartd', 2, 1, () => 'Device: /dev/sdc [SAT], FAILED SMART self-check. BACK UP DATA NOW!'],
  ['ups', 'upsmon', 1, 1, () => 'UPS ups@localhost on battery'],
  ['docker', 'jellyfin', 3, 1, () => '[ERR] FFmpeg exited with code 187: hardware decoder not available (/dev/dri/renderD128)'],
  ['router', 'kernel', 4, 1, () => 'igb0: link down, carrier lost'],
];

const totalWeight = TEMPLATES.reduce((s, t) => s + t[3], 0);

function chooseTemplate(): Template {
  if (Math.random() < 0.0015) return pick(INCIDENTS);
  let r = Math.random() * totalWeight;
  for (const t of TEMPLATES) {
    r -= t[3];
    if (r <= 0) return t;
  }
  return TEMPLATES[0];
}

let nextId = 1;

export function makeLog(at: Date, template: Template = chooseTemplate()): LogEntry {
  const [hostKey, app, severity, , message] = template;
  const host = HOSTS[hostKey];
  const msg = message();
  const iso = at.toISOString();
  return {
    id: nextId++,
    timestamp: iso,
    received_at: iso,
    source_ip: host.ip,
    source_alias: host.alias,
    app_name: app,
    facility: app === 'kernel' ? 0 : 1,
    severity,
    message: msg,
    raw: `<${8 + severity}>1 ${iso} ${host.alias} ${app} - - - ${msg}`,
  };
}

/** History in time order, ending now: busier in the last hours, with a backup failure 40 minutes ago. */
export function makeHistory(hours = 48): LogEntry[] {
  const now = Date.now();
  const logs: LogEntry[] = [];
  let t = now - hours * 3600_000;
  while (t < now) {
    const recent = now - t < 3 * 3600_000;
    t += (recent ? 4000 : 25000) * (0.3 + Math.random() * 1.4);
    if (t < now) logs.push(makeLog(new Date(t)));
  }
  const failAt = now - 40 * 60_000;
  logs.push(makeLog(new Date(failAt - 1000), ['pve2', 'vzdump', 6, 1, () => 'INFO: Starting Backup of VM 102 (qemu)']));
  logs.push(makeLog(new Date(failAt), BACKUP_FAILURE));
  logs.push(makeLog(new Date(failAt + 2000), ['nas', 'nfsd', 4, 1, () => `nfsd: client ${HOSTS.pve2.ip} did not reply to RPC, retrying`]));
  logs.sort((a, b) => a.timestamp.localeCompare(b.timestamp));
  // Ids follow time order, as they do in the real database
  logs.forEach((l, i) => { l.id = i + 1; });
  nextId = logs.length + 1;
  return logs;
}

export const DEMO_HOSTS = Object.values(HOSTS);
