"""
Rutas públicas de consulta, participación en votaciones, API JSON y
transmisión en tiempo real mediante Server-Sent Events (SSE).

Rutas definidas:
- `GET  /`
- `GET  /votaciones`
- `GET  /votaciones/<id_votacion>`
- `GET  /votaciones/<id_votacion>/resultados`
- `GET  /api/votaciones/<id_votacion>`
- `POST /api/votaciones/<id_votacion>/votos`
- `GET  /api/votaciones/<id_votacion>/eventos`
"""

import json
from typing import Any, Iterator
from flask import (
    Blueprint,
    Response,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    stream_with_context,
    url_for,
)
from app.negocio.excepciones import (
    ErrorAlmacenamientoNoDisponible,
    ErrorRecursoNoEncontrado,
    ErrorValidacion,
    ErrorVotoRechazado,
)
from app.negocio.servicio_votaciones import ServicioVotaciones
from app.presentacion.decoradores import (
    es_solicitud_json,
    obtener_admin_actual,
    obtener_o_crear_id_sesion_votante,
    proteger_csrf,
)

bp_publicas = Blueprint("publicas", __name__)


def _obtener_servicio_votaciones() -> ServicioVotaciones:
    """Obtiene la instancia configurada de `ServicioVotaciones`."""
    return current_app.extensions["servicio_votaciones"]


@bp_publicas.route("/", methods=["GET"])
def inicio() -> Any:
    """Página principal con resumen didáctico del sistema y votaciones disponibles."""
    id_sesion = obtener_o_crear_id_sesion_votante()
    admin = obtener_admin_actual()
    servicio = _obtener_servicio_votaciones()

    try:
        listado = servicio.listar_votaciones_publicas(
            id_sesion_votante=id_sesion,
            id_administrador=admin["id"] if admin else None,
        )
        return render_template(
            "inicio.html",
            votaciones_abiertas=listado["abiertas"],
            votaciones_cerradas=listado["cerradas"],
            vista_listado=False,
        )
    except ErrorAlmacenamientoNoDisponible as exc:
        flash(exc.mensaje, "error")
        return (
            render_template(
                "inicio.html",
                votaciones_abiertas=[],
                votaciones_cerradas=[],
                vista_listado=False,
            ),
            503,
        )


@bp_publicas.route("/votaciones", methods=["GET"])
def listar_votaciones() -> Any:
    """Listado público de todas las votaciones abiertas y cerradas."""
    id_sesion = obtener_o_crear_id_sesion_votante()
    admin = obtener_admin_actual()
    servicio = _obtener_servicio_votaciones()

    try:
        listado = servicio.listar_votaciones_publicas(
            id_sesion_votante=id_sesion,
            id_administrador=admin["id"] if admin else None,
        )
        return render_template(
            "inicio.html",
            votaciones_abiertas=listado["abiertas"],
            votaciones_cerradas=listado["cerradas"],
            vista_listado=True,
        )
    except ErrorAlmacenamientoNoDisponible as exc:
        flash(exc.mensaje, "error")
        return (
            render_template(
                "inicio.html",
                votaciones_abiertas=[],
                votaciones_cerradas=[],
                vista_listado=True,
            ),
            503,
        )


@bp_publicas.route("/votaciones/<string:id_votacion>", methods=["GET"])
def detalle_votacion(id_votacion: str) -> Any:
    """
    Página pública de detalle y participación en una votación.
    Tanto los visitantes anónimos como los administradores autenticados votan
    desde esta misma interfaz compartida.
    """
    id_sesion = obtener_o_crear_id_sesion_votante()
    admin = obtener_admin_actual()
    servicio = _obtener_servicio_votaciones()

    try:
        votacion = servicio.obtener_detalle_votacion(
            id_votacion=id_votacion,
            id_sesion_votante=id_sesion,
            id_administrador=admin["id"] if admin else None,
        )
        return render_template(
            "detalle_votacion.html",
            votacion=votacion,
        )
    except ErrorRecursoNoEncontrado as exc:
        flash(exc.mensaje, "error")
        return redirect(url_for("publicas.listar_votaciones"))
    except ErrorAlmacenamientoNoDisponible as exc:
        flash(exc.mensaje, "error")
        return redirect(url_for("publicas.inicio"))


@bp_publicas.route("/votaciones/<string:id_votacion>/resultados", methods=["GET"])
def resultados_votacion(id_votacion: str) -> Any:
    """Página dedicada a la visualización en tiempo real de resultados y claves Redis."""
    id_sesion = obtener_o_crear_id_sesion_votante()
    admin = obtener_admin_actual()
    servicio = _obtener_servicio_votaciones()

    try:
        votacion = servicio.obtener_detalle_votacion(
            id_votacion=id_votacion,
            id_sesion_votante=id_sesion,
            id_administrador=admin["id"] if admin else None,
        )
        return render_template(
            "resultados.html",
            votacion=votacion,
        )
    except ErrorRecursoNoEncontrado as exc:
        flash(exc.mensaje, "error")
        return redirect(url_for("publicas.listar_votaciones"))
    except ErrorAlmacenamientoNoDisponible as exc:
        flash(exc.mensaje, "error")
        return redirect(url_for("publicas.inicio"))


