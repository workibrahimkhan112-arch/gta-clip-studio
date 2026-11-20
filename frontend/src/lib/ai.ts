import type { CloudProvider, Settings } from '../types/api';

// Matches the backend's known-price catalog, not a claim of account availability.
export const apiModels: Record<CloudProvider, { value: string; label: string }[]> = {
  openai: [{ value: 'gpt-6-luna', label: 'GPT-6 Luna · low cost' }],
  gemini: [{ value: 'gemini-3.5-flash-lite', label: 'Gemini 3.5 Flash Lite' }],
  groq: [
    { value: 'openai/gpt-oss-20b', label: 'GPT OSS 20B' },
    { value: 'openai/gpt-oss-120b', label: 'GPT OSS 120B' },
  ],
};
export function defaultReviewModel(provider: Settings['ai_scoring_provider']): string {
  return provider === 'none' ? '' : apiModels[provider][0].value;
}
export function modelWarning(model: string): string | null {
  if (/^gpt-4\.1-nano(?:-|$)/.test(model))
    return 'GPT-4.1 nano has shut down. Choose a current model; requests will not switch models automatically.';
  if (/^gpt-5-nano(?:-|$)/.test(model))
    return 'GPT-5 nano is deprecated. Choose a current model; GPT-6 Luna is the low-cost OpenAI default.';
  return null;
}
