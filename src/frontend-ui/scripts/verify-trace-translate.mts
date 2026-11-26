/* Kiểm bộ dịch sự kiện trace: backend → `SseEvent`.
 *
 * Chạy trên ĐÚNG hình dạng payload mà `GET /runs/{id}/events` phát ra —
 * `TraceEvent.model_dump()` (field `type`, mọi thứ khác nằm trong `metadata`)
 * — chứ KHÔNG phải hình dạng đã bị `agents/thinking_stream.py` rút gọn của
 * `GET /runs/{id}/stream` (field `event_type`/`stage`, không có `metadata`).
 * Nếu backend đổi hình dạng `/events`, script này gãy — chứ không phải phát
 * hiện lúc demo.
 *
 * `.mts` chứ không phải `.mjs` như các script verify khác: mô-đun được kiểm là
 * TypeScript, và `node --experimental-strip-types` nạp thẳng được vì
 * `trace-translate.ts` cố ý chỉ có `import type`.
 *
 *   npm run verify:trace-translate
 */

import assert from "node:assert/strict";
import { traceEventToSse, isTerminalTrace } from "../lib/api/trace-translate.ts";

let checks = 0;
function check(name: string, fn: () => void) {
  fn();
  checks += 1;
  console.log(`  ✓ ${name}`);
}

check("tool.started giữ tên tool và tham số — thứ làm nên dòng hành động", () => {
  const [status, tool] = traceEventToSse({
    type: "tool.started",
    tool_name: "capture_tool",
    arguments: { cameras: ["top", "wrist"] },
    metadata: { id: "call_1" },
  });
  assert.deepEqual(status, { type: "agent.status", status: "Đang thực thi" });
  assert.deepEqual(tool, {
    type: "tool.started",
    tool_call_id: "call_1",
    tool_name: "capture_tool",
    arguments: { cameras: ["top", "wrist"] },
  });
});

check("thiếu id trong metadata thì KHÔNG dựng dòng tool.started — dòng không đóng được sẽ kẹt mãi", () => {
  const events = traceEventToSse({
    type: "tool.started",
    tool_name: "capture_tool",
    metadata: {},
  });
  assert.equal(events.filter((e) => e.type === "tool.started").length, 0);
});

check("tool.completed đóng đúng dòng đã mở (id thật từ metadata)", () => {
  const events = traceEventToSse({
    type: "tool.completed",
    duration_ms: 812,
    metadata: { id: "call_1" },
  });
  const done = events.find((e) => e.type === "tool.completed");
  assert.deepEqual(done, {
    type: "tool.completed",
    tool_call_id: "call_1",
    status: "SUCCEEDED",
    result: {},
    duration_ms: 812,
  });
});

check("tool.completed thiếu id vẫn đóng được nhờ resolver theo tên tool — không để dòng quay mãi", () => {
  const events = traceEventToSse(
    {
      type: "tool.completed",
      tool_name: "capture_tool",
      duration_ms: 500,
      metadata: {},
    },
    (name) => (name === "capture_tool" ? "call_1" : undefined),
  );
  const done = events.find((e) => e.type === "tool.completed");
  assert.ok(done && done.type === "tool.completed");
  assert.equal(done.tool_call_id, "call_1");
});

check("id thật trong metadata luôn thắng resolver", () => {
  const [, done] = traceEventToSse(
    {
      type: "tool.completed",
      tool_name: "capture_tool",
      metadata: { id: "call_that" },
    },
    () => "call_doan",
  );
  assert.equal(done.type === "tool.completed" && done.tool_call_id, "call_that");
});

check("episode.progress mang đủ số cho thanh tiến độ, đọc từ metadata", () => {
  const events = traceEventToSse({
    type: "episode.progress",
    metadata: { episode_id: "ep_1", step: 120, max_steps: 400, elapsed_s: 12 },
  });
  assert.deepEqual(events.find((e) => e.type === "episode.progress"), {
    type: "episode.progress",
    episode_id: "ep_1",
    step: 120,
    max_steps: 400,
    elapsed_s: 12,
  });
});

check("episode.started suy ra tool_call_id qua resolver take_action/go_home", () => {
  const events = traceEventToSse(
    { type: "episode.started", metadata: { episode_id: "ep_1", max_steps: 400 } },
    (name) => (name === "take_action" ? "call_action" : undefined),
  );
  assert.deepEqual(events.find((e) => e.type === "episode.started"), {
    type: "episode.started",
    episode_id: "ep_1",
    tool_call_id: "call_action",
    max_steps: 400,
  });
});

check("episode thiếu outcome ra STOPPED, không tự nhận SUCCESS", () => {
  const [, done] = traceEventToSse({
    type: "episode.completed",
    metadata: { episode_id: "ep_1", steps: 360 },
  });
  assert.equal(done.type === "episode.completed" && done.outcome, "STOPPED");
});

check("run.timeout quy về run.failed AGENT_003 — nếu bỏ qua, ô nhập kẹt khoá", () => {
  const events = traceEventToSse({ type: "run.timeout", run_id: "run_x" });
  const failed = events.find((e) => e.type === "run.failed");
  assert.ok(failed && failed.type === "run.failed");
  assert.equal(failed.error.code, "AGENT_003");
});

check("mọi sự kiện kết thúc đều nhận ra được", () => {
  for (const t of ["run.completed", "run.failed", "run.cancelled", "run.timeout"]) {
    assert.ok(isTerminalTrace({ type: t }), t);
  }
  assert.equal(isTerminalTrace({ type: "tool.started" }), false);
});

