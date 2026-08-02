import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import AnswerComposer from "./AnswerComposer.vue";

const speechApiMocks = vi.hoisted(() => ({
  transcribeAssessmentSpeech: vi.fn(),
}));

vi.mock("../../api/session", () => ({
  transcribeAssessmentSpeech: speechApiMocks.transcribeAssessmentSpeech,
}));

type ResultItem = { isFinal: boolean; 0: { transcript: string } };

class FakeSpeechRecognition {
  static instances: FakeSpeechRecognition[] = [];
  static autoStart = true;
  static autoEndOnStop = true;
  static failOnStart = false;
  continuous = false;
  interimResults = false;
  lang = "";
  onstart: (() => void) | null = null;
  onend: (() => void) | null = null;
  onresult: ((event: { results: ArrayLike<ResultItem> }) => void) | null = null;
  onerror: ((event: { error: string }) => void) | null = null;
  start = vi.fn(() => {
    if (FakeSpeechRecognition.failOnStart) throw new Error("start failed");
    if (FakeSpeechRecognition.autoStart) this.onstart?.();
  });
  stop = vi.fn(() => {
    if (FakeSpeechRecognition.autoEndOnStop) this.onend?.();
  });
  abort = vi.fn(() => {
    this.onerror?.({ error: "aborted" });
    this.onend?.();
  });

  constructor() {
    FakeSpeechRecognition.instances.push(this);
  }

  result(transcript: string, isFinal = true) {
    this.onresult?.({ results: [{ isFinal, 0: { transcript } }] });
  }

  begin() {
    this.onstart?.();
  }

  finish() {
    this.onend?.();
  }

  error(error: string) {
    this.onerror?.({ error });
  }
}

class FakeMediaRecorder {
  static instances: FakeMediaRecorder[] = [];
  static chunks: Blob[] = [new Blob(["recording"], { type: "audio/webm" })];
  static isTypeSupported = vi.fn((type: string) => type === "audio/webm;codecs=opus");
  state: RecordingState = "inactive";
  mimeType: string;
  ondataavailable: ((event: BlobEvent) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  onstop: ((event: Event) => void) | null = null;
  start = vi.fn(() => {
    this.state = "recording";
  });
  stop = vi.fn(() => {
    if (this.state === "inactive") return;
    this.state = "inactive";
    for (const data of FakeMediaRecorder.chunks) {
      this.ondataavailable?.({ data } as BlobEvent);
    }
    this.onstop?.(new Event("stop"));
  });

  constructor(_stream: MediaStream, options?: MediaRecorderOptions) {
    this.mimeType = options?.mimeType || "audio/webm";
    FakeMediaRecorder.instances.push(this);
  }
}

function fakeMediaStream() {
  const stop = vi.fn();
  return {
    stream: { getTracks: () => [{ stop }] } as unknown as MediaStream,
    stop,
  };
}

function installRecordingFallback(getUserMedia: ReturnType<typeof vi.fn>) {
  delete (window as Window & { webkitSpeechRecognition?: unknown }).webkitSpeechRecognition;
  Object.defineProperty(window, "MediaRecorder", {
    configurable: true,
    value: FakeMediaRecorder,
  });
  Object.defineProperty(navigator, "mediaDevices", {
    configurable: true,
    value: { getUserMedia },
  });
}

function mountComposer(overrides: Record<string, unknown> = {}) {
  return mount(AnswerComposer, {
    props: {
      sessionUuid: "session-voice-input",
      modelValue: "",
      canSubmit: true,
      submitting: false,
      busy: false,
      ...overrides,
    },
  });
}

describe("AnswerComposer speech state", () => {
  beforeEach(() => {
    speechApiMocks.transcribeAssessmentSpeech.mockReset();
    FakeSpeechRecognition.instances = [];
    FakeSpeechRecognition.autoStart = true;
    FakeSpeechRecognition.autoEndOnStop = true;
    FakeSpeechRecognition.failOnStart = false;
    FakeMediaRecorder.instances = [];
    FakeMediaRecorder.chunks = [new Blob(["recording"], { type: "audio/webm" })];
    FakeMediaRecorder.isTypeSupported.mockClear();
    delete (window as Window & { SpeechRecognition?: unknown }).SpeechRecognition;
    (window as Window & { webkitSpeechRecognition?: unknown }).webkitSpeechRecognition =
      FakeSpeechRecognition;
  });

  afterEach(() => {
    delete (window as Window & { webkitSpeechRecognition?: unknown }).webkitSpeechRecognition;
    delete (window as Window & { MediaRecorder?: unknown }).MediaRecorder;
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: undefined,
    });
  });

