/* mockSseStream — MASTER §19

   Nhịp thời gian cố ý gần với một lượt chạy thật: mỗi chặng của agent giữ
   1–2 giây chứ không phải vài trăm mili-giây. §19 yêu cầu mock "phát lại kịch
   bản event theo thời gian thật" — chạy quá nhanh thì không kiểm được giao diện
   có theo kịp không, mà mắt người cũng không đọc kịp.
   Replays a realistic event script in real time so the whole chain in §13.1 is
   exercised without the Cloud:
     run.started → token.delta ×N → tool.started → tool.completed
       → episode.started → episode.progress ×N → episode.completed → run.completed
   Plus a failure script: robot.disconnected → run.failed.

   The event names and payload shapes mirror P04 §8 exactly, so swapping in the
   real `fetch` + ReadableStream client means replacing this file only. */

import type {
  ConnectionStatus,
  EpisodeOutcome,
  JointState,
  MessageRun,
  RobotStatus,
  ToolStatus,
} from "@/stores";
import { ApiError } from "@/lib/api/client";

/* ---------------------------------------------------------------- Event types */

export type SseEvent =
  | { type: "run.started"; run_id: string; input_text: string }
  | { type: "agent.status"; status: string }
  | { type: "agent.retry_loop"; request_id: string; retry_count: number; last_error: string; run_id: string }
  | { type: "agent.clarification"; request_id: string; question: string; options: { id: string; label: string }[]; run_id: string }
  | { type: "token.delta"; message_id: string; delta: string }
  | { type: "message.completed"; message_id: string; tool_calls?: number; run_metadata?: MessageRun; content?: string }
  | { type: "tool.started"; tool_call_id: string; tool_name: string; arguments: Record<string, unknown> }
  | { type: "tool.pending_approval"; request_id: string; tool_call_id: string; tool_name: string; risk_reason: string; run_id: string }
  | { type: "tool.progress"; tool_call_id: string; progress: number }
  | { type: "tool.completed"; tool_call_id: string; status: Extract<ToolStatus, "SUCCEEDED">; result: Record<string, unknown>; duration_ms: number }
  | { type: "tool.failed"; tool_call_id: string; error: { code: string; message: string }; duration_ms: number }
  | { type: "episode.started"; episode_id: string; tool_call_id: string; instruction?: string; max_steps: number }
  | { type: "episode.progress"; episode_id: string; tool_call_id?: string; step: number; max_steps: number; elapsed_s: number }
  | { type: "episode.completed"; episode_id: string; tool_call_id?: string; outcome: EpisodeOutcome; steps: number; duration_s: number; stop_reason?: string }
  | { type: "episode.stopped"; episode_id: string }
  | { type: "robot.state.updated"; robot_status: RobotStatus; connection_status: ConnectionStatus; joints: JointState[] }
  | { type: "robot.disconnected" }
  | { type: "robot.reconnected" }
  | { type: "artifact.created"; artifact: { id: string; url: string; camera: "top" | "wrist"; captured_at: string } }
  | { type: "run.completed"; run_id: string }
  | { type: "run.failed"; run_id: string; error: { code: string; message: string } }
  | { type: "run.cancelled"; run_id: string }
  | { type: "error"; error: { code: string; message: string } }
  | { type: "ping" };

interface ScriptStep {
  /** Delay before emitting, in ms. */
  after: number;
  event: SseEvent;
  /** Runner phát event này rồi TẠM DỪNG, chờ `submitMockHitlDecision` cho
      đúng `request_id` của event — không tự chạy tiếp theo hẹn giờ. Đây là
      điểm khác biệt với `APPROVAL_LINGER_MS` cũ: trước đây mock tự đếm giờ
      rồi chạy tiếp bất kể người dùng có quyết định hay chưa, nên hành vi mock
      và hành vi thật (chờ quyết định thật) không khớp nhau. */
  awaitDecision?: { kind: HitlDecisionScenario; requestId: string };
}

/* ---------------------------------------------------------------- Scripts */

const AGENT_STATUSES = [
  "Đang hiểu yêu cầu",
  "Đang quan sát",
  "Đang lập kế hoạch",
  "Đang thực thi",
  "Đang kiểm tra kết quả",
] as const;

