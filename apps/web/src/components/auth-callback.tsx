"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { LoaderCircle, ShieldCheck, TriangleAlert } from "lucide-react";

import { storeAuthToken } from "@/lib/api";

type AuthCallbackProps = {
  error?: string | null;
  nextPath?: string | null;
  token?: string | null;
};

function safeNextPath(value: string | null | undefined) {
  if (!value || !value.startsWith("/") || value.startsWith("//")) {
    return "/";
  }
  return value;
}

export function AuthCallback({ error, nextPath, token }: AuthCallbackProps) {
  const router = useRouter();
  const hasError = Boolean(error || !token);
  const message = error || (!token ? "第三方登录没有返回有效会话，请重新登录。" : "正在跳转到 OpenClass 工作台。");

  useEffect(() => {
    if (error || !token) {
      return;
    }
    storeAuthToken(token);
    router.replace(safeNextPath(nextPath));
  }, [error, nextPath, router, token]);

  return (
    <main className="flex min-h-screen items-center justify-center bg-[#f7f5ef] px-4 text-stone-950">
      <section className="w-full max-w-md rounded-lg border border-stone-200 bg-white p-6 text-center shadow-[0_24px_70px_rgba(15,23,42,0.12)]">
        <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-lg bg-stone-950 text-white">
          {hasError ? <TriangleAlert className="h-5 w-5" /> : <ShieldCheck className="h-5 w-5" />}
        </div>
        <h1 className="mt-5 text-2xl font-semibold tracking-tight">{hasError ? "登录未完成" : "登录成功"}</h1>
        <p className="mt-3 text-sm leading-6 text-stone-600">
          {message}
        </p>
        {!hasError ? <LoaderCircle className="mx-auto mt-5 h-5 w-5 animate-spin text-stone-400" /> : null}
        {hasError ? (
          <Link
            href="/login"
            className="mt-6 inline-flex h-10 items-center justify-center rounded-md bg-stone-950 px-4 text-sm font-semibold text-white transition hover:bg-stone-800"
          >
            返回登录
          </Link>
        ) : null}
      </section>
    </main>
  );
}
