"use client";

import clsx from "clsx";
import { Check, LoaderCircle, X } from "lucide-react";
import { useEffect, useRef, useState, type FormEvent } from "react";

type InlineNameFormVariant = "sidebar" | "tab";

type InlineNameFormProps = {
  label: string;
  placeholder: string;
  isBusy?: boolean;
  variant?: InlineNameFormVariant;
  className?: string;
  onCancel: () => void;
  onSubmit: (value: string) => void | boolean | Promise<void | boolean>;
};

export function InlineNameForm({
  label,
  placeholder,
  isBusy = false,
  variant = "sidebar",
  className,
  onCancel,
  onSubmit,
}: InlineNameFormProps) {
  const [draft, setDraft] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  const trimmedDraft = draft.trim();

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!trimmedDraft || isBusy) {
      return;
    }
    await onSubmit(trimmedDraft);
  }

  return (
    <form
      onSubmit={(event) => void handleSubmit(event)}
      className={clsx(
        "flex items-center gap-2 border bg-white",
        variant === "tab"
          ? "h-12 min-w-[220px] border-y-0 border-l-0 border-r-gray-100 px-3"
          : "rounded-2xl border-stone-200 px-3 py-2 shadow-sm",
        className
      )}
    >
      <input
        ref={inputRef}
        type="text"
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Escape" && !isBusy) {
            event.preventDefault();
            onCancel();
          }
        }}
        disabled={isBusy}
        aria-label={label}
        placeholder={placeholder}
        className={clsx(
          "min-w-0 flex-1 bg-transparent text-sm text-stone-950 outline-none placeholder:text-stone-400 disabled:text-stone-400",
          variant === "tab" ? "font-medium" : "font-semibold"
        )}
      />
      <button
        type="submit"
        disabled={!trimmedDraft || isBusy}
        title="确认"
        aria-label="确认"
        className="flex h-8 w-8 shrink-0 items-center justify-center rounded-xl bg-stone-950 text-white transition hover:bg-stone-800 disabled:cursor-not-allowed disabled:bg-stone-200 disabled:text-stone-400"
      >
        {isBusy ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
      </button>
      <button
        type="button"
        onClick={onCancel}
        disabled={isBusy}
        title="取消"
        aria-label="取消"
        className="flex h-8 w-8 shrink-0 items-center justify-center rounded-xl text-stone-400 transition hover:bg-stone-100 hover:text-stone-950 disabled:cursor-not-allowed disabled:text-stone-300"
      >
        <X className="h-4 w-4" />
      </button>
    </form>
  );
}
