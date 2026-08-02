"""DeepSeek API 连通性快速检查。

不依赖项目内部模型封装，直接调用 DeepSeek API，用于排查 key/模型名/网络问题。
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings


def main() -> int:
    settings = get_settings()
    api_key = settings.DEEPSEEK_API_KEY
    base_url = settings.DEEPSEEK_BASE_URL.rstrip("/")
    model = settings.DEEPSEEK_MODEL

    print(f"BASE_URL: {base_url}")
    print(f"MODEL:    {model}")
    print(f"API_KEY:  {'已配置' if api_key else '未配置'}")
    print()

    if not api_key:
        print("错误：DEEPSEEK_API_KEY 未配置")
        return 1

    endpoint = f"{base_url}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": "你是一个帮助测试 API 连通性的助手。"},
            {"role": "user", "content": "请回复：API 连通性测试成功。"},
        ],
        "temperature": 0.3,
        "max_tokens": 128,
        "stream": False,
    }

    try:
        response = httpx.post(endpoint, headers=headers, json=body, timeout=60)
        print(f"HTTP status: {response.status_code}")
        print(f"Response headers: {dict(response.headers)}")
        print(f"Response body:\n{response.text}")
        response.raise_for_status()
        data = response.json()
        content = data["choices"][0]["message"]["content"]
        print(f"\n模型回复：{content}")
        return 0
    except httpx.HTTPStatusError as exc:
        print(f"\nHTTP 错误：{exc.response.status_code}")
        print(exc.response.text)
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"\n请求失败：{type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
