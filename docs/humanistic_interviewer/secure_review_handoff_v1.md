# Humanistic Interviewer v1 私有候选与双盲评审安全交接

## 当前证据状态

仓库当前不存在 `generation_receipt_v1.json`。因此，仓库内能够确认的是候选生成工具已经实现，**不能确认仓库外正式候选是否已经完整生成**。PR 描述、聊天消息、口头说明或终端截图都不能替代脱敏回执。

若候选保管人已经在仓库外生成正式包，应从私有 `candidate_generation_manifest_v1.json` 生成脱敏回执。回执进入仓库并经审核前，统一使用“正式候选生成状态未核验”，不得写成“已经生成”或“尚未生成”。

脱敏回执只公开：

- `run_id`、`complete` 状态和各类记录计数；
- provider/model 标识；
- 冻结来源文件 SHA-256；
- manifest 声明的各输出 SHA-256；
- manifest 自身 SHA-256 和 exact-model-tie 数量；
- exact tie 大于零时，对 sealed exact-tie 文件的实际 SHA-256、schema、数量和 `run_id` 复核结果。

回执不包含候选正文、case/arm key 内容或 provenance 记录。`output_sha256` 中除 exact-tie 文件外的值属于“经严格 manifest schema 接收的声明值”，不是工具读取并公开私有文件内容后的二次证明。

## 角色与文件隔离

| 角色 | 可以接触 | 不得接触 |
|---|---|---|
| 候选保管人 | 完整私有生成目录、manifest、sealed 文件 | 不得把私有目录提交 Git |
| Reviewer A / B | `reviewer/blind_review_packet_v1.jsonl`、本人空白评分模板 | `sealed/`、generation manifest、另一评审的评分 |
| 解盲保管人 | 两份已冻结评分、case/arm key | 不得在评分冻结前解盲 |
| 成员 A / B 验收 | 脱敏回执、冻结评分哈希、解盲后的评估结果、一致性报告、运行/UAT 证据 | 不以 PR 描述代替证据 |

两名评审必须使用不同的 pseudonymous `reviewer_id`，独立完成全部 48 个 case。不得使用真实姓名、邮箱或其他直接身份信息。

## 第一步：生成脱敏回执

在 `backend/` 下执行：

```bash
.venv/bin/python -m scripts.prepare_humanistic_review_handoff_v1 \
  generation-receipt \
  --manifest /absolute/private/run/candidate_generation_manifest_v1.json \
  --exact-ties /absolute/private/run/sealed/exact_model_ties_v1.jsonl \
  --output ../docs/humanistic_interviewer/generation_receipt_v1.json
```

只有 manifest 中 `exact_model_tie_count > 0` 时才必须提供 `--exact-ties`；为零时省略。工具默认严格要求 48 个 context、144 个候选、完整 schema/计数关系、固定输出哈希集合和 `status: complete`。

以下情况均返回 `BLOCKED`，且不会写出回执：

- manifest 或 exact-tie 文件位于 Git 仓库内；
- schema 不符、存在额外字段/重复 JSON key、状态不是 `complete`；
- context、candidate、case key、arm key 或 provenance 计数不一致；
- exact-tie 数量、`run_id` 或文件 SHA-256 不一致；
- provider/model 不是安全的公开标识；
- 输出文件已经存在。

回执输出使用原子排他创建；工具永不覆盖既有文件。审核人还应通过受控渠道向候选保管人核对 `manifest_sha256` 与 `run_id`。

## 第二步：为两名评审分别生成空白模板

Reviewer A：

```bash
.venv/bin/python -m scripts.prepare_humanistic_review_handoff_v1 \
  ratings-template \
  --packet /absolute/private/run/reviewer/blind_review_packet_v1.jsonl \
  --receipt ../docs/humanistic_interviewer/generation_receipt_v1.json \
  --reviewer-id REVIEWER-A \
  --output /absolute/private/reviews/reviewer-a.blank.jsonl
```

Reviewer B 使用相同 `--receipt`、相同 packet、不同的 `--reviewer-id` 和输出路径重复执行。工具先校验实际 packet SHA-256 与 receipt 中 `reviewer/blind_review_packet_v1.jsonl` 的声明完全一致，再从 receipt 取得正式 context/candidate 数量；命令行不能降低该数量。输入 packet 和输出模板都必须位于仓库外；输出权限为 `0600`，且拒绝覆盖。

工具严格校验 48 个 case、每个 3 个候选、全局唯一 opaque case/candidate ID 和盲包 schema。模板只包含：

- opaque `case_id` / `candidate_id`；
- pseudonymous `reviewer_id`；
- 值为 `null` 的三项 1–5 分量表和五项通过判断；
- 值为 `null` 的硬错误码字段；
- 三组与 arm 无关的通用两两偏好。

模板不复制候选正文、review context、model、Prompt、context ID、split、arm 或 sealed 映射。评审时由受控界面或只读 packet 展示 context 和候选，评分保存在本人模板中。

## 第三步：完成、冻结并解盲

每名评审必须独立完成：

