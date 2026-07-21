import clsx from "clsx";
import {
  Download,
  GitBranch,
  Pause,
  Play,
  SkipBack,
  SkipForward,
  Upload,
  X,
} from "lucide-react";
import { useRef } from "react";

import type { LessonPlaybackStep } from "@/hooks/course-studio/use-lesson-package";

type LessonPackageControlsProps = {
  currentStep: LessonPlaybackStep | null;
  stepIndex: number;
  stepCount: number;
  isPlaying: boolean;
  isPlaybackActive: boolean;
  speed: number;
  operation: "export" | "import" | null;
  onSpeedChange: (speed: number) => void;
  onPlayToggle: () => void | Promise<void>;
  onPrevious: () => void | Promise<void>;
  onNext: () => void | Promise<void>;
  onExit: () => void;
  onFork: () => void | Promise<void>;
  onExport: () => void | Promise<void>;
  onImport: (file: File) => void | Promise<void>;
};

export function LessonPackageControls({
  currentStep,
  stepIndex,
  stepCount,
  isPlaying,
  isPlaybackActive,
  speed,
  operation,
  onSpeedChange,
  onPlayToggle,
  onPrevious,
  onNext,
  onExit,
  onFork,
  onExport,
  onImport,
}: LessonPackageControlsProps) {
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const operationActive = operation !== null;

  return (
    <section className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-[10px] font-bold uppercase tracking-widest text-gray-400">RIDOC 课程包</p>
          <p className="mt-1 text-xs leading-5 text-gray-600">完整课节历史、证据、板书资源与分支图</p>
        </div>
        <div className="flex shrink-0 gap-1.5">
          <button
            type="button"
            disabled={operationActive}
            onClick={() => void onExport()}
            className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-gray-200 px-2.5 text-[10px] font-semibold text-gray-600 hover:border-gray-300 hover:text-black disabled:cursor-not-allowed disabled:opacity-50"
          >
            <Download className="h-3.5 w-3.5" />
            {operation === "export" ? "导出中" : "导出"}
          </button>
          <button
            type="button"
            disabled={operationActive}
            onClick={() => fileInputRef.current?.click()}
            className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-gray-200 px-2.5 text-[10px] font-semibold text-gray-600 hover:border-gray-300 hover:text-black disabled:cursor-not-allowed disabled:opacity-50"
          >
            <Upload className="h-3.5 w-3.5" />
            {operation === "import" ? "导入中" : "导入"}
          </button>
          <input
            ref={fileInputRef}
            type="file"
            accept=".ridoc,application/vnd.openclass.ridoc+zip"
            className="hidden"
            onChange={(event) => {
              const file = event.target.files?.[0];
              event.target.value = "";
              if (file) {
                void onImport(file);
              }
            }}
          />
        </div>
      </div>

      <div className="mt-4 border-t border-gray-100 pt-4">
        <div className="flex items-center gap-1.5">
          <button
            type="button"
            aria-label={isPlaying ? "暂停播放" : "播放课程"}
            disabled={!stepCount}
            onClick={() => void onPlayToggle()}
            className="inline-flex h-8 w-8 items-center justify-center rounded-lg bg-black text-white disabled:cursor-not-allowed disabled:opacity-30"
          >
            {isPlaying ? <Pause className="h-3.5 w-3.5" /> : <Play className="h-3.5 w-3.5" />}
          </button>
          <button
            type="button"
            aria-label="上一步"
            disabled={!stepCount || (isPlaybackActive && stepIndex <= 0)}
            onClick={() => void onPrevious()}
            className="inline-flex h-8 w-8 items-center justify-center rounded-lg border border-gray-200 text-gray-600 disabled:cursor-not-allowed disabled:opacity-30"
          >
            <SkipBack className="h-3.5 w-3.5" />
          </button>
          <button
            type="button"
            aria-label="下一步"
            disabled={!stepCount || (isPlaybackActive && stepIndex >= stepCount - 1)}
            onClick={() => void onNext()}
            className="inline-flex h-8 w-8 items-center justify-center rounded-lg border border-gray-200 text-gray-600 disabled:cursor-not-allowed disabled:opacity-30"
          >
            <SkipForward className="h-3.5 w-3.5" />
          </button>
          <select
            aria-label="播放速度"
            value={speed}
            onChange={(event) => onSpeedChange(Number(event.target.value))}
            className="h-8 rounded-lg border border-gray-200 bg-white px-2 text-[10px] font-semibold text-gray-600 outline-none"
          >
            <option value={0.5}>0.5×</option>
            <option value={1}>1×</option>
            <option value={2}>2×</option>
          </select>
          <span className="ml-auto text-[10px] font-mono text-gray-400">
            {isPlaybackActive ? stepIndex + 1 : 0}/{stepCount}
          </span>
        </div>

        <div
          className={clsx(
            "mt-3 min-h-[72px] rounded-lg border px-3 py-2.5",
            currentStep ? "border-blue-100 bg-blue-50/70" : "border-gray-100 bg-gray-50"
          )}
        >
          <p className="text-[10px] font-bold uppercase tracking-wider text-gray-500">
            {currentStep?.title ?? "语义步骤播放"}
          </p>
          <p className="mt-1 line-clamp-2 text-[11px] leading-5 text-gray-700">
            {currentStep?.detail ?? "播放时不会调用模型，也不会修改课程历史。"}
          </p>
        </div>

        {isPlaybackActive ? (
          <div className="mt-3 flex gap-2">
            <button
              type="button"
              onClick={() => void onFork()}
              className="inline-flex flex-1 items-center justify-center gap-1.5 rounded-lg border border-blue-200 bg-blue-50 px-3 py-2 text-[10px] font-semibold text-blue-700 hover:bg-blue-100"
            >
              <GitBranch className="h-3.5 w-3.5" />
              从这里分叉
            </button>
            <button
              type="button"
              onClick={onExit}
              className="inline-flex flex-1 items-center justify-center gap-1.5 rounded-lg border border-gray-200 px-3 py-2 text-[10px] font-semibold text-gray-600 hover:text-black"
            >
              <X className="h-3.5 w-3.5" />
              退出并继续学习
            </button>
          </div>
        ) : null}
      </div>
    </section>
  );
}
