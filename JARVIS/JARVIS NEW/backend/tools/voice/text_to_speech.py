"""
backend/tools/voice/text_to_speech.py
J.A.R.V.I.S. Text-to-Speech (TTS) Output Engine Tool.
Converts text into realistic spoken audio via the central API engine, 
managing voice selection, safe file paths, temporary cleanup, playback interruption,
and SessionManager/EmergencyStop guardrails using established project paths.
"""

import os
import sys
import time
import uuid
import shutil
import logging
import asyncio
from typing import Optional
from pydantic import BaseModel, Field

from backend.tools.base_tool import BaseTool
from backend.tools.files.file_security import secure_path_resolve, get_default_output_dir
from backend.infrastructure.api_engine import api_engine
from backend.infrastructure.event_bus import event_bus, JarvisEvent, EventType
from backend.observation.session_manager import session_manager
from backend.core.emergency_stop import emergency_stop
from backend.core.execution_errors import ExecutionError, ErrorClassification

logger = logging.getLogger("JARVIS.Tools.TextToSpeech")

def utc_now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)

class TextToSpeechInput(BaseModel):
    text: str = Field(description="The text that J.A.R.V.I.S. should speak out loud.")
    voice: str = Field(default="onyx", description="The voice personality to use (e.g., onyx, alloy, echo, nova).")
    cleanup_after_play: bool = Field(default=True, description="Whether to delete the temporary audio file after playback.")