/** Joint snapshot used by `robot.state.updated`. Moving joints run warmer. */
function jointsSnapshot(moving: boolean): JointState[] {
  const names = ["shoulder_pan", "shoulder_lift", "elbow", "wrist_1", "wrist_2", "wrist_3"];
  return names.map((name, i) => ({
    name,
    present_position: -30 + i * 25,
    present_load: moving ? 18 + i * 3 : 4,
    torque_enabled: true,
    moving,
    error_flags: [],
  }));
}

function tokenSteps(messageId: string, text: string, startAfter: number): ScriptStep[] {
  // Split on word boundaries so the typing effect looks like real streaming.
  const chunks = text.match(/\S+\s*/g) ?? [text];
  return chunks.map((delta, i) => ({
    after: i === 0 ? startAfter : 45,
    event: { type: "token.delta", message_id: messageId, delta } as SseEvent,
  }));
}

/* ---------------------------------------------------- Human-in-the-loop (mock) */

export type HitlDecisionKind = "retry" | "cancel" | "answer" | "approve" | "reject";

export interface HitlDecisionPayload {
  kind: HitlDecisionKind;
  answer?: { option_id: string };
}

type HitlDecisionScenario = "risk" | "retry" | "clarify";

interface HitlRunContext {
  runId: string;
  actionId: string;
  episodeId: string;
  maxSteps: number;
  inputText: string;
  /** Mảng bước tiến độ episode DÙNG CHUNG cho cả lần chạy đầu và lần chạy lại
      sau quyết định — cùng một episode thì tiến độ phải giống nhau. */
  progressSteps: ScriptStep[];
  clarifyOptionLabel: (optionId: string | undefined) => string | undefined;
}

/** Yêu cầu HITL của mock đang chờ quyết định — TỐI ĐA MỘT tại một thời điểm,
    khớp với việc trang điều khiển chỉ chạy một lượt chat tại một thời điểm. */
let pendingHitl: {
  runId: string;
  requestId: string;
  resolve: (decision: HitlDecisionPayload) => void;
} | null = null;

/** Gửi quyết định của người dùng cho yêu cầu HITL mock đang chờ.
 *
 * Đây là mô phỏng phía CLIENT của `POST /api/v1/runs/{run_id}/decision` —
 * endpoint thật chưa tồn tại (xem `lib/api/hitl.ts`). Validate `request_id`
 * khớp yêu cầu đang chờ, đúng tinh thần "stale request bị từ chối" mà thiết
 * kế backend thật cũng cần có (409 nếu request_id không khớp cái đang mở). */
export function submitMockHitlDecision(
  runId: string,
  requestId: string,
  decision: HitlDecisionPayload,
): Promise<void> {
  if (!pendingHitl || pendingHitl.runId !== runId || pendingHitl.requestId !== requestId) {
    return Promise.reject(
      new ApiError(409, {
        code: "AGENT_043",
        message: "Yêu cầu xác nhận này đã cũ hoặc lượt chạy không còn chờ quyết định.",
      }),
    );
  }
  const { resolve } = pendingHitl;
  pendingHitl = null;
  resolve(decision);
  return Promise.resolve();
}

let hitlRequestSeed = 0;
function nextRequestId(): string {
  hitlRequestSeed += 1;
  return `hitl-mock-${Date.now().toString(36)}-${hitlRequestSeed}`;
}

/** Context cần để tính bước tiếp theo sau một quyết định HITL, giữ theo
    `request_id` — module-level vì `pendingHitl.resolve` được gọi từ
    `submitMockHitlDecision`, một lệnh gọi TÁCH BIỆT khỏi lúc `buildActionSection`
    tạo ra yêu cầu, nên phải tra lại context thay vì đóng gói trực tiếp trong
    một closure duy nhất. */
const hitlContinuationCtx = new Map<string, HitlRunContext>();

/** Chuỗi bước sau khi `take_action` được PHÉP chạy (approve / retry / answer):
    episode chạy hết tiến độ rồi thành công — cùng đuôi mà đường "không có
    HITL" vẫn dùng. */
function successTail(ctx: HitlRunContext, instruction: string): ScriptStep[] {
  return [
    {
      after: 200,
      event: {
        type: "episode.started",
        episode_id: ctx.episodeId,
        tool_call_id: ctx.actionId,
        instruction,
        max_steps: ctx.maxSteps,
      },
    },
    ...ctx.progressSteps,
    {
      after: 300,
      event: { type: "episode.completed", episode_id: ctx.episodeId, outcome: "SUCCESS", steps: 372, duration_s: 24 },
    },
    {
      after: 120,
      event: {
        type: "tool.completed",
        tool_call_id: ctx.actionId,
        status: "SUCCEEDED",
        duration_ms: 24000,
        result: { outcome: "SUCCESS", steps: 372 },
      },
    },
  ];
}

