"""
DataSentinel AI — Data Quality Engine

This is the core deterministic engine that detects data quality issues.
Every check is a pure Python function using Pandas. No LLM is involved
in the detection — findings are based on exact counts, percentages,
and documented statistical methods.

HOW IT WORKS:
1. The user uploads a CSV file.
2. Pandas loads it into a DataFrame.
3. Each check function examines the DataFrame and returns findings.
4. Findings are combined and a quality score is calculated.
5. Results go to the frontend for display.

SEVERITY RULES (transparent, not AI-generated):
- CRITICAL: >30% of data affected or fundamental structural problems
- HIGH: 10-30% of data affected
- MEDIUM: 1-10% of data affected
- LOW: <1% of data affected or minor observations
- INFO: Informational, no action needed
"""

import pandas as pd
import re
from datetime import datetime
from app.models import (
    DataQualityFinding, ColumnProfile, DatasetProfile,
    QualityScore, Severity, IssueCategory, AnalysisResult
)
from app.config import settings


def _assign_severity(percentage: float) -> Severity:
    """
    Assign severity based on the percentage of data affected.

    This is a transparent rule — the thresholds are documented here
    and shown in the UI. No LLM decides the severity.

    Args:
        percentage: The percentage of rows/values affected (0-100).

    Returns:
        A Severity enum value.
    """
    if percentage > 30:
        return Severity.CRITICAL
    elif percentage > 10:
        return Severity.HIGH
    elif percentage > 1:
        return Severity.MEDIUM
    elif percentage > 0:
        return Severity.LOW
    else:
        return Severity.INFO


# ─────────────────────────────────────────────
# A. COMPLETENESS CHECKS
# ─────────────────────────────────────────────

_PANDAS_UNNAMED_COLUMN = re.compile(r"Unnamed:\s*\d+(?:_level_\d+)?$")


def _missing_counts(series: pd.Series) -> tuple[int, int]:
    """Return null and blank-string counts without counting any cell twice."""
    null_count = int(series.isna().sum())
    if pd.api.types.is_string_dtype(series.dtype) or series.dtype == object:
        blank_count = int(series.map(lambda value: isinstance(value, str) and not value.strip()).sum())
    else:
        blank_count = 0
    return null_count, blank_count


def _nonblank_nonnull_values(series: pd.Series) -> pd.Series:
    """Return values that belong in validity checks, not completeness checks."""
    values = series.dropna()
    if pd.api.types.is_string_dtype(series.dtype) or series.dtype == object:
        values = values[~values.map(lambda value: isinstance(value, str) and not value.strip())]
    return values

