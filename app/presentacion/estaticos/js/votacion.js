/**
 * Lógica de interfaz para actualización en tiempo real mediante Server-Sent Events (SSE)
 * respaldados por Redis Pub/Sub (votacion.js).
 *
 * Cubre dos contextos públicos:
 * A) Detalle de votación y pantalla de resultados (`#aplicacion-votacion`):
 *    - Escucha `/api/votaciones/<id>/eventos` para actualizar conteos, porcentajes,
 *      barras de progreso y cambios de estado (`borrador -> abierta -> cerrada`) al instante.
 *    - Envía votos mediante `fetch` con protección CSRF.
 * B) Portada y listado público de votaciones (`#aplicacion-listado-votaciones`):
 *    - Escucha `/api/votaciones/eventos` para mostrar en tiempo real nuevas votaciones
 *      abiertas, votaciones que acaban de cerrarse y cambios en el total de votos
 *      sin recargar la página.
 */

(function () {
    "use strict";

    /**
     * Formatea una fecha ISO 8601 al formato DD/MM/YYYY HH:MM:SS UTC usado por el servidor.
     */
    function formatearFechaIso(cadenaIso) {
        if (!cadenaIso) {
            return "—";
        }
        const fecha = new Date(cadenaIso);
        if (Number.isNaN(fecha.getTime())) {
            return String(cadenaIso);
        }
        const pad = function (n) {
            return String(n).padStart(2, "0");
        };
        return (
            pad(fecha.getUTCDate()) +
            "/" +
            pad(fecha.getUTCMonth() + 1) +
            "/" +
            fecha.getUTCFullYear() +
            " " +
            pad(fecha.getUTCHours()) +
            ":" +
            pad(fecha.getUTCMinutes()) +
            ":" +
            pad(fecha.getUTCSeconds()) +
            " UTC"
        );
    }

    /**
     * Actualiza un indicador visual de estado de conexión SSE.
     */
    function actualizarInsigniaSse(contenedor, punto, texto, estado, mensaje) {
        if (!contenedor || !punto || !texto) {
            return;
        }
        texto.textContent = mensaje;
        contenedor.className =
            "inline-flex items-center gap-2 text-xs font-medium px-3.5 py-1.5 rounded-full border transition ";

        if (estado === "conectado") {
            contenedor.className += "bg-emerald-50 border-emerald-300 text-emerald-800";
            punto.className = "w-2 h-2 rounded-full bg-emerald-500 animate-pulse";
        } else if (estado === "reconectando") {
            contenedor.className += "bg-amber-50 border-amber-300 text-amber-800";
            punto.className = "w-2 h-2 rounded-full bg-amber-500 animate-ping";
        } else {
            contenedor.className += "bg-red-50 border-red-300 text-red-800";
            punto.className = "w-2 h-2 rounded-full bg-red-500";
        }
    }

    // =========================================================================
    // CONTEXTO A: Detalle de una votación y resultados en vivo (#aplicacion-votacion)
    // =========================================================================

    function inicializarDetalleVotacion(contenedorApp) {
        const urlVoto = contenedorApp.dataset.urlVoto;
        const urlEstado = contenedorApp.dataset.urlEstado;
        const urlEventos = contenedorApp.dataset.urlEventos;

        const metaCsrf = document.querySelector('meta[name="csrf-token"]');
        const tokenCsrf = metaCsrf ? metaCsrf.getAttribute("content") : "";

        const indicadorSse = document.getElementById("indicador-sse");
        const puntoSse = document.getElementById("punto-sse");
        const textoSse = document.getElementById("texto-sse");

        const alertaVotacion = document.getElementById("alerta-votacion");
        const formularioVotacion = document.getElementById("formulario-votacion");
        const grupoOpciones = document.getElementById("grupo-opciones");
        const botonVotar = document.getElementById("boton-votar");
        const avisoParticipacion = document.getElementById("aviso-participacion");
        const textoMotivoBloqueo = document.getElementById("texto-motivo-bloqueo");

        const insigniaEstado = document.getElementById("insignia-estado-votacion");
        const textoEstado = document.getElementById("texto-estado-votacion");
        const contenedorFechaApertura = document.getElementById("contenedor-fecha-apertura");
        const fechaAperturaElemento = document.getElementById("fecha-apertura-votacion");
        const contenedorFechaCierre = document.getElementById("contenedor-fecha-cierre");
        const fechaCierreElemento = document.getElementById("fecha-cierre-votacion");

        const elementoTotalVotos = document.getElementById("total-votos");
        const elementoTotalIdentidades = document.getElementById("total-identidades");
        const contenedorResultados = document.getElementById("contenedor-resultados");

        let fuenteEventos = null;
        let huboDesconexion = false;
        let ultimoEstadoConocido = textoEstado ? textoEstado.textContent.trim() : "";

        function mostrarMensajeVotacion(tipo, texto) {
            if (!alertaVotacion) {
                return;
            }
            alertaVotacion.classList.remove(
                "hidden",
                "bg-emerald-50",
                "border-emerald-400",
                "text-emerald-900",
                "bg-amber-50",
                "border-amber-400",
                "text-amber-900",
                "bg-red-50",
                "border-red-400",
                "text-red-900"
            );

            if (tipo === "exito") {
                alertaVotacion.classList.add(
                    "bg-emerald-50",
                    "border-emerald-400",
                    "text-emerald-900"
                );
            } else if (tipo === "advertencia") {
                alertaVotacion.classList.add(
                    "bg-amber-50",
                    "border-amber-400",
                    "text-amber-900"
                );
            } else {
                alertaVotacion.classList.add("bg-red-50", "border-red-400", "text-red-900");
            }
            alertaVotacion.textContent = texto;
        }

        function actualizarEstadoYFormulario(votacion) {
            if (!votacion) {
                return;
            }

            const estadoPrevio = ultimoEstadoConocido;
            ultimoEstadoConocido = votacion.estado;

            if (textoEstado) {
                textoEstado.textContent = votacion.estado;
            }
            if (insigniaEstado) {
                insigniaEstado.className =
                    "inline-flex items-center gap-1.5 text-xs font-bold px-3 py-1 rounded-full uppercase tracking-wide ";
                if (votacion.estado === "abierta") {
                    insigniaEstado.className += "bg-emerald-100 text-emerald-800";
                } else if (votacion.estado === "cerrada") {
                    insigniaEstado.className += "bg-slate-200 text-slate-700";
                } else {
                    insigniaEstado.className += "bg-amber-100 text-amber-800";
                }
            }

            if (fechaAperturaElemento && votacion.fecha_apertura) {
                fechaAperturaElemento.textContent = formatearFechaIso(votacion.fecha_apertura);
                if (contenedorFechaApertura) {
                    contenedorFechaApertura.classList.remove("hidden");
                }
            }
            if (fechaCierreElemento && votacion.fecha_cierre) {
                fechaCierreElemento.textContent = formatearFechaIso(votacion.fecha_cierre);
                if (contenedorFechaCierre) {
                    contenedorFechaCierre.classList.remove("hidden");
                }
            }

            if (estadoPrevio && estadoPrevio !== votacion.estado) {
                if (votacion.estado === "cerrada") {
                    mostrarMensajeVotacion(
                        "advertencia",
                        "Esta votación acaba de ser cerrada en tiempo real por el administrador. Los resultados mostrados son definitivos."
                    );
                } else if (votacion.estado === "abierta") {
                    mostrarMensajeVotacion(
                        "exito",
                        "¡Esta votación acaba de abrirse en tiempo real! Ya puedes emitir tu voto."
                    );
                }
            }

            if (!votacion.participacion) {
                return;
            }

            const puedeVotar =
                votacion.estado === "abierta" && Boolean(votacion.participacion.puede_votar);

            if (grupoOpciones) {
                grupoOpciones.disabled = !puedeVotar;
            }
            if (botonVotar) {
                botonVotar.disabled = !puedeVotar;
                if (votacion.participacion.ya_voto) {
                    botonVotar.textContent = "Voto ya registrado en esta votación";
                } else if (votacion.estado !== "abierta") {
                    botonVotar.textContent = "Votación no disponible para votar";
                } else {
                    botonVotar.textContent = "Registrar voto en Redis";
                }
            }
            if (avisoParticipacion && textoMotivoBloqueo) {
                if (!puedeVotar && votacion.participacion.motivo_bloqueo) {
                    textoMotivoBloqueo.textContent = votacion.participacion.motivo_bloqueo;
                    avisoParticipacion.classList.remove("hidden");
                } else if (puedeVotar) {
                    avisoParticipacion.classList.add("hidden");
                }
            }
        }

        function renderizarResultados(votacion) {
            if (!votacion) {
                return;
            }

            if (elementoTotalVotos) {
                elementoTotalVotos.textContent = String(votacion.total_votos);
            }
            if (
                elementoTotalIdentidades &&
                votacion.total_identidades_registradas !== undefined
            ) {
                elementoTotalIdentidades.textContent = String(
                    votacion.total_identidades_registradas
                );
            }

            if (!contenedorResultados || !Array.isArray(votacion.resultados)) {
                return;
            }

            votacion.resultados.forEach(function (item) {
                const nodo = contenedorResultados.querySelector(
                    '[data-id-opcion="' + item.id_opcion + '"]'
                );
                if (!nodo) {
                    return;
                }

                const nodoPosicion = nodo.querySelector(".posicion-opcion");
                const nodoTexto = nodo.querySelector(".texto-opcion");
                const nodoVotos = nodo.querySelector(".votos-opcion");
                const nodoPorcentaje = nodo.querySelector(".porcentaje-opcion");
                const nodoBarra = nodo.querySelector(".barra-opcion");

                if (nodoPosicion) {
                    nodoPosicion.textContent = "#" + item.posicion;
                }
                if (nodoTexto) {
                    nodoTexto.textContent = item.texto;
                }
                if (nodoVotos) {
                    nodoVotos.textContent = String(item.votos);
                }
                if (nodoPorcentaje) {
                    nodoPorcentaje.textContent = String(item.porcentaje);
                }
                if (nodoBarra) {
                    nodoBarra.style.width = item.porcentaje + "%";
                }

                nodo.classList.remove("actualizado-reciente");
                void nodo.offsetWidth;
                nodo.classList.add("actualizado-reciente");

                contenedorResultados.appendChild(nodo);
            });
        }

        async function sincronizarEstadoCompleto() {
            if (!urlEstado) {
                return;
            }
            try {
                const respuesta = await fetch(urlEstado, {
                    method: "GET",
                    headers: { Accept: "application/json" },
                    credentials: "same-origin",
                });
                if (!respuesta.ok) {
                    return;
                }
                const datos = await respuesta.json();
                if (datos && datos.exito && datos.votacion) {
                    renderizarResultados(datos.votacion);
                    actualizarEstadoYFormulario(datos.votacion);
                }
            } catch (_error) {
                // La reconexión SSE reintentará automáticamente
            }
        }

        function iniciarConexionSseDetalle() {
            if (!urlEventos || typeof window.EventSource === "undefined") {
                actualizarInsigniaSse(
                    indicadorSse,
                    puntoSse,
                    textoSse,
                    "desconectado",
                    "SSE no soportado por el navegador"
                );
                return;
            }

            fuenteEventos = new EventSource(urlEventos);

            fuenteEventos.onopen = function () {
                actualizarInsigniaSse(
                    indicadorSse,
                    puntoSse,
                    textoSse,
                    "conectado",
                    "En vivo · Redis Pub/Sub + SSE activo"
                );
                if (huboDesconexion) {
                    huboDesconexion = false;
                    sincronizarEstadoCompleto();
                }
            };

            fuenteEventos.addEventListener("actualizacion", function (evento) {
                try {
                    const carga = JSON.parse(evento.data);
                    if (carga && carga.votacion) {
                        renderizarResultados(carga.votacion);
                        actualizarEstadoYFormulario(carga.votacion);
                    }
                } catch (_err) {
                    sincronizarEstadoCompleto();
                }
            });

            fuenteEventos.addEventListener("latido", function () {
                actualizarInsigniaSse(
                    indicadorSse,
                    puntoSse,
                    textoSse,
                    "conectado",
                    "En vivo · Redis Pub/Sub + SSE activo"
                );
            });

            fuenteEventos.onerror = function () {
                huboDesconexion = true;
                actualizarInsigniaSse(
                    indicadorSse,
                    puntoSse,
                    textoSse,
                    "reconectando",
                    "Reconectando flujo en tiempo real…"
                );
            };
        }

        if (formularioVotacion) {
            formularioVotacion.addEventListener("submit", async function (evento) {
                evento.preventDefault();

                const seleccion = formularioVotacion.querySelector(
                    'input[name="id_opcion"]:checked'
                );
                if (!seleccion) {
                    mostrarMensajeVotacion(
                        "error",
                        "Por favor, selecciona una opción antes de votar."
                    );
                    return;
                }

                if (botonVotar) {
                    botonVotar.disabled = true;
                    botonVotar.textContent = "Registrando voto atómico en Redis…";
                }

                try {
                    const respuesta = await fetch(urlVoto, {
                        method: "POST",
                        headers: {
                            "Content-Type": "application/json",
                            Accept: "application/json",
                            "X-CSRFToken": tokenCsrf,
                        },
                        credentials: "same-origin",
                        body: JSON.stringify({
                            id_opcion: seleccion.value,
                        }),
                    });

                    const resultado = await respuesta.json();

                    if (respuesta.ok && resultado.exito) {
                        mostrarMensajeVotacion(
                            "exito",
                            resultado.mensaje || "¡Tu voto fue registrado exitosamente!"
                        );
                        if (resultado.votacion) {
                            renderizarResultados(resultado.votacion);
                            actualizarEstadoYFormulario(resultado.votacion);
                        }
                    } else {
                        mostrarMensajeVotacion(
                            "error",
                            resultado.error || "No fue posible registrar tu voto."
                        );
                        await sincronizarEstadoCompleto();
                    }
                } catch (_errorRed) {
                    mostrarMensajeVotacion(
                        "error",
                        "Error de comunicación con el servidor al enviar tu voto."
                    );
                    if (botonVotar) {
                        botonVotar.disabled = false;
                        botonVotar.textContent = "Registrar voto en Redis";
                    }
                }
            });
        }

        iniciarConexionSseDetalle();

        window.addEventListener("beforeunload", function () {
            if (fuenteEventos) {
                fuenteEventos.close();
            }
        });
    }

    // =========================================================================
    // CONTEXTO B: Listado público de votaciones en vivo (#aplicacion-listado-votaciones)
    // =========================================================================

    function inicializarListadoVotaciones(contenedorListado) {
        const urlListado = contenedorListado.dataset.urlListado;
        const urlEventos = contenedorListado.dataset.urlEventos;

        const indicadorSse = document.getElementById("indicador-sse-listado");
        const puntoSse = document.getElementById("punto-sse-listado");
        const textoSse = document.getElementById("texto-sse-listado");

        const contadorAbiertas = document.getElementById("contador-votaciones-abiertas");
        const contadorHeroAbiertas = document.getElementById("contador-hero-abiertas");
        const contenedorAbiertas = document.getElementById("contenedor-votaciones-abiertas");

        const seccionCerradas = document.getElementById("seccion-votaciones-cerradas");
        const contadorCerradas = document.getElementById("contador-votaciones-cerradas");
        const contenedorCerradas = document.getElementById("contenedor-votaciones-cerradas");

        let fuenteEventosListado = null;
        let huboDesconexionListado = false;

        function crearTarjetaAbierta(votacion) {
            const articulo = document.createElement("article");
            articulo.className =
                "tarjeta-votacion actualizado-reciente bg-white rounded-xl border border-slate-200 shadow-sm hover:shadow-md transition p-6 flex flex-col justify-between";

            const urlDetalle = "/votaciones/" + encodeURIComponent(votacion.id);
            const urlResultados = urlDetalle + "/resultados";
            const yaVoto = Boolean(votacion.participacion && votacion.participacion.ya_voto);

            const bloqueSuperior = document.createElement("div");
            bloqueSuperior.className = "space-y-3";

            const filaInsignias = document.createElement("div");
            filaInsignias.className = "flex items-center justify-between gap-2";

            const insigniaAbierta = document.createElement("span");
            insigniaAbierta.className =
                "insignia-abierta inline-flex items-center gap-1.5 bg-emerald-100 text-emerald-800 text-xs font-bold px-2.5 py-1 rounded-full uppercase tracking-wide";
            const puntoVerde = document.createElement("span");
            puntoVerde.className = "w-1.5 h-1.5 rounded-full bg-emerald-600";
            insigniaAbierta.appendChild(puntoVerde);
            insigniaAbierta.appendChild(document.createTextNode(" Abierta"));

            const insigniaParticipacion = document.createElement("span");
            if (yaVoto) {
                insigniaParticipacion.className =
                    "bg-indigo-50 text-indigo-700 border border-indigo-200 text-xs font-medium px-2.5 py-0.5 rounded-full";
                insigniaParticipacion.textContent = "✓ Ya participaste";
            } else {
                insigniaParticipacion.className =
                    "bg-slate-100 text-slate-700 text-xs font-medium px-2.5 py-0.5 rounded-full";
                insigniaParticipacion.textContent = "Disponible para votar";
            }

            filaInsignias.appendChild(insigniaAbierta);
            filaInsignias.appendChild(insigniaParticipacion);

            const titulo = document.createElement("h3");
            titulo.className = "text-lg font-bold text-slate-900 leading-snug";
            const enlaceTitulo = document.createElement("a");
            enlaceTitulo.href = urlDetalle;
            enlaceTitulo.className = "hover:text-indigo-600 transition";
            enlaceTitulo.textContent = votacion.pregunta;
            titulo.appendChild(enlaceTitulo);

            const metadatos = document.createElement("div");
            metadatos.className = "flex flex-wrap items-center gap-4 text-xs text-slate-500 pt-1";
            metadatos.innerHTML =
                '<span>Opciones: <strong class="text-slate-700">' +
                Number(votacion.total_opciones || 0) +
                '</strong></span>' +
                '<span>Total de votos: <strong class="text-slate-700">' +
                Number(votacion.total_votos || 0) +
                '</strong></span>' +
                '<span>Abierta: <strong class="text-slate-700">' +
                formatearFechaIso(votacion.fecha_apertura) +
                "</strong></span>";

            bloqueSuperior.appendChild(filaInsignias);
            bloqueSuperior.appendChild(titulo);
            bloqueSuperior.appendChild(metadatos);

            const bloqueAcciones = document.createElement("div");
            bloqueAcciones.className =
                "mt-6 pt-4 border-t border-slate-100 flex flex-wrap items-center justify-between gap-3";

            const botonPrincipal = document.createElement("a");
            botonPrincipal.href = urlDetalle;
            botonPrincipal.className =
                "bg-indigo-600 hover:bg-indigo-500 text-white text-sm font-semibold px-4 py-2 rounded-lg transition shadow-sm";
            botonPrincipal.textContent = yaVoto
                ? "Ver detalle y resultados"
                : "Participar y votar";

            const enlaceResultados = document.createElement("a");
            enlaceResultados.href = urlResultados;
            enlaceResultados.className =
                "text-sm font-medium text-slate-600 hover:text-indigo-600 px-3 py-2 rounded-lg hover:bg-slate-50 transition";
            enlaceResultados.textContent = "Pantalla de resultados en vivo →";

            bloqueAcciones.appendChild(botonPrincipal);
            bloqueAcciones.appendChild(enlaceResultados);

            articulo.appendChild(bloqueSuperior);
            articulo.appendChild(bloqueAcciones);
            return articulo;
        }

        function crearTarjetaCerrada(votacion) {
            const articulo = document.createElement("article");
            articulo.className =
                "actualizado-reciente bg-white rounded-xl border border-slate-200 p-6 flex flex-col justify-between opacity-95";

            const urlResultados = "/votaciones/" + encodeURIComponent(votacion.id) + "/resultados";

            const bloqueSuperior = document.createElement("div");
            bloqueSuperior.className = "space-y-2.5";

            const cabecera = document.createElement("div");
            cabecera.className = "flex items-center justify-between";
            cabecera.innerHTML =
                '<span class="insignia-cerrada inline-flex items-center gap-1.5 bg-slate-200 text-slate-700 text-xs font-bold px-2.5 py-1 rounded-full uppercase tracking-wide">Cerrada</span>' +
                '<span class="text-xs text-slate-500">Cerrada: ' +
                formatearFechaIso(votacion.fecha_cierre) +
                "</span>";

            const titulo = document.createElement("h3");
            titulo.className = "text-lg font-bold text-slate-800";
            titulo.textContent = votacion.pregunta;

            const resumen = document.createElement("p");
            resumen.className = "text-xs text-slate-500";
            resumen.innerHTML =
                'Total definitivo: <strong class="text-slate-800">' +
                Number(votacion.total_votos || 0) +
                " votos</strong> en " +
                Number(votacion.total_opciones || 0) +
                " opciones.";

            bloqueSuperior.appendChild(cabecera);
            bloqueSuperior.appendChild(titulo);
            bloqueSuperior.appendChild(resumen);

            const pie = document.createElement("div");
            pie.className = "mt-5 pt-3 border-t border-slate-100 flex items-center justify-end gap-3";
            const enlace = document.createElement("a");
            enlace.href = urlResultados;
            enlace.className =
                "bg-slate-800 hover:bg-slate-700 text-white text-xs font-semibold px-3.5 py-2 rounded-lg transition";
            enlace.textContent = "Consultar resultados finales";
            pie.appendChild(enlace);

            articulo.appendChild(bloqueSuperior);
            articulo.appendChild(pie);
            return articulo;
        }

        function renderizarListadoPublico(abiertas, cerradas) {
            const listaAbiertas = Array.isArray(abiertas) ? abiertas : [];
            const listaCerradas = Array.isArray(cerradas) ? cerradas : [];

            if (contadorAbiertas) {
                contadorAbiertas.textContent = String(listaAbiertas.length);
            }
            if (contadorHeroAbiertas) {
                contadorHeroAbiertas.textContent = String(listaAbiertas.length);
            }

            if (contenedorAbiertas) {
                contenedorAbiertas.innerHTML = "";
                if (listaAbiertas.length > 0) {
                    const rejilla = document.createElement("div");
                    rejilla.className = "grid grid-cols-1 md:grid-cols-2 gap-6";
                    listaAbiertas.forEach(function (votacion) {
                        rejilla.appendChild(crearTarjetaAbierta(votacion));
                    });
                    contenedorAbiertas.appendChild(rejilla);
                } else {
                    const vacio = document.createElement("div");
                    vacio.className =
                        "bg-white rounded-xl border border-slate-200 p-8 text-center space-y-3";
                    vacio.innerHTML =
                        '<p class="text-slate-600 font-medium">No hay votaciones abiertas en este momento.</p>' +
                        '<p class="text-xs text-slate-500 max-w-lg mx-auto">En cuanto un administrador abra una votación, aparecerá aquí automáticamente en tiempo real sin necesidad de recargar la página.</p>';
                    contenedorAbiertas.appendChild(vacio);
                }
            }

            if (seccionCerradas && contenedorCerradas && contadorCerradas) {
                contadorCerradas.textContent = String(listaCerradas.length);
                contenedorCerradas.innerHTML = "";
                if (listaCerradas.length > 0) {
                    seccionCerradas.classList.remove("hidden");
                    listaCerradas.forEach(function (votacion) {
                        contenedorCerradas.appendChild(crearTarjetaCerrada(votacion));
                    });
                } else {
                    seccionCerradas.classList.add("hidden");
                }
            }
        }

        async function sincronizarListadoDesdeApi() {
            if (!urlListado) {
                return;
            }
            try {
                const respuesta = await fetch(urlListado, {
                    method: "GET",
                    headers: { Accept: "application/json" },
                    credentials: "same-origin",
                });
                if (!respuesta.ok) {
                    return;
                }
                const datos = await respuesta.json();
                if (datos && datos.exito) {
                    renderizarListadoPublico(datos.abiertas, datos.cerradas);
                }
            } catch (_err) {
                // Reintento gestionado por SSE
            }
        }

        function iniciarConexionSseListado() {
            if (!urlEventos || typeof window.EventSource === "undefined") {
                actualizarInsigniaSse(
                    indicadorSse,
                    puntoSse,
                    textoSse,
                    "desconectado",
                    "SSE no soportado"
                );
                return;
            }

            fuenteEventosListado = new EventSource(urlEventos);

            fuenteEventosListado.onopen = function () {
                actualizarInsigniaSse(
                    indicadorSse,
                    puntoSse,
                    textoSse,
                    "conectado",
                    "En vivo · Listado sincronizado con Redis Pub/Sub"
                );
                if (huboDesconexionListado) {
                    huboDesconexionListado = false;
                    sincronizarListadoDesdeApi();
                }
            };

            fuenteEventosListado.addEventListener("actualizacion_listado", function (evento) {
                try {
                    const carga = JSON.parse(evento.data);
                    if (carga && carga.listado) {
                        renderizarListadoPublico(
                            carga.listado.abiertas,
                            carga.listado.cerradas
                        );
                    }
                    // Sincronizar con la sesión del cliente actual para reflejar "Ya participaste"
                    sincronizarListadoDesdeApi();
                } catch (_err) {
                    sincronizarListadoDesdeApi();
                }
            });

            fuenteEventosListado.addEventListener("latido", function () {
                actualizarInsigniaSse(
                    indicadorSse,
                    puntoSse,
                    textoSse,
                    "conectado",
                    "En vivo · Listado sincronizado con Redis Pub/Sub"
                );
            });

            fuenteEventosListado.onerror = function () {
                huboDesconexionListado = true;
                actualizarInsigniaSse(
                    indicadorSse,
                    puntoSse,
                    textoSse,
                    "reconectando",
                    "Reconectando actualizaciones en vivo…"
                );
            };
        }

        iniciarConexionSseListado();

        window.addEventListener("beforeunload", function () {
            if (fuenteEventosListado) {
                fuenteEventosListado.close();
            }
        });
    }

    // Inicializar según la vista activa
    const contenedorDetalle = document.getElementById("aplicacion-votacion");
    if (contenedorDetalle) {
        inicializarDetalleVotacion(contenedorDetalle);
    }

    const contenedorListado = document.getElementById("aplicacion-listado-votaciones");
    if (contenedorListado) {
        inicializarListadoVotaciones(contenedorListado);
    }
})();
