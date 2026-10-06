"""Offline MCP protocol, profile isolation, and shared SQL-boundary checks."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from threading import Barrier
import unittest
from unittest.mock import MagicMock, patch

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.shared.memory import create_connected_server_and_client_session
import psycopg

from app import mcp_server
from app.config import ConfigurationError, Settings
from app.database import DatabaseUnavailableError, _database_target
from app.profiles import PUBLIC_DATABASES
from app.tools import MAX_ROWS, STATEMENT_TIMEOUT_MS, calculator, describe_table, execute_sql, get_schema

ROOT = Path(__file__).resolve().parent.parent
SETTINGS = Settings(
    db_host="localhost", db_port="5432", db_name="default",
    db_user="analyst_agent", db_password="fake-password",
)
TOOL_NAMES = {"list_database_profiles", "get_schema", "describe_table", "execute_sql"}


def text(result):
    return result.content[0].text


def fake_connection():
    connection, cursor = MagicMock(), MagicMock()
    connection.__enter__.return_value = connection
    connection.cursor.return_value = cursor
    cursor.__enter__.return_value = cursor
    return connection, cursor


class MCPAdapterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # Never load the real .env or contact a real database/model.
        settings = patch("app.mcp_server.get_settings", return_value=SETTINGS)
        settings.start()
        self.addCleanup(settings.stop)
        connection = patch("app.tools.get_connection", side_effect=AssertionError("Live DB forbidden"))
        self.connect = connection.start()
        self.addCleanup(connection.stop)

    async def test_registration_and_safe_public_profiles_over_protocol(self):
        with patch("app.mcp_server.get_settings", side_effect=AssertionError("No config needed")):
            async with create_connected_server_and_client_session(mcp_server.server) as client:
                tools = (await client.list_tools()).tools
                self.assertEqual({tool.name for tool in tools}, TOOL_NAMES)
                for tool in tools:
                    self.assertTrue(tool.annotations.readOnlyHint)
                    self.assertFalse(tool.annotations.destructiveHint)
                    self.assertFalse(tool.inputSchema["additionalProperties"])
                    if tool.name != "list_database_profiles":
                        self.assertIn("profile", tool.inputSchema["required"])
                        self.assertEqual(tool.inputSchema["properties"]["profile"]["enum"], ["sales", "saas"])
                profiles = await client.call_tool("list_database_profiles", {})
        self.assertFalse(profiles.isError)
        self.assertEqual(json.loads(text(profiles)), PUBLIC_DATABASES)
        self.assertEqual(set(json.loads(text(profiles))[0]), {"id", "name", "description"})
        self.connect.assert_not_called()

    async def test_schema_routes_both_profiles_and_restores_context(self):
        original = _database_target.get()
        for profile, name in (("sales", "agentic_analyst"), ("saas", "agentic_analyst_saas")):
            def inspect_context(arguments):
                self.assertEqual(arguments, {})
                self.assertEqual(_database_target.get().database_name, name)
                return "TABLE: dynamically_discovered\n- id: integer"
            with self.subTest(profile=profile), patch.object(type(get_schema), "invoke", side_effect=inspect_context) as invoke:
                result = await mcp_server.call_tool("get_schema", {"profile": profile})
            invoke.assert_called_once_with({})
            self.assertFalse(result.isError)
            self.assertIn("dynamically_discovered", text(result))
            self.assertEqual(_database_target.get(), original)

    async def test_hosted_profile_preserves_private_url_in_context_only(self):
        url = "postgresql://analyst_agent:fake-password@host.invalid/db?sslmode=require"
        settings = Settings(database_sales_url=url)
        def inspect_context(arguments):
            self.assertEqual(_database_target.get().url, url)
            return "TABLE: example"
        with patch("app.mcp_server.get_settings", return_value=settings), \
             patch.object(type(get_schema), "invoke", side_effect=inspect_context):
            result = await mcp_server.call_tool("get_schema", {"profile": "sales"})
        self.assertFalse(result.isError)
        self.assertNotIn(url, text(result))
        self.assertIsNone(_database_target.get())

    async def test_describe_reuses_metadata_output_and_original_arguments(self):
        metadata = "TABLE: arbitrary\nCOLUMNS:\n- rate: numeric\n  description: Fractional rate\nPRIMARY KEY:\n- id\nFOREIGN KEYS:\n- FOREIGN KEY (other_id) REFERENCES other(id)"
        with patch.object(type(describe_table), "invoke", return_value=metadata) as invoke:
            result = await mcp_server.call_tool("describe_table", {"profile": "saas", "table_name": "arbitrary"})
        invoke.assert_called_once_with({"table_name": "arbitrary"})
        self.assertEqual(text(result), metadata)
        self.assertFalse(result.isError)

    async def test_missing_table_is_safe_native_tool_error(self):
        connection, cursor = fake_connection()
        cursor.fetchone.return_value = None
        table = "missing' OR 1=1 --"
        with patch("app.tools.get_connection", return_value=connection):
            result = await mcp_server.call_tool("describe_table", {"profile": "sales", "table_name": table})
        self.assertTrue(result.isError)
        self.assertIn("not found in the public schema", text(result))
        self.assertNotIn(table, text(result))
        self.assertEqual(cursor.execute.call_args.args[1], (table,))
        self.assertNotIn(table, cursor.execute.call_args.args[0])
        cursor.__exit__.assert_called_once()
        connection.__exit__.assert_called_once()

    async def test_unknown_profiles_and_malformed_arguments_never_echo_values(self):
        secret = "postgresql://owner:fake-secret@private.invalid/db"
        cases = [
            ("get_schema", {"profile": secret}, "Unknown database profile"),
            ("get_schema", {}, "Invalid tool arguments"),
            ("get_schema", {"profile": [secret]}, "Invalid tool arguments"),
            ("get_schema", {"profile": "sales", "database_url": secret}, "Invalid tool arguments"),
            ("describe_table", {"profile": "sales", "table_name": {"password": secret}}, "Invalid tool arguments"),
            ("execute_sql", {"profile": "sales", "query": [secret]}, "Invalid tool arguments"),
            ("list_database_profiles", {"password": secret}, "Invalid tool arguments"),
        ]
        async with create_connected_server_and_client_session(mcp_server.server) as client:
            for name, arguments, message in cases:
                with self.subTest(name=name, arguments=arguments):
                    result = await client.call_tool(name, arguments)
                    self.assertTrue(result.isError)
                    self.assertIn(message, text(result))
                    self.assertNotIn(secret, text(result))
        self.connect.assert_not_called()

    async def test_unknown_tool_is_safe_native_error(self):
        result = await mcp_server.call_tool("not_a_tool", {})
        self.assertTrue(result.isError)
        self.assertIn("Unknown tool", text(result))
        self.connect.assert_not_called()

    async def test_write_and_ddl_queries_use_original_rejection_before_connection(self):
        queries = (
            "INSERT INTO example VALUES (1)", "UPDATE example SET id=1", "DELETE FROM example",
            "TRUNCATE example", "CREATE TABLE example (id integer)", "ALTER TABLE example ADD x int",
            "DROP TABLE example", "SELECT 1; SELECT 2", "SELECT 1 INTO example",
            "WITH changed AS (DELETE FROM example RETURNING *) SELECT * FROM changed", " ",
        )
        for query in queries:
            with self.subTest(query=query):
                result = await mcp_server.call_tool("execute_sql", {"profile": "sales", "query": query})
                self.assertTrue(result.isError)
                self.assertEqual(text(result), execute_sql.invoke({"query": query}))
        self.connect.assert_not_called()

    async def test_execute_sql_reuses_read_only_timeout_cap_and_connection_lifecycle(self):
        for query in ("SELECT id FROM example", "WITH x AS (SELECT 1 AS id) SELECT id FROM x"):
            connection, cursor = fake_connection()
            cursor.description = [MagicMock(name="column")]
            cursor.description[0].name = "id"
            cursor.fetchmany.return_value = [(i,) for i in range(MAX_ROWS + 1)]
            with self.subTest(query=query), patch("app.tools.get_connection", return_value=connection), \
                 patch.object(type(execute_sql), "invoke", wraps=execute_sql.invoke) as invoke:
                result = await mcp_server.call_tool("execute_sql", {"profile": "saas", "query": query})
            invoke.assert_called_once_with({"query": query})
            self.assertFalse(result.isError)
            self.assertEqual(cursor.execute.call_args_list[0].args, ("SET TRANSACTION READ ONLY",))
            self.assertEqual(cursor.execute.call_args_list[1].args,
                             ("SELECT set_config('statement_timeout', %s, true)", (str(STATEMENT_TIMEOUT_MS),)))
            self.assertEqual(cursor.execute.call_args_list[2].args, (query,))
            cursor.fetchmany.assert_called_once_with(MAX_ROWS + 1)
            self.assertIn(f"Result truncated to {MAX_ROWS} rows.", text(result))
            rows = text(result).split("ROWS:\n")[1].split("\n\nRows returned:")[0]
            self.assertEqual(len(rows.splitlines()), MAX_ROWS)
            cursor.__exit__.assert_called_once()
            connection.__exit__.assert_called_once()

    async def test_sql_syntax_timeout_and_permission_errors_keep_existing_safe_details(self):
        for error, message in (
            (psycopg.errors.SyntaxError("fake secret details"), "invalid SQL syntax"),
            (psycopg.errors.QueryCanceled("fake secret details"), "query timed out or was cancelled"),
            (psycopg.errors.InsufficientPrivilege("fake secret details"), "PostgreSQL rejected the query"),
        ):
            connection, cursor = fake_connection()
            cursor.execute.side_effect = [None, None, error]
            with self.subTest(error=type(error)), patch("app.tools.get_connection", return_value=connection):
                result = await mcp_server.call_tool("execute_sql", {"profile": "sales", "query": "SELECT 1"})
            self.assertTrue(result.isError)
            self.assertEqual(text(result), f"SQL execution error: {message}.")
            self.assertNotIn("fake secret", text(result))
            cursor.__exit__.assert_called_once()
            connection.__exit__.assert_called_once()

    async def test_configuration_connection_and_runtime_failures_never_expose_exceptions(self):
        details = "postgresql://owner:fake-password@host.invalid/db C:\\private\\secret.env"
        original = _database_target.get()
        for failure, expected in (
            (ConfigurationError(details), "Database configuration is missing or invalid."),
            (DatabaseUnavailableError(details), "The database is unavailable."),
            (RuntimeError(details), "The database tool could not be completed."),
        ):
            with self.subTest(failure=type(failure)), patch.object(type(get_schema), "invoke", side_effect=failure):
                result = await mcp_server.call_tool("get_schema", {"profile": "sales"})
            self.assertTrue(result.isError)
            self.assertEqual(text(result), expected)
            self.assertEqual(_database_target.get(), original)
        with patch("app.mcp_server.get_settings", side_effect=ConfigurationError(details)):
            result = await mcp_server.call_tool("get_schema", {"profile": "sales"})
        self.assertTrue(result.isError)
        self.assertNotIn(details, text(result))

    async def test_output_uses_existing_secret_redaction(self):
        url = "postgresql://analyst_agent:fake-password@host.invalid/db"
        with patch.dict(os.environ, {"DB_PASSWORD": "fake-password", "DATABASE_SALES_URL": url}), \
             patch.object(type(get_schema), "invoke", return_value=f"TABLE: example\n{url}\nfake-password"):
            result = await mcp_server.call_tool("get_schema", {"profile": "sales"})
        self.assertFalse(result.isError)
        self.assertNotIn(url, text(result))
        self.assertNotIn("fake-password", text(result))


class MCPIsolationTests(unittest.TestCase):
    def test_overlapping_calls_keep_profiles_isolated_without_env_changes(self):
        barrier = Barrier(2)
        original_environment = dict(os.environ)
        def observe(arguments):
            before = _database_target.get().database_name
            barrier.wait(timeout=5)
            self.assertEqual(_database_target.get().database_name, before)
            return before
        def call(profile):
            result = mcp_server.invoke_database_tool("get_schema", {"profile": profile})
            self.assertIsNone(_database_target.get())
            return text(result)
        with patch("app.mcp_server.get_settings", return_value=SETTINGS), \
             patch.object(type(get_schema), "invoke", side_effect=observe), ThreadPoolExecutor(2) as pool:
            sales = pool.submit(call, "sales")
            saas = pool.submit(call, "saas")
            self.assertEqual(sales.result(timeout=10), "agentic_analyst")
            self.assertEqual(saas.result(timeout=10), "agentic_analyst_saas")
        self.assertEqual(dict(os.environ), original_environment)

    def test_existing_langchain_tools_keep_their_original_schemas(self):
        for item in (get_schema, describe_table, execute_sql):
            self.assertIs(mcp_server.DATABASE_TOOLS[item.name], item)
            self.assertNotIn("profile", item.get_input_schema().model_json_schema()["properties"])
        self.assertEqual(calculator.invoke({"operation": "multiply", "a": 125, "b": 37}), 4625)


class MCPStdioTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_stdio_process_starts_lists_tools_and_profiles_then_exits(self):
        parameters = StdioServerParameters(
            command=sys.executable, args=["-m", "app.mcp_server"], cwd=ROOT,
            env={"GOOGLE_API_KEY": "", "DATABASE_SALES_URL": "", "DATABASE_SAAS_URL": "",
                 "TRACE_ENABLED": "false", "ALLOWED_ORIGINS": ""},
        )
        async def smoke():
            with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as errors:
                async with stdio_client(parameters, errlog=errors) as (read, write):
                    async with ClientSession(read, write) as client:
                        await client.initialize()
                        self.assertEqual({tool.name for tool in (await client.list_tools()).tools}, TOOL_NAMES)
                        result = await client.call_tool("list_database_profiles", {})
                        self.assertEqual(json.loads(text(result)), PUBLIC_DATABASES)
                        malformed = await client.call_tool("execute_sql", {"profile": "sales", "query": ["fake-secret"]})
                        self.assertTrue(malformed.isError)
                        unknown = await client.call_tool("fake-secret-unknown-tool", {})
                        self.assertTrue(unknown.isError)
                errors.seek(0)
                self.assertEqual(errors.read(), "")
        # Includes client shutdown and child-process cleanup; no persistent server.
        await asyncio.wait_for(smoke(), timeout=30)

    async def test_help_exits_without_starting_server(self):
        result = await asyncio.to_thread(subprocess.run,
            [sys.executable, "-m", "app.mcp_server", "--help"],
            cwd=ROOT, capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("stdio", result.stdout)
        self.assertEqual(result.stderr, "")


if __name__ == "__main__":
    unittest.main()
