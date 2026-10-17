"""
Pruebas de extremo a extremo sobre las rutas HTTP públicas, administrativas y
endpoints de la API JSON en Flask.
"""

import pytest
from flask import Flask
from flask.testing import FlaskClient

from pruebas.conftest import CODIGO_INVITACION_PRUEBA, extraer_csrf_html


@pytest.mark.integracion_redis
def test_flujo_completo_rutas_publicas_y_administrativas(
    app_prueba: Flask,
    cliente_http: FlaskClient,
) -> None:
    """
    Recorre el flujo completo exigido por el proyecto:
    1. Registro de administrador.
    2. Creación de votación desde el panel.
    3. Apertura de la votación.
    4. Voto del administrador desde la interfaz pública.
    5. Voto de un participante anónimo desde otro cliente.
    6. Rechazo de votos repetidos (por sesión y por cuenta de administrador).
    7. Cierre de la votación y rechazo de votos posteriores.
    """
    # 1. Registrar administrador
    form_reg = cliente_http.get("/administracion/registro")
    csrf = extraer_csrf_html(form_reg.get_data(as_text=True))

    resp_reg = cliente_http.post(
        "/administracion/registro",
        data={
            "csrf_token": csrf,
            "nombre_usuario": "admin_expositor",
            "contrasena": "PasswordExposicion2026",
            "confirmacion_contrasena": "PasswordExposicion2026",
            "codigo_invitacion": CODIGO_INVITACION_PRUEBA,
        },
        follow_redirects=True,
    )
    assert resp_reg.status_code == 200

    # 2. Crear una nueva votación
    pagina_nueva = cliente_http.get("/administracion/votaciones/nueva")
    assert pagina_nueva.status_code == 200
    csrf_crear = extraer_csrf_html(pagina_nueva.get_data(as_text=True))

    resp_crear = cliente_http.post(
        "/administracion/votaciones",
        data={
            "csrf_token": csrf_crear,
            "pregunta": "¿Qué patrón garantiza atomicidad en Redis?",
            "opciones": ["Scripts Lua con EVALSHA", "Consultas separadas en cliente"],
        },
        follow_redirects=True,
    )
    assert resp_crear.status_code == 200
    html_panel = resp_crear.get_data(as_text=True)
    assert "¿Qué patrón garantiza atomicidad en Redis?" in html_panel

    # Obtener el ID de la votación creada desde el servicio
    servicio = app_prueba.extensions["servicio_votaciones"]
    id_votacion = servicio.listar_todas_las_votaciones()[0]["id"]

    # Mientras está en borrador, no aparece en el listado de votaciones abiertas de /votaciones
    resp_publica_borrador = cliente_http.get("/votaciones")
    assert resp_publica_borrador.status_code == 200
    assert "No hay votaciones abiertas en este momento" in resp_publica_borrador.get_data(
        as_text=True
    )

    # 3. Abrir la votación desde el panel administrativo
    csrf_panel = extraer_csrf_html(html_panel)
    resp_abrir = cliente_http.post(
        f"/administracion/votaciones/{id_votacion}/abrir",
        data={"csrf_token": csrf_panel},
        follow_redirects=True,
    )
    assert resp_abrir.status_code == 200
    assert "ahora está ABIERTA" in resp_abrir.get_data(as_text=True)

    # 4. El administrador accede a la página pública de votación y emite su voto
    pagina_detalle = cliente_http.get(f"/votaciones/{id_votacion}")
    assert pagina_detalle.status_code == 200
    csrf_voto_admin = extraer_csrf_html(pagina_detalle.get_data(as_text=True))

    resp_voto_admin = cliente_http.post(
        f"/api/votaciones/{id_votacion}/votos",
        json={"id_opcion": "opcion_1"},
        headers={"X-CSRFToken": csrf_voto_admin, "Accept": "application/json"},
    )
    assert resp_voto_admin.status_code == 200
    datos_voto_admin = resp_voto_admin.get_json()
    assert datos_voto_admin["exito"] is True
    assert datos_voto_admin["votacion"]["total_votos"] == 1

    # 5. Un visitante anónimo abre otro navegador (nuevo test_client) y vota sin registrarse
    cliente_anonimo = app_prueba.test_client()
    pagina_anonima = cliente_anonimo.get(f"/votaciones/{id_votacion}")
    assert pagina_anonima.status_code == 200
    csrf_anonimo = extraer_csrf_html(pagina_anonima.get_data(as_text=True))

    resp_voto_anonimo = cliente_anonimo.post(
        f"/api/votaciones/{id_votacion}/votos",
        json={"id_opcion": "opcion_1"},
        headers={"X-CSRFToken": csrf_anonimo, "Accept": "application/json"},
    )
    assert resp_voto_anonimo.status_code == 200
    datos_anonimo = resp_voto_anonimo.get_json()
    assert datos_anonimo["exito"] is True
    assert datos_anonimo["votacion"]["total_votos"] == 2

    # 6a. El visitante anónimo intenta votar por segunda vez desde su misma sesión -> 409
    resp_duplicado_anonimo = cliente_anonimo.post(
        f"/api/votaciones/{id_votacion}/votos",
        json={"id_opcion": "opcion_2"},
        headers={"X-CSRFToken": csrf_anonimo, "Accept": "application/json"},
    )
    assert resp_duplicado_anonimo.status_code == 409
    assert resp_duplicado_anonimo.get_json()["codigo"] == "YA_VOTO_SESION"

    # 6b. El mismo administrador inicia sesión en un tercer navegador e intenta volver a votar -> 409
    cliente_admin_otro_nav = app_prueba.test_client()
    form_login = cliente_admin_otro_nav.get("/administracion/ingresar")
    csrf_login = extraer_csrf_html(form_login.get_data(as_text=True))
    cliente_admin_otro_nav.post(
        "/administracion/ingresar",
        data={
            "csrf_token": csrf_login,
            "nombre_usuario": "admin_expositor",
            "contrasena": "PasswordExposicion2026",
        },
        follow_redirects=True,
    )
    pagina_voto_nav3 = cliente_admin_otro_nav.get(f"/votaciones/{id_votacion}")
    csrf_nav3 = extraer_csrf_html(pagina_voto_nav3.get_data(as_text=True))

    resp_duplicado_admin = cliente_admin_otro_nav.post(
        f"/api/votaciones/{id_votacion}/votos",
        json={"id_opcion": "opcion_2"},
        headers={"X-CSRFToken": csrf_nav3, "Accept": "application/json"},
    )
    assert resp_duplicado_admin.status_code == 409
    assert resp_duplicado_admin.get_json()["codigo"] == "YA_VOTO_ADMINISTRADOR"

    # 7. Cerrar la votación desde el panel administrativo
    panel_actual = cliente_http.get("/administracion")
    csrf_cerrar = extraer_csrf_html(panel_actual.get_data(as_text=True))
    resp_cerrar = cliente_http.post(
        f"/administracion/votaciones/{id_votacion}/cerrar",
        data={"csrf_token": csrf_cerrar},
        follow_redirects=True,
    )
    assert resp_cerrar.status_code == 200
    assert "CERRADA definitivamente" in resp_cerrar.get_data(as_text=True)

    # Verificar que un nuevo visitante ya no puede votar porque está cerrada
    cliente_tardio = app_prueba.test_client()
    pagina_tardia = cliente_tardio.get(f"/votaciones/{id_votacion}")
    csrf_tardio = extraer_csrf_html(pagina_tardia.get_data(as_text=True))
    resp_voto_tardio = cliente_tardio.post(
        f"/api/votaciones/{id_votacion}/votos",
        json={"id_opcion": "opcion_1"},
        headers={"X-CSRFToken": csrf_tardio, "Accept": "application/json"},
    )
    assert resp_voto_tardio.status_code == 409
    assert resp_voto_tardio.get_json()["codigo"] == "VOTACION_NO_ABIERTA"

    # Consultar la pantalla de resultados finales y API JSON
    resp_resultados_html = cliente_tardio.get(f"/votaciones/{id_votacion}/resultados")
    assert resp_resultados_html.status_code == 200
    resp_api_json = cliente_tardio.get(f"/api/votaciones/{id_votacion}")
    assert resp_api_json.status_code == 200
    assert resp_api_json.get_json()["votacion"]["total_votos"] == 2


