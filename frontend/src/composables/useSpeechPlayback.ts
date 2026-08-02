import { computed, onBeforeUnmount, ref } from "vue";
import { getAssessmentTurnSpeech } from "../api/session";

export type SpeechPlaybackStatus =
  | "disabled"
  | "ready"
  | "loading"
  | "speaking"
  | "error";

export interface SpeechPlaybackTurn {
  sessionUuid: string;
  turnIndex: number;
  text: string;
}

export interface SpeechPlaybackProvider {
  supported(): boolean;
  speak(
    text: string,
    options: {
      onStart: () => void;
      onEnd: () => void;
      onError: (message: string) => void;
    },
  ): void;
  stop(): void;
}

export interface SpeechAudioElement {
  currentTime: number;
  onended: ((event: Event) => unknown) | null;
  onerror: ((event: Event) => unknown) | null;
  onplay: ((event: Event) => unknown) | null;
  pause(): void;
  play(): Promise<void>;
}

export interface ServerSpeechPlaybackProvider {
  supported(): boolean;
  load(turn: SpeechPlaybackTurn, signal: AbortSignal): Promise<Blob>;
  createObjectUrl(blob: Blob): string;
  revokeObjectUrl(url: string): void;
  createAudio(url: string): SpeechAudioElement;
}

class BrowserSpeechPlaybackProvider implements SpeechPlaybackProvider {
  private requestId = 0;

  supported() {
    return typeof window !== "undefined" && "speechSynthesis" in window;
  }

  speak(
    text: string,
    options: {
      onStart: () => void;
      onEnd: () => void;
      onError: (message: string) => void;
    },
  ) {
    if (!this.supported()) {
      options.onError("当前浏览器不支持备用语音朗读。");
      return;
    }
    const synthesis = window.speechSynthesis;
    const currentRequestId = ++this.requestId;
    const chunks = splitSpeechText(text);
    let chunkIndex = 0;
    let started = false;

    synthesis.cancel();
    synthesis.resume();

    const speakNextChunk = () => {
      if (currentRequestId !== this.requestId) return;
      const chunk = chunks[chunkIndex];
      if (!chunk) {
        options.onEnd();
        return;
      }
      const utterance = new SpeechSynthesisUtterance(chunk);
      utterance.lang = "zh-CN";
      utterance.rate = 1;
      utterance.onstart = () => {
        if (currentRequestId !== this.requestId || started) return;
        started = true;
        options.onStart();
      };
      utterance.onend = () => {
        if (currentRequestId !== this.requestId) return;
        chunkIndex += 1;
        window.setTimeout(speakNextChunk, 20);
      };
      utterance.onerror = (event) => {
        if (currentRequestId !== this.requestId) return;
        options.onError(speechSynthesisErrorMessage(event.error));
      };
      try {
        synthesis.speak(utterance);
      } catch {
        options.onError("备用语音朗读无法启动。");
      }
    };

    speakNextChunk();
  }

  stop() {
    this.requestId += 1;
    if (!this.supported()) return;
    window.speechSynthesis.cancel();
    window.speechSynthesis.resume();
  }
}

class DoubaoSpeechPlaybackProvider implements ServerSpeechPlaybackProvider {
  supported() {
    return (
      typeof window !== "undefined" &&
      typeof Audio !== "undefined" &&
      typeof URL !== "undefined" &&
      typeof URL.createObjectURL === "function"
    );
  }

  load(turn: SpeechPlaybackTurn, signal: AbortSignal) {
    return getAssessmentTurnSpeech(turn.sessionUuid, turn.turnIndex, signal);
  }

  createObjectUrl(blob: Blob) {
    return URL.createObjectURL(blob);
  }

  revokeObjectUrl(url: string) {
    URL.revokeObjectURL(url);
  }

  createAudio(url: string) {
    return new Audio(url);
  }
}

