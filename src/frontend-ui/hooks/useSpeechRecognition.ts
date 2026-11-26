"use client";
/* useSpeechRecognition — mic-to-text cho Composer
   Bọc Web Speech API (`SpeechRecognition`/`webkitSpeechRecognition`) của trình
   duyệt. Không gọi backend nào — chuyển giọng nói thành text ngay tại client,
   phần còn lại (gửi lệnh) vẫn qua đường `onSend` sẵn có của Composer.

   API này chỉ có ở Chrome/Edge (và Safari bản mới, có tiền tố webkit). Nơi
   không hỗ trợ, `supported` trả về false để nơi gọi ẩn nút mic thay vì hiện
   một nút bấm không làm gì. */

import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";

interface SpeechRecognitionResultLike {
  isFinal: boolean;
  0: { transcript: string };
}

interface SpeechRecognitionEventLike {
  resultIndex: number;
  results: ArrayLike<SpeechRecognitionResultLike>;
}

interface SpeechRecognitionLike extends EventTarget {
  lang: string;
  interimResults: boolean;
  continuous: boolean;
  start: () => void;
  stop: () => void;
  onresult: ((event: SpeechRecognitionEventLike) => void) | null;
  onerror: ((event: { error: string }) => void) | null;
  onend: (() => void) | null;
}

type SpeechRecognitionCtor = new () => SpeechRecognitionLike;

function getSpeechRecognitionCtor(): SpeechRecognitionCtor | undefined {
  if (typeof window === "undefined") return undefined;
  const w = window as unknown as {
    SpeechRecognition?: SpeechRecognitionCtor;
    webkitSpeechRecognition?: SpeechRecognitionCtor;
  };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition;
}

/* Thông báo lỗi tiếng Việt cho các mã lỗi chuẩn của Web Speech API — xem
   https://developer.mozilla.org/en-US/docs/Web/API/SpeechRecognitionErrorEvent/error.
   `aborted` KHÔNG có ở đây: đó là kết quả bình thường của việc bấm nút dừng
   (`recognition.stop()`), báo lỗi cho nó thì mỗi lần dừng ghi âm đều hiện toast. */
const ERROR_MESSAGES: Record<string, string> = {
  "not-allowed": "Trình duyệt chặn quyền dùng micro. Vào cài đặt site để cho phép rồi thử lại.",
  "service-not-allowed": "Trình duyệt chặn quyền dùng micro. Vào cài đặt site để cho phép rồi thử lại.",
  "audio-capture": "Không tìm thấy micro. Kiểm tra thiết bị rồi thử lại.",
  "network": "Mất kết nối mạng khi nhận dạng giọng nói.",
  "no-speech": "Không nghe thấy giọng nói. Thử lại gần micro hơn.",
};

interface UseSpeechRecognitionOptions {
  lang?: string;
  /* Gọi mỗi khi transcript đổi. `finalTranscript` là phần đã chốt; `interimTranscript`
     là phần trình duyệt đang đoán, dùng cho trường hợp người dùng bấm xác nhận sớm. */
  onResult?: (payload: { finalTranscript: string; interimTranscript: string }) => void;
  /* Gọi mỗi khi trình duyệt trả về kết quả mới, kể cả interim — nơi gọi dùng để
     reset bộ đếm im lặng. */
  onActivity?: () => void;
  /* Lỗi đã dịch sang tiếng Việt — nơi gọi tự quyết định hiện thế nào (toast…).
     `aborted` không bao giờ gọi tới đây, xem ghi chú ở `ERROR_MESSAGES`. */
  onError?: (message: string) => void;
  onEnd?: () => void;
}

export function useSpeechRecognition({
  lang = "vi-VN",
  onResult,
  onActivity,
  onError,
  onEnd,
}: UseSpeechRecognitionOptions) {
  /* `useSyncExternalStore`, không phải `useState(() => ...)` — lazy initializer
     chạy cả lúc SSR, nơi `window` chưa tồn tại nên luôn trả `false`. Trang này
     vẫn render phía server (component "use client" không tự tắt SSR), nên HTML
     server gửi xuống thiếu nút mic; client hydrate xong mới phát hiện trình
     duyệt hỗ trợ, và một `setState` trong effect để bật lại sẽ ăn thêm một
     lượt render lồng (bị chặn bởi rule `react-hooks/set-state-in-effect`).
     `getServerSnapshot` trả cố định `false` khớp đúng HTML server đã gửi;
     `getSnapshot` chạy trên client đọc `window` thật — không lượt render thừa
     nào, không lệch hydrate. */
  const supported = useSyncExternalStore(
    () => () => {},
    () => getSpeechRecognitionCtor() !== undefined,
    () => false,
  );
  const [listening, setListening] = useState(false);
  const [error, setError] = useState<string | undefined>();
  const [finalTranscript, setFinalTranscript] = useState("");
  const [interimTranscript, setInterimTranscript] = useState("");
  const recognitionRef = useRef<SpeechRecognitionLike | null>(null);
  // Tránh callback cũ bị đóng băng trong closure khi component re-render.
  const onResultRef = useRef(onResult);
  const onActivityRef = useRef(onActivity);
  const onErrorRef = useRef(onError);
  const onEndRef = useRef(onEnd);
  useEffect(() => {
    onResultRef.current = onResult;
    onActivityRef.current = onActivity;
    onErrorRef.current = onError;
    onEndRef.current = onEnd;
  }, [onResult, onActivity, onError, onEnd]);

  const stop = useCallback(() => {
    recognitionRef.current?.stop();
  }, []);

  const resetTranscript = useCallback(() => {
    setFinalTranscript("");
    setInterimTranscript("");
  }, []);

  const start = useCallback(() => {
    const Ctor = getSpeechRecognitionCtor();
    if (!Ctor) return;
    setError(undefined);
    resetTranscript();

    const recognition = new Ctor();
    recognition.lang = lang;
    recognition.interimResults = true;
    recognition.continuous = true;

    recognition.onresult = (event) => {
      let finalChunk = "";
      let interim = "";
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const result = event.results[i];
        const transcript = result[0].transcript.trim();
        if (!transcript) continue;
        if (result.isFinal) finalChunk = `${finalChunk} ${transcript}`.trim();
        else interim = `${interim} ${transcript}`.trim();
      }
      if (!finalChunk && !interim) return;

      onActivityRef.current?.();
      setInterimTranscript(interim);
      if (!finalChunk) {
        onResultRef.current?.({ finalTranscript, interimTranscript: interim });
        return;
      }

      setFinalTranscript((prev) => {
        const next = prev ? `${prev} ${finalChunk}` : finalChunk;
        onResultRef.current?.({ finalTranscript: next, interimTranscript: interim });
        return next;
      });
    };
    recognition.onerror = (event) => {
      if (event.error === "aborted") return;
      setError(event.error);
      setListening(false);
      const message = ERROR_MESSAGES[event.error] ?? "Không thể thu giọng nói. Thử lại sau.";
      onErrorRef.current?.(message);
    };
    recognition.onend = () => {
      setListening(false);
      onEndRef.current?.();
    };

    recognitionRef.current = recognition;
    recognition.start();
    setListening(true);
  }, [finalTranscript, lang, resetTranscript]);

  const toggle = useCallback(() => {
    if (listening) stop();
    else start();
  }, [listening, start, stop]);

  useEffect(() => () => recognitionRef.current?.stop(), []);

  return { supported, listening, error, finalTranscript, interimTranscript, start, stop, toggle, resetTranscript };
}