@pytest.mark.integracion_redis
def test_endpoint_sse_eventos_en_tiempo_real(
    app_prueba: Flask,
    cliente_http: FlaskClient,
) -> None:
    """
    Verifica que el endpoint SSE (/api/votaciones/<id>/eventos) devuelva 404
    para votaciones inexistentes y transmita correctamente el evento inicial
    con cabecera text/event-stream para votaciones existentes.
    """
    resp_no_existe = cliente_http.get("/api/votaciones/no-existe/eventos")
    assert resp_no_existe.status_code == 404

    servicio = app_prueba.extensions["servicio_votaciones"]
    votacion = servicio.crear_votacion(
        pregunta="¿Probar transmisión SSE con Redis Pub/Sub?",
        opciones=["Opción SSE 1", "Opción SSE 2"],
        creador_id="admin-sse",
        creador_usuario="admin_sse",
    )
    id_votacion = votacion["id"]

    respuesta_sse = cliente_http.get(
        f"/api/votaciones/{id_votacion}/eventos",
        buffered=False,
    )
    assert respuesta_sse.status_code == 200
    assert "text/event-stream" in respuesta_sse.content_type

    # Leer el primer bloque del generador SSE (evento de estado_inicial) y cerrar el stream
    iterador = respuesta_sse.response
    primer_mensaje = next(iter(iterador))
    if isinstance(primer_mensaje, bytes):
        texto_mensaje = primer_mensaje.decode("utf-8")
    else:
        texto_mensaje = str(primer_mensaje)

    respuesta_sse.close()

    assert "event: actualizacion" in texto_mensaje
    assert "estado_inicial" in texto_mensaje
    assert id_votacion in texto_mensaje


