"""Focused tests for the manual agent tool registry and dispatch."""

import unittest
from unittest.mock import patch

from langchain.messages import AIMessage, ToolMessage

from app.agent import TOOLS, run_agent


class FakeBoundLlm:
    def __init__(self, responses):
        self.responses = list(responses)
        self.message_history = []

    def invoke(self, messages):
        self.message_history.append(list(messages))
        return self.responses.pop(0)


class FakeLlm:
    def __init__(self, responses):
        self.bound = FakeBoundLlm(responses)
        self.bound_tools = None

    def bind_tools(self, tools):
        self.bound_tools = tools
        return self.bound


class AgentTests(unittest.TestCase):
    def test_tool_registry_contains_all_project_tools(self):
        self.assertEqual(
            [tool.name for tool in TOOLS],
            ["calculator", "get_schema", "describe_table", "execute_sql"],
        )

    def test_dispatches_tool_and_returns_result_with_matching_call_id(self):
        fake_llm = FakeLlm([
            AIMessage(
                content="",
                tool_calls=[{
                    "name": "calculator",
                    "args": {"operation": "multiply", "a": 25, "b": 17},
                    "id": "calculator-call-1",
                    "type": "tool_call",
                }],
            ),
            AIMessage(content="25 multiplied by 17 is 425."),
        ])

        with patch("app.agent.get_llm", return_value=fake_llm):
            answer = run_agent("What is 25 * 17?")

        self.assertEqual(answer, "25 multiplied by 17 is 425.")
        tool_message = fake_llm.bound.message_history[1][-1]
        self.assertIsInstance(tool_message, ToolMessage)
        self.assertEqual(tool_message.content, "425.0")
        self.assertEqual(tool_message.tool_call_id, "calculator-call-1")

    def test_unknown_tool_is_returned_to_model_as_an_error(self):
        fake_llm = FakeLlm([
            AIMessage(
                content="",
                tool_calls=[{
                    "name": "unknown_tool",
                    "args": {},
                    "id": "unknown-call-1",
                    "type": "tool_call",
                }],
            ),
            AIMessage(content="I could not use that tool."),
        ])

        with patch("app.agent.get_llm", return_value=fake_llm):
            answer = run_agent("Use an unknown tool.")

        self.assertEqual(answer, "I could not use that tool.")
        tool_message = fake_llm.bound.message_history[1][-1]
        self.assertIn("Unknown tool requested: unknown_tool", tool_message.content)
        self.assertEqual(tool_message.tool_call_id, "unknown-call-1")


if __name__ == "__main__":
    unittest.main()
