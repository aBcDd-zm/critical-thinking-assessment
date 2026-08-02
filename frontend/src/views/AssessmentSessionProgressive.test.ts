import { flushPromises, mount } from "@vue/test-utils";
import { createMemoryHistory, createRouter } from "vue-router";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  finishAssessmentSession,
  getAssessmentSession,
  submitAssessmentTurnStream,
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

const opening = "这是后端已经保存的开场。你最想先弄清楚什么？";
const followup = "这是后端已经保存的下一问？";
const profileQuestion = "你平时最常处理哪类任务？";
const profileAnswer = "我主要负责课程项目协调。";

function progressiveSession(
  withAnswer = false,
  progress: Partial<{
    formal_answer_count: number;
    target_min_answers: number;
    target_max_answers: number;
    percent: number;
    estimated_remaining_minutes: number;
    elapsed_seconds: number;
  }> = {},
) {
  const formalAnswerCount = progress.formal_answer_count ?? (withAnswer ? 1 : 0);
  return {
    session_uuid: "progressive-session",
    status: "in_progress",
    flow_version: "progressive_v3" as const,
    phase: "assessment" as const,
    participant_nickname: "小周",
    scenario: {
      scenario_code: "occupation-v3",
      title: "隐藏职业情境",
      background: "不应作为资料板展示",
      estimated_minutes: 25,
      version: "v3",
      source_type: "ai_adapted",
    },
    current_stage: null,
    turns: [
      {
        turn_index: 1,
        speaker: "ai",
        content: profileQuestion,
        content_type: "profile_question",
        created_at: "2026-07-21T11:58:00",
      },
      {
        turn_index: 2,
        speaker: "user",
        content: profileAnswer,
        content_type: "profile_answer",
        created_at: "2026-07-21T11:59:00",
      },
      {
        turn_index: 3,
        speaker: "ai",
        content: opening,
        content_type: "interview_opening",
        created_at: "2026-07-21T12:00:00",
      },
      ...(withAnswer
        ? [
            {
              turn_index: 4,
              speaker: "user",
              content: "我先核实来源。",
              content_type: "scenario_answer",
              created_at: "2026-07-21T12:01:00",
            },
            {
              turn_index: 5,
              speaker: "ai",
              content: followup,
              content_type: "interview_followup",
              created_at: "2026-07-21T12:01:01",
            },
          ]
        : []),
    ],
    interview_progress: {
      formal_answer_count: formalAnswerCount,
      target_min_answers: progress.target_min_answers ?? 9,
      target_max_answers: progress.target_max_answers ?? 12,
      percent: progress.percent ?? (withAnswer ? 8 : 0),
      estimated_remaining_minutes:
        progress.estimated_remaining_minutes ?? (withAnswer ? 16 : 18),
      elapsed_seconds: progress.elapsed_seconds ?? 2,
    },
    progress: null,
    language_mode: "standard" as const,
  };
}

