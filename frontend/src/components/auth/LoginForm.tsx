import React, { useState, useRef } from 'react';
import { Lock, AlertCircle, CheckCircle } from 'lucide-react';
import { Button } from '../common/Button.tsx';
import { LABEL_CLASS } from '../common/Field.tsx';
import { LogShedLogo } from '../common/LogShedLogo.tsx';
import { useAuth } from '../../context/AuthContext.tsx';

interface LoginFormProps {
  /** Shown above the form, for a screen that asks for sign-in first (recovery mode). */
  notice?: React.ReactNode;
}

export const LoginForm: React.FC<LoginFormProps> = ({ notice }) => {
  const { login, error: authError } = useAuth();
  const [password, setPassword] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const passwordInputRef = useRef<HTMLInputElement>(null);
  const [noticeMsg, setNoticeMsg] = useState<string | null>(() => {
    if (typeof window !== 'undefined') {
      const notice = sessionStorage.getItem('login_notice');
      if (notice) {
        sessionStorage.removeItem('login_notice');
        return notice;
      }
    }
    return null;
  });

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!password.trim()) return;

    try {
      setIsLoading(true);
      setErrorMsg(null);
      setNoticeMsg(null);
      await login(password);
    } catch (err: any) {
      setErrorMsg(err.message || 'Login failed. Please check your password.');
      setTimeout(() => {
        passwordInputRef.current?.focus();
        passwordInputRef.current?.select();
      }, 0);
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <main className="min-h-screen bg-canvas flex flex-col items-center justify-center p-4">
      <div className="w-full max-w-md bg-surface border border-line rounded-xl p-8 shadow-2xl">
        {/* Brand Header */}
        <div className="flex flex-col items-center mb-6">
          <div className="w-12 h-12 rounded-xl bg-raised border border-line flex items-center justify-center mb-3">
            <LogShedLogo className="w-7 h-7 text-accent-600" />
          </div>
          <h1 className="text-xl font-bold text-fg-strong tracking-[-0.02em]">LogShed</h1>
          <p className="text-xs text-fg-muted mt-1">Single-Process Log Aggregator & Ops Console</p>
        </div>

        {notice && <div className="mb-4">{notice}</div>}

        {noticeMsg && (
          <div className="mb-4 p-3 bg-success-950/60 border border-success-800 rounded-lg flex items-start gap-x-2 text-xs text-success-300">
            <CheckCircle className="w-4 h-4 text-success-400 shrink-0 mt-0.5" />
            <span>{noticeMsg}</span>
          </div>
        )}

        {(errorMsg || (!noticeMsg && authError)) && (
          <div className="mb-4 p-3 bg-danger-950/60 border border-danger-800 rounded-lg flex items-start gap-x-2 text-xs text-danger-300">
            <AlertCircle className="w-4 h-4 text-danger-400 shrink-0 mt-0.5" />
            <span>{errorMsg || authError}</span>
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label htmlFor="login-password" className={LABEL_CLASS}>
              Admin Password
            </label>
            <div className="relative">
              <input
                id="login-password"
                ref={passwordInputRef}
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="Enter password..."
                required
                autoFocus
                className="w-full bg-canvas border border-line rounded-sm px-4 py-2.5 pl-10 text-base sm:text-sm text-fg-strong placeholder-fg-subtle focus:outline-hidden focus:border-accent-500 focus:ring-1 focus:ring-accent-500 font-mono"
              />
              <Lock className="w-4 h-4 text-fg-subtle absolute left-3.5 top-3" />
            </div>
          </div>

          <Button
            type="submit"
            variant="primary"
            size="lg"
            disabled={isLoading || !password.trim()}
            className="w-full"
          >
            {isLoading ? (
              <span>Authenticating...</span>
            ) : (
              <>
                <Lock className="w-4 h-4" />
                <span>Sign In to Console</span>
              </>
            )}
          </Button>
        </form>
      </div>
    </main>
  );
};
