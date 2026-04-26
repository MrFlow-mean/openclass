import type { Editor as TiptapEditor } from "@tiptap/core";
import type { Mark, Node as ProseMirrorNode, Schema } from "@tiptap/pm/model";

const CJK_TEXT = /[\u3400-\u9fff]/;
const MATH_SIGNAL =
  /[\\_^=·*/∞→←≤≥≈≠±]|\b(?:lim|sin|cos|tan|ln|log|sqrt|exp)\b|\d+\s*\/\s*\d+|[A-Za-z]\s*\([^)]*\)|[A-Za-z]\s*\^\s*\{?[-+\w/]+\}?/;
const MATH_RUN = /[A-Za-z0-9\\_{}^()+\-−*/=·∞→←≤≥≈≠±<>|'\s.]+/g;
const DELIMITED_MATH = /\\\((.+?)\\\)|\$(?!\d+\$)([^$\n]+?)\$(?!\d)/g;
const TRAILING_SENTENCE_MARKS = /[\s.,，。；;:：]+$/;
const LEADING_SENTENCE_MARKS = /^[\s.,，。；;:：]+/;

type MathSegment = {
  start: number;
  end: number;
  latex: string;
};

function hasMathSignal(value: string) {
  return MATH_SIGNAL.test(value);
}

function normalizeLimitSubscript(value: string) {
  return value
    .replaceAll("→", "\\to ")
    .replaceAll("∞", "\\infty")
    .replaceAll("+\\infty", "+\\infty")
    .replaceAll("-\\infty", "-\\infty")
    .replace(/\s+/g, " ")
    .trim();
}

function normalizeLatex(value: string) {
  let latex = value.trim().replace(TRAILING_SENTENCE_MARKS, "").replace(LEADING_SENTENCE_MARKS, "");

  latex = latex
    .replaceAll("−", "-")
    .replaceAll("→", "\\to ")
    .replaceAll("←", "\\leftarrow ")
    .replaceAll("∞", "\\infty")
    .replaceAll("·", "\\cdot ")
    .replaceAll("≤", "\\le ")
    .replaceAll("≥", "\\ge ")
    .replaceAll("≈", "\\approx ")
    .replaceAll("≠", "\\ne ")
    .replaceAll("±", "\\pm ")
    .replace(/，/g, ",\\quad ")
    .replace(/\blim_\{([^}]+)\}/g, (_, subscript: string) => `\\lim_{${normalizeLimitSubscript(subscript)}}`)
    .replace(/\b(sin|cos|tan|ln|log|sqrt|exp)\b/g, "\\$1");

  latex = latex
    .replace(/([A-Za-z]'?\([^()]+\))\s*\/\s*([A-Za-z]'?\([^()]+\))/g, "\\frac{$1}{$2}")
    .replace(
      /(\\(?:sin|cos|tan|ln|log|sqrt|exp)\s+[A-Za-z0-9]+(?:\^\{?[-+\w/]+\}?)?)\s*\/\s*\(([^()]+)\)/g,
      "\\frac{$1}{$2}"
    )
    .replace(
      /(\\(?:sin|cos|tan|ln|log|sqrt|exp)\s+[A-Za-z0-9]+(?:\^\{?[-+\w/]+\}?)?)\s*\/\s*([A-Za-z0-9]+(?:\^\{?[-+\w/]+\}?)?)/g,
      "\\frac{$1}{$2}"
    )
    .replace(/\(([^()]+)\)\s*\/\s*\(([^()]+)\)/g, "\\frac{$1}{$2}")
    .replace(/\(([^()]+)\)\s*\/\s*([A-Za-z0-9\\]+(?:\^\{?[-+\w/]+\}?)?)/g, "\\frac{$1}{$2}");

  return latex.replace(/\s+/g, " ").trim();
}

function formulaOnlyLatex(text: string) {
  const compact = text.trim().replace(TRAILING_SENTENCE_MARKS, "");
  const delimited = compact.match(/^\\\[(.+?)\\\]$/) ?? compact.match(/^\\\((.+?)\\\)$/) ?? compact.match(/^\$\$?(.+?)\$\$?$/);

  if (delimited) {
    return normalizeLatex(delimited[1]);
  }

  if (!compact || CJK_TEXT.test(compact) || !hasMathSignal(compact)) {
    return null;
  }

  return normalizeLatex(compact);
}

