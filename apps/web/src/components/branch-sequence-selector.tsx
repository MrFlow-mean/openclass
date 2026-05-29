"use client";

import clsx from "clsx";
import { useEffect, useRef, useState } from "react";
import { BookOpen, Clock3, GitBranch } from "lucide-react";

import { useInterfaceLanguage } from "@/contexts/interface-language-context";

export type BranchSequenceOption = {
  order: number;
  branchName: string;
  documentTitle: string;
  documentOverview: string;
  latestLabel: string;
  latestMessage: string;
  updatedAt: string;
};

function formatBranchDate(value: string, locale: string) {
  return new Date(value).toLocaleString(locale, {
    hour12: false,
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function BranchSequenceSelector({
  branches,
  currentBranchName,
  onSelectBranch,
}: {
  branches: BranchSequenceOption[];
  currentBranchName: string;
  onSelectBranch: (branchName: string) => void;
}) {
  const { texts: txt, intlLocale } = useInterfaceLanguage();
  const s = txt.studio.branchSequence;
  const rootRef = useRef<HTMLDivElement | null>(null);
  const [openBranchName, setOpenBranchName] = useState<string | null>(null);
  const openBranch = branches.find((branch) => branch.branchName === openBranchName) ?? null;

  useEffect(() => {
    if (!openBranchName) {
      return;
    }

    function handlePointerDown(event: PointerEvent) {
      const target = event.target;
      if (target instanceof Node && rootRef.current?.contains(target)) {
        return;
      }
      setOpenBranchName(null);
    }

    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setOpenBranchName(null);
      }
    }

    document.addEventListener("pointerdown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("pointerdown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [openBranchName]);

  if (branches.length < 2) {
    return null;
  }

  return (
    <div ref={rootRef} className="mt-2 inline-flex max-w-full items-center gap-1 rounded-lg border border-gray-200 bg-white p-1 shadow-sm">
      <GitBranch className="ml-1 h-3 w-3 shrink-0 text-gray-300" />
      <div className="flex min-w-0 flex-wrap gap-1">
        {branches.map((branch) => {
          const isCurrent = branch.branchName === currentBranchName;
          return (
            <div key={branch.branchName} className="relative">
              <button
                type="button"
                onClick={() => {
                  if (!isCurrent) {
                    onSelectBranch(branch.branchName);
                  }
                }}
                onContextMenu={(event) => {
                  event.preventDefault();
                  event.stopPropagation();
                  setOpenBranchName((current) => (current === branch.branchName ? null : branch.branchName));
                }}
                className={clsx(
                  "flex h-6 min-w-6 items-center justify-center rounded-md px-1.5 text-[10px] font-bold transition",
                  isCurrent
                    ? "bg-black text-white"
                    : "text-blue-600 hover:bg-blue-50 hover:text-blue-700"
                )}
                aria-current={isCurrent ? "true" : undefined}
                aria-label={s.switchAria(branch.order, branch.branchName)}
                title={s.title(branch.branchName)}
              >
                {branch.order}
              </button>
              {openBranch?.branchName === branch.branchName ? (
                <div className="absolute left-0 top-8 z-50 w-72 rounded-xl border border-gray-200 bg-white p-4 text-left shadow-xl">
                  <div className="flex items-center gap-2 text-[10px] font-bold uppercase tracking-[0.16em] text-gray-400">
                    <GitBranch className="h-3.5 w-3.5" />
                    <span>{openBranch.branchName}</span>
                  </div>
                  <div className="mt-3 flex items-start gap-2">
                    <BookOpen className="mt-0.5 h-4 w-4 shrink-0 text-gray-400" />
                    <div className="min-w-0">
                      <p className="break-words text-xs font-semibold text-gray-950">{openBranch.documentTitle}</p>
                      <p className="mt-2 whitespace-pre-wrap break-words text-[11px] leading-5 text-gray-600">
                        {openBranch.documentOverview}
                      </p>
                    </div>
                  </div>
                  <div className="mt-3 border-t border-gray-100 pt-3">
                    <p className="text-[11px] font-semibold text-gray-800">{openBranch.latestLabel}</p>
                    <p className="mt-1 line-clamp-2 text-[11px] leading-5 text-gray-500">
                      {openBranch.latestMessage}
                    </p>
                    <p className="mt-2 flex items-center gap-1 text-[10px] text-gray-400">
                      <Clock3 className="h-3 w-3" />
                      {formatBranchDate(openBranch.updatedAt, intlLocale)}
                    </p>
                  </div>
                </div>
              ) : null}
            </div>
          );
        })}
      </div>
    </div>
  );
}
