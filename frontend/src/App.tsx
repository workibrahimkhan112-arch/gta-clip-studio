import { Component, Suspense, lazy } from 'react';
import type { ErrorInfo, ReactNode } from 'react';
import { BrowserRouter, Link, Route, Routes } from 'react-router-dom';
import { AlertTriangle, Loader2 } from './icons';
import { Layout } from './components/Layout';
import { Button, TooltipProvider } from './components/ui';
import { useLiveData } from './hooks/useLiveData';
import { EmptyState } from './components/shared';

const Dashboard = lazy(() => import('./pages/Dashboard').then((m) => ({ default: m.Dashboard })));
const Projects = lazy(() => import('./pages/Projects').then((m) => ({ default: m.Projects })));
const ProjectWorkspace = lazy(() =>
  import('./pages/ProjectWorkspace').then((m) => ({ default: m.ProjectWorkspace })),
);
const Highlights = lazy(() =>
  import('./pages/Highlights').then((m) => ({ default: m.Highlights })),
);
const Exports = lazy(() => import('./pages/Exports').then((m) => ({ default: m.Exports })));
const Settings = lazy(() => import('./pages/Settings').then((m) => ({ default: m.Settings })));

function StudioRoutes() {
  useLiveData();
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route
          index
          element={
            <Page>
              <Dashboard />
            </Page>
          }
        />
        <Route
          path="projects"
          element={
            <Page>
              <Projects />
            </Page>
          }
        />
        <Route
          path="projects/:projectId"
          element={
            <Page>
              <ProjectWorkspace />
            </Page>
          }
        />
        <Route
          path="highlights"
          element={
            <Page>
              <Highlights />
            </Page>
          }
        />
        <Route
          path="exports"
          element={
            <Page>
              <Exports />
            </Page>
          }
        />
        <Route
          path="settings"
          element={
            <Page>
              <Settings />
            </Page>
          }
        />
        <Route
          path="*"
          element={
            <EmptyState
              title="This cut doesn’t exist"
              description="The page you’re looking for is not in this workspace."
            >
              <Button asChild>
                <Link to="/">Back to dashboard</Link>
              </Button>
            </EmptyState>
          }
        />
      </Route>
    </Routes>
  );
}
function Page({ children }: { children: ReactNode }) {
  return (
    <Suspense
      fallback={
        <div className="flex min-h-60 items-center justify-center gap-2 text-xs text-zinc-500">
          <Loader2 className="size-4 animate-spin" />
          Opening workspace…
        </div>
      }
    >
      {children}
    </Suspense>
  );
}
class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state: { error: Error | null } = { error: null };
  static getDerivedStateFromError(error: Error) {
    return { error };
  }
  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('Studio UI error', error, info.componentStack);
  }
  render() {
    if (this.state.error)
      return (
        <div className="flex min-h-dvh items-center justify-center p-8">
          <div className="max-w-md text-center">
            <AlertTriangle className="mx-auto mb-5 size-8 text-amber-200" />
            <h1 className="text-base font-semibold">The studio couldn’t render this view</h1>
            <p className="my-4 break-words text-xs leading-relaxed text-zinc-400">
              {this.state.error.message}
            </p>
            <Button onClick={() => window.location.reload()}>Reload studio</Button>
          </div>
        </div>
      );
    return this.props.children;
  }
}
export default function App() {
  return (
    <ErrorBoundary>
      <TooltipProvider delayDuration={350}>
        <BrowserRouter>
          <StudioRoutes />
        </BrowserRouter>
      </TooltipProvider>
    </ErrorBoundary>
  );
}
