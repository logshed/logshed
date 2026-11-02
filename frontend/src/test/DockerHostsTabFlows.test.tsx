import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { DockerHostsTab } from '../components/settings/DockerHostsTab.tsx';
import * as dockerHostsApi from '../api/dockerHosts.ts';
import type { DockerEndpoint } from '../types.ts';

let isMobile = false;
vi.mock('../utils/hooks.ts', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../utils/hooks.ts')>();
  return { ...actual, useMediaQuery: () => isMobile };
});

const host = (overrides: Partial<DockerEndpoint>): DockerEndpoint => ({
  id: 9,
  name: 'Host',
  url: 'tcp://10.0.0.9:2375',
  source_alias: 'host',
  enabled: true,
  tls_enabled: false,
  ca_cert: null,
  client_cert: null,
  has_client_key: false,
  exclude_containers: '',
  created_at: '2026-10-10T10:00:00Z',
  updated_at: '2026-10-10T10:00:00Z',
  is_connected: false,
  active_containers: 0,
  ...overrides,
});

const HOSTS: DockerEndpoint[] = [
  host({ id: 1, name: 'Primary Docker', url: 'unix:///var/run/docker.sock', source_alias: 'docker', is_connected: true, active_containers: 4 }),
  host({ id: 2, name: 'NAS', url: 'tcp://192.168.1.20:2376', source_alias: 'nas', tls_enabled: true, has_client_key: true,
    ca_cert: 'CA PEM', client_cert: 'CERT PEM', last_error: 'certificate verify failed' }),
  host({ id: 3, name: 'Pi', url: 'tcp://socket-proxy.lan:2375', source_alias: 'pi' }),
  host({ id: 4, name: 'Old Box', url: 'tcp://10.0.0.4:2375', source_alias: 'oldbox', enabled: false }),
];

function stubApi(hosts: DockerEndpoint[] = HOSTS) {
  return {
    fetch: vi.spyOn(dockerHostsApi, 'fetchDockerEndpoints').mockResolvedValue(hosts),
    update: vi.spyOn(dockerHostsApi, 'updateDockerEndpoint').mockResolvedValue(hosts[0]),
    create: vi.spyOn(dockerHostsApi, 'createDockerEndpoint').mockResolvedValue(hosts[0]),
    remove: vi.spyOn(dockerHostsApi, 'deleteDockerEndpoint').mockResolvedValue(undefined as never),
    test: vi.spyOn(dockerHostsApi, 'testDockerEndpoint'),
  };
}

/** The phone card of a host, found from its name. */
function cardOf(name: string): HTMLElement {
  return screen.getByText(name).closest('div.p-4') as HTMLElement;
}

beforeEach(() => {
  vi.restoreAllMocks();
  isMobile = false;
});

