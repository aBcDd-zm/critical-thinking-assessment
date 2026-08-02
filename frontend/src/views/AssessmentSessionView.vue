<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import {
  continueCurrentAssessmentStage,
  finishAssessmentSession,
  getAssessmentPreparation,
  getAssessmentSession,
  skipCurrentAssessmentStage,
  startAssessmentInterviewStream,
  submitAssessmentTurnStream,
  submitProfileTurnStream,
} from "../api/session";
import AnswerComposer from "../components/assessment/AnswerComposer.vue";
import DialogueTurn from "../components/assessment/DialogueTurn.vue";
import { useSpeechPlayback } from "../composables/useSpeechPlayback";
import type { DialogueTurnItem, SessionResponse } from "../types/session";

type SpeechInputState =
  | "unsupported"
  | "ready"
  | "starting"
  | "listening"
  | "stopping"
  | "review"
  | "error";

type SessionWatchdogResult =
  | { kind: "completed"; latest: SessionResponse }
  | { kind: "settled"; latest: SessionResponse }
  | { kind: "timeout" }
  | { kind: "cancelled" };

const route = useRoute();
const router = useRouter();
const sessionUuid = computed(() => String(route.params.sessionUuid || ""));
const session = ref<SessionResponse | null>(null);
const answer = ref("");
const loading = ref(false);
const submitting = ref(false);
const finishing = ref(false);
const skipping = ref(false);
const continuing = ref(false);
const error = ref("");
const statusMessage = ref("");
const transcriptRef = ref<HTMLElement | null>(null);
const chatZoneRef = ref<HTMLElement | null>(null);
const contextPanelWidth = ref<number | null>(null);
const isPanelResizing = ref(false);
const optimisticUserTurn = ref<DialogueTurnItem | null>(null);
const streamingAiText = ref("");
const streamingStartedAt = ref("");
const openingStarting = ref(false);
const completionPending = ref(false);
const elapsedSecondsBase = ref(0);
const elapsedSecondsSyncedAt = ref(Date.now());
const timerNow = ref(Date.now());
const profileHistoryExpanded = ref(false);
const autoFollowTranscript = ref(true);
const hasUnseenTurns = ref(false);
const speechInputState = ref<SpeechInputState>("unsupported");
const STREAMING_AI_TURN_INDEX = -1;
const TRANSCRIPT_BOTTOM_THRESHOLD = 48;
const SUBMISSION_WATCHDOG_DELAY_MS = 12_000;
const SESSION_STATUS_POLL_INTERVAL_MS = 2_000;
const SESSION_STATUS_POLL_LIMIT = 24;
const RESPONSE_STATUS_STEPS = [
  { delay: 1_800, message: "正在联系你刚才的重点和当前情境..." },
  { delay: 4_500, message: "正在准备一个更聚焦的问题..." },
  { delay: 7_000, message: "回答已保存，仍在处理，请勿重复提交..." },
] as const;
let statusClearTimer: ReturnType<typeof setTimeout> | null = null;
let preparationPollTimer: ReturnType<typeof setInterval> | null = null;
let interviewTimer: ReturnType<typeof setInterval> | null = null;
let submissionAttempt = 0;
let generationRecoveryAttempt = 0;
const watchdogTimers = new Set<ReturnType<typeof setTimeout>>();
const responseStatusTimers = new Set<ReturnType<typeof setTimeout>>();

const pendingClientTurnId = ref<string | null>(null);
const answerStartedAt = ref<number | null>(null);
const CONTEXT_PANEL_WIDTH_STORAGE_KEY = "assessment-context-panel-width";
const MIN_CONTEXT_PANEL_WIDTH = 320;
const MIN_DIALOGUE_PANEL_WIDTH = 420;
const DEFAULT_CONTEXT_PANEL_WIDTH = 440;
const {
  disable: disableSpeech,
  enable: enableSpeech,
  enableAndSpeakTurn,
  enabled: interviewerVoiceEnabled,
  isSupported: speechPlaybackSupported,
  notice: speechNotice,
  speakTurn,
  stop: stopSpeech,
} = useSpeechPlayback();
const speechInputActive = computed(() =>
  ["starting", "listening", "stopping"].includes(speechInputState.value),
);

function setStatus(message: string, autoClearMs?: number) {
  if (statusClearTimer) {
    clearTimeout(statusClearTimer);
    statusClearTimer = null;
  }
  statusMessage.value = message;
  if (autoClearMs && autoClearMs > 0) {
    statusClearTimer = setTimeout(() => {
      statusMessage.value = "";
      statusClearTimer = null;
    }, autoClearMs);
  }
}

function waitForWatchdog(ms: number) {
  return new Promise<void>((resolve) => {
    const timer = setTimeout(() => {
      watchdogTimers.delete(timer);
      resolve();
    }, ms);
    watchdogTimers.add(timer);
  });
}

function clearResponseStatusSteps() {
  for (const timer of responseStatusTimers) clearTimeout(timer);
  responseStatusTimers.clear();
}

function beginResponseStatusSteps(finalAnswer: boolean) {
  clearResponseStatusSteps();
  if (finalAnswer) {
    setStatus("最终回答已收到，正在检查完整性并准备报告...");
    return;
  }
  setStatus("已收到，正在整理你刚才的重点...");
  for (const step of RESPONSE_STATUS_STEPS) {
    const timer = setTimeout(() => {
      responseStatusTimers.delete(timer);
      if (submitting.value && !streamingAiText.value) {
        setStatus(step.message);
      }
    }, step.delay);
    responseStatusTimers.add(timer);
  }
}

async function pollSessionUntilSettled(
  targetSessionUuid: string,
  isCurrent: () => boolean,
  initialDelayMs: number,
): Promise<SessionWatchdogResult> {
  if (initialDelayMs > 0) {
    await waitForWatchdog(initialDelayMs);
  }
  for (let attempt = 0; attempt < SESSION_STATUS_POLL_LIMIT; attempt += 1) {
    if (!isCurrent()) return { kind: "cancelled" };
    if (attempt > 0) {
      await waitForWatchdog(SESSION_STATUS_POLL_INTERVAL_MS);
      if (!isCurrent()) return { kind: "cancelled" };
    }
    setStatus(
      completionPending.value
        ? attempt === 0
          ? "最终阶段回答已保存，正在确认报告生成进度..."
          : "报告仍在后台生成，正在确认最新进度..."
        : attempt === 0
          ? "处理时间较长，正在确认当前进度..."
          : "回答已保存，后台仍在处理，正在确认进度...",
    );
    try {
      const latest = await getAssessmentSession(targetSessionUuid);
      if (!isCurrent()) return { kind: "cancelled" };
      session.value = latest;
      if (latest.status === "completed") {
        return { kind: "completed", latest };
      }
      if (latest.status === "generating") {
        continue;
      }
      return { kind: "settled", latest };
    } catch {
      // A later bounded poll may recover from a transient status-read failure.
    }
  }
  return { kind: "timeout" };
}

