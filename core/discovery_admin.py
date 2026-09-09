"""Human moderation with no publish action and an immutable decision history."""
from django import forms
from django.contrib import admin
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse
from django.utils.html import format_html

from .bibliography import safe_url
from .literature_discovery import review_candidate, validate_review
from .models import (LiteratureCandidate, DiscoveryRun, DiscoveryCheckpoint,
                     DiscoveryEvidence, DiscoveryReviewEvent)


class ReadOnlyDiscoveryAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class EvidenceInline(admin.StackedInline):
    model = DiscoveryEvidence
    extra = 0
    can_delete = False
    fields = ('provider', 'provider_id', 'retrieved_at', 'query', 'rationale', 'evidence_url', 'metadata', 'run')
    readonly_fields = fields
    classes = ('collapse',)

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


class HistoryInline(EvidenceInline):
    model = DiscoveryReviewEvent
    fields = ('reviewer', 'reviewed_at', 'before', 'after', 'note')
    readonly_fields = fields


@admin.register(LiteratureCandidate)
class LiteratureInboxAdmin(admin.ModelAdmin):
    list_display = ('title', 'decision', 'data_status', 'categories', 'first_seen', 'reference_link')
    list_filter = ('decision', 'data_status', 'first_seen')
    search_fields = ('title', 'doi', 'review_note', 'metadata', 'categories')
    readonly_fields = ('title', 'doi', 'paper_link', 'categories', 'metadata', 'reference_link',
                       'first_seen', 'last_seen', 'reviewed_by', 'reviewed_at')
    fields = ('title', 'doi', 'paper_link', 'categories', 'decision', 'review_note', 'data_status',
              'reference_link', 'metadata', 'first_seen', 'last_seen', 'reviewed_by', 'reviewed_at')
    inlines = [EvidenceInline, HistoryInline]
    actions = None

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description='Paper')
    def paper_link(self, obj):
        url = safe_url(obj.source_url)
        return format_html('<a href="{}" target="_blank" rel="noopener noreferrer">Open paper / source</a>', url) if url else 'No safe source link'

    @admin.display(description='Reference (review separately before publication)')
    def reference_link(self, obj):
        if not obj.reference_id:
            return 'Accept to create or reuse a pending reference'
        return format_html('<a href="{}">Ref {} — {}</a>', reverse('admin:core_reference_change', args=[obj.reference_id]), obj.reference_id, obj.reference.status)

    def get_form(self, request, obj=None, **kwargs):
        base = super().get_form(request, obj, **kwargs)
        class ReviewForm(base):
            def clean(self):
                cleaned = super().clean()
                if obj and all(f in cleaned for f in ('decision', 'data_status', 'review_note')):
                    try:
                        current = LiteratureCandidate.objects.get(pk=obj.pk)
                        validate_review(current, cleaned['decision'], cleaned['data_status'], cleaned['review_note'], request.user)
                    except (ValidationError, PermissionDenied) as exc:
                        raise forms.ValidationError(str(exc)) from exc
                return cleaned
        return ReviewForm

    def save_model(self, request, obj, form, change):
        saved = review_candidate(obj.pk, obj.decision, obj.data_status, obj.review_note, request.user)
        obj.__dict__.update(saved.__dict__)


@admin.register(DiscoveryRun)
class DiscoveryRunAdmin(ReadOnlyDiscoveryAdmin):
    list_display = ('id', 'started_at', 'finished_at', 'status')
    list_filter = ('status', 'started_at')


@admin.register(DiscoveryCheckpoint)
class DiscoveryCheckpointAdmin(ReadOnlyDiscoveryAdmin):
    list_display = ('provider', 'query_name', 'completed_through', 'window_start', 'updated_at')
    list_filter = ('provider',)
