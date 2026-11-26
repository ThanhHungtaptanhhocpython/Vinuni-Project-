"use client";
/* P03 — TỔNG QUAN
   Nghiệp vụ đổi: một tài khoản giờ ghép nối được NHIỀU robot, nên trang này
   không còn là trạng thái của một con robot cố định nữa mà mở đầu bằng danh
   sách các robot đã kết nối — trả lời câu hỏi mới "tôi đang có những robot
   nào?" trước khi hỏi "nó đang thế nào?" (câu đó giờ thuộc về trang
   `/robots/[robotId]` của từng con).

   Ghép nối qua UI (`POST /robots`) tạm tắt — danh sách chỉ hiện robot đã có
   trong SQL store. */

import { useState, useEffect, useCallback } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Wifi, WifiOff } from "lucide-react";

import { RobotStatusBadge } from "@/components/robot-state/RobotStatusBadge";
import { StatusBadge, RUN_STATUS_STATS_META } from "@/components/common/StatusBadge";
import { EmptyState } from "@/components/common/EmptyState";
import { ErrorPanel } from "@/components/common/ErrorMessage";
import { Skeleton } from "@/components/common/Skeleton";

/* Thống kê giờ đến từ `GET /robots/{id}/stats` — tính từ bảng `runs` trên
   Postgres. Không dùng robot mặc định: khi tài khoản chưa ghép nối robot nào
   thì bỏ qua thống kê và hiện empty state ở danh sách robot. */
import {
  connectRobot,
  disconnectRobot,
  fetchRobots,
  fetchRobotStats,
  type RobotStats,
  type DailyActivity,
} from "@/lib/api/robots";
import { robotModelLabel } from "@/lib/robot/models";
import { fetchRuns } from "@/lib/api/runs";
import { fetchSchedules } from "@/lib/api/schedules";
import { errorText, toErrorObject, type ErrorObject } from "@/lib/errors";
import { formatRelative, formatAbsolute, formatDuration } from "@/lib/format";
import { toast, useAuthStore, type Robot, type Run, type Schedule } from "@/stores";
import { ROBOT_CONNECTION_CHANGED_EVENT } from "@/lib/robot-connection-state";

const ROBOT_REFETCH_MS = 15_000;

/* ---------------------------------------------------------------- section */

/** Tiêu đề mốc thời gian. Trang này kể một câu chuyện theo thứ tự: robot đang
    thế nào → sắp có việc gì → vừa qua đã làm những gì. Không có hàng ô thống kê
    riêng: mỗi con số nằm trong đúng khối sở hữu nó, xuất hiện một lần. */
function SectionHeading({ title, meta }: { title: string; meta?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3 flex-wrap pt-1">
      <h2 className="eyebrow">{title}</h2>
      {meta && <span className="text-[13.5px] text-haze-500">{meta}</span>}
    </div>
  );
}

/* ---------------------------------------------------------------- thống kê */

/** Vòng tỉ lệ. Cùng một con số nhưng đọc được bằng ngoại vi: cung tròn đầy tới
    đâu thấy ngay tới đó, không phải so sánh chữ số. Dựng bằng SVG chứ không kéo
    Recharts vào — một cung tròn không đáng một thư viện biểu đồ (§16). */
function Gauge({ value, label }: { value: number; label: string }) {
  const size = 92;
  const stroke = 7;
  const radius = (size - stroke) / 2;
  const circumference = 2 * Math.PI * radius;
  const pct = Math.min(Math.max(value, 0), 1);
  const tone = pct >= 0.9 ? "var(--jade-400)" : pct >= 0.75 ? "var(--amber-400)" : "var(--halt-500)";

  return (
    <div className="flex flex-col items-center gap-1.5 shrink-0">
      <div className="relative" style={{ width: size, height: size }}>
        <svg width={size} height={size} className="-rotate-90" aria-hidden="true">
          <circle
            cx={size / 2}
            cy={size / 2}
            r={radius}
            fill="none"
            stroke="var(--ink-700)"
            strokeWidth={stroke}
          />
          <circle
            cx={size / 2}
            cy={size / 2}
            r={radius}
            fill="none"
            stroke={tone}
            strokeWidth={stroke}
            strokeLinecap="round"
            strokeDasharray={`${pct * circumference} ${circumference}`}
          />
        </svg>
        <span className="absolute inset-0 flex items-center justify-center font-mono text-[22px] tabular-nums text-paper-50">
          {Math.round(pct * 100)}%
        </span>
      </div>
      <span className="eyebrow">{label}</span>
    </div>
  );
}