def check_completeness(df: pd.DataFrame) -> list[DataQualityFinding]:
    """
    Check for missing values in every column.

    Method: Uses pandas .isnull() to count NaN/None values per column.
    Also counts empty and whitespace-only strings as effectively missing.

    Returns one finding per column that has missing values,
    plus an overall completeness finding.
    """
    findings = []
    total_cells = df.shape[0] * df.shape[1]
    total_missing = 0

    for col in df.columns:
        # Count null and blank values separately; their sets do not overlap.
        null_count, empty_str_count = _missing_counts(df[col])

        missing = null_count + empty_str_count
        total_missing += missing

        if missing > 0:
            pct = round((missing / len(df)) * 100, 2)
            severity = _assign_severity(pct)
            sparse_unnamed = (
                _PANDAS_UNNAMED_COLUMN.fullmatch(str(col)) is not None
                and missing / len(df) >= 0.8
                and missing < len(df)
            )
            title = (
                f"Sparse unnamed column '{col}' — possible CSV formatting artifact"
                if sparse_unnamed else f"Missing values in '{col}'"
            )
            description = (
                f"Column '{col}' has {missing} missing value(s) "
                f"out of {len(df)} rows ({pct}%). "
                f"This includes {null_count} null(s) and {empty_str_count} empty string(s)."
            )
            recommendation = (
                f"Review '{col}' against the source CSV and its delimiter/quoting rules to determine "
                "whether the sparsity is expected or may reflect a formatting artifact. Preserve its "
                "populated values while investigating. This column's missing cells are already included "
                "once in the overall completeness count; do not add this finding to that total again."
                if sparse_unnamed else (
                    f"Investigate why '{col}' has missing values. "
                    "Consider whether nulls should be filled with defaults, imputed from other data, "
                    "or flagged for upstream data providers."
                )
            )

            findings.append(DataQualityFinding(
                issue_id=f"completeness-{col}-missing",
                category=IssueCategory.COMPLETENESS,
                severity=severity,
                title=title,
                description=description,
                column=col,
                count=missing,
                total=len(df),
                percentage=pct,
                evidence=(
                    f"Detected using pandas .isnull() for null values "
                    f"and whitespace-trimmed string comparison for blank strings. "
                    f"Null count: {null_count}, Empty string count: {empty_str_count}."
                    + (" The 'Unnamed' header pattern and at least 80% missing rate are signals of a possible CSV formatting artifact, not proof; populated values were retained." if sparse_unnamed else "")
                ),
                recommendation=recommendation
            ))

    # Overall completeness finding
    if total_cells > 0:
        overall_missing_pct = round((total_missing / total_cells) * 100, 2)
        if total_missing > 0:
            findings.append(DataQualityFinding(
                issue_id="completeness-overall",
                category=IssueCategory.COMPLETENESS,
                severity=_assign_severity(overall_missing_pct),
                title="Overall data completeness",
                description=(
                    f"Dataset has {total_missing} missing cell(s) out of "
                    f"{total_cells} total cells ({overall_missing_pct}% missing)."
                ),
                column=None,
                count=total_missing,
                total=total_cells,
                percentage=overall_missing_pct,
                evidence=(
                    f"Counted once per data cell across all columns, including null and blank-string cells. "
                    f"Per-column findings are a breakdown of this total, not additional missing cells. "
                    f"Total cells: {df.shape[0]} rows × {df.shape[1]} columns = {total_cells}."
                ),
                recommendation=(
                    "Review columns with the highest missing-value rates first and determine if missingness is random or systematic. "
                    "This overall count includes each missing cell once; do not add the per-column findings to it again."
                )
            ))

    return findings


# ─────────────────────────────────────────────
# B. UNIQUENESS CHECKS
# ─────────────────────────────────────────────

def check_uniqueness(df: pd.DataFrame) -> list[DataQualityFinding]:
    """
    Check for duplicate rows and duplicate values in likely identifier columns.

    Method:
    - Exact duplicate rows: uses pandas .duplicated() with keep=False
      to find all rows that are exact copies of another row.
    - Duplicate IDs: checks columns whose name contains 'id', 'key', 'code',
      or 'number' for duplicate values. These columns are often expected
      to be unique identifiers.

    The check clearly distinguishes between duplicate rows (entire row
    is repeated) and duplicate IDs (a specific identifier column has
    repeated values).
    """
    findings = []

    # Check for exact duplicate rows
    dup_mask = df.duplicated(keep=False)
    dup_count = int(dup_mask.sum())

    if dup_count > 0:
        # Number of unique duplicate groups
        dup_groups = int(df[dup_mask].duplicated(keep='first').sum())
        pct = round((dup_count / len(df)) * 100, 2)

        findings.append(DataQualityFinding(
            issue_id="uniqueness-duplicate-rows",
            category=IssueCategory.UNIQUENESS,
            severity=_assign_severity(pct),
            title="Exact duplicate rows detected",
            description=(
                f"Found {dup_count} rows that are exact duplicates of other rows "
                f"({pct}% of all rows). This means {dup_groups} row(s) are redundant copies."
            ),
            column=None,
            count=dup_count,
            total=len(df),
            percentage=pct,
            evidence=(
                f"Detected using pandas .duplicated(keep=False) which flags all rows "
                f"in duplicate groups. {dup_count} total rows are in duplicate groups, "
                f"of which {dup_groups} are redundant copies (keeping one from each group)."
            ),
            recommendation=(
                "Determine whether duplicates are data entry errors or legitimate "
                "repeated records. Use df.drop_duplicates() to remove if confirmed redundant."
            )
        ))

    # Check for duplicate values in likely identifier columns
    id_patterns = re.compile(r'(^id$|_id$|^id_|key|code|number|num$|no$)', re.IGNORECASE)
    for col in df.columns:
        if id_patterns.search(col):
            dup_values = df[col].dropna().duplicated(keep=False)
            dup_val_count = int(dup_values.sum())

            if dup_val_count > 0:
                non_null = int(df[col].dropna().shape[0])
                pct = round((dup_val_count / non_null) * 100, 2) if non_null > 0 else 0

                findings.append(DataQualityFinding(
                    issue_id=f"uniqueness-{col}-duplicate-ids",
                    category=IssueCategory.UNIQUENESS,
                    severity=_assign_severity(pct),
                    title=f"Duplicate values in identifier column '{col}'",
                    description=(
                        f"Column '{col}' appears to be an identifier but has "
                        f"{dup_val_count} duplicate value(s) out of {non_null} "
                        f"non-null values ({pct}%). Identifiers are usually expected to be unique."
                    ),
                    column=col,
                    count=dup_val_count,
                    total=non_null,
                    percentage=pct,
                    evidence=(
                        f"Column name matches identifier pattern. "
                        f"Checked non-null values using pandas .duplicated(keep=False). "
                        f"Found {dup_val_count} values that appear more than once."
                    ),
                    recommendation=(
                        f"Verify whether '{col}' should be a unique identifier. "
                        f"If so, investigate duplicate values and fix at the data source."
                    )
                ))

    return findings


