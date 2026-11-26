"use client";
/* Hộp thoại "Thêm robot" — ghép nối thêm một cánh tay vào tài khoản.

   Hai trường, không hơn: TÊN để người vận hành gọi, ID để hệ thống định
   danh. LOẠI TAY không còn do người dùng chọn — gõ xong ID là tự tra cứu lên
   Cloud (`GET /edge/registry/{id}`) để lấy loại tay Edge đã tự khai khi nó
   đăng ký lúc khởi động (JWT ký bằng EDGE_JWT_SECRET). Bắt người dùng tự chọn
   loại tay là để họ có thể ghép sai — Cloud biết chắc chắn hơn họ.

   Lỗi hiện tại chỗ gây ra nó, không phải một dòng đỏ chung ở cuối form
   (MASTER §14.1): sai ID / chưa tìm thấy Edge thì câu nhắc nằm ngay dưới ô
   ID. Riêng lỗi từ backend (trùng ID, mất mạng) không thuộc về ô nào nên nằm
   ở chân hộp thoại.

   Hộp thoại KHÔNG tự đóng khi lệnh hỏng — đóng đi thì người dùng mất luôn cái
   vừa gõ và không biết vì sao. */

import { useState, useCallback, useEffect, useRef } from "react";

import { Dialog } from "@/components/common/Dialog";
import { addRobot, fetchEdgeRegistry, type EdgeRegistryEntry } from "@/lib/api/robots";
import { ROBOT_ID_PATTERN, ROBOT_ID_HINT, robotModelLabel } from "@/lib/robot/models";
import { errorText, toErrorObject } from "@/lib/errors";
import type { Robot } from "@/stores";

interface AddRobotDialogProps {
  open: boolean;
  onClose: () => void;
  /** Gọi sau khi backend xác nhận đã thêm — chỗ gọi tự đọc lại danh sách. */
  onAdded: (robot: Robot) => void;
}

const FIELD_CLASS =
  "mt-1.5 w-full h-10 px-3 bg-ink-900 border rounded-lg text-[14px] text-paper-50 placeholder:text-haze-500 focus:outline-none focus-ring";

/* Đợi người dùng gõ xong rồi mới tra cứu — tránh bắn một request mỗi ký tự. */
const LOOKUP_DEBOUNCE_MS = 400;

type LookupState =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "found"; entry: EdgeRegistryEntry }
  | { status: "not_found" }
  | { status: "error"; message: string };

