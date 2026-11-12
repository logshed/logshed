import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { ModelChainEditor } from '../components/ai/ModelChainEditor.tsx';
import { parseFallbackModels, promoteFirstFallback } from '../utils/modelChain.ts';
import { AiModelInfo } from '../types.ts';
import { chooseOption, optionLabels } from './selectDropdownHelpers.ts';

const models = [
  { id: 'model-a', name: 'Model A', supports_thinking: false },
  { id: 'model-b', name: 'Model B', supports_thinking: true },
  { id: 'model-c', name: 'Model C', supports_thinking: false },
] as AiModelInfo[];

const picker = () => screen.getByRole('button', { name: /Add Fallback Model/ });

function renderEditor(fallbacks: string[], overrides: Partial<React.ComponentProps<typeof ModelChainEditor>> = {}) {
  const onChange = vi.fn();
  const onPromoteFirst = vi.fn();
  render(
    <ModelChainEditor
      fallbacks={fallbacks}
      primaryModel="model-a"
      availableModels={models}
      onChange={onChange}
      onPromoteFirst={onPromoteFirst}
      heading="Fallback Models"
      emptyText="No fallbacks."
      size="roomy"
      {...overrides}
    />
  );
  return { onChange, onPromoteFirst };
}

describe('modelChain helpers', () => {
  it('parses the stored list without blanks', () => {
    expect(parseFallbackModels('')).toEqual([]);
    expect(parseFallbackModels(' a, ,b ,c,')).toEqual(['a', 'b', 'c']);
  });

  it('swaps the first fallback with the primary model', () => {
    expect(promoteFirstFallback([], 'p')).toBeNull();
    expect(promoteFirstFallback(['a', 'b'], 'p')).toEqual({ primary: 'a', fallbacks: ['p', 'b'] });
    // A blank primary, or one already further down the list, is not added again
    expect(promoteFirstFallback(['a', 'b'], ' ')).toEqual({ primary: 'a', fallbacks: ['b'] });
    expect(promoteFirstFallback(['a', 'p'], 'p')).toEqual({ primary: 'a', fallbacks: ['p'] });
  });
});

describe('ModelChainEditor', () => {
  it('shows the empty text when there are no fallbacks', () => {
    renderEditor([]);
    expect(screen.getByText('No fallbacks.')).toBeInTheDocument();
  });

  it('lists fallbacks and reorders, removes and promotes them', () => {
    const { onChange, onPromoteFirst } = renderEditor(['model-b', 'model-c']);
    expect(screen.getByText('1st Fallback')).toBeInTheDocument();
    expect(screen.getByText('2nd Fallback')).toBeInTheDocument();
    expect(screen.getByText('Reasoning')).toBeInTheDocument();

    const up = screen.getAllByRole('button', { name: /Make primary model|Move fallback up/ });
    const down = screen.getAllByRole('button', { name: 'Move fallback down' });
    expect(down[1]).toBeDisabled();

    fireEvent.click(up[0]);
    expect(onPromoteFirst).toHaveBeenCalledTimes(1);
    fireEvent.click(up[1]);
    expect(onChange).toHaveBeenLastCalledWith(['model-c', 'model-b']);
    fireEvent.click(down[0]);
    expect(onChange).toHaveBeenLastCalledWith(['model-c', 'model-b']);
    fireEvent.click(screen.getAllByRole('button', { name: 'Remove fallback' })[0]);
    expect(onChange).toHaveBeenLastCalledWith(['model-c']);
  });

  it('adds a listed model that is neither primary nor already a fallback', () => {
    const { onChange } = renderEditor(['model-b']);
    const add = screen.getByRole('button', { name: 'Add' });
    expect(add).toBeDisabled();

    const offered = optionLabels(picker());
    expect(offered.some((t) => t.includes('model-a'))).toBe(false);
    expect(offered.some((t) => t.includes('model-b'))).toBe(false);

    chooseOption(picker(), /model-c/);
    fireEvent.click(screen.getByRole('button', { name: 'Add' }));
    expect(onChange).toHaveBeenLastCalledWith(['model-b', 'model-c']);
  });

  it('adds a typed model, ignoring a duplicate, and cancels back to the list', () => {
    const { onChange } = renderEditor(['model-b']);
    chooseOption(picker(), 'Custom fallback model...');
    const input = screen.getByPlaceholderText('Enter custom model ID');

    fireEvent.change(input, { target: { value: ' model-b ' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add' }));
    expect(onChange).not.toHaveBeenCalled();

    chooseOption(picker(), 'Custom fallback model...');
    fireEvent.change(screen.getByPlaceholderText('Enter custom model ID'), { target: { value: ' my-model ' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add' }));
    expect(onChange).toHaveBeenLastCalledWith(['model-b', 'my-model']);

    chooseOption(picker(), 'Custom fallback model...');
    fireEvent.click(screen.getByTitle('Cancel custom fallback'));
    expect(screen.queryByPlaceholderText('Enter custom model ID')).not.toBeInTheDocument();
  });

  it('shows the heading note in the compact size', () => {
    renderEditor([], { size: 'compact', headingNote: 'Queried in order' });
    expect(screen.getByText('Queried in order')).toBeInTheDocument();
  });
});