describe('DockerHostsTab on a phone', () => {
  beforeEach(() => {
    isMobile = true;
  });

  it('shows each host as a card with its badges, URL, alias and status', async () => {
    stubApi();
    render(<DockerHostsTab />);

    await screen.findByText('Configured Docker Hosts (4)');
    expect(within(cardOf('Primary Docker')).getByText('Primary')).toBeInTheDocument();
    expect(within(cardOf('Primary Docker')).getByText('Connected (4 active)')).toBeInTheDocument();
    expect(within(cardOf('NAS')).getByText('TLS')).toBeInTheDocument();
    expect(within(cardOf('NAS')).getByText('tcp://192.168.1.20:2376')).toBeInTheDocument();
    expect(within(cardOf('NAS')).getByText('nas')).toBeInTheDocument();
    expect(within(cardOf('NAS')).getByTitle('certificate verify failed')).toHaveTextContent('Error');
    expect(within(cardOf('Pi')).getByText('Reconnecting')).toBeInTheDocument();
    expect(within(cardOf('Old Box')).getByText('Disabled')).toBeInTheDocument();
    expect(within(cardOf('Primary Docker')).queryByTitle('Delete Host')).toBeNull();
  });

  it('enables, disables, edits and deletes from the card', async () => {
    const api = stubApi();
    render(<DockerHostsTab />);
    await screen.findByText('Configured Docker Hosts (4)');

    fireEvent.click(within(cardOf('Old Box')).getByRole('button', { name: /Enable/ }));
    await waitFor(() => expect(api.update).toHaveBeenCalledWith(4, { enabled: true }));
    fireEvent.click(within(cardOf('Pi')).getByRole('button', { name: /Disable/ }));
    await waitFor(() => expect(api.update).toHaveBeenCalledWith(3, { enabled: false }));

    fireEvent.click(within(cardOf('Pi')).getByTitle('Edit Host'));
    expect(screen.getByRole('heading', { name: 'Edit Docker Host' })).toBeInTheDocument();
    expect((screen.getByLabelText(/Host Name/i) as HTMLInputElement).value).toBe('Pi');
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));

    fireEvent.click(within(cardOf('Pi')).getByTitle('Delete Host'));
    fireEvent.click(screen.getByRole('button', { name: 'Remove Host' }));
    await waitFor(() => expect(api.remove).toHaveBeenCalledWith(3));
  });

  it('shows the result of a connection test on the card', async () => {
    const api = stubApi();
    api.test
      .mockResolvedValueOnce({ success: true, version: '27.3.1', latency_ms: 12.4 })
      .mockRejectedValueOnce(new Error('timed out'));
    render(<DockerHostsTab />);
    await screen.findByText('Configured Docker Hosts (4)');

    fireEvent.click(within(cardOf('Pi')).getByRole('button', { name: /Test Connection/ }));
    expect(await within(cardOf('Pi')).findByText('Connected (27.3.1, 12 ms)')).toBeInTheDocument();
    expect(api.test).toHaveBeenLastCalledWith(expect.objectContaining({ endpoint_id: 3, url: 'tcp://socket-proxy.lan:2375' }));

    fireEvent.click(within(cardOf('NAS')).getByRole('button', { name: /Test Connection/ }));
    expect(await within(cardOf('NAS')).findByText('timed out')).toBeInTheDocument();
    expect(api.test).toHaveBeenLastCalledWith(
      expect.objectContaining({ endpoint_id: 2, tls_enabled: true, ca_cert: 'CA PEM', client_cert: 'CERT PEM' })
    );
  });
});

