"""
Servicio de negocio para el registro, autenticación y verificación de
administradores.

Implementa:
- Validación de formato y políticas de contraseña.
- Comparación en tiempo constante del código de invitación (`hmac.compare_digest`).
- Hash seguro de contraseñas mediante `Werkzeug` (`scrypt`).
- Prevención de enumeración de usuarios en el inicio de sesión.
- Control de límite de intentos fallidos respaldado en Redis.
"""

from datetime import datetime, timezone
import hmac
import re
import uuid
from typing import Dict, Optional
from werkzeug.security import check_password_hash, generate_password_hash

from app.datos.cliente_redis import ErrorConexionRedis
from app.datos.repositorio_administradores import RepositorioAdministradores
from app.negocio.excepciones import (
    ErrorAlmacenamientoNoDisponible,
    ErrorAutenticacion,
    ErrorLimiteIntentos,
    ErrorValidacion,
)

PATRON_NOMBRE_USUARIO = re.compile(r"^[a-zA-Z0-9_.-]{3,30}$")
LONGITUD_MINIMA_CONTRASENA = 8
LONGITUD_MAXIMA_CONTRASENA = 128

# Hash señuelo para mitigar ataques de temporización cuando el usuario no existe
HASH_SENUELO = generate_password_hash("contrasena_senuelo_tiempo_constante_2026")