/** Cột hoạt động theo ngày. Cho thấy nhịp làm việc — hôm nào chạy nhiều, hôm
    nào nghỉ — thứ mà một con số tổng không nói được. */
function ActivityBars({ data, days }: { data: DailyActivity[]; days: number }) {
  if (data.length === 0) return null;
  const max = Math.max(...data.map((d) => d.count), 1);
  const total = data.reduce((sum, d) => sum + d.count, 0);

  return (
    <div className="flex-1 min-w-0">
      <div className="flex items-baseline justify-between gap-2 mb-2">
        <span className="eyebrow">Hoạt động {days} ngày</span>
        <span className="data text-[12.5px] text-haze-500">{total} lượt</span>
      </div>

      <div className="flex items-end gap-1 h-[68px]" role="img" aria-label={`Hoạt động ${days} ngày, tổng ${total} lượt`}>
        {data.map((day) => (
          <div
            key={day.date}
            className="flex-1 min-w-0 bg-arc-500/35 hover:bg-arc-500/55 rounded-sm transition-colors duration-[120ms]"
            style={{ height: `${Math.max((day.count / max) * 100, 4)}%` }}
            title={`${new Date(day.date).toLocaleDateString("vi-VN", { day: "2-digit", month: "2-digit" })}: ${day.count} lượt`}
          />
        ))}
      </div>

      <div className="flex justify-between mt-1.5">
        <span className="eyebrow normal-case tracking-normal">
          {new Date(data[0].date).toLocaleDateString("vi-VN", { day: "2-digit", month: "2-digit" })}
        </span>
        <span className="eyebrow normal-case tracking-normal">Hôm nay</span>
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------- outcome */

/* Khoá là TRẠNG THÁI LƯỢT CHẠY có thật trong database, không phải outcome của
   episode (SUCCESS / SAFETY_ABORT…). Hệ thống chưa lưu episode nào xuống đĩa,
   nên gán nhãn đó là bịa ra một tầng dữ liệu không tồn tại. Màu theo khoá ngữ
   nghĩa §4.1 — bảng dùng chung với trang Robot, xem `StatusBadge.tsx`. */
const STATUS_META = RUN_STATUS_STATS_META;

/** Kết quả nhiệm vụ 30 ngày — thống kê đúng nghĩa dashboard: một thanh xếp
    chồng cho thấy tỉ lệ, kèm số đếm từng loại. Màu theo khoá ngữ nghĩa §4.1. */
function StatsPanel({ stats }: { stats: RobotStats }) {
  const total = stats.outcomes.reduce((sum, o) => sum + o.count, 0);
  if (total === 0) return null;

  return (
    <section className="panel p-4 space-y-4">
      <div className="flex items-center gap-6 flex-wrap sm:flex-nowrap">
        {/* Chưa lượt nào kết thúc thì KHÔNG vẽ vòng tròn: `success_rate` là
            `null`, và vẽ 0% sẽ đọc ra "hỏng hết" trong khi thật ra là "chưa
            xong cái nào". */}
        {stats.success_rate !== null && <Gauge value={stats.success_rate} label="Thành công" />}
        <ActivityBars data={stats.daily_activity} days={stats.window_days} />
      </div>

      <div className="border-t border-[var(--line)] pt-3">
      <div className="flex items-baseline justify-between gap-3 mb-3">
        <h2 className="eyebrow">Phân loại kết quả</h2>
        <span className="data text-[12.5px] text-haze-500">{total} lượt</span>
      </div>

      <div className="flex h-2 rounded-full overflow-hidden bg-ink-700 mb-3">
        {stats.outcomes
          .filter((o) => o.count > 0)
          .map((o) => (
            <div
              key={o.status}
              className={STATUS_META[o.status]?.bar ?? "bg-haze-500"}
              style={{ width: `${(o.count / total) * 100}%` }}
              title={`${STATUS_META[o.status]?.label ?? o.status}: ${o.count}`}
            />
          ))}
      </div>

      <ul className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-1">
        {stats.outcomes.map((o) => {
          const meta = STATUS_META[o.status];
          return (
            <li key={o.status} className="flex items-center gap-2 text-[13.5px]">
              <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${meta?.bar ?? "bg-haze-500"}`} />
              <span className="flex-1 text-haze-300 truncate">{meta?.label ?? o.status}</span>
              <span className={`data text-[12.5px] ${o.count > 0 ? (meta?.text ?? "") : "text-haze-500"}`}>
                {o.count}
              </span>
            </li>
          );
        })}
      </ul>
      </div>
    </section>
  );
}

/* ---------------------------------------------------------------- lists */

function RecentRuns({ runs }: { runs: Run[] }) {
  const router = useRouter();
  return (
    <section className="panel p-4">
      {runs.length === 0 ? (
        <p className="text-[14px] text-haze-500 py-4">Chưa có lượt chạy nào.</p>
      ) : (
        <div className="flex flex-col">
          {runs.slice(0, 5).map((run) => (
            <button
              key={run.id}
              type="button"
              onClick={() => router.push(`/runs/${run.id}`)}
              className="flex items-center gap-3 h-12 px-3 rounded-lg hover:bg-ink-700 transition-colors duration-[120ms] text-left focus-ring cursor-pointer"
            >
              <StatusBadge kind="source" value={run.source} />
              <span className="flex-1 min-w-0 text-[14px] text-paper-50 truncate">{run.input_text}</span>
              <span className="shrink-0 text-[12.5px] text-haze-500 tabular-nums w-[60px] text-right">
                {formatDuration(run.duration_s)}
              </span>
              <StatusBadge kind="run" value={run.status} />
            </button>
          ))}
        </div>
      )}
    </section>
  );
}

function UpcomingSchedules({ schedules }: { schedules: Schedule[] }) {
  const upcoming = schedules.filter((s) => s.enabled).slice(0, 5);
  return (
    <section className="panel p-4">
      {upcoming.length === 0 ? (
        <p className="text-[14px] text-haze-500 py-4">Chưa có lịch nào đang bật.</p>
      ) : (
        <div className="flex flex-col gap-1">
          {upcoming.map((schedule) => (
            <Link
              key={schedule.id}
              href={`/schedules/${schedule.id}`}
              className="px-3 py-2 rounded-lg hover:bg-ink-700 transition-colors duration-[120ms] focus-ring"
            >
              <p className="text-[14px] text-paper-50 font-medium truncate">{schedule.name}</p>
              {/* Câu tiếng Việt, không bao giờ là chuỗi cron thô (P07) */}
              <p className="text-[13px] text-haze-500 mt-0.5">{schedule.cron_description}</p>
              {schedule.next_run_at && (
                <p className="data text-[12.5px] text-haze-500 mt-1" title={formatAbsolute(schedule.next_run_at)}>
                  Lần tới: {formatRelative(schedule.next_run_at)}
                </p>
              )}
            </Link>
          ))}
        </div>
      )}
    </section>
  );
}

/* ---------------------------------------------------------------- robots */

/** Một thẻ trong danh sách robot đã liên kết. Bấm vào card: nếu robot chưa
    online thì kết nối trước, rồi mới mở trang chi tiết. */
function RobotCard({ robot, onConnectionChanged }: { robot: Robot; onConnectionChanged: (robot?: Robot) => void }) {
  const router = useRouter();
  const selectRobot = useAuthStore((s) => s.selectRobot);
  const [busy, setBusy] = useState(false);
  const online = robot.connection_status === "ONLINE";

  const connectIfNeeded = async (): Promise<boolean> => {
    if (online) return true;
    setBusy(true);
    try {
      const updated = await connectRobot(robot.id);
      toast.success("Đã kết nối robot.");
      onConnectionChanged(updated);
      return true;
    } catch (err) {
      toast.error(errorText(toErrorObject(err)));
      return false;
    } finally {
      setBusy(false);
    }
  };

  const openRobot = async () => {
    selectRobot(robot.id);
    const ok = await connectIfNeeded();
    if (ok) router.push(`/robots/${robot.id}`);
  };

  const toggleConnection = async (event: React.MouseEvent<HTMLButtonElement>) => {
    event.stopPropagation();
    setBusy(true);
    try {
      if (online) {
        const updated = await disconnectRobot(robot.id);
        toast.success("Đã ngắt kết nối robot.");
        onConnectionChanged(updated);
      } else {
        const updated = await connectRobot(robot.id);
        toast.success("Đã kết nối robot.");
        onConnectionChanged(updated);
      }
    } catch (err) {
      toast.error(errorText(toErrorObject(err)));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={openRobot}
      onKeyDown={(event) => {
        if (event.key !== "Enter" && event.key !== " ") return;
        event.preventDefault();
        void openRobot();
      }}
      aria-disabled={busy}
      className={`panel p-4 flex items-center gap-3 hover:bg-ink-700 transition-colors duration-[120ms] text-left focus-ring ${
        busy ? "opacity-75 cursor-wait" : "cursor-pointer"
      }`}
    >
      <span className="min-w-0 flex-1">
        <div className="flex items-center gap-2.5 flex-wrap">
          <span className="text-[14px] text-paper-50 font-medium truncate">{robot.name}</span>
          <RobotStatusBadge robotStatus={robot.robot_status} connectionStatus={robot.connection_status} size="sm" />
        </div>
        <p className="eyebrow mt-1.5 flex flex-wrap gap-2">
          <span className="data">{robot.id}</span>
          <span aria-hidden="true">·</span>
          <span>{robotModelLabel(robot.model)}</span>
        </p>
      </span>
      {robot.health.has_error && (
        <span className="data text-[11.5px] shrink-0 px-2 py-0.5 rounded bg-halt-500/12 text-halt-500">
          {robot.health.error_flags[0]}
        </span>
      )}
      <button
        type="button"
        onClick={toggleConnection}
        disabled={busy}
        className={`shrink-0 h-9 px-3 rounded-lg inline-flex items-center gap-1.5 text-[13px] font-semibold focus-ring disabled:opacity-60 disabled:cursor-not-allowed ${
          online
            ? "bg-ink-800 text-paper-50 border border-[var(--line-strong)] hover:bg-ink-700"
            : "bg-arc-500 text-ink-950 hover:bg-arc-400"
        }`}
      >
        {online ? <WifiOff size={14} /> : <Wifi size={14} />}
        {busy ? "Đang xử lý" : online ? "Ngắt kết nối" : "Kết nối robot"}
      </button>
    </div>
  );
}

/** Danh sách robot đã kết nối — thay cho khối trạng thái servo của một robot
    cố định trước đây. Ghép nối qua UI tạm tắt. */
function RobotListPanel({
  robots,
  onConnectionChanged,
}: {
  robots: Robot[];
  onConnectionChanged: (robot?: Robot) => void;
}) {
  return (
    <section className="space-y-3">
      {robots.length === 0 ? (
        <EmptyState message="Chưa có robot trong hệ thống." />
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
          {robots.map((robot) => (
            <RobotCard key={robot.id} robot={robot} onConnectionChanged={onConnectionChanged} />
          ))}
        </div>
      )}
    </section>
  );
}

/* ---------------------------------------------------------------- page */

export default function DashboardPage() {
  const [robots, setRobots] = useState<Robot[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [schedules, setSchedules] = useState<Schedule[]>([]);
  const [stats, setStats] = useState<RobotStats | null>(null);
  const [robotState, setRobotState] = useState<"loading" | "ready" | "error">("loading");
  const [robotError, setRobotError] = useState<ErrorObject | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;

    const load = () => {
      fetchRobots()
        .then((data) => {
          if (cancelled) return;
          setRobots(data);
          if (data.length === 0) setStats(null);
          setRobotState("ready");
        })
        .catch((err) => {
          if (cancelled) return;
          setRobotError(toErrorObject(err));
          setRobotState("error");
        });
    };

    load();
    const interval = setInterval(load, ROBOT_REFETCH_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [attempt]);

  /* Đọc lại danh sách robot sau khi thêm mới thay vì tự chèn robot vừa thêm
     vào state — trạng thái "server đã xác nhận ghép nối" phải đến từ chính
     server, không phải suy ra từ response của lệnh thêm. */
  const refreshRobots = useCallback((updatedRobot?: Robot) => {
    if (updatedRobot) {
      setRobots((rows) => rows.map((robot) => (robot.id === updatedRobot.id ? updatedRobot : robot)));
      return;
    }
    fetchRobots()
      .then((data) => setRobots(data))
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    const onRobotChanged = (event: Event) => {
      const robot = (event as CustomEvent<Robot>).detail;
      if (!robot?.id) return;
      refreshRobots(robot);
    };
    window.addEventListener(ROBOT_CONNECTION_CHANGED_EVENT, onRobotChanged);
    return () => window.removeEventListener(ROBOT_CONNECTION_CHANGED_EVENT, onRobotChanged);
  }, [refreshRobots]);

  const primaryRobotId = robots[0]?.id;

  useEffect(() => {
    let cancelled = false;
    fetchRuns()
      .then((data) => !cancelled && setRuns(data))
      .catch(() => undefined);
    fetchSchedules()
      .then((data) => !cancelled && setSchedules(data))
      .catch(() => undefined);
    /* Thống kê hiện vẫn tính theo MỘT robot (con đầu tiên trong danh sách) —
       backend `/robots/{id}/stats` chưa tổng hợp nhiều robot. Đây là phần
       "sửa sau" của thay đổi nghiệp vụ: giao diện đã lên danh sách nhiều
       robot, khối Thống kê sẽ theo sau khi backend hỗ trợ.

       `fetchRobotStats` đã tự trả `null` cho đúng một trường hợp hợp lệ:
       backend xác nhận 404 "chưa có lượt chạy nào" (xem ghi chú tại đó). Mọi
       lỗi khác — 500, mất mạng, endpoint hỏng — nó NÉM LÊN, và `catch(() =>
       undefined)` trước đây nuốt luôn tất cả, nên một backend đang lỗi trông
       giống hệt "robot mới tinh chưa chạy gì" — khối Thống kê lặng lẽ biến
       mất mà không ai biết vì sao. Giờ chỉ nuốt đúng cái `null` hợp lệ, còn
       lại báo toast như mọi lỗi khác trong trang. */
    if (primaryRobotId) {
      fetchRobotStats(primaryRobotId)
        .then((data) => !cancelled && setStats(data))
        .catch((err) => {
          if (cancelled) return;
          toast.error(errorText(toErrorObject(err)));
        });
    }
    return () => {
      cancelled = true;
    };
  }, [primaryRobotId]);

  const retryRobots = useCallback(() => {
    setRobotState("loading");
    setRobotError(null);
    setAttempt((a) => a + 1);
  }, []);

  return (
    <div className="p-4 sm:p-6 max-w-[1440px] mx-auto space-y-4">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <h1 className="display-l text-paper-50">Tổng quan</h1>
      </div>

      {robotState === "loading" && (
        <>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
            {[0, 1, 2].map((i) => (
              <div key={i} className="panel p-4 h-[76px] flex flex-col justify-center gap-2">
                <Skeleton className="h-4 w-32" />
                <Skeleton className="h-3 w-20" />
              </div>
            ))}
          </div>
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
            {[0, 1, 2, 3].map((i) => (
              <div key={i} className="panel p-4 h-[88px] flex flex-col justify-between">
                <Skeleton className="h-3 w-20" />
                <Skeleton className="h-6 w-24" />
              </div>
            ))}
          </div>
        </>
      )}

      {robotState === "error" && robotError && <ErrorPanel error={robotError} onRetry={retryRobots} />}

      {/* Thứ tự: thống kê → robot đã kết nối → sắp tới → chạy gần đây. */}
      {stats && (
        <>
          {/* Câu tóm tắt chỉ nói tỉ lệ khi CÓ lượt kết thúc — `success_rate`
              là `null` lúc chưa có, và làm tròn `null` sẽ ra "0% thành công",
              tức là báo hỏng trong khi thật ra chưa xong cái nào. */}
          <SectionHeading
            title="Thống kê"
            meta={
              stats.success_rate === null
                ? `${stats.total_runs} lượt trong ${stats.window_days} ngày`
                : `${stats.total_runs} lượt · ${Math.round(stats.success_rate * 100)}% thành công trong ${stats.window_days} ngày`
            }
          />
          <StatsPanel stats={stats} />
        </>
      )}

      {robotState === "ready" && (
        <>
          <SectionHeading title="Robot đã kết nối" meta={`${robots.length} robot`} />
          <RobotListPanel
            robots={robots}
            onConnectionChanged={refreshRobots}
          />
        </>
      )}

      <SectionHeading title="Sắp tới" meta={`${schedules.filter((s) => s.enabled).length} lịch đang bật`} />
      <UpcomingSchedules schedules={schedules} />

      <SectionHeading title="Chạy gần đây" />
      <RecentRuns runs={runs} />
    </div>
  );
}
