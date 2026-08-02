<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { transcribeAssessmentSpeech } from "../../api/session";

interface SpeechRecognitionAlternativeLike {
  transcript: string;
}

interface SpeechRecognitionResultLike {
  isFinal: boolean;
  0: SpeechRecognitionAlternativeLike;
}

interface SpeechRecognitionEventLike extends Event {
  results: ArrayLike<SpeechRecognitionResultLike>;
}

interface SpeechRecognitionErrorEventLike extends Event {
  error: string;
}

interface SpeechRecognitionLike {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  onstart: (() => void) | null;
  onend: (() => void) | null;
  onresult: ((event: SpeechRecognitionEventLike) => void) | null;
  onerror: ((event: SpeechRecognitionErrorEventLike) => void) | null;
  start: () => void;
  stop: () => void;
  abort: () => void;
}

type SpeechRecognitionConstructor = new () => SpeechRecognitionLike;
type SpeechInputMode = "recognition" | "recording" | null;
type SpeechRecognitionState =
  | "unsupported"
  | "ready"
  | "starting"
  | "listening"
  | "stopping"
  | "review"
  | "error";

const props = defineProps<{
  sessionUuid: string;
  modelValue: string;
  canSubmit: boolean;
  submitting: boolean;
  busy: boolean;
  isDebug?: boolean;
  prompts?: string[];
}>();

const emit = defineEmits<{
  "update:modelValue": [value: string];
  submit: [];
  "apply-prompt": [prompt: string];
  "speech-state-change": [state: SpeechRecognitionState];
  "listening-change": [isListening: boolean];
}>();

const speechState = ref<SpeechRecognitionState>("unsupported");
const speechSupported = ref(false);
const speechInputMode = ref<SpeechInputMode>(null);
const speechMessage = ref("");
const speechBaseText = ref("");
const recognizedDraftText = ref("");
const hasRecognizedSpeech = ref(false);
const discardRecognitionResults = ref(false);
let RecognitionConstructor: SpeechRecognitionConstructor | null = null;
let recognition: SpeechRecognitionLike | null = null;
let activeRecognitionAttempt = 0;
let recognitionStartTimer: ReturnType<typeof setTimeout> | null = null;
let mediaRecorder: MediaRecorder | null = null;
let recordingStream: MediaStream | null = null;
let recordingChunks: Blob[] = [];
let recordingSize = 0;
let recordingMimeType = "";
let recordingLimitExceeded = false;
let recordingStopTimer: ReturnType<typeof setTimeout> | null = null;
let transcriptionController: AbortController | null = null;
let componentUnmounted = false;
const RECOGNITION_START_TIMEOUT_MS = 8000;
const MICROPHONE_START_TIMEOUT_MS = 8000;
const MAX_RECORDING_DURATION_MS = 60_000;
const MAX_RECORDING_BYTES = 5 * 1024 * 1024;
const RECORDING_MIME_CANDIDATES = [
  "audio/ogg;codecs=opus",
  "audio/webm;codecs=opus",
  "audio/mp4",
] as const;

