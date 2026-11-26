import assert from "node:assert/strict";

import {
  isRobotRunJournalEvent,
  traceEventsToLogs,
  traceEventsToReplayFrames,
  traceEventsToServoSamples,
} from "../lib/api/run-replay.ts";
import type { TraceEvent } from "../lib/api/runs.ts";

function event(sequence: number, type: string, metadata: Record<string, unknown> = {}): TraceEvent {
  return {
    id: `evt-${sequence}`,
    run_id: "run_1",
    trace_id: "trc_1",
    sequence,
    timestamp: new Date(1_000_000 + sequence * 1000).toISOString(),
    type,
    status: null,
    duration_ms: null,
    model: null,
    tool_name: type.startsWith("tool.") ? "take_action" : null,
    arguments: null,
    result_summary: null,
    error: null,
    metadata,
  };
}

const servoSample = {
  ts: 1002,
  step: 4,
  joints: [
    { id: 1, name: "shoulder", pos: 0.25, vel: 0.1 },
    { id: 2, name: "elbow", pos: -0.1, vel: 0.2 },
  ],
};

const events = [
  event(1, "run.started"),
  event(2, "llm.started"),
  event(3, "tool.started", { id: "call_1" }),
  event(4, "episode.started", { episode_id: "task_1", max_steps: 100 }),
  event(5, "servo.stream.status", { episode_id: "task_1", status: "connected" }),
  event(6, "servo.stream.batch", {
    episode_id: "task_1",
    source: "edge_servo_stream",
    sample_count: 1,
    samples: [servoSample],
  }),
  event(7, "robot.state.updated", {
    episode_id: "task_1",
    status: "BUSY",
    source: "episode_monitor",
    robot_state: servoSample,
  }),
  event(8, "episode.progress", { episode_id: "task_1", step: 4, max_steps: 100 }),
  event(9, "episode.completed", { episode_id: "task_1", status: "DONE", success: true }),
  event(10, "verification.started"),
  event(11, "tool.completed", { id: "call_1" }),
  event(12, "run.completed"),
  event(13, "servo.state.updated", { servo_state: servoSample }),
];

assert.deepEqual(
  events.filter(isRobotRunJournalEvent).map((item) => item.type),
  [
    "episode.started",
    "servo.stream.status",
    "servo.stream.batch",
    "robot.state.updated",
    "episode.progress",
    "episode.completed",
  ],
);

const logs = traceEventsToLogs(events);
assert.deepEqual(
  logs.map((item) => item.event),
  [
    "episode.started",
    "servo.stream.status",
    "servo.stream.batch",
    "robot.state.updated",
    "episode.progress",
    "episode.completed",
  ],
);
assert.equal(logs.some((item) => item.service === "llm" || item.service === "tool-executor"), false);

const frames = traceEventsToReplayFrames(events);
assert.equal(frames.every((frame) => frame.actor === "robot"), true);
assert.equal(frames.some((frame) => frame.event.startsWith("llm.") || frame.event.startsWith("tool.")), false);

const servoSamples = traceEventsToServoSamples(events);
assert.equal(servoSamples.length, 2);
assert.deepEqual(
  servoSamples.map((sample) => sample.source),
  ["edge_servo_stream", "episode_monitor"],
);

console.log("Run replay robot journal verification passed.");
