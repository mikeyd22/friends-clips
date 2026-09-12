"""Portable runtime paths for the Friends clipping tools."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def whisper_command() -> str:
    configured = os.environ.get("FRIENDS_WHISPER_BIN")
    if configured:
        return configured
    wrapper = REPO_ROOT / "scripts" / "transcribe_compat.py"
    if wrapper.exists():
        return str(wrapper)
    for name in ("mlx_whisper", "whisper"):
        found = shutil.which(name)
        if found:
            return found
    return str(wrapper)


def whisper_model() -> str:
    return os.environ.get("FRIENDS_WHISPER_MODEL", "large-v3")


def _fc_match(family: str) -> str | None:
    if not shutil.which("fc-match"):
        return None
    try:
        result = subprocess.run(
            ["fc-match", "-f", "%{file}", family],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    value = result.stdout.strip()
    return value if value and Path(value).exists() else None


def font_path(env_name: str, candidates: list[str], families: list[str]) -> str:
    configured = os.environ.get(env_name)
    if configured and Path(configured).exists():
        return configured
    for candidate in candidates:
        if Path(candidate).exists():
            return candidate
    for family in families:
        matched = _fc_match(family)
        if matched:
            return matched
    raise FileNotFoundError(
        f"No usable font found for {env_name}; set {env_name} to a font file"
    )


EN_FONT = font_path(
    "FRIENDS_EN_FONT",
    [str(REPO_ROOT / "System/fonts/tiktok-sans/TikTokSans-Variable.ttf")],
    ["TikTok Sans", "DejaVu Sans"],
)

ZH_FONT = font_path(
    "FRIENDS_ZH_FONT",
    [
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    ],
    ["Noto Sans CJK SC", "Arial Unicode MS", "DejaVu Sans"],
)

EMOJI_FONT = font_path(
    "FRIENDS_EMOJI_FONT",
    [
        "/System/Library/Fonts/Apple Color Emoji.ttc",
        "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",
    ],
    ["Noto Color Emoji", "Apple Color Emoji"],
)
