"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowLeft, Pause, Play, SkipBack, SkipForward } from "lucide-react";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { ReplayFrame, ReplayServoSample } from "@/lib/api/run-replay";
import { formatAbsolute } from "@/lib/format";
import { RobotArmViewer } from "./RobotArmViewer";

type ChartPoint = Record<string, number>;

const COLORS = ["#A3E635", "#D946EF", "#38BDF8", "#FBBF24", "#2DD4BF", "#F87171", "#C084FC"];

function formatTime(value: number): string {
  const date = new Date(value);
  return `${date.getMinutes().toString().padStart(2, "0")}:${date.getSeconds().toString().padStart(2, "0")}`;
}

function chartPoints(samples: ReplayServoSample[]): ChartPoint[] {
  return samples.map((sample) => {
    const point: ChartPoint = { time: Date.parse(sample.at) };
    sample.joints.forEach((joint) => {
      if (typeof joint.pos === "number") point[`pos_${joint.name}`] = joint.pos;
      if (typeof joint.vel === "number") point[`vel_${joint.name}`] = joint.vel;
      if (typeof joint.actuator_force === "number") point[`force_${joint.name}`] = joint.actuator_force;
    });
    return point;
  });
}

function jointNames(samples: ReplayServoSample[]): string[] {
  const names = new Set<string>();
  samples.forEach((sample) => sample.joints.forEach((joint) => names.add(joint.name)));
  return [...names];
}

function nearestServo(
  frames: ReplayFrame[],
  index: number,
  servoSamples: ReplayServoSample[],
): ReplayServoSample | undefined {
  for (let i = index; i >= 0; i -= 1) {
    if (frames[i]?.servo) return frames[i].servo;
  }
  for (let i = index + 1; i < frames.length; i += 1) {
    if (frames[i]?.servo) return frames[i].servo;
  }
  return servoSamples[0];
}

function nearestServoIndex(samples: ReplayServoSample[], sequence: number): number {
  if (samples.length === 0) return -1;
  let candidate = -1;
  samples.forEach((sample, index) => {
    if (sample.sequence <= sequence) candidate = index;
  });
  return candidate >= 0 ? candidate : 0;
}

function EventPill({ children }: { children: React.ReactNode }) {
  return (
    <span className="inline-flex h-5 items-center rounded-md bg-ink-800 px-2 font-mono text-[11.5px] text-haze-300">
      {children}
    </span>
  );
}

function IconButton({
  label,
  onClick,
  disabled,
  children,
}: {
  label: string;
  onClick: () => void;
  disabled?: boolean;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-label={label}
      title={label}
      className="inline-flex h-9 w-9 items-center justify-center rounded-lg bg-ink-800 text-paper-50 transition-colors duration-[120ms] hover:bg-ink-700 disabled:cursor-not-allowed disabled:opacity-40 focus-ring"
    >
      {children}
    </button>
  );
}

