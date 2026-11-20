import { create } from 'zustand';
interface Toast {
  id: number;
  title: string;
  message?: string;
  variant: 'success' | 'error' | 'info';
}
interface UIState {
  importOpen: boolean;
  setImportOpen: (open: boolean) => void;
  toasts: Toast[];
  toast: (title: string, message?: string, variant?: Toast['variant']) => void;
  dismiss: (id: number) => void;
}
let nextId = 0;
export const useUI = create<UIState>((set, get) => ({
  importOpen: false,
  setImportOpen: (importOpen) => set({ importOpen }),
  toasts: [],
  toast: (title, message, variant = 'success') => {
    const id = ++nextId;
    set((s) => ({ toasts: [...s.toasts.slice(-3), { id, title, message, variant }] }));
    setTimeout(() => get().dismiss(id), variant === 'error' ? 10000 : 6000);
  },
  dismiss: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
}));
