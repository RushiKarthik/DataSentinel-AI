"""
DataSentinel AI — AI Investigation Agent

This module implements a controlled, inspectable agent workflow.
It does NOT pretend to be multiple autonomous agents.

HOW IT WORKS:
1. The user selects a finding and asks a question.
2. The agent calls specific TOOLS (Python functions) to gather evidence.
3. If an LLM API key is configured, the structured evidence is sent to
   the model to generate a human-readable explanation.
4. If no API key is available, a rule-based template generates the response.
5. The response always shows which tools were called and what evidence was used.

IMPORTANT SAFETY RULES:
- The agent never fabricates issue counts or evidence.
- The agent never executes generated SQL or code automatically.
- The agent never modifies the uploaded dataset.
- Generated code snippets are TEXT ONLY — for human review.
- The agent clearly distinguishes facts from possible causes.
"""

import os
from typing import Optional
from app.models import (
    AnalysisResult, DataQualityFinding, InvestigationResponse,
    Severity, IssueCategory
)
from app.config import settings


# ─────────────────────────────────────────────
# AGENT TOOLS — callable functions
# ─────────────────────────────────────────────

def tool_profile_dataset(result: AnalysisResult) -> dict:
    """
    Tool 1: profile_dataset
    Returns dataset dimensions, column types, and basic statistics.
    """
    profile = result.profile
    return {
        "tool": "profile_dataset",
        "filename": profile.filename,
        "rows": profile.row_count,
        "columns": profile.column_count,
        "column_details": [
            {
                "name": c.name,
                "type": c.dtype,
                "null_pct": c.null_percentage,
                "unique": c.unique_count,
            }
            for c in profile.columns
        ],
        "memory_mb": profile.memory_usage_mb,
    }


def tool_run_quality_checks(result: AnalysisResult) -> dict:
    """
    Tool 2: run_quality_checks
    Returns the structured findings from the deterministic engine.
    """
    return {
        "tool": "run_quality_checks",
        "total_issues": result.total_issues,
        "quality_score": result.quality_score.overall_score,
        "by_severity": result.issues_by_severity,
        "by_category": result.issues_by_category,
        "findings_summary": [
            {
                "id": f.issue_id,
                "title": f.title,
                "severity": f.severity.value,
                "category": f.category.value,
                "column": f.column,
                "count": f.count,
                "percentage": f.percentage,
            }
            for f in result.findings
        ],
    }


def tool_inspect_issue(result: AnalysisResult, issue_id: str) -> dict:
    """
    Tool 3: inspect_issue
    Returns detailed evidence for a specific finding.
    """
    finding = next((f for f in result.findings if f.issue_id == issue_id), None)
    if not finding:
        return {
            "tool": "inspect_issue",
            "error": f"No finding with ID '{issue_id}' exists.",
        }

    return {
        "tool": "inspect_issue",
        "issue_id": finding.issue_id,
        "title": finding.title,
        "category": finding.category.value,
        "severity": finding.severity.value,
        "description": finding.description,
        "column": finding.column,
        "count": finding.count,
        "total": finding.total,
        "percentage": finding.percentage,
        "evidence": finding.evidence,
        "recommendation": finding.recommendation,
    }


