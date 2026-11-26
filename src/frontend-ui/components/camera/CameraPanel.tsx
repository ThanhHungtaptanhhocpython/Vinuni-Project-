"use client";
/* CameraPanel — P05
   The robot's camera. This is operational data, NOT the landing-page wallpaper
   video (MASTER §8): it carries a LIVE badge, FPS and connection state, and it
   only ever appears inside (app) routes. */

import { useEffect, useRef, useState, useCallback } from "react";
import { Camera as CameraIcon, Maximize2, Minimize2, RefreshCw } from "lucide-react";
import { useUiPreferenceStore, type CameraLayout } from "@/stores";
import { useCameraSession } from "./useCameraSession";
import { errorTextByCode } from "@/lib/errors";

const LAYOUTS: { key: CameraLayout; label: string }[] = [
  { key: "both", label: "Cả hai" },
  { key: "top", label: "Top" },
  { key: "wrist", label: "Wrist" },
];

interface CameraPanelProps {
  robotId: string;
  robotName: string;
  /** Robot is offline — camera is unreachable (§11.2 "Xem camera" = ⛔). */
  unavailableReason?: string;
  /** False when the panel's tab is not showing, so decoding can pause. */
  active?: boolean;
  className?: string;
  /** Hướng chia khi layout="both". "row" = hai camera cạnh nhau (cần đủ bề
      ngang, dùng cho trang chi tiết robot); "column" (mặc định) = xếp chồng
      lên nhau, giữ nguyên hành vi cũ cho những chỗ panel hẹp như tab Camera
      trong phiên điều khiển. */
  bothDirection?: "row" | "column";
}

/* ---------------------------------------------------------------- HUD */

function HudBadge({ children, dotClass }: { children: React.ReactNode; dotClass?: string }) {
  return (
    <div className="liquid-glass liquid-glass--hud rounded-full px-2.5 py-1 text-[12px] font-mono tabular-nums text-paper-50 flex items-center gap-1.5">
      {dotClass && <span className={`w-1.5 h-1.5 rounded-full ${dotClass}`} />}
      {children}
    </div>
  );
}

/* ---------------------------------------------------------------- feed */

function CameraFeed({
  robotName,
  streamUrl,
  paused,
  layout,
  children,
  className = "",
}: {
  robotName: string;
  streamUrl: string | null;
  paused: boolean;
  layout: CameraLayout;
  children?: React.ReactNode;
  className?: string;
}) {
  // Some browsers reuse a cached multipart body across ?camera= changes and keep
  // showing the stacked both-frame. If the decoded frame is still dual-wide while
  // a single camera is selected, crop to the matching half.
  const [frameWide, setFrameWide] = useState(false);

  useEffect(() => {
    setFrameWide(false);
  }, [streamUrl, layout]);

  const cropDual = layout !== "both" && frameWide;

  return (
    <div className={`relative bg-black overflow-hidden ${className}`}>
      {streamUrl && !paused ? (
        <div className="absolute inset-0 overflow-hidden">
          {/* eslint-disable-next-line @next/next/no-img-element -- MJPEG stream, not a static asset */}
          <img
            key={streamUrl}
            src={streamUrl}
            alt={`Ảnh trực tiếp từ camera của robot ${robotName}`}
            onLoad={(event) => {
              const img = event.currentTarget;
              setFrameWide(img.naturalWidth > img.naturalHeight * 1.4);
            }}
            className={
              cropDual
                ? "h-full w-[200%] max-w-none object-cover object-left"
                : "h-full w-full object-contain"
            }
            style={cropDual && layout === "wrist" ? { transform: "translateX(-50%)" } : undefined}
          />
        </div>
      ) : (
        <div className="w-full h-full bg-black" aria-label={`Camera của robot ${robotName}`} />
      )}
      <span className="absolute bottom-1.5 left-2 data text-[11.5px] px-1.5 py-0.5 rounded bg-ink-950/70 text-haze-300">
        mjpeg{layout !== "both" ? ` · ${layout}` : ""}
      </span>
      {children}
    </div>
  );
}

/* ---------------------------------------------------------------- panel */

