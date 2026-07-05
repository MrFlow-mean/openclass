import { Node, mergeAttributes } from "@tiptap/core";

export const ResourceVisualBlock = Node.create({
  name: "resourceVisualBlock",
  group: "block",
  atom: true,
  selectable: true,

  addAttributes() {
    return {
      marker: {
        default: "",
        parseHTML: (element) => element.getAttribute("data-openclass-resource-visual") || "",
        renderHTML: (attributes) =>
          attributes.marker ? { "data-openclass-resource-visual": attributes.marker } : {},
      },
      caption: {
        default: "",
        parseHTML: (element) => element.getAttribute("data-caption") || "",
        renderHTML: (attributes) => (attributes.caption ? { "data-caption": attributes.caption } : {}),
      },
      source: {
        default: "",
        parseHTML: (element) => element.getAttribute("data-source") || "",
        renderHTML: (attributes) => (attributes.source ? { "data-source": attributes.source } : {}),
      },
      recreationKind: {
        default: "original",
        parseHTML: (element) => element.getAttribute("data-recreation-kind") || "original",
        renderHTML: (attributes) => ({ "data-recreation-kind": attributes.recreationKind || "original" }),
      },
      recreationStatus: {
        default: "original_only",
        parseHTML: (element) => element.getAttribute("data-recreation-status") || "original_only",
        renderHTML: (attributes) => ({ "data-recreation-status": attributes.recreationStatus || "original_only" }),
      },
      recreationConfidence: {
        default: "0.00",
        parseHTML: (element) => element.getAttribute("data-recreation-confidence") || "0.00",
        renderHTML: (attributes) => ({ "data-recreation-confidence": attributes.recreationConfidence || "0.00" }),
      },
      recreationNote: {
        default: "",
        parseHTML: (element) => element.getAttribute("data-recreation-note") || "",
        renderHTML: (attributes) =>
          attributes.recreationNote ? { "data-recreation-note": attributes.recreationNote } : {},
      },
      recreationHtml: {
        default: "",
        parseHTML: (element) => element.getAttribute("data-recreation-html") || "",
        renderHTML: (attributes) =>
          attributes.recreationHtml ? { "data-recreation-html": attributes.recreationHtml } : {},
      },
      originalSrc: {
        default: "",
        parseHTML: (element) => element.getAttribute("data-original-src") || "",
        renderHTML: (attributes) => (attributes.originalSrc ? { "data-original-src": attributes.originalSrc } : {}),
      },
      originalAlt: {
        default: "",
        parseHTML: (element) => element.getAttribute("data-original-alt") || "",
        renderHTML: (attributes) => (attributes.originalAlt ? { "data-original-alt": attributes.originalAlt } : {}),
      },
      originalInitiallyCollapsed: {
        default: true,
        parseHTML: (element) => element.getAttribute("data-original-initially-collapsed") !== "false",
        renderHTML: (attributes) => ({
          "data-original-initially-collapsed": attributes.originalInitiallyCollapsed === false ? "false" : "true",
        }),
      },
    };
  },

  parseHTML() {
    return [{ tag: 'section[data-type="resource-visual-block"]' }];
  },

  renderHTML({ HTMLAttributes, node }) {
    const caption = visualAttr(node.attrs.caption) || "资料视觉素材";
    const source = visualAttr(node.attrs.source);
    return [
      "section",
      mergeAttributes(HTMLAttributes, {
        "data-type": "resource-visual-block",
        class: "word-editor__resource-visual",
      }),
      ["p", { class: "word-editor__resource-visual-label" }, `复刻图示：${caption}`],
      ["p", { class: "word-editor__resource-visual-source" }, source ? `原图来源：${source}` : "原图来源：解析资料"],
    ];
  },

  renderText({ node }) {
    const caption = visualAttr(node.attrs.caption) || "资料视觉素材";
    const source = visualAttr(node.attrs.source);
    return source ? `复刻图示：${caption}\n原图来源：${source}` : `复刻图示：${caption}`;
  },

  addNodeView() {
    return ({ node }) => {
      const attrs = node.attrs;
      const caption = visualAttr(attrs.caption) || "资料视觉素材";
      const source = visualAttr(attrs.source);
      const recreationHtml = visualAttr(attrs.recreationHtml);
      const recreationStatus = visualAttr(attrs.recreationStatus);
      const originalSrc = visualAttr(attrs.originalSrc);
      const originalAlt = visualAttr(attrs.originalAlt) || caption;
      let originalOpen = attrs.originalInitiallyCollapsed === false;

      const dom = document.createElement("section");
      dom.className = "word-editor__resource-visual";
      dom.setAttribute("data-type", "resource-visual-block");
      dom.contentEditable = "false";

      const eyebrow = document.createElement("div");
      eyebrow.className = "word-editor__resource-visual-eyebrow";
      eyebrow.textContent = recreationStatus === "recreated" ? "复刻图示" : "资料图示";
      dom.appendChild(eyebrow);

      const title = document.createElement("p");
      title.className = "word-editor__resource-visual-title";
      title.textContent = caption;
      dom.appendChild(title);

      const replica = document.createElement("div");
      replica.className = "word-editor__resource-visual-replica";
      if (recreationHtml.trim()) {
        replica.innerHTML = recreationHtml;
      } else {
        replica.textContent = "暂未可靠复刻，已保留原图来源供核对。";
      }
      dom.appendChild(replica);

      const original = document.createElement("div");
      original.className = "word-editor__resource-visual-original";
      original.hidden = !originalOpen;
      const sourceLine = document.createElement("p");
      sourceLine.className = "word-editor__resource-visual-source";
      sourceLine.textContent = source ? `原图来源：${source}` : "原图来源：解析资料";
      original.appendChild(sourceLine);
      if (originalSrc) {
        const image = document.createElement("img");
        image.className = "word-editor__resource-visual-original-image";
        image.src = originalSrc;
        image.alt = originalAlt;
        original.appendChild(image);
      } else {
        const note = document.createElement("p");
        note.className = "word-editor__resource-visual-source";
        note.textContent = "原始图片不可用，已保留解析出的文字证据。";
        original.appendChild(note);
      }

      const toggle = document.createElement("button");
      toggle.type = "button";
      toggle.className = "word-editor__resource-visual-toggle";
      toggle.textContent = originalOpen ? "收起原图来源" : "查看原图来源";
      toggle.setAttribute("aria-expanded", String(originalOpen));
      toggle.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        originalOpen = !originalOpen;
        original.hidden = !originalOpen;
        toggle.textContent = originalOpen ? "收起原图来源" : "查看原图来源";
        toggle.setAttribute("aria-expanded", String(originalOpen));
      });
      dom.appendChild(toggle);
      dom.appendChild(original);

      return { dom };
    };
  },
});

function visualAttr(value: unknown): string {
  return typeof value === "string" ? value : "";
}
