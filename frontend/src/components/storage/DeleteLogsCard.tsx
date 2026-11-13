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
import { fromLocalDatetimeInputString, timeInputZoneLabel } from '../../utils/formatters.ts';
import { useServerTimezone } from '../../utils/hooks.ts';
import { Button } from '../common/Button.tsx';
import { Card, CardHeader } from '../common/Card.tsx';
import { TextField } from '../common/Field.tsx';

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
  // The custom range is read in the server's time zone, like the Console's date pickers
  const serverTimezone = useServerTimezone();
  const zoneLabel = timeInputZoneLabel(serverTimezone);

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
        derivedFrom: fromLocalDatetimeInputString(customFrom, serverTimezone),
        derivedTo: fromLocalDatetimeInputString(customTo, serverTimezone),
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
  }, [timePreset, customFrom, customTo, serverTimezone]);

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
    <Card className="min-w-0 max-w-full">
      <CardHeader
        icon={<Trash2 />}
        title="Targeted Log Deletion"
        description="Permanently delete specific logs by host, application, time period, or text search query. Deleted logs are purged from the database and search index."
      />

      {/* Messages */}
      {errorMsg && (
        <div className="p-3 bg-danger-950/60 border border-danger-800 rounded-lg flex items-start gap-2 text-xs text-danger-300">
          <AlertCircle className="w-4 h-4 text-danger-400 shrink-0 mt-0.5" />
          <span>{errorMsg}</span>
        </div>
      )}

      {successMsg && (
        <div className="p-3 bg-success-950/60 border border-success-800 rounded-lg flex items-start gap-2 text-xs text-success-300">
          <CheckCircle2 className="w-4 h-4 text-success-400 shrink-0 mt-0.5" />
          <span>{successMsg}</span>
        </div>
      )}

      {/* Criteria Form Controls */}
      <div className="space-y-3 bg-canvas p-3.5 rounded-lg border border-line-subtle min-w-0 max-w-full">
        {/* Host and App dropdowns matching AlertRuleModal setup and defaulting to None */}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 w-full min-w-0">
          <div className="space-y-1 min-w-0">
            <label className="text-fg-soft font-medium flex items-center gap-1.5 text-xs">
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
            <label className="text-fg-soft font-medium flex items-center gap-1.5 text-xs">
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
          <label className="text-fg-soft font-medium flex items-center gap-1.5 text-xs">
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
                    ? 'bg-accent-700 text-on-fill font-semibold shadow-xs'
                    : 'bg-canvas text-fg-muted hover:text-fg hover:bg-raised border border-line'
                }`}
              >
                {preset.label}
              </button>
            ))}
          </div>

          {timePreset === 'custom' && (
            <div className="flex flex-col sm:flex-row gap-3 w-full min-w-0 max-w-full pt-1">
              <div className="flex-1 min-w-0 max-w-full space-y-1 overflow-hidden">
                <span className="text-[11px] text-fg-muted block font-mono">From ({zoneLabel}):</span>
                <TextField
                  type="datetime-local"
                  aria-label={`From (${zoneLabel})`}
                  value={customFrom}
                  onChange={(e) => setCustomFrom(e.target.value)}
                  mono
                />
              </div>
              <div className="flex-1 min-w-0 max-w-full space-y-1 overflow-hidden">
                <span className="text-[11px] text-fg-muted block font-mono">To ({zoneLabel}):</span>
                <TextField
                  type="datetime-local"
                  aria-label={`To (${zoneLabel})`}
                  value={customTo}
                  onChange={(e) => setCustomTo(e.target.value)}
                  mono
                />
              </div>
            </div>
          )}
        </div>

        {/* Text Search Filter */}
        <div className="space-y-1 min-w-0">
          <label htmlFor="delete-card-query" className="text-fg-soft font-medium flex items-center gap-1.5 text-xs">
            <Search className="w-3.5 h-3.5 text-accent-500" />
            <span>Search Text (Optional)</span>
          </label>
          <TextField
            id="delete-card-query"
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="e.g. timeout, connection reset, error"
            mono
            className="min-w-0"
          />
        </div>
      </div>

      {/* Live Preview Counter Box */}
      <div className="p-3 bg-canvas border border-line-subtle rounded-lg flex flex-col sm:flex-row sm:items-center justify-between gap-2 text-xs">
        <div className="flex items-center gap-2">
          {isPreviewLoading ? (
            <>
              <RefreshCw className="w-4 h-4 animate-spin text-accent-400 shrink-0" />
              <span className="text-fg-muted">Calculating matching logs...</span>
            </>
          ) : matchedCount !== null ? (
            <div className="flex items-center gap-2">
              <span className="font-mono text-fg-muted">Matching Logs:</span>
              <span
                data-testid="matched-count-display"
                className={`font-mono font-bold text-sm ${
                  matchedCount > 0 ? 'text-warning-400' : 'text-fg-muted'
                }`}
              >
                {matchedCount.toLocaleString()}
              </span>
            </div>
          ) : (
            <span className="text-fg-muted">
              Select criteria above to preview logs targeted for deletion.
            </span>
          )}
        </div>

        <div className="text-[11px] text-fg-muted font-mono">
          {!hasActiveCriteria ? (
            <span className="text-fg-muted">No criteria selected</span>
          ) : isAllLogsTargeted ? (
            <span className="text-danger-400 font-semibold">Targets entire log collection</span>
          ) : (
            <span>Filtered criteria</span>
          )}
        </div>
      </div>

      {/* High-Risk Confirmation Barrier */}
      {isHighRisk && matchedCount !== null && matchedCount > 0 && (
        <div className="p-3.5 bg-danger-950/40 border border-danger-800/80 rounded-lg space-y-2 text-xs text-danger-200">
          <div className="flex items-center gap-2 font-semibold text-danger-300">
            <AlertTriangle className="w-4 h-4 text-danger-400 shrink-0" />
            <span>High Risk Operation Confirmation</span>
          </div>
          <p className="text-[11px] text-danger-300/90 leading-relaxed">
            This will permanently delete{' '}
            <span className="font-bold font-mono text-fg-max">{matchedCount.toLocaleString()}</span>{' '}
            logs. This action cannot be reversed. To proceed, type{' '}
            <span className="font-mono font-bold bg-canvas px-1 py-0.5 rounded-sm text-fg-max border border-danger-900">
              DELETE
            </span>{' '}
            below:
          </p>
          <input
            type="text"
            value={confirmText}
            onChange={(e) => setConfirmText(e.target.value)}
            placeholder="Type DELETE to confirm"
            className="w-full bg-surface border border-danger-700/80 rounded-lg px-3 py-1.5 text-xs text-danger-100 placeholder-danger-400/50 focus:outline-hidden focus:border-danger-500 font-mono"
          />
        </div>
      )}

      {/* Action Buttons */}
      <div className="flex items-center justify-between pt-1">
        <Button
          variant="ghost"
          onClick={handleResetFilters}
          disabled={isDeleting}
          icon={<RotateCcw className="w-3.5 h-3.5" />}
        >
          Reset Criteria
        </Button>

        <Button
          variant="danger"
          onClick={handleDelete}
          disabled={isDeleteDisabled}
          icon={isDeleting ? <RefreshCw className="w-3.5 h-3.5 animate-spin" /> : <Trash2 className="w-3.5 h-3.5" />}
        >
          {isDeleting
            ? 'Deleting Logs...'
            : `Delete Matching Logs ${matchedCount !== null && matchedCount > 0 ? `(${matchedCount.toLocaleString()})` : ''}`.trim()}
        </Button>
      </div>
    </Card>
  );
};
