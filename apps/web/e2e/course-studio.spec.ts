import { expect, test, type Page } from "@playwright/test";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8110";

test.beforeEach(async ({ page }) => {
  const textModel = {
    provider: "openai_codex",
    model: "gpt-5.5",
    label: "OpenAI Codex test model",
    capability: "text",
    enabled: true,
    configured: true,
    default: true,
    default_reasoning_effort: null,
    supported_reasoning_efforts: [],
    default_service_tier: null,
    service_tiers: [],
  };
  const realtimeModel = {
    provider: "openai_codex",
    model: "realtime-unavailable",
    label: "Realtime unavailable in browser tests",
    capability: "realtime",
    enabled: false,
    configured: false,
    default: true,
    default_reasoning_effort: null,
    supported_reasoning_efforts: [],
    default_service_tier: null,
    service_tiers: [],
  };
  await page.route("**/api/ai-models", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        text: [textModel],
        realtime: [realtimeModel],
        defaults: {
          text: { provider: textModel.provider, model: textModel.model },
          realtime: { provider: realtimeModel.provider, model: realtimeModel.model },
        },
      }),
    });
  });
});

async function enterAsGuest(page: Page, nextPath = "/") {
  await page.goto(`/login?next=${encodeURIComponent(nextPath)}`);
  await page.getByRole("button", { name: /游客登录/ }).click();
  await expect(page.getByLabel("添加课程包")).toBeVisible();
}

async function createPackageFromHome(page: Page, title: string) {
  await page.getByLabel("添加课程包").click();
  await page.getByLabel("课程包名称").fill(title);
  const createPackageResponse = page.waitForResponse(
    (response) => response.url().endsWith("/api/packages") && response.request().method() === "POST"
  );
  await page.getByLabel("确认").click();
  await createPackageResponse;
  await expect(page.locator("[data-package-selection-root]").filter({ hasText: title }).first()).toBeVisible();
}

async function createLessonFromEmptyStudio(page: Page, title: string) {
  await page.goto("/studio");
  await expect(page.getByText("这个课程包还是空的")).toBeVisible();
  await page.getByRole("button", { name: "新建第一页" }).click();
  await page.getByLabel("第一页名称").fill(title);
  await page.getByLabel("确认").click();
  await expect(page.locator(".ProseMirror")).toBeVisible();
}

async function setInterfaceLanguage(page: Page, interfaceLanguage: "zh-CN" | "en") {
  await page.evaluate((nextLanguage) => {
    const key = "openclass.profile.settings";
    const eventName = "openclass.profile.settings.changed";
    const stored = window.localStorage.getItem(key);
    const current = stored ? (JSON.parse(stored) as Record<string, unknown>) : {};
    const nextSettings = { ...current, interfaceLanguage: nextLanguage };
    window.localStorage.setItem(key, JSON.stringify(nextSettings));
    window.dispatchEvent(new CustomEvent(eventName, { detail: nextSettings }));
  }, interfaceLanguage);
}

async function writeEditorTextAndWaitForSave(page: Page, text: string) {
  const editor = page.locator(".ProseMirror").first();
  const saveResponse = page.waitForResponse(
    (response) => response.url().includes("/document/save") && response.request().method() === "POST"
  );
  await editor.click();
  await editor.fill(text);
  await saveResponse;
  await expect(editor).toContainText(text);
}

async function openHistoryPanel(page: Page) {
  await page.getByTitle("展开右侧栏").click();
  await expect(page.getByText("修订记录")).toBeVisible();
}

test("creates a package and lesson, edits the document, and persists a version", async ({ page }) => {
  const unique = Date.now();
  await enterAsGuest(page);
  await createPackageFromHome(page, `维护性测试课程包 ${unique}`);
  await createLessonFromEmptyStudio(page, `主流程页面 ${unique}`);

  await writeEditorTextAndWaitForSave(page, `第一版讲义内容 ${unique}`);
  await openHistoryPanel(page);

  await expect(page.getByText("Auto Save").first()).toBeVisible();
});

