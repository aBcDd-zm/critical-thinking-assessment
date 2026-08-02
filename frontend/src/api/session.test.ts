import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "./client";
import { getAssessmentTurnSpeech, transcribeAssessmentSpeech } from "./session";

describe("getAssessmentTurnSpeech", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("requests the persisted turn by its real index and forwards cancellation", async () => {
    const audio = new Blob(["mp3"], { type: "audio/mpeg" });
    const get = vi.spyOn(api, "get").mockResolvedValue({ data: audio });
    const controller = new AbortController();

    await expect(
      getAssessmentTurnSpeech("voice-session", 11, controller.signal),
    ).resolves.toBe(audio);
    expect(get).toHaveBeenCalledWith(
      "/sessions/voice-session/turns/11/speech",
      {
        responseType: "blob",
        signal: controller.signal,
      },
    );
  });
});

describe("transcribeAssessmentSpeech", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("posts the raw recording with its real MIME type and forwards cancellation", async () => {
    const audio = new Blob(["voice"], { type: "audio/mp4" });
    const result = { text: "转写结果", provider: "doubao", request_id: "asr-1" };
    const post = vi.spyOn(api, "post").mockResolvedValue({ data: result });
    const controller = new AbortController();

    await expect(
      transcribeAssessmentSpeech("voice/session", audio, controller.signal),
    ).resolves.toEqual(result);
    expect(post).toHaveBeenCalledWith(
      "/sessions/voice%2Fsession/speech/transcriptions",
      audio,
      {
        headers: { "Content-Type": "audio/mp4" },
        signal: controller.signal,
        timeout: 30_000,
      },
    );
  });
});
