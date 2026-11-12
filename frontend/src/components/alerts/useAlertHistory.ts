import { useCallback, useEffect, useState } from 'react';
import { AlertHistoryItem } from '../../types.ts';
import {
  fetchAlertHistory,
  fetchAlertHistoryItem,
  deleteAlertHistoryItem,
  clearAlertHistory,
} from '../../api/alerts.ts';
import { usePolling } from '../../utils/hooks.ts';
import { parseIncidentIdFromUrl, removeIncidentIdFromUrl } from './alertRoutes.ts';
import type { Feedback } from './useMaintenance.ts';

const HISTORY_PAGE_SIZE = 50;

/**
 * The incident history on Rules & History: one page of records and the total, the record open in
 * the detail dialog (from a click or a notification link), and deleting records. The page refreshes
 * every 10 seconds while isShown. Errors go to onFeedback.
 */
export function useAlertHistory({
  isShown,
  onFeedback,
}: {
  isShown: boolean;
  onFeedback: (feedback: Feedback) => void;
}) {
  const [historyItems, setHistoryItems] = useState<AlertHistoryItem[]>([]);
  const [historyTotal, setHistoryTotal] = useState<number>(0);
  const historyLimit = HISTORY_PAGE_SIZE;
  const [historyOffset, setHistoryOffset] = useState<number>(0);
  // History selected item state for modal overlay
  const [selectedHistoryItem, setSelectedHistoryItem] = useState<AlertHistoryItem | null>(null);
  // The record the delete question is about, and whether the clear-all question is open
  const [historyItemToDelete, setHistoryItemToDelete] = useState<AlertHistoryItem | null>(null);
  const [isClearHistoryModalOpen, setIsClearHistoryModalOpen] = useState<boolean>(false);

  /** Shows the first page from a load of the whole panel. */
  const showFirstPage = (data: { items: AlertHistoryItem[]; total: number }) => {
    setHistoryItems(data.items);
    setHistoryTotal(data.total);
    setHistoryOffset(0);
  };

  // A notification link opens its incident (/rules/history?id=N), which may be on any history page
  useEffect(() => {
    const id = parseIncidentIdFromUrl();
    if (id === null) return;
    let cancelled = false;
    fetchAlertHistoryItem(id)
      .then((item) => {
        if (!cancelled) setSelectedHistoryItem(item);
      })
      .catch((err: any) => {
        if (cancelled) return;
        removeIncidentIdFromUrl();
        onFeedback({
          text:
            err?.status === 404
              ? `Incident record ${id} was not found. It may have been deleted or removed by retention.`
              : err?.message || 'Failed to load the incident record.',
          isError: true,
        });
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reads the link once when the panel opens
  }, []);

  const closeHistoryItem = () => {
    setSelectedHistoryItem(null);
    removeIncidentIdFromUrl();
  };

  const refreshHistory = useCallback(async () => {
    try {
      const data = await fetchAlertHistory(historyLimit, historyOffset);
      setHistoryItems(data.items);
      setHistoryTotal(data.total);
    } catch {
      // Keep the current list when a background refresh fails
    }
  }, [historyLimit, historyOffset]);

  // The incident list refreshes every 10 seconds while shown. Maintenance status needs no poll here:
  // the Navbar polls it and announces each result with a 'maintenance-updated' event.
  usePolling(() => {
    if (isShown) refreshHistory();
  }, 10000);

  /** Deletes one record and closes the delete question. */
  const deleteHistory = async (id: number) => {
    try {
      await deleteAlertHistoryItem(id);
      setHistoryItems((prev) => prev.filter((h) => h.id !== id));
      setHistoryTotal((prev) => Math.max(0, prev - 1));
      if (selectedHistoryItem?.id === id) {
        closeHistoryItem();
      }
      setHistoryItemToDelete(null);
      // Refetch the current page, and step back one page when it is now empty
      try {
        let offset = historyOffset;
        let data = await fetchAlertHistory(historyLimit, offset);
        if (data.items.length === 0 && offset > 0) {
          offset = Math.max(0, offset - historyLimit);
          data = await fetchAlertHistory(historyLimit, offset);
        }
        setHistoryItems(data.items);
        setHistoryTotal(data.total);
        setHistoryOffset(offset);
      } catch {
        // Keep the locally updated list when the refetch fails
      }
    } catch (err: any) {
      onFeedback({ text: err.message || 'Failed to delete history record.', isError: true });
    }
  };

  /** Deletes every record and closes the clear-all question. */
  const clearAllHistory = async () => {
    try {
      await clearAlertHistory();
      setHistoryItems([]);
      setHistoryTotal(0);
      closeHistoryItem();
      setIsClearHistoryModalOpen(false);
      onFeedback({ text: 'All alert history records cleared.', isError: false });
    } catch (err: any) {
      onFeedback({ text: err.message || 'Failed to clear alert history.', isError: true });
    }
  };

  const changePage = async (newOffset: number) => {
    try {
      const data = await fetchAlertHistory(historyLimit, newOffset);
      setHistoryItems(data.items);
      setHistoryTotal(data.total);
      setHistoryOffset(newOffset);
    } catch (err: any) {
      onFeedback({ text: err.message || 'Failed to load history page.', isError: true });
    }
  };

  return {
    historyItems,
    historyTotal,
    historyLimit,
    historyOffset,
    selectedHistoryItem,
    setSelectedHistoryItem,
    closeHistoryItem,
    historyItemToDelete,
    setHistoryItemToDelete,
    isClearHistoryModalOpen,
    setIsClearHistoryModalOpen,
    showFirstPage,
    refreshHistory,
    deleteHistory,
    clearAllHistory,
    changePage,
  };
}
