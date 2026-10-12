import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { DockerHostsTab } from '../components/settings/DockerHostsTab.tsx';
import * as dockerHostsApi from '../api/dockerHosts.ts';
import { DockerEndpoint } from '../types.ts';

const mockEndpoints: DockerEndpoint[] = [
  {
    id: 1,
    name: 'Primary Docker',
    url: 'unix:///var/run/docker.sock',
    source_alias: 'docker',
    enabled: true,
    tls_enabled: false,
    ca_cert: null,
    client_cert: null,
    has_client_key: false,
    exclude_containers: 'gluetun',
    created_at: '2026-10-10T10:00:00Z',
    updated_at: '2026-10-10T10:00:00Z',
    is_connected: true,
    active_containers: 4,
    latency_ms: 1.5,
  },
  {
    id: 2,
    name: 'Remote Worker',
    url: 'tcp://192.168.1.50:2375',
    source_alias: 'worker-1',
    enabled: true,
    tls_enabled: false,
    ca_cert: null,
    client_cert: null,
    has_client_key: false,
    exclude_containers: '',
    created_at: '2026-10-10T11:00:00Z',
    updated_at: '2026-10-10T11:00:00Z',
    is_connected: false,
    active_containers: 0,
    last_error: 'Connection refused',
  },
];

describe('DockerHostsTab Component', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('renders empty state when no Docker endpoints exist', async () => {
    vi.spyOn(dockerHostsApi, 'fetchDockerEndpoints').mockResolvedValue([]);

    render(<DockerHostsTab />);

    await waitFor(() => {
      expect(screen.getByText('No Docker hosts configured')).toBeInTheDocument();
    });

    expect(screen.getByText('Docker Host Endpoints')).toBeInTheDocument();
    expect(screen.getByText('Configured Docker Hosts (0)')).toBeInTheDocument();
  });

  it('renders configured Docker endpoints with status and primary badge', async () => {
    vi.spyOn(dockerHostsApi, 'fetchDockerEndpoints').mockResolvedValue(mockEndpoints);

    render(<DockerHostsTab />);

    await waitFor(() => {
      expect(screen.getByText('Configured Docker Hosts (2)')).toBeInTheDocument();
    });

    expect(screen.getByText('Primary Docker')).toBeInTheDocument();
    expect(screen.getByText('Remote Worker')).toBeInTheDocument();
    expect(screen.getByText('Primary')).toBeInTheDocument();
    expect(screen.getByText('Connected')).toBeInTheDocument();
    expect(screen.getByText('Error')).toBeInTheDocument();
  });

  it('automatically derives source alias from host name when adding', async () => {
    vi.spyOn(dockerHostsApi, 'fetchDockerEndpoints').mockResolvedValue([]);
    render(<DockerHostsTab />);

    await waitFor(() => {
      expect(screen.getByText('No Docker hosts configured')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: /Add Docker Host/i }));

    const nameInput = screen.getByLabelText(/Host Name/i);
    const aliasInput = screen.getByLabelText(/^Host Alias/i) as HTMLInputElement;

    fireEvent.change(nameInput, { target: { value: 'Unraid Server' } });
    expect(aliasInput.value).toBe('unraid-server');
  });

  it('opens add modal and creates a new endpoint', async () => {
    vi.spyOn(dockerHostsApi, 'fetchDockerEndpoints').mockResolvedValue(mockEndpoints);
    const createSpy = vi.spyOn(dockerHostsApi, 'createDockerEndpoint').mockResolvedValue({
      id: 3,
      name: 'Mini PC',
      url: 'tcp://192.168.1.60:2375',
      source_alias: 'minipc',
      enabled: true,
      tls_enabled: false,
      exclude_containers: '',
      created_at: '2026-10-11T12:00:00Z',
      updated_at: '2026-10-11T12:00:00Z',
    });

    render(<DockerHostsTab />);

    await waitFor(() => {
      expect(screen.getByText('Configured Docker Hosts (2)')).toBeInTheDocument();
    });

    const addBtn = screen.getByRole('button', { name: /Add Docker Host/i });
    fireEvent.click(addBtn);

    expect(screen.getByRole('heading', { name: 'Add Docker Host' })).toBeInTheDocument();

    const nameInput = screen.getByLabelText(/Host Name/i);
    const urlInput = screen.getByLabelText(/Docker Endpoint URL/i);
    const aliasInput = screen.getByLabelText(/^Host Alias/i);

    fireEvent.change(nameInput, { target: { value: 'Mini PC' } });
    fireEvent.change(urlInput, { target: { value: 'tcp://192.168.1.60:2375' } });
    fireEvent.change(aliasInput, { target: { value: 'minipc' } });

    const submitBtn = screen.getByRole('button', { name: 'Add Host' });
    fireEvent.click(submitBtn);

    await waitFor(() => {
      expect(createSpy).toHaveBeenCalledWith(
        expect.objectContaining({
          name: 'Mini PC',
          url: 'tcp://192.168.1.60:2375',
          source_alias: 'minipc',
        })
      );
    });
  });

  it('tests connection from within add modal', async () => {
    vi.spyOn(dockerHostsApi, 'fetchDockerEndpoints').mockResolvedValue([]);
    const testSpy = vi.spyOn(dockerHostsApi, 'testDockerEndpoint').mockResolvedValue({
      success: true,
      version: '27.3.1',
      latency_ms: 12.4,
    });

    render(<DockerHostsTab />);

    await waitFor(() => {
      expect(screen.getByText('No Docker hosts configured')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: /Add Docker Host/i }));

    const urlInput = screen.getByLabelText(/Docker Endpoint URL/i);
    fireEvent.change(urlInput, { target: { value: 'tcp://192.168.1.50:2375' } });

    const testBtn = screen.getAllByRole('button', { name: /Test Connection/i })[0];
    fireEvent.click(testBtn);

    await waitFor(() => {
      expect(testSpy).toHaveBeenCalledWith(
        expect.objectContaining({
          url: 'tcp://192.168.1.50:2375',
        })
      );
      expect(screen.getByText(/Connection verified successfully/i)).toBeInTheDocument();
    });
  });

  it('deletes a secondary endpoint with confirmation', async () => {
    vi.spyOn(dockerHostsApi, 'fetchDockerEndpoints').mockResolvedValue(mockEndpoints);
    const deleteSpy = vi.spyOn(dockerHostsApi, 'deleteDockerEndpoint').mockResolvedValue({
      status: 'ok',
    });

    render(<DockerHostsTab />);

    await waitFor(() => {
      expect(screen.getByText('Remote Worker')).toBeInTheDocument();
    });

    const deleteBtns = screen.getAllByTitle('Delete Host');
    expect(deleteBtns.length).toBe(1); // Only for host id 2

    fireEvent.click(deleteBtns[0]);

    expect(screen.getByRole('heading', { name: 'Remove Docker Host' })).toBeInTheDocument();
    expect(screen.getByText(/Are you sure you want to remove Docker host/i)).toBeInTheDocument();

    const confirmBtn = screen.getByRole('button', { name: 'Remove Host' });
    fireEvent.click(confirmBtn);

    await waitFor(() => {
      expect(deleteSpy).toHaveBeenCalledWith(2);
    });
  });
});
