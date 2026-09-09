"""Move legacy contact lines into the private receipt table without exposing them in exports."""
import re
from django.db import migrations


def move_contacts(apps, schema_editor):
    Reference = apps.get_model('core', 'Reference')
    Receipt = apps.get_model('core', 'SubmissionReceipt')
    alias = schema_editor.connection.alias
    for ref in Reference.objects.using(alias).filter(notes__icontains='Submitter email:').iterator():
        lines = re.findall(r'^.*Submitter email:.*$', ref.notes, flags=re.M | re.I)
        if lines:
            Receipt.objects.using(alias).create(reference_id=ref.pk, payload={'legacy_contact_lines': lines}, result={'privacy_migration': True})
            ref.notes = re.sub(r'^.*Submitter email:.*(?:\n|$)', '', ref.notes, flags=re.M | re.I).strip()
            ref.save(using=alias, update_fields=['notes'])


class Migration(migrations.Migration):
    dependencies = [('core', '0007_reference_metadata_visual_acuity')]
    operations = [migrations.RunPython(move_contacts, migrations.RunPython.noop)]