const turns = computed<DialogueTurnItem[]>(() => session.value?.turns || []);
const isProgressive = computed(() =>
  ["progressive_v3", "progressive_v3_2", "progressive_v3_3"].includes(session.value?.flow_version || ""),
);
const isV32 = computed(() =>
  ["progressive_v3_2", "progressive_v3_3"].includes(session.value?.flow_version || ""),
);
const formalOpeningTurnIndex = computed(
  () => turns.value.find((turn) => turn.content_type === "stage_question")?.turn_index,
);
const isOnboarding = computed(
  () =>
    session.value?.phase === "onboarding" ||
    session.value?.phase === "scenario_preparing" ||
    session.value?.phase === "opening_pending",
);
const isWaitingForScenario = computed(() =>
  ["scenario_preparing", "opening_pending"].includes(session.value?.phase || ""),
);
const fallbackScenario = {
  title: "产品上线前 48 小时",
  background:
    "团队计划在 48 小时后上线一款面向高校与初入职场用户的任务协作产品，但内测反馈、市场窗口、技术风险和团队资源之间存在冲突。",
  stageTitle: "问题界定",
  stageContext: "当前需要先厘清真正需要决策的问题边界，再判断哪些信息会影响上线策略。",
  question: "如果现在由你负责，你会先把哪件事定下来？",
};
const userVisibleTurns = computed(() =>
  turns.value
    .filter((turn) => !isInternalAuditTurn(turn))
    .slice()
    .sort((left, right) => left.turn_index - right.turn_index),
);
const profileTurns = computed(() =>
  userVisibleTurns.value.filter((turn) => isProfileTurn(turn)),
);
const displayTurns = computed<DialogueTurnItem[]>(() => {
  const items = userVisibleTurns.value
    .filter((turn) => (isOnboarding.value ? isProfileTurn(turn) : !isProfileTurn(turn)))
    .filter((turn) => isProgressive.value || !isOpeningStageQuestion(turn))
    .map((turn) => (isProgressive.value ? turn : compactStageQuestion(turn)));
  if (
    optimisticUserTurn.value &&
    !items.some((turn) => turn.turn_index === optimisticUserTurn.value?.turn_index)
  ) {
    items.push(optimisticUserTurn.value);
  }
  if (streamingAiText.value.trim().length > 0) {
    items.push({
      turn_index: STREAMING_AI_TURN_INDEX,
      speaker: "ai",
      content: streamingAiText.value,
      content_type: "followup_question",
      created_at: streamingStartedAt.value || new Date().toISOString(),
    });
  }
  return items;
});

