"""Coeur métier de Local Gmail Manager (aucune donnée envoyée hors de Google)."""
from __future__ import annotations

import base64
import csv
import hashlib
import json
import os
import random
import re
import sqlite3
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from email.utils import parseaddr
from pathlib import Path
from typing import Callable, Iterable

from platformdirs import user_data_path

SCOPE = "https://www.googleapis.com/auth/gmail.modify"
DATA_DIR = user_data_path("LocalGmailManager", appauthor=False)
TOKEN_PATH = DATA_DIR / "token.json"
APP_DIR = Path(__file__).resolve().parent
# Le fichier OAuth de l'utilisateur n'est jamais embarqué dans le programme.
# Conserver l'emplacement historique en développement, utiliser le dossier
# de données utilisateur dans une distribution PyInstaller.
import sys
CREDENTIALS_PATH = (DATA_DIR if getattr(sys, "frozen", False) else APP_DIR) / "credentials.json"
_thread = threading.local()


def secure_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".token-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temp, 0o600)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def credentials(allow_login: bool = False):
    """OAuth Desktop: le token est sauvegardé sur ce PC uniquement."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    creds = None
    if TOKEN_PATH.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), [SCOPE])
            # Interdit de réutiliser un ancien token sans gmail.modify.
            if SCOPE not in (creds.scopes or []):
                creds = None
        except (OSError, ValueError):
            creds = None
    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            secure_write(TOKEN_PATH, creds.to_json())
            return creds
        except Exception:
            if not allow_login:
                raise RuntimeError("Session expirée : reconnecte ton compte Gmail.")
    if not allow_login:
        return None
    if not CREDENTIALS_PATH.is_file():
        raise FileNotFoundError(f"Fichier OAuth introuvable : {CREDENTIALS_PATH}")
    flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_PATH), [SCOPE])
    # Le serveur de callback écoute uniquement sur le loopback du PC.
    creds = flow.run_local_server(host="127.0.0.1", port=0, open_browser=True,
                                  authorization_prompt_message="Connexion Google dans le navigateur : {url}")
    secure_write(TOKEN_PATH, creds.to_json())
    return creds


def gmail_service(creds=None):
    from googleapiclient.discovery import build
    if creds is None:
        creds = credentials()
    if not creds:
        raise RuntimeError("Compte Gmail non connecté")
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def quota_budget() -> int:
    """Plafond local volontairement inférieur aux 6 000 unités/min/utilisateur."""
    try:
        requested = int(os.environ.get("GMAIL_QUOTA_UNITS_PER_MIN", "3600"))
    except ValueError:
        requested = 3600
    return max(600, min(requested, 5400))


class QuotaPacer:
    """Répartit les appels dans le temps entre TOUS les threads de ce processus.

    Ce limiteur ne peut pas comptabiliser un autre programme accédant au même
    compte Gmail ; la marge de sécurité est intentionnelle.
    """

    def __init__(self, units_per_minute: int, clock=None, sleeper=None):
        if units_per_minute <= 0:
            raise ValueError("Budget de quota invalide")
        self.budget = units_per_minute
        self._clock = clock or time.monotonic
        self._sleep = sleeper or time.sleep
        self._lock = threading.Lock()
        self._next_slot = 0.0

    def acquire(self, cost: int) -> None:
        if cost <= 0:
            raise ValueError("Coût de quota invalide")
        with self._lock:
            now = self._clock()
            slot = max(self._next_slot, now)
            self._next_slot = slot + 60.0 * cost / self.budget
        waiting = slot - self._clock()
        if waiting > 0:
            self._sleep(waiting)


QUOTA_UNITS_PER_MIN = quota_budget()
_pacer = QuotaPacer(QUOTA_UNITS_PER_MIN)


def _retryable_http_error(exc) -> bool:
    status = getattr(exc.resp, "status", 0)
    if status in (429, 500, 502, 503, 504):
        return True
    if status != 403:
        return False
    detail = str(exc).lower()
    content = getattr(exc, "content", b"")
    if isinstance(content, bytes):
        detail += " " + content.decode("utf-8", errors="replace").lower()
    return any(part in detail for part in (
        "ratelimitexceeded", "userratelimitexceeded", "rate limit exceeded",
        "quota exceeded", "quotaexceeded",
    )) and "dailylimitexceeded" not in detail


def google_call(request, quota_cost: int = 20, max_attempts: int = 7):
    """Limitation proactive + reprises uniquement pour les erreurs temporaires."""
    from googleapiclient.errors import HttpError
    for attempt in range(max_attempts):
        _pacer.acquire(quota_cost)  # Chaque tentative consomme potentiellement du quota.
        try:
            return request.execute()
        except HttpError as exc:
            if not _retryable_http_error(exc) or attempt + 1 >= max_attempts:
                raise
            retry_after = 0.0
            try:
                retry_after = max(0.0, float(exc.resp.get("retry-after", 0)))
            except (ValueError, TypeError, AttributeError):
                pass
        except (ConnectionError, TimeoutError, OSError):
            if attempt + 1 >= max_attempts:
                raise
            retry_after = 0.0
        time.sleep(max(retry_after, min(2 ** attempt, 60) + random.uniform(0, 0.5)))


def user_email(service) -> str:
    return google_call(service.users().getProfile(userId="me"), quota_cost=1)["emailAddress"].lower()


def db_path(email: str) -> Path:
    hash_name = hashlib.sha256(email.lower().encode("utf-8")).hexdigest()[:20]
    return DATA_DIR / f"index-{hash_name}.sqlite3"


def connect_db(email: str) -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path(email))
    con.row_factory = sqlite3.Row
    con.execute("""CREATE TABLE IF NOT EXISTS messages (
        id TEXT PRIMARY KEY,
        sender_name TEXT NOT NULL,
        sender_email TEXT NOT NULL,
        sender_domain TEXT NOT NULL,
        subject TEXT NOT NULL,
        sent_at_ms INTEGER NOT NULL,
        size_bytes INTEGER NOT NULL,
        attachment_count INTEGER NOT NULL,
        attachment_bytes INTEGER NOT NULL,
        labels_json TEXT NOT NULL,
        newsletter INTEGER NOT NULL,
        scan_id TEXT NOT NULL
    )""")
    con.execute("CREATE INDEX IF NOT EXISTS idx_sender ON messages(sender_email)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_size ON messages(size_bytes)")
    con.execute("""CREATE TABLE IF NOT EXISTS scan_info (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        searched TEXT, finished_at TEXT, total INTEGER, truncated INTEGER, failed INTEGER NOT NULL DEFAULT 0
    )""")
    con.commit()
    return con


def walk_parts(payload: dict) -> Iterable[dict]:
    """Parcours aussi les structures MIME imbriquées."""
    yield payload
    for part in payload.get("parts", []) or []:
        yield from walk_parts(part)


def is_attachment(part: dict) -> bool:
    headers = {str(h.get("name", "")).lower(): str(h.get("value", ""))
               for h in part.get("headers", []) or []}
    disposition = headers.get("content-disposition", "").lower()
    return bool(part.get("filename") or disposition.startswith("attachment"))


def get_attachments(payload: dict) -> list[dict]:
    found = []
    for part in walk_parts(payload):
        if not is_attachment(part):
            continue
        body = part.get("body") or {}
        found.append({
            "filename": part.get("filename") or "piece_jointe.bin",
            "attachment_id": body.get("attachmentId"),
            "inline_data": body.get("data"),
            "size": int(body.get("size") or 0),
        })
    return found


def parse_message(message: dict, scan_id: str) -> tuple:
    payload = message.get("payload") or {}
    headers = {h.get("name", "").lower(): h.get("value", "")
               for h in payload.get("headers", []) or []}
    sender_name, sender_email = parseaddr(headers.get("from", ""))
    sender_email = sender_email.strip().lower() or "adresse-inconnue"
    sender_name = sender_name.strip() or sender_email
    domain = sender_email.rpartition("@")[2] or "inconnu"
    attachments = get_attachments(payload)
    labels = message.get("labelIds", [])
    is_newsletter = (bool(headers.get("list-unsubscribe"))
                     or "CATEGORY_PROMOTIONS" in labels)
    return (
        str(message["id"]), sender_name, sender_email, domain,
        headers.get("subject", "(sans objet)"),
        int(message.get("internalDate") or 0),
        int(message.get("sizeEstimate") or 0),
        len(attachments), sum(a["size"] for a in attachments),
        json.dumps(labels), int(is_newsletter), scan_id,
    )


INSERT_SQL = """INSERT INTO messages (
    id, sender_name, sender_email, sender_domain, subject, sent_at_ms,
    size_bytes, attachment_count, attachment_bytes, labels_json, newsletter, scan_id
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(id) DO UPDATE SET
    sender_name=excluded.sender_name, sender_email=excluded.sender_email,
    sender_domain=excluded.sender_domain, subject=excluded.subject,
    sent_at_ms=excluded.sent_at_ms, size_bytes=excluded.size_bytes,
    attachment_count=excluded.attachment_count, attachment_bytes=excluded.attachment_bytes,
    labels_json=excluded.labels_json, newsletter=excluded.newsletter, scan_id=excluded.scan_id
"""


def gmail_query(scope: str, search: str) -> str:
    # Gmail exclut déjà spam/corbeille par défaut ; ici on filtre aussi les envoyés.
    base = "in:inbox" if scope == "inbox" else "-in:sent -in:drafts -in:trash -in:spam"
    return f"{base} {search.strip()}".strip()


def list_message_ids(service, scope: str, search: str, limit: int,
                     progress: Callable[[int], None] | None = None) -> tuple[list[str], bool]:
    result = []
    page = None
    query = gmail_query(scope, search)
    truncated = False
    while True:
        remaining = limit - len(result)
        if remaining <= 0:
            truncated = True
            break
        response = google_call(service.users().messages().list(
            userId="me", q=query, maxResults=min(500, remaining), pageToken=page), quota_cost=5)
        result.extend(item["id"] for item in response.get("messages", []))
        if progress:
            progress(len(result))
        page = response.get("nextPageToken")
        if not page:
            break
        if len(result) >= limit:
            truncated = True
            break
    return result, truncated


def _thread_service(creds):
    if not hasattr(_thread, "service"):
        _thread.service = gmail_service(creds)
    return _thread.service


def _fetch_message(creds, msg_id: str):
    service = _thread_service(creds)
    return google_call(service.users().messages().get(userId="me", id=msg_id, format="full"))


_SCAN_LOCK = threading.Lock()


def scan_mailbox(creds, email: str, scope: str, search: str, limit: int,
                 on_progress: Callable[[str, int, int], None],
                 reuse_cache: bool = True) -> dict:
    """Empêche deux analyses simultanées sur le même index et quota local."""
    if not _SCAN_LOCK.acquire(blocking=False):
        raise RuntimeError("Une analyse est déjà en cours sur cette instance de l'application.")
    try:
        return _scan_mailbox_impl(creds, email, scope, search, limit,
                                  on_progress, reuse_cache=reuse_cache)
    finally:
        _SCAN_LOCK.release()


def _scan_mailbox_impl(creds, email: str, scope: str, search: str, limit: int,
                       on_progress: Callable[[str, int, int], None],
                       reuse_cache: bool = True) -> dict:
    """Indexation prudente et reprenable, avec cache pour une même recherche.

    L'index est marqué invalide AVANT le premier appel Google. Si le processus
    est interrompu, aucune mise en corbeille n'est autorisée via cet index.
    """
    service = gmail_service(creds)
    query = gmail_query(scope, search)
    run_id = uuid.uuid4().hex
    db = connect_db(email)
    try:
        previous = db.execute("SELECT searched FROM scan_info WHERE id=1").fetchone()
        may_reuse = bool(reuse_cache and previous and previous["searched"] == query)
        db.execute("""INSERT INTO scan_info (id, searched, finished_at, total, truncated, failed)
                   VALUES (1, ?, ?, 0, 0, 1)
                   ON CONFLICT(id) DO UPDATE SET
                   searched=excluded.searched, finished_at=excluded.finished_at,
                   total=0, truncated=0, failed=1""",
                   (query, datetime.now(timezone.utc).isoformat()))
        db.commit()
        on_progress("Recherche des messages", 0, limit)
        ids, truncated = list_message_ids(service, scope, search, limit,
                                          lambda n: on_progress("Liste des messages", n, limit))
        cached_ids = ({row[0] for row in db.execute("SELECT id FROM messages")}
                      if may_reuse else set())
        reused = [msg_id for msg_id in ids if msg_id in cached_ids]
        pending = [msg_id for msg_id in ids if msg_id not in cached_ids]
        if reused:
            db.executemany("UPDATE messages SET scan_id=? WHERE id=?",
                           ((run_id, msg_id) for msg_id in reused))
            db.commit()
        completed = len(reused)
        fetched = 0
        errors = []
        on_progress("Fiches locales réutilisées", completed, len(ids))
        # Un maximum de 100 tâches en attente évite de surcharger la mémoire.
        with ThreadPoolExecutor(max_workers=2) as pool:
            for offset in range(0, len(pending), 100):
                batch = pending[offset:offset + 100]
                futures = {pool.submit(_fetch_message, creds, msg_id): msg_id for msg_id in batch}
                for future in as_completed(futures):
                    msg_id = futures[future]
                    try:
                        message = future.result()
                        db.execute(INSERT_SQL, parse_message(message, run_id))
                        fetched += 1
                    except Exception as exc:
                        errors.append(f"{msg_id}: {exc}")
                    completed += 1
                    if completed % 25 == 0:
                        db.commit()
                        on_progress("Analyse des messages", completed, len(ids))
                db.commit()
        on_progress("Analyse terminée", completed, len(ids))
        if not errors:
            db.execute("DELETE FROM messages WHERE scan_id <> ?", (run_id,))
        # Si une fiche a échoué, le nettoyage reste volontairement désactivé.
        db.execute("""UPDATE scan_info SET searched=?, finished_at=?, total=?,
                   truncated=?, failed=? WHERE id=1""",
                   (query, datetime.now(timezone.utc).isoformat(), len(ids),
                    int(truncated), int(bool(errors))))
        db.commit()
        return {"processed": completed, "found": len(ids), "truncated": truncated,
                "cached": len(reused), "fetched": fetched, "errors": errors}
    finally:
        db.close()


def all_messages(email: str) -> tuple[list[dict], dict | None]:
    with connect_db(email) as db:
        result = [dict(row) for row in db.execute(
            "SELECT * FROM messages ORDER BY sent_at_ms DESC")]
        line = db.execute("SELECT * FROM scan_info WHERE id=1").fetchone()
    return result, dict(line) if line else None


def cleaning_denial_reason(confirmation: str, index_valid: bool) -> str | None:
    """Contrôle de sécurité centralisé pour toute mise en corbeille depuis l'UI.

    Le champ Streamlit n'actualise pas nécessairement l'état du bouton avant
    qu'on quitte le champ : la vérification est donc faite au SOUMETTRE,
    et jamais via disabled=(confirmation != ...).
    """
    if not index_valid:
        return ("Mise à la corbeille bloquée : le dernier scan Gmail est incomplet "
                "ou a échoué (par exemple après une erreur de quota). "
                "Relance l'analyse et attends qu'elle se termine sans erreur. "
                "Le téléchargement sans mise à la corbeille reste possible.")
    if confirmation.strip().casefold() != "corbeille":
        return "Confirmation incorrecte : saisis CORBEILLE, puis valide le formulaire."
    return None


def is_inbox_message(row: dict) -> bool:
    """N'archive pas un message déjà hors de la boîte de réception."""
    try:
        return "INBOX" in json.loads(row.get("labels_json") or "[]")
    except (ValueError, TypeError):
        return False


def user_labels(service) -> list[dict]:
    """Libellés personnels Gmail (pas les dossiers système INBOX, TRASH, etc.)."""
    result = google_call(service.users().labels().list(userId="me"), quota_cost=1)
    return sorted(({"id": item["id"], "name": item["name"]}
                   for item in result.get("labels", [])
                   if item.get("type") == "user" and item.get("id") and item.get("name")),
                  key=lambda item: item["name"].casefold())


def create_user_label(service, name: str) -> dict:
    """Crée un dossier (libellé Gmail) seulement sur action explicite."""
    clean = name.strip()
    if not clean or len(clean) > 225 or "\n" in clean or "\r" in clean:
        raise ValueError("Nom de dossier invalide (1 à 225 caractères, sur une seule ligne).")
    for item in user_labels(service):
        if item["name"].casefold() == clean.casefold():
            raise ValueError("Un dossier porte déjà ce nom. Sélectionne-le dans la liste.")
    result = google_call(service.users().labels().create(userId="me", body={
        "name": clean, "labelListVisibility": "labelShow",
        "messageListVisibility": "show"}), quota_cost=5)
    if not result.get("id"):
        raise ValueError("Gmail n'a pas confirmé la création du dossier.")
    return {"id": result["id"], "name": result.get("name", clean)}


def filter_messages_for_view(rows: list[dict], view: str) -> list[dict]:
    """INBOX est la vue par défaut; un libellé ne modifie pas les messages."""
    if view == "ALL":
        return list(rows)
    if view == "INBOX":
        return [row for row in rows if is_inbox_message(row)]
    if view.startswith("LABEL:") and view[6:]:
        label_id = view[6:]
        result = []
        for row in rows:
            try:
                if label_id in json.loads(row.get("labels_json") or "[]"):
                    result.append(row)
            except (ValueError, TypeError):
                pass
        return result
    raise ValueError("Vue des emails inconnue")


def _mark_moved(db: sqlite3.Connection, row: dict, label_ids: list[str] | None,
                target_id: str) -> None:
    """Conserve la fiche locale pour la navigation par dossiers après déplacement."""
    try:
        old = json.loads(row.get("labels_json") or "[]")
    except (ValueError, TypeError):
        old = []
    labels = (label_ids if label_ids is not None else
              [value for value in old if value != "INBOX"] + [target_id])
    labels = list(dict.fromkeys([value for value in labels if value != "INBOX"] + [target_id]))
    db.execute("UPDATE messages SET labels_json=? WHERE id=?",
               (json.dumps(labels), row["id"]))
    db.commit()


def move_messages(service, email: str, rows: list[dict], label_id: str,
                  on_progress: Callable[[int, int], None]) -> dict:
    """Ajoute un libellé utilisateur ET retire INBOX, sans toucher aux autres libellés.

    Sécurise l'id cible avec une nouvelle lecture des libellés côté serveur pour
    éviter qu'un libellé supprimé ou système soit utilisé par erreur.
    """
    labels = {item["id"] for item in user_labels(service)}
    if label_id not in labels:
        raise ValueError("Dossier Gmail inconnu ou supprimé : rafraîchis la liste.")
    moved, errors = 0, []
    with connect_db(email) as db:
        for i, row in enumerate(rows, 1):
            try:
                response = google_call(service.users().messages().modify(
                    userId="me", id=row["id"], body={
                        "addLabelIds": [label_id], "removeLabelIds": ["INBOX"]}),
                    quota_cost=5)
                _mark_moved(db, row, response.get("labelIds") if isinstance(response, dict) else None,
                            label_id)
                moved += 1
            except Exception as exc:
                errors.append(f"{row['id']}: {exc}")
            on_progress(i, len(rows))
    return {"moved": moved, "errors": errors}


def _mark_archived(db: sqlite3.Connection, row: dict, label_ids: list[str] | None = None) -> None:
    """Maintient l'index cohérent avec l'action Gmail confirmée.

    Une recherche `in:inbox` ne doit plus montrer les messages archivés.
    Pour les recherches reçus + archivés, les messages restent indexés.
    """
    labels = [label for label in (label_ids if label_ids is not None else
                                   json.loads(row.get("labels_json") or "[]")) if label != "INBOX"]
    scan = db.execute("SELECT searched FROM scan_info WHERE id=1").fetchone()
    search = scan["searched"] if scan else ""
    inbox_scope = re.search(r"(?:^|\s)in:inbox(?:\s|$)", search or "", re.IGNORECASE)
    if inbox_scope:
        db.execute("DELETE FROM messages WHERE id=?", (row["id"],))
    else:
        db.execute("UPDATE messages SET labels_json=? WHERE id=?",
                   (json.dumps(labels), row["id"]))
    db.commit()


def archive_messages(service, email: str, rows: list[dict],
                     on_progress: Callable[[int, int], None]) -> dict:
    """Retire uniquement le label INBOX, jamais le mail ni ses autres labels."""
    archived, skipped, errors = 0, 0, []
    with connect_db(email) as db:
        for i, row in enumerate(rows, 1):
            try:
                if not is_inbox_message(row):
                    skipped += 1
                else:
                    response = google_call(service.users().messages().modify(
                        userId="me", id=row["id"],
                        body={"removeLabelIds": ["INBOX"]}), quota_cost=5)
                    _mark_archived(db, row, response.get("labelIds") if isinstance(response, dict) else None)
                    archived += 1
            except Exception as exc:
                errors.append(f"{row['id']}: {exc}")
            on_progress(i, len(rows))
    return {"archived": archived, "skipped": skipped, "errors": errors}


def trash_messages(service, email: str, rows: list[dict],
                   on_progress: Callable[[int, int], None]) -> dict:
    succeeded = 0
    errors = []
    with connect_db(email) as db:
        for i, row in enumerate(rows, 1):
            try:
                google_call(service.users().messages().trash(userId="me", id=row["id"]))
                db.execute("DELETE FROM messages WHERE id=?", (row["id"],))
                db.commit()
                succeeded += 1
            except Exception as exc:
                errors.append(f"{row['id']}: {exc}")
            on_progress(i, len(rows))
    return {"trashed": succeeded, "errors": errors}


_INVALID = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f]')
_WINDOWS_RESERVED = re.compile(r"^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)", re.I)


