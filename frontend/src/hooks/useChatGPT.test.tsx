// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, renderHook } from '@testing-library/react';
import { refreshChatGPT, useChatGPT, useChatGPTStatus } from './useChatGPT';
import { api } from '../services/api';
import { chatgptFixture } from '../test/fixtures';
import type { ChatGPTStatus } from '../types/api';

beforeEach(() => {
  vi.useFakeTimers();
  useChatGPTStatus.setState({ status: null, error: null });
  vi.spyOn(api, 'chatgptStatus').mockResolvedValue(chatgptFixture);
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
  useChatGPTStatus.setState({ status: null, error: null });
});

describe('public ChatGPT status polling', () => {
  it('polls only while pending and stops after completion or unmount', async () => {
    vi.mocked(api.chatgptStatus).mockResolvedValue({ ...chatgptFixture, pending: true });
    const { unmount } = renderHook(() => useChatGPT());
    await act(async () => {});
    expect(api.chatgptStatus).toHaveBeenCalledTimes(1);
    await act(() => vi.advanceTimersByTimeAsync(1500));
    expect(api.chatgptStatus).toHaveBeenCalledTimes(2);
    vi.mocked(api.chatgptStatus).mockResolvedValue(chatgptFixture);
    await act(() => vi.advanceTimersByTimeAsync(1500));
    expect(api.chatgptStatus).toHaveBeenCalledTimes(3);
    await act(() => vi.advanceTimersByTimeAsync(6000));
    expect(api.chatgptStatus).toHaveBeenCalledTimes(3);
    unmount();
    await act(() => vi.advanceTimersByTimeAsync(6000));
    expect(api.chatgptStatus).toHaveBeenCalledTimes(3);
  });
  it('does not make even a status request when disabled', async () => {
    renderHook(() => useChatGPT(false));
    await act(() => vi.advanceTimersByTimeAsync(6000));
    expect(api.chatgptStatus).not.toHaveBeenCalled();
  });
  it('does not overwrite a later sign-out or account selection with an older poll', async () => {
    let resolve!: (status: ChatGPTStatus) => void;
    vi.mocked(api.chatgptStatus).mockReturnValue(
      new Promise((done) => {
        resolve = done;
      }),
    );
    const request = refreshChatGPT();
    const disconnected = { ...chatgptFixture };
    useChatGPTStatus.getState().setStatus(disconnected);
    resolve({
      ...chatgptFixture,
      connected: true,
      plan_enabled: true,
      active_account_id: 'old-account',
    });
    await request;
    expect(useChatGPTStatus.getState().status).toBe(disconnected);
  });
});