@pytest.mark.integracion_redis
def test_actualizacion_tiempo_real_listado_al_crear_y_cerrar(
    app_prueba: Flask,
    cliente_http: FlaskClient,
) -> None:
    """
    Verifica que:
    1. Crear una votación con 'abrir_al_crear=si' la deje inmediatamente abierta
       y publique el evento en el canal global de Redis Pub/Sub.
    2. El endpoint /api/votaciones y el stream SSE /api/votaciones/eventos
       reflejen en tiempo real tanto la nueva votación abierta como su paso a cerrada.
    """
    # Registrar administrador
    form_reg = cliente_http.get("/administracion/registro")
    csrf = extraer_csrf_html(form_reg.get_data(as_text=True))
    cliente_http.post(
        "/administracion/registro",
        data={
            "csrf_token": csrf,
            "nombre_usuario": "admin_tiempo_real",
            "contrasena": "PasswordSegura2026",
            "confirmacion_contrasena": "PasswordSegura2026",
            "codigo_invitacion": CODIGO_INVITACION_PRUEBA,
        },
        follow_redirects=True,
    )

    # Suscribirse al canal global de Redis Pub/Sub para comprobar la notificación
    gestor = app_prueba.extensions["gestor_redis"]
    repo = app_prueba.extensions["repositorio_votaciones"]
    canal_global = repo.canal_global_votaciones()
    pubsub = gestor.crear_cliente_pubsub()
    pubsub.subscribe(canal_global)

    try:
        pubsub.get_message(timeout=0.1)

        # Crear votación con abrir_al_crear="si" (como en el formulario web por defecto)
        pagina_nueva = cliente_http.get("/administracion/votaciones/nueva")
        csrf_crear = extraer_csrf_html(pagina_nueva.get_data(as_text=True))
        resp_crear = cliente_http.post(
            "/administracion/votaciones",
            data={
                "csrf_token": csrf_crear,
                "pregunta": "¿Se actualiza el listado público en tiempo real?",
                "opciones": ["Sí, mediante SSE global", "No"],
                "abrir_al_crear": "si",
            },
            follow_redirects=True,
        )
        assert resp_crear.status_code == 200

        # Comprobar que llegaron los eventos de creación y apertura al canal global Pub/Sub
        evento_creacion = pubsub.get_message(timeout=1.0)
        assert evento_creacion is not None
        assert "votacion_creada" in str(evento_creacion["data"])

        evento_apertura = pubsub.get_message(timeout=1.0)
        assert evento_apertura is not None
        assert "abierta" in str(evento_apertura["data"])

        # Comprobar mediante /api/votaciones que aparece en abiertas
        cliente_visitante = app_prueba.test_client()
        resp_listado = cliente_visitante.get("/api/votaciones")
        assert resp_listado.status_code == 200
        datos_listado = resp_listado.get_json()
        assert len(datos_listado["abiertas"]) == 1
        assert len(datos_listado["cerradas"]) == 0
        id_votacion = datos_listado["abiertas"][0]["id"]

        # Comprobar el stream SSE del listado (/api/votaciones/eventos)
        resp_sse_listado = cliente_visitante.get("/api/votaciones/eventos", buffered=False)
        assert resp_sse_listado.status_code == 200
        assert "text/event-stream" in resp_sse_listado.content_type
        primer_evento_listado = next(iter(resp_sse_listado.response))
        texto_sse = (
            primer_evento_listado.decode("utf-8")
            if isinstance(primer_evento_listado, bytes)
            else str(primer_evento_listado)
        )
        resp_sse_listado.close()
        assert "actualizacion_listado" in texto_sse
        assert id_votacion in texto_sse

        # Emitir un voto desde el visitante y comprobar que el endpoint del panel administrativo
        # (/administracion/api/votaciones) refleja el contador actualizado al instante
        pagina_voto = cliente_visitante.get(f"/votaciones/{id_votacion}")
        csrf_voto = extraer_csrf_html(pagina_voto.get_data(as_text=True))
        cliente_visitante.post(
            f"/api/votaciones/{id_votacion}/votos",
            json={"id_opcion": "opcion_1"},
            headers={"X-CSRFToken": csrf_voto, "Accept": "application/json"},
        )
        # Consumir el mensaje de voto_registrado del canal global
        evento_voto = pubsub.get_message(timeout=1.0)
        assert evento_voto is not None
        assert "voto_registrado" in str(evento_voto["data"])

        resp_panel_api = cliente_http.get(
            "/administracion/api/votaciones",
            headers={"Accept": "application/json"},
        )
        assert resp_panel_api.status_code == 200
        datos_panel_api = resp_panel_api.get_json()
        assert datos_panel_api["resumen"]["votos_totales"] == 1
        assert datos_panel_api["votaciones"][0]["total_votos"] == 1

        # Verificar que el enlace a estadísticas/resultados en el panel no abra pestaña nueva (sin target="_blank")
        panel_html = cliente_http.get("/administracion").get_data(as_text=True)
        assert 'target="_blank"' not in panel_html

        # Cerrar la votación y verificar que se publica en el canal global y pasa a cerradas
        csrf_cerrar = extraer_csrf_html(panel_html)
        cliente_http.post(
            f"/administracion/votaciones/{id_votacion}/cerrar",
            data={"csrf_token": csrf_cerrar},
            follow_redirects=True,
        )

        evento_cierre = pubsub.get_message(timeout=1.0)
        assert evento_cierre is not None
        assert "cerrada" in str(evento_cierre["data"])

        resp_listado_post = cliente_visitante.get("/api/votaciones").get_json()
        assert len(resp_listado_post["abiertas"]) == 0
        assert len(resp_listado_post["cerradas"]) == 1
    finally:
        pubsub.unsubscribe(canal_global)
        pubsub.close()