const isListening = computed(() => isActiveSpeechState(speechState.value));
const micDisabled = computed(
  () =>
    !speechSupported.value ||
    props.submitting ||
    props.busy ||
    speechState.value === "starting" ||
    speechState.value === "stopping",
);
const submitEnabled = computed(() => props.canSubmit && !isListening.value);
const micLabel = computed(() => {
  if (speechInputMode.value === "recording") {
    const recordingLabels: Record<SpeechRecognitionState, string> = {
      unsupported: "当前浏览器不支持语音输入",
      ready: "开始录音转文字",
      starting: "正在启动麦克风",
      listening: "停止录音并转写",
      stopping: "正在转写录音",
      review: "继续录音转文字",
      error: "重试录音转文字",
    };
    return recordingLabels[speechState.value];
  }
  const labels: Record<SpeechRecognitionState, string> = {
    unsupported: "当前浏览器不支持语音转文字，请直接输入文字",
    ready: "开始语音转文字",
    starting: "正在启动语音转文字",
    listening: "停止语音转文字",
    stopping: "正在停止语音转文字",
    review: "继续语音转文字",
    error: "重试语音转文字",
  };
  return labels[speechState.value];
});
const micButtonText = computed(() => {
  const labels: Record<SpeechRecognitionState, string> = {
    unsupported: "不可用",
    ready: "开始",
    starting: "启动中",
    listening: "停止",
    stopping: speechInputMode.value === "recording" ? "转写中" : "停止中",
    review: "再说",
    error: "重试",
  };
  return labels[speechState.value];
});
const speechStatusText = computed(() => {
  if (speechMessage.value) return speechMessage.value;
  if (speechInputMode.value === "recording") {
    const recordingMessages: Record<SpeechRecognitionState, string> = {
      unsupported: "当前浏览器不支持语音输入，请直接输入文字。",
      ready: "语音输入已准备好。",
      starting: "正在请求麦克风权限，请稍候。",
      listening: "正在录音；音频仅用于本次转写、不保存，文字不会自动发送。",
      stopping: "正在转写录音，请稍候。",
      review: "转写已完成，请检查或修改文字，再按 Enter 或点击发送。",
      error: "语音输入出现问题，可点击“重试”或继续使用文字输入。",
    };
    return recordingMessages[speechState.value];
  }
  const messages: Record<SpeechRecognitionState, string> = {
    unsupported: "当前浏览器不支持语音转文字，请直接输入文字。",
    ready: "语音转文字已准备好，点击“开始”后说话。",
    starting: "正在启动麦克风，请稍候。",
    listening: "正在聆听；点击“停止”或按 Enter 结束，识别文字不会自动发送。",
    stopping: "正在结束识别，完成后可以检查和修改文字。",
    review: "识别已结束，请检查或修改文字，再按 Enter 或点击发送。",
    error: "语音识别出现问题，可点击“重试”或继续使用文字输入。",
  };
  return messages[speechState.value];
});
const showSpeechStatus = computed(() =>
  ["starting", "listening", "stopping", "review", "error"].includes(
    speechState.value,
  ),
);

function updateValue(event: Event) {
  if (isListening.value) {
    discardRecognitionResults.value = true;
    if (speechInputMode.value === "recognition" && speechState.value === "listening") {
      stopListening();
    } else {
      abortListening();
    }
  }
  emit("update:modelValue", (event.target as HTMLTextAreaElement).value);
}

function handleKeydown(event: KeyboardEvent) {
  if (event.key !== "Enter" || event.shiftKey || event.isComposing) return;
  event.preventDefault();
  if (isListening.value) {
    if (speechState.value === "listening") stopListening();
    else if (speechState.value === "starting") abortListening();
    return;
  }
  if (submitEnabled.value) emit("submit");
}

function submitForm() {
  if (isListening.value) {
    if (speechState.value === "listening") stopListening();
    else if (speechState.value === "starting") abortListening();
    return;
  }
  if (submitEnabled.value) emit("submit");
}

function recognitionConstructor(): SpeechRecognitionConstructor | null {
  const speechWindow = window as typeof window & {
    SpeechRecognition?: SpeechRecognitionConstructor;
    webkitSpeechRecognition?: SpeechRecognitionConstructor;
  };
  return speechWindow.SpeechRecognition || speechWindow.webkitSpeechRecognition || null;
}

function setupRecognition() {
  const Constructor = recognitionConstructor();
  if (Constructor) {
    RecognitionConstructor = Constructor;
    speechInputMode.value = "recognition";
    speechSupported.value = true;
    setSpeechState("ready");
    return;
  }

  if (recordingFallbackSupported()) {
    speechInputMode.value = "recording";
    speechSupported.value = true;
    setSpeechState("ready");
    return;
  }

  speechInputMode.value = null;
  speechSupported.value = false;
  setSpeechState("unsupported", true);
}

function recordingFallbackSupported() {
  return Boolean(
    props.sessionUuid &&
      typeof navigator.mediaDevices?.getUserMedia === "function" &&
      typeof window.MediaRecorder !== "undefined",
  );
}

