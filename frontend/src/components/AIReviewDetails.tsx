import type { Highlight } from '../types/api';
import { Badge } from './ui';
import { label } from '../lib/utils';

export function AIReviewDetails({
  highlight,
  compact = false,
}: {
  highlight: Highlight;
  compact?: boolean;
}) {
  const review = highlight.ai_review;
  if (!review && !highlight.ai_error) return null;
  return (
    <div className="mt-3 rounded-md border border-border bg-black/10 p-3">
      {review ? (
        <>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="text-[11px] font-medium">
              AI review{' '}
              <span className="font-mono text-primary">{review.score.toFixed(0)}/100</span>
            </p>
            <Badge>
              {review.provider}
              {review.cache_hit ? ' · cached' : ''}
            </Badge>
          </div>
          <p className="mt-1 break-words text-[11px] text-zinc-500">
            {review.model} ·{' '}
            {review.evidence === 'transcript_only'
              ? 'Transcript only · no visual judgment'
              : 'Sparse frames + transcript'}{' '}
            · editorial judgment, not a probability
          </p>
          {compact ? (
            <details className="mt-2 text-[11px]">
              <summary className="cursor-pointer text-zinc-400">Score details</summary>
              <Rubric highlight={highlight} />
            </details>
          ) : (
            <Rubric highlight={highlight} />
          )}
        </>
      ) : (
        <p className="text-[11px] text-zinc-500">AI review unavailable</p>
      )}
      {highlight.ai_error && (
        <p role="status" className="mt-2 break-words text-[11px] text-amber-200">
          Review issue: {highlight.ai_error} Local results are unchanged.
          {review ? ' The previous review is shown above.' : ''}
        </p>
      )}
    </div>
  );
}
function Rubric({ highlight }: { highlight: Highlight }) {
  const review = highlight.ai_review!;
  return (
    <>
      <dl className="mt-3 grid grid-cols-3 gap-2 text-[11px]">
        {Object.entries(review.rubric).map(([name, value]) => (
          <div key={name}>
            <dt className="text-zinc-500">{label(name)}</dt>
            <dd className="mt-0.5 font-mono">{value.toFixed(0)}/100</dd>
          </div>
        ))}
      </dl>
      <p className="mt-3 whitespace-pre-wrap break-words text-[11px] leading-relaxed text-zinc-400">
        {review.explanation}
      </p>
      <p className="mt-2 text-[11px] text-zinc-600">
        Reviewed {new Date(review.created_at).toLocaleString()}
      </p>
    </>
  );
}
