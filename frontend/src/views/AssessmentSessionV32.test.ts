import { flushPromises, mount } from "@vue/test-utils";
import { createMemoryHistory, createRouter } from "vue-router";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  getAssessmentSession,
  startAssessmentInterviewStream,
} from "../api/session";
import AssessmentSessionView from "./AssessmentSessionView.vue";

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

const base = {
  session_uuid: "v32-session",
  flow_version: "progressive_v3_2" as const,
  participant_nickname: "小周",
  scenario: {
    scenario_code: "skeleton-v32",
    title: "课程小组作业的协作安排",
    background: "后台最小骨架",
    estimated_minutes: 25,
    version: "v3.2",
    source_type: "progressive_skeleton",
  },
  current_stage: null,
  progress: null,
  language_mode: "standard" as const,
  onboarding: { question_count: 2, max_questions: 3, completed: true },
  scenario_preparation: {
    status: "skeleton_ready",
    cache_hit: false,
    fallback_used: false,
  },
};

describe("AssessmentSessionView progressive v3.2 opening", () => {
  beforeEach(() => {
    Object.defineProperty(HTMLElement.prototype, "scrollTo", {
      configurable: true,
      value: vi.fn(),
    });
    vi.clearAllMocks();
  });

  it("starts the saved opening automatically without preparation polling", async () => {
    const opening = "小周，你正和同学完成课程小组作业。你最想先理清哪一点？";
    vi.mocked(getAssessmentSession)
      .mockResolvedValueOnce({
        ...base,
        status: "opening_pending",
        phase: "opening_pending" as const,
        turns: [],
      })
      .mockResolvedValue({
        ...base,
        status: "in_progress",
        phase: "assessment" as const,
        turns: [
          {
            turn_index: 5,
            speaker: "ai",
            content: opening,
            content_type: "interview_opening",
            created_at: "2026-07-22T12:00:00",
          },
        ],
        interview_progress: {
          formal_answer_count: 0,
          target_min_answers: 9,
          target_max_answers: 12,
          percent: 0,
          estimated_remaining_minutes: 18,
        },
      });
    vi.mocked(startAssessmentInterviewStream).mockImplementation(
      async (_uuid, onEvent) => {
        onEvent({ event: "agent_started" });
        onEvent({ event: "agent_delta", delta: opening });
        onEvent({ event: "agent_completed", replayed: false });
      },
    );
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: "/assessment/session/:sessionUuid", component: AssessmentSessionView }],
    });
    await router.push("/assessment/session/v32-session");
    await router.isReady();
    const wrapper = mount(AssessmentSessionView, {
      global: {
        plugins: [router],
        stubs: {
          AnswerComposer: true,
          DialogueTurn: {
            props: ["turn"],
            template: "<div class='turn-stub'>{{ turn.content }}</div>",
          },
        },
      },
    });
    await flushPromises();

    expect(startAssessmentInterviewStream).toHaveBeenCalledTimes(1);
    expect(wrapper.text()).toContain(opening);
    expect(wrapper.text()).not.toContain("正在生成职业基础情景");
    expect(wrapper.text()).not.toContain("正在做情景适配");
    expect(wrapper.text()).not.toContain("平台运营");
    wrapper.unmount();
  });
});
