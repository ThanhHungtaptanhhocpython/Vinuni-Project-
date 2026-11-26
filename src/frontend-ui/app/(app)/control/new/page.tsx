"use client";
/* /control/new — P04 "Route phụ"
   Reopens the latest phiên for this robot (Điều khiển). Pass ?new=1 to force
   a blank session. Then replace the URL with /control/{sessionId}. */

import { useEffect, useState, Suspense } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Loader, Wifi } from "lucide-react";
import { createSession, fetchLatestSession } from "@/lib/api/sessions";
import { ErrorPanel } from "@/components/common/ErrorMessage";
import { toErrorObject, type ErrorObject } from "@/lib/errors";
import { connectRobot, fetchRobots } from "@/lib/api/robots";
import { RobotStatusBadge } from "@/components/robot-state/RobotStatusBadge";
import { EmptyState } from "@/components/common/EmptyState";
import { robotModelLabel } from "@/lib/robot/models";
import { toast, useAuthStore, type Robot } from "@/stores";

function RobotPicker({
  robots,
  busyRobotId,
  onPick,
}: {
  robots: Robot[];
  busyRobotId: string | null;
  onPick: (robot: Robot) => void;
}) {
  if (robots.length === 0) {
    return <EmptyState message="Chưa có robot trong hệ thống." />;
  }

  return (
    <div className="panel p-4 w-full max-w-[640px] space-y-3">
      <div>
        <h1 className="title text-[18px] text-paper-50">Chọn robot cho phiên chat</h1>
        <p className="text-[13.5px] text-haze-500 mt-1">
          Robot chưa online sẽ được kết nối trước khi mở phiên.
        </p>
      </div>
      <div className="space-y-2">
        {robots.map((robot) => (
          <button
            key={robot.id}
            type="button"
            onClick={() => onPick(robot)}
            disabled={busyRobotId !== null}
            className="w-full flex items-center gap-3 p-3 rounded-lg bg-ink-900 border border-[var(--line)] hover:border-[var(--line-strong)] text-left focus-ring disabled:opacity-60 disabled:cursor-not-allowed"
          >
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="text-[14px] font-medium text-paper-50 truncate">{robot.name}</span>
                <RobotStatusBadge robotStatus={robot.robot_status} connectionStatus={robot.connection_status} size="sm" />
              </div>
              <p className="eyebrow mt-1.5 flex gap-2">
                <span className="data">{robot.id}</span>
                <span aria-hidden="true">·</span>
                <span>{robotModelLabel(robot.model)}</span>
              </p>
            </div>
            <span className="btn-ghost !h-8 !text-[12.5px] pointer-events-none">
              {busyRobotId === robot.id ? (
                <Loader size={13} className="animate-spin-slow" />
              ) : (
                <Wifi size={13} />
              )}
              {robot.connection_status === "ONLINE" ? "Mở" : "Kết nối"}
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}

function NewSession() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const selectRobot = useAuthStore((s) => s.selectRobot);
  const [robots, setRobots] = useState<Robot[]>([]);
  const [robotsState, setRobotsState] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState<ErrorObject | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [busyRobotId, setBusyRobotId] = useState<string | null>(null);

  const forceNew = searchParams?.get("new") === "1";
  const robotIdFromUrl = searchParams?.get("robot_id");

  useEffect(() => {
    let cancelled = false;
    Promise.resolve().then(() => {
      if (!cancelled) setRobotsState("loading");
    });
    fetchRobots()
      .then((rows) => {
        if (cancelled) return;
        setRobots(rows);
        setRobotsState("ready");
      })
      .catch((err) => {
        if (cancelled) return;
        setError(toErrorObject(err));
        setRobotsState("error");
      });
    return () => {
      cancelled = true;
    };
  }, [attempt]);

  useEffect(() => {
    let cancelled = false;

    const open = async () => {
      if (robotsState !== "ready") return;
      if (!robotIdFromUrl) return;
      const robot = robots.find((r) => r.id === robotIdFromUrl);
      if (!robot) {
        throw { code: "ROBOT_000", message: "Robot đã chọn không còn thuộc tài khoản của bạn." };
      }
      setBusyRobotId(robot.id);
      if (robot.connection_status !== "ONLINE") await connectRobot(robot.id);
      const existing = forceNew ? null : await fetchLatestSession(robot.id);
      const session = existing ?? (await createSession(robot.id));
      if (cancelled) return;
      selectRobot(session.robot_id);
      router.replace(`/control/${session.id}`);
    };

    open().catch((err) => {
      if (!cancelled) setError(toErrorObject(err));
    });

    return () => {
      cancelled = true;
    };
  }, [robotsState, robotIdFromUrl, forceNew, attempt]); // eslint-disable-line react-hooks/exhaustive-deps -- router/selectRobot are stable; including them retriggers and cancels the redirect

  const chooseRobot = async (robot: Robot) => {
    setError(null);
    setBusyRobotId(robot.id);
    try {
      if (robot.connection_status !== "ONLINE") {
        await connectRobot(robot.id);
        toast.success("Đã kết nối robot.");
      }
      selectRobot(robot.id);
      const existing = forceNew ? null : await fetchLatestSession(robot.id);
      const session = existing ?? (await createSession(robot.id));
      router.replace(`/control/${session.id}`);
    } catch (err) {
      setError(toErrorObject(err));
      setBusyRobotId(null);
    }
  };

  if (error) {
    return (
      <div className="p-6 max-w-[680px] mx-auto mt-16 space-y-4">
        <div className="max-w-[520px] mx-auto">
          <ErrorPanel
            error={error}
            onRetry={() => {
              setError(null);
              setAttempt((a) => a + 1);
            }}
          />
        </div>
        {robotsState === "ready" && (
          <RobotPicker robots={robots} busyRobotId={busyRobotId} onPick={chooseRobot} />
        )}
      </div>
    );
  }

  if (robotsState === "ready" && !robotIdFromUrl) {
    return (
      <div className="h-full flex flex-col items-center justify-center gap-4 p-6">
        <RobotPicker robots={robots} busyRobotId={busyRobotId} onPick={chooseRobot} />
      </div>
    );
  }

  return (
    <div className="h-full flex flex-col items-center justify-center gap-3 text-haze-500">
      <Loader size={20} className="animate-spin-slow" />
      <p className="text-[14px]">Đang mở trò chuyện gần nhất…</p>
    </div>
  );
}

export default function NewControlSessionPage() {
  return (
    <Suspense
      fallback={
        <div className="h-full flex items-center justify-center text-haze-500">
          <Loader size={20} className="animate-spin-slow" />
        </div>
      }
    >
      <NewSession />
    </Suspense>
  );
}