function createRecognition(attempt: number) {
  if (!RecognitionConstructor) return null;
  const nextRecognition = new RecognitionConstructor();
  const isCurrentAttempt = () =>
    activeRecognitionAttempt === attempt && recognition === nextRecognition;

  nextRecognition.continuous = true;
  nextRecognition.interimResults = true;
  nextRecognition.lang = "zh-CN";
  nextRecognition.onstart = () => {
    if (!isCurrentAttempt()) {
      try {
        nextRecognition.abort();
      } catch {
        // Ignore a late start event from an already-cancelled Safari session.
      }
      return;
    }
    if (speechState.value !== "starting") {
      try {
        nextRecognition.abort();
      } catch {
        // Ignore a late start event from an already-cancelled Safari session.
      }
      return;
    }
    clearRecognitionStartTimer();
    setSpeechState("listening");
    hasRecognizedSpeech.value = false;
    discardRecognitionResults.value = false;
    recognizedDraftText.value = speechBaseText.value;
    speechMessage.value = "";
  };
  nextRecognition.onresult = (event) => {
    if (
      !isCurrentAttempt() ||
      discardRecognitionResults.value ||
      !["listening", "stopping"].includes(speechState.value)
    ) {
      return;
    }
    const finalSegments: string[] = [];
    let interimText = "";
    for (let index = 0; index < event.results.length; index += 1) {
      const text = applySpokenPunctuation(event.results[index][0]?.transcript || "");
      if (event.results[index].isFinal) finalSegments.push(text);
      else interimText += text;
    }
    const finalText = joinFinalSpeechSegments(finalSegments, Boolean(interimText.trim()));
    hasRecognizedSpeech.value = Boolean(finalText.trim() || interimText.trim());
    recognizedDraftText.value = joinSpeechText(
      speechBaseText.value,
      finalText + interimText.trimStart(),
    );
    emit("update:modelValue", recognizedDraftText.value);
  };
  nextRecognition.onerror = (event) => {
    if (!isCurrentAttempt()) return;
    if (
      event.error === "aborted" &&
      discardRecognitionResults.value &&
      !isActiveSpeechState(speechState.value)
    ) {
      return;
    }
    clearRecognitionStartTimer();
    activeRecognitionAttempt += 1;
    recognition = null;
    setSpeechState("error");
    speechMessage.value = speechErrorMessage(event.error);
    try {
      nextRecognition.abort();
    } catch {
      // The browser may already have closed the failed recognition session.
    }
  };
  nextRecognition.onend = () => {
    if (!isCurrentAttempt()) return;
    clearRecognitionStartTimer();
    const previousState = speechState.value;
    const wasListening = isActiveSpeechState(previousState);
    if (!wasListening) return;
    recognition = null;
    if (
      hasRecognizedSpeech.value &&
      !discardRecognitionResults.value
    ) {
      recognizedDraftText.value = ensureTerminalPunctuation(recognizedDraftText.value);
      emit("update:modelValue", recognizedDraftText.value);
      speechMessage.value = "";
      setSpeechState("review");
    } else if (discardRecognitionResults.value) {
      speechMessage.value = "已停止语音输入，你可以继续修改文字。";
      setSpeechState("review");
    } else {
      speechMessage.value = "没有识别到语音，未发送任何内容。";
      setSpeechState("ready");
    }
  };
  return nextRecognition;
}

