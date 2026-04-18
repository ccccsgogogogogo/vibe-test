from __future__ import annotations

import json
from typing import Dict, Iterable, List

import requests

from .base import StreamingModelClient


class HTTPStreamingModelClient(StreamingModelClient):
    """通用 HTTP 流式模型客户端，具体厂商在子类中复用。"""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model_name: str,
        headers: Dict[str, str] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model_name = model_name
        self.headers = headers or {}

    def build_payload(self, messages: List[Dict[str, str]]) -> Dict:
        """构造兼容 OpenAI 风格的基础请求体。"""

        return {
            "model": self.model_name,
            "messages": messages,
            "stream": True,
        }

    def stream_chat(self, messages: List[Dict[str, str]]) -> Iterable[str]:
        """发送流式请求并逐行解析 SSE 数据。"""

        missing_fields = []
        if not self.base_url:
            missing_fields.append("base_url")
        if not self.api_key:
            missing_fields.append("api_key")
        if not self.model_name:
            missing_fields.append("model_name")

        if missing_fields:
            yield f"[模型配置缺失: {', '.join(missing_fields)}]"
            return

        req_headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            **self.headers,
        }

        with requests.post(
            self.base_url,
            headers=req_headers,
            data=json.dumps(self.build_payload(messages)),
            stream=True,
            timeout=120,
        ) as resp:
            resp.raise_for_status()
            for raw_line in resp.iter_lines(decode_unicode=True):
                if not raw_line:
                    continue
                # 兼容 data: 前缀的流式事件格式。
                text = raw_line.strip()
                if text.startswith("data:"):
                    text = text[5:].strip()
                if text == "[DONE]":
                    break
                yield text


class QwenClient(HTTPStreamingModelClient):
    """Qwen 模型适配器占位类。"""

    pass


class KimiClient(HTTPStreamingModelClient):
    """Kimi 模型适配器占位类。"""

    pass


class DeepSeekClient(HTTPStreamingModelClient):
    """DeepSeek 模型适配器占位类。"""

    pass