function isInternalAuditTurn(turn: DialogueTurnItem): boolean {
  return (
    turn.speaker === "system" ||
    [
      "system_message",
      "stage_continue",
      "stage_skipped",
      "stage_incomplete_prompt",
    ].includes(
      turn.content_type,
    )
  );
}
const latestStage = computed(() => session.value?.current_stage);
const isDebug = computed(() => route.query.debug === "1");
const totalStages = computed(() => session.value?.progress?.total_stages || 6);
const currentStageOrder = computed(
  () => session.value?.progress?.current_stage_order || latestStage.value?.stage_order || 1,
);
const stageTitle = computed(() => latestStage.value?.title || fallbackScenario.stageTitle);
const formalAnswerCount = computed(
  () => session.value?.interview_progress?.formal_answer_count || 0,
);
const targetMinAnswers = computed(
  () => session.value?.interview_progress?.target_min_answers || 9,
);
const targetMaxAnswers = computed(
  () => session.value?.interview_progress?.target_max_answers || 12,
);
const stageMeta = computed(() =>
  isOnboarding.value
    ? `背景了解 ${session.value?.onboarding?.question_count || 1}/${session.value?.onboarding?.max_questions || 3}`
    : isProgressive.value
      ? completionPending.value
        ? "正在完成测评"
        : session.value?.status === "completed"
          ? "测评已完成"
          : `已回答 ${formalAnswerCount.value} 轮 · 通常 ${targetMinAnswers.value}–${targetMaxAnswers.value} 轮`
      : `主题 ${currentStageOrder.value}/${totalStages.value} · ${stageTitle.value}`,
);
const progressiveRemainingText = computed(() => {
  if (!isProgressive.value || isOnboarding.value) return "";
  if (completionPending.value) return "报告生成中";
  if (session.value?.status === "completed") return "";
  if (formalAnswerCount.value < targetMinAnswers.value) {
    const minutes = session.value?.interview_progress?.estimated_remaining_minutes || 0;
    return minutes > 0 ? `预计还需约 ${minutes} 分钟` : "正在整理下一轮问题";
  }
  const remainingRounds = Math.max(targetMaxAnswers.value - formalAnswerCount.value, 0);
  return remainingRounds > 0
    ? `正在收束 · 最多还有 ${remainingRounds} 轮`
    : "正在完成测评";
});
const canReviewHistory = computed(
  () => displayTurns.value.length > 1 || (!isOnboarding.value && profileTurns.value.length > 0),
);
const canSubmit = computed(
  () =>
    Boolean(session.value) &&
    answer.value.trim().length > 0 &&
    !submitting.value &&
    !isWaitingForScenario.value &&
    session.value?.status !== "generating" &&
    session.value?.status !== "completed",
);
const progressSegments = computed(() => Array.from({ length: totalStages.value }, (_, index) => index + 1));
const progressCountText = computed(() => `已进行 ${currentStageOrder.value}/${totalStages.value}`);
const currentStageProgress = computed(() => {
  const stageCode = latestStage.value?.stage_code;
  if (!stageCode) return null;
  return session.value?.progress?.stages.find((item) => item.stage_code === stageCode) || null;
});
const serverElapsedSeconds = computed(() =>
  isProgressive.value
    ? session.value?.interview_progress?.elapsed_seconds
    : session.value?.progress?.elapsed_seconds,
);
const timerShouldTick = computed(
  () =>
    Boolean(session.value) &&
    !isOnboarding.value &&
    !completionPending.value &&
    ["in_progress", "generating"].includes(session.value?.status || ""),
);
const displayElapsedSeconds = computed(() =>
  elapsedSecondsBase.value +
  (timerShouldTick.value
    ? Math.floor((timerNow.value - elapsedSecondsSyncedAt.value) / 1000)
    : 0),
);
const timerText = computed(() => {
  if (isOnboarding.value) return "准备中";
  if (isProgressive.value) {
    return `已用时 ${formatSeconds(displayElapsedSeconds.value)}`;
  }
  const seconds = serverElapsedSeconds.value;
  if (seconds != null) return formatSeconds(seconds);
  const minutes = session.value?.scenario?.estimated_minutes || 30;
  return `${String(minutes).padStart(2, "0")}:00`;
});
watch(serverElapsedSeconds, (seconds) => {
  if (seconds == null) return;
  elapsedSecondsBase.value = Math.max(0, seconds);
  elapsedSecondsSyncedAt.value = Date.now();
  timerNow.value = elapsedSecondsSyncedAt.value;
}, { immediate: true });
const scenarioTitle = computed(() => session.value?.scenario?.title || fallbackScenario.title);
const scenarioBackground = computed(
  () =>
    session.value?.scenario?.background ||
    fallbackScenario.background,
);
const currentStageContext = computed(() => latestStage.value?.context || fallbackScenario.stageContext);
const currentMainQuestion = computed(() => latestStage.value?.main_question || fallbackScenario.question);
const compactOpeningMessages = computed<DialogueTurnItem[]>(() => {
  if (isOnboarding.value) return [];
  const opening = turns.value.find((turn) => isOpeningStageQuestion(turn));
  if (!opening) return [];
  const paragraphs = opening.content
    .split(/\n{2,}/)
    .map((item) => item.trim())
    .filter(Boolean);
  return [
    {
      turn_index: -101,
      speaker: "ai",
      content: `${session.value?.participant_nickname || "你好"}，你好。先说你的第一反应就可以，我会顺着你的回答继续问。`,
      content_type: "intro_greeting",
      created_at: opening.created_at,
    },
    {
      turn_index: -102,
      speaker: "ai",
      content: paragraphs.at(-1) || currentMainQuestion.value,
      content_type: "stage_question",
      created_at: opening.created_at,
    },
  ];
});
const stageEstimateText = computed(() => {
  const minutes = currentStageProgress.value?.estimated_minutes;
  return minutes ? `预计 ${minutes} 分钟` : `全程 ${session.value?.scenario?.estimated_minutes || 30} 分钟`;
});
const followupLimitText = computed(() => {
  const used = currentStageProgress.value?.used_followups ?? 0;
  const max = latestStage.value?.max_followups ?? currentStageProgress.value?.max_followups ?? 0;
  return max > 0 ? `追问 ${used}/${max}` : "开放表达";
});
const canSkipCurrentStage = computed(
  () => Boolean(currentStageProgress.value?.can_skip) && !submitting.value && !skipping.value,
);
const waitingForStageChoice = computed(
  () => Boolean(currentStageProgress.value?.waiting_for_stage_choice),
);
const statusIndicatorState = computed<"thinking" | "success" | null>(() => {
  const isThinking =
    submitting.value ||
    session.value?.status === "generating" ||
    streamingAiText.value.length > 0 ||
    /正在|等待|思考|分析|回复|生成|刷新/.test(statusMessage.value);
  if (isThinking) return "thinking";
  return statusMessage.value ? "success" : null;
});
const statusAriaText = computed(() =>
  statusMessage.value ||
  (statusIndicatorState.value === "thinking" ? "罗杰斯教授正在准备回复" : "操作已完成"),
);
const assessmentActionPrompts = [
  "我会先用一句话说清核心判断，并列出两项限制。",
  "我会区分现有材料能支持什么、还不能确定什么。",
  "我会比较不同相关方的目标、风险和冲突。",
  "如果出现反向证据，我会说明保留和调整哪些判断。",
];
const actionPrompts = computed(() =>
  isOnboarding.value || isProgressive.value ? [] : assessmentActionPrompts,
);
const chatZoneStyle = computed(() =>
  contextPanelWidth.value == null
    ? undefined
    : { "--context-panel-width": `${contextPanelWidth.value}px` },
);

watch(answer, (value) => {
  if (value.trim() && answerStartedAt.value == null) answerStartedAt.value = Date.now();
  if (!value.trim() && !submitting.value) answerStartedAt.value = null;
});

function panelWidthBounds() {
  const containerWidth = chatZoneRef.value?.getBoundingClientRect().width || 0;
  const maximum = Math.max(
    MIN_CONTEXT_PANEL_WIDTH,
    containerWidth - MIN_DIALOGUE_PANEL_WIDTH - 64,
  );
  return { minimum: MIN_CONTEXT_PANEL_WIDTH, maximum };
}

function clampContextPanelWidth(width: number) {
  const { minimum, maximum } = panelWidthBounds();
  return Math.min(maximum, Math.max(minimum, Math.round(width)));
}

function currentContextPanelWidth() {
  if (contextPanelWidth.value != null) return contextPanelWidth.value;
  const contextCard = chatZoneRef.value?.querySelector<HTMLElement>(
    ".interview-context-card",
  );
  return contextCard?.getBoundingClientRect().width || DEFAULT_CONTEXT_PANEL_WIDTH;
}

function updateContextPanelFromPointer(clientX: number) {
  const bounds = chatZoneRef.value?.getBoundingClientRect();
  if (!bounds) return;
  const rightPadding = 20;
  contextPanelWidth.value = clampContextPanelWidth(
    bounds.right - rightPadding - clientX,
  );
}

function persistContextPanelWidth() {
  if (contextPanelWidth.value == null) return;
  window.localStorage.setItem(
    CONTEXT_PANEL_WIDTH_STORAGE_KEY,
    String(contextPanelWidth.value),
  );
}

function stopPanelResize() {
  if (!isPanelResizing.value) return;
  isPanelResizing.value = false;
  document.body.classList.remove("is-resizing-assessment-panels");
  window.removeEventListener("pointermove", handlePanelPointerMove);
  window.removeEventListener("pointerup", stopPanelResize);
  window.removeEventListener("pointercancel", stopPanelResize);
  persistContextPanelWidth();
}

function handlePanelPointerMove(event: PointerEvent) {
  if (!isPanelResizing.value) return;
  updateContextPanelFromPointer(event.clientX);
}

function startPanelResize(event: PointerEvent) {
  if (event.pointerType === "mouse" && event.button !== 0) return;
  event.preventDefault();
  isPanelResizing.value = true;
  document.body.classList.add("is-resizing-assessment-panels");
  updateContextPanelFromPointer(event.clientX);
  window.addEventListener("pointermove", handlePanelPointerMove);
  window.addEventListener("pointerup", stopPanelResize);
  window.addEventListener("pointercancel", stopPanelResize);
}

