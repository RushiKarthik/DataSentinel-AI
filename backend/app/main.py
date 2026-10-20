"""
DataSentinel AI — FastAPI Backend Application

This is the main API server. It connects the React frontend to the
Python data quality engine.

HOW THE API WORKS:
1. Frontend sends a CSV file via POST /api/upload
2. Backend validates the file (size, type, structure)
3. Pandas loads the CSV into a DataFrame
4. The quality engine runs all checks
5. Results are returned as JSON to the frontend
6. The DataFrame is kept in memory for the session (no database)
7. Investigation requests use the stored results

ENDPOINTS:
- POST   /api/upload          — Upload and analyze a CSV file
- POST   /api/analyze-sample  — Analyze the built-in sample dataset
- GET    /api/results/{id}    — Get analysis results by dataset ID
- POST   /api/investigate     — Ask the AI agent about findings
- GET    /api/export/{id}     — Download findings as JSON report
- GET    /api/export/{id}/csv — Download findings as CSV report
- GET    /api/health          — Health check
- GET    /api/preview/{id}    — Preview dataset rows
"""

import uuid
import tempfile
import os
from datetime import datetime
from typing import Optional
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
import pandas as pd
import io
import json
import re

from app.config import settings
from app.models import InvestigationRequest, AnalysisResult
from app.quality_engine import analyze_dataset
from app.agent import investigate
from app.sample_data import get_sample_dataframe, get_sample_csv_bytes


# ─────────────────────────────────────────────
# APP INITIALIZATION
# ─────────────────────────────────────────────

app = FastAPI(
    title="DataSentinel AI",
    description="AI-powered data reliability engine for detecting and investigating data quality issues.",
    version="1.0.0",
)

# CORS: Allow the React dev server to make requests
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:4173", "http://127.0.0.1:4173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory storage for analysis results and DataFrames
# In a production system, these would be in a database or cache
_results_store: dict[str, AnalysisResult] = {}
_dataframes_store: dict[str, pd.DataFrame] = {}


_PANDAS_UNNAMED_COLUMN = re.compile(r"Unnamed:\s*\d+(?:_level_\d+)?$")


