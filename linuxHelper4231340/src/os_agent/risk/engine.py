from __future__ import annotations

import enum
import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from os_agent.models.base import StreamingModelClient


class RiskLevel(str, enum.Enum):
    """风险等级定义。"""

    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


class RiskAction(str, enum.Enum):
    """基于LLM评分的处置动作。"""

    continue_execution = "continue"
    ask_user = "ask_user"
    block = "block"


@dataclass
class RiskDecision:
    """风险评估结果。"""

    level: RiskLevel
    blocked: bool
    requires_confirmation: bool
    reason: str
    llm_score: float = 0.0
    llm_reason: str = ""
    action: RiskAction = RiskAction.continue_execution


class RiskPolicyEngine:
    """基于正则策略和LLM评分的风险识别引擎。"""

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

    def __init__(self, model_client: StreamingModelClient | None = None) -> None:
        self._model = model_client

    def evaluate(self, command: str) -> RiskDecision:
        """评估命令风险并返回处置建议。"""

        cmd = command.strip().lower()

        for pattern in self.CRITICAL_PATTERNS:
            if re.search(pattern, cmd):
                return RiskDecision(
                    level=RiskLevel.critical,
                    blocked=True,
                    requires_confirmation=False,
                    reason=f"Critical command blocked by policy: {pattern}",
                    llm_score=1.0,
                    llm_reason="Policy-level critical command",
                    action=RiskAction.block,
                )

        for pattern in self.HIGH_PATTERNS:
            if re.search(pattern, cmd):
                return RiskDecision(
                    level=RiskLevel.high,
                    blocked=False,
                    requires_confirmation=True,
                    reason=f"High-risk command requires confirmation: {pattern}",
                    llm_score=0.7,
                    llm_reason="Policy-level high-risk command",
                    action=RiskAction.ask_user,
                )

        if self._model is not None:
            return self._evaluate_with_llm(command)

        return RiskDecision(
            level=RiskLevel.low,
            blocked=False,
            requires_confirmation=False,
            reason="Allowed by default policy.",
            llm_score=0.0,
            action=RiskAction.continue_execution,
        )

    def _evaluate_with_llm(self, command: str) -> RiskDecision:
        """使用LLM对命令进行危险程度评分。"""

        prompt = [
            {
                "role": "system",
                "content": (
                    "You are a Linux command risk assessment expert. "
                    "Analyze the given shell command and return a JSON object with the following fields:\n"
                    "- score: a float between 0.0 and 1.0 (0.0=safe, 1.0=extremely dangerous)\n"
                    "- level: one of 'low', 'medium', 'high', 'critical'\n"
                    "- action: one of 'continue', 'ask_user', 'block'\n"
                    "- reason: a brief explanation of the risk assessment\n\n"
                    "Scoring guidelines:\n"
                    "- 0.0-0.3: Safe read-only commands (ls, cat, ps, etc.) -> continue\n"
                    "- 0.3-0.6: Moderate risk, may modify files (touch, mkdir, echo > file) -> ask_user\n"
                    "- 0.6-0.8: High risk, system modifications (useradd, systemctl restart, package install) -> ask_user\n"
                    "- 0.8-1.0: Critical risk, destructive operations (rm -rf, format disk, modify critical configs) -> block\n\n"
                    "Return ONLY valid JSON, no other text."
                ),
            },
            {
                "role": "user",
                "content": f"Assess the risk of this command: {command}",
            },
        ]

        try:
            chunks: list[str] = []
            for chunk in self._model.stream_chat(prompt):
                chunks.append(chunk)

            response_text = "".join(chunks).strip()
            llm_result = self._parse_llm_response(response_text)

            if llm_result is None:
                return RiskDecision(
                    level=RiskLevel.low,
                    blocked=False,
                    requires_confirmation=False,
                    reason="LLM evaluation failed, allowed by default policy.",
                    llm_score=0.0,
                    llm_reason="LLM parsing failed",
                    action=RiskAction.continue_execution,
                )

            score = llm_result["score"]
            level = RiskLevel(llm_result["level"])
            action = RiskAction(llm_result["action"])
            reason = llm_result["reason"]

            blocked = action == RiskAction.block
            requires_confirmation = action == RiskAction.ask_user

            return RiskDecision(
                level=level,
                blocked=blocked,
                requires_confirmation=requires_confirmation,
                reason=f"LLM assessed: {reason}",
                llm_score=score,
                llm_reason=reason,
                action=action,
            )
        except Exception:
            return RiskDecision(
                level=RiskLevel.low,
                blocked=False,
                requires_confirmation=False,
                reason="LLM evaluation error, allowed by default policy.",
                llm_score=0.0,
                llm_reason="LLM evaluation error",
                action=RiskAction.continue_execution,
            )

    @staticmethod
    def _parse_llm_response(response: str) -> dict | None:
        """解析LLM返回的JSON响应。"""

        text = response.strip()

        if text.startswith("```"):
            lines = text.splitlines()
            code_lines: list[str] = []
            for line in lines[1:]:
                if line.strip().startswith("```"):
                    break
                code_lines.append(line)
            text = "\n".join(code_lines).strip()

        if not text.startswith("{"):
            left = text.find("{")
            right = text.rfind("}")
            if left >= 0 and right > left:
                text = text[left : right + 1]

        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return None

        if not isinstance(payload, dict):
            return None

        score = payload.get("score")
        level = payload.get("level")
        action = payload.get("action")
        reason = payload.get("reason")

        if score is None or level is None or action is None or reason is None:
            return None

        try:
            score = float(score)
            if not (0.0 <= score <= 1.0):
                return None
        except (TypeError, ValueError):
            return None

        if level not in ("low", "medium", "high", "critical"):
            return None

        if action not in ("continue", "ask_user", "block"):
            return None

        return {
            "score": score,
            "level": level,
            "action": action,
            "reason": str(reason),
        }
