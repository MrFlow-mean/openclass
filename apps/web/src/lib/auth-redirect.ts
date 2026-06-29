const PRODUCT_HOME_PATH = "/";
const PRODUCT_LANDING_REDIRECTS = ["/admin", "/following", "/profile"];

function matchesPath(path: string, target: string) {
  return path === target || path.startsWith(`${target}?`) || path.startsWith(`${target}/`);
}

export function loginRedirectPath(value: string | null | undefined) {
  if (!value || !value.startsWith("/") || value.startsWith("//")) {
    return PRODUCT_HOME_PATH;
  }
  if (value === "/login" || value.startsWith("/login?") || value === "/register" || value.startsWith("/register?")) {
    return PRODUCT_HOME_PATH;
  }
  if (PRODUCT_LANDING_REDIRECTS.some((target) => matchesPath(value, target))) {
    return PRODUCT_HOME_PATH;
  }
  return value;
}
