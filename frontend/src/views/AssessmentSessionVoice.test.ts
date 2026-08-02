import { defineComponent, type Ref } from "vue";
import { flushPromises, mount } from "@vue/test-utils";
import { createMemoryHistory, createRouter } from "vue-router";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  getAssessmentPreparation,
  getAssessmentSession,
  submitProfileTurnStream,
} from "../api/session";
import AssessmentSessionView from "./AssessmentSessionView.vue";

const speechHarness = vi.hoisted(() => ({
  disable: vi.fn(),
  enable: vi.fn(),
  enableAndSpeakTurn: vi.fn(),
  enabled: null as Ref<boolean> | null,
  speakTurn: vi.fn(),
  stop: vi.fn(),
}));

vi.mock("../api/session", () => ({
  continueCurrentAssessmentStage: vi.fn(),
  finishAssessmentSession: vi.fn(),
  getAssessmentPreparation: vi.fn(),
  getAssessmentSession: vi.fn(),
  skipCurrentAssessmentStage: vi.fn(),
  startAssessmentInterviewStream: vi.fn(),
  submitAssessmentTurnStream: vi.fn(),
  submitProfileTurnStream: vi.fn(),
}));

vi.mock("../composables/useSpeechPlayback", async () => {
  const { ref } = await import("vue");
  const enabled = ref(false);
  speechHarness.enabled = enabled;
  speechHarness.enable.mockImplementation(() => {
    enabled.value = true;
  });
  speechHarness.enableAndSpeakTurn.mockImplementation(() => {
    enabled.value = true;
  });
  speechHarness.disable.mockImplementation(() => {
    enabled.value = false;
  });
  return {
    useSpeechPlayback: () => ({
      disable: speechHarness.disable,
      enable: speechHarness.enable,
      enableAndSpeakTurn: speechHarness.enableAndSpeakTurn,
      enabled,
      isSupported: ref(true),
      notice: ref(""),
      speakTurn: speechHarness.speakTurn,
      status: ref("ready"),
      statusText: ref("声音已启用"),
      stop: speechHarness.stop,
    }),
  };
});

const currentQuestion = "请说说你会如何核实这条信息？";

function sessionFixture() {
  return {
    session_uuid: "voice-session",
    status: "in_progress",
    flow_version: "progressive_v3_3",
    phase: "assessment",
    participant_nickname: "小周",
    scenario: {
      scenario_code: "voice-v3",
      title: "语音测试情境",
      background: "测试背景",
      estimated_minutes: 25,
      version: "v3",
      source_type: "ai_adapted",
    },
    current_stage: null,
    turns: [
      {
        turn_index: 3,
        speaker: "ai",
        content: currentQuestion,
        content_type: "interview_opening",
        created_at: "2026-07-28T10:00:00",
      },
    ],
    interview_progress: {
      formal_answer_count: 0,
      target_min_answers: 9,
      target_max_answers: 12,
      percent: 0,
      estimated_remaining_minutes: 18,
      elapsed_seconds: 0,
    },
    progress: null,
    language_mode: "standard",
  };
}

const AnswerComposerStub = defineComponent({
  props: ["modelValue"],
  emits: ["speech-state-change", "submit", "update:modelValue"],
  template: `
    <div>
      <input
        class="answer-input"
        :value="modelValue"
        @input="$emit('update:modelValue', $event.target.value)"
      />
      <button class="submit-answer" @click="$emit('submit')">提交</button>
      <button class="start-speech-input" @click="$emit('speech-state-change', 'starting')">
        启动语音转文字
      </button>
      <button class="review-speech-input" @click="$emit('speech-state-change', 'review')">
        确认识别文字
      </button>
    </div>
  `,
});

const DialogueTurnStub = defineComponent({
  props: ["speakerLabel"],
  template: '<div class="turn-speaker-label">{{ speakerLabel }}</div>',
});

