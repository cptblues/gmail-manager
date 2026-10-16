import base64
import csv
import hashlib
import json
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import core


# Ces tests couvrent le code métier sans appel réseau ni accès au compte Gmail.
class FakeHttpError(Exception):
    pass

fake_errors = types.ModuleType("googleapiclient.errors")
fake_errors.HttpError = FakeHttpError
fake_api = types.ModuleType("googleapiclient")
fake_api.errors = fake_errors
sys.modules.setdefault("googleapiclient", fake_api)
sys.modules.setdefault("googleapiclient.errors", fake_errors)


class Req:
    def __init__(self, value=None, exc=None):
        self.value = value
        self.exc = exc

    def execute(self):
        if self.exc:
            raise self.exc
        return self.value


class FakeAttachments:
    def __init__(self, values):
        self.values = values

    def get(self, **kwargs):
        return Req(self.values[kwargs["id"]])


class FakeMessages:
    def __init__(self, values, fail_trash=False, fail_modify=False, attachments=None):
        self.values = values
        self.trashed = []
        self.modified = []
        self.fail_trash = fail_trash
        self.fail_modify = fail_modify
        self._attachments = FakeAttachments(attachments or {})

    def get(self, **kwargs):
        return Req(self.values[kwargs["id"]])

    def trash(self, **kwargs):
        if self.fail_trash:
            return Req(exc=OSError("Réponse Gmail indisponible"))
        self.trashed.append(kwargs["id"])
        return Req({"id": kwargs["id"], "labelIds": ["TRASH"]})

    def modify(self, **kwargs):
        if self.fail_modify:
            return Req(exc=OSError("Modification Gmail impossible"))
        msg_id = kwargs["id"]
        body = kwargs["body"]
        self.modified.append((msg_id, body))
        old = self.values[msg_id]
        labels = [name for name in old["labelIds"] if name not in body["removeLabelIds"]]
        return Req({"id": msg_id, "labelIds": labels})

    def attachments(self):
        return self._attachments


class FakeUsers:
    def __init__(self, messages):
        self._messages = messages

    def messages(self):
        return self._messages


class FakeGmail:
    def __init__(self, values, **kwargs):
        self.mock = FakeMessages(values, **kwargs)

    def users(self):
        return FakeUsers(self.mock)


def gmail_message(msg_id, parts=None):
    return {
        "id": msg_id,
        "internalDate": "1697500000000",
        "sizeEstimate": 50000,
        "labelIds": ["INBOX", "CATEGORY_PROMOTIONS"],
        "payload": {
            "headers": [
                {"name": "From", "value": '"Info Client" <newsletter@example.com>'},
                {"name": "Subject", "value": "Relevé mensuel"},
                {"name": "List-Unsubscribe", "value": "<mailto:unsubscribe@example.com>"},
            ],
            "parts": parts or [],
        },
    }


def encoded(content: bytes) -> str:
    return base64.urlsafe_b64encode(content).decode("ascii").rstrip("=")


def setup_db(monkeypatch, tmp_path, message):
    monkeypatch.setattr(core, "DATA_DIR", tmp_path / "data")
    email = "tester@example.com"
    row = core.parse_message(message, "test_scan")
    with core.connect_db(email) as con:
        con.execute(core.INSERT_SQL, row)
    rows, _ = core.all_messages(email)
    return email, rows


def test_nested_attachments_and_headers():
    nested = {"mimeType": "multipart/mixed", "parts": [
        {"filename": "contrat.pdf", "body": {"size": 123, "attachmentId": "a1"}},
        {"mimeType": "text/html", "body": {"data": encoded(b"message")}},
        {"headers": [{"name": "Content-Disposition", "value": 'attachment; filename="facture"'}],
         "body": {"size": 3, "data": encoded(b"hey")}},
    ]}
    msg = gmail_message("msg1", [nested])
    att = core.get_attachments(msg["payload"])
    assert len(att) == 2
    assert att[0]["attachment_id"] == "a1"
    assert core.parse_message(msg, "scan")[7:9] == (2, 126)
    assert core.parse_message(msg, "scan")[11] == "scan"