/** Chuỗi bước khi bị từ chối (risk) hoặc huỷ (mọi loại) — tool không chạy,
    lượt chạy kết thúc trung thực thay vì giả vờ đã xong việc. */
function rejectedTail(ctx: HitlRunContext, message: string): ScriptStep[] {
  return [
    {
      after: 150,
      event: {
        type: "tool.failed",
        tool_call_id: ctx.actionId,
        error: { code: "HITL_REJECTED", message },
        duration_ms: 300,
      },
    },
    { after: 150, event: { type: "run.failed", run_id: ctx.runId, error: { code: "HITL_REJECTED", message } } },
  ];
}

/** Tính chuỗi bước tiếp theo dựa trên loại yêu cầu HITL + quyết định người
    dùng gửi lên. Đây là phần "backend mock" xử lý quyết định — tương ứng
    logic mà `human_gate_node` phía backend thật sẽ phải làm (xem PLAN). */
function continuationFor(scenario: HitlDecisionScenario, decision: HitlDecisionPayload, ctx: HitlRunContext): ScriptStep[] {
  if (decision.kind === "cancel") {
    return [{ after: 150, event: { type: "run.cancelled", run_id: ctx.runId } }];
  }

  if (scenario === "risk") {
    if (decision.kind === "reject") {
      return rejectedTail(ctx, "Người dùng đã từ chối hành động có rủi ro.");
    }
    // approve
    return [
      { after: 200, event: { type: "agent.status", status: AGENT_STATUSES[3] } },
      ...successTail(ctx, ctx.inputText),
    ];
  }

  if (scenario === "retry") {
    // decision.kind === "retry" — thử lại CHÍNH lời gọi take_action đó.
    return [
      {
        after: 150,
        event: { type: "tool.started", tool_call_id: ctx.actionId, tool_name: "take_action", arguments: { instruction: ctx.inputText } },
      },
      ...successTail(ctx, ctx.inputText),
    ];
  }

  // scenario === "clarify" — decision.kind === "answer"
  const label = ctx.clarifyOptionLabel(decision.answer?.option_id) ?? ctx.inputText;
  return [
    {
      after: 150,
      event: { type: "tool.started", tool_call_id: ctx.actionId, tool_name: "take_action", arguments: { instruction: label } },
    },
    ...successTail(ctx, label),
  ];
}

function pickHitlScenario(inputText: string): HitlDecisionScenario | "none" {
  if (/nặng/i.test(inputText)) return "risk";
  if (/retry|thử lại|lặp/i.test(inputText)) return "retry";
  if (/làm gì đó|xử lý|sắp xếp lại|dọn dẹp/i.test(inputText)) return "clarify";
  return "none";
}

const CLARIFY_OPTIONS = [
  { id: "option-1", label: "Quan sát toàn bộ khu vực rồi báo cáo" },
  { id: "option-2", label: "Sắp xếp lại đồ vật trên bàn theo nhóm" },
  { id: "option-3", label: "Dọn dẹp bề mặt bàn" },
];

/** Chuỗi bước TĨNH cho đoạn `take_action`, dừng lại đúng một bước
    `awaitDecision` khi có kịch bản HITL — phần "chạy tiếp sau quyết định"
    được `continuationFor` tính và chèn vào lúc chạy (`tick`), không nằm sẵn ở
    đây vì còn tuỳ người dùng quyết định gì. */
