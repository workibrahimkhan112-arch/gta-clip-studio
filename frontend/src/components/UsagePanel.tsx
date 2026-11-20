import { useEffect, useState } from 'react';
import type { UsageSummary } from '../types/api';
import { api } from '../services/api';
import { useStudio } from '../stores/studio';
import { Button, Card, CardHeader, CardTitle } from './ui';
import { ErrorBanner } from './shared';

const number = (value: number | null) => (value === null ? 'Unknown' : value.toLocaleString());
const usd = (value: number | null) => {
  if (value === null) return 'Unknown';
  if (value > 0 && value < 0.000001) return '<$0.000001';
  if (value > 0 && value < 0.01 && Number(value.toFixed(4)) !== value)
    return `$${value.toFixed(6).replace(/0+$/, '').replace(/\.$/, '')}`;
  return `$${value.toFixed(4)}`;
};
export const planUsageUrl = 'https://chatgpt.com/settings/usage';

export function UsagePanel() {
  const [summary, setSummary] = useState<UsageSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const jobs = useStudio((state) => state.data.jobs);
  const settings = useStudio((state) => state.data.settings);
  const planOnly =
    !!summary?.rows.length && summary.rows.every((row) => row.auth_mode === 'chatgpt');
  const completion = jobs
    .filter((job) => ['score', 'titles', 'captions'].includes(job.kind))
    .map((job) => `${job.id}:${job.status}`)
    .sort()
    .join('|');
  const refresh = async () => {
    setLoading(true);
    setError(null);
    try {
      setSummary(await api.usage());
    } catch (error) {
      setError(error instanceof Error ? error.message : 'Could not load usage.');
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => {
    let active = true;
    setLoading(true);
    api
      .usage()
      .then(
        (value) => {
          if (active) {
            setSummary(value);
            setError(null);
          }
        },
        (error: unknown) => {
          if (active) setError(error instanceof Error ? error.message : 'Could not load usage.');
        },
      )
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [completion]);
  return (
    <Card className="mt-5" aria-label="AI usage">
      <CardHeader>
        <CardTitle>AI usage</CardTitle>
        <Button
          type="button"
          size="sm"
          variant="ghost"
          disabled={loading}
          onClick={() => void refresh()}
        >
          Refresh usage
        </Button>
      </CardHeader>
      <div className="space-y-4 p-4">
        <p className="text-[11px] leading-relaxed text-zinc-400">
          Local request ledger, not a provider balance or invoice. API costs use known prices and
          reported usage. ChatGPT plan quota is not available here.{' '}
          <a className="underline" href={planUsageUrl} target="_blank" rel="noreferrer">
            View ChatGPT plan limits
          </a>
          .
        </p>
        {settings && (
          <div
            className="rounded-md border border-border p-3 text-[11px] text-zinc-300"
            aria-label="Saved cloud limits"
          >
            <p>
              Saved daily limits: {settings.ai_daily_request_limit} cloud requests · API budget{' '}
              {usd(settings.ai_daily_budget_usd)}.
            </p>
            <p className="mt-1 text-zinc-400">
              {settings.ai_daily_budget_usd === 0 ? 'API requests are disabled. ' : ''}ChatGPT plan
              uses the local request cap and provider plan limits, not the USD budget.{' '}
              {settings.ai_allow_unpriced_models
                ? 'Unpriced API models are allowed: their dollar ceiling cannot be enforced.'
                : 'Unpriced API models are blocked.'}
            </p>
          </div>
        )}
        {error && (
          <ErrorBanner
            message={`${summary ? 'Showing last received usage. ' : ''}${error}`}
            retry={() => void refresh()}
          />
        )}
        {loading && (
          <p role="status" className="text-xs text-zinc-500">
            Loading usage…
          </p>
        )}
        {!summary && !loading && !error && (
          <p className="text-xs text-zinc-500">Usage has not been loaded.</p>
        )}
        {summary && (
          <>
            <div className="flex flex-wrap gap-x-6 gap-y-2 text-xs text-zinc-300">
              <span>{summary.day} UTC</span>
              <span>{summary.request_count} requests</span>
              <span>{summary.cache_hits} cache hits</span>
              <span>
                Estimated API cost: {planOnly ? '$—, plan usage' : usd(summary.estimated_cost_usd)}
                {!planOnly && summary.unknown_usage_requests > 0
                  ? ' · partial / unknown usage'
                  : ''}
              </span>
              <span>
                Reserved API cost: {planOnly ? 'Not applicable' : usd(summary.reserved_cost_usd)}
              </span>
            </div>
            {summary.unknown_usage_requests > 0 && (
              <p className="text-[11px] text-amber-200">
                {summary.unknown_usage_requests} request(s) have usage or cost that is not reported.
                Plan USD is not applicable. Failed or interrupted API requests may still be billed;
                reservations can remain counted.
              </p>
            )}
            {!summary.rows.length ? (
              <p className="text-xs text-zinc-500">No cloud usage recorded today.</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-left text-[11px]">
                  <caption className="sr-only">
                    Requests, tokens and estimated cost per model and operation
                  </caption>
                  <thead className="text-zinc-400">
                    <tr>
                      {[
                        'Provider / model',
                        'Operation',
                        'Requests / cache',
                        'Input / cached',
                        'Output',
                        'Audio',
                        'Estimated USD',
                        'Reserved USD',
                      ].map((title) => (
                        <th key={title} className="whitespace-nowrap px-2 py-2 font-medium">
                          {title}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {summary.rows.map((row, index) => (
                      <tr
                        key={`${row.provider}-${row.auth_mode}-${row.model}-${row.operation}-${index}`}
                        className="border-t border-border align-top text-zinc-300"
                      >
                        <td className="px-2 py-3">
                          <span className="block">
                            {row.provider} · {row.auth_mode === 'chatgpt' ? 'ChatGPT plan' : 'API'}
                          </span>
                          <span className="break-all font-mono text-zinc-400">{row.model}</span>
                        </td>
                        <td className="px-2 py-3">{row.operation}</td>
                        <td className="px-2 py-3 font-mono">
                          {row.requests} / {row.cache_hits}
                        </td>
                        <td className="px-2 py-3 font-mono">
                          {number(row.input_tokens)} / {number(row.cached_input_tokens)}
                        </td>
                        <td className="px-2 py-3 font-mono">{number(row.output_tokens)}</td>
                        <td className="px-2 py-3">
                          {row.audio_seconds === null ? '—' : `${row.audio_seconds.toFixed(1)}s`}
                        </td>
                        <td className="px-2 py-3">
                          {row.auth_mode === 'chatgpt'
                            ? '$—, plan usage'
                            : usd(row.estimated_cost_usd)}
                          {row.unknown_usage_requests > 0 && (
                            <span className="block text-amber-200">
                              {row.unknown_usage_requests} unknown
                            </span>
                          )}
                        </td>
                        <td className="px-2 py-3">
                          {row.auth_mode === 'chatgpt'
                            ? 'Not applicable'
                            : usd(row.reserved_cost_usd)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {summary.note && (
              <p className="text-[11px] leading-relaxed text-zinc-400">{summary.note}</p>
            )}
            <details className="text-[11px] text-zinc-500">
              <summary className="cursor-pointer py-1">Price snapshot · USD</summary>
              <ul className="mt-2 space-y-2">
                {summary.pricing.map((price) => (
                  <li key={`${price.provider}-${price.model}`} className="break-words">
                    {price.provider} / {price.model}: input {usd(price.input_per_million)}/M, cached{' '}
                    {usd(price.cached_input_per_million)}/M, output {usd(price.output_per_million)}
                    /M
                    {price.audio_per_hour !== null
                      ? `, audio ${usd(price.audio_per_hour)}/hour`
                      : ''}
                  </li>
                ))}
              </ul>
            </details>
          </>
        )}
      </div>
    </Card>
  );
}
