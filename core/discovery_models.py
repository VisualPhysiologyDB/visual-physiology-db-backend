"""Private literature discovery state. Nothing here is exposed by public serializers."""
import uuid
from django.conf import settings
from django.db import models


class DiscoveryRun(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, default='RUNNING')
    config_hash = models.CharField(max_length=64)
    configuration = models.JSONField(default=dict)
    summary = models.JSONField(default=dict)

    class Meta:
        ordering = ['-started_at']


class DiscoveryCheckpoint(models.Model):
    key = models.CharField(max_length=64, unique=True)
    provider = models.CharField(max_length=20)
    query_name = models.CharField(max_length=100)
    completed_through = models.DateField(null=True, blank=True)
    window_start = models.DateField(null=True, blank=True)
    window_end = models.DateField(null=True, blank=True)
    position = models.JSONField(default=dict)
    updated_at = models.DateTimeField(auto_now=True)


class LiteratureCandidate(models.Model):
    DECISIONS = [('NEW', 'New'), ('ACCEPTED', 'Accepted'), ('REJECTED', 'Rejected'), ('DEFERRED', 'Deferred')]
    DATA_STATES = [('NEEDS_DATA', 'Needs data entry'), ('COMPLETE', 'Data entry complete'), ('NOT_NEEDED', 'No data entry needed')]
    title = models.TextField()
    doi = models.TextField(blank=True)
    source_url = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, help_text='Provider metadata; verify against the paper before publishing.')
    categories = models.JSONField(default=list, help_text='Search relevance only; these are NOT verified measurement methods.')
    decision = models.CharField(max_length=12, choices=DECISIONS, default='NEW', db_index=True)
    data_status = models.CharField(max_length=12, choices=DATA_STATES, default='NEEDS_DATA')
    review_note = models.TextField(blank=True, help_text='Required when rejecting, deferring or reopening a rejected suggestion.')
    reference = models.ForeignKey('core.Reference', on_delete=models.PROTECT, null=True, blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    first_seen = models.DateTimeField(auto_now_add=True)
    last_seen = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'literature inbox item'
        verbose_name_plural = 'literature inbox'
        ordering = ['-first_seen']

    def __str__(self):
        return self.title[:120]


class DiscoveryIdentifier(models.Model):
    candidate = models.ForeignKey(LiteratureCandidate, on_delete=models.PROTECT, related_name='identifiers')
    key = models.CharField(max_length=64, unique=True)
    value = models.TextField()


class DiscoveryEvidence(models.Model):
    candidate = models.ForeignKey(LiteratureCandidate, on_delete=models.PROTECT, related_name='evidence')
    run = models.ForeignKey(DiscoveryRun, on_delete=models.PROTECT)
    provider = models.CharField(max_length=20)
    provider_id = models.TextField()
    query = models.TextField()
    evidence_url = models.TextField()
    retrieved_at = models.DateTimeField()
    rationale = models.TextField()
    metadata = models.JSONField(default=dict)
    fingerprint = models.CharField(max_length=64, unique=True)


class DiscoveryReviewEvent(models.Model):
    candidate = models.ForeignKey(LiteratureCandidate, on_delete=models.PROTECT, related_name='review_history')
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    reviewed_at = models.DateTimeField(auto_now_add=True)
    before = models.JSONField(default=dict)
    after = models.JSONField(default=dict)
    note = models.TextField(blank=True)
