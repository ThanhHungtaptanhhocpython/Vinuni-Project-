"use client";
/* AppShellClient — P02
   The interactive half of the (app) layout, kept out of the Server Component
   layout.tsx (MASTER §18.1). Owns: sidebar, topbar, safety banner slot,
   ⌘K palette, and the global "Esc Esc" emergency stop.

   Không còn Telemetry Rail: nội dung trùng Tổng quan; trạng thái robot vẫn
   thấy trên topbar. */

import { useState, useEffect, useCallback, useRef, type ReactNode } from "react";
import { Sidebar } from "./Sidebar";
import { Topbar } from "./Topbar";
import { SafetyBanner } from "./SafetyBanner";
import { CommandPalette } from "./CommandPalette";
import { fetchRobots, stopRobot } from "@/lib/api/robots";
import { errorText, toErrorObject } from "@/lib/errors";
import { USE_MOCK } from "@/lib/api/config";
import { bootstrapSession } from "@/lib/api/auth";
import { fetchAgentStatus, type AgentStatus } from "@/lib/api/status";
import { hasSession, markSessionStarted, logout } from "@/lib/auth/session";
import { useAuthStore, useRealtimeStore, useUiPreferenceStore, hydrateUiPreferences, toast, type Robot } from "@/stores";
import { useRouter } from "next/navigation";
import { ROBOT_CONNECTION_CHANGED_EVENT } from "@/lib/robot-connection-state";

const ESC_ESC_WINDOW_MS = 400;
const ROBOT_POLL_MS = 15_000;

/** Safety error flags that raise the assertive banner — MASTER §12.2. */
const SAFETY_FLAGS = ["ROBOT_021", "ROBOT_022", "ROBOT_023", "OVERLOAD", "JOINT_LIMIT", "OVERHEAT"];

