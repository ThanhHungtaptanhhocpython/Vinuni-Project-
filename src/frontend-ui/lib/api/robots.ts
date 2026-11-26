import type { Robot } from "@/stores";
import { apiClient } from "./client";
import { USE_MOCK } from "./config";
import {
  clearRobotManualDisconnect,
  emitRobotConnectionChanged,
  markRobotManuallyDisconnected,
} from "@/lib/robot-connection-state";
import {
  addMockRobot,
  connectMockRobot,
  deleteMockRobot,
  disconnectMockRobot,
  fetchMockRobotEnvironment,
  fetchMockEdgeRegistry,
  fetchMockRobots,
  fetchMockRobotStats,
  resetMockRobotEnvironment,
  stopMockRobot,
} from "@/lib/mock/robots";

export async function fetchRobots(): Promise<Robot[]> {
  if (USE_MOCK) return fetchMockRobots();
  /* KHÔNG nuốt lỗi rồi trả `mockRobots`.
     Bản trước có `catch { return mockRobots }`, nghĩa là backend chết thì giao
     diện vẫn vẽ ra một cánh tay IDLE/ONLINE khoẻ mạnh. Với một ứng dụng điều
     khiển robot, hiện trạng thái giả còn nguy hiểm hơn hiện lỗi: người dùng có
     thể bấm lệnh dựa trên một tình trạng không có thật. Ném lỗi lên để chỗ gọi
     hiện đúng trạng thái mất kết nối. */
  return apiClient.get<Robot[]>("/api/v1/robots");
}

/** Dữ liệu người dùng nhập ở hộp thoại "Thêm robot". Chỉ ID + tên — loại tay
    KHÔNG do người dùng chọn nữa, backend tự điền từ những gì Edge đã khai khi
    đăng ký (`fetchEdgeRegistry`). Mọi thứ còn lại của `Robot` (khớp,
    capability, phiên bản edge) cũng do thiết bị Edge tự khai khi nó kết nối. */
export interface AddRobotInput {
  /** ID in trên thiết bị Edge — khoá định danh cánh tay. */
  id: string;
  name: string;
}

/** `POST /robots` — pairing is disabled on the API for now (`ROBOT_004`). */
export async function addRobot(input: AddRobotInput): Promise<Robot> {
  if (USE_MOCK) return addMockRobot(input);
  void input;
  throw {
    code: "ROBOT_004",
    message: "Ghép nối robot qua API đã tạm tắt.",
    status: 403,
  };
}

/** Thông tin ground-truth Edge đã tự khai khi đăng ký với Cloud (JWT ký bằng
    `EDGE_JWT_SECRET`) — KHÔNG phải bản ghi robot đã ghép nối vào tài khoản. */
export interface EdgeRegistryEntry {
  robot_id: string;
  /** Khớp `RobotModelId` (`@/lib/robot/models`) khi Edge dùng đúng danh mục;
      backend có thể trả chuỗi khác nếu Edge khai loại chưa có trong danh mục. */
  robot_type: string;
  capabilities: { name: string; version: string }[];
  registered_at: string;
  last_seen_at: string;
}

/** `GET /edge/registry/{id}` — tra cứu xem thiết bị Edge với ID này đã tự
 * đăng ký với Cloud chưa. Gọi khi người dùng gõ ID trong hộp thoại "Thêm
 * robot" để tự điền loại tay — không còn cho người dùng tự chọn (MASTER: loại
 * tay phải khớp phần cứng thật, đoán sai là ghép nối sai giao thức điều khiển).
 *
 * `null` khi chưa có Edge nào đăng ký ID này (404) — chỗ gọi hiện "chưa tìm
 * thấy thiết bị". Lỗi khác (mạng, 500) ném lên như các hàm còn lại trong file
 * này. */
export async function fetchEdgeRegistry(robotId: string): Promise<EdgeRegistryEntry | null> {
  if (USE_MOCK) return fetchMockEdgeRegistry(robotId);
  try {
    return await apiClient.get<EdgeRegistryEntry>(
      `/api/v1/edge/registry/${encodeURIComponent(robotId)}`,
    );
  } catch (err) {
    if (err && typeof err === "object" && "status" in err && (err as { status: number }).status === 404) {
      return null;
    }
    throw err;
  }
}

