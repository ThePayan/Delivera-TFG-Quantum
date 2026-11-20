"""
API HTTP de la prueba de annealers, para lanzarla desde Postman.

Arranque (desde Solver-FMS). El token va en experimentation/annealing/.env (copia de
.env.example, ignorado por Git) o en el entorno, nunca en la peticion:

    experimentation\\annealing\\.venv\\Scripts\\python.exe -m uvicorn experimentation.annealing.api:app --port 8096

Para que lo llame la pasarela (solver AMPLIFY) tiene que escuchar fuera de localhost:
anade --host 0.0.0.0 al arranque.

Endpoints:
  POST /api/v1/engine/solve         El contrato de la pasarela, como los demas motores.
  GET  /actuator/health             La sonda de salud de la pasarela.
  GET  /health                      Si el servicio esta vivo y que tokens ve.
  GET  /instances                   Las instancias Cordeau disponibles, con su BKS.
  POST /cordeau/{name}/solve        Resuelve una instancia Cordeau (p01, pr03...).
  POST /solve                       Resuelve una instancia propia, en el formato JSON
                                    de instances-MD-CVRP-JSON.

Cada resolucion real (no dryRun) se guarda tambien en results/, que Git ignora.
"""

import json
import os
from datetime import datetime
from typing import Literal

from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .. import BASE_DIR
from ..comparison import instance as cordeau
from . import engine
from .solver import solve_instance

# Los tokens se leen de experimentation/annealing/.env si existe. Una variable ya
# puesta en la terminal tiene preferencia sobre el fichero.
load_dotenv(Path(__file__).with_name(".env"))

RESULTS_DIR = BASE_DIR / "results"

app = FastAPI(title="Annealer CVRP (Amplify AE / SQBM+)", version="0.1.0")


class Options(BaseModel):
    client: Literal["ae", "sqbm", "hitachi"] = Field(
        "ae", description="ae = Fixstars Amplify AE, sqbm = Toshiba SQBM+, hitachi = Hitachi Annealing Cloud Web")
    timeLimitMs: int = Field(1000, ge=100, le=10000, description="Tiempo por llamada al annealer (el plan gratuito permite 10 s)")
    maxCustomersPerSubproblem: int = Field(8, ge=1, le=30, description="Clientes por subproblema QUBO")
    dryRun: bool = Field(False, description="Solo trocea y cuenta variables, sin llamar al annealer")


class CustomRequest(BaseModel):
    name: str = "custom"
    instance: dict = Field(..., description="Mismo formato que instances-MD-CVRP-JSON/pXX.json")
    options: Options = Options()


@app.get("/actuator/health")
def actuator_health():
    """La sonda de la pasarela: cualquier 200 cuenta como UP."""
    return {"status": "UP"}


@app.post("/api/v1/engine/solve")
def engine_solve(request: dict):
    """El contrato de la pasarela, el mismo que el de los demas motores (solver AMPLIFY)."""
    try:
        return engine.solve_request(request)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    except RuntimeError as error:  # falta el token o el servicio lo rechaza
        raise HTTPException(503, str(error)) from error


@app.get("/health")
def health():
    return {
        "status": "UP",
        "amplifyTokenConfigured": bool(os.environ.get("AMPLIFY_TOKEN")),
        "sqbmConfigured": bool(os.environ.get("SQBM_TOKEN") and os.environ.get("SQBM_URL")),
        "hitachiTokenConfigured": bool(os.environ.get("HITACHI_TOKEN")),
    }


@app.get("/instances")
def instances():
    best = cordeau.load_best_known()
    out = []
    for name in cordeau.all_names():
        inst = cordeau.Instance.load(name)
        out.append({"name": name, "customers": len(inst.customers), "depots": len(inst.depots),
                    "vehiclesPerDepot": inst.fleet, "capacity": inst.capacity(),
                    "maxDuration": inst.max_duration() or None, "bestKnown": best.get(name)})
    return out


@app.post("/cordeau/{name}/solve")
def solve_cordeau(name: str, options: Options | None = None):
    options = options or Options()
    if not cordeau.exists(name):
        raise HTTPException(404, f"No existe la instancia {name}. Mira GET /instances")
    inst = cordeau.Instance.load(name)
    return _run(inst, cordeau.load_best_known().get(name), options)


@app.post("/solve")
def solve_custom(request: CustomRequest):
    try:
        inst = cordeau.Instance(request.name, _with_defaults(request.instance))
    except (KeyError, TypeError) as error:
        raise HTTPException(422, f"Instancia mal formada, falta o sobra el campo {error}") from error
    return _run(inst, None, request.options)


def _run(inst, best_known, options: Options):
    try:
        response = solve_instance(inst, best_known, client_kind=options.client,
                                  time_limit_ms=options.timeLimitMs,
                                  max_customers=options.maxCustomersPerSubproblem,
                                  dry_run=options.dryRun)
    except RuntimeError as error:  # falta el token
        raise HTTPException(400, str(error)) from error
    if not options.dryRun:
        RESULTS_DIR.mkdir(exist_ok=True)
        path = RESULTS_DIR / f"annealing-{inst.name}-{datetime.now():%Y-%m-%d-%H%M%S}.json"
        path.write_text(json.dumps(response, indent=2, ensure_ascii=False), encoding="utf-8")
        response["savedTo"] = str(path.relative_to(BASE_DIR))
    return response


def _with_defaults(data):
    """Rellena los campos de Cordeau que una instancia escrita a mano no necesita."""
    data = dict(data)
    data["depots"] = [{"max_duration": 0, **d} for d in data["depots"]]
    data["customers"] = [{"service_duration": 0, **c} for c in data["customers"]]
    return data
