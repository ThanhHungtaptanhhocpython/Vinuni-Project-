"use client";
/* useSessionStream — P04 §8 + §9
   Owns the run lifecycle for the control page: opens the event stream, folds
   every event into message + realtime state, and always cleans up.

   In mock mode the source is `mockSseStream`. Swapping to the real Cloud means
   replacing `openStream` with the fetch + ReadableStream client (P04 §9) —
   the reducer below is transport-agnostic. */

import { useCallback, useEffect, useRef } from "react";
import type { Dispatch, SetStateAction } from "react";
import { mockSseStream, type SseEvent, type MockStreamHandle } from "@/lib/mock/sse";
import { USE_MOCK } from "@/lib/api/config";
import { startChat } from "@/lib/api/chat";
import { streamRunTrace } from "@/lib/api/trace-stream";
import { fetchMessages } from "@/lib/api/sessions";
import { cancelRun as cancelRunRequest, fetchRun } from "@/lib/api/runs";
import { toErrorObject } from "@/lib/errors";
import {
  useAuthStore,
  useRealtimeStore,
  useHumanInLoopStore,
  toast,
  toMessageRun,
  type Message,
  type ToolCall,
  type Episode,
  type AgentStatusLabel,
} from "@/stores";
import { errorText } from "@/lib/errors";
import { isTraceHiddenTool } from "@/lib/trace";

/** The closed set of user-facing agent statuses — MASTER §13.2.
    Anything the backend sends outside this set collapses to "Đang xử lý";
    we never infer a status from token content. */
const ALLOWED_STATUSES: readonly string[] = [
  "Đang hiểu yêu cầu",
  "Đang quan sát",
  "Đang lập kế hoạch",
  "Đang thực thi",
  "Đang dừng khẩn cấp",
  "Đang kiểm tra kết quả",
];

function normalizeStatus(raw: string): AgentStatusLabel {
  return (ALLOWED_STATUSES.includes(raw) ? raw : "Đang xử lý") as AgentStatusLabel;
}

const MOTION_TOOLS = new Set(["take_action", "go_home"]);

function episodeStillRunning(): boolean {
  const store = useRealtimeStore.getState();
  if (store.episodeProgress) return true;
  return store.runBlocks.some((block) => {
    if (block.kind !== "tool") return false;
    if (!MOTION_TOOLS.has(block.call.tool_name)) return false;
    if (block.call.status === "RUNNING" || block.call.status === "PENDING") return true;
    return (block.call.episodes ?? []).some((episode) => !episode.outcome);
  });
}

function markRobotBusy() {
  const store = useRealtimeStore.getState();
  const prev = store.robotState;
  store.updateRobotState({
    robot_status: "BUSY",
    connection_status: prev?.connection_status ?? "ONLINE",
    joints: prev?.joints ?? [],
  });
}

function releaseBusyIfIdle() {
  if (episodeStillRunning()) return;
  const store = useRealtimeStore.getState();
  const prev = store.robotState;
  if (prev?.robot_status !== "BUSY") return;
  store.updateRobotState({
    robot_status: "IDLE",
    connection_status: prev.connection_status,
    joints: prev.joints,
  });
}

/** Kết cục của lượt chạy, đọc từ sự kiện cuối của luồng trace. */
interface TerminalInfo {
  failed: boolean;
  cancelled: boolean;
  code?: string;
  message?: string;
}

/** Câu trả lời cuối của agent, lấy từ tin nhắn phiên sau khi lượt chạy xong.
 *
 * ĐƯỜNG DỰ PHÒNG DUY NHẤT, không phải đường chính: chữ giờ tới từ
 * `token.delta` thật trong lúc stream (A4/B1) — hàm này chỉ chạy khi lượt
 * chạy kết thúc mà CHƯA nhận được token nào (provider free chạy non-stream
 * lỗi giữa chừng, hoặc reconnect mất khúc đầu của stream).
 *
 * Hỏng thì trả một câu trung thực chứ không để trống: bong bóng rỗng làm người
 * dùng tưởng agent im lặng, trong khi thật ra là bước lấy tin nhắn thất bại. */
async function fetchFinalAnswer(sessionId: string, terminal: TerminalInfo | null): Promise<string> {
  try {
    const messages = await fetchMessages(sessionId);
    for (let i = messages.length - 1; i >= 0; i -= 1) {
      const content = messages[i].role === "assistant" ? messages[i].content?.trim() : "";
      if (content) return content;
    }
  } catch {
    /* rơi xuống phần dưới */
  }
  if (terminal?.cancelled) return "Đã huỷ.";
  if (terminal?.failed) return terminal.message ?? "Lượt chạy thất bại.";
  return "Agent đã xử lý xong nhưng không trả về nội dung.";
}

interface UseSessionStreamArgs {
  sessionId: string;
  setMessages: Dispatch<SetStateAction<Message[]>>;
}

