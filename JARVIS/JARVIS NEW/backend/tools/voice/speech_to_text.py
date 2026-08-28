"""
backend/tools/voice/speech_to_text.py
J.A.R.V.I.S. Speech-to-Text (STT) Input Engine Tool.
Performs real microphone audio stream capture, enforces SessionManager eligibility and EmergencyStop checks,
delegates transcription to the central API engine, and returns normalized transcript contracts with canonical TIMEOUT error classification.
"""

import os
import uuid
import wave
import logging
import asyncio
from typing import Dict, Any, Optional
from pydantic import BaseModel, Field

from backend.tools.base_tool import BaseTool
from backend.tools.files.file_security import secure_path_resolve, get_default_output_dir
from backend.infrastructure.api_engine import api_engine
from backend.infrastructure.event_bus import event_bus, JarvisEvent, EventType
from backend.observation.session_manager import session_manager
from backend.core.emergency_stop import emergency_stop
from backend.core.execution_errors import ExecutionError, ErrorClassification

logger = logging.getLogger("JARVIS.Tools.SpeechToText")

def utc_now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)

class SpeechToTextInput(BaseModel):
    file_path: Optional[str] = Field(default=None, description="Optional path to a pre-recorded audio file to transcribe.")
    language: str = Field(default="en", description="The ISO language code of the audio or 'auto' for detection.")
    duration_seconds: float = Field(default=10.0, description="Max recording duration if capturing live audio.")

