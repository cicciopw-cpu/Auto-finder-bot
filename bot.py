import os
import re
import json
import time
import hashlib
import datetime
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup


BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

STATE_FILE = "state.json"
MODELS_FILE = "models_db.json"
SOURCES_FILE = "sources.json"

MIN_PRICE = 5000
MAX_PRICE = 9000
MIN_YEAR = 2024
MAX_LENGTH_MM = 4000

MAX_SEEN_ITEMS = 1500
MAX_RECENT_MATCHES = 50
REQUEST_TIMEOUT = 45

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36 AutoFinderBot/1.0"
)

AUTOMATIC_WORDS = [
    "automatico", "automatica", "automatic", "auto.", "cambio aut", "cambio automatico",
    "cvt", "dct", "edc", "eat8", "dsg", "steptronic", "powershift", "robotizzato",
    "dualogic", "mta", "selezione automatica"
]

MANUAL_WORDS = [
    "manuale", "cambio manuale", "manual", "5 marce", "6 marce"
]

PREFERRED_FUEL_WORDS = [
    "ibrida", "ibrido", "hybrid", "mhev", "hev", "elettrica", "elettrico", "electric", "bev"
]

ELECTRIC_WORDS = ["elettrica", "elettrico", "electric", "bev", "ev", "full electric"]

BAD_WORDS = [
    "noleggio", "leasing", "anticipo", "rata", "mensile", "finanziamento obbligatorio",
    "solo finanziamento", "promo con finanziamento", "prezzo iva esclusa", "iva esclusa",
    "incidentata", "sinistrata", "per ricambi", "motore rotto", "non marciante"
]

FAMILY_WORDS = ["5 porte", "cinque porte", "isofix", "clima", "climatizzatore", "garanzia", "unico proprietario"]

LINK_HINTS = [
    "/annunci/", "/annuncio/", "/auto/", "/usate/", "/used/", "/detail", "/details", "/scheda", "/veicolo"
]


def now_italy():
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=2)))


def now_string():
    return now_italy().strftime("%d/%m/%Y %H:%M")


def today_string():
    return now_italy().strftime("%Y-%m-%d")


def load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def text_hash(value):
    return hashlib.sha256(str(value).encode("utf-8", errors="ignore")).hexdigest()


def normalize_space(text):
    return re.sub(r"\s+", " ", str(text or "")).strip()


def normalize_text(text):
    text = str(text or "").lower()
    replacements = {
        "à": "a", "è": "e", "é": "e", "ì": "i", "ò": "o", "ù": "u",
        "€": " euro ", "–": "-", "—": "-"
    }
    for a, b in replacements.items():
        text = text.replace(a, b)
    return normalize_space(text)


def format_euro(value):
    try:
        return f"{int(value):,}".replace(",", ".") + " €"
    except Exception:
        return str(value)


def send_telegram(message, chat_id=None):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id or CHAT_ID,
        "text": message[:3900],
        "disable_web_page_preview": False,
    }
    response = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()


def request_html(url):
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "it-IT,it;q=0.9,en;q=0.8",
        "Cache-Control": "no-cache",
    }
    response = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return response.text, response.url


def absolute_url(base_url, href):
    if not href:
        return ""
    return urljoin(base_url, href.split("#")[0])


def same_domain(url_a, url_b):
    try:
        return urlparse(url_a).netloc.replace("www.", "") == urlparse(url_b).netloc.replace("www.", "")
    except Exception:
        return False


def extract_price(text):
    text_norm = normalize_text(text)

    # Ignore obvious monthly/rental prices.
    if any(word in text_norm for word in ["mese", "mensile", "rata", "/mese", "al mese"]):
        # Still try to find a full price, but mark cautiously by returning None if only tiny amounts exist.
        pass

    patterns = [
        r"(?:euro|eur)\s*([0-9]{1,3}(?:[\.\s][0-9]{3})+|[0-9]{4,6})",
        r"([0-9]{1,3}(?:[\.\s][0-9]{3})+|[0-9]{4,6})\s*(?:euro|eur)",
        r"€\s*([0-9]{1,3}(?:[\.\s][0-9]{3})+|[0-9]{4,6})",
        r"([0-9]{1,3}(?:[\.\s][0-9]{3})+|[0-9]{4,6})\s*€",
    ]

    candidates = []
    for pattern in patterns:
        for match in re.findall(pattern, str(text), flags=re.IGNORECASE):
            raw = re.sub(r"[^0-9]", "", match)
            if not raw:
                continue
            value = int(raw)
            if 1000 <= value <= 100000:
                candidates.append(value)

    if not candidates:
        return None

    # Prefer prices inside target range, otherwise closest realistic full price.
    target = [x for x in candidates if MIN_PRICE <= x <= MAX_PRICE]
    if target:
        return min(target)
    return min(candidates)


