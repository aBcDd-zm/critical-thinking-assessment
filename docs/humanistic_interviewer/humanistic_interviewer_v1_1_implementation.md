# 罗杰斯式人本访谈增强 v1.1 实现说明

## 定位与边界

`humanistic_v1_1` 是审辩式思维测评的表达增强层，不是心理咨询、心理治疗、
罗杰斯人格模拟或第七个测评维度。Planner 继续确定行动、目标维度、证据缺口、
动态事实和阶段推进；表达层只能改变用户可见措辞，不能改变测量计划和评分规则。

运行时默认值保持：

```dotenv
INTERVIEWER_STYLE_ENABLED=false
INTERVIEWER_STYLE_DEFAULT=baseline_v1
```

旧的 `baseline_v1`、`humanistic_v1`、Prompt 和历史 Trace 均予以保留。
v1.1 使用独立的 `humanistic_compact_v1_1` Prompt，不通过修改旧 Prompt
实现升级。

## “接—映—核—问—记”

| 步骤 | v1.1 行为 | 硬边界 |
| --- | --- | --- |
| 接 | 中性承接用户当前关注点 | 不赞扬、不判断答案好坏 |
| 映 | 使用当前计划允许的用户逐字原话 | 不补写情绪、人格、动机或隐藏心理 |
| 核 | 仅在模糊、矛盾、纠正或澄清时使用试探措辞 | 正式测量回合不能因此增加第二个问题 |
| 问 | 执行 Planner 冻结的单一证据问题 | 最多两句、90 字、一个问号，不提供答案 |
| 记 | 保存来源片段、三个候选、校验和选择结果 | 不把 Renderer 输出当作用户证据 |

双面反映只有在用户明确表达两个冲突面时才可使用，并分别保留两个逐字来源。
例如用户说“我想尽快上线，但又不愿扩大故障影响”，可以反映这两个已表达的
考虑；不能改写为“你害怕失败，也在逃避责任”。

当用户要求 AI 代替决策时，系统说明不能替其承担情境决策，并追问用户自己的
判断标准。若同一回答还包含实质分析，实质部分仍进入正常证据提取，不能因为
一句“你帮我选”而丢弃整段回答。

已有确定性非测量路由（补充当前情境、解释术语或处理角色边界）
直接保留其已经校验的事实与说明，不再交给表达 Renderer 改写。此类回合记录
`candidate_selection_applied=false` 和
`renderer_bypass_reason=deterministic_non_measurement_router`，避免为了套用
三候选结构而把确定性事实回答退化为泛化追问。纯“替我决定”请求仍使用 v1.1
专门的自主支持候选族。用户明确表示“什么意思”、“没看懂”等理解
困难时例外：确定性路由仍冻结测量意图，但允许一次有上下文的表达调用，
由系统用日常语言解释前一问，不把解释责任推回给用户。

若用户明确指出系统理解有误，或用“我的重点是”“我的意思是”“我说了”
“都说过”等表达纠正焦点，
v1.1 非测量路由会逐字引用该纠正片段，再接续一个未重复的问题；不会继续复述
被用户否定的旧理解，也不会因此增加第二个问题。

## 体验、语义与延迟策略（UX5）

`humanistic_v1_1` 的运行时表达采用“确定性测量、单次自适应表达”：

- PROBE、CHALLENGE、RELEASE_EVENT、CLARIFY 和 INTEGRATE 在 `adaptive`
  模式下各允许一次短 Renderer 调用；开场、结束、事实查询和角色边界
  仍使用确定性路径；
- Renderer 可以结合最近六条可见对话改写候选问题，但必须保留每组
  测量语义锚点、来源引用和动态事实；不能改变 Planner 目标；
- 理解失败时必须解释前一个可解释的具体问题，低信息时必须结合当前任务
  缩小问题；“具体说说”、转述前问和“你在说什么”统一识别为澄清请求，
  跳过已失败的元澄清轮，拒绝“具体指哪部分”或抽象“边界/限制”循环；
- 拒绝“你提到”“刚才的问题是想了解”等模板化元话语，也不用“是第一步”
  之类表达评价用户的回答；
- 用户明确问原因、责任、进度或影响时，最终问题必须保留同一语义焦点；
  例如“谁负责”不得改写成“谁受影响”；
