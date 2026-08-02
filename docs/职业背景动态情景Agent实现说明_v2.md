# 职业背景动态情景 Agent 实现说明 v2.0

## 已实现链路

新匿名会话不再直接进入固定“产品上线前 48 小时”情景，而是执行：

```text
创建 onboarding 会话
  ├─ 后台：职业基础情景生成 → 审查 → 缓存
  └─ 前台：ProfileAgent 进行 1–3 轮非评分背景访谈
                         ↓
                  单次表层适配
                         ↓
       本地 Schema + 结构指纹校验并物化
                         ↓
             正式六阶段测评 in_progress
```

`started_at` 只在正式第一阶段激活时写入。背景访谈的 `DialogueTurn.stage_id` 为 `null`，类型为 `profile_question`、`profile_answer` 或 `profile_completed`。

## 测量不变量

生成模型只能生产职业外壳、事实材料、角色关系和动态证据文本。以下内容由代码和现有关系模板固定：

- 六个阶段代码、顺序、主次维度和权重；
- 六个固定主问题任务合同；
- 每阶段最多两次正式追问；
- 每阶段的提问合同（`exit_criteria_json.question_contract`）：定点探针问句（S1 两条单点追问文案）与追问结构约束（单问号、禁复合、禁重问核心、跨阶段问句去重）由配置固定，运行时由 `app/agents/question_contract.py` 统一执行，mock 与 real 走同一引擎；
- 追问规则代码、类型、优先级、次数和维度绑定；
- 动态信息所属阶段及六类测量功能；
- 评分和报告结构。

适配前后结构指纹覆盖阶段代码、动态信息代码与测量功能、核心事实 ID、条件关系，以及背景、阶段材料和动态证据中的数字。任何结构变化都会导致适配失败并回退到已审查的职业基础情景。

## 模型、Prompt 与降级

ProfileAgent、ScenarioDesignAgent、ScenarioReviewAgent 和 ScenarioAdaptationAgent 均通过现有 `ModelGatewayService` 使用同一套 DeepSeek 配置。启用的 `PromptTemplate.content` 会实际并入模型输入；`AgentTrace` 保存对应模板 ID、版本、模型、耗时、输入摘要、输出和错误码。

降级顺序为：

1. ProfileAgent 失败：使用固定人本主义问题继续或结束画像；
2. 个体适配失败或结构指纹改变：使用职业基础情景；
3. 基础生成或审查失败：使用 `general_cctst_fallback_v2` 通用六阶段情景；
4. `MODEL_GATEWAY_MODE=mock`：使用确定性职业情景夹具。

后台任务通过独立 SQLAlchemy Session 执行。`drafting`、`reviewing` 或 `adapting` 锁超过 120 秒后可由准备状态查询恢复。`adapting` 状态作为幂等锁，重复轮询不会发起第二次个体适配。

## 隐私与研究导出

- 正式 Agent 上下文会过滤全部 `profile_*` 对话；评分和报告也使用同一过滤后的上下文。
- 管理员复核保留独立背景访谈区域及情景生成 Trace。
- 匿名研究导出只保留职业大类；删除具体职业、画像、背景访谈和 ProfileAgent Trace。
- 情景生成/审查/适配 Trace 的原始输入输出在匿名导出中被裁剪，避免间接导出具体职业或画像。

## 主要接口

- `POST /api/v1/sessions`：昵称、职业大类和具体职业为必填项，返回 `onboarding` 会话；
- `POST /api/v1/sessions/{uuid}/profile/turns/stream`：提交背景回答并流式返回追问或完成事件；
- `GET /api/v1/sessions/{uuid}/preparation`：查询访谈、缓存、适配及降级状态；
- 原正式作答、结束、报告接口保持不变；正式开始前调用结束接口会返回 `409`。

## 验证

```bash
cd backend
.venv/bin/alembic upgrade head
.venv/bin/python scripts/seed_db.py
.venv/bin/python scripts/check_adaptive_scenario_flow.py

cd ../frontend
npm test -- --run
npm run build
```

`check_adaptive_scenario_flow.py` 使用临时 SQLite 数据库，不修改本地 MySQL；覆盖职业字段校验、背景访谈、同职业缓存、结构校验、重复适配上限、生成失败降级、适配失败降级和正式上下文隔离。
