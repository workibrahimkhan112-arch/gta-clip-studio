import { useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { AlertTriangle, Download, ListChecks } from '../icons';
import type { CaptionStyle, Highlight } from '../types/api';
import { api } from '../services/api';
import { useAction } from '../hooks/useAction';
import { useStudio } from '../stores/studio';
import { Button, Checkbox, Select } from './ui';
import { layoutExportProblem } from '../lib/layout';

export const captionOptions = [
  { value: 'none', label: 'No captions' },
  { value: 'standard', label: 'Standard captions' },
  { value: 'animated', label: 'Animated captions' },
];
export function ExportControls({ highlights }: { highlights: Highlight[] }) {
  const settings = useStudio((s) => s.data.settings);
  const health = useStudio((s) => s.data.health);
  const projects = useStudio((s) => s.data.projects);
  const [style, setStyle] = useState<CaptionStyle>('standard');
  const [touched, setTouched] = useState(false);
  const { run, pending } = useAction();
  const selection = useAction();
  const navigate = useNavigate();
  useEffect(() => {
    if (settings && !touched) setStyle(settings.caption_style);
  }, [settings, touched]);
  const selected = highlights.filter((h) => h.selected);
  const missing = selected.filter((h) => !h.transcript.length).length;
  const tooMany = selected.length > 50;
  const blockedProjects = [...new Set(selected.map((h) => h.project_id))].filter((id) =>
    layoutExportProblem(projects.find((p) => p.id === id)),
  );
  const selectAll = (value: boolean) =>
    selection.run(async () => {
      const results = await Promise.allSettled(
        highlights
          .filter((h) => h.selected !== value)
          .map(async (h) => {
            const updated = await api.patchHighlight(h.id, { selected: value });
            useStudio.getState().upsertHighlight(updated);
          }),
      );
      const failed = results.filter((r) => r.status === 'rejected').length;
      if (failed)
        throw new Error(
          `${failed} selections could not be saved. Successful changes have been kept. Try again.`,
        );
    });
  const exportBatch = () =>
    run(async () => {
      const jobs = await api.batchExport(
        selected.map((h) => h.id),
        style,
      );
      jobs.forEach((job) => useStudio.getState().upsertJob(job));
      void useStudio.getState().refresh(['jobs', 'exports']);
      navigate('/exports');
    }, 'Selected clips added to the export queue');
  if (!highlights.length) return null;
  return (
    <div className="mb-5 rounded-lg border border-border bg-card">
      <div className="flex flex-wrap items-center justify-between gap-3 px-4 py-3">
        <div className="flex items-center gap-3">
          <Checkbox
            aria-label="Select all visible highlights"
            checked={
              selected.length === highlights.length
                ? true
                : selected.length
                  ? 'indeterminate'
                  : false
            }
            disabled={selection.pending}
            onCheckedChange={(value) => {
              void selectAll(value === true);
            }}
          />
          <ListChecks className="hidden size-4 text-zinc-500 sm:block" />
          <p className="text-[11px]">
            <span className="font-semibold text-primary">{selected.length}</span>
            <span className="ml-1.5 text-muted-foreground">
              of {highlights.length} visible selected
            </span>
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Select
            className="h-9 w-[175px] text-[11px]"
            aria-label="Batch export caption style"
            value={style}
            options={captionOptions}
            onValueChange={(value) => {
              setTouched(true);
              setStyle(value as CaptionStyle);
            }}
          />
          <Button
            size="default"
            loading={pending}
            disabled={
              !selected.length ||
              tooMany ||
              blockedProjects.length > 0 ||
              selection.pending ||
              health?.capabilities.ffmpeg === false
            }
            onClick={() => {
              void exportBatch();
            }}
          >
            <Download />
            Export selected
            <span className="ml-1 rounded bg-black/10 px-1.5 text-[11px]">{selected.length}</span>
          </Button>
        </div>
      </div>
      {blockedProjects.length > 0 && (
        <div className="space-y-1 border-t border-border px-4 py-3 text-[11px] text-amber-200">
          {blockedProjects.map((id) => (
            <p key={id}>
              {layoutExportProblem(projects.find((p) => p.id === id))}{' '}
              <Link className="underline" to={`/projects/${encodeURIComponent(id)}?tab=layout`}>
                Open {projects.find((p) => p.id === id)?.name ?? 'project'} layout
              </Link>
            </p>
          ))}
        </div>
      )}
      {tooMany && (
        <p className="border-t border-border px-4 py-2.5 text-[11px] text-amber-200">
          Select at most 50 clips per export batch. Deselect clips or filter by project first.
        </p>
      )}
      {style !== 'none' && missing > 0 && (
        <p className="flex items-start gap-2 border-t border-border px-4 py-2.5 text-[11px] leading-relaxed text-amber-200/75">
          <AlertTriangle className="mt-0.5 size-3 shrink-0" />
          {missing} selected clip{missing > 1 ? 's need' : ' needs'} subtitles. They generate on
          export; choose “No captions” to skip.
          {settings?.caption_provider === 'groq' && ' Groq will receive the clip audio.'}
        </p>
      )}
    </div>
  );
}
