"""
Rutas protegidas del panel administrativo para crear, consultar, abrir,
cerrar y realizar mantenimiento sobre las votaciones.

Rutas definidas:
- `GET  /administracion`
- `GET  /administracion/votaciones/nueva`
- `POST /administracion/votaciones`
- `POST /administracion/votaciones/<id_votacion>/abrir`
- `POST /administracion/votaciones/<id_votacion>/cerrar`
- `POST /administracion/votaciones/<id_votacion>/reiniciar`
"""

from typing import Any, List
from flask import (
    Blueprint,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from app.negocio.excepciones import (
    ErrorAlmacenamientoNoDisponible,
    ErrorAutorizacion,
    ErrorConflictoEstado,
    ErrorRecursoNoEncontrado,
    ErrorValidacion,
)
from app.negocio.servicio_votaciones import ServicioVotaciones
from app.presentacion.decoradores import (
    obtener_admin_actual,
    obtener_o_crear_id_sesion_votante,
    proteger_csrf,
    requiere_administrador,
)

bp_administracion = Blueprint(
    "administracion",
    __name__,
    url_prefix="/administracion",
)


def _obtener_servicio_votaciones() -> ServicioVotaciones:
    """Obtiene la instancia configurada de `ServicioVotaciones`."""
    return current_app.extensions["servicio_votaciones"]


@bp_administracion.route("/api/votaciones", methods=["GET"])
@requiere_administrador
def api_panel_votaciones() -> Any:
    """
    Devuelve en formato JSON el resumen y el listado completo de votaciones
    para actualizar los contadores del panel administrativo en tiempo real.
    """
    admin = obtener_admin_actual()
    id_sesion = obtener_o_crear_id_sesion_votante()
    servicio = _obtener_servicio_votaciones()

    try:
        votaciones = servicio.listar_todas_las_votaciones(
            id_sesion_votante=id_sesion,
            id_administrador=admin["id"] if admin else None,
        )
        resumen = {
            "total": len(votaciones),
            "borrador": sum(1 for v in votaciones if v["estado"] == "borrador"),
            "abiertas": sum(1 for v in votaciones if v["estado"] == "abierta"),
            "cerradas": sum(1 for v in votaciones if v["estado"] == "cerrada"),
            "votos_totales": sum(v["total_votos"] for v in votaciones),
        }
        return jsonify({"exito": True, "resumen": resumen, "votaciones": votaciones}), 200
    except ErrorAlmacenamientoNoDisponible as exc:
        return (
            jsonify({"exito": False, "error": exc.mensaje, "codigo": exc.codigo}),
            503,
        )


@bp_administracion.route("", methods=["GET"])
@bp_administracion.route("/", methods=["GET"])
@requiere_administrador
def panel() -> Any:
    """Muestra el panel administrativo con todas las votaciones registradas."""
    admin = obtener_admin_actual()
    id_sesion = obtener_o_crear_id_sesion_votante()
    servicio = _obtener_servicio_votaciones()

    try:
        votaciones = servicio.listar_todas_las_votaciones(
            id_sesion_votante=id_sesion,
            id_administrador=admin["id"] if admin else None,
        )
        resumen = {
            "total": len(votaciones),
            "borrador": sum(1 for v in votaciones if v["estado"] == "borrador"),
            "abiertas": sum(1 for v in votaciones if v["estado"] == "abierta"),
            "cerradas": sum(1 for v in votaciones if v["estado"] == "cerrada"),
            "votos_totales": sum(v["total_votos"] for v in votaciones),
        }
        return render_template(
            "administracion/panel.html",
            votaciones=votaciones,
            resumen=resumen,
            prefijo_redis=current_app.config.get("PREFIJO_REDIS", "votacion"),
        )
    except ErrorAlmacenamientoNoDisponible as exc:
        flash(exc.mensaje, "error")
        return (
            render_template(
                "administracion/panel.html",
                votaciones=[],
                resumen={
                    "total": 0,
                    "borrador": 0,
                    "abiertas": 0,
                    "cerradas": 0,
                    "votos_totales": 0,
                },
                prefijo_redis=current_app.config.get("PREFIJO_REDIS", "votacion"),
            ),
            503,
        )


@bp_administracion.route("/votaciones/nueva", methods=["GET"])
@requiere_administrador
def nueva_votacion() -> Any:
    """Muestra el formulario para crear una nueva votación."""
    return render_template(
        "administracion/crear_votacion.html",
        pregunta_previa="",
        opciones_previas=["", "", ""],
    )


@bp_administracion.route("/votaciones", methods=["POST"])
@requiere_administrador
@proteger_csrf
def crear_votacion() -> Any:
    """
    Procesa la creación de una nueva votación en estado inicial `borrador` y,
    si el administrador seleccionó la opción `abrir_al_crear`, ejecuta a
    continuación la transición atómica a `abierta` para publicarla en vivo.
    """
    admin = obtener_admin_actual()
    pregunta = request.form.get("pregunta", "")
    opciones: List[str] = request.form.getlist("opciones")
    abrir_al_crear = request.form.get("abrir_al_crear", "") == "si"

    servicio = _obtener_servicio_votaciones()
    try:
        votacion = servicio.crear_votacion(
            pregunta=pregunta,
            opciones=opciones,
            creador_id=admin["id"] if admin else "",
            creador_usuario=admin["nombre_usuario"] if admin else "",
        )
        if abrir_al_crear:
            votacion = servicio.abrir_votacion(
                id_votacion=votacion["id"],
                id_administrador=admin["id"] if admin else "",
            )
            flash(
                f"Votación creada y ABIERTA en tiempo real: «{votacion['pregunta']}».",
                "exito",
            )
        else:
            flash(
                f"Votación creada exitosamente en estado borrador: «{votacion['pregunta']}».",
                "exito",
            )
        return redirect(url_for("administracion.panel"))
    except (ErrorValidacion, ErrorAutorizacion) as exc:
        flash(exc.mensaje, "error")
        opciones_mostrar = [op for op in opciones if op.strip()] or ["", ""]
        while len(opciones_mostrar) < 2:
            opciones_mostrar.append("")
        return (
            render_template(
                "administracion/crear_votacion.html",
                pregunta_previa=pregunta.strip(),
                opciones_previas=opciones_mostrar,
            ),
            400,
        )
    except ErrorAlmacenamientoNoDisponible as exc:
        flash(exc.mensaje, "error")
        return (
            render_template(
                "administracion/crear_votacion.html",
                pregunta_previa=pregunta.strip(),
                opciones_previas=opciones or ["", ""],
            ),
            503,
        )


@bp_administracion.route("/votaciones/<string:id_votacion>/abrir", methods=["POST"])
@requiere_administrador
@proteger_csrf
def abrir_votacion(id_votacion: str) -> Any:
    """Abre una votación en borrador para permitir la participación pública."""
    admin = obtener_admin_actual()
    servicio = _obtener_servicio_votaciones()
    try:
        votacion = servicio.abrir_votacion(
            id_votacion=id_votacion,
            id_administrador=admin["id"] if admin else "",
        )
        flash(
            f"La votación «{votacion['pregunta']}» ahora está ABIERTA para recibir votos.",
            "exito",
        )
    except (ErrorRecursoNoEncontrado, ErrorConflictoEstado, ErrorAutorizacion) as exc:
        flash(exc.mensaje, "error")
    except ErrorAlmacenamientoNoDisponible as exc:
        flash(exc.mensaje, "error")
    return redirect(url_for("administracion.panel"))


@bp_administracion.route("/votaciones/<string:id_votacion>/cerrar", methods=["POST"])
@requiere_administrador
@proteger_csrf
def cerrar_votacion(id_votacion: str) -> Any:
    """Cierra una votación abierta impidiendo nuevos votos."""
    admin = obtener_admin_actual()
    servicio = _obtener_servicio_votaciones()
    try:
        votacion = servicio.cerrar_votacion(
            id_votacion=id_votacion,
            id_administrador=admin["id"] if admin else "",
        )
        flash(
            f"La votación «{votacion['pregunta']}» ha sido CERRADA definitivamente.",
            "exito",
        )
    except (ErrorRecursoNoEncontrado, ErrorConflictoEstado, ErrorAutorizacion) as exc:
        flash(exc.mensaje, "error")
    except ErrorAlmacenamientoNoDisponible as exc:
        flash(exc.mensaje, "error")
    return redirect(url_for("administracion.panel"))


@bp_administracion.route("/votaciones/<string:id_votacion>/reiniciar", methods=["POST"])
@requiere_administrador
@proteger_csrf
def reiniciar_votacion(id_votacion: str) -> Any:
    """
    Acción explícita de mantenimiento para poner en cero los votos y limpiar
    el conjunto de votantes únicamente de la votación seleccionada.
    """
    servicio = _obtener_servicio_votaciones()
    try:
        votacion = servicio.reiniciar_votacion_demostracion(id_votacion)
        flash(
            f"Se reiniciaron los contadores y participantes de «{votacion['pregunta']}».",
            "exito",
        )
    except ErrorRecursoNoEncontrado as exc:
        flash(exc.mensaje, "error")
    except ErrorAlmacenamientoNoDisponible as exc:
        flash(exc.mensaje, "error")
    return redirect(url_for("administracion.panel"))
