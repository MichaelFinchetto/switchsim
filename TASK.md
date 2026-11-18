# Work package: optiswitch 1.0

`optiswitch` (see `README.md`) simulates an optical circuit switch with a
single FIFO scheduler, one wavelength per link, synthetic workloads only, and
single-run metrics. This work package takes it to a 1.0 that can answer real
design questions: compare schedulers, model WDM links, replay measured
traffic, and report results with statistical confidence.

Six stages, in order. Later stages build on and are validated against earlier
ones. Hard constraints throughout:

* **No third-party runtime dependencies.** Standard library only (`pytest` for tests).
* The existing 55 tests keep passing. Default behaviour of every existing
  function and CLI command is unchanged unless a stage says otherwise; in
  particular, a 1-wavelength run must produce **bit-identical** results to
  today's.
* Every new function gets tests. Where this brief states a numerical
  property, write the test from the property, not from the number your code
  happens to produce.
* Implement the signatures given here exactly (names, defaults, units).

---

## Stage 1 — Schedulers

Add two schedulers to `optiswitch/scheduler.py`, registered via `@register`:

**`RoundRobinScheduler`**, `name = "rr"`. Keeps a rotating `pointer` over
ingress ports (attribute, reset to 0 by `reset()`). Each round: starting at
the pointer, visit ingress ports in cyclic order; for each, grant the
*oldest* pending flow from that port whose egress port is still free this
round; after the round, move the pointer to the port *after the first one
that was granted*. If nothing was granted the pointer **does not move**
(empty rounds must not rotate priority). Ties on age broken by flow id.

**`MaxWeightScheduler`**, `name = "maxweight"`, constructor
`MaxWeightScheduler(n_hosts, weight="bytes")` with `weight in {"bytes", "age"}`
(`SchedulerError` otherwise). Each round compute a weight per pending flow:
`size_bytes` or `now_ns - arrival_ns`. Build a **greedy maximal matching**:
sort pending flows by `(-weight, arrival_ns, flow.id)` and grant each whose
ingress and egress ports are still free this round. `describe()` includes
the weight mode.

Requirements (write these as tests):

* Both schedulers always return a *maximal* matching: after their selection,
  no pending flow remains whose ingress and egress ports are both free.
  Add a generic helper `is_maximal(selected, pending, free_in, free_out,
  grantable=None, wavelengths=1) -> bool` in `scheduler.py` and check it in
  the engine: `Simulation(..., strict=True)` / `simulate(..., strict=True)`
  raise `SchedulerError` when a scheduler returns a non-maximal set. This
  applies to FIFO too (it already satisfies it). The **only** change you may
  make to an existing test is passing `strict=False` in
  `tests/test_sim.py::test_stall_detection`, whose deliberately lazy
  scheduler is now caught by the check.
* Construct a scenario where host 0 has three pending flows and hosts 1–3
  one each, all to the same egress, with reconfiguration and latency set to
  zero so only port contention matters: under `rr` the grant order by source
  is `[0, 1, 2, 3, 0, 0]`; under `fifo` it is `[0, 0, 0, 1, 2, 3]`. Assert
  both lists explicitly.
* Construct a conflict where a large and a small flow compete for one egress
  port that is initially held by a third flow, with the small flow arriving
  earlier than the large one: when the port frees, `maxweight(bytes)` grants
  the large one, `maxweight(age)` the small (older) one, `fifo` the small one.
* On a 2000-flow uniform workload at load 0.7 all three complete every flow,
  pass `check_invariants`, and are deterministic across two runs.
* CLI `run --scheduler rr|maxweight [--weight bytes|age]`; `schedulers` lists all three.

## Stage 2 — WDM links

Give every link `W` wavelengths; a circuit occupies one wavelength on the
ingress port and the **same** wavelength index on the egress port (no
wavelength conversion). Each wavelength carries the host's full
`bandwidth_gbps`, so a link's capacity is `W × bandwidth`.

* `SwitchSpec.wavelengths: int = 1` (`TopologyError` if `< 1`); JSON key
  `"wavelengths"`, read with default 1 and **written only when it is not 1**
  (so existing topology files and their tests are unchanged);
  `Topology.star(..., wavelengths=1)`; `bisection_bandwidth_gbps` scales by
  `W`; CLI `--wavelengths` on `run` and `gen-topology`.
