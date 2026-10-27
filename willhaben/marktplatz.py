"""The marketplace's categories and tree attributes (condition, sizes, colours).

willhaben constrains a search to a category via the ``ATTRIBUTE_TREE`` query
parameter (the category id). Condition, clothing size and shoe size are all
carried by the *same* multi-valued ``treeAttributes`` parameter -- the ids
below were harvested from the app's own filter responses."""

import json
import logging
from pathlib import Path
from typing import Literal, Optional, Union

from mcp.server.mcpserver.exceptions import ToolError

from . import matching


Condition = Literal[
    "neu", "neuwertig", "generalueberholt", "gebraucht", "defekt", "ausstellungsstueck"
]
condition_id = {
    "neu": "22",
    "neuwertig": "2546",
    "generalueberholt": "5013256",
    "gebraucht": "23",
    "defekt": "24",
    "ausstellungsstueck": "2539",
}

# Clothing size -> treeAttributes id. Keys are willhaben's own labels.
# willhaben uses two clothing size systems depending on the item type; both are
# accepted here. Pass the one that matches the garment (numeric for most
# women's/men's wear, letters for tops/knitwear etc.):
#   * numeric / combined: "36", "38", ... "46 / S", "48 / M", ... "ab 58"
#   * pure letters:        "XS", "S", "M", "L", "XL"
clothing_size_id = {
    # numeric / combined system
    "bis 34": "5014962", "36": "5014963", "38": "5014964", "40": "5014965",
    "42": "5014966", "44": "3226", "46 / S": "3227", "48 / M": "3228",
    "50 / L": "3229", "52 / L": "3230", "54 / XL": "3231", "56 / XL": "3232",
    "ab 58": "3233",
    # pure letter system
    "bis XS": "4313", "XS": "4313", "S": "4314", "M": "4315", "L": "4316",
    "XL": "4317", "ab XL": "4317",
}
# Colour -> treeAttributes id.
color_id = {
    "mehrfarbig": "3200", "schwarz": "3201", "braun": "3202", "blau": "3203",
    "tuerkis": "3204", "gruen": "3205", "gelb": "3206", "orange": "3207",
    "rot": "3208", "rosa": "3209", "violett": "3210", "grau": "3211",
    "silber": "3212", "gold": "3213", "beige": "3214", "weiss": "3215",
}

# Pattern -> treeAttributes id.
pattern_id = {
    "einfarbig": "5010052", "mustermix": "5010064", "farbverlauf": "5010059",
    "floral": "5010053", "gepunktet": "5010058", "gestreift": "5010054",
    "nadelstreifen": "5010062", "kariert": "5010057", "fischgraet": "5010063",
    "camouflage": "5010061", "paisley": "5010060", "motivprint": "5010055",
    "animalprint": "5010056", "andere muster": "5010065",
}

Color = Literal[
    "mehrfarbig", "schwarz", "braun", "blau", "tuerkis", "gruen", "gelb",
    "orange", "rot", "rosa", "violett", "grau", "silber", "gold", "beige", "weiss",
]
Pattern = Literal[
    "einfarbig", "mustermix", "farbverlauf", "floral", "gepunktet", "gestreift",
    "nadelstreifen", "kariert", "fischgraet", "camouflage", "paisley",
    "motivprint", "animalprint", "andere muster",
]

# Shoe size -> treeAttributes id.
shoe_size_id = {
    "bis 15": "4319", "16": "5014897", "17": "5014898", "18": "5014899",
    "19": "5014900", "20": "5014901", "21": "5014902", "22": "5014903",
    "23": "5014904", "24": "5014905", "25": "5014906", "26": "5014907",
    "27": "5014908", "28": "5014909", "29": "5014910", "30": "5014911",
    "31": "5014912", "32": "5014913", "33": "5014914", "34": "5014915",
    "35": "5014916", "36": "4330", "37": "4331", "38": "4332", "39": "4333",
    "40": "4334", "41": "4335", "42": "4336", "43": "4337", "44": "4338",
    "45": "4339", "46": "4340", "47": "4341", "ab 48": "4342",
}


def _load_categories() -> list[dict]:
    """Load the crawled willhaben category tree (``[{id,label,parentId,children}]``)
    that sits next to this module. Missing file -> empty tree (search still works
    without category filtering)."""
    path = Path(__file__).parent.parent / "data" / "marktplatz" / "categories.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        logging.getLogger(__name__).warning("marktplatz categories.json not found -- category filter disabled")
        return []