# ─────────────────────────────────────────────
# C. VALIDITY CHECKS
# ─────────────────────────────────────────────

def check_validity(df: pd.DataFrame) -> list[DataQualityFinding]:
    """
    Check for invalid values in columns based on their inferred types.

    Method:
    - Numeric columns: checks for non-numeric values mixed into what should
      be numeric data (using pd.to_numeric with errors='coerce').
    - Date columns: checks columns with 'date', 'time', 'created', 'updated'
      in their name for values that cannot be parsed as dates.
    - Email columns: checks columns with 'email' in name for basic format.
    - Blank strings are handled by completeness and excluded from validity checks.
    """
    findings = []

    for col in df.columns:
        col_lower = col.lower()

        # Check for invalid numerics in numeric-looking columns
        if (pd.api.types.is_string_dtype(df[col].dtype) or df[col].dtype == object):
            # Try converting to numeric to find mixed types
            non_null_original = _nonblank_nonnull_values(df[col])
            numeric_converted = pd.to_numeric(non_null_original, errors='coerce')

            if len(non_null_original) > 0:
                # Blank strings are already reported by completeness.
                non_numeric_count = int(numeric_converted.isna().sum())
                numeric_count = int(numeric_converted.notna().sum())

                # Only flag if more than half the non-null values are numeric
                # (indicating this should probably be a numeric column)
                if numeric_count > 0 and numeric_count > len(non_null_original) * 0.5 and non_numeric_count > 0:
                    pct = round((non_numeric_count / len(non_null_original)) * 100, 2)
                    findings.append(DataQualityFinding(
                        issue_id=f"validity-{col}-non-numeric",
                        category=IssueCategory.VALIDITY,
                        severity=_assign_severity(pct),
                        title=f"Non-numeric values in mostly-numeric column '{col}'",
                        description=(
                            f"Column '{col}' appears to contain mostly numeric data, "
                            f"but {non_numeric_count} value(s) cannot be converted to numbers."
                        ),
                        column=col,
                        count=non_numeric_count,
                        total=len(non_null_original),
                        percentage=pct,
                        evidence=(
                            f"Used pd.to_numeric(errors='coerce') to attempt conversion. "
                            f"{numeric_count} values converted successfully, "
                            f"{non_numeric_count} could not be converted."
                        ),
                        recommendation=(
                            f"Review the non-numeric values in '{col}'. They may be data-entry "
                            f"errors, units appended to numbers, or special codes."
                        )
                    ))

        # Check date columns
        date_patterns = re.compile(r'(date|time|created|updated|timestamp|_at$|_on$)', re.IGNORECASE)
        if date_patterns.search(col_lower) and (pd.api.types.is_string_dtype(df[col].dtype) or df[col].dtype == object):
            non_null_vals = _nonblank_nonnull_values(df[col])
            if len(non_null_vals) > 0:
                parsed = pd.to_datetime(non_null_vals, errors='coerce')
                invalid_dates = int(parsed.isna().sum())

                if invalid_dates > 0:
                    pct = round((invalid_dates / len(non_null_vals)) * 100, 2)
                    findings.append(DataQualityFinding(
                        issue_id=f"validity-{col}-invalid-dates",
                        category=IssueCategory.VALIDITY,
                        severity=_assign_severity(pct),
                        title=f"Invalid date values in '{col}'",
                        description=(
                            f"Column '{col}' appears to be a date column but {invalid_dates} "
                            f"value(s) could not be parsed as valid dates."
                        ),
                        column=col,
                        count=invalid_dates,
                        total=len(non_null_vals),
                        percentage=pct,
                        evidence=(
                            f"Used pd.to_datetime(errors='coerce', infer_datetime_format=True). "
                            f"{len(non_null_vals) - invalid_dates} parsed successfully, "
                            f"{invalid_dates} returned NaT (Not a Time)."
                        ),
                        recommendation=(
                            f"Inspect the unparseable values in '{col}'. Common causes: "
                            f"inconsistent date formats, typos, or placeholder values like '0000-00-00'."
                        )
                    ))

        # Check email columns
        if 'email' in col_lower and (pd.api.types.is_string_dtype(df[col].dtype) or df[col].dtype == object):
            non_null_vals = _nonblank_nonnull_values(df[col])
            if len(non_null_vals) > 0:
                email_regex = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
                invalid_mask = ~non_null_vals.astype(str).str.match(email_regex)
                invalid_count = int(invalid_mask.sum())

                if invalid_count > 0:
                    pct = round((invalid_count / len(non_null_vals)) * 100, 2)
                    findings.append(DataQualityFinding(
                        issue_id=f"validity-{col}-invalid-email",
                        category=IssueCategory.VALIDITY,
                        severity=_assign_severity(pct),
                        title=f"Invalid email format in '{col}'",
                        description=(
                            f"Column '{col}' has {invalid_count} value(s) that don't match "
                            f"a basic email format (text@domain.tld)."
                        ),
                        column=col,
                        count=invalid_count,
                        total=len(non_null_vals),
                        percentage=pct,
                        evidence=(
                            f"Validated against regex pattern: ^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$ "
                            f"(basic format check, not RFC-5322 compliant). "
                            f"Found {invalid_count} non-matching values."
                        ),
                        recommendation=(
                            f"Review invalid emails in '{col}'. This is a basic format check — "
                            f"some valid but unusual addresses may be flagged."
                        )
                    ))

    return findings


