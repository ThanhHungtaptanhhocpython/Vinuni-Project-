import { create } from "zustand";
import type { ErrorObject } from "@/lib/errors";

export type RobotStatus = "IDLE" | "BUSY" | "STOPPING" | "ERROR" | "DISCONNECTED";
export type ConnectionStatus = "UNREGISTERED" | "ONLINE" | "OFFLINE";

export type AgentStatusLabel =
  | "Đang hiểu yêu cầu"
  | "Đang quan sát"
  | "Đang lập kế hoạch"
  | "Đang thực thi"
  | "Đang dừng khẩn cấp"
  | "Đang kiểm tra kết quả"
  | "Đang xử lý";

export type EpisodeOutcome =
  | "SUCCESS"
  | "STOPPED"
  | "TIMEOUT"
  | "MAX_STEPS"
  | "SAFETY_ABORT";

export type ToolStatus =
  | "PENDING"
  | "RUNNING"
  | "WAITING_APPROVAL"
  | "SUCCEEDED"
  | "FAILED"
  | "CANCELLED"
  | "TIMEOUT";

export type RunSource = "CHAT" | "SCHEDULE" | "MANUAL_RUN";
/* `SCHEDULED` = lịch đã tới giờ nhưng run chưa khởi động. Nó khác `RUNNING`
   ở chỗ chưa có gì đang chạy, nên trang Nhật ký phải phân biệt được.
   `TIMEOUT` đến từ develop: run bị cắt vì quá giờ. */
export type RunStatus =
  | "RUNNING"
  | "WAITING_TOOL"
  | "COMPLETED"
  | "FAILED"
  | "CANCELLED"
  | "TIMEOUT"
  | "SCHEDULED";

export interface MessageRun {
  id: string;
  trace_id: string;
  status: RunStatus;
  duration_ms: number | null;
  /** Cùng trục dữ liệu với `Run.outcome`/`verification_status` bên dưới —
      xem `toMessageRun()`. Vắng mặt (chưa fetch xong `GET /runs/{id}`) khác
      với `null` (đã fetch, backend xác nhận không có): component hiển thị
      (`MessageStatusLine`) coi cả hai như nhau — chỉ ẩn khi chắc chắn không
      áp dụng, không bao giờ đoán. */
  outcome?: string | null;
  verification_status?: string | null;
  verification_result?: {
    status: string;
    confidence: number;
    reason: string;
    evidence_refs?: string[] | null;
  } | null;
}

/* ----------------------------------------------------------------
   Robot
   ---------------------------------------------------------------- */
export interface Robot {
  id: string;
  name: string;
  model: string;
  robot_status: RobotStatus;
  connection_status: ConnectionStatus;
  edge_version: string;
  health: {
    has_error: boolean;
    error_flags: string[];
  };
  capabilities: { name: string; version: string }[];
  joints: JointState[];
  /** Pose đầu công tác. Vắng mặt nếu backend chưa phơi ra — khi đó ẩn khối. */
  end_effector?: EndEffectorState;
  /** Thời gian mô phỏng, giây. Chỉ có khi robot chạy trong MuJoCo. */
  sim_time?: number;
}

/* Trạng thái một khớp của cánh tay.

   Chia làm hai nhóm, cố ý không trộn:

   · Nhóm động lực học — tương ứng các đại lượng MuJoCo phơi ra cho mỗi khớp:
     `qpos` (góc), `qvel` (vận tốc góc), `ctrl` (lệnh đặt cho actuator),
     lực khớp, và dải giới hạn `range`. Sai số giữa `ctrl` và `qpos` là thứ
     nói lên bộ điều khiển đang bám kịp hay không.

   Cánh tay này KHÔNG có cảm biến nhiệt, nên không có `present_temperature`.
   Vai trò "đại lượng cho biết khớp có đang bị ép quá không" — thứ mà Telemetry
   Rail §12.4 và các ngưỡng DOC 0 §3.5.2 vốn dựng trên nhiệt độ — nay do mô-men
   so với `forcerange` đảm nhiệm. Trường nào backend không trả thì ẩn phần tử đi
   chứ không hiện N/A (§13.3). */