class ServicioAutenticacion:
    """Coordina la lógica de negocio para cuentas administrativas."""

    def __init__(
        self,
        repositorio_administradores: RepositorioAdministradores,
        codigo_invitacion_esperado: str,
        max_intentos_login: int = 5,
        ventana_bloqueo_segundos: int = 300,
    ) -> None:
        self.repositorio = repositorio_administradores
        self.codigo_invitacion_esperado = codigo_invitacion_esperado
        self.max_intentos_login = max_intentos_login
        self.ventana_bloqueo_segundos = ventana_bloqueo_segundos

    @staticmethod
    def normalizar_nombre_usuario(nombre_usuario: str) -> str:
        """Normaliza el nombre de usuario eliminando espacios extremos y pasando a minúsculas."""
        return (nombre_usuario or "").strip().lower()

    @staticmethod
    def _sanitizar_administrador(datos: Dict[str, str]) -> Dict[str, str]:
        """
        Devuelve únicamente los atributos públicos del administrador.
        Garantiza que el `hash_contrasena` nunca salga de la capa de negocio.
        """
        return {
            "id": datos.get("id", ""),
            "nombre_usuario": datos.get("nombre_usuario", ""),
            "nombre_normalizado": datos.get("nombre_normalizado", ""),
            "fecha_creacion": datos.get("fecha_creacion", ""),
        }

    def registrar_administrador(
        self,
        nombre_usuario: str,
        contrasena: str,
        confirmacion_contrasena: str,
        codigo_invitacion: str,
    ) -> Dict[str, str]:
        """
        Valida los datos del formulario y registra atómicamente un nuevo administrador.
        """
        nombre_limpio = (nombre_usuario or "").strip()
        nombre_normalizado = self.normalizar_nombre_usuario(nombre_limpio)
        contrasena = contrasena or ""
        confirmacion_contrasena = confirmacion_contrasena or ""
        codigo_limpio = (codigo_invitacion or "").strip()

        if not nombre_limpio or not contrasena or not confirmacion_contrasena or not codigo_limpio:
            raise ErrorValidacion(
                "Todos los campos son obligatorios para registrar un administrador.",
                "CAMPOS_INCOMPLETOS",
            )

        if not PATRON_NOMBRE_USUARIO.match(nombre_limpio):
            raise ErrorValidacion(
                "El nombre de usuario debe tener entre 3 y 30 caracteres y contener solo "
                "letras, números, guiones, puntos o guiones bajos.",
                "FORMATO_USUARIO_INVALIDO",
            )

        if len(contrasena) < LONGITUD_MINIMA_CONTRASENA:
            raise ErrorValidacion(
                f"La contraseña debe tener al menos {LONGITUD_MINIMA_CONTRASENA} caracteres.",
                "CONTRASENA_MUY_CORTA",
            )

        if len(contrasena) > LONGITUD_MAXIMA_CONTRASENA:
            raise ErrorValidacion(
                f"La contraseña no puede superar los {LONGITUD_MAXIMA_CONTRASENA} caracteres.",
                "CONTRASENA_MUY_LARGA",
            )

        if contrasena != confirmacion_contrasena:
            raise ErrorValidacion(
                "La contraseña y su confirmación no coinciden.",
                "CONTRASENAS_NO_COINCIDEN",
            )

        # Comparación en tiempo constante para proteger el código de invitación
        codigo_valido = hmac.compare_digest(
            codigo_limpio.encode("utf-8"),
            self.codigo_invitacion_esperado.encode("utf-8"),
        )
        if not codigo_valido:
            raise ErrorAutenticacion(
                "El código de invitación de administrador no es válido.",
                "CODIGO_INVITACION_INVALIDO",
            )

        id_admin = str(uuid.uuid4())
        hash_contrasena = generate_password_hash(contrasena)
        fecha_creacion = datetime.now(timezone.utc).isoformat()

        try:
            creado, codigo_resultado = self.repositorio.registrar_administrador(
                id_admin=id_admin,
                nombre_usuario=nombre_limpio,
                nombre_normalizado=nombre_normalizado,
                hash_contrasena=hash_contrasena,
                fecha_creacion=fecha_creacion,
            )
        except ErrorConexionRedis as exc:
            raise ErrorAlmacenamientoNoDisponible(str(exc)) from exc

        if not creado or codigo_resultado == "USUARIO_DUPLICADO":
            raise ErrorValidacion(
                "Ya existe una cuenta de administrador con ese nombre de usuario.",
                "USUARIO_DUPLICADO",
            )

        return {
            "id": id_admin,
            "nombre_usuario": nombre_limpio,
            "nombre_normalizado": nombre_normalizado,
            "fecha_creacion": fecha_creacion,
        }

    def autenticar_administrador(
        self,
        nombre_usuario: str,
        contrasena: str,
        identificador_cliente: str = "global",
    ) -> Dict[str, str]:
        """
        Verifica las credenciales de un administrador sin revelar si el nombre
        de usuario existe, aplicando límite de intentos fallidos en Redis.
        """
        nombre_normalizado = self.normalizar_nombre_usuario(nombre_usuario)
        contrasena = contrasena or ""

        if not nombre_normalizado or not contrasena:
            raise ErrorValidacion(
                "Debe ingresar su nombre de usuario y contraseña.",
                "CREDenciales_VACIAS",
            )

        clave_control = f"{identificador_cliente}:{nombre_normalizado}"

        try:
            intentos, ttl_restante = self.repositorio.obtener_intentos_fallidos(clave_control)
            if intentos >= self.max_intentos_login:
                segundos_espera = ttl_restante if ttl_restante > 0 else self.ventana_bloqueo_segundos
                raise ErrorLimiteIntentos(
                    f"Demasiados intentos fallidos de inicio de sesión. "
                    f"Intente nuevamente en {segundos_espera} segundos.",
                    "LIMITE_INTENTOS_EXCEDIDO",
                )

            datos_admin = self.repositorio.obtener_por_nombre_normalizado(nombre_normalizado)
        except ErrorConexionRedis as exc:
            raise ErrorAlmacenamientoNoDisponible(str(exc)) from exc

        if not datos_admin:
            # Ejecutar verificación ficticia para igualar tiempos de respuesta
            check_password_hash(HASH_SENUELO, contrasena)
            self._registrar_fallo_seguro(clave_control)
            raise ErrorAutenticacion(
                "Credenciales incorrectas. Verifique su nombre de usuario y contraseña.",
                "CREDENCIALES_INVALIDAS",
            )

        hash_almacenado = datos_admin.get("hash_contrasena", "")
        if not hash_almacenado or not check_password_hash(hash_almacenado, contrasena):
            self._registrar_fallo_seguro(clave_control)
            raise ErrorAutenticacion(
                "Credenciales incorrectas. Verifique su nombre de usuario y contraseña.",
                "CREDENCIALES_INVALIDAS",
            )

        try:
            self.repositorio.limpiar_intentos_fallidos(clave_control)
        except ErrorConexionRedis:
            pass

        return self._sanitizar_administrador(datos_admin)

    def _registrar_fallo_seguro(self, clave_control: str) -> None:
        """Incrementa el contador de intentos fallidos en Redis."""
        try:
            self.repositorio.registrar_intento_fallido(
                clave_control, self.ventana_bloqueo_segundos
            )
        except ErrorConexionRedis as exc:
            raise ErrorAlmacenamientoNoDisponible(str(exc)) from exc

    def obtener_administrador_por_id(self, id_admin: str) -> Optional[Dict[str, str]]:
        """
        Recupera los datos públicos de un administrador autenticado a partir de su ID.
        Devuelve `None` si la cuenta no existe en Redis.
        """
        if not id_admin:
            return None
        try:
            datos = self.repositorio.obtener_por_id(id_admin)
        except ErrorConexionRedis as exc:
            raise ErrorAlmacenamientoNoDisponible(str(exc)) from exc

        if not datos:
            return None
        return self._sanitizar_administrador(datos)
