/* HITL decision API — gửi quyết định của người dùng (retry/cancel/answer/
 * approve/reject) cho một yêu cầu Human-in-the-Loop đang chờ.
 *
 * HỢP ĐỒNG BACKEND CẦN CUNG CẤP (chưa tồn tại hôm nay — xác nhận qua audit
 * trực tiếp mã nguồn cloud-agent: không có route resume/decision/approve/
 * reject/clarify nào, không có trạng thái "chờ người dùng" nào trong agent
 * graph):
 *
 *   POST /api/v1/runs/{run_id}/decision
 *   body: { request_id: string,
 *           kind: "retry" | "cancel" | "answer" | "approve" | "reject",
 *           answer?: { option_id: string } }
 *   -> 200 { data: { run_id, request_id, status: "ACKNOWLEDGED" } }
 *   -> 404 run không tồn tại / không thuộc user
 *   -> 409 run không ở trạng thái chờ quyết định, hoặc request_id đã cũ (stale)
 *
 * Gọi hàm này ở chế độ thật sẽ 404 cho tới khi backend thêm route đó. Chỗ gọi
 * (`HumanInLoopPanel`) xử lý bằng cách giữ panel mở và hiện lỗi — KHÔNG giả
 * lập thành công cho một backend chưa có khả năng này. */

import { apiClient } from "./client";
import { USE_MOCK } from "./config";
import { submitMockHitlDecision, type HitlDecisionKind, type HitlDecisionPayload } from "@/lib/mock/sse";

export type { HitlDecisionKind, HitlDecisionPayload };

export async function submitHitlDecision(
  runId: string,
  requestId: string,
  decision: HitlDecisionPayload,
): Promise<void> {
  if (USE_MOCK) {
    await submitMockHitlDecision(runId, requestId, decision);
    return;
  }
  await apiClient.post(`/api/v1/runs/${encodeURIComponent(runId)}/decision`, {
    request_id: requestId,
    kind: decision.kind,
    answer: decision.answer,
  });
}