export function CameraPanel({
  robotId,
  robotName,
  unavailableReason,
  active = true,
  className = "",
  bothDirection = "column",
}: CameraPanelProps) {
  const layout = useUiPreferenceStore((s) => s.cameraLayout);
  const setLayout = useUiPreferenceStore((s) => s.setCameraLayout);

  const [documentHidden, setDocumentHidden] = useState(false);
  const [fullscreen, setFullscreen] = useState(false);
  const [compactHud, setCompactHud] = useState(false);
  const frameRef = useRef<HTMLDivElement>(null);

  const enabled = !unavailableReason && active;
  const session = useCameraSession({ robotId, active: enabled });

  // Tab visibility — stop burning GPU on a hidden tab (MASTER §16).
  useEffect(() => {
    const onVisibility = () => setDocumentHidden(document.hidden);
    document.addEventListener("visibilitychange", onVisibility);
    return () => document.removeEventListener("visibilitychange", onVisibility);
  }, []);

  // 768–1279px keeps only the connection badge (MASTER §17).
  useEffect(() => {
    const query = window.matchMedia("(max-width: 1279px)");
    const apply = () => setCompactHud(query.matches);
    apply();
    query.addEventListener("change", apply);
    return () => query.removeEventListener("change", apply);
  }, []);

  useEffect(() => {
    const onChange = () => setFullscreen(Boolean(document.fullscreenElement));
    document.addEventListener("fullscreenchange", onChange);
    return () => document.removeEventListener("fullscreenchange", onChange);
  }, []);

  const toggleFullscreen = useCallback(() => {
    if (document.fullscreenElement) void document.exitFullscreen();
    else void frameRef.current?.requestFullscreen?.();
  }, []);

  if (unavailableReason) {
    return (
      <div className={`panel-inset p-6 flex flex-col items-center justify-center gap-2 text-center ${className}`}>
        <CameraIcon size={20} className="text-haze-500" />
        <p className="text-[14px] text-haze-300">{unavailableReason}</p>
      </div>
    );
  }

  const connectionTone =
    session.connectionState === "connected"
      ? "bg-jade-400"
      : session.connectionState === "failed"
        ? "bg-halt-500"
        : "bg-amber-400";

  const connectionLabel =
    session.connectionState === "connected"
      ? "Đã kết nối"
      : session.connectionState === "failed"
        ? "Mất kết nối"
        : session.connectionState === "reconnecting"
          ? "Đang kết nối lại"
          : "Đang kết nối";

  const live = session.connectionState === "connected" && !documentHidden;
  // Edge encodes a single camera when ?camera=top|wrist (sim maps top→agentview).
  // Cache-bust path so browsers don't reuse a stacked MJPEG body across tabs.
  const mjpegUrl = live
    ? `/api/v1/robots/${encodeURIComponent(robotId)}/stream/mjpeg?camera=${encodeURIComponent(layout)}&view=${encodeURIComponent(layout)}`
    : null;

  // Tối đa 3 badge cùng lúc (P05 cho phép 4); ≤1279px chỉ giữ badge kết nối.
  const hud = (
    <div className="absolute top-2 left-2 flex flex-wrap gap-1.5 max-w-[85%]">
      {!compactHud && live && <HudBadge dotClass="bg-cyan-400 animate-pulse-dot">LIVE</HudBadge>}
      <HudBadge dotClass={connectionTone}>{connectionLabel}</HudBadge>
    </div>
  );

  const feedPane = (
    <CameraFeed
      robotName={robotName}
      streamUrl={mjpegUrl}
      paused={documentHidden || !active}
      layout={layout}
      className="flex-1"
    >
      {hud}
    </CameraFeed>
  );

  return (
    /* The feed is black in either theme, so the frame and its toolbar keep the
       dark palette — a white bar clamped to a black picture reads as a bug. */
    <div
      ref={frameRef}
      data-theme="dark"
      className={`glass-edge flex flex-col bg-black rounded-lg overflow-hidden border border-[var(--line)] ${className}`}
    >
      {/* Toolbar */}
      <div className="h-9 px-2 flex items-center justify-between gap-2 border-b border-[var(--line)] bg-ink-900 shrink-0">
        <div className="flex bg-ink-800 rounded-lg p-0.5" role="group" aria-label="Chọn camera">
          {LAYOUTS.map((option) => (
            <button
              key={option.key}
              type="button"
              onClick={() => setLayout(option.key)}
              aria-pressed={layout === option.key}
              className={`px-2.5 py-1 text-[12.5px] font-medium rounded-md transition-colors duration-[120ms] focus-ring cursor-pointer ${
                layout === option.key ? "bg-ink-700 text-paper-50" : "text-haze-500 hover:text-haze-300"
              }`}
            >
              {option.label}
            </button>
          ))}
        </div>

        <div className="flex items-center gap-0.5">
          <button
            type="button"
            onClick={toggleFullscreen}
            title={fullscreen ? "Thoát toàn màn hình" : "Toàn màn hình"}
            aria-label={fullscreen ? "Thoát toàn màn hình" : "Toàn màn hình"}
            className="w-7 h-7 flex items-center justify-center rounded text-haze-300 hover:bg-ink-800 hover:text-paper-50 transition-colors duration-[120ms] focus-ring cursor-pointer"
          >
            {fullscreen ? <Minimize2 size={14} /> : <Maximize2 size={14} />}
          </button>
        </div>
      </div>

      {/* Feeds */}
      <div className={`flex-1 min-h-[220px] flex ${layout === "both" && bothDirection === "row" ? "flex-row" : "flex-col"}`}>
        {feedPane}
      </div>

      {/* Recovery — only after the retry ladder is exhausted (P05) */}
      {session.retriesExhausted && (
        <div className="px-3 py-2 bg-ink-900 border-t border-[var(--line)] flex items-center justify-between gap-3">
          <span className="text-[13.5px] text-halt-500">
            {session.errorCode ? errorTextByCode(session.errorCode, "Không kết nối được camera.") : "Không kết nối được camera."}
          </span>
          <button type="button" onClick={session.reconnect} className="btn-ghost !h-7 !text-[13px] focus-ring">
            <RefreshCw size={12} />
            Thử kết nối lại camera
          </button>
        </div>
      )}
    </div>
  );
}
