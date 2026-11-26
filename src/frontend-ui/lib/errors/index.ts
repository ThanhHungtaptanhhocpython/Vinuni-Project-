/* Error i18n — MASTER §14.3
   Never hardcode Vietnamese error strings in components.
   Look up by `code`; fall back to the server message. */

import errorsVi from "@/i18n/errors.vi.json";

export interface ErrorObject {
  code: string;
  message: string;
  trace_id?: string;
}

const dictionary = errorsVi as Record<string, string>;

/** Resolve a user-facing message for an error code.
 *
 * Nhóm `VAL_*` ưu tiên `error.message` CỤ THỂ backend trả (vd. "field
 * required", "not a valid email" — xem `detailToError` ở `lib/api/client.ts`)
 * thay vì luôn rơi về "Giá trị không hợp lệ." chung chung: dictionary không
 * phân biệt được người dùng thiếu trường nào hay gõ sai định dạng gì. Các mã
 * khác vẫn ưu tiên dictionary như cũ — message thô của chúng thường không
 * dành cho người dùng cuối. */
export function errorText(error: ErrorObject | null | undefined): string {
  if (!error) return dictionary.SYS_001;
  if (error.code.startsWith("VAL_") && error.message) return error.message;
  return dictionary[error.code] ?? error.message ?? dictionary.SYS_001;
}

/** Resolve by bare code — for tooltips and inline hints. */
export function errorTextByCode(code: string, fallback = ""): string {
  return dictionary[code] ?? fallback;
}

/** Narrow an unknown thrown value into an ErrorObject. */
export function toErrorObject(err: unknown): ErrorObject {
  if (err && typeof err === "object") {
    const e = err as { code?: unknown; message?: unknown; trace_id?: unknown };
    return {
      code: typeof e.code === "string" ? e.code : "SYS_001",
      message: typeof e.message === "string" ? e.message : dictionary.SYS_001,
      trace_id: typeof e.trace_id === "string" ? e.trace_id : undefined,
    };
  }
  return { code: "SYS_001", message: dictionary.SYS_001 };
}

/** Safely convert a backend error dict into an ErrorObject. */
export function traceErrorToErrorObject(error: Record<string, unknown> | null | undefined): ErrorObject | null {
  if (!error) return null;
  const code = typeof error.code === "string" ? error.code : "SYS_001";
  const message = typeof error.message === "string" ? error.message : (dictionary.SYS_001 ?? "Đã xảy ra lỗi không xác định.");
  return { code, message };
}
