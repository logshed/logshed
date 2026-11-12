import { MutableRefObject, useEffect, useMemo, useRef, useState } from 'react';
import { LogEntry, LogFilterParams } from '../../types.ts';
import {
  QUICK_FILTER_RERANK_MS,
  QuickFilterCounts,
  countQuickFilterVolumes,
  rankQuickFilterChips,
} from '../../utils/quickFilters.ts';

interface QuickFilterChipsOptions {
  logs: LogEntry[];
  filters: LogFilterParams;
  activeHosts: string[];
  activeApps: string[];
  allAvailableSources: string[];
  allAvailableApps: string[];
  availableAppsForSelectedHosts: string[];
  availableSourcesForSelectedApps: string[];
  /** Set to rank the chips again at the next change of the list (new results, a cleared buffer) */
  rerankRef: MutableRefObject<boolean>;
}

/**
 * The host and app chips of the Quick Filters bar. Chips are ranked by volume in the loaded buffer.
 * The counts are a snapshot taken when the filters change (once their results load) and at most
 * every QUICK_FILTER_RERANK_MS while live, so the chips keep their order as logs stream in.
 */
export function useQuickFilterChips({
  logs,
  filters,
  activeHosts,
  activeApps,
  allAvailableSources,
  allAvailableApps,
  availableAppsForSelectedHosts,
  availableSourcesForSelectedApps,
  rerankRef,
}: QuickFilterChipsOptions) {
  const chipRankedAtRef = useRef<number>(0);
  const [chipCounts, setChipCounts] = useState<QuickFilterCounts>({ hosts: {}, apps: {} });

  useEffect(() => {
    rerankRef.current = true;
  }, [filters, rerankRef]);

  // Hosts are the top level and apps live under them. Host chips are ranked from the unfiltered
  // buffer and stay where they are while any chip is selected (after a first ranking); hosts the
  // selected apps rule out are shown disabled. App chips follow the selected hosts: their apps,
  // ranked by volume in the buffer, which then holds only those hosts' rows. While an app is
  // selected the buffer holds only that app, so the app ranking is kept as it was.
  const [appChipCounts, setAppChipCounts] = useState<Record<string, number>>({});
  const chipSelectionRef = useRef({ hosts: 0, apps: 0 });
  chipSelectionRef.current = { hosts: activeHosts.length, apps: activeApps.length };

  useEffect(() => {
    const now = Date.now();
    if (!rerankRef.current && now - chipRankedAtRef.current < QUICK_FILTER_RERANK_MS) return;
    const { hosts: selectedHosts, apps: selectedApps } = chipSelectionRef.current;
    const firstRanking = chipRankedAtRef.current === 0;
    rerankRef.current = false;
    chipRankedAtRef.current = now;
    const counts = countQuickFilterVolumes(logs);
    if (firstRanking || (selectedHosts === 0 && selectedApps === 0)) setChipCounts(counts);
    if (firstRanking || selectedApps === 0) setAppChipCounts(counts.apps);
  }, [logs, rerankRef]);

  const hostChips = useMemo(
    () => rankQuickFilterChips(allAvailableSources, chipCounts.hosts, activeHosts),
    [allAvailableSources, chipCounts.hosts, activeHosts],
  );

  const appChips = useMemo(
    () =>
      rankQuickFilterChips(
        activeHosts.length > 0 ? availableAppsForSelectedHosts : allAvailableApps,
        appChipCounts,
        activeApps,
      ),
    [activeHosts.length, availableAppsForSelectedHosts, allAvailableApps, appChipCounts, activeApps],
  );

  // Hosts that the selected apps leave selectable; the others show disabled
  const selectableHosts = useMemo(() => new Set(availableSourcesForSelectedApps), [availableSourcesForSelectedApps]);

  return { hostChips, appChips, selectableHosts };
}
