"use client";

import { useCallback, useEffect, useState } from "react";
import { connectRobot } from "@/lib/api/robots";
import { isRobotManuallyDisconnected } from "@/lib/robot-connection-state";

export type CameraConnectionState = "connecting" | "connected" | "reconnecting" | "failed";
export type CameraMode = "mjpeg";

export interface CameraStats {
  fps: number | null;
  latencyMs: number | null;
}

export type CameraName = "top" | "wrist";
export type CameraStreams = Record<CameraName, MediaStream | null>;

const MAX_RETRIES = 3;
const BACKOFF_MS = [2000, 5000, 10_000];
const NO_STATS: CameraStats = { fps: null, latencyMs: null };
const NO_STREAMS: CameraStreams = { top: null, wrist: null };

interface UseCameraSessionArgs {
  robotId: string;
  active?: boolean;
}

export interface CameraSession {
  connectionState: CameraConnectionState;
  mode: CameraMode;
  stats: CameraStats;
  streams: CameraStreams;
  errorCode: string | null;
  retriesExhausted: boolean;
  reconnect: () => void;
}

function errorCodeOf(error: unknown): string {
  if (error && typeof error === "object" && typeof (error as { code?: unknown }).code === "string") {
    return (error as { code: string }).code;
  }
  return "MCP_001";
}

export function useCameraSession({ robotId, active = true }: UseCameraSessionArgs): CameraSession {
  const desiredInitialState: CameraConnectionState = active && robotId ? "connecting" : "failed";
  const [connectionState, setConnectionState] = useState<CameraConnectionState>(desiredInitialState);
  const [errorCode, setErrorCode] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [runId, setRunId] = useState(0);
  const [lastStartKey, setLastStartKey] = useState(`${robotId}:${active}:${runId}`);
  const startKey = `${robotId}:${active}:${runId}`;

  if (startKey !== lastStartKey) {
    setLastStartKey(startKey);
    if (active && robotId) {
      setConnectionState(connectionState === "failed" ? "reconnecting" : "connecting");
      setErrorCode(null);
    }
  }

  const reconnect = useCallback(() => {
    setErrorCode(null);
    setAttempt(0);
    setConnectionState("connecting");
    setRunId((id) => id + 1);
  }, []);

  useEffect(() => {
    if (!active || !robotId) return;
    let cancelled = false;

    if (isRobotManuallyDisconnected(robotId)) {
      Promise.resolve().then(() => {
        if (cancelled) return;
        setErrorCode("ROBOT_042");
        setConnectionState("failed");
      });
      return () => {
        cancelled = true;
      };
    }

    void connectRobot(robotId)
      .then(() => {
        if (cancelled) return;
        setAttempt(0);
        setConnectionState("connected");
      })
      .catch((error) => {
        if (cancelled) return;
        setErrorCode(errorCodeOf(error));
        setConnectionState("failed");
      });

    return () => {
      cancelled = true;
    };
  }, [active, robotId, runId]);

  const retriesExhausted = connectionState === "failed" && attempt >= MAX_RETRIES;

  useEffect(() => {
    if (connectionState !== "failed" || attempt >= MAX_RETRIES) return;
    const timer = setTimeout(() => {
      setAttempt((value) => value + 1);
      setConnectionState("reconnecting");
      setRunId((id) => id + 1);
    }, BACKOFF_MS[attempt] ?? 10_000);
    return () => clearTimeout(timer);
  }, [attempt, connectionState]);

  return {
    connectionState,
    mode: "mjpeg",
    stats: NO_STATS,
    streams: NO_STREAMS,
    errorCode,
    retriesExhausted,
    reconnect,
  };
}
