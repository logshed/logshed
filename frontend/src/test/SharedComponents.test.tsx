import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { Button, IconButton, buttonClass } from '../components/common/Button.tsx';
import { Checkbox, Field, TextArea, TextField } from '../components/common/Field.tsx';
import { Tabs } from '../components/common/Tabs.tsx';
import { TableHead } from '../components/common/Table.tsx';
import { Card, CardHeader } from '../components/common/Card.tsx';
import { Badge } from '../components/common/Badge.tsx';

describe('Button', () => {
  it('is a plain button by default, so it never submits a form by accident', () => {
    const onSubmit = vi.fn((e: React.FormEvent) => e.preventDefault());
    render(
      <form onSubmit={onSubmit}>
        <Button>Cancel</Button>
        <Button type="submit" variant="primary">Save</Button>
      </form>
    );
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(onSubmit).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect(onSubmit).toHaveBeenCalledTimes(1);
  });

  it('draws each variant one way, with 4 px corners and a border on every variant', () => {
    for (const variant of ['primary', 'secondary', 'danger', 'danger-soft', 'soft', 'ghost'] as const) {
      const look = buttonClass(variant);
      expect(look).toContain('rounded-sm');
      expect(look).toMatch(/(^| )border( |$)/);
    }
    expect(buttonClass('primary')).toContain('bg-accent-700');
    expect(buttonClass('danger')).toContain('bg-danger-600');
  });

  it('puts the icon before the label and adds layout classes after the look', () => {
    render(
      <Button icon={<svg data-testid="icon" />} className="w-full">
        Add
      </Button>
    );
    const button = screen.getByRole('button', { name: 'Add' });
    expect(button.firstElementChild).toBe(screen.getByTestId('icon'));
    expect(button.className.endsWith(' w-full')).toBe(true);
  });
});

describe('IconButton', () => {
  it('is named by its label, which is also its tooltip unless a title is given', () => {
    render(
      <>
        <IconButton label="Delete rule">
          <svg />
        </IconButton>
        <IconButton label="Close" title="Close (Esc)">
          <svg />
        </IconButton>
      </>
    );
    expect(screen.getByRole('button', { name: 'Delete rule' })).toHaveAttribute('title', 'Delete rule');
    expect(screen.getByRole('button', { name: 'Close' })).toHaveAttribute('title', 'Close (Esc)');
  });
});

describe('Field and text boxes', () => {
  it('labels the box and announces its hint', () => {
    render(<TextField label="Public URL" hint="Used in notification links." id="url" />);
    const box = screen.getByLabelText('Public URL');
    expect(box).toHaveAttribute('id', 'url');
    expect(box).toHaveAccessibleDescription('Used in notification links.');
    expect(box).not.toHaveAttribute('aria-invalid');
  });

  it('shows an error in place of the hint and marks the box invalid', () => {
    render(<TextArea label="Pattern" hint="A regular expression." error="Not a valid pattern" />);
    const box = screen.getByLabelText('Pattern');
    expect(box).toHaveAttribute('aria-invalid', 'true');
    expect(box).toHaveAccessibleDescription('Not a valid pattern');
    expect(screen.queryByText('A regular expression.')).toBeNull();
    expect(box.className).toContain('border-danger-600');
  });

  it('makes an id when none is given, and gives a dropdown the label id', () => {
    render(
      <Field label="Provider">
        {({ id, labelId }) => <button id={id} aria-labelledby={labelId} type="button">OpenAI</button>}
      </Field>
    );
    expect(screen.getByRole('button', { name: 'Provider' })).toHaveAttribute('id');
  });

  it('renders a bare box, named by aria-label, when there is no label', () => {
    render(<TextField aria-label="Search" mono className="min-w-0" />);
    const box = screen.getByRole('textbox', { name: 'Search' });
    expect(box.className).toContain('font-mono');
    expect(box.className).toContain('min-w-0');
  });
});

describe('Checkbox', () => {
  it('toggles from its whole label line and is ticked in the accent colour', () => {
    const onChange = vi.fn();
    render(<Checkbox label="Check for new versions" checked={false} onChange={onChange} />);
    fireEvent.click(screen.getByText('Check for new versions'));
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('checkbox', { name: 'Check for new versions' }).className).toContain('accent-accent-600');
  });
});

describe('Tabs', () => {
  it('is a named navigation bar that marks the shown tab and reports clicks', () => {
    const onSelect = vi.fn();
    render(
      <Tabs
        label="Settings sections"
        active="app"
        onSelect={onSelect}
        items={[
          { id: 'app', label: 'Application', icon: <svg /> },
          { id: 'advanced', label: 'Advanced', icon: <svg />, extra: <span>3</span> },
        ]}
      />
    );
    const nav = screen.getByRole('navigation', { name: 'Settings sections' });
    expect(nav).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Application' })).toHaveAttribute('aria-current', 'page');
    const advanced = screen.getByRole('button', { name: /^Advanced/ });
    expect(advanced).not.toHaveAttribute('aria-current');
    fireEvent.click(advanced);
    expect(onSelect).toHaveBeenCalledWith('advanced');
  });
});

describe('TableHead, Card and Badge', () => {
  it('heads every column as a column header in the shared style', () => {
    render(
      <table>
        <TableHead columns={[{ label: 'Name' }, { label: 'Actions', align: 'right' }]} />
      </table>
    );
    const headers = screen.getAllByRole('columnheader');
    expect(headers).toHaveLength(2);
    expect(headers[0]).toHaveAttribute('scope', 'col');
    expect(headers[0].className).toContain('text-left');
    expect(headers[1].className).toContain('text-right');
    expect(headers[0].parentElement!.className).toContain('uppercase');
  });

  it('gives a card a heading at the level asked for, its description and its actions', () => {
    render(
      <Card aria-label="About">
        <CardHeader title="About LogShed" as="h2" description="Version and links" actions={<Badge>v1.4.0</Badge>} />
      </Card>
    );
    expect(screen.getByRole('heading', { level: 2, name: 'About LogShed' })).toBeInTheDocument();
    expect(screen.getByText('Version and links')).toBeInTheDocument();
    expect(screen.getByText('v1.4.0').className).toContain('rounded-sm');
  });
});
