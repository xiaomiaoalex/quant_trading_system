# Phase 3 Independent Test Report — closed-bar ingress (live and warm-up)

- report_id: `P3-INDP-2026-11-03-R3`
- workflow_version: 3
- phase: 3
- role: tester
- agent_id: `9110fa6b-ee08-4877-ac72-79b7aebd6f0e` (created by engineer
  `9d4b7865-3461-416b-875b-efc7d7e3234a`)
- round: 3 (parent return 2; `P3-PARENT-INPUT-TYPE-GATE` attempt 2)
- base_commit: `fa5fab7ddabcfd93d3828a3761a47a1ca45b4281`
- branch: `feat/foundation-refactor`
- source_manifest: `artifacts/foundation/phase3/final-source-manifest-r9.json`,
  40 files, digest `bf5a26272dd30ee8f700704420f8def56c5b30a901db525a6e8e146bd3c714fd`
- superseded/retained digests (history only): the round-2 37-file
  `825adc0238fde8665d114a119040a0b6358373eea7032de20dcb20761155afba`, its frozen
  report `2b126443c74ea93077dd2576a56a0c9809df44ccd272ee5f51651d766c979626`,
  the engineer's 39-file ingress-fix manifest
  `a5cf4d2ce363cbcb73c18cca46df02a8cb7c577e6064ba883ed2437d33fbb254`, and the
  earlier attempt-1/parent-reviewed/fix-2 manifests. None is rewritten.
- authored_by: tester (independent)
- executed_by: engineer `9d4b7865-3461-416b-875b-efc7d7e3234a`, in its controlled
  channel, on the tester's frozen bytes (rounds 8 and 9)
- analyzed_by: tester (independent), from raw stdout/stderr, JUnit attributes, the
  two parent-probe JSON outputs and the execution manifests under
  `artifacts/foundation/phase3/execution-r8/` and `execution-r9/`
- overall: `pass`
- blocking_findings: none. `P3-PARENT-INPUT-TYPE-GATE` is closed at attempt 2 by
  independent evidence (§6); `P3-R1-3.1-CLOCK` remains closed at attempt 2;
  tester-side defects `P3I-F-1`…`P3I-F-5` are closed.
- workspace_frozen: true

## 1. Result

| suite | cases | result | raw evidence |
|---|---|---|---|
| independent ingress (`IN`) | 25 | 25 passed, 0 failed/error/skip | `execution-r9/ingress.stdout.txt`, `tester-junit-ingress-r9.xml` |
| independent affected offline | 70 | 70 passed, 0 failed/error/skip | `tester-junit-affected-r9.xml` |
| phase-2 b1 runner target | 1 | 1 passed | `tester-junit-b1-r9.xml` |
| independent real PostgreSQL (`PG`) | 5 | 5 passed, 0 failed/error/skip | `tester-junit-postgres-r9.xml` |
| parent boundary probe (read-only) | 5 | 5/5 `contract_pass`, exit 0 | `execution-r9/parent_boundary_probe.stdout.txt` |
| parent ingress probe (read-only) | 5 | 5/5 `contract_pass`, exit 0 | `execution-r9/parent_ingress_probe.stdout.txt` |
| style (black, isort) | 7 files | exit 0, 7 unchanged / empty | `execution-r9/black.stderr.txt` |

`IN` 25 = 6 non-bar history types + 2 mixed windows + 8 malformed-field cases +
future + unfinished + duplicate + short + valid history control + parent live
counterexample + missing-price/contradiction/interval + exact boundary + LIMIT.
The affected offline 70 is `CB` 25 + `IG` 11 + `VC` 6 + `EL` 28, rerun in round 9
on byte-identical files; the round-9 JUnit is the evidence and agrees with the
earlier round-6/7 runs. Round 8 ran the same suite at 24/25 (JUnit
`tests=25 failures=1`); the single
failure was the tester fixture defect `P3I-F-5`, and round 9 is the green rerun
of those bytes after the fixture fix. Every JUnit file reports
`failures="0" errors="0" skipped="0"`; no `xfail`, `skip` or tolerance exists.

The full `trader/tests` suite was not run by the tester. The engineer reports
**3649 passed / 0 failed / 0 errors / 0 skipped** on the fixed source
(`evidence/full-after-ingress-fix2.xml`); the tester checked the JUnit
`tests="3649" 0/0/0` attributes only and records it as engineer-run evidence.
With `IN` 25 added, the next full run should be 3674; that number is not asserted
as executed.

