import React, { useState, useEffect } from 'react';
import { CheckCircle2, AlertTriangle, RefreshCw, FlaskConical } from 'lucide-react';
import { AlertRule } from '../../types.ts';
import { testAlertRule } from '../../api/alerts.ts';
import { Modal } from '../common/Modal.tsx';

export interface AlertTestModalProps {
  isOpen: boolean;
  rule: AlertRule | null;
  availableApps: string[];
  onClose: () => void;
}

export const generateInitialSample = (rule: AlertRule): string => {
  const pattern = rule.match_pattern || '';
  if (!pattern) {
    return 'Sample test log line from 192.168.1.100';
  }

  // Handle known presets and common threat signatures
  if (pattern.includes('401') && pattern.includes('403')) {
    return '192.168.1.100 - - [19/Sep/2026:12:00:00 +0000] "GET /api/v1/auth HTTP/1.1" 401 Unauthorized';
  }
  if (
    pattern.includes('Failed password') ||
    pattern.includes('authentication failure') ||
    pattern.includes('invalid user')
  ) {
    return 'Failed password for root from 192.168.1.100 port 22';
  }
  if (pattern.includes('COMMAND=')) {
    return 'pam_unix(sudo:session): session opened for user root by admin(uid=0) COMMAND=/usr/bin/cat /etc/shadow from 192.168.1.100';
  }
  if (pattern.includes('Out of memory: Kill process')) {
    return 'kernel: Out of memory: Kill process 1234 (mysqld) score 950 or sacrifice child from 192.168.1.100';
  }

  // If pattern is a simple list of pipe-separated keywords without regex syntax
  if (/^[a-zA-Z0-9_\- ]+(\|[a-zA-Z0-9_\- ]+)+$/.test(pattern)) {
    const firstOption = pattern.split('|')[0].trim();
    return `Sample event matching ${firstOption} from 192.168.1.100`;
  }

  // If complex regex syntax is detected, avoid raw regex characters in sample payload
  const hasRegexSyntax = /[\\^$*+?.()|[\]{}]/.test(pattern);
  if (hasRegexSyntax) {
    return 'Sample test log payload from 192.168.1.100';
  }

  return `Sample event matching ${pattern} from 192.168.1.100`;
};

