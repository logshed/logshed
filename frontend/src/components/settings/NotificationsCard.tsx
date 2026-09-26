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
import { NotificationChannel } from '../../types.ts';
import {
  fetchNotificationChannels,
  createNotificationChannel,
  updateNotificationChannel,
  deleteNotificationChannel,
  testNotificationTarget,
} from '../../api/notifications.ts';
import { Modal } from '../common/Modal.tsx';

export const NotificationsCard: React.FC = () => {
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
    if (!channelToDelete) return;
    try {
      await deleteNotificationChannel(channelToDelete.id);
      setChannels((prev) => prev.filter((c) => c.id !== channelToDelete.id));
      setFeedbackMsg({ text: `Notification target "${channelToDelete.name}" deleted.`, isError: false });
      setChannelToDelete(null);
    } catch (err: any) {
      setFeedbackMsg({ text: err.message || 'Failed to delete notification target.', isError: true });
    }
  };

  return (
    <section className="bg-dark-900 border border-dark-700 rounded-xl p-3.5 sm:p-5 shadow-md space-y-4">
      {/* Header */}
      <div className="flex items-start sm:items-center justify-between gap-2 sm:gap-3">
        <div className="flex flex-wrap items-center gap-1.5 sm:gap-2 min-w-0">
          <div className="flex items-center gap-1.5 sm:gap-2 min-w-0">
            <Bell className="w-4 h-4 text-accent-500 shrink-0" />
            <h3 className="text-xs font-semibold text-slate-300 uppercase tracking-wider truncate">
              Notification Targets
            </h3>
          </div>
          <div className="flex items-center gap-1.5 shrink-0">
            <span className="text-[11px] px-1.5 py-0.5 rounded bg-dark-800 border border-dark-700 text-slate-400 font-mono whitespace-nowrap">
              {channels.length} {channels.length === 1 ? 'target' : 'targets'}
            </span>
          </div>
        </div>

        <div className="flex items-center gap-1.5 sm:gap-2 shrink-0">
          <button
            type="button"
            onClick={handleOpenCreateModal}
            className="flex items-center gap-1 px-2 py-1 sm:px-2.5 sm:gap-1.5 rounded bg-dark-800 hover:bg-dark-750 border border-dark-700 text-xs text-slate-300 hover:text-white transition cursor-pointer whitespace-nowrap"
            title="Configure a new notification target"
          >
            <Plus className="w-3.5 h-3.5 text-accent-400" />
            <span>New Target</span>
          </button>
        </div>
      </div>

      <p className="text-xs text-slate-400 mt-0.5">
        Dispatch real-time alerts across 80+ platforms (Discord, Gotify, Telegram, Ntfy, Pushover, Home Assistant, Slack, Email) using standard webhook URLs. Sensitive tokens are encrypted at rest.
      </p>

      {/* Feedback Banner */}
      {feedbackMsg && (
        <div
          className={`p-3 rounded-lg border text-xs flex items-start gap-2 ${
            feedbackMsg.isError
              ? 'bg-red-950/60 border-red-800 text-red-300'
              : 'bg-emerald-950/60 border-emerald-800 text-emerald-300'
          }`}
        >
          {feedbackMsg.isError ? (
            <AlertTriangle className="w-4 h-4 text-red-400 shrink-0 mt-0.5" />
          ) : (
            <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0 mt-0.5" />
          )}
          <span className="flex-1">{feedbackMsg.text}</span>
          <button
            type="button"
            onClick={() => setFeedbackMsg(null)}
            className="text-slate-400 hover:text-slate-200 text-xs cursor-pointer ml-2"
          >
            Dismiss
          </button>
        </div>
      )}

      {/* Content Table */}
      <div className="overflow-x-auto">
          {channels.length === 0 ? (
            <div className="text-center py-6 text-slate-400 text-xs italic bg-dark-950/40 rounded-lg border border-dark-800">
              No notification targets configured. Add a target to receive alerts on Discord, Gotify, Telegram, or other services.
            </div>
          ) : (
            <table className="w-full text-left text-xs border-collapse">
              <thead>
                <tr className="border-b border-dark-700 text-slate-400 font-medium">
                  <th className="pb-2 pl-2">Status</th>
                  <th className="pb-2">Name</th>
                  <th className="pb-2">Target URL (Masked)</th>
                  <th className="pb-2 text-right pr-2">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-dark-800">
                {channels.map((channel) => (
                  <tr
                    key={channel.id}
                    className="hover:bg-dark-800/40 transition group"
                  >
                    <td className="py-2 pl-2">
                      <button
                        type="button"
                        onClick={() => handleToggleStatus(channel)}
                        className={`px-2 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wider transition cursor-pointer ${
                          channel.is_enabled
                            ? 'bg-emerald-950 text-emerald-400 border border-emerald-800'
                            : 'bg-dark-800 text-slate-400 border border-dark-700'
                        }`}
                        title={channel.is_enabled ? 'Click to disable' : 'Click to enable'}
                      >
                        {channel.is_enabled ? 'Active' : 'Disabled'}
                      </button>
                    </td>
                    <td className="py-2 font-mono text-slate-200">
                      {channel.name}
                    </td>
                    <td className="py-2 font-mono text-slate-400 max-w-[250px] truncate" title={channel.url}>
                      {channel.url}
                    </td>
                    <td className="py-2 text-right pr-2">
                      <div className="flex items-center justify-end gap-1">
                        <button
                          type="button"
                          onClick={() => handleRowTest(channel)}
                          disabled={testingChannelId === channel.id}
                          className="p-1 text-slate-400 hover:text-accent-400 hover:bg-dark-700 rounded transition cursor-pointer disabled:opacity-50"
                          title="Send test notification"
                          aria-label={`Test ${channel.name}`}
                        >
                          <Send
                            className={`w-3.5 h-3.5 ${
                              testingChannelId === channel.id ? 'animate-pulse text-accent-400' : ''
                            }`}
                          />
                        </button>
                        <button
                          type="button"
                          onClick={() => handleOpenEditModal(channel)}
                          className="p-1 text-slate-400 hover:text-slate-200 hover:bg-dark-700 rounded transition cursor-pointer"
                          title="Edit target"
                          aria-label={`Edit ${channel.name}`}
                        >
                          <Edit2 className="w-3.5 h-3.5" />
                        </button>
                        <button
                          type="button"
                          onClick={() => setChannelToDelete(channel)}
                          className="p-1 text-slate-400 hover:text-red-400 hover:bg-dark-700 rounded transition cursor-pointer"
                          title="Delete target"
                          aria-label={`Delete ${channel.name}`}
                        >
                          <Trash2 className="w-3.5 h-3.5" />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

      {/* Add / Edit Channel Modal */}
      <Modal
        isOpen={isModalOpen}
        onClose={() => setIsModalOpen(false)}
        title={channelToEdit ? 'Edit Notification Target' : 'Add Notification Target'}
        maxWidth="max-w-xl"
      >
        <form onSubmit={handleModalSubmit} className="space-y-4">
          {formError && (
            <div className="p-3 bg-red-950/60 border border-red-800 rounded-lg text-xs text-red-300 flex items-start gap-2">
              <AlertTriangle className="w-4 h-4 text-red-400 shrink-0 mt-0.5" />
              <span>{formError}</span>
            </div>
          )}

          <div>
            <label className="block text-[11px] font-semibold text-slate-400 uppercase mb-1">
              Target Name
            </label>
            <input
              type="text"
              required
              value={formName}
              onChange={(e) => setFormName(e.target.value)}
              placeholder="e.g. Homelab Discord, Admin Telegram, Gotify Push"
              className="w-full bg-dark-950 border border-dark-700 rounded px-3 py-2 text-xs text-slate-100 placeholder-slate-500 focus:outline-hidden focus:border-accent-500 font-mono"
            />
          </div>

          <div>
            <div className="flex items-center justify-between mb-1">
              <label className="block text-[11px] font-semibold text-slate-400 uppercase">
                Notification URL (Apprise Format)
              </label>
              <a
                href="https://github.com/caronc/apprise/wiki"
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1 text-[10px] text-accent-400 hover:underline"
              >
                <span>Syntax Docs</span>
                <ExternalLink className="w-2.5 h-2.5" />
              </a>
            </div>
            <input
              type="text"
              required
              value={formUrl}
              onChange={(e) => {
                setFormUrl(e.target.value);
                setTestResult(null);
              }}
              placeholder="discord://webhook_id/webhook_token or gotify://hostname/token"
              className="w-full bg-dark-950 border border-dark-700 rounded px-3 py-2 text-xs text-slate-100 placeholder-slate-500 focus:outline-hidden focus:border-accent-500 font-mono"
            />
            <p className="text-[10px] text-slate-500 mt-1">
              Supports Discord (<code>discord://...</code>), Gotify (<code>gotify://...</code>), Telegram (<code>tgram://...</code>), Ntfy (<code>ntfy://...</code>), Pushover (<code>pover://...</code>), Slack (<code>slack://...</code>), and generic webhooks (<code>https://...</code>).
            </p>
          </div>

          <div className="pt-1">
            <label className="flex items-center gap-2 text-xs text-slate-200 cursor-pointer select-none">
              <input
                type="checkbox"
                checked={formEnabled}
                onChange={(e) => setFormEnabled(e.target.checked)}
                className="rounded bg-dark-950 border-dark-700 text-accent-600 focus:ring-0 focus:ring-offset-0 w-4 h-4 cursor-pointer"
              />
              <span className="font-medium">Channel active (enabled for alerting)</span>
            </label>
          </div>

          {/* Test connection results */}
          {testResult && (
            <div
              className={`p-3 rounded-lg border text-xs flex items-start gap-2 ${
                testResult.success
                  ? 'bg-emerald-950/60 border-emerald-800 text-emerald-300'
                  : 'bg-red-950/60 border-red-800 text-red-300'
              }`}
            >
              {testResult.success ? (
                <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0 mt-0.5" />
              ) : (
                <XCircle className="w-4 h-4 text-red-400 shrink-0 mt-0.5" />
              )}
              <span className="font-mono text-[11px] leading-relaxed">{testResult.message}</span>
            </div>
          )}

          {/* Modal Actions */}
          <div className="flex flex-col-reverse sm:flex-row items-center justify-between gap-2 pt-3 border-t border-dark-800">
            <button
              type="button"
              onClick={handleModalTest}
              disabled={isTesting || !formUrl.trim()}
              className="w-full sm:w-auto px-3 py-2 bg-dark-800 hover:bg-dark-750 border border-dark-700 rounded-lg text-xs text-slate-300 font-medium flex items-center justify-center gap-1.5 transition cursor-pointer disabled:opacity-40"
            >
              <Send className={`w-3.5 h-3.5 text-accent-400 ${isTesting ? 'animate-spin' : ''}`} />
              <span>{isTesting ? 'Testing...' : 'Send Test Notification'}</span>
            </button>

            <div className="flex items-center gap-2 w-full sm:w-auto justify-end">
              <button
                type="button"
                onClick={() => setIsModalOpen(false)}
                className="px-3 py-2 text-xs text-slate-400 hover:text-slate-200 cursor-pointer transition"
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={isSubmitting || !formName.trim() || !formUrl.trim()}
                className="px-4 py-2 text-xs font-medium text-white bg-accent-600 hover:bg-accent-500 rounded-lg transition cursor-pointer flex items-center justify-center gap-1.5 shadow-md disabled:opacity-50"
              >
                {isSubmitting && <RefreshCw className="w-3.5 h-3.5 animate-spin" />}
                <span>{channelToEdit ? 'Save Changes' : 'Create Target'}</span>
              </button>
            </div>
          </div>
        </form>
      </Modal>

      {/* Delete Confirmation Modal */}
      <Modal
        isOpen={Boolean(channelToDelete)}
        onClose={() => setChannelToDelete(null)}
        title="Delete Notification Target"
        maxWidth="max-w-md"
      >
        <div className="space-y-4">
          <p className="text-xs text-slate-300">
            Are you sure you want to delete notification target{' '}
            <strong className="text-slate-100 font-semibold">{channelToDelete?.name}</strong>?
            This will permanently remove the channel from alerting pipelines.
          </p>
          <div className="flex justify-end gap-2 pt-2 border-t border-dark-800">
            <button
              type="button"
              onClick={() => setChannelToDelete(null)}
              className="px-3 py-1.5 text-xs text-slate-400 hover:text-slate-200 cursor-pointer"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={handleConfirmDelete}
              className="px-4 py-1.5 text-xs font-medium text-white bg-red-600 hover:bg-red-500 rounded-lg transition cursor-pointer flex items-center gap-1.5 shadow-md"
            >
              <Trash2 className="w-3.5 h-3.5" />
              <span>Delete Target</span>
            </button>
          </div>
        </div>
      </Modal>
    </section>
  );
};
