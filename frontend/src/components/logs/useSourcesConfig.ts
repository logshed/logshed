import { useEffect, useRef, useState } from 'react';
import { DockerEndpoint, HostAlias } from '../../types.ts';
import { fetchAliases } from '../../api/aliases.ts';
import { fetchDockerEndpoints } from '../../api/dockerHosts.ts';
import { useAlias } from '../../context/AliasContext.tsx';
import { buildDockerAliasSet, ProcessedLogEntry } from '../../utils/logEntry.ts';

/** How the hosts the Console shows changed when the aliases or Docker hosts were edited. */
export interface SourcesChange {
  /** Host aliases by address, before and after */
  previousAliases: Record<string, string>;
  aliases: Record<string, string>;
  /** Old host name to the name it is shown as now (renamed alias, removed alias back to its address) */
  hostReplacements: Record<string, string>;
  /** Host names no longer shown (renamed, removed, or replaced by a new alias) */
  retiredHosts: Set<string>;
  /** Shows the rows already in the list under their new host names */
  remapRow: (log: ProcessedLogEntry) => ProcessedLogEntry;
}

export interface SourcesConfigHandlers {
  /** The aliases and Docker hosts arrived (first load); rows prepared before then may need them */
  onLoaded: (aliases: Record<string, string>, dockerAliases: Set<string>) => void;
  /** The aliases or Docker hosts were edited elsewhere in the app and have been read again */
  onChanged: (change: SourcesChange) => void;
}

function aliasMap(list: HostAlias[]): Record<string, string> {
  const map: Record<string, string> = {};
  list.forEach((a) => {
    if (a.ip && a.alias) {
      map[a.ip] = a.alias;
    }
  });
  return map;
}

function loadAliasesAndEndpoints(action: 'load' | 'reload') {
  return Promise.all([
    fetchAliases().catch((err) => {
      console.error(`Failed to ${action} host aliases in stream`, err);
      return [] as HostAlias[];
    }),
    fetchDockerEndpoints().catch((err) => {
      console.error(`Failed to ${action} Docker endpoints in stream`, err);
      return [] as DockerEndpoint[];
    }),
  ]);
}

/**
 * Works out which host names changed between two alias lists and Docker host lists, and how each
 * row already shown is renamed. Docker rows keep their Docker host name and never take a syslog alias.
 */
export function diffSources(
  previousAliases: Record<string, string>,
  aliases: Record<string, string>,
  previousDockerEndpoints: DockerEndpoint[],
  endpointsList: DockerEndpoint[],
  formerDockerAliases: string[],
): SourcesChange {
  // Build all current and previous Docker aliases to reliably identify Docker logs
  const allDockerAliases = new Set<string>(formerDockerAliases);
  endpointsList.forEach((ep) => {
    if (ep.source_alias) allDockerAliases.add(ep.source_alias);
  });
  previousDockerEndpoints.forEach((ep) => {
    if (ep.source_alias) allDockerAliases.add(ep.source_alias);
  });
  allDockerAliases.add('docker');

  // Build mapping of old host identifiers to new canonical hostnames
  const hostReplacements: Record<string, string> = {};
  const retiredHosts = new Set<string>();

  Object.entries(previousAliases).forEach(([ip, oldAlias]) => {
    const newAlias = aliases[ip];
    if (!newAlias) {
      // Alias was deleted: revert to raw IP
      hostReplacements[oldAlias] = ip;
      retiredHosts.add(oldAlias);
    } else if (newAlias !== oldAlias) {
      // Alias was renamed
      hostReplacements[oldAlias] = newAlias;
      retiredHosts.add(oldAlias);
    }
  });

  Object.entries(aliases).forEach(([ip, newAlias]) => {
    if (newAlias && (!previousAliases[ip] || previousAliases[ip] !== newAlias)) {
      hostReplacements[ip] = newAlias;
      retiredHosts.add(ip);
    }
  });

  // Docker alias updates
  const primaryPrev = previousDockerEndpoints.find((e) => e.id === 1);
  const primaryCurr = endpointsList.find((e) => e.id === 1);
  if (primaryCurr) {
    const oldPrimaryAlias = primaryPrev?.source_alias || 'docker';
    const newPrimaryAlias = primaryCurr.source_alias || 'docker';
    if (oldPrimaryAlias !== newPrimaryAlias) {
      hostReplacements[oldPrimaryAlias] = newPrimaryAlias;
      retiredHosts.add(oldPrimaryAlias);
    }
  }

  endpointsList.forEach((ep) => {
    if (ep.id > 1) {
      const prevEp = previousDockerEndpoints.find((e) => e.id === ep.id);
      if (prevEp && prevEp.source_alias && ep.source_alias && prevEp.source_alias !== ep.source_alias) {
        hostReplacements[prevEp.source_alias] = ep.source_alias;
        retiredHosts.add(prevEp.source_alias);
      }
    }
  });

  previousDockerEndpoints.forEach((prevEp) => {
    if (!endpointsList.some((e) => e.id === prevEp.id)) {
      if (prevEp.source_alias) {
        retiredHosts.add(prevEp.source_alias);
      }
    }
  });

  // Update source_alias on matching in-memory logs with strict separation between Docker and Syslog
  const remapRow = (log: ProcessedLogEntry): ProcessedLogEntry => {
    const isDocker =
      log.source_ip === 'docker' ||
      Boolean(log.source_alias && allDockerAliases.has(log.source_alias));

    if (isDocker) {
      // Primary Docker endpoint
      if (log.source_ip === 'docker') {
        const targetAlias = primaryCurr?.source_alias || 'docker';
        if (log.source_alias !== targetAlias) {
          return { ...log, source_alias: targetAlias };
        }
        return log;
      }

      // Remote Docker endpoints
      if (log.source_alias) {
        for (const ep of endpointsList) {
          if (ep.id > 1) {
            const prevEp = previousDockerEndpoints.find((e) => e.id === ep.id);
            if (prevEp && prevEp.source_alias && ep.source_alias && prevEp.source_alias !== ep.source_alias) {
              if (log.source_alias === prevEp.source_alias) {
                return { ...log, source_alias: ep.source_alias };
              }
            }
          }
        }
      }

      // Docker log is completely protected from Syslog remapping
      return log;
    }

    // Syslog logs ONLY
    if (!log.source_ip) return log;
    if (aliases[log.source_ip]) {
      const newAlias = aliases[log.source_ip];
      if (log.source_alias !== newAlias) {
        return { ...log, source_alias: newAlias };
      }
    } else if (previousAliases[log.source_ip]) {
      // Alias was removed; revert to raw IP matching backend behavior
      if (log.source_alias !== log.source_ip) {
        return { ...log, source_alias: log.source_ip };
      }
    }
    return log;
  };

  return { previousAliases, aliases, hostReplacements, retiredHosts, remapRow };
}

