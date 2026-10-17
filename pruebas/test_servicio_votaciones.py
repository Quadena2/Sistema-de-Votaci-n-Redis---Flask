"""
Pruebas del servicio de votaciones, validaciones de negocio, ciclo de vida,
prevención de duplicados, atomicidad concurrente del script Lua y Pub/Sub.
"""

from concurrent.futures import ThreadPoolExecutor
import time
from typing import List
import pytest

from app.datos.cliente_redis import GestorRedis
from app.datos.repositorio_votaciones import RepositorioVotaciones
from app.negocio.excepciones import (
    ErrorConflictoEstado,
    ErrorValidacion,
    ErrorVotoRechazado,
)
from app.negocio.servicio_votaciones import ServicioVotaciones


@pytest.mark.integracion_redis
def test_1_creacion_valida_votacion(servicio_votaciones: ServicioVotaciones) -> None:
    """1. Verifica la creación válida de una votación en estado inicial 'borrador'."""
    votacion = servicio_votaciones.crear_votacion(
        pregunta="¿Qué estructura de Redis se usa para rankings en tiempo real?",
        opciones=["Sorted Set (ZSET)", "Hash", "Set"],
        creador_id="admin-1",
        creador_usuario="profesor",
    )

    assert votacion["id"]
    assert votacion["estado"] == "borrador"
    assert votacion["total_votos"] == 0
    assert votacion["total_opciones"] == 3
    assert len(votacion["opciones"]) == 3
    assert votacion["fecha_creacion"] != ""
    assert votacion["fecha_apertura"] == ""
    assert votacion["fecha_cierre"] == ""


@pytest.mark.integracion_redis
def test_2_validacion_preguntas_y_opciones(servicio_votaciones: ServicioVotaciones) -> None:
    """2. Verifica validaciones de longitud de pregunta, cantidad de opciones (2 a 10) y duplicados."""
    # Pregunta demasiado corta
    with pytest.raises(ErrorValidacion) as err_pregunta:
        servicio_votaciones.crear_votacion(
            pregunta="¿Sí?",
            opciones=["Opción A", "Opción B"],
            creador_id="admin-1",
            creador_usuario="profesor",
        )
    assert err_pregunta.value.codigo == "PREGUNTA_MUY_CORTA"

    # Menos de 2 opciones válidas
    with pytest.raises(ErrorValidacion) as err_pocas:
        servicio_votaciones.crear_votacion(
            pregunta="Pregunta válida con una sola opción",
            opciones=["Única opción", "   "],
            creador_id="admin-1",
            creador_usuario="profesor",
        )
    assert err_pocas.value.codigo == "CANTIDAD_OPCIONES_INVALIDA"

    # Más de 10 opciones
    with pytest.raises(ErrorValidacion) as err_muchas:
        servicio_votaciones.crear_votacion(
            pregunta="Pregunta válida con demasiadas opciones",
            opciones=[f"Opción {i}" for i in range(1, 12)],
            creador_id="admin-1",
            creador_usuario="profesor",
        )
    assert err_muchas.value.codigo == "CANTIDAD_OPCIONES_INVALIDA"

    # Opciones duplicadas (insensible a mayúsculas/minúsculas)
    with pytest.raises(ErrorValidacion) as err_duplicadas:
        servicio_votaciones.crear_votacion(
            pregunta="¿Cuál lenguaje prefieres?",
            opciones=["Python", "python", "Rust"],
            creador_id="admin-1",
            creador_usuario="profesor",
        )
    assert err_duplicadas.value.codigo == "OPCIONES_DUPLICADAS"


