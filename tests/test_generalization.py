"""Offline SaaS suite checks, plus explicitly enabled read-only DB checks."""

import copy
import json
import os
import re
import unittest
from collections import Counter
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, Mock, patch

from dotenv import load_dotenv
from langchain.messages import AIMessage

from app.agent import SYSTEM_INSTRUCTIONS, ToolCallRecord, run_agent
from app.database import get_connection
from app.tools import _query_validation_error, calculator, describe_table, execute_sql, get_schema
from app.trace import read_trace
from eval.generalization.verify import (
    CASE_PATH, DATABASE_NAME, TABLE_COUNTS, derive_expected_results,
    execute_verification_sql, require_database, save_verified_cases, verify_database,
)
from eval.generalization_runner import run_generalization
from eval.runner import compare_result, load_cases


class GeneralizationCaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = load_cases(CASE_PATH)

    def test_count_unique_ids_and_difficulties(self):
        self.assertEqual(len(self.cases), 16)
        self.assertEqual(len({case['id'] for case in self.cases}), 16)
        self.assertEqual(len({case['question'] for case in self.cases}), 16)
        self.assertEqual(Counter(case['difficulty'] for case in self.cases),
                         {'easy': 4, 'medium': 6, 'hard': 6})
        self.assertTrue(set(case['id'] for case in self.cases).isdisjoint(
            case['id'] for case in load_cases()))

    def test_required_fields_read_only_sql_and_expected_structure(self):
        required = {'id', 'question', 'difficulty', 'category', 'requires', 'reference_sql',
                    'expected_result', 'expected_columns', 'comparison', 'ground_truth_verified'}
        for case in self.cases:
            with self.subTest(case=case['id']):
                self.assertTrue(required <= case.keys())
                self.assertRegex(case['id'], r'^saas_[a-z0-9_]+$')
                self.assertTrue(case['question'] and case['category'] and case['requires'])
                self.assertEqual(len(case['requires']), len(set(case['requires'])))
                self.assertIsNone(_query_validation_error(case['reference_sql']))
                self.assertTrue(case['expected_columns'])
                self.assertEqual(len(case['expected_columns']), len(set(case['expected_columns'])))
                self.assertIsInstance(case['ground_truth_verified'], bool)
                if not case['ground_truth_verified']:
                    # Until migration, no invented business values are acceptable.
                    self.assertIsNone(case['expected_result'])
                elif case['comparison']['result_type'] == 'scalar':
                    self.assertIsInstance(case['expected_result'], (int, float, str))
                else:
                    expected = case['expected_result']
                    self.assertEqual(set(expected), {'columns', 'rows'})
                    self.assertEqual(expected['columns'], case['expected_columns'])
                    self.assertTrue(all(len(row) == len(expected['columns']) for row in expected['rows']))

    def test_comparison_contracts_using_synthetic_shapes_or_verified_values(self):
        for case in self.cases:
            with self.subTest(case=case['id']):
                contract = case['comparison']
                self.assertIn(contract['result_type'], {'table', 'scalar'})
                if case['ground_truth_verified']:
                    expected = case['expected_result']
                elif contract['result_type'] == 'scalar':
                    expected = 7  # Unit-test value only; never saved in the suite.
                else:
                    expected = {'columns': case['expected_columns'],
                                'rows': [['fixture-only'] * len(case['expected_columns'])]}
                actual = expected if isinstance(expected, dict) else {'columns': ['count'], 'rows': [[expected]]}
                self.assertTrue(compare_result(actual, expected, comparison=contract)[0])
                if contract['result_type'] == 'table':
                    self.assertEqual(contract['column_matching'], 'position')
                    self.assertEqual(contract['required_columns'], case['expected_columns'])
                if 'top_k' in case['requires']:
                    self.assertEqual(contract['row_order'], 'strict')
                    self.assertEqual(contract['row_match'], 'prefix')
                    self.assertIn('tie', case['question'].lower())
                    self.assertIn('ORDER BY', case['reference_sql'])
                    self.assertIn('LIMIT', case['reference_sql'])
                if 'zero_result' in case['requires']:
                    self.assertNotEqual(contract.get('row_match'), 'prefix')
                    if case['ground_truth_verified']:
                        self.assertEqual(expected['rows'], [])

    def test_meaningful_join_coverage(self):
        tags = Counter(tag for case in self.cases for tag in case['requires'])
        self.assertGreaterEqual(tags['join_2'], 3)
        self.assertGreaterEqual(tags['join_3'], 4)
        self.assertGreaterEqual(tags['join_4'], 1)
        for case in self.cases:
            sql = case['reference_sql'].lower()
            tables = set(re.findall(r'public\.([a-z_]+)', sql))
            for tag, minimum in [('join_2', 2), ('join_3', 3), ('join_4', 4)]:
                if tag in case['requires']:
                    self.assertGreaterEqual(len(tables), minimum, case['id'])
                    self.assertIn(' join ', sql)
        four_table = next(case for case in self.cases if 'join_4' in case['requires'])
        self.assertIn('SELECT DISTINCT s.account_id', four_table['reference_sql'])
        self.assertIn('Count each ticket once', four_table['question'])

    def test_temporal_semantic_and_zero_result_coverage(self):
        tags = Counter(tag for case in self.cases for tag in case['requires'])
        for tag in ('count', 'filter', 'distinct', 'group_by', 'sum', 'avg', 'temporal',
                    'ranking', 'top_k', 'zero_result', 'business_semantics', 'metadata',
                    'revenue', 'multi_step', 'null_handling'):
            self.assertGreater(tags[tag], 0, tag)
        self.assertGreaterEqual(tags['temporal'], 3)
        mrr = [case for case in self.cases if case['category'] == 'mrr']
        self.assertEqual(len(mrr), 2)
        for case in mrr:
            self.assertIn('s.seats * p.monthly_price_per_seat', case['reference_sql'])
            self.assertIn("s.status = 'active'", case['reference_sql'])
            self.assertIn('metadata', case['requires'])
        monthly = next(case for case in self.cases if case['id'] == 'saas_paid_invoice_count_by_month')
        self.assertEqual(monthly['comparison']['time_granularity'], 'month')
        self.assertIn('calendar month', monthly['question'])

    def test_actual_agent_prompt_and_tool_descriptions_have_no_saas_identifiers(self):
        forbidden = r'\b(accounts|plans|subscriptions|invoices|support_tickets|monthly_price_per_seat)\b'
        self.assertIsNone(re.search(forbidden, SYSTEM_INSTRUCTIONS, re.I))
        for tool in (calculator, get_schema, describe_table, execute_sql):
            self.assertIsNone(re.search(forbidden, tool.description, re.I))
        # Also inspect the exact SystemMessage supplied by the real loop.
        model = Mock()
        model.bind_tools.return_value = model
        model.invoke.return_value = AIMessage(content='Offline answer.')
        with TemporaryDirectory() as directory, patch('app.trace.RUNS_DIR', Path(directory)), \
             patch('app.agent.get_llm', return_value=model):
            run_agent('An offline question.', verbose=False)
        instructions = model.invoke.call_args.args[0][0].content
        self.assertIn(SYSTEM_INSTRUCTIONS, instructions)
        self.assertIsNone(re.search(forbidden, instructions, re.I))


class GeneralizationRunnerTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.runs = Path(directory.name)
        patcher = patch('app.trace.RUNS_DIR', self.runs)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_pending_ground_truth_stops_before_agent_or_database(self):
        pending = copy.deepcopy(load_cases(CASE_PATH))
        pending[0].update(expected_result=None, ground_truth_verified=False)
        agent, database = Mock(), Mock()
        with self.assertRaisesRegex(ValueError, 'ground truth is pending'):
            run_generalization(pending, agent, database)
        agent.assert_not_called()
        database.assert_not_called()

    def test_wrong_database_stops_before_agent(self):
        case = {**load_cases(CASE_PATH)[0], 'expected_result': 7, 'ground_truth_verified': True}
        database = Mock(return_value={'columns': ['database_name'], 'rows': [['agentic_analyst']]})
        agent = Mock()
        with self.assertRaisesRegex(ValueError, 'process DB_NAME'):
            run_generalization([case], agent, database)
        agent.assert_not_called()

    def test_reuses_evaluator_and_links_one_trace_without_ground_truth(self):
        # Deliberately unrelated unit-test query and value, not SaaS ground truth.
        case = {**load_cases(CASE_PATH)[0], 'expected_result': 7, 'ground_truth_verified': True,
                'reference_sql': 'SELECT evaluator_only_secret_reference'}
        seen_questions = []

        def fake_agent(question, *, trace, verbose):
            seen_questions.append(question)
            self.assertFalse(verbose)
            self.assertEqual(trace.source, 'eval_generalization')
            self.assertEqual(trace.case_id, case['id'])
            trace.model_turns = 2
            trace.tool_calls.append(ToolCallRecord(
                'execute_sql', {'query': 'SELECT 7 AS count'},
                'COLUMNS:\ncount\nROWS:\n7\n(1 row)', 'test-sql-id'))
            return 'The count is 7.'

        def fake_database(query):
            if query == 'SELECT current_database() AS database_name':
                return {'columns': ['database_name'], 'rows': [[DATABASE_NAME]]}
            self.assertEqual(query, 'SELECT 7 AS count')
            return {'columns': ['count'], 'rows': [[7]]}

        with patch('app.agent.get_llm') as gemini:
            report = run_generalization([case], fake_agent, fake_database)
        gemini.assert_not_called()
        self.assertEqual(seen_questions, [case['question']])
        self.assertEqual(report['summary']['passed'], 1)
        self.assertEqual(report['summary']['total_sql_attempts'], 1)
        result = report['cases'][0]
        self.assertNotIn('tool_calls', result)
        self.assertIsNotNone(result['run_id'])
        saved = read_trace(Path(result['trace_path']))
        self.assertEqual(saved['run_id'], result['run_id'])
        self.assertEqual(saved['source'], 'eval_generalization')
        self.assertEqual(saved['case_id'], case['id'])
        self.assertEqual(len(list(self.runs.glob('*.json'))), 1)
        raw = json.dumps(saved)
        for forbidden in ('reference_sql', 'expected_result', 'evaluator_only_secret_reference',
                          'comparison', 'ground_truth_verified', 'failure_reason'):
            self.assertNotIn(forbidden, raw)


