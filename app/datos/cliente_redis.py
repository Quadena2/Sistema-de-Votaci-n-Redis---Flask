"""
Módulo de conexión centralizada a Redis.

Solo la capa de datos puede importar y utilizar este módulo. Gestiona la
instancia del cliente Redis, la construcción consistente de claves con el
prefijo configurable y la carga de scripts Lua desde el directorio `scripts/`.
"""

from pathlib import Path
from typing import Dict, Optional
import redis
from redis.commands.core import Script
from redis.exceptions import RedisError


class ErrorConexionRedis(Exception):
    """Excepción lanzada cuando Redis no está disponible o falla una operación de red."""


class GestorRedis:
    """
    Encapsula el cliente de Redis, el prefijo de claves de la aplicación y
    el registro de scripts Lua.
    """

    def __init__(
        self,
        redis_url: str,
        prefijo: str = "votacion",
        cliente_personalizado: Optional[redis.Redis] = None,
    ) -> None:
        self.redis_url: str = redis_url
        self.prefijo: str = prefijo.strip()
        self._cliente: Optional[redis.Redis] = cliente_personalizado
        self._scripts: Dict[str, Script] = {}
        self._directorio_scripts: Path = Path(__file__).resolve().parent / "scripts"

    def obtener_cliente(self) -> redis.Redis:
        """
        Devuelve la instancia del cliente Redis configurada con `decode_responses=True`
        para trabajar directamente con cadenas de texto en Python.
        """
        if self._cliente is None:
            try:
                self._cliente = redis.Redis.from_url(
                    self.redis_url,
                    decode_responses=True,
                    socket_connect_timeout=3,
                    socket_timeout=5,
                )
            except RedisError as exc:
                raise ErrorConexionRedis(
                    "No fue posible inicializar el cliente de conexión a Redis."
                ) from exc
        return self._cliente

    def crear_cliente_pubsub(self) -> redis.client.PubSub:
        """
        Crea un objeto PubSub dedicado para escuchar eventos en tiempo real.
        Utiliza una conexión sin tiempo de espera de lectura corto para mantener
        abierta la suscripción SSE, permitiendo lecturas con timeout controlado.
        """
        try:
            cliente_eventos = redis.Redis.from_url(
                self.redis_url,
                decode_responses=True,
                socket_connect_timeout=3,
            )
            return cliente_eventos.pubsub(ignore_subscribe_messages=True)
        except RedisError as exc:
            raise ErrorConexionRedis(
                "No fue posible abrir el canal Pub/Sub con Redis."
            ) from exc

    def construir_clave(self, *partes: str) -> str:
        """
        Construye una clave jerárquica de Redis precedida por el prefijo configurado.
        Ejemplo: `construir_clave("votacion", id_votacion, "datos")`
        produce `votacion:votacion:<id>:datos` o según los segmentos indicados.
        """
        segmentos = [self.prefijo] + [str(parte).strip(":") for parte in partes if parte]
        return ":".join(segmentos)

    def comprobar_conexion(self) -> bool:
        """
        Ejecuta `PING` contra el servidor Redis para comprobar que está activo.
        Lanza `ErrorConexionRedis` si la comunicación falla.
        """
        try:
            return bool(self.obtener_cliente().ping())
        except RedisError as exc:
            raise ErrorConexionRedis(
                "No se pudo establecer comunicación con el servidor Redis."
            ) from exc

    def obtener_script_lua(self, nombre_archivo: str) -> Script:
        """
        Lee y registra un script Lua ubicado en `app/datos/scripts/`.
        Cachea el objeto `Script` para reutilizar `EVALSHA` automáticamente.
        """
        if nombre_archivo not in self._scripts:
            ruta = self._directorio_scripts / nombre_archivo
            if not ruta.exists():
                raise FileNotFoundError(f"No se encontró el script Lua: {ruta}")
            contenido_lua = ruta.read_text(encoding="utf-8")
            cliente = self.obtener_cliente()
            self._scripts[nombre_archivo] = cliente.register_script(contenido_lua)
        return self._scripts[nombre_archivo]
