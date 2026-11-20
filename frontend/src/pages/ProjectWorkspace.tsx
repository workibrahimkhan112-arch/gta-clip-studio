import { useEffect, useRef, useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import {
  ArrowLeft,
  Captions,
  ChevronRight,
  Film,
  Loader2,
  Play,
  RefreshCw,
  ScanLine,
  Sparkles,
  Trash2,
  X,
} from 'lucide-react';
import { api } from '../services/api';
import { useStudio } from '../stores/studio';
import { useAction } from '../hooks/useAction';
import { errorMessage, isActive } from '../lib/utils';
import {
  Badge,
  Button,
  Card,
  CardHeader,
  CardTitle,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
  Select,
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from '../components/ui';
import {
  DependencyWarnings,
  EmptyState,
  ErrorBanner,
  ResourceState,
  StatusBadge,
} from '../components/shared';
import { MediaPlayer, type SeekTarget } from '../components/MediaPlayer';
import { LayoutEditor } from '../components/LayoutEditor';
import { ProjectEnhancements } from '../components/ProjectEnhancements';
import { ProjectDetails } from '../components/ProjectDetails';
import { EventTimeline } from '../components/EventTimeline';
import { JobCard } from '../components/JobCard';
import { HighlightCard } from '../components/HighlightCard';
import { HighlightEditor } from '../components/HighlightEditor';
import { ExportControls } from '../components/ExportControls';
import type { Highlight, EventSnapshot } from '../types/api';

export function ProjectWorkspace() {
  const { projectId = '' } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const tab = searchParams.get('tab') === 'layout' ? 'layout' : 'review';
  const requestedTab = useRef(tab);
  useEffect(() => {
    requestedTab.current = tab;
  }, [tab]);
  const [layoutVisited, setLayoutVisited] = useState(tab === 'layout');
  const layoutRef = useRef<HTMLDivElement>(null);
  const { projects, highlights: allHighlights, jobs, health, settings } = useStudio((s) => s.data);
  const project = projects.find((p) => p.id === projectId);
  const [sort, setSort] = useState('score');
  const highlights = allHighlights
    .filter((h) => h.project_id === projectId)
    .sort((a, b) =>
      sort === 'ai'
        ? (b.ai_review?.score ?? -1) - (a.ai_review?.score ?? -1) || b.score - a.score
        : b.score - a.score,
    );
  const projectJobs = jobs
    .filter((j) => j.project_id === projectId)
    .sort((a, b) => b.updated_at.localeCompare(a.updated_at));
  const activeJobs = projectJobs.filter(isActive);
  const failedJobs = projectJobs.filter(
    (job) => !isActive(job) && (job.status === 'failed' || !!job.error),
  );
  const previousJobs = projectJobs.filter(
    (job) => !isActive(job) && job.status !== 'failed' && !job.error,
  );
  const [fetching, setFetching] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [progressError, setProgressError] = useState<string | null>(null);
  const [position, setPosition] = useState(0);
  const [seek, setSeek] = useState<SeekTarget>({ time: 0, token: 0 });
  const [focusedId, setFocusedId] = useState<string | null>(null);
  const [editorId, setEditorId] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<'analyze' | 'delete' | null>(null);
  const [reload, setReload] = useState(0);
  const [eventSnapshot, setEventSnapshot] = useState<EventSnapshot | null>(null);
  const [eventError, setEventError] = useState<string | null>(null);
  const previewRef = useRef<HTMLDivElement>(null);
  const navigate = useNavigate();
  const { run, pending } = useAction();
  useEffect(() => {
    if (tab === 'layout') setLayoutVisited(true);
    else layoutRef.current?.querySelectorAll('video').forEach((video) => video.pause());
  }, [tab]);
  useEffect(() => {
    const abort = new AbortController();
    let disposed = false;
    setFetching(true);
    setLoadError(null);
    setProgressError(null);
    setFocusedId(null);
    setPosition(0);
    setSeek({ time: 0, token: 0 });
    api
      .project(projectId, abort.signal)
      .then((p) => {
        if (!disposed) useStudio.getState().upsertProject(p);
      })
      .catch((err) => {
        if (!disposed) setLoadError(errorMessage(err));
      })
      .finally(() => {
        if (!disposed) setFetching(false);
      });
    api
      .projectProgress(projectId)
      .then((job) => {
        if (!disposed && job) useStudio.getState().upsertJob(job);
      })
      .catch((err) => {
        if (!disposed) setProgressError(errorMessage(err));
      });
    return () => {
      disposed = true;
      abort.abort();
    };
  }, [projectId, reload]);
  useEffect(() => {
    let disposed = false;
    setEventSnapshot(null);
    setEventError(null);
    api
      .projectEvents(projectId)
      .then((snapshot) => {
        if (!disposed) setEventSnapshot(snapshot);
      })
      .catch((error) => {
        if (!disposed) setEventError(errorMessage(error));
      });
    return () => {
      disposed = true;
    };
  }, [projectId, project?.status, reload]);
  const analyze = (force = false) =>
    run(async () => {
      const job = await api.analyze(projectId, force);
      useStudio.getState().upsertJob(job);
      void useStudio.getState().refresh(['projects', 'jobs']);
      setConfirm(null);
    }, 'Analysis queued');
  const remove = () =>
    run(async () => {
      await api.deleteProject(projectId);
      await useStudio.getState().refresh(['projects', 'highlights', 'exports', 'jobs']);
      navigate('/projects');
    }, 'Project removed. The original source reference was not deleted.');
  const seekTo = (seconds: number) => {
    setSeek((previous) => ({ time: seconds, token: previous.token + 1 }));
    setPosition(seconds);
  };
  const preview = (highlight: Highlight) => {
    setFocusedId(highlight.id);
    seekTo(highlight.start);
    previewRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' });
  };
  if (!project)
    return (
      <>
        <Button variant="ghost" size="sm" className="mb-5" asChild>
          <Link to="/projects">
            <ArrowLeft />
            Projects
          </Link>
        </Button>
        {loadError ? (
          <ErrorBanner message={loadError} retry={() => setReload((n) => n + 1)} />
        ) : fetching ? (
          <div className="flex min-h-60 items-center justify-center gap-2 text-xs text-zinc-500">
            <Loader2 className="size-4 animate-spin" />
            Loading project…
          </div>
        ) : (
          <EmptyState
            icon={Film}
            title="Project not found"
            description="This project may have been removed."
          />
        )}
      </>
    );
  const canAnalyze =
    !['importing', 'analyzing'].includes(project.status) &&
    !activeJobs.length &&
    health?.capabilities.ffmpeg !== false &&
    health?.capabilities.ffprobe !== false;
  return (
    <>
      <div className="mb-5 flex items-center gap-2 text-[11px] text-zinc-600">
        <Link className="hover:text-zinc-300" to="/projects">
          Projects
        </Link>
        <ChevronRight className="size-3" />
        <span className="truncate text-zinc-400">{project.name}</span>
      </div>
      <div className="page-heading">
        <div className="min-w-0">
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <span className="eyebrow">Project workspace</span>
            <StatusBadge status={project.status} />
          </div>
          <h1 className="break-words text-base font-semibold tracking-tight">{project.name}</h1>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="ghost"
            size="icon"
            aria-label="Delete project"
            onClick={() => setConfirm('delete')}
          >
            <Trash2 className="!size-4" />
          </Button>
          <Button
            loading={pending}
            disabled={!canAnalyze}
            onClick={() => {
              if (highlights.length) setConfirm('analyze');
              else void analyze();
            }}
          >
            {highlights.length ? <RefreshCw /> : <ScanLine />}
            {highlights.length ? 'Re-analyze' : 'Find highlights'}
          </Button>
        </div>
      </div>
      <DependencyWarnings />
      {loadError && (
        <ErrorBanner className="mb-5" message={loadError} retry={() => setReload((n) => n + 1)} />
      )}
      {progressError && (
        <ErrorBanner
          className="mb-5"
          message={progressError}
          retry={() => setReload((n) => n + 1)}
        />
      )}
      {(activeJobs.length > 0 || failedJobs.length > 0) && (
        <section aria-label="Project jobs" className="mb-5 grid gap-3 lg:grid-cols-2">
          {[...activeJobs, ...failedJobs].map((job) => (
            <JobCard key={job.id} job={job} />
          ))}
        </section>
      )}
      <Tabs
        value={tab}
        onValueChange={(value) => {
          // Focus and click can both arrive before a router transition commits.
          // Only push one history entry for that tab change.
          if (requestedTab.current === value) return;
          requestedTab.current = value === 'layout' ? 'layout' : 'review';
          setSearchParams((current) => {
            const next = new URLSearchParams(current);
            if (value === 'layout') next.set('tab', 'layout');
            else next.delete('tab');
            return next;
          });
        }}
      >
        <TabsList aria-label="Project view">
          <TabsTrigger value="review">Review</TabsTrigger>
          <TabsTrigger value="layout">Layout</TabsTrigger>
        </TabsList>
        <TabsContent value="review">
          <Card className="overflow-hidden" ref={previewRef}>
            <CardHeader className="px-4 py-3">
              <CardTitle className="flex items-center gap-2 text-xs">
                <Play className="size-3.5 text-zinc-500" />
                Source preview
              </CardTitle>
              {project.metadata.video_codec && <Badge>{project.metadata.video_codec}</Badge>}
            </CardHeader>
            {project.status === 'importing' ? (
              <div className="flex aspect-video items-center justify-center gap-3 bg-black/30">
                <Loader2 className="size-6 animate-spin text-primary/60" />
                <p className="text-xs text-zinc-400">Source available after import finishes.</p>
              </div>
            ) : (
              <MediaPlayer
                key={project.id}
                projectId={project.id}
                seek={seek}
                onTimeChange={setPosition}
              />
            )}
            <EventTimeline
              duration={project.metadata.duration ?? 0}
              position={position}
              events={eventSnapshot?.events ?? []}
              highlights={[...highlights].sort((a, b) => a.start - b.start)}
              focusedId={focusedId}
              onSeek={seekTo}
              onSelect={preview}
            />
          </Card>
          {eventError && (
            <ErrorBanner
              className="mt-3"
              message={`Signal timeline: ${eventError}`}
              retry={() => setReload((n) => n + 1)}
            />
          )}
          {eventSnapshot?.complete && (
            <details className="mt-2 text-[11px] text-zinc-500">
              <summary className="cursor-pointer">About this scan</summary>
              <p className="mt-2 leading-relaxed">{eventSnapshot.detail}</p>
            </details>
          )}
          {focusedId && (
            <div className="mt-3 flex items-center justify-between gap-2 rounded-md border border-primary/15 bg-primary/[0.03] px-4 py-2.5">
              <p className="truncate text-[11px] text-primary/80">
                {highlights.find((h) => h.id === focusedId)?.title}
              </p>
              <div className="flex shrink-0 items-center gap-2">
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-7 text-[11px]"
                  onClick={() => setEditorId(focusedId)}
                >
                  <Captions className="!size-3" />
                  Edit subtitles
                </Button>
                <button aria-label="Dismiss focused highlight" onClick={() => setFocusedId(null)}>
                  <X className="size-3 text-zinc-500" />
                </button>
              </div>
            </div>
          )}
          <section className="mt-8">
            <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
              <h2 className="flex items-center gap-2 text-sm font-semibold">
                <Sparkles className="size-4 text-primary" />
                Highlights<Badge>{highlights.length}</Badge>
              </h2>
              <Select
                aria-label="Sort project highlights"
                className="w-48"
                value={sort}
                onValueChange={setSort}
                options={[
                  { value: 'score', label: 'Detection score' },
                  { value: 'ai', label: 'AI review score' },
                ]}
              />
            </div>
            <ResourceState resource="highlights">
              {highlights.length ? (
                <>
                  <details className="mb-5 rounded-lg border border-border bg-card">
                    <summary className="cursor-pointer px-4 py-3 text-xs font-medium">
                      Tools{' '}
                      <span className="ml-2 font-normal text-zinc-500">Subtitles & AI review</span>
                    </summary>
                    <ProjectEnhancements projectId={project.id} highlights={highlights} />
                  </details>
                  <ExportControls highlights={highlights} />
                  <div className="highlight-grid">
                    {highlights.map((highlight) => (
                      <HighlightCard
                        key={highlight.id}
                        highlight={highlight}
                        onEdit={(h) => setEditorId(h.id)}
                        onPreview={preview}
                      />
                    ))}
                  </div>
                </>
              ) : (
                <Card>
                  <EmptyState
                    icon={ScanLine}
                    title={
                      project.status === 'analyzing'
                        ? 'Finding highlights…'
                        : project.status === 'analyzed'
                          ? 'No highlights found'
                          : 'No highlights yet'
                    }
                    description={
                      project.status === 'analyzing'
                        ? 'Highlights appear when analysis finishes.'
                        : project.status === 'analyzed'
                          ? 'Check the source or adjust analysis settings before trying again.'
                          : 'Run local analysis to find candidates. Detection categories are estimates, not verified events.'
                    }
                  >
                    {canAnalyze && (
                      <Button
                        size="sm"
                        loading={pending}
                        onClick={() => {
                          void analyze(project.status === 'analyzed');
                        }}
                      >
                        <ScanLine />
                        {project.status === 'analyzed' ? 'Analyze again' : 'Find highlights'}
                      </Button>
                    )}
                  </EmptyState>
                </Card>
              )}
            </ResourceState>
          </section>
        </TabsContent>
        {/* Keep layout drafts mounted after the first visit so tab changes do not discard edits. */}
        {(tab === 'layout' || layoutVisited) && (
          <TabsContent
            value="layout"
            forceMount
            hidden={tab !== 'layout'}
            className={tab !== 'layout' ? 'hidden' : undefined}
            ref={layoutRef}
          >
            {project.status === 'importing' ? (
              <Card className="flex min-h-60 items-center justify-center gap-3 p-5">
                <Loader2 className="size-5 animate-spin text-primary/60" />
                <p className="text-xs text-zinc-400">Layout available after import finishes.</p>
              </Card>
            ) : (
              <LayoutEditor
                key={project.id}
                project={project}
                seek={seek}
                onTimeChange={setPosition}
              />
            )}
          </TabsContent>
        )}
      </Tabs>
      <details className="mt-6 rounded-lg border border-border bg-card">
        <summary className="cursor-pointer px-4 py-3 text-xs font-medium">Project details</summary>
        <div className="space-y-4 border-t border-border p-4">
          <ProjectDetails project={project} />
          {settings && (
            <p className="text-[11px] leading-relaxed text-zinc-500">
              Analysis settings: up to {settings.max_clips} clips · {settings.clip_duration}s target
              · {settings.sampling_interval}s sampling · {settings.cpu_workers} FFmpeg thread
              {settings.cpu_workers > 1 ? 's' : ''}
            </p>
          )}
        </div>
      </details>
      {previousJobs.length > 0 && (
        <details className="mt-4 rounded-lg border border-border bg-card">
          <summary className="cursor-pointer px-4 py-3 text-xs font-medium">
            Previous jobs <Badge>{previousJobs.length}</Badge>
          </summary>
          <div className="grid gap-3 border-t border-border p-4 lg:grid-cols-2">
            {previousJobs.map((job) => (
              <JobCard key={job.id} job={job} />
            ))}
          </div>
        </details>
      )}
      <HighlightEditor
        highlight={allHighlights.find((h) => h.id === editorId) ?? null}
        onClose={() => setEditorId(null)}
      />
      <Dialog
        open={!!confirm}
        onOpenChange={(open) => {
          if (!open && !pending) setConfirm(null);
        }}
      >
        <DialogContent hideClose={pending}>
          <DialogTitle>
            {confirm === 'delete' ? 'Remove this project?' : 'Run analysis again?'}
          </DialogTitle>
          <DialogDescription>
            {confirm === 'delete'
              ? 'This removes the project from the studio. A referenced original source file is never deleted. This action cannot be undone.'
              : 'A new pass can replace the current highlight candidates, including selections and edited transcripts. Export or save any text you want to keep first.'}
          </DialogDescription>
          {confirm === 'delete' && activeJobs.length > 0 && (
            <div className="mt-4">
              <p className="mb-3 text-xs text-amber-200">
                Cancel this project’s queued or running jobs before deleting it.
              </p>
              <div className="space-y-2">
                {activeJobs.map((job) => (
                  <JobCard key={job.id} job={job} compact />
                ))}
              </div>
            </div>
          )}
          <div className="mt-6 flex justify-end gap-2">
            <Button variant="secondary" disabled={pending} onClick={() => setConfirm(null)}>
              Keep project
            </Button>
            <Button
              variant={confirm === 'delete' ? 'destructive' : 'default'}
              loading={pending}
              disabled={confirm === 'delete' && activeJobs.length > 0}
              onClick={() => {
                if (confirm === 'delete') void remove();
                else void analyze(true);
              }}
            >
              {confirm === 'delete' ? <Trash2 /> : <RefreshCw />}
              {confirm === 'delete' ? 'Remove project' : 'Re-analyze footage'}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}
