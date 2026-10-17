# Reglas Permanentes del Proyecto (`AGENTS.md`)

Este documento define las directrices arquitectónicas, técnicas y pedagógicas obligatorias para el proyecto **Sistema de Votaciones en Tiempo Real con Flask, Redis y Tailwind CSS**. Cualquier modificación o ampliación debe respetar estrictamente estas reglas.

---

## 1. Propósito Académico

- El objetivo principal del sistema es **demostrar de forma didáctica y transparente el uso de estructuras de datos, atomicidad con Lua y mensajería Pub/Sub en Redis Open Source** dentro de una aplicación web real.
- El código debe priorizar la **legibilidad, claridad y trazabilidad** sobre abstracciones empresariales innecesarias.

---

## 2. Arquitectura Estricta de Tres Capas

La aplicación sigue una separación estricta en tres capas con dirección única de dependencias:

```text
Presentación (Flask / Blueprints / Jinja2 / SSE)
      ↓
Negocio (Servicios de dominio y validaciones)
      ↓
Datos (Repositorios / Cliente Redis / Scripts Lua / Pub/Sub)
      ↓
Redis Open Source
```

### Reglas por capa:
1. **Capa de Presentación (`app/presentacion/`)**:
   - Gestiona rutas HTTP, formularios, sesiones de Flask, tokens CSRF, decoradores de autorización, plantillas Jinja2, respuestas JSON y transmisión Server-Sent Events (SSE).
   - **Prohibido**: Ejecutar comandos de Redis directamente, instanciar clientes de Redis o implementar reglas centrales de negocio.
2. **Capa de Negocio (`app/negocio/`)**:
   - Gestiona registro y autenticación de administradores (`Werkzeug`), validación del código de invitación (`hmac.compare_digest`), límite de intentos de inicio de sesión, reglas de creación/apertura/cierre de votaciones, validación de preguntas y opciones, identificación de votantes (`sesion:<id>` y `administrador:<id>`) y cálculo de porcentajes.
   - **Prohibido**: Acceder a `request`, `session` o `render_template` de Flask, o invocar directamente el cliente de Redis.
3. **Capa de Datos (`app/datos/`)**:
   - Es la **única capa autorizada** para comunicarse con Redis.
   - Gestiona la conexión centralizada, las estructuras de datos (`Hashes`, `Sets`, `Sorted Sets`, `Strings`), la carga y ejecución de scripts Lua (`registrar_administrador.lua`, `registrar_voto.lua`, `cambiar_estado_votacion.lua`) y las suscripciones a canales `Pub/Sub`.

---

## 3. Redis como Único Sistema de Almacenamiento

- **Almacenamiento exclusivo**: Todo el estado persistente o compartido (cuentas de administradores, índices, votaciones, opciones, resultados, registro de votantes e intentos de inicio de sesión) debe residir en Redis.
- **Sin bases de datos adicionales**: Está prohibido añadir SQLite, PostgreSQL, MySQL, MariaDB, MongoDB, SQLAlchemy, ni archivos locales JSON/CSV como base de datos.
- **Sin estado global en memoria Python**: No se deben guardar resultados oficiales ni listas globales de clientes SSE en variables globales de Python.
- **Atomicidad obligatoria**:
  - El registro de votos debe realizarse en una sola operación atómica mediante `app/datos/scripts/registrar_voto.lua`. No se permite verificar condiciones en Python y luego incrementar contadores por separado.
  - El registro de administradores debe prevenir condiciones de carrera en nombres de usuario mediante `app/datos/scripts/registrar_administrador.lua`.
  - El cambio de estado de una votación (`borrador` → `abierta` → `cerrada`) debe ejecutarse atómicamente mediante `app/datos/scripts/cambiar_estado_votacion.lua`.

---

## 4. Uso Seguro del Contenedor Redis Existente

- **Reutilizar el contenedor existente**: Redis ya se ejecuta en un contenedor Docker local. No se deben crear contenedores adicionales ni modificar volúmenes o puertos existentes sin necesidad.
- **Prohibición absoluta de borrado global**: **NUNCA** ejecutar `FLUSHALL` ni `FLUSHDB` en el código de la aplicación, en comandos CLI ni en las pruebas automatizadas.
- **Aislamiento por prefijo**: Todas las claves deben construirse con el prefijo configurable (`PREFIJO_REDIS`, por ejemplo `votacion`). Las pruebas deben utilizar un prefijo exclusivo (como `votacion_test`) y limpiar únicamente las claves creadas bajo dicho prefijo.

---

## 5. Convenciones de Nomenclatura y Estilo

- **Idioma español**: Carpetas, archivos, módulos, clases, métodos, funciones, variables, claves de Redis y comentarios técnicos deben estar escritos en español.
- **Sin tildes en nombres físicos**: Los nombres de archivos, carpetas e identificadores de código no deben llevar tildes ni caracteres especiales (ejemplo: `configuracion.py`, `servicio_votaciones.py`), pero los textos visibles en la interfaz, comentarios y documentación deben utilizar ortografía española correcta con tildes.
- **Tipado y claridad**: Añadir anotaciones de tipos (`type hints`) en las funciones y métodos principales, mantener funciones cohesivas aplicando el principio de responsabilidad única (SRP) y evitar patrones o interfaces artificiales.

---

## 6. Seguridad y Restricciones Tecnológicas

- **Contraseñas**: Almacenar únicamente hashes generados con `werkzeug.security.generate_password_hash` y verificar con `check_password_hash`. Prohibido guardar contraseñas en texto plano o exponer hashes en respuestas HTTP/JSON o en la sesión.
- **Sesiones y CSRF**: Mantener cookies con `HttpOnly` y `SameSite=Lax`, limpiar o regenerar la sesión al autenticar/salir y exigir validación de token CSRF en todas las peticiones `POST`.
- **Frontend**: Utilizar HTML5, Jinja2, CSS propio (`estilos.css`), JavaScript puro (`votacion.js`, `administracion.js`) y Tailwind CSS vía CDN con fines académicos. No introducir Node.js, npm, React, Vue, Angular ni WebSockets.