export const AlertTestModal: React.FC<AlertTestModalProps> = ({
  isOpen,
  rule,
  availableApps,
  onClose,
}) => {
  const [testSampleMessage, setTestSampleMessage] = useState<string>('');
  const [testSampleApp, setTestSampleApp] = useState<string>('');
  const [testSampleSeverity, setTestSampleSeverity] = useState<number>(6);
  const [isTesting, setIsTesting] = useState<boolean>(false);
  const [testResult, setTestResult] = useState<{
    matched: boolean;
    extracted_ip?: string | null;
    error?: string | null;
  } | null>(null);

  useEffect(() => {
    if (!isOpen || !rule) return;

    setTestSampleMessage(generateInitialSample(rule));
    setTestSampleApp(rule.filter_app ? rule.filter_app.split(',')[0].trim() : '');
    setTestSampleSeverity(
      rule.filter_severity !== null && rule.filter_severity !== undefined
        ? rule.filter_severity
        : 6
    );
    setTestResult(null);
  }, [isOpen, rule]);

  const handleRunTest = async () => {
    if (!rule) return;
    setIsTesting(true);
    setTestResult(null);
    try {
      const res = await testAlertRule({
        rule_type: rule.rule_type,
        filter_app: testSampleApp.trim() || null,
        filter_severity: testSampleSeverity,
        match_pattern: rule.match_pattern,
        sample_message: testSampleMessage,
        sample_app: testSampleApp.trim() || null,
        sample_severity: testSampleSeverity,
      });
      setTestResult(res);
    } catch (err: any) {
      setTestResult({ matched: false, error: err.message });
    } finally {
      setIsTesting(false);
    }
  };

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title={`Dry-Run Test: ${rule?.name || 'Alert Rule'}`}
      maxWidth="max-w-lg"
    >
      <div className="space-y-4 text-xs">
        <p className="text-slate-400">
          Verify pattern matching and IP address extraction against a sample log payload.
        </p>

        <div className="space-y-1">
          <label className="text-slate-300 font-medium">Active Pattern</label>
          <div className="p-2 bg-dark-800 rounded font-mono text-slate-300 border border-dark-700">
            {rule?.match_pattern || '(No pattern filter)'}
          </div>
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div className="space-y-1">
            <label className="text-slate-300 font-medium">Sample App</label>
            <input
              type="text"
              list="test-sample-apps-list"
              value={testSampleApp}
              onChange={(e) => setTestSampleApp(e.target.value)}
              className="w-full bg-dark-800 border border-dark-700 rounded px-2.5 py-1.5 text-slate-200"
            />
            {availableApps.length > 0 && (
              <datalist id="test-sample-apps-list">
                {availableApps.map((app) => (
                  <option key={app} value={app} />
                ))}
              </datalist>
            )}
          </div>
          <div className="space-y-1">
            <label className="text-slate-300 font-medium">Sample Severity</label>
            <select
              value={testSampleSeverity}
              onChange={(e) => setTestSampleSeverity(Number(e.target.value))}
              className="w-full bg-dark-800 border border-dark-700 rounded px-2.5 py-1.5 text-slate-200"
            >
              <option value="1">1 - Alert</option>
              <option value="2">2 - Critical</option>
              <option value="3">3 - Error</option>
              <option value="4">4 - Warning</option>
              <option value="6">6 - Info</option>
            </select>
          </div>
        </div>

        <div className="space-y-1">
          <label className="text-slate-300 font-medium">Sample Message</label>
          <textarea
            rows={3}
            value={testSampleMessage}
            onChange={(e) => setTestSampleMessage(e.target.value)}
            className="w-full bg-dark-800 border border-dark-700 rounded p-2 text-slate-200 font-mono text-xs focus:outline-hidden focus:border-accent-500"
          />
          <p className="text-[11px] text-slate-500">
            Enter a sample log line payload to evaluate pattern matching and IP extraction.
          </p>
        </div>

        {testResult && (
          <div
            className={`p-3 rounded-lg border text-xs space-y-1 ${
              testResult.matched
                ? 'bg-emerald-950/30 border-emerald-800/50 text-emerald-300'
                : 'bg-amber-950/30 border-amber-800/50 text-amber-300'
            }`}
          >
            <div className="font-semibold flex items-center gap-1.5">
              {testResult.matched ? (
                <CheckCircle2 className="w-4 h-4" />
              ) : (
                <AlertTriangle className="w-4 h-4" />
              )}
              <span>{testResult.matched ? 'Pattern Matched Successfully' : 'No Match Found'}</span>
            </div>
            {testResult.extracted_ip && (
              <div className="text-[11px] text-slate-300">
                Extracted IP address:{' '}
                <span className="font-mono text-white">{testResult.extracted_ip}</span>
              </div>
            )}
            {testResult.error && (
              <div className="text-[11px] text-red-300">
                Evaluation error: {testResult.error}
              </div>
            )}
          </div>
        )}

        <div className="pt-3 border-t border-dark-700 flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            className="px-3 py-1.5 rounded-lg text-xs font-medium text-slate-300 hover:text-white bg-dark-800 transition cursor-pointer"
          >
            Close
          </button>
          <button
            type="button"
            onClick={handleRunTest}
            disabled={isTesting}
            className="px-4 py-1.5 rounded-lg text-xs font-medium text-white bg-accent-600 hover:bg-accent-500 transition cursor-pointer flex items-center gap-1.5"
          >
            {isTesting ? (
              <RefreshCw className="w-3 h-3 animate-spin" />
            ) : (
              <FlaskConical className="w-3.5 h-3.5" />
            )}
            <span>Run Test</span>
          </button>
        </div>
      </div>
    </Modal>
  );
};
