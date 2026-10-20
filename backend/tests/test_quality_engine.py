"""
DataSentinel AI — Backend Tests

These tests verify the data quality engine with EXACT EXPECTED COUNTS
based on the known issues in the sample dataset.

Run with: pytest tests/ -v
"""

import pytest
import pandas as pd
import numpy as np
from io import StringIO

from app.quality_engine import (
    check_completeness,
    check_uniqueness,
    check_validity,
    check_consistency,
    calculate_quality_score,
    analyze_dataset,
    profile_dataset,
)
from app.models import IssueCategory, Severity
from app.sample_data import get_sample_dataframe, EXPECTED_ISSUES


class TestSampleDataProfile:
    """Test that the sample dataset has the expected structure."""

    def setup_method(self):
        self.df = get_sample_dataframe()

    def test_row_count(self):
        """Sample data should have exactly 30 rows."""
        assert len(self.df) == EXPECTED_ISSUES["total_rows"], \
            f"Expected {EXPECTED_ISSUES['total_rows']} rows, got {len(self.df)}"

    def test_column_count(self):
        """Sample data should have exactly 7 columns."""
        assert len(self.df.columns) == EXPECTED_ISSUES["total_columns"], \
            f"Expected {EXPECTED_ISSUES['total_columns']} columns, got {len(self.df.columns)}"

    def test_column_names(self):
        """Sample data should have the expected column names."""
        expected = {'employee_id', 'name', 'email', 'department', 'hire_date', 'salary', 'status'}
        assert set(self.df.columns) == expected

    def test_profile_creation(self):
        """Profile should contain correct metadata."""
        profile = profile_dataset(self.df, "test.csv")
        assert profile.row_count == 30
        assert profile.column_count == 7
        assert profile.filename == "test.csv"
        assert len(profile.columns) == 7


class TestCompletenessChecks:
    """Test missing value detection."""

    def setup_method(self):
        self.df = get_sample_dataframe()
        self.findings = check_completeness(self.df)

    def test_missing_name_count(self):
        """Should detect exactly 3 missing name values."""
        name_finding = next(
            (f for f in self.findings if f.issue_id == "completeness-name-missing"),
            None
        )
        assert name_finding is not None, "Missing name finding not found"
        assert name_finding.count == EXPECTED_ISSUES["missing_name"], \
            f"Expected {EXPECTED_ISSUES['missing_name']} missing names, got {name_finding.count}"

    def test_missing_email_count(self):
        """Should detect exactly 5 missing email values."""
        email_finding = next(
            (f for f in self.findings if f.issue_id == "completeness-email-missing"),
            None
        )
        assert email_finding is not None, "Missing email finding not found"
        assert email_finding.count == EXPECTED_ISSUES["missing_email"], \
            f"Expected {EXPECTED_ISSUES['missing_email']} missing emails, got {email_finding.count}"

    def test_missing_salary_count(self):
        """Should detect exactly 2 missing salary values."""
        salary_finding = next(
            (f for f in self.findings if f.issue_id == "completeness-salary-missing"),
            None
        )
        assert salary_finding is not None, "Missing salary finding not found"
        assert salary_finding.count == EXPECTED_ISSUES["missing_salary"], \
            f"Expected {EXPECTED_ISSUES['missing_salary']} missing salaries, got {salary_finding.count}"

    def test_missing_department_count(self):
        """Should detect exactly 4 missing department values."""
        dept_finding = next(
            (f for f in self.findings if f.issue_id == "completeness-department-missing"),
            None
        )
        assert dept_finding is not None, "Missing department finding not found"
        assert dept_finding.count == EXPECTED_ISSUES["missing_department"], \
            f"Expected {EXPECTED_ISSUES['missing_department']} missing departments, got {dept_finding.count}"

    def test_whitespace_only_strings_count_as_missing(self):
        df = pd.DataFrame({"value": ["kept", "  ", None]})
        findings = check_completeness(df)
        finding = next(f for f in findings if f.issue_id == "completeness-value-missing")
        profile = profile_dataset(df, "whitespace.csv")
        assert finding.count == 2
        assert "1 null(s) and 1 empty string(s)" in finding.description
        assert profile.columns[0].null_count == 1
        assert profile.columns[0].missing_count == 2

    def test_sparse_unnamed_column_is_flagged_as_possible_formatting_artifact(self):
        df = pd.DataFrame({"Unnamed: 9": ["retained"] + [None] * 9})
        findings = check_completeness(df)
        sparse = next(f for f in findings if f.issue_id == "completeness-Unnamed: 9-missing")
        overall = next(f for f in findings if f.issue_id == "completeness-overall")
        result = analyze_dataset(df, "sparse.csv", "sparse")

        assert sparse.count == 9
        assert sparse.title == "Sparse unnamed column 'Unnamed: 9' — possible CSV formatting artifact"
        assert "possible CSV formatting artifact, not proof" in sparse.evidence
        assert "Review 'Unnamed: 9' against the source CSV" in sparse.recommendation
        assert "Preserve its populated values" in sparse.recommendation
        assert "do not add this finding to that total again" in sparse.recommendation
        assert overall.count == 9  # The column finding is a breakdown, not an additional count.
        assert "Per-column findings are a breakdown" in overall.evidence
        assert result.profile.columns[0].non_null_count == 1
        assert result.quality_score.completeness_score == 10

    def test_overall_completeness_exists(self):
        """Should have an overall completeness finding."""
        overall = next(
            (f for f in self.findings if f.issue_id == "completeness-overall"),
            None
        )
        assert overall is not None, "Overall completeness finding not found"
        assert overall.count > 0

    def test_findings_have_evidence(self):
        """Every finding should have evidence explaining the detection method."""
        for f in self.findings:
            assert f.evidence, f"Finding {f.issue_id} has no evidence"
            assert len(f.evidence) > 20, f"Finding {f.issue_id} has insufficient evidence"


