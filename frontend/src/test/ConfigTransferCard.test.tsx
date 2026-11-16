import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { ConfigTransferCard, describeSectionPlan } from '../components/settings/ConfigTransferCard.tsx';
import * as configApi from '../api/config.ts';
import { stubDownloads } from './downloadStubs.ts';

const plainFile = { format: 'logshed-config', version: 1, logshed_version: '1.4.0', sections: { alert_rules: [] } };
const secretFile = { ...plainFile, secrets: { kdf: 'scrypt' } };

const preview: configApi.ConfigImportPreview = {
  has_secrets: false,
  secrets_read: false,
  logshed_version: '1.4.0',
  exported_at: '2026-11-16T10:00:00+00:00',
  sections: [
    { name: 'alert_rules', add: 1, replace: 1, unchanged: 0, skip: 0, notes: [], items: [
      { label: 'Disk errors', action: 'replace' },
      { label: 'Fan', action: 'add', note: "Its target 'Ops' will not be on this server, so it is imported turned off." },
    ] },
    { name: 'saved_views', add: 2, replace: 0, unchanged: 0, skip: 0, notes: [], items: [] },
    { name: 'drop_rules', add: 0, replace: 0, unchanged: 3, skip: 0, notes: [], items: [] },
  ],
};

function chooseFile(content: object | string) {
  const text = typeof content === 'string' ? content : JSON.stringify(content);
  fireEvent.change(screen.getByTestId('config-import-file'), {
    target: { files: [new File([text], 'config.json', { type: 'application/json' })] },
  });
}

describe('describeSectionPlan', () => {
  it('names each count that is not zero', () => {
    expect(describeSectionPlan(2, 1, 0, 0)).toBe('2 to add, 1 to replace');
    expect(describeSectionPlan(0, 0, 4, 3)).toBe('4 the same, 3 skipped');
    expect(describeSectionPlan(0, 0, 0, 0)).toBe('Nothing to change');
  });
});

describe('ConfigTransferCard', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('exports without secrets by default', async () => {
    const restore = stubDownloads();
    const spy = vi.spyOn(configApi, 'exportConfiguration').mockResolvedValue(new Blob(['{}']));
    try {
      render(<ConfigTransferCard onImported={vi.fn()} />);
      fireEvent.click(screen.getByRole('button', { name: /Export Configuration/i }));
      await screen.findByText('Configuration exported without secrets.');
      expect(spy).toHaveBeenCalledWith(false, '', '');
    } finally {
      restore();
    }
  });

  it('asks for the admin password and a file password of 8 characters, typed twice, before exporting secrets', async () => {
    const restore = stubDownloads();
    const spy = vi.spyOn(configApi, 'exportConfiguration').mockResolvedValue(new Blob(['{}']));
    try {
      render(<ConfigTransferCard onImported={vi.fn()} />);
      fireEvent.click(screen.getByLabelText(/Include secrets/i));
      const exportButton = screen.getByRole('button', { name: /Export Configuration/i });
      expect(exportButton).toBeDisabled();
      fireEvent.change(screen.getByLabelText(/^File Password/), { target: { value: 'short' } });
      fireEvent.change(screen.getByLabelText('Confirm Password'), { target: { value: 'short' } });
      expect(exportButton).toBeDisabled();
      fireEvent.change(screen.getByLabelText(/^File Password/), { target: { value: 'long enough' } });
      expect(exportButton).toBeDisabled();
      expect(screen.getByText('The passwords do not match.')).toBeInTheDocument();
      fireEvent.change(screen.getByLabelText('Confirm Password'), { target: { value: 'long enough' } });
      // Still waits for the admin password
      expect(exportButton).toBeDisabled();
      fireEvent.change(screen.getByLabelText('Admin Password'), { target: { value: 'admin pw' } });
      fireEvent.click(exportButton);
      await screen.findByText(/exported with its secrets/i);
      expect(spy).toHaveBeenCalledWith(true, 'long enough', 'admin pw');
    } finally {
      restore();
    }
  });

  it('previews a file, imports only the ticked sections and reloads the page settings', async () => {
    const previewSpy = vi.spyOn(configApi, 'previewConfigurationImport').mockResolvedValue(preview);
    const importSpy = vi.spyOn(configApi, 'importConfiguration').mockResolvedValue({
      sections: [{ name: 'alert_rules', added: 1, replaced: 1, unchanged: 0, skipped: 0, failed: [], notes: ["Fan: Its target 'Ops' will not be on this server, so it is imported turned off."] }],
    });
    const onImported = vi.fn();
    render(<ConfigTransferCard onImported={onImported} />);
    chooseFile(plainFile);

    const dialog = await screen.findByTestId('config-import-dialog');
    await within(dialog).findByText('1 to add, 1 to replace');
    expect(previewSpy).toHaveBeenCalledWith(plainFile, '');
    expect(within(dialog).getByText(/Exported from LogShed 1.4.0/)).toBeInTheDocument();
    expect(within(dialog).getByText(/Fan: Its target 'Ops'/)).toBeInTheDocument();
    // Sections with something to do start ticked; one with nothing cannot be ticked
    expect(within(dialog).getByLabelText('Alert rules')).toBeChecked();
    expect(within(dialog).getByLabelText('Saved views')).toBeChecked();
    expect(within(dialog).getByLabelText('Drop rules')).toBeDisabled();
    expect(within(dialog).getByText('3 the same')).toBeInTheDocument();

    fireEvent.click(within(dialog).getByLabelText('Saved views'));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Import 1 Section' }));
    await within(dialog).findByText('1 added, 1 replaced');
    expect(importSpy).toHaveBeenCalledWith(plainFile, ['alert_rules'], '');
    expect(onImported).toHaveBeenCalledTimes(1);
    fireEvent.click(within(dialog).getByRole('button', { name: 'Close' }));
    await waitFor(() => expect(screen.queryByTestId('config-import-dialog')).toBeNull());
  });

  it('asks for the password of a file with secrets, and can import without them', async () => {
    const previewSpy = vi.spyOn(configApi, 'previewConfigurationImport')
      .mockRejectedValueOnce(new Error('The password does not open this file\'s secrets.'))
      .mockResolvedValueOnce({ ...preview, has_secrets: true, secrets_read: false });
    render(<ConfigTransferCard onImported={vi.fn()} />);
    chooseFile(secretFile);

    const dialog = await screen.findByTestId('config-import-dialog');
    fireEvent.change(within(dialog).getByLabelText('File Password'), { target: { value: 'wrong one' } });
    fireEvent.click(within(dialog).getByRole('button', { name: /Open Secrets/i }));
    expect(await within(dialog).findByRole('alert')).toHaveTextContent('does not open');
    expect(previewSpy).toHaveBeenLastCalledWith(secretFile, 'wrong one');

    fireEvent.click(within(dialog).getByRole('button', { name: /Import Without Secrets/i }));
    await within(dialog).findByText(/Read without its secrets/);
    expect(previewSpy).toHaveBeenLastCalledWith(secretFile, '');
  });

  it('says so when the file is not a configuration file', async () => {
    const previewSpy = vi.spyOn(configApi, 'previewConfigurationImport');
    render(<ConfigTransferCard onImported={vi.fn()} />);
    chooseFile({ alert_rules: [] });
    await screen.findByText('This is not a LogShed configuration file.');
    chooseFile('not json');
    await screen.findByText(/The file is not JSON/);
    expect(previewSpy).not.toHaveBeenCalled();
    expect(screen.queryByTestId('config-import-dialog')).toBeNull();
  });
});
