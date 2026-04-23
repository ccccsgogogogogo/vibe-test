from __future__ import annotations

import os
from pathlib import Path
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv
from dotenv import dotenv_values


def _load_env_files() -> None:
    """优先读取 .env；若不存在则回退读取 .env.example。"""

    root = Path(__file__).resolve().parents[2]
    env_file = root / ".env"
    example_file = root / ".env.example"

    if env_file.exists():
        # 强制加载 .env 文件
        load_dotenv(env_file, override=True)
        return

    if example_file.exists():
        # 不覆盖系统中已存在的环境变量，便于外部注入配置。
        load_dotenv(example_file, override=False)


_load_env_files()


@dataclass
class SSHConfig:
    """SSH 连接配置。"""

    host: str
    port: int = 22
    username: str = "root"
    password: Optional[str] = None
    private_key_path: Optional[str] = None


@dataclass
class AppConfig:
    """应用级配置，集中维护模型与连接参数。"""

    model_provider: str = "qwen"
    model_name: str = ""

    qwen_base_url: str = ""
    qwen_api_key: str = ""

    kimi_base_url: str = ""
    kimi_api_key: str = ""

    deepseek_base_url: str = ""
    deepseek_api_key: str = ""

    ssh_enabled: bool = False
    ssh: Optional[SSHConfig] = None


def load_config() -> AppConfig:
    """从环境变量读取配置并构造应用对象。"""

    ssh_enabled = os.getenv("OA_SSH_ENABLED", "false").lower() == "true"

    ssh_cfg: Optional[SSHConfig] = None
    if ssh_enabled:
        # 启用 SSH 时，按环境变量组装远程连接配置。
        ssh_cfg = SSHConfig(
            host=os.getenv("OA_SSH_HOST", ""),
            port=int(os.getenv("OA_SSH_PORT", "22")),
            username=os.getenv("OA_SSH_USERNAME", "root"),
            password=os.getenv("OA_SSH_PASSWORD", "") or None,
            private_key_path=os.getenv("OA_SSH_PRIVATE_KEY", "") or None,
        )

    return AppConfig(
        model_provider=os.getenv("OA_MODEL_PROVIDER", "qwen"),
        model_name=os.getenv("OA_MODEL_NAME", ""),
        qwen_base_url=os.getenv("OA_QWEN_BASE_URL", ""),
        qwen_api_key=os.getenv("OA_QWEN_API_KEY", ""),
        kimi_base_url=os.getenv("OA_KIMI_BASE_URL", ""),
        kimi_api_key=os.getenv("OA_KIMI_API_KEY", ""),
        deepseek_base_url=os.getenv("OA_DEEPSEEK_BASE_URL", ""),
        deepseek_api_key=os.getenv("OA_DEEPSEEK_API_KEY", ""),
        ssh_enabled=ssh_enabled,
        ssh=ssh_cfg,
    )
