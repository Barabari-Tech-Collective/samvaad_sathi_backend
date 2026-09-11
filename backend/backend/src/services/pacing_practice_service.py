"""Service layer for speech pacing practice.

Responsibilities
----------------
* Load and vend prompts from the static JSON bank.
* Compute WPM and pause-distribution metrics from Whisper word timestamps.
* Score a single attempt (0-100) based on WPM and pause interval targets.
* Derive level unlock status and overall readiness from persisted best scores.
"""

import difflib
import json
import re
import random
import statistics
from pathlib import Path
from typing import Optional

from src.services.pace_analysis import calculate_pace_metrics

# ---------------------------------------------------------------------------
# Prompt bank
# ---------------------------------------------------------------------------

_PROMPT_BANK: dict | None = None

# Level metadata kept here so it is always consistent across service + routes
LEVEL_META: dict[int, dict] = {
    1: {
        "name": "Level 1 - Sentence Control",
        "description": "Master basic sentence delivery and pacing",
    },
    2: {
        "name": "Level 2 - Paragraph Fluency",
        "description": "Build confidence with longer responses",
    },
    3: {
        "name": "Level 3 - Interview Mastery",
        "description": "Handle complex questions with confidence",
    },
}

# Score needed at level N to unlock level N+1
UNLOCK_THRESHOLD = 90


# ---------------------------------------------------------------------------
# Filler word detection
# ---------------------------------------------------------------------------

# Unambiguous non-lexical fillers only — contextual words ("like", "actually",
# "right") are deliberately excluded to avoid false-positives.
FILLER_WORDS: set[str] = {
    "um", "uh", "hmm", "hm", "er", "ah", "erm",
    "uhh", "umm", "ahh", "uhm", "mhm", "ugh",
}


def detect_filler_words(transcript: str, words: list[dict]) -> dict:
    """Count filler words and classify density.

    Parameters
    ----------
    transcript:
        Full plain-text transcription string (unused beyond signature compat —
        detection is done at the word-token level via *words*).
    words:
        Whisper word-level list used to get individual spoken tokens.

    Returns
    -------
    dict with keys:
        count, total_words, filler_ratio, status, suggestion, fillers_found
    """
    tokens = [w.get("word", "").strip().lower().strip(".,!?;:") for w in words]
    total_words = len(tokens)

    fillers_found: list[str] = [t for t in tokens if t in FILLER_WORDS]
    count = len(fillers_found)
    filler_ratio = count / total_words if total_words > 0 else 0.0

    if filler_ratio <= 0.03:
        status = "Good"
        suggestion = (
            "Great job! Your speech is clean and free of filler sounds."
            if count == 0
            else "Very few filler sounds detected. Keep up the clean delivery."
        )
    elif filler_ratio <= 0.08:
        status = "Average"
        suggestion = (
            'A few filler sounds appeared while speaking. '
            'Try pausing silently instead of saying "um" or "uh".'
        )
    else:
        status = "Needs Adjustment"
        suggestion = (
            "Frequent filler sounds are interrupting your speech. "
            "Slow slightly and replace filler words with short silent pauses."
        )

    return {
        "count": count,
        "total_words": total_words,
        "filler_ratio": round(filler_ratio, 4),
        "status": status,
        "suggestion": suggestion,
        "fillers_found": fillers_found,
    }


def _filler_score(filler_ratio: float) -> float:
    """Map filler ratio to a 0-100 score.

    0.00 → 100  |  0.03 → 75  |  0.08 → 40  |  ≥ 0.20 → 0
    """
    if filler_ratio <= 0.03:
        return 100.0 - (filler_ratio / 0.03) * 25
    elif filler_ratio <= 0.08:
        return 75.0 - ((filler_ratio - 0.03) / 0.05) * 35
    elif filler_ratio <= 0.20:
        return max(0.0, 40.0 - ((filler_ratio - 0.08) / 0.12) * 40)
    return 0.0


# ---------------------------------------------------------------------------
# Pause distribution — linguistic-structure-aware scoring
# ---------------------------------------------------------------------------

# Gaps ≥ this many seconds between consecutive words count as a deliberate pause.
_PAUSE_THRESHOLD_S = 0.50

_STRONG_PUNCT = frozenset([".", "?", "!", ";", ":"])
_MEDIUM_PUNCT  = frozenset([",", "—", "–"])


