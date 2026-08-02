# 全栈 C 前端报告展示与测评体验设计方案

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` or `superpowers:subagent-driven-development` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
> 
> 本文档基于已完成的 DEV-C 后端（`ScoringAgent` / `ReportAgent` / 落库服务）设计对应的前端报告展示页，并对测评对话页做必要的状态增强，确保一次 completed session 能在浏览器端呈现专业、可解释、移动友好的报告。

## 1. 目标与范围

### 1.1 目标

- 报告页从「空状态 / 原始 JSON」升级为结构化报告展示；
- 六维评分、证据引用、优势、改进建议、发展计划、免责声明按后端 `ReportOutput` schema 渲染；
- 测评对话页补充阶段上下文、动态信息高亮、进度感知，减少用户迷失；
- 所有改动复用现有沉浸式 CSS 主题，不引入新的运行时依赖；
- 报告页支持桌面端与移动端自适应。

### 1.2 范围

**必做（P0）：**

1. 强类型化 `ReportOutput` 前端类型；
2. 重构 `AssessmentReportView.vue`；
3. 新增报告专用展示组件；
4. 报告加载 / 生成中 / 报告缺失状态处理；
5. 测评对话页增强：阶段上下文、动态信息标签、进度条。

**可选（P1）：**

1. 报告页打印 / PDF 导出友好样式；
2. 对话页在 debug 模式下显示 AgentTrace 关键字段；
3. 报告页分享链接复制。

**不做：**

1. 不修改后端 Agent、Service、Model、Migration；
2. 不新增图表库；
3. 不做用户账号体系；
4. 不做报告实时生成触发接口（若需要，归属全栈 A）。

## 2. 约束条件

- 技术栈：Vue 3 Composition API、TypeScript、Vite、Pinia、Axios、纯 CSS。
- 样式：复用 `frontend/src/assets/main.css` 中已有的 `--immersive-*` / `--workspace-*` 变量与组件类名。
- 数据：只能消费现有 API（`GET /sessions/{uuid}/report`），返回结构即 `ReportOutput` JSON。
- 报告生成触发：当前 `POST /sessions/{uuid}/finish` 不会自动调用 ReportAgent。前端需处理「报告尚未生成」状态；若团队决定由前端触发，必须先由全栈 A 暴露触发端点。
- 合规：报告展示不得强化临床诊断感；免责声明必须可见。

## 3. 前端现状

```text
frontend/src/views/AssessmentStartView.vue    # 入口页，已实现
frontend/src/views/AssessmentSessionView.vue  # 对话页，已实现基础流式交互
frontend/src/views/AssessmentReportView.vue   # 报告页，当前仅展示空状态或 JSON 原文
frontend/src/components/assessment/           # 对话相关组件
frontend/src/types/session.ts                 # 类型定义，report 字段为 Record<string, unknown>
frontend/src/api/session.ts                   # API 封装，已有 getAssessmentReport
frontend/src/router/index.ts                  # 已有 /assessment/report/:sessionUuid
```

当前问题：

1. `AssessmentReportResponse.report` 是 `Record<string, unknown>`，模板无法安全访问；
2. 报告页把 `report.report_json` 直接 `JSON.stringify`，不可读；
3. 没有「报告生成中」的等待和重试 UI；
4. 对话页未显示阶段目标，动态信息与普通 AI 提问视觉区分不足。

## 4. 数据契约

后端 `ReportOutput` schema（来自 `backend/app/agents/schemas.py`）映射到前端类型：

```typescript
// frontend/src/types/report.ts
export interface DimensionReport {
  dimension_key: string;
  dimension_name: string;
  score: number;
  level_label: string;
  strength: string;
  weakness: string | null;
  evidence_quotes: string[];
  suggestion: string;
}

export interface ReportOutput {
  status: "ok";
  agent_name: "report";
  summary: string;
  overall_level: string;
  dimension_reports: DimensionReport[];
  advantages: string[];
  improvement_suggestions: string[];
  development_plan: string[];
  disclaimer: string;
  fallback_used: boolean;
  warnings: string[];
}

