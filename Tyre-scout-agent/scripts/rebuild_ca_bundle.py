"""Rebuild this workstation's TLS bundle from certifi and Windows-trusted CAs.

Run with the tire-scout environment's Python. No network calls, installations,
registry changes, or TLS-verification overrides. Original files are backed up.
The SHA-1 thumbprints identify existing Windows trust-store entries; they are
not used to verify certificate signatures. See docs/SSL_FIX_REPORT.md.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import ssl
import time
from datetime import datetime, timezone

import certifi


DEFAULT_CA_THUMBPRINTS = [
    "831A93699724F7517DDCA71B4D25FFA80C59CE95",  # Apollo root CA
    "DBFCD20DE7B7B7D5652B9E71E28B7D863D316EBB",  # Apollo forward trust CA
]
SERVER_AUTH = "1.3.6.1.5.5.7.3.1"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ca-thumbprint", action="append",
                        help="Windows ROOT CA thumbprint; repeat for its chain")
    args = parser.parse_args()
    wanted = list(dict.fromkeys(
        t.replace(" ", "").upper()
        for t in (args.ca_thumbprint or DEFAULT_CA_THUMBPRINTS)))
    if any(len(t) != 40 or any(c not in "0123456789ABCDEF" for c in t)
           for t in wanted):
        parser.error("CA thumbprints must be 40 hexadecimal characters")

    trusted = {}
    for der, encoding, trust in ssl.enum_certificates("ROOT"):
        thumbprint = hashlib.sha1(der).hexdigest().upper()
        if thumbprint in wanted and encoding == "x509_asn" \
                and (trust is True or SERVER_AUTH in trust):
            trusted[thumbprint] = der
    missing = set(wanted) - trusted.keys()
    if missing:
        raise RuntimeError(f"CAs missing from Windows server-auth trust: {sorted(missing)}")

    ca_pems, certificates = [], []
    for thumbprint in wanted:
        pem = ssl.DER_cert_to_PEM_cert(trusted[thumbprint])
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.load_verify_locations(cadata=pem)
        ca_info = context.get_ca_certs()
        if len(ca_info) != 1:
            raise RuntimeError(f"Not a CA certificate: {thumbprint}")
        info = ca_info[0]
        if not ssl.cert_time_to_seconds(info["notBefore"]) <= time.time() \
                <= ssl.cert_time_to_seconds(info["notAfter"]):
            raise RuntimeError(f"CA outside validity period: {thumbprint}")
        ca_pems.append(pem.strip().encode("ascii") + b"\n")
        certificates.append({"thumbprint": thumbprint,
                             "sha256": hashlib.sha256(trusted[thumbprint]).hexdigest(),
                             "subject": info["subject"], "not_after": info["notAfter"]})

    corporate = b"\n".join(ca_pems)
    public = Path(certifi.where()).read_bytes().rstrip() + b"\n"
    bundle = public + b"\n" + corporate
    # Validate the complete candidate before changing any working file.
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.load_verify_locations(cadata=bundle.decode("ascii"))
    public_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    public_context.load_verify_locations(cadata=public.decode("ascii"))
    expected = {hashlib.sha256(c).hexdigest()
                for c in public_context.get_ca_certs(binary_form=True)}
    expected.update(hashlib.sha256(trusted[t]).hexdigest() for t in wanted)
    actual = {hashlib.sha256(c).hexdigest()
              for c in context.get_ca_certs(binary_form=True)}
    if actual != expected:
        raise RuntimeError("Candidate bundle does not contain the expected CAs")

    root = Path(__file__).resolve().parents[1]
    certs = root / "certs"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = certs / "backups" / stamp
    backup.mkdir(parents=True)
    for name in ("corp-root.crt", "corp-root.pem", "agent-ca-bundle.pem"):
        path = certs / name
        if path.exists():
            shutil.copy2(path, backup / name)
    for name, content in (("corp-root.pem", corporate), ("agent-ca-bundle.pem", bundle)):
        path = certs / name
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_bytes(content)
        ssl.create_default_context(cafile=str(temporary))
        os.replace(temporary, path)

    report = {"ca_count": len(actual), "bundle": str(certs / "agent-ca-bundle.pem"),
              "bundle_sha256": hashlib.sha256(bundle).hexdigest(),
              "certifi_sha256": hashlib.sha256(Path(certifi.where()).read_bytes()).hexdigest(),
              "backup": str(backup), "corporate_certificates": certificates}
    (backup / "rebuild.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
