"""Vietnamese-friendly text normalization helpers."""

import re
import unicodedata


def normalize_text(value):
    value = unicodedata.normalize("NFD", str(value).strip().lower())
    value = "".join(char for char in value if unicodedata.category(char) != "Mn")
    value = value.replace("đ", "d")
    return re.sub(r"\s+", " ", value).strip()
