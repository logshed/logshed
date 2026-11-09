import { fireEvent, screen } from '@testing-library/react';

/** The Console toolbar's Severity or Time dropdown, named by its label and value ("Time: Last 1h"). */
export const toolbarSelect = (label: 'Severity' | 'Time'): HTMLElement =>
  screen.getByRole('button', { name: new RegExp(`^${label}: `) });

/** The phone filter panel's Severity or Time Range dropdown, named by the label above it. */
export const panelSelect = (label: 'Severity' | 'Time Range'): HTMLElement =>
  screen.getByRole('button', { name: new RegExp(`^${label} `) });

/** Opens a SelectDropdown and clicks one of its rows. */
export function chooseOption(trigger: HTMLElement, optionLabel: string | RegExp): void {
  fireEvent.click(trigger);
  fireEvent.click(screen.getByRole('option', { name: optionLabel }));
}

/** The value a SelectDropdown shows. */
export const shownValue = (trigger: HTMLElement): string | null | undefined =>
  trigger.querySelector('[id$="-value"]')?.textContent;

/** Opens a SelectDropdown, reads the labels of its rows, and closes it again. */
export function optionLabels(trigger: HTMLElement): string[] {
  fireEvent.click(trigger);
  const labels = screen.getAllByRole('option').map((o) => o.textContent ?? '');
  fireEvent.click(trigger);
  return labels;
}
