# Copyright 2025 Lunch Pail Labs, LLC
# Licensed under the Apache License, Version 2.0
import asyncio
import json
import os
import secrets
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlencode

import httpx
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, EmailStr, Field, field_validator
from loguru import logger

from .bot import run_interview

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseModel):
    api_auth_token: str = Field(min_length=1)
    public_base_url: str
    daily_api_key: str = Field(min_length=1)
    openai_api_key: str = Field(min_length=1)
    deepgram_api_key: str = Field(min_length=1)
    resend_api_key: str = ''
    resend_email_domain: str = ''

    @field_validator('public_base_url')
    @classmethod
    def valid_url(cls, value):
        from urllib.parse import urlsplit
        url = urlsplit(value)
        if url.scheme not in ('http', 'https') or not url.netloc or url.query or url.fragment or url.username:
            raise ValueError('PUBLIC_BASE_URL must be an http(s) origin or base path')
        return value.rstrip('/')


class InterviewRequest(BaseModel):
    interview_prompt: str = Field(min_length=1, max_length=32000)
    analysis_prompt: str = Field(min_length=1, max_length=32000)
    bot_name: str = Field(default='PailBot', min_length=1, max_length=100)
    greeting: str = Field(default='', max_length=4000)
    email_recipient: EmailStr | None = None


@dataclass
class Session:
    interview_id: str
    request: InterviewRequest
    capability: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    room: dict = field(default_factory=dict)
    status: str = 'starting'
    transcript: list = field(default_factory=list)
    result: dict | None = None
    error: str | None = None
    stop: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task | None = None


async def create_room(settings, interview_id):
    expires = int(time.time()) + 3600
    async with httpx.AsyncClient(timeout=20, headers={'Authorization': f'Bearer {settings.daily_api_key}'}) as client:
        response = await client.post('https://api.daily.co/v1/rooms', json={
            'name': interview_id, 'privacy': 'private',
            'properties': {'exp': expires, 'eject_at_room_exp': True, 'max_participants': 2,
                           'enable_prejoin_ui': True, 'enable_screenshare': False},
        })
        response.raise_for_status()
        room = response.json()
        try:
            tokens = []
            for is_bot in (True, False):
                response = await client.post('https://api.daily.co/v1/meeting-tokens', json={'properties': {
                    'room_name': interview_id, 'exp': expires, 'eject_at_token_exp': True,
                    'is_owner': False, 'user_name': 'Interview agent' if is_bot else 'Participant',
                    'permissions': {'canAdmin': False},
                }})
                response.raise_for_status()
                tokens.append(response.json()['token'])
            return {'name': interview_id, 'url': room['url'], 'bot_token': tokens[0], 'guest_token': tokens[1]}
        except BaseException:
            await client.delete(f'https://api.daily.co/v1/rooms/{interview_id}')
            raise


async def delete_room(settings, room):
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.delete(f"https://api.daily.co/v1/rooms/{room['name']}", headers={'Authorization': f'Bearer {settings.daily_api_key}'})
        if response.status_code != 404:
            response.raise_for_status()


def create_app(settings=None):
    # Don't log conversational debug frames or traceback locals containing credentials.
    logger.remove()
    logger.add(sys.stderr, level='INFO', backtrace=False, diagnose=False)
    if settings is None:
        load_dotenv(ROOT / '.env')
        settings = Settings(**{key: os.getenv(key.upper(), '') for key in Settings.model_fields if key != 'public_base_url'},
                            public_base_url=os.getenv('PUBLIC_BASE_URL') or 'http://localhost:8000')
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    sessions = {}
    bearer = HTTPBearer(auto_error=False)

    async def authorize(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
        if not credentials or not secrets.compare_digest(credentials.credentials.encode(), settings.api_auth_token.encode()):
            raise HTTPException(401, 'Bearer token required', headers={'WWW-Authenticate': 'Bearer'})

    def find(interview_id):
        if interview_id not in sessions:
            raise HTTPException(404, 'Interview not found')
        return sessions[interview_id]

    async def execute(session):
        try:
            await run_interview(session, settings)
            session.status = 'completed'
        except asyncio.CancelledError:
            session.status = 'stopped'
            raise
        except Exception as error:
            session.status = 'failed'
            session.error = f'Interview processing failed ({type(error).__name__}). Check provider configuration and availability.'
        finally:
            try:
                await delete_room(settings, session.room)
            except Exception:
                session.error = (session.error or '') + ' Room cleanup failed; room expires automatically within one hour.'

    @app.on_event('shutdown')
    async def shutdown():
        active = [s for s in sessions.values() if s.task and not s.task.done()]
        for session in active:
            session.stop.set()
        if active:
            done, pending = await asyncio.wait([s.task for s in active], timeout=30)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

    @app.post('/interviews', status_code=201, dependencies=[Depends(authorize)])
    async def create(request: InterviewRequest):
        if request.email_recipient and not (settings.resend_api_key and settings.resend_email_domain):
            raise HTTPException(422, 'Email delivery is not configured')
        session = Session(secrets.token_hex(16), request)
        try:
            session.room = await create_room(settings, session.interview_id)
        except Exception:
            raise HTTPException(502, 'Could not create Daily room') from None
        sessions[session.interview_id] = session
        session.task = asyncio.create_task(execute(session))
        return {'interview_id': session.interview_id,
                'meeting_url': session.room['url'] + '?' + urlencode({'t': session.room['guest_token']}),
                'ui_url': f'{settings.public_base_url}/meet/{session.interview_id}?' + urlencode({'cap': session.capability})}

    @app.get('/interviews/{interview_id}', dependencies=[Depends(authorize)])
    async def status(interview_id: str):
        session = find(interview_id)
        return {'interview_id': interview_id, 'status': session.status, 'transcript': session.transcript,
                'result': session.result, 'error': session.error}

    @app.post('/interviews/{interview_id}/stop', dependencies=[Depends(authorize)])
    async def stop(interview_id: str):
        session = find(interview_id)
        session.stop.set()
        return {'interview_id': interview_id, 'status': session.status, 'stop_requested': True}

    @app.get('/meet/{interview_id}', response_class=HTMLResponse)
    async def meeting(interview_id: str, cap: str = ''):
        session = find(interview_id)
        if not secrets.compare_digest(cap.encode(), session.capability.encode()):
            raise HTTPException(404, 'Interview not found')
        if session.status in ('completed', 'failed', 'stopped'):
            raise HTTPException(410, 'Interview has ended')
        page = (ROOT / 'static' / 'meeting.html').read_text()
        page = page.replace('__ROOM_URL__', json.dumps(session.room['url']).replace('<', '\\u003c'))
        page = page.replace('__GUEST_TOKEN__', json.dumps(session.room['guest_token']).replace('<', '\\u003c'))
        return HTMLResponse(page, headers={'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer'})

    return app
