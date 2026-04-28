"use client";

import clsx from "clsx";
import Image from "next/image";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState, type FormEvent, type ReactNode } from "react";
import type { LucideIcon } from "lucide-react";
import {
  AtSign,
  Bell,
  Building2,
  Check,
  CircleUserRound,
  Clock3,
  CreditCard,
  Eye,
  Globe2,
  KeyRound,
  LinkIcon,
  LoaderCircle,
  LockKeyhole,
  Mail,
  MapPin,
  MonitorSmartphone,
  Palette,
  RotateCcw,
  Send,
  ShieldCheck,
  Sparkles,
  UserRound,
} from "lucide-react";

import { api, OPENCLASS_AUTH_TOKEN_STORAGE_KEY } from "@/lib/api";
import { userAccountLabel, userPublicEmail } from "@/lib/account";
import type { AIModelCatalog, AIModelOption, UserView } from "@/types";

type SettingsSectionId =
  | "profile"
  | "account"
  | "appearance"
  | "notifications"
  | "billing"
  | "email"
  | "password"
  | "models"
  | "security";

type SettingsNavItem = {
  id: SettingsSectionId;
  label: string;
  icon: LucideIcon;
};

export type ProfileSettings = {
  displayName: string;
  handle: string;
  profileVisibility: "private" | "workspace" | "public";
  publicEmail: string;
  showPublicEmail: boolean;
  showSocialLinks: boolean;
  showRepositoriesOnProfile: boolean;
  showStarsOnProfile: boolean;
  bio: string;
  website: string;
  company: string;
  location: string;
  learningFocus: string;
  socialLinks: string[];
  theme: "system" | "light" | "warm";
  density: "comfortable" | "compact";
  startPage: "home" | "studio" | "profile";
  reduceMotion: boolean;
  highContrast: boolean;
  largeText: boolean;
  visibleFocus: boolean;
  courseActivityNotifications: boolean;
  weeklyDigestNotifications: boolean;
  aiResultNotifications: boolean;
  resourceNotifications: boolean;
  browserNotifications: boolean;
  notificationFrequency: "instant" | "hourly" | "daily";
  quietStart: string;
  quietEnd: string;
  emailCourseDigest: boolean;
  emailAiSummary: boolean;
  emailSecurityAlerts: boolean;
  preferredTextModel: string;
  preferredRealtimeModel: string;
  hideLocalPaths: boolean;
  confirmExternalLinks: boolean;
  clearSessionOnExit: boolean;
  allowCourseDiscovery: boolean;
};

type ProfileSettingsPanelProps = {
  avatarUrl: string;
  favoriteCount: number;
  onSettingsPreviewChange?: (settings: ProfileSettings) => void;
  repositoryCount: number;
};

type SaveStatus = "idle" | "saved" | "error";
type BrowserNotificationPermission = NotificationPermission | "unsupported";

export const PROFILE_SETTINGS_STORAGE_KEY = "openclass.profile.settings";
export const PROFILE_SETTINGS_CHANGED_EVENT = "openclass.profile.settings.changed";

const settingsPrimaryNav: SettingsNavItem[] = [
  { id: "profile", label: "公开资料", icon: UserRound },
  { id: "account", label: "账户", icon: CircleUserRound },
  { id: "appearance", label: "外观", icon: Palette },
  { id: "notifications", label: "通知", icon: Bell },
];

const settingsAccountNav: SettingsNavItem[] = [
  { id: "billing", label: "计费和许可", icon: CreditCard },
  { id: "email", label: "电子邮件", icon: Mail },
  { id: "password", label: "密码和身份验证", icon: KeyRound },
  { id: "models", label: "AI 模型", icon: Sparkles },
  { id: "security", label: "代码安全", icon: ShieldCheck },
];

const sectionTitles: Record<SettingsSectionId, { title: string; eyebrow: string }> = {
  profile: { title: "公开资料", eyebrow: "Profile" },
  account: { title: "账户概览", eyebrow: "Account" },
  appearance: { title: "外观", eyebrow: "Appearance" },
  notifications: { title: "通知", eyebrow: "Notifications" },
  billing: { title: "计费和许可", eyebrow: "License" },
  email: { title: "电子邮件", eyebrow: "Email" },
  password: { title: "密码和身份验证", eyebrow: "Authentication" },
  models: { title: "AI 模型", eyebrow: "Models" },
  security: { title: "代码安全", eyebrow: "Security" },
};

export const DEFAULT_PROFILE_SETTINGS: ProfileSettings = {
  displayName: "Flow-mean",
  handle: "blackboard-student",
  profileVisibility: "workspace",
  publicEmail: "",
  showPublicEmail: false,
  showSocialLinks: true,
  showRepositoriesOnProfile: true,
  showStarsOnProfile: true,
  bio: "管理自己的课程项目，Stars 收藏值得继续学习的他人开源课程。",
  website: "",
  company: "",
  location: "",
  learningFocus: "数学、AI 课程设计、课堂讲义",
  socialLinks: ["", "", "", ""],
  theme: "system",
  density: "comfortable",
  startPage: "home",
  reduceMotion: false,
  highContrast: false,
  largeText: false,
  visibleFocus: true,
  courseActivityNotifications: true,
  weeklyDigestNotifications: true,
  aiResultNotifications: true,
  resourceNotifications: false,
  browserNotifications: false,
  notificationFrequency: "instant",
  quietStart: "22:00",
  quietEnd: "08:00",
  emailCourseDigest: true,
  emailAiSummary: false,
  emailSecurityAlerts: true,
  preferredTextModel: "auto",
  preferredRealtimeModel: "auto",
  hideLocalPaths: true,
  confirmExternalLinks: true,
  clearSessionOnExit: false,
  allowCourseDiscovery: false,
};

const settingsInputClass =
  "w-full rounded-md border border-stone-300 bg-white px-3 py-2 text-sm text-stone-900 shadow-sm outline-none transition placeholder:text-stone-400 focus:border-sky-500 focus:ring-2 focus:ring-sky-100";

const settingSectionClass = "border-b border-stone-200 pb-7";

export function normalizeProfileSettings(raw: Partial<ProfileSettings> | null): ProfileSettings {
  const next = {
    ...DEFAULT_PROFILE_SETTINGS,
    ...(raw ?? {}),
  };

  return {
    ...next,
    socialLinks: Array.from({ length: 4 }, (_, index) =>
      typeof raw?.socialLinks?.[index] === "string" ? raw.socialLinks[index] : DEFAULT_PROFILE_SETTINGS.socialLinks[index]
    ),
  };
}

