"""Offline Olist profile checks and explicitly enabled local database checks."""

import json
import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.api import create_app
from app.config import ConfigurationError, Settings, get_settings, secret_values
from app.database import database_context, get_connection
from app.mcp_server import tool_definitions
from app.profiles import (
    DATABASE_PROFILES, LOCAL_AGENT_DATABASES, LOCAL_AGENT_PROFILES,
    PUBLIC_DATABASES, resolve_profile,
)
from app.tools import describe_table, execute_sql, get_schema


SALES_URL = "postgresql://analyst_agent:sales-secret@sales.example.invalid/sales"
SAAS_URL = "postgresql://analyst_agent:saas-secret@saas.example.invalid/saas"
OLIST_URL = "postgresql://analyst_agent:olist-secret@olist.example.invalid/olist"


def local_settings(**overrides) -> Settings:
    values = {
        "db_host": "localhost", "db_port": "5432", "db_name": "cli_default",
        "db_user": "analyst_agent", "db_password": "local-secret",
    }
    values.update(overrides)
    return Settings(**values)


class OlistProfileTests(unittest.TestCase):
    def test_olist_is_a_local_agent_profile_with_safe_metadata(self):
        self.assertEqual(LOCAL_AGENT_PROFILES["olist"], "agentic_analyst_olist")
        metadata = next(item for item in LOCAL_AGENT_DATABASES if item["id"] == "olist")
        self.assertEqual(metadata, {
            "id": "olist",
            "name": "Olist E-Commerce",
            "description": "An anonymized real-world Brazilian e-commerce dataset.",
        })
        self.assertIn("real-world", metadata["description"])
        self.assertIn("anonymized", metadata["description"])

    def test_existing_profile_targets_and_invalid_rejection_are_unchanged(self):
        settings = local_settings()
        self.assertEqual(resolve_profile("sales", settings).database_name, "agentic_analyst")
        self.assertEqual(resolve_profile("saas", settings).database_name, "agentic_analyst_saas")
        with self.assertRaisesRegex(ConfigurationError, "Unknown database profile"):
            resolve_profile("unknown", settings)

    def test_olist_url_and_local_fallback_resolve_without_affecting_other_profiles(self):
        hosted = local_settings(
            database_sales_url=SALES_URL,
            database_saas_url=SAAS_URL,
            database_olist_url=OLIST_URL,
        )
        targets = {profile: resolve_profile(profile, hosted)
                   for profile in ("sales", "saas", "olist")}
        self.assertEqual(targets["sales"].url, SALES_URL)
        self.assertEqual(targets["saas"].url, SAAS_URL)
        self.assertEqual(targets["olist"].url, OLIST_URL)
        self.assertEqual(targets["sales"].database_name, "agentic_analyst")
        self.assertEqual(targets["saas"].database_name, "agentic_analyst_saas")
        self.assertEqual(targets["olist"].database_name, "agentic_analyst_olist")

        fallback = resolve_profile("olist", local_settings())
        self.assertEqual(fallback.database_name, "agentic_analyst_olist")
        self.assertIsNone(fallback.url)

    def test_profile_context_is_isolated_and_never_mutates_environment(self):
        environment = {
            "DB_HOST": "localhost", "DB_PORT": "5432", "DB_NAME": "cli_default",
            "DB_USER": "analyst_agent", "DB_PASSWORD": "local-secret",
        }
        with patch.dict(os.environ, environment, clear=True), \
             patch("app.database.load_dotenv"), patch("app.database.psycopg.connect") as connect:
            before = dict(os.environ)
            for profile in ("sales", "olist", "saas"):
                target = resolve_profile(profile, local_settings())
                with database_context(target.database_name, database_url=target.url):
                    get_connection()
                    self.assertEqual(
                        connect.call_args.kwargs["dbname"], LOCAL_AGENT_PROFILES[profile]
                    )
            get_connection()
            self.assertEqual(connect.call_args.kwargs["dbname"], "cli_default")
            self.assertEqual(dict(os.environ), before)

    def test_public_api_remains_sales_and_saas_only(self):
        settings = local_settings(google_api_key="fake-key")
        with TestClient(create_app(settings)) as client, patch("app.api.run_agent") as agent:
            response = client.get("/databases")
            rejected = client.post(
                "/query", json={"question": "How many orders?", "database": "olist"}
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), PUBLIC_DATABASES)
        self.assertEqual([item["id"] for item in response.json()], ["sales", "saas"])
        self.assertEqual(set(DATABASE_PROFILES), {"sales", "saas"})
        self.assertEqual(rejected.status_code, 400)
        self.assertNotIn("olist", rejected.text)
        agent.assert_not_called()

    def test_mcp_profile_schema_and_metadata_remain_sales_and_saas_only(self):
        definitions = {tool.name: tool for tool in tool_definitions()}
        for name in ("get_schema", "describe_table", "execute_sql"):
            self.assertEqual(
                definitions[name].inputSchema["properties"]["profile"]["enum"],
                ["sales", "saas"],
            )
        self.assertNotIn("olist", json.dumps(PUBLIC_DATABASES).lower())

    def test_olist_url_and_credentials_are_private_and_redacted(self):
        environment = {
            "DATABASE_OLIST_URL": OLIST_URL,
            "DB_PASSWORD": "local-secret",
            "TRACE_ENABLED": "true",
        }
        with patch.dict(os.environ, environment, clear=True), patch("app.config.load_dotenv"):
            settings = get_settings(load_environment=False)
            secrets = secret_values()
        self.assertEqual(settings.database_olist_url, OLIST_URL)
        for secret in (OLIST_URL, "olist-secret", "local-secret"):
            self.assertNotIn(secret, repr(settings))
        self.assertIn(OLIST_URL, secrets)
        self.assertIn("olist-secret", secrets)
        self.assertNotIn(OLIST_URL, json.dumps(LOCAL_AGENT_DATABASES))
        self.assertNotIn("olist-secret", json.dumps(LOCAL_AGENT_DATABASES))


