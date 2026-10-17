-- ============================================================================
-- Script Lua: registrar_voto.lua
-- Propósito:
--   Ejecutar de manera 100% atómica la validación de estado, comprobación de
--   opción, verificación de voto duplicado (por sesión y por cuenta de
--   administrador si está autenticado), registro de identidades en el Set de
--   votantes, incremento del contador en el Sorted Set de resultados y
--   publicación del evento en el canal Pub/Sub de la votación y en el canal
--   global de listados públicos.
--
-- KEYS[1] -> <prefijo>:votacion:<id>:datos      (Hash de metadatos de la votación)
-- KEYS[2] -> <prefijo>:votacion:<id>:opciones   (Hash de id_opcion -> texto)
-- KEYS[3] -> <prefijo>:votacion:<id>:resultados (Sorted Set de resultados por opción)
-- KEYS[4] -> <prefijo>:votacion:<id>:votantes   (Set de identidades que ya votaron)
-- KEYS[5] -> <prefijo>:canal:votacion:<id>      (Canal Pub/Sub individual de la votación)
-- KEYS[6] -> <prefijo>:canal:votaciones:global  (Canal Pub/Sub global del listado público)
--
-- ARGV[1] -> id_opcion seleccionada (ej. "opcion_1")
-- ARGV[2] -> identidad de sesión (ej. "sesion:<uuid>")
-- ARGV[3] -> identidad de administrador (ej. "administrador:<uuid>" o "" si es anónimo)
-- ARGV[4] -> mensaje JSON que se publicará en los canales Pub/Sub si el voto es válido
--
-- Retorno:
--   {1, "VOTO_REGISTRADO", "<nuevo_puntaje_opcion>"}
--   {0, "VOTACION_NO_EXISTE"}
--   {0, "VOTACION_NO_ABIERTA", "<estado_actual>"}
--   {0, "OPCION_NO_EXISTE"}
--   {0, "YA_VOTO_SESION"}
--   {0, "YA_VOTO_ADMINISTRADOR"}
-- ============================================================================

local clave_datos = KEYS[1]
local clave_opciones = KEYS[2]
local clave_resultados = KEYS[3]
local clave_votantes = KEYS[4]
local canal_eventos = KEYS[5]
local canal_global = KEYS[6]

local id_opcion = ARGV[1]
local identidad_sesion = ARGV[2]
local identidad_admin = ARGV[3]
local mensaje_evento = ARGV[4]

-- 1. Verificar existencia de la votación
if redis.call('EXISTS', clave_datos) == 0 then
    return {0, 'VOTACION_NO_EXISTE'}
end

-- 2. Verificar que la votación se encuentre en estado "abierta"
local estado = redis.call('HGET', clave_datos, 'estado')
if estado ~= 'abierta' then
    return {0, 'VOTACION_NO_ABIERTA', estado or ''}
end

-- 3. Verificar que la opción elegida exista en el Hash de opciones
if redis.call('HEXISTS', clave_opciones, id_opcion) == 0 then
    return {0, 'OPCION_NO_EXISTE'}
end

-- 4. Verificar si la sesión actual ya registró un voto en esta votación
if redis.call('SISMEMBER', clave_votantes, identidad_sesion) == 1 then
    return {0, 'YA_VOTO_SESION'}
end

-- 5. Si hay un administrador autenticado, verificar si su cuenta ya votó
if identidad_admin ~= '' then
    if redis.call('SISMEMBER', clave_votantes, identidad_admin) == 1 then
        return {0, 'YA_VOTO_ADMINISTRADOR'}
    end
end

-- 6. Registrar las identidades pertinentes en el Set de votantes
redis.call('SADD', clave_votantes, identidad_sesion)
if identidad_admin ~= '' then
    redis.call('SADD', clave_votantes, identidad_admin)
end

-- 7. Incrementar en 1 el puntaje de la opción en el Sorted Set de resultados
local nuevo_puntaje = redis.call('ZINCRBY', clave_resultados, 1, id_opcion)

-- 8. Publicar notificación de actualización en el canal individual y en el canal global
redis.call('PUBLISH', canal_eventos, mensaje_evento)
if canal_global and canal_global ~= '' then
    redis.call('PUBLISH', canal_global, mensaje_evento)
end

return {1, 'VOTO_REGISTRADO', tostring(nuevo_puntaje)}
