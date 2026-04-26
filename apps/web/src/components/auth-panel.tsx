"use client";

import clsx from "clsx";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useEffect, useState } from "react";
import { ArrowLeft, LoaderCircle, LockKeyhole, Mail, ShieldCheck, UserPlus } from "lucide-react";

import { OPENCLASS_AUTH_TOKEN_STORAGE_KEY, api } from "@/lib/api";
import type { UserView } from "@/types";

type AuthPanelProps = {
  initialMode: "register" | "login";
};

function storeSession(token: string) {
  window.localStorage.setItem(OPENCLASS_AUTH_TOKEN_STORAGE_KEY, token);
}

export function AuthPanel({ initialMode }: AuthPanelProps) {
  const router = useRouter();
  const [mode, setMode] = useState(initialMode);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [currentUser, setCurrentUser] = useState<UserView | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [isCheckingSession, setIsCheckingSession] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let disposed = false;

    async function loadUser() {
      try {
        const user = await api.getCurrentUser();
        if (!disposed) {
          setCurrentUser(user);
        }
      } catch {
        if (!disposed) {
          setCurrentUser(null);
        }
      } finally {
        if (!disposed) {
          setIsCheckingSession(false);
        }
      }
    }

    void loadUser();

    return () => {
      disposed = true;
    };
  }, []);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setIsLoading(true);
    setError(null);

    try {
      const payload = mode === "register" ? await api.register(email, password) : await api.login(email, password);
      storeSession(payload.token);
      setCurrentUser(payload.user);
      router.push(payload.user.role === "admin" ? "/admin" : "/");
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : "操作失败");
    } finally {
      setIsLoading(false);
    }
  }

  function handleLogout() {
    window.localStorage.removeItem(OPENCLASS_AUTH_TOKEN_STORAGE_KEY);
    setCurrentUser(null);
  }

  const isRegister = mode === "register";

  return (
    <main className="min-h-screen bg-[#f7f5ef] text-stone-950">
      <header className="border-b border-stone-200 bg-[#fcfbf8]/92 backdrop-blur">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-4 py-3 sm:px-6">
          <Link
            href="/"
            className="inline-flex items-center gap-2 rounded-md px-2 py-2 text-sm font-semibold text-stone-700 transition hover:bg-stone-100 hover:text-stone-950"
          >
            <ArrowLeft className="h-4 w-4" />
            Learning Hub
          </Link>
          <Link
            href="/admin"
            className="inline-flex items-center gap-2 rounded-md border border-stone-200 bg-white px-3 py-2 text-sm font-semibold text-stone-700 transition hover:border-stone-300 hover:text-stone-950"
          >
            <ShieldCheck className="h-4 w-4" />
            管理后台
          </Link>
        </div>
      </header>

      <section className="mx-auto grid max-w-6xl gap-8 px-4 py-10 sm:px-6 lg:grid-cols-[minmax(0,1fr)_28rem] lg:py-16">
        <div className="flex min-h-[24rem] flex-col justify-center">
          <p className="text-[11px] font-semibold uppercase tracking-[0.28em] text-stone-400">OpenClass Account</p>
          <h1 className="mt-3 max-w-2xl text-4xl font-semibold tracking-tight text-stone-950 sm:text-5xl">
            用一个邮箱进入你的 AI 课程工作台
          </h1>
          <p className="mt-5 max-w-2xl text-base leading-8 text-stone-600">
            当前版本只保留最轻量的账号入口：邮箱和密码即可注册。第一个注册账号会自动成为管理员，负责进入后台查看用户与课程数据。
          </p>
          <div className="mt-8 grid max-w-2xl gap-3 sm:grid-cols-3">
            {["邮箱注册", "密码登录", "首位管理员"].map((item) => (
              <div key={item} className="rounded-lg border border-stone-200 bg-white/82 p-4 text-sm font-semibold text-stone-700">
                {item}
              </div>
            ))}
          </div>
        </div>

        <div className="h-fit rounded-lg border border-stone-200 bg-white p-6 shadow-[0_18px_50px_rgba(15,23,42,0.07)]">
          {isCheckingSession ? (
            <div className="flex min-h-72 items-center justify-center text-stone-500">
              <LoaderCircle className="mr-2 h-4 w-4 animate-spin" />
              正在检查登录状态
            </div>
          ) : currentUser ? (
            <div>
              <div className="flex h-11 w-11 items-center justify-center rounded-full bg-stone-950 text-white">
                <ShieldCheck className="h-5 w-5" />
              </div>
              <h2 className="mt-5 text-xl font-semibold text-stone-950">已登录</h2>
              <p className="mt-2 text-sm text-stone-600">{currentUser.email}</p>
              <p className="mt-1 text-xs text-stone-500">权限：{currentUser.role === "admin" ? "管理员" : "普通用户"}</p>
              <div className="mt-6 flex flex-col gap-2 sm:flex-row">
                <Link
                  href={currentUser.role === "admin" ? "/admin" : "/"}
                  className="inline-flex h-10 items-center justify-center rounded-md bg-stone-950 px-4 text-sm font-semibold text-white transition hover:bg-stone-800"
                >
                  {currentUser.role === "admin" ? "进入后台" : "回到主页"}
                </Link>
                <button
                  type="button"
                  onClick={handleLogout}
                  className="inline-flex h-10 items-center justify-center rounded-md border border-stone-200 bg-white px-4 text-sm font-semibold text-stone-700 transition hover:border-stone-300 hover:text-stone-950"
                >
                  退出登录
                </button>
              </div>
            </div>
          ) : (
            <>
              <div className="flex gap-2 rounded-lg bg-stone-100 p-1">
                {(["register", "login"] as const).map((item) => (
                  <button
                    key={item}
                    type="button"
                    onClick={() => {
                      setMode(item);
                      setError(null);
                    }}
                    className={clsx(
                      "h-10 flex-1 rounded-md text-sm font-semibold transition",
                      mode === item ? "bg-white text-stone-950 shadow-sm" : "text-stone-500 hover:text-stone-950"
                    )}
                  >
                    {item === "register" ? "注册" : "登录"}
                  </button>
                ))}
              </div>

              <form onSubmit={(event) => void handleSubmit(event)} className="mt-6 space-y-4">
                <label className="block">
                  <span className="text-sm font-semibold text-stone-950">邮箱</span>
                  <div className="relative mt-2">
                    <Mail className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-stone-400" />
                    <input
                      type="email"
                      value={email}
                      onChange={(event) => setEmail(event.target.value)}
                      required
                      autoComplete="email"
                      className="w-full rounded-md border border-stone-300 bg-white py-2.5 pl-9 pr-3 text-sm outline-none transition placeholder:text-stone-400 focus:border-sky-500 focus:ring-2 focus:ring-sky-100"
                      placeholder="you@example.com"
                    />
                  </div>
                </label>

                <label className="block">
                  <span className="text-sm font-semibold text-stone-950">密码</span>
                  <div className="relative mt-2">
                    <LockKeyhole className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-stone-400" />
                    <input
                      type="password"
                      value={password}
                      onChange={(event) => setPassword(event.target.value)}
                      required
                      minLength={8}
                      autoComplete={isRegister ? "new-password" : "current-password"}
                      className="w-full rounded-md border border-stone-300 bg-white py-2.5 pl-9 pr-3 text-sm outline-none transition placeholder:text-stone-400 focus:border-sky-500 focus:ring-2 focus:ring-sky-100"
                      placeholder="至少 8 位"
                    />
                  </div>
                </label>

                {error ? (
                  <div className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">{error}</div>
                ) : null}

                <button
                  type="submit"
                  disabled={isLoading}
                  className="inline-flex h-11 w-full items-center justify-center gap-2 rounded-md bg-stone-950 px-4 text-sm font-semibold text-white transition hover:bg-stone-800 disabled:cursor-wait disabled:opacity-70"
                >
                  {isLoading ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <UserPlus className="h-4 w-4" />}
                  {isRegister ? "创建账号" : "登录"}
                </button>
              </form>
            </>
          )}
        </div>
      </section>
    </main>
  );
}

