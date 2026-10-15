"""Offline, rule-based triage of owner-exported comments, replies and mentions.

The owner exports comments from official platform APIs or native tools into a
normalized JSON file. This module labels each item with transparent keyword
rules (English, German, French, Spanish, Italian, Portuguese and Persian packs,
with a heuristic ``language_guess``), orders a human review queue and writes
reply/issue *drafts* to local
files. It has NO network access and NO send, reply, follow, like, DM or issue
filing path: every draft is ``draft_requires_approval`` and a human must review,
edit and post it manually from the official app, if at all.

All comment text is untrusted data. It is stripped of control/format characters,
length-capped, scanned for prompt-injection style directives and never quoted
into a draft reply.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path
from typing import cast

from .campaigns import SENSITIVE_KEY, public_url
from .capabilities import reply_send_support as _reply_send_support
from .optout import listed_entry, load_optout, validate_optout
from .policy import CONSERVATIVE_TEXT_LIMITS, PLATFORMS, ValidationError
from .privacy import redact as redact_secrets
from .storage import parse_utc

EXPORT_SCHEMA = "reachweaver.engagement-export.v1"
DOCS_MAP_SCHEMA = "reachweaver.docs-map.v1"
REPORT_SCHEMA = "reachweaver.engagement-triage.v1"
DRAFT_STATUS = "draft_requires_approval"

# Explicit, tested policy. These are not configurable switches.
NETWORK_ACCESS = False
SEND_PATH = None
AUTO_REPLY = False
MASS_FOLLOW = False
MASS_LIKE = False
MASS_DM = False
UNSOLICITED_OUTREACH = False
VOTE_MANIPULATION = False
FAKE_PERSONAS = False
PROHIBITED_ACTIONS = (
    "send_or_post_reply", "auto_reply", "mass_follow", "mass_like", "mass_dm",
    "unsolicited_outreach", "vote_manipulation", "fake_personas", "file_issue_via_api",
)

ENGAGEMENT_PLATFORMS = PLATFORMS | {"github", "hackernews"}
# Platforms whose rules prohibit AI-generated comments/replies: route to the human.
HUMAN_AUTHORED_ONLY = frozenset({"devto", "hackernews"})
DEFAULT_REPLY_LIMIT = 500

MAX_ITEMS = 5000
MAX_TEXT_CHARS = 4000
EXCERPT_CHARS = 280
MAX_HANDLE_CHARS = 64
MAX_ID_CHARS = 200
MAX_DOC_TOPICS = 200
MAX_REPLY_EVIDENCE = 50
SENTIMENTS = ("negative", "neutral", "positive")
MAX_REACH = 10_000_000_000
REPLY_EVIDENCE_KINDS = frozenset({"release", "repository", "documentation", "announcement", "paper", "benchmark", "other"})
CLAIMS_CHECK_LIMITATION = ("Lexical grounding only: figures, release language, quotes, superlatives and feature claims "
                           "in the draft are matched against the evidence entries you list in the docs map. It does "
                           "not open a source or verify truth; a clean result is not proof.")
_EVIDENCE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
MAX_TEMPLATE_CHARS = 1000
# Owner templates may contain newlines and ZWNJ (needed in Persian), never bidi controls.
TEMPLATE_ALLOWED_FORMAT = frozenset("\n\u200c")

REQUIRED_STRING_FIELDS = ("id", "platform", "account_id", "author_handle", "text", "url", "created_at")
OPTIONAL_URL_FIELDS = ("in_reply_to", "thread_root_url")
# Unified-inbox categories (issue #17). Optional per item; an export without `kind` is "unspecified".
INBOX_KINDS = ("reply", "mention", "quote_post", "comment", "direct_message")
KIND_UNSPECIFIED = "unspecified"

# Highest priority first; spam last. Harassment/suspicious are human-review routes.
LABELS = (
    "bug_report", "technical_question", "harassment", "actionable_feedback",
    "feature_request", "collaboration_opportunity", "suspicious", "praise",
    "off_topic", "spam",
)
PRIORITY = {label: rank for rank, label in enumerate(LABELS, start=1)}
# When several rules match, the first label in this order wins.
PRECEDENCE = (
    "suspicious", "harassment", "spam", "bug_report", "technical_question",
    "feature_request", "collaboration_opportunity", "actionable_feedback", "praise",
)
DRAFTABLE = frozenset({
    "bug_report", "technical_question", "feature_request", "actionable_feedback",
    "collaboration_opportunity", "praise",
})
HUMAN_ROUTE = frozenset({"harassment", "suspicious"})
ISSUE_LABELS = {"bug_report": "Bug", "feature_request": "Feature"}


def _rx(pattern: str) -> re.Pattern:
    return re.compile(pattern, re.I)


# (rule id, label, compiled regex, human-readable reason). Matched text is NOT
# echoed into outputs; the rule id and reason are the explanation.
RULES: tuple[tuple[str, str, re.Pattern | None, str], ...] = (
    ("injection_ignore_instructions", "suspicious",
     _rx(r"\b(ignore|disregard|forget|override)\b.{0,30}\b(previous|prior|above|earlier|all|your|system)\b.{0,20}\b(instructions?|prompts?|rules|directions)\b"),
     "Tries to override instructions"),
    ("injection_ai_identity", "suspicious",
     _rx(r"\b((you are|you're) (now )?(an? )?(ai|a\.i\.|assistant|language model|llm|chatbot|chatgpt|claude|gpt)|as an? (ai|assistant|language model|llm|chatbot))\b"),
     "Addresses an AI agent directly"),
    ("injection_system_prompt", "suspicious",
     _rx(r"(system prompt|new instructions\s*:|</?\s*(system|assistant|instructions?)\s*>|\bjailbreak\b|\bdo anything now\b|\bdeveloper mode\b|\bpretend (to be|you are)\b|\bact as (an?|the|my)\b)"),
     "Prompt or role manipulation markup"),
    ("injection_send_me", "suspicious", _rx(r"\bsend (me|us)\b"),
     "Requests that something be sent"),
    ("injection_click", "suspicious", _rx(r"\bclick (this|here|the link|on (this|the) link|below)\b"),
     "Click-through lure"),
    ("injection_credentials", "suspicious",
     _rx(r"\b(send|share|give|tell|dm|reply with|enter|confirm|verify|paste)\b.{0,30}\b(password|passcode|api[ _-]?key|access token|seed phrase|private key|2fa code|verification code|login code)\b"),
     "Asks for credentials or secret codes"),
    ("hidden_unicode_controls", "suspicious", None,
     "Contained hidden bidirectional/format control characters"),
    ("harassment_threat", "harassment",
     _rx(r"\b(kill yourself|kys|i will (find|hurt|kill|ruin) you|you should die|hope you die|watch your back|dox(x)?(ing|ed)? you)\b"),
     "Threat or self-harm incitement"),
    ("harassment_insult", "harassment",
     _rx(r"\b(you'?re|you are|ur) (an? )?(complete |total |fucking )?(idiot|moron|stupid|loser|pathetic|worthless|clown|imbecile)\b|\bshut up\b|\bnobody (wants|cares about) you\b"),
     "Personal insult"),
    ("spam_promotion", "spam",
     _rx(r"\b(buy (cheap )?(followers|likes|views|subscribers)|sub4sub|follow ?back|free (crypto|bitcoin|money|giveaway)|airdrop|promo code|check out my (channel|page|profile|store)|earn \$?\d+|work from home|dm me (for|to)|whatsapp\s*\+?\d|telegram me|casino|forex signals)\b"),
     "Promotional or engagement-bait spam phrase"),
    ("spam_link_flood", "spam", None, "Three or more links in one comment"),
    ("bug_symptom", "bug_report",
     _rx(r"\b(bug|crash(es|ed|ing)?|traceback|stack ?trace|exception|segfault|regression|broken|doesn'?t work|does not work|not working|fails?|failed|failing|error)\b"),
     "Describes a defect or failure"),
    ("question_how", "technical_question",
     _rx(r"\b(how (do|does|can|to|would|should)|is there a way|can (i|it|you|we)|does (it|this)|what('?s| is) the (best |right )?way|why does|which version|is it possible)\b"),
     "Asks how something works"),
    ("question_technical_term", "technical_question",
     _rx(r"\b(install\w*|configur\w*|setup|api|cli|version|python|docs?|documentation|dependenc\w*|docker|deploy\w*)\b[^?]*\?"),
     "Question mark with a technical term"),
    ("feature_ask", "feature_request",
     _rx(r"\b(feature request|would (be|love) (nice|great|awesome|helpful|it)|please add|could you add|can you add|add (an? )?(option|flag|setting|support)|support for|it would help if|i wish (it|there|this)|on the roadmap)\b"),
     "Requests a new capability"),
    ("collaboration", "collaboration_opportunity",
     _rx(r"\b(collaborat\w*|partner(ship)?|work together|co-?author|joint (project|paper|work)|sponsor\w*|integrat(e|ion) (it )?with (our|my)|invite you to (speak|present|talk))\b"),
     "Proposes a collaboration"),
    ("feedback_suggestion", "actionable_feedback",
     _rx(r"\b(suggest\w*|you should|consider|typo|confusing|unclear|hard to (find|follow|read)|would be clearer|improve\w*|feedback|misleading|outdated)\b"),
     "Concrete improvement feedback"),
    ("praise", "praise",
     _rx(r"\b(thanks?|thank you|great (work|job|project|tool|post|write-?up)|love (this|it)|awesome|amazing|nice (work|job)|well done|kudos|brilliant|very helpful|so useful)\b"),
     "Positive feedback"),
)
OPT_OUT = _rx(r"\b(stop (contacting|messaging|replying to|tagging|mentioning) me|unsubscribe|opt[- ]?out|(do not|don'?t) (contact|message|reply to|tag|mention|dm) me|leave me alone)\b")

# ---------------------------------------------------------------------------
# Multilingual rule packs (issue #17 follow-up).
#
# Each pack is a hand-written keyword/regex list authored for this project and
# exercised by synthetic fixtures (tests/fixtures/engagement/multilingual.json). None
# is a vetted moderation lexicon. Text AND pattern source go through the same fold
# (NFKC, casefold, accents/harakat removed, Persian letter variants unified), so
# patterns are written without accents: "precedent" matches "précédentes". In Persian
# patterns "~" marks a spot where writers use a ZWNJ, a space or nothing
# ("می‌کنم" / "می کنم" / "میکنم"). See docs/ENGAGEMENT.md#multilingual-rules.
# ---------------------------------------------------------------------------
LANGUAGES = ("en", "de", "fr", "es", "it", "pt", "fa")
LANGUAGE_NAMES = {"en": "English", "de": "German", "fr": "French", "es": "Spanish",
                  "it": "Italian", "pt": "Portuguese", "fa": "Persian"}
# Safety labels run every pack on every comment: a missed injection, threat or
# opt-out costs more than a false alarm that a human then reviews.
SAFETY_LABELS = frozenset({"suspicious", "harassment", "spam"})
LANGUAGE_CONFIDENT = 0.5       # below this every content pack is applied too
MAX_LANGUAGE_CONFIDENCE = 0.95  # the heuristic never reports certainty
EVIDENCE_HITS = 4               # stopword hits needed for full evidence weight
PERSIAN_SCRIPT_WEIGHT = 2       # Persian-only letters count as two stopword hits
LANGUAGE_METHOD = "script + stopword-overlap heuristic"

_APOSTROPHES = str.maketrans({"’": "'", "‘": "'", "ʼ": "'", "´": "'"})
_ARABIC_SCRIPT_FOLD = str.maketrans({
    "ي": "ی", "ى": "ی",  # Arabic yeh / alef maksura -> Persian yeh
    "ك": "ک",                      # Arabic kaf -> Persian keheh
    "ة": "ه", "ہ": "ه", "ھ": "ه", "ە": "ه",  # heh variants
    "ـ": None, "‌": None, "‍": None,  # tatweel, ZWNJ, ZWJ
    **{chr(0x06F0 + d): str(d) for d in range(10)},  # Persian digits
    **{chr(0x0660 + d): str(d) for d in range(10)},  # Arabic-Indic digits
})
_ARABIC_RANGES = ((0x0600, 0x06FF), (0x0750, 0x077F), (0x08A0, 0x08FF), (0xFB50, 0xFDFF), (0xFE70, 0xFEFF))
# Letters used in Persian but not in Arabic: pe, che, zhe, gaf, keheh, Farsi yeh.
_PERSIAN_ONLY_LETTERS = frozenset("پچژگکی")


def _strip_marks(text: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFD", text) if unicodedata.category(ch) != "Mn")


def fold(text: str) -> str:
    """Matching form of untrusted text: NFKC, casefold, accents and Arabic harakat removed,
    typographic apostrophes unified, Persian/Arabic letter variants (ي/ی, ك/ک), tatweel,
    ZWNJ/ZWJ and Persian/Arabic-Indic digits normalized, all whitespace collapsed to single spaces. Used only for matching, never shown."""
    text = unicodedata.normalize("NFKC", text.translate(_APOSTROPHES)).casefold()
    text = unicodedata.normalize("NFC", _strip_marks(text).translate(_ARABIC_SCRIPT_FOLD))
    return " ".join(text.split())  # newlines/tabs/runs of spaces must not split a directive


def _lx(pattern: str) -> re.Pattern:
    """Compile a pack pattern after the same fold as the text; '~' = optional space/ZWNJ."""
    folded = unicodedata.normalize("NFC", _strip_marks(pattern).translate(_ARABIC_SCRIPT_FOLD))
    return re.compile(folded.replace("~", r"\s?"), re.I)


def _w(terms: str) -> str:
    """Whole-word alternation (Latin packs)."""
    return rf"(?<!\w)(?:{terms})(?!\w)"


def _fw(terms: str) -> str:
    """Prefix-bounded alternation (Persian attaches suffixes such as ها/ی/ت to words)."""
    return rf"(?<!\w)(?:{terms})"


def _directive(verb: str, qualifier: str, noun: str, bound=_w) -> str:
    """'ignore previous instructions' shapes: verb + qualifier + noun, in either word order."""
    v, q, n = bound(verb), bound(qualifier), bound(noun)
    qn = rf"(?:{q}.{{0,30}}{n}|{n}.{{0,30}}{q})"
    return rf"(?:{v}.{{0,40}}{qn}|{qn}.{{0,40}}{v})"


def _either(first: str, second: str, bound=_w, gap: int = 40) -> str:
    a, b = bound(first), bound(second)
    return rf"(?:{a}.{{0,{gap}}}{b}|{b}.{{0,{gap}}}{a})"


# rule name -> (label, reason) from the English pack; other packs reuse the names.
_RULE_META = {rule_id: (label, reason) for rule_id, label, _, reason in RULES}

# Patterns per language, in the English rule order. Ids become "<lang>_<name>".
_PACK_SOURCES: dict[str, dict[str, str]] = {
    "de": {
        "injection_ignore_instructions": _directive(
            r"ignorier\w*|vergiss|vergesst|vergessen sie|missacht\w*|uberschreib\w*",
            r"vorherig\w*|bisherig\w*|obig\w*|fruher\w*|alle[nrs]?|deine[nrs]?|ihre[nrs]?|eure[nrs]?|system\w*",
            r"anweisung\w*|instruktion\w*|befehl\w*|regeln|vorgaben|prompts?"),
        "injection_ai_identity": _w(
            r"(du bist|sie sind|ihr seid)( jetzt| nun| ab jetzt)? (eine?n? )?(ki-assistent\w*|ki|k\.i\.|assistent\w*|sprachmodell|chatbot|llm)"
            r"|als (eine? )?(ki|sprachmodell|chatbot)(?![\w-])"),
        "injection_system_prompt": _w(
            r"system-?prompt\w*|systemanweisung\w*|neue anweisungen\s*:|entwicklermodus|developer-?modus"
            r"|tu so,? als (ob|warst|seist)|gib dich als .{0,30} aus|spiele? die rolle"),
        "injection_send_me": _w(r"(schick|schicke|schickt|sende|sendet|schicken sie|senden sie) (mir|uns)"),
        "injection_click": _w(r"(klick|klicke|klickt|klicken sie)( mal)? (hier|auf (diesen|den) link|diesen link|unten)"),
        "injection_credentials": _either(
            r"schick|schicke|schickt|sende|sendet|teile|teilt|gib|geben sie|nenne?|verrate?|bestatige?"
            r"|verifizier\w*|eingeben|gib .{0,20} ein",
            r"passwort\w*|kennwort\w*|api-?schlussel\w*|api[ _-]?key|zugangstoken|zugangsdaten|seed-?phrase"
            r"|privaten? schlussel|2fa-?code|bestatigungscode|verifizierungscode|login-?code"),
        "harassment_threat": _w(
            r"bring dich um|ich bring dich um|tote? dich|ich (werde|will) dich (finden|umbringen|fertigmachen|toten|kaputtmachen)"
            r"|ich finde dich|du solltest sterben|stirb|hoffentlich stirbst du|pass auf,? wo du hingehst"),
        "harassment_insult": _w(
            r"(du bist|sie sind|du) (wirklich |echt )?(ein |eine |so ein |so eine )?(totaler |kompletter |verdammter |echter )?"
            r"(idiot|vollidiot|depp|trottel|versager|arschloch|dummkopf|wichser|loser|vollpfosten)"
            r"|halt (die|deine|ihre) (fresse|klappe|maul)|niemand mag dich|du bist (erbarmlich|wertlos)"),
        "spam_promotion": _w(
            r"follower kaufen|kauf\w* (gunstige |billige )?(follower|likes|abonnenten|aufrufe)|gratis[- ](krypto|bitcoins?|geld)"
            r"|kostenlose[nrs]? (krypto|bitcoins?|geld)|verdiene \d+|von zu hause (aus )?(arbeiten|geld verdienen)"
            r"|schau (dir )?meinen (kanal|shop) an|besuch(t)? meinen (kanal|shop)|folg(e|t)? mir,? ich folge? zuruck"
            r"|rabattcode|gutscheincode|online-?casino|forex-?signale"),
        "bug_symptom": _w(
            r"fehler\w*|bug\w*|absturz\w*|abgesturzt|sturzt (\w+ ){0,4}ab|funktioniert (nicht|nicht mehr|gar nicht)"
            r"|geht (nicht|nicht mehr|kaputt)|kaputt|fehlgeschlagen|schlagt fehl|ausnahme|regression"),
        "question_how": _w(
            r"wie (kann|konnte|mache|macht|funktioniert|installiere|installiert|richte|konfiguriere|bekomme|benutze|nutze"
            r"|stelle|aktiviere|deaktiviere)|gibt es (eine moglichkeit|einen weg)|ist es moglich"
            r"|(kann|konnte) (ich|man)[^.!?]*\?|lasst sich[^.!?]*\?|warum (funktioniert|wird|ist|gibt|geht)"
            r"|welche version|was ist der (beste |richtige )?weg"),
        "question_technical_term": _w(
            r"install\w*|konfigur\w*|einricht\w*|einstellung\w*|version\w*|doku\w*|anleitung\w*|abhangigkeit\w*"
            r"|docker|deploy\w*|api|cli|python") + r"[^?]*\?",
        "feature_ask": _w(
            r"feature-?wunsch|feature-?request|funktionswunsch|(es )?ware (schon|toll|super|gut|hilfreich|praktisch|cool|klasse|nett),? (wenn|falls)"
            r"|bitte (fugt|fuge|fugen sie|baut|baue|bauen sie) .{0,40}(hinzu|ein)"
            r"|(konntet|konntest|konnt|konnen sie|konnten sie) (ihr |du )?.{0,40}(hinzufugen|einbauen|unterstutzen)"
            r"|unterstutzung fur|ich wunsche mir|wunschenswert|auf der roadmap"),
        "collaboration": _w(
            r"zusammenarbeit\w*|zusammenarbeiten|kooperation\w*|kooperier\w*|partnerschaft\w*"
            r"|gemeinsame[nsm]? (projekt|paper|artikel|arbeit|veroffentlichung|studie)\w*|mitautor\w*|co-?autor\w*|sponsor\w*"
            r"|als (redner|sprecher|referent)\w* .{0,40}einladen|einladen,? .{0,20}(vortrag|zu sprechen)"),
        "feedback_suggestion": _w(
            r"vorschlag\w*|ich schlage vor|du solltest|ihr solltet|sie sollten|man sollte|tippfehler|verwirrend|unklar"
            r"|missverstandlich|schwer (zu )?(finden|verstehen|lesen)|veraltet|irrefuhrend|verbesser\w*|feedback|ruckmeldung"),
        "praise": _w(
            r"danke\w*|vielen dank|tolle arbeit|super arbeit|gute arbeit|klasse arbeit|toll(e|er|es|en|em)?|genial\w*"
            r"|grossartig\w*|fantastisch\w*|hervorragend\w*|sehr hilfreich|sehr nutzlich|gefallt mir|ich liebe (das|es|dieses)"
            r"|weiter so"),
    },
    "fr": {
        "injection_ignore_instructions": _directive(
            r"ignor\w*|oubli\w*|ne (tiens|tenez) (pas|plus) compte|fais abstraction|faites abstraction",
            r"precedent\w*|anterieur\w*|ci-dessus|tou(te)?s?|tes|vos|votre|du systeme|systeme",
            r"instructions?|consignes?|directives?|regles|prompts?|ordres"),
        "injection_ai_identity": _w(
            r"(tu es|vous etes|t'es)( maintenant| desormais| a present)? (une? )?(ia|i\.a\.|intelligence artificielle"
            r"|assistant\w*|modele de langage|chatbot|llm)|en tant qu'(ia|assistant\w*)"
            r"|en tant que (modele de langage|chatbot|llm|intelligence artificielle)"),
        "injection_system_prompt": _w(
            r"prompt (du )?systeme|invite (du )?systeme|instructions? (du )?systeme|message systeme"
            r"|nouvelles (instructions|consignes)\s*:|mode developpeur|(fais|faites) semblant d'etre"
            r"|(joue|jouez) le role d'\w+|(agis|agissez) comme (un|une|le|la|mon|ma)"),
        "injection_send_me": _w(r"(envoie|envoyez|transmets|transmettez)[- ](moi|nous)|m'envoy\w*"),
        "injection_click": _w(r"(clique|cliquez|cliquer)( donc)? (ici|sur ce lien|sur le lien|ce lien|ci-dessous)"),
        "injection_credentials": _either(
            r"envoie|envoyez|envoyer|partage|partagez|partager|donne|donnez|donner|communique[rz]?|confirme[rz]?"
            r"|verifie[rz]?|saisis|saisissez|saisir|entre[rz]?|colle[rz]?|tapez|taper|indique[rz]?",
            r"mot de passe|mdp|cle (d')?api|api[ _-]?key|jeton d'acces|token d'acces|phrase de recuperation"
            r"|phrase secrete|cle privee|code 2fa|code de verification|code de connexion|code d'acces"),
        "harassment_threat": _w(
            r"va te tuer|tue-toi|suicide-toi|je vais te (trouver|tuer|retrouver|detruire|frapper|defoncer)"
            r"|je te retrouverai|tu devrais mourir|j'espere que tu (meurs|vas mourir|creves)|creve"),
        "harassment_insult": _w(
            r"(t'es|tu es|vous etes) (vraiment |completement |juste )?(un |une )?(vraie? |gros |grosse |sale |pauvre )?"
            r"(idiot\w*|imbecile|cretin\w*|abruti\w*|connard|connasse|nul|nulle|minable|debile|bouffon\w*|pathetique|bon a rien)"
            r"|ta gueule|ferme-la|fermez-la|personne ne veut de toi"),
        "spam_promotion": _w(
            r"achet\w* des (followers|abonnes|likes|vues)|(abonnes|followers) pas chers|cryptos? gratuites?|bitcoins? gratuits?"
            r"|argent facile|gagne[rz]? \d+|travail a domicile|code promo|abonne[zr]?-(toi|vous) a ma chaine"
            r"|regarde[rz]? ma chaine|visite[rz]? ma (chaine|boutique|page)|follow en retour|casino en ligne"),
        "bug_symptom": _w(
            r"bug\w*|bogue\w*|plantage\w*|(ca|il|elle|le programme|l'outil|l'appli\w*) plante|crash\w*|erreur\w*"
            r"|ne (marche|fonctionne) (pas|plus)|marche pas|fonctionne pas|echou\w*|echec|(est|c'est) casse\w*"
            r"|exception|regression|trace d'appels|trace de pile"),
        "question_how": _w(
            r"comment (faire|puis-je|peut-on|on fait|est-ce|configurer|installer|utiliser|ca marche|ca fonctionne|fonctionne)"
            r"|est-ce qu'(il|on|elle)|est-ce que (je|l'on|on|c'est|ca)|y a-t-il|existe-t-il|peut-on|puis-je"
            r"|pourquoi (est-ce|ca|le|la|il|elle)|quelle version|est-il possible|est-ce possible"),
        "question_technical_term": _w(
            r"install\w*|configur\w*|parametr\w*|reglage\w*|version\w*|doc\w*|dependance\w*|docker|deploi\w*"
            r"|deploy\w*|api|cli|python") + r"[^?]*\?",
        "feature_ask": _w(
            r"demande de fonctionnalite|ce serait (bien|super|genial|utile|pratique|cool) (de|si|d'\w+)"
            r"|(pourriez|pouvez)-vous ajouter|(peux|pourrais)-tu ajouter|merci d'ajouter|ajout(er)? (d'une|une) option"
            r"|prise en charge (de|du|des)|j'aimerais (bien )?(qu'il y ait|pouvoir|avoir)"
            r"|il manque (une|un) (option|fonction\w*)|sur la feuille de route|dans la roadmap"),
        "collaboration": _w(
            r"collabor\w*|partenariat\w*|travailler ensemble|co-?auteur\w*|co-?ecri\w*|projet commun|article commun"
            r"|publication commune|sponsor\w*|parrain\w*|(vous|t') ?inviter a (parler|presenter|intervenir)"),
        "feedback_suggestion": _w(
            r"suggestion\w*|je suggere|je propose|vous devriez|tu devrais|on devrait|faute de frappe|coquille"
            r"|deroutant\w*|confus\w*|pas clair\w*|peu clair\w*|difficile a (trouver|suivre|lire|comprendre)"
            r"|obsolete|trompeu\w*|amelior\w*"),
        "praise": _w(
            r"merci\w*|bravo|super travail|beau travail|excellent travail|du bon travail|genial\w*|formidable"
            r"|j'adore|tres utile|super utile|tres pratique|chapeau|felicitations"),
    },
    "es": {
        "injection_ignore_instructions": _directive(
            r"ignora\w*|olvida\w*|haz caso omiso (de|a)|omite|omitan|no hagas caso (de|a)",
            r"anterior\w*|previa?s?|toda?s?|tus|sus|del sistema|de arriba",
            r"instrucciones|instruccion|indicaciones|reglas|ordenes|prompts?|directrices"),
        "injection_ai_identity": _w(
            r"(eres|tu eres|usted es|ahora eres|ahora tu eres) (una? )?(ia|i\.a\.|inteligencia artificial"
            r"|asistente( virtual)?|modelo de lenguaje|chatbot|llm)"
            r"|como (una? )?(ia|inteligencia artificial|modelo de lenguaje|asistente virtual|chatbot)"),
        "injection_system_prompt": _w(
            r"prompt del sistema|mensaje del sistema|instrucciones del sistema|nuevas instrucciones\s*:|modo desarrollador"
            r"|finge (ser|que eres)|haz de cuenta que eres|actua como (un|una|el|la|mi)|hazte pasar por"),
        "injection_send_me": _w(
            r"enviame|mandame|envianos|mandanos|me (envias|mandas|puedes enviar|puedes mandar|podrias enviar|podrias mandar)"),
        "injection_click": _w(
            r"(haz|haga|da|dale|hagan) (clic|click) (aqui|en (este|el) (enlace|link)|abajo)"
            r"|(pincha|pulsa|clica|clickea) (aqui|en (este|el) (enlace|link)|abajo)"),
        "injection_credentials": _either(
            r"envia|enviame|envie|enviar|manda|mandame|mandar|comparte|compartir|dame|dime|danos|confirma|confirmar"
            r"|verifica|verificar|introduce|introducir|ingresa|ingresar|pega|pegar|escribe|escribir|indica|indicar",
            r"contrasena\w*|clave de (la )?api|clave api|api[ _-]?key|token de acceso|frase semilla"
            r"|frase de recuperacion|clave privada|codigo 2fa|codigo de verificacion|codigo de acceso"),
        "harassment_threat": _w(
            r"matate|suicidate|te voy a (encontrar|matar|hacer dano|destruir|buscar)|te (encontrare|matare|buscare)"
            r"|deberias morir|ojala te mueras|ojala te murieras|cuidate la espalda|cuida tu espalda"),
        "harassment_insult": _w(
            r"(eres|sos|es usted) (un |una )?(completo |completa |total |puto |puta |gran )?(idiota|imbecil|estupid[oa]"
            r"|perdedora?|patetic[oa]|inutil|payaso|tont[oa]|gilipollas|pendej[oa]|subnormal|cretin[oa])"
            r"|callate|cierra la boca|cierra el pico|nadie te quiere"),
        "spam_promotion": _w(
            r"compra(r)? seguidores|seguidores baratos|compra(r)? (likes|me gusta|vistas|visitas|suscriptores)"
            r"|cripto(monedas)? gratis|bitcoins? gratis|dinero (gratis|facil)|gana \$?\d+|trabaja desde (tu )?casa"
            r"|codigo (promocional|de descuento)|mira mi canal|suscribete a mi canal|visita mi (canal|perfil|tienda)"
            r"|sigueme y te sigo|casino (en linea|online)"),
        "bug_symptom": _w(
            r"error\w*|fallo\w*|falla\w*|se (cuelga|bloquea|cierra|rompe)|no funciona|dejo de funcionar|roto|rota"
            r"|excepcion|bug\w*|crash\w*|regresion"),
        "question_how": _w(
            r"como (puedo|hago|configuro|instalo|funciona|uso|hacer|podria)|como se (puede|hace|configura|instala|usa)"
            r"|hay (alguna|una) (forma|manera)|es posible (usar|instalar|configurar|hacer|cambiar)|se puede[^.!?]*\?"
            r"|puedo (usar|instalar|configurar|hacer|cambiar)|por que (no )?(funciona|falla|aparece|sale|da)"
            r"|que version|cual es la (mejor )?(forma|manera)"),
        "question_technical_term": _w(
            r"instal\w*|configur\w*|version\w*|documentacion|docs?|dependencia\w*|docker|despleg\w*|despliegue\w*"
            r"|api|cli|python") + r"[^?]*\?",
        "feature_ask": _w(
            r"solicitud de (funcion|funcionalidad|caracteristica)"
            r"|seria (genial|bueno|util|estupendo|ideal|increible) (que|si|poder|tener|anadir|contar)"
            r"|(podrian|podrias|pueden|puedes|podria) (anadir|agregar|incluir)|por favor (anadan|anade|agreguen|agrega|incluyan)"
            r"|(anadir|agregar) (una opcion|soporte)|soporte para|compatibilidad con|me gustaria (que|poder)"
            r"|en la hoja de ruta|en el roadmap"),
        "collaboration": _w(
            r"colabor\w*|asociacion|alianza|trabajar juntos|coautor\w*|proyecto conjunto|articulo conjunto"
            r"|publicacion conjunta|patrocin\w*|invitar(te|le|les) a (hablar|presentar|dar una charla)"),
        "feedback_suggestion": _w(
            r"sugerencia\w*|sugiero|propongo|deberias|deberian|errata|confus[oa]|poco claro|no (esta|queda) claro"
            r"|dificil de (encontrar|seguir|leer|entender)|desactualizad[oa]|obsolet[oa]|enganos[oa]|mejorar\w*"
            r"|mejoraria|se podria mejorar"),
        "praise": _w(
            r"gracias|muchas gracias|buen trabajo|gran trabajo|excelente trabajo|genial|increible|me encanta|muy util"
            r"|muy bueno|felicidades|enhorabuena|bravo|que maravilla"),
    },
    "it": {
        "injection_ignore_instructions": _directive(
            r"ignora\w*|dimentica\w*|non tenere conto|non considerare|trascura",
            r"precedent\w*|tutte|tutti|tue|sue|vostre|di sistema|del sistema|sopra",
            r"istruzion\w*|indicazion\w*|regole|direttive|prompt|comandi"),
        "injection_ai_identity": _w(
            r"(sei|tu sei|lei e|ora sei|adesso sei) (un'|una |un )?(ia|i\.a\.|intelligenza artificiale|assistente( virtuale)?"
            r"|modello linguistico|modello di linguaggio|chatbot|llm)"
            r"|in quanto (ia|intelligenza artificiale|assistente|modello linguistico)"),
        "injection_system_prompt": _w(
            r"prompt (di|del) sistema|messaggio di sistema|istruzioni di sistema|nuove istruzioni\s*:|modalita sviluppatore"
            r"|fingi di essere|fai finta di essere|comportati come (un|una|il|la|mio|mia)"),
        "injection_send_me": _w(
            r"mandami|inviami|mandaci|inviaci|spediscimi|mi (mandi|invii|puoi mandare|puoi inviare|potresti mandare|potresti inviare)"),
        "injection_click": _w(r"(clicca|cliccate|clicchi|fai clic|fate clic|fai click) (qui|qua|su (questo|il) link|sul link|sotto)"),
        "injection_credentials": _either(
            r"invia|inviami|inviare|manda|mandami|mandare|condividi|condividere|dammi|dimmi|dacci|conferma|confermare"
            r"|verifica|verificare|inserisci|inserire|incolla|incollare|scrivi|scrivere|comunica|comunicare",
            r"password|parola d'ordine|chiave (dell')?api|chiave api|api[ _-]?key|token di accesso|frase seed|seed phrase"
            r"|frase di recupero|chiave privata|codice 2fa|codice di verifica|codice di accesso|codice otp"),
        "harassment_threat": _w(
            r"ammazzati|ucciditi|ti (trovero|ammazzo|uccido|ammazzero|uccidero|distruggo|vengo a prendere)"
            r"|dovresti morire|spero che tu muoia|spero che muori|guardati le spalle"),
        "harassment_insult": _w(
            r"(sei|lei e|sei solo) (un |una |un')?(completo |vero |totale |grande |gran )?(idiota|imbecille|stupid[oa]"
            r"|cretin[oa]|perdente|patetic[oa]|incapace|pagliaccio|deficiente|scem[oa]|coglione|fallit[oa])"
            r"|stai zitt[oa]|chiudi la bocca|nessuno ti vuole"),
        "spam_promotion": _w(
            r"compra(re)? (follower|seguaci)|follower (economici|a poco prezzo)|compra(re)? (like|mi piace|visualizzazioni|iscritti)"
            r"|cripto gratis|bitcoin gratis|soldi facili|guadagna \$?\d+|lavor(o|a) da casa|codice (promozionale|sconto)"
            r"|guarda il mio canale|iscriviti al mio canale|visita il mio (canale|profilo|negozio)|casino online"),
        "bug_symptom": _w(
            r"errore\w*|errori|bug\w*|crash\w*|si (blocca|chiude|pianta|impalla)|non funziona( piu)?"
            r"|(ha smesso|smette) di funzionare|rott[oa]|fallisce|eccezione|regressione|malfunzionament\w*"),
        "question_how": _w(
            r"come (posso|si fa|faccio|configuro|installo|funziona|uso|si usa|si installa|si configura)|c'e (un )?modo"
            r"|e possibile (usare|installare|configurare|fare|cambiare)|si puo[^.!?]*\?|posso (usare|installare|configurare|cambiare)"
            r"|perche (non )?(funziona|fallisce|compare|appare|da)|quale versione|qual e il (modo )?(migliore|giusto)"),
        "question_technical_term": _w(
            r"install\w*|configur\w*|impostazion\w*|version[ei]|documentazione|docs?|dipendenz\w*|docker|deploy\w*"
            r"|api|cli|python") + r"[^?]*\?",
        "feature_ask": _w(
            r"richiesta di (funzionalita|funzione)|sarebbe (bello|utile|fantastico|ottimo|comodo|perfetto) (se|poter|avere|aggiungere)"
            r"|(potreste|potresti|puoi|potete) (aggiungere|integrare|supportare)|per favore aggiung(ete|i)"
            r"|aggiungere (un'opzione|un opzione|il supporto|supporto)|supporto per|mi piacerebbe (se|poter|avere)"
            r"|(nella|sulla) roadmap"),
        "collaboration": _w(
            r"collabor\w*|partnership|lavorare insieme|co-?autor\w*|progetto (congiunto|comune)|articolo congiunto"
            r"|pubblicazione congiunta|sponsor\w*|invitar(ti|la|vi) a (parlare|presentare|tenere un talk)"),
        "feedback_suggestion": _w(
            r"suggeriment\w*|suggerisco|propongo|dovresti|dovreste|refuso|confus[oa]|poco chiar[oa]|non e chiaro"
            r"|difficile da (trovare|seguire|leggere|capire)|obsolet[oa]|non aggiornat[oa]|fuorviante"
            r"|miglior(are|amento|amenti|erei|erebbe)|si potrebbe migliorare"),
        "praise": _w(
            r"grazie\w*|ottimo lavoro|bel lavoro|gran lavoro|ottimo progetto|fantastico|bellissimo|stupendo"
            r"|mi piace (molto|tanto)|lo adoro|molto utile|utilissimo|complimenti|brav[oai]|eccellente"),
    },
    "pt": {
        "injection_ignore_instructions": _directive(
            r"ignor\w*|esqueca\w*|esquece\w*|esquecam|desconsider\w*|nao (considere|leve em conta)",
            r"anterior\w*|previa?s?|toda?s?|suas|tuas|do sistema|acima",
            r"instrucoes|instrucao|orientacoes|regras|comandos|prompts?|diretrizes"),
        "injection_ai_identity": _w(
            r"(voce e|tu es|voce agora e|agora voce e|a partir de agora voce e) (uma? )?(ia|i\.a\.|inteligencia artificial"
            r"|assistente( virtual)?|modelo de linguagem|chatbot|llm)"
            r"|como (uma? )?(ia|inteligencia artificial|modelo de linguagem|assistente virtual|chatbot)"),
        "injection_system_prompt": _w(
            r"prompt do sistema|mensagem do sistema|instrucoes do sistema|novas instrucoes\s*:|modo (de )?desenvolvedor"
            r"|finja (ser|que e|que voce e)|aja como (um|uma|o|a|meu|minha)|faca de conta que"),
        "injection_send_me": _w(r"me (envie|envia|mande|manda)|(envie|envia|manda|mande)-(me|nos)|nos (envie|mande)"),
        "injection_click": _w(r"(clique|clica|cliquem|clicar) (aqui|neste link|nesse link|no link|abaixo)"),
        "injection_credentials": _either(
            r"envie|envia|enviar|mande|manda|mandar|compartilhe|compartilha|compartilhar|partilhe|partilha"
            r"|me (de|da|diga|passe|passa)|confirme|confirmar|verifique|verificar|digite|digitar|insira|inserir"
            r"|informe|informar",
            r"senha\w*|chave (de |da )?api|api[ _-]?key|token de acesso|frase semente|frase de recuperacao|chave privada"
            r"|codigo 2fa|codigo de verificacao|codigo de acesso"),
        "harassment_threat": _w(
            r"se mata|vai se matar|mate-se|vou te (encontrar|matar|pegar|destruir|achar)|vou (encontrar|matar|achar) voce"
            r"|voce deveria morrer|espero que voce morra|tomara que (voce )?morra"),
        "harassment_insult": _w(
            r"(voce e|tu es|voce eh) (um |uma )?(completo |completa |total |grande )?(idiota|imbecil|estupid[oa]|burr[oa]"
            r"|perdedora?|patetic[oa]|inutil|palhaco|otari[oa]|babaca)|cala a boca|cale a boca|cala boca"
            r"|ninguem gosta de voce"),
        "spam_promotion": _w(
            r"compr(e|ar|a) seguidores|seguidores baratos|compr(e|ar|a) (curtidas|likes|visualizacoes|inscritos)"
            r"|cripto gratis|bitcoins? gratis|dinheiro facil|ganhe (r\$ ?)?\$?\d+|trabalhe (de|em) casa|renda extra"
            r"|codigo promocional|cupom de desconto|veja meu canal|inscreva-se no meu canal|visite meu (canal|perfil)"
            r"|sigo de volta|cassino online"),
        "bug_symptom": _w(
            r"erros?(?! de (digitacao|ortografia))|bug\w*|falh(a|as|ou|ando)|trav(a|ou|ando|ado)|crash\w*"
            r"|nao funciona( mais)?|parou de funcionar|quebrad[oa]|quebrou|excecao|regressao|deu pau"),
        "question_how": _w(
            r"como (posso|faco|configuro|instalo|funciona|uso|fazer|consigo)|tem (algum|um) jeito"
            r"|existe (alguma|uma) (forma|maneira)|e possivel (usar|instalar|configurar|fazer|mudar)"
            r"|da (para|pra) (usar|instalar|configurar|fazer|mudar)|posso (usar|instalar|configurar|mudar)"
            r"|por que (nao )?(funciona|falha|aparece|da)|qual versao|qual e a (melhor )?(forma|maneira)"),
        "question_technical_term": _w(
            r"instal\w*|configur\w*|versao|versoes|documentacao|docs?|dependencia\w*|docker|implant\w*|deploy\w*"
            r"|api|cli|python") + r"[^?]*\?",
        "feature_ask": _w(
            r"pedido de (funcionalidade|recurso)|sugestao de (recurso|funcionalidade)"
            r"|seria (otimo|legal|bom|util|incrivel|interessante) (se|ter|poder|adicionar)"
            r"|(poderiam|poderia|podem|pode) (adicionar|incluir|suportar)|por favor (adicionem|adicione|incluam)"
            r"|adicionar (uma opcao|suporte)|suporte (para|ao)|gostaria (que|de poder)|no roadmap"),
        "collaboration": _w(
            r"colabor\w*|parceria\w*|trabalhar juntos|co-?autor\w*|projeto conjunto|artigo conjunto|publicacao conjunta"
            r"|patrocin\w*|convida-lo a (falar|apresentar|palestrar)|(convidar voce|te convidar) para (falar|palestrar|apresentar)"),
        "feedback_suggestion": _w(
            r"sugest(ao|oes)|sugiro|proponho|voce deveria|voces deveriam|deveria(m)?|erro de digitacao|confus[oa]"
            r"|pouco clar[oa]|nao esta clar[oa]|dificil de (encontrar|seguir|ler|entender)|desatualizad[oa]"
            r"|obsolet[oa]|engan\w*|melhorar\w*|melhoria\w*"),
        "praise": _w(
            r"obrigad[oa]|valeu|muito obrigad[oa]|otimo trabalho|bom trabalho|excelente trabalho|incrivel|adorei|amei"
            r"|muito util|parabens|show de bola|sensacional|mandou bem"),
    },
    "fa": {
        "injection_ignore_instructions": _directive(
            r"نادیده (بگیر|بگیرید|گرفته)|فراموش (کن|کنید)|توجه (نکن|نکنید)|اعتنا (نکن|نکنید)|کنار (بگذار|بذار)|بی~خیال",
            r"قبلی|قبل|پیشین|بالا|سیستم|همه|تمام|تمامی|خودت|خودتان",
            r"دستور|قوانین|قواعد|پرامپت|راهنمایی|فرمان", bound=_fw),
        "injection_ai_identity": _fw(
            r"(تو|شما) (الان |از الان |از این به بعد )?(یک |یه )?(هوش مصنوعی|دستیار|مدل زبانی|چت~بات|ربات|بات) "
            r"(هستی|هستید|هستین|باش|باشید)|به عنوان (یک |یه )?(هوش مصنوعی|مدل زبانی|چت~بات)"),
        "injection_system_prompt": _fw(
            r"پرامپت (سیستم|سیستمی)|دستورات (سیستم|سیستمی)|پیام سیستم|دستورات جدید\s*:|دستورالعمل جدید\s*:"
            r"|حالت توسعه~دهنده|وانمود (کن|کنید)|نقش .{0,20} را بازی (کن|کنید)|جیلبریک"),
        "injection_send_me": _fw(
            r"(برام|برایم|برامون|برایمان|برای من|برای ما|به من|به ما) (\S+ ){0,3}(بفرست|ارسال (کن|کنید))"
            r"|(بفرست|بفرستید|ارسال کن|ارسال کنید) (برام|برایم|برای من|به من)"),
        "injection_click": _fw(
            r"(روی|رو|به) (این |همین )?(لینک|اینجا|پیوند)\w* (\S+ )?کلیک (کن|کنید|کنین)|اینجا کلیک (کن|کنید|کنین)"
            r"|کلیک (کن|کنید) (روی|رو) (این|لینک)"),
        "injection_credentials": _either(
            r"بفرست|بده|بدید|بدهید|بگو|بگید|بگویید|وارد (کن|کنید)|تایید (کن|کنید)|ارسال (کن|کنید)|بنویس",
            r"رمز عبور|رمز ورود|رمزت|پسورد|گذرواژه|کلید (ای پی ای|api)|api[ _-]?key|توکن دسترسی"
            r"|کد (تایید|ورود|دو مرحله~ای|یکبار مصرف)|عبارت بازیابی|کلید خصوصی", bound=_fw),
        "harassment_threat": _fw(
            r"خودتو بکش|خودت (رو|را) بکش|خودکشی کن|می~کشمت|پیدات می~کنم|پیدایت می~کنم|بمیر(?!\w)|امیدوارم بمیری"
            r"|باید بمیری|حسابتو می~رسم|حسابت (رو|را) می~رسم"),
        "harassment_insult": _fw(
            r"(احمق|کودن|بی~شعور|عوضی|کثافت|الاغ|بی~عرضه|نفهم)(ی|ید|ین| هستی| هستید)?(?!\w)|خفه (شو|شید|شوید)"
            r"|هیچ~کس (دوستت ندارد|دوستت نداره|تو را نمی~خواهد)"),
        "spam_promotion": _fw(
            r"خرید فالوور|فالوور (ارزان|فیک)|خرید (لایک|ویو|بازدید|ممبر|سابسکرایبر)|فالو (کن|کنید) فالو~ت (می~کنم)"
            r"|فالو~بک|ارز دیجیتال رایگان|بیت~کوین رایگان|پول رایگان|درآمد (میلیونی|دلاری)"
            r"|کسب درآمد (از خانه|در منزل|آنلاین|اینترنتی)|کد تخفیف|کانال (من|منو) (ببین|ببینید)"
            r"|پیج (من|منو) فالو (کن|کنید)|کازینو|شرط~بندی|سیگنال فارکس"),
        "bug_symptom": _fw(
            r"باگ|ارور|کرش|خطا(ی|ها)?(?!\w)|کار (نمی~کند|نمی~کنه|نکرد)|خراب|از کار (افتاد|می~افتد|میفته)"
            r"|(بسته|کرش) (می~شه|می~شود)|هنگ (می~کند|می~کنه|کرد)|استثنا|مشکل (دارد|داره|پیدا کرده|خورده)"
            r"|به مشکل (خورد|می~خوره|می~خورد)"),
        "question_how": _fw(
            r"چطور|چگونه|چه~طور|چه جوری|چجوری|آیا (می~شود|می~شه|می~توان|راهی|امکان|ممکن)"
            r"|امکانش (هست|وجود داره)|راهی (هست|وجود دارد|وجود داره|داره)|چرا .{0,30}(کار نمی|نمی~شود|نمی~شه|نمی~ده)"
            r"|(کدام|کدوم) نسخه|(چی|چه) کار کنم"),
        "question_technical_term": _fw(
            r"نصب|پیکربندی|کانفیگ|تنظیم|نسخه|ورژن|مستندات|داکیومنت|وابستگی|داکر|استقرار|دیپلوی|api|cli|پایتون")
            + r"[^?؟]*[?؟]",
        "feature_ask": _fw(
            r"درخواست (قابلیت|ویژگی|فیچر)|(عالی|خوب|خیلی خوب|جالب|مفید) (می~شد|می~شود|می~شه) (اگر|اگه)|کاش(کی)?(?!\w)"
            r"|لطفا .{0,40}(اضافه|پشتیبانی) (کن|کنید|کنین)"
            r"|(می~شه|می~شود|می~توانید|می~تونید|ممکنه) .{0,40}(اضافه|پشتیبانی) (کن|کنید|کنین|کرد)"
            r"|پشتیبانی از|اضافه کردن (گزینه|قابلیت|پشتیبانی)|(در|توی|تو) (نقشه راه|رودمپ)"),
        "collaboration": _fw(
            r"همکاری (کنیم|داشته باشیم|کنید|مشترک|با شما|با تیم)|پیشنهاد همکاری|مشارکت (کنیم|در پروژه)|با هم کار کنیم"
            r"|هم~نویسنده|پروژه (مشترک|مشترکی)|مقاله (مشترک|مشترکی)|حمایت مالی|اسپانسر"
            r"|دعوت .{0,30}(سخنرانی|ارائه)|(سخنرانی|ارائه) .{0,30}دعوت"),
        "feedback_suggestion": _fw(
            r"پیشنهاد|بهتره|بهتر است|بهتر بود|بهتر (می~شد|می~شه)|غلط (تایپی|املایی)|اشتباه تایپی|گیج~کننده|نامفهوم"
            r"|واضح نیست|گنگ(ه)?(?!\w)|سخت (پیدا|خوانده|فهمیده|می~شه)|قدیمی (است|شده|هست)|منسوخ|گمراه~کننده"
            r"|بهبود|بازخورد"),
        "praise": _fw(
            r"ممنون|مرسی|متشکر|سپاس|تشکر|دستت درد نکنه|دستتون درد نکنه|دست مریزاد|عالی(ه| است| بود)?(?!\w)"
            r"|کارت (عالیه|عالی است|حرف نداره)|خیلی خوب(ه)?(?!\w)|فوق~العاده|بی~نظیر|خیلی (مفید|کاربردی)"
            r"|خسته نباشید|دمت گرم|دمتون گرم|آفرین|عاشقشم"),
    },
}

_OPT_OUT_SOURCES = {
    "de": _w(
        r"kontaktier\w* (sie |du )?mich nicht( mehr)?|schreib\w* mir nicht mehr"
        r"|hor\w* auf,? mich (zu )?(kontaktieren|anschreiben|taggen|erwahnen|markieren|nerven)|mich anzuschreiben"
        r"|lass(t)? mich in ruhe|lassen sie mich in ruhe|bitte nicht (mehr )?(kontaktieren|taggen|markieren|erwahnen|anschreiben)"
        r"|nicht mehr (kontaktieren|taggen|markieren|erwahnen|anschreiben)|keine (weiteren )?nachrichten mehr|abmelden|abbestellen"),
    "fr": _w(
        r"ne me (contacte[zr]?|contactez|mentionne[zr]?|mentionnez|tague[zr]?|taguez|identifie[zr]?|identifiez|ecri(s|vez)"
        r"|relance[zr]?|relancez) (plus|pas)|ne plus me (contacter|mentionner|taguer|identifier|ecrire|relancer)"
        r"|arret\w* de me (contacter|mentionner|taguer|identifier|ecrire|relancer|spammer)|laisse[zr]?-moi tranquille"
        r"|desinscri\w*|desabonne[zr]?-moi|me desabonner"),
    "es": _w(
        r"no me (contactes|contacten|contacte|escribas|escriban|escriba|menciones|mencionen|mencione|etiquetes|etiqueten"
        r"|etiquete|mandes mensajes)( mas)?|deja(d|n)? de (contactarme|escribirme|mencionarme|etiquetarme|molestarme)"
        r"|dej(a|e|en|ad)(me|nme)? en paz|dar(me)? de baja|cancelar (la |mi )?suscripcion"),
    "it": _w(
        r"non (contattarmi|scrivermi|menzionarmi|taggarmi)( piu)?|non mi (contattare|contattate|contatti|scrivete|scrivere"
        r"|menzionare|menzionate|taggare|taggate)( piu)?|smett(i|ete|ila|etela) di (contattarmi|scrivermi|menzionarmi|taggarmi)"
        r"|lascia(mi|temi) in pace|mi lasci in pace|disiscriv\w*|cancellami"),
    "pt": _w(
        r"nao me (contate|contatem|contacte|contactem|procure|procurem|mencione|mencionem|marque|marquem"
        r"|mande mensage(m|ns)|escreva|escrevam)( mais)?"
        r"|par(e|em|ar) de me (contatar|contactar|mencionar|marcar|mandar mensage(m|ns)|escrever|procurar)"
        r"|me deixe(m)? em paz|deixe-me em paz|cancelar (a |minha )?inscricao|descadastr\w*"),
    "fa": _fw(
        r"(با من|باهام|با ما) تماس (نگیر|نگیرید|نگیرین)|(دیگه|دیگر) (به من |بهم )?پیام (نده|ندید|ندهید|نفرست|نفرستید)"
        r"|(من|منو|مرا|من را) (تگ|منشن) (نکن|نکنید|نکنین)|مزاحم(م)? (نشو|نشید|نشوید)|راحتم (بذار|بذارید|بگذارید)"
        r"|دست از سرم (بردار|بردارید)|لغو (اشتراک|عضویت)"),
}

Rule = tuple[str, str, re.Pattern | None, str]


def _build_pack(lang: str) -> tuple[Rule, ...]:
    pack = []
    for name, source in _PACK_SOURCES[lang].items():
        label, reason = _RULE_META[name]
        pack.append((f"{lang}_{name}", label, _lx(source), f"{reason} ({LANGUAGE_NAMES[lang]} pack)"))
    return tuple(pack)


RULE_PACKS: dict[str, tuple[Rule, ...]] = {"en": RULES, **{lang: _build_pack(lang) for lang in LANGUAGES[1:]}}
OPT_OUT_PACKS: dict[str, re.Pattern] = {"en": OPT_OUT, **{lang: _lx(src) for lang, src in _OPT_OUT_SOURCES.items()}}
RULE_INDEX = {rule_id: (label, reason) for pack in RULE_PACKS.values() for rule_id, label, _, reason in pack}

# Function words used only for the language guess. Words shared by several of
# these languages ("de", "la", "que", "no", "in", "a", "e") are left out on
# purpose so that a hit points at one language.
_STOPWORD_SOURCES = {
    "en": "the and is are it this that to of for you with how what not my do does can have be please thanks "
          "when there would should your from but just any",
    "de": "der die das und ist nicht ich ein eine mit wie zu den dem auf für kann bitte danke wenn aber auch noch "
          "wir mir mich sich oder nur bei von habe sind wird mein meine dass kein keine euch ihr ohne unser unsere "
          "diese dieser dieses",
    "fr": "le les et est une des je pas pour qui dans avec sur ce cette vous nous elle mais merci sont ne au aux "
          "mon votre très j sans notre nos ces mes leur leurs",
    "es": "el los las y pero muy gracias puedo hay donde del al esto es estoy tengo cuando también ahora "
          "usted nada sin nuestro nuestra ese esa mucho muchos muchas",
    "it": "gli di sono della delle nel nella questo questa grazie molto perché anche ancora che mio mia "
          "tutto funziona sul alla senza nostro nostra questi quello degli ho",
    "pt": "os um uma não meu minha você vocês isso isto muito obrigado obrigada eu ele ela dos das em também "
          "já tem estou consigo pra sem pelo pela ao aos seu sua nosso nossa esse essa",
    "fa": "و در به از که این را رو با است برای آن یک یه هم من تو شما ما چطور چرا هست بود اما ولی اگر خیلی "
          "ممنون لطفاً نمی می دارم کنم نیست",
}
STOPWORDS = {lang: frozenset(fold(word) for word in words.split()) for lang, words in _STOPWORD_SOURCES.items()}
_TOKEN = re.compile(r"[^\W\d_]+")


def _is_arabic_script(ch: str) -> bool:
    code = ord(ch)
    return any(low <= code <= high for low, high in _ARABIC_RANGES)


def guess_language(text: str) -> dict:
    """Transparent language guess: script counts plus stopword overlap per language.

    ``confidence = min(0.95, share * evidence)`` where ``share`` is the top language's
    fraction of all stopword hits and ``evidence = min(1, hits / 4)``. Persian-only
    letters (پ چ ژ گ ک ی) add two hits to Persian. Below 0.5 every rule pack is applied.
    This is a heuristic guess, never a certainty, and short comments stay uncertain.
    """
    nfkc = unicodedata.normalize("NFKC", text)
    letters = [ch for ch in nfkc if ch.isalpha()]
    arabic = sum(1 for ch in letters if _is_arabic_script(ch))
    latin = sum(1 for ch in letters if "LATIN" in unicodedata.name(ch, ""))
    tokens = _TOKEN.findall(fold(text))
    scores: dict[str, int] = {}
    for lang in LANGUAGES:
        hits = sum(1 for token in tokens if token in STOPWORDS[lang])
        if lang == "fa" and arabic and any(ch in _PERSIAN_ONLY_LETTERS for ch in nfkc):
            hits += PERSIAN_SCRIPT_WEIGHT
        if hits:
            scores[lang] = hits
    script = {"latin": latin, "arabic": arabic, "other": len(letters) - latin - arabic}
    if not scores:
        return {"language": "und", "confidence": 0.0, "method": LANGUAGE_METHOD, "heuristic": True,
                "scores": {}, "script_letters": script, "candidates": [], "mixed": False,
                "packs_applied": list(LANGUAGES), "all_packs_fallback": True}
    top = max(LANGUAGES, key=lambda lang: (scores.get(lang, 0), -LANGUAGES.index(lang)))
    top_hits = scores[top]
    share = top_hits / sum(scores.values())
    confidence = round(min(MAX_LANGUAGE_CONFIDENCE, share * min(1.0, top_hits / EVIDENCE_HITS)), 2)
    candidates = [lang for lang in LANGUAGES if lang == top
                  or (scores.get(lang, 0) >= 2 and 2 * scores.get(lang, 0) >= top_hits)]
    candidates.sort(key=lambda lang: (-scores[lang], LANGUAGES.index(lang)))
    fallback = confidence < LANGUAGE_CONFIDENT
    packs = list(LANGUAGES) if fallback else [lang for lang in LANGUAGES if lang == "en" or lang in candidates]
    return {"language": top, "confidence": confidence, "method": LANGUAGE_METHOD, "heuristic": True,
            "scores": {lang: scores[lang] for lang in LANGUAGES if lang in scores}, "script_letters": script,
            "candidates": candidates, "mixed": len(candidates) > 1,
            "packs_applied": packs, "all_packs_fallback": fallback}
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PHONE = re.compile(r"(?<![\w/])\+?\d[\d ().-]{7,}\d(?![\w/])")
MIN_PHONE_DIGITS = 9  # Avoid redacting ISO dates (8 digits) and short versions.
URL_IN_TEXT = re.compile(r"(?:https?://|www\.)\S+", re.I)
BIDI_CONTROLS = frozenset("‪‫‬‭‮⁦⁧⁨⁩‎‏")

REPLY_TEMPLATES = {
    "bug_report": ("Thanks for reporting this. Could you share the version, operating system and exact "
                   "steps to reproduce?{docs} We will look into it."),
    "technical_question": "Good question.{docs}",
    "feature_request": "Thanks for the suggestion; it is noted for review against the roadmap.{docs}",
    "actionable_feedback": "Thanks for the concrete feedback; it helps improve the project.{docs}",
    "collaboration_opportunity": ("Thanks for reaching out about collaborating. Happy to continue the "
                                  "conversation through the project's public channels.{docs}"),
    "praise": "Thank you, glad it is useful!{docs}",
}
DOCS_SENTENCE = {
    "bug_report": " The troubleshooting notes may help meanwhile: {url}",
    "technical_question": " This is covered in the documentation: {url}",
}
NO_DOCS_SENTENCE = {
    "technical_question": " [OWNER: add a verified answer or docs link before posting.]",
}
DEFAULT_DOCS_SENTENCE = " Related documentation: {url}"


def sanitize_text(value: object, limit: int = MAX_TEXT_CHARS) -> tuple[str, dict]:
    """Return untrusted text without control/format characters, capped at ``limit``."""
    text = value if isinstance(value, str) else ""
    removed = bidi = 0
    kept = []
    for ch in text:
        if ch in BIDI_CONTROLS:
            bidi += 1
        category = unicodedata.category(ch)
        if (category == "Cc" and ch not in "\n\t") or category in ("Cf", "Co", "Cs"):
            removed += 1
            continue
        kept.append(" " if ch == "\t" else ch)
    clean = re.sub(r"[  ]{2,}", " ", "".join(kept)).strip()
    clean = re.sub(r"\n{3,}", "\n\n", clean)
    truncated = len(clean) > limit
    if truncated:
        clean = clean[:limit].rstrip() + "…"
    return clean, {"control_chars_removed": removed, "bidi_controls": bidi, "truncated": truncated}


def redact_private_data(text: str) -> tuple[str, bool]:
    """Replace secrets, e-mail addresses and phone-number-like digit runs in an excerpt.

    Secrets (bearer/JWT/GitHub/Telegram tokens, app-password shapes, ``token=`` style
    assignments and REACHWEAVER_* secret env values) use ``privacy.redact`` so a token
    pasted into a comment never reaches the excerpt, review sheet or any draft.
    """
    redacted = redact_secrets(text)
    redacted = EMAIL.sub("[redacted-email]", redacted)
    redacted = PHONE.sub(lambda m: "[redacted-phone]" if sum(c.isdigit() for c in m.group())
                         >= MIN_PHONE_DIGITS else m.group(), redacted)
    return redacted, redacted != text


def analyze(text: str, flags: dict | None = None) -> dict:
    """Label, matched rules, language guess and opt-out detection for one comment.

    Safety rules (suspicious, harassment, spam) and opt-out phrases run from EVERY
    language pack. Content rules run from English plus the guessed language(s), or
    from every pack when the guess is below ``LANGUAGE_CONFIDENT``.
    """
    flags = flags or {}
    # The fold maps full-width/compatibility look-alikes, accents and Persian letter
    # variants onto one form so obfuscated or differently typed directives still match.
    folded = fold(text)
    guess = guess_language(text)
    content_packs = set(guess["packs_applied"])
    matched = []
    for lang in LANGUAGES:
        for rule_id, label, pattern, reason in RULE_PACKS[lang]:
            if label not in SAFETY_LABELS and lang not in content_packs:
                continue
            if rule_id == "hidden_unicode_controls":
                hit = flags.get("bidi_controls", 0) > 0
            elif rule_id == "spam_link_flood":
                hit = len(URL_IN_TEXT.findall(folded)) >= 3
            else:
                hit = bool(cast(re.Pattern, pattern).search(folded))
            if hit:
                matched.append({"rule": rule_id, "label": label, "why": reason})
    opt_out_languages = [lang for lang in LANGUAGES if OPT_OUT_PACKS[lang].search(folded)]
    labels = {m["label"] for m in matched}
    chosen = next((label for label in PRECEDENCE if label in labels), None)
    if chosen is None:
        chosen = "off_topic"
        matched = [{"rule": "no_rule_matched", "label": "off_topic", "why": "No triage rule matched"}]
    return {"label": chosen, "matched_rules": matched, "language_guess": guess,
            "opt_out": bool(opt_out_languages), "opt_out_languages": opt_out_languages}


def classify(text: str, flags: dict | None = None) -> tuple[str, list[dict]]:
    """Deterministic label plus every matched rule (id, label, reason)."""
    result = analyze(text, flags)
    return result["label"], result["matched_rules"]


def _https_url(value: object, field: str, where: str) -> str:
    # Comment permalinks often carry an anchor (e.g. #issuecomment-1); allow a plain one.
    base, _, anchor = value.partition("#") if isinstance(value, str) else ("", "", "")
    if (not isinstance(value, str) or len(value) > 2048 or not public_url(base)
            or not re.fullmatch(r"[\w.:=-]*", anchor)):
        raise ValidationError(f"{where}: {field} must be a public HTTPS URL without credentials")
    return value


def load_export(data: object) -> list[dict]:
    """Validate the normalized export contract and return items (not yet sanitized)."""
    if not isinstance(data, dict) or data.get("schema_version") != EXPORT_SCHEMA:
        raise ValidationError(f"Engagement export must be an object with schema_version {EXPORT_SCHEMA}")
    items = data.get("items")
    if not isinstance(items, list) or not items:
        raise ValidationError("Engagement export needs a non-empty items list")
    if len(items) > MAX_ITEMS:
        raise ValidationError(f"Engagement export is limited to {MAX_ITEMS} items per run")
    seen = set()
    out = []
    for index, item in enumerate(items):
        where = f"items[{index}]"
        if not isinstance(item, dict):
            raise ValidationError(f"{where} must be an object")
        sensitive = [key for key in item if SENSITIVE_KEY.search(str(key))]
        if sensitive:
            raise ValidationError(f"{where}: secret-like fields are not accepted: {', '.join(sorted(sensitive))}")
        for field in REQUIRED_STRING_FIELDS:
            if not isinstance(item.get(field), str) or (field != "text" and not item[field].strip()):
                raise ValidationError(f"{where}: {field} must be a non-empty string")
        if not isinstance(item.get("author_is_owner"), bool):
            raise ValidationError(f"{where}: author_is_owner must be true or false")
        if len(item["id"]) > MAX_ID_CHARS or len(item["account_id"]) > MAX_ID_CHARS:
            raise ValidationError(f"{where}: id/account_id too long")
        if item["id"] in seen:
            raise ValidationError(f"{where}: duplicate id")
        seen.add(item["id"])
        platform = item["platform"].strip().lower()
        if platform not in ENGAGEMENT_PLATFORMS:
            raise ValidationError(f"{where}: unknown platform {platform!r}")
        _https_url(item["url"], "url", where)
        kind = item.get("kind")
        if kind is not None and kind not in INBOX_KINDS:
            raise ValidationError(f"{where}: kind must be one of {', '.join(INBOX_KINDS)}")
        for field in OPTIONAL_URL_FIELDS:
            value = item.get(field)
            if value is None or value == "":
                continue
            if field == "in_reply_to" and isinstance(value, str) and value and not value.startswith("https://"):
                if len(value) > MAX_ID_CHARS:
                    raise ValidationError(f"{where}: in_reply_to too long")
                continue  # A parent item id rather than a URL.
            _https_url(value, field, where)
        reach, sentiment = item.get("reach"), item.get("sentiment")
        if reach is not None and (type(reach) is not int or not 0 <= reach <= MAX_REACH):
            raise ValidationError(f"{where}: reach must be an integer 0..{MAX_REACH} (omit it when unknown)")
        if sentiment is not None and sentiment not in SENTIMENTS:
            raise ValidationError(f"{where}: sentiment must be one of {', '.join(SENTIMENTS)} (omit it when unknown)")
        out.append({
            "id": item["id"], "platform": platform, "account_id": item["account_id"],
            "author_handle": item["author_handle"], "author_is_owner": item["author_is_owner"],
            "text": item["text"], "url": item["url"], "created_at": parse_utc(item["created_at"]),
            "in_reply_to": item.get("in_reply_to") or None, "thread_root_url": item.get("thread_root_url") or None,
            "kind": kind or KIND_UNSPECIFIED, "reach": reach, "sentiment": sentiment,
        })
    return out


def load_docs_map(data: object) -> list[dict]:
    """Owner-supplied topic keyword -> documentation URL map, in priority order."""
    if data is None:
        return []
    if not isinstance(data, dict) or data.get("schema_version") != DOCS_MAP_SCHEMA:
        raise ValidationError(f"Docs map must be an object with schema_version {DOCS_MAP_SCHEMA}")
    topics = data.get("topics")
    if not isinstance(topics, list) or len(topics) > MAX_DOC_TOPICS:
        raise ValidationError(f"Docs map topics must be a list of at most {MAX_DOC_TOPICS}")
    out = []
    for index, topic in enumerate(topics):
        where = f"topics[{index}]"
        if not isinstance(topic, dict):
            raise ValidationError(f"{where} must be an object")
        keywords = topic.get("keywords")
        if (not isinstance(keywords, list) or not keywords
                or not all(isinstance(k, str) and 1 <= len(k.strip()) <= 60 for k in keywords)):
            raise ValidationError(f"{where}: keywords must be a non-empty list of short strings")
        url = _https_url(topic.get("url"), "url", where)
        pattern = re.compile(r"\b(" + "|".join(re.escape(fold(k.strip())) for k in keywords) + r")\b")
        out.append({"topic": str(topic.get("topic") or keywords[0])[:80], "url": url, "pattern": pattern})
    return out


def load_reply_evidence(data: object) -> list[dict]:
    """Optional owner-vetted facts a reply draft may state, from the docs map ``evidence`` list.

    Each entry has the campaign-evidence shape: ``id``, ``claim`` and optional ``excerpt``, ``kind``
    and an https ``url``. They are the only facts the claims check will accept in a reply.
    """
    if data is None or not isinstance(data, dict):
        return []
    raw = data.get("evidence")
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > MAX_REPLY_EVIDENCE:
        raise ValidationError(f"Docs map evidence must be a list of at most {MAX_REPLY_EVIDENCE} entries")
    out: list[dict] = []
    seen: set[str] = set()
    for index, entry in enumerate(raw):
        where = f"evidence[{index}]"
        if not isinstance(entry, dict):
            raise ValidationError(f"{where} must be an object")
        ident = entry.get("id")
        if not isinstance(ident, str) or not _EVIDENCE_ID.match(ident) or ident in seen:
            raise ValidationError(f"{where}: id must be a unique short slug (letters, digits, . _ -)")
        seen.add(ident)
        claim = entry.get("claim")
        if not isinstance(claim, str) or not claim.strip() or len(claim) > 1000:
            raise ValidationError(f"{where}: claim must be 1-1000 characters of text")
        excerpt = entry.get("excerpt")
        if excerpt is not None and (not isinstance(excerpt, str) or len(excerpt) > 5000):
            raise ValidationError(f"{where}: excerpt must be text of at most 5000 characters")
        kind = entry.get("kind")
        if kind is not None and kind not in REPLY_EVIDENCE_KINDS:
            raise ValidationError(f"{where}: kind must be one of {', '.join(sorted(REPLY_EVIDENCE_KINDS))}")
        item = {"id": ident, "claim": claim.strip()}
        if excerpt:
            item["excerpt"] = excerpt
        if kind:
            item["kind"] = kind
        if entry.get("url") is not None:
            item["url"] = _https_url(entry.get("url"), "url", where)
        out.append(item)
    return out


def check_reply_drafts(drafts: list[dict], evidence: list[dict]) -> dict:
    """Run the lexical claims check on every reply draft against the owner's evidence list.

    Annotates each draft with ``claims_check`` (``clean``, ``review`` or ``error``) and returns a
    summary. It never blocks, edits or sends anything: a draft stays ``draft_requires_approval``
    and the owner decides. Warnings that do not apply to a reply (missing CTA, near-duplicates of
    the fixed templates) are left out.
    """
    from .claims import check_claims
    cited = [item["id"] for item in evidence]
    report = check_claims({
        "evidence": evidence,
        "variants": [{"id": d["draft_id"], "platform": d["platform"], "account_id": d["account_id"],
                      "text": d["text"], "evidence_ids": cited} for d in drafts]})
    by_id = {entry["variant_id"]: entry for entry in report["variants"]}
    counts = {"clean": 0, "review": 0, "error": 0}
    for draft in drafts:
        found = [{"code": f["code"], "severity": f["severity"], "message": f["message"]}
                 for f in by_id[draft["draft_id"]]["findings"] if f["severity"] in ("error", "review")]
        status = "error" if any(f["severity"] == "error" for f in found) else ("review" if found else "clean")
        counts[status] += 1
        draft["claims_check"] = {"method": "lexical", "status": status, "evidence_ids": cited, "findings": found}
    return {"method": "lexical", "checked": len(drafts), **counts, "evidence_entries": len(evidence),
            "limitation": CLAIMS_CHECK_LIMITATION}


def load_templates(data: object) -> dict[str, dict[str, str]]:
    """Optional owner-written reply templates per language from the docs map.

    ``{"templates": {"de": {"technical_question": "Gute Frage. {docs}"}}}``. Keys are
    supported language codes and draftable labels; ``{docs}`` is the only placeholder
    (replaced by the matched docs URL or removed). ReachWeaver never translates: a
    language without an owner template gets the English template.
    """
    if data is None:
        return {}
    if not isinstance(data, dict) or data.get("schema_version") != DOCS_MAP_SCHEMA:
        raise ValidationError(f"Docs map must be an object with schema_version {DOCS_MAP_SCHEMA}")
    raw = data.get("templates")
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValidationError("Docs map templates must be an object of {language: {label: text}}")
    out: dict[str, dict[str, str]] = {}
    for lang, labels in raw.items():
        where = f"templates[{lang!r}]"
        if lang not in LANGUAGES:
            raise ValidationError(f"{where}: unsupported language (supported: {', '.join(LANGUAGES)})")
        if not isinstance(labels, dict) or not labels:
            raise ValidationError(f"{where} must be a non-empty object of label -> text")
        for label, text in labels.items():
            if label not in DRAFTABLE:
                raise ValidationError(f"{where}: {label!r} is not a draftable label ({', '.join(sorted(DRAFTABLE))})")
            if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEMPLATE_CHARS:
                raise ValidationError(f"{where}[{label!r}] must be 1-{MAX_TEMPLATE_CHARS} characters of text")
            if any(unicodedata.category(ch) in ("Cc", "Cf", "Co", "Cs") and ch not in TEMPLATE_ALLOWED_FORMAT
                   for ch in text):
                raise ValidationError(f"{where}[{label!r}] contains control or bidirectional format characters")
            if "{" in text.replace("{docs}", "") or "}" in text.replace("{docs}", ""):
                raise ValidationError(f"{where}[{label!r}]: the only placeholder allowed is {{docs}}")
            out.setdefault(lang, {})[label] = text.strip()
    return out


def _docs_for(text: str, docs: list[dict]) -> dict | None:
    folded = fold(text)
    for entry in docs:
        if entry["pattern"].search(folded):
            return {"topic": entry["topic"], "url": entry["url"]}
    return None


def _thread_key(item: dict, by_id: dict) -> str:
    if item["thread_root_url"]:
        return item["thread_root_url"]
    parent = item["in_reply_to"]
    hops = 0
    current = item
    while parent and hops < 50:
        if parent in by_id:
            current = by_id[parent]
            if current["thread_root_url"]:
                return current["thread_root_url"]
            parent = current["in_reply_to"]
            hops += 1
            continue
        return parent  # External parent URL/id identifies the thread.
    return current["url"]


def _constraints(platform: str, send_support: str = "none") -> dict:
    """``send_support`` is ``capabilities.reply_send_support``: ``none`` unless the owner enabled
    the issue #17 reply adapter in the registry. Triage itself never sends either way."""
    if send_support != "none":
        how = (f"Triage never sends. To reply from ReachWeaver, write the text yourself and run "
               f"'draft --account ID --reply-to ITEM_ID --online', then show, approve and publish --live; "
               f"or post it yourself from the official {platform} app.")
    else:
        how = (f"ReachWeaver has no {platform} comment send path. Open the source URL in the official "
               f"{platform} app or website while signed in as the owner, read the full thread, edit the "
               "draft and post it yourself only if you approve it.")
    return {
        "conservative_text_limit": CONSERVATIVE_TEXT_LIMITS.get(platform, DEFAULT_REPLY_LIMIT),
        "reachweaver_send_support": send_support,
        "human_authored_only": platform in HUMAN_AUTHORED_ONLY,
        "manual_review_instructions": how,
    }


