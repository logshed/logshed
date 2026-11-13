import React, { useState } from 'react';
import { AlertCircle, Check, Lock } from 'lucide-react';
import { changePassword } from '../../api/auth.ts';
import { useAuth } from '../../context/AuthContext.tsx';
import { PASSWORD_MIN_LENGTH } from '../../utils/fieldLimits.ts';
import { Button } from '../common/Button.tsx';
import { Card, CardHeader } from '../common/Card.tsx';
import { TextField } from '../common/Field.tsx';

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
    <Card>
      <CardHeader icon={<Lock />} title="Change Admin Password" />

      {pwdMsg && (
        <div
          className={`p-3 rounded-lg border text-xs flex items-start gap-2 ${pwdMsg.isError
              ? 'bg-danger-950/60 border-danger-800 text-danger-300'
              : 'bg-success-950/60 border-success-800 text-success-300'
            }`}
        >
          {pwdMsg.isError ? (
            <AlertCircle className="w-4 h-4 text-danger-400 shrink-0 mt-0.5" />
          ) : (
            <Check className="w-4 h-4 text-success-400 shrink-0 mt-0.5" />
          )}
          <span>{pwdMsg.text}</span>
        </div>
      )}

      <form onSubmit={handleChangePassword} className="grid grid-cols-1 sm:grid-cols-3 gap-3 items-end">
        <TextField
          label="Current Password"
          id="current-password"
          type="password"
          value={currentPwd}
          onChange={(e) => setCurrentPwd(e.target.value)}
          required
          mono
        />

        <TextField
          label={`New Password (min ${PASSWORD_MIN_LENGTH} chars)`}
          id="new-password"
          type="password"
          value={newPwd}
          onChange={(e) => setNewPwd(e.target.value)}
          minLength={PASSWORD_MIN_LENGTH}
          required
          mono
        />

        <TextField
          label="Confirm New Password"
          id="confirm-password"
          type="password"
          value={confirmPwd}
          onChange={(e) => setConfirmPwd(e.target.value)}
          minLength={PASSWORD_MIN_LENGTH}
          required
          mono
        />

        <div className="sm:col-span-3 flex justify-end pt-1">
          <Button
            type="submit"
            disabled={isChangingPwd || !currentPwd || newPwd.length < PASSWORD_MIN_LENGTH || newPwd !== confirmPwd}
            icon={<Lock className="w-3.5 h-3.5" />}
            className="w-full sm:w-auto"
          >
            {isChangingPwd ? 'Updating...' : 'Update Password'}
          </Button>
        </div>
      </form>
    </Card>
  );
};
