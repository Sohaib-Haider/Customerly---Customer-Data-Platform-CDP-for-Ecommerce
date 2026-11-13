"""Existing canonical tables are unmanaged; only prediction results are managed."""

import uuid

from django.db import models


class Store(models.Model):
    id = models.UUIDField(primary_key=True)
    name = models.CharField(max_length=255)

    class Meta:
        managed = False
        db_table = "stores"


class Customer(models.Model):
    id = models.UUIDField(primary_key=True)
    store = models.ForeignKey(Store, on_delete=models.DO_NOTHING)
    source_customer_id = models.CharField(max_length=255)
    tenure = models.IntegerField(null=True)
    warehouse_to_home = models.DecimalField(max_digits=38, decimal_places=12, null=True)
    number_of_devices_registered = models.IntegerField(null=True)
    satisfaction_score = models.SmallIntegerField(null=True)
    complain = models.BooleanField(null=True)
    cashback_amount = models.DecimalField(max_digits=38, decimal_places=12, null=True)

    class Meta:
        managed = False
        db_table = "customers"


class SegmentDefinition(models.Model):
    id = models.UUIDField(primary_key=True)
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(null=True)
    segment_type = models.CharField(max_length=50)
    required_fields = models.JSONField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = False
        db_table = "segments"


class CustomerMembership(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    store = models.ForeignKey(Store, on_delete=models.DO_NOTHING)
    customer = models.ForeignKey(Customer, on_delete=models.DO_NOTHING)
    segment = models.ForeignKey(SegmentDefinition, on_delete=models.DO_NOTHING)
    score = models.DecimalField(max_digits=5, decimal_places=4, null=True)
    assigned_at = models.DateTimeField()

    class Meta:
        managed = False
        db_table = "customer_segments"
        unique_together = (("store", "customer", "segment"),)


class SegmentPrediction(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    store = models.ForeignKey(Store, on_delete=models.CASCADE)
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE)
    segment = models.ForeignKey(SegmentDefinition, on_delete=models.CASCADE)
    segment_key = models.CharField(max_length=20)
    status = models.CharField(max_length=20)
    result = models.JSONField()
    observation_date = models.DateField(null=True)
    computed_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "segment_predictions"
        constraints = [models.UniqueConstraint(fields=["store", "customer", "segment"], name="uq_segment_prediction_current"),
                       models.CheckConstraint(condition=models.Q(status__in=["predicted", "unavailable"]), name="ck_segment_prediction_status")]
        indexes = [models.Index(fields=["store", "segment_key", "status"], name="idx_prediction_store_status")]