- 承接采用“简短回应—焦点相关单问题”，不评价用户的做法是否“直接”
  “稳妥”或“正确”；开会、分工、延迟、进度和组员等常见焦点有独立的
  口语化承接路径；
- RELEASE_EVENT 按“承接用户当前选择—信息边界或转场—原样新事实—
  相关单问题”呈现，禁止新信息裸露或冒充成对用户追问的答案；
- Renderer 不重做测量规划，失败时继续使用
  同一份已校验的确定性结果；v1.1 不进行模型重试；
- 单轮共享总预算默认 8 秒，复杂 Renderer 的一次调用预算默认 5 秒，硬上限
  分别为 15 秒和 6 秒；这两个运行时预算不修改候选生成冻结配置；
- 前端只展示阶段化等待提示，不流式显示尚未通过 Validator 的草稿，避免用户
  因短暂无反馈而重复提交，也不牺牲失败关闭边界。

对应环境变量为：

```dotenv
RUNTIME_CONSULTATIVE_TURN_TIMEOUT_SECONDS=8
RUNTIME_INTERVIEWER_RENDER_TIMEOUT_SECONDS=5
RUNTIME_HUMANISTIC_V11_MODEL_POLISH_MODE=adaptive
```

`RUNTIME_HUMANISTIC_V11_MODEL_POLISH_MODE` 允许 `off`、`complex_only`、
`adaptive` 或 `always`。当前 v1.1 体验默认为 `adaptive`；`always` 只用于
诊断，不应作为生产默认值。

## Prompt 与审计

数据库通过 `agent_name + template_code + version + status=active` 精确解析
不可变 Prompt：

```text
agent_name   = interviewer
template_code = humanistic_compact_v1_1
version       = humanistic_compact_v1_1
```

`PromptTemplate.version` 扩展为 64 字符，以兼容现有和后续不可变版本标识。
AgentTrace 记录候选文本及 ID、校验码、相似度、是否可选、最终候选与理由、
`candidate_intent_key`、`intent_family`、`candidate_mapping_source`、
`candidate_mapping_fields`、`candidate_mapping_fingerprint`、Planner 原始
问题意图、逐字反映来源、Renderer 状态和兜底原因。意图族注册表依据
`question_intent + target_evidence + action + event` 确定性解析，每个意图族
固定提供三个等价候选；硬门禁会从受保护的 Planner 字段重新计算映射、指纹、
候选原文和语义锚点，不能仅凭候选自报同一个标签通过。硬门禁还核对最终候选与可见问题、
以及动态事实全文；Renderer 省略动态事实的任一分句都会失败关闭。管理端沿用
现有 JSON 展开和匿名导出，不增加被试端或公开 API。

用户证据按回应来源标记为：

- `spontaneous_evidence`：回答开场或已释放动态信息；
- `elicited_evidence`：回答正式追问或整合问题；
- `not_scored`：控制、解释或澄清回合。

这里按当前用户回合的确定性意图判断是否计分：用户发出的控制、解释或澄清
请求本身为 `not_scored`；若系统完成说明后，用户随后给出被 Planner 判定为
正式实质回答的内容，则标为 `elicited_evidence`，不能仅因上一条 AI 消息的
类型是 `interview_clarification` 而丢弃该证据。

`ai_copy_exclusion_v1` 规则仅使用用户原话评分：Planner 必须先把行为证据保存
为对应行为的最小连续原文片段，而不是整段回答。如果不少于四个汉字的候选证据
片段逐字出现在紧邻 AI 消息中、且从未出现在更早的用户回答中，则记录
`introduced_by_ai=true`、`validity=invalid`，不更新六维证据槽。同一回答中的
其他原创证据仍可保留；AI 复述用户早先原话时不判污染。置信度前后值继续为
空值，仅记录证据槽状态变化和已有抽取置信度。

## v1.1 独立评测合同

v1.1 复用已冻结的 48 个上下文和 v1 评分阈值，但不继承 v1 候选、评分或放行
结论。正式配置为
[`evaluation_config_v1_1.json`](evaluation_config_v1_1.json)，其中明确
`legacy_v1_evidence_accepted=false`。

每条候选、评分、arm key、运行记录和 UAT JSONL 顶层都必须包含：

```json
{"evidence_namespace": "humanistic_v1_1"}
```