check("llm.* chỉ góp nhãn giai đoạn, không thành dòng riêng (§13.2)", () => {
  const events = traceEventToSse({
    type: "llm.started",
    result_summary: "suy luận nội bộ không được phép lộ",
  });
  assert.deepEqual(events, [{ type: "agent.status", status: "Đang lập kế hoạch" }]);
});

check("agent.retry_loop CHƯA được backend thật phát ra — thiếu request_id thì không dựng yêu cầu HITL nào (không giả lập)", () => {
  const events = traceEventToSse({
    type: "agent.retry_loop",
    run_id: "run_x",
    metadata: { retry_count: 3, last_error: "MCP_001" },
  });
  assert.equal(events.filter((e) => e.type === "agent.retry_loop").length, 0);
});

check("agent.retry_loop dịch đúng khi có đủ request_id + run_id (hợp đồng dự kiến)", () => {
  const events = traceEventToSse({
    type: "agent.retry_loop",
    run_id: "run_x",
    metadata: { request_id: "hitl_1", retry_count: 3, last_error: "MCP_001" },
  });
  assert.deepEqual(events.find((e) => e.type === "agent.retry_loop"), {
    type: "agent.retry_loop",
    request_id: "hitl_1",
    retry_count: 3,
    last_error: "MCP_001",
    run_id: "run_x",
  });
});

check("token.delta dịch thẳng message_id + delta — nguồn chữ streaming thật (A4/B1)", () => {
  const events = traceEventToSse({
    type: "token.delta",
    metadata: { message_id: "run_1-m1", delta: "Xin chào" },
  });
  assert.deepEqual(
    events.find((e) => e.type === "token.delta"),
    { type: "token.delta", message_id: "run_1-m1", delta: "Xin chào" },
  );
});

check("token.delta thiếu message_id hoặc delta thì bỏ qua — không dựng khối text rỗng/lạc chủ", () => {
  const events = traceEventToSse({ type: "token.delta", metadata: { delta: "abc" } });
  assert.equal(events.filter((e) => e.type === "token.delta").length, 0);
});

check("message.completed mang message_id + tool_calls — FE dùng để chốt khối narration hay câu trả lời cuối", () => {
  const events = traceEventToSse({
    type: "message.completed",
    metadata: { message_id: "run_1-m1", tool_calls: 2 },
  });
  assert.deepEqual(events.find((e) => e.type === "message.completed"), {
    type: "message.completed",
    message_id: "run_1-m1",
    tool_calls: 2,
  });
});

check("tool.completed đọc error object thật (A2) khi tool.failed — không rơi về AGENT_002 chung chung", () => {
  const events = traceEventToSse({
    type: "tool.failed",
    duration_ms: 500,
    metadata: { id: "call_1" },
    error: { code: "MCP_003", message: "Robot timeout" },
  });
  const failed = events.find((e) => e.type === "tool.failed");
  assert.ok(failed && failed.type === "tool.failed");
  assert.deepEqual(failed.error, { code: "MCP_003", message: "Robot timeout" });
});

check("episode.started đọc thẳng metadata.tool_call_id (A3) — không cần đoán qua resolver", () => {
  const events = traceEventToSse({
    type: "episode.started",
    metadata: { episode_id: "ep_1", max_steps: 400, tool_call_id: "call_action" },
  });
  assert.deepEqual(events.find((e) => e.type === "episode.started"), {
    type: "episode.started",
    episode_id: "ep_1",
    tool_call_id: "call_action",
    max_steps: 400,
  });
});

check("episode.progress/completed giữ tool_call_id để cập nhật đúng dòng tool đang chạy", () => {
  const progress = traceEventToSse({
    type: "episode.progress",
    metadata: { episode_id: "ep_1", step: 80, max_steps: 400, elapsed_s: 8, tool_call_id: "call_action" },
  }).find((e) => e.type === "episode.progress");
  assert.deepEqual(progress, {
    type: "episode.progress",
    episode_id: "ep_1",
    tool_call_id: "call_action",
    step: 80,
    max_steps: 400,
    elapsed_s: 8,
  });

  const completed = traceEventToSse({
    type: "episode.completed",
    metadata: { episode_id: "ep_1", status: "DONE", success: true, step: 80, elapsed_s: 12, tool_call_id: "call_action" },
  }).find((e) => e.type === "episode.completed");
  assert.deepEqual(completed, {
    type: "episode.completed",
    episode_id: "ep_1",
    tool_call_id: "call_action",
    outcome: "SUCCESS",
    steps: 80,
    duration_s: 12,
    stop_reason: undefined,
  });
});

check("robot.state.updated từ Edge busy/RUNNING thành BUSY — khoá ô chat khi take_action đang chạy", () => {
  const busy = traceEventToSse({
    type: "robot.state.updated",
    metadata: { busy: true, status: "RUNNING" },
  }).find((e) => e.type === "robot.state.updated");
  assert.deepEqual(busy, {
    type: "robot.state.updated",
    robot_status: "BUSY",
    connection_status: "ONLINE",
    joints: [],
  });

  const idle = traceEventToSse({
    type: "robot.state.updated",
    metadata: { busy: false, robot_status: "IDLE" },
  }).find((e) => e.type === "robot.state.updated");
  assert.ok(idle && idle.type === "robot.state.updated");
  assert.equal(idle.robot_status, "IDLE");
});
