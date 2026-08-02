# 基线版 API 合同 v1

本文档用于约定前端、Agent 模块和数据库服务之间的第一版接口边界。当前阶段先保证链路可运行，后续评分、报告和多 Agent 编排在此基础上扩展。

## 1. 基础约定

- Base URL: `http://127.0.0.1:8000/api/v1`
- Content-Type: `application/json`
- 第一版暂不做用户账号体系，受测者数据与一次测评会话绑定。
- 模型网关默认使用 `mock` 模式，方便无 API Key 环境启动；需要真实调用 DeepSeek 时再切换为 `real`。

## 2. 健康检查

### GET `/health`

用途：检查后端服务是否启动。

示例响应：

```json
{
  "status": "ok"
}
```

### GET `/health/db`

用途：检查数据库连接是否可用。

示例响应：

```json
{
  "status": "ok",
  "database": "connected"
}
```

## 3. 情境读取

### GET `/scenarios/default`

用途：读取当前默认测评情境、阶段、动态信息、追问策略和目标维度，供前端预览或 Agent 编排使用。

验收标准：

- 返回启用状态的默认情境；
- 至少包含一个启用阶段；
- 阶段中可包含动态信息和干预规则。

## 4. 测评会话

### POST `/sessions`

用途：用户输入昵称后创建一次测评会话，同时写入 `participant` 和 `assessment_session`，并生成第一条 AI 开场问题。

请求示例：

```json
{
  "nickname": "小秦",
  "info_collect_method": "ai_dialogue",
  "assessment_mode": "mock"
}
```

响应示例：

```json
{
  "session_uuid": "f3f5c2b8-8f19-4d1c-96d4-38e4e04c1200",
  "status": "in_progress",
  "participant_nickname": "小秦",
  "scenario": {
    "scenario_code": "product_launch_48h",
    "title": "产品上线前 48 小时",
    "estimated_minutes": 30,
    "version": "v1"
  },
  "current_stage": {
    "stage_code": "s1",
    "title": "初始问题界定",
    "stage_order": 1,
    "main_question": "你会如何判断当前是否应该按计划上线？",
    "max_followups": 3
  },
  "turns": [
    {
      "turn_index": 1,
      "speaker": "ai",
      "content": "小秦，你好。我们先进入本次测评的第一个情境问题：...",
      "content_type": "stage_question",
      "created_at": "2026-06-27T14:40:00"
    }
  ]
}
```

### GET `/sessions/{session_uuid}`

用途：读取会话当前状态和完整对话历史。

前端使用场景：

- 刷新页面后恢复当前测评；
- 报告生成前确认会话状态；
- 调试用户与 AI 的交互过程。

### POST `/sessions/{session_uuid}/turns`

用途：保存用户的一轮回答。当前版本先只落库，后续会接入 FollowupAgent 生成下一轮 AI 追问。

请求示例：

```json
{
  "content": "我会先确认问题影响范围、用户投诉数量和延期成本，再决定是否上线。",
  "content_type": "scenario_answer"
}
```

响应示例：

```json
{
  "session_uuid": "f3f5c2b8-8f19-4d1c-96d4-38e4e04c1200",
  "saved_turn_index": 2,
  "next_action": "agent_followup_pending",
  "message": "User turn saved. Agent follow-up service is ready to be connected."
}
```

### POST `/sessions/{session_uuid}/finish`

用途：结束一次测评会话，记录完成时间和总耗时。

响应示例：

```json
{
  "session_uuid": "f3f5c2b8-8f19-4d1c-96d4-38e4e04c1200",
  "status": "completed",
  "completed_at": "2026-06-27T15:05:00"
}
```

### GET `/sessions/{session_uuid}/report`

用途：读取最终报告。当前版本若报告未生成，会返回 `404`。

后续扩展：

- ScoringAgent 写入六维评分、证据句和评分理由；
- ReportAgent 写入结构化报告；
- 前端报告页读取该接口展示图表和文本解释。

## 5. 统一模型网关

统一模型网关负责屏蔽具体模型供应商差异。基线版按 DeepSeek OpenAI-compatible Chat API 接入，默认模型为 `deepseek-v4-pro`。

### GET `/model-gateway/status`

用途：检查当前模型网关配置。

响应示例：

```json
{
  "provider": "deepseek",
  "mode": "mock",
  "model": "deepseek-v4-pro",
  "base_url": "https://api.deepseek.com",
  "api_key_configured": false,
  "thinking_enabled": true,
  "reasoning_effort": "high"
}
```

### POST `/model-gateway/chat`

用途：提供最小可用的 Chat 调用接口，供 Agent 同学先做连通性测试。

请求示例：

```json
{
  "messages": [
    {
      "role": "system",
      "content": "你是一个审辩式思维测评助手。"
    },
    {
      "role": "user",
      "content": "请生成一个简短追问。"
    }
  ],
  "temperature": 0.2,
  "max_tokens": 1024,
  "json_mode": false
}
```

响应示例：

```json
{
  "provider": "deepseek",
  "model": "deepseek-v4-pro",
  "content": "这是统一模型网关的 mock 回复...",
  "raw_response": {
    "mode": "mock",
    "request_message_count": 2
  }
}
```

真实调用 DeepSeek 时需要在 `backend/.env` 中配置：

```env
MODEL_PROVIDER=deepseek
MODEL_GATEWAY_MODE=real
DEEPSEEK_API_KEY=你的 DeepSeek API Key
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-pro
DEEPSEEK_ENABLE_THINKING=true
DEEPSEEK_REASONING_EFFORT=high
```

## 6. 当前联调状态

当前用户测评前端已完成最小闭环，可访问：

```text
http://127.0.0.1:5173/assessment
```

已完成：

- 输入昵称创建会话；
- 展示 AI 开场问题和对话历史；
- 提交用户回答并刷新会话；
- 结束测评并进入报告页；
- 报告未生成时展示六维评分空状态。

以下能力仍属于下一步开发任务：

- Agent 编排接口：接收用户回答并返回 AI 追问；
- 评分写入接口：保存 `ScoreSnapshot`、`ScoreResult` 和 `ScoreEvidence`；
- 报告生成接口：由 ReportAgent 写入最终报告；
- 报告页真实数据联调：读取最终报告并展示六维评分、证据句和建议。
