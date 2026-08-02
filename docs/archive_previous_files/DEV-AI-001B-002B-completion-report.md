# DEV-AI-001B / DEV-AI-002B 完成报告

**负责人**：全栈 B  
**任务范围**：Agent 对话编排（HostAgent / FollowupAgent / 对话策略模块）及对话侧真实 LLM 接入  
**完成时间**：2026-06-29  
**对应文档**：`docs/03_基线版开发任务分配.md` 第 8.7 节

---

## 1. 交付目标

完成“用户回答后，系统判断下一步问什么或是否推进阶段”的对话编排链路，并支持从 mock 模式切换到真实 DeepSeek 模型。

---

## 2. 新增 / 修改文件

### 2.1 核心 Agent 模块（DEV-AI-001B）

| 文件路径 | 说明 |
| --- | --- |
| `backend/app/agents/host_agent.py` | HostAgent 入口：生成阶段开场 / 切换问题 |
| `backend/app/agents/followup_agent.py` | FollowupAgent 入口：生成追问、释放动态信息或推进阶段 |
| `backend/app/agents/dialogue_policy.py` | 对话策略：决定 ask_followup / advance_stage / finish_ready / wait_user_answer |
| `backend/app/agents/dialogue_llm_client.py` | 对话侧统一模型网关封装（mock / real 双模式） |
| `backend/app/agents/dialogue_prompts.py` | Host / Followup 提示词模板，动态注入 context |
| `backend/app/agents/mock_dialogue.py` | MockHostAgent / MockFollowupAgent / MockDialogueAgent，无 API Key 可跑 |
| `backend/scripts/check_dialogue_agent.py` | 001B 验收测试脚本，10 个测试用例 |

### 2.2 真实模型接入（DEV-AI-002B）

| 文件路径 | 说明 |
| --- | --- |
| `backend/app/agents/dialogue_llm_client.py` | 增大默认 `max_tokens=2048`；增加 Markdown JSON 清理与宽松 JSON 解析兜底 |
| `backend/app/agents/dialogue_prompts.py` | Host prompt 要求模型返回 `stage_code`，避免 schema 校验失败 |
| `backend/scripts/check_deepseek_connectivity.py` | DeepSeek API 连通性快速检查脚本 |
| `backend/scripts/check_dialogue_agent_real.py` | 002B 真实模型回归测试脚本 |

### 2.3 未修改的边界文件

按任务分配要求，以下文件**未修改**：

- `backend/app/agents/schemas.py`
- `backend/app/agents/base.py`
- `backend/app/services/session_service.py`
- `backend/app/services/agent_orchestrator.py`
- `backend/app/agents/scoring_agent.py`
- `backend/app/agents/report_agent.py`

---

## 3. 关键设计决策

### 3.1 不硬编码情境与评分规则

- 所有阶段问题、追问文本、动态信息均从 `AgentRuntimeContext` 动态读取；
- 不直接引用数据库 ID、rubric 维度名称或具体情境文本；
- 测试用例 `test_no_hardcoded_scenario_text` 验证不同 `stage.main_question` 会生成不同输出。

### 3.2 失败兜底策略

- `HostAgent` / `FollowupAgent` 在真实模型失败、JSON 解析失败或字段缺失时，自动降级为 `Mock` 输出；
- 降级输出标记 `fallback_used=True`，并在 `warnings` 中记录失败原因；
- `DialogueLLMClient` 增加 `strip_markdown_json` 和 `parse_json_loose`，处理模型可能输出的 Markdown 包裹或前后说明文字。

### 3.3 阶段终点判断

- 不硬编码阶段总数；
- 通过候选规则中是否存在 `rule_type=advance` 且带 `exit_prompt` 的规则，判断当前是否为最后阶段。

### 3.4 对话策略

- `DialoguePolicy.decide(context)` 只读取上下文，不修改状态；
- 追问次数统计只统计当前阶段 speaker=ai 且 content_type 为 `followup_question` / `dynamic_info_question` 的轮次；
- 规则选择优先级：评分缺口匹配 > 触发条件关键词匹配 > priority 升序。

