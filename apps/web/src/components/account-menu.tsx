"use client";

import clsx from "clsx";
import Image from "next/image";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { ChevronDown, LogOut, ShieldCheck, UserRound } from "lucide-react";

import { useInterfaceLanguage } from "@/contexts/interface-language-context";

import { api, clearAuthToken } from "@/lib/api";
import { userAccountLabel, userDisplayName, userInitial } from "@/lib/account";
import type { UserView } from "@/types";

export function AccountMenu({ compact = false }: { compact?: boolean }) {
  const router = useRouter();
  const { texts: txt } = useInterfaceLanguage();
  const m = txt.accountMenu;
  const [user, setUser] = useState<UserView | null>(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    let disposed = false;

    async function loadUser() {
      try {
        const currentUser = await api.getCurrentUser();
        if (!disposed) {
          setUser(currentUser);
        }
      } catch {
        if (!disposed) {
          setUser(null);
        }
      }
    }

    void loadUser();

    return () => {
      disposed = true;
    };
  }, []);

  async function handleLogout() {
    try {
      await api.logout();
    } catch {
      // Local cleanup still happens if the server session is already gone.
    }
    clearAuthToken();
    setOpen(false);
    router.replace("/login");
  }

  const isGuest = user?.role === "guest";

  function handleLoginToSave() {
    setOpen(false);
    const next = `${window.location.pathname}${window.location.search}` || "/";
    router.push(`/login?next=${encodeURIComponent(next)}`);
  }

  return (
    <div className="relative" data-account-menu-root>
      <button
        type="button"
        onClick={() => setOpen((current) => !current)}
        className={clsx(
          "inline-flex h-10 items-center gap-2 rounded-xl border border-stone-200 bg-white px-2 text-sm font-semibold text-stone-700 shadow-sm transition hover:border-stone-300 hover:text-stone-950",
          compact ? "w-10 justify-center px-0" : "max-w-[16rem]"
        )}
        aria-haspopup="menu"
        aria-expanded={open}
        title={userAccountLabel(user)}
      >
        <span className="flex h-7 w-7 shrink-0 items-center justify-center overflow-hidden rounded-full bg-stone-950 text-xs font-bold text-white">
          {user?.avatar_url ? (
            <Image src={user.avatar_url} alt="" width={28} height={28} className="h-full w-full object-cover" unoptimized />
          ) : (
            userInitial(user)
          )}
        </span>
        {!compact ? <span className="truncate">{userDisplayName(user)}</span> : null}
        {!compact ? <ChevronDown className="h-4 w-4 shrink-0 text-stone-400" /> : null}
      </button>

      {open ? (
        <div
          role="menu"
          className="absolute right-0 z-[120] mt-2 w-80 overflow-hidden rounded-lg border border-stone-200 bg-white text-left shadow-[0_24px_60px_rgba(15,23,42,0.16)]"
        >
          <div className="border-b border-stone-200 p-4">
            <div className="flex items-start gap-3">
              <span className="flex h-11 w-11 shrink-0 items-center justify-center overflow-hidden rounded-full bg-stone-950 text-sm font-bold text-white">
                {user?.avatar_url ? (
                  <Image src={user.avatar_url} alt="" width={44} height={44} className="h-full w-full object-cover" unoptimized />
                ) : (
                  userInitial(user)
                )}
              </span>
              <div className="min-w-0">
                <p className="truncate text-sm font-semibold text-stone-950">{userDisplayName(user)}</p>
                <p className="mt-1 break-all text-xs text-stone-500">{user ? userAccountLabel(user) : m.loadingAccount}</p>
                {user ? <p className="mt-1 break-all font-mono text-[11px] text-stone-400">ID {user.id}</p> : null}
              </div>
            </div>
            {user ? (
              <div className="mt-3 flex flex-wrap gap-1.5">
                <span className="rounded-full border border-stone-200 bg-stone-50 px-2 py-0.5 text-[11px] font-semibold text-stone-600">
                  {user.role === "guest" ? m.guestBadge : user.role === "admin" ? m.adminBadge : m.memberBadge}
                </span>
                {user.auth_identities.map((identity) => (
                  <span
                    key={`${identity.provider}:${identity.email ?? identity.created_at}`}
                    className="rounded-full border border-sky-200 bg-sky-50 px-2 py-0.5 text-[11px] font-semibold text-sky-700"
                  >
                    {identity.provider_label}
                  </span>
                ))}
              </div>
            ) : null}
          </div>

          <div className="p-2">
            {isGuest ? (
              <button
                type="button"
                className="flex w-full items-center gap-2 rounded-md px-3 py-2 text-left text-sm font-semibold text-stone-700 transition hover:bg-stone-100 hover:text-stone-950"
                role="menuitem"
                onClick={handleLoginToSave}
              >
                <UserRound className="h-4 w-4 text-stone-400" />
                {m.loginToSave}
              </button>
            ) : (
              <Link
                href="/profile?tab=settings"
                className="flex items-center gap-2 rounded-md px-3 py-2 text-sm font-semibold text-stone-700 transition hover:bg-stone-100 hover:text-stone-950"
                role="menuitem"
                onClick={() => setOpen(false)}
              >
                <UserRound className="h-4 w-4 text-stone-400" />
                {m.profileLink}
              </Link>
            )}
            {user?.role === "admin" ? (
              <Link
                href="/admin"
                className="flex items-center gap-2 rounded-md px-3 py-2 text-sm font-semibold text-stone-700 transition hover:bg-stone-100 hover:text-stone-950"
                role="menuitem"
                onClick={() => setOpen(false)}
              >
                <ShieldCheck className="h-4 w-4 text-stone-400" />
                {m.adminLink}
              </Link>
            ) : null}
            <button
              type="button"
                onClick={() => void handleLogout()}
              className="flex w-full items-center gap-2 rounded-md px-3 py-2 text-left text-sm font-semibold text-rose-700 transition hover:bg-rose-50"
              role="menuitem"
            >
              <LogOut className="h-4 w-4" />
              {isGuest ? m.signOutGuest : m.signOut}
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
