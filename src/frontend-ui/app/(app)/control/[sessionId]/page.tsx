"use client";
/* P04 — ĐIỀU KHIỂN / CHAT ⭐
   The screen the product exists for. Left: the conversation with the agent and
   the composer. Right: what the robot sees and reports.

   The chain in MASTER §13.1 is visible end to end:
     user message → agent reply → tool card → observation → episode bar → outcome

   No chain-of-thought is rendered — only the closed set of user-facing statuses
   (§13.2). There is no background video here; the only moving image is the
   robot camera (§8). */

import { useState, useEffect, useCallback, useMemo, useRef } from "react";
import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { Loader, MoreHorizontal, RefreshCcw, RotateCcw, TriangleAlert, Info, X } from "lucide-react";
import { useDismissable } from "@/hooks/useDismissable";

import { MessageList } from "@/components/chat/MessageList";
import { Composer } from "@/components/chat/Composer";
import { HumanInLoopPanel } from "@/components/chat/HumanInLoopPanel";
import { LimitReachedPrompt } from "@/components/chat/LimitReachedPrompt";
import { JointTable } from "@/components/robot-state/JointTable";
import { TelemetryChart } from "@/components/robot-state/TelemetryChart";
import { CameraPanel } from "@/components/camera/CameraPanel";
import { RobotArmViewer } from "@/components/logs/RobotArmViewer";
import { EmptyState } from "@/components/common/EmptyState";
import { ErrorPanel } from "@/components/common/ErrorMessage";
import { ConfirmDialog, Dialog } from "@/components/common/Dialog";
import { Skeleton } from "@/components/common/Skeleton";

import { useSessionStream } from "@/hooks/useSessionStream";
import { useServoStream } from "@/hooks/useServoStream";
import { actionAvailability, chatInputLocked } from "@/lib/robot-actions";
import { errorText, toErrorObject, type ErrorObject } from "@/lib/errors";
import { recordMockUserMessage } from "@/lib/mock/sessions";
import {
  fetchSession,
  fetchMessages,
  deleteSession,
  newSessionHref,
  setMessageFeedback,
} from "@/lib/api/sessions";
import { USE_MOCK } from "@/lib/api/config";
import {
  connectRobot,
  fetchRobotEnvironment,
  fetchRobots,
  resetRobotEpisode,
  resetRobotEnvironment,
  type RobotEnvironment,
} from "@/lib/api/robots";
import { isRobotManuallyDisconnected, ROBOT_CONNECTION_CHANGED_EVENT } from "@/lib/robot-connection-state";
import type { ServoSample } from "@/lib/api/servo-stream";
import type { ReplayServoSample } from "@/lib/api/run-replay";
import {
  useAuthStore,
  useRealtimeStore,
  useUiPreferenceStore,
  useHumanInLoopStore,
  toast,
  type Message,
  type Session,
  type Robot,
  type AgentStatusLabel,
  type ArtifactTab,
} from "@/stores";

function motionToolInFlight(
  runBlocks: ReturnType<typeof useRealtimeStore.getState>["runBlocks"],
  episodeProgress: ReturnType<typeof useRealtimeStore.getState>["episodeProgress"],
): boolean {
  if (episodeProgress) return true;
  return runBlocks.some((block) => {
    if (block.kind !== "tool") return false;
    if (block.call.tool_name !== "take_action" && block.call.tool_name !== "go_home") return false;
    if (block.call.status === "RUNNING" || block.call.status === "PENDING") return true;
    return (block.call.episodes ?? []).some((episode) => !episode.outcome);
  });
}

/* ---------------------------------------------------------------- pieces */

/** The one status line allowed while the agent works — MASTER §13.2. */
function AgentStatusLine({ status }: { status: AgentStatusLabel | null }) {
  if (!status) return null;
  return (
    <span className="hidden sm:flex items-center gap-1.5 text-[13px] text-haze-300">
      <span className="w-1.5 h-1.5 rounded-full bg-arc-500 animate-pulse-dot" />
      {status}
    </span>
  );
}

/* Hai mã lỗi báo agent tự dừng vì chạm giới hạn AN TOÀN của một lượt (vòng
   lặp/tool call ở `guard_reject`, hoặc quá thời gian) — KHÔNG PHẢI một lỗi
   thật sự đã hỏng. Cả hai đều mang nghĩa "nhiệm vụ có thể chưa xong, gửi
   tiếp nếu muốn làm nốt", nên xứng đáng một câu hỏi Dừng/Tiếp tục thay vì
   banner lỗi một-nút-đóng. Mọi banner lỗi khác (robot mất kết nối, MCP
   hỏng...) vẫn là lỗi thật — không đổi cách hiện. */
const CONTINUABLE_BANNER_CODES = new Set(["AGENT_011", "AGENT_020"]);

/* Hai tab, không phải ba. P04 §7 đặc tả thêm tab "Ảnh", nhưng ảnh mà agent
   chụp đã nằm ngay trong tool card `capture_tool` giữa dòng hội thoại — đúng
   mắt xích "quan sát" của §13.1 — nên tab riêng chỉ là chỗ thứ hai chứa cùng
   một thứ, và làm hẹp mất khung camera. */
const TABS: { key: ArtifactTab; label: string }[] = [
  { key: "camera", label: "Camera" },
  { key: "state", label: "Trạng thái" },
];

function replaySampleFromLiveServo(sample: ServoSample | null): ReplayServoSample | null {
  if (!sample) return null;
  const atMs = sample.received_at_ms ?? (sample.ts ? sample.ts * 1000 : Date.now());
  return {
    at: new Date(atMs).toISOString(),
    sequence: atMs,
    step: sample.step ?? null,
    status: "LIVE",
    source: "servo_stream",
    joints: sample.joints,
    eef_pos: sample.eef_pos,
    gripper_qpos: sample.gripper_qpos,
  };
}

