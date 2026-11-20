import { Captions, ChevronRight, Play, Sparkles } from 'lucide-react';
import { api } from '../services/api';
import type { Highlight } from '../types/api';
import { useStudio } from '../stores/studio';
import { useAction } from '../hooks/useAction';
import { cn, label, time } from '../lib/utils';
import { Badge, Button, Card, Checkbox, Tooltip } from './ui';
import { MediaThumbnail } from './shared';
import { AIReviewDetails } from './AIReviewDetails';

export function HighlightCard({
  highlight,
  onEdit,
  projectName,
  onPreview,
}: {
  highlight: Highlight;
  onEdit: (highlight: Highlight) => void;
  projectName?: string;
  onPreview?: (highlight: Highlight) => void;
}) {
  const { pending, run } = useAction();
  const select = (selected: boolean) =>
    run(async () =>
      useStudio.getState().upsertHighlight(await api.patchHighlight(highlight.id, { selected })),
    );
  return (
    <Card
      className={cn(
        'group min-w-0 overflow-hidden transition-colors',
        highlight.selected ? 'border-primary/25' : 'hover:border-white/20',
      )}
    >
      <div className="relative">
        <button
          className="block w-full text-left outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-primary"
          onClick={() => (onPreview ? onPreview(highlight) : onEdit(highlight))}
          aria-label={`Preview ${highlight.title}`}
        >
          <MediaThumbnail
            src={
              highlight.thumbnail_url ||
              `/api/highlights/${encodeURIComponent(highlight.id)}/thumbnail`
            }
            alt={highlight.title}
          >
            <div className="absolute inset-0 bg-gradient-to-t from-black/65 to-transparent" />
            <span className="absolute bottom-2.5 right-3 rounded bg-black/65 px-1.5 py-0.5 font-mono text-[11px]">
              {time(highlight.duration)}
            </span>
            <span className="absolute inset-0 flex items-center justify-center">
              <span className="flex size-9 items-center justify-center rounded-full border border-white/20 bg-black/40 opacity-0 backdrop-blur-sm transition-opacity group-hover:opacity-100">
                <Play className="ml-0.5 size-3.5 fill-white" />
              </span>
            </span>
          </MediaThumbnail>
        </button>
        <div className="absolute left-3 top-3 flex size-6 items-center justify-center rounded bg-black/60 backdrop-blur-sm">
          <Checkbox
            checked={highlight.selected}
            disabled={pending}
            onCheckedChange={(value) => {
              void select(value === true);
            }}
            aria-label={`Select ${highlight.title}`}
          />
        </div>
        <Tooltip content="Detection score is an estimate, not a verified event or content label.">
          <span className="absolute right-3 top-3 flex items-center gap-1 rounded border border-primary/20 bg-black/70 px-1.5 py-0.5 font-mono text-[11px] font-bold text-primary">
            <Sparkles className="size-2.5" />
            {highlight.score.toFixed(0)}
          </span>
        </Tooltip>
      </div>
      <div className="p-3.5">
        <div className="mb-2 flex items-center justify-between gap-2">
          <Badge>{label(highlight.category)}</Badge>
          <span className="font-mono text-[11px] text-zinc-500">
            {time(highlight.start)} – {time(highlight.end)}
          </span>
        </div>
        <button
          onClick={() => onEdit(highlight)}
          className="block w-full truncate text-left text-xs font-semibold leading-5 hover:text-primary"
          title={highlight.title}
        >
          {highlight.title}
        </button>
        {projectName && <p className="mt-0.5 truncate text-[11px] text-zinc-500">{projectName}</p>}
        <p className="mt-2 line-clamp-2 min-h-8 text-[11px] leading-relaxed text-zinc-500">
          {highlight.explanation || 'No explanation available.'}
        </p>
        <AIReviewDetails highlight={highlight} compact />
        {highlight.caption_error && (
          <p className="mt-2 break-words text-[11px] text-amber-200">
            Subtitles: {highlight.caption_error}
          </p>
        )}
        <div className="mt-3 flex items-center justify-between border-t border-border pt-2.5">
          <span
            className={cn(
              'flex items-center gap-1.5 text-[11px]',
              highlight.transcript.length ? 'text-zinc-400' : 'text-zinc-600',
            )}
          >
            <Captions className="size-3" />
            {highlight.caption_status === 'pending'
              ? 'Subtitles pending'
              : highlight.caption_status === 'failed'
                ? 'Subtitles failed · retry in editor'
                : highlight.caption_status === 'no_speech'
                  ? 'No speech detected'
                  : highlight.transcript.length
                    ? `${highlight.transcript.length} subtitle segments`
                    : 'No transcript yet'}
          </span>
          <Button
            variant="ghost"
            size="sm"
            className="h-6 px-1 text-[11px]"
            onClick={() => onEdit(highlight)}
          >
            Edit subtitles
            <ChevronRight className="!size-3" />
          </Button>
        </div>
      </div>
    </Card>
  );
}
