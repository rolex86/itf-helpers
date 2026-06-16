from app.integrations.linkedin.models import LinkedInExportManifest


def test_manifest_to_dict() -> None:
    manifest = LinkedInExportManifest(
        platform="linkedin",
        context_key="ctx",
        connection_key="main",
        started_at="2026-06-16T00:00:00+00:00",
    )
    payload = manifest.to_dict()
    assert payload["platform"] == "linkedin"
    assert payload["context_key"] == "ctx"