@pytest.mark.parametrize("name,expected", [
    ("../../privé.pdf", "_.._privé.pdf"),
    ("C:\\photos\\test.png", "C__photos_test.png"),
    ("CON", "_CON"),
    ("", "fichier"),
    ("x\x00.pdf", "x_.pdf"),
    ("...", "fichier"),
])
def test_safe_name(name, expected):
    assert core.safe_name(name) == expected


def test_download_then_trash_success(monkeypatch, tmp_path):
    file_bytes = b"Bonjour!\n"
    inline_bytes = b"123"
    message = gmail_message("m1", [
        {"filename": "facture.pdf", "body": {"size": len(file_bytes), "attachmentId": "p1"}},
        {"filename": "../danger.txt", "body": {"size": len(inline_bytes), "data": encoded(inline_bytes)}},
    ])
    email, rows = setup_db(monkeypatch, tmp_path, message)
    service = FakeGmail({"m1": message}, attachments={"p1": {"data": encoded(file_bytes)}})
    result = core.download_attachments(service, email, rows, tmp_path / "out", True, lambda *_: None)
    assert result["attachments"] == 2
    assert result["trashed"] == 1
    assert result["errors"] == []
    assert service.mock.trashed == ["m1"]
    assert core.all_messages(email)[0] == []
    files = list((tmp_path / "out").rglob("*.pdf"))
    assert len(files) == 1
    assert files[0].read_bytes() == file_bytes
    with (tmp_path / "out" / "manifest.csv").open(encoding="utf-8-sig", newline="") as fp:
        saved = list(csv.DictReader(fp))
    assert len(saved) == 2
    assert saved[0]["sha256"] == hashlib.sha256(file_bytes).hexdigest()
    assert (tmp_path / "out" / "manifest.csv").is_file()


def test_incomplete_download_never_trashes(monkeypatch, tmp_path):
    message = gmail_message("m2", [
        {"filename": "rapport.pdf", "body": {"size": 5, "attachmentId": "missing"}},
    ])
    email, rows = setup_db(monkeypatch, tmp_path, message)
    service = FakeGmail({"m2": message})
    result = core.download_attachments(service, email, rows, tmp_path / "out", True, lambda *_: None)
    assert result["attachments"] == 0
    assert result["trashed"] == 0
    assert len(result["errors"]) == 1
    assert service.mock.trashed == []
    assert len(core.all_messages(email)[0]) == 1


def test_trash_api_failure_keeps_local_index(monkeypatch, tmp_path):
    message = gmail_message("m3")
    email, rows = setup_db(monkeypatch, tmp_path, message)
    service = FakeGmail({"m3": message}, fail_trash=True)
    # Ne pas attendre les backoffs lors de ce test.
    monkeypatch.setattr(core.time, "sleep", lambda _: None)
    result = core.trash_messages(service, email, rows, lambda *_: None)
    assert result["trashed"] == 0
    assert result["errors"]
    assert len(core.all_messages(email)[0]) == 1


def test_existing_different_file_does_not_overwrite(tmp_path):
    path = tmp_path / "facture.txt"
    path.write_bytes(b"important")
    with pytest.raises(FileExistsError):
        core.atomic_attachment_write(path, b"ecraser")
    assert path.read_bytes() == b"important"


def test_query_scope():
    assert core.gmail_query("inbox", "older_than:1y") == "in:inbox older_than:1y"
    assert "-in:sent" in core.gmail_query("received", "")


def test_quota_pacer_spreads_requests_over_time():
    clock = [100.0]
    waits = []

    def sleep(seconds):
        waits.append(seconds)
        clock[0] += seconds

    pacer = core.QuotaPacer(3600, clock=lambda: clock[0], sleeper=sleep)
    for _ in range(4):
        pacer.acquire(20)
    assert len(waits) == 3
    assert abs(sum(waits) - 1.0) < 1e-6