function mathSegments(text: string): MathSegment[] {
  const segments: MathSegment[] = [];

  for (const match of text.matchAll(DELIMITED_MATH)) {
    const raw = match[0];
    const rawStart = match.index ?? 0;
    const latex = match[1] ?? match[2];

    if (!latex?.trim()) {
      continue;
    }

    segments.push({
      start: rawStart,
      end: rawStart + raw.length,
      latex: normalizeLatex(latex),
    });
  }

  for (const match of text.matchAll(MATH_RUN)) {
    const raw = match[0];
    const rawStart = match.index ?? 0;
    const leadingTrimmed = raw.replace(LEADING_SENTENCE_MARKS, "");
    const leadingOffset = raw.length - leadingTrimmed.length;
    const candidate = leadingTrimmed.replace(TRAILING_SENTENCE_MARKS, "");
    const trailingOffset = leadingTrimmed.length - candidate.length;

    if (!candidate || CJK_TEXT.test(candidate) || !hasMathSignal(candidate)) {
      continue;
    }

    const start = rawStart + leadingOffset;
    const end = rawStart + raw.length - trailingOffset;

    if (end <= start) {
      continue;
    }

    if (segments.some((segment) => start < segment.end && end > segment.start)) {
      continue;
    }

    segments.push({
      start,
      end,
      latex: normalizeLatex(candidate),
    });
  }

  return segments.sort((left, right) => left.start - right.start);
}

function nodesForTextWithMath(schema: Schema, text: string, marks: readonly Mark[]) {
  const segments = mathSegments(text);

  if (!segments.length) {
    return null;
  }

  const nodes: ProseMirrorNode[] = [];
  let cursor = 0;

  for (const segment of segments) {
    if (segment.start > cursor) {
      nodes.push(schema.text(text.slice(cursor, segment.start), marks));
    }
    nodes.push(schema.nodes.inlineMath.create({ latex: segment.latex }));
    cursor = segment.end;
  }

  if (cursor < text.length) {
    nodes.push(schema.text(text.slice(cursor), marks));
  }

  return nodes;
}

export function normalizeEditorMath(editor: TiptapEditor) {
  const { blockMath } = editor.schema.nodes;
  const { inlineMath } = editor.schema.nodes;

  if (!blockMath || !inlineMath) {
    return;
  }

  let tr = editor.state.tr;
  let changed = false;
  const blockReplacements: Array<{ pos: number; size: number; latex: string }> = [];

  tr.doc.descendants((node, pos, parent, index) => {
    if (node.type.name !== "paragraph" || parent?.type.name !== "doc") {
      return;
    }

    const latex = formulaOnlyLatex(node.textContent);
    if (!latex || !parent.canReplaceWith(index, index + 1, blockMath)) {
      return;
    }

    blockReplacements.push({ pos, size: node.nodeSize, latex });
  });

  for (const replacement of blockReplacements.reverse()) {
    tr = tr.replaceWith(
      replacement.pos,
      replacement.pos + replacement.size,
      blockMath.create({ latex: replacement.latex })
    );
    changed = true;
  }

  const inlineReplacements: Array<{ pos: number; size: number; nodes: ProseMirrorNode[] }> = [];

  tr.doc.descendants((node, pos, parent) => {
    if (!node.isText || !node.text || parent?.type.name === "codeBlock") {
      return;
    }

    const nodes = nodesForTextWithMath(editor.schema, node.text, node.marks);
    if (!nodes) {
      return;
    }

    inlineReplacements.push({ pos, size: node.nodeSize, nodes });
  });

  for (const replacement of inlineReplacements.reverse()) {
    tr = tr.replaceWith(replacement.pos, replacement.pos + replacement.size, replacement.nodes);
    changed = true;
  }

  if (!changed) {
    return;
  }

  tr.setMeta("addToHistory", false);
  tr.setMeta("preventUpdate", true);
  editor.view.dispatch(tr);
}

export const MATH_TEXT_SERIALIZERS = {
  inlineMath: ({ node }: { node: ProseMirrorNode }) => `$${node.attrs.latex ?? ""}$`,
  blockMath: ({ node }: { node: ProseMirrorNode }) => `$$\n${node.attrs.latex ?? ""}\n$$`,
};
