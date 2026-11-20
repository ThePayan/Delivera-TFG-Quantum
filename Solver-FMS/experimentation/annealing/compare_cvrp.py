"""
Compara annealers sobre CVRP de un solo deposito, sacados de una instancia Cordeau.

Cada deposito de la instancia, con los clientes que le tocan por cercania, es un CVRP
independiente. Asi se prueban los annealers en su caso mas limpio: un CVRP entero en
una sola QUBO, sin el troceo del multideposito.

Uso (desde Solver-FMS, con los tokens en experimentation/annealing/.env):

    experimentation\\annealing\\.venv\\Scripts\\python.exe -m experimentation.annealing.compare_cvrp p01 ae sqbm

Escribe la tabla por pantalla y la guarda en results/annealing-cvrp-<instancia>-<fecha>.md.
"""

import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from .. import BASE_DIR
from ..comparison import instance as cordeau
from .decomposition import classical_fallback, nearest_depot
from .qubo import Subproblem
from .solver import solve_instance

load_dotenv(Path(__file__).with_name(".env"))

TIME_LIMIT_MS = 3000
MAX_CUSTOMERS = 30  # nunca trocea: cada CVRP va entero a una QUBO


def single_depot_cvrps(name):
    """Un CVRP por deposito, en el formato de Instance, para que valide igual que siempre."""
    raw = __import__("json").loads((cordeau.INSTANCES_DIR / f"{name}.json").read_text(encoding="utf-8"))
    md = cordeau.Instance(name, raw)
    groups = nearest_depot(md)
    for position, depot in enumerate(raw["depots"]):
        depot_id = str(position + 1)
        ids = set(groups[depot_id])
        data = dict(raw, depots=[depot], customers=[c for c in raw["customers"] if str(c["id"]) in ids])
        yield cordeau.Instance(f"{name}-dep{depot_id}", data)


def sweep_cost(inst):
    """Referencia clasica minima: el barrido abriendo vehiculo cuando no cabe."""
    depot_id = next(iter(inst.depots))
    depot = inst.depots[depot_id]
    ids = sorted(inst.customers)
    sub = Subproblem(depot_id, depot, ids, [inst.customers[c] for c in ids], depot["capacity"], 1)
    from .decomposition import sweep
    sub.customer_ids = sweep(depot, ids, inst.customers)
    sub.customers = [inst.customers[c] for c in sub.customer_ids]
    routes = classical_fallback(sub)
    return inst.cost({"routes": [{"vehicleId": "v", "depotId": depot_id, "stops": r} for r in routes]})


def main():
    name, clients = sys.argv[1], sys.argv[2:] or ["ae", "sqbm"]
    header = "| CVRP | Clientes | Barrido clasico | " + " | ".join(
        f"{c.upper()} distancia | {c.upper()} factible | {c.upper()} annealer (s)" for c in clients) + " |"
    lines = [f"# CVRP de un deposito sacados de {name}, {datetime.now():%d/%m/%Y %H:%M}", "",
             f"{TIME_LIMIT_MS} ms por llamada, una QUBO por CVRP. Una ejecucion por celda.", "",
             header, "|" + "---|" * (3 + 3 * len(clients))]
    for inst in single_depot_cvrps(name):
        row = [inst.name, str(len(inst.customers)), f"{sweep_cost(inst):.2f}"]
        for client in clients:
            r = solve_instance(inst, client_kind=client, time_limit_ms=TIME_LIMIT_MS, max_customers=MAX_CUSTOMERS)
            s = r["summary"]
            ok = "si" if s["feasible"] and s["solvedByFallback"] == 0 else "no (respaldo)" if s["solvedByFallback"] else "no"
            row += [f"{s['totalDistance']:.2f}", ok, f"{s['solverSeconds']:.1f}"]
            print(inst.name, client, s["totalDistance"], ok, [a.get("reason", "") for e in r["subproblems"] for a in e["attempts"]], flush=True)
        lines.append("| " + " | ".join(row) + " |")
    report = "\n".join(lines)
    print("\n" + report)
    out = BASE_DIR / "results" / f"annealing-cvrp-{name}-{datetime.now():%Y-%m-%d-%H%M}.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text(report + "\n", encoding="utf-8")
    print(f"\nGuardado en {out.relative_to(BASE_DIR)}")


if __name__ == "__main__":
    main()
