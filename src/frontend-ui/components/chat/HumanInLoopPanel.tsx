"use client";
/* HumanInLoopPanel — đính trên khung chat, căn giữa, style khớp Dialog.
   Người dùng PHẢI xác nhận trước khi agent tiếp tục.

   Quyết định của người dùng đi qua `submitHitlDecision` (lib/api/hitl.ts) —
   KHÔNG bao giờ chỉ dọn state cục bộ rồi coi như đã xong: panel chỉ đóng lại
   khi backend (hoặc mock, ở chế độ mock) xác nhận đã nhận quyết định. Nếu
   backend từ chối (stale request, run không còn chờ...), panel ở lại và hiện
   lỗi — không có đường "coi như thành công" cho một request đã thất bại. */

import { useEffect, useRef } from "react";
import { AlertTriangle, RotateCw, HelpCircle, ChevronRight, Square, X } from "lucide-react";
import { useHumanInLoopStore, toast, type HumanInLoopRequest } from "@/stores";
import { stopMockRobot } from "@/lib/mock/robots";
import { USE_MOCK } from "@/lib/api/config";
import { stopRobot } from "@/lib/api/robots";
import { submitHitlDecision } from "@/lib/api/hitl";
import { errorText, toErrorObject } from "@/lib/errors";

interface HumanInLoopPanelProps {
  /** Robot THẬT của session đang mở — cùng nguồn duy nhất mà trang điều khiển
      đã tra ra (`robot.id`), không tự dò lại theo cách khác. Trước đây panel
      hardcode `"robot-001"`: session gắn robot khác thì nút Dừng ở đây dừng
      NHẦM robot trong khi cánh tay thật đang di chuyển vẫn tiếp tục. */
  robotId: string;
}

const KIND_META: Record<
  HumanInLoopRequest["kind"],
  {
    icon: typeof AlertTriangle;
    label: string;
    iconClass: string;
  }
> = {
  retry: {
    icon: RotateCw,
    label: "Agent thử lại nhiều lần",
    iconClass: "text-amber-400",
  },
  risk: {
    icon: AlertTriangle,
    label: "Hành động có rủi ro",
    iconClass: "text-halt-500",
  },
  clarify: {
    icon: HelpCircle,
    label: "Cần xác nhận",
    iconClass: "text-arc-400",
  },
};

