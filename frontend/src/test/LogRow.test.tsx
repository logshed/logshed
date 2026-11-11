import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { LogRow, areLogRowPropsEqual, ProcessedLogEntry, LOG_GRID_COLS_WITH_AI, LOG_GRID_COLS_WITHOUT_AI } from '../components/logs/LogRow.tsx';
import {
  buildDockerAliasSet,
  matchesSearchQuery,
  prepareLogEntry,
  reapplyAliases,
  restoreDockerAliases,
} from '../components/logs/LiveLogStream.tsx';

describe('LogRow Component and Optimization', () => {
  const baseLog: ProcessedLogEntry = {
    id: 101,
    timestamp: '2026-09-03T14:30:15.123Z',
    received_at: '2026-09-03T14:30:15.150Z',
    source_ip: '192.168.1.50',
    source_alias: 'homelab-host',
    app_name: 'nginx',
    facility: 1,
    severity: 3,
    message: '\u001b[31mUpstream timeout error\u001b[0m',
    raw: '<11>1 2026-09-03T14:30:15.123Z homelab-host nginx - - - Upstream timeout error',
    formattedTimestamp: '14:30:15.123',
    cleanedMessage: 'Upstream timeout error',
  };

  it('renders desktop view with precomputed fields and responds to interactions', () => {
    const onSelect = vi.fn();
    const onClick = vi.fn();
    const onDiagnoseAi = vi.fn();

    render(
      <LogRow
        log={baseLog}
        index={0}
        isSelected={false}
        isMobile={false}
        aiEnabled={true}
        style={{ height: '28px', transform: 'translateY(0px)' }}
        onSelect={onSelect}
        onClick={onClick}
        onDiagnoseAi={onDiagnoseAi}
      />
    );

    expect(screen.getByText('14:30:15.123')).toBeInTheDocument();
    expect(screen.getByText('Upstream timeout error')).toBeInTheDocument();
    expect(screen.getByText('homelab-host')).toBeInTheDocument();
    expect(screen.getByText('nginx')).toBeInTheDocument();

    // No hover tooltip repeating the message: clicking the row shows it in full
    expect(screen.getByText('Upstream timeout error')).not.toHaveAttribute('title');

    // Check row click triggers onClick
    const row = screen.getByText('Upstream timeout error').closest('.log-row');
    expect(row).toBeInTheDocument();
    fireEvent.click(row!);
    expect(onClick).toHaveBeenCalledWith(baseLog);

    // Check AI button click
    const aiBtn = screen.getByTitle('Explain with AI');
    fireEvent.click(aiBtn);
    expect(onDiagnoseAi).toHaveBeenCalledWith([baseLog]);
  });

  it('renders mobile view with 3-line triage card', () => {
    const onSelect = vi.fn();
    const onClick = vi.fn();
    const onDiagnoseAi = vi.fn();

    render(
      <LogRow
        log={baseLog}
        index={1}
        isSelected={true}
        isMobile={true}
        aiEnabled={true}
        style={{ height: '74px', transform: 'translateY(74px)' }}
        onSelect={onSelect}
        onClick={onClick}
        onDiagnoseAi={onDiagnoseAi}
      />
    );

    expect(screen.getByText('14:30:15.123')).toBeInTheDocument();
    expect(screen.getByText('Upstream timeout error')).toBeInTheDocument();
    expect(screen.getByText('(homelab-host)')).toBeInTheDocument();

    // AI button in mobile
    const aiBtn = screen.getByTitle('Explain with AI');
    fireEvent.click(aiBtn);
    expect(onDiagnoseAi).toHaveBeenCalledWith([baseLog]);
  });

  it('gives phone cards a visible divider and leaves desktop rows as they were', () => {
    const props = { log: baseLog, index: 0, isSelected: false, style: {}, onSelect: vi.fn(), onClick: vi.fn(), onDiagnoseAi: vi.fn() };
    const { rerender } = render(<LogRow {...props} isMobile={true} />);
    const card = screen.getByRole('option');
    // dark-900 on the dark-950 page cannot be seen; dark-700 matches the toolbar dividers above the list
    expect(card).toHaveClass('border-b', 'border-dark-700');
    expect(card).not.toHaveClass('border-dark-900');

    rerender(<LogRow {...props} isMobile={false} />);
    expect(screen.getByRole('option')).toHaveClass('border-b', 'border-dark-900');
  });

  it('brightens a selected or alert row on hover instead of dropping its highlight', () => {
    const props = { log: baseLog, index: 0, isMobile: false, style: {}, onSelect: vi.fn(), onClick: vi.fn(), onDiagnoseAi: vi.fn() };
    const { rerender } = render(<LogRow {...props} isSelected={false} />);
    const row = () => screen.getByRole('option');
    // dark-900 at 60% on the dark-950 list could hardly be seen; dark-800 and a grey left edge can,
    // and grey stays apart from the blue of a selected row
    expect(row().className).toContain('hover:bg-dark-800');
    expect(row().className).toContain('hover:border-l-slate-500');

    rerender(<LogRow {...props} isSelected={true} />);
    expect(row().className).toContain('bg-accent-950/40');
    expect(row().className).toContain('hover:bg-accent-900/50');
    expect(row().className).not.toContain('hover:bg-dark-800');
    expect(row().className).not.toContain('hover:border-l-slate-500');

    rerender(<LogRow {...props} isSelected={true} isFocused={true} />);
    expect(row().className).toContain('bg-amber-950/50');
    expect(row().className).toContain('hover:bg-amber-900/50');
    expect(row().className).not.toContain('hover:bg-dark-800');
    expect(row().className).not.toContain('hover:border-l-slate-500');
  });

  it('custom comparison function areLogRowPropsEqual handles id, selection and style', () => {
    const defaultProps = {
      log: baseLog,
      index: 0,
      isSelected: false,
      isMobile: false,
      style: { height: '28px', transform: 'translateY(0px)' },
      onSelect: vi.fn(),
      onClick: vi.fn(),
      onDiagnoseAi: vi.fn(),
    };

    // Identical props should return true (skip re-render)
    expect(areLogRowPropsEqual(defaultProps, { ...defaultProps })).toBe(true);

    // Changed selection status should return false
    expect(areLogRowPropsEqual(defaultProps, { ...defaultProps, isSelected: true })).toBe(false);

    // Changed id should return false
    expect(
      areLogRowPropsEqual(defaultProps, { ...defaultProps, log: { ...baseLog, id: 999 } })
    ).toBe(false);

    // Changed transform (virtual scroll movement) should return false
    expect(
      areLogRowPropsEqual(defaultProps, {
        ...defaultProps,
        style: { height: '28px', transform: 'translateY(28px)' },
      })
    ).toBe(false);

    // Changed mobile mode should return false
    expect(areLogRowPropsEqual(defaultProps, { ...defaultProps, isMobile: true })).toBe(false);
  });

  it('prepareLogEntry attaches precomputed fields to raw log entry', () => {
    const rawLog = {
      id: 200,
      timestamp: '2026-09-03T14:30:15.123Z',
      received_at: '2026-09-03T14:30:15.150Z',
      source_ip: '10.0.0.1',
      source_alias: 'db-server',
      app_name: 'postgres',
      facility: 1,
      severity: 4,
      message: '\u001b[33mSlow query detected\u001b[0m',
      raw: 'raw log line',
    };

    const prepared = prepareLogEntry(rawLog);
    expect(prepared.formattedTimestamp).toBeDefined();
    expect(prepared.cleanedMessage).toBe('Slow query detected');
  });

  it('prepareLogEntry keeps the alias of a former Docker host instead of the syslog alias of its address', () => {
    const containerLog = {
      id: 202,
      timestamp: '2026-11-03T10:00:00.000Z',
      received_at: '2026-11-03T10:00:00.000Z',
      source_ip: '192.168.1.60',
      source_alias: 'nas-docker',
      app_name: 'plex',
      facility: 1,
      severity: 6,
      message: 'Container started',
      raw: 'Container started',
    };
    const aliases = { '192.168.1.60': 'nas' };

    expect(prepareLogEntry(containerLog, aliases, undefined, buildDockerAliasSet([], [])).source_alias).toBe('nas');
    const dockerAliases = buildDockerAliasSet([], ['nas-docker']);
    expect(prepareLogEntry(containerLog, aliases, undefined, dockerAliases).source_alias).toBe('nas-docker');
  });

  it('restoreDockerAliases gives rows prepared before the Docker aliases loaded their stored alias back', () => {
    const base = {
      timestamp: '2026-11-03T10:00:00.000Z',
      received_at: '2026-11-03T10:00:00.000Z',
      source_ip: '192.168.1.60',
      facility: 1,
      severity: 6,
      raw: 'line',
    };
    const aliases = { '192.168.1.60': 'nas' };
    // Prepared while the Docker alias set was still empty: both rows show the syslog alias
    const rows = [
      prepareLogEntry({ ...base, id: 1, source_alias: 'nas-docker', app_name: 'plex', message: 'container' }, aliases, undefined, new Set()),
      prepareLogEntry({ ...base, id: 2, source_alias: '192.168.1.60', app_name: 'sshd', message: 'syslog' }, aliases, undefined, new Set()),
    ];
    expect(rows.map((r) => r.source_alias)).toEqual(['nas', 'nas']);

    const restored = restoreDockerAliases(rows, buildDockerAliasSet([], ['nas-docker']));
    expect(restored.map((r) => r.source_alias)).toEqual(['nas-docker', 'nas']);
    expect(restored[1]).toBe(rows[1]);
    // Nothing to change: the same array comes back, so React skips the update
    expect(restoreDockerAliases(restored, buildDockerAliasSet([], ['nas-docker']))).toBe(restored);
    // Preparing a prepared row again reads its stored alias, not the shown one
    expect(prepareLogEntry(rows[0], aliases, undefined, buildDockerAliasSet([], ['nas-docker'])).source_alias).toBe('nas-docker');
  });

  it('reapplyAliases gives rows prepared before the alias list loaded their host alias', () => {
    const base = {
      timestamp: '2026-11-03T10:00:00.000Z',
      received_at: '2026-11-03T10:00:00.000Z',
      source_ip: '192.168.1.60',
      facility: 1,
      severity: 6,
      raw: 'line',
    };
    // Prepared with no alias list and no Docker hosts yet: both rows show what was stored
    const rows = [
      prepareLogEntry({ ...base, id: 1, source_alias: 'nas-docker', app_name: 'plex', message: 'container' }, {}, undefined, new Set()),
      prepareLogEntry({ ...base, id: 2, source_alias: 'nas-hostname', app_name: 'sshd', message: 'syslog' }, {}, undefined, new Set()),
    ];
    expect(rows.map((r) => r.source_alias)).toEqual(['nas-docker', 'nas-hostname']);

    const aliases = { '192.168.1.60': 'nas' };
    const dockerAliases = buildDockerAliasSet([], ['nas-docker']);
    const realiased = reapplyAliases(rows, aliases, dockerAliases);
    // The syslog row takes its address's alias; the Docker row keeps its own
    expect(realiased.map((r) => r.source_alias)).toEqual(['nas-docker', 'nas']);
    expect(realiased[0]).toBe(rows[0]);
    expect(realiased[1].storedSourceAlias).toBe('nas-hostname');
    expect(realiased[1].formattedTimestamp).toBe(rows[1].formattedTimestamp);
    // Nothing to change: the same array comes back, so React skips the update
    expect(reapplyAliases(realiased, aliases, dockerAliases)).toBe(realiased);
  });

  it('prepareLogEntry canonicalizes host alias upon ingestion when aliases are supplied', () => {
    const rawLog = {
      id: 201,
      timestamp: '2026-09-03T14:30:15.123Z',
      received_at: '2026-09-03T14:30:15.150Z',
      source_ip: '10.0.0.1',
      source_alias: '10.0.0.1',
      app_name: 'postgres',
      facility: 1,
      severity: 4,
      message: 'query finished',
      raw: 'raw log line',
    };

    // 1. IP match takes precedence
    const aliasedByIp = prepareLogEntry(rawLog, { '10.0.0.1': 'db-primary' });
    expect(aliasedByIp.source_alias).toBe('db-primary');

    // 2. Existing alias rename match
    const aliasedByName = prepareLogEntry(
      { ...rawLog, source_ip: '', source_alias: 'old-gateway' },
      { 'old-gateway': 'edge-router' }
    );
    expect(aliasedByName.source_alias).toBe('edge-router');

    // 3. Fallback to existing alias when not in mapping
    const unaliased = prepareLogEntry(rawLog, { '192.168.1.1': 'gateway' });
    expect(unaliased.source_alias).toBe('10.0.0.1');
  });

  it('prepareLogEntry keeps Docker rows on their alias and matches Docker aliases exactly', () => {
    const base = {
      id: 202,
      timestamp: '2026-09-03T14:30:15.123Z',
      received_at: '2026-09-03T14:30:15.150Z',
      source_ip: '192.168.1.50',
      app_name: 'plex',
      facility: 1,
      severity: 6,
      message: 'started',
      raw: 'started',
    };
    const aliases = { '192.168.1.50': 'Tower NAS' };
    const dockerAliases = new Set(['tower']);

    // Container row from the Docker host on the same machine keeps its Docker alias
    const container = prepareLogEntry({ ...base, source_alias: 'tower' }, aliases, undefined, dockerAliases);
    expect(container.source_alias).toBe('tower');

    // Syslog row stamped with the machine's hostname, spelled differently from the Docker alias
    const syslog = prepareLogEntry({ ...base, source_alias: 'Tower' }, aliases, undefined, dockerAliases);
    expect(syslog.source_alias).toBe('Tower NAS');
  });

  it('matchesSearchQuery supports precompiled RegExp pattern', () => {
    const precompiled = new RegExp('upstream', 'i');
    expect(matchesSearchQuery(baseLog, 'upstream', precompiled)).toBe(true);
    expect(matchesSearchQuery(baseLog, 'redis', precompiled)).toBe(true); // precompiled matches 'Upstream timeout error'
    const nonMatching = new RegExp('nonexistent_keyword', 'i');
    expect(matchesSearchQuery(baseLog, 'nonexistent_keyword', nonMatching)).toBe(false);
  });
});

