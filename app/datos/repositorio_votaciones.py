"""
Repositorio de datos para la gestión de votaciones, resultados, identidades
de votantes y canales Pub/Sub en Redis.

Estructuras Redis utilizadas:
- `<prefijo>:votaciones:indice` (Sorted Set): IDs de votaciones ordenados por
  marca de tiempo de creación (`score` = timestamp epoch).
- `<prefijo>:votacion:<id>:datos` (Hash): metadatos de la votación (`id`,
  `pregunta`, `estado`, `creador_id`, `creador_usuario`, `fecha_creacion`,
  `fecha_apertura`, `fecha_cierre`).
- `<prefijo>:votacion:<id>:opciones` (Hash): mapa `<id_opcion> -> <texto>`.
- `<prefijo>:votacion:<id>:resultados` (Sorted Set): cada miembro es `<id_opcion>`
  y su `score` es la cantidad acumulada de votos.
- `<prefijo>:votacion:<id>:votantes` (Set): identidades (`sesion:<id>` y
  `administrador:<id>`) que ya emitieron voto en esta votación.
- `<prefijo>:canal:votacion:<id>` (Canal Pub/Sub): notificaciones en tiempo real.
"""

import time
from typing import Any, Dict, Iterator, List, Optional, Tuple
from redis.exceptions import RedisError
from app.datos.cliente_redis import ErrorConexionRedis, GestorRedis