test("restores each lesson's attached composer reference after switching tabs", async ({ page }) => {
  const unique = Date.now();
  const firstTitle = `引用保留页面一 ${unique}`;
  const secondTitle = `引用保留页面二 ${unique}`;
  const referencedText = `需要保留的引用内容 ${unique}`;

  await enterAsGuest(page);
  await createPackageFromHome(page, `引用保留课程包 ${unique}`);
  await createLessonFromEmptyStudio(page, firstTitle);
  await writeEditorTextAndWaitForSave(page, referencedText);

  await page.getByLabel("新建页面").click();
  await page.getByLabel("新页面名称").fill(secondTitle);
  await page.getByLabel("确认").click();
  await expect(page.getByRole("button", { name: `${secondTitle} main` })).toBeVisible();

  await page.getByRole("button", { name: `${firstTitle} main` }).click();
  const editor = page.locator(".ProseMirror").first();
  await editor.click();
  await page.keyboard.press("ControlOrMeta+A");
  await page.getByRole("button", { name: "引用到输入框" }).click();
  await expect(page.getByLabel("移除引用")).toBeVisible();
  await expect(page.getByText(referencedText, { exact: false }).last()).toBeVisible();
  await page.getByPlaceholder("基于选中内容继续追问").click();
  await expect(page.getByLabel("移除引用")).toBeVisible();

  await page.getByRole("button", { name: `${secondTitle} main` }).click();
  await expect(page.getByLabel("移除引用")).toHaveCount(0);

  await page.getByRole("button", { name: `${firstTitle} main` }).click();
  await expect(page.getByLabel("移除引用")).toBeVisible();
  await expect(page.getByText(referencedText, { exact: false }).last()).toBeVisible();
});

test("orders lesson tabs from newest to oldest", async ({ page }) => {
  const unique = Date.now();
  const firstTitle = `较早课程 ${unique}`;
  const secondTitle = `最近课程 ${unique}`;

  await enterAsGuest(page);
  await createPackageFromHome(page, `课程顺序测试包 ${unique}`);
  await createLessonFromEmptyStudio(page, firstTitle);

  await page.getByLabel("新建页面").click();
  await page.getByLabel("新页面名称").fill(secondTitle);
  await page.getByLabel("确认").click();

  const lessonTabList = page
    .getByRole("navigation")
    .filter({ has: page.getByRole("button", { name: `${secondTitle} main` }) });
  const lessonTabs = lessonTabList.locator(":scope > button");
  await expect(lessonTabs.nth(0)).toHaveAccessibleName(`${secondTitle} main`);
  await expect(lessonTabs.nth(1)).toHaveAccessibleName(`${firstTitle} main`);
});

test("uses the top-right profile avatar as the only account menu on the home page", async ({ page }) => {
  await enterAsGuest(page);

  const accountMenu = page.locator("[data-account-menu-root]");
  await expect(accountMenu).toHaveCount(1);
  await page.getByRole("button", { name: "开放课堂用户头像" }).click();
  await expect(page.getByRole("menu")).toBeVisible();
  await expect(page.getByRole("menuitem", { name: "登录以保存" })).toBeVisible();
  await expect(page.getByRole("menuitem", { name: "结束游客访问" })).toBeVisible();
});

test("collapses course package and standalone lesson lists independently", async ({ page }) => {
  await enterAsGuest(page);

  const packageList = page.locator("#learning-home-course-packages");
  const standaloneList = page.locator("#learning-home-standalone-lessons");
  const collapsePackages = page.getByLabel("收起课程包");
  const collapseStandaloneLessons = page.getByLabel("收起单独课程");

  await expect(packageList).toBeVisible();
  await expect(packageList).toHaveCSS("overflow-y", "auto");
  await expect(standaloneList).toBeVisible();

  await collapsePackages.click();
  await expect(packageList).toBeHidden();
  await expect(page.getByLabel("展开课程包")).toHaveAttribute("aria-expanded", "false");
  await expect(standaloneList).toBeVisible();

  await collapseStandaloneLessons.click();
  await expect(standaloneList).toBeHidden();
  await expect(page.getByLabel("展开单独课程")).toHaveAttribute("aria-expanded", "false");
});