@pytest.mark.integracion_redis
def test_3_apertura_cierre_y_prohibicion_reapertura(
    servicio_votaciones: ServicioVotaciones,
) -> None:
    """3. Verifica el ciclo borrador -> abierta -> cerrada e impide reabrir una cerrada."""
    creada = servicio_votaciones.crear_votacion(
        pregunta="¿Aprobar el calendario de exposiciones?",
        opciones=["A favor", "En contra"],
        creador_id="admin-1",
        creador_usuario="profesor",
    )
    id_votacion = creada["id"]

    # No se puede cerrar directamente desde borrador
    with pytest.raises(ErrorConflictoEstado) as err_cerrar_borrador:
        servicio_votaciones.cerrar_votacion(id_votacion, "admin-1")
    assert err_cerrar_borrador.value.codigo == "VOTACION_EN_BORRADOR"

    # Abrir votación
    abierta = servicio_votaciones.abrir_votacion(id_votacion, "admin-1")
    assert abierta["estado"] == "abierta"
    assert abierta["fecha_apertura"] != ""

    # Cerrar votación
    cerrada = servicio_votaciones.cerrar_votacion(id_votacion, "admin-1")
    assert cerrada["estado"] == "cerrada"
    assert cerrada["fecha_cierre"] != ""

    # Prohibido reabrir una votación cerrada
    with pytest.raises(ErrorConflictoEstado) as err_reabrir:
        servicio_votaciones.abrir_votacion(id_votacion, "admin-1")
    assert err_reabrir.value.codigo == "VOTACION_CERRADA_NO_REABRIBLE"


