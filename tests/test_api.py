import json

from fastapi.testclient import TestClient

import api
from api import app


client = TestClient(app)


def atom(serial, name, xyz):
    x, y, z = xyz
    return f"ATOM  {serial:5d} {name:>4} ALA A   1    {x:8.3f}{y:8.3f}{z:8.3f}  1.00 20.00          {name[0]:>2}  "


def test_api_health():
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_api_versions():
    response = client.get("/api/v1/versions")
    assert response.status_code == 200
    assert "python" in response.json()
    assert "vina" in response.json()


def test_api_structure_inspection():
    pdb = "\n".join([
        atom(1, "N", (-2, 0, 0)),
        atom(2, "CA", (0, 0, 0)),
        atom(3, "C", (2, 0, 0)),
        atom(4, "O", (3.2, 0, 0)),
    ]) + "\nEND\n"
    response = client.post(
        "/api/v1/structures/inspect",
        files={"file": ("test.pdb", pdb, "chemical/x-pdb")},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["atoms"] == 4
    assert payload["residues"][0]["name"] == "ALA"
    assert payload["structure_sha256"]


class FakeGitHubBackend:
    name = "github-actions"
    repository = "peterDataScientia/PanDoc"

    def __init__(self):
        self.cancelled = False
        self.cleaned = False

    def submit(self, root, config):
        return {
            "backend": self.name,
            "job_id": "a" * 32,
            "branch": "pandoc-job-" + "a" * 32,
            "repository": self.repository,
        }

    def status(self, handle):
        return {
            "state": "completed",
            "completed": 1,
            "total": 1,
            "run_id": 123,
            "html_url": "https://github.com/example/run/123",
            "conclusion": "success",
        }

    def materialize(self, handle, target):
        target.mkdir(parents=True, exist_ok=True)
        (target / "status.json").write_text(json.dumps({"state": "completed", "completed": 1, "total": 1}))
        (target / "results.json").write_text(json.dumps([{"ligand": "ligand_001", "seed": 2026, "rank": 1, "score_kcal_mol": -8.0, "sdf": "pose.sdf"}]))
        (target / "pose.sdf").write_text("test")
        return target

    def cleanup(self, handle):
        self.cleaned = True

    def cancel(self, handle):
        self.cancelled = True
        return True


def test_api_github_actions_job_flow(monkeypatch, tmp_path):
    backend = FakeGitHubBackend()
    monkeypatch.setattr(api, "API_ROOT", tmp_path)
    monkeypatch.setattr(api, "compute_backend", lambda require_github=False: backend)

    response = client.post(
        "/api/v1/jobs/dock",
        files=[
            ("receptor", ("receptor.pdbqt", "RECEPTOR", "text/plain")),
            ("ligands", ("ligand.pdbqt", "LIGAND", "text/plain")),
        ],
        data={
            "center": "[1, 2, 3]",
            "size": "[20, 20, 20]",
            "seeds": "[2026]",
            "exhaustiveness": "8",
            "poses": "9",
            "cpu": "2",
        },
    )
    assert response.status_code == 200
    submitted = response.json()
    assert submitted["backend"] == "github-actions"
    assert submitted["job_id"] == "a" * 32

    status = client.get(f"/api/v1/jobs/{submitted['job_id']}")
    assert status.status_code == 200
    assert status.json()["state"] == "completed"
    assert status.json()["backend"] == "github-actions"
    assert backend.cleaned is True

    results = client.get(f"/api/v1/jobs/{submitted['job_id']}/results")
    assert results.status_code == 200
    assert results.json()["results"][0]["score_kcal_mol"] == -8.0

    bundle = client.get(f"/api/v1/jobs/{submitted['job_id']}/bundle")
    assert bundle.status_code == 200
    assert bundle.headers["content-type"] == "application/zip"

    cancel = client.post(f"/api/v1/jobs/{submitted['job_id']}/cancel")
    assert cancel.status_code == 200
    assert cancel.json()["cancel_requested"] is True
    assert backend.cancelled is True
