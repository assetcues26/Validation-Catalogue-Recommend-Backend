"""Shared helpers."""

import logging
import re
import unicodedata

from asset_validator.core.config import PHASE2_IMAGE_ANALYSIS_MAX_CHARS


def to_int_or_none(value) -> int | None:
    """Safely convert messy strings to int or None."""
    if value is None:
        return None
    if isinstance(value, int):
        return value
    try:
        s = str(value).strip()
        cleaned = re.sub(r"[^0-9\-]+", "", s)
        if cleaned in ("", "-"):
            return None
        return int(cleaned)
    except Exception:
        return None


def truncate_for_phase2(text: str, *, max_chars: int | None = None) -> str:
    """Truncate vision analysis text for downstream Phase 2 / Pinecone prompts."""
    value = str(text or "").strip()
    limit = PHASE2_IMAGE_ANALYSIS_MAX_CHARS if max_chars is None else max_chars
    if limit <= 0 or len(value) <= limit:
        return value
    return value[:limit].rstrip() + "…"


def normalize_user_description(description) -> str:
    desc = str(description or "").strip()
    if desc.upper() in ("N/A", "NA", "NULL", "NONE", ""):
        return ""
    return desc


# A name that says nothing about the asset (docs 5.7): every word of it, and of the description, is one of these;
# digits and punctuation are ignored. "Asset 9799" / "Description of Asset 9799" (AssetId 316770, QA customer 1004,
# 2026-11-01) passed the name check at 95 on a MacBook photo: the name check never sees the category, and the model
# compared the photo with itself. Closed and matched word for word, so shorthand keeps its letters and is never a
# placeholder: "A/C", "T.V.", "U.P.S.", "P-101A", "5T", "TV", "HP". "ref" is left out: "REF 190" is a refrigerator.
_PLACEHOLDER_NAME_WORDS = frozenset({
    "asset", "assets", "item", "items", "description", "desc", "of", "the", "no", "nos", "number", "num",
    "sr", "sl", "srno", "slno", "id", "code", "test", "dummy", "sample", "na", "nil", "null", "none",
    "unknown", "tbd", "misc", "untitled",
})


def _letter_runs(text) -> list[str]:
    """Runs of letters, casefolded. Combining marks stay inside a run, so the Hindi "कुर्सी" (chair) is one word,
    not three letters stripped of their vowel signs."""
    runs: list[str] = []
    current: list[str] = []
    for ch in str(text or "").casefold():
        if unicodedata.category(ch)[0] in ("L", "M"):
            current.append(ch)
        elif current:
            runs.append("".join(current))
            current = []
    if current:
        runs.append("".join(current))
    return runs


def is_placeholder_name(name, description) -> bool:
    """True when the name is non-empty and neither it nor the description says what the asset is (docs 5.7).

    "Asset 9799" and "Item 12" with a blank or filler description are placeholders. A description that names the
    asset ("Asset 12" / "Dell Latitude laptop") leaves the record to the name check, and so does a name with any
    other word, in any script. A name with no letters at all ("333", "1000970405") is left to the name check too:
    a number can be a plant code, which 5.6 never counts as evidence of a mismatch, and the owner approved failing
    filler words, not numbers (review, 2 Nov 2026).
    """
    if not str(name or "").strip():
        return False
    name_words = _letter_runs(name)
    if not name_words:
        return False
    words = name_words + _letter_runs(normalize_user_description(description))
    return all(word in _PLACEHOLDER_NAME_WORDS for word in words)


# Model-written text ("Jost's semi-electric stacker") carries punctuation that breaks a
# consumer which inlines strings into SQL rather than using parameterized queries: an ASCII
# apostrophe terminates the quoted literal. Fold the apostrophe family to a backtick, and the
# other non-ASCII punctuation Gemini emits down to ASCII so non-UTF8 columns cannot fail either.
_DB_SAFE_TEXT_MAP = {
    "'": "`",    # '  straight apostrophe — the character that breaks concatenated SQL
    "‘": "`",    # '  left single quote
    "’": "`",    # '  right single quote / typographic apostrophe
    "ʼ": "`",    # ʼ  modifier letter apostrophe
    "´": "`",    # ´  acute accent used as an apostrophe
    "′": "`",    # ′  prime
    "\\": "/",   # \  backslash — the other character that mangles a MySQL string literal
    "“": '"',    # "  left double quote
    "”": '"',    # "  right double quote
    "–": "-",    # –  en dash
    "—": "-",    # —  em dash
    "…": "...",  # …  ellipsis
    " ": " ",    #    non-breaking space
}
_DB_SAFE_TEXT_TABLE = str.maketrans(_DB_SAFE_TEXT_MAP)