CATEGORY_LABEL: dict[int, str] = {}
CATEGORY_PARENT: dict[int, Optional[int]] = {}
CATEGORY_CHILDREN: dict[Optional[int], list[int]] = {}
CATEGORY_PATH: dict[int, str] = {}
_CATEGORY_BY_NAME: dict[str, list[int]] = {}


def _index_categories(nodes: list[dict], parent: Optional[int] = None, trail: tuple = ()) -> None:
    for nd in nodes:
        cid = nd["id"]
        label = nd.get("label") or str(cid)
        CATEGORY_LABEL[cid] = label
        CATEGORY_PARENT[cid] = parent
        CATEGORY_CHILDREN.setdefault(parent, []).append(cid)
        path = trail + (label,)
        CATEGORY_PATH[cid] = " > ".join(path)
        _CATEGORY_BY_NAME.setdefault(matching.norm(label), []).append(cid)
        _index_categories(nd.get("children", []), cid, path)


_index_categories(_load_categories())


def resolve_category(category: Union[int, str]) -> str:
    """Resolve a category given as id or (unique) name to its ATTRIBUTE_TREE id."""
    if isinstance(category, int) or (isinstance(category, str) and category.strip().isdigit()):
        cid = int(str(category).strip())
        if cid not in CATEGORY_LABEL:
            raise ToolError(f"Unknown category id {cid}. Use the list_categories tool.")
        return str(cid)
    matches = _CATEGORY_BY_NAME.get(matching.norm(category), [])
    if not matches:
        # names come in any order and wording ("Handys / Smartphones" for
        # "Smartphones / Handys"): a category carrying every word is meant
        wanted = matching.tokens(category)
        matches = [cid for cid, label in CATEGORY_LABEL.items() if wanted and wanted <= matching.tokens(label)]
    if not matches:
        raise ToolError(f"Unknown category '{category}'. {_closest_categories(category)}")
    if len(matches) > 1:
        opts = ", ".join(f"{i} ({CATEGORY_PATH[i]})" for i in matches[:10])
        raise ToolError(f"Category '{category}' is ambiguous ({len(matches)} matches): {opts}. Please pass an id.")
    return str(matches[0])


def _word_overlap(wanted: set, words: set) -> int:
    """How many of `words` relate to one of `wanted`, counting a part of a
    German compound: "damenschuhe" finds both "schuhe" and "damenmode"."""
    def related(a, b):
        if a == b:
            return True
        # parts of a compound only count from four letters on, or "r" from
        # "R&B" would be found in every other word
        return len(a) >= 4 and len(b) >= 4 and (a in b or b in a or a[:5] == b[:5])
    return sum(any(related(w, word) for w in wanted) for word in words)


def _closest_categories(name: str, limit: int = 5) -> str:
    """The categories whose path shares the most words with `name`, as a hint
    for the model, so a near miss does not end in a bare "unknown"."""
    wanted = {w for w in matching.tokens(name) if len(w) >= 3}
    scored = sorted(((_word_overlap(wanted, matching.tokens(CATEGORY_PATH[cid])), cid) for cid in CATEGORY_LABEL),
                    key=lambda pair: (-pair[0], len(CATEGORY_PATH[pair[1]])))
    best = [f"{cid} ({CATEGORY_PATH[cid]})" for score, cid in scored[:limit] if score]
    if not best:
        return "Use list_categories to find the id."
    return "Closest: " + ", ".join(best) + ". Pass one of these ids."


def resolve_sizes(values, mapping: dict, kind: str) -> list[str]:
    """Map one or more size labels/ids to treeAttributes ids."""
    if values is None:
        return []
    if not isinstance(values, list):
        values = [values]
    ids_by_norm = {matching.norm(k): v for k, v in mapping.items()}
    valid_ids = set(mapping.values())
    out = []
    for v in values:
        s = str(v).strip()
        if s in valid_ids:                     # already a treeAttributes id
            out.append(s)
        elif matching.norm(s) in ids_by_norm:          # a label like "42" / "46 / S"
            out.append(ids_by_norm[matching.norm(s)])
        else:
            raise ToolError(
                f"Unknown {kind} '{v}'. Valid values: {', '.join(mapping)}"
            )
    return out
