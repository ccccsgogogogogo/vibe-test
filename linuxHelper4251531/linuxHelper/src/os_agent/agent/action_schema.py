from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Optional

ACTION_SCHEMA_VERSION = "1.0"
VALID_RISK_HINTS = {"low", "medium", "high"}


@dataclass
class ActionStep:
    exec: str
    description: str = ""
    risk_hint: str = "low"

    def __post_init__(self) -> None:
        if self.risk_hint not in VALID_RISK_HINTS:
            self.risk_hint = "low"


@dataclass
class ActionPlan:
    schema_version: str = ACTION_SCHEMA_VERSION
    plan_type: str = "unavailable"
    commands: list[ActionStep] = field(default_factory=list)
    explanation: str = ""
    abort_reason: str = ""

    @property
    def is_available(self) -> bool:
        return self.plan_type in ("single", "composite") and len(self.commands) > 0

    @property
    def is_composite(self) -> bool:
        return self.plan_type == "composite" and len(self.commands) > 1

    def to_shell_command(self, use_strict_mode: bool = True) -> str:
        if not self.commands:
            return ""
        if len(self.commands) == 1:
            return self.commands[0].exec
        lines: list[str] = []
        if use_strict_mode:
            lines.append("set -e")
        for step in self.commands:
            lines.append(step.exec)
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "plan_type": self.plan_type,
            "commands": [
                {
                    "exec": s.exec,
                    "description": s.description,
                    "risk_hint": s.risk_hint,
                }
                for s in self.commands
            ],
            "explanation": self.explanation,
            "abort_reason": self.abort_reason,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> ActionPlan:
        commands: list[ActionStep] = []
        raw_commands = payload.get("commands")
        if isinstance(raw_commands, list):
            for item in raw_commands:
                if isinstance(item, dict):
                    commands.append(ActionStep(
                        exec=str(item.get("exec", "")).strip(),
                        description=str(item.get("description", "")),
                        risk_hint=str(item.get("risk_hint", "low")),
                    ))
        return cls(
            schema_version=str(payload.get("schema_version", ACTION_SCHEMA_VERSION)),
            plan_type=str(payload.get("plan_type", "unavailable")),
            commands=commands,
            explanation=str(payload.get("explanation", "")),
            abort_reason=str(payload.get("abort_reason", "")),
        )


BUILTIN_ACTION_PLAN_PROMPT = (
    "You are a Linux shell action planner. Given a natural language request, "
    "output a strict JSON action plan.\n\n"
    "=== JSON SCHEMA (MUST follow exactly) ===\n"
    '{\n'
    '  "schema_version": "1.0",\n'
    '  "plan_type": "single" | "composite" | "unavailable",\n'
    '  "commands": [\n'
    '    {\n'
    '      "exec": "executable bash command (single line)",\n'
    '      "description": "brief human-readable description",\n'
    '      "risk_hint": "low" | "medium" | "high"\n'
    '    }\n'
    '  ],\n'
    '  "explanation": "brief explanation of what this plan does",\n'
    '  "abort_reason": "non-empty only if plan_type is unavailable"\n'
    '}\n\n'
    "=== RULES ===\n"
    "1. Output ONLY the JSON object, no markdown, no code fences, no extra text.\n"
    "2. For single-step tasks: plan_type=\"single\", one command in commands[].\n"
    "3. For multi-step composite tasks: plan_type=\"composite\", ordered commands in commands[].\n"
    "4. If the request is unclear, unsafe, or cannot be mapped to commands: plan_type=\"unavailable\", "
    "commands=[], abort_reason with explanation.\n"
    "5. Every \"exec\" string must be a valid bash command line, non-interactive.\n"
    "6. Prefer safe, read-only commands when possible.\n"
    "7. risk_hint: \"low\" for read-only/inspection, \"medium\" for state changes, "
    "\"high\" for destructive/system-wide changes.\n"
    "8. description should briefly explain what each command does.\n\n"
    "=== EXAMPLES ===\n"
    "Request: Check disk usage\n"
    '{"schema_version":"1.0","plan_type":"single","commands":[{"exec":"df -h","description":"Show disk usage","risk_hint":"low"}],'
    '"explanation":"Display filesystem disk space usage","abort_reason":""}\n\n'
    "Request: Clean system cache and remove old logs\n"
    '{"schema_version":"1.0","plan_type":"composite","commands":['
    '{"exec":"apt-get clean","description":"Clean apt cache","risk_hint":"medium"},'
    '{"exec":"journalctl --vacuum-time=7d","description":"Remove logs older than 7 days","risk_hint":"medium"}],'
    '"explanation":"Clean package cache and purge old system logs","abort_reason":""}\n\n'
    "Request: do some kung fu magic\n"
    '{"schema_version":"1.0","plan_type":"unavailable","commands":[],'
    '"explanation":"","abort_reason":"Request is ambiguous and cannot be mapped to safe shell commands"}'
)


def _extract_json_payload(text: str) -> Optional[str]:
    text = text.strip()
    if not text:
        return None

    if text.startswith("```"):
        lines = text.splitlines()
        code_lines: list[str] = []
        started = False
        for line in lines[1:]:
            if line.strip().startswith("```"):
                break
            if not started and line.strip():
                started = True
            if started:
                code_lines.append(line)
        text = "\n".join(code_lines).strip()

    if text.startswith("{") and text.endswith("}"):
        return text

    left = text.find("{")
    right = text.rfind("}")
    if left >= 0 and right > left:
        return text[left:right + 1]

    return None


def parse_action_plan(output_text: str) -> Optional[ActionPlan]:
    json_text = _extract_json_payload(output_text)
    if not json_text:
        return None

    try:
        payload = json.loads(json_text)
    except json.JSONDecodeError:
        return None

    if not isinstance(payload, dict):
        return None

    plan = ActionPlan.from_dict(payload)

    if plan.plan_type == "unavailable":
        return plan

    if not plan.commands:
        return None

    valid_commands: list[ActionStep] = []
    for step in plan.commands:
        if not step.exec:
            continue
        valid_commands.append(step)

    if not valid_commands:
        return None

    plan.commands = valid_commands
    return plan
