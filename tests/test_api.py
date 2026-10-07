from fastapi.testclient import TestClient

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
