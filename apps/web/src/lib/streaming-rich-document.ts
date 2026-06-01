const MARKDOWN_HEADING_RE = /^(#{1,3})\s+(.+)$/;
const MARKDOWN_BULLET_RE = /^[-*]\s+(.+)$/;
const MARKDOWN_ORDERED_RE = /^\d+[.、]\s+(.+)$/;
const MARKDOWN_TABLE_SEPARATOR_RE = /^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$/;

function escapeHtml(value: string) {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function canOpenInlineMark(value: string, index: number, markerLength: number) {
  const previous = index === 0 ? "" : value[index - 1];
  const next = value[index + markerLength] ?? "";
  if (!next || /\s/.test(next)) {
    return false;
  }
  return !previous || /[\s([{"'，。；;:：、]/.test(previous);
}

function renderInlineMarkdown(value: string) {
  const parts: string[] = [];
  let index = 0;

  while (index < value.length) {
    if (value[index] === "`") {
      const closeIndex = value.indexOf("`", index + 1);
      if (closeIndex > index + 1) {
        parts.push(`<code>${escapeHtml(value.slice(index + 1, closeIndex))}</code>`);
        index = closeIndex + 1;
        continue;
      }
    }

    if (value.startsWith("**", index) && canOpenInlineMark(value, index, 2)) {
      const closeIndex = value.indexOf("**", index + 2);
      const contentEnd = closeIndex > index + 2 ? closeIndex : value.length;
      parts.push(`<strong>${escapeHtml(value.slice(index + 2, contentEnd))}</strong>`);
      index = closeIndex > index + 2 ? closeIndex + 2 : value.length;
      continue;
    }

    if (value[index] === "*" && value[index + 1] !== "*" && canOpenInlineMark(value, index, 1)) {
      const closeIndex = value.indexOf("*", index + 1);
      if (closeIndex > index + 1 && !/\s/.test(value[closeIndex - 1] ?? "")) {
        parts.push(`<em>${escapeHtml(value.slice(index + 1, closeIndex))}</em>`);
        index = closeIndex + 1;
        continue;
      }
    }

    parts.push(escapeHtml(value[index]));
    index += 1;
  }

  return parts.join("");
}

function splitMarkdownTableRow(value: string) {
  return value
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((cell) => cell.trim());
}

function isMarkdownTable(lines: string[], index: number) {
  if (index + 1 >= lines.length) {
    return false;
  }
  const header = lines[index].trim();
  const separator = lines[index + 1].trim();
  return header.includes("|") && MARKDOWN_TABLE_SEPARATOR_RE.test(separator);
}

function renderTable(rows: string[][]) {
  return `<table><tbody>${rows
    .map((row, rowIndex) => {
      const tag = rowIndex === 0 ? "th" : "td";
      return `<tr>${row.map((cell) => `<${tag}>${renderInlineMarkdown(cell)}</${tag}>`).join("")}</tr>`;
    })
    .join("")}</tbody></table>`;
}

function lineStartsBlock(line: string) {
  const trimmed = line.trim();
  return (
    !trimmed ||
    MARKDOWN_HEADING_RE.test(trimmed) ||
    MARKDOWN_BULLET_RE.test(trimmed) ||
    MARKDOWN_ORDERED_RE.test(trimmed) ||
    trimmed.startsWith(">")
  );
}

export function streamingMarkdownToHtml(contentText: string) {
  const lines = contentText.split(/\r?\n/);
  const parts: string[] = [];
  let index = 0;

  while (index < lines.length) {
    const line = lines[index].trim();
    if (!line) {
      index += 1;
      continue;
    }

    if (isMarkdownTable(lines, index)) {
      const rows = [splitMarkdownTableRow(lines[index])];
      index += 2;
      while (index < lines.length && lines[index].includes("|") && lines[index].trim()) {
        rows.push(splitMarkdownTableRow(lines[index]));
        index += 1;
      }
      parts.push(renderTable(rows));
      continue;
    }

    const headingMatch = MARKDOWN_HEADING_RE.exec(line);
    if (headingMatch) {
      const level = Math.min(headingMatch[1].length, 3);
      parts.push(`<h${level}>${renderInlineMarkdown(headingMatch[2].trim())}</h${level}>`);
      index += 1;
      continue;
    }

    const bulletMatch = MARKDOWN_BULLET_RE.exec(line);
    if (bulletMatch) {
      const items: string[] = [];
      while (index < lines.length) {
        const itemMatch = MARKDOWN_BULLET_RE.exec(lines[index].trim());
        if (!itemMatch) {
          break;
        }
        items.push(itemMatch[1].trim());
        index += 1;
      }
      parts.push(`<ul>${items.map((item) => `<li>${renderInlineMarkdown(item)}</li>`).join("")}</ul>`);
      continue;
    }

    const orderedMatch = MARKDOWN_ORDERED_RE.exec(line);
    if (orderedMatch) {
      const items: string[] = [];
      while (index < lines.length) {
        const itemMatch = MARKDOWN_ORDERED_RE.exec(lines[index].trim());
        if (!itemMatch) {
          break;
        }
        items.push(itemMatch[1].trim());
        index += 1;
      }
      parts.push(`<ol>${items.map((item) => `<li>${renderInlineMarkdown(item)}</li>`).join("")}</ol>`);
      continue;
    }

    if (line.startsWith(">")) {
      const quoteLines: string[] = [];
      while (index < lines.length && lines[index].trim().startsWith(">")) {
        quoteLines.push(lines[index].trim().replace(/^>\s?/, ""));
        index += 1;
      }
      parts.push(`<blockquote>${renderInlineMarkdown(quoteLines.join(" "))}</blockquote>`);
      continue;
    }

    const paragraphLines = [line];
    index += 1;
    while (index < lines.length && !lineStartsBlock(lines[index]) && !isMarkdownTable(lines, index)) {
      paragraphLines.push(lines[index].trim());
      index += 1;
    }
    const paragraphContent = paragraphLines.map((paragraphLine) => renderInlineMarkdown(paragraphLine)).join("<br>");
    parts.push(`<p>${paragraphContent}</p>`);
  }

  return parts.join("") || "<p></p>";
}