# ─────────────────────────────────────────────
# D. CONSISTENCY & ANOMALY CHECKS
# ─────────────────────────────────────────────

def check_consistency(df: pd.DataFrame) -> list[DataQualityFinding]:
    """
    Check for statistical outliers and inconsistencies.

    Method:
    - Numeric outliers: Uses the Interquartile Range (IQR) method.
      Values below Q1 - 1.5*IQR or above Q3 + 1.5*IQR are flagged.
      This is a standard statistical method, NOT a claim that outliers
      are errors — they are candidates for investigation.
    - Negative amounts: Checks columns with 'amount', 'price', 'cost',
      'salary', 'revenue', 'total' in name for negative values.
    """
    findings = []

    for col in df.columns:
        col_lower = col.lower()

        # Numeric outlier detection using IQR
        if pd.api.types.is_numeric_dtype(df[col]):
            clean_vals = df[col].dropna()
            if len(clean_vals) >= 10:  # Need enough data for meaningful statistics
                q1 = clean_vals.quantile(0.25)
                q3 = clean_vals.quantile(0.75)
                iqr = q3 - q1

                if iqr > 0:  # Avoid flagging constant columns
                    lower_bound = q1 - 1.5 * iqr
                    upper_bound = q3 + 1.5 * iqr

                    outlier_mask = (clean_vals < lower_bound) | (clean_vals > upper_bound)
                    outlier_count = int(outlier_mask.sum())

                    if outlier_count > 0:
                        pct = round((outlier_count / len(clean_vals)) * 100, 2)
                        findings.append(DataQualityFinding(
                            issue_id=f"consistency-{col}-outliers",
                            category=IssueCategory.CONSISTENCY,
                            severity=_assign_severity(pct),
                            title=f"Statistical outliers in '{col}'",
                            description=(
                                f"Column '{col}' has {outlier_count} value(s) outside the "
                                f"expected range [{round(lower_bound, 2)}, {round(upper_bound, 2)}]. "
                                f"These are candidates for investigation, not necessarily errors."
                            ),
                            column=col,
                            count=outlier_count,
                            total=len(clean_vals),
                            percentage=pct,
                            evidence=(
                                f"IQR method: Q1={round(q1, 2)}, Q3={round(q3, 2)}, "
                                f"IQR={round(iqr, 2)}. "
                                f"Lower bound: Q1 - 1.5*IQR = {round(lower_bound, 2)}. "
                                f"Upper bound: Q3 + 1.5*IQR = {round(upper_bound, 2)}. "
                                f"Values outside these bounds: {outlier_count}."
                            ),
                            recommendation=(
                                f"Review outlier values in '{col}'. They may be valid extreme "
                                f"values, data entry errors, or unit mismatches. Do not remove "
                                f"outliers without understanding their cause."
                            )
                        ))

        # Check for negative values in amount/price columns
        amount_patterns = re.compile(
            r'(amount|price|cost|salary|revenue|total|fee|charge|balance)',
            re.IGNORECASE
        )
        if amount_patterns.search(col_lower) and pd.api.types.is_numeric_dtype(df[col]):
            neg_count = int((df[col].dropna() < 0).sum())
            non_null = int(df[col].dropna().shape[0])

            if neg_count > 0:
                pct = round((neg_count / non_null) * 100, 2) if non_null > 0 else 0
                findings.append(DataQualityFinding(
                    issue_id=f"consistency-{col}-negative",
                    category=IssueCategory.CONSISTENCY,
                    severity=Severity.MEDIUM if neg_count > 1 else Severity.LOW,
                    title=f"Negative values in amount column '{col}'",
                    description=(
                        f"Column '{col}' appears to represent a monetary amount but has "
                        f"{neg_count} negative value(s). This may indicate returns, "
                        f"corrections, or data errors."
                    ),
                    column=col,
                    count=neg_count,
                    total=non_null,
                    percentage=pct,
                    evidence=(
                        f"Column name matches amount/price pattern. "
                        f"Found {neg_count} values less than zero out of {non_null} non-null values."
                    ),
                    recommendation=(
                        f"Verify whether negative values in '{col}' are intentional "
                        f"(e.g., refunds, adjustments) or errors."
                    )
                ))

    return findings


