import { useState } from 'react';
import { NavLink, Outlet, Link, useLocation } from 'react-router-dom';
import {
  Activity,
  ArrowUpRight,
  Check,
  PanelLeftClose,
  PanelLeftOpen,
  ChevronRight,
  Clapperboard,
  Download,
  Film,
  FolderClosed,
  HardDrive,
  LayoutDashboard,
  Menu,
  Plus,
  Radio,
  Settings2,
  ShieldCheck,
  Sparkles,
  WifiOff,
} from '../icons';
import { useStudio } from '../stores/studio';
import { useUI } from '../stores/ui';
import { bytes, cn, isActive } from '../lib/utils';
import { Button, Dialog, DialogContent, DialogDescription, DialogTitle, Tooltip } from './ui';
import { ImportDialog } from './ImportDialog';
import { Toasts } from './shared';

const navigation = [
  { to: '/', label: 'Dashboard', icon: LayoutDashboard, end: true },
  { to: '/projects', label: 'Projects', icon: FolderClosed },
  { to: '/highlights', label: 'Highlights', icon: Sparkles },
  { to: '/exports', label: 'Exports', icon: Download },
  { to: '/settings', label: 'Settings', icon: Settings2 },
];
function Brand() {
  return (
    <Link to="/" className="flex items-center gap-2.5">
      <div className="flex size-9 items-center justify-center rounded-lg bg-primary text-primary-foreground">
        <Clapperboard className="size-5" strokeWidth={2.25} />
      </div>
      <div>
        <p className="text-sm font-bold leading-none tracking-tight">
          GTA<span className="font-normal text-zinc-400"> / </span>CLIP STUDIO
        </p>
        <p className="mt-1.5 text-[11px] font-medium uppercase tracking-[0.23em] text-zinc-500">
          Video highlights & subtitles
        </p>
      </div>
    </Link>
  );
}
function SidebarContent({
  onNavigate,
  collapsed = false,
}: {
  onNavigate?: () => void;
  collapsed?: boolean;
}) {
  const projects = useStudio((s) => s.data.projects);
  const highlights = useStudio((s) => s.data.highlights);
  const health = useStudio((s) => s.data.health);
  const jobs = useStudio((s) => s.data.jobs);
  const loaded = useStudio((s) => s.loaded.projects);
  const busy = jobs.find(isActive);
  return (
    <>
      <div className={cn('pb-7 pt-7', collapsed ? 'flex justify-center px-2' : 'px-5')}>
        {collapsed ? (
          <Link
            to="/"
            aria-label="GTA Clip Studio home"
            className="flex size-9 items-center justify-center rounded-lg bg-primary text-primary-foreground"
          >
            <Clapperboard className="size-5" />
          </Link>
        ) : (
          <Brand />
        )}
      </div>
      <div className={collapsed ? 'px-3' : 'px-4'}>
        <Button
          className={cn('w-full gap-2.5', collapsed ? 'justify-center px-0' : 'justify-start')}
          aria-label={collapsed ? 'New project' : undefined}
          onClick={() => {
            useUI.getState().setImportOpen(true);
            onNavigate?.();
          }}
        >
          <Plus className="!size-4" />
          {!collapsed && (
            <>
              New project<span className="ml-auto text-[11px] opacity-60">+</span>
            </>
          )}
        </Button>
      </div>
      {!collapsed && <p className="eyebrow mb-3 mt-8 px-6 text-[11px]">Workspace</p>}
      <nav className="space-y-1 px-3" aria-label="Main navigation">
        {navigation.map(({ to, label, icon: Icon, end }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            onClick={onNavigate}
            className={({ isActive }) =>
              cn(
                'nav-item group',
                collapsed && 'justify-center px-0',
                isActive && 'nav-item-active',
              )
            }
            aria-label={collapsed ? label : undefined}
            title={collapsed ? label : undefined}
          >
            <Icon className="size-[17px] shrink-0" strokeWidth={1.7} />
            {!collapsed && <span className="flex-1">{label}</span>}
            {!collapsed && loaded && label === 'Projects' && (
              <span className="nav-count">{projects.length}</span>
            )}
            {!collapsed && loaded && label === 'Highlights' && highlights.length > 0 && (
              <span className="nav-count">{highlights.length}</span>
            )}
          </NavLink>
        ))}
      </nav>
      {!collapsed && (
        <div className="mt-8 px-6">
          <div className="mb-3 flex items-center justify-between">
            <p className="eyebrow text-[11px]">Recent projects</p>
            <Film className="size-3 text-zinc-600" />
          </div>
          {projects.length ? (
            <div className="space-y-0.5">
              {[...projects]
                .sort((a, b) => b.created_at.localeCompare(a.created_at))
                .slice(0, 4)
                .map((project) => (
                  <Link
                    onClick={onNavigate}
                    to={`/projects/${project.id}`}
                    key={project.id}
                    className="flex items-center gap-2.5 rounded py-2 text-[11px] text-zinc-500 hover:text-zinc-200"
                  >
                    <span
                      className={cn(
                        'size-1 shrink-0 rounded-full',
                        project.status === 'failed'
                          ? 'bg-red-400/70'
                          : project.status === 'analyzed'
                            ? 'bg-primary/70'
                            : 'bg-zinc-600',
                      )}
                    />
                    <span className="truncate">{project.name}</span>
                  </Link>
                ))}
            </div>
          ) : (
            <p className="text-[11px] leading-relaxed text-zinc-600">
              Imported recordings will appear here.
            </p>
          )}
        </div>
      )}
      {!collapsed && (
        <div className="mt-auto px-4 pb-5 pt-8">
          <div className="rounded-lg border border-white/[0.06] bg-white/[0.02] p-3.5">
            <div className="mb-3 flex items-center gap-2 text-[11px] font-medium">
              <HardDrive className="size-3.5 text-primary" />
              Local workstation
              <ShieldCheck className="ml-auto size-3 text-zinc-500" />
            </div>
            <div className="flex items-center justify-between text-[11px] text-zinc-500">
              <span>Storage available</span>
              <span className="font-mono text-zinc-300">
                {health ? bytes(health.storage.free_bytes) : 'Not connected'}
              </span>
            </div>
            <div className="mt-3 border-t border-white/5 pt-3">
              <p className="flex items-center gap-1.5 text-[11px] text-zinc-500">
                {busy ? (
                  <Activity className="size-3 text-amber-200" />
                ) : (
                  <Check className="size-3 text-primary" />
                )}
                {busy ? 'Processing queue active' : 'One processing job at a time'}
              </p>
            </div>
          </div>
          <p className="mt-4 flex items-center justify-between px-1 text-[11px] text-zinc-600">
            <span>GTA CLIP STUDIO</span>
            <span>{health ? `v${health.version}` : 'LOCAL'}</span>
          </p>
        </div>
      )}
    </>
  );
}
export function Layout() {
  const [mobileOpen, setMobileOpen] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => {
    try {
      return localStorage.getItem('gta-sidebar-collapsed') === 'true';
    } catch {
      return false;
    }
  });
  const toggleSidebar = () =>
    setSidebarCollapsed((current) => {
      const next = !current;
      try {
        localStorage.setItem('gta-sidebar-collapsed', String(next));
      } catch {
        /* Storage may be unavailable. */
      }
      return next;
    });
  const connection = useStudio((s) => s.connection);
  const healthError = useStudio((s) => s.errors.health);
  const location = useLocation();
  const section = location.pathname.startsWith('/projects/')
    ? 'Project workspace'
    : navigation.find((n) => n.to === location.pathname)?.label || 'Workspace';
  const connectionLabel =
    connection === 'live'
      ? 'Live connection'
      : connection === 'polling'
        ? 'Polling updates'
        : connection === 'offline'
          ? 'Server offline'
          : 'Connecting';
  return (
    <div className="min-h-dvh">
      <a href="#main-content" className="skip-link">
        Skip to content
      </a>
      <aside className={cn('sidebar hidden lg:flex', sidebarCollapsed && '!w-[72px]')}>
        <SidebarContent collapsed={sidebarCollapsed} />
      </aside>
      <Dialog open={mobileOpen} onOpenChange={setMobileOpen}>
        <DialogContent className="!left-0 !top-0 flex h-dvh max-h-none w-64 !translate-x-0 !translate-y-0 flex-col gap-0 rounded-none border-y-0 border-l-0 p-0">
          <DialogTitle className="sr-only">Workspace navigation</DialogTitle>
          <DialogDescription className="sr-only">Navigate GTA Clip Studio.</DialogDescription>
          <SidebarContent onNavigate={() => setMobileOpen(false)} />
        </DialogContent>
      </Dialog>
      <div className={sidebarCollapsed ? 'lg:ml-[72px]' : 'lg:ml-[232px]'}>
        <header className="app-header">
          <div className="flex min-w-0 items-center gap-3">
            <Button
              variant="ghost"
              size="icon"
              className="lg:hidden"
              onClick={() => setMobileOpen(true)}
              aria-label="Open navigation"
            >
              <Menu />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              className="hidden lg:inline-flex"
              onClick={toggleSidebar}
              aria-label={sidebarCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}
              title={sidebarCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}
            >
              {sidebarCollapsed ? <PanelLeftOpen /> : <PanelLeftClose />}
            </Button>
            <span className="hidden text-[11px] text-zinc-500 sm:inline">Workspace</span>
            <ChevronRight className="hidden size-3 text-zinc-700 sm:block" />
            <span className="truncate text-[11px] font-medium text-zinc-300">{section}</span>
          </div>
          <div className="flex items-center gap-3 sm:gap-5">
            <Tooltip
              content={
                healthError ||
                (connection === 'live'
                  ? 'Receiving job updates from the local server via SSE.'
                  : connection === 'polling'
                    ? 'Live stream unavailable. Refreshing from the server every four seconds.'
                    : connection === 'offline'
                      ? 'Cannot reach the local backend. Retrying automatically.'
                      : 'Connecting to your local processing server.')
              }
            >
              <button
                className="flex items-center gap-2 rounded text-[11px] text-zinc-500"
                onClick={() => {
                  void useStudio.getState().refresh();
                }}
              >
                {connection === 'offline' ? (
                  <WifiOff className="size-3 text-red-300" />
                ) : (
                  <span
                    className={cn(
                      'size-1.5 rounded-full',
                      connection === 'live'
                        ? 'bg-primary shadow-[0_0_8px_#bef26444]'
                        : connection === 'connecting'
                          ? 'bg-zinc-500'
                          : 'bg-amber-200',
                    )}
                  />
                )}
                <span className="hidden sm:inline">{connectionLabel}</span>
              </button>
            </Tooltip>
            <div className="h-4 w-px bg-border" />
            <span className="flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wider text-zinc-500">
              <Radio className="size-3 text-zinc-400" />
              Local<span className="hidden sm:inline">mode</span>
            </span>
          </div>
        </header>
        <main id="main-content" className="app-main">
          <Outlet />
        </main>
        <footer className="app-footer">
          <span className="flex items-center gap-1.5">
            <ShieldCheck className="size-3" />
            Local processing · cloud tools require consent
          </span>
          <Link to="/settings" className="flex items-center gap-1 hover:text-zinc-300">
            Settings
            <ArrowUpRight className="size-3" />
          </Link>
        </footer>
      </div>
      <ImportDialog />
      <Toasts />
    </div>
  );
}
