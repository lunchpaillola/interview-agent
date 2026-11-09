# Copyright 2025 Lunch Pail Labs, LLC
# Licensed under the Apache License, Version 2.0
"""One independent Pipecat pipeline per interview, with bounded Daily lifetime."""
import asyncio
from datetime import datetime, timezone

from deepgram import LiveOptions
from pipecat.audio.turn.smart_turn.base_smart_turn import SmartTurnParams
from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import LLMRunFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair, LLMUserAggregatorParams
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.services.openai.tts import OpenAITTSService
from pipecat.transports.daily.transport import DailyParams, DailyTransport
from pipecat.turns.user_start import MinWordsUserTurnStartStrategy
from pipecat.turns.user_stop import TurnAnalyzerUserTurnStopStrategy
from pipecat.turns.user_turn_strategies import UserTurnStrategies

from .animation import TalkingAnimation
from .results import process_result
from .video_frames import load_bot_video_frames


async def run_interview(session, settings):
    transport = DailyTransport(session.room['url'], session.room['bot_token'], session.request.bot_name,
        DailyParams(audio_in_enabled=True, audio_out_enabled=True, video_out_enabled=True,
                    video_out_width=1280, video_out_height=720, transcription_enabled=False))
    runner_task = None
    watchers = []
    joined = asyncio.Event()
    failure = []
    try:
        messages = [{'role': 'system', 'content': session.request.interview_prompt + '\n\nYour output is spoken aloud. Use natural, concise complete sentences without markdown. Wait for the participant to finish speaking.'}]
        context = LLMContext(messages)
        # Model loading is CPU-bound; don't stall other interviews or HTTP requests.
        turn_analyzer = await asyncio.to_thread(LocalSmartTurnAnalyzerV3, params=SmartTurnParams())
        vad = await asyncio.to_thread(SileroVADAnalyzer, params=VADParams(stop_secs=.5))
        aggregators = LLMContextAggregatorPair(context, user_params=LLMUserAggregatorParams(
            user_turn_strategies=UserTurnStrategies(
                start=[MinWordsUserTurnStartStrategy(min_words=3)],
                stop=[TurnAnalyzerUserTurnStopStrategy(turn_analyzer=turn_analyzer, timeout=60)]),
            vad_analyzer=vad, user_turn_stop_timeout=60))
        quiet, talking = await asyncio.to_thread(load_bot_video_frames)
        animation = TalkingAnimation(quiet_frame=quiet, talking_frame=talking)
        pipeline = Pipeline([
            transport.input(),
            DeepgramSTTService(api_key=settings.deepgram_api_key,
                               live_options=LiveOptions(model='nova-2', diarize=True)),
            aggregators.user(),
            OpenAILLMService(api_key=settings.openai_api_key, model='gpt-4.1'),
            OpenAITTSService(api_key=settings.openai_api_key, voice='echo'),
            animation, transport.output(), aggregators.assistant(),
        ])
        task = PipelineTask(pipeline, params=PipelineParams(enable_metrics=False, enable_usage_metrics=False))

        def record(role, message):
            if message and getattr(message, 'content', ''):
                session.transcript.append({'role': role, 'content': message.content,
                    'timestamp': str(getattr(message, 'timestamp', datetime.now(timezone.utc).isoformat()))})

        @aggregators.user().event_handler('on_user_turn_stopped')
        async def user_turn(_, __, message):
            record('user', message)

        @aggregators.assistant().event_handler('on_assistant_turn_stopped')
        async def assistant_turn(_, message):
            record('assistant', message)

        @transport.event_handler('on_first_participant_joined')
        async def first_participant(_, participant):
            joined.set()
            session.status = 'active'
            context.add_message({'role': 'system', 'content': f'Introduce yourself warmly. Your name is {session.request.bot_name}. {session.request.greeting}'})
            await task.queue_frame(LLMRunFrame())

        @transport.event_handler('on_participant_left')
        async def participant_left(_, participant, reason):
            counts = transport.participant_counts() or {}
            if counts.get('present', 1) <= 1:
                session.stop.set()

        @transport.event_handler('on_error')
        async def transport_error(_, error):
            failure.append('Daily transport failed')
            session.stop.set()

        @task.event_handler('on_pipeline_error')
        async def pipeline_error(_, frame):
            # Provider failures can be nonfatal in Pipecat; this service has no retry loop.
            failure.append('Voice pipeline failed')
            session.stop.set()

        async def wait_for_participant():
            try:
                await asyncio.wait_for(joined.wait(), timeout=300)
            except TimeoutError:
                session.stop.set()

        async def limit_duration():
            await asyncio.sleep(1800)
            session.stop.set()

        session.status = 'waiting'
        runner = PipelineRunner(handle_sigint=False, handle_sigterm=False)
        runner_task = asyncio.create_task(runner.run(task))
        watchers = [asyncio.create_task(wait_for_participant()), asyncio.create_task(limit_duration()),
                    asyncio.create_task(session.stop.wait())]
        await task.queue_frame(quiet)
        await asyncio.wait([runner_task, watchers[-1]], return_when=asyncio.FIRST_COMPLETED)
        if not runner_task.done():
            await task.cancel(reason='Interview ended')
        await runner_task
        if failure:
            raise RuntimeError(failure[0])
    finally:
        for watcher in watchers:
            watcher.cancel()
        await asyncio.gather(*watchers, return_exceptions=True)
        if runner_task and not runner_task.done():
            runner_task.cancel()
            await asyncio.gather(runner_task, return_exceptions=True)
        try:
            # The input/output processors own Daily's native client; the transport
            # object's inherited cleanup alone does not release that client.
            try:
                await transport.input().cleanup()
            finally:
                await transport.output().cleanup()
        finally:
            # Daily's Rust renderer threads must settle before the event loop closes.
            await asyncio.sleep(1.5)
    session.status = 'analyzing'
    await process_result(session, settings)
