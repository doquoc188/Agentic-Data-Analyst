"""HTTP and per-run database tests; no Gemini or live SQL calls."""

import json
import os
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier, Lock
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from langchain.messages import AIMessage

from app.agent import ModelIntegrationError, ToolInvocationError, run_agent
from app.api import app
from app.database import database_context, get_connection
from app.profiles import DATABASE_PROFILES
from app.trace import AgentTrace


def fake_model(*responses):
    model = MagicMock()
    model.bind_tools.return_value = model
    model.invoke.side_effect = list(responses)
    return model


class ApiTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.runs = Path(directory.name)
        trace_patch = patch("app.trace.RUNS_DIR", self.runs)
        trace_patch.start()
        self.addCleanup(trace_patch.stop)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def test_health_does_not_call_agent_model_or_database(self):
        with patch("app.api.run_agent") as agent, patch("app.agent.get_llm") as model, \
             patch("app.database.psycopg.connect") as connect:
            response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})
        agent.assert_not_called()
        model.assert_not_called()
        connect.assert_not_called()

    def test_success_profiles_share_agent_core_and_persist_trace(self):
        for profile, database in DATABASE_PROFILES.items():
            with self.subTest(profile=profile), \
                 patch("app.agent.get_llm", return_value=fake_model(AIMessage(content="A real-core fake answer."))), \
                 patch("app.api.run_agent", wraps=run_agent) as agent:
                response = self.client.post("/query", json={"question": " Count records. ", "database": profile})
                self.assertEqual(response.status_code, 200)
                body = response.json()
                self.assertEqual(body["status"], "success")
                self.assertEqual(body["answer"], "A real-core fake answer.")
                self.assertEqual(body["database"], profile)
                self.assertEqual(agent.call_args.kwargs["database_name"], database)
                self.assertEqual(agent.call_args.args, ("Count records.",))
                document = json.loads(Path(body["trace_path"]).read_text(encoding="utf-8"))
                self.assertEqual(document["run_id"], body["run_id"])
                self.assertEqual(document["source"], "api")
                self.assertEqual(document["database_profile"], profile)
                self.assertEqual(document["status"], "success")
                self.assertNotIn("tool_calls", body)

    def test_invalid_requests_are_400_and_do_not_echo_input(self):
        secret = "fake-secret-not-to-echo"
        invalid = [
            {"database": "sales"},
            {"question": "", "database": "sales"},
            {"question": " \t\n", "database": "saas"},
            {"question": "x" * 4001, "database": "sales"},
            {"question": "Count", "database": secret},
            {"question": "Count", "database": "postgresql://user:password@host/db"},
            {"question": "Count", "database": "sales", "DB_PASSWORD": secret},
            {"question": 123, "database": "sales"},
            {"question": "Count"},
        ]
        with patch("app.api.run_agent") as agent:
            for body in invalid:
                with self.subTest(body=body):
                    response = self.client.post("/query", json=body)
                    self.assertEqual(response.status_code, 400)
                    self.assertEqual(response.json()["error_type"], "invalid_request")
                    self.assertNotIn(secret, response.text)
                    self.assertNotIn("postgresql://", response.text)
            response = self.client.post("/query", content='{"password":"' + secret,
                                        headers={"Content-Type": "application/json"})
            self.assertEqual(response.status_code, 400)
            self.assertNotIn(secret, response.text)
        agent.assert_not_called()

    def test_provider_failure_is_safe_and_links_failed_trace(self):
        secret = "fake-db-password"
        api_key = "fake-api-key"
        with patch.dict(os.environ, {"DB_PASSWORD": secret, "GOOGLE_API_KEY": api_key}), \
             patch("app.agent.get_llm", side_effect=RuntimeError(secret + api_key)):
            response = self.client.post("/query", json={"question": "Count records", "database": "sales"})
        self.assertEqual(response.status_code, 503)
        body = response.json()
        self.assertEqual(body["error_type"], "model_integration_error")
        self.assertNotIn(secret, response.text)
        self.assertNotIn(api_key, response.text)
        self.assertNotIn("Traceback", response.text)
        document = json.loads(Path(body["trace_path"]).read_text(encoding="utf-8"))
        self.assertEqual(document["status"], "model_error")
        self.assertEqual(document["run_id"], body["run_id"])
        self.assertNotIn(secret, json.dumps(document))
        self.assertNotIn(api_key, json.dumps(document))

    def test_tool_and_unexpected_errors_never_return_exception_text(self):
        for error, error_type in [(ToolInvocationError("password=private"), "tool_runtime_error"),
                                  (RuntimeError("api_key=private"), "internal_error")]:
            with self.subTest(error_type=error_type), patch("app.api.run_agent", side_effect=error):
                response = self.client.post("/query", json={"question": "Count records", "database": "saas"})
                self.assertEqual(response.status_code, 500)
                self.assertEqual(response.json()["error_type"], error_type)
                self.assertNotIn("private", response.text)

    def test_iteration_limit_is_503_with_trace_link(self):
        calls = [AIMessage(content="", tool_calls=[{
            "name": "calculator", "args": {"operation": "add", "a": 1, "b": 2},
            "id": f"call_{turn}", "type": "tool_call",
        }]) for turn in range(8)]
        with patch("app.agent.get_llm", return_value=fake_model(*calls)):
            response = self.client.post("/query", json={"question": "Add", "database": "sales"})
        self.assertEqual(response.status_code, 503)
        body = response.json()
        self.assertEqual(body["error_type"], "iteration_limit")
        self.assertTrue(Path(body["trace_path"]).is_file())

    def test_success_redacts_configured_secrets(self):
        with patch.dict(os.environ, {"DB_PASSWORD": "fake-db-secret", "GOOGLE_API_KEY": "fake-model-secret"}), \
             patch("app.agent.get_llm", return_value=fake_model(AIMessage(content="fake-db-secret fake-model-secret"))):
            response = self.client.post("/query", json={"question": "Count", "database": "sales"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["answer"], "[REDACTED] [REDACTED]")

    def test_concurrent_api_runs_isolate_all_three_database_tools(self):
        # Both requests overlap at every connection, so a shared DB_NAME would leak.
        barrier, lock = Barrier(2), Lock()
        seen = []
        settings = {"DB_HOST": "localhost", "DB_PORT": "5432", "DB_NAME": "cli_default",
                    "DB_USER": "analyst_agent", "DB_PASSWORD": "fake-password"}

        def connect(**kwargs):
            database = kwargs["dbname"]
            with lock:
                seen.append((database, os.environ["DB_NAME"]))
            barrier.wait(timeout=10)
            connection, cursor = MagicMock(), MagicMock()
            connection.__enter__.return_value = connection
            connection.cursor.return_value.__enter__.return_value = cursor
            # The three database tools execute their real SQL-building paths.
            def fetchall():
                query = cursor.execute.call_args.args[0]
                if "c.table_name, c.column_name" in query:
                    return [("example", "id", "integer")]
                if "c.is_nullable" in query:
                    return [("id", "integer", "NO", None, None)]
                if "attribute.attname" in query:
                    return [("id",)]
                return []  # No foreign keys.
            cursor.fetchall.side_effect = fetchall
            cursor.fetchone.return_value = (1,)
            column = MagicMock()
            column.name = "current_database"
            cursor.description = [column]
            cursor.fetchmany.return_value = [(database,)]
            return connection

        def model():
            calls = [{"name": name, "args": args, "id": name, "type": "tool_call"}
                     for name, args in [
                         ("get_schema", {}), ("describe_table", {"table_name": "example"}),
                         ("execute_sql", {"query": "SELECT current_database()"}),
                     ]]
            return fake_model(AIMessage(content="", tool_calls=calls), AIMessage(content="Done."))

        def request(profile):
            with TestClient(app) as client:
                return client.post("/query", json={"question": "Inspect", "database": profile})

        with patch.dict(os.environ, settings), patch("app.database.load_dotenv"), \
             patch("app.database.psycopg.connect", side_effect=connect), \
             patch("app.agent.get_llm", side_effect=model):
            original = dict(os.environ)
            with ThreadPoolExecutor(max_workers=2) as pool:
                responses = list(pool.map(request, ["sales", "saas"]))
            self.assertEqual(dict(os.environ), original)
            # A subsequent connection on this thread still uses the CLI default.
            with patch("app.database.psycopg.connect") as default_connect:
                get_connection()
                self.assertEqual(default_connect.call_args.kwargs["dbname"], "cli_default")

        self.assertEqual([response.status_code for response in responses], [200, 200])
        self.assertEqual(len({response.json()["run_id"] for response in responses}), 2)
        self.assertEqual(seen.count(("agentic_analyst", "cli_default")), 3)
        self.assertEqual(seen.count(("agentic_analyst_saas", "cli_default")), 3)
        for response in responses:
            body = response.json()
            document = json.loads(Path(body["trace_path"]).read_text(encoding="utf-8"))
            sql_result = document["tool_calls"][-1]["result"]["text"]
            self.assertIn(DATABASE_PROFILES[body["database"]], sql_result)
            self.assertEqual([c["status"] for c in document["tool_calls"]], ["success"] * 3)


class DatabaseContextTests(unittest.TestCase):
    def setUp(self):
        settings = {"DB_HOST": "localhost", "DB_PORT": "5432", "DB_NAME": "normal_database",
                    "DB_USER": "analyst_agent", "DB_PASSWORD": "fake-password"}
        for patcher in (patch.dict(os.environ, settings), patch("app.database.load_dotenv")):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_explicit_override_nested_context_and_exception_cleanup(self):
        with patch("app.database.psycopg.connect") as connect:
            with self.assertRaises(ValueError), database_context("outer"):
                get_connection()
                self.assertEqual(connect.call_args.kwargs["dbname"], "outer")
                with database_context("inner"):
                    get_connection()
                    self.assertEqual(connect.call_args.kwargs["dbname"], "inner")
                    get_connection(database_name="explicit")
                    self.assertEqual(connect.call_args.kwargs["dbname"], "explicit")
                get_connection()
                self.assertEqual(connect.call_args.kwargs["dbname"], "outer")
                raise ValueError("controlled")
            get_connection()
            self.assertEqual(connect.call_args.kwargs["dbname"], "normal_database")

    def test_agent_failure_restores_database_context(self):
        with TemporaryDirectory() as directory, patch("app.trace.RUNS_DIR", Path(directory)), \
             patch("app.agent.get_llm", side_effect=RuntimeError("fake failure")), \
             patch("app.database.psycopg.connect") as connect:
            with self.assertRaises(ModelIntegrationError):
                run_agent("Question", database_name="other_database", verbose=False)
            get_connection()
            self.assertEqual(connect.call_args.kwargs["dbname"], "normal_database")

    def test_cli_still_calls_same_core_without_database_override(self):
        from app.agent import main
        with patch.object(sys, "argv", ["app.agent", "How many records?"]), \
             patch("app.agent.run_agent") as agent:
            main()
        self.assertEqual(agent.call_args.args, ("How many records?",))
        self.assertNotIn("database_name", agent.call_args.kwargs)
        self.assertEqual(agent.call_args.kwargs["trace"].source, "cli")


if __name__ == "__main__":
    unittest.main()
