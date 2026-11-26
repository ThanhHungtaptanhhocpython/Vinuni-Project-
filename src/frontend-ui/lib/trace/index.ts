/* Từ vựng hiển thị cho tool call — dùng chung giữa agent trace, tool card và
   timeline P09.
 *
 * Trước đây các bảng này nằm riêng tư trong `components/chat/ToolCard.tsx`, nên
 * `components/logs/TimelineView.tsx` đành in `tool_name` thô (`take_action`)
 * cho người dùng đọc. Đặt ở `lib/` để chỗ nào cũng lấy được mà không phải kéo
 * theo một component chat.
 *
 * Mọi hàm ở đây THUẦN và chỉ đọc những trường backend thật sự trả về — §13.3
 * cấm client tự suy diễn. Không hàm nào ở đây được chạm tới nội dung token hay
 * suy luận của model. */

import {
  Camera, Cpu, Zap, OctagonX, Home, ShieldCheck, Activity, Gauge,
  RotateCcw, CircleCheck,
  type LucideIcon,
} from "lucide-react";
import type { ToolCall } from "@/stores";
import { errorText } from "@/lib/errors";

/* `capture_tool`/`get_task_status`/`stop_robot`/`reset_error`/`health_check`
   là tên tool theo đặc tả MCP cũ — `src/edge_vla/server.py` hiện tại KHÔNG
   dùng những tên đó, nó dùng `capture`/`check_status`/`abort`, và không có
   `stop_robot`/`reset_error`/`health_check` (xem `@mcp.tool()` trong
   server.py). Giữ nguyên các entry cũ (không xoá — lịch sử phiên cũ hoặc một
   edge server khác có thể vẫn dùng tên đó), chỉ THÊM entry cho tên thật. */
export const TOOL_ICON: Record<string, LucideIcon> = {
  capture_tool: Camera,
  capture: Camera,
  get_robot_state: Cpu,
  get_servo_state: Gauge,
  take_action: Zap,
  check_status: Zap,
  stop_robot: OctagonX,
  abort: OctagonX,
  go_home: Home,
  reset_error: ShieldCheck,
  reset_episode: RotateCcw,
  check_success: CircleCheck,
  health_check: Activity,
};

export const TOOL_LABEL: Record<string, string> = {
  capture_tool: "Chụp ảnh quan sát",
  capture: "Chụp ảnh quan sát",
  get_robot_state: "Đọc trạng thái robot",
  get_servo_state: "Đọc trạng thái khớp",
  take_action: "Thực thi hành động",
  check_status: "Kiểm tra tiến độ",
  stop_robot: "Dừng robot",
  abort: "Yêu cầu dừng khẩn cấp",
  go_home: "Về vị trí home",
  reset_error: "Xoá cờ lỗi",
  reset_episode: "Đặt lại vòng thử",
  check_success: "Kiểm tra kết quả nhiệm vụ",
  health_check: "Kiểm tra thiết bị",
};

/** Biểu tượng dùng khi tool không có trong bảng.

    Export dưới dạng HẰNG chứ không phải hàm `toolIcon(name)`: gọi một hàm trả
    về component ngay trong render bị lint coi là "tạo component lúc render".
    Chỗ dùng tra bảng trực tiếp — `TOOL_ICON[name] ?? DEFAULT_TOOL_ICON`. */
export const DEFAULT_TOOL_ICON: LucideIcon = Cpu;

/** Tên tiếng Việt của tool. Tool lạ thì trả về nguyên tên kỹ thuật — thà hiện
    `some_new_tool` còn hơn nuốt mất một hành động đã thực sự xảy ra. */
export function toolLabel(toolName: string): string {
  return TOOL_LABEL[toolName] ?? toolName;
}

/** `get_task_status`/`check_status` là vòng lặp thăm dò nội bộ của Cloud (nay
    đã nối dây thật — `agents/nodes/tool.py`'s `_monitor_episode`, gọi tên
    thật `check_status`): nó đã hiện thành thanh tiến độ episode
    (`episode.started`/`.progress`/`.completed`), nên không được thành một
    dòng riêng (P04 §4).

    Phải lọc từ lúc DỰNG danh sách chứ không phải để component con trả `null` —
    nếu không, số đếm "Đã xong N bước" và phép tính thời lượng vẫn tính nó. */
export function isTraceHiddenTool(toolName: string): boolean {
  return toolName === "get_task_status" || toolName === "check_status";
}

/** Tóm tắt tham số cho dòng thu gọn — không có chain-of-thought.
    `subgoal` là tên tham số thật của `take_action` trên `edge_vla.server`
    (`instruction` là tên cũ, giữ lại phòng khi có edge server khác dùng). */
export function toolArgSummary(call: ToolCall): string {
  const args = call.arguments ?? {};
  if (typeof args.instruction === "string") return args.instruction;
  if (typeof args.subgoal === "string") return args.subgoal;
  if (Array.isArray(args.cameras)) return args.cameras.join(" + ");
  const keys = Object.keys(args);
  return keys.length === 0 ? "" : keys.map((k) => `${k}: ${String(args[k])}`).join(" · ");
}

/* Tên camera KHÔNG kèm chữ "camera": nó được thêm đúng một lần ở câu bao ngoài.
   Để "camera trên"/"camera cổ tay" ở đây thì hai camera ghép lại thành "Chụp
   ảnh camera trên và camera cổ tay" — lặp thừa, đọc lúng túng. */