def _builtin_draft(label: str, limit: int, doc: dict | None) -> str:
    if doc:
        docs = DOCS_SENTENCE.get(label, DEFAULT_DOCS_SENTENCE).format(url=doc["url"])
    else:
        docs = NO_DOCS_SENTENCE.get(label, "")
    text = REPLY_TEMPLATES[label].format(docs=docs)
    return text if len(text) <= limit else REPLY_TEMPLATES[label].format(docs="")


def _owner_draft(template: str, label: str, doc: dict | None, with_url: bool = True) -> str:
    # Plain replace, never str.format: owner text must not reach a format mini-language.
    text = template.replace("{docs}", doc["url"] if doc and with_url else "")
    if label in NO_DOCS_SENTENCE and not doc:
        text += NO_DOCS_SENTENCE[label]
    return re.sub(r"[ \t]{2,}", " ", text).strip()


def _template_choice(label: str, guess: dict, templates: dict[str, dict[str, str]]) -> str:
    """Language whose owner template is used; 'en' unless the guess is confident and single."""
    lang = guess["language"]
    if (lang in templates and label in templates[lang] and not guess["mixed"]
            and guess["confidence"] >= LANGUAGE_CONFIDENT):
        return lang
    return "en"


def _draft_text(label: str, platform: str, doc: dict | None, guess: dict | None = None,
                templates: dict[str, dict[str, str]] | None = None) -> tuple[str, str]:
    """(draft text, template language). Owner templates first, built-in English otherwise."""
    limit = CONSERVATIVE_TEXT_LIMITS.get(platform, DEFAULT_REPLY_LIMIT)
    templates = templates or {}
    lang = _template_choice(label, guess, templates) if guess else "en"
    template = templates.get(lang, {}).get(label)
    if template:
        for with_url in (True, False):
            text = _owner_draft(template, label, doc, with_url)
            if len(text) <= limit:
                return text, lang
    return _builtin_draft(label, limit, doc), "en"