function toggleListening() {
  if (micDisabled.value) return;
  if (!speechSupported.value || !speechInputMode.value) {
    speechMessage.value = "当前浏览器不支持语音输入，请使用文字输入。";
    setSpeechState("unsupported", true);
    return;
  }
  if (speechState.value === "listening") {
    stopListening();
    return;
  }
  if (speechState.value === "stopping") {
    return;
  }
  if (speechInputMode.value === "recording") {
    void startRecording();
    return;
  }
  if (!RecognitionConstructor) {
    speechMessage.value = "当前浏览器不支持语音转文字，请使用文字输入。";
    setSpeechState("unsupported", true);
    return;
  }
  if (recognition) {
    discardRecognitionResults.value = true;
    cancelRecognition();
  }
  speechBaseText.value = props.modelValue.trimEnd();
  recognizedDraftText.value = speechBaseText.value;
  hasRecognizedSpeech.value = false;
  discardRecognitionResults.value = false;
  speechMessage.value = "";
  setSpeechState("starting");
  const attempt = activeRecognitionAttempt + 1;
  activeRecognitionAttempt = attempt;
  try {
    const nextRecognition = createRecognition(attempt);
    if (!nextRecognition) throw new Error("Speech recognition is unavailable.");
    recognition = nextRecognition;
    recognitionStartTimer = setTimeout(() => {
      if (activeRecognitionAttempt !== attempt || recognition !== nextRecognition) return;
      discardRecognitionResults.value = true;
      cancelRecognition();
      setSpeechState("error");
      speechMessage.value =
        "麦克风启动超时，请检查权限后重试，或继续使用文字输入。";
    }, RECOGNITION_START_TIMEOUT_MS);
    nextRecognition.start();
  } catch {
    cancelRecognition();
    setSpeechState("error");
    speechMessage.value = "语音识别暂时无法启动，请稍后重试或使用文字输入。";
  }
}

function stopListening() {
  if (speechInputMode.value === "recording") {
    stopRecording();
    return;
  }
  if (!recognition || speechState.value !== "listening") return;
  setSpeechState("stopping");
  try {
    recognition.stop();
  } catch {
    discardRecognitionResults.value = true;
    cancelRecognition();
    setSpeechState("error");
    speechMessage.value = "语音识别无法正常停止，请检查文字后重试。";
  }
}

async function startRecording() {
  cancelRecordingAndTranscription();
  speechBaseText.value = props.modelValue.trimEnd();
  recognizedDraftText.value = speechBaseText.value;
  hasRecognizedSpeech.value = false;
  discardRecognitionResults.value = false;
  speechMessage.value = "";
  recordingChunks = [];
  recordingSize = 0;
  recordingLimitExceeded = false;
  setSpeechState("starting");
  const attempt = activeRecognitionAttempt + 1;
  activeRecognitionAttempt = attempt;

  try {
    const stream = await getRecordingStream(attempt);
    if (
      componentUnmounted ||
      attempt !== activeRecognitionAttempt ||
      speechState.value !== "starting" ||
      props.busy ||
      props.submitting
    ) {
      stopMediaTracks(stream);
      return;
    }

    recordingStream = stream;
    recordingMimeType = preferredRecordingMimeType();
    const Recorder = window.MediaRecorder;
    const recorder = recordingMimeType
      ? new Recorder(stream, { mimeType: recordingMimeType })
      : new Recorder(stream);
    mediaRecorder = recorder;

    recorder.ondataavailable = (event) => {
      if (attempt !== activeRecognitionAttempt || discardRecognitionResults.value) return;
      if (!event.data.size) return;
      recordingChunks.push(event.data);
      recordingSize += event.data.size;
      if (recordingSize > MAX_RECORDING_BYTES && speechState.value === "listening") {
        recordingLimitExceeded = true;
        speechMessage.value = "录音已达到 5MB 上限，正在停止。";
        stopRecording();
      }
    };
    recorder.onerror = () => {
      if (attempt !== activeRecognitionAttempt) return;
      activeRecognitionAttempt += 1;
      cancelRecordingAndTranscription();
      setSpeechState("error");
      speechMessage.value = "录音失败，请检查麦克风后重试。";
    };
    recorder.onstop = () => {
      void finishRecording(attempt, recorder);
    };

    recorder.start(1000);
    setSpeechState("listening");
    recordingStopTimer = setTimeout(() => {
      if (attempt !== activeRecognitionAttempt || speechState.value !== "listening") return;
      speechMessage.value = "已达到 60 秒录音上限，正在转写。";
      stopRecording();
    }, MAX_RECORDING_DURATION_MS);
  } catch (error) {
    if (attempt !== activeRecognitionAttempt || componentUnmounted) return;
    cancelRecordingAndTranscription();
    setSpeechState("error");
    speechMessage.value = recordingStartErrorMessage(error);
  }
}