describe('DockerHostsTab desktop rows', () => {
  it('shows Reconnecting and Disabled states and the row test result', async () => {
    const api = stubApi();
    api.test.mockResolvedValueOnce({ success: true, latency_ms: 7.6 }).mockResolvedValueOnce({ success: false, error: 'refused' });
    render(<DockerHostsTab />);
    await screen.findByText('Configured Docker Hosts (4)');

    const piRow = screen.getByText('Pi').closest('tr') as HTMLElement;
    expect(within(piRow).getByText('Reconnecting')).toBeInTheDocument();
    expect(within(screen.getByText('Old Box').closest('tr') as HTMLElement).getByText('Disabled')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Test connection to Pi' }));
    expect(await within(piRow).findByText('8 ms')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Test connection to NAS' }));
    const nasRow = screen.getByText('NAS').closest('tr') as HTMLElement;
    expect(await within(nasRow).findByTitle('refused')).toHaveTextContent('Failed');
  });
});

describe('DockerHostsTab errors', () => {
  it('shows a load failure', async () => {
    vi.spyOn(dockerHostsApi, 'fetchDockerEndpoints').mockRejectedValue(new Error('Database is locked'));
    render(<DockerHostsTab />);
    expect(await screen.findByText('Database is locked')).toBeInTheDocument();
  });

  it('shows a failed enable or disable', async () => {
    const api = stubApi();
    api.update.mockRejectedValue(new Error('Host is busy'));
    render(<DockerHostsTab />);
    await screen.findByText('Configured Docker Hosts (4)');
    fireEvent.click(screen.getByRole('button', { name: 'Disable host Pi' }));
    expect(await screen.findByText('Host is busy')).toBeInTheDocument();
  });

  it('shows a failed delete and closes the confirmation', async () => {
    const api = stubApi();
    api.remove.mockRejectedValue(new Error('Host is in use'));
    render(<DockerHostsTab />);
    await screen.findByText('Configured Docker Hosts (4)');
    fireEvent.click((screen.getByText('Pi').closest('tr') as HTMLElement).querySelector('button[title="Delete Host"]')!);
    fireEvent.click(screen.getByRole('button', { name: 'Remove Host' }));
    expect(await screen.findByText('Host is in use')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Remove Host' })).toBeNull();
  });

  it('offers no test without a URL, and shows a failed test and a failed save in the dialog', async () => {
    const api = stubApi([]);
    api.test.mockRejectedValue(new Error('Name or service not known'));
    api.create.mockRejectedValue(new Error('A Docker host with this name already exists.'));
    const onHostSaved = vi.fn();
    render(<DockerHostsTab onHostSaved={onHostSaved} />);
    await screen.findByText('No Docker hosts configured');

    fireEvent.click(screen.getByRole('button', { name: /Add Docker Host/i }));
    const dialog = screen.getByRole('heading', { name: 'Add Docker Host' }).closest('div[role="dialog"]') ?? document.body;
    expect(within(dialog as HTMLElement).getByRole('button', { name: /Test Connection/ })).toBeDisabled();

    fireEvent.change(screen.getByLabelText(/Host Name/i), { target: { value: 'Unraid' } });
    fireEvent.change(screen.getByLabelText(/Docker Endpoint URL/i), { target: { value: 'tcp://unraid.lan:2375' } });
    fireEvent.click(within(dialog as HTMLElement).getByRole('button', { name: /Test Connection/ }));
    expect(await screen.findByText('Name or service not known')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Add Host' }));
    expect(await screen.findByText('A Docker host with this name already exists.')).toBeInTheDocument();
    expect(onHostSaved).not.toHaveBeenCalled();
  });

  it('refuses to save without a name and URL', async () => {
    const api = stubApi([]);
    render(<DockerHostsTab />);
    await screen.findByText('No Docker hosts configured');
    fireEvent.click(screen.getByRole('button', { name: /Add Docker Host/i }));
    fireEvent.submit(screen.getByRole('button', { name: 'Add Host' }).closest('form')!);
    expect(screen.getByText('Name and URL are required.')).toBeInTheDocument();
    expect(api.create).not.toHaveBeenCalled();
  });
});

describe('DockerHostsTab saving a stored client key', () => {
  it('sends an empty key when the stored key is removed, and a new key when one is typed', async () => {
    const api = stubApi();
    render(<DockerHostsTab />);
    await screen.findByText('Configured Docker Hosts (4)');

    fireEvent.click((screen.getByText('NAS').closest('tr') as HTMLElement).querySelector('button[title="Edit Host"]')!);
    fireEvent.click(screen.getByRole('button', { name: 'Remove stored key' }));
    expect(screen.getByRole('button', { name: 'Key marked for removal (Undo)' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Update Host' }));
    await waitFor(() => expect(api.update).toHaveBeenCalledWith(2, expect.objectContaining({ client_key: '' })));

    fireEvent.click((screen.getByText('NAS').closest('tr') as HTMLElement).querySelector('button[title="Edit Host"]')!);
    fireEvent.change(screen.getByLabelText('Client Private Key'), { target: { value: '  NEW KEY PEM  ' } });
    fireEvent.click(screen.getByRole('button', { name: 'Update Host' }));
    await waitFor(() => expect(api.update).toHaveBeenLastCalledWith(2, expect.objectContaining({ client_key: 'NEW KEY PEM' })));

    fireEvent.click((screen.getByText('NAS').closest('tr') as HTMLElement).querySelector('button[title="Edit Host"]')!);
    fireEvent.click(screen.getByRole('button', { name: 'Update Host' }));
    await waitFor(() => expect(api.update).toHaveBeenCalledTimes(3));
    expect(api.update.mock.calls[2][1]).not.toHaveProperty('client_key');
  });
});

describe('DockerHostsTab opened from elsewhere', () => {
  it('opens the edit dialog for the requested host, with the primary host name fixed', async () => {
    stubApi();
    render(<DockerHostsTab initialEditEndpointId={1} />);
    expect(await screen.findByRole('heading', { name: 'Edit Docker Host' })).toBeInTheDocument();
    expect(screen.getByLabelText(/Host Name/i)).toBeDisabled();
    expect(screen.getByText('Primary Docker endpoint name is fixed.')).toBeInTheDocument();
  });

  it('opens the add dialog filled in from the request', async () => {
    stubApi([]);
    render(<DockerHostsTab initialAddHost={{ name: 'Media Box', url: 'tcp://10.0.0.30:2375', source_alias: 'media' }} />);
    expect(await screen.findByRole('heading', { name: 'Add Docker Host' })).toBeInTheDocument();
    expect((screen.getByLabelText(/Host Name/i) as HTMLInputElement).value).toBe('Media Box');
    expect((screen.getByLabelText(/^Host Alias/i) as HTMLInputElement).value).toBe('media');
    // The alias was given, so typing a new name no longer derives it
    fireEvent.change(screen.getByLabelText(/Host Name/i), { target: { value: 'Media Server' } });
    expect((screen.getByLabelText(/^Host Alias/i) as HTMLInputElement).value).toBe('media');
  });

  it('explains a deleted host and offers a syslog alias or re-adding it', async () => {
    stubApi([]);
    const onAddSyslogAlias = vi.fn();
    const onClearSourcesState = vi.fn();
    render(
      <DockerHostsTab
        initialDeletedHostInfo={{ name: 'old-nas', ip: '192.168.1.40' }}
        onAddSyslogAlias={onAddSyslogAlias}
        onClearSourcesState={onClearSourcesState}
      />
    );

    expect(await screen.findByText('Docker host endpoint not found')).toBeInTheDocument();
    expect(screen.getByText('192.168.1.40')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Add Syslog Host Alias' }));
    expect(onAddSyslogAlias).toHaveBeenCalledWith('192.168.1.40');
    expect(onClearSourcesState).toHaveBeenCalled();
    expect(screen.queryByText('Docker host endpoint not found')).toBeNull();
  });

  it('re-adds a deleted host with its address, or the local socket for a docker source', async () => {
    stubApi([]);
    const { unmount } = render(<DockerHostsTab initialDeletedHostInfo={{ name: 'old-nas', ip: '192.168.1.40' }} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Re-add Docker Host' }));
    expect((screen.getByLabelText(/Host Name/i) as HTMLInputElement).value).toBe('old-nas');
    expect(screen.getByDisplayValue('tcp://192.168.1.40:2375')).toBeInTheDocument();
    unmount();

    render(<DockerHostsTab initialDeletedHostInfo={{ name: 'docker', ip: 'docker' }} onAddSyslogAlias={vi.fn()} />);
    expect(await screen.findByText('Docker host endpoint not found')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Add Syslog Host Alias' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Re-add Docker Host' }));
    expect(screen.getByDisplayValue('unix:///var/run/docker.sock')).toBeInTheDocument();
  });

  it('Close dismisses the notice', async () => {
    stubApi([]);
    const onClearSourcesState = vi.fn();
    render(<DockerHostsTab initialDeletedHostInfo={{ name: 'old-nas', ip: '192.168.1.40' }} onClearSourcesState={onClearSourcesState} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Close' }));
    expect(screen.queryByText('Docker host endpoint not found')).toBeNull();
    expect(onClearSourcesState).toHaveBeenCalledTimes(1);
  });
});
