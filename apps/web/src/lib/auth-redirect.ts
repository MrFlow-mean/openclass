const PRODUCT_HOME_PATH = "/";
const WORKBENCH_PATH = "/studio";

export function loginRedirectPath(value: string | null | undefined) {
  if (!value || !value.startsWith("/") || value.startsWith("//")) {
    return PRODUCT_HOME_PATH;
  }
  if (value === "/login" || value.startsWith("/login?") || value === "/register" || value.startsWith("/register?")) {
    return PRODUCT_HOME_PATH;
  }
  if (value === WORKBENCH_PATH || value.startsWith(`${WORKBENCH_PATH}?`)) {
    return PRODUCT_HOME_PATH;
  }
  return value;
}
