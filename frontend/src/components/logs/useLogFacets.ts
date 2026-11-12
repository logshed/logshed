import { MutableRefObject, useCallback, useEffect, useMemo, useState } from 'react';
import { LogEntry, LogFacetsResponse } from '../../types.ts';
import { fetchLogFacets } from '../../api/logs.ts';
import { SourcesChange } from './useSourcesConfig.ts';

interface LogFacetsOptions {
  /** Host aliases by address, and the latest of them for callbacks created once */
  aliases: Record<string, string>;
  aliasesRef: MutableRefObject<Record<string, string>>;
  dockerAliasesRef: MutableRefObject<Set<string>>;
  /** Hosts and apps the filters select, which scope each other's lists */
  activeHosts: string[];
  activeApps: string[];
  /** The stored facets arrived (once, on mount) */
  onLoaded: (facets: LogFacetsResponse) => void;
}

/** Merges two name-to-names maps, sorting each merged list. */
function mergeRelations(
  base: Record<string, string[]>,
  extra: Record<string, string[]>,
): Record<string, string[]> {
  const next = { ...base };
  Object.entries(extra).forEach(([key, values]) => {
    if (!next[key]) next[key] = values;
    else next[key] = Array.from(new Set([...next[key], ...values])).sort();
  });
  return next;
}

/**
 * The hosts and apps the Console's filters offer, and which apps each host has sent (and the
 * reverse). Read from the whole database on mount, then grown from every page and live batch
 * (addLogs) without rescanning the buffer, and renamed when aliases change.
 */