describe("AssessmentSessionView voice mutual exclusion", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    if (speechHarness.enabled) speechHarness.enabled.value = false;
    Object.defineProperty(HTMLElement.prototype, "scrollTo", {
      configurable: true,
      value: vi.fn(),
    });
    vi.mocked(getAssessmentSession).mockResolvedValue(sessionFixture() as never);
  });

  it("reads the current question immediately and blocks playback while speech input is active", async () => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        {
          path: "/assessment/session/:sessionUuid",
          component: AssessmentSessionView,
        },
      ],
    });
    await router.push("/assessment/session/voice-session");
    await router.isReady();
    const wrapper = mount(AssessmentSessionView, {
      global: {
        plugins: [router],
        stubs: {
          AnswerComposer: AnswerComposerStub,
          DialogueTurn: DialogueTurnStub,
        },
      },
    });
    await flushPromises();

    expect(speechHarness.enableAndSpeakTurn).not.toHaveBeenCalled();
    expect(speechHarness.speakTurn).not.toHaveBeenCalled();
    await wrapper.get(".interview-read-button").trigger("click");
    expect(speechHarness.enableAndSpeakTurn).toHaveBeenCalledWith({
      sessionUuid: "voice-session",
      turnIndex: 3,
      text: currentQuestion,
    });
    expect(wrapper.findAll(".interview-voice-controls button")).toHaveLength(1);
    expect(wrapper.text()).toContain("罗杰斯教授");

    await wrapper.get(".start-speech-input").trigger("click");
    expect(speechHarness.stop).toHaveBeenCalledTimes(1);
    expect(wrapper.get(".interview-read-button").attributes("disabled")).toBeDefined();

    await wrapper.get(".review-speech-input").trigger("click");
    expect(wrapper.get(".interview-read-button").attributes("disabled")).toBeUndefined();
    wrapper.unmount();
  });

  it("does not reread an old profile question and reads the new formal opening after preparation", async () => {
    vi.useFakeTimers();
    try {
      if (speechHarness.enabled) speechHarness.enabled.value = true;
      const initial = {
        ...sessionFixture(),
        status: "pending",
        flow_version: "progressive_v3",
        phase: "onboarding",
        turns: [
          {
            turn_index: 1,
            speaker: "ai",
            content: "你平时最常处理哪类任务？",
            content_type: "profile_question",
            created_at: "2026-07-28T09:00:00",
          },
        ],
        onboarding: { question_count: 1, max_questions: 3, completed: false },
        scenario_preparation: { status: "pending", message: "等待背景了解" },
      };
      const preparing = {
        ...initial,
        phase: "scenario_preparing",
        turns: [
          ...initial.turns,
          {
            turn_index: 2,
            speaker: "user",
            content: "我主要负责项目协调。",
            content_type: "profile_answer",
            created_at: "2026-07-28T09:01:00",
          },
        ],
        onboarding: { question_count: 1, max_questions: 3, completed: true },
        scenario_preparation: { status: "running", message: "正在准备情景" },
      };
      const formalOpening = "正式测评开始：面对这条信息，你会先核实什么？";
      const ready = {
        ...sessionFixture(),
        flow_version: "progressive_v3",
        turns: [
          ...preparing.turns,
          {
            turn_index: 3,
            speaker: "ai",
            content: formalOpening,
            content_type: "stage_question",
            created_at: "2026-07-28T09:02:00",
          },
          {
            turn_index: 4,
            speaker: "ai",
            content: "内部继续控制指令",
            content_type: "stage_continue",
            created_at: "2026-07-28T09:02:01",
          },
        ],
      };
      vi.mocked(getAssessmentSession)
        .mockReset()
        .mockResolvedValueOnce(initial as never)
        .mockResolvedValueOnce(preparing as never)
        .mockResolvedValueOnce(ready as never);
      vi.mocked(submitProfileTurnStream).mockImplementation(
        async (_sessionUuid, _content, onEvent) => {
          onEvent({ event: "profile_answer_saved", saved_turn_index: 2 });
          onEvent({ event: "profile_completed" });
        },
      );
      vi.mocked(getAssessmentPreparation).mockResolvedValue({
        assessment_ready: true,
        phase: "assessment",
        onboarding: preparing.onboarding,
        scenario_preparation: { status: "completed", message: "情景已就绪" },
      } as never);
      const router = createRouter({
        history: createMemoryHistory(),
        routes: [
          {
            path: "/assessment/session/:sessionUuid",
            component: AssessmentSessionView,
          },
        ],
      });
      await router.push("/assessment/session/voice-session");
      await router.isReady();
      const wrapper = mount(AssessmentSessionView, {
        global: {
          plugins: [router],
          stubs: {
            AnswerComposer: AnswerComposerStub,
            DialogueTurn: DialogueTurnStub,
          },
        },
      });
      await flushPromises();

      await wrapper.get(".answer-input").setValue("我主要负责项目协调。");
      await wrapper.get(".submit-answer").trigger("click");
      await flushPromises();
      expect(speechHarness.speakTurn).not.toHaveBeenCalled();

      await vi.advanceTimersByTimeAsync(2000);
      await flushPromises();
      expect(speechHarness.speakTurn).toHaveBeenCalledTimes(1);
      expect(speechHarness.speakTurn).toHaveBeenCalledWith({
        sessionUuid: "voice-session",
        turnIndex: 3,
        text: formalOpening,
      });
      expect(speechHarness.speakTurn).not.toHaveBeenCalledWith(
        expect.objectContaining({ text: "内部继续控制指令" }),
      );
      wrapper.unmount();
    } finally {
      vi.useRealTimers();
    }
  });
});
