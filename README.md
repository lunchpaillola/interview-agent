# Interview Agent

[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

Run an AI interview from one API request, using your own provider keys.

Send an interview prompt and an analysis prompt. Interview Agent creates a private Daily room, returns a shareable meeting page, and runs the conversation with an animated voice agent. When the call ends, retrieve the transcript and analysis or receive a summary by email.

[Watch the original workflow demo](https://www.youtube.com/watch?v=Oy98s9RcscY) · [View on GitHub](https://github.com/lunchpaillola/interview-agent)

**This is an experimental self-hosted extraction of the PailFlow interview workflow, not a hosted API. Features may break. You run the service, bring your own provider accounts, and own hosting, persistence, and application integration.**

If it is useful, star the repo so more people can find it.

## Interested in a hosted interview API?

Want to use this interview API without running the service yourself? We're gathering interest in a hosted version.

Email [help@lunchpaillabs.com](mailto:help@lunchpaillabs.com?subject=Hosted%20interview%20API%20interest) with what you're building, your expected interview volume, and when you'd want to start. Those details help us assess whether it's a fit.

This is an expression of interest, not a hosted signup or availability commitment. Please use this email for hosted API inquiries, not self-hosted installation support.

## What you can do

- **Start an interview by API.** Define how the interviewer behaves and what the analysis should produce.
- **Share or embed the meeting page.** The styled Daily call UI and animated agent are included.
- **Run overlapping interviews.** Each call has its own room, conversation, transcript, and result.
- **Get the outcome.** Read the transcript and structured analysis through the API, with optional Resend email delivery.

No external database, Docker setup, or managed runtime is required. State stays in memory while the service runs.

## Quick start

### Requirements

- Python 3.12 and `uv`
- Daily, OpenAI, and Deepgram accounts with API access
- A Resend account and verified sender domain if you want email delivery

Provider usage is billed to your accounts. This version supports Daily rooms, not Zoom, Google Meet, or Teams.

### 1. Install

```sh
git clone https://github.com/lunchpaillola/interview-agent.git
cd interview-agent
uv venv --python python3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt
cp -n .env.example .env
```

The copy command leaves an existing `.env` untouched. Keep real credentials in that ignored file, never in committed source or browser code.

### 2. Configure

Fill in `.env`:

| Variable | Purpose |
| --- | --- |
| `DAILY_API_KEY` | Create private rooms and meeting tokens. |
| `OPENAI_API_KEY` | Conversation, speech generation, and post-call analysis. |
| `DEEPGRAM_API_KEY` | Speech recognition. |
| `API_AUTH_TOKEN` | Your own strong, random shared token for the interview API. Required at startup. |
| `PUBLIC_BASE_URL` | Public HTTPS address of your service, without a trailing slash. Defaults to `http://localhost:8000`. |
| `RESEND_API_KEY` | Optional email delivery. A send-only key is sufficient. |
| `RESEND_EMAIL_DOMAIN` | Optional email delivery. Must be verified in Resend; mail comes from `noreply@<domain>`. |
| `TEST_EMAIL_TO` | Optional test mailbox for your own checks; not a default API recipient. |

You can generate a local API token with:

```sh
python3 -c 'import secrets; print(secrets.token_urlsafe(32))'
```

For remote participants, configure your own HTTPS reverse proxy or tunnel and set `PUBLIC_BASE_URL` **before creating interviews**. The root URL has no dashboard; the API returns the meeting-page link.

### 3. Start the service

```sh
.venv/bin/uvicorn interview_agent.app:create_app --factory --host 127.0.0.1 --port 8000 --workers 1 --no-access-log
```

Keep this process running. **Use exactly one worker and no reload mode.** Initial imports and model loading can take a little time.

### 4. Create an interview

In a second terminal, set `API_AUTH_TOKEN` to the same value you saved in `.env`, then make a request:

```sh
export API_AUTH_TOKEN='the-same-token-you-put-in-.env'

curl --fail-with-body http://localhost:8000/interviews \
  -H "Authorization: Bearer $API_AUTH_TOKEN" \
  -H 'Content-Type: application/json' \
  --data '{
    "interview_prompt": "Interview the participant about a project they led. Ask one question at a time.",
    "analysis_prompt": "Return JSON with summary, strengths, and follow_up_questions. Transcript: {transcript}"
  }'
```

The service returns HTTP 201 after creating the room and tokens. It does not wait for the interview to finish:

```json
{
  "interview_id": "random-id",
  "meeting_url": "https://your-domain.daily.co/random-id?t=guest-meeting-token",
  "ui_url": "https://your-service.example/meet/random-id?cap=unguessable-capability"
}
```

Open `ui_url` to join the styled meeting page. The agent greets you when you join. `meeting_url` is the direct Daily alternative.

### 5. Read the result

Use the returned `interview_id`:

```sh
curl --fail-with-body http://localhost:8000/interviews/INTERVIEW_ID \
  -H "Authorization: Bearer $API_AUTH_TOKEN"
```

After the participant leaves and analysis finishes, the response looks like:

```json
{
  "interview_id": "random-id",
  "status": "completed",
  "transcript": [{"role": "user", "content": "My answer", "timestamp": "ISO timestamp"}],
  "result": {"analysis": {"summary": "..."}, "summary": "...", "email_sent": false},
  "error": null
}
```

## Customize the interview

Only `interview_prompt` and `analysis_prompt` are required. The create request also accepts:

```json
{
  "interview_prompt": "Interview the participant about their onboarding experience. Ask one question at a time.",
  "analysis_prompt": "Return JSON with summary, strengths, and weaknesses. Transcript: {transcript}",
  "bot_name": "PailBot",
  "greeting": "Explain that this is a short feedback interview.",
  "email_recipient": "participant@example.com"
}
```

- `bot_name` and `greeting` customize the introduction.
- Analysis supports `{transcript}` and `{qa_text}` placeholders. Otherwise, the complete role-labeled transcript is appended to your prompt. Analysis returns a JSON object with the fields requested by your prompt.
- Add `email_recipient` to send the summary and transcript through Resend. Omit it to skip email.
- Email failure appears in `result.email_error` without discarding analysis. Failed analysis leaves the collected transcript available. Empty calls complete without analysis or email.

### Embed the meeting page

Create the interview from your trusted backend, then use its returned `ui_url` as the iframe source:

```html
<iframe
  src="RETURNED_UI_URL"
  title="AI interview"
  allow="camera; microphone; autoplay; fullscreen"
  style="width: 100%; height: 720px; border: 0;"
></iframe>
```

Use HTTPS for remote microphone/camera access. Do not put the shared API token or provider keys in frontend JavaScript. Your application handles creating interviews and retrieving results through its backend.

## API reference

All `/interviews` routes require `Authorization: Bearer <API_AUTH_TOKEN>`.

| Method | Route | Behavior |
| --- | --- | --- |
| `POST` | `/interviews` | Create a private room and start an interview. |
| `GET` | `/interviews/{interview_id}` | Read status, transcript, analysis, and email outcome. |
| `POST` | `/interviews/{interview_id}/stop` | Request a stop, then process the captured transcript. |
| `GET` | `/meet/{interview_id}?cap=...` | Open the room-scoped meeting page. |

Statuses are `starting`, `waiting`, `active`, `analyzing`, `completed`, `failed`, or `stopped` (shutdown cancellation).

Stop requests return immediately and are safe to repeat. Poll the status route until processing finishes:

```sh
curl --fail-with-body -X POST http://localhost:8000/interviews/INTERVIEW_ID/stop \
  -H "Authorization: Bearer $API_AUTH_TOKEN"
```

## Operation and limits

- **Memory only.** Restarting loses interviews, transcripts, and results and invalidates meeting pages. Multiple worker processes do not share state.
- **Concurrent, not unlimited.** Calls can overlap, subject to CPU, memory, and provider limits. You own scaling and durable storage.
- **Bounded calls.** The bot stops when the interviewee leaves, after five minutes with no participant or speech activity, or after thirty minutes total.
- **Room cleanup.** Rooms are deleted after processing. Failed cleanup is visible in the status error. Daily rooms/tokens expire and eject participants after one hour as an independent orphan-bot backstop.
- **No durable orchestration.** The service does not reschedule failed interviews or resend failed email. Voice-provider SDKs may perform their own connection/request retries.
- **Graceful shutdown.** Uvicorn owns SIGINT/SIGTERM. Shutdown signals each interview to stop, waits up to 30 seconds, then cancels remaining tasks while allowing transport cleanup and Daily renderer settling. Avoid SIGKILL, which cannot clean up gracefully.
- **No automatic recording.** Calls do not start recordings or Daily server-side transcription by default.

### Access and privacy

The shared API token controls **all** interviews. No user accounts or tenant isolation are provided. Call protected endpoints from a trusted server-side client.

Both returned meeting links grant access to one interview: anyone holding either link can join. Do not publish them or log their query strings; configure proxies to omit or redact those strings too. Rooms are private and limited to the bot plus one interviewee. Guest tokens are not room owners/admins, and the browser never receives the bot's separate token.

Transcripts and results are available only through the protected API, not the public meeting page. The page uses `no-store` and `no-referrer`, but loads Daily JS, Tailwind, and Google Fonts assets from external CDNs.

Do not expose the repository directory with a file server. The application serves only the explicit meeting HTML route. Sprites are loaded internally by the bot and sent as video. Provider credentials and the shared API token stay on the server.

## Development

```sh
.venv/bin/python -m unittest discover -s tests -v
uv pip check --python .venv/bin/python
```

Tests use in-process HTTP and fake provider transports. They do not spend provider credits or send email. Live browser/audio and delivery checks require provider credentials and must be run separately.

### Under the hood

The voice pipeline uses Daily transport, Deepgram Nova-2 transcription with diarization, OpenAI `gpt-4.1` conversation/analysis, and OpenAI `echo` speech. Turn detection uses Silero VAD (0.5-second stop), minimum-three-word turn starts, and local Smart Turn v3 stops (60-second timeout). The local models use bundled ONNX files, not PyTorch.

This extraction preserves the first-participant greeting, aggregator-based transcripts, meeting layout/themes, forward/reverse RGB talking sprites, and Daily cleanup/renderer settling. It leaves out workflow checkpoints, database writes, billing/usage tracking, PostHog, accounts, webhooks, and managed infrastructure.

Transcripts use user/assistant roles rather than workflow-specific speaker mapping. Full-transcript analysis replaces filtered Q&A conversion, so short answers are retained. Context truncation/summarization is not included; keep prompts and conversations reasonably sized within the bounded session. Email uses a small escaped summary/transcript layout rather than the original candidate-specific template.

## License

Licensed under the [Apache License 2.0](LICENSE).

Copyright 2025 Lunch Pail Labs, LLC. Adapted Python code, meeting page, and sprites originate in PailFlow.
