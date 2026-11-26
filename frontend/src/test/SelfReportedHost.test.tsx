import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { LogRow } from '../components/logs/LogRow.tsx';
import { StreamToolbar } from '../components/logs/StreamToolbar.tsx';
import { LogDetailModal } from '../components/logs/LogDetailModal.tsx';
import { diffSources } from '../components/logs/useSourcesConfig.ts';
import { prepareLogEntry, ProcessedLogEntry } from '../utils/logEntry.ts';
import { DockerEndpoint } from '../types.ts';

// A device at 172.17.0.4 with no alias sends the hostname printer-hp, which no configured host uses
const reportedLog: ProcessedLogEntry = {
  id: 7,
  timestamp: '2026-11-14T11:17:13.992Z',
  received_at: '2026-11-14T11:17:13.992Z',
  source_ip: '172.17.0.4',
  source_alias: 'printer-hp',
  app_name: 'cupsd',
  facility: 1,
  severity: 6,
  message: 'Paper low in tray 2',
  raw: '<14>Nov 14 11:17:13 printer-hp cupsd[311]: Paper low in tray 2',
  source_kind: 'syslog',
  reported_host: 'printer-hp',
  host_self_reported: true,
  formattedTimestamp: '11:17:13.992',
  cleanedMessage: 'Paper low in tray 2',
};

// The same device sending truenas, the alias of 192.168.10.20: shown by its address
const refusedLog: ProcessedLogEntry = {
  ...reportedLog,
  id: 8,
  source_alias: '172.17.0.4',
  reported_host: 'truenas',
  host_self_reported: false,
  message: 'session opened for user root',
  cleanedMessage: 'session opened for user root',
};

const row = (log: ProcessedLogEntry, isMobile = false) =>
  render(
    <LogRow log={log} index={0} isSelected={false} isMobile={isMobile} style={{}} onSelect={vi.fn()} onClick={vi.fn()} onDiagnoseAi={vi.fn()} />
  );

