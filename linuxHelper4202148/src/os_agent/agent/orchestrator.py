from __future__ import annotations

from dataclasses import dataclass
from typing import List

from os_agent.config import AppConfig
from os_agent.env import best_practice_profile, parse_os_release
from os_agent.execution import IntentPlanner, LinuxCommandExecutor, LinuxCommandResult
from os_agent.models import build_model_client
from os_agent.risk import RiskDecision, RiskLevel, RiskPolicyEngine
from os_agent.logging_config import get_logger


@dataclass
class TurnResult:
    """单轮对话处理结果。"""

    user_text: str
    profile: str
    planned_intent: str
    command: str
    risk: RiskDecision
    execution: LinuxCommandResult | None
    assistant_text: str


class Orchestrator:
    """将环境探测、意图规划、风控、执行与模型总结串联为单一入口。"""

    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        self.model = build_model_client(cfg)
        self.executor = LinuxCommandExecutor(cfg.ssh)
        self.planner = IntentPlanner()
        self.risk = RiskPolicyEngine()

    def handle_turn(self, user_text: str, confirmed: bool = False) -> TurnResult:
        """处理一轮用户请求。"""
        
        logger = get_logger()
        logger.info(f"处理用户请求: {user_text}")

        try:
            # 先识别目标系统类型，保证后续命令选择更稳妥。
            os_release = self.executor.read_os_release()
            env = parse_os_release(os_release)
            profile = best_practice_profile(env)
            logger.debug(f"检测到系统配置: profile={profile}")

            # 规划意图并执行安全评估。
            plan = self.planner.plan(user_text, profile=profile)
            if plan.intent == "generic_shell" and plan.command.startswith("echo 'Intent not mapped yet"):
                plan = self._generate_generic_shell_command(user_text, profile)
            logger.info(f"意图规划完成: intent={plan.intent}, command={plan.command}")

            if not plan.execute:
                logger.info(f"对话意图无需执行: {plan.intent}, 返回预设响应")
                return TurnResult(
                    user_text=user_text,
                    profile=profile,
                    planned_intent=plan.intent,
                    command=plan.command,
                    risk=RiskDecision(
                        level=RiskLevel.low,
                        blocked=False,
                        requires_confirmation=False,
                        reason="Non-executable conversational intent.",
                    ),
                    execution=None,
                    assistant_text=plan.response_text or "",
                )

            risk_decision = self.risk.evaluate(plan.command)
            logger.info(f"风险评估完成: level={risk_decision.level.name}, blocked={risk_decision.blocked}")

            if risk_decision.blocked:
                logger.warning(
                    f"命令被阻止: {plan.command}, 原因: {risk_decision.reason}"
                )
                return TurnResult(
                    user_text=user_text,
                    profile=profile,
                    planned_intent=plan.intent,
                    command=plan.command,
                    risk=risk_decision,
                    execution=None,
                    assistant_text=(
                        f"Command blocked.\\nReason: {risk_decision.reason}\\n"
                        f"Planned command: {plan.command}"
                    ),
                )

            if (risk_decision.requires_confirmation or plan.needs_confirmation) and not confirmed:
                logger.warning(
                    f"高风险命令需要确认: {plan.command}, 原因: {risk_decision.reason}"
                )
                return TurnResult(
                    user_text=user_text,
                    profile=profile,
                    planned_intent=plan.intent,
                    command=plan.command,
                    risk=risk_decision,
                    execution=None,
                    assistant_text=(
                        "High-risk or sensitive action detected. "
                        "Please confirm to proceed.\\n"
                        f"Reason: {risk_decision.reason}\\n"
                        f"Planned command: {plan.command}"
                    ),
                )

            # 执行命令后把关键结果交给模型做解释总结。
            logger.info(f"开始执行命令: {plan.command}")
            execution = self.executor.run(plan.command)
            logger.info(
                f"命令执行完成: return_code={execution.return_code}, "
                f"stdout_len={len(execution.stdout)}, stderr_len={len(execution.stderr)}"
            )
            
            summary_prompt = [
                {
                    "role": "system",
                    "content": (
                        "You are a Linux operations assistant. Summarize command result briefly "
                        "and suggest next safe action."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"User request: {user_text}\\n"
                        f"Intent: {plan.intent}\\n"
                        f"Command: {plan.command}\\n"
                        f"Return code: {execution.return_code}\\n"
                        f"STDOUT:\\n{execution.stdout[:5000]}\\n"
                        f"STDERR:\\n{execution.stderr[:3000]}"
                    ),
                },
            ]

            chunks: List[str] = []
            for chunk in self.model.stream_chat(summary_prompt):
                chunks.append(chunk)

            # 流式分片是逐 token 返回，使用连续拼接避免每片单独换行。
            assistant_text = "".join(chunks).strip() or self._fallback_summary(execution)

            logger.info("用户请求处理完成")
            return TurnResult(
                user_text=user_text,
                profile=profile,
                planned_intent=plan.intent,
                command=plan.command,
                risk=risk_decision,
                execution=execution,
                assistant_text=assistant_text,
            )
        except Exception as e:
            logger.error(
                f"处理用户请求时发生异常: {user_text}, 错误: {str(e)}",
                exc_info=True
            )
            raise

    def _generate_generic_shell_command(self, user_text: str, profile: str) -> PlannedCommand:
        """Use the configured model to infer a safe shell command from natural language."""
        prompt = [
            {
                "role": "system",
                "content": (
                    "You are a Linux shell command generator. Given a natural language user request, "
                    "output exactly one bash command or a short bash command sequence. "
                    "Do not add explanations or markdown formatting. Do not quote the command. "
                    "If the request is unclear, unsafe, or you cannot infer a command, respond with COMMAND_NOT_FOUND. "
                    "For SSH key insertion, generate the command to append the public key into ~/.ssh/authorized_keys safely."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"User request: {user_text}\n"
                    f"Profile: {profile}\n"
                    "Generate a single executable bash command to satisfy this request."
                ),
            },
        ]

        chunks: List[str] = []
        for chunk in self.model.stream_chat(prompt):
            chunks.append(chunk)

        generated = "".join(chunks).strip()
        command = self._extract_command_from_model_output(generated)

        if not command or command == "COMMAND_NOT_FOUND":
            return PlannedCommand(
                intent="generic_shell",
                command="echo 'Intent not mapped yet. Please extend planner.'",
                execute=False,
                response_text=(
                    "无法自动将该自然语言请求映射为安全的Shell命令。"
                    " 请更明确描述操作目标或使用更具体的服务器命令。"
                ),
            )

        return PlannedCommand(intent="generic_shell", command=command)

    @staticmethod
    def _extract_command_from_model_output(output: str) -> str:
        lines = [line.strip() for line in output.splitlines() if line.strip()]
        if not lines:
            return ""
        first = lines[0]
        if first.startswith("```"):
            code_lines = []
            for line in lines[1:]:
                if line.startswith("```"):
                    break
                code_lines.append(line)
            return "\n".join(code_lines).strip()
        return first

    @staticmethod
    def _fallback_summary(result: LinuxCommandResult) -> str:
        """模型不可用时的兜底摘要。"""

        if result.return_code == 0:
            return f"Command succeeded.\\n{result.stdout[:800]}"
        return f"Command failed (code={result.return_code}).\\n{result.stderr[:800]}"
