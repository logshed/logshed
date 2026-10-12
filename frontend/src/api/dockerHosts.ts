import { apiFetch } from './client.ts';
import {
  DockerEndpoint,
  DockerEndpointCreate,
  DockerEndpointUpdate,
  DockerEndpointTestRequest,
  DockerEndpointTestResult,
} from '../types.ts';

export async function fetchDockerEndpoints(): Promise<DockerEndpoint[]> {
  return apiFetch<DockerEndpoint[]>('/api/docker/hosts');
}

export async function createDockerEndpoint(data: DockerEndpointCreate): Promise<DockerEndpoint> {
  return apiFetch<DockerEndpoint>('/api/docker/hosts', {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export async function updateDockerEndpoint(
  id: number,
  data: DockerEndpointUpdate
): Promise<DockerEndpoint> {
  return apiFetch<DockerEndpoint>(`/api/docker/hosts/${id}`, {
    method: 'PUT',
    body: JSON.stringify(data),
  });
}

export async function deleteDockerEndpoint(id: number): Promise<{ status: string }> {
  return apiFetch<{ status: string }>(`/api/docker/hosts/${id}`, {
    method: 'DELETE',
  });
}

export async function testDockerEndpoint(
  data: DockerEndpointTestRequest
): Promise<DockerEndpointTestResult> {
  return apiFetch<DockerEndpointTestResult>('/api/docker/hosts/test', {
    method: 'POST',
    body: JSON.stringify(data),
  });
}