export interface JointState {
  name: string;
  /** qpos — góc hiện tại, độ */
  present_position: number;
  /** qvel — vận tốc góc, độ/giây */
  present_velocity?: number;
  /** ctrl — góc mà bộ điều khiển đang yêu cầu, độ */
  target_position?: number;
  /** Lực sinh ra ở khớp, N·m */
  torque_nm?: number;
  /** Mô-men tối đa của actuator — `forcerange` trong MJCF. Dùng làm mẫu số
      để biết khớp đang gánh bao nhiêu phần trăm sức của nó. */
  torque_limit_nm?: number;
  /** Giới hạn khớp, độ. Thiếu thì thanh trượt lùi về ±180 */
  range_min?: number;
  range_max?: number;

  present_load: number;
  torque_enabled: boolean;
  moving: boolean;
  error_flags: string[];
}

/** Pose đầu công tác — MuJoCo cho qua `xpos`/`xquat` của site cuối chuỗi. */
export interface EndEffectorState {
  /** xpos, mét, trong hệ toạ độ gốc của cánh tay */
  position: { x: number; y: number; z: number };
  /** Quy từ xquat sang góc Euler, độ */
  orientation: { roll: number; pitch: number; yaw: number };
  /** Độ mở kẹp, 0 = đóng hẳn, 1 = mở hết */
  gripper?: number;
}

/* ----------------------------------------------------------------
   Session
   ---------------------------------------------------------------- */
export interface Session {
  id: string;
  title: string;
  robot_id: string;
  created_at: string;
  updated_at: string;
  active_run: string | null;
  last_message_preview: string | null;
  /** Ghim lên đầu danh sách. Đây là dữ liệu của người dùng, không phải tuỳ chọn
      giao diện — nên nó nằm ở backend chứ không phải `localStorage` (§18.2):
      đổi máy hay đổi trình duyệt thì các phiên đã ghim vẫn phải còn. */
  pinned: boolean;
}

/* ----------------------------------------------------------------
   Message
   ---------------------------------------------------------------- */
export interface Message {
  id: string;
  session_id: string;
  role: "user" | "assistant";
  content: string;
  created_at: string;
  tool_calls?: ToolCall[];
  /** Chặng đường agent đã đi để ra được câu trả lời này. Gắn vào tin nhắn khi
      lượt chạy kết thúc, giống cách tool_calls được gắn — nhờ vậy cuộn lại vẫn
      xem được, và mỗi lượt giữ chặng của riêng nó. */
  agent_steps?: AgentStep[];
  /** Dòng thời gian phẳng kiểu Claude Code cho bong bóng này — chữ agent nói
      xen kẽ tool call, ĐÚNG THEO THỨ TỰ chúng đến (`TraceFlow`). Gắn vào tin
      nhắn khi lượt chạy kết thúc, giống `agent_steps`/`tool_calls`. Vắng mặt
      (lịch sử tải từ `GET /sessions/{id}/messages`, hoặc lượt chạy cũ trước
      khi có field này) không phải lỗi — `TraceFlow` tự dựng lại một bản gần
      đúng từ `content` + `tool_calls` khi thiếu. */
  blocks?: TraceBlock[];
  send_status?: "sending" | "sent" | "failed";
  run?: MessageRun;
  /** Đánh giá của người dùng cho câu trả lời — chỉ có ý nghĩa với tin nhắn
      assistant. Lưu ở backend cùng tin nhắn (§18.2), không phải localStorage. */
  feedback?: "like" | "dislike" | null;
}

/** Một mắt xích trong dòng thời gian phẳng của một lượt chạy — chữ agent nói
    (`text`) hoặc một hành động thật (`tool`), theo ĐÚNG thứ tự chúng đến.
    Thay cho việc trộn `content` + `tool_calls` + `agent_steps` (ba mảng song
    song, phải sắp lại bằng timestamp) bằng một mảng đơn, thứ tự đã đúng sẵn.

    `sealed` chỉ có ý nghĩa với khối `text`: `false` = còn đang stream (hiện
    con trỏ nhấp nháy), `true` = đã chốt (message.completed đã tới). Khối
    `tool` không cần `sealed` — trạng thái của nó đã nằm trong `call.status`. */
export type TraceBlock =
  | { kind: "text"; id: string; content: string; sealed: boolean }
  | { kind: "tool"; id: string; call: ToolCall };

