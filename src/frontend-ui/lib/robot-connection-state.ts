import type { Robot } from "@/stores";

const MANUAL_DISCONNECT_KEY = "manual_disconnected_robots";
export const ROBOT_CONNECTION_CHANGED_EVENT = "robot-connection-changed";

function readIds(): Set<string> {
  if (typeof window === "undefined") return new Set();
  try {
    const raw = localStorage.getItem(MANUAL_DISCONNECT_KEY);
    const ids = raw ? JSON.parse(raw) : [];
    return new Set(Array.isArray(ids) ? ids.filter((id): id is string => typeof id === "string") : []);
  } catch {
    return new Set();
  }
}

function writeIds(ids: Set<string>): void {
  if (typeof window === "undefined") return;
  try {
    localStorage.setItem(MANUAL_DISCONNECT_KEY, JSON.stringify([...ids]));
  } catch {
    /* ignore localStorage errors */
  }
}

export function markRobotManuallyDisconnected(robotId: string): void {
  const ids = readIds();
  ids.add(robotId);
  writeIds(ids);
}

export function clearRobotManualDisconnect(robotId: string): void {
  const ids = readIds();
  ids.delete(robotId);
  writeIds(ids);
}

export function isRobotManuallyDisconnected(robotId: string): boolean {
  return readIds().has(robotId);
}

export function emitRobotConnectionChanged(robot: Robot): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent<Robot>(ROBOT_CONNECTION_CHANGED_EVENT, { detail: robot }));
}
