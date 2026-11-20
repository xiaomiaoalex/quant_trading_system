# Phase 4 Independent Test Report

Status: **final round-3 controlled execution passed; overall=pass**. The round-1
review findings B1 (weak F1 rejection assertion) and B4 (stale source fingerprint)
are fixed. On the frozen final source, the revised offline suite is 13/13, the
PostgreSQL/process suite is 1/1, the no-DSN negative control fails directly with
no skip, Black/isort are exit 0, the accepted `trader/tests` full suite is
3702/3702 with zero failures/errors/skips, and the 12-file canonical source digest
is `cfcb8a1351d07dcaa03181ffc27c7fcaa07230d2fc40e30a8603c09a4ffe6682`. The
historical round-2 pass was not used as the round-3 verdict.

## 1. Identity and frozen inputs

| Field | Value |
|---|---|
| report_id | `phase4-independent-test-r3` |
| workflow_version | 3 |
| phase | 4 |
| role | tester |
| agent_id | `c6ff85ec-e215-4459-a617-44599682129a` |
| round | 3 |
| branch (declared) | `feat/foundation-refactor` |
| base_commit (declared) | `1b1564bc4ad0c14c9b035582d384713661d96861` |
| source_digest (final) | `cfcb8a1351d07dcaa03181ffc27c7fcaa07230d2fc40e30a8603c09a4ffe6682` |
| snapshot_path | `artifacts/foundation/phase4/final-source-snapshot.json` |
| authored_by | `c6ff85ec-e215-4459-a617-44599682129a` |
| executed_by | engineer controlled channel (round 3, final source) |
| analyzed_by | `c6ff85ec-e215-4459-a617-44599682129a` |

The round-1 and round-2 snapshots are historical. The tester independently
enumerated all twelve current changed source paths and hashed each with browser
WebCrypto; the resulting canonical digest is
`cfcb8a1351d07dcaa03181ffc27c7fcaa07230d2fc40e30a8603c09a4ffe6682`. The
pre-review `final-source-snapshot.json` value `d8e04b84…` was stale because the
engineer subsequently changed `trader/trading/backtest.py`,
`docs/INTERFACE_CONTRACTS.md`, `trader/tests/test_phase4_joint_backtest.py` and
`trader/tests/test_phase4_postgres.py`; the tester has regenerated
`final-source-snapshot.json` with the final 12 `(path, sha256)` pairs and the
same canonical rule: SHA-256 of the compact JSON array of sorted
`[path, sha256]` pairs (UTF-8, no spaces). The report itself is operational
metadata and is excluded from the source digest.

## 2. Independence method

Expected values below come only from:

- `docs/FOUNDATION_REFACTOR_PHASE0.md` phase 4, 4.1-4.10;
- `docs/INTERFACE_CONTRACTS.md` 14.2, 14.5 and 14.6;
- `docs/PROJECT_ARCHITECTURE.md` 8.2 (books, settlement and joint backtest);
- `artifacts/foundation/phase4/parent-phase4-work-order.md`;
- explicit hand arithmetic recorded in section 3.

No expected balance, fee, allocation, hash or order field is obtained by calling
the implementation under test. Test helpers build expected structures from the
contract wording and literal arithmetic. Contract-shaped fakes are limited to
market data, deterministic strategy scripts, stores and process hosts; they do not
reimplement admission, reservation, fee or equity rules.

## 3. Independent oracles and concrete arithmetic

### 3.1 Unequal books and the 100/900 refusal oracle (4.1, 4.7)

Initial account cash is 1000. Frozen allocations are A=100 and B=900, so the
allocation sum is exactly 1000 and `UNALLOCATED` is zero.

At a closed reference bar with price 100, zero slippage and `fee_bps=10`:

- A buying 2 units has notional 200. This exceeds A's 100 allocation regardless of
  whether the fee is added to the admission buffer. Expected result: A is refused
  with `BOOK_INSUFFICIENT_AVAILABLE_BALANCE`; A has no accepted command, order,
  fill, hold or ledger effect.
- B buying 1 unit has notional 100 and quote fee `100 * 10 / 10000 = 0.1`. Expected
  B cash after settlement is 799.9, base position 1, B equity at price 100 is
  899.9, and B remains able to trade.
- Conservation after B's fill: A cash 100 + B cash 799.9 + position value 100 = 999.9,
  equal to initial cash 1000 minus the 0.1 fee. Every asset is conserved; the fee
  is not double-counted and is not moved between books.
- The refusal must not alter B's balance, holds, available budget or ability to
  submit its order. Aggregate `Σ book balances + UNALLOCATED` must equal account
  totals asset by asset.

### 3.2 Per-book fees, partial fills and actual fee currency (4.2)

A direct shared-economic-entry scenario uses explicit fills, not the simulator's
convenience path:

- Book A BUY 2 units at 100, filled in two executions: 0.5 units with 0.05 USDT
  fee and 1.5 units with 0.15 USDT fee. Expected A quote fees = 0.20 USDT, A base
  position = 2.
- Book B SELL 1 unit at 100 with 0.10 USDT fee. Expected B quote fees = 0.10 USDT.
- `Σ book_fees[USDT] = 0.30`, `total_fees[USDT] = 0.30`.
- A base-fee case BUY 1 unit with fee 0.001 BTC: cash decreases by the 100 USDT
  notional, position is 0.999 BTC (fee currency is base and must not be deducted
  as quote a second time), and the BTC fee ledger entry is 0.001.

Per-book exports must expose allocation, equity, held, available, balances,
positions, fees and valuations; sum of book equity must equal account equity, and
each book's exposure is derived from that book only.