def _parse_prompt_pause_zones(prompt_text: str) -> dict:
    """Tokenise *prompt_text* and locate expected pause zones.

    Returns
    -------
    dict with keys:
        word_count, mandatory_zones (list[int]), optional_zones (list[int]),
        expected_pauses (float)
    """
    tokens = prompt_text.split()
    mandatory: list[int] = []
    optional_: list[int] = []
    word_idx = 0

    for token in tokens:
        bare = token.rstrip(".,!?;:—–")
        trailing = token[len(bare):]

        if bare:
            word_idx_for_this = word_idx
            word_idx += 1
        else:
            word_idx_for_this = max(0, word_idx - 1)

        for ch in trailing:
            if ch in _STRONG_PUNCT:
                mandatory.append(word_idx_for_this)
            elif ch in _MEDIUM_PUNCT:
                optional_.append(word_idx_for_this)

    # A pause "after" the very last prompt word is unmeasurable - pauses are
    # only detected as gaps *between* two consecutive words, and there's no
    # word following the last one to measure a gap against (speech just
    # ends there). Without this, the final full stop - the single mandatory
    # zone in most short one-sentence prompts - could never be satisfied,
    # permanently forcing "mandatory pauses not covered" regardless of how
    # the user actually paused.
    last_word_idx = word_idx - 1
    mandatory = [z for z in mandatory if z != last_word_idx]
    optional_ = [z for z in optional_ if z != last_word_idx]

    expected = len(mandatory) + len(optional_) * 0.6

    return {
        "word_count": word_idx,
        "mandatory_zones": mandatory,
        "optional_zones": optional_,
        "expected_pauses": round(expected, 2),
    }


def _detect_audio_pauses(words: list[dict]) -> list[int]:
    """Return word-indices *after* which a pause ≥ 500 ms was detected."""
    pause_positions: list[int] = []
    for i in range(len(words) - 1):
        gap = words[i + 1].get("start", 0.0) - words[i].get("end", 0.0)
        if gap >= _PAUSE_THRESHOLD_S:
            pause_positions.append(i)
    return pause_positions


def _is_near_zone(pause_idx: int, zones: list[int], tolerance: int = 1) -> bool:
    return any(abs(pause_idx - z) <= tolerance for z in zones)


def _prompt_word_tokens(prompt_text: str) -> list[str]:
    """Bare (punctuation-stripped) lowercase word tokens from prompt_text, in
    the same order/indexing as _parse_prompt_pause_zones' word_idx so the
    two stay aligned."""
    tokens: list[str] = []
    for token in prompt_text.split():
        bare = token.rstrip(".,!?;:—–")
        if bare:
            tokens.append(bare.lower())
    return tokens


def _align_words_to_prompt(words: list[dict], prompt_tokens: list[str]) -> list[int]:
    """Map each transcribed-word index to its best-matching prompt-word index.

    Whisper's transcript is rarely a perfect word-for-word match of the
    prompt (dropped/added words, contraction differences, mis-transcribed
    tokens), so pause positions detected in the transcript can't be
    compared by raw index against zones computed from the prompt text -
    doing so was making placement accuracy and mandatory compliance (70%
    of the pause score) collapse to near-zero on almost any transcription
    that wasn't an exact match, regardless of actual pause behaviour.

    difflib finds the longest matching runs between the two token
    sequences; positions inside a matching/replace run map 1:1 to the
    aligned prompt position, and any inserted words fall back to the
    nearest already-aligned neighbour.
    """
    spoken_tokens = [
        w.get("word", "").strip().lower().strip(".,!?;:—–") for w in words
    ]
    if not spoken_tokens or not prompt_tokens:
        return [0] * len(spoken_tokens)

    matcher = difflib.SequenceMatcher(None, spoken_tokens, prompt_tokens, autojunk=False)
    mapping: list[int | None] = [None] * len(spoken_tokens)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in ("equal", "replace"):
            span = min(i2 - i1, j2 - j1)
            for k in range(span):
                mapping[i1 + k] = j1 + k

    last_seen = 0
    for idx in range(len(mapping)):
        if mapping[idx] is not None:
            last_seen = mapping[idx]
        else:
            mapping[idx] = last_seen
    return mapping


def _segment_lengths(words: list[dict], pause_positions: list[int]) -> list[int]:
    if not pause_positions:
        return [len(words)]
    segs: list[int] = []
    prev = 0
    for pos in sorted(pause_positions):
        segs.append(pos - prev + 1)
        prev = pos + 1
    segs.append(len(words) - prev)
    return [s for s in segs if s > 0]


