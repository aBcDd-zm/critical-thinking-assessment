# Humanistic Style Policy v1 原则映射审核（草案）

文档 ID：`humanistic_style_policy_mapping_audit_v1`  
状态：`policy_frozen_v1`  
审核日期：`2026-07-28`  
输入原则：`humanistic_source_notes_v1`（`frozen_v1`，21 条）  
被审核 Policy：`humanistic_style_policy_v1.yaml`（`provisional_synthetic`）

## 1. 审核范围与判定口径

本审核只评估并记录：

1. 21 条冻结原则是否被现有 Policy 明确覆盖；
2. Policy 规则是否存在来源、测量或产品边界问题；
3. Policy 与 Renderer Prompt、运行时 Validator、测试之间是否一致；
4. 下一阶段需要补充哪些可机器检查的约束。

本阶段没有修改 `humanistic_style_policy_v1.yaml`、Prompt、Validator、测试、语料或发布配置。

覆盖等级：

- `完整`：Policy 已明确表达原则的核心允许行为与禁止边界；不代表运行时一定完全实现。
- `部分`：Policy 有相邻规则，但缺少该原则的一项关键语义或可操作表述。
- `缺失`：Policy 没有可直接承担该原则的规则。

运行时等级：

- `结构硬门禁`：由 Schema、Validator 或 Renderer 流程直接阻断并 fallback。
- `词法/正则门禁`：能拦截已列模式，但不能覆盖全部同义表达。
- `Prompt 约束`：只在模型指令中要求，无法保证机械阻断。
- `未实现`：当前没有直接检查。

## 2. 结论摘要

Policy 语义覆盖结果：

| 覆盖等级 | 数量 | principle_id |
|---|---:|---|
| 完整 | 12 | HSP-02、03、06、10、11、12、13、14、16、17、20、21 |
| 部分 | 8 | HSP-01、04、05、07、09、15、18、19 |
| 缺失 | 1 | HSP-08 |

总体结论：

- 没有发现现有 Policy 与冻结原则直接相反的规则。
- 核心安全边界已经较完整：隐藏意义、虚假亲密、角色替代、AI 自我披露、权威建议和临床角色均有硬错误码。
- 最大缺口不在理念方向，而在可追溯性和执行一致性：Policy 没有引用任何 `HSP-*`，运行时代码也不读取该 YAML。
- `HSP-08`（容纳矛盾、犹豫和不确定性）没有对应 Policy 规则。
- 评价性夸奖、理解过度确定、诱使附和、纠正式教学等测量风险只被部分覆盖。
- 现有 `claims_allowed` 中“表达更自然”尚未经过盲评验证，应改为设计目标或使用非效果性措辞。
- `clinical_boundary.label` 当前复用 `role_substitution`，使临床越界与关系角色替代在标签层混为一类；虽然已有不同 hard error code，但审计语义不够清晰。

因此，现有 Policy 暂不具备冻结条件。

## 3. 21 条原则逐项映射