def _language_label(guess: dict) -> str:
    lang = guess["language"]
    if lang == "und":
        return "undetermined"
    text = f"{LANGUAGE_NAMES[lang]} ({lang}), heuristic confidence {guess['confidence']:.2f}"
    others = [LANGUAGE_NAMES[c] for c in guess["candidates"] if c != lang]
    return text + (f", mixed with {', '.join(others)}" if others else "")


def _language_review_note(guess: dict, template_language: str) -> str:
    note = (f"Detected language (heuristic guess, not certain): {_language_label(guess)}. "
            f"Draft template language: {template_language}. Have a native-speaker human review the original "
            "comment and this draft before posting. ReachWeaver never machine-translates.")
    lang = guess["language"]
    if template_language == "en" and lang not in ("en", "und"):
        note += (f" The comment may be in {LANGUAGE_NAMES[lang]}: reply in that language only if you or a native "
                 f"speaker write or check the reply, or add templates.{lang} to the docs map.")
    return note


def _slug(value: str) -> str:
    base = re.sub(r"[^A-Za-z0-9_-]+", "-", value).strip("-")[:40] or "item"
    return f"{base}-{hashlib.sha256(value.encode()).hexdigest()[:8]}"


def inbox_summary(triaged: list[dict]) -> dict:
    """Unified-inbox view: counts per category and per source platform, plus actions per category.

    Pure aggregation of already-triaged items. It reads nothing and sends nothing.
    """
    kinds = (*INBOX_KINDS, KIND_UNSPECIFIED)
    by_kind = {kind: sum(1 for t in triaged if t["kind"] == kind) for kind in kinds}
    platforms = sorted({t["platform"] for t in triaged})
    actions: dict[str, dict[str, int]] = {}
    for t in triaged:
        bucket = actions.setdefault(t["kind"], {})
        bucket[t["action"]] = bucket.get(t["action"], 0) + 1
    return {
        "categories": list(INBOX_KINDS),
        "by_kind": by_kind,
        "by_platform": {p: sum(1 for t in triaged if t["platform"] == p) for p in platforms},
        "actions_by_kind": {k: dict(sorted(actions[k].items())) for k in kinds if k in actions},
    }