# Civil / infrastructure records need ~2,100 tokens of extra name-match rules (the tag sits on
# a mounting post, the asset IS the surrounding structure, room signage identifies it). Those
# rules are useless on the ~99% of records that are ordinary equipment, so they are loaded only
# when the record looks like a structure. Bias toward INCLUDING: a false positive costs tokens,
# a false negative costs a wrong reject.
_STRUCTURE_TERMS = (
    # unambiguous structure nouns
    r"shed", r"godown", r"warehouse", r"canopy", r"mezzanine", r"staircase", r"culvert",
    r"drainage", r"fencing", r"landscaping", r"plinth", r"pathway", r"footpath", r"road",
    r"building", r"godown", r"porch", r"verandah", r"parapet", r"paving", r"kerb",
    # ambiguous alone — require the qualifying word
    r"compound wall", r"boundary wall", r"retaining wall", r"partition wall",
    r"pump house", r"guard room", r"security cabin", r"control room", r"server room",
    r"mcc room", r"dg room", r"toilet block", r"civil work", r"land and building",
    r"storm water", r"parking area", r"main gate", r"entrance gate", r"tank farm",
    r"civil structure", r"factory building", r"plant building",
)
_STRUCTURE_RE = re.compile(
    r"\b(?:" + "|".join(_STRUCTURE_TERMS) + r")\b", re.IGNORECASE
)
_STRUCTURE_FIELDS = (
    "assetname", "description", "categoryname", "subcategoryname",
    "assetclassname", "makemodel", "makemodelname",
)


def is_structure_or_infrastructure_asset(req_body: dict) -> bool:
    """True when the record is a building, room, road or other civil work.

    Deliberately excludes bare "wall", "block" and "gate": those appear in wall fan, engine
    block and gate valve, which are ordinary equipment.
    """
    blob = " ".join(
        str(req_body.get(field) or "").strip() for field in _STRUCTURE_FIELDS
    ).strip()
    return bool(blob) and bool(_STRUCTURE_RE.search(blob))


# In-pit / in-ground vessels get an extra phase-1 identification rule (prompts/md/
# vision_in_ground_vessel.md): only the vessel's top is visible, so vision must name the tank,
# not the pump or pipework beside it. AssetId 2441535, "2.3 kl PPFRP Rectangular Tank" installed
# in a pit, was reported as "industrial piping and pump assembly" and failed 5/5.
#
# The rule is injected ONLY for these records. Added to the shared prompt for everyone, it moved
# an unrelated record: a Samsung 65" professional display (suite S01) went from 10/10 passing to
# 3/10, because phase 1 started calling it a television. So the trigger is deliberately NARROW —
# the vessel must be the asset, not a qualifier. "Sump Pump" and "Tank Cleaning Machine" name a
# pump and a machine. A missed trigger keeps today's behaviour; a wrong one changes the prompt.
_IN_GROUND_VESSEL_HEADS = frozenset({"tank", "tanks", "sump", "sumps", "pit", "pits"})
_IN_GROUND_VESSEL_PHRASES = re.compile(
    r"\b(?:tank\s+in\s+tank|septic\s+tank|soak\s+pit|underground\s+(?:tank|sump|vessel)"
    r"|in[\s-]ground\s+(?:tank|sump|vessel))s?\b",
    re.IGNORECASE,
)


def _head_word(text: str) -> str:
    """Last alphabetic word, ignoring anything in brackets: "Storage Tank (5 KL)" -> "tank"."""
    words = re.findall(r"[A-Za-z]+", re.sub(r"\([^)]*\)", " ", str(text or "")))
    return words[-1].lower() if words else ""


def names_in_ground_vessel(*fields: str) -> bool:
    """True when a declared field names a tank, sump or pit as THE asset."""
    for field in fields:
        text = str(field or "").strip()
        if not text:
            continue
        if _head_word(text) in _IN_GROUND_VESSEL_HEADS or _IN_GROUND_VESSEL_PHRASES.search(text):
            return True
    return False


