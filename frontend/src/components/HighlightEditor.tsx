import { useEffect, useMemo, useRef, useState } from 'react';
import { Captions, Check, Download, Plus, Save, Trash2 } from '../icons';
import { Link, useNavigate } from 'react-router-dom';
import type { CaptionStyle, Highlight, SubtitleStyle, TranscriptSegment } from '../types/api';
import { api } from '../services/api';
import { useStudio } from '../stores/studio';
import { useAction } from '../hooks/useAction';
import { cn, isActive, validateTranscript } from '../lib/utils';
import { insertSubtitle, subtitleTime } from '../lib/subtitles';
import { layoutExportProblem, projectLayout } from '../lib/layout';
import {
  Button,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
  Input,
  Label,
  Select,
  Textarea,
} from './ui';
import { ErrorBanner } from './shared';
import { captionOptions } from './ExportControls';
import { WordTimingsEditor } from './WordTimingsEditor';
import { AIReviewDetails } from './AIReviewDetails';
import { SubtitlePlayer } from './SubtitlePlayer';
import { SubtitleStyleControls } from './SubtitleStyleControls';
import { TitleSuggestions } from './TitleSuggestions';

export function HighlightEditor({
  highlight,
  onClose,
}: {
  highlight: Highlight | null;
  onClose: () => void;
}) {
  useEffect(() => {
    if (highlight) document.querySelectorAll('video').forEach((video) => video.pause());
  }, [highlight?.id]);
  return (
    <Dialog
      open={!!highlight}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      {highlight && <EditorContent key={highlight.id} highlight={highlight} onClose={onClose} />}
    </Dialog>
  );
}

