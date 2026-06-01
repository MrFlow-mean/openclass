"use client";

import type { Editor } from "@tiptap/react";
import { EditorContent } from "@tiptap/react";
import clsx from "clsx";
import { type CSSProperties, type RefObject } from "react";

import type { BoardDocument, DocumentPageSettings } from "@/types";

export function WordEditorPageCanvas({
  pageScrollRef,
  pageSettings,
  pageStyle,
  pageChromeStyle,
  titleStyle,
  contentStyle,
  document,
  readOnly,
  editor,
  currentPageNumberLabel,
  untitledLessonLabel,
  onDocumentChange,
}: {
  pageScrollRef: RefObject<HTMLDivElement | null>;
  pageSettings: DocumentPageSettings;
  pageStyle: CSSProperties;
  pageChromeStyle: CSSProperties;
  titleStyle: CSSProperties;
  contentStyle: CSSProperties;
  document: BoardDocument;
  readOnly: boolean;
  editor: Editor | null;
  currentPageNumberLabel: string;
  untitledLessonLabel: string;
  onDocumentChange: (document: BoardDocument) => void;
}) {
  return (
    <div
      ref={pageScrollRef}
      className="min-h-0 flex-1 overflow-auto bg-[radial-gradient(circle_at_top,#f7f5ef,transparent_28%),linear-gradient(180deg,#f3f0e7_0%,#eef2f8_100%)]"
    >
      <div className="mx-auto flex w-max min-w-full justify-center px-6 py-10 md:px-10">
        <div
          className={clsx(
            "word-editor__page word-editor__page--zoomable relative flex shrink-0 flex-col overflow-hidden",
            !pageSettings.page_border && "word-editor__page--borderless",
            pageSettings.background_style === "warm" && "word-editor__page--warm",
            pageSettings.background_style === "grid" && "word-editor__page--grid",
            pageSettings.columns === 2 && "word-editor__page--columns-2",
            pageSettings.line_numbers && "word-editor__page--line-numbers"
          )}
          style={pageStyle}
        >
          {pageSettings.watermark_text ? (
            <div className="word-editor__watermark pointer-events-none select-none">{pageSettings.watermark_text}</div>
          ) : null}
          {pageSettings.header_text ? (
            <div className="word-editor__chrome word-editor__chrome--header" style={pageChromeStyle}>
              <span>{pageSettings.header_text}</span>
            </div>
          ) : null}
          <div className="border-b border-[#ece4d9]" style={titleStyle}>
            <input
              value={document.title}
              disabled={readOnly}
              onChange={(event) => onDocumentChange({ ...document, title: event.target.value })}
              className="w-full border-0 bg-transparent text-[34px] font-semibold tracking-tight text-[#1a1a1a] outline-none placeholder:text-gray-300"
              placeholder={untitledLessonLabel}
            />
          </div>
          <div className="flex-1" style={contentStyle}>
            <EditorContent editor={editor} />
          </div>
          {pageSettings.footer_text || currentPageNumberLabel ? (
            <div className="word-editor__chrome word-editor__chrome--footer" style={pageChromeStyle}>
              <span>{pageSettings.footer_text}</span>
              <span>{currentPageNumberLabel}</span>
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}