function splitSpeechText(text: string, maxLength = 160) {
  const normalized = text.replace(/\s+/g, " ").trim();
  if (!normalized) return [];
  const sentences = normalized.match(/[^。！？!?；;]+[。！？!?；;]?/gu) || [normalized];
  const chunks: string[] = [];
  let current = "";
  for (const sentence of sentences) {
    if ((current + sentence).length <= maxLength) {
      current += sentence;
      continue;
    }
    if (current) chunks.push(current);
    current = "";
    for (let offset = 0; offset < sentence.length; offset += maxLength) {
      const part = sentence.slice(offset, offset + maxLength);
      if (part.length === maxLength) chunks.push(part);
      else current = part;
    }
  }
  if (current) chunks.push(current);
  return chunks;
}

function speechSynthesisErrorMessage(error: string) {
  const messages: Record<string, string> = {
    "not-allowed": "浏览器阻止了备用语音朗读。",
    "audio-busy": "音频设备正忙，请稍后重试。",
    "audio-hardware": "没有可用的音频输出设备。",
    canceled: "朗读已取消。",
    interrupted: "朗读被浏览器中断。",
  };
  return messages[error] || `备用语音朗读失败：${error || "浏览器未返回原因"}`;
}

const VOICE_ENABLED_STORAGE_KEY = "assessment_interviewer_voice_enabled";

function readEnabledPreference() {
  try {
    return typeof localStorage !== "undefined"
      ? localStorage.getItem(VOICE_ENABLED_STORAGE_KEY) === "true"
      : false;
  } catch {
    return false;
  }
}

function saveEnabledPreference(enabled: boolean) {
  try {
    if (typeof localStorage !== "undefined") {
      localStorage.setItem(VOICE_ENABLED_STORAGE_KEY, String(enabled));
    }
  } catch {
    // Private browsing and locked-down browser policies may block persistence.
  }
}