function getRecordingStream(attempt: number) {
  let settled = false;
  let timedOut = false;
  const request = navigator.mediaDevices.getUserMedia({
    audio: {
      channelCount: 1,
      echoCancellation: true,
      noiseSuppression: true,
    },
  });
  return new Promise<MediaStream>((resolve, reject) => {
    const timer = setTimeout(() => {
      if (settled) return;
      timedOut = true;
      settled = true;
      const error = new Error("Microphone request timed out.");
      error.name = "TimeoutError";
      reject(error);
    }, MICROPHONE_START_TIMEOUT_MS);
    request.then(
      (stream) => {
        if (timedOut || componentUnmounted || attempt !== activeRecognitionAttempt) {
          stopMediaTracks(stream);
          return;
        }
        if (settled) {
          stopMediaTracks(stream);
          return;
        }
        settled = true;
        clearTimeout(timer);
        resolve(stream);
      },
      (error) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        reject(error);
      },
    );
  });
}

function stopRecording() {
  const recorder = mediaRecorder;
  if (!recorder || speechState.value !== "listening") return;
  clearRecordingStopTimer();
  setSpeechState("stopping");
  if (!speechMessage.value) speechMessage.value = "录音已停止，正在转写。";
  try {
    recorder.stop();
  } catch {
    activeRecognitionAttempt += 1;
    cancelRecordingAndTranscription();
    setSpeechState("error");
    speechMessage.value = "录音无法正常停止，请重试或使用文字输入。";
  } finally {
    stopCurrentMediaTracks();
  }
}

async function finishRecording(attempt: number, recorder: MediaRecorder) {
  if (attempt !== activeRecognitionAttempt || discardRecognitionResults.value) return;
  clearRecordingStopTimer();
  if (mediaRecorder === recorder) mediaRecorder = null;
  stopCurrentMediaTracks();

  if (recordingLimitExceeded || recordingSize > MAX_RECORDING_BYTES) {
    recordingChunks = [];
    setSpeechState("error");
    speechMessage.value = "录音超过 5MB，请缩短内容后重试。";
    return;
  }

  const mimeType = recorder.mimeType || recordingMimeType || recordingChunks[0]?.type;
  const audio = new Blob(recordingChunks, { type: mimeType || "application/octet-stream" });
  recordingChunks = [];
  if (!audio.size) {
    setSpeechState("ready");
    speechMessage.value = "没有录到声音，未发送任何内容。";
    return;
  }

  const controller = new AbortController();
  transcriptionController = controller;
  try {
    const result = await transcribeAssessmentSpeech(props.sessionUuid, audio, controller.signal);
    if (
      componentUnmounted ||
      attempt !== activeRecognitionAttempt ||
      transcriptionController !== controller
    ) {
      return;
    }
    const text = result.text.trim();
    if (!text) {
      setSpeechState("ready");
      speechMessage.value = "没有识别到语音，未发送任何内容。";
      return;
    }
    recognizedDraftText.value = ensureTerminalPunctuation(
      joinSpeechText(speechBaseText.value, text),
    );
    hasRecognizedSpeech.value = true;
    emit("update:modelValue", recognizedDraftText.value);
    speechMessage.value = "";
    setSpeechState("review");
  } catch (error) {
    if (controller.signal.aborted || attempt !== activeRecognitionAttempt) return;
    setSpeechState("error");
    speechMessage.value = transcriptionErrorMessage(error);
  } finally {
    if (transcriptionController === controller) transcriptionController = null;
  }
}

function preferredRecordingMimeType() {
  const Recorder = window.MediaRecorder;
  if (typeof Recorder.isTypeSupported !== "function") return "";
  return RECORDING_MIME_CANDIDATES.find((type) => Recorder.isTypeSupported(type)) || "";
}

