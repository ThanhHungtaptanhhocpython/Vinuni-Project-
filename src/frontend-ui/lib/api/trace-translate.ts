/* trace-translate — dịch `TraceEvent` THẬT của backend sang từ vựng `SseEvent`.
 *
 * Endpoint thật là `GET /api/v1/runs/{id}/events` (SSE event tên `trace`,
 * payload = `TraceEvent.model_dump()` đầy đủ — xem `models/schemas.py` phía
 * cloud-agent và `lib/api/runs.ts`'s `TraceEvent`, cùng hình dạng). Bản trước
 * của file này nhắm vào `GET /runs/{id}/stream` (event `agent.thinking`, payload
 * đã bị `project_trace_event` rút gọn: đọc `payload.event_type` — field này
 * KHÔNG tồn tại trên payload thật, nên mọi nhánh của switch dưới đây từng là
 * dead code khi cắm vào backend thật; xem lại lịch sử file nếu cần đối chiếu.
 *
 * Tách khỏi `trace-stream.ts` vì đây là phần THUẦN: không fetch, không token,
 * không phụ thuộc trình duyệt. Nhờ vậy kiểm được bằng một script Node chạy
 * thẳng trên payload thật do backend sinh ra (`npm run verify:trace-translate`)
 * — không phải mở trình duyệt rồi cầu may.
 *
 * Chỉ `import type` ở đây, có chủ đích: import kiểu bị xoá lúc biên dịch nên
 * file này nạp được bằng `node --experimental-strip-types` mà không cần alias
 * `@/` hay bundler.
 */

import type { SseEvent } from "@/lib/mock/sse";
import type { ConnectionStatus, EpisodeOutcome, JointState, RobotStatus } from "@/stores";
/* Đường dẫn TƯƠNG ĐỐI + đủ đuôi `.ts`, không phải alias `@/` — file này được
   `npm run verify:trace-translate` nạp thẳng bằng `node --experimental-strip-
   types`, và plain Node không biết alias tsconfig. `allowImportingTsExtensions`
   (tsconfig.json) cho phép cùng cú pháp này chạy được cả trong build Next.js. */
import { EVENT_STAGE, STAGE_LABEL_VI } from "../trace/stage-labels.ts";

/** Hình dạng THẬT của một trace event — `TraceEvent` (cloud-agent
    `models/schemas.py`). MỌI field ngoài danh sách cố định này nằm trong
    `metadata` (backend đổ nguyên payload emit gốc vào đó) — xem
    `lib/api/run-timeline.ts`, file đọc CÙNG hình dạng cho lịch sử đã lưu. */
export interface TracePayload {
  id?: string;
  run_id?: string;
  trace_id?: string;
  sequence?: number;
  timestamp?: string;
  /** Tên sự kiện GỐC (`tool.started`, `run.completed`…). Đây là thứ dịch dựa vào. */
  type?: string;
  status?: string | null;
  duration_ms?: number | null;
  model?: string | null;
  tool_name?: string | null;
  arguments?: Record<string, unknown> | null;
  result_summary?: unknown;
  error?: { code?: string; message?: string } | null;
  metadata?: Record<string, unknown>;
}

const EPISODE_OUTCOMES: EpisodeOutcome[] = [
  "SUCCESS",
  "STOPPED",
  "TIMEOUT",
  "MAX_STEPS",
  "SAFETY_ABORT",
];

/* Native `edge_vla.core.task_store` dùng từ vựng khác `EpisodeOutcome`
   (DONE/ABORTED/SAFETY_STOP/FAILED_MAX_RETRY thay vì SUCCESS/STOPPED/
   SAFETY_ABORT). `agents/nodes/tool.py`'s `_monitor_episode` chuyển tiếp
   nguyên trạng thái edge trả về — ánh xạ ở đây, không phải suy đoán kết quả:
   mỗi nhánh đọc thẳng `status`/`success` thật, không có gì tự bịa. `DONE`
   không kèm `success` (thiếu trường) coi là chưa rõ kết quả → STOPPED, không
   phải SUCCESS mặc định. */