export function AddRobotDialog({ open, onClose, onAdded }: AddRobotDialogProps) {
  const [name, setName] = useState("");
  const [robotId, setRobotId] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [lookup, setLookup] = useState<LookupState>({ status: "idle" });
  /* Chỉ soi lỗi một ô SAU KHI người dùng rời ô đó hoặc đã bấm Thêm. Bắt lỗi
     ngay từ ký tự đầu tiên thì ô nào cũng đỏ trong lúc đang gõ dở. */
  const [touched, setTouched] = useState<{ name: boolean; id: boolean }>({ name: false, id: false });

  /* Mở lại hộp thoại là một lần ghép nối mới, không phải lần trước gõ dở.
     Reset ngay trong lúc render khi bắt được cạnh đóng→mở — theo đúng khuyến
     nghị của React cho việc "điều chỉnh state khi prop đổi" thay vì dùng
     effect, tránh một vòng render thừa. */
  const [prevOpen, setPrevOpen] = useState(open);
  if (open !== prevOpen) {
    setPrevOpen(open);
    if (open) {
      setName("");
      setRobotId("");
      setFormError(null);
      setLookup({ status: "idle" });
      setTouched({ name: false, id: false });
    }
  }

  const idFormatError = !ROBOT_ID_PATTERN.test(robotId.trim()) ? ROBOT_ID_HINT : null;

  /* Tra cứu Edge mỗi khi ID hợp lệ định dạng đổi — huỷ request cũ (bằng cờ
     `cancelled`) khi ID đổi tiếp hoặc hộp thoại đóng, để một phản hồi chậm
     không ghi đè kết quả của lần gõ sau. */
  const lookupSeq = useRef(0);
  useEffect(() => {
    if (!open) return;
    const trimmed = robotId.trim();
    // Sai định dạng thì không tra cứu — `idError` đã ưu tiên hiện lỗi định
    // dạng bất kể `lookup` đang giữ giá trị gì từ lần gõ hợp lệ trước đó, nên
    // không cần đặt lại state `lookup` ở đây.
    if (idFormatError) return;
    // Mọi setState nằm trong callback của setTimeout/fetch, không gọi đồng bộ
    // ngay thân effect (react-hooks/set-state-in-effect) — "loading" chỉ hiện
    // sau khi hết thời gian debounce, không phải ngay lúc gõ.
    const timer = setTimeout(() => {
      const seq = ++lookupSeq.current;
      setLookup({ status: "loading" });
      fetchEdgeRegistry(trimmed)
        .then((entry) => {
          if (lookupSeq.current !== seq) return;
          setLookup(entry ? { status: "found", entry } : { status: "not_found" });
        })
        .catch((err) => {
          if (lookupSeq.current !== seq) return;
          setLookup({ status: "error", message: errorText(toErrorObject(err)) });
        });
    }, LOOKUP_DEBOUNCE_MS);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- idFormatError phái sinh từ robotId, thêm vào deps sẽ chạy lại vô ích
  }, [robotId, open]);

  const nameError = name.trim().length === 0 ? "Nhập tên để nhận ra robot này trong danh sách." : null;
  const idError =
    idFormatError ??
    (lookup.status === "not_found"
      ? "Chưa tìm thấy thiết bị Edge với ID này — bật Edge lên và đợi nó đăng ký trước."
      : lookup.status === "error"
        ? lookup.message
        : null);
  const valid = nameError === null && idError === null && lookup.status === "found";

  const submit = useCallback(async () => {
    setTouched({ name: true, id: true });
    if (!valid || submitting) return;

    setSubmitting(true);
    setFormError(null);
    try {
      const robot = await addRobot({ id: robotId.trim(), name: name.trim() });
      onAdded(robot);
      onClose();
    } catch (err) {
      setFormError(errorText(toErrorObject(err)));
    } finally {
      setSubmitting(false);
    }
  }, [valid, submitting, robotId, name, onAdded, onClose]);

  return (
    <Dialog
      open={open}
      onClose={submitting ? () => undefined : onClose}
      title="Thêm robot"
      disableOutsideClose={submitting}
      footer={
        <>
          <button type="button" onClick={onClose} disabled={submitting} className="btn-ghost focus-ring">
            Huỷ
          </button>
          <button
            type="button"
            onClick={submit}
            disabled={submitting}
            className="btn-primary focus-ring disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {submitting ? "Đang kết nối…" : "Thêm robot"}
          </button>
        </>
      }
    >
      {/* `onSubmit` chứ không chỉ nút bấm: gõ xong ID rồi Enter là thao tác
          bình thường của một form. */}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void submit();
        }}
        className="space-y-4"
      >
        <label className="block">
          <span className="text-[14px] text-paper-50">Tên robot</span>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            onBlur={() => setTouched((t) => ({ ...t, name: true }))}
            placeholder="Cánh tay Alpha"
            autoComplete="off"
            aria-invalid={touched.name && nameError !== null}
            className={`${FIELD_CLASS} ${
              touched.name && nameError ? "border-halt-500" : "border-[var(--line)] focus:border-arc-500"
            }`}
          />
          {touched.name && nameError && <span className="block mt-1 text-[13px] text-halt-500">{nameError}</span>}
        </label>

        <label className="block">
          <span className="text-[14px] text-paper-50">ID robot</span>
          <input
            type="text"
            value={robotId}
            onChange={(e) => setRobotId(e.target.value)}
            onBlur={() => setTouched((t) => ({ ...t, id: true }))}
            placeholder="robot-lab-01"
            autoComplete="off"
            spellCheck={false}
            aria-invalid={touched.id && idError !== null}
            className={`${FIELD_CLASS} data ${
              touched.id && idError ? "border-halt-500" : "border-[var(--line)] focus:border-arc-500"
            }`}
          />
          {touched.id && idError ? (
            <span className="block mt-1 text-[13px] text-halt-500">{idError}</span>
          ) : lookup.status === "loading" ? (
            <span className="block mt-1 text-[13px] text-haze-500">Đang tra cứu thiết bị Edge…</span>
          ) : lookup.status === "found" ? (
            <span className="block mt-1 text-[13px] text-arc-500">
              Đã tìm thấy — loại tay: {robotModelLabel(lookup.entry.robot_type)}
            </span>
          ) : (
            <span className="block mt-1 text-[13px] text-haze-500">{ROBOT_ID_HINT}</span>
          )}
        </label>

        {formError && (
          <p role="alert" className="text-[13.5px] text-halt-500">
            {formError}
          </p>
        )}

        {/* Cho phép Enter submit khi con trỏ đang ở một ô input. */}
        <button type="submit" className="hidden" aria-hidden="true" tabIndex={-1} />
      </form>
    </Dialog>
  );
}
