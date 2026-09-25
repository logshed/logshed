export class ApiError extends Error {
  status: number;
  data: any;

  constructor(status: number, message: string, data?: any) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.data = data;
  }
}

export async function apiFetch<T>(endpoint: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers || {});

  if (!headers.has('X-Requested-With')) {
    headers.set('X-Requested-With', 'XMLHttpRequest');
  }

  if (options.body && !(options.body instanceof FormData) && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }

  const response = await fetch(endpoint, {
    ...options,
    headers,
    credentials: 'include', // SameSite=Lax session cookie
  });

  if (!response.ok) {
    let errorMsg = `Request failed with status ${response.status}`;
    let errorData = null;
    try {
      const text = await response.text();
      try {
        errorData = JSON.parse(text);
        if (errorData?.detail) {
          if (typeof errorData.detail === 'string') {
            errorMsg = errorData.detail;
          } else if (Array.isArray(errorData.detail)) {
            errorMsg = errorData.detail
              .map((d: any) => (typeof d === 'string' ? d : d.msg || d.message || JSON.stringify(d)))
              .join('; ');
          } else {
            errorMsg = JSON.stringify(errorData.detail);
          }
        } else if (text && text.trim().length > 0) {
          errorMsg = text;
        }
      } catch {
        if (text && text.trim().length > 0) {
          errorMsg = text;
        }
      }
    } catch {
      // Fallback to default message
    }

    if (
      response.status === 401 &&
      !endpoint.includes('/api/auth/login') &&
      !endpoint.includes('/api/auth/setup')
    ) {
      if (typeof window !== 'undefined') {
        window.dispatchEvent(
          new CustomEvent('logshed:unauthorized', { detail: { message: errorMsg } })
        );
      }
    }

    throw new ApiError(response.status, errorMsg, errorData);
  }

  // Handle 204 No Content
  if (response.status === 204) {
    return {} as T;
  }

  return response.json();
}

export async function apiFetchBlob(endpoint: string, options: RequestInit = {}): Promise<Blob> {
  const headers = new Headers(options.headers || {});

  if (!headers.has('X-Requested-With')) {
    headers.set('X-Requested-With', 'XMLHttpRequest');
  }

  const response = await fetch(endpoint, {
    ...options,
    headers,
    credentials: 'include',
  });

  if (!response.ok) {
    let errorMsg = `Request failed with status ${response.status}`;
    let errorData = null;
    try {
      const text = await response.text();
      try {
        errorData = JSON.parse(text);
        if (errorData?.detail) {
          if (typeof errorData.detail === 'string') {
            errorMsg = errorData.detail;
          } else if (Array.isArray(errorData.detail)) {
            errorMsg = errorData.detail
              .map((d: any) => (typeof d === 'string' ? d : d.msg || d.message || JSON.stringify(d)))
              .join('; ');
          } else {
            errorMsg = JSON.stringify(errorData.detail);
          }
        } else if (text && text.trim().length > 0) {
          errorMsg = text;
        }
      } catch {
        if (text && text.trim().length > 0) {
          errorMsg = text;
        }
      }
    } catch {
      // Fallback to default message
    }

    if (
      response.status === 401 &&
      !endpoint.includes('/api/auth/login') &&
      !endpoint.includes('/api/auth/setup')
    ) {
      if (typeof window !== 'undefined') {
        window.dispatchEvent(
          new CustomEvent('logshed:unauthorized', { detail: { message: errorMsg } })
        );
      }
    }

    throw new ApiError(response.status, errorMsg, errorData);
  }

  return response.blob();
}
