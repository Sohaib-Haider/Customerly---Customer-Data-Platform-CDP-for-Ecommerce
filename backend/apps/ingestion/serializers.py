import re
from rest_framework import serializers
from core.api import StrictSerializer
from apps.mapping.serializers import TargetSerializer, ReviewSerializer as MappingReview

PROFILES = ["generic", "retail", "transactions", "churn", "events", "messages", "coupons"]


class SourceSerializer(StrictSerializer):
    namespace = serializers.RegexField(r"^[a-z][a-z0-9_]{0,39}$")
    name = serializers.CharField(max_length=200)
    currency = serializers.RegexField(r"^[A-Z]{3}$", allow_null=True, default=None)
    analytics_profile = serializers.ChoiceField(choices=PROFILES, default="generic")

    def validate_namespace(self, value):
        if value in {"ret", "dnb", "chr", "evb", "msg", "cpn", "unknown"}:
            raise serializers.ValidationError("This namespace is reserved for the existing demo populations.")
        return value

    def validate(self, data):
        if data["analytics_profile"] in {"retail", "transactions"} and not data["currency"]:
            raise serializers.ValidationError("Monetary profiles require a verified source currency.")
        return data


class CreateBatchSerializer(StrictSerializer):
    source_id = serializers.UUIDField()
    request_key = serializers.CharField(max_length=100)
    files = serializers.ListField(child=serializers.FileField(), min_length=1, max_length=10)


class RevisionSerializer(StrictSerializer):
    revision = serializers.IntegerField(min_value=0)


class MapSerializer(RevisionSerializer):
    use_llm = serializers.BooleanField(default=True)


class CalculationSerializer(StrictSerializer):
    target_table = serializers.CharField(max_length=100)
    target_column = serializers.CharField(max_length=100)
    operation = serializers.ChoiceField(choices=["multiply", "date", "channel_norm"])
    source_columns = serializers.ListField(child=serializers.CharField(max_length=255), min_length=1, max_length=2)
    format = serializers.CharField(max_length=100, required=False)


class FileReviewSerializer(StrictSerializer):
    file_id = serializers.UUIDField()
    approve = MappingReview().fields["approve"]
    reject = MappingReview().fields["reject"]
    set_targets = TargetSerializer(many=True, default=list)
    approve_exact = serializers.BooleanField(default=False)
    approve_suggested = serializers.BooleanField(default=False)
    bindings = TargetSerializer(many=True, default=list)
    calculations = CalculationSerializer(many=True, default=list)
    identity_columns = serializers.DictField(child=serializers.ListField(child=serializers.CharField(max_length=255), min_length=1, max_length=10), default=dict)
    deduplicate = serializers.BooleanField(default=False)


class SegmentOptionSerializer(StrictSerializer):
    enabled = serializers.BooleanField(default=True)
    observation_date = serializers.DateField(required=False)
    update_memberships = serializers.BooleanField(default=False)
    membership_threshold = serializers.DecimalField(max_digits=24, decimal_places=8, min_value=0, required=False)


class ReviewSerializer(RevisionSerializer):
    files = FileReviewSerializer(many=True, allow_empty=False)
    segment_options = serializers.DictField(child=SegmentOptionSerializer(), default=dict)

    def validate_segment_options(self, value):
        if set(value) - {"churn", "clv"}:
            raise serializers.ValidationError("Only churn and CLV are implemented.")
        for key, options in value.items():
            if options["enabled"] and key == "clv" and "observation_date" not in options:
                raise serializers.ValidationError("CLV requires an explicit source-local observation_date.")
            if options["update_memberships"] and "membership_threshold" not in options:
                raise serializers.ValidationError("Membership updates require an explicit threshold.")
            if key == "churn" and options.get("membership_threshold", 0) > 1:
                raise serializers.ValidationError("Churn threshold cannot exceed 1.")
        return value


class PageSerializer(StrictSerializer):
    limit = serializers.IntegerField(min_value=1, max_value=100, default=25)
    offset = serializers.IntegerField(min_value=0, default=0)
