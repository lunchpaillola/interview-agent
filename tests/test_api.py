import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from interview_agent.app import Settings, create_app


class ApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.settings = Settings(api_auth_token='test-secret', public_base_url='https://interviews.example', daily_api_key='daily', openai_api_key='openai', deepgram_api_key='deepgram')
        self.rooms = patch('interview_agent.app.create_room', AsyncMock(side_effect=self.room))
        self.rooms.start()
        self.bot = patch('interview_agent.app.run_interview', self.run_bot)
        self.bot.start()
        self.cleanup = patch('interview_agent.app.delete_room', AsyncMock())
        self.cleanup.start()
        self.app = create_app(self.settings)
        await self.app.router.startup()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url='http://test')
        self.auth = {'Authorization': 'Bearer test-secret'}

    async def asyncTearDown(self):
        await self.app.router.shutdown()
        await self.client.aclose()
        self.rooms.stop()
        self.bot.stop()
        self.cleanup.stop()

    async def room(self, settings, interview_id):
        return {'name': interview_id, 'url': f'https://example.daily.co/{interview_id}', 'bot_token': 'private-bot', 'guest_token': 'guest'}

    async def run_bot(self, session, settings):
        session.status = 'waiting'
        await session.stop.wait()
        if session.request.interview_prompt == 'fail':
            raise RuntimeError('provider failed with secret details')
        session.transcript.append({'role': 'user', 'content': session.request.interview_prompt})
        session.result = {'analysis': session.request.interview_prompt}

    async def create(self, prompt='hello'):
        return await self.client.post('/interviews', headers=self.auth, json={'interview_prompt': prompt, 'analysis_prompt': 'Summarize'})

    async def test_auth_validation_and_static_boundary(self):
        self.assertEqual((await self.client.post('/interviews', json={})).status_code, 401)
        self.assertEqual((await self.client.get('/interviews/missing')).status_code, 401)
        self.assertEqual((await self.client.post('/interviews/missing/stop')).status_code, 401)
        self.assertEqual((await self.client.post('/interviews', headers=self.auth, json={})).status_code, 422)
        for path in ['/.env', '/static/.env', '/README.md']:
            self.assertEqual((await self.client.get(path)).status_code, 404)

    async def test_concurrent_and_repeated_sessions_are_isolated(self):
        responses = await asyncio.gather(self.create('first'), self.create('second'))
        bodies = [r.json() for r in responses]
        self.assertTrue(all(r.status_code == 201 for r in responses))
        self.assertNotEqual(bodies[0]['interview_id'], bodies[1]['interview_id'])
        self.assertNotIn('private-bot', str(bodies))
        for body in bodies:
            self.assertTrue(body['ui_url'].startswith(self.settings.public_base_url))
            self.assertEqual((await self.client.get('/meet/' + body['interview_id'])).status_code, 404)
            page = await self.client.get(body['ui_url'])
            self.assertEqual(page.status_code, 200)
            self.assertIn('Daily.createFrame', page.text)
            self.assertNotIn('private-bot', page.text)
            await self.client.post('/interviews/' + body['interview_id'] + '/stop', headers=self.auth)
        await asyncio.sleep(.02)
        for body, prompt in zip(bodies, ['first', 'second']):
            state = (await self.client.get('/interviews/' + body['interview_id'], headers=self.auth)).json()
            self.assertEqual(state['status'], 'completed')
            self.assertEqual(state['result']['analysis'], prompt)
            self.assertEqual(state['transcript'], [{'role': 'user', 'content': prompt}])
        self.assertEqual((await self.create('third')).status_code, 201)

    async def test_failed_task_does_not_poison_next_session(self):
        failed = (await self.create('fail')).json()['interview_id']
        await self.client.post(f'/interviews/{failed}/stop', headers=self.auth)
        await asyncio.sleep(.02)
        state = (await self.client.get(f'/interviews/{failed}', headers=self.auth)).json()
        self.assertEqual(state['status'], 'failed')
        self.assertNotIn('secret details', str(state))
        self.assertEqual((await self.create()).status_code, 201)

    def test_config_requires_auth(self):
        with self.assertRaises(ValueError):
            Settings(api_auth_token='', public_base_url='https://example.com', daily_api_key='d', openai_api_key='o', deepgram_api_key='g')
