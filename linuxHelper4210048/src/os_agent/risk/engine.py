from __future__ import annotations

import enum
import re
from dataclasses import dataclass


class RiskLevel(str, enum.Enum):
    """风险等级定义。"""

    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


@dataclass
class RiskDecision:
    """风险评估结果。"""

    level: RiskLevel
    blocked: bool
    requires_confirmation: bool
    reason: str


class RiskPolicyEngine:
    """基于正则策略的风险识别引擎。"""

    CRITICAL_PATTERNS = [
        r"\brm\s+-rf\s+/(?:\s|$)",
        r"\brm\s+-rf\s+/(?:bin|boot|dev|etc|lib|lib64|proc|root|sbin|sys|usr|var)(?:/|\s|$)",
        r"\b(?:mkfs|fdisk|parted)\b",
        r"\bdd\s+if=.*\s+of=/dev/",
        r"/etc/sudoers",
        r"(?:^|\s)>\s*/etc/(?:passwd|shadow|sudoers)\b",
        r"\bchmod\s+777\s+/(?:\s|$)",
        r":\(\)\s*\{\s*:\|:&\s*\};:",
        r"\bcurl\b.*\|\s*(?:bash|sh)\b",
    ]

    HIGH_PATTERNS = [
        r"\buserdel\b",
        r"\bshutdown\b",
        r"\breboot\b",
        r"\bkill\s+-9\b",
        r"\biptables\b",
    ]

    def evaluate(self, command: str) -> RiskDecision:
        """评估命令风险并返回处置建议。"""

        cmd = command.strip().lower()

        # 致命风险命令直接阻断。
        for pattern in self.CRITICAL_PATTERNS:
            if re.search(pattern, cmd):
                return RiskDecision(
                    level=RiskLevel.critical,
                    blocked=True,
                    requires_confirmation=False,
                    reason=f"Critical command blocked by policy: {pattern}",
                )

        # 高风险命令需要二次确认。
        for pattern in self.HIGH_PATTERNS:
            if re.search(pattern, cmd):
                return RiskDecision(
                    level=RiskLevel.high,
                    blocked=False,
                    requires_confirmation=True,
                    reason=f"High-risk command requires confirmation: {pattern}",
                )

        return RiskDecision(
            level=RiskLevel.low,
            blocked=False,
            requires_confirmation=False,
            reason="Allowed by default policy.",
        )