export function useLogFacets({
  aliases,
  aliasesRef,
  dockerAliasesRef,
  activeHosts,
  activeApps,
  onLoaded,
}: LogFacetsOptions) {
  const [accumulatedSources, setAccumulatedSources] = useState<string[]>([]);
  const [accumulatedApps, setAccumulatedApps] = useState<string[]>([]);
  const [facetsTruncated, setFacetsTruncated] = useState<boolean>(false);
  const [facetsLimit, setFacetsLimit] = useState<number | undefined>(undefined);
  const [hostToAppsMap, setHostToAppsMap] = useState<Record<string, string[]>>({});
  const [appToHostsMap, setAppToHostsMap] = useState<Record<string, string[]>>({});

  // Incrementally accumulate facets from incoming batches without rescanning 50k logs
  const addLogs = useCallback((newLogs: LogEntry[]) => {
    if (!newLogs || newLogs.length === 0) return;

    const getLogCanonicalHost = (log: LogEntry): string => {
      const isDocker =
        log.source_ip === 'docker' ||
        Boolean(dockerAliasesRef.current && log.source_alias && dockerAliasesRef.current.has(log.source_alias));

      if (isDocker) {
        return log.source_alias || 'docker';
      }
      return (
        (log.source_alias && aliasesRef.current[log.source_alias]) ||
        (log.source_ip && aliasesRef.current[log.source_ip]) ||
        log.source_alias ||
        log.source_ip ||
        ''
      );
    };

    // 1. Incrementally add any new sources
    setAccumulatedSources((prev) => {
      let added = false;
      const currentSet = new Set(prev);
      const aliasedIps = new Set(Object.keys(aliasesRef.current || {}));
      for (const log of newLogs) {
        const canonical = getLogCanonicalHost(log);
        if (canonical && !aliasedIps.has(canonical) && !currentSet.has(canonical)) {
          currentSet.add(canonical);
          added = true;
        }
      }
      return added ? Array.from(currentSet).sort() : prev;
    });

    // 2. Incrementally add any new apps
    setAccumulatedApps((prev) => {
      let added = false;
      const currentSet = new Set(prev);
      for (const log of newLogs) {
        if (log.app_name && !currentSet.has(log.app_name)) {
          currentSet.add(log.app_name);
          added = true;
        }
      }
      return added ? Array.from(currentSet).sort() : prev;
    });

    // 3. Incrementally update hostToAppsMap
    setHostToAppsMap((prev) => {
      let changed = false;
      const next = { ...prev };
      for (const log of newLogs) {
        if (log.app_name) {
          const canonical = getLogCanonicalHost(log);
          if (canonical) {
            const currentApps = next[canonical] || [];
            if (!currentApps.includes(log.app_name)) {
              next[canonical] = [...currentApps, log.app_name].sort();
              changed = true;
            }
          }
        }
      }
      return changed ? next : prev;
    });

    // 4. Incrementally update appToHostsMap
    setAppToHostsMap((prev) => {
      let changed = false;
      const next = { ...prev };
      for (const log of newLogs) {
        if (log.app_name) {
          const canonical = getLogCanonicalHost(log);
          if (canonical) {
            const currentHosts = next[log.app_name] || [];
            if (!currentHosts.includes(canonical)) {
              next[log.app_name] = [...currentHosts, canonical].sort();
              changed = true;
            }
          }
        }
      }
      return changed ? next : prev;
    });
  }, [aliasesRef, dockerAliasesRef]);

  /** Renames hosts after the aliases or Docker hosts changed (useSourcesConfig's onChanged). */
  const applySourcesChange = useCallback(({ aliases: newMerged, hostReplacements, retiredHosts }: SourcesChange) => {
    // Update accumulatedSources: replace retired aliases/IPs with their new canonical names
    setAccumulatedSources((prev) => {
      const aliasedIps = new Set(Object.keys(newMerged));
      const next = new Set<string>();
      prev.forEach((s) => {
        const mapped = hostReplacements[s] || (newMerged[s] ? newMerged[s] : s);
        if (!retiredHosts.has(mapped) && !aliasedIps.has(mapped)) {
          next.add(mapped);
        }
      });
      Object.values(newMerged).forEach((alias) => {
        if (alias && alias.trim()) next.add(alias.trim());
      });
      return Array.from(next).sort();
    });

    // Remap hostToAppsMap keys
    setHostToAppsMap((prev) => {
      const next: Record<string, string[]> = {};
      Object.entries(prev).forEach(([h, apps]) => {
        const mapped = hostReplacements[h] || (newMerged[h] ? newMerged[h] : h);
        next[mapped] = Array.from(new Set([...(next[mapped] || []), ...apps])).sort();
      });
      return next;
    });

    // Remap appToHostsMap values
    setAppToHostsMap((prev) => {
      const next: Record<string, string[]> = {};
      Object.entries(prev).forEach(([app, hosts]) => {
        const mappedHosts = hosts
          .map((h) => hostReplacements[h] || (newMerged[h] ? newMerged[h] : h))
          .filter((h) => !retiredHosts.has(h));
        next[app] = Array.from(new Set(mappedHosts)).sort();
      });
      return next;
    });
  }, []);

  // Fetch full database facets on mount so all historical hosts and apps are available
  useEffect(() => {
    fetchLogFacets()
      .then((facets) => {
        onLoaded(facets);
        setFacetsTruncated(Boolean(facets.truncated));
        setFacetsLimit(facets.limit);
        if (facets.sources && facets.sources.length > 0) {
          setAccumulatedSources((prev) => Array.from(new Set([...prev, ...facets.sources])).sort());
        }
        if (facets.apps && facets.apps.length > 0) {
          setAccumulatedApps((prev) => Array.from(new Set([...prev, ...facets.apps])).sort());
        }
        if (facets.host_to_apps) {
          setHostToAppsMap((prev) => mergeRelations(facets.host_to_apps, prev));
        }
        if (facets.app_to_hosts) {
          setAppToHostsMap((prev) => mergeRelations(facets.app_to_hosts, prev));
        }
      })
      .catch((err) => {
        console.error('Failed to load database facets', err);
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps -- loaded once on mount; onLoaded only calls state setters and refs
  }, []);

  // Remap host/app relations whenever aliases are added or modified
  useEffect(() => {
    if (!aliases || Object.keys(aliases).length === 0) return;

    setAccumulatedSources((prev) => {
      const aliasedIps = new Set(Object.keys(aliases));
      const set = new Set<string>();
      let changed = false;

      prev.forEach((s) => {
        if (aliases[s]) {
          set.add(aliases[s]);
          changed = true;
        } else if (!aliasedIps.has(s)) {
          set.add(s);
        } else {
          changed = true;
        }
      });

      Object.values(aliases).forEach((alias) => {
        if (alias && alias.trim() && !set.has(alias.trim())) {
          set.add(alias.trim());
          changed = true;
        }
      });

      return changed ? Array.from(set).sort() : prev;
    });

    setHostToAppsMap((prev) => {
      let changed = false;
      const next = { ...prev };
      Object.entries(aliases).forEach(([ip, alias]) => {
        if (ip && alias && next[ip]) {
          const existingForAlias = next[alias] || [];
          next[alias] = Array.from(new Set([...existingForAlias, ...next[ip]])).sort();
          delete next[ip];
          changed = true;
        }
      });
      return changed ? next : prev;
    });

    setAppToHostsMap((prev) => {
      let changed = false;
      const next = { ...prev };
      Object.entries(aliases).forEach(([ip, alias]) => {
        if (ip && alias) {
          Object.keys(next).forEach((app) => {
            if (next[app]?.includes(ip)) {
              next[app] = Array.from(new Set(next[app].map((h) => (h === ip ? alias : h)))).sort();
              changed = true;
            }
          });
        }
      });
      return changed ? next : prev;
    });
  }, [aliases]);

  // Merge accumulated sources & apps with aliases (ensuring all discovered items remain selectable)
  const allAvailableSources = useMemo(() => {
    const aliasedIps = new Set(Object.keys(aliases || {}));
    const set = new Set<string>();

    accumulatedSources.forEach((src) => {
      if (aliases && aliases[src]) {
        set.add(aliases[src]);
      } else if (!aliasedIps.has(src)) {
        set.add(src);
      }
    });

    if (aliases) {
      Object.values(aliases).forEach((alias) => {
        if (alias && alias.trim()) set.add(alias.trim());
      });
    }

    return Array.from(set).sort();
  }, [accumulatedSources, aliases]);

  const allAvailableApps = useMemo(() => {
    return Array.from(new Set(accumulatedApps)).sort();
  }, [accumulatedApps]);

  // Scope available apps to only those matching the selected host(s) if host(s) are chosen
  const availableAppsForSelectedHosts = useMemo(() => {
    if (activeHosts.length === 0) {
      return allAvailableApps;
    }
    const set = new Set<string>();
    activeHosts.forEach((src) => {
      const apps = hostToAppsMap[src];
      if (apps) {
        apps.forEach((a) => set.add(a));
      }
    });
    const result = Array.from(set).sort();
    return result.length > 0 ? result : allAvailableApps;
  }, [activeHosts, allAvailableApps, hostToAppsMap]);

  // Scope available sources to only those hosting the selected app(s) if app(s) are chosen
  const availableSourcesForSelectedApps = useMemo(() => {
    if (activeApps.length === 0) {
      return allAvailableSources;
    }
    const set = new Set<string>();
    activeApps.forEach((app) => {
      const hosts = appToHostsMap[app];
      if (hosts) {
        hosts.forEach((h) => set.add(h));
      }
    });
    const result = Array.from(set).sort();
    return result.length > 0 ? result : allAvailableSources;
  }, [activeApps, allAvailableSources, appToHostsMap]);

  return {
    allAvailableSources,
    allAvailableApps,
    availableAppsForSelectedHosts,
    availableSourcesForSelectedApps,
    facetsTruncated,
    facetsLimit,
    addLogs,
    applySourcesChange,
  };
}
