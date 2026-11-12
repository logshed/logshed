// Shared by the LiveLogStream test files: the EventSource stand-in, sample logs, and the mocks each
// group of tests starts from. The virtualizer stand-in is in liveLogStreamVirtualizer.ts.
import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import { expect, vi, beforeEach } from 'vitest';
import { LiveLogStream } from '../components/logs/LiveLogStream.tsx';
import * as settingsApi from '../api/settings.ts';
import * as logsApi from '../api/logs.ts';
import * as aliasesApi from '../api/aliases.ts';
import * as dockerHostsApi from '../api/dockerHosts.ts';
import { LogEntry } from '../types.ts';
import { AliasContext, AliasContextValue } from '../context/AliasContext.tsx';


import { virtualizerControl } from './liveLogStreamVirtualizer.ts';

export { virtualizerControl };

// Mock EventSource
export class MockEventSource {
  static instances: MockEventSource[] = [];
  url: string;
  listeners: Record<string, ((event: any) => void)[]> = {};
  onerror: ((e: any) => void) | null = null;
  readyState = 1;

  constructor(url: string) {
    this.url = url;
    this.listeners = {};
    MockEventSource.instances.push(this);
  }

  addEventListener(type: string, cb: (event: any) => void) {
    if (!this.listeners[type]) this.listeners[type] = [];
    this.listeners[type].push(cb);
  }

  removeEventListener(type: string, cb: (event: any) => void) {
    if (this.listeners[type]) {
      this.listeners[type] = this.listeners[type].filter((l) => l !== cb);
    }
  }

  close() {}

  emit(type: string, data: any) {
    if (this.listeners[type]) {
      this.listeners[type].forEach((cb) => cb({ data: JSON.stringify(data) }));
    }
  }
}

(globalThis as any).EventSource = MockEventSource;

export const sampleLogs: LogEntry[] = [
  {
    id: 101,
    timestamp: '2026-09-03T14:30:15.123Z',
    received_at: '2026-09-03T14:30:15.150Z',
    source_ip: '192.168.1.50',
    source_alias: 'homelab-host',
    app_name: 'nginx',
    facility: 1,
    severity: 3,
    message: 'Nginx upstream connection timeout',
    raw: '<11>1 2026-09-03T14:30:15.123Z homelab-host nginx - - - Nginx upstream connection timeout',
  },
  {
    id: 102,
    timestamp: '2026-09-03T14:30:10.456Z',
    received_at: '2026-09-03T14:30:10.460Z',
    source_ip: '192.168.1.50',
    source_alias: 'homelab-host',
    app_name: 'postgres',
    facility: 1,
    severity: 4,
    message: 'Postgres slow query detected',
    raw: '<12>1 2026-09-03T14:30:10.456Z homelab-host postgres - - - Postgres slow query detected',
  },
  {
    id: 103,
    timestamp: '2026-09-03T14:30:05.789Z',
    received_at: '2026-09-03T14:30:05.800Z',
    source_ip: '192.168.1.1',
    source_alias: 'opnsense-router',
    app_name: 'filterlog',
    facility: 4,
    severity: 6,
    message: 'Default deny rule matched WAN block',
    raw: '<134>1 2026-09-03T14:30:05.789Z opnsense-router filterlog - - - Default deny rule matched WAN block',
  },
  {
    id: 104,
    timestamp: '2026-09-03T14:30:00.000Z',
    received_at: '2026-09-03T14:30:00.020Z',
    source_ip: '192.168.1.50',
    source_alias: 'homelab-host',
    app_name: 'docker',
    facility: 1,
    severity: 6,
    message: 'Container started cleanly',
    raw: '<14>1 2026-09-03T14:30:00.000Z homelab-host docker - - - Container started cleanly',
  },
];

/** Renders the Console inside an alias context; rerender can change the context too. */
export function renderWithContext(
  ui: React.ReactElement,
  contextValue: Partial<AliasContextValue> = {}
) {
  const value: AliasContextValue = {
    aliasVersion: 0,
    bumpAliasVersion: vi.fn(),
    ...contextValue,
  };
  const result = render(
    <AliasContext.Provider value={value}>
      {ui}
    </AliasContext.Provider>
  );
  return {
    ...result,
    rerender: (newUi: React.ReactElement, newContextValue: Partial<AliasContextValue> = {}) =>
      result.rerender(
        <AliasContext.Provider value={{ ...value, ...newContextValue }}>
          {newUi}
        </AliasContext.Provider>
      ),
  };
}

