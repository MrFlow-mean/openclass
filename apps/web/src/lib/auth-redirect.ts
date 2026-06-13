const PRODUCT_HOME_PATH = "/";

export function loginRedirectPath(value: string | null | undefined) {
  if (!value || !value.startsWith("/") || value.startsWith("//")) {
    return PRODUCT_HOME_PATH;
  }
  if (value === "/login" || value.startsWith("/login?") || value === "/register" || value.startsWith("/register?")) {
    return PRODUCT_HOME_PATH;
  }
  return value;
}
