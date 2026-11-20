import {
  ArrowRight,
  Captions,
  Clock3,
  Cpu,
  Download,
  Film,
  FolderOpen,
  Gauge,
  HardDrive,
  MonitorPlay,
  ScanLine,
  ShieldCheck,
  Sparkles,
} from '../icons';
import { Link } from 'react-router-dom';
import { useStudio } from '../stores/studio';

import { isActive, time, bytes } from '../lib/utils';
import { Badge, Button, Card, CardHeader, CardTitle } from '../components/ui';
import {
  DependencyWarnings,
  EmptyState,
  ErrorBanner,
  ImportButton,
  PageHeading,
  ResourceState,
  SectionLink,
} from '../components/shared';
import { ProjectCard } from '../components/ProjectCard';
import { JobCard } from '../components/JobCard';

export function Dashboard() {
  const { projects, highlights, exports, jobs, health } = useStudio((s) => s.data);
  const loaded = useStudio((s) => s.loaded);
  const healthError = useStudio((s) => s.errors.health);
  const activeJobs = jobs.filter(isActive);
  const recent = [...projects].sort((a, b) => b.created_at.localeCompare(a.created_at)).slice(0, 3);
  const totalDuration = projects.reduce((sum, p) => sum + (p.metadata.duration ?? 0), 0);
  const finishedExports = exports.filter((e) => e.status === 'completed');
  return (
    <>
      <PageHeading title="Dashboard" description="Recent projects, exports, and processing jobs.">
        <Button variant="secondary" asChild>
          <Link to="/exports">
            <Download />
            View exports
          </Link>
        </Button>
        <ImportButton />
      </PageHeading>
      <DependencyWarnings />
      {healthError && (
        <ErrorBanner
          className="mb-5"
          message={healthError}
          retry={() => {
            void useStudio.getState().refresh(['health']);
          }}
        />
      )}
      <section
        aria-label="Workspace summary"
        className="mb-6 overflow-hidden rounded-lg border border-border bg-card"
      >
        <div className="grid grid-cols-3">
          {[
            {
              label: 'Source footage',
              value: loaded.projects ? time(totalDuration) : '—',
              detail: `${projects.length} recordings`,
              icon: Film,
            },
            {
              label: 'Highlights found',
              value: loaded.highlights ? highlights.length : '—',
              detail: 'Ranked by detection score',
              icon: Sparkles,
            },
            {
              label: 'Clips exported',
              value: loaded.exports ? finishedExports.length : '—',
              detail: 'Completed renders',
              icon: Download,
            },
          ].map(({ label, value, detail, icon: Icon }) => (
            <div
              key={label}
              className="border-r border-white/[0.06] px-4 py-4 last:border-r-0 sm:px-7"
            >
              <p className="mb-2 flex items-center gap-2 text-[11px] text-zinc-500">
                <Icon className="hidden size-3 sm:block" />
                {label}
              </p>
              <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                <span className="font-mono text-base font-medium">{value}</span>
                <span className="hidden text-[11px] text-zinc-600 xl:inline">{detail}</span>
              </div>
            </div>
          ))}
        </div>
      </section>
      <div className="grid items-start gap-6 xl:grid-cols-[minmax(0,1fr)_300px]">
        <div className="min-w-0">
          <div className="mb-4 flex items-center justify-between">
            <h2 className="flex items-center gap-2 text-sm font-semibold">
              <FolderOpen className="size-4 text-zinc-500" />
              Recent projects
            </h2>
            <SectionLink to="/projects">All projects</SectionLink>
          </div>
          <ResourceState resource="projects">
            {recent.length ? (
              <div className="grid gap-4 sm:grid-cols-2 2xl:grid-cols-3">
                {recent.map((p) => (
                  <ProjectCard key={p.id} project={p} />
                ))}
              </div>
            ) : (
              <Card className="border-dashed">
                <EmptyState
                  icon={MonitorPlay}
                  title="No projects yet"
                  description="Import a local recording or a public YouTube video."
                >
                  <ImportButton size="sm" />
                </EmptyState>
              </Card>
            )}
          </ResourceState>
          <details className="mt-6 rounded-lg border border-border bg-card">
            <summary className="cursor-pointer px-5 py-4 text-xs font-medium">
              How to create a clip
            </summary>
            <div className="grid divide-y divide-border md:grid-cols-3 md:divide-x md:divide-y-0">
              {[
                {
                  n: '01',
                  icon: FolderOpen,
                  title: 'Import a recording',
                  description: 'Reference a local file, upload a video, or import a public URL.',
                },
                {
                  n: '02',
                  icon: ScanLine,
                  title: 'Find the highlights',
                  description: 'Analyze audio, motion, and scene changes, then review the results.',
                },
                {
                  n: '03',
                  icon: Captions,
                  title: 'Edit and export',
                  description: 'Set the layout, edit subtitles, and export in 9:16.',
                },
              ].map(({ n, icon: Icon, title, description }) => (
                <div className="p-5" key={n}>
                  <div className="mb-3 flex items-center justify-between">
                    <Icon className="size-4 text-zinc-400" />
                    <span className="font-mono text-[11px] text-zinc-700">{n}</span>
                  </div>
                  <h3 className="text-[11px] font-medium">{title}</h3>
                  <p className="mt-2 text-[11px] leading-relaxed text-zinc-500">{description}</p>
                </div>
              ))}
            </div>
          </details>
        </div>
        <div className="space-y-5">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Clock3 className="size-3.5 text-zinc-500" />
                Processing queue
              </CardTitle>
              <Badge variant={activeJobs.length ? 'amber' : 'neutral'}>
                {loaded.jobs ? activeJobs.length : '—'}
              </Badge>
            </CardHeader>
            <div className="p-3">
              <ResourceState resource="jobs">
                {activeJobs.length ? (
                  <div className="space-y-3">
                    {activeJobs.slice(0, 3).map((job) => (
                      <JobCard key={job.id} job={job} compact />
                    ))}
                    {activeJobs.length > 3 && (
                      <Link
                        to="/exports"
                        className="block py-2 text-center text-[11px] text-zinc-500"
                      >
                        +{activeJobs.length - 3} more queued jobs
                      </Link>
                    )}
                  </div>
                ) : (
                  <EmptyState
                    compact
                    icon={ShieldCheck}
                    title="No active jobs"
                    description="Jobs appear here while queued or running."
                  />
                )}
              </ResourceState>
            </div>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Cpu className="size-3.5 text-zinc-500" />
                Workstation
              </CardTitle>
              <SectionLink to="/settings">Details</SectionLink>
            </CardHeader>
            <div className="space-y-4 p-5">
              <SystemRow
                icon={Cpu}
                name="Physical / logical cores"
                value={
                  health
                    ? `${health.capabilities.physical_cores} / ${health.capabilities.logical_cores}`
                    : '—'
                }
              />
              <SystemRow
                icon={HardDrive}
                name="System memory"
                value={health ? `${health.capabilities.memory_gb.toFixed(1)} GB` : '—'}
              />
              <SystemRow
                icon={FolderOpen}
                name="Storage free"
                value={health ? bytes(health.storage.free_bytes) : '—'}
              />
              <SystemRow
                icon={Gauge}
                name="Median analysis speed"
                value={
                  health?.benchmark?.median_speed_ratio != null
                    ? `${health.benchmark.median_speed_ratio.toFixed(2)}×`
                    : health
                      ? 'No runs yet'
                      : 'Not available'
                }
              />
              <p className="border-t border-border pt-3 text-[11px] leading-relaxed text-zinc-600">
                {health?.benchmark?.runs
                  ? `Measured from ${health.benchmark.runs} stored analysis runs. Speed is source seconds per elapsed second.`
                  : 'Performance figures appear after analysis finishes.'}
              </p>
            </div>
          </Card>
          <div className="flex gap-3 px-2 text-[11px] leading-relaxed text-zinc-600">
            <ShieldCheck className="mt-0.5 size-4 shrink-0" />
            <p>
              Local subtitles use the cached model prepared by the launcher. Choose No captions to
              skip transcription. Cloud subtitles and AI review require consent; failures leave
              local detection results unchanged.
            </p>
          </div>
          <Link
            to="/highlights"
            className="flex items-center justify-between px-2 text-[11px] text-zinc-500 hover:text-primary"
          >
            All highlights
            <ArrowRight className="size-3.5" />
          </Link>
        </div>
      </div>
    </>
  );
}
function SystemRow({ icon: Icon, name, value }: { icon: typeof Cpu; name: string; value: string }) {
  return (
    <div className="flex items-center gap-2">
      <Icon className="size-3 text-zinc-600" />
      <span className="flex-1 text-[11px] text-zinc-500">{name}</span>
      <span className="font-mono text-[11px] text-zinc-300">{value}</span>
    </div>
  );
}