---

## 4. 测试与验收

### 4.1 DEV-AI-001B 验收测试（mock 模式）

```bash
cd backend
python scripts/check_dialogue_agent.py
```

**结果**：通过 10 / 10

覆盖场景：

1. HostOutput schema 校验
2. 普通追问（`content_type=followup_question`）
3. 动态信息释放（`content_type=dynamic_info_question` + `selected_dynamic_info_code`）
4. fallback — 规则缺失（`fallback_used=True`）
5. fallback — 无规则无动态信息（推进阶段）
6. 阶段推进（`next_action=advance_stage`）
7. finish_ready（`next_action=finish_ready`）
8. DialoguePolicy 独立决策
9. MockDialogueAgent 统一入口
10. 无硬编码情境文本

### 4.2 DEV-AI-002B 真实模型回归测试

```bash
cd backend
$env:MODEL_GATEWAY_MODE='real'
$env:DEEPSEEK_API_KEY='你的 API Key'
python scripts/check_dialogue_agent_real.py
```

**结果**：4 / 4 通过

1. HostAgent real 模式生成阶段问题
2. FollowupAgent real 模式生成追问
3. HostAgent 非 JSON 输出兜底
4. FollowupAgent 字段缺失输出兜底

### 4.3 契约与编译检查

```bash
python scripts/check_agent_contract.py
python -m compileall app/agents scripts
```

**结果**：均通过。

---

## 5. 运行示例

### 5.1 mock 模式（无 API Key）

```powershell
cd backend
$env:MODEL_GATEWAY_MODE='mock'
python scripts/check_dialogue_agent.py
```

### 5.2 real 模式

```powershell
cd backend
$env:MODEL_GATEWAY_MODE='real'
$env:DEEPSEEK_API_KEY='sk-...'
python scripts/check_dialogue_agent_real.py
```

---

## 6. 已知限制与后续建议

### 6.1 当前限制

1. `DialogueLLMClient` 当前以同步方式封装 `async` 模型网关调用，在已有事件循环的上下文（如 FastAPI 请求处理中）会新建事件循环。后续建议为生产环境提供 `async` 接口；
2. 真实模型追问的 `question_type` 由模型自行选择，可能与配置侧规则类型不完全一致，后续可加入规则类型约束或后处理；
3. 动态信息释放策略目前偏保守，仅在评分缺口明确或用户回答较短时触发，后续可根据心理组情境蓝图进一步调优。

### 6.2 建议下一步

1. **DEV-BE-003（全栈 A）**：把 B 的 `HostOutput` / `FollowupOutput` 接入 `agent_orchestrator.py`，保存为 `DialogueTurn` 和 `AgentTrace`；
2. **DEV-AI-001C（全栈 C）**：完成 ScoringAgent / ReportAgent，输出 `ScoreGapSummary` 供 B 的追问编排使用；
3. **FE-001（前端开发）**：做用户测评端入口页和测评对话页，调用后端 Session API；
4. **端到端 demo**：A/B/C 联调一次“创建 session → 用户回答 → Agent 追问 → 评分 → 报告”完整流程。

---

## 7. 安全与合规

- API Key 未写入代码或配置文件，仅通过环境变量使用；
- 真实模型输出仅生成阶段问题和追问，不输出六维评分、不生成最终报告、不做临床诊断；
- 所有用户可见 AI 文本均可通过 `HostOutput.message` / `FollowupOutput.question` 获取，便于 A 落库审计。

---

## 8. 结论

DEV-AI-001B 和 DEV-AI-002B 已完成，满足任务分配文档中的验收标准：

- mock 模式可稳定运行；
- 真实模型模式可切换并输出合法结构化 JSON；
- 普通追问、动态信息释放、fallback、阶段推进四类场景均已覆盖；
- 失败时可降级，不阻塞流程；
- 未越界修改全栈 A / 全栈 C 的 owner 文件。

可交付给全栈 A 进行落库与编排集成。