- `review_status` 从 `blank` 改为 `completed`；
- `naturalness`、`warmth`、`clarity` 填写整数 `1–5`；
- 五项 pass 字段填写真实布尔值；
- `hard_error_codes` 经检查后填写数组；确认无命中时才填 `[]`；
- 每组 `pairwise_preferences` 的 `preferred_candidate_id` 必须是该组两个 ID 之一。

两份评分先分别计算 SHA-256、登记冻结时间并锁定为只读，再交给解盲保管人。Reviewer A / B 不参与解盲，也不得收到 sealed 文件。

解盲保管人在 `backend/` 下执行完整 evaluator 输入包准备：

```bash
.venv/bin/python -m scripts.prepare_humanistic_review_handoff_v1 \
  prepare-evaluator-inputs \
  --packet /absolute/private/run/reviewer/blind_review_packet_v1.jsonl \
  --ratings /absolute/private/reviews/reviewer-a.completed.jsonl \
  --ratings /absolute/private/reviews/reviewer-b.completed.jsonl \
  --receipt ../docs/humanistic_interviewer/generation_receipt_v1.json \
  --case-key /absolute/private/run/sealed/case_key_v1.jsonl \
  --arm-key /absolute/private/run/sealed/arm_key_v1.jsonl \
  --exact-ties /absolute/private/run/sealed/exact_model_ties_v1.jsonl \
  --output-dir /absolute/private/evaluation/evaluator-inputs-v1
```

`--ratings` 可以重复提供。receipt 的 `exact_model_tie_count > 0` 时必须提供 `--exact-ties`；为零时可以省略，若仍提供则文件 SHA-256 和零记录状态也必须与 receipt 一致。准备工具执行以下硬校验：

- 实际 blind packet、case key、arm key、exact ties 的 SHA-256 与同一 generation receipt 绑定；
- sealed case/arm key 各覆盖且只覆盖 48 个 case，case、context 和 candidate ID 唯一；
- 评分覆盖全部 48 个 case，每个 case 至少两名不同评审，不接受重复评审；
- blind packet、case key、arm key 覆盖相同 case，且每个 case 的三个 candidate ID 完全一致；
- 每份评分的三个 candidate ID 与 sealed arm key 完全一致；
- 三组通用两两偏好完整且无重复；
- 提供 exact ties 时，case/context/split、model pair、fallback 与 sealed keys 一致，成对候选评分完全相同；
- 所有私有输入和输出都在仓库外，新输出目录以 `0700`、文件以 `0600` 排他创建并拒绝覆盖。

新目录包含：

- `candidate_packet_v1.jsonl`：只含 `context_id` 和三个 candidate ID/正文，不含 `case_id`、review context 或 arm；
- `ratings_v1.jsonl`：只含 evaluator 所需的 `context_id`、reviewer、三候选评分和从冻结两两偏好提取的 `baseline_humanistic_preference`，不含 `arm`；
- `arm_key_v1.jsonl`：只供 evaluator 使用的 `context_id` arm key；
- `evaluator_input_manifest_v1.json`：不含候选正文、arm assignments 或 case mapping，只记录 `run_id`、计数和所有输入/三个输出 SHA-256。

工具只在保管人侧选择 sealed baseline-vs-humanistic 所对应的那组 opaque 两两偏好。完整目录生成前全部输入已校验；目标目录存在时整次拒绝，不产生覆盖。CLI 回执和 `evaluator_input_manifest_v1.json` 应与冻结登记一并保存。

评分冻结后必须生成独立评审一致性报告，并作为验收附件：

```bash
.venv/bin/python -m scripts.analyze_humanistic_inter_rater_agreement_v1 \
  --ratings /absolute/private/evaluation/evaluator-inputs-v1/ratings_v1.jsonl \
  --candidate-packet /absolute/private/evaluation/evaluator-inputs-v1/candidate_packet_v1.jsonl \
  --output /absolute/private/evaluation/inter_rater_agreement_v1.json
```

一致性报告至少应披露量表一致性、布尔项一致性、偏好一致性、有效样本数、缺失/常量项和不可计算原因；不能只给一个笼统“通过”结论。候选正文完全相同的 exact-tie 比较仍纳入量表与布尔项统计，但从偏好一致性统计中排除，避免把无真实可比差异的选择当作分歧。该报告是描述性附件，不单独改变 release gate。

## 仍然保持 BLOCKED 的条件

即使脱敏 generation receipt 验证通过，以下任一项缺失时也不能启用默认风格：

- 两名评审的 48-case 独立评分及冻结 SHA-256；
- 解盲后的完整 evaluator 输入与 PASS/FAIL 结果；
- 独立评审一致性报告；
- 每个 48-context 至少一条真实运行记录；
- 10–20 次完成的内部 UAT 和严重问题关闭证据；
- 成员 A / B 对证据包的共同审核。

回执只解决“私有正式生成是否有可审计记录”这一项，不证明 Humanistic 优于 baseline，不证明测量效度，也不证明已经具备生产发布条件。
