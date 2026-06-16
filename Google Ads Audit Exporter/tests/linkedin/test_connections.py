from pathlib import Path

from app.integrations.linkedin.connections import load_linkedin_connections, save_linkedin_connections
from app.integrations.linkedin.models import LinkedInConnection


def test_connections_persist_without_secrets(tmp_path: Path) -> None:
    connection = LinkedInConnection(key="main", label="Main", client_id="abc")
    save_linkedin_connections(tmp_path, [connection])
    rows = load_linkedin_connections(tmp_path)
    assert len(rows) == 1
    assert rows[0].key == "main"
    assert rows[0].client_id == "abc"