def triage_engagement(export: object, docs_map: object = None, optout: object = None) -> dict:
    """Pure offline triage: labels, review order, reply drafts and issue drafts.

    ``optout`` is a validated entry list from ``optout.load_optout`` (or a raw
    opt-out document). A listed author never gets a reply draft or issue draft.
    """
    raw = load_export(export)
    docs = load_docs_map(docs_map)
    templates = load_templates(docs_map)
    reply_evidence = load_reply_evidence(docs_map)
    optout_entries = validate_optout(optout) if isinstance(optout, dict) else list(cast(list, optout or []))
    by_id = {item["id"]: item for item in raw}
    owner_replied_to = {item["in_reply_to"] for item in raw if item["author_is_owner"] and item["in_reply_to"]}
    owner_replied_to |= {by_id[i]["url"] for i in list(owner_replied_to) if i in by_id}
    triaged = []
    for item in raw:
        clean, flags = sanitize_text(item["text"])
        analysis = analyze(clean, flags)
        label, matched = analysis["label"], analysis["matched_rules"]
        excerpt, redacted = redact_private_data(sanitize_text(clean, EXCERPT_CHARS)[0])
        handle = sanitize_text(item["author_handle"], MAX_HANDLE_CHARS)[0]
        triaged.append({
            "id": item["id"], "platform": item["platform"], "account_id": item["account_id"],
            "author_handle": handle, "author_is_owner": item["author_is_owner"],
            "created_at": item["created_at"], "source_url": item["url"], "kind": item["kind"],
            "reach": item["reach"], "sentiment": item["sentiment"],
            "thread_root_url": item["thread_root_url"], "in_reply_to": item["in_reply_to"],
            "thread_key": _thread_key(item, by_id), "label": label, "priority": PRIORITY[label],
            "matched_rules": matched, "language_guess": analysis["language_guess"],
            "excerpt": ("[withheld: suspicious content; open source_url to review it yourself]"
                        if label == "suspicious" else excerpt),
            "flags": {**flags, "private_data_redacted": redacted, "opt_out": analysis["opt_out"],
                      "opt_out_languages": analysis["opt_out_languages"],
                      "optout_listed": listed_entry(item["author_handle"], optout_entries, item["platform"]) is not None},
            "_clean": clean,
        })
    triaged.sort(key=lambda t: (t["priority"], t["created_at"], t["id"]))
    drafted_threads: dict
    drafts, issues, drafted_threads = [], [], {}
    send_support: dict[str, str] = {}  # registry read once per platform per run
    for item in triaged:
        label = item["label"]
        if item["author_is_owner"]:
            action = "skip_owner_comment"
        elif item["flags"]["optout_listed"]:
            action = "optout_listed_no_contact"
        elif label in HUMAN_ROUTE:
            action = "human_review_required_no_draft"
        elif label == "spam":
            action = "no_reply_spam"
        elif item["flags"]["opt_out"]:
            action = "opt_out_no_contact"
        elif label not in DRAFTABLE:
            action = "no_reply_suggested"
        elif item["platform"] in HUMAN_AUTHORED_ONLY:
            action = "human_authored_reply_only"
        elif item["id"] in owner_replied_to or item["source_url"] in owner_replied_to:
            action = "owner_already_replied"
        elif item["thread_key"] in drafted_threads:
            action = "duplicate_thread_draft_exists"
            item["thread_draft_id"] = drafted_threads[item["thread_key"]]
        else:
            action = "draft_reply_requires_approval"
        item["action"] = action
        if item["platform"] not in send_support:
            send_support[item["platform"]] = (_reply_send_support(item["platform"])
                                              if item["platform"] in ("bluesky", "mastodon") else "none")
        item["constraints"] = _constraints(item["platform"], send_support[item["platform"]])
        doc = _docs_for(item["_clean"], docs) if label in DRAFTABLE else None
        if action == "draft_reply_requires_approval":
            draft_id = "draft-" + hashlib.sha256(f"{item['id']}|{item['thread_key']}".encode()).hexdigest()[:12]
            drafted_threads[item["thread_key"]] = draft_id
            guess = item["language_guess"]
            text, template_language = _draft_text(label, item["platform"], doc, guess, templates)
            drafts.append({
                "draft_id": draft_id, "status": DRAFT_STATUS, "item_id": item["id"],
                "platform": item["platform"], "account_id": item["account_id"],
                "reply_to_url": item["source_url"], "thread_key": item["thread_key"], "label": label,
                "text": text, "template_language": template_language,
                "language_guess": {key: guess[key] for key in ("language", "confidence", "candidates", "mixed")},
                "native_speaker_review_recommended": True, "machine_translated": False,
                "language_review_note": _language_review_note(guess, template_language),
                "original_excerpt": item["excerpt"],
                "evidence": {"matched_rules": item["matched_rules"], "docs_link": doc},
                "constraints": item["constraints"],
                "approval": {"approved": False, "approved_by": None,
                             "note": "Human must review, edit and post manually; no send path exists."},
            })
        if (label in ISSUE_LABELS and not item["author_is_owner"] and not item["flags"]["opt_out"]
                and not item["flags"]["optout_listed"]):
            issues.append(_issue_draft(item, doc))
    for item in triaged:
        del item["_clean"]
    claims_summary = check_reply_drafts(drafts, reply_evidence)
    counts = {label: sum(1 for t in triaged if t["label"] == label) for label in LABELS}
    guessed = [t["language_guess"]["language"] for t in triaged]
    return {
        "schema_version": REPORT_SCHEMA, "status": "triage_only_no_send",
        "counts": counts, "inbox": inbox_summary(triaged), "items": triaged, "drafts": drafts, "issue_drafts": issues,
        "claims_check": claims_summary,
        "languages": {
            "supported_packs": list(LANGUAGES), "method": LANGUAGE_METHOD,
            "guesses": {lang: guessed.count(lang) for lang in (*LANGUAGES, "und") if lang in guessed},
            "owner_templates": sorted(templates),
            "note": ("language_guess is a heuristic, never a certainty. Safety and opt-out rules run in every "
                     "language; reply templates stay English unless you supply templates.<lang> in the docs "
                     "map. Nothing is machine-translated; have a native speaker review non-English threads."),
        },
        "optout": {
            "list_entries": len(optout_entries),
            "listed_authors_skipped": sum(1 for t in triaged if t["action"] == "optout_listed_no_contact"),
            "requests_to_record": [t["id"] for t in triaged
                                   if t["flags"]["opt_out"] and not t["flags"]["optout_listed"]
                                   and not t["author_is_owner"]],
            "note": ("Items in requests_to_record asked not to be contacted. Review each and, if confirmed, add "
                     "the author's handle to config/optout.json (docs/PRIVACY-LIFECYCLE.md#opt-out-list)."),
        },
        "policy": {
            "network_access": NETWORK_ACCESS, "send_path": SEND_PATH, "auto_reply": AUTO_REPLY,
            "prohibited": list(PROHIBITED_ACTIONS),
            "note": ("Labels are keyword heuristics, not moderation decisions. Harassment and suspicious "
                     "items go to a human. Drafts never quote comment text and are not approval."),
        },
    }


