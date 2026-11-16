from rest_framework import serializers
from core.api import StrictSerializer
from .detect import KINDS


class ProposeSerializer(StrictSerializer):
    file = serializers.FileField()
    use_llm = serializers.BooleanField(default=True)
    kind = serializers.ChoiceField(choices=KINDS, required=False)


class TargetSerializer(StrictSerializer):
    source_column = serializers.CharField(max_length=255)
    target_table = serializers.CharField(max_length=100)
    target_column = serializers.CharField(max_length=100)


class ReviewSerializer(StrictSerializer):
    state = serializers.CharField(max_length=2_000_000, trim_whitespace=False)
    approve = serializers.ListField(child=serializers.CharField(max_length=255), max_length=200, default=list)
    reject = serializers.ListField(child=serializers.CharField(max_length=255), max_length=200, default=list)
    set_targets = TargetSerializer(many=True, default=list)
    approve_exact = serializers.BooleanField(default=False)
    approve_suggested = serializers.BooleanField(default=False)
    kind = serializers.ChoiceField(choices=KINDS, required=False)

    def validate(self, data):
        corrected = [target["source_column"] for target in data["set_targets"]]
        if len(corrected) > 200 or len(set(corrected)) != len(corrected):
            raise serializers.ValidationError("Corrections must contain at most 200 unique source columns.")
        if set(data["reject"]) & (set(data["approve"]) | set(corrected)):
            raise serializers.ValidationError("A column cannot be approved/corrected and rejected together.")
        return data


class ApplySerializer(StrictSerializer):
    file = serializers.FileField()
    state = serializers.CharField(max_length=2_000_000, trim_whitespace=False)
