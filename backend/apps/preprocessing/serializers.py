"""Strict per-step configuration, preserving the existing pipeline defaults."""
import json
import math
from rest_framework import serializers
from core.api import StrictSerializer, validate_table
from .pipeline import default_config


class StepSerializer(StrictSerializer):
    enabled = serializers.BooleanField(required=False)


class ColumnsStepSerializer(StepSerializer):
    columns = serializers.ListField(child=serializers.CharField(max_length=255), allow_null=True, required=False, max_length=200)


class RequiredSerializer(StepSerializer):
    required_columns = serializers.ListField(child=serializers.CharField(max_length=255), required=False, max_length=200)
    raise_on_missing = serializers.BooleanField(required=False)


class FiniteFloatField(serializers.FloatField):
    def to_internal_value(self, data):
        value = super().to_internal_value(data)
        if not math.isfinite(value):
            raise serializers.ValidationError("Use a finite number.")
        return value


class InferSerializer(StepSerializer):
    categorical_threshold = FiniteFloatField(min_value=0, max_value=1, required=False)


class DuplicateSerializer(StepSerializer):
    subset = serializers.ListField(child=serializers.CharField(max_length=255), allow_null=True, required=False, max_length=200)
    keep = serializers.JSONField(required=False)

    def validate_keep(self, value):
        if value is not False and value not in ("first", "last"):
            raise serializers.ValidationError("Choose first, last, or false.")
        return value


class TypesSerializer(StepSerializer):
    type_map = serializers.DictField(child=serializers.ChoiceField(choices=["int", "float", "str", "datetime", "category", "bool"]), required=False)


class DatesSerializer(ColumnsStepSerializer):
    formats = serializers.ListField(child=serializers.CharField(max_length=100), allow_null=True, required=False, max_length=10)


class MissingSerializer(ColumnsStepSerializer):
    strategy = serializers.ChoiceField(choices=["type_aware", "drop", "mean", "median", "mode", "constant"], required=False)
    fill_value = serializers.JSONField(required=False, allow_null=True)

    def validate_fill_value(self, value):
        if isinstance(value, (dict, list)) or isinstance(value, float) and not math.isfinite(value):
            raise serializers.ValidationError("Use a finite scalar value.")
        return value


class OutlierSerializer(ColumnsStepSerializer):
    method = serializers.ChoiceField(choices=["iqr", "zscore"], required=False)
    action = serializers.ChoiceField(choices=["cap", "remove", "flag"], required=False)
    z_threshold = FiniteFloatField(min_value=0.000001, required=False)


STEP_SERIALIZERS = {
    "clean_column_names": StepSerializer,
    "trim_whitespace": ColumnsStepSerializer,
    "validate_required_columns": RequiredSerializer,
    "infer_column_types": InferSerializer,
    "remove_constant_columns": StepSerializer,
    "remove_duplicates": DuplicateSerializer,
    "fix_data_types": TypesSerializer,
    "parse_dates": DatesSerializer,
    "handle_missing_values": MissingSerializer,
    "handle_outliers": OutlierSerializer,
}


def validate_config(value):
    if not isinstance(value, dict):
        raise serializers.ValidationError("Config must be an object.")
    unknown = set(value) - set(STEP_SERIALIZERS) - {"generate_report"}
    if unknown:
        raise serializers.ValidationError({key: "Unknown preprocessing step." for key in sorted(unknown)})
    config = default_config()
    for step, options in value.items():
        if step == "generate_report":
            serializers.BooleanField().run_validation(options)
            continue  # The API always returns the report.
        serializer = STEP_SERIALIZERS[step](data=options)
        serializer.is_valid(raise_exception=True)
        config[step].update(serializer.validated_data)
    return config


class RunSerializer(StrictSerializer):
    file = serializers.FileField(required=False)
    columns = serializers.ListField(child=serializers.JSONField(), required=False, max_length=200)
    rows = serializers.ListField(child=serializers.JSONField(), required=False, max_length=10_000)
    config = serializers.JSONField(default=dict)

    def validate_config(self, value):
        # multipart fields carry config as a JSON string; JSON bodies use an object.
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError:
                raise serializers.ValidationError("Config must contain valid JSON.") from None
        return validate_config(value)

    def validate(self, data):
        has_file = "file" in data
        has_table = "columns" in data or "rows" in data
        if has_file == has_table:
            raise serializers.ValidationError("Supply either file or both columns and rows.")
        if not has_file:
            if "columns" not in data or "rows" not in data:
                raise serializers.ValidationError("Supply both columns and rows.")
            validate_table(data["columns"], data["rows"])
        # DRF defaults bypass field validators, so merge defaults explicitly too.
        data["config"] = validate_config(data.get("config", {}))
        return data
