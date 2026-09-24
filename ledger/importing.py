import csv
import io
import sqlite3
from .validation import HEADERS, normalize
from .storage import insert_invoice, insert_payment
from .matching import find_invoice


def import_csv(db, text, kind):
    if kind not in HEADERS:
        raise ValueError('Unknown import kind')
    reader = csv.DictReader(io.StringIO(text.lstrip('\ufeff')))
    if reader.fieldnames != HEADERS[kind]:
        raise ValueError('Expected CSV header: ' + ','.join(HEADERS[kind]))
    customers = {r[0] for r in db.execute('SELECT customer_id FROM customers')}
    result = {
        'imported': 0,
        'skipped': 0,
        'rejected': 0,
        'errors': [],
        'unmatched': 0,
        'unmatched_items': [],
        'skipped_items': [],
    }
    with db:
        for row in reader:
            line = reader.line_num
            try:
                normalized = normalize(row, kind, customers)
                if kind == 'invoices':
                    outcome = insert_invoice(db, normalized)
                    if outcome == 'skipped':
                        result['skipped_items'].append({
                            'line': line,
                            'customer_id': normalized['customer_id'],
                            'invoice_number': normalized['invoice_number'],
                        })
                else:
                    matched_id = find_invoice(db, normalized)
                    outcome = insert_payment(db, normalized, matched_id)
                    if outcome == 'imported' and matched_id is None:
                        result['unmatched'] += 1
                        result['unmatched_items'].append({
                            'line': line,
                            'payment_id': normalized['payment_id'],
                            'customer_id': normalized['customer_id'],
                            'invoice_number': normalized['invoice_number'],
                        })
                    elif outcome == 'skipped':
                        result['skipped_items'].append({
                            'line': line,
                            'payment_id': normalized['payment_id'],
                        })
                result[outcome] += 1
            except (ValueError, sqlite3.IntegrityError) as exc:
                result['rejected'] += 1
                result['errors'].append({'line': line, 'reason': str(exc)})
    return result
