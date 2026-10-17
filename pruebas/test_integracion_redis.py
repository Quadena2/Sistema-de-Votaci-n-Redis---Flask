"""
Pruebas de integración directa con Redis, verificación de estructuras nativas
(`Hashes`, `Sets`, `Sorted Sets`), manejo de desconexión y comandos CLI.
"""

import pytest
from flask import Flask

from app import crear_aplicacion
from app.datos.cliente_redis import ErrorConexionRedis, GestorRedis
from app.datos.repositorio_votaciones import RepositorioVotaciones
from app.negocio.excepciones import ErrorAlmacenamientoNoDisponible
from app.negocio.servicio_votaciones import ServicioVotaciones
from pruebas.conftest import CLAVE_SECRETA_PRUEBA, CODIGO_INVITACION_PRUEBA


@pytest.mark.integracion_redis
def test_1_2_3_estructuras_redis_creacion_recuperacion_y_resultados(
    gestor_redis_prueba: GestorRedis,
    repo_votaciones: RepositorioVotaciones,
    servicio_votaciones: ServicioVotaciones,
) -> None:
    """
    1, 2 y 3. Comprueba que las claves creadas en Redis tengan los tipos y
    contenidos esperados (`zset`, `hash`, `set`) y que la consulta ordene
    adecuadamente los resultados.
    """
    cliente = gestor_redis_prueba.obtener_cliente()

    v1 = servicio_votaciones.crear_votacion(
        pregunta="Primera votación de prueba en Redis",
        opciones=["Opción Uno", "Opción Dos", "Opción Tres"],
        creador_id="admin-10",
        creador_usuario="admin_redis",
    )
    id_v1 = v1["id"]
    servicio_votaciones.abrir_votacion(id_v1, "admin-10")

    # Emitir dos votos para opcion_2 y un voto para opcion_3
    servicio_votaciones.registrar_voto(id_v1, "opcion_2", "sesion-a")
    servicio_votaciones.registrar_voto(id_v1, "opcion_2", "sesion-b", "admin-10")
    servicio_votaciones.registrar_voto(id_v1, "opcion_3", "sesion-c")

    # Comprobar tipos nativos de cada clave directamente en Redis (comando TYPE)
    assert cliente.type(repo_votaciones.clave_indice_votaciones()) == "zset"
    assert cliente.type(repo_votaciones.clave_datos_votacion(id_v1)) == "hash"
    assert cliente.type(repo_votaciones.clave_opciones_votacion(id_v1)) == "hash"
    assert cliente.type(repo_votaciones.clave_resultados_votacion(id_v1)) == "zset"
    assert cliente.type(repo_votaciones.clave_votantes_votacion(id_v1)) == "set"

    # Verificar operaciones HGET, HEXISTS, ZCARD, SCARD, SISMEMBER
    assert repo_votaciones.existe_votacion(id_v1) is True
    assert repo_votaciones.obtener_estado_votacion(id_v1) == "abierta"
    assert repo_votaciones.existe_opcion(id_v1, "opcion_2") is True
    assert repo_votaciones.existe_opcion(id_v1, "opcion_inexistente") is False
    assert repo_votaciones.contar_opciones_registradas(id_v1) == 3
    # 3 sesiones + 1 cuenta de administrador = 4 identidades en el Set
    assert repo_votaciones.contar_identidades_votantes(id_v1) == 4
    assert repo_votaciones.ha_votado_identidad(id_v1, "sesion:sesion-a") is True
    assert repo_votaciones.ha_votado_identidad(id_v1, "administrador:admin-10") is True

    # Verificar ZREVRANGE en resultados
    ordenados = repo_votaciones.obtener_resultados_ordenados(id_v1)
    assert ordenados[0] == ("opcion_2", 2)
    assert ordenados[1] == ("opcion_3", 1)
    assert ordenados[2] == ("opcion_1", 0)