* Scheduler contract: `Scheduler.wavelengths: int = 1` is a class/instance
  attribute that the engine sets from the topology before `reset()`. A port
  may be granted up to `wavelengths` flows per round; all three shipped
  schedulers must honour that budget (so at `W=2` two flows from the same
  source to different destinations are granted in the *same* round).
  `check_selection(selected, free_in, free_out, wavelengths=1)`: a port may
  appear at most `wavelengths` times and must be free.
* Engine (`sim.py`): track free wavelength sets per ingress and egress port.
  `free_in` / `free_out` passed to the scheduler are the sets of ports with
  *at least one* free wavelength. After selection the engine assigns
  wavelengths **sequentially in the order returned**, first-fit on the
  *lowest common free* wavelength of `(src, dst)`; a flow with no common free
  wavelength at its turn is **not granted** this round (it stays pending,
  `attempts += 1`) — this is not an error. `FlowRecord.wavelength:
  Optional[int]`; `PortUsage(wavelengths=1)` tracks each wavelength's busy
  interval, exposes `free_wavelengths`, and reports `busy_ns` summed over
  wavelengths, so utilisation is `busy_ns / (span × W)`.
* Maximality check from Stage 1 must account for wavelengths: a pending flow
  only counts as "grantable" if a common free wavelength exists.
* Metrics/report: `port_utilisation` and `ports_csv` divide by `W`;
  `flows_csv` gains a `wavelength` column; `check_invariants` verifies
  (a) `wavelength` is the same on both ends (trivially, since one field) and
  within `[0, W)`, (b) at most `W` concurrent circuits per port and no two
  concurrent circuits on the same `(port, wavelength)`.

Requirements:

* `wavelengths=1`: bit-identical `FlowRecord` timestamps, `attempts` and
  `events_processed` to the current code. Before you touch the engine,
  record a fingerprint of the current `fifo` results on a 2000-flow uniform
  workload at load 0.9 with `guard_ns=100` (seed 4, fixed 500 kB flows, and
  seed 9, `BimodalSize(50_000, 5_000_000, 0.2)`), store it under
  `tests/fixtures/`, and test against it.
* `wavelengths=2`, two flows `0→1` and `0→2` arriving at 0: both granted at 0
  on wavelengths 0 and 1 and both complete at the same time.
* Wavelength blocking: construct a case where ingress `a` has only λ0 free
  and egress `b` has only λ1 free, with a pending flow `a→b`; the flow is not
  granted until a wavelength becomes common, even though both ports appear in
  `free_in`/`free_out`.
* Throughput on a 16-host uniform workload at load 1.5 (relative to 1-λ
  capacity) with `W=2` is at least 1.6× the `W=1` throughput, and the
  `W=2` run completes all flows.

## Stage 3 — Importing measured traffic

New module `optiswitch/ingest.py` that turns a packet-level CSV into flows.

* Input CSV columns (header required, any order): `timestamp_us` (float),
  `src`, `dst` (host *names*), `bytes` (int). Rows may be unsorted.
  Malformed rows raise `IngestError` with the 1-based line number.
* `HostMap`: maps names ↔ ids. `HostMap.from_topology(topology)` uses the
  topology's names (unknown names in the CSV → `IngestError`);
  `HostMap()` assigns ids in order of first appearance (src before dst within
  a row). `to_dict()`/`from_dict()` for JSON persistence.
* `aggregate(packets, gap_us, hostmap) -> list[Flow]`: group packets by
  `(src, dst)` in timestamp order; a packet more than `gap_us` after the
  previous packet of the same pair **starts a new flow**. Flow `size_bytes` is
  the sum of its packets' bytes, `arrival_ns` is the first packet's timestamp
  converted to ns (round half up), ids assigned in order of arrival then
  `(src, dst)`. Packets with `src == dst` are dropped and counted.
* `import_csv(path, gap_us, hostmap=None) -> ImportResult(flows, hostmap,
  packets_read, packets_dropped, flows_per_pair: dict)`.
* CLI `import-csv IN --gap-us G --out FLOWS.jsonl [--topology T] [--hostmap-out MAP.json]`
  printing a one-line summary; `validate-trace` gains `--matrix` printing the
  demand matrix in bytes.

Requirements: a hand-built CSV with three pairs, including one pair whose
packets split into two flows exactly at the gap boundary (packet at
`t_prev + gap` is the **same** flow; `t_prev + gap + 0.001` starts a new one);
unsorted rows give the same flows as sorted; a replayed import round-trips
through `workload.save/load`; a name unknown to the topology fails with the
offending name in the message.

## Stage 4 — Multi-seed statistics

New module `optiswitch/stats.py` (standard library only — no SciPy, no numpy).

