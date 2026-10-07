import React, { useState, useEffect, useCallback, useMemo } from 'react';
import {
  Trash2,
  AlertCircle,
  AlertTriangle,
  Server,
  Box,
  Calendar,
  Search,
  RefreshCw,
  CheckCircle2,
  RotateCcw,
} from 'lucide-react';
import { previewDeleteLogs, deleteLogs, fetchLogFacets } from '../../api/logs.ts';
import { MultiSelectDropdown } from '../common/MultiSelectDropdown.tsx';

interface DeleteLogsCardProps {
  onLogsDeleted?: () => void;
}

export const DeleteLogsCard: React.FC<DeleteLogsCardProps> = ({ onLogsDeleted }) => {
  const [availableSources, setAvailableSources] = useState<string[]>([]);
  const [availableApps, setAvailableApps] = useState<string[]>([]);
  const [selectedSources, setSelectedSources] = useState<string[]>([]);
  const [selectedApps, setSelectedApps] = useState<string[]>([]);
  const [timePreset, setTimePreset] = useState<string>('');
  const [customFrom, setCustomFrom] = useState<string>('');
  const [customTo, setCustomTo] = useState<string>('');
  const [query, setQuery] = useState<string>('');

  const [matchedCount, setMatchedCount] = useState<number | null>(null);
  const [isPreviewLoading, setIsPreviewLoading] = useState<boolean>(false);
  const [isDeleting, setIsDeleting] = useState<boolean>(false);
  const [confirmText, setConfirmText] = useState<string>('');
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);

  // Load available hosts and apps from database facets
  useEffect(() => {
    fetchLogFacets()
      .then((res) => {
        if (res.sources && res.sources.length > 0) {
          setAvailableSources(res.sources);
        }
        if (res.apps && res.apps.length > 0) {
          setAvailableApps(res.apps);
        }
      })
      .catch(() => {
        // Facets fallback silently
      });
  }, []);

  // Compute ISO time range from presets
  const { derivedFrom, derivedTo } = useMemo(() => {
    const now = new Date();
    if (timePreset === 'all' || !timePreset) {
      return { derivedFrom: undefined, derivedTo: undefined };
    }
    if (timePreset === 'custom') {
      return {
        derivedFrom: customFrom ? new Date(customFrom).toISOString() : undefined,
        derivedTo: customTo ? new Date(customTo).toISOString() : undefined,
      };
    }

    const durationMap: Record<string, number> = {
      '24h': 24 * 60 * 60 * 1000,
      '7d': 7 * 24 * 60 * 60 * 1000,
      '14d': 14 * 24 * 60 * 60 * 1000,
      '30d': 30 * 24 * 60 * 60 * 1000,
    };

    const delta = durationMap[timePreset];
    if (delta) {
      const fromDate = new Date(now.getTime() - delta);
      return {
        derivedFrom: fromDate.toISOString(),
        derivedTo: now.toISOString(),
      };
    }

    return { derivedFrom: undefined, derivedTo: undefined };
  }, [timePreset, customFrom, customTo]);

  const hasActiveCriteria =
    selectedSources.length > 0 ||
    selectedApps.length > 0 ||
    timePreset !== '' ||
    Boolean(query.trim());

  const isAllLogsTargeted =
    timePreset === 'all' &&
    selectedSources.length === 0 &&
    selectedApps.length === 0 &&
    !query.trim();

  const isHighRisk = isAllLogsTargeted || (matchedCount !== null && matchedCount >= 500);

  // Debounced preview query
  const fetchPreview = useCallback(async () => {
    if (!hasActiveCriteria) {
      setMatchedCount(null);
      setIsPreviewLoading(false);
      return;
    }

    setIsPreviewLoading(true);
    setErrorMsg(null);
    try {
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
  }, [selectedSources, selectedApps, derivedFrom, derivedTo, query, isAllLogsTargeted, hasActiveCriteria]);

  useEffect(() => {
    if (!hasActiveCriteria) {
      setMatchedCount(null);
      setIsPreviewLoading(false);
      return;
    }
    const timer = setTimeout(() => {
      fetchPreview();
    }, 250);
    return () => clearTimeout(timer);
  }, [fetchPreview, hasActiveCriteria]);

  const handleResetFilters = () => {
    setSelectedSources([]);
    setSelectedApps([]);
    setTimePreset('');
    setCustomFrom('');
    setCustomTo('');
    setQuery('');
    setConfirmText('');
    setMatchedCount(null);
    setErrorMsg(null);
    setSuccessMsg(null);
  };

  const handleDelete = async () => {
    if (!hasActiveCriteria) return;

    if (isHighRisk && confirmText.trim().toUpperCase() !== 'DELETE') {
      setErrorMsg('Please type DELETE to confirm this operation.');
      return;
    }

    try {
      setIsDeleting(true);
      setErrorMsg(null);
      setSuccessMsg(null);

      const res = await deleteLogs({
        sources: selectedSources.length > 0 ? selectedSources : undefined,
        apps: selectedApps.length > 0 ? selectedApps : undefined,
        from: derivedFrom,
        to: derivedTo,
        query: query.trim() || undefined,
        delete_all: isAllLogsTargeted ? true : undefined,
      });

      setSuccessMsg(`Successfully deleted ${res.deleted_count} logs.`);
      setConfirmText('');

      if (onLogsDeleted) {
        onLogsDeleted();
      }

      // Re-trigger preview count
      fetchPreview();
    } catch (err: any) {
      setErrorMsg(err.message || 'Failed to delete logs.');
    } finally {
      setIsDeleting(false);
    }
  };

  const isDeleteDisabled =
    !hasActiveCriteria ||
    isDeleting ||
    isPreviewLoading ||
    matchedCount === null ||
    matchedCount === 0 ||
    (isHighRisk && confirmText.trim().toUpperCase() !== 'DELETE');

  return (
    <div className="bg-dark-900 border border-dark-700 rounded-xl p-4 sm:p-5 shadow-md overflow-hidden min-w-0 max-w-full space-y-4">
      {/* Header with blue accent icon and no manual purge label */}
      <div className="flex items-center justify-between">
        <h3 className="text-xs font-semibold text-slate-300 uppercase tracking-wider flex items-center gap-2">
          <Trash2 className="w-4 h-4 text-accent-500" />
          <span>Targeted Log Deletion</span>
        </h3>
      </div>

      <p className="text-xs text-slate-400 leading-relaxed">
        Permanently delete specific logs by host, application, time period, or text search query.
        Deleted logs are purged from the database and search index.
      </p>

      {/* Messages */}
      {errorMsg && (
        <div className="p-3 bg-red-950/60 border border-red-800 rounded-lg flex items-start gap-2 text-xs text-red-300">
          <AlertCircle className="w-4 h-4 text-red-400 shrink-0 mt-0.5" />
          <span>{errorMsg}</span>
        </div>
      )}

      {successMsg && (
        <div className="p-3 bg-emerald-950/60 border border-emerald-800 rounded-lg flex items-start gap-2 text-xs text-emerald-300">
          <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0 mt-0.5" />
          <span>{successMsg}</span>
        </div>
      )}

      {/* Criteria Form Controls */}
      <div className="space-y-3 bg-dark-950 p-3.5 rounded-lg border border-dark-800 min-w-0 max-w-full">
        {/* Host and App dropdowns matching AlertRuleModal setup and defaulting to None */}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 w-full min-w-0">
          <div className="space-y-1 min-w-0">
            <label className="text-slate-300 font-medium flex items-center gap-1.5 text-xs">
              <Server className="w-3.5 h-3.5 text-accent-500" />
              <span>Host / IP (Optional)</span>
            </label>
            <MultiSelectDropdown
              label="Host"
              placeholder="None"
              options={availableSources}
              selected={selectedSources}
              onChange={setSelectedSources}
              allowCustomInput={true}
              variant="form"
            />
          </div>

          <div className="space-y-1 min-w-0">
            <label className="text-slate-300 font-medium flex items-center gap-1.5 text-xs">
              <Box className="w-3.5 h-3.5 text-accent-500" />
              <span>App / Container (Optional)</span>
            </label>
            <MultiSelectDropdown
              label="Application"
              placeholder="None"
              options={availableApps}
              selected={selectedApps}
              onChange={setSelectedApps}
              allowCustomInput={true}
              variant="form"
            />
          </div>
        </div>

        {/* Time Period Selector */}
        <div className="space-y-1.5 min-w-0">
          <label className="text-slate-300 font-medium flex items-center gap-1.5 text-xs">
            <Calendar className="w-3.5 h-3.5 text-accent-500" />
            <span>Time Period</span>
          </label>
          <div className="flex flex-wrap items-center gap-1.5 w-full min-w-0">
            {[
              { id: '24h', label: 'Past 24 Hours' },
              { id: '7d', label: 'Past 7 Days' },
              { id: '14d', label: 'Past 14 Days' },
              { id: '30d', label: 'Past 30 Days' },
              { id: 'all', label: 'All Time' },
              { id: 'custom', label: 'Custom Range' },
            ].map((preset) => (
              <button
                key={preset.id}
                type="button"
                onClick={() => setTimePreset((prev) => (prev === preset.id ? '' : preset.id))}
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
            <div className="flex flex-col sm:flex-row gap-3 w-full min-w-0 max-w-full pt-1">
              <div className="flex-1 min-w-0 max-w-full space-y-1 overflow-hidden">
                <span className="text-[11px] text-slate-400 block font-mono">From:</span>
                <input
                  type="datetime-local"
                  value={customFrom}
                  onChange={(e) => setCustomFrom(e.target.value)}
                  className="w-full min-w-0 max-w-full block bg-dark-900 border border-dark-700 rounded-lg px-2.5 py-1.5 text-xs text-slate-200 focus:outline-hidden focus:border-accent-500 font-mono box-border [color-scheme:dark]"
                />
              </div>
              <div className="flex-1 min-w-0 max-w-full space-y-1 overflow-hidden">
                <span className="text-[11px] text-slate-400 block font-mono">To:</span>
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

        {/* Text Search Filter */}
        <div className="space-y-1 min-w-0">
          <label htmlFor="delete-card-query" className="text-slate-300 font-medium flex items-center gap-1.5 text-xs">
            <Search className="w-3.5 h-3.5 text-accent-500" />
            <span>Search Text (Optional)</span>
          </label>
          <input
            id="delete-card-query"
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="e.g. timeout, connection reset, error"
            className="w-full min-w-0 max-w-full bg-dark-900 border border-dark-700 rounded-lg px-3 py-1.5 text-xs text-slate-200 focus:outline-hidden focus:border-accent-500 font-mono box-border"
          />
        </div>
      </div>

      {/* Live Preview Counter Box */}
      <div className="p-3 bg-dark-950 border border-dark-800 rounded-lg flex flex-col sm:flex-row sm:items-center justify-between gap-2 text-xs">
        <div className="flex items-center gap-2">
          {isPreviewLoading ? (
            <>
              <RefreshCw className="w-4 h-4 animate-spin text-accent-400 shrink-0" />
              <span className="text-slate-400">Calculating matching logs...</span>
            </>
          ) : matchedCount !== null ? (
            <div className="flex items-center gap-2">
              <span className="font-mono text-slate-400">Matching Logs:</span>
              <span
                data-testid="matched-count-display"
                className={`font-mono font-bold text-sm ${
                  matchedCount > 0 ? 'text-amber-400' : 'text-slate-400'
                }`}
              >
                {matchedCount.toLocaleString()}
              </span>
            </div>
          ) : (
            <span className="text-slate-500">
              Select criteria above to preview logs targeted for deletion.
            </span>
          )}
        </div>

        <div className="text-[11px] text-slate-500 font-mono">
          {!hasActiveCriteria ? (
            <span className="text-slate-500">No criteria selected</span>
          ) : isAllLogsTargeted ? (
            <span className="text-red-400 font-semibold">Targets entire log collection</span>
          ) : (
            <span>Filtered criteria</span>
          )}
        </div>
      </div>

      {/* High-Risk Confirmation Barrier */}
      {isHighRisk && matchedCount !== null && matchedCount > 0 && (
        <div className="p-3.5 bg-red-950/40 border border-red-800/80 rounded-lg space-y-2 text-xs text-red-200">
          <div className="flex items-center gap-2 font-semibold text-red-300">
            <AlertTriangle className="w-4 h-4 text-red-400 shrink-0" />
            <span>High Risk Operation Confirmation</span>
          </div>
          <p className="text-[11px] text-red-300/90 leading-relaxed">
            This will permanently delete{' '}
            <span className="font-bold font-mono text-white">{matchedCount.toLocaleString()}</span>{' '}
            logs. This action cannot be reversed. To proceed, type{' '}
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
            className="w-full bg-dark-900 border border-red-700/80 rounded-lg px-3 py-1.5 text-xs text-red-100 placeholder-red-400/50 focus:outline-hidden focus:border-red-500 font-mono"
          />
        </div>
      )}

      {/* Action Buttons */}
      <div className="flex items-center justify-between pt-1">
        <button
          type="button"
          onClick={handleResetFilters}
          disabled={isDeleting}
          className="px-3 py-1.5 text-xs text-slate-400 hover:text-slate-200 hover:bg-dark-800 rounded-lg transition cursor-pointer flex items-center gap-1.5 disabled:opacity-50"
        >
          <RotateCcw className="w-3.5 h-3.5" />
          <span>Reset Criteria</span>
        </button>

        <button
          type="button"
          onClick={handleDelete}
          disabled={isDeleteDisabled}
          className="px-4 py-2 text-xs bg-red-600 hover:bg-red-500 text-white font-medium rounded-lg shadow-sm transition flex items-center gap-1.5 cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
        >
          {isDeleting ? (
            <>
              <RefreshCw className="w-3.5 h-3.5 animate-spin" />
              <span>Deleting Logs...</span>
            </>
          ) : (
            <>
              <Trash2 className="w-3.5 h-3.5" />
              <span>
                Delete Matching Logs{' '}
                {matchedCount !== null && matchedCount > 0 ? `(${matchedCount.toLocaleString()})` : ''}
              </span>
            </>
          )}
        </button>
      </div>
    </div>
  );
};
