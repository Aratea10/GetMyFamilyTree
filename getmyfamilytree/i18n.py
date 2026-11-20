"""English and Spanish messages for the GetMyFamilyTree command-line interface.

Use stable message keys rather than translating FamilySearch's original data.
"""
from __future__ import annotations

import locale
import os

MESSAGES = {
    "en": {
        "description": "Explore all accessible FamilySearch relatives in every direction.",
        "help_language": "Interface language: auto (system), en (English), es (Spanish)",
        "help_individual": "FamilySearch person ID",
        "help_output": "Output GEDCOM",
        "help_database": "Persistent SQLite checkpoint (defaults to output with .sqlite suffix)",
        "help_max_people": "Pause after this many people; 0 means no cap (potentially enormous)",
        "help_rate_limit": "Maximum HTTP requests per second, passed to FamilySearch session",
        "help_remember": "After a successful login, save credentials in the system keyring",
        "help_forget": "Remove saved FamilySearch credentials and exit",
        "help_progress": "Show progress every N people (default: 1; 0 disables progress)",
        "help_no_notes": "Do not download notes",
        "help_no_sources": "Do not download sources",
        "help_no_memories": "Do not download memory references",
        "help_no_contributors": "Do not download contributor information",
        "error_forget_conflict": "--forget-login cannot be combined with --remember-login",
        "error_forget_keyring": "Cannot access the system keyring to remove credentials.",
        "saved_removed": "Saved FamilySearch login removed.",
        "saved_not_found": "No saved FamilySearch login found.",
        "error_required": "both -i/--individual and -o/--output are required for a download",
        "error_invalid_id": "Invalid FamilySearch ID",
        "error_invalid_limits": "max-people and progress-every must be >= 0; rate-limit must be >= 1",
        "error_checkpoint_origin": (
            "Checkpoint belongs to {existing}, not {requested}. "
            "Choose a different output or database filename."
        ),
        "error_keyring": "System keyring unavailable; please enter FamilySearch credentials.",
        "saved_login_used": "Using FamilySearch login from the system keyring.",
        "saved_login_rejected": "Saved login was not accepted; enter your current credentials.",
        "username_prompt": "Enter FamilySearch username: ",
        "password_prompt": "Enter FamilySearch password: ",
        "error_login": "Could not log in to FamilySearch.",
        "login_saved": "FamilySearch login saved in the system keyring.",
        "login_save_failed": (
            "Could not save login in the system keyring. "
            "You will be prompted next time."
        ),
        "checkpoint": "Checkpoint: {path}",
        "start": (
            "Starting exploration: {done} already explored, "
            "{pending} queued, limit {limit}."
        ),
        "unlimited": "unlimited",
        "exploring": "Exploring {position}{total}: {fid}...",
        "retrieval_failed": "Could not retrieve {fid}; will retry on a future run.",
        "found": (
            "Found: {name} ({fid}) | +{added} new relatives | {pending} queued."
        ),
        "paused": (
            "Exploration paused: {done} explored, {pending} queued, "
            "{failed} unavailable."
        ),
        "complete": (
            "Exploration complete: {done} explored, {pending} queued, "
            "{failed} unavailable."
        ),
        "interrupted": "Paused. Writing GEDCOM snapshot...",
        "building": "Building GEDCOM from {done} retrieved people...",
        "saved_tree": (
            "Saved {people} people and {families} families to {path}. "
            "Pending: {pending}; failed: {failed}."
        ),
        "resume": (
            "Not yet exhaustive. Run the SAME command again to resume "
            "from the SQLite checkpoint."
        ),
    },
    "es": {
        "description": "Explora todos los familiares accesibles en FamilySearch en cualquier dirección.",
        "help_language": "Idioma de la interfaz: auto (sistema), en (inglés), es (español)",
        "help_individual": "ID de la persona en FamilySearch",
        "help_output": "Archivo GEDCOM de salida",
        "help_database": "Base SQLite de progreso (por defecto, junto al GEDCOM)",
        "help_max_people": "Detenerse tras este número de personas; 0 sin límite (puede ser enorme)",
        "help_rate_limit": "Máximo de solicitudes HTTP por segundo a FamilySearch",
        "help_remember": "Guardar el inicio de sesión en el llavero tras autenticarse",
        "help_forget": "Eliminar las credenciales guardadas y salir",
        "help_progress": "Mostrar el progreso cada N personas (1 por defecto; 0 para desactivar)",
        "help_no_notes": "No descargar notas",
        "help_no_sources": "No descargar fuentes",
        "help_no_memories": "No descargar referencias a recuerdos",
        "help_no_contributors": "No descargar información de colaboradores",
        "error_forget_conflict": "No puedes combinar --forget-login con --remember-login",
        "error_forget_keyring": "No se puede acceder al llavero para borrar las credenciales.",
        "saved_removed": "Se ha eliminado el inicio de sesión de FamilySearch guardado.",
        "saved_not_found": "No hay ningún inicio de sesión de FamilySearch guardado.",
        "error_required": "para descargar necesitas -i/--individual y -o/--output",
        "error_invalid_id": "El ID de FamilySearch no es válido",
        "error_invalid_limits": (
            "max-people y progress-every deben ser >= 0; rate-limit debe ser >= 1"
        ),
        "error_checkpoint_origin": (
            "El archivo de progreso corresponde a {existing}, no a {requested}. "
            "Elige otro nombre o ruta para el GEDCOM o la base de datos."
        ),
        "error_keyring": (
            "No se puede acceder al llavero del sistema; "
            "introduce tus credenciales de FamilySearch."
        ),
        "saved_login_used": "Se está usando el inicio de sesión guardado en el llavero.",
        "saved_login_rejected": (
            "El inicio de sesión guardado no es válido; "
            "introduce tus credenciales actuales."
        ),
        "username_prompt": "Usuario de FamilySearch: ",
        "password_prompt": "Contraseña de FamilySearch: ",
        "error_login": "No se ha podido iniciar sesión en FamilySearch.",
        "login_saved": "Inicio de sesión guardado en el llavero del sistema.",
        "login_save_failed": (
            "No se ha podido guardar el inicio de sesión en el llavero. "
            "Se volverán a pedir las credenciales."
        ),
        "checkpoint": "Archivo de progreso: {path}",
        "start": (
            "Iniciando exploración: {done} personas ya exploradas, "
            "{pending} pendientes, límite {limit}."
        ),
        "unlimited": "sin límite",
        "exploring": "Explorando {position}{total}: {fid}...",
        "retrieval_failed": "No se ha podido recuperar {fid}; se reintentará más adelante.",
        "found": (
            "Encontrada: {name} ({fid}) | +{added} familiares nuevos | "
            "{pending} pendientes."
        ),
        "paused": (
            "Exploración pausada: {done} personas exploradas, "
            "{pending} pendientes, {failed} no disponibles."
        ),
        "complete": (
            "Exploración terminada: {done} personas exploradas, "
            "{pending} pendientes, {failed} no disponibles."
        ),
        "interrupted": "Exploración interrumpida. Guardando una copia GEDCOM...",
        "building": "Generando GEDCOM con {done} personas recuperadas...",
        "saved_tree": (
            "Guardadas {people} personas y {families} familias en {path}. "
            "Pendientes: {pending}; errores: {failed}."
        ),
        "resume": (
            "Aún quedan familiares por explorar. Ejecuta el MISMO comando "
            "para continuar desde el archivo SQLite."
        ),
    },
}


def resolve_language(requested="auto", system_locale=None):
    """Choose English or Spanish without requiring installed locale catalogs."""
    if requested in ("en", "es"):
        return requested
    if requested != "auto":
        raise ValueError(f"Unsupported interface language: {requested}")

    if system_locale is None:
        system_locale = next(
            (os.environ[name] for name in ("LC_ALL", "LC_MESSAGES", "LANG")
             if os.environ.get(name)),
            None,
        )
        if not system_locale:
            system_locale = locale.getlocale()[0]

    code = str(system_locale or "").lower().replace("-", "_")
    return "es" if code == "es" or code.startswith("es_") else "en"


def translate(language, message, **values):
    """Format a translated user-facing message with an English fallback."""
    template = MESSAGES.get(language, MESSAGES["en"]).get(
        message, MESSAGES["en"][message]
    )
    return template.format(**values)
