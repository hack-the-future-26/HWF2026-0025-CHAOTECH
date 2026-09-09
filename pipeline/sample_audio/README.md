# Sample audio

Regression fixtures for `asr.py`. Generated with Google TTS, **not human speech**.

| File | Language | Says |
|---|---|---|
| `hindi.mp3` | hi | हमारे गाँव में सड़क बहुत खराब है |
| `marathi.mp3` | mr | आमच्या गावात रस्ता खूप खराब आहे |
| `english.mp3` | en | the road in our village is very bad |
| `silence_noise.wav` | — | 3s of faint noise, no speech (hallucination check) |

## What these do and do not prove

They **do** verify the language-constraint fix: Hindi audio now transcribes as Hindi
instead of being detected as English and translated.

They **do not** stand in for real voice testing. TTS audio is far cleaner than any
microphone — no room tone, no breath, no clipping, no webm/opus compression, and
speech starts immediately. Those are exactly the conditions under which Whisper
invents fluent sentences it never heard ("Hello, how are you? What are you doing?"
is a well-known example, and is what a real recording produced here before the fix).

**Someone still needs to record real clips into a real microphone**, through the
browser, in each demo language — build plan P1 Step 2. Until that happens, live mic
input on demo day is an untested path.

## Regenerate

```bash
python -c "
from gtts import gTTS
gTTS('हमारे गाँव में सड़क बहुत खराब है', lang='hi').save('hindi.mp3')
gTTS('आमच्या गावात रस्ता खूप खराब आहे', lang='mr').save('marathi.mp3')
gTTS('the road in our village is very bad', lang='en').save('english.mp3')
"
```
