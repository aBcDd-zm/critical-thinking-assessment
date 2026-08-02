import { flushPromises, mount } from "@vue/test-utils";
import { createMemoryHistory, createRouter } from "vue-router";
import { beforeEach, describe, expect, it, vi } from "vitest";
import AssessmentReportView from "./AssessmentReportView.vue";
import {
  downloadAssessmentReportPdf,
  getAssessmentFeedback,
  getAssessmentReport,
  getAssessmentSession,
  requestAssessmentReportGeneration,
  submitAssessmentFeedback,
} from "../api/session";

vi.mock("../api/session", () => ({
  downloadAssessmentReportPdf: vi.fn(),
  getAssessmentFeedback: vi.fn(),
  getAssessmentReport: vi.fn(),
  getAssessmentSession: vi.fn(),
  requestAssessmentReportGeneration: vi.fn(),
  submitAssessmentFeedback: vi.fn(),
}));

const feedback = {
  session_uuid: "session-1",
  realism_score: 4,
  difficulty_score: 3,
  naturalness_score: 4,
  fatigue_score: 3,
  report_trust_score: 4,
  overall_satisfaction_score: 4,
  open_feedback: "整体自然",
  submitted_at: "2026-07-17T12:00:00",
};

async function mountView() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/assessment", component: { template: "<div />" } },
      { path: "/assessment/report/:sessionUuid", component: AssessmentReportView },
      { path: "/assessment/session/:sessionUuid", component: { template: "<div />" } },
    ],
  });
  await router.push("/assessment/report/session-1");
  await router.isReady();
  const wrapper = mount(AssessmentReportView, { global: { plugins: [router] } });
  await flushPromises();
  return wrapper;
}

