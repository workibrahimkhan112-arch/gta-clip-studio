import { apiModels, defaultReviewModel, modelWarning } from '../lib/ai';
import type { Settings } from '../types/api';
import { Input, Label, Select } from './ui';

export function APIModelPicker({
  provider,
  value,
  disabled,
  onChange,
}: {
  provider: Settings['ai_scoring_provider'];
  value: string;
  disabled: boolean;
  onChange: (value: string) => void;
}) {
  const catalog = provider === 'none' ? [] : apiModels[provider];
  const selected = value || defaultReviewModel(provider);
  const custom = !!value && !catalog.some((model) => model.value === value);
  const warning = modelWarning(value);
  return (
    <div className="w-full space-y-2 sm:max-w-sm">
      {provider === 'none' ? (
        <Input
          id="review-model"
          value={value}
          disabled={disabled}
          onChange={(event) => onChange(event.target.value)}
          placeholder="Choose a provider first"
        />
      ) : (
        <Select
          id="review-model"
          value={selected}
          disabled={disabled}
          onValueChange={onChange}
          options={[...catalog, ...(custom ? [{ value, label: `${value} · custom` }] : [])]}
        />
      )}
      {provider !== 'none' && (
        <p className="text-[11px] text-zinc-400">
          Known-price API models. Access depends on your provider account. No automatic fallback.
        </p>
      )}
      {warning && (
        <p role="status" className="text-[11px] text-amber-200">
          {warning}
        </p>
      )}
      {provider !== 'none' && (
        <details className="text-[11px] text-zinc-400" open={custom || undefined}>
          <summary className="cursor-pointer py-1">Custom API model</summary>
          <div className="mt-2 space-y-2">
            <Label htmlFor="custom-review-model">Custom model ID</Label>
            <Input
              id="custom-review-model"
              value={value}
              disabled={disabled}
              onChange={(event) => onChange(event.target.value.trim())}
              placeholder={defaultReviewModel(provider)}
            />
            <p>
              Unpriced models require the explicit opt-in under Advanced AI limits. A dollar ceiling
              cannot be guaranteed for them.
            </p>
          </div>
        </details>
      )}
    </div>
  );
}
