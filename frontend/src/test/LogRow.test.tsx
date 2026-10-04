import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { LogRow, areLogRowPropsEqual, ProcessedLogEntry } from '../components/logs/LogRow.tsx';
import { matchesSearchQuery, prepareLogEntry } from '../components/logs/LiveLogStream.tsx';

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
    strippedMessage: 'Upstream timeout error',
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

  it('custom comparison function areLogRowPropsEqual handles id, selection, hover, and style', () => {
    const defaultProps = {
      log: baseLog,
      index: 0,
      isSelected: false,
      isHovered: false,
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

    // Changed hover status should return false
    expect(areLogRowPropsEqual(defaultProps, { ...defaultProps, isHovered: true })).toBe(false);

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
    expect(prepared.strippedMessage).toBe('Slow query detected');
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

  it('matchesSearchQuery supports precompiled RegExp pattern', () => {
    const precompiled = new RegExp('upstream', 'i');
    expect(matchesSearchQuery(baseLog, 'upstream', precompiled)).toBe(true);
    expect(matchesSearchQuery(baseLog, 'redis', precompiled)).toBe(true); // precompiled matches 'Upstream timeout error'
    const nonMatching = new RegExp('nonexistent_keyword', 'i');
    expect(matchesSearchQuery(baseLog, 'nonexistent_keyword', nonMatching)).toBe(false);
  });
});
