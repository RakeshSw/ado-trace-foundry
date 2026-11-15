"""Read-only, evidence-grounded coverage assessment using Azure OpenAI v1 Responses API."""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path
from xml.etree import ElementTree as ET


def _json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _clean(text):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", str(text or ""))).strip()


def _steps(raw):
    """Preserve action/expected distinction in Azure DevOps test-step XML."""
    if not raw:
        return []
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return [{"step": 0, "unparsed_source": str(raw)[:20000]}]
    result = []
    for idx, node in enumerate(root.findall(".//step"), 1):
        parts = [_clean(el.text) for el in node.findall("parameterizedString")]
        result.append({"step": int(node.attrib.get("id") or idx),
                       "action": parts[0] if parts else "",
                       "expected": parts[1] if len(parts) > 1 else ""})
    return result


def evidence_from_export(root: Path, requirement_id: int):
    nodes = {}
    with (root / "workitems.jsonl").open(encoding="utf-8") as fp:
        for line in fp:
            if line.strip():
                node = json.loads(line)
                nodes[int(node["id"])] = node
    edges = _json(root / "relationships.json")
    story = nodes.get(requirement_id)
    if not story:
        raise ValueError(f"Work item {requirement_id} not in export")
    tests = sorted({int(e["target"]) for e in edges
                    if e["kind"] == "tested-by" and int(e["source"]) == requirement_id})
    # Only explicit Tested-By edges establish which cases assess this requirement.
    if not tests:
        raise ValueError(f"Work item {requirement_id} has no linked tests in export")
    missing = [x for x in tests if x not in nodes]
    if missing:
        raise ValueError(f"Linked test IDs are absent from export: {missing}")

    req_fields = story.get("fields") or {}
    req = {"id": requirement_id, "type": story.get("type"), "title": story.get("title"),
           "revision": story.get("revision"), "url": story.get("url"),
           "description": _clean(req_fields.get("System.Description")),
           "acceptance_criteria": _clean(req_fields.get("Microsoft.VSTS.Common.AcceptanceCriteria"))}
    cases = []
    for tid in tests:
        n = nodes[tid]
        fields = n.get("fields") or {}
        cases.append({"id": tid, "title": n.get("title"), "revision": n.get("revision"),
                      "url": n.get("url"), "description": _clean(fields.get("System.Description")),
                      "steps": _steps(fields.get("Microsoft.VSTS.TCM.Steps"))})
    return {"requirement": req, "test_cases": cases,
            "source": "Azure DevOps read-only local export", "assessment_scope": "linked test cases only"}


def _response_text(result):
    chunks = []
    for item in result.get("output", []):
        for part in item.get("content", []):
            if part.get("type") == "output_text" and isinstance(part.get("text"), str):
                chunks.append(part["text"])
    if not chunks:
        raise ValueError("Model returned no output_text; cannot create assessment")
    return "\n".join(chunks).strip()


def _model_json(text):
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I).strip()
    obj = json.loads(text)
    if not isinstance(obj, dict) or not isinstance(obj.get("criteria"), list):
        raise ValueError("Model output must be a JSON object with a criteria array")
    return obj


def _validate_model(model_obj, evidence):
    allowed = {case["id"]: {step["step"] for step in case["steps"] if step["step"] > 0}
               for case in evidence["test_cases"]}
    valid_status = {"covered", "partial", "uncovered", "insufficient_evidence"}
    issues = []
    cleaned = []
    for idx, item in enumerate(model_obj["criteria"]):
        if not isinstance(item, dict):
            issues.append(f"criterion {idx}: invalid object")
            continue
        status = item.get("status")
        if status not in valid_status:
            issues.append(f"criterion {idx}: invalid status")
            status = "insufficient_evidence"
        good = []
        for citation in item.get("evidence", []):
            if not isinstance(citation, dict):
                issues.append(f"criterion {idx}: malformed evidence")
                continue
            tid, step = citation.get("test_case_id"), citation.get("step")
            if type(tid) is int and type(step) is int and tid in allowed and step in allowed[tid]:
                good.append({"test_case_id": tid, "step": step})
            else:
                issues.append(f"criterion {idx}: unsupported test-step citation {tid}/{step}")
        if status == "covered" and not good:
            status = "insufficient_evidence"
            issues.append(f"criterion {idx}: covered downgraded; no valid cited test step")
        cleaned.append({"criterion": str(item.get("criterion") or ""), "status": status,
                        "rationale": str(item.get("rationale") or ""), "evidence": good})
    return {"requirement_id": evidence["requirement"]["id"],
            "model": model_obj.get("model"), "criteria": cleaned,
            "suggested_tests": [str(x) for x in model_obj.get("suggested_tests", []) if isinstance(x, str)],
            "validation_warnings": issues,
            "disclaimer": "Model-assisted review; evidence references are checked for existence, not semantic correctness."}


