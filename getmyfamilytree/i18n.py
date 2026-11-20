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
        "help_output": "Output GEDCOM filename (.ged) or export folder",
        "help_database": "Persistent SQLite checkpoint (defaults to output with .sqlite suffix)",
        "help_max_people": "Maximum people in the selected scope; 0 removes the safety cap",
        "help_scope": "Download scope: full, ancestors, descendants, or family",
        "help_generations": "Number of generations from the starting person (ancestors/descendants)",
        "help_distance": "Maximum relationship links from the starting person (family)",
        "error_generations": "ancestors and descendants require --generations N (N >= 0); do not use --distance",
        "error_distance": "family requires --distance N (N >= 0); do not use --generations",
        "error_full_options": "full does not accept --generations or --distance",
        "error_scope_origin": "Cannot find the starting FamilySearch ID in this checkpoint",
        "scope_full": "Complete connected tree",
        "scope_ancestors": "Ancestors",
        "scope_descendants": "Descendants",
        "scope_family": "Extended family",
        "scope_start": "Exploring {scope}; person limit {limit}. Reusing cached records.",
        "scope_person": (
            "Explored {index}{limit}: {name} ({fid}), depth {depth} | "
            "+{added} newly queued | {queued} to visit"
        ),
        "scope_finish": (
            "Scope: {selected} people, {pending} queued, {failed} unavailable; "
            "{reused} person records reused from cache."
        ),
        "help_rate_limit": "Maximum HTTP requests per second, passed to FamilySearch session",
        "help_remember": "After a successful login, save credentials in the system keyring",
        "help_forget": "Remove saved FamilySearch credentials and exit",
        "help_progress": "Show progress every N people (default: 1; 0 disables progress)",
        "help_report_only": "Regenerate the person-by-person report from SQLite without logging in",
        "help_csv": "Also export seven separate CSV review tables (optional)",
        "error_output_path": "Output must be a .ged filename or a folder",
        "person_report_saved": (
            "Created one review document for {people} people "
            "and {events} events: {path}"
        ),
        "error_report_requires_output": "--report-only requires -o/--output",
        "error_report_database_missing": "Saved checkpoint not found: {path}",
        "report_saved": (
            "Created review tables for {people} people, {events} events, "
            "{sources} source links, {notes} notes, and {memories} memory links: {path}"
        ),
        "report_review": "Review checklist (annotations preserved): {path}",
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
        "gedcom_loading_batch": (
            "Processing people {start}-{end} of {total}..."
        ),
        "gedcom_people_progress": (
            "People processed: {count}/{total}"
        ),
        "gedcom_extra_start": (
            "Processing {total} additional tasks..."
        ),
        "gedcom_extra_progress": (
            "Tasks completed: {count}/{total}"
        ),
        "gedcom_done": (
            "GEDCOM successfully generated: {path}"
        ),
        "report_building": "Generating review document...",
    },
    "es": {
        "description": "Explora todos los familiares accesibles en FamilySearch en cualquier dirección.",
        "help_language": "Idioma de la interfaz: auto (sistema), en (inglés), es (español)",
        "help_individual": "ID de la persona en FamilySearch",
        "help_output": "Archivo GEDCOM (.ged) o carpeta de exportación",
        "help_database": "Base SQLite de progreso (por defecto, junto al GEDCOM)",
        "help_max_people": "Máximo de personas en el modo seleccionado; 0 elimina el límite",
        "help_scope": "Modo de descarga: full, ancestors, descendants o family",
        "help_generations": "Número de generaciones desde la persona inicial (antepasados/descendientes)",
        "help_distance": "Distancia máxima de vínculos familiares (familia ampliada)",
        "error_generations": "ancestors y descendants requieren --generations N (N >= 0); no utilices --distance",
        "error_distance": "family requiere --distance N (N >= 0); no utilices --generations",
        "error_full_options": "full no admite --generations ni --distance",
        "error_scope_origin": "No se encuentra el ID de la persona inicial en la base de datos",
        "scope_full": "Árbol completo",
        "scope_ancestors": "Antepasados",
        "scope_descendants": "Descendientes",
        "scope_family": "Familia ampliada",
        "scope_start": "Explorando {scope}; límite {limit} personas. Reutilizando datos guardados.",
        "scope_person": (
            "Explorada {index}{limit}: {name} ({fid}), distancia {depth} | "
            "+{added} en cola | {queued} por visitar"
        ),
        "scope_finish": (
            "Modo: {selected} personas, {pending} pendientes, {failed} no disponibles; "
            "{reused} fichas recuperadas de la base local."
        ),
        "help_rate_limit": "Máximo de solicitudes HTTP por segundo a FamilySearch",
        "help_remember": "Guardar el inicio de sesión en el llavero tras autenticarse",
        "help_forget": "Eliminar las credenciales guardadas y salir",
        "help_progress": "Mostrar el progreso cada N personas (1 por defecto; 0 para desactivar)",
        "help_report_only": (
            "Regenerar las fichas por persona desde SQLite sin iniciar sesión"
        ),
        "help_csv": "Generar también las siete tablas CSV por separado (opcional)",
        "error_output_path": "La salida debe ser un archivo .ged o una carpeta",
        "person_report_saved": (
            "Creado un único documento de revisión para {people} personas "
            "y {events} acontecimientos: {path}"
        ),
        "error_report_requires_output": "--report-only necesita -o/--output",
        "error_report_database_missing": "No se encuentra el progreso guardado: {path}",
        "report_saved": (
            "Creadas tablas de revisión para {people} personas, {events} acontecimientos, "
            "{sources} referencias de fuentes, {notes} notas y {memories} recuerdos: {path}"
        ),
        "report_review": "Lista de verificación (anotaciones conservadas): {path}",
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
        "gedcom_loading_batch": (
            "Procesando personas {start}-{end} de {total}..."
        ),
        "gedcom_people_progress": (
            "Personas procesadas: {count}/{total}"
        ),
        "gedcom_extra_start": (
            "Procesando {total} tareas adicionales..."
        ),
        "gedcom_extra_progress": (
            "Tareas completadas: {count}/{total}"
        ),
        "gedcom_done": (
            "GEDCOM generado correctamente: {path}"
        ),
        "report_building": "Generando documento de revisión...",
    },
}


def resolve_language(
    requested="auto", system_locale=None
):
    """Choose English or Spanish without requiring installed locale catalogs."""
    if requested in ("en", "es"):
        return requested
    if requested != "auto":
        raise ValueError(
            f"Unsupported interface language: {requested}"
        )

    if system_locale is None:
        system_locale = next(
            (
                os.environ[name]
                for name in (
                    "LC_ALL",
                    "LC_MESSAGES",
                    "LANG",
                )
                if os.environ.get(name)
            ),
            None,
        )
        if not system_locale:
            system_locale = (
                locale.getlocale()[0]
            )

    code = (
        str(system_locale or "")
        .lower()
        .replace("-", "_")
    )
    return (
        "es"
        if code == "es"
        or code.startswith("es_")
        else "en"
    )


def translate(
    language, message, **values
):
    """Format a translated user-facing message with an English fallback."""
    template = MESSAGES.get(
        language, MESSAGES["en"]
    ).get(
        message, MESSAGES["en"][message]
    )
    return template.format(**values)
