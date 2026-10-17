"""
Pruebas de autenticación, registro de administradores, protección de contraseñas,
control de sesiones y protección CSRF.
"""

from typing import Dict, Optional, Tuple
import pytest
from flask.testing import FlaskClient

from app.configuracion import Configuracion, ErrorConfiguracion
from app.datos.repositorio_administradores import RepositorioAdministradores
from app.negocio.excepciones import (
    ErrorAutenticacion,
    ErrorLimiteIntentos,
    ErrorValidacion,
)
from app.negocio.servicio_autenticacion import ServicioAutenticacion
from pruebas.conftest import CODIGO_INVITACION_PRUEBA, extraer_csrf_html


# =============================================================================
# Doble en memoria para pruebas unitarias puras (sin Redis real)
# =============================================================================


class RepositorioAdministradoresFalso:
    """Doble de prueba ligero para verificar la lógica unitaria de ServicioAutenticacion."""

    def __init__(self) -> None:
        self.por_id: Dict[str, Dict[str, str]] = {}
        self.por_nombre: Dict[str, str] = {}
        self.intentos: Dict[str, int] = {}

    def registrar_administrador(
        self,
        id_admin: str,
        nombre_usuario: str,
        nombre_normalizado: str,
        hash_contrasena: str,
        fecha_creacion: str,
    ) -> Tuple[bool, str]:
        if nombre_normalizado in self.por_nombre:
            return False, "USUARIO_DUPLICADO"
        self.por_nombre[nombre_normalizado] = id_admin
        self.por_id[id_admin] = {
            "id": id_admin,
            "nombre_usuario": nombre_usuario,
            "nombre_normalizado": nombre_normalizado,
            "hash_contrasena": hash_contrasena,
            "fecha_creacion": fecha_creacion,
        }
        return True, "ADMINISTRADOR_REGISTRADO"

    def obtener_por_nombre_normalizado(self, nombre_normalizado: str) -> Optional[Dict[str, str]]:
        id_admin = self.por_nombre.get(nombre_normalizado)
        return self.por_id.get(id_admin) if id_admin else None

    def obtener_por_id(self, id_admin: str) -> Optional[Dict[str, str]]:
        return self.por_id.get(id_admin)

    def obtener_intentos_fallidos(self, identificador_cliente: str) -> Tuple[int, int]:
        return self.intentos.get(identificador_cliente, 0), 60

    def registrar_intento_fallido(self, identificador_cliente: str, ventana_segundos: int) -> int:
        self.intentos[identificador_cliente] = self.intentos.get(identificador_cliente, 0) + 1
        return self.intentos[identificador_cliente]

    def limpiar_intentos_fallidos(self, identificador_cliente: str) -> None:
        self.intentos.pop(identificador_cliente, None)


# =============================================================================
# 1. Pruebas unitarias de configuración y validaciones de autenticación
# =============================================================================


@pytest.mark.unitaria
def test_configuracion_rechaza_secretos_por_defecto() -> None:
    """Verifica que Configuracion rechace valores inseguros o vacíos."""
    with pytest.raises(ErrorConfiguracion):
        Configuracion(
            {
                "FLASK_CLAVE_SECRETA": "cambiar-por-una-clave-segura",
                "CODIGO_INVITACION_ADMIN": "codigo-valido-123",
                "REDIS_URL": "redis://localhost:6379/0",
            }
        )


@pytest.mark.unitaria
def test_unitario_validaciones_registro_y_login() -> None:
    """Prueba las validaciones de formato, código de invitación y contraseñas sin Redis."""
    repo_falso = RepositorioAdministradoresFalso()
    servicio = ServicioAutenticacion(
        repositorio_administradores=repo_falso,  # type: ignore[arg-type]
        codigo_invitacion_esperado="INVITACION-SECRETA",
        max_intentos_login=3,
    )

    # Rechazo de código de invitación incorrecto
    with pytest.raises(ErrorAutenticacion) as exc_codigo:
        servicio.registrar_administrador(
            nombre_usuario="profesor_db",
            contrasena="ContrasenaSegura123",
            confirmacion_contrasena="ContrasenaSegura123",
            codigo_invitacion="CODIGO-ERRONEO",
        )
    assert exc_codigo.value.codigo == "CODIGO_INVITACION_INVALIDO"

    # Rechazo de contraseñas que no coinciden
    with pytest.raises(ErrorValidacion) as exc_no_coinciden:
        servicio.registrar_administrador(
            nombre_usuario="profesor_db",
            contrasena="ContrasenaSegura123",
            confirmacion_contrasena="OtraContrasena999",
            codigo_invitacion="INVITACION-SECRETA",
        )
    assert exc_no_coinciden.value.codigo == "CONTRASENAS_NO_COINCIDEN"

    # Registro válido
    creado = servicio.registrar_administrador(
        nombre_usuario="Profesor_DB",
        contrasena="ContrasenaSegura123",
        confirmacion_contrasena="ContrasenaSegura123",
        codigo_invitacion="INVITACION-SECRETA",
    )
    assert creado["nombre_usuario"] == "Profesor_DB"
    assert creado["nombre_normalizado"] == "profesor_db"
    assert "hash_contrasena" not in creado


