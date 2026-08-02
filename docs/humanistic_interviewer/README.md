# Humanistic Interviewer Style v1 研究资产

本目录保存“受人本主义沟通原则启发的测评访谈表达”首期研究资产。它服务于工作情境中的审辩式思维测评，不构成心理咨询、心理治疗或罗杰斯人格模拟。

## 文件

- `source_audit_v1.md`：来源等级、文章纠错和采用边界。
- `source_ledger_v1.jsonl`：逐条记录出处、页码/turn、短摘、复核译文、状态、风险与产品处置。
- `humanistic_source_notes_v1.md`：成员 A / PSY 审核并冻结的 21 条来源原则及项目化边界。
- `humanistic_style_policy_v1.yaml`：表达政策、硬性禁区、错误码与反例标签。
- `humanistic_style_policy_mapping_audit_v1.md`：21 条原则到 Policy、Prompt、Validator 和测试的映射及冻结记录。
- `secure_review_handoff_v1.md`：仓库外私有候选、脱敏生成回执、双盲评分模板、冻结与解盲交接。
- `offline_ab_evaluation_v1.md`：48-context 双人盲评、运行指标和发布门禁的数据契约。
- `internal_uat_template_v1.md`：10–20 次内部 UAT 的空白执行模板和 JSONL 结构。
- `acceptance_report_v1.md`：明确保持 `BLOCKED` 的待验收报告。
- `humanistic_interviewer_v1_1_implementation.md`：罗杰斯式“接—映—核—问—记”实现、证据污染防护与版本边界。
- `evaluation_config_v1_1.json`：复用冻结上下文与 v1 阈值、但拒绝旧证据的 v1.1 门禁配置。
- `acceptance_report_v1_1.md`：v1.1 独立证据缺失时保持 `BLOCKED` 的验收状态。
- `../../backend/tests/fixtures/humanistic_interviewer/pilot_contexts_development_v1.jsonl`：40 个可用于开发检查的合成工作场景上下文。
- `../../backend/tests/fixtures/humanistic_interviewer/pilot_contexts_locked_v1.jsonl`：8 个不得用于 Prompt、规则或阈值调优的 locked-test 上下文。
- `../../backend/tests/fixtures/humanistic_interviewer/pilot_context_manifest_v1.json`：数据拆分、冻结资产哈希和 `ISO-A` 至 `ISO-H` 隔离规则。
- `../../backend/tests/fixtures/humanistic_interviewer/review_examples_v1.jsonl`：30 个正例与 12 个反例候选。

私有交接工具位于 `backend/scripts/prepare_humanistic_review_handoff_v1.py`；离线门禁脚本位于 `backend/scripts/evaluate_humanistic_interviewer_v1.py`。前者生成脱敏回执、用回执哈希绑定的 arm-blind 空白评分模板，并在评分冻结后由保管人生成完整 context-keyed evaluator 私有输入包；后者只校验和计算真实验收输入。二者都不伪造人工评分、运行记录或 UAT 结论。

v1.1 使用独立入口 `backend/scripts/evaluate_humanistic_interviewer_v1_1.py`。
它只复用冻结的 48 个上下文和评分阈值，要求所有证据进入
`humanistic_v1_1` 命名空间，并由 v1.1 专属回执绑定；旧 v1 证据不能放行
v1.1。回执还必须绑定候选选择器、Renderer、Validator、Session 集成、匿名
审计导出、证据追踪器、行为片段抽取器、Planner 与 Prompt 种子的源码 SHA-256，
防止评测证据与待发布代码脱节。
成员 A 未批准 `ai_copy_exclusion_v1`、或任一盲评/运行/UAT 证据缺失时，门禁
固定返回 `BLOCKED` 和退出码 2。

JSONL 中每行都是独立 JSON 对象。所有样例均为项目新写的合成材料，不复制 Gloria 会谈、公众号文章或书籍中的长段原文。

## 当前完成度

当前资产仅完成来源审计、规则筛查和合成候选构造：

