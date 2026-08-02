import { defineComponent } from "vue";
import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  useSpeechPlayback,
  type ServerSpeechPlaybackProvider,
  type SpeechAudioElement,
  type SpeechPlaybackProvider,
  type SpeechPlaybackStatus,
  type SpeechPlaybackTurn,
} from "./useSpeechPlayback";

describe("useSpeechPlayback", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("loads and plays only the supplied persisted AI turn", async () => {
    const browser = createBrowserProvider();
    const server = createServerProvider();
    const wrapper = mountPlayback(browser, server);
    const playback = wrapper.vm as unknown as PlaybackVm;
    const turn = persistedTurn(7, "请说说你的判断。");

    playback.enableAndSpeakTurn(turn);
    await flushPromises();

    expect(server.load).toHaveBeenCalledTimes(1);
    expect(server.load).toHaveBeenCalledWith(turn, expect.any(AbortSignal));
    expect(server.createObjectUrl).toHaveBeenCalledTimes(1);
    expect(server.audio.play).toHaveBeenCalledTimes(1);
    expect(browser.speak).not.toHaveBeenCalled();
    expect(playback.status).toBe("speaking");
    expect(localStorage.getItem("assessment_interviewer_voice_enabled")).toBe("true");

    server.audio.finish();
    expect(playback.status).toBe("ready");
    expect(server.revokeObjectUrl).toHaveBeenCalledWith("blob:doubao-audio");

    playback.disable();
    expect(localStorage.getItem("assessment_interviewer_voice_enabled")).toBe("false");
    expect(playback.status).toBe("disabled");
    wrapper.unmount();
  });

  it("remembers the voice toggle without replaying an old question on remount", () => {
    const firstServer = createServerProvider();
    const firstWrapper = mountPlayback(createBrowserProvider(), firstServer);
    const firstPlayback = firstWrapper.vm as unknown as PlaybackVm;
    firstPlayback.enable();
    firstWrapper.unmount();

    const restoredServer = createServerProvider();
    const restoredWrapper = mountPlayback(createBrowserProvider(), restoredServer);
    const restoredPlayback = restoredWrapper.vm as unknown as PlaybackVm;
    expect(restoredPlayback.enabled).toBe(true);
    expect(restoredPlayback.status).toBe("ready");
    expect(restoredServer.load).not.toHaveBeenCalled();
    restoredWrapper.unmount();
  });

  it("falls back to browser speech with a transient notice when Doubao fails", async () => {
    const browser = createBrowserProvider();
    const server = createServerProvider();
    server.load.mockRejectedValueOnce(new Error("supplier unavailable"));
    const wrapper = mountPlayback(browser, server);
    const playback = wrapper.vm as unknown as PlaybackVm;

    playback.enableAndSpeakTurn(persistedTurn(9, "这里先看一条新信息。"));
    await flushPromises();

    expect(browser.speak).toHaveBeenCalledWith(
      "这里先看一条新信息。",
      expect.any(Object),
    );
    expect(playback.notice).toContain("已切换为浏览器朗读");
    expect(playback.status).toBe("ready");
    wrapper.unmount();
  });

  it("aborts an older turn request and releases its audio when stopped", async () => {
    const server = createServerProvider();
    let firstSignal: AbortSignal | undefined;
    let resolveFirst: ((blob: Blob) => void) | undefined;
    server.load
      .mockImplementationOnce((_turn, signal) => {
        firstSignal = signal;
        return new Promise<Blob>((resolve) => {
          resolveFirst = resolve;
        });
      })
      .mockResolvedValueOnce(new Blob(["second"], { type: "audio/mpeg" }));
    const wrapper = mountPlayback(createBrowserProvider(), server);
    const playback = wrapper.vm as unknown as PlaybackVm;
    playback.enable();

    void playback.speakTurn(persistedTurn(3, "第一问"));
    await flushPromises();
    void playback.speakTurn(persistedTurn(5, "第二问"));
    await flushPromises();

    expect(firstSignal?.aborted).toBe(true);
    expect(server.load).toHaveBeenCalledTimes(2);
    resolveFirst?.(new Blob(["late"], { type: "audio/mpeg" }));
    await flushPromises();
    expect(server.createObjectUrl).toHaveBeenCalledTimes(1);

    playback.stop();
    expect(server.audio.pause).toHaveBeenCalled();
    expect(server.revokeObjectUrl).toHaveBeenCalledWith("blob:doubao-audio");
    wrapper.unmount();
  });

  it("keeps text assessment usable when neither audio path is supported", () => {
    localStorage.setItem("assessment_interviewer_voice_enabled", "true");
    const wrapper = mountPlayback(
      createBrowserProvider(false),
      createServerProvider(false),
    );
    const playback = wrapper.vm as unknown as PlaybackVm;
    expect(playback.enabled).toBe(false);
    expect(playback.statusText).toBe("当前浏览器不支持语音播放");
    playback.enableAndSpeakTurn(persistedTurn(1, "不会播放"));
    expect(playback.status).toBe("disabled");
    wrapper.unmount();
  });
});

interface PlaybackVm {
  disable: () => void;
  enable: () => void;
  enableAndSpeakTurn: (turn: SpeechPlaybackTurn) => void;
  enabled: boolean;
  notice: string;
  speakTurn: (turn: SpeechPlaybackTurn) => Promise<void>;
  status: SpeechPlaybackStatus;
  statusText: string;
  stop: () => void;
}

function mountPlayback(
  browser: SpeechPlaybackProvider,
  server: ServerSpeechPlaybackProvider,
) {
  const Host = defineComponent({
    setup() {
      return useSpeechPlayback(browser, server);
    },
    template: "<div />",
  });
  return mount(Host);
}

function createBrowserProvider(supported = true): SpeechPlaybackProvider & {
  speak: ReturnType<typeof vi.fn<SpeechPlaybackProvider["speak"]>>;
  stop: ReturnType<typeof vi.fn<SpeechPlaybackProvider["stop"]>>;
} {
  return {
    supported: () => supported,
    speak: vi.fn<SpeechPlaybackProvider["speak"]>((_text, options) => {
      options.onStart();
      options.onEnd();
    }),
    stop: vi.fn<SpeechPlaybackProvider["stop"]>(),
  };
}

function createServerProvider(supported = true) {
  const audio = new FakeAudio();
  return {
    supported: () => supported,
    load: vi.fn<ServerSpeechPlaybackProvider["load"]>(
      async () => new Blob(["mp3"], { type: "audio/mpeg" }),
    ),
    createObjectUrl: vi.fn(() => "blob:doubao-audio"),
    revokeObjectUrl: vi.fn(),
    createAudio: vi.fn(() => audio),
    audio,
  };
}

class FakeAudio implements SpeechAudioElement {
  currentTime = 0;
  onended: ((event: Event) => unknown) | null = null;
  onerror: ((event: Event) => unknown) | null = null;
  onplay: ((event: Event) => unknown) | null = null;
  pause = vi.fn();
  play = vi.fn(async () => {
    this.onplay?.(new Event("play"));
  });

  finish() {
    this.onended?.(new Event("ended"));
  }
}

function persistedTurn(turnIndex: number, text: string): SpeechPlaybackTurn {
  return {
    sessionUuid: "voice-session",
    turnIndex,
    text,
  };
}
