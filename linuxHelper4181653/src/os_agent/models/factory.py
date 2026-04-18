from __future__ import annotations

from os_agent.config import AppConfig

from .adapters import DeepSeekClient, KimiClient, QwenClient
from .base import StreamingModelClient


def build_model_client(cfg: AppConfig) -> StreamingModelClient:
    """根据配置选择具体模型供应商客户端。"""

    provider = cfg.model_provider.lower().strip()

    if provider == "qwen":
        return QwenClient(
            base_url=cfg.qwen_base_url,
            api_key=cfg.qwen_api_key,
            model_name=cfg.model_name,
        )
    if provider == "kimi":
        return KimiClient(
            base_url=cfg.kimi_base_url,
            api_key=cfg.kimi_api_key,
            model_name=cfg.model_name,
        )
    if provider == "deepseek":
        return DeepSeekClient(
            base_url=cfg.deepseek_base_url,
            api_key=cfg.deepseek_api_key,
            model_name=cfg.model_name,
        )

    raise ValueError(f"Unsupported model provider: {cfg.model_provider}")
