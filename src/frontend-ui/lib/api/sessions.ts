/* Cloud session CRUD. */

import type { Message, Run, Session } from "@/stores";
import { toMessageRun } from "@/stores";
import { apiClient } from "./client";
import { fetchRun, getRunTimeline, type TraceEvent } from "./runs";
import { traceEventsToAgentSteps, traceEventsToToolCalls, traceEventsToBlocks } from "./run-timeline";
import { USE_MOCK } from "./config";
import {
  createMockSession,
  deleteMockSession,
  fetchMockMessages,
  fetchMockSessions,
  pinMockSession,
  renameMockSession,
  setMockMessageFeedback,
} from "@/lib/mock/sessions";

export function newSessionHref(robotId?: string | null): string {
  const params = new URLSearchParams({ new: "1" });
  if (robotId) params.set("robot_id", robotId);
  return `/control/new?${params.toString()}`;
}

export async function fetchSessions(params?: {
  q?: string;
  robot_id?: string;
  include_hidden?: boolean;
}): Promise<Session[]> {
  if (USE_MOCK) {
    const rows = await fetchMockSessions();
    const query = (params?.q ?? "").trim().toLowerCase();
    return rows.filter((session) => {
      if (params?.robot_id && session.robot_id !== params.robot_id) return false;
      if (!params?.include_hidden && session.title === "Phiên tự động") return false;
      if (session.title === "Phiên mới" && !session.last_message_preview && !session.active_run) {
        return false;
      }
      if (!query) return true;
      return (
        session.title.toLowerCase().includes(query) ||
        (session.last_message_preview ?? "").toLowerCase().includes(query)
      );
    });
  }
  const search = new URLSearchParams();
  if (params?.q) search.set("q", params.q);
  if (params?.robot_id) search.set("robot_id", params.robot_id);
  if (params?.include_hidden) search.set("include_hidden", "true");
  const suffix = search.toString() ? `?${search.toString()}` : "";
  return apiClient.get<Session[]>(`/api/v1/sessions${suffix}`);
}

export async function fetchLatestSession(robotId?: string): Promise<Session | null> {
  if (USE_MOCK) {
    const rows = await fetchMockSessions();
    const filtered = rows
      .filter((s) => s.title !== "Phiên tự động")
      .filter((s) => !robotId || s.robot_id === robotId)
      .sort((a, b) => (a.updated_at < b.updated_at ? 1 : -1));
    return filtered[0] ?? null;
  }
  try {
    const suffix = robotId ? `?robot_id=${encodeURIComponent(robotId)}` : "";
    return await apiClient.get<Session>(`/api/v1/sessions/latest${suffix}`);
  } catch (err) {
    if (err && typeof err === "object" && "status" in err && (err as { status: number }).status === 404) {
      return null;
    }
    throw err;
  }
}

export async function createSession(robotId: string): Promise<Session> {
  if (USE_MOCK) return createMockSession(robotId);
  return apiClient.post<Session>("/api/v1/sessions", { robot_id: robotId });
}

export async function renameSession(sessionId: string, title: string): Promise<Session> {
  if (USE_MOCK) return renameMockSession(sessionId, title);
  return apiClient.patch<Session>(`/api/v1/sessions/${sessionId}`, { title });
}

export async function deleteSession(sessionId: string): Promise<void> {
  if (USE_MOCK) return deleteMockSession(sessionId);
  await apiClient.delete(`/api/v1/sessions/${sessionId}`);
}

export interface CancelRunResult {
  run_id: string | null;
  cancel_requested: boolean;
  reason?: string;
}

/** Huỷ lượt chạy đang hoạt động của một phiên — `POST /sessions/{id}/cancel`.
 *
 * KHÁC với `stopRobot` (dừng robot vật lý/mock): cái này chỉ báo cho Agent
 * dừng suy luận/tool call giữa chừng (P1.3). Backend tự lo phần còn lại — nếu
 * robot đang chạy episode, chính backend gọi stop_robot; giao diện KHÔNG tự
 * suy ra "đã huỷ", nó chỉ gọi API rồi đợi sự kiện `run.cancelled` thật từ
 * stream cập nhật UI (xem `useSessionStream.ts`). Không có mock: chỉ có ý
 * nghĩa khi nói chuyện với backend thật đang giữ run_id đó. */
export async function cancelSessionRun(sessionId: string): Promise<CancelRunResult> {
  return apiClient.post<CancelRunResult>(`/api/v1/sessions/${sessionId}/cancel`);
}

/** Ghim phiên lên đầu danh sách.
 *
 * Lưu ở BACKEND chứ không phải `localStorage`: ghim là dữ liệu của người dùng,
 * không phải tuỳ chọn giao diện (§18.2) — đổi máy hay đổi trình duyệt thì phiên
 * đã ghim vẫn phải còn đó. */
