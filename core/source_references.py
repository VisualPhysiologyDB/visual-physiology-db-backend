import csv
import math
from pathlib import Path

from .models import CuratedSCP, Reference


VPOD_IN_VIVO_SOURCE_DATASET = "VPOD_in_vivo_v1.0"
MNM_SOURCE_DATASET = "mnm_on_vpod_in_vivo"
CURATED_SCP_SOURCE_DATASET = "curated_scp"

SOURCE_PUBLICATIONS = {
    "longcore_id": {
        "label": "Longcore 2023",
        "doi": "10.1016/j.baae.2023.09.002",
        "year_of_publication": 2023,
        "citation": (
            "Longcore T. A compendium of photopigment peak sensitivities and visual "
            "spectral response curves of terrestrial wildlife to guide design of "
            "outdoor nighttime lighting. Basic and Applied Ecology 73:40-50."
        ),
    },
    "murwes_id": {
        "label": "Murphy and Westerman 2022",
        "doi": "10.1098/rspb.2022.0612",
        "year_of_publication": 2022,
        "citation": (
            "Murphy MJ, Westerman EL. Evolutionary history limits species' ability "
            "to match colour sensitivity to available habitat light. Proc. R. Soc. B "
            "289:20220612."
        ),
    },
    "caves_id": {
        "label": "Schweikert et al. 2019",
        "doi": "10.1111/jfb.13859",
        "year_of_publication": 2019,
        "citation": (
            "Schweikert LE, Caves EM, Solie SE, Sutton TT, Johnsen S. Variation in "
            "rod spectral sensitivity of fishes is best predicted by habitat and "
            "depth. Journal of Fish Biology 95:179-185."
        ),
    },
    "porter2005_id": {
        "label": "Porter 2005",
        "doi": None,
        "year_of_publication": 2005,
        "citation": (
            "Porter ML. Crustacean phylogenetic systematics and opsin evolution. "
            "Doctoral dissertation, Brigham Young University. ScholarsArchive: "
            "https://scholarsarchive.byu.edu/etd/557. No DOI found in attached PDF."
        ),
    },
    "porter2006_id": {
        "label": "Porter et al. 2006",
        "doi": "10.1093/molbev/msl152",
        "year_of_publication": 2007,
        "citation": (
            "Porter ML, Cronin TW, McClellan DA, Crandall KA. Molecular "
            "characterization of crustacean visual pigments and the evolution of "
            "pancrustacean opsins. Mol. Biol. Evol. 24(1):253-268. PDF lists "
            "Advance Access publication October 19, 2006."
        ),
    },
    "kooi_id": {
        "label": "van der Kooi et al. 2021",
        "doi": "10.1146/annurev-ento-061720-071644",
        "year_of_publication": 2021,
        "citation": (
            "van der Kooi CJ, Stavenga DG, Arikawa K, Belusic G, Kelber A. "
            "Evolution of insect color vision: from spectral sensitivity to visual "
            "ecology. Annu. Rev. Entomol. 66:435-461."
        ),
    },
}

SOURCE_REFERENCE_COLUMNS = (
    "longcore_id",
    "murwes_id",
    "caves_id",
    "porter2005_id",
    "porter2006_id",
    "kooi_id",
    "maxid",
)


def clean_source_value(value):
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    text = str(value).strip()
    if text.lower() in {"", "nan", "none", "null"}:
        return ""
    if text.endswith(".0") and text[:-2].isdigit():
        return text[:-2]
    return text


def parse_float(value):
    text = clean_source_value(value)
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_int(value):
    text = clean_source_value(value)
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def populated_source_columns(row):
    return [
        column
        for column in SOURCE_REFERENCE_COLUMNS
        if clean_source_value(row.get(column, ""))
    ]


def source_column_for_compendium_row(row):
    columns = populated_source_columns(row)
    return (columns[0] if columns else None), columns


def source_publication_note(source_column, metadata):
    return "\n".join(
        [
            f"Source key: {source_column}",
            f"Source label: {metadata['label']}",
            metadata["citation"],
        ]
    )


def ensure_source_publication_reference(source_column, metadata):
    doi = clean_source_value(metadata.get("doi"))
    source_key = f"Source key: {source_column}"
    queryset = Reference.objects.none()
    if doi:
        queryset = Reference.objects.filter(doi__iexact=doi)
    else:
        queryset = Reference.objects.filter(notes__icontains=source_key)

    reference = queryset.order_by("refid").first()
    notes = source_publication_note(source_column, metadata)

    if reference is None:
        return Reference.objects.create(
            doi=doi or None,
            year_of_publication=metadata.get("year_of_publication"),
            notes=notes,
            status="APPROVED",
        )

    changed = False
    if doi and not reference.doi:
        reference.doi = doi
        changed = True
    if metadata.get("year_of_publication") and not reference.year_of_publication:
        reference.year_of_publication = metadata["year_of_publication"]
        changed = True
    if source_key not in (reference.notes or ""):
        reference.notes = f"{reference.notes or ''}\n\n{notes}".strip()
        changed = True
    if reference.status != "APPROVED":
        reference.status = "APPROVED"
        changed = True
    if changed:
        reference.save()
    return reference


def ensure_source_publication_references():
    return {
        source_column: ensure_source_publication_reference(source_column, metadata)
        for source_column, metadata in SOURCE_PUBLICATIONS.items()
    }


def load_compendium_by_comp_id(csv_path):
    path = Path(csv_path)
    with path.open(newline="", encoding="utf-8-sig") as handle:
        rows = csv.DictReader(handle)
        return {
            clean_source_value(row.get("comp_db_id")): row
            for row in rows
            if clean_source_value(row.get("comp_db_id"))
        }


def build_scp_reference_by_maxid():
    references = {}
    records = CuratedSCP.objects.filter(reference__isnull=False).select_related("reference")
    for record in records:
        if record.source_dataset == CURATED_SCP_SOURCE_DATASET and record.source_record_id:
            references.setdefault(clean_source_value(record.source_record_id), record.reference)
        if record.scpid is not None:
            references.setdefault(str(record.scpid), record.reference)
    return references


def reference_for_compendium_row(row, source_references=None, scp_reference_by_maxid=None):
    source_references = source_references or {}
    scp_reference_by_maxid = scp_reference_by_maxid or {}
    source_column, source_columns = source_column_for_compendium_row(row)

    if source_column is None:
        return None, None, source_columns

    if source_column == "maxid":
        maxid = clean_source_value(row.get("maxid"))
        return scp_reference_by_maxid.get(maxid), source_column, source_columns

    return source_references.get(source_column), source_column, source_columns
