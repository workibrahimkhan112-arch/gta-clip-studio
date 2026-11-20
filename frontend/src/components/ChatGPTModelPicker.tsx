import { useEffect, useState } from 'react';
import type { ChatGPTModel } from '../types/api';
import { api } from '../services/api';
import { useChatGPTStatus } from '../hooks/useChatGPT';
import { Button, Select } from './ui';

export function ChatGPTModelPicker({
  value,
  disabled,
  onChange,
}: {
  value: string;
  disabled: boolean;
  onChange: (value: string) => void;
}) {
  const status = useChatGPTStatus((state) => state.status);
  const [catalog, setCatalog] = useState<{ account: string | null; models: ChatGPTModel[] } | null>(
    null,
  );
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const account = status?.active_account_id ?? null;
  const models = catalog?.account === account ? catalog.models : null;
  const selected = value || 'gpt-6-luna';
  useEffect(() => {
    setCatalog(null);
    setError(null);
  }, [account]);
  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      setCatalog({ account, models: await api.chatgptModels() });
    } catch (error) {
      setError(error instanceof Error ? error.message : 'Could not load plan models.');
    } finally {
      setLoading(false);
    }
  };
  return (
    <div className="w-full space-y-2 sm:max-w-sm">
      <Select
        id="review-model"
        aria-label="Review model"
        value={selected}
        disabled={
          disabled ||
          loading ||
          !status?.connected ||
          !status.plan_enabled ||
          status.pending ||
          !models?.length
        }
        onValueChange={onChange}
        options={[
          ...(!models?.some((model) => model.id === selected)
            ? [
                {
                  value: selected,
                  label: `${selected} · not verified in account catalog`,
                  disabled: true,
                },
              ]
            : []),
          ...(models ?? []).map((model) => ({ value: model.id, label: model.name })),
        ]}
      />
      <Button
        type="button"
        size="sm"
        variant="ghost"
        disabled={
          disabled || loading || !status?.connected || !status.plan_enabled || status.pending
        }
        onClick={() => void load()}
      >
        {loading ? 'Loading models…' : 'Load plan models'}
      </Button>
      <p className="text-[11px] text-zinc-400">
        Only this account’s available models can be used. Loading the catalog does not run inference
        or change the saved model.
      </p>
      {!status?.connected && (
        <p className="text-[11px] text-amber-200">
          Connect an eligible account in Connections below.
        </p>
      )}
      {models && !models.some((model) => model.id === selected) && (
        <p role="status" className="text-[11px] text-amber-200">
          {selected} is unavailable for this account. Choose a listed model explicitly; no
          replacement or upgrade is automatic.
        </p>
      )}
      {models?.length === 0 && (
        <p role="status" className="text-[11px] text-amber-200">
          No plan models are available for this account.
        </p>
      )}
      {error && (
        <p role="alert" className="text-[11px] text-red-300">
          {error}
        </p>
      )}
    </div>
  );
}