function resizePanelWithKeyboard(event: KeyboardEvent) {
  if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
  event.preventDefault();
  const { minimum, maximum } = panelWidthBounds();
  const step = event.shiftKey ? 40 : 16;
  if (event.key === "Home") contextPanelWidth.value = minimum;
  if (event.key === "End") contextPanelWidth.value = maximum;
  if (event.key === "ArrowLeft") {
    contextPanelWidth.value = clampContextPanelWidth(currentContextPanelWidth() + step);
  }
  if (event.key === "ArrowRight") {
    contextPanelWidth.value = clampContextPanelWidth(currentContextPanelWidth() - step);
  }
  persistContextPanelWidth();
}

function resetContextPanelWidth() {
  contextPanelWidth.value = null;
  window.localStorage.removeItem(CONTEXT_PANEL_WIDTH_STORAGE_KEY);
}

function restoreContextPanelWidth() {
  const storedWidth = Number(
    window.localStorage.getItem(CONTEXT_PANEL_WIDTH_STORAGE_KEY),
  );
  if (Number.isFinite(storedWidth) && storedWidth > 0) {
    contextPanelWidth.value = clampContextPanelWidth(storedWidth);
  }
}

function keepPanelWidthInBounds() {
  if (contextPanelWidth.value != null) {
    contextPanelWidth.value = clampContextPanelWidth(contextPanelWidth.value);
  }
}

async function loadSession() {
  if (!sessionUuid.value) return;
  loading.value = true;
  completionPending.value = false;
  error.value = "";
  let loaded = false;
  try {
    session.value = await getAssessmentSession(sessionUuid.value);
    optimisticUserTurn.value = null;
    streamingAiText.value = "";
    loaded = true;
    if (session.value.status === "completed") {
      await router.replace({
        path: `/assessment/report/${session.value.session_uuid}`,
        query: { fresh: "1" },
      });
      return;
    }
    if (isV32.value && session.value.phase === "opening_pending") {
      await startV32Opening();
    } else {
      syncPreparationPolling();
    }
    if (
      session.value.status === "generating" &&
      !isOnboarding.value &&
      session.value.phase !== "opening_pending"
    ) {
      void recoverGeneratingSession();
    }
  } catch (err) {
    error.value = "会话读取失败，请检查 session 是否存在或后端是否运行。";
  } finally {
    loading.value = false;
    if (loaded) await scrollToBottom("auto");
  }
}

async function recoverGeneratingSession() {
  if (!session.value || session.value.status !== "generating") return;
  const targetSessionUuid = session.value.session_uuid;
  const recoveryAttempt = ++generationRecoveryAttempt;
  completionPending.value = false;
  setStatus("正在确认当前处理进度...");
  const result = await pollSessionUntilSettled(
    targetSessionUuid,
    () => generationRecoveryAttempt === recoveryAttempt,
    0,
  );
  if (generationRecoveryAttempt !== recoveryAttempt) return;
  if (result.kind === "completed") {
    completionPending.value = true;
    setStatus("测评已完成，正在打开报告...");
    await router.push({
      path: `/assessment/report/${targetSessionUuid}`,
      query: { fresh: "1" },
    });
    return;
  }
  if (result.kind === "settled") {
    completionPending.value = false;
    setStatus("处理已完成，已恢复到最新进度。", 2500);
    return;
  }
  if (result.kind === "timeout") {
    setStatus("");
    error.value = "后台处理时间超过约 60 秒，已停止自动等待。请重新加载页面确认进度。";
  }
}

async function startV32Opening() {
  if (!session.value || openingStarting.value || session.value.phase !== "opening_pending") return;
  const previousAiTurnIndex = latestSpeakableAiTurn()?.turn_index ?? 0;
  openingStarting.value = true;
  streamingStartedAt.value = new Date().toISOString();
  streamingAiText.value = "";
  setStatus("罗杰斯教授正在结合你的日常身份开始第一轮对话...");
  try {
    await startAssessmentInterviewStream(session.value.session_uuid, (event) => {
      if (event.event === "agent_started") {
        setStatus("罗杰斯教授正在准备第一个问题...");
      } else if (event.event === "agent_delta") {
        streamingAiText.value += event.delta || "";
        setStatus("罗杰斯教授正在回复...");
        void followLatestIfAllowed();
      }
    });
    session.value = await getAssessmentSession(session.value.session_uuid);
    streamingAiText.value = "";
    setStatus("访谈已开始。", 1800);
    speakLatestQuestion(previousAiTurnIndex);
    await followLatestIfAllowed("auto");
  } catch {
    streamingAiText.value = "";
    error.value = "暂时无法开始访谈，请点击重新加载后重试。";
  } finally {
    openingStarting.value = false;
  }
}

function isOpeningStageQuestion(turn: DialogueTurnItem) {
  return (
    turn.speaker === "ai" &&
    turn.content_type === "stage_question" &&
    turn.turn_index === formalOpeningTurnIndex.value
  );
}

function isProfileTurn(turn: DialogueTurnItem) {
  return turn.content_type.startsWith("profile_");
}

function compactStageQuestion(turn: DialogueTurnItem) {
  if (turn.content_type !== "stage_question") return turn;
  const paragraphs = turn.content
    .split(/\n{2,}/)
    .map((item) => item.trim())
    .filter(Boolean);
  return { ...turn, content: paragraphs.at(-1) || turn.content };
}