def tool_generate_recommendations(
    result: AnalysisResult, issue_id: Optional[str] = None
) -> dict:
    """
    Tool 4: generate_recommendations
    Produces investigation/remediation steps based on the findings.
    """
    if issue_id:
        finding = next((f for f in result.findings if f.issue_id == issue_id), None)
        if not finding:
            return {"tool": "generate_recommendations", "error": "Finding not found."}
        findings_to_analyze = [finding]
    else:
        # Prioritize: critical first, then high
        findings_to_analyze = sorted(
            result.findings,
            key=lambda f: list(Severity).index(f.severity)
        )[:5]  # Top 5 most severe

    recommendations = []
    for f in findings_to_analyze:
        rec = {
            "issue": f.title,
            "severity": f.severity.value,
            "action": f.recommendation,
        }

        # Generate investigation code snippet
        if f.column:
            if f.category == IssueCategory.COMPLETENESS:
                rec["python_snippet"] = (
                    f"# Investigate missing values in '{f.column}'\n"
                    f"missing_rows = df[df['{f.column}'].isnull()]\n"
                    f"print(f'Missing rows: {{len(missing_rows)}}')\n"
                    f"print(missing_rows.head(10))"
                )
                rec["sql_snippet"] = (
                    f"-- Find rows with missing '{f.column}'\n"
                    f"SELECT * FROM your_table\n"
                    f"WHERE \"{f.column}\" IS NULL\n"
                    f"LIMIT 20;"
                )
            elif f.category == IssueCategory.UNIQUENESS:
                rec["python_snippet"] = (
                    f"# Find duplicate values in '{f.column}'\n"
                    f"dup_values = df[df['{f.column}'].duplicated(keep=False)]\n"
                    f"print(dup_values.sort_values('{f.column}').head(20))"
                )
                rec["sql_snippet"] = (
                    f"-- Find duplicate values in '{f.column}'\n"
                    f"SELECT \"{f.column}\", COUNT(*) as cnt\n"
                    f"FROM your_table\n"
                    f"GROUP BY \"{f.column}\"\n"
                    f"HAVING COUNT(*) > 1\n"
                    f"ORDER BY cnt DESC\n"
                    f"LIMIT 20;"
                )
            elif f.category == IssueCategory.VALIDITY:
                rec["python_snippet"] = (
                    f"# Inspect invalid values in '{f.column}'\n"
                    f"print(df['{f.column}'].value_counts(dropna=False).head(20))"
                )
            elif f.category == IssueCategory.CONSISTENCY:
                rec["python_snippet"] = (
                    f"# Investigate outliers in '{f.column}'\n"
                    f"print(df['{f.column}'].describe())\n"
                    f"q1 = df['{f.column}'].quantile(0.25)\n"
                    f"q3 = df['{f.column}'].quantile(0.75)\n"
                    f"iqr = q3 - q1\n"
                    f"outliers = df[(df['{f.column}'] < q1 - 1.5*iqr) | (df['{f.column}'] > q3 + 1.5*iqr)]\n"
                    f"print(f'Outlier rows: {{len(outliers)}}')\n"
                    f"print(outliers.head(10))"
                )

        recommendations.append(rec)

    return {
        "tool": "generate_recommendations",
        "recommendations": recommendations,
        "note": "Review all code snippets before executing. They are suggestions for investigation.",
    }


# ─────────────────────────────────────────────
# RULE-BASED RESPONSE (no LLM needed)
# ─────────────────────────────────────────────

