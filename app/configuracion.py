"""
Módulo de configuración centralizada de la aplicación.

Carga las variables de entorno desde `.env` utilizando `python-dotenv`,
valida la presencia de las configuraciones críticas de seguridad y define
los parámetros de cookies de sesión y conexión a Redis.
"""

import os
from typing import Any, Dict, Optional
from dotenv import load_dotenv

# Cargar variables desde el archivo .env si existe en el directorio raíz
load_dotenv()

VALORES_INSEGUROS_POR_DEFECTO = {
    "",
    "cambiar-por-una-clave-segura",
    "cambiar-por-una-clave-secreta-larga-y-aleatoria",
    "cambiar-por-un-codigo-privado",
}


class ErrorConfiguracion(ValueError):
    """Excepción lanzada cuando falta o es inválida una configuración obligatoria."""


class Configuracion:
    """
    Clase que agrupa y valida los parámetros de configuración de la aplicación.
    """

    def __init__(self, sobrecargas: Optional[Dict[str, Any]] = None) -> None:
        sobrecargas = sobrecargas or {}

        self.ENTORNO: str = str(
            sobrecargas.get("ENTORNO", os.getenv("ENTORNO", "desarrollo"))
        ).strip().lower()

        self.SECRET_KEY: str = str(
            sobrecargas.get(
                "SECRET_KEY",
                sobrecargas.get("FLASK_CLAVE_SECRETA", os.getenv("FLASK_CLAVE_SECRETA", "")),
            )
        ).strip()

        self.CODIGO_INVITACION_ADMIN: str = str(
            sobrecargas.get(
                "CODIGO_INVITACION_ADMIN",
                os.getenv("CODIGO_INVITACION_ADMIN", ""),
            )
        ).strip()

        self.REDIS_URL: str = str(
            sobrecargas.get("REDIS_URL", os.getenv("REDIS_URL", ""))
        ).strip()

        self.PREFIJO_REDIS: str = str(
            sobrecargas.get("PREFIJO_REDIS", os.getenv("PREFIJO_REDIS", "votacion"))
        ).strip()

        # En producción el modo de depuración siempre se desactiva por seguridad
        depuracion_env = str(
            sobrecargas.get("FLASK_DEPURACION", os.getenv("FLASK_DEPURACION", "false"))
        ).strip().lower()
        es_produccion = self.ENTORNO == "produccion"
        self.DEBUG: bool = False if es_produccion else depuracion_env in ("true", "1", "si", "yes")
        self.TESTING: bool = bool(sobrecargas.get("TESTING", self.ENTORNO == "pruebas"))

        # Límites de intentos de inicio de sesión respaldados en Redis
        self.MAX_INTENTOS_LOGIN: int = int(
            sobrecargas.get("MAX_INTENTOS_LOGIN", os.getenv("MAX_INTENTOS_LOGIN", "5"))
        )
        self.VENTANA_BLOQUEO_LOGIN_SEGUNDOS: int = int(
            sobrecargas.get(
                "VENTANA_BLOQUEO_LOGIN_SEGUNDOS",
                os.getenv("VENTANA_BLOQUEO_LOGIN_SEGUNDOS", "300"),
            )
        )

        # Configuración segura de cookies de sesión
        self.SESSION_COOKIE_NAME: str = "votacion_sesion"
        self.SESSION_COOKIE_HTTPONLY: bool = True
        self.SESSION_COOKIE_SAMESITE: str = "Lax"
        cookie_segura_env = str(
            sobrecargas.get("SESSION_COOKIE_SECURE", os.getenv("COOKIE_SEGURA", ""))
        ).strip().lower()
        if cookie_segura_env in ("true", "1", "si", "yes"):
            self.SESSION_COOKIE_SECURE: bool = True
        elif cookie_segura_env in ("false", "0", "no"):
            self.SESSION_COOKIE_SECURE = False
        else:
            self.SESSION_COOKIE_SECURE = es_produccion

        self.validar()

    def validar(self) -> None:
        """
        Verifica que todas las variables obligatorias estén definidas y no usen
        valores de plantilla inseguros. Nunca incluye el valor de los secretos
        en los mensajes de error.
        """
        if not self.SECRET_KEY or self.SECRET_KEY in VALORES_INSEGUROS_POR_DEFECTO:
            raise ErrorConfiguracion(
                "Configuración inválida: 'FLASK_CLAVE_SECRETA' es obligatoria y no puede "
                "usar el valor de ejemplo por defecto. Revise su archivo .env."
            )

        if len(self.SECRET_KEY) < 16:
            raise ErrorConfiguracion(
                "Configuración inválida: 'FLASK_CLAVE_SECRETA' debe tener al menos 16 caracteres."
            )

        if (
            not self.CODIGO_INVITACION_ADMIN
            or self.CODIGO_INVITACION_ADMIN in VALORES_INSEGUROS_POR_DEFECTO
        ):
            raise ErrorConfiguracion(
                "Configuración inválida: 'CODIGO_INVITACION_ADMIN' es obligatorio y no puede "
                "usar el valor de ejemplo por defecto. Revise su archivo .env."
            )

        if not self.REDIS_URL:
            raise ErrorConfiguracion(
                "Configuración inválida: 'REDIS_URL' es obligatoria para conectar con Redis."
            )

        if not self.PREFIJO_REDIS or ":" in self.PREFIJO_REDIS or " " in self.PREFIJO_REDIS:
            raise ErrorConfiguracion(
                "Configuración inválida: 'PREFIJO_REDIS' debe ser un identificador no vacío "
                "sin espacios ni dos puntos (':')."
            )

    def a_diccionario_flask(self) -> Dict[str, Any]:
        """Devuelve un diccionario compatible con `app.config.from_mapping`."""
        return {
            "ENTORNO": self.ENTORNO,
            "SECRET_KEY": self.SECRET_KEY,
            "CODIGO_INVITACION_ADMIN": self.CODIGO_INVITACION_ADMIN,
            "REDIS_URL": self.REDIS_URL,
            "PREFIJO_REDIS": self.PREFIJO_REDIS,
            "DEBUG": self.DEBUG,
            "TESTING": self.TESTING,
            "MAX_INTENTOS_LOGIN": self.MAX_INTENTOS_LOGIN,
            "VENTANA_BLOQUEO_LOGIN_SEGUNDOS": self.VENTANA_BLOQUEO_LOGIN_SEGUNDOS,
            "SESSION_COOKIE_NAME": self.SESSION_COOKIE_NAME,
            "SESSION_COOKIE_HTTPONLY": self.SESSION_COOKIE_HTTPONLY,
            "SESSION_COOKIE_SAMESITE": self.SESSION_COOKIE_SAMESITE,
            "SESSION_COOKIE_SECURE": self.SESSION_COOKIE_SECURE,
        }
