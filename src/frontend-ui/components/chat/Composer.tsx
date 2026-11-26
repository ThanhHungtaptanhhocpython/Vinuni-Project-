"use client";
/* Composer — P04 §6
   Enter sends, Shift+Enter breaks the line, auto-resize up to 6 rows.
   Locked by the §11.2 matrix with the reason shown, placeholder from §11.3.
   Each press generates a fresh Idempotency-Key; a retry reuses the old one. */

import { useState, useRef, useCallback, useEffect } from "react";
import { CircleStop, Mic, MicOff, SendHorizontal } from "lucide-react";
import type { RobotStatus, ConnectionStatus } from "@/stores";
import { toast } from "@/stores";
import { actionAvailability, composerPlaceholder } from "@/lib/robot-actions";
import { DisabledHint } from "@/components/common/DisabledHint";
import { Dialog } from "@/components/common/Dialog";
import { MicSpectrogram } from "@/components/chat/MicSpectrogram";
import { useSpeechRecognition } from "@/hooks";

const MAX_CHARS = 8000;
const SILENCE_CONFIRM_MS = 3000;
/** Khớp với `leading-5` của textarea; py-3 hai đầu cộng thêm 24px. */
const LINE_HEIGHT = 20;
const VERTICAL_PADDING = 24;

interface ComposerProps {
  robotStatus: RobotStatus;
  connectionStatus: ConnectionStatus;
  /** True while a run is in flight — locks the box regardless of robot state. */
  composerLocked: boolean;
  onSend: (text: string, idempotencyKey: string) => void;
  onEmergencyStop?: () => void;
  emergencyStopVisible?: boolean;
  emergencyStopPending?: boolean;
  /** Text pushed in from "Chạy lại" (P09) — filled in, never auto-sent. */
  prefillText?: string;
  /* Ref vào shell ô gõ (không gồm dòng hint "Agent đang xử lý..." phía dưới)
     để HumanInLoopPanel neo đúng mép trên ô gõ — dòng hint xuất hiện/biến
     mất không làm panel tụt theo. */
  shellRef?: React.RefObject<HTMLDivElement | null>;
}