class SpeechToTextTool(BaseTool):
    name = "speech_to_text"
    description = "Transcribes spoken audio into text using real microphone capture or files via the central API engine with session guardrails."
    category = "voice"
    args_schema = SpeechToTextInput
    risk_level = "low"
    requires_approval = False
    
    def __init__(self):
        super().__init__()
        self._is_listening: bool = False

    async def _emit_stt_event(self, topic: str, payload: dict):
        event = JarvisEvent(
            event_type=EventType.VOICE,
            topic=topic,
            timestamp=utc_now(),
            correlation_id="stt_processing",
            task_id="STT_INPUT",
            source="SpeechToTextTool",
            payload=payload
        )
        await event_bus.publish(event)

    async def _enforce_safety_boundaries(self):
        """Enforces global EmergencyStop and SessionManager voice eligibility before processing."""
        try:
            emergency_stop.assert_system_running()
        except Exception as e:
            raise ExecutionError(f"STT blocked: Global Emergency Stop is active. {str(e)}", classification=ErrorClassification.SECURITY_FAILURE)

        if not await session_manager.is_voice_listening_eligible():
            raise ExecutionError("STT blocked: Voice listening is not currently eligible or session is locked.", classification=ErrorClassification.AUTHORIZATION_FAILURE)

    async def listen_and_transcribe(self, timeout: float = 60.0, language: str = "en") -> Dict[str, Any]:
        """
        Public canonical method for WakeWord/Voice lifecycle integration:
        Validates eligibility, captures live microphone stream input, handles temporary storage,
        transcribes via API Engine, and returns a normalized transcript payload.
        """
        await self._enforce_safety_boundaries()

        try:
            safe_dir = get_default_output_dir(os.path.join("output", "audio_in"))
        except Exception as e:
            raise ExecutionError(
                message=f"Audio input path resolution failed: {str(e)}",
                classification=ErrorClassification.SECURITY_FAILURE
            )
        
        temp_file_name = f"mic_capture_{uuid.uuid4().hex[:8]}.wav"
        temp_file_path = os.path.join(safe_dir, temp_file_name)

        await self._emit_stt_event("stt.listening_started", {"timeout": timeout, "language": language})
        self._is_listening = True

        try:
            # 1. Real microphone audio stream capture with timeout protection
            captured_path = await asyncio.wait_for(
                self._capture_microphone_audio(temp_file_path, duration=min(timeout, 15.0)),
                timeout=timeout
            )
            
            if not captured_path or not os.path.exists(captured_path) or os.path.getsize(captured_path) <= 44:
                await self._emit_stt_event("stt.silence_detected", {})
                return {"transcript": "", "confidence": 1.0, "provider": "none", "latency_ms": 0.0, "empty": True}

            with open(captured_path, "rb") as audio_file:
                audio_bytes = audio_file.read()

            await self._emit_stt_event("stt.transcription_started", {"file_size_bytes": len(audio_bytes)})
            start_time = utc_now()

            # 2. Delegate to central API Engine transcribe_audio with failover support
            transcription_result = await api_engine.transcribe_audio(
                audio_bytes=audio_bytes,
                language=language if language != "auto" else None
            )

            latency_ms = (utc_now() - start_time).total_seconds() * 1000.0

            # Normalize response payload contract
            if isinstance(transcription_result, dict):
                transcript_text = transcription_result.get("text") or transcription_result.get("response", "") or ""
                confidence = transcription_result.get("confidence", 0.95)
                provider = transcription_result.get("provider", "default_provider")
                model = transcription_result.get("model", "default_model")
            else:
                transcript_text = str(transcription_result)
                confidence = 0.95
                provider = "default_provider"
                model = "default_model"

            normalized_response = {
                "transcript": transcript_text,
                "confidence": confidence,
                "provider": provider,
                "model": model,
                "latency_ms": latency_ms,
                "empty": not bool(transcript_text.strip())
            }

            await self._emit_stt_event("stt.transcription_completed", normalized_response)
            return normalized_response

        except asyncio.TimeoutError:
            await self._emit_stt_event("stt.timeout", {})
            raise ExecutionError(
                message="Audio capture and transcription operation timed out.",
                classification=ErrorClassification.TIMEOUT
            )
        except Exception as e:
            logger.error(f"STT execution error: {str(e)}")
            await self._emit_stt_event("stt.transcription_failed", {"error": str(e)})
            if isinstance(e, ExecutionError):
                raise e
            raise ExecutionError(
                message=f"Transcription failed: {str(e)}",
                classification=ErrorClassification.TRANSIENT_PROVIDER
            )
        finally:
            self._is_listening = False
            # Strict temporary audio file cleanup to prevent plaintext/audio leakage
            if os.path.exists(temp_file_path):
                try:
                    os.remove(temp_file_path)
                    logger.debug(f"Temporary input audio file cleaned up: {temp_file_path}")
                except Exception as cleanup_err:
                    logger.warning(f"Failed to clean up temporary audio file {temp_file_path}: {cleanup_err}")

    async def _capture_microphone_audio(self, output_path: str, duration: float) -> str:
        """
        Real hardware microphone capture implementation using PyAudio (PortAudio bindings).
        Records PCM audio chunks into memory and writes a structured WAV file.
        """
        def _record_sync():
            try:
                import pyaudio
            except ImportError as imp_err:
                raise ExecutionError(
                    message=f"Required audio library 'pyaudio' is not installed: {imp_err}",
                    classification=ErrorClassification.VALIDATION_FAILURE
                )

            chunk = 1024
            sample_format = pyaudio.paInt16
            channels = 1
            rate = 16000
            frames_to_record = int(rate / chunk * duration)

            p = pyaudio.PyAudio()
            try:
                stream = p.open(
                    format=sample_format,
                    channels=channels,
                    rate=rate,
                    input=True,
                    frames_per_buffer=chunk
                )
            except Exception as stream_err:
                p.terminate()
                raise ExecutionError(
                    message=f"Failed to open microphone audio input stream: {stream_err}",
                    classification=ErrorClassification.TRANSIENT_PROVIDER
                )

            frames = []
            try:
                for _ in range(frames_to_record):
                    data = stream.read(chunk, exception_on_overflow=False)
                    frames.append(data)
            finally:
                stream.stop_stream()
                stream.close()
                p.terminate()

            # Write recorded raw PCM frames into a WAV file container
            with wave.open(output_path, "wb") as wf:
                wf.setnchannels(channels)
                wf.setsampwidth(p.get_sample_size(sample_format))
                wf.setframerate(rate)
                wf.writeframes(b"".join(frames))

            return output_path

        return await asyncio.to_thread(_record_sync)

    async def _run(self, file_path: Optional[str] = None, language: str = "en", duration_seconds: float = 10.0) -> str:
        """Tool execution interface compliance wrapper with unified safety boundaries."""
        await self._enforce_safety_boundaries()

        if file_path:
            # Handle pre-recorded file input path with identical normalization and security bounds
            safe_path = secure_path_resolve(file_path)
            if not os.path.exists(safe_path):
                raise FileNotFoundError(f"Audio file not found at: {safe_path}")
            
            with open(safe_path, "rb") as f:
                audio_bytes = f.read()
                
            start_time = utc_now()
            res = await api_engine.transcribe_audio(audio_bytes=audio_bytes, language=language if language != "auto" else None)
            
            if isinstance(res, dict):
                return str(res.get("text") or res.get("response") or "")
            return str(res)
        else:
            # Handle live microphone listening trigger
            result = await self.listen_and_transcribe(timeout=duration_seconds, language=language)
            return result.get("transcript", "")

speech_to_text_engine = SpeechToTextTool()