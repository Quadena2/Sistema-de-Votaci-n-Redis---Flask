-- ============================================================================
-- Script Lua: registrar_administrador.lua
-- Propósito:
--   Garantizar el registro atómico de una cuenta de administrador en Redis,
--   evitando condiciones de carrera (TOCTOU) cuando dos solicitudes concurrentes
--   intentan registrar el mismo nombre de usuario normalizado al mismo tiempo.
--
-- KEYS[1] -> <prefijo>:administradores:usuario:<nombre_normalizado> (String índice usuario -> id)
-- KEYS[2] -> <prefijo>:administrador:<id>:datos                     (Hash de datos del administrador)
-- KEYS[3] -> <prefijo>:administradores:indice                       (Set global de IDs de administradores)
--
-- ARGV[1] -> id del administrador (UUID)
-- ARGV[2] -> nombre_usuario (con formato de visualización original)
-- ARGV[3] -> nombre_normalizado (en minúsculas)
-- ARGV[4] -> hash_contrasena (generado con Werkzeug)
-- ARGV[5] -> fecha_creacion (ISO 8601)
--
-- Retorno:
--   {1, "ADMINISTRADOR_REGISTRADO"} si se creó con éxito
--   {0, "USUARIO_DUPLICADO"}        si el nombre normalizado ya está ocupado
-- ============================================================================

local clave_indice_usuario = KEYS[1]
local clave_datos_admin = KEYS[2]
local clave_conjunto_admins = KEYS[3]

local id_admin = ARGV[1]
local nombre_usuario = ARGV[2]
local nombre_normalizado = ARGV[3]
local hash_contrasena = ARGV[4]
local fecha_creacion = ARGV[5]

-- 1. Comprobar si ya existe una cuenta con este nombre de usuario normalizado
if redis.call('EXISTS', clave_indice_usuario) == 1 then
    return {0, 'USUARIO_DUPLICADO'}
end

-- 2. Asociar el nombre de usuario normalizado con el identificador único
redis.call('SET', clave_indice_usuario, id_admin)

-- 3. Almacenar los campos de la cuenta en un Hash de Redis
redis.call(
    'HSET',
    clave_datos_admin,
    'id', id_admin,
    'nombre_usuario', nombre_usuario,
    'nombre_normalizado', nombre_normalizado,
    'hash_contrasena', hash_contrasena,
    'fecha_creacion', fecha_creacion
)

-- 4. Registrar el identificador en el Set índice de administradores
redis.call('SADD', clave_conjunto_admins, id_admin)

return {1, 'ADMINISTRADOR_REGISTRADO'}
