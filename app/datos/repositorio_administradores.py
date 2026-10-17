"""
Repositorio de datos para la gestión de cuentas de administradores en Redis.

Estructuras Redis utilizadas:
- `<prefijo>:administradores:indice` (Set): contiene los IDs de todos los administradores.
- `<prefijo>:administrador:<id>:datos` (Hash): almacena `id`, `nombre_usuario`,
  `nombre_normalizado`, `hash_contrasena` y `fecha_creacion`.
- `<prefijo>:administradores:usuario:<nombre_normalizado>` (String): índice secundario
  que asocia el nombre de usuario en minúsculas con el `<id>` único del administrador.
- `<prefijo>:seguridad:intentos_login:<clave_cliente>` (String con TTL): contador de
  intentos fallidos de inicio de sesión para protección básica contra fuerza bruta.
"""

from typing import Dict, Optional, Tuple
from redis.exceptions import RedisError
from app.datos.cliente_redis import ErrorConexionRedis, GestorRedis


class RepositorioAdministradores:
    """Gestiona la persistencia de administradores y control de intentos en Redis."""

    def __init__(self, gestor_redis: GestorRedis) -> None:
        self.gestor = gestor_redis

    # -------------------------------------------------------------------------
    # Constructores de claves Redis
    # -------------------------------------------------------------------------

    def clave_indice_administradores(self) -> str:
        """Clave del Set con todos los identificadores de administradores."""
        return self.gestor.construir_clave("administradores", "indice")

    def clave_datos_administrador(self, id_admin: str) -> str:
        """Clave del Hash con los datos de un administrador específico."""
        return self.gestor.construir_clave("administrador", id_admin, "datos")

    def clave_usuario_normalizado(self, nombre_normalizado: str) -> str:
        """Clave que asocia un nombre de usuario normalizado con su ID."""
        return self.gestor.construir_clave("administradores", "usuario", nombre_normalizado)

    def clave_intentos_login(self, identificador_cliente: str) -> str:
        """Clave temporal con TTL para contabilizar intentos fallidos de inicio de sesión."""
        return self.gestor.construir_clave("seguridad", "intentos_login", identificador_cliente)

    # -------------------------------------------------------------------------
    # Operaciones de escritura y lectura de administradores
    # -------------------------------------------------------------------------

    def registrar_administrador(
        self,
        id_admin: str,
        nombre_usuario: str,
        nombre_normalizado: str,
        hash_contrasena: str,
        fecha_creacion: str,
    ) -> Tuple[bool, str]:
        """
        Registra atómicamente un nuevo administrador ejecutando `registrar_administrador.lua`.

        Devuelve `(True, "ADMINISTRADOR_REGISTRADO")` o `(False, "USUARIO_DUPLICADO")`.
        """
        try:
            script = self.gestor.obtener_script_lua("registrar_administrador.lua")
            resultado = script(
                keys=[
                    self.clave_usuario_normalizado(nombre_normalizado),
                    self.clave_datos_administrador(id_admin),
                    self.clave_indice_administradores(),
                ],
                args=[
                    id_admin,
                    nombre_usuario,
                    nombre_normalizado,
                    hash_contrasena,
                    fecha_creacion,
                ],
            )
            exito = int(resultado[0]) == 1
            codigo = str(resultado[1])
            return exito, codigo
        except RedisError as exc:
            raise ErrorConexionRedis(
                "Error de comunicación con Redis al registrar el administrador."
            ) from exc

    def obtener_id_por_nombre_normalizado(self, nombre_normalizado: str) -> Optional[str]:
        """Obtiene el ID interno del administrador a partir de su nombre normalizado."""
        try:
            cliente = self.gestor.obtener_cliente()
            id_admin = cliente.get(self.clave_usuario_normalizado(nombre_normalizado))
            return str(id_admin) if id_admin else None
        except RedisError as exc:
            raise ErrorConexionRedis(
                "Error al consultar el índice de usuario en Redis."
            ) from exc

    def obtener_por_id(self, id_admin: str) -> Optional[Dict[str, str]]:
        """
        Recupera el Hash completo de un administrador usando `SISMEMBER` y `HGETALL`.
        Devuelve `None` si no existe.
        """
        if not id_admin:
            return None
        try:
            cliente = self.gestor.obtener_cliente()
            pertenece = cliente.sismember(self.clave_indice_administradores(), id_admin)
            if not pertenece:
                return None
            datos = cliente.hgetall(self.clave_datos_administrador(id_admin))
            return dict(datos) if datos else None
        except RedisError as exc:
            raise ErrorConexionRedis(
                "Error al recuperar los datos del administrador desde Redis."
            ) from exc

    def obtener_por_nombre_normalizado(self, nombre_normalizado: str) -> Optional[Dict[str, str]]:
        """Busca un administrador por su nombre de usuario normalizado."""
        id_admin = self.obtener_id_por_nombre_normalizado(nombre_normalizado)
        if not id_admin:
            return None
        return self.obtener_por_id(id_admin)

    def existe_administrador(self, id_admin: str) -> bool:
        """
        Verifica si el administrador está en el índice (`SISMEMBER`) y tiene
        su campo `id` en el Hash (`HEXISTS`).
        """
        if not id_admin:
            return False
        try:
            cliente = self.gestor.obtener_cliente()
            en_indice = bool(cliente.sismember(self.clave_indice_administradores(), id_admin))
            tiene_hash = bool(cliente.hexists(self.clave_datos_administrador(id_admin), "id"))
            return en_indice and tiene_hash
        except RedisError as exc:
            raise ErrorConexionRedis(
                "Error al verificar la existencia del administrador en Redis."
            ) from exc

    def contar_administradores(self) -> int:
        """Devuelve el número total de administradores registrados usando `SCARD`."""
        try:
            cliente = self.gestor.obtener_cliente()
            return int(cliente.scard(self.clave_indice_administradores()))
        except RedisError as exc:
            raise ErrorConexionRedis(
                "Error al contar los administradores registrados en Redis."
            ) from exc

    # -------------------------------------------------------------------------
    # Control de intentos de inicio de sesión en Redis
    # -------------------------------------------------------------------------

    def obtener_intentos_fallidos(self, identificador_cliente: str) -> Tuple[int, int]:
        """
        Consulta el número de intentos fallidos y el tiempo restante de bloqueo (TTL).
        """
        try:
            cliente = self.gestor.obtener_cliente()
            clave = self.clave_intentos_login(identificador_cliente)
            valor = cliente.get(clave)
            intentos = int(valor) if valor is not None else 0
            ttl = int(cliente.ttl(clave)) if intentos > 0 else 0
            return intentos, max(ttl, 0)
        except RedisError as exc:
            raise ErrorConexionRedis(
                "Error al consultar el límite de intentos de inicio de sesión en Redis."
            ) from exc

    def registrar_intento_fallido(
        self, identificador_cliente: str, ventana_segundos: int
    ) -> int:
        """
        Incrementa el contador de intentos fallidos (`INCR`) y asigna un tiempo
        de expiración (`EXPIRE`) cuando se inicia la ventana.
        """
        try:
            cliente = self.gestor.obtener_cliente()
            clave = self.clave_intentos_login(identificador_cliente)
            total = int(cliente.incr(clave))
            if total == 1 or int(cliente.ttl(clave)) < 0:
                cliente.expire(clave, ventana_segundos)
            return total
        except RedisError as exc:
            raise ErrorConexionRedis(
                "Error al registrar intento fallido de inicio de sesión en Redis."
            ) from exc

    def limpiar_intentos_fallidos(self, identificador_cliente: str) -> None:
        """Elimina el contador de intentos fallidos tras un inicio de sesión exitoso."""
        try:
            cliente = self.gestor.obtener_cliente()
            cliente.delete(self.clave_intentos_login(identificador_cliente))
        except RedisError as exc:
            raise ErrorConexionRedis(
                "Error al reiniciar el contador de intentos en Redis."
            ) from exc
