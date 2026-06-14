import type { Editor as TiptapEditor } from "@tiptap/core";
import type { Mark, Node as ProseMirrorNode, Schema } from "@tiptap/pm/model";

const CJK_TEXT = /[\u3400-\u9fff]/;
const STRONG_MATH_SIGNAL =
  /\\(?:begin|end|frac|sqrt|lim|sum|prod|int|sin|cos|tan|ln|log|exp|to|leftarrow|rightarrow|leftrightarrow|infty|cdot|times|div|leq?|geq?|approx|neq?|pm|in|notin|mid|subseteq?|supseteq?|cup|cap|mathbb|mathcal|mathfrak|mathbf|mathrm|operatorname|dots|cdots|ldots|vdots|partial|nabla|forall|exists|alpha|beta|gamma|delta|theta|lambda|mu|pi|sigma|phi|omega)\b|[_^]|[A-Za-z0-9)]\s*(?:[+\-−*/=<>≤≥≈≠±]|→|←)\s*[A-Za-z0-9(\\]|\d+\s*\/\s*\d+|\\[{}]|^\([^()\n]{1,80},[^()\n]{1,80}\)$|^[A-Za-z]{1,3}\s*\([A-Za-z0-9α-ωΑ-Ω\\_{}\[\]^()+\-−*/=·∞→←≤≥≈≠±<>|&:'\s.,]+\)$/;
const DELIMITED_MATH = /\\\[([\s\S]+?)\\\]|\\\((.+?)\\\)|\$\$([\s\S]+?)\$\$|\$(?!\d+\$)([^$\n]+?)\$(?!\d)/g;
const TRAILING_SENTENCE_MARKS = /[\s.,，。；;:：]+$/;
const LEADING_SENTENCE_MARKS = /^[\s.,，。；;:：]+/;
const LATIN_WORD = /[A-Za-z]+/g;
const NON_FORMULA_LETTER = /[\u00c0-\u024f\u3400-\u9fff]/;
const FORMULA_CHARS = /^[A-Za-z0-9α-ωΑ-Ω\\_{}\[\]^()+\-−*/=·∞→←≤≥≈≠±<>|&:'\s.,]+$/;
const LATEX_ENVIRONMENT = /\\(?:begin|end)\{[A-Za-z*]+\}/g;
const LATEX_TEXT_ARGUMENT = /\\(?:text|mathrm|operatorname)\{[^{}]*\}/g;
const LATEX_FUNCTIONS = new Set(["lim", "sin", "cos", "tan", "ln", "log", "sqrt", "exp"]);

type MathSegment = {
  start: number;
  end: number;
  latex: string;
};

function hasStrongMathSignal(value: string) {
  return STRONG_MATH_SIGNAL.test(value);
}

function latexValidationText(value: string) {
  return value.replace(LATEX_ENVIRONMENT, "").replace(LATEX_TEXT_ARGUMENT, "");
}

function withoutLatexCommands(value: string) {
  return latexValidationText(value).replace(/\\[A-Za-z]+/g, "");
}

function hasNonFormulaLetters(value: string) {
  return NON_FORMULA_LETTER.test(withoutLatexCommands(value));
}

function latinWordsAreFormulaLike(value: string) {
  for (const word of withoutLatexCommands(value).matchAll(LATIN_WORD)) {
    const token = word[0];
    if (token.length > 3 && !LATEX_FUNCTIONS.has(token)) {
      return false;
    }
  }
  return true;
}

function isLikelyDelimitedMath(value: string) {
  const compact = value.trim().replace(TRAILING_SENTENCE_MARKS, "").replace(LEADING_SENTENCE_MARKS, "");
  const validationText = latexValidationText(compact);
  if (
    !compact ||
    !validationText ||
    !FORMULA_CHARS.test(validationText) ||
    hasNonFormulaLetters(compact) ||
    !latinWordsAreFormulaLike(compact)
  ) {
    return false;
  }
  return hasStrongMathSignal(compact) || /^[A-Za-zα-ωΑ-Ω]$/.test(validationText);
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
  const bracketDelimited = compact.match(/^\\\[(.+?)\\\]$/) ?? compact.match(/^\\\((.+?)\\\)$/);

  if (bracketDelimited) {
    return normalizeLatex(bracketDelimited[1]);
  }

  const dollarDelimited = compact.match(/^\$\$?(.+?)\$\$?$/);
  if (dollarDelimited) {
    return isLikelyDelimitedMath(dollarDelimited[1]) ? normalizeLatex(dollarDelimited[1]) : null;
  }

  if (!compact || CJK_TEXT.test(compact) || !hasStrongMathSignal(compact)) {
    return null;
  }

  return isLikelyDelimitedMath(compact) ? normalizeLatex(compact) : null;
}

function mathSegments(text: string): MathSegment[] {
  const segments: MathSegment[] = [];

  for (const match of text.matchAll(DELIMITED_MATH)) {
    const raw = match[0];
    const rawStart = match.index ?? 0;
    const latex = match[1] ?? match[2] ?? match[3] ?? match[4];

    if (!latex?.trim()) {
      continue;
    }
    if (!isLikelyDelimitedMath(latex)) {
      continue;
    }

    segments.push({
      start: rawStart,
      end: rawStart + raw.length,
      latex: normalizeLatex(latex),
    });
  }

  return segments.sort((left, right) => left.start - right.start);
}

function topLevelBlockMathDelimiterReplacements(doc: ProseMirrorNode, blockMath: Schema["nodes"][string]) {
  const children: Array<{ node: ProseMirrorNode; offset: number; index: number }> = [];
  doc.forEach((node, offset, index) => {
    children.push({ node, offset, index });
  });

  const replacements: Array<{ pos: number; size: number; latex: string }> = [];
  let cursor = 0;
  while (cursor < children.length) {
    const openerText = children[cursor].node.type.name === "paragraph" ? children[cursor].node.textContent.trim() : "";
    const closerText = openerText === "\\[" ? "\\]" : openerText === "$$" ? "$$" : null;
    if (!closerText) {
      cursor += 1;
      continue;
    }

    const formulaParts: string[] = [];
    let closerIndex = -1;
    for (let index = cursor + 1; index < children.length; index += 1) {
      const current = children[index];
      const text = current.node.type.name === "paragraph" ? current.node.textContent.trim() : "";
      if (text === closerText) {
        closerIndex = index;
        break;
      }
      formulaParts.push(current.node.textContent);
    }

    const latex = normalizeLatex(formulaParts.join("\n"));
    if (
      closerIndex > cursor + 1 &&
      latex &&
      hasStrongMathSignal(latex) &&
      doc.canReplaceWith(children[cursor].index, children[closerIndex].index + 1, blockMath)
    ) {
      const start = children[cursor].offset;
      const end = children[closerIndex].offset + children[closerIndex].node.nodeSize;
      replacements.push({ pos: start, size: end - start, latex });
      cursor = closerIndex + 1;
      continue;
    }

    cursor += 1;
  }

  return replacements;
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

  const delimitedBlockReplacements = topLevelBlockMathDelimiterReplacements(tr.doc, blockMath);
  for (const replacement of delimitedBlockReplacements.reverse()) {
    tr = tr.replaceWith(
      replacement.pos,
      replacement.pos + replacement.size,
      blockMath.create({ latex: replacement.latex })
    );
    changed = true;
  }

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
