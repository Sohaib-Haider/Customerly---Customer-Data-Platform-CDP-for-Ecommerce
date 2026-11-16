"""Shared bounded data input and authentication for stateless processing APIs."""
import csv
import io
import math

from rest_framework import serializers
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.parsers import JSONParser
from rest_framework.views import APIView

from apps.segments.authentication import SegmentTokenAuthentication

MAX_BYTES = 5 * 1024 * 1024
MAX_ROWS = 10_000
MAX_COLUMNS = 200


class InputTooLarge(APIException):
    status_code = 413
    default_detail = "Input exceeds the processing API limits."


class BoundedJSONParser(JSONParser):
    def parse(self, stream, media_type=None, parser_context=None):
        raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise InputTooLarge("JSON bodies must be at most 5 MiB.")
        return super().parse(io.BytesIO(raw), media_type, parser_context)


class ProcessingAPIView(APIView):
    authentication_classes = [SegmentTokenAuthentication]
    permission_classes = [IsAuthenticated]

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "private, no-store"
        return response


class StrictSerializer(serializers.Serializer):
    def to_internal_value(self, data):
        if isinstance(data, dict):
            unknown = set(data) - set(self.fields)
            if unknown:
                raise ValidationError({key: "Unknown field." for key in sorted(unknown)})
        return super().to_internal_value(data)


def validate_table(columns, records):
    if not columns or len(columns) > MAX_COLUMNS:
        raise ValidationError({"columns": f"Supply 1–{MAX_COLUMNS} column names."})
    if any(not isinstance(name, str) or not name.strip() or len(name) > 255 for name in columns):
        raise ValidationError({"columns": "Names must be nonblank strings of at most 255 characters."})
    if len(set(columns)) != len(columns):
        raise ValidationError({"columns": "Duplicate column names are not supported."})
    if len(records) > MAX_ROWS:
        raise InputTooLarge(f"At most {MAX_ROWS} data rows are supported per request.")
    if not records:
        raise ValidationError({"rows": "Supply at least one data row."})
    for row in records:
        if not isinstance(row, list) or len(row) != len(columns):
            raise ValidationError({"rows": "Each row must be a list with one cell per column."})
        if any(isinstance(value, (dict, list)) or not isinstance(value, (str, int, float, bool, type(None)))
               or (isinstance(value, float) and not math.isfinite(value)) for value in row):
            raise ValidationError({"rows": "Cells must be finite JSON scalars or null."})


def read_csv_upload(upload):
    if upload.size > MAX_BYTES:
        raise InputTooLarge("CSV files must be at most 5 MiB.")
    raw = upload.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise InputTooLarge("CSV files must be at most 5 MiB.")
    try:
        text = raw.decode("utf-8-sig")
        if "\x00" in text:
            raise ValueError
        reader = csv.reader(io.StringIO(text, newline=""), strict=True)
        columns = next(reader, [])
        records = []
        for row in reader:
            records.append(row)
            if len(records) > MAX_ROWS:
                raise InputTooLarge(f"At most {MAX_ROWS} data rows are supported per request.")
        validate_table(columns, records)
    except (UnicodeError, csv.Error, ValueError):
        raise ValidationError({"file": "Supply a valid UTF-8 comma-separated CSV with a header."}) from None
    return raw, columns, records
