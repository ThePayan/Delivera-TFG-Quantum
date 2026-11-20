"""
Prueba de annealers (Fixstars Amplify AE, Toshiba SQBM+) sobre las instancias Cordeau.

Es una prueba ligera que se arranca a mano fuera de Docker. Ademas de su API propia
habla el contrato de la pasarela (engine.py), que la lista como el solver AMPLIFY.
Sirve para comprobar que la codificacion QUBO del CVRP funciona y para ver que
calidad dan los annealers frente a los BKS.

  decomposition.py  Trocea el MD-CVRP en subproblemas CVRP pequenos: cada cliente al
                    deposito mas cercano y, dentro de cada deposito, grupos por barrido
                    angular. Es la "descomposicion clasica con subproblemas QUBO
                    pequenos" que se propuso en el TFG.
  qubo.py           Formula un subproblema como QUBO y lo resuelve en el annealer.
  solver.py         Junta las dos cosas y valida la solucion con las mismas reglas que
                    compare_solvers.py.
  engine.py         El adaptador al contrato de la pasarela (solver AMPLIFY).
  api.py            La API HTTP, para lanzarlo desde Postman o desde la pasarela.
"""