@pytest.mark.integracion_redis
def test_4_5_6_7_8_9_12_registro_votos_rechazos_y_consistencia(
    servicio_votaciones: ServicioVotaciones,
) -> None:
    """
    Prueba integral de:
    4. Registro de votos válidos.
    5. Rechazo de opción inexistente.
    6. Rechazo de voto repetido desde la misma sesión.
    7. Rechazo de voto repetido por la misma cuenta de administrador.
    8. Rechazo de votos antes de abrir y después del cierre.
    9. Consistencia de resultados y porcentajes.
    12. Ausencia de cambios tras rechazos.
    """
    votacion = servicio_votaciones.crear_votacion(
        pregunta="¿Qué comando incrementa el puntaje en un Sorted Set?",
        opciones=["ZINCRBY", "HINCRBY", "SADD"],
        creador_id="admin-1",
        creador_usuario="profesor",
    )
    id_votacion = votacion["id"]

    # 8a. Rechazo de voto cuando está en borrador
    with pytest.raises(ErrorVotoRechazado) as err_borrador:
        servicio_votaciones.registrar_voto(
            id_votacion=id_votacion,
            id_opcion="opcion_1",
            id_sesion_votante="sesion-anonima-1",
        )
    assert err_borrador.value.codigo == "VOTACION_NO_ABIERTA"

    # Abrir votación
    servicio_votaciones.abrir_votacion(id_votacion, "admin-1")

    # 4. Voto válido anónimo
    tras_voto_1 = servicio_votaciones.registrar_voto(
        id_votacion=id_votacion,
        id_opcion="opcion_1",
        id_sesion_votante="sesion-anonima-1",
    )
    assert tras_voto_1["total_votos"] == 1
    assert tras_voto_1["participacion"]["ya_voto_sesion"] is True

    # 5. Rechazo de opción inexistente y 12. Ausencia de cambios
    with pytest.raises(ErrorVotoRechazado) as err_opcion:
        servicio_votaciones.registrar_voto(
            id_votacion=id_votacion,
            id_opcion="opcion_999",
            id_sesion_votante="sesion-anonima-2",
        )
    assert err_opcion.value.codigo == "OPCION_NO_EXISTE"
    estado_sin_cambios = servicio_votaciones.obtener_detalle_votacion(
        id_votacion, id_sesion_votante="sesion-anonima-2"
    )
    assert estado_sin_cambios["total_votos"] == 1
    assert estado_sin_cambios["participacion"]["ya_voto"] is False

    # 6. Rechazo de voto repetido desde la misma sesión
    with pytest.raises(ErrorVotoRechazado) as err_sesion_rep:
        servicio_votaciones.registrar_voto(
            id_votacion=id_votacion,
            id_opcion="opcion_2",
            id_sesion_votante="sesion-anonima-1",
        )
    assert err_sesion_rep.value.codigo == "YA_VOTO_SESION"

    # Caso especial: si esa misma sesión inicia sesión como administrador, tampoco puede volver a votar
    with pytest.raises(ErrorVotoRechazado) as err_sesion_con_admin:
        servicio_votaciones.registrar_voto(
            id_votacion=id_votacion,
            id_opcion="opcion_2",
            id_sesion_votante="sesion-anonima-1",
            id_administrador="admin-nuevo",
        )
    assert err_sesion_con_admin.value.codigo == "YA_VOTO_SESION"

    # 4b. Voto válido de un administrador desde una nueva sesión
    tras_voto_admin = servicio_votaciones.registrar_voto(
        id_votacion=id_votacion,
        id_opcion="opcion_1",
        id_sesion_votante="sesion-admin-navegador-1",
        id_administrador="admin-1",
    )
    assert tras_voto_admin["total_votos"] == 2

    # 7. Rechazo de voto repetido por la misma cuenta de administrador desde otro navegador/sesión
    with pytest.raises(ErrorVotoRechazado) as err_admin_rep:
        servicio_votaciones.registrar_voto(
            id_votacion=id_votacion,
            id_opcion="opcion_2",
            id_sesion_votante="sesion-admin-navegador-2",
            id_administrador="admin-1",
        )
    assert err_admin_rep.value.codigo == "YA_VOTO_ADMINISTRADOR"

    # Voto válido de otro participante para verificar porcentajes (total = 4 votos: 3 para opcion_1, 1 para opcion_2)
    servicio_votaciones.registrar_voto(
        id_votacion=id_votacion,
        id_opcion="opcion_1",
        id_sesion_votante="sesion-anonima-3",
    )
    resultado_final = servicio_votaciones.registrar_voto(
        id_votacion=id_votacion,
        id_opcion="opcion_2",
        id_sesion_votante="sesion-anonima-4",
    )

    # 9. Consistencia de resultados y porcentajes
    assert resultado_final["total_votos"] == 4
    ranking = resultado_final["resultados"]
    assert ranking[0]["id_opcion"] == "opcion_1"
    assert ranking[0]["votos"] == 3
    assert ranking[0]["porcentaje"] == 75.0
    assert ranking[1]["id_opcion"] == "opcion_2"
    assert ranking[1]["votos"] == 1
    assert ranking[1]["porcentaje"] == 25.0
    assert ranking[2]["id_opcion"] == "opcion_3"
    assert ranking[2]["votos"] == 0
    assert ranking[2]["porcentaje"] == 0.0

    # 8b. Cerrar la votación y verificar rechazo de nuevos votos tras el cierre
    servicio_votaciones.cerrar_votacion(id_votacion, "admin-1")
    with pytest.raises(ErrorVotoRechazado) as err_cerrada:
        servicio_votaciones.registrar_voto(
            id_votacion=id_votacion,
            id_opcion="opcion_3",
            id_sesion_votante="sesion-anonima-5",
        )
    assert err_cerrada.value.codigo == "VOTACION_NO_ABIERTA"

    # 12. Verificar que el total siguió intacto en 4 después del rechazo
    consulta_post_cierre = servicio_votaciones.obtener_detalle_votacion(id_votacion)
    assert consulta_post_cierre["total_votos"] == 4