class TestUniquenessChecks:
    """Test duplicate detection."""

    def setup_method(self):
        self.df = get_sample_dataframe()
        self.findings = check_uniqueness(self.df)

    def test_duplicate_rows_detected(self):
        """Should detect duplicate rows."""
        dup_finding = next(
            (f for f in self.findings if f.issue_id == "uniqueness-duplicate-rows"),
            None
        )
        assert dup_finding is not None, "Duplicate rows finding not found"
        assert dup_finding.count == EXPECTED_ISSUES["duplicate_rows"], \
            f"Expected {EXPECTED_ISSUES['duplicate_rows']} rows in dup groups, got {dup_finding.count}"

    def test_duplicate_ids_detected(self):
        """Should detect duplicate employee_ids."""
        id_finding = next(
            (f for f in self.findings if "employee_id" in f.issue_id),
            None
        )
        assert id_finding is not None, "Duplicate employee_id finding not found"
        assert id_finding.count > 0


class TestValidityChecks:
    """Test format validation."""

    def setup_method(self):
        self.df = get_sample_dataframe()
        self.findings = check_validity(self.df)

    def test_invalid_emails_detected(self):
        """Should detect invalid email formats."""
        email_finding = next(
            (f for f in self.findings if "invalid-email" in f.issue_id),
            None
        )
        assert email_finding is not None, "Invalid email finding not found"
        assert email_finding.count == EXPECTED_ISSUES["invalid_emails"], \
            f"Expected {EXPECTED_ISSUES['invalid_emails']} invalid emails, got {email_finding.count}"

    def test_invalid_dates_detected(self):
        """Should detect invalid date formats."""
        date_finding = next(
            (f for f in self.findings if "invalid-date" in f.issue_id),
            None
        )
        assert date_finding is not None, "Invalid date finding not found"
        assert date_finding.count == EXPECTED_ISSUES["invalid_dates"], \
            f"Expected {EXPECTED_ISSUES['invalid_dates']} invalid dates, got {date_finding.count}"

    def test_blank_values_are_only_reported_as_missing_not_invalid(self):
        df = pd.DataFrame({
            "email": ["alice@example.com", "bob@example.com", "   ", None, ""],
            "created_date": ["2025-01-01", "2025-01-02", " ", None, ""],
            "amount": ["10", "20", " ", None, ""],
        })
        result = analyze_dataset(df, "blank-values.csv", "blank-values")
        validity = [f for f in result.findings if f.category == IssueCategory.VALIDITY]
        overall = next(f for f in result.findings if f.issue_id == "completeness-overall")

        assert validity == []
        assert overall.count == 9
        assert sum(column.missing_count for column in result.profile.columns) == 9
        assert result.quality_score.validity_score == 100
        assert result.quality_score.completeness_score == 40

class TestConsistencyChecks:
    """Test outlier and anomaly detection."""

    def setup_method(self):
        self.df = get_sample_dataframe()
        self.findings = check_consistency(self.df)

    def test_salary_outliers_detected(self):
        """Should detect salary outliers (350000 and -5000)."""
        outlier_finding = next(
            (f for f in self.findings if "salary" in f.issue_id and "outlier" in f.issue_id),
            None
        )
        assert outlier_finding is not None, "Salary outlier finding not found"
        assert outlier_finding.count >= 1, "Should detect at least 1 salary outlier"

    def test_negative_salary_detected(self):
        """Should detect negative salary value."""
        neg_finding = next(
            (f for f in self.findings if "salary" in f.issue_id and "negative" in f.issue_id),
            None
        )
        assert neg_finding is not None, "Negative salary finding not found"
        assert neg_finding.count == 1  # Only -5000


