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
  const response = page.waitForResponse(
    (candidate) => candidate.url().endsWith("/api/packages") && candidate.request().method() === "POST"
  );
  await page.getByLabel("确认").click();
  await response;
}

async function createLessonFromEmptyStudio(page: Page, title: string) {
  await page.goto("/studio");
  await expect(page.getByText("这个课程包还是空的")).toBeVisible();
  await page.getByRole("button", { name: "新建第一页" }).click();
  await page.getByLabel("第一页名称").fill(title);
  await page.getByLabel("确认").click();
  await expect(page.locator(".ProseMirror")).toBeVisible();
}

function visualEvidenceBundle(unique: number) {
  const sourceId = `source_visual_${unique}`;
  const chapterId = `chapter_visual_${unique}`;
  const visual = (visualId: string, caption: string, pageNo: number) => ({
    visual_id: visualId,
    source_ingestion_id: sourceId,
    source_title: `图表资料 ${unique}`,
    chapter_id: chapterId,
    section_path: ["第一章"],
    kind: "chart",
    order_index: pageNo,
    source_locator: `page:${pageNo}`,
    page_range: `第 ${pageNo} 页`,
    page_no: pageNo,
    slide_no: null,
    sheet_name: "",
    bbox: [0.1, 0.1, 0.9, 0.8],
    before_chunk_id: `chunk_before_${pageNo}`,
    after_chunk_id: `chunk_after_${pageNo}`,
    caption,
    ocr_text: "",
    anchor_status: "verified",
    confidence: 0.98,
    storage_key: `visuals/${visualId}.png`,
    mime_type: "image/png",
    content_hash: `content_hash_${visualId}`,
    position_hash: `position_hash_${visualId}`,
    asset_hash: `asset_hash_${visualId}`,
    anchor_hash: `anchor_hash_${visualId}`,
    width: 800,
    height: 500,
    table_data: [],
    metadata: {},
  });
  return {
    id: `bundle_visual_${unique}`,
    owner_user_id: "guest",
    package_id: `package_visual_${unique}`,
    lesson_id: null,
    requirement_run_id: `requirement_visual_${unique}`,
    board_task_run_id: null,
    purpose: "board_generation",
    status: "candidate",
    query: `使用资料生成板书 ${unique}`,
    evidence_items: [
      {
        id: `evidence_visual_${unique}`,
        source_ingestion_id: sourceId,
        source_title: `图表资料 ${unique}`,
        source_uri: null,
        chapter_id: chapterId,
        section_path: ["第一章"],
        page_range: "第 3-5 页",
        chunk_ids: ["chunk_before_3", "chunk_after_5"],
        excerpt: "本章包含两张需要随正文插入的图表。",
        expanded_text: "本章包含两张需要随正文插入的图表。",
        relevance_score: 1,
        reason: "已定位到确认章节",
        token_count: 20,
        metadata: {},
      },
    ],
    visual_items: [
      visual(`visual_growth_${unique}`, "增长趋势", 3),
      visual(`visual_distribution_${unique}`, "分布对比", 5),
    ],
    context_text: "本章包含两张需要随正文插入的图表。",
    token_count: 20,
    confirmed_by_user: false,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    confirmed_at: null,
    metadata: {},
  };
}

test("shows visual count, captions, and pages on the atomic evidence confirmation card", async ({ page }) => {
  const unique = Date.now();
  const bundle = visualEvidenceBundle(unique);
  const requirementSheet = {
    theme: `图表资料学习 ${unique}`,
    learning_goal: "理解资料中的正文与图表",
    level: "入门",
    known_background: "",
    current_questions: [],
    learning_need_checklist: [],
    target_depth: "理解结构",
    output_preference: "",
    boundary: "",
    board_scope: [],
    success_criteria: "",
    risk_notes: [],
    board_workflow: "generate_from_scratch",
    work_mode: "knowledge_board",
    granularity: "source_chapter",
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
    granularity: "source_chapter",
  };

  await enterAsGuest(page);
  await createPackageFromHome(page, `视觉证据确认 ${unique}`);
  await page.route("**/api/lessons/*/evidence/pending", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(bundle) });
  });
  await createLessonFromEmptyStudio(page, `视觉证据页面 ${unique}`);
  await page.route("**/api/lessons/*/chat/stream", async (route) => {
    const authHeader = route.request().headers().authorization;
    const currentPackageResponse = await page.request.get(`${API_BASE_URL}/api/course-package`, {
      headers: authHeader ? { Authorization: authHeader } : undefined,
    });
    const currentPackage = await currentPackageResponse.json();
    const response = {
      chatbot_message: "资料与学习需求已准备好。",
      agent_turn_decision: null,
      agent_activity: [],
      learning_requirement_sheet: requirementSheet,
      active_requirement_sheet: requirementSheet,
      active_interaction_session: null,
      interaction_decision: null,
      learning_clarification: clarityStatus,
      requirement_run_id: bundle.requirement_run_id,
      requirement_version_id: `requirement_version_${unique}`,
      requirement_phase: "ready",
      learning_requirement_operation_status: "succeeded",
      learning_requirement_operation_failure_reason: null,
      board_task_sheet: null,
      active_board_task_sheet: null,
      board_task_run_id: null,
      board_task_version_id: null,
      board_task_phase: null,
      board_task_questions: [],
      board_decision: { action: "no_change", reason: "等待用户确认资料" },
      needs_clarification: false,
      clarification_questions: [],
      patch_proposal: null,
      scope_options: [],
      board_edit_prompt: null,
      resolved_focus: null,
      focus_candidates: [],
      board_search_evidence: null,
      evidence_bundle: bundle,
      candidate_evidence_bundle: bundle,
      requirement_cleared: false,
      board_document_operation_status: "none",
      board_document_operation_failure_reason: null,
      board_patch_diff: [],
      created_lesson: null,
      teaching_progress: null,
      course_package: currentPackage,
    };
    await route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body: `event: final\ndata: ${JSON.stringify(response)}\n\n`,
    });
  });

  await page.getByPlaceholder("给 OpenClass 发消息...").fill(bundle.query);
  await page.getByRole("button", { name: "发送消息" }).click();

  const card = page.locator("[data-board-generation-confirmation-card]");
  await expect(card).toBeVisible();
  await expect(card.getByText("图表 2", { exact: true })).toBeVisible();
  await expect(card.getByText("增长趋势", { exact: true })).toBeVisible();
  await expect(card.getByText(`图表资料 ${unique} / 第 3 页`, { exact: true })).toBeVisible();
  await expect(card.getByText("分布对比", { exact: true })).toBeVisible();
  await expect(card.getByText(`图表资料 ${unique} / 第 5 页`, { exact: true })).toBeVisible();
  await expect(card.getByRole("checkbox")).toHaveCount(0);
  await expect(card.getByRole("button", { name: "使用资料" })).toBeVisible();
});

