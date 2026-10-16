"""Interface locale en français : python -m streamlit run app.py"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from pathlib import Path

import pandas as pd
import streamlit as st

from core import (
    CREDENTIALS_PATH, DATA_DIR, QUOTA_UNITS_PER_MIN, TOKEN_PATH, all_messages,
    archive_messages, credentials, domain_subject_summary, domain_summary,
    download_attachments, gmail_service, human_size, is_inbox_message,
    scan_mailbox, select_group_messages, sender_summary, trash_messages, user_email,
    user_labels, create_user_label, move_messages, filter_messages_for_view,
)

st.set_page_config(page_title="Local Gmail Manager", page_icon="📬", layout="wide")
st.markdown("""<style>
.block-container {max-width: 1320px; padding-top: 1.8rem;}
[data-testid="stMetric"] {background: rgba(130,130,130,.065); border-radius: 12px; padding: 15px;}
</style>""", unsafe_allow_html=True)


def report_results() -> None:
    if "report" not in st.session_state:
        return
    report = st.session_state.pop("report")
    if report["errors"]:
        st.warning(report["message"])
        with st.expander(f"Voir les {len(report['errors'])} erreurs"):
            for error in report["errors"][:30]:
                st.code(error)
            if len(report["errors"]) > 30:
                st.caption("Seules les 30 premières erreurs sont affichées.")
    else:
        st.success(report["message"])


def result_message(message: str, errors: list[str]):
    st.session_state["report"] = {"message": message, "errors": errors}
    st.rerun()


def progress_callback(placeholder):
    def update(done: int, total: int):
        placeholder.progress(min(1., done / max(total, 1)), text=f"Traités : {done:,} / {total:,}")
    return update


def fmt_date(ms: int) -> str:
    if not ms:
        return "—"
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%d/%m/%Y")


ACTION_LABELS = {
    "archive": ("Archiver", "archivés"),
    "trash": ("Mettre à la corbeille", "mis à la corbeille"),
    "move": ("Déplacer vers le dossier", "déplacés"),
}


@st.dialog("Confirmer l'opération")
def confirmation_dialog(service, email: str, chosen: list[dict], action: str,
                        *, download_folder: str | None = None, target_label: dict | None = None):
    """Une vraie fenêtre modale, sans mot de confirmation à recopier."""
    title, past = ACTION_LABELS[action]
    if action == "move":
        if not target_label or not target_label.get("id"):
            st.error("Sélectionne un dossier de destination valide.")
            return
        title = f"Déplacer vers « {target_label['name']} »"
    if download_folder:
        st.write(f"**{len(chosen):,} emails** : télécharger les pièces jointes, "
                 f"puis **{title.lower()}** chaque email après une sauvegarde réussie.")
        st.caption(f"Dossier : {download_folder}")
    else:
        st.write(f"**{title} {len(chosen):,} emails sélectionnés ?**")
    if action == "trash":
        st.warning("Les emails seront déplacés dans la corbeille Gmail (suppression "
                   "automatique habituelle après 30 jours). Aucune suppression définitive immédiate.")
    elif action == "move":
        st.info("Le libellé de destination sera ajouté et « Boîte de réception » retiré. "
                "Les autres libellés du message seront conservés.")
    else:
        st.info("Archiver retire les messages de la boîte de réception. "
                "Ils restent consultables dans « Tous les messages » et la recherche Gmail.")
        already = sum(not is_inbox_message(row) for row in chosen)
        if already:
            st.caption(f"{already:,} messages déjà archivés seront ignorés.")
    st.dataframe(pd.DataFrame([{
        "Expéditeur": r["sender_email"], "Objet": r["subject"],
        "Date": fmt_date(r["sent_at_ms"])
    } for r in chosen[:12]]), hide_index=True, use_container_width=True, height=250)
    if len(chosen) > 12:
        st.caption(f"Extrait : 12 sur {len(chosen):,}. L'action vise tous les messages sélectionnés.")
    cancel, accept = st.columns(2)
    with cancel:
        if st.button("Annuler", use_container_width=True):
            st.rerun()
    with accept:
        if st.button(f"Confirmer : {title.lower()}", type="primary", use_container_width=True):
            fresh, scan = all_messages(email)
            fresh_ids = {r["id"]: r for r in fresh}
            ids = {r["id"] for r in chosen}
            if not scan or scan.get("failed") or ids - fresh_ids.keys():
                st.error("L'index a changé ou le dernier scan est incomplet. "
                         "Actualise la page et vérifie à nouveau ta sélection.")
                return
            # Protège contre une sélection vieillie par un autre scan ou une modification locale.
            if any(fresh_ids[r["id"]]["subject"] != r["subject"] or
                   fresh_ids[r["id"]]["sender_email"] != r["sender_email"]
                   for r in chosen):
                st.error("Les caractéristiques d'un email ont changé depuis la sélection. "
                         "Vérifie à nouveau les messages avant de confirmer.")
                return
            verified = [fresh_ids[r["id"]] for r in chosen]
            holder = st.empty()
            with st.spinner("Opération Gmail en cours…"):
                try:
                    if download_folder:
                        result = download_attachments(service, email, verified,
                                                      Path(download_folder), action,
                                                      progress_callback(holder))
                        message = (f"{result['attachments']} pièces jointes sauvegardées. "
                                   f"{result['archived']} emails archivés, "
                                   f"{result['trashed']} mis à la corbeille.")
                    elif action == "archive":
                        result = archive_messages(service, email, verified, progress_callback(holder))
                        message = f"{result['archived']} emails archivés."
                    elif action == "move":
                        result = move_messages(service, email, verified, target_label["id"],
                                               progress_callback(holder))
                        message = (f"{result['moved']} emails déplacés vers "
                                   f"« {target_label['name']} ».")
                    else:
                        result = trash_messages(service, email, verified, progress_callback(holder))
                        message = f"{result['trashed']} emails mis à la corbeille."
                    if result.get("skipped"):
                        message += f" {result['skipped']} déjà archivés ignorés."
                    st.session_state["cleanup_saved_selection"] = None
                    st.session_state["selected_cleanup_ids"] = []
                    st.session_state["cleanup_epoch"] = st.session_state.get("cleanup_epoch", 0) + 1
                    st.session_state["big_epoch"] = st.session_state.get("big_epoch", 0) + 1
                    st.session_state["report"] = {"message": message, "errors": result["errors"]}
                    st.rerun()
                except Exception as exc:
                    st.error(f"Opération interrompue : {exc}. Consulte Gmail avant de réessayer.")


st.title("📬 Local Gmail Manager")
st.caption("Analyser, sauvegarder, nettoyer Gmail — interface et index uniquement sur cet ordinateur.")

with st.sidebar:
    st.subheader("Connexion Gmail")
    st.caption("Aucun mot de passe Gmail n'est demandé par cette application.")
    if not CREDENTIALS_PATH.is_file():
        st.error("Fichier `credentials.json` manquant.")
        st.markdown("Consulte le **README.md** : création d'un client OAuth Google de type 'Application de bureau'.")
        st.code(str(CREDENTIALS_PATH))
        st.stop()

    try:
        creds = credentials(allow_login=False)
    except Exception as exc:
        st.warning(str(exc))
        creds = None

    if not creds:
        if st.button("🔐 Se connecter à Google", use_container_width=True, type="primary"):
            try:
                with st.spinner("Autorisation Google dans ton navigateur…"):
                    credentials(allow_login=True)
                st.rerun()
            except Exception as exc:
                st.error(f"Connexion impossible : {exc}")
        st.caption("La connexion ouvre la page officielle Google, puis revient à l'app locale.")
        st.stop()

    try:
        service = gmail_service(creds)
        email = user_email(service)
    except Exception as exc:
        st.error(f"Gmail inaccessible : {exc}")
        st.stop()

    st.success(f"Connecté : {email}")
    with st.expander("Stockage local et déconnexion"):
        st.caption("Le jeton OAuth est enregistré localement ; protège ton compte utilisateur système.")
        st.code(str(DATA_DIR))
        if st.button("Oublier le jeton sur ce PC"):
            TOKEN_PATH.unlink(missing_ok=True)
            st.rerun()
        st.caption("La déconnexion locale ne révoque pas l'accès déjà accordé chez Google.")

    st.divider()
    st.subheader("Analyser ma boîte")
    scope_value = st.radio("Messages à indexer", ["Boîte de réception", "Reçus + archivés"],
                           help="Les emails envoyés, brouillons, spams et la corbeille sont exclus du second mode.")
    query = st.text_input("Recherche Gmail supplémentaire (optionnel)",
                          placeholder="older_than:1y ou category:promotions")
    limit = st.number_input("Limite de messages par analyse", min_value=100,
                            max_value=100000, value=3000, step=500)
    reuse_cache = st.checkbox(
        "Réutiliser les emails déjà analysés (recommandé)", value=True,
        help="Pour la même recherche, seuls les emails absents de l'index local sont téléchargés. "
             "Décoche pour actualiser les détails de tous les messages.")
    st.caption(f"Mode prudent : {QUOTA_UNITS_PER_MIN:,} unités/minute utilisées sur les 6 000 "
               "autorisées par Google. À 3 600 unités/minute, 3 000 nouveaux emails "
               "prennent environ 17 minutes (hors latences et reprises).")
    if st.button("🔄 Lancer l'analyse", type="primary", use_container_width=True):
        holder = st.empty()
        def scanning(stage, done, total):
            holder.progress(min(1.0, done / max(total, 1)),
                            text=f"{stage} — {done:,}/{total:,}")
        try:
            stats = scan_mailbox(
                creds, email, "inbox" if scope_value == "Boîte de réception" else "received",
                query, int(limit), scanning, reuse_cache=reuse_cache)
            message = (f"Analyse : {stats['processed']:,} messages traités "
                       f"({stats['cached']:,} réutilisés depuis SQLite, "
                       f"{stats['fetched']:,} récupérés sur Gmail).")
            if stats["truncated"]:
                message += " Limite atteinte : résultats PARTIELS."
            if stats["errors"]:
                message += " Certaines fiches n'ont pas pu être analysées : index potentiellement incomplet."
            result_message(message, stats["errors"])
        except Exception as exc:
            st.error(f"Échec de l'analyse : {exc}")


def get_folder_labels(force_refresh: bool = False) -> list[dict]:
    """Liste de dossiers mise en cache uniquement dans la session Streamlit."""
    cache = st.session_state.get("folder_labels_cache")
    if force_refresh or not cache or cache["email"] != email:
        labels = user_labels(service)
        st.session_state["folder_labels_cache"] = {"email": email, "labels": labels}
        return labels
    return cache["labels"]


report_results()
rows, scan_info = all_messages(email)
if not rows:
    st.info("Aucun email dans l'index local. Tu peux toujours consulter et créer des dossiers. "
            "Pour les remplir depuis l'application, lance une analyse depuis la barre latérale.")

index_valid = bool(scan_info and not scan_info.get("failed"))
if scan_info:
    if not index_valid:
        st.error("Dernier scan incomplet : actions Gmail désactivées tant qu'une nouvelle analyse n'a pas réussi.")
    st.caption(f"Dernier index : **{scan_info['searched']}** · {scan_info['total']:,} messages · "
               f"{scan_info['finished_at'][:19].replace('T',' ')} UTC"
               + (" · ⚠️ analyse limitée" if scan_info["truncated"] else ""))
elif rows:
    st.warning("Analyse non terminée : relance une analyse avant de déplacer ou nettoyer des emails.")

try:
    folders = get_folder_labels()
    folder_error = None
except Exception as exc:
    folders = []
    folder_error = str(exc)
    st.warning(f"Lecture des dossiers Gmail impossible : {folder_error}")
folder_map = {"LABEL:" + label["id"]: label["name"] for label in folders}
view_names = {"INBOX": "📥 Boîte de réception", "ALL": "📨 Tous les messages indexés", **{
    key: "📁 " + name for key, name in folder_map.items()
}}
view_key = st.selectbox("Afficher les emails de", list(view_names),
                        format_func=lambda key: view_names[key], key="message_view",
                        help="Par défaut, seuls les messages dans INBOX sont affichés. "
                             "Les dossiers ne contiennent ici que les messages de l'index local.")
visible_rows = filter_messages_for_view(rows, view_key)
if view_key.startswith("LABEL:"):
    st.caption("Dossier Gmail : seuls les messages déjà indexés localement sont affichés. "
               "Pour voir tous tes messages rangés, analyse « Reçus + archivés ».")
elif view_key == "ALL":
    st.caption("Cette vue comprend également les messages déjà archivés ou rangés, "
               "s'ils ont été indexés.")

senders = sender_summary(visible_rows)
col1, col2, col3, col4 = st.columns(4)
col1.metric("Emails affichés", f"{len(visible_rows):,}")
col2.metric("Domaines", f"{len(domain_summary(visible_rows)):,}")
col3.metric("Volume estimé", human_size(sum(row["size_bytes"] for row in visible_rows)))
col4.metric("Pièces jointes", f"{sum(row['attachment_count'] for row in visible_rows):,}")

PAGES = ["📊 Analyse", "🧹 Nettoyage", "📦 Emails volumineux", "📎 Pièces jointes", "📁 Dossiers"]
page = st.segmented_control("Navigation", PAGES, default=PAGES[0],
                            key="active_page", label_visibility="collapsed")
if page == PAGES[1] and st.session_state.get("previous_page") != PAGES[1]:
    st.session_state["cleanup_epoch"] = st.session_state.get("cleanup_epoch", 0) + 1
st.session_state["previous_page"] = page
if page == PAGES[0]:
    st.subheader("Qui remplit ta boîte ?")
    st.caption("La taille est une estimation fournie par Gmail, pas un calcul exact d'espace libérable.")
    namesearch = st.text_input("Filtrer les expéditeurs", placeholder="amazon, newsletter, domaine…")
    only_news = st.checkbox("Afficher uniquement les newsletters / promotions détectées")
    min_msgs = st.number_input("Nombre minimal d'emails", min_value=1, value=1)
    filtered = [s for s in senders if s["Messages"] >= min_msgs
                and (namesearch.lower() in s["Email"].lower() or namesearch.lower() in s["Nom"].lower())
                and (not only_news or s["Newsletters"] > 0)]
    display = pd.DataFrame([{
        "Expéditeur": s["Email"], "Nom": s["Nom"],
        "Messages": s["Messages"], "Volume": human_size(s["Octets"]),
        "Pièces jointes": s["PJ"], "Newsletter / promo": s["Newsletters"],
        "Dernier mail": fmt_date(s["Dernier"])
    } for s in filtered])
    st.dataframe(display, hide_index=True, use_container_width=True, height=390)
    if filtered:
        top = filtered[:20]
        st.bar_chart(pd.DataFrame({"Email": [s["Email"] for s in top],
                                   "Messages": [s["Messages"] for s in top]}).set_index("Email"))

    with st.expander("Regroupement par domaine"):
        st.dataframe(pd.DataFrame([{
            "Domaine": d["Domaine"], "Emails": d["Messages"],
            "Expéditeurs": d["Expediteurs"], "Volume": human_size(d["Octets"])
        } for d in domain_summary(visible_rows)]), hide_index=True, use_container_width=True)
        st.caption("Pour cocher plusieurs domaines et les nettoyer, ouvre l’onglet Nettoyage.")

    st.markdown("#### Pistes de nettoyage")
    cutoff_ms = int((datetime.now(timezone.utc).timestamp() - 365 * 86400) * 1000)
    n_old = sum(1 for row in visible_rows if row["sent_at_ms"] < cutoff_ms)
    n_news = sum(row["newsletter"] for row in visible_rows)
    n_attach = sum(row["attachment_count"] > 0 for row in visible_rows)
    st.write(f"**{n_news:,}** newsletters/promotions détectées · **{n_old:,}** mails de plus d'un an · "
             f"**{n_attach:,}** emails avec pièces jointes.")
    st.caption("Ces catégories peuvent se recouper. Aucun email n'est supprimé automatiquement.")

if page == PAGES[1]:
    # Les interactions des filtres / cases n'entraînent plus de rerun global.
    @st.fragment
    def show_cleanup_page():
        st.subheader("Nettoyage groupé : domaines, expéditeurs et objets")
        st.write("Coche uniquement les séries de mails à traiter. Le mode **Domaine + objet** "
                 "sépare les emails selon leur objet exact au sein d'un domaine.")
        group_mode = st.radio(
            "Regrouper par", ["Domaine", "Expéditeur", "Domaine + objet"],
            horizontal=True, key="cleanup_group_mode",
            help="Les sous-domaines restent distincts. Les objets sont exacts : Re: et Fwd: "
                 "ne sont pas fusionnés avec l'objet initial.")
        if group_mode == "Domaine":
            groups, core_mode, group_field = domain_summary(visible_rows), "domaine", "Domaine"
        elif group_mode == "Expéditeur":
            groups, core_mode, group_field = senders, "expediteur", "Email"
        else:
            groups, core_mode, group_field = domain_subject_summary(visible_rows), "domaine_objet", None

        a, b, c = st.columns([2, 1, 1])
        with a:
            group_search = st.text_input("Rechercher un domaine / expéditeur",
                                         placeholder="amazon.fr, newsletter…", key="cleanup_group_search")
        with b:
            minimum = st.number_input("Emails minimum", min_value=1, value=1,
                                      key="cleanup_group_min")
        with c:
            order = st.selectbox("Trier par", ["Emails", "Volume", "Pièces jointes"],
                                 key="cleanup_group_sort")
        subject_search = ""
        if core_mode == "domaine_objet":
            subject_search = st.text_input("Filtrer les objets (contient le texte)",
                                           placeholder="commande, facture, promotion…",
                                           key="cleanup_subject_search")
            st.caption("Chaque ligne cible **un seul objet exact** pour ce domaine. "
                       "Le filtre de recherche trouve les objets contenant ton texte, "
                       "mais ne coche pas automatiquement toutes les correspondances.")

        filtered_groups = [g for g in groups if g["Messages"] >= minimum
                           and group_search.casefold() in
                           (g["Domaine"] if core_mode == "domaine_objet"
                            else g[group_field] + " " + g.get("Nom", "")).casefold()
                           and (core_mode != "domaine_objet" or
                                subject_search.casefold() in g["Objet"].casefold())]
        sort_field = {"Emails": "Messages", "Volume": "Octets", "Pièces jointes": "PJ"}[order]
        filtered_groups.sort(key=lambda g: (g[sort_field], g["Messages"]), reverse=True)
        total_groups = len(filtered_groups)
        # Évite de transmettre des dizaines de milliers de lignes éditables au navigateur.
        filtered_groups = filtered_groups[:1000]
        st.caption(f"{total_groups:,} groupes correspondants, "
                   f"{len(filtered_groups):,} affichés/sélectionnables. "
                   "Affîne tes filtres pour voir les suivants. "
                   "Modifier un filtre ou le regroupement remet les cases à zéro.")

        visible_index_digest = hashlib.sha256("|".join(r["id"] for r in visible_rows).encode()).hexdigest()[:16]
        editor_signature = repr((email, scan_info["finished_at"] if scan_info else "",
                                 view_key, visible_index_digest, group_mode, group_search.casefold(),
                                 subject_search.casefold(), minimum, order))
        # Sauvegarde indépendante du cycle de vie du widget : survivra aux changements d'onglet.
        if st.session_state.get("cleanup_saved_selection") is None or \
                st.session_state["cleanup_saved_selection"].get("signature") != editor_signature:
            st.session_state["cleanup_saved_selection"] = {"signature": editor_signature, "keys": set()}
        preserved = st.session_state["cleanup_saved_selection"]
        visible_group_keys = [((g["Domaine"], g["Objet"]) if core_mode == "domaine_objet"
                               else g[group_field]) for g in filtered_groups]
        # Une case globale sélectionne UNIQUEMENT les lignes du tableau affiché,
        # jamais les résultats tronqués (au-delà de 1 000) ni ceux d'un autre filtre.
        all_checked = bool(visible_group_keys) and set(visible_group_keys) == set(preserved["keys"])
        select_all_key = "cleanup_select_all_" + hashlib.sha256(
            repr((editor_signature, st.session_state.get("cleanup_epoch", 0), all_checked)).encode()
        ).hexdigest()[:16]
        global_check = st.checkbox(
            f"Tout sélectionner ({len(visible_group_keys):,} lignes affichées)",
            value=all_checked, key=select_all_key, disabled=not visible_group_keys)
        if global_check != all_checked:
            preserved["keys"] = set(visible_group_keys) if global_check else set()
            st.session_state["cleanup_epoch"] = st.session_state.get("cleanup_epoch", 0) + 1
            # Ce clic global peut reconstruire la liste ; on ne relance jamais toute l'app.
            st.rerun(scope="fragment")
        st.caption(f"{len(preserved['keys']):,} / {len(visible_group_keys):,} lignes cochées. "
                   "Changer les filtres vide la sélection, pour éviter un déplacement involontaire.")
        # Le DataEditor applique ses changements par rapport à son tableau initial.
        # Garder ce point de départ fixe pendant tous les reruns de la section évite
        # les cases impossibles à décocher / les doubles modifications.
        mount_signature = (editor_signature, st.session_state.get("cleanup_epoch", 0))
        if st.session_state.get("cleanup_editor_mount") != mount_signature:
            st.session_state["cleanup_editor_mount"] = mount_signature
            st.session_state["cleanup_editor_baseline"] = set(preserved["keys"])
        baseline = st.session_state["cleanup_editor_baseline"]
        selected_groups = set()
        selection = []
        if filtered_groups:
            display_rows = []
            for g in filtered_groups:
                group_key = (g["Domaine"], g["Objet"]) if core_mode == "domaine_objet" else g[group_field]
                line = {"Choisir": group_key in baseline,
                        "Emails": g["Messages"], "Volume": human_size(g["Octets"]),
                        "Pièces jointes": g["PJ"], "Newsletters": g["Newsletters"],
                        "Dernier mail": fmt_date(g["Dernier"])}
                if core_mode == "domaine_objet":
                    line = {"Choisir": group_key in baseline,
                            "Domaine": g["Domaine"], "Objet": g["Objet"], **{
                                k: v for k, v in line.items() if k != "Choisir"}}
                    line["Expéditeurs"] = g["Expediteurs"]
                else:
                    line[group_field] = g[group_field]
                    if core_mode == "domaine":
                        line["Expéditeurs"] = g["Expediteurs"]
                    else:
                        line["Nom"] = g["Nom"]
                display_rows.append(line)
            editor_key = "cleanup_editor_" + hashlib.sha256(
                repr(mount_signature).encode()).hexdigest()[:16]
            table = pd.DataFrame(display_rows)
            edited = st.data_editor(
                table, key=editor_key, num_rows="fixed", hide_index=True,
                use_container_width=True, height=min(515, 70 + 35 * len(display_rows)),
                disabled=[col for col in table.columns if col != "Choisir"],
                column_config={"Choisir": st.column_config.CheckboxColumn(
                    "✓", help="Sélectionner uniquement les messages de cette ligne",
                    default=False, width="small")})
            selected_rows = edited.loc[edited["Choisir"] == True]
            if core_mode == "domaine_objet":
                selected_groups = set(zip(selected_rows["Domaine"], selected_rows["Objet"]))
            else:
                selected_groups = set(selected_rows[group_field].tolist())
            if selected_groups != preserved["keys"]:
                was_all_checked = all_checked
                preserved["keys"] = set(selected_groups)
                # Pas de remount, pas de st.rerun() global : garder la position du tableau.
                # Synchroniser la case « Tout sélectionner » uniquement lors d'un passage
                # tout coché <-> partiellement coché.
                is_all_checked = bool(visible_group_keys) and set(visible_group_keys) == selected_groups
                if is_all_checked != was_all_checked:
                    st.rerun(scope="fragment")
            selection = select_group_messages(visible_rows, selected_groups, core_mode)
        else:
            st.info("Aucun groupe ne correspond à ces filtres.")
            preserved["keys"] = set()
        st.session_state["selected_cleanup_ids"] = [r["id"] for r in selection]

        if selection:
            st.info(f"**{len(selected_groups):,} groupes cochés** · **{len(selection):,} emails** · "
                    f"**{human_size(sum(r['size_bytes'] for r in selection))}** estimés · "
                    f"{sum(r['attachment_count'] for r in selection):,} pièces jointes")
            st.markdown("#### Vérifier les emails sélectionnés")
            preview = pd.DataFrame([{
                "Date": fmt_date(r["sent_at_ms"]), "Domaine": r["sender_domain"],
                "Expéditeur": r["sender_email"], "Objet": r["subject"],
                "Taille": human_size(r["size_bytes"])
            } for r in selection[:250]])
            st.dataframe(preview, hide_index=True, height=270, use_container_width=True)
            if len(selection) > 250:
                st.caption(f"Aperçu limité à 250 sur {len(selection):,} emails. "
                           "Tous les emails sélectionnés seront traités après confirmation.")
            action = st.radio("Action", ["Déplacer vers…", "Archiver", "Mettre à la corbeille"],
                              horizontal=True, key="cleanup_action")
            target_label = None
            if action == "Déplacer vers…":
                if folders:
                    target_id = st.selectbox("Dossier de destination", [f["id"] for f in folders],
                                             format_func=lambda ident: next(
                                                 (f["name"] for f in folders if f["id"] == ident), ident),
                                             key="cleanup_target_folder")
                    target_label = next(f for f in folders if f["id"] == target_id)
                else:
                    st.warning("Aucun dossier disponible. Crée-en un dans l'onglet Dossiers.")
            if action == "Archiver":
                st.caption(f"{sum(is_inbox_message(r) for r in selection):,} emails dans la boîte de réception "
                           "seront archivés ; ceux déjà archivés seront ignorés.")
            if not index_valid:
                st.error("Action bloquée : dernier scan Gmail incomplet. Relance l'analyse.")
            if st.button(f"Continuer : {action.lower()} {len(selection):,} emails",
                         type="primary", disabled=not index_valid or
                         (action == "Déplacer vers…" and target_label is None), key="cleanup_review"):
                # Ouvrir la modale depuis l'app principale, pas à l'intérieur
                # du fragment : évite les problèmes de dialogues imbriqués.
                st.session_state["pending_cleanup_confirmation"] = {
                    "ids": [r["id"] for r in selection],
                    "action": "move" if action == "Déplacer vers…" else
                              "archive" if action == "Archiver" else "trash",
                    "target_label": target_label,
                }
                st.rerun()  # Un rerun global n'est nécessaire QUE pour la modale.
        else:
            st.caption("Coche une ou plusieurs lignes pour obtenir un aperçu et choisir une action.")

    show_cleanup_page()
    pending = st.session_state.pop("pending_cleanup_confirmation", None)
    if pending:
        # Réutilise la même vue pour éviter toute sélection hors filtre.
        pending_ids = set(pending["ids"])
        pending_rows = [r for r in visible_rows if r["id"] in pending_ids]
        if len(pending_rows) != len(pending_ids):
            st.error("La sélection a changé. Vérifie le filtre avant de réessayer.")
        else:
            confirmation_dialog(service, email, pending_rows,
                                pending["action"], target_label=pending["target_label"])

if page == PAGES[2]:
    st.subheader("Les emails les plus volumineux")
    threshold = st.slider("Taille minimale estimée (Mo)", 0, 25, 1)
    large_rows = sorted((r for r in visible_rows if r["size_bytes"] >= threshold * 1024 * 1024),
                        key=lambda r: r["size_bytes"], reverse=True)
    st.caption(f"{len(large_rows):,} emails correspondent au filtre (500 plus volumineux sélectionnables).")
    big_options = large_rows[:500]
    large_by_id = {r["id"]: r for r in big_options}
    st.dataframe(pd.DataFrame([{
        "Date": fmt_date(r["sent_at_ms"]), "Expéditeur": r["sender_email"],
        "Objet": r["subject"], "Taille": human_size(r["size_bytes"]),
        "PJ": r["attachment_count"]
    } for r in big_options]), hide_index=True, use_container_width=True, height=370)
    selected_big = st.multiselect("Choisir individuellement les emails",
                                 list(large_by_id),
                                 format_func=lambda identifier: (
                                     f"{human_size(large_by_id[identifier]['size_bytes'])} · "
                                     f"{large_by_id[identifier]['sender_email']} · "
                                     f"{large_by_id[identifier]['subject'][:70]}"),
                                 key=f"selected_big_{st.session_state.get('big_epoch', 0)}")
    valid_big = [large_by_id[msg_id] for msg_id in selected_big if msg_id in large_by_id]
    if valid_big:
        action_big = st.radio("Action", ["Déplacer vers…", "Archiver", "Mettre à la corbeille"],
                              horizontal=True, key="big_action")
        target_big = None
        if action_big == "Déplacer vers…":
            if folders:
                target_id = st.selectbox("Dossier de destination", [f["id"] for f in folders],
                                         format_func=lambda ident: next(
                                             (f["name"] for f in folders if f["id"] == ident), ident),
                                         key="big_target_folder")
                target_big = next(f for f in folders if f["id"] == target_id)
            else:
                st.warning("Crée un dossier dans l'onglet Dossiers.")
        if not index_valid:
            st.error("Action bloquée : dernier scan Gmail incomplet. Relance l'analyse.")
        if st.button(f"Continuer : {action_big.lower()} {len(valid_big)} emails",
                     type="primary", disabled=not index_valid or
                     (action_big == "Déplacer vers…" and target_big is None), key="big_review"):
            confirmation_dialog(service, email, valid_big,
                                "move" if action_big == "Déplacer vers…" else
                                "archive" if action_big == "Archiver" else "trash",
                                target_label=target_big)

if page == PAGES[3]:
    st.subheader("Sauvegarder les pièces jointes")
    st.write("Chaque email reçoit son propre dossier ; les fichiers sont enregistrés sans écrasement silencieux.")
    choice = st.radio("Quels emails ?", ["Tous ceux avec des pièces jointes",
                                         "Seulement les emails des groupes cochés"],
                      key="attachment_scope")
    # Sélection mémorisée même après changement de section.
    selected_ids = set(st.session_state.get("selected_cleanup_ids", []))
    att_rows = [r for r in visible_rows if r["attachment_count"] > 0 and (
        choice == "Tous ceux avec des pièces jointes" or r["id"] in selected_ids)]
    st.info(f"**{len(att_rows):,} emails** · "
            f"**{sum(r['attachment_count'] for r in att_rows):,} pièces jointes** · "
            f"{human_size(sum(r['attachment_bytes'] for r in att_rows))} estimés")
    directory = st.text_input("Dossier de destination sur TON ordinateur",
                              value=str(Path.home() / "Downloads" / "Gmail-attachments"))
    st.caption("Un fichier manifest.csv (noms, chemins, tailles, empreintes SHA-256) sera créé.")
    after_choice = st.radio("Après téléchargement complet et vérifié de chaque email",
                            ["Conserver les emails", "Archiver", "Mettre à la corbeille"],
                            horizontal=True, key="attachment_action")
    after_action = {"Conserver les emails": "none", "Archiver": "archive",
                    "Mettre à la corbeille": "trash"}[after_choice]
    if after_action != "none":
        st.warning("Chaque message ne sera déplacé qu'après la sauvegarde complète de "
                   "toutes ses pièces jointes. En cas d'erreur, il reste dans Gmail.")
        if not index_valid:
            st.error("Action Gmail bloquée : dernier scan incomplet. "
                     "Tu peux toujours télécharger sans archiver ni mettre à la corbeille.")
    can_start = bool(att_rows and directory.strip()) and (index_valid or after_action == "none")
    if st.button("📥 Télécharger" + (" puis " + after_choice.lower() if after_action != "none" else ""),
                 type="primary", disabled=not can_start, key="attachment_start"):
        if after_action == "none":
            holder = st.empty()
            try:
                result = download_attachments(service, email, att_rows, Path(directory),
                                              "none", progress_callback(holder))
                result_message(f"{result['attachments']} pièces jointes sauvegardées dans "
                               f"{result['destination']}. Aucun email déplacé.", result["errors"])
            except Exception as exc:
                st.error(f"Sauvegarde interrompue : {exc}")
        else:
            confirmation_dialog(service, email, att_rows, after_action,
                                download_folder=directory)
    st.caption("Les pièces jointes ne sont jamais exécutées. N'ouvre pas les fichiers non fiables.")

if page == PAGES[4]:
    st.subheader("📁 Dossiers Gmail")
    st.write("Ce sont les **libellés personnalisés** de ton compte Gmail. "
             "Déplacer un email lui ajoute le libellé choisi et le retire de la boîte de réception.")
    if st.button("🔄 Actualiser les dossiers", key="refresh_folders"):
        try:
            get_folder_labels(force_refresh=True)
            st.rerun()
        except Exception as exc:
            st.error(f"Actualisation impossible : {exc}")
    if folders:
        st.dataframe(pd.DataFrame([{
            "Dossier": label["name"],
            "Emails dans l'index": len(filter_messages_for_view(rows, "LABEL:" + label["id"])),
        } for label in folders]), hide_index=True, use_container_width=True, height=340)
        st.caption("Les compteurs affichent uniquement les emails déjà analysés dans SQLite. "
                   "Pour une vue complète, lance un scan « Reçus + archivés ».")
    else:
        st.info("Aucun dossier personnel trouvé (ou lecture des dossiers indisponible).")
    with st.form("create_folder_form", clear_on_submit=True):
        new_folder = st.text_input("Créer un nouveau dossier", placeholder="À conserver / Factures")
        create_clicked = st.form_submit_button("➕ Créer dans Gmail", type="primary")
    if create_clicked:
        try:
            item = create_user_label(service, new_folder)
            get_folder_labels(force_refresh=True)
            st.success(f"Dossier « {item['name']} » créé dans Gmail.")
            st.rerun()
        except Exception as exc:
            st.error(f"Création impossible : {exc}")
    st.caption("Les libellés système (Spam, Corbeille, etc.) ne sont pas proposés comme "
               "destinations. Tu peux aussi ouvrir un dossier depuis « Afficher les emails de ».")

st.divider()
st.caption("Application locale v1.6 • Seule l'API Gmail est contactée • Aucune suppression définitive • "
           "La synchronisation reflète le dernier scan, pas les nouveaux emails en temps réel.")
