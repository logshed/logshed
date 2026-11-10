// Canned AI answers. The demo makes no AI calls; it picks the answer whose keywords match the selected lines.
import type { AiDiagnosisResponse, LogEntry } from '../types.ts';

type Answer = Omit<AiDiagnosisResponse, 'model_used' | 'audit_id'> & { keywords: RegExp };

const tokens = (inp: number, out: number) => ({ tokens_in: inp, tokens_out: out, tokens_thoughts: 0, tokens_used: inp + out });

export const DIAGNOSES: Answer[] = [
  {
    keywords: /vzdump|stale file handle|nfsd/i,
    summary: 'The nightly backup of VM 102 on pve-node2 failed while writing to the truenas-nfs storage. At the same moment truenas logged that pve-node2 stopped answering NFS RPCs.',
    root_cause: 'A "Stale file handle" means the NFS export was remounted or changed on truenas while pve-node2 still held the old handle. The NFS retries on truenas line up with the failure, so the share went away under the backup rather than the VM being at fault.',
    remediation: '1. On pve-node2, check the mount: `pvesm status` and `ls /mnt/pve/truenas-nfs`.\n2. If it is stale, run `umount -f /mnt/pve/truenas-nfs`; Proxmox remounts it on the next access.\n3. Make sure the truenas dataset is not replicated or snapshotted with a rollback during the backup window.\n4. Rerun the backup: `vzdump 102 --storage truenas-nfs --mode snapshot`.',
    ...tokens(1840, 412),
  },
  {
    keywords: /password authentication failed|postgres/i,
    summary: 'Postgres on docker-vm is refusing logins for the "paperless" user.',
    root_cause: 'The password stored in the paperless container no longer matches the database role, usually after the .env file was edited or the database volume was restored from an older backup.',
    remediation: '1. Compare `PAPERLESS_DBPASS` in the compose file with the role\'s password.\n2. Reset it in Postgres: `ALTER ROLE paperless WITH PASSWORD \'...\';`.\n3. Restart the paperless container and watch for "consumption finished" lines.',
    ...tokens(1320, 288),
  },
  {
    keywords: /SMART|unreadable|smartd/i,
    summary: 'The disk /dev/sdc in truenas reports pending sectors and has now failed its SMART self-check.',
    root_cause: 'Pending (unreadable) sectors that keep growing mean the drive surface is degrading. A failed overall self-check means the drive expects to fail soon.',
    remediation: '1. Check that pool "tank" still has redundancy: `zpool status tank`.\n2. Replace /dev/sdc as soon as you can, and let the resilver finish before you do other heavy work.\n3. Run `smartctl -a /dev/sdc` and keep the output for the RMA.',
    ...tokens(1105, 251),
  },
  {
    keywords: /on battery|upsmon/i,
    summary: 'The UPS switched to battery: mains power was lost or dropped out of range.',
    root_cause: 'Usually a short utility outage. If it repeats with no outage, the UPS input sensitivity may be too high for your supply.',
    remediation: '1. Check whether the UPS is back on line power.\n2. Make sure the NUT shutdown settings give truenas and the Proxmox nodes time to shut down cleanly.\n3. If it repeats, lower the input sensitivity in the UPS settings.',
    ...tokens(860, 190),
  },
];

const GENERIC: Answer = {
  keywords: /./,
  summary: 'These lines show normal activity: requests served, scheduled jobs finishing and routine status messages.',
  root_cause: 'Nothing here points to a fault. Warnings in this group are isolated and do not repeat in a pattern.',
  remediation: 'No action needed. If you are chasing a problem, select the Error lines around the time it happened and ask again, or add context about what you noticed.',
  ...tokens(940, 120),
};

export function pickDiagnosis(chosen: LogEntry[]): Omit<AiDiagnosisResponse, 'model_used'> {
  const text = chosen.map((l) => `${l.app_name} ${l.message}`).join('\n');
  const { keywords, ...answer } = DIAGNOSES.find((d) => d.keywords.test(text)) || GENERIC;
  void keywords;
  return answer;
}

export const FOLLOW_UPS = [
  'Good question. Based on these lines alone I cannot be certain, but the timing is the strongest clue: the failure and the related warnings arrive within a few seconds of each other. Look at what else changed on that host at the same moment, such as a scheduled task, a snapshot or a network blip, and select those lines too so I can compare them.',
  'Yes, that would explain it. If the same thing happens again at the same time tomorrow, it is almost certainly a scheduled job. An alert rule on this message with a 10 minute window would tell you the next time it happens, without you watching the console.',
  'In this demo the answers are canned, so I cannot look further. In your own LogShed the follow-up goes to the AI provider you configure, with the same redaction of passwords and tokens as the first question.',
];
