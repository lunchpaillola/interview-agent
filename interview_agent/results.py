# Copyright 2025 Lunch Pail Labs, LLC
# Licensed under the Apache License, Version 2.0
"""Prompt-driven OpenAI analysis and optional, escaped Resend email."""
import html
import json

import httpx
from openai import AsyncOpenAI


async def process_result(session, settings):
    transcript = '\n'.join(f"{turn['role']}: {turn['content']}" for turn in session.transcript)
    if not transcript.strip():
        session.result = {'analysis': None, 'summary': 'No transcript available.', 'email_sent': False}
        return
    prompt = session.request.analysis_prompt
    if '{transcript}' in prompt or '{qa_text}' in prompt:
        prompt = prompt.replace('{transcript}', transcript).replace('{qa_text}', transcript)
    else:
        prompt += '\n\nConversation Transcript:\n' + transcript
    async with AsyncOpenAI(api_key=settings.openai_api_key, timeout=30, max_retries=0) as client:
        response = await client.chat.completions.create(model='gpt-4.1', temperature=.3,
            response_format={'type': 'json_object'}, messages=[
                {'role': 'system', 'content': 'You are an expert call evaluator. Analyze only what was said. Always return a JSON object, preserving the fields requested by the analysis prompt.'},
                {'role': 'user', 'content': prompt}])
    insights = json.loads(response.choices[0].message.content)
    summary = insights.get('summary')
    if not isinstance(summary, str):
        parts = []
        if insights.get('overall_score') is not None:
            parts.append(f"Overall Score: {insights['overall_score']}/10")
        for key, label in [('strengths', 'Strengths'), ('weaknesses', 'Areas for Improvement')]:
            if isinstance(insights.get(key), list):
                parts.append(label + ':\n' + '\n'.join('- ' + str(value) for value in insights[key][:3]))
        summary = '\n\n'.join(parts) or json.dumps(insights, indent=2)
    session.result = {'analysis': insights, 'summary': summary, 'email_sent': False}
    if session.request.email_recipient:
        body = ('<html><body style="font-family:Arial,sans-serif;color:#1e293b;max-width:680px;margin:auto">'
                '<h1>Session complete</h1><h2>Summary</h2><pre style="white-space:pre-wrap">'
                + html.escape(summary) + '</pre><h2>Transcript</h2><pre style="white-space:pre-wrap">'
                + html.escape(transcript) + '</pre></body></html>')
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.post('https://api.resend.com/emails',
                    headers={'Authorization': f'Bearer {settings.resend_api_key}',
                             'Idempotency-Key': f'interview-{session.interview_id}'},
                    json={'from': f'Interview Agent <noreply@{settings.resend_email_domain}>',
                          'to': [str(session.request.email_recipient)], 'subject': 'Session Complete',
                          'html': body})
                response.raise_for_status()
                session.result['email_sent'] = True
        except Exception:
            session.result['email_error'] = 'Email delivery failed; analysis remains available here.'
