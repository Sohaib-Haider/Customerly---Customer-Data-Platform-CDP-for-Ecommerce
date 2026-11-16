"""Validated query parameters; monetary response values are decimal strings."""
from rest_framework import serializers


class PageQuerySerializer(serializers.Serializer):
    limit = serializers.IntegerField(default=25, min_value=1, max_value=100)
    offset = serializers.IntegerField(default=0, min_value=0)


class CustomerQuerySerializer(PageQuerySerializer):
    search = serializers.CharField(required=False, max_length=255, allow_blank=True)
    source = serializers.RegexField(r"^[A-Za-z0-9_-]{1,50}$", required=False)