- `humanistic_source_notes_v1` 与 `humanistic_style_policy_v1` 已由成员 A / PSY 冻结为 v1；该冻结只表示研究规范固定，不表示发布门禁通过；
- 仓库当前不存在 `generation_receipt_v1.json`，所以仓库外正式候选是否已完整生成处于“未核验”状态；PR 描述、聊天或口头说明不能替代脱敏回执；
- 48 条 context 的 `status: frozen_v1` 只表示评估输入已经固定；`review_examples_v1.jsonl` 仍以 `provisional_synthetic` 表示尚未经过正式双人盲评；
- 文件不包含虚构的人类评分、评审姓名、一致性系数或偏好率；
- 48 个上下文按照 `train/dev/locked_test = 32/8/8` 分组，同一上下文只属于一个分组；
- 类别数量固定为 `opening/probe/event/clarify/repair/integrate_close = 4/12/12/6/6/8`；
- `locked_test` 在正式评测前不得用于 Prompt 示例选择或规则调参；
- `review_examples_v1.jsonl` 只能引用 train/dev；其 context ID 与 locked-test 的交集必须为零；
- 正式评估只接受仓库中的 canonical manifest；原始 JSONL 不能绕过路径、哈希、冻结状态或隔离校验；
- 真实测评对话尚未进入语料；未来如需使用，必须先核对知情同意范围、完成去标识化并通过人工审批。

### 48-context 冻结状态机

第 3B-4A 先实现冻结门禁，并保持 manifest 与 48 条 context 为 `provisional_synthetic`。2026-07-28 经成员 A / PSY 再次确认后，第 3B-4B 已将 manifest 与全部 48 条 context 同步冻结为 `frozen_v1`；该转换没有生成候选。

本次冻结已满足 `FREEZE-A` 至 `FREEZE-H`、`RJ-01` 至 `RJ-12`、完整后端回归、标准 CLI 验收和成员 A / PSY 正式确认，并同步完成以下状态转换：

- manifest 与全部 48 条 context 一次性改为 `frozen_v1`，禁止混合状态；
- 写入包含批准角色、批准门禁、拒绝测试和 `candidate_generation_started: false` 的冻结记录；
- 更新 development 与 locked 的 SHA-256，并继续校验 review examples 和全部必需执行资产的既有 SHA-256；
- `candidate_generator_status` 保持 `pending_before_generation`，候选生成改由下一阶段的独立来源记录追踪，不再修改已冻结的 context manifest。

这里的 `frozen_v1` 只表示评估输入固定，不表示双人盲评、测量效度、UAT 或生产发布已经通过。`review_examples_v1.jsonl` 仍是 `provisional_synthetic` 的作者规则筛查资产，不能伪装成人工评审结果。

每个上下文都包含三个渲染边界字段：

- `frozen_plan`：Renderer 必须忠实执行且不得修改的行动、目标维度和问题意图；
- `allowed_facts`：可在可见回复中使用的事实白名单；
- `reflection_basis`：允许暂定复述的用户 turn、受支持摘要以及明确禁止推断的内容。

`review_examples_v1.jsonl` 中的 `policy_review` 只是编写阶段的规则筛查预期，不是人类评审结论；`human_review` 保持 `null`，直至独立盲评流程真实完成。

因此，下列验收项仍未完成或尚未形成仓库内可核验证据：

1. 从仓库外 `complete` generation manifest 生成并审核脱敏回执；在此之前不得判断正式候选已经生成或尚未生成；
2. 按“每个上下文生成三个候选”的冻结契约，对实际生成并经回执核验的三个候选执行随机顺序双盲评审；
3. 至少两名评审独立给出自然度、温暖度、清晰度、计划忠实度和非诱导性评分；
4. 运行独立一致性分析工具并把报告作为验收附件，同时计算相对 baseline 的偏好率；
5. 进行 10–20 次内部 UAT；
6. 验证总响应 p95、Renderer 兜底率及真实模型下的零容忍违规率。

在这些步骤完成前，`humanistic_v1` 不应成为生产默认风格，也不得据此宣称心理治疗效果或测量效度提升。

## 盲评约定

- 评审界面隐藏模型、Prompt 版本、候选顺序和预期标签；
- 同一上下文的候选随机展示，评审不得查看 `locked_test` 的参考解释；
- 忠实度或非诱导性未满分的候选直接淘汰；
- 任一零容忍错误码命中的候选直接淘汰；
- 只有完成双人评审并解决分歧后，才能把状态改成后续正式状态；不得在本数据包中预填人类评分。
