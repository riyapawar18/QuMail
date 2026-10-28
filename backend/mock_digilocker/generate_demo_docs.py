"""
generate_demo_docs.py - Creates the synthetic PDFs served by the mock
DigiLocker document vault. Run once after cloning:

    python backend/mock_digilocker/generate_demo_docs.py

The generated files contain only fictitious data and are safe to publish.
"""

from pathlib import Path

OUT_DIR = Path(__file__).resolve().parent / "static" / "demo_docs"


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _build_pdf(title, subtitle, rows):
    lines = [
        "BT /F1 20 Tf 50 780 Td (%s) Tj ET" % _escape(title),
        "BT /F1 12 Tf 50 752 Td (%s) Tj ET" % _escape(subtitle),
    ]
    y = 710
    for label, value in rows:
        lines.append("BT /F1 10 Tf 50 %d Td (%s:) Tj ET" % (y, _escape(label)))
        lines.append("BT /F1 11 Tf 170 %d Td (%s) Tj ET" % (y, _escape(value)))
        y -= 26

    lines.append(
        "BT /F1 26 Tf 0.82 g 55 360 Td (SAMPLE - NOT A GOVERNMENT DOCUMENT) Tj ET"
    )
    lines.append("BT /F1 10 Tf 0.4 g 55 90 Td "
                 "(Generated for the QuMail demo. Fictitious data only. "
                 "Not issued or verified by any authority.) Tj ET")
    content = "\n".join(lines).encode("ascii")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
         b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
    ]

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += ("%d 0 obj\n" % index).encode() + obj + b"\nendobj\n"

    xref_at = len(out)
    out += ("xref\n0 %d\n" % (len(objects) + 1)).encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += ("%010d 00000 n \n" % offset).encode()
    out += (
        "trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
        % (len(objects) + 1, xref_at)
    ).encode()
    return bytes(out)


DOCUMENTS = {
    "sample_degree_certificate.pdf": (
        "Demo University",
        "Sample Degree Certificate",
        [
            ("Document", "Bachelor of Technology (Demonstration)"),
            ("Holder", "Demo Student"),
            ("Enrolment", "DEMO-2026-0001"),
            ("Issued", "15 January 2026"),
            ("Status", "Synthetic sample / not verified"),
        ],
    ),
    "sample_marksheet.pdf": (
        "Demo State Board",
        "Sample Academic Marksheet",
        [
            ("Holder", "Demo Student"),
            ("Roll number", "DEMO-ROLL-0001"),
            ("Mathematics", "88 (demo)"),
            ("Computer Science", "92 (demo)"),
            ("Status", "Synthetic sample / not verified"),
        ],
    ),
    "sample_identity_card.pdf": (
        "Demo Identity Issuer",
        "Sample Identity Document",
        [
            ("Name", "Demo User"),
            ("Demo ID", "DEMO-0000-0000"),
            ("Date of birth", "01 Jan 2000 (fictitious)"),
            ("Address", "1 Example Street, Demo City"),
            ("Status", "Synthetic sample / not an official ID"),
        ],
    ),
}


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for filename, (title, subtitle, rows) in DOCUMENTS.items():
        path = OUT_DIR / filename
        path.write_bytes(_build_pdf(title, subtitle, rows))
        print("wrote %s (%d bytes)" % (path, path.stat().st_size))


if __name__ == "__main__":
    main()
