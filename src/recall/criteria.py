from __future__ import annotations

import re

from recall.models import Criteria, Evidence, Finding, Rule, Snapshot, Stock, normalized


class EvidenceError(ValueError):
    pass


def nodes(rule: Rule) -> list[Rule]:
    return [rule] + [node for child in rule.children for node in nodes(child)]


def valid_span(span: Evidence, text: str) -> bool:
    return (
        0 <= span.start < span.end <= len(text)
        and text[span.start : span.end] == span.quote
        and bool(span.quote.strip())
    )


def validate_criteria(criteria: Criteria, snapshot: Snapshot) -> None:
    from recall.sources import URLS, digest, load_snapshot

    if digest(snapshot.text.encode()) != snapshot.source_hash:
        raise EvidenceError("Snapshot content hash is invalid.")
    if (
        snapshot.notice_id in URLS
        and snapshot.source_hash != load_snapshot(snapshot.notice_id).source_hash
    ):
        raise EvidenceError(
            "Changed source requires a reviewed criteria profile before new exclusions."
        )
    for node in nodes(criteria.root):
        if not valid_span(node.evidence, snapshot.text):
            raise EvidenceError("Evidence does not match the immutable source span.")
        if node.op in {"all", "any"}:
            if not node.children or node.values:
                raise EvidenceError("Boolean rules require children and no values.")
        elif node.children:
            raise EvidenceError("Leaf rules cannot have children.")
        if node.op in {"model_set", "date_code_set"}:
            if not node.values or len(set(node.values)) != len(node.values):
                raise EvidenceError("Identifier sets must be nonempty and unique.")
            for value in node.values:
                # Token boundaries prevent a quoted SAF-53D from supporting SAF-53.
                if not re.search(
                    r"(?<![A-Z0-9-])" + re.escape(value.upper()) + r"(?![A-Z0-9-])",
                    node.evidence.quote.upper(),
                ):
                    raise EvidenceError("Rule value is not supported by its cited identifier.")
        if node.op == "date_code_set":
            if "date code" not in node.evidence.quote.lower():
                raise EvidenceError("Date codes require explicit source labeling.")
            if not all(re.fullmatch(r"[0-9]{4}", v) for v in node.values):
                raise EvidenceError("Unsupported date-code syntax.")
        if node.op == "serial_range":
            if (
                not node.lower
                or not node.upper
                or not node.lower.isascii()
                or not node.lower.isdigit()
                or not node.upper.isdigit()
                or len(node.lower) != len(node.upper)
                or int(node.lower) > int(node.upper)
                or "serial" not in node.evidence.quote.lower()
                or node.lower not in node.evidence.quote
                or node.upper not in node.evidence.quote
            ):
                raise EvidenceError(
                    "Only explicitly cited fixed-width numeric serial ranges are supported."
                )
    for alias in criteria.aliases:
        if not valid_span(alias.evidence, snapshot.text):
            raise EvidenceError("Alias span is invalid.")
        pair = re.escape(alias.canonical) + r"\s*\(" + re.escape(alias.alias) + r"\)"
        if not re.search(pair, alias.evidence.quote, re.I):
            raise EvidenceError("Alias requires an explicit parenthetical source relationship.")
    if criteria.brand.upper() not in snapshot.text.upper():
        raise EvidenceError("Brand is absent from source.")
    # Structural checks do not prove semantic fidelity. Independent gold extraction scoring is separate.
    for node in nodes(criteria.root):
        if node.op == "all":
            models = [c for c in node.children if c.op == "model_set"]
            codes = [c for c in node.children if c.op == "date_code_set"]
            if codes and (len(models) != 1 or len(models[0].values) != 1):
                raise EvidenceError(
                    "Each date-code group must bind to exactly one canonical model."
                )
            if codes and models:
                # Require codes in the same model clause, not elsewhere in a multi-model quote.
                quote = node.evidence.quote
                model = models[0].values[0]
                clauses = re.split(r",\s+and model number", quote, flags=re.I)
                clause = next(
                    (
                        c
                        for c in clauses
                        if re.search(
                            r"(?<![A-Z0-9-])" + re.escape(model) + r"(?![A-Z0-9-])", c, re.I
                        )
                    ),
                    "",
                )
                if not all(v in clause for code in codes for v in code.values):
                    raise EvidenceError("Date code was attached to a different model's clause.")

    audit_source_coverage(criteria, snapshot)


def evaluate(rule: Rule, stock: Stock, criteria: Criteria) -> tuple[bool | None, list[str]]:
    if rule.op in {"all", "any"}:
        results = [evaluate(child, stock, criteria) for child in rule.children]
        vals = [r[0] for r in results]
        needed = sorted({f for value, fields in results if value is None for f in fields})
        if rule.op == "all":
            return (
                (False, []) if False in vals else ((None, needed) if None in vals else (True, []))
            )
        return (True, []) if True in vals else ((None, needed) if None in vals else (False, []))
    if rule.op == "unsupported":
        return None, []
    field = {"model_set": "model", "date_code_set": "date_code", "serial_range": "serial_number"}[
        rule.op
    ]
    identifier = getattr(stock, field)
    if identifier.state != "present":
        return None, [field]
    value = normalized(identifier.value)
    if rule.op == "model_set":
        for alias in criteria.aliases:
            if value in {normalized(alias.alias), normalized(f"{alias.canonical} ({alias.alias})")}:
                value = normalized(alias.canonical)
        return value in {normalized(v) for v in rule.values}, []
    if rule.op == "date_code_set":
        return value in rule.values, []
    if not (
        value.isascii()
        and value.isdigit()
        and rule.lower
        and rule.upper
        and len(value) == len(rule.lower)
    ):
        return None, ["serial_number"]
    return int(rule.lower) <= int(value) <= int(rule.upper), []