describe('LogRow AI controls follow the AI setting', () => {
  const log: ProcessedLogEntry = {
    id: 301,
    timestamp: '2026-09-03T14:30:15.123Z',
    received_at: '2026-09-03T14:30:15.150Z',
    source_ip: '192.168.1.50',
    source_alias: 'homelab-host',
    app_name: 'nginx',
    facility: 1,
    severity: 3,
    message: 'Upstream timeout error',
    raw: 'Upstream timeout error',
    formattedTimestamp: '14:30:15.123',
    cleanedMessage: 'Upstream timeout error',
  };

  const renderRow = (isMobile: boolean, aiEnabled?: boolean) =>
    render(
      <LogRow
        log={log}
        index={0}
        isSelected={false}
        isMobile={isMobile}
        aiEnabled={aiEnabled}
        style={{ height: isMobile ? '74px' : '28px' }}
        onSelect={vi.fn()}
        onClick={vi.fn()}
        onDiagnoseAi={vi.fn()}
      />
    );

  it('hides the desktop AI button and drops the actions column when AI is off', () => {
    const { container } = renderRow(false, false);
    expect(screen.queryByTitle('Explain with AI')).toBeNull();
    const row = container.querySelector('.log-row')!;
    expect(row.className).toContain(LOG_GRID_COLS_WITHOUT_AI);
    expect(row.className).not.toContain(LOG_GRID_COLS_WITH_AI);
    expect(row.children.length).toBe(6);
  });

  it('treats a missing aiEnabled prop as AI off', () => {
    renderRow(false);
    expect(screen.queryByTitle('Explain with AI')).toBeNull();
  });

  it('shows the desktop AI button and actions column when AI is on', () => {
    const { container } = renderRow(false, true);
    expect(screen.getByTitle('Explain with AI')).toBeInTheDocument();
    const row = container.querySelector('.log-row')!;
    expect(row.className).toContain(LOG_GRID_COLS_WITH_AI);
    expect(row.children.length).toBe(7);
  });

  it.each([
    ['desktop row', false],
    ['mobile card', true],
  ])('with AI on, the %s holds no control inside its list option, and its AI target works by pointer', (_label, isMobile) => {
    const onClick = vi.fn();
    const onDiagnoseAi = vi.fn();
    const { container } = render(
      <LogRow
        log={log}
        index={0}
        isSelected={false}
        isMobile={isMobile}
        aiEnabled={true}
        style={{ height: isMobile ? '74px' : '28px' }}
        onSelect={vi.fn()}
        onClick={onClick}
        onDiagnoseAi={onDiagnoseAi}
      />
    );
    const option = screen.getByRole('option');
    expect(option.querySelectorAll('button, a, input, [tabindex], [role="button"]')).toHaveLength(0);
    expect(screen.queryByRole('button', { hidden: false })).toBeNull();

    const target = screen.getByTitle('Explain with AI');
    expect(target.closest('[aria-hidden="true"]')).not.toBeNull();
    fireEvent.click(target);
    expect(onDiagnoseAi).toHaveBeenCalledWith([log]);
    expect(onClick).not.toHaveBeenCalled();
    expect(container.querySelector('.log-row')).toBe(option);
  });

  it('hides the mobile card AI button when AI is off', () => {
    renderRow(true, false);
    expect(screen.queryByTitle('Explain with AI')).toBeNull();
    expect(screen.queryByText('AI')).toBeNull();
  });

  it('shows a 32px tall mobile card AI button when AI is on', () => {
    renderRow(true, true);
    const btn = screen.getByTitle('Explain with AI');
    expect(btn.className).toContain('min-h-8');
  });

  it('re-renders a memoized row when the AI setting changes', () => {
    const props = {
      log,
      index: 0,
      isSelected: false,
      isMobile: false,
      style: { height: '28px' },
      onSelect: vi.fn(),
      onClick: vi.fn(),
      onDiagnoseAi: vi.fn(),
    };
    expect(areLogRowPropsEqual({ ...props, aiEnabled: false }, { ...props, aiEnabled: true })).toBe(false);
    expect(areLogRowPropsEqual({ ...props, aiEnabled: true }, { ...props, aiEnabled: true })).toBe(true);
  });
});
