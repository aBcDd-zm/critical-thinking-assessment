import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import sessionView from "./AssessmentSessionView.vue?raw";
import indexHtml from "../../index.html?raw";

const styles = readFileSync(resolve(process.cwd(), "src/assets/main.css"), "utf8");

describe("AssessmentSessionView mobile layout contract", () => {
  it("uses the dynamic viewport and phone safe areas", () => {
    expect(indexHtml).toContain("viewport-fit=cover");
    expect(styles).toContain("height: 100dvh");

    const phoneRules = styles.slice(
      styles.indexOf("/* Phone / WeChat viewport"),
    );
    expect(phoneRules).toContain("@media (max-width: 480px)");
    expect(phoneRules).toContain("env(safe-area-inset-top, 0px)");
    expect(phoneRules).toContain("env(safe-area-inset-bottom, 0px)");
    expect(phoneRules).toContain("grid-template-columns: minmax(48px, 1fr) auto auto auto");
  });

  it("starts short conversations at the top and keeps the composer within the viewport", () => {
    const phoneRules = styles.slice(
      styles.indexOf("/* Phone / WeChat viewport"),
    );
    expect(phoneRules).toMatch(
      /\.interview-transcript\s*>\s*:first-child\s*\{\s*margin-top:\s*0;/,
    );
    expect(styles).toMatch(/\.interview-status-row\s*\{[^}]*grid-row:\s*1;/s);
    expect(styles).toMatch(/\.interview-transcript\s*\{[^}]*grid-row:\s*2;/s);
    expect(phoneRules).toContain(
      "grid-template-columns: 38px minmax(0, 1fr) 54px",
    );
    expect(phoneRules).toContain("max-height: min(104px, 24dvh)");
    expect(phoneRules).toContain("overflow-wrap: anywhere");
  });

  it("keeps one voice toggle and the Rogers identity", () => {
    expect(sessionView.match(/class="interview-read-button"/g)).toHaveLength(1);
    expect(sessionView).toContain('class="interview-logo"');
    expect(sessionView).toContain(':src="interviewMarkUrl"');
    expect(sessionView).not.toContain('<span class="interview-logo">罗</span>');
    expect(sessionView).toContain("罗杰斯教授");
    expect(sessionView).not.toContain("AI 访谈员");
    expect(sessionView).not.toContain("语音转文字已准备好");
  });
});