test("localizes the empty course package page in English", async ({ page }) => {
  const unique = Date.now();
  await enterAsGuest(page);
  await createPackageFromHome(page, `English empty package ${unique}`);
  await page.goto("/studio");

  await expect(page.getByText("这个课程包还是空的")).toBeVisible();
  await setInterfaceLanguage(page, "en");
  await expect(page.getByRole("heading", { name: "This package is empty" })).toBeVisible();
  await expect(page.getByText("The tab bar above is this package's page area.")).toBeVisible();
  await page.getByRole("button", { name: "Create first page" }).click();
  await expect(page.getByLabel("First page name")).toHaveAttribute(
    "placeholder",
    "Course intro / Lecture 1 / Practice notes"
  );
  await expect(page.getByLabel("Confirm")).toBeVisible();
  await expect(page.getByLabel("Cancel")).toBeVisible();
});

test("restores an older document version from history", async ({ page }) => {
  const unique = Date.now();
  await enterAsGuest(page);
  await createPackageFromHome(page, `恢复测试课程包 ${unique}`);
  await createLessonFromEmptyStudio(page, `恢复测试页面 ${unique}`);

  const firstVersion = `历史版本一 ${unique}`;
  const secondVersion = `历史版本二 ${unique}`;
  await writeEditorTextAndWaitForSave(page, firstVersion);
  await writeEditorTextAndWaitForSave(page, secondVersion);
  await openHistoryPanel(page);

  const restoreResponse = page.waitForResponse(
    (response) => response.url().includes("/restore") && response.request().method() === "POST"
  );
  await page.getByRole("button", { name: "Restore" }).nth(1).click();
  await restoreResponse;

  const editor = page.locator(".ProseMirror").first();
  await expect(editor).toContainText(firstVersion);
  await expect(editor).not.toContainText(secondVersion);
});

test("DOCX import and export entry points complete without breaking the editor", async ({ page }) => {
  const unique = Date.now();
  await enterAsGuest(page);
  await createPackageFromHome(page, `DOCX 测试课程包 ${unique}`);
  await createLessonFromEmptyStudio(page, `DOCX 测试页面 ${unique}`);
  await writeEditorTextAndWaitForSave(page, `导入前内容 ${unique}`);

  await page.route("**/api/lessons/*/document/import-docx", async (route) => {
    const authHeader = route.request().headers().authorization;
    const currentPackageResponse = await page.request.get(`${API_BASE_URL}/api/course-package`, {
      headers: authHeader ? { Authorization: authHeader } : undefined,
    });
    const currentPackage = await currentPackageResponse.json();
    const importedText = `DOCX 导入内容 ${unique}`;
    const lesson = currentPackage.lessons[0];
    lesson.board_document = {
      ...lesson.board_document,
      content_text: importedText,
      content_html: `<p>${importedText}</p>`,
      content_json: {
        type: "doc",
        content: [{ type: "paragraph", content: [{ type: "text", text: importedText }] }],
      },
    };
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(currentPackage) });
  });
  await page.route("**/api/lessons/*/document/export-docx", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
      body: Buffer.from("openclass-docx-smoke"),
    });
  });

  const fileChooserPromise = page.waitForEvent("filechooser");
  await page.getByRole("button", { name: "导入 DOCX" }).click();
  const fileChooser = await fileChooserPromise;
  await fileChooser.setFiles({
    name: "smoke.docx",
    mimeType: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    buffer: Buffer.from("docx-smoke"),
  });

  await expect(page.locator(".ProseMirror").first()).toContainText(`DOCX 导入内容 ${unique}`);
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "导出 DOCX" }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toMatch(/\.docx$/);
});

