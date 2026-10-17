"""
Configuración compartida y fixtures de `pytest` para las pruebas unitarias
y de integración con Redis.

Garantiza aislamiento absoluto mediante un prefijo único por prueba
(`votacion_pytest_<uuid>`) y limpia al finalizar exclusivamente las claves
creadas bajo ese prefijo mediante `SCAN` + `DEL`, sin ejecutar jamás
`FLUSHDB` ni `FLUSHALL`.
"""

import os
from pathlib import Path
import re
import sys
import uuid
from typing import Any, Dict, Generator
import pytest
from flask import Flask
from flask.testing import FlaskClient

RAIZ_PROYECTO = Path(__file__).resolve().parent.parent
if str(RAIZ_PROYECTO) not in sys.path:
    sys.path.insert(0, str(RAIZ_PROYECTO))

from app import crear_aplicacion
from app.datos.cliente_redis import GestorRedis
from app.datos.repositorio_administradores import RepositorioAdministradores
from app.datos.repositorio_votaciones import RepositorioVotaciones
from app.negocio.servicio_autenticacion import ServicioAutenticacion
from app.negocio.servicio_votaciones import ServicioVotaciones


CODIGO_INVITACION_PRUEBA = "CODIGO-INVITACION-PRUEBAS-2026"
CLAVE_SECRETA_PRUEBA = "clave-secreta-exclusiva-para-suite-de-pruebas-2026"


def pytest_configure(config: Any) -> None:
    """Registra marcadores personalizados para separar pruebas unitarias e integración."""
    config.addinivalue_line(
        "markers", "unitaria: prueba unitaria de lógica de negocio o configuración sin Redis real."
    )
    config.addinivalue_line(
        "markers", "integracion_redis: prueba que interactúa con el servidor Redis real y scripts Lua."
    )


def limpiar_claves_prefijo(gestor: GestorRedis) -> None:
    """
    Elimina únicamente las claves pertenecientes al prefijo de prueba actual
    utilizando `scan_iter` y `delete`. Nunca utiliza `FLUSHDB` ni `FLUSHALL`.
    """
    cliente = gestor.obtener_cliente()
    patron = f"{gestor.prefijo}:*"
    claves = list(cliente.scan_iter(match=patron, count=100))
    if claves:
        cliente.delete(*claves)


@pytest.fixture
def prefijo_prueba() -> str:
    """Genera un prefijo único y aislado para cada función de prueba."""
    return f"votacion_pytest_{uuid.uuid4().hex[:10]}"


@pytest.fixture
def gestor_redis_prueba(prefijo_prueba: str) -> Generator[GestorRedis, None, None]:
    """
    Proporciona un `GestorRedis` conectado al contenedor Redis local con un
    prefijo temporal exclusivo y limpia sus claves al finalizar la prueba.
    """
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    gestor = GestorRedis(redis_url=redis_url, prefijo=prefijo_prueba)
    gestor.comprobar_conexion()
    yield gestor
    limpiar_claves_prefijo(gestor)


@pytest.fixture
def repo_administradores(gestor_redis_prueba: GestorRedis) -> RepositorioAdministradores:
    """Fixture de `RepositorioAdministradores` sobre el prefijo de prueba."""
    return RepositorioAdministradores(gestor_redis_prueba)


@pytest.fixture
def repo_votaciones(gestor_redis_prueba: GestorRedis) -> RepositorioVotaciones:
    """Fixture de `RepositorioVotaciones` sobre el prefijo de prueba."""
    return RepositorioVotaciones(gestor_redis_prueba)


@pytest.fixture
def servicio_autenticacion(
    repo_administradores: RepositorioAdministradores,
) -> ServicioAutenticacion:
    """Fixture de `ServicioAutenticacion` configurado para pruebas."""
    return ServicioAutenticacion(
        repositorio_administradores=repo_administradores,
        codigo_invitacion_esperado=CODIGO_INVITACION_PRUEBA,
        max_intentos_login=4,
        ventana_bloqueo_segundos=60,
    )


@pytest.fixture
def servicio_votaciones(repo_votaciones: RepositorioVotaciones) -> ServicioVotaciones:
    """Fixture de `ServicioVotaciones` configurado para pruebas."""
    return ServicioVotaciones(repositorio_votaciones=repo_votaciones)


@pytest.fixture
def app_prueba(
    prefijo_prueba: str,
    gestor_redis_prueba: GestorRedis,
) -> Flask:
    """Crea una instancia de la aplicación Flask conectada al prefijo de prueba."""
    config_test: Dict[str, Any] = {
        "ENTORNO": "pruebas",
        "TESTING": True,
        "SECRET_KEY": CLAVE_SECRETA_PRUEBA,
        "CODIGO_INVITACION_ADMIN": CODIGO_INVITACION_PRUEBA,
        "REDIS_URL": gestor_redis_prueba.redis_url,
        "PREFIJO_REDIS": prefijo_prueba,
        "MAX_INTENTOS_LOGIN": 4,
        "VENTANA_BLOQUEO_LOGIN_SEGUNDOS": 60,
    }
    return crear_aplicacion(
        config_personalizada=config_test,
        gestor_redis_personalizado=gestor_redis_prueba,
    )


@pytest.fixture
def cliente_http(app_prueba: Flask) -> FlaskClient:
    """Proporciona un cliente HTTP de prueba de Flask con soporte de cookies de sesión."""
    return app_prueba.test_client()


def extraer_csrf_html(html: str) -> str:
    """Extrae el valor del token CSRF desde un documento HTML renderizado."""
    coincidencia = re.search(r'name="csrf_token"\s+value="([^"]+)"', html)
    if coincidencia:
        return coincidencia.group(1)
    coincidencia_meta = re.search(r'name="csrf-token"\s+content="([^"]+)"', html)
    if coincidencia_meta:
        return coincidencia_meta.group(1)
    raise AssertionError("No se encontró el token CSRF en el HTML de respuesta.")
