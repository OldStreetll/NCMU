"""FastGPT v4.14.x read-only client module (errata-13).

Narrow surface: list collections / get collection files / download file.
Backend NEVER writes to FastGPT — admin uploads happen via the FastGPT
console UI directly (baseline §3.1 + errata-13 §1.1).
"""
