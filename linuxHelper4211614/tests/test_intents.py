from os_agent.execution import IntentPlanner


def test_greeting_maps_to_self_intro() -> None:
    planner = IntentPlanner()
    plan = planner.plan("你好", profile="debian-family")

    assert plan.intent == "greeting"
    assert plan.execute is False
    assert "凌企鹅" in plan.response_text


def test_identity_query_maps_to_self_intro() -> None:
    planner = IntentPlanner()
    plan = planner.plan("你是谁", profile="debian-family")

    assert plan.intent == "identity"
    assert plan.execute is False
    assert "远程 Linux 服务器" in plan.response_text


def test_service_restart_maps_remote_command() -> None:
    planner = IntentPlanner()
    plan = planner.plan("请重启 nginx 服务", profile="debian-family")

    assert plan.intent == "service_restart"
    assert plan.execute is True
    assert plan.needs_confirmation is True
    assert plan.command.startswith("systemctl restart nginx")


def test_zip_filename_should_not_match_ip_network_intent() -> None:
    planner = IntentPlanner()
    plan = planner.plan("压缩到1.zip", profile="debian-family")

    assert plan.intent != "network_status"
