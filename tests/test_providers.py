import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from openai import AsyncOpenAI

from interview_agent.app import InterviewRequest, Session, Settings, create_room, delete_room
from interview_agent.results import process_result


class ProviderTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.settings = Settings(api_auth_token='auth', public_base_url='http://localhost:8000', daily_api_key='daily', openai_api_key='openai', deepgram_api_key='dg', resend_api_key='resend', resend_email_domain='example.com')
        self.real_client = httpx.AsyncClient

    async def test_daily_room_tokens_are_private_scoped_and_expiring(self):
        requests = []

        def handle(request):
            data = json.loads(request.content) if request.content else None
            requests.append((request.method, request.url.path, data))
            if request.url.path.endswith('/rooms'):
                return httpx.Response(200, json={'url': 'https://example.daily.co/one'})
            if request.method == 'DELETE':
                return httpx.Response(200, json={})
            return httpx.Response(200, json={'token': 'token-' + str(len(requests))})

        with patch('interview_agent.app.httpx.AsyncClient', side_effect=lambda **kw: self.real_client(transport=httpx.MockTransport(handle), **kw)):
            room = await create_room(self.settings, 'one')
            await delete_room(self.settings, room)
        self.assertEqual(requests[0][2]['privacy'], 'private')
        self.assertTrue(requests[0][2]['properties']['eject_at_room_exp'])
        for _, _, data in requests[1:3]:
            properties = data['properties']
            self.assertEqual(properties['room_name'], 'one')
            self.assertFalse(properties['is_owner'])
            self.assertTrue(properties['eject_at_token_exp'])
        self.assertNotEqual(room['guest_token'], room['bot_token'])
        self.assertEqual(requests[-1][:2], ('DELETE', '/v1/rooms/one'))

    async def test_analysis_and_email_use_each_sessions_transcript(self):
        requests = []

        def handle(request):
            body = json.loads(request.content)
            requests.append((request.url.host, body))
            if request.url.host == 'api.openai.com':
                return httpx.Response(200, json={'id': 'chat-test', 'object': 'chat.completion', 'created': 0, 'model': 'gpt-4.1', 'choices': [{'index': 0, 'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': json.dumps({'summary': '<script>unsafe</script>', 'custom_field': 42})}}]})
            return httpx.Response(200, json={'id': 'email-test'})

        def openai(**kw):
            return AsyncOpenAI(http_client=self.real_client(transport=httpx.MockTransport(handle)), **kw)

        with patch('interview_agent.results.AsyncOpenAI', side_effect=openai), patch('interview_agent.results.httpx', SimpleNamespace(AsyncClient=lambda **kw: self.real_client(transport=httpx.MockTransport(handle), **kw))):
            for text in ['first answer', 'second answer']:
                session = Session(text, InterviewRequest(interview_prompt='Ask', analysis_prompt='Review {transcript}', email_recipient='person@example.com'))
                session.transcript.append({'role': 'user', 'content': text})
                await process_result(session, self.settings)
                self.assertEqual(session.result['analysis']['custom_field'], 42)
                self.assertTrue(session.result['email_sent'])
        self.assertIn('first answer', requests[0][1]['messages'][1]['content'])
        self.assertNotIn('first answer', requests[2][1]['messages'][1]['content'])
        self.assertIn('second answer', requests[2][1]['messages'][1]['content'])
        self.assertIn('&lt;script&gt;', requests[1][1]['html'])
        self.assertNotIn('<script>', requests[1][1]['html'])
        self.assertEqual(requests[1][1]['to'], ['person@example.com'])

    async def test_empty_transcript_does_not_call_providers(self):
        session = Session('empty', InterviewRequest(interview_prompt='Ask', analysis_prompt='Review'))
        with patch('interview_agent.results.AsyncOpenAI', side_effect=AssertionError('must not call')):
            await process_result(session, self.settings)
        self.assertIsNone(session.result['analysis'])
        self.assertFalse(session.result['email_sent'])