# =============================================================================
# 2. Pruebas con Redis real y rutas Flask
# =============================================================================


@pytest.mark.integracion_redis
def test_1_registro_valido_administrador(
    servicio_autenticacion: ServicioAutenticacion,
    repo_administradores: RepositorioAdministradores,
) -> None:
    """1. Verifica el registro válido de un administrador en Redis."""
    admin = servicio_autenticacion.registrar_administrador(
        nombre_usuario="Admin_Academico",
        contrasena="ClaveFuerte2026!",
        confirmacion_contrasena="ClaveFuerte2026!",
        codigo_invitacion=CODIGO_INVITACION_PRUEBA,
    )

    assert admin["id"]
    assert admin["nombre_usuario"] == "Admin_Academico"
    assert admin["nombre_normalizado"] == "admin_academico"
    assert repo_administradores.existe_administrador(admin["id"]) is True
    assert repo_administradores.contar_administradores() == 1


@pytest.mark.integracion_redis
def test_2_rechazo_codigo_invitacion_incorrecto(
    servicio_autenticacion: ServicioAutenticacion,
    repo_administradores: RepositorioAdministradores,
) -> None:
    """2. Verifica que un código de invitación incorrecto sea rechazado y no cree datos."""
    with pytest.raises(ErrorAutenticacion) as info:
        servicio_autenticacion.registrar_administrador(
            nombre_usuario="intruso",
            contrasena="ClaveFuerte2026!",
            confirmacion_contrasena="ClaveFuerte2026!",
            codigo_invitacion="CODIGO-FALSO",
        )
    assert info.value.codigo == "CODIGO_INVITACION_INVALIDO"
    assert repo_administradores.contar_administradores() == 0


@pytest.mark.integracion_redis
def test_3_rechazo_nombre_usuario_duplicado(
    servicio_autenticacion: ServicioAutenticacion,
) -> None:
    """3. Verifica que el script Lua rechace nombres de usuario duplicados (incluso con mayúsculas distintas)."""
    servicio_autenticacion.registrar_administrador(
        nombre_usuario="Coordinador",
        contrasena="ClaveFuerte2026!",
        confirmacion_contrasena="ClaveFuerte2026!",
        codigo_invitacion=CODIGO_INVITACION_PRUEBA,
    )

    with pytest.raises(ErrorValidacion) as info:
        servicio_autenticacion.registrar_administrador(
            nombre_usuario="coordinador",
            contrasena="OtraClaveFuerte2026!",
            confirmacion_contrasena="OtraClaveFuerte2026!",
            codigo_invitacion=CODIGO_INVITACION_PRUEBA,
        )
    assert info.value.codigo == "USUARIO_DUPLICADO"


@pytest.mark.integracion_redis
def test_4_rechazo_contrasenas_no_coinciden_o_cortas(
    servicio_autenticacion: ServicioAutenticacion,
) -> None:
    """4. Verifica el rechazo cuando las contraseñas no coinciden o son demasiado cortas."""
    with pytest.raises(ErrorValidacion) as info_distintas:
        servicio_autenticacion.registrar_administrador(
            nombre_usuario="admin_prueba",
            contrasena="ClaveSegura123",
            confirmacion_contrasena="ClaveDistinta456",
            codigo_invitacion=CODIGO_INVITACION_PRUEBA,
        )
    assert info_distintas.value.codigo == "CONTRASENAS_NO_COINCIDEN"

    with pytest.raises(ErrorValidacion) as info_corta:
        servicio_autenticacion.registrar_administrador(
            nombre_usuario="admin_prueba",
            contrasena="12345",
            confirmacion_contrasena="12345",
            codigo_invitacion=CODIGO_INVITACION_PRUEBA,
        )
    assert info_corta.value.codigo == "CONTRASENA_MUY_CORTA"


@pytest.mark.integracion_redis
def test_5_contrasenas_nunca_en_texto_plano(
    servicio_autenticacion: ServicioAutenticacion,
    repo_administradores: RepositorioAdministradores,
) -> None:
    """5. Verifica directamente en el Hash de Redis que la contraseña no se guarde en texto plano."""
    contrasena_plana = "MiSecretoAcademico2026$"
    admin = servicio_autenticacion.registrar_administrador(
        nombre_usuario="auditor_seguridad",
        contrasena=contrasena_plana,
        confirmacion_contrasena=contrasena_plana,
        codigo_invitacion=CODIGO_INVITACION_PRUEBA,
    )

    datos_crudos = repo_administradores.obtener_por_id(admin["id"])
    assert datos_crudos is not None
    assert "contrasena" not in datos_crudos
    hash_guardado = datos_crudos.get("hash_contrasena", "")
    assert hash_guardado != ""
    assert hash_guardado != contrasena_plana
    assert contrasena_plana not in hash_guardado
    assert hash_guardado.startswith(("scrypt:", "pbkdf2:"))