export function readStoredProfileSettings() {
  if (typeof window === "undefined") {
    return DEFAULT_PROFILE_SETTINGS;
  }

  try {
    const stored = window.localStorage.getItem(PROFILE_SETTINGS_STORAGE_KEY);
    if (!stored) {
      return DEFAULT_PROFILE_SETTINGS;
    }
    return normalizeProfileSettings(JSON.parse(stored) as Partial<ProfileSettings>);
  } catch {
    return DEFAULT_PROFILE_SETTINGS;
  }
}

function getNotificationPermission(): BrowserNotificationPermission {
  if (typeof window === "undefined" || !("Notification" in window)) {
    return "unsupported";
  }

  return Notification.permission;
}

function formatAccountDate(value: string | null | undefined) {
  if (!value) {
    return "未记录";
  }

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "未记录";
  }

  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function modelValue(model: AIModelOption) {
  return `${model.provider}:${model.model}`;
}

function modelLabel(model: AIModelOption) {
  const provider = model.provider.toUpperCase();
  return `${model.label || model.model} · ${provider}`;
}

function configuredModels(models: AIModelOption[]) {
  return models.filter((model) => model.configured);
}

export function ProfileSettingsPanel({
  avatarUrl,
  favoriteCount,
  onSettingsPreviewChange,
  repositoryCount,
}: ProfileSettingsPanelProps) {
  const router = useRouter();
  const [activeSection, setActiveSection] = useState<SettingsSectionId>("profile");
  const [settings, setSettings] = useState<ProfileSettings>(DEFAULT_PROFILE_SETTINGS);
  const [saveStatus, setSaveStatus] = useState<SaveStatus>("idle");
  const [saveMessage, setSaveMessage] = useState<string | null>(null);
  const [currentUser, setCurrentUser] = useState<UserView | null>(null);
  const [isLoadingUser, setIsLoadingUser] = useState(true);
  const [userError, setUserError] = useState<string | null>(null);
  const [modelCatalog, setModelCatalog] = useState<AIModelCatalog | null>(null);
  const [isLoadingModels, setIsLoadingModels] = useState(true);
  const [modelError, setModelError] = useState<string | null>(null);
  const [passwordMessage, setPasswordMessage] = useState<string | null>(null);
  const [notificationPermission, setNotificationPermission] =
    useState<BrowserNotificationPermission>("unsupported");
  const [notificationMessage, setNotificationMessage] = useState<string | null>(null);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }

    const timeoutId = window.setTimeout(() => {
      setSettings(readStoredProfileSettings());
      setNotificationPermission(getNotificationPermission());
    }, 0);

    return () => window.clearTimeout(timeoutId);
  }, []);

  useEffect(() => {
    onSettingsPreviewChange?.(settings);
  }, [onSettingsPreviewChange, settings]);

  useEffect(() => {
    let isDisposed = false;

    async function loadAccount() {
      try {
        const user = await api.getCurrentUser();
        if (isDisposed) {
          return;
        }
        setCurrentUser(user);
        setUserError(null);
      } catch (error) {
        if (!isDisposed) {
          setCurrentUser(null);
          setUserError(error instanceof Error ? error.message : "无法读取账户信息");
        }
      } finally {
        if (!isDisposed) {
          setIsLoadingUser(false);
        }
      }
    }

    async function loadModels() {
      try {
        const catalog = await api.getAIModels();
        if (isDisposed) {
          return;
        }
        setModelCatalog(catalog);
        setModelError(null);
      } catch (error) {
        if (!isDisposed) {
          setModelCatalog(null);
          setModelError(error instanceof Error ? error.message : "无法读取模型配置");
        }
      } finally {
        if (!isDisposed) {
          setIsLoadingModels(false);
        }
      }
    }

    void loadAccount();
    void loadModels();

    return () => {
      isDisposed = true;
    };
  }, []);

  const textModels = useMemo(() => configuredModels(modelCatalog?.text ?? []), [modelCatalog]);
  const realtimeModels = useMemo(() => configuredModels(modelCatalog?.realtime ?? []), [modelCatalog]);
  const defaultTextModel = useMemo(() => modelCatalog?.text.find((model) => model.default) ?? null, [modelCatalog]);
  const defaultRealtimeModel = useMemo(
    () => modelCatalog?.realtime.find((model) => model.default) ?? null,
    [modelCatalog]
  );
  const normalizedHandle = settings.handle.trim().toLowerCase();
  const isHandleValid = /^[a-z0-9][a-z0-9-]{2,31}$/.test(normalizedHandle);
  const publicProfileUrl = `openclass.local/${normalizedHandle || DEFAULT_PROFILE_SETTINGS.handle}`;
  const filledProfileFields = [
    settings.displayName,
    settings.handle,
    settings.bio,
    settings.learningFocus,
    settings.website,
    settings.company,
    settings.location,
    ...settings.socialLinks.filter(Boolean),
  ].filter((value) => value.trim()).length;
  const profileCompleteness = Math.min(100, Math.round((filledProfileFields / 11) * 100));
  const enabledNotificationCount = [
    settings.courseActivityNotifications,
    settings.weeklyDigestNotifications,
    settings.aiResultNotifications,
    settings.resourceNotifications,
    settings.browserNotifications,
  ].filter(Boolean).length;

  function updateSetting<Key extends keyof ProfileSettings>(key: Key, value: ProfileSettings[Key]) {
    setSettings((current) => ({ ...current, [key]: value }));
    setSaveStatus("idle");
    setSaveMessage(null);
  }

  function updateSocialLink(index: number, value: string) {
    setSettings((current) => {
      const socialLinks = [...current.socialLinks];
      socialLinks[index] = value;
      return { ...current, socialLinks };
    });
    setSaveStatus("idle");
    setSaveMessage(null);
  }

  function handleSave(event?: FormEvent<HTMLFormElement>) {
    event?.preventDefault();

    if (activeSection === "profile" && !isHandleValid) {
      setSaveStatus("error");
      setSaveMessage("用户名需为 3-32 位小写字母、数字或连字符，并以字母或数字开头。");
      return;
    }

    try {
      window.localStorage.setItem(PROFILE_SETTINGS_STORAGE_KEY, JSON.stringify(settings));
      window.dispatchEvent(new CustomEvent(PROFILE_SETTINGS_CHANGED_EVENT, { detail: settings }));
      setSaveStatus("saved");
      setSaveMessage("已保存到本机");
    } catch {
      setSaveStatus("error");
      setSaveMessage("保存失败，请检查浏览器存储权限。");
    }
  }

  function handleResetAppearance() {
    setSettings((current) => ({
      ...current,
      density: DEFAULT_PROFILE_SETTINGS.density,
      highContrast: DEFAULT_PROFILE_SETTINGS.highContrast,
      largeText: DEFAULT_PROFILE_SETTINGS.largeText,
      reduceMotion: DEFAULT_PROFILE_SETTINGS.reduceMotion,
      startPage: DEFAULT_PROFILE_SETTINGS.startPage,
      theme: DEFAULT_PROFILE_SETTINGS.theme,
      visibleFocus: DEFAULT_PROFILE_SETTINGS.visibleFocus,
    }));
    setSaveStatus("idle");
    setSaveMessage(null);
  }

  async function handleRequestNotificationPermission() {
    if (typeof window === "undefined" || !("Notification" in window)) {
      setNotificationPermission("unsupported");
      setNotificationMessage("当前浏览器不支持桌面通知。");
      return;
    }

    try {
      const permission = await Notification.requestPermission();
      setNotificationPermission(permission);
      setNotificationMessage(permission === "granted" ? "桌面通知已启用。" : "浏览器没有授予桌面通知权限。");
      if (permission === "granted") {
        updateSetting("browserNotifications", true);
      }
    } catch {
      setNotificationMessage("通知权限请求失败。");
    }
  }

  async function handleSendTestNotification() {
    if (typeof window === "undefined" || !("Notification" in window)) {
      setNotificationPermission("unsupported");
      setNotificationMessage("当前浏览器不支持桌面通知。");
      return;
    }

    let permission = Notification.permission;
    if (permission === "default") {
      permission = await Notification.requestPermission();
      setNotificationPermission(permission);
    }

    if (permission !== "granted") {
      setNotificationMessage("需要先允许浏览器通知。");
      return;
    }

    try {
      new Notification("OpenClass 通知测试", {
        body: `课程活动、AI 结果和资料库变化会按 ${settings.quietStart}-${settings.quietEnd} 的免打扰时段过滤。`,
        tag: "openclass-notification-test",
      });
      setNotificationMessage("测试通知已发送。");
    } catch {
      setNotificationMessage("测试通知发送失败。");
    }
  }

  function handleSignOut() {
    window.localStorage.removeItem(OPENCLASS_AUTH_TOKEN_STORAGE_KEY);
    setCurrentUser(null);
    router.push("/login");
  }

  function handlePasswordSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formData = new FormData(event.currentTarget);
    const newPassword = String(formData.get("newPassword") ?? "");
    const confirmPassword = String(formData.get("confirmPassword") ?? "");

    if (newPassword.length < 8) {
      setPasswordMessage("新密码至少需要 8 位。");
      return;
    }

    if (newPassword !== confirmPassword) {
      setPasswordMessage("两次输入的新密码不一致。");
      return;
    }

    setPasswordMessage("当前版本还没有开放密码修改接口。");
  }

  function renderSettingsMenuItem(item: SettingsNavItem) {
    const Icon = item.icon;
    const isActive = activeSection === item.id;

    return (
      <button
        key={item.id}
        type="button"
        onClick={() => setActiveSection(item.id)}
        className={clsx(
          "flex min-h-9 w-full items-center gap-2 rounded-md border-l-2 px-3 py-2 text-left text-sm transition",
          isActive
            ? "border-sky-500 bg-stone-100 font-semibold text-stone-950"
            : "border-transparent text-stone-700 hover:bg-stone-100 hover:text-stone-950"
        )}
      >
        <Icon className="h-4 w-4 shrink-0 text-stone-500" />
        <span className="truncate">{item.label}</span>
      </button>
    );
  }

  function renderSaveFooter(options: { disabled?: boolean; helper?: ReactNode } = {}) {
    return (
      <div className="flex flex-col gap-3 border-t border-stone-200 pt-5 sm:flex-row sm:items-center sm:justify-between">
        <div className="min-h-5 text-sm">
          {saveStatus === "saved" ? (
            <span className="inline-flex items-center gap-1.5 font-medium text-emerald-700">
              <Check className="h-4 w-4" />
              {saveMessage ?? "已保存到本机"}
            </span>
          ) : null}
          {saveStatus === "error" ? (
            <span className="font-medium text-red-600">{saveMessage ?? "保存失败，请检查浏览器存储权限。"}</span>
          ) : null}
          {saveStatus === "idle" ? options.helper : null}
        </div>
        <button
          type="submit"
          disabled={options.disabled}
          className={clsx(
            "inline-flex h-10 items-center justify-center rounded-md px-4 text-sm font-semibold text-white shadow-sm transition",
            options.disabled
              ? "cursor-not-allowed bg-stone-300"
              : "bg-emerald-600 hover:bg-emerald-700"
          )}
        >
          保存设置
        </button>
      </div>
    );
  }

  function renderSectionHeader() {
    const section = sectionTitles[activeSection];

    return (
      <div className="mb-6 border-b border-stone-200 pb-4">
        <p className="text-xs font-semibold uppercase tracking-[0.18em] text-stone-400">{section.eyebrow}</p>
        <h2 className="mt-1 text-2xl font-semibold tracking-tight text-stone-950">{section.title}</h2>
      </div>
    );
  }

  function renderProfileSection() {
    return (
      <form className="max-w-5xl space-y-7" onSubmit={handleSave}>
        <section className={clsx(settingSectionClass, "grid gap-5 lg:grid-cols-[minmax(0,1fr)_22rem]")}>
          <div className="space-y-5">
            <div className="flex flex-col gap-4 sm:flex-row sm:items-center">
              <Image
                src={avatarUrl}
                alt="用户头像"
                className="h-20 w-20 rounded-full border-4 border-white bg-stone-200 shadow-[0_16px_34px_rgba(15,23,42,0.08)]"
                width={80}
                height={80}
                unoptimized
              />
              <div className="min-w-0">
                <h3 className="truncate text-lg font-semibold text-stone-950">
                  {settings.displayName || DEFAULT_PROFILE_SETTINGS.displayName}
                </h3>
                <p className="mt-1 text-sm text-stone-500">@{settings.handle || DEFAULT_PROFILE_SETTINGS.handle}</p>
                <p className="mt-2 text-xs font-medium text-stone-500">公开链接：{publicProfileUrl}</p>
              </div>
            </div>

            <div>
              <div className="mb-2 flex items-center justify-between gap-3 text-sm">
                <span className="font-semibold text-stone-950">资料完整度</span>
                <span className="text-stone-500">{profileCompleteness}%</span>
              </div>
              <div className="h-2 overflow-hidden rounded-full bg-stone-200">
                <div className="h-full rounded-full bg-emerald-500" style={{ width: `${profileCompleteness}%` }} />
              </div>
            </div>

            <SegmentedSetting
              label="公开范围"
              value={settings.profileVisibility}
              options={[
                { value: "private", label: "仅自己" },
                { value: "workspace", label: "工作区" },
                { value: "public", label: "公开" },
              ]}
              onChange={(value) => updateSetting("profileVisibility", value as ProfileSettings["profileVisibility"])}
            />
          </div>

          <div className="rounded-md border border-stone-200 bg-white p-4">
            <div className="mb-3 flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.14em] text-stone-400">
              <Eye className="h-3.5 w-3.5" />
              Preview
            </div>
            <div className="flex items-start gap-3">
              <Image
                src={avatarUrl}
                alt=""
                className="h-12 w-12 rounded-full border border-stone-200 bg-stone-100"
                width={48}
                height={48}
                unoptimized
              />
              <div className="min-w-0">
                <p className="truncate text-base font-semibold text-stone-950">
                  {settings.displayName || DEFAULT_PROFILE_SETTINGS.displayName}
                </p>
                <p className="mt-0.5 truncate text-sm text-stone-500">@{settings.handle || DEFAULT_PROFILE_SETTINGS.handle}</p>
              </div>
            </div>
            <p className="mt-4 line-clamp-3 text-sm leading-6 text-stone-600">{settings.bio || "还没有填写个人简介。"}</p>
            <div className="mt-4 space-y-2 text-xs text-stone-500">
              {settings.location ? (
                <p className="flex items-center gap-2">
                  <MapPin className="h-3.5 w-3.5" />
                  {settings.location}
                </p>
              ) : null}
              {settings.company ? (
                <p className="flex items-center gap-2">
                  <Building2 className="h-3.5 w-3.5" />
                  {settings.company}
                </p>
              ) : null}
              {settings.website ? (
                <p className="flex items-center gap-2">
                  <Globe2 className="h-3.5 w-3.5" />
                  {settings.website}
                </p>
              ) : null}
              {settings.showPublicEmail && (settings.publicEmail || userPublicEmail(currentUser)) ? (
                <p className="flex items-center gap-2">
                  <AtSign className="h-3.5 w-3.5" />
                  {settings.publicEmail || userPublicEmail(currentUser)}
                </p>
              ) : null}
            </div>
            <div className="mt-4 flex flex-wrap gap-2 text-xs font-semibold text-stone-600">
              {settings.showRepositoriesOnProfile ? <span>{repositoryCount} repositories</span> : null}
              {settings.showStarsOnProfile ? <span>{favoriteCount} stars</span> : null}
            </div>
          </div>
        </section>

        <div className="grid max-w-3xl gap-5 sm:grid-cols-2">
          <label className="block">
            <span className="flex items-center justify-between gap-3 text-sm font-semibold text-stone-950">
              姓名
              <span className="text-xs font-medium text-stone-400">{settings.displayName.length}/40</span>
            </span>
            <input
              className={`${settingsInputClass} mt-2`}
              maxLength={40}
              value={settings.displayName}
              onChange={(event) => updateSetting("displayName", event.target.value)}
            />
          </label>

          <label className="block">
            <span className="block text-sm font-semibold text-stone-950">用户名</span>
            <input
              className={clsx(`${settingsInputClass} mt-2`, !isHandleValid && "border-red-300 focus:border-red-500 focus:ring-red-100")}
              maxLength={32}
              value={settings.handle}
              onChange={(event) =>
                updateSetting(
                  "handle",
                  event.target.value
                    .toLowerCase()
                    .replace(/[^a-z0-9-]/g, "-")
                    .replace(/-+/g, "-")
                )
              }
            />
            <span className={clsx("mt-2 block text-xs leading-5", isHandleValid ? "text-stone-500" : "text-red-600")}>
              3-32 位小写字母、数字或连字符。
            </span>
          </label>
        </div>

        <label className="block max-w-3xl">
          <span className="block text-sm font-semibold text-stone-950">公开电子邮件</span>
          <select
            className={`${settingsInputClass} mt-2 max-w-xl`}
            value={settings.publicEmail}
            onChange={(event) => updateSetting("publicEmail", event.target.value)}
          >
            <option value="">不公开</option>
            {userPublicEmail(currentUser) ? <option value={userPublicEmail(currentUser)}>{userPublicEmail(currentUser)}</option> : null}
          </select>
        </label>

        <label className="block max-w-3xl">
          <span className="flex items-center justify-between gap-3 text-sm font-semibold text-stone-950">
            个人简介
            <span className="text-xs font-medium text-stone-400">{settings.bio.length}/160</span>
          </span>
          <textarea
            className={`${settingsInputClass} mt-2 min-h-28 resize-y leading-6`}
            maxLength={160}
            value={settings.bio}
            onChange={(event) => updateSetting("bio", event.target.value)}
            placeholder="请简单介绍一下你自己。"
          />
        </label>

        <label className="block max-w-3xl">
          <span className="block text-sm font-semibold text-stone-950">学习方向</span>
          <input
            className={`${settingsInputClass} mt-2`}
            value={settings.learningFocus}
            onChange={(event) => updateSetting("learningFocus", event.target.value)}
          />
        </label>

        <div className="grid max-w-3xl gap-5 sm:grid-cols-2">
          <label className="block">
            <span className="block text-sm font-semibold text-stone-950">URL</span>
            <input
              className={`${settingsInputClass} mt-2`}
              value={settings.website}
              onChange={(event) => updateSetting("website", event.target.value)}
              placeholder="https://openclass.local/profile"
            />
          </label>

          <label className="block">
            <span className="block text-sm font-semibold text-stone-950">地点</span>
            <input
              className={`${settingsInputClass} mt-2`}
              value={settings.location}
              onChange={(event) => updateSetting("location", event.target.value)}
              placeholder="Shanghai"
            />
          </label>
        </div>

        <label className="block max-w-3xl">
          <span className="block text-sm font-semibold text-stone-950">机构</span>
          <input
            className={`${settingsInputClass} mt-2 max-w-xl`}
            value={settings.company}
            onChange={(event) => updateSetting("company", event.target.value)}
          />
        </label>

        <div className="max-w-3xl">
          <h3 className="text-sm font-semibold text-stone-950">社交账号</h3>
          <div className="mt-2 space-y-2">
            {settings.socialLinks.map((link, index) => (
              <div key={index} className="flex max-w-2xl items-center gap-2">
                <LinkIcon className="h-4 w-4 shrink-0 text-stone-500" />
                <input
                  className={settingsInputClass}
                  value={link}
                  onChange={(event) => updateSocialLink(index, event.target.value)}
                  placeholder={`链接到社交个人资料 ${index + 1}`}
                />
              </div>
            ))}
          </div>
        </div>

        <section className="max-w-3xl space-y-5 border-y border-stone-200 py-5">
          <ToggleSetting
            title="公开邮箱"
            description="在公开资料中显示已选择的邮箱。"
            enabled={settings.showPublicEmail}
            onChange={(value) => updateSetting("showPublicEmail", value)}
          />
          <ToggleSetting
            title="公开社交账号"
            description="在个人主页展示社交链接。"
            enabled={settings.showSocialLinks}
            onChange={(value) => updateSetting("showSocialLinks", value)}
          />
          <ToggleSetting
            title="展示个人项目"
            description="在个人主页侧栏显示 repositories 数量。"
            enabled={settings.showRepositoriesOnProfile}
            onChange={(value) => updateSetting("showRepositoriesOnProfile", value)}
          />
          <ToggleSetting
            title="展示 Stars 收藏"
            description="在个人主页侧栏显示收藏课程数量。"
            enabled={settings.showStarsOnProfile}
            onChange={(value) => updateSetting("showStarsOnProfile", value)}
          />
        </section>

        {renderSaveFooter({
          disabled: !isHandleValid,
          helper: (
            <span className="text-stone-500">
              {isHandleValid ? `公开资料会预览到 ${publicProfileUrl}` : "请先修正用户名。"}
            </span>
          ),
        })}
      </form>
    );
  }

  function renderAccountSection() {
    return (
      <div className="max-w-3xl space-y-7">
        <section className={settingSectionClass}>
          {isLoadingUser ? (
            <div className="inline-flex items-center gap-2 text-sm text-stone-500">
              <LoaderCircle className="h-4 w-4 animate-spin" />
              正在读取账户信息
            </div>
          ) : currentUser ? (
            <div className="grid gap-4 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-center">
              <div className="min-w-0">
                <p className="truncate text-lg font-semibold text-stone-950">{userAccountLabel(currentUser)}</p>
                <p className="mt-1 text-sm text-stone-500">
                  {currentUser.role === "admin" ? "管理员" : "普通用户"} · 创建于 {formatAccountDate(currentUser.created_at)}
                </p>
              </div>
              <button
                type="button"
                onClick={handleSignOut}
                className="inline-flex h-10 items-center justify-center rounded-md border border-stone-200 bg-white px-4 text-sm font-semibold text-stone-700 transition hover:border-stone-300 hover:text-stone-950"
              >
                退出登录
              </button>
            </div>
          ) : (
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <p className="text-sm text-stone-600">{userError || "当前没有登录账户。"}</p>
              <Link
                href="/login"
                className="inline-flex h-10 items-center justify-center rounded-md bg-stone-950 px-4 text-sm font-semibold text-white"
              >
                去登录
              </Link>
            </div>
          )}
        </section>

        <section className="grid gap-3 sm:grid-cols-3">
          <MetricTile label="个人项目" value={repositoryCount} />
          <MetricTile label="Stars 收藏" value={favoriteCount} />
          <MetricTile label="上次登录" value={currentUser?.last_login_at ? formatAccountDate(currentUser.last_login_at) : "未记录"} />
        </section>

        <section className="space-y-3">
          <h3 className="text-sm font-semibold text-stone-950">快捷入口</h3>
          <div className="flex flex-wrap gap-2">
            <Link
              href="/studio"
              className="inline-flex h-10 items-center rounded-md border border-stone-200 bg-white px-4 text-sm font-semibold text-stone-700 transition hover:border-stone-300 hover:text-stone-950"
            >
              课程工作台
            </Link>
            {currentUser?.role === "admin" ? (
              <Link
                href="/admin"
                className="inline-flex h-10 items-center rounded-md border border-stone-200 bg-white px-4 text-sm font-semibold text-stone-700 transition hover:border-stone-300 hover:text-stone-950"
              >
                管理后台
              </Link>
            ) : null}
          </div>
        </section>
      </div>
    );
  }

  function renderAppearanceSection() {
    return (
      <form className="max-w-5xl space-y-7" onSubmit={handleSave}>
        <section className={clsx(settingSectionClass, "grid gap-5 lg:grid-cols-[minmax(0,1fr)_22rem]")}>
          <div>
            <h3 className="text-sm font-semibold text-stone-950">主题</h3>
            <div className="mt-3 grid gap-3 sm:grid-cols-3">
              <ThemeOption
                active={settings.theme === "system"}
                label="跟随系统"
                swatches={["#f8fafc", "#0f172a", "#0ea5e9"]}
                onClick={() => updateSetting("theme", "system")}
              />
              <ThemeOption
                active={settings.theme === "light"}
                label="明亮"
                swatches={["#ffffff", "#e7eef8", "#2563eb"]}
                onClick={() => updateSetting("theme", "light")}
              />
              <ThemeOption
                active={settings.theme === "warm"}
                label="暖色纸面"
                swatches={["#fff7ed", "#f5d0a5", "#059669"]}
                onClick={() => updateSetting("theme", "warm")}
              />
            </div>
          </div>

          <div
            className={clsx(
              "rounded-md border p-4",
              settings.theme === "light" && "border-slate-200 bg-slate-50",
              settings.theme !== "light" && "border-stone-200 bg-white",
              settings.highContrast && "border-stone-950"
            )}
          >
            <div className="mb-3 flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.14em] text-stone-400">
              <MonitorSmartphone className="h-3.5 w-3.5" />
              Live Preview
            </div>
            <div className={clsx("rounded-md border bg-white p-3", settings.density === "compact" ? "space-y-2" : "space-y-3")}>
              <p className={clsx("font-semibold text-stone-950", settings.largeText ? "text-base" : "text-sm")}>
                {settings.displayName || DEFAULT_PROFILE_SETTINGS.displayName}
              </p>
              <p className={clsx("leading-6 text-stone-600", settings.largeText ? "text-sm" : "text-xs")}>
                {settings.bio || DEFAULT_PROFILE_SETTINGS.bio}
              </p>
              <div className="flex flex-wrap gap-1.5">
                <span className="rounded-full bg-sky-50 px-2 py-1 text-[11px] font-semibold text-sky-700">AI 课程</span>
                <span className="rounded-full bg-emerald-50 px-2 py-1 text-[11px] font-semibold text-emerald-700">讲义</span>
              </div>
            </div>
          </div>
        </section>

        <section className="grid gap-6 border-b border-stone-200 pb-7 lg:grid-cols-2">
          <SegmentedSetting
            label="界面密度"
            value={settings.density}
            options={[
              { value: "comfortable", label: "舒展" },
              { value: "compact", label: "紧凑" },
            ]}
            onChange={(value) => updateSetting("density", value as ProfileSettings["density"])}
          />

          <SegmentedSetting
            label="默认入口"
            value={settings.startPage}
            options={[
              { value: "home", label: "学习首页" },
              { value: "studio", label: "课程工作台" },
              { value: "profile", label: "个人主页" },
            ]}
            onChange={(value) => updateSetting("startPage", value as ProfileSettings["startPage"])}
          />
        </section>

        <section className="space-y-5 border-b border-stone-200 pb-7">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <h3 className="text-sm font-semibold text-stone-950">阅读辅助</h3>
              <p className="mt-1 text-sm text-stone-500">这些选项会立即作用于个人主页和设置页。</p>
            </div>
            <button
              type="button"
              onClick={handleResetAppearance}
              className="inline-flex h-9 items-center justify-center gap-2 rounded-md border border-stone-200 bg-white px-3 text-sm font-semibold text-stone-700 transition hover:border-stone-300 hover:text-stone-950"
            >
              <RotateCcw className="h-4 w-4" />
              重置外观
            </button>
          </div>
          <ToggleSetting
            title="减少动态效果"
            description="降低动画和过渡频率。"
            enabled={settings.reduceMotion}
            onChange={(value) => updateSetting("reduceMotion", value)}
          />
          <ToggleSetting
            title="高对比度"
            description="提高文字和边框对比度。"
            enabled={settings.highContrast}
            onChange={(value) => updateSetting("highContrast", value)}
          />
          <ToggleSetting
            title="放大正文"
            description="提高课程列表和设置页正文尺寸。"
            enabled={settings.largeText}
            onChange={(value) => updateSetting("largeText", value)}
          />
          <ToggleSetting
            title="突出键盘焦点"
            description="让键盘导航状态更容易被看见。"
            enabled={settings.visibleFocus}
            onChange={(value) => updateSetting("visibleFocus", value)}
          />
        </section>

        {renderSaveFooter({ helper: <span className="text-stone-500">外观会立即预览，保存后下次打开仍然保留。</span> })}
      </form>
    );
  }

  function renderNotificationsSection() {
    return (
      <form className="max-w-5xl space-y-7" onSubmit={handleSave}>
        <section className={clsx(settingSectionClass, "grid gap-4 lg:grid-cols-[minmax(0,1fr)_18rem]")}>
          <div>
            <h3 className="text-sm font-semibold text-stone-950">通知中心</h3>
            <p className="mt-1 text-sm leading-6 text-stone-500">
              已开启 {enabledNotificationCount} 个通知来源，免打扰时段为 {settings.quietStart}-{settings.quietEnd}。
            </p>
          </div>
          <div className="rounded-md border border-stone-200 bg-white px-4 py-3">
            <p className="text-xs font-semibold text-stone-500">浏览器权限</p>
            <p className="mt-1 text-sm font-semibold text-stone-950">
              {notificationPermission === "unsupported"
                ? "不支持"
                : notificationPermission === "granted"
                  ? "已允许"
                  : notificationPermission === "denied"
                    ? "已拒绝"
                    : "未询问"}
            </p>
          </div>
        </section>

        <section className="grid gap-6 border-b border-stone-200 pb-7 lg:grid-cols-[minmax(0,1fr)_18rem]">
          <div className="space-y-5">
            <ToggleSetting
              title="桌面通知"
              description="允许浏览器弹出课程和 AI 任务提醒。"
              enabled={settings.browserNotifications}
              onChange={(value) => updateSetting("browserNotifications", value)}
            />
            <SegmentedSetting
              label="提醒频率"
              value={settings.notificationFrequency}
              options={[
                { value: "instant", label: "即时" },
                { value: "hourly", label: "每小时" },
                { value: "daily", label: "每日摘要" },
              ]}
              onChange={(value) => updateSetting("notificationFrequency", value as ProfileSettings["notificationFrequency"])}
            />
          </div>

          <div className="space-y-2">
            <button
              type="button"
              onClick={() => void handleRequestNotificationPermission()}
              className="inline-flex h-10 w-full items-center justify-center gap-2 rounded-md border border-stone-200 bg-white px-4 text-sm font-semibold text-stone-700 transition hover:border-stone-300 hover:text-stone-950"
            >
              <Bell className="h-4 w-4" />
              允许浏览器通知
            </button>
            <button
              type="button"
              onClick={() => void handleSendTestNotification()}
              className="inline-flex h-10 w-full items-center justify-center gap-2 rounded-md bg-stone-950 px-4 text-sm font-semibold text-white transition hover:bg-stone-800"
            >
              <Send className="h-4 w-4" />
              发送测试通知
            </button>
            {notificationMessage ? <p className="text-sm leading-6 text-stone-500">{notificationMessage}</p> : null}
          </div>
        </section>

        <section className="grid gap-5 lg:grid-cols-2">
          <ToggleSetting
            title="课程活动"
            description="课程包、讲义和资料更新。"
            enabled={settings.courseActivityNotifications}
            onChange={(value) => updateSetting("courseActivityNotifications", value)}
          />
          <ToggleSetting
            title="每周摘要"
            description="Stars 收藏和个人项目的周报。"
            enabled={settings.weeklyDigestNotifications}
            onChange={(value) => updateSetting("weeklyDigestNotifications", value)}
          />
          <ToggleSetting
            title="AI 生成结果"
            description="长任务结束后提醒。"
            enabled={settings.aiResultNotifications}
            onChange={(value) => updateSetting("aiResultNotifications", value)}
          />
          <ToggleSetting
            title="资料库变化"
            description="上传资料解析完成或失败。"
            enabled={settings.resourceNotifications}
            onChange={(value) => updateSetting("resourceNotifications", value)}
          />
        </section>

        <section className="grid gap-4 border-y border-stone-200 py-6 sm:grid-cols-2">
          <label className="block">
            <span className="flex items-center gap-2 text-sm font-semibold text-stone-950">
              <Clock3 className="h-4 w-4 text-stone-500" />
              免打扰开始
            </span>
            <input
              type="time"
              className={`${settingsInputClass} mt-2`}
              value={settings.quietStart}
              onChange={(event) => updateSetting("quietStart", event.target.value)}
            />
          </label>
          <label className="block">
            <span className="flex items-center gap-2 text-sm font-semibold text-stone-950">
              <Clock3 className="h-4 w-4 text-stone-500" />
              免打扰结束
            </span>
            <input
              type="time"
              className={`${settingsInputClass} mt-2`}
              value={settings.quietEnd}
              onChange={(event) => updateSetting("quietEnd", event.target.value)}
            />
          </label>
        </section>

        {renderSaveFooter({ helper: <span className="text-stone-500">浏览器权限由当前浏览器控制，其他通知偏好保存到本机。</span> })}
      </form>
    );
  }

  function renderBillingSection() {
    return (
      <div className="max-w-3xl space-y-7">
        <section className={settingSectionClass}>
          <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <p className="text-lg font-semibold text-stone-950">OpenClass 本地工作台</p>
              <p className="mt-1 text-sm text-stone-500">Community License</p>
            </div>
            <span className="inline-flex w-fit items-center rounded-full border border-emerald-200 bg-emerald-50 px-3 py-1 text-xs font-semibold text-emerald-700">
              已启用
            </span>
          </div>
        </section>

        <section className="space-y-4">
          <UsageMeter label="个人项目" value={repositoryCount} max={50} />
          <UsageMeter label="Stars 收藏" value={favoriteCount} max={100} />
          <UsageMeter label="本地席位" value={currentUser ? 1 : 0} max={1} />
        </section>

        <section className="flex flex-wrap gap-2">
          <Link
            href="/admin"
            className="inline-flex h-10 items-center rounded-md border border-stone-200 bg-white px-4 text-sm font-semibold text-stone-700 transition hover:border-stone-300 hover:text-stone-950"
          >
            查看后台
          </Link>
          <Link
            href="/"
            className="inline-flex h-10 items-center rounded-md border border-stone-200 bg-white px-4 text-sm font-semibold text-stone-700 transition hover:border-stone-300 hover:text-stone-950"
          >
            返回学习首页
          </Link>
        </section>
      </div>
    );
  }

  function renderEmailSection() {
    return (
      <form className="max-w-3xl space-y-6" onSubmit={handleSave}>
        <section className={settingSectionClass}>
          <label className="block">
            <span className="block text-sm font-semibold text-stone-950">主邮箱</span>
            <input className={`${settingsInputClass} mt-2 max-w-xl bg-stone-50`} value={userPublicEmail(currentUser) || "未绑定邮箱"} readOnly />
          </label>
        </section>

        <ToggleSetting
          title="课程摘要邮件"
          description="每周发送学习项目和 Stars 收藏变化。"
          enabled={settings.emailCourseDigest}
          onChange={(value) => updateSetting("emailCourseDigest", value)}
        />
        <ToggleSetting
          title="AI 任务邮件"
          description="长时间生成任务完成后发送。"
          enabled={settings.emailAiSummary}
          onChange={(value) => updateSetting("emailAiSummary", value)}
        />
        <ToggleSetting
          title="安全邮件"
          description="登录、权限和账户安全变化。"
          enabled={settings.emailSecurityAlerts}
          onChange={(value) => updateSetting("emailSecurityAlerts", value)}
        />

        {renderSaveFooter()}
      </form>
    );
  }

  function renderPasswordSection() {
    return (
      <form className="max-w-3xl space-y-6" onSubmit={handlePasswordSubmit}>
        <label className="block">
          <span className="block text-sm font-semibold text-stone-950">当前密码</span>
          <input className={`${settingsInputClass} mt-2 max-w-xl`} type="password" name="currentPassword" autoComplete="current-password" />
        </label>
        <label className="block">
          <span className="block text-sm font-semibold text-stone-950">新密码</span>
          <input className={`${settingsInputClass} mt-2 max-w-xl`} type="password" name="newPassword" autoComplete="new-password" />
        </label>
        <label className="block">
          <span className="block text-sm font-semibold text-stone-950">确认新密码</span>
          <input className={`${settingsInputClass} mt-2 max-w-xl`} type="password" name="confirmPassword" autoComplete="new-password" />
        </label>

        <section className="flex flex-col gap-3 border-y border-stone-200 py-5 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex items-start gap-3">
            <LockKeyhole className="mt-0.5 h-4 w-4 text-stone-500" />
            <div>
              <p className="text-sm font-semibold text-stone-950">会话保护</p>
              <p className="mt-1 text-sm leading-6 text-stone-500">当前登录令牌仅保存在本机浏览器中。</p>
            </div>
          </div>
          <button
            type="button"
            onClick={handleSignOut}
            className="inline-flex h-10 items-center justify-center rounded-md border border-stone-200 bg-white px-4 text-sm font-semibold text-stone-700 transition hover:border-stone-300 hover:text-stone-950"
          >
            退出所有本机会话
          </button>
        </section>

        {passwordMessage ? <p className="text-sm font-medium text-stone-600">{passwordMessage}</p> : null}

        <button
          type="submit"
          className="inline-flex h-10 items-center justify-center rounded-md bg-stone-950 px-4 text-sm font-semibold text-white transition hover:bg-stone-800"
        >
          更新密码
        </button>
      </form>
    );
  }

  function renderModelsSection() {
    return (
      <form className="max-w-3xl space-y-7" onSubmit={handleSave}>
        <section className={settingSectionClass}>
          {isLoadingModels ? (
            <div className="inline-flex items-center gap-2 text-sm text-stone-500">
              <LoaderCircle className="h-4 w-4 animate-spin" />
              正在读取模型
            </div>
          ) : modelError ? (
            <p className="text-sm text-red-600">{modelError}</p>
          ) : (
            <div className="grid gap-3 sm:grid-cols-2">
              <ModelSummary title="文本默认" model={defaultTextModel} />
              <ModelSummary title="实时语音默认" model={defaultRealtimeModel} />
            </div>
          )}
        </section>

        <label className="block">
          <span className="block text-sm font-semibold text-stone-950">偏好文本模型</span>
          <select
            className={`${settingsInputClass} mt-2 max-w-xl`}
            value={settings.preferredTextModel}
            onChange={(event) => updateSetting("preferredTextModel", event.target.value)}
          >
            <option value="auto">自动</option>
            {textModels.map((model) => (
              <option key={modelValue(model)} value={modelValue(model)}>
                {modelLabel(model)}
              </option>
            ))}
          </select>
        </label>

        <label className="block">
          <span className="block text-sm font-semibold text-stone-950">偏好实时语音模型</span>
          <select
            className={`${settingsInputClass} mt-2 max-w-xl`}
            value={settings.preferredRealtimeModel}
            onChange={(event) => updateSetting("preferredRealtimeModel", event.target.value)}
          >
            <option value="auto">自动</option>
            {realtimeModels.map((model) => (
              <option key={modelValue(model)} value={modelValue(model)}>
                {modelLabel(model)}
              </option>
            ))}
          </select>
        </label>

        <section className="space-y-2">
          {(modelCatalog?.text ?? []).slice(0, 6).map((model) => (
            <ModelRow key={modelValue(model)} model={model} />
          ))}
        </section>

        {renderSaveFooter()}
      </form>
    );
  }

  function renderSecuritySection() {
    return (
      <form className="max-w-3xl space-y-6" onSubmit={handleSave}>
        <ToggleSetting
          title="隐藏本地路径"
          description="在个人页和导出信息中避免展示本机文件路径。"
          enabled={settings.hideLocalPaths}
          onChange={(value) => updateSetting("hideLocalPaths", value)}
        />
        <ToggleSetting
          title="打开外部链接前确认"
          description="跳转到外部学习资源前显示确认。"
          enabled={settings.confirmExternalLinks}
          onChange={(value) => updateSetting("confirmExternalLinks", value)}
        />
        <ToggleSetting
          title="关闭页面时清理会话"
          description="离开浏览器后清除本机登录令牌。"
          enabled={settings.clearSessionOnExit}
          onChange={(value) => updateSetting("clearSessionOnExit", value)}
        />
        <ToggleSetting
          title="允许课程被发现"
          description="公开资料页展示课程项目摘要。"
          enabled={settings.allowCourseDiscovery}
          onChange={(value) => updateSetting("allowCourseDiscovery", value)}
        />
        {renderSaveFooter()}
      </form>
    );
  }

  function renderActiveSection() {
    switch (activeSection) {
      case "profile":
        return renderProfileSection();
      case "account":
        return renderAccountSection();
      case "appearance":
        return renderAppearanceSection();
      case "notifications":
        return renderNotificationsSection();
      case "billing":
        return renderBillingSection();
      case "email":
        return renderEmailSection();
      case "password":
        return renderPasswordSection();
      case "models":
        return renderModelsSection();
      case "security":
        return renderSecuritySection();
      default:
        return renderProfileSection();
    }
  }

  return (
    <div className="mx-auto grid max-w-6xl gap-6 px-4 py-6 sm:px-6 md:grid-cols-[15rem_minmax(0,1fr)]">
      <aside className="h-fit md:sticky md:top-28">
        <nav className="space-y-5" aria-label="个人设置导航">
          <section className="space-y-1">{settingsPrimaryNav.map((item) => renderSettingsMenuItem(item))}</section>
          <section className="border-t border-stone-200 pt-4">
            <h2 className="mb-2 px-3 text-xs font-semibold text-stone-500">使用权</h2>
            <div className="space-y-1">{settingsAccountNav.map((item) => renderSettingsMenuItem(item))}</div>
          </section>
        </nav>
      </aside>

      <section className="min-w-0">
        {renderSectionHeader()}
        {renderActiveSection()}
      </section>
    </div>
  );
}