test("normalizes raw bold vector notation and math delimiters in the board editor", async ({ page }) => {
  const unique = Date.now();
  const lessonTitle = `公式显示回归页面 ${unique}`;

  await enterAsGuest(page);
  await createPackageFromHome(page, `公式显示回归课程包 ${unique}`);
  await createLessonFromEmptyStudio(page, lessonTitle);

  const rawBlockFormula = "\\boldsymbol{x}=(x_1;x_2;\\cdots;x_d)";
  const rawInlineFormula = "向量 $$\\boldsymbol{x}$$ 的分量";
  let injectedPackage: Record<string, unknown> | null = null;
  await page.route("**/api/course-package", async (route) => {
    if (!injectedPackage) {
      const authHeader = route.request().headers().authorization;
      const currentPackageResponse = await page.request.get(`${API_BASE_URL}/api/course-package`, {
        headers: authHeader ? { Authorization: authHeader } : undefined,
      });
      const currentPackage = await currentPackageResponse.json();
      const lesson = currentPackage.lessons.find((candidate: { title: string }) => candidate.title === lessonTitle);
      lesson.board_document = {
        ...lesson.board_document,
        content_text: `${rawBlockFormula}\n\n${rawInlineFormula}`,
        content_html: `<p>${rawBlockFormula}</p><p>${rawInlineFormula}</p>`,
        content_json: {
          type: "doc",
          content: [
            { type: "paragraph", content: [{ type: "text", text: rawBlockFormula }] },
            { type: "paragraph", content: [{ type: "text", text: rawInlineFormula }] },
          ],
        },
      };
      injectedPackage = currentPackage;
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(injectedPackage) });
  });

  await page.reload();

  const editor = page.locator(".ProseMirror").first();
  await expect(editor.locator("div.tiptap-mathematics-render")).toHaveCount(1);
  await expect(editor.locator("span.tiptap-mathematics-render")).toHaveCount(1);
  await expect(editor).not.toContainText("$$");
});