def extract_year(text):
    years = []
    for match in re.findall(r"\b(20[2-3][0-9])\b", str(text)):
        year = int(match)
        if 2020 <= year <= now_italy().year + 1:
            years.append(year)
    if not years:
        return None
    # In listings the vehicle year usually appears as the latest relevant recent year.
    return max(years)


def has_any(text, words):
    text_norm = normalize_text(text)
    return any(normalize_text(word) in text_norm for word in words)


def detect_transmission(text):
    text_norm = normalize_text(text)
    if has_any(text_norm, MANUAL_WORDS) and not has_any(text_norm, AUTOMATIC_WORDS):
        return "manuale"
    if has_any(text_norm, AUTOMATIC_WORDS):
        return "automatico"
    if has_any(text_norm, ELECTRIC_WORDS):
        # Most EVs do not use a manual gearbox. Keep as assumed, not as certain.
        return "automatico_probabile_ev"
    return "non_specificato"


def detect_fuel(text):
    text_norm = normalize_text(text)
    if any(w in text_norm for w in ["elettrica", "elettrico", "electric", "bev", "full electric"]):
        return "elettrica"
    if any(w in text_norm for w in ["ibrida", "ibrido", "hybrid", "mhev", "hev"]):
        return "ibrida"
    if "benzina" in text_norm:
        return "benzina"
    if "diesel" in text_norm:
        return "diesel"
    if "gpl" in text_norm:
        return "gpl"
    if "metano" in text_norm:
        return "metano"
    return "non specificato"


def build_model_alias_index(models):
    alias_index = []
    for key, model in models.items():
        aliases = model.get("aliases", []) + [model.get("name", "")]
        for alias in aliases:
            alias_norm = normalize_text(alias)
            if alias_norm:
                alias_index.append((alias_norm, key, model))
    alias_index.sort(key=lambda x: len(x[0]), reverse=True)
    return alias_index


def detect_model(text, models):
    text_norm = normalize_text(text)
    for alias_norm, key, model in build_model_alias_index(models):
        if alias_norm and alias_norm in text_norm:
            return key, model
    return None, None


def make_item_id(item):
    base = item.get("url") or f"{item.get('title')}|{item.get('price')}|{item.get('year')}"
    return text_hash(base)


def short_url(url):
    if not url:
        return ""
    return url[:250]


def evaluate_item(raw_item, models):
    title = normalize_space(raw_item.get("title") or "")
    url = raw_item.get("url") or ""
    source = raw_item.get("source") or "Fonte sconosciuta"
    raw_text = normalize_space(" ".join([
        title,
        raw_item.get("description") or "",
        raw_item.get("raw_text") or "",
        url,
    ]))

    price = raw_item.get("price") or extract_price(raw_text)
    year = raw_item.get("year") or extract_year(raw_text)
    transmission = detect_transmission(raw_text)
    fuel = raw_item.get("fuel") or detect_fuel(raw_text)
    model_key, model = detect_model(raw_text, models)

    length_mm = model.get("length_mm") if model else None
    family_score = int(model.get("family_score", 0)) if model else 0

    failures = []
    warnings = []
    score = 0

    if price is None:
        failures.append("prezzo non letto")
    elif MIN_PRICE <= int(price) <= MAX_PRICE:
        score += 30
    else:
        failures.append(f"prezzo fuori range ({format_euro(price)})")

    if year is None:
        failures.append("anno non letto")
    elif int(year) >= MIN_YEAR:
        score += 25
    else:
        failures.append(f"anno troppo vecchio ({year})")

    if transmission == "automatico":
        score += 20
    elif transmission == "automatico_probabile_ev":
        score += 14
        warnings.append("cambio automatico probabile perché elettrica, ma da verificare")
    elif transmission == "manuale":
        failures.append("cambio manuale")
    else:
        failures.append("cambio automatico non confermato")

    if not model:
        failures.append("modello non riconosciuto nel database")
    elif length_mm and int(length_mm) <= MAX_LENGTH_MM:
        score += 15
    else:
        failures.append(f"lunghezza sopra 4m o non valida ({length_mm} mm)")

    if has_any(raw_text, PREFERRED_FUEL_WORDS):
        score += 5

    if has_any(raw_text, FAMILY_WORDS):
        score += 5

    if family_score >= 8:
        score += 5
    elif family_score <= 2 and model:
        warnings.append("modello poco pratico con un bambino")

    if has_any(raw_text, BAD_WORDS):
        warnings.append("presenti parole sospette: noleggio/leasing/rata/incidentata/altro")
        score -= 15

    score = max(0, min(100, score))

    if not failures and score >= 80:
        priority = "ALTA"
    elif len(failures) <= 1 and score >= 65:
        priority = "MEDIA"
    else:
        priority = "SCARTA"

    return {
        "id": make_item_id({**raw_item, "title": title, "url": url}),
        "title": title or "Annuncio senza titolo",
        "url": url,
        "source": source,
        "price": price,
        "year": year,
        "transmission": transmission,
        "fuel": fuel,
        "model_key": model_key,
        "model_name": model.get("name") if model else None,
        "length_mm": length_mm,
        "family_score": family_score,
        "score": score,
        "priority": priority,
        "failures": failures,
        "warnings": warnings,
        "checked_at": now_string(),
        "raw_text_preview": raw_text[:1200],
    }