# -----------------------------------------------------------------------------
# Endpoints de API JSON y Server-Sent Events (SSE)
# -----------------------------------------------------------------------------


@bp_publicas.route("/api/votaciones", methods=["GET"])
def api_listar_votaciones() -> Any:
    """
    Devuelve el listado público actual de votaciones abiertas y cerradas en JSON.
    Utilizado para sincronizar en tiempo real las páginas `/` y `/votaciones`.
    """
    id_sesion = obtener_o_crear_id_sesion_votante()
    admin = obtener_admin_actual()
    servicio = _obtener_servicio_votaciones()

    try:
        listado = servicio.listar_votaciones_publicas(
            id_sesion_votante=id_sesion,
            id_administrador=admin["id"] if admin else None,
        )
        return (
            jsonify(
                {
                    "exito": True,
                    "abiertas": listado["abiertas"],
                    "cerradas": listado["cerradas"],
                }
            ),
            200,
        )
    except ErrorAlmacenamientoNoDisponible as exc:
        return (
            jsonify({"exito": False, "error": exc.mensaje, "codigo": exc.codigo}),
            503,
        )


@bp_publicas.route("/api/votaciones/eventos", methods=["GET"])
def api_eventos_listado_votaciones() -> Any:
    """
    Endpoint Server-Sent Events (SSE) que suscribe las páginas de listado público
    (`/` y `/votaciones`) al canal global de votaciones en Redis Pub/Sub.
    """
    id_sesion = obtener_o_crear_id_sesion_votante()
    admin = obtener_admin_actual()
    id_admin = admin["id"] if admin else None
    servicio = _obtener_servicio_votaciones()

    def generar_flujo_listado_sse() -> Iterator[str]:
        try:
            for item in servicio.escuchar_actualizaciones_listado_publico(
                id_sesion_votante=id_sesion,
                id_administrador=id_admin,
            ):
                tipo_evento = item.get("evento", "actualizacion_listado")
                if tipo_evento == "latido":
                    yield ": keep-alive\n\n"
                    yield f"event: latido\ndata: {json.dumps({'tipo': 'latido'})}\n\n"
                else:
                    carga = json.dumps(
                        {
                            "evento": tipo_evento,
                            "listado": item.get("listado"),
                        }
                    )
                    yield f"event: actualizacion_listado\ndata: {carga}\n\n"
        except GeneratorExit:
            return
        except ErrorAlmacenamientoNoDisponible as exc:
            carga_error = json.dumps({"error": exc.mensaje, "codigo": exc.codigo})
            yield f"event: error_servidor\ndata: {carga_error}\n\n"

    respuesta = Response(
        stream_with_context(generar_flujo_listado_sse()),
        mimetype="text/event-stream",
    )
    respuesta.headers["Cache-Control"] = "no-cache"
    respuesta.headers["X-Accel-Buffering"] = "no"
    respuesta.headers["Connection"] = "keep-alive"
    return respuesta


@bp_publicas.route("/api/votaciones/<string:id_votacion>", methods=["GET"])
def api_obtener_votacion(id_votacion: str) -> Any:
    """
    Devuelve el estado completo y los resultados actualizados de una votación.
    Utilizado por el navegador al iniciar o recuperar una conexión SSE.
    """
    id_sesion = obtener_o_crear_id_sesion_votante()
    admin = obtener_admin_actual()
    servicio = _obtener_servicio_votaciones()

    try:
        votacion = servicio.obtener_detalle_votacion(
            id_votacion=id_votacion,
            id_sesion_votante=id_sesion,
            id_administrador=admin["id"] if admin else None,
        )
        return jsonify({"exito": True, "votacion": votacion}), 200
    except ErrorRecursoNoEncontrado as exc:
        return (
            jsonify({"exito": False, "error": exc.mensaje, "codigo": exc.codigo}),
            404,
        )
    except ErrorAlmacenamientoNoDisponible as exc:
        return (
            jsonify({"exito": False, "error": exc.mensaje, "codigo": exc.codigo}),
            503,
        )


