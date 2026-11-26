"use client";
/* TraceFlow — dòng thời gian phẳng kiểu Claude Code (C1), thay `AgentTrace`.
 *
 * Bỏ hẳn mọi cơ chế cũ của `AgentTrace` (panel-inset lồng trong bong bóng,
 * accordion một-hàng-một-lúc, cửa sổ trượt 2 dòng, gộp phase, badge tam giác
 * hai màu, tự-mở-hàng-đang-chạy) — mỗi cơ chế có lý do riêng nhưng cộng lại
 * người dùng phải học một giao diện riêng mới đọc được. Thay bằng đúng một
 * quy tắc: render thẳng `message.blocks` (hay `runBlocks` lúc đang chạy) THEO
 * ĐÚNG THỨ TỰ đã đến — chữ agent nói xen kẽ dòng `●` hành động, dòng `⎿` tóm
 * tắt kết quả, bấm vào mở chi tiết. Không còn dòng giai đoạn ("Đang lập kế
 * hoạch"…) riêng — `ThinkingLine` đã lấp khoảng chờ trước khối đầu tiên, và
 * mọi khối SAU đó đều là nội dung thật (chữ hoặc hành động), không phải suy
 * luận nội bộ (§13.2). */

import { useState } from "react";
import { ChevronDown } from "lucide-react";
import type { ToolCall, TraceBlock } from "@/stores";
import { useTicker } from "@/hooks/useTicker";
import { formatDurationMs } from "@/lib/utils";
import { TOOL_ICON, DEFAULT_TOOL_ICON, describeToolCall, summarizeToolResult, isTraceHiddenTool } from "@/lib/trace";
import { TOOL_STATUS_STYLE } from "./ToolCard";
import { ToolCallBody, readImages } from "./ToolCallBody";
import { renderContent } from "@/lib/markdown-inline";

/** Dựng một bản `TraceBlock[]` GẦN ĐÚNG cho tin nhắn lịch sử chưa có
 * `blocks` (tải từ `GET /sessions/{id}/messages`, hoặc lượt chạy từ trước khi
 * field này tồn tại) — tương thích ngược, không phải nguồn sự thật.
 *
 * Lịch sử chỉ còn ĐÚNG MỘT chuỗi `content` (câu trả lời cuối) cho mỗi tin
 * nhắn — backend không lưu narration giữa chừng (`token.delta` cố ý không
 * ghi DB, xem `agents/trace_repo.py`). Không có cách nào biết câu đó đứng ở
 * đâu GIỮA các tool call, nên xếp nó SAU cùng — đúng bố cục cũ (`AgentTrace`
 * luôn đứng trên, `content` luôn đứng dưới), không bịa một vị trí xen kẽ
 * không có căn cứ. */
export function legacyBlocksOf(content: string, toolCalls: ToolCall[] | undefined): TraceBlock[] {
  const blocks: TraceBlock[] = (toolCalls ?? [])
    .filter((call) => !isTraceHiddenTool(call.tool_name))
    .map((call) => ({ kind: "tool" as const, id: call.id, call }));
  if (content.trim()) {
    blocks.push({ kind: "text", id: "legacy-text", content, sealed: true });
  }
  return blocks;
}

function TextBlockRow({ block }: { block: Extract<TraceBlock, { kind: "text" }> }) {
  if (!block.content && block.sealed) return null;
  return (
    <div>
      {renderContent(block.content)}
      {!block.sealed && (
        <span className="inline-block w-1.5 h-4 align-text-bottom bg-arc-400 rounded-full ml-0.5 animate-pulse-dot" />
      )}
    </div>
  );
}

function ToolBlockRow({
  call,
  open,
  onToggle,
  showCameraLink,
}: {
  call: ToolCall;
  open: boolean;
  onToggle: () => void;
  showCameraLink?: boolean;
}) {
  const running = call.status === "RUNNING" || call.status === "PENDING";
  const now = useTicker(running);
  const status = TOOL_STATUS_STYLE[call.status] ?? TOOL_STATUS_STYLE.PENDING;
  const ToolIcon = TOOL_ICON[call.tool_name] ?? DEFAULT_TOOL_ICON;
  const label = describeToolCall(call);
  const resultLine = summarizeToolResult(call);
  const bodyId = `trace-flow-body-${call.id}`;

  const isCapture = call.tool_name === "capture" || call.tool_name === "capture_tool";
  const previewImages = isCapture ? readImages(call.result) : [];

  const elapsed = running
    ? call.started_at !== undefined && now > call.started_at
      ? formatDurationMs(now - call.started_at)
      : ""
    : formatDurationMs(call.duration_ms);

  return (
    <div>
      <div className="flex items-start gap-2 px-1 py-0.5">
        <status.Icon size={13} className={`shrink-0 mt-0.5 ${status.iconClass}`} />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-1.5">
            <ToolIcon size={12} className="shrink-0 text-haze-300" />
            <span title={label} className="truncate text-paper-50">
              {label}
            </span>
          </div>
          <button
            type="button"
            onClick={onToggle}
            aria-expanded={open}
            aria-controls={bodyId}
            className="mt-0.5 w-full flex items-center gap-1.5 text-left focus-ring rounded cursor-pointer text-haze-500"
          >
            <span className="shrink-0 select-none">⎿</span>
            {previewImages.length > 0 && (
              <span className="flex shrink-0 -space-x-1.5">
                {previewImages.map((img) => (
                  // eslint-disable-next-line @next/next/no-img-element -- artifact URLs come from the backend at runtime
                  <img
                    key={img.id}
                    src={img.url}
                    alt={`Ảnh từ ${img.camera === "top" ? "camera trên" : "camera cổ tay"}`}
                    className="w-5 h-5 rounded object-cover ring-1 ring-ink-900"
                  />
                ))}
              </span>
            )}
            {resultLine && (
              <span className={`truncate text-[13.5px] ${call.status === "FAILED" ? "text-halt-500" : ""}`}>
                {resultLine}
              </span>
            )}
            {elapsed && (
              <span className={`data text-[12px] shrink-0 ml-auto ${running ? "text-arc-400" : "text-haze-500"}`}>
                {elapsed}
              </span>
            )}
            <ChevronDown
              size={12}
              className={`shrink-0 text-haze-500 transition-transform duration-200 ${open ? "rotate-180" : ""}`}
            />
          </button>
        </div>
      </div>

      {open && (
        <div id={bodyId} className="mt-1.5 mb-2 ml-6 mr-1">
          <ToolCallBody toolCall={call} showCameraLink={showCameraLink} />
        </div>
      )}
    </div>
  );
}

interface TraceFlowProps {
  blocks: TraceBlock[];
  showCameraLink?: boolean;
}

export function TraceFlow({ blocks, showCameraLink }: TraceFlowProps) {
  const [openIds, setOpenIds] = useState<Set<string>>(() => new Set());

  if (blocks.length === 0) return null;

  return (
    <div className="space-y-1.5">
      {blocks.map((block) =>
        block.kind === "text" ? (
          <TextBlockRow key={block.id} block={block} />
        ) : (
          <ToolBlockRow
            key={block.id}
            call={block.call}
            showCameraLink={showCameraLink}
            open={openIds.has(block.id)}
            onToggle={() =>
              setOpenIds((prev) => {
                const next = new Set(prev);
                if (next.has(block.id)) next.delete(block.id);
                else next.add(block.id);
                return next;
              })
            }
          />
        ),
      )}
    </div>
  );
}
