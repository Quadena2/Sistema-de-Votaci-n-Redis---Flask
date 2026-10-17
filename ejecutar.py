"""
Punto de entrada principal para ejecutar el servidor de desarrollo de Flask
y exponer la instancia `app` para comandos CLI (`flask --app ejecutar.py ...`).
"""

import os
from app import crear_aplicacion

app = crear_aplicacion()

if __name__ == "__main__":
    puerto = int(os.getenv("PUERTO", "5000"))
    host = os.getenv("HOST", "127.0.0.1")
    # threaded=True es necesario en desarrollo para atender simultáneamente
    # conexiones SSE abiertas y peticiones HTTP de registro de votos.
    app.run(host=host, port=puerto, debug=app.config.get("DEBUG", False), threaded=True)
