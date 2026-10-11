import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, text

from app import db
from app.db import _report_ownership_statements, _schema_constraint_plan
from app.infrastructure.orm import Base, _MIGRATED_COLUMNS


class SchemaContractTests(unittest.TestCase):
    def test_migrated_columns_keep_their_fresh_schema_constraints(self):
        for table_name, column_name, _ddl in _MIGRATED_COLUMNS:
            column = Base.metadata.tables[table_name].c[column_name]
            if "NOT NULL" in _ddl.upper():
                self.assertFalse(column.nullable, f"{table_name}.{column_name}")
                self.assertIsNotNone(column.server_default, f"{table_name}.{column_name}")

    def test_fact_and_claim_assertion_semantics_are_additive_fields(self):
        for table_name in ("facts", "claims"):
            for column_name in ("assertion_mode", "assertion_mode_raw"):
                column = Base.metadata.tables[table_name].c[column_name]
                self.assertFalse(column.nullable)
                self.assertEqual(str(column.server_default.arg), "unknown")
        migration = {(table, column): ddl for table, column, ddl in _MIGRATED_COLUMNS
                     if column in {"assertion_mode", "assertion_mode_raw"}}
        self.assertEqual(set(migration), {
            ("facts", "assertion_mode"), ("claims", "assertion_mode"),
            ("facts", "assertion_mode_raw"), ("claims", "assertion_mode_raw"),
        })
        self.assertTrue(all("DEFAULT 'unknown'" in ddl for ddl in migration.values()))

    def test_open_semantic_profile_storage_exists_on_source_and_derived_records(self):
        expected = {
            "facts": ("semantic_profile_json",),
            "claims": ("semantic_profile_json",),
            "inferences": ("semantic_profile_json", "source_semantics_json"),
        }
        for table_name, columns in expected.items():
            for column_name in columns:
                column = Base.metadata.tables[table_name].c[column_name]
                self.assertFalse(column.nullable, f"{table_name}.{column_name}")
                self.assertIsNotNone(column.server_default, f"{table_name}.{column_name}")
        migrated = {(table, column) for table, column, _ddl in _MIGRATED_COLUMNS}
        self.assertTrue({
            (table, column)
            for table, columns in expected.items()
            for column in columns
        } <= migrated)

    def test_reports_have_a_direct_task_owner(self):
        owner = Base.metadata.tables["reports"].c["task_id"]
        self.assertFalse(owner.nullable)
        self.assertIsNotNone(owner.server_default)

    def test_existing_nullable_default_is_repaired_in_safe_order(self):
        statements = _schema_constraint_plan({
            "facts": {
                "fact_type": {"nullable": True, "default": None},
            },
        })
        self.assertEqual(
            statements[:3],
            [
                'UPDATE "facts" SET "fact_type" = \'unknown\' '
                'WHERE "fact_type" IS NULL',
                'ALTER TABLE "facts" ALTER COLUMN "fact_type" '
                "SET DEFAULT 'unknown'",
                'ALTER TABLE "facts" ALTER COLUMN "fact_type" SET NOT NULL',
            ],
        )

    def test_matching_existing_constraints_are_not_rewritten(self):
        statements = _schema_constraint_plan({
            "facts": {
                "fact_type": {"nullable": False, "default": "'unknown'::text"},
            },
        })
        self.assertEqual(statements, [])

    def test_legacy_open_assertion_labels_are_preserved_during_protocol_migration(self):
        engine = create_engine("sqlite:///:memory:")
        with engine.begin() as conn:
            conn.execute(text(
                "CREATE TABLE facts (id INTEGER PRIMARY KEY, assertion_mode TEXT NOT NULL)"
            ))
            conn.execute(text(
                "CREATE TABLE claims (id INTEGER PRIMARY KEY, assertion_mode TEXT NOT NULL)"
            ))
            conn.execute(text("INSERT INTO facts VALUES (1, '设计目标')"))
            conn.execute(text("INSERT INTO claims VALUES (1, '实施建议')"))
        with patch.object(db, "_get_engine", lambda: engine):
            db._migrate_columns()

        with engine.connect() as conn:
            fact = conn.execute(text(
                "SELECT assertion_mode, assertion_mode_raw FROM facts WHERE id=1"
            )).one()
            claim = conn.execute(text(
                "SELECT assertion_mode, assertion_mode_raw FROM claims WHERE id=1"
            )).one()
        assert tuple(fact) == ("unknown", "设计目标")
        assert tuple(claim) == ("unknown", "实施建议")

    def test_unexpected_existing_default_is_removed(self):
        statements = _schema_constraint_plan({
            "reports": {
                "title": {"nullable": False, "default": "''::text"},
            },
        })
        self.assertEqual(
            statements,
            ['ALTER TABLE "reports" ALTER COLUMN "title" DROP DEFAULT'],
        )

    def test_serial_primary_key_default_is_preserved(self):
        statements = _schema_constraint_plan({
            "facts": {
                "id": {
                    "nullable": False,
                    "default": "nextval('facts_id_seq'::regclass)",
                },
            },
        })
        self.assertEqual(statements, [])

    def test_report_ownership_audit_is_part_of_the_schema(self):
        audit = Base.metadata.tables["report_ownership_audits"]
        self.assertIn("report_id", audit.c)
        self.assertIn("status", audit.c)
        self.assertIn("candidate_task_ids", audit.c)
        self.assertTrue(any(
            fk.target_fullname == "reports.id" for fk in audit.c.report_id.foreign_keys
        ))

    def test_report_ownership_migration_records_ambiguous_and_orphaned_rows(self):
        backfill, audit = _report_ownership_statements()
        self.assertIn("HAVING COUNT(DISTINCT BTRIM(task_id)) = 1", backfill)
        self.assertIn("'ambiguous'", audit)
        self.assertIn("'orphaned'", audit)
        self.assertIn("ON CONFLICT (report_id)", audit)


class PlanReviewStateTests(unittest.TestCase):
    def test_plan_id_alone_does_not_create_phantom_plan_artifacts(self):
        from app.interaction import _plan_review_state

        self.assertEqual(_plan_review_state({"plan_version": 3}), (False, False, 3))

    def test_plan_presence_and_version_are_read_from_the_current_row(self):
        from app.interaction import _plan_review_state

        state = _plan_review_state({
            "analysis_plan_json": '{"dimensions": ["scope"]}',
            "final_plan_json": '{"chapter_plans": [{"title": "A"}]}',
            "plan_version": 4,
        })
        self.assertEqual(state, (True, True, 4))


if __name__ == "__main__":
    unittest.main()
