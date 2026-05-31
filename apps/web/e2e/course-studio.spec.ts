import { expect, test, type Page } from "@playwright/test";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8110";

async function enterAsGuest(page: Page, nextPath = "/") {
  await page.goto(`/login?next=${encodeURIComponent(nextPath)}`);
  await page.getByRole("button", { name: /Continue as guest/ }).click();
  await expect(page.getByLabel("Add course package")).toBeVisible();
}

async function createPackageFromHome(page: Page, title: string) {
  await page.getByLabel("Add course package").click();
  await page.getByLabel("Package name").fill(title);
  const createPackageResponse = page.waitForResponse(
    (response) => response.url().endsWith("/api/packages") && response.request().method() === "POST"
  );
  await page.getByLabel("Confirm").click();
  await createPackageResponse;
  await expect(page.locator("[data-package-selection-root]").filter({ hasText: title }).first()).toBeVisible();
}

async function createLessonFromEmptyStudio(page: Page, title: string) {
  await page.goto("/studio");
  await expect(page.getByText("This package is empty")).toBeVisible();
  await page.getByRole("button", { name: "Create first page" }).click();
  await page.getByLabel("First page name").fill(title);
  await page.getByLabel("Confirm").click();
  await expect(page.locator(".ProseMirror")).toBeVisible();
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
  await page.getByTitle("Expand side panel").click();
  await expect(page.getByText("Branch history", { exact: true })).toBeVisible();
}

test("creates a package and lesson, edits the document, and persists a version", async ({ page }) => {
  const unique = Date.now();
  await enterAsGuest(page);
  await createPackageFromHome(page, `Maintainability test package ${unique}`);
  await createLessonFromEmptyStudio(page, `Main flow page ${unique}`);

  await writeEditorTextAndWaitForSave(page, `First note version ${unique}`);
  await openHistoryPanel(page);

  await expect(page.getByText("Auto Save").first()).toBeVisible();
});

test("previews an older document version from history without a restore action", async ({ page }) => {
  const unique = Date.now();
  await enterAsGuest(page);
  await createPackageFromHome(page, `History preview test package ${unique}`);
  await createLessonFromEmptyStudio(page, `History preview test page ${unique}`);

  const firstVersion = `Historical version one ${unique}`;
  const secondVersion = `Historical version two ${unique}`;
  await writeEditorTextAndWaitForSave(page, firstVersion);
  await writeEditorTextAndWaitForSave(page, secondVersion);
  await openHistoryPanel(page);
  await page.getByLabel(/View history node/).nth(1).click();

  const editor = page.locator(".ProseMirror").first();
  await expect(editor).toContainText(firstVersion);
  await expect(editor).not.toContainText(secondVersion);
  await expect(page.getByRole("button", { name: "Restore", exact: true })).toHaveCount(0);
});

test("shows a sideways branch sprout immediately after creating a branch", async ({ page }) => {
  const unique = Date.now();
  await enterAsGuest(page);
  await createPackageFromHome(page, `Branch history test package ${unique}`);
  await createLessonFromEmptyStudio(page, `Branch history test page ${unique}`);
  await openHistoryPanel(page);

  const branchResponse = page.waitForResponse(
    (response) => response.url().includes("/branches") && response.request().method() === "POST"
  );
  await page.getByTestId("history-create-branch").click();
  await branchResponse;

  await expect(page.getByText("2 branches")).toBeVisible();
  await expect(page.getByTestId("history-branch-sprout")).toHaveCount(1);
  await expect(page.getByTestId("history-branch-sprout-label")).toContainText("branch-2");
});

test("merges a source branch into the current branch with manual choices", async ({ page }) => {
  const unique = Date.now();
  await enterAsGuest(page);
  await createPackageFromHome(page, `Merge branch test package ${unique}`);
  await createLessonFromEmptyStudio(page, `Merge branch test page ${unique}`);
  await openHistoryPanel(page);

  const branchResponse = page.waitForResponse(
    (response) => response.url().includes("/branches") && response.request().method() === "POST"
  );
  await page.getByTestId("history-create-branch").click();
  await branchResponse;

  const sourceText = `Source branch document ${unique}`;
  await writeEditorTextAndWaitForSave(page, sourceText);

  const switchMainResponse = page.waitForResponse(
    (response) => response.url().includes("/branches/checkout") && response.request().method() === "POST"
  );
  await page.locator('button[title^="main:"]').click();
  await switchMainResponse;

  const targetText = `Current branch document ${unique}`;
  await writeEditorTextAndWaitForSave(page, targetText);

  const previewResponse = page.waitForResponse(
    (response) => response.url().includes("/branches/merge-preview") && response.request().method() === "POST"
  );
  await page.getByLabel("Merge branch branch-2").click();
  await previewResponse;
  await expect(page.getByText("Merge Review")).toBeVisible();

  await page.getByLabel("Use Source for Document").click();
  const mergeResponse = page.waitForResponse(
    (response) => response.url().includes("/branches/merge") && response.request().method() === "POST"
  );
  await page.getByRole("button", { name: "Confirm merge" }).click();
  await mergeResponse;

  const editor = page.locator(".ProseMirror").first();
  await expect(editor).toContainText(sourceText);
  await expect(editor).not.toContainText(targetText);
  await expect(page.getByText("Merge branch-2").first()).toBeVisible();
});

test("DOCX import and export entry points complete without breaking the editor", async ({ page }) => {
  const unique = Date.now();
  await enterAsGuest(page);
  await createPackageFromHome(page, `DOCX test package ${unique}`);
  await createLessonFromEmptyStudio(page, `DOCX test page ${unique}`);
  await writeEditorTextAndWaitForSave(page, `Before import ${unique}`);

  await page.route("**/api/lessons/*/document/import-docx", async (route) => {
    const authHeader = route.request().headers().authorization;
    const currentPackageResponse = await page.request.get(`${API_BASE_URL}/api/course-package`, {
      headers: authHeader ? { Authorization: authHeader } : undefined,
    });
    const currentPackage = await currentPackageResponse.json();
    const importedText = `DOCX imported content ${unique}`;
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
  await page.getByRole("button", { name: "Import DOCX" }).click();
  const fileChooser = await fileChooserPromise;
  await fileChooser.setFiles({
    name: "smoke.docx",
    mimeType: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    buffer: Buffer.from("docx-smoke"),
  });

  await expect(page.locator(".ProseMirror").first()).toContainText(`DOCX imported content ${unique}`);
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "Export DOCX" }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toMatch(/\.docx$/);
});
