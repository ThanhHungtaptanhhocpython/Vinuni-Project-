"use client";
/* ToolCallBody — phần "agent đã thấy gì" của một tool call (MASTER §13.1).
 *
 * Tách khỏi `ToolCard` để hai chỗ cùng dùng được: dòng trong `AgentTrace` xổ ra
 * chính khối này, và `ToolCard` (khung riêng, dùng cho timeline P09) cũng render
 * nó. Trước đây nó nằm lồng trong `ToolCard`, nên muốn hiện chi tiết ở chỗ khác
 * thì phải kéo theo cả khung thẻ.
 *
 * Render theo TỪNG tool — không bao giờ đổ JSON thô — và chỉ từ những trường
 * backend thật sự trả về (MASTER §13.3). Thiếu trường thì ẩn phần tử, không
 * hiện `N/A`. */

import { useState } from "react";
import { Check, TriangleAlert } from "lucide-react";
import type { ToolCall, JointState } from "@/stores";
import { EpisodeProgress } from "./EpisodeProgress";
import { JointTable } from "@/components/robot-state/JointTable";
import { Lightbox, type ImageRef } from "@/components/common/Lightbox";
import { CopyableCode } from "@/components/common/CopyableCode";
import { errorText } from "@/lib/errors";
import { toolArgSummary } from "@/lib/tool-display";

/* ---------------------------------------------------------------- helpers */

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null;
}

function imageUrlOf(raw: Record<string, unknown>): string | null {
  const url = typeof raw.url === "string" ? raw.url.trim() : "";
  const thumbnail = typeof raw.thumbnail_b64 === "string" ? raw.thumbnail_b64.trim() : "";

  if (url.startsWith("http://") || url.startsWith("https://") || url.startsWith("data:") || url.startsWith("/")) {
    return url;
  }
  if (thumbnail) {
    return thumbnail.startsWith("data:") ? thumbnail : `data:image/jpeg;base64,${thumbnail}`;
  }
  if (url && !url.startsWith("file://")) {
    return `data:image/jpeg;base64,${url}`;
  }
  return null;
}

/** Pull typed images out of an untyped tool result without inventing fields.
    Real `edge_vla`'s `ImageRef` (`cloud-agent/agents/tools/mcp_client.py`)
    names the field `camera_id` with values `"agentview"`/`"wrist"` — `camera`
    was the old spec's field name, kept as a fallback in case another edge
    server still sends it. `agentview` = the UI's "top" camera. */
export function readImages(result: Record<string, unknown> | undefined): ImageRef[] {
  if (!result || !Array.isArray(result.images)) return [];
  return result.images.flatMap((raw): ImageRef[] => {
    if (!isRecord(raw)) return [];
    const url = imageUrlOf(raw);
    if (!url) return [];
    const cameraId = raw.camera_id ?? raw.camera;
    const camera = cameraId === "wrist" ? "wrist" : "top";
    return [{
      id: typeof raw.id === "string" ? raw.id : url,
      url,
      camera,
      captured_at: typeof raw.captured_at === "string" ? raw.captured_at : "",
    }];
  });
}

function readJoints(result: Record<string, unknown> | undefined): JointState[] {
  if (!result || !Array.isArray(result.joints)) return [];
  return result.joints.flatMap((raw): JointState[] => {
    if (!isRecord(raw) || typeof raw.name !== "string") return [];
    return [{
      name: raw.name,
      present_position: typeof raw.present_position === "number" ? raw.present_position : 0,
      present_load: typeof raw.present_load === "number" ? raw.present_load : 0,
      torque_enabled: raw.torque_enabled === true,
      moving: raw.moving === true,
      error_flags: Array.isArray(raw.error_flags) ? raw.error_flags.filter((f): f is string => typeof f === "string") : [],
    }];
  });
}

function readStrings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((v): v is string => typeof v === "string") : [];
}

/** Real `edge_vla.get_servo_state()` shape (`sim/sim_env.py`'s `servo_state()`):
    `{name: "joint_0".."joint_N", pos, vel, actuator_force, range: [min,max]}`,
    radians/rad-per-s/N·m — MuJoCo-native, not SO-101's named joints/degrees
    (`JointTable`'s `shoulder_pan`/`wrist_1`/... vocabulary, which nothing
    here maps to — inventing that mapping would be a guess, not a read of a
    real field, so this stays a separate, plainer table instead of forcing
    the data into `JointTable`). */