function ToggleSetting({
  description,
  enabled,
  onChange,
  title,
}: {
  description: string;
  enabled: boolean;
  onChange: (enabled: boolean) => void;
  title: string;
}) {
  return (
    <section className="flex flex-col gap-3 border-b border-stone-200 pb-5 sm:flex-row sm:items-center sm:justify-between">
      <div className="min-w-0">
        <h3 className="text-sm font-semibold text-stone-950">{title}</h3>
        <p className="mt-1 text-sm leading-6 text-stone-500">{description}</p>
      </div>
      <button
        type="button"
        role="switch"
        aria-checked={enabled}
        onClick={() => onChange(!enabled)}
        className={clsx(
          "relative h-6 w-11 shrink-0 rounded-full transition focus:outline-none focus:ring-2 focus:ring-sky-200",
          enabled ? "bg-emerald-600" : "bg-stone-300"
        )}
      >
        <span
          className={clsx(
            "absolute top-0.5 h-5 w-5 rounded-full bg-white shadow-sm transition",
            enabled ? "left-5" : "left-0.5"
          )}
        />
      </button>
    </section>
  );
}

function ThemeOption({
  active,
  label,
  onClick,
  swatches,
}: {
  active: boolean;
  label: string;
  onClick: () => void;
  swatches: string[];
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={clsx(
        "flex min-h-20 items-center justify-between gap-3 rounded-md border bg-white px-4 py-3 text-left transition",
        active ? "border-sky-500 ring-2 ring-sky-100" : "border-stone-200 hover:border-stone-300"
      )}
    >
      <span className="text-sm font-semibold text-stone-950">{label}</span>
      <span className="flex -space-x-1.5">
        {swatches.map((color) => (
          <span key={color} className="h-6 w-6 rounded-full border border-white shadow-sm" style={{ backgroundColor: color }} />
        ))}
      </span>
    </button>
  );
}

