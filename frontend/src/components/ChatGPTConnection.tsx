import { useState } from 'react';
import { api } from '../services/api';
import { useChatGPT } from '../hooks/useChatGPT';
import { Badge, Button, Label, Select } from './ui';
import { ErrorBanner } from './shared';
import { planUsageUrl } from './UsagePanel';

export function ChatGPTConnection() {
  const { status, error, setStatus, refresh } = useChatGPT();
  const [pending, setPending] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [loginUrl, setLoginUrl] = useState<string | null>(null);
  const connect = async (accountId?: string) => {
    // Open during the click, before awaiting localhost, to avoid popup blocking.
    const popup = window.open('about:blank', '_blank');
    if (popup) popup.opener = null;
    setPending(true);
    setActionError(null);
    setLoginUrl(null);
    try {
      const login = await api.connectChatGPT(accountId);
      const url = new URL(login.authorization_url);
      if (
        url.protocol !== 'https:' ||
        url.origin !== 'https://auth.openai.com' ||
        url.pathname !== '/api/accounts/authorize' ||
        !!url.hash ||
        url.username ||
        url.password
      )
        throw new Error('Unexpected sign-in host');
      if (popup) popup.location.replace(url.href);
      else setLoginUrl(url.href);
      // The server owns verification. Never mark the account connected here.
      setStatus({
        ...(status ?? {
          connected: false,
          plan_enabled: false,
          active_account_id: null,
          accounts: [],
          error: null,
        }),
        pending: true,
        expires_at: login.expires_at,
      });
      await refresh();
    } catch {
      popup?.close();
      setActionError('Could not start official ChatGPT sign-in. Retry or check the local server.');
    } finally {
      setPending(false);
    }
  };
  const update = async (operation: () => ReturnType<typeof api.chatgptStatus>) => {
    setPending(true);
    setActionError(null);
    setLoginUrl(null);
    try {
      setStatus(await operation());
    } catch {
      setActionError('Could not update the ChatGPT account. Refresh status and try again.');
    } finally {
      setPending(false);
    }
  };
  return (
    <section
      aria-label="ChatGPT connection"
      className="space-y-3 rounded-md border border-border p-4"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-xs font-medium">ChatGPT plan</h3>
        <Badge variant={status?.connected && status.plan_enabled ? 'lime' : 'neutral'}>
          {status?.pending
            ? 'Sign-in pending'
            : status?.connected
              ? status.plan_enabled
                ? 'Connected · plan enabled'
                : 'Connected · plan not enabled'
              : status
                ? 'Not connected'
                : 'Status unavailable'}
        </Badge>
      </div>
      <p className="text-[11px] leading-relaxed text-zinc-400">
        Official sign-in preview: account eligibility and supported models vary. This uses your
        ChatGPT plan, not an API-key balance. Choose ChatGPT plan as the OpenAI mode in AI review
        settings; there is no automatic API-key fallback. Access and refresh tokens stay on the
        local server.
      </p>
      <a
        href={planUsageUrl}
        target="_blank"
        rel="noreferrer"
        className="inline-block text-[11px] text-zinc-300 underline"
      >
        View ChatGPT usage and plan limits
      </a>
      {status && status.accounts.length > 0 && (
        <div>
          <Label htmlFor="chatgpt-account">ChatGPT account</Label>
          <Select
            id="chatgpt-account"
            value={status.active_account_id ?? undefined}
            placeholder="Choose an account"
            disabled={pending || status.pending}
            options={status.accounts.map((account) => ({
              value: account.id,
              label: `${account.label}${account.email ? ` · ${account.email}` : ''}`,
            }))}
            onValueChange={(id) => void update(() => api.selectChatGPT(id))}
          />
        </div>
      )}
      {(actionError || error) && <ErrorBanner message={actionError || error!} />}
      {status?.error && (
        <p role="status" className="text-xs text-amber-200">
          {status.error}
        </p>
      )}
      {status?.connected && !status.plan_enabled && (
        <p className="text-xs text-amber-200">
          This account does not currently have plan inference enabled. Check eligibility or choose
          another account. No API key will be used instead.
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        <Button
          type="button"
          size="sm"
          variant="secondary"
          disabled={pending || !!status?.pending}
          onClick={() => void connect()}
          data-oai-siwc-button=""
        >
          {status?.accounts.length ? 'Add ChatGPT account' : 'Continue with ChatGPT'}
        </Button>
        {status?.pending && (
          <Button
            type="button"
            size="sm"
            variant="ghost"
            disabled={pending}
            onClick={() => void update(() => api.cancelChatGPT())}
          >
            Cancel sign-in
          </Button>
        )}
        {status?.active_account_id && (
          <Button
            type="button"
            size="sm"
            variant="ghost"
            disabled={pending || status.pending}
            onClick={() => void connect(status.active_account_id!)}
          >
            Reconnect account
          </Button>
        )}
        {status?.active_account_id && (
          <Button
            type="button"
            size="sm"
            variant="ghost"
            disabled={pending}
            onClick={() => void update(() => api.disconnectChatGPT(status.active_account_id!))}
          >
            Sign out of ChatGPT
          </Button>
        )}
        <Button
          type="button"
          size="sm"
          variant="ghost"
          disabled={pending}
          onClick={() => void refresh()}
        >
          Refresh ChatGPT status
        </Button>
      </div>
      {status?.pending && (
        <p role="status" className="text-[11px] text-zinc-400">
          Finish sign-in in the opened browser window. Status checks stop when sign-in completes or
          expires.
        </p>
      )}
      {loginUrl && status?.pending && (
        <a
          href={loginUrl}
          target="_blank"
          rel="noreferrer"
          className="text-xs text-primary underline"
        >
          Popup blocked? Open official ChatGPT sign-in
        </a>
      )}
    </section>
  );
}