class GeneralizationVerificationTests(unittest.TestCase):
    def mock_connection(self):
        connection = MagicMock()
        connection.__enter__.return_value = connection
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = ('analyst_agent', DATABASE_NAME)
        cursor.description = [Mock(name='column')]
        cursor.description[0].name = 'value'
        cursor.fetchmany.return_value = [(7,)]
        return connection, cursor

    def test_select_and_with_use_direct_read_only_connection_timeout_and_rollback(self):
        for query in ('SELECT 7 AS value', 'WITH example AS (SELECT 7 AS value) SELECT value FROM example'):
            with self.subTest(query=query):
                connection, cursor = self.mock_connection()
                with patch('eval.generalization.verify.get_connection', return_value=connection), \
                     patch('app.tools.execute_sql') as tool, patch('app.agent.get_llm') as gemini, \
                     patch('eval.runner.AgentTrace') as trace:
                    result = execute_verification_sql(query)
                self.assertEqual(result, {'columns': ['value'], 'rows': [[7]]})
                statements = [call.args[0] for call in cursor.execute.call_args_list]
                self.assertEqual(statements[0], 'SET TRANSACTION READ ONLY')
                self.assertIn('statement_timeout', statements[1])
                self.assertEqual(cursor.execute.call_args_list[1].args[1], ('5000',))
                self.assertEqual(statements[-1], query)
                cursor.fetchmany.assert_called_once_with(101)
                connection.rollback.assert_called_once()
                connection.__exit__.assert_called_once()
                tool.invoke.assert_not_called()
                gemini.assert_not_called()
                trace.assert_not_called()

    def test_write_ddl_and_transaction_control_rejected_before_connecting(self):
        for query in ('INSERT INTO example VALUES (1)', 'DELETE FROM example',
                      'CREATE TABLE example (id integer)', 'DROP TABLE example',
                      'WITH removed AS (DELETE FROM example RETURNING *) SELECT * FROM removed',
                      'SET TRANSACTION READ ONLY', 'BEGIN', 'COMMIT'):
            with self.subTest(query=query), patch('eval.generalization.verify.get_connection') as connect:
                with self.assertRaises(ValueError):
                    execute_verification_sql(query)
                connect.assert_not_called()
        case = {**load_cases(CASE_PATH)[0], 'reference_sql': 'DELETE FROM public.subscriptions'}
        database = Mock()
        with self.assertRaisesRegex(ValueError, case['id']):
            derive_expected_results([case], database)
        database.assert_not_called()

    def test_privilege_names_are_parameters_not_blocked_sql_tokens(self):
        query = "SELECT has_table_privilege(current_user, 'public.accounts', %s)"
        connection, cursor = self.mock_connection()
        with patch('eval.generalization.verify.get_connection', return_value=connection):
            execute_verification_sql(query, ('INSERT',))
        self.assertEqual(cursor.execute.call_args.args, (query, ('INSERT',)))
        # Preserve the existing conservative agent guardrail, including literals.
        self.assertIsNotNone(_query_validation_error(query.replace('%s', "'INSERT'")))
        with patch('app.tools.get_connection') as connect:
            self.assertIn('only SELECT or WITH', execute_sql.invoke({'query': 'SET TRANSACTION READ ONLY'}))
            connect.assert_not_called()

    def test_wrong_role_database_and_query_failure_close_with_rollback(self):
        for identity in (('postgres', DATABASE_NAME), ('analyst_agent', 'agentic_analyst')):
            with self.subTest(identity=identity):
                connection, cursor = self.mock_connection()
                cursor.fetchone.return_value = identity
                with patch('eval.generalization.verify.get_connection', return_value=connection):
                    with self.assertRaises(ValueError):
                        execute_verification_sql('SELECT 7 AS value')
                connection.rollback.assert_called_once()
                connection.__exit__.assert_called_once()
                self.assertNotIn('SELECT 7 AS value', [call.args[0] for call in cursor.execute.call_args_list])
        connection, cursor = self.mock_connection()
        cursor.execute.side_effect = [None, None, None, RuntimeError('offline query failure')]
        with patch('eval.generalization.verify.get_connection', return_value=connection):
            with self.assertRaises(RuntimeError):
                execute_verification_sql('SELECT 7 AS value')
        connection.rollback.assert_called_once()
        connection.__exit__.assert_called_once()

    def test_missing_columns_or_excess_rows_do_not_produce_ground_truth(self):
        for description, rows in ((None, []), ([Mock(name='column')], [(7,)] * 101)):
            connection, cursor = self.mock_connection()
            cursor.description = description
            cursor.fetchmany.return_value = rows
            with patch('eval.generalization.verify.get_connection', return_value=connection):
                with self.assertRaises(ValueError):
                    execute_verification_sql('SELECT 7 AS value')
            connection.rollback.assert_called_once()

    def test_process_database_override_wins_over_dotenv(self):
        with TemporaryDirectory() as directory:
            dotenv_path = Path(directory) / '.env'
            dotenv_path.write_text('DB_NAME=agentic_analyst\n', encoding='utf-8')
            settings = {'DB_NAME': DATABASE_NAME, 'DB_HOST': 'localhost', 'DB_PORT': '5432',
                        'DB_USER': 'analyst_agent', 'DB_PASSWORD': 'unit-test-placeholder'}
            with patch.dict(os.environ, settings, clear=True), \
                 patch('app.database.load_dotenv', side_effect=lambda: load_dotenv(dotenv_path)), \
                 patch('app.database.psycopg.connect') as connect:
                get_connection()
                self.assertEqual(connect.call_args.kwargs['dbname'], DATABASE_NAME)
                self.assertEqual(connect.call_args.kwargs['user'], 'analyst_agent')
                self.assertEqual(dotenv_path.read_text(encoding='utf-8'), 'DB_NAME=agentic_analyst\n')

    def test_ground_truth_is_derived_from_query_results_not_guessed(self):
        cases = copy.deepcopy(load_cases(CASE_PATH))
        query_to_case = {case['reference_sql']: case for case in cases}

        def fake_database(query):
            case = query_to_case[query]
            columns = case['expected_columns']
            rows = [] if 'zero_result' in case['requires'] else [
                [Decimal('7.25')] if case['comparison']['result_type'] == 'scalar'
                else ['fixture-only'] * len(columns)]
            return {'columns': columns, 'rows': rows}

        derived = derive_expected_results(cases, fake_database)
        self.assertEqual(len(derived), 16)
        self.assertTrue(all(case['ground_truth_verified'] for case in derived))
        self.assertEqual(derived[0]['expected_result'], 7.25)
        self.assertIsNot(derived[0], cases[0])
        zero = next(case for case in derived if 'zero_result' in case['requires'])
        self.assertEqual(zero['expected_result']['rows'], [])

    def test_reference_error_cannot_publish_a_partial_suite(self):
        cases = copy.deepcopy(load_cases(CASE_PATH))
        database = Mock(side_effect=[{'columns': cases[0]['expected_columns'], 'rows': [[7]]},
                                     RuntimeError('offline failure')])
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'cases.json'
            path.write_text('original', encoding='utf-8')
            with self.assertRaises(RuntimeError):
                save_verified_cases(derive_expected_results(cases, database), path)
            self.assertEqual(path.read_text(encoding='utf-8'), 'original')
            self.assertEqual(len(list(Path(directory).iterdir())), 1)

    def test_verification_rejects_bad_reference_shapes_and_zero_result_drift(self):
        scalar = load_cases(CASE_PATH)[0]
        for result in ({'columns': ['wrong'], 'rows': [[7]]},
                       {'columns': scalar['expected_columns'], 'rows': [[7], [8]]}):
            with self.assertRaises(ValueError):
                derive_expected_results([scalar], Mock(return_value=result))
        zero = next(case for case in load_cases(CASE_PATH) if 'zero_result' in case['requires'])
        with self.assertRaisesRegex(ValueError, 'zero-result'):
            derive_expected_results([zero], Mock(return_value={
                'columns': zero['expected_columns'], 'rows': [['fixture-only', 9]]}))

    def test_atomic_publication_requires_verified_results(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'cases.json'
            path.write_text('original', encoding='utf-8')
            with self.assertRaises(ValueError):
                save_verified_cases([{'ground_truth_verified': False}], path)
            self.assertEqual(path.read_text(encoding='utf-8'), 'original')
            verified = [{'ground_truth_verified': True, 'expected_result': 7}]
            save_verified_cases(verified, path)
            self.assertEqual(json.loads(path.read_text(encoding='utf-8')), verified)
            self.assertFalse(path.with_suffix('.json.tmp').exists())


@unittest.skipUnless(os.getenv('RUN_GENERALIZATION_DB_TESTS') == '1',
                     'New database verification awaits the admin migration; enable explicitly afterward.')
class GeneralizationDatabaseTests(unittest.TestCase):
    def setUp(self):
        # Scope the override to these tests; sales regression tests keep their DB.
        override = patch.dict(os.environ, {'DB_NAME': DATABASE_NAME})
        override.start()
        self.addCleanup(override.stop)

    def test_counts_constraints_comments_tools_and_effective_read_only_privileges(self):
        report = verify_database()
        self.assertEqual(report['counts'], TABLE_COUNTS)
        self.assertTrue(report['read_only_privileges'])
        self.assertEqual(len(report['constraints']), 9)

    def test_read_only_transaction_is_active_and_with_works_in_postgresql(self):
        with patch('app.agent.get_llm') as gemini, patch('app.tools.execute_sql') as tool, \
             patch('eval.runner.AgentTrace') as trace:
            self.assertEqual(execute_verification_sql(
                "SELECT current_setting('transaction_read_only') AS read_only")['rows'], [['on']])
            self.assertEqual(execute_verification_sql(
                'WITH example AS (SELECT 7 AS value) SELECT value FROM example')['rows'], [[7]])
        gemini.assert_not_called()
        tool.invoke.assert_not_called()
        trace.assert_not_called()

    def test_reference_sql_matches_verified_postgresql_ground_truth(self):
        require_database()
        cases = load_cases(CASE_PATH)
        self.assertTrue(all(case['ground_truth_verified'] for case in cases),
                        'Run python -m eval.generalization.verify --write-expected first.')
        verified = derive_expected_results(cases)
        for stored, fresh in zip(cases, verified):
            with self.subTest(case=stored['id']):
                actual = fresh['expected_result']
                if stored['comparison']['result_type'] == 'scalar':
                    actual = {'columns': stored['expected_columns'], 'rows': [[actual]]}
                self.assertTrue(compare_result(actual, stored['expected_result'],
                                               comparison=stored['comparison'])[0])


if __name__ == '__main__':
    unittest.main()