def safe_name(value: str, fallback: str = "fichier") -> str:
    clean = _INVALID.sub("_", value).strip().strip(".")
    if not clean or clean in {".", ".."}:
        clean = fallback
    if _WINDOWS_RESERVED.match(clean):
        clean = "_" + clean
    if len(clean) > 150:
        stem, dot, suffix = clean.rpartition(".")
        clean = (stem[:125] + "." + suffix[:20]) if dot else clean[:150]
    return clean


def decode_data(data: str) -> bytes:
    if not data:
        return b""
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def atomic_attachment_write(destination: Path, data: bytes) -> str:
    """Ne remplace jamais un fichier déjà existant mais différent."""
    digest = hashlib.sha256(data).hexdigest()
    if destination.exists():
        if hashlib.sha256(destination.read_bytes()).hexdigest() == digest:
            return digest
        raise FileExistsError(f"Le fichier existe avec un autre contenu : {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".part-", dir=destination.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        # En usage mono-instance, évite de remplacer un fichier qui est apparu entre-temps.
        if destination.exists():
            if hashlib.sha256(destination.read_bytes()).hexdigest() == digest:
                return digest
            raise FileExistsError(f"Le fichier existe avec un autre contenu : {destination}")
        os.replace(temp, destination)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
    return digest


MANIFEST_HEADER = ["horodatage_utc", "id_email", "expediteur", "objet",
                   "nom_original", "chemin_local", "octets", "sha256"]


def append_manifest(folder: Path, data: list[list]) -> None:
    path = folder / "manifest.csv"
    exists = path.exists() and path.stat().st_size > 0
    with path.open("a", encoding="utf-8" if exists else "utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        if not exists:
            writer.writerow(MANIFEST_HEADER)
        writer.writerows(data)
        handle.flush()
        os.fsync(handle.fileno())


def download_attachments(service, email: str, rows: list[dict], destination: Path,
                         after_action: str | bool,
                         on_progress: Callable[[int, int], None]) -> dict:
    """Action Gmail uniquement après sauvegarde vérifiée de TOUTES les PJ.

    Les booléens restent tolérés pour compatibilité avec les appels des V1.x.
    """
    if isinstance(after_action, bool):
        after_action = "trash" if after_action else "none"
    if after_action not in ("none", "archive", "trash"):
        raise ValueError("Action après téléchargement inconnue")
    destination = destination.expanduser().resolve()
    # Vérifier l'écriture AVANT toute opération Gmail destructive.
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryFile(dir=destination):
        pass
    saved = 0
    trashed = 0
    archived = 0
    skipped = 0
    errors = []
    with connect_db(email) as db:
        for i, row in enumerate(rows, 1):
            try:
                msg = google_call(service.users().messages().get(
                    userId="me", id=row["id"], format="full"))
                attachments = get_attachments(msg.get("payload") or {})
                # Ne rien mettre en corbeille si la recherche complète ne confirme pas les PJ.
                if not attachments:
                    raise ValueError("Aucune pièce jointe retrouvée : email conservé")
                stamp = datetime.fromtimestamp(max(row["sent_at_ms"], 0) / 1000,
                                               tz=timezone.utc).strftime("%Y-%m-%d")
                folder = destination / safe_name(row["sender_email"]) / (stamp + "_" + safe_name(row["id"]))
                manifests = []
                for j, attachment in enumerate(attachments, 1):
                    identifier = attachment["attachment_id"]
                    if identifier:
                        content = google_call(service.users().messages().attachments().get(
                            userId="me", messageId=row["id"], id=identifier))
                        encoded = content.get("data")
                    else:
                        encoded = attachment["inline_data"]
                    if encoded is None:
                        raise ValueError(f"Contenu manquant : {attachment['filename']}")
                    payload = decode_data(encoded)
                    reported_size = attachment["size"]
                    if reported_size != len(payload):
                        raise ValueError(f"Taille incohérente pour {attachment['filename']}")
                    basename = f"{j:03d}_" + safe_name(attachment["filename"])
                    path = folder / basename
                    digest = atomic_attachment_write(path, payload)
                    manifests.append([
                        datetime.now(timezone.utc).isoformat(), row["id"],
                        row["sender_email"], row["subject"], attachment["filename"],
                        str(path), len(payload), digest,
                    ])
                append_manifest(destination, manifests)
                saved += len(manifests)
                if after_action == "trash":
                    google_call(service.users().messages().trash(userId="me", id=row["id"]))
                    db.execute("DELETE FROM messages WHERE id=?", (row["id"],))
                    db.commit()
                    trashed += 1
                elif after_action == "archive":
                    if is_inbox_message(row):
                        response = google_call(service.users().messages().modify(
                            userId="me", id=row["id"], body={"removeLabelIds": ["INBOX"]}),
                            quota_cost=5)
                        _mark_archived(db, row, response.get("labelIds") if isinstance(response, dict) else None)
                        archived += 1
                    else:
                        skipped += 1
            except Exception as exc:
                errors.append(f"{row['id']}: {exc}")
            on_progress(i, len(rows))
    return {"attachments": saved, "trashed": trashed, "archived": archived,
            "skipped": skipped, "errors": errors, "destination": str(destination)}


def human_size(amount: int) -> str:
    value = float(amount)
    for unit in ("o", "Ko", "Mo", "Go", "To"):
        if abs(value) < 1024 or unit == "To":
            return f"{value:,.1f} {unit}".replace(",", " ")
        value /= 1024
    return "0 o"


def sender_summary(rows: list[dict]) -> list[dict]:
    result = {}
    for item in rows:
        addr = item["sender_email"]
        if addr not in result:
            result[addr] = {"Email": addr, "Nom": item["sender_name"],
                            "Messages": 0, "Octets": 0, "PJ": 0,
                            "Newsletters": 0, "Dernier": 0}
        target = result[addr]
        target["Messages"] += 1
        target["Octets"] += item["size_bytes"]
        target["PJ"] += item["attachment_count"]
        target["Newsletters"] += int(item["newsletter"])
        target["Dernier"] = max(target["Dernier"], item["sent_at_ms"])
    return sorted(result.values(), key=lambda x: (x["Messages"], x["Octets"]), reverse=True)


def domain_summary(rows: list[dict]) -> list[dict]:
    """Résumé par domaine exact après @ ; aucune requête Gmail supplémentaire.

    Ne fusionne pas foo.example.com avec example.com afin de ne pas
    sélectionner involontairement des messages hors du domaine choisi.
    """
    result: dict[str, dict] = {}
    for item in rows:
        domain = str(item["sender_domain"]).strip().lower()
        if not domain or domain == "inconnu":
            continue
        target = result.setdefault(domain, {
            "Domaine": domain, "Messages": 0, "Octets": 0,
            "PJ": 0, "Newsletters": 0, "Dernier": 0, "Expediteurs": set(),
        })
        target["Messages"] += 1
        target["Octets"] += item["size_bytes"]
        target["PJ"] += item["attachment_count"]
        target["Newsletters"] += int(item["newsletter"])
        target["Dernier"] = max(target["Dernier"], item["sent_at_ms"])
        target["Expediteurs"].add(item["sender_email"])
    summaries = []
    for target in result.values():
        summaries.append({**target, "Expediteurs": len(target["Expediteurs"])})
    return sorted(summaries, key=lambda x: (x["Messages"], x["Octets"]), reverse=True)


def domain_subject_summary(rows: list[dict]) -> list[dict]:
    """Groupes (domaine exact, objet exact), sans appels supplémentaires à Gmail.

    La casse et les préfixes Re:/Fwd: restent distincts, pour éviter de
    sélectionner des messages dont l'objet n'est pas strictement identique.
    """
    result: dict[tuple[str, str], dict] = {}
    for row in rows:
        domain = str(row.get("sender_domain") or "").strip().lower()
        if domain in ("", "inconnu"):
            continue
        subject = str(row.get("subject") or "(sans objet)")
        key = (domain, subject)
        target = result.setdefault(key, {
            "Domaine": domain, "Objet": subject, "Messages": 0, "Octets": 0,
            "PJ": 0, "Newsletters": 0, "Dernier": 0, "Expediteurs": set(),
        })
        target["Messages"] += 1
        target["Octets"] += row["size_bytes"]
        target["PJ"] += row["attachment_count"]
        target["Newsletters"] += int(row["newsletter"])
        target["Dernier"] = max(target["Dernier"], row["sent_at_ms"])
        target["Expediteurs"].add(row["sender_email"])
    return sorted(({**g, "Expediteurs": len(g["Expediteurs"])} for g in result.values()),
                  key=lambda g: (g["Messages"], g["Octets"]), reverse=True)


def select_group_messages(rows: list[dict], selected: set, mode: str) -> list[dict]:
    """Sélection exacte depuis SQLite ; jamais d'élargissement sur Gmail."""
    if mode not in ("domaine", "expediteur", "domaine_objet"):
        raise ValueError("Mode de sélection inconnu")
    if mode == "domaine_objet":
        return [row for row in rows if (row["sender_domain"], row["subject"]) in selected]
    field = "sender_domain" if mode == "domaine" else "sender_email"
    return [row for row in rows if row[field] in selected]