describe("AssessmentReportView", () => {
  beforeEach(() => {
    vi.mocked(getAssessmentSession).mockResolvedValue({
      session_uuid: "session-1",
      status: "completed",
      phase: "completed",
      participant_nickname: "测试用户",
      scenario: {
        scenario_code: "product_launch_48h",
        title: "产品上线前 48 小时",
        background: "测试背景",
        estimated_minutes: 30,
        version: "v1",
        source_type: "seeded_fallback",
      },
      current_stage: null,
      turns: [],
      language_mode: "standard",
    });
    vi.mocked(getAssessmentFeedback).mockResolvedValue({
      session_uuid: "session-1",
      submitted: false,
      feedback: null,
    });
    vi.mocked(getAssessmentReport).mockResolvedValue({
      session_uuid: "session-1",
      status: "generated",
      report: {
        status: "ok",
        agent_name: "report",
        summary: "能够围绕情境任务形成判断。",
        overall_level: "high",
        dimension_reports: [
          {
            dimension_key: "problem_definition",
            dimension_name: "问题界定",
            score: 4,
            assessment_status: "scored",
            level_label: "high",
            strength: "能识别核心问题。",
            weakness: null,
            evidence_quotes: ["我会先界定核心问题。"],
            suggestion: "继续明确约束。",
            evidence_sufficiency_index: 72,
            evidence_sufficiency_level: "medium",
            score_kind: "supported",
            evidence_sufficiency_note: "表示本次能力判断的证据基础。",
          },
        ],
        advantages: [],
        improvement_suggestions: [],
        development_plan: [],
        disclaimer: "仅用于学习参考。",
        fallback_used: false,
        warnings: [],
        measurement_quality: {
          status: "valid",
          technical_failure_rate: 0,
          total_fallback_rate: 0,
          missing_events: [],
          retest_recommended: true,
          reasons: [],
          overall_evidence_sufficiency_index: 12,
        },
      },
    });
    vi.mocked(submitAssessmentFeedback).mockResolvedValue(feedback);
    vi.mocked(requestAssessmentReportGeneration).mockResolvedValue({
      session_uuid: "session-1",
      status: "scheduled",
    });
    vi.mocked(downloadAssessmentReportPdf).mockResolvedValue(new Blob(["%PDF-test"]));
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      value: vi.fn(() => "blob:report"),
    });
    Object.defineProperty(URL, "revokeObjectURL", {
      configurable: true,
      value: vi.fn(),
    });
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
  });

  it("treats missing feedback as a normal state and renders Chinese labels", async () => {
    const wrapper = await mountView();
    expect(wrapper.text()).toContain("受测者");
    expect(wrapper.text()).toContain("对话轮次");
    expect(wrapper.text()).toContain("体验反馈");
    expect(wrapper.text()).toContain("已完成");
    expect(wrapper.text()).toContain("较强");
    expect(wrapper.text()).toContain("ESI 72/100 · 中");
    expect(wrapper.text()).toContain("证据基础指数（ESI）");
    expect(wrapper.text()).toContain("整体证据基础指数（ESI） 12/100");
    expect(wrapper.text()).not.toContain("证据充分度");
    expect(wrapper.text()).toContain("已评分维度均分");
    expect(wrapper.text()).toContain(
      "当前仅有 1/6 个维度达到评分条件，该均分不代表六维综合水平",
    );
    expect(wrapper.get(".report-hero-actions a").text()).toBe("返回测评首页");
    expect(wrapper.get(".report-hero-actions a").attributes("href")).toBe("/assessment");
    expect(wrapper.find(".report-empty-state").exists()).toBe(false);
    expect(wrapper.get(".feedback-actions button").text()).toBe("提交反馈");
  });

  it("shows an explicit background-generation state while the report is pending", async () => {
    vi.mocked(getAssessmentReport).mockImplementationOnce(
      () => new Promise(() => undefined),
    );
    const wrapper = await mountView();
    expect(wrapper.text()).toContain("报告生成中");
    expect(wrapper.text()).toContain("访谈已经完成，正在整理评分与报告");
    expect(wrapper.text()).toContain("页面会自动刷新");
    expect(wrapper.find(".report-download-button").exists()).toBe(false);
    expect(wrapper.find(".feedback-panel").exists()).toBe(false);
    expect(getAssessmentFeedback).not.toHaveBeenCalled();
    expect(downloadAssessmentReportPdf).not.toHaveBeenCalled();
    expect(submitAssessmentFeedback).not.toHaveBeenCalled();
    wrapper.unmount();
  });

  it("requests one idempotent recovery when a completed report is initially missing", async () => {
    vi.useFakeTimers();
    try {
      const generated = await getAssessmentReport("session-1");
      const notReady = Object.assign(new Error("report pending"), {
        isAxiosError: true,
        response: { status: 404 },
      });
      vi.mocked(getAssessmentReport)
        .mockRejectedValueOnce(notReady)
        .mockResolvedValueOnce(generated);

      const wrapper = await mountView();
      await flushPromises();
      expect(requestAssessmentReportGeneration).toHaveBeenCalledTimes(1);
      expect(requestAssessmentReportGeneration).toHaveBeenCalledWith("session-1");

      await vi.advanceTimersByTimeAsync(1000);
      await flushPromises();
      expect(wrapper.text()).toContain("六维评分概览");
      expect(requestAssessmentReportGeneration).toHaveBeenCalledTimes(1);
      wrapper.unmount();
    } finally {
      vi.useRealTimers();
    }
  });

  it("normalizes a legacy numeric provisional score out of ability scoring", async () => {
    const current = await getAssessmentReport("session-1");
    vi.mocked(getAssessmentReport).mockResolvedValueOnce({
      ...current,
      report: {
        ...current.report,
        dimension_reports: [
          {
            ...current.report.dimension_reports[0],
            score: 4,
            score_kind: "provisional",
          },
        ],
      },
    });

    const wrapper = await mountView();

    expect(wrapper.text()).toContain("暂不评分");
    expect(wrapper.text()).not.toContain("当前仅有 1/6 个维度达到评分条件");
    expect(wrapper.find(".score-scale").exists()).toBe(false);
    expect(wrapper.get(".dimension-level").text()).toBe("暂不评分");
    expect(wrapper.text()).toContain("关键评分证据：未达标");
    expect(wrapper.text()).toContain("已获得部分相关证据，但仍缺关键项");
    wrapper.unmount();
  });

  it("keeps feedback hidden until existing feedback has loaded", async () => {
    let resolveFeedback: ((value: Awaited<ReturnType<typeof getAssessmentFeedback>>) => void) | undefined;
    vi.mocked(getAssessmentFeedback).mockImplementationOnce(
      () => new Promise((resolve) => {
        resolveFeedback = resolve;
      }),
    );

    const wrapper = await mountView();

    expect(wrapper.text()).toContain("六维评分概览");
    expect(wrapper.text()).not.toContain("报告生成中");
    expect(wrapper.find(".feedback-panel").exists()).toBe(false);

    resolveFeedback?.({
      session_uuid: "session-1",
      submitted: true,
      feedback,
    });
    await flushPromises();

    expect(wrapper.find(".feedback-panel").exists()).toBe(true);
    expect(wrapper.get(".feedback-actions button").text()).toBe("更新反馈");
    wrapper.unmount();
  });

  it("downloads the server PDF and can submit feedback after an empty state", async () => {
    const wrapper = await mountView();
    await wrapper.get(".report-download-button").trigger("click");
    await flushPromises();
    expect(downloadAssessmentReportPdf).toHaveBeenCalledWith("session-1");
    expect(HTMLAnchorElement.prototype.click).toHaveBeenCalled();
    expect(wrapper.text()).toContain("PDF 已生成");

    await wrapper.get(".feedback-actions button").trigger("click");
    await flushPromises();
    expect(submitAssessmentFeedback).toHaveBeenCalledTimes(1);
    expect(wrapper.get(".feedback-actions button").text()).toBe("更新反馈");
  });

  it("prefills existing feedback and shows a stable PDF failure state", async () => {
    vi.mocked(getAssessmentFeedback).mockResolvedValue({
      session_uuid: "session-1",
      submitted: true,
      feedback,
    });
    vi.mocked(downloadAssessmentReportPdf).mockRejectedValue(new Error("PDF unavailable"));
    const wrapper = await mountView();

    expect(wrapper.get(".feedback-actions button").text()).toBe("更新反馈");
    expect(
      (wrapper.get(".feedback-open textarea").element as HTMLTextAreaElement).value,
    ).toBe("整体自然");
    await wrapper.get(".report-download-button").trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("PDF 下载失败，请稍后重试");
    expect(wrapper.get(".report-download-button").attributes("disabled")).toBeUndefined();
  });

  it("does not display the exact occupation through an adapted scenario title", async () => {
    vi.mocked(getAssessmentSession).mockResolvedValueOnce({
      session_uuid: "session-1",
      status: "completed",
      phase: "completed",
      participant_nickname: "测试用户",
      scenario: {
        scenario_code: "ai_adapted_demo",
        title: "高中生物教师的课堂协作调整",
        background: "测试背景",
        estimated_minutes: 30,
        version: "v1",
        source_type: "ai_adapted",
      },
      current_stage: null,
      turns: [],
      language_mode: "standard",
    });
    const wrapper = await mountView();
    expect(wrapper.text()).toContain("职业适配协作判断情景");
    expect(wrapper.text()).not.toContain("高中生物教师");
  });

  it("distinguishes report-text fallback from a valid measurement process", async () => {
    const current = await getAssessmentReport("session-1");
    vi.mocked(getAssessmentReport).mockResolvedValueOnce({
      ...current,
      report: {
        ...current.report,
        fallback_used: true,
        warnings: ["MODEL_ERROR: report renderer fallback"],
      },
    });
    const wrapper = await mountView();
    expect(wrapper.text()).toContain("报告文字采用确定性降级生成；测量质量仍为有效");
    expect(wrapper.text()).not.toContain("本次结果包含降级生成或证据不足提示");
    expect(wrapper.find(".measurement-quality-alert").exists()).toBe(false);
  });

  it("marks an invalid measurement process and offers a retest", async () => {
    const current = await getAssessmentReport("session-1");
    vi.mocked(getAssessmentReport).mockResolvedValueOnce({
      ...current,
      report: {
        ...current.report,
        summary: "测评过程异常，结果不宜解释，建议重新测评。",
        fallback_used: true,
        measurement_quality: {
          status: "invalid",
          technical_failure_rate: 0.56,
          total_fallback_rate: 0.94,
          missing_events: ["counter_evidence", "integration"],
          retest_recommended: true,
          reasons: ["总回退率达到或超过50%"],
          overall_evidence_sufficiency_index: null,
        },
      },
    });
    const wrapper = await mountView();
    expect(wrapper.text()).toContain("测评过程异常，结果不宜解释");
    expect(wrapper.text()).toContain("报告文字采用确定性降级生成；测量质量请以下方提示为准");
    expect(wrapper.text()).toContain("总回退率达到或超过50%");
    expect(wrapper.get(".measurement-quality-alert button").text()).toBe("重新测评");
  });

  it("shows caution without treating an unobserved dimension as low ability", async () => {
    const current = await getAssessmentReport("session-1");
    vi.mocked(getAssessmentReport).mockResolvedValueOnce({
      ...current,
      report: {
        ...current.report,
        summary: "本次仅形成部分维度结果。",
        overall_level: "部分结果",
        dimension_reports: [
          {
            ...current.report.dimension_reports[0],
            score: null,
            assessment_status: "insufficient_evidence",
            level_label: "暂不评分",
            evidence_quotes: [],
            score_kind: "provisional",
            evidence_sufficiency_index: 90,
            evidence_sufficiency_level: "high",
          },
          {
            ...current.report.dimension_reports[0],
            dimension_key: "multiple_perspectives",
            dimension_name: "多元视角",
            score: null,
            assessment_status: "insufficient_evidence",
            level_label: "未测到",
            evidence_quotes: [],
            score_kind: "unobserved",
            evidence_sufficiency_index: null,
            evidence_sufficiency_level: null,
          },
        ],
        measurement_quality: {
          status: "caution",
          technical_failure_rate: 0,
          total_fallback_rate: 0,
          missing_events: [],
          unobserved_dimensions: ["multiple_perspectives"],
          provisional_dimensions: [],
          scoring_contamination_turn_ids: [],
          retest_recommended: true,
          reasons: ["存在未测到维度，不得解释为能力不足"],
          overall_evidence_sufficiency_index: 68,
        },
      },
    });
    const wrapper = await mountView();
    expect(wrapper.text()).toContain("本次报告仅形成部分可解释结果");
    expect(wrapper.text()).toContain("未测到或证据未充分的维度不会被解释为能力不足");
    expect(wrapper.text()).toContain("存在未测到维度，不得解释为能力不足");
    expect(wrapper.text()).toContain("暂不评分");
    expect(wrapper.text()).toContain("关键评分证据：未达标");
    const provisionalCard = wrapper.findAll(".dimension-report-card")[0];
    expect(provisionalCard.text()).toContain("证据说明");
    expect(provisionalCard.text()).not.toContain("优势");
    expect(provisionalCard.text()).toContain(
      "已获得部分有效证据，但尚不足以形成可引用的评分证据",
    );
    expect(provisionalCard.text()).not.toContain("未提供该维度的有效证据");
    expect(wrapper.text()).toContain(
      "仍缺关键证据的维度即使 ESI 较高，也不会进入能力评分",
    );
    expect(wrapper.text()).toContain("未测到");
    expect(wrapper.get(".measurement-quality-alert button").text()).toBe("重新测评");
  });
});