### 3.3 Frozen manifest and nested mutation (4.3, 4.4, 4.10)

A joint manifest is built with:

- `dataset_ids` supplied in non-sorted order;
- strategies supplied in non-sorted order, each with nested `params`;
- book allocations keyed by both strategy books and summing to `initial_cash`;
- `fee_bps`, `slippage_bps`, `risk_config`, `engine=event_runtime`,
  `purpose=admission_evidence`, `runtime_version=spot_bar_v2_shared`;
- `provenance` mapping each dataset to a trusted test hash.

Expected canonical properties:

1. frozen manifest contains every required field and a derived
   `book_id = book:{strategy_id}` for every strategy;
2. reordering datasets or strategies alone leaves `manifest_hash` unchanged;
3. changing any nested strategy param, allocation, cost, runtime version or
   provenance hash changes `manifest_hash`;
4. after submit, mutating the caller's nested objects does not change the stored
   manifest or its hash;
5. a fresh runtime/process and a retry of the same stored manifest produce the
   same canonical hash and byte-equivalent economic result;
6. altered stored manifest bytes are rejected before execution and the stored
   document is not silently rewritten to the new hash.

The test computes an independent canonical SHA-256 only if the contract's
canonical form is unambiguous; otherwise it asserts the invariants above and
records the canonicalisation gap as a limitation rather than substituting the
implementation's hash as the oracle.

### 3.4 Order semantics (4.5, 4.6)

The same economic intent is expressed through the live order-spec entry and the
research intent path:

- LIMIT BUY BTCUSDT, qty 0.5, limit 12345: expected type LIMIT, symbol BTCUSDT,
  side BUY, qty 0.5, price 12345; the limit price is not replaced by a bar close.
- MARKET BUY BTCUSDT, qty 0.25, closed bar close 100 at `close_time_ms`: expected
  type MARKET, symbol/side/qty unchanged, reference price 100, source
  `MARKET_REFERENCE_CLOSED_BAR`, reference id equal to the closed bar id, and
  reference time equal to the bar close time.
- A non-bar/ticker input and a LIMIT signal with missing/zero price must fail
  before an order or economic mutation.

Composition must enter `trader.trading.core` for admission, reservation and
execution accounting. The test checks the observable result fields and that the
shared pure functions are the ones invoked; it does not count a copied helper or
a second private balance calculation as compliance.

### 3.5 Backtest isolation from live state and orders (4.9)

A poisoned live store/account and a poisoned exchange transport are installed at
the process/test boundary. They raise on any read or write. The research
submission, execution and publication path must complete without touching them.
The result may contain only research-derived allocations, balances, positions and
fees; it must not contain or mutate the live account's economic state. No order
transport call is permitted.

### 3.6 Real Control/Worker/PostgreSQL (4.8, 4.10)

The process test creates a unique task-owned disposable database containing only
the `research` schema, submits a joint manifest through the Control research API,
and starts `trader.runtime.research_worker_main` as a separate OS process with only
that database's DSN. It polls the published job through a separate connection,
then re-reads the result after the worker exits.

Assertions:

- submission returns a durable `run_id`; the job reaches `SUCCEEDED`;
- the published result carries the same `manifest_hash`, `runtime_manifest_hash`,
  `dataset_ids`, `dataset_provenance` and `runtime_version` as the frozen input;
- per-book results and fills survive reload from PostgreSQL;
- no live account table/state is read or written and no exchange/order transport
  is configured in the worker;
- retry/re-execution of the same frozen manifest is deterministic; a conflicting
  stored manifest is rejected;
- no pytest skip or in-memory fake substitutes for PostgreSQL/process coverage.

The DSN is read only from `POSTGRES_CONNECTION_STRING`; it is never written to
argv, logs, reports or artifacts. Cleanup drops only the task-created database.

### 3.7 Regression and mutation evidence (G1-G3)

Required regression targets include phase-4 engineer suites, the phase-2 book/
reservation/fee suites, phase-3 eligibility/calendar/warmup/no-lookahead suites,
the planned offline phase-4 suites, P0 and the real-PG suites named by the work
order. Exact commands and counts are listed in the execution request.

Two isolated mutations must show that assertions detect a broken invariant and
recover when the mutation is removed:

- **M1 frozen input:** a test-local store wrapper alters one nested allocation
  after submission. Execution must reject the altered document; with the wrapper
  disabled the same job executes successfully and keeps the original hash.
- **M2 book isolation:** a test-local seam makes a book admission see another
  book's cash/available balance. The independent budget oracle must fail with the
  expected conservation/refusal mismatch; restoring the real seam makes the same
  scenario pass. The mutation is confined to the test process and never edits a
  shared production file.

Mutation assertions are reported separately and do not inflate normal pass counts.

## 4. Authored test inventory

The authored modules are executable tests, not design placeholders. Exact-node
commands are in section 5.2.

