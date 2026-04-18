from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PlannedCommand:
    """意图规划结果：包含意图名、命令与确认要求。"""

    intent: str
    command: str
    needs_confirmation: bool = False


class IntentPlanner:
    """规则优先的意图规划器，后续可替换为 LLM 规划。"""

    def plan(self, user_text: str, profile: str) -> PlannedCommand:
        """将用户请求映射到可执行命令。"""

        text = user_text.strip().lower()

        if any(k in text for k in ["disk", "storage", "space", "磁盘"]):
            if profile == "debian-family":
                return PlannedCommand(intent="disk_check", command="df -h; lsblk")
            return PlannedCommand(intent="disk_check", command="df -h; lsblk")

        if any(k in text for k in ["memory", "ram", "内存"]):
            return PlannedCommand(intent="memory_check", command="free -h; vmstat 1 3")

        if any(k in text for k in ["create user", "add user", "新增用户", "添加用户"]):
            return PlannedCommand(
                intent="user_create",
                command="echo 'Please specify username and role.'",
                needs_confirmation=True,
            )

        if any(k in text for k in ["service", "status", "服务状态"]):
            return PlannedCommand(intent="service_status", command="systemctl --type=service --state=running")

        return PlannedCommand(intent="generic_shell", command="echo 'Intent not mapped yet. Please extend planner.'")
