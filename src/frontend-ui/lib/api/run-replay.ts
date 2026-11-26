import type { MockLogEntry } from "@/lib/mock/runs";
import type { TraceEvent } from "./runs";

export interface ReplayJoint {
  id?: number;
  name: string;
  pos?: number;
  vel?: number;
  actuator_force?: number;
  range?: [number, number];
}

export interface ReplayServoSample {
  at: string;
  sequence: number;
  step?: number | null;
  status?: string;
  source?: string;
  joints: ReplayJoint[];
  eef_pos?: number[];
  gripper_qpos?: number[];
}

export interface ReplayFrame {
  at: string;
  sequence: number;
  event: string;
  label: string;
  actor: "agent" | "robot" | "system";
  agentAction?: string;
  robotAction?: string;
  status?: string;
  tool?: string;
  episodeId?: string;
  step?: number;
  maxSteps?: number;
  servo?: ReplayServoSample;
  attrs?: Record<string, unknown>;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function str(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value : undefined;
}

function num(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function numberArray(value: unknown): number[] | undefined {
  return Array.isArray(value) && value.every((item) => typeof item === "number") ? value : undefined;
}

function range(value: unknown): [number, number] | undefined {
  return Array.isArray(value) && value.length === 2 && typeof value[0] === "number" && typeof value[1] === "number"
    ? [value[0], value[1]]
    : undefined;
}

function readJoints(value: unknown): ReplayJoint[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((joint): ReplayJoint[] => {
    if (!isRecord(joint)) return [];
    const name = str(joint.name) ?? (typeof joint.id === "number" ? `joint_${joint.id}` : undefined);
    if (!name) return [];
    return [
      {
        id: num(joint.id),
        name,
        pos: num(joint.pos) ?? num(joint.present_position),
        vel: num(joint.vel) ?? num(joint.present_velocity),
        actuator_force: num(joint.actuator_force) ?? num(joint.torque_nm),
        range: range(joint.range),
      },
    ];
  });
}

function readJointsDeg(value: unknown): ReplayJoint[] {
  if (!Array.isArray(value) || !value.every((item) => typeof item === "number")) return [];
  return value.map((degrees, index) => ({
    id: index,
    name: `joint_${index}`,
    pos: degrees * (Math.PI / 180),
  }));
}

function robotStateOf(event: TraceEvent): Record<string, unknown> | undefined {
  const fromServoMetadata = event.metadata?.servo_state;
  if (isRecord(fromServoMetadata)) return fromServoMetadata;
  const fromSummaryMetadata = event.metadata?.summary;
  if (isRecord(fromSummaryMetadata) && Array.isArray(fromSummaryMetadata.joints)) return fromSummaryMetadata;
  const fromMetadata = event.metadata?.robot_state;
  if (isRecord(fromMetadata)) return fromMetadata;
  if (isRecord(event.result_summary)) return event.result_summary;
  return undefined;
}

function servoSampleOf(event: TraceEvent): ReplayServoSample | undefined {
  const state = robotStateOf(event);
  if (!state) return undefined;
  return servoSampleFromState(event, state, event.sequence);
}

function servoSampleFromState(
  event: TraceEvent,
  state: Record<string, unknown>,
  sequence: number,
): ReplayServoSample | undefined {
  const joints = readJoints(state.joints);
  const fallbackJoints = joints.length > 0 ? joints : readJointsDeg(state.joints_deg);
  if (fallbackJoints.length === 0) return undefined;
  const edgeTimestamp = num(state.ts);
  return {
    at: edgeTimestamp === undefined ? event.timestamp : new Date(edgeTimestamp * 1000).toISOString(),
    sequence,
    step: num(state.step) ?? num(event.metadata?.step) ?? null,
    status: str(event.metadata?.status) ?? str(state.status),
    source: str(event.metadata?.source),
    joints: fallbackJoints,
    eef_pos: numberArray(state.eef_pos),
    gripper_qpos: numberArray(state.gripper_qpos),
  };
}

function servoSamplesOf(event: TraceEvent): ReplayServoSample[] {
  const samples = event.metadata?.samples;
  if (Array.isArray(samples)) {
    return samples.flatMap((sample, index): ReplayServoSample[] => {
      if (!isRecord(sample)) return [];
      const parsed = servoSampleFromState(event, sample, event.sequence + index / 1000);
      return parsed ? [parsed] : [];
    });
  }
  const sample = servoSampleOf(event);
  return sample ? [sample] : [];
}

function hasEpisodeId(event: TraceEvent): boolean {
  return Boolean(str(event.metadata?.episode_id));
}

function isEpisodeMonitorState(event: TraceEvent): boolean {
  return event.type === "robot.state.updated" && str(event.metadata?.source) === "episode_monitor";
}

export function isRobotRunJournalEvent(event: TraceEvent): boolean {
  if (event.type.startsWith("episode.")) return hasEpisodeId(event);
  if (event.type === "robot.state.updated") return hasEpisodeId(event) || isEpisodeMonitorState(event);
  if (event.type === "servo.stream.batch" || event.type === "servo.stream.status") return hasEpisodeId(event);
  if (event.type === "servo.state.updated") return hasEpisodeId(event);
  return false;
}

function eventLevel(event: TraceEvent): MockLogEntry["level"] {
  if (event.type === "run.failed" || event.type === "tool.failed") return "ERROR";
  if (event.type === "run.timeout" || event.type === "run.cancelled" || event.type === "system.warning") return "WARN";
  if (
    event.type === "episode.progress" ||
    event.type === "robot.state.updated" ||
    event.type === "servo.state.updated" ||
    event.type === "servo.stream.batch" ||
    event.type === "servo.stream.status"
  ) return "DEBUG";
  return "INFO";
}

function eventService(event: TraceEvent): string {
  if (event.type.startsWith("tool.")) return "tool-executor";
  if (event.type.startsWith("episode.")) return "vla-runtime";
  if (event.type.startsWith("robot.") || event.type.startsWith("servo.")) return "robot-state";
  if (event.type.startsWith("llm.")) return "llm";
  return "orchestrator";
}

function eventActor(event: TraceEvent): ReplayFrame["actor"] {
  if (event.type.startsWith("tool.") || event.type.startsWith("llm.") || event.type.startsWith("verification.")) {
    return "agent";
  }
  if (event.type.startsWith("episode.") || event.type.startsWith("robot.") || event.type.startsWith("servo.")) return "robot";
  return "system";
}

function eventMessage(event: TraceEvent): string {
  const tool = str(event.tool_name) ?? str(event.metadata?.tool);
  const episodeId = str(event.metadata?.episode_id);
  const step = num(event.metadata?.step);
  const maxSteps = num(event.metadata?.max_steps);
  const status = str(event.metadata?.status) ?? str(event.status);
  if (event.type === "tool.started") return `Bắt đầu tool ${tool ?? "unknown_tool"}`;
  if (event.type === "tool.completed") return `Tool ${tool ?? "unknown_tool"} hoàn thành`;
  if (event.type === "tool.failed") return `Tool ${tool ?? "unknown_tool"} thất bại`;
  if (event.type === "episode.started") return `Episode ${episodeId ?? ""} bắt đầu`.trim();
  if (event.type === "episode.progress") {
    const progress = step !== undefined && maxSteps !== undefined ? ` ${step}/${maxSteps}` : "";
    return `Episode ${episodeId ?? ""} đang chạy${progress}`.trim();
  }
  if (event.type === "episode.completed") return `Episode ${episodeId ?? ""} kết thúc: ${status ?? "UNKNOWN"}`.trim();
  if (event.type === "robot.state.updated") return `Robot state: ${status ?? "UNKNOWN"}`;
  if (event.type === "servo.state.updated") return `Servo snapshot${step !== undefined ? ` bước ${step}` : ""}`;
  if (event.type === "servo.stream.batch") {
    const count = num(event.metadata?.sample_count) ?? 0;
    return `Servo stream: ${count} mẫu`;
  }
  if (event.type === "servo.stream.status") {
    const streamStatus = str(event.metadata?.status) ?? "unknown";
    const reason = str(event.metadata?.reason);
    return `Servo stream ${streamStatus}${reason ? `: ${reason}` : ""}`;
  }
  if (event.type === "run.completed") return "Run hoàn thành";
  if (event.type === "run.failed") return "Run thất bại";
  if (event.type === "run.cancelled") return "Run đã huỷ";
  if (event.type === "run.timeout") return "Run quá thời gian";
  return event.type;
}

function agentAction(event: TraceEvent): string | undefined {
  const tool = str(event.tool_name) ?? str(event.metadata?.tool);
  if (event.type === "llm.started") return "Đang phân tích yêu cầu";
  if (event.type === "llm.completed") return "Đã phân tích xong";
  if (event.type === "tool.started") return `Gọi tool ${tool ?? "unknown_tool"}`;
  if (event.type === "tool.completed") return `Nhận kết quả từ ${tool ?? "unknown_tool"}`;
  if (event.type === "tool.failed") return `Tool ${tool ?? "unknown_tool"} lỗi`;
  if (event.type === "verification.started") return "Bắt đầu xác minh kết quả";
  if (event.type === "verification.completed") return "Hoàn tất xác minh kết quả";
  return undefined;
}

function robotAction(event: TraceEvent): string | undefined {
  const episodeId = str(event.metadata?.episode_id);
  const step = num(event.metadata?.step);
  const maxSteps = num(event.metadata?.max_steps);
  const status = str(event.metadata?.status) ?? str(event.status);
  if (event.type === "episode.started") return `Bắt đầu episode ${episodeId ?? ""}`.trim();
  if (event.type === "episode.progress") {
    const progress = step !== undefined && maxSteps !== undefined ? `: ${step}/${maxSteps} bước` : "";
    return `Đang thao tác${progress}`;
  }
  if (event.type === "episode.completed") return `Kết thúc episode: ${status ?? "UNKNOWN"}`;
  if (event.type === "robot.state.updated") return `Trạng thái robot: ${status ?? "UNKNOWN"}`;
  if (event.type === "servo.state.updated") {
    const progress = step !== undefined && maxSteps !== undefined ? `: ${step}/${maxSteps} bước` : "";
    return `Ghi trạng thái servo${progress}`;
  }
  if (event.type === "servo.stream.batch") return "Ghi stream servo thật từ Edge";
  if (event.type === "servo.stream.status") {
    const streamStatus = str(event.metadata?.status) ?? "unknown";
    return `Servo stream: ${streamStatus}`;
  }
  return undefined;
}

export function traceEventsToLogs(events: TraceEvent[]): MockLogEntry[] {
  return [...events]
    .sort((a, b) => a.sequence - b.sequence)
    .filter(isRobotRunJournalEvent)
    .map((event) => ({
      timestamp: event.timestamp,
      level: eventLevel(event),
      service: eventService(event),
      event: event.type,
      message: eventMessage(event),
      trace_id: event.trace_id,
      attrs: {
        sequence: event.sequence,
        tool_name: event.tool_name,
        duration_ms: event.duration_ms,
        arguments: event.arguments,
        result_summary: event.result_summary,
        error: event.error,
        metadata: event.metadata,
      },
    }));
}

export function traceEventsToReplayFrames(events: TraceEvent[]): ReplayFrame[] {
  return [...events]
    .sort((a, b) => a.sequence - b.sequence)
    .filter(isRobotRunJournalEvent)
    .map((event) => {
      const servo = servoSampleOf(event);
      return {
        at: event.timestamp,
        sequence: event.sequence,
        event: event.type,
        label: eventMessage(event),
        actor: eventActor(event),
        agentAction: agentAction(event),
        robotAction: robotAction(event),
        status: str(event.metadata?.status) ?? str(event.status),
        tool: str(event.tool_name) ?? str(event.metadata?.tool),
        episodeId: str(event.metadata?.episode_id),
        step: num(event.metadata?.step),
        maxSteps: num(event.metadata?.max_steps),
        servo,
        attrs: {
          duration_ms: event.duration_ms,
          error: event.error,
          metadata: event.metadata,
        },
      };
    });
}

export function traceEventsToServoSamples(events: TraceEvent[]): ReplayServoSample[] {
  return [...events]
    .sort((a, b) => a.sequence - b.sequence)
    .filter(isRobotRunJournalEvent)
    .flatMap(servoSamplesOf)
    .sort((a, b) => Date.parse(a.at) - Date.parse(b.at));
}