function buildActionSection(scenario: HitlDecisionScenario | "none", ctx: HitlRunContext): ScriptStep[] {
  if (scenario === "none") {
    return [
      {
        after: 150,
        event: { type: "tool.started", tool_call_id: ctx.actionId, tool_name: "take_action", arguments: { instruction: ctx.inputText } },
      },
      ...successTail(ctx, ctx.inputText),
    ];
  }

  const requestId = nextRequestId();
  hitlContinuationCtx.set(requestId, ctx);

  if (scenario === "risk") {
    return [
      {
        after: 150,
        event: { type: "tool.started", tool_call_id: ctx.actionId, tool_name: "take_action", arguments: { instruction: ctx.inputText } },
      },
      {
        after: 300,
        event: {
          type: "tool.pending_approval",
          request_id: requestId,
          tool_call_id: ctx.actionId,
          tool_name: "take_action",
          risk_reason: "Vật nặng (~3kg) gần giới hạn tải của khớp vai — cần xác nhận trước khi di chuyển.",
          run_id: ctx.runId,
        },
        awaitDecision: { kind: "risk", requestId },
      },
    ];
  }

  if (scenario === "retry") {
    return [
      {
        after: 150,
        event: { type: "tool.started", tool_call_id: ctx.actionId, tool_name: "take_action", arguments: { instruction: ctx.inputText } },
      },
      // Mô phỏng vài lần thử đã thất bại TRƯỚC khi agent leo thang cho người
      // dùng quyết định — khớp §1 (thiết kế backend: leo thang sau N lần
      // liên tiếp cùng lỗi), không phải mất tích rồi đột nhiên hỏi.
      {
        after: 900,
        event: { type: "tool.failed", tool_call_id: ctx.actionId, error: { code: "MCP_001", message: "Không gọi được thiết bị Edge." }, duration_ms: 800 },
      },
      {
        after: 600,
        event: {
          type: "agent.retry_loop",
          request_id: requestId,
          retry_count: 3,
          last_error: "Tool `take_action` đã thất bại 3 lần liên tiếp với cùng lỗi MCP_001.",
          run_id: ctx.runId,
        },
        awaitDecision: { kind: "retry", requestId },
      },
    ];
  }

  // clarify — hỏi TRƯỚC khi gọi take_action, vì đây là làm rõ Ý ĐỊNH tổng
  // thể chứ không phải xử lý một lời gọi tool cụ thể đã lỗi.
  return [
    {
      after: 1000,
      event: {
        type: "agent.clarification",
        request_id: requestId,
        question: "Câu lệnh của bạn có thể hiểu theo nhiều cách. Bạn muốn agent làm thế nào?",
        options: CLARIFY_OPTIONS,
        run_id: ctx.runId,
      },
      awaitDecision: { kind: "clarify", requestId },
    },
  ];
}

