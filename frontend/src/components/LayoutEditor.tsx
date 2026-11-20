import { useEffect, useId, useRef, useState } from 'react';
import type { KeyboardEvent as ReactKeyboardEvent, PointerEvent as ReactPointerEvent } from 'react';
import type { CropRect, LayoutConfig, Project } from '../types/api';
import { api } from '../services/api';
import { useStudio } from '../stores/studio';
import { useAction } from '../hooks/useAction';
import {
  changeCrop,
  containedBounds,
  cropHandles,
  resizedCrop,
  type CropHandle,
  fullCrop,
  movedCrop,
  projectLayout,
  redrawnCrop,
  sourcePoint,
  validCrop,
} from '../lib/layout';
import { Button, Input, Label, Select } from './ui';
import { MediaPlayer, type SeekTarget } from './MediaPlayer';
import { LayoutPreview } from './LayoutPreview';

type Region = 'facecam' | 'gameplay';
type CropTool = 'draw' | 'move' | 'resize';
const cropFields = ['x', 'y', 'width', 'height'] as const;
const isFullFrame = (rect: CropRect | null) =>
  !!rect && rect.x === 0 && rect.y === 0 && rect.width === 1 && rect.height === 1;

export function CropOverlay({
  video,
  target,
  rect,
  other,
  tool,
  onChange,
}: {
  video: HTMLVideoElement | null;
  target: Region;
  rect: CropRect | null;
  other: CropRect | null;
  tool?: CropTool;
  onChange: (rect: CropRect) => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const helpId = useId();
  const [bounds, setBounds] = useState({ left: 0, top: 0, width: 0, height: 0 });
  const gesture = useRef<{
    pointer: number;
    start: { x: number; y: number };
    rect: CropRect | null;
    tool: CropTool;
    handle: CropHandle;
  } | null>(null);
  useEffect(() => {
    if (!ref.current || !video) return;
    const measure = () => {
      const box = ref.current!.getBoundingClientRect();
      const area = containedBounds(box, video.videoWidth, video.videoHeight);
      setBounds({ ...area, left: area.left - box.left, top: area.top - box.top });
    };
    const observer = new ResizeObserver(measure);
    observer.observe(ref.current);
    video.addEventListener('resize', measure);
    video.addEventListener('loadedmetadata', measure);
    measure();
    return () => {
      observer.disconnect();
      video.removeEventListener('resize', measure);
      video.removeEventListener('loadedmetadata', measure);
    };
  }, [video]);
  const point = (event: ReactPointerEvent) => {
    const box = ref.current!.getBoundingClientRect();
    return sourcePoint(
      event.clientX,
      event.clientY,
      containedBounds(box, video?.videoWidth ?? 0, video?.videoHeight ?? 0),
    );
  };

  const update = (event: ReactPointerEvent) => {
    const active = gesture.current;
    if (!active || active.pointer !== event.pointerId) return;
    const p = point(event);
    if (active.tool === 'draw') {
      if (Math.abs(active.start.x - p.x) > 0.001 && Math.abs(active.start.y - p.y) > 0.001)
        onChange(redrawnCrop(active.start, p));
    } else if (active.rect) {
      const dx = p.x - active.start.x;
      const dy = p.y - active.start.y;
      onChange(
        active.tool === 'move'
          ? movedCrop(active.rect, dx, dy)
          : resizedCrop(active.rect, active.handle, dx, dy),
      );
    }
  };
  const keyboard = (event: ReactKeyboardEvent, action: 'move' | CropHandle) => {
    if (!rect || !validCrop(rect)) return;
    const step = event.shiftKey ? 0.1 : 0.01;
    const dx = event.key === 'ArrowLeft' ? -step : event.key === 'ArrowRight' ? step : 0;
    const dy = event.key === 'ArrowUp' ? -step : event.key === 'ArrowDown' ? step : 0;
    if (!dx && !dy) return;
    event.preventDefault();
    event.stopPropagation();
    onChange(action === 'move' ? movedCrop(rect, dx, dy) : resizedCrop(rect, action, dx, dy));
  };
  const boxStyle = (r: CropRect) => ({
    left: `${r.x * 100}%`,
    top: `${r.y * 100}%`,
    width: `${r.width * 100}%`,
    height: `${r.height * 100}%`,
  });
  return (
    <div
      ref={ref}
      className="pointer-events-none absolute inset-0 select-none"
      aria-label="Source crop selection"
    >
      <div
        role="group"
        aria-label={`${target === 'facecam' ? 'Facecam' : 'Gameplay'} crop canvas`}
        className="pointer-events-auto absolute touch-none cursor-crosshair overflow-hidden"
        style={bounds}
        onPointerDown={(event) => {
          if (event.button !== 0 || !bounds.width || gesture.current) return;
          const start = point(event);
          const inside =
            rect &&
            start.x >= rect.x &&
            start.y >= rect.y &&
            start.x <= rect.x + rect.width &&
            start.y <= rect.y + rect.height;
          const handleElement = (event.target as HTMLElement).closest<HTMLElement>(
            '[data-crop-handle]',
          );
          const handle = handleElement?.dataset.cropHandle as CropHandle | undefined;
          if (tool && tool !== 'draw' && !inside && !handle) return;
          // A full-frame crop has nowhere outside it to begin a new selection.
          const action = handle
            ? 'resize'
            : (tool ?? (inside && !isFullFrame(rect) ? 'move' : 'draw'));
          event.preventDefault();
          if (document.activeElement instanceof HTMLElement) document.activeElement.blur();
          (
            handleElement ?? event.currentTarget.querySelector<HTMLElement>('[data-crop-move]')
          )?.focus({ preventScroll: true });
          event.currentTarget.setPointerCapture(event.pointerId);
          gesture.current = {
            pointer: event.pointerId,
            start,
            rect,
            tool: action,
            handle: handle ?? 'se',
          };
        }}
        onPointerMove={update}
        onPointerUp={(event) => {
          update(event);
          if (gesture.current?.pointer === event.pointerId) gesture.current = null;
          if (event.currentTarget.hasPointerCapture(event.pointerId))
            event.currentTarget.releasePointerCapture(event.pointerId);
        }}
        onPointerCancel={(event) => {
          if (gesture.current?.pointer === event.pointerId) gesture.current = null;
        }}
        onLostPointerCapture={(event) => {
          if (gesture.current?.pointer === event.pointerId) gesture.current = null;
        }}
      >
        {other && validCrop(other) && (
          <div
            className="pointer-events-none absolute border border-sky-300/60"
            style={boxStyle(other)}
          />
        )}
        {rect && validCrop(rect) && (
          <>
            <button
              type="button"
              aria-label={`Move ${target} crop`}
              data-crop-move
              aria-describedby={helpId}
              className={`absolute border-2 border-primary bg-primary/10 text-left focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-white ${isFullFrame(rect) ? 'cursor-crosshair' : 'cursor-move'}`}
              style={boxStyle(rect)}
              onKeyDown={(event) => keyboard(event, 'move')}
            >
              <span className="absolute left-0 top-0 bg-black/75 px-1.5 py-0.5 text-[11px] capitalize text-primary">
                {target}
              </span>
            </button>
            {cropHandles.map((handle) => {
              const hx = handle.includes('w') ? 0 : handle.includes('e') ? 1 : 0.5;
              const hy = handle.includes('n') ? 0 : handle.includes('s') ? 1 : 0.5;
              return (
                <button
                  key={handle}
                  type="button"
                  aria-label={`Resize ${target} crop${handle === 'se' ? '' : ` ${handle}`}`}
                  aria-describedby={helpId}
                  data-crop-handle={handle}
                  className="absolute size-5 rounded-sm border-2 border-black bg-primary text-black focus-visible:outline focus-visible:outline-2 focus-visible:outline-white"
                  style={{
                    left: `${(rect.x + rect.width * hx) * 100}%`,
                    top: `${(rect.y + rect.height * hy) * 100}%`,
                    transform: `translate(${-hx * 100}%, ${-hy * 100}%)`,
                    cursor: `${handle}-resize`,
                  }}
                  onKeyDown={(event) => keyboard(event, handle)}
                >
                  <span className="sr-only">{handle}</span>
                </button>
              );
            })}
          </>
        )}
      </div>
      <p id={helpId} className="sr-only">
        Arrow keys move the crop or resize the focused edge or corner, keeping the opposite edge
        fixed. Hold Shift for larger steps. Enter exact coordinates in Advanced. To draw a new box,
        drag outside the current crop.
      </p>
    </div>
  );
}

function CropCoordinates({
  region,
  rect,
  onChange,
}: {
  region: Region;
  rect: CropRect | null;
  onChange: (rect: CropRect) => void;
}) {
  const [draft, setDraft] = useState({ x: '', y: '', width: '', height: '' });
  const candidate = {
    x: Number(draft.x),
    y: Number(draft.y),
    width: Number(draft.width),
    height: Number(draft.height),
  };
  const complete = cropFields.every((field) => draft[field].trim() !== '') && validCrop(candidate);
  return (
    <div className="space-y-3 rounded-md border border-border p-3">
      <p className="text-xs capitalize">
        {region} coordinates <span className="text-zinc-500">· 0–1</span>
      </p>
      {!rect && (
        <p className="text-xs text-amber-200">Enter all four coordinates, or draw on the source.</p>
      )}
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        {cropFields.map((field) => (
          <div key={field}>
            <Label htmlFor={`${region}-${field}`} className="text-[11px]">
              {region} {field}
            </Label>
            <Input
              id={`${region}-${field}`}
              type="number"
              min={field === 'x' || field === 'y' ? 0 : 0.001}
              max={1}
              step={0.001}
              value={rect ? Number(rect[field].toFixed(5)) : draft[field]}
              onChange={(event) => {
                if (rect) onChange(changeCrop(rect, field, event.target.valueAsNumber));
                else setDraft((old) => ({ ...old, [field]: event.target.value }));
              }}
            />
          </div>
        ))}
      </div>
      {!rect && (
        <Button
          type="button"
          size="sm"
          variant="secondary"
          disabled={!complete}
          onClick={() => onChange(candidate)}
        >
          Set facecam crop
        </Button>
      )}
    </div>
  );
}

export function LayoutEditor({
  project,
  seek,
  onTimeChange,
}: {
  project: Project;
  seek?: SeekTarget;
  onTimeChange?: (seconds: number) => void;
}) {
  const persisted = JSON.stringify(projectLayout(project));
  const [layout, setLayout] = useState(() => projectLayout(project));
  const [baseline, setBaseline] = useState(persisted);
  const observed = useRef({ id: project.id, persisted });
  const projectId = useRef(project.id);
  projectId.current = project.id;
  const [video, setVideo] = useState<HTMLVideoElement | null>(null);
  const [editing, setEditing] = useState(true);
  const [target, setTarget] = useState<Region>('facecam');
  const { pending, error, run, clearError } = useAction();
  useEffect(() => {
    const previous = observed.current;
    observed.current = { id: project.id, persisted };
    if (previous.id !== project.id) {
      setLayout(JSON.parse(persisted) as LayoutConfig);
      setBaseline(persisted);
      setTarget('facecam');
      setEditing(true);
    } else if (previous.persisted !== persisted) {
      setLayout((draft) =>
        JSON.stringify(draft) === baseline ? (JSON.parse(persisted) as LayoutConfig) : draft,
      );
      setBaseline(persisted);
    }
  }, [project.id, persisted, baseline]);
  const dirty = JSON.stringify(layout) !== baseline;
  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = '';
    };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [dirty]);
  const cropping = editing && layout.mode === 'split' && !pending;
  useEffect(() => {
    if (!video) return;
    // Edit and playback are separate: an overlay never sits over native controls.
    video.controls = !cropping;
    if (cropping) video.pause();
    const play = () => setEditing(false);
    video.addEventListener('play', play);
    return () => {
      video.controls = true;
      video.removeEventListener('play', play);
    };
  }, [video, cropping]);
  const update = <K extends keyof LayoutConfig>(key: K, value: LayoutConfig[K]) =>
    setLayout((old) => ({ ...old, [key]: value }));
  const rect = layout[target];
  const targetLabel = target === 'facecam' ? 'Facecam' : 'Gameplay';
  const valid =
    validCrop(layout.gameplay) &&
    (!layout.facecam || validCrop(layout.facecam)) &&
    (layout.mode !== 'split' || !!layout.facecam);
  const save = () => {
    if (!dirty || !valid || pending) return;
    const submitted = layout;
    const id = project.id;
    void run(async () => {
      const saved = await api.saveLayout(id, submitted);
      useStudio.getState().upsertProject(saved);
      if (projectId.current !== id) return;
      const next = projectLayout(saved);
      setBaseline(JSON.stringify(next));
      setLayout((draft) => (draft === submitted ? next : draft));
    }, 'Layout saved. Already queued exports keep their original layout.');
  };
  return (
    <section aria-label="Layout editor">
      <fieldset disabled={pending} className="min-w-0 space-y-4 p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="text-xs text-zinc-300">
            {layout.mode === 'split'
              ? 'Choose Facecam, then Gameplay. Preview and save.'
              : 'Preview the blurred background, then save.'}
          </p>
          <div
            role="group"
            aria-label="Project layout"
            className="flex gap-1 rounded-lg border border-border p-1"
          >
            {(['split', 'blurred'] as const).map((mode) => (
              <Button
                key={mode}
                type="button"
                size="sm"
                variant={layout.mode === mode ? 'secondary' : 'ghost'}
                aria-pressed={layout.mode === mode}
                onClick={() => update('mode', mode)}
              >
                {mode === 'split' ? 'Split' : 'Blurred'}
              </Button>
            ))}
          </div>
        </div>
        <div className="grid items-start gap-5 md:grid-cols-[minmax(0,1fr)_200px] xl:grid-cols-[minmax(0,1fr)_240px]">
          <div className="min-w-0 space-y-3">
            <div className="flex min-h-8 flex-wrap items-center justify-between gap-2">
              <h3 className="text-xs font-semibold">Source</h3>
              {layout.mode === 'split' && (
                <div role="group" aria-label="Source interaction" className="flex gap-1">
                  <Button
                    type="button"
                    size="sm"
                    variant={editing ? 'secondary' : 'ghost'}
                    aria-pressed={editing}
                    onClick={() => setEditing(true)}
                  >
                    Edit crops
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant={!editing ? 'secondary' : 'ghost'}
                    aria-pressed={!editing}
                    onClick={() => setEditing(false)}
                  >
                    Play / seek
                  </Button>
                </div>
              )}
            </div>
            {layout.mode === 'split' && (
              <div role="group" aria-label="Crop steps" className="grid grid-cols-2 gap-2">
                {(['facecam', 'gameplay'] as const).map((region, index) => (
                  <Button
                    key={region}
                    type="button"
                    variant={target === region ? 'secondary' : 'ghost'}
                    aria-pressed={target === region}
                    className={
                      target === region
                        ? 'h-auto justify-between border-primary/50 py-2'
                        : 'h-auto justify-between border border-border py-2'
                    }
                    onClick={() => {
                      setTarget(region);
                      setEditing(true);
                    }}
                  >
                    <span>
                      {index + 1} {region === 'facecam' ? 'Facecam' : 'Gameplay'}
                    </span>
                    <span
                      className={
                        region === 'facecam' && !layout.facecam
                          ? 'text-[11px] text-amber-200'
                          : 'text-[11px] text-zinc-400'
                      }
                    >
                      {region === 'facecam'
                        ? layout.facecam
                          ? 'Selected'
                          : 'Required'
                        : isFullFrame(layout.gameplay)
                          ? 'Full frame'
                          : 'Selected'}
                    </span>
                  </Button>
                ))}
              </div>
            )}
            <MediaPlayer
              projectId={project.id}
              seek={seek}
              onTimeChange={onTimeChange}
              onVideo={setVideo}
              overlay={
                cropping ? (
                  <CropOverlay
                    key={target}
                    video={video}
                    target={target}
                    rect={rect}
                    other={layout[target === 'facecam' ? 'gameplay' : 'facecam']}
                    onChange={(crop) => update(target, crop)}
                  />
                ) : undefined
              }
            />
            {layout.mode === 'split' && (
              <>
                <div className="space-y-1 text-xs">
                  <p className="text-zinc-400">
                    {cropping
                      ? isFullFrame(rect)
                        ? 'Editing crops · drag to crop the full frame, or keep it as is.'
                        : 'Editing crops · draw outside the box, drag inside to move, or drag any edge or corner to resize.'
                      : 'Playback · crop editing off.'}
                  </p>
                  {!layout.facecam && (
                    <p role="status" className="text-amber-200">
                      Facecam required: draw its box on the source or enter coordinates in Advanced.
                    </p>
                  )}
                </div>
                <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-border p-3">
                  <span className="text-xs text-zinc-400">{targetLabel} scaling</span>
                  <div role="group" aria-label={`${targetLabel} scaling`} className="flex gap-1">
                    {(['fit', 'fill'] as const).map((fit) => (
                      <Button
                        key={fit}
                        type="button"
                        size="sm"
                        variant={layout[`${target}_fit`] === fit ? 'secondary' : 'ghost'}
                        aria-pressed={layout[`${target}_fit`] === fit}
                        title={
                          fit === 'fit'
                            ? 'Keep the whole crop; add padding if needed'
                            : 'Fill the panel; trim edges if needed'
                        }
                        onClick={() => update(`${target}_fit`, fit)}
                      >
                        {fit === 'fit' ? 'Fit' : 'Fill'}
                      </Button>
                    ))}
                  </div>
                  <p className="w-full text-[11px] text-zinc-500">
                    Fit keeps the whole crop. Fill trims edges to fill the panel.
                  </p>
                </div>
              </>
            )}
          </div>
          <div className="mx-auto w-full max-w-[240px] space-y-3">
            <h3 className="flex min-h-8 items-center justify-between text-xs font-semibold">
              Portrait <span className="font-normal text-zinc-500">9:16</span>
            </h3>
            <LayoutPreview video={video} layout={layout} />
            <p className="text-center text-[11px] text-zinc-400">
              {dirty ? 'Unsaved preview' : 'Saved layout'}
            </p>
            {layout.mode === 'split' && (
              <div className="space-y-2 rounded-md border border-border p-3">
                <Label htmlFor="split-ratio" className="mb-0 text-[11px]">
                  Facecam share · {Math.round(layout.split_ratio * 100)}%
                </Label>
                <input
                  id="split-ratio"
                  className="w-full accent-primary"
                  type="range"
                  min={20}
                  max={70}
                  step={1}
                  value={Math.round(layout.split_ratio * 100)}
                  onChange={(event) => update('split_ratio', event.target.valueAsNumber / 100)}
                />
                <div role="group" aria-label="Split presets" className="flex justify-between gap-1">
                  {[0.3, 0.5, 0.7].map((ratio) => (
                    <Button
                      key={ratio}
                      type="button"
                      size="sm"
                      variant={layout.split_ratio === ratio ? 'secondary' : 'ghost'}
                      className="px-2"
                      aria-pressed={layout.split_ratio === ratio}
                      onClick={() => update('split_ratio', ratio)}
                    >
                      {Math.round(ratio * 100)}/{Math.round((1 - ratio) * 100)}
                    </Button>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>
        <details className="rounded-md border border-border">
          <summary className="cursor-pointer px-3 py-3 text-xs text-zinc-300 focus-visible:outline-primary">
            Advanced · coordinates & captions
          </summary>
          <div className="space-y-3 border-t border-border p-3">
            {layout.mode === 'split' && (
              <>
                <p className="text-xs text-zinc-400">
                  Keyboard: Tab to a crop, edge, or corner and use arrow keys. Coordinates below use
                  the source frame, from 0 to 1.
                </p>
                {(['facecam', 'gameplay'] as const).map((region) => (
                  <CropCoordinates
                    key={`${project.id}-${region}-${!!layout[region]}`}
                    region={region}
                    rect={layout[region]}
                    onChange={(crop) => update(region, crop)}
                  />
                ))}
                <div className="flex flex-wrap gap-2">
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    disabled={!layout.facecam}
                    onClick={() => update('facecam', null)}
                  >
                    Clear facecam
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    onClick={() => update('gameplay', fullCrop())}
                  >
                    Reset gameplay to full frame
                  </Button>
                </div>
              </>
            )}
            <div className="max-w-xs">
              <Label htmlFor="caption-position">Caption position</Label>
              <Select
                id="caption-position"
                disabled={pending}
                value={layout.caption_position}
                onValueChange={(value) =>
                  update('caption_position', value as LayoutConfig['caption_position'])
                }
                options={[
                  { value: 'lower_middle', label: 'Lower middle' },
                  { value: 'center', label: 'Center' },
                  { value: 'bottom', label: 'Bottom' },
                ]}
              />
            </div>
          </div>
        </details>
        {error && (
          <p role="alert" className="text-xs text-red-300">
            {error}
          </p>
        )}
        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border pt-4">
          <p className="text-[11px] text-zinc-500">
            Layout changes never alter already queued exports.
          </p>
          <div className="flex gap-2">
            <Button
              type="button"
              variant="ghost"
              size="sm"
              disabled={!dirty}
              onClick={() => {
                setLayout(JSON.parse(baseline) as LayoutConfig);
                clearError();
              }}
            >
              Discard layout edits
            </Button>
            <Button
              type="button"
              size="sm"
              loading={pending}
              disabled={!dirty || !valid}
              onClick={save}
            >
              Save layout
            </Button>
          </div>
        </div>
      </fieldset>
    </section>
  );
}