def alert_text(item):
    ok = "✅"
    warn = "⚠️"
    star = "⭐"
    model_line = item.get("model_name") or "Modello non riconosciuto"
    length = item.get("length_mm")
    length_line = f"{length / 1000:.2f} m" if length else "N/D"

    lines = [
        "🚗 AUTO TROVATA - POSSIBILE OCCASIONE",
        "",
        f"{star} Priorità: {item.get('priority')} | Score: {item.get('score')}/100",
        "",
        f"Modello: {model_line}",
        f"Titolo: {item.get('title')}",
        f"Anno: {item.get('year') or 'N/D'}",
        f"Prezzo: {format_euro(item.get('price')) if item.get('price') else 'N/D'}",
        f"Cambio: {item.get('transmission')}",
        f"Carburante: {item.get('fuel')}",
        f"Lunghezza: {length_line}",
        f"Fonte: {item.get('source')}",
        "",
        "Valutazione:",
    ]

    if item.get("failures"):
        for failure in item["failures"][:5]:
            lines.append(f"❌ {failure}")
    else:
        lines.extend([
            f"{ok} prezzo nel range",
            f"{ok} anno ok",
            f"{ok} automatica/probabile automatica",
            f"{ok} sotto 4 metri",
        ])

    if item.get("warnings"):
        lines.append("")
        lines.append("Da controllare:")
        for warning in item["warnings"][:5]:
            lines.append(f"{warn} {warning}")

    if item.get("url"):
        lines.extend(["", f"Link: {item.get('url')}"])

    return "\n".join(lines)


def parse_ld_json_items(html, base_url, source_name):
    soup = BeautifulSoup(html, "html.parser")
    items = []

    def walk(obj):
        if isinstance(obj, list):
            for x in obj:
                walk(x)
            return
        if not isinstance(obj, dict):
            return

        obj_type = obj.get("@type")
        types = obj_type if isinstance(obj_type, list) else [obj_type]
        types = [str(t).lower() for t in types if t]

        maybe_vehicle = any(t in ["car", "vehicle", "product", "listitem", "offer"] for t in types)
        text_blob = json.dumps(obj, ensure_ascii=False)
        has_vehicle_words = has_any(text_blob, ["auto", "vehicle", "car", "prezzo", "price", "fiat", "panda", "ypsilon"])

        if maybe_vehicle and has_vehicle_words:
            name = obj.get("name") or obj.get("title") or ""
            description = obj.get("description") or ""
            url = obj.get("url") or obj.get("@id") or ""

            offers = obj.get("offers") or {}
            price = None
            if isinstance(offers, dict):
                price = offers.get("price") or offers.get("lowPrice")
            if price:
                try:
                    price = int(float(str(price).replace(",", ".")))
                except Exception:
                    price = None

            if isinstance(obj.get("item"), dict):
                item = obj["item"]
                name = name or item.get("name") or item.get("title") or ""
                description = description or item.get("description") or ""
                url = url or item.get("url") or ""

            if name or url:
                items.append({
                    "title": normalize_space(name),
                    "description": normalize_space(description),
                    "url": absolute_url(base_url, url),
                    "source": source_name,
                    "price": price,
                    "raw_text": text_blob[:3000],
                })

        for value in obj.values():
            if isinstance(value, (dict, list)):
                walk(value)

    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.get_text(" ", strip=True)
        if not raw:
            continue
        try:
            data = json.loads(raw)
            walk(data)
        except Exception:
            continue

    return items