def _quote(text: str) -> str:
    safe = text.replace("`", "'").replace("<", "&lt;").replace(">", "&gt;")
    safe = re.sub(r"(?<![\w`])@([A-Za-z0-9_][\w.-]*)", r"`@\1`", safe)  # never ping accounts
    return "\n".join("> " + line for line in safe.splitlines() or [""])


def _issue_draft(item: dict, doc: dict | None) -> dict:
    kind = ISSUE_LABELS[item["label"]]
    first = item["excerpt"].splitlines()[0] if item["excerpt"] else ""
    first = re.sub(r"[`<>\[\]#@]", "", first).strip()
    title = f"[{kind}] " + (first[:72].rstrip() + ("…" if len(first) > 72 else "") if first else f"{item['platform']} comment")
    rules = ", ".join(m["rule"] for m in item["matched_rules"])
    body = "\n".join([
        f"# {title}", "",
        "> **Draft only, not filed.** Review, remove any private data and file it yourself only after "
        "confirming. ReachWeaver never calls `gh` or any issue API.", "",
        "## Source", "",
        f"- Platform: {item['platform']} (account `{item['account_id']}`)",
        f"- Comment: {item['source_url']}",
        f"- Thread: {item['thread_root_url'] or item['thread_key']}",
        f"- Created at: {item['created_at']}",
        f"- Triage rules: {rules}",
        f"- Language guess (heuristic, not certain): {_language_label(item['language_guess'])}",
        *( [f"- Related docs: {doc['url']}"] if doc else [] ), "",
        "## Reported text (sanitized excerpt, untrusted)", "",
        _quote(item["excerpt"]), "",
        "## Maintainer checklist", "",
        "- [ ] Reproduce or confirm the request against the current release",
        "- [ ] Confirm the reporter is fine with a public issue linking their comment",
        "- [ ] Edit the title/body, then file manually", "",
    ])
    return {"item_id": item["id"], "label": item["label"], "title": title,
            "filename": f"issue-{_slug(item['id'])}.md", "status": DRAFT_STATUS, "body": body}


