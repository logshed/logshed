import { useState } from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { SelectDropdown, SelectOption } from '../components/common/SelectDropdown.tsx';

const MODELS: SelectOption[] = [
  { value: 'openai/gpt-6-luna', label: 'openai/gpt-6-luna [Reasoning]', hint: '$0.1 in / $0.5 out per 1M tokens, 1.05M context' },
  { value: 'anthropic/claude-sonnet-5.5', label: 'anthropic/claude-sonnet-5.5 [Reasoning]', hint: '$2 in / $10 out per 1M tokens, 1M context' },
  { value: 'meta-llama/llama-4-scout:free', label: 'meta-llama/llama-4-scout:free', hint: 'Free, 128K context' },
  { value: '__custom__', label: 'Custom model name...', pinned: true },
];

function Harness({ searchable = true, onChange = vi.fn() }: { searchable?: boolean; onChange?: (v: string) => void }) {
  const [value, setValue] = useState('openai/gpt-6-luna');
  return (
    <SelectDropdown
      variant="panel"
      label="Primary Model"
      options={MODELS}
      value={value}
      searchable={searchable}
      onChange={(v) => {
        onChange(v);
        setValue(v);
      }}
    />
  );
}

const trigger = () => screen.getByRole('button', { name: /^Primary Model / });
const search = () => screen.getByRole('combobox', { name: 'Search Primary Model' });
const shownRows = () => screen.getAllByRole('option').map((o) => o.getAttribute('id') && o.firstElementChild?.firstChild?.textContent);
const type = (text: string) => fireEvent.change(search(), { target: { value: text } });
const key = (k: string) => fireEvent.keyDown(document.activeElement!, { key: k });

