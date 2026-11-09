# interview-agent
Experimental, self-hosted AI interview and meeting bot. Bring your own provider accounts; no hosted service or support commitment.

A small Python HTTP service that creates a private Daily room, runs a Pipecat voice interviewer, collects the conversation, and analyzes it with your prompt. Optional results email uses Resend. The styled meeting page and talking sprite animation are preserved from PailFlow.

## Run locally

Requires Python 3.12, `uv`, and provider accounts. No database, Docker, or managed runtime is required.

```sh
uv venv --python python3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt
cp .env.example .env  # only on first setup; never overwrite an existing .env
```

Set these server-only values in `.env`:

- `API_AUTH_TOKEN`: a strong, random shared bearer token. Required; startup fails without it.
- `DAILY_API_KEY`, `OPENAI_API_KEY`, `DEEPGRAM_API_KEY`: required.
- `PUBLIC_BASE_URL`: the public HTTPS address of this service, with no trailing slash. Defaults to `http://localhost:8000` for local use. For remote participants configure your own HTTPS reverse proxy or tunnel before creating interviews.
- `RESEND_API_KEY`, `RESEND_EMAIL_DOMAIN`: required only when requesting email. The domain must be verified with Resend; send-only API keys are sufficient. Mail comes from `noreply@<domain>`.

```sh
.venv/bin/uvicorn interview_agent.app:create_app --factory --host 127.0.0.1 --port 8000 --workers 1 --no-access-log
```

**Use exactly one worker process and no reload mode.** Every interview, task, transcript, and result lives in that process's memory. Multiple simultaneous and repeated interviews are supported, but multiple worker processes do not share state. Restarting loses all state and invalidates meeting pages. Initial imports/model loading can take a little time. Local Silero VAD and Smart Turn v3 use bundled ONNX models, not PyTorch.

Do not expose the repository directory with a file server. This application serves only the explicit `/meet/{id}` HTML route; sprites are loaded internally by the bot and sent as video. Provider credentials and the shared API token never enter the browser.

## API

All `/interviews` routes require `Authorization: Bearer <API_AUTH_TOKEN>`. Use a trusted server-side client, not browser JavaScript containing the shared token. No user accounts or tenant isolation are provided: the shared token controls all interviews.

### Create

`POST /interviews`, JSON:

```json
{
  "interview_prompt": "Interview the participant about their onboarding experience. Ask one question at a time.",
  "analysis_prompt": "Return JSON with summary, strengths, and weaknesses. Transcript: {transcript}",
  "bot_name": "PailBot",
  "greeting": "Explain that this is a short feedback interview.",
  "email_recipient": "participant@example.com"
}
```

Only the two prompts are required. Omit `email_recipient` to skip email. Analysis supports `{transcript}` and `{qa_text}` placeholders; otherwise the complete role-labeled transcript is appended. Analysis always returns a JSON object, preserving custom requested fields.

Returns HTTP 201 after Daily room/token creation, without waiting for the call or analysis:

```json
{
  "interview_id": "random-id",
  "meeting_url": "https://your-domain.daily.co/random-id?t=guest-meeting-token",
  "ui_url": "https://your-service.example/meet/random-id?cap=unguessable-capability"
}
```

Share `ui_url` for the styled meeting experience. `meeting_url` is the direct Daily alternative. Both are room-specific bearer capabilities: anyone holding either link can join that interview. Do not publish them or log their query strings. Configure proxies to omit/redact query strings, too. The page uses `no-store` and `no-referrer`, but loads the existing Daily JS, Tailwind, and Google Fonts assets from external CDNs. The browser never receives the bot's separate token. The room is private, limited to two participants (bot plus interviewee), and guest tokens are not room owners/admins.

### Read status and results

`GET /interviews/{interview_id}` returns:

```json
{
  "interview_id": "random-id",
  "status": "completed",
  "transcript": [{"role": "user", "content": "My answer", "timestamp": "ISO timestamp"}],
  "result": {"analysis": {"summary": "..."}, "summary": "...", "email_sent": false},
  "error": null
}
```

Statuses: `starting`, `waiting`, `active`, `analyzing`, `completed`, `failed`, or `stopped` (shutdown cancellation). Transcript/results are only on this protected route, never on the public meeting page. Email failure is reported in `result.email_error` without discarding analysis. Empty calls complete without an analysis or email. Failed analysis leaves the collected transcript available. The service does not reschedule failed interviews or resend failed email; voice-provider SDKs may perform their own connection/request retries.

### Stop

`POST /interviews/{interview_id}/stop` requests a stop, returning immediately. The bot leaves, analyzes its captured transcript, and optionally emails results; poll the status route for completion. Repeated stop requests are safe. The bot also stops when the interviewee leaves, after five minutes with no participant or speech activity, or after thirty minutes total. Daily tokens/room expire and eject participants after one hour as an independent orphan-bot backstop. Rooms are deleted after processing; failed cleanup is visible in the status error.

SIGINT/SIGTERM is owned by Uvicorn, not individual Pipecat runners. Shutdown signals each interview to stop, waits up to 30 seconds, then cancels remaining tasks while allowing transport cleanup and Daily renderer settling. Avoid SIGKILL, which cannot clean up gracefully; Daily expiry remains the backstop.

## Checks

```sh
.venv/bin/python -m unittest discover -s tests -v
uv pip check --python .venv/bin/python
```

Tests use in-process HTTP and fake provider transports. They do not spend provider credits or send email. Live browser/audio and delivery testing must be done separately with provider credentials.

## Extraction boundaries

Preserved: Deepgram Nova-2 transcription with diarization, OpenAI `gpt-4.1` conversation/analysis, OpenAI `echo` speech, Silero VAD (0.5-second stop), minimum-three-word turn starts, local Smart Turn v3 stops (60-second timeout), greeting on first participant, aggregator-based user/assistant transcripts, existing meeting layout/themes, RGB forward/reverse talking sprites, and Daily cleanup/renderer settling.

Removed: workflow checkpoints, database writes, billing/usage tracking, PostHog, accounts, webhooks, and managed infrastructure. Transcripts use user/assistant roles rather than workflow-specific speaker mapping. Full transcript analysis replaces the old filtered Q&A conversion, so short answers are retained. Context truncation/summarization is not included; keep interview prompts and conversations reasonably sized within the bounded session. Email uses a small escaped summary/transcript layout rather than the workflow's candidate-specific template. No recordings or Daily server-side transcription are started by default.

Copyright 2025 Lunch Pail Labs, LLC. Licensed under Apache-2.0; see [LICENSE](LICENSE). Adapted Python code, meeting page, and sprites originate in PailFlow.
