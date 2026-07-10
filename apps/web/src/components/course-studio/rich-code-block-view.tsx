"use client";

import { NodeViewContent, NodeViewWrapper, type NodeViewProps } from "@tiptap/react";
import clsx from "clsx";
import { Check, Code2, Copy } from "lucide-react";
import { useMemo, useState } from "react";

import { codeLanguageLabel, highlightCode } from "@/lib/code-highlight";

export function RichCodeBlockView({ node, editor }: NodeViewProps) {
  const language = typeof node.attrs.language === "string" ? node.attrs.language : null;
  const label = codeLanguageLabel(language);
  const code = node.textContent;
  const isEditable = editor.isEditable;
  const [copied, setCopied] = useState(false);
  const highlighted = useMemo(() => highlightCode(code, language), [code, language]);

  async function handleCopy() {
    if (!code.trim()) {
      return;
    }
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch {
      setCopied(false);
    }
  }

  return (
    <NodeViewWrapper
      as="div"
      className="rich-code-block"
      data-language={language || "plaintext"}
      contentEditable={false}
    >
      <div className="rich-code-block__header">
        <div className="rich-code-block__title">
          <Code2 className="h-4 w-4" aria-hidden="true" />
          <span>{label}</span>
        </div>
        <button
          type="button"
          className="rich-code-block__copy"
          aria-label={copied ? "已复制" : "复制代码"}
          onClick={() => void handleCopy()}
        >
          {copied ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
        </button>
      </div>
      {isEditable ? (
        <pre className="rich-code-block__pre">
          <code className={clsx("rich-code-block__code", language ? `language-${language}` : null)}>
            <NodeViewContent />
          </code>
        </pre>
      ) : (
        <pre className="rich-code-block__pre">
          <code
            className={clsx("rich-code-block__code hljs", language ? `language-${language}` : null)}
            dangerouslySetInnerHTML={{ __html: highlighted }}
          />
        </pre>
      )}
    </NodeViewWrapper>
  );
}
