"use client";

import clsx from "clsx";
import Image from "next/image";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import {
  ArrowLeft,
  BookOpenCheck,
  Clock3,
  GitFork,
  GitPullRequest,
  LoaderCircle,
  ShieldCheck,
  Sparkles,
  UsersRound,
} from "lucide-react";

import { api } from "@/lib/api";
import { courseAvatarUrl, formatCompactNumber, openCourseFromSummary } from "@/lib/open-courses";
import type { CourseContributionSummary, OpenCourseDetail } from "@/types";

type CourseDetailTab = "overview" | "lessons" | "contributions" | "history" | "maintainers";

type OpenCourseDetailPageProps = {
  courseId: string;
};

const tabs: Array<{ id: CourseDetailTab; label: string }> = [
  { id: "overview", label: "Overview" },
  { id: "lessons", label: "Lessons" },
  { id: "contributions", label: "Contributions" },
  { id: "history", label: "History" },
  { id: "maintainers", label: "Maintainers" },
];

function formatDate(value: string) {
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  }).format(new Date(value));
}

function statusTone(status: CourseContributionSummary["status"]) {
  if (status === "merged") {
    return "border-emerald-200 bg-emerald-50 text-emerald-700";
  }
  if (status === "closed") {
    return "border-stone-200 bg-stone-100 text-stone-600";
  }
  if (status === "changes_requested") {
    return "border-amber-200 bg-amber-50 text-amber-700";
  }
  return "border-sky-200 bg-sky-50 text-sky-700";
}

