# Copyright 2025 Lunch Pail Labs, LLC
# Licensed under the Apache License, Version 2.0

"""Animation processor for bot visual states."""

from typing import Optional

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
    OutputImageRawFrame,
    SpriteFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


class TalkingAnimation(FrameProcessor):
    """Manages the bot's visual animation states.

    Switches between static (listening) and animated (talking) states based on
    the bot's current speaking status.
    """

    def __init__(
        self,
        quiet_frame: Optional[OutputImageRawFrame] = None,
        talking_frame: Optional[OutputImageRawFrame | SpriteFrame] = None,
    ):
        super().__init__()
        self._is_talking = False
        self.quiet_frame = quiet_frame
        self.talking_frame = talking_frame

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        """Process incoming frames and update animation state.

        Args:
            frame: The incoming frame to process
            direction: The direction of frame flow in the pipeline
        """
        await super().process_frame(frame, direction)

        # Switch to talking frame when bot starts speaking
        # SpriteFrame handles animation internally - we just push it once
        if isinstance(frame, BotStartedSpeakingFrame):
            if not self._is_talking and self.talking_frame is not None:
                await self.push_frame(self.talking_frame)
                self._is_talking = True
        # Return to static frame when bot stops speaking
        elif isinstance(frame, BotStoppedSpeakingFrame):
            if self.quiet_frame is not None:
                await self.push_frame(self.quiet_frame)
            self._is_talking = False

        await self.push_frame(frame, direction)
