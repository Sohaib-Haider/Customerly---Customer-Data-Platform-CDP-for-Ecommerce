from django.apps import AppConfig


class MappingConfig(AppConfig):
    """LLM-assisted column mapping of store uploads onto the canonical schema."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.mapping"
    label = "mapping"
    verbose_name = "Column mapping"
