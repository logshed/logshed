import React, { useState } from 'react';
import { ShieldCheck, Lock, AlertCircle } from 'lucide-react';
import { useAuth } from '../../context/AuthContext.tsx';
import { PASSWORD_MIN_LENGTH } from '../../utils/fieldLimits.ts';

export const SetupModal: React.FC = () => {
  const { setup } = useAuth();
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (password.length < PASSWORD_MIN_LENGTH) {
      setErrorMsg(`Password must be at least ${PASSWORD_MIN_LENGTH} characters long.`);
      return;
    }
    if (password !== confirmPassword) {
      setErrorMsg('Passwords do not match.');
      return;
    }

    try {
      setIsLoading(true);
      setErrorMsg(null);
      await setup(password);
    } catch (err: any) {
      setErrorMsg(err.message || 'Setup failed. Please try again.');
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/80 backdrop-blur-xs">
      <div className="w-full max-w-md bg-dark-900 border border-dark-700 rounded-xl p-8 shadow-2xl">
        <div className="flex flex-col items-center mb-6">
          <div className="w-12 h-12 rounded-xl bg-accent-950/80 border border-accent-800/60 flex items-center justify-center mb-3">
            <ShieldCheck className="w-6 h-6 text-accent-400" />
          </div>
          <h1 className="text-xl font-bold text-slate-100 tracking-wide">FIRST-RUN ADMIN SETUP</h1>
          <p className="text-xs text-slate-400 mt-1 text-center">
            Set up your master administrator password for LogShed.
          </p>
        </div>

        {errorMsg && (
          <div className="mb-4 p-3 bg-red-950/60 border border-red-800 rounded-lg flex items-start space-x-2 text-xs text-red-300">
            <AlertCircle className="w-4 h-4 text-red-400 shrink-0 mt-0.5" />
            <span>{errorMsg}</span>
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-xs font-semibold text-slate-300 uppercase tracking-wider mb-2">
              Master Password (min {PASSWORD_MIN_LENGTH} chars)
            </label>
            <div className="relative">
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="Enter new master password..."
                minLength={PASSWORD_MIN_LENGTH}
                required
                autoFocus
                className="w-full bg-dark-950 border border-dark-700 rounded-lg px-4 py-2.5 pl-10 text-base sm:text-sm text-slate-100 placeholder-slate-500 focus:outline-hidden focus:border-accent-500 font-mono"
              />
              <Lock className="w-4 h-4 text-slate-500 absolute left-3.5 top-3" />
            </div>
          </div>

          <div>
            <label className="block text-xs font-semibold text-slate-300 uppercase tracking-wider mb-2">
              Confirm Master Password
            </label>
            <div className="relative">
              <input
                type="password"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                placeholder="Confirm master password..."
                minLength={PASSWORD_MIN_LENGTH}
                required
                className="w-full bg-dark-950 border border-dark-700 rounded-lg px-4 py-2.5 pl-10 text-base sm:text-sm text-slate-100 placeholder-slate-500 focus:outline-hidden focus:border-accent-500 font-mono"
              />
              <Lock className="w-4 h-4 text-slate-500 absolute left-3.5 top-3" />
            </div>
          </div>

          <button
            type="submit"
            disabled={isLoading || password.length < PASSWORD_MIN_LENGTH || password !== confirmPassword}
            className="w-full bg-accent-600 hover:bg-accent-500 disabled:opacity-50 text-white font-medium py-2.5 px-4 rounded-lg text-sm transition-all shadow-md flex items-center justify-center space-x-2"
          >
            {isLoading ? (
              <span>Setting up Account...</span>
            ) : (
              <>
                <ShieldCheck className="w-4 h-4" />
                <span>Set Up Admin & Log In</span>
              </>
            )}
          </button>
        </form>
      </div>
    </div>
  );
};