const NATIVE_OUTCOME: Record<string, EpisodeOutcome> = {
  ABORTED: "STOPPED",
  SAFETY_STOP: "SAFETY_ABORT",
  FAILED_MAX_RETRY: "STOPPED",
};

function toOutcome(status: unknown, success?: unknown): EpisodeOutcome {
  const match = EPISODE_OUTCOMES.find((o) => o === status);
  if (match) return match;
  if (status === "DONE") return success === true ? "SUCCESS" : "STOPPED";
  if (typeof status === "string" && NATIVE_OUTCOME[status]) return NATIVE_OUTCOME[status];
  /* Không đoán thành SUCCESS khi thiếu: một episode kết thúc mà không nói rõ
     kết quả thì "đã dừng" là mô tả trung thực, còn "thành công" là bịa. */
  return "STOPPED";
}

function str(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value : undefined;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function num(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function toRobotStatus(metadata: Record<string, unknown>, payload: TracePayload): RobotStatus {
  if (metadata.busy === true) return "BUSY";
  const raw = str(metadata.robot_status) ?? str(metadata.status) ?? str(payload.status);
  if (raw === "RUNNING" || raw === "BUSY") return "BUSY";
  if (raw === "STOPPING" || raw === "ERROR" || raw === "DISCONNECTED" || raw === "IDLE") {
    return raw;
  }
  return "IDLE";
}

function errorOf(payload: TracePayload, fallbackCode: string): { code: string; message: string } {
  return {
    code: payload.error?.code ?? fallbackCode,
    message: payload.error?.message ?? "Lượt chạy thất bại.",
  };
}

function robotStatusOf(metadata: Record<string, unknown>, robotState: Record<string, unknown>): RobotStatus {
  const raw = str(metadata.status) ?? str(robotState.status);
  if (raw === "IDLE" || raw === "BUSY" || raw === "STOPPING" || raw === "ERROR" || raw === "DISCONNECTED") {
    return raw;
  }
  if (robotState.busy === true) return metadata.source === "emergency_stop" ? "STOPPING" : "BUSY";
  return "IDLE";
}

function connectionStatusOf(metadata: Record<string, unknown>): ConnectionStatus {
  const raw = str(metadata.connection_status);
  if (raw === "UNREGISTERED" || raw === "ONLINE" || raw === "OFFLINE") return raw;
  return "ONLINE";
}

function jointStateOf(value: unknown, moving: boolean): JointState | null {
  if (!isRecord(value)) return null;
  const range = Array.isArray(value.range) ? value.range : [];
  const position = num(value.present_position) ?? num(value.pos);
  if (position === undefined) return null;
  const torque = num(value.torque_nm) ?? num(value.actuator_force);
  const torqueLimit =
    num(value.torque_limit_nm) ??
    (typeof range[0] === "number" && typeof range[1] === "number"
      ? Math.max(Math.abs(range[0]), Math.abs(range[1]))
      : undefined);
  return {
    name: str(value.name) ?? `joint_${num(value.id) ?? 0}`,
    present_position: position,
    present_velocity: num(value.present_velocity) ?? num(value.vel),
    target_position: num(value.target_position),
    torque_nm: torque,
    torque_limit_nm: torqueLimit,
    range_min: typeof range[0] === "number" ? range[0] : num(value.range_min),
    range_max: typeof range[1] === "number" ? range[1] : num(value.range_max),
    present_load: num(value.present_load) ?? Math.abs(torque ?? 0),
    torque_enabled: typeof value.torque_enabled === "boolean" ? value.torque_enabled : true,
    moving: typeof value.moving === "boolean" ? value.moving : moving,
    error_flags: Array.isArray(value.error_flags) ? value.error_flags.filter((flag): flag is string => typeof flag === "string") : [],
  };
}

/** Dịch một payload thành 0..n `SseEvent`.
 *
 * Trả về MẢNG vì có sự kiện backend gộp việc: `episode.completed` vừa kết thúc
 * episode vừa là chỗ duy nhất mang `outcome`. Và trả rỗng cho những sự kiện
 * giao diện không dùng, thay vì ép chúng thành một loại gần đúng. */
/** Tra id của lời gọi đang mở theo tên tool. Xem `resolveOpenToolId` bên dưới. */
export type OpenToolResolver = (toolName: string) => string | undefined;

export function traceEventToSse(
  payload: TracePayload,
  /* LƯỚI AN TOÀN cho `tool.completed`/`tool.failed` thiếu id.
   *
   * Backend thật CHỈ gắn id vào `tool.started` (`call.get("id")` trong payload
   * emit, đổ vào `metadata.id`) — `tool.completed`/`tool.failed` không kèm id
   * (xác nhận trực tiếp trong `agents/nodes/tool.py`, không phải suy đoán).
   * Thiếu id thì dòng tool sẽ QUAY MÃI dù tool đã xong — kiểu hỏng tệ nhất:
   * giao diện nói dối là còn đang chạy, người dùng ngồi chờ một thứ đã kết
   * thúc.
   *
   * Có resolver thì lấy lời gọi đang mở gần nhất CÙNG TÊN TOOL. Không hoàn hảo
   * — gọi song song hai lần cùng một tool thì có thể đóng nhầm cái — nhưng
   * đóng nhầm một dòng vẫn tốt hơn treo vĩnh viễn cả hai. */
  resolveOpenToolId?: OpenToolResolver,
): SseEvent[] {
  const type = payload.type;
  const metadata = payload.metadata ?? {};
  const out: SseEvent[] = [];

  const toolCallId = str(metadata.id);
  const closingId = toolCallId ?? (payload.tool_name ? resolveOpenToolId?.(payload.tool_name) : undefined);

  /* Nhãn giai đoạn đi kèm MỌI sự kiện suy ra được stage từ `type` — backend
     không tự gắn `stage`/`label` vào sự kiện (đó là khái niệm riêng của UI),
     nên tự suy ra bằng đúng bảng mà `run-timeline.ts` dùng cho lịch sử, để hai
     nơi không kể khác nhau cho cùng một loại sự kiện. */
  const stage = type ? EVENT_STAGE[type] : undefined;
  const status = stage ? STAGE_LABEL_VI[stage] : undefined;
  if (status) out.push({ type: "agent.status", status });

  switch (type) {
    case "run.started":
      if (payload.run_id) {
        out.push({ type: "run.started", run_id: payload.run_id, input_text: "" });
      }
      break;

    case "token.delta": {
      const messageId = str(metadata.message_id);
      const delta = typeof metadata.delta === "string" ? metadata.delta : undefined;
      if (messageId && delta) {
        out.push({ type: "token.delta", message_id: messageId, delta });
      }
      break;
    }

    case "message.completed": {
      const messageId = str(metadata.message_id);
      if (messageId) {
        out.push({
          type: "message.completed",
          message_id: messageId,
          tool_calls: num(metadata.tool_calls) ?? 0,
          ...(typeof metadata.content === "string" ? { content: metadata.content } : {}),
        });
      }
      break;
    }

    case "tool.started":
      /* Không có id thì bỏ qua hẳn: dựng một dòng không đóng lại được sẽ kẹt
         ở trạng thái "đang chạy" mãi mãi sau khi lượt chạy xong. */
      if (toolCallId && payload.tool_name) {
        out.push({
          type: "tool.started",
          tool_call_id: toolCallId,
          tool_name: payload.tool_name,
          arguments: payload.arguments ?? {},
        });
      }
      break;

    case "tool.completed":
      if (closingId) {
        out.push({
          type: "tool.completed",
          tool_call_id: closingId,
          status: "SUCCEEDED",
          // `result_summary` = `TraceEvent.result_summary` (backend
          // `agents/events.py`) = the emitted `tool.completed`'s `summary`
          // dict (`agents/nodes/tool.py` — real edge_vla's tool.data, flat
          // fields like task_id/status/step/success). Was hardcoded `{}`
          // before, so no tool ever showed a real outcome in the trace.
          result: isRecord(payload.result_summary) ? payload.result_summary : {},
          duration_ms: payload.duration_ms ?? 0,
        });
      }
      break;

    case "tool.failed":
      if (closingId) {
        out.push({
          type: "tool.failed",
          tool_call_id: closingId,
          error: errorOf(payload, "AGENT_002"),
          duration_ms: payload.duration_ms ?? 0,
        });
      }
      break;

    case "episode.started": {
      const episodeId = str(metadata.episode_id);
      /* Backend thật (A3) giờ gắn thẳng `tool_call_id` vào payload episode.* —
         đọc trực tiếp trước. Resolver theo tên tool chỉ còn là LƯỚI AN TOÀN
         cho session cũ chưa có id (xem docstring `resolveOpenToolId` ở trên). */
      const linkedToolCallId =
        str(metadata.tool_call_id) ??
        resolveOpenToolId?.("take_action") ??
        resolveOpenToolId?.("go_home");
      if (episodeId && linkedToolCallId) {
        out.push({
          type: "episode.started",
          episode_id: episodeId,
          tool_call_id: linkedToolCallId,
          max_steps: num(metadata.max_steps) ?? 0,
        });
      }
      break;
    }

    case "episode.progress": {
      const episodeId = str(metadata.episode_id);
      const linkedToolCallId =
        str(metadata.tool_call_id) ??
        resolveOpenToolId?.("take_action") ??
        resolveOpenToolId?.("go_home");
      if (episodeId) {
        out.push({
          type: "episode.progress",
          episode_id: episodeId,
          ...(linkedToolCallId ? { tool_call_id: linkedToolCallId } : {}),
          step: num(metadata.step) ?? num(metadata.steps) ?? 0,
          max_steps: num(metadata.max_steps) ?? num(metadata.total) ?? 0,
          elapsed_s: num(metadata.elapsed_s) ?? 0,
        });
      }
      break;
    }

    case "episode.completed": {
      const episodeId = str(metadata.episode_id);
      const linkedToolCallId =
        str(metadata.tool_call_id) ??
        resolveOpenToolId?.("take_action") ??
        resolveOpenToolId?.("go_home");
      if (episodeId) {
        out.push({
          type: "episode.completed",
          episode_id: episodeId,
          ...(linkedToolCallId ? { tool_call_id: linkedToolCallId } : {}),
          outcome: toOutcome(metadata.status ?? metadata.outcome, metadata.success),
          steps: num(metadata.step) ?? num(metadata.steps) ?? 0,
          duration_s: num(metadata.elapsed_s) ?? 0,
          stop_reason: str(metadata.stop_reason ?? metadata.reason),
        });
      }
      break;
    }

    case "robot.state.updated": {
      const robotState = isRecord(metadata.robot_state) ? metadata.robot_state : metadata;
      const robotStatus = robotStatusOf(metadata, robotState);
      const rawJoints = Array.isArray(robotState.joints) ? robotState.joints : [];
      out.push({
        type: "robot.state.updated",
        robot_status: robotStatus,
        connection_status: connectionStatusOf(metadata),
        joints: rawJoints.flatMap((joint) => {
          const parsed = jointStateOf(joint, robotStatus === "BUSY" || robotStatus === "STOPPING");
          return parsed ? [parsed] : [];
        }),
      });
      break;
    }

    case "run.completed":
      if (payload.run_id) out.push({ type: "run.completed", run_id: payload.run_id });
      break;

    case "run.cancelled":
      if (payload.run_id) out.push({ type: "run.cancelled", run_id: payload.run_id });
      break;

    case "run.failed":
      if (payload.run_id) {
        out.push({ type: "run.failed", run_id: payload.run_id, error: errorOf(payload, "AGENT_001") });
      }
      break;

    case "run.timeout":
      /* Giao diện không có loại riêng cho quá giờ — quy về `run.failed` với mã
         AGENT_003 để `i18n/errors.vi.json` nói đúng nguyên nhân. Bỏ qua thì
         lượt chạy kẹt "đang chạy" vĩnh viễn và ô nhập không mở khoá lại. */
      if (payload.run_id) {
        out.push({
          type: "run.failed",
          run_id: payload.run_id,
          error: {
            code: payload.error?.code ?? "AGENT_003",
            message: payload.error?.message ?? "Lượt chạy vượt quá thời gian cho phép.",
          },
        });
      }
      break;

    case "robot.state.updated": {
      const robotStatus = toRobotStatus(metadata, payload);
      out.push({
        type: "robot.state.updated",
        robot_status: robotStatus,
        connection_status: "ONLINE",
        joints: [],
      });
      break;
    }

    /* ---- Human-in-the-loop — HỢP ĐỒNG DỰ KIẾN, CHƯA CÓ TRÊN BACKEND THẬT.
     *
     * `agent.retry_loop`/`agent.clarification`/`tool.pending_approval` không
     * nằm trong danh sách `type` mà backend thật sự phát ra hôm nay (đã xác
     * nhận qua audit trực tiếp mã nguồn — không có endpoint resume/decision
     * nào, không có state "chờ người dùng" nào trong agent graph). Ba case
     * dưới đây tồn tại để:
     *   1. Frontend đã SẴN SÀNG khi backend thêm khả năng này — không phải
     *      sửa lại `useSessionStream`/`useHumanInLoopStore`/`HumanInLoopPanel`
     *      một lần nữa.
     *   2. Ghi lại RÕ hợp đồng backend cần cung cấp: mỗi sự kiện phải có một
     *      `request_id` ổn định trong `metadata` để tương quan với quyết định
     *      gửi lên `POST /runs/{run_id}/decision` (endpoint này cũng chưa tồn
     *      tại — xem `lib/api/hitl.ts`).
     * Cho tới khi đó, các `type` này không bao giờ tới từ backend thật nên
     * nhánh này không tự ý "giả lập thành công" — nó chỉ dịch đúng những gì
     * nhận được, y như mọi nhánh khác trong switch này. */
    case "agent.retry_loop": {
      const requestId = str(metadata.request_id);
      if (requestId && payload.run_id) {
        out.push({
          type: "agent.retry_loop",
          request_id: requestId,
          retry_count: num(metadata.retry_count) ?? 0,
          last_error: str(metadata.last_error) ?? errorOf(payload, "AGENT_002").message,
          run_id: payload.run_id,
        });
      }
      break;
    }

    case "agent.clarification": {
      const requestId = str(metadata.request_id);
      const options = Array.isArray(metadata.options)
        ? metadata.options.flatMap((o) =>
            o && typeof o === "object" && typeof (o as { id?: unknown }).id === "string" && typeof (o as { label?: unknown }).label === "string"
              ? [{ id: (o as { id: string }).id, label: (o as { label: string }).label }]
              : [],
          )
        : [];
      if (requestId && payload.run_id && str(metadata.question) && options.length > 0) {
        out.push({
          type: "agent.clarification",
          request_id: requestId,
          question: str(metadata.question) as string,
          options,
          run_id: payload.run_id,
        });
      }
      break;
    }

    case "tool.pending_approval": {
      const requestId = str(metadata.request_id);
      if (requestId && payload.run_id && toolCallId && payload.tool_name) {
        out.push({
          type: "tool.pending_approval",
          request_id: requestId,
          tool_call_id: toolCallId,
          tool_name: payload.tool_name,
          risk_reason: str(metadata.risk_reason) ?? "Hành động có rủi ro, cần xác nhận trước khi tiếp tục.",
          run_id: payload.run_id,
        });
      }
      break;
    }

    /* `llm.*`, `system.warning`… chỉ đóng góp nhãn giai đoạn ở trên. Không
       dựng dòng riêng. `robot.state.updated` đã được dịch ở trên để khoá
       ô chat khi cánh tay đang bận. */
    default:
      break;
  }

  return out;
}

/** Sự kiện kết thúc — chỗ gọi dừng chờ và đi lấy câu trả lời cuối. */
export function isTerminalTrace(payload: TracePayload): boolean {
  return (
    payload.type === "run.completed" ||
    payload.type === "run.failed" ||
    payload.type === "run.cancelled" ||
    payload.type === "run.timeout"
  );
}
