/* Mock robots — 6 variants covering all status combinations (MASTER §19) */
import { useRealtimeStore, type Robot, type JointState } from "@/stores";
import { mockDelay } from ".";

/* Dải giới hạn từng khớp của cánh tay 6 bậc tự do — tương ứng thẻ `range`
   trong file MJCF của MuJoCo. Vai và khuỷu bị chặn hẹp hơn cổ tay vì chúng
   mang tải; cổ tay xoay gần trọn vòng. */
const JOINT_LIMITS: Record<string, [min: number, max: number]> = {
  shoulder_pan: [-165, 165],
  shoulder_lift: [-110, 110],
  elbow: [-155, 155],
  wrist_1: [-180, 180],
  wrist_2: [-120, 120],
  wrist_3: [-180, 180],
};

/* `forcerange` của actuator từng khớp, N·m — MJCF khai trong <actuator>.
   Khớp gần gốc phải gánh cả cánh tay nên khoẻ hơn hẳn khớp cổ tay. */
const TORQUE_LIMITS: Record<string, number> = {
  shoulder_pan: 12,
  shoulder_lift: 12,
  elbow: 8,
  wrist_1: 4,
  wrist_2: 4,
  wrist_3: 3,
};

function makeJoints(healthy: boolean): JointState[] {
  const names = ["shoulder_pan", "shoulder_lift", "elbow", "wrist_1", "wrist_2", "wrist_3"];
  return names.map((name, i) => {
    const [range_min, range_max] = JOINT_LIMITS[name];
    const present_position = -30 + i * 25 + Math.random() * 10;
    // Sai số bám: bộ điều khiển luôn lệch một chút so với lệnh đặt. Đây chính
    // là con số nói lên khớp có theo kịp quỹ đạo hay không.
    const trackingError = (Math.random() - 0.5) * (healthy ? 0.8 : 4.5);
    return {
      name,
      present_position,
      target_position: present_position + trackingError,
      present_velocity: 0,
      // Khớp gần gốc gánh cả cánh tay nên mô-men lớn hơn hẳn khớp cổ tay.
      torque_nm: (6.5 - i * 0.9) * (healthy ? 0.4 + Math.random() * 0.3 : 0.9 + Math.random() * 0.4),
      torque_limit_nm: TORQUE_LIMITS[name],
      range_min,
      range_max,
      present_load: Math.random() * 40,
      torque_enabled: healthy,
      moving: false,
      error_flags: healthy ? [] : i === 2 ? ["OVERLOAD"] : [],
    };
  });
}

/** Pose đầu công tác — MuJoCo lấy từ `xpos`/`xquat` của site cuối chuỗi. */
function makeEndEffector() {
  return {
    position: { x: 0.312, y: -0.048, z: 0.276 },
    orientation: { roll: 178.4, pitch: -2.1, yaw: 46.7 },
    gripper: 0.35,
  };
}