def render_markdown(report: dict) -> str:
    """Human review sheet. Excerpts are sanitized, redacted and shown as quotes."""
    lines = ["# Engagement triage (drafts require approval)", "",
             "No reply was sent. ReachWeaver has no send path for comments; post manually after review.", "",
             "| Label | Count |", "|---|---|"]
    lines += [f"| {label} | {count} |" for label, count in report["counts"].items() if count]
    inbox = report.get("inbox", {}).get("by_kind", {})
    if any(inbox.values()):
        lines += ["", "| Inbox category | Count |", "|---|---|"]
        lines += [f"| {kind} | {count} |" for kind, count in inbox.items() if count]
    lines += ["", "## Review queue", ""]
    for item in report["items"]:
        lines += [f"### P{item['priority']} {item['label']}: {item['id']}", "",
                  f"- Source: {item['source_url']}", f"- Category: {item['kind']}", f"- Action: `{item['action']}`",
                  f"- Rules: {', '.join(m['rule'] for m in item['matched_rules'])}",
                  f"- Language guess: {_language_label(item['language_guess'])}", "",
                  _quote(item["excerpt"]), ""]
    lines += ["## Reply drafts", ""]
    for draft in report["drafts"]:
        lines += [f"### {draft['draft_id']} ({draft['status']})", "",
                  f"- Reply to: {draft['reply_to_url']}",
                  f"- Limit: {draft['constraints']['conservative_text_limit']} characters",
                  f"- Template language: {draft['template_language']}",
                  f"- {draft['language_review_note']}",
                  f"- Claims check (lexical, not truth): {draft['claims_check']['status']}"]
        lines += [f"  - {f['severity']} {f['code']}: {f['message']}" for f in draft["claims_check"]["findings"]]
        lines += ["", "```text", draft["text"], "```", ""]
    return "\n".join(lines)


