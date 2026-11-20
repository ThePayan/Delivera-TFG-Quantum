# Annealer experiment (Amplify AE / Toshiba SQBM+)

A light experiment, run by hand outside Docker, that the gateway can also call as the solver
`AMPLIFY` (see below). It checks that a QUBO encoding of the CVRP works on real annealers and how good the
routes are against the Cordeau BKS. Code in
[`experimentation/annealing`](../experimentation/annealing).

## How it solves an instance

The MD-CVRP is too large for one QUBO, so it is split classically into small CVRP subproblems,
and only those go to the annealer:

1. Each customer goes to its nearest depot.
2. Inside each depot, customers are sorted by angle around the depot (sweep) and cut into groups
   of at most `maxCustomersPerSubproblem` (8 by default).
3. Each group is a single-depot CVRP with `ceil(demand / capacity)` vehicles, written as a QUBO:
   `x[t, i, k] = 1` when vehicle `k` is at node `i` in step `t`, with node 0 the depot (a vehicle
   at the depot is idle). Constraints: one node per vehicle and step, every customer exactly once,
   load at most the capacity. Objective: route distance.
4. If the annealer returns no feasible solution, the group is retried with one more vehicle, up to
   two times. If it still fails, a classical fallback (sweep order, new vehicle when the next
   customer does not fit) completes the solution, and the response marks that group as `fallback`.
5. The solution is validated with the same rules as `compare_solvers.py` (`Instance.violations`)
   and its cost is recomputed from the stops.

With the defaults every subproblem needs at most 126 binary variables, far below the size limits
of either service.

Known limits, on purpose:

- **Maximum duration is not modelled.** The 22 instances with a duration limit will usually come
  back with duration violations, listed in `summary.violations`.
- **Nearest-depot assignment ignores the fleet.** In p07 and p11 it overloads a depot; the routes
  are spread over the depot's vehicles in turns (a vehicle may do several trips), and the
  validation says when the fleet is exceeded.
- The decomposition fixes which customers share a subproblem, so the result is bounded by it: the
  annealer only optimises inside each group.

## Running it

Once, from `Solver-FMS`:

```bash
py -3.11 -m venv experimentation/annealing/.venv
experimentation/annealing/.venv/Scripts/python.exe -m pip install -r experimentation/annealing/requirements.txt
```

The tokens never travel in the request. Copy
[`.env.example`](../experimentation/annealing/.env.example) to `experimentation/annealing/.env`
and fill in `AMPLIFY_TOKEN` (and `SQBM_TOKEN` for Toshiba SQBM+, `HITACHI_TOKEN` for Hitachi
Annealing Cloud Web; `SQBM_URL` only for the AWS version of SQBM+). That `.env` is
ignored by Git. A variable already set in the shell takes precedence over the file.

Then, every time, from `Solver-FMS`:

```powershell
experimentation\annealing\.venv\Scripts\python.exe -m uvicorn experimentation.annealing.api:app --host 0.0.0.0 --port 8096
```

`--host 0.0.0.0` is only needed for the gateway path below; Windows may ask once to allow Python
through the firewall.

The Postman collection is
[`annealer-cvrp.postman_collection.json`](../experimentation/annealing/annealer-cvrp.postman_collection.json);
change the `instance` variable to try another Cordeau instance. The interactive docs are at
`http://localhost:8096/docs`.

## Through the gateway (solvers `AMPLIFY` and `SQBM`)

The service also speaks the engine contract, so the gateway lists it as two solvers that share
the same decomposition and QUBO and differ only in the annealer: `AMPLIFY` (Fixstars Amplify AE)
and `SQBM` (Toshiba SQBM+). They run like any other engine:

```
POST http://localhost:8090/api/v1/fms/instances/send?fileName=p01&solverType=AMPLIFY
POST http://localhost:8090/api/v1/fms/instances/send?fileName=p01&solverType=SQBM
```

with an optional body of parameters: `{"timeLimitMs": 3000, "maxCustomersPerSubproblem": 20}` by
default for `AMPLIFY`, and 8 customers per group for `SQBM`, which only solves small groups. The
gateway's response has no field for how many groups the annealer solved, so the engine prints it
on its console (`[SQBM] p01: coste ..., 5/9 grupos por el annealer, ...`).