def parse_fallback_items(html, base_url, source_name):
    soup = BeautifulSoup(html, "html.parser")
    items = []
    seen_urls = set()

    # Remove noise.
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()

    for a in soup.find_all("a", href=True):
        href = a.get("href")
        url = absolute_url(base_url, href)
        if not url or url in seen_urls:
            continue
        if not same_domain(base_url, url):
            continue

        url_norm = url.lower()
        anchor_text = normalize_space(a.get_text(" ", strip=True))
        is_vehicle_link = any(hint in url_norm for hint in LINK_HINTS)
        has_model_hint = has_any(f"{anchor_text} {url_norm}", [
            "fiat", "panda", "500", "ypsilon", "aygo", "i10", "picanto", "swift", "ignis",
            "spring", "twingo", "smart", "fortwo", "forfour", "citroen", "c3", "ami", "topolino"
        ])

        if not is_vehicle_link and not has_model_hint:
            continue

        # Collect nearby text from parent cards.
        parent = a
        texts = [anchor_text]
        for _ in range(4):
            parent = parent.parent
            if not parent:
                break
            txt = normalize_space(parent.get_text(" ", strip=True))
            if len(txt) > 40:
                texts.append(txt)
            if len(txt) > 250:
                break

        raw_text = max(texts, key=len) if texts else anchor_text
        if len(raw_text) < 15:
            continue

        seen_urls.add(url)
        title = anchor_text if len(anchor_text) >= 8 else raw_text[:120]
        items.append({
            "title": normalize_space(title)[:180],
            "description": "",
            "url": url,
            "source": source_name,
            "raw_text": raw_text[:2500],
        })

    return items[:80]


def parse_source(source):
    url = source["url"]
    html, final_url = request_html(url)
    source_name = source.get("name", final_url)

    items = []
    items.extend(parse_ld_json_items(html, final_url, source_name))
    items.extend(parse_fallback_items(html, final_url, source_name))

    # Deduplicate parsed items by URL/title.
    unique = {}
    for item in items:
        key = item.get("url") or item.get("title")
        if key:
            unique[key] = item

    return list(unique.values()), {
        "final_url": final_url,
        "html_length": len(html),
        "items_found": len(unique),
    }


def cleanup_state(state):
    seen = state.get("_seen_items", {})
    if isinstance(seen, dict) and len(seen) > MAX_SEEN_ITEMS:
        items = list(seen.items())[-MAX_SEEN_ITEMS:]
        state["_seen_items"] = dict(items)

    recent = state.get("_recent_matches", [])
    if isinstance(recent, list) and len(recent) > MAX_RECENT_MATCHES:
        state["_recent_matches"] = recent[-MAX_RECENT_MATCHES:]


def add_recent_match(state, item):
    recent = state.get("_recent_matches", [])
    if not isinstance(recent, list):
        recent = []
    recent.append(item)
    state["_recent_matches"] = recent[-MAX_RECENT_MATCHES:]


def check_sources(state, models, sources_config):
    alerts = []
    errors = []
    stats = []
    seen = state.get("_seen_items", {})
    if not isinstance(seen, dict):
        seen = {}

    sources = sources_config.get("sources", []) if isinstance(sources_config, dict) else []
    enabled_sources = [s for s in sources if s.get("enabled", True) and s.get("url")]

    for source in enabled_sources:
        try:
            raw_items, source_stats = parse_source(source)
            stats.append({
                "name": source.get("name"),
                "url": source.get("url"),
                "last_checked": now_string(),
                **source_stats,
            })

            for raw in raw_items:
                evaluated = evaluate_item(raw, models)
                item_id = evaluated["id"]

                if item_id in seen:
                    continue

                seen[item_id] = {
                    "title": evaluated["title"],
                    "url": evaluated["url"],
                    "source": evaluated["source"],
                    "priority": evaluated["priority"],
                    "score": evaluated["score"],
                    "first_seen": now_string(),
                }

                if evaluated["priority"] in ["ALTA", "MEDIA"]:
                    add_recent_match(state, evaluated)
                    alerts.append(alert_text(evaluated))

        except Exception as e:
            errors.append(f"{source.get('name', source.get('url'))}: {str(e)}")

    state["_seen_items"] = seen
    state["_source_stats"] = stats[-30:]
    state["_source_errors"] = errors[:20]
    state["_last_check"] = now_string()
    state["_last_check_day"] = today_string()
    return alerts


