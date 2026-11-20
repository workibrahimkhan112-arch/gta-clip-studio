import { useState } from 'react';
import type { CloudProvider, Integrations } from '../types/api';
import { useIntegrations } from '../hooks/useIntegrations';
import { api } from '../services/api';
import { Badge, Button, Card, CardHeader, CardTitle, Input, Label } from './ui';
import { ErrorBanner } from './shared';
import { ChatGPTConnection } from './ChatGPTConnection';

export const cloudPrivacy =
  'Cloud is opt-in. Text review and title suggestions send saved transcript and measured signals to the selected provider. Vision review sends sparse timestamped frames too (Gemini or OpenAI only). Groq captions upload clip audio. Providers may retain data under their policies. API charges, rate limits, and ChatGPT plan eligibility and limits may apply; free quotas are not guaranteed. Local detection and local captions do not need cloud consent.';

export function IntegrationSettings() {
  const { integrations, setIntegrations, error, loading, refresh } = useIntegrations();
  return (
    <Card className="mt-5">
      <CardHeader>
        <CardTitle>Connections</CardTitle>
        <Badge>Local server</Badge>
      </CardHeader>
      <div className="space-y-4 p-5">
        <p className="text-[11px] leading-relaxed text-zinc-400">
          Keys are sent only to this local server and stored in its protected credential file, never
          in browser storage or settings. Saving a key does not grant cloud consent. Key values
          cannot be read back.
        </p>
        {error && <ErrorBanner message={error} retry={refresh} />}
        {loading && (
          <p role="status" className="text-xs text-zinc-500">
            Checking configured providers…
          </p>
        )}
        {(['openai', 'gemini', 'groq'] as const).map((provider) => (
          <ProviderKey
            key={provider}
            provider={provider}
            integrations={integrations}
            onSaved={setIntegrations}
          />
        ))}
        <ChatGPTConnection />
      </div>
    </Card>
  );
}
function ProviderKey({
  provider,
  integrations,
  onSaved,
}: {
  provider: CloudProvider;
  integrations: Integrations | null;
  onSaved: (value: Integrations) => void;
}) {
  const [key, setKey] = useState('');
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const status = integrations?.[provider];
  const save = async (remove = false) => {
    const value = remove ? '' : key.trim();
    setKey(''); // Clear even on network failure; never retain or echo a submitted credential.
    setPending(true);
    setMessage(null);
    setFailed(false);
    try {
      onSaved(await api.saveIntegration(provider, value));
      setMessage(
        remove
          ? 'Local key removed. An environment key, if present, still takes precedence.'
          : 'Key saved on the local server.',
      );
    } catch {
      // Do not display a response body that could accidentally echo a secret.
      setFailed(true);
      setMessage('Could not update the key. Check the local server and enter it again.');
    } finally {
      setPending(false);
    }
  };
  return (
    <div className="rounded-md border border-border p-4">
      <div className="mb-3 flex flex-wrap justify-between gap-2">
        <Label htmlFor={`${provider}-key`} className="mb-0 capitalize">
          {provider} API key
        </Label>
        <Badge variant={status?.configured ? 'lime' : 'neutral'}>
          {status
            ? status.configured
              ? `Configured · ${status.source}`
              : 'Not configured'
            : 'Status unavailable'}
        </Badge>
      </div>
      <div className="flex flex-wrap gap-2">
        <Input
          id={`${provider}-key`}
          type="password"
          autoComplete="off"
          spellCheck={false}
          value={key}
          disabled={pending}
          onChange={(e) => setKey(e.target.value)}
          className="min-w-40 flex-1"
          placeholder="Enter a new key"
        />
        <Button
          type="button"
          variant="secondary"
          disabled={!key.trim() || pending}
          onClick={() => {
            void save();
          }}
        >
          Save {provider} key
        </Button>
        <Button
          type="button"
          variant="ghost"
          disabled={pending || !status}
          onClick={() => {
            void save(true);
          }}
        >
          Remove {provider} local key
        </Button>
      </div>
      {message && (
        <p role={failed ? 'alert' : 'status'} className="mt-2 text-[11px] text-zinc-400">
          {message}
        </p>
      )}
    </div>
  );
}