function clearRecordingStopTimer() {
  if (!recordingStopTimer) return;
  clearTimeout(recordingStopTimer);
  recordingStopTimer = null;
}

function stopMediaTracks(stream: MediaStream | null) {
  stream?.getTracks().forEach((track) => track.stop());
}

function stopCurrentMediaTracks() {
  stopMediaTracks(recordingStream);
  recordingStream = null;
}

function cancelRecordingAndTranscription() {
  clearRecordingStopTimer();
  transcriptionController?.abort();
  transcriptionController = null;
  const recorder = mediaRecorder;
  mediaRecorder = null;
  if (recorder && recorder.state !== "inactive") {
    try {
      recorder.stop();
    } catch {
      // The recorder may already have stopped while its final event is queued.
    }
  }
  stopCurrentMediaTracks();
  recordingChunks = [];
  recordingSize = 0;
  recordingLimitExceeded = false;
}

function abortListening() {
  if (!recognition && !mediaRecorder && !transcriptionController && !isListening.value) return;
  discardRecognitionResults.value = true;
  cancelRecognition();
  cancelRecordingAndTranscription();
  setSpeechState(speechSupported.value ? "ready" : "unsupported");
}

function clearRecognitionStartTimer() {
  if (!recognitionStartTimer) return;
  clearTimeout(recognitionStartTimer);
  recognitionStartTimer = null;
}

function cancelRecognition() {
  const recognitionToAbort = recognition;
  activeRecognitionAttempt += 1;
  recognition = null;
  clearRecognitionStartTimer();
  if (!recognitionToAbort) return;
  try {
    recognitionToAbort.abort();
  } catch {
    // The browser may already have closed the recognition session.
  }
}

function isActiveSpeechState(state: SpeechRecognitionState) {
  return state === "starting" || state === "listening" || state === "stopping";
}

function setSpeechState(nextState: SpeechRecognitionState, force = false) {
  const previousState = speechState.value;
  if (!force && previousState === nextState) return;
  const wasActive = isActiveSpeechState(previousState);
  speechState.value = nextState;
  emit("speech-state-change", nextState);
  const isActive = isActiveSpeechState(nextState);
  if (wasActive !== isActive) emit("listening-change", isActive);
}

function joinFinalSpeechSegments(segments: string[], hasInterim: boolean) {
  return segments
    .map((segment, index) => {
      const text = segment.trim();
      if (!text || /[。！？!?；;，,：:\n]$/u.test(text)) return text;
      return index < segments.length - 1 || hasInterim ? `${text}，` : text;
    })
    .join("");
}

function applySpokenPunctuation(text: string) {
  return text
    .replace(/(逗号|逗點)/gu, "，")
    .replace(/(句号|句點)/gu, "。")
    .replace(/(问号|問號)/gu, "？")
    .replace(/(感叹号|驚嘆號|感嘆號)/gu, "！")
    .replace(/(分号|分號)/gu, "；")
    .replace(/(换行|換行)/gu, "\n");
}

function ensureTerminalPunctuation(text: string) {
  const trimmed = text.trimEnd();
  if (!trimmed || /[。！？!?；;]$/u.test(trimmed)) return trimmed;
  return `${trimmed}。`;
}

function joinSpeechText(base: string, speech: string) {
  if (!base) return speech.trimStart();
  if (!speech.trim()) return base;
  return `${base}${/\s$/.test(base) ? "" : "\n"}${speech.trimStart()}`;
}

function speechErrorMessage(error: string) {
  const messages: Record<string, string> = {
    "not-allowed": "未获得麦克风权限，请在浏览器设置中允许麦克风后重试。",
    "service-not-allowed": "浏览器未允许语音识别服务，请改用文字输入。",
    "audio-capture": "未检测到可用麦克风，请检查设备后重试。",
    network: "语音识别服务连接失败，请检查网络或改用文字输入。",
    "no-speech": "没有识别到语音，请再试一次。",
    aborted: "语音识别已停止。",
  };
  return messages[error] || "语音识别出现问题，请改用文字输入。";
}