@pytest.mark.integracion_redis
def test_6_y_7_inicio_sesion_valido_e_invalido_y_limite_intentos(
    servicio_autenticacion: ServicioAutenticacion,
) -> None:
    """6 y 7. Verifica inicio de sesión válido, rechazo de credenciales e imposición de límite en Redis."""
    servicio_autenticacion.registrar_administrador(
        nombre_usuario="docente_redis",
        contrasena="PasswordCorrecto2026",
        confirmacion_contrasena="PasswordCorrecto2026",
        codigo_invitacion=CODIGO_INVITACION_PRUEBA,
    )

    # Credenciales válidas
    autenticado = servicio_autenticacion.autenticar_administrador(
        nombre_usuario="DOCENTE_REDIS",
        contrasena="PasswordCorrecto2026",
        identificador_cliente="127.0.0.1",
    )
    assert autenticado["nombre_usuario"] == "docente_redis"
    assert "hash_contrasena" not in autenticado

    # Credenciales incorrectas (contraseña errónea y usuario inexistente dan el mismo mensaje genérico)
    with pytest.raises(ErrorAutenticacion) as err_pass:
        servicio_autenticacion.autenticar_administrador(
            nombre_usuario="docente_redis",
            contrasena="PasswordIncorrecto",
            identificador_cliente="10.0.0.5",
        )
    with pytest.raises(ErrorAutenticacion) as err_user:
        servicio_autenticacion.autenticar_administrador(
            nombre_usuario="usuario_que_no_existe",
            contrasena="PasswordIncorrecto",
            identificador_cliente="10.0.0.6",
        )
    assert err_pass.value.mensaje == err_user.value.mensaje

    # Superar el límite de intentos fallidos (configurado en 4 para el fixture)
    for _ in range(3):
        with pytest.raises(ErrorAutenticacion):
            servicio_autenticacion.autenticar_administrador(
                nombre_usuario="docente_redis",
                contrasena="PasswordIncorrecto",
                identificador_cliente="10.0.0.5",
            )

    with pytest.raises(ErrorLimiteIntentos):
        servicio_autenticacion.autenticar_administrador(
            nombre_usuario="docente_redis",
            contrasena="PasswordCorrecto2026",
            identificador_cliente="10.0.0.5",
        )


@pytest.mark.integracion_redis
def test_8_9_10_acceso_panel_cierre_sesion_y_csrf(cliente_http: FlaskClient) -> None:
    """
    8. Acceso denegado al panel sin autenticación.
    9. Cierre correcto de sesión.
    10. Protección CSRF en operaciones POST.
    """
    # 8. Sin iniciar sesión, el acceso a /administracion redirige al login
    respuesta_panel = cliente_http.get("/administracion", follow_redirects=False)
    assert respuesta_panel.status_code in (302, 303)
    assert "/administracion/ingresar" in respuesta_panel.headers["Location"]

    # 10. Intento de registro POST sin token CSRF debe ser rechazado con 400
    respuesta_sin_csrf = cliente_http.post(
        "/administracion/registro",
        data={
            "nombre_usuario": "admin_csrf",
            "contrasena": "PasswordSegura123",
            "confirmacion_contrasena": "PasswordSegura123",
            "codigo_invitacion": CODIGO_INVITACION_PRUEBA,
        },
    )
    assert respuesta_sin_csrf.status_code == 400

    # Obtener token CSRF legítimo del formulario de registro
    pagina_registro = cliente_http.get("/administracion/registro")
    csrf = extraer_csrf_html(pagina_registro.get_data(as_text=True))

    # Registro exitoso vía HTTP POST con CSRF
    respuesta_registro = cliente_http.post(
        "/administracion/registro",
        data={
            "csrf_token": csrf,
            "nombre_usuario": "admin_web",
            "contrasena": "PasswordSegura123",
            "confirmacion_contrasena": "PasswordSegura123",
            "codigo_invitacion": CODIGO_INVITACION_PRUEBA,
        },
        follow_redirects=True,
    )
    assert respuesta_registro.status_code == 200
    html_panel = respuesta_registro.get_data(as_text=True)
    assert "Panel de Administración" in html_panel
    assert "admin_web" in html_panel

    # 9. Cerrar sesión con CSRF
    csrf_panel = extraer_csrf_html(html_panel)
    respuesta_salir = cliente_http.post(
        "/administracion/salir",
        data={"csrf_token": csrf_panel},
        follow_redirects=False,
    )
    assert respuesta_salir.status_code in (302, 303)

    # Verificar que tras cerrar sesión ya no puede entrar al panel administrativo
    respuesta_post_salir = cliente_http.get("/administracion", follow_redirects=False)
    assert respuesta_post_salir.status_code in (302, 303)
    assert "/administracion/ingresar" in respuesta_post_salir.headers["Location"]