const robotFixtures: Robot[] = [
  {
    id: "robot-001",
    name: "Cánh tay Alpha",
    model: "VLA-6X",
    robot_status: "IDLE",
    connection_status: "ONLINE",
    edge_version: "1.4.2",
    health: { has_error: false, error_flags: [] },
    capabilities: [
      { name: "capture_tool", version: "1.0" },
      { name: "get_robot_state", version: "1.0" },
      { name: "take_action", version: "2.1" },
      { name: "stop_robot", version: "1.0" },
      { name: "go_home", version: "1.0" },
      { name: "reset_error", version: "1.0" },
      { name: "reset_episode", version: "1.0" },
      { name: "health_check", version: "1.0" },
      { name: "get_task_status", version: "1.0" },
    ],
    joints: makeJoints(true),
    end_effector: makeEndEffector(),
    sim_time: 128.45,
  },
  {
    id: "robot-002",
    name: "Cánh tay Beta",
    model: "VLA-6X",
    robot_status: "BUSY",
    connection_status: "ONLINE",
    edge_version: "1.4.1",
    health: { has_error: false, error_flags: [] },
    capabilities: [
      { name: "capture_tool", version: "1.0" },
      { name: "get_robot_state", version: "1.0" },
      { name: "take_action", version: "2.1" },
      { name: "stop_robot", version: "1.0" },
      { name: "go_home", version: "1.0" },
      { name: "reset_error", version: "1.0" },
      { name: "health_check", version: "1.0" },
      { name: "get_task_status", version: "1.0" },
    ],
    joints: makeJoints(true),
  },
  {
    id: "robot-003",
    name: "Cánh tay Gamma",
    model: "VLA-6S",
    robot_status: "ERROR",
    connection_status: "ONLINE",
    edge_version: "1.3.8",
    health: { has_error: true, error_flags: ["OVERLOAD", "JOINT_LIMIT"] },
    capabilities: [
      { name: "capture_tool", version: "1.0" },
      { name: "get_robot_state", version: "1.0" },
      { name: "take_action", version: "2.0" },
      { name: "stop_robot", version: "1.0" },
      { name: "go_home", version: "1.0" },
      { name: "reset_error", version: "1.0" },
      { name: "health_check", version: "1.0" },
      { name: "get_task_status", version: "1.0" },
    ],
    joints: makeJoints(false),
  },
  {
    id: "robot-004",
    name: "Cánh tay Delta",
    model: "VLA-6X",
    robot_status: "STOPPING",
    connection_status: "ONLINE",
    edge_version: "1.4.2",
    health: { has_error: false, error_flags: [] },
    capabilities: [
      { name: "capture_tool", version: "1.0" },
      { name: "get_robot_state", version: "1.0" },
      { name: "take_action", version: "2.1" },
      { name: "stop_robot", version: "1.0" },
      { name: "go_home", version: "1.0" },
      { name: "reset_error", version: "1.0" },
      { name: "health_check", version: "1.0" },
      { name: "get_task_status", version: "1.0" },
    ],
    joints: makeJoints(true),
  },
  {
    id: "robot-005",
    name: "Cánh tay Epsilon",
    model: "VLA-6S",
    robot_status: "DISCONNECTED",
    connection_status: "ONLINE",
    edge_version: "1.2.0",
    health: { has_error: false, error_flags: [] },
    capabilities: [
      { name: "capture_tool", version: "1.0" },
      { name: "get_robot_state", version: "1.0" },
      { name: "take_action", version: "1.9" },
      { name: "stop_robot", version: "1.0" },
      { name: "go_home", version: "1.0" },
      { name: "reset_error", version: "1.0" },
      { name: "health_check", version: "1.0" },
      { name: "get_task_status", version: "1.0" },
    ],
    joints: makeJoints(true),
  },
  {
    id: "robot-006",
    name: "Cánh tay Zeta",
    model: "VLA-6X",
    robot_status: "IDLE",
    connection_status: "OFFLINE",
    edge_version: "1.4.0",
    health: { has_error: false, error_flags: [] },
    capabilities: [
      { name: "capture_tool", version: "1.0" },
      { name: "get_robot_state", version: "1.0" },
      { name: "take_action", version: "2.1" },
      { name: "stop_robot", version: "1.0" },
      { name: "go_home", version: "1.0" },
      { name: "reset_error", version: "1.0" },
      { name: "health_check", version: "1.0" },
      { name: "get_task_status", version: "1.0" },
    ],
    joints: makeJoints(true),
  },
];

/* Sản phẩm vận hành ĐÚNG MỘT robot (quyết định của nhóm), nên đội robot mà
   giao diện thấy chỉ có một phần tử. Năm bản ghi còn lại giữ nguyên làm bộ thử
   trạng thái: đổi hằng số dưới đây sang `robot-003` là cả giao diện chuyển sang
   nhánh ERROR, sang `robot-006` là nhánh OFFLINE — không phải sửa component nào.

   Vẫn để kiểu mảng thay vì một object: hợp đồng `GET /robots` của Cloud trả về
   danh sách, và mọi trạng thái rỗng/đang tải ở các trang đều dựng trên mảng. */
export const PRIMARY_ROBOT_ID = "robot-001";

export const mockRobots: Robot[] = robotFixtures.filter((r) => r.id === PRIMARY_ROBOT_ID);

/** Bộ 6 trạng thái đầy đủ — chỉ dùng để thử giao diện, không phải đội robot thật. */
export const robotStateFixtures = robotFixtures;

export function fetchMockRobots(): Promise<Robot[]> {
  return mockDelay(mockRobots, 0.03);
}

export function fetchMockRobot(id: string): Promise<Robot | undefined> {
  return mockDelay(robotFixtures.find((r) => r.id === id), 0.03);
}

/* ---------------------------------------------------------------- commands */