# Waste bins are registered by the bin but photographed in whatever holds them. Customer 1158's
# "STEEL DUST BIN SWING 8X24" bins are built into a wooden cabinet, so phase 1 calls the unit a
# "Cabinet" and cost prices the cabinet. Only a waste bin counts: bare "bin" is a storage bin as
# often as not ("Store Bin Trolley", "Bin tray for bottle handling", "NILKAMAL BINS").
_WASTE_BIN_NAME_RE = re.compile(
    r"\b(?:dust\s?-?bins?|waste\s?(?:paper\s?)?(?:bins?|baskets?|receptacles?)"
    r"|trash\s?(?:cans?|bins?)|garbage\s?(?:bins?|cans?)|litter\s?bins?|recycl\w*\s?bins?"
    r"|(?:swing|pedal|flap|segregation)\s?(?:type\s?)?bins?|waste\s?bin\s?flap)\b",
    re.IGNORECASE,
)


def names_waste_bin(*fields: str) -> bool:
    """True when a declared field names a waste / dust bin as the asset."""
    return any(_WASTE_BIN_NAME_RE.search(str(field or "")) for field in fields)


# Industrial records: plant and machinery, and civil structures. Their equipment is often
# specialist and photographed in part, so the photo model can fail to recognise it: customer
# 1299's smelter "Air slide system for Pot" was described as "pipes and conduits" or "a white
# metal enclosure" by gemini-2.5-flash-lite and rejected on 13 of 22 records, where
# gemini-3.1-flash-lite named it "Air slide system" and rejected 1.
_INDUSTRIAL_CLASS_RE = re.compile(r"\b(?:plant|machinery|machines?|industrial)\b", re.IGNORECASE)


def is_industrial_record(req_body: dict) -> bool:
    """True for a plant & machinery record or a civil structure."""
    body = req_body or {}
    classes = " ".join(str(body.get(f) or "") for f in ("categoryname", "assetclassname"))
    return bool(_INDUSTRIAL_CLASS_RE.search(classes)) or is_structure_or_infrastructure_asset(body)


# Installed industrial plant: equipment built into a process line or capital works, never seen
# whole in one frame and never bought off a shelf. Photographed as a duct, an enclosure, a
# support or a section of pipework, with the tag on whatever panel or member was reachable.
# Deliberately NOT bare "system" or "assembly": "100 KVA UPS SYSTEM", "Assembly Tool" and "Track &
# Trace System" in the logs are single machines or tooling.
_INSTALLED_PLANT_TERMS = (
    # Aluminium smelter pot line (customer 1299)
    "air slide", "pot shell", "anode suspension", "anode assembly", "anode beam", "potline",
    "pot line", "pot room", "potroom", "smelter", "busbar", "bus bar", "fume treatment",
    "gas treatment", "cathode assembly",
    # Capital works in a plant (customer 1009 registers them this way)
    "mechanical items", "mechanical item", "mechanical work", "mechanical works",
    "electrical work", "piping items", "piping item", "pipeline", "pipe rack", "upgradation",
    "capacity enhancement", "plant phase", "erection", "commissioning",
    # Plant structures
    "steel structure", "structural steel", "structural work", "gantry", "trestle",
)
_INSTALLED_PLANT_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(t).replace(r"\ ", r"\s*") for t in _INSTALLED_PLANT_TERMS) + r")s?\b",
    re.IGNORECASE,
)
_INSTALLED_PLANT_CLASS_FIELDS = ("categoryname", "subcategoryname", "assetclassname", "makemodel", "makemodelname")


def installed_plant_match(text: str) -> bool:
    """True when the text names installed industrial plant (see _INSTALLED_PLANT_TERMS)."""
    return bool(_INSTALLED_PLANT_RE.search(str(text or "")))


def is_installed_plant(req_body: dict, *, include_name: bool = True) -> bool:
    """True when the record is installed industrial plant.

    With include_name=False only the master-data classification fields count — the cost policy
    matches those alone, to keep a free-text name from widening it.
    """
    body = req_body or {}
    fields = _INSTALLED_PLANT_CLASS_FIELDS + (("assetname", "description") if include_name else ())
    return installed_plant_match(" ".join(str(body.get(f) or "") for f in fields))


