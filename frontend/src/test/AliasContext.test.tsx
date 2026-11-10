import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { AliasProvider, useAlias } from '../context/AliasContext.tsx';

const Saver: React.FC = () => {
  const { bumpAliasVersion } = useAlias();
  return <button onClick={bumpAliasVersion}>Save alias</button>;
};

const Reader: React.FC = () => {
  const { aliasVersion } = useAlias();
  return <span data-testid="version">{aliasVersion}</span>;
};

describe('AliasProvider', () => {
  it('passes an alias save from one component to another that reloads aliases', () => {
    render(
      <AliasProvider>
        <Saver />
        <Reader />
      </AliasProvider>
    );
    expect(screen.getByTestId('version').textContent).toBe('0');
    fireEvent.click(screen.getByRole('button', { name: 'Save alias' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save alias' }));
    expect(screen.getByTestId('version').textContent).toBe('2');
  });

  it('without a provider, reading works and saving does nothing', () => {
    render(
      <>
        <Saver />
        <Reader />
      </>
    );
    fireEvent.click(screen.getByRole('button', { name: 'Save alias' }));
    expect(screen.getByTestId('version').textContent).toBe('0');
  });
});
