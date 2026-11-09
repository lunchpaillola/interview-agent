import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from pipecat.processors.frame_processor import FrameProcessor
from pipecat.frames.frames import ErrorFrame
from pipecat.utils.base_object import BaseObject

from interview_agent.app import InterviewRequest, Session, Settings
from interview_agent.bot import run_interview


class FakeDaily(BaseObject):
    def __init__(self, *args, **kwargs):
        super().__init__()
        self.closed = False
        for name in ['on_first_participant_joined', 'on_participant_left', 'on_error']:
            self._register_event_handler(name)

    def input(self):
        return self.processor()

    def output(self):
        return self.processor()

    def processor(self):
        owner = self
        class Processor(FrameProcessor):
            async def cleanup(self):
                owner.closed = True
                await super().cleanup()
        return Processor()

    def participant_counts(self):
        return {'present': 1}

    async def cleanup(self):
        self.closed = True
        await super().cleanup()


class BotTests(unittest.IsolatedAsyncioTestCase):
    async def test_stt_startup_failure_stops_without_analysis(self):
        await self.check_pipeline_failure('Unable to connect to Deepgram')

    async def test_completion_failure_stops_without_analysis(self):
        await self.check_pipeline_failure('OpenAILLMService: Error during completion: authentication failed')

    async def test_speech_generation_failure_stops_without_analysis(self):
        await self.check_pipeline_failure('OpenAITTSService: Error generating TTS: quota exceeded')

    async def check_pipeline_failure(self, error):
        settings = Settings(api_auth_token='auth', public_base_url='http://localhost:8000', daily_api_key='daily', openai_api_key='openai', deepgram_api_key='dg')
        session = Session('failed-stt', InterviewRequest(interview_prompt='Ask about work', analysis_prompt='Summarize'))
        session.room = {'url': 'https://example.daily.co/failed-stt', 'bot_token': 'bot'}
        transport = FakeDaily()

        async def fake_run(runner, task):
            await task._call_event_handler('on_pipeline_error', ErrorFrame(error=error, fatal=False))

        with patch('interview_agent.bot.DailyTransport', return_value=transport), patch('interview_agent.bot.PipelineRunner.run', fake_run), patch('interview_agent.bot.process_result', AsyncMock()) as results:
            with self.assertRaisesRegex(RuntimeError, 'Voice pipeline failed'):
                await run_interview(session, settings)
        self.assertTrue(transport.closed)
        results.assert_not_awaited()

    async def test_installed_pipeline_callbacks_greet_collect_and_cleanup(self):
        settings = Settings(api_auth_token='auth', public_base_url='http://localhost:8000', daily_api_key='daily', openai_api_key='openai', deepgram_api_key='dg')
        session = Session('one', InterviewRequest(interview_prompt='Ask about work', analysis_prompt='Summarize', greeting='Say welcome'))
        session.room = {'url': 'https://example.daily.co/one', 'bot_token': 'bot'}
        transport = FakeDaily()

        async def fake_run(runner, task):
            def walk(processor):
                yield processor
                for child in processor.processors:
                    yield from walk(child)
            processors = list(walk(task.pipeline))
            user = next(p for p in processors if 'on_user_turn_stopped' in p._event_handlers)
            assistant = next(p for p in processors if 'on_assistant_turn_stopped' in p._event_handlers)
            await transport._call_event_handler('on_first_participant_joined', {'id': 'participant'})
            await user._call_event_handler('on_user_turn_stopped', None, SimpleNamespace(content='My answer', timestamp='now'))
            await assistant._call_event_handler('on_assistant_turn_stopped', SimpleNamespace(content='Thank you', timestamp='later'))
            await asyncio.sleep(0)
            self.assertEqual(session.status, 'active')
            self.assertIn('Say welcome', user._context.messages[-1]['content'])
            await transport._call_event_handler('on_participant_left', {}, 'left')
            await session.stop.wait()

        with patch('interview_agent.bot.DailyTransport', return_value=transport), patch('interview_agent.bot.PipelineRunner.run', fake_run), patch.object(asyncio.get_running_loop(), 'add_signal_handler', side_effect=AssertionError('runner must not install signal handlers')), patch('interview_agent.bot.process_result', AsyncMock()) as results:
            await run_interview(session, settings)
        self.assertTrue(transport.closed)
        self.assertEqual(session.transcript, [
            {'role': 'user', 'content': 'My answer', 'timestamp': 'now'},
            {'role': 'assistant', 'content': 'Thank you', 'timestamp': 'later'}])
        results.assert_awaited_once()