def _build_rule_based_response(
    question: str,
    result: AnalysisResult,
    issue_id: Optional[str],
    tools_called: list[str],
    evidence_used: list[str],
) -> InvestigationResponse:
    """
    Generate a structured response using templates when no LLM is available.

    This ensures the quality engine works fully without an API key.
    """
    finding = None
    if issue_id:
        finding = next((f for f in result.findings if f.issue_id == issue_id), None)

    question_lower = question.lower()

    # Build response based on question type
    if finding:
        # Specific issue investigation
        answer_parts = [
            f"**Investigation: {finding.title}**\n",
            f"**Category:** {finding.category.value.title()}",
            f"**Severity:** {finding.severity.value.upper()}",
            f"**Affected Column:** {finding.column or 'Multiple/All'}",
            "",
            f"**What was found:**\n{finding.description}",
            "",
            f"**Evidence:**\n{finding.evidence}",
            "",
            f"**Recommended Action:**\n{finding.recommendation}",
        ]

        # Add context based on question
        if any(w in question_lower for w in ['why', 'matter', 'important', 'impact']):
            severity_impact = {
                Severity.CRITICAL: "This is a CRITICAL issue affecting data reliability. It likely impacts downstream reports and decisions.",
                Severity.HIGH: "This is a HIGH severity issue that could lead to incorrect analysis results.",
                Severity.MEDIUM: "This is a MEDIUM severity issue worth investigating to improve data quality.",
                Severity.LOW: "This is a LOW severity issue. It's worth noting but may be acceptable.",
                Severity.INFO: "This is informational — no immediate action needed.",
            }
            answer_parts.append(f"\n**Why it matters:**\n{severity_impact.get(finding.severity, '')}")

        suggested_code = None
        code_explanation = None
        recs = tool_generate_recommendations(result, issue_id)
        if recs.get("recommendations"):
            rec = recs["recommendations"][0]
            if "python_snippet" in rec:
                suggested_code = rec["python_snippet"]
                code_explanation = (
                    "This Python snippet helps you investigate the issue interactively. "
                    "Load your CSV into a pandas DataFrame named 'df' first. "
                    "Review the output before making any changes."
                )
            if "sql_snippet" in rec:
                if suggested_code:
                    suggested_code += f"\n\n# SQL equivalent:\n# {rec['sql_snippet']}"
                else:
                    suggested_code = rec["sql_snippet"]
                    code_explanation = "This SQL query helps investigate the issue in a database context."

        return InvestigationResponse(
            answer="\n".join(answer_parts),
            evidence_used=evidence_used,
            tools_called=tools_called,
            mode="rule-based",
            suggested_code=suggested_code,
            code_explanation=code_explanation,
        )
    else:
        # General dataset question
        score = result.quality_score
        answer_parts = [
            f"**Dataset Analysis Summary**\n",
            f"**Dataset:** {result.profile.filename}",
            f"**Dimensions:** {result.profile.row_count:,} rows × {result.profile.column_count} columns",
            f"**Quality Score:** {score.overall_score}/100",
            "",
            f"**Score Breakdown:**",
            f"- Completeness: {score.completeness_score}/100 (weight: {score.weights['completeness']})",
            f"- Uniqueness: {score.uniqueness_score}/100 (weight: {score.weights['uniqueness']})",
            f"- Validity: {score.validity_score}/100 (weight: {score.weights['validity']})",
            f"- Consistency: {score.consistency_score}/100 (weight: {score.weights['consistency']})",
            "",
            f"**Total Issues Found:** {result.total_issues}",
        ]

        if result.issues_by_severity:
            answer_parts.append("\n**Issues by Severity:**")
            for sev, count in sorted(result.issues_by_severity.items()):
                answer_parts.append(f"- {sev.upper()}: {count}")

        # Top findings
        critical_high = [f for f in result.findings if f.severity in (Severity.CRITICAL, Severity.HIGH)]
        if critical_high:
            answer_parts.append("\n**Priority Findings (Critical/High):**")
            for f in critical_high[:5]:
                answer_parts.append(f"- [{f.severity.value.upper()}] {f.title}")

        answer_parts.append(
            "\n*This is a rule-based summary. Configure an LLM API key for "
            "AI-powered explanations and deeper investigation.*"
        )

        return InvestigationResponse(
            answer="\n".join(answer_parts),
            evidence_used=evidence_used,
            tools_called=tools_called,
            mode="rule-based",
        )


# ─────────────────────────────────────────────
# LLM-POWERED RESPONSE
# ─────────────────────────────────────────────

