"""
Factoría de la aplicación Flask (`crear_aplicacion`) y comandos CLI de
mantenimiento académico.

Conecta las tres capas del sistema respetando la dirección de dependencias:
Presentación -> Negocio -> Datos -> Redis.
"""

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional
import click
from flask import Flask

from app.configuracion import Configuracion
from app.datos.cliente_redis import ErrorConexionRedis, GestorRedis
from app.datos.repositorio_administradores import RepositorioAdministradores
from app.datos.repositorio_votaciones import RepositorioVotaciones
from app.negocio.excepciones import ErrorNegocio
from app.negocio.servicio_autenticacion import ServicioAutenticacion
from app.negocio.servicio_votaciones import ServicioVotaciones
from app.presentacion.decoradores import (
    obtener_admin_actual,
    obtener_o_crear_id_sesion_votante,
    obtener_token_csrf,
)
from app.presentacion.rutas_administracion import bp_administracion
from app.presentacion.rutas_autenticacion import bp_autenticacion
from app.presentacion.rutas_publicas import bp_publicas


def formatear_fecha_iso(valor: Optional[str]) -> str:
    """Convierte una cadena ISO 8601 en un formato legible para la interfaz."""
    if not valor:
        return "—"
    try:
        dt = datetime.fromisoformat(valor)
        return dt.strftime("%d/%m/%Y %H:%M:%S UTC")
    except ValueError:
        return str(valor)


def crear_aplicacion(
    config_personalizada: Optional[Dict[str, Any]] = None,
    gestor_redis_personalizado: Optional[GestorRedis] = None,
) -> Flask:
    """
    Crea y configura una instancia de la aplicación Flask ensamblando las
    capas de datos, negocio y presentación.
    """
    directorio_presentacion = Path(__file__).resolve().parent / "presentacion"
    app = Flask(
        __name__,
        template_folder=str(directorio_presentacion / "plantillas"),
        static_folder=str(directorio_presentacion / "estaticos"),
        static_url_path="/estaticos",
    )

    configuracion = Configuracion(config_personalizada)
    app.config.from_mapping(configuracion.a_diccionario_flask())

    # 1. Inicializar Capa de Datos
    gestor_redis = gestor_redis_personalizado or GestorRedis(
        redis_url=configuracion.REDIS_URL,
        prefijo=configuracion.PREFIJO_REDIS,
    )
    repo_administradores = RepositorioAdministradores(gestor_redis)
    repo_votaciones = RepositorioVotaciones(gestor_redis)

    # 2. Inicializar Capa de Negocio
    servicio_autenticacion = ServicioAutenticacion(
        repositorio_administradores=repo_administradores,
        codigo_invitacion_esperado=configuracion.CODIGO_INVITACION_ADMIN,
        max_intentos_login=configuracion.MAX_INTENTOS_LOGIN,
        ventana_bloqueo_segundos=configuracion.VENTANA_BLOQUEO_LOGIN_SEGUNDOS,
    )
    servicio_votaciones = ServicioVotaciones(
        repositorio_votaciones=repo_votaciones,
    )

    app.extensions["gestor_redis"] = gestor_redis
    app.extensions["repositorio_administradores"] = repo_administradores
    app.extensions["repositorio_votaciones"] = repo_votaciones
    app.extensions["servicio_autenticacion"] = servicio_autenticacion
    app.extensions["servicio_votaciones"] = servicio_votaciones

    # 3. Registrar filtros y procesador de contexto para plantillas Jinja2
    app.jinja_env.filters["formatear_fecha"] = formatear_fecha_iso

    @app.context_processor
    def inyectar_contexto_plantillas() -> Dict[str, Any]:
        return {
            "csrf_token": obtener_token_csrf,
            "admin_actual": obtener_admin_actual(),
            "id_sesion_votante": obtener_o_crear_id_sesion_votante(),
            "prefijo_redis": app.config.get("PREFIJO_REDIS", "votacion"),
        }

    # 4. Registrar Blueprints de la Capa de Presentación
    app.register_blueprint(bp_publicas)
    app.register_blueprint(bp_autenticacion)
    app.register_blueprint(bp_administracion)

    # 5. Registrar comandos CLI de mantenimiento académico
    _registrar_comandos_cli(app)

    return app