Two time guards keep the gateway from cutting the engine off at its 300 s limit: an annealer call
that does not answer in 45 s is abandoned (a hung connection was seen on 18/11/2026), and after
240 s no new calls start; the remaining groups go to the classical fallback. It is not a container of the compose: it runs on the host with its own token, so it has
to be started with `--host 0.0.0.0` for the gateway container to reach it at
`host.docker.internal:8096` (`FMS_ENGINES_AMPLIFY_URL`). While it is not running the gateway lists
`AMPLIFY` as `DOWN`. On this path distances come from the gateway's distance matrix and the fleet
and capacity from its vehicle list; routes are spread over each depot's vehicles in turns.

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/api/v1/engine/solve` | The engine contract the gateway calls (solver `AMPLIFY`) |
| GET | `/actuator/health` | The gateway's health probe |
| GET | `/health` | Service status and which tokens are configured |
| GET | `/instances` | The 33 Cordeau instances with their size and BKS |
| POST | `/cordeau/{name}/solve` | Solves a Cordeau instance |
| POST | `/solve` | Solves your own instance, in the JSON format of `instances-MD-CVRP-JSON` |

Options (the body of `/cordeau/{name}/solve`, or `options` in `/solve`):

| Field | Default | Meaning |
| --- | --- | --- |
| `client` | `ae` | `ae` for Fixstars Amplify AE, `sqbm` for Toshiba SQBM+, `hitachi` for Hitachi Annealing Cloud Web (needs `HITACHI_TOKEN`; Amplify embeds the QUBO into Hitachi's King graph, so dense subproblems use many more spins) |
| `timeLimitMs` | 1000 | Time of each annealer call (100 to 10000; the free plans allow 10 s) |
| `maxCustomersPerSubproblem` | 8 | Customers per QUBO subproblem (1 to 30) |
| `dryRun` | false | Only decomposes and counts variables; does not call the annealer |

The response carries the routes (`vehicleId`, `depotId`, `stops`, `totalLoad`, `distance`), each
subproblem with its size, attempts and method, and a `summary` with the total distance, the gap to
the BKS, feasibility, violations, how many groups the annealer solved and the time spent. Each real
run (not `dryRun`) is also saved to `results/annealing-<instance>-<date>.json`, which Git ignores.

## First results

Hitachi Annealing Cloud Web (18/11/2026, default machine type, `num_executions` 10): the token works
and trivial models are solved correctly (an unconstrained 3-variable problem and a 3-way one-hot).
But it returned **no feasible solution** for anything with a real structure: not the 4-customer
CVRP (20 variables, with penalty weights of 1, 3 and 10 times the largest distance), not the same
CVRP without the capacity constraint, and not a 4-city TSP (16 variables). The likely cause is the
embedding: Amplify has to map these dense QUBOs onto Hitachi's sparse King graph, which chains
spins together and loses precision in the coefficients. As it stands, Hitachi is not usable for
this encoding; it would need a sparser formulation.

Amplify AE (18/11/2026, one run each, so not yet a statistical result). Every subproblem was solved
by the annealer on the first attempt, and every solution is feasible:

| Instance | `maxCustomersPerSubproblem` | `timeLimitMs` | Subproblems | Largest QUBO | Distance | Gap to BKS | Annealer time | Wall time |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 4-customer CVRP | 8 | 1000 | 1 | 20 | 12.46 (fallback: 14.25) | | 1.0 s | 12 s |
| p01 | 8 | 1000 | 9 | 126 | 712.81 | 23.57 % | 7.3 s | 63 s |
| p01 | 12 | 2000 | 6 | 312 | 629.31 | 9.09 % | 10.2 s | 28 s |
| p01 | 20 | 3000 | 4 | 576 | 609.24 | 5.61 % | 12.1 s | 42 s |

Larger subproblems clearly pay off: with 20 each depot is a single QUBO, and the gap drops from
23.6 % to 5.6 %, the same order as the genetic engine on p01. The wall time is dominated by the
network, not by the annealer. The connection to Amplify fails intermittently (SSL errors and
timeouts), so network errors are retried, and a subproblem that still cannot connect falls back
to the classical routes and says so.

### Single-depot CVRP: Amplify AE against Toshiba SQBM+

`python -m experimentation.annealing.compare_cvrp p01 ae sqbm` turns each depot of a Cordeau
instance, with its nearest customers, into an independent CVRP and sends it whole to one QUBO.
On p01 (18/11/2026, 3000 ms per call, one run per cell):

| CVRP | Customers | Sweep heuristic | Amplify AE | Toshiba SQBM+ |
| --- | --- | --- | --- | --- |
| p01-dep1 | 13 | 202.71 | 161.66 | no feasible solution |
| p01-dep2 | 17 | 284.40 | 217.70 | no feasible solution |
| p01-dep3 | 11 | 131.36 | 115.94 | no feasible solution |
| p01-dep4 | 9 | 128.77 | 113.95 | 135.56 |

- Amplify AE solves all four, 10 % to 23 % shorter than the sweep heuristic. It calibrates the
  weight of each constraint by itself (the SDK warns that it ignores the weight we set).
- SQBM+ through Fixstars Amplify needs only `SQBM_TOKEN` (`SQBM_URL` is only for the AWS version).
  With its default `Qubo` solver mode it found no feasible CVRP at all, even with penalty weights
  20 times larger, although it solves a 6-city TSP and the CVRP without the capacity constraint:
  the capacity inequality, encoded with slack variables, is what breaks it. The client therefore
  uses the `Qplib` mode, where constraints travel as such. Even then it only solves the 9-customer
  CVRP, worse than the sweep heuristic, and still fails on 11 and 13 customers with the 10 s
  maximum of the free plan.

### Multi-depot through the gateway: `SQBM` against `AMPLIFY`

p01 through `instances/send` (18/11/2026, one run): `SQBM` with 8 customers per group and 2000 ms
per call gives **847.83 (47.0 % gap)** in 86 s, with 5 of the 9 groups solved by SQBM+ and 4 by the
fallback. `AMPLIFY` on the same groups gave 712.81 (23.6 %), and 609.24 (5.6 %) with 20 customers.

## Cost of a run

One annealer call per subproblem, plus a retry for each group it fails: p01 has 9 subproblems, the
largest instances (p21 to p23) have 45. With `timeLimitMs` at 1000, p01 should need somewhere between 9
and 27 seconds of annealer time plus network latency (not measured yet). Use `dryRun` first to see how many calls an instance will make.