async function submitAnswer() {
  if (!canSubmit.value || !session.value) return;
  if (isOnboarding.value) {
    await submitProfileAnswer();
    return;
  }
  const previousAiTurnIndex = latestSpeakableAiTurn()?.turn_index ?? 0;
  const targetSessionUuid = session.value.session_uuid;
  const currentSubmissionAttempt = ++submissionAttempt;
  submitting.value = true;
  error.value = "";
  stopSpeech();
  setStatus("回答正在提交...");
  let userTurnSaved = false;
  const pendingAnswer = answer.value.trim();
  pendingClientTurnId.value ||= crypto.randomUUID();
  const clientTurnId = pendingClientTurnId.value;
  const answerDurationMs = answerStartedAt.value
    ? Math.max(0, Date.now() - answerStartedAt.value)
    : null;
  answer.value = "";
  let watchdogActive = true;
  try {
    const streamRequest = submitAssessmentTurnStream(
      targetSessionUuid,
      {
        content: pendingAnswer,
        client_turn_id: clientTurnId,
        answer_duration_ms: answerDurationMs,
      },
      (event) => {
        if (submissionAttempt !== currentSubmissionAttempt) return;
        if (event.event === "user_turn_saved") {
          userTurnSaved = true;
          optimisticUserTurn.value = {
            turn_index: event.saved_turn_index || (turns.value.at(-1)?.turn_index || 0) + 1,
            speaker: "user",
            content: pendingAnswer,
            content_type: "scenario_answer",
            created_at: new Date().toISOString(),
          };
          beginResponseStatusSteps(false);
          void followLatestIfAllowed();
        } else if (event.event === "agent_started") {
          streamingStartedAt.value = new Date().toISOString();
          streamingAiText.value = "";
          if (!responseStatusTimers.size) {
            beginResponseStatusSteps(completionPending.value);
          }
        } else if (event.event === "agent_delta") {
          clearResponseStatusSteps();
          streamingAiText.value += event.delta || "";
          setStatus("罗杰斯教授已准备好下一问...");
          void followLatestIfAllowed();
        } else if (event.event === "agent_heartbeat") {
          setStatus(`罗杰斯教授正在整理回复，已等待 ${event.elapsed_seconds || 0} 秒...`);
        } else if (event.event === "agent_completed") {
          clearResponseStatusSteps();
          if (event.next_action === "generate_report") {
            completionPending.value = true;
            setStatus("最终回答已记录，正在生成报告...");
          } else {
            completionPending.value = false;
            setStatus("罗杰斯教授的下一问已生成，正在刷新对话...");
          }
        }
      },
    ).then(
      () => ({ kind: "stream_completed" as const }),
      (cause: unknown) => ({ kind: "stream_failed" as const, cause }),
    );
    const watchdogRequest = pollSessionUntilSettled(
      targetSessionUuid,
      () =>
        submissionAttempt === currentSubmissionAttempt &&
        watchdogActive,
      SUBMISSION_WATCHDOG_DELAY_MS,
    ).then((result) => ({ kind: "watchdog" as const, result }));
    const outcome = await Promise.race([streamRequest, watchdogRequest]);
    watchdogActive = false;
    if (submissionAttempt !== currentSubmissionAttempt) return;

    let latest: SessionResponse;
    if (outcome.kind === "stream_failed") {
      throw outcome.cause;
    }
    if (outcome.kind === "watchdog") {
      if (outcome.result.kind === "cancelled") return;
      if (outcome.result.kind === "timeout") {
        completionPending.value = false;
        answer.value = pendingAnswer;
        setStatus("");
        error.value =
          "处理时间超过约 60 秒，已停止自动等待。回答可能已保存，请重新加载页面确认进度。";
        return;
      }
      latest = outcome.result.latest;
    } else {
      latest = await getAssessmentSession(targetSessionUuid);
      if (latest.status === "generating") {
        const recovery = await pollSessionUntilSettled(
          targetSessionUuid,
          () => submissionAttempt === currentSubmissionAttempt,
          0,
        );
        if (recovery.kind === "cancelled") return;
        if (recovery.kind === "timeout") {
          completionPending.value = false;
          answer.value = pendingAnswer;
          setStatus("");
          error.value =
            "处理时间超过约 60 秒，已停止自动等待。回答可能已保存，请重新加载页面确认进度。";
          return;
        }
        latest = recovery.latest;
      }
    }
    session.value = latest;
    pendingClientTurnId.value = null;
    answerStartedAt.value = null;
    optimisticUserTurn.value = null;
    streamingAiText.value = "";
    if (latest.status === "completed") {
      completionPending.value = true;
      setStatus("测评已完成，正在打开报告...", 1500);
      await router.push({
        path: `/assessment/report/${targetSessionUuid}`,
        query: { fresh: "1" },
      });
      return;
    }
    completionPending.value = false;
    if ((latestSpeakableAiTurn()?.turn_index ?? 0) <= previousAiTurnIndex) {
      answer.value = pendingAnswer;
      error.value = "回答已保存，但下一轮内容尚未生成。请重新加载页面确认进度。";
      return;
    }
    speakLatestQuestion(previousAiTurnIndex);
    setStatus("回答已记录。", 2500);
    await followLatestIfAllowed();
  } catch {
    if (userTurnSaved && submissionAttempt === currentSubmissionAttempt) {
      const recovery = await pollSessionUntilSettled(
        targetSessionUuid,
        () => submissionAttempt === currentSubmissionAttempt,
        0,
      );
      if (recovery.kind === "completed") {
        session.value = recovery.latest;
        completionPending.value = true;
        pendingClientTurnId.value = null;
        answerStartedAt.value = null;
        optimisticUserTurn.value = null;
        streamingAiText.value = "";
        setStatus("测评已完成，正在打开报告...");
        await router.push({
          path: `/assessment/report/${targetSessionUuid}`,
          query: { fresh: "1" },
        });
        return;
      }
      if (recovery.kind === "settled") {
        session.value = recovery.latest;
        completionPending.value = false;
        if ((latestSpeakableAiTurn()?.turn_index ?? 0) > previousAiTurnIndex) {
          pendingClientTurnId.value = null;
          answerStartedAt.value = null;
          optimisticUserTurn.value = null;
          streamingAiText.value = "";
          speakLatestQuestion(previousAiTurnIndex);
          setStatus("回答已记录。", 2500);
          await followLatestIfAllowed();
          return;
        }
      }
      if (recovery.kind === "timeout") {
        completionPending.value = false;
        setStatus("");
        error.value =
          "处理时间超过约 60 秒，已停止自动等待。回答可能已保存，请重新加载页面确认进度。";
      }
    }
    if (!userTurnSaved || isProgressive.value) answer.value = pendingAnswer;
    if (!error.value) {
      error.value = "回答提交或下一问生成失败，请刷新会话确认是否已保存。";
    }
  } finally {
    clearResponseStatusSteps();
    watchdogActive = false;
    if (submissionAttempt === currentSubmissionAttempt) {
      submissionAttempt += 1;
      submitting.value = false;
    }
  }
}