export interface AssessmentReportResponse {
  session_uuid: string;
  status: string;
  report: ReportOutput;
}
```

评分相关类型（用于未来扩展，若报告页需要展示评分明细）：

```typescript
// frontend/src/types/scoring.ts
export interface EvidenceItem {
  text: string;
  evidence_type: "supporting_evidence" | "weak_evidence" | "invalid_evidence";
  explanation: string | null;
  dialogue_turn_id: number | null;
}

export interface DimensionScore {
  dimension_key: string;
  score: number;
  confidence: number | null;
  reason: string;
  evidence: EvidenceItem[];
  scoring_source: string;
}
```

## 5. 组件设计

所有新组件放在 `frontend/src/components/report/`。

| 组件 | 职责 | 输入 Props |
|---|---|---|
| `ReportHero.vue` | 页面标题、会话摘要、整体等级 | `nickname`, `scenarioTitle`, `overallLevel`, `summary`, `warnings` |
| `ScoreOverview.vue` | 六维概览：条形图 + 均分 | `dimensionReports: DimensionReport[]` |
| `DimensionReportCard.vue` | 单个维度详情：分数、等级、优势、不足、证据引用、建议 | `report: DimensionReport`, `index: number` |
| `EvidenceQuote.vue` | 证据引用块：引用原文 + 类型标签 | `quote: string`, `evidenceType?: string` |
| `AdvantageList.vue` | 优势列表 | `advantages: string[]` |
| `ImprovementPlan.vue` | 改进建议 + 发展计划 | `suggestions: string[]`, `plan: string[]` |
| `ReportDisclaimer.vue` | 免责声明块 | `disclaimer: string` |
| `ReportSkeleton.vue` | 报告生成中占位骨架 | — |
| `EmptyReportState.vue` | 报告缺失 / 生成失败空状态 | `error?: string`, `onRetry?: () => void` |

通用原则：

- Props 用 `interface` 声明；
- 展示组件不直接调用 API；
- 引用用户原话时使用 `<blockquote>` 语义化标签；
- 分数用 1-5 分制，条形图宽度 `score / 5 * 100%`；
- 低分（1-2）用 `--workspace-clay` / `--red` 暗示，中分（3）用 `--workspace-amber`，高分（4-5）用 `--workspace-green`。

## 6. 页面设计

### 6.1 报告页 `AssessmentReportView.vue`

布局（桌面）：

```text
+---------------------------------------------------+
| ReportHero                                        |
|  测评报告 · 昵称 · 情境 · 整体等级 · 摘要          |
+---------------------------------------------------+
| ScoreOverview          | SessionSummary           |
|  六维条形图             |  轮次 / 状态 / 返回对话   |
+---------------------------------------------------+
| DimensionReportCard x 6                           |
|  维度名 / 分数 / 等级 / 优势 / 不足 / 证据 / 建议  |
+---------------------------------------------------+
| AdvantageList                                     |
+---------------------------------------------------+
| ImprovementPlan                                   |
+---------------------------------------------------+
| ReportDisclaimer                                  |
+---------------------------------------------------+
```

移动端：单列堆叠。

状态流转：

1. `loading`：显示 `ReportSkeleton`；
2. `reportMissing`（404）：显示「报告尚未生成」+ 倒计时自动重试 3 次；
3. `error`（非 404）：显示 `EmptyReportState`；
4. `reportReady`：渲染完整报告。

自动重试策略：

- 间隔：1s、2s、4s，最多 3 次；
- 每次重试仍 404 则进入「报告尚未生成」手动重试状态；
- 说明文案：「报告正在生成中，请稍候。如果长时间未生成，请联系管理员确认后端服务。」

### 6.2 测评对话页 `AssessmentSessionView.vue` 增强

最小改动：

1. 在 `transcript` 上方固定显示当前阶段目标卡片（`stage-context` 样式已存在）；
2. `DialogueTurn.vue` 对 `content_type === "dynamic_info_question"` 显示「新信息」标签；
3. 已存在的 `AssessmentMap.vue` 进度条复用，移动端用 `mobile-stage-strip` 显示当前阶段；
4. 结束测评后跳转报告页时携带 `?fresh=1`，报告页首次进入强制刷新一次。

## 7. API 与状态管理

### 7.1 类型更新

修改 `frontend/src/types/session.ts`：

- 新增 `ReportOutput` 导入或内联类型；
- 将 `AssessmentReportResponse.report` 从 `Record<string, unknown>` 改为 `ReportOutput`。

### 7.2 API 层

`frontend/src/api/session.ts` 无需新增接口，`getAssessmentReport` 返回类型改为强类型即可。

若后续全栈 A 暴露「触发报告生成」端点（例如 `POST /sessions/{uuid}/report`），可新增：

```typescript
export async function triggerAssessmentReport(sessionUuid: string) {
  const response = await api.post(`/sessions/${sessionUuid}/report`);
  return response.data;
}
```

**在本方案中，该接口为预留，不实现。**

### 7.3 Pinia 状态

报告页状态较简单，不新增 Pinia store，在视图组件内用 `ref` / `computed` 管理。

## 8. 实现任务

### Task 1: 类型与 API 层准备

**Files:**
- Create: `frontend/src/types/report.ts`
- Create: `frontend/src/types/scoring.ts`
- Modify: `frontend/src/types/session.ts`
- Modify: `frontend/src/api/session.ts`

- [ ] **Step 1: 创建报告类型**

按第 4 节契约，创建 `frontend/src/types/report.ts`。

- [ ] **Step 2: 创建评分类型**

创建 `frontend/src/types/scoring.ts`（当前报告页主要消费 `report.ts`，`scoring.ts` 为后续扩展）。

- [ ] **Step 3: 更新 session 类型**

将 `AssessmentReportResponse.report` 改为 `ReportOutput`。

- [ ] **Step 4: 更新 API 返回类型**

`getAssessmentReport` 返回类型改为 `AssessmentReportResponse`。

### Task 2: 报告展示组件

**Files:**
- Create: `frontend/src/components/report/ReportHero.vue`
- Create: `frontend/src/components/report/ScoreOverview.vue`
- Create: `frontend/src/components/report/DimensionReportCard.vue`
- Create: `frontend/src/components/report/EvidenceQuote.vue`
- Create: `frontend/src/components/report/AdvantageList.vue`
- Create: `frontend/src/components/report/ImprovementPlan.vue`
- Create: `frontend/src/components/report/ReportDisclaimer.vue`
- Create: `frontend/src/components/report/ReportSkeleton.vue`
- Create: `frontend/src/components/report/EmptyReportState.vue`

- [ ] **Step 1: ReportHero**

显示 `AI 心理测评师 · 测评报告`、昵称、情境标题、整体等级徽章、摘要。

- [ ] **Step 2: ScoreOverview**

六维条形图：每个维度一行，左侧维度名，中间分数数字，右侧进度条。

- [ ] **Step 3: DimensionReportCard**

卡片头部：序号、维度名、分数徽章、等级标签。
卡片主体：strength / weakness / evidence_quotes / suggestion。

- [ ] **Step 4: EvidenceQuote**

引用样式，附带证据类型标签（有效 / 弱 / 无效）。

- [ ] **Step 5: AdvantageList / ImprovementPlan / ReportDisclaimer**

按 `ReportOutput` 字段渲染列表文本。

- [ ] **Step 6: ReportSkeleton / EmptyReportState**

骨架屏使用 CSS 渐变脉冲动画；空状态提供手动重试按钮。

### Task 3: 重构报告页

**Files:**
- Modify: `frontend/src/views/AssessmentReportView.vue`

- [ ] **Step 1: 导入新类型与组件**

- [ ] **Step 2: 实现自动重试逻辑**

`loadReport` 遇到 404 时进入自动重试队列，最多 3 次；其他错误直接展示。

- [ ] **Step 3: 按组件拼装页面**

依次渲染 ReportHero → ScoreOverview → DimensionReportCards → AdvantageList → ImprovementPlan → ReportDisclaimer。

- [ ] **Step 4: 移除 JSON 原文展示**

保留调试用折叠区域（仅在 `?debug=1` 显示）。

### Task 4: 对话页体验增强

**Files:**
- Modify: `frontend/src/views/AssessmentSessionView.vue`
- Modify: `frontend/src/components/assessment/DialogueTurn.vue`

- [ ] **Step 1: 阶段目标常驻显示**

在 `transcript` 上方渲染 `current_stage.main_question`。

- [ ] **Step 2: 动态信息标签**

`DialogueTurn.vue` 对 `dynamic_info_question` 显示「新信息」标签，并应用已有 `.turn-dynamic_info_question` 样式。

- [ ] **Step 3: 结束测评跳转参数**

`finishSession` 跳转时改为 `/assessment/report/${sessionUuid}?fresh=1`。

### Task 5: 样式补充与响应式

**Files:**
- Modify: `frontend/src/assets/main.css`

- [ ] **Step 1: 报告页专用类**

在 `/* Immersive minimal assessment skin */` 或末尾新增 `.report-section`、`.score-bar`、`.dimension-score-badge`、`.evidence-quote` 等类。

- [ ] **Step 2: 打印友好**

为报告页添加 `@media print`：隐藏按钮、背景变白、链接去下划线。

### Task 6: 验证

**Commands:**

```powershell
cd frontend
npm run build
```

- [ ] **Step 1: 类型检查通过**

`vue-tsc --noEmit` 无错误。

- [ ] **Step 2: 构建通过**

`npm run build` 成功。

- [ ] **Step 3: 人工验收**

1. 完成一次测评 → 进入报告页；
2. 若报告未生成，应看到生成中状态；
3. 报告生成后，六维评分、证据、优势、建议、免责声明均正确显示；
4. 切换桌面 / 移动端宽度，无重叠；
5. 对话页能区分动态信息与普通追问。

## 9. 验收标准

| 检查项 | 标准 |
|---|---|
| 类型安全 | `vue-tsc --noEmit` 通过，无 `any` 滥用 |
| 报告渲染 | 完整展示 summary、overall_level、6 个 dimension_reports、advantages、improvement_suggestions、development_plan、disclaimer |
| 证据引用 | 每条 evidence_quotes 来自用户原话，带引用样式 |
| 报告缺失 | 404 时展示友好提示并自动重试 3 次 |
| 移动端 | 宽度 375px ~ 1440px 均可读，无横向滚动 |
| 对话增强 | 动态信息有「新信息」标签，阶段目标可见 |
| 无新增依赖 | 不安装 chart 库、ui 库 |

## 10. 风险与依赖

| 风险 | 影响 | 缓解 |
|---|---|---|
| 后端未自动触发报告生成 | 报告页一直 404 | 前端自动重试 + 提示；由全栈 A 在 finish 流程中集成报告生成 |
| `ReportOutput` schema 调整 | 前端类型不匹配 | 与全栈 C 确认 schema；类型定义集中在一个文件 |
| 报告内容过长 | 移动端阅读体验差 | 折叠维度详情、锚点导航 |
| 证据句为空数组 | 维度卡片缺少依据 | 显示「本次对话未提供该维度有效证据」占位 |

## 11. PR 建议

标题：

```text
DEV-FE-C-001 implement report display page and assessment session polish
```

提交拆分：

```text
DEV-FE-C-001 add report and scoring typescript types
DEV-FE-C-002 add report display components
DEV-FE-C-003 refactor assessment report view
DEV-FE-C-004 polish assessment session experience
DEV-FE-C-005 add report print and responsive styles
```

PR 描述需包含：

- 改动文件清单；
- 运行 `npm run build` 的结果；
- 桌面端与移动端截图；
- 已知依赖：需要全栈 A 将报告生成接入 finish 流程，或暴露触发端点。