function findOrReject(id: string): Robot | null {
  return robotFixtures.find((r) => r.id === id) ?? null;
}

/** POST /robots/{id}/stop — the one command that must always be accepted.

    In mock mode this also pushes the state transitions the Cloud would send
    over SSE, so the badge and composer react exactly as they will in
    production: STOPPING immediately, IDLE once the Edge confirms. DOC 2 §9.1
    budgets ≤500ms from click to the IDLE badge. */
export function stopMockRobot(id: string): Promise<Robot> {
  const robot = findOrReject(id);
  if (!robot) return Promise.reject({ code: "ROBOT_011", message: "Không tìm thấy robot." });

  robot.robot_status = "STOPPING";
  const realtime = useRealtimeStore.getState();
  // Stopping the machine stops the run that is driving it.
  realtime.closeStream?.();
  const stoppedJoints = robot.joints.map((j) => ({ ...j, moving: false }));
  realtime.updateRobotState({
    robot_status: "STOPPING",
    connection_status: robot.connection_status,
    joints: stoppedJoints,
  });

  setTimeout(() => {
    if (robot.robot_status === "STOPPING") robot.robot_status = "IDLE";
    const store = useRealtimeStore.getState();
    store.updateRobotState({
      robot_status: "IDLE",
      connection_status: robot.connection_status,
      joints: stoppedJoints,
    });
    // Mirrors `episode.stopped` + `run.cancelled`: the banner shows briefly and
    // the composer comes back.
    if (store.runId !== null) {
      store.setEpisodeProgress(null);
      store.unlockComposer();
      store.setAgentStatus(null);
      store.setRunId(null);
      store.setBanner({ tone: "info", message: "Đã dừng theo yêu cầu." });
      setTimeout(() => useRealtimeStore.getState().setBanner(null), 3000);
    }
  }, 280);

  return mockDelay(robot, 0.02);
}

/** POST /robots/{id}/go-home */
export function goHomeMockRobot(id: string): Promise<Robot> {
  const robot = findOrReject(id);
  if (!robot) return Promise.reject({ code: "ROBOT_011", message: "Không tìm thấy robot." });
  if (robot.robot_status !== "IDLE") {
    return Promise.reject({ code: "ROBOT_010", message: "Robot đang bận." });
  }
  robot.robot_status = "BUSY";
  setTimeout(() => {
    robot.robot_status = "IDLE";
  }, 2500);
  return mockDelay(robot, 0.05);
}

/** POST /robots/{id}/reset-error — returns the flags that were cleared. */
export function resetMockRobotError(id: string): Promise<{ robot: Robot; cleared_flags: string[] }> {
  const robot = findOrReject(id);
  if (!robot) return Promise.reject({ code: "ROBOT_011", message: "Không tìm thấy robot." });
  const cleared = [...robot.health.error_flags];
  robot.health = { ...robot.health, has_error: false, error_flags: [] };
  robot.joints = robot.joints.map((j) => ({ ...j, error_flags: [] }));
  robot.robot_status = "IDLE";
  return mockDelay({ robot, cleared_flags: cleared }, 0.05);
}

/** POST /robots/{id}/reset-episode — abort running work and reset the scene. */
export function resetMockEpisode(id: string): Promise<Robot> {
  const robot = findOrReject(id);
  if (!robot) return Promise.reject({ code: "ROBOT_011", message: "Không tìm thấy robot." });
  robot.health = { ...robot.health, has_error: false, error_flags: [] };
  robot.joints = robot.joints.map((j) => ({ ...j, error_flags: [] }));
  robot.robot_status = "IDLE";
  return mockDelay(robot, 0.08);
}

export function fetchMockRobotEnvironment(
  id: string,
): Promise<import("@/lib/api/robots").RobotEnvironment> {
  const robot = findOrReject(id);
  if (!robot) return Promise.reject({ code: "ROBOT_011", message: "Không tìm thấy robot." });
  const virtual = typeof robot.sim_time === "number" || robot.model === "franka";
  return mockDelay(
    {
      robot_id: id,
      backend_mode: virtual ? "sim" : "real",
      virtual,
      reset_available: virtual,
    },
    0.03,
  );
}

export function resetMockRobotEnvironment(id: string): Promise<{ robot_id: string; backend_mode: string }> {
  const robot = findOrReject(id);
  if (!robot) return Promise.reject({ code: "ROBOT_011", message: "Không tìm thấy robot." });
  robot.robot_status = "IDLE";
  return mockDelay({ robot_id: id, backend_mode: "sim" }, 0.08);
}