# How a record is classed for the rules that apply to everyday assets only (docs 6.7): by the customer's own category
# and asset class, read here and never judged (6.6). Industrial wins, with no IT or furniture exception:
# industrial_decision.in_scope's IT test reads "tablets" and "switch", so it would class a pharma tablet press or a
# switch gear panel filed under plant and machinery as IT. "P&M" is plant and machinery written short, which the word
# test of is_industrial_record misses (170 logged requests of 15-29 Oct); it is tested here only, so that word test
# and its other users do not change.
_PLANT_ABBREVIATION_RE = re.compile(r"\bp\s*(?:&|and)\s*m\b", re.IGNORECASE)
# Everyday classes (owner, 2 Nov 2026): IT, furniture, office equipment and appliances, with EI&E (customer 1063's
# batteries and UPS) and Admin (fans, ACs, phones), with Flipkart's CP classes (below). Whole words and exact abbreviations, in any case, as customers
# write them: "I.T. Equipments", "IT-Laptop", "FF-Chair", "Furnitures and Fixtures", "Origins Operations (IT)". "IT"
# is not inside "Unit", "FF" is not inside "Office", and "Non IT" (a master-data category) names no IT.
_EVERYDAY_CLASS_RE = re.compile(
    r"(?<!\bnon[\s-])\b(?:it|i\.t|computers?|laptops?|electronics?|telecom|furnitures?|fixtures?|fittings?|ff|f&f"
    r"|oe|office\s+equipments?|appliances?|ei&e|admin)\b",
    re.IGNORECASE,
)
# Flipkart's own class names (customer 1063, owner 3 Nov 2026): "CP", "CP-ENG" and "CP-APST" hold its scanners, desktops,
# printers, laptops, routers, switches, monitors and TVs. Matched as the whole category or asset class, so "CP" inside
# another name ("CP Plus", a CCTV brand) never counts. Master data of 1 Nov 2026: no other customer uses these names.
_CUSTOMER_EVERYDAY_CLASSES = frozenset({"cp", "cp-eng", "cp-apst"})
# Heavy plant equipment the record itself names is industrial whatever it is filed under (owner, 2 Nov 2026; docs
# 6.7): customer 1214 files pumps, panels, valves and dampers under Admin, customer 1312 cars under Admin Assets, and DG
# sets sit under EI&E, Admin and Office Equipment. Whole words, plurals included. "DG" before a hyphenated code is a
# model number (Digisol's "DG-BG4011N" router); a distribution board is written out or "DB" with up to four letters
# before it ("LDB", "UPSDB"), never a decibel figure ("120 dB").
_HEAVY_EQUIPMENT_RE = re.compile(
    r"\b(?:dg(?!-[a-z])|d\.\s?g|gen[\s-]*sets?|transformers?|pump(?:s|sets?)?|motors?|compressors?|blowers?|valves?"
    r"|dampers?|capacitor[\s-]*banks?|hvac|f?ahus?|air[\s-]*handling[\s-]*units?|chillers?|cooling[\s-]*towers?"
    r"|heat[\s-]*exchangers?|boilers?|furnaces?|cranes?|hoists?|chain[\s-]*(?:pulleys?|blocks?)|winch(?:es)?"
    r"|conveyors?|lifts?|elevators?|escalators?|switch[\s-]*gears?|vfds?|distribution[\s-]*boards?"
    r"|(?<!\d\s)[a-z]{0,4}db"
    r"|vehicles?|cars?|trucks?|bus|buses|vans?|jeeps?|tractors?|trailers?|tankers?|ambulances?|tempos?|jcbs?"
    r"|excavators?|fork[\s-]*lifts?|motor[\s-]*(?:cycles?|bikes?)|bikes?|scooters?|e?[\s-]*rickshaws?|buggy|buggies"
    r"|(?:two|three|four|2|3|4|6)[\s-]*wheelers?)\b",
    re.IGNORECASE,
)
# "Panel" and "generator" alone name a display, a light or a laboratory instrument as often, so they count only beside
# a word that makes them plant, anywhere in the same field: a panel's voltage, job or load ("Main LT Panel", "UPS
# Output Panel", "Exhaust Fan Starter Panel"), a generator's fuel, output or rating ("Diesel Generator", "Pure Steam
# Generator", "125 kVA Generator"). "Patch Panel", "Touch Panel", "Fire Alarm Panel" and an RF generator name none, and
# a panel the field calls LED or solar before any "for" or "with" is a light fitting or a solar module ("LED Lighting
# Panel"; a "Power Panel for LED lights" is a panel).
_PANEL_RE = re.compile(r"\bpann?els?\b", re.IGNORECASE)
_PANEL_QUALIFIER_RE = re.compile(
    r"\b(?:lt|ht|mv|lv|hv|electrical|electric|elect|power|mains?|distribution|control|controller|incom(?:er|ing)"
    r"|outgoing|out\s?put|input|o/g|o/p|i/p|mcc|pcc|apfcr?|amf|ats|asts|acb|vcb|vfd|plc|hmi|bms|ddc|ems|relay|rly"
    r"|protection|metering|synch?|synchroni[sz]ing|change\s?over|starter|moter|feeder|breaker|bracker|isolat[eo]r"
    r"|capacitor|thyristor|switching|lighting|sfu|ups|charger|inverter|stabili[sz]er|fan|automation|auxil?i?ary"
    r"|sequen\w*)\b",
    re.IGNORECASE,
)
_LED_OR_SOLAR_RE = re.compile(r"\b(?:led|solar)\b", re.IGNORECASE)
_GENERATOR_RE = re.compile(r"\bgenerators?\b", re.IGNORECASE)
_GENERATOR_QUALIFIER_RE = re.compile(
    r"\b(?:diesel|dg|gas|petrol|power|sets?|silent|portable|standby|steam|hot\s?water|nitrogen|oxygen|hydrogen|h2)\b"
    r"|(?<![a-z])(?:kva|kw)\b",
    re.IGNORECASE,
)
# A UPS or a battery is not heavy equipment (owner: the maker check's Exide, AMITHI and Numeric-Vertiv catches). The
# thing a field names is the last of these words before any "for" or "with": a "DG Battery" and an "Exide battery for
# DG set" are batteries and a "UPS with Internal Battery" is a UPS, but a "UPS Output Panel", a "Battery Charger Panel"
# and a "Reach Truck With Battery Charger" are the panel and the truck.
_UPS_OR_BATTERY_RE = re.compile(r"\b(?:ups|batter(?:y|ies))(?![a-z])", re.IGNORECASE)
_FOR_OR_WITH_RE = re.compile(r"\b(?:for|with)\b", re.IGNORECASE)
_HEAVY_EQUIPMENT_FIELDS = ("subcategoryname", "makemodel", "makemodelname", "assetname")


