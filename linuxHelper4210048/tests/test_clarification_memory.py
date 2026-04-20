from os_agent.agent.orchestrator import Orchestrator


def test_ambiguous_request_detection() -> None:
    assert Orchestrator._is_ambiguous_request("继续") is True
    assert Orchestrator._is_ambiguous_request("帮我查看 add.py 的内容") is False


def test_guess_request_from_memory_for_input_hint() -> None:
    memory = [
        {
            "user_text": "创建一个加法脚本并运行",
            "intent": "generic_shell",
            "command": "cat > add.py << 'EOF'\nprint('ok')\nEOF\npython3 add.py",
            "state": "success",
        }
    ]

    guessed = Orchestrator._guess_request_from_memory("你帮我直接输入两组", memory)
    assert "add.py" in guessed
    assert "标准输入" in guessed


def test_resolve_pending_intent_guess_confirm() -> None:
    orchestrator = Orchestrator.__new__(Orchestrator)
    orchestrator.pending_intent_guess = {
        "original_request": "你帮我直接输入两组",
        "guessed_request": "运行 add.py，并输入两组测试数据后返回结果",
    }

    action, resolved = Orchestrator._resolve_pending_intent_guess(orchestrator, "是")
    assert action == "confirm"
    assert "add.py" in resolved


def test_resolve_pending_intent_guess_refine() -> None:
    orchestrator = Orchestrator.__new__(Orchestrator)
    orchestrator.pending_intent_guess = {
        "original_request": "继续",
        "guessed_request": "查看文件 add.py 内容",
    }

    action, resolved = Orchestrator._resolve_pending_intent_guess(orchestrator, "不是，改成运行它")
    assert action == "refine"
    assert "补充要求" in resolved
