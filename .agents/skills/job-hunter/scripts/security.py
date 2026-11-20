"""Security, sanitization, SSRF protection, and safe I/O utilities for job-hunter."""

from __future__ import annotations

import ipaddress
import os
import re
import socket
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

MAX_RESPONSE_BYTES = 1_500_000  # 1.5 MB cap for crawled HTML pages
MAX_PDF_BYTES = 10_000_000  # 10 MB cap for CV PDF attachments
MAX_DESCRIPTION_CHARS = 8_000
MAX_FIELD_CHARS = 240

_BLOCKED_HOSTNAMES = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "ip6-localhost",
        "ip6-loopback",
        "metadata.google.internal",
        "metadata.goog",
        "metadata",
    }
)

_BLOCKED_HOST_SUFFIXES = (
    ".localhost",
    ".local",
    ".internal",
    ".home.arpa",
    ".lan",
)

# Invisible / BiDi / dangerous control characters that can hide prompt injections or spoof text
_DANGEROUS_UNICODE_RE = re.compile(
    r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f"
    r"\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff]"
)

_EMAIL_SYNTAX_RE = re.compile(
    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+$"
)

_CSV_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")

_PROMPT_INJECTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "ignore_instructions",
        re.compile(
            r"\b(?:ignore|disregard|forget)\s+(?:all\s+)?(?:previous|above|prior|system)\s+"
            r"(?:instructions|prompts|rules)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "ignore_instructions_fr",
        re.compile(
            r"\b(?:ignore[zr]?|oublie[zr]?)\s+(?:toutes?\s+)?les\s+instructions\s+"
            r"(?:precedentes|précédentes|anterieures|antérieures|systeme|système)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "role_override",
        re.compile(
            r"\b(?:you\s+are\s+now\s+(?:a|an)\s+(?:unrestricted|different|new)\s+(?:ai|assistant|agent)"
            r"|override\s+(?:system|safety|security)\s+(?:rules|instructions|policy))\b",
            re.IGNORECASE,
        ),
    ),
    (
        "command_execution",
        re.compile(
            r"\b(?:execute|run)\s+(?:this\s+)?(?:shell|bash|terminal|python)\s+command\b",
            re.IGNORECASE,
        ),
    ),
    (
        "secret_exfiltration",
        re.compile(
            r"\b(?:exfiltrate|send|upload|curl|wget)\b.{0,50}"
            r"(?:id_rsa|/etc/passwd|\.env|profile\.yaml|api[_\s-]?key|access[_\s-]?token)",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
    (
        "chat_delimiter_spoof",
        re.compile(r"<\|(?:im_start|system|endoftext)\|>|\[/?INST\]|<<SYS>>", re.IGNORECASE),
    ),
)


def _is_global_ip(raw_ip: str) -> bool:
    """Return True only if raw_ip is a public, globally routable IP address."""
    try:
        addr = ipaddress.ip_address(raw_ip)
    except ValueError:
        return False

    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped

    return bool(
        addr.is_global
        and not addr.is_private
        and not addr.is_loopback
        and not addr.is_link_local
        and not addr.is_multicast
        and not addr.is_reserved
        and not addr.is_unspecified
    )


def sanitize_url(url: str) -> str:
    """Keep only syntactically valid http(s) URLs; reject javascript:, file:, data:, etc."""
    cleaned = _DANGEROUS_UNICODE_RE.sub("", (url or "").strip())
    if not cleaned:
        return ""
    try:
        parsed = urlparse(cleaned)
    except ValueError:
        return ""
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return ""
    if parsed.username is not None or parsed.password is not None:
        return ""
    return cleaned


def is_safe_public_url(url: str, *, resolve_dns: bool = True) -> bool:
    """Validate that a URL targets a public http(s) host and not localhost/internal IPs (SSRF guard)."""
    cleaned = sanitize_url(url)
    if not cleaned:
        return False

    parsed = urlparse(cleaned)
    host = (parsed.hostname or "").strip().lower().rstrip(".")
    if not host:
        return False

    if host in _BLOCKED_HOSTNAMES or any(host.endswith(suffix) for suffix in _BLOCKED_HOST_SUFFIXES):
        return False

    # Reject raw IP literals unless globally routable
    try:
        ipaddress.ip_address(host)
        return _is_global_ip(host)
    except ValueError:
        pass

    # Require at least one dot for public hostnames (blocks bare internal hostnames like "intranet")
    if "." not in host:
        return False

    if not resolve_dns:
        return True

    try:
        infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
    except OSError:
        return False

    if not infos:
        return False

    for info in infos:
        sockaddr = info[4]
        ip_str = str(sockaddr[0])
        if not _is_global_ip(ip_str):
            return False

    return True


def safe_http_get(
    client: httpx.Client,
    url: str,
    *,
    max_bytes: int = MAX_RESPONSE_BYTES,
    max_redirects: int = 3,
    resolve_dns: bool = True,
) -> tuple[int, str, str, str] | None:
    """Perform a size-capped GET while validating SSRF safety on every redirect hop."""
    current_url = url
    for _ in range(max_redirects + 1):
        if not is_safe_public_url(current_url, resolve_dns=resolve_dns):
            return None
        try:
            with client.stream("GET", current_url, follow_redirects=False) as response:
                status = response.status_code
                if status in (301, 302, 303, 307, 308):
                    location = response.headers.get("location", "").strip()
                    if not location:
                        return None
                    current_url = urljoin(current_url, location)
                    continue

                content_type = response.headers.get("content-type", "")
                chunks: list[bytes] = []
                total = 0
                for chunk in response.iter_bytes():
                    if not chunk:
                        continue
                    remaining = max_bytes - total
                    if remaining <= 0:
                        break
                    chunks.append(chunk[:remaining])
                    total += min(len(chunk), remaining)
                    if total >= max_bytes:
                        break

                encoding = response.encoding or "utf-8"
                text = b"".join(chunks).decode(encoding, errors="replace")
                return status, current_url, content_type, text
        except httpx.HTTPError:
            return None

    return None


def sanitize_untrusted_text(
    text: str,
    *,
    max_length: int = MAX_DESCRIPTION_CHARS,
    single_line: bool = False,
) -> str:
    """Remove dangerous control/BiDi characters and bound untrusted scraped text length."""
    cleaned = _DANGEROUS_UNICODE_RE.sub("", text or "")
    if single_line:
        cleaned = re.sub(r"[\r\n\t]+", " ", cleaned)
        cleaned = re.sub(r"\s{2,}", " ", cleaned)
    else:
        cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n")
    cleaned = cleaned.strip()
    if len(cleaned) > max_length:
        cleaned = cleaned[:max_length].rstrip()
    return cleaned


def detect_prompt_injection(text: str) -> list[str]:
    """Return rule tags if untrusted scraped text contains suspicious prompt-injection patterns."""
    if not text:
        return []
    hits: list[str] = []
    for label, pattern in _PROMPT_INJECTION_PATTERNS:
        if pattern.search(text):
            hits.append(f"prompt_injection:{label}")
    return hits


def sanitize_header_value(value: str) -> str:
    """Strip CR/LF and control characters to prevent RFC 5322 email header injection."""
    cleaned = _DANGEROUS_UNICODE_RE.sub("", value or "")
    cleaned = cleaned.replace("\r", " ").replace("\n", " ")
    return re.sub(r"\s{2,}", " ", cleaned).strip()


def is_valid_email_syntax(email: str) -> bool:
    """Strictly validate email address syntax without whitespace or control characters."""
    if not email or len(email) > 254:
        return False
    if any(ch in email for ch in ("\r", "\n", "\x00", " ", "\t")):
        return False
    if email.count("@") != 1:
        return False
    local, domain = email.split("@", 1)
    if not local or len(local) > 64 or not domain or len(domain) > 253:
        return False
    if ".." in local or ".." in domain:
        return False
    tld = domain.rsplit(".", 1)[-1]
    if len(tld) < 2 or not tld.isalpha():
        return False
    return bool(_EMAIL_SYNTAX_RE.match(email))


def validate_pdf_file(path: Path, *, max_bytes: int = MAX_PDF_BYTES) -> tuple[bool, str]:
    """Verify a CV file exists, is non-empty, respects size cap, and has a valid %PDF- header."""
    if not path.is_file():
        return False, f"CV file not found: {path}"
    try:
        size = path.stat().st_size
    except OSError as exc:
        return False, f"Unable to stat CV file: {exc}"
    if size == 0:
        return False, f"CV file is empty: {path}"
    if size > max_bytes:
        return False, f"CV file exceeds max allowed size ({max_bytes} bytes): {size} bytes"
    try:
        with path.open("rb") as handle:
            header = handle.read(5)
    except OSError as exc:
        return False, f"Unable to read CV file: {exc}"
    if header != b"%PDF-":
        return False, f"CV file does not have a valid PDF header (%PDF-): {path.name}"
    return True, ""


def sanitize_csv_cell(value: Any) -> str:
    """Neutralize CSV/spreadsheet formula injection (DDE) in exported fields."""
    text = _DANGEROUS_UNICODE_RE.sub("", str(value if value is not None else ""))
    if text and text[0] in _CSV_FORMULA_PREFIXES:
        return f"'{text}"
    return text


def ensure_within_directory(target: Path, root: Path) -> Path:
    """Resolve target and ensure it stays strictly inside root (path traversal guard)."""
    resolved_root = root.resolve()
    resolved_target = target.resolve()
    if not resolved_target.is_relative_to(resolved_root):
        raise ValueError(f"Path escapes allowed directory: {target} not within {root}")
    return resolved_target


def atomic_write_text(path: Path, content: str, encoding: str = "utf-8") -> None:
    """Write text atomically using a unique temporary file in the target directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding=encoding) as handle:
            handle.write(content)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write bytes atomically using a unique temporary file in the target directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