export interface ToolCall {
  id: string;
  tool_name: string;
  arguments: Record<string, unknown>;
  status: ToolStatus;
  result?: Record<string, unknown>;
  error?: { code: string; message: string };
  duration_ms?: number;
  episodes?: Episode[];
  /** Mốc client NHẬN được `tool.started`, không phải trường từ backend.

      Dùng để xếp tool xen kẽ đúng thứ tự với các bước giai đoạn trong agent
      trace — `ToolCall` vốn không mang mốc bắt đầu nào, chỉ có `duration_ms`
      sau khi xong. Cùng cơ sở đo với `AgentStep.startedAt`.

      Chỉ dùng cho THỨ TỰ và số đếm tương đối. Không bao giờ hiện thành giờ
      tuyệt đối, và khi tool đã xong thì luôn ưu tiên `duration_ms` của backend
      thay vì hiệu số đo ở client. */
  started_at?: number;
}

export interface Episode {
  id: string;
  /** Absent while the episode is still running — set by `episode.completed`. */
  outcome?: EpisodeOutcome;
  steps: number;
  max_steps: number;
  duration_s: number;
  /** The instruction the robot is executing, when the backend reports one. */
  instruction?: string;
  stop_reason?: string;
  video_artifact_id?: string;
}

/* ----------------------------------------------------------------
   Run
   ---------------------------------------------------------------- */
export interface Run {
  id: string;
  session_id: string;
  robot_id: string;
  status: RunStatus;
  source: RunSource;
  /** Có khi `source = SCHEDULE` — cho phép nhảy ngược về lịch đã tạo ra run.
      Hai nhánh cùng thêm trường này ở hai vị trí khác nhau nên git gộp ra hai
      dòng trùng tên; giữ lại bản có mô tả. */
  schedule_id?: string | null;
  input_text: string;
  created_at: string;
  completed_at: string | null;
  duration_s: number | null;
  trace_id: string;
  /** Execution-level outcome — KHÁC `verification_status` bên dưới, không suy
      ra vật lý task từ đây. Giá trị thật (P1.2): `action_executed` |
      `no_action_taken` | `partial_failure`. Một số nguồn cũ (thống kê
      session-gộp) vẫn có thể trả `SUCCEEDED`/`FAILED` — badge xử lý cả hai
      dạng, xem `components/common/StatusBadge.tsx`. */
  outcome?: string;
  /** Verification Layer Foundation — độc lập với `status`/`outcome`. `null`/
      `undefined` nghĩa là verify_node chưa từng chạy cho run này (không phải
      execution nào cần action vật lý, hoặc execution đã fail). */
  verification_status?: string | null;
  verification_result?: {
    status: string;
    confidence: number;
    reason: string;
    evidence_refs?: string[] | null;
  } | null;
  error?: { code: string; message: string };
  episodes: Episode[];
}

/** `Run` (full detail, `GET /runs/{id}`) → `MessageRun` (gắn vào một tin nhắn
    chat). Một chỗ DUY NHẤT ánh xạ field, dùng ở cả đường lịch sử
    (`fetchMessages`) lẫn đường live (`useSessionStream`'s `message.completed`)
    — hai đường phải kể cùng một trạng thái cho cùng một run. */
export function toMessageRun(run: Run): MessageRun {
  return {
    id: run.id,
    trace_id: run.trace_id,
    status: run.status,
    duration_ms: run.duration_s != null ? Math.round(run.duration_s * 1000) : null,
    outcome: run.outcome ?? null,
    verification_status: run.verification_status ?? null,
    verification_result: run.verification_result ?? null,
  };
}

/* ----------------------------------------------------------------
   Schedule
   ---------------------------------------------------------------- */
export interface Schedule {
  id: string;
  name: string;
  description: string;
  robot_id: string;
  task_prompt: string;
  cron_expression: string;
  cron_description: string;
  timezone: string;
  enabled: boolean;
  overlap_policy: "SKIP" | "QUEUE" | "FORCE_STOP";
  max_duration_s: number;
  consecutive_failures: number;
  next_run_at: string | null;
  created_at: string;
  updated_at: string;
}

/* ----------------------------------------------------------------
   Auth Store
   ---------------------------------------------------------------- */
export interface User {
  id: string;
  name: string;
  email: string;
  avatar_url: string | null;
  timezone: string;
}

interface AuthStore {
  user: User | null;
  selectedRobotId: string | null;
  setUser: (user: User | null) => void;
  selectRobot: (id: string | null) => void;
}

export const useAuthStore = create<AuthStore>((set) => ({
  user: null,
  selectedRobotId: null,
  setUser: (user) => set({ user }),
  selectRobot: (id) => set({ selectedRobotId: id }),
}));

/* ----------------------------------------------------------------
   UI Preference Store — localStorage only for UI prefs (MASTER §18.2)
   ---------------------------------------------------------------- */