test("scrolls to and highlights the Board AI-authorized section being explained", async ({ page }) => {
  const unique = Date.now();
  const lessonTitle = `讲解定位回归页面 ${unique}`;
  const targetHeading = `当前讲解的小节 ${unique}`;
  const targetSentence = `需要被荧光标记的讲解内容 ${unique}`;
  const nextHeading = `下一个小节 ${unique}`;
  const nextSentence = `不属于当前讲解范围的内容 ${unique}`;

  await enterAsGuest(page);
  await createPackageFromHome(page, `讲解定位回归课程包 ${unique}`);
  await createLessonFromEmptyStudio(page, lessonTitle);

  let injectedPackage: Record<string, unknown> | null = null;
  await page.route("**/api/course-package", async (route) => {
    if (!injectedPackage) {
      const authHeader = route.request().headers().authorization;
      const upstream = await page.request.get(`${API_BASE_URL}/api/course-package`, {
        headers: authHeader ? { Authorization: authHeader } : undefined,
      });
      const nextPackage = (await upstream.json()) as Record<string, unknown>;
      const lesson = (nextPackage.lessons as Array<Record<string, unknown>>)[0];
      const document = lesson.board_document as Record<string, unknown>;
      const historyGraph = lesson.history_graph as {
        commits: Array<Record<string, unknown>>;
        current_branch: string;
        branches: Record<string, { head_commit_id: string | null }>;
      };
      const branch = historyGraph.branches[historyGraph.current_branch];
      const fillerParagraphs = Array.from({ length: 48 }, (_, index) => `前置内容第 ${index + 1} 段 ${unique}`);
      const targetExcerpt = `## ${targetHeading}\n${targetSentence}`;
      const contentText = [
        `# ${lessonTitle}`,
        ...fillerParagraphs,
        targetExcerpt,
        `## ${nextHeading}\n${nextSentence}`,
      ].join("\n\n");
      const contentJson = {
        type: "doc",
        content: [
          {
            type: "heading",
            attrs: { level: 1 },
            content: [{ type: "text", text: lessonTitle }],
          },
          ...fillerParagraphs.map((text) => ({
            type: "paragraph",
            content: [{ type: "text", text }],
          })),
          {
            type: "heading",
            attrs: { level: 2 },
            content: [{ type: "text", text: targetHeading }],
          },
          {
            type: "paragraph",
            content: [{ type: "text", text: targetSentence }],
          },
          {
            type: "heading",
            attrs: { level: 2 },
            content: [{ type: "text", text: nextHeading }],
          },
          {
            type: "paragraph",
            content: [{ type: "text", text: nextSentence }],
          },
        ],
      };
      lesson.board_document = {
        ...document,
        content_text: contentText,
        content_html: "",
        content_json: contentJson,
      };
      const commitId = `commit_board_directed_explanation_${unique}`;
      historyGraph.commits.push({
        id: commitId,
        label: "Board-directed explanation",
        message: "Chatbot explained the Board AI-authorized section.",
        branch_name: historyGraph.current_branch,
        created_at: new Date().toISOString(),
        parent_ids: branch.head_commit_id ? [branch.head_commit_id] : [],
        operations: [],
        snapshot: lesson.board_document,
        metadata: {
          kind: "board_directed_explanation",
          assistant_message: `正在讲解 ${targetHeading}`,
          board_task_route: "explain",
          resolved_focus: {
            source: "board",
            lesson_id: lesson.id,
            document_id: document.id,
            kind: "heading",
            heading_path: [targetHeading],
            excerpt: targetExcerpt,
            confidence: 1,
            display_label: targetHeading,
          },
          board_explanation_directive: {
            status: "approved",
            target_excerpt: targetExcerpt,
          },
        },
      });
      branch.head_commit_id = commitId;
      injectedPackage = nextPackage;
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(injectedPackage) });
  });

  await page.reload();

  const teachingFocus = page.locator('[data-teaching-focus="true"]');
  const highlightedHeading = teachingFocus.filter({ hasText: targetHeading });
  const highlightedSentence = teachingFocus.filter({ hasText: targetSentence });
  await expect(highlightedHeading).toBeVisible();
  await expect(highlightedSentence).toBeVisible();
  await expect(highlightedHeading).toBeInViewport();
  await expect(highlightedSentence).toBeInViewport();
  await expect(teachingFocus.filter({ hasText: nextSentence })).toHaveCount(0);
});

test("keeps the learning requirement failure visible when the chat final event is missing", async ({ page }) => {
  const unique = Date.now();
  const userMessage = `继续整理我的学习需求 ${unique}`;
  const failureReason = "本轮学习需求没有成功更新，请重试刚才的输入。";
  let recoveredPackage: Record<string, unknown> | null = null;

  await enterAsGuest(page);
  await createPackageFromHome(page, `失败恢复测试课程包 ${unique}`);
  await createLessonFromEmptyStudio(page, `失败恢复测试页面 ${unique}`);

  await page.route("**/api/course-package", async (route) => {
    if (route.request().method() === "GET" && recoveredPackage) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(recoveredPackage),
      });
      return;
    }
    await route.continue();
  });
  await page.route("**/api/lessons/*/chat/stream", async (route) => {
    const authHeader = route.request().headers().authorization;
    const currentPackageResponse = await page.request.get(`${API_BASE_URL}/api/course-package`, {
      headers: authHeader ? { Authorization: authHeader } : undefined,
    });
    const currentPackage = await currentPackageResponse.json();
    const lesson = currentPackage.lessons[0];
    const branch = lesson.history_graph.branches[lesson.history_graph.current_branch];
    const commitId = `commit_recovered_failure_${unique}`;
    lesson.history_graph.commits.push({
      id: commitId,
      label: "Learning requirement refinement failed",
      message: "Recorded a failed blank-board learning requirement refinement turn",
      branch_name: lesson.history_graph.current_branch,
      created_at: new Date().toISOString(),
      parent_ids: branch.head_commit_id ? [branch.head_commit_id] : [],
      operations: [],
      snapshot: lesson.board_document,
      metadata: {
        kind: "learning_requirement_refinement",
        refinement_route: "refinement_failed",
        user_message: userMessage,
        assistant_message: "",
        assistant_message_source: "chatbot_empty",
        learning_requirement_operation_status: "failed",
        learning_requirement_operation_failure_reason: failureReason,
      },
    });
    branch.head_commit_id = commitId;
    recoveredPackage = currentPackage;
    await route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body: 'event: phase\ndata: {"label":"正在整理学习需求"}\n\n',
    });
  });

  await page.getByPlaceholder("给 OpenClass 发消息...").fill(userMessage);
  await page.getByRole("button", { name: "发送消息" }).click();

  await expect(page.getByRole("alert").filter({ hasText: failureReason })).toBeVisible();
});