| Test function | File | Acceptance | Independent oracle |
|---|---|---|---|
| `test_allocation_sum_book_ids_and_manifest_hash_are_exact` | offline | 4.1, 4.3, 4.4 | literal 100 + 900 = 1000; canonical SHA-256 over frozen JSON |
| `test_manifest_rejects_impossible_allocations_and_stale_runtime` | offline | 4.1, 4.3, 4.4 | sum mismatch, wrong book set, legacy runtime, provenance set mismatch |
| `test_manifest_nested_mutation_is_refused_and_recovers` | offline | 4.3, 4.4 | nested param mutation keeps old SHA-256; retry and executor refuse |
| `test_same_frozen_manifest_is_deterministic_in_fresh_runtimes` | offline | 4.4 | two fresh runtimes, byte-identical canonical result |
| `test_unequal_books_refuse_a_and_leave_b_trading` | offline | 4.1, 4.2, 4.7 | A=100 rejects 200; B=900 fills 1; 799.9 cash, 899.9 equity, 0.1 fee |
| `test_fill_above_a_books_budget_must_not_borrow_from_the_account` | offline | 4.1, 4.7 | 95.095 reserved; 115.06495 actual cost > 100; exact `{BOOK_INSUFFICIENT_AVAILABLE_BALANCE: 1}` and one alpha/book:alpha/MARKET attributed rejection; A=100, B=900, zero fees |
| `test_partial_fill_actual_fee_currency_and_per_book_reconciliation` | offline | 4.2 | 0.05+0.15=0.20 A, 0.10 B, total 0.30; per-book conservation |
| `test_base_fee_is_booked_in_base_currency_once` | offline | 4.2 | 0.001 BTC fee; 0.999 BTC position; cash 400; one BTC fee entry |
| `test_mutation_book_borrowing_is_detected_and_restored` | offline | 4.1, 4.7, G3 | test-local M2 seam; independent negative-available audit; real seam rejects |
| `test_live_and_research_order_intent_match` | offline | 4.5, 4.6 | LIMIT 99 is not close 100; MARKET uses closed bar close 100 and close time |
| `test_research_executor_rechecks_trusted_link_after_queue` | offline | 4.10, G2 | queued link flipped `official_eligible=False` must fail before execution |
| `test_executor_rejects_changed_dataset_content_and_recovers` | offline | 4.10 | dataset content mutation changes `ds_*`; failure then restored success |
| `test_research_executor_only_needs_research_store` | offline | 4.9 | wrapper raises on any non-research store member |
| `test_control_worker_postgres_publish_reload_and_tamper_detection` | postgres | 4.8, 4.9, 4.10, G2 | disposable DB; Control API; separate worker process; 2+1 fills at 102; .306 fee; fresh reload; tamper/retry |

The PostgreSQL test never skips: a missing `asyncpg`, missing DSN, DSN without a
database, or lack of `CREATEDB` is a hard failure. It creates and drops only a
random `qts_phase4_ind_*` database and reads the DSN only from
`POSTGRES_CONNECTION_STRING`.

Two contract-versus-code counterexamples were authored as red tests. They failed
on the round-1 source exactly as predicted; the engineer then fixed the
implementation, and the round-2 offline run passes both. They remain in the suite
as regression tests:

1. **Over-budget fill after adverse slippage.** Pre-fix `EventBacktestRuntime._execute`
   checked the account-level available balance, not the owning book's remaining
   budget. The test reserves 95.095 for A, then fills at a slipped cost of
   115.06495. The round-1 evidence shows the fill was accepted; the round-2 source
   has the shared book-budget check and the test passes.
2. **Trusted status not rechecked by the executor.** Pre-fix `ResearchExecutor._verify_provenance`
   compared only `provenance_hash`; flipping `official_eligible` while retaining
   the hash succeeded. Round 1 reproduced this. The round-2 source calls
   `link_is_trusted` and the test passes.

### 4.1 Round-1 execution and test corrections (controlled engineer channel)

The raw files are under `artifacts/foundation/phase4/evidence/`; the tester read
the stdout/stderr/exit/JUnit files directly.

| Run | Command | Exit | JUnit | Result |
|---|---|---|---|---|
| offline round 1 | `pytest -q trader/tests/test_phase4_independent_offline.py` | 1 | 13 tests, 5 failures | F1 and F2 reproduced; two test expectation defects and one diagnostic-order failure |
| postgres round 1 | `pytest -q trader/tests/test_phase4_independent_postgres.py` | 1 | 1 test, 1 failure | publication/reload/determinism passed; tamper step was a JSONB no-op |
| offline round 2 | same offline command, post-fix source | 1 | 13 tests, 12 passed, 1 failure | only fee-entry ordering failed; all economic values correct |
| offline round 2 final | same offline command, corrected tests, pre-review source | 0 | 13 tests, 13 passed, 0 failed, 0 skipped | historical evidence `offline-final.xml`; superseded by the round-3 source/test changes |
| postgres round 2 final | corrected PostgreSQL test, pre-review source | 0 | 1 test, 1 passed, 0 failed, 0 skipped | historical evidence; publication/reload/tamper passed |
| offline round 3 | revised F1 exact rejection test, final source | 0 | 13 tests, 13 passed, 0 failed, 0 skipped | exact rejection/attribution passed; `offline-round3.xml` |
| postgres round 3 | final source, DSN only in process env | 0 | 1 test, 1 passed, 0 failed, 0 skipped | publication/reload/tamper passed; `postgres-round3.xml` |
| independent PG no-DSN round 3 | same test, `POSTGRES_CONNECTION_STRING` absent | 1 | 1 failed, 0 skipped, 0 xfailed | hard failure `POSTGRES_CONNECTION_STRING is required; no default DSN is accepted` |

The five round-1 offline failures were:

1. `test_unequal_books...`: `available["USDT"]` was `"799.900"` under the old
   export path; the exact economic value was correct. The test now compares
   `Decimal`, not string spelling. This is a test robustness fix, not a relaxed
   amount assertion.
2. `test_fill_above...`: one fill was accepted at the over-budget price,
   confirming F1.
3. `test_partial_fill...`: the fee entries were correct; the expected list was not
   in the same order as `sorted(fee_entries)`. The expected list is now sorted
   `(-0.15), (-0.05), (-0.10)` by book/asset/value; the amount and currency
   assertions are unchanged.
4. `test_research_executor_rechecks...`: the run succeeded after
   `official_eligible=False`, confirming F2.