export function HumanInLoopPanel({ robotId }: HumanInLoopPanelProps) {
  const request = useHumanInLoopStore((s) => s.request);
  const setRequest = useHumanInLoopStore((s) => s.setRequest);
  const status = useHumanInLoopStore((s) => s.status);
  const submitError = useHumanInLoopStore((s) => s.error);
  const setSubmitting = useHumanInLoopStore((s) => s.setSubmitting);
  const setSubmitError = useHumanInLoopStore((s) => s.setSubmitError);

  // Chặn double-submit chắc chắn hơn so với chỉ `disabled` trên nút: React có
  // thể xử lý hai lần click đã xếp hàng trước khi re-render kịp vô hiệu nút.
  const submittingRef = useRef(false);

  useEffect(() => {
    submittingRef.current = false;
  }, [request?.requestId]);

  if (!request) return null;

  const meta = KIND_META[request.kind];
  const Icon = meta.icon;
  const pending = status === "submitting";

  const submit = async (
    kind: "retry" | "answer" | "approve" | "reject" | "cancel",
    answer?: { option_id: string },
  ) => {
    if (submittingRef.current) return;
    submittingRef.current = true;
    setSubmitting();
    try {
      await submitHitlDecision(request.runId, request.requestId, { kind, answer });
      setRequest(null);
    } catch (err) {
      submittingRef.current = false;
      setSubmitError(toErrorObject(err));
    }
  };

  const handleClose = () => submit("cancel");

  const handleStop = async () => {
    if (submittingRef.current) return;
    try {
      if (USE_MOCK) {
        await stopMockRobot(robotId);
      } else {
        await stopRobot(robotId);
      }
      toast.success("Đã dừng.");
    } catch {
      toast.error("Không thể dừng.");
    }
    /* Quyết định HITL đi kèm — best-effort: robot đã được lệnh dừng ở trên
       rồi (phản hồi chính đã hiện qua toast), lượt chạy phía backend có thể
       tự kết thúc do robot dừng nên request có thể đã hết hiệu lực trước khi
       gọi tới đây — không sao, không cần báo lỗi riêng cho phần này. */
    try {
      await submitHitlDecision(request.runId, request.requestId, {
        kind: request.kind === "risk" ? "reject" : "cancel",
      });
    } catch {
      /* xem ghi chú ở trên */
    }
    setRequest(null);
  };

  const handleClarifyChoice = (optionId: string) => {
    void submit("answer", { option_id: optionId });
  };

  return (
    <div className="pointer-events-auto">
      {/* Panel — style khớp ô gõ chat: cùng `.composer-shell`, bo tròn lớn như
          textarea (rounded-3xl) thay vì rounded-xl để mềm hơn và đồng điệu. */}
      <div
        className="composer-shell rounded-3xl relative max-w-[520px] w-full shrink-0 max-h-[60vh] overflow-y-auto outline-none"
        role="dialog"
        aria-modal="true"
        aria-labelledby="hil-title"
      >
        {/* Header — đồng điệu ô gõ chat: cùng tone, không có thanh nền riêng */}
        <div className="flex items-center justify-between px-5 pt-4 pb-3 sticky top-0 z-10">
          <div className="flex items-center gap-2.5">
            <Icon size={18} className={meta.iconClass} />
            <h2 id="hil-title" className="title">{meta.label}</h2>
          </div>
          <button
            type="button"
            onClick={handleClose}
            disabled={pending}
            aria-label="Đóng"
            className="p-1.5 rounded-lg text-haze-300 hover:text-paper-50 hover:bg-ink-700 transition-colors duration-[120ms] focus-ring cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
          >
            <X size={18} />
          </button>
        </div>

        {/* Body */}
        <div className="p-5">
          <p className="body-text text-haze-300">
            {request.kind === "retry" && (
              <>
                Agent đã thử lại <strong className="text-paper-50">{request.retryCount} lần</strong>{" "}
                liên tiếp và đều thất bại. Lỗi: <em>{request.lastError}</em>
              </>
            )}
            {request.kind === "risk" && (
              <>
                Tool <strong className="text-paper-50">{request.toolName}</strong> có rủi ro:{" "}
                <em>{request.riskReason}</em>
              </>
            )}
            {request.kind === "clarify" && <>{request.question}</>}
          </p>

          {submitError && (
            <p className="mt-3 text-[13.5px] text-halt-500">{errorText(submitError)}</p>
          )}

          {/* Clarification options */}
          {request.kind === "clarify" && (
            <ul className="mt-4 space-y-2">
              {request.options.map((option) => (
                <li key={option.id}>
                  <button
                    type="button"
                    onClick={() => handleClarifyChoice(option.id)}
                    disabled={pending}
                    className="w-full flex items-center justify-between px-3 py-2.5 rounded-lg text-left text-[14px] text-paper-50 transition-colors duration-[120ms] focus-ring cursor-pointer bg-ink-900 border border-[var(--line)] hover:bg-ink-800 hover:border-[var(--line-strong)] disabled:opacity-40 disabled:cursor-not-allowed"
                  >
                    <span>{option.label}</span>
                    <ChevronRight size={14} className="text-haze-500 shrink-0" />
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        {/* Footer — y hệt Dialog */}
        {request.kind !== "clarify" && (
          <div className="px-5 pt-3 pb-4 flex justify-end gap-2 sticky bottom-0 z-10">
            <button
              type="button"
              onClick={handleStop}
              disabled={pending}
              className="h-9 px-4 rounded-lg bg-halt-600 text-white text-[14px] font-medium hover:bg-halt-700 transition-colors duration-[120ms] focus-ring cursor-pointer flex items-center gap-1.5 disabled:opacity-40 disabled:cursor-not-allowed"
            >
              <Square size={12} />
              Dừng
            </button>
            <button
              type="button"
              onClick={() => submit(request.kind === "retry" ? "retry" : "approve")}
              disabled={pending}
              className="btn-primary focus-ring flex items-center gap-1.5 disabled:opacity-40 disabled:cursor-not-allowed"
            >
              <ChevronRight size={14} />
              {pending ? "Đang gửi…" : request.kind === "retry" ? "Thử tiếp" : "Tiếp tục"}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
