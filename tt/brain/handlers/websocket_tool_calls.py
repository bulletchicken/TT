"""Handlers for function/tool call events."""

import json
import threading

from tt.brain.handlers.tools_plug import run_tool


def on_function_call_args_delta(conv, msg: dict):
    """Buffer incremental function call arguments."""
    call_id = msg["call_id"]
    delta = msg.get("delta", "")
    conv.tool_arg_buffers[call_id] = conv.tool_arg_buffers.get(call_id, "") + delta


def on_function_call_args_done(conv, msg: dict):
    """Kick off tool execution once args streaming completes."""
    call_id = msg["call_id"]
    tool_name = msg["name"]

    # Parse arguments
    args_json = conv.tool_arg_buffers.pop(call_id, "") + msg.get("arguments", "")
    try:
        parsed_args = json.loads(args_json or "{}")
    except json.JSONDecodeError:
        parsed_args = {}

    threading.Thread(
        target=_execute_tool_call,
        args=(conv, call_id, tool_name, parsed_args),
        daemon=True,
    ).start()


def _execute_tool_call(conv, call_id: str, tool_name: str, parsed_args: dict):
    """Execute tool and send result back to model."""
    if tool_name == "get_memories":
        start_turn = getattr(conv, "user_turn", 0)
        condition = getattr(conv, "user_turn_condition", None)
        if condition:
            with condition:
                condition.wait_for(
                    lambda: getattr(conv, "user_turn", 0) > start_turn
                    or not getattr(conv, "running", False),
                    timeout=1.0,
                )

        # get_memories never needs model-sent args; always build from recent user turns.
        user_messages = [
            entry.get("content", "")
            for entry in reversed(conv.log.messages)
            if entry.get("role") == "user"
        ][:3]
        combined = " ".join(reversed(user_messages)).strip()
        parsed_args = {"message": combined}

    print(f"\n🛠️  Tool: {tool_name}({parsed_args})")
    conv.log.add_tool_call(tool_name, parsed_args)

    # Execute tool
    try:
        tool_output = run_tool(tool_name, parsed_args)
    except Exception as e:
        tool_output = {"error": str(e)}

    conv.log.add_tool_result(tool_name, tool_output)

    # Send result back to model
    conv.sock.send(
        {
            "type": "conversation.item.create",
            "item": {
                "type": "function_call_output",
                "call_id": call_id,
                "output": json.dumps(tool_output),
            },
        }
    )

    # Request model to continue
    conv.sock.send(
        {
            "type": "response.create",
            "response": {"modalities": ["audio", "text"]},
        }
    )