def _heavy_equipment_ends(text: str) -> list[int]:
    """Where each heavy-equipment word in `text` ends (_HEAVY_EQUIPMENT_RE, a panel or generator with its qualifier)."""
    ends = [m.end() for m in _HEAVY_EQUIPMENT_RE.finditer(text)]
    if _PANEL_QUALIFIER_RE.search(text) and not _LED_OR_SOLAR_RE.search(_FOR_OR_WITH_RE.split(text, maxsplit=1)[0]):
        ends += [m.end() for m in _PANEL_RE.finditer(text)]
    if _GENERATOR_QUALIFIER_RE.search(text):
        ends += [m.end() for m in _GENERATOR_RE.finditer(text)]
    return ends


def _names_ups_or_battery(text: str) -> bool:
    """True when the last UPS, battery or heavy-equipment word before any "for" or "with" is a UPS or a battery."""
    head = _FOR_OR_WITH_RE.split(text, maxsplit=1)[0]
    ups = [m.end() for m in _UPS_OR_BATTERY_RE.finditer(head)]
    return bool(ups) and max(ups) > max(_heavy_equipment_ends(head), default=-1)


def names_heavy_equipment(req_body: dict) -> bool:
    """True when the record's subcategory, make/model or asset name names heavy plant equipment and none of them names
    a UPS or a battery (docs 6.7)."""
    fields = [str(req_body.get(f) or "") for f in _HEAVY_EQUIPMENT_FIELDS]
    if any(_names_ups_or_battery(field) for field in fields):
        return False
    return any(_heavy_equipment_ends(field) for field in fields)


