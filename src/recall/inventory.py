import csv
import io

from recall.models import Identifier, Stock, display_guard

MAX_BYTES = 1_000_000
MAX_ROWS = 1000
HEADERS = {"stock_id", "brand", "model", "date_code", "serial_number", "location"}
REQUIRED = {"stock_id", "brand", "model", "location"}


class InventoryError(ValueError):
    pass


def read_inventory(data: bytes) -> list[Stock]:
    if len(data) > MAX_BYTES:
        raise InventoryError("CSV exceeds 1 MB.")
    try:
        text = data.decode("utf-8-sig")
        display_guard(text)
    except (UnicodeDecodeError, ValueError) as exc:
        raise InventoryError("Use UTF-8 text without forbidden display punctuation.") from exc
    reader = csv.DictReader(io.StringIO(text), strict=True)
    headers = reader.fieldnames or []
    if len(headers) != len(set(headers)) or not REQUIRED <= set(headers) or set(headers) - HEADERS:
        raise InventoryError(
            "Headers must be unique, supported, and include stock_id,brand,model,location."
        )
    rows: list[Stock] = []
    errors: list[str] = []
    seen: set[str] = set()
    try:
        for line, row in enumerate(reader, 2):
            if line > MAX_ROWS + 1:
                raise InventoryError("CSV exceeds 1000 stock units.")
            if None in row or any(row.get(h) is None for h in headers):
                errors.append(f"Row {line}: column count differs from header.")
                continue
            stock_id = row["stock_id"].strip()
            if not stock_id or stock_id in seen or len(stock_id) > 80:
                errors.append(
                    f"Row {line}: stock_id must be nonempty, unique, and at most 80 characters."
                )
                continue
            seen.add(stock_id)
            if any(len(v) > 200 for v in row.values() if v is not None):
                errors.append(f"Row {line}: cell exceeds 200 characters.")
                continue
            identifiers = {
                name: Identifier.parse(row.get(name), name)
                for name in ("brand", "model", "date_code", "serial_number")
            }
            issues = [
                f"Row {line}: {k} is {v.state}."
                for k, v in identifiers.items()
                if v.state != "present" and (k in ("brand", "model") or v.state == "malformed")
            ]
            rows.append(
                Stock(stock_id=stock_id, location=row["location"], issues=issues, **identifiers)
            )
    except csv.Error as exc:
        raise InventoryError(f"Malformed CSV: {exc}") from exc
    if errors:
        raise InventoryError("\n".join(errors[:20]))
    if not rows:
        raise InventoryError("CSV contains no stock units.")
    return rows


def safe_cell(value: object) -> str:
    text = str(value)
    display_guard(text)
    # Spreadsheet apps can ignore leading whitespace before formula markers.
    return (
        "'" + text
        if text.lstrip().startswith(("=", "+", "-", "@")) or text.startswith(("\t", "\r"))
        else text
    )


def export_csv(headers: list[str], rows: list[list[object]]) -> str:
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(headers)
    for row in rows:
        writer.writerow([safe_cell(cell) for cell in row])
    return output.getvalue()