export async function pinSession(sessionId: string, pinned: boolean): Promise<Session> {
  if (USE_MOCK) return pinMockSession(sessionId, pinned);
  return apiClient.patch<Session>(`/api/v1/sessions/${sessionId}`, { pinned });
}

export async function fetchSession(sessionId: string): Promise<Session | null> {
  if (USE_MOCK) {
    return (await fetchMockSessions()).find((s) => s.id === sessionId) ?? null;
  }
  try {
    return await apiClient.get<Session>(`/api/v1/sessions/${sessionId}`);
  } catch (err) {
    if (err && typeof err === "object" && "status" in err && (err as { status: number }).status === 404) {
      return null;
    }
    throw err;
  }
}

/** PATCH /sessions/{id}/messages/{messageId}/feedback — like/dislike một câu trả lời.
 *
 * Gửi lại `null` để bỏ chọn (bấm lại nút đang bật). Chỉ áp dụng cho tin nhắn
 * assistant — backend từ chối nếu message_id trỏ vào tin nhắn của người dùng. */
export async function setMessageFeedback(
  sessionId: string,
  messageId: string,
  feedback: "like" | "dislike" | null,
): Promise<Message> {
  if (USE_MOCK) return setMockMessageFeedback(sessionId, messageId, feedback);
  return apiClient.patch<Message>(
    `/api/v1/sessions/${sessionId}/messages/${messageId}/feedback`,
    { feedback },
  );
}

export async function fetchMessages(sessionId: string): Promise<Message[]> {
  if (USE_MOCK) return fetchMockMessages(sessionId);

  const rows = await apiClient.get<(Message & { run_id?: string | null })[]>(
    `/api/v1/sessions/${sessionId}/messages`,
  );

  /* Dựng lại chặng đường agent cho từng tin nhắn assistant.
   *
   * Backend không lưu `agent_steps`/`tool_calls` cùng tin nhắn — chúng sống
   * trong kho sự kiện trace, đánh chỉ mục theo `run_id`. Không lấy về thì mở
   * lại một phiên cũ chỉ còn chữ, và toàn bộ việc agent đã làm biến mất.
   *
   * Đọc từ kho sự kiện chứ không nhân bản sang bảng tin nhắn: cùng nguồn với
   * trang Nhật ký, nên hai nơi không thể kể hai câu chuyện khác nhau.
   *
   * Chạy SONG SONG và nuốt lỗi từng cái: một lượt chạy cũ đã bị dọn trace thì
   * tin nhắn đó mất phần chặng đường, các tin còn lại không việc gì. */
  const runIds = [
    ...new Set(
      rows
        .filter((m) => m.role === "assistant" && m.run_id)
        .map((m) => m.run_id as string),
    ),
  ];
  if (runIds.length === 0) return rows;

  const traces = new Map<string, TraceEvent[]>();
  const runDetails = new Map<string, Run>();
  await Promise.all(
    runIds.map(async (runId) => {
      try {
        traces.set(runId, await getRunTimeline(runId));
      } catch {
        /* lượt chạy không còn trace — bỏ qua, tin nhắn vẫn hiện chữ */
      }
      try {
        /* Execution + verification status cho `MessageStatusLine` — cùng
           `run_id`, cùng lượt fetch song song, không phải endpoint mới. */
        const run = await fetchRun(runId);
        if (run) runDetails.set(runId, run);
      } catch {
        /* run không fetch được — tin nhắn vẫn hiện chữ, chỉ thiếu status line */
      }
    }),
  );

  return rows.map((m) => {
    /* CHỈ tin nhắn assistant. Tin nhắn của người dùng mang CÙNG `run_id` — nó
       chính là câu lệnh mở ra lượt chạy đó — nên thiếu điều kiện vai trò thì
       khối trace mọc luôn trong bong bóng xanh của người dùng, kể lại chặng
       đường agent bên trong câu người ta vừa gõ. */
    const isAssistantWithRun = m.role === "assistant" && m.run_id;
    const events = isAssistantWithRun ? traces.get(m.run_id as string) : undefined;
    const runDetail = isAssistantWithRun ? runDetails.get(m.run_id as string) : undefined;
    if ((!events || events.length === 0) && !runDetail) return m;
    return {
      ...m,
      ...(events && events.length > 0
        ? {
            agent_steps: traceEventsToAgentSteps(events),
            tool_calls: traceEventsToToolCalls(events),
            blocks: traceEventsToBlocks(events, m.content),
          }
        : {}),
      ...(runDetail ? { run: toMessageRun(runDetail) } : {}),
    };
  });
}

