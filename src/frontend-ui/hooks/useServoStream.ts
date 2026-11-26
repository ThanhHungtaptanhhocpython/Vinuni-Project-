"use client";

import { useEffect, useState } from "react";
import { streamServoState, type ServoSample } from "@/lib/api/servo-stream";

export type ServoStreamStatus = "connecting" | "connected" | "error" | "idle";

const HISTORY_POINTS = 50;

export function useServoStream(robotId: string, enabled = true) {
  const [sampleState, setSampleState] = useState<{
    robotId: string;
    sample: ServoSample;
    history: ServoSample[];
    latencyMs: number | null;
  } | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!enabled || !robotId) {
      return;
    }

    const controller = new AbortController();

    Promise.resolve().then(() => {
      if (!controller.signal.aborted) {
        setSampleState(null);
        setError(null);
      }
    });

    streamServoState({
      robotId,
      signal: controller.signal,
      onSample: (next) => {
        const receivedAtMs = Date.now();
        const sample = { ...next, received_at_ms: receivedAtMs };
        const latencyMs = next.ts ? Math.max(0, receivedAtMs - next.ts * 1000) : null;
        setSampleState((prev) => {
          const previousHistory = prev?.robotId === robotId ? prev.history : [];
          const history = [...previousHistory, sample];
          if (history.length > HISTORY_POINTS) history.shift();
          return { robotId, sample, history, latencyMs };
        });
      },
    }).catch((err) => {
      if (controller.signal.aborted) return;
      setError(err instanceof Error ? err.message : String(err));
    });

    return () => {
      controller.abort();
    };
  }, [enabled, robotId]);

  const sample = sampleState?.robotId === robotId ? sampleState.sample : null;
  const history = sampleState?.robotId === robotId ? sampleState.history : [];
  const latencyMs = sampleState?.robotId === robotId ? sampleState.latencyMs : null;
  const status: ServoStreamStatus = !enabled || !robotId ? "idle" : error ? "error" : sample ? "connected" : "connecting";

  return { sample, history, status, error, latencyMs };
}
