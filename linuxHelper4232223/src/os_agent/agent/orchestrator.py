from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, List
from uuid import uuid4

from os_agent.config import AppConfig
from os_agent.env import best_practice_profile, parse_os_release
from os_agent.execution import IntentPlanner, LinuxCommandExecutor, LinuxCommandResult, PlannedCommand
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
    operation_plan_path: str = ""
    secondary_decision: str = "normal"
    recovery_recommendation: str = ""
    recovery_request_text: str = ""
    interaction_mode: str = "normal"


class Orchestrator:
    """将环境探测、意图规划、风控、执行与模型总结串联为单一入口。"""

    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        self.model = build_model_client(cfg)
        self.executor = LinuxCommandExecutor(cfg.ssh)
        self.planner = IntentPlanner()
        self.risk = RiskPolicyEngine(model_client=self.model)
        self.operation_runtime_dir = Path(__file__).resolve().parents[3] / "logs" / "operation_runtime"
        self.operation_runtime_dir.mkdir(parents=True, exist_ok=True)
        self.turn_memory: list[dict[str, str]] = []
        self.pending_intent_guess: dict[str, str] | None = None

    def handle_turn(
        self,
        user_text: str,
        confirmed: bool = False,
        operation_plan_path: str | None = None,
        status_callback: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> TurnResult:
        """处理一轮用户请求。"""
        
        logger = get_logger()
        raw_user_text = user_text.strip() or user_text
        logger.info(f"处理用户请求: {raw_user_text}")

        try:
            low_risk = RiskDecision(
                level=RiskLevel.low,
                blocked=False,
                requires_confirmation=False,
                reason="Allowed by default policy.",
            )

            clarification_action = "none"
            resolved_user_text = raw_user_text
            if self.pending_intent_guess is not None:
                clarification_action, resolved_user_text = self._resolve_pending_intent_guess(raw_user_text)
                self.pending_intent_guess = None

                if clarification_action == "cancel":
                    return TurnResult(
                        user_text=raw_user_text,
                        profile="",
                        planned_intent="clarification_cancelled",
                        command="",
                        risk=low_risk,
                        execution=None,
                        assistant_text=(
                            "好的，我不会按刚才的猜测继续。"
                            "请直接告诉我你希望我执行什么操作。"
                        ),
                        interaction_mode="clarification",
                    )

            if clarification_action == "none":
                auto_expanded = self._auto_expand_followup_request(raw_user_text, self.turn_memory)
                if auto_expanded:
                    resolved_user_text = auto_expanded
                    logger.info("已基于上下文自动补全请求: %s -> %s", raw_user_text, resolved_user_text)

            if clarification_action == "none" and self._is_ambiguous_request(resolved_user_text):
                guessed_request = self._guess_request_from_memory(resolved_user_text, self.turn_memory)
                if guessed_request:
                    self.pending_intent_guess = {
                        "original_request": resolved_user_text,
                        "guessed_request": guessed_request,
                    }
                    return TurnResult(
                        user_text=resolved_user_text,
                        profile="",
                        planned_intent="clarification_needed",
                        command="",
                        risk=low_risk,
                        execution=None,
                        assistant_text=(
                            "我理解到你的这条指令有些模糊。\n"
                            f"我猜你可能想要：{guessed_request}\n"
                            "如果我猜对了，请直接回复“是”或“继续”；"
                            "如果不对，请直接补充你要做的目标。"
                        ),
                        interaction_mode="clarification",
                    )

                return TurnResult(
                    user_text=resolved_user_text,
                    profile="",
                    planned_intent="clarification_needed",
                    command="",
                    risk=low_risk,
                    execution=None,
                    assistant_text=(
                        "我还不能准确判断你的目标。"
                        "请补充三个信息：操作对象、希望执行的动作、预期结果。"
                    ),
                    interaction_mode="clarification",
                )

            # 先识别目标系统类型，保证后续命令选择更稳妥。
            os_release = self.executor.read_os_release()
            env = parse_os_release(os_release)
            profile = best_practice_profile(env)
            logger.debug(f"检测到系统配置: profile={profile}")

            # 规划意图并执行安全评估。
            context_hint = self._recent_context_hint(self.turn_memory)
            plan = self.planner.plan(resolved_user_text, profile=profile)
            if plan.intent == "generic_shell" and plan.command.startswith("echo 'Intent not mapped yet"):
                plan = self._generate_generic_shell_command(resolved_user_text, profile, context_hint=context_hint)
            logger.info(f"意图规划完成: intent={plan.intent}, command={plan.command}")
            self._emit_status(
                status_callback,
                "intent_understood",
                {
                    "intent": plan.intent,
                    "message": self._build_intent_understanding_text(resolved_user_text, plan),
                },
            )

            if not plan.execute:
                logger.info(f"对话意图无需执行: {plan.intent}, 返回预设响应")
                return TurnResult(
                    user_text=resolved_user_text,
                    profile=profile,
                    planned_intent=plan.intent,
                    command=plan.command,
                    risk=low_risk,
                    execution=None,
                    assistant_text=plan.response_text or "",
                    operation_plan_path="",
                    secondary_decision="normal",
                    interaction_mode="normal",
                )

            risk_decision = self.risk.evaluate(plan.command)
            logger.info(f"风险评估完成: level={risk_decision.level.name}, blocked={risk_decision.blocked}")

            if risk_decision.blocked:
                plan_json_path = self._write_operation_plan_json(
                    user_text=resolved_user_text,
                    profile=profile,
                    plan=plan,
                    risk_decision=risk_decision,
                    status="blocked",
                )
                self._emit_status(
                    status_callback,
                    "operation_json_created",
                    {
                        "path": plan_json_path,
                        "command": plan.command,
                        "target_host": self._target_host_for_display(),
                        "status": "blocked",
                    },
                )

                logger.warning(
                    f"命令被阻止: {plan.command}, 原因: {risk_decision.reason}"
                )
                self._remember_turn_context(
                    user_text=resolved_user_text,
                    intent=plan.intent,
                    command=plan.command,
                    execution=None,
                    state="blocked",
                )
                return TurnResult(
                    user_text=resolved_user_text,
                    profile=profile,
                    planned_intent=plan.intent,
                    command=plan.command,
                    risk=risk_decision,
                    execution=None,
                    assistant_text=(
                        f"Command blocked.\\nReason: {risk_decision.reason}\\n"
                        f"Planned command: {plan.command}"
                    ),
                    operation_plan_path=plan_json_path,
                    secondary_decision="blocked",
                    interaction_mode="blocked",
                )

            if (risk_decision.requires_confirmation or plan.needs_confirmation) and not confirmed:
                plan_json_path = self._write_operation_plan_json(
                    user_text=resolved_user_text,
                    profile=profile,
                    plan=plan,
                    risk_decision=risk_decision,
                    status="awaiting_confirmation",
                )
                self._emit_status(
                    status_callback,
                    "operation_json_created",
                    {
                        "path": plan_json_path,
                        "command": plan.command,
                        "target_host": self._target_host_for_display(),
                        "status": "awaiting_confirmation",
                    },
                )
                logger.warning(
                    f"高风险命令需要确认: {plan.command}, 原因: {risk_decision.reason}"
                )
                self._remember_turn_context(
                    user_text=resolved_user_text,
                    intent=plan.intent,
                    command=plan.command,
                    execution=None,
                    state="awaiting_confirmation",
                )
                return TurnResult(
                    user_text=resolved_user_text,
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
                    operation_plan_path=plan_json_path,
                    secondary_decision="awaiting_confirmation",
                    interaction_mode="risk_confirmation",
                )

            # 将操作写入 JSON，并通过解析 JSON 回读命令执行。
            if confirmed and operation_plan_path:
                plan_json_path = operation_plan_path
                logger.info("确认后优先使用历史计划文件执行: %s", plan_json_path)
                try:
                    operation_doc = self._load_operation_plan_json(plan_json_path)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("读取历史计划失败，将重建计划文件: %s", str(exc))
                    plan_json_path = self._write_operation_plan_json(
                        user_text=resolved_user_text,
                        profile=profile,
                        plan=plan,
                        risk_decision=risk_decision,
                        status="confirmed",
                    )
                    self._emit_status(
                        status_callback,
                        "operation_json_created",
                        {
                            "path": plan_json_path,
                            "command": plan.command,
                            "target_host": self._target_host_for_display(),
                            "status": "recreated_after_load_failed",
                        },
                    )
                    operation_doc = self._load_operation_plan_json(plan_json_path)
            else:
                plan_json_path = self._write_operation_plan_json(
                    user_text=resolved_user_text,
                    profile=profile,
                    plan=plan,
                    risk_decision=risk_decision,
                    status="confirmed" if confirmed else "approved",
                )
                self._emit_status(
                    status_callback,
                    "operation_json_created",
                    {
                        "path": plan_json_path,
                        "command": plan.command,
                        "target_host": self._target_host_for_display(),
                        "status": "confirmed" if confirmed else "approved",
                    },
                )
                operation_doc = self._load_operation_plan_json(plan_json_path)

            command_to_run = self._command_from_operation_doc(operation_doc, fallback=plan.command)
            timeout_to_use = self._timeout_from_operation_doc(operation_doc, default_timeout=60)
            execution_risk = self.risk.evaluate(command_to_run)

            if execution_risk.blocked:
                logger.warning("JSON回读命令被风控阻止: %s", command_to_run)
                self._remember_turn_context(
                    user_text=resolved_user_text,
                    intent=plan.intent,
                    command=command_to_run,
                    execution=None,
                    state="blocked",
                )
                return TurnResult(
                    user_text=resolved_user_text,
                    profile=profile,
                    planned_intent=plan.intent,
                    command=command_to_run,
                    risk=execution_risk,
                    execution=None,
                    assistant_text=(
                        "计划文件中的命令被安全策略阻止，未执行。\n"
                        f"Reason: {execution_risk.reason}\n"
                        f"Planned command: {command_to_run}"
                    ),
                    operation_plan_path=plan_json_path,
                    secondary_decision="blocked",
                    interaction_mode="blocked",
                )

            if execution_risk.requires_confirmation and not confirmed:
                logger.warning("JSON回读命令需要确认: %s", command_to_run)
                self._remember_turn_context(
                    user_text=resolved_user_text,
                    intent=plan.intent,
                    command=command_to_run,
                    execution=None,
                    state="awaiting_confirmation",
                )
                return TurnResult(
                    user_text=resolved_user_text,
                    profile=profile,
                    planned_intent=plan.intent,
                    command=command_to_run,
                    risk=execution_risk,
                    execution=None,
                    assistant_text=(
                        "计划文件中的命令属于高风险操作，请确认后再执行。\n"
                        f"Reason: {execution_risk.reason}\n"
                        f"Planned command: {command_to_run}"
                    ),
                    operation_plan_path=plan_json_path,
                    secondary_decision="awaiting_confirmation",
                    interaction_mode="risk_confirmation",
                )

            logger.info(f"开始执行命令: {command_to_run}")
            execution = self.executor.run(command_to_run, timeout=timeout_to_use)
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
                        f"User request: {resolved_user_text}\\n"
                        f"Intent: {plan.intent}\\n"
                        f"Command: {command_to_run}\\n"
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
            if self._should_append_raw_output(resolved_user_text, command_to_run, execution):
                raw_output = self._build_raw_output_excerpt(execution)
                if raw_output:
                    assistant_text = f"{assistant_text}\n\n命令原始输出（节选）：\n{raw_output}"
            recovery_recommendation = ""
            recovery_request_text = ""
            secondary_decision = "normal"

            if execution.return_code != 0:
                secondary_decision, recovery_recommendation, recovery_request_text = self._secondary_decision_after_failure(
                    user_text=resolved_user_text,
                    intent=plan.intent,
                    command=command_to_run,
                    execution=execution,
                )

            self._emit_status(
                status_callback,
                "execution_result_ready",
                {
                    "summary": self._simple_result_summary(execution),
                    "return_code": execution.return_code,
                    "secondary_decision": secondary_decision,
                    "recovery_recommendation": recovery_recommendation,
                    "recovery_request_text": recovery_request_text,
                },
            )

            logger.info("用户请求处理完成")
            self._remember_turn_context(
                user_text=resolved_user_text,
                intent=plan.intent,
                command=command_to_run,
                execution=execution,
                state="success" if execution.return_code == 0 else "failed",
            )
            return TurnResult(
                user_text=resolved_user_text,
                profile=profile,
                planned_intent=plan.intent,
                command=command_to_run,
                risk=execution_risk,
                execution=execution,
                assistant_text=assistant_text,
                operation_plan_path=plan_json_path,
                secondary_decision=secondary_decision,
                recovery_recommendation=recovery_recommendation,
                recovery_request_text=recovery_request_text,
                interaction_mode="normal",
            )
        except Exception as e:
            logger.error(
                f"处理用户请求时发生异常: {raw_user_text}, 错误: {str(e)}",
                exc_info=True
            )
            raise

    def _emit_status(
        self,
        status_callback: Callable[[str, dict[str, Any]], None] | None,
        event: str,
        payload: dict[str, Any],
    ) -> None:
        """向 UI 派发阶段性状态事件；回调异常不影响主流程。"""
        if status_callback is None:
            return
        try:
            status_callback(event, payload)
        except Exception:  # noqa: BLE001
            get_logger().warning("状态回调触发失败: event=%s", event, exc_info=True)

    def _target_host_for_display(self) -> str:
        if self.cfg.ssh and self.cfg.ssh.host:
            return self.cfg.ssh.host
        return "localhost"

    def _write_operation_plan_json(
        self,
        user_text: str,
        profile: str,
        plan: PlannedCommand,
        risk_decision: RiskDecision,
        status: str,
    ) -> str:
        """将本轮计划固化为 JSON，供确认与回放执行复用。"""
        operation_id = f"op-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid4().hex[:8]}"
        target_mode = "ssh" if self.cfg.ssh and self.cfg.ssh.host else "local"
        target_port = self.cfg.ssh.port if self.cfg.ssh else None

        payload = {
            "schema_version": "1.0",
            "operation_id": operation_id,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "request": {
                "user_text": user_text,
                "profile": profile,
                "intent": plan.intent,
            },
            "target": {
                "mode": target_mode,
                "host": self._target_host_for_display(),
                "port": target_port,
            },
            "operation": {
                "command": plan.command,
                "timeout_seconds": 60,
                "requires_confirmation": bool(risk_decision.requires_confirmation or plan.needs_confirmation),
            },
            "risk": {
                "level": risk_decision.level.name,
                "blocked": risk_decision.blocked,
                "requires_confirmation": risk_decision.requires_confirmation,
                "reason": risk_decision.reason,
                "llm_score": risk_decision.llm_score,
                "llm_reason": risk_decision.llm_reason,
                "action": risk_decision.action.value,
            },
            "status": status,
        }

        output_path = self.operation_runtime_dir / f"operation_plan_{operation_id}.json"
        with open(output_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        return str(output_path)

    def _load_operation_plan_json(self, plan_path: str) -> dict[str, Any]:
        """读取并校验计划文件的最小结构，避免空命令被执行。"""
        with open(plan_path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)

        if not isinstance(payload, dict):
            raise ValueError("计划文件格式错误：根节点必须是对象")

        operation = payload.get("operation")
        if not isinstance(operation, dict):
            raise ValueError("计划文件格式错误：缺少 operation 字段")

        command = str(operation.get("command", "")).strip()
        if not command:
            raise ValueError("计划文件格式错误：operation.command 不能为空")

        return payload

    @staticmethod
    def _command_from_operation_doc(operation_doc: dict[str, Any], fallback: str) -> str:
        operation = operation_doc.get("operation")
        if isinstance(operation, dict):
            command = str(operation.get("command", "")).strip()
            if command:
                return command
        return fallback

    @staticmethod
    def _timeout_from_operation_doc(operation_doc: dict[str, Any], default_timeout: int) -> int:
        operation = operation_doc.get("operation")
        if isinstance(operation, dict):
            try:
                timeout = int(operation.get("timeout_seconds", default_timeout))
                if timeout > 0:
                    return timeout
            except (TypeError, ValueError):
                return default_timeout
        return default_timeout

    @staticmethod
    def _build_intent_understanding_text(user_text: str, plan: PlannedCommand) -> str:
        if not plan.execute:
            if plan.intent == "generic_shell":
                return (
                    f"我理解你的意思是：{user_text}。\n"
                    f"识别意图：{plan.intent}。\n"
                    "这是一个执行类请求，但当前未生成可靠可执行命令，已转为安全回退响应。"
                )
            return f"我理解你的意思是：{user_text}。\n这是一个对话类请求（{plan.intent}），不需要执行系统命令。"
        return (
            f"我理解你的意思是：{user_text}。\n"
            f"识别意图：{plan.intent}。\n"
            f"计划执行命令：{plan.command}"
        )

    @staticmethod
    def _simple_result_summary(result: LinuxCommandResult) -> str:
        if result.return_code == 0:
            short = (result.stdout or "命令执行成功。")[:120].strip()
            return f"执行成功，返回码 0。{short}"
        err_short = (result.stderr or "").strip()[:120]
        out_short = (result.stdout or "").strip()[:120]
        if err_short and out_short:
            return f"执行失败，返回码 {result.return_code}。错误：{err_short}；但命令有输出：{out_short}"
        if err_short:
            return f"执行失败，返回码 {result.return_code}。{err_short}"
        if out_short:
            return f"执行失败，返回码 {result.return_code}。命令有输出：{out_short}"
        return f"执行失败，返回码 {result.return_code}。"

    @staticmethod
    def _should_append_raw_output(
        user_text: str,
        command: str,
        execution: LinuxCommandResult,
    ) -> bool:
        stdout_text = (execution.stdout or "").strip()
        stderr_text = (execution.stderr or "").strip()
        if not stdout_text and not stderr_text:
            return False

        # 失败但有输出时优先展示，避免关键信息（如已找到的文件）丢失。
        if execution.return_code != 0 and stdout_text:
            return True

        text = user_text.lower()
        cmd = command.lower()
        request_list_keywords = [
            "列举",
            "列出",
            "列表",
            "展示",
            "显示",
            "清单",
            "list",
            "show",
        ]
        command_list_tokens = ["find ", " ls", "grep ", "locate ", "fd ", "tree "]

        asked_for_listing = any(token in text for token in request_list_keywords)
        listing_command = any(token in f" {cmd} " for token in command_list_tokens)
        return bool(stdout_text and (asked_for_listing or listing_command))

    @staticmethod
    def _build_raw_output_excerpt(result: LinuxCommandResult) -> str:
        max_lines = 60
        max_chars = 3000

        def _clip(content: str) -> str:
            lines = content.splitlines()
            excerpt = "\n".join(lines[:max_lines])
            clipped = False
            if len(lines) > max_lines:
                clipped = True
            if len(excerpt) > max_chars:
                excerpt = excerpt[:max_chars]
                clipped = True
            if clipped:
                excerpt += "\n... [输出过长，已截断]"
            return excerpt.strip()

        sections: list[str] = []
        stdout_text = (result.stdout or "").strip()
        stderr_text = (result.stderr or "").strip()

        if stdout_text:
            sections.append("stdout:\n" + _clip(stdout_text))
        if stderr_text:
            sections.append("stderr:\n" + _clip(stderr_text))

        return "\n\n".join(section for section in sections if section.strip())

    @staticmethod
    def _secondary_decision_after_failure(
        user_text: str,
        intent: str,
        command: str,
        execution: LinuxCommandResult,
    ) -> tuple[str, str, str]:
        """执行失败后的二次决策：判断是否可继续处理并生成推荐操作。"""

        stderr_l = execution.stderr.lower()
        stdout_l = execution.stdout.lower()
        merged = f"{stderr_l}\n{stdout_l}"

        if "permission denied" in merged:
            return (
                "recoverable_failure",
                "检测到权限不足导致执行失败。建议先确认当前用户权限，再使用 sudo 或具备权限的账号执行。",
                f"请先检查当前用户是否有 sudo 权限，再使用高权限方式重试：{user_text}",
            )

        if "command not found" in merged:
            first_token = command.split()[0] if command.split() else "相关命令"
            return (
                "recoverable_failure",
                f"检测到命令不存在（{first_token}）。建议先安装缺失工具后再重试。",
                f"请安装 {first_token} 并验证可用后，重新执行：{user_text}",
            )

        if "no such file or directory" in merged:
            return (
                "recoverable_failure",
                "检测到目标文件或目录不存在。建议先检查路径是否正确，再重试。",
                f"请先校验目标路径和资源是否存在，再执行：{user_text}",
            )

        if "connection timed out" in merged or "network is unreachable" in merged or "temporary failure in name resolution" in merged:
            return (
                "recoverable_failure",
                "检测到网络连通性异常。建议先修复网络/DNS问题，再重试。",
                f"请先检查网络连通性与DNS解析，恢复后重试：{user_text}",
            )

        if "service not found" in merged or "unit" in merged and "not found" in merged:
            return (
                "recoverable_failure",
                "检测到服务名不存在或拼写错误。建议先列出服务并确认名称后再执行。",
                f"请先列出并确认服务名称，再执行这类操作：{intent}",
            )

        if execution.stderr.strip() or execution.stdout.strip():
            return (
                "recoverable_failure",
                "命令执行失败，但存在可分析的错误输出。建议先按错误信息修复环境后再次执行。",
                f"请先根据报错信息修复问题后，再重试：{user_text}",
            )

        return (
            "failed_no_action",
            "命令执行失败且缺少可用错误上下文，暂无法给出安全的自动续处理建议。",
            "",
        )

    def _generate_generic_shell_command(self, user_text: str, profile: str, context_hint: str = "") -> PlannedCommand:
        """Use the configured model to infer safe shell commands from natural language."""

        command = self._generate_shell_command_with_model(user_text, profile, context_hint=context_hint)

        # 复合任务失败时，尝试走结构化多步骤兜底生成。
        if (not command or command == "COMMAND_NOT_FOUND") and self._is_composite_request(user_text):
            command = self._generate_composite_shell_script(user_text, profile, context_hint=context_hint)

        if not command or command == "COMMAND_NOT_FOUND":
            return PlannedCommand(
                intent="generic_shell",
                command="echo 'Intent not mapped yet. Please extend planner.'",
                execute=False,
                response_text=(
                    "无法自动将该自然语言请求映射为安全的Shell命令。"
                    " 请更明确描述操作目标，或拆分为两到三步子任务后重试。"
                ),
            )

        return PlannedCommand(intent="generic_shell", command=command)

    def _generate_shell_command_with_model(self, user_text: str, profile: str, context_hint: str = "") -> str:
        prompt = [
            {
                "role": "system",
                "content": (
                    "You are a Linux shell command generator. Given a natural language user request, "
                    "output executable bash commands only. "
                    "For single tasks output one command; for compound tasks output a full multi-line command sequence. "
                    "Prefer non-interactive execution. If a program requires input(), use printf/echo pipes to feed sample input. "
                    "Do not add explanations, titles, or markdown formatting. "
                    "If the request is unclear, unsafe, or you cannot infer a command, respond with COMMAND_NOT_FOUND. "
                    "For SSH key insertion, generate the command to append the public key into ~/.ssh/authorized_keys safely. "
                    "Common tasks: cleaning system cache (apt clean, journalctl --vacuum-time=7d), "
                    "optimizing permissions (chmod, chown), checking disk usage (du -sh /*), "
                    "removing old logs, clearing temp files (/tmp), etc."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"User request: {user_text}\n"
                    f"Profile: {profile}\n"
                    f"Recent context:\n{context_hint or 'N/A'}\n"
                    "Generate a single executable bash command to satisfy this request."
                ),
            },
        ]

        chunks: List[str] = []
        for chunk in self.model.stream_chat(prompt):
            chunks.append(chunk)

        generated = "".join(chunks).strip()
        return self._extract_command_from_model_output(generated)

    def _generate_composite_shell_script(self, user_text: str, profile: str, context_hint: str = "") -> str:
        """复合任务兜底：要求模型返回结构化步骤，再拼接为可执行脚本。"""
        prompt = [
            {
                "role": "system",
                "content": (
                    "You generate Linux shell steps for compound tasks. "
                    "Return strict JSON only with format: {\"steps\":[{\"command\":\"...\"}]}. "
                    "Each command must be executable on bash and should be a single line. "
                    "No markdown, no comments, no extra fields. "
                    "If cannot generate safely, return {\"steps\":[]}.\n\n"
                    "Common compound tasks examples:\n"
                    "- Clean system: apt-get clean, journalctl --vacuum-time=7d, rm -rf /tmp/*\n"
                    "- Optimize permissions: chmod 755 /, chown root:root /\n"
                    "- System maintenance: apt-get autoremove -y, apt-get autoclean, dpkg --configure -a\n"
                    "- Log cleanup: find /var/log -name '*.gz' -delete, journalctl --vacuum-size=100M\n"
                    "- Disk optimization: fstrim -av, sync\n"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"User request: {user_text}\n"
                    f"Profile: {profile}\n"
                    f"Recent context:\n{context_hint or 'N/A'}\n"
                    "Generate steps for this compound task."
                ),
            },
        ]

        chunks: List[str] = []
        for chunk in self.model.stream_chat(prompt):
            chunks.append(chunk)

        generated = "".join(chunks).strip()
        commands = self._extract_command_list_from_json_output(generated)
        if not commands:
            return ""

        script_lines = ["set -e"] + commands
        return "\n".join(script_lines)

    @staticmethod
    def _extract_command_from_model_output(output: str) -> str:
        """从模型输出中提取可执行命令，兼容 JSON/代码块/混合文本。"""
        text = output.strip()
        if not text:
            return ""

        if "COMMAND_NOT_FOUND" in text.upper():
            return "COMMAND_NOT_FOUND"

        # 优先解析 JSON 格式输出：{"command":"..."}
        if text.startswith("{") and text.endswith("}"):
            try:
                payload = json.loads(text)
                if isinstance(payload, dict):
                    command = str(payload.get("command", "")).strip()
                    if command:
                        return command
            except json.JSONDecodeError:
                pass

        if text.startswith("```"):
            lines = text.splitlines()
            code_lines: list[str] = []
            for line in lines[1:]:
                if line.strip().startswith("```"):
                    break
                code_lines.append(line)
            text = "\n".join(code_lines).strip()

        # 去掉常见前缀（如 "Command:"）
        cleaned_lines: list[str] = []
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue

            line = re.sub(r"^[-*]\s+", "", line)
            line = re.sub(r"^\d+[\.)]\s+", "", line)

            if re.match(r"^(command|bash|shell)\s*:\s*", line, re.I):
                line = re.sub(r"^(command|bash|shell)\s*:\s*", "", line, flags=re.I).strip()

            cleaned_lines.append(line)

        if not cleaned_lines:
            return ""

        # here-doc 需要完整保留中间内容与结束标记，不能按“命令行”过滤。
        if any("<<" in line for line in cleaned_lines):
            return "\n".join(cleaned_lines)

        if len(cleaned_lines) == 1:
            return cleaned_lines[0] if Orchestrator._looks_like_shell_command(cleaned_lines[0]) else ""

        shell_like_lines = [line for line in cleaned_lines if Orchestrator._looks_like_shell_command(line)]
        if len(shell_like_lines) == len(cleaned_lines):
            return "\n".join(cleaned_lines)

        # 多行文本里混有解释时，只提取可执行行。
        if shell_like_lines:
            return "\n".join(shell_like_lines)

        return ""

    @staticmethod
    def _extract_command_list_from_json_output(output: str) -> list[str]:
        """从结构化 JSON 输出中提取步骤命令列表。"""
        text = output.strip()
        if not text:
            return []

        if text.startswith("```"):
            lines = text.splitlines()
            code_lines: list[str] = []
            for line in lines[1:]:
                if line.strip().startswith("```"):
                    break
                code_lines.append(line)
            text = "\n".join(code_lines).strip()

        # 允许输出前后有噪声，尝试提取最外层 JSON。
        if not text.startswith("{"):
            left = text.find("{")
            right = text.rfind("}")
            if left >= 0 and right > left:
                text = text[left:right + 1]

        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return []

        if not isinstance(payload, dict):
            return []

        steps = payload.get("steps")
        if not isinstance(steps, list):
            return []

        commands: list[str] = []
        for step in steps:
            if isinstance(step, dict):
                command = str(step.get("command", "")).strip()
            elif isinstance(step, str):
                command = step.strip()
            else:
                command = ""

            if command and Orchestrator._looks_like_shell_command(command):
                commands.append(command)

        return commands

    @staticmethod
    def _looks_like_shell_command(line: str) -> bool:
        """启发式判断一行文本是否像 shell 命令。"""
        stripped = line.strip()
        if not stripped:
            return False

        control_keywords = {"then", "do", "done", "fi", "else"}
        if stripped in control_keywords:
            return True

        if stripped.startswith(("./", "~/", "/")):
            return True

        common_prefixes = (
            "ls", "cat", "echo", "touch", "mkdir", "rm", "cp", "mv", "python", "python3",
            "pip", "pip3", "apt", "apt-get", "yum", "dnf", "systemctl", "service", "journalctl",
            "grep", "sed", "awk", "find", "chmod", "chown", "tar", "zip", "unzip", "curl",
            "wget", "ss", "ip", "ps", "top", "free", "df", "uname", "whoami", "pwd", "cd",
            "head", "tail", "tee", "nohup", "bash", "sh", "xdg-open", "vi", "vim", "nano",
            "export", "source", "printf",
        )
        if stripped.startswith(common_prefixes):
            return True

        # 支持变量赋值与常见 shell 连接符。
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", stripped):
            return True

        if any(token in stripped for token in ["&&", "||", "|", ";", ">", "<", "$(", "`"]):
            return True

        return False

    @staticmethod
    def _is_composite_request(user_text: str) -> bool:
        """粗略识别"多动作复合任务"，用于触发多步骤兜底生成。"""
        text = user_text.lower()
        connectors = [
            "并", "然后", "再", "后", "之后", "同时", "且", "and", "then",
            "顺便", "另外", "还有", "以及", "接着", "随后", "之后", "也",
            "，", ",", "，然后", "，再", "，顺便",
        ]
        action_keywords = [
            "创建", "新建", "写", "填入", "保存", "运行", "执行", "打开", "查看", "输出", "告诉我",
            "create", "write", "save", "run", "execute", "open", "read", "show",
            "清理", "清除", "删除", "优化", "修改", "更改", "设置", "配置", "安装", "卸载",
            "重启", "启动", "停止", "检查", "检测", "分析", "修复", "更新", "升级",
            "clean", "clear", "remove", "delete", "optimize", "modify", "change", "set",
            "configure", "install", "uninstall", "restart", "start", "stop", "check",
            "fix", "update", "upgrade", "垃圾", "缓存", "权限", "目录", "文件",
        ]

        connector_hit = any(token in text for token in connectors)
        action_hits = sum(1 for token in action_keywords if token in text)
        return connector_hit and action_hits >= 2

    @staticmethod
    def _is_affirmative_text(text: str) -> bool:
        """判断用户是否在做肯定确认（用于澄清/风险确认分支）。"""
        normalized = text.strip().lower()
        tokens = {
            "是", "对", "没错", "确认", "继续", "可以", "好", "好的", "行", "嗯", "嗯嗯",
            "yes", "y", "ok", "okay", "sure", "continue",
        }
        return normalized in tokens

    @staticmethod
    def _is_negative_text(text: str) -> bool:
        """判断用户是否在做否定/取消确认。"""
        normalized = text.strip().lower()
        tokens = {
            "不", "不是", "不要", "取消", "不用", "否", "算了", "no", "n", "cancel", "stop",
        }
        return normalized in tokens

    @staticmethod
    def _is_ambiguous_request(text: str) -> bool:
        """识别是否为信息不足的模糊请求，决定是否先进入澄清。"""
        normalized = text.strip().lower()
        if not normalized:
            return True

        if len(normalized) <= 2:
            return True

        vague_tokens = ["这个", "那个", "它", "继续", "再来", "同样", "刚才那个", "上一个", "上一条", "然后呢"]
        action_tokens = [
            "创建", "新建", "运行", "执行", "打开", "查看", "删除", "修改", "输入", "重试", "安装",
            "create", "run", "execute", "open", "read", "delete", "modify", "input", "retry", "install",
        ]

        has_vague = any(token in normalized for token in vague_tokens)
        has_action = any(token in normalized for token in action_tokens)
        has_file = bool(re.search(r"[\w.-]+\.[a-zA-Z0-9]{1,6}", normalized))

        if has_vague and not has_action:
            return True
        if has_vague and has_action and not has_file and len(normalized) < 18:
            return True
        if Orchestrator._is_compress_request_missing_source(normalized):
            return True
        return False

    @staticmethod
    def _extract_file_candidates(text: str) -> list[str]:
        return re.findall(r"([A-Za-z0-9_./-]+\.[A-Za-z0-9]{1,6})", text)

    @staticmethod
    def _guess_request_from_memory(user_text: str, memory: list[dict[str, str]]) -> str:
        """结合最近上下文对模糊请求做高置信度猜测。"""
        if not memory:
            return ""

        normalized = user_text.lower()
        last = memory[-1]
        last_user = last.get("user_text", "")
        last_command = last.get("command", "")

        compression_guess = Orchestrator._build_compression_followup_guess(normalized, last_user, last_command)
        if compression_guess:
            return compression_guess

        file_candidates = Orchestrator._extract_file_candidates(user_text)
        if not file_candidates:
            file_candidates = Orchestrator._extract_file_candidates(last_command)
        if not file_candidates:
            file_candidates = Orchestrator._extract_file_candidates(last_user)

        if ("输入" in normalized or "样例" in normalized) and file_candidates:
            py_files = [f for f in file_candidates if f.endswith(".py")]
            if py_files:
                return f"运行 {py_files[0]}，通过标准输入提供两组测试数据，并返回输出结果"

        if any(token in normalized for token in ["打开", "查看", "读", "内容"]) and file_candidates:
            return f"查看文件 {file_candidates[0]} 的内容并返回关键结果"

        if any(token in normalized for token in ["继续", "再来", "同样", "刚才", "那个"]):
            if last_user:
                return f"继续处理上一项任务：{last_user}"

        return ""

    @staticmethod
    def _auto_expand_followup_request(user_text: str, memory: list[dict[str, str]]) -> str:
        """对高置信度短句做自动上下文补全，减少多轮对话中断。"""
        if not memory:
            return ""

        normalized = user_text.strip().lower()
        if not Orchestrator._is_compress_request_missing_source(normalized):
            return ""

        last = memory[-1]
        last_user = last.get("user_text", "")
        last_command = last.get("command", "")
        return Orchestrator._build_compression_followup_guess(normalized, last_user, last_command)

    @staticmethod
    def _is_compress_request_missing_source(text: str) -> bool:
        compress_tokens = ["压缩", "打包", "归档", "zip", "tar"]
        if not any(token in text for token in compress_tokens):
            return False

        archive_target = Orchestrator._extract_archive_target(text)
        if not archive_target:
            return False

        # 只出现目标压缩包，且未明确给出来源对象时，视作依赖上下文的续接请求。
        file_candidates = Orchestrator._extract_file_candidates(text)
        non_target_files = [
            candidate for candidate in file_candidates
            if candidate.lower() != archive_target.lower()
        ]
        has_source_hint = any(token in text for token in ["把", "将", "从", "目录", "文件夹", "文件", "source"])
        return (not non_target_files) and (not has_source_hint)

    @staticmethod
    def _extract_archive_target(text: str) -> str:
        pattern = r"([A-Za-z0-9_./-]+\.(?:zip|tar|tgz|tbz2|txz|tar\.gz|tar\.bz2|tar\.xz))"
        matches = re.findall(pattern, text, flags=re.IGNORECASE)
        return matches[-1] if matches else ""

    @staticmethod
    def _extract_directory_candidates(text: str) -> list[str]:
        candidates: list[str] = []

        for matched in re.findall(r"\bmkdir(?:\s+-p)?\s+([A-Za-z0-9_./-]+)", text):
            cleaned = matched.strip().rstrip("/")
            if cleaned and cleaned not in candidates:
                candidates.append(cleaned)

        for matched in re.findall(r"([A-Za-z0-9_./-]+/)", text):
            cleaned = matched.strip().rstrip("/")
            if cleaned and cleaned not in candidates:
                candidates.append(cleaned)

        return candidates

    @staticmethod
    def _build_compression_followup_guess(user_text: str, last_user: str, last_command: str) -> str:
        archive_target = Orchestrator._extract_archive_target(user_text)
        if not archive_target:
            return ""

        directories = Orchestrator._extract_directory_candidates(f"{last_user}\n{last_command}")
        file_candidates: list[str] = []
        for source_text in [last_command, last_user]:
            for candidate in Orchestrator._extract_file_candidates(source_text):
                if candidate not in file_candidates and candidate.lower() != archive_target.lower():
                    file_candidates.append(candidate)

        if directories:
            source = directories[-1]
            return f"基于上一项任务结果，将 {source} 压缩为 {archive_target}"

        if len(file_candidates) >= 2:
            source = " ".join(file_candidates[:4])
            return f"基于上一项任务结果，将 {source} 压缩为 {archive_target}"

        if len(file_candidates) == 1:
            return f"基于上一项任务结果，将 {file_candidates[0]} 压缩为 {archive_target}"

        return f"继续上一项任务，并将产物压缩为 {archive_target}"

    @staticmethod
    def _recent_context_hint(memory: list[dict[str, str]], limit: int = 4) -> str:
        if not memory:
            return ""

        rows: list[str] = []
        for idx, item in enumerate(memory[-limit:], start=1):
            rows.append(
                f"[{idx}] user={item.get('user_text', '')}; intent={item.get('intent', '')}; "
                f"command={item.get('command', '')}; state={item.get('state', '')}"
            )
        return "\n".join(rows)

    def _resolve_pending_intent_guess(self, user_text: str) -> tuple[str, str]:
        """处理用户对“猜你想做什么”提示的反馈。"""
        pending = self.pending_intent_guess
        if pending is None:
            return "none", user_text

        guessed = pending.get("guessed_request", "").strip()
        if self._is_affirmative_text(user_text):
            return "confirm", guessed or user_text
        if self._is_negative_text(user_text):
            return "cancel", ""

        if guessed:
            return "refine", f"{guessed}。补充要求：{user_text}"
        return "refine", user_text

    def _remember_turn_context(
        self,
        user_text: str,
        intent: str,
        command: str,
        execution: LinuxCommandResult | None,
        state: str,
    ) -> None:
        """记录最近轮次上下文，为后续澄清与补全提供记忆。"""
        memory_item = {
            "user_text": user_text.strip(),
            "intent": intent.strip(),
            "command": command.strip(),
            "state": state,
        }

        if execution is not None:
            memory_item["return_code"] = str(execution.return_code)

        self.turn_memory.append(memory_item)
        if len(self.turn_memory) > 20:
            self.turn_memory = self.turn_memory[-20:]

    @staticmethod
    def _fallback_summary(result: LinuxCommandResult) -> str:
        """模型不可用时的兜底摘要。"""

        if result.return_code == 0:
            return f"Command succeeded.\\n{result.stdout[:800]}"
        return f"Command failed (code={result.return_code}).\\n{result.stderr[:800]}"