| principle_id | Policy 覆盖 | 当前 Policy 映射 | 当前运行时执行 | 主要缺口/判断 |
|---|---|---|---|---|
| HSP-01 | 部分 | `preserve.neutral_acknowledgement`、`single_focus`；`renderer_contract` | Prompt 要求自然承接；句数、长度和问题数有结构门禁 | 未明确禁止“共情模板堆叠、冗长风格表演盖过测量焦点” |
| HSP-02 | 完整 | `degrade.congruence`、`first_person_policy.allowed` | Prompt 要求承认边界；部分虚假自述有正则门禁 | Policy 完整；运行时尚不能普遍识别虚假记忆、虚假读取或虚假能力声明 |
| HSP-03 | 完整 | `prohibit.self_disclosure`、`first_person_policy.prohibited`、`fabricated_self_disclosure` | 词法/正则硬门禁，命中即 fallback | 无原则级缺口；需补 `source_principles` 引用 |
| HSP-04 | 部分 | `traceable_tentative_reflection`、`explicit_repair`、程序性第一人称 | 反映依据有结构门禁；隐藏意义有正则门禁 | 未明确规定“依据不足时必须澄清，不得以确定语气补全理解” |
| HSP-05 | 部分 | `neutral_acknowledgement`、`degrade.positive_regard` | Prompt 禁止评价性表扬；`JUDGMENTAL_TERMS` 词表门禁 | 未明确规定尊重语气不得随回答强弱、立场或可评分性变化 |
| HSP-06 | 完整 | `understanding_not_endorsement`、`authority_advice` | 权威建议和部分背书表达有正则硬门禁 | 无原则级缺口；“赞同/支持”的同义变体仍需扩充测试 |
| HSP-07 | 部分 | `degrade.positive_regard` 中“不贴标签、不评判” | `judgmental`、`unsupported_inference` 词表门禁 | 未明确写出“评价具体证据/回答，不把评价扩展为对整个人的判断” |
| HSP-08 | 缺失 | 无直接对应项 | 未实现 | 需要新增 `ambivalence_tolerance`：允许矛盾、犹豫和不确定性存在，同时按单一焦点澄清，不强迫即时一致 |
| HSP-09 | 部分 | 仅由 `positive_regard` 间接支持 | Prompt 明确禁评价性表扬；`JUDGMENTAL_TERMS` 拦截少量常见词 | Policy 应显式新增 `no_evaluative_praise`，并扩大同义表达测试 |
| HSP-10 | 完整 | `attachment`、`role_substitution`、第一人称禁区 | 两类正则硬门禁 | 无原则级缺口；需保持普通工作关系词不误杀 |
| HSP-11 | 完整 | `degrade.internal_frame_of_reference` | Payload 提供指定用户 turn；Prompt 要求从用户表达承接 | 语义主要依赖 Prompt，难以机械判断是否真正采用用户视角 |
| HSP-12 | 完整 | `hidden_meaning`、`single_focus`、`maximum_primary_questions` | 隐藏意义、leading、问题数有门禁 | 盘问语气无法完全机械识别，但核心边界已覆盖 |
| HSP-13 | 完整 | `traceable_tentative_reflection`、`degrade.empathy` | 反映可追溯，但“我完全理解/我感受到你的……”未形成稳定独立错误码 | Policy 语义完整；运行时需补理解过度确定的测试与门禁 |
| HSP-14 | 完整 | `traceable_tentative_reflection`、`reflection_requires_turn_basis` | turn ID、逐字依据、缺失反映均有结构硬门禁 | 无原则级缺口；需把现有 validation code 列入 Policy 契约 |
| HSP-15 | 部分 | `hidden_meaning`、事实白名单边界 | `unsupported_inference` 与隐藏意义正则门禁 | 未明确“先回应显性事实/理由，再引入中性概念；不得心理化普通工作判断” |
| HSP-16 | 完整 | `authority_advice`、`prescriptive_authority` | 正则硬门禁 | 无原则级缺口 |
| HSP-17 | 完整 | `preserve.autonomy`、`degrade.self_direction` | 权威代答有门禁 | 隐蔽的“先替用户生成关键理由、再让用户确认”仍需样例测试 |
| HSP-18 | 部分 | `autonomy`；release gate 要求 `non_leading_100_percent` | `LEADING_PATTERNS` 只覆盖少量显式句式 | 缺少 `no_agreement_pressure`：不得寻求附和、暗示唯一答案或用反问制造顺从压力 |
| HSP-19 | 部分 | `explicit_repair` | “正确”等词可触发 `judgmental`；无独立澄清/纠正判定 | 需要新增 `clarify_not_correct`，明确偏离或矛盾时先中性澄清，不教学、不纠错 |
| HSP-20 | 完整 | `single_focus`、`must_follow_frozen_plan`、单问题与 fallback 契约 | 问题数、事件事实、反映缺失和计划结构均有硬门禁 | 无原则级缺口；应补“只有陪伴语而遗漏计划问题”的固定反例 |
| HSP-21 | 完整 | `product_boundary`、`authority_advice`、`role_substitution`、`clinical_boundary` | 权威、临床角色、关系替代有正则硬门禁 | 角色边界完整；需要修正临床标签与角色替代标签混用 |

## 4. Policy 中非来源原则规则的处置

下列内容不是从 HSP 原则直接推出，但并非“无依据规则”，而是项目必须保留的工程、测量或来源治理约束：

| Policy 区域 | 规则来源类型 | 审核判断 |
|---|---|---|
| `provenance_levels` | 来源治理 | 保留；来自 `source_audit_v1`，不应伪装成 HSP 原则 |
| `source_contamination_blocks` | 来源污染审计 | 保留；来自 `source_ledger_v1` 的排除证据 |
| `renderer_contract.maximum_primary_questions` | 测量合同 | 保留；不是人本主义理论命题 |
| `may_change_action/stage/target_dimension` | 架构与测量合同 | 保留；用于保证 Renderer 不改变 Planner |
| `model_attempts`、`model_timeout_seconds`、fallback | 工程可靠性 | 保留；应标注 `rule_origin: engineering_constraint` |
| `release_gate` | 研究验收合同 | 保留；应标注 `rule_origin: release_evidence` |

建议在下一版 Policy 中为规则增加 `rule_origin` 或分区元数据，区分：

