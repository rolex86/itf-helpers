from __future__ import annotations

from pathlib import Path

from src.profiles import (
    DEFAULT_PROFILE_ID,
    create_profile,
    delete_profile,
    duplicate_profile,
    duplicate_profile_files,
    ensure_unique_profile_id,
    get_active_profile_id,
    load_profile_payload,
    load_profile_presets,
    load_profile_settings,
    load_profiles_index,
    profile_allowlist_path,
    profile_favorites_path,
    profile_settings_path,
    rename_profile,
    save_profile_presets,
    save_profile_settings,
    save_profiles_index,
    set_active_profile,
    slugify_profile_id,
)


def test_slugify_profile_id() -> None:
    assert slugify_profile_id("PlusSystem") == "plussystem"
    assert slugify_profile_id("Chainway HW") == "chainway_hw"
    assert slugify_profile_id("Žluťoučký Kůň") == "zlutoucky_kun"
    assert slugify_profile_id("###") == "profil"


def test_ensure_unique_profile_id() -> None:
    used = {"plussystem", "plussystem_2"}
    assert ensure_unique_profile_id("PlusSystem", used) == "plussystem_3"


def test_profiles_index_defaults_when_missing(tmp_path: Path) -> None:
    index_path = tmp_path / "index.yaml"
    index = load_profiles_index(index_path)
    assert index["active_profile_id"] == DEFAULT_PROFILE_ID
    assert len(index["profiles"]) == 1


def test_create_duplicate_rename_delete_profile(tmp_path: Path) -> None:
    index_path = tmp_path / "index.yaml"
    index = load_profiles_index(index_path)

    index, chainway_id = create_profile(index, "Chainway")
    assert chainway_id == "chainway"
    assert get_active_profile_id(index) == chainway_id

    index, chainway_hw_id = duplicate_profile(index, chainway_id, "Chainway HW")
    assert chainway_hw_id == "chainway_hw"
    assert get_active_profile_id(index) == chainway_hw_id

    index = rename_profile(index, chainway_hw_id, "Chainway Hardware")
    renamed = [x for x in index["profiles"] if x["id"] == chainway_hw_id][0]
    assert renamed["name"] == "Chainway Hardware"

    index, active_after_delete = delete_profile(index, chainway_hw_id)
    assert active_after_delete != chainway_hw_id
    assert all(x["id"] != chainway_hw_id for x in index["profiles"])

    save_profiles_index(index_path, index)
    loaded = load_profiles_index(index_path)
    assert len(loaded["profiles"]) == len(index["profiles"])


def test_set_active_profile_ignores_unknown() -> None:
    index = load_profiles_index(Path("/does/not/exist.yaml"))
    changed = set_active_profile(index, "neexistuje")
    assert get_active_profile_id(changed) == DEFAULT_PROFILE_ID


def test_profile_settings_roundtrip_and_presets(tmp_path: Path) -> None:
    settings_path = profile_settings_path(tmp_path, "plussystem")
    settings = {
        "api": {"mode": "api_safe_import", "diff_preflight_enabled": True},
        "ui": {"output_encoding": "cp1250"},
    }
    presets = [
        {"id": "preset_1", "name": "Default", "values": {"api": {"mode": "api_safe_import"}}},
    ]

    save_profile_settings(
        settings_path,
        settings=settings,
        profile_id="plussystem",
        profile_name="PlusSystem",
        presets=presets,
    )

    loaded_settings = load_profile_settings(settings_path)
    loaded_payload = load_profile_payload(settings_path)
    loaded_presets = load_profile_presets(settings_path)

    assert loaded_settings == settings
    assert loaded_payload["profile_id"] == "plussystem"
    assert loaded_payload["profile_name"] == "PlusSystem"
    assert loaded_presets == presets

    presets2 = [
        {"id": "preset_2", "name": "Dry run", "values": {"api": {"mode": "api_dry_run"}}},
    ]
    save_profile_presets(settings_path, presets2, profile_id="plussystem", profile_name="PlusSystem")
    assert load_profile_presets(settings_path) == presets2


def test_duplicate_profile_files(tmp_path: Path) -> None:
    src_settings = profile_settings_path(tmp_path, "source")
    src_allowlist = profile_allowlist_path(tmp_path, "source")
    src_favorites = profile_favorites_path(tmp_path, "source")
    src_settings.parent.mkdir(parents=True, exist_ok=True)
    src_settings.write_text("settings: {}\n", encoding="utf-8")
    src_allowlist.write_text("custom_field_ids: ['1']\n", encoding="utf-8")
    src_favorites.write_text("favorite_list_ids: ['422']\n", encoding="utf-8")

    duplicate_profile_files(tmp_path, "source", "target")

    assert profile_settings_path(tmp_path, "target").exists()
    assert profile_allowlist_path(tmp_path, "target").exists()
    assert profile_favorites_path(tmp_path, "target").exists()