# ─────────────────────────────────────────────
# QUALITY SCORE CALCULATION
# ─────────────────────────────────────────────

def calculate_quality_score(
    df: pd.DataFrame,
    findings: list[DataQualityFinding]
) -> QualityScore:
    """
    Calculate a transparent, documented quality score.

    Formula:
        overall_score = Σ (weight_i × category_score_i)

    Where each category_score is calculated as:
        category_score = 100 × (1 - issue_rate)

    Issue rates:
    - Completeness: total missing cells / total cells
    - Uniqueness: duplicate rows / total rows
    - Validity: invalid values / total values checked
    - Consistency: outlier values / total values checked

    Weights (from config.py):
    - Completeness: 0.35
    - Uniqueness: 0.25
    - Validity: 0.25
    - Consistency: 0.15

    The score is a SUMMARY metric, not a guarantee. Always check
    the individual findings for specific issues.
    """
    weights = settings.QUALITY_WEIGHTS
    total_cells = df.shape[0] * df.shape[1] if df.shape[1] > 0 else 1

    # Calculate completeness score
    completeness_findings = [f for f in findings if f.category == IssueCategory.COMPLETENESS]
    overall_comp = next((f for f in completeness_findings if f.issue_id == "completeness-overall"), None)
    comp_rate = (overall_comp.percentage / 100) if overall_comp else 0
    completeness_score = round(100 * (1 - comp_rate), 2)

    # Calculate uniqueness score
    dup_finding = next((f for f in findings if f.issue_id == "uniqueness-duplicate-rows"), None)
    dup_rate = (dup_finding.percentage / 100) if dup_finding else 0
    uniqueness_score = round(100 * (1 - dup_rate), 2)

    # Calculate validity score
    validity_findings = [f for f in findings if f.category == IssueCategory.VALIDITY]
    if validity_findings:
        avg_validity_pct = sum(f.percentage for f in validity_findings if f.percentage) / len(validity_findings)
        validity_rate = avg_validity_pct / 100
    else:
        validity_rate = 0
    validity_score = round(100 * (1 - validity_rate), 2)

    # Calculate consistency score
    consistency_findings = [f for f in findings if f.category == IssueCategory.CONSISTENCY]
    if consistency_findings:
        avg_consistency_pct = sum(f.percentage for f in consistency_findings if f.percentage) / len(consistency_findings)
        consistency_rate = avg_consistency_pct / 100
    else:
        consistency_rate = 0
    consistency_score = round(100 * (1 - consistency_rate), 2)

    # Weighted overall score
    overall = round(
        weights["completeness"] * completeness_score +
        weights["uniqueness"] * uniqueness_score +
        weights["validity"] * validity_score +
        weights["consistency"] * consistency_score,
        2
    )

    formula_explanation = (
        f"Score = "
        f"({weights['completeness']} × Completeness:{completeness_score}) + "
        f"({weights['uniqueness']} × Uniqueness:{uniqueness_score}) + "
        f"({weights['validity']} × Validity:{validity_score}) + "
        f"({weights['consistency']} × Consistency:{consistency_score}) "
        f"= {overall}/100. "
        f"Each category score = 100 × (1 - issue_rate). "
        f"Higher is better. This score is a summary — always review individual findings."
    )

    return QualityScore(
        overall_score=overall,
        completeness_score=completeness_score,
        uniqueness_score=uniqueness_score,
        validity_score=validity_score,
        consistency_score=consistency_score,
        formula_explanation=formula_explanation,
        weights=weights,
    )


