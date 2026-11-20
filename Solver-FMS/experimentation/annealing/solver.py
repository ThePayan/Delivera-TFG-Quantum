"""
Resuelve una instancia completa: trocea, manda cada subproblema al annealer y valida.

La respuesta tiene la misma forma de rutas que la de la pasarela (vehicleId, depotId,
stops, totalLoad), asi que `Instance.cost` y `Instance.violations` la juzgan con las
mismas reglas que a cualquier otro solver en compare_solvers.py.
"""

import time

from .decomposition import classical_fallback, decompose
from .qubo import QuboResult, make_client, solve_subproblem

# Si el annealer no encuentra solucion con el minimo de vehiculos, se reintenta con
# uno mas: el minimo por carga total puede no admitir un reparto entero.
EXTRA_VEHICLE_ATTEMPTS = 2


def solve_subproblems(subproblems, client, depot_ids, dry_run=False, budget_s=None):
    """
    Manda cada subproblema al annealer (con reintentos de un vehiculo mas) y junta sus
    rutas por deposito. Devuelve (rutas_por_deposito, informe_por_subproblema). Lo
    comparten la API de prueba y el motor de la pasarela.

    Con `budget_s`, pasado ese tiempo no se hacen mas llamadas: los grupos que quedan van
    al respaldo clasico. Es lo que garantiza que la pasarela reciba respuesta antes de
    cortar la conexion.
    """
    started = time.perf_counter()
    routes_by_depot = {depot_id: [] for depot_id in depot_ids}
    report = []
    for index, sub in enumerate(subproblems):
        entry = {
            "index": index,
            "depotId": sub.depot_id,
            "customers": len(sub.customers),
            "demand": sum(sub.demands()),
            "vehicles": sub.vehicles,
            "maxStops": sub.max_stops(),
            "variables": sub.num_variables(),
        }
        report.append(entry)
        if dry_run:
            continue

        attempts, result = [], QuboResult(reason="presupuesto de tiempo agotado")
        for extra in range(EXTRA_VEHICLE_ATTEMPTS + 1):
            if budget_s is not None and time.perf_counter() - started > budget_s:
                result = QuboResult(reason="presupuesto de tiempo agotado")
                break
            sub.vehicles = entry["vehicles"] + extra
            result = solve_subproblem(sub, client)
            attempts.append({
                "vehicles": sub.vehicles,
                "variables": sub.num_variables(),
                "feasible": result.feasible,
                "solverSeconds": result.solver_seconds,
                "wallSeconds": round(result.wall_seconds, 3),
                **({"reason": result.reason} if result.reason else {}),
            })
            # Un vehiculo mas no arregla un fallo de red: no se gastan mas llamadas.
            if result.feasible or result.reason.startswith("error de red"):
                break
        entry["attempts"] = attempts
        if result.feasible:
            entry["method"] = "annealer"
            routes_by_depot[sub.depot_id].extend(result.routes)
        else:
            entry["method"] = "fallback"
            routes_by_depot[sub.depot_id].extend(classical_fallback(sub))
    return routes_by_depot, report


def solve_instance(instance, best_known=None, *, client_kind="ae", time_limit_ms=1000,
                   max_customers=8, dry_run=False):
    started = time.perf_counter()
    subproblems = decompose(instance, max_customers)
    client = None if dry_run else make_client(client_kind, time_limit_ms)
    routes_by_depot, report = solve_subproblems(subproblems, client, instance.depots, dry_run)

    response = {
        "instance": instance.name,
        "solver": "DRY_RUN" if dry_run else f"AMPLIFY_{client_kind.upper()}",
        "parameters": {"client": client_kind, "timeLimitMs": time_limit_ms,
                       "maxCustomersPerSubproblem": max_customers, "dryRun": dry_run},
        "problem": instance.describe(),
        "bestKnown": best_known,
        "subproblems": report,
        "summary": {
            "subproblems": len(subproblems),
            "maxVariables": max((e["variables"] for e in report), default=0),
        },
    }
    if dry_run:
        response["summary"]["wallSeconds"] = round(time.perf_counter() - started, 3)
        return response

    routes = to_routes(instance, routes_by_depot)
    cost = instance.cost({"routes": routes})
    violations = instance.violations({"routes": routes})
    solver_seconds = [a["solverSeconds"] for e in report for a in e["attempts"] if a["solverSeconds"]]
    response["routes"] = routes
    response["summary"].update({
        "totalDistance": round(cost, 2),
        "gapPercent": round(100 * (cost - best_known) / best_known, 2) if best_known else None,
        "feasible": not violations,
        "violations": violations,
        "solvedByAnnealer": sum(e["method"] == "annealer" for e in report),
        "solvedByFallback": sum(e["method"] == "fallback" for e in report),
        "annealerCalls": sum(len(e["attempts"]) for e in report),
        "solverSeconds": round(sum(solver_seconds), 3),
        "wallSeconds": round(time.perf_counter() - started, 3),
    })
    return response


def to_routes(instance, routes_by_depot):
    """
    Reparte las rutas de cada deposito entre sus vehiculos por turnos. Un vehiculo
    puede hacer mas de un viaje (es la regla de Instance.violations); lo que no puede
    es haber mas vehiculos que la flota.
    """
    routes = []
    for depot_id, depot_routes in routes_by_depot.items():
        for number, stops in enumerate(depot_routes):
            vehicle = f"{depot_id}-{number % instance.fleet + 1}"
            routes.append({
                "vehicleId": vehicle,
                "depotId": depot_id,
                "stops": [int(s) if str(s).isdigit() else s for s in stops],
                "totalLoad": sum(instance.customers[str(s)]["demand"] for s in stops),
                "distance": round(instance._route_distance({"depotId": depot_id, "stops": stops}), 2),
            })
    return routes