export function connectMockRobot(id: string): Promise<Robot> {
  const robot = findOrReject(id);
  if (!robot) return Promise.reject({ code: "ROBOT_011", message: "Không tìm thấy robot." });
  robot.connection_status = "ONLINE";
  return mockDelay(robot, 0.04);
}

export function disconnectMockRobot(id: string): Promise<Robot> {
  const robot = findOrReject(id);
  if (!robot) return Promise.reject({ code: "ROBOT_011", message: "Không tìm thấy robot." });
  robot.connection_status = "OFFLINE";
  return mockDelay(robot, 0.04);
}


/* Camera health and episode statistics shown on P10. In mock mode these are the
   values the backend would return; when USE_MOCK=0 the page hides the cards
   rather than inventing numbers (MASTER §13.3). */
export interface CameraHealth {
  camera: "top" | "wrist";
  fps: number;
  last_frame_age_s: number;
}

export interface EpisodeStats {
  total_episodes: number;
  success_rate: number;
  outcomes: { outcome: "SUCCESS" | "STOPPED" | "TIMEOUT" | "MAX_STEPS" | "SAFETY_ABORT"; count: number }[];
}

export function fetchMockCameraHealth(robotId: string): Promise<CameraHealth[]> {
  return mockDelay(
    robotId
      ? [
          { camera: "top" as const, fps: 15, last_frame_age_s: 1 },
          { camera: "wrist" as const, fps: 14, last_frame_age_s: 2 },
        ]
      : [],
    0.03,
  );
}

/** Số nhiệm vụ mỗi ngày, 14 ngày gần nhất — nguyên liệu cho biểu đồ hoạt động.
    Cuối tuần thấp hẳn, đúng nhịp một phòng lab. */
export interface DailyActivity {
  date: string;
  count: number;
}

export function fetchMockDailyActivity(robotId: string): Promise<DailyActivity[]> {
  if (!robotId) return mockDelay([], 0);
  const days: DailyActivity[] = [];
  for (let i = 13; i >= 0; i--) {
    const date = new Date(Date.now() - i * 86_400_000);
    const weekend = date.getDay() === 0 || date.getDay() === 6;
    // Nhịp cố định theo thứ, không random — để hai lần mở trang không ra hai
    // biểu đồ khác nhau.
    const base = weekend ? 1 : 6 + ((i * 7) % 9);
    days.push({ date: date.toISOString().slice(0, 10), count: base });
  }
  return mockDelay(days, 0.02);
}

export function fetchMockEpisodeStats(robotId: string): Promise<EpisodeStats> {
  return mockDelay(
    {
      total_episodes: robotId ? 142 : 0,
      success_rate: 0.92,
      outcomes: [
        { outcome: "SUCCESS" as const, count: 131 },
        { outcome: "STOPPED" as const, count: 4 },
        { outcome: "TIMEOUT" as const, count: 3 },
        { outcome: "MAX_STEPS" as const, count: 2 },
        { outcome: "SAFETY_ABORT" as const, count: 2 },
      ],
    },
    0.03,
  );
}

/** Mock thống kê lượt chạy cho dashboard — dùng trong mock mode khi backend chưa chạy. */
export async function fetchMockRobotStats(robotId: string): Promise<import("@/lib/api/robots").RobotStats> {
  if (!robotId) {
    return {
      window_days: 14,
      total_runs: 0,
      finished_runs: 0,
      success_rate: null,
      avg_duration_s: null,
      outcomes: [],
      daily_activity: [],
    };
  }
  const daily = await fetchMockDailyActivity(robotId);
  const total = daily.reduce((s, d) => s + d.count, 0);
  return {
    window_days: 14,
    total_runs: total,
    finished_runs: Math.floor(total * 0.88),
    success_rate: 0.88,
    avg_duration_s: 34.5,
    outcomes: [
      { status: "COMPLETED", count: Math.floor(total * 0.75) },
      { status: "FAILED", count: Math.floor(total * 0.08) },
      { status: "RUNNING", count: Math.floor(total * 0.10) },
      { status: "CANCELLED", count: Math.floor(total * 0.05) },
      { status: "TIMEOUT", count: Math.floor(total * 0.02) },
    ],
    daily_activity: daily,
  };
}

