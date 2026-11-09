import { useState } from 'react';
import { render, screen, fireEvent, act } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { SelectDropdown, SelectOption } from '../components/common/SelectDropdown.tsx';
import { SlideOver } from '../components/common/SlideOver.tsx';
import { Modal } from '../components/common/Modal.tsx';

const OPTIONS: SelectOption[] = [
  { value: 'all', label: 'All Time' },
  { value: '15m', label: 'Last 15m' },
  { value: '1h', label: 'Last 1h' },
  { value: 'since_midnight', label: 'Today' },
  { value: 'custom', label: 'Custom...' },
];

function Harness({ initial = 'all', onChange = vi.fn(), onClear }: { initial?: string; onChange?: (v: string) => void; onClear?: () => void }) {
  const [value, setValue] = useState(initial);
  return (
    <>
      <SelectDropdown
        label="Time"
        options={OPTIONS}
        value={value}
        onChange={(v) => {
          onChange(v);
          setValue(v);
        }}
        active={value !== 'all'}
        onClear={onClear ?? (() => setValue('all'))}
        clearLabel="Clear time filter"
      />
      <button type="button">After</button>
    </>
  );
}

const trigger = () => screen.getByRole('button', { name: /^Time: / });
const listbox = () => screen.getByRole('listbox');
const activeLabel = () => document.getElementById(listbox().getAttribute('aria-activedescendant')!)?.textContent;
const key = (k: string, init: Partial<KeyboardEventInit> = {}) => fireEvent.keyDown(document.activeElement!, { key: k, ...init });

