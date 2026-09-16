import unittest

from app.db import _report_ownership_statements, _schema_constraint_plan
from app.infrastructure.orm import Base, _MIGRATED_COLUMNS


class SchemaContractTests(unittest.TestCase):
    def test_migrated_columns_keep_their_fresh_schema_constraints(self):
        for table_name, column_name, _ddl in _MIGRATED_COLUMNS:
            column = Base.metadata.tables[table_name].c[column_name]
            if "NOT NULL" in _ddl.upper():
                self.assertFalse(column.nullable, f"{table_name}.{column_name}")
                self.assertIsNotNone(column.server_default, f"{table_name}.{column_name}")

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