def build_status_text(state):
    stats = state.get("_source_stats", [])
    errors = state.get("_source_errors", [])
    seen = state.get("_seen_items", {})
    recent = state.get("_recent_matches", [])

    lines = [
        "📊 STATUS AUTO FINDER - V1",
        "",
        f"Ora: {now_string()}",
        f"Ultimo controllo: {state.get('_last_check', 'N/D')}",
        "",
        "🎯 Filtri:",
        f"- Prezzo: {format_euro(MIN_PRICE)} - {format_euro(MAX_PRICE)}",
        f"- Anno: da {MIN_YEAR}",
        "- Cambio: automatico",
        f"- Lunghezza max: {MAX_LENGTH_MM/1000:.2f} m",
        "- Carburante: qualsiasi, preferenza ibrida/elettrica",
        "",
        "📌 Stato:",
        f"- Fonti controllate ultima run: {len(stats)}",
        f"- Annunci visti totali: {len(seen) if isinstance(seen, dict) else 0}",
        f"- Match recenti: {len(recent) if isinstance(recent, list) else 0}",
        f"- Errori fonti: {len(errors) if isinstance(errors, list) else 0}",
    ]
    return "\n".join(lines)


def build_recent_text(state):
    recent = state.get("_recent_matches", [])
    if not recent:
        return "🚗 Nessun match recente."

    lines = ["🚗 ULTIME AUTO TROVATE"]
    for item in recent[-8:][::-1]:
        lines.extend([
            "",
            f"{item.get('priority')} | {item.get('score')}/100 | {item.get('model_name') or 'N/D'}",
            f"{item.get('title')}",
            f"Anno: {item.get('year') or 'N/D'} | Prezzo: {format_euro(item.get('price')) if item.get('price') else 'N/D'}",
            f"Link: {short_url(item.get('url'))}",
        ])
    return "\n".join(lines)[:3900]


def build_sources_text(state, sources_config):
    sources = sources_config.get("sources", []) if isinstance(sources_config, dict) else []
    stats = state.get("_source_stats", [])
    stat_by_url = {s.get("url"): s for s in stats}

    lines = ["📡 FONTI MONITORATE"]
    for source in sources:
        enabled = "✅" if source.get("enabled", True) else "⛔"
        st = stat_by_url.get(source.get("url"), {})
        lines.append(
            f"\n{enabled} {source.get('name')}\n"
            f"Ultimo controllo: {st.get('last_checked', 'mai')} | item trovati: {st.get('items_found', 'N/D')}"
        )
    return "\n".join(lines)[:3900]


def build_debug_text(state):
    errors = state.get("_source_errors", [])
    stats = state.get("_source_stats", [])
    tg_error = state.get("_telegram_command_error", "")

    lines = [
        "🛠️ DEBUG AUTO FINDER - V1",
        "",
        f"Ultimo controllo: {state.get('_last_check', 'N/D')}",
        f"Telegram offset: {state.get('_telegram_update_offset', 'N/D')}",
        f"Errore Telegram: {tg_error or 'nessuno'}",
        "",
        f"Errori fonti: {len(errors)}",
    ]

    for err in errors[:8]:
        lines.append(f"- {err}")

    lines.append("")
    lines.append("Ultime statistiche fonti:")
    for st in stats[-8:]:
        lines.append(f"- {st.get('name')}: {st.get('items_found')} item | html {st.get('html_length')}")

    return "\n".join(lines)[:3900]


def build_models_text(models):
    good = [m for m in models.values() if int(m.get("length_mm", 999999)) <= MAX_LENGTH_MM]
    good.sort(key=lambda x: (int(x.get("family_score", 0)) * -1, int(x.get("length_mm", 0))))

    lines = ["🚘 MODELLI NEL DATABASE SOTTO 4 METRI"]
    for m in good[:40]:
        lines.append(f"- {m.get('name')}: {m.get('length_mm')/1000:.2f} m | famiglia {m.get('family_score')}/10")
    return "\n".join(lines)[:3900]