## 2. Why this revision exists

`PHASE3-PARENT-INPUT-BOUNDARY-RETURN-2` returned `overall: fail` on the same
finding, reopening `P3-PARENT-INPUT-TYPE-GATE` as **attempt 2** (attempt 1 and
its history stay as written). The parent reproduced two bypasses on
`825adc02…afba`:

1. `prepare_warmup()` with one TICKER and a stateful plugin requiring one bar
   returned True, called the plugin, counted one observation and declared WARM;
   the next valid completed bar then created one order, command and hold.
2. A KLINE with `open_time_ms=600000`, interval `1m` and timestamp at the open,
   missing `available_at_ms`, `close_time_ms` and `kline_close`, was consumed at
   decision 659500 (contract close 660000), called the plugin and created one
   order/command/hold with `price_source=MARKET_REFERENCE_CLOSED_BAR` and
   `price_at_ms=600000`.

The clock finding was not reopened; its 5/5 probe evidence stands.

## 3. The attempt-2 rule under test

One validator (`_validated_closed_bar`) is applied before `_feed` on both the
live observation and every warm-up history element:

| rejection code | trigger |
|---|---|
| `NON_BAR_MARKET_DATA` (warm-up: `STRATEGY_WARMUP_NON_BAR_DATA`) | any non-KLINE type |
| `UNFINISHED_BAR` (warm-up: `STRATEGY_WARMUP_UNFINISHED_BAR`) | `complete`/`is_closed` is explicitly false |
| `BAR_INTERVAL_REQUIRED` | no interval, or an interval outside `1m/5m/15m/1h/4h/1d` |
| `BAR_OPEN_TIME_REQUIRED` | no explicit `open_time_ms` |
| `BAR_CLOSE_TIME_REQUIRED` | no supplied close/availability field at all |
| `BAR_TIMESTAMP_INCONSISTENT` | any supplied close value ≠ `open_time_ms + interval_ms` |
| `BAR_CLOSE_PRICE_REQUIRED` | no explicit `kline_close` |
| `INVALID_BAR_CLOSE` | `kline_close` is not finite or ≤ 0 |
| `BAR_NOT_AVAILABLE_YET` (warm-up: `STRATEGY_WARMUP_BAR_NOT_AVAILABLE`) | `available_at_ms > decision clock` |

Warm-up wraps field failures as `STRATEGY_WARMUP_INVALID_BAR:<code>`, validates
the **whole** window before feeding or counting anything, and blocks on a short
window or duplicate-only window (`STRATEGY_WARMUP_HISTORY_INCOMPLETE`). The
observation `timestamp` and `data.price` are never used to invent a close or an
availability.

## 4. Acceptance mapping (corrected) and verdict

| item | oracle | independent evidence | verdict |
|---|---|---|---|
| 3.1 | 13.15 + BAR_V1; venue anchor + monotonic elapsed; wall steps cannot move visibility; failing source fails closed | `VC` 6/6, both parent probes 5/5, `IN` exact boundary | pass |
| 3.2 | MARKET reference = bar close at `close_time_ms` with bar id; LIMIT keeps the signal price | `IN` MARKET (1 200 000, `bar:BTCUSDT:1m:1140000`, 100), LIMIT (99.5 vs close 104.25), `IG`; `CB` | pass |
| 3.3 | feed and warm-up accept completed KLINE only; every other type refused before side effects; no opt-in | `IN` six non-bar history types + valid prefixes + six live types via `IG`; parent ingress probe TICKER/TRADE | pass |
| 3.4 | `warmup_events = max(deployment, plugin)`; stateless plugin warm at zero | `CB` 3.4 unchanged and rerun | pass |
| 3.5 | per-instrument `{state, observations, required}`; all WARM gates the runner | `CB` 3.5, `IN` blocked-state instrument counts | pass |
| 3.6 | restart re-collects; replay/out-of-order no double count; failure codes block; warm-up signals never trade | `CB` 3.6 (5 codes), `IN` duplicate/short/mixed windows and valid control | pass |
| 3.7 / 3.8 / 3.11 | submit/retry eligibility gate; untrusted provenance; storage outage code | `EL` 28/28 rerun | pass |
| 3.9 | `BacktestApplication.submit` / `validate_dataset_set`: quote, interval, venue, duplicates, shared completed-bar calendar before queueing | `EL`: an accepted set is QUEUED, then five incompatible sets are refused with `store.jobs` unchanged; `validate_dataset_set` rejects five invalid documents and accepts the positive control (6 cases, rerun) | pass |
| 3.10 | real PostgreSQL: no trusted link, linked-unpublished, each published flag, lifecycle, `HISTORY_STORE_UNAVAILABLE`; CI registered | `PG` 5/5 on a real server | pass |

