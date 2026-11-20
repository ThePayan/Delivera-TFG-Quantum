"""
El annealer como motor de la pasarela: habla el contrato de Delivera.

La pasarela llama a todos los motores igual: POST /api/v1/engine/solve con depositos,
clientes, vehiculos, matriz de distancias y parametros, y espera rutas con su
distancia y su carga. Asi el annealer se lanza como cualquier otro solver:

    GET/POST /api/v1/fms/instances/send?fileName=p01&solverType=AMPLIFY

Dos solvers de la pasarela apuntan a este mismo motor y cambian solo el annealer: el
troceo clasico (deposito mas cercano y barrido) y la QUBO son los mismos.

    AMPLIFY  Fixstars Amplify AE
    SQBM     Toshiba SQBM+ (via Fixstars Amplify, modo Qplib)

Diferencias con la API de prueba (/cordeau/...): las distancias salen de la matriz que
manda la pasarela, no de las coordenadas, y la flota y la capacidad salen de la lista
de vehiculos. Como en la API de prueba, la duracion maxima no se modela.
"""

import time

from .decomposition import decompose
from .qubo import make_client
from .solver import solve_subproblems

# solverType de la pasarela -> cliente del Amplify SDK
CLIENTS = {"AMPLIFY": "ae", "SQBM": "sqbm"}
DEFAULT_TIME_LIMIT_MS = 3000
DEFAULT_MAX_CUSTOMERS = 20
# La pasarela abandona un motor a los 300 s. Pasados 240 s no se empiezan mas llamadas
# (y cada una tiene su propio plazo en qubo.CALL_TIMEOUT_S), asi que siempre responde.
BUDGET_S = 240


class DeliveraInstance:
    """Lo que necesita `decompose` (depots, customers), construido desde la peticion."""

    def __init__(self, request):
        self.matrix = request["distanceMatrix"]
        vehicles = request.get("vehicles") or []
        if not vehicles:
            raise ValueError("La peticion no trae vehiculos: el annealer necesita la flota de cada deposito")

        self.vehicles_by_depot = {}
        for vehicle in vehicles:
            self.vehicles_by_depot.setdefault(str(vehicle["startDepotId"]), []).append(vehicle)

        # Solo cuentan los depositos con algun vehiculo: un cliente asignado a un
        # deposito sin flota no podria servirse. Si la flota de un deposito no es
        # homogenea se toma la capacidad menor, que es la que siempre se puede cumplir.
        self.depots = {}
        for depot in request["depots"]:
            fleet = self.vehicles_by_depot.get(str(depot["id"]))
            if fleet:
                self.depots[str(depot["id"])] = {
                    "x": depot["lng"], "y": depot["lat"], "index": depot["matrixIndex"],
                    "capacity": min(v["capacity"] for v in fleet),
                }
        self.customers = {
            str(c["id"]): {"x": c["lng"], "y": c["lat"], "index": c["matrixIndex"], "demand": c["demand"]}
            for c in request["customers"]
        }

    def route_distance(self, depot_id, stops):
        node = self.depots[depot_id]["index"]
        total = 0.0
        for stop in stops:
            nxt = self.customers[stop]["index"]
            total += self.matrix[node][nxt]
            node = nxt
        return total + self.matrix[node][self.depots[depot_id]["index"]]


def solve_request(request):
    started = time.perf_counter()
    params = request.get("parameters") or {}
    time_limit_ms = int(params.get("timeLimitMs") or DEFAULT_TIME_LIMIT_MS)
    max_customers = int(params.get("maxCustomersPerSubproblem") or DEFAULT_MAX_CUSTOMERS)

    solver_name = str(request.get("solverType") or "AMPLIFY").upper()
    if solver_name not in CLIENTS:
        raise ValueError(f"Este motor solo atiende {sorted(CLIENTS)}, no {solver_name}")

    inst = DeliveraInstance(request)
    subproblems = decompose(inst, max_customers, matrix=inst.matrix)
    client = make_client(CLIENTS[solver_name], time_limit_ms)
    routes_by_depot, report = solve_subproblems(subproblems, client, inst.depots, budget_s=BUDGET_S)

    # Las rutas de cada deposito se reparten por turnos entre sus vehiculos: un
    # vehiculo puede hacer mas de un viaje, que es lo que admite la validacion.
    routes = []
    for depot_id, depot_routes in routes_by_depot.items():
        fleet = inst.vehicles_by_depot[depot_id]
        for number, stops in enumerate(depot_routes):
            routes.append({
                "vehicleId": str(fleet[number % len(fleet)]["id"]),
                "depotId": depot_id,
                "stops": [str(s) for s in stops],
                "totalDistance": inst.route_distance(depot_id, stops),
                "totalLoad": sum(inst.customers[s]["demand"] for s in stops),
            })

    total = sum(r["totalDistance"] for r in routes)
    by_annealer = sum(e["method"] == "annealer" for e in report)
    # La respuesta de la pasarela no tiene donde decirlo, asi que queda en la consola
    # del motor: cuantos grupos resolvio el annealer y cuantos el barrido de respaldo.
    print(f"[{solver_name}] {request.get('problemId')}: coste {total:.2f}, "
          f"{by_annealer}/{len(report)} grupos por el annealer, "
          f"{len(report) - by_annealer} por el respaldo clasico", flush=True)
    return {
        "problemId": request.get("problemId"),
        "status": "COMPLETED",
        "solverUsed": solver_name,
        "totalCost": total,
        "computationTimeMs": round((time.perf_counter() - started) * 1000),
        "seed": None,
        "routes": routes,
        "trace": None,
        # La pasarela ignora este campo; queda para quien llame al motor directamente.
        "annealer": {
            "timeLimitMs": time_limit_ms,
            "maxCustomersPerSubproblem": max_customers,
            "subproblems": len(report),
            "solvedByAnnealer": by_annealer,
            "solvedByFallback": len(report) - by_annealer,
            "maxVariables": max((e["variables"] for e in report), default=0),
            "detail": report,
        },
    }
