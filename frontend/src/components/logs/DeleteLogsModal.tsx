import React, { useState, useEffect, useMemo, useCallback } from 'react';
import { Trash2, AlertTriangle, RefreshCw, AlertCircle, Calendar, Server, Box, Search } from 'lucide-react';
import { Modal } from '../common/Modal.tsx';
import { previewDeleteLogs, deleteLogs, fetchLogFacets } from '../../api/logs.ts';
import { toLocalDatetimeInputString, fromLocalDatetimeInputString } from '../../utils/formatters.ts';
import { MultiSelectDropdown } from '../common/MultiSelectDropdown.tsx';

interface DeleteLogsModalProps {
  isOpen: boolean;
  onClose: () => void;
  onDeleted?: (deletedCount: number) => void;
  availableSources?: string[];
  availableApps?: string[];
  initialSource?: string;
  initialApp?: string;
  initialFrom?: string;
  initialTo?: string;
  initialQuery?: string;
}

export const DeleteLogsModal: React.FC<DeleteLogsModalProps> = ({
  isOpen,
  onClose,
  onDeleted,
  availableSources = [],
  availableApps = [],
  initialSource,
  initialApp,
  initialFrom,
  initialTo,
  initialQuery,
}) => {
  const [sourcesList, setSourcesList] = useState<string[]>(availableSources);
  const [appsList, setAppsList] = useState<string[]>(availableApps);

  useEffect(() => {
    if (availableSources.length > 0) setSourcesList(availableSources);
  }, [availableSources]);

  useEffect(() => {
    if (availableApps.length > 0) setAppsList(availableApps);
  }, [availableApps]);

  useEffect(() => {
    if (isOpen && (sourcesList.length === 0 || appsList.length === 0)) {
      fetchLogFacets().then((res) => {
        if (sourcesList.length === 0 && res.sources) setSourcesList(res.sources);
        if (appsList.length === 0 && res.apps) setAppsList(res.apps);
      }).catch(() => {});
    }
  }, [isOpen, sourcesList.length, appsList.length]);

  const [selectedSources, setSelectedSources] = useState<string[]>(initialSource ? [initialSource] : []);
  const [selectedApps, setSelectedApps] = useState<string[]>(initialApp ? [initialApp] : []);
  const [query, setQuery] = useState<string>(initialQuery || '');
  const [timePreset, setTimePreset] = useState<string>(() => {
    if (initialFrom || initialTo) return 'custom';
    return 'all';
  });
  const [customFrom, setCustomFrom] = useState<string>(() => toLocalDatetimeInputString(initialFrom));
  const [customTo, setCustomTo] = useState<string>(() => toLocalDatetimeInputString(initialTo));

  const [matchedCount, setMatchedCount] = useState<number | null>(null);
  const [isPreviewLoading, setIsPreviewLoading] = useState<boolean>(false);
  const [isDeleting, setIsDeleting] = useState<boolean>(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [confirmText, setConfirmText] = useState<string>('');

  // Reset or populate state when modal opens
  useEffect(() => {
    if (isOpen) {
      setSelectedSources(initialSource ? [initialSource] : []);
      setSelectedApps(initialApp ? [initialApp] : []);
      setQuery(initialQuery || '');
      if (initialFrom || initialTo) {
        setTimePreset('custom');
        setCustomFrom(toLocalDatetimeInputString(initialFrom));
        setCustomTo(toLocalDatetimeInputString(initialTo));
      } else {
        setTimePreset('all');
        setCustomFrom('');
        setCustomTo('');
      }
      setConfirmText('');
      setErrorMsg(null);
    }
  }, [isOpen, initialSource, initialApp, initialFrom, initialTo, initialQuery]);

  // Derive ISO timestamps based on selected time preset
  const { derivedFrom, derivedTo } = useMemo(() => {
    if (timePreset === 'custom') {
      return {
        derivedFrom: fromLocalDatetimeInputString(customFrom) || undefined,
        derivedTo: fromLocalDatetimeInputString(customTo) || undefined,
      };
    }
    if (timePreset === 'all') {
      return { derivedFrom: undefined, derivedTo: undefined };
    }

    const now = new Date();
    let days = 0;
    if (timePreset === '7d') days = 7;
    else if (timePreset === '14d') days = 14;
    else if (timePreset === '30d') days = 30;

    const fromDate = new Date(now.getTime() - days * 24 * 60 * 60 * 1000);
    return {
      derivedFrom: fromDate.toISOString(),
      derivedTo: undefined,
    };
  }, [timePreset, customFrom, customTo]);

  // Check if any filter is active
  const isAllLogsTargeted =
    selectedSources.length === 0 &&
    selectedApps.length === 0 &&
    !derivedFrom &&
    !derivedTo &&
    !query.trim();

  // Determine if typed confirmation is required
  const isHighRisk = isAllLogsTargeted || (matchedCount !== null && matchedCount >= 500);

  // Fetch preview count with debounce
  const fetchPreview = useCallback(async () => {
    try {
      setIsPreviewLoading(true);
      setErrorMsg(null);
      const res = await previewDeleteLogs({
        sources: selectedSources.length > 0 ? selectedSources : undefined,
        apps: selectedApps.length > 0 ? selectedApps : undefined,
        from: derivedFrom,
        to: derivedTo,
        query: query.trim() || undefined,
        delete_all: isAllLogsTargeted ? true : undefined,
      });
      setMatchedCount(res.matched_count);
    } catch (err: any) {
      setErrorMsg(err.message || 'Failed to calculate matching log count.');
      setMatchedCount(null);
    } finally {
      setIsPreviewLoading(false);
    }
  }, [selectedSources, selectedApps, derivedFrom, derivedTo, query, isAllLogsTargeted]);

  useEffect(() => {
    if (!isOpen) return;
    const timer = setTimeout(() => {
      fetchPreview();
    }, 250);
    return () => clearTimeout(timer);
  }, [isOpen, fetchPreview]);

  const handleDelete = async () => {
    if (isHighRisk && confirmText.trim().toUpperCase() !== 'DELETE') {
      setErrorMsg('Please type DELETE to confirm this operation.');
      return;
    }

    try {
      setIsDeleting(true);
      setErrorMsg(null);
      const res = await deleteLogs({
        sources: selectedSources.length > 0 ? selectedSources : undefined,
        apps: selectedApps.length > 0 ? selectedApps : undefined,
        from: derivedFrom,
        to: derivedTo,
        query: query.trim() || undefined,
        delete_all: isAllLogsTargeted ? true : undefined,
      });

      if (onDeleted) {
        onDeleted(res.deleted_count);
      }
      onClose();
    } catch (err: any) {
      setErrorMsg(err.message || 'Failed to delete logs.');
    } finally {
      setIsDeleting(false);
    }
  };

  const isDeleteDisabled =
    isDeleting ||
    isPreviewLoading ||
    matchedCount === 0 ||
    (isHighRisk && confirmText.trim().toUpperCase() !== 'DELETE');

  return (
    <Modal isOpen={isOpen} onClose={onClose} title="Delete Logs" maxWidth="max-w-xl">
      <div className="space-y-4 text-xs font-sans text-slate-200">
        {/* Intro */}
        <p className="text-slate-400 leading-relaxed">
          Specify the criteria for the logs you wish to permanently delete. Deleted logs will be purged from the database and search index.
        </p>

        {errorMsg && (
          <div className="p-3 bg-red-950/60 border border-red-800 rounded-lg flex items-start gap-2 text-xs text-red-300">
            <AlertCircle className="w-4 h-4 text-red-400 shrink-0 mt-0.5" />
            <span>{errorMsg}</span>
          </div>
        )}

        {/* Filters Grid */}
        <div className="space-y-3 bg-dark-950 p-3.5 rounded-lg border border-dark-700 min-w-0 max-w-full">
          {/* Host and App Dropdowns matching AlertRuleModal styling */}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 w-full min-w-0">
            <div className="space-y-1 min-w-0">
              <label className="text-slate-300 font-medium flex items-center gap-1.5 text-xs">
                <Server className="w-3.5 h-3.5 text-accent-500" />
                <span>Host / IP (Optional)</span>
              </label>
              <MultiSelectDropdown
                label="Host"
                placeholder="None"
                options={sourcesList}
                selected={selectedSources}
                onChange={setSelectedSources}
                allowCustomInput={true}
                variant="form"
              />
            </div>

            <div className="space-y-1 min-w-0">
              <label className="text-slate-300 font-medium flex items-center gap-1.5 text-xs">
                <Box className="w-3.5 h-3.5 text-accent-500" />
                <span>Application / Container (Optional)</span>
              </label>
              <MultiSelectDropdown
                label="Application"
                placeholder="None"
                options={appsList}
                selected={selectedApps}
                onChange={setSelectedApps}
                allowCustomInput={true}
                variant="form"
              />
            </div>
          </div>

          {/* Time Range Selector */}
          <div className="space-y-1.5 min-w-0">
            <label className="block text-[11px] font-medium text-slate-400 flex items-center gap-1.5">
              <Calendar className="w-3.5 h-3.5 text-accent-500" />
              <span>Time Period</span>
            </label>
            <div className="flex flex-wrap gap-1.5 mb-2 w-full min-w-0">
              {[
                { id: 'all', label: 'All Time' },
                { id: '7d', label: 'Past 7 Days' },
                { id: '14d', label: 'Past 14 Days' },
                { id: '30d', label: 'Past 30 Days' },
                { id: 'custom', label: 'Custom Range' },
              ].map((preset) => (
                <button
                  key={preset.id}
                  type="button"
                  onClick={() => setTimePreset(preset.id)}
                  className={`px-2.5 py-0.5 rounded text-[11px] font-mono transition cursor-pointer ${
                    timePreset === preset.id
                      ? 'bg-accent-600 text-white font-semibold shadow-xs'
                      : 'bg-dark-950 text-slate-400 hover:text-slate-200 hover:bg-dark-800 border border-dark-700'
                  }`}
                >
                  {preset.label}
                </button>
              ))}
            </div>

            {timePreset === 'custom' && (
              <div className="flex flex-col sm:flex-row gap-3 pt-1 font-mono w-full min-w-0 max-w-full">
                <div className="flex-1 min-w-0 max-w-full space-y-1 overflow-hidden">
                  <span className="text-[10px] text-slate-400 block mb-0.5">From:</span>
                  <input
                    type="datetime-local"
                    value={customFrom}
                    onChange={(e) => setCustomFrom(e.target.value)}
                    className="w-full min-w-0 max-w-full block bg-dark-900 border border-dark-700 rounded-lg px-2.5 py-1.5 text-xs text-slate-200 focus:outline-hidden focus:border-accent-500 font-mono box-border [color-scheme:dark]"
                  />
                </div>
                <div className="flex-1 min-w-0 max-w-full space-y-1 overflow-hidden">
                  <span className="text-[10px] text-slate-400 block mb-0.5">To:</span>
                  <input
                    type="datetime-local"
                    value={customTo}
                    onChange={(e) => setCustomTo(e.target.value)}
                    className="w-full min-w-0 max-w-full block bg-dark-900 border border-dark-700 rounded-lg px-2.5 py-1.5 text-xs text-slate-200 focus:outline-hidden focus:border-accent-500 font-mono box-border [color-scheme:dark]"
                  />
                </div>
              </div>
            )}
          </div>

          {/* Search Query Filter */}
          <div className="space-y-1 min-w-0">
            <label htmlFor="delete-logs-query" className="block text-[11px] font-medium text-slate-400 flex items-center gap-1.5">
              <Search className="w-3.5 h-3.5 text-accent-500" />
              <span>Matching Search Query (Optional)</span>
            </label>
            <input
              id="delete-logs-query"
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="e.g. error, connection refused, kernel"
              className="w-full min-w-0 max-w-full bg-dark-900 border border-dark-700 rounded-lg px-2.5 py-1.5 text-xs text-slate-200 focus:outline-hidden focus:border-accent-500 font-mono box-border"
            />
          </div>
        </div>

        {/* Live Match Counter Banner */}
        <div className="p-3 bg-dark-950 border border-dark-700 rounded-lg flex items-center justify-between">
          <div className="flex items-center gap-2">
            {isPreviewLoading ? (
              <>
                <RefreshCw className="w-4 h-4 animate-spin text-accent-400" />
                <span className="text-slate-400">Calculating matching logs...</span>
              </>
            ) : matchedCount !== null ? (
              <>
                <span className="font-mono text-slate-400">Matching Logs:</span>
                <span
                  data-testid="matched-count-display"
                  className={`font-mono font-bold text-sm ${matchedCount > 0 ? 'text-amber-400' : 'text-slate-400'}`}
                >
                  {matchedCount.toLocaleString()}
                </span>
              </>
            ) : (
              <span className="text-slate-500">Ready to calculate preview</span>
            )}
          </div>

          <div className="text-[11px] text-slate-500 font-mono">
            {isAllLogsTargeted ? (
              <span className="text-red-400 font-semibold">Targets all logs</span>
            ) : (
              <span>Filtered criteria</span>
            )}
          </div>
        </div>

        {/* High Risk Confirmation Input */}
        {isHighRisk && matchedCount !== null && matchedCount > 0 && (
          <div className="p-3 bg-red-950/40 border border-red-800/80 rounded-lg space-y-2 text-red-200">
            <div className="flex items-center gap-2 font-semibold text-red-300">
              <AlertTriangle className="w-4 h-4 text-red-400 shrink-0" />
              <span>High Risk Operation Confirmation</span>
            </div>
            <p className="text-[11px] text-red-300/90 leading-relaxed">
              This operation will permanently delete{' '}
              <span className="font-bold font-mono text-white">{matchedCount.toLocaleString()}</span>{' '}
              logs. To proceed, please type{' '}
              <span className="font-mono font-bold bg-dark-950 px-1 py-0.5 rounded text-white border border-red-900">
                DELETE
              </span>{' '}
              below:
            </p>
            <input
              type="text"
              value={confirmText}
              onChange={(e) => setConfirmText(e.target.value)}
              placeholder="Type DELETE to confirm"
              className="w-full bg-dark-900 border border-red-700/80 rounded px-2.5 py-1.5 text-xs text-red-100 placeholder-red-400/50 focus:outline-hidden focus:border-red-500 font-mono"
            />
          </div>
        )}

        {/* Action Buttons */}
        <div className="flex items-center justify-end gap-2 pt-2 border-t border-dark-800">
          <button
            type="button"
            onClick={onClose}
            disabled={isDeleting}
            className="px-3 py-1.5 text-xs text-slate-400 hover:text-slate-200 hover:bg-dark-800 rounded transition cursor-pointer"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={handleDelete}
            disabled={isDeleteDisabled}
            className="px-4 py-1.5 text-xs bg-red-600 hover:bg-red-500 text-white font-medium rounded shadow-sm transition flex items-center gap-1.5 cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {isDeleting ? (
              <>
                <RefreshCw className="w-3.5 h-3.5 animate-spin" />
                <span>Deleting Logs...</span>
              </>
            ) : (
              <>
                <Trash2 className="w-3.5 h-3.5" />
                <span>Delete Matching Logs</span>
              </>
            )}
          </button>
        </div>
      </div>
    </Modal>
  );
};
