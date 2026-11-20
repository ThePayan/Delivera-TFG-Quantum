"""
Un subproblema CVRP de un deposito formulado como QUBO y resuelto en un annealer.

Variables: x[t, i, k] = 1 si el vehiculo k visita el nodo i en el paso t. El nodo 0
es el deposito: un vehiculo que "visita" el deposito en un paso esta parado, y asi
cada vehiculo puede hacer rutas de cualquier longitud hasta `max_stops` paradas.

Restricciones:
  - En cada paso, cada vehiculo esta en exactamente un nodo.
  - Cada cliente se visita exactamente una vez, por un vehiculo y en un paso.
  - La carga de cada vehiculo no pasa de la capacidad.

Objetivo: la distancia de deposito al primer nodo, entre pasos consecutivos y del
ultimo nodo al deposito. Si el deposito aparece en mitad de una ruta, la distancia
de ese rodeo cuenta en el modelo pero no en la ruta decodificada, que solo guarda
los clientes; por la desigualdad triangular el annealer no tiene incentivo a hacerlo.
"""

import os
import threading
import time
from dataclasses import dataclass, field
from datetime import timedelta

import amplify
import numpy as np
from amplify import AmplifyAEClient, HitachiClient, ToshibaSQBM2Client, VariableGenerator, einsum, less_equal, one_hot, solve


@dataclass
class Subproblem:
    """Un deposito y un grupo de sus clientes, listo para formular."""

    depot_id: str
    depot: dict
    customer_ids: list
    customers: list
    capacity: int
    vehicles: int
    # Matriz de distancias completa de la pasarela; los nodos llevan su fila en "index".
    # Sin ella se usa la distancia euclidea entre coordenadas, como en Cordeau.
    matrix: object = None

    def demands(self):
        return [c["demand"] for c in self.customers]

    def max_stops(self):
        """Cuantos clientes caben como mucho en un vehiculo: los de menor demanda primero."""
        stops, load = 0, 0
        for demand in sorted(self.demands()):
            if load + demand > self.capacity:
                break
            load += demand
            stops += 1
        return max(1, stops)

    def num_variables(self):
        return self.max_stops() * (len(self.customers) + 1) * self.vehicles

    def distance_matrix(self):
        nodes = [self.depot, *self.customers]
        if self.matrix is not None:
            rows = [n["index"] for n in nodes]
            return np.asarray(self.matrix, dtype=float)[np.ix_(rows, rows)]
        coords = np.array([[n["x"], n["y"]] for n in nodes], dtype=float)
        diff = coords[:, None, :] - coords[None, :, :]
        return np.sqrt((diff**2).sum(axis=-1))


@dataclass
class QuboResult:
    routes: list = field(default_factory=list)   # listas de customer_ids, una por vehiculo usado
    feasible: bool = False
    solver_seconds: float | None = None
    wall_seconds: float = 0.0
    reason: str = ""


def build_model(sub: Subproblem):
    n, K, L = len(sub.customers), sub.vehicles, sub.max_stops()
    d = sub.distance_matrix()
    demand = np.array([0, *sub.demands()], dtype=int)

    x = VariableGenerator().array("Binary", shape=(L, n + 1, K))

    objective = einsum("i,ik->", d[0], x[0]) + einsum("i,ik->", d[:, 0], x[L - 1])
    if L > 1:
        objective += einsum("ij,tik,tjk->", d, x[:-1], x[1:])

    one_node_per_step = one_hot(x, axis=1)
    each_customer_once = amplify.sum([one_hot(x[:, i, :].sum()) for i in range(1, n + 1)])
    capacity = amplify.sum([less_equal((demand * x[:, :, k]).sum(), sub.capacity) for k in range(K)])

    # Amplify normaliza las penalizaciones; el peso las pone a la escala de las distancias.
    weight = float(d.max())
    model = objective + weight * (one_node_per_step + each_customer_once + capacity)
    return x, model


def decode(sub: Subproblem, values: np.ndarray):
    """Convierte x[t, i, k] en rutas de clientes. None si no es una asignacion valida."""
    L, N, K = values.shape
    if not np.all(values.sum(axis=1) == 1):
        return None
    routes = []
    for k in range(K):
        nodes = [int(np.argmax(values[t, :, k])) for t in range(L)]
        stops = [sub.customer_ids[i - 1] for i in nodes if i != 0]
        if stops:
            routes.append(stops)
    served = [c for route in routes for c in route]
    if sorted(served) != sorted(sub.customer_ids):
        return None
    by_id = dict(zip(sub.customer_ids, sub.customers))
    if any(sum(by_id[c]["demand"] for c in route) > sub.capacity for route in routes):
        return None
    return routes