@pytest.mark.unitaria
def test_4_comportamiento_ante_desconexion_de_redis() -> None:
    """
    4. Verifica el comportamiento controlado de las capas de datos, negocio y
    presentación cuando Redis se encuentra desconectado o inaccesible.
    """
    import redis

    # Puerto local cerrado con timeout breve para verificar el fallo rápido de red
    cliente_inaccesible = redis.Redis.from_url(
        "redis://127.0.0.1:6399/0",
        decode_responses=True,
        socket_connect_timeout=0.2,
        socket_timeout=0.2,
    )
    gestor_caido = GestorRedis(
        redis_url="redis://127.0.0.1:6399/0",
        prefijo="votacion_desconectado",
        cliente_personalizado=cliente_inaccesible,
    )

    with pytest.raises(ErrorConexionRedis):
        gestor_caido.comprobar_conexion()

    repo_caido = RepositorioVotaciones(gestor_caido)
    servicio_caido = ServicioVotaciones(repo_caido)

    with pytest.raises(ErrorAlmacenamientoNoDisponible):
        servicio_caido.listar_votaciones_publicas()

    app_caida = crear_aplicacion(
        config_personalizada={
            "ENTORNO": "pruebas",
            "TESTING": True,
            "SECRET_KEY": CLAVE_SECRETA_PRUEBA,
            "CODIGO_INVITACION_ADMIN": CODIGO_INVITACION_PRUEBA,
            "REDIS_URL": "redis://127.0.0.1:6399/0",
            "PREFIJO_REDIS": "votacion_desconectado",
        },
        gestor_redis_personalizado=gestor_caido,
    )
    cliente_http = app_caida.test_client()

    # La ruta pública responde con 503 Service Unavailable sin colapsar con traza sin manejar
    respuesta_inicio = cliente_http.get("/")
    assert respuesta_inicio.status_code == 503

    # El endpoint API responde con JSON estructurado y código 503
    respuesta_api = cliente_http.get("/api/votaciones/id-cualquiera")
    assert respuesta_api.status_code == 503
    datos_json = respuesta_api.get_json()
    assert datos_json["exito"] is False
    assert datos_json["codigo"] == "REDIS_NO_DISPONIBLE"


@pytest.mark.integracion_redis
def test_comandos_cli_mantenimiento(app_prueba: Flask) -> None:
    """
    Verifica los comandos CLI de mantenimiento: comprobar-redis,
    crear-votacion-ejemplo, consultar-votacion, consultar-resultados y
    reiniciar-votacion (con aislamiento de claves).
    """
    runner = app_prueba.test_cli_runner()

    # 1. comprobar-redis
    res_ping = runner.invoke(args=["comprobar-redis"])
    assert res_ping.exit_code == 0
    assert "PONG" in res_ping.output

    # 2. crear-votacion-ejemplo
    res_ejemplo = runner.invoke(args=["crear-votacion-ejemplo"])
    assert res_ejemplo.exit_code == 0
    assert "Votación de ejemplo creada" in res_ejemplo.output

    servicio: ServicioVotaciones = app_prueba.extensions["servicio_votaciones"]
    votaciones = servicio.listar_todas_las_votaciones()
    assert len(votaciones) == 1
    id_ejemplo = votaciones[0]["id"]

    # Emitir un voto en la votación de ejemplo
    servicio.registrar_voto(id_ejemplo, "opcion_1", "sesion-cli-1")

    # 3. consultar-votacion
    res_consulta = runner.invoke(args=["consultar-votacion", id_ejemplo])
    assert res_consulta.exit_code == 0
    assert id_ejemplo in res_consulta.output

    # 4. consultar-resultados
    res_resultados = runner.invoke(args=["consultar-resultados", id_ejemplo])
    assert res_resultados.exit_code == 0
    assert "Total de votos emitidos: 1" in res_resultados.output

    # 5. reiniciar-votacion sin --confirmar debe rechazarse
    res_sin_confirmar = runner.invoke(args=["reiniciar-votacion", id_ejemplo])
    assert res_sin_confirmar.exit_code != 0

    # 6. reiniciar-votacion con --confirmar pone en 0 únicamente esa votación
    res_reinicio = runner.invoke(args=["reiniciar-votacion", id_ejemplo, "--confirmar"])
    assert res_reinicio.exit_code == 0
    assert servicio.obtener_detalle_votacion(id_ejemplo)["total_votos"] == 0
