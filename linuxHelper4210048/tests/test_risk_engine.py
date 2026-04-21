from os_agent.risk import RiskLevel, RiskPolicyEngine


def test_block_critical_rm_rf_root() -> None:
    engine = RiskPolicyEngine()
    decision = engine.evaluate("rm -rf /")
    assert decision.level == RiskLevel.critical
    assert decision.blocked is True


def test_require_confirm_on_reboot() -> None:
    engine = RiskPolicyEngine()
    decision = engine.evaluate("reboot")
    assert decision.level == RiskLevel.high
    assert decision.requires_confirmation is True


def test_block_core_directory_delete() -> None:
    engine = RiskPolicyEngine()
    decision = engine.evaluate("rm -rf /etc")
    assert decision.level == RiskLevel.critical
    assert decision.blocked is True
