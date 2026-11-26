/* trace-stream — đọc `GET /api/v1/runs/{run_id}/events` theo thời gian thật.
 *
 * KHÔNG dùng `GET /runs/{id}/stream`: endpoint đó phát sự kiện SSE tên
 * `agent.thinking` với payload đã bị `project_trace_event` (cloud-agent,
 * `agents/thinking_stream.py`) rút gọn — mất field `type` gốc và không có
 * `tool_call_id` ổn định, khiến mọi nhánh dịch ở `trace-translate.ts` thành
 * dead code. `/events` phát sự kiện SSE tên `trace` với payload là
 * `TraceEvent.model_dump()` đầy đủ — cùng hình dạng mà `run-timeline.ts` đọc
 * cho lịch sử đã lưu, nên trực tiếp/lịch sử không còn kể khác nhau.
 *
 * Chỉ lo phần I/O: mở kết nối, cắt khối SSE, làm mới cookie khi access hết
 * hạn giữa chừng. Việc dịch sự kiện nằm ở `trace-translate.ts`.
 *
 * DÙNG `fetch` + ReadableStream + `credentials: "include"` (HttpOnly cookies).
 * Không dùng `EventSource` (không gửi cookie refresh flow rõ ràng như fetch).
 */

import { API_BASE } from "./config";
import { refreshAccessToken } from "./client";
import type { SseEvent } from "@/lib/mock/sse";
import { traceEventToSse, isTerminalTrace, type TracePayload } from "./trace-translate";

interface StreamOptions {
  runId: string;
  signal: AbortSignal;
  onEvent: (event: SseEvent) => void;
  /** Gọi đúng một lần khi lượt chạy kết thúc, kèm payload cuối. */
  onTerminal: (payload: TracePayload) => void;
}

function parseData(block: string): TracePayload | null {
  const data = block
    .split("\n")
    .filter((line) => line.startsWith("data:"))
    .map((line) => line.slice(5).trimStart())
    .join("\n");
  if (!data) return null;
  try {
    return JSON.parse(data) as TracePayload;
  } catch {
    return null;
  }
}

/** Đọc luồng tới khi lượt chạy kết thúc hoặc `signal` huỷ. */
export async function streamRunTrace({ runId, signal, onEvent, onTerminal }: StreamOptions): Promise<void> {
  const path = `${API_BASE}/api/v1/runs/${encodeURIComponent(runId)}/events`;

  const open = async () =>
    fetch(path, {
      method: "GET",
      headers: { Accept: "text/event-stream" },
      credentials: "include",
      signal,
    });

  let res = await open();
  if (res.status === 401) {
    const refreshed = await refreshAccessToken();
    if (refreshed) res = await open();
  }
  if (!res.ok || !res.body) {
    throw new Error(`Không mở được luồng trace (HTTP ${res.status}).`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  const openTools = new Map<string, string>();
  const resolveOpenToolId = (toolName: string) => openTools.get(toolName);

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      /* Khối SSE ngăn nhau bằng dòng trống. Giữ phần đuôi chưa trọn vẹn lại
         trong buffer — một khối có thể bị cắt làm đôi giữa hai lần đọc. */
      let sep = buffer.indexOf("\n\n");
      while (sep !== -1) {
        const block = buffer.slice(0, sep);
        buffer = buffer.slice(sep + 2);

        const payload = parseData(block);
        if (payload) {
          for (const event of traceEventToSse(payload, resolveOpenToolId)) {
            if (event.type === "tool.started") {
              openTools.set(event.tool_name, event.tool_call_id);
            } else if (event.type === "tool.completed" || event.type === "tool.failed") {
              // Đóng rồi thì gỡ khỏi sổ, để lần gọi sau không khớp nhầm vào nó.
              for (const [name, id] of openTools) {
                if (id === event.tool_call_id) openTools.delete(name);
              }
            }
            onEvent(event);
          }
          if (isTerminalTrace(payload)) {
            onTerminal(payload);
            return;
          }
        }
        sep = buffer.indexOf("\n\n");
      }
    }
  } finally {
    reader.cancel().catch(() => undefined);
  }
}