describe("AssessmentSessionView progressive v3", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    Object.defineProperty(HTMLElement.prototype, "scrollTo", {
      configurable: true,
      value: vi.fn(),
    });
    Object.defineProperty(globalThis.crypto, "randomUUID", {
      configurable: true,
      value: vi.fn(() => "11111111-1111-4111-8111-111111111111"),
    });
    vi.mocked(getAssessmentSession)
      .mockResolvedValueOnce(progressiveSession())
      .mockResolvedValue(progressiveSession(true));
  });

  it("renders saved turns verbatim, hides stage structure, and reuses the retry id", async () => {
    const unsortedSession = progressiveSession();
    unsortedSession.turns = [
      unsortedSession.turns[2],
      unsortedSession.turns[0],
      unsortedSession.turns[1],
    ];
    vi.mocked(getAssessmentSession)
      .mockReset()
      .mockResolvedValueOnce(unsortedSession)
      .mockResolvedValue(progressiveSession(true));
    let calls = 0;
    vi.mocked(submitAssessmentTurnStream).mockImplementation(
      async (_sessionUuid, payload, onEvent) => {
        calls += 1;
        onEvent({ event: "user_turn_saved", saved_turn_index: 4 });
        if (calls === 1) throw new Error("simulated interrupted response");
        onEvent({ event: "agent_started" });
        onEvent({ event: "agent_delta", delta: followup });
        onEvent({
          event: "agent_completed",
          ai_turn: {
            turn_index: 5,
            speaker: "ai",
            content: followup,
            content_type: "interview_followup",
            created_at: "2026-07-21T12:01:01",
          },
        });
        expect(payload.answer_duration_ms).not.toBeUndefined();
      },
    );
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        {
          path: "/assessment/session/:sessionUuid",
          component: AssessmentSessionView,
        },
      ],
    });
    await router.push("/assessment/session/progressive-session");
    await router.isReady();
    const wrapper = mount(AssessmentSessionView, {
      global: {
        plugins: [router],
        stubs: {
          DialogueTurn: {
            props: ["turn"],
            template: "<div class='turn-stub'>{{ turn.content }}</div>",
          },
          AnswerComposer: {
            props: ["modelValue"],
            emits: ["update:modelValue", "submit"],
            template:
              "<div><input :value='modelValue' @input='$emit(\"update:modelValue\", $event.target.value)'/><button class='composer-submit' @click='$emit(\"submit\")'>提交</button></div>",
          },
        },
      },
    });
    await flushPromises();

    expect(wrapper.text().match(new RegExp(opening, "g"))).toHaveLength(1);
    expect(wrapper.text()).toContain("已回答 0 轮 · 通常 9–12 轮");
    expect(wrapper.text()).toContain("预计还需约 18 分钟");
    expect(wrapper.text()).toContain("已用时 00:02");
    expect(wrapper.text()).not.toContain("访谈进度 0%");
    expect(wrapper.text()).not.toContain("24:58");
    expect(wrapper.get(".interview-profile-history").attributes("open")).toBeUndefined();
    expect(wrapper.text()).toContain("背景了解（2 条）");
    expect(wrapper.text()).toContain("不计入正式评分");
    expect(wrapper.findAll(".turn-stub").map((turn) => turn.text())).toEqual([
      profileQuestion,
      profileAnswer,
      opening,
    ]);
    expect(wrapper.text()).not.toContain("资料板");
    expect(wrapper.text()).not.toContain("阶段主问题");
    expect(wrapper.text()).not.toContain("追问 0/2");
    expect(wrapper.text()).not.toContain("我会先用一句话说清核心判断");
    expect(wrapper.find('[role="separator"]').exists()).toBe(false);
    expect(wrapper.find(".digital-interviewer-scene").exists()).toBe(false);
    expect(wrapper.find(".reference-office-image").exists()).toBe(false);

    await wrapper.get("input").setValue("我先核实来源。");
    await wrapper.get(".composer-submit").trigger("click");
    await flushPromises();
    await wrapper.get("input").setValue("我先核实来源。");
    await wrapper.get(".composer-submit").trigger("click");
    await flushPromises();

    const payloads = vi.mocked(submitAssessmentTurnStream).mock.calls.map(
      (item) => item[1],
    );
    expect(payloads).toHaveLength(2);
    expect(payloads[0].client_turn_id).toBe(payloads[1].client_turn_id);
    expect(wrapper.text().match(new RegExp(followup, "g"))).toHaveLength(1);
    expect(wrapper.text()).not.toContain("pleased");
    wrapper.unmount();
  });

  it("counts up elapsed time from the persisted server value", async () => {
    vi.useFakeTimers();
    try {
      vi.mocked(getAssessmentSession).mockResolvedValue(progressiveSession());
      const router = createRouter({
        history: createMemoryHistory(),
        routes: [
          {
            path: "/assessment/session/:sessionUuid",
            component: AssessmentSessionView,
          },
        ],
      });
      await router.push("/assessment/session/progressive-session");
      await router.isReady();
      const wrapper = mount(AssessmentSessionView, {
        global: {
          plugins: [router],
          stubs: { DialogueTurn: true, AnswerComposer: true },
        },
      });
      await flushPromises();

      expect(wrapper.text()).toContain("已用时 00:02");
      await vi.advanceTimersByTimeAsync(1000);
      expect(wrapper.text()).toContain("已用时 00:03");
      expect(wrapper.text()).not.toContain("24:57");
      wrapper.unmount();
    } finally {
      vi.useRealTimers();
    }
  });

  it("shows report generation immediately and opens the report after the final stream completes", async () => {
    let finishStream: (() => void) | undefined;
    vi.mocked(getAssessmentSession)
      .mockReset()
      .mockResolvedValueOnce(progressiveSession(true, { formal_answer_count: 8 }))
      .mockResolvedValue({
        ...progressiveSession(true, {
          formal_answer_count: 9,
          percent: 100,
          estimated_remaining_minutes: 0,
        }),
        status: "completed",
        phase: "completed",
      });
    vi.mocked(submitAssessmentTurnStream).mockImplementation(
      async (_sessionUuid, _payload, onEvent) => {
        onEvent({ event: "user_turn_saved", saved_turn_index: 6 });
        onEvent({ event: "agent_started" });
        onEvent({
          event: "agent_completed",
          next_action: "generate_report",
        });
        await new Promise<void>((resolve) => {
          finishStream = resolve;
        });
      },
    );
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        {
          path: "/assessment/session/:sessionUuid",
          component: AssessmentSessionView,
        },
        {
          path: "/assessment/report/:sessionUuid",
          component: { template: "<div>报告页</div>" },
        },
      ],
    });
    await router.push("/assessment/session/progressive-session");
    await router.isReady();
    const wrapper = mount(AssessmentSessionView, {
      global: {
        plugins: [router],
        stubs: {
          DialogueTurn: true,
          AnswerComposer: {
            props: ["modelValue"],
            emits: ["update:modelValue", "submit"],
            template:
              "<div><input :value='modelValue' @input='$emit(\"update:modelValue\", $event.target.value)'/><button class='composer-submit' @click='$emit(\"submit\")'>提交</button></div>",
          },
        },
      },
    });
    await flushPromises();

    await wrapper.get("input").setValue("这是最终判断。");
    await wrapper.get(".composer-submit").trigger("click");
    await flushPromises();

    expect(wrapper.text()).toContain("正在完成测评");
    expect(wrapper.text()).toContain("报告生成中");
    expect(wrapper.text()).toContain("最终回答已记录，正在生成报告");

    finishStream?.();
    await flushPromises();

    expect(router.currentRoute.value.path).toBe(
      "/assessment/report/progressive-session",
    );
    expect(router.currentRoute.value.query).toEqual({ fresh: "1" });
    wrapper.unmount();
  });

  it("recovers a completed final turn by polling when the response stream stalls", async () => {
    vi.useFakeTimers();
    try {
      vi.mocked(getAssessmentSession)
        .mockReset()
        .mockResolvedValueOnce(progressiveSession(true, { formal_answer_count: 8 }))
        .mockResolvedValue({
          ...progressiveSession(true, {
            formal_answer_count: 9,
            percent: 100,
            estimated_remaining_minutes: 0,
          }),
          status: "completed",
          phase: "completed",
        });
      vi.mocked(submitAssessmentTurnStream).mockImplementation(
        async (_sessionUuid, _payload, onEvent) => {
          onEvent({ event: "user_turn_saved", saved_turn_index: 6 });
          await new Promise<void>(() => {});
        },
      );
      const router = createRouter({
        history: createMemoryHistory(),
        routes: [
          {
            path: "/assessment/session/:sessionUuid",
            component: AssessmentSessionView,
          },
          {
            path: "/assessment/report/:sessionUuid",
            component: { template: "<div>报告页</div>" },
          },
        ],
      });
      await router.push("/assessment/session/progressive-session");
      await router.isReady();
      const wrapper = mount(AssessmentSessionView, {
        global: {
          plugins: [router],
          stubs: {
            DialogueTurn: true,
            AnswerComposer: {
              props: ["modelValue"],
              emits: ["update:modelValue", "submit"],
              template:
                "<div><input :value='modelValue' @input='$emit(\"update:modelValue\", $event.target.value)'/><button class='composer-submit' @click='$emit(\"submit\")'>提交</button></div>",
            },
          },
        },
      });
      await flushPromises();

      await wrapper.get("input").setValue("这是最终判断。");
      await wrapper.get(".composer-submit").trigger("click");
      await flushPromises();

      expect(wrapper.text()).toContain("已收到，正在整理你刚才的重点");
      expect(wrapper.text()).not.toContain("正在完成测评");
      expect(wrapper.text()).not.toContain("报告生成中");
      expect(wrapper.text()).not.toContain("最终回答");

      await vi.advanceTimersByTimeAsync(12_000);
      await flushPromises();

      expect(getAssessmentSession).toHaveBeenCalledTimes(2);
      expect(router.currentRoute.value.path).toBe(
        "/assessment/report/progressive-session",
      );
      expect(router.currentRoute.value.query).toEqual({ fresh: "1" });
      wrapper.unmount();
    } finally {
      vi.useRealTimers();
    }
  });

  it("resumes completion polling when a generating session page is reloaded", async () => {
    vi.mocked(getAssessmentSession)
      .mockReset()
      .mockResolvedValueOnce({
        ...progressiveSession(true, { formal_answer_count: 9 }),
        status: "generating",
      })
      .mockResolvedValue({
        ...progressiveSession(true, {
          formal_answer_count: 9,
          percent: 100,
          estimated_remaining_minutes: 0,
        }),
        status: "completed",
        phase: "completed",
      });
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        {
          path: "/assessment/session/:sessionUuid",
          component: AssessmentSessionView,
        },
        {
          path: "/assessment/report/:sessionUuid",
          component: { template: "<div>报告页</div>" },
        },
      ],
    });
    await router.push("/assessment/session/progressive-session");
    await router.isReady();
    const wrapper = mount(AssessmentSessionView, {
      global: {
        plugins: [router],
        stubs: { DialogueTurn: true, AnswerComposer: true },
      },
    });
    await flushPromises();

    expect(getAssessmentSession).toHaveBeenCalledTimes(2);
    expect(router.currentRoute.value.path).toBe(
      "/assessment/report/progressive-session",
    );
    expect(router.currentRoute.value.query).toEqual({ fresh: "1" });
    wrapper.unmount();
  });

  it("stops the automatic wait after the bounded status-check window", async () => {
    vi.useFakeTimers();
    try {
      const generatingSession = {
        ...progressiveSession(true, { formal_answer_count: 8 }),
        status: "generating",
      };
      vi.mocked(getAssessmentSession)
        .mockReset()
        .mockResolvedValueOnce(progressiveSession(true, { formal_answer_count: 8 }))
        .mockResolvedValue(generatingSession);
      vi.mocked(submitAssessmentTurnStream).mockImplementation(
        async (_sessionUuid, _payload, onEvent) => {
          onEvent({ event: "user_turn_saved", saved_turn_index: 6 });
          await new Promise<void>(() => {});
        },
      );
      const router = createRouter({
        history: createMemoryHistory(),
        routes: [
          {
            path: "/assessment/session/:sessionUuid",
            component: AssessmentSessionView,
          },
        ],
      });
      await router.push("/assessment/session/progressive-session");
      await router.isReady();
      const wrapper = mount(AssessmentSessionView, {
        global: {
          plugins: [router],
          stubs: {
            DialogueTurn: true,
            AnswerComposer: {
              props: ["modelValue"],
              emits: ["update:modelValue", "submit"],
              template:
                "<div><input :value='modelValue' @input='$emit(\"update:modelValue\", $event.target.value)'/><button class='composer-submit' @click='$emit(\"submit\")'>提交</button></div>",
            },
          },
        },
      });
      await flushPromises();

      await wrapper.get("input").setValue("需要保留的最终判断。");
      await wrapper.get(".composer-submit").trigger("click");
      await flushPromises();

      expect(wrapper.text()).toContain("已收到，正在整理你刚才的重点");
      expect(wrapper.text()).not.toContain("报告生成中");

      await vi.advanceTimersByTimeAsync(12_000);
      await flushPromises();

      expect(wrapper.text()).toContain("正在确认当前进度");
      expect(wrapper.text()).not.toContain("正在完成测评");
      expect(wrapper.text()).not.toContain("报告生成中");

      await vi.advanceTimersByTimeAsync(48_000);
      await flushPromises();

      expect(wrapper.text()).toContain("已停止自动等待");
      expect((wrapper.get("input").element as HTMLInputElement).value).toBe(
        "需要保留的最终判断。",
      );
      expect(router.currentRoute.value.path).toBe(
        "/assessment/session/progressive-session",
      );
      wrapper.unmount();
    } finally {
      vi.useRealTimers();
    }
  });

  it("does not treat an early generating session as report generation", async () => {
    vi.mocked(getAssessmentSession)
      .mockReset()
      .mockResolvedValueOnce({
        ...progressiveSession(true, { formal_answer_count: 2 }),
        status: "generating",
      })
      .mockResolvedValue({
        ...progressiveSession(true, { formal_answer_count: 2 }),
        status: "in_progress",
      });
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        {
          path: "/assessment/session/:sessionUuid",
          component: AssessmentSessionView,
        },
      ],
    });
    await router.push("/assessment/session/progressive-session");
    await router.isReady();
    const wrapper = mount(AssessmentSessionView, {
      global: {
        plugins: [router],
        stubs: { DialogueTurn: true, AnswerComposer: true },
      },
    });
    await flushPromises();

    expect(wrapper.text()).toContain("已回答 2 轮");
    expect(wrapper.text()).not.toContain("正在完成测评");
    expect(wrapper.text()).not.toContain("报告生成中");
    expect(router.currentRoute.value.path).toBe(
      "/assessment/session/progressive-session",
    );
    wrapper.unmount();
  });

  it("keeps history review in place while new streamed text arrives", async () => {
    const persistedSession = progressiveSession(true);
    vi.mocked(getAssessmentSession).mockReset().mockResolvedValue(persistedSession);
    vi.mocked(submitAssessmentTurnStream).mockImplementation(
      async (_sessionUuid, _payload, onEvent) => {
        onEvent({ event: "user_turn_saved", saved_turn_index: 6 });
        onEvent({ event: "agent_started" });
        onEvent({ event: "agent_delta", delta: "新的追问内容？" });
        onEvent({ event: "agent_completed" });
      },
    );
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        {
          path: "/assessment/session/:sessionUuid",
          component: AssessmentSessionView,
        },
      ],
    });
    await router.push("/assessment/session/progressive-session");
    await router.isReady();
    const wrapper = mount(AssessmentSessionView, {
      global: {
        plugins: [router],
        stubs: {
          DialogueTurn: {
            props: ["turn"],
            template: "<div class='turn-stub'>{{ turn.content }}</div>",
          },
          AnswerComposer: {
            props: ["modelValue"],
            emits: ["update:modelValue", "submit"],
            template:
              "<div><input :value='modelValue' @input='$emit(\"update:modelValue\", $event.target.value)'/><button class='composer-submit' @click='$emit(\"submit\")'>提交</button></div>",
          },
        },
      },
    });
    await flushPromises();

    const transcript = wrapper.get(".interview-transcript");
    Object.defineProperties(transcript.element, {
      scrollHeight: { configurable: true, value: 1200 },
      clientHeight: { configurable: true, value: 400 },
      scrollTop: { configurable: true, writable: true, value: 120 },
    });
    await transcript.trigger("scroll");
    expect(wrapper.text()).toContain("回到最新");

    await wrapper.get(".interview-history-actions button").trigger("click");
    await flushPromises();
    expect(wrapper.get(".interview-profile-history").attributes("open")).toBeDefined();

    await wrapper.get("input").setValue("我补充一个判断。");
    await wrapper.get(".composer-submit").trigger("click");
    await flushPromises();

    expect(wrapper.text()).toContain("有新消息 · 回到最新");
    expect(HTMLElement.prototype.scrollTo).toHaveBeenCalledWith({
      top: 0,
      behavior: "auto",
    });
    wrapper.unmount();
  });

  it("shows the closing range after nine answers and confirms early finish", async () => {
    vi.mocked(getAssessmentSession).mockReset().mockResolvedValue(
      progressiveSession(true, {
        formal_answer_count: 9,
        percent: 75,
        estimated_remaining_minutes: 2,
      }),
    );
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        {
          path: "/assessment/session/:sessionUuid",
          component: AssessmentSessionView,
        },
      ],
    });
    await router.push("/assessment/session/progressive-session");
    await router.isReady();
    const wrapper = mount(AssessmentSessionView, {
      global: {
        plugins: [router],
        stubs: { DialogueTurn: true, AnswerComposer: true },
      },
    });
    await flushPromises();

    expect(wrapper.text()).toContain("已回答 9 轮 · 通常 9–12 轮");
    expect(wrapper.text()).toContain("正在收束 · 最多还有 3 轮");
    expect(wrapper.text()).not.toContain("预计还需约 0 分钟");

    await wrapper.get(".interview-end-button").trigger("click");
    expect(confirmSpy).toHaveBeenCalledWith(
      expect.stringContaining("提前结束可能导致部分维度显示“证据不足”"),
    );
    expect(finishAssessmentSession).not.toHaveBeenCalled();
    wrapper.unmount();
  });

  it.each([
    {
      count: 8,
      remainingMinutes: 2,
      expected: "预计还需约 2 分钟",
    },
    {
      count: 11,
      remainingMinutes: 2,
      expected: "正在收束 · 最多还有 1 轮",
    },
  ])("renders the active $count-answer progress state", async ({
    count,
    remainingMinutes,
    expected,
  }) => {
    vi.mocked(getAssessmentSession).mockReset().mockResolvedValue(
      progressiveSession(true, {
        formal_answer_count: count,
        estimated_remaining_minutes: remainingMinutes,
      }),
    );
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        {
          path: "/assessment/session/:sessionUuid",
          component: AssessmentSessionView,
        },
      ],
    });
    await router.push("/assessment/session/progressive-session");
    await router.isReady();
    const wrapper = mount(AssessmentSessionView, {
      global: {
        plugins: [router],
        stubs: { DialogueTurn: true, AnswerComposer: true },
      },
    });
    await flushPromises();

    expect(wrapper.text()).toContain(`已回答 ${count} 轮 · 通常 9–12 轮`);
    expect(wrapper.text()).toContain(expected);
    wrapper.unmount();
  });

  it("replaces a completed session URL with its report on load", async () => {
    vi.mocked(getAssessmentSession).mockReset().mockResolvedValue({
      ...progressiveSession(true, {
        formal_answer_count: 11,
        percent: 100,
        estimated_remaining_minutes: 0,
        elapsed_seconds: 915,
      }),
      status: "completed",
      phase: "completed",
    });
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        {
          path: "/assessment/session/:sessionUuid",
          component: AssessmentSessionView,
        },
        {
          path: "/assessment/report/:sessionUuid",
          component: { template: "<div>报告页</div>" },
        },
      ],
    });
    await router.push("/assessment/session/progressive-session");
    await router.isReady();
    const replaceSpy = vi.spyOn(router, "replace");
    const wrapper = mount(AssessmentSessionView, {
      global: {
        plugins: [router],
        stubs: { DialogueTurn: true, AnswerComposer: true },
      },
    });
    await flushPromises();

    expect(replaceSpy).toHaveBeenCalledWith({
      path: "/assessment/report/progressive-session",
      query: { fresh: "1" },
    });
    expect(router.currentRoute.value.path).toBe(
      "/assessment/report/progressive-session",
    );
    expect(router.currentRoute.value.query).toEqual({ fresh: "1" });
    wrapper.unmount();
  });
});
