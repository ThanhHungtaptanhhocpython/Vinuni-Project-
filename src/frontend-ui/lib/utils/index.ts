/* Formatting — MASTER §20
   Dates follow the user's timezone; relative strings always carry an absolute
   `title`. Numbers use vi-VN. Nothing here invents a value it wasn't given. */

import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";
import { useAuthStore } from "@/stores";

/** shadcn/ui convention — merge conditional class names, letting later
    Tailwind classes win over earlier conflicting ones. */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

function timezone(): string | undefined {
  return useAuthStore.getState().user?.timezone;
}

export function formatAbsolute(iso: string | null | undefined): string {
  if (!iso) return "";
  return new Date(iso).toLocaleString("vi-VN", { timeZone: timezone() });
}

export function formatTime(iso: string | null | undefined): string {
  if (!iso) return "";
  return new Date(iso).toLocaleTimeString("vi-VN", {
    timeZone: timezone(),
    hour: "2-digit",
    minute: "2-digit",
  });
}

const UNITS: [limitSeconds: number, divisor: number, unit: Intl.RelativeTimeFormatUnit][] = [
  [60, 1, "second"],
  [3600, 60, "minute"],
  [86_400, 3600, "hour"],
  [604_800, 86_400, "day"],
  [2_629_800, 604_800, "week"],
  [31_557_600, 2_629_800, "month"],
  [Infinity, 31_557_600, "year"],
];

/** "2 phút trước" / "trong 3 giờ". Pair with `title={formatAbsolute(iso)}`. */
export function formatRelative(iso: string | null | undefined): string {
  if (!iso) return "";
  const deltaSeconds = (new Date(iso).getTime() - Date.now()) / 1000;
  const magnitude = Math.abs(deltaSeconds);
  const [, divisor, unit] = UNITS.find(([limit]) => magnitude < limit) ?? UNITS[UNITS.length - 1];
  const formatter = new Intl.RelativeTimeFormat("vi-VN", { numeric: "auto" });
  return formatter.format(Math.round(deltaSeconds / divisor), unit);
}

/** Seconds → "45s" / "12 phút 5s". Returns "—" when the backend sent nothing. */
export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "—";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const minutes = Math.floor(seconds / 60);
  const rest = Math.round(seconds % 60);
  if (minutes < 60) return rest ? `${minutes} phút ${rest}s` : `${minutes} phút`;
  const hours = Math.floor(minutes / 60);
  return `${hours} giờ ${minutes % 60} phút`;
}

/** Seconds → { value: number, unit: string, extra?: string } for aligned display.
    Returns null when backend sent nothing. */
export function parseDuration(seconds: number | null | undefined): { value: string; unit: string; extra?: string } | null {
  if (seconds === null || seconds === undefined) return null;
  if (seconds < 60) return { value: String(Math.round(seconds)), unit: "s" };
  const minutes = Math.floor(seconds / 60);
  const rest = Math.round(seconds % 60);
  if (minutes < 60) {
    return rest ? { value: String(minutes), unit: "phút", extra: `${rest}s` } : { value: String(minutes), unit: "phút" };
  }
  const hours = Math.floor(minutes / 60);
  const mins = minutes % 60;
  return mins > 0
    ? { value: String(hours), unit: "giờ", extra: `${mins} phút` }
    : { value: String(hours), unit: "giờ" };
}

/** Mili-giây → `"480ms"` / `"1.1s"` / `"24s"` / `"1 phút 5s"`.
 *
 *  Khác `formatDuration` ở trên: hàm kia nhận GIÂY và trả `"—"` khi rỗng. Ở đây
 *  trả về CHUỖI RỖNG, để chỗ gọi ẩn hẳn phần tử thay vì hiện một dấu gạch —
 *  §13.3: thiếu trường thì ẩn, không hiện chỗ trống.
 *
 *  Dưới 10 giây giữ một chữ số thập phân vì phần lớn thao tác của agent nằm
 *  trong khoảng đó, làm tròn hết thì mọi bước trông như nhau. */
export function formatDurationMs(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || Number.isNaN(ms)) return "";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  const seconds = ms / 1000;
  if (seconds < 10) return `${seconds.toFixed(1)}s`;
  if (seconds < 60) return `${Math.round(seconds)}s`;
  return formatDuration(seconds);
}

export function formatNumber(value: number): string {
  return new Intl.NumberFormat("vi-VN").format(value);
}

/** `YYYY-MM-DD` theo timezone của người dùng — chỉ để SO SÁNH ngày, không để
    hiện ra. `en-CA` là locale duy nhất `toLocaleDateString` trả sẵn đúng thứ
    tự năm-tháng-ngày, tránh phải tự ghép chuỗi từ `getFullYear`/`getMonth`
    (vốn đọc theo giờ máy chủ, không theo timezone của người dùng). */
function dayKey(iso: string): string {
  return new Date(iso).toLocaleDateString("en-CA", { timeZone: timezone() });
}

export function isSameDay(isoA: string, isoB: string): boolean {
  return dayKey(isoA) === dayKey(isoB);
}

/** Nhãn cho dải phân cách thời gian kiểu Messenger — hiện giữa hội thoại
 *  lâu lâu một lần, không phải trên từng tin nhắn.
 *
 *  Cùng ngày với `previousIso` (tin nhắn lần trước có hiện dải phân cách,
 *  KHÔNG PHẢI tin ngay trước trong mảng — một cụm tin nhắn liên tiếp không
 *  cần lặp lại ngày) → chỉ giờ: "14:32".
 *  Khác ngày → kèm theo ngày: "Hôm nay, 14:32" / "Hôm qua, 09:05" / tên thứ
 *  trong tuần nếu trong 6 ngày gần đây / ngày-tháng-năm đầy đủ nếu xa hơn. */
export function formatTimelineDivider(iso: string, previousIso?: string | null): string {
  const time = formatTime(iso);
  if (previousIso && isSameDay(iso, previousIso)) return time;

  const key = dayKey(iso);
  const now = new Date();
  const todayKey = dayKey(now.toISOString());
  if (key === todayKey) return `Hôm nay, ${time}`;

  const yesterday = new Date(now);
  yesterday.setDate(yesterday.getDate() - 1);
  if (key === dayKey(yesterday.toISOString())) return `Hôm qua, ${time}`;

  const diffDays = Math.round(
    (new Date(`${todayKey}T00:00:00Z`).getTime() - new Date(`${key}T00:00:00Z`).getTime()) / 86_400_000,
  );
  const date = new Date(iso);
  const dayLabel =
    diffDays > 0 && diffDays < 7
      ? date.toLocaleDateString("vi-VN", { weekday: "long", timeZone: timezone() })
      : date.toLocaleDateString("vi-VN", { day: "2-digit", month: "2-digit", year: "numeric", timeZone: timezone() });
  return `${dayLabel}, ${time}`;
}
