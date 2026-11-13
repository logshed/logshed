import React, { useState, useRef } from 'react';
import { Lock, AlertCircle, CheckCircle } from 'lucide-react';
import { LogShedLogo } from '../common/LogShedLogo.tsx';
import { useAuth } from '../../context/AuthContext.tsx';

export const LoginForm: React.FC = () => {
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
    <main className="min-h-screen bg-dark-950 flex flex-col items-center justify-center p-4">
      <div className="w-full max-w-md bg-dark-900 border border-dark-700 rounded-xl p-8 shadow-2xl">
        {/* Brand Header */}
        <div className="flex flex-col items-center mb-6">
          <div className="w-12 h-12 rounded-xl bg-dark-800 border border-dark-700 flex items-center justify-center mb-3">
            <LogShedLogo className="w-7 h-7 text-accent-600" />
          </div>
          <h1 className="text-xl font-bold text-slate-50 tracking-[-0.02em]">LogShed</h1>
          <p className="text-xs text-slate-400 mt-1">Single-Process Log Aggregator & Ops Console</p>
        </div>

        {noticeMsg && (
          <div className="mb-4 p-3 bg-emerald-950/60 border border-emerald-800 rounded-lg flex items-start gap-x-2 text-xs text-emerald-300">
            <CheckCircle className="w-4 h-4 text-emerald-400 shrink-0 mt-0.5" />
            <span>{noticeMsg}</span>
          </div>
        )}

        {(errorMsg || (!noticeMsg && authError)) && (
          <div className="mb-4 p-3 bg-red-950/60 border border-red-800 rounded-lg flex items-start gap-x-2 text-xs text-red-300">
            <AlertCircle className="w-4 h-4 text-red-400 shrink-0 mt-0.5" />
            <span>{errorMsg || authError}</span>
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-xs font-semibold text-slate-300 uppercase tracking-wider mb-2">
              Admin Password
            </label>
            <div className="relative">
              <input
                ref={passwordInputRef}
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="Enter password..."
                required
                autoFocus
                className="w-full bg-dark-950 border border-dark-700 rounded-lg px-4 py-2.5 pl-10 text-base sm:text-sm text-slate-100 placeholder-slate-500 focus:outline-hidden focus:border-accent-500 focus:ring-1 focus:ring-accent-500 font-mono"
              />
              <Lock className="w-4 h-4 text-slate-500 absolute left-3.5 top-3" />
            </div>
          </div>

          <button
            type="submit"
            disabled={isLoading || !password.trim()}
            className="w-full bg-accent-700 hover:bg-accent-600 disabled:opacity-50 text-white font-medium py-2.5 px-4 rounded-lg text-sm transition-all shadow-md flex items-center justify-center gap-x-2"
          >
            {isLoading ? (
              <span>Authenticating...</span>
            ) : (
              <>
                <Lock className="w-4 h-4" />
                <span>Sign In to Console</span>
              </>
            )}
          </button>
        </form>
      </div>
    </main>
  );
};
