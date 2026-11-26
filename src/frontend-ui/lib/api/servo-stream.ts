export interface ServoJointSample {
  id?: number;
  name: string;
  pos?: number;
  vel?: number;
  actuator_force?: number;
  range?: [number, number];
}

export interface ServoSample {
  step?: number | null;
  joints: ServoJointSample[];
  gripper_qpos?: number[];
  eef_pos?: number[];
  ts?: number;
  received_at_ms?: number;
}

interface StreamServoOptions {
  robotId: string;
  signal: AbortSignal;
  onSample: (sample: ServoSample) => void;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function readNumberArray(value: unknown): number[] | undefined {
  if (!Array.isArray(value) || !value.every((v) => typeof v === "number")) return undefined;
  return value;
}

function readRange(value: unknown): [number, number] | undefined {
  if (!Array.isArray(value) || value.length !== 2) return undefined;
  if (typeof value[0] !== "number" || typeof value[1] !== "number") return undefined;
  return [value[0], value[1]];
}

function parseSample(data: string): ServoSample | null {
  let raw: unknown;
  try {
    raw = JSON.parse(data);
  } catch {
    return null;
  }
  if (!isRecord(raw) || !Array.isArray(raw.joints)) return null;

  const joints = raw.joints.flatMap((joint): ServoJointSample[] => {
    if (!isRecord(joint) || typeof joint.name !== "string") return [];
    return [{
      id: typeof joint.id === "number" ? joint.id : undefined,
      name: joint.name,
      pos: typeof joint.pos === "number" ? joint.pos : undefined,
      vel: typeof joint.vel === "number" ? joint.vel : undefined,
      actuator_force: typeof joint.actuator_force === "number" ? joint.actuator_force : undefined,
      range: readRange(joint.range),
    }];
  });

  return {
    step: typeof raw.step === "number" || raw.step === null ? raw.step : undefined,
    joints,
    gripper_qpos: readNumberArray(raw.gripper_qpos),
    eef_pos: readNumberArray(raw.eef_pos),
    ts: typeof raw.ts === "number" ? raw.ts : undefined,
  };
}

function parseData(block: string): string {
  return block
    .split("\n")
    .filter((line) => line.startsWith("data:"))
    .map((line) => line.slice(5).trimStart())
    .join("\n");
}

export async function streamServoState({ robotId, signal, onSample }: StreamServoOptions): Promise<void> {
  const response = await fetch(`/api/v1/robots/${encodeURIComponent(robotId)}/stream/servo`, {
    method: "GET",
    headers: { Accept: "text/event-stream" },
    signal,
    cache: "no-store",
  });

  if (!response.ok || !response.body) {
    throw new Error(`Không mở được servo stream (HTTP ${response.status}).`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      let sep = buffer.indexOf("\n\n");
      while (sep !== -1) {
        const block = buffer.slice(0, sep);
        buffer = buffer.slice(sep + 2);
        const data = parseData(block);
        if (data) {
          const sample = parseSample(data);
          if (sample) onSample(sample);
        }
        sep = buffer.indexOf("\n\n");
      }
    }
  } finally {
    reader.cancel().catch(() => undefined);
  }
}