async function submitProfileAnswer() {
  if (!session.value || !canSubmit.value) return;
  const previousAiTurnIndex = latestSpeakableAiTurn()?.turn_index ?? 0;
  submitting.value = true;
  error.value = "";
  stopSpeech();
  const pendingAnswer = answer.value.trim();
  answer.value = "";
  setStatus("正在了解你熟悉的任务与协作方式...");
  try {
    await submitProfileTurnStream(session.value.session_uuid, pendingAnswer, (event) => {
      if (event.event === "profile_answer_saved") {
        optimisticUserTurn.value = {
          turn_index: event.saved_turn_index || (turns.value.at(-1)?.turn_index || 0) + 1,
          speaker: "user",
          content: pendingAnswer,
          content_type: "profile_answer",
          created_at: new Date().toISOString(),
        };
        void followLatestIfAllowed();
      } else if (event.event === "profile_agent_started") {
        setStatus(event.message || "正在整理背景信息...");
      } else if (event.event === "profile_agent_completed") {
        setStatus("谢谢，我再了解一个小问题。", 1800);
      } else if (event.event === "profile_completed") {
        setStatus(
          isV32.value
            ? "背景了解完成，正在开始第一轮对话..."
            : "背景了解完成，正在完成情景适配...",
        );
      }
    });
    session.value = await getAssessmentSession(session.value.session_uuid);
    optimisticUserTurn.value = null;
    if (isV32.value && session.value.phase === "opening_pending") {
      await startV32Opening();
    } else {
      syncPreparationPolling();
      speakLatestQuestion(previousAiTurnIndex);
    }
    await followLatestIfAllowed();
  } catch {
    answer.value = pendingAnswer;
    error.value = "背景信息提交失败，请稍后重试。";
  } finally {
    submitting.value = false;
  }
}

function syncPreparationPolling() {
  if (isV32.value) {
    if (preparationPollTimer) clearInterval(preparationPollTimer);
    preparationPollTimer = null;
    return;
  }
  if (!session.value || !isOnboarding.value) {
    if (preparationPollTimer) clearInterval(preparationPollTimer);
    preparationPollTimer = null;
    return;
  }
  if (preparationPollTimer) return;
  preparationPollTimer = setInterval(async () => {
    if (!session.value) return;
    try {
      const preparation = await getAssessmentPreparation(session.value.session_uuid);
      if (session.value.scenario_preparation) {
        session.value.scenario_preparation = preparation.scenario_preparation;
        session.value.onboarding = preparation.onboarding;
        session.value.phase = preparation.phase;
      }
      if (preparation.assessment_ready) {
        const previousAiTurnIndex = latestSpeakableAiTurn()?.turn_index ?? 0;
        session.value = await getAssessmentSession(session.value.session_uuid);
        if (preparationPollTimer) clearInterval(preparationPollTimer);
        preparationPollTimer = null;
        setStatus(
          session.value.scenario_preparation?.fallback_used
            ? "个性化情景暂不可用，已切换为通用情景。"
            : "情景已准备完成，正式测评现在开始。",
          3500,
        );
        speakLatestQuestion(previousAiTurnIndex);
        await followLatestIfAllowed("auto");
      }
    } catch {
      // Keep the current dialogue usable; the next poll can recover a stale job.
    }
  }, 2000);
}

function latestSpeakableAiTurn() {
  return [...userVisibleTurns.value].reverse().find((turn) => turn.speaker === "ai");
}

function speechTurnPayload(turn: DialogueTurnItem) {
  if (!session.value || turn.turn_index < 0) return null;
  return {
    sessionUuid: session.value.session_uuid,
    turnIndex: turn.turn_index,
    text: turn.content,
  };
}

function speakLatestQuestion(afterTurnIndex = 0) {
  if (speechInputActive.value) return;
  const latestAiTurn = latestSpeakableAiTurn();
  if (!latestAiTurn?.content.trim() || latestAiTurn.turn_index <= afterTurnIndex) return;
  const payload = speechTurnPayload(latestAiTurn);
  if (payload) void speakTurn(payload);
}

function toggleInterviewerVoice() {
  if (speechInputActive.value) return;
  if (interviewerVoiceEnabled.value) {
    disableSpeech();
    setStatus("语音已关闭。", 1800);
  } else {
    const latestAiTurn = latestSpeakableAiTurn();
    const payload = latestAiTurn ? speechTurnPayload(latestAiTurn) : null;
    if (payload?.text.trim()) enableAndSpeakTurn(payload);
    else enableSpeech();
    setStatus("语音已打开。", 1800);
  }
}

function handleSpeechStateChange(state: SpeechInputState) {
  speechInputState.value = state;
  if (state === "starting") stopSpeech();
}

async function skipStage() {
  if (!session.value || !canSkipCurrentStage.value) return;
  const previousAiTurnIndex = latestSpeakableAiTurn()?.turn_index ?? 0;
  skipping.value = true;
  error.value = "";
  stopSpeech();
  try {
    const result = await skipCurrentAssessmentStage(session.value.session_uuid);
    session.value = await getAssessmentSession(session.value.session_uuid);
    if (result.next_action === "generate_report" || session.value.status === "completed") {
      await router.push({
        path: `/assessment/report/${session.value.session_uuid}`,
        query: { fresh: "1" },
      });
      return;
    }
    setStatus(result.message, 3000);
    speakLatestQuestion(previousAiTurnIndex);
    await followLatestIfAllowed();
  } catch {
    error.value = "暂时无法跳过当前题目，请刷新会话后重试。";
  } finally {
    skipping.value = false;
  }
}

async function continueStage() {
  if (!session.value || !waitingForStageChoice.value || continuing.value) return;
  const previousAiTurnIndex = latestSpeakableAiTurn()?.turn_index ?? 0;
  continuing.value = true;
  error.value = "";
  stopSpeech();
  try {
    const result = await continueCurrentAssessmentStage(session.value.session_uuid);
    session.value = await getAssessmentSession(session.value.session_uuid);
    setStatus(result.message, 2600);
    speakLatestQuestion(previousAiTurnIndex);
    await followLatestIfAllowed();
  } catch {
    error.value = "暂时无法继续补充，请刷新会话后重试。";
  } finally {
    continuing.value = false;
  }
}

async function requestFinishSession() {
  if (!session.value || finishing.value) return;
  const progressNote = isProgressive.value
    ? `当前已回答 ${formalAnswerCount.value} 轮，通常需要 ${targetMinAnswers.value}–${targetMaxAnswers.value} 轮。`
    : "当前测评尚未自然结束。";
  const confirmed = window.confirm(
    `${progressNote}\n提前结束可能导致部分维度显示“证据不足”。确定现在结束并生成报告吗？`,
  );
  if (!confirmed) return;
  await finishSession();
}

async function finishSession() {
  if (!session.value || finishing.value) return;
  finishing.value = true;
  error.value = "";
  stopSpeech();
  try {
    await finishAssessmentSession(session.value.session_uuid);
    await router.push({
      path: `/assessment/report/${session.value.session_uuid}`,
      query: { fresh: "1" },
    });
  } catch (err) {
    error.value = "结束测评失败，请稍后重试。";
  } finally {
    finishing.value = false;
  }
}

