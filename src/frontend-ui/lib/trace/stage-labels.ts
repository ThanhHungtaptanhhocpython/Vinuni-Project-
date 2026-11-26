/* Bảng ánh xạ stage → nhãn tiếng Việt — nguồn DUY NHẤT, dùng chung giữa
   `trace-translate.ts` (luồng SSE đang chạy) và `run-timeline.ts` (lịch sử đã
   lưu, P09). Trước đây hai file tự định nghĩa hai bản giống hệt nhau với ghi
   chú "cùng bảng ánh xạ với ..." — sửa một chỗ mà quên chỗ kia thì cùng một
   lượt chạy hiện hai nhãn khác nhau tuỳ xem trực tiếp hay mở lại lịch sử. */

export const STAGE_LABEL_VI: Record<string, string> = {
  initializing: "Đang hiểu yêu cầu",
  model_call: "Đang lập kế hoạch",
  tool_execution: "Đang thực thi",
  robot_episode: "Đang thực thi",
  emergency_stop: "Đang dừng khẩn cấp",
  finished: "Đang kiểm tra kết quả",
};

/** `TraceEvent.type` gốc → stage. Backend thật không tự gắn `stage` vào sự
    kiện (đó là khái niệm riêng của UI) — cả luồng SSE đang chạy lẫn lịch sử đã
    lưu đều xuất phát từ CÙNG `type` gốc nên phải suy ra stage bằng đúng một
    bảng, không phải hai bảng trùng lặp. */
export const EVENT_STAGE: Record<string, string> = {
  "run.started": "initializing",
  "run.preflight_completed": "initializing",
  "llm.started": "model_call",
  "llm.completed": "model_call",
  "llm.failed": "model_call",
  "tool.started": "tool_execution",
  "tool.completed": "tool_execution",
  "tool.failed": "tool_execution",
  "episode.started": "robot_episode",
  "episode.progress": "robot_episode",
  "episode.completed": "robot_episode",
  "emergency.stop.requested": "emergency_stop",
  "emergency.stop.safety_started": "emergency_stop",
  "emergency.stop.safety_completed": "emergency_stop",
  "emergency.stop.cancel_forwarded": "emergency_stop",
  "run.completed": "finished",
  "run.failed": "finished",
  "run.cancelled": "finished",
  "run.timeout": "finished",
};