def make_client(kind: str, time_limit_ms: int):
    """El token sale siempre de variables de entorno, nunca de la peticion."""
    if kind == "ae":
        token = os.environ.get("AMPLIFY_TOKEN")
        if not token:
            raise RuntimeError("Falta la variable de entorno AMPLIFY_TOKEN")
        client = AmplifyAEClient()
        client.token = token
        client.parameters.time_limit_ms = time_limit_ms
        return client
    if kind == "sqbm":
        # A traves de Fixstars Amplify basta el token; la URL solo hace falta en la
        # version de AWS Marketplace (documentacion de ToshibaSQBM2Client, Amplify v1).
        token, url = os.environ.get("SQBM_TOKEN"), os.environ.get("SQBM_URL")
        if not token:
            raise RuntimeError("Falta la variable de entorno SQBM_TOKEN")
        client = ToshibaSQBM2Client()
        client.token = token
        if url:
            client.url = url
        # Con el modo "Qubo" (por defecto) Amplify convierte la desigualdad de capacidad
        # en penalizaciones con variables auxiliares, y SQBM+ no encontro ninguna solucion
        # factible ni con pesos 20 veces mayores (18/11/2026). En "Qplib" las restricciones
        # viajan como tales y SQBM+ las maneja: con eso si devuelve rutas factibles.
        client.solver = "Qplib"
        client.parameters.timeout = timedelta(milliseconds=time_limit_ms)
        return client
    if kind == "hitachi":
        token = os.environ.get("HITACHI_TOKEN")
        if not token:
            raise RuntimeError("Falta la variable de entorno HITACHI_TOKEN")
        # Annealing Cloud Web: la maquina trabaja sobre un grafo King y Amplify
        # incrusta en el la QUBO antes de enviarla. Se usa el tipo por defecto (GPU).
        client = HitachiClient()
        client.token = token
        return client
    raise ValueError(f"Cliente desconocido: {kind}")


NETWORK_RETRIES = 3
NETWORK_ERRORS = ("SSL connection failed", "Connection timeout", "timed out", "Connection refused")
# Una llamada que no vuelve nunca: visto el 18/11/2026, una conexion se quedo colgada
# mas de ocho minutos sin error. Pasado este plazo el grupo se da por perdido.
CALL_TIMEOUT_S = 45


class NetworkError(RuntimeError):
    pass


def _solve_with_timeout(model, client):
    """solve() en un hilo aparte con plazo maximo. El hilo colgado se abandona (daemon)."""
    box = {}

    def run():
        try:
            box["result"] = solve(model, client)
        except BaseException as error:  # se relanza en el hilo principal
            box["error"] = error

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(CALL_TIMEOUT_S)
    if thread.is_alive():
        raise NetworkError(f"sin respuesta del annealer en {CALL_TIMEOUT_S} s")
    if "error" in box:
        raise box["error"]
    return box["result"]


def solve_with_retries(model, client):
    """
    La conexion con los servicios falla a veces de forma intermitente (visto el
    18/11/2026: fallos SSL y timeouts sueltos; el intento siguiente sale bien). Solo se
    reintentan esos errores; cualquier otro, como un 401 por token invalido, sube tal cual.
    """
    for attempt in range(1, NETWORK_RETRIES + 1):
        try:
            return _solve_with_timeout(model, client)
        except RuntimeError as error:
            if not any(marker in str(error) for marker in NETWORK_ERRORS):
                raise
            if attempt == NETWORK_RETRIES:
                raise NetworkError(str(error)) from error
            time.sleep(2 * attempt)


def solve_subproblem(sub: Subproblem, client) -> QuboResult:
    x, model = build_model(sub)
    start = time.perf_counter()
    try:
        result = solve_with_retries(model, client)
    except NetworkError as error:
        # Un subproblema sin conexion no tumba la instancia: se marca y sigue el resto.
        return QuboResult(wall_seconds=time.perf_counter() - start, reason=f"error de red: {error}")
    out = QuboResult(wall_seconds=time.perf_counter() - start)
    exec_time = getattr(result, "execution_time", None)
    out.solver_seconds = exec_time.total_seconds() if exec_time is not None else None

    if len(result) == 0:
        out.reason = "el annealer no devolvio ninguna solucion que cumpla las restricciones"
        return out
    routes = decode(sub, x.evaluate(result.best.values))
    if routes is None:
        out.reason = "la mejor solucion no se pudo decodificar como rutas validas"
        return out
    out.routes, out.feasible = routes, True
    return out