# ─────────────────────────────────────────────
# DATASET PROFILING
# ─────────────────────────────────────────────

def profile_dataset(df: pd.DataFrame, filename: str) -> DatasetProfile:
    """
    Create a structured profile of the uploaded dataset.

    This provides the summary information shown in the dashboard:
    row count, column count, types, and sample values.
    """
    columns = []
    for col in df.columns:
        non_null = int(df[col].notna().sum())
        null_count, blank_count = _missing_counts(df[col])
        missing_count = null_count + blank_count
        null_pct = round((null_count / len(df)) * 100, 2) if len(df) > 0 else 0
        unique_count = int(df[col].nunique())

        # Get sample values (up to 5, avoiding nulls)
        sample_vals = df[col].dropna().head(5).tolist()
        sample_vals = [str(v) for v in sample_vals]  # Convert to strings for JSON safety

        columns.append(ColumnProfile(
            name=col,
            dtype=str(df[col].dtype),
            non_null_count=non_null,
            null_count=null_count,
            missing_count=missing_count,
            null_percentage=null_pct,
            unique_count=unique_count,
            sample_values=sample_vals,
        ))

    memory_mb = round(df.memory_usage(deep=True).sum() / (1024 * 1024), 3)

    return DatasetProfile(
        filename=filename,
        row_count=len(df),
        column_count=len(df.columns),
        columns=columns,
        memory_usage_mb=memory_mb,
        upload_timestamp=datetime.now().isoformat(),
    )


# ─────────────────────────────────────────────
# MAIN ANALYSIS PIPELINE
# ─────────────────────────────────────────────

def analyze_dataset(df: pd.DataFrame, filename: str, dataset_id: str) -> AnalysisResult:
    """
    Run the full data quality analysis pipeline.

    This is the main entry point called by the API endpoint.
    It runs all checks, calculates the score, and packages everything
    into a single AnalysisResult.

    Args:
        df: The loaded pandas DataFrame
        filename: Original filename for the profile
        dataset_id: Unique identifier for this analysis session

    Returns:
        AnalysisResult with profile, score, and all findings
    """
    # Step 1: Profile the dataset
    profile = profile_dataset(df, filename)

    # Step 2: Run all deterministic checks
    findings = []
    findings.extend(check_completeness(df))
    findings.extend(check_uniqueness(df))
    findings.extend(check_validity(df))
    findings.extend(check_consistency(df))

    # Step 3: Calculate quality score from findings
    quality_score = calculate_quality_score(df, findings)

    # Step 4: Aggregate statistics
    severity_counts = {}
    for sev in Severity:
        count = len([f for f in findings if f.severity == sev])
        if count > 0:
            severity_counts[sev.value] = count

    category_counts = {}
    for cat in IssueCategory:
        count = len([f for f in findings if f.category == cat])
        if count > 0:
            category_counts[cat.value] = count

    return AnalysisResult(
        dataset_id=dataset_id,
        profile=profile,
        quality_score=quality_score,
        findings=findings,
        analysis_timestamp=datetime.now().isoformat(),
        total_issues=len(findings),
        issues_by_severity=severity_counts,
        issues_by_category=category_counts,
    )
