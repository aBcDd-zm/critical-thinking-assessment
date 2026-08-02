# Humanistic Interviewer v1 验收报告

## 当前结论

**状态：BLOCKED（正式生成状态未核验，且等待真实双人盲评、运行采样和 10–20 次内部 UAT）**

当前已具备来源政策、规则筛查用合成示例、硬校验、私有交接工具与离线门禁工具。仓库当前没有 `generation_receipt_v1.json`，所以仓库外正式候选是否已完整生成仍未核验；PR 描述不能作为证据。仓库也没有真实人工评分、独立一致性报告、真实模型运行指标或 UAT 结果。因此不能填写通过率，不能声称门槛通过，也不能把 `humanistic_v1` 切为默认风格。

## 工程验收记录（2026-07-28）

下列结果只证明本地工程链能够运行，不替代盲评、真实模型采样或 UAT：

| 工程项 | 本次证据 | 结果 |
|---|---|---|
| 来源/语料结构 | 48 contexts、32/8/8、30 正例、12 反例、污染隔离测试 | PASS |
| 后端自动测试 | `40 passed, 190 subtests passed` | PASS |
| Humanistic + v3.3 控制意图专项测试 | `35 passed, 186 subtests passed` | PASS |
| baseline 与 Humanistic 成对状态回放 | action、事件、证据、正式轮数相关状态保持一致 | PASS |
| v3.3 专项回归 | baseline 与 Humanistic 开关状态分别运行 | PASS |
| v3.2 / progressive_v3 回归 | Humanistic 配置下仍冻结 baseline | PASS |
| MySQL 迁移 | `20260723_0009 -> 20260728_0010`，既有会话回填 `baseline_v1` | PASS |
| 管理端、专家复核、匿名导出 | 数据库脚本及 Renderer 审计字段测试 | PASS |
| 完整评分报告链 | 6 维评分与报告生成数据库流程 | PASS |
| 前端 | 12 test files、36 tests；Vue TypeScript 与生产构建 | PASS |
| 默认发布状态 | `INTERVIEWER_STYLE_ENABLED=false`、`baseline_v1` | PASS |
| 离线门禁无证据运行 | 脚本返回 `BLOCKED`、退出码 2 | 符合预期 |

以上数字对应本报告日期的工作树验证快照；代码变化后必须重新运行，不能沿用为新版本证据。

## 证据状态

| 验收项 | 门槛 | 当前证据 | 状态 |
|---|---:|---|---|
| 脱敏 generation receipt | `complete`、48 contexts、144 candidates、哈希与 exact ties 校验 | 仓库无回执；正式生成状态未核验 | BLOCKED |
| 48-context 候选包 | 每个恰好 3 个候选 | 无经回执关联并冻结的盲化包证据 | BLOCKED |
| 独立盲评 | 每个上下文至少 2 人 | 尚未执行 | BLOCKED |
| 独立评审一致性 | 附独立报告、样本数与不可计算原因 | 尚无报告 | BLOCKED |
| 自然度、温暖度、清晰度 | 各 `>= 4/5` | 未计算 | BLOCKED |
| 忠实度、非诱导性 | 各 `100%` | 未计算 | BLOCKED |
| 单问题、事实白名单、反映依据 | 各 `100%` | 未计算 | BLOCKED |
| 临床越界等硬错误 | `0` | 无 locked-test 真人盲评结果 | BLOCKED |
| Humanistic 对 baseline 偏好率 | `>= 60%` | 未计算 | BLOCKED |
| 总响应 p95 | `<= 10s` | 尚无真实运行记录 | BLOCKED |
| Renderer 兜底率 | `<= 5%` | 尚无真实运行记录 | BLOCKED |
| 内部 UAT | 10–20 次通过 | 尚未执行 | BLOCKED |

## 生成最终验收结论

脱敏 generation receipt 经审核、两份人工评分先冻结并通过保管人专用 `prepare-evaluator-inputs` 生成完整私有输入包、运行记录和 UAT 记录全部固定后，运行：

```bash
cd backend
.venv/bin/python -m scripts.evaluate_humanistic_interviewer_v1 \
  --candidate-packet /absolute/private/evaluation/evaluator-inputs-v1/candidate_packet_v1.jsonl \
  --ratings /absolute/private/evaluation/evaluator-inputs-v1/ratings_v1.jsonl \
  --arm-key /absolute/private/evaluation/evaluator-inputs-v1/arm_key_v1.jsonl \
  --runtime-records /absolute/path/runtime_records.jsonl \
  --uat-records /absolute/path/uat_records.jsonl \
  --output /absolute/path/acceptance_result.json
```

评审负责人应把生成的 `acceptance_result.json`、独立 `analyze_humanistic_inter_rater_agreement_v1.py` 输出、两份冻结评分 SHA-256 和 generation receipt 作为本报告附件，并核对全部输入文件哈希和缺陷关闭证据。只有离线门禁返回 `PASS`、一致性结果已披露且成员 A/B 共同审核后，才能单独提交切换默认值的变更；一键回退必须继续保留。

本验收只支持“受人本主义沟通原则启发的测评访谈表达”的工程可用性判断，不证明心理治疗效果，也不证明测量效度提升。
