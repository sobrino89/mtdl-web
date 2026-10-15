// Texts of the sign-in, kept pure for tests: an API error of mtdl_web/auth.py as a sentence for people.
import { ApiError, errorMessage } from "./api";
import { t, type Key } from "./i18n";

const KNOWN = ["setup_code_wrong", "password_wrong", "already_set_up", "setup_required", "password_from_env", "cross_site"];

export function accessError(e: unknown): string {
  if (e instanceof ApiError) {
    const detail = e.detail && typeof e.detail === "object" ? (e.detail as Record<string, unknown>) : {};
    if (e.code === "too_many_attempts") {
      return t("auth.err.too_many_attempts", { n: Math.max(1, Math.ceil(Number(detail.retry_after ?? 60) / 60)) });
    }
    if (e.code === "password_too_short") return t("auth.err.password_too_short", { min: Number(detail.min ?? 8) });
    if (e.code && KNOWN.includes(e.code)) return t(`auth.err.${e.code}` as Key);
  }
  return errorMessage(e);
}
