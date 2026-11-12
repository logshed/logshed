import React from 'react';
import { AlertTriangle, ArrowUpCircle, Check, ExternalLink, Info } from 'lucide-react';
import { VersionInfo } from '../../types.ts';

export interface AboutCardProps {
  versionInfo: VersionInfo | null;
  /** The "Check for new versions" box as currently ticked (saved or not). */
  checkForUpdates: boolean;
}

/** Settings > Application: the running version, whether an update is out, the license and links. */
export const AboutCard: React.FC<AboutCardProps> = ({ versionInfo, checkForUpdates }) => (
  <section className="bg-dark-900 border border-dark-700 rounded-xl p-3.5 sm:p-5 shadow-md space-y-3">
    <div className="flex items-center justify-between">
      <h3 className="text-xs font-semibold text-slate-300 uppercase tracking-wider flex items-center gap-2">
        <Info className="w-4 h-4 text-accent-500" />
        <span>About LogShed</span>
      </h3>
      {versionInfo && (
        <span className="font-mono text-xs px-2 py-0.5 rounded bg-dark-950 border border-dark-700 text-slate-300">
          v{versionInfo.current_version}
        </span>
      )}
    </div>

    {/* Version & Update Status */}
    {versionInfo && (
      <div className="flex flex-wrap items-center gap-2 text-xs">
        {versionInfo.repo_deprecated && (
          <div className="w-full flex items-start gap-2.5 p-3 rounded-lg bg-amber-950/40 border border-amber-800/60 text-amber-200">
            <AlertTriangle className="w-4 h-4 text-amber-400 shrink-0 mt-0.5" />
            <div className="space-y-1">
              <div className="font-semibold text-amber-300">
                Container Repository Moved
              </div>
              <p className="text-slate-300 text-xs">
                This instance is running from <code className="font-mono bg-dark-950 px-1 py-0.5 rounded text-amber-300">ghcr.io/benhornertech/logshed</code>. Please update your Docker Compose or Unraid template to <code className="font-mono bg-dark-950 px-1 py-0.5 rounded text-amber-300">ghcr.io/logshed/logshed</code> to continue receiving future releases.
              </p>
            </div>
          </div>
        )}
        {versionInfo.update_available && versionInfo.check_enabled !== false && checkForUpdates ? (
          <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-amber-950/40 border border-amber-800/60 text-amber-300">
            <ArrowUpCircle className="w-4 h-4 text-amber-400 shrink-0" />
            <span>
              App update available: <strong className="font-semibold font-mono">v{versionInfo.latest_version}</strong>
            </span>
            <a
              href="https://github.com/logshed/logshed/releases"
              target="_blank"
              rel="noopener noreferrer"
              className="ml-1 inline-flex items-center gap-1 text-amber-400 hover:text-amber-200 underline text-[11px]"
            >
              <span>Release Notes</span>
              <ExternalLink className="w-3 h-3" />
            </a>
          </div>
        ) : versionInfo.check_enabled === false || !checkForUpdates ? (
          <div className="flex items-center gap-1.5 text-slate-400 text-xs">
            <span>Update checks are disabled</span>
          </div>
        ) : (
          <div className="flex items-center gap-1.5 text-slate-400 text-xs">
            <Check className="w-3.5 h-3.5 text-emerald-400" />
            <span>LogShed is up to date</span>
          </div>
        )}
      </div>
    )}

    {/* License & Copyright */}
    <p className="text-xs text-slate-400">
      MIT License - Copyright (c) 2026 LogShed Contributors
    </p>

    {/* Links */}
    <div className="flex flex-wrap items-center gap-4 text-xs pt-1 border-t border-dark-800">
      <a
        href="https://github.com/logshed/logshed"
        target="_blank"
        rel="noopener noreferrer"
        className="inline-flex items-center gap-1 text-accent-400 hover:text-accent-300 hover:underline transition"
      >
        <span>GitHub Repository</span>
        <ExternalLink className="w-3 h-3" />
      </a>
      <a
        href="https://github.com/logshed/logshed/blob/main/CHANGELOG.md"
        target="_blank"
        rel="noopener noreferrer"
        className="inline-flex items-center gap-1 text-accent-400 hover:text-accent-300 hover:underline transition"
      >
        <span>Changelog</span>
        <ExternalLink className="w-3 h-3" />
      </a>
      <a
        href="https://github.com/sponsors/BenHornerTech"
        target="_blank"
        rel="noopener noreferrer"
        className="inline-flex items-center gap-1 text-accent-400 hover:text-accent-300 hover:underline transition"
      >
        <span>Sponsor LogShed</span>
        <ExternalLink className="w-3 h-3" />
      </a>
    </div>
  </section>
);
