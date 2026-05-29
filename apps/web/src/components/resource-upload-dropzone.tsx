"use client";

import clsx from "clsx";
import { LoaderCircle, Upload } from "lucide-react";
import { useCallback, useState, type DragEvent } from "react";

import { useInterfaceLanguage } from "@/contexts/interface-language-context";

type ResourceUploadDropzoneProps = {
  disabled?: boolean;
  uploading?: boolean;
  onUpload: (file: File | null) => void;
};

function dragIncludesFiles(event: DragEvent<HTMLElement>) {
  return Array.from(event.dataTransfer.types).includes("Files");
}

export function ResourceUploadDropzone({ disabled = false, uploading = false, onUpload }: ResourceUploadDropzoneProps) {
  const { texts: txt } = useInterfaceLanguage();
  const u = txt.studio.upload;
  const [isDragActive, setIsDragActive] = useState(false);

  const handleDragEnter = useCallback(
    (event: DragEvent<HTMLLabelElement>) => {
      if (!dragIncludesFiles(event)) {
        return;
      }
      event.preventDefault();
      event.stopPropagation();
      if (!disabled) {
        setIsDragActive(true);
      }
    },
    [disabled]
  );

  const handleDragOver = useCallback(
    (event: DragEvent<HTMLLabelElement>) => {
      if (!dragIncludesFiles(event)) {
        return;
      }
      event.preventDefault();
      event.stopPropagation();
      event.dataTransfer.dropEffect = disabled ? "none" : "copy";
      if (!disabled) {
        setIsDragActive(true);
      }
    },
    [disabled]
  );

  const handleDragLeave = useCallback((event: DragEvent<HTMLLabelElement>) => {
    const relatedTarget = event.relatedTarget;
    if (relatedTarget instanceof Node && event.currentTarget.contains(relatedTarget)) {
      return;
    }
    setIsDragActive(false);
  }, []);

  const handleDrop = useCallback(
    (event: DragEvent<HTMLLabelElement>) => {
      if (!dragIncludesFiles(event)) {
        return;
      }
      event.preventDefault();
      event.stopPropagation();
      setIsDragActive(false);
      if (disabled) {
        return;
      }
      onUpload(event.dataTransfer.files.item(0));
    },
    [disabled, onUpload]
  );

  return (
    <label
      aria-busy={uploading}
      aria-disabled={disabled}
      onDragEnter={handleDragEnter}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
      onClick={(event) => {
        if (disabled) {
          event.preventDefault();
        }
      }}
      className={clsx(
        "mt-4 block rounded-xl border border-dashed bg-white px-4 py-5 text-center text-sm font-semibold transition",
        disabled ? "cursor-not-allowed border-gray-200 text-gray-300" : "cursor-pointer border-gray-300 text-gray-500 hover:border-gray-400",
        isDragActive && !disabled && "border-blue-400 bg-blue-50 text-blue-700 ring-2 ring-blue-100"
      )}
    >
      <span className="inline-flex items-center justify-center gap-2">
        {uploading ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <Upload className="h-4 w-4" />}
        {isDragActive && !disabled ? u.drop : uploading ? u.uploading : u.idle}
      </span>
      <input
        type="file"
        className="hidden"
        disabled={disabled}
        onChange={(event) => {
          const file = event.target.files?.[0] ?? null;
          event.currentTarget.value = "";
          onUpload(file);
        }}
      />
    </label>
  );
}