def get_telegram_updates(state):
    params = {"timeout": 0}
    offset = state.get("_telegram_update_offset")
    if offset is not None:
        params["offset"] = offset
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
    response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return response.json().get("result", [])


def command_name(text):
    first = text.strip().split()[0].lower()
    return first.split("@")[0]


def build_help_text():
    return (
        "🤖 Comandi Auto Finder:\n\n"
        "/status - stato bot\n"
        "/ultime - ultimi annunci buoni\n"
        "/fonti - fonti monitorate\n"
        "/modelli - modelli sotto 4 metri\n"
        "/debug - errori e dettagli tecnici\n"
        "/valuta + testo/link annuncio - valuta manualmente un annuncio\n\n"
        "Il bot controlla automaticamente le fonti impostate in sources.json."
    )


def handle_valuta(text, models):
    payload = text.split(" ", 1)[1] if " " in text else ""
    if not payload.strip():
        return "Mandami così:\n/valuta Fiat Panda 2024 automatica 8.900 euro ibrida 5 porte"

    link_match = re.search(r"https?://\S+", payload)
    raw_text = payload
    url = ""

    if link_match:
        url = link_match.group(0).strip()
        try:
            html, final_url = request_html(url)
            soup = BeautifulSoup(html, "html.parser")
            for tag in soup(["script", "style", "noscript", "svg"]):
                tag.decompose()
            page_text = normalize_space(soup.get_text(" ", strip=True))
            raw_text = f"{payload}\n{page_text[:4000]}"
            url = final_url
        except Exception as e:
            raw_text = payload + f"\n[Errore lettura link: {e}]"

    item = evaluate_item({
        "title": payload[:180],
        "url": url,
        "source": "Valutazione manuale Telegram",
        "raw_text": raw_text,
    }, models)

    return alert_text(item)


def handle_telegram_commands(state, models, sources_config):
    try:
        updates = get_telegram_updates(state)
        state["_telegram_command_error"] = ""
    except Exception as e:
        state["_telegram_command_error"] = str(e)
        return

    if not updates:
        return

    max_update_id = state.get("_telegram_update_offset", 0) - 1
    now_ts = int(datetime.datetime.now(datetime.timezone.utc).timestamp())

    for update in updates:
        update_id = update.get("update_id")
        if update_id is not None:
            max_update_id = max(max_update_id, update_id)

        message = update.get("message") or update.get("edited_message") or {}
        text = message.get("text", "")
        chat = message.get("chat", {})
        chat_id = chat.get("id")
        msg_date = message.get("date", 0)

        if str(chat_id) != str(CHAT_ID):
            continue
        if msg_date and now_ts - int(msg_date) > 3600:
            continue
        if not text.startswith("/"):
            continue

        cmd = command_name(text)
        try:
            if cmd in ["/start", "/help"]:
                send_telegram(build_help_text(), chat_id=chat_id)
            elif cmd in ["/status", "/check"]:
                send_telegram(build_status_text(state), chat_id=chat_id)
            elif cmd in ["/ultime", "/last"]:
                send_telegram(build_recent_text(state), chat_id=chat_id)
            elif cmd == "/fonti":
                send_telegram(build_sources_text(state, sources_config), chat_id=chat_id)
            elif cmd == "/debug":
                send_telegram(build_debug_text(state), chat_id=chat_id)
            elif cmd == "/modelli":
                send_telegram(build_models_text(models), chat_id=chat_id)
            elif cmd == "/valuta":
                send_telegram(handle_valuta(text, models), chat_id=chat_id)
            else:
                send_telegram("Comando non riconosciuto. Scrivi /help.", chat_id=chat_id)
        except Exception as e:
            state["_telegram_command_error"] = str(e)

    state["_telegram_update_offset"] = max_update_id + 1


def main():
    state = load_json(STATE_FILE, {})
    models = load_json(MODELS_FILE, {})
    sources_config = load_json(SOURCES_FILE, {"sources": []})

    cleanup_state(state)

    alerts = check_sources(state, models, sources_config)
    save_json(STATE_FILE, state)

    for alert in alerts[:10]:
        try:
            send_telegram(alert)
            time.sleep(0.5)
        except Exception as e:
            state["_telegram_send_error"] = str(e)

    handle_telegram_commands(state, models, sources_config)
    cleanup_state(state)
    save_json(STATE_FILE, state)


if __name__ == "__main__":
    main()