/** The happy path: observe → plan → execute → verify. */
function successScript(runId: string, inputText: string, seed: string): ScriptStep[] {
  const msgId = `msg-agent-${seed}`;
  const captureId = `tc-capture-${seed}`;
  const stateId = `tc-state-${seed}`;
  const actionId = `tc-action-${seed}`;
  const episodeId = `ep-${seed}`;
  const maxSteps = 400;

  const progressSteps: ScriptStep[] = [];
  for (let step = 40; step <= 360; step += 40) {
    progressSteps.push({
      after: 320,
      event: {
        type: "episode.progress",
        episode_id: episodeId,
        step,
        max_steps: maxSteps,
        elapsed_s: Math.round((step / maxSteps) * 24),
      },
    });
  }

  const ctx: HitlRunContext = {
    runId,
    actionId,
    episodeId,
    maxSteps,
    inputText,
    progressSteps,
    clarifyOptionLabel: (optionId) => CLARIFY_OPTIONS.find((o) => o.id === optionId)?.label,
  };
  const scenario = pickHitlScenario(inputText);

  return [
    { after: 120, event: { type: "run.started", run_id: runId, input_text: inputText } },
    // The robot is committed as soon as the run starts — this is what makes the
    // Stop button appear (§11.2).
    {
      after: 60,
      event: { type: "robot.state.updated", robot_status: "BUSY", connection_status: "ONLINE", joints: jointsSnapshot(true) },
    },
    { after: 900, event: { type: "agent.status", status: AGENT_STATUSES[0] } },

    ...tokenSteps(
      msgId,
      "Tôi sẽ quan sát khu vực làm việc trước, kiểm tra trạng thái các khớp, rồi thực hiện yêu cầu của bạn.",
      420,
    ),
    { after: 200, event: { type: "message.completed", message_id: msgId } },

    // ---- Observe
    { after: 1600, event: { type: "agent.status", status: AGENT_STATUSES[1] } },
    {
      after: 150,
      event: { type: "tool.started", tool_call_id: captureId, tool_name: "capture_tool", arguments: { cameras: ["top", "wrist"] } },
    },
    {
      after: 900,
      event: {
        type: "artifact.created",
        artifact: { id: `img-top-${seed}`, url: "/media/hero-poster.jpg", camera: "top", captured_at: new Date().toISOString() },
      },
    },
    {
      after: 120,
      event: {
        type: "artifact.created",
        artifact: { id: `img-wrist-${seed}`, url: "/media/hero-poster.jpg", camera: "wrist", captured_at: new Date().toISOString() },
      },
    },
    {
      after: 100,
      event: {
        type: "tool.completed",
        tool_call_id: captureId,
        status: "SUCCEEDED",
        duration_ms: 1120,
        result: {
          images: [
            { id: `img-top-${seed}`, camera: "top", url: "/media/hero-poster.jpg" },
            { id: `img-wrist-${seed}`, camera: "wrist", url: "/media/hero-poster.jpg" },
          ],
        },
      },
    },

    // ---- Read robot state
    {
      after: 250,
      event: { type: "tool.started", tool_call_id: stateId, tool_name: "get_robot_state", arguments: {} },
    },
    {
      after: 500,
      event: {
        type: "tool.completed",
        tool_call_id: stateId,
        status: "SUCCEEDED",
        duration_ms: 480,
        result: {
          joints: [
            { name: "shoulder_pan", present_position: -12.4, present_load: 8.1, torque_enabled: true, moving: false, error_flags: [] },
            { name: "shoulder_lift", present_position: 41.8, present_load: 22.4, torque_enabled: true, moving: false, error_flags: [] },
            { name: "elbow", present_position: -63.1, present_load: 31.0, torque_enabled: true, moving: false, error_flags: [] },
            { name: "wrist_1", present_position: 5.6, present_load: 6.7, torque_enabled: true, moving: false, error_flags: [] },
            { name: "wrist_2", present_position: 88.2, present_load: 4.2, torque_enabled: true, moving: false, error_flags: [] },
            { name: "wrist_3", present_position: -4.9, present_load: 3.1, torque_enabled: true, moving: false, error_flags: [] },
          ],
        },
      },
    },

    // ---- Plan + execute
    { after: 1400, event: { type: "agent.status", status: AGENT_STATUSES[2] } },
    { after: 1200, event: { type: "agent.status", status: AGENT_STATUSES[3] } },
    ...buildActionSection(scenario, ctx),

    // ---- Verify + wrap up
    { after: 1500, event: { type: "agent.status", status: AGENT_STATUSES[4] } },
    ...tokenSteps(`${msgId}-final`, "Đã hoàn thành yêu cầu. Bạn có thể xem ảnh trước và sau ở tab Ảnh.", 400),
    { after: 150, event: { type: "message.completed", message_id: `${msgId}-final` } },
    {
      after: 120,
      event: { type: "robot.state.updated", robot_status: "IDLE", connection_status: "ONLINE", joints: jointsSnapshot(false) },
    },
    { after: 200, event: { type: "run.completed", run_id: runId } },
  ];
}

/** The failure path: the robot drops mid-episode. Not a transient error — no auto-retry. */
function failureScript(runId: string, inputText: string, seed: string): ScriptStep[] {
  const msgId = `msg-agent-${seed}`;
  const actionId = `tc-action-${seed}`;
  const episodeId = `ep-${seed}`;

  return [
    { after: 120, event: { type: "run.started", run_id: runId, input_text: inputText } },
    {
      after: 60,
      event: { type: "robot.state.updated", robot_status: "BUSY", connection_status: "ONLINE", joints: jointsSnapshot(true) },
    },
    { after: 900, event: { type: "agent.status", status: AGENT_STATUSES[0] } },
    ...tokenSteps(msgId, "Tôi bắt đầu thực hiện yêu cầu.", 400),
    { after: 150, event: { type: "message.completed", message_id: msgId } },
    { after: 200, event: { type: "agent.status", status: AGENT_STATUSES[3] } },
    {
      after: 150,
      event: { type: "tool.started", tool_call_id: actionId, tool_name: "take_action", arguments: { instruction: inputText } },
    },
    { after: 200, event: { type: "episode.started", episode_id: episodeId, tool_call_id: actionId, instruction: inputText, max_steps: 400 } },
    { after: 400, event: { type: "episode.progress", episode_id: episodeId, step: 40, max_steps: 400, elapsed_s: 3 } },
    { after: 400, event: { type: "episode.progress", episode_id: episodeId, step: 92, max_steps: 400, elapsed_s: 7 } },
    { after: 500, event: { type: "robot.disconnected" } },
    {
      after: 60,
      event: { type: "robot.state.updated", robot_status: "DISCONNECTED", connection_status: "OFFLINE", joints: jointsSnapshot(false) },
    },
    {
      after: 200,
      event: {
        type: "episode.completed",
        episode_id: episodeId,
        outcome: "SAFETY_ABORT",
        steps: 92,
        duration_s: 8,
        stop_reason: "Mất kết nối với thiết bị Edge giữa lúc đang di chuyển.",
      },
    },
    {
      after: 150,
      event: {
        type: "tool.failed",
        tool_call_id: actionId,
        duration_ms: 8200,
        error: { code: "MCP_001", message: "Không gọi được thiết bị Edge." },
      },
    },
    { after: 200, event: { type: "run.failed", run_id: runId, error: { code: "MCP_001", message: "Không gọi được thiết bị Edge." } } },
  ];
}

