import { useEffect, useState } from 'react';
import {
  AlertTriangle,
  Check,
  CheckCircle2,
  ChevronDown,
  Cpu,
  Database,
  FolderOpen,
  Gauge,
  HardDrive,
  Info,
  RefreshCw,
  Save,
  Settings2,
  ShieldCheck,
  Trash2,
  XCircle,
} from '../icons';
import type { AppIcon } from '../icons';
import type { Settings as StudioSettings } from '../types/api';
import { api } from '../services/api';
import { bytes, cn, label } from '../lib/utils';
import { useStudio } from '../stores/studio';
import { useUI } from '../stores/ui';
import { useAction } from '../hooks/useAction';
import {
  Badge,
  Button,
  Card,
  CardHeader,
  CardTitle,
  Checkbox,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
  Input,
  Label,
  Select,
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from '../components/ui';
import { EmptyState, PageHeading, ResourceState } from '../components/shared';
import { captionOptions } from '../components/ExportControls';
import { cloudPrivacy, IntegrationSettings } from '../components/IntegrationSettings';
import { ChatGPTModelPicker } from '../components/ChatGPTModelPicker';
import { APIModelPicker } from '../components/APIModelPicker';
import { UsagePanel } from '../components/UsagePanel';

export function Settings() {
  const settings = useStudio((s) => s.data.settings);
  const [tab, setTab] = useState('analysis');
  const { pending, run } = useAction();
  useEffect(() => {
    void useStudio.getState().refresh(['settings', 'health', 'benchmarks']);
  }, []);
  return (
    <>
      <PageHeading
        title="Settings"
        description="Processing defaults, providers, and local storage."
      >
        <Button
          variant="secondary"
          loading={pending}
          onClick={() => {
            void run(() => useStudio.getState().refresh(['health', 'benchmarks']));
          }}
        >
          <RefreshCw />
          Refresh diagnostics
        </Button>
      </PageHeading>
      <Tabs value={tab} onValueChange={setTab}>
        <TabsList
          aria-label="Settings sections"
          className="mb-5 flex h-auto flex-wrap justify-start gap-1"
        >
          <TabsTrigger value="analysis">Analysis</TabsTrigger>
          <TabsTrigger value="export">Export & subtitles</TabsTrigger>
          <TabsTrigger value="ai">AI review</TabsTrigger>
          <TabsTrigger value="connections">Connections & usage</TabsTrigger>
          <TabsTrigger value="system">System</TabsTrigger>
        </TabsList>
        <ResourceState resource="settings">
          {settings && <SettingsForm persisted={settings} tab={tab} />}
        </ResourceState>
        <TabsContent value="connections">
          <IntegrationSettings />
          <UsagePanel />
        </TabsContent>
        <TabsContent value="system">
          <div className="grid items-start gap-5 xl:grid-cols-2">
            <SystemHealth />
            <Maintenance />
          </div>
          <Benchmarks />
        </TabsContent>
      </Tabs>
    </>
  );
}
function SettingsForm({ persisted, tab }: { persisted: StudioSettings; tab: string }) {
  const [draft, setDraft] = useState<StudioSettings>(() => structuredClone(persisted));
  const { pending, run } = useAction();
  const dirty = JSON.stringify(draft) !== JSON.stringify(persisted);
  useEffect(() => {
    if (!dirty) return;
    const handler = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = '';
    };
    window.addEventListener('beforeunload', handler);
    return () => window.removeEventListener('beforeunload', handler);
  }, [dirty]);
  const update = <K extends keyof StudioSettings>(key: K, value: StudioSettings[K]) =>
    setDraft((s) => ({
      ...s,
      [key]: value,
      // Provider model IDs are not interchangeable. Start with its priced default.
      ...(key === 'ai_scoring_provider' && value !== s.ai_scoring_provider
        ? { ai_scoring_model: '' }
        : {}),
    }));
  const number = (
    key:
      | 'max_clips'
      | 'clip_duration'
      | 'analysis_chunk_seconds'
      | 'ai_scoring_max_candidates'
      | 'ai_scoring_frames'
      | 'ai_daily_request_limit'
      | 'ai_daily_budget_usd'
      | 'ai_max_output_tokens',
    title: string,
    description: string,
    min: number,
    max: number,
    step = 1,
  ) => (
    <SettingRow title={title} description={description} htmlFor={`setting-${key}`}>
      <Input
        id={`setting-${key}`}
        className="w-full font-mono sm:w-40"
        type="number"
        min={min}
        max={max}
        step={step}
        required
        value={Number.isNaN(draft[key]) ? '' : String(draft[key])}
        onChange={(e) => update(key, e.target.valueAsNumber)}
        disabled={pending}
      />
    </SettingRow>
  );
  const choose = <K extends keyof StudioSettings>(
    key: K,
    title: string,
    description: string,
    options: { value: string; label: string }[],
    numeric = false,
  ) => (
    <SettingRow title={title} description={description} htmlFor={`setting-${key}`}>
      <Select
        id={`setting-${key}`}
        className="w-full sm:w-48"
        value={String(draft[key])}
        onValueChange={(value) => {
          if (key === 'ai_scoring_provider')
            setDraft((old) => ({
              ...old,
              ai_scoring_provider: value as StudioSettings['ai_scoring_provider'],
              ai_scoring_model: '',
              ...(value === 'groq' ? { ai_review_mode: 'text' as const } : {}),
            }));
          else if (key === 'openai_auth_mode')
            setDraft((old) => ({
              ...old,
              openai_auth_mode: value as StudioSettings['openai_auth_mode'],
              ai_scoring_model: '',
            }));
          else update(key, (numeric ? Number(value) : value) as StudioSettings[K]);
        }}
        options={options}
        disabled={pending}
      />
    </SettingRow>
  );
  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    void run(async () => {
      const settings = await api.saveSettings(draft);
      useStudio.getState().setSettings(settings);
      setDraft(structuredClone(settings));
    }, 'Processing preferences saved');
  };
  return (
    <form onSubmit={submit}>
      <TabsContent value="analysis" className="grid items-start gap-5 xl:grid-cols-2">
        <div className="space-y-5">
          <SettingSection
            icon={Gauge}
            title="Analysis"
            subtitle="Analyzes sampled frames, not every frame."
          >
            {number(
              'max_clips',
              'Maximum highlights',
              'Candidate clips per analysis pass. Between 1 and 50.',
              1,
              50,
            )}
            {number(
              'clip_duration',
              'Target clip length',
              'Target duration in seconds, from 10 to 90.',
              10,
              90,
            )}
            {choose(
              'sampling_interval',
              'Sampling interval',
              'Seconds between sampled frames. A larger interval reduces work.',
              [1, 2, 5, 10].map((v) => ({
                value: String(v),
                label: `${v} second${v > 1 ? 's' : ''}`,
              })),
              true,
            )}
            {choose(
              'analysis_width',
              'Analysis width',
              'Sampled frame width; does not change the original recording.',
              [160, 320, 480].map((v) => ({ value: String(v), label: `${v} pixels` })),
              true,
            )}
            {number(
              'analysis_chunk_seconds',
              'Recovery chunk size',
              'Cache chunk length in seconds (60–600). Completed chunks support recovery.',
              60,
              600,
            )}
            <SettingRow
              title="Local refinement (legacy)"
              description="Local-only compatibility option, separate from editorial cloud review. Uses the cached model; no implicit downloads."
              htmlFor="setting-ai"
            >
              <Checkbox
                id="setting-ai"
                checked={draft.ai_refinement}
                disabled={pending}
                onCheckedChange={(checked) => update('ai_refinement', checked === true)}
              />
            </SettingRow>
          </SettingSection>
        </div>
        <div className="space-y-5">
          <SettingSection
            icon={Cpu}
            title="Resource budget"
            subtitle="Analysis and export run one job at a time."
          >
            {choose(
              'cpu_workers',
              'FFmpeg thread limit',
              'Not the number of simultaneous jobs. Analysis and export share one processing slot.',
              [
                { value: '1', label: '1 thread' },
                { value: '2', label: '2 threads' },
              ],
              true,
            )}
            {choose(
              'memory_mode',
              'Memory mode',
              'Low memory caps feature chunks at 300s; balanced permits 600s. The 1.5GB target is not an OS memory cap.',
              [
                { value: 'low', label: 'Low memory' },
                { value: 'balanced', label: 'Balanced' },
              ],
            )}
            <SettingRow
              title="Audio sample rate"
              description="Fixed by the analysis pipeline."
              htmlFor="audio-sample-rate"
            >
              <Input
                id="audio-sample-rate"
                value={`${draft.audio_sample_rate.toLocaleString()} Hz`}
                readOnly
                className="w-full font-mono text-zinc-500 sm:w-48"
              />
            </SettingRow>
          </SettingSection>
        </div>
      </TabsContent>
      <TabsContent value="export">
        <SettingSection
          icon={FolderOpen}
          title="Export defaults"
          subtitle="Applies to new exports, not queued jobs."
        >
          {choose(
            'output_resolution',
            'Vertical resolution',
            'Portrait output for short-form video.',
            [
              { value: '720x1280', label: '720 × 1280 · 9:16' },
              { value: '1080x1920', label: '1080 × 1920 · 9:16' },
            ],
          )}
          {choose(
            'caption_style',
            'Subtitle style',
            'Subtitles generate after detection and on export. No captions disables transcription and burned-in text.',
            captionOptions,
          )}
          {choose(
            'caption_provider',
            'Subtitle provider',
            'Local uses the cached CPU model prepared by the launcher. Groq uploads clip audio and requires saved consent and a configured Groq key.',
            [
              { value: 'local', label: 'Local · offline model' },
              { value: 'groq', label: 'Groq · cloud audio' },
            ],
          )}
          {choose(
            'default_layout',
            'New project layout',
            'Applies to new imports only. Split starts 50/50 and requires selecting the real embedded facecam. Legacy projects keep blurred layout.',
            [
              { value: 'split', label: 'Split · facecam / gameplay' },
              { value: 'blurred', label: 'Blurred background' },
            ],
          )}
          <div className="px-5 py-4">
            <Label htmlFor="output-folder">Output folder on the server</Label>
            <Input
              id="output-folder"
              value={draft.output_folder}
              onChange={(e) => update('output_folder', e.target.value)}
              disabled={pending}
              placeholder="Use default: storage/exports"
              className="font-mono text-[11px]"
            />
            <p className="mt-2 text-[11px] leading-relaxed text-zinc-500">
              Leave empty to use storage/exports. A custom folder must be writable by the backend.
              This is a server path, not your browser’s download folder.
            </p>
          </div>
        </SettingSection>
      </TabsContent>
      <TabsContent value="ai">
        <SettingSection
          icon={ShieldCheck}
          title="AI review"
          subtitle="Manual by default. AI scores stay separate from local detection; title suggestions never replace a title automatically."
        >
          {choose(
            'ai_scoring_provider',
            'Review provider',
            'None keeps scoring local. Gemini and OpenAI support text or sparse-frame review; Groq is text-only.',
            [
              { value: 'none', label: 'None · local signals only' },
              { value: 'openai', label: 'OpenAI' },
              { value: 'gemini', label: 'Gemini' },
              { value: 'groq', label: 'Groq · transcript only' },
            ],
          )}
          {draft.ai_scoring_provider === 'openai' &&
            choose(
              'openai_auth_mode',
              'OpenAI connection mode',
              'API keys use separate API billing. ChatGPT plan uses the connected eligible account and its limits. No hidden fallback between modes.',
              [
                { value: 'api_key', label: 'OpenAI API key' },
                { value: 'chatgpt', label: 'ChatGPT plan · preview' },
              ],
            )}
          <SettingRow
            title="Review model"
            description="Empty uses the provider default: OpenAI gpt-6-luna, Gemini gemini-3.5-flash-lite, or Groq openai/gpt-oss-20b. No automatic expensive-model fallback."
            htmlFor="review-model"
          >
            {draft.ai_scoring_provider === 'openai' && draft.openai_auth_mode === 'chatgpt' ? (
              <ChatGPTModelPicker
                value={draft.ai_scoring_model}
                disabled={pending}
                onChange={(value) => update('ai_scoring_model', value)}
              />
            ) : (
              <APIModelPicker
                provider={draft.ai_scoring_provider}
                value={draft.ai_scoring_model}
                disabled={pending}
                onChange={(value) => update('ai_scoring_model', value)}
              />
            )}
          </SettingRow>
          {choose(
            'ai_review_mode',
            'Review evidence',
            'Text sends transcript and signals only. Vision also uploads a few video frames. Title suggestions use text only.',
            draft.ai_scoring_provider === 'groq'
              ? [{ value: 'text', label: 'Text only' }]
              : [
                  { value: 'text', label: 'Text only · default' },
                  { value: 'vision', label: 'Vision · sparse frames + text' },
                ],
          )}
          <SettingRow
            title="Automatic AI review"
            description="Off by default. When enabled, review runs after analysis only with a provider and saved consent. Local subtitles still generate independently."
            htmlFor="auto-ai-review"
          >
            <Checkbox
              id="auto-ai-review"
              checked={draft.auto_ai_review}
              disabled={pending}
              onCheckedChange={(value) => update('auto_ai_review', value === true)}
            />
          </SettingRow>
          {number(
            'ai_daily_request_limit',
            'Daily cloud request limit',
            'UTC day, shared across providers and operations. Cache hits do not consume requests. Default 20; range 1–500.',
            1,
            500,
          )}
          {number(
            'ai_daily_budget_usd',
            'Daily API budget (USD)',
            'Default $0.10. Zero blocks API requests; it does not mean unlimited. Conservative estimates and reservations guard requests, not provider invoices. ChatGPT plan uses the request cap and provider plan limits, not this USD ceiling.',
            0,
            100,
            0.01,
          )}
          {draft.ai_daily_budget_usd === 0 && (
            <p className="px-5 py-3 text-[11px] text-amber-200">
              API requests are disabled by the zero-dollar budget. ChatGPT plan requests remain
              subject to the daily request cap and provider limits.
            </p>
          )}
          <details className="border-t border-border">
            <summary className="cursor-pointer px-5 py-3 text-xs text-zinc-400">
              Advanced AI limits
            </summary>
            {number(
              'ai_scoring_max_candidates',
              'Maximum review candidates',
              'Top local-detection candidates per review, 1–20. More candidates use more quota.',
              1,
              20,
            )}
            {number(
              'ai_scoring_frames',
              'Frames per candidate',
              'Vision only: 1–8 sparse frames, not the whole video. Gemini vision needs at least two usable frames.',
              1,
              8,
            )}
            {number(
              'ai_max_output_tokens',
              'Maximum API output tokens',
              'Default 512; range 128–2048. Applies to API requests only. ChatGPT preview does not support this field; bounded requests and provider plan limits apply instead.',
              128,
              2048,
            )}
            <SettingRow
              title="Allow unpriced API models"
              description="Off by default. Explicitly allows custom models without known prices. Request caps still apply, but a USD ceiling cannot be guaranteed."
              htmlFor="allow-unpriced"
            >
              <Checkbox
                id="allow-unpriced"
                checked={draft.ai_allow_unpriced_models}
                disabled={pending}
                onCheckedChange={(value) => update('ai_allow_unpriced_models', value === true)}
              />
            </SettingRow>
            {draft.ai_allow_unpriced_models && (
              <p className="px-5 py-3 text-[11px] text-amber-200">
                Unpriced model costs are unknown. The app cannot enforce a dollar ceiling for them.
              </p>
            )}
          </details>
          <SettingRow
            title="Allow cloud processing"
            description={cloudPrivacy}
            htmlFor="cloud-consent"
          >
            <Checkbox
              id="cloud-consent"
              checked={draft.cloud_consent}
              disabled={pending}
              onCheckedChange={(value) => update('cloud_consent', value === true)}
            />
          </SettingRow>
          {!draft.cloud_consent &&
            (draft.caption_provider === 'groq' || draft.ai_scoring_provider !== 'none') && (
              <p className="px-5 py-3 text-[11px] text-amber-200">
                Cloud jobs cannot run without saved consent and a configured API key or eligible
                ChatGPT plan account. Local highlight detection still works.
              </p>
            )}
        </SettingSection>
      </TabsContent>
      {(dirty || ['analysis', 'export', 'ai'].includes(tab)) && (
        <div className="settings-savebar">
          <p className="flex items-center gap-2 text-[11px] text-zinc-500">
            {dirty ? (
              <>
                <span className="size-1.5 rounded-full bg-amber-200" />
                <span className="text-amber-200/80">Unsaved changes</span>
              </>
            ) : (
              <>
                <Check className="size-3.5 text-primary" />
                All preferences saved on server
              </>
            )}
          </p>
          <div className="flex flex-wrap gap-2">
            <Button
              type="button"
              variant="ghost"
              disabled={!dirty || pending}
              onClick={() => setDraft(structuredClone(persisted))}
            >
              Discard changes
            </Button>
            <Button type="submit" loading={pending} disabled={!dirty}>
              <Save />
              Save preferences
            </Button>
          </div>
        </div>
      )}
    </form>
  );
}
function SettingSection({
  icon: Icon,
  title,
  subtitle,
  children,
}: {
  icon: AppIcon;
  title: string;
  subtitle: string;
  children: React.ReactNode;
}) {
  return (
    <Card className="overflow-hidden">
      <div className="border-b border-border px-5 py-4">
        <h2 className="flex items-center gap-2 text-sm font-semibold">
          <Icon className="size-4 text-primary/80" />
          {title}
        </h2>
        <p className="mt-2 text-[11px] leading-relaxed text-zinc-500">{subtitle}</p>
      </div>
      <div className="divide-y divide-border">{children}</div>
    </Card>
  );
}
function SettingRow({
  title,
  description,
  htmlFor,
  children,
}: {
  title: string;
  description: string;
  htmlFor: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col justify-between gap-3 px-5 py-4 sm:flex-row sm:items-center">
      <div className="min-w-0 flex-1 sm:pr-4">
        <Label htmlFor={htmlFor} className="mb-1 text-[11px]">
          {title}
        </Label>
        <p className="max-w-sm text-[11px] leading-relaxed text-zinc-500">{description}</p>
      </div>
      <div className="shrink-0">{children}</div>
    </div>
  );
}
function SystemHealth() {
  const health = useStudio((s) => s.data.health);
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <ShieldCheck className="size-4 text-zinc-500" />
          System & dependencies
        </CardTitle>
        {health && (
          <Badge variant={health.status === 'ok' || health.status === 'healthy' ? 'lime' : 'amber'}>
            {health.status}
          </Badge>
        )}
      </CardHeader>
      <div className="p-5">
        <ResourceState resource="health">
          {health && (
            <>
              <div className="space-y-4">
                {[
                  {
                    name: 'FFmpeg',
                    installed: health.capabilities.ffmpeg,
                    detail: 'Required for processing and vertical exports.',
                    optional: false,
                  },
                  {
                    name: 'FFprobe',
                    installed: health.capabilities.ffprobe,
                    detail: 'Required to inspect source metadata.',
                    optional: false,
                  },
                  {
                    name: 'yt-dlp',
                    installed: health.capabilities.yt_dlp,
                    detail: 'Required only for public YouTube imports.',
                    optional: true,
                  },
                  {
                    name: 'faster-whisper',
                    installed: health.capabilities.faster_whisper,
                    detail:
                      'Local subtitle engine. Launcher setup installs it and caches the model; jobs never download models. Detection works without it.',
                    optional: true,
                  },
                  {
                    name: 'Local caption model',
                    installed: health.capabilities.caption_model_cached,
                    detail:
                      'Cached tiny.en model for offline subtitles. If missing, rerun launcher setup, opt into Groq, or choose No captions.',
                    optional: true,
                  },
                ].map((dependency) => (
                  <div key={dependency.name} className="flex gap-3">
                    {dependency.installed ? (
                      <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-primary/70" />
                    ) : dependency.optional ? (
                      <Info className="mt-0.5 size-4 shrink-0 text-amber-200/60" />
                    ) : (
                      <XCircle className="mt-0.5 size-4 shrink-0 text-red-300/80" />
                    )}
                    <div className="min-w-0 flex-1">
                      <p className="flex flex-wrap justify-between gap-2 text-[11px] font-medium">
                        {dependency.name}
                        <span
                          className={cn(
                            'text-[11px] font-normal',
                            dependency.installed ? 'text-primary/60' : 'text-zinc-500',
                          )}
                        >
                          {dependency.installed
                            ? 'Detected'
                            : dependency.optional
                              ? 'Unavailable · see setup'
                              : 'Missing · required'}
                        </span>
                      </p>
                      <p className="mt-1 text-[11px] leading-relaxed text-zinc-500">
                        {dependency.detail}
                      </p>
                    </div>
                  </div>
                ))}
              </div>
              <div className="mt-5 grid grid-cols-3 gap-3 border-t border-border pt-4">
                {[
                  { label: 'Physical cores', value: health.capabilities.physical_cores },
                  { label: 'Logical cores', value: health.capabilities.logical_cores },
                  {
                    label: 'System memory',
                    value: `${health.capabilities.memory_gb.toFixed(1)} GB`,
                  },
                ].map((stat) => (
                  <div key={stat.label}>
                    <p className="text-[11px] text-zinc-600">{stat.label}</p>
                    <p className="mt-1 font-mono text-sm">{stat.value}</p>
                  </div>
                ))}
              </div>
            </>
          )}
        </ResourceState>
      </div>
    </Card>
  );
}
function Maintenance() {
  const health = useStudio((s) => s.data.health);
  const [confirm, setConfirm] = useState(false);
  const { run, pending } = useAction();
  const cleanup = () =>
    run(async () => {
      const result = await api.cleanup();
      useUI
        .getState()
        .toast(
          'Cache cleanup complete',
          `${result.removed_files} files removed · ${bytes(result.freed_bytes)} freed`,
        );
      setConfirm(false);
      void useStudio.getState().refresh(['health']);
    });
  return (
    <>
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <HardDrive className="size-4 text-zinc-500" />
            Storage & maintenance
          </CardTitle>
          <Badge>Local disk</Badge>
        </CardHeader>
        <div className="p-5">
          <div className="grid grid-cols-2 gap-4">
            <div>
              <p className="text-[11px] text-zinc-500">Storage used</p>
              <p className="mt-2 font-mono text-base">
                {health ? bytes(health.storage.used_bytes) : '—'}
              </p>
            </div>
            <div>
              <p className="text-[11px] text-zinc-500">Available space</p>
              <p className="mt-2 font-mono text-base">
                {health ? bytes(health.storage.free_bytes) : '—'}
              </p>
            </div>
          </div>
          <div className="mt-5 rounded-md border border-border bg-black/10 p-4">
            <h3 className="flex items-center gap-2 text-xs font-medium">
              <Database className="size-3.5 text-zinc-500" />
              Clean orphaned cache
            </h3>
            <p className="mb-4 mt-2 text-[11px] leading-relaxed text-zinc-500">
              Remove orphaned temporary files and caches for inactive projects only. Active project
              work is excluded by the backend.
            </p>
            <Button variant="secondary" size="sm" onClick={() => setConfirm(true)}>
              <Trash2 />
              Clean cache
            </Button>
          </div>
          <p className="mt-4 flex items-start gap-2 text-[11px] leading-relaxed text-zinc-600">
            <Settings2 className="mt-0.5 size-3 shrink-0" />
            Interrupted jobs requeue when the server restarts. Recovery uses completed detection
            cache chunks; stage timing resets for the current run.
          </p>
        </div>
      </Card>
      <Dialog
        open={confirm}
        onOpenChange={(open) => {
          if (!pending) setConfirm(open);
        }}
      >
        <DialogContent hideClose={pending}>
          <DialogTitle>Clean orphaned cache?</DialogTitle>
          <DialogDescription>
            The server will remove orphaned cache and temporary files only for inactive projects.
            This may mean future analysis needs to regenerate cached work.
          </DialogDescription>
          <div className="mt-6 flex justify-end gap-2">
            <Button variant="ghost" disabled={pending} onClick={() => setConfirm(false)}>
              Cancel
            </Button>
            <Button
              variant="secondary"
              loading={pending}
              onClick={() => {
                void cleanup();
              }}
            >
              <Trash2 />
              Clean cache
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}
function Benchmarks() {
  const benchmarks = useStudio((s) => s.data.benchmarks);
  const health = useStudio((s) => s.data.health);
  const columns = [...new Set(benchmarks.flatMap((run) => Object.keys(run)))];
  return (
    <Card className="mt-6 overflow-hidden">
      <CardHeader>
        <div>
          <CardTitle className="flex items-center gap-2">
            <Gauge className="size-4 text-zinc-500" />
            Measured performance
          </CardTitle>
          <p className="mt-1.5 text-[11px] text-zinc-500">
            Measurements from completed analysis runs.
          </p>
        </div>
        {health?.benchmark && (
          <Badge variant="lime">
            {health.benchmark.runs} run{health.benchmark.runs === 1 ? '' : 's'}
            {health.benchmark.median_speed_ratio != null
              ? ` · ${health.benchmark.median_speed_ratio.toFixed(2)}× median`
              : ''}
          </Badge>
        )}
      </CardHeader>
      <div className="p-4">
        <ResourceState resource="benchmarks">
          {benchmarks.length ? (
            <details open>
              <summary className="mb-4 flex cursor-pointer items-center gap-2 text-[11px] text-zinc-500">
                <ChevronDown className="size-3" />
                Run records
              </summary>
              <div className="max-h-80 overflow-auto rounded-md border border-border">
                <table className="w-full border-collapse text-left text-[11px]">
                  <thead className="sticky top-0 bg-[#212620]">
                    <tr>
                      {columns.map((column) => (
                        <th
                          className="whitespace-nowrap border-b border-border px-3 py-2.5 font-medium text-zinc-400"
                          key={column}
                        >
                          {label(column)}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {benchmarks.map((record, index) => (
                      <tr key={index} className="border-b border-border last:border-0">
                        {columns.map((column) => (
                          <td
                            key={column}
                            className="max-w-xs truncate whitespace-nowrap px-3 py-2.5 font-mono text-zinc-500"
                            title={JSON.stringify(record[column])}
                          >
                            {record[column] == null
                              ? '—'
                              : typeof record[column] === 'object'
                                ? JSON.stringify(record[column])
                                : String(record[column])}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </details>
          ) : (
            <EmptyState
              compact
              icon={Gauge}
              title="No benchmark runs yet"
              description="Complete an analysis to record performance. Speed is source seconds processed per elapsed second."
            />
          )}
        </ResourceState>
        <p className="mt-3 flex items-start gap-1.5 text-[11px] leading-relaxed text-zinc-600">
          <AlertTriangle className="mt-0.5 size-3 shrink-0" />
          Speed varies by stage, codec, and cache state. Current-run timings are not cumulative
          across server restarts.
        </p>
      </div>
    </Card>
  );
}
