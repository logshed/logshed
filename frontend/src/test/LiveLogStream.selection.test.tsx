// Ticking rows, the keyboard cursor, the log drawer and the actions on selected logs.
import { render, screen, fireEvent, waitFor, act, within } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, type Mock } from 'vitest';
import { LiveLogStream } from '../components/logs/LiveLogStream.tsx';
import { DESKTOP_LOG_ROW_HEIGHT } from '../components/logs/LogRow.tsx';
import * as settingsApi from '../api/settings.ts';
import * as logDetailModalModule from '../components/logs/LogDetailModal.tsx';
import * as logsApi from '../api/logs.ts';
import * as aliasesApi from '../api/aliases.ts';
import * as dockerHostsApi from '../api/dockerHosts.ts';
import { LogEntry } from '../types.ts';
import { AliasContext } from '../context/AliasContext.tsx';
import { takeAlertRuleHandoff } from '../utils/alertRuleHandoff.ts';
import { MockEventSource, virtualizerControl, sampleLogs, renderWithContext, setupConsoleMocks, setupStreamMocks, setupConsoleControlsMocks } from './liveLogStreamSetup.tsx';

vi.mock('@tanstack/react-virtual', async () => (await import('./liveLogStreamVirtualizer.ts')).virtualizerMock);

describe('Console selection and the log drawer', () => {
  setupConsoleMocks();

  it('handles checkbox clicking without bubbling to row detail modal', async () => {
    renderWithContext(<LiveLogStream onDiagnoseAi={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText('Nginx upstream connection timeout')).toBeInTheDocument();
    });

    const logRows = document.querySelectorAll('.log-row');
    const firstRow = logRows[0];
    const checkboxContainer = firstRow.firstElementChild as HTMLElement;

    // Click checkbox container
    fireEvent.click(checkboxContainer);

    // Should select log without opening LogDetailModal
    await waitFor(() => {
      expect(screen.getByText('1 log selected')).toBeInTheDocument();
    });
    expect(screen.queryByText(/Log Record #101/i)).toBeNull();

    // Verify row maintains border-l-2 consistently without shifting
    expect(firstRow).toHaveClass('border-l-2', 'border-l-accent-500');
    expect(logRows[1]).toHaveClass('border-l-2', 'border-l-transparent');

    // Clicking anywhere else on row (e.g. message cell) opens detail modal
    const messageCell = screen.getByText('Nginx upstream connection timeout');
    fireEvent.click(messageCell);

    await waitFor(() => {
      expect(screen.getByText(/Log Record #101/i)).toBeInTheDocument();
    });

    // Test Newer/Older navigation in modal adhering to filtered stream (newest first)
    const prevBtn = screen.getByRole('button', { name: /Older log/i });
    const nextBtn = screen.getByRole('button', { name: /Newer log/i });
    expect(nextBtn).toBeDisabled(); // Row 0 is the newest log
    expect(prevBtn).toBeEnabled();

    // Click Older -> moves back in time to earlier log in row 1 (Log Record #102)
    fireEvent.click(prevBtn);
    await waitFor(() => {
      expect(screen.getByText(/Log Record #102/i)).toBeInTheDocument();
    });

    // Now Newer (forward in time) is enabled
    expect(nextBtn).toBeEnabled();
    fireEvent.click(nextBtn);
    await waitFor(() => {
      expect(screen.getByText(/Log Record #101/i)).toBeInTheDocument();
    });
  });

  it('keeps the log drawer open under the AI dialog it opens', async () => {
    const onDiagnoseAi = vi.fn();
    renderWithContext(<LiveLogStream onDiagnoseAi={onDiagnoseAi} />);
    await waitFor(() => expect(screen.getByText('Nginx upstream connection timeout')).toBeInTheDocument());

    fireEvent.click(screen.getByText('Nginx upstream connection timeout'));
    fireEvent.click(await screen.findByRole('button', { name: /Older log/i }));
    await waitFor(() => expect(screen.getByText(/Log Record #102/i)).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: /Explain with AI/i }));
    expect(onDiagnoseAi).toHaveBeenCalledTimes(1);
    expect(onDiagnoseAi.mock.calls[0][0].map((l: { id: number }) => l.id)).toEqual([102]);
    // Still open on log 102, so closing the AI dialog shows it again without a reopen
    expect(screen.getByText(/Log Record #102/i)).toBeInTheDocument();
  });

  it('performs Shift-Click range selection across multiple hosts without error', async () => {
    renderWithContext(<LiveLogStream onDiagnoseAi={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText('Nginx upstream connection timeout')).toBeInTheDocument();
    });

    const logRows = document.querySelectorAll('.log-row');
    const row0Checkbox = logRows[0].firstElementChild as HTMLElement; // id 101, host: homelab-host
    const row1Checkbox = logRows[1].firstElementChild as HTMLElement; // id 102, host: homelab-host

    // Click row 0 (index 0)
    fireEvent.click(row0Checkbox);
    expect(screen.getByText('1 log selected')).toBeInTheDocument();

    // Shift-click row 1 (index 1)
    fireEvent.click(row1Checkbox, { shiftKey: true });
    expect(screen.getByText('2 logs selected')).toBeInTheDocument();

    // Now shift-click row 3 (index 3) - row 2 is opnsense-router (cross-host selection)
    const row3Checkbox = logRows[3].firstElementChild as HTMLElement; // id 104, host: homelab-host
    fireEvent.click(row3Checkbox, { shiftKey: true });

    // Should select all 4 logs across both hosts without any errors
    await waitFor(() => {
      expect(screen.getByText('4 logs selected')).toBeInTheDocument();
      expect(screen.queryByText(/Range selection contained logs from multiple hosts/i)).toBeNull();
      expect(screen.getByText(/2 hosts/i)).toBeInTheDocument();
    });
  });

  it('supports individual multi-host selection and shows multi-host status in action bar', async () => {
    renderWithContext(<LiveLogStream onDiagnoseAi={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText('Nginx upstream connection timeout')).toBeInTheDocument();
    });

    const logRows = document.querySelectorAll('.log-row');
    const row0Checkbox = logRows[0].firstElementChild as HTMLElement; // id 101, host: homelab-host
    const row2Checkbox = logRows[2].firstElementChild as HTMLElement; // id 103, host: opnsense-router

    // Select row 0 (homelab-host)
    fireEvent.click(row0Checkbox);
    expect(screen.getByText('1 log selected')).toBeInTheDocument();
    expect(screen.getAllByText('homelab-host').length).toBeGreaterThan(0);

    // Select row 2 (opnsense-router)
    fireEvent.click(row2Checkbox);
    expect(screen.getByText('2 logs selected')).toBeInTheDocument();
    expect(screen.getByText(/2 hosts/i)).toBeInTheDocument();
    expect(screen.queryByText(/Cannot select across different hosts/i)).toBeNull();
  });

  it('selects all logs currently in browser buffer with Select All action (capped at 200) and clears with Deselect All', async () => {
    renderWithContext(<LiveLogStream onDiagnoseAi={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText('Nginx upstream connection timeout')).toBeInTheDocument();
    });

    // Locate "Select All" button in the toolbar
    const selectAllBtn = screen.getByRole('button', { name: /^Select All$/i });
    expect(selectAllBtn).toBeInTheDocument();

    // Click "Select All"
    fireEvent.click(selectAllBtn);

    // All 4 logs in sampleLogs should be selected
    await waitFor(() => {
      expect(screen.getByText('4 logs selected')).toBeInTheDocument();
    });

    // Deselect All button should now be available in toolbar and floating action bar
    const deselectAllBtns = screen.getAllByRole('button', { name: /^Deselect All$/i });
    expect(deselectAllBtns.length).toBeGreaterThan(0);

    // Click Deselect All from the floating action bar or toolbar
    fireEvent.click(deselectAllBtns[0]);

    // Selection should be cleared
    await waitFor(() => {
      expect(screen.queryByText('4 logs selected')).toBeNull();
    });
  });

  it('toggles Select All and Deselect All via table header column checkbox', async () => {
    renderWithContext(<LiveLogStream onDiagnoseAi={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText('Nginx upstream connection timeout')).toBeInTheDocument();
    });

    // Find table header toggle button (aria-label "Toggle select all in table")
    const headerToggle = screen.getByRole('button', { name: 'Toggle select all in table' });
    expect(headerToggle).toBeInTheDocument();

    // Click table header toggle to select all
    fireEvent.click(headerToggle);

    await waitFor(() => {
      expect(screen.getByText('4 logs selected')).toBeInTheDocument();
    });

    // Now button aria-label should be "Toggle deselect all in table"
    const headerDeselectToggle = screen.getByRole('button', { name: 'Toggle deselect all in table' });
    expect(headerDeselectToggle).toBeInTheDocument();

    // Click table header toggle to deselect all
    fireEvent.click(headerDeselectToggle);

    await waitFor(() => {
      expect(screen.queryByText('4 logs selected')).toBeNull();
    });
  });

  it('resets selectedLogIds when clearSelectionSignal is incremented', async () => {
    const { rerender } = renderWithContext(<LiveLogStream onDiagnoseAi={vi.fn()} clearSelectionSignal={0} />);

    await waitFor(() => {
      expect(screen.getByText('Nginx upstream connection timeout')).toBeInTheDocument();
    });

    // Select all logs
    const selectAllBtn = screen.getByRole('button', { name: /^select all$/i });
    fireEvent.click(selectAllBtn);

    await waitFor(() => {
      expect(screen.getByText(/selected/i)).toBeInTheDocument();
    });

    // Re-render with incremented clearSelectionSignal
    rerender(<LiveLogStream onDiagnoseAi={vi.fn()} clearSelectionSignal={1} />);

    await waitFor(() => {
      expect(screen.queryByText(/selected/i)).not.toBeInTheDocument();
    });
  });

  it('preserves user modifications in Create Drop Rule modal when a new log arrives', async () => {
    renderWithContext(<LiveLogStream onDiagnoseAi={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText('Nginx upstream connection timeout')).toBeInTheDocument();
    });

    // Click log to open LogDetailModal
    const messageCell = screen.getByText('Nginx upstream connection timeout');
    fireEvent.click(messageCell);

    await waitFor(() => {
      expect(screen.getByText(/Log Record #101/i)).toBeInTheDocument();
    });

    // Click Create Drop Rule button
    const dropRuleBtn = screen.getByRole('button', { name: /Create Drop Rule/i });
    fireEvent.click(dropRuleBtn);

    // Drop rule modal opens
    await waitFor(() => {
      expect(screen.getByText('Create Ingestion Drop Rule')).toBeInTheDocument();
    });

    const ruleNameInput = screen.getByPlaceholderText(/e.g. CRON Chatter or Docker Healthchecks/i);
    fireEvent.change(ruleNameInput, { target: { value: 'Custom Drop Rule Name' } });
    expect(ruleNameInput).toHaveValue('Custom Drop Rule Name');

    // Also toggle regex checkbox
    const regexCheckbox = screen.getByRole('checkbox', { name: /Interpret message pattern as Regular Expression/i });
    fireEvent.click(regexCheckbox);
    expect(regexCheckbox).toBeChecked();

    // Now emit a new incoming SSE log
    const es = MockEventSource.instances[0];
    const newEntry: LogEntry = {
      id: 999,
      timestamp: '2026-09-03T14:45:00.000Z',
      received_at: '2026-09-03T14:45:00.010Z',
      source_ip: '192.168.1.50',
      source_alias: 'homelab-host',
      app_name: 'postfix',
      facility: 2,
      severity: 6,
      message: 'Incoming background mail log',
      raw: 'raw incoming mail log',
    };

    act(() => {
      es.emit('log', newEntry);
    });

    // Stream updates with the new log
    await waitFor(() => {
      expect(screen.getByText('Incoming background mail log')).toBeInTheDocument();
    });

    // The modal must still be open and user modifications must NOT be wiped
    expect(screen.getByText('Create Ingestion Drop Rule')).toBeInTheDocument();
    expect(ruleNameInput).toHaveValue('Custom Drop Rule Name');
    expect(regexCheckbox).toBeChecked();
  });

  describe('keyboard row cursor', () => {
    const loadList = async () => {
      renderWithContext(<LiveLogStream onDiagnoseAi={vi.fn()} />);
      await waitFor(() => {
        expect(screen.getByText('Nginx upstream connection timeout')).toBeInTheDocument();
      });
      const list = screen.getByRole('listbox', { name: 'Log list' });
      list.focus();
      return list;
    };
    const key = (el: HTMLElement, k: string, init: Record<string, unknown> = {}) => fireEvent.keyDown(el, { key: k, ...init });

    it('is one Tab stop whose arrow, page, Home and End keys move a cursor named by aria-activedescendant', async () => {
      virtualizerControl.scrollToIndex.mockClear();
      const list = await loadList();
      expect(list).toHaveAttribute('tabindex', '0');
      expect(list).toHaveAttribute('aria-multiselectable', 'true');
      expect(list).not.toHaveAttribute('aria-activedescendant');
      expect(within(list).getAllByRole('option')).toHaveLength(4);

      key(list, 'ArrowDown'); // the first key lands on the first row in view
      expect(list).toHaveAttribute('aria-activedescendant', 'log-row-101');
      key(list, 'ArrowDown');
      expect(list).toHaveAttribute('aria-activedescendant', 'log-row-102');
      key(list, 'ArrowUp');
      expect(list).toHaveAttribute('aria-activedescendant', 'log-row-101');
      key(list, 'ArrowUp'); // stays on the first row
      expect(list).toHaveAttribute('aria-activedescendant', 'log-row-101');
      key(list, 'End');
      expect(list).toHaveAttribute('aria-activedescendant', 'log-row-104');
      expect(virtualizerControl.scrollToIndex).toHaveBeenLastCalledWith(3, { align: 'auto' });
      key(list, 'ArrowDown'); // stays on the last row
      expect(list).toHaveAttribute('aria-activedescendant', 'log-row-104');
      key(list, 'Home');
      expect(list).toHaveAttribute('aria-activedescendant', 'log-row-101');
      // Four rows tall, so a page moves three rows (one stays in view for context)
      Object.defineProperty(list, 'clientHeight', { configurable: true, value: DESKTOP_LOG_ROW_HEIGHT * 4 });
      key(list, 'PageDown');
      expect(list).toHaveAttribute('aria-activedescendant', 'log-row-104');
      key(list, 'PageUp');
      expect(list).toHaveAttribute('aria-activedescendant', 'log-row-101');

      expect(document.getElementById('log-row-101')!.className).toContain('group-focus/loglist:ring-1');
      expect(document.getElementById('log-row-104')!.className).not.toContain('group-focus/loglist:ring-1');
    });

    it('pauses auto-scroll when the cursor moves, so new live rows do not carry the cursor row away', async () => {
      const list = await loadList();
      expect(screen.getByText('Auto-Scroll ON')).toBeInTheDocument();
      key(list, 'Enter'); // no cursor yet: nothing happens, and the stream keeps following
      expect(screen.getByText('Auto-Scroll ON')).toBeInTheDocument();
      key(list, 'ArrowDown');
      expect(list).toHaveAttribute('aria-activedescendant', 'log-row-101');
      expect(screen.getByText('Paused')).toBeInTheDocument();
      key(list, 'ArrowDown');
      expect(list).toHaveAttribute('aria-activedescendant', 'log-row-102');
    });

    it('ticks the cursor row with Space and a range with Shift+Space', async () => {
      const list = await loadList();
      key(list, 'ArrowDown');
      key(list, ' ');
      expect(screen.getByText('1 log selected')).toBeInTheDocument();
      expect(document.getElementById('log-row-101')).toHaveAttribute('aria-selected', 'true');
      expect(document.getElementById('log-row-102')).toHaveAttribute('aria-selected', 'false');

      key(list, 'ArrowDown');
      key(list, 'ArrowDown');
      key(list, ' ', { shiftKey: true });
      expect(screen.getByText('3 logs selected')).toBeInTheDocument();

      key(list, ' '); // unticks the cursor row
      expect(screen.getByText('2 logs selected')).toBeInTheDocument();
      expect(document.getElementById('log-row-103')).toHaveAttribute('aria-selected', 'false');
    });

    it('opens the cursor row with Enter, and Esc returns focus to the list with the cursor on the last log viewed', async () => {
      const list = await loadList();
      key(list, ' '); // nothing happens without a cursor
      key(list, 'Enter');
      expect(screen.queryByText('1 log selected')).toBeNull();
      expect(screen.queryByText(/Log Record #/i)).toBeNull();

      key(list, 'ArrowDown');
      key(list, 'Enter');
      await waitFor(() => {
        expect(screen.getByText(/Log Record #101/i)).toBeInTheDocument();
      });
      const drawer = screen.getByRole('dialog', { name: /Log Record #101/ });
      expect(drawer.contains(document.activeElement)).toBe(true);

      fireEvent.click(screen.getByRole('button', { name: /Older log/i }));
      await waitFor(() => {
        expect(screen.getByText(/Log Record #102/i)).toBeInTheDocument();
      });

      fireEvent.keyDown(document.activeElement!, { key: 'Escape' });
      await waitFor(() => {
        expect(screen.queryByText(/Log Record #102/i)).toBeNull();
      });
      expect(list).toHaveFocus();
      expect(list).toHaveAttribute('aria-activedescendant', 'log-row-102');
    });

    it('outlines the cursor row only after a key moves the cursor, not after a mouse click', async () => {
      const list = await loadList();
      const ring = 'group-focus/loglist:ring-1';
      // Opening a row with the mouse puts the cursor on it, but draws no outline
      fireEvent.mouseDown(list);
      fireEvent.click(screen.getByText('Nginx upstream connection timeout'));
      const drawer = await screen.findByRole('dialog', { name: /Log Record #101/ });
      fireEvent.keyDown(drawer, { key: 'Escape' });
      await waitFor(() => {
        expect(screen.queryByRole('dialog', { name: /Log Record #101/ })).toBeNull();
      });
      expect(list).toHaveAttribute('aria-activedescendant', 'log-row-101');
      expect(document.getElementById('log-row-101')!.className).not.toContain(ring);
      // Ticking rows with the mouse draws none either
      fireEvent.mouseDown(list);
      fireEvent.click(within(document.getElementById('log-row-102')!).getByTestId('log-row-checkbox'));
      expect(document.getElementById('log-row-101')!.className).not.toContain(ring);
      expect(document.getElementById('log-row-102')!.className).not.toContain(ring);

      key(list, 'ArrowDown');
      expect(document.getElementById('log-row-102')!.className).toContain(ring);
      fireEvent.mouseDown(list);
      expect(document.getElementById('log-row-102')!.className).not.toContain(ring);
    });

    it('leaves keys pressed with Ctrl, Meta or Alt alone', async () => {
      const list = await loadList();
      key(list, 'ArrowDown', { ctrlKey: true });
      key(list, 'End', { metaKey: true });
      key(list, 'ArrowDown', { altKey: true });
      expect(list).not.toHaveAttribute('aria-activedescendant');
    });

    it('is a plain region while there are no rows', async () => {
      (logsApi.fetchLogs as Mock).mockResolvedValue({ logs: [], total: 0, limit: 500, offset: 0 });
      renderWithContext(<LiveLogStream onDiagnoseAi={vi.fn()} />);
      const region = await screen.findByRole('region', { name: 'Log list' });
      expect(screen.queryByRole('listbox')).toBeNull();
      fireEvent.keyDown(region, { key: 'ArrowDown' });
      expect(region).not.toHaveAttribute('aria-activedescendant');
    });
  });
});

describe('Console drawer', () => {
  const { renderStream } = setupStreamMocks();

  it('does not render the log detail panel while it is closed', async () => {
    const modalSpy = vi.spyOn(logDetailModalModule, 'LogDetailModal');
    renderStream();
    await waitFor(() => expect(screen.getByText('Nginx upstream connection timeout')).toBeInTheDocument());
    const es = MockEventSource.instances[MockEventSource.instances.length - 1];
    act(() => es.emit('log', { ...sampleLogs[0], id: 950, message: 'Fresh row' }));
    await waitFor(() => expect(screen.getByText('Fresh row')).toBeInTheDocument());
    expect(modalSpy).not.toHaveBeenCalled();

    fireEvent.click(screen.getByText('Fresh row'));
    await screen.findByText(/Log Record #950/i);
    expect(modalSpy).toHaveBeenCalled();
  });
});

describe('Console selection, AI and drawer controls', () => {
  const { renderStream, mockSettings, waitForRows, setMobile } = setupConsoleControlsMocks();

  describe('AI controls', () => {
    it('hides row AI buttons, the actions column and "Run Analysis" when AI is off', async () => {
      mockSettings({ ai_enabled: false, has_ai_api_key: true });
      renderStream();
      await waitForRows();
      await act(async () => {});
      expect(screen.queryAllByTitle('Explain with AI')).toHaveLength(0);
      const header = screen.getByTestId('log-table-header');
      expect(within(header).queryByText('ACTIONS')).toBeNull();
      expect(header.className).toContain('grid-cols-[36px_120px_65px_130px_130px_1fr]');
      expect(header.className).not.toContain('_60px]');

      fireEvent.click(document.querySelectorAll('.log-row')[0].firstElementChild as HTMLElement);
      await screen.findByText('1 log selected');
      expect(screen.getByRole('button', { name: 'Delete (1)' })).toBeInTheDocument();
      expect(screen.queryByRole('button', { name: /Run Analysis/i })).toBeNull();
    });

    it('shows row AI buttons, the actions column and "Run Analysis" when AI is on', async () => {
      // No ai_enabled field: a stored API key turns AI on, the same rule as Settings
      mockSettings({ has_ai_api_key: true });
      const onDiagnoseAi = vi.fn();
      renderStream({ onDiagnoseAi });
      await waitForRows();
      await waitFor(() => expect(screen.getAllByTitle('Explain with AI').length).toBe(sampleLogs.length));
      const header = screen.getByTestId('log-table-header');
      expect(within(header).getByText('ACTIONS')).toBeInTheDocument();
      expect(header.className).toContain('grid-cols-[36px_120px_65px_130px_130px_1fr_60px]');

      fireEvent.click(document.querySelectorAll('.log-row')[0].firstElementChild as HTMLElement);
      fireEvent.click(await screen.findByRole('button', { name: 'Run Analysis (1)' }));
      expect(onDiagnoseAi).toHaveBeenCalledWith([expect.objectContaining({ id: 101 })]);
      expect(screen.getByRole('button', { name: 'Delete (1)' })).toBeInTheDocument();
    });

    it('treats AI as off until settings have loaded', async () => {
      vi.spyOn(settingsApi, 'fetchSettings').mockImplementation(() => new Promise(() => {}));
      renderStream();
      await waitForRows();
      expect(screen.queryAllByTitle('Explain with AI')).toHaveLength(0);
    });

    it('treats AI as off when settings fail to load', async () => {
      vi.spyOn(settingsApi, 'fetchSettings').mockRejectedValue(new Error('offline'));
      renderStream();
      await waitForRows();
      await act(async () => {});
      expect(screen.queryAllByTitle('Explain with AI')).toHaveLength(0);
    });

    it('hides the mobile card AI button when AI is off and shows it when AI is on', async () => {
      setMobile(true);
      mockSettings({ ai_enabled: false });
      const { unmount } = renderStream();
      await waitForRows();
      await act(async () => {});
      expect(screen.queryAllByTitle('Explain with AI')).toHaveLength(0);
      unmount();

      mockSettings({ ai_enabled: true });
      renderStream();
      await waitForRows();
      await waitFor(() => expect(screen.getAllByTitle('Explain with AI').length).toBe(sampleLogs.length));
    });

    it('passes the AI setting to the drawer: muted "Explain with AI" and no context AI action when off', async () => {
      mockSettings({ ai_enabled: false });
      const onDiagnoseAi = vi.fn();
      renderStream({ onDiagnoseAi });
      await waitForRows();
      await act(async () => {});
      fireEvent.click(screen.getByText('Nginx upstream connection timeout'));
      const btn = await screen.findByRole('button', { name: /Explain with AI/i });
      expect(btn).toHaveAttribute('title', 'AI features are turned off in Settings');
      // Still opens the AI modal, which explains how to turn AI on
      fireEvent.click(btn);
      expect(onDiagnoseAi).toHaveBeenCalledWith([expect.objectContaining({ id: 101 })]);
    });

    it('passes the AI setting to the drawer: primary "Explain with AI" when on', async () => {
      mockSettings({ ai_enabled: true });
      renderStream();
      await waitForRows();
      await waitFor(() => expect(screen.getAllByTitle('Explain with AI').length).toBeGreaterThan(0));
      fireEvent.click(screen.getByText('Nginx upstream connection timeout'));
      await screen.findByText(/Log Record #101/i);
      // Row buttons are icon-only; the drawer's button is the one that shows the label
      const drawerBtns = screen
        .getAllByRole('button', { name: /^Explain with AI$/ })
        .filter((b) => b.textContent === 'Explain with AI');
      expect(drawerBtns).toHaveLength(1);
      expect(drawerBtns[0]).not.toHaveAttribute('title');
    });
  });

  describe('drawer Newer / Older', () => {
    it('steps to the row below with Older and the row above with Newer, by button and key', async () => {
      mockSettings({});
      renderStream();
      await waitForRows();
      fireEvent.click(screen.getByText('Postgres slow query detected'));
      await screen.findByText(/Log Record #102/i);

      fireEvent.click(screen.getByRole('button', { name: 'Older log' }));
      await screen.findByText(/Log Record #103/i);
      fireEvent.click(screen.getByRole('button', { name: 'Newer log' }));
      await screen.findByText(/Log Record #102/i);

      fireEvent.keyDown(document.body, { key: 'ArrowUp' });
      await screen.findByText(/Log Record #101/i);
      expect(screen.getByRole('button', { name: 'Newer log' })).toBeDisabled();
      fireEvent.keyDown(document.body, { key: 'k' });
      expect(screen.getByText(/Log Record #101/i)).toBeInTheDocument();

      fireEvent.keyDown(document.body, { key: 'j' });
      await screen.findByText(/Log Record #102/i);
      fireEvent.keyDown(document.body, { key: 'ArrowDown' });
      await screen.findByText(/Log Record #103/i);
      fireEvent.keyDown(document.body, { key: 'j' });
      await screen.findByText(/Log Record #104/i);
      expect(screen.getByRole('button', { name: 'Older log' })).toBeDisabled();
    });

    it('ignores the keys while typing in the search box', async () => {
      mockSettings({});
      renderStream();
      await waitForRows();
      fireEvent.click(screen.getByText('Postgres slow query detected'));
      await screen.findByText(/Log Record #102/i);
      const search = document.querySelector('input[type="text"], input:not([type])') as HTMLInputElement;
      expect(search).not.toBeNull();
      fireEvent.keyDown(search, { key: 'j' });
      fireEvent.keyDown(search, { key: 'ArrowUp' });
      expect(screen.getByText(/Log Record #102/i)).toBeInTheDocument();
    });
  });

  it('prefills Create Drop Rule with the host alias rather than the IP', async () => {
    mockSettings({});
    renderStream();
    await waitForRows();
    fireEvent.click(screen.getByText('Nginx upstream connection timeout'));
    fireEvent.click(await screen.findByRole('button', { name: /Create Drop Rule/i }));
    // The dialog is loaded on first use
    const dialog = await screen.findByRole('dialog', { name: /Drop Rule/i });
    // The Host field shows the chosen host
    expect(await within(dialog).findByText('homelab-host')).toBeInTheDocument();
    expect(within(dialog).queryByText('192.168.1.50')).toBeNull();
  });

  it('switches Select All at the same 768px width as the mobile cards', async () => {
    mockSettings({});
    renderStream();
    await waitForRows();
    const group = screen.getByTestId('select-all-group');
    expect(group.className).toContain('hidden md:flex');
    expect(group.className).not.toContain('sm:flex');
    expect(within(group).getByRole('button', { name: 'Select All' })).toBeInTheDocument();
  });
});

describe('LiveLogStream "Create Alert Rule" hand-off', () => {
  const renderStream = () =>
    render(
      <AliasContext.Provider value={{ aliasVersion: 0, bumpAliasVersion: vi.fn() }}>
        <LiveLogStream onDiagnoseAi={vi.fn()} />
      </AliasContext.Provider>
    );

  beforeEach(() => {
    window.history.replaceState(null, '', '/?q=nginx');
    vi.restoreAllMocks();
    MockEventSource.instances = [];
    virtualizerControl.edgeRows = null;
    virtualizerControl.headOnly = false;
    vi.spyOn(logsApi, 'fetchLogs').mockResolvedValue({ logs: [...sampleLogs], total: sampleLogs.length, limit: 500, offset: 0 });
    vi.spyOn(logsApi, 'fetchLogFacets').mockResolvedValue({ sources: [], apps: [], host_to_apps: {}, app_to_hosts: {} } as any);
    vi.spyOn(aliasesApi, 'fetchAliases').mockResolvedValue([]);
    vi.spyOn(dockerHostsApi, 'fetchDockerEndpoints').mockResolvedValue([]);
    vi.spyOn(settingsApi, 'fetchSettings').mockResolvedValue({} as any);
  });

  it('closes the drawer, stores the app and message in memory and switches to Alert Rules without URL params', async () => {
    const popstate = vi.fn();
    window.addEventListener('popstate', popstate);
    renderStream();
    fireEvent.click(await screen.findByText('Nginx upstream connection timeout'));
    fireEvent.click(await screen.findByRole('button', { name: /Create Alert Rule/i }));
    window.removeEventListener('popstate', popstate);

    expect(popstate).toHaveBeenCalled();
    expect(window.location.pathname).toBe('/rules/rules');
    expect(window.location.search).toBe('');
    expect(takeAlertRuleHandoff()).toEqual({ appName: 'nginx', message: 'Nginx upstream connection timeout' });
    // Read once only
    expect(takeAlertRuleHandoff()).toBeNull();
    await waitFor(() => expect(screen.queryByRole('button', { name: /Create Alert Rule/i })).toBeNull());
    window.history.replaceState(null, '', '/');
  });
});
