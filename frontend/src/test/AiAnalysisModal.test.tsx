import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { AiAnalysisModal } from '../components/ai/AiAnalysisModal.tsx';
import { LogEntry } from '../types.ts';
import * as aiApi from '../api/ai.ts';
import * as clipboardUtil from '../utils/clipboard.ts';

describe('AiAnalysisModal Component (Items #10, #23, #26, #27, #28)', () => {
  const sampleLogs: LogEntry[] = [
    {
      id: 1,
      timestamp: '2026-09-04T08:00:00.000Z',
      received_at: '2026-09-04T08:00:00.100Z',
      source_ip: '192.168.1.1',
      source_alias: 'router',
      app_name: 'dnsmasq',
      facility: 1,
      severity: 3,
      message: 'query failed: upstream DNS server refused connection',
      raw: 'raw dnsmasq log',
    },
    {
      id: 2,
      timestamp: '2026-09-04T08:00:01.000Z',
      received_at: '2026-09-04T08:00:01.100Z',
      source_ip: '192.168.1.1',
      source_alias: 'router',
      app_name: 'dnsmasq',
      facility: 1,
      severity: 4,
      message: 'retrying upstream DNS server at 1.1.1.1:53',
      raw: 'raw dnsmasq retry',
    },
  ];

  const samplePreview = {
    redacted_prompt:
      "### System Metadata\n- Host / Source: router\n- Container / Service: dnsmasq\n- Total Selected Logs: 2\n\n### Redacted Log Stream (Chronological)\n```\n[2026-09-04T08:00:00.000Z] [dnsmasq] query failed: upstream DNS server refused connection\n[2026-09-04T08:00:01.000Z] [dnsmasq] retrying upstream DNS server at 1.1.1.1:53\n```\n\nPlease review these logs and provide Summary, Root Cause, and Actionable Remediation.",
    estimated_tokens: 280,
    provider: 'gemini',
    model: 'gemini-3.7-flash',
    log_count: 2,
    source_alias: 'router',
    app_name: 'dnsmasq',
    system_prompt:
      'You are an expert systems engineer, site reliability engineer (SRE), and Linux/Docker administrator.\nReview the following redacted server/container logs and provide a structured diagnosis in Markdown format.\n\nYour response MUST include the following three sections with exact headers:\n## Summary\nA concise 1-2 sentence overview of the issue.\n\n## Root Cause\nA detailed explanation of why the event or failure occurred based on the log evidence.\n\n## Actionable Remediation\nStep-by-step commands, configuration fixes, or debugging steps to resolve the issue.',
  };

  beforeEach(() => {
    vi.clearAllMocks();
    vi.spyOn(aiApi, 'previewAiPrompt').mockResolvedValue(samplePreview);
    vi.spyOn(aiApi, 'diagnoseLogs').mockResolvedValue({
      summary: 'DNS server connection refused.',
      root_cause: 'Upstream 1.1.1.1 DNS is unreachable.',
      remediation: 'Check firewall routing and DNS configuration.',
      model_used: 'gemini-3.7-flash',
      tokens_in: 285,
      tokens_out: 45,
      tokens_thoughts: 0,
      tokens_used: 330,
      audit_id: 12,
    });
    vi.spyOn(aiApi, 'getAiModels').mockResolvedValue({
      provider: 'gemini',
      models: [
        { id: 'gemini-3.7-flash', name: 'Gemini 3.7 Flash', supports_thinking: true },
        { id: 'gemini-2.5-flash', name: 'Gemini 2.5 Flash', supports_thinking: true },
        { id: 'gemini-2.5-pro', name: 'Gemini 2.5 Pro', supports_thinking: true },
      ],
      has_api_key: true,
      cached_at: null,
      is_live: true,
    });
  });

  it('renders an editable textarea instead of read-only div with full prompt visibility', async () => {
    render(
      <AiAnalysisModal
        isOpen={true}
        onClose={vi.fn()}
        selectedLogs={sampleLogs}
      />
    );

    await waitFor(() => {
      expect(aiApi.previewAiPrompt).toHaveBeenCalledWith({ log_ids: [1, 2] });
    });

    // The prompt is rendered inside an editable textarea
    const promptTextarea = await screen.findByPlaceholderText('Redacted prompt...');
    expect(promptTextarea.tagName.toLowerCase()).toBe('textarea');
    expect((promptTextarea as HTMLTextAreaElement).value).toContain('### System Metadata');
    expect((promptTextarea as HTMLTextAreaElement).value).toContain('query failed: upstream DNS server refused connection');

    // Does NOT have max-h-48 CSS height clipping
    expect(promptTextarea.className).not.toContain('max-h-48');
  });

  it('allows inline prompt editing and displays (modified) tag and Reset Prompt button', async () => {
    render(
      <AiAnalysisModal
        isOpen={true}
        onClose={vi.fn()}
        selectedLogs={sampleLogs}
      />
    );

    const promptTextarea = await screen.findByPlaceholderText('Redacted prompt...');
    expect(screen.queryByText('Reset Prompt')).not.toBeInTheDocument();
    expect(screen.queryByText('(modified)')).not.toBeInTheDocument();

    // Directly edit prompt in textarea
    fireEvent.change(promptTextarea, {
      target: { value: 'Custom operator edited prompt with pruned logs' },
    });

    expect((promptTextarea as HTMLTextAreaElement).value).toBe('Custom operator edited prompt with pruned logs');
    expect(screen.getByText('(modified)')).toBeInTheDocument();
    expect(screen.getByText('Reset Prompt')).toBeInTheDocument();

    // Clicking Reset Prompt restores the original generated prompt
    fireEvent.click(screen.getByText('Reset Prompt'));
    expect((promptTextarea as HTMLTextAreaElement).value).toContain('### System Metadata');
    expect(screen.queryByText('(modified)')).not.toBeInTheDocument();
    expect(screen.queryByText('Reset Prompt')).not.toBeInTheDocument();
  });

  it('displays accurate token count and informational tooltip', async () => {
    render(
      <AiAnalysisModal
        isOpen={true}
        onClose={vi.fn()}
        selectedLogs={sampleLogs}
      />
    );

    await screen.findByPlaceholderText('Redacted prompt...');

    const tooltipText =
      'Estimated prompt/input tokens only (includes system instructions and metadata). Does not include model thinking or response output tokens.';

    const tokenBadges = screen.getAllByTitle(tooltipText);
    expect(tokenBadges.length).toBeGreaterThan(0);
    expect(tokenBadges[0].textContent).toMatch(/~\d+ tokens/);
  });

  it('renders the AI advisory disclaimer banner above the action button with exact text', async () => {
    render(
      <AiAnalysisModal
        isOpen={true}
        onClose={vi.fn()}
        selectedLogs={sampleLogs}
      />
    );

    await screen.findByPlaceholderText('Redacted prompt...');

    const disclaimerText =
      'AI root-cause analyses and remediation commands are advisory only. Always verify proposed commands and configurations before executing on systems. API calls consume tokens billed to your provider.';

    expect(screen.getByText(disclaimerText)).toBeInTheDocument();
  });

  it('renders the log redaction notice reminding the user of their responsibility', async () => {
    render(
      <AiAnalysisModal
        isOpen={true}
        onClose={vi.fn()}
        selectedLogs={sampleLogs}
      />
    );

    await screen.findByPlaceholderText('Redacted prompt...');

    expect(screen.getByText('Redaction Notice:')).toBeInTheDocument();
    expect(
      screen.getByText(/Automated credential scrubbing operates on a best-effort basis/i)
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Please review the prompt above before sending/i)
    ).toBeInTheDocument();
    expect(
      screen.getByText(/you are responsible for the contents and sensitive data you transmit to external AI providers/i)
    ).toBeInTheDocument();
  });

  it('dispatches prompt_override to diagnoseLogs when prompt was edited by operator', async () => {
    render(
      <AiAnalysisModal
        isOpen={true}
        onClose={vi.fn()}
        selectedLogs={sampleLogs}
      />
    );

    const promptTextarea = await screen.findByPlaceholderText('Redacted prompt...');

    fireEvent.change(promptTextarea, {
      target: { value: 'Pruned prompt by operator for faster diagnosis.' },
    });

    const runBtn = screen.getByRole('button', { name: /Run AI Analysis/i });
    fireEvent.click(runBtn);

    await waitFor(() => {
      expect(aiApi.diagnoseLogs).toHaveBeenCalledWith(
        expect.objectContaining({
          log_ids: [1, 2],
          prompt_override: 'Pruned prompt by operator for faster diagnosis.',
        })
      );
    });
  });

  it('copies the edited prompt to clipboard when copy button is clicked', async () => {
    const copySpy = vi.spyOn(clipboardUtil, 'copyToClipboard').mockResolvedValue(true);

    render(
      <AiAnalysisModal
        isOpen={true}
        onClose={vi.fn()}
        selectedLogs={sampleLogs}
      />
    );

    const promptTextarea = await screen.findByPlaceholderText('Redacted prompt...');

    fireEvent.change(promptTextarea, {
      target: { value: 'My custom prompt to copy' },
    });

    const copyBtn = screen.getByRole('button', { name: /Copy Prompt/i });
    fireEvent.click(copyBtn);

    await waitFor(() => {
      expect(copySpy).toHaveBeenCalledWith('My custom prompt to copy');
    });
  });

  it('toggles between Analysis Prompt and Full LLM Prompt views and copies the active view', async () => {
    const copySpy = vi.spyOn(clipboardUtil, 'copyToClipboard').mockResolvedValue(true);

    render(
      <AiAnalysisModal
        isOpen={true}
        onClose={vi.fn()}
        selectedLogs={sampleLogs}
      />
    );

    const promptTextarea = await screen.findByPlaceholderText('Redacted prompt...');
    expect((promptTextarea as HTMLTextAreaElement).value).not.toContain('=== SYSTEM INSTRUCTIONS ===');

    // Click "Full LLM Prompt" toggle button
    const fullPromptBtn = screen.getByRole('button', { name: 'Full LLM Prompt' });
    fireEvent.click(fullPromptBtn);

    // Textarea now shows full envelope
    expect((promptTextarea as HTMLTextAreaElement).value).toContain('=== SYSTEM INSTRUCTIONS ===');
    expect((promptTextarea as HTMLTextAreaElement).value).toContain('=== USER ANALYSIS PROMPT ===');

    // Copying now copies the full envelope
    const copyBtn = screen.getByRole('button', { name: /Copy Prompt/i });
    fireEvent.click(copyBtn);

    await waitFor(() => {
      expect(copySpy).toHaveBeenCalledWith(
        expect.stringContaining('=== SYSTEM INSTRUCTIONS ===')
      );
    });

    // Switch back to "Analysis Prompt"
    const analysisPromptBtn = screen.getByRole('button', { name: 'Analysis Prompt' });
    fireEvent.click(analysisPromptBtn);
    expect((promptTextarea as HTMLTextAreaElement).value).not.toContain('=== SYSTEM INSTRUCTIONS ===');
  });

  it('allows editing system prompt in Full LLM Prompt view and passes system_prompt_override to diagnoseLogs', async () => {
    render(
      <AiAnalysisModal
        isOpen={true}
        onClose={vi.fn()}
        selectedLogs={sampleLogs}
      />
    );

    await screen.findByPlaceholderText('Redacted prompt...');

    // Switch to Full LLM Prompt
    fireEvent.click(screen.getByRole('button', { name: 'Full LLM Prompt' }));

    const fullPromptTextarea = screen.getByPlaceholderText('Full LLM prompt envelope...');
    fireEvent.change(fullPromptTextarea, {
      target: {
        value:
          '=== SYSTEM INSTRUCTIONS ===\nCustom operator persona for network triage.\n\n=== USER ANALYSIS PROMPT ===\nCustom user logs payload.',
      },
    });

    expect(screen.getByText('(modified)')).toBeInTheDocument();

    const runBtn = screen.getByRole('button', { name: /Run AI Analysis/i });
    fireEvent.click(runBtn);

    await waitFor(() => {
      expect(aiApi.diagnoseLogs).toHaveBeenCalledWith(
        expect.objectContaining({
          log_ids: [1, 2],
          prompt_override: 'Custom user logs payload.',
          system_prompt_override: 'Custom operator persona for network triage.',
        })
      );
    });
  });

  it('displays a clear guardrail error when more than 200 logs are selected without calling preview API', async () => {
    const manyLogs: LogEntry[] = Array.from({ length: 250 }, (_, i) => ({
      id: i + 1,
      timestamp: '2026-09-04T08:00:00.000Z',
      received_at: '2026-09-04T08:00:00.100Z',
      source_ip: '192.168.1.1',
      source_alias: 'router',
      app_name: 'dnsmasq',
      facility: 1,
      severity: 3,
      message: `log line ${i}`,
      raw: `raw log ${i}`,
    }));

    render(
      <AiAnalysisModal
        isOpen={true}
        onClose={vi.fn()}
        selectedLogs={manyLogs}
      />
    );

    expect(await screen.findByText(/Cannot analyze more than 200 logs at once/i)).toBeInTheDocument();
    expect(screen.getAllByText(/250 logs selected/i).length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText(/\(Max 200 logs allowed\)/i)).toBeInTheDocument();
    expect(aiApi.previewAiPrompt).not.toHaveBeenCalled();
  });

  it('hides Reset Prompt button when user manually reverts changes back to default', async () => {
    render(
      <AiAnalysisModal
        isOpen={true}
        onClose={vi.fn()}
        selectedLogs={sampleLogs}
      />
    );

    const promptTextarea = await screen.findByPlaceholderText('Redacted prompt...');
    const originalPrompt = (promptTextarea as HTMLTextAreaElement).value;

    expect(screen.queryByText('Reset Prompt')).not.toBeInTheDocument();

    // Modify prompt
    fireEvent.change(promptTextarea, {
      target: { value: 'Changed prompt text' },
    });
    expect(screen.getByText('Reset Prompt')).toBeInTheDocument();

    // Revert back to original prompt
    fireEvent.change(promptTextarea, {
      target: { value: originalPrompt },
    });
    expect(screen.queryByText('Reset Prompt')).not.toBeInTheDocument();
  });

  it('shows Reset Prompt button when system instructions are modified in full prompt view mode and resets on click', async () => {
    render(
      <AiAnalysisModal
        isOpen={true}
        onClose={vi.fn()}
        selectedLogs={sampleLogs}
      />
    );

    await screen.findByPlaceholderText('Redacted prompt...');

    // Switch to Full LLM Prompt mode
    fireEvent.click(screen.getByRole('button', { name: 'Full LLM Prompt' }));

    const fullPromptTextarea = screen.getByPlaceholderText('Full LLM prompt envelope...');
    expect(screen.queryByText('Reset Prompt')).not.toBeInTheDocument();

    // Modify only the system prompt portion in full view
    const modifiedFullEnvelope =
      '=== SYSTEM INSTRUCTIONS ===\nAltered system instructions.\n\n=== USER ANALYSIS PROMPT ===\n' +
      samplePreview.redacted_prompt;

    fireEvent.change(fullPromptTextarea, {
      target: { value: modifiedFullEnvelope },
    });

    expect(screen.getByText('Reset Prompt')).toBeInTheDocument();

    // Click Reset Prompt
    fireEvent.click(screen.getByText('Reset Prompt'));
    expect(screen.queryByText('Reset Prompt')).not.toBeInTheDocument();
    expect((fullPromptTextarea as HTMLTextAreaElement).value).toContain(samplePreview.system_prompt);
  });

  it('does not display Reset Prompt when prompt text matches default with CRLF line endings', async () => {
    render(
      <AiAnalysisModal
        isOpen={true}
        onClose={vi.fn()}
        selectedLogs={sampleLogs}
      />
    );

    const promptTextarea = await screen.findByPlaceholderText('Redacted prompt...');
    const crlfPrompt = samplePreview.redacted_prompt.replace(/\n/g, '\r\n');

    fireEvent.change(promptTextarea, {
      target: { value: crlfPrompt },
    });

    expect(screen.queryByText('Reset Prompt')).not.toBeInTheDocument();
  });

  it('keeps prompt in sync with situational context when unedited, and shows Reset Prompt only when edited', async () => {
    render(
      <AiAnalysisModal
        isOpen={true}
        onClose={vi.fn()}
        selectedLogs={sampleLogs}
      />
    );

    const promptTextarea = await screen.findByPlaceholderText('Redacted prompt...');
    const contextTextarea = screen.getByPlaceholderText(/Occurred immediately following network switch/i);

    // Initial load: Reset Prompt not shown
    expect(screen.queryByText('Reset Prompt')).not.toBeInTheDocument();

    // Type situational context
    fireEvent.change(contextTextarea, {
      target: { value: 'Container restarted 3 times' },
    });

    // Prompt updates automatically with context and Reset Prompt is NOT shown because it is not diverged
    expect((promptTextarea as HTMLTextAreaElement).value).toContain('Container restarted 3 times');
    expect(screen.queryByText('Reset Prompt')).not.toBeInTheDocument();

    // Now manually edit prompt
    fireEvent.change(promptTextarea, {
      target: { value: 'Manually customized prompt content' },
    });
    expect(screen.getByText('Reset Prompt')).toBeInTheDocument();

    // Further changes to situational context do NOT overwrite manual edits
    fireEvent.change(contextTextarea, {
      target: { value: 'Updated context while prompt is custom' },
    });
    expect((promptTextarea as HTMLTextAreaElement).value).toBe('Manually customized prompt content');
    expect(screen.getByText('Reset Prompt')).toBeInTheDocument();

    // Clicking Reset Prompt restores default prompt with current context and hides button
    fireEvent.click(screen.getByText('Reset Prompt'));
    expect(screen.queryByText('Reset Prompt')).not.toBeInTheDocument();
    expect((promptTextarea as HTMLTextAreaElement).value).toContain('Updated context while prompt is custom');
  });

  it('displays fallback badge and failover feedback banner when fallback_used is true', async () => {
    vi.spyOn(aiApi, 'diagnoseLogs').mockResolvedValue({
      summary: 'DNS server connection refused.',
      root_cause: 'Upstream 1.1.1.1 DNS is unreachable.',
      remediation: 'Check firewall routing and DNS configuration.',
      model_used: 'gemini-2.5-flash',
      fallback_used: true,
      fallback_attempts: ['gemini-3.7-flash failed: 503 Model Overloaded'],
      tokens_in: 285,
      tokens_out: 45,
      tokens_thoughts: 0,
      tokens_used: 330,
      audit_id: 15,
    });

    render(
      <AiAnalysisModal
        isOpen={true}
        onClose={vi.fn()}
        selectedLogs={sampleLogs}
      />
    );

    await waitFor(() => {
      expect(aiApi.previewAiPrompt).toHaveBeenCalled();
    });

    const runBtn = await screen.findByText('Run AI Analysis');
    fireEvent.click(runBtn);

    await waitFor(() => {
      expect(screen.getAllByText('gemini-2.5-flash').length).toBeGreaterThanOrEqual(1);
    });

    expect(screen.getByText('Fallback')).toBeInTheDocument();
    expect(screen.getByText('Model Failover Active')).toBeInTheDocument();
    expect(screen.getByText(/gemini-3.7-flash failed: 503 Model Overloaded/)).toBeInTheDocument();
  });

  it('displays configured fallback models from preview in sequential order', async () => {
    vi.spyOn(aiApi, 'previewAiPrompt').mockResolvedValue({
      ...samplePreview,
      fallback_models: ['gemini-2.5-flash', 'gemini-2.5-pro'],
    });

    render(
      <AiAnalysisModal
        isOpen={true}
        onClose={vi.fn()}
        selectedLogs={sampleLogs}
      />
    );

    await waitFor(() => {
      expect(screen.getByText('Primary Model')).toBeInTheDocument();
    });

    // Fallback models are listed in order
    await waitFor(() => {
      expect(screen.getByText('1st Fallback')).toBeInTheDocument();
      expect(screen.getByText('2nd Fallback')).toBeInTheDocument();
      expect(screen.getByText('gemini-2.5-flash')).toBeInTheDocument();
      expect(screen.getByText('gemini-2.5-pro')).toBeInTheDocument();
    });
  });

  it('allows editing fallback models and passes custom fallback_models to diagnoseLogs', async () => {
    render(
      <AiAnalysisModal
        isOpen={true}
        onClose={vi.fn()}
        selectedLogs={sampleLogs}
      />
    );

    await waitFor(() => {
      expect(screen.getByText('Primary Model')).toBeInTheDocument();
    });

    // Select custom fallback model option from the Add Fallback dropdown
    const fallbackSelect = await screen.findByDisplayValue('-- Add Fallback Model --');
    fireEvent.change(fallbackSelect, { target: { value: '__custom_fallback__' } });

    const customInput = await screen.findByPlaceholderText('Enter custom model ID');
    fireEvent.change(customInput, { target: { value: 'custom-fallback-model' } });

    const addBtn = screen.getByRole('button', { name: /Add/i });
    fireEvent.click(addBtn);

    await waitFor(() => {
      expect(screen.getByText('1st Fallback')).toBeInTheDocument();
      expect(screen.getByText('custom-fallback-model')).toBeInTheDocument();
    });

    const runBtn = screen.getByRole('button', { name: /Run AI Analysis/i });
    fireEvent.click(runBtn);

    await waitFor(() => {
      expect(aiApi.diagnoseLogs).toHaveBeenCalledWith(
        expect.objectContaining({
          fallback_models: ['custom-fallback-model'],
        })
      );
    });
  });

  it('renders live in-flight progress feedback when streaming progress events occur', async () => {
    let capturedOnEvent: any = null;
    vi.spyOn(aiApi, 'diagnoseLogs').mockImplementation(async (req) => {
      if (req.onEvent) {
        capturedOnEvent = req.onEvent;
        // Simulate in-flight progress calling event
        req.onEvent({
          stage: 'calling',
          model: 'gemini-3.7-flash',
          is_fallback: false,
          message: 'Querying primary model (gemini-3.7-flash)...',
        });
      }
      // Return a pending promise that we control
      return new Promise((resolve) => {
        setTimeout(() => {
          if (capturedOnEvent) {
            // Simulate failover event
            capturedOnEvent({
              stage: 'failover',
              failed_model: 'gemini-3.7-flash',
              next_model: 'gemini-2.5-flash',
              error: '503 Model Overloaded',
            });
          }
          resolve({
            summary: 'DNS resolved.',
            root_cause: 'Failover cause.',
            remediation: 'Restart dnsmasq.',
            model_used: 'gemini-2.5-flash',
            fallback_used: true,
            fallback_attempts: ['gemini-3.7-flash failed: 503'],
            tokens_in: 100,
            tokens_out: 50,
            tokens_thoughts: 0,
            tokens_used: 150,
            audit_id: 20,
          });
        }, 50);
      });
    });

    render(
      <AiAnalysisModal
        isOpen={true}
        onClose={vi.fn()}
        selectedLogs={sampleLogs}
      />
    );

    await waitFor(() => {
      expect(aiApi.previewAiPrompt).toHaveBeenCalled();
    });

    const runBtn = screen.getByRole('button', { name: /Run AI Analysis/i });
    fireEvent.click(runBtn);

    // Shows in-flight querying message and elapsed label
    await waitFor(() => {
      expect(screen.getByText(/Querying primary model \(gemini-3.7-flash\)\.\.\./)).toBeInTheDocument();
      expect(screen.getByText(/Elapsed: 00:00/)).toBeInTheDocument();
    });

    // Eventually resolves and shows final result
    await waitFor(() => {
      expect(screen.getByText('Restart dnsmasq.')).toBeInTheDocument();
    });
  });

  it('filters out fallback models from primary model dropdown and prunes fallback if set as primary', async () => {
    vi.spyOn(aiApi, 'previewAiPrompt').mockResolvedValue({
      ...samplePreview,
      fallback_models: ['gemini-2.5-flash'],
    });

    render(
      <AiAnalysisModal
        isOpen={true}
        onClose={vi.fn()}
        selectedLogs={sampleLogs}
      />
    );

    await waitFor(() => {
      expect(screen.getByText('1st Fallback')).toBeInTheDocument();
      expect(screen.getByText('gemini-2.5-flash')).toBeInTheDocument();
    });

    // In Primary Model dropdown, gemini-2.5-flash should NOT be available as an option
    const primarySelect = screen.getByDisplayValue(/gemini-3.7-flash/);
    const options = Array.from(primarySelect.querySelectorAll('option')).map((o) => o.value);
    expect(options).toContain('gemini-3.7-flash');
    expect(options).not.toContain('gemini-2.5-flash');

    // Switch to custom model and input gemini-2.5-flash
    fireEvent.change(primarySelect, { target: { value: '__custom__' } });
    const customInput = screen.getByPlaceholderText(/DEFAULT_AI_MODEL|gemini-3.7-flash/i);
    fireEvent.change(customInput, { target: { value: 'gemini-2.5-flash' } });

    // Fallback list should now be pruned
    await waitFor(() => {
      expect(screen.queryByText('1st Fallback')).toBeNull();
      expect(screen.getByText(/No fallback models configured for this analysis/i)).toBeInTheDocument();
    });
  });

  it('toggles AI provider and model configuration panel when toggle header is clicked', async () => {
    render(
      <AiAnalysisModal
        isOpen={true}
        onClose={vi.fn()}
        selectedLogs={sampleLogs}
      />
    );

    await waitFor(() => {
      expect(screen.getByText('Model & Provider Settings')).toBeInTheDocument();
    });

    const toggleBtn = screen.getByRole('button', { name: /Model & Provider Settings/i });
    expect(toggleBtn).toHaveAttribute('aria-expanded', 'false');
    expect(screen.getByText('Configure')).toBeInTheDocument();

    const settingsContainer = document.getElementById('ai-model-settings-content');
    expect(settingsContainer).toHaveClass('hidden');

    // Click toggle button to expand settings
    fireEvent.click(toggleBtn);
    expect(toggleBtn).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByText('Hide')).toBeInTheDocument();
    expect(settingsContainer).not.toHaveClass('hidden');

    // Click again to collapse settings
    fireEvent.click(toggleBtn);
    expect(toggleBtn).toHaveAttribute('aria-expanded', 'false');
    expect(screen.getByText('Configure')).toBeInTheDocument();
    expect(settingsContainer).toHaveClass('hidden');
  });

  it('preserves prompt text when typing or deleting characters on the first line in Full LLM Prompt mode', async () => {
    render(
      <AiAnalysisModal
        isOpen={true}
        onClose={vi.fn()}
        selectedLogs={sampleLogs}
      />
    );

    await screen.findByPlaceholderText('Redacted prompt...');

    // Switch to Full LLM Prompt
    fireEvent.click(screen.getByRole('button', { name: 'Full LLM Prompt' }));

    const fullPromptTextarea = screen.getByPlaceholderText('Full LLM prompt envelope...') as HTMLTextAreaElement;
    expect(fullPromptTextarea.value).toContain('=== SYSTEM INSTRUCTIONS ===');

    // Simulate prepending text on line 1 before the header
    const prependedText = `[CRITICAL NOTICE]\n${fullPromptTextarea.value}`;
    fireEvent.change(fullPromptTextarea, {
      target: { value: prependedText },
    });

    expect(fullPromptTextarea.value.startsWith('[CRITICAL NOTICE]')).toBe(true);

    // Simulate deleting characters on line 1
    const editedLine1 = fullPromptTextarea.value.slice(10);
    fireEvent.change(fullPromptTextarea, {
      target: { value: editedLine1 },
    });

    expect(fullPromptTextarea.value).toBe(editedLine1);
  });

  it('displays advisory banner and disables run button when AI is disabled in settings', async () => {
    vi.spyOn(aiApi, 'previewAiPrompt').mockResolvedValue({
      ...samplePreview,
      ai_enabled: false,
      has_ai_api_key: true,
    });
    const onNavigateToSettings = vi.fn();
    const onClose = vi.fn();

    render(
      <AiAnalysisModal
        isOpen={true}
        onClose={onClose}
        selectedLogs={sampleLogs}
        onNavigateToSettings={onNavigateToSettings}
      />
    );

    await screen.findByPlaceholderText('Redacted prompt...');

    expect(screen.getByText('AI Provider Disabled')).toBeInTheDocument();
    expect(
      screen.getByText(/AI features are disabled in system configuration\. Enable an AI provider in Settings to inspect logs\./i)
    ).toBeInTheDocument();

    const configureBtn = screen.getByRole('button', { name: /Configure in Settings/i });
    expect(configureBtn).toBeInTheDocument();

    fireEvent.click(configureBtn);
    expect(onClose).toHaveBeenCalled();
    expect(onNavigateToSettings).toHaveBeenCalled();

    const runBtn = screen.getByRole('button', { name: /Run AI Analysis/i });
    expect(runBtn).toBeDisabled();
  });

  it('displays advisory banner and disables run button when API key is missing', async () => {
    vi.spyOn(aiApi, 'previewAiPrompt').mockResolvedValue({
      ...samplePreview,
      ai_enabled: true,
      has_ai_api_key: false,
    });

    render(
      <AiAnalysisModal
        isOpen={true}
        onClose={vi.fn()}
        selectedLogs={sampleLogs}
      />
    );

    await screen.findByPlaceholderText('Redacted prompt...');

    expect(screen.getByText('AI Provider Not Configured')).toBeInTheDocument();
    expect(
      screen.getByText(/An API key is required to query models\. Configure your AI provider in Settings to inspect logs\./i)
    ).toBeInTheDocument();

    const runBtn = screen.getByRole('button', { name: /Run AI Analysis/i });
    expect(runBtn).toBeDisabled();
  });

  it('lists Anthropic Claude in provider options and switches model settings when changing provider', async () => {
    render(
      <AiAnalysisModal
        isOpen={true}
        onClose={vi.fn()}
        selectedLogs={sampleLogs}
      />
    );

    await screen.findByPlaceholderText('Redacted prompt...');

    // Open Model & Provider Settings
    const toggleBtn = screen.getByRole('button', { name: /Model & Provider Settings/i });
    fireEvent.click(toggleBtn);

    const providerSelect = screen.getByDisplayValue('Google Gemini') as HTMLSelectElement;
    const optionValues = Array.from(providerSelect.querySelectorAll('option')).map((o) => o.value);
    expect(optionValues).toContain('gemini');
    expect(optionValues).toContain('openai');
    expect(optionValues).toContain('anthropic');
    expect(optionValues).toContain('openai_compatible');

    // Switch to OpenAI
    fireEvent.change(providerSelect, { target: { value: 'openai' } });

    await waitFor(() => {
      expect(aiApi.getAiModels).toHaveBeenCalledWith('openai');
    });

    // Switch to Anthropic Claude
    fireEvent.change(providerSelect, { target: { value: 'anthropic' } });

    await waitFor(() => {
      expect(aiApi.getAiModels).toHaveBeenCalledWith('anthropic');
    });
  });
});