function RobotSwitchDialog({
  open,
  robots,
  error,
  busyRobotId,
  onChoose,
  onDashboard,
}: {
  open: boolean;
  robots: Robot[];
  error: ErrorObject | null;
  busyRobotId: string | null;
  onChoose: (robot: Robot) => void;
  onDashboard: () => void;
}) {
  return (
    <Dialog
      open={open}
      onClose={onDashboard}
      title="Không kết nối được robot của phiên này"
      disableOutsideClose
      showCloseButton={false}
      footer={
        <button type="button" onClick={onDashboard} className="btn-ghost focus-ring">
          Về trang chủ
        </button>
      }
    >
      <div className="space-y-4">
        <p className="text-[14px] text-haze-300">
          {error ? errorText(error) : "Robot đang dùng trong phiên này không sẵn sàng."}
        </p>
        <div className="space-y-2">
          {robots.length === 0 ? (
            <EmptyState message="Bạn chưa liên kết robot nào khác." />
          ) : (
            robots.map((robot) => (
              <button
                key={robot.id}
                type="button"
                onClick={() => onChoose(robot)}
                disabled={busyRobotId !== null}
                className="w-full flex items-center justify-between gap-3 p-3 rounded-lg bg-ink-900 border border-[var(--line)] hover:border-[var(--line-strong)] text-left focus-ring disabled:opacity-60 disabled:cursor-not-allowed"
              >
                <span className="min-w-0">
                  <span className="block text-[14px] font-medium text-paper-50 truncate">{robot.name}</span>
                  <span className="block data text-[12.5px] text-haze-500 mt-1">{robot.id}</span>
                </span>
                <span className="btn-ghost !h-8 !text-[12.5px] pointer-events-none">
                  {busyRobotId === robot.id ? "Đang kết nối" : robot.connection_status === "ONLINE" ? "Mở" : "Kết nối"}
                </span>
              </button>
            ))
          )}
        </div>
      </div>
    </Dialog>
  );
}

function ArtifactPanel({
  robotId,
  robotName,
  robot,
  cameraUnavailable,
}: {
  robotId: string;
  robotName: string;
  /* Robot truyền xuống từ trang, KHÔNG tra `mockRobots` tại chỗ: với dữ liệu
     thật thì mảng tĩnh đó không chứa con robot đang chạy, nên biểu đồ telemetry
     im lặng rơi về bảng khớp và không ai biết vì sao. */
  robot: Robot | null;
  cameraUnavailable?: string;
}) {
  const tab = useUiPreferenceStore((s) => s.artifactTab);
  const setTab = useUiPreferenceStore((s) => s.setArtifactTab);
  const liveRobotState = useRealtimeStore((s) => s.robotState);
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const servoStream = useServoStream(robotId, !USE_MOCK);
  const liveArmSample = useMemo(() => replaySampleFromLiveServo(servoStream.sample), [servoStream.sample]);

  // Tab opens with the fetched state, then only SSE updates it (P04 §7).
  const joints = liveRobotState?.joints ?? robot?.joints ?? [];

  // Arrow-key navigation across the tab strip (MASTER §15).
  const onTabKeyDown = (e: React.KeyboardEvent, index: number) => {
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
    e.preventDefault();
    const next = e.key === "ArrowRight" ? (index + 1) % TABS.length : (index - 1 + TABS.length) % TABS.length;
    setTab(TABS[next].key);
    tabRefs.current[next]?.focus();
  };

  return (
    <div className="panel flex flex-col h-full overflow-hidden">
      <div className="h-10 border-b border-[var(--line)] flex shrink-0" role="tablist" aria-label="Dữ liệu từ robot">
        {TABS.map((t, i) => (
          <button
            key={t.key}
            ref={(el) => {
              tabRefs.current[i] = el;
            }}
            role="tab"
            aria-selected={tab === t.key}
            tabIndex={tab === t.key ? 0 : -1}
            onClick={() => setTab(t.key)}
            onKeyDown={(e) => onTabKeyDown(e, i)}
            className={`flex-1 relative text-[14px] font-medium transition-colors duration-[120ms] focus-ring cursor-pointer ${
              tab === t.key ? "text-paper-50" : "text-haze-500 hover:text-haze-300"
            }`}
          >
            {t.label}
            {tab === t.key && <span className="absolute bottom-0 left-0 right-0 h-0.5 bg-arc-500" />}
          </button>
        ))}
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto p-3">
        {tab === "camera" && (
          <div className="grid min-h-full grid-rows-[minmax(260px,1fr)_auto] gap-3">
            <CameraPanel
              robotId={robotId}
              robotName={robotName}
              unavailableReason={cameraUnavailable}
              className="min-h-[260px]"
            />
            <RobotArmViewer sample={liveArmSample} robotModel={robot?.model} />
          </div>
        )}

        {tab === "state" &&
          (robot ? (
            <div className="space-y-4">
              <div className="border-t border-[var(--line)] pt-3">
                  <TelemetryChart
                    robot={robot}
                    servoSample={servoStream.sample}
                    servoHistory={servoStream.history}
                    servoStatus={servoStream.status}
                    servoError={servoStream.error}
                    servoLatencyMs={servoStream.latencyMs}
                />
              </div>
            </div>
          ) : joints.length === 0 ? (
            <EmptyState message="Chưa đọc được trạng thái khớp của robot." />
          ) : (
            <JointTable joints={joints} detailed />
          ))}

      </div>
    </div>
  );
}

/* ---------------------------------------------------------------- page */

