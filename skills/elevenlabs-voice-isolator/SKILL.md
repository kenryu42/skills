---
name: elevenlabs-voice-isolator
description: Remove background noise and isolate vocals/speech from audio using ElevenLabs Voice Isolator (audio isolation) API. Use when cleaning up noisy recordings, removing music or background ambience from dialogue, isolating speech from field recordings, preparing audio for transcription, extracting vocals, or any "denoise / clean up / isolate voice" task.
license: MIT
metadata: {"openclaw": {"requires": {"env": ["ELEVENLABS_API_KEY"]}, "primaryEnv": "ELEVENLABS_API_KEY"}}
---

Requires internet access and `ELEVENLABS_API_KEY`. Use the direct API, SDK, or CLI; no connected plugin or browser OAuth is required. If credentials are missing, use [API-key setup](../elevenlabs-setup-api-key/SKILL.md). Load a chosen `.env` explicitly when needed; saving it does not automatically configure the process environment.

# ElevenLabs Voice Isolator

Removes background noise from audio and isolates vocals/speech — useful for cleaning up noisy recordings, prepping audio for transcription, or pulling dialogue out of a mixed track.

> **Setup:** See [Installation Guide](references/installation.md). For JavaScript, use `@elevenlabs/*` packages only.

## Local output and remote cleanup

Default: retain outputs locally and remove this task's remote generations after saving them. This also applies to previews, retries, test clips and streamed output. The examples below show generation only; complete these cleanup steps before reporting success.

1. Capture response headers and returned resource IDs alongside the local output path. Finish the download or stream, then verify the actual audio/video decodes or the transcript parses and contains the expected result. A filename or nonzero size alone is insufficient.
2. After verification and any dependent calls finish, delete only remote resources positively identified as created by this task. Keep IDs needed for cleanup locally until deletion is confirmed. Do not delete on a failed local save, while a dependent generation still needs the resource, or based only on voice, prompt or recency. Never bulk-delete unrelated history or voice profiles.
3. Verify removal using the resource lookup or a fully paginated, appropriately filtered listing. An authentication error or missing item on just the first page is not proof. If cleanup fails, retry once for a transient failure, then report the remaining IDs and reason. Preserve local files.

Do not treat HTTP success with `enable_logging=false` as proof of zero retention. Our Creator-plan test accepted that parameter but still stored downloadable audio. Use it only where supported and verify the outcome. Report verified dashboard/API removal, not guaranteed erasure from provider logs or backups. If deletion is unsupported or cannot be verified, report cleanup as incomplete.

Use the dedicated [`GET /v1/audio-isolation/history`](https://elevenlabs.io/docs/api-reference/audio-isolation/list) to locate the exact task-created generation and [`DELETE /v1/audio-isolation/history/{history_item_id}`](https://elevenlabs.io/docs/api-reference/audio-isolation/delete) to remove it and its associated media. Verify absence through that history API, following its documented pagination. Capture IDs returned by the operation; if none are returned, record the pre-call history and use returned metadata to establish ownership. Concurrent activity can make a before/after difference ambiguous; do not delete an uncertain match.

## Quick Start

### Python

```python
from elevenlabs import ElevenLabs

client = ElevenLabs()

with open("noisy.mp3", "rb") as audio_file:
    audio_stream = client.audio_isolation.convert(audio=audio_file)

with open("clean.mp3", "wb") as f:
    for chunk in audio_stream:
        f.write(chunk)
```

### JavaScript

```javascript
import { ElevenLabsClient } from "@elevenlabs/elevenlabs-js";
import { createReadStream, createWriteStream } from "fs";

const client = new ElevenLabsClient();

const audioStream = await client.audioIsolation.convert({
  audio: createReadStream("noisy.mp3"),
});

audioStream.pipe(createWriteStream("clean.mp3"));
```

### CLI

```bash
elevenlabs audio-isolation convert --audio noisy.mp3 --output clean.mp3
```

## Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `audio` | file (required) | — | Audio file with vocals/speech to isolate |
| `file_format` | string | `other` | `other` for any encoded audio, or `pcm_s16le_16` for 16-bit PCM mono @ 16kHz little-endian (lower latency) |

## Isolating from a URL

```python
import requests
from io import BytesIO
from elevenlabs import ElevenLabs

client = ElevenLabs()

audio_url = "https://example.com/noisy.mp3"
response = requests.get(audio_url)
audio_data = BytesIO(response.content)

audio_stream = client.audio_isolation.convert(audio=audio_data)

with open("clean.mp3", "wb") as f:
    for chunk in audio_stream:
        f.write(chunk)
```

## Low-Latency PCM Input

If you already have raw 16-bit PCM mono @ 16kHz, passing `file_format="pcm_s16le_16"` skips decoding and reduces latency:

```python
audio_stream = client.audio_isolation.convert(
    audio=pcm_bytes,
    file_format="pcm_s16le_16",
)
```

## Supported Formats

Any common encoded audio/video container works as input (MP3, WAV, M4A, FLAC, OGG, WebM, MP4, etc.). Response is a streamed MP3 by default.

## Common Workflows

- **Clean up interview/podcast recordings** — strip room tone, HVAC, traffic before editing.
- **Prep noisy audio for Speech-to-Text** — isolate voice first, then pass through `speech_to_text.convert()` for better transcription accuracy.
- **Extract dialogue from mixed tracks** — pull vocals out of a track with music/SFX.
- **Pre-processing for Voice Changer** — isolate the source voice before applying voice transformation.

## Error Handling

```python
try:
    audio_stream = client.audio_isolation.convert(audio=audio_file)
except Exception as e:
    print(f"Voice isolation failed: {e}")
```

Common errors:
- **401**: Invalid API key
- **422**: Invalid parameters (e.g. wrong `file_format` for the supplied audio)
- **429**: Rate limit exceeded

## References

- [Installation Guide](references/installation.md)
