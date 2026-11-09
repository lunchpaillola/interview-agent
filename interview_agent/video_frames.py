# Copyright 2025 Lunch Pail Labs, LLC
# Licensed under the Apache License, Version 2.0
"""Original forward/reverse talking sprites, converted to RGB for Daily."""
from pathlib import Path

from PIL import Image
from pipecat.frames.frames import OutputImageRawFrame, SpriteFrame


def load_bot_video_frames():
    sprites = []
    for path in sorted((Path(__file__).resolve().parent.parent / 'static' / 'sprites').glob('frame_*.png')):
        with Image.open(path) as image:
            rgba = image.convert('RGBA')
            rgb = Image.new('RGB', rgba.size, 'white')
            rgb.paste(rgba, mask=rgba.getchannel('A'))
            sprites.append(OutputImageRawFrame(image=rgb.tobytes(), size=rgb.size, format='RGB'))
    if not sprites:
        raise RuntimeError('Bot sprites are missing')
    return sprites[0], SpriteFrame(images=sprites + sprites[::-1])