test("restores persisted learning-intake assistant replies after a page refresh", async ({ page }) => {
  const unique = Date.now();
  const userMessage = `我想学习一个新知识点 ${unique}`;
  const assistantOpening = `这是已持久化的学习需求回复 ${unique}`;
  const assistantMessage = `${assistantOpening}\n$$\nx(t) = \\sin(2\\pi t)\n$$\n向量 $$\\boldsymbol{x}$$ 也应显示为行内公式。\n公式后面的说明仍应正常显示。`;
  let persistedPackage: Record<string, unknown> | null = null;

  await enterAsGuest(page);
  await createPackageFromHome(page, `聊天历史恢复测试课程包 ${unique}`);
  await createLessonFromEmptyStudio(page, `聊天历史恢复测试页面 ${unique}`);

  await page.route("**/api/course-package", async (route) => {
    if (!persistedPackage) {
      const authHeader = route.request().headers().authorization;
      const upstream = await page.request.get(`${API_BASE_URL}/api/course-package`, {
        headers: authHeader ? { Authorization: authHeader } : undefined,
      });
      const nextPackage = (await upstream.json()) as Record<string, unknown>;
      persistedPackage = nextPackage;
      const lesson = (nextPackage.lessons as Array<Record<string, unknown>>)[0];
      const historyGraph = lesson.history_graph as {
        commits: Array<Record<string, unknown>>;
        current_branch: string;
        branches: Record<string, { head_commit_id: string | null }>;
      };
      const branch = historyGraph.branches[historyGraph.current_branch];
      const commitId = `commit_persisted_learning_intake_${unique}`;
      historyGraph.commits.push({
        id: commitId,
        label: "Learning requirement refinement",
        message: "Recorded a learning-intake conversation turn",
        branch_name: historyGraph.current_branch,
        created_at: new Date().toISOString(),
        parent_ids: branch.head_commit_id ? [branch.head_commit_id] : [],
        operations: [],
        snapshot: lesson.board_document,
        metadata: {
          kind: "learning_requirement_refinement",
          user_message: userMessage,
          assistant_message: assistantMessage,
          assistant_message_source: "chatbot_learning_intake",
        },
      });
      branch.head_commit_id = commitId;
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(persistedPackage) });
  });

  await page.reload();

  const chatSidebar = page.getByRole("complementary");
  await expect(chatSidebar.getByText(userMessage)).toBeVisible();
  await expect(chatSidebar.getByText(assistantOpening)).toBeVisible();
  await expect(chatSidebar.getByText("公式后面的说明仍应正常显示。")).toBeVisible();
  await expect(chatSidebar.locator(".katex-display")).toHaveCount(1);
  await expect(chatSidebar.locator(".katex")).toHaveCount(2);
  await expect(chatSidebar).not.toContainText("$$");
  await expect(chatSidebar).not.toContainText("BLOCKMATH");
});