function transcriptIsNearBottom() {
  const transcript = transcriptRef.value;
  if (!transcript) return true;
  return (
    transcript.scrollHeight - transcript.scrollTop - transcript.clientHeight <=
    TRANSCRIPT_BOTTOM_THRESHOLD
  );
}

function handleTranscriptScroll() {
  const isNearBottom = transcriptIsNearBottom();
  autoFollowTranscript.value = isNearBottom;
  if (isNearBottom) hasUnseenTurns.value = false;
}

async function followLatestIfAllowed(behavior: ScrollBehavior = "auto") {
  await nextTick();
  if (!autoFollowTranscript.value) {
    hasUnseenTurns.value = true;
    return;
  }
  await scrollToBottom(behavior);
}

async function reviewConversationHistory() {
  profileHistoryExpanded.value = profileTurns.value.length > 0;
  autoFollowTranscript.value = false;
  hasUnseenTurns.value = false;
  await nextTick();
  transcriptRef.value?.scrollTo({ top: 0, behavior: "auto" });
}

function handleProfileHistoryToggle(event: Event) {
  const isOpen = (event.currentTarget as HTMLDetailsElement).open;
  profileHistoryExpanded.value = isOpen;
  if (isOpen) {
    autoFollowTranscript.value = false;
  }
}

async function scrollToBottom(behavior: ScrollBehavior = "auto") {
  await nextTick();
  const transcript = transcriptRef.value;
  transcript?.scrollTo({
    top: transcript.scrollHeight,
    behavior,
  });
  autoFollowTranscript.value = true;
  hasUnseenTurns.value = false;
}

function turnKey(turn: DialogueTurnItem) {
  return turn.turn_index === STREAMING_AI_TURN_INDEX ? "streaming-ai" : String(turn.turn_index);
}

function speakerLabel(speaker: string) {
  if (speaker === "ai") return "罗杰斯教授";
  if (speaker === "user") return session.value?.participant_nickname || "受测者";
  return "系统";
}

function applyPrompt(prompt: string) {
  answer.value = answer.value ? `${answer.value}\n${prompt}` : prompt;
}

