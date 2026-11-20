// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { ChatGPTConnection } from './ChatGPTConnection';
import { ChatGPTModelPicker } from './ChatGPTModelPicker';
import { APIModelPicker } from './APIModelPicker';
import { TitleSuggestions } from './TitleSuggestions';
import { UsagePanel } from './UsagePanel';
import { reviewSetupProblem } from './ProjectEnhancements';
import { api } from '../services/api';
import { useChatGPTStatus } from '../hooks/useChatGPT';
import { useStudio } from '../stores/studio';
import {
  chatgptFixture,
  highlightFixture,
  integrationsFixture,
  jobFixture,
  settingsFixture,
  usageFixture,
} from '../test/fixtures';

const connected = {
  ...chatgptFixture,
  connected: true,
  plan_enabled: true,
  active_account_id: 'account-1',
  accounts: [{ id: 'account-1', label: 'Test account', email: null, active: true }],
};
const titled = {
  ...highlightFixture,
  transcript: [{ start: 0, end: 2, text: 'Authored transcript', words: [] }],
  ai_titles: ['A suggested title', 'Another title'],
};
const originalData = useStudio.getState().data;
beforeEach(() => {
  useChatGPTStatus.setState({ status: null, error: null });
  useStudio.setState({
    data: {
      ...originalData,
      settings: { ...settingsFixture, cloud_consent: true, ai_scoring_provider: 'openai' },
      jobs: [],
      highlights: [titled],
    },
  });
  vi.spyOn(api, 'chatgptStatus').mockResolvedValue(chatgptFixture);
  vi.spyOn(api, 'integrations').mockResolvedValue({
    ...integrationsFixture,
    openai: { configured: true, source: 'local' },
  });
  vi.spyOn(api, 'usage').mockResolvedValue(usageFixture);
  vi.spyOn(api, 'connectChatGPT').mockResolvedValue({
    authorization_url: 'https://auth.openai.com/api/accounts/authorize?state=test',
    expires_at: '2026-11-20T12:10:00Z',
  });
  vi.spyOn(api, 'chatgptModels').mockResolvedValue([
    { id: 'account-model', name: 'Account model' },
  ]);
  vi.spyOn(api, 'suggestTitles').mockResolvedValue({ ...jobFixture, kind: 'titles' });
  vi.spyOn(api, 'patchHighlight').mockImplementation(async (_id, patch) => ({
    ...titled,
    ...patch,
  }));
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  useChatGPTStatus.setState({ status: null, error: null });
  useStudio.setState({ data: originalData });
});

function router(children: React.ReactNode) {
  return (
    <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      {children}
    </MemoryRouter>
  );
}