function recordingStartErrorMessage(error: unknown) {
  const name = String((error as { name?: string })?.name || "");
  if (name === "NotAllowedError" || name === "SecurityError") {
    return "未获得麦克风权限，请在微信或系统设置中允许麦克风后重试。";
  }
  if (name === "NotFoundError" || name === "DevicesNotFoundError") {
    return "未检测到可用麦克风，请检查设备后重试。";
  }
  if (name === "NotReadableError" || name === "TrackStartError") {
    return "麦克风正被其他应用占用，请关闭占用后重试。";
  }
  if (name === "TimeoutError") {
    return "微信未能打开麦克风，请在右上角“…”或系统设置中允许麦克风后重试。";
  }
  return "麦克风暂时无法启动，请检查权限、HTTPS 和网络后重试。";
}

function transcriptionErrorMessage(error: unknown) {
  const response = (error as {
    response?: { data?: { detail?: { code?: string } | string; code?: string } };
  })?.response;
  const detail = response?.data?.detail;
  const code =
    (typeof detail === "object" ? detail?.code : undefined) || response?.data?.code || "";
  const messages: Record<string, string> = {
    unsupported_audio_type: "当前录音格式暂不支持，请更换浏览器或使用文字输入。",
    audio_too_large: "录音超过 5MB，请缩短内容后重试。",
    empty_audio: "没有录到声音，请再试一次。",
    rate_limited: "语音请求过于频繁，请稍后再试。",
    asr_rate_limited: "语音请求过于频繁，请稍后再试。",
    asr_no_speech: "没有识别到语音，请再试一次。",
    asr_unavailable: "语音转写服务暂时不可用，请稍后重试或使用文字输入。",
    asr_timeout: "语音转写超时，请缩短内容后重试。",
    asr_transcription_failed: "语音转写失败，请再试一次或使用文字输入。",
    session_not_found: "当前测评会话已失效，请刷新页面后重试。",
    session_completed: "测评已结束，无法继续语音输入。",
    session_not_active: "当前测评无法继续语音输入，请刷新页面确认会话状态。",
  };
  return messages[code] || "语音转写失败，请检查网络后重试或使用文字输入。";
}

watch(
  () => props.submitting || props.busy,
  (blocked) => {
    if (!blocked) return;
    abortListening();
    if (speechState.value === "review") {
      speechMessage.value = "";
      setSpeechState(speechSupported.value ? "ready" : "unsupported");
    }
  },
);

onMounted(setupRecognition);
onBeforeUnmount(() => {
  componentUnmounted = true;
  discardRecognitionResults.value = true;
  cancelRecognition();
  cancelRecordingAndTranscription();
});
</script>

<template>
  <form class="interview-input-dock" @submit.prevent="submitForm">
    <div v-if="props.isDebug" class="interview-prompt-row" aria-label="快速回答方向">
      <button
        v-for="prompt in props.prompts"
        :key="prompt"
        type="button"
        @click="emit('apply-prompt', prompt)"
      >
        {{ prompt }}
      </button>
    </div>

    <div class="interview-input-main">
      <button
        class="interview-mic-button"
        :class="{ 'is-listening': isListening }"
        type="button"
        :aria-label="micLabel"
        :aria-pressed="isListening"
        :data-speech-state="speechState"
        :title="micLabel"
        :disabled="micDisabled"
        @click="toggleListening"
      >
        <span>{{ micButtonText }}</span>
      </button>

      <label class="interview-text-field">
        <span class="sr-only">回答内容</span>
        <textarea
          :value="modelValue"
          rows="1"
          placeholder="可以直接说出你的想法，也可以在这里输入文字..."
          @input="updateValue"
          @keydown="handleKeydown"
        ></textarea>
      </label>

      <button class="interview-send-button" type="submit" :disabled="!submitEnabled">
        {{ submitting ? "思考中" : "发送" }}
      </button>

    </div>

    <p v-if="showSpeechStatus" class="interview-voice-status" aria-live="polite">
      {{ speechStatusText }}
    </p>
  </form>
</template>