def score_pause_distribution(words: list[dict], prompt_text: str) -> dict:
    """Full 4-component pause distribution scorer (0-100).

    Components
    ----------
    40 %  Placement Accuracy   – pauses at/near expected punctuation zones
    30 %  Mandatory Compliance – pauses at sentence-ending punctuation
    20 %  Segment Length       – word-chunks in ideal 6-12 range
    10 %  Over/Under Penalty   – extreme behaviour check

    Returns
    -------
    dict with all raw metrics + pause_score (0-100) + status + feedback.
    """
    structure       = _parse_prompt_pause_zones(prompt_text)
    mandatory_zones = structure["mandatory_zones"]
    optional_zones  = structure["optional_zones"]
    all_zones       = mandatory_zones + optional_zones
    expected_pauses = structure["expected_pauses"]

    # `detected` indexes the actual transcript (used for segment lengths and
    # counts). `detected_aligned` maps those same pauses into prompt-word
    # index space so they can be compared against mandatory_zones/
    # optional_zones, which are computed from the prompt text.
    detected       = _detect_audio_pauses(words)
    total_detected = len(detected)

    prompt_tokens   = _prompt_word_tokens(prompt_text)
    word_alignment  = _align_words_to_prompt(words, prompt_tokens)
    detected_aligned = [
        word_alignment[p] for p in detected if p < len(word_alignment)
    ]

    # ----- 1. Placement Accuracy (40 %) -----------------------------------
    total_zones = len(all_zones)
    if total_zones > 0:
        correct = sum(1 for p in detected_aligned if _is_near_zone(p, all_zones))
        placement_acc = correct / total_zones
    else:
        placement_acc = 1.0
        correct = total_detected

    placement_pts = placement_acc * 40

    # ----- 2. Mandatory Pause Compliance (30 %) ---------------------------
    n_mandatory = len(mandatory_zones)
    if n_mandatory > 0:
        mandatory_hit    = sum(
            1 for z in mandatory_zones
            if any(abs(p - z) <= 1 for p in detected_aligned)
        )
        mandatory_compliance = mandatory_hit / n_mandatory
        mandatory_missed     = n_mandatory - mandatory_hit
    else:
        mandatory_compliance = 1.0
        mandatory_hit    = 0
        mandatory_missed = 0

    mandatory_pts = mandatory_compliance * 30

    # ----- 3. Segment Length Accuracy (20 %) ------------------------------
    # Segment lengths describe the actual spoken word chunks, so this stays
    # in transcript-index space rather than the prompt-aligned one.
    segs = _segment_lengths(words, detected)
    if segs:
        good_segs = sum(1 for s in segs if 6 <= s <= 12)
        seg_acc   = good_segs / len(segs)
    else:
        seg_acc = 1.0
    seg_pts = seg_acc * 20

    avg_words_per_pause = (
        len(words) / total_detected if total_detected > 0 else float(len(words))
    )

    # ----- 4. Over/Under Penalty (10 %) -----------------------------------
    penalty_score = 1.0
    if avg_words_per_pause < 4:
        penalty_score = 0.3
    elif avg_words_per_pause > 18:
        penalty_score = 0.3
    elif avg_words_per_pause > 15:
        penalty_score = 0.6
    elif avg_words_per_pause < 5:
        penalty_score = 0.6

    if expected_pauses > 0:
        ratio_vs_expected = total_detected / expected_pauses
        if ratio_vs_expected < 0.3 or ratio_vs_expected > 2.5:
            penalty_score = min(penalty_score, 0.4)

    penalty_pts = penalty_score * 10

    # ----- Final pause score ----------------------------------------------
    pause_score = int(round(placement_pts + mandatory_pts + seg_pts + penalty_pts))
    pause_score = max(0, min(100, pause_score))

    # ----- Comma misses (for user-facing report) --------------------------
    n_optional  = len(optional_zones)
    comma_hit   = sum(
        1 for z in optional_zones
        if any(abs(p - z) <= 1 for p in detected_aligned)
    )
    comma_missed = n_optional - comma_hit

    # ----- Status ---------------------------------------------------------
    if pause_score >= 80:
        pause_status   = "Good"
        pause_feedback = "Your pauses are well timed and naturally placed."
    elif pause_score >= 60:
        pause_status   = "Average"
        pause_feedback = (
            "Your pausing rhythm is developing. "
            "Try to pause at sentence boundaries and after commas."
        )
    else:
        pause_status   = "Needs Adjustment"
        pause_feedback = (
            "Work on pausing at natural linguistic breaks — especially "
            "after periods and commas — to improve clarity."
        )

    return {
        "score": pause_score,
        "status": pause_status,
        "feedback": pause_feedback,
        # Report-card summary numbers
        "avg_words_per_pause": round(avg_words_per_pause, 1),
        "total_pauses": total_detected,
        "expected_pauses": expected_pauses,
        "mandatory_pause_count": n_mandatory,
        "mandatory_pauses_hit": mandatory_hit,
        "mandatory_pauses_missed": mandatory_missed,
        "comma_pauses_missed": comma_missed,
        "mandatory_covered": mandatory_missed == 0,
        # Component breakdown (percentages)
        "placement_accuracy": round(placement_acc * 100, 1),
        "mandatory_compliance": round(mandatory_compliance * 100, 1),
        "segment_accuracy": round(seg_acc * 100, 1),
        "penalty_pct": round(penalty_score * 100, 1),
        # backward-compat alias
        "pause_words_interval": round(avg_words_per_pause, 2),
    }


