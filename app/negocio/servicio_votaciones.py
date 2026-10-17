"""
Servicio de negocio para la gestión del ciclo de vida de las votaciones,
el registro atómico de votos y el cálculo de resultados y porcentajes.
"""

from datetime import datetime, timezone
import json
import uuid
from typing import Any, Dict, Iterator, List, Optional

from app.datos.cliente_redis import ErrorConexionRedis
from app.datos.repositorio_votaciones import RepositorioVotaciones
from app.negocio.excepciones import (
    ErrorAlmacenamientoNoDisponible,
    ErrorAutorizacion,
    ErrorConflictoEstado,
    ErrorRecursoNoEncontrado,
    ErrorValidacion,
    ErrorVotoRechazado,
)

LONGITUD_MINIMA_PREGUNTA = 5
LONGITUD_MAXIMA_PREGUNTA = 250
MINIMO_OPCIONES = 2
MAXIMO_OPCIONES = 10
LONGITUD_MAXIMA_OPCION = 120


class ServicioVotaciones:
    """Coordina las reglas de negocio de creación, estado, votación y resultados."""

    def __init__(self, repositorio_votaciones: RepositorioVotaciones) -> None:
        self.repositorio = repositorio_votaciones

    # -------------------------------------------------------------------------
    # Creación y validación de votaciones
    # -------------------------------------------------------------------------

    def crear_votacion(
        self,
        pregunta: str,
        opciones: List[str],
        creador_id: str,
        creador_usuario: str,
    ) -> Dict[str, Any]:
        """
        Valida la pregunta y las opciones (entre 2 y 10, sin duplicados) y
        crea una nueva votación con estado inicial `borrador`.
        """
        if not creador_id or not creador_usuario:
            raise ErrorAutorizacion(
                "Se requiere una sesión de administrador válida para crear votaciones.",
                "NO_AUTORIZADO",
            )

        if not isinstance(pregunta, str):
            raise ErrorValidacion("La pregunta debe ser una cadena de texto.", "PREGUNTA_INVALIDA")

        pregunta_limpia = pregunta.strip()
        if len(pregunta_limpia) < LONGITUD_MINIMA_PREGUNTA:
            raise ErrorValidacion(
                f"La pregunta debe tener al menos {LONGITUD_MINIMA_PREGUNTA} caracteres.",
                "PREGUNTA_MUY_CORTA",
            )
        if len(pregunta_limpia) > LONGITUD_MAXIMA_PREGUNTA:
            raise ErrorValidacion(
                f"La pregunta no puede exceder los {LONGITUD_MAXIMA_PREGUNTA} caracteres.",
                "PREGUNTA_MUY_LARGA",
            )

        if not isinstance(opciones, list):
            raise ErrorValidacion(
                "El listado de opciones proporcionado no tiene un formato válido.",
                "FORMATO_OPCIONES_INVALIDO",
            )

        opciones_limpias: List[str] = []
        vistos_normalizados = set()

        for opcion in opciones:
            if not isinstance(opcion, str):
                raise ErrorValidacion(
                    "Cada opción de la votación debe ser texto válido.",
                    "TIPO_OPCION_INVALIDO",
                )
            texto = opcion.strip()
            if not texto:
                continue
            if len(texto) > LONGITUD_MAXIMA_OPCION:
                raise ErrorValidacion(
                    f"Ninguna opción puede superar los {LONGITUD_MAXIMA_OPCION} caracteres.",
                    "OPCION_MUY_LARGA",
                )
            clave_normalizada = texto.lower()
            if clave_normalizada in vistos_normalizados:
                raise ErrorValidacion(
                    f"La opción '{texto}' está duplicada. Todas las opciones deben ser distintas.",
                    "OPCIONES_DUPLICADAS",
                )
            vistos_normalizados.add(clave_normalizada)
            opciones_limpias.append(texto)

        if len(opciones_limpias) < MINIMO_OPCIONES or len(opciones_limpias) > MAXIMO_OPCIONES:
            raise ErrorValidacion(
                f"La votación debe tener entre {MINIMO_OPCIONES} y {MAXIMO_OPCIONES} opciones válidas.",
                "CANTIDAD_OPCIONES_INVALIDA",
            )

        id_votacion = str(uuid.uuid4())
        ahora = datetime.now(timezone.utc)
        fecha_creacion = ahora.isoformat()
        marca_tiempo = ahora.timestamp()

        mapa_opciones: Dict[str, str] = {
            f"opcion_{indice}": texto for indice, texto in enumerate(opciones_limpias, start=1)
        }

        try:
            self.repositorio.guardar_votacion(
                id_votacion=id_votacion,
                pregunta=pregunta_limpia,
                opciones=mapa_opciones,
                creador_id=creador_id,
                creador_usuario=creador_usuario,
                fecha_creacion=fecha_creacion,
                marca_tiempo_epoch=marca_tiempo,
            )
            return self.obtener_detalle_votacion(id_votacion)
        except ErrorConexionRedis as exc:
            raise ErrorAlmacenamientoNoDisponible(str(exc)) from exc

    # -------------------------------------------------------------------------
    # Transiciones de estado (borrador -> abierta -> cerrada)
    # -------------------------------------------------------------------------

    def abrir_votacion(self, id_votacion: str, id_administrador: str) -> Dict[str, Any]:
        """
        Cambia el estado de una votación de `borrador` a `abierta` de forma atómica.
        No permite reabrir una votación que ya fue cerrada.
        """
        if not id_administrador:
            raise ErrorAutorizacion(
                "Solo un administrador autenticado puede abrir una votación.",
                "NO_AUTORIZADO",
            )

        ahora_iso = datetime.now(timezone.utc).isoformat()
        evento_json = json.dumps(
            {
                "evento": "estado_actualizado",
                "id_votacion": id_votacion,
                "nuevo_estado": "abierta",
                "fecha": ahora_iso,
            }
        )

        try:
            exito, codigo, estado_actual = self.repositorio.cambiar_estado_atomico(
                id_votacion=id_votacion,
                estado_esperado="borrador",
                nuevo_estado="abierta",
                campo_fecha="fecha_apertura",
                valor_fecha=ahora_iso,
                mensaje_evento=evento_json,
            )
        except ErrorConexionRedis as exc:
            raise ErrorAlmacenamientoNoDisponible(str(exc)) from exc

        if not exito:
            if codigo == "VOTACION_NO_EXISTE":
                raise ErrorRecursoNoEncontrado(
                    "La votación que intenta abrir no existe.",
                    "VOTACION_NO_EXISTE",
                )
            if estado_actual == "abierta":
                raise ErrorConflictoEstado(
                    "La votación ya se encuentra abierta.",
                    "VOTACION_YA_ABIERTA",
                )
            if estado_actual == "cerrada":
                raise ErrorConflictoEstado(
                    "No se permite reabrir una votación cerrada. Cree una nueva votación.",
                    "VOTACION_CERRADA_NO_REABRIBLE",
                )
            raise ErrorConflictoEstado(
                f"No se puede abrir la votación desde el estado '{estado_actual}'.",
                "TRANSICION_INVALIDA",
            )

        return self.obtener_detalle_votacion(id_votacion)

    def cerrar_votacion(self, id_votacion: str, id_administrador: str) -> Dict[str, Any]:
        """
        Cambia el estado de una votación de `abierta` a `cerrada` de forma atómica.
        """
        if not id_administrador:
            raise ErrorAutorizacion(
                "Solo un administrador autenticado puede cerrar una votación.",
                "NO_AUTORIZADO",
            )

        ahora_iso = datetime.now(timezone.utc).isoformat()
        evento_json = json.dumps(
            {
                "evento": "estado_actualizado",
                "id_votacion": id_votacion,
                "nuevo_estado": "cerrada",
                "fecha": ahora_iso,
            }
        )

        try:
            exito, codigo, estado_actual = self.repositorio.cambiar_estado_atomico(
                id_votacion=id_votacion,
                estado_esperado="abierta",
                nuevo_estado="cerrada",
                campo_fecha="fecha_cierre",
                valor_fecha=ahora_iso,
                mensaje_evento=evento_json,
            )
        except ErrorConexionRedis as exc:
            raise ErrorAlmacenamientoNoDisponible(str(exc)) from exc

        if not exito:
            if codigo == "VOTACION_NO_EXISTE":
                raise ErrorRecursoNoEncontrado(
                    "La votación que intenta cerrar no existe.",
                    "VOTACION_NO_EXISTE",
                )
            if estado_actual == "borrador":
                raise ErrorConflictoEstado(
                    "No se puede cerrar una votación en borrador antes de abrirla.",
                    "VOTACION_EN_BORRADOR",
                )
            if estado_actual == "cerrada":
                raise ErrorConflictoEstado(
                    "La votación ya se encuentra cerrada.",
                    "VOTACION_YA_CERRADA",
                )
            raise ErrorConflictoEstado(
                f"No se puede cerrar la votación desde el estado '{estado_actual}'.",
                "TRANSICION_INVALIDA",
            )

        return self.obtener_detalle_votacion(id_votacion)

    # -------------------------------------------------------------------------
    # Registro atómico de votos
    # -------------------------------------------------------------------------

    @staticmethod
    def construir_identidad_sesion(id_sesion: str) -> str:
        """Construye el identificador de votante para una sesión firmada."""
        return f"sesion:{id_sesion.strip()}"

    @staticmethod
    def construir_identidad_administrador(id_administrador: Optional[str]) -> str:
        """Construye el identificador estable de votante para una cuenta de administrador."""
        if not id_administrador or not str(id_administrador).strip():
            return ""
        return f"administrador:{str(id_administrador).strip()}"

    def registrar_voto(
        self,
        id_votacion: str,
        id_opcion: str,
        id_sesion_votante: str,
        id_administrador: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Coordina el registro atómico de un voto mediante el script Lua en Redis.
        Verifica y registra tanto la identidad de sesión (`sesion:<id>`) como
        la identidad de cuenta administrativa (`administrador:<id>`) cuando
        el usuario haya iniciado sesión.
        """
        id_votacion_limpio = (id_votacion or "").strip()
        id_opcion_limpio = (id_opcion or "").strip()
        id_sesion_limpio = (id_sesion_votante or "").strip()

        if not id_votacion_limpio:
            raise ErrorValidacion("Falta el identificador de la votación.", "VOTACION_REQUERIDA")
        if not id_opcion_limpio:
            raise ErrorValidacion(
                "Debe seleccionar una opción válida antes de votar.",
                "OPCION_REQUERIDA",
            )
        if not id_sesion_limpio:
            raise ErrorValidacion(
                "No se pudo determinar una sesión válida de participación.",
                "SESION_REQUERIDA",
            )

        identidad_sesion = self.construir_identidad_sesion(id_sesion_limpio)
        identidad_admin = self.construir_identidad_administrador(id_administrador)

        ahora_iso = datetime.now(timezone.utc).isoformat()
        evento_json = json.dumps(
            {
                "evento": "voto_registrado",
                "id_votacion": id_votacion_limpio,
                "id_opcion": id_opcion_limpio,
                "fecha": ahora_iso,
            }
        )

        try:
            exito, codigo, detalle = self.repositorio.registrar_voto_atomico(
                id_votacion=id_votacion_limpio,
                id_opcion=id_opcion_limpio,
                identidad_sesion=identidad_sesion,
                identidad_admin=identidad_admin,
                mensaje_evento=evento_json,
            )
        except ErrorConexionRedis as exc:
            raise ErrorAlmacenamientoNoDisponible(str(exc)) from exc

        if not exito:
            if codigo == "VOTACION_NO_EXISTE":
                raise ErrorRecursoNoEncontrado(
                    "La votación indicada no existe.",
                    "VOTACION_NO_EXISTE",
                )
            if codigo == "VOTACION_NO_ABIERTA":
                if detalle == "cerrada":
                    mensaje = "Esta votación ya fue cerrada y no admite nuevos votos."
                elif detalle == "borrador":
                    mensaje = "Esta votación aún está en borrador y no ha sido abierta."
                else:
                    mensaje = "La votación no se encuentra abierta para votar."
                raise ErrorVotoRechazado(mensaje, "VOTACION_NO_ABIERTA")
            if codigo == "OPCION_NO_EXISTE":
                raise ErrorVotoRechazado(
                    "La opción elegida no pertenece a esta votación.",
                    "OPCION_NO_EXISTE",
                )
            if codigo == "YA_VOTO_SESION":
                raise ErrorVotoRechazado(
                    "Ya has participado en esta votación desde tu sesión actual.",
                    "YA_VOTO_SESION",
                )
            if codigo == "YA_VOTO_ADMINISTRADOR":
                raise ErrorVotoRechazado(
                    "Tu cuenta de administrador ya registró un voto en esta votación.",
                    "YA_VOTO_ADMINISTRADOR",
                )
            raise ErrorVotoRechazado(
                "No fue posible registrar el voto solicitado.",
                codigo or "VOTO_RECHAZADO",
            )

        return self.obtener_detalle_votacion(
            id_votacion=id_votacion_limpio,
            id_sesion_votante=id_sesion_limpio,
            id_administrador=id_administrador,
        )

    # -------------------------------------------------------------------------
    # Consultas de votaciones, resultados y porcentajes
    # -------------------------------------------------------------------------

    @staticmethod
    def _orden_natural_opcion(id_opcion: str) -> int:
        """Extrae el número final de `opcion_N` para ordenar el formulario de votación."""
        partes = id_opcion.split("_")
        if len(partes) == 2 and partes[1].isdigit():
            return int(partes[1])
        return 999

    def obtener_detalle_votacion(
        self,
        id_votacion: str,
        id_sesion_votante: Optional[str] = None,
        id_administrador: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Construye la representación completa de una votación incluyendo sus
        opciones, conteos ordenados desde el Sorted Set, porcentajes calculados,
        estado de participación del visitante/administrador y claves Redis.
        """
        try:
            datos = self.repositorio.obtener_datos_votacion(id_votacion)
            if not datos:
                raise ErrorRecursoNoEncontrado(
                    "La votación solicitada no existe.",
                    "VOTACION_NO_EXISTE",
                )

            opciones_hash = self.repositorio.obtener_opciones_votacion(id_votacion)
            resultados_zset = self.repositorio.obtener_resultados_ordenados(id_votacion)
            total_identidades = self.repositorio.contar_identidades_votantes(id_votacion)
            total_opciones = self.repositorio.contar_opciones_registradas(id_votacion)

            ya_voto_sesion = False
            if id_sesion_votante:
                identidad_sesion = self.construir_identidad_sesion(id_sesion_votante)
                ya_voto_sesion = self.repositorio.ha_votado_identidad(
                    id_votacion, identidad_sesion
                )

            ya_voto_admin = False
            if id_administrador:
                identidad_admin = self.construir_identidad_administrador(id_administrador)
                ya_voto_admin = self.repositorio.ha_votado_identidad(
                    id_votacion, identidad_admin
                )
        except ErrorConexionRedis as exc:
            raise ErrorAlmacenamientoNoDisponible(str(exc)) from exc

        total_votos = sum(votos for _, votos in resultados_zset)
        mapa_votos = {id_op: votos for id_op, votos in resultados_zset}

        # Lista de resultados ordenada por mayor número de votos (salida directa de ZREVRANGE)
        lista_resultados: List[Dict[str, Any]] = []
        for posicion, (id_op, votos) in enumerate(resultados_zset, start=1):
            porcentaje = round((votos / total_votos) * 100.0, 1) if total_votos > 0 else 0.0
            lista_resultados.append(
                {
                    "posicion": posicion,
                    "id_opcion": id_op,
                    "texto": opciones_hash.get(id_op, id_op),
                    "votos": votos,
                    "porcentaje": porcentaje,
                }
            )

        # Lista de opciones en orden natural de creación para el formulario de votación
        opciones_formulario: List[Dict[str, Any]] = []
        for id_op in sorted(opciones_hash.keys(), key=self._orden_natural_opcion):
            votos = mapa_votos.get(id_op, 0)
            porcentaje = round((votos / total_votos) * 100.0, 1) if total_votos > 0 else 0.0
            opciones_formulario.append(
                {
                    "id_opcion": id_op,
                    "texto": opciones_hash[id_op],
                    "votos": votos,
                    "porcentaje": porcentaje,
                }
            )

        estado = datos.get("estado", "borrador")
        ya_voto = ya_voto_sesion or ya_voto_admin
        puede_votar = estado == "abierta" and not ya_voto

        motivo_bloqueo = ""
        if estado == "borrador":
            motivo_bloqueo = "Esta votación aún se encuentra en estado borrador y no admite votos."
        elif estado == "cerrada":
            motivo_bloqueo = "Esta votación está cerrada. Los resultados mostrados son definitivos."
        elif ya_voto_admin:
            motivo_bloqueo = "Ya registraste tu voto en esta votación con tu cuenta de administrador."
        elif ya_voto_sesion:
            motivo_bloqueo = "Ya registraste tu voto en esta votación desde tu sesión actual."

        return {
            "id": datos.get("id", id_votacion),
            "pregunta": datos.get("pregunta", ""),
            "estado": estado,
            "creador_id": datos.get("creador_id", ""),
            "creador_usuario": datos.get("creador_usuario", ""),
            "fecha_creacion": datos.get("fecha_creacion", ""),
            "fecha_apertura": datos.get("fecha_apertura", ""),
            "fecha_cierre": datos.get("fecha_cierre", ""),
            "total_votos": total_votos,
            "total_opciones": total_opciones,
            "total_identidades_registradas": total_identidades,
            "opciones": opciones_formulario,
            "resultados": lista_resultados,
            "participacion": {
                "ya_voto": ya_voto,
                "ya_voto_sesion": ya_voto_sesion,
                "ya_voto_administrador": ya_voto_admin,
                "puede_votar": puede_votar,
                "motivo_bloqueo": motivo_bloqueo,
            },
            "estructuras_redis": self.repositorio.obtener_resumen_claves_redis(id_votacion),
        }

    def listar_todas_las_votaciones(
        self,
        id_sesion_votante: Optional[str] = None,
        id_administrador: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Recupera todas las votaciones ordenadas por fecha de creación descendente.
        Se utiliza en el panel administrativo.
        """
        try:
            ids = self.repositorio.listar_ids_votaciones()
        except ErrorConexionRedis as exc:
            raise ErrorAlmacenamientoNoDisponible(str(exc)) from exc

        votaciones: List[Dict[str, Any]] = []
        for id_votacion in ids:
            try:
                detalle = self.obtener_detalle_votacion(
                    id_votacion=id_votacion,
                    id_sesion_votante=id_sesion_votante,
                    id_administrador=id_administrador,
                )
                votaciones.append(detalle)
            except ErrorRecursoNoEncontrado:
                continue
        return votaciones

    def listar_votaciones_publicas(
        self,
        id_sesion_votante: Optional[str] = None,
        id_administrador: Optional[str] = None,
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        Devuelve las votaciones públicas clasificadas en `abiertas` y `cerradas`.
        Las votaciones en estado `borrador` nunca se exponen en el listado público.
        """
        todas = self.listar_todas_las_votaciones(
            id_sesion_votante=id_sesion_votante,
            id_administrador=id_administrador,
        )
        abiertas = [v for v in todas if v["estado"] == "abierta"]
        cerradas = [v for v in todas if v["estado"] == "cerrada"]
        return {
            "abiertas": abiertas,
            "cerradas": cerradas,
        }

    # -------------------------------------------------------------------------
    # Suscripción a eventos en tiempo real y mantenimiento CLI
    # -------------------------------------------------------------------------

    def escuchar_actualizaciones_votacion(
        self,
        id_votacion: str,
        id_sesion_votante: Optional[str] = None,
        id_administrador: Optional[str] = None,
        intervalo_latido_segundos: float = 15.0,
    ) -> Iterator[Dict[str, Any]]:
        """
        Verifica que la votación exista y abre un generador conectado al canal
        Redis Pub/Sub de la votación. Cada vez que ocurre una actualización,
        consulta el estado fresco en Redis y lo cede al endpoint SSE.
        """
        detalle_inicial = self.obtener_detalle_votacion(
            id_votacion=id_votacion,
            id_sesion_votante=id_sesion_votante,
            id_administrador=id_administrador,
        )
        yield {
            "evento": "estado_inicial",
            "votacion": detalle_inicial,
        }

        try:
            for notificacion in self.repositorio.suscribir_eventos_votacion(
                id_votacion=id_votacion,
                intervalo_latido_segundos=intervalo_latido_segundos,
            ):
                if notificacion.get("tipo") == "latido":
                    yield {"evento": "latido", "comentario": "keep-alive"}
                else:
                    detalle_actualizado = self.obtener_detalle_votacion(
                        id_votacion=id_votacion,
                        id_sesion_votante=id_sesion_votante,
                        id_administrador=id_administrador,
                    )
                    yield {
                        "evento": "actualizacion",
                        "votacion": detalle_actualizado,
                    }
        except ErrorConexionRedis as exc:
            raise ErrorAlmacenamientoNoDisponible(str(exc)) from exc

    def escuchar_actualizaciones_listado_publico(
        self,
        id_sesion_votante: Optional[str] = None,
        id_administrador: Optional[str] = None,
        intervalo_latido_segundos: float = 15.0,
    ) -> Iterator[Dict[str, Any]]:
        """
        Abre un generador conectado al canal global de votaciones en Redis Pub/Sub.
        Cuando se abre o cierra una votación (o cambian sus conteos), cede el
        listado público actualizado de votaciones abiertas y cerradas.
        """
        listado_inicial = self.listar_votaciones_publicas(
            id_sesion_votante=id_sesion_votante,
            id_administrador=id_administrador,
        )
        yield {
            "evento": "estado_inicial",
            "listado": listado_inicial,
        }

        try:
            for notificacion in self.repositorio.suscribir_eventos_globales(
                intervalo_latido_segundos=intervalo_latido_segundos,
            ):
                if notificacion.get("tipo") == "latido":
                    yield {"evento": "latido", "comentario": "keep-alive"}
                else:
                    listado_actualizado = self.listar_votaciones_publicas(
                        id_sesion_votante=id_sesion_votante,
                        id_administrador=id_administrador,
                    )
                    yield {
                        "evento": "actualizacion_listado",
                        "listado": listado_actualizado,
                    }
        except ErrorConexionRedis as exc:
            raise ErrorAlmacenamientoNoDisponible(str(exc)) from exc

    def reiniciar_votacion_demostracion(self, id_votacion: str) -> Dict[str, Any]:
        """
        Reinicia explícitamente los conteos y votantes de una única votación
        (sin afectar otras claves de Redis ni ejecutar FLUSHDB/FLUSHALL).
        """
        ahora_iso = datetime.now(timezone.utc).isoformat()
        evento_json = json.dumps(
            {
                "evento": "votacion_reiniciada",
                "id_votacion": id_votacion,
                "fecha": ahora_iso,
            }
        )
        try:
            reiniciada = self.repositorio.reiniciar_resultados_votacion(
                id_votacion=id_votacion,
                mensaje_evento=evento_json,
            )
        except ErrorConexionRedis as exc:
            raise ErrorAlmacenamientoNoDisponible(str(exc)) from exc

        if not reiniciada:
            raise ErrorRecursoNoEncontrado(
                f"No existe ninguna votación con el identificador '{id_votacion}'.",
                "VOTACION_NO_EXISTE",
            )
        return self.obtener_detalle_votacion(id_votacion)

    def crear_votacion_ejemplo_si_no_existe(self) -> Dict[str, Any]:
        """
        Crea y abre una votación académica de ejemplo únicamente si aún no
        existe ninguna votación registrada bajo el prefijo actual.
        """
        todas = self.listar_todas_las_votaciones()
        if todas:
            return {
                "creada": False,
                "votacion": todas[0],
            }

        votacion = self.crear_votacion(
            pregunta="¿Cuál estructura de Redis consideras más versátil para sistemas en tiempo real?",
            opciones=[
                "Sorted Sets (ZSET) para rankings y conteos ordenados",
                "Hashes para almacenar objetos y metadatos estructurados",
                "Sets para control de unicidad y pertenencia O(1)",
                "Pub/Sub junto con Scripts Lua para eventos atómicos",
            ],
            creador_id="sistema-academico",
            creador_usuario="demostracion_cli",
        )
        votacion_abierta = self.abrir_votacion(
            id_votacion=votacion["id"],
            id_administrador="sistema-academico",
        )
        return {
            "creada": True,
            "votacion": votacion_abierta,
        }
