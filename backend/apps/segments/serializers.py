"""Validated, bounded requests for existing-customer segmentation."""

from rest_framework import serializers


class RunSerializer(serializers.Serializer):
    customer_ids = serializers.ListField(child=serializers.UUIDField(), min_length=1, max_length=100)
    observation_date = serializers.DateField(required=False)
    currency = serializers.ChoiceField(choices=["GBP"], required=False)
    persist = serializers.BooleanField(default=True)
    update_memberships = serializers.BooleanField(default=False)
    membership_threshold = serializers.DecimalField(max_digits=24, decimal_places=8, min_value=0, required=False)

    def validate(self, attrs):
        if len(set(attrs["customer_ids"])) != len(attrs["customer_ids"]):
            raise serializers.ValidationError("customer_ids must not contain duplicates.")
        if self.context["segment"] == "clv":
            if "observation_date" not in attrs or "currency" not in attrs:
                raise serializers.ValidationError("CLV requires an explicit observation_date and currency=GBP.")
        elif attrs.get("membership_threshold", 0) > 1:
            raise serializers.ValidationError("Churn membership_threshold must be between 0 and 1.")
        if attrs["update_memberships"] and (not attrs["persist"] or "membership_threshold" not in attrs):
            raise serializers.ValidationError("Membership updates require persist=true and an explicit membership_threshold.")
        return attrs


class ResultsQuerySerializer(serializers.Serializer):
    limit = serializers.IntegerField(min_value=1, max_value=100, default=25)
    offset = serializers.IntegerField(min_value=0, default=0)
    status = serializers.ChoiceField(choices=["predicted", "unavailable"], required=False)