describe('Self-reported host names', () => {
  it('marks a self-reported name in the host column, with the address on hover', () => {
    row(reportedLog);
    const cell = screen.getByText('printer-hp').closest('[title]');
    expect(cell).toHaveAttribute('title', 'printer-hp: the name 172.17.0.4 reports for itself (no alias set)');
    expect(screen.getByText('(self-reported)')).toHaveClass('sr-only');
  });

  it('draws the row badge as one masked box after the name, with no wrapper or SVG', () => {
    const { container } = row(reportedLog);
    const cell = screen.getByText('printer-hp').closest('[title]') as HTMLElement;
    expect(cell.querySelector('svg')).toBeNull();
    expect(cell.querySelectorAll('.self-reported-mark')).toHaveLength(1);
    expect(cell.firstChild?.textContent).toBe('printer-hp');
    expect(container.querySelector('.inline-flex .self-reported-mark')).toBeNull();
  });

  it('leaves configured names and addresses unmarked', () => {
    row(refusedLog);
    expect(screen.getByText('172.17.0.4')).toBeInTheDocument();
    expect(screen.queryByText('(self-reported)')).toBeNull();
  });

  it('marks it on a phone too', () => {
    row(reportedLog, true);
    expect(screen.getByText('(self-reported)')).toBeInTheDocument();
  });

  it('marks self-reported names among the Hosts chips', () => {
    render(
      <StreamToolbar
        isMobile={false}
        pinnedViews={[]}
        activeSavedView={null}
        onApplySavedView={vi.fn()}
        onResetFilters={vi.fn()}
        hostChips={['opnsense', 'printer-hp']}
        appChips={[]}
        activeHosts={[]}
        activeApps={[]}
        selectableHosts={new Set(['opnsense', 'printer-hp'])}
        selfReportedHosts={{ 'printer-hp': ['172.17.0.4', '172.17.0.5'] }}
        onToggleHost={vi.fn()}
        onToggleApp={vi.fn()}
        bufferSize={10}
        hasSelection={false}
        onSelectAll={vi.fn()}
        onDeselectAll={vi.fn()}
        historicalRange={false}
        autoScroll={true}
        onToggleAutoScroll={vi.fn()}
        onClearBuffer={vi.fn()}
        onDownload={vi.fn()}
        histogramShown={false}
        onToggleHistogram={vi.fn()}
      />
    );
    const [plain, reported] = screen.getAllByTestId('quick-filter-host');
    expect(plain).not.toHaveAttribute('title');
    expect(plain).not.toHaveTextContent('(self-reported)');
    expect(reported).toHaveAttribute('title', 'printer-hp: the name 172.17.0.4, 172.17.0.5 report for themselves (no alias set)');
    expect(reported).toHaveTextContent('(self-reported)');
  });

  it('offers to make a self-reported name the alias in one click, and closes the drawer when saved', async () => {
    const onMakeAlias = vi.fn().mockResolvedValue(undefined);
    const onClose = vi.fn();
    render(
      <LogDetailModal log={reportedLog} isOpen={true} onClose={onClose} onExplainWithAi={vi.fn()} onAddAlias={vi.fn()} onMakeAlias={onMakeAlias} />
    );
    expect(screen.getByTestId('reported-host-note')).toHaveTextContent('printer-hp is the name this device reports for itself.');
    // No alias yet, so the other action adds one rather than modifying it
    expect(screen.getByText('Add Host Alias')).toBeInTheDocument();

    fireEvent.click(screen.getByText('Make it the alias'));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(onMakeAlias).toHaveBeenCalledWith('172.17.0.4', 'printer-hp');
  });

  it('keeps the drawer open with the reason when the alias cannot be saved', async () => {
    const onMakeAlias = vi.fn().mockRejectedValue(new Error('The alias is already used by a Docker host.'));
    const onClose = vi.fn();
    render(<LogDetailModal log={reportedLog} isOpen={true} onClose={onClose} onExplainWithAi={vi.fn()} onMakeAlias={onMakeAlias} />);
    fireEvent.click(screen.getByText('Make it the alias'));
    expect(await screen.findByRole('alert')).toHaveTextContent('already used by a Docker host');
    expect(onClose).not.toHaveBeenCalled();
  });

  it('says which host uses a name the device could not take', () => {
    render(
      <LogDetailModal
        log={refusedLog}
        isOpen={true}
        onClose={vi.fn()}
        onExplainWithAi={vi.fn()}
        hostAliases={{ '192.168.10.20': 'TrueNAS' }}
        onMakeAlias={vi.fn()}
      />
    );
    const note = screen.getByTestId('reported-host-note');
    expect(note).toHaveTextContent('This device calls itself truenas, which is the alias of 192.168.10.20.');
    expect(screen.queryByText('Make it the alias')).toBeNull();
  });

  it('shows no note once the address has an alias', () => {
    render(
      <LogDetailModal log={reportedLog} isOpen={true} onClose={vi.fn()} onExplainWithAi={vi.fn()} hostAliases={{ '172.17.0.4': 'printer' }} />
    );
    expect(screen.queryByTestId('reported-host-note')).toBeNull();
  });

  it('drops the mark when the address gets an alias or another host takes the name', () => {
    expect(prepareLogEntry(reportedLog, { '172.17.0.4': 'printer' }).host_self_reported).toBe(false);

    const aliased = diffSources({}, { '172.17.0.4': 'printer' }, [], [], []).remapRow(reportedLog);
    expect([aliased.source_alias, aliased.host_self_reported]).toEqual(['printer', false]);

    const taken = diffSources({}, { '10.0.0.9': 'Printer-HP' }, [], [], []).remapRow(reportedLog);
    expect([taken.source_alias, taken.host_self_reported]).toEqual(['172.17.0.4', false]);

    const docker = diffSources({}, {}, [], [{ id: 2, source_alias: 'printer-hp' } as DockerEndpoint], []).remapRow(reportedLog);
    expect(docker.source_alias).toBe('172.17.0.4');
  });
});

describe('Self-reported host names after an alias is deleted', () => {
  // A log stored while 172.17.0.4 had the alias office-printer
  const aliasedLog: ProcessedLogEntry = { ...reportedLog, source_alias: 'office-printer', host_self_reported: false };

  it('shows the reported name again, marked, when no configured host uses it', () => {
    const row = diffSources({ '172.17.0.4': 'office-printer' }, {}, [], [], []).remapRow(aliasedLog);
    expect([row.source_alias, row.host_self_reported]).toEqual(['printer-hp', true]);
  });

  it('falls back to the address when another host uses the name', () => {
    const row = diffSources({ '172.17.0.4': 'office-printer' }, { '10.0.0.9': 'printer-hp' }, [], [], []).remapRow(aliasedLog);
    expect([row.source_alias, row.host_self_reported]).toEqual(['172.17.0.4', false]);
  });

  it('falls back to the address for a log with no reported name', () => {
    const row = diffSources({ '172.17.0.4': 'office-printer' }, {}, [], [], []).remapRow({ ...aliasedLog, reported_host: null });
    expect(row.source_alias).toBe('172.17.0.4');
  });
});