describe('SelectDropdown with a search box', () => {
  it('shows the label alone on the trigger and the hint as a second line in the list', () => {
    render(<Harness />);
    expect(trigger().querySelector('[id$="-value"]')).toHaveTextContent(/^openai\/gpt-6-luna \[Reasoning\]$/);
    expect(trigger()).not.toHaveTextContent('1.05M context');
    fireEvent.click(trigger());
    const row = screen.getByRole('option', { name: /claude-sonnet-5\.5/ });
    expect(row).toHaveTextContent('$2 in / $10 out per 1M tokens, 1M context');
  });

  it('opens with the search box focused, empty, and every row shown', () => {
    render(<Harness />);
    fireEvent.click(trigger());
    expect(search()).toHaveFocus();
    expect(search()).toHaveValue('');
    expect(search()).toHaveAttribute('aria-controls', screen.getByRole('listbox').id);
    expect(screen.getAllByRole('option')).toHaveLength(4);
  });

  it('keeps rows that hold every typed word, in any order and case, in the label or the hint; pinned rows always stay', () => {
    render(<Harness />);
    fireEvent.click(trigger());
    type('CLAUDE');
    expect(shownRows()).toEqual(['anthropic/claude-sonnet-5.5 [Reasoning]', 'Custom model name...']);
    type('free 128k');
    expect(shownRows()).toEqual(['meta-llama/llama-4-scout:free', 'Custom model name...']);
    type('reasoning $2');
    expect(shownRows()).toEqual(['anthropic/claude-sonnet-5.5 [Reasoning]', 'Custom model name...']);
  });

  it('moves with Up and Down through the rows shown and chooses with Enter', () => {
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);
    fireEvent.click(trigger());
    type('llama');
    expect(search().getAttribute('aria-activedescendant')).toBe(screen.getAllByRole('option')[0].id);
    key('ArrowDown');
    expect(search().getAttribute('aria-activedescendant')).toBe(screen.getAllByRole('option')[1].id);
    key('ArrowUp');
    key('Enter');
    expect(onChange).toHaveBeenCalledWith('meta-llama/llama-4-scout:free');
    expect(screen.queryByRole('listbox')).toBeNull();
    expect(trigger()).toHaveFocus();
  });

  it('lets Space, Home, End and letters edit the search text instead of choosing or jumping', () => {
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);
    fireEvent.click(trigger());
    for (const k of [' ', 'Home', 'End', 'c']) {
      const event = fireEvent.keyDown(search(), { key: k });
      expect(event).toBe(true); // not prevented: the box gets the key
    }
    expect(onChange).not.toHaveBeenCalled();
    expect(screen.getByRole('listbox')).toBeInTheDocument();
  });

  it('says when nothing matches, and Enter then chooses nothing', () => {
    const onChange = vi.fn();
    const unpinned = MODELS.filter((m) => !m.pinned);
    function NoPinned() {
      return <SelectDropdown variant="panel" label="Primary Model" options={unpinned} value="openai/gpt-6-luna" onChange={onChange} searchable />;
    }
    render(<NoPinned />);
    fireEvent.click(trigger());
    type('mistral');
    expect(screen.queryAllByRole('option')).toHaveLength(0);
    expect(screen.getByText('Nothing matches "mistral".')).toBeInTheDocument();
    expect(search()).not.toHaveAttribute('aria-activedescendant');
    key('Enter');
    expect(onChange).not.toHaveBeenCalled();
  });

  it('closes on Esc without choosing, and opens again with the search cleared', () => {
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);
    fireEvent.click(trigger());
    type('claude');
    key('Escape');
    expect(screen.queryByRole('listbox')).toBeNull();
    expect(onChange).not.toHaveBeenCalled();
    fireEvent.click(trigger());
    expect(search()).toHaveValue('');
    expect(screen.getAllByRole('option')).toHaveLength(4);
  });

  it('closes on Tab without choosing', () => {
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);
    fireEvent.click(trigger());
    type('claude');
    key('Tab');
    expect(screen.queryByRole('listbox')).toBeNull();
    expect(onChange).not.toHaveBeenCalled();
  });

  it('makes the first match active after typing, not a pinned row above it, so Enter chooses the match', () => {
    const onChange = vi.fn();
    const withPlaceholder: SelectOption[] = [{ value: '', label: '-- Add Fallback Model --', pinned: true }, ...MODELS];
    function Fallback() {
      return <SelectDropdown variant="panel" label="Primary Model" options={withPlaceholder} value="" onChange={onChange} searchable />;
    }
    render(<Fallback />);
    fireEvent.click(trigger());
    type('claude');
    expect(shownRows()).toEqual(['-- Add Fallback Model --', 'anthropic/claude-sonnet-5.5 [Reasoning]', 'Custom model name...']);
    key('Enter');
    expect(onChange).toHaveBeenCalledWith('anthropic/claude-sonnet-5.5');
  });

  it('with only pinned rows left, the first of them is active', () => {
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);
    fireEvent.click(trigger());
    type('mistral');
    expect(shownRows()).toEqual(['Custom model name...']);
    key('Enter');
    expect(onChange).toHaveBeenCalledWith('__custom__');
  });

  it('keeps focus in the search box when the popup around the list is pressed', () => {
    render(<Harness />);
    fireEvent.click(trigger());
    const popup = search().closest('div[style]') as HTMLElement;
    expect(fireEvent.mouseDown(popup)).toBe(false); // prevented: focus stays in the box
    expect(fireEvent.mouseDown(search())).toBe(true);
    expect(search()).toHaveFocus();
    // The list itself (rows, scrollbar) takes presses as usual: it takes focus and its keys work
    expect(fireEvent.mouseDown(screen.getByRole('listbox'))).toBe(true);
    expect(fireEvent.mouseDown(screen.getAllByRole('option')[1])).toBe(true);
  });

  it('closes an empty list on Enter when it has no search box, as before', () => {
    const onChange = vi.fn();
    render(<SelectDropdown variant="panel" label="Primary Model" options={[]} value="" onChange={onChange} />);
    fireEvent.click(screen.getByRole('button', { name: /^Primary Model/ }));
    expect(screen.getByRole('listbox')).toHaveFocus();
    key('Enter');
    expect(screen.queryByRole('listbox')).toBeNull();
    expect(onChange).not.toHaveBeenCalled();
  });

  it('has no search box without searchable, and the list takes focus as before', () => {
    render(<Harness searchable={false} />);
    fireEvent.click(trigger());
    expect(screen.queryByRole('combobox')).toBeNull();
    expect(screen.getByRole('listbox')).toHaveFocus();
  });
});
