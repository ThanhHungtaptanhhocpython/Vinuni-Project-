import { apiRequest } from "./client";

export interface ChatRequest {
  message: string;
  robot_id?: string;
  session_id?: string | null;
}

/** Robot_id gửi thẳng lên Cloud agent. Không còn fallback robot mặc định:
 * thiếu robot thì backend trả validation error và UI hiện trạng thái chưa
 * kết nối robot. */
export function toAgentRobotId(uiRobotId: string | null | undefined): string | undefined {
  return uiRobotId || undefined;
}

export interface ChatStartResponse {
  run_id: string;
  trace_id: string;
  session_id: string;
}

/** Mở lượt chạy và trả `run_id` NGAY, agent chạy nền.
 *
 * Khác `postChat` ở đúng chỗ quyết định: `postChat` chỉ trả lời khi agent đã
 * xong, nên trong lúc nó chạy không có gì để hiện — mọi hành động của agent
 * đến cùng một lúc, lúc đã quá muộn. Có `run_id` sớm thì mới đăng ký được
 * `/runs/{id}/stream` và xem agent làm việc theo thời gian thật. */
export async function startChat(
  payload: ChatRequest,
  options?: { signal?: AbortSignal },
): Promise<ChatStartResponse> {
  return apiRequest<ChatStartResponse>("/api/v1/chat/start", {
    method: "POST",
    body: {
      message: payload.message,
      robot_id: toAgentRobotId(payload.robot_id),
      session_id: payload.session_id ?? null,
    },
    signal: options?.signal,
  });
}
