# Hot-path optimization: authenticated read path (get_messages)

Commit: 8d601da perf(read-path): eliminate redundant account lookup per authenticated command

## Finding (measured, not assumed)

The perf baseline (t_af15796f) flagged get_messages/get_conversation as the
most expensive commands and suggested "SQL read optimization". Profiling
contradicted that:

- The message_page correlated subquery is NOT a bottleneck. On the real DB
  (16k msgs, WAL, synchronous=FULL) it measured 629 us/query vs 653 us for a
  LEFT JOIN variant — the subquery is already indexed and slightly faster.
  Project docs docs/perf/PERFORMANCE.md §2.3 already state "Aucune requête
  coûteuse détectée". A "subquery → JOIN" rewrite would have been a
  regression, not an optimization. None was made.
- With a warm auth cache, get_messages is ~1.3 ms in-process; the SQL is not
  the bottleneck.

The real, genuine waste found: the dispatch path fetched the authenticated
account row THREE times per read command:
  1. inside `_authenticate` (validates the principal);
  2. again in `_dispatch` (observer/principal checks);
  3. via `_org_of`, evaluated eagerly as an argument even for non-audited
     reads (get_messages is not in `_AUDITED_COMMANDS`).

## Change

`_authenticate` now returns the `(username, account_row)` it already
fetched; `_dispatch` reuses that row instead of re-fetching; the audit
`org_name` is read from the same row (only for audited commands). Net
effect: exactly ONE indexed SELECT per authenticated command instead of
three. Behavior preserved (web-local identity still yields row=None;
audited org attribution identical).

## Before / after (same method, warm auth cache, real config)

Harness: benchmarks/bench_dispatch_read.py, in-process, W=1 and W=8.

| metric                     | BEFORE (committed pre-fix) | AFTER (8d601da) | Δ          |
|----------------------------|----------------------------|-----------------|------------|
| accounts.get per get_messages | 3.00                      | 1.00            | **−66%**   |
| W=1 mean latency           | 1.40 ms                    | 1.31 ms         | −6%        |
| W=8 mean latency           | 39.07 ms                   | 24.77 ms        | −37%       |
| W=8 p95 latency            | 89.83 ms                   | 66.82 ms        | −26%       |
| W=8 throughput (RPS)       | 202                        | 309             | +53%       |

The accounts.get reduction (3→1, deterministic) is the real measured win.
W=8 latency/RPS deltas are partly machine-load noise (load ~4.7 during both
runs; the project documents ±18% machine variance for pure reads, and the
GIL as the remaining ceiling — docs/perf/PERFORMANCE.md §12.6), so they are
reported as directional, not exact.

## Regression guard

tests/test_read_path_account_lookup.py:
- `test_get_messages_read_performs_single_account_lookup` — asserts exactly
  1 accounts.get per authenticated read (fails if the redundant fetch is
  reintroduced).
- `test_get_messages_response_unchanged` — behavioral guard (read still
  succeeds, empty mailbox returned correctly).

Targeted suite green (14 files): test_read_path_account_lookup, test_auth,
test_messages, test_conversations, test_audit_metrics, test_principal_type,
test_spec_web_d6, test_observers_web, test_org, test_organizations,
test_auth_cache, test_e2e_journey, test_concurrency, test_webui. All pass.