export interface SessionStreamApi {
  /** Begin a run for the text the user just sent. Resolves when the run finishes. */
  startRun: (inputText: string) => Promise<void>;
  /** Reconnect to an existing active run and resume its trace. */
  reconnectRun: (runId: string) => Promise<void>;
  /** Ask Cloud Agent to cancel the active run after any needed safe stop. */
  cancelRun: () => Promise<void>;
  /** Tear the stream down (used by the stop button and on unmount). */
  closeStream: () => void;
}

export function useSessionStream({ sessionId, setMessages }: UseSessionStreamArgs): SessionStreamApi {
  const handleRef = useRef<MockStreamHandle | null>(null);
  const bannerTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  /* Đã nhận được `token.delta` thật nào trong lượt chạy hiện tại chưa —
     quyết định `startRun` có cần rơi xuống `fetchFinalAnswer` dự phòng sau
     khi stream kết thúc hay không (B2). Reset ở `run.started`. */
  const receivedTokenRef = useRef(false);
  const streamedMessageIdsRef = useRef<Set<string>>(new Set());
  /* id của tin nhắn assistant vừa được `message.completed` chốt gần nhất —
     `startRun` cần nó sau khi stream kết thúc để vá `run_metadata` (trạng
     thái/outcome) vào ĐÚNG bong bóng câu trả lời cuối, khi chữ đã tới qua
     `token.delta` thật (không cần dựng lại một message.completed giả). */
  const lastAssistantMessageIdRef = useRef<string | null>(null);

  /* ---- helpers that mutate the message list -------------------------- */

  /** Bong bóng mà MỌI thứ của lượt chạy hiện tại gắn vào: tool call, và cả
      chặng agent lúc chốt. Trả về id, tạo bong bóng rỗng nếu lượt này chưa
      sinh ra cái nào.

      Trước đây hai chỗ đều tự quét ngược tìm "assistant cuối cùng" tại thời
      điểm gọi. Kịch bản thật phát narration → tool → narration, nên tool rơi
      vào bong bóng thứ nhất còn chặng rơi vào bong bóng thứ hai — trace bị xé
      làm đôi. Neo theo id giải quyết luôn cả lỗi thứ hai: nếu tool của lượt 2
      tới trước tin nhắn đầu của lượt 2, quét ngược sẽ gắn nhầm vào bong bóng
      của lượt 1, còn anchor `null` nghĩa là "lượt này chưa có gì" nên nó tạo
      bong bóng mới. */
  const ensureRunAnchor = useCallback(
    /* Từ khi `run.started` mở sẵn bong bóng anchor, hàm này chỉ còn là LƯỚI
       HỨNG: tool nổ mà chưa từng có `run.started` (stream nối lại giữa chừng,
       backend gửi thiếu) thì vẫn phải có chỗ để gắn. */
    (fallbackId: string): string => {
      const store = useRealtimeStore.getState();
      const existing = store.runAnchorMessageId;
      if (existing !== null) return existing;
      store.setRunAnchorMessageId(fallbackId);
      return fallbackId;
    },
    [],
  );

  /** Append or update a tool call on the run's anchor message so the card sits
      inline in the conversation (MASTER §13.1) — never in its own bubble,
      never collected at the end. */
  const upsertToolCall = useCallback(
    (toolCallId: string, update: (existing: ToolCall | undefined) => ToolCall) => {
      const anchorId = ensureRunAnchor(`msg-agent-${toolCallId}`);
      /* Tính ngoài updater: React có thể gọi updater hai lần (StrictMode), mà
         `new Date()` trong đó thì mỗi lần ra một giá trị khác. */
      const createdAt = new Date().toISOString();
      /* Tính riêng object cho `runBlocks` từ nguồn live hiện tại, không đọc
         biến được gán bên trong React `setMessages` updater. Updater của React
         là công việc được enqueue; dựa vào side-effect trong đó khiến tool
         cards biến mất khỏi dòng phẳng dù `message.tool_calls` cuối cùng vẫn
         có thể được cập nhật. */
      const liveBlock = useRealtimeStore
        .getState()
        .runBlocks.find((b) => b.kind === "tool" && b.id === toolCallId);
      const liveExisting = liveBlock?.kind === "tool" ? liveBlock.call : undefined;
      const liveUpdatedCall = update(liveExisting);
      setMessages((prev) => {
        const next = [...prev];
        let idx = next.findIndex((m) => m.id === anchorId);
        // Anchor chưa thành hình (tool nổ trước khi có chữ nào) — mở bong bóng.
        if (idx === -1) {
          next.push({
            id: anchorId,
            session_id: sessionId,
            role: "assistant",
            content: "",
            created_at: createdAt,
            tool_calls: [],
          });
          idx = next.length - 1;
        }
        const target = next[idx];
        const calls = [...(target.tool_calls ?? [])];
        const pos = calls.findIndex((c) => c.id === toolCallId);
        const updated = update(pos >= 0 ? calls[pos] : undefined);
        if (pos >= 0) calls[pos] = updated;
        else calls.push(updated);
        next[idx] = { ...target, tool_calls: calls };
        return next;
      });
      /* Giữ `runBlocks` (dòng thời gian phẳng của lượt đang chạy, B3) đồng bộ
         với ĐÚNG object `ToolCall` vừa tính ở trên — tool bị ẩn khỏi trace
         (`check_status`/`get_task_status`, vòng poll nội bộ) thì cũng không
         được vào timeline phẳng, giống cách `AgentTrace` cũ lọc nó ra. */
      if (!isTraceHiddenTool(liveUpdatedCall.tool_name)) {
        useRealtimeStore.getState().upsertRunToolBlock(liveUpdatedCall);
      }
    },
    [sessionId, setMessages, ensureRunAnchor],
  );

  /** Update the episode attached to a tool call. */
  const upsertEpisode = useCallback(
    (episodeId: string, patch: Partial<Episode>, toolCallId?: string) => {
      const liveToolBlock = useRealtimeStore
        .getState()
        .runBlocks.find((block) => {
          if (block.kind !== "tool") return false;
          if (toolCallId) return block.id === toolCallId;
          return (block.call.episodes ?? []).some((episode) => episode.id === episodeId);
        });
      if (liveToolBlock?.kind === "tool") {
        const episodes = [...(liveToolBlock.call.episodes ?? [])];
        const pos = episodes.findIndex((episode) => episode.id === episodeId);
        if (pos >= 0) episodes[pos] = { ...episodes[pos], ...patch };
        else
          episodes.push({
            id: episodeId,
            steps: 0,
            max_steps: 400,
            duration_s: 0,
            ...patch,
          });
        useRealtimeStore.getState().upsertRunToolBlock({ ...liveToolBlock.call, episodes });
      }
      setMessages((prev) =>
        prev.map((msg) => {
          if (!msg.tool_calls) return msg;
          let touched = false;
          const calls = msg.tool_calls.map((call) => {
            const isTarget = toolCallId ? call.id === toolCallId : (call.episodes ?? []).some((e) => e.id === episodeId);
            if (!isTarget) return call;
            touched = true;
            const episodes = [...(call.episodes ?? [])];
            const pos = episodes.findIndex((e) => e.id === episodeId);
            if (pos >= 0) episodes[pos] = { ...episodes[pos], ...patch };
            else
              episodes.push({
                id: episodeId,
                steps: 0,
                max_steps: 400,
                duration_s: 0,
                ...patch,
              });
            return { ...call, episodes };
          });
          return touched ? { ...msg, tool_calls: calls } : msg;
        }),
      );
    },
    [setMessages],
  );

  /** Chốt chặng của lượt vừa xong: gắn vào ĐÚNG bong bóng anchor — cùng chỗ
      với tool call của lượt đó — rồi dọn store cho lượt sau. */
  const sealAgentSteps = useCallback(() => {
    const store = useRealtimeStore.getState();
    store.closeAgentSteps();
    const steps = useRealtimeStore.getState().agentSteps;
    const anchorId = store.runAnchorMessageId;
    if (steps.length > 0 && anchorId !== null) {
      setMessages((prev) => {
        const idx = prev.findIndex((m) => m.id === anchorId);
        if (idx === -1) return prev;
        const next = [...prev];
        next[idx] = { ...next[idx], agent_steps: steps };
        return next;
      });
    }
    store.resetAgentSteps();
  }, [setMessages]);

  /** Chốt dòng thời gian phẳng (`runBlocks`, B3) của lượt vừa xong vào
      `message.blocks` của bong bóng anchor — cùng khuôn với `sealAgentSteps`.
      Khối text cuối (nếu còn mở, vd lượt chạy bị huỷ/lỗi giữa chừng) được
      chốt `sealed:true` luôn, để không còn con trỏ nhấp nháy trên lịch sử. */
  const sealTraceBlocks = useCallback(() => {
    const store = useRealtimeStore.getState();
    store.sealCurrentTextBlock();
    const blocks = useRealtimeStore.getState().runBlocks;
    const anchorId = store.runAnchorMessageId;
    if (blocks.length > 0 && anchorId !== null) {
      setMessages((prev) => {
        const idx = prev.findIndex((m) => m.id === anchorId);
        if (idx === -1) return prev;
        const next = [...prev];
        next[idx] = { ...next[idx], blocks };
        return next;
      });
    }
    store.resetRunBlocks();
  }, [setMessages]);

  /** Vá `run_metadata` (trạng thái/outcome) vào bong bóng câu trả lời cuối
      SAU KHI lượt chạy đã kết thúc — dùng khi chữ đã tới qua `token.delta`
      thật trong lúc stream (B2), nên không cần dựng một `message.completed`
      giả chỉ để chở `run_metadata` như trước. */
  const attachRunMetadata = useCallback(
    (targetId: string | null, runMeta: NonNullable<Message["run"]>) => {
      if (!targetId) return;
      setMessages((prev) => prev.map((m) => (m.id === targetId ? { ...m, run: runMeta } : m)));
      fetchRun(runMeta.id)
        .then((run) => {
          if (!run) return;
          setMessages((prev) =>
            prev.map((m) => (m.run?.id === runMeta.id ? { ...m, run: toMessageRun(run) } : m)),
          );
        })
        .catch(() => {
          /* status line cứ để trống execution/verification "mềm" — không suy
             đoán khi không fetch được */
        });
    },
    [setMessages],
  );

  /* ---- the reducer: one case per row of the P04 §8 table -------------- */

  const handleEvent = useCallback(
    (event: SseEvent) => {
      const store = useRealtimeStore.getState();

      switch (event.type) {
        case "run.started": {
          store.setRunId(event.run_id);
          store.setBanner(null);
          // Dọn chặng của lượt trước, nếu không nó cộng dồn qua mọi lượt chat
          // và ra "Đã xong 25 bước" với danh sách lặp đi lặp lại.
          store.resetAgentSteps();
          store.resetRunBlocks();
          receivedTokenRef.current = false;
          streamedMessageIdsRef.current.clear();

          /* Mở BONG BÓNG RỖNG ngay, trước cả token đầu tiên.

             Trước đây anchor chỉ thành hình ở `message.completed`, nên suốt
             mấy giây đầu trace phải nổi tạm ở đáy hội thoại rồi nhảy lên khi
             câu trả lời xuất hiện. Ở nhịp chậm, cú nhảy đó nhìn rõ và khó chịu.

             Đổi lại: chữ đang stream phải chảy vào ĐÚNG bong bóng này chứ
             không dựng bong bóng riêng, nếu không mỗi lượt ra hai bong bóng —
             một chứa trace, một chứa chữ. Xem `message.completed` bên dưới và
             `MessageList`. */
          const anchorId = `msg-run-${event.run_id}`;
          store.setRunAnchorMessageId(anchorId);
          const createdAt = new Date().toISOString();
          setMessages((prev) =>
            prev.some((m) => m.id === anchorId)
              ? prev
              : [
                  ...prev,
                  {
                    id: anchorId,
                    session_id: sessionId,
                    role: "assistant" as const,
                    content: "",
                    created_at: createdAt,
                  },
                ],
          );
          break;
        }

        case "agent.status": {
          const label = normalizeStatus(event.status);
          store.setAgentStatus(label);
          // Ghi lại thành một bước để người dùng thấy được cả chặng đã đi, chứ
          // không chỉ mỗi bước hiện tại rồi nó biến mất.
          store.pushAgentStep(label);
          break;
        }

        case "agent.retry_loop": {
          useHumanInLoopStore.getState().setRequest({
            kind: "retry",
            requestId: event.request_id,
            retryCount: event.retry_count,
            lastError: event.last_error,
            runId: event.run_id,
          });
          break;
        }

        case "agent.clarification": {
          useHumanInLoopStore.getState().setRequest({
            kind: "clarify",
            requestId: event.request_id,
            question: event.question,
            options: event.options,
            runId: event.run_id,
          });
          break;
        }

        case "token.delta":
          receivedTokenRef.current = true;
          streamedMessageIdsRef.current.add(event.message_id);
          store.appendStreamingContent(event.message_id, event.delta);
          store.appendTokenBlock(event.delta);
          break;

        case "message.completed": {
          const streaming = useRealtimeStore.getState().streamingMessage;
          const anchorId = useRealtimeStore.getState().runAnchorMessageId;
          const createdAt = new Date().toISOString();
          const runMeta =
            "run_metadata" in event && event.run_metadata ? event.run_metadata : undefined;

          // Replay block logic: if we didn't receive any tokens for this message but the trace has a finalized content string
          if (!streamedMessageIdsRef.current.has(event.message_id) && event.content) {
            streamedMessageIdsRef.current.add(event.message_id);
            store.appendTokenBlock(event.content);
            receivedTokenRef.current = true;
          }

          const finalContent = streaming ? streaming.content : event.content;

          if (finalContent !== undefined && anchorId !== null) {
            lastAssistantMessageIdRef.current = anchorId;
            setMessages((prev) => {
              const idx = prev.findIndex((m) => m.id === anchorId);
              if (idx === -1) return prev;
              const next = [...prev];
              next[idx] = {
                ...next[idx],
                content: finalContent,
                created_at: createdAt,
                ...(runMeta ? { run: runMeta } : {}),
              };
              return next;
            });
          }

          if (runMeta && finalContent !== undefined) {
            const runId = runMeta.id;
            fetchRun(runId)
              .then((run) => {
                if (!run) return;
                setMessages((prev) =>
                  prev.map((m) => (m.run?.id === runId ? { ...m, run: toMessageRun(run) } : m)),
                );
              })
              .catch(() => {
                // Ignore fetch failure
              });
          }
          store.finalizeMessage();
          store.sealCurrentTextBlock();
          break;
        }

        case "tool.started":
          upsertToolCall(event.tool_call_id, (existing) => ({
            ...(existing ?? {
              id: event.tool_call_id,
              tool_name: event.tool_name,
              arguments: event.arguments,
            }),
            id: event.tool_call_id,
            tool_name: event.tool_name,
            arguments: event.arguments,
            // Giữ mốc cũ khi sự kiện đến lặp (reconnect): thứ tự không được nhảy.
            started_at: existing?.started_at ?? Date.now(),
            status: "RUNNING",
          }));
          if (MOTION_TOOLS.has(event.tool_name)) markRobotBusy();
          break;

        case "tool.pending_approval": {
          // Đặt tool vào trạng thái WAITING_APPROVAL để trace hiển thị rõ là nó
          // đang chờ người dùng chứ không phải đang chạy ngầm.
          upsertToolCall(event.tool_call_id, (existing) => ({
            id: event.tool_call_id,
            tool_name: existing?.tool_name ?? event.tool_name,
            arguments: existing?.arguments ?? {},
            started_at: existing?.started_at,
            status: "WAITING_APPROVAL",
          }));
          useHumanInLoopStore.getState().setRequest({
            kind: "risk",
            requestId: event.request_id,
            toolName: event.tool_name,
            riskReason: event.risk_reason,
            runId: event.run_id,
          });
          break;
        }

        case "tool.progress":
          store.updateToolCard(event.tool_call_id, { progress: event.progress });
          break;

        case "tool.completed":
          upsertToolCall(event.tool_call_id, (existing) => ({
            id: event.tool_call_id,
            tool_name: existing?.tool_name ?? "",
            arguments: existing?.arguments ?? {},
            episodes: existing?.episodes,
            // BẮT BUỘC mang theo: hai case này dựng lại object từ đầu, quên thì
            // hàng mất vị trí trong trace ngay khi tool xong.
            started_at: existing?.started_at,
            status: "SUCCEEDED",
            result: event.result,
            duration_ms: event.duration_ms,
          }));
          releaseBusyIfIdle();
          break;

        case "tool.failed":
          upsertToolCall(event.tool_call_id, (existing) => ({
            id: event.tool_call_id,
            tool_name: existing?.tool_name ?? "",
            arguments: existing?.arguments ?? {},
            episodes: existing?.episodes,
            started_at: existing?.started_at,
            status: "FAILED",
            error: event.error,
            duration_ms: event.duration_ms,
          }));
          releaseBusyIfIdle();
          break;

        case "episode.started":
          upsertEpisode(
            event.episode_id,
            { steps: 0, max_steps: event.max_steps, duration_s: 0, instruction: event.instruction },
            event.tool_call_id,
          );
          store.setEpisodeProgress({
            id: event.episode_id,
            step: 0,
            max_steps: event.max_steps,
            instruction: event.instruction,
            duration_s: 0,
          });
          markRobotBusy();
          break;

        case "episode.progress":
          upsertEpisode(event.episode_id, {
            steps: event.step,
            max_steps: event.max_steps,
            duration_s: event.elapsed_s,
          }, event.tool_call_id);
          store.setEpisodeProgress({
            id: event.episode_id,
            step: event.step,
            max_steps: event.max_steps,
            instruction: useRealtimeStore.getState().episodeProgress?.instruction,
            duration_s: event.elapsed_s,
          });
          break;

        case "episode.completed":
          upsertEpisode(event.episode_id, {
            steps: event.steps,
            duration_s: event.duration_s,
            outcome: event.outcome,
            stop_reason: event.stop_reason,
          }, event.tool_call_id);
          store.setEpisodeProgress(null);
          releaseBusyIfIdle();
          break;

        case "episode.stopped":
          store.setBanner({ tone: "info", message: "Đã dừng theo yêu cầu." });
          if (bannerTimer.current) clearTimeout(bannerTimer.current);
          bannerTimer.current = setTimeout(() => useRealtimeStore.getState().setBanner(null), 3000);
          break;

        case "robot.state.updated":
          store.updateRobotState({
            robot_status: event.robot_status,
            connection_status: event.connection_status,
            joints: event.joints.length > 0 ? event.joints : (store.robotState?.joints ?? []),
          });
          break;

        case "robot.disconnected":
          // Not a transient failure — do not retry, but keep Stop reachable.
          store.setRobotDisconnected(true);
          store.setBanner({ tone: "error", message: "Robot mất kết nối. Kiểm tra thiết bị Edge trước khi ra lệnh tiếp." });
          break;

        case "robot.reconnected":
          store.setRobotDisconnected(false);
          store.setBanner(null);
          break;

        case "artifact.created":
          store.addImage({
            id: event.artifact.id,
            url: event.artifact.url,
            camera: event.artifact.camera,
            captured_at: event.artifact.captured_at,
          });
          break;

        case "run.completed":
          releaseBusyIfIdle();
          store.setAgentStatus(null);
          sealAgentSteps();
          sealTraceBlocks();
          store.setPendingRun(false);
          store.setRunId(null);
          break;

        case "run.failed":
          // Always clear the planning label — otherwise a failed LLM call leaves
          // "Đang lập kế hoạch" spinning even when the run is dead.
          store.setAgentStatus(null);
          if (episodeStillRunning()) {
            store.setBanner({
              tone: "error",
              message: errorText(event.error),
              code: event.error.code,
            });
            break;
          }
          releaseBusyIfIdle();
          sealAgentSteps();
          sealTraceBlocks();
          store.setPendingRun(false);
          store.setRunId(null);
          store.setBanner({
            tone: "error",
            message: errorText(event.error),
            code: event.error.code,
          });
          break;

        case "run.cancelled":
          if (episodeStillRunning()) {
            store.setBanner({ tone: "info", message: "Đã huỷ." });
            break;
          }
          releaseBusyIfIdle();
          store.setAgentStatus(null);
          sealAgentSteps();
          sealTraceBlocks();
          store.setPendingRun(false);
          store.setRunId(null);
          store.setBanner({ tone: "info", message: "Đã huỷ." });
          break;

        case "error":
          // Non-blocking: toast only, composer stays usable.
          toast.error(errorText(event.error));
          break;

        case "ping":
          break;

        default:
          // Unknown event: ignore safely, never crash the page (P04 §8).
          console.debug("[sse] unhandled event", event);
      }
    },
    [sessionId, setMessages, upsertToolCall, upsertEpisode, sealAgentSteps, sealTraceBlocks],
  );

  const closeStream = useCallback(() => {
    handleRef.current?.close();
    handleRef.current = null;
    useRealtimeStore.getState().setCloseStream(null);
  }, []);

  const cancelRun = useCallback(async () => {
    const store = useRealtimeStore.getState();
    const runId = store.runId;
    if (!runId) return;

    store.setBanner({ tone: "info", message: "Đang gửi yêu cầu dừng khẩn cấp..." });

    if (USE_MOCK) {
      handleRef.current?.close();
      handleEvent({ type: "run.cancelled", run_id: runId });
      return;
    }

    const result = await cancelRunRequest(runId);
    if (result.safety_error) {
      store.setBanner({
        tone: "error",
        message: `Đã yêu cầu huỷ, nhưng lệnh dừng an toàn báo lỗi: ${result.safety_error}`,
      });
      return;
    }
    store.setBanner({
      tone: "info",
      message: result.dangerous_activity ? "Đã gửi lệnh dừng an toàn. Đang chờ agent kết thúc..." : "Đã yêu cầu huỷ run.",
    });
  }, [handleEvent]);

  const startRun = useCallback(
    async (inputText: string) => {
      const selectedRobotId = useAuthStore.getState().selectedRobotId;
      if (!USE_MOCK && !selectedRobotId) {
        useRealtimeStore.getState().setBanner({
          tone: "error",
          message: "Bạn chưa kết nối robot nào.",
        });
        return;
      }
      /* ĐỒNG BỘ, trước cả `closeStream()`/request HTTP đầu tiên (C4): chỉ báo
         chờ phải hiện ra NGAY khoảnh khắc bấm gửi, không đợi round-trip mạng
         của `POST /chat/start`. Tắt lại ở khối `finally` bên dưới VÀ ngay khi
         khối đầu tiên của lượt chạy tới (`appendTokenBlock`/`upsertRunToolBlock`). */
      useRealtimeStore.getState().setPendingRun(true);
      closeStream();
      const runId = `run-${Date.now().toString(36)}`;
      const store = useRealtimeStore.getState();
      store.setAgentStatus("Đang hiểu yêu cầu");
      store.setRunId(runId);
      store.setBanner(null);
      streamedMessageIdsRef.current.clear();

      if (USE_MOCK) {
        handleRef.current = mockSseStream({ runId, inputText, onEvent: handleEvent });
        store.setCloseStream(() => handleRef.current?.close());
        return;
      }

      /* Cloud agent thật: mở lượt chạy rồi NGHE trace theo thời gian thật.
       *
       * Trước đây chỗ này gọi `postChat` — một request chờ tới lúc agent xong
       * hẳn, rồi tự dựng ra vài sự kiện giả tại chỗ. Hệ quả là agent trace
       * không bao giờ có dòng tool nào: mọi hành động đã xảy ra xong xuôi
       * trước khi giao diện biết bất cứ điều gì.
       *
       * Nay: `POST /chat/start` trả `run_id` ngay và agent chạy nền, rồi
       * `GET /runs/{id}/stream` đẩy từng `tool.started`/`tool.completed` thật
       * về đúng lúc chúng xảy ra. `traceEventToSse` dịch sang từ vựng giao
       * diện, nên toàn bộ `handleEvent` phía dưới và `AgentTrace` không phải
       * đổi một dòng nào. */
      let cancelled = false;
      // Free OpenRouter models often need 30–90s per tool loop; hard-cap so UI unlocks.
      const controller = new AbortController();
      const hardTimeoutMs = 300_000;
      const hardTimer = window.setTimeout(() => controller.abort(), hardTimeoutMs);

      const handle = {
        close: () => {
          cancelled = true;
          controller.abort();
        },
      };
      handleRef.current = handle;
      store.setCloseStream(() => handle.close());

      try {
        const started = await startChat(
          { message: inputText, session_id: sessionId, robot_id: selectedRobotId ?? undefined },
          { signal: controller.signal },
        );
        if (cancelled) return;

        const rid = started.run_id || runId;
        /* Đổi `runId` tạm sang id thật NGAY: bong bóng anchor được đặt tên theo
           `msg-run-${run_id}`, nên nếu để lệch thì trace và câu trả lời rơi vào
           hai bong bóng khác nhau — đúng lỗi đã sửa ở phần mock. */
        store.setRunId(rid);
        handleEvent({ type: "run.started", run_id: rid, input_text: inputText });

        /* Ô chứa thay vì biến `let`: phép gán nằm trong callback nên phân tích
           luồng của TypeScript không thấy, và nó thu hẹp biến xuống `null` —
           mọi lần đọc sau đó thành `never`. Một tầng object thì không bị thu
           hẹp, và cũng nói rõ giá trị này được điền từ nơi khác. */
        const terminalRef: { current: TerminalInfo | null } = { current: null };

        await streamRunTrace({
          runId: rid,
          signal: controller.signal,
          onEvent: handleEvent,
          onTerminal: (payload) => {
            terminalRef.current = {
              failed:
                payload.type === "run.failed" ||
                payload.type === "run.timeout" ||
                payload.type === "run.cancelled",
              cancelled: payload.type === "run.cancelled",
              code: payload.error?.code,
              message: payload.error?.message,
            };
          },
        });
        if (cancelled) return;
        const terminal = terminalRef.current;

        const runMeta = {
          id: rid,
          trace_id: started.trace_id,
          status: terminal?.cancelled ? ("CANCELLED" as const) : terminal?.failed ? ("FAILED" as const) : ("COMPLETED" as const),
          duration_ms: null,
        };

        if (receivedTokenRef.current) {
          /* Đường CHÍNH: chữ đã chảy vào đúng bong bóng qua `token.delta` +
             `message.completed` thật trong lúc stream (B1/B2) — chỉ còn vá
             `run_metadata` vào bong bóng câu trả lời cuối mà reducer vừa
             chốt (`lastAssistantMessageIdRef`). */
          attachRunMetadata(lastAssistantMessageIdRef.current, runMeta);
        } else {
          /* ĐƯỜNG DỰ PHÒNG: hết stream mà không nhận được token nào (provider
             free non-stream lỗi giữa chừng, hoặc reconnect mất khúc đầu) —
             lấy câu trả lời cuối từ tin nhắn đã lưu ở backend, y như trước. */
          const messageId = `msg-agent-${rid}`;
          const text = await fetchFinalAnswer(started.session_id || sessionId, terminal);
          handleEvent({ type: "token.delta", message_id: messageId, delta: text });
          handleEvent({ type: "message.completed", message_id: messageId, run_metadata: runMeta });
        }

        /* Sự kiện kết thúc phát SAU câu trả lời: `run.completed` chốt chặng
           agent vào bong bóng anchor, mà lúc đó anchor phải đã có chữ.
           Nếu stream đứt giữa take_action thì KHÔNG giả completed — composer
           phải giữ khoá khi cánh tay còn đang chạy. */
        if (terminal?.failed) {
          handleEvent({
            type: "run.failed",
            run_id: rid,
            error: {
              code: terminal.code ?? "AGENT_001",
              message: terminal.message ?? "Run thất bại.",
            },
          });
        } else if (terminal || !episodeStillRunning()) {
          handleEvent({ type: "run.completed", run_id: rid });
        }
      } catch (err) {
        if (cancelled) return;
        const isAbort =
          err instanceof DOMException
            ? err.name === "AbortError"
            : err instanceof Error && err.name === "AbortError";
        const e = isAbort
          ? {
              code: "AGENT_003",
              message:
                "Agent chạy quá lâu (free LLM + robot tools). Thử câu ngắn hơn hoặc dùng model nhanh hơn.",
            }
          : toErrorObject(err);

        const messageId = `msg-agent-${runId}`;
        handleEvent({ type: "token.delta", message_id: messageId, delta: e.message });
        handleEvent({ type: "message.completed", message_id: messageId });

        handleEvent({
          type: "run.failed",
          run_id: runId,
          error: { code: e.code, message: e.message },
        });
        throw err;
      } finally {
        window.clearTimeout(hardTimer);
        const superseded = handleRef.current !== handle;
        if (handleRef.current === handle) handleRef.current = null;
        if (superseded) return;
        const rt = useRealtimeStore.getState();
        rt.setAgentStatus(null);
        rt.setRunId(null);
        rt.setPendingRun(false);
        if (!episodeStillRunning()) releaseBusyIfIdle();
      }
    },
    [closeStream, handleEvent, sessionId, attachRunMetadata],
  );

  const reconnectRun = useCallback(
    async (runId: string) => {
      if (USE_MOCK) return;

      useRealtimeStore.getState().setPendingRun(true);
      closeStream();
      const store = useRealtimeStore.getState();
      store.lockComposer();
      store.setAgentStatus("Đang xử lý");
      store.setRunId(runId);
      store.setBanner(null);
      receivedTokenRef.current = false;
      streamedMessageIdsRef.current.clear();

      let cancelled = false;
      const controller = new AbortController();
      const hardTimeoutMs = 300_000;
      const hardTimer = window.setTimeout(() => controller.abort(), hardTimeoutMs);

      const handle = {
        close: () => {
          cancelled = true;
          controller.abort();
        },
      };
      handleRef.current = handle;
      store.setCloseStream(() => handle.close());

      try {
        const terminalRef: { current: TerminalInfo | null } = { current: null };

        await streamRunTrace({
          runId,
          signal: controller.signal,
          onEvent: handleEvent,
          onTerminal: (payload) => {
            terminalRef.current = {
              failed:
                payload.type === "run.failed" ||
                payload.type === "run.timeout" ||
                payload.type === "run.cancelled",
              cancelled: payload.type === "run.cancelled",
              code: payload.error?.code,
              message: payload.error?.message,
            };
          },
        });
        if (cancelled) return;
        const terminal = terminalRef.current;

        const runMeta = {
          id: runId,
          trace_id: "", // We don't have the original trace_id, which is okay
          status: terminal?.cancelled ? ("CANCELLED" as const) : terminal?.failed ? ("FAILED" as const) : ("COMPLETED" as const),
          duration_ms: null,
        };

        if (receivedTokenRef.current) {
          attachRunMetadata(lastAssistantMessageIdRef.current, runMeta);
        } else {
          const messageId = `msg-agent-${runId}`;
          const text = await fetchFinalAnswer(sessionId, terminal);
          handleEvent({ type: "token.delta", message_id: messageId, delta: text });
          handleEvent({ type: "message.completed", message_id: messageId, run_metadata: runMeta });
        }

        if (terminal?.cancelled) {
          handleEvent({ type: "run.cancelled", run_id: runId });
        } else if (terminal?.failed) {
          handleEvent({
            type: "run.failed",
            run_id: runId,
            error: {
              code: terminal.code ?? "AGENT_001",
              message: terminal.message ?? "Run thất bại.",
            },
          });
        } else {
          handleEvent({ type: "run.completed", run_id: runId });
        }
      } catch (err) {
        if (cancelled) return;
        const isAbort =
          err instanceof DOMException
            ? err.name === "AbortError"
            : err instanceof Error && err.name === "AbortError";
        const e = isAbort
          ? {
              code: "AGENT_003",
              message:
                "Agent chạy quá lâu (free LLM + robot tools). Thử câu ngắn hơn hoặc dùng model nhanh hơn.",
            }
          : toErrorObject(err);

        const messageId = `msg-agent-${runId}`;
        handleEvent({ type: "token.delta", message_id: messageId, delta: e.message });
        handleEvent({ type: "message.completed", message_id: messageId });

        handleEvent({
          type: "run.failed",
          run_id: runId,
          error: { code: e.code, message: e.message },
        });
        throw err;
      } finally {
        window.clearTimeout(hardTimer);
        if (handleRef.current === handle) handleRef.current = null;
        const rt = useRealtimeStore.getState();
        rt.unlockComposer();
        rt.setAgentStatus(null);
        rt.setRunId(null);
        rt.setPendingRun(false);
      }
    },
    [closeStream, handleEvent, sessionId, attachRunMetadata]
  );

  // Every connection-opening effect cleans up — MASTER §21.
  useEffect(() => {
    return () => {
      handleRef.current?.close();
      handleRef.current = null;
      if (bannerTimer.current) clearTimeout(bannerTimer.current);
      useRealtimeStore.getState().reset();
    };
  }, [sessionId]);

  return { startRun, reconnectRun, cancelRun, closeStream };
}