def call_azure_responses(evidence, *, transport=None):
    endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT", "").rstrip("/")
    deployment = os.environ.get("AZURE_OPENAI_DEPLOYMENT", "")
    key = os.environ.get("AZURE_OPENAI_API_KEY", "")
    if not endpoint or not deployment or not key:
        raise ValueError("AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_DEPLOYMENT and AZURE_OPENAI_API_KEY are required")
    from urllib.parse import urlsplit
    parsed = urlsplit(endpoint)
    if parsed.scheme != "https" or not parsed.hostname or not (
        parsed.hostname.endswith(".openai.azure.com") or
        parsed.hostname.endswith(".services.ai.azure.com") or
        parsed.hostname.endswith(".cognitiveservices.azure.com")):
        raise ValueError("Azure endpoint must be an HTTPS Azure AI hostname")
    if parsed.path not in ("", "/", "/openai/v1", "/openai/v1/") or parsed.query or parsed.fragment:
        raise ValueError("Endpoint must be the resource root or /openai/v1, without query parameters")
    url = endpoint.rstrip("/")
    if not url.endswith("/openai/v1"):
        url += "/openai/v1"
    url += "/responses"
    instruction = (
        "Assess only the provided requirement and linked test cases. Do not invent acceptance criteria or coverage. "
        "Return ONLY a JSON object with keys criteria (array), suggested_tests (array). Each criterion object "
        "has criterion (verbatim or close excerpt from actual acceptance criteria), status (covered, partial, "
        "uncovered, insufficient_evidence), rationale, and evidence (array of {test_case_id: integer, step: integer}). "
        "Cite a test step only if it actually supports the assessment. For covered status cite at least one step. "
        "When acceptance criteria are absent, explicitly report insufficient_evidence and avoid fabricated criteria. "
        "Do not treat test case titles or links alone as functional coverage."
    )
    payload = {"model": deployment,
               "instructions": instruction,
               "input": json.dumps(evidence, ensure_ascii=False)}
    request = urllib.request.Request(url,
         data=json.dumps(payload).encode("utf-8"), method="POST",
         headers={"api-key": key, "Content-Type": "application/json"})
    try:
        if transport is None:
            with urllib.request.urlopen(request, timeout=120) as response:
                result = json.load(response)
        else:
            result = transport(request)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Azure OpenAI HTTP {exc.code}; verify route, deployment and quota") from exc
    if result.get("status") not in (None, "completed"):
        raise RuntimeError(f"Model response status: {result.get('status')}")
    assessment = _validate_model(_model_json(_response_text(result)), evidence)
    assessment["model"] = deployment
    return assessment


def run_coverage(root: Path, requirement_id: int, output: Path, provider: str):
    if provider != "azure-openai":
        raise ValueError("Only --provider azure-openai is implemented")
    evidence = evidence_from_export(root, requirement_id)
    # Evidence is persisted before API call for reproducibility. Never include credentials.
    output.mkdir(parents=True, exist_ok=True)
    stem = f"requirement-{requirement_id}"
    (output / f"{stem}-evidence.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    assessment = call_azure_responses(evidence)
    (output / f"{stem}-assessment.json").write_text(
        json.dumps(assessment, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = [f"# AI Coverage Assessment — Work Item {requirement_id}", "",
              f"Model: `{assessment['model']}`", "",
              "Model-assisted analysis; validate findings against the original work items.", ""]
    for item in assessment["criteria"]:
        cited = ", ".join(f"TC {e['test_case_id']} step {e['step']}" for e in item["evidence"]) or "None"
        report += [f"## {item['criterion']}", f"Status: **{item['status']}**", f"Evidence: {cited}",
                   f"Rationale: {item['rationale']}", ""]
    if assessment["suggested_tests"]:
        report += ["## Suggested tests", ""]
        report += [f"- {x}" for x in assessment["suggested_tests"]]
    if assessment["validation_warnings"]:
        report += ["", "## Validation warnings", ""]
        report += [f"- {x}" for x in assessment["validation_warnings"]]
    (output / f"{stem}-report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    return assessment