- `frozen_source_principle`
- `measurement_contract`
- `engineering_constraint`
- `source_governance`
- `release_evidence`

这样可以避免把三秒超时、单问题或发布门禁错误地描述成人本主义理论结论。

## 5. Policy 与运行时代码的一致性审核

### 5.1 YAML 当前不是运行时唯一规则源

仓库搜索结果显示，`humanistic_style_policy_v1.yaml` 当前由语料资产测试读取，但 `InterviewerAgent` 和 `InterviewQuestionValidator` 不在运行时加载该文件。

实际执行链为：

```text
InterviewerAgent 内嵌 Prompt
  -> InterviewQuestionValidator 常量、词表和正则
  -> validation error codes
  -> deterministic fallback
```

这意味着：

- 只修改 YAML 不会自动改变运行时行为；
- YAML、Prompt、Validator 很容易发生版本漂移；
- 当前测试主要检查固定 label/code 集合，没有检查 21 条原则的完整映射；
- Policy 冻结前必须明确它是“规范源”还是“说明性副本”。

本审核建议 v1 采用低风险方案：

1. YAML 作为规范性 Policy；
2. Prompt 与 Validator 保持显式实现；
3. 增加 parity tests，验证 Policy principle ID、规则 ID、错误码与代码常量一致；
4. 暂不让生产运行时动态解析文档 YAML，以免在本 PR 扩大配置加载风险。

### 5.2 错误码契约不完整

Policy 当前只列出 6 个 `hard_error_codes`，但 Validator 还会产生：

- `internal_terms`
- `judgmental`
- `leading`
- `unsupported_inference`
- `question_count`
- `unreleased_fact` / `unexpected_fact`
- `ungrounded_reflection`
- `reflection_quote_ids`
- `missing_reflection`
- `quality_flags`
- `missing_selected_fact`
- `duplicate_question` / `semantic_duplicate_question`
- `too_long`
- `too_many_sentences`

这些错误并非全部属于“人本主义硬安全错误”，但它们属于 Renderer 的测量与结构验证契约。Policy 应新增独立的 `validation_error_codes` 或引用清单，不能让文档看起来只有 6 种可能失败原因。

### 5.3 临床标签混用

当前：

```yaml
clinical_boundary:
  label: "role_substitution"

clinical_role_claim:
  negative_label: "role_substitution"
```

这能保持现有五类负例集合，但会把“宣称心理治疗/疗效”与“扮演父母、朋友或伴侣”压进同一标签。下一阶段需要二选一：

- 新增独立 `clinical_boundary` negative label，并同步更新语料、测试和评估契约；或
- 保持五类 blind-review 标签不变，但给 `clinical_role_claim` 增加独立 `boundary_type: clinical_boundary`，避免审计时丢失含义。

推荐第二种，改动更小且不破坏当前盲评标签集合。

## 6. 冻结前必须完成的 Policy 修订清单

### P0：阻断 Policy 冻结

1. 增加冻结来源元数据：`source_notes_id`、`source_notes_status` 和 21 个 approved principle ID。
2. 为 `preserve`、`degrade`、`prohibit`、`first_person_policy`、`renderer_contract` 的相关规则增加 `source_principles` 或 `rule_origin`。
3. 新增 `ambivalence_tolerance`，覆盖 HSP-08。
4. 将“表达更自然”从未经验证的允许效果声明改为 `design_objective`，或明确“待盲评验证”。
5. 明确 YAML 是规范源，并用 parity test 防止 Prompt/Validator 漂移。
6. 在 Policy 中列出非安全类 `validation_error_codes`，与 Validator 当前输出对齐。

### P1：必须在候选生成前补齐

1. `no_evaluative_praise`：覆盖 HSP-05、HSP-09。
2. `epistemic_humility`：覆盖 HSP-04、HSP-13。
3. `person_behavior_separation`：覆盖 HSP-07。
4. `explicit_before_abstraction`：覆盖 HSP-15。
5. `no_agreement_pressure`：覆盖 HSP-18。
6. `clarify_not_correct`：覆盖 HSP-19。
7. 为上述规则补正例、反例和 Validator 测试，确认不会误杀用户原话引用。

## 7. 建议的机器检查项

| 新增检查 | 建议代码 | 建议级别 | 目的 |
|---|---|---|---|
| 评价性奖励或夸奖 | `evaluative_praise` | block | 防止泄露评分方向和强化迎合 |
| 对理解的无依据确定性 | `overclaimed_understanding` | block | 防止“完全懂你/我感受到你的内心” |
| 寻求附和或唯一答案暗示 | `agreement_pressure` | block | 支持 non-leading 100% 门禁 |
| 纠正式教学 | `corrective_instruction` | block | 防止把测评变成训练 |
| 用户矛盾被强迫消除 | `forced_resolution` | review/block | 保留不确定性和权衡证据 |
| 仅安慰而遗漏计划问题 | `plan_question_omission` | block | 保证非指导不等于不执行计划 |

