from app.services.codex_activity import CodexActivityRecorder


def test_public_activity_uses_openai_brand_for_reasoning() -> None:
    recorder = CodexActivityRecorder()

    recorder.start_item(
        {
            "turnId": "turn_brand",
            "item": {"id": "reasoning_brand", "type": "reasoning"},
        }
    )

    assert recorder.events[0].label == "OpenAI 正在思考"
    assert recorder.events[0].role == "OpenAI"

    recorder.complete_item(
        {
            "turnId": "turn_brand",
            "item": {
                "id": "reasoning_brand",
                "type": "reasoning",
                "summary": ["已确认当前板书。"],
            },
        }
    )

    assert recorder.events[0].label == "OpenAI 已完成思考"
    assert recorder.events[0].role == "OpenAI"


def test_public_activity_uses_openai_brand_for_commentary_and_tools() -> None:
    recorder = CodexActivityRecorder()

    recorder.start_item(
        {
            "turnId": "turn_brand",
            "item": {
                "id": "commentary_brand",
                "type": "agentMessage",
                "phase": "commentary",
                "text": "正在查看板书。",
            },
        }
    )
    recorder.start_item(
        {
            "turnId": "turn_brand",
            "item": {
                "id": "command_brand",
                "type": "commandExecution",
                "command": "sed -n '1,20p' board.md",
            },
        }
    )

    assert recorder.events[0].label == "OpenAI 工作进展"
    assert recorder.events[0].role == "OpenAI"
    assert recorder.events[1].label == "运行命令"
    assert recorder.events[1].role == "OpenAI tool"