def test_retry_403_quota_with_exponential_backoff(monkeypatch):
    class Response:
        status = 403

        def get(self, key, fallback=None):
            return fallback

    class QuotaError(FakeHttpError):
        resp = Response()
        content = (b'{"error":{"errors":[{"reason":"rateLimitExceeded",'
                   b'"message":"Quota exceeded per user"}]}}')

    class RecoveringRequest:
        def __init__(self):
            self.calls = 0

        def execute(self):
            self.calls += 1
            if self.calls == 1:
                raise QuotaError('Rate limit exceeded')
            return {"ok": True}

    costs = []
    monkeypatch.setattr(core, "_pacer", types.SimpleNamespace(acquire=costs.append))
    waits = []
    monkeypatch.setattr(core.time, "sleep", waits.append)
    monkeypatch.setattr(core.random, "uniform", lambda a, b: 0)
    request = RecoveringRequest()
    assert core.google_call(request, quota_cost=5) == {"ok": True}
    assert request.calls == 2
    assert costs == [5, 5]
    assert waits == [1]


def test_scan_reuses_cached_ids_and_fetches_new_ones(monkeypatch, tmp_path):
    monkeypatch.setattr(core, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(core, "gmail_service", lambda creds: object())
    listed_ids = ["m1", "m2"]
    calls = []
    monkeypatch.setattr(core, "list_message_ids",
                        lambda service, scope, search, limit, progress=None: (listed_ids[:limit], False))

    def fetch(creds, msg_id):
        calls.append(msg_id)
        return gmail_message(msg_id)

    monkeypatch.setattr(core, "_fetch_message", fetch)
    def progress(stage, done, total):
        pass

    email = "tester@example.com"
    first = core.scan_mailbox(None, email, "inbox", "", 10, progress)
    assert first["cached"] == 0 and first["fetched"] == 2
    assert len(calls) == 2
    listed_ids.append("m3")
    second = core.scan_mailbox(None, email, "inbox", "", 10, progress)
    assert second["cached"] == 2 and second["fetched"] == 1
    assert len(calls) == 3
    assert {r["id"] for r in core.all_messages(email)[0]} == {"m1", "m2", "m3"}

    third = core.scan_mailbox(None, email, "inbox", "", 10, progress, reuse_cache=False)
    assert third["cached"] == 0 and third["fetched"] == 3
    assert len(calls) == 6


def test_failed_scan_disables_cleanup(monkeypatch, tmp_path):
    monkeypatch.setattr(core, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(core, "gmail_service", lambda creds: object())
    monkeypatch.setattr(core, "list_message_ids",
                        lambda *a: (["m1"], False))
    monkeypatch.setattr(core, "_fetch_message", lambda creds, msg_id: gmail_message(msg_id))
    email = "tester@example.com"
    core.scan_mailbox(None, email, "inbox", "", 10, lambda *a: None)

    def broken(*args, **kwargs):
        raise RuntimeError("API inaccessible")
    monkeypatch.setattr(core, "list_message_ids", broken)
    with pytest.raises(RuntimeError, match="API inaccessible"):
        core.scan_mailbox(None, email, "inbox", "", 10, lambda *a: None)
    rows, status = core.all_messages(email)
    assert len(rows) == 1
    assert status["failed"] == 1


@pytest.mark.parametrize("text", ["CORBEILLE", " corbeille ", "CorBeIlLe", "\tCORBEILLE\n"])
def test_cleaning_confirmation_accepts_typing_variants(text):
    assert core.cleaning_denial_reason(text, True) is None


@pytest.mark.parametrize("text", ["", "CORBEIL", "POUBELLE", "CORBEILLE !"])
def test_cleaning_confirmation_rejects_wrong_text(text):
    assert "Confirmation incorrecte" in core.cleaning_denial_reason(text, True)


def test_cleaning_denied_after_failed_scan_even_with_right_confirmation():
    assert "dernier scan" in core.cleaning_denial_reason("CORBEILLE", False)


def sample_grouped_rows():
    def record(msg_id, address, domain, *, size=100, pj=0, newsletter=0, timestamp=0):
        return {
            "id": msg_id,
            "sender_email": address,
            "sender_domain": domain,
            "sender_name": address,
            "size_bytes": size,
            "attachment_count": pj,
            "newsletter": newsletter,
            "sent_at_ms": timestamp,
        }
    return [
        record("a", "promo@exemple.com", "exemple.com", size=500, pj=2,
               newsletter=1, timestamp=40),
        record("b", "support@exemple.com", "exemple.com", size=300, pj=1,
               timestamp=20),
        record("c", "news@news.exemple.com", "news.exemple.com", size=600,
               newsletter=1, timestamp=30),
        record("d", "info@ailleurs.fr", "ailleurs.fr", size=900,
               timestamp=70),
        record("e", "adresse-inconnue", "inconnu", size=300, timestamp=10),
    ]


def test_domain_summary_counts_multiple_senders_and_attachments():
    summaries = core.domain_summary(sample_grouped_rows())
    assert [d["Domaine"] for d in summaries] == [
        "exemple.com", "ailleurs.fr", "news.exemple.com"]
    domain = summaries[0]
    assert domain["Messages"] == 2
    assert domain["Octets"] == 800
    assert domain["PJ"] == 3
    assert domain["Newsletters"] == 1
    assert domain["Expediteurs"] == 2
    assert domain["Dernier"] == 40
    assert all(d["Domaine"] != "inconnu" for d in summaries)


def test_domain_selection_exact_only_no_subdomains():
    matches = core.select_group_messages(
        sample_grouped_rows(), {"exemple.com"}, "domaine")
    assert {r["id"] for r in matches} == {"a", "b"}
    assert "c" not in [r["id"] for r in matches]


def test_multiple_domain_checkbox_selection_does_not_duplicate_messages():
    matches = core.select_group_messages(
        sample_grouped_rows(), {"exemple.com", "ailleurs.fr"}, "domaine")
    assert [r["id"] for r in matches] == ["a", "b", "d"]


def test_sender_selection_exact_addresses():
    matches = core.select_group_messages(
        sample_grouped_rows(), {"promo@exemple.com"}, "expediteur")
    assert [r["id"] for r in matches] == ["a"]


def test_selected_group_validation():
    with pytest.raises(ValueError, match="Mode de sélection inconnu"):
        core.select_group_messages(sample_grouped_rows(), {"exemple.com"}, "query")


def test_domain_subject_summary_separates_subjects_and_senders():
    data = sample_grouped_rows()
    data[0]["subject"] = "Facture"
    data[1]["subject"] = "Offre spéciale"
    data[2]["subject"] = "Facture"
    data[3]["subject"] = "Facture"
    items = core.domain_subject_summary(data)
    keys = {(x["Domaine"], x["Objet"]) for x in items}
    assert ("exemple.com", "Facture") in keys
    assert ("exemple.com", "Offre spéciale") in keys
    assert ("news.exemple.com", "Facture") in keys
    assert ("ailleurs.fr", "Facture") in keys
    assert ("inconnu", "(sans objet)") not in keys


def test_domain_subject_selection_exact_not_subject_fragment_or_subdomain():
    data = sample_grouped_rows()
    for row, sub in zip(data, ["Commande", "Commande validée", "Commande", "Commande", "Commande"]):
        row["subject"] = sub
    got = core.select_group_messages(data, {("exemple.com", "Commande")}, "domaine_objet")
    assert [x["id"] for x in got] == ["a"]
    # Un filtre d'interface « contient Commande » ne doit jamais élargir la sélection.
    assert core.select_group_messages(data, {("exemple.com", "Commande validée")}, "domaine_objet")[0]["id"] == "b"


def test_domain_subject_no_casefold_and_re_prefix_merging():
    rows = sample_grouped_rows()[:3]
    for row, obj in zip(rows, ["Avis", "avis", "Re: Avis"]):
        row["subject"] = obj
    assert len(core.domain_subject_summary(rows)) == 3


def test_archive_removes_only_inbox_label_keeps_db_when_received(monkeypatch, tmp_path):
    msg = gmail_message("m4")
    email, rows = setup_db(monkeypatch, tmp_path, msg)
    service = FakeGmail({"m4": msg})
    result = core.archive_messages(service, email, rows, lambda *_: None)
    assert result == {"archived": 1, "skipped": 0, "errors": []}
    assert service.mock.modified == [("m4", {"removeLabelIds": ["INBOX"]})]
    saved, _ = core.all_messages(email)
    assert len(saved) == 1
    labels = json.loads(saved[0]["labels_json"])
    assert "INBOX" not in labels
    assert "CATEGORY_PROMOTIONS" in labels
    result2 = core.archive_messages(service, email, saved, lambda *_: None)
    assert result2["skipped"] == 1
    assert len(service.mock.modified) == 1


def test_archive_removes_local_row_when_scope_inbox(monkeypatch, tmp_path):
    msg = gmail_message("m5")
    email, rows = setup_db(monkeypatch, tmp_path, msg)
    with core.connect_db(email) as db:
        db.execute("INSERT INTO scan_info (id, searched, finished_at, total, truncated, failed) "
                   "VALUES (1, 'in:inbox', '2026-10-14', 1, 0, 0)")
    service = FakeGmail({"m5": msg})
    result = core.archive_messages(service, email, rows, lambda *_: None)
    assert result["archived"] == 1
    assert core.all_messages(email)[0] == []


def test_archive_error_does_not_touch_local_index(monkeypatch, tmp_path):
    msg = gmail_message("m6")
    email, rows = setup_db(monkeypatch, tmp_path, msg)
    service = FakeGmail({"m6": msg}, fail_modify=True)
    monkeypatch.setattr(core.time, "sleep", lambda _: None)
    result = core.archive_messages(service, email, rows, lambda *_: None)
    assert result["archived"] == 0
    assert len(result["errors"]) == 1
    assert core.is_inbox_message(core.all_messages(email)[0][0])


def test_attachments_then_archive_only_after_safe_download(monkeypatch, tmp_path):
    msg = gmail_message("m7", [
        {"filename": "reçu.pdf", "body": {"size": 4, "attachmentId": "att"}},
    ])
    email, rows = setup_db(monkeypatch, tmp_path, msg)
    service = FakeGmail({"m7": msg}, attachments={"att": {"data": encoded(b"abcd")}})
    result = core.download_attachments(service, email, rows, tmp_path / "out",
                                       "archive", lambda *_: None)
    assert result["attachments"] == 1
    assert result["archived"] == 1
    assert result["trashed"] == 0
    assert service.mock.modified[0][0] == "m7"
    assert not core.is_inbox_message(core.all_messages(email)[0][0])


def test_attachments_fail_never_archive(monkeypatch, tmp_path):
    msg = gmail_message("m8", [
        {"filename": "reçu.pdf", "body": {"size": 4, "attachmentId": "att"}},
    ])
    email, rows = setup_db(monkeypatch, tmp_path, msg)
    service = FakeGmail({"m8": msg}, attachments={"att": {"data": encoded(b"ab")}})
    result = core.download_attachments(service, email, rows, tmp_path / "out",
                                       "archive", lambda *_: None)
    assert result["archived"] == 0
    assert service.mock.modified == []
    assert result["errors"]


def test_attachment_invalid_action_cannot_make_folder(monkeypatch, tmp_path):
    msg = gmail_message("m9")
    email, rows = setup_db(monkeypatch, tmp_path, msg)
    service = FakeGmail({"m9": msg})
    with pytest.raises(ValueError, match="inconnue"):
        core.download_attachments(service, email, rows, tmp_path / "out", "delete", lambda *_: None)
    assert not (tmp_path / "out").exists()

# V1.5 : libellés Gmail / dossiers et déplacement sans suppression.
class FakeLabels:
    def __init__(self, values, *, fail_create=False):
        self.values = list(values)
        self.created = []
        self.fail_create = fail_create

    def list(self, **kwargs):
        return Req({"labels": list(self.values)})

    def create(self, **kwargs):
        if self.fail_create:
            return Req(exc=OSError("Impossible de créer le dossier"))
        obj = {"id": "Label_New", "name": kwargs["body"]["name"], "type": "user"}
        self.created.append(kwargs["body"])
        self.values.append(obj)
        return Req(obj)


class LabelUsers:
    def __init__(self, labels, messages):
        self._labels = labels
        self._messages = messages

    def labels(self):
        return self._labels

    def messages(self):
        return self._messages


class LabelGmail:
    def __init__(self, messages=None, *, fail_modify=False, labels=None):
        self.mock = FakeMessages(messages or {}, fail_modify=fail_modify)
        self.mock_labels = FakeLabels(labels if labels is not None else [
            {"id": "INBOX", "name": "Inbox", "type": "system"},
            {"id": "Label_Keep", "name": "À conserver", "type": "user"},
            {"id": "Label_Bills", "name": "Factures", "type": "user"},
        ])

    def users(self):
        return LabelUsers(self.mock_labels, self.mock)


def test_user_labels_only_returns_personal_labels_sorted():
    service = LabelGmail()
    assert core.user_labels(service) == [
        {"id": "Label_Bills", "name": "Factures"},
        {"id": "Label_Keep", "name": "À conserver"},
    ]


def test_create_user_label_deduplicates_and_rejects_bad_names():
    service = LabelGmail()
    with pytest.raises(ValueError, match="déjà ce nom"):
        core.create_user_label(service, "  FACTURES ")
    with pytest.raises(ValueError, match="invalide"):
        core.create_user_label(service, " \n ")
    label = core.create_user_label(service, " Famille ")
    assert label == {"id": "Label_New", "name": "Famille"}
    assert service.mock_labels.created == [{"name": "Famille",
                                            "labelListVisibility": "labelShow",
                                            "messageListVisibility": "show"}]


def test_filter_messages_for_view_only_inbox_and_target_label():
    a = {"id": "a", "labels_json": json.dumps(["INBOX", "Label_Other"])}
    b = {"id": "b", "labels_json": json.dumps(["Label_Keep"])}
    c = {"id": "c", "labels_json": "not JSON"}
    rows = [a, b, c]
    assert [r["id"] for r in core.filter_messages_for_view(rows, "INBOX")] == ["a"]
    assert [r["id"] for r in core.filter_messages_for_view(rows, "LABEL:Label_Keep")] == ["b"]
    assert [r["id"] for r in core.filter_messages_for_view(rows, "ALL")] == ["a", "b", "c"]
    with pytest.raises(ValueError, match="inconnue"):
        core.filter_messages_for_view(rows, "LABEL:")


def test_move_removes_inbox_and_adds_personal_label_without_losing_others(monkeypatch, tmp_path):
    msg = gmail_message("m21")
    email, rows = setup_db(monkeypatch, tmp_path, msg)
    with core.connect_db(email) as db:
        db.execute("INSERT INTO scan_info (id, searched, finished_at, total, truncated, failed) "
                   "VALUES (1, 'in:inbox', '2026-10-14', 1, 0, 0)")
    service = LabelGmail({"m21": msg})
    progress = []
    result = core.move_messages(service, email, rows, "Label_Keep", lambda *args: progress.append(args))
    assert result == {"moved": 1, "errors": []}
    assert progress == [(1, 1)]
    assert service.mock.modified == [
        ("m21", {"addLabelIds": ["Label_Keep"], "removeLabelIds": ["INBOX"]})]
    # La fiche reste indexée pour être visible immédiatement dans le dossier.
    saved, _ = core.all_messages(email)
    assert len(saved) == 1
    assert [r["id"] for r in core.filter_messages_for_view(saved, "INBOX")] == []
    assert [r["id"] for r in core.filter_messages_for_view(saved, "LABEL:Label_Keep")] == ["m21"]
    assert "CATEGORY_PROMOTIONS" in json.loads(saved[0]["labels_json"])


def test_move_rejects_unknown_or_system_label_before_any_modification(monkeypatch, tmp_path):
    msg = gmail_message("m22")
    email, rows = setup_db(monkeypatch, tmp_path, msg)
    service = LabelGmail({"m22": msg})
    for target in ("INBOX", "Label_Deleted"):
        with pytest.raises(ValueError, match="inconnu"):
            core.move_messages(service, email, rows, target, lambda *_: None)
    assert service.mock.modified == []
    assert core.is_inbox_message(core.all_messages(email)[0][0])


def test_move_failure_keeps_local_index_untouched(monkeypatch, tmp_path):
    monkeypatch.setattr(core.time, "sleep", lambda _: None)
    msg = gmail_message("m23")
    email, rows = setup_db(monkeypatch, tmp_path, msg)
    service = LabelGmail({"m23": msg}, fail_modify=True)
    result = core.move_messages(service, email, rows, "Label_Keep", lambda *_: None)
    assert result["moved"] == 0
    assert len(result["errors"]) == 1
    saved, _ = core.all_messages(email)
    assert core.is_inbox_message(saved[0])
    assert "Label_Keep" not in json.loads(saved[0]["labels_json"])