这些代码应与现有六个临床/关系安全 hard code 分区，避免把测量错误与临床安全错误混为同一统计口径。

## 8. 成员 A / PSY 审核记录（待填写）

请对以下修订包分别选择 `approve`、`revise` 或 `reject`：

| package_id | 内容 | 决定 | 修改意见 | 审核人 | 日期 |
|---|---|---|---|---|---|
| MAP-P0-A | 增加 21 条原则的来源元数据与逐规则映射 | approve | 无修改 | 成员 A / PSY（孙然之） | 2026-07-28 |
| MAP-P0-B | 新增 HSP-08 `ambivalence_tolerance` | approve | 无修改 | 成员 A / PSY（孙然之） | 2026-07-28 |
| MAP-P0-C | 将“更自然”降为待验证设计目标 | approve | 无修改 | 成员 A / PSY（孙然之） | 2026-07-28 |
| MAP-P0-D | 增加 YAML/Prompt/Validator parity tests | approve | 无修改 | 成员 A / PSY（孙然之） | 2026-07-28 |
| MAP-P0-E | 补齐非安全类 validation error code 契约 | approve | 无修改 | 成员 A / PSY（孙然之） | 2026-07-28 |
| MAP-P1-A | 增加评价性夸奖、理解过度确定、人与表现分离规则 | approve | 无修改 | 成员 A / PSY（孙然之） | 2026-07-28 |
| MAP-P1-B | 增加显性内容优先、禁止附和压力、澄清不纠正规则 | approve | 无修改 | 成员 A / PSY（孙然之） | 2026-07-28 |
| MAP-P1-C | 临床越界保留现有 blind label，增加独立 boundary type | approve | 无修改 | 成员 A / PSY（孙然之） | 2026-07-28 |
| MAP-P1-D | 为新增规则补正反例与防误杀测试 | approve | 无修改 | 成员 A / PSY（孙然之） | 2026-07-28 |

## 9. 当前决定

```yaml
mapping_audit_status: policy_frozen_v1
policy_freeze_ready: true
policy_modified_in_this_step: false
policy_modified_in_2b: true
policy_status: frozen_v1
source_notes_status: frozen_v1
source_principle_count: 21
approved_by: "成员 A / PSY（孙然之）"
approved_at: "2026-07-28"
```

## 10. 第 2B 实现记录

实现日期：`2026-07-28`

已完成：

- Policy schema 更新为 `1.1`，并增加 21 条冻结原则的逐规则映射；
- 补齐 `ambivalence_tolerance`、`no_evaluative_praise`、`epistemic_humility`、`person_behavior_separation`、`explicit_before_abstraction`、`no_agreement_pressure`、`clarify_not_correct` 和 `active_facilitation`；
- 将自然表达保留为 `pending_blind_review` 的设计目标；
- Renderer Prompt 加入批准后的必要标记，并由 parity test 检查；
- Validator 增加 `evaluative_praise`、`overclaimed_understanding`、`agreement_pressure`、`corrective_instruction`、`forced_resolution` 和 `plan_question_omission`；
- 新增 Policy/Validator error-code parity test、21 条原则映射完整性测试、Prompt marker parity test、正反例和用户原话防误杀测试；
- 新规则只在 humanistic 安全模式启用；原有 baseline 行为不新增这些语言模式错误码；
- 现有五类 blind-review label 和六个 hard error code 保持不变。

验证证据：

```text
python -m unittest discover -s tests -p 'test_humanistic_interviewer_*.py' -v
Ran 35 tests - OK

python -m unittest discover -s tests -v
Ran 48 tests - OK
```

说明：本地虚拟环境未安装 `pytest`，因此使用标准库 `unittest` 运行同一测试模块；未为此安装或变更依赖。

## 11. Policy 冻结记录

```yaml
policy_id: humanistic_style_policy_v1
status: frozen_v1
frozen_by: "成员 A / PSY（孙然之）"
frozen_at: "2026-07-28"
approved_principle_count: 21
backend_unittest_result: "48_passed"
git_commit: "pending_first_commit"
release_status: "not_released_pending_blind_review_and_uat"
```

本次冻结只表示来源原则、语言规则、Prompt 标记、Validator 契约和一致性测试已形成不可随意改动的 v1 规范。它不表示离线盲评、运行时指标、真人 UAT 或生产发布门禁已经通过。