  it("publishes the complete state sequence and requires review before manual submission", async () => {
    FakeSpeechRecognition.autoStart = false;
    FakeSpeechRecognition.autoEndOnStop = false;
    const wrapper = mountComposer();
    await flushPromises();
    const mic = wrapper.get(".interview-mic-button");
    expect(mic.text()).toBe("开始");
    expect(wrapper.find(".interview-voice-status").exists()).toBe(false);
    expect(wrapper.find(".interview-input-meta").exists()).toBe(false);
    expect(wrapper.text()).not.toContain("Shift+Enter");
    expect(wrapper.text()).not.toContain("系统不保存音频");

    await mic.trigger("click");
    const recognition = FakeSpeechRecognition.instances[0];
    expect(mic.attributes("data-speech-state")).toBe("starting");
    expect(mic.text()).toBe("启动中");
    expect(wrapper.emitted("speech-state-change")?.at(-1)).toEqual(["starting"]);
    expect(wrapper.emitted("listening-change")).toEqual([[true]]);

    recognition.begin();
    await flushPromises();
    expect(mic.attributes("data-speech-state")).toBe("listening");
    expect(mic.text()).toBe("停止");
    recognition.result("我会先小范围测试");
    await flushPromises();
    expect(wrapper.emitted("update:modelValue")?.at(-1)?.[0]).toContain("小范围测试");
    expect(wrapper.emitted("submit")).toBeUndefined();

    await wrapper.get("textarea").trigger("keydown", { key: "Enter" });
    expect(recognition.stop).toHaveBeenCalledTimes(1);
    expect(mic.attributes("data-speech-state")).toBe("stopping");
    expect(mic.text()).toBe("停止中");
    expect(wrapper.emitted("submit")).toBeUndefined();

    recognition.finish();
    await flushPromises();
    expect(mic.attributes("data-speech-state")).toBe("review");
    expect(mic.text()).toBe("再说");
    expect(wrapper.text()).toContain("请检查或修改文字");

    await wrapper.get("textarea").trigger("keydown", { key: "Enter" });
    expect(wrapper.emitted("submit")).toHaveLength(1);
    expect(wrapper.emitted("speech-state-change")?.map(([state]) => state)).toEqual([
      "ready",
      "starting",
      "listening",
      "stopping",
      "review",
    ]);
    expect(wrapper.emitted("listening-change")).toEqual([[true], [false]]);
  });

  it("stops recognition when the user edits and ignores late results", async () => {
    const wrapper = mountComposer({ modelValue: "原有文字" });
    await flushPromises();
    await wrapper.get(".interview-mic-button").trigger("click");
    const recognition = FakeSpeechRecognition.instances[0];
    recognition.result("语音草稿", false);
    await wrapper.get("textarea").setValue("用户手动修改");

    recognition.result("迟到的识别结果");
    await flushPromises();
    expect(recognition.stop).toHaveBeenCalledTimes(1);
    expect(wrapper.emitted("update:modelValue")?.at(-1)?.[0]).toBe("用户手动修改");
  });

  it("ignores stale callbacks after aborting and immediately restarting recognition", async () => {
    FakeSpeechRecognition.autoStart = false;
    FakeSpeechRecognition.autoEndOnStop = false;
    const wrapper = mountComposer();
    await flushPromises();

    await wrapper.get(".interview-mic-button").trigger("click");
    const firstRecognition = FakeSpeechRecognition.instances[0];
    firstRecognition.begin();
    firstRecognition.result("第一轮旧草稿", false);
    await wrapper.setProps({ busy: true });
    expect(firstRecognition.abort).toHaveBeenCalledTimes(1);

    await wrapper.setProps({ busy: false });
    await wrapper.get(".interview-mic-button").trigger("click");
    const secondRecognition = FakeSpeechRecognition.instances[1];
    secondRecognition.begin();
    secondRecognition.result("第二轮新内容", false);
    await flushPromises();

    firstRecognition.result("不应写入的迟到结果");
    firstRecognition.error("network");
    firstRecognition.finish();
    await flushPromises();

    const mic = wrapper.get(".interview-mic-button");
    expect(mic.attributes("data-speech-state")).toBe("listening");
    expect(wrapper.text()).toContain("正在聆听");
    expect(wrapper.text()).not.toContain("语音识别服务连接失败");
    expect(wrapper.emitted("update:modelValue")?.at(-1)?.[0]).toContain("第二轮新内容");
    expect(wrapper.emitted("update:modelValue")?.at(-1)?.[0]).not.toContain(
      "不应写入的迟到结果",
    );

    await mic.trigger("click");
    secondRecognition.finish();
    await flushPromises();
    expect(mic.attributes("data-speech-state")).toBe("review");
  });