/** The mocks most Console tests start from: sample logs, database facets, no aliases or Docker hosts. */
export function setupConsoleMocks() {
  beforeEach(() => {
    window.history.replaceState(null, '', '/');
    vi.restoreAllMocks();
    vi.spyOn(dockerHostsApi, 'fetchDockerEndpoints').mockResolvedValue([]);
    MockEventSource.instances = [];
    virtualizerControl.edgeRows = null;
    virtualizerControl.headOnly = false;
    window.matchMedia = ((query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })) as any;
    vi.spyOn(logsApi, 'fetchLogs').mockResolvedValue({
      logs: [...sampleLogs],
      total: sampleLogs.length,
      limit: 500,
      offset: 0,
    });
    vi.spyOn(logsApi, 'fetchLogFacets').mockResolvedValue({
      sources: ['homelab-host', 'opnsense-router'],
      apps: ['nginx', 'postgres', 'docker', 'filterlog'],
      host_to_apps: {
        'homelab-host': ['docker', 'nginx', 'postgres'],
        'opnsense-router': ['filterlog'],
      },
      app_to_hosts: {
        docker: ['homelab-host'],
        nginx: ['homelab-host'],
        postgres: ['homelab-host'],
        filterlog: ['opnsense-router'],
      },
    });
    vi.spyOn(aliasesApi, 'fetchAliases').mockResolvedValue([]);
  });
}

/** Like setupConsoleMocks, with empty facets; returns a plain render. */
export function setupStreamMocks() {
  const renderStream = () =>
    render(
      <AliasContext.Provider value={{ aliasVersion: 0, bumpAliasVersion: vi.fn() }}>
        <LiveLogStream onDiagnoseAi={vi.fn()} />
      </AliasContext.Provider>
    );

  beforeEach(() => {
    window.history.replaceState(null, '', '/');
    vi.restoreAllMocks();
    MockEventSource.instances = [];
    virtualizerControl.edgeRows = null;
    virtualizerControl.headOnly = false;
    window.matchMedia = ((query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })) as any;
    vi.spyOn(logsApi, 'fetchLogs').mockResolvedValue({
      logs: [...sampleLogs],
      total: sampleLogs.length,
      limit: 500,
      offset: 0,
    });
    vi.spyOn(logsApi, 'fetchLogFacets').mockResolvedValue({
      sources: [],
      apps: [],
      host_to_apps: {},
      app_to_hosts: {},
    } as any);
    vi.spyOn(aliasesApi, 'fetchAliases').mockResolvedValue([]);
    vi.spyOn(dockerHostsApi, 'fetchDockerEndpoints').mockResolvedValue([]);
  });


  return { renderStream };
}

/** Database facets, plus helpers for settings, logs, rows, chips and the phone layout. */
export function setupConsoleControlsMocks() {
  const dbFacets = {
    sources: ['homelab-host', 'opnsense-router'],
    apps: ['nginx', 'postgres', 'docker', 'filterlog'],
    host_to_apps: {
      'homelab-host': ['docker', 'nginx', 'postgres'],
      'opnsense-router': ['filterlog'],
    },
    app_to_hosts: {
      docker: ['homelab-host'],
      nginx: ['homelab-host'],
      postgres: ['homelab-host'],
      filterlog: ['opnsense-router'],
    },
  };
  const emptyFacets = { sources: [], apps: [], host_to_apps: {}, app_to_hosts: {} };

  const renderStream = (props: Partial<React.ComponentProps<typeof LiveLogStream>> = {}) =>
    render(
      <AliasContext.Provider value={{ aliasVersion: 0, bumpAliasVersion: vi.fn() }}>
        <LiveLogStream onDiagnoseAi={vi.fn()} {...props} />
      </AliasContext.Provider>
    );

  const mockSettings = (settings: Record<string, unknown>) =>
    vi.spyOn(settingsApi, 'fetchSettings').mockResolvedValue(settings as any);

  const mockLogs = (logs: LogEntry[]) =>
    vi.spyOn(logsApi, 'fetchLogs').mockResolvedValue({ logs, total: logs.length, limit: 500, offset: 0 });

  const waitForRows = () =>
    waitFor(() => expect(screen.getByText('Nginx upstream connection timeout')).toBeInTheDocument());

  const chipTexts = (testId: string) => screen.queryAllByTestId(testId).map((el) => el.textContent);

  const setMobile = (mobile: boolean) => {
    window.matchMedia = ((query: string) => ({
      matches: mobile && query.includes('max-width: 767px'),
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })) as any;
  };

  beforeEach(() => {
    window.history.replaceState(null, '', '/');
    vi.restoreAllMocks();
    MockEventSource.instances = [];
    virtualizerControl.edgeRows = null;
    virtualizerControl.headOnly = false;
    setMobile(false);
    mockLogs([...sampleLogs]);
    vi.spyOn(logsApi, 'fetchLogFacets').mockResolvedValue(dbFacets as any);
    vi.spyOn(aliasesApi, 'fetchAliases').mockResolvedValue([]);
    vi.spyOn(dockerHostsApi, 'fetchDockerEndpoints').mockResolvedValue([]);
  });

  return { dbFacets, emptyFacets, renderStream, mockSettings, mockLogs, waitForRows, chipTexts, setMobile };
}
