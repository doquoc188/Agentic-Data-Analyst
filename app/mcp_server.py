"""Local stdio MCP interface over the existing read-only LangChain tools."""

import argparse
import json
import logging

import anyio
import jsonschema
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from mcp.types import CallToolResult, TextContent, Tool, ToolAnnotations

from app.config import ConfigurationError, get_settings
from app.database import DatabaseUnavailableError, database_context
from app.profiles import DATABASE_PROFILES, PUBLIC_DATABASES, resolve_profile
from app.tools import describe_table, execute_sql, get_schema
from app.trace import safe_trace_value

server = Server("agentic-data-analyst", version="5.1")
DATABASE_TOOLS = {item.name: item for item in (get_schema, describe_table, execute_sql)}


def tool_definitions() -> list[Tool]:
    """Reuse LangChain argument schemas, adding the explicit profile selector."""
    definitions = [Tool(
        name="list_database_profiles",
        description="List supported synthetic datasets and safe display metadata; no connection check.",
        inputSchema={"type": "object", "properties": {}, "additionalProperties": False},
        annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False),
    )]
    for name, tool in DATABASE_TOOLS.items():
        schema = tool.get_input_schema().model_json_schema()
        schema["properties"]["profile"] = {
            "type": "string", "enum": list(DATABASE_PROFILES),
            "description": "Required dataset ID; connection settings are resolved privately.",
        }
        schema["required"] = ["profile", *schema.get("required", [])]
        schema["additionalProperties"] = False
        definitions.append(Tool(
            name=name, description=tool.description, inputSchema=schema,
            annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False),
        ))
    return definitions


@server.list_tools()
async def list_tools() -> list[Tool]:
    return tool_definitions()


def tool_result(text: str, *, error: bool = False) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=safe_trace_value(text))], isError=error,
    )


def invoke_database_tool(name: str, arguments: dict) -> CallToolResult:
    """Scope the existing connection target and invoke the unchanged tool."""
    try:
        target = resolve_profile(arguments["profile"], get_settings())
        tool_arguments = {key: value for key, value in arguments.items() if key != "profile"}
        with database_context(target.database_name, database_url=target.url):
            text = DATABASE_TOOLS[name].invoke(tool_arguments)
        if name == "describe_table" and text.endswith("was not found in the public schema."):
            return tool_result("Table was not found in the public schema. Use get_schema to discover tables.", error=True)
        error = name == "execute_sql" and text.startswith(("Query rejected:", "SQL execution error:"))
        return tool_result(text, error=error)
    except ConfigurationError:
        return tool_result("Database configuration is missing or invalid.", error=True)
    except DatabaseUnavailableError:
        return tool_result("The database is unavailable.", error=True)
    except Exception:
        # Never serialize exception text, raw arguments, or a traceback.
        return tool_result("The database tool could not be completed.", error=True)


# SDK validation messages can echo invalid values. Validate the same advertised
# schemas here, returning fixed messages instead of raw validation exceptions.
@server.call_tool(validate_input=False)
async def call_tool(name: str, arguments: dict | None) -> CallToolResult:
    definitions = {tool.name: tool for tool in tool_definitions()}
    if name not in definitions:
        return tool_result("Unknown tool. Use tools/list to discover available tools.", error=True)
    arguments = {} if arguments is None else arguments
    if isinstance(arguments, dict) and isinstance(arguments.get("profile"), str):
        if arguments["profile"] not in DATABASE_PROFILES:
            return tool_result("Unknown database profile. Choose sales or saas.", error=True)
    try:
        jsonschema.validate(arguments, definitions[name].inputSchema)
    except jsonschema.ValidationError:
        return tool_result(
            "Invalid tool arguments. Supply the required fields with their declared types; extra fields are not allowed.",
            error=True,
        )
    if name == "list_database_profiles":
        return tool_result(json.dumps(PUBLIC_DATABASES))
    # Psycopg and LangChain's invoke are synchronous; keep protocol IO responsive.
    return await anyio.to_thread.run_sync(invoke_database_tool, name, arguments)


async def run_stdio() -> None:
    # The SDK can log raw malformed protocol messages. Disable logging in this
    # dedicated process; safe tool failures are returned through MCP instead.
    logging.disable(logging.CRITICAL)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local read-only MCP server over stdio.")
    parser.parse_args()
    anyio.run(run_stdio)


if __name__ == "__main__":
    main()
