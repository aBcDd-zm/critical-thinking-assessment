import { flushPromises, mount } from "@vue/test-utils";
import { createMemoryHistory, createRouter } from "vue-router";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createAssessmentSession } from "../api/session";
import AssessmentStartView from "./AssessmentStartView.vue";

vi.mock("../api/session", () => ({ createAssessmentSession: vi.fn() }));

async function mountView() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/assessment", component: AssessmentStartView },
      { path: "/assessment/session/:sessionUuid", component: { template: "<div />" } },
    ],
  });
  await router.push("/assessment");
  await router.isReady();
  const wrapper = mount(AssessmentStartView, {
    global: {
      plugins: [router],
      stubs: { TypewriterText: { props: ["text"], template: "<span>{{ text }}</span>" } },
    },
  });
  return { router, wrapper };
}

describe("AssessmentStartView occupation onboarding", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.mocked(createAssessmentSession).mockResolvedValue({
      session_uuid: "occupation-session",
      status: "onboarding",
      phase: "onboarding",
      participant_nickname: "小林",
      scenario: {
        scenario_code: "general_cctst_fallback_v2",
        title: "团队协作任务调整",
        background: "背景",
        estimated_minutes: 30,
        version: "v1",
        source_type: "seeded_fallback",
      },
      current_stage: null,
      turns: [],
      language_mode: "standard",
    });
  });

  it("requires profile fields and versioned consent before creating", async () => {
    const { router, wrapper } = await mountView();
    const submit = wrapper.get('button[type="submit"]');
    expect(submit.attributes("disabled")).toBeDefined();

    await wrapper.get('input[autocomplete="nickname"]').setValue("小林");
    await wrapper.get("select").setValue("教育培训");
    await wrapper.get('input[placeholder*="高中教师"]').setValue("教");
    expect(submit.attributes("disabled")).toBeDefined();

    await wrapper.get('input[placeholder*="高中教师"]').setValue("高中教师");
    expect(submit.attributes("disabled")).toBeDefined();
    await wrapper.get('.consent-checkbox input').setValue(true);
    expect(submit.attributes("disabled")).toBeUndefined();
    await wrapper.get("form").trigger("submit");
    await flushPromises();

    expect(createAssessmentSession).toHaveBeenCalledWith({
      nickname: "小林",
      occupation_category: "教育培训",
      occupation: "高中教师",
      consent_accepted: true,
      consent_version: "critical_thinking_assessment_consent_v1",
    });
    expect(router.currentRoute.value.fullPath).toBe(
      "/assessment/session/occupation-session",
    );
    expect(localStorage.getItem("assessment_session_uuid")).toBe("occupation-session");
    expect(wrapper.text()).toContain("不会进入个人报告或匿名研究导出");
    expect(wrapper.text()).toContain("罗杰斯教授是 AI 访谈角色");
    expect(wrapper.text()).toContain("不提供心理诊断或治疗");
    expect(wrapper.findAll(".assessment-field-help")).toHaveLength(0);
    expect(wrapper.find(".privacy-note").exists()).toBe(false);
  });

  it("shows a student-specific identity example after choosing the student category", async () => {
    const { wrapper } = await mountView();
    await wrapper.get("select").setValue("学生");
    expect(wrapper.get('input[placeholder*="大学生"]').attributes("placeholder")).toContain(
      "大学生",
    );
  });

  it("restores the last onboarding or assessment session", async () => {
    localStorage.setItem("assessment_session_uuid", "resume-session");
    const { router, wrapper } = await mountView();
    await wrapper.findAll("button").at(-1)!.trigger("click");
    await flushPromises();
    expect(router.currentRoute.value.fullPath).toBe(
      "/assessment/session/resume-session",
    );
  });
});
