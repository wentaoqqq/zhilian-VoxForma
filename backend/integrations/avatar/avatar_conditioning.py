"""Request-local optional emotion recording for the native generation route.

Only the validated internal avatar endpoint sets this. It never changes global
engine preferences, and ordinary VoiceStudio requests see no override.
"""
from contextvars import ContextVar

emotion_audio = ContextVar('avatar_emotion_audio', default=None)
