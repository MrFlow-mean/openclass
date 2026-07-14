import CodeBlock from "@tiptap/extension-code-block";
import { ReactNodeViewRenderer } from "@tiptap/react";

import { RichCodeBlockView } from "@/components/course-studio/rich-code-block-view";

export const RichCodeBlock = CodeBlock.extend({
  addAttributes() {
    return {
      ...this.parent?.(),
      listingTitle: {
        default: null,
        parseHTML: (element: HTMLElement) => element.getAttribute("data-listing-title"),
        renderHTML: (attributes: Record<string, unknown>) =>
          typeof attributes.listingTitle === "string" && attributes.listingTitle.trim()
            ? { "data-listing-title": attributes.listingTitle }
            : {},
      },
      listingNumber: {
        default: null,
        parseHTML: (element: HTMLElement) => element.getAttribute("data-listing-number"),
        renderHTML: (attributes: Record<string, unknown>) =>
          typeof attributes.listingNumber === "string" && attributes.listingNumber.trim()
            ? { "data-listing-number": attributes.listingNumber }
            : {},
      },
      blockKind: {
        default: null,
        parseHTML: (element: HTMLElement) => element.getAttribute("data-block-kind"),
        renderHTML: (attributes: Record<string, unknown>) =>
          typeof attributes.blockKind === "string" && attributes.blockKind.trim()
            ? { "data-block-kind": attributes.blockKind }
            : {},
      },
    };
  },
  addNodeView() {
    return ReactNodeViewRenderer(RichCodeBlockView);
  },
});