/** `DELETE /robots/{id}` — gỡ ghép nối một cánh tay khỏi tài khoản.
 *
 * Cửa một chiều: không có API nào nối lại, người dùng phải ghép nối lại từ
 * đầu nếu gỡ nhầm. Vì vậy chỗ gọi LUÔN phải xác nhận với người dùng trước —
 * xem `ConfirmDialog` ở trang `/robots/[robotId]`. */
export async function deleteRobot(robotId: string): Promise<void> {
  if (USE_MOCK) {
    await deleteMockRobot(robotId);
    return;
  }
  await apiClient.delete(`/api/v1/robots/${encodeURIComponent(robotId)}`);
}

export async function connectRobot(robotId: string): Promise<Robot> {
  const robot = USE_MOCK
    ? await connectMockRobot(robotId)
    : await apiClient.post<Robot>(`/api/v1/robots/${encodeURIComponent(robotId)}/connect`);
  clearRobotManualDisconnect(robot.id);
  emitRobotConnectionChanged(robot);
  return robot;
}

export async function disconnectRobot(robotId: string): Promise<Robot> {
  const robot = USE_MOCK
    ? await disconnectMockRobot(robotId)
    : await apiClient.post<Robot>(`/api/v1/robots/${encodeURIComponent(robotId)}/disconnect`);
  markRobotManuallyDisconnected(robot.id);
  emitRobotConnectionChanged(robot);
  return robot;
}

/** Kết quả thô của `health_check` — bọc y nguyên `{ok, tool_name, data,
    duration_ms}` mà Edge trả (`robots_routes.py:148`, gọi thẳng tool, không có
    schema riêng). `data` là `Record<string, unknown>` cố ý: hôm nay chỉ có
    `status`/`edge_version` (mock) hoặc `status`/`endpoint_url` (real) — không
    field nào của camera. Giữ kiểu mở để nếu sau này Edge thêm field camera
    thật, chỗ đọc (`CameraHealthCard`) tự nhận ra mà không cần đổi type này. */
export interface RobotHealthResult {
  ok: boolean;
  tool_name?: string;
  data?: Record<string, unknown> | null;
  duration_ms?: number | null;
}

/** `GET /robots/{id}/health` — sức khoẻ thiết bị THẬT do Edge trả, không có
    bản mock riêng ở tầng API này: khi cần hiển thị "camera giả lập", chỗ gọi
    (trang robot) dùng thẳng `fetchMockCameraHealth` (client thuần, không qua
    HTTP) thay vì gọi hàm này — gọi hàm này trong mock mode sẽ vẫn chạm backend
    thật và backend thật (dù đang chạy MockEdgeTransport) cũng không trả field
    camera nào, nên không có lý do đi qua đây trong trường hợp đó. */
export async function fetchRobotHealth(robotId: string): Promise<RobotHealthResult> {
  return apiClient.get<RobotHealthResult>(`/api/v1/robots/${encodeURIComponent(robotId)}/health`);
}

export async function fetchRobot(robotId: string): Promise<Robot | null> {
  if (USE_MOCK) {
    const rows = await fetchMockRobots();
    return rows.find((r) => r.id === robotId) ?? null;
  }
  try {
    return await apiClient.get<Robot>(`/api/v1/robots/${encodeURIComponent(robotId)}`);
  } catch (err) {
    if (err && typeof err === "object" && "status" in err && (err as { status: number }).status === 404) {
      return null;
    }
    throw err;
  }
}

/* ---------------------------------------------------- lệnh gửi tới cánh tay
 *
 * Bốn hàm dưới đây gọi thẳng tool của Edge qua Cloud. KHÔNG hàm nào nuốt lỗi:
 * đây là lệnh chạm vào cánh tay thật, và báo thành công khi thực ra không gửi
 * được là kiểu nói dối tệ nhất ở đây. Chỗ gọi bắt lỗi rồi hiện lý do. */

/** Dừng robot — nút Dừng và phím Esc Esc (MASTER §12.1). */
export async function stopRobot(robotId: string, mode: "GRACEFUL" | "EMERGENCY" = "GRACEFUL"): Promise<void> {
  if (USE_MOCK) {
    await stopMockRobot(robotId);
    return;
  }
  await apiClient.post(`/api/v1/robots/${encodeURIComponent(robotId)}/stop?mode=${mode}`);
}

