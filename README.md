# 审辩式思维动态测评系统

本仓库提供一套可运行、可记录、可复核的审辩式思维动态测评系统，包含渐进式 AI 访谈、六维证据评分、结构化报告、管理端复核与匿名导出。

当前用户端支持文本输入、可编辑语音转写和 AI 问题播报。普通浏览器优先使用原生语音识别；微信等不支持 Web Speech API 的环境会录制短音频，由后端调用豆包 ASR 转写，转写结果不会自动提交。

## 文档目录

当前已同步的项目文档位于 `docs/`：

| 文件 | 说明 |
| --- | --- |
| `产品系统接口增量_v1.md` | 当前反馈状态、PDF 下载及前端状态语义 |
| `psych/2026-07-13_assessment_protocol_v1/` | 当前测评协议、追问证据与评分规范 |
| `archive_previous_files/api_contract_v1.md` | 历史基线 API 合同（归档） |
| `archive_previous_files/部署运行手册.md` | 历史本地部署与故障排查手册（归档） |

## 项目结构

```text
backend/
  app/
    api/             # FastAPI 路由
    core/            # 配置、数据库连接
    models/          # SQLAlchemy ORM 模型
    schemas/         # Pydantic schema
    repositories/    # 数据访问封装
    services/        # 业务流程编排
  migrations/        # Alembic migration
  seeds/             # 测评配置 YAML
  scripts/           # 初始化、检查、导入脚本
frontend/
  src/               # Vue 后台管理系统
docs/                # 项目设计文档
```

## 后端启动

推荐用 Docker Compose 启动统一的开发 MySQL：

```bash
docker compose up -d mysql
```

```bash
cd backend
python -m venv .venv
source .venv/bin/activate              # macOS / Linux
# Windows PowerShell：.venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env                   # Windows 可使用 copy
alembic upgrade head
python scripts/seed_db.py
python scripts/check_db.py
python scripts/check_agent_contract.py
uvicorn app.main:app --reload
```

启动后可访问：

```text
http://127.0.0.1:8000/docs
http://127.0.0.1:8000/api/v1/health
http://127.0.0.1:8000/api/v1/health/db
http://127.0.0.1:8000/api/v1/scenarios/default
http://127.0.0.1:8000/api/v1/model-gateway/status
```

后台管理 API 默认账号由 `backend/.env` 控制，首次登录时会自动创建管理员：

```env
ADMIN_USERNAME=admin
ADMIN_PASSWORD=请改成强密码
ADMIN_TOKEN_SECRET=请改成至少32位随机串
```

如果本机暂时没有 Docker，也可以使用本地 MySQL。需要先手动创建数据库：

```sql
CREATE DATABASE IF NOT EXISTS psychological_assessment
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_0900_ai_ci;
```

然后把 `backend/.env` 中的 `DATABASE_URL` 改成自己的 MySQL 用户名、密码和端口。

## 模型网关

基线版已预留统一模型网关，默认供应商为 DeepSeek，默认模型为 `deepseek-v4-pro`。为了保证所有成员无密钥也能启动项目，默认模式是 `mock`：

```env
MODEL_PROVIDER=deepseek
MODEL_GATEWAY_MODE=mock
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-pro
```

如果需要真实调用 DeepSeek API，把 `backend/.env` 改为：

```env
MODEL_GATEWAY_MODE=real
DEEPSEEK_API_KEY=你的 DeepSeek API Key
```

模型网关历史基线说明见 `docs/archive_previous_files/api_contract_v1.md`；本轮产品接口增量见 `docs/产品系统接口增量_v1.md`。

## 豆包语音

生产示例默认关闭第三方语音。配置真实语音时，只把 Key 写入服务器的 `.env.production`，不要提交到 Git：

```env
TTS_MODE=doubao
ASR_MODE=doubao
DOUBAO_TTS_API_KEY=你的新版控制台APIKey
# 如使用同一 Key，DOUBAO_ASR_API_KEY 可以留空。
DOUBAO_ASR_API_KEY=
```

TTS 只处理数据库中已持久化的 AI 回合；ASR 只在内存中处理一次短录音并返回可编辑文字，不保存录音。微信录音可能产生 WebM、MP4 等格式，生产后端镜像通过 ffmpeg 在内存管道中转换为豆包支持的 OGG OPUS。

接口与资源开通要求以火山引擎官方文档为准：

- [大模型录音文件极速版识别 API](https://www.volcengine.com/docs/6561/1631584?lang=zh)
- [语音合成 V3 API 列表](https://www.volcengine.com/docs/6561/2228192?lang=zh)

## 轻量后台启动

后台管理前端位于 `frontend/`，用于维护六维能力模型、评分锚点、情境阶段、动态信息和追问策略。

```bash
cd frontend
npm install
npm run dev
```

启动后访问：

```text
http://127.0.0.1:5173/admin/login
```

默认连接后端地址为 `http://localhost:8000/api/v1`。如需调整，可在 `frontend/.env` 中配置：

```env
VITE_API_BASE_URL=http://127.0.0.1:8000/api/v1
```

## 公开与安全说明

- `.env`、数据库、运行日志、录音、测评会话和导出结果均不得提交。
- 公开仓库包含用于可复现测试的 Prompt、Rubric 和测量规则，不适合作为需要题目保密的高风险正式考试题库。
- 公网部署会消耗模型和语音额度，应在供应商控制台设置额度告警，并保留管理端强密码与服务器回退版本。

## 许可

本仓库当前未授予开源许可证。代码公开用于审阅和项目展示；复制、修改、分发或商业使用需另行获得授权。

## 当前阶段

当前版本已跑通会话、访谈、证据记录、评分、报告、管理端复核与 Docker 部署闭环。正式研究使用前仍需由测量负责人完成量表效度、专家一致性和真实样本验证。
