from django.contrib import admin
from django.forms.models import model_to_dict
from .models import TuningProtein,TuningEvidence,TuningCitation,TuningAudit
from .models import TuningCandidate
from django import forms
from django.core.exceptions import ValidationError
from django.utils.html import format_html, format_html_join
from django.conf import settings
from .tuning_extraction import (publish_candidate, current_input_check, analyze, inventory,
    read_supplement, AlignmentCache, VERSION, store_analysis, auto_approve)



def snapshot(obj):
    fields={f.name:f.value_from_object(obj) for f in obj._meta.fields}
    return {k:str(v) if hasattr(v,'isoformat') else v for k,v in fields.items()}


class TuningAdmin(admin.ModelAdmin):
    actions=None
    readonly_fields=('created_at','updated_at','submitted_by','approved_by')
    def save_model(self,request,obj,form,change):
        before=snapshot(type(obj).objects.get(pk=obj.pk)) if change else None
        if obj.submitted_by_id is None:obj.submitted_by=request.user
        if obj.status=='APPROVED':obj.approved_by=request.user
        obj.full_clean();obj.save()
        TuningAudit.objects.create(actor=f'admin:{request.user.pk}',object_type=type(obj).__name__,object_key=obj.key,
                                  before=before,after=snapshot(obj),reason='Curator edit')
    def has_delete_permission(self,request,obj=None):return False


@admin.register(TuningProtein)
class ProteinAdmin(TuningAdmin):
    list_display=('key','name','family','subtype','status')
    search_fields=('key','name','accession')
    list_filter=('status','family')


class CitationInline(admin.TabularInline):
    model=TuningCitation
    extra=1
    autocomplete_fields=('reference',)


@admin.register(TuningEvidence)
class EvidenceAdmin(TuningAdmin):
    list_display=('key','title','category','family','subtype','shift_nm','status')
    list_filter=('status','family','category','subtype')
    search_fields=('key','title','original_notation','organism','notes','source_locator')
    inlines=[CitationInline]
    autocomplete_fields=('protein','wild_type_assay','mutant_assay')
    def save_related(self,request,form,formsets,change):
        before=list(form.instance.citations.values('reference_id','role','locator'))
        super().save_related(request,form,formsets,change)
        after=list(form.instance.citations.values('reference_id','role','locator'))
        if before!=after:TuningAudit.objects.create(actor=f'admin:{request.user.pk}',object_type='TuningCitation',object_key=form.instance.key,
                                                   before=before,after=after,reason='Curator citation edit')


@admin.register(TuningAudit)
class AuditAdmin(admin.ModelAdmin):
    list_display=('created_at','actor','object_type','object_key','reason')
    def has_add_permission(self,request):return False
    def has_change_permission(self,request,obj=None):return False
    def has_delete_permission(self,request,obj=None):return False


class CandidateForm(forms.ModelForm):
    acknowledge_changes = forms.BooleanField(required=False, label='I reviewed the changed extraction inputs',
        help_text='Only needed for a stale candidate. Reconcile any linked Tuning evidence before re-approval.')
    class Meta:
        model = TuningCandidate
        fields = ('selection', 'numbering_confirmed', 'conditions_confirmed', 'cross_publication_confirmed',
                  'review_note', 'decision', 'require_condition_match')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        options = self.instance.analysis.get('options', [])
        choices = [('', 'Use the preferred valid comparison after re-analysis')]
        choices += [(o['key'], f'{o["scheme"]} numbering → source position {o["source_position"]}; '
                     f'WT het {o["comparator_id"]}; candidate shift {o["candidate_shift_nm"]:+g} nm'
                     + (' — different publication' if o['cross_publication'] else '')
                     + (' — condition differences/unknowns (optional strict check)' if o.get('condition_warnings') else '')
                     + (' — CONFLICT: ' + '; '.join(o['conflicts']) if o['conflicts'] else '')) for o in options]
        if self.instance.selection and self.instance.selection not in {v for v, _ in choices}:
            choices.append((self.instance.selection, 'Previous selection (no longer supported): ' + self.instance.selection))
        self.fields['selection'] = forms.ChoiceField(choices=choices, required=False, label='Comparator and numbering')
        for name in ('numbering_confirmed', 'conditions_confirmed', 'cross_publication_confirmed'):
            self.fields[name].help_text = 'Optional record of a manual literature check; automatic approval never checks this box for you.'

    def clean(self):
        data = super().clean()
        if self.errors:
            return data
        if self.instance.analysis.get('algorithm', {}).get('version') != VERSION or not current_input_check(self.instance):
            rows = inventory()
            row = next(r for r in rows if r['hetid'] == self.instance.assay_id)
            path = settings.BASE_DIR / 'heterologous.csv'
            raw = read_supplement(path if path.exists() else None)
            previous = self.instance.analysis
            try:
                analysis = analyze(row, rows, raw, AlignmentCache(settings.BASE_DIR / 'var/tuning-extraction-cache'),
                    {row['hetid']: previous.get('existing_evidence', [])})
            except (ValidationError, ValueError, OSError) as exc:
                raise forms.ValidationError(str(exc))
            self.instance.analysis = analysis; self.instance.fingerprint = analysis['fingerprint']; self.instance.outcome = analysis['outcome']
            if data.get('selection') not in {o['key'] for o in analysis['options']}:
                data['selection'] = analysis.get('suggested_selection', '')
        if not data.get('selection'):
            data['selection'] = self.instance.analysis.get('suggested_selection', '')
        if data.get('acknowledge_changes'):
            self.instance.stale = False
        return data


