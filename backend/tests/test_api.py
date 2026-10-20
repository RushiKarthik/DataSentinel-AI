"""API contract and upload validation tests for the DataSentinel frontend."""
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_endpoint_reports_service_status():
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"



def test_local_vite_origin_is_allowed_by_cors():
    response = client.options(
        "/api/upload",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"

def test_upload_returns_real_analysis_and_sanitized_filename():
    response = client.post(
        "/api/upload",
        files={"file": (r"C:\\Users\\Student\\sample.csv", b"name,age\nAlice,30\n,25\n", "text/csv")},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["profile"]["filename"] == "sample.csv"
    assert payload["profile"]["row_count"] == 2
    assert payload["profile"]["column_count"] == 2
    assert payload["quality_score"]["overall_score"] < 100
    assert payload["total_issues"] > 0
    assert payload["findings"]
    assert all("sample_values" not in column for column in payload["profile"]["columns"])


def test_upload_removes_only_completely_empty_unnamed_csv_columns():
    response = client.post(
        "/api/upload",
        files={"file": ("trailing-delimiters.csv", b"id,value,,\n1,a,,\n2,b,,\n", "text/csv")},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["profile"]["row_count"] == 2
    assert payload["profile"]["column_count"] == 2
    assert [column["name"] for column in payload["profile"]["columns"]] == ["id", "value"]
    assert payload["csv_warnings"] == [
        "Ignored completely empty CSV formatting column(s): Unnamed: 2, Unnamed: 3"
    ]
    assert payload["quality_score"]["overall_score"] == 100
    assert sum(column["missing_count"] for column in payload["profile"]["columns"]) == 0
    assert not any(finding["category"] == "completeness" for finding in payload["findings"])


def test_upload_preserves_nonempty_unnamed_and_named_empty_columns():
    response = client.post(
        "/api/upload",
        files={"file": ("real-unnamed-data.csv", b"id,,empty,value\n1,keep,,x\n2,, ,y\n", "text/csv")},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["profile"]["row_count"] == 2
    assert payload["profile"]["column_count"] == 4
    profiles = {column["name"]: column for column in payload["profile"]["columns"]}
    assert "Unnamed: 1" in profiles
    assert "empty" in profiles
    assert profiles["Unnamed: 1"]["missing_count"] == 1
    assert profiles["empty"]["missing_count"] == 2
    assert payload["quality_score"]["completeness_score"] == 62.5
    assert payload["csv_warnings"] == []
    findings = {finding["column"]: finding for finding in payload["findings"] if finding["category"] == "completeness"}
    assert findings["Unnamed: 1"]["count"] == 1
    assert findings["empty"]["count"] == 2


def test_upload_flags_but_preserves_sparse_unnamed_column():
    rows = ["id,,value"]
    rows.extend(f"{index},{'retained' if index == 0 else ''},value-{index}" for index in range(10))
    response = client.post(
        "/api/upload",
        files={"file": ("sparse-column.csv", ("\n".join(rows) + "\n").encode(), "text/csv")},
    )
    assert response.status_code == 200
    payload = response.json()
    profiles = {column["name"]: column for column in payload["profile"]["columns"]}
    assert payload["profile"]["row_count"] == 10
    assert payload["profile"]["column_count"] == 3
    assert profiles["Unnamed: 1"]["non_null_count"] == 1
    assert profiles["Unnamed: 1"]["missing_count"] == 9
    assert payload["csv_warnings"] == []
    sparse = next(f for f in payload["findings"] if f["column"] == "Unnamed: 1")
    overall = next(f for f in payload["findings"] if f["issue_id"] == "completeness-overall")
    assert sparse["title"] == "Sparse unnamed column 'Unnamed: 1' — possible CSV formatting artifact"
    assert sparse["count"] == 9
    assert "Preserve its populated values" in sparse["recommendation"]
    assert overall["count"] == 9
    assert payload["quality_score"]["completeness_score"] == 70


def test_upload_returns_real_findings_with_missing_duplicate_and_invalid_values():
    csv_data = (
        "email,signup_date,id,amount,notes\n"
        "alice@example.com,2024-01-01,1,10,ok\n"
        "alice@example.com,2024-01-01,1,10,ok\n"
        "bad-email,not-a-date,3,20,okay\n"
        '"   ","   ",4,30,"   "\n'
        ", ,5,40,ok\n"
    ).encode()
    response = client.post("/api/upload", files={"file": ("mixed-quality.csv", csv_data, "text/csv")})
    assert response.status_code == 200
    payload = response.json()
    assert payload["profile"]["row_count"] == 5
    assert payload["profile"]["column_count"] == 5
    assert sum(column["missing_count"] for column in payload["profile"]["columns"]) == 5

    by_id = {finding["issue_id"]: finding for finding in payload["findings"]}
    assert by_id["completeness-overall"]["count"] == 5
    assert by_id["uniqueness-duplicate-rows"]["count"] == 2
    assert by_id["validity-email-invalid-email"]["count"] == 1
    assert by_id["validity-signup_date-invalid-dates"]["count"] == 1
    assert "validity-email-whitespace" not in by_id
    assert "validity-signup_date-whitespace" not in by_id
    assert payload["quality_score"]["completeness_score"] == 80
    assert payload["quality_score"]["uniqueness_score"] == 60
    assert payload["quality_score"]["validity_score"] == 66.67
    assert payload["quality_score"]["overall_score"] == 74.67


def test_second_upload_returns_current_dataset_while_first_remains_addressable():
    first = client.post(
        "/api/upload",
        files={"file": ("first.csv", b"name\nAlpha\nBeta\n", "text/csv")},
    )
    second = client.post(
        "/api/upload",
        files={"file": ("second.csv", b"score\n10\n20\n30\n", "text/csv")},
    )
    assert first.status_code == second.status_code == 200
    first_payload, second_payload = first.json(), second.json()
    assert second_payload["dataset_id"] != first_payload["dataset_id"]
    assert second_payload["profile"]["filename"] == "second.csv"
    assert second_payload["profile"]["row_count"] == 3
    assert second_payload["profile"]["column_count"] == 1
    assert client.get(f"/api/results/{first_payload['dataset_id']}").json()["profile"]["filename"] == "first.csv"


def test_upload_rejects_non_csv():
    response = client.post("/api/upload", files={"file": ("data.txt", b"a,b\n1,2\n", "text/plain")})
    assert response.status_code == 400
    assert "CSV" in response.json()["detail"]


def test_upload_rejects_empty_file():
    response = client.post("/api/upload", files={"file": ("empty.csv", b"", "text/csv")})
    assert response.status_code == 400
    assert "empty" in response.json()["detail"].lower()


def test_upload_rejects_malformed_csv():
    response = client.post(
        "/api/upload",
        files={"file": ("broken.csv", b'"unterminated\nvalue', "text/csv")},
    )
    assert response.status_code == 400


def test_sample_endpoint_returns_real_analysis():
    response = client.post("/api/analyze-sample")
    assert response.status_code == 200
    payload = response.json()
    assert payload["profile"]["row_count"] == 30
    assert payload["profile"]["column_count"] == 7
    assert payload["findings"]

