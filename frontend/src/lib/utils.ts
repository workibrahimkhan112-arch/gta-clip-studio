import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';
import type { Job, TranscriptSegment } from '../types/api';

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
export function time(seconds?: number | null, precise = false): string {
  if (seconds == null || !Number.isFinite(seconds)) return '—';
  const value = Math.max(0, seconds);
  const hours = Math.floor(value / 3600);
  const minutes = Math.floor(value / 60) % 60;
  const secs = Math.floor(value % 60)
    .toString()
    .padStart(2, '0');
  const base = hours
    ? `${hours}:${minutes.toString().padStart(2, '0')}:${secs}`
    : `${minutes}:${secs}`;
  return precise ? `${base}.${Math.floor((value % 1) * 10)}` : base;
}
export function bytes(value?: number): string {
  if (value == null || !Number.isFinite(value)) return '—';
  if (value === 0) return '0 B';
  const i = Math.min(Math.floor(Math.log(Math.max(1, value)) / Math.log(1024)), 4);
  return `${(value / 1024 ** i).toFixed(i > 1 ? 1 : 0)} ${['B', 'KB', 'MB', 'GB', 'TB'][i]}`;
}
export function date(value: string): string {
  const d = new Date(value);
  return Number.isNaN(d.getTime())
    ? 'Unknown date'
    : new Intl.DateTimeFormat(undefined, {
        month: 'short',
        day: 'numeric',
        year: 'numeric',
      }).format(d);
}
export function label(value: string): string {
  return value.replace(/[_-]/g, ' ').replace(/^\w/, (s) => s.toUpperCase());
}
export function isActive(job: Job): boolean {
  return job.status === 'running' || job.status === 'queued';
}
export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'Something went wrong. Please try again.';
}
export function validateTranscript(segments: TranscriptSegment[], duration: number): string | null {
  let previousEnd = 0;
  for (let i = 0; i < segments.length; i++) {
    const { start, end, text, words } = segments[i];
    const prefix = `Caption ${i + 1}`;
    if (!Number.isFinite(start) || !Number.isFinite(end))
      return `${prefix}: enter valid start and end times.`;
    if (start < 0 || end <= start || end > duration + 0.01)
      return `${prefix}: times must be between 0 and ${duration.toFixed(2)} seconds, with the end after the start.`;
    if (start < previousEnd) return `${prefix}: captions must be in order and must not overlap.`;
    if (!text.trim()) return `${prefix}: text cannot be empty.`;
    if (
      words.some(
        (w) =>
          !Number.isFinite(w.start) ||
          !Number.isFinite(w.end) ||
          w.start < start ||
          w.end > end ||
          w.end <= w.start ||
          !w.word.trim(),
      )
    )
      return `${prefix}: word timing is outside the segment. Edit its text or timing to reset word timings.`;
    previousEnd = end;
  }
  return null;
}
