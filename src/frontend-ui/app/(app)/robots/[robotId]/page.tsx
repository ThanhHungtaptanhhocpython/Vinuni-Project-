"use client";
/* P10 — CHI TIẾT ROBOT
   Trả lời "cỗ máy này đang hoạt động ra sao?". Mọi hành động ở đây theo ma trận
   §11.2, và nút nào bị khoá đều nói rõ lý do.

   Camera đặt TRÊN khối trạng thái servo: đây là thứ đầu tiên người vận hành
   cần nhìn thấy trước khi ra lệnh — "cánh tay đang ở đâu, xung quanh nó có gì"
   trả lời trước "servo có bám lệnh không". Bảng khớp chi tiết (JointTable) đã
   bỏ khỏi trang này — đồ thị servo thời gian thực (`TelemetryChart`) đã nói
   đúng câu hỏi "servo có bám lệnh không" bằng hình, không cần một bảng số
   tĩnh lặp lại cùng dữ liệu.

   Nút "Xoá robot" LUÔN qua `ConfirmDialog` — đây là cửa một chiều, không có
   API nào ghép nối lại; matrix `unpairRobot` (`@/lib/robot`) đã quyết định
   khi nào nút được bấm, giống mọi nút khác trên trang. */

import { useState, useEffect, useCallback } from "react";
import { useParams, useRouter } from "next/navigation";
import { Cpu, Home, TriangleAlert, ShieldCheck, Video, Trash2, RotateCcw, Wifi, WifiOff } from "lucide-react";

import { RobotStatusBadge } from "@/components/robot-state/RobotStatusBadge";
import { RUN_STATUS_STATS_META } from "@/components/common/StatusBadge";
import { TelemetryChart } from "@/components/robot-state/TelemetryChart";
import { CameraPanel } from "@/components/camera/CameraPanel";
import { DisabledHint } from "@/components/common/DisabledHint";
import { ErrorPanel } from "@/components/common/ErrorMessage";
import { EmptyState } from "@/components/common/EmptyState";
import { Skeleton } from "@/components/common/Skeleton";
import { ConfirmDialog } from "@/components/common/Dialog";

import {
  fetchRobot,
  fetchRobotStats,
  goHomeRobot,
  resetRobotError,
  resetRobotEpisode,
  deleteRobot,
  connectRobot,
  disconnectRobot,
  type RobotStats,
} from "@/lib/api/robots";
import { USE_MOCK } from "@/lib/api/config";
import { actionAvailability } from "@/lib/robot-actions";
import { errorText, toErrorObject, type ErrorObject } from "@/lib/errors";
import { toast, type Robot } from "@/stores";
import { useServoStream } from "@/hooks/useServoStream";

/* ---------------------------------------------------------------- cards */

/* Khoá theo TRẠNG THÁI LƯỢT CHẠY thật trong database, không phải outcome của
   episode — hệ thống chưa lưu episode nào xuống đĩa. Bảng dùng chung với
   trang Tổng quan, xem `StatusBadge.tsx`. */
const STATUS_STYLE = RUN_STATUS_STATS_META;

/** Khối "Thống kê" có 3 trạng thái, không suy đoán giữa chúng: `null` từ
    `fetchRobotStats` nghĩa là backend đã trả lời và xác nhận CHƯA CÓ run nào
    (404) — khác hẳn `statsError`, nghĩa là bản thân lệnh gọi thất bại (mạng/
    5xx/auth) và ta KHÔNG BIẾT có run nào hay không. Coi hai trường hợp là một
    (im lặng ẩn khối) sẽ khiến backend đang lỗi trông giống hệt "robot mới". */
function RunStatsCard({ stats, error }: { stats: RobotStats | null; error: boolean }) {
  if (error) {
    return (
      <div className="panel p-4">
        <h2 className="eyebrow mb-3">Thống kê</h2>
        <p className="text-[14px] text-halt-500">Không lấy được thống kê vận hành.</p>
      </div>
    );
  }
  if (!stats) return null;
  return <RunStatsCardBody stats={stats} />;
}

