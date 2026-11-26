/* Dịch `TraceEvent[]` của backend sang `TimelineEntry[]` mà `TimelineView` vẽ.
 *
 * Cùng vai trò với `trace-translate.ts` nhưng cho trang Nhật ký chi tiết (P09):
 * ở đó là luồng SSE đang chạy, ở đây là lịch sử đã lưu. Hai nơi đọc cùng một
 * kho sự kiện nhưng cần hai hình dạng khác nhau, nên tách riêng.
 *
 * Thuần, không I/O — cùng lý do như `trace-translate`. */

import type { TraceEvent } from "./runs";
import type { TimelineEntry } from "@/components/logs/TimelineView";
import type { AgentStep, AgentStatusLabel, ToolCall, ToolStatus, TraceBlock } from "@/stores";
import { isTraceHiddenTool } from "@/lib/tool-display";
import { EVENT_STAGE, STAGE_LABEL_VI } from "@/lib/trace/stage-labels";

function str(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value : undefined;
}

function num(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** Id của lời gọi tool — emitter lưu payload gốc vào `metadata`. */
function toolCallId(event: TraceEvent): string {
  return str(event.metadata?.id) ?? `${event.run_id}-${event.sequence}`;
}

const TOOL_STATUS_BY_TYPE: Record<string, ToolStatus> = {
  "tool.started": "RUNNING",
  "tool.completed": "SUCCEEDED",
  "tool.failed": "FAILED",
};

/** Gộp `tool.started` với `tool.completed`/`tool.failed` thành MỘT dòng.
 *
 * Timeline kể một lời gọi tool là một mắt xích, không phải hai. Ghép theo id;
 * sự kiện kết thúc mang `duration_ms` và lỗi, sự kiện bắt đầu mang tham số. */
export function buildToolCalls(events: TraceEvent[]): Map<string, ToolCall> {
  const calls = new Map<string, ToolCall>();
  for (const event of events) {
    const status = TOOL_STATUS_BY_TYPE[event.type];
    if (!status) continue;
    const id = toolCallId(event);
    const existing = calls.get(id);
    const error = event.error
      ? {
          code: str(event.error.code) ?? "AGENT_002",
          message: str(event.error.message) ?? "Tool thất bại.",
        }
      : existing?.error;
    calls.set(id, {
      id,
      tool_name: str(event.tool_name) ?? existing?.tool_name ?? "unknown_tool",
      arguments: event.arguments ?? existing?.arguments ?? {},
      /* Trạng thái chỉ TIẾN, không lùi: `tool.started` đến sau `tool.completed`
         (thứ tự đảo do sequence) không được kéo dòng về lại "đang chạy". */
      status: existing && existing.status !== "RUNNING" ? existing.status : status,
      duration_ms: num(event.duration_ms) ?? existing?.duration_ms,
      // Chỉ `tool.completed` mang `result_summary` thật — giữ lại của
      // `existing` khi sự kiện đang xét không có, để không bị `tool.started`
      // đến sau (thứ tự đảo do sequence) xoá mất kết quả đã có.
      result: isRecord(event.result_summary) ? event.result_summary : existing?.result,
      error,
    });
  }
  return calls;
}

/* Gộp `episode.started`/`.progress`/`.completed` thành MỘT dòng theo
   `episode_id` — cùng lý do với `buildToolCalls`. `episode.progress` KHÔNG tự
   thành dòng riêng (bắn liên tục trong lúc poll robot, xem `nodes/tool.py`) —
   chỉ đọc để lấy `step`/`max_steps` MỚI NHẤT gắn vào dòng đã gộp, đúng kiểu
   throttle-ở-UI mà không đụng backend. */
interface TimelineEpisode {
  episodeId: string;
  outcome?: string;
  step?: number;
  maxSteps?: number;
  startedAt?: string;
  completedAt?: string;
}

function episodeIdOf(event: TraceEvent): string | undefined {
  return str(event.metadata?.episode_id);
}

export function buildEpisodes(events: TraceEvent[]): Map<string, TimelineEpisode> {
  const episodes = new Map<string, TimelineEpisode>();
  for (const event of events) {
    if (!event.type.startsWith("episode.")) continue;
    const id = episodeIdOf(event);
    if (!id) continue;
    const existing = episodes.get(id) ?? { episodeId: id };
    if (event.type === "episode.started") {
      existing.startedAt = event.timestamp;
    } else if (event.type === "episode.progress") {
      const step = num(event.metadata?.step);
      const maxSteps = num(event.metadata?.max_steps);
      if (step !== undefined) existing.step = step;
      if (maxSteps !== undefined) existing.maxSteps = maxSteps;
    } else if (event.type === "episode.completed") {
      existing.completedAt = event.timestamp;
      existing.outcome = str(event.metadata?.status);
    }
    episodes.set(id, existing);
  }
  return episodes;
}

/* Gộp `verification.started`/`.completed` theo `tool_call_id` — cùng mắt xích
   của MỘT lần verify, không phải hai. `status`/`reason` chỉ có ở
   `.completed`; chưa tới thì dòng vẫn ở trạng thái "đang xác minh". */
interface TimelineVerification {
  key: string;
  status?: string;
  reason?: string;
  startedAt?: string;
  completedAt?: string;
}

function verificationKeyOf(event: TraceEvent): string {
  return str(event.metadata?.tool_call_id) ?? `${event.run_id}-verification`;
}

export function buildVerifications(events: TraceEvent[]): Map<string, TimelineVerification> {
  const out = new Map<string, TimelineVerification>();
  for (const event of events) {
    if (event.type !== "verification.started" && event.type !== "verification.completed") continue;
    const key = verificationKeyOf(event);
    const existing = out.get(key) ?? { key };
    if (event.type === "verification.started") {
      existing.startedAt = event.timestamp;
    } else {
      existing.completedAt = event.timestamp;
      existing.status = str(event.metadata?.status);
      existing.reason = str(event.metadata?.reason);
    }
    out.set(key, existing);
  }
  return out;
}

export function traceEventsToTimeline(events: TraceEvent[]): TimelineEntry[] {
  const ordered = [...events].sort((a, b) => a.sequence - b.sequence);
  const calls = buildToolCalls(ordered);
  const episodes = buildEpisodes(ordered);
  const verifications = buildVerifications(ordered);
  const seenToolRows = new Set<string>();
  const seenEpisodeRows = new Set<string>();
  const seenVerificationRows = new Set<string>();
  /* `robot.state.updated` bắn nhiều lần với CÙNG status trong lúc poll robot
     (episode monitor, `nodes/tool.py`) — chỉ thành dòng khi status THAY ĐỔI
     so với dòng robot_state gần nhất đã render, không phải mọi tick. */
  let lastRobotStatus: string | undefined;
  const entries: TimelineEntry[] = [];

  for (const event of ordered) {
    switch (event.type) {
      case "run.started":
        entries.push({
          kind: "run_started",
          at: event.timestamp,
          source: str(event.metadata?.source) ?? "CHAT",
        });
        break;

      case "llm.completed":
        entries.push({
          kind: "llm_call",
          at: event.timestamp,
          duration_ms: num(event.duration_ms) ?? 0,
        });
        break;

      case "tool.started": {
        /* Dòng tool đặt ở vị trí BẮT ĐẦU — đó là lúc hành động xảy ra. Nhưng
           nội dung lấy từ bản đã gộp, nên nó mang cả kết quả và thời lượng. */
        if (isTraceHiddenTool(str(event.tool_name) ?? "")) break;
        const id = toolCallId(event);
        const call = calls.get(id);
        if (call && !seenToolRows.has(id)) {
          seenToolRows.add(id);
          entries.push({ kind: "tool_call", at: event.timestamp, toolCall: call });
        }
        break;
      }

      case "episode.started": {
        const id = episodeIdOf(event);
        const ep = id ? episodes.get(id) : undefined;
        if (ep && !seenEpisodeRows.has(ep.episodeId)) {
          seenEpisodeRows.add(ep.episodeId);
          entries.push({
            kind: "episode",
            at: ep.startedAt ?? event.timestamp,
            episodeId: ep.episodeId,
            outcome: ep.outcome,
            step: ep.step,
            maxSteps: ep.maxSteps,
            durationMs:
              ep.startedAt && ep.completedAt
                ? Date.parse(ep.completedAt) - Date.parse(ep.startedAt)
                : undefined,
          });
        }
        break;
      }

      case "verification.started": {
        const key = verificationKeyOf(event);
        const v = verifications.get(key);
        if (v && !seenVerificationRows.has(key)) {
          seenVerificationRows.add(key);
          entries.push({
            kind: "verification",
            at: v.startedAt ?? event.timestamp,
            status: v.status,
            reason: v.reason,
          });
        }
        break;
      }

      case "robot.state.updated": {
        const status = str(event.metadata?.status);
        if (status && status !== lastRobotStatus) {
          lastRobotStatus = status;
          entries.push({ kind: "robot_state", at: event.timestamp, status });
        }
        break;
      }

      case "run.completed":
      case "run.failed":
      case "run.cancelled":
      case "run.timeout":
        entries.push({
          kind: "run_completed",
          at: event.timestamp,
          ok: event.type === "run.completed",
          outcome: event.type.split(".")[1].toUpperCase(),
        });
        break;

      /* `llm.started`, `run.preflight_completed`, `episode.progress`,
         `verification.completed` (đã gộp vào dòng `.started` ở trên),
         `message.completed`… không thành dòng riêng — timeline kể các mắt
         xích của lượt chạy, không phải mọi tick nội bộ. */
      default:
        break;
    }
  }

  return entries;
}

/* --------------------------------------------------- dựng lại trace lịch sử */

/** Chặng đường agent đã đi, dựng lại từ kho sự kiện của một lượt chạy.
 *
 * Dùng khi MỞ LẠI một phiên cũ. Trace lúc chạy sống trong store phía client và
 * mất khi tải lại trang; backend không lưu `agent_steps` cùng tin nhắn. Nhưng
 * kho sự kiện trace thì còn nguyên, và nó là nguồn đáng tin hơn — cùng dữ liệu
 * mà trang Nhật ký đọc, nên hai nơi không thể kể hai câu chuyện khác nhau.
 *
 * `startedAt`/`endedAt` lấy từ MỐC THẬT của sự kiện, không phải giờ client lúc
 * tải lại: mở một phiên của tuần trước mà thấy "vừa xong 2 giây trước" thì còn
 * tệ hơn không hiện gì. */
export function traceEventsToAgentSteps(events: TraceEvent[]): AgentStep[] {
  const ordered = [...events].sort((a, b) => a.sequence - b.sequence);
  const steps: AgentStep[] = [];

  for (const event of ordered) {
    const label = STAGE_LABEL_VI[EVENT_STAGE[event.type] ?? ""] as AgentStatusLabel | undefined;
    if (!label) continue;
    const at = Date.parse(event.timestamp);
    if (!Number.isFinite(at)) continue;

    const last = steps[steps.length - 1];
    // Cùng nhãn với bước đang mở thì vẫn là bước đó — cùng quy tắc gộp như
    // `pushAgentStep`, nếu không lịch sử sẽ dài hơn lúc chạy trực tiếp.
    if (last && last.label === label && last.endedAt === undefined) continue;
    if (last && last.endedAt === undefined) last.endedAt = at;
    steps.push({ id: `step-${event.sequence}`, label, startedAt: at });
  }

  const last = steps[steps.length - 1];
  if (last && last.endedAt === undefined) {
    const end = Date.parse(ordered[ordered.length - 1]?.timestamp ?? "");
    last.endedAt = Number.isFinite(end) ? end : last.startedAt;
  }
  return steps;
}

/** Tool đã gọi trong một lượt chạy, theo đúng thứ tự xảy ra. */
export function traceEventsToToolCalls(events: TraceEvent[]): ToolCall[] {
  const ordered = [...events].sort((a, b) => a.sequence - b.sequence);
  const calls = buildToolCalls(ordered);
  const out: ToolCall[] = [];
  const seen = new Set<string>();
  for (const event of ordered) {
    if (event.type !== "tool.started") continue;
    const id = toolCallId(event);
    if (seen.has(id)) continue;
    const call = calls.get(id);
    if (!call) continue;
    seen.add(id);
    const at = Date.parse(event.timestamp);
    out.push({ ...call, started_at: Number.isFinite(at) ? at : undefined });
  }
  return out;
}

/** Dựng lại luồng TraceBlock (của TraceFlow) từ kho sự kiện.
 *
 * TraceBlock giữ thứ tự tool calls theo như khi chúng xảy ra (dựa vào `sequence`).
 * Chữ cuối cùng (final content) được thêm thành khối text đóng lại ở cuối nếu có. */
export function traceEventsToBlocks(events: TraceEvent[], finalContent?: string): TraceBlock[] {
  const ordered = [...events].sort((a, b) => a.sequence - b.sequence);
  const calls = buildToolCalls(ordered);
  const out: TraceBlock[] = [];
  const seen = new Set<string>();
  let usedFinalContent = false;

  for (const event of ordered) {
    if (event.type === "tool.started") {
      if (isTraceHiddenTool(str(event.tool_name) ?? "")) continue;
      const id = toolCallId(event);
      if (seen.has(id)) continue;
      const call = calls.get(id);
      if (!call) continue;
      seen.add(id);
      out.push({
        kind: "tool",
        id,
        call,
      });
    } else if (event.type === "message.completed") {
      const content = str(event.metadata?.content);
      const toolCallsCount = num(event.metadata?.tool_calls) ?? 0;

      if (content) {
        let textToUse = content;

        // Use the canonical finalContent for the final message to avoid trace truncation (2000 chars limit)
        if (toolCallsCount === 0 && finalContent && finalContent.trim()) {
          textToUse = finalContent;
          usedFinalContent = true;
        }

        out.push({
          kind: "text",
          id: `text-${event.sequence}`,
          content: textToUse,
          sealed: true,
        });
      }
    }
  }

  if (!usedFinalContent && finalContent && finalContent.trim()) {
    out.push({ kind: "text", id: "final-text", content: finalContent, sealed: true });
  }

  return out;
}
