import { expect, test, type Page } from "@playwright/test";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8110";

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

test("uploads a resource when a file is dropped on the resource panel dropzone", async ({ page }) => {
  const unique = Date.now();
  await enterAsGuest(page);
  await createPackageFromHome(page, `拖放上传课程包 ${unique}`);
  await createLessonFromEmptyStudio(page, `拖放上传页面 ${unique}`);

  await page.getByTitle("展开右侧栏").click();
  await page.getByRole("button", { name: "图书馆" }).click();
  const dropzone = page.getByTestId("resource-upload-dropzone");
  await expect(dropzone).toBeVisible();

  const fileName = `drop-upload-${unique}.md`;
  let uploadedFileSeen = false;
  await page.route("**/api/resources/upload", async (route) => {
    const body = route.request().postDataBuffer();
    uploadedFileSeen = Boolean(body?.includes(Buffer.from(fileName)));
    const authHeader = route.request().headers().authorization;
    const currentPackageResponse = await page.request.get(`${API_BASE_URL}/api/course-package`, {
      headers: authHeader ? { Authorization: authHeader } : undefined,
    });
    const currentPackage = await currentPackageResponse.json();
    currentPackage.resources = [
      ...currentPackage.resources,
      {
        id: `res_${unique}`,
        name: fileName,
        mime_type: "text/markdown",
        resource_type: "markdown",
        size_bytes: 31,
        uploaded_at: new Date().toISOString(),
        scope_lesson_id: currentPackage.active_lesson_id,
        outline: [],
        concept_index: {},
        extracted_text_available: true,
        structure_regions: [],
        toc_entries: [],
        chapter_shards: [],
        parse_warnings: [],
      },
    ];
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(currentPackage) });
  });

  const dataTransfer = await page.evaluateHandle((name) => {
    const transfer = new DataTransfer();
    transfer.items.add(new File(["# Drag upload smoke\n\nbody"], name, { type: "text/markdown" }));
    return transfer;
  }, fileName);

  await dropzone.dispatchEvent("dragenter", { dataTransfer });
  await expect(page.getByRole("button", { name: "松开上传" })).toBeVisible();
  await dropzone.dispatchEvent("dragover", { dataTransfer });
  const uploadResponse = page.waitForResponse(
    (response) => response.url().endsWith("/api/resources/upload") && response.request().method() === "POST"
  );
  await dropzone.dispatchEvent("drop", { dataTransfer });
  await uploadResponse;

  expect(uploadedFileSeen).toBe(true);
  await expect(page.getByText(fileName)).toBeVisible();
});
