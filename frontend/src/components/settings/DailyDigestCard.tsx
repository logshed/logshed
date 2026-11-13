import React, { useEffect, useState } from 'react';
import { Calendar, Send } from 'lucide-react';
import { NotificationChannel, SystemSettings } from '../../types.ts';
import { sendDailyDigest } from '../../api/notifications.ts';
import { formatLocalTimestamp } from '../../utils/formatters.ts';
import { SelectDropdown } from '../common/SelectDropdown.tsx';

export interface DailyDigestCardProps {
  settings: SystemSettings | null;
  channels: NotificationChannel[];
  digestEnabled: boolean;
  onDigestEnabledChange: (enabled: boolean) => void;
  digestChannelId: number | null;
  onDigestChannelIdChange: (channelId: number | null) => void;
  digestScheduleTime: string;
  onDigestScheduleTimeChange: (time: string) => void;
  isSavingSettings: boolean;
  /** The result of Send Digest Now, shown in the notifications card's message (null clears it). */
  onFeedback: (feedback: { text: string; isError: boolean } | null) => void;
}

/**
 * The daily digest part of the notifications card: on or off, its target and time (saved with the
 * settings form), the last run, and Send Digest Now.
 */
export const DailyDigestCard: React.FC<DailyDigestCardProps> = ({
  settings,
  channels,
  digestEnabled,
  onDigestEnabledChange,
  digestChannelId,
  onDigestChannelIdChange,
  digestScheduleTime,
  onDigestScheduleTimeChange,
  isSavingSettings,
  onFeedback,
}) => {
  const serverTimezone = settings?.server_timezone || undefined;
  const [scheduleTimeError, setScheduleTimeError] = useState<string | null>(null);
  const [digestLastRun, setDigestLastRun] = useState<string | null>(null);
  const [isSendingDigest, setIsSendingDigest] = useState<boolean>(false);

  useEffect(() => {
    if (settings) {
      setDigestLastRun(settings.daily_digest_last_run ?? null);
    }
  }, [settings]);

  const hasActiveChannel = channels.some((c) => c.is_enabled);

  const handleSendDigestNow = async () => {
    try {
      setIsSendingDigest(true);
      onFeedback(null);
      const res = await sendDailyDigest();
      if (res.status === 'ok') {
        const channelLabel = res.channel_id
          ? (channels.find((c) => c.id === res.channel_id)?.name || `Channel #${res.channel_id}`)
          : hasActiveChannel
          ? 'All Enabled Channels'
          : null;
        if (channelLabel && !res.notification_sent) {
          onFeedback({
            text: `Digest saved to History, but delivery to ${channelLabel} failed (${res.total_logs.toLocaleString()} logs). Check the target and try again.`,
            isError: true,
          });
        } else {
          const successMessage = channelLabel
            ? `Daily digest dispatched successfully to ${channelLabel} and saved to History (${res.total_logs.toLocaleString()} logs).`
            : `Daily digest generated and saved to History (${res.total_logs.toLocaleString()} logs).`;
          onFeedback({
            text: successMessage,
            isError: false,
          });
        }
        if (res.triggered_at) {
          setDigestLastRun(res.triggered_at);
        }
      } else {
        onFeedback({
          text: 'Failed to dispatch daily digest.',
          isError: true,
        });
      }
    } catch (err: any) {
      onFeedback({
        text: err.message || 'Failed to dispatch daily digest.',
        isError: true,
      });
    } finally {
      setIsSendingDigest(false);
    }
  };

  return (
    <div className="pt-4 border-t border-line-subtle space-y-3">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
        <div>
          <h4 className="text-xs font-semibold text-fg-soft uppercase tracking-wider flex items-center gap-2">
            <Calendar className="w-3.5 h-3.5 text-accent-500" />
            <span>Daily Digest Rollup</span>
          </h4>
          <p className="text-xs text-fg-muted mt-0.5">
            Automated 24-hour analytical summary covering log volume, top error sources, top logging services, and database storage delta.
          </p>
        </div>

        <button
          type="button"
          onClick={handleSendDigestNow}
          disabled={isSendingDigest || isSavingSettings}
          className="flex items-center gap-1.5 px-2.5 py-1 rounded-sm bg-raised hover:bg-raised-hover border border-line text-xs text-fg-soft hover:text-fg-max transition cursor-pointer disabled:opacity-50 whitespace-nowrap self-start sm:self-auto"
          title="Dispatch a daily digest immediately using current 24-hour analytical rollup"
        >
          <Send className={`w-3 h-3 text-accent-400 ${isSendingDigest ? 'animate-pulse' : ''}`} />
          <span>{isSendingDigest ? 'Sending...' : 'Send Digest Now'}</span>
        </button>
      </div>

      <div className="p-3.5 bg-canvas/60 rounded-lg border border-line-subtle space-y-3">
        {/* Enable checkbox */}
        <div>
          <label className="flex items-start gap-2 text-xs select-none cursor-pointer text-fg">
            <input
              type="checkbox"
              checked={digestEnabled}
              disabled={isSavingSettings}
              onChange={(e) => onDigestEnabledChange(e.target.checked)}
              className="rounded-sm bg-canvas border-line text-accent-600 focus:ring-0 focus:ring-offset-0 w-4 h-4 cursor-pointer mt-0.5 disabled:opacity-50"
            />
            <div>
              <span className="font-medium">Enable 24-hour daily digest rollup</span>
              {!hasActiveChannel && (
                <p className="text-[11px] text-fg-muted mt-0.5">
                  No active notification targets configured. Digests will still be recorded and viewable in History.
                </p>
              )}
            </div>
          </label>
        </div>

        {/* Configuration options */}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 pt-1">
          <div className="space-y-1 min-w-0">
            <label id="digest-target-channel-label" htmlFor="digest-target-channel" className="block text-[11px] font-semibold text-fg-muted uppercase">
              Target Channel
            </label>
            <SelectDropdown
              variant="panel"
              fieldClassName="bg-surface border-line rounded-sm px-3 py-2 text-fg"
              id="digest-target-channel"
              labelledBy="digest-target-channel-label"
              label="Target Channel"
              options={[
                { value: '', label: hasActiveChannel ? 'All Enabled Channels' : 'None (History Only)' },
                ...channels.map((ch) => ({
                  value: String(ch.id),
                  label: `Channel: ${ch.name}${ch.is_enabled ? '' : ' (Disabled)'}`,
                })),
              ]}
              value={String(digestChannelId ?? '')}
              disabled={!digestEnabled || !hasActiveChannel || isSavingSettings}
              onChange={(v) => onDigestChannelIdChange(v ? Number(v) : null)}
            />
          </div>

          <div className="space-y-1 min-w-0">
            <label htmlFor="digest-schedule-time" className="block text-[11px] font-semibold text-fg-muted uppercase">
              Schedule Time{' '}
              {/* The digest runs on the server clock (TZ), not the browser's */}
              <span className="normal-case font-mono">({serverTimezone || 'server time'})</span>
            </label>
            <input
              type="text"
              id="digest-schedule-time"
              value={digestScheduleTime}
              placeholder="HH:MM"
              maxLength={5}
              disabled={!digestEnabled || isSavingSettings}
              onChange={(e) => {
                onDigestScheduleTimeChange(e.target.value);
                setScheduleTimeError(null);
              }}
              onBlur={() => {
                const val = digestScheduleTime;
                if (!val || val.trim() === '') {
                  setScheduleTimeError(null);
                  return;
                }
                if (!/^([01]\d|2[0-3]):([0-5]\d)$/.test(val)) {
                  setScheduleTimeError('Enter a valid 24-hour time, e.g. 09:00 or 23:30.');
                  return;
                }
                setScheduleTimeError(null);
              }}
              className={`w-full bg-surface border rounded px-3 py-2 text-xs text-fg placeholder-fg-faint focus:outline-hidden disabled:opacity-50 font-mono ${
                scheduleTimeError
                  ? 'border-danger-500 focus:border-danger-400'
                  : 'border-line focus:border-accent-500'
              }`}
            />
            {scheduleTimeError ? (
              <p className="text-[11px] text-danger-400">{scheduleTimeError}</p>
            ) : (
              <p className="text-[11px] text-fg-muted">24-hour format (e.g. 09:00).</p>
            )}
          </div>
        </div>

        {digestLastRun && (
          <div className="text-[11px] text-fg-muted pt-1 font-mono flex items-center gap-1.5">
            <span>Last scheduled run:</span>
            <span className="text-fg-muted">{formatLocalTimestamp(digestLastRun, true, serverTimezone)}</span>
          </div>
        )}
      </div>
    </div>
  );
};
