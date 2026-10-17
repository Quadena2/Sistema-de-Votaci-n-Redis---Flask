"""
Rutas de registro, inicio y cierre de sesión para administradores.

Rutas definidas:
- `GET  /administracion/registro`
- `POST /administracion/registro`
- `GET  /administracion/ingresar`
- `POST /administracion/ingresar`
- `POST /administracion/salir`
"""

from typing import Any
from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from app.negocio.excepciones import (
    ErrorAlmacenamientoNoDisponible,
    ErrorAutenticacion,
    ErrorLimiteIntentos,
    ErrorValidacion,
)
from app.negocio.servicio_autenticacion import ServicioAutenticacion
from app.presentacion.decoradores import (
    cerrar_sesion_administrador,
    establecer_sesion_administrador,
    obtener_admin_actual,
    proteger_csrf,
)

bp_autenticacion = Blueprint(
    "autenticacion",
    __name__,
    url_prefix="/administracion",
)


def _obtener_servicio_autenticacion() -> ServicioAutenticacion:
    """Obtiene la instancia configurada de `ServicioAutenticacion`."""
    return current_app.extensions["servicio_autenticacion"]


@bp_autenticacion.route("/registro", methods=["GET", "POST"])
@proteger_csrf
def registro() -> Any:
    """Formulario y procesamiento de registro de nuevos administradores."""
    admin_actual = obtener_admin_actual()
    if admin_actual and request.method == "GET":
        return redirect(url_for("administracion.panel"))

    if request.method == "GET":
        return render_template("administracion/registro.html", valores_previos={})

    nombre_usuario = request.form.get("nombre_usuario", "")
    contrasena = request.form.get("contrasena", "")
    confirmacion_contrasena = request.form.get("confirmacion_contrasena", "")
    codigo_invitacion = request.form.get("codigo_invitacion", "")

    servicio = _obtener_servicio_autenticacion()
    try:
        datos_admin = servicio.registrar_administrador(
            nombre_usuario=nombre_usuario,
            contrasena=contrasena,
            confirmacion_contrasena=confirmacion_contrasena,
            codigo_invitacion=codigo_invitacion,
        )
        establecer_sesion_administrador(datos_admin)
        flash(
            f"Cuenta de administrador '{datos_admin['nombre_usuario']}' creada correctamente.",
            "exito",
        )
        return redirect(url_for("administracion.panel"))
    except (ErrorValidacion, ErrorAutenticacion) as exc:
        flash(exc.mensaje, "error")
        return (
            render_template(
                "administracion/registro.html",
                valores_previos={"nombre_usuario": nombre_usuario.strip()},
            ),
            400,
        )
    except ErrorAlmacenamientoNoDisponible as exc:
        flash(exc.mensaje, "error")
        return (
            render_template(
                "administracion/registro.html",
                valores_previos={"nombre_usuario": nombre_usuario.strip()},
            ),
            503,
        )


@bp_autenticacion.route("/ingresar", methods=["GET", "POST"])
@proteger_csrf
def ingresar() -> Any:
    """Formulario y validación de inicio de sesión para administradores."""
    admin_actual = obtener_admin_actual()
    if admin_actual and request.method == "GET":
        return redirect(url_for("administracion.panel"))

    if request.method == "GET":
        return render_template("administracion/ingresar.html", valores_previos={})

    nombre_usuario = request.form.get("nombre_usuario", "")
    contrasena = request.form.get("contrasena", "")
    ip_cliente = request.remote_addr or "cliente_local"

    servicio = _obtener_servicio_autenticacion()
    try:
        datos_admin = servicio.autenticar_administrador(
            nombre_usuario=nombre_usuario,
            contrasena=contrasena,
            identificador_cliente=ip_cliente,
        )
        establecer_sesion_administrador(datos_admin)
        flash(
            f"Bienvenido al panel administrativo, {datos_admin['nombre_usuario']}.",
            "exito",
        )
        return redirect(url_for("administracion.panel"))
    except ErrorValidacion as exc:
        flash(exc.mensaje, "error")
        return (
            render_template(
                "administracion/ingresar.html",
                valores_previos={"nombre_usuario": nombre_usuario.strip()},
            ),
            400,
        )
    except ErrorAutenticacion as exc:
        flash(exc.mensaje, "error")
        return (
            render_template(
                "administracion/ingresar.html",
                valores_previos={"nombre_usuario": nombre_usuario.strip()},
            ),
            401,
        )
    except ErrorLimiteIntentos as exc:
        flash(exc.mensaje, "error")
        return (
            render_template(
                "administracion/ingresar.html",
                valores_previos={"nombre_usuario": nombre_usuario.strip()},
            ),
            429,
        )
    except ErrorAlmacenamientoNoDisponible as exc:
        flash(exc.mensaje, "error")
        return (
            render_template(
                "administracion/ingresar.html",
                valores_previos={"nombre_usuario": nombre_usuario.strip()},
            ),
            503,
        )


@bp_autenticacion.route("/salir", methods=["POST"])
@proteger_csrf
def salir() -> Any:
    """Cierra la sesión administrativa actual de forma segura."""
    cerrar_sesion_administrador()
    flash("Has cerrado la sesión administrativa correctamente.", "exito")
    return redirect(url_for("publicas.inicio"))
