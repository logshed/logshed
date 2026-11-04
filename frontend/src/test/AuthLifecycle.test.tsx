import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { AuthProvider, useAuth } from '../context/AuthContext.tsx';
import { SetupModal } from '../components/auth/SetupModal.tsx';
import { LoginForm } from '../components/auth/LoginForm.tsx';
import { apiFetch, apiFetchBlob, toApiError, ApiError } from '../api/client.ts';

// The real AuthProvider, SetupModal and LoginForm, with only fetch replaced. Gate picks the
// screen the way App does: setup, then sign-in, then the console.
function Gate() {
  const { isLoading, setupRequired, isAuthenticated, logout } = useAuth();
  if (isLoading) return <p>Loading</p>;
  if (setupRequired) return <SetupModal />;
  if (!isAuthenticated) return <LoginForm />;
  return (
    <div>
      <p>Console</p>
      <button onClick={() => apiFetch('/api/logs').catch(() => undefined)}>Load logs</button>
      <button onClick={() => logout().catch(() => undefined)}>Sign out</button>
    </div>
  );
}

type Route = { status: number; body?: unknown; text?: string };

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

/** Stub fetch with one answer per "METHOD path"; returns the mock to inspect calls. */
function stubFetch(routes: Record<string, Route | Route[]>) {
  const queues: Record<string, Route[]> = {};
  for (const [key, value] of Object.entries(routes)) queues[key] = Array.isArray(value) ? [...value] : [value];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const key = `${(init?.method || 'GET').toUpperCase()} ${String(input)}`;
    const queue = queues[key];
    if (!queue || queue.length === 0) throw new Error(`unexpected request ${key}`);
    const route = queue.length > 1 ? queue.shift()! : queue[0];
    if (route.text !== undefined) return new Response(route.text, { status: route.status });
    return json(route.status, route.body ?? {});
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

function renderApp() {
  return render(
    <AuthProvider>
      <Gate />
    </AuthProvider>
  );
}

const reload = vi.fn();
const originalLocation = window.location;

beforeEach(() => {
  reload.mockClear();
  Object.defineProperty(window, 'location', { configurable: true, value: { ...originalLocation, reload } });
  sessionStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
  Object.defineProperty(window, 'location', { configurable: true, value: originalLocation });
});

describe('Sign-in lifecycle (AuthProvider)', () => {
  it('shows first-run setup, refuses a short or mismatched password, and signs in after setup', async () => {
    const fetchMock = stubFetch({
      'GET /api/auth/status': { status: 200, body: { authenticated: false, setup_required: true } },
      'POST /api/auth/setup': { status: 200, body: { status: 'ok' } },
    });
    renderApp();

    expect(await screen.findByText('FIRST-RUN ADMIN SETUP')).toBeInTheDocument();
    const [password, confirm] = [
      screen.getByPlaceholderText('Enter new master password...'),
      screen.getByPlaceholderText('Confirm master password...'),
    ];
    const submit = screen.getByRole('button', { name: /Set Up Admin & Log In/i });

    fireEvent.change(password, { target: { value: 'short' } });
    fireEvent.change(confirm, { target: { value: 'short' } });
    expect(submit).toBeDisabled();
    fireEvent.submit(submit.closest('form')!);
    expect(screen.getByText('Password must be at least 8 characters long.')).toBeInTheDocument();

    fireEvent.change(password, { target: { value: 'correct-horse-1' } });
    fireEvent.change(confirm, { target: { value: 'correct-horse-2' } });
    fireEvent.submit(submit.closest('form')!);
    expect(screen.getByText('Passwords do not match.')).toBeInTheDocument();

    fireEvent.change(confirm, { target: { value: 'correct-horse-1' } });
    fireEvent.click(submit);

    expect(await screen.findByText('Console')).toBeInTheDocument();
    const setupCall = fetchMock.mock.calls.find(([url]) => url === '/api/auth/setup')!;
    expect(JSON.parse(String(setupCall[1]!.body))).toEqual({ password: 'correct-horse-1' });
    expect(new Headers(setupCall[1]!.headers).get('X-Requested-With')).toBe('XMLHttpRequest');
  });

  it('shows the server message when setup fails and stays on the setup screen', async () => {
    stubFetch({
      'GET /api/auth/status': { status: 200, body: { authenticated: false, setup_required: true } },
      'POST /api/auth/setup': { status: 409, body: { detail: 'Admin is already set up.' } },
    });
    renderApp();

    fireEvent.change(await screen.findByPlaceholderText('Enter new master password...'), { target: { value: 'correct-horse-1' } });
    fireEvent.change(screen.getByPlaceholderText('Confirm master password...'), { target: { value: 'correct-horse-1' } });
    fireEvent.click(screen.getByRole('button', { name: /Set Up Admin & Log In/i }));

    expect(await screen.findByText('Admin is already set up.')).toBeInTheDocument();
    expect(screen.getByText('FIRST-RUN ADMIN SETUP')).toBeInTheDocument();
  });

  it('a 401 from any other API call returns the user to sign-in with its message', async () => {
    stubFetch({
      'GET /api/auth/status': { status: 200, body: { authenticated: true, setup_required: false } },
      'GET /api/logs': { status: 401, body: { detail: 'Session expired. Please sign in again.' } },
    });
    renderApp();

    fireEvent.click(await screen.findByRole('button', { name: 'Load logs' }));

    expect(await screen.findByRole('button', { name: /Sign In to Console/i })).toBeInTheDocument();
    expect(screen.getByText('Session expired. Please sign in again.')).toBeInTheDocument();
    expect(screen.queryByText('Console')).toBeNull();
  });

  it('a wrong password at sign-in shows the error without the session-expired event', async () => {
    const onUnauthorized = vi.fn();
    window.addEventListener('logshed:unauthorized', onUnauthorized);
    stubFetch({
      'GET /api/auth/status': { status: 200, body: { authenticated: false, setup_required: false } },
      'POST /api/auth/login': [
        { status: 401, body: { detail: 'Invalid password.' } },
        { status: 200, body: { status: 'ok' } },
      ],
    });
    renderApp();

    const input = await screen.findByPlaceholderText('Enter password...');
    fireEvent.change(input, { target: { value: 'wrong-password' } });
    fireEvent.click(screen.getByRole('button', { name: /Sign In to Console/i }));
    expect(await screen.findByText('Invalid password.')).toBeInTheDocument();
    expect(onUnauthorized).not.toHaveBeenCalled();

    fireEvent.change(input, { target: { value: 'right-password' } });
    fireEvent.click(screen.getByRole('button', { name: /Sign In to Console/i }));
    expect(await screen.findByText('Console')).toBeInTheDocument();
    window.removeEventListener('logshed:unauthorized', onUnauthorized);
  });

  it('sign-out calls the logout endpoint, clears the session state and reloads', async () => {
    const fetchMock = stubFetch({
      'GET /api/auth/status': { status: 200, body: { authenticated: true, setup_required: false } },
      'POST /api/auth/logout': { status: 200, body: { status: 'ok' } },
    });
    renderApp();

    fireEvent.click(await screen.findByRole('button', { name: 'Sign out' }));

    expect(await screen.findByRole('button', { name: /Sign In to Console/i })).toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([url, init]) => url === '/api/auth/logout' && init?.method === 'POST')).toBe(true);
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it('sign-out still clears the state and reloads when the logout request fails', async () => {
    stubFetch({
      'GET /api/auth/status': { status: 200, body: { authenticated: true, setup_required: false } },
      'POST /api/auth/logout': { status: 500, text: 'boom' },
    });
    renderApp();

    fireEvent.click(await screen.findByRole('button', { name: 'Sign out' }));

    await waitFor(() => expect(reload).toHaveBeenCalledTimes(1));
    expect(await screen.findByRole('button', { name: /Sign In to Console/i })).toBeInTheDocument();
  });

  it('a failed status check shows its error on the sign-in screen', async () => {
    stubFetch({ 'GET /api/auth/status': { status: 503, body: { detail: 'Database is starting up.' } } });
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    renderApp();

    expect(await screen.findByText('Database is starting up.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Sign In to Console/i })).toBeInTheDocument();
  });
});

describe('API client errors', () => {
  it('joins a FastAPI validation error list into one message', async () => {
    const err = await toApiError(
      json(422, { detail: [{ msg: 'field required' }, 'too long', { message: 'bad value' }] }),
      '/api/settings'
    );
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(422);
    expect(err.message).toBe('field required; too long; bad value');
  });

  it('uses a plain text body, and a default message for an empty one', async () => {
    expect((await toApiError(new Response('upstream timeout', { status: 502 }), '/api/x')).message).toBe('upstream timeout');
    expect((await toApiError(new Response('', { status: 500 }), '/api/x')).message).toBe('Request failed with status 500');
    expect((await toApiError(json(400, { detail: { code: 7 } }), '/api/x')).message).toBe('{"code":7}');
  });

  it('dispatches the session-expired event for a 401 except from login and setup', async () => {
    const onUnauthorized = vi.fn();
    window.addEventListener('logshed:unauthorized', onUnauthorized);

    await toApiError(json(401, { detail: 'Wrong password' }), '/api/auth/login');
    await toApiError(json(401, { detail: 'Already set up' }), '/api/auth/setup');
    expect(onUnauthorized).not.toHaveBeenCalled();

    await toApiError(json(401, { detail: 'Session expired' }), '/api/alerts/rules');
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
    expect((onUnauthorized.mock.calls[0][0] as CustomEvent).detail).toEqual({ message: 'Session expired' });
    window.removeEventListener('logshed:unauthorized', onUnauthorized);
  });

  it('apiFetchBlob returns the body as a blob and throws an ApiError on failure', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) =>
      String(input) === '/api/export/logs'
        ? new Response('a,b\n1,2\n', { status: 200 })
        : json(404, { detail: 'Not found' })
    );
    vi.stubGlobal('fetch', fetchMock);

    const blob = await apiFetchBlob('/api/export/logs');
    // Node.js 20 returns its own Blob; Node.js 24 returns jsdom's, which has no text() but works with FileReader.
    const text = typeof blob.text === 'function'
      ? await blob.text()
      : await new Promise<string>((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result as string);
        reader.onerror = () => reject(reader.error);
        reader.readAsText(blob);
      });
    expect(text).toBe('a,b\n1,2\n');
    expect(new Headers(fetchMock.mock.calls[0][1]!.headers).get('X-Requested-With')).toBe('XMLHttpRequest');
    expect(fetchMock.mock.calls[0][1]!.credentials).toBe('include');

    await expect(apiFetchBlob('/api/export/missing')).rejects.toMatchObject({ status: 404, message: 'Not found' });
  });
});
