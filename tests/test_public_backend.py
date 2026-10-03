"""Cloud-independent backend checks using only fake models/connections."""

import io
import json
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier, Lock
from unittest.mock import MagicMock, patch

import psycopg
from fastapi.testclient import TestClient
from langchain.messages import AIMessage

from app.api import create_app
from app.config import ConfigurationError, get_settings
from app.database import DatabaseUnavailableError, database_context, get_connection
from app.llm import get_llm
from app.profiles import resolve_profile
from app.trace import AgentTrace, read_trace, safe_trace_value

SALES_URL = "postgresql://analyst_agent:sales-fake-secret@sales.example.invalid:5432/private_sales?sslmode=require"
SAAS_URL = "postgresql://analyst_agent:saas-fake-secret@saas.example.invalid:5432/private_saas?sslmode=require"


def model_with(*responses):
    model = MagicMock()
    model.model = "fake-model"
    model.bind_tools.return_value = model
    model.invoke.side_effect = responses
    return model


def schema_request():
    return AIMessage(content="", tool_calls=[{
        "name": "get_schema", "args": {}, "id": "schema-call", "type": "tool_call",
    }])


class PublicBackendTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        settings = {
            "GOOGLE_API_KEY": "fake-model-key",
            "DATABASE_SALES_URL": "", "DATABASE_SAAS_URL": "",
            "DB_HOST": "local.example.invalid", "DB_PORT": "5432", "DB_NAME": "cli_default",
            "DB_USER": "analyst_agent", "DB_PASSWORD": "local-fake-password",
            "ALLOWED_ORIGINS": "http://localhost:5173, https://frontend.example.invalid",
            "TRACE_ENABLED": "true", "TRACE_DIR": str(self.directory / "traces"),
        }
        for patcher in (patch.dict(os.environ, settings, clear=True),
                        patch("app.config.load_dotenv"), patch("app.database.load_dotenv")):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = TestClient(create_app(get_settings(load_environment=False)))
        self.addCleanup(self.client.close)

    def post(self, profile="sales"):
        return self.client.post("/query", json={"question": "Count records", "database": profile})

    def test_health_and_ready_call_neither_model_nor_database(self):
        with patch("app.agent.get_llm") as model, patch("app.database.psycopg.connect") as connect:
            self.assertEqual(self.client.get("/health").json(), {"status": "ok"})
            response = self.client.get("/ready")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {"status": "ready"})
        model.assert_not_called()
        connect.assert_not_called()

    def test_ready_all_hosted_profiles_need_no_local_settings(self):
        with patch.dict(os.environ, {"DATABASE_SALES_URL": SALES_URL, "DATABASE_SAAS_URL": SAAS_URL,
                                    "DB_HOST": "", "DB_PORT": "", "DB_NAME": "",
                                    "DB_USER": "", "DB_PASSWORD": ""}), \
             patch("app.database.psycopg.connect") as connect:
            self.assertEqual(self.client.get("/ready").status_code, 200)
        connect.assert_not_called()

    def test_readiness_missing_or_invalid_configuration_is_safe(self):
        for overrides in ({"GOOGLE_API_KEY": ""}, {"DB_PASSWORD": ""},
                          {"DATABASE_SALES_URL": "postgresql://fake-secret@host/db?invalid-option=private"},
                          {"TRACE_ENABLED": "invalid-private-value"}):
            with self.subTest(overrides=overrides), patch.dict(os.environ, overrides):
                response = self.client.get("/ready")
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.json()["error_type"], "service_unavailable")
                self.assertNotIn("private", response.text)
                self.assertNotIn("postgresql", response.text)
                self.assertNotIn("fake-model-key", response.text)
                self.assertEqual(self.client.get("/health").status_code, 200)

    def test_database_metadata_has_only_safe_display_fields(self):
        with patch.dict(os.environ, {"DATABASE_SALES_URL": SALES_URL, "DATABASE_SAAS_URL": SAAS_URL}), \
             patch("app.database.psycopg.connect") as connect:
            response = self.client.get("/databases")
        self.assertEqual(response.status_code, 200)
        self.assertEqual([p["id"] for p in response.json()], ["sales", "saas"])
        for profile in response.json():
            self.assertEqual(set(profile), {"id", "name", "description"})
        for value in (SALES_URL, SAAS_URL, "private_sales", "private_saas", "analyst_agent", "5432"):
            self.assertNotIn(value, response.text)
        connect.assert_not_called()

    def test_connection_fields_and_unknown_destinations_are_rejected(self):
        fields = {"host": "badhost", "port": 9999, "user": "admin", "password": "fake-secret",
                  "database_url": SALES_URL, "database_name": "arbitrary", "sslmode": "disable"}
        with patch("app.api.run_agent") as agent:
            for key, value in fields.items():
                response = self.client.post("/query", json={"question": "Count", "database": "sales", key: value})
                self.assertEqual(response.status_code, 400)
                self.assertNotIn(str(value), response.text)
            for value in ("arbitrary_database", SALES_URL):
                self.assertEqual(self.client.post("/query", json={"question": "Count", "database": value}).status_code, 400)
        agent.assert_not_called()

    def test_profile_urls_reach_native_psycopg_unchanged(self):
        with patch.dict(os.environ, {"DATABASE_SALES_URL": SALES_URL, "DATABASE_SAAS_URL": SAAS_URL}), \
             patch("app.database.psycopg.connect") as connect:
            original = dict(os.environ)
            for profile, expected in (("sales", SALES_URL), ("saas", SAAS_URL)):
                target = resolve_profile(profile)
                with database_context(target.database_name, database_url=target.url):
                    get_connection()
                connect.assert_called_with(expected, connect_timeout=5)
            self.assertEqual(dict(os.environ), original)

    def test_url_profiles_work_without_any_local_credentials(self):
        with patch.dict(os.environ, {"DATABASE_SALES_URL": SALES_URL, "DB_HOST": "",
                                    "DB_PORT": "", "DB_USER": "", "DB_PASSWORD": ""}), \
             patch("app.database.psycopg.connect") as connect:
            target = resolve_profile("sales")
            get_connection(database_url=target.url)
        connect.assert_called_with(SALES_URL, connect_timeout=5)

    def test_local_fallback_cli_and_mixed_hosted_profiles(self):
        with patch.dict(os.environ, {"DATABASE_SALES_URL": SALES_URL}), \
             patch("app.database.psycopg.connect") as connect:
            # No profile/context: the CLI still uses DB_NAME, even with hosted URLs configured.
            get_connection()
            self.assertEqual(connect.call_args.kwargs["dbname"], "cli_default")
            target = resolve_profile("saas")
            self.assertIsNone(target.url)
            with database_context(target.database_name):
                get_connection()
            self.assertEqual(connect.call_args.kwargs["dbname"], "agentic_analyst_saas")
            self.assertEqual(connect.call_args.kwargs["user"], "analyst_agent")

    def test_api_resolves_both_hosted_profiles_through_same_core(self):
        with patch.dict(os.environ, {"DATABASE_SALES_URL": SALES_URL, "DATABASE_SAAS_URL": SAAS_URL}), \
             patch("app.api.run_agent", return_value="Fake answer") as agent:
            for profile, expected in (("sales", SALES_URL), ("saas", SAAS_URL)):
                response = self.post(profile)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["database"], profile)
                self.assertEqual(agent.call_args.kwargs["database_url"], expected)
                self.assertNotIn(expected, response.text)
                self.assertEqual(agent.call_args.kwargs["trace"].database_profile, profile)

    def test_concurrent_hosted_api_connections_are_isolated(self):
        barrier, lock = Barrier(2), Lock()
        seen = []

        def connect(url, **kwargs):
            with lock:
                seen.append(url)
            barrier.wait(timeout=10)
            connection, cursor = MagicMock(), MagicMock()
            connection.__enter__.return_value = connection
            connection.cursor.return_value.__enter__.return_value = cursor
            cursor.fetchall.return_value = [("example", "id", "integer")]
            return connection

        def request(profile):
            with TestClient(create_app(get_settings(load_environment=False))) as client:
                return client.post("/query", json={"question": "Inspect", "database": profile})

        with patch.dict(os.environ, {"DATABASE_SALES_URL": SALES_URL, "DATABASE_SAAS_URL": SAAS_URL}), \
             patch("app.database.psycopg.connect", side_effect=connect), \
             patch("app.agent.get_llm", side_effect=lambda: model_with(schema_request(), AIMessage(content="Done"))):
            before = dict(os.environ)
            with ThreadPoolExecutor(max_workers=2) as pool:
                responses = list(pool.map(request, ["sales", "saas"]))
            self.assertEqual(dict(os.environ), before)
            with patch("app.database.psycopg.connect") as default:
                get_connection()
                self.assertEqual(default.call_args.kwargs["dbname"], "cli_default")
        self.assertEqual([r.status_code for r in responses], [200, 200])
        self.assertCountEqual(seen, [SALES_URL, SAAS_URL])
        self.assertEqual(len({r.json()["run_id"] for r in responses}), 2)

    def test_cors_allows_only_configured_origins_without_credentials(self):
        for origin, allowed in (("http://localhost:5173", True),
                                ("https://frontend.example.invalid", True),
                                ("https://unconfigured.example.invalid", False)):
            with self.subTest(origin=origin):
                headers = {"Origin": origin, "Access-Control-Request-Method": "POST",
                           "Access-Control-Request-Headers": "content-type"}
                preflight = self.client.options("/query", headers=headers)
                actual = self.client.get("/health", headers={"Origin": origin})
                self.assertEqual(preflight.status_code, 200 if allowed else 400)
                for response in (preflight, actual):
                    self.assertEqual(response.headers.get("access-control-allow-origin"), origin if allowed else None)
                    self.assertNotIn("access-control-allow-credentials", response.headers)

    def test_default_cors_is_closed_and_wildcards_are_rejected(self):
        with patch.dict(os.environ, {"ALLOWED_ORIGINS": ""}):
            with TestClient(create_app(get_settings(load_environment=False))) as client:
                response = client.get("/health", headers={"Origin": "https://any.example.invalid"})
                self.assertNotIn("access-control-allow-origin", response.headers)
        with patch.dict(os.environ, {"ALLOWED_ORIGINS": "*"}):
            with self.assertRaises(ConfigurationError):
                get_settings(load_environment=False)

    def test_connection_failure_returns_safe_503_and_failed_trace(self):
        requests = [schema_request(), AIMessage(content="", tool_calls=[{
            "name": "execute_sql", "args": {"query": "SELECT 1"}, "id": "sql-call", "type": "tool_call",
        }])]
        for call in requests:
            with self.subTest(tool=call.tool_calls[0]["name"]), \
                 patch.dict(os.environ, {"DATABASE_SALES_URL": SALES_URL}), \
                 patch("app.agent.get_llm", return_value=model_with(call)), \
                 patch("app.database.psycopg.connect", side_effect=psycopg.OperationalError(
                     SALES_URL + " local-fake-password fake-model-key Authorization: Bearer private")):
                response = self.post()
                self.assertEqual(response.status_code, 503)
                body = response.json()
                self.assertEqual(body["error_type"], "database_unavailable")
                document = json.loads(Path(body["trace_path"]).read_text(encoding="utf-8"))
                self.assertEqual(document["status"], "database_error")
                self.assertEqual(document["tool_calls"][0]["error_category"], "database_unavailable")
                self.assertEqual(document["tool_calls"][0]["tool_call_id"], call.tool_calls[0]["id"])
                for content in (response.text, json.dumps(document)):
                    for secret in (SALES_URL, "sales-fake-secret", "local-fake-password", "fake-model-key", "Bearer private"):
                        self.assertNotIn(secret, content)
        self.assertEqual(self.client.get("/health").status_code, 200)

    def test_native_connection_error_itself_retains_no_raw_message(self):
        with patch("app.database.psycopg.connect", side_effect=psycopg.OperationalError(SALES_URL)):
            with self.assertRaises(DatabaseUnavailableError) as raised:
                get_connection(database_url=SALES_URL)
        self.assertEqual(str(raised.exception), "The database is unavailable.")
        self.assertTrue(raised.exception.__suppress_context__)

    def test_bad_profile_configuration_fails_before_model(self):
        with patch.dict(os.environ, {"DB_PASSWORD": ""}), patch("app.agent.get_llm") as model:
            response = self.post()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error_type"], "service_unavailable")
        model.assert_not_called()

    def test_model_and_unexpected_failures_do_not_echo_secrets_or_headers(self):
        raw = SALES_URL + " fake-model-key local-fake-password Authorization: Bearer private"
        with patch("app.agent.get_llm", side_effect=RuntimeError(raw)):
            response = self.post()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error_type"], "model_integration_error")
        self.assertNotIn("fake-model-key", response.text)
        self.assertNotIn("postgresql", response.text)
        with patch("app.api.run_agent", side_effect=RuntimeError(raw)):
            response = self.post()
        self.assertEqual(response.status_code, 500)
        self.assertNotIn("Bearer", response.text)
        self.assertNotIn("local-fake-password", response.text)

    def test_http_errors_do_not_echo_path_or_exception_details(self):
        response = self.client.get("/fake-model-key")
        self.assertEqual(response.status_code, 404)
        self.assertNotIn("fake-model-key", response.text)
        self.assertEqual(self.client.put("/query").status_code, 405)

    def test_trace_disabled_preserves_success_and_failure_without_files(self):
        with patch.dict(os.environ, {"TRACE_ENABLED": "false"}), \
             patch("app.agent.get_llm", return_value=model_with(AIMessage(content="Done"))):
            response = self.post()
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["trace_path"])
        self.assertTrue(response.json()["run_id"])
        with patch.dict(os.environ, {"TRACE_ENABLED": "false"}), \
             patch("app.agent.get_llm", side_effect=RuntimeError("fake error")):
            response = self.post()
        self.assertEqual(response.status_code, 503)
        self.assertIsNone(response.json()["trace_path"])
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_trace_directory_and_hosted_password_redaction(self):
        url = "postgresql://analyst_agent:p%40ss-fake-secret@host.example.invalid/db?sslmode=require"
        with patch.dict(os.environ, {"DATABASE_SALES_URL": url}), \
             patch("app.agent.get_llm", return_value=model_with(AIMessage(content="p@ss-fake-secret " + url))):
            response = self.post()
            saved = read_trace(Path(response.json()["trace_path"]))
            redacted = safe_trace_value({"headers": "Bearer private", "database_url": url})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Path(response.json()["trace_path"]).parent, self.directory / "traces")
        self.assertEqual(saved["final_answer"], "[REDACTED] [REDACTED]")
        self.assertNotIn("p@ss-fake-secret", response.text)
        self.assertNotIn(url, json.dumps(saved))
        self.assertEqual(redacted["headers"], "[REDACTED]")

    def test_unwritable_trace_directory_preserves_answer_and_safe_warning(self):
        blocked = self.directory / "blocked"
        blocked.write_text("This is a file, not a directory.", encoding="utf-8")
        errors = io.StringIO()
        with patch.dict(os.environ, {"TRACE_DIR": str(blocked)}), redirect_stderr(errors), \
             patch("app.agent.get_llm", return_value=model_with(AIMessage(content="Done"))):
            response = self.post()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["answer"], "Done")
        self.assertIsNone(response.json()["trace_path"])
        self.assertIn("Trace warning:", errors.getvalue())
        self.assertNotIn(str(blocked), errors.getvalue())

    def test_settings_hide_secrets_and_gemini_uses_central_configuration(self):
        with patch.dict(os.environ, {"DATABASE_SALES_URL": SALES_URL}), \
             patch("app.llm.ChatGoogleGenerativeAI") as model:
            settings = get_settings(load_environment=False)
            get_llm()
        model.assert_called_once_with(model="gemini-3.5-flash-lite", temperature=0, api_key="fake-model-key")
        for secret in ("fake-model-key", "local-fake-password", SALES_URL):
            self.assertNotIn(secret, repr(settings))


if __name__ == "__main__":
    unittest.main()