@pytest.mark.integracion_redis
def test_10_comportamiento_concurrente_script_lua(
    servicio_votaciones: ServicioVotaciones,
) -> None:
    """
    10. Verifica que bajo solicitudes concurrentes:
    a) 15 hilos intentando votar al mismo tiempo con la MISMA sesión solo logren 1 voto.
    b) 20 hilos con sesiones distintas logren exactamente 20 votos adicionales sin pérdidas.
    """
    votacion = servicio_votaciones.crear_votacion(
        pregunta="Prueba de concurrencia atómica con Lua en Redis",
        opciones=["Opción Concurrente A", "Opción Concurrente B"],
        creador_id="admin-1",
        creador_usuario="profesor",
    )
    id_votacion = votacion["id"]
    servicio_votaciones.abrir_votacion(id_votacion, "admin-1")

    def intentar_voto(id_sesion: str) -> bool:
        try:
            servicio_votaciones.registrar_voto(
                id_votacion=id_votacion,
                id_opcion="opcion_1",
                id_sesion_votante=id_sesion,
            )
            return True
        except ErrorVotoRechazado:
            return False

    # Parte A: 15 hilos concurrentes con la MISMA identidad de sesión
    with ThreadPoolExecutor(max_workers=15) as ejecutor:
        resultados_misma_sesion: List[bool] = list(
            ejecutor.map(intentar_voto, ["sesion-repetida-concurrente"] * 15)
        )

    assert resultados_misma_sesion.count(True) == 1
    assert resultados_misma_sesion.count(False) == 14

    # Parte B: 20 hilos concurrentes con sesiones únicas distintas
    sesiones_unicas = [f"sesion-unica-{i}" for i in range(20)]
    with ThreadPoolExecutor(max_workers=10) as ejecutor:
        resultados_distintas: List[bool] = list(ejecutor.map(intentar_voto, sesiones_unicas))

    assert all(resultados_distintas)

    detalle = servicio_votaciones.obtener_detalle_votacion(id_votacion)
    assert detalle["total_votos"] == 21
    assert detalle["total_identidades_registradas"] == 21


@pytest.mark.integracion_redis
def test_11_publicacion_eventos_pubsub_tras_voto_aceptado_y_no_en_rechazo(
    gestor_redis_prueba: GestorRedis,
    repo_votaciones: RepositorioVotaciones,
    servicio_votaciones: ServicioVotaciones,
) -> None:
    """
    11 y 12. Verifica que el script Lua publique una notificación en el canal
    Pub/Sub cuando un voto es aceptado y NO publique nada cuando el voto es rechazado.
    """
    votacion = servicio_votaciones.crear_votacion(
        pregunta="¿Se publica el evento en Redis Pub/Sub?",
        opciones=["Sí, con PUBLISH", "No"],
        creador_id="admin-1",
        creador_usuario="profesor",
    )
    id_votacion = votacion["id"]
    servicio_votaciones.abrir_votacion(id_votacion, "admin-1")

    canal = repo_votaciones.canal_eventos_votacion(id_votacion)
    pubsub = gestor_redis_prueba.crear_cliente_pubsub()
    pubsub.subscribe(canal)

    try:
        # Esperar breve instante para asegurar la suscripción activa
        time.sleep(0.05)
        pubsub.get_message(timeout=0.1)

        # Emitir voto válido -> debe publicar un mensaje en el canal
        servicio_votaciones.registrar_voto(
            id_votacion=id_votacion,
            id_opcion="opcion_1",
            id_sesion_votante="sesion-pubsub-1",
        )

        mensaje_recibido = pubsub.get_message(timeout=1.0)
        assert mensaje_recibido is not None
        assert mensaje_recibido["type"] == "message"
        assert "voto_registrado" in str(mensaje_recibido["data"])

        # Intentar voto duplicado (rechazado) -> NO debe publicar ningún mensaje
        with pytest.raises(ErrorVotoRechazado):
            servicio_votaciones.registrar_voto(
                id_votacion=id_votacion,
                id_opcion="opcion_1",
                id_sesion_votante="sesion-pubsub-1",
            )

        mensaje_tras_rechazo = pubsub.get_message(timeout=0.2)
        assert mensaje_tras_rechazo is None
    finally:
        pubsub.unsubscribe(canal)
        pubsub.close()
