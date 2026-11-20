import { describe, expect, it } from 'vitest';
import { containSubtitleStyle, defaultSubtitleStyle, resizeSubtitleWidth } from './subtitleStyle';
import { subtitleAt } from './subtitles';
import { subtitleStyleFixture } from '../test/fixtures';

describe('subtitle style geometry and preview contract', () => {
  it('derives controls from legacy positions without mutating saved data', () => {
    expect(defaultSubtitleStyle().y).toBe(0.73);
    expect(defaultSubtitleStyle('center').y).toBe(0.5);
    expect(defaultSubtitleStyle('bottom').y).toBe(0.88);
    const style = { ...subtitleStyleFixture, x: 0, y: 2, width: 2 };
    expect(containSubtitleStyle(style)).toMatchObject({ x: 0.5, y: 0.95, width: 1 });
    expect(style).toMatchObject({ x: 0, y: 2, width: 2 });
  });
  it.each(['left', 'right'] as const)(
    'resizes the %s width edge without moving the opposite edge',
    (side) => {
      const original = subtitleStyleFixture;
      for (const dx of [-2, -0.1, 0.1, 2]) {
        const next = resizeSubtitleWidth(original, side, dx);
        expect(next.width).toBeGreaterThanOrEqual(0.2 - 1e-9);
        expect(next.width).toBeLessThanOrEqual(1);
        expect(next.x - next.width / 2).toBeGreaterThanOrEqual(-1e-9);
        expect(next.x + next.width / 2).toBeLessThanOrEqual(1 + 1e-9);
        const sign = side === 'left' ? 1 : -1;
        expect(next.x + (sign * next.width) / 2).toBeCloseTo(
          original.x + (sign * original.width) / 2,
        );
        expect(next.y).toBe(original.y);
      }
    },
  );
  it('uses style width and font size for phrase groups, not the legacy 28-character line break', () => {
    const words = Array.from({ length: 4 }, (_, i) => ({
      word: 'Wideword',
      start: i,
      end: i + 0.5,
    }));
    const transcript = [
      { start: 0, end: 4, text: words.map((word) => word.word).join(' '), words },
    ];
    const narrow = { ...subtitleStyleFixture, width: 0.2, x: 0.5, font_size: 96 };
    expect(subtitleAt(transcript, 1.25, 'animated', narrow).map((word) => word.text)).toEqual([
      'Wideword',
    ]);
    expect(
      subtitleAt(transcript, 1.25, 'animated', narrow).map((word) => word.highlighted),
    ).toEqual([true]);
    expect(subtitleAt(transcript, 1.25, 'standard', narrow, 1080).map((word) => word.text)).toEqual(
      ['Wideword'],
    );
    expect(subtitleAt(transcript, 1.25, 'standard').some((word) => word.lineBreak)).toBe(true);
    expect(
      subtitleAt(transcript, 1.25, 'standard', { ...narrow, width: 1, font_size: 18 }).some(
        (word) => word.lineBreak,
      ),
    ).toBe(false);
    expect(subtitleAt(transcript, 0.75, 'animated', narrow)).toEqual([]);
  });
  it('never creates words or timing in an empty or manually edited styled preview', () => {
    expect(subtitleAt([], 0, 'animated', subtitleStyleFixture)).toEqual([]);
    const transcript = [{ start: 0, end: 2, text: 'Authored words', words: [] }];
    expect(
      subtitleAt(transcript, 1, 'animated', subtitleStyleFixture).every(
        (word) => !word.highlighted,
      ),
    ).toBe(true);
    expect(transcript[0].words).toEqual([]);
    const lines = [{ ...transcript[0], text: 'Authored\nwords' }];
    expect(subtitleAt(lines, 1, 'standard', subtitleStyleFixture)[1].separator).toBe('\n');
    expect(subtitleAt(transcript, 1, 'none', subtitleStyleFixture)).toEqual([]);
  });
});