@admin.register(TuningCandidate)
class CandidateAdmin(admin.ModelAdmin):
    form = CandidateForm
    actions = ['repair_accessions']
    list_display = ('assay_id', 'notation', 'organism', 'outcome', 'decision', 'approval_mode', 'stale', 'evidence')
    list_filter = ('decision', 'approval_mode', 'outcome', 'stale', 'assay__opsin__phylum')
    search_fields = ('=assay__hetid', 'assay__mutations', 'assay__opsin__genus', 'assay__opsin__species',
                     'assay__reference__doi', 'review_note')
    readonly_fields = ('source_summary', 'numbering_hypotheses', 'sequence_diagnostics', 'review_warnings', 'source_measurements',
                       'fingerprint', 'reviewed_fingerprint', 'stale', 'evidence', 'reviewed_by', 'updated_at', 'approval_mode', 'approval_policy')
    fieldsets = (
        ('Source and extraction', {'fields': ('source_summary', 'sequence_diagnostics', 'numbering_hypotheses', 'review_warnings', 'source_measurements')}),
        ('Approval', {'fields': ('selection', 'require_condition_match', 'acknowledge_changes', 'review_note', 'decision'),
         'description': 'Eligible unambiguous matches can be approved automatically by the applied builder. '
             'Strict condition equality is optional and off by default. Numbering alone cannot validate a WT–mutant comparison.'}),
        ('Optional manual literature checks', {'classes': ('collapse',), 'fields': ('numbering_confirmed', 'conditions_confirmed', 'cross_publication_confirmed')}),
        ('History', {'classes': ('collapse',), 'fields': ('approval_mode', 'approval_policy', 'fingerprint', 'reviewed_fingerprint', 'stale', 'evidence', 'reviewed_by', 'updated_at')}),
    )

    def notation(self, obj): return obj.analysis.get('notation')
    def organism(self, obj): return obj.analysis.get('organism')
    def source_summary(self, obj):
        d = obj.analysis
        if obj.assay.duplicate_of_id:
            target = obj.assay.duplicate_of_id
            return format_html('Archived duplicate of <a href="/admin/core/heterologousdata/{}/change/">retained assay {}</a>. Review the retained assay’s tuning candidate; this duplicate cannot be published.', target, target)
        return format_html('<a href="/admin/core/heterologousdata/{}/change/">Edit source assay {}</a> · {} · {} · {} · ref {} · {}. Outcome: {}. {}', obj.assay_id, obj.assay_id,
            d.get('notation'), d.get('organism'), d.get('phylum'), d.get('reference_id'), d.get('doi'), obj.outcome,
            'Source records changed: re-extract before approval.' if not current_input_check(obj) else '')

    def numbering_hypotheses(self, obj):
        return format_html_join('', '<p><strong>{}</strong>: source position {}; residue {}; {}</p>',
            ((h['scheme'], h['source_position'] or 'unresolved', h['residue'] or 'unknown', h['assessment'])
             for h in obj.analysis.get('hypotheses', [])))

    def sequence_diagnostics(self, obj):
        diagnostics = obj.analysis.get('sequence_diagnostics')
        if diagnostics is None:
            # Explain old v1 candidates before their first v2 extraction, without MAFFT on GET.
            row = obj.analysis['inputs']['assay']
            diagnostics = []
            for w in obj.analysis['inputs']['comparators']:
                a, b = w['protein_sequence'], row['protein_sequence']
                reasons = []
                if a == b: reasons.append('WT and mutant sequences are identical; no stored mutation can be verified.')
                if w['opsin_id'] == row['opsin_id']: reasons.append(f'Both assays point to shared Opsin {row["opsin_id"]}; edit the source notation if needed, then repair the accession using the list action.')
                diagnostics.append({'comparator_id': w['hetid'], 'wt_length':len(a), 'mutant_length':len(b),
                    'difference_count':sum(x != y for x,y in zip(a,b)), 'reasons':reasons})
        return format_html_join('', '<p><a href="/admin/core/heterologousdata/{}/change/">WT het {}</a>: {} residue differences; WT length {}, mutant length {}. {}</p>',
            ((d['comparator_id'],d['comparator_id'],d['difference_count'],d['wt_length'],d['mutant_length'],' '.join(d['reasons'])) for d in diagnostics))

    def review_warnings(self, obj):
        warnings = list(obj.analysis.get('warnings', []))
        if obj.analysis.get('existing_evidence'):
            warnings.append('Existing entries: ' + ', '.join(obj.analysis['existing_evidence']))
        return format_html_join('', '<p>{}</p>', ((w,) for w in warnings))

    def source_measurements(self, obj):
        inputs = obj.analysis['inputs']
        rows = [inputs['assay'], *inputs['comparators']]
        return format_html_join('', '<details><summary>Het {} · {} · {} nm · {}</summary><p>{}</p><p>{}</p></details>',
            ((r['hetid'], r['mutations'] or 'WT', r['lambda_max'], r['cell_culture'],
              r['accession'], str(inputs['supplement'].get(str(r['hetid']), {}))) for r in rows))

    @admin.action(description='Repair selected mutant accessions and refresh/approve eligible matches')
    def repair_accessions(self, request, queryset):
        from .tuning_mutations import repair_inventory
        if not request.user.has_perms(['core.change_heterologousdata', 'core.add_opsin', 'core.change_opsin']):
            self.message_user(request, 'Changing source constructs requires assay-change, opsin-add and opsin-change permissions.', level='ERROR')
            return
        ids = list(queryset.values_list('assay_id', flat=True))
        report = repair_inventory(apply=True, assay_ids=ids, actor=f'admin:{request.user.pk}', retry_unresolved=True)
        rows = inventory(); cache = AlignmentCache(settings.BASE_DIR / 'var/tuning-extraction-cache')
        raw = read_supplement(settings.BASE_DIR / 'heterologous.csv')
        for candidate in queryset:
            row = next(r for r in rows if r['hetid'] == candidate.assay_id)
            data = analyze(row, rows, raw, cache, {row['hetid']: candidate.analysis.get('existing_evidence', [])})
            store_analysis(data)
            candidate.refresh_from_db()
            if settings.VPOD_TUNING_AUTO_APPROVE: auto_approve(candidate)
        self.message_user(request, f'Accession repair results: {report["counts"]}. Candidates refreshed; unresolved comparisons remain available for review.')

    def has_add_permission(self, request): return False
    def has_delete_permission(self, request, obj=None): return False

    def save_model(self, request, obj, form, change):
        # Django wraps change-form POST in a transaction. Model/form validation occurs first.
        before = snapshot(TuningCandidate.objects.get(pk=obj.pk))
        if obj.decision == 'APPROVED':
            publish_candidate(obj, request.user)
            obj.approval_mode = 'MANUAL'
        obj.reviewed_by = request.user
        obj.save()
        TuningAudit.objects.create(actor=f'admin:{request.user.pk}', object_type='TuningCandidate', object_key=str(obj.assay_id),
            before=before, after=snapshot(obj), reason=obj.review_note or 'Curator review decision')