export type CameraLayout = "both" | "top" | "wrist";
export type ArtifactTab = "camera" | "state";
/** What the user picked. "system" follows prefers-color-scheme. */
export type ThemePreference = "system" | "dark" | "light";
/** What the DOM actually gets stamped with. */
export type ResolvedTheme = "dark" | "light";

interface UiPreferenceStore {
  theme: ThemePreference;
  controlSplitRatio: number;
  cameraLayout: CameraLayout;
  /** top-first or wrist-first when both cameras are shown (P05) */
  cameraWristFirst: boolean;
  sidebarCollapsed: boolean;
  /** Bề rộng sidebar lúc MỞ RỘNG, tính bằng px. Lúc thu gọn thì dải 64px là cố
      định — kéo rộng một cột chỉ có icon thì không để làm gì. */
  sidebarWidth: number;
  /** Which artifact tab the control page shows — lets "Xem camera" switch tabs
      without navigating (P04 §5). */
  artifactTab: ArtifactTab;
  setTheme: (theme: ThemePreference) => void;
  setSplitRatio: (ratio: number) => void;
  setCameraLayout: (layout: CameraLayout) => void;
  toggleCameraOrder: () => void;
  toggleSidebar: () => void;
  setSidebarWidth: (px: number) => void;
  setArtifactTab: (tab: ArtifactTab) => void;
}

/* Giới hạn bề rộng sidebar.
 *
 * MIN 200px: dưới mức này nhãn dài nhất ("Lịch sử phiên", 13px medium) bắt đầu
 * bị `truncate` cắt, mà một mục nav bị cắt chữ thì thà thu gọn hẳn về icon.
 * MAX 400px: sidebar rộng hơn nữa thì cột nội dung phải thu lại, trong khi
 * chẳng có gì trong sidebar dùng tới chỗ đó — nav item dài nhất chỉ ~150px.
 * DEFAULT 240px giữ đúng `w-60` cũ, nên người chưa từng kéo không thấy gì đổi. */
export const SIDEBAR_MIN_WIDTH = 200;
export const SIDEBAR_MAX_WIDTH = 400;
export const SIDEBAR_DEFAULT_WIDTH = 240;

export function clampSidebarWidth(px: number): number {
  return Math.min(Math.max(px, SIDEBAR_MIN_WIDTH), SIDEBAR_MAX_WIDTH);
}

/* localStorage holds UI preferences only — auth JWTs are HttpOnly cookies
   set by the cloud agent, never stored in JS. */
const LS_KEYS = {
  theme: "ui.theme",
  split: "ui.control.split",
  cameraLayout: "ui.camera.layout",
  cameraOrder: "ui.camera.wristFirst",
  sidebar: "ui.sidebar.collapsed",
  sidebarWidth: "ui.sidebar.width",
} as const;

function readLocal<T>(key: string, parse: (raw: string) => T, fallback: T): T {
  if (typeof window === "undefined") return fallback;
  try {
    const raw = window.localStorage.getItem(key);
    return raw === null ? fallback : parse(raw);
  } catch {
    return fallback;
  }
}

function writeLocal(key: string, value: string) {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(key, value);
  } catch {
    /* private mode / quota — preferences are non-essential */
  }
}

export const useUiPreferenceStore = create<UiPreferenceStore>((set, get) => ({
  theme: "dark",
  controlSplitRatio: 0.6,
  cameraLayout: "both",
  cameraWristFirst: false,
  sidebarCollapsed: false,
  sidebarWidth: SIDEBAR_DEFAULT_WIDTH,
  artifactTab: "camera",
  setTheme: (theme) => {
    set({ theme });
    writeLocal(LS_KEYS.theme, theme);
    applyTheme(theme);
  },
  setSplitRatio: (ratio) => {
    set({ controlSplitRatio: ratio });
    writeLocal(LS_KEYS.split, String(ratio));
  },
  setCameraLayout: (layout) => {
    set({ cameraLayout: layout });
    writeLocal(LS_KEYS.cameraLayout, layout);
  },
  toggleCameraOrder: () => {
    const next = !get().cameraWristFirst;
    set({ cameraWristFirst: next });
    writeLocal(LS_KEYS.cameraOrder, String(next));
  },
  toggleSidebar: () => {
    const next = !get().sidebarCollapsed;
    set({ sidebarCollapsed: next });
    writeLocal(LS_KEYS.sidebar, String(next));
  },
  /* Kẹp NGAY tại store chứ không tin chỗ gọi: giá trị này còn đến từ
     localStorage, mà localStorage thì người dùng sửa tay được. */
  setSidebarWidth: (px) => {
    const width = clampSidebarWidth(px);
    set({ sidebarWidth: width });
    writeLocal(LS_KEYS.sidebarWidth, String(Math.round(width)));
  },
  setArtifactTab: (artifactTab) => set({ artifactTab }),
}));