export default function ControlPage() {
  const params = useParams();
  const router = useRouter();
  const searchParams = useSearchParams();
  const sessionId = (params?.sessionId as string) ?? "";
  // "Chạy lại" from a failed run (P09) lands here with the text prefilled.
  const prefillFromUrl = searchParams?.get("prefill") ?? undefined;

  const selectedRobotId = useAuthStore((s) => s.selectedRobotId);
  const selectRobot = useAuthStore((s) => s.selectRobot);
  const agentStatus = useRealtimeStore((s) => s.agentStatus);
  const episodeProgress = useRealtimeStore((s) => s.episodeProgress);
  const runBlocks = useRealtimeStore((s) => s.runBlocks);
  const banner = useRealtimeStore((s) => s.banner);
  const setBanner = useRealtimeStore((s) => s.setBanner);
  const liveRobotState = useRealtimeStore((s) => s.robotState);
  const activeRunId = useRealtimeStore((s) => s.runId);

  const splitRatio = useUiPreferenceStore((s) => s.controlSplitRatio);
  const setSplitRatio = useUiPreferenceStore((s) => s.setSplitRatio);

  const [messages, setMessages] = useState<Message[]>([]);
  const [loadState, setLoadState] = useState<"loading" | "ready" | "error">("loading");
  const [loadError, setLoadError] = useState<ErrorObject | null>(null);
  const [loadAttempt, setLoadAttempt] = useState(0);
  const [mobileTab, setMobileTab] = useState<"chat" | "camera">("chat");
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  useDismissable(menuOpen, menuRef, useCallback(() => setMenuOpen(false), []));
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [confirmResetEpisode, setConfirmResetEpisode] = useState(false);
  const [resettingEpisode, setResettingEpisode] = useState(false);
  const [prefill, setPrefill] = useState<string | undefined>(prefillFromUrl);
  const composerShellRef = useRef<HTMLDivElement>(null);
  const chatColumnRef = useRef<HTMLDivElement>(null);
  const [shellTopOffset, setShellTopOffset] = useState(0);
  const hilRequest = useHumanInLoopStore((s) => s.request);
  const [dragging, setDragging] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  /* Phiên tải BẤT ĐỒNG BỘ qua tầng API.
     Trước đây chỗ này là `mockSessions.find(...)` — một mảng tĩnh trong bundle.
     Với phiên thật thì không bao giờ tìm thấy, nên trang mở ra như một phiên
     rỗng và toàn bộ hội thoại đã chat coi như biến mất. */
  const [session, setSession] = useState<Session | null>(null);
  useEffect(() => {
    let cancelled = false;
    fetchSession(sessionId)
      .then((s) => {
        if (!cancelled) setSession(s);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [sessionId]);

  /* Đo khoảng cách từ mép trên shell ô gõ chat xuống đáy chat column — bất biến
     khi dòng hint "Agent đang xử lý..." xuất hiện/biến mất (dòng đó nằm
     NGOÀI shell, bên dưới). Panel HIL neo đúng mép trên shell theo con số
     này, cộng 8px đệm. */
  useEffect(() => {
    const shell = composerShellRef.current;
    const column = chatColumnRef.current;
    if (!shell || !column) return;
    const measure = () => {
      const shellRect = shell.getBoundingClientRect();
      const columnRect = column.getBoundingClientRect();
      setShellTopOffset(columnRect.bottom - shellRect.top);
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(shell);
    ro.observe(column);
    window.addEventListener("resize", measure);
    return () => {
      ro.disconnect();
      window.removeEventListener("resize", measure);
    };
  }, []);

  /* Danh sách robot nạp từ backend — KHÔNG fallback về `mockRobots[0]`.
     Trước đây có chốt cuối `?? mockRobots[0]`, nghĩa là backend chết hoặc
     trả mảng rỗng thì trang vẫn vẽ ra một cánh tay IDLE/ONLINE giả — đúng
     kiểu "hiện trạng thái giả còn nguy hiểm hơn hiện lỗi" mà `lib/api/robots.ts`
     đã từ chối làm ở tầng fetch. Ở đây theo dõi rõ trạng thái tải
     (loading/ready/error) và chặn màn hình chat lại cho tới khi có robot
     THẬT — xem khối render `if (!robot)` bên dưới. */
  const [robots, setRobots] = useState<Robot[]>([]);
  const [robotsLoadState, setRobotsLoadState] = useState<"loading" | "ready" | "error">("loading");
  const [robotsError, setRobotsError] = useState<ErrorObject | null>(null);
  const [robotsLoadAttempt, setRobotsLoadAttempt] = useState(0);
  const [robotConnectState, setRobotConnectState] = useState<"idle" | "connecting" | "ready" | "error">("idle");
  const [robotConnectError, setRobotConnectError] = useState<ErrorObject | null>(null);
  const [connectBusyRobotId, setConnectBusyRobotId] = useState<string | null>(null);
  const [ensuredRobotId, setEnsuredRobotId] = useState<string | null>(null);
  const [environment, setEnvironment] = useState<RobotEnvironment | null>(null);
  const [environmentLoadState, setEnvironmentLoadState] = useState<"idle" | "loading" | "ready" | "error">("idle");
  const [resetEnvironmentPending, setResetEnvironmentPending] = useState(false);
  useEffect(() => {
    let cancelled = false;
    const load = (isRetry: boolean) => {
      fetchRobots()
        .then((data) => {
          if (cancelled) return;
          setRobots(data);
          setRobotsLoadState("ready");
          setRobotsError(null);
          const rid = session?.robot_id ?? useAuthStore.getState().selectedRobotId;
          const row = data.find((r) => r.id === rid) ?? data[0];
          if (row?.robot_status !== "IDLE") return;
          const rt = useRealtimeStore.getState();
          if (motionToolInFlight(rt.runBlocks, rt.episodeProgress)) return;
          if (rt.episodeProgress) rt.setEpisodeProgress(null);
          if (rt.robotState?.robot_status === "BUSY") {
            rt.updateRobotState({
              robot_status: "IDLE",
              connection_status: rt.robotState.connection_status,
              joints: rt.robotState.joints,
            });
          }
          if (rt.composerLocked) rt.unlockComposer();
        })
        .catch((err) => {
          if (cancelled) return;
          if (isRetry) {
            setRobotsError(toErrorObject(err));
            setRobotsLoadState("error");
          }
        });
    };
    load(true);
    const interval = window.setInterval(() => load(false), 5_000);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [robotsLoadAttempt, session?.robot_id]);

  const retryRobotsLoad = useCallback(() => {
    setRobotsLoadState("loading");
    setRobotsError(null);
    setRobotsLoadAttempt((a) => a + 1);
  }, []);

  const refreshRobots = useCallback(() => {
    fetchRobots()
      .then((data) => {
        setRobots(data);
        setRobotsLoadState("ready");
      })
      .catch((err) => {
        setRobotsError(toErrorObject(err));
        setRobotsLoadState("error");
      });
  }, []);

  const robot: Robot | null =
    robots.find((r) => r.id === (session?.robot_id ?? selectedRobotId)) ?? null;
  const activeRobotId = robot?.id ?? null;

  useEffect(() => {
    if (!session?.robot_id || robotsLoadState !== "ready" || !robot) return;
    if (ensuredRobotId === session.robot_id) return;
    let cancelled = false;
    const robotId = session.robot_id;
    void (async () => {
      await Promise.resolve();
      if (cancelled) return;
      if (isRobotManuallyDisconnected(robotId)) {
        setRobotConnectError({
          code: "ROBOT_042",
          message: "Robot đã được ngắt kết nối. Chọn robot khác hoặc kết nối lại robot này.",
        });
        setRobotConnectState("error");
        return;
      }
      setRobotConnectState("connecting");
      setRobotConnectError(null);
      setConnectBusyRobotId(robotId);
      try {
        const updated = await connectRobot(robotId);
        if (cancelled) return;
        setRobots((prev) => prev.map((r) => (r.id === updated.id ? updated : r)));
        selectRobot(updated.id);
        setEnsuredRobotId(updated.id);
        setRobotConnectState("ready");
      } catch (err) {
        if (cancelled) return;
        setRobotConnectError(toErrorObject(err));
        setRobotConnectState("error");
      } finally {
        if (!cancelled) setConnectBusyRobotId(null);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [session?.robot_id, robotsLoadState, robot?.id, ensuredRobotId]); // eslint-disable-line react-hooks/exhaustive-deps -- selectRobot stable; effect intentionally keyed by active session robot

  useEffect(() => {
    const onRobotChanged = (event: Event) => {
      const changed = (event as CustomEvent<Robot>).detail;
      if (!changed?.id) return;
      setRobots((rows) => rows.map((row) => (row.id === changed.id ? changed : row)));
      if (changed.id === session?.robot_id && changed.connection_status === "OFFLINE") {
        setEnsuredRobotId(null);
        setRobotConnectError({
          code: "ROBOT_042",
          message: "Robot đã được ngắt kết nối. Chọn robot khác hoặc kết nối lại robot này.",
        });
        setRobotConnectState("error");
      }
    };
    window.addEventListener(ROBOT_CONNECTION_CHANGED_EVENT, onRobotChanged);
    return () => window.removeEventListener(ROBOT_CONNECTION_CHANGED_EVENT, onRobotChanged);
  }, [session?.robot_id]);

  const { startRun, reconnectRun } = useSessionStream({ sessionId, setMessages });

  useEffect(() => {
    if (robotsLoadState !== "ready" || robotConnectState !== "ready" || !activeRobotId) return;

    let cancelled = false;
    void (async () => {
      setEnvironmentLoadState("loading");
      try {
        const data = await fetchRobotEnvironment(activeRobotId);
        if (cancelled) return;
        setEnvironment(data);
        setEnvironmentLoadState("ready");
      } catch {
        if (cancelled) return;
        setEnvironment(null);
        setEnvironmentLoadState("error");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [activeRobotId, robotsLoadState, robotConnectState]);

  /* ---- load the transcript (GET /sessions/{id}/messages) ---- */
  useEffect(() => {
    let cancelled = false;
    fetchMessages(sessionId)
      .then((msgs) => {
        if (cancelled) return;
        setMessages(msgs);
        setLoadState("ready");
      })
      .catch((err) => {
        if (cancelled) return;
        setLoadError(toErrorObject(err));
        setLoadState("error");
      });
    return () => {
      cancelled = true;
    };
  }, [sessionId, loadAttempt]);

  const retryLoad = useCallback(() => {
    setLoadState("loading");
    setLoadError(null);
    setLoadAttempt((a) => a + 1);
  }, []);

  /* ---- reconnect to active run if needed ---- */
  const reconnectRunRef = useRef(reconnectRun);
  const reconnectedRunsRef = useRef<Set<string>>(new Set());

  useEffect(() => {
    reconnectRunRef.current = reconnectRun;
  }, [reconnectRun]);

  useEffect(() => {
    if (loadState === "ready" && session?.active_run && !activeRunId) {
      if (reconnectedRunsRef.current.has(session.active_run)) return;
      reconnectedRunsRef.current.add(session.active_run);
      reconnectRunRef.current(session.active_run).catch(() => undefined);
    }
  }, [loadState, session?.active_run, activeRunId]);

  /* ---- resizable split, keyboard-operable ---- */
  useEffect(() => {
    if (!dragging) return;
    const onMove = (e: MouseEvent) => {
      const rect = containerRef.current?.getBoundingClientRect();
      if (!rect) return;
      setSplitRatio(Math.min(Math.max((e.clientX - rect.left) / rect.width, 0.3), 0.7));
    };
    const onUp = () => setDragging(false);
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
  }, [dragging, setSplitRatio]);

  const onSeparatorKeyDown = (e: React.KeyboardEvent) => {
    const step = 0.02;
    if (e.key === "ArrowLeft") {
      e.preventDefault();
      setSplitRatio(Math.max(splitRatio - step, 0.3));
    } else if (e.key === "ArrowRight") {
      e.preventDefault();
      setSplitRatio(Math.min(splitRatio + step, 0.7));
    } else if (e.key === "Home") {
      e.preventDefault();
      setSplitRatio(0.3);
    } else if (e.key === "End") {
      e.preventDefault();
      setSplitRatio(0.7);
    }
  };

  /* ---- sending ---- */
  const send = useCallback(
    (text: string, idempotencyKey: string) => {
      const rt = useRealtimeStore.getState();
      if (
        motionToolInFlight(rt.runBlocks, rt.episodeProgress) ||
        rt.robotState?.robot_status === "BUSY" ||
        rt.robotState?.robot_status === "STOPPING"
      ) {
        return;
      }
      rt.setPendingRun(true);

      const optimistic: Message = {
        id: `msg-local-${idempotencyKey}`,
        session_id: sessionId,
        role: "user",
        content: text,
        created_at: new Date().toISOString(),
        send_status: "sending",
      };
      setMessages((prev) => [...prev, optimistic]);
      setPrefill(undefined);

      // POST /messages is never retried automatically (P04 §6) — on failure the
      // bubble is marked and the user decides, reusing the same key.
      window.setTimeout(() => {
        setMessages((prev) =>
          prev.map((m) => (m.id === optimistic.id ? { ...m, send_status: "sent" as const } : m)),
        );
        /* Ghi tin nhắn vào phiên. Câu lệnh đầu tiên đồng thời đặt tên cho phiên
           — nếu không thì danh sách Lịch sử phiên toàn là "Phiên mới" giống hệt
           nhau, không phân biệt được cái nào với cái nào.

           CHỈ ở chế độ mock. Chạy thật thì `POST /chat/start` đã tự ghi tin
           nhắn user và đặt tên phiên ở phía server; gọi thêm ở đây là ghi vào
           kho mock — không ai đọc, và tạo cảm giác sai rằng client là nơi chịu
           trách nhiệm lưu hội thoại. */
        if (USE_MOCK) {
          void recordMockUserMessage(sessionId, text).catch(() => undefined);
        }
        startRun(text);
      }, 260);
    },
    [sessionId, startRun],
  );

  const retrySend = useCallback(
    (message: Message) => {
      // Same idempotency key: the id already carries it.
      setMessages((prev) =>
        prev.map((m) => (m.id === message.id ? { ...m, send_status: "sending" as const } : m)),
      );
      window.setTimeout(() => {
        setMessages((prev) =>
          prev.map((m) => (m.id === message.id ? { ...m, send_status: "sent" as const } : m)),
        );
        startRun(message.content);
      }, 260);
    },
    [startRun],
  );

  /* Like/dislike — optimistic, lùi lại nếu backend từ chối. Không chờ round
     trip mới tô nút: nút phản hồi ngay khi bấm, giống mọi hành động khác
     trong composer. */
  const handleFeedback = useCallback(
    (message: Message, feedback: "like" | "dislike" | null) => {
      const previous = message.feedback ?? null;
      setMessages((prev) =>
        prev.map((m) => (m.id === message.id ? { ...m, feedback } : m)),
      );
      setMessageFeedback(sessionId, message.id, feedback).catch((err) => {
        setMessages((prev) =>
          prev.map((m) => (m.id === message.id ? { ...m, feedback: previous } : m)),
        );
        toast.error(errorText(toErrorObject(err)));
      });
    },
    [sessionId],
  );

  /* "Tiếp tục" trên `LimitReachedPrompt` — lượt trước đã kết thúc (agent tự
     dừng vì chạm giới hạn an toàn), không có quyết định nào đang treo ở
     backend để gửi lại. Cách "tiếp tục" đúng nghĩa là mở một lượt MỚI, đi
     qua đúng đường ống bình thường (`send`) như người dùng tự gõ — agent đọc
     lại lịch sử phiên nên không cần lặp lại yêu cầu gốc. */
  const continueAfterLimit = useCallback(() => {
    setBanner(null);
    send("Tiếp tục nhiệm vụ trước đó.", crypto.randomUUID());
  }, [send, setBanner]);

  const handleResetEnvironment = useCallback(async () => {
    if (!robot || resetEnvironmentPending) return;
    setResetEnvironmentPending(true);
    try {
      await resetRobotEnvironment(robot.id);
      const refreshed = await fetchRobotEnvironment(robot.id).catch(() => null);
      if (refreshed) {
        setEnvironment(refreshed);
        setEnvironmentLoadState("ready");
      }
      toast.success("Đã reset môi trường LIBERO.");
    } catch (err) {
      toast.error(errorText(toErrorObject(err)));
    } finally {
      setResetEnvironmentPending(false);
    }
  }, [robot, resetEnvironmentPending]);

  const chooseReplacementRobot = async (nextRobot: Robot) => {
    setConnectBusyRobotId(nextRobot.id);
    setRobotConnectError(null);
    try {
      if (nextRobot.connection_status !== "ONLINE") {
        const updated = await connectRobot(nextRobot.id);
        setRobots((prev) => prev.map((r) => (r.id === updated.id ? updated : r)));
      }
      selectRobot(nextRobot.id);
      router.replace(newSessionHref(nextRobot.id));
    } catch (err) {
      setRobotConnectError(toErrorObject(err));
    } finally {
      setConnectBusyRobotId(null);
    }
  };

  /* Chưa có robot THẬT thì dừng ở đây — không vẽ tiếp phần còn lại của trang
     bằng dữ liệu rỗng/giả. Đặt SAU mọi hook (Rules of Hooks) và TRƯỚC mọi chỗ
     đọc `robotStatus`/`connectionStatus`/`robot.id` không optional. */
  if (robotsLoadState !== "ready" || !robot || robotConnectState === "connecting" || robotConnectState === "error") {
    const noRobotReady = robotsLoadState === "ready" && !robot;
    const connectFailed = robotConnectState === "error" && Boolean(robot);
    return (
      <div className="flex flex-col h-full min-h-0 items-center justify-center p-6">
        {connectFailed ? (
          <div className="flex flex-col items-center gap-3">
            <Skeleton className="h-6 w-48" />
            <Skeleton className="h-4 w-64" />
          </div>
        ) : robotsLoadState === "error" ? (
          <ErrorPanel
            error={robotsError ?? { code: "SYS_001", message: "Không tải được danh sách robot." }}
            onRetry={retryRobotsLoad}
          />
        ) : (
          <div className="flex flex-col items-center gap-3">
            <Skeleton className="h-6 w-48" />
            <Skeleton className="h-4 w-64" />
          </div>
        )}
        <RobotSwitchDialog
          open={connectFailed}
          robots={robots.filter((r) => r.id !== robot?.id)}
          error={robotConnectError}
          busyRobotId={connectBusyRobotId}
          onChoose={chooseReplacementRobot}
          onDashboard={() => router.push("/dashboard")}
        />
        <Dialog
          open={noRobotReady}
          onClose={() => router.push("/dashboard")}
          title="Chưa có robot để điều khiển"
          disableOutsideClose
          showCloseButton={false}
          footer={
            <button type="button" onClick={() => router.push("/dashboard")} className="btn-primary focus-ring">
              Về trang chủ
            </button>
          }
        >
          <p className="body-text text-haze-300">
            {robots.length === 0
              ? "Chưa có robot trong hệ thống. Cloud sẽ seed robot khi khởi động hoặc khi Edge đăng ký."
              : "Robot của phiên này không còn tồn tại."}
          </p>
        </Dialog>
      </div>
    );
  }

  // Live SSE BUSY wins; so does a freshly polled snapshot. Either source
  // must lock the composer while take_action or go_home is moving the arm.
  const robotStatus =
    liveRobotState?.robot_status === "BUSY" || robot.robot_status === "BUSY"
      ? "BUSY"
      : liveRobotState?.robot_status === "STOPPING" || robot.robot_status === "STOPPING"
        ? "STOPPING"
        : (liveRobotState?.robot_status ?? robot.robot_status);
  const connectionStatus = liveRobotState?.connection_status ?? robot.connection_status;
  const motionRunning =
    motionToolInFlight(runBlocks, episodeProgress) ||
    robotStatus === "BUSY" ||
    robotStatus === "STOPPING";
  const inputLocked =
    chatInputLocked({ motionInFlight: motionRunning, robotStatus }) || !!hilRequest;

  const resetEpisode = actionAvailability("resetEpisode", robotStatus, connectionStatus);
  const camera = actionAvailability("viewCamera", robotStatus, connectionStatus);
  const cameraUnavailable = camera.state === "disabled" ? camera.reason : undefined;
  const looksLikeVirtualEnvironment = robot.model === "franka" || typeof robot.sim_time === "number";
  const resetEnvironmentVisible =
    environment?.robot_id === robot.id ? environment.reset_available || looksLikeVirtualEnvironment : looksLikeVirtualEnvironment;

  const chatColumn = (
    <div ref={chatColumnRef} className="relative flex flex-col flex-1 min-h-0">
      {loadState === "error" && loadError ? (
        <div className="flex-1 min-h-0 p-5">
          <ErrorPanel error={loadError} onRetry={retryLoad} />
        </div>
      ) : (
        <MessageList
          messages={messages}
          loading={loadState === "loading"}
          onRetry={retrySend}
          onFeedback={handleFeedback}
        />
      )}

      {/* Run-level banner: stopped / disconnected / failed (P04 §8).
          Chạm giới hạn an toàn (AGENT_011/AGENT_020) đổi hẳn sang câu hỏi
          Dừng/Tiếp tục kiểu HITL — mọi lỗi thật khác vẫn là banner một-nút
          "Thử lại" như cũ. */}
      {banner && banner.code && CONTINUABLE_BANNER_CODES.has(banner.code) ? (
        <LimitReachedPrompt
          message={banner.message}
          onContinue={continueAfterLimit}
          onStop={() => setBanner(null)}
        />
      ) : (
        banner && (
          <div
            role={banner.tone === "error" ? "alert" : "status"}
            className={`mx-3 mb-2 rounded-lg px-3 py-2.5 text-[14px] flex items-start gap-2 ${
              banner.tone === "error"
                ? "bg-halt-500/12 border border-halt-500/40 text-halt-500"
                : "bg-ink-800 border border-[var(--line)] text-haze-300"
            }`}
          >
            {banner.tone === "error" ? (
              <TriangleAlert size={15} className="mt-0.5 shrink-0" />
            ) : (
              <Info size={15} className="mt-0.5 shrink-0" />
            )}
            <span className="flex-1">{banner.message}</span>
            {banner.code && (
              <button
                type="button"
                onClick={() => {
                  const lastUser = [...messages].reverse().find((m) => m.role === "user");
                  if (lastUser) setPrefill(lastUser.content);
                  setBanner(null);
                }}
                className="underline hover:no-underline focus-ring rounded cursor-pointer shrink-0"
              >
                Thử lại
              </button>
            )}
            <button
              type="button"
              onClick={() => setBanner(null)}
              aria-label="Đóng thông báo"
              className="p-0.5 rounded focus-ring cursor-pointer shrink-0"
            >
              <X size={14} />
            </button>
          </div>
        )
      )}

      {/* Composer LUÔN mounted — kể cả khi có yêu cầu HITL. Trước đây
          `{!hilRequest && <Composer ... />}` unmount hẳn component khi panel
          hiện ra: mất nội dung người dùng đang gõ dở, và làm ResizeObserver ở
          effect phía trên trỏ vào một node đã bị gỡ (nó chỉ chạy một lần lúc
          mount, không bao giờ re-observe sau khi Composer remount). Khoá bằng
          `composerLocked` — cơ chế đã có sẵn cho đúng việc này — thay vì
          unmount. */}
      <Composer
        robotStatus={robotStatus}
        connectionStatus={connectionStatus}
        composerLocked={inputLocked}
        onSend={send}
        prefillText={prefill}
        shellRef={composerShellRef}
      />

      {/* Human-in-the-loop: neo theo mép trên shell ô gõ (không bị dòng hint
          dưới Composer làm tụt theo). */}
      <div
        className="absolute inset-x-0 bottom-0 z-40 flex flex-col items-center px-4 pointer-events-none"
        style={{ paddingBottom: shellTopOffset + 8 }}
      >
        <HumanInLoopPanel robotId={robot.id} />
      </div>
    </div>
  );

  return (
    <div className="flex flex-col h-full min-h-0">
      {/* Header — P04 §2 */}
      <header className="h-14 px-4 sm:px-5 border-b border-[var(--line)] flex items-center justify-between gap-3 bg-ink-900 shrink-0">
        <div className="flex items-center gap-3 min-w-0">
          {/* Đường dẫn ngược về danh sách. Hai mục nav "Phiên mới" và "Lịch sử
              phiên" dẫn tới cùng màn hình này, nên phải nói rõ đang ở phiên nào
              và cho lối quay ra — nếu không thì vào rồi mắc kẹt, không biết
              mình đang xem cái gì trong số các phiên đã có. */}
          <nav aria-label="Đường dẫn" className="flex items-center gap-2 min-w-0 text-[14px]">
            <Link
              href="/sessions"
              className="text-haze-500 hover:text-paper-50 transition-colors duration-[120ms] focus-ring rounded shrink-0"
            >
              Lịch sử phiên
            </Link>
            <span className="text-haze-500 shrink-0" aria-hidden="true">
              /
            </span>
            {loadState === "loading" ? (
              <Skeleton className="h-4 w-32" />
            ) : (
              <span className="text-paper-50 font-medium truncate">
                {session?.title ?? "Phiên mới"}
              </span>
            )}
          </nav>

          {/* KHÔNG lặp lại badge trạng thái robot ở đây.
              Topbar ngay phía trên đã hiện nó rồi, và hai badge nằm cách nhau
              một dòng thì không đọc ra "hai thông tin" mà ra "một thông tin bị
              in hai lần". Tệ hơn: hai chỗ lấy từ hai nguồn khác nhau — topbar
              từ `selectedRobot` của shell, chỗ này từ `robotStatus` của trang —
              nên có lúc chúng lệch nhau, và người dùng không biết tin cái nào.
              Tên robot vẫn đi vào aria-label của camera cho trình đọc màn hình. */}
          <AgentStatusLine status={agentStatus} />

        </div>

        <div className="flex items-center gap-2 shrink-0">
          {/* Mobile: chat ⇄ camera */}
          <div className="flex md:hidden rounded-lg overflow-hidden border border-[var(--line)]">
            {(["chat", "camera"] as const).map((t) => (
              <button
                key={t}
                type="button"
                onClick={() => setMobileTab(t)}
                aria-pressed={mobileTab === t}
                className={`px-3 h-8 text-[13px] font-medium transition-colors duration-[120ms] focus-ring cursor-pointer ${
                  mobileTab === t ? "bg-ink-700 text-paper-50" : "text-haze-500"
                }`}
              >
                {t === "chat" ? "Trò chuyện" : "Camera"}
              </button>
            ))}
          </div>

          <div className="relative" ref={menuRef}>
            <button
              type="button"
              onClick={() => setMenuOpen((v) => !v)}
              aria-label="Tuỳ chọn phiên"
              aria-expanded={menuOpen}
              className="w-8 h-8 flex items-center justify-center rounded-lg text-haze-300 hover:text-paper-50 hover:bg-ink-700 transition-colors duration-[120ms] focus-ring cursor-pointer"
            >
              <MoreHorizontal size={18} />
            </button>
            {menuOpen && (
              <div className="absolute right-0 top-10 z-30 w-44 menu-surface py-1">
                {/* Đang chat mà muốn mở phiên khác thì không phải chạy ra
                    sidebar — và trên mobile sidebar đang ẩn sau sheet nên đây
                    là lối duy nhất với tới được. */}
                <button
                  type="button"
                  onClick={() => {
                    setMenuOpen(false);
                    // `?new=1`: `/control/new` trơn giờ mở lại phiên gần nhất.
                    router.push(newSessionHref(robot.id));
                  }}
                  className="w-full text-left px-3 h-9 text-[14px] text-paper-50 hover:bg-ink-700 focus-ring cursor-pointer"
                >
                  Phiên mới
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setMenuOpen(false);
                    toast.info("Đổi tên phiên ở trang Lịch sử phiên.");
                  }}
                  className="w-full text-left px-3 h-9 text-[14px] text-haze-300 hover:text-paper-50 hover:bg-ink-700 focus-ring cursor-pointer"
                >
                  Đổi tên phiên
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setMenuOpen(false);
                    setConfirmResetEpisode(true);
                  }}
                  disabled={resetEpisode.state !== "enabled"}
                  title={
                    resetEpisode.state === "disabled" ? resetEpisode.reason : "Reset môi trường sim"
                  }
                  className="w-full text-left px-3 h-9 text-[14px] text-haze-300 hover:text-paper-50 hover:bg-ink-700 focus-ring cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
                >
                  Reset môi trường
                </button>
                {/* Ngăn cách hành động phá huỷ khỏi hai hành động thường, để
                    không bấm nhầm khi menu vừa bung ra. */}
                <span className="block my-1 h-px bg-[var(--line)]" aria-hidden="true" />
                <button
                  type="button"
                  onClick={() => {
                    setMenuOpen(false);
                    setConfirmDelete(true);
                  }}
                  className="w-full text-left px-3 h-9 text-[14px] text-halt-500 hover:bg-halt-500/10 focus-ring cursor-pointer"
                >
                  Xoá phiên
                </button>
              </div>
            )}
          </div>

          {resetEnvironmentVisible && (
            <button
              type="button"
              onClick={handleResetEnvironment}
              disabled={resetEnvironmentPending || environmentLoadState === "loading"}
              className="hidden md:inline-flex items-center gap-2 h-9 px-4 rounded-full text-[14px] font-medium border border-[var(--line)] bg-ink-800 text-haze-300 hover:text-paper-50 hover:border-[var(--line-strong)] transition-colors duration-[120ms] focus-ring disabled:opacity-60 disabled:cursor-not-allowed"
            >
              {resetEnvironmentPending ? (
                <Loader size={14} className="animate-spin-slow" />
              ) : (
                <RotateCcw size={14} />
              )}
              {resetEnvironmentPending ? "Đang reset" : "Reset môi trường"}
            </button>
          )}
        </div>
      </header>

      {/* Mobile layout */}
      <div className="flex-1 min-h-0 flex flex-col md:hidden">
        {mobileTab === "chat" ? (
          chatColumn
        ) : (
          <div className="flex-1 min-h-0 p-3">
            <ArtifactPanel
              robotId={robot.id}
              robotName={robot.name}
              robot={robot}
              cameraUnavailable={cameraUnavailable}
            />
          </div>
        )}
        {resetEnvironmentVisible && (
          <div className="sticky bottom-0 z-40 p-3 bg-ink-900/95 border-t border-[var(--line)] space-y-2">
            <button
              type="button"
              onClick={handleResetEnvironment}
              disabled={resetEnvironmentPending || environmentLoadState === "loading"}
              className="w-full inline-flex items-center justify-center gap-2 h-10 px-3 rounded-lg border border-[var(--line)] bg-ink-800 text-[14px] font-medium text-paper-50 hover:border-[var(--line-strong)] hover:bg-ink-700 transition-colors duration-[120ms] focus-ring disabled:opacity-60 disabled:cursor-not-allowed"
            >
              {resetEnvironmentPending ? (
                <Loader size={15} className="animate-spin-slow" />
              ) : (
                <RefreshCcw size={15} />
              )}
              <span>{resetEnvironmentPending ? "Đang reset" : "Reset môi trường"}</span>
            </button>
          </div>
        )}
      </div>

      {/* Desktop layout — draggable split, persisted */}
      <div ref={containerRef} className="hidden md:flex flex-1 min-h-0">
        <div className="flex flex-col min-w-[420px] min-h-0" style={{ width: `${splitRatio * 100}%` }}>
          {chatColumn}
        </div>

        <div
          role="separator"
          aria-orientation="vertical"
          aria-label="Điều chỉnh tỉ lệ hai cột"
          aria-valuenow={Math.round(splitRatio * 100)}
          aria-valuemin={30}
          aria-valuemax={70}
          tabIndex={0}
          onMouseDown={() => setDragging(true)}
          onKeyDown={onSeparatorKeyDown}
          className="group relative w-1 shrink-0 cursor-col-resize touch-none focus-ring"
        >
          <span
            aria-hidden="true"
            className={`pointer-events-none absolute inset-y-6 left-1/2 w-0.5 -translate-x-1/2 rounded-full bg-arc-500 transition-opacity duration-[120ms] ${
              dragging ? "opacity-100" : "opacity-0 group-hover:opacity-60"
            }`}
          />
        </div>

        <div className="flex-1 min-w-[320px] min-h-0 py-3 pr-3">
          <ArtifactPanel
              robotId={robot.id}
              robotName={robot.name}
              robot={robot}
              cameraUnavailable={cameraUnavailable}
            />
        </div>
      </div>

      <ConfirmDialog
        open={confirmResetEpisode}
        onClose={() => (resettingEpisode ? undefined : setConfirmResetEpisode(false))}
        onConfirm={async () => {
          if (resettingEpisode) return;
          setResettingEpisode(true);
          try {
            await resetRobotEpisode(robot.id);
            setConfirmResetEpisode(false);
            toast.success("Đã reset môi trường.");
          } catch (err) {
            setConfirmResetEpisode(false);
            toast.error(errorText(toErrorObject(err)));
          } finally {
            setResettingEpisode(false);
          }
        }}
        title="Reset môi trường"
        description={
          robotStatus === "BUSY"
            ? "Sẽ hủy nhiệm vụ đang chạy và đưa scene về trạng thái ban đầu (sim). Trên robot thật chỉ xoá hàng đợi policy. Tiếp tục?"
            : "Đưa scene về trạng thái ban đầu (sim). Trên robot thật chỉ xoá hàng đợi policy. Tiếp tục?"
        }
        confirmLabel={resettingEpisode ? "Đang reset…" : "Reset môi trường"}
      />

      <ConfirmDialog
        open={confirmDelete}
        onClose={() => setConfirmDelete(false)}
        onConfirm={async () => {
          setConfirmDelete(false);
          try {
            await deleteSession(sessionId);
            toast.success("Đã xoá phiên.");
            router.push("/sessions");
          } catch (err) {
            toast.error(errorText(toErrorObject(err)));
          }
        }}
        title="Xoá phiên này?"
        description="Toàn bộ tin nhắn và tool card của phiên sẽ bị xoá. Nhật ký chạy vẫn được giữ ở trang Nhật ký."
        confirmLabel="Xoá phiên"
      />
    </div>
  );
}
