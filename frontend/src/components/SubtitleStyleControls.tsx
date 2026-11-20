import type { LayoutConfig, SubtitleStyle } from '../types/api';
import { containSubtitleStyle, defaultSubtitleStyle } from '../lib/subtitleStyle';
import { Button, Checkbox, Label, Select } from './ui';

export function SubtitleStyleControls({
  value,
  position,
  disabled,
  onChange,
}: {
  value: SubtitleStyle | null;
  position: LayoutConfig['caption_position'];
  disabled: boolean;
  onChange: (style: SubtitleStyle | null) => void;
}) {
  const style = value ?? defaultSubtitleStyle(position);
  const update = <K extends keyof SubtitleStyle>(key: K, value: SubtitleStyle[K]) =>
    onChange(containSubtitleStyle({ ...style, [key]: value }));
  const slider = (
    key: 'font_size' | 'width' | 'outline_width' | 'background_opacity' | 'x' | 'y',
    name: string,
    min: number,
    max: number,
    step: number,
    suffix = '',
  ) => (
    <label className="block space-y-2 text-xs">
      <span className="flex justify-between gap-2 text-zinc-300">
        <span>{name}</span>
        <output className="font-mono text-zinc-400">
          {Math.round(style[key] * (suffix === '%' ? 100 : 1))}
          {suffix}
        </output>
      </span>
      <input
        aria-label={name}
        className="w-full"
        type="range"
        min={min}
        max={max}
        step={step}
        value={style[key]}
        disabled={disabled}
        onChange={(event) => update(key, event.target.valueAsNumber)}
      />
    </label>
  );
  const color = (
    key: 'text_color' | 'highlight_color' | 'outline_color' | 'background_color',
    name: string,
  ) => (
    <label className="flex items-center justify-between gap-2 text-xs text-zinc-300">
      {name}
      <input
        aria-label={name}
        type="color"
        className="h-8 w-12 cursor-pointer rounded border border-border bg-transparent p-1"
        value={style[key]}
        disabled={disabled}
        onChange={(event) => update(key, event.target.value.toUpperCase())}
      />
    </label>
  );
  return (
    <fieldset disabled={disabled} className="space-y-5 p-4" aria-label="Subtitle style controls">
      <p className="text-[11px] leading-relaxed text-zinc-400">
        Drag the subtitle box in the preview. Use its side handles to change text width. Arrow keys
        work too; Shift moves farther.
      </p>
      <div>
        <Label htmlFor="subtitle-font">Font</Label>
        <Select
          id="subtitle-font"
          value={style.font_family}
          disabled={disabled}
          onValueChange={(value) => update('font_family', value as SubtitleStyle['font_family'])}
          options={['Arial', 'Impact', 'Verdana', 'Inter', 'Montserrat'].map((font) => ({ value: font, label: font }))}
        />
      </div>
      {slider('font_size', 'Font size', 18, 96, 1, ' px')}
      {slider('width', 'Text width', 0.2, 1, 0.01, '%')}
      <div className="grid grid-cols-2 gap-4">
        {color('text_color', 'Text color')}
        {color('highlight_color', 'Active word color')}
      </div>
      <div>
        <Label>Position</Label>
        <div className="flex flex-wrap gap-1" role="group" aria-label="Subtitle position presets">
          {[
            { label: 'Center', y: 0.5 },
            { label: 'Lower middle', y: 0.73 },
            { label: 'Bottom', y: 0.88 },
          ].map((preset) => (
            <Button
              type="button"
              key={preset.label}
              size="sm"
              variant={style.x === 0.5 && style.y === preset.y ? 'secondary' : 'ghost'}
              aria-pressed={style.x === 0.5 && style.y === preset.y}
              onClick={() => onChange({ ...style, x: 0.5, y: preset.y })}
            >
              {preset.label}
            </Button>
          ))}
        </div>
      </div>
      <details className="rounded-md border border-border p-3">
        <summary className="cursor-pointer text-xs text-zinc-400">Advanced typography</summary>
        <div className="mt-4 space-y-4">
          <div className="flex items-center gap-2">
            <Checkbox
              id="subtitle-bold"
              checked={style.bold}
              onCheckedChange={(value) => update('bold', value === true)}
            />
            <Label htmlFor="subtitle-bold" className="mb-0">
              Bold
            </Label>
          </div>
          {slider('outline_width', 'Outline width', 0, 8, 0.5, ' px')}
          {color('outline_color', 'Outline color')}
          {color('background_color', 'Background color')}
          {slider('background_opacity', 'Background opacity', 0, 0.85, 0.01, '%')}
          {slider('x', 'Horizontal center', style.width / 2, 1 - style.width / 2, 0.005, '%')}
          {slider('y', 'Vertical center', 0.05, 0.95, 0.005, '%')}
          <p className="text-[11px] text-zinc-500">
            Pixel sizes are measured at 720px output width. Export scales them with the video.
          </p>
        </div>
      </details>
      <Button
        type="button"
        variant="ghost"
        size="sm"
        disabled={disabled || value === null}
        onClick={() => onChange(null)}
      >
        Reset to legacy style
      </Button>
      <p className="text-[11px] text-zinc-500">
        {value
          ? 'Saved with this highlight when you save changes.'
          : 'Using the original subtitle style and project position. Nothing changes until you edit a control.'}
      </p>
    </fieldset>
  );
}
