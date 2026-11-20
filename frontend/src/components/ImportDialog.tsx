import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Check,
  FileVideo,
  FolderOpen,
  HardDrive,
  Link2,
  ShieldCheck,
  Upload,
  X,
  Youtube,
} from 'lucide-react';
import { api, uploadVideo } from '../services/api';
import { useUI } from '../stores/ui';
import { useStudio } from '../stores/studio';
import { bytes, cn, errorMessage } from '../lib/utils';
import {
  Button,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
  Input,
  Label,
  Progress,
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from './ui';
import { ErrorBanner } from './shared';
import type { Project } from '../types/api';

export function ImportDialog() {
  const open = useUI((s) => s.importOpen);
  const setOpen = useUI((s) => s.setImportOpen);
  const health = useStudio((s) => s.data.health);
  const navigate = useNavigate();
  const [mode, setMode] = useState('upload');
  const [path, setPath] = useState('');
  const [url, setUrl] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const controller = useRef<AbortController | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const locked = useRef(false);
  useEffect(() => () => controller.current?.abort(), []);
  const chooseFile = (chosen?: File) => {
    if (!chosen) return;
    if (chosen.size === 0) {
      setError('This file is empty. Choose a video with content.');
      return;
    }
    setFile(chosen);
    setError(null);
    setProgress(null);
  };
  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (locked.current) return;
    setError(null);
    if (mode === 'upload' && !file) {
      setError('Choose a video file to upload.');
      return;
    }
    if (mode === 'path' && !/^(?:[A-Za-z]:[\\/]|\\\\|\/)/.test(path.trim())) {
      setError('Enter an absolute path on the server, such as F:\\Recordings\\session.mp4.');
      return;
    }
    if (mode === 'youtube') {
      try {
        const parsed = new URL(url.trim());
        if (
          !['https:', 'http:'].includes(parsed.protocol) ||
          !['youtube.com', 'www.youtube.com', 'm.youtube.com', 'youtu.be'].includes(
            parsed.hostname,
          ) ||
          parsed.username ||
          parsed.password
        )
          throw new Error();
      } catch {
        setError('Enter a valid public youtube.com or youtu.be video URL.');
        return;
      }
    }
    locked.current = true;
    setBusy(true);
    setProgress(null);
    controller.current = new AbortController();
    try {
      let project: Project;
      if (mode === 'youtube') {
        const result = await api.youtube(url.trim());
        project = result.project;
        useStudio.getState().upsertJob(result.job);
      } else if (mode === 'path') project = await api.local(path.trim());
      else project = await uploadVideo(file!, setProgress, controller.current.signal);
      useStudio.getState().upsertProject(project);
      void useStudio.getState().refresh(['projects', 'jobs']);
      setOpen(false);
      setFile(null);
      setPath('');
      setUrl('');
      useUI
        .getState()
        .toast(
          mode === 'youtube' ? 'YouTube import queued' : 'Footage imported',
          'Open the project to find its highlights.',
        );
      navigate(`/projects/${project.id}`);
    } catch (err) {
      if (err instanceof DOMException && err.name === 'AbortError')
        setError('Upload cancelled. No analysis was started.');
      else setError(errorMessage(err));
    } finally {
      locked.current = false;
      setBusy(false);
      controller.current = null;
    }
  };
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!locked.current) {
          setOpen(next);
          setError(null);
        }
      }}
    >
      <DialogContent
        hideClose={busy}
        className="max-w-[570px]"
        onInteractOutside={(e) => {
          if (busy) e.preventDefault();
        }}
        onEscapeKeyDown={(e) => {
          if (busy) e.preventDefault();
        }}
      >
        <div className="mb-5 flex size-10 items-center justify-center rounded-lg border border-primary/20 bg-primary/10">
          <FolderOpen className="size-5 text-primary" />
        </div>
        <DialogTitle>Bring your footage in</DialogTitle>
        <DialogDescription>Import a recording. Keep the moments worth sharing.</DialogDescription>
        <form
          onSubmit={(e) => {
            void submit(e);
          }}
          className="mt-6"
        >
          <Tabs
            value={mode}
            onValueChange={(value) => {
              if (!busy) {
                setMode(value);
                setError(null);
              }
            }}
          >
            <TabsList className="grid w-full grid-cols-3">
              <TabsTrigger value="upload" disabled={busy} className="px-2 sm:px-3">
                <Upload className="hidden size-3.5 sm:block" />
                Upload
              </TabsTrigger>
              <TabsTrigger value="path" disabled={busy} className="px-2 sm:px-3">
                <HardDrive className="hidden size-3.5 sm:block" />
                Local path
              </TabsTrigger>
              <TabsTrigger value="youtube" disabled={busy} className="px-2 sm:px-3">
                <Youtube className="hidden size-3.5 sm:block" />
                YouTube
              </TabsTrigger>
            </TabsList>
            <TabsContent value="upload">
              <input
                ref={fileInput}
                type="file"
                accept="video/*,.mkv,.avi,.mov,.mp4,.webm,.m4v,.ts"
                className="sr-only"
                id="video-file"
                aria-label="Video file"
                disabled={busy}
                onChange={(e) => chooseFile(e.target.files?.[0])}
              />
              <button
                type="button"
                disabled={busy}
                onClick={() => fileInput.current?.click()}
                onDragOver={(e) => {
                  e.preventDefault();
                  if (!busy) setDragging(true);
                }}
                onDragLeave={() => setDragging(false)}
                onDrop={(e) => {
                  e.preventDefault();
                  setDragging(false);
                  if (!busy) chooseFile(e.dataTransfer.files[0]);
                }}
                className={cn(
                  'flex min-h-44 w-full flex-col items-center justify-center rounded-lg border border-dashed border-white/15 bg-white/[0.015] px-4 py-7 text-center transition-colors hover:border-primary/50 hover:bg-primary/[0.025] disabled:cursor-default',
                  dragging && 'border-primary bg-primary/5',
                  file && 'border-primary/30',
                )}
              >
                <div className="mb-3 flex size-10 items-center justify-center rounded-full bg-white/5">
                  {file ? (
                    <FileVideo className="size-5 text-primary" />
                  ) : (
                    <Upload className="size-5 text-zinc-400" />
                  )}
                </div>
                <p className="max-w-full truncate text-xs font-medium">
                  {file ? file.name : 'Drop a video here, or browse files'}
                </p>
                <p className="mt-2 text-[11px] text-muted-foreground">
                  {file
                    ? `${bytes(file.size)} · Click to choose a different file`
                    : 'MP4, MKV, MOV, WebM and other FFmpeg-supported video'}
                </p>
              </button>
              <p className="mt-3 text-[11px] leading-relaxed text-muted-foreground">
                Uploads a copy to local server storage. For large recordings, a local path avoids
                the transfer.
              </p>
            </TabsContent>
            <TabsContent value="path">
              <Label htmlFor="server-path">Absolute path on the server</Label>
              <Input
                id="server-path"
                value={path}
                onChange={(e) => setPath(e.target.value)}
                placeholder="F:\Recordings\GTA-session.mp4"
                disabled={busy}
                autoComplete="off"
              />
              <div className="mt-4 rounded-lg border border-border bg-black/10 p-3 text-[11px] leading-relaxed text-muted-foreground">
                <p className="mb-1.5 flex items-center gap-2 font-medium text-zinc-300">
                  <Link2 className="size-3.5 text-primary" />
                  Reference, don’t duplicate
                </p>
                The original file stays where it is. This path must exist on the machine running the
                backend. Keep the source available for previews and exports.
              </div>
            </TabsContent>
            <TabsContent value="youtube">
              <Label htmlFor="youtube-url">Public YouTube video URL</Label>
              <Input
                type="url"
                id="youtube-url"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                placeholder="https://www.youtube.com/watch?v=…"
                disabled={busy}
              />
              <p className="mt-3 text-[11px] leading-relaxed text-muted-foreground">
                The local server downloads the public video using yt-dlp. Private, restricted, and
                sign-in-only videos are not supported. Only import footage you have permission to
                use.
              </p>
              {health && !health.capabilities.yt_dlp && (
                <ErrorBanner
                  className="mt-3"
                  message="yt-dlp is not installed on the server. Install it before importing YouTube videos."
                />
              )}
            </TabsContent>
          </Tabs>
          {error && <ErrorBanner message={error} className="mt-4" />}
          {busy && mode === 'upload' && (
            <div className="mt-5">
              <div className="mb-2 flex justify-between text-[11px]">
                <span className="text-muted-foreground">
                  {progress === 1 ? 'Upload sent · reading media on server…' : 'Uploading video…'}
                </span>
                <span className="font-mono text-primary">
                  {progress === null ? 'Waiting for transfer' : `${Math.round(progress * 100)}%`}
                </span>
              </div>
              <Progress value={progress} label="File upload progress" />
              {file && progress !== null && (
                <p className="mt-1.5 font-mono text-[11px] text-zinc-500">
                  {bytes(file.size * progress)} / {bytes(file.size)}
                </p>
              )}
            </div>
          )}
          <div className="mt-6 flex flex-wrap items-center justify-between gap-3 border-t border-border pt-5">
            <span className="flex items-center gap-1.5 text-[11px] text-zinc-500">
              <ShieldCheck className="size-3.5" />
              Local-first processing
            </span>
            <div className="flex flex-wrap gap-2">
              {busy && mode === 'upload' ? (
                <Button
                  type="button"
                  variant="secondary"
                  onClick={() => controller.current?.abort()}
                >
                  <X />
                  Cancel upload
                </Button>
              ) : (
                !busy && (
                  <Button type="button" variant="ghost" onClick={() => setOpen(false)}>
                    Cancel
                  </Button>
                )
              )}
              <Button
                type="submit"
                loading={busy}
                disabled={mode === 'youtube' && health?.capabilities.yt_dlp === false}
              >
                {!busy && <Check />}
                {busy
                  ? mode === 'youtube'
                    ? 'Queuing import…'
                    : mode === 'upload'
                      ? 'Uploading…'
                      : 'Reading media…'
                  : 'Import footage'}
              </Button>
            </div>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
