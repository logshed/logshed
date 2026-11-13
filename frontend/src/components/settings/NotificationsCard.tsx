import React, { useEffect, useState, useCallback } from 'react';
import {
  Bell,
  Plus,
  Trash2,
  Edit2,
  RefreshCw,
  Send,
  AlertTriangle,
  CheckCircle2,
  XCircle,
  ExternalLink,
} from 'lucide-react';
import { NotificationChannel, SystemSettings } from '../../types.ts';
import {
  fetchNotificationChannels,
  createNotificationChannel,
  updateNotificationChannel,
  deleteNotificationChannel,
  testNotificationTarget,
} from '../../api/notifications.ts';
import { Modal } from '../common/Modal.tsx';
import { ConfirmDialog } from '../common/ConfirmDialog.tsx';
import { DailyDigestCard } from './DailyDigestCard.tsx';
import { Badge } from '../common/Badge.tsx';
import { Button, IconButton } from '../common/Button.tsx';
import { Card, CardHeader } from '../common/Card.tsx';
import { Checkbox, LABEL_TEXT_CLASS, TextField } from '../common/Field.tsx';
import { TableHead } from '../common/Table.tsx';

/**
 * Notification targets, and the daily digest fields of the settings form: the digest values are
 * held by the settings page and saved with its other changes.
 */
export interface NotificationsCardProps {
  settings: SystemSettings | null;
  onSettingsSaved?: () => Promise<void> | void;
  digestEnabled: boolean;
  onDigestEnabledChange: (enabled: boolean) => void;
  digestChannelId: number | null;
  onDigestChannelIdChange: (channelId: number | null) => void;
  digestScheduleTime: string;
  onDigestScheduleTimeChange: (time: string) => void;
  isSavingSettings?: boolean;
}