/* ------------------------------------------------------------ Edge registry */

/** Mock mode không có Edge thật nên không có ground truth để tra — suy ra
    loại tay tất định từ ID (cùng ID luôn cùng loại tay giữa các lần gọi)
    thay vì random, để hộp thoại "Thêm robot" không đổi loại tay giữa các lần
    người dùng gõ lại cùng một ID. */
function mockEdgeRobotType(id: string): "franka" | "so101" {
  let hash = 0;
  for (let i = 0; i < id.length; i++) hash = (hash * 31 + id.charCodeAt(i)) | 0;
  return Math.abs(hash) % 2 === 0 ? "so101" : "franka";
}

/** `GET /edge/registry/{id}` mock — coi mọi ID chưa ghép nối là "Edge đã tự
    đăng ký sẵn" (mock không mô phỏng việc bật Edge lên trước). ID đã ghép
    nối rồi thì báo not-found, khớp hành vi thật (không đăng ký lại). */
export function fetchMockEdgeRegistry(
  id: string,
): Promise<import("@/lib/api/robots").EdgeRegistryEntry | null> {
  const trimmed = id.trim();
  if (robotFixtures.some((r) => r.id === trimmed) || mockRobots.some((r) => r.id === trimmed)) {
    return mockDelay(null, 0.02);
  }
  return mockDelay(
    {
      robot_id: trimmed,
      robot_type: mockEdgeRobotType(trimmed),
      capabilities: [],
      registered_at: new Date().toISOString(),
      last_seen_at: new Date().toISOString(),
    },
    0.2,
  );
}

/* ------------------------------------------------------------ thêm robot */

/** POST /robots — ghép nối thêm một cánh tay.
 *
 * Bản mock giữ robot mới trong `mockRobots` để danh sách trên Tổng quan phản
 * ánh đúng thao tác vừa làm. Robot mới luôn ONLINE/IDLE với bộ khớp khoẻ: mock
 * không có thiết bị thật để hỏi, và dựng sẵn một con đang lỗi thì người thử
 * giao diện sẽ tưởng lệnh ghép nối hỏng.
 *
 * Trùng ID thì TỪ CHỐI, không ghi đè: ID là thứ định danh cánh tay vật lý, và
 * âm thầm thay tên/loại của một con đang chạy là kiểu sai nguy hiểm nhất ở đây.
 */
export function addMockRobot(input: { id: string; name: string }): Promise<Robot> {
  const id = input.id.trim();
  if (robotFixtures.some((r) => r.id === id) || mockRobots.some((r) => r.id === id)) {
    return Promise.reject({ code: "ROBOT_012", message: `Robot với ID "${id}" đã tồn tại.` });
  }

  const robot: Robot = {
    id,
    name: input.name.trim(),
    model: mockEdgeRobotType(id),
    robot_status: "IDLE",
    connection_status: "ONLINE",
    edge_version: "1.4.2",
    health: { has_error: false, error_flags: [] },
    capabilities: [
      { name: "capture_tool", version: "1.0" },
      { name: "get_robot_state", version: "1.0" },
      { name: "take_action", version: "2.1" },
      { name: "stop_robot", version: "1.0" },
      { name: "go_home", version: "1.0" },
      { name: "reset_error", version: "1.0" },
      { name: "health_check", version: "1.0" },
      { name: "get_task_status", version: "1.0" },
    ],
    joints: makeJoints(true),
  };

  robotFixtures.push(robot);
  mockRobots.push(robot);
  return mockDelay(robot, 0.08);
}

/** DELETE /robots/{id} — gỡ ghép nối một cánh tay.
 *
 * Cửa một chiều: gỡ xong thì robot biến khỏi cả `robotFixtures` lẫn
 * `mockRobots`, giống hệt việc backend xoá bản ghi khỏi Postgres — chỗ gọi
 * không còn cách nào lấy lại robot này qua `fetchRobots`/`fetchRobot`. */
export function deleteMockRobot(id: string): Promise<void> {
  const idx = robotFixtures.findIndex((r) => r.id === id);
  if (idx === -1) return Promise.reject({ code: "ROBOT_011", message: "Không tìm thấy robot." });
  robotFixtures.splice(idx, 1);
  const mockIdx = mockRobots.findIndex((r) => r.id === id);
  if (mockIdx !== -1) mockRobots.splice(mockIdx, 1);
  return mockDelay(undefined, 0.05);
}
