import { useState } from 'react';
import { render, screen, fireEvent, act } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { SelectDropdown, SelectOption } from '../components/common/SelectDropdown.tsx';
import { SlideOver } from '../components/common/SlideOver.tsx';

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
});