def match(stock: Stock, snapshot: Snapshot, criteria: Criteria | None) -> Finding:

    def uncertain(reason: str, required: list[str] | None = None) -> Finding:
        return Finding(
            stock_id=stock.stock_id,
            notice_id=snapshot.notice_id,
            source_hash=snapshot.source_hash,
            status="needs_information",
            reason=reason,
            required_fields=required or [],
        )

    if not snapshot.complete or criteria is None or not criteria.complete or criteria.unsupported:
        return uncertain(
            "Source or criteria are incomplete or unsupported. Review this specific notice."
        )
    try:
        validate_criteria(criteria, snapshot)
    except EvidenceError as exc:
        return uncertain(f"Criteria evidence rejected: {exc}")
    if any(n.op == "unsupported" for n in nodes(criteria.root)):
        return uncertain("Unsupported source condition requires manual review.")
    if stock.brand.state != "present" or stock.model.state != "present":
        return uncertain(
            "Supply an unambiguous brand and model from the unit label.",
            [k for k in ("brand", "model") if getattr(stock, k).state != "present"],
        )
    if normalized(stock.brand.value or "") != normalized(criteria.brand):
        return uncertain(
            "Brand differs from this notice; check the label. Search coverage is limited."
        )
    # An invalid identifier cannot support a negative conclusion, even on a nonmatching branch.
    if stock.date_code.state == "malformed" or stock.serial_number.state == "malformed":
        return uncertain(
            "An identifier is malformed or conflicting; inspect the unit label.",
            [k for k in ("date_code", "serial_number") if getattr(stock, k).state == "malformed"],
        )
    value, needed = evaluate(criteria.root, stock, criteria)
    spans = list(
        {(n.evidence.start, n.evidence.end): n.evidence for n in nodes(criteria.root)}.values()
    )
    if value is None:
        finding = uncertain("A required identifier is missing, empty, or unsupported.", needed)
        finding.evidence = spans
        return finding
    return Finding(
        stock_id=stock.stock_id,
        notice_id=snapshot.notice_id,
        source_hash=snapshot.source_hash,
        status="affected_by_notice" if value else "excluded_by_notice",
        reason=(
            "Validated identifiers satisfy this notice's supported criteria."
            if value
            else "Identifiers fall outside this notice's explicit model or code criteria. "
            "This is not a general safety determination."
        ),
        evidence=spans,
    )


def aggregate(findings: list[Finding]) -> str:
    active = [f for f in findings if f.valid]
    if any(f.status == "affected_by_notice" for f in active):
        return "affected_by_notice"
    if not active or any(f.status == "needs_information" for f in active):
        return "needs_information"
    return "excluded_by_notice"


def audit_source_coverage(criteria: Criteria, snapshot: Snapshot) -> None:
    """Conservative grammar audit for the supported English notice descriptions.

    This checks omissions and associations, not just whether a quote exists. New
    prose formats require review instead of silently broadening the interpreter.
    """
    description = snapshot.text.split("Description:", 1)[-1].strip().split(". ", 1)[0]
    tokens = set(re.findall(r"\b[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+\b", description))
    leaves = nodes(criteria.root)
    extracted = {v for n in leaves if n.op == "model_set" for v in n.values}
    aliases = {a.alias for a in criteria.aliases}
    if not tokens or extracted | aliases != tokens or extracted & aliases:
        raise EvidenceError(
            "Model enumeration or aliases do not cover the source description exactly."
        )
    if "date code" in description.lower():
        groups = re.findall(
            r"model number ([A-Z0-9-]+)\s*\([A-Z0-9-]+\) with date codes? ([0-9]{4}(?: and [0-9]{4})?)",
            description,
        )
        actual = []
        for n in leaves:
            if n.op == "all":
                models = [c.values for c in n.children if c.op == "model_set"]
                codes = [c.values for c in n.children if c.op == "date_code_set"]
                if len(models) == len(codes) == 1:
                    actual.append((models[0][0], tuple(sorted(codes[0]))))
        expected = [(m, tuple(sorted(re.findall(r"[0-9]{4}", c)))) for m, c in groups]
        if not expected or sorted(actual) != sorted(expected):
            raise EvidenceError("Date-code groups do not faithfully cover the source clauses.")
        if (
            criteria.root.op != "any"
            or len(criteria.root.children) != len(expected)
            or any(c.op != "all" or len(c.children) != 2 for c in criteria.root.children)
        ):
            raise EvidenceError("Source alternatives require OR across exact model/code groups.")
    elif any(n.op == "date_code_set" for n in leaves):
        raise EvidenceError(
            "Source description does not support additional identifier constraints."
        )
    elif not any(n.op == "serial_range" for n in leaves) and criteria.root.op != "model_set":
        raise EvidenceError("An enumerated model list requires a single exact model set.")
