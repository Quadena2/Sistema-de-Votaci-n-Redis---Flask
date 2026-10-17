"""
Excepciones de dominio de la capa de negocio.

Permiten comunicar de forma explícita y desacoplada las causas de error hacia
la capa de presentación o los comandos CLI sin acoplar la lógica a códigos HTTP.
"""


class ErrorNegocio(Exception):
    """Excepción base para todas las reglas y errores de negocio."""

    def __init__(self, mensaje: str, codigo: str = "ERROR_NEGOCIO") -> None:
        super().__init__(mensaje)
        self.mensaje: str = mensaje
        self.codigo: str = codigo


class ErrorValidacion(ErrorNegocio):
    """Error lanzado cuando los datos de entrada incumplen reglas de formato o negocio."""

    def __init__(self, mensaje: str, codigo: str = "DATOS_INVALIDOS") -> None:
        super().__init__(mensaje, codigo)


class ErrorAutenticacion(ErrorNegocio):
    """Error lanzado ante credenciales inválidas o código de invitación incorrecto."""

    def __init__(self, mensaje: str, codigo: str = "AUTENTICACION_FALLIDA") -> None:
        super().__init__(mensaje, codigo)


class ErrorAutorizacion(ErrorNegocio):
    """Error lanzado cuando un usuario sin permisos intenta realizar una acción protegida."""

    def __init__(self, mensaje: str, codigo: str = "NO_AUTORIZADO") -> None:
        super().__init__(mensaje, codigo)


class ErrorLimiteIntentos(ErrorNegocio):
    """Error lanzado cuando se supera el número máximo de intentos de inicio de sesión."""

    def __init__(self, mensaje: str, codigo: str = "LIMITE_INTENTOS_EXCEDIDO") -> None:
        super().__init__(mensaje, codigo)


class ErrorRecursoNoEncontrado(ErrorNegocio):
    """Error lanzado cuando una votación o cuenta solicitada no existe."""

    def __init__(self, mensaje: str, codigo: str = "NO_ENCONTRADO") -> None:
        super().__init__(mensaje, codigo)


class ErrorConflictoEstado(ErrorNegocio):
    """Error lanzado cuando se intenta una transición de estado no permitida."""

    def __init__(self, mensaje: str, codigo: str = "CONFLICTO_ESTADO") -> None:
        super().__init__(mensaje, codigo)


class ErrorVotoRechazado(ErrorNegocio):
    """Error lanzado cuando el script atómico rechaza el registro de un voto."""

    def __init__(self, mensaje: str, codigo: str = "VOTO_RECHAZADO") -> None:
        super().__init__(mensaje, codigo)


class ErrorAlmacenamientoNoDisponible(ErrorNegocio):
    """Error lanzado cuando la capa de datos reporta indisponibilidad de Redis."""

    def __init__(
        self,
        mensaje: str = "El servicio de almacenamiento Redis no está disponible en este momento.",
        codigo: str = "REDIS_NO_DISPONIBLE",
    ) -> None:
        super().__init__(mensaje, codigo)