/* ---------------------------------------------------------------- Runner */

export interface MockStreamHandle {
  /** Stop the replay and release every pending timer. */
  close: () => void;
}

export interface MockStreamOptions {
  runId: string;
  inputText: string;
  /** Force a scenario; otherwise picked by keyword/chance. */
  scenario?: "success" | "failure";
  onEvent: (event: SseEvent) => void;
}

/** Choose the script. Typing "lỗi" or "hỏng" forces the failure path so the
    broken-state UI is reachable on demand during review. */
function pickScenario(inputText: string, forced?: "success" | "failure") {
  if (forced) return forced;
  return /lỗi|hỏng|mất kết nối|disconnect/i.test(inputText) ? "failure" : "success";
}

/* Hệ số tốc độ phát lại kịch bản mock.
 *
 * `1` = nhịp gốc (một lượt chạy ~15.5s). Số NHỎ HƠN 1 thì chậm lại:
 * `0.5` = chậm gấp đôi, `0.25` = chậm gấp bốn. Số lớn hơn 1 thì nhanh lên,
 * tiện khi cần chạy qua nhiều lượt để kiểm giao diện.
 *
 * Có mặt vì nhịp gốc quá nhanh để theo dõi agent trace bằng mắt — vài bước
 * trôi qua chưa đầy một giây. Chỉ ảnh hưởng lớp mock, không có ý nghĩa gì khi
 * đã cắm SSE thật.
 *
 * Chặn dưới ở 0.05 để một giá trị gõ nhầm (0, số âm) không làm kịch bản đứng
 * hẳn hoặc phát ngược. KHÔNG áp dụng cho thời gian chờ quyết định HITL —
 * người dùng đọc panel bao lâu là tuỳ họ, không phải một hằng số nhân tốc độ. */
const SPEED = (() => {
  const raw = Number(process.env.NEXT_PUBLIC_MOCK_SPEED);
  return Number.isFinite(raw) && raw > 0 ? Math.max(raw, 0.05) : 1;
})();

export function mockSseStream({ runId, inputText, scenario, onEvent }: MockStreamOptions): MockStreamHandle {
  const seed = Date.now().toString(36).slice(-5);
  const scenarioName = pickScenario(inputText, scenario);
  const script = scenarioName === "failure" ? failureScript(runId, inputText, seed) : successScript(runId, inputText, seed);

  let cancelled = false;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let i = 0;

  const tick = () => {
    if (cancelled || i >= script.length) return;
    const step = script[i++];
    timer = setTimeout(() => {
      if (cancelled) return;
      onEvent(step.event);
      if (step.awaitDecision) {
        // Dừng thật: KHÔNG hẹn giờ tự chạy tiếp. `submitMockHitlDecision` mới
        // là thứ đánh thức runner — đúng tinh thần "chờ quyết định thật" mà
        // §1 (HITL) đòi hỏi, thay vì mock tự đếm giờ rồi cứ thế chạy tiếp bất
        // kể người dùng đã quyết định gì (hành vi cũ, sai lệch với thật).
        const { kind, requestId } = step.awaitDecision;
        pendingHitl = {
          runId,
          requestId,
          resolve: (decision) => {
            const ctx = hitlContinuationCtx.get(requestId);
            if (!ctx) return; // stream đã đóng — không còn gì để tiếp tục.
            hitlContinuationCtx.delete(requestId);
            const followUp = continuationFor(kind, decision, ctx);
            script.splice(i, 0, ...followUp);
            tick();
          },
        };
        return;
      }
      tick();
    }, step.after / SPEED);
  };

  tick();

  return {
    close: () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
      if (pendingHitl?.runId === runId) pendingHitl = null;
    },
  };
}
