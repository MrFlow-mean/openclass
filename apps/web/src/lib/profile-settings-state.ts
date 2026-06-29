export const INTERFACE_LANGUAGE_OPTIONS = [
  { value: "zh-CN", label: "简体中文" },
  { value: "en", label: "English" },
] as const;

export type InterfaceLanguage = (typeof INTERFACE_LANGUAGE_OPTIONS)[number]["value"];

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
  startPage: "home" | "profile";
  /** UI locale; changing it updates `<html lang>` via InterfaceLanguageProvider. */
  interfaceLanguage: InterfaceLanguage;
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

export const PROFILE_SETTINGS_STORAGE_KEY = "openclass.profile.settings";
export const PROFILE_SETTINGS_CHANGED_EVENT = "openclass.profile.settings.changed";

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
  learningFocus: "概念解释、资料扩讲、练习训练",
  socialLinks: ["", "", "", ""],
  theme: "system",
  density: "comfortable",
  startPage: "home",
  interfaceLanguage: "zh-CN",
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

function parseInterfaceLanguage(value: unknown): InterfaceLanguage {
  return value === "en" ? "en" : "zh-CN";
}

function parseStartPage(value: unknown): ProfileSettings["startPage"] {
  return value === "profile" ? "profile" : "home";
}

export function normalizeProfileSettings(raw: Partial<ProfileSettings> | null): ProfileSettings {
  const next = {
    ...DEFAULT_PROFILE_SETTINGS,
    ...(raw ?? {}),
  };

  return {
    ...next,
    startPage: parseStartPage(raw?.startPage),
    interfaceLanguage: parseInterfaceLanguage(raw?.interfaceLanguage),
    socialLinks: Array.from({ length: 4 }, (_, index) =>
      typeof raw?.socialLinks?.[index] === "string"
        ? (raw.socialLinks![index] as string)
        : DEFAULT_PROFILE_SETTINGS.socialLinks[index]
    ),
  };
}

export function readStoredProfileSettings(): ProfileSettings {
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