class RepositorioVotaciones:
    """Encapsula todas las operaciones de Redis asociadas a las votaciones."""

    def __init__(self, gestor_redis: GestorRedis) -> None:
        self.gestor = gestor_redis

    # -------------------------------------------------------------------------
    # Constructores de claves y canales Redis
    # -------------------------------------------------------------------------

    def clave_indice_votaciones(self) -> str:
        """Sorted Set que indexa todas las votaciones por fecha de creación."""
        return self.gestor.construir_clave("votaciones", "indice")

    def clave_datos_votacion(self, id_votacion: str) -> str:
        """Hash con los metadatos y el estado de la votación."""
        return self.gestor.construir_clave("votacion", id_votacion, "datos")

    def clave_opciones_votacion(self, id_votacion: str) -> str:
        """Hash que asocia cada identificador de opción con su texto."""
        return self.gestor.construir_clave("votacion", id_votacion, "opciones")

    def clave_resultados_votacion(self, id_votacion: str) -> str:
        """Sorted Set que mantiene el conteo de votos por opción."""
        return self.gestor.construir_clave("votacion", id_votacion, "resultados")

    def clave_votantes_votacion(self, id_votacion: str) -> str:
        """Set que almacena las identidades que ya participaron en la votación."""
        return self.gestor.construir_clave("votacion", id_votacion, "votantes")

    def canal_eventos_votacion(self, id_votacion: str) -> str:
        """Canal Pub/Sub exclusivo para notificar eventos de una votación."""
        return self.gestor.construir_clave("canal", "votacion", id_votacion)

    def canal_global_votaciones(self) -> str:
        """Canal Pub/Sub global para notificar apertura, cierre o cambios en el listado."""
        return self.gestor.construir_clave("canal", "votaciones", "global")

    # -------------------------------------------------------------------------
    # Escritura y lectura de votaciones
    # -------------------------------------------------------------------------

    def guardar_votacion(
        self,
        id_votacion: str,
        pregunta: str,
        opciones: Dict[str, str],
        creador_id: str,
        creador_usuario: str,
        fecha_creacion: str,
        marca_tiempo_epoch: float,
    ) -> None:
        """
        Almacena una nueva votación en estado `borrador` inicializando su Hash de datos,
        su Hash de opciones, su Sorted Set de resultados en cero y su entrada en el
        Sorted Set índice.
        """
        try:
            cliente = self.gestor.obtener_cliente()
            tuberia = cliente.pipeline(transaction=True)

            clave_datos = self.clave_datos_votacion(id_votacion)
            clave_opciones = self.clave_opciones_votacion(id_votacion)
            clave_resultados = self.clave_resultados_votacion(id_votacion)
            clave_indice = self.clave_indice_votaciones()

            tuberia.hset(
                clave_datos,
                mapping={
                    "id": id_votacion,
                    "pregunta": pregunta,
                    "estado": "borrador",
                    "creador_id": creador_id,
                    "creador_usuario": creador_usuario,
                    "fecha_creacion": fecha_creacion,
                    "fecha_apertura": "",
                    "fecha_cierre": "",
                },
            )

            tuberia.hset(clave_opciones, mapping=opciones)

            puntajes_iniciales = {id_opcion: 0.0 for id_opcion in opciones.keys()}
            tuberia.zadd(clave_resultados, puntajes_iniciales)

            tuberia.zadd(clave_indice, {id_votacion: float(marca_tiempo_epoch)})
            tuberia.publish(
                self.canal_global_votaciones(),
                f'{{"evento":"votacion_creada","id_votacion":"{id_votacion}"}}',
            )

            tuberia.execute()
        except RedisError as exc:
            raise ErrorConexionRedis(
                "Error al guardar la nueva votación en Redis."
            ) from exc

    def existe_votacion(self, id_votacion: str) -> bool:
        """Comprueba si existe el campo `id` en el Hash de la votación (`HEXISTS`)."""
        if not id_votacion:
            return False
        try:
            cliente = self.gestor.obtener_cliente()
            return bool(cliente.hexists(self.clave_datos_votacion(id_votacion), "id"))
        except RedisError as exc:
            raise ErrorConexionRedis(
                "Error al comprobar la existencia de la votación en Redis."
            ) from exc

    def obtener_estado_votacion(self, id_votacion: str) -> Optional[str]:
        """Consulta únicamente el campo `estado` del Hash (`HGET`)."""
        if not id_votacion:
            return None
        try:
            cliente = self.gestor.obtener_cliente()
            estado = cliente.hget(self.clave_datos_votacion(id_votacion), "estado")
            return str(estado) if estado is not None else None
        except RedisError as exc:
            raise ErrorConexionRedis(
                "Error al consultar el estado de la votación en Redis."
            ) from exc

    def obtener_datos_votacion(self, id_votacion: str) -> Optional[Dict[str, str]]:
        """Recupera todos los campos del Hash de metadatos (`HGETALL`)."""
        if not id_votacion:
            return None
        try:
            cliente = self.gestor.obtener_cliente()
            datos = cliente.hgetall(self.clave_datos_votacion(id_votacion))
            return dict(datos) if datos else None
        except RedisError as exc:
            raise ErrorConexionRedis(
                "Error al obtener los datos de la votación desde Redis."
            ) from exc

    def obtener_opciones_votacion(self, id_votacion: str) -> Dict[str, str]:
        """Recupera todas las opciones del Hash `opciones` (`HGETALL`)."""
        try:
            cliente = self.gestor.obtener_cliente()
            opciones = cliente.hgetall(self.clave_opciones_votacion(id_votacion))
            return dict(opciones) if opciones else {}
        except RedisError as exc:
            raise ErrorConexionRedis(
                "Error al obtener las opciones de la votación desde Redis."
            ) from exc

    def obtener_texto_opcion(self, id_votacion: str, id_opcion: str) -> Optional[str]:
        """Obtiene el texto de una opción específica mediante `HGET`."""
        try:
            cliente = self.gestor.obtener_cliente()
            texto = cliente.hget(self.clave_opciones_votacion(id_votacion), id_opcion)
            return str(texto) if texto is not None else None
        except RedisError as exc:
            raise ErrorConexionRedis(
                "Error al consultar el texto de la opción en Redis."
            ) from exc

    def existe_opcion(self, id_votacion: str, id_opcion: str) -> bool:
        """Verifica si una opción pertenece a la votación usando `HEXISTS`."""
        try:
            cliente = self.gestor.obtener_cliente()
            return bool(cliente.hexists(self.clave_opciones_votacion(id_votacion), id_opcion))
        except RedisError as exc:
            raise ErrorConexionRedis(
                "Error al verificar la opción de la votación en Redis."
            ) from exc

    def obtener_resultados_ordenados(self, id_votacion: str) -> List[Tuple[str, int]]:
        """
        Devuelve la lista de `(id_opcion, votos)` ordenada de mayor a menor
        puntuación utilizando `ZREVRANGE` con `withscores=True`.
        """
        try:
            cliente = self.gestor.obtener_cliente()
            resultados = cliente.zrevrange(
                self.clave_resultados_votacion(id_votacion),
                0,
                -1,
                withscores=True,
            )
            return [(str(id_opcion), int(puntaje)) for id_opcion, puntaje in resultados]
        except RedisError as exc:
            raise ErrorConexionRedis(
                "Error al consultar el Sorted Set de resultados en Redis."
            ) from exc

    def contar_opciones_registradas(self, id_votacion: str) -> int:
        """Devuelve la cardinalidad del Sorted Set de resultados (`ZCARD`)."""
        try:
            cliente = self.gestor.obtener_cliente()
            return int(cliente.zcard(self.clave_resultados_votacion(id_votacion)))
        except RedisError as exc:
            raise ErrorConexionRedis(
                "Error al contar las opciones de la votación en Redis."
            ) from exc

    def contar_identidades_votantes(self, id_votacion: str) -> int:
        """Devuelve la cardinalidad del Set de identidades registradas (`SCARD`)."""
        try:
            cliente = self.gestor.obtener_cliente()
            return int(cliente.scard(self.clave_votantes_votacion(id_votacion)))
        except RedisError as exc:
            raise ErrorConexionRedis(
                "Error al contar las identidades de votantes en Redis."
            ) from exc

    def ha_votado_identidad(self, id_votacion: str, identidad: str) -> bool:
        """
        Comprueba si una identidad (`sesion:<id>` o `administrador:<id>`) ya se
        encuentra en el Set de votantes mediante `SISMEMBER`.
        """
        if not identidad:
            return False
        try:
            cliente = self.gestor.obtener_cliente()
            return bool(
                cliente.sismember(self.clave_votantes_votacion(id_votacion), identidad)
            )
        except RedisError as exc:
            raise ErrorConexionRedis(
                "Error al verificar participación previa en Redis."
            ) from exc

    def listar_ids_votaciones(self) -> List[str]:
        """
        Obtiene los identificadores de todas las votaciones ordenados desde la
        más reciente hasta la más antigua usando `ZREVRANGE` sobre el índice.
        """
        try:
            cliente = self.gestor.obtener_cliente()
            ids = cliente.zrevrange(self.clave_indice_votaciones(), 0, -1)
            return [str(item) for item in ids]
        except RedisError as exc:
            raise ErrorConexionRedis(
                "Error al listar el índice de votaciones desde Redis."
            ) from exc

    def contar_votaciones(self) -> int:
        """Devuelve el total de votaciones registradas en el índice (`ZCARD`)."""
        try:
            cliente = self.gestor.obtener_cliente()
            return int(cliente.zcard(self.clave_indice_votaciones()))
        except RedisError as exc:
            raise ErrorConexionRedis(
                "Error al contar las votaciones en Redis."
            ) from exc

    # -------------------------------------------------------------------------
    # Operaciones atómicas con Lua
    # -------------------------------------------------------------------------

    def cambiar_estado_atomico(
        self,
        id_votacion: str,
        estado_esperado: str,
        nuevo_estado: str,
        campo_fecha: str,
        valor_fecha: str,
        mensaje_evento: str,
    ) -> Tuple[bool, str, str]:
        """
        Ejecuta `cambiar_estado_votacion.lua` para realizar la transición de
        estado de manera atómica y publicar el cambio en el canal individual
        y en el canal global de listados.
        """
        try:
            script = self.gestor.obtener_script_lua("cambiar_estado_votacion.lua")
            respuesta = script(
                keys=[
                    self.clave_datos_votacion(id_votacion),
                    self.canal_eventos_votacion(id_votacion),
                    self.canal_global_votaciones(),
                ],
                args=[
                    estado_esperado,
                    nuevo_estado,
                    campo_fecha,
                    valor_fecha,
                    mensaje_evento,
                ],
            )
            exito = int(respuesta[0]) == 1
            codigo = str(respuesta[1])
            detalle = str(respuesta[2]) if len(respuesta) > 2 else ""
            return exito, codigo, detalle
        except RedisError as exc:
            raise ErrorConexionRedis(
                "Error al ejecutar el cambio de estado atómico en Redis."
            ) from exc

    def registrar_voto_atomico(
        self,
        id_votacion: str,
        id_opcion: str,
        identidad_sesion: str,
        identidad_admin: str,
        mensaje_evento: str,
    ) -> Tuple[bool, str, str]:
        """
        Ejecuta `registrar_voto.lua` para validar y registrar un voto de forma
        completamente atómica en Redis, publicando tanto en el canal individual
        como en el canal global.
        """
        try:
            script = self.gestor.obtener_script_lua("registrar_voto.lua")
            respuesta = script(
                keys=[
                    self.clave_datos_votacion(id_votacion),
                    self.clave_opciones_votacion(id_votacion),
                    self.clave_resultados_votacion(id_votacion),
                    self.clave_votantes_votacion(id_votacion),
                    self.canal_eventos_votacion(id_votacion),
                    self.canal_global_votaciones(),
                ],
                args=[
                    id_opcion,
                    identidad_sesion,
                    identidad_admin or "",
                    mensaje_evento,
                ],
            )
            exito = int(respuesta[0]) == 1
            codigo = str(respuesta[1])
            detalle = str(respuesta[2]) if len(respuesta) > 2 else ""
            return exito, codigo, detalle
        except RedisError as exc:
            raise ErrorConexionRedis(
                "Error al procesar el registro atómico del voto en Redis."
            ) from exc

    # -------------------------------------------------------------------------
    # Operaciones de mantenimiento acotadas (sin FLUSHALL / FLUSHDB)
    # -------------------------------------------------------------------------

    def reiniciar_resultados_votacion(self, id_votacion: str, mensaje_evento: str) -> bool:
        """
        Reinicia exclusivamente los resultados y el registro de votantes de una
        votación específica, poniendo en 0 los puntajes del Sorted Set de
        resultados y eliminando únicamente el Set `votacion:<id>:votantes`.
        """
        if not self.existe_votacion(id_votacion):
            return False
        try:
            opciones = self.obtener_opciones_votacion(id_votacion)
            cliente = self.gestor.obtener_cliente()
            tuberia = cliente.pipeline(transaction=True)

            if opciones:
                puntajes_cero = {id_op: 0.0 for id_op in opciones.keys()}
                tuberia.zadd(self.clave_resultados_votacion(id_votacion), puntajes_cero)

            tuberia.delete(self.clave_votantes_votacion(id_votacion))
            tuberia.publish(self.canal_eventos_votacion(id_votacion), mensaje_evento)
            tuberia.publish(self.canal_global_votaciones(), mensaje_evento)
            tuberia.execute()
            return True
        except RedisError as exc:
            raise ErrorConexionRedis(
                "Error al reiniciar los datos de la votación seleccionada en Redis."
            ) from exc

    def obtener_resumen_claves_redis(self, id_votacion: str) -> Dict[str, Any]:
        """
        Devuelve los nombres reales de las claves y el canal Pub/Sub asociados
        a una votación para su inspección académica en la interfaz y en CLI.
        """
        return {
            "indice_global": {
                "clave": self.clave_indice_votaciones(),
                "tipo": "Sorted Set (ZSET)",
                "comandos": ["ZADD", "ZREVRANGE", "ZCARD"],
            },
            "datos": {
                "clave": self.clave_datos_votacion(id_votacion),
                "tipo": "Hash",
                "comandos": ["HSET", "HGET", "HGETALL", "HEXISTS"],
            },
            "opciones": {
                "clave": self.clave_opciones_votacion(id_votacion),
                "tipo": "Hash",
                "comandos": ["HSET", "HGET", "HGETALL", "HEXISTS"],
            },
            "resultados": {
                "clave": self.clave_resultados_votacion(id_votacion),
                "tipo": "Sorted Set (ZSET)",
                "comandos": ["ZADD", "ZINCRBY", "ZREVRANGE WITHSCORES", "ZCARD"],
            },
            "votantes": {
                "clave": self.clave_votantes_votacion(id_votacion),
                "tipo": "Set",
                "comandos": ["SADD", "SISMEMBER", "SCARD"],
            },
            "canal_pubsub": {
                "canal": self.canal_eventos_votacion(id_votacion),
                "tipo": "Canal Pub/Sub",
                "comandos": ["PUBLISH", "SUBSCRIBE"],
            },
        }

    # -------------------------------------------------------------------------
    # Suscripción Pub/Sub para Server-Sent Events (SSE)
    # -------------------------------------------------------------------------

    def _suscribir_canal(
        self,
        canal: str,
        intervalo_latido_segundos: float = 15.0,
    ) -> Iterator[Dict[str, Any]]:
        """
        Abre una suscripción Redis Pub/Sub sobre el canal indicado y cede
        diccionarios de evento cuando llegan notificaciones o latidos de
        mantenimiento (`keep-alive`). Libera siempre la conexión al salir.
        """
        pubsub = self.gestor.crear_cliente_pubsub()
        ultimo_latido = time.monotonic()

        try:
            pubsub.subscribe(canal)
            while True:
                mensaje = pubsub.get_message(timeout=1.0)
                if mensaje and mensaje.get("type") == "message":
                    yield {
                        "tipo": "actualizacion",
                        "canal": canal,
                        "datos": str(mensaje.get("data", "")),
                    }
                    ultimo_latido = time.monotonic()
                else:
                    ahora = time.monotonic()
                    if ahora - ultimo_latido >= intervalo_latido_segundos:
                        yield {
                            "tipo": "latido",
                            "canal": canal,
                            "datos": "mantener-conexion",
                        }
                        ultimo_latido = ahora
        except RedisError as exc:
            raise ErrorConexionRedis(
                "Se interrumpió la suscripción Pub/Sub con Redis."
            ) from exc
        finally:
            try:
                pubsub.unsubscribe(canal)
            except Exception:
                pass
            try:
                pubsub.close()
            except Exception:
                pass

    def suscribir_eventos_votacion(
        self,
        id_votacion: str,
        intervalo_latido_segundos: float = 15.0,
    ) -> Iterator[Dict[str, Any]]:
        """Suscribe un cliente SSE al canal individual de una votación."""
        yield from self._suscribir_canal(
            canal=self.canal_eventos_votacion(id_votacion),
            intervalo_latido_segundos=intervalo_latido_segundos,
        )

    def suscribir_eventos_globales(
        self,
        intervalo_latido_segundos: float = 15.0,
    ) -> Iterator[Dict[str, Any]]:
        """Suscribe un cliente SSE al canal global de listados de votaciones."""
        yield from self._suscribir_canal(
            canal=self.canal_global_votaciones(),
            intervalo_latido_segundos=intervalo_latido_segundos,
        )