The parent's two new counterexamples are 3.1/3.2/3.3/3.4/3.5/3.6 subcases, not
new IDs, matching the parent's routing text.

## 5. Independent cases and what they prove

- **History type gate**: each of TICKER / TRADE / DEPTH / FUNDING / INDEX_PRICE /
  MARK_PRICE as the sole history member → `STRATEGY_WARMUP_NON_BAR_DATA`, state
  BLOCKED, plugin calls 0, observations 0, and a subsequent valid bar raises
  `NotReady` with no order/command/hold.
- **No partial acceptance**: `[valid, TICKER]` and `[valid, missing-close-bar]`
  with `events=1` both leave plugin calls and observations at 0, proving the
  window is validated before the first `_feed`/`_count` (a valid prefix alone
  would otherwise be enough to warm).
- **Eight malformed-field cases**: missing open, missing interval, unsupported
  interval, missing close fields, contradictory close, missing close price, zero
  close, negative close; each asserts the exact
  `STRATEGY_WARMUP_INVALID_BAR:<code>`, zero plugin calls and zero observations.
- **Window rules**: future bar → `STRATEGY_WARMUP_BAR_NOT_AVAILABLE`; explicit
  unfinished bar → `STRATEGY_WARMUP_UNFINISHED_BAR`; duplicate-only and short
  windows → `STRATEGY_WARMUP_HISTORY_INCOMPLETE` with no plugin call.
- **Valid control**: two completed bars warm the plugin (2 calls), no order is
  placed during replay, then a bar at the exact close trades once with MARKET
  provenance, 100.1 on both reservation layers, conservation `{}`, one order and
  one hold; replay returns the same `cl_ord_id` without a second trade.
- **Parent live counterexample**: `open=600000`, timestamp at open, missing
  close/available/kline_close, decision 659500 → `BAR_CLOSE_TIME_REQUIRED`, all
  five counters 0; the same bar with the timestamp at the close is also refused,
  proving the timestamp is not a substitute.
- **Live field rules**: missing close price, contradictory close, unsupported
  interval are each refused before the plugin/account.
- **Boundary and provenance**: 659999 for a 660000 bar → `BAR_NOT_AVAILABLE_YET`;
  at 660000 admitted once at price 100 / `price_at_ms=660000` / bar id; LIMIT at
  99.5 keeps `PRICE_SOURCE_SIGNAL_LIMIT` and the declared price.
- **Parent probe corroboration** (`execution-r9/parent_ingress.stdout.txt`,
  read-only rerun): `ticker_history` → `prepared=false`, BLOCKED, plugin 0, and
  the next bar raises `NotReady: STRATEGY_WARMUP_NON_BAR_DATA` with 0 effects;
  `missing_close_at_659500` → `TradingError: BAR_CLOSE_TIME_REQUIRED` with 0
  effects; `explicit_close_at_660000` → one order at 104.25,
  `price_at_ms=660000`, `price_reference_id=bar:BTCUSDT:1m:600000`; both controls
  pass.

## 6. Findings register

| id | owner | state | closure evidence |
|---|---|---|---|
| `P3-PARENT-INPUT-TYPE-GATE` | production | **closed** (attempt 2) | the parent's two counterexamples are reproduced as tests and now fail closed (TICKER history blocks with 0 plugin calls; missing close is `BAR_CLOSE_TIME_REQUIRED` with 0 effects); the parent's own ingress probe is 5/5 exit 0 on the same bytes; `IN` 25/25 |
| `P3-R1-3.1-CLOCK` | production | closed (attempt 2) | `VC` 6/6 and both parent probes 5/5; not reopened |
| `P3I-F-1` | tester | closed | black hunk on the tester's PG file |
| `P3I-F-2` | tester | closed | undeclared warm-up probe in the round-3 VC fixture |
| `P3I-F-3` | tester | closed | b1 command count included the setup ALLOCATE |
| `P3I-F-4` | tester | closed | new ingress suite review (no production finding) |
| `P3I-F-5` | tester | closed | the round-8 boundary case moved the clock without refreshing the account snapshot and hit `ACCOUNT_SNAPSHOT_STALE`; fixed by keeping clock/snapshot at NOW and moving the bar by +1 ms |

