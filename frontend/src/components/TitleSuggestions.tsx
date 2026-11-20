import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import type { Highlight } from '../types/api';
import { api } from '../services/api';
import { useStudio } from '../stores/studio';
import { isActive } from '../lib/utils';
import { useIntegrations } from '../hooks/useIntegrations';
import { useChatGPT } from '../hooks/useChatGPT';
import { reviewSetupProblem } from './ProjectEnhancements';
import { Button } from './ui';
import { ErrorBanner } from './shared';

export function TitleSuggestions({
  highlight,
  disabled,
  onApplied,
}: {
  highlight: Highlight;
  disabled: boolean;
  onApplied: (title: string) => void;
}) {
  const { settings, jobs } = useStudio((state) => state.data);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dismissed, setDismissed] = useState<string[]>([]);
  const suggestions = highlight.ai_titles ?? [];
  const signature = JSON.stringify(suggestions);
  useEffect(() => setDismissed([]), [signature]);
  const busy = jobs.some((job) => job.project_id === highlight.project_id && isActive(job));
  const { integrations, loading, error: connectionError, refresh } = useIntegrations();
  const planMode =
    settings?.ai_scoring_provider === 'openai' && settings.openai_auth_mode === 'chatgpt';
  const chatgpt = useChatGPT(planMode);
  const provider = settings?.ai_scoring_provider ?? 'none';
  const problem = !highlight.transcript.some((segment) => segment.text.trim())
    ? 'Save a transcript before requesting titles. This action does not transcribe audio.'
    : reviewSetupProblem(
        settings,
        provider !== 'none' && !!integrations?.[provider]?.configured,
        chatgpt.status,
      );
  const unavailable = planMode ? !!chatgpt.error : loading || !!connectionError;
  const act = async (operation: () => Promise<void>) => {
    setPending(true);
    setError(null);
    try {
      await operation();
    } catch (error) {
      setError(error instanceof Error ? error.message : 'Could not update title suggestions.');
    } finally {
      setPending(false);
    }
  };
  return (
    <section
      aria-label="Title suggestions"
      className="space-y-3 rounded-md border border-border p-3"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-xs font-medium">Title suggestions</h3>
        <Button
          type="button"
          size="sm"
          variant="secondary"
          disabled={disabled || pending || busy || !!problem || unavailable}
          onClick={() =>
            void act(async () => {
              useStudio.getState().upsertJob(await api.suggestTitles(highlight.id));
            })
          }
        >
          {busy ? 'Project job in progress' : 'Suggest titles'}
        </Button>
      </div>
      <p className="text-[11px] text-zinc-400">
        Uses the saved transcript and cloud request budget. Your title changes only when you choose
        Apply.
      </p>
      {disabled && (
        <p className="text-[11px] text-amber-200">
          Save edits before requesting or applying a title.
        </p>
      )}
      {problem && (
        <p className="text-[11px] text-zinc-400">
          {problem}{' '}
          <Link
            to="/settings"
            onClick={(event) => {
              if (disabled) event.preventDefault();
            }}
            className="underline"
          >
            Settings
          </Link>
        </p>
      )}
      {(planMode ? chatgpt.error : connectionError) && (
        <ErrorBanner
          message={(planMode ? chatgpt.error : connectionError)!}
          retry={() => {
            if (planMode) void chatgpt.refresh();
            else refresh();
          }}
        />
      )}
      {highlight.title_error && (
        <p role="status" className="text-xs text-amber-200">
          {highlight.title_error} Your current title is unchanged.
        </p>
      )}
      {error && <ErrorBanner message={error} />}
      {suggestions
        .filter((title) => !dismissed.includes(title))
        .map((title, index) => (
          <div
            key={`${index}-${title}`}
            className="flex flex-wrap items-center justify-between gap-2 border-t border-border pt-2"
          >
            <p className="min-w-0 flex-1 break-words text-xs">{title}</p>
            <Button
              type="button"
              size="sm"
              variant="secondary"
              aria-label={`Apply title: ${title}`}
              disabled={disabled || pending || busy}
              onClick={() =>
                void act(async () => {
                  const updated = await api.patchHighlight(highlight.id, { title });
                  useStudio.getState().upsertHighlight(updated);
                  onApplied(updated.title);
                })
              }
            >
              Apply
            </Button>
            <Button
              type="button"
              size="sm"
              variant="ghost"
              aria-label={`Dismiss title: ${title}`}
              disabled={pending}
              onClick={() => setDismissed((items) => [...items, title])}
            >
              Dismiss
            </Button>
          </div>
        ))}
      {!!suggestions.length && suggestions.every((title) => dismissed.includes(title)) && (
        <p className="text-[11px] text-zinc-500">
          Suggestions dismissed for this session. Your title was not changed.
        </p>
      )}
    </section>
  );
}