def triage_files(input_path: Path, docs_map: Path | None = None, out: Path | None = None,
                 issues_dir: Path | None = None, optout_path: Path | None = None) -> tuple[dict, list[str]]:
    """Read local JSON files, triage, and write NEW local draft files (never overwrite)."""
    def read(path: Path) -> object:
        try:
            return json.loads(Path(path).read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValidationError(f"Invalid JSON in {path}: {exc.msg}") from exc
    report = triage_engagement(read(input_path), read(docs_map) if docs_map else None,
                               load_optout(optout_path))
    targets: list[tuple[Path, str]] = []
    if out:
        content = render_markdown(report) if out.suffix.lower() == ".md" else json.dumps(report, indent=2, ensure_ascii=False)
        targets.append((out, content + "\n"))
    if issues_dir:
        targets += [(issues_dir / issue["filename"], issue["body"]) for issue in report["issue_drafts"]]
    existing = [str(path) for path, _ in targets if path.exists()]
    if existing:
        raise ValidationError(f"Refusing to overwrite existing draft files: {', '.join(existing)}")
    if issues_dir:
        issues_dir.mkdir(parents=True, exist_ok=True)
    for path, content in targets:
        with open(path, "x", encoding="utf-8") as handle:
            handle.write(content)
    return report, [str(path) for path, _ in targets]