  it("aborts listening while busy and reports permission and microphone errors", async () => {
    const wrapper = mountComposer();
    await flushPromises();
    await wrapper.get(".interview-mic-button").trigger("click");
    const recognition = FakeSpeechRecognition.instances[0];
    recognition.error("not-allowed");
    await flushPromises();
    expect(wrapper.text()).toContain("未获得麦克风权限");

    await wrapper.get(".interview-mic-button").trigger("click");
    await wrapper.setProps({ busy: true });
    const retriedRecognition = FakeSpeechRecognition.instances.at(-1)!;
    expect(retriedRecognition.abort).toHaveBeenCalled();
    expect(wrapper.get(".interview-mic-button").attributes("disabled")).toBeDefined();

    await wrapper.setProps({ busy: false });
    await wrapper.get(".interview-mic-button").trigger("click");
    FakeSpeechRecognition.instances.at(-1)!.error("audio-capture");
    await flushPromises();
    expect(wrapper.text()).toContain("未检测到可用麦克风");
  });

  it("shows a recoverable error when recognition cannot start", async () => {
    FakeSpeechRecognition.failOnStart = true;
    const wrapper = mountComposer();
    await flushPromises();
    await wrapper.get(".interview-mic-button").trigger("click");

    const mic = wrapper.get(".interview-mic-button");
    expect(mic.attributes("data-speech-state")).toBe("error");
    expect(mic.text()).toBe("重试");
    expect(wrapper.text()).toContain("语音识别暂时无法启动");
    expect(wrapper.emitted("listening-change")).toEqual([[true], [false]]);
  });

  it("times out a recognition attempt that never starts", async () => {
    vi.useFakeTimers();
    try {
      FakeSpeechRecognition.autoStart = false;
      const wrapper = mountComposer();
      await flushPromises();
      await wrapper.get(".interview-mic-button").trigger("click");
      const recognition = FakeSpeechRecognition.instances[0];

      await vi.advanceTimersByTimeAsync(8000);
      const mic = wrapper.get(".interview-mic-button");
      expect(recognition.abort).toHaveBeenCalledTimes(1);
      expect(mic.attributes("data-speech-state")).toBe("error");
      expect(wrapper.text()).toContain("麦克风启动超时");
      wrapper.unmount();
    } finally {
      vi.useRealTimers();
    }
  });

  it("reports network failures and safely falls back when recognition is unsupported", async () => {
    const wrapper = mountComposer();
    await flushPromises();
    await wrapper.get(".interview-mic-button").trigger("click");
    FakeSpeechRecognition.instances[0].error("network");
    await flushPromises();
    expect(wrapper.text()).toContain("语音识别服务连接失败");
    wrapper.unmount();

    delete (window as Window & { webkitSpeechRecognition?: unknown }).webkitSpeechRecognition;
    const unsupportedWrapper = mountComposer();
    expect(
      unsupportedWrapper.get(".interview-mic-button").attributes("data-speech-state"),
    ).toBe("unsupported");
    expect(unsupportedWrapper.get(".interview-mic-button").text()).toBe("不可用");
    expect(
      unsupportedWrapper.get(".interview-mic-button").attributes("disabled"),
    ).toBeDefined();
    expect(unsupportedWrapper.find(".interview-voice-status").exists()).toBe(false);
    await unsupportedWrapper.get("textarea").setValue("继续使用文字输入");
    expect(unsupportedWrapper.emitted("update:modelValue")?.at(-1)?.[0]).toBe(
      "继续使用文字输入",
    );
  });

  it("records and transcribes in browsers without Web Speech without auto-submitting", async () => {
    const { stream, stop } = fakeMediaStream();
    installRecordingFallback(vi.fn().mockResolvedValue(stream));
    speechApiMocks.transcribeAssessmentSpeech.mockResolvedValue({
      text: "我会先核对约束，再做小范围验证",
      provider: "doubao",
      request_id: "asr-1",
    });

    const wrapper = mountComposer({ modelValue: "原有判断" });
    await flushPromises();
    const mic = wrapper.get(".interview-mic-button");
    expect(mic.attributes("data-speech-state")).toBe("ready");
    expect(mic.attributes("disabled")).toBeUndefined();

    await mic.trigger("click");
    await flushPromises();
    expect(mic.attributes("data-speech-state")).toBe("listening");
    expect(wrapper.text()).toContain("正在录音");

    await mic.trigger("click");
    await flushPromises();
    expect(stop).toHaveBeenCalled();
    expect(speechApiMocks.transcribeAssessmentSpeech).toHaveBeenCalledWith(
      "session-voice-input",
      expect.objectContaining({ type: "audio/webm;codecs=opus" }),
      expect.any(AbortSignal),
    );
    expect(mic.attributes("data-speech-state")).toBe("review");
    expect(wrapper.emitted("update:modelValue")?.at(-1)?.[0]).toBe(
      "原有判断\n我会先核对约束，再做小范围验证。",
    );
    expect(wrapper.emitted("submit")).toBeUndefined();
  });