function ServoChart({ samples, currentAt }: { samples: ReplayServoSample[]; currentAt?: string }) {
  const data = useMemo(() => chartPoints(samples), [samples]);
  const names = useMemo(() => jointNames(samples), [samples]);
  const currentTime = currentAt ? Date.parse(currentAt) : undefined;

  if (samples.length === 0 || names.length === 0) {
    return (
      <div className="panel-inset p-4 text-[13.5px] text-haze-500">
        Run này chưa có snapshot servo/joints để vẽ biểu đồ.
      </div>
    );
  }

  return (
    <div className="bg-ink-950 rounded-lg border border-[var(--line)] p-2">
      <div className="h-[300px]">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 8, right: 16, left: -16, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#2a2a2a" vertical={false} />
            <XAxis
              dataKey="time"
              tickFormatter={formatTime}
              tick={{ fill: "#777", fontSize: 12 }}
              axisLine={false}
              tickLine={false}
              minTickGap={24}
            />
            <YAxis domain={["auto", "auto"]} tick={{ fill: "#777", fontSize: 12 }} axisLine={false} tickLine={false} />
            <Tooltip
              contentStyle={{ background: "rgb(32 29 26)", border: "1px solid var(--line-strong)", borderRadius: 8 }}
              labelFormatter={(value) => formatTime(Number(value))}
            />
            <ReferenceLine y={0} stroke="#444" />
            {currentTime !== undefined && (
              <ReferenceLine
                x={currentTime}
                stroke="#A3E635"
                strokeWidth={2}
                strokeDasharray="4 4"
                ifOverflow="extendDomain"
                label={{ value: "hiện tại", position: "top", fill: "#A3E635", fontSize: 11 }}
              />
            )}
            {names.slice(0, 7).map((name, index) => (
              <Line
                key={name}
                type="stepAfter"
                dataKey={`pos_${name}`}
                name={name}
                stroke={COLORS[index % COLORS.length]}
                strokeWidth={1.5}
                dot={false}
                isAnimationActive={false}
              />
            ))}
            <Legend wrapperStyle={{ fontSize: 12, color: "#aaa" }} />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

export function RunReplayPanel({
  frames,
  servoSamples,
  robotModel,
  live = false,
}: {
  frames: ReplayFrame[];
  servoSamples: ReplayServoSample[];
  robotModel?: string | null;
  live?: boolean;
}) {
  const router = useRouter();
  const [index, setIndex] = useState(0);
  const [servoIndex, setServoIndex] = useState(0);
  const [playing, setPlaying] = useState(false);
  const effectiveIndex = live && !playing && frames.length > 0 ? frames.length - 1 : index;
  const effectiveServoIndex = live && !playing && servoSamples.length > 0 ? servoSamples.length - 1 : servoIndex;
  const current = frames[effectiveIndex];
  const frameServo = current ? nearestServo(frames, effectiveIndex, servoSamples) : servoSamples[0];
  const latestServo = servoSamples.length > 0 ? servoSamples[servoSamples.length - 1] : undefined;
  const servo =
    playing && servoSamples.length > 0
      ? servoSamples[Math.min(effectiveServoIndex, servoSamples.length - 1)]
      : live
        ? latestServo
        : frameServo;
  const displayedServoIndex = servoSamples.findIndex((sample) => sample.sequence === servo?.sequence);

  useEffect(() => {
    if (!playing || frames.length <= 1) return;
    const timer = setInterval(() => {
      setIndex((value) => {
        if (value >= frames.length - 1) {
          setPlaying(false);
          return value;
        }
        return value + 1;
      });
    }, 450);
    return () => clearInterval(timer);
  }, [frames.length, playing]);

  useEffect(() => {
    if (!playing || servoSamples.length <= 1) return;
    const timer = setInterval(() => {
      setServoIndex((value) => (value >= servoSamples.length - 1 ? servoSamples.length - 1 : value + 1));
    }, 160);
    return () => clearInterval(timer);
  }, [playing, servoSamples.length]);

  if (frames.length === 0) {
    return (
      <div className="panel p-4 text-[13.5px] text-haze-500">
        Lượt chạy này chưa có dữ liệu để phát lại.
      </div>
    );
  }

  const visibleFrames = frames.slice(Math.max(0, effectiveIndex - 4), Math.min(frames.length, effectiveIndex + 5));

  return (
    <div className="space-y-4">
      <div className="flex items-start gap-3">
        <button
          type="button"
          onClick={() => router.back()}
          aria-label="Quay lại"
          title="Quay lại"
          className="w-9 h-9 flex items-center justify-center rounded-lg bg-ink-800 hover:bg-ink-700 transition-colors duration-[120ms] focus-ring cursor-pointer shrink-0"
        >
          <ArrowLeft size={17} />
        </button>
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-3 flex-wrap">
            <h2 className="eyebrow">Phát lại lượt chạy</h2>
            <div className="flex items-center gap-2 flex-wrap">
              <EventPill>#{current.sequence}</EventPill>
              <EventPill>{current.event}</EventPill>
              <EventPill>{current.actor}</EventPill>
              {current.tool && <EventPill>{current.tool}</EventPill>}
              {current.episodeId && <EventPill>{current.episodeId}</EventPill>}
            </div>
          </div>
          <p className="body-text text-paper-50 mt-1">{current.label}</p>
          <p className="data text-[12.5px] text-haze-500 mt-1">{formatAbsolute(current.at)}</p>
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
        <div className="space-y-4">
          <section className="panel p-4 space-y-3">
            <div className="flex items-baseline justify-between gap-3 flex-wrap">
              <h2 className="eyebrow">Viewer cánh tay</h2>
              <span className="text-[13.5px] text-haze-500">
                {servo ? `Servo ${displayedServoIndex >= 0 ? displayedServoIndex + 1 : "-"} / ${servoSamples.length}` : "Chưa có servo"}
              </span>
            </div>
            <RobotArmViewer sample={servo} robotModel={robotModel} />

            <div className="space-y-2 pt-1">
              <input
                type="range"
                min={0}
                max={Math.max(0, frames.length - 1)}
                value={effectiveIndex}
                onChange={(event) => {
                  setPlaying(false);
                  const nextIndex = Number(event.target.value);
                  setIndex(nextIndex);
                  const nextFrame = frames[nextIndex];
                  if (nextFrame) {
                    const nextServoIndex = nearestServoIndex(servoSamples, nextFrame.sequence);
                    if (nextServoIndex >= 0) setServoIndex(nextServoIndex);
                  }
                }}
                aria-label="Mốc phát lại"
                className="w-full accent-[var(--arc-500)]"
              />
              <div className="flex items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  <IconButton
                    label="Về mốc đầu"
                    onClick={() => {
                      setPlaying(false);
                      setIndex(0);
                      setServoIndex(0);
                    }}
                    disabled={effectiveIndex === 0}
                  >
                    <SkipBack size={16} />
                  </IconButton>
                  <IconButton
                    label={playing ? "Tạm dừng phát lại" : "Phát lại"}
                    onClick={() => {
                      if (!playing) {
                        const atEnd = effectiveIndex >= frames.length - 1;
                        const startIndex = atEnd ? 0 : effectiveIndex;
                        if (atEnd) setIndex(0);
                        const startFrame = frames[startIndex];
                        const nextServoIndex = startFrame ? nearestServoIndex(servoSamples, startFrame.sequence) : -1;
                        setServoIndex(nextServoIndex >= 0 ? nextServoIndex : 0);
                      }
                      setPlaying((value) => !value);
                    }}
                  >
                    {playing ? <Pause size={17} /> : <Play size={17} />}
                  </IconButton>
                  <IconButton
                    label="Sang mốc tiếp theo"
                    onClick={() => {
                      setPlaying(false);
                      setIndex(Math.min(frames.length - 1, effectiveIndex + 1));
                    }}
                    disabled={effectiveIndex >= frames.length - 1}
                  >
                    <SkipForward size={16} />
                  </IconButton>
                </div>
                <span className="data text-[12.5px] text-haze-500">
                  {effectiveIndex + 1}/{frames.length}
                </span>
              </div>
            </div>
          </section>

          <section className="panel p-4 space-y-3">
            <div className="flex items-baseline justify-between gap-3 flex-wrap">
              <h2 className="eyebrow">Biểu đồ servo đã ghi</h2>
              <span className="text-[13.5px] text-haze-500">{servoSamples.length} mẫu</span>
            </div>
            <ServoChart samples={servoSamples} currentAt={servo?.at} />
          </section>
        </div>

        <div className="panel-inset p-3 lg:sticky lg:top-4 lg:self-start lg:max-h-[calc(100vh-2rem)] overflow-y-auto">
          <h3 className="eyebrow mb-2">Các bước chạy</h3>
          <ol className="space-y-2">
            {visibleFrames.map((frame) => (
              <li
                key={frame.sequence}
                className={`rounded-md px-3 py-2 border ${
                  frame.sequence === current.sequence
                    ? "border-arc-500/60 bg-arc-500/10"
                    : "border-transparent bg-ink-900/60"
                }`}
              >
                <button
                  type="button"
                  onClick={() => setIndex(frames.findIndex((item) => item.sequence === frame.sequence))}
                  className="w-full text-left focus-ring rounded"
                >
                  <div className="flex items-center gap-2">
                    <span className="data text-[12px] text-haze-500">#{frame.sequence}</span>
                    <span className="text-[13.5px] text-paper-50">{frame.label}</span>
                  </div>
                  <p className="data text-[12px] text-haze-500 mt-1">{frame.event}</p>
                </button>
              </li>
            ))}
          </ol>
        </div>
      </div>
    </div>
  );
}