class TestQualityScore:
    """Test the quality score calculation."""

    def setup_method(self):
        self.df = get_sample_dataframe()
        all_findings = (
            check_completeness(self.df) +
            check_uniqueness(self.df) +
            check_validity(self.df) +
            check_consistency(self.df)
        )
        self.score = calculate_quality_score(self.df, all_findings)

    def test_score_range(self):
        """Score should be between 0 and 100."""
        assert 0 <= self.score.overall_score <= 100, \
            f"Score {self.score.overall_score} is outside valid range"

    def test_score_reflects_issues(self):
        """Score should be below 100 for a dataset with issues."""
        assert self.score.overall_score < 100, \
            "Sample dataset has known issues, score should be below 100"

    def test_category_scores_exist(self):
        """All category scores should be present and in range."""
        assert 0 <= self.score.completeness_score <= 100
        assert 0 <= self.score.uniqueness_score <= 100
        assert 0 <= self.score.validity_score <= 100
        assert 0 <= self.score.consistency_score <= 100

    def test_formula_explanation_present(self):
        """Score should include a formula explanation."""
        assert self.score.formula_explanation
        assert len(self.score.formula_explanation) > 50

    def test_weights_sum_to_one(self):
        """Weights should sum to 1.0."""
        total = sum(self.score.weights.values())
        assert abs(total - 1.0) < 0.001, f"Weights sum to {total}, expected 1.0"


class TestFullAnalysisPipeline:
    """Test the complete analysis pipeline."""

    def setup_method(self):
        self.df = get_sample_dataframe()
        self.result = analyze_dataset(self.df, "sample.csv", "test-001")

    def test_result_has_all_fields(self):
        """Result should contain profile, score, and findings."""
        assert self.result.profile is not None
        assert self.result.quality_score is not None
        assert self.result.findings is not None
        assert self.result.dataset_id == "test-001"

    def test_total_issues_matches_findings(self):
        """total_issues should match the length of findings list."""
        assert self.result.total_issues == len(self.result.findings)

    def test_severity_counts_consistent(self):
        """Severity counts should sum to total issues."""
        total_from_severity = sum(self.result.issues_by_severity.values())
        assert total_from_severity == self.result.total_issues

    def test_category_counts_consistent(self):
        """Category counts should sum to total issues."""
        total_from_category = sum(self.result.issues_by_category.values())
        assert total_from_category == self.result.total_issues


class TestEmptyAndMalformedData:
    """Test behavior with edge cases."""

    def test_empty_dataframe(self):
        """Should handle empty DataFrame without crashing."""
        df = pd.DataFrame()
        # Should not raise an exception
        findings = check_completeness(df)
        assert isinstance(findings, list)

    def test_single_column(self):
        """Should work with a single-column dataset."""
        df = pd.DataFrame({"value": [1, 2, None, 4, 5]})
        result = analyze_dataset(df, "single.csv", "test-single")
        assert result.profile.column_count == 1
        assert result.profile.row_count == 5

    def test_all_nulls_column(self):
        """Should handle a column that's entirely null."""
        df = pd.DataFrame({"empty_col": [None, None, None], "ok": [1, 2, 3]})
        findings = check_completeness(df)
        empty_finding = next(
            (f for f in findings if f.column == "empty_col"),
            None
        )
        assert empty_finding is not None
        assert empty_finding.percentage == 100.0
        assert empty_finding.severity == Severity.CRITICAL

    def test_no_issues_dataset(self):
        """Should give perfect score for clean data."""
        df = pd.DataFrame({
            "name": ["Alice", "Bob", "Carol"],
            "age": [30, 25, 35],
        })
        result = analyze_dataset(df, "clean.csv", "test-clean")
        assert result.quality_score.overall_score == 100.0
        assert result.total_issues == 0

    def test_all_duplicates(self):
        """Should handle dataset where every row is a duplicate."""
        df = pd.DataFrame({
            "a": [1, 1, 1],
            "b": ["x", "x", "x"],
        })
        findings = check_uniqueness(df)
        dup_finding = next(
            (f for f in findings if f.issue_id == "uniqueness-duplicate-rows"),
            None
        )
        assert dup_finding is not None
        assert dup_finding.count == 3  # All 3 rows are in duplicate groups
