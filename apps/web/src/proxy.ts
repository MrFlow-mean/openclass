import { NextResponse, type NextRequest } from "next/server";

const AUTH_COOKIE_NAME = "openclass.auth.token";
const GUEST_AUTH_COOKIE_NAME = "openclass.guest.auth.token";
const publicRoutes = ["/login", "/register", "/auth/callback", "/trending"];
const publicRoutePrefixes = ["/courses"];

function isPublicRoute(pathname: string) {
  return (
    publicRoutes.some((route) => pathname === route || pathname.startsWith(`${route}/`)) ||
    publicRoutePrefixes.some((route) => pathname === route || pathname.startsWith(`${route}/`))
  );
}

function safeNextPath(value: string | null) {
  if (!value || !value.startsWith("/") || value.startsWith("//")) {
    return "/";
  }
  if (value === "/login" || value.startsWith("/login?") || value === "/register" || value.startsWith("/register?")) {
    return "/";
  }
  if (value === "/studio" || value.startsWith("/studio?")) {
    return "/";
  }
  return value;
}

export function proxy(request: NextRequest) {
  const { pathname, search } = request.nextUrl;
  const token = request.cookies.get(AUTH_COOKIE_NAME)?.value;
  const guestToken = request.cookies.get(GUEST_AUTH_COOKIE_NAME)?.value;
  const hasSession = Boolean(token || guestToken);

  if (!token && pathname.startsWith("/admin")) {
    const loginUrl = new URL("/login", request.url);
    loginUrl.searchParams.set("next", `${pathname}${search}`);
    return NextResponse.redirect(loginUrl);
  }

  if (!hasSession && !isPublicRoute(pathname)) {
    const loginUrl = new URL("/login", request.url);
    loginUrl.searchParams.set("next", `${pathname}${search}`);
    return NextResponse.redirect(loginUrl);
  }

  if (hasSession && (pathname === "/login" || pathname === "/register")) {
    return NextResponse.redirect(new URL(safeNextPath(request.nextUrl.searchParams.get("next")), request.url));
  }

  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!api|_next/static|_next/image|favicon.ico|.*\\..*).*)"],
};