function RunStatsCardBody({ stats }: { stats: RobotStats }) {
  const total = stats.outcomes.reduce((sum, o) => sum + o.count, 0) || 1;
  return (
    <div className="panel p-4">
      <h2 className="eyebrow mb-3">Thống kê {stats.window_days} ngày</h2>
      <div className="flex items-end justify-between mb-4">
        <div>
          <p className="font-mono text-[32px] tabular-nums text-paper-50 leading-none">{stats.total_runs}</p>
          <p className="eyebrow mt-1">Lượt thực thi</p>
        </div>
        <div className="text-right">
          {/* `success_rate` là `null` khi chưa lượt nào kết thúc — hiện dấu
              gạch chứ không phải 0%, vì 0% đọc ra là "hỏng hết". */}
          <p className="font-mono text-[32px] tabular-nums text-jade-400 leading-none">
            {stats.success_rate === null ? "—" : `${Math.round(stats.success_rate * 100)}%`}
          </p>
          <p className="eyebrow mt-1">Thành công</p>
        </div>
      </div>

      {/* Colours are keyed to outcome semantics — MASTER §4.1 */}
      <div className="h-2 flex rounded-full overflow-hidden" role="img" aria-label={`Tỉ lệ kết quả ${stats.window_days} ngày`}>
        {stats.outcomes.map((o) => (
          <div
            key={o.status}
            className={STATUS_STYLE[o.status]?.bar ?? "bg-ink-700"}
            style={{ width: `${(o.count / total) * 100}%` }}
            title={`${STATUS_STYLE[o.status]?.label ?? o.status}: ${o.count}`}
          />
        ))}
      </div>
      <div className="flex flex-wrap gap-x-4 gap-y-1 mt-3">
        {stats.outcomes.map((o) => (
          <span key={o.status} className="flex items-center gap-1.5 text-[12.5px] text-haze-500">
            <span className={`w-1.5 h-1.5 rounded-full ${STATUS_STYLE[o.status]?.bar ?? "bg-ink-700"}`} />
            {STATUS_STYLE[o.status]?.label ?? o.status}
            <span className="data text-[12px]">{o.count}</span>
          </span>
        ))}
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------- page */

export default function RobotDetailPage() {
  const params = useParams();
  const router = useRouter();
  const robotId = (params?.robotId as string) ?? "";

  const [robot, setRobot] = useState<Robot | null>(null);
  const [stats, setStats] = useState<RobotStats | null>(null);
  const [statsError, setStatsError] = useState(false);
  const [state, setState] = useState<"loading" | "ready" | "error" | "missing">("loading");
  const [error, setError] = useState<ErrorObject | null>(null);
  const [attempt, setAttempt] = useState(0);

  const [showRawRegisters, setShowRawRegisters] = useState(false);
  const [actionError, setActionError] = useState<ErrorObject | null>(null);
  const [busyAction, setBusyAction] = useState<"home" | "reset" | "resetEpisode" | "connection" | null>(null);
  const servoStream = useServoStream(robotId, state === "ready" && !USE_MOCK);

  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [resetEpisodeDialogOpen, setResetEpisodeDialogOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [resettingEpisode, setResettingEpisode] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetchRobot(robotId)
      .then((robotData) => {
        if (cancelled) return;
        if (!robotData) {
          setState("missing");
          return;
        }
        setRobot(robotData);
        setState("ready");
      })
      .catch((err) => {
        if (cancelled) return;
        setError(toErrorObject(err));
        setState("error");
      });
    return () => {
      cancelled = true;
    };
  }, [robotId, attempt]);

  /* Thống kê tách khỏi effect chính ở trên — cùng lý do với sức khoẻ camera:
     một lệnh gọi có thể fail (mạng/5xx) mà không được kéo sập cả trang (khớp,
     khả năng thiết bị… vẫn phải hiện bình thường). */
  useEffect(() => {
    let cancelled = false;
    fetchRobotStats(robotId)
      .then((data) => {
        if (cancelled) return;
        setStats(data);
        setStatsError(false);
      })
      .catch(() => {
        if (cancelled) return;
        setStats(null);
        setStatsError(true);
      });
    return () => {
      cancelled = true;
    };
  }, [robotId, attempt]);

  const reload = useCallback(() => {
    setState("loading");
    setError(null);
    setAttempt((a) => a + 1);
  }, []);

  const goHome = useCallback(async () => {
    if (!robot) return;
    setBusyAction("home");
    setActionError(null);
    try {
      await goHomeRobot(robot.id);
      const updated = await fetchRobot(robot.id);
      if (updated) setRobot(updated);
      toast.success("Đã gửi lệnh về vị trí home.");
    } catch (err) {
      setActionError(toErrorObject(err));
    } finally {
      setBusyAction(null);
    }
  }, [robot]);

  const resetError = useCallback(async () => {
    if (!robot) return;
    setBusyAction("reset");
    setActionError(null);
    try {
      await resetRobotError(robot.id);
      const updated = await fetchRobot(robot.id);
      if (updated) setRobot(updated);
      toast.success("Đã gửi lệnh xoá cờ lỗi.");
    } catch (err) {
      setActionError(toErrorObject(err));
    } finally {
      setBusyAction(null);
    }
  }, [robot]);

  const confirmResetEpisode = useCallback(async () => {
    if (!robot || resettingEpisode) return;
    setResettingEpisode(true);
    setBusyAction("resetEpisode");
    setActionError(null);
    try {
      await resetRobotEpisode(robot.id);
      const updated = await fetchRobot(robot.id);
      if (updated) setRobot(updated);
      setResetEpisodeDialogOpen(false);
      toast.success("Đã reset môi trường.");
    } catch (err) {
      setResetEpisodeDialogOpen(false);
      setActionError(toErrorObject(err));
    } finally {
      setResettingEpisode(false);
      setBusyAction(null);
    }
  }, [robot, resettingEpisode]);

  /* Cửa một chiều — không có API nào ghép nối lại — nên KHÔNG bao giờ xoá
     trực tiếp từ nút bấm; luôn qua `ConfirmDialog` trước (mở bằng
     `setDeleteDialogOpen`). Xoá xong điều hướng thẳng về Tổng quan vì trang
     này không còn gì để hiện — robot đã biến mất khỏi backend. */
  const confirmDelete = useCallback(async () => {
    if (!robot || deleting) return;
    setDeleting(true);
    try {
      await deleteRobot(robot.id);
      toast.success(`Đã gỡ ${robot.name} khỏi danh sách.`);
      router.push("/dashboard");
    } catch (err) {
      setDeleteDialogOpen(false);
      toast.error(errorText(toErrorObject(err)));
    } finally {
      setDeleting(false);
    }
  }, [robot, router, deleting]);

  /* ---- states ---- */

  if (state === "loading") {
    return (
      <div className="p-6 max-w-[1440px] mx-auto space-y-4">
        <Skeleton className="h-8 w-56" />
        <div className="grid grid-cols-1 lg:grid-cols-[1fr_380px] gap-3">
          <Skeleton className="h-[320px] rounded-xl" />
          <Skeleton className="h-[240px] rounded-xl" />
        </div>
      </div>
    );
  }

  if (state === "error" && error) {
    return (
      <div className="p-6 max-w-[720px] mx-auto mt-10">
        <ErrorPanel error={error} onRetry={reload} />
      </div>
    );
  }

  if (state === "missing" || !robot) {
    return (
      <div className="p-6 max-w-[720px] mx-auto mt-10">
        <EmptyState
          message="Không tìm thấy robot này. Có thể nó đã được ngắt ghép nối."
          action={
            <button type="button" onClick={() => router.push("/dashboard")} className="btn-primary focus-ring">
              Về Tổng quan
            </button>
          }
        />
      </div>
    );
  }

  const reset = actionAvailability("resetError", robot.robot_status, robot.connection_status);
  const home = actionAvailability("goHome", robot.robot_status, robot.connection_status);
  const resetEpisode = actionAvailability("resetEpisode", robot.robot_status, robot.connection_status);
  const unpair = actionAvailability("unpairRobot", robot.robot_status, robot.connection_status);
  const camera = actionAvailability("viewCamera", robot.robot_status, robot.connection_status);
  const cameraUnavailable = camera.state === "disabled" ? camera.reason : undefined;
  const offline = robot.connection_status === "OFFLINE";
  const toggleConnection = async () => {
    setBusyAction("connection");
    setActionError(null);
    try {
      const updated = offline ? await connectRobot(robot.id) : await disconnectRobot(robot.id);
      setRobot(updated);
      toast.success(offline ? "Đã kết nối robot." : "Đã ngắt kết nối robot.");
    } catch (err) {
      setActionError(toErrorObject(err));
    } finally {
      setBusyAction(null);
    }
  };

  return (
    <div className="p-4 sm:p-6 max-w-[1440px] mx-auto space-y-4">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[var(--line)] pb-4">
        <div className="flex items-center gap-3 flex-wrap">
          <h1 className="display-l text-paper-50">{robot.name}</h1>
          <RobotStatusBadge robotStatus={robot.robot_status} connectionStatus={robot.connection_status} />
          <span className="eyebrow flex items-center gap-2">
            <span>{robot.model}</span>
            <span aria-hidden="true">·</span>
            <span className="data text-[12.5px] text-haze-500">v{robot.edge_version}</span>
          </span>
        </div>
        <button
          type="button"
          onClick={toggleConnection}
          disabled={busyAction === "connection"}
          className={`h-9 px-3 rounded-lg inline-flex items-center gap-1.5 text-[13px] font-semibold focus-ring disabled:opacity-60 disabled:cursor-not-allowed ${
            offline
              ? "bg-arc-500 text-ink-950 hover:bg-arc-400"
              : "bg-ink-800 text-paper-50 border border-[var(--line-strong)] hover:bg-ink-700"
          }`}
        >
          {offline ? <Wifi size={14} /> : <WifiOff size={14} />}
          {busyAction === "connection" ? "Đang xử lý" : offline ? "Kết nối robot" : "Ngắt kết nối"}
        </button>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-[1fr_380px] gap-3">
        {/* Left column */}
        <div className="space-y-3 min-w-0">
          <RunStatsCard stats={stats} error={statsError} />

          {/* Camera lên trước servo — đây là thứ người vận hành cần nhìn đầu
              tiên trước khi ra lệnh. */}
          <section className="panel p-2 overflow-hidden">
            <h2 className="eyebrow flex items-center gap-2 px-2 pt-2 pb-1">
              <Video size={13} /> Camera
            </h2>
            <CameraPanel
              robotId={robot.id}
              robotName={robot.name}
              unavailableReason={cameraUnavailable}
              className="h-[320px]"
              bothDirection="row"
            />
          </section>

          <section className="panel p-4">
            <div className="flex items-center justify-between gap-3 mb-4">
              <h2 className="eyebrow flex items-center gap-2">
                <Cpu size={13} /> Trạng thái {robot.joints.length} khớp
              </h2>
              <label className="flex items-center gap-2 text-[13px] text-haze-300 cursor-pointer">
                <input
                  type="checkbox"
                  checked={showRawRegisters}
                  onChange={(e) => setShowRawRegisters(e.target.checked)}
                  className="accent-arc-500 focus-ring"
                />
                Hiện thanh ghi thô
              </label>
            </div>

            <TelemetryChart
              robot={robot}
              servoSample={servoStream.sample}
              servoHistory={servoStream.history}
              servoStatus={servoStream.status}
              servoError={servoStream.error}
              servoLatencyMs={servoStream.latencyMs}
            />

            {showRawRegisters && (
              <div className="mt-3 panel-inset p-3 overflow-x-auto">
                <p className="eyebrow mb-2">Thanh ghi thô</p>
                <pre className="log-text text-haze-300">
                  {robot.joints
                    .map((j) =>
                      [
                        j.name.padEnd(15),
                        `qpos=${j.present_position.toFixed(3).padStart(9)}`,
                        `ctrl=${(j.target_position ?? NaN).toFixed(3).padStart(9)}`,
                        `qvel=${(j.present_velocity ?? NaN).toFixed(3).padStart(8)}`,
                        `frc=${(j.torque_nm ?? NaN).toFixed(3).padStart(7)}`,
                        `range=[${j.range_min ?? -180},${j.range_max ?? 180}]`,
                      ].join("  "),
                    )
                    .join("\n")}
                </pre>
              </div>
            )}
          </section>

        </div>

        {/* Right column */}
        <aside className="panel p-4 space-y-4 h-fit">
          <div>
            <h2 className="eyebrow mb-2">Khả năng của thiết bị</h2>
            <table className="w-full font-mono text-[13px]">
              <tbody>
                {robot.capabilities.map((cap) => (
                  <tr key={cap.name} className="border-b border-[var(--line)] last:border-0">
                    <td className="py-1.5 text-haze-300">{cap.name}</td>
                    <td className="py-1.5 text-right text-haze-500">v{cap.version}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="border-t border-[var(--line)] pt-4 space-y-2">
            <DisabledHint reason={home.state === "disabled" ? home.reason : undefined} className="w-full">
            <button
              type="button"
              onClick={goHome}
              disabled={home.state !== "enabled" || busyAction === "home"}
              title={home.state === "enabled" ? "Đưa robot về vị trí mặc định" : undefined}
              className="btn-ghost w-full !justify-start border border-[var(--line)] disabled:opacity-40 disabled:cursor-not-allowed focus-ring"
            >
              <Home size={14} />
              {busyAction === "home" ? "Đang về home…" : "Về vị trí home"}
            </button>
            </DisabledHint>

            <DisabledHint reason={resetEpisode.state === "disabled" ? resetEpisode.reason : undefined} className="w-full">
            <button
              type="button"
              onClick={() => setResetEpisodeDialogOpen(true)}
              disabled={resetEpisode.state !== "enabled" || busyAction === "resetEpisode"}
              title={
                resetEpisode.state === "enabled"
                  ? "Reset môi trường sim (hủy task đang chạy nếu có)"
                  : undefined
              }
              className="btn-ghost w-full !justify-start border border-[var(--line)] disabled:opacity-40 disabled:cursor-not-allowed focus-ring"
            >
              <RotateCcw size={14} />
              {busyAction === "resetEpisode" ? "Đang reset…" : "Reset môi trường"}
            </button>
            </DisabledHint>

            {/* reset_error is only rendered in ERROR state — §11.2 */}
            {reset.state !== "hidden" && (
              <div className="pt-2">
                <p className="eyebrow mb-1.5">Cờ lỗi hiện tại</p>
                <div className="flex flex-wrap gap-1.5 mb-2">
                  {robot.health.error_flags.length === 0 ? (
                    <span className="text-[13px] text-haze-500">Không có</span>
                  ) : (
                    robot.health.error_flags.map((flag) => (
                      <span key={flag} className="data text-[12px] px-2 py-0.5 rounded bg-halt-500/12 text-halt-500">
                        {flag}
                      </span>
                    ))
                  )}
                </div>
                <DisabledHint reason={reset.state === "disabled" ? reset.reason : undefined} className="w-full">
                <button
                  type="button"
                  onClick={resetError}
                  disabled={reset.state !== "enabled" || busyAction === "reset"}
                  title={reset.state === "enabled" ? "Xoá cờ lỗi để ra lệnh tiếp" : undefined}
                  className="btn-ghost w-full !justify-start border border-[var(--line)] disabled:opacity-40 disabled:cursor-not-allowed focus-ring"
                >
                  <ShieldCheck size={14} />
                  {busyAction === "reset" ? "Đang xoá lỗi…" : "Xoá lỗi"}
                </button>
                </DisabledHint>
              </div>
            )}

            {/* Errors from an action land next to that action, never in a toast */}
            {actionError && (
              <p className="flex items-start gap-1.5 text-[13.5px] text-halt-500 pt-1">
                <TriangleAlert size={14} className="mt-0.5 shrink-0" />
                {errorText(actionError)}
              </p>
            )}
          </div>

          {/* Cửa một chiều, nên tách hẳn khỏi cụm lệnh vận hành phía trên bằng
              một đường kẻ riêng — không để cạnh "Xoá lỗi" dễ bấm nhầm. */}
          {unpair.state !== "hidden" && (
            <div className="border-t border-[var(--line)] pt-4">
              <DisabledHint reason={unpair.state === "disabled" ? unpair.reason : undefined} className="w-full">
                <button
                  type="button"
                  onClick={() => setDeleteDialogOpen(true)}
                  disabled={unpair.state !== "enabled"}
                  title={unpair.state === "enabled" ? "Gỡ robot này khỏi danh sách" : undefined}
                  className="btn-ghost w-full !justify-start border border-halt-500/30 text-halt-500 hover:bg-halt-500/10 disabled:opacity-40 disabled:cursor-not-allowed focus-ring"
                >
                  <Trash2 size={14} />
                  Xoá robot
                </button>
              </DisabledHint>
            </div>
          )}
        </aside>
      </div>

      <ConfirmDialog
        open={resetEpisodeDialogOpen}
        onClose={() => (resettingEpisode ? undefined : setResetEpisodeDialogOpen(false))}
        onConfirm={confirmResetEpisode}
        title="Reset môi trường"
        description={
          robot.robot_status === "BUSY"
            ? "Sẽ hủy nhiệm vụ đang chạy và đưa scene về trạng thái ban đầu (sim). Trên robot thật chỉ xoá hàng đợi policy, không di chuyển phần cứng. Tiếp tục?"
            : "Đưa scene về trạng thái ban đầu (sim). Trên robot thật chỉ xoá hàng đợi policy, không di chuyển phần cứng. Tiếp tục?"
        }
        confirmLabel={resettingEpisode ? "Đang reset…" : "Reset môi trường"}
      />

      <ConfirmDialog
        open={deleteDialogOpen}
        onClose={() => (deleting ? undefined : setDeleteDialogOpen(false))}
        onConfirm={confirmDelete}
        title="Xoá robot"
        description={`Robot "${robot.name}" (${robot.id}) sẽ bị gỡ khỏi danh sách và không thể phục hồi. Bạn có chắc muốn xoá?`}
        confirmLabel={deleting ? "Đang xoá…" : "Xoá robot"}
      />
    </div>
  );
}
