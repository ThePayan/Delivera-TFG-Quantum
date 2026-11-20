"""
Trocea un MD-CVRP en subproblemas CVRP lo bastante pequeños para el annealer.

Dos pasos clasicos y deliberadamente simples:
  1. Cada cliente va al deposito mas cercano.
  2. Dentro de cada deposito, los clientes se ordenan por angulo alrededor del
     deposito (barrido) y se cortan en grupos de como mucho `max_customers`.

Asi cada subproblema tiene clientes vecinos y un tamano acotado, que es lo que decide
cuantas variables binarias necesita. La asignacion al deposito mas cercano no mira la
flota: en algunas instancias (p07, p11) deja un deposito con mas carga de la que
puede llevar su flota, y la validacion final lo dira.
"""

import math

from ..comparison.instance import distance
from .qubo import Subproblem


def nearest_depot(instance, matrix=None):
    """
    Con `matrix` (la matriz de distancias de la pasarela, indexada por el campo `index`
    de cada nodo) se mide con ella; sin ella, distancia euclidea entre coordenadas.
    """
    def dist(customer, depot):
        if matrix is not None:
            return matrix[depot["index"]][customer["index"]]
        return distance(customer, depot)

    groups = {depot_id: [] for depot_id in instance.depots}
    for customer_id, customer in instance.customers.items():
        depot_id = min(instance.depots, key=lambda d: dist(customer, instance.depots[d]))
        groups[depot_id].append(customer_id)
    return groups


def sweep(depot, customer_ids, customers):
    return sorted(customer_ids, key=lambda c: math.atan2(customers[c]["y"] - depot["y"],
                                                         customers[c]["x"] - depot["x"]))


def decompose(instance, max_customers, matrix=None):
    subproblems = []
    for depot_id, ids in nearest_depot(instance, matrix).items():
        depot = instance.depots[depot_id]
        ordered = sweep(depot, ids, instance.customers)
        for start in range(0, len(ordered), max_customers):
            chunk = ordered[start:start + max_customers]
            customers = [instance.customers[c] for c in chunk]
            demand = sum(c["demand"] for c in customers)
            subproblems.append(Subproblem(
                depot_id=depot_id,
                depot=depot,
                customer_ids=chunk,
                customers=customers,
                capacity=depot["capacity"],
                vehicles=max(1, math.ceil(demand / depot["capacity"])),
                matrix=matrix,
            ))
    return subproblems


def classical_fallback(sub):
    """
    Rutas sin annealer para un subproblema que el annealer no resolvio: se recorre el
    barrido abriendo un vehiculo nuevo cuando el siguiente cliente ya no cabe. Es lo
    minimo para que la solucion quede completa; la respuesta marca que grupo lo uso.
    """
    routes, current, load = [], [], 0
    for customer_id, customer in zip(sub.customer_ids, sub.customers):
        if current and load + customer["demand"] > sub.capacity:
            routes.append(current)
            current, load = [], 0
        current.append(customer_id)
        load += customer["demand"]
    if current:
        routes.append(current)
    return routes
