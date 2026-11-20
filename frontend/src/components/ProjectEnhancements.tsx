import { useState } from 'react';
import { Link } from 'react-router-dom';
import type { ChatGPTStatus, Highlight, Settings } from '../types/api';
import { api } from '../services/api';
import { isActive } from '../lib/utils';
import { useStudio } from '../stores/studio';
import { useAction } from '../hooks/useAction';
import { useIntegrations } from '../hooks/useIntegrations';
import { useChatGPT } from '../hooks/useChatGPT';
import { Button, Card, Checkbox, Label } from './ui';
import { ErrorBanner } from './shared';

export function reviewSetupProblem(
  settings: Settings | null,
  configured: boolean,
  chatgpt?: ChatGPTStatus | null,
): string | null {
  if (!settings) return 'Load and save settings before reviewing.';
  if (settings.ai_scoring_provider === 'none')
    return 'Choose OpenAI, Gemini, or Groq as the review provider in Settings.';
  if (!settings.cloud_consent)
    return 'Enable and save cloud consent in Settings after reviewing the privacy notice.';
  if (settings.ai_scoring_provider === 'openai' && settings.openai_auth_mode === 'chatgpt') {
    if (!chatgpt?.connected || !chatgpt.plan_enabled || chatgpt.pending)
      return 'Connect an eligible ChatGPT plan account in Settings. No API-key fallback is used.';
  } else {
    if (!configured)
      return `Configure a ${settings.ai_scoring_provider} API key on the local server in Settings.`;
    if (settings.ai_daily_budget_usd === 0)
      return 'API requests are disabled by the zero-dollar budget in Settings.';
  }
  return null;
}
export function ProjectEnhancements({
  projectId,
  highlights,
}: {
  projectId: string;
  highlights: Highlight[];
}) {
  const { settings, jobs } = useStudio((s) => s.data);
  const { integrations, error, loading, refresh } = useIntegrations();
  const [replaceCaptions, setReplaceCaptions] = useState(false);
  const [forceReview, setForceReview] = useState(false);
  const captions = useAction(),
    review = useAction();
  const provider = settings?.ai_scoring_provider ?? 'none';
  const planMode = provider === 'openai' && settings?.openai_auth_mode === 'chatgpt';
  const chatgpt = useChatGPT(planMode);
  const problem = reviewSetupProblem(
    settings,
    provider !== 'none' && !!integrations?.[provider]?.configured,
    chatgpt.status,
  );
  const projectBusy = jobs.some((job) => job.project_id === projectId && isActive(job));
  const busy = (kind: 'captions' | 'score') =>
    jobs.some((job) => job.project_id === projectId && job.kind === kind && isActive(job));
  return (
    <Card className="grid gap-4 border-0 border-t p-4 shadow-none lg:grid-cols-2">
      <div>
        <h3 className="text-xs font-semibold">Automatic subtitles</h3>
        <p className="mt-2 text-[11px] leading-relaxed text-zinc-500">
          {settings?.caption_style === 'none'
            ? 'No captions is selected in Settings. Choose Standard or Animated to enable transcription and subtitle export.'
            : 'Generate subtitles for every project highlight, not just selected clips (up to 50 clips, 90s each). Missing subtitles also generate after detection and on export.'}
        </p>
        <div className="my-3 flex items-center gap-2">
          <Checkbox
            id="replace-project-captions"
            checked={replaceCaptions}
            onCheckedChange={(v) => setReplaceCaptions(v === true)}
          />
          <Label htmlFor="replace-project-captions" className="mb-0 text-[11px]">
            Replace existing transcripts, including manual edits
          </Label>
        </div>
        <Button
          size="sm"
          variant="secondary"
          loading={captions.pending}
          disabled={
            !highlights.length ||
            !settings ||
            settings.caption_style === 'none' ||
            projectBusy ||
            highlights.length > 50 ||
            highlights.some((h) => h.duration > 90)
          }
          onClick={() => {
            void captions.run(async () => {
              const job = await api.projectCaptions(projectId, replaceCaptions);
              useStudio.getState().upsertJob(job);
              setReplaceCaptions(false);
            }, 'Subtitles queued. Text updates when the job finishes.');
          }}
        >
          {busy('captions') ? 'Subtitles queued / running' : 'Generate subtitles'}
        </Button>
      </div>
      <div>
        <h3 className="text-xs font-semibold">AI review</h3>
        <p className="mt-2 text-[11px] leading-relaxed text-zinc-500">
          Review up to {settings?.ai_scoring_max_candidates ?? 5} top candidates with{' '}
          {provider === 'none' ? 'a cloud provider' : provider}.{' '}
          {settings?.ai_review_mode === 'vision' && provider !== 'groq'
            ? 'Vision uploads sparse frames and transcript.'
            : 'Text review sends transcript and signals, without visual judgment.'}{' '}
          Uses the saved request limit{planMode ? ' and ChatGPT plan limits.' : ' and API budget.'}
        </p>
        {error && !planMode && <ErrorBanner className="mt-2" message={error} retry={refresh} />}
        {planMode && chatgpt.error && (
          <ErrorBanner
            className="mt-2"
            message={chatgpt.error}
            retry={() => void chatgpt.refresh()}
          />
        )}
        {problem && (
          <p className="mt-2 text-[11px] text-amber-200">
            {problem}{' '}
            <Link className="underline" to="/settings">
              Open Settings
            </Link>
          </p>
        )}
        <div className="my-3 flex items-center gap-2">
          <Checkbox
            id="force-review"
            checked={forceReview}
            onCheckedChange={(v) => setForceReview(v === true)}
          />
          <Label htmlFor="force-review" className="mb-0 text-[11px]">
            Bypass cached reviews (may use additional quota)
          </Label>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button
            size="sm"
            loading={review.pending}
            disabled={
              !highlights.length ||
              !!problem ||
              (planMode ? !!chatgpt.error : loading || !!error) ||
              projectBusy
            }
            onClick={() => {
              void review.run(async () => {
                const job = await api.scoreProject(projectId, forceReview);
                useStudio.getState().upsertJob(job);
                setForceReview(false);
              }, 'AI review queued. Local results are unchanged.');
            }}
          >
            {busy('score') ? 'AI review queued / running' : 'Review highlights with AI'}
          </Button>
          <Button
            size="sm"
            variant="ghost"
            disabled={loading}
            onClick={() => {
              if (planMode) void chatgpt.refresh();
              else refresh();
            }}
          >
            {planMode ? 'Refresh account status' : 'Refresh key status'}
          </Button>
        </div>
        <p className="mt-2 text-[11px] text-zinc-600">
          AI scores stay separate from local detection. Subtitle or cloud failures leave local
          results unchanged. Provider quotas and data policies apply.
        </p>
        <details className="mt-3 text-[11px] text-zinc-500">
          <summary className="cursor-pointer">Review settings</summary>
          <p className="mt-2 leading-relaxed">
            Uses saved settings, consent and server credentials, not the previous analysis snapshot.
            Provider: {provider}
            {settings?.ai_scoring_model ? ` · ${settings.ai_scoring_model}` : ' · default model'}.
          </p>
        </details>
      </div>
    </Card>
  );
}