def _registrar_comandos_cli(app: Flask) -> None:
    """Registra los comandos CLI solicitados para inspección y mantenimiento."""

    @app.cli.command("comprobar-redis")
    def comando_comprobar_redis() -> None:
        """Comprueba la conexión con Redis y muestra métricas básicas del prefijo."""
        gestor: GestorRedis = app.extensions["gestor_redis"]
        repo_admins: RepositorioAdministradores = app.extensions["repositorio_administradores"]
        repo_votaciones: RepositorioVotaciones = app.extensions["repositorio_votaciones"]
        try:
            activo = gestor.comprobar_conexion()
            total_admins = repo_admins.contar_administradores()
            total_votaciones = repo_votaciones.contar_votaciones()
            click.secho("Conexión a Redis exitosa (PONG recibido).", fg="green", bold=True)
            click.echo(f"  URL configurada : {gestor.redis_url}")
            click.echo(f"  Prefijo activo  : {gestor.prefijo}:*")
            click.echo(f"  Estado PING     : {'Activo' if activo else 'Inactivo'}")
            click.echo(f"  Administradores : {total_admins}")
            click.echo(f"  Votaciones      : {total_votaciones}")
        except ErrorConexionRedis as exc:
            click.secho(f"Error de conexión a Redis: {exc}", fg="red", bold=True)
            raise SystemExit(1) from exc

    @app.cli.command("consultar-votacion")
    @click.argument("id_votacion")
    def comando_consultar_votacion(id_votacion: str) -> None:
        """Consulta los metadatos y las claves Redis de una votación por su ID."""
        servicio: ServicioVotaciones = app.extensions["servicio_votaciones"]
        try:
            detalle = servicio.obtener_detalle_votacion(id_votacion)
            click.secho(f"=== Detalle de Votación: {detalle['id']} ===", fg="cyan", bold=True)
            click.echo(f"Pregunta         : {detalle['pregunta']}")
            click.echo(f"Estado           : {detalle['estado'].upper()}")
            click.echo(f"Creador          : {detalle['creador_usuario']} ({detalle['creador_id']})")
            click.echo(f"Fecha creación   : {formatear_fecha_iso(detalle['fecha_creacion'])}")
            click.echo(f"Fecha apertura   : {formatear_fecha_iso(detalle['fecha_apertura'])}")
            click.echo(f"Fecha cierre     : {formatear_fecha_iso(detalle['fecha_cierre'])}")
            click.echo(f"Total de votos   : {detalle['total_votos']}")
            click.echo(f"Identidades Set  : {detalle['total_identidades_registradas']}")
            click.echo("\nClaves Redis asociadas:")
            for nombre, info in detalle["estructuras_redis"].items():
                identificador = info.get("clave") or info.get("canal")
                click.echo(f"  - {nombre:15s}: {identificador} [{info['tipo']}]")
        except ErrorNegocio as exc:
            click.secho(f"Error: {exc.mensaje}", fg="red", bold=True)
            raise SystemExit(1) from exc

    @app.cli.command("consultar-resultados")
    @click.argument("id_votacion")
    def comando_consultar_resultados(id_votacion: str) -> None:
        """Consulta los resultados ordenados y porcentajes de una votación."""
        servicio: ServicioVotaciones = app.extensions["servicio_votaciones"]
        try:
            detalle = servicio.obtener_detalle_votacion(id_votacion)
            click.secho(
                f"=== Resultados de «{detalle['pregunta']}» ({detalle['estado'].upper()}) ===",
                fg="cyan",
                bold=True,
            )
            click.echo(f"Total de votos emitidos: {detalle['total_votos']}\n")
            for item in detalle["resultados"]:
                barra = "#" * int(item["porcentaje"] // 5)
                click.echo(
                    f"  #{item['posicion']} [{item['id_opcion']}] {item['texto']}\n"
                    f"     {item['votos']} votos ({item['porcentaje']}%) {barra}"
                )
        except ErrorNegocio as exc:
            click.secho(f"Error: {exc.mensaje}", fg="red", bold=True)
            raise SystemExit(1) from exc

    @app.cli.command("crear-votacion-ejemplo")
    def comando_crear_votacion_ejemplo() -> None:
        """Crea y abre una votación académica de demostración si aún no existen votaciones."""
        servicio: ServicioVotaciones = app.extensions["servicio_votaciones"]
        try:
            resultado = servicio.crear_votacion_ejemplo_si_no_existe()
            votacion = resultado["votacion"]
            if resultado["creada"]:
                click.secho("Votación de ejemplo creada y abierta con éxito:", fg="green", bold=True)
            else:
                click.secho(
                    "Ya existen votaciones registradas. Mostrando la más reciente:",
                    fg="yellow",
                    bold=True,
                )
            click.echo(f"  ID       : {votacion['id']}")
            click.echo(f"  Pregunta : {votacion['pregunta']}")
            click.echo(f"  Estado   : {votacion['estado']}")
        except ErrorNegocio as exc:
            click.secho(f"Error: {exc.mensaje}", fg="red", bold=True)
            raise SystemExit(1) from exc

    @app.cli.command("reiniciar-votacion")
    @click.argument("id_votacion")
    @click.option(
        "--confirmar",
        is_flag=True,
        help="Confirma explícitamente el reinicio de los resultados de esta votación.",
    )
    def comando_reiniciar_votacion(id_votacion: str, confirmar: bool) -> None:
        """
        Reinicia en cero los resultados y votantes únicamente de la votación
        seleccionada (sin ejecutar FLUSHDB ni FLUSHALL).
        """
        if not confirmar:
            click.secho(
                "Operación cancelada: debe incluir la bandera explícita --confirmar "
                "para reiniciar los resultados de la votación seleccionada.",
                fg="yellow",
                bold=True,
            )
            raise SystemExit(1)

        servicio: ServicioVotaciones = app.extensions["servicio_votaciones"]
        try:
            detalle = servicio.reiniciar_votacion_demostracion(id_votacion)
            click.secho(
                f"Se reiniciaron los resultados y votantes de la votación '{detalle['id']}'.",
                fg="green",
                bold=True,
            )
            click.echo(f"  Pregunta     : {detalle['pregunta']}")
            click.echo(f"  Votos ahora  : {detalle['total_votos']}")
        except ErrorNegocio as exc:
            click.secho(f"Error: {exc.mensaje}", fg="red", bold=True)
            raise SystemExit(1) from exc