export function useSpeechPlayback(
  browserProvider: SpeechPlaybackProvider = new BrowserSpeechPlaybackProvider(),
  serverProvider: ServerSpeechPlaybackProvider = new DoubaoSpeechPlaybackProvider(),
) {
  const isSupported = ref(serverProvider.supported() || browserProvider.supported());
  const restoreEnabled = isSupported.value && readEnabledPreference();
  const status = ref<SpeechPlaybackStatus>(restoreEnabled ? "ready" : "disabled");
  const errorMessage = ref("");
  const notice = ref("");
  let requestId = 0;
  let controller: AbortController | null = null;
  let audio: SpeechAudioElement | null = null;
  let objectUrl = "";
  let noticeTimer: ReturnType<typeof setTimeout> | null = null;

  const enabled = computed(() => status.value !== "disabled");
  const statusText = computed(() => {
    if (!isSupported.value) return "当前浏览器不支持语音播放";
    if (status.value === "disabled") return "语音已关闭";
    if (status.value === "loading") return "正在准备罗杰斯教授的语音";
    if (status.value === "speaking") return "罗杰斯教授正在朗读";
    if (status.value === "error") return errorMessage.value || "语音播放失败";
    return "语音已打开";
  });

  function clearNotice() {
    if (noticeTimer) clearTimeout(noticeTimer);
    noticeTimer = null;
    notice.value = "";
  }

  function showNotice(message: string) {
    clearNotice();
    notice.value = message;
    noticeTimer = setTimeout(() => {
      notice.value = "";
      noticeTimer = null;
    }, 4200);
  }

  function releaseAudio() {
    const currentAudio = audio;
    audio = null;
    if (currentAudio) {
      currentAudio.onended = null;
      currentAudio.onerror = null;
      currentAudio.onplay = null;
      currentAudio.pause();
      try {
        currentAudio.currentTime = 0;
      } catch {
        // Some browsers reject seeking before enough metadata has loaded.
      }
    }
    if (objectUrl) {
      serverProvider.revokeObjectUrl(objectUrl);
      objectUrl = "";
    }
  }

  function stopCurrentPlayback() {
    requestId += 1;
    controller?.abort();
    controller = null;
    releaseAudio();
    browserProvider.stop();
  }

  function playBrowserFallback(turn: SpeechPlaybackTurn, activeRequestId: number) {
    if (activeRequestId !== requestId || !enabled.value) return;
    if (!browserProvider.supported()) {
      errorMessage.value = "豆包语音暂不可用，当前浏览器也无法启动备用朗读。";
      status.value = "error";
      showNotice(errorMessage.value);
      return;
    }
    showNotice("豆包语音暂不可用，已切换为浏览器朗读。");
    browserProvider.speak(turn.text, {
      onStart: () => {
        if (activeRequestId === requestId && enabled.value) status.value = "speaking";
      },
      onEnd: () => {
        if (activeRequestId === requestId && enabled.value) status.value = "ready";
      },
      onError: (message) => {
        if (activeRequestId !== requestId || !enabled.value) return;
        errorMessage.value = message;
        status.value = "error";
        showNotice("语音播放失败，你仍可继续使用文字作答。");
      },
    });
  }

  async function speakTurn(turn: SpeechPlaybackTurn, force = false) {
    const text = turn.text.trim();
    if (
      !text ||
      !Number.isInteger(turn.turnIndex) ||
      turn.turnIndex < 0 ||
      !turn.sessionUuid ||
      !isSupported.value ||
      (!enabled.value && !force)
    ) {
      return;
    }

    stopCurrentPlayback();
    const activeRequestId = requestId;
    clearNotice();
    errorMessage.value = "";
    status.value = "loading";
    if (!serverProvider.supported()) {
      playBrowserFallback({ ...turn, text }, activeRequestId);
      return;
    }

    const nextController = new AbortController();
    controller = nextController;
    try {
      const blob = await serverProvider.load({ ...turn, text }, nextController.signal);
      if (nextController.signal.aborted || activeRequestId !== requestId || !enabled.value) return;
      if (!blob.size) throw new Error("empty_audio");
      objectUrl = serverProvider.createObjectUrl(blob);
      const nextAudio = serverProvider.createAudio(objectUrl);
      audio = nextAudio;
      let fallbackStarted = false;
      const fallbackOnce = () => {
        if (fallbackStarted || activeRequestId !== requestId || !enabled.value) return;
        fallbackStarted = true;
        releaseAudio();
        playBrowserFallback({ ...turn, text }, activeRequestId);
      };
      nextAudio.onplay = () => {
        if (activeRequestId === requestId && enabled.value) status.value = "speaking";
      };
      nextAudio.onended = () => {
        if (activeRequestId !== requestId) return;
        releaseAudio();
        if (enabled.value) status.value = "ready";
      };
      nextAudio.onerror = fallbackOnce;
      try {
        await nextAudio.play();
        if (activeRequestId === requestId && enabled.value && status.value === "loading") {
          status.value = "speaking";
        }
      } catch {
        fallbackOnce();
      }
    } catch {
      if (nextController.signal.aborted || activeRequestId !== requestId || !enabled.value) return;
      releaseAudio();
      playBrowserFallback({ ...turn, text }, activeRequestId);
    } finally {
      if (controller === nextController) controller = null;
    }
  }

  function enable() {
    if (!isSupported.value) return;
    if (status.value === "disabled") status.value = "ready";
    errorMessage.value = "";
    saveEnabledPreference(true);
  }

  function enableAndSpeakTurn(turn: SpeechPlaybackTurn) {
    enable();
    void speakTurn(turn, true);
  }

  function disable() {
    stopCurrentPlayback();
    clearNotice();
    errorMessage.value = "";
    status.value = "disabled";
    saveEnabledPreference(false);
  }

  function stop() {
    stopCurrentPlayback();
    clearNotice();
    if (enabled.value) status.value = "ready";
  }

  onBeforeUnmount(() => {
    stopCurrentPlayback();
    clearNotice();
  });

  return {
    disable,
    enable,
    enableAndSpeakTurn,
    enabled,
    isSupported,
    notice,
    speakTurn,
    status,
    statusText,
    stop,
  };
}