def _load_prompt_bank() -> dict:
    """Load prompt bank from JSON file on first call (module-level cache)."""
    global _PROMPT_BANK
    if _PROMPT_BANK is not None:
        return _PROMPT_BANK
    prompts_file = Path(__file__).parent.parent / "data" / "pacing_practice_prompts.json"
    with open(prompts_file, "r", encoding="utf-8") as f:
        _PROMPT_BANK = json.load(f)
    return _PROMPT_BANK


def get_random_prompt(level: int) -> tuple[str, int]:
    """Return a randomly selected (prompt_text, prompt_index) for the given level."""
    bank = _load_prompt_bank()
    level_str = str(level)
    prompts: list[str] = bank.get(level_str, {}).get("prompts", [])
    if not prompts:
        raise ValueError(f"No prompts available for level {level}")
    prompt_index = random.randrange(len(prompts))
    return prompts[prompt_index], prompt_index


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def wpm_status_and_feedback(avg_wpm: float) -> tuple[str, str]:
    """Map an average WPM to its (status, feedback) pair.

    Shared by the live scoring path and the past-session retrieval fallback
    so status and feedback text can never drift apart - see the bug where
    GET /pacing-practice/session/{id} used to send back feedback="" and the
    frontend fell back to stale copy that didn't match the real status.
    """
    if 120 <= avg_wpm <= 150:
        return "Good", "Your speaking pace is within the ideal range for interviews"
    elif avg_wpm < 120:
        return "Needs Adjustment", "Try speaking a little faster to maintain listener engagement"
    else:
        return "Needs Adjustment", "Slow down slightly to give your listener time to follow along"


def _wpm_score(wpm: float) -> float:
    """Map WPM to a 0-100 component score.

    Ideal: 120-150 WPM → 100.
    Below 120: linearly decreases to 0 at 60 WPM.
    Above 150: linearly decreases to 0 at 210 WPM.
    """
    if 120 <= wpm <= 150:
        return 100.0
    elif wpm < 120:
        return max(0.0, (wpm - 60) / 60 * 100)
    else:
        return max(0.0, (210 - wpm) / 60 * 100)


def calculate_pacing_score(wpm: float, pause_score: float, filler_ratio: float) -> int:
    """Return a 0-100 composite score.

    Weights
    -------
    WPM          50 %
    Pause score  35 %  (linguistic 4-component scorer)
    Filler words 15 %
    """
    wpm_component    = _wpm_score(wpm) * 0.50
    pause_component  = pause_score * 0.35
    filler_component = _filler_score(filler_ratio) * 0.15
    return int(round(wpm_component + pause_component + filler_component))


# ---------------------------------------------------------------------------
# Metric building
# ---------------------------------------------------------------------------

def _badge_from_score(score: int) -> str:
    if score >= 80:
        return "Good"
    if score >= 65:
        return "Average"
    if score >= 50:
        return "Acceptable"
    return "Needs Adjustment"


def _safe_duration(words: list[dict]) -> float:
    if not words:
        return 0.0
    start = words[0].get("start", 0.0)
    end = words[-1].get("end", 0.0)
    return max(0.0, end - start)


def _score_wpm_level3(wpm: float) -> tuple[int, str]:
    if 110 <= wpm <= 165:
        return 100, "Your pace is within interview-ready range."
    if (100 <= wpm < 110) or (165 < wpm <= 175):
        return 80, "Your pace is slightly off ideal range but still understandable."
    if (90 <= wpm < 100) or (175 < wpm <= 185):
        return 60, "Your speed is noticeably outside ideal interview pacing."
    if wpm < 90:
        return 40, "You are speaking too slowly, which can sound unsure."
    return 40, "You are speaking too fast, which can sound rushed."