function formatSeconds(totalSeconds: number) {
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

onMounted(() => {
  restoreContextPanelWidth();
  window.addEventListener("resize", keepPanelWidthInBounds);
  interviewTimer = setInterval(() => {
    timerNow.value = Date.now();
  }, 1000);
  void loadSession();
});
onBeforeUnmount(() => {
  submissionAttempt += 1;
  generationRecoveryAttempt += 1;
  stopSpeech();
  stopPanelResize();
  window.removeEventListener("resize", keepPanelWidthInBounds);
  if (preparationPollTimer) clearInterval(preparationPollTimer);
  if (interviewTimer) clearInterval(interviewTimer);
  for (const timer of watchdogTimers) clearTimeout(timer);
  watchdogTimers.clear();
  clearResponseStatusSteps();
});
</script>

<template>
  <div class="interview-room-page">
    <main class="interview-shell">
      <template v-if="loading || (!session && !error)">
        <header class="interview-loading-topbar" aria-hidden="true">
          <i></i><span></span><em></em>
        </header>
        <div class="interview-loading-scene" aria-hidden="true"><i></i></div>
        <section class="interview-loading-content" aria-label="正在读取测评会话">
          <div class="interview-loading-dialogue">
            <i></i><i></i><i></i>
          </div>
          <aside class="interview-loading-context">
            <i></i><i></i><i></i>
          </aside>
        </section>
        <div class="interview-loading-input" aria-hidden="true"><i></i><span></span></div>
      </template>
      <section v-else-if="!session" class="interview-load-error">
        <p>{{ error }}</p>
        <button type="button" @click="loadSession">重新加载</button>
      </section>
      <template v-else>
      <header class="interview-topbar">
        <div class="interview-brand">
          <span class="interview-logo">罗</span>
          <h1>审辩式思维动态测评</h1>
        </div>
        <div class="interview-toolbar">
          <span class="interview-stage-meta">{{ stageMeta }}</span>
          <span v-if="progressiveRemainingText" class="interview-progress-detail">
            {{ progressiveRemainingText }}
          </span>
          <span class="interview-timer" aria-label="测评计时">
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <circle cx="12" cy="12" r="8"></circle>
              <path d="M12 7v5l4 2"></path>
            </svg>
            {{ timerText }}
          </span>
          <div v-if="speechPlaybackSupported" class="interview-voice-controls">
            <button
              class="interview-read-button"
              type="button"
              :aria-pressed="interviewerVoiceEnabled"
              :disabled="speechInputActive"
              @click="toggleInterviewerVoice"
            >
              {{ interviewerVoiceEnabled ? "关闭语音" : "打开语音" }}
            </button>
            <small v-if="speechNotice" class="is-error" role="status">
              {{ speechNotice }}
            </small>
          </div>
          <button
            v-if="!isOnboarding"
            class="interview-end-button"
            type="button"
            :disabled="finishing || submitting || !session || session.status === 'generating' || session.status === 'completed'"
            @click="requestFinishSession"
          >
            {{ finishing ? "生成中" : "结束测评" }}
          </button>
        </div>
      </header>

      <section
        ref="chatZoneRef"
        class="interview-chat-zone"
        :class="{
          'is-panel-resizing': isPanelResizing,
          'is-progressive': isProgressive,
        }"
        :style="chatZoneStyle"
        aria-label="访谈对话区"
      >
        <div id="assessment-dialogue-panel" class="interview-message-column">
          <div class="interview-status-row">
            <p v-if="error" class="interview-alert">{{ error }}</p>
            <div
              v-else-if="statusIndicatorState"
              class="interview-status-indicator"
              :class="`is-${statusIndicatorState}`"
              role="status"
              :aria-label="statusAriaText"
            >
              <span class="sr-only">{{ statusAriaText }}</span>
              <span
                v-if="statusIndicatorState === 'thinking'"
                class="interview-thinking-dots"
                aria-hidden="true"
              >
                <i></i><i></i><i></i>
              </span>
              <span v-else class="interview-success-mark" aria-hidden="true">
                <svg viewBox="0 0 24 24">
                  <path d="m6.5 12.5 3.3 3.2 7.7-8"></path>
                </svg>
              </span>
            </div>
            <nav
              v-if="canReviewHistory"
              class="interview-history-actions"
              aria-label="对话记录导航"
            >
              <button type="button" @click="reviewConversationHistory">
                回看历史
              </button>
              <button
                v-if="!autoFollowTranscript || hasUnseenTurns"
                type="button"
                class="is-latest"
                @click="scrollToBottom('auto')"
              >
                {{ hasUnseenTurns ? "有新消息 · 回到最新" : "回到最新" }}
              </button>
            </nav>
          </div>

          <div
            ref="transcriptRef"
            class="interview-transcript"
            @scroll.passive="handleTranscriptScroll"
          >
            <details
              v-if="!isOnboarding && profileTurns.length > 0"
              class="interview-profile-history"
              :open="profileHistoryExpanded"
              @toggle="handleProfileHistoryToggle"
            >
              <summary>
                <span>背景了解（{{ profileTurns.length }} 条）</span>
                <em>不计入正式评分</em>
              </summary>
              <div class="interview-profile-turns">
                <DialogueTurn
                  v-for="turn in profileTurns"
                  :key="`profile-${turn.turn_index}`"
                  :turn="turn"
                  :speaker-label="speakerLabel(turn.speaker)"
                />
              </div>
            </details>
            <template v-if="!isProgressive">
              <DialogueTurn
                v-for="turn in compactOpeningMessages"
                :key="`intro-${turn.turn_index}`"
                :turn="turn"
                :speaker-label="speakerLabel(turn.speaker)"
              />
            </template>
            <DialogueTurn
              v-for="turn in displayTurns"
              :key="turnKey(turn)"
              :turn="turn"
              :speaker-label="speakerLabel(turn.speaker)"
              :streaming="turn.speaker === 'ai' && turn.turn_index === STREAMING_AI_TURN_INDEX"
            />
            <div v-if="compactOpeningMessages.length === 0 && displayTurns.length === 0" class="interview-empty">
              罗杰斯教授正在准备开场问题。
            </div>
          </div>
        </div>

        <div
          v-if="!isProgressive"
          class="interview-panel-resizer"
          role="separator"
          tabindex="0"
          aria-label="调整对话区和资料板宽度"
          aria-orientation="vertical"
          aria-controls="assessment-dialogue-panel assessment-context-panel"
          :aria-valuemin="MIN_CONTEXT_PANEL_WIDTH"
          :aria-valuemax="panelWidthBounds().maximum"
          :aria-valuenow="Math.round(currentContextPanelWidth())"
          title="拖动调整宽度；双击恢复默认"
          @pointerdown="startPanelResize"
          @keydown="resizePanelWithKeyboard"
          @dblclick="resetContextPanelWidth"
        >
          <span aria-hidden="true"></span>
        </div>

        <aside
          v-if="!isProgressive"
          id="assessment-context-panel"
          class="interview-context-card"
          aria-label="当前测评情境资料板"
        >
          <template v-if="isOnboarding">
            <div class="context-card-header">
              <span class="context-pill">测评前准备</span>
              <span class="context-card-meta">不计入六维评分</span>
            </div>
            <div class="context-card-scroll">
              <section class="context-section">
                <span>背景了解</span>
                <h2>让情景更容易理解</h2>
                <p class="context-background">
                  我们只了解你熟悉的任务类型、协作角色和判断场面。请不要提供单位、地点或真实人物信息。
                </p>
              </section>
              <section class="context-section context-stage-note">
                <span>情景准备状态</span>
                <strong>{{ session.scenario_preparation?.message || "正在准备职业基础情景" }}</strong>
                <p>
                  六阶段能力结构、追问次数和评分规则保持固定；职业信息只改变情景表层内容。
                </p>
              </section>
            </div>
            <div class="context-current-block">
              <section class="context-section context-stage-main-question">
                <span>{{ isWaitingForScenario ? "请稍候" : "当前问题" }}</span>
                <p>
                  {{
                    isWaitingForScenario
                      ? "背景访谈已经完成，系统正在进行最后的情景一致性检查。"
                      : "请按自己的真实经历简短回答；这些内容不会进入正式评分或个人报告。"
                  }}
                </p>
              </section>
            </div>
            <div class="context-progress">
              <div class="context-progress-head">
                <span>背景问题 {{ session.onboarding?.question_count || 1 }}/{{ session.onboarding?.max_questions || 3 }}</span>
                <em>{{ session.onboarding?.completed ? "已完成" : "进行中" }}</em>
              </div>
            </div>
          </template>
          <template v-else>
          <div class="context-card-header">
            <span class="context-pill">资料板</span>
            <span class="context-card-meta">{{ stageEstimateText }}</span>
          </div>
          <div class="context-card-scroll">
            <section class="context-section">
              <span>情境背景</span>
              <h2>{{ scenarioTitle }}</h2>
              <p class="context-background">{{ scenarioBackground }}</p>
            </section>
            <section v-if="currentStageContext" class="context-section context-stage-note">
              <span>当前已知信息</span>
              <strong>{{ stageTitle }}</strong>
              <p>{{ currentStageContext }}</p>
            </section>
          </div>
          <div class="context-current-block">
            <section class="context-section context-stage-main-question">
              <span>阶段主问题</span>
              <p>{{ currentMainQuestion }}</p>
            </section>
            <div
              v-if="waitingForStageChoice || canSkipCurrentStage"
              class="context-question-actions"
            >
              <button
                v-if="waitingForStageChoice"
                type="button"
                :disabled="continuing"
                @click="continueStage"
              >
                {{ continuing ? "正在准备" : "继续补充" }}
              </button>
              <button
                v-if="canSkipCurrentStage"
                class="context-skip-button"
                type="button"
                :disabled="skipping"
                @click="skipStage"
              >
                {{ skipping ? "正在进入" : "进入下一阶段" }}
              </button>
            </div>
          </div>
          <div class="context-progress">
            <div class="context-progress-head">
              <span>{{ progressCountText }}</span>
              <em>{{ followupLimitText }}</em>
            </div>
            <div class="context-bars" aria-hidden="true">
              <i
                v-for="segment in progressSegments"
                :key="segment"
                :class="{ active: segment <= currentStageOrder }"
              ></i>
            </div>
          </div>
          </template>
        </aside>
      </section>

      <AnswerComposer
        v-model="answer"
        :session-uuid="sessionUuid"
        :can-submit="canSubmit"
        :submitting="submitting"
        :busy="finishing || skipping || continuing || submitting || isWaitingForScenario || session.status === 'generating' || session.status === 'completed'"
        :is-debug="isDebug"
        :prompts="actionPrompts"
        @submit="submitAnswer"
        @apply-prompt="applyPrompt"
        @speech-state-change="handleSpeechStateChange"
      />
      </template>
    </main>
  </div>
</template>