@unittest.skipUnless(
    os.getenv("RUN_OLIST_DB_TESTS") == "1",
    "Enable explicitly for the verified local Olist database.",
)
class OlistDatabaseTests(unittest.TestCase):
    def setUp(self):
        settings = get_settings()
        if settings.database_olist_url:
            self.skipTest("Live Olist tests require the local fallback, not a configured URL.")
        if settings.db_host.lower() not in {"localhost", "127.0.0.1", "::1"}:
            self.skipTest("Live Olist tests require a local PostgreSQL host.")
        if settings.db_user != "analyst_agent":
            self.fail("Live Olist tests require the analyst_agent runtime role.")
        self.target = resolve_profile("olist", settings)
        self.context = database_context(
            self.target.database_name, database_url=self.target.url
        )
        self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)

    def test_identity_schema_comments_and_safe_sql(self):
        with get_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT current_user, current_database()")
                self.assertEqual(
                    cursor.fetchone(), ("analyst_agent", "agentic_analyst_olist")
                )

        schema = get_schema.invoke({})
        tables = {line.removeprefix("TABLE: ") for line in schema.splitlines()
                  if line.startswith("TABLE: ")}
        self.assertEqual(tables, {
            "customers", "orders", "products", "sellers", "order_items",
            "payments", "reviews", "category_translation",
        })
        self.assertNotIn("geolocation", schema)

        customers = describe_table.invoke({"table_name": "customers"})
        orders = describe_table.invoke({"table_name": "orders"})
        order_items = describe_table.invoke({"table_name": "order_items"})
        payments = describe_table.invoke({"table_name": "payments"})
        self.assertIn("Order-scoped customer record identifier", customers)
        self.assertIn("same customer across multiple orders", customers)
        self.assertIn("Delivered is the closest equivalent to a completed order", orders)
        self.assertIn("Item price excluding freight_value", order_items)
        self.assertIn("Freight or shipping amount for this item", order_items)
        self.assertEqual(order_items.count("must not infer or display a currency symbol"), 2)
        self.assertIn("Amount for one payment row", payments)
        self.assertIn("multiple payment rows", payments)
        self.assertIn("must not infer or display a currency symbol", payments)

        result = execute_sql.invoke({
            "query": "SELECT current_user, current_database(), "
                     "current_setting('transaction_read_only') AS read_only, "
                     "COUNT(*) AS orders FROM public.orders"
        })
        self.assertIn("analyst_agent | agentic_analyst_olist | on | 99441", result)
        rejected = execute_sql.invoke({"query": "DELETE FROM public.orders"})
        self.assertTrue(rejected.startswith("Query rejected:"))


if __name__ == "__main__":
    unittest.main()
