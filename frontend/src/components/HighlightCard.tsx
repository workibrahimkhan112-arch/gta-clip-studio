import { useState } from 'react';
import { Captions, ChevronRight, Sparkles, Trash2 } from '../icons';
import { api } from '../services/api';
import type { Highlight } from '../types/api';
import { useStudio } from '../stores/studio';
import { useAction } from '../hooks/useAction';
import { cn, label, time } from '../lib/utils';
import {
  Button,
  Card,
  Checkbox,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
  Tooltip,
} from './ui';
import { MediaThumbnail } from './shared';

export function HighlightCard({
  highlight,
  onEdit,
  projectName,
  onPreview,
  onDeleted,
}: {
  highlight: Highlight;
  onEdit: (highlight: Highlight) => void;
  projectName?: string;
  onPreview?: (highlight: Highlight) => void;
  onDeleted?: (identifier: string) => void;
}) {
  const { pending, run } = useAction();
  const [confirmDelete, setConfirmDelete] = useState(false);
  const select = (selected: boolean) =>
    run(async () =>
      useStudio.getState().upsertHighlight(await api.patchHighlight(highlight.id, { selected })),
    );
  const remove = () =>
    run(async () => {
      await api.deleteHighlight(highlight.id);
      useStudio.getState().removeHighlight(highlight.id);
      onDeleted?.(highlight.id);
      void useStudio.getState().refresh(['projects', 'highlights']);
    }, 'Highlight removed');
  return (
    <>
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
              <span className="absolute bottom-2.5 right-3 rounded bg-black/65 px-1.5 py-0.5 font-mono text-[11px]">
                {time(highlight.duration)}
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
          <button
            onClick={() => onEdit(highlight)}
            className="block w-full truncate text-left text-xs font-semibold leading-5 hover:text-primary"
            title={highlight.title}
          >
            {highlight.title}
          </button>
          {projectName && (
            <p className="mt-0.5 truncate text-[11px] text-zinc-500">{projectName}</p>
          )}
          <div className="mt-2 flex items-center justify-between gap-2 text-[11px] text-zinc-500">
            <span className="truncate">
              {label(highlight.category)} · {time(highlight.start)}–{time(highlight.end)}
            </span>
            {highlight.ai_review && (
              <span className="shrink-0 text-primary">
                AI {highlight.ai_review.score.toFixed(0)}
              </span>
            )}
          </div>
          <div className="mt-3 flex items-center justify-between gap-2 border-t border-border pt-2.5">
            <span
              className={cn(
                'flex items-center gap-1.5 text-[11px]',
                highlight.transcript.length ? 'text-zinc-400' : 'text-zinc-600',
              )}
            >
              <Captions className="size-3" />
              {highlight.caption_error
                ? 'Subtitles need attention'
                : highlight.caption_status === 'pending'
                  ? 'Subtitles pending'
                  : highlight.caption_status === 'failed'
                    ? 'Subtitles failed'
                    : highlight.caption_status === 'no_speech'
                      ? 'No speech detected'
                      : highlight.transcript.length
                        ? `${highlight.transcript.length} subtitles`
                        : 'No transcript yet'}
            </span>
            <div className="flex items-center gap-1">
              <Button
                variant="ghost"
                size="icon"
                className="!size-6"
                aria-label={`Delete ${highlight.title}`}
                onClick={() => setConfirmDelete(true)}
              >
                <Trash2 className="!size-3" />
              </Button>
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
        </div>
      </Card>
      <Dialog
        open={confirmDelete}
        onOpenChange={(open) => {
          if (!pending) setConfirmDelete(open);
        }}
      >
        <DialogContent hideClose={pending}>
          <DialogTitle>Delete this highlight?</DialogTitle>
          <DialogDescription>
            Remove “{highlight.title}” and its subtitle edits from the project. Existing exported
            files remain available. This cannot be undone.
          </DialogDescription>
          <div className="mt-6 flex justify-end gap-2">
            <Button variant="secondary" disabled={pending} onClick={() => setConfirmDelete(false)}>
              Keep highlight
            </Button>
            <Button variant="destructive" loading={pending} onClick={() => void remove()}>
              <Trash2 />
              Delete highlight
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}
