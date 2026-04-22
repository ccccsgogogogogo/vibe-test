from os_agent.agent.orchestrator import Orchestrator
from os_agent.execution import LinuxCommandResult


def test_secondary_decision_permission_denied_recoverable() -> None:
    result = LinuxCommandResult(
        command="systemctl restart nginx",
        return_code=1,
        stdout="",
        stderr="permission denied",
    )

    decision, recommendation, request_text = Orchestrator._secondary_decision_after_failure(
        user_text="重启 nginx 服务",
        intent="service_restart",
        command="systemctl restart nginx",
        execution=result,
    )

    assert decision == "recoverable_failure"
    assert "权限" in recommendation
    assert "sudo" in request_text.lower() or "权限" in request_text


def test_secondary_decision_no_context_failed_no_action() -> None:
    result = LinuxCommandResult(
        command="unknown_cmd",
        return_code=2,
        stdout="",
        stderr="",
    )

    decision, recommendation, request_text = Orchestrator._secondary_decision_after_failure(
        user_text="执行未知命令",
        intent="generic_shell",
        command="unknown_cmd",
        execution=result,
    )

    assert decision == "failed_no_action"
    assert "无法" in recommendation
    assert request_text == ""