/**
 * The host aliases and Docker hosts the Console names rows by. Loaded on mount, and read again
 * whenever the alias context's version moves (an alias or Docker host was edited elsewhere).
 * The refs hold the latest values for callbacks that are created once.
 */
export function useSourcesConfig(handlers: SourcesConfigHandlers) {
  const [aliases, setAliases] = useState<Record<string, string>>({});
  const aliasesRef = useRef<Record<string, string>>(aliases);
  const [dockerEndpoints, setDockerEndpoints] = useState<DockerEndpoint[]>([]);
  const dockerEndpointsRef = useRef<DockerEndpoint[]>([]);
  // Every source_alias that marks a row as Docker output (buildDockerAliasSet)
  const dockerAliasesRef = useRef<Set<string>>(new Set());
  const [formerDockerAliases, setFormerDockerAliases] = useState<string[]>([]);
  const formerDockerAliasesRef = useRef<string[]>([]);
  const { aliasVersion } = useAlias();
  const lastHandledAliasVersionRef = useRef<number>(aliasVersion);
  const handlersRef = useRef(handlers);
  handlersRef.current = handlers;

  const applyEndpoints = (endpointsList: DockerEndpoint[]) => {
    setDockerEndpoints(endpointsList);
    dockerEndpointsRef.current = endpointsList;
    dockerAliasesRef.current = buildDockerAliasSet(endpointsList, formerDockerAliasesRef.current);
  };
  const applyAliases = (map: Record<string, string>) => {
    setAliases(map);
    aliasesRef.current = map;
  };

  useEffect(() => {
    loadAliasesAndEndpoints('load').then(([aliasesList, endpointsList]) => {
      const map = aliasMap(aliasesList);
      applyAliases(map);
      applyEndpoints(endpointsList);
      handlersRef.current.onLoaded(map, dockerAliasesRef.current);
    });
  }, []);

  // Read the aliases and Docker hosts again when aliasVersion moves past the version handled
  useEffect(() => {
    if (aliasVersion <= lastHandledAliasVersionRef.current) {
      lastHandledAliasVersionRef.current = aliasVersion;
      return;
    }
    lastHandledAliasVersionRef.current = aliasVersion;

    const previousAliases = aliasesRef.current || {};
    const previousDockerEndpoints = dockerEndpointsRef.current || [];

    loadAliasesAndEndpoints('reload')
      .then(([list, endpointsList]) => {
        applyEndpoints(endpointsList);
        const map = aliasMap(list);
        applyAliases(map);
        handlersRef.current.onChanged(
          diffSources(previousAliases, map, previousDockerEndpoints, endpointsList, formerDockerAliasesRef.current),
        );
      })
      .catch((err) => {
        console.error('Failed to reload host aliases in stream', err);
      });
  }, [aliasVersion]);

  useEffect(() => {
    aliasesRef.current = aliases;
  }, [aliases]);

  /** Sets the aliases of deleted or renamed Docker hosts (from the facets) and returns the new Docker alias set. */
  const applyFormerDockerAliases = (former: string[]): Set<string> => {
    formerDockerAliasesRef.current = former;
    setFormerDockerAliases(former);
    dockerAliasesRef.current = buildDockerAliasSet(dockerEndpointsRef.current, former);
    return dockerAliasesRef.current;
  };

  return {
    aliases,
    aliasesRef,
    dockerEndpoints,
    dockerAliasesRef,
    formerDockerAliases,
    applyFormerDockerAliases,
  };
}