async def _build_llm_response(
    question: str,
    result: AnalysisResult,
    issue_id: Optional[str],
    tools_called: list[str],
    evidence_used: list[str],
    tool_outputs: list[dict],
) -> InvestigationResponse:
    """
    Use an LLM to generate a natural-language explanation from structured evidence.

    The LLM receives ONLY the tool outputs (facts) and the user's question.
    It is instructed to explain findings, not invent new ones.
    """
    try:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)

        system_prompt = (
            "You are a data quality analyst assistant. You help users understand "
            "data quality findings from the DataSentinel AI quality engine.\n\n"
            "RULES:\n"
            "1. Base your answers ONLY on the provided tool outputs (evidence).\n"
            "2. Never fabricate issue counts, percentages, or findings.\n"
            "3. Clearly distinguish confirmed facts from possible causes.\n"
            "4. If evidence is insufficient, say so explicitly.\n"
            "5. When suggesting code, present it as text for human review only.\n"
            "6. Never claim to execute code or modify data.\n"
            "7. Be concise but thorough."
        )

        user_message = (
            f"User question: {question}\n\n"
            f"Evidence from quality engine tools:\n"
            f"{str(tool_outputs)}\n\n"
            f"Provide a clear, evidence-based answer. Use markdown formatting."
        )

        response = await client.chat.completions.create(
            model=settings.LLM_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            max_tokens=1000,
            temperature=0.3,
        )

        answer = response.choices[0].message.content

        # Extract code snippets from recommendations tool
        suggested_code = None
        code_explanation = None
        recs_output = next((t for t in tool_outputs if t.get("tool") == "generate_recommendations"), None)
        if recs_output and recs_output.get("recommendations"):
            rec = recs_output["recommendations"][0]
            if "python_snippet" in rec:
                suggested_code = rec["python_snippet"]
                code_explanation = "Review this code before executing. It's a starting point for investigation."

        return InvestigationResponse(
            answer=answer,
            evidence_used=evidence_used,
            tools_called=tools_called,
            mode="ai",
            suggested_code=suggested_code,
            code_explanation=code_explanation,
        )

    except Exception as e:
        # Fall back to rule-based if LLM fails
        fallback = _build_rule_based_response(question, result, issue_id, tools_called, evidence_used)
        fallback.answer = (
            f"*AI mode encountered an error: {str(e)}. Showing rule-based analysis instead.*\n\n"
            + fallback.answer
        )
        return fallback


# ─────────────────────────────────────────────
# MAIN INVESTIGATION WORKFLOW
# ─────────────────────────────────────────────

async def investigate(
    question: str,
    result: AnalysisResult,
    issue_id: Optional[str] = None,
) -> InvestigationResponse:
    """
    Main investigation entry point.

    This is the controlled agent workflow:
    1. Receive user's question and optional issue ID.
    2. Call the appropriate tools to gather evidence.
    3. Use the tool results as source of truth.
    4. Generate a response (LLM or rule-based).

    The workflow is transparent — the response always shows
    which tools were called and what evidence was used.
    """
    tools_called = []
    evidence_used = []
    tool_outputs = []

    # Always start with dataset profile
    profile_output = tool_profile_dataset(result)
    tools_called.append("profile_dataset")
    evidence_used.append(f"Dataset: {result.profile.filename} ({result.profile.row_count} rows, {result.profile.column_count} columns)")
    tool_outputs.append(profile_output)

    # Always include quality check results
    quality_output = tool_run_quality_checks(result)
    tools_called.append("run_quality_checks")
    evidence_used.append(f"Quality score: {result.quality_score.overall_score}/100, {result.total_issues} issues found")
    tool_outputs.append(quality_output)

    # If investigating a specific issue, inspect it
    if issue_id:
        issue_output = tool_inspect_issue(result, issue_id)
        tools_called.append("inspect_issue")
        if "error" not in issue_output:
            evidence_used.append(f"Issue: {issue_output.get('title', issue_id)}")
        tool_outputs.append(issue_output)

    # Generate recommendations
    recs_output = tool_generate_recommendations(result, issue_id)
    tools_called.append("generate_recommendations")
    evidence_used.append("Generated investigation recommendations")
    tool_outputs.append(recs_output)

    # Choose response mode
    if settings.has_llm_key:
        return await _build_llm_response(
            question, result, issue_id, tools_called, evidence_used, tool_outputs
        )
    else:
        return _build_rule_based_response(
            question, result, issue_id, tools_called, evidence_used
        )
