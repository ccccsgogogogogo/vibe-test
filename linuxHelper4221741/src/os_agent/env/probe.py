from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class LinuxEnvironment:
    """目标 Linux 环境关键信息。"""

    distro_id: str = "unknown"
    pretty_name: str = "Unknown Linux"


def parse_os_release(raw: str) -> LinuxEnvironment:
    """解析 /etc/os-release 文本为结构化对象。"""

    kv = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        kv[k.strip()] = v.strip().strip('"')

    return LinuxEnvironment(
        distro_id=kv.get("ID", "unknown").lower(),
        pretty_name=kv.get("PRETTY_NAME", "Unknown Linux"),
    )


def best_practice_profile(env: LinuxEnvironment) -> str:
    """按发行版选择最佳实践画像。"""

    distro = env.distro_id
    if re.search(r"ubuntu|debian", distro):
        return "debian-family"
    if re.search(r"openeuler|centos|rhel|rocky|alma", distro):
        return "redhat-family"
    if re.search(r"arch", distro):
        return "arch-family"
    return "generic-linux"
