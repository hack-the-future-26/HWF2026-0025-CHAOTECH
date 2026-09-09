"""
Speech-to-text.

Rewritten after live testing showed Hindi speech coming back as
"Hello, how are you? What are you doing?" -- fluent English that was never
said. Four separate causes, all now addressed:

1. NO LANGUAGE CONSTRAINT. The old call passed `language=None`, letting
   Whisper choose from 99 languages. On short, noisy, phone-quality audio it
   drifts to English, and once it has decided the audio is English it
   *translates* rather than transcribes. Detection is now restricted to the
   three languages this project actually supports, by reading Whisper's
   per-language probabilities and taking the best supported one.

2. NO VOICE-ACTIVITY DETECTION. Whisper hallucinates confident sentences
   over silence and breath noise -- "Hello, how are you?" is one of its
   best-known invented outputs. `vad_filter=True` strips non-speech before
   decoding, which is the single biggest fix here.

3. REPEAT LOOPS. `condition_on_previous_text` defaults to True, which makes
   the model echo its own previous output when it is unsure. Off.

4. NO DOMAIN VOCABULARY. Civic words (borewell, anganwadi, PHC, नल, रस्ता)
   are rare in Whisper's training distribution. A short `initial_prompt`
   per language biases decoding toward them.

Model size is configurable via WHISPER_MODEL, but `small` is the default and
MEASUREMENT SAYS KEEP IT. Compared on one TTS clip per language, both models
running through this module (VAD + domain prompt):

    Hindi    expected  हमारे गाँव में सड़क बहुत खराब है
             small     हमारे गाँव में सड़क बहुत खराब है     <- exact
             medium    हमारे गाव में सड़क बहुत खराब हैं     <- worse

    Marathi  expected  आमच्या गावात रस्ता खूप खराब आहे
             small     आम्च्या गावात रस्ता कुब खराभा हे    <- wrong
             medium    अम्च्या गावात रस्ता खुब खराबा है।   <- still wrong

    English  both exact.

So `medium` is roughly 2x slower (8.4s vs ~4s per clip), took 450s to
download, is WORSE on Hindi, and does not fix Marathi. Do not reach for it
expecting Indic gains.

Marathi needs a model actually trained for it -- a fine-tuned IndicWhisper /
IndicVoices checkpoint, which is exactly the recommendation in the research
report SS13.3. Vanilla Whisper at any size is the wrong tool for it.

Caveat on the above: one synthetic TTS clip per language is an indication,
not a benchmark. Re-measure on real recorded speech before making a final
model choice.
"""

from __future__ import annotations

import os

from faster_whisper import WhisperModel
from faster_whisper.audio import decode_audio

# Only these are supported end to end (see lang_id.SUPPORTED_LANGUAGES and
# extract.py's keyword dictionaries). Constraining detection to them is what
# stops Hindi being "detected" as English and silently translated.
SUPPORTED_ASR_LANGUAGES = ("hi", "mr", "en")

MODEL_SIZE = os.getenv("WHISPER_MODEL", "small")
DEVICE = os.getenv("WHISPER_DEVICE", "cpu")
COMPUTE_TYPE = os.getenv("WHISPER_COMPUTE_TYPE", "int8")

# Below this probability we do not trust the detected language and fall back
# to DEFAULT_LANGUAGE rather than guessing.
LANGUAGE_CONFIDENCE_FLOOR = 0.25
DEFAULT_LANGUAGE = "hi"

BEAM_SIZE = 5
NO_SPEECH_THRESHOLD = 0.6

# Domain vocabulary, per language. Keep these short -- a long prompt starts
# leaking its own words into the transcript.
DOMAIN_PROMPTS = {
    "hi": "सड़क, गड्ढा, पानी, नल, बोरवेल, अस्पताल, स्कूल, गाँव, शिकायत",
    "mr": "रस्ता, खड्डा, पाणी, नळ, दवाखाना, शाळा, गाव, तक्रार",
    "en": "road, pothole, water supply, borewell, hospital, school, village, complaint",
}

_model: WhisperModel | None = None


def get_model() -> WhisperModel:
    """Load the model once, on first use rather than at import time."""
    global _model
    if _model is None:
        _model = WhisperModel(MODEL_SIZE, device=DEVICE, compute_type=COMPUTE_TYPE)
    return _model


def detect_audio_language(audio_path: str) -> tuple[str, float]:
    """
    Detect the spoken language, restricted to SUPPORTED_ASR_LANGUAGES.

    Whisper's unrestricted detection is the root cause of Hindi being read as
    English, so instead of taking its top pick across 99 languages we read
    the full probability distribution and take the best *supported* one.

    Returns (language_code, probability).
    """
    audio = decode_audio(audio_path, sampling_rate=16000)
    _language, _probability, all_probs = get_model().detect_language(
        audio=audio,
        vad_filter=True,
    )

    if not all_probs:
        return DEFAULT_LANGUAGE, 0.0

    scores = dict(all_probs)
    best = max(SUPPORTED_ASR_LANGUAGES, key=lambda code: scores.get(code, 0.0))
    best_score = scores.get(best, 0.0)

    if best_score < LANGUAGE_CONFIDENCE_FLOOR:
        return DEFAULT_LANGUAGE, best_score

    return best, best_score


def transcribe_detailed(
    audio_path: str,
    language_hint: str | None = None,
) -> dict:
    """
    Transcribe audio and report which language it was read as.

    Args:
        audio_path:    path to an audio file (wav/webm/mp3/...).
        language_hint: force a language ("hi"/"mr"/"en"). Always more
                       reliable than detection when the speaker is known --
                       the dev console and the citizen UI should offer it.

    Returns:
        {"text", "language", "language_probability", "language_was_detected"}
    """
    if language_hint in SUPPORTED_ASR_LANGUAGES:
        language, probability, detected = language_hint, 1.0, False
    else:
        language, probability = detect_audio_language(audio_path)
        detected = True

    segments, _info = get_model().transcribe(
        audio_path,
        language=language,
        task="transcribe",           # never "translate" -- keep the citizen's own words
        vad_filter=True,             # strips silence: the main hallucination guard
        condition_on_previous_text=False,  # stops self-echoing repeat loops
        beam_size=BEAM_SIZE,
        no_speech_threshold=NO_SPEECH_THRESHOLD,
        initial_prompt=DOMAIN_PROMPTS.get(language),
    )

    text = " ".join(segment.text for segment in segments).strip()

    return {
        "text": text,
        "language": language,
        "language_probability": round(float(probability), 3),
        "language_was_detected": detected,
    }


def transcribe(audio_path: str, language_hint: str | None = None) -> str:
    """Text-only wrapper, kept for existing callers (pipeline.main)."""
    return transcribe_detailed(audio_path, language_hint)["text"]
