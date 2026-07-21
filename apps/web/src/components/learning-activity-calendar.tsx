import clsx from "clsx";
import { Activity } from "lucide-react";

import type { WorkspaceState } from "@/types";

const CONTRIBUTION_WEEKS = 32;
const DAYS_PER_WEEK = 7;

type ActivityDay = {
  key: string;
  date: Date;
  count: number;
  level: 0 | 1 | 2 | 3 | 4;
};

type ActivityWeek = {
  key: string;
  days: Array<ActivityDay | null>;
};

export type LearningActivitySummary = {
  total: number;
  recentActiveDay: ActivityDay | null;
  weeks: ActivityWeek[];
};

type LearningActivityCalendarLabels = {
  title: string;
  subtitle: string;
  total: (count: number) => string;
  dayTitle: (date: string, count: number) => string;
  lastActivePrefix: string;
  noActivityYet: string;
};

type LearningActivityCalendarProps = {
  workspace: WorkspaceState | null;
  labels: LearningActivityCalendarLabels;
  formatRelativeDate: (date: Date) => string;
};

function localDayKey(date: Date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function getActivityLevel(count: number, maxCount: number): ActivityDay["level"] {
  if (count <= 0) {
    return 0;
  }
  if (maxCount <= 1) {
    return 4;
  }

  const ratio = count / maxCount;
  if (ratio >= 0.75) {
    return 4;
  }
  if (ratio >= 0.5) {
    return 3;
  }
  if (ratio >= 0.25) {
    return 2;
  }
  return 1;
}

function activityTone(level: ActivityDay["level"]) {
  return {
    0: "bg-white",
    1: "bg-amber-100",
    2: "bg-amber-300",
    3: "bg-amber-500",
    4: "bg-orange-600",
  }[level];
}

export function buildLearningActivitySummary(
  workspace: WorkspaceState | null,
  now = new Date()
): LearningActivitySummary {
  const today = new Date(now);
  today.setHours(0, 0, 0, 0);

  const firstMonday = new Date(today);
  const daysSinceMonday = (firstMonday.getDay() + 6) % DAYS_PER_WEEK;
  firstMonday.setDate(firstMonday.getDate() - daysSinceMonday - (CONTRIBUTION_WEEKS - 1) * DAYS_PER_WEEK);

  const activityByDay = new Map<string, number>();
  const track = (value?: string | null) => {
    if (!value) {
      return;
    }

    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
      return;
    }

    const key = localDayKey(date);
    activityByDay.set(key, (activityByDay.get(key) ?? 0) + 1);
  };

  workspace?.packages.forEach((coursePackage) => {
    coursePackage.lessons.forEach((lesson) => {
      track(lesson.created_at);
      track(lesson.updated_at);
      lesson.history_graph.commits.forEach((commit) => track(commit.created_at));
    });
    coursePackage.resources.forEach((resource) => track(resource.uploaded_at));
  });

  const calendarDays: Array<ActivityDay | null> = [];
  for (let offset = 0; offset < CONTRIBUTION_WEEKS * DAYS_PER_WEEK; offset += 1) {
    const date = new Date(firstMonday);
    date.setDate(firstMonday.getDate() + offset);
    if (date > today) {
      calendarDays.push(null);
      continue;
    }

    const key = localDayKey(date);
    calendarDays.push({
      key,
      date,
      count: activityByDay.get(key) ?? 0,
      level: 0,
    });
  }

  const visibleDays = calendarDays.filter((day): day is ActivityDay => day !== null);
  const maxCount = visibleDays.reduce((max, day) => Math.max(max, day.count), 0);
  const leveledDays = calendarDays.map((day) =>
    day
      ? {
          ...day,
          level: getActivityLevel(day.count, maxCount),
        }
      : null
  );

  const weeks: ActivityWeek[] = Array.from({ length: CONTRIBUTION_WEEKS }, (_, index) => {
    const days = leveledDays.slice(index * DAYS_PER_WEEK, index * DAYS_PER_WEEK + DAYS_PER_WEEK);
    const datedDays = days.filter((day): day is ActivityDay => day !== null);

    return {
      key: datedDays[0]?.key ?? `week-${index}`,
      days,
    };
  });

  const leveledVisibleDays = leveledDays.filter((day): day is ActivityDay => day !== null);
  return {
    total: leveledVisibleDays.reduce((sum, day) => sum + day.count, 0),
    recentActiveDay: [...leveledVisibleDays].reverse().find((day) => day.count > 0) ?? null,
    weeks,
  };
}

export function LearningActivityCalendar({
  workspace,
  labels,
  formatRelativeDate,
}: LearningActivityCalendarProps) {
  const activity = buildLearningActivitySummary(workspace);

  return (
    <section className="mb-12 rounded-[30px] border border-white/70 bg-white/80 p-6 shadow-[0_18px_50px_rgba(15,23,42,0.07)] backdrop-blur sm:p-7">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h3 className="flex items-center gap-2 text-base font-semibold text-stone-950">
            <Activity className="h-4 w-4" />
            {labels.title}
          </h3>
          <p className="mt-1 text-sm text-stone-500">{labels.subtitle}</p>
        </div>
        <span className="text-xs font-medium text-stone-500" data-testid="learning-activity-total">
          {labels.total(activity.total)}
        </span>
      </div>

      <div className="mt-6 overflow-x-auto" aria-label={labels.subtitle} data-testid="learning-activity-calendar">
        <div className="flex w-full min-w-[42rem] justify-between">
          {activity.weeks.map((week) => (
            <div key={week.key} className="flex flex-col gap-[4px]">
              {week.days.map((day, index) =>
                day ? (
                  <time
                    key={day.key}
                    dateTime={day.key}
                    title={labels.dayTitle(day.key, day.count)}
                    aria-label={labels.dayTitle(day.key, day.count)}
                    data-activity-count={day.count}
                    className={clsx("h-3 w-3 rounded-[3px]", activityTone(day.level))}
                  />
                ) : (
                  <span key={`${week.key}-future-${index}`} className="h-3 w-3" aria-hidden="true" />
                )
              )}
            </div>
          ))}
        </div>
      </div>

      <div className="mt-5 flex flex-col gap-3 text-xs text-stone-400 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-1.5">
          <span>Less</span>
          <div className="h-3 w-3 rounded-[3px] bg-white" />
          <div className="h-3 w-3 rounded-[3px] bg-amber-100" />
          <div className="h-3 w-3 rounded-[3px] bg-amber-300" />
          <div className="h-3 w-3 rounded-[3px] bg-amber-500" />
          <div className="h-3 w-3 rounded-[3px] bg-orange-600" />
          <span>More</span>
        </div>
        <p>
          {labels.lastActivePrefix}
          <span className="ml-1 text-stone-500">
            {activity.recentActiveDay ? formatRelativeDate(activity.recentActiveDay.date) : labels.noActivityYet}
          </span>
        </p>
      </div>
    </section>
  );
}