def _slice_wpm(words: list[dict], start_t: float, end_t: float) -> float:
    if end_t <= start_t:
        return 0.0
    count = sum(1 for w in words if w.get("start", 0.0) >= start_t and w.get("end", 0.0) <= end_t)
    return (count / (end_t - start_t)) * 60 if count > 0 else 0.0


def _score_speed_consistency(words: list[dict]) -> dict:
    duration = _safe_duration(words)
    if duration <= 0:
        return {
            "score": 40,
            "status": "Needs Adjustment",
            "variance_wpm": 0.0,
            "start_wpm": 0.0,
            "middle_wpm": 0.0,
            "end_wpm": 0.0,
            "feedback": "Could not compute pace consistency from the recording.",
        }
    start_t = words[0].get("start", 0.0)
    one_third = duration / 3.0
    s1 = _slice_wpm(words, start_t, start_t + one_third)
    s2 = _slice_wpm(words, start_t + one_third, start_t + 2 * one_third)
    s3 = _slice_wpm(words, start_t + 2 * one_third, start_t + duration)
    variance = max(s1, s2, s3) - min(s1, s2, s3)

    if variance <= 10:
        score = 100
    elif variance <= 20:
        score = 85
    elif variance <= 30:
        score = 65
    else:
        score = 40

    trend = ""
    if s3 < s1 - 10:
        trend = " Your pace slowed toward the end of the response."
    elif s3 > s1 + 10:
        trend = " Your pace increased toward the end of the response."

    return {
        "score": score,
        "status": _badge_from_score(score),
        "variance_wpm": round(variance, 1),
        "start_wpm": round(s1, 1),
        "middle_wpm": round(s2, 1),
        "end_wpm": round(s3, 1),
        "feedback": f"Pace variance across segments is {round(variance,1)} WPM.{trend}".strip(),
    }


def _score_pause_distribution_level3(words: list[dict]) -> dict:
    pauses: list[float] = []
    for i in range(len(words) - 1):
        gap = words[i + 1].get("start", 0.0) - words[i].get("end", 0.0)
        if gap > 0.3:
            pauses.append(gap)

    total = len(pauses)
    if total == 0:
        return {
            "score": 40,
            "status": "Needs Adjustment",
            "total_pauses": 0,
            "micro_pause_pct": 0.0,
            "thinking_pause_pct": 0.0,
            "long_pause_pct": 0.0,
            "feedback": "No meaningful pauses were detected in your response.",
        }

    micro = sum(1 for p in pauses if 0.3 <= p < 0.7)
    thinking = sum(1 for p in pauses if 0.7 <= p <= 1.5)
    long = sum(1 for p in pauses if p > 1.5)

    micro_pct = (micro / total) * 100
    thinking_pct = (thinking / total) * 100
    long_pct = (long / total) * 100

    if long_pct < 5:
        score = 100
    elif long_pct <= 10:
        score = 85
    elif long_pct <= 15:
        score = 65
    else:
        score = 40

    feedback = "Your pause balance supports clear thinking structure."
    if long_pct > 10:
        feedback = "Multiple long pauses suggest hesitation."

    return {
        "score": score,
        "status": _badge_from_score(score),
        "total_pauses": total,
        "micro_pause_pct": round(micro_pct, 1),
        "thinking_pause_pct": round(thinking_pct, 1),
        "long_pause_pct": round(long_pct, 1),
        "feedback": feedback,
    }


def _score_sentence_variation(transcript: str) -> dict:
    sentences = [s.strip() for s in re.split(r"[.!?]+", transcript) if s.strip()]
    lengths = [len([w for w in s.split() if w.strip()]) for s in sentences if s.strip()]
    if len(lengths) < 2:
        std_dev = 0.0
    else:
        std_dev = statistics.pstdev(lengths)

    if std_dev >= 6:
        score = 100
        level = "High variation"
        feedback = "Good sentence length variation improves natural flow."
    elif std_dev >= 4:
        score = 80
        level = "Moderate"
        feedback = "Try mixing short and long sentences. Break long ideas into smaller parts, and combine shorter ones to sound more natural."
    elif std_dev >= 2:
        score = 60
        level = "Low"
        feedback = "Try mixing short and long sentences. Break long ideas into smaller parts, and combine shorter ones to sound more natural."
    else:
        score = 40
        level = "Very repetitive"
        feedback = "Most of your sentences are similar in length. This makes your response sound robotic. Vary your sentence length to improve flow."

    return {
        "score": score,
        "status": _badge_from_score(score),
        "sentence_count": len(lengths),
        "std_dev_words": round(std_dev, 2),
        "variation_level": level,
        "feedback": feedback,
    }


