"""Minimal manual loop for Gemini calculator tool calls."""

from langchain.messages import HumanMessage, ToolMessage

from app.llm import get_llm
from app.tools import calculator


def main() -> None:
    tools = {calculator.name: calculator}
    llm_with_tools = get_llm().bind_tools(list(tools.values()))
    messages = [
        HumanMessage(content="What is 125 multiplied by 37? Use the calculator when necessary.")
    ]

    for _ in range(5):
        response = llm_with_tools.invoke(messages)
        messages.append(response)

        if not response.tool_calls:
            print("Final answer:", response.text)
            return

        for tool_call in response.tool_calls:
            tool_name = tool_call["name"]
            tool = tools.get(tool_name)
            if tool is None:
                raise ValueError(f"Unknown tool requested: {tool_name}")

            print("Tool requested:", tool_name)
            print("Tool arguments:", tool_call["args"])
            result = tool.invoke(tool_call["args"])
            print("Tool result:", result)
            messages.append(
                ToolMessage(content=str(result), tool_call_id=tool_call["id"])
            )

    raise RuntimeError("No final answer after 5 model responses.")


if __name__ == "__main__":
    main()