interface RawServoJoint {
  name: string;
  pos?: number;
  vel?: number;
  actuator_force?: number;
  range?: [number, number];
}

function readServoJoints(result: Record<string, unknown> | undefined): RawServoJoint[] {
  if (!result || !Array.isArray(result.joints)) return [];
  return result.joints.flatMap((raw): RawServoJoint[] => {
    if (!isRecord(raw) || typeof raw.name !== "string") return [];
    const range =
      Array.isArray(raw.range) && raw.range.length === 2 && raw.range.every((n) => typeof n === "number")
        ? (raw.range as [number, number])
        : undefined;
    return [{
      name: raw.name,
      pos: typeof raw.pos === "number" ? raw.pos : undefined,
      vel: typeof raw.vel === "number" ? raw.vel : undefined,
      actuator_force: typeof raw.actuator_force === "number" ? raw.actuator_force : undefined,
      range,
    }];
  });
}

/* -------------------------------------------------------------- component */

interface ToolCallBodyProps {
  toolCall: ToolCall;
  /** Trang điều khiển truyền `true` để thanh episode mời được "Xem camera". */
  showCameraLink?: boolean;
}

export function ToolCallBody({ toolCall, showCameraLink }: ToolCallBodyProps) {
  const [lightboxIndex, setLightboxIndex] = useState<number | null>(null);
  const images = readImages(toolCall.result);
  const joints = readJoints(toolCall.result);
  const servoJoints = readServoJoints(toolCall.result);

  return (
    <div className="space-y-3">
      {/* ---- capture_tool / capture (native edge_vla): what the robot saw */}
      {(toolCall.tool_name === "capture_tool" || toolCall.tool_name === "capture") && images.length > 0 && (
        <>
          <div className="grid grid-cols-2 gap-2">
            {images.map((img, i) => (
              <button
                key={img.id}
                type="button"
                onClick={() => setLightboxIndex(i)}
                className="relative aspect-video rounded-lg overflow-hidden focus-ring cursor-pointer group"
                aria-label={`Mở ảnh ${img.camera === "top" ? "camera trên" : "camera cổ tay"}`}
              >
                {/* eslint-disable-next-line @next/next/no-img-element -- artifact URLs come from the backend at runtime */}
                <img
                  src={img.url}
                  alt={`Ảnh từ ${img.camera === "top" ? "camera trên" : "camera cổ tay"}`}
                  className="w-full h-full object-cover"
                />
                <span className="absolute bottom-1 left-1 data text-[11px] px-1.5 py-0.5 rounded bg-ink-950/80 text-paper-50">
                  {img.camera}
                </span>
              </button>
            ))}
          </div>
          <Lightbox
            images={images}
            index={lightboxIndex}
            onClose={() => setLightboxIndex(null)}
            onIndexChange={setLightboxIndex}
          />
        </>
      )}

      {/* ---- get_robot_state (old spec, joints): the measured numbers */}
      {toolCall.tool_name === "get_robot_state" && joints.length > 0 && <JointTable joints={joints} />}

      {/* ---- get_robot_state (native edge_vla): task/busy state, not joints —
          `edge_vla.server.get_robot_state()` returns
          `{current_task_id, busy, retry_count, current_libero_task_id,
          libero_tasks}`, no per-joint data at all (that's `get_servo_state`,
          below). */}
      {toolCall.tool_name === "get_robot_state" && joints.length === 0 && isRecord(toolCall.result) && (
        <p className="text-[13.5px] text-haze-300 data">
          {toolCall.result.busy === true ? "Đang bận" : toolCall.result.busy === false ? "Rảnh" : null}
          {typeof toolCall.result.current_libero_task_id === "number" && (
            <> · task #{toolCall.result.current_libero_task_id}</>
          )}
          {typeof toolCall.result.retry_count === "number" && toolCall.result.retry_count > 0 && (
            <> · đã thử lại {toolCall.result.retry_count} lần</>
          )}
        </p>
      )}

      {/* ---- get_servo_state (native edge_vla): per-joint pos/vel/force, MuJoCo units */}
      {toolCall.tool_name === "get_servo_state" && servoJoints.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-[12.5px] data">
            <thead>
              <tr className="text-haze-500">
                <th className="text-left font-normal pr-3">Khớp</th>
                <th className="text-right font-normal pr-3">Góc (rad)</th>
                <th className="text-right font-normal pr-3">Vận tốc (rad/s)</th>
                <th className="text-right font-normal">Lực (N·m)</th>
              </tr>
            </thead>
            <tbody>
              {servoJoints.map((j) => (
                <tr key={j.name} className="text-paper-50">
                  <td className="pr-3">{j.name}</td>
                  <td className="text-right pr-3">{j.pos?.toFixed(3) ?? ""}</td>
                  <td className="text-right pr-3">{j.vel?.toFixed(3) ?? ""}</td>
                  <td className="text-right">{j.actuator_force?.toFixed(2) ?? ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* ---- take_action / go_home: the episode progress bar */}
      {(toolCall.tool_name === "take_action" || toolCall.tool_name === "go_home") &&
        (toolCall.episodes ?? []).map((ep) => (
          <EpisodeProgress
            key={ep.id}
            episode={ep}
            hideInstruction={toolCall.tool_name === "go_home"}
            showCameraLink={showCameraLink}
          />
        ))}

      {/* ---- stop_robot: one line, nothing more */}
      {toolCall.tool_name === "stop_robot" && toolCall.result && (
        <p className="text-[13.5px] text-haze-300">
          {typeof toolCall.result.stopped_at === "string" && (
            <>
              Đã dừng lúc{" "}
              <span className="data">
                {new Date(toolCall.result.stopped_at).toLocaleTimeString("vi-VN")}
              </span>
            </>
          )}
          {typeof toolCall.result.reason === "string" && <>, lý do: {String(toolCall.result.reason)}</>}
        </p>
      )}

      {/* ---- reset_error: which flags were cleared */}
      {toolCall.tool_name === "reset_error" && (
        <div className="flex flex-wrap gap-1.5">
          {readStrings(toolCall.result?.cleared_flags).map((flag) => (
            <span key={flag} className="data text-[12px] px-2 py-0.5 rounded bg-ink-700 text-haze-300">
              {flag}
            </span>
          ))}
        </div>
      )}

      {/* ---- health_check: component-by-component verdict */}
      {toolCall.tool_name === "health_check" && isRecord(toolCall.result?.components) && (
        <div className="flex flex-wrap gap-1.5">
          {Object.entries(toolCall.result.components as Record<string, unknown>).map(([name, ok]) => (
            <span
              key={name}
              className={`inline-flex items-center gap-1 h-6 px-2.5 rounded-full text-[12.5px] font-medium ${
                ok === true ? "bg-jade-400/15 text-jade-400" : "bg-halt-500/15 text-halt-500"
              }`}
            >
              {ok === true ? <Check size={11} /> : <TriangleAlert size={11} />}
              {name}
            </span>
          ))}
        </div>
      )}

      {/* ---- arguments, when there is something worth showing.
          `take_action` đã có instruction trong tiêu đề dòng trace,
          `capture_tool` đã có lưới ảnh + nhãn camera phía trên — dòng "Tham
          số" ở đây chỉ lặp lại đúng thông tin đó lần nữa. */}
      {toolArgSummary(toolCall) && toolCall.tool_name !== "take_action" && toolCall.tool_name !== "capture_tool" && (
        <p className="text-[13px] text-haze-500">
          <span className="eyebrow mr-2">Tham số</span>
          <span className="data text-[12.5px]">{toolArgSummary(toolCall)}</span>
        </p>
      )}

      {/* ---- failure detail: message + copyable code */}
      {toolCall.status === "FAILED" && toolCall.error && (
        <div className="flex items-start gap-2 text-halt-500">
          <TriangleAlert size={14} className="mt-0.5 shrink-0" />
          <div className="flex flex-col gap-1 min-w-0">
            <span className="text-[13.5px]">{errorText(toolCall.error)}</span>
            <CopyableCode value={toolCall.error.code} label="Mã lỗi" />
          </div>
        </div>
      )}

      {toolCall.status === "TIMEOUT" && (
        <p className="text-[13.5px] text-amber-400">
          Tool không phản hồi kịp. Thử lại hoặc kiểm tra kết nối của thiết bị Edge.
        </p>
      )}
    </div>
  );
}
