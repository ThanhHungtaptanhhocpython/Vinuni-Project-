"use client";
/* MicSpectrogram — phản hồi hình ảnh khi mic đang thu, để người dùng biết
   trình duyệt có thực sự "nghe" thấy tiếng nói hay không (mic im cũng trông
   giống mic hỏng nếu không có gì nhấp nháy).

   `useSpeechRecognition` không lộ dữ liệu audio thô — Web Speech API chỉ trả
   text. Nên component này tự xin một luồng `getUserMedia` riêng, chỉ để đọc
   biên độ tần số qua `AnalyserNode`, không đụng tới việc nhận dạng giọng nói. */

import { useEffect, useRef } from "react";

const BAR_COUNT = 20;

export function MicSpectrogram({ active }: { active: boolean }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const frameRef = useRef<number>(0);

  useEffect(() => {
    if (!active) return;

    let stream: MediaStream | undefined;
    let audioCtx: AudioContext | undefined;
    let cancelled = false;

    navigator.mediaDevices
      .getUserMedia({ audio: true })
      .then((mediaStream) => {
        // Effect đã bị dọn (mic tắt) trước khi permission prompt kịp trả lời.
        if (cancelled) {
          mediaStream.getTracks().forEach((track) => track.stop());
          return;
        }
        stream = mediaStream;
        audioCtx = new AudioContext();
        const source = audioCtx.createMediaStreamSource(mediaStream);
        const analyser = audioCtx.createAnalyser();
        analyser.fftSize = 64;
        // Chỉ đọc, không nối `analyser` ra `destination` — nối ra loa sẽ phát
        // lại tiếng người dùng vừa nói, gây hú/vọng.
        source.connect(analyser);
        const data = new Uint8Array(analyser.frequencyBinCount);

        const barColor = getComputedStyle(document.documentElement)
          .getPropertyValue("--arc-500")
          .trim() || "#0B67E0";

        const draw = () => {
          analyser.getByteFrequencyData(data);
          const canvas = canvasRef.current;
          const ctx = canvas?.getContext("2d");
          if (canvas && ctx) {
            const { width, height } = canvas;
            ctx.clearRect(0, 0, width, height);
            const step = Math.max(1, Math.floor(data.length / BAR_COUNT));
            const barWidth = width / BAR_COUNT;
            for (let i = 0; i < BAR_COUNT; i++) {
              const level = data[i * step] / 255;
              const barHeight = Math.max(2, level * height);
              ctx.globalAlpha = 0.35 + level * 0.65;
              ctx.fillStyle = barColor;
              ctx.fillRect(i * barWidth + 1, height - barHeight, barWidth - 2, barHeight);
            }
          }
          frameRef.current = requestAnimationFrame(draw);
        };
        draw();
      })
      .catch(() => {
        // Permission bị từ chối hoặc không có mic — bỏ qua hình ảnh, lỗi thật
        // (nếu có) đã được `useSpeechRecognition` báo qua đường riêng.
      });

    return () => {
      cancelled = true;
      cancelAnimationFrame(frameRef.current);
      stream?.getTracks().forEach((track) => track.stop());
      audioCtx?.close();
    };
  }, [active]);

  if (!active) return null;

  return (
    <canvas
      ref={canvasRef}
      width={96}
      height={28}
      className="block"
      aria-hidden="true"
    />
  );
}
