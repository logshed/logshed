import React, { useMemo, useState } from 'react';
import { AiPreviewResponse } from '../../types.ts';
import { DEFAULT_SYSTEM_PROMPT, buildFullEnvelope, parseFullEnvelope, normalizePrompt } from '../../utils/aiPrompt.ts';

/** Puts the operator's context before the redacted logs (or at the end when the prompt has no log heading). */
export const buildCombinedPrompt = (basePrompt: string, ctx: string) => {
  if (!ctx.trim()) return basePrompt;
  const parts = basePrompt.split('### Redacted Log Stream');
  if (parts.length === 2) {
    return `${parts[0]}### Situational Context from Operator\n${ctx.trim()}\n\n### Redacted Log Stream${parts[1]}`;
  }
  return `${basePrompt}\n\n### Situational Context from Operator\n${ctx.trim()}`;
};

/**
 * The prompt of the AI analysis dialog: the server's redacted preview, the operator's context, and
 * the prompt as edited, shown either as the analysis prompt and system instructions or as the full
 * text sent to the model. It tracks what was edited, so only edits are sent as overrides.
 */
export function usePromptPreview() {
  const [preview, setPreview] = useState<AiPreviewResponse | null>(null);
  const [isLoadingPreview, setIsLoadingPreview] = useState<boolean>(false);
  const [previewError, setPreviewError] = useState<string | null>(null);

  const [promptText, setPromptText] = useState<string>('');
  const [systemPrompt, setSystemPrompt] = useState<string>(DEFAULT_SYSTEM_PROMPT);
  const [fullPromptText, setFullPromptText] = useState<string>('');
  const [promptViewMode, setPromptViewMode] = useState<'analysis' | 'full'>('analysis');
  const [userContext, setUserContext] = useState<string>('');

  const defaultPrompt = useMemo(() => {
    if (!preview) return '';
    return buildCombinedPrompt(preview.redacted_prompt, userContext);
  }, [preview, userContext]);

  // Counted on the server's redacted prompt, not on the editable text, so edits cannot change it
  const maskedValueCount = useMemo(
    () => (preview ? preview.redacted_prompt.split('[REDACTED]').length - 1 : 0),
    [preview]
  );

  const defaultFullPrompt = useMemo(() => {
    if (!preview) return '';
    return buildFullEnvelope(preview.system_prompt || DEFAULT_SYSTEM_PROMPT, defaultPrompt);
  }, [preview, defaultPrompt]);

  const hasEditedPrompt = Boolean(
    preview && normalizePrompt(promptText) !== normalizePrompt(defaultPrompt)
  );
  const hasEditedSystem = Boolean(
    preview &&
      normalizePrompt(systemPrompt) !==
        normalizePrompt(preview.system_prompt || DEFAULT_SYSTEM_PROMPT)
  );
  const hasEditedFull = Boolean(
    preview && normalizePrompt(fullPromptText) !== normalizePrompt(defaultFullPrompt)
  );
  const isModified = promptViewMode === 'full' ? hasEditedFull : (hasEditedPrompt || hasEditedSystem);

  const estimatedTokens = useMemo(() => {
    if (!promptText) {
      return preview ? preview.estimated_tokens : 0;
    }
    const currentSysLen = systemPrompt.length;
    return Math.max(1, Math.floor(promptText.length / 3.5) + Math.floor(currentSysLen / 3.5) + 50);
  }, [promptText, systemPrompt, preview]);

  /** Shows a new preview, with the operator's context already typed. */
  const applyPreview = (res: AiPreviewResponse) => {
    setPreview(res);
    const initialSys = res.system_prompt || DEFAULT_SYSTEM_PROMPT;
    setSystemPrompt(initialSys);
    const initialUser = buildCombinedPrompt(res.redacted_prompt, userContext);
    setPromptText(initialUser);
    setFullPromptText(buildFullEnvelope(initialSys, initialUser));
  };

  /** Back to an empty dialog. */
  const reset = () => {
    setIsLoadingPreview(false);
    setPreview(null);
    setPreviewError(null);
    setUserContext('');
    setPromptText('');
    setSystemPrompt(DEFAULT_SYSTEM_PROMPT);
    setFullPromptText('');
    setPromptViewMode('analysis');
  };

  const handleUserContextChange = (newContext: string) => {
    const prevDefault = buildCombinedPrompt(preview?.redacted_prompt || '', userContext);
    setUserContext(newContext);
    const newDefault = buildCombinedPrompt(preview?.redacted_prompt || '', newContext);
    // If promptText currently matches previous default, keep it in sync with newContext
    if (preview && normalizePrompt(promptText) === normalizePrompt(prevDefault)) {
      setPromptText(newDefault);
      if (normalizePrompt(fullPromptText) === normalizePrompt(buildFullEnvelope(systemPrompt, prevDefault))) {
        setFullPromptText(buildFullEnvelope(systemPrompt, newDefault));
      }
    }
  };

  const handlePromptChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    if (promptViewMode === 'full') {
      setFullPromptText(e.target.value);
    } else {
      setPromptText(e.target.value);
    }
  };

  const handleResetPrompt = () => {
    if (preview) {
      setPromptText(defaultPrompt);
      const defaultSys = preview.system_prompt || DEFAULT_SYSTEM_PROMPT;
      setSystemPrompt(defaultSys);
      setFullPromptText(buildFullEnvelope(defaultSys, defaultPrompt));
    }
  };

  /** Switches to the analysis prompt, taking any edits made to the full text. */
  const showAnalysisView = () => {
    if (promptViewMode === 'full') {
      const parsed = parseFullEnvelope(fullPromptText, systemPrompt);
      setSystemPrompt(parsed.systemPrompt);
      setPromptText(parsed.userPrompt);
      setPromptViewMode('analysis');
    }
  };

  /** Switches to the full text sent to the model. */
  const showFullView = () => {
    if (promptViewMode !== 'full') {
      setFullPromptText(buildFullEnvelope(systemPrompt, promptText));
      setPromptViewMode('full');
    }
  };

  /** The prompt and system instructions to send instead of the server's, where they were edited. */
  const buildOverrides = (): { promptOverride?: string; systemPromptOverride?: string } => {
    let promptOverride: string | undefined = undefined;
    let systemPromptOverride: string | undefined = undefined;

    if (promptViewMode === 'full') {
      if (hasEditedFull) {
        const parsed = parseFullEnvelope(fullPromptText, systemPrompt);
        const defaultSys = preview?.system_prompt || DEFAULT_SYSTEM_PROMPT;
        if (normalizePrompt(parsed.userPrompt) !== normalizePrompt(defaultPrompt)) {
          promptOverride = parsed.userPrompt.trim();
        }
        if (normalizePrompt(parsed.systemPrompt) !== normalizePrompt(defaultSys)) {
          systemPromptOverride = parsed.systemPrompt.trim();
        }
      }
    } else {
      if (hasEditedPrompt) {
        promptOverride = promptText.trim();
      }
      if (hasEditedSystem) {
        systemPromptOverride = systemPrompt.trim();
      }
    }
    return { promptOverride, systemPromptOverride };
  };

  const promptToCopy = () => (promptViewMode === 'full' ? fullPromptText : promptText);

  return {
    preview,
    isLoadingPreview,
    setIsLoadingPreview,
    previewError,
    setPreviewError,
    promptText,
    systemPrompt,
    fullPromptText,
    promptViewMode,
    userContext,
    maskedValueCount,
    isModified,
    estimatedTokens,
    applyPreview,
    reset,
    handleUserContextChange,
    handlePromptChange,
    handleResetPrompt,
    showAnalysisView,
    showFullView,
    buildOverrides,
    promptToCopy,
  };
}
