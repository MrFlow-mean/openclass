import { Table } from "@tiptap/extension-table";

function stringAttribute(property: string, dataAttribute: string) {
  return {
    default: "",
    parseHTML: (element: HTMLElement) => element.getAttribute(dataAttribute) || "",
    renderHTML: (attributes: Record<string, unknown>) => {
      const value = attributes[property];
      return typeof value === "string" && value ? { [dataAttribute]: value } : {};
    },
  };
}

export const SourceAwareTable = Table.extend({
  addAttributes() {
    return {
      ...this.parent?.(),
      sourceVisualId: stringAttribute("sourceVisualId", "data-source-visual-id"),
      sourceIngestionId: stringAttribute("sourceIngestionId", "data-source-ingestion-id"),
      sourceChapterId: stringAttribute("sourceChapterId", "data-source-chapter-id"),
      sourceTitle: stringAttribute("sourceTitle", "data-source-title"),
      sourceLocator: stringAttribute("sourceLocator", "data-source-locator"),
      pageRange: stringAttribute("pageRange", "data-page-range"),
      caption: stringAttribute("caption", "data-caption"),
      pageNo: {
        default: null,
        parseHTML: (element: HTMLElement) => {
          const raw = element.getAttribute("data-page-no");
          return raw && /^\d+$/.test(raw) ? Number(raw) : null;
        },
        renderHTML: (attributes: Record<string, unknown>) =>
          typeof attributes.pageNo === "number" && attributes.pageNo > 0
            ? { "data-page-no": String(attributes.pageNo) }
            : {},
      },
    };
  },
});
