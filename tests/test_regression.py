"""Regression test suite for Track A repairs:
- Invoice idempotency and conflict handling
- Payment matching strictly by identity (not amount)
- Unmatched payments retention
- Mixed valid/invalid CSV row independence
- Open / paid / all invoice filtering
- Exact two-decimal export without truncation
- Import result improvement (unmatched and skipped tracking)
- Fixture baseline preservation and persistence across restart
"""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from ledger import storage, reporting, importing, matching

ROOT = Path(__file__).resolve().parent.parent


class RegressionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / 'test.sqlite3'
        self.db = storage.connect(self.db_path)
        storage.seed(self.db)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_invoice_idempotency_duplicate_skipped(self):
        # Initial count is 6 from seed
        self.assertEqual(len(reporting.invoices(self.db)), 6)
        # Import exact same invoice as in seed (HARBOR, INV-100, 1250.00, 2026-09-01)
        csv_data = "customer_id,invoice_number,amount,due_date\nHARBOR,INV-100,1250.00,2026-09-01\n"
        result = importing.import_csv(self.db, csv_data, 'invoices')
        self.assertEqual(result['imported'], 0)
        self.assertEqual(result['skipped'], 1)
        self.assertEqual(result['rejected'], 0)
        self.assertEqual(len(result['skipped_items']), 1)
        self.assertEqual(result['skipped_items'][0]['invoice_number'], 'INV-100')
        # Totals and records must remain unchanged
        self.assertEqual(len(reporting.invoices(self.db)), 6)
        inv = storage.invoice_by_key(self.db, 'HARBOR', 'INV-100')
        self.assertEqual(inv['amount'], 1250.00)

    def test_invoice_conflict_rejected_original_preserved(self):
        # Attempt to import same (customer_id, invoice_number) with different amount
        csv_data = "customer_id,invoice_number,amount,due_date\nHARBOR,INV-100,9999.00,2026-09-01\n"
        result = importing.import_csv(self.db, csv_data, 'invoices')
        self.assertEqual(result['imported'], 0)
        self.assertEqual(result['skipped'], 0)
        self.assertEqual(result['rejected'], 1)
        self.assertEqual(len(result['errors']), 1)
        self.assertEqual(result['errors'][0]['line'], 2)
        self.assertIn('Invoice already exists with different details', result['errors'][0]['reason'])
        # Original invoice must be preserved
        inv = storage.invoice_by_key(self.db, 'HARBOR', 'INV-100')
        self.assertEqual(inv['amount'], 1250.00)

        # Attempt with different due date
        csv_data2 = "customer_id,invoice_number,amount,due_date\nHARBOR,INV-100,1250.00,2099-12-31\n"
        result2 = importing.import_csv(self.db, csv_data2, 'invoices')
        self.assertEqual(result2['rejected'], 1)
        inv2 = storage.invoice_by_key(self.db, 'HARBOR', 'INV-100')
        self.assertEqual(inv2['due_date'], '2026-09-01')

    def test_payment_matching_equal_amounts_different_identities(self):
        # HARBOR/INV-100 and MAPLE/INV-200 both have amount 1250.00
        # Post payment specifically for MAPLE/INV-200
        payment_csv = "payment_id,customer_id,invoice_number,amount\nPAY-M200,MAPLE,INV-200,1250.00\n"
        result = importing.import_csv(self.db, payment_csv, 'payments')
        self.assertEqual(result['imported'], 1)
        self.assertEqual(result['unmatched'], 0)

        # Verify MAPLE/INV-200 received the payment
        inv_maple = next(r for r in reporting.invoices(self.db) if r['customer_id'] == 'MAPLE' and r['invoice_number'] == 'INV-200')
        self.assertEqual(inv_maple['paid'], 1250.00)
        self.assertEqual(inv_maple['balance'], 0.00)
        self.assertEqual(inv_maple['status'], 'paid')

        # Verify HARBOR/INV-100 did NOT receive the payment
        inv_harbor = next(r for r in reporting.invoices(self.db) if r['customer_id'] == 'HARBOR' and r['invoice_number'] == 'INV-100')
        self.assertEqual(inv_harbor['paid'], 0.00)
        self.assertEqual(inv_harbor['balance'], 1250.00)
        self.assertEqual(inv_harbor['status'], 'open')

    def test_unmatched_payment_retention_and_visibility(self):
        # Payment for non-existent invoice
        payment_csv = "payment_id,customer_id,invoice_number,amount\nPAY-UNKNOWN,NORTH,NO-SUCH-INV,75.50\n"
        result = importing.import_csv(self.db, payment_csv, 'payments')
        self.assertEqual(result['imported'], 1)
        self.assertEqual(result['unmatched'], 1)
        self.assertEqual(result['unmatched_items'][0]['payment_id'], 'PAY-UNKNOWN')

        overview_data = reporting.overview(self.db)
        unmatched = overview_data['unmatched_payments']
        self.assertTrue(any(p['payment_id'] == 'PAY-UNKNOWN' for p in unmatched))
        # Ensure outstanding totals were not altered by unmatched payment
        self.assertEqual(overview_data['summary']['outstanding'], 3209.99)

    def test_payment_idempotency_and_conflict(self):
        # SEED-1 exists: HARBOR, INV-101, 300.00
        dup_csv = "payment_id,customer_id,invoice_number,amount\nSEED-1,HARBOR,INV-101,300.00\n"
        res = importing.import_csv(self.db, dup_csv, 'payments')
        self.assertEqual(res['skipped'], 1)

        # Conflicting payment details
        conflict_csv = "payment_id,customer_id,invoice_number,amount\nSEED-1,HARBOR,INV-101,500.00\n"
        res2 = importing.import_csv(self.db, conflict_csv, 'payments')
        self.assertEqual(res2['rejected'], 1)
        self.assertIn('Payment ID already exists with different details', res2['errors'][0]['reason'])

    def test_mixed_csv_row_independence(self):
        # Line 1: Header
        # Line 2: Valid invoice
        # Line 3: Invalid amount (malformed row)
        # Line 4: Valid invoice
        mixed_csv = (
            "customer_id,invoice_number,amount,due_date\n"
            "HARBOR,TEST-MIX-1,45.00,2026-09-20\n"
            "NORTH,TEST-MIX-BAD,invalid-amount,2026-09-20\n"
            "MAPLE,TEST-MIX-2,95.50,2026-09-21\n"
        )
        result = importing.import_csv(self.db, mixed_csv, 'invoices')
        self.assertEqual(result['imported'], 2)
        self.assertEqual(result['rejected'], 1)
        self.assertEqual(len(result['errors']), 1)
        self.assertEqual(result['errors'][0]['line'], 3)

        # Confirm valid rows were actually written to database
        self.assertIsNotNone(storage.invoice_by_key(self.db, 'HARBOR', 'TEST-MIX-1'))
        self.assertIsNotNone(storage.invoice_by_key(self.db, 'MAPLE', 'TEST-MIX-2'))
        self.assertIsNone(storage.invoice_by_key(self.db, 'NORTH', 'TEST-MIX-BAD'))

    def test_invalid_header_rejects_whole_import(self):
        bad_header_csv = "wrong,header,row\nHARBOR,INV-999,50.00,2026-09-01\n"
        with self.assertRaises(ValueError) as ctx:
            importing.import_csv(self.db, bad_header_csv, 'invoices')
        self.assertIn('Expected CSV header', str(ctx.exception))
        # Ensure nothing was written
        self.assertIsNone(storage.invoice_by_key(self.db, 'HARBOR', 'INV-999'))

    def test_invoice_open_paid_filtering(self):
        # Demo seed has 6 invoices:
        # INV-100: bal 1250 (open)
        # INV-200: bal 1250 (open)
        # INV-300: 19.99 - 10.00 = 9.99 (open)
        # INV-101: 300 - 300 = 0 (paid)
        # INV-201: bal 600 (open)
        # INV-301: bal 100 (open)
        # Total: 5 open, 1 paid
        all_inv = reporting.invoices(self.db, 'all')
        self.assertEqual(len(all_inv), 6)

        open_inv = reporting.invoices(self.db, 'open')
        self.assertEqual(len(open_inv), 5)
        self.assertTrue(all(r['status'] == 'open' and r['balance'] > 0 for r in open_inv))

        paid_inv = reporting.invoices(self.db, 'paid')
        self.assertEqual(len(paid_inv), 1)
        self.assertTrue(all(r['status'] == 'paid' and r['balance'] <= 0 for r in paid_inv))
        self.assertEqual(paid_inv[0]['invoice_number'], 'INV-101')

        # Invalid filter raises ValueError
        with self.assertRaises(ValueError):
            reporting.invoices(self.db, 'pending')

    def test_overpayment_handling(self):
        # NORTH INV-301 has amount 100.00. Overpay by 120.00
        pay_csv = "payment_id,customer_id,invoice_number,amount\nPAY-OVER,NORTH,INV-301,120.00\n"
        importing.import_csv(self.db, pay_csv, 'payments')
        inv = next(r for r in reporting.invoices(self.db) if r['invoice_number'] == 'INV-301')
        self.assertEqual(inv['paid'], 120.00)
        self.assertEqual(inv['balance'], -20.00)
        self.assertEqual(inv['status'], 'paid')

        # Overpayment must not reduce other invoices' outstanding amount
        # Other 4 open invoices: 1250 + 1250 + 9.99 + 600 = 3109.99
        summary = reporting.overview(self.db)['summary']
        self.assertEqual(summary['outstanding'], 3109.99)
        self.assertEqual(summary['open_count'], 4)

    def test_exact_two_decimal_export(self):
        # NORTH INV-300 has amount 19.99, paid 10.00, balance 9.99
        csv_text = reporting.export_csv(self.db)
        lines = [line.split(',') for line in csv_text.strip().splitlines()]
        header = lines[0]
        self.assertEqual(header, ['customer_id', 'invoice_number', 'amount', 'paid', 'balance', 'status'])
        inv_row = next(r for r in lines[1:] if r[0] == 'NORTH' and r[1] == 'INV-300')
        # Exact values: amount 19.99 (not 19.98!), paid 10.00, balance 9.99 (not 9.98!)
        self.assertEqual(inv_row[2], '19.99')
        self.assertEqual(inv_row[3], '10.00')
        self.assertEqual(inv_row[4], '9.99')
        self.assertEqual(inv_row[5], 'open')

    def test_restored_fixture_preservation_and_metrics(self):
        fixture_path = ROOT / 'fixtures' / 'existing-register.sqlite3'
        self.assertTrue(fixture_path.exists())
        fdb = storage.connect(fixture_path)
        try:
            overview = reporting.overview(fdb)
            summary = overview['summary']
            # Expected metrics from fixtures/README.md:
            # Invoices: 9, Payments: 5, Open invoices: 7, Outstanding: 3698.19, Unmatched: 1
            self.assertEqual(summary['invoice_count'], 9)
            self.assertEqual(summary['open_count'], 7)
            self.assertEqual(summary['outstanding'], 3698.19)

            unmatched = overview['unmatched_payments']
            self.assertEqual(len(unmatched), 1)
            self.assertEqual(unmatched[0]['payment_id'], 'KEEP-U1')
            self.assertEqual(unmatched[0]['amount'], 33.33)

            open_list = reporting.invoices(fdb, 'open')
            self.assertEqual(len(open_list), 7)

            paid_list = reporting.invoices(fdb, 'paid')
            self.assertEqual(len(paid_list), 2)
            self.assertEqual({r['invoice_number'] for r in paid_list}, {'INV-101', 'KEEP-702'})

            # Verify against expected-records.json
            with open(ROOT / 'fixtures' / 'expected-records.json', encoding='utf-8') as f:
                expected = json.load(f)
            self.assertEqual(len(expected['invoices']), 9)
            for exp_inv in expected['invoices']:
                act_inv = storage.invoice_by_key(fdb, exp_inv['customer_id'], exp_inv['invoice_number'])
                self.assertIsNotNone(act_inv)
                self.assertEqual(act_inv['amount'], float(exp_inv['amount']))
                self.assertEqual(act_inv['due_date'], exp_inv['due_date'])
        finally:
            fdb.close()

    def test_import_and_persistence_on_restored_fixture(self):
        # Copy existing register to a temporary database to simulate working register
        import shutil
        working_db = Path(self.tmp.name) / 'working.sqlite3'
        shutil.copy2(ROOT / 'fixtures' / 'existing-register.sqlite3', working_db)

        # Open connection and import a new invoice and new payment
        db1 = storage.connect(working_db)
        inv_csv = "customer_id,invoice_number,amount,due_date\nHARBOR,NEW-INV-1,250.00,2026-09-30\n"
        res_inv = importing.import_csv(db1, inv_csv, 'invoices')
        self.assertEqual(res_inv['imported'], 1)

        pay_csv = "payment_id,customer_id,invoice_number,amount\nNEW-PAY-1,HARBOR,NEW-INV-1,100.00\n"
        res_pay = importing.import_csv(db1, pay_csv, 'payments')
        self.assertEqual(res_pay['imported'], 1)
        self.assertEqual(res_pay['unmatched'], 0)

        # Close connection to simulate server shutdown
        db1.close()

        # Reopen connection to simulate server restart
        db2 = storage.connect(working_db)
        try:
            summary = reporting.overview(db2)['summary']
            # Previously: 9 invoices, 3698.19 outstanding, 7 open
            # With NEW-INV-1 (250.00 - 100.00 = 150.00 open):
            # Now: 10 invoices, 8 open, outstanding 3698.19 + 150.00 = 3848.19
            self.assertEqual(summary['invoice_count'], 10)
            self.assertEqual(summary['open_count'], 8)
            self.assertEqual(summary['outstanding'], 3848.19)

            new_inv = storage.invoice_by_key(db2, 'HARBOR', 'NEW-INV-1')
            self.assertIsNotNone(new_inv)
            self.assertEqual(new_inv['amount'], 250.00)

            # Ensure original fixture records are intact
            keep700 = storage.invoice_by_key(db2, 'HARBOR', 'KEEP-700')
            self.assertEqual(keep700['amount'], 456.78)
        finally:
            db2.close()


if __name__ == '__main__':
    unittest.main()
