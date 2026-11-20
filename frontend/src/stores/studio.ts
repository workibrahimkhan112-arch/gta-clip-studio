import { create } from 'zustand';
import { api } from '../services/api';
import { errorMessage } from '../lib/utils';
import type {
  Benchmark,
  ExportRecord,
  Health,
  Highlight,
  Job,
  Project,
  Settings,
} from '../types/api';

interface Resources {
  projects: Project[];
  highlights: Highlight[];
  exports: ExportRecord[];
  jobs: Job[];
  settings: Settings | null;
  health: Health | null;
  benchmarks: Benchmark[];
}
export type ResourceKey = keyof Resources;
const loaders = {
  projects: api.projects,
  highlights: api.highlights,
  exports: api.exports,
  jobs: api.jobs,
  settings: api.settings,
  health: api.health,
  benchmarks: api.benchmarks,
};
const versions: Partial<Record<ResourceKey, number>> = {};
interface StudioState {
  data: Resources;
  loading: Partial<Record<ResourceKey, boolean>>;
  loaded: Partial<Record<ResourceKey, boolean>>;
  errors: Partial<Record<ResourceKey, string | null>>;
  connection: 'connecting' | 'live' | 'polling' | 'offline';
  refresh: (keys?: ResourceKey[]) => Promise<void>;
  setConnection: (connection: StudioState['connection']) => void;
  acceptJobs: (jobs: Job[]) => void;
  upsertJob: (job: Job) => void;
  upsertProject: (project: Project) => void;
  upsertHighlight: (highlight: Highlight) => void;
  removeHighlight: (identifier: string) => void;
  setSettings: (settings: Settings) => void;
}
export const useStudio = create<StudioState>((set, get) => ({
  data: {
    projects: [],
    highlights: [],
    exports: [],
    jobs: [],
    settings: null,
    health: null,
    benchmarks: [],
  },
  loading: {},
  loaded: {},
  errors: {},
  connection: 'connecting',
  refresh: async (keys = ['projects', 'highlights', 'exports', 'jobs', 'settings', 'health']) => {
    await Promise.all(
      keys.map(async (key) => {
        const version = (versions[key] ?? 0) + 1;
        versions[key] = version;
        set((s) => ({ loading: { ...s.loading, [key]: true } }));
        try {
          const value = await loaders[key]();
          if (versions[key] !== version) return;
          set((s) => ({
            data: { ...s.data, [key]: value },
            errors: { ...s.errors, [key]: null },
            loaded: { ...s.loaded, [key]: true },
          }));
        } catch (error) {
          if (versions[key] === version)
            set((s) => ({ errors: { ...s.errors, [key]: errorMessage(error) } }));
        } finally {
          if (versions[key] === version) set((s) => ({ loading: { ...s.loading, [key]: false } }));
        }
      }),
    );
  },
  setConnection: (connection) => set({ connection }),
  acceptJobs: (jobs) => {
    versions.jobs = (versions.jobs ?? 0) + 1;
    set((s) => ({
      data: { ...s.data, jobs },
      errors: { ...s.errors, jobs: null },
      loading: { ...s.loading, jobs: false },
      loaded: { ...s.loaded, jobs: true },
    }));
  },
  upsertJob: (job) => get().acceptJobs([job, ...get().data.jobs.filter((j) => j.id !== job.id)]),
  upsertProject: (project) => {
    // A saved layout wins over an older list refresh, but a single workspace fetch
    // must not prevent the initial full project list from loading.
    if (get().loaded.projects) versions.projects = (versions.projects ?? 0) + 1;
    set((s) => ({
      loading: s.loaded.projects ? { ...s.loading, projects: false } : s.loading,
      data: {
        ...s.data,
        projects: [project, ...s.data.projects.filter((p) => p.id !== project.id)],
      },
    }));
  },
  upsertHighlight: (highlight) => {
    // A GET started before a successful edit must not overwrite that edit.
    versions.highlights = (versions.highlights ?? 0) + 1;
    set((s) => ({
      loading: { ...s.loading, highlights: false },
      data: {
        ...s.data,
        highlights: s.data.highlights.some((h) => h.id === highlight.id)
          ? s.data.highlights.map((h) => (h.id === highlight.id ? highlight : h))
          : [...s.data.highlights, highlight],
      },
    }));
  },
  removeHighlight: (identifier) => {
    versions.highlights = (versions.highlights ?? 0) + 1;
    set((s) => ({
      loading: { ...s.loading, highlights: false },
      data: { ...s.data, highlights: s.data.highlights.filter((h) => h.id !== identifier) },
    }));
  },
  setSettings: (settings) => {
    versions.settings = (versions.settings ?? 0) + 1;
    set((s) => ({ data: { ...s.data, settings }, loading: { ...s.loading, settings: false } }));
  },
}));