function EditorContent({ highlight, onClose }: { highlight: Highlight; onClose: () => void }) {
  const { settings, health, projects, jobs } = useStudio((s) => s.data);
  const project = projects.find((item) => item.id === highlight.project_id);
  const layout = useMemo(() => (project ? projectLayout(project) : null), [project]);
  const exportProblem = layoutExportProblem(project);
  const projectBusy = jobs.some((job) => job.project_id === highlight.project_id && isActive(job));
  const captionsBusy = jobs.some(
    (job) => job.project_id === highlight.project_id && job.kind === 'captions' && isActive(job),
  );
  const [title, setTitle] = useState(highlight.title);
  const [segments, setSegments] = useState<TranscriptSegment[]>(() =>
    structuredClone(highlight.transcript),
  );
  const [style, setStyle] = useState<CaptionStyle>(settings?.caption_style ?? 'standard');
  const [subtitleStyle, setSubtitleStyle] = useState<SubtitleStyle | null>(
    highlight.subtitle_style ?? null,
  );
  const [panel, setPanel] = useState<'text' | 'style'>('text');
  const [position, setPosition] = useState(0);
  const [seek, setSeek] = useState({ time: 0, token: 0 });
  const [expanded, setExpanded] = useState<number | null>(null);
  const [validation, setValidation] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [confirmDiscard, setConfirmDiscard] = useState(false);
  const [confirmGenerate, setConfirmGenerate] = useState(false);
  const serverTranscript = JSON.stringify(highlight.transcript);
  const serverStyle = JSON.stringify(highlight.subtitle_style ?? null);
  const previous = useRef({
    title: highlight.title,
    transcript: serverTranscript,
    style: serverStyle,
  });
  const [baseline, setBaseline] = useState(previous.current);
  const rows = useRef<(HTMLDivElement | null)[]>([]);
  const generation = useAction();
  const { pending, run } = useAction();
  const navigate = useNavigate();
  const dirty =
    title !== baseline.title ||
    JSON.stringify(segments) !== baseline.transcript ||
    JSON.stringify(subtitleStyle) !== baseline.style;
  const activeIndex = segments.findIndex(
    (segment) => position >= segment.start && position < segment.end,
  );
  const missingWordTimes =
    style === 'animated' && segments.some((segment) => !segment.words.length);

  useEffect(() => {
    const old = previous.current;
    const incoming = { title: highlight.title, transcript: serverTranscript, style: serverStyle };
    setSubtitleStyle((value) =>
      JSON.stringify(value) === old.style
        ? (JSON.parse(serverStyle) as SubtitleStyle | null)
        : value,
    );
    setTitle((value) => (value === old.title ? incoming.title : value));
    setSegments((value) =>
      JSON.stringify(value) === old.transcript
        ? (JSON.parse(serverTranscript) as TranscriptSegment[])
        : value,
    );
    setBaseline(incoming);
    previous.current = incoming;
  }, [highlight.title, serverTranscript, serverStyle]);
  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = '';
    };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [dirty]);
  useEffect(() => {
    if (activeIndex < 0 || ['INPUT', 'TEXTAREA'].includes(document.activeElement?.tagName ?? ''))
      return;
    rows.current[activeIndex]?.scrollIntoView?.({ block: 'nearest' });
  }, [activeIndex]);
  const changed = () => {
    setSaved(false);
    setValidation(null);
    setConfirmGenerate(false);
  };
  const updateSegment = (index: number, patch: Partial<TranscriptSegment>) => {
    setSegments((items) =>
      items.map((segment, i) => (i === index ? { ...segment, ...patch, words: [] } : segment)),
    );
    changed();
  };
  const jump = (seconds: number) => {
    setSeek((old) => ({ time: seconds, token: old.token + 1 }));
    setPosition(seconds);
  };
  const addSegment = () => {
    try {
      const result = insertSubtitle(segments, position, highlight.duration);
      setSegments(result.segments);
      setExpanded(result.index);
      jump(result.segments[result.index].start);
      changed();
    } catch (error) {
      setValidation(error instanceof Error ? error.message : 'Could not add a subtitle.');
    }
  };
  const generate = async (force: boolean) => {
    await generation.run(async () => {
      const job = await api.generateCaptions(highlight.id, force);
      useStudio.getState().upsertJob(job);
      setConfirmGenerate(false);
    }, 'Subtitle generation queued');
  };
  const save = async (exportAfter: boolean) => {
    const problem =
      exportAfter && exportProblem
        ? exportProblem
        : exportAfter && missingWordTimes
          ? 'Animated subtitles need word timings. Regenerate subtitles or choose Standard.'
          : !title.trim()
            ? 'Give this clip a title.'
            : title.trim().length > 160
              ? 'Use a title of at most 160 characters.'
              : validateTranscript(segments, highlight.duration);
    if (problem) {
      setValidation(problem);
      return;
    }
    setValidation(null);
    await run(
      async () => {
        const updated = await api.patchHighlight(highlight.id, {
          ...(title !== baseline.title ? { title: title.trim() } : {}),
          ...(JSON.stringify(segments) !== baseline.transcript
            ? { transcript: segments.map((segment) => ({ ...segment, text: segment.text.trim() })) }
            : {}),
          ...(JSON.stringify(subtitleStyle) !== baseline.style
            ? { subtitle_style: subtitleStyle }
            : {}),
        });
        useStudio.getState().upsertHighlight(updated);
        setTitle(updated.title);
        setSegments(structuredClone(updated.transcript));
        setSubtitleStyle(updated.subtitle_style ?? null);
        setBaseline({
          title: updated.title,
          transcript: JSON.stringify(updated.transcript),
          style: JSON.stringify(updated.subtitle_style ?? null),
        });
        setSaved(true);
        if (exportAfter) {
          const job = await api.exportHighlight(highlight.id, style);
          useStudio.getState().upsertJob(job);
          void useStudio.getState().refresh(['exports', 'jobs']);
          onClose();
          navigate('/exports');
        }
      },
      exportAfter ? 'Export queued' : 'Subtitles saved',
    );
  };
  const generationDisabled =
    pending || dirty || projectBusy || !settings || settings.caption_style === 'none';
  return (
    <DialogContent
      hideClose
      className="flex max-h-[94dvh] max-w-[1120px] flex-col overflow-hidden p-0"
      onInteractOutside={(event) => {
        if (dirty || pending) event.preventDefault();
      }}
      onEscapeKeyDown={(event) => {
        if (dirty || pending) event.preventDefault();
      }}
    >
      <header className="shrink-0 border-b border-border px-5 py-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <DialogTitle className="flex items-center gap-2">
            <Captions className="size-5 text-primary" />
            Subtitles
          </DialogTitle>
          <span className="max-w-[60%] truncate text-xs text-zinc-400">{title}</span>
        </div>
        <DialogDescription>
          Edit text, timing, and appearance. Saved style applies to this highlight’s exports.
        </DialogDescription>
      </header>
      <div className="grid min-h-0 flex-1 overflow-y-auto lg:grid-cols-[minmax(0,1fr)_minmax(320px,400px)] lg:overflow-hidden">
        <div className="flex min-h-0 min-w-0 flex-col border-b border-border lg:border-b-0 lg:border-r">
          <div
            role="group"
            aria-label="Subtitle editor panels"
            className="flex shrink-0 gap-1 border-b border-border px-4 py-2"
          >
            {(['text', 'style'] as const).map((value) => (
              <Button
                key={value}
                type="button"
                size="sm"
                variant={panel === value ? 'secondary' : 'ghost'}
                aria-pressed={panel === value}
                onClick={() => setPanel(value)}
              >
                {value === 'text' ? 'Text' : 'Style'}
              </Button>
            ))}
          </div>
          {panel === 'style' && (
            <div className="min-h-0 flex-1 overflow-y-auto">
              <SubtitleStyleControls
                value={subtitleStyle}
                position={layout?.caption_position ?? 'lower_middle'}
                disabled={pending}
                onChange={(value) => {
                  setSubtitleStyle(value);
                  changed();
                }}
              />
            </div>
          )}
          <div hidden={panel !== 'text'} className="shrink-0 border-b border-border px-4 py-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="text-xs text-zinc-400">
                {segments.length} subtitle{segments.length === 1 ? '' : 's'}
                {captionsBusy ? ' · Generating…' : ''}
              </p>
              <div className="flex gap-2">
                <Button
                  size="sm"
                  variant="ghost"
                  loading={generation.pending}
                  disabled={generationDisabled}
                  onClick={() => {
                    if (segments.length) setConfirmGenerate(true);
                    else void generate(false);
                  }}
                >
                  {captionsBusy
                    ? 'Generating…'
                    : highlight.caption_status === 'failed'
                      ? 'Retry captions'
                      : segments.length
                        ? 'Regenerate'
                        : 'Generate captions'}
                </Button>
                <Button
                  size="sm"
                  variant="secondary"
                  disabled={pending || segments.length >= 200}
                  onClick={addSegment}
                  title="Add at the playhead"
                >
                  <Plus />
                  Add caption
                </Button>
              </div>
            </div>
            {dirty && (
              <p className="mt-2 text-[11px] text-zinc-500">
                Unsaved changes. Save before regenerating.
              </p>
            )}
            {settings?.caption_style === 'none' && (
              <p className="mt-2 text-[11px] text-zinc-500">
                Generation is off in{' '}
                <Link
                  to="/settings"
                  onClick={(event) => {
                    if (dirty) {
                      event.preventDefault();
                      setValidation('Save or discard your edits before opening Settings.');
                    } else onClose();
                  }}
                  className="underline"
                >
                  Settings
                </Link>
                .
              </p>
            )}
            {highlight.caption_error && (
              <p role="status" className="mt-2 text-xs text-amber-200">
                {highlight.caption_error}
              </p>
            )}
            {confirmGenerate && (
              <div className="mt-3 rounded-md border border-amber-300/20 bg-amber-300/5 p-3">
                <p className="text-xs text-amber-100">
                  Replace saved subtitles? Manual edits will be lost.
                </p>
                <div className="mt-3 flex gap-2">
                  <Button
                    size="sm"
                    disabled={generationDisabled}
                    loading={generation.pending}
                    onClick={() => void generate(true)}
                  >
                    Replace subtitles
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => setConfirmGenerate(false)}>
                    Keep current
                  </Button>
                </div>
              </div>
            )}
          </div>
          <div
            className="min-h-32 flex-1 space-y-2 overflow-y-auto p-3 lg:min-h-0"
            hidden={panel !== 'text'}
            aria-label="Subtitle list"
          >
            {!segments.length ? (
              <div className="px-4 py-12 text-center">
                <Captions className="mx-auto mb-3 size-6 text-zinc-600" />
                <p className="text-sm text-zinc-300">No subtitles yet</p>
                <p className="mt-2 text-xs text-zinc-500">
                  Generate them from the clip audio, or add a line.
                </p>
              </div>
            ) : (
              segments.map((segment, index) => (
                <div
                  key={index}
                  ref={(node) => {
                    rows.current[index] = node;
                  }}
                  className={cn(
                    'rounded-lg border border-border bg-black/10 p-3',
                    activeIndex === index && 'border-primary/50 bg-primary/[0.035]',
                  )}
                >
                  <div className="mb-2 flex items-center justify-between gap-2">
                    <button
                      type="button"
                      aria-label={`Preview subtitle ${index + 1}`}
                      onClick={() => jump(segment.start)}
                      className="flex items-center gap-2 rounded text-left text-[11px] text-zinc-400 hover:text-primary focus-visible:outline-primary"
                    >
                      <span className="w-5 font-mono text-zinc-500">
                        {String(index + 1).padStart(2, '0')}
                      </span>
                      <span className="font-mono">
                        {subtitleTime(segment.start)} → {subtitleTime(segment.end)}
                      </span>
                    </button>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="hover:text-red-300"
                      aria-label={`Remove caption ${index + 1}`}
                      disabled={pending}
                      onClick={() => {
                        setSegments((items) => items.filter((_, i) => i !== index));
                        setExpanded(null);
                        changed();
                      }}
                    >
                      <Trash2 className="!size-3.5" />
                    </Button>
                  </div>
                  <Textarea
                    aria-label={`Caption ${index + 1} text`}
                    value={segment.text}
                    placeholder="Type what is said…"
                    disabled={pending}
                    className="min-h-[64px] resize-none text-xs leading-relaxed"
                    onChange={(event) => updateSegment(index, { text: event.target.value })}
                  />
                  <details open={expanded === index} className="mt-2 text-xs text-zinc-500">
                    <summary
                      className="cursor-pointer py-1 text-[11px]"
                      onClick={(event) => {
                        event.preventDefault();
                        setExpanded((old) => (old === index ? null : index));
                      }}
                    >
                      Timing and words
                    </summary>
                    <div className="mt-2 grid grid-cols-2 gap-3">
                      <div>
                        <Label htmlFor={`subtitle-start-${index}`} className="text-[11px]">
                          Start (seconds)
                        </Label>
                        <Input
                          id={`subtitle-start-${index}`}
                          aria-label={`Caption ${index + 1} start seconds`}
                          type="number"
                          step="0.01"
                          min={0}
                          max={highlight.duration}
                          value={Number.isNaN(segment.start) ? '' : segment.start}
                          disabled={pending}
                          onChange={(event) =>
                            updateSegment(index, { start: event.target.valueAsNumber })
                          }
                        />
                      </div>
                      <div>
                        <Label htmlFor={`subtitle-end-${index}`} className="text-[11px]">
                          End (seconds)
                        </Label>
                        <Input
                          id={`subtitle-end-${index}`}
                          aria-label={`Caption ${index + 1} end seconds`}
                          type="number"
                          step="0.01"
                          min={0}
                          max={highlight.duration}
                          value={Number.isNaN(segment.end) ? '' : segment.end}
                          disabled={pending}
                          onChange={(event) =>
                            updateSegment(index, { end: event.target.valueAsNumber })
                          }
                        />
                      </div>
                    </div>
                    <WordTimingsEditor
                      words={segment.words}
                      start={segment.start}
                      end={segment.end}
                      disabled={pending}
                      onApply={(words) => {
                        setSegments((items) =>
                          items.map((item, i) => (i === index ? { ...item, words } : item)),
                        );
                        changed();
                      }}
                    />
                  </details>
                </div>
              ))
            )}
            <TitleSuggestions
              highlight={highlight}
              disabled={dirty || pending}
              onApplied={(value) => {
                setTitle((draft) => (draft === baseline.title ? value : draft));
                setSaved(false);
              }}
            />
            <details className="rounded-md border border-border px-3 py-2 text-xs text-zinc-500">
              <summary className="cursor-pointer py-1">Clip details</summary>
              <div className="space-y-3 py-3">
                <div>
                  <Label htmlFor="clip-title">Clip title</Label>
                  <Input
                    id="clip-title"
                    value={title}
                    maxLength={160}
                    disabled={pending}
                    onChange={(event) => {
                      setTitle(event.target.value);
                      changed();
                    }}
                  />
                </div>
                <p>
                  Detection score {highlight.score.toFixed(0)}/100 · {highlight.explanation}
                </p>
                <AIReviewDetails highlight={highlight} />
              </div>
            </details>
          </div>
          {validation && (
            <div className="shrink-0 px-4 pb-3">
              <ErrorBanner message={validation} />
            </div>
          )}
        </div>
        <aside
          className="min-w-0 space-y-4 bg-black/10 p-4 lg:overflow-y-auto"
          aria-label="Subtitle preview"
        >
          <div>
            <Label htmlFor="single-caption-style" className="text-[11px]">
              Export captions
            </Label>
            <Select
              id="single-caption-style"
              value={style}
              onValueChange={(value) => setStyle(value as CaptionStyle)}
              options={captionOptions}
              disabled={pending}
            />
          </div>
          {layout ? (
            <SubtitlePlayer
              projectId={highlight.project_id}
              start={highlight.start}
              end={highlight.end}
              layout={layout}
              segments={segments}
              style={style}
              subtitleStyle={subtitleStyle}
              editingStyle={panel === 'style' && !pending}
              onStyleChange={(value) => {
                setSubtitleStyle(value);
                changed();
              }}
              seek={seek}
              onTimeChange={setPosition}
            />
          ) : (
            <p className="text-xs text-zinc-400">Open the project to load its preview.</p>
          )}
          {style === 'none' && (
            <p className="text-xs text-zinc-400">Subtitles are off for this export.</p>
          )}
          {missingWordTimes && (
            <p role="status" className="text-xs text-amber-200">
              Animated subtitles need word timings. Regenerate them or choose Standard.
            </p>
          )}
          <Link
            to={`/projects/${encodeURIComponent(highlight.project_id)}?tab=layout`}
            onClick={(event) => {
              if (dirty) {
                event.preventDefault();
                setValidation('Save or discard your subtitle edits before opening Layout.');
              } else onClose();
            }}
            className="inline-block text-xs text-primary underline underline-offset-2"
          >
            Edit video layout
          </Link>
          {exportProblem && <p className="text-xs text-amber-200">{exportProblem}</p>}
        </aside>
      </div>
      <footer className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-t border-border px-5 py-3">
        <div>
          {confirmDiscard ? (
            <div className="flex items-center gap-2">
              <span className="text-xs text-amber-200">Discard unsaved edits?</span>
              <Button variant="destructive" size="sm" onClick={onClose}>
                Discard
              </Button>
              <Button variant="ghost" size="sm" onClick={() => setConfirmDiscard(false)}>
                Keep editing
              </Button>
            </div>
          ) : (
            <Button
              variant="ghost"
              disabled={pending}
              onClick={() => {
                if (dirty) setConfirmDiscard(true);
                else onClose();
              }}
            >
              Close
            </Button>
          )}
        </div>
        <div className="flex gap-2">
          <Button variant="secondary" loading={pending} onClick={() => void save(false)}>
            {saved ? <Check /> : <Save />}
            {saved ? 'Saved' : 'Save changes'}
          </Button>
          <Button
            loading={pending}
            disabled={
              projectBusy ||
              health?.capabilities.ffmpeg === false ||
              !!exportProblem ||
              missingWordTimes
            }
            onClick={() => void save(true)}
          >
            <Download />
            Save & export
          </Button>
        </div>
      </footer>
    </DialogContent>
  );
}