function SegmentedSetting({
  label,
  onChange,
  options,
  value,
}: {
  label: string;
  onChange: (value: string) => void;
  options: Array<{ label: string; value: string }>;
  value: string;
}) {
  return (
    <div>
      <h3 className="text-sm font-semibold text-stone-950">{label}</h3>
      <div className="mt-3 inline-flex flex-wrap rounded-md border border-stone-200 bg-white p-1">
        {options.map((option) => (
          <button
            key={option.value}
            type="button"
            onClick={() => onChange(option.value)}
            className={clsx(
              "min-h-8 rounded px-3 text-sm font-semibold transition",
              value === option.value ? "bg-stone-950 text-white" : "text-stone-600 hover:bg-stone-100 hover:text-stone-950"
            )}
          >
            {option.label}
          </button>
        ))}
      </div>
    </div>
  );
}

function MetricTile({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="rounded-md border border-stone-200 bg-white px-4 py-3">
      <p className="text-xs font-semibold text-stone-500">{label}</p>
      <p className="mt-1 break-words text-lg font-semibold text-stone-950">{value}</p>
    </div>
  );
}

function UsageMeter({ label, max, value }: { label: string; max: number; value: number }) {
  const percentage = Math.min(100, Math.round((value / max) * 100));

  return (
    <div>
      <div className="mb-2 flex items-center justify-between gap-3 text-sm">
        <span className="font-semibold text-stone-950">{label}</span>
        <span className="text-stone-500">
          {value}/{max}
        </span>
      </div>
      <div className="h-2 overflow-hidden rounded-full bg-stone-200">
        <div className="h-full rounded-full bg-sky-500" style={{ width: `${percentage}%` }} />
      </div>
    </div>
  );
}

function ModelSummary({ model, title }: { model: AIModelOption | null; title: string }) {
  return (
    <div className="rounded-md border border-stone-200 bg-white px-4 py-3">
      <p className="text-xs font-semibold text-stone-500">{title}</p>
      <p className="mt-1 truncate text-sm font-semibold text-stone-950">{model ? modelLabel(model) : "未配置"}</p>
    </div>
  );
}

function ModelRow({ model }: { model: AIModelOption }) {
  return (
    <div className="flex items-center justify-between gap-3 rounded-md border border-stone-200 bg-white px-4 py-3">
      <div className="min-w-0">
        <p className="truncate text-sm font-semibold text-stone-950">{modelLabel(model)}</p>
        <p className="mt-1 text-xs text-stone-500">{model.capability === "realtime" ? "实时语音" : "文本生成"}</p>
      </div>
      <span
        className={clsx(
          "shrink-0 rounded-full px-2.5 py-1 text-xs font-semibold",
          model.configured ? "bg-emerald-50 text-emerald-700" : "bg-stone-100 text-stone-500"
        )}
      >
        {model.configured ? "已配置" : "未配置"}
      </span>
    </div>
  );
}