describe('SelectDropdown', () => {
  it('is a button named by its label and value that announces a listbox', () => {
    render(<Harness initial="1h" />);
    const t = screen.getByRole('button', { name: 'Time: Last 1h' });
    expect(t.tagName).toBe('BUTTON');
    expect(t).toHaveAttribute('aria-haspopup', 'listbox');
    expect(t).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByRole('listbox')).toBeNull();
  });

  it('shows an unset value in the dimmer text of the Host and App dropdowns, and a set one in the accent', () => {
    render(<Harness />);
    expect(trigger().querySelector('[id$="-value"]')!.className).toContain('text-slate-400');
    fireEvent.click(trigger());
    fireEvent.click(screen.getByRole('option', { name: 'Last 1h' }));
    expect(trigger().querySelector('[id$="-value"]')!.className).toContain('text-accent-300');
  });

  it('opens on a click with the chosen row active and selected, and closes on choosing another with the mouse', () => {
    const onChange = vi.fn();
    render(<Harness initial="1h" onChange={onChange} />);
    fireEvent.click(trigger());
    expect(trigger()).toHaveAttribute('aria-expanded', 'true');
    expect(listbox()).toHaveFocus();
    expect(activeLabel()).toBe('Last 1h');
    expect(screen.getByRole('option', { name: 'Last 1h' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('option', { name: 'Today' })).toHaveAttribute('aria-selected', 'false');
    // No checkboxes, search box or Select All: a plain list
    expect(screen.queryByRole('textbox')).toBeNull();
    expect(screen.queryByText('Select All')).toBeNull();

    fireEvent.click(screen.getByRole('option', { name: 'Today' }));
    expect(onChange).toHaveBeenCalledWith('since_midnight');
    expect(screen.queryByRole('listbox')).toBeNull();
    expect(trigger()).toHaveFocus();
    expect(trigger()).toHaveAccessibleName('Time: Today');
  });

  it.each([['Enter'], [' '], ['ArrowDown'], ['ArrowUp']])('opens with %j on the trigger', (k) => {
    render(<Harness initial="15m" />);
    trigger().focus();
    key(k);
    expect(listbox()).toHaveFocus();
    expect(activeLabel()).toBe('Last 15m');
  });

  it('opens with Alt+Down', () => {
    render(<Harness />);
    trigger().focus();
    key('ArrowDown', { altKey: true });
    expect(listbox()).toHaveFocus();
    expect(activeLabel()).toBe('All Time');
  });

  it('moves with Up, Down, Home and End, and stops at either end', () => {
    render(<Harness />);
    fireEvent.click(trigger());
    key('ArrowUp');
    expect(activeLabel()).toBe('All Time');
    key('ArrowDown');
    key('ArrowDown');
    expect(activeLabel()).toBe('Last 1h');
    key('End');
    expect(activeLabel()).toBe('Custom...');
    key('ArrowDown');
    expect(activeLabel()).toBe('Custom...');
    key('Home');
    expect(activeLabel()).toBe('All Time');
  });

  it('jumps to the next row starting with a typed letter, wrapping round', () => {
    render(<Harness />);
    fireEvent.click(trigger());
    key('l');
    expect(activeLabel()).toBe('Last 15m');
    key('L');
    expect(activeLabel()).toBe('Last 1h');
    key('l');
    expect(activeLabel()).toBe('Last 15m');
    key('t');
    expect(activeLabel()).toBe('Today');
    key('x');
    expect(activeLabel()).toBe('Today');
  });

  it.each([['Enter'], [' ']])('chooses the active row with %j and returns focus to the trigger', (k) => {
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);
    fireEvent.click(trigger());
    key('ArrowDown');
    key('ArrowDown');
    key(k);
    expect(onChange).toHaveBeenCalledWith('1h');
    expect(screen.queryByRole('listbox')).toBeNull();
    expect(trigger()).toHaveFocus();
  });

  it('does not call onChange when the chosen row is chosen again', () => {
    const onChange = vi.fn();
    render(<Harness initial="1h" onChange={onChange} />);
    fireEvent.click(trigger());
    key('Enter');
    expect(onChange).not.toHaveBeenCalled();
    expect(screen.queryByRole('listbox')).toBeNull();
  });

  it.each([['Escape'], ['Tab']])('closes without choosing on %s, with focus back on the trigger', (k) => {
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);
    fireEvent.click(trigger());
    key('ArrowDown');
    key(k);
    expect(screen.queryByRole('listbox')).toBeNull();
    expect(trigger()).toHaveFocus();
    expect(onChange).not.toHaveBeenCalled();
  });

  it('closes on a click outside', () => {
    render(<Harness />);
    fireEvent.click(trigger());
    fireEvent.click(screen.getByTestId('select-dropdown-backdrop'));
    expect(screen.queryByRole('listbox')).toBeNull();
  });

  it('shows its clear button beside the trigger while active, not inside it', () => {
    const onClear = vi.fn();
    render(<Harness onClear={onClear} />);
    expect(screen.queryByRole('button', { name: 'Clear time filter' })).toBeNull();
    fireEvent.click(trigger());
    fireEvent.click(screen.getByRole('option', { name: 'Last 1h' }));
    const clear = screen.getByRole('button', { name: 'Clear time filter' });
    expect(trigger()).not.toContainElement(clear);
    expect(trigger().closest('[data-active]')).toHaveAttribute('data-active', 'true');
    expect(trigger().closest('[data-active]')).toContainElement(clear);
    fireEvent.click(clear);
    expect(onClear).toHaveBeenCalledTimes(1);
  });

  it('Esc inside a drawer closes only the list, and the next Esc the drawer', async () => {
    const onClose = vi.fn();
    function InDrawer() {
      const [open, setOpen] = useState(true);
      return (
        <SlideOver
          isOpen={open}
          onClose={() => {
            onClose();
            setOpen(false);
          }}
          title="Filter Logs"
        >
          <span id="panel-label">Time Range</span>
          <SelectDropdown variant="panel" labelledBy="panel-label" label="Time Range" options={OPTIONS} value="all" onChange={vi.fn()} />
        </SlideOver>
      );
    }
    render(<InDrawer />);
    await act(async () => {});
    const t = screen.getByRole('button', { name: 'Time Range All Time' });
    fireEvent.click(t);
    expect(screen.getByRole('listbox')).toHaveAccessibleName('Time Range');
    key('Escape');
    expect(screen.queryByRole('listbox')).toBeNull();
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByRole('dialog', { name: 'Filter Logs' })).toBeInTheDocument();
    expect(t).toHaveFocus();
    key('Escape');
    expect(onClose).toHaveBeenCalledTimes(1);
  });
  it('keeps the list shut while disabled, and greys the trigger out', () => {
    const onChange = vi.fn();
    render(<SelectDropdown label="Time" options={OPTIONS} value="all" onChange={onChange} disabled />);
    const t = trigger();
    expect(t).toBeDisabled();
    expect(t.className).toContain('opacity-50');
    fireEvent.click(t);
    fireEvent.keyDown(t, { key: 'ArrowDown' });
    expect(screen.queryByRole('listbox')).toBeNull();
    expect(onChange).not.toHaveBeenCalled();
  });

  it('keeps the panel list fixed to the viewport beside its trigger, inside the dialog around it', async () => {
    const onChange = vi.fn();
    render(
      <Modal isOpen onClose={vi.fn()} title="Edit rule">
        <span id="panel-label">Rule Type</span>
        <div data-testid="field" style={{ overflow: 'hidden' }}>
          <SelectDropdown variant="panel" labelledBy="panel-label" label="Rule Type" options={OPTIONS} value="all" onChange={onChange} />
        </div>
      </Modal>,
    );
    await act(async () => {});
    fireEvent.click(screen.getByRole('button', { name: 'Rule Type All Time' }));
    const list = screen.getByRole('listbox');
    expect(screen.getByRole('dialog', { name: 'Edit rule' })).toContainElement(list);
    expect(list.style.position).toBe('fixed');
    expect(list).toHaveFocus();
    fireEvent.click(screen.getByRole('option', { name: 'Today' }));
    expect(onChange).toHaveBeenCalledWith('since_midnight');
    expect(screen.queryByRole('listbox')).toBeNull();
  });

  it('takes the look of the form it sits in, and its rows take the field font', () => {
    const { rerender } = render(
      <SelectDropdown variant="panel" label="Sample Severity" options={OPTIONS} value="all" onChange={vi.fn()} fieldClassName="bg-dark-900 border-dark-800 px-2 py-1" />
    );
    const t = screen.getByRole('button', { name: /^Sample Severity / });
    expect(t.className).toContain('bg-dark-900 border-dark-800 px-2 py-1');
    expect(t.className).not.toContain('bg-dark-950');
    fireEvent.click(t);
    expect(screen.getByRole('option', { name: 'Today' }).className).not.toContain('font-mono');
    expect(t.className).toContain('!border-accent-500');
    fireEvent.click(t);

    rerender(<SelectDropdown variant="panel" label="Sample Severity" options={OPTIONS} value="all" onChange={vi.fn()} />);
    expect(t.className).toContain('bg-dark-950');
    fireEvent.click(t);
    expect(screen.getByRole('option', { name: 'Today' }).className).toContain('font-mono');
  });

  it('names a panel without a visible label by its label prop', () => {
    render(<SelectDropdown variant="panel" label="Add fallback model" options={OPTIONS} value="all" onChange={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: 'Add fallback model All Time' }));
    expect(screen.getByRole('listbox')).toHaveAccessibleName('Add fallback model');
  });

  describe('panel list placement', () => {
    const LONG: SelectOption[] = Array.from({ length: 40 }, (_, i) => ({ value: `m${i}`, label: `model-${i}` }));

    /** Opens a panel dropdown whose trigger spans the given rows of an 800 px viewport, with a list 600 px tall. */
    function openAt(top: number, bottom: number): HTMLElement {
      render(
        <>
          <span id="panel-label">Model</span>
          <SelectDropdown variant="panel" labelledBy="panel-label" label="Model" options={LONG} value="m0" onChange={vi.fn()} />
        </>,
      );
      const t = screen.getByRole('button', { name: /^Model / });
      vi.spyOn(t, 'getBoundingClientRect').mockReturnValue({ top, bottom, left: 16, right: 359, width: 343, height: bottom - top, x: 16, y: top, toJSON: () => ({}) });
      vi.spyOn(HTMLElement.prototype, 'scrollHeight', 'get').mockReturnValue(600);
      fireEvent.click(t);
      return screen.getByRole('listbox');
    }

    const withViewport = (height: number, run: () => void) => {
      const before = window.innerHeight;
      Object.defineProperty(window, 'innerHeight', { configurable: true, value: height });
      try {
        run();
      } finally {
        Object.defineProperty(window, 'innerHeight', { configurable: true, value: before });
        vi.restoreAllMocks();
      }
    };

    it('opens below the trigger at its width, and scrolls within 288 px', () => {
      withViewport(800, () => {
        const list = openAt(100, 138);
        expect(list.style.top).toBe('142px');
        expect(list.style.bottom).toBe('');
        expect(list.style.left).toBe('16px');
        expect(list.style.width).toBe('343px');
        expect(list.style.maxHeight).toBe('288px');
      });
    });

    it('opens above the trigger when there is more room above', () => {
      withViewport(800, () => {
        const list = openAt(700, 738);
        expect(list.style.top).toBe('');
        expect(list.style.bottom).toBe('104px');
        expect(list.style.maxHeight).toBe('288px');
      });
    });

    it('stops short of the top of a short viewport when it opens above', () => {
      withViewport(400, () => {
        const list = openAt(250, 288);
        // 250 px above, less the 4 px gap and the 8 px margin; below has only 100 px
        expect(list.style.bottom).toBe('154px');
        expect(list.style.maxHeight).toBe('238px');
      });
    });

    it('stays below in a short viewport when below has more room, and scrolls within it', () => {
      withViewport(300, () => {
        const list = openAt(60, 98);
        expect(list.style.top).toBe('102px');
        expect(list.style.maxHeight).toBe('190px');
      });
    });
  });
});