def _expected_duration_from_prompt(prompt_text: str) -> tuple[float, float]:
    words_count = len([w for w in prompt_text.split() if w.strip()])
    expected_center = (words_count / 140.0) * 60.0
    min_s = max(30.0, expected_center * 0.7)
    max_s = max(min_s + 20.0, expected_center * 1.3)
    return round(min_s, 1), round(max_s, 1)


def _score_duration(actual_seconds: float, expected_min: float, expected_max: float) -> dict:
    if expected_min <= actual_seconds <= expected_max:
        score = 100
    else:
        center = (expected_min + expected_max) / 2.0
        if center <= 0:
            deviation_pct = 100.0
        else:
            deviation_pct = abs(actual_seconds - center) / center * 100
        if deviation_pct <= 20:
            score = 85
        elif deviation_pct <= 35:
            score = 65
        else:
            score = 40

    feedback = "Your answer is well-paced and fits within the ideal range."
    if score < 80:
        feedback = "Your answer length is not aligned with the ideal response window for this prompt."

    return {
        "score": score,
        "status": _badge_from_score(score),
        "actual_seconds": round(actual_seconds, 1),
        "expected_min_seconds": expected_min,
        "expected_max_seconds": expected_max,
        "feedback": feedback,
    }


def _score_energy(audio_features: dict | None, words: list[dict]) -> dict:
    if not audio_features:
        audio_features = {}

    pitch_var = float(audio_features.get("pitch_variation", 0.0))
    vol_var = float(audio_features.get("volume_variation", 0.0))
    source = audio_features.get("source", "fallback")

    if source == "fallback":
        durations = [max(0.01, w.get("end", 0.0) - w.get("start", 0.0)) for w in words]
        gaps = [max(0.0, words[i + 1].get("start", 0.0) - words[i].get("end", 0.0)) for i in range(len(words) - 1)]
        pitch_var = min(1.0, statistics.pstdev(durations) / max(0.01, statistics.fmean(durations))) if len(durations) > 1 else 0.0
        vol_var = min(1.0, statistics.pstdev(gaps) / max(0.01, statistics.fmean(gaps))) if len(gaps) > 1 else 0.0

    energy_idx = (pitch_var + vol_var) / 2.0
    if energy_idx >= 0.35:
        score = 100
    elif energy_idx >= 0.22:
        score = 80
    elif energy_idx >= 0.12:
        score = 60
    else:
        score = 40

    feedback = "Your vocal energy feels engaging and varied."
    if score < 80:
        feedback = "Your voice remained mostly flat."

    return {
        "score": score,
        "status": _badge_from_score(score),
        "pitch_variation": round(pitch_var, 4),
        "volume_variation": round(vol_var, 4),
        "feedback": feedback,
    }


def _score_consistency(speed_consistency: dict, filler: dict, pause_l3: dict) -> dict:
    pace_penalty = min(40.0, float(speed_consistency.get("variance_wpm", 0.0)) * 1.2)
    filler_density = float(filler.get("filler_ratio", 0.0)) * 100
    filler_penalty = min(30.0, filler_density * 3)
    long_pause_penalty = min(30.0, float(pause_l3.get("long_pause_pct", 0.0)) * 2)
    fluctuation_index = pace_penalty + filler_penalty + long_pause_penalty
    score = int(round(max(40.0, 100.0 - fluctuation_index)))

    feedback = "Your delivery is mostly stable with minor fluctuations."
    if score < 65:
        feedback = "Try keeping a more even rhythm to reduce noticeable delivery fluctuations."

    return {
        "score": score,
        "status": _badge_from_score(score),
        "fluctuation_index": round(fluctuation_index, 1),
        "feedback": feedback,
    }