运行记录和 UAT 还必须写入 `style_version`、`prompt_version` 与
`runtime_source_bundle_sha256`。独立的 v1.1 evidence receipt 不仅绑定六份
证据文件，还绑定候选选择器、确定性意图族注册表、运行时 Renderer、Validator、Session 集成、
匿名审计导出、证据追踪器、行为片段抽取器、确定性 Planner 和 Prompt 种子
源码；因此不能只靠自报版本字符串
把其他代码生成的证据用于放行：

```json
{
  "schema_version": "humanistic_evaluation_receipt_v1_1",
  "receipt_status": "VERIFIED_COMPLETE_V1_1_EVIDENCE",
  "evidence_namespace": "humanistic_v1_1",
  "style_version": "humanistic_v1_1",
  "prompt_version": "humanistic_compact_v1_1",
  "candidate_generation_version": "humanistic_candidate_generation_v1_1",
  "blind_review_version": "humanistic_blind_review_v1_1",
  "measurement_policy_version": "ai_copy_exclusion_v1",
  "config_sha256": "<64位SHA-256>",
  "context_manifest_sha256": "<64位SHA-256>",
  "runtime_source_bundle_version": "humanistic_v1_1_runtime_source_bundle_v2",
  "runtime_source_bundle_sha256": "<64位SHA-256>",
  "runtime_sources": {
    "humanistic_microstructure": {
      "path": "backend/app/agents/humanistic_interviewer_v11.py",
      "sha256": "<64位SHA-256>"
    },
    "candidate_intent_registry": {
      "path": "backend/app/agents/humanistic_v11_intent_registry.py",
      "sha256": "<64位SHA-256>"
    },
    "...": {"path": "<仓库相对路径>", "sha256": "<64位SHA-256>"}
  },
  "files": {
    "candidate_packet": {"sha256": "<64位SHA-256>"},
    "ratings": {"sha256": "<64位SHA-256>"},
    "arm_key": {"sha256": "<64位SHA-256>"},
    "runtime_records": {"sha256": "<64位SHA-256>"},
    "uat_records": {"sha256": "<64位SHA-256>"},
    "measurement_approval": {"sha256": "<64位SHA-256>"}
  }
}
```

成员 A 的测量合同批准文件必须为真实审批记录，而非工具自动生成：

```json
{
  "schema_version": "humanistic_measurement_contract_approval_v1_1",
  "evidence_namespace": "humanistic_v1_1",
  "measurement_policy_version": "ai_copy_exclusion_v1",
  "approver_role": "member_a",
  "approved": true,
  "evidence_ref": "<可复核批准记录>"
}
```

门禁命令：

```bash
cd backend
.venv/bin/python scripts/evaluate_humanistic_interviewer_v1_1.py \
  --receipt /private/v1_1/evaluation_receipt_v1_1.json \
  --candidate-packet /private/v1_1/candidate_packet.jsonl \
  --ratings /private/v1_1/ratings.jsonl \
  --arm-key /private/v1_1/arm_key.jsonl \
  --runtime-records /private/v1_1/runtime.jsonl \
  --uat-records /private/v1_1/uat.jsonl \
  --measurement-approval /private/v1_1/member_a_approval.json
```

退出码为 `0=PASS`、`1=FAIL`、`2=BLOCKED`。任一 v1.1 回执、双人盲评、
真实运行样本、10–20 次 UAT 或成员 A 批准缺失时都必须返回 `BLOCKED`。
工具只校验证据，不生成候选、人工评分、审批或 UAT 结论。

v1.1 运行时表达层另对所有 `RELEASE_EVENT` 施加新信息可见转场契约：
先承接用户当前选择，用“为了……”说明引入理由，再用“我补充一条/
一项新的……”明确标记新信息，之后原样呈现事实并只问一个与用户当前
行动相关的问题。缺少引入理由或新信息标识任一项时，模型输出被拒绝，
改用同契约的确定性安全兜底。理由位置必须早于新信息标识，同一轮不得
反复用“你想/你会”召唤用户。该变更不修改事件事实、六维测量、评分或事件推进顺序。

## 发布纪律

即使本地测试和构建通过，也不得据此启用 v1.1。只有 v1.1 专属门禁返回
`PASS`、成员 A/B 共同复核证据并另行批准生产变更后，才可在受控环境切换。
本次实现不修改生产开关、不部署，也不修改原始 Word 研究材料。
