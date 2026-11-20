# RelayAid measured results

Generated 2026-11-20T00:41:35+00:00 on Windows 11, Python 3.14.6, Intel64 Family 6 Model 186 Stepping 2, GenuineIntel.

All values are measured by `scripts/benchmark.py`; E1-E7 use in-process nodes (separate SQLite files, full signed/gzip protocol over an ASGI transport), E8 uses four OS processes over loopback HTTP.

## E1 - Sync payload and incrementality
| events | initial_sync_ms | initial_wire_bytes | initial_raw_bytes | wire_bytes_per_event | compression_ratio | batches | incremental_1_event_wire_bytes | incremental_events_transferred | noop_sync_wire_bytes | noop_sync_ms |
|---|---|---|---|---|---|---|---|---|---|---|
| 10 | 43.2 | 1770 | 5221 | 177.0 | 2.95 | 1 | 658 | 1 | 321 | 23.5 |
| 100 | 144.6 | 12916 | 50488 | 129.2 | 3.91 | 1 | 657 | 1 | 323 | 21.4 |
| 1000 | 1647.1 | 129114 | 507559 | 129.1 | 3.93 | 10 | 667 | 1 | 324 | 30.5 |

## E2 - Convergence after a three-way partition
| order | updates_per_node | nodes | trials | converged | no_lost_updates | mean_ms | max_ms | mean_rounds | mean_events_moved | mean_wire_kb | mean_open_conflicts |
|---|---|---|---|---|---|---|---|---|---|---|---|
| ring | 20 | 3 | 5 | 5 | 5 | 316.7 | 323.5 | 2 | 120 | 19.2 | 0.6 |
| ring | 100 | 3 | 5 | 5 | 5 | 747.5 | 896.6 | 2 | 600 | 78.9 | 1 |
| reverse_ring | 20 | 3 | 5 | 5 | 5 | 323.0 | 327.0 | 2 | 120 | 19.2 | 0.6 |
| reverse_ring | 100 | 3 | 5 | 5 | 5 | 789.0 | 849.7 | 2 | 600 | 79.0 | 1 |
| star_via_server | 20 | 3 | 5 | 5 | 5 | 445.9 | 461.4 | 3 | 182 | 28.0 | 0.6 |
| star_via_server | 100 | 3 | 5 | 5 | 5 | 1209.2 | 1296.2 | 3 | 902 | 115.3 | 1 |
| full_mesh | 20 | 3 | 5 | 5 | 5 | 473.1 | 507.6 | 2 | 120 | 20.9 | 0.6 |
| full_mesh | 100 | 3 | 5 | 5 | 5 | 980.5 | 1035.2 | 2 | 600 | 79.8 | 1 |

## E3 - Randomized partition chaos
```json
{
  "runs": 25,
  "converged": 25,
  "no_lost_updates": 25,
  "conflict_sets_identical": 25,
  "total_writes": 1043,
  "total_duplicates_ignored": 1021,
  "runs_with_open_conflicts": 25
}
```

## E4 - Allocation safety under partitions
```json
{
  "attempts": 267,
  "accepted": 51,
  "blocked": 216,
  "token_transfers": 0,
  "releases": 6,
  "forged_events_injected": 10,
  "forged_events_rejected_by_every_replica": 10,
  "audit_ok_on_all_nodes": true,
  "boat_active_allocations": 1,
  "kits_reserved": 300,
  "kits_total": 300,
  "kits_never_overallocated": true,
  "allocations_blocked_counter": 232
}
```

## E5 - Duplicate delivery
```json
{
  "events": 200,
  "converged": true,
  "duplicates_ignored": 400,
  "needs_per_node": [
    200,
    200,
    200
  ]
}
```

## E6 - Lossy links (retry and backoff)
| drop_rate | trials | converged | sessions | session_success_rate | retries |
|---|---|---|---|---|---|
| 0.1 | 5 | 5 | 60 | 1.0 | 23 |
| 0.3 | 5 | 5 | 60 | 0.95 | 70 |
| 0.5 | 5 | 5 | 66 | 0.803 | 157 |

## E7 - Projection rebuild cost
| events | rebuild_ms_median | local_write_ms_incl_rebuild |
|---|---|---|
| 100 | 9.6 | 20.4 |
| 1000 | 109.7 | 113.7 |
| 3000 | 311.8 | 859.2 |

## E8 - Real processes over HTTP
```json
{
  "transport": "real HTTP over loopback, 4 OS processes",
  "updates_per_node": 50,
  "local_writes_total": 150,
  "mean_local_write_ms": 8.36,
  "converged": true,
  "rounds": 2,
  "convergence_ms": 394.3,
  "wire_kb": 40.6
}
```
