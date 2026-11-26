import type { RunSource, ToolCall, ToolStatus } from "@/stores";
import { formatAbsolute } from "@/lib/format";
import { StatusBadge } from "@/components/common/StatusBadge";
import { describeToolCall } from "@/lib/tool-display";

export type TimelineEntry =
  | { kind: "run_started"; at: string; source: RunSource | string }
  /* Không kèm model/token — DOC "không expose technical details": Timeline
     kể "agent đã phân tích xong", không phải log dev. */
  | { kind: "llm_call"; at: string; duration_ms: number }
  | { kind: "tool_call"; at: string; toolCall: ToolCall }
  | {
      kind: "episode";
      at: string;
      episodeId: string;
      /** `EpisodeOutcome` khi khớp bảng đã biết; chuỗi thô nếu backend trả giá
          trị lạ — thà hiện tên kỹ thuật còn hơn nuốt mất (cùng quy ước với
          `lib/tool-display.ts`). `undefined` = episode chưa `.completed`. */
      outcome?: string;
      step?: number;
      maxSteps?: number;
      durationMs?: number;
    }
  | {
      kind: "verification";
      at: string;
      /** `undefined` = mới có `.started`, chưa `.completed`. */
      status?: string;
      /** Chỉ có khi backend thực sự trả `reason` không rỗng. */
      reason?: string;
    }
  | { kind: "robot_state"; at: string; status: string }
  | { kind: "run_completed"; at: string; ok: boolean; outcome: string };

interface TimelineViewProps {
  entries: TimelineEntry[];
}

const TOOL_STATUS_LABEL: Record<ToolStatus, string> = {
  PENDING: "Chờ",
  RUNNING: "Đang chạy",
  WAITING_APPROVAL: "Chờ xác nhận",
  SUCCEEDED: "Xong",
  FAILED: "Lỗi",
  CANCELLED: "Huỷ",
  TIMEOUT: "Hết hạn",
};

function StatusChip({ label, tone }: { label: string; tone: string }) {
  return (
    <span
      className={`inline-flex items-center h-5 px-2 rounded-full text-[11.5px] font-medium uppercase tracking-[0.06em] ${tone}`}
    >
      {label}
    </span>
  );
}

function ToolStatusChip({ status }: { status: ToolStatus }) {
  const tone =
    status === "SUCCEEDED"
      ? "bg-jade-400/15 text-jade-400"
      : status === "FAILED"
        ? "bg-halt-500/15 text-halt-500"
        : status === "RUNNING"
          ? "bg-arc-500/15 text-arc-400"
          : "bg-ink-700 text-haze-500";
  return <StatusChip label={TOOL_STATUS_LABEL[status] ?? status} tone={tone} />;
}

/* Cùng 5 giá trị `EpisodeOutcome` mà `EpisodeProgress.tsx` đã dùng cho thanh
   tiến độ chi tiết trong chat — đây là bản NHÃN NGẮN cho một chip, không phải
   câu đầy đủ kèm số bước, nên không tái dùng thẳng bảng ở đó. */
const EPISODE_OUTCOME: Record<string, { label: string; tone: string }> = {
  SUCCESS: { label: "Thành công", tone: "bg-jade-400/15 text-jade-400" },
  STOPPED: { label: "Đã dừng", tone: "bg-ink-700 text-haze-500" },
  TIMEOUT: { label: "Quá thời gian", tone: "bg-amber-400/15 text-amber-400" },
  MAX_STEPS: { label: "Đạt giới hạn bước", tone: "bg-amber-400/15 text-amber-400" },
  SAFETY_ABORT: { label: "Dừng an toàn", tone: "bg-halt-500/15 text-halt-500" },
};

/* Cùng 6 giá trị `VerificationStatus` mà `MessageStatusLine.tsx` (Phase 2)
   dùng cho Chat — Timeline lặp lại đúng ý nghĩa đó, không phải một trục mới. */
const VERIFICATION_LABEL: Record<string, { label: string; tone: string }> = {
  VERIFIED_SUCCESS: { label: "Xác minh thành công", tone: "text-jade-400" },
  VERIFIED_FAILED: { label: "Xác minh thất bại", tone: "text-halt-500" },
  UNKNOWN: { label: "Chưa thể xác nhận", tone: "text-amber-400" },
  UNAVAILABLE: { label: "Xác minh không khả dụng", tone: "text-amber-400" },
  NOT_STARTED: { label: "Chưa xác minh", tone: "text-haze-500" },
  VERIFYING: { label: "Đang xác minh", tone: "text-arc-400" },
};

