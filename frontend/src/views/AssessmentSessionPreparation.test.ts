import { flushPromises, mount } from "@vue/test-utils";
import { createMemoryHistory, createRouter } from "vue-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { getAssessmentSession } from "../api/session";
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

describe("AssessmentSessionView preparation phase", () => {
  beforeEach(() => {
    Object.defineProperty(HTMLElement.prototype, "scrollTo", {
      configurable: true,
      value: vi.fn(),
    });
    vi.mocked(getAssessmentSession).mockResolvedValue({
      session_uuid: "prepare-session",
      status: "scenario_preparing",
      phase: "scenario_preparing",
      participant_nickname: "小周",
      scenario: {
        scenario_code: "general_cctst_fallback_v2",
        title: "团队协作任务调整",
        background: "通用背景",
        estimated_minutes: 30,
        version: "v1",
        source_type: "seeded_fallback",
      },
      current_stage: null,
      turns: [
        {
          turn_index: 1,
          speaker: "ai",
          content: "你平时最常处理哪类任务？",
          content_type: "profile_question",
          created_at: "2026-07-17T12:00:00",
        },
        {
          turn_index: 2,
          speaker: "user",
          content: "安排课程和课堂活动",
          content_type: "profile_answer",
          created_at: "2026-07-17T12:01:00",
        },
      ],
      language_mode: "standard",
      onboarding: { question_count: 2, max_questions: 3, completed: true },
      scenario_preparation: {
        status: "fallback",
        cache_hit: false,
        fallback_used: true,
        message: "个性化情景暂不可用，已切换为通用情景。",
      },
    });
  });

  afterEach(() => vi.restoreAllMocks());

  it("shows the non-scored interview and fallback state without formal prompts", async () => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        {
          path: "/assessment/session/:sessionUuid",
          component: AssessmentSessionView,
        },
      ],
    });
    await router.push("/assessment/session/prepare-session");
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

    expect(wrapper.text()).toContain("不计入六维评分");
    expect(wrapper.text()).toContain("安排课程和课堂活动");
    expect(wrapper.text()).toContain("个性化情景暂不可用，已切换为通用情景");
    expect(wrapper.text()).toContain("六阶段能力结构、追问次数和评分规则保持固定");
    expect(wrapper.text()).not.toContain("我会先用一句话说清核心判断");
    const resizer = wrapper.get('[role="separator"]');
    expect(resizer.attributes("aria-label")).toBe("调整对话区和资料板宽度");
    expect(resizer.attributes("aria-controls")).toContain("assessment-context-panel");
    wrapper.unmount();
  });
});