export function Composer({
  robotStatus,
  connectionStatus,
  composerLocked,
  onSend,
  onEmergencyStop,
  emergencyStopVisible = false,
  emergencyStopPending = false,
  prefillText,
  shellRef,
}: ComposerProps) {
  const [text, setText] = useState("");
  const [voiceModalOpen, setVoiceModalOpen] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const {
    supported: micSupported,
    listening,
    finalTranscript,
    interimTranscript,
    start: startMic,
    stop: stopMic,
    resetTranscript,
  } = useSpeechRecognition({
    lang: "vi-VN",
    // Trước đây lỗi (mic bị chặn quyền, không tìm thấy thiết bị…) chỉ nằm im
    // trong state của hook — nút tự tắt mà không ai biết vì sao, trông như
    // "bấm không ăn". Giờ báo thẳng bằng toast, cùng chỗ mọi lỗi khác trong
    // trang này vẫn báo.
    onError: (message) => toast.error(message),
  });
  const spokenText = `${finalTranscript} ${interimTranscript}`.trim().slice(0, MAX_CHARS);

  const availability = actionAvailability("sendMessage", robotStatus, connectionStatus);
  const lockedByState = availability.state !== "enabled";
  const disabled = composerLocked || lockedByState;
  const reason = composerLocked
    ? robotStatus === "BUSY" || robotStatus === "STOPPING"
      ? "Robot đang thực hiện nhiệm vụ. Không gửi lệnh mới cho đến khi xong."
      : "Agent đang xử lý yêu cầu trước đó."
    : availability.state === "disabled"
      ? availability.reason
      : undefined;
  const placeholder = disabled
    ? robotStatus === "BUSY" || robotStatus === "STOPPING"
      ? "Robot đang thực hiện nhiệm vụ…"
      : "Agent đang xử lý yêu cầu trước đó…"
    : composerPlaceholder(robotStatus, connectionStatus);

  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 6 * LINE_HEIGHT + VERTICAL_PADDING)}px`;
  }, [text]);

  // "Chạy lại" pushes text in from outside. Adjusting state during render is the
  // supported way to react to a changed prop without an effect round-trip.
  const [lastPrefill, setLastPrefill] = useState(prefillText);
  if (prefillText !== lastPrefill) {
    setLastPrefill(prefillText);
    if (prefillText) setText(prefillText);
  }

  const handleSend = useCallback(() => {
    const trimmed = text.trim();
    if (disabled || !trimmed) return;
    onSend(trimmed, crypto.randomUUID());
    setText("");
  }, [text, disabled, onSend]);

  const cancelVoice = useCallback(() => {
    stopMic();
    resetTranscript();
    setVoiceModalOpen(false);
  }, [resetTranscript, stopMic]);

  const confirmVoice = useCallback(() => {
    const trimmed = spokenText.trim();
    if (disabled || !trimmed) return;
    stopMic();
    onSend(trimmed, crypto.randomUUID());
    resetTranscript();
    setVoiceModalOpen(false);
  }, [disabled, onSend, resetTranscript, spokenText, stopMic]);

  const openVoiceModal = useCallback(() => {
    if (!micSupported || disabled) return;
    setVoiceModalOpen(true);
    startMic();
  }, [disabled, micSupported, startMic]);

  useEffect(() => {
    if (!voiceModalOpen || disabled || !spokenText) return;
    const timeoutId = window.setTimeout(confirmVoice, SILENCE_CONFIRM_MS);
    return () => window.clearTimeout(timeoutId);
  }, [confirmVoice, disabled, spokenText, voiceModalOpen]);

  const remaining = MAX_CHARS - text.length;

  return (
    <div className="p-3 bg-ink-900/40 backdrop-blur-md shrink-0 relative z-10">
      {/* Nền/viền/bóng nằm ở `.composer-shell` trong globals.css chứ không phải
          ở đây: chúng phải đổi theo theme, mà `bg-white/5` thì không — xem ghi
          chú tại recipe. Hình dạng (bo góc) vẫn là utility. */}
      <div ref={shellRef} className="relative rounded-3xl composer-shell" title={reason}>
        <textarea
          ref={textareaRef}
          value={text}
          onChange={(e) => setText(e.target.value.slice(0, MAX_CHARS))}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              handleSend();
            }
          }}
          placeholder={placeholder}
          rows={1}
          readOnly={disabled}
          aria-label="Ra lệnh cho robot"
          aria-disabled={disabled}
          aria-describedby={reason ? "composer-lock-reason" : undefined}
          /* `block`: textarea mặc định là inline-block nên nó ngồi trên đường
             baseline và chừa một khoảng trống vài px ngay dưới, làm khung bao
             cao hơn chính nó — nút định vị theo đáy khung sẽ lệch xuống.

             `leading-5` cho chiều cao mỗi dòng cố định 20px thay vì phụ thuộc
             line-height "normal" của từng font; phép tính auto-resize 6 dòng
             dựa vào con số này.

             `pr-14` chừa chỗ cho nút, thiếu thì dòng cuối chui xuống dưới nút
             và mất mấy chữ. `bg-transparent` vì nền và bo góc do khung bao vẽ.

             `px-5` chứ không phải `px-4`: khung bo tròn hẳn nên mép trái là một
             đường cong, chữ sát quá sẽ đâm vào chỗ cong. */
          className={`block w-full bg-transparent px-5 py-3 pr-24 text-[15px] leading-5 text-paper-50 placeholder:text-haze-500 resize-none focus:outline-none ${
            disabled ? "cursor-not-allowed" : ""
          }`}
        />

        {remaining < 500 && (
          /* Bên TRÁI nút, không phải góc phải — góc đó nút chiếm rồi. Cùng
             `bottom` và cùng chiều cao với nút, nên hai thứ luôn chung một
             đường ngang mà không phải tính riêng cho cỡ chữ 11px. */
          <span className="absolute bottom-1 right-24 h-9 flex items-center data text-[12px] text-haze-500">
            {remaining}
          </span>
        )}

        {/* Thẻ bọc luôn tồn tại và mang định vị. Để `DisabledHint` tự bọc một
            nút đã `absolute` thì thẻ span của nó rộng 0, và tooltip lý do khoá
            không hover được — đúng thứ `DisabledHint` sinh ra để sửa.

            `flex` KHÔNG phải để xếp bố cục mà để bỏ dòng nền: `<button>` là
            inline-block nên trong một thẻ bọc thường nó ngồi trên baseline và
            chừa ~4px descender phía DƯỚI. Thẻ bọc vì thế cao ~40px trong khi
            nút chỉ 36px, và `bottom-*` đo tới đáy thẻ bọc chứ không tới đáy
            nút — nút bị đội lên đúng phần dôi ra đó. Đây chính là lỗi đang
            thấy, và cũng đúng cái bẫy mà `block` trên textarea phía trên đã
            phải xử lý. `flex` tạo flex formatting context, không có baseline,
            thẻ bọc cao đúng 36px.

            Số 4px (`bottom-1`/`right-1`) là phép tính, không phải ước lượng:
            khung một dòng cao 20 + 24 = 44px, nút 36px → (44−36)/2 = 4. Tâm
            nút nằm đúng đường giữa khung. Cùng số đó theo chiều ngang đặt tâm
            nút cách mép phải 22px = bán kính của đầu bo, nên nút và đầu khung
            đồng tâm; khung giãn nhiều dòng thì nút tụt xuống ôm góc dưới-phải,
            vẫn đồng tâm với góc đó. */}
        {listening && !voiceModalOpen && (
          /* Neo theo mép trên của CẢ KHUNG (shellRef), không phải theo nút
             mic — khung cao dần khi gõ nhiều dòng (tới 6 dòng), nếu neo theo
             nút (luôn ghim đáy-phải) thì "ngay trên nút" sẽ lọt vào giữa
             vùng chữ thay vì nằm hẳn phía trên, che mất mấy dòng cuối. Neo
             theo khung thì dù khung cao bao nhiêu, popup vẫn luôn nằm trên
             đỉnh, không bao giờ đè chữ. */
          <div className="absolute bottom-full right-3 mb-2 px-2 py-1.5 rounded-xl bg-ink-900 border border-white/10 shadow-lg">
            <MicSpectrogram active={listening} />
          </div>
        )}

        <div className="absolute bottom-1 right-1 flex items-center gap-1">
          {/* Luôn hiện nút mic, kể cả khi trình duyệt không hỗ trợ — trước đây
             ẩn hẳn nút khiến người dùng tưởng bấm "không ăn" hoặc không biết
             tính năng có tồn tại. Vì voice flow mới tự gửi lệnh, nút mic cũng
             chịu cùng trạng thái khoá với nút Gửi. */}
          <div className="relative">
            <DisabledHint
              reason={
                !micSupported
                  ? "Trình duyệt này không hỗ trợ nhận dạng giọng nói. Dùng Chrome, Edge hoặc Safari bản mới."
                  : reason
              }
            >
              <button
                type="button"
                onClick={openVoiceModal}
                disabled={!micSupported || disabled}
                title={micSupported && !reason ? "Thu giọng nói" : undefined}
                aria-label="Thu giọng nói"
                className={`w-9 h-9 rounded-full flex items-center justify-center shrink-0 transition-colors duration-[120ms] focus-ring cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed ${
                  voiceModalOpen
                    ? "bg-red-500 text-white animate-pulse"
                    : "bg-white/5 text-haze-500 hover:text-paper-50"
                }`}
              >
                {voiceModalOpen ? <MicOff size={16} /> : <Mic size={16} />}
              </button>
            </DisabledHint>
          </div>
          {emergencyStopVisible ? (
            <button
              type="button"
              onClick={onEmergencyStop}
              disabled={emergencyStopPending || !onEmergencyStop}
              title="Dừng khẩn cấp"
              aria-label="Dừng khẩn cấp"
              className="w-9 h-9 rounded-full bg-red-600 flex items-center justify-center text-white shrink-0 hover:bg-red-500 transition-colors duration-[120ms] disabled:opacity-60 disabled:cursor-not-allowed focus-ring cursor-pointer"
            >
              <CircleStop size={18} />
            </button>
          ) : (
            <DisabledHint reason={reason}>
              <button
                type="button"
                onClick={handleSend}
                disabled={disabled || !text.trim()}
                title={reason ? undefined : "Gửi (Enter)"}
                aria-label="Gửi lệnh"
                className="w-9 h-9 rounded-full bg-arc-500 flex items-center justify-center text-white shrink-0 hover:bg-arc-hover transition-colors duration-[120ms] disabled:opacity-40 disabled:cursor-not-allowed focus-ring cursor-pointer"
              >
                <SendHorizontal size={18} />
              </button>
            </DisabledHint>
          )}
        </div>
      </div>

      {/* Lý do bị khoá, hiện thẳng chứ không chỉ trong tooltip */}
      {reason && (
        <p id="composer-lock-reason" className="mt-1.5 text-[13px] text-haze-500">
          {reason}
          {text.trim() && " Câu lệnh của bạn được giữ nguyên, gửi được ngay khi robot rảnh."}
        </p>
      )}

      <Dialog
        open={voiceModalOpen}
        onClose={cancelVoice}
        title={listening ? "Đang nghe..." : "Thu giọng nói"}
        disableOutsideClose
        surface="solid"
        className="max-w-[592px] min-h-[440px]"
        footer={
          <>
            <button type="button" onClick={cancelVoice} className="btn-ghost focus-ring">
              Hủy
            </button>
            <button
              type="button"
              onClick={confirmVoice}
              disabled={disabled || !spokenText}
              className="btn-primary focus-ring disabled:opacity-40 disabled:cursor-not-allowed"
            >
              Xác nhận
            </button>
          </>
        }
      >
        <div className="min-h-[288px] flex flex-col items-center justify-center gap-8">
          <div
            className={`w-20 h-20 rounded-full flex items-center justify-center shrink-0 ${
              listening ? "bg-red-600 text-white animate-pulse" : "bg-ink-800 text-haze-300"
            }`}
            aria-hidden="true"
          >
            <Mic size={38} />
          </div>

          {spokenText && (
            <p className="max-w-[440px] text-center text-[16px] leading-6 text-paper-50 whitespace-pre-wrap break-words">
              {finalTranscript}
              {interimTranscript && (
                <span className="text-haze-400">
                  {finalTranscript ? " " : ""}
                  {interimTranscript}
                </span>
              )}
            </p>
          )}
        </div>
      </Dialog>
    </div>
  );
}