def _remove_empty_unnamed_columns(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Remove only parser-generated unnamed columns with no nonblank values.

    Unnamed columns with even one value are retained and analyzed as user data.
    Named empty columns are retained as well, since they may be intentional.
    """
    removable = []
    for column in df.columns:
        if not _PANDAS_UNNAMED_COLUMN.fullmatch(str(column)):
            continue
        values = df[column]
        is_blank = values.isna() | values.map(
            lambda value: isinstance(value, str) and not value.strip()
        )
        if bool(is_blank.all()):
            removable.append(column)
    if not removable:
        return df, []
    return df.drop(columns=removable), [str(column) for column in removable]


def _public_result(result: AnalysisResult) -> dict:
    """Serialize analysis metadata without returning sample cell values."""
    payload = result.model_dump()
    for column in payload["profile"]["columns"]:
        column.pop("sample_values", None)
    return payload


# ─────────────────────────────────────────────
# HEALTH CHECK
# ─────────────────────────────────────────────

@app.get("/api/health")
async def health_check():
    """Simple health check endpoint."""
    return {
        "status": "healthy",
        "service": "DataSentinel AI",
        "version": "1.0.0",
        "llm_configured": settings.has_llm_key,
        "timestamp": datetime.now().isoformat(),
    }


# ─────────────────────────────────────────────
# FILE UPLOAD AND ANALYSIS
# ─────────────────────────────────────────────

@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...)):
    """
    Upload a CSV file for data quality analysis.

    Validation steps:
    1. Check file extension (.csv only)
    2. Check file size (max 50MB)
    3. Try to parse with pandas
    4. Check for empty datasets
    5. Run the quality engine

    The uploaded file is read into memory and NOT saved to disk
    permanently. A temporary file is used only during parsing.
    """
    # Validate file extension
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided.")

    safe_filename = file.filename.replace("\\", "/").split("/")[-1]
    ext = Path(safe_filename).suffix.lower()
    if ext not in settings.ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: '{ext}'. Only CSV files are accepted."
        )

    # Read file content
    try:
        content = await file.read()
    except Exception as e:
        raise HTTPException(status_code=400, detail="Could not read the uploaded file.")

    # Validate file size
    size_mb = len(content) / (1024 * 1024)
    if size_mb > settings.MAX_FILE_SIZE_MB:
        raise HTTPException(
            status_code=400,
            detail=f"File too large: {size_mb:.1f}MB. Maximum allowed: {settings.MAX_FILE_SIZE_MB}MB."
        )

    if len(content) == 0:
        raise HTTPException(status_code=400, detail="File is empty.")

    # Parse CSV
    try:
        df = pd.read_csv(io.BytesIO(content))
    except pd.errors.EmptyDataError:
        raise HTTPException(status_code=400, detail="CSV file contains no data.")
    except pd.errors.ParserError as e:
        raise HTTPException(
            status_code=400,
            detail="Could not parse this file as CSV. Check that it contains valid, consistently structured CSV data."
        )
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail="Could not process this file as CSV."
        )

    # Validate dataset is not empty
    if df.empty or len(df.columns) == 0:
        raise HTTPException(
            status_code=400,
            detail="Dataset is empty or has no columns."
        )

    df, removed_empty_columns = _remove_empty_unnamed_columns(df)
    if len(df.columns) == 0:
        raise HTTPException(status_code=400, detail="CSV contains no data columns after removing empty formatting columns.")

    # Run analysis
    dataset_id = str(uuid.uuid4())[:8]
    try:
        result = analyze_dataset(df, safe_filename, dataset_id)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail="Analysis could not be completed for this dataset."
        )

    if removed_empty_columns:
        result = result.model_copy(update={
            "csv_warnings": [
                "Ignored completely empty CSV formatting column(s): " + ", ".join(removed_empty_columns)
            ]
        })

    # Store results in memory
    _results_store[dataset_id] = result
    _dataframes_store[dataset_id] = df

    return _public_result(result)


# ─────────────────────────────────────────────
# SAMPLE DATA ANALYSIS
# ─────────────────────────────────────────────

@app.post("/api/analyze-sample")
async def analyze_sample():
    """
    Analyze the built-in sample dataset.

    This allows users to try the application without uploading their own data.
    The sample dataset has known issues for demonstration purposes.
    """
    df = get_sample_dataframe()
    dataset_id = "sample-" + str(uuid.uuid4())[:4]

    result = analyze_dataset(df, "sample_employee_data.csv", dataset_id)

    _results_store[dataset_id] = result
    _dataframes_store[dataset_id] = df

    return _public_result(result)


# ─────────────────────────────────────────────
# RESULTS RETRIEVAL
# ─────────────────────────────────────────────

@app.get("/api/results/{dataset_id}")
async def get_results(dataset_id: str):
    """Retrieve analysis results by dataset ID."""
    if dataset_id not in _results_store:
        raise HTTPException(status_code=404, detail="Dataset not found. It may have expired.")
    return _public_result(_results_store[dataset_id])


# ─────────────────────────────────────────────
# DATA PREVIEW
# ─────────────────────────────────────────────

@app.get("/api/preview/{dataset_id}")
async def preview_data(dataset_id: str, rows: int = 20):
    """
    Preview the first N rows of the dataset.

    Returns the data as a list of dictionaries for easy table rendering.
    Limited to MAX_ROWS_PREVIEW to prevent large payloads.
    """
    if dataset_id not in _dataframes_store:
        raise HTTPException(status_code=404, detail="Dataset not found.")

    df = _dataframes_store[dataset_id]
    preview_rows = min(rows, settings.MAX_ROWS_PREVIEW)

    # Convert to JSON-safe format
    preview = df.head(preview_rows).fillna("").to_dict(orient='records')
    columns = list(df.columns)

    return {
        "columns": columns,
        "rows": preview,
        "total_rows": len(df),
        "showing": preview_rows,
    }


# ─────────────────────────────────────────────
# AI INVESTIGATION
# ─────────────────────────────────────────────

@app.post("/api/investigate")
async def investigate_finding(request: InvestigationRequest):
    """
    Ask the AI investigation agent about a finding or the dataset.

    The agent calls tools to gather evidence and generates a response.
    Uses LLM when API key is configured, otherwise uses rule-based templates.
    """
    if request.dataset_id not in _results_store:
        raise HTTPException(status_code=404, detail="Dataset not found. Please upload or analyze data first.")

    result = _results_store[request.dataset_id]

    try:
        response = await investigate(
            question=request.question,
            result=result,
            issue_id=request.issue_id,
        )
        return response.model_dump()
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Investigation failed: {str(e)}"
        )


# ─────────────────────────────────────────────
# REPORT EXPORT
# ─────────────────────────────────────────────

@app.get("/api/export/{dataset_id}")
async def export_json_report(dataset_id: str):
    """
    Export analysis results as a JSON report.

    The report includes dataset summary, quality score with formula,
    all findings with evidence, and the analysis timestamp.
    """
    if dataset_id not in _results_store:
        raise HTTPException(status_code=404, detail="Dataset not found.")

    result = _results_store[dataset_id]

    report = {
        "report_title": "DataSentinel AI - Data Quality Report",
        "generated_at": datetime.now().isoformat(),
        "dataset_summary": {
            "filename": result.profile.filename,
            "rows": result.profile.row_count,
            "columns": result.profile.column_count,
            "analysis_timestamp": result.analysis_timestamp,
        },
        "quality_score": {
            "overall": result.quality_score.overall_score,
            "completeness": result.quality_score.completeness_score,
            "uniqueness": result.quality_score.uniqueness_score,
            "validity": result.quality_score.validity_score,
            "consistency": result.quality_score.consistency_score,
            "formula": result.quality_score.formula_explanation,
            "weights": result.quality_score.weights,
        },
        "total_issues": result.total_issues,
        "issues_by_severity": result.issues_by_severity,
        "issues_by_category": result.issues_by_category,
        "findings": [f.model_dump() for f in result.findings],
        "privacy_notice": (
            "This report was generated locally by DataSentinel AI. "
            "Do not upload confidential data to unapproved deployments. "
            "This tool has not undergone a formal security review."
        ),
    }

    report_json = json.dumps(report, indent=2, default=str)

    return StreamingResponse(
        io.BytesIO(report_json.encode()),
        media_type="application/json",
        headers={
            "Content-Disposition": f"attachment; filename=datasentinel_report_{dataset_id}.json"
        }
    )


@app.get("/api/export/{dataset_id}/csv")
async def export_csv_report(dataset_id: str):
    """
    Export findings as a CSV file.

    Each row is one finding with all fields.
    """
    if dataset_id not in _results_store:
        raise HTTPException(status_code=404, detail="Dataset not found.")

    result = _results_store[dataset_id]

    # Build CSV from findings
    rows = []
    for f in result.findings:
        rows.append({
            "Issue ID": f.issue_id,
            "Category": f.category.value,
            "Severity": f.severity.value,
            "Title": f.title,
            "Column": f.column or "N/A",
            "Count": f.count or "N/A",
            "Total": f.total or "N/A",
            "Percentage": f.percentage or "N/A",
            "Evidence": f.evidence,
            "Recommendation": f.recommendation,
        })

    if rows:
        export_df = pd.DataFrame(rows)
    else:
        export_df = pd.DataFrame(columns=[
            "Issue ID", "Category", "Severity", "Title", "Column",
            "Count", "Total", "Percentage", "Evidence", "Recommendation"
        ])

    csv_buffer = io.StringIO()
    export_df.to_csv(csv_buffer, index=False)

    return StreamingResponse(
        io.BytesIO(csv_buffer.getvalue().encode()),
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename=datasentinel_findings_{dataset_id}.csv"
        }
    )


# ─────────────────────────────────────────────
# STARTUP
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=settings.HOST, port=settings.PORT)
