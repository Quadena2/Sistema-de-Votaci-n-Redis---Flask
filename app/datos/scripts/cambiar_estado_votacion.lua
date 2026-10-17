-- ============================================================================
-- Script Lua: cambiar_estado_votacion.lua
-- Propósito:
--   Ejecutar de manera atómica las transiciones del ciclo de vida de una
--   votación ("borrador" -> "abierta" y "abierta" -> "cerrada"), evitando
--   condiciones de carrera frente a votos concurrentes e impidiendo reabrir
--   una votación cerrada.
--
-- KEYS[1] -> <prefijo>:votacion:<id>:datos      (Hash de metadatos de la votación)
-- KEYS[2] -> <prefijo>:canal:votacion:<id>      (Canal Pub/Sub individual de la votación)
-- KEYS[3] -> <prefijo>:canal:votaciones:global  (Canal Pub/Sub global del listado público)
--
-- ARGV[1] -> estado_esperado ("borrador" para abrir, "abierta" para cerrar)
-- ARGV[2] -> nuevo_estado    ("abierta" o "cerrada")
-- ARGV[3] -> campo_fecha     ("fecha_apertura" o "fecha_cierre")
-- ARGV[4] -> valor_fecha     (marca temporal ISO 8601)
-- ARGV[5] -> mensaje_evento  (JSON para publicar en los canales Pub/Sub)
--
-- Retorno:
--   {1, "ESTADO_ACTUALIZADO", "<nuevo_estado>"}
--   {0, "VOTACION_NO_EXISTE"}
--   {0, "TRANSICION_INVALIDA", "<estado_actual>"}
-- ============================================================================

local clave_datos = KEYS[1]
local canal_eventos = KEYS[2]
local canal_global = KEYS[3]

local estado_esperado = ARGV[1]
local nuevo_estado = ARGV[2]
local campo_fecha = ARGV[3]
local valor_fecha = ARGV[4]
local mensaje_evento = ARGV[5]

-- 1. Comprobar que la votación exista
if redis.call('EXISTS', clave_datos) == 0 then
    return {0, 'VOTACION_NO_EXISTE'}
end

-- 2. Comprobar que el estado actual coincida con el estado previo requerido
local estado_actual = redis.call('HGET', clave_datos, 'estado')
if estado_actual ~= estado_esperado then
    return {0, 'TRANSICION_INVALIDA', estado_actual or ''}
end

-- 3. Actualizar atómicamente el estado y la fecha correspondiente
redis.call('HSET', clave_datos, 'estado', nuevo_estado, campo_fecha, valor_fecha)

-- 4. Notificar el cambio de estado al canal individual y al canal global de listados
redis.call('PUBLISH', canal_eventos, mensaje_evento)
if canal_global and canal_global ~= '' then
    redis.call('PUBLISH', canal_global, mensaje_evento)
end

return {1, 'ESTADO_ACTUALIZADO', nuevo_estado}