test("does not show a second board-generation confirmation after learning requirements are ready", async ({ page }) => {
  const unique = Date.now();
  const userMessage = `直接开始生成板书 ${unique}`;
  const assistantMessage = `学习需求已准备好 ${unique}`;
  const requirementSheet = {
    theme: `聚焦学习主题 ${unique}`,
    learning_goal: `理解聚焦学习主题 ${unique}`,
    level: "入门",
    known_background: "",
    current_questions: [],
    learning_need_checklist: [],
    target_depth: "建立直觉",
    output_preference: "",
    boundary: "",
    board_scope: [],
    success_criteria: "",
    risk_notes: [],
    board_workflow: "generate_from_scratch",
    work_mode: "knowledge_board",
    granularity: "single_knowledge_point",
  };
  const clarityStatus = {
    progress: 100,
    label: "ready",
    reason: requirementSheet.learning_goal,
    missing_items: [],
    can_start: true,
    forced_start: false,
    summary: requirementSheet.learning_goal,
    key_facts: [],
    checklist: [],
    next_question: "",
    ready_for_board: true,
    work_mode: "knowledge_board",
    granularity: "single_knowledge_point",
  };

  await enterAsGuest(page);
  await createPackageFromHome(page, `无资料生成测试课程包 ${unique}`);
  await page.route("**/api/lessons/*/evidence/pending", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: "null" });
  });
  const initialEvidenceResponse = page.waitForResponse(
    (response) => response.url().includes("/evidence/pending") && response.request().method() === "GET"
  );
  await createLessonFromEmptyStudio(page, `无资料生成测试页面 ${unique}`);
  await initialEvidenceResponse;

  await page.route("**/api/lessons/*/chat/stream", async (route) => {
    const authHeader = route.request().headers().authorization;
    const currentPackageResponse = await page.request.get(`${API_BASE_URL}/api/course-package`, {
      headers: authHeader ? { Authorization: authHeader } : undefined,
    });
    const currentPackage = await currentPackageResponse.json();
    const lesson = currentPackage.lessons[0];
    const branch = lesson.history_graph.branches[lesson.history_graph.current_branch];
    const commitId = `commit_ready_without_evidence_${unique}`;
    lesson.learning_requirements = requirementSheet;
    lesson.history_graph.commits.push({
      id: commitId,
      label: "Learning requirement refinement",
      message: "Recorded a ready learning requirement without source evidence",
      branch_name: lesson.history_graph.current_branch,
      created_at: new Date().toISOString(),
      parent_ids: branch.head_commit_id ? [branch.head_commit_id] : [],
      operations: [],
      snapshot: lesson.board_document,
      metadata: {
        kind: "learning_requirement_refinement",
        user_message: userMessage,
        assistant_message: assistantMessage,
        assistant_message_source: "chatbot_learning_intake",
        learning_clarification_after: clarityStatus,
      },
    });
    branch.head_commit_id = commitId;
    const response = {
      chatbot_message: assistantMessage,
      agent_activity: [],
      learning_requirement_sheet: requirementSheet,
      active_requirement_sheet: requirementSheet,
      learning_clarification: clarityStatus,
      requirement_run_id: `reqrun_ready_without_evidence_${unique}`,
      requirement_version_id: `reqver_ready_without_evidence_${unique}`,
      requirement_phase: "ready",
      learning_requirement_operation_status: "succeeded",
      learning_requirement_operation_failure_reason: null,
      board_task_sheet: null,
      active_board_task_sheet: null,
      board_task_run_id: null,
      board_task_version_id: null,
      board_task_phase: null,
      board_task_questions: [],
      board_decision: { action: "no_change", reason: "等待用户开始生成板书" },
      needs_clarification: false,
      clarification_questions: [],
      requirement_cleared: false,
      board_document_operation_status: "none",
      board_document_operation_failure_reason: null,
      teaching_progress: null,
      course_package: currentPackage,
    };
    await route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body: `event: final\ndata: ${JSON.stringify(response)}\n\n`,
    });
  });

  await page.getByPlaceholder("给 OpenClass 发消息...").fill(userMessage);
  await page.getByRole("button", { name: "发送消息" }).click();

  await expect(page.getByText("学习需求已清晰")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "开始生成板书" })).toHaveCount(0);
  await expect(page.getByText("正在核对本轮资料证据。")).toHaveCount(0);
});
