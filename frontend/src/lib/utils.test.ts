import { describe, expect, it } from 'vitest';
import { bytes, isActive, time, validateTranscript } from './utils';
import type { Job, TranscriptSegment } from '../types/api';

const segment = (start: number, end: number, text = 'Actual spoken words'): TranscriptSegment => ({
  start,
  end,
  text,
  words: [],
});
describe('media display values', () => {
  it('formats source and stage durations without inventing missing values', () => {
    expect(time(undefined)).toBe('—');
    expect(time(null)).toBe('—');
    expect(time(Number.NaN)).toBe('—');
    expect(time(0)).toBe('0:00');
    expect(time(3661)).toBe('1:01:01');
    expect(time(-1)).toBe('0:00');
  });
  it('formats real storage sizes', () => {
    expect(bytes()).toBe('—');
    expect(bytes(0)).toBe('0 B');
    expect(bytes(1024)).toBe('1 KB');
    expect(bytes(1048576)).toBe('1.0 MB');
    expect(bytes(1073741824)).toBe('1.0 GB');
  });
  it('only treats queued and running jobs as active', () => {
    for (const status of ['queued', 'running']) expect(isActive({ status } as Job)).toBe(true);
    for (const status of ['completed', 'cancelled', 'failed'])
      expect(isActive({ status } as Job)).toBe(false);
  });
});
describe('manual transcript validation', () => {
  it('accepts an empty transcript and valid relative segments', () => {
    expect(validateTranscript([], 10)).toBeNull();
    expect(validateTranscript([segment(0, 2), segment(2, 5)], 10)).toBeNull();
  });
  it('rejects invalid timing, out-of-clip captions and overlaps', () => {
    expect(validateTranscript([segment(-1, 2)], 10)).toContain('between 0 and');
    expect(validateTranscript([segment(2, 2)], 10)).toContain('end after');
    expect(validateTranscript([segment(0, 11)], 10)).toContain('between 0 and');
    expect(validateTranscript([segment(0, 5), segment(4, 7)], 10)).toContain('must not overlap');
    expect(validateTranscript([segment(Number.NaN, 5)], 10)).toContain('valid start');
  });
  it('rejects empty invented caption rows, preserves and validates actual word timing', () => {
    expect(validateTranscript([segment(0, 2, '  ')], 10)).toContain('text cannot be empty');
    expect(
      validateTranscript([{ ...segment(0, 2), words: [{ start: 0, end: 1, word: 'Actual' }] }], 10),
    ).toBeNull();
    expect(
      validateTranscript([{ ...segment(0, 2), words: [{ start: 0, end: 3, word: 'Actual' }] }], 10),
    ).toContain('word timing');
  });
});