export function AppShellClient({ children }: { children: ReactNode }) {
  const router = useRouter();
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [robots, setRobots] = useState<Robot[]>([]);
  const [authReady, setAuthReady] = useState(USE_MOCK);
  const [robotTransport, setRobotTransport] = useState<AgentStatus["robot_transport"] | null>(null);

  const selectedRobotId = useAuthStore((s) => s.selectedRobotId);
  const liveRobotState = useRealtimeStore((s) => s.robotState);
  const sidebarCollapsed = useUiPreferenceStore((s) => s.sidebarCollapsed);
  const toggleSidebar = useUiPreferenceStore((s) => s.toggleSidebar);
  const lastEscRef = useRef(0);

  // Bootstrap: refresh → /me, hoặc mock user. Redirect /login khi thật và 401.
  useEffect(() => {
    hydrateUiPreferences();
    let cancelled = false;

    (async () => {
      if (USE_MOCK) {
        if (!useAuthStore.getState().user) {
          useAuthStore.getState().setUser({
            id: "user-001",
            name: "Phòng Lab",
            email: "lab@example.io",
            avatar_url: null,
            timezone: "Asia/Ho_Chi_Minh",
          });
        }
        if (!hasSession()) markSessionStarted();
        if (!cancelled) setAuthReady(true);
        return;
      }

      const user = await bootstrapSession();
      if (cancelled) return;
      if (!user) {
        await logout();
        router.replace("/login?error=AUTH_004");
        return;
      }
      useAuthStore.getState().setUser(user);
      markSessionStarted();
      setAuthReady(true);
    })();

    return () => {
      cancelled = true;
    };
  }, [router]);

  useEffect(() => {
    let cancelled = false;
    const load = () => {
      fetchRobots()
        .then((data) => {
          if (cancelled) return;
          setRobots(data);
          const selected = useAuthStore.getState().selectedRobotId;
          if (selected && !data.some((robot) => robot.id === selected)) {
            useAuthStore.getState().selectRobot(null);
          }
        })
        .catch(() => undefined);
    };
    load();
    const interval = setInterval(load, ROBOT_POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  useEffect(() => {
    const onRobotChanged = (event: Event) => {
      const robot = (event as CustomEvent<Robot>).detail;
      if (!robot?.id) return;
      setRobots((previous) => previous.map((row) => (row.id === robot.id ? robot : row)));
      if (robot.connection_status === "OFFLINE") {
        const live = useRealtimeStore.getState().robotState;
        if (live) {
          useRealtimeStore.getState().updateRobotState({
            ...live,
            connection_status: "OFFLINE",
          });
        }
      }
    };
    window.addEventListener(ROBOT_CONNECTION_CHANGED_EVENT, onRobotChanged);
    return () => window.removeEventListener(ROBOT_CONNECTION_CHANGED_EVENT, onRobotChanged);
  }, []);

  /* Nguồn duy nhất cho "robot đang thật hay giả lập" — GET /status
     (`routes.py` `agent_status`). Hỏi MỘT LẦN khi shell mount: đây là cấu
     hình server-side (`MCP_ENDPOINT_URL`/`ROBOT_MODE`), không đổi giữa các
     request nên không cần poll lại như danh sách robot. Chỉ hỏi ở chế độ
     thật — ở USE_MOCK không có backend nào để hỏi, và im lặng bỏ qua đúng
     hơn là báo sai. */
  useEffect(() => {
    if (USE_MOCK) return;
    let cancelled = false;
    fetchAgentStatus().then((status) => {
      if (!cancelled) setRobotTransport(status?.robot_transport ?? null);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  const selectedRobot = robots.find((r) => r.id === selectedRobotId) ?? null;
  const effectiveStatus = liveRobotState?.robot_status ?? selectedRobot?.robot_status ?? "IDLE";

  /* ---- Esc Esc → emergency stop (MASTER §12.1) ---- */
  const emergencyStop = useCallback(() => {
    const robot = robots.find((r) => r.id === useAuthStore.getState().selectedRobotId);
    if (!robot) return;
    stopRobot(robot.id)
      .then(() => toast.success(`Đã gửi lệnh dừng ${robot.name}.`))
      .catch((err) => toast.error(errorText(toErrorObject(err))));
  }, [robots]);

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        const now = Date.now();
        if (now - lastEscRef.current < ESC_ESC_WINDOW_MS && effectiveStatus === "BUSY") {
          lastEscRef.current = 0;
          emergencyStop();
        } else {
          lastEscRef.current = now;
        }
        return;
      }
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPaletteOpen((v) => !v);
        return;
      }
      // ⌘B / Ctrl+B — thu gọn navbar, quen tay từ các app cùng loại.
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "b") {
        e.preventDefault();
        toggleSidebar();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [emergencyStop, effectiveStatus, toggleSidebar]);

  /* ---- safety banner ---- */
  const safetyFlag = selectedRobot?.health.error_flags.find((flag) => SAFETY_FLAGS.includes(flag));

  if (!authReady) {
    return (
      <div className="app-shell-bg flex h-screen items-center justify-center text-haze-500 text-[14px]">
        Đang xác thực phiên…
      </div>
    );
  }

  return (
    /* Hai tấm nổi trên nền lưới, cách nhau 8px: cột trái TRONG SUỐT — chỉ còn
       viền, lưới xuyên qua sắc nét — còn cột phải bằng kính đục để dữ liệu vận
       hành luôn đọc rõ. Cùng bo góc, cùng kiểu viền. */
    <div className="app-shell-bg flex h-screen overflow-hidden gap-2 p-2">
      {/* Không truyền `robot` xuống Sidebar: develop cho sidebar hiện trạng
          thái robot, nhưng ở bản này trạng thái đó nằm trên Topbar (§2.1 ưu
          tiên 2). Để cả hai chỗ cùng hiện thì hai chấm trạng thái có thể lệch
          nhau, mà đây là thông tin an toàn. */}
      <Sidebar
        collapsed={sidebarCollapsed}
        onToggleCollapsed={toggleSidebar}
        mobileOpen={mobileMenuOpen}
        onMobileClose={() => setMobileMenuOpen(false)}
      />

      <div className="glass-edge flex-1 flex flex-col min-w-0 overflow-hidden rounded-xl border border-[var(--line)]">
        <Topbar
          selectedRobot={selectedRobot}
          robotTransport={robotTransport}
          onMobileMenuOpen={() => setMobileMenuOpen(true)}
          onCommandPaletteOpen={() => setPaletteOpen(true)}
        />

        {/* Pushes content down, never overlays it (MASTER §12.2).
            `key` theo robot + toàn bộ tập cờ lỗi hiện tại: SafetyBanner giữ
            `dismissed` bằng `useState` cục bộ, nên nếu không có `key`, một
            cảnh báo đã bị đóng vẫn giữ nguyên INSTANCE khi cờ lỗi đổi thẳng
            từ mã này sang mã khác (không đi qua trạng thái rỗng ở giữa) —
            React tái dùng component cũ, `dismissed` vẫn `true`, và cảnh báo
            MỚI không bao giờ hiện ra dù §12.2 đòi nó phải luôn hiện. */}
        {safetyFlag && selectedRobot && (
          <SafetyBanner
            key={`${selectedRobot.id}:${selectedRobot.health.error_flags.join(",")}`}
            errorCode={safetyFlag}
            robotName={selectedRobot.name}
            flags={selectedRobot.health.error_flags}
          />
        )}

        <main className="flex-1 min-h-0 overflow-y-auto">{children}</main>
      </div>

      {paletteOpen && <CommandPalette onClose={() => setPaletteOpen(false)} robots={robots} />}
    </div>
  );
}