## 7. Gate checklist

| gate | status | basis |
|---|---|---|
| G1 scope | pass | tester writes: the six tester test files, the tester-owned phase-2 b1 fixture, this report, tester artifacts, one existing CI line. No production, engineer test, contract, architecture or AGENTS edit. Parent probes/returns are byte-identical |
| G2 no weakened assertions | pass | §5 maps each new case; no assertion deleted, no xfail/skip/tolerance; the old 70 + b1 rerun unchanged; the only round-8 change was a fixture clock fix that keeps every ingress assertion |
| G3 executed evidence | pass | 25/70/1/5 plus both probes 5/5, raw returncodes and JUnit; the single round-8 failure was analyzed as a fixture defect and rerun green |
| G4 fail-closed | pass | every missing/contradictory field, non-bar type, future/unfinished bar and unsynced clock is refused before side effects |
| G5 no side effects | pass | each refusal asserts plugin 0 / exchange 0 / orders 0 / commands 0 / holds 0; blocked runners refuse later valid bars |
| G6 CI + frozen docs | pass | the existing tester CI line gains one file and loses no gate; parent artifacts and historical manifests untouched |

## 8. Limitations and unexecuted checks

- The tester's command channel is ACL-denied
  (`SetNamedSecurityInfoW failed (Win32 5)`); all execution is delegated.
  Authorship, expected values and verdicts are the tester's.
- Raw stdout may omit pytest's `N passed`; counts come from dot progress and,
  authoritatively, the JUnit attributes and testcase elements.
- `PG` runs against `POSTGRES_CONNECTION_STRING`; every case creates and drops a
  unique database, but the server's database list is not auditable from output.
  The DSN is not printed.
- Not executed by the tester: the full `trader/tests` suite (engineer evidence:
  3649 passed; next expected 3674 with `IN`), P0 and the safety regressions
  (production untouched by the tester), `trader/`-wide style, an actual GitHub
  Actions run (registration only), frontend, Binance network/private stream and
  Compose.
- The two parent probes are the parent's fixtures; the tester only has them
  re-run read-only and cannot repair them. Their 5/5 is behaviour evidence, not a
  substitute for `IN`/`VC`/`IG`; both are reported.
- Expected rejection strings are the published reason codes of the frozen fix
  (`docs/INTERFACE_CONTRACTS.md` 13.15 and the fix handoff); the numeric
  expectations are hand-computed from BAR_V1, not copied from the engineer's
  assertions.
- `warmup_source` truncation (a source that silently returns fewer bars) still
  surfaces as `STRATEGY_WARMUP_HISTORY_INCOMPLETE`; the tester did not add a
  source that lies about its own length beyond the duplicate/short cases above.

## 9. Frozen workspace

The six phase-3 tester test files (`CB`, `VC`, `IG`, `IN`, `EL`, `PG`), the
phase-2 b1 fixture and this report are frozen for the final fingerprint.
Final hashes: `IN` `7eb1b718165cfa720608c831d5f92d0ae20e0f5c8d1ab4ee4718f305b123b2e7`,
b1 `d1563891…d6f804`, CB `a25eb96b…b32da55`,
EL `00bc6951…a03f8ea`, PG `170dba04…2dc18b`, VC `a30a5113…3244cd`, IG
`63cce982…0b534a3a`, CI `decd61eff7268f618d6e67d3946790c93b41bccde5b72a39f89e5c86476a95b6`,
parent boundary probe `4744cf59…7180d8`,
parent ingress probe `6bd39b23…47163f`, parent return 1 JSON `26207d58…c444d2`,
parent return 2 JSON `294b780bd2103cabb528db85ac7d61e7968d3c4c84812a5336f9652364bc97ed`
(packet md `b54f23ae…139a13`); source digest
`bf5a26272dd30ee8f700704420f8def56c5b30a901db525a6e8e146bd3c714fd`
(`final-source-manifest-r9.json`, 40 files). The final single-step fingerprint
re-hashes the report and these twelve inputs; the report is not edited afterwards.