@bp_publicas.route("/api/votaciones/<string:id_votacion>/votos", methods=["POST"])
@proteger_csrf
def api_registrar_voto(id_votacion: str) -> Any:
    """
    Registra un voto de forma atómica usando la identidad de sesión firmada
    en el servidor y la cuenta de administrador si está autenticada.
    """
    id_sesion = obtener_o_crear_id_sesion_votante()
    admin = obtener_admin_actual()
    id_admin = admin["id"] if admin else None

    id_opcion = ""
    if request.is_json:
        cuerpo = request.get_json(silent=True) or {}
        if isinstance(cuerpo, dict):
            id_opcion = str(cuerpo.get("id_opcion", "")).strip()
    else:
        id_opcion = str(request.form.get("id_opcion", "")).strip()

    servicio = _obtener_servicio_votaciones()

    try:
        votacion_actualizada = servicio.registrar_voto(
            id_votacion=id_votacion,
            id_opcion=id_opcion,
            id_sesion_votante=id_sesion,
            id_administrador=id_admin,
        )
        mensaje_exito = "¡Tu voto ha sido registrado atómicamente en Redis!"
        if es_solicitud_json():
            return (
                jsonify(
                    {
                        "exito": True,
                        "mensaje": mensaje_exito,
                        "votacion": votacion_actualizada,
                    }
                ),
                200,
            )
        flash(mensaje_exito, "exito")
        return redirect(url_for("publicas.detalle_votacion", id_votacion=id_votacion))

    except ErrorValidacion as exc:
        if es_solicitud_json():
            return (
                jsonify({"exito": False, "error": exc.mensaje, "codigo": exc.codigo}),
                400,
            )
        flash(exc.mensaje, "error")
        return redirect(url_for("publicas.detalle_votacion", id_votacion=id_votacion))

    except ErrorRecursoNoEncontrado as exc:
        if es_solicitud_json():
            return (
                jsonify({"exito": False, "error": exc.mensaje, "codigo": exc.codigo}),
                404,
            )
        flash(exc.mensaje, "error")
        return redirect(url_for("publicas.listar_votaciones"))

    except ErrorVotoRechazado as exc:
        if es_solicitud_json():
            return (
                jsonify({"exito": False, "error": exc.mensaje, "codigo": exc.codigo}),
                409,
            )
        flash(exc.mensaje, "error")
        return redirect(url_for("publicas.detalle_votacion", id_votacion=id_votacion))

    except ErrorAlmacenamientoNoDisponible as exc:
        if es_solicitud_json():
            return (
                jsonify({"exito": False, "error": exc.mensaje, "codigo": exc.codigo}),
                503,
            )
        flash(exc.mensaje, "error")
        return redirect(url_for("publicas.detalle_votacion", id_votacion=id_votacion))


@bp_publicas.route("/api/votaciones/<string:id_votacion>/eventos", methods=["GET"])
def api_eventos_votacion(id_votacion: str) -> Any:
    """
    Endpoint Server-Sent Events (SSE) que suscribe la conexión al canal Pub/Sub
    de Redis de la votación y transmite los resultados actualizados en vivo.
    """
    id_sesion = obtener_o_crear_id_sesion_votante()
    admin = obtener_admin_actual()
    id_admin = admin["id"] if admin else None
    servicio = _obtener_servicio_votaciones()

    try:
        # Comprobar previamente que la votación exista antes de abrir el stream SSE
        servicio.obtener_detalle_votacion(id_votacion)
    except ErrorRecursoNoEncontrado as exc:
        return (
            jsonify({"exito": False, "error": exc.mensaje, "codigo": exc.codigo}),
            404,
        )
    except ErrorAlmacenamientoNoDisponible as exc:
        return (
            jsonify({"exito": False, "error": exc.mensaje, "codigo": exc.codigo}),
            503,
        )

    def generar_flujo_sse() -> Iterator[str]:
        try:
            for item in servicio.escuchar_actualizaciones_votacion(
                id_votacion=id_votacion,
                id_sesion_votante=id_sesion,
                id_administrador=id_admin,
            ):
                tipo_evento = item.get("evento", "actualizacion")
                if tipo_evento == "latido":
                    yield ": keep-alive\n\n"
                    yield f"event: latido\ndata: {json.dumps({'tipo': 'latido'})}\n\n"
                else:
                    carga = json.dumps(
                        {
                            "evento": tipo_evento,
                            "votacion": item.get("votacion"),
                        }
                    )
                    yield f"event: actualizacion\ndata: {carga}\n\n"
        except GeneratorExit:
            return
        except ErrorAlmacenamientoNoDisponible as exc:
            carga_error = json.dumps({"error": exc.mensaje, "codigo": exc.codigo})
            yield f"event: error_servidor\ndata: {carga_error}\n\n"

    respuesta = Response(
        stream_with_context(generar_flujo_sse()),
        mimetype="text/event-stream",
    )
    respuesta.headers["Cache-Control"] = "no-cache"
    respuesta.headers["X-Accel-Buffering"] = "no"
    respuesta.headers["Connection"] = "keep-alive"
    return respuesta