export function OpenCourseDetailPage({ courseId }: OpenCourseDetailPageProps) {
  const router = useRouter();
  const [detail, setDetail] = useState<OpenCourseDetail | null>(null);
  const [activeTab, setActiveTab] = useState<CourseDetailTab>("overview");
  const [isLoading, setIsLoading] = useState(true);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [contributionTitle, setContributionTitle] = useState("");
  const [contributionDescription, setContributionDescription] = useState("");
  const [maintainerEmail, setMaintainerEmail] = useState("");

  async function refreshDetail() {
    try {
      const payload = await api.getOpenCourse(courseId);
      setDetail(payload);
      setError(null);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Could not load open course");
    } finally {
      setIsLoading(false);
    }
  }

  useEffect(() => {
    let isDisposed = false;

    api
      .getOpenCourse(courseId)
      .then((payload) => {
        if (!isDisposed) {
          setDetail(payload);
          setError(null);
        }
      })
      .catch((loadError) => {
        if (!isDisposed) {
          setError(loadError instanceof Error ? loadError.message : "Could not load open course");
        }
      })
      .finally(() => {
        if (!isDisposed) {
          setIsLoading(false);
        }
      });

    return () => {
      isDisposed = true;
    };
  }, [courseId]);

  const openCourse = useMemo(() => (detail ? openCourseFromSummary(detail.course) : null), [detail]);

  async function handleFork() {
    if (!detail) {
      return;
    }
    setBusyAction("fork");
    try {
      await api.forkOpenCourse(detail.course.id);
      setMessage("Fork created. Open Studio to refine it, then submit an improvement from this page.");
      await refreshDetail();
    } catch (forkError) {
      setError(forkError instanceof Error ? forkError.message : "Could not fork course");
    } finally {
      setBusyAction(null);
    }
  }

  async function handleSubmitContribution() {
    if (!detail?.viewer_fork) {
      return;
    }
    setBusyAction("submit");
    try {
      await api.submitContribution(
        detail.viewer_fork.id,
        contributionTitle.trim() || `Improve ${detail.course.title}`,
        contributionDescription.trim() || "Ready for maintainer review."
      );
      setMessage("Improvement submitted to the maintainers.");
      await refreshDetail();
      setActiveTab("contributions");
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : "Could not submit improvement");
    } finally {
      setBusyAction(null);
    }
  }

  async function handleReview(contributionId: string, action: "request_changes" | "close" | "merge") {
    setBusyAction(`${action}:${contributionId}`);
    try {
      await api.reviewContribution(contributionId, action, action === "merge" ? "Merged into the maintained course." : "");
      setMessage(action === "merge" ? "Improvement merged into the course." : "Review updated.");
      await refreshDetail();
    } catch (reviewError) {
      setError(reviewError instanceof Error ? reviewError.message : "Could not review improvement");
    } finally {
      setBusyAction(null);
    }
  }

  async function handleAddMaintainer() {
    if (!detail || !maintainerEmail.trim()) {
      return;
    }
    setBusyAction("maintainer");
    try {
      const nextDetail = await api.addMaintainer(detail.course.id, maintainerEmail);
      setDetail(nextDetail);
      setMaintainerEmail("");
      setMessage("Maintainer added.");
    } catch (maintainerError) {
      setError(maintainerError instanceof Error ? maintainerError.message : "Could not add maintainer");
    } finally {
      setBusyAction(null);
    }
  }

  if (isLoading) {
    return (
      <main className="min-h-screen bg-[#f7f5ef] px-4 py-10 text-stone-950">
        <div className="mx-auto flex max-w-5xl items-center gap-3 rounded-md border border-stone-200 bg-white px-4 py-3 text-sm text-stone-600">
          <LoaderCircle className="h-4 w-4 animate-spin" />
          Loading course...
        </div>
      </main>
    );
  }

  if (!detail || !openCourse) {
    return (
      <main className="min-h-screen bg-[#f7f5ef] px-4 py-10 text-stone-950">
        <div className="mx-auto max-w-5xl rounded-md border border-stone-200 bg-white px-4 py-3 text-sm text-stone-600">
          {error || "Course not found"}
        </div>
      </main>
    );
  }

  const stats = [
    { label: "Forks", value: formatCompactNumber(detail.course.stats.forks), Icon: GitFork },
    { label: "Open improvements", value: detail.course.stats.open_contributions.toString(), Icon: GitPullRequest },
    { label: "Lessons", value: detail.course.stats.lessons.toString(), Icon: BookOpenCheck },
    { label: "Maintainers", value: detail.course.stats.maintainers.toString(), Icon: UsersRound },
  ];

  return (
    <main className="min-h-screen bg-[#f7f5ef] text-stone-950">
      <div className="mx-auto flex w-full max-w-6xl flex-col gap-6 px-4 py-6 sm:px-6 lg:px-8">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <Link
            href="/trending"
            className="inline-flex w-fit items-center gap-2 rounded-md border border-stone-200 bg-white px-3 py-2 text-sm font-semibold text-stone-700 transition hover:border-stone-300 hover:text-stone-950"
          >
            <ArrowLeft className="h-4 w-4" />
            Back to OpenClass
          </Link>
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={() => void handleFork()}
              disabled={busyAction === "fork"}
              className="inline-flex items-center gap-2 rounded-md border border-stone-200 bg-white px-3 py-2 text-sm font-semibold text-stone-700 transition hover:border-stone-300 hover:text-stone-950 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {busyAction === "fork" ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <GitFork className="h-4 w-4" />}
              Fork
            </button>
            {detail.viewer_fork ? (
              <button
                type="button"
                onClick={() => router.push("/studio")}
                className="inline-flex items-center gap-2 rounded-md bg-stone-950 px-3 py-2 text-sm font-semibold text-white transition hover:bg-stone-800"
              >
                Open fork in Studio
              </button>
            ) : null}
          </div>
        </div>

        {message || error ? (
          <div
            className={clsx(
              "rounded-md border px-4 py-3 text-sm",
              error ? "border-rose-200 bg-rose-50 text-rose-700" : "border-emerald-200 bg-emerald-50 text-emerald-700"
            )}
          >
            {error || message}
          </div>
        ) : null}

        <section className="grid gap-8 lg:grid-cols-[minmax(0,1fr)_20rem]">
          <div className="min-w-0">
            <div className="flex flex-col gap-5 sm:flex-row sm:items-start">
              <Image
                src={courseAvatarUrl(openCourse)}
                alt=""
                className="h-20 w-20 rounded-lg border border-stone-200 bg-white"
                width={80}
                height={80}
                priority
                unoptimized
              />
              <div className="min-w-0">
                <p className="text-sm font-semibold text-stone-500">{detail.course.owner.display_name}</p>
                <h1 className="mt-2 break-words text-4xl font-semibold text-stone-950 sm:text-5xl">{detail.course.title}</h1>
                <p className="mt-5 max-w-3xl text-base leading-8 text-stone-700">{detail.course.summary}</p>
              </div>
            </div>
          </div>

          <aside className="h-fit rounded-lg border border-stone-200 bg-white p-5 shadow-[0_16px_34px_rgba(15,23,42,0.05)]">
            <div className="flex items-center gap-2 text-sm font-semibold text-stone-950">
              <Sparkles className="h-4 w-4 text-sky-600" />
              Course analysis
            </div>
            <dl className="mt-5 grid grid-cols-2 gap-3">
              {stats.map(({ label, value, Icon }) => (
                <div key={label} className="rounded-md border border-stone-200 bg-stone-50 px-3 py-3">
                  <dt className="flex items-center gap-1.5 text-xs text-stone-500">
                    <Icon className="h-3.5 w-3.5" />
                    {label}
                  </dt>
                  <dd className="mt-2 text-lg font-semibold text-stone-950">{value}</dd>
                </div>
              ))}
            </dl>
            <div className="mt-5 flex items-center gap-2 rounded-md border border-stone-200 bg-white px-3 py-3 text-sm text-stone-600">
              <Clock3 className="h-4 w-4 text-stone-400" />
              Updated {formatDate(detail.course.updated_at)}
            </div>
          </aside>
        </section>

        {detail.viewer_fork ? (
          <section className="rounded-lg border border-sky-200 bg-sky-50 p-4">
            <div className="flex flex-col gap-3 lg:flex-row lg:items-end">
              <div className="min-w-0 flex-1">
                <label className="text-xs font-semibold uppercase text-sky-700">Improvement title</label>
                <input
                  value={contributionTitle}
                  onChange={(event) => setContributionTitle(event.target.value)}
                  className="mt-1 w-full rounded-md border border-sky-200 bg-white px-3 py-2 text-sm outline-none focus:border-sky-500"
                  placeholder={`Improve ${detail.course.title}`}
                />
              </div>
              <div className="min-w-0 flex-[1.4]">
                <label className="text-xs font-semibold uppercase text-sky-700">Review note</label>
                <input
                  value={contributionDescription}
                  onChange={(event) => setContributionDescription(event.target.value)}
                  className="mt-1 w-full rounded-md border border-sky-200 bg-white px-3 py-2 text-sm outline-none focus:border-sky-500"
                  placeholder="Describe the course changes, teaching intent, and review notes."
                />
              </div>
              <button
                type="button"
                onClick={() => void handleSubmitContribution()}
                disabled={busyAction === "submit"}
                className="inline-flex items-center justify-center gap-2 rounded-md bg-sky-700 px-4 py-2 text-sm font-semibold text-white transition hover:bg-sky-800 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {busyAction === "submit" ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <GitPullRequest className="h-4 w-4" />}
                Submit improvement
              </button>
            </div>
            <p className="mt-3 text-xs leading-5 text-sky-800">
              This fork contains a full copy of the course materials. Submit only when you are ready for maintainers to review the current fork snapshot.
            </p>
          </section>
        ) : null}

        <nav className="flex flex-wrap gap-2 border-b border-stone-200">
          {tabs.map((tab) => (
            <button
              key={tab.id}
              type="button"
              onClick={() => setActiveTab(tab.id)}
              className={clsx(
                "border-b-2 px-3 py-2 text-sm font-semibold transition",
                activeTab === tab.id
                  ? "border-stone-950 text-stone-950"
                  : "border-transparent text-stone-500 hover:text-stone-950"
              )}
            >
              {tab.label}
            </button>
          ))}
        </nav>

        {activeTab === "overview" ? (
          <section className="grid gap-4 md:grid-cols-2">
            <div className="rounded-lg border border-stone-200 bg-white p-5">
              <h2 className="text-base font-semibold text-stone-950">Course topics</h2>
              <div className="mt-4 flex flex-wrap gap-2">
                {(detail.course.topics.length ? detail.course.topics : ["course"]).map((topic) => (
                  <span key={topic} className="rounded-full bg-sky-50 px-3 py-1.5 text-sm font-semibold text-sky-700">
                    {topic}
                  </span>
                ))}
              </div>
            </div>
            <div className="rounded-lg border border-stone-200 bg-white p-5">
              <h2 className="text-base font-semibold text-stone-950">Maintenance model</h2>
              <p className="mt-3 text-sm leading-6 text-stone-600">
                Forks are reviewed by course maintainers before they become part of the maintained course.
              </p>
            </div>
          </section>
        ) : null}

        {activeTab === "lessons" ? (
          <section className="space-y-3">
            {detail.package.lessons.map((lesson, index) => (
              <article key={lesson.id} className="rounded-lg border border-stone-200 bg-white p-4">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <p className="text-xs font-semibold text-stone-400">Lesson {index + 1}</p>
                    <h2 className="mt-1 text-base font-semibold text-stone-950">{lesson.title}</h2>
                    <p className="mt-2 line-clamp-2 text-sm leading-6 text-stone-600">{lesson.summary || lesson.board_document.content_text}</p>
                  </div>
                  <span className="rounded-full bg-stone-100 px-2.5 py-1 text-xs font-semibold text-stone-600">
                    {lesson.history_graph.commits.length} commits
                  </span>
                </div>
              </article>
            ))}
          </section>
        ) : null}

        {activeTab === "contributions" ? (
          <section className="space-y-3">
            {detail.contributions.length ? (
              detail.contributions.map((contribution) => (
                <article key={contribution.id} className="rounded-lg border border-stone-200 bg-white p-4">
                  <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <h2 className="text-base font-semibold text-stone-950">{contribution.title}</h2>
                        <span className={clsx("rounded-full border px-2.5 py-1 text-xs font-semibold", statusTone(contribution.status))}>
                          {contribution.status.replace("_", " ")}
                        </span>
                      </div>
                      <p className="mt-2 text-sm leading-6 text-stone-600">{contribution.description}</p>
                      <div className="mt-3 flex flex-wrap gap-2 text-xs text-stone-500">
                        <span>{contribution.lesson_changes.length} lesson changes</span>
                        <span>{contribution.resource_changes.length} resource changes</span>
                        <span>by {contribution.contributor.display_name}</span>
                      </div>
                    </div>
                    {detail.viewer_can_review && contribution.status !== "merged" && contribution.status !== "closed" ? (
                      <div className="flex shrink-0 flex-wrap gap-2">
                        <button
                          type="button"
                          onClick={() => void handleReview(contribution.id, "request_changes")}
                          disabled={Boolean(busyAction)}
                          className="rounded-md border border-amber-200 bg-amber-50 px-3 py-1.5 text-xs font-semibold text-amber-700"
                        >
                          Request changes
                        </button>
                        <button
                          type="button"
                          onClick={() => void handleReview(contribution.id, "close")}
                          disabled={Boolean(busyAction)}
                          className="rounded-md border border-stone-200 bg-stone-50 px-3 py-1.5 text-xs font-semibold text-stone-600"
                        >
                          Close
                        </button>
                        <button
                          type="button"
                          onClick={() => void handleReview(contribution.id, "merge")}
                          disabled={Boolean(busyAction)}
                          className="rounded-md bg-emerald-700 px-3 py-1.5 text-xs font-semibold text-white"
                        >
                          Merge
                        </button>
                      </div>
                    ) : null}
                  </div>
                  <div className="mt-4 grid gap-2 md:grid-cols-2">
                    {contribution.lesson_changes.map((change) => (
                      <div key={`${contribution.id}:${change.status}:${change.source_lesson_id}:${change.fork_lesson_id}`} className="rounded-md border border-stone-200 bg-stone-50 p-3">
                        <p className="text-xs font-semibold uppercase text-stone-500">{change.status}</p>
                        <p className="mt-1 text-sm font-semibold text-stone-950">{change.title}</p>
                        <p className="mt-2 line-clamp-2 text-xs leading-5 text-stone-600">{change.proposed_summary || change.current_summary || change.base_summary}</p>
                        {change.current_changed ? <p className="mt-2 text-xs font-semibold text-amber-700">Main changed after fork baseline</p> : null}
                      </div>
                    ))}
                  </div>
                </article>
              ))
            ) : (
              <div className="rounded-lg border border-dashed border-stone-300 bg-white px-5 py-10 text-sm text-stone-500">
                No improvements have been submitted yet.
              </div>
            )}
          </section>
        ) : null}

        {activeTab === "history" ? (
          <section className="space-y-3">
            {detail.package.lessons.flatMap((lesson) =>
              lesson.history_graph.commits.map((commit) => (
                <article key={`${lesson.id}:${commit.id}`} className="rounded-lg border border-stone-200 bg-white p-4">
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <div>
                      <p className="text-xs font-semibold text-stone-400">{lesson.title}</p>
                      <h2 className="mt-1 text-sm font-semibold text-stone-950">{commit.label}</h2>
                      <p className="mt-1 text-sm text-stone-600">{commit.message}</p>
                    </div>
                    <span className="text-xs text-stone-400">{formatDate(commit.created_at)}</span>
                  </div>
                  {commit.metadata?.contribution_id ? (
                    <p className="mt-3 inline-flex rounded-full bg-emerald-50 px-2.5 py-1 text-xs font-semibold text-emerald-700">
                      Merged contribution {String(commit.metadata.contribution_id)}
                    </p>
                  ) : null}
                </article>
              ))
            )}
          </section>
        ) : null}

        {activeTab === "maintainers" ? (
          <section className="space-y-3">
            {detail.viewer_is_owner ? (
              <div className="flex flex-col gap-3 rounded-lg border border-stone-200 bg-white p-4 sm:flex-row sm:items-end">
                <label className="min-w-0 flex-1 text-sm font-semibold text-stone-700">
                  Add maintainer by email
                  <input
                    value={maintainerEmail}
                    onChange={(event) => setMaintainerEmail(event.target.value)}
                    className="mt-1 w-full rounded-md border border-stone-200 px-3 py-2 text-sm outline-none focus:border-stone-950"
                    placeholder="maintainer@example.com"
                  />
                </label>
                <button
                  type="button"
                  onClick={() => void handleAddMaintainer()}
                  disabled={busyAction === "maintainer"}
                  className="inline-flex items-center justify-center gap-2 rounded-md bg-stone-950 px-4 py-2 text-sm font-semibold text-white disabled:opacity-60"
                >
                  {busyAction === "maintainer" ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <ShieldCheck className="h-4 w-4" />}
                  Add
                </button>
              </div>
            ) : null}
            {detail.maintainers.map((maintainer) => (
              <article key={maintainer.user.id} className="flex items-center justify-between rounded-lg border border-stone-200 bg-white p-4">
                <div>
                  <p className="text-sm font-semibold text-stone-950">{maintainer.user.display_name}</p>
                  <p className="mt-1 text-xs text-stone-500">{maintainer.role}</p>
                </div>
                <span className="text-xs text-stone-400">Added {formatDate(maintainer.added_at)}</span>
              </article>
            ))}
          </section>
        ) : null}
      </div>
    </main>
  );
}
