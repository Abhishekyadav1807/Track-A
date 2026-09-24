# Track A Handover — ClearLedger Register Repair

## 1. Chosen Track

- **Name:** Abhishek Yadav
- **Email used for application:** abhishek74580y@gmail.com
- **Chosen track:** Track A — Product Engineering
- **Why this track:** I chose Track A because I enjoy debugging existing software, understanding business rules, making focused changes, and verifying that the changes preserve existing behavior.
- **Approximate total time:** Approximately 4 hours, including investigation, implementation, testing, browser verification, and handover preparation.

## 2. What I Delivered

I repaired the six seeded ClearLedger defects:

1. **Invoice idempotency/conflict handling — `ledger/storage.py`**
   - Re-importing an identical invoice is skipped without changing totals.
   - Conflicting invoice details are rejected while preserving the original.
   - Added a unique `(customer_id, invoice_number)` index.

2. **Payment matching — `ledger/matching.py`**
   - Payments now match strictly by `(customer_id, invoice_number)`.
   - Equal invoice amounts cannot cause an incorrect match.
   - Unmatched payments remain retained as unmatched.

3. **Independent CSV row processing — `ledger/importing.py`**
   - Rows are processed independently.
   - Invalid rows are rejected with their CSV line number and reason while valid rows continue importing.

4. **Open/paid invoice filtering — `ledger/reporting.py`**
   - `open` returns positive balances.
   - `paid` returns zero/negative balances.
   - `all` returns both.

5. **Money export precision — `ledger/reporting.py`**
   - Replaced floating-point truncation with two-decimal formatting so values such as `19.99` export correctly.

6. **Browser import feedback — `web/app.js`**
   - HTTP failures no longer appear as successful imports.
   - The UI reports imported, skipped, and rejected counts and rejection details.

### Additional improvement

I added operator-facing import feedback for **unmatched payments and skipped duplicates**. Payment imports report unmatched references, while duplicate re-imports show which rows were safely skipped.

## 3. Run and Verify

The project uses Python's standard-library `unittest`; no third-party dependency installation is required.

### Full test suite

```powershell
py -m unittest discover -s tests -v
```

Result:

```text
Ran 17 tests in 0.326s

OK
```

This includes the original 5 smoke tests and 12 new regression tests.

### Restore the supplied baseline

```powershell
py restore_fixture.py --replace
```

Verified baseline:

```text
Invoices: 9
Payments: 5
Open invoices: 7
Paid invoices: 2
Outstanding INR: 3698.19
Unmatched payments: 1
```

### Run the application

```powershell
py app.py
```

Then open:

```text
http://127.0.0.1:8787
```

Manual browser verification covered the required import feedback:

- Mixed invoice import: **2 imported, 0 skipped, 1 rejected**, with the rejected CSV line and reason displayed.
- Re-import of the same file: **0 imported, 2 skipped, 1 rejected**, with duplicate rows identified as skipped.
- Invalid-header import: server error displayed instead of a false success message.
- Payment import: **3 imported, 0 skipped, 0 rejected**, with **1 unmatched payment** reported to the operator.

Persistence was also verified by importing records, closing/reopening the database connection, and confirming the records remained present.

## 4. Evidence and Limits

The supplied fixture was restored and preserved. The automated suite verifies the repaired behaviors, edge cases, baseline metrics, and persistence.

Manual browser verification was performed after starting the application locally. The final working database was restored to the supplied baseline before submission.

Known production considerations are outside this assessment's scope, including high-concurrency SQLite writes, retroactive rematching of previously unmatched payments, and more advanced payment allocation/audit requirements.

## 5. Changed-Input / Before-and-After Evidence

### Mixed CSV

A CSV containing valid and invalid rows previously caused the import to fail as a whole. After the repair, valid rows are imported and the invalid row is rejected with its line number and reason.

### Duplicate invoice

Previously, re-importing an existing invoice could create another record. After the repair, the identical row is skipped and the existing totals remain unchanged.

### Equal-amount invoices

Two invoices can have the same amount. Previously, amount matching could attach a payment to the wrong invoice. After the repair, only the customer/invoice identity determines the match.

## 6. AI / Tool-Use Summary

No AI tools or AI assistants were used to implement or verify the solution.

## 7. Remaining Work

No assessment requirement remains unfinished.

