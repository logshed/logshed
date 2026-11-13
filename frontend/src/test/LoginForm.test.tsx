import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { LoginForm } from '../components/auth/LoginForm.tsx';

const mockLogin = vi.fn();
let mockAuthError: string | null = null;

vi.mock('../context/AuthContext.tsx', () => ({
  useAuth: () => ({
    login: mockLogin,
    error: mockAuthError,
  }),
}));

describe('LoginForm Component', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockAuthError = null;
    sessionStorage.clear();
  });

  it('renders login form elements properly', () => {
    render(<LoginForm />);
    expect(screen.getByRole('main')).toContainElement(screen.getByRole('heading', { level: 1 }));

    expect(screen.getByRole('heading', { level: 1, name: 'LogShed' })).toBeInTheDocument();
    expect(screen.getByPlaceholderText('Enter password...')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Sign In to Console/i })).toBeInTheDocument();
  });

  it('displays and clears one-time login notice from sessionStorage', () => {
    sessionStorage.setItem('login_notice', 'Password updated. Please sign in again.');

    render(<LoginForm />);

    expect(screen.getByText('Password updated. Please sign in again.')).toBeInTheDocument();
    expect(sessionStorage.getItem('login_notice')).toBeNull();
  });

  it('displays auth error from context when no notice is set', () => {
    mockAuthError = 'This session was ended by a sign-out or a password change. Please log in again.';

    render(<LoginForm />);

    expect(screen.getByText('This session was ended by a sign-out or a password change. Please log in again.')).toBeInTheDocument();
  });

  it('submits password when valid', async () => {
    mockLogin.mockResolvedValueOnce(undefined);

    render(<LoginForm />);

    const input = screen.getByPlaceholderText('Enter password...');
    const submitBtn = screen.getByRole('button', { name: /Sign In to Console/i });

    fireEvent.change(input, { target: { value: 'correctpassword' } });
    fireEvent.click(submitBtn);

    await waitFor(() => {
      expect(mockLogin).toHaveBeenCalledWith('correctpassword');
    });
  });

  it('focuses and selects the password input when login fails', async () => {
    mockLogin.mockRejectedValueOnce(new Error('Invalid password.'));

    render(<LoginForm />);

    const input = screen.getByPlaceholderText('Enter password...') as HTMLInputElement;
    const submitBtn = screen.getByRole('button', { name: /Sign In to Console/i });

    const selectSpy = vi.spyOn(input, 'select');
    const focusSpy = vi.spyOn(input, 'focus');

    fireEvent.change(input, { target: { value: 'wrongpassword' } });
    fireEvent.click(submitBtn);

    await waitFor(() => {
      expect(screen.getByText('Invalid password.')).toBeInTheDocument();
    });

    await waitFor(() => {
      expect(focusSpy).toHaveBeenCalled();
      expect(selectSpy).toHaveBeenCalled();
    });
  });
});
