import { mount } from "@vue/test-utils";
import { createPinia } from "pinia";
import { createMemoryHistory, createRouter } from "vue-router";
import { describe, expect, it } from "vitest";
import LoginView from "./LoginView.vue";

describe("LoginView", () => {
  it("does not expose default administrator credentials", async () => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: "/admin/login", component: LoginView },
        { path: "/admin/dashboard", component: { template: "<div />" } },
      ],
    });
    await router.push("/admin/login");
    await router.isReady();

    const wrapper = mount(LoginView, {
      global: {
        plugins: [createPinia(), router],
      },
    });
    const inputs = wrapper.findAll("input");

    expect(inputs).toHaveLength(2);
    expect((inputs[0].element as HTMLInputElement).value).toBe("");
    expect((inputs[1].element as HTMLInputElement).value).toBe("");
  });
});
