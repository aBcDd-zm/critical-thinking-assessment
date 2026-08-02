import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";
import EvidenceQuote from "./EvidenceQuote.vue";

describe("EvidenceQuote", () => {
  it("collapses long evidence and preserves the complete original text", async () => {
    const quote = "这是一条完整的用户原话。".repeat(30);
    const wrapper = mount(EvidenceQuote, { props: { quote } });
    const paragraph = wrapper.get("p");

    expect(paragraph.classes()).toContain("is-collapsed");
    expect(paragraph.text()).toContain(quote);
    await wrapper.get(".evidence-toggle").trigger("click");
    expect(paragraph.classes()).not.toContain("is-collapsed");
    expect(wrapper.get(".evidence-toggle").text()).toBe("收起");
  });
});