5. `test_executor_rejects_changed_dataset_content...`: content mutation was
   reported as `Invalid OHLCV bounds` before the content-hash check; the
   diagnostic-order fix makes it fail as a content mismatch.

The PostgreSQL failure was a **test defect**: the JSONB path
`{strategies,0,params,qty}` did not exist (`params` contains `order_size`), so
`jsonb_set(..., create_missing=false)` was a no-op and retry correctly requeued
the unchanged manifest. The corrected path is
`{strategies,0,params,order_size}`, with `order_size` changed from `"2"` to
`"999"`; the test still requires `Frozen manifest hash mismatch` and checks that
the tampered value remains and `manifest_hash` is unchanged. The engineer's
`manual-pg-tamper` evidence independently demonstrated the real path.

### 4.2 Independent review of the production fixes

The tester read the round-2 source and compared it with the contract:

- F1 adds `books.book_available_including(...)` and
  `core.book_budget_available(...)`. `_execute` now checks the owning book's
  budget (including the order's own hold) for both BUY notional+fee and SELL qty,
  and releases both holds on rejection. The rejection path now also writes
  `execution_rejections`, `errors` and an attributed `rejected_intents` entry
  (`strategy_id`, `book_id`, `symbol`, `side`, `qty`, `order_type`, `code`), so a
  silently accepted or silently dropped intent is distinguishable. This is the
  required no-borrowing rule; it does not check only the account total.
- F2 adds `link_is_trusted(link)` after the hash comparison in
  `_verify_provenance`, so `official_eligible` and `history_dataset_id` are
  revalidated after queueing.
- F3 compares `"ds_" + digest({identity,bars})` with the stored dataset id before
  calling `freeze_dataset`, making content tampering fail for the right reason.
- F4 uses `decimal_text` for per-book backtest export fields; the shared
  `books.book_snapshot` behavior is unchanged, as required by phase 2.

These are bounded fixes, not a relaxation of the independent tests. The final
round-3 verdict is based on the new offline/PG/full-suite runs on the frozen
source, not on the pre-review pass.

### 4.3 Format gates for the tester-owned files

The engineer's check-only run on the tester files returned exit 1:

```text
python -m black --check --diff --line-length 100 \
  trader/tests/test_phase4_independent_offline.py \
  trader/tests/test_phase4_independent_postgres.py
# exit 1; diff: artifacts/foundation/phase4/evidence/black-independent.diff

python -m isort --check-only --diff --profile black \
  trader/tests/test_phase4_independent_offline.py \
  trader/tests/test_phase4_independent_postgres.py
# exit 1; diff: artifacts/foundation/phase4/evidence/isort-independent.diff
```

The tester applied exactly the reported Black collapses and the isort import
collapse by hand, without changing any assertion. Those round-2 check-only runs
are exit 0. The F1 assertion revision in round 3 changed executable lines, so the
same two check-only commands were rerun and are exit 0 on the final source in
section 5.3.

## 5. Execution and final evidence

The tester's command channel cannot start a process (section 6). The engineer
executed the authored tests through its controlled channel and returned raw
evidence. The engineer did not edit, skip or weaken the independent tests and did
not modify production code to make them pass. `authored_by` is the tester;
`executed_by` is the controlled engineer channel; `analyzed_by` is the tester.

### 5.1 Frozen-input confirmation (final source rehashed)

Round 2's `final-git.*` and `final-hashes.*` establish the branch/HEAD and the
pre-review bytes; they are retained as history. The final source fingerprint was
independently recomputed after the review fixes:

```text
branch: feat/foundation-refactor
HEAD:   1b1564bc4ad0c14c9b035582d384713661d96861
digest: cfcb8a1351d07dcaa03181ffc27c7fcaa07230d2fc40e30a8603c09a4ffe6682
snapshot: artifacts/foundation/phase4/final-source-snapshot.json
```

The twelve fingerprinted paths are:

```text
.github/workflows/ci-gate.yml
docs/INTERFACE_CONTRACTS.md
docs/PROJECT_ARCHITECTURE.md
trader/tests/test_phase4_independent_offline.py
trader/tests/test_phase4_independent_postgres.py
trader/tests/test_phase4_joint_backtest.py
trader/tests/test_phase4_postgres.py
trader/trading/backtest.py
trader/trading/books.py
trader/trading/core.py
trader/trading/jobs.py
trader/trading/research.py
```

The full `(path, sha256)` rows are in the snapshot; current test hashes are
`db9b267e98a4831f7b499d7d2d063f6de410056d6db0e8841d2fd60f27d69f15` (offline,
after the F1 strengthening) and
`8091411f544724bd40902dd5de104fbf1bd5419c9d60c6e769b59be0a5fcb39e` (postgres,
unchanged). The report is operational metadata, is excluded from the source
digest, and records its own current hash in
`artifacts/foundation/phase4/tester-authored-hashes.json`. The round-3
`git status` and hash run are `final-git-round3.*` and `final-hashes-round3.*`;
all twelve source hashes match the snapshot and the recomputed digest, with no
source/test edit by the tester.

### 5.2 Exact test commands and results

```text
python -m pytest -q trader/tests/test_phase4_independent_offline.py --tb=short --junitxml=artifacts/foundation/phase4/evidence/offline-round3.xml
```

| Field | Value |
|---|---|
| exit code | 0 (`offline-round3.exit.txt`) |
| stdout | 13 dots, `[100%]`, warnings only (`offline-round3.stdout.txt`) |
| stderr | empty (`offline-round3.stderr.txt`) |
| JUnit | tests=13, failures=0, errors=0, skipped=0 (`offline-round3.xml`) |
| collected nodes | exactly the 13 authored offline tests, including the strengthened F1 test |
| F1 assertion | no fills; `execution_rejections == {"BOOK_INSUFFICIENT_AVAILABLE_BALANCE": 1}`; one alpha/book:alpha/BTCUSDT/BUY/0.95/MARKET rejection; A=100, B=900, positions empty, fees 0 |

```text
python -m pytest -q trader/tests/test_phase4_independent_postgres.py --tb=short --junitxml=artifacts/foundation/phase4/evidence/postgres-round3.xml
```

| Field | Value |
|---|---|
| exit code | 0 (`postgres-round3.exit.txt`) |
| stdout | `.` and `[100%]`, warnings only (`postgres-round3.stdout.txt`) |
| stderr | empty (`postgres-round3.stderr.txt`) |
| JUnit | tests=1, failures=0, errors=0, skipped=0 (`postgres-round3.xml`) |
| DSN handling | only `POSTGRES_CONNECTION_STRING` in the process environment; random disposable `qts_phase4_ind_*` database; no DSN in argv or artifacts |

The independent negative control ran with `POSTGRES_CONNECTION_STRING` absent:

| Field | Value |
|---|---|
| path | `postgres-round3-no-dsn.*` |
| exit code | 1 |
| result | 1 failed, 0 skipped, 0 xfailed |
| failure | `POSTGRES_CONNECTION_STRING is required; no default DSN is accepted` |

A DSN scan over the evidence directory found no `postgresql://` or password
strings. A missing environment is a hard failure, never a skip.

### 5.3 Format checks for the tester files (round 3)

```text
python -m black --check --line-length 100 trader/tests/test_phase4_independent_offline.py trader/tests/test_phase4_independent_postgres.py
python -m isort --check-only --profile black trader/tests/test_phase4_independent_offline.py trader/tests/test_phase4_independent_postgres.py
```

Both exit 0 (`black-independent-round3.*`, `isort-independent-round3.*`). Black
reports on stderr `All done! ... 2 files would be left unchanged.`; isort
stdout/stderr are empty. The F1 revision is therefore formatted without changing
its assertions.

### 5.4 Regression evidence (round 3, same frozen source)

The round-3 full suite ran on the same frozen source with the DSN in the process
environment only:

```text
python -m pytest -q trader/tests --tb=short --junitxml=artifacts/foundation/phase4/evidence/full-round3.xml
```

`full-round3.exit.txt` is 0, stderr is empty, and the JUnit suite is tests=3702,
failures=0, errors=0, skipped=0 (200.890 s), including all 13 independent offline
tests, the independent PostgreSQL test, the 14 phase-4 engineer tests and the
earlier phase-2/phase-3 suites. The `trader/tests` scope is the same accepted
scope as the round-2 run; a repo-root bare `pytest` would additionally collect
the unrelated nested `.kilo/worktrees/peppered-heliotrope/**` tree, which is not
part of this repository's accepted suite. The old six-bundle/3701-test directory
is retained only as historical evidence and is not used to claim the final source.
The historical `black_check`/`isort_check` entries in
`engineer-evidence-round2/summary.json` are both returncode 0 at 18:44:17 (506
files unchanged). That record predates the final F1 edit at 18:44:36, so it is
not the final format evidence; it is also not an exit-1 record. The final format
evidence is `black-independent-round3.*` / `isort-independent-round3.*`, with
the whole-tree `black-final-tree.*` / `isort-final-tree.*` as supplemental
server-side checks. Section 5.3 is the final format result.

### 5.5 Evidence package received (round 3)

- `offline-round3.*`, `postgres-round3.*`, `postgres-round3-no-dsn.*`;
- `full-round3.*` with JUnit `tests=3702, failures=0, errors=0, skipped=0`;
- `black-independent-round3.*`, `isort-independent-round3.*`;
- `final-git-round3.*`, `final-hashes-round3.*`, `versions-round3.stdout.txt`;
- JUnit XML for both independent modules and the full suite;
- no DSN in argv, stdout, stderr, JUnit, report or hash evidence.

The engineer reported no source/test edits during execution; the tester checked
`final-git-round3.*` and every one of the twelve final hashes against the frozen
snapshot instead of relying on that statement.

### 5.6 Verdict rule applied (round 3)

The tester re-read every raw round-3 file: exit codes are direct values, the JUnit
suite attributes were re-parsed (`tests="13" failures="0" errors="0" skipped="0"`,
`tests="1" failures="0" errors="0" skipped="0"`, and
`tests="3702" failures="0" errors="0" skipped="0"`), every phase-4 `<testcase>` was
enumerated and none contains `<failure>`, `<error>` or `<skipped>`, and all twelve
hashes match the independent WebCrypto values. The strengthened F1 test passed
its exact rejection/attribution assertions. No independent test was weakened,
deleted, skipped or xfailed.

## 6. Command-channel limitation and raw error

The DSH `pwsh` tool failed during workspace provisioning before any command
executed:

```text
Error: SetNamedSecurityInfoW failed (Win32 5): grantWrite(D:\Code of Python\quant_trading_system)
```

The same error occurred for `git rev-parse`, reading the snapshot, and a
`python --version` probe with a temporary working directory. The tester did not
modify ACLs, permissions or terminal settings and did not use an unrestricted
terminal. The unsuccessful ACL-diagnostic escalation was explicitly rejected by
the coordinator. File read/write tools remained available, so the independent
tests and this design were authored; all execution is delegated under workflow
v3 section 2.1.

## 7. Authored files

| File | Change | Current SHA-256 |
|---|---|---|
| `trader/tests/test_phase4_independent_offline.py` | independent test module; round 3 exact F1 rejection/attribution | `db9b267e98a4831f7b499d7d2d063f6de410056d6db0e8841d2fd60f27d69f15` |
| `trader/tests/test_phase4_independent_postgres.py` | independent PostgreSQL/process test module | `8091411f544724bd40902dd5de104fbf1bd5419c9d60c6e769b59be0a5fcb39e` |
| `docs/PHASE4_INDEPENDENT_TEST_REPORT.md` | this report | see `artifacts/foundation/phase4/tester-authored-hashes.json` (self-referential hash is recorded after the final edit) |
| `.github/workflows/ci-gate.yml` | append-only commands in `trading-core-gate` and `postgres-integration` | `e61f2f1794ed3168c66b365e9f84b5d13e99cdd63ed3a9f7ece38733f3e44830` |

The tester did not edit production code, engineer tests, contracts, existing gate
conditions, or state files. The tester appended two independent-test pytest lines
(shown below); the engineer separately appended two phase-4 test lines. The total
workflow change is four added lines, with no deletions and no modification of
existing gate conditions:

```text
python -m pytest -q trader/tests/test_phase4_independent_offline.py --tb=short
python -m pytest -q trader/tests/test_phase4_independent_postgres.py --tb=short
```

`workspace_frozen: true` means this report revision and the two test files are the
frozen batch-1 artifacts. The tester will make no further source/test edits; a
necessary correction requires a new round and a new report revision, not silent
mutation of these files.

## 8. Machine-readable handoff (round 3, pass)

```yaml
report_id: phase4-independent-test-r3
workflow_version: 3
phase: 4
role: tester
agent_id: c6ff85ec-e215-4459-a617-44599682129a
executed_by: engineer_controlled
analyzed_by: tester
round: 3
base_commit: 1b1564bc4ad0c14c9b035582d384713661d96861
branch: feat/foundation-refactor
source_digest: cfcb8a1351d07dcaa03181ffc27c7fcaa07230d2fc40e30a8603c09a4ffe6682
snapshot_path: artifacts/foundation/phase4/final-source-snapshot.json
overall: pass
items:
  - id: "4.1"
    verdict: pass
    evidence: "Round-3 offline 13/13 and PG 1/1 on the frozen source: unequal-book routing, impossible allocation refusal, book-scoped budget and joint publication/export all pass."
  - id: "4.2"
    verdict: pass
    evidence: "Round-3 offline partial fills, actual fee currency, base-fee once, per-book balances/equity/fees and total reconciliation pass."
  - id: "4.3"
    verdict: pass
    evidence: "Round-3 literal canonical manifest hash, allocation sum/book mapping, nested mutation refusal and post-rejection recovery pass."
  - id: "4.4"
    verdict: pass
    evidence: "Round-3 fresh-runtime determinism, retry with the same frozen manifest and PG second-run canonical equality pass."
  - id: "4.5"
    verdict: pass
    evidence: "Round-3 explicit LIMIT 99 versus MARKET closed-bar 100 parity passes offline; PG fills are at 102 with stable symbols/sides/qty."
  - id: "4.6"
    verdict: pass
    evidence: "Round-3 closed-bar close and reference_at_ms=60000 assertions pass; no ticker reference is used."
  - id: "4.7"
    verdict: pass
    evidence: "Strengthened F1 passes: no fills; execution_rejections == {BOOK_INSUFFICIENT_AVAILABLE_BALANCE: 1}; exactly one alpha/book:alpha/BTCUSDT/BUY/0.95/MARKET rejected intent; A=100, B=900, positions empty, fees 0."
  - id: "4.8"
    verdict: pass
    evidence: "Round-3 PG 1/1: Control API -> separate worker process -> PostgreSQL publication/reload; only research schema; deterministic and no live state."
  - id: "4.9"
    verdict: pass
    evidence: "Round-3 offline research-only store wrapper and poisoned live-service isolation pass; PG control uses the research application only."
  - id: "4.10"
    verdict: pass
    evidence: "Round-3 trusted-link recheck after queue, changed dataset content/recovery, PG manifest tamper/retry and frozen provenance pass."
  - id: "G1"
    verdict: pass
    evidence: "Round-3 full suite on the same frozen source: 3702 tests, exit 0, failures=0, errors=0, skipped=0; includes 13 independent offline, 1 independent PG, 14 phase-4 engineer and earlier phase-2/3 suites. Six-bundle/3701 history is not used for this source."
  - id: "G2"
    verdict: pass
    evidence: "Independent oracles came from contract/hand arithmetic, not implementation. F1/F2 were red before the fixes and green after; F1 now asserts the exact rejection count, attribution and zero-fee book invariants; no assertion was weakened."
  - id: "G3"
    verdict: pass
    evidence: "Required independent, PG, format and full-suite checks actually ran. PG has no skip/xfail and no default DSN; the no-DSN negative control exits 1 with 1 failed/0 skipped/0 xfailed. M1/M2 test-local mutations pass."
  - id: "G4"
    verdict: pass
    evidence: "Branch/HEAD match; all 8 tracked + 4 untracked paths are enumerated in final-source-snapshot.json; 12-file digest cfcb8a1351d07dcaa03181ffc27c7fcaa07230d2fc40e30a8603c09a4ffe6682 and every hash match the independent recomputation; report hash at execution time eeb3e3cd...de7f7."
  - id: "G5"
    verdict: pass
    evidence: "Tester authored only its two test files, this report, append-only CI commands and evidence artifacts; no production/contract/engineer-test/gate edits, commit, push, deploy or real-account access. This is a write-boundary claim, not a Black/isort claim."
  - id: "G6"
    verdict: pass
    evidence: "Contract 14.6, architecture, CI commands and implementation were checked for the same order/book/provenance semantics; the frozen offline/PG/full-suite runs confirm the implementation, not merely that CI was appended."
checks:
  - command: "python -m pytest -q trader/tests/test_phase4_independent_offline.py --tb=short --junitxml=artifacts/foundation/phase4/evidence/offline-round3.xml"
    executed_by: engineer_controlled
    exit_code: 0
    passed: 13
    failed: 0
    skipped: 0
    xfailed: 0
    required: true
    output_path: artifacts/foundation/phase4/evidence/offline-round3.xml
  - command: "python -m pytest -q trader/tests/test_phase4_independent_postgres.py --tb=short --junitxml=artifacts/foundation/phase4/evidence/postgres-round3.xml"
    executed_by: engineer_controlled
    exit_code: 0
    passed: 1
    failed: 0
    skipped: 0
    xfailed: 0
    required: true
    output_path: artifacts/foundation/phase4/evidence/postgres-round3.xml
  - command: "python -m pytest -q trader/tests/test_phase4_independent_postgres.py --tb=short (POSTGRES_CONNECTION_STRING absent; no default DSN)"
    executed_by: engineer_controlled
    exit_code: 1
    passed: 0
    failed: 1
    skipped: 0
    xfailed: 0
    required: true
    output_path: artifacts/foundation/phase4/evidence/postgres-round3-no-dsn.stdout.txt
  - command: "python -m black --check --line-length 100 trader/tests/test_phase4_independent_offline.py trader/tests/test_phase4_independent_postgres.py"
    executed_by: engineer_controlled
    exit_code: 0
    passed: 0
    failed: 0
    skipped: 0
    xfailed: 0
    required: true
    output_path: artifacts/foundation/phase4/evidence/black-independent-round3.stderr.txt
  - command: "python -m isort --check-only --profile black trader/tests/test_phase4_independent_offline.py trader/tests/test_phase4_independent_postgres.py"
    executed_by: engineer_controlled
    exit_code: 0
    passed: 0
    failed: 0
    skipped: 0
    xfailed: 0
    required: true
    output_path: artifacts/foundation/phase4/evidence/isort-independent-round3.stdout.txt
  - command: "python -m pytest -q trader/tests --tb=short --junitxml=artifacts/foundation/phase4/evidence/full-round3.xml"
    executed_by: engineer_controlled
    exit_code: 0
    passed: 3702
    failed: 0
    skipped: 0
    xfailed: 0
    required: true
    output_path: artifacts/foundation/phase4/evidence/full-round3.xml
historical_checks:
  - command: "python -m pytest -q trader/tests/test_phase4_postgres.py --tb=short (no DSN negative control; two cases must fail, no skip)"
    executed_by: engineer_controlled
    exit_code: 1
    passed: 0
    failed: 2
    skipped: 0
    xfailed: 0
    required: true
    output_path: artifacts/foundation/phase4/evidence/engineer-pg-no-dsn.stdout.txt
  - command: "python -m pytest -q trader/tests/test_phase4_independent_offline.py --tb=short"
    executed_by: engineer_controlled
    exit_code: 1
    passed: 8
    failed: 5
    skipped: 0
    xfailed: 0
    required: true
    output_path: artifacts/foundation/phase4/evidence/offline.xml
  - command: "python -m pytest -q trader/tests/test_phase4_independent_offline.py --tb=short (post-fix source)"
    executed_by: engineer_controlled
    exit_code: 1
    passed: 12
    failed: 1
    skipped: 0
    xfailed: 0
    required: true
    output_path: artifacts/foundation/phase4/evidence/offline-round2.xml
  - command: "python -m pytest -q trader/tests/test_phase4_independent_postgres.py --tb=short"
    executed_by: engineer_controlled
    exit_code: 1
    passed: 0
    failed: 1
    skipped: 0
    xfailed: 0
    required: true
    output_path: artifacts/foundation/phase4/evidence/postgres.xml
  - command: "python -m pytest -q trader/tests/test_phase4_independent_postgres.py --tb=short (negative control: execution-side DSN omitted, not a source failure)"
    executed_by: engineer_controlled
    exit_code: 1
    passed: 0
    failed: 1
    skipped: 0
    xfailed: 0
    required: true
    output_path: artifacts/foundation/phase4/evidence/postgres-final-no-dsn.xml
  - command: "python -m pytest -q trader/tests/test_phase4_independent_offline.py --tb=short --junitxml=artifacts/foundation/phase4/evidence/offline-final.xml"
    executed_by: engineer_controlled
    exit_code: 0
    passed: 13
    failed: 0
    skipped: 0
    xfailed: 0
    required: true
    output_path: artifacts/foundation/phase4/evidence/offline-final.xml
  - command: "python -m pytest -q trader/tests/test_phase4_independent_postgres.py --tb=short --junitxml=artifacts/foundation/phase4/evidence/postgres-final.xml"
    executed_by: engineer_controlled
    exit_code: 0
    passed: 1
    failed: 0
    skipped: 0
    xfailed: 0
    required: true
    output_path: artifacts/foundation/phase4/evidence/postgres-final.xml
  - command: "python -m black --check --line-length 100 trader/tests/test_phase4_independent_offline.py trader/tests/test_phase4_independent_postgres.py"
    executed_by: engineer_controlled
    exit_code: 0
    passed: 0
    failed: 0
    skipped: 0
    xfailed: 0
    required: true
    output_path: artifacts/foundation/phase4/evidence/black-independent-final.stdout.txt
  - command: "python -m isort --check-only --profile black trader/tests/test_phase4_independent_offline.py trader/tests/test_phase4_independent_postgres.py"
    executed_by: engineer_controlled
    exit_code: 0
    passed: 0
    failed: 0
    skipped: 0
    xfailed: 0
    required: true
    output_path: artifacts/foundation/phase4/evidence/isort-independent-final.stdout.txt
  - command: "git rev-parse --abbrev-ref HEAD; git rev-parse HEAD; git status --porcelain=v1"
    executed_by: engineer_controlled
    exit_code: 0
    passed: 0
    failed: 0
    skipped: 0
    xfailed: 0
    required: true
    output_path: artifacts/foundation/phase4/evidence/final-git.stdout.txt
  - command: "SHA-256 final tester/workflow files"
    executed_by: engineer_controlled
    exit_code: 0
    passed: 0
    failed: 0
    skipped: 0
    xfailed: 0
    required: true
    output_path: artifacts/foundation/phase4/evidence/final-hashes.stdout.txt
  - command: "phase4 engineer regression bundle"
    executed_by: engineer_controlled
    exit_code: 0
    passed: 14
    failed: 0
    skipped: 0
    xfailed: 0
    required: true
    output_path: artifacts/foundation/phase4/engineer-evidence-round2/phase4_engineer.xml
  - command: "planned offline regression bundle"
    executed_by: engineer_controlled
    exit_code: 0
    passed: 154
    failed: 0
    skipped: 0
    xfailed: 0
    required: true
    output_path: artifacts/foundation/phase4/engineer-evidence-round2/planned_offline.xml
  - command: "phase2/phase3 regression bundle"
    executed_by: engineer_controlled
    exit_code: 0
    passed: 107
    failed: 0
    skipped: 0
    xfailed: 0
    required: true
    output_path: artifacts/foundation/phase4/engineer-evidence-round2/phase2_phase3_regressions.xml
  - command: "research/PG regression bundle"
    executed_by: engineer_controlled
    exit_code: 0
    passed: 7
    failed: 0
    skipped: 0
    xfailed: 0
    required: true
    output_path: artifacts/foundation/phase4/engineer-evidence-round2/research_pg.xml
  - command: "touched persistence regression bundle"
    executed_by: engineer_controlled
    exit_code: 0
    passed: 25
    failed: 0
    skipped: 0
    xfailed: 0
    required: true
    output_path: artifacts/foundation/phase4/engineer-evidence-round2/touched_persistence.xml
  - command: "P0 regression bundle"
    executed_by: engineer_controlled
    exit_code: 0
    passed: 99
    failed: 0
    skipped: 0
    xfailed: 0
    required: true
    output_path: artifacts/foundation/phase4/engineer-evidence-round2/p0.xml
  - command: "engineer-controlled full pytest suite (all tests; raw XML carries 3701 cases)"
    executed_by: engineer_controlled
    exit_code: 0
    passed: 3701
    failed: 0
    skipped: 0
    xfailed: 0
    required: true
    output_path: artifacts/foundation/phase4/engineer-full-suite-round2.xml
changed_files:
  - trader/tests/test_phase4_independent_offline.py
  - trader/tests/test_phase4_independent_postgres.py
  - docs/PHASE4_INDEPENDENT_TEST_REPORT.md
  - .github/workflows/ci-gate.yml
blocking_findings:
  - id: BLK-1
    summary: "Tester command channel fails during workspace provisioning before any process starts; all execution is delegated to the engineer controlled channel."
    raw_error: "Error: SetNamedSecurityInfoW failed (Win32 5): grantWrite(D:\\Code of Python\\quant_trading_system)"
limitations:
  - "The tester's own command channel never executed; every command was delegated to the engineer controlled channel, then independently analyzed from raw stdout/stderr/exit/JUnit files."
  - "The final 12-file source digest was independently recomputed as cfcb8a1351d07dcaa03181ffc27c7fcaa07230d2fc40e30a8603c09a4ffe6682; the snapshot is final-source-snapshot.json. The pre-review d8e04b84 and round-2 d51161 values are stale by construction. The tester's test/report hashes are in tester-authored-hashes.json."
  - "F1 is exact: no fills, execution_rejections == {BOOK_INSUFFICIENT_AVAILABLE_BALANCE: 1}, one alpha/book:alpha/BTCUSDT/BUY/0.95/MARKET attributed rejection, A=100, B=900 unaffected, total fees 0. F2 remains green."
  - "The historical round-1/2 checks are retained under historical_checks only; they are not part of the round-3 pass counts."
  - "The engineer's independent no-DSN control has one hard failure (not a skip); the final PG run used POSTGRES_CONNECTION_STRING only in the process environment and a random disposable qts_phase4_ind_* database. A DSN scan found no credentials in the evidence."
  - "The full suite used the accepted trader/tests scope. A repo-root bare pytest would additionally collect the unrelated nested .kilo/worktrees/peppered-heliotrope/** tree; this is disclosed rather than silently expanding the scope."
  - "No GitHub-hosted Actions runner was used; the appended workflow commands and the full suite were executed in the controlled local channel."
unexecuted_checks: []
workspace_frozen: true
release_declaration: "Phase 4 batch 1 independent test PASS for 4.1-4.10 and G1-G6. Tester changed only its two test files, this report, append-only CI commands and evidence artifacts; no production/contract/engineer-test/gate edits, commit, push, deploy or real-account action."
from_agent_id: c6ff85ec-e215-4459-a617-44599682129a
to_agent_id: d4607ac2-c9f1-40bf-8ec3-742e0ae8df27
handoff_kind: test_report
finding_id: null
```

