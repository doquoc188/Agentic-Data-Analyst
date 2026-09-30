"""Minimal manual tool-calling loop for the data analyst agent."""

import sys

from langchain.messages import HumanMessage, SystemMessage, ToolMessage

from app.llm import get_llm
from app.tools import calculator, describe_table, execute_sql, get_schema

TOOLS = [
    calculator,
    get_schema,
    describe_table,
    execute_sql,
]
TOOL_MAP = {tool.name: tool for tool in TOOLS}

SYSTEM_INSTRUCTIONS = """You are a data analyst agent.
Never invent database tables, columns, or query results.
For an unfamiliar database question, inspect the schema with get_schema.
Use describe_table when detailed table metadata is needed.
Use execute_sql to obtain real data, and never claim a database result without it.
Prefer read-only analytical SQL.
If execute_sql returns a SQL error, inspect the schema or table metadata and correct the query.
Do not expose database credentials or internal configuration.
Give the user a concise natural-language answer based on real tool results.
Choose only the tools needed for the user's question."""

DEFAULT_QUESTION = "How many rows are in the database sales data?"


def run_agent(question: str, max_iterations: int = 8) -> str:
    """Answer one question with the manual Gemini tool-calling loop."""
    llm_with_tools = get_llm().bind_tools(TOOLS)
    messages = [
        SystemMessage(content=SYSTEM_INSTRUCTIONS),
        HumanMessage(content=question),
    ]

    for _ in range(max_iterations):
        response = llm_with_tools.invoke(messages)
        messages.append(response)

        if not response.tool_calls:
            final_answer = response.text
            print("Final answer:", final_answer)
            return final_answer

        for tool_call in response.tool_calls:
            tool_name = tool_call["name"]
            tool_arguments = tool_call["args"]
            print("Tool requested:", tool_name)
            print("Tool arguments:", tool_arguments)

            selected_tool = TOOL_MAP.get(tool_name)
            if selected_tool is None:
                available_tools = ", ".join(TOOL_MAP)
                result = (
                    f"Unknown tool requested: {tool_name}. "
                    f"Available tools: {available_tools}."
                )
            else:
                result = selected_tool.invoke(tool_arguments)

            print("Tool result:", result)
            messages.append(
                ToolMessage(content=str(result), tool_call_id=tool_call["id"])
            )

    raise RuntimeError(
        f"Agent stopped after {max_iterations} model responses without a final answer."
    )


def main() -> None:
    question = " ".join(sys.argv[1:]).strip() or DEFAULT_QUESTION
    run_agent(question)


if __name__ == "__main__":
    main()