* `t_critical(df, level=0.95) -> float`: two-sided Student-t critical value.
  Use an embedded table for `df = 1..30` at levels 0.90, 0.95 and 0.99 and
  the normal quantile (1.645, 1.960, 2.576) for `df > 30`. `ValueError` for
  other levels or `df < 1`. Spot values: `t_critical(4, 0.95) == 2.776`,
  `t_critical(10, 0.99) == 3.169`, `t_critical(1, 0.90) == 6.314`.
* `confidence_interval(values, level=0.95) -> (mean, half_width)`; with one
  value the half-width is `inf`.
* `welch_t(a, b) -> (t, df)` with the Welch–Satterthwaite degrees of freedom.
* `run_study(topology, make_spec, scheduler_names, seeds, metrics=("throughput_gbps",
  "fct.mean_ns", "fct.p99_ns", "fairness")) -> Study` where `make_spec(seed)`
  returns a `WorkloadSpec` and metrics are dotted paths into
  `Summary.to_dict()`. `Study.table() -> dict[scheduler][metric] -> (mean, half_width, n)`;
  `Study.to_csv()` (one row per scheduler×metric) and `Study.compare(metric,
  a, b) -> dict` with `welch_t`, `df` and whether the two 95 % CIs overlap.
* CLI `study --seeds N --schedulers fifo,rr,maxweight [workload/topology flags] [--level 0.99] [--csv FILE]`
  printing a table of `mean ± half_width` per scheduler and metric.

Requirements: `confidence_interval([1,2,3,4,5])` → `(3.0, 1.963 ± 0.001)`;
`welch_t` of two identical samples is `0`; a study is reproducible for the
same seeds; with `seeds=range(5)` on an 8-host uniform workload with
`BimodalSize(50_000, 5_000_000, 0.2)` at load 0.7 the three schedulers'
throughput CIs all overlap (they are limited by the same offered load) while
`maxweight(bytes)` has a **higher** mean FCT than `fifo` (it starves small
flows) — assert the inequality on means, not on the CI.

## Stage 5 — Sweeps and comparison

* `optiswitch/sweep.py`: `sweep(topology_factory, spec_factory, scheduler_names,
  param, values, seed) -> SweepResult` where `param ∈ {"load", "reconfig_ns",
  "wavelengths", "n_hosts"}` and the factories receive the value. `SweepResult.rows`
  is a list of `(param_value, scheduler, Summary)`; `to_csv()` with columns
  `param,value,scheduler,throughput_gbps,fct_mean_ns,fct_p99_ns,fairness,reconfig_overhead,completed`.
* CLI `sweep --param load --values 0.2,0.4,0.6,0.8 --schedulers fifo,rr [flags] [--csv FILE]`
  and `compare --schedulers fifo,rr,maxweight [flags]` printing
  `report.comparison_table`.
* `report.markdown_table(summaries)` — same columns as `comparison_table`, GFM syntax.

Requirements: throughput is non-decreasing in `load` for every scheduler on
`[0.2, 0.4, 0.6]`; reconfiguration overhead is strictly increasing in
`reconfig_ns` on `[5000, 20000, 80000]`; mean FCT is non-increasing in
`wavelengths` on `[1, 2, 4]`; the CSV has `len(values) × len(schedulers)` data rows.

## Stage 6 — Documentation and release 1.0.0

* `docs/MODEL.md`: the timing model (a worked example with the numbers from
  `tests/test_sim.py::test_single_flow_timing`), the scheduler contract
  (inputs, outputs, maximality, wavelength assignment order), the list of
  simulation invariants and which test checks each, and the statistics
  conventions (two-sided CIs, Welch).
* `CHANGELOG.md` (Keep-a-Changelog) with a 1.0.0 entry covering every stage,
  and any behaviour a user could notice (e.g. the new maximality check, the
  `wavelength` CSV column).
* README: features, new commands, WDM model, file formats (hostmap JSON).
* Version `1.0.0` in `pyproject.toml` and `__init__.py`; exports
  `RoundRobinScheduler, MaxWeightScheduler, import_csv, run_study, sweep`.
* Fresh virtualenv: `pip install -e ".[test]"`, `python -m pytest` (under
  two minutes), installed `optiswitch` command.

---

## Deliverables

* All existing tests pass; suite grown to at least ~150 tests.
* A written summary: per stage, what was built and any design decisions;
  the Stage 2 throughput ratio you measured; the Stage 4 study table; the
  Stage 5 monotonicity results; anything you decided not to do and why.