const ROBOT_STATE_LABEL: Record<string, string> = {
  IDLE: "Robot sẵn sàng",
  BUSY: "Robot đang chạy",
  STOPPING: "Robot đang dừng",
  ERROR: "Robot báo lỗi",
  DISCONNECTED: "Robot mất kết nối",
};

const RUN_COMPLETED_LABEL: Record<string, { label: string; tone: string }> = {
  COMPLETED: { label: "Hoàn thành", tone: "text-jade-400" },
  FAILED: { label: "Task thất bại", tone: "text-halt-500" },
  CANCELLED: { label: "Đã huỷ", tone: "text-haze-300" },
  TIMEOUT: { label: "Quá thời gian", tone: "text-amber-400" },
};

function EntryLabel({ entry }: { entry: TimelineEntry }) {
  switch (entry.kind) {
    case "run_started":
      return (
        <>
          <span className="text-paper-50">Bắt đầu lượt chạy</span>
          <StatusBadge kind="source" value={entry.source} />
        </>
      );
    case "llm_call":
      return (
        <>
          <span className="text-paper-50">Đã phân tích</span>
          <span className="data text-[12.5px] text-haze-500">{entry.duration_ms}ms</span>
        </>
      );
    case "tool_call":
      return (
        <>
          <span className="text-paper-50" title={entry.toolCall.tool_name}>
            {describeToolCall(entry.toolCall)}
          </span>
          <ToolStatusChip status={entry.toolCall.status} />
          {entry.toolCall.duration_ms != null && (
            <span className="data text-[12.5px] text-haze-500">{entry.toolCall.duration_ms}ms</span>
          )}
        </>
      );
    case "episode": {
      const outcome = entry.outcome ? EPISODE_OUTCOME[entry.outcome] : undefined;
      return (
        <>
          <span className="text-paper-50">
            {outcome ? "Hoàn thành thực hiện" : "Bắt đầu thực hiện"}
          </span>
          {outcome ? (
            <StatusChip label={outcome.label} tone={outcome.tone} />
          ) : (
            <StatusChip label="Đang chạy" tone="bg-arc-500/15 text-arc-400" />
          )}
          {entry.step != null && entry.maxSteps != null && (
            <span className="data text-[12.5px] text-haze-500">
              {entry.step}/{entry.maxSteps} bước
            </span>
          )}
          {entry.durationMs != null && (
            <span className="data text-[12.5px] text-haze-500">{entry.durationMs}ms</span>
          )}
        </>
      );
    }
    case "verification": {
      const known = entry.status ? VERIFICATION_LABEL[entry.status] : undefined;
      return (
        <>
          <span className={known?.tone ?? "text-arc-400"}>
            {known?.label ?? "Đang xác minh"}
          </span>
          {entry.reason && <span className="text-[13.5px] text-haze-300">{entry.reason}</span>}
        </>
      );
    }
    case "robot_state":
      return <span className="text-paper-50">{ROBOT_STATE_LABEL[entry.status] ?? entry.status}</span>;
    case "run_completed": {
      const known = RUN_COMPLETED_LABEL[entry.outcome] ?? {
        label: entry.ok ? "Hoàn thành" : "Kết thúc lỗi",
        tone: entry.ok ? "text-jade-400" : "text-halt-500",
      };
      return (
        <>
          <span className={known.tone}>{known.label}</span>
        </>
      );
    }
  }
}

export function TimelineView({ entries }: TimelineViewProps) {
  return (
    <ol className="space-y-3">
      {entries.map((entry, index) => (
        <li key={`${entry.kind}-${entry.at}-${index}`} className="flex gap-3">
          <div className="flex flex-col items-center pt-1">
            <span className="w-2 h-2 rounded-full bg-arc-500 shrink-0" />
            {index < entries.length - 1 && <span className="w-px flex-1 bg-[var(--line)] mt-1" />}
          </div>
          <div className="min-w-0 flex-1 pb-3">
            <div className="flex flex-wrap items-center gap-2">
              <EntryLabel entry={entry} />
            </div>
            <p className="eyebrow mt-1 normal-case tracking-normal">{formatAbsolute(entry.at)}</p>
          </div>
        </li>
      ))}
    </ol>
  );
}