/* ---- theme plumbing ------------------------------------------------
   The store holds the *preference*; the DOM always carries a resolved value,
   so CSS never has to know about "system". */

export function resolveTheme(preference: ThemePreference): ResolvedTheme {
  if (preference !== "system") return preference;
  if (typeof window === "undefined") return "dark";
  return window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

/** Stamp the resolved theme onto <html>. Safe to call before React mounts. */
export function applyTheme(preference: ThemePreference) {
  if (typeof document === "undefined") return;
  document.documentElement.dataset.theme = resolveTheme(preference);
}

export function readStoredTheme(): ThemePreference {
  return readLocal(
    LS_KEYS.theme,
    (raw) => (raw === "light" || raw === "dark" || raw === "system" ? raw : "dark"),
    "dark",
  );
}

/** Hydrate preferences from localStorage after mount — keeps SSR output stable. */
export function hydrateUiPreferences() {
  useUiPreferenceStore.setState({
    theme: readStoredTheme(),
    controlSplitRatio: readLocal(LS_KEYS.split, (r) => {
      const n = parseFloat(r);
      return Number.isFinite(n) ? Math.min(Math.max(n, 0.3), 0.7) : 0.6;
    }, 0.6),
    cameraLayout: readLocal(LS_KEYS.cameraLayout, (r) => (r === "top" || r === "wrist" ? r : "both"), "both"),
    cameraWristFirst: readLocal(LS_KEYS.cameraOrder, (r) => r === "true", false),
    /* Chưa từng chọn thì lấy theo bề rộng màn hình: <1280px mặc định thu gọn,
       đúng tinh thần MASTER §17. Đã chọn rồi thì tôn trọng lựa chọn đó. */
    sidebarCollapsed: readLocal(
      LS_KEYS.sidebar,
      (r) => r === "true",
      typeof window !== "undefined" && !window.matchMedia("(min-width: 1280px)").matches,
    ),
    sidebarWidth: readLocal(
      LS_KEYS.sidebarWidth,
      (r) => {
        const n = parseFloat(r);
        return Number.isFinite(n) ? clampSidebarWidth(n) : SIDEBAR_DEFAULT_WIDTH;
      },
      SIDEBAR_DEFAULT_WIDTH,
    ),
  });
}

/* ----------------------------------------------------------------
   Toast Store — MASTER §14.3 row 1 / §14.4
   Non-blocking errors and confirmations only. Anything tied to the button
   the user just pressed goes inline instead.
   ---------------------------------------------------------------- */
export interface Toast {
  id: string;
  tone: "success" | "error" | "info";
  message: string;
}

interface ToastStore {
  toasts: Toast[];
  push: (tone: Toast["tone"], message: string) => void;
  dismiss: (id: string) => void;
}

export const useToastStore = create<ToastStore>((set) => ({
  toasts: [],
  push: (tone, message) =>
    set((s) => ({
      toasts: [...s.toasts, { id: `toast-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`, tone, message }],
    })),
  dismiss: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
}));

/** Convenience helper so components don't need the hook inside callbacks. */
export const toast = {
  success: (message: string) => useToastStore.getState().push("success", message),
  error: (message: string) => useToastStore.getState().push("error", message),
  info: (message: string) => useToastStore.getState().push("info", message),
};

/* ----------------------------------------------------------------
   Realtime Store — SSE events per session (MASTER §18.2)
   ---------------------------------------------------------------- */
export interface ToolCardState {
  id: string;
  tool_name: string;
  arguments: Record<string, unknown>;
  status: ToolStatus;
  result?: Record<string, unknown>;
  error?: { code: string; message: string };
  duration_ms?: number;
  progress?: number;
}

export interface EpisodeProgressState {
  id: string;
  step: number;
  max_steps: number;
  instruction?: string;
  outcome?: EpisodeOutcome;
  stop_reason?: string;
  duration_s: number;
}

/** Một bước agent đã đi qua trong lượt chạy hiện tại.

    Đây là *tiến trình user-facing*, không phải suy luận nội bộ: nhãn lấy nguyên
    từ tập đóng của §13.2, thời lượng do client đo giữa hai sự kiện. §13.2 vẫn
    cấm hiện chain-of-thought — khối này nói agent ĐANG LÀM GÌ, không nói nó
    đang nghĩ gì. */
export interface AgentStep {
  id: string;
  label: AgentStatusLabel;
  startedAt: number;
  endedAt?: number;
}

/** Artifact image emitted by `artifact.created` — P04 §7 "Ảnh" tab. */
export interface ImageRef {
  id: string;
  url: string;
  camera: "top" | "wrist";
  captured_at: string;
  description?: string;
}

/** Transient banner above the composer (episode.stopped, robot.disconnected, run.failed). */
export interface ControlBanner {
  tone: "info" | "warning" | "error";
  message: string;
  /** Error code for i18n lookup — set for run.failed. */
  code?: string;
  /** Text to refill the composer with when the user presses "Thử lại". */
  retryInput?: string;
}

interface RealtimeStore {
  sessionId: string | null;
  connectionState: "connecting" | "connected" | "reconnecting" | "disconnected";
  streamingMessage: { id: string; content: string } | null;
  toolCards: Map<string, ToolCardState>;
  episodeProgress: EpisodeProgressState | null;
  robotState: {
    robot_status: RobotStatus;
    connection_status: ConnectionStatus;
    joints: JointState[];
  } | null;
  agentStatus: AgentStatusLabel | null;
  agentSteps: AgentStep[];
  sseLastEventTime: number | null;
  robotDisconnected: boolean;
  composerLocked: boolean;
  images: ImageRef[];
  banner: ControlBanner | null;
  runId: string | null;
  /** Bong bóng assistant mà lượt chạy hiện tại gắn vào.

      Trước đây cả tool call lẫn agent step đều tìm "assistant message cuối
      cùng" tại thời điểm gọi. Kịch bản thật phát narration → tool → narration,
      nên tool rơi vào bong bóng thứ nhất còn step rơi vào bong bóng thứ hai —
      trace bị xé làm đôi. Neo theo id thì cả hai về cùng một chỗ.

      `null` = lượt này chưa sinh ra bong bóng nào. */
  runAnchorMessageId: string | null;

  /** Dòng thời gian phẳng của lượt chạy ĐANG DIỄN RA — nguồn sự thật sống cho
      `TraceFlow` trong lúc chạy (`message.blocks` chỉ có sau khi lượt chạy
      kết thúc và bị "chốt" vào bong bóng anchor). `token.delta` nối vào khối
      text cuối; `tool.started` đẩy một khối tool mới. */
  runBlocks: TraceBlock[];
  /** `true` từ đúng lúc `startRun` được gọi (đồng bộ, trước cả request HTTP
      đầu tiên) tới khi khối đầu tiên của lượt chạy tới hoặc lượt chạy kết
      thúc — nuôi `ThinkingLine` để chỉ báo chờ hiện ra NGAY khoảnh khắc bấm
      gửi, không phải đợi round-trip mạng đầu tiên. */
  pendingRun: boolean;

  /** Closes the live event stream. Registered by useSessionStream so that a
      stop command can end the run wherever the button was pressed. */
  closeStream: (() => void) | null;

  addImage: (image: ImageRef) => void;
  setBanner: (banner: ControlBanner | null) => void;
  setRunId: (runId: string | null) => void;
  setRunAnchorMessageId: (id: string | null) => void;
  setCloseStream: (close: (() => void) | null) => void;
  setSessionId: (id: string | null) => void;
  setConnectionState: (state: RealtimeStore["connectionState"]) => void;
  appendStreamingContent: (id: string, delta: string) => void;
  finalizeMessage: () => void;
  setAgentStatus: (status: AgentStatusLabel | null) => void;
  /** Đóng bước đang mở rồi mở bước mới. Gọi từ `agent.status`. */
  pushAgentStep: (label: AgentStatusLabel) => void;
  /** Đóng bước cuối khi lượt chạy kết thúc. */
  closeAgentSteps: () => void;
  /** Dọn sạch khi mở lượt chạy mới. */
  resetAgentSteps: () => void;
  addToolCard: (card: ToolCardState) => void;
  updateToolCard: (id: string, update: Partial<ToolCardState>) => void;
  setEpisodeProgress: (progress: EpisodeProgressState | null) => void;
  updateRobotState: (state: RealtimeStore["robotState"]) => void;
  lockComposer: () => void;
  unlockComposer: () => void;
  setRobotDisconnected: (v: boolean) => void;
  setPendingRun: (v: boolean) => void;
  /** Nối `delta` vào khối text CUỐI của `runBlocks` nếu nó còn mở (`sealed:
      false`); mở khối text mới nếu khối cuối là tool hoặc chưa có khối nào
      (đây chính là điều kiện làm cho narration → tool → narration ra ĐÚNG BA
      khối tách biệt, không phải một chuỗi lẫn lộn). */
  appendTokenBlock: (delta: string) => void;
  /** `message.completed` tới — chốt khối text đang mở (nếu có) lại, để
      `token.delta` KẾ TIẾP (của lượt LLM sau, khác message_id) mở một khối
      MỚI thay vì nối tiếp vào câu vừa xong. */
  sealCurrentTextBlock: () => void;
  /** Đẩy hoặc cập nhật khối tool khớp `call.id` trong `runBlocks` — gọi song
      song với `upsertToolCall` (giữ `message.tool_calls` cho tương thích
      ngược) mỗi khi có `tool.started`/`.completed`/`.failed`. */
  upsertRunToolBlock: (call: ToolCall) => void;
  resetRunBlocks: () => void;
  reset: () => void;
}

export const useRealtimeStore = create<RealtimeStore>((set) => ({
  sessionId: null,
  connectionState: "disconnected",
  streamingMessage: null,
  toolCards: new Map(),
  episodeProgress: null,
  robotState: null,
  agentStatus: null,
  agentSteps: [],
  sseLastEventTime: null,
  robotDisconnected: false,
  composerLocked: false,
  images: [],
  banner: null,
  runId: null,
  runAnchorMessageId: null,
  runBlocks: [],
  pendingRun: false,
  closeStream: null,

  setCloseStream: (closeStream) => set({ closeStream }),
  addImage: (image) =>
    set((s) => ({ images: [image, ...s.images], sseLastEventTime: Date.now() })),
  setBanner: (banner) => set({ banner }),
  setRunId: (runId) => set({ runId }),
  setRunAnchorMessageId: (runAnchorMessageId) => set({ runAnchorMessageId }),
  setSessionId: (id) => set({ sessionId: id }),
  setConnectionState: (connectionState) => set({ connectionState }),
  appendStreamingContent: (id, delta) =>
    set((s) => ({
      streamingMessage: {
        id,
        content: (s.streamingMessage?.id === id ? s.streamingMessage.content : "") + delta,
      },
      sseLastEventTime: Date.now(),
    })),
  finalizeMessage: () => set({ streamingMessage: null }),
  setAgentStatus: (agentStatus) => set({ agentStatus }),
  pushAgentStep: (label) =>
    set((s) => {
      const last = s.agentSteps[s.agentSteps.length - 1];

      /* Cùng nhãn với bước ĐANG MỞ thì vẫn là bước đó — không mở dòng mới, và
         cũng KHÔNG đóng nó lại.

         Bản trước đóng bước rồi mới so sánh, và so bằng `last.endedAt === now`.
         Hệ quả: lần phát lại thứ nhất đóng bước đó lại, lần thứ hai thấy bước
         đã đóng nên điều kiện sai và mở một dòng TRÙNG. Backend thật phát cùng
         một `stage` nhiều lần liên tiếp (mỗi `episode.progress` một lần), nên
         trace ra bốn dòng "Đang thực thi" liền nhau. Với mock thì không lộ vì
         mỗi nhãn chỉ phát đúng một lần. */
      if (last && last.label === label && last.endedAt === undefined) return s;

      const now = Date.now();
      const steps = s.agentSteps.map((step, i) =>
        i === s.agentSteps.length - 1 && step.endedAt === undefined ? { ...step, endedAt: now } : step,
      );
      return {
        agentSteps: [...steps, { id: `step-${now}-${steps.length}`, label, startedAt: now }],
      };
    }),
  resetAgentSteps: () => set({ agentSteps: [] }),
  closeAgentSteps: () =>
    set((s) => ({
      agentSteps: s.agentSteps.map((step, i) =>
        i === s.agentSteps.length - 1 && step.endedAt === undefined
          ? { ...step, endedAt: Date.now() }
          : step,
      ),
    })),
  addToolCard: (card) =>
    set((s) => {
      const next = new Map(s.toolCards);
      next.set(card.id, card);
      return { toolCards: next, sseLastEventTime: Date.now() };
    }),
  updateToolCard: (id, update) =>
    set((s) => {
      const next = new Map(s.toolCards);
      const existing = next.get(id);
      if (existing) next.set(id, { ...existing, ...update });
      return { toolCards: next, sseLastEventTime: Date.now() };
    }),
  setEpisodeProgress: (episodeProgress) => set({ episodeProgress, sseLastEventTime: Date.now() }),
  updateRobotState: (robotState) => set({ robotState, sseLastEventTime: Date.now() }),
  lockComposer: () => set({ composerLocked: true }),
  unlockComposer: () => set({ composerLocked: false }),
  setRobotDisconnected: (v) => set({ robotDisconnected: v }),
  setPendingRun: (v) => set({ pendingRun: v }),
  appendTokenBlock: (delta) =>
    set((s) => {
      const blocks = [...s.runBlocks];
      const last = blocks[blocks.length - 1];
      if (last && last.kind === "text" && !last.sealed) {
        blocks[blocks.length - 1] = { ...last, content: last.content + delta };
      } else {
        blocks.push({
          kind: "text",
          id: `blk-${Date.now().toString(36)}-${blocks.length}`,
          content: delta,
          sealed: false,
        });
      }
      return { runBlocks: blocks, pendingRun: false, sseLastEventTime: Date.now() };
    }),
  sealCurrentTextBlock: () =>
    set((s) => {
      const last = s.runBlocks[s.runBlocks.length - 1];
      if (!last || last.kind !== "text" || last.sealed) return s;
      const blocks = [...s.runBlocks];
      blocks[blocks.length - 1] = { ...last, sealed: true };
      return { runBlocks: blocks };
    }),
  upsertRunToolBlock: (call) =>
    set((s) => {
      const idx = s.runBlocks.findIndex((b) => b.kind === "tool" && b.id === call.id);
      const blocks =
        idx === -1
          ? [...s.runBlocks, { kind: "tool" as const, id: call.id, call }]
          : s.runBlocks.map((b, i) => (i === idx ? { kind: "tool" as const, id: call.id, call } : b));
      return { runBlocks: blocks, pendingRun: false };
    }),
  resetRunBlocks: () => set({ runBlocks: [] }),
  reset: () =>
    set({
      sessionId: null,
      connectionState: "disconnected",
      streamingMessage: null,
      toolCards: new Map(),
      episodeProgress: null,
      robotState: null,
      agentStatus: null,
      agentSteps: [],
      sseLastEventTime: null,
      robotDisconnected: false,
      composerLocked: false,
      images: [],
      banner: null,
      runId: null,
      runAnchorMessageId: null,
      runBlocks: [],
      pendingRun: false,
      closeStream: null,
    }),
}));

/* ---------------------------------------------------------------- Human-in-the-loop */

/** `requestId` tương quan quyết định người dùng gửi lên với ĐÚNG yêu cầu đang
    chờ — bắt buộc trên cả ba loại để `submitHitlDecision` (lib/api/hitl.ts) có
    thể validate và để backend từ chối được một quyết định đã cũ (stale). */
export type HumanInLoopRequest =
  | { kind: "retry"; requestId: string; retryCount: number; lastError: string; runId: string }
  | { kind: "risk"; requestId: string; toolName: string; riskReason: string; runId: string }
  | { kind: "clarify"; requestId: string; question: string; options: { id: string; label: string }[]; runId: string };

interface HumanInLoopStore {
  request: HumanInLoopRequest | null;
  /** Trạng thái GỬI quyết định — nguồn DUY NHẤT cho `HumanInLoopPanel`, để
      không có state loading/error trùng lặp giữa panel và store (rule C). */
  status: "idle" | "submitting" | "error";
  error: ErrorObject | null;
  setRequest: (req: HumanInLoopRequest | null) => void;
  setSubmitting: () => void;
  setSubmitError: (error: ErrorObject) => void;
}

export const useHumanInLoopStore = create<HumanInLoopStore>((set) => ({
  request: null,
  status: "idle",
  error: null,
  // Yêu cầu mới (kể cả cùng loại, `requestId` khác) luôn reset trạng thái gửi
  // — một lỗi của yêu cầu TRƯỚC không được dính sang yêu cầu SAU.
  setRequest: (request) => set({ request, status: "idle", error: null }),
  setSubmitting: () => set({ status: "submitting", error: null }),
  setSubmitError: (error) => set({ status: "error", error }),
}));