def build_level3_report(words: list[dict], prompt_text: str, transcript: str, audio_features: dict | None) -> dict:
    pace_raw = calculate_pace_metrics(words) or {"avg_wpm": 0.0}
    wpm = float(pace_raw.get("avg_wpm", 0.0))

    wpm_score, wpm_feedback = _score_wpm_level3(wpm)
    speech_speed = {
        "score": wpm_score,
        "status": _badge_from_score(wpm_score),
        "wpm": round(wpm, 1),
        "ideal_range": "120-160",
        "feedback": wpm_feedback,
    }

    speech_consistency = _score_speed_consistency(words)
    pause_distribution = _score_pause_distribution_level3(words)
    filler = detect_filler_words(transcript, words)
    sentence_variation = _score_sentence_variation(transcript)

    actual_duration = _safe_duration(words)
    exp_min, exp_max = _expected_duration_from_prompt(prompt_text)
    response_duration = _score_duration(actual_duration, exp_min, exp_max)
    energy_level = _score_energy(audio_features, words)
    consistency = _score_consistency(speech_consistency, filler, pause_distribution)

    fluency_score = 100 if float(filler.get("filler_ratio", 0.0)) < 0.02 else 80 if float(filler.get("filler_ratio", 0.0)) < 0.04 else 60 if float(filler.get("filler_ratio", 0.0)) < 0.07 else 40
    filler["status"] = _badge_from_score(fluency_score)
    filler["score"] = fluency_score

    delivery_score = int(round((speech_speed["score"] + speech_consistency["score"]) / 2))
    clarity_score = int(round((pause_distribution["score"] + sentence_variation["score"]) / 2))
    fluency_group_score = fluency_score
    interview_quality_score = int(round((response_duration["score"] + energy_level["score"] + consistency["score"]) / 3))

    overall_score = int(round((delivery_score + clarity_score + fluency_group_score + interview_quality_score) / 4))

    level3_report = {
        "overall_score": overall_score,
        "overall_status": _badge_from_score(overall_score),
        "delivery_control": {
            "score": delivery_score,
            "status": _badge_from_score(delivery_score),
            "speech_speed": speech_speed,
            "speech_consistency": speech_consistency,
        },
        "clarity": {
            "score": clarity_score,
            "status": _badge_from_score(clarity_score),
            "pause_distribution": pause_distribution,
            "sentence_variation": sentence_variation,
        },
        "fluency": {
            "score": fluency_group_score,
            "status": _badge_from_score(fluency_group_score),
            "filler_words": filler,
        },
        "interview_quality": {
            "score": interview_quality_score,
            "status": _badge_from_score(interview_quality_score),
            "response_duration": response_duration,
            "energy_level": energy_level,
            "consistency": consistency,
        },
    }

    return {
        "wpm": wpm,
        "wpm_status": speech_speed["status"],
        "wpm_feedback": speech_speed["feedback"],
        "pause": {
            "score": pause_distribution["score"],
            "status": pause_distribution["status"],
            "feedback": pause_distribution["feedback"],
            "avg_words_per_pause": round((len(words) / max(1, pause_distribution["total_pauses"])), 1),
            "total_pauses": pause_distribution["total_pauses"],
            # Level 3 answers are free-form (no scripted prompt to derive
            # punctuation-based mandatory/optional zones from), so this
            # reuses _score_pause_distribution_level3's duration-based
            # metrics instead of the prompt-zone-based ones from
            # score_pause_distribution. These used to be hardcoded to 0.0 /
            # True regardless of actual pausing - always claiming "100%
            # mandatory covered" alongside "0% compliance" at the same
            # time, which is why the Level 3 report looked static.
            "expected_pauses": float(pause_distribution["total_pauses"]),
            "mandatory_pause_count": 0,
            "mandatory_pauses_hit": 0,
            "mandatory_pauses_missed": 0,
            "comma_pauses_missed": 0,
            "mandatory_covered": True,
            "placement_accuracy": float(pause_distribution["score"]),
            "mandatory_compliance": 100.0,
            "segment_accuracy": float(pause_distribution["score"]),
            "penalty_pct": float(pause_distribution.get("long_pause_pct", 0.0)),
            "pause_words_interval": round((len(words) / max(1, pause_distribution["total_pauses"])), 2),
        },
        "filler": filler,
        "score": overall_score,
        "pace_raw": pace_raw,
        "pause_words_interval": round((len(words) / max(1, pause_distribution["total_pauses"])), 2),
        "pause_status": pause_distribution["status"],
        "pause_feedback": pause_distribution["feedback"],
        "level3_report": level3_report,
    }