def asset_scope(req_body: dict) -> str:
    """The record's class: "industrial", "everyday" or "other" (docs 6.7).

    By the customer's category and asset class, except that heavy plant equipment the record names is industrial
    wherever it is filed. Never raises: should the classing fail, the record is "industrial", which every rule that
    reads the class leaves as it was.
    """
    try:
        body = req_body or {}
        classes = " ".join(str(body.get(f) or "") for f in ("categoryname", "assetclassname"))
        if (is_industrial_record(body) or is_installed_plant(body) or _PLANT_ABBREVIATION_RE.search(classes)
                or names_heavy_equipment(body)):
            return "industrial"
        if _EVERYDAY_CLASS_RE.search(classes):
            return "everyday"
        named = {re.sub(r"\s*-\s*", "-", " ".join(str(body.get(f) or "").split())).casefold()
                 for f in ("categoryname", "assetclassname")}
        return "everyday" if named & _CUSTOMER_EVERYDAY_CLASSES else "other"
    except Exception as exc:
        logging.warning(f"Record classing failed, classed industrial: {exc}")
        return "industrial"


# Symbols with an ASCII spelling (docs 2.8). The consumer's text columns take ASCII only: in the
# ten days to 2026-10-22, 36 responses carried "105.0°C", "486 M³/hr", a Sony "α7", Hindi safety
# signage read off a tank, or a Cyrillic С inside a tag number, and could not be saved.
_ASCII_SYMBOL_MAP = {
    "°": " deg", "²": "2", "³": "3", "¹": "1", "½": "1/2", "¼": "1/4", "¾": "3/4",
    "×": "x", "÷": "/", "±": "+/-", "µ": "u", "μ": "u", "Ω": "ohm", "α": "a", "β": "b",
    "Ø": "O", "ø": "o", "₹": "Rs ", "€": "EUR ", "£": "GBP ", "¥": "JPY ",
    "®": "", "™": "", "©": "", "•": "-", "·": ".", "″": '"',
}
_ASCII_SYMBOL_TABLE = str.maketrans(_ASCII_SYMBOL_MAP)
# C0 controls other than tab, newline and carriage return, plus DEL.
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
# The only characters the consumer's application accepts in a response string (owner, 2026-11-01), as its own
# validation pattern lists them. Everything else is replaced or dropped on the way out (docs 2.8).
_DISALLOWED_OUTPUT_CHARS_RE = re.compile(r"[^a-zA-Z0-9!#$()*+,\-\.=\?@\[\]\^_{ }~/&|:<>]")
_ALLOWED_CHAR_REPLACEMENTS = {
    "`": "", "'": "", '"': "",   # quote marks around names and apostrophes add nothing a reader needs
    ";": ",",                    # keeps the clause break
    "%": " percent",             # "85%" reads "85 percent"
    "\n": " ", "\r": " ", "\t": " ",
}
_SPACE_RUN_RE = re.compile(r" {2,}")


def to_db_safe_text(value: str) -> str:
    """Fold a response string to plain ASCII the consumer's database can store (docs 2.8).

    Punctuation first (apostrophes to backticks, curly quotes, dashes), then symbols with an
    ASCII spelling, then accents are stripped from letters (NFKD) and whatever is still outside
    ASCII — another script, an emoji — is dropped. Control characters become a space. Never
    raises: the fallback for an unknown character is to drop it.
    """
    from asset_validator.core.config import OUTPUT_ALLOWED_CHARACTERS_ENABLED

    text = value.translate(_DB_SAFE_TEXT_TABLE)
    if not text.isascii():
        text = text.translate(_ASCII_SYMBOL_TABLE)
        if not text.isascii():
            text = "".join(ch for ch in unicodedata.normalize("NFKD", text) if ch.isascii())
    text = _CONTROL_CHARS_RE.sub(" ", text)
    if OUTPUT_ALLOWED_CHARACTERS_ENABLED and _DISALLOWED_OUTPUT_CHARS_RE.search(text):
        # The consumer accepts only its listed characters (owner, 2026-11-01). Replacements can leave double
        # spaces; only those are folded, so text without a replaced character is returned exactly as it was.
        text = _DISALLOWED_OUTPUT_CHARS_RE.sub(lambda m: _ALLOWED_CHAR_REPLACEMENTS.get(m.group(0), ""), text)
        text = _SPACE_RUN_RE.sub(" ", text)
    return text


def sanitize_output_text(value):
    """Recursively apply to_db_safe_text to every string in a response payload.

    Walks nested dicts and lists (condition, damage_items, barcodeposition). Dict keys are
    fixed schema names, so they are left untouched; non-string values pass through unchanged.
    """
    if isinstance(value, str):
        return to_db_safe_text(value)
    if isinstance(value, dict):
        return {key: sanitize_output_text(item) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize_output_text(item) for item in value]
    return value
