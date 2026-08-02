# Agent 测试版 seed 映射说明 v1

本文档说明全栈 A 已完成的 Agent 测试版配置如何从心理测评设计落到后端 seed、数据库表和 Agent 开发输入中。它用于支持全栈 B、全栈 C 和心理组对齐开发边界。

## 1. 当前交付状态

当前版本为 `agent_test_v1`，目标是先给 Agent 开发提供稳定输入，不代表心理组最终专业内容已经定稿。

已完成内容：

| 配置项 | 当前数量 | 文件 |
| --- | ---: | --- |
| 六维能力模型 | 6 个维度 | `backend/seeds/rubric.yaml` |
| 评分锚点 | 每维 1/3/5 分锚点 | `backend/seeds/rubric.yaml` |
| 完整测评情境 | 1 个 | `backend/seeds/scenario_product_48h.yaml` |
| 情境阶段 | 6 个阶段 | `backend/seeds/scenario_product_48h.yaml` |
| 动态信息 | 6 条 | `backend/seeds/scenario_product_48h.yaml` |
| 追问策略 | 12 条 | `backend/seeds/scenario_product_48h.yaml` |
| Prompt 模板 | host/followup/scoring/report | `backend/seeds/prompts.yaml` |
| 报告模板 | 1 套 | `backend/seeds/report_template.yaml` |

## 2. 六维能力模型如何落库

`backend/seeds/rubric.yaml` 对应心理测评机制中的六个审辩式思维能力维度：

| 能力维度 | dimension_key | 数据库表 |
| --- | --- | --- |
| 问题界定 | `problem_definition` | `rubric_dimension`、`rubric_anchor` |
| 证据评估 | `evidence_evaluation` | `rubric_dimension`、`rubric_anchor` |
| 推理论证 | `reasoning_argumentation` | `rubric_dimension`、`rubric_anchor` |
| 多元视角 | `multiple_perspectives` | `rubric_dimension`、`rubric_anchor` |
| 整合决策 | `integrative_decision` | `rubric_dimension`、`rubric_anchor` |
| 动态调整 | `dynamic_adjustment` | `rubric_dimension`、`rubric_anchor` |

每个维度至少包含：

1. 维度定义；
2. 可观察行为；
3. 无效证据说明；
4. 1/3/5 分行为锚点；
5. 典型证据和反例。

全栈 C 的 ScoringAgent 应读取这些配置，输出 `ScoringOutput`，不要在 Agent 代码里硬编码评分维度和分数解释。

## 3. 情境流程如何落库

`backend/seeds/scenario_product_48h.yaml` 对应一个完整管理决策测评任务：`产品上线前 48 小时`。

| 阶段 | 阶段编码 | 主测目标 | 主要数据库表 |
| --- | --- | --- | --- |
| 初始问题界定 | `s1_problem_definition` | 问题界定 | `scenario_stage`、`scenario_stage_dimension` |
| 证据核实 | `s2_evidence_verification` | 证据评估 | `scenario_stage`、`stage_dynamic_info`、`stage_intervention_rule` |
| 多方视角权衡 | `s3_stakeholder_perspectives` | 多元视角 | `scenario_stage`、`stage_dynamic_info`、`stage_intervention_rule` |
| 推理论证与初步决策 | `s4_reasoning_decision` | 推理论证 | `scenario_stage`、`stage_dynamic_info`、`stage_intervention_rule` |
| 新信息下的动态调整 | `s5_dynamic_adjustment` | 动态调整 | `scenario_stage`、`stage_dynamic_info`、`stage_intervention_rule` |
| 最终方案整合 | `s6_integrated_plan` | 整合决策 | `scenario_stage`、`stage_dynamic_info`、`stage_intervention_rule` |

全栈 B 的 HostAgent / FollowupAgent 应读取阶段、动态信息和追问策略，输出 `HostOutput` 或 `FollowupOutput`，不要直接写数据库。

## 4. AI 与配置比重如何体现

测试版 seed 已保留人工可调字段：

| 字段 | 含义 | 使用方 |
| --- | --- | --- |
| `context_generation_mode` | 阶段情境生成方式 | HostAgent |
| `context_ai_weight` | 阶段情境中 AI 自由发挥比例 | HostAgent |
| `context_generation_constraints_json` | 情境生成必须遵守的事实边界 | HostAgent |
| `question_generation_mode` | 追问生成方式 | FollowupAgent |
| `question_ai_weight` | 追问中 AI 自由发挥比例 | FollowupAgent |
| `question_generation_constraints_json` | 追问生成边界 | FollowupAgent |
| `fallback_question` | 模型失败或输出质量不达标时的兜底问题 | FollowupAgent |

这意味着第一版就支持人工调整“配置主导”与“AI 主导”的比例。后台后续可直接编辑这些字段。

## 5. B/C 开发使用方式

全栈 B 主要读取：

1. `scenario_stage`
2. `stage_dynamic_info`
3. `stage_intervention_rule`
4. `scenario_stage_dimension`
5. `backend/app/agents/schemas.py` 中的 `HostOutput`、`FollowupOutput`

全栈 C 主要读取：

1. `rubric_dimension`
2. `rubric_anchor`
3. `report_template`
4. 对话历史 `dialogue_turn`
5. `backend/app/agents/schemas.py` 中的 `ScoringOutput`、`ReportOutput`

全栈 A 后续负责把 B/C 输出落库到：

1. `agent_trace`
2. `dialogue_turn`
3. `score_snapshot`
4. `score_result`
5. `score_evidence`
6. `assessment_report`

## 6. 验收命令

在 `backend` 目录执行：

```bash
python scripts/seed_db.py --dry-run
python scripts/check_agent_contract.py
python -m compileall app scripts
```

通过标准：

1. seed 文件均可解析；
2. `rubric.yaml` 至少包含 6 个维度，且每维有 1/3/5 分锚点；
3. `scenario_product_48h.yaml` 至少包含 6 个阶段、4 条动态信息、8 条追问策略；
4. 所有追问策略都有 `fallback_question`；
5. Host / Followup / Scoring / Report 四类输出样例均能通过 Pydantic 校验。