export const NotificationsCard: React.FC<NotificationsCardProps> = ({
  settings,
  onSettingsSaved,
  digestEnabled,
  onDigestEnabledChange,
  digestChannelId,
  onDigestChannelIdChange,
  digestScheduleTime,
  onDigestScheduleTimeChange,
  isSavingSettings = false,
}) => {
  const [channels, setChannels] = useState<NotificationChannel[]>([]);
  const [isModalOpen, setIsModalOpen] = useState<boolean>(false);
  const [channelToEdit, setChannelToEdit] = useState<NotificationChannel | null>(null);
  const [channelToDelete, setChannelToDelete] = useState<NotificationChannel | null>(null);
  const [feedbackMsg, setFeedbackMsg] = useState<{ text: string; isError: boolean } | null>(null);

  // Form states inside Add/Edit modal
  const [formName, setFormName] = useState<string>('');
  const [formUrl, setFormUrl] = useState<string>('');
  const [formEnabled, setFormEnabled] = useState<boolean>(true);
  const [isSubmitting, setIsSubmitting] = useState<boolean>(false);
  const [formError, setFormError] = useState<string | null>(null);

  // In-modal live test states
  const [isTesting, setIsTesting] = useState<boolean>(false);
  const [testResult, setTestResult] = useState<{ success: boolean; message: string } | null>(null);

  // Quick testing state from the table row
  const [testingChannelId, setTestingChannelId] = useState<number | null>(null);

  const [isDeleting, setIsDeleting] = useState<boolean>(false);

  const loadChannels = useCallback(async () => {
    try {
      const data = await fetchNotificationChannels();
      setChannels(data);
    } catch (err: any) {
      setFeedbackMsg({ text: err.message || 'Failed to load notification targets.', isError: true });
    }
  }, []);

  useEffect(() => {
    loadChannels();
  }, [loadChannels]);

  const handleOpenCreateModal = () => {
    setChannelToEdit(null);
    setFormName('');
    setFormUrl('');
    setFormEnabled(true);
    setFormError(null);
    setTestResult(null);
    setIsModalOpen(true);
  };

  const handleOpenEditModal = (channel: NotificationChannel) => {
    setChannelToEdit(channel);
    setFormName(channel.name);
    setFormUrl(channel.url); // Masked URL
    setFormEnabled(channel.is_enabled);
    setFormError(null);
    setTestResult(null);
    setIsModalOpen(true);
  };

  const handleToggleStatus = async (channel: NotificationChannel) => {
    try {
      const updated = await updateNotificationChannel(channel.id, { is_enabled: !channel.is_enabled });
      setChannels((prev) => prev.map((c) => (c.id === channel.id ? updated : c)));
    } catch (err: any) {
      setFeedbackMsg({ text: err.message || 'Failed to update channel status.', isError: true });
    }
  };

  const handleRowTest = async (channel: NotificationChannel) => {
    try {
      setTestingChannelId(channel.id);
      setFeedbackMsg(null);
      const res = await testNotificationTarget({ channel_id: channel.id });
      if (res.success) {
        setFeedbackMsg({ text: `Test notification sent to "${channel.name}" successfully!`, isError: false });
      } else {
        setFeedbackMsg({ text: `Test failed for "${channel.name}": ${res.message}`, isError: true });
      }
    } catch (err: any) {
      setFeedbackMsg({ text: err.message || 'Failed to send test notification.', isError: true });
    } finally {
      setTestingChannelId(null);
    }
  };

  const handleModalTest = async () => {
    if (!formUrl.trim()) {
      setFormError('Please enter a notification URL before testing.');
      return;
    }

    try {
      setIsTesting(true);
      setTestResult(null);
      setFormError(null);

      // If URL was unmodified during edit and still masked, test via existing channel_id
      const isUnmodifiedMasked = channelToEdit && formUrl.trim() === channelToEdit.url;
      const res = isUnmodifiedMasked
        ? await testNotificationTarget({ channel_id: channelToEdit.id })
        : await testNotificationTarget({ url: formUrl.trim() });

      setTestResult(res);
    } catch (err: any) {
      setTestResult({ success: false, message: err.message || 'Test connection failed.' });
    } finally {
      setIsTesting(false);
    }
  };

  const handleModalSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (!formName.trim()) {
      setFormError('Target name is required.');
      return;
    }
    if (!formUrl.trim()) {
      setFormError('Notification URL is required.');
      return;
    }

    try {
      setIsSubmitting(true);
      setFormError(null);

      if (channelToEdit) {
        const isUnchangedUrl = formUrl.trim() === channelToEdit.url;
        const updated = await updateNotificationChannel(channelToEdit.id, {
          name: formName.trim(),
          url: isUnchangedUrl ? undefined : formUrl.trim(),
          is_enabled: formEnabled,
        });
        setChannels((prev) => prev.map((c) => (c.id === channelToEdit.id ? updated : c)));
        setFeedbackMsg({ text: `Notification target "${updated.name}" updated successfully.`, isError: false });
      } else {
        const created = await createNotificationChannel({
          name: formName.trim(),
          url: formUrl.trim(),
          is_enabled: formEnabled,
        });
        setChannels((prev) => [...prev, created]);
        setFeedbackMsg({ text: `Notification target "${created.name}" created successfully.`, isError: false });
      }

      setIsModalOpen(false);
    } catch (err: any) {
      setFormError(err.message || 'Failed to save notification target.');
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleConfirmDelete = async () => {
    if (!channelToDelete || isDeleting) return;
    setIsDeleting(true);
    try {
      await deleteNotificationChannel(channelToDelete.id);
      setChannels((prev) => prev.filter((c) => c.id !== channelToDelete.id));
      setFeedbackMsg({ text: `Notification target "${channelToDelete.name}" deleted.`, isError: false });
      setChannelToDelete(null);
      // The server clears the digest channel when its target is deleted: mirror that here
      if (digestChannelId === channelToDelete.id) {
        if (onSettingsSaved) {
          await onSettingsSaved();
        }
      }
    } catch (err: any) {
      setFeedbackMsg({ text: err.message || 'Failed to delete notification target.', isError: true });
    } finally {
      setIsDeleting(false);
    }
  };

  return (
    <Card>
      <CardHeader
        icon={<Bell />}
        title={
          <>
            Notification Targets
            <Badge code className="ml-2 align-middle normal-case tracking-normal">
              {channels.length} {channels.length === 1 ? 'target' : 'targets'}
            </Badge>
          </>
        }
        description="Dispatch real-time alerts across 80+ platforms (Discord, Gotify, Telegram, Ntfy, Pushover, Home Assistant, Slack, Email) using standard webhook URLs. Sensitive tokens are encrypted at rest."
        actions={
          <Button
            size="sm"
            onClick={handleOpenCreateModal}
            title="Configure a new notification target"
            icon={<Plus className="w-3.5 h-3.5 text-accent-400" />}
          >
            New Target
          </Button>
        }
      />

      {/* Feedback Banner */}
      {feedbackMsg && (
        <div
          className={`p-3 rounded-lg border text-xs flex items-start gap-2 ${
            feedbackMsg.isError
              ? 'bg-danger-950/60 border-danger-800 text-danger-300'
              : 'bg-success-950/60 border-success-800 text-success-300'
          }`}
        >
          {feedbackMsg.isError ? (
            <AlertTriangle className="w-4 h-4 text-danger-400 shrink-0 mt-0.5" />
          ) : (
            <CheckCircle2 className="w-4 h-4 text-success-400 shrink-0 mt-0.5" />
          )}
          <span className="flex-1">{feedbackMsg.text}</span>
          <Button variant="ghost" size="sm" onClick={() => setFeedbackMsg(null)} className="-my-1">
            Dismiss
          </Button>
        </div>
      )}

      {/* Content Table */}
      <div className="overflow-x-auto rounded-lg border border-line-subtle">
          {channels.length === 0 ? (
            <div className="text-center py-6 text-fg-muted text-xs italic bg-canvas/40">
              No notification targets configured. Add a target to receive alerts on Discord, Gotify, Telegram, or other services.
            </div>
          ) : (
            <table className="w-full text-left text-xs border-collapse">
              <TableHead
                columns={[
                  { label: 'Status' },
                  { label: 'Name' },
                  { label: 'Target URL (Masked)' },
                  { label: 'Actions', align: 'right' },
                ]}
              />
              <tbody className="divide-y divide-line-subtle">
                {channels.map((channel) => (
                  <tr
                    key={channel.id}
                    className="hover:bg-raised/40 transition group"
                  >
                    <td className="py-2 px-4">
                      <button
                        type="button"
                        onClick={() => handleToggleStatus(channel)}
                        className={`px-2 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wider transition cursor-pointer ${
                          channel.is_enabled
                            ? 'bg-success-950 text-success-400 border border-success-800'
                            : 'bg-raised text-fg-muted border border-line'
                        }`}
                        title={channel.is_enabled ? 'Click to disable' : 'Click to enable'}
                      >
                        {channel.is_enabled ? 'Active' : 'Disabled'}
                      </button>
                    </td>
                    <td className="py-2 px-4 font-mono text-fg">
                      {channel.name}
                    </td>
                    <td className="py-2 px-4 font-mono text-fg-muted max-w-[250px] truncate" title={channel.url}>
                      {channel.url}
                    </td>
                    <td className="py-2 px-4 text-right">
                      <div className="flex items-center justify-end gap-1.5">
                        <IconButton
                          size="sm"
                          onClick={() => handleRowTest(channel)}
                          disabled={testingChannelId === channel.id}
                          title="Send test notification"
                          label={`Test ${channel.name}`}
                        >
                          <Send
                            className={`w-3.5 h-3.5 ${
                              testingChannelId === channel.id ? 'animate-pulse text-accent-400' : ''
                            }`}
                          />
                        </IconButton>
                        <IconButton size="sm" onClick={() => handleOpenEditModal(channel)} title="Edit target" label={`Edit ${channel.name}`}>
                          <Edit2 className="w-3.5 h-3.5" />
                        </IconButton>
                        <IconButton
                          size="sm"
                          variant="danger"
                          onClick={() => setChannelToDelete(channel)}
                          title="Delete target"
                          label={`Delete ${channel.name}`}
                        >
                          <Trash2 className="w-3.5 h-3.5" />
                        </IconButton>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

      {/* Daily Digest Section */}
      <DailyDigestCard
        settings={settings}
        channels={channels}
        digestEnabled={digestEnabled}
        onDigestEnabledChange={onDigestEnabledChange}
        digestChannelId={digestChannelId}
        onDigestChannelIdChange={onDigestChannelIdChange}
        digestScheduleTime={digestScheduleTime}
        onDigestScheduleTimeChange={onDigestScheduleTimeChange}
        isSavingSettings={isSavingSettings}
        onFeedback={setFeedbackMsg}
      />

      {/* Add / Edit Channel Modal */}
      <Modal
        isOpen={isModalOpen}
        onClose={() => setIsModalOpen(false)}
        title={channelToEdit ? 'Edit Notification Target' : 'Add Notification Target'}
        maxWidth="max-w-xl"
      >
        <form onSubmit={handleModalSubmit} className="space-y-4">
          {formError && (
            <div className="p-3 bg-danger-950/60 border border-danger-800 rounded-lg text-xs text-danger-300 flex items-start gap-2">
              <AlertTriangle className="w-4 h-4 text-danger-400 shrink-0 mt-0.5" />
              <span>{formError}</span>
            </div>
          )}

          <TextField
            label="Target Name"
            type="text"
            required
            value={formName}
            onChange={(e) => setFormName(e.target.value)}
            placeholder="e.g. Homelab Discord, Admin Telegram, Gotify Push"
            mono
          />

          <div>
            <div className="flex items-center justify-between mb-1">
              <label htmlFor="notification-url" className={LABEL_TEXT_CLASS}>
                Notification URL (Apprise Format)
              </label>
              <div className="flex items-center gap-2.5">
                <a
                  href="https://appriseit.com/services/"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1 text-[10px] text-accent-400 hover:underline"
                  title="View supported Apprise notification services and syntax"
                >
                  <span>Services &amp; Syntax</span>
                  <ExternalLink className="w-2.5 h-2.5" />
                </a>
                <a
                  href="https://appriseit.com/url-builder/"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1 text-[10px] text-accent-400 hover:underline"
                  title="Construct notification URLs using the official Apprise URL Builder"
                >
                  <span>URL Builder</span>
                  <ExternalLink className="w-2.5 h-2.5" />
                </a>
              </div>
            </div>
            <TextField
              id="notification-url"
              type="text"
              required
              value={formUrl}
              onChange={(e) => {
                setFormUrl(e.target.value);
                setTestResult(null);
              }}
              placeholder="discord://webhook_id/webhook_token or gotify://hostname/token"
              mono
            />
            <p className="text-[11px] text-fg-muted mt-1">
              Supports Discord (<code>discord://...</code>), Gotify (<code>gotify://...</code>), Telegram (<code>tgram://...</code>), Ntfy (<code>ntfy://...</code>), Pushover (<code>pover://...</code>), Slack (<code>slack://...</code>), and generic webhooks (<code>https://...</code>).
            </p>
          </div>

          <div className="pt-1">
            <Checkbox
              label="Channel active (enabled for alerting)"
              checked={formEnabled}
              onChange={(e) => setFormEnabled(e.target.checked)}
            />
          </div>

          {/* Test connection results */}
          {testResult && (
            <div
              className={`p-3 rounded-lg border text-xs flex items-start gap-2 ${
                testResult.success
                  ? 'bg-success-950/60 border-success-800 text-success-300'
                  : 'bg-danger-950/60 border-danger-800 text-danger-300'
              }`}
            >
              {testResult.success ? (
                <CheckCircle2 className="w-4 h-4 text-success-400 shrink-0 mt-0.5" />
              ) : (
                <XCircle className="w-4 h-4 text-danger-400 shrink-0 mt-0.5" />
              )}
              <span className="font-mono text-[11px] leading-relaxed">{testResult.message}</span>
            </div>
          )}

          {/* Modal Actions */}
          <div className="flex flex-col-reverse sm:flex-row items-center justify-between gap-2 pt-3 border-t border-line-subtle">
            <Button
              onClick={handleModalTest}
              disabled={isTesting || !formUrl.trim()}
              className="w-full sm:w-auto"
              icon={<Send className={`w-3.5 h-3.5 text-accent-400 ${isTesting ? 'animate-spin' : ''}`} />}
            >
              {isTesting ? 'Testing...' : 'Send Test Notification'}
            </Button>

            <div className="flex items-center gap-2 w-full sm:w-auto justify-end">
              <Button onClick={() => setIsModalOpen(false)}>Cancel</Button>
              <Button
                type="submit"
                variant="primary"
                disabled={isSubmitting || !formName.trim() || !formUrl.trim()}
                icon={isSubmitting ? <RefreshCw className="w-3.5 h-3.5 animate-spin" /> : undefined}
              >
                {channelToEdit ? 'Save Changes' : 'Create Target'}
              </Button>
            </div>
          </div>
        </form>
      </Modal>

      {/* Delete Confirmation Modal */}
      <ConfirmDialog
        isOpen={Boolean(channelToDelete)}
        title="Delete Notification Target"
        onCancel={() => setChannelToDelete(null)}
        onConfirm={handleConfirmDelete}
        confirmDisabled={isDeleting}
        confirmIcon={<Trash2 className="w-3.5 h-3.5" />}
        confirmLabel="Delete Target"
      >
        <p className="text-xs text-fg-soft">
          Are you sure you want to delete notification target{' '}
          <strong className="text-fg-strong font-semibold">{channelToDelete?.name}</strong>?
          This will permanently remove the channel from alerting pipelines.
        </p>
      </ConfirmDialog>
    </Card>
  );
};