def build_pacing_metrics(
    words: list[dict],
    prompt_text: str,
    transcript: str,
    level: int | None = None,
    audio_features: dict | None = None,
) -> dict:
    """Compute all pacing metrics from Whisper word-timestamp objects.

    Parameters
    ----------
    words:
        List of ``{"word": str, "start": float, "end": float}`` dicts.
    prompt_text:
        The original prompt sentence(s) the user read aloud.
        Used to derive expected punctuation pause zones.
    transcript:
        Plain-text transcription (used for filler word detection).

    Returns
    -------
    dict with keys: wpm, wpm_status, wpm_feedback, pause (dict), filler (dict),
                    score, pace_raw, pause_words_interval (backward-compat alias)
    """
    if level == 3:
        return build_level3_report(words, prompt_text, transcript, audio_features)

    # --- WPM via existing service ---
    pace_raw = calculate_pace_metrics(words)
    if pace_raw is None:
        total_words = len(words)
        if total_words < 2:
            avg_wpm = 0.0
        else:
            duration_s = words[-1]["end"] - words[0]["start"]
            avg_wpm = (total_words / duration_s * 60) if duration_s > 0 else 0.0
        pace_raw = {"avg_wpm": avg_wpm}
    else:
        avg_wpm = pace_raw.get("avg_wpm", 0.0)

    wpm_status, wpm_feedback = wpm_status_and_feedback(avg_wpm)

    # --- Pause distribution (linguistic scorer) ---
    pause = score_pause_distribution(words, prompt_text)

    # --- Filler words ---
    filler = detect_filler_words(transcript, words)

    # --- Composite score ---
    score = calculate_pacing_score(avg_wpm, pause["score"], filler["filler_ratio"])

    return {
        "wpm": avg_wpm,
        "wpm_status": wpm_status,
        "wpm_feedback": wpm_feedback,
        "pause": pause,
        "filler": filler,
        "score": score,
        "pace_raw": pace_raw,
        # backward-compat flat field used by CRUD .update_with_analysis()
        "pause_words_interval": pause["pause_words_interval"],
        "pause_status": pause["status"],
        "pause_feedback": pause["feedback"],
    }


# ---------------------------------------------------------------------------
# Level unlock + readiness
# ---------------------------------------------------------------------------

def score_label(score: int) -> str:
    """Human-readable label for a numeric score."""
    if score >= 90:
        return "Excellent! Keep it up!"
    elif score >= 75:
        return "Good Progress! Keep Practicing"
    elif score >= 60:
        return "Almost there! A little more practice"
    else:
        return "Keep Practicing – you can do it!"


def get_level_statuses(level_bests: dict[int, Optional[int]]) -> list[dict]:
    """Return status dicts for all three levels given a user's best scores.

    Level 1 is always unlocked.
    Level 2 unlocks when Level 1 best >= UNLOCK_THRESHOLD.
    Level 3 unlocks when Level 2 best >= UNLOCK_THRESHOLD.
    """
    unlock_msgs = {
        1: f"Unlock level-2 at {UNLOCK_THRESHOLD}%",
        2: f"Unlock level-3 at {UNLOCK_THRESHOLD}%",
        3: "Complete all levels",
    }

    statuses = []
    for level in (1, 2, 3):
        meta = LEVEL_META[level]
        best = level_bests.get(level)

        # Determine if this level is accessible
        if level == 1:
            is_unlocked = True
        elif level == 2:
            is_unlocked = (level_bests.get(1) or 0) >= UNLOCK_THRESHOLD
        else:  # level == 3
            is_unlocked = (level_bests.get(2) or 0) >= UNLOCK_THRESHOLD

        if not is_unlocked:
            level_status = "locked"
        elif best is not None and best >= UNLOCK_THRESHOLD:
            level_status = "complete"
        else:
            level_status = "in_progress"

        statuses.append(
            {
                "level": level,
                "name": meta["name"],
                "description": meta["description"],
                "status": level_status,
                "best_score": best,
                "unlock_threshold": UNLOCK_THRESHOLD,
                "unlock_message": unlock_msgs[level],
            }
        )
    return statuses


def compute_overall_readiness(level_bests: dict[int, Optional[int]]) -> int:
    """Return 0-100 overall readiness percentage.

    Each level contributes equally (33.33 %).  Within a level the contribution
    is ``min(best, UNLOCK_THRESHOLD) / UNLOCK_THRESHOLD * 33.33``.
    """
    total = 0.0
    per_level = 100.0 / 3
    for level in (1, 2, 3):
        best = level_bests.get(level)
        if best is not None:
            capped = min(best, UNLOCK_THRESHOLD)
            total += (capped / UNLOCK_THRESHOLD) * per_level
    return int(round(total))
