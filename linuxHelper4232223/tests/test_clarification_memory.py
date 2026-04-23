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


def test_compress_followup_missing_source_detected() -> None:
    assert Orchestrator._is_compress_request_missing_source("压缩到1.zip") is True
    assert Orchestrator._is_compress_request_missing_source("将 1.txt 2.txt 压缩到1.zip") is False


def test_auto_expand_compress_followup_from_memory() -> None:
    memory = [
        {
            "user_text": "把1.txt和22.txt复制到compressed_folder",
            "intent": "generic_shell",
            "command": "mkdir -p compressed_folder && cp 1.txt 22.txt compressed_folder/",
            "state": "success",
        }
    ]

    expanded = Orchestrator._auto_expand_followup_request("压缩到1.zip", memory)
    assert "compressed_folder" in expanded
    assert "1.zip" in expanded
