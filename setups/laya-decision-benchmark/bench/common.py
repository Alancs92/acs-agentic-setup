"""Shared paths, privacy gate, truncation and JSONL helpers for the Laya benchmark.

Everything that touches work content lives under CACHE and is never committed.
"""
import datetime
import json
import os
import pathlib
import re

CACHE = pathlib.Path(os.environ.get("ACS_LAYA_CACHE", pathlib.Path.home() / ".cache" / "acs-laya-bench"))
DATA = CACHE / "data"
PREDS = CACHE / "preds"
SHADOW = CACHE / "shadow"
RUNLOG = CACHE / "RUNLOG.md"
SETUP = pathlib.Path(__file__).resolve().parent.parent
os.environ.setdefault("HF_HOME", str(CACHE / "hf"))

# State budget: the English checkpoint reads 512 tokens, 192 of which are the head budget.
STATE_TOKENS = 300


def runlog(msg):
    RUNLOG.parent.mkdir(parents=True, exist_ok=True)
    with open(RUNLOG, "a") as f:
        f.write(f"- {datetime.datetime.now().isoformat(timespec='seconds')} {msg}\n")


# ---- PHI gate: Python port of the vault's scripts/hooks/pre-commit UID check ----------------
# Rule there: >=3 dotted numeric arcs AND (length >=30 OR an arc of >=8 digits) AND
# (>=4 arcs OR the 2.25.<uuid> form); the DICOM standard root 1.2.840.10008. is exempt.
_OID_RE = re.compile(r"\b[0-9]{1,3}(?:\.[0-9]+){2,}\b")
_WRAP_RE = re.compile(r"(?<=[0-9.])[ \t]*\r?\n[ \t]*(?=[0-9.])")


def phi_hit(text):
    text = _WRAP_RE.sub("", text or "")
    for m in _OID_RE.finditer(text):
        uid = m.group(0)
        if uid.startswith("1.2.840.10008."):
            continue
        entropic = len(uid) >= 30 or re.search(r"\.[0-9]{8,}(\.|$)", uid)
        arcs_ok = uid.count(".") >= 3 or uid.startswith("2.25.")
        if entropic and arcs_ok:
            return True
    return False


# ---- truncation ------------------------------------------------------------------------------
_TOK = None


def tokenizer():
    global _TOK
    if _TOK is None:
        from transformers import AutoTokenizer
        snap = next((CACHE / "hf" / "hub" / "models--convaiinnovations--laya" / "snapshots").iterdir())
        _TOK = AutoTokenizer.from_pretrained(str(snap / "tokenizer"))
    return _TOK


def n_tokens(text):
    return len(tokenizer().encode(text, add_special_tokens=False))


def head_tail(text, budget=STATE_TOKENS):
    """Head-plus-tail truncation to `budget` tokens. Returns (text, was_truncated)."""
    ids = tokenizer().encode(text, add_special_tokens=False)
    if len(ids) <= budget:
        return text, False
    head = int(budget * 0.7)
    tail = budget - head - 3
    t = tokenizer()
    return t.decode(ids[:head]) + " … " + t.decode(ids[-tail:]), True


# ---- IO --------------------------------------------------------------------------------------
def write_jsonl(path, rows):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def read_jsonl(path):
    path = pathlib.Path(path)
    if not path.exists():
        return []
    return [json.loads(l) for l in open(path) if l.strip()]


def load_split(task, split):
    return read_jsonl(DATA / task / f"{split}.jsonl")


def validate_record(r):
    """Schema check standing in for `laya-evals validate` (not shipped in the 0.3.20 wheel)."""
    assert isinstance(r.get("id"), str) and r["id"], "id"
    assert isinstance(r.get("state"), dict) and r["state"].get("text"), "state.text"
    assert isinstance(r.get("questions"), dict) and len(r["questions"]) == 1, "one question"
    (qk, q), = r["questions"].items()
    assert q["type"] in ("choice", "noul"), q["type"]
    if q["type"] == "choice":
        assert r["expected"][qk] in q["criteria"], "expected in criteria"
    else:
        assert isinstance(r["expected"][qk], bool), "noul expects bool"
    assert any(t in r["tags"] for t in ("label:gold", "label:silver")), "label tag"
    assert re.match(r"\d{4}-\d{2}-\d{2}", r["ts"]), "ts"


def label_source(r):
    return "gold" if "label:gold" in r["tags"] else "silver"


def question(r):
    (qk, q), = r["questions"].items()
    return qk, q


def expected(r):
    qk, _ = question(r)
    return r["expected"][qk]