describe('official ChatGPT connection', () => {
  it('does not start OAuth until clicked, validates the URL and never stores credentials in the browser', async () => {
    const popup = { opener: window, location: { replace: vi.fn() }, close: vi.fn() };
    const open = vi.spyOn(window, 'open').mockReturnValue(popup as unknown as Window);
    const storage = vi.spyOn(Storage.prototype, 'setItem');
    vi.mocked(api.chatgptStatus).mockResolvedValue({ ...chatgptFixture, pending: true });
    render(<ChatGPTConnection />);
    await waitFor(() => expect(api.chatgptStatus).toHaveBeenCalledOnce());
    expect(open).not.toHaveBeenCalled();
    expect(api.connectChatGPT).not.toHaveBeenCalled();
    act(() => useChatGPTStatus.getState().setStatus(chatgptFixture));
    fireEvent.click(screen.getByRole('button', { name: 'Continue with ChatGPT' }));
    await waitFor(() =>
      expect(popup.location.replace).toHaveBeenCalledWith(
        'https://auth.openai.com/api/accounts/authorize?state=test',
      ),
    );
    expect(open).toHaveBeenCalledWith('about:blank', '_blank');
    expect(popup.opener).toBeNull();
    expect(storage).not.toHaveBeenCalled();
    expect(api.patchHighlight).not.toHaveBeenCalled();
    expect(screen.getByText(/Access and refresh tokens stay on the local server/)).toBeTruthy();
  });
  it('offers a click-only link when popups are blocked even if the status refresh fails', async () => {
    vi.spyOn(window, 'open').mockReturnValue(null);
    render(<ChatGPTConnection />);
    await waitFor(() => expect(api.chatgptStatus).toHaveBeenCalledOnce());
    vi.mocked(api.chatgptStatus).mockRejectedValue(new Error('Network unavailable'));
    fireEvent.click(screen.getByRole('button', { name: 'Continue with ChatGPT' }));
    const link = await screen.findByRole('link', { name: /Popup blocked/ });
    expect(link.getAttribute('href')).toBe(
      'https://auth.openai.com/api/accounts/authorize?state=test',
    );
    expect(useChatGPTStatus.getState().status?.connected).toBe(false);
  });
  it.each([
    'https://evil.example/authorize',
    'javascript:alert(1)',
    'https://auth.openai.com:444/authorize',
  ])('rejects an unexpected authorization URL: %s', async (authorization_url) => {
    const popup = { opener: window, location: { replace: vi.fn() }, close: vi.fn() };
    vi.spyOn(window, 'open').mockReturnValue(popup as unknown as Window);
    vi.mocked(api.connectChatGPT).mockResolvedValue({ authorization_url, expires_at: '' });
    render(<ChatGPTConnection />);
    await waitFor(() => expect(api.chatgptStatus).toHaveBeenCalledOnce());
    fireEvent.click(screen.getByRole('button', { name: 'Continue with ChatGPT' }));
    expect((await screen.findByRole('alert')).textContent).toContain('Could not start');
    expect(popup.location.replace).not.toHaveBeenCalled();
    expect(popup.close).toHaveBeenCalledOnce();
  });
  it('cancels a pending sign-in without signing out the active account', async () => {
    vi.mocked(api.chatgptStatus).mockResolvedValue({ ...connected, pending: true });
    const cancel = vi.spyOn(api, 'cancelChatGPT').mockResolvedValue(connected);
    const disconnect = vi.spyOn(api, 'disconnectChatGPT');
    render(<ChatGPTConnection />);
    await screen.findByText('Sign-in pending');
    fireEvent.click(screen.getByRole('button', { name: 'Cancel sign-in' }));
    await waitFor(() => expect(cancel).toHaveBeenCalledOnce());
    expect(useChatGPTStatus.getState().status?.active_account_id).toBe('account-1');
    expect(disconnect).not.toHaveBeenCalled();
  });
  it('selects and signs out the active account without changing API-key settings', async () => {
    vi.mocked(api.chatgptStatus).mockResolvedValue(connected);
    vi.spyOn(api, 'selectChatGPT').mockResolvedValue(connected);
    vi.spyOn(api, 'disconnectChatGPT').mockResolvedValue(chatgptFixture);
    render(<ChatGPTConnection />);
    await screen.findByText('Connected · plan enabled');
    expect(screen.getByRole('combobox', { name: 'ChatGPT account' }).textContent).toContain(
      'Test account',
    );
    act(() =>
      useChatGPTStatus.getState().setStatus({
        ...connected,
        accounts: [
          ...connected.accounts,
          { id: 'account-2', label: 'Second account', email: null, active: false },
        ],
      }),
    );
    fireEvent.click(screen.getByRole('combobox', { name: 'ChatGPT account' }));
    fireEvent.click(screen.getByRole('option', { name: 'Second account' }));
    await waitFor(() => expect(api.selectChatGPT).toHaveBeenCalledWith('account-2'));
    await waitFor(() =>
      expect(
        (screen.getByRole('button', { name: 'Sign out of ChatGPT' }) as HTMLButtonElement).disabled,
      ).toBe(false),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Sign out of ChatGPT' }));
    await waitFor(() => expect(api.disconnectChatGPT).toHaveBeenCalledWith('account-1'));
    expect(useStudio.getState().data.settings?.openai_auth_mode).toBe('api_key');
  });
  it('loads only the account catalog on click and never substitutes an unavailable default', async () => {
    useChatGPTStatus.getState().setStatus(connected);
    const change = vi.fn();
    render(<ChatGPTModelPicker value="" disabled={false} onChange={change} />);
    expect(api.chatgptModels).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Load plan models' }));
    await screen.findByText(/gpt-6-luna is unavailable/);
    expect(change).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('combobox', { name: 'Review model' }));
    fireEvent.click(screen.getByRole('option', { name: 'Account model' }));
    expect(change).toHaveBeenCalledWith('account-model');
  });
  it('shows only current priced API defaults and warns about confirmed retired nano models', () => {
    const { rerender } = render(
      <APIModelPicker provider="openai" value="" disabled={false} onChange={() => {}} />,
    );
    expect(screen.getByRole('combobox').textContent).toContain('GPT-6 Luna');
    rerender(
      <APIModelPicker
        provider="openai"
        value="gpt-4.1-nano"
        disabled={false}
        onChange={() => {}}
      />,
    );
    expect(screen.getByRole('status').textContent).toContain('has shut down');
    rerender(
      <APIModelPicker provider="openai" value="gpt-5-nano" disabled={false} onChange={() => {}} />,
    );
    expect(screen.getByRole('status').textContent).toContain('deprecated');
  });
});

