/**
 * Interacciones de interfaz para el panel administrativo y el formulario de
 * creación de votaciones (administracion.js).
 *
 * Responsabilidades:
 * 1. Sincronizar en tiempo real el Panel de Administración (`#aplicacion-panel-admin`)
 *    mediante Server-Sent Events (SSE) y `/administracion/api/votaciones` para que
 *    el contador de votos emitidos y el estado de cada votación se actualicen al
 *    instante cuando cualquier usuario vota.
 * 2. Gestionar dinámicamente entre 2 y 10 campos de opciones en `crear_votacion.html`.
 * 3. Solicitar confirmación en acciones administrativas sensibles (cerrar / reiniciar).
 */

(function () {
    "use strict";

    const MINIMO_OPCIONES = 2;
    const MAXIMO_OPCIONES = 10;

    const listaOpciones = document.getElementById("lista-opciones-crear");
    const botonAgregar = document.getElementById("boton-agregar-opcion");
    const contadorOpciones = document.getElementById("contador-opciones");

    /**
     * Recalcula la numeración visual (#1, #2, ...) y habilita/deshabilita
     * los botones según los límites de 2 a 10 opciones.
     */
    function actualizarEstadoOpciones() {
        if (!listaOpciones) {
            return;
        }
        const filas = listaOpciones.querySelectorAll(".fila-opcion");
        const total = filas.length;

        filas.forEach(function (fila, indice) {
            const numero = indice + 1;
            const etiqueta = fila.querySelector(".etiqueta-indice-opcion");
            const campo = fila.querySelector('input[name="opciones"]');
            const botonQuitar = fila.querySelector(".boton-eliminar-opcion");

            if (etiqueta) {
                etiqueta.textContent = "#" + numero;
            }
            if (campo) {
                campo.placeholder = "Escribe el texto de la opción " + numero;
            }
            if (botonQuitar) {
                botonQuitar.disabled = total <= MINIMO_OPCIONES;
                botonQuitar.classList.toggle("opacity-40", total <= MINIMO_OPCIONES);
                botonQuitar.classList.toggle("cursor-not-allowed", total <= MINIMO_OPCIONES);
            }
        });

        if (contadorOpciones) {
            contadorOpciones.textContent = total + " / " + MAXIMO_OPCIONES + " opciones";
        }
        if (botonAgregar) {
            botonAgregar.disabled = total >= MAXIMO_OPCIONES;
            botonAgregar.classList.toggle("opacity-50", total >= MAXIMO_OPCIONES);
            botonAgregar.classList.toggle("cursor-not-allowed", total >= MAXIMO_OPCIONES);
        }
    }

    /**
     * Crea una nueva fila de opción para el formulario de creación.
     */
    function crearFilaOpcion(numero) {
        const div = document.createElement("div");
        div.className = "fila-opcion flex items-center gap-2";

        const span = document.createElement("span");
        span.className =
            "etiqueta-indice-opcion w-8 text-xs font-mono font-bold text-slate-500 text-center";
        span.textContent = "#" + numero;

        const input = document.createElement("input");
        input.type = "text";
        input.name = "opciones";
        input.maxLength = 120;
        input.placeholder = "Escribe el texto de la opción " + numero;
        input.className =
            "flex-1 rounded-xl border border-slate-300 px-3.5 py-2 text-sm focus:border-indigo-600 focus:ring-2 focus:ring-indigo-600/20 outline-none transition";

        const boton = document.createElement("button");
        boton.type = "button";
        boton.className =
            "boton-eliminar-opcion text-xs font-semibold text-slate-400 hover:text-red-600 px-2.5 py-2 rounded-lg hover:bg-red-50 transition";
        boton.textContent = "Quitar";

        div.appendChild(span);
        div.appendChild(input);
        div.appendChild(boton);
        return div;
    }

    if (listaOpciones && botonAgregar) {
        botonAgregar.addEventListener("click", function () {
            const totalActual = listaOpciones.querySelectorAll(".fila-opcion").length;
            if (totalActual >= MAXIMO_OPCIONES) {
                return;
            }
            const nuevaFila = crearFilaOpcion(totalActual + 1);
            listaOpciones.appendChild(nuevaFila);
            actualizarEstadoOpciones();
            const campo = nuevaFila.querySelector("input");
            if (campo) {
                campo.focus();
            }
        });

        listaOpciones.addEventListener("click", function (evento) {
            const botonQuitar = evento.target.closest(".boton-eliminar-opcion");
            if (!botonQuitar) {
                return;
            }
            const filas = listaOpciones.querySelectorAll(".fila-opcion");
            if (filas.length <= MINIMO_OPCIONES) {
                return;
            }
            const fila = botonQuitar.closest(".fila-opcion");
            if (fila) {
                fila.remove();
                actualizarEstadoOpciones();
            }
        });

        actualizarEstadoOpciones();
    }

    // Confirmación para acciones administrativas sensibles (cerrar / reiniciar)
    const formulariosConfirmacion = document.querySelectorAll(".form-confirmar-accion");
    formulariosConfirmacion.forEach(function (formulario) {
        formulario.addEventListener("submit", function (evento) {
            const mensaje =
                formulario.getAttribute("data-mensaje") ||
                "¿Estás seguro de ejecutar esta acción?";
            if (!window.confirm(mensaje)) {
                evento.preventDefault();
            }
        });
    });

    // =========================================================================
    // Actualización en tiempo real del Panel de Administración mediante SSE
    // =========================================================================

    const contenedorPanel = document.getElementById("aplicacion-panel-admin");
    if (contenedorPanel) {
        const urlPanelApi = contenedorPanel.dataset.urlPanelApi;
        const urlEventos = contenedorPanel.dataset.urlEventos;

        const indicadorSse = document.getElementById("indicador-sse-panel");
        const puntoSse = document.getElementById("punto-sse-panel");
        const textoSse = document.getElementById("texto-sse-panel");

        const resumenTotal = document.getElementById("resumen-total");
        const resumenBorrador = document.getElementById("resumen-borrador");
        const resumenAbiertas = document.getElementById("resumen-abiertas");
        const resumenCerradas = document.getElementById("resumen-cerradas");
        const resumenVotosTotales = document.getElementById("resumen-votos-totales");
        const tarjetaResumenVotos = document.getElementById("tarjeta-resumen-votos");

        let fuenteEventosPanel = null;
        let huboDesconexionPanel = false;

        function actualizarInsigniaPanelSse(estado, mensaje) {
            if (!indicadorSse || !puntoSse || !textoSse) {
                return;
            }
            textoSse.textContent = mensaje;
            indicadorSse.className =
                "inline-flex items-center gap-2 text-xs font-medium px-3.5 py-1.5 rounded-full border transition ";
            if (estado === "conectado") {
                indicadorSse.className += "bg-emerald-50 border-emerald-300 text-emerald-800";
                puntoSse.className = "w-2 h-2 rounded-full bg-emerald-500 animate-pulse";
            } else if (estado === "reconectando") {
                indicadorSse.className += "bg-amber-50 border-amber-300 text-amber-800";
                puntoSse.className = "w-2 h-2 rounded-full bg-amber-500 animate-ping";
            } else {
                indicadorSse.className += "bg-red-50 border-red-300 text-red-800";
                puntoSse.className = "w-2 h-2 rounded-full bg-red-500";
            }
        }

        function aplicarActualizacionPanel(datos) {
            if (!datos || !datos.exito) {
                return;
            }

            if (datos.resumen) {
                if (resumenTotal) {
                    resumenTotal.textContent = String(datos.resumen.total);
                }
                if (resumenBorrador) {
                    resumenBorrador.textContent = String(datos.resumen.borrador);
                }
                if (resumenAbiertas) {
                    resumenAbiertas.textContent = String(datos.resumen.abiertas);
                }
                if (resumenCerradas) {
                    resumenCerradas.textContent = String(datos.resumen.cerradas);
                }
                if (resumenVotosTotales) {
                    const valorAnterior = resumenVotosTotales.textContent.trim();
                    const valorNuevo = String(datos.resumen.votos_totales);
                    resumenVotosTotales.textContent = valorNuevo;
                    if (valorAnterior !== valorNuevo && tarjetaResumenVotos) {
                        tarjetaResumenVotos.classList.add("ring-2", "ring-emerald-400");
                        setTimeout(function () {
                            tarjetaResumenVotos.classList.remove("ring-2", "ring-emerald-400");
                        }, 900);
                    }
                }
            }

            if (!Array.isArray(datos.votaciones)) {
                return;
            }

            const filasActuales = document.querySelectorAll(".fila-votacion-panel");
            if (filasActuales.length !== datos.votaciones.length) {
                window.location.reload();
                return;
            }

            let requiereRecargaPorCambioEstado = false;

            datos.votaciones.forEach(function (votacion) {
                const fila = document.querySelector(
                    '.fila-votacion-panel[data-id-votacion="' + votacion.id + '"]'
                );
                if (!fila) {
                    requiereRecargaPorCambioEstado = true;
                    return;
                }

                if (fila.dataset.estado !== votacion.estado) {
                    requiereRecargaPorCambioEstado = true;
                    return;
                }

                const nodoContador = fila.querySelector(".contador-votos-votacion");
                const cajaVotos = fila.querySelector(".caja-votos-fila");
                if (nodoContador) {
                    const previo = nodoContador.textContent.trim();
                    const actual = String(votacion.total_votos);
                    if (previo !== actual) {
                        nodoContador.textContent = actual;
                        if (cajaVotos) {
                            cajaVotos.classList.remove("actualizado-reciente");
                            void cajaVotos.offsetWidth;
                            cajaVotos.classList.add("actualizado-reciente");
                        }
                    }
                }

                const formReiniciar = fila.querySelector(".form-reiniciar-votacion");
                if (formReiniciar) {
                    formReiniciar.classList.toggle("hidden", votacion.total_votos === 0);
                }

                const insigniaYaVoto = fila.querySelector(".insignia-ya-voto-admin");
                if (insigniaYaVoto && votacion.participacion) {
                    insigniaYaVoto.classList.toggle(
                        "hidden",
                        !Boolean(votacion.participacion.ya_voto)
                    );
                }
            });

            if (requiereRecargaPorCambioEstado) {
                window.location.reload();
            }
        }

        async function sincronizarPanelDesdeApi() {
            if (!urlPanelApi) {
                return;
            }
            try {
                const respuesta = await fetch(urlPanelApi, {
                    method: "GET",
                    headers: { Accept: "application/json" },
                    credentials: "same-origin",
                });
                if (!respuesta.ok) {
                    return;
                }
                const datos = await respuesta.json();
                aplicarActualizacionPanel(datos);
            } catch (_err) {
                // La reconexión SSE volverá a sincronizar
            }
        }

        if (urlEventos && typeof window.EventSource !== "undefined") {
            fuenteEventosPanel = new EventSource(urlEventos);

            fuenteEventosPanel.onopen = function () {
                actualizarInsigniaPanelSse(
                    "conectado",
                    "En vivo · Contadores sincronizados con Redis"
                );
                if (huboDesconexionPanel) {
                    huboDesconexionPanel = false;
                    sincronizarPanelDesdeApi();
                }
            };

            fuenteEventosPanel.addEventListener("actualizacion_listado", function () {
                sincronizarPanelDesdeApi();
            });

            fuenteEventosPanel.addEventListener("latido", function () {
                actualizarInsigniaPanelSse(
                    "conectado",
                    "En vivo · Contadores sincronizados con Redis"
                );
            });

            fuenteEventosPanel.onerror = function () {
                huboDesconexionPanel = true;
                actualizarInsigniaPanelSse("reconectando", "Reconectando panel en vivo…");
            };

            window.addEventListener("beforeunload", function () {
                if (fuenteEventosPanel) {
                    fuenteEventosPanel.close();
                }
            });
        }
    }
})();