/** Đưa cánh tay về vị trí home an toàn. */
export async function goHomeRobot(robotId: string): Promise<void> {
  if (USE_MOCK) {
    const { goHomeMockRobot } = await import("@/lib/mock/robots");
    await goHomeMockRobot(robotId);
    return;
  }
  await apiClient.post(`/api/v1/robots/${encodeURIComponent(robotId)}/go-home`);
}

/** Xoá cờ lỗi sau khi đã xử lý nguyên nhân. */
export async function resetRobotError(robotId: string): Promise<void> {
  if (USE_MOCK) {
    const { resetMockRobotError } = await import("@/lib/mock/robots");
    await resetMockRobotError(robotId);
    return;
  }
  await apiClient.post(`/api/v1/robots/${encodeURIComponent(robotId)}/reset-error`);
}

/** Reset môi trường sim (scene + task queue). Real mode chỉ xoá hàng đợi policy. */
export async function resetRobotEpisode(robotId: string): Promise<void> {
  if (USE_MOCK) {
    const { resetMockEpisode } = await import("@/lib/mock/robots");
    await resetMockEpisode(robotId);
    return;
  }
  await apiClient.post(`/api/v1/robots/${encodeURIComponent(robotId)}/reset-episode`);
}

export interface RobotEnvironment {
  robot_id: string;
  backend_mode: string;
  virtual: boolean;
  reset_available: boolean;
}

export async function fetchRobotEnvironment(robotId: string): Promise<RobotEnvironment> {
  if (USE_MOCK) return fetchMockRobotEnvironment(robotId);
  return apiClient.get<RobotEnvironment>(
    `/api/v1/robots/${encodeURIComponent(robotId)}/environment`,
  );
}

export async function resetRobotEnvironment(robotId: string): Promise<void> {
  if (USE_MOCK) {
    await resetMockRobotEnvironment(robotId);
    return;
  }
  await apiClient.post(`/api/v1/robots/${encodeURIComponent(robotId)}/environment/reset`);
}

/* ------------------------------------------------------- thống kê vận hành */

export interface RunOutcomeCount {
  /** Trạng thái lượt chạy THẬT trong database: COMPLETED / FAILED / RUNNING… */
  status: string;
  count: number;
}

export interface DailyActivity {
  date: string;
  count: number;
}

export interface RobotStats {
  window_days: number;
  total_runs: number;
  /** Số lượt đã kết thúc — mẫu số của `success_rate`. */
  finished_runs: number;
  /** `null` khi chưa lượt nào kết thúc: chưa có gì để tính, không phải 0%. */
  success_rate: number | null;
  avg_duration_s: number | null;
  outcomes: RunOutcomeCount[];
  daily_activity: DailyActivity[];
}

/** Thống kê lượt chạy của một robot — nguồn cho trang Tổng quan.
 *
 * KHÔNG có bản mock: đây là số liệu vận hành, và một tỉ lệ thành công bịa ra
 * còn nguy hiểm hơn không có — người ta nhìn nó để quyết định robot có ổn
 * không. Chế độ mock thì trang tự ẩn khối này đi.
 *
 * `null` CHỈ khi backend xác nhận 404 (chưa có run nào) — mọi lỗi khác (auth,
 * mạng, 500) phải ném lên. Trước đây `catch { return null }` bắt luôn tất cả,
 * nên một backend đang lỗi trông giống hệt "robot mới, chưa chạy lần nào" —
 * cùng kiểu trạng thái giả mà file này đã từ chối làm ở `fetchRobots`. */
export async function fetchRobotStats(robotId: string, days = 14): Promise<RobotStats | null> {
  if (USE_MOCK) return fetchMockRobotStats(robotId);
  try {
    return await apiClient.get<RobotStats>(
      `/api/v1/robots/${encodeURIComponent(robotId)}/stats?days=${days}`,
    );
  } catch (err) {
    if (err && typeof err === "object" && "status" in err && (err as { status: number }).status === 404) {
      return null;
    }
    throw err;
  }
}