class TextToSpeechTool(BaseTool):
    name = "text_to_speech"
    description = "Converts text into realistic spoken audio (MP3) using the central API engine and manages playback output."
    category = "voice"
    args_schema = TextToSpeechInput
    risk_level = "low"
    requires_approval = False
    
    def __init__(self):
        super().__init__()
        self._current_playback_process: Optional[asyncio.subprocess.Process] = None
        # True while audible output is being generated/played. The microphone
        # capture loop checks this to avoid transcribing JARVIS's own voice (echo).
        self.is_speaking: bool = False
        # Timestamp (monotonic) of when JARVIS last finished speaking. Lets the
        # microphone suppress the tail of speaker reverb after playback ends.
        self._last_speech_end: float = 0.0

    async def _emit_tts_event(self, topic: str, payload: dict):
        event = JarvisEvent(
            event_type=EventType.VOICE,
            topic=topic,
            timestamp=utc_now(),
            correlation_id="tts_playback",
            task_id="TTS_OUTPUT",
            source="TextToSpeechTool",
            payload=payload
        )
        await event_bus.publish(event)

    async def interrupt(self):
        """Immediately halts active audio playback/generation for interruption handling."""
        if self._current_playback_process and self._current_playback_process.returncode is None:
            try:
                self._current_playback_process.terminate()
                # Popen.wait() blocks — offload so the event loop stays responsive.
                await asyncio.to_thread(self._current_playback_process.wait)
                logger.info("Active TTS playback successfully interrupted.")
                await self._emit_tts_event("tts.interrupted", {})
            except Exception as e:
                logger.error(f"Failed to interrupt TTS playback: {e}")
            finally:
                self._current_playback_process = None
                self.is_speaking = False
                self._last_speech_end = time.time()
                await self._emit_tts_event("tts.lifecycle_reset", {})

    async def speak(self, text: str, voice: str = "onyx", allow_when_locked: bool = False) -> str:
        """
        Public system-level speech method used by the voice lifecycle and startup/unlock
        greetings. `allow_when_locked=True` is reserved for JARVIS core system messages
        (e.g., boot greeting); the Emergency Stop guardrail ALWAYS remains active.
        """
        return await self._run(text=text, voice=voice, cleanup_after_play=True, allow_when_locked=allow_when_locked)

    @staticmethod
    def _clip_speech_text(text: str, max_chars: int = 320) -> str:
        """
        Clips spoken text to a comfortably speakable length, truncating at the
        last complete sentence boundary. Rambling LLM replies previously ran the
        offline SAPI synthesizer past the 60s subprocess timeout — producing a
        timed-out command and ZERO audible output — so long replies must be
        trimmed before synthesis.
        """
        if len(text) <= max_chars:
            return text
        window = text[:max_chars]
        # Prefer the last full sentence that fits.
        for sep in (". ", "! ", "? ", ".\n", "!\n", "?\n"):
            idx = window.rfind(sep)
            if idx >= max_chars // 2:
                return window[: idx + 1].strip()
        # No sentence boundary in range — cut at the last word boundary instead.
        cut = window.rsplit(" ", 1)[0].strip()
        return (cut + ".").strip()

    def _get_playback_command(self, audio_file: str) -> list[str]:
        """Determine correct platform-specific audio playback command with active executable verification."""
        if sys.platform == "darwin":
            if shutil.which("afplay"):
                return ["afplay", audio_file]
            raise ExecutionError("Missing required 'afplay' utility on macOS.", classification=ErrorClassification.UNKNOWN_ERROR)
            
        elif sys.platform.startswith("linux"):
            for player in ["paplay", "aplay", "ffplay"]:
                if shutil.which(player):
                    if player == "ffplay":
                        return ["ffplay", "-nodisp", "-autoexit", audio_file]
                    return [player, audio_file]
            raise ExecutionError("No compatible audio playback utility found on Linux (checked paplay, aplay, ffplay).", classification=ErrorClassification.UNKNOWN_ERROR)
            
        elif sys.platform == "win32":
            # Media.SoundPlayer only supports WAV; TTS output is MP3.
            # Use the Windows Media Player COM object with a play-state polling loop,
            # which handles MP3/WMA correctly and blocks until playback finishes.
            ps_script = (
                "$ErrorActionPreference='Stop';"
                "$p=New-Object -ComObject WMPlayer.OCX.7;"
                f"$m=$p.newMedia('{audio_file}');"
                "$p.currentPlaylist.appendItem($m);"
                "$p.controls.play();"
                "while($p.playState -ne 1){Start-Sleep -Milliseconds 200};"
                "$p.close();"
            )
            return ["powershell", "-NoProfile", "-Command", ps_script]
        else:
            raise ExecutionError(f"Unsupported operating system for audio playback: {sys.platform}", classification=ErrorClassification.VALIDATION_FAILURE)

    async def _run(self, text: str, voice: str = "onyx", cleanup_after_play: bool = True, allow_when_locked: bool = False) -> str:
        if not text or not text.strip():
            return "No text provided for speech synthesis."

        # Voice-length guard: rambling LLM replies previously ran the SAPI
        # synthesizer past the 60s subprocess timeout, so NOTHING was heard and
        # the user had to repeat themselves. Cap spoken text to a comfortably
        # speakable length, truncating on the last complete sentence boundary.
        text = self._clip_speech_text(text.strip())

        # 1. Enforce Emergency Stop and SessionManager eligibility checks using canonical API
        try:
            emergency_stop.assert_system_running()
        except Exception as e:
            raise ExecutionError(f"TTS blocked: Global Emergency Stop is active. {str(e)}", classification=ErrorClassification.SECURITY_FAILURE)

        session_state = await session_manager.get_session_state()
        if not allow_when_locked and (session_state.get("is_locked") or not session_state.get("is_active")):
            raise ExecutionError("TTS blocked: Session is inactive or currently locked.", classification=ErrorClassification.AUTHORIZATION_FAILURE)

        try:
            safe_dir = get_default_output_dir(os.path.join("output", "audio"))
        except Exception as e:
            raise ExecutionError(
                message=f"Audio path resolution failed: {str(e)}",
                classification=ErrorClassification.SECURITY_FAILURE
            )
        
        file_name = f"speech_{uuid.uuid4().hex[:8]}.mp3"
        output_path = os.path.join(safe_dir, file_name)

        await self._emit_tts_event("tts.generation_started", {"voice": voice, "text_length": len(text)})

        try:
            # 2. Await asynchronous API Engine generation returning audio bytes
            try:
                tts_result = await api_engine.generate_tts(text=text, voice=voice)
                # API Engine returns a normalized dict contract carrying the raw audio
                # payload under "response_bytes"; accept both shapes defensively so
                # downstream playback always operates on raw bytes.
                if isinstance(tts_result, dict):
                    audio_bytes = tts_result.get("response_bytes")
                    logger.debug(f"TTS generation succeeded via provider [{tts_result.get('provider', 'unknown')}].")
                else:
                    audio_bytes = tts_result
            except ExecutionError:
                # No cloud TTS provider configured/available: fall back to the
                # offline Windows SAPI synthesizer so voice output still works.
                if sys.platform == "win32":
                    logger.info("Cloud TTS unavailable; using offline Windows SAPI fallback.")
                    self.is_speaking = True
                    try:
                        await self._speak_via_windows_sapi(text)
                    finally:
                        self.is_speaking = False
                        self._last_speech_end = time.time()
                    await self._emit_tts_event("tts.generation_completed", {"provider": "windows_sapi"})
                    return "Speech synthesized successfully via offline Windows SAPI."
                raise
            
            if not audio_bytes:
                raise ExecutionError("API Engine returned empty audio payload.", classification=ErrorClassification.TRANSIENT_PROVIDER)

            with open(output_path, "wb") as f:
                f.write(audio_bytes)
            
            logger.info(f"Audio successfully generated and saved to {output_path}")
            await self._emit_tts_event("tts.generation_completed", {"output_path": output_path})

            # 3. Handle playback integration asynchronously (strictly enforcing no-false-completion)
            await self._play_audio(output_path)

            return f"Audio generated successfully and played from {output_path}"
            
        except Exception as e:
            logger.error(f"TTS Execution failed: {str(e)}")
            await self._emit_tts_event("tts.generation_failed", {"error": str(e)})
            if isinstance(e, ExecutionError):
                raise e
            raise ExecutionError(
                message=f"TTS execution failed: {str(e)}",
                classification=ErrorClassification.TRANSIENT_PROVIDER
            )
        finally:
            # 4. Strict temporary-file cleanup and final lifecycle reset
            if cleanup_after_play and os.path.exists(output_path):
                try:
                    os.remove(output_path)
                    logger.debug(f"Temporary audio file cleaned up: {output_path}")
                except Exception as cleanup_err:
                    logger.warning(f"Failed to clean up temporary audio file {output_path}: {cleanup_err}")
            
            self._current_playback_process = None
            await self._emit_tts_event("tts.lifecycle_reset", {})

    async def _speak_via_windows_sapi(self, text: str):
        """
        Offline fallback: speaks text via the built-in Windows SAPI synthesizer
        (System.Speech). No network or API keys required. Playback blocks until
        the utterance completes.
        """
        import base64

        from backend.tools.subprocess_compat import run_process, decode_stream
        # PowerShell single-quoted strings make `"`, `$`, and backtick literal; the
        # only escape needed is `''` for an embedded apostrophe. (JSON escaping via
        # json.dumps previously emitted `\"` for embedded double-quotes — e.g. when
        # the reply quoted the user's words — which PowerShell parsed as an
        # unterminated string and crashed every such utterance.)
        safe_text = (text or "").replace("'", "''")
        ps_script = (
            "Add-Type -AssemblyName System.Speech;"
            "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
            f"$s.Speak('{safe_text}');"
            "$s.Dispose();"
        )
        # Passing the script via `-EncodedCommand` (UTF-16LE base64) avoids Windows
        # command-line argument quoting, which otherwise mangles the embedded double
        # quotes used by the SAPI Speak() call.
        encoded_cmd = base64.b64encode(ps_script.encode("utf-16-le")).decode("ascii")

        # Loop-safe execution: uvicorn --reload runs a WindowsSelectorEventLoop where
        # asyncio.create_subprocess_exec raises NotImplementedError, so this must
        # NOT use loop-integrated subprocess transports.
        result = await run_process(
            ["powershell", "-NoProfile", "-EncodedCommand", encoded_cmd],
            timeout=60.0,
            capture_output=True,
        )
        if result.returncode != 0:
            detail = decode_stream(result.stderr) or "unknown reason"
            raise ExecutionError(
                message=f"Windows SAPI speech failed (exit code {result.returncode}: {detail})",
                classification=ErrorClassification.TRANSIENT_PROVIDER
            )
        await self._emit_tts_event("tts.playback_completed", {"provider": "windows_sapi"})

    async def _play_audio(self, file_path: str):
        """Internal helper to manage audio playback securely with strict failure enforcement.

        Uses a thread-backed Popen handle (loop-independent) so playback also works on
        WindowsSelectorEventLoop (uvicorn --reload), where asyncio's integrated
        subprocess transports raise NotImplementedError.
        """
        await self._emit_tts_event("tts.playback_started", {"file_path": file_path})

        player_cmd = self._get_playback_command(file_path)
        self.is_speaking = True

        try:
            import asyncio as _asyncio
            import subprocess as _subprocess

            def _spawn() -> "_subprocess.Popen":
                return _subprocess.Popen(
                    player_cmd,
                    stdout=_subprocess.DEVNULL,
                    stderr=_subprocess.DEVNULL,
                )

            proc_handle = await _asyncio.to_thread(_spawn)
            # Store the live handle BEFORE waiting so interrupt() can terminate it.
            self._current_playback_process = proc_handle

            returncode = await _asyncio.to_thread(proc_handle.wait)

            if returncode != 0:
                raise ExecutionError(
                    message=f"Audio player exited with non-zero status code: {returncode}",
                    classification=ErrorClassification.TRANSIENT_PROVIDER
                )

            await self._emit_tts_event("tts.playback_completed", {"file_path": file_path})

        except Exception as e:
            logger.error(f"Audio playback execution failed: {e!r}")
            await self._emit_tts_event("tts.playback_failed", {"error": str(e)})
            if isinstance(e, ExecutionError):
                raise e
            raise ExecutionError(
                message=f"Audio playback failed: {str(e)!r}",
                classification=ErrorClassification.TRANSIENT_PROVIDER
            )
        finally:
            self.is_speaking = False
            self._last_speech_end = time.time()


text_to_speech_engine = TextToSpeechTool()