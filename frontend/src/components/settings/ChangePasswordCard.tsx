import React, { useState } from 'react';
import { AlertCircle, Check, Lock } from 'lucide-react';
import { changePassword } from '../../api/auth.ts';
import { useAuth } from '../../context/AuthContext.tsx';
import { PASSWORD_MIN_LENGTH } from '../../utils/fieldLimits.ts';

/** Settings > Advanced: change the admin password, then sign out so the new one is used. */
export const ChangePasswordCard: React.FC = () => {
  const { logout } = useAuth();
  const [currentPwd, setCurrentPwd] = useState<string>('');
  const [newPwd, setNewPwd] = useState<string>('');
  const [confirmPwd, setConfirmPwd] = useState<string>('');
  const [isChangingPwd, setIsChangingPwd] = useState<boolean>(false);
  const [pwdMsg, setPwdMsg] = useState<{ text: string; isError: boolean } | null>(null);

  const handleChangePassword = async (e: React.FormEvent) => {
    e.preventDefault();
    if (newPwd.length < PASSWORD_MIN_LENGTH) {
      setPwdMsg({ text: `New password must be at least ${PASSWORD_MIN_LENGTH} characters long.`, isError: true });
      return;
    }
    if (newPwd !== confirmPwd) {
      setPwdMsg({ text: 'New passwords do not match.', isError: true });
      return;
    }

    try {
      setIsChangingPwd(true);
      setPwdMsg(null);
      await changePassword(currentPwd, newPwd);
      sessionStorage.setItem('login_notice', 'Admin password changed successfully. Please sign in with your new password.');
      setPwdMsg({ text: 'Admin password changed successfully. Redirecting to sign in...', isError: false });
      setCurrentPwd('');
      setNewPwd('');
      setConfirmPwd('');
      setTimeout(() => {
        logout();
      }, 1000);
    } catch (err: any) {
      setPwdMsg({ text: err.message || 'Failed to update password.', isError: true });
    } finally {
      setIsChangingPwd(false);
    }
  };

  return (
    <section className="bg-dark-900 border border-dark-700 rounded-xl p-3.5 sm:p-5 shadow-md space-y-4">
      <h3 className="text-xs font-semibold text-slate-300 uppercase tracking-wider flex items-center gap-2">
        <Lock className="w-4 h-4 text-accent-500" />
        <span>Change Admin Password</span>
      </h3>

      {pwdMsg && (
        <div
          className={`p-3 rounded-lg border text-xs flex items-start gap-2 ${pwdMsg.isError
              ? 'bg-red-950/60 border-red-800 text-red-300'
              : 'bg-emerald-950/60 border-emerald-800 text-emerald-300'
            }`}
        >
          {pwdMsg.isError ? (
            <AlertCircle className="w-4 h-4 text-red-400 shrink-0 mt-0.5" />
          ) : (
            <Check className="w-4 h-4 text-emerald-400 shrink-0 mt-0.5" />
          )}
          <span>{pwdMsg.text}</span>
        </div>
      )}

      <form onSubmit={handleChangePassword} className="grid grid-cols-1 sm:grid-cols-3 gap-3 items-end">
        <div>
          <label htmlFor="current-password" className="block text-[11px] font-semibold text-slate-400 uppercase mb-1">
            Current Password
          </label>
          <input
            id="current-password"
            type="password"
            value={currentPwd}
            onChange={(e) => setCurrentPwd(e.target.value)}
            required
            className="w-full bg-dark-950 border border-dark-700 rounded px-3 py-2 text-xs text-slate-100 placeholder-slate-500 focus:outline-none focus:border-accent-500 font-mono"
          />
        </div>

        <div>
          <label htmlFor="new-password" className="block text-[11px] font-semibold text-slate-400 uppercase mb-1">
            New Password (min {PASSWORD_MIN_LENGTH} chars)
          </label>
          <input
            id="new-password"
            type="password"
            value={newPwd}
            onChange={(e) => setNewPwd(e.target.value)}
            minLength={PASSWORD_MIN_LENGTH}
            required
            className="w-full bg-dark-950 border border-dark-700 rounded px-3 py-2 text-xs text-slate-100 placeholder-slate-500 focus:outline-none focus:border-accent-500 font-mono"
          />
        </div>

        <div>
          <label htmlFor="confirm-password" className="block text-[11px] font-semibold text-slate-400 uppercase mb-1">
            Confirm New Password
          </label>
          <input
            id="confirm-password"
            type="password"
            value={confirmPwd}
            onChange={(e) => setConfirmPwd(e.target.value)}
            minLength={PASSWORD_MIN_LENGTH}
            required
            className="w-full bg-dark-950 border border-dark-700 rounded px-3 py-2 text-xs text-slate-100 placeholder-slate-500 focus:outline-none focus:border-accent-500 font-mono"
          />
        </div>

        <div className="sm:col-span-3 flex justify-end pt-1">
          <button
            type="submit"
            disabled={isChangingPwd || !currentPwd || newPwd.length < PASSWORD_MIN_LENGTH || newPwd !== confirmPwd}
            className="w-full sm:w-auto justify-center bg-dark-800 hover:bg-dark-700 disabled:opacity-50 text-slate-200 border border-dark-600 font-medium px-4 py-2.5 sm:py-2 rounded-lg text-xs flex items-center gap-1.5 transition cursor-pointer min-h-[40px] sm:min-h-0"
          >
            <Lock className="w-3.5 h-3.5" />
            <span>{isChangingPwd ? 'Updating...' : 'Update Password'}</span>
          </button>
        </div>
      </form>
    </section>
  );
};