test("loads a permanent board asset with auth and never executes legacy recreation HTML", async ({ page }) => {
  const unique = Date.now();
  const assetId = `boardasset_visual_${unique}`;
  const visualId = `sourcevisual_${unique}`;
  let assetAuthorization = "";
  let hydratedPackage: Record<string, unknown> | null = null;

  await enterAsGuest(page);
  await createPackageFromHome(page, `板书图片显示 ${unique}`);
  await createLessonFromEmptyStudio(page, `板书图片页面 ${unique}`);

  await page.route(`**/api/board-assets/${assetId}/content`, async (route) => {
    assetAuthorization = route.request().headers().authorization ?? "";
    await route.fulfill({
      status: 200,
      contentType: "image/png",
      body: Buffer.from(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=",
        "base64"
      ),
    });
  });
  await page.route("**/api/course-package", async (route) => {
    if (!hydratedPackage) {
      const authHeader = route.request().headers().authorization;
      const upstream = await page.request.get(`${API_BASE_URL}/api/course-package`, {
        headers: authHeader ? { Authorization: authHeader } : undefined,
      });
      hydratedPackage = (await upstream.json()) as Record<string, unknown>;
      const lesson = (hydratedPackage.lessons as Array<Record<string, unknown>>)[0];
      const document = {
        ...(lesson.board_document as Record<string, unknown>),
        content_text: `图表前文 ${unique}\n增长趋势\n图表后文 ${unique}`,
        content_html: `<p>图表前文 ${unique}</p><section data-type="resource-visual-block" data-visual-id="${visualId}" data-board-asset-id="${assetId}" data-caption="增长趋势" data-source="图表资料 ${unique}" data-source-locator="page:7"></section><p>图表后文 ${unique}</p>`,
        content_json: {
          type: "doc",
          content: [
            { type: "paragraph", content: [{ type: "text", text: `图表前文 ${unique}` }] },
            {
              type: "resourceVisualBlock",
              attrs: {
                marker: `visual_marker_${unique}`,
                assetId,
                visualId,
                sourceIngestionId: `source_${unique}`,
                sourceChapterId: `chapter_${unique}`,
                sourceTitle: `图表资料 ${unique}`,
                sourceLocator: "page:7",
                kind: "chart",
                caption: "增长趋势",
                source: `图表资料 ${unique}`,
                pageNo: 7,
                pageRange: "第 7 页",
                recreationHtml: '<img src="x" onerror="window.__unsafeVisualHtmlExecuted=true">',
                originalSrc: "",
                originalAlt: "增长趋势原图",
              },
            },
            { type: "paragraph", content: [{ type: "text", text: `图表后文 ${unique}` }] },
          ],
        },
      };
      lesson.board_document = document;
      const historyGraph = lesson.history_graph as {
        commits: Array<Record<string, unknown>>;
        current_branch: string;
        branches: Record<string, { head_commit_id: string | null }>;
      };
      const headId = historyGraph.branches[historyGraph.current_branch]?.head_commit_id;
      const head = historyGraph.commits.find((commit) => commit.id === headId);
      if (head) {
        head.snapshot = document;
      }
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(hydratedPackage) });
  });

  await page.reload();

  const block = page.locator(`section[data-type="resource-visual-block"][data-board-asset-id="${assetId}"]`);
  const image = block.locator("img");
  await expect(block).toBeVisible();
  await expect(image).toBeVisible();
  await expect(image).toHaveAttribute("src", /^blob:/);
  await expect(image).toHaveAttribute("alt", "增长趋势原图");
  await expect(block).toContainText(`来源：图表资料 ${unique} / 第 7 页`);
  await expect(block.locator("img")).toHaveCount(1);
  expect(assetAuthorization).toMatch(/^Bearer /);
  expect(
    await page.evaluate(
      () => (window as Window & { __unsafeVisualHtmlExecuted?: boolean }).__unsafeVisualHtmlExecuted
    )
  ).toBeUndefined();
});