  it("aborts an in-flight transcription when the session becomes busy", async () => {
    const { stream } = fakeMediaStream();
    installRecordingFallback(vi.fn().mockResolvedValue(stream));
    let resolveTranscription!: (value: { text: string; provider: string }) => void;
    speechApiMocks.transcribeAssessmentSpeech.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveTranscription = resolve;
        }),
    );

    const wrapper = mountComposer();
    await flushPromises();
    await wrapper.get(".interview-mic-button").trigger("click");
    await flushPromises();
    await wrapper.get(".interview-mic-button").trigger("click");
    await flushPromises();
    const signal = speechApiMocks.transcribeAssessmentSpeech.mock.calls[0][2] as AbortSignal;
    expect(signal.aborted).toBe(false);

    await wrapper.setProps({ busy: true });
    expect(signal.aborted).toBe(true);
    resolveTranscription({ text: "不应回填的迟到结果", provider: "doubao" });
    await flushPromises();
    expect(wrapper.emitted("update:modelValue")).toBeUndefined();
  });

  it("shows the stable backend ASR error without changing or submitting the answer", async () => {
    const { stream } = fakeMediaStream();
    installRecordingFallback(vi.fn().mockResolvedValue(stream));
    speechApiMocks.transcribeAssessmentSpeech.mockRejectedValue({
      response: { data: { code: "asr_rate_limited" } },
    });
    const wrapper = mountComposer({ modelValue: "保留这段文字" });
    await flushPromises();
    await wrapper.get(".interview-mic-button").trigger("click");
    await flushPromises();
    await wrapper.get(".interview-mic-button").trigger("click");
    await flushPromises();

    expect(wrapper.text()).toContain("请求过于频繁");
    expect(wrapper.emitted("update:modelValue")).toBeUndefined();
    expect(wrapper.emitted("submit")).toBeUndefined();
  });

  it("times out a stalled WeChat microphone request and releases a late stream", async () => {
    vi.useFakeTimers();
    try {
      let resolveStream!: (stream: MediaStream) => void;
      const pendingStream = new Promise<MediaStream>((resolve) => {
        resolveStream = resolve;
      });
      const { stream, stop } = fakeMediaStream();
      installRecordingFallback(vi.fn().mockReturnValue(pendingStream));
      const wrapper = mountComposer();
      await flushPromises();

      await wrapper.get(".interview-mic-button").trigger("click");
      await vi.advanceTimersByTimeAsync(8000);
      await flushPromises();
      expect(wrapper.get(".interview-mic-button").attributes("data-speech-state")).toBe(
        "error",
      );
      expect(wrapper.text()).toContain("右上角");

      resolveStream(stream);
      await flushPromises();
      expect(stop).toHaveBeenCalledTimes(1);
      wrapper.unmount();
    } finally {
      vi.useRealTimers();
    }
  });

  it("automatically stops and transcribes at the 60 second recording limit", async () => {
    vi.useFakeTimers();
    try {
      const { stream } = fakeMediaStream();
      installRecordingFallback(vi.fn().mockResolvedValue(stream));
      speechApiMocks.transcribeAssessmentSpeech.mockResolvedValue({
        text: "限时录音结果",
        provider: "doubao",
      });
      const wrapper = mountComposer();
      await flushPromises();
      await wrapper.get(".interview-mic-button").trigger("click");
      await flushPromises();

      await vi.advanceTimersByTimeAsync(60_000);
      await flushPromises();
      expect(FakeMediaRecorder.instances[0].stop).toHaveBeenCalledTimes(1);
      expect(speechApiMocks.transcribeAssessmentSpeech).toHaveBeenCalledTimes(1);
      expect(wrapper.get(".interview-mic-button").attributes("data-speech-state")).toBe(
        "review",
      );
      wrapper.unmount();
    } finally {
      vi.useRealTimers();
    }
  });

  it("enforces the recording size limit and releases tracks on unmount", async () => {
    const first = fakeMediaStream();
    installRecordingFallback(vi.fn().mockResolvedValue(first.stream));
    FakeMediaRecorder.chunks = [
      new Blob([new Uint8Array(5 * 1024 * 1024 + 1)], { type: "audio/webm" }),
    ];
    const wrapper = mountComposer();
    await flushPromises();
    await wrapper.get(".interview-mic-button").trigger("click");
    await flushPromises();
    await wrapper.get(".interview-mic-button").trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("超过 5MB");
    expect(speechApiMocks.transcribeAssessmentSpeech).not.toHaveBeenCalled();
    expect(first.stop).toHaveBeenCalled();

    const second = fakeMediaStream();
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: { getUserMedia: vi.fn().mockResolvedValue(second.stream) },
    });
    await wrapper.get(".interview-mic-button").trigger("click");
    await flushPromises();
    wrapper.unmount();
    expect(second.stop).toHaveBeenCalled();
  });
});