describe('manual titles and truthful usage', () => {
  it('queues one title job but changes the actual title only on explicit Apply', async () => {
    const applied = vi.fn();
    render(router(<TitleSuggestions highlight={titled} disabled={false} onApplied={applied} />));
    await waitFor(() =>
      expect(
        (screen.getByRole('button', { name: 'Suggest titles' }) as HTMLButtonElement).disabled,
      ).toBe(false),
    );
    expect(api.suggestTitles).not.toHaveBeenCalled();
    expect(api.patchHighlight).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Suggest titles' }));
    await waitFor(() => expect(api.suggestTitles).toHaveBeenCalledWith('highlight-1'));
    expect(api.patchHighlight).not.toHaveBeenCalled();
    expect(useStudio.getState().data.highlights[0].title).toBe(highlightFixture.title);
    act(() => useStudio.setState((state) => ({ data: { ...state.data, jobs: [] } })));
    fireEvent.click(screen.getByRole('button', { name: 'Apply title: A suggested title' }));
    await waitFor(() =>
      expect(api.patchHighlight).toHaveBeenCalledWith('highlight-1', {
        title: 'A suggested title',
      }),
    );
    expect(applied).toHaveBeenCalledWith('A suggested title');
  });
  it('dismisses a suggestion locally without changing the title and guards dirty drafts', () => {
    render(router(<TitleSuggestions highlight={titled} disabled onApplied={() => {}} />));
    expect(
      (screen.getByRole('button', { name: 'Apply title: A suggested title' }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: 'Dismiss title: A suggested title' }));
    expect(api.patchHighlight).not.toHaveBeenCalled();
    expect(screen.queryByText('A suggested title')).toBeNull();
  });
  it('blocks zero-budget API review but does not invent a USD budget for plan usage or fall back to keys', () => {
    const settings = {
      ...settingsFixture,
      ai_scoring_provider: 'openai' as const,
      cloud_consent: true,
      ai_daily_budget_usd: 0,
    };
    expect(reviewSetupProblem(settings, true)).toContain('zero-dollar');
    expect(
      reviewSetupProblem({ ...settings, openai_auth_mode: 'chatgpt' }, true, chatgptFixture),
    ).toContain('No API-key fallback');
    expect(
      reviewSetupProblem({ ...settings, openai_auth_mode: 'chatgpt' }, false, connected),
    ).toBeNull();
  });
  it('shows plan USD as not applicable, unknown tokens as unknown, and saved local limits without a balance claim', async () => {
    vi.mocked(api.usage).mockResolvedValue({
      ...usageFixture,
      request_count: 1,
      rows: [
        {
          provider: 'openai',
          auth_mode: 'chatgpt',
          model: 'account-model',
          operation: 'titles',
          requests: 1,
          cache_hits: 0,
          input_tokens: null,
          cached_input_tokens: null,
          output_tokens: 12,
          audio_seconds: null,
          estimated_cost_usd: null,
          reserved_cost_usd: 0,
          unknown_usage_requests: 0,
        },
      ],
    });
    render(<UsagePanel />);
    const table = await screen.findByRole('table');
    expect(within(table).getByText('$—, plan usage')).toBeTruthy();
    expect(within(table).getByText('Unknown / Unknown')).toBeTruthy();
    expect(within(table).getByText('12')).toBeTruthy();
    expect(within(table).getByText('Not applicable')).toBeTruthy();
    expect(screen.getByLabelText('Saved cloud limits').textContent).toContain('20 cloud requests');
    expect(
      screen.getByRole('link', { name: 'View ChatGPT plan limits' }).getAttribute('href'),
    ).toBe('https://chatgpt.com/settings/usage');
    expect(screen.getByText(/not a provider balance or invoice/)).toBeTruthy();
  });
  it('labels partial API totals and outstanding reservations when actual usage is missing', async () => {
    vi.mocked(api.usage).mockResolvedValue({
      ...usageFixture,
      request_count: 2,
      estimated_cost_usd: 0.001,
      reserved_cost_usd: 0.02,
      unknown_usage_requests: 1,
    });
    render(<UsagePanel />);
    expect(await screen.findByText(/Estimated API cost: \$0.0010 · partial/)).toBeTruthy();
    expect(screen.getByText(/Reserved API cost: \$0.0200/)).toBeTruthy();
    expect(screen.getByText(/Failed or interrupted API requests may still be billed/)).toBeTruthy();
  });
});
