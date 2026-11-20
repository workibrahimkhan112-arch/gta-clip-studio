import { useEffect } from 'react';
import { create } from 'zustand';
import type { ChatGPTStatus } from '../types/api';
import { api } from '../services/api';

// Only public account status belongs in the browser. Credentials stay on the local server.
export const useChatGPTStatus = create<{
  status: ChatGPTStatus | null;
  error: string | null;
  setStatus: (status: ChatGPTStatus) => void;
}>((set) => ({ status: null, error: null, setStatus: (status) => set({ status, error: null }) }));
let request: Promise<void> | null = null;
export function refreshChatGPT(): Promise<void> {
  if (request) return request;
  const observed = useChatGPTStatus.getState().status;
  request = api
    .chatgptStatus()
    .then(
      (status) => {
        // An older poll must not undo a completed account selection or sign-out.
        if (useChatGPTStatus.getState().status === observed)
          useChatGPTStatus.getState().setStatus(status);
      },
      () => {
        if (useChatGPTStatus.getState().status === observed)
          useChatGPTStatus.setState({
            error: 'Could not load ChatGPT connection status. Retry on the local server.',
          });
      },
    )
    .finally(() => {
      request = null;
    });
  return request;
}
export function useChatGPT(enabled = true) {
  const state = useChatGPTStatus();
  useEffect(() => {
    if (enabled) void refreshChatGPT();
  }, [enabled]);
  useEffect(() => {
    if (!enabled || !state.status?.pending) return;
    const timer = window.setInterval(() => {
      void refreshChatGPT();
    }, 1500);
    return () => window.clearInterval(timer);
  }, [enabled, state.status?.pending]);
  return { ...state, refresh: refreshChatGPT };
}
