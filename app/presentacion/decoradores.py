"""
Decoradores y utilidades de seguridad para la capa de presentación.

Gestiona:
- Identificador de sesión firmada para votantes (`id_sesion_votante`).
- Generación y validación en tiempo constante de tokens CSRF.
- Gestión de la sesión autenticada de administradores.
- Decorador `@requiere_administrador` para proteger el panel administrativo.
"""

from functools import wraps
import hmac
import secrets
import uuid
from typing import Any, Callable, Dict, Optional
from flask import (
     current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from app.negocio.excepciones import ErrorAlmacenamientoNoDisponible


CLAVE_SESION_VOTANTE = "id_sesion_votante"
CLAVE_SESION_ADMIN_ID = "admin_id"
CLAVE_SESION_ADMIN_USUARIO = "admin_usuario"
CLAVE_SESION_CSRF = "_csrf_token"


def es_solicitud_json() -> bool:
    """Determina si la solicitud actual espera una respuesta en formato JSON."""
    if request.path.startswith("/api/"):
        return True
    if request.is_json:
        return True
    aceptar = request.headers.get("Accept", "")
    return "application/json" in aceptar and "text/html" not in aceptar


def obtener_o_crear_id_sesion_votante() -> str:
    """
    Obtiene el identificador único de sesión de votación desde la cookie firmada
    de Flask o genera uno nuevo en el servidor mediante UUID4 si aún no existe.
    Nunca acepta identificadores enviados por el cliente en el cuerpo o query.
    """
    id_existente = session.get(CLAVE_SESION_VOTANTE)
    if not id_existente or not isinstance(id_existente, str):
        id_existente = str(uuid.uuid4())
        session[CLAVE_SESION_VOTANTE] = id_existente
    return id_existente


def obtener_token_csrf() -> str:
    """Devuelve el token CSRF asociado a la sesión actual o genera uno nuevo."""
    token = session.get(CLAVE_SESION_CSRF)
    if not token or not isinstance(token, str):
        token = secrets.token_hex(32)
        session[CLAVE_SESION_CSRF] = token
    return token


def establecer_sesion_administrador(datos_admin: Dict[str, str]) -> None:
    """
    Limpia cualquier estado previo de la sesión antes de registrar la sesión
    autenticada del administrador, conservando únicamente el `id_sesion_votante`
    previo (si ya existía en el navegador) para evitar que una sesión que votó
    de forma anónima vuelva a votar tras iniciar sesión.
    Además, rota el token CSRF.
    """
    id_votante_previo = session.get(CLAVE_SESION_VOTANTE)
    session.clear()
    session[CLAVE_SESION_VOTANTE] = id_votante_previo or str(uuid.uuid4())
    session[CLAVE_SESION_ADMIN_ID] = str(datos_admin["id"])
    session[CLAVE_SESION_ADMIN_USUARIO] = str(datos_admin["nombre_usuario"])
    session[CLAVE_SESION_CSRF] = secrets.token_hex(32)


def cerrar_sesion_administrador() -> None:
    """
    Elimina las credenciales administrativas de la sesión y rota el token CSRF,
    conservando el identificador de sesión de votante del navegador.
    """
    id_votante_previo = session.get(CLAVE_SESION_VOTANTE)
    session.clear()
    if id_votante_previo:
        session[CLAVE_SESION_VOTANTE] = id_votante_previo
    session[CLAVE_SESION_CSRF] = secrets.token_hex(32)


def obtener_admin_actual() -> Optional[Dict[str, str]]:
    """
    Devuelve los datos básicos del administrador autenticado en la sesión actual,
    verificando su existencia en Redis a través del servicio de autenticación.
    """
    admin_id = session.get(CLAVE_SESION_ADMIN_ID)
    if not admin_id:
        return None

    servicio_auth = current_app.extensions.get("servicio_autenticacion")
    if not servicio_auth:
        return None

    try:
        datos = servicio_auth.obtener_administrador_por_id(str(admin_id))
    except ErrorAlmacenamientoNoDisponible:
        # Si Redis falla temporalmente, devolvemos los datos firmados de sesión
        return {
            "id": str(admin_id),
            "nombre_usuario": str(session.get(CLAVE_SESION_ADMIN_USUARIO, "Administrador")),
        }

    if not datos:
        session.pop(CLAVE_SESION_ADMIN_ID, None)
        session.pop(CLAVE_SESION_ADMIN_USUARIO, None)
        return None
    return datos


def extraer_token_csrf_solicitud() -> str:
    """Extrae el token CSRF desde formulario HTML, cabeceras HTTP o cuerpo JSON."""
    token_formulario = request.form.get("csrf_token", "")
    if token_formulario:
        return str(token_formulario)

    token_cabecera = (
        request.headers.get("X-CSRFToken")
        or request.headers.get("X-CSRF-Token")
        or ""
    )
    if token_cabecera:
        return str(token_cabecera)

    if request.is_json:
        cuerpo = request.get_json(silent=True)
        if isinstance(cuerpo, dict) and cuerpo.get("csrf_token"):
            return str(cuerpo.get("csrf_token"))

    return ""


def validar_csrf_actual() -> bool:
    """Compara en tiempo constante el token CSRF de la sesión con el enviado en la petición."""
    token_sesion = session.get(CLAVE_SESION_CSRF, "")
    token_recibido = extraer_token_csrf_solicitud()
    if not token_sesion or not token_recibido:
        return False
    return hmac.compare_digest(str(token_sesion), str(token_recibido))


def proteger_csrf(funcion: Callable[..., Any]) -> Callable[..., Any]:
    """
    Decorador que exige un token CSRF válido en solicitudes que modifican datos
    (`POST`, `PUT`, `PATCH`, `DELETE`).
    """

    @wraps(funcion)
    def envoltura(*args: Any, **kwargs: Any) -> Any:
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            if not validar_csrf_actual():
                mensaje = (
                    "La solicitud fue rechazada porque el token de seguridad CSRF "
                    "es inválido o está ausente. Recargue la página e intente de nuevo."
                )
                if es_solicitud_json():
                    return (
                        jsonify(
                            {
                                "exito": False,
                                "error": mensaje,
                                "codigo": "CSRF_INVALIDO",
                            }
                        ),
                        400,
                    )
                flash(mensaje, "error")
                return (
                    render_template(
                        "inicio.html",
                        votaciones_abiertas=[],
                        votaciones_cerradas=[],
                        error_csrf=mensaje,
                    ),
                    400,
                )
        return funcion(*args, **kwargs)

    return envoltura


def requiere_administrador(funcion: Callable[..., Any]) -> Callable[..., Any]:
    """
    Decorador de autorización que restringe el acceso únicamente a administradores
    autenticados con cuenta válida en Redis.
    """

    @wraps(funcion)
    def envoltura(*args: Any, **kwargs: Any) -> Any:
        admin = obtener_admin_actual()
        if not admin:
            mensaje = "Debe iniciar sesión como administrador para acceder a esta sección."
            if es_solicitud_json():
                return (
                    jsonify(
                        {
                            "exito": False,
                            "error": mensaje,
                            "codigo": "NO_AUTORIZADO",
                        }
                    ),
                    401,
                )
            flash(mensaje, "advertencia")
            return redirect(url_for("autenticacion.ingresar"))
        return funcion(*args, **kwargs)

    return envoltura