const CAMERA_NAME: Record<string, string> = {
  top: "trên",
  wrist: "cổ tay",
};

/** Một câu mô tả HÀNH ĐỘNG cụ thể, kèm tham số thật.
 *
 * Đây là thứ tạo khác biệt giữa "Đang quan sát" — giống hệt nhau ở mọi lượt
 * chat — và "Chụp ảnh camera trên và cổ tay", nói đúng việc agent vừa làm với
 * yêu cầu NÀY.
 *
 * Mọi nhánh chỉ đọc `call.arguments`. Không suy ra ý định, không đoán kết quả,
 * không tóm tắt lại suy luận của model (§13.2, §13.3).
 *
 * KHÔNG cắt chuỗi ở đây: composer cho tới 8000 ký tự nên instruction có thể rất
 * dài, nhưng cắt bằng JS thì `title` cũng mất theo. Chỗ gọi dùng CSS `truncate`
 * và đặt `title` bằng chính chuỗi đầy đủ này. */
export function describeToolCall(call: ToolCall): string {
  const args = call.arguments ?? {};

  if (call.tool_name === "capture_tool") {
    const cameras = Array.isArray(args.cameras)
      ? args.cameras.filter((c): c is string => typeof c === "string")
      : [];
    if (cameras.length === 0) return TOOL_LABEL.capture_tool;
    // Giá trị lạ giữ nguyên: chào một camera tên khác còn hơn im lặng bỏ qua.
    return `Chụp ảnh camera ${cameras.map((c) => CAMERA_NAME[c] ?? c).join(" và ")}`;
  }

  // `edge_vla.server.capture()` không nhận tham số — luôn chụp cả 2 camera
  // (agentview/top + wrist), nên không có gì để đọc từ `args` ở đây.
  if (call.tool_name === "capture") {
    return "Chụp ảnh camera trên và cổ tay";
  }

  if (call.tool_name === "take_action" && typeof args.instruction === "string") {
    const instruction = args.instruction.trim();
    if (instruction) return `Thực thi "${instruction}"`;
  }

  // `subgoal` là tên tham số thật của `take_action` (xem toolArgSummary).
  if (call.tool_name === "take_action" && typeof args.subgoal === "string") {
    const subgoal = args.subgoal.trim();
    if (subgoal) return `Thực thi "${subgoal}"`;
  }

  const label = toolLabel(call.tool_name);
  /* `trim()` bắt buộc: tham số toàn khoảng trắng vẫn là chuỗi "truthy", không
     cắt thì nhãn ra "Thực thi hành động ·   " với dấu chấm giữa lơ lửng. */
  const summary = toolArgSummary(call).trim();
  return summary ? `${label} · ${summary}` : label;
}

/** Tóm tắt KẾT QUẢ cho dòng `⎿` của `TraceFlow` (C2) — một dòng, không phải
 * `describeToolCall` ở trên (đó là HÀNH ĐỘNG, cái này là KẾT QUẢ của hành
 * động đó).
 *
 * Cùng kỷ luật với mọi hàm khác trong file: chỉ đọc field backend thật sự
 * trả về. Cánh tay mô phỏng KHÔNG có cảm biến nhiệt (xem `stores/index.ts`'s
 * `JointState` docstring) nên không có dòng "nhiệt độ tối đa" — bịa ra một
 * trường không tồn tại còn tệ hơn một dòng ngắn hơn.
 *
 * Không có gì để nói → chuỗi rỗng, dòng `⎿` chỉ còn hiện thời lượng (chỗ gọi
 * tự lo phần đó), vẫn bấm mở được như mọi dòng khác. */
export function summarizeToolResult(call: ToolCall): string {
  if (call.status === "TIMEOUT") return "quá thời gian chờ";
  if (call.status === "CANCELLED") return "đã huỷ";
  if (call.status === "FAILED") return call.error ? errorText(call.error) : "thất bại";

  const result = call.result;

  if (
    (call.tool_name === "capture" || call.tool_name === "capture_tool") &&
    Array.isArray(result?.images)
  ) {
    return `${result.images.length} ảnh`;
  }

  if (
    (call.tool_name === "get_robot_state" || call.tool_name === "get_servo_state") &&
    Array.isArray(result?.joints)
  ) {
    return `${result.joints.length} khớp`;
  }

  if (call.tool_name === "take_action" || call.tool_name === "go_home") {
    const episode = (call.episodes ?? [])[0];
    if (episode) {
      if (!episode.outcome) {
        const percent = episode.max_steps > 0 ? Math.round((episode.steps / episode.max_steps) * 100) : 0;
        return `bước ${episode.steps}/${episode.max_steps} (${percent}%)`;
      }
      return `${episode.outcome} · ${episode.steps} bước`;
    }
    if (call.tool_name === "go_home" && call.status === "SUCCEEDED") return "đã về vị trí home";
  }

  // Còn lại: các key phẳng của result (chuỗi/số/bool), cắt còn 1 dòng.
  if (result && Object.keys(result).length > 0) {
    const parts = Object.entries(result)
      .filter(
        ([key, value]) =>
          key !== "images" &&
          key !== "joints" &&
          (typeof value === "string" || typeof value === "number" || typeof value === "boolean"),
      )
      .map(([key, value]) => `${key}: ${String(value)}`);
    if (parts.length > 0) return parts.join(" · ");
  }

  return "";
}
