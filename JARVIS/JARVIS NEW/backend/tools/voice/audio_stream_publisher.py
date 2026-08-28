"""
backend/tools/voice/audio_stream_publisher.py
J.A.R.V.I.S. Continuous Audio Stream Publisher.
The missing half of the autonomous voice lifecycle: a background loop that captures
live microphone audio, performs simple energy-based voice-activity detection (VAD),
transcribes complete utterances through the central API engine, and publishes them
as canonical `system.audio.raw_utterance` events consumed by the WakeWordTool
continuous conversation loop.

Privacy boundaries honored:
  - Runs ONLY while voice listening is eligible (SessionManager authoritative:
    unlocked, active, wake-listening enabled, no emergency stop).
  - Raw audio is held in memory only; temporary WAV files are deleted immediately.
  - No transcripts are persisted by this component; they are dispatched as events.
"""

import os
import time
import uuid
import wave
import struct
import logging
import asyncio
from typing import Optional

from backend.infrastructure.event_bus import event_bus, JarvisEvent, EventType
from backend.infrastructure.api_engine import api_engine
from backend.observation.session_manager import session_manager
from backend.core.emergency_stop import emergency_stop
from backend.tools.files.file_security import get_default_output_dir
from backend.tools.voice.text_to_speech import text_to_speech_engine

logger = logging.getLogger("JARVIS.Tools.AudioStreamPublisher")

def utc_now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)


class AudioStreamPublisher:
    """
    Background microphone listener publishing transcribed utterances onto the EventBus.

    Lifecycle:
      start()  -> spawns the streaming loop task (idempotent).
      stop()   -> cancels the loop and releases resources.

    The loop only captures while `session_manager.is_voice_listening_eligible()`
    returns True — i.e., after the operator enables wake-word listening and the
    session is unlocked and active.
    """

    # Capture tuning (16 kHz mono PCM16)
    CHUNK = 1024
    RATE = 16000
    CHANNELS = 1
    SAMPLE_WIDTH = 2

    # VAD tuning
    SILENCE_RMS_THRESHOLD = 500          # legacy fixed floor (kept as minimum guard)
    MIN_RMS_THRESHOLD = 300              # absolute floor for the adaptive onset threshold
    NOISE_FLOOR_MULTIPLIER = 2.4         # onset threshold = calibrated ambient floor * this
    ONSET_FRAMES = 3                     # consecutive loud frames required to start speech
    PRE_SPEECH_BUFFER_SECONDS = 0.5      # pre-roll kept before speech onset
    SILENCE_TAIL_SECONDS = 1.2           # trailing silence that ends an utterance
    MAX_UTTERANCE_SECONDS = 15.0         # hard cap per utterance
    CALIBRATION_SECONDS = 0.5            # ambient noise sampled before each capture
    ECHO_COOLDOWN_SECONDS = 2.0          # suppress mic for this long after JARVIS stops speaking
    MIN_UTTERANCE_SECONDS = 0.6          # discard sub-600ms captures (noise spikes → "so" transcripts)

    def __init__(self):
        self.is_running: bool = False
        self._loop_task: Optional[asyncio.Task] = None
        self._consecutive_failures: int = 0

    async def start(self):
        """Starts the continuous audio publisher loop (idempotent)."""
        if not self.is_running:
            self.is_running = True
            self._loop_task = asyncio.create_task(self._stream_loop())
            logger.info("AudioStreamPublisher started. Awaiting voice-listening eligibility.")

    async def stop(self):
        """Gracefully halts the publisher."""
        self.is_running = False
        if self._loop_task:
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass
            self._loop_task = None
        logger.info("AudioStreamPublisher stopped.")

    def _is_eligible(self) -> bool:
        """Synchronous eligibility snapshot (emergency stop + session state flags)."""
        try:
            emergency_stop.assert_system_running()
        except Exception:
            return False
        return True

    def _echo_suppressed(self) -> bool:
        """
        True while JARVIS is generating/playing audio, or during the short reverb
        tail after it stops speaking. Used to keep JARVIS's own voice from being
        transcribed back as a user command (audio feedback loop).
        """
        if getattr(text_to_speech_engine, "is_speaking", False):
            return True
        last_end = getattr(text_to_speech_engine, "_last_speech_end", 0.0)
        return (time.time() - last_end) < self.ECHO_COOLDOWN_SECONDS

    async def _stream_loop(self):
        """Main loop: wait for eligibility, then capture→transcribe→publish utterances."""
        while self.is_running:
            try:
                if not self._is_eligible():
                    await asyncio.sleep(1.0)
                    continue

                if not await session_manager.is_voice_listening_eligible():
                    await asyncio.sleep(0.5)
                    continue

                # Echo guard: never start a capture while JARVIS is generating/playing
                # audio (or during the short reverb tail after it stops), otherwise the
                # microphone transcribes JARVIS's own voice and creates phantom commands.
                if self._echo_suppressed():
                    await asyncio.sleep(0.5)
                    continue

                transcript = await self._capture_and_transcribe_utterance()
                if transcript and transcript.strip():
                    await self._publish_utterance(transcript.strip())
                    self._consecutive_failures = 0
                else:
                    self._consecutive_failures = 0

            except asyncio.CancelledError:
                break
            except Exception as e:
                self._consecutive_failures += 1
                logger.error(f"Audio stream loop error ({self._consecutive_failures} consecutive): {e}")
                # Back off progressively to avoid hot-looping on hardware faults
                await asyncio.sleep(min(2.0 * self._consecutive_failures, 15.0))

    async def _capture_and_transcribe_utterance(self) -> str:
        """
        Captures one utterance from the microphone using energy-based VAD,
        writes it to a temporary WAV inside the allowed boundary, transcribes via
        the API Engine, cleans up, and returns the transcript text ('' if silent).
        """
        temp_path = os.path.join(
            get_default_output_dir(os.path.join("output", "audio_in")),
            f"stream_{uuid.uuid4().hex[:8]}.wav"
        )

        try:
            captured = await asyncio.to_thread(
                self._record_utterance_sync, temp_path
            )
            if not captured or os.path.getsize(temp_path) <= 44:
                return ""

            # Post-capture echo guard: if JARVIS began generating/playing audio
            # while this utterance was being recorded, the capture contains
            # JARVIS's own voice. Discard it instead of transcribing self-heard
            # audio back as a phantom user command.
            if self._echo_suppressed():
                logger.debug("Discarded capture: TTS playback started mid-capture (echo guard).")
                return ""

            with open(temp_path, "rb") as f:
                audio_bytes = f.read()

            result = await api_engine.transcribe_audio(audio_bytes=audio_bytes)
            if isinstance(result, dict):
                return str(result.get("text") or result.get("response") or "")
            return str(result or "")
        finally:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception as cleanup_err:
                    logger.warning(f"Failed to clean up stream audio file: {cleanup_err}")

    def _record_utterance_sync(self, output_path: str) -> bool:
        """
        Blocking PyAudio capture of a single utterance with VAD:
          - Maintains a small pre-speech ring buffer.
          - Speech onset = RMS above threshold.
          - Utterance ends after SILENCE_TAIL_SECONDS of quiet or MAX_UTTERANCE_SECONDS.
        Returns True if any speech was captured.
        """
        try:
            import pyaudio
        except ImportError:
            logger.debug("PyAudio unavailable; audio publisher idle.")
            return False

        p = pyaudio.PyAudio()
        frames_per_second = int(self.RATE / self.CHUNK)
        pre_buffer_limit = int(frames_per_second * self.PRE_SPEECH_BUFFER_SECONDS)
        silence_tail_limit = int(frames_per_second * self.SILENCE_TAIL_SECONDS)
        max_frames = int(frames_per_second * self.MAX_UTTERANCE_SECONDS)

        try:
            stream = p.open(
                format=pyaudio.paInt16,
                channels=self.CHANNELS,
                rate=self.RATE,
                input=True,
                frames_per_buffer=self.CHUNK,
            )
        except Exception as stream_err:
            p.terminate()
            raise RuntimeError(f"Failed to open microphone input stream: {stream_err}")

        # --- Adaptive ambient-noise calibration ---
        # A fixed RMS threshold fails across rooms/mics: too high misses speech
        # entirely, too low transcribes ambient noise. Sample the ambient floor
        # briefly before each capture and derive thresholds from it.
        calibration_frames = max(1, int(frames_per_second * self.CALIBRATION_SECONDS))
        ambient_total = 0.0
        try:
            for _ in range(calibration_frames):
                cal_data = stream.read(self.CHUNK, exception_on_overflow=False)
                ambient_total += self._compute_rms(cal_data)
            ambient_floor = ambient_total / calibration_frames
        except Exception as cal_err:
            ambient_floor = 0.0
            logger.warning(f"Microphone calibration failed; using fixed threshold: {cal_err}")

        onset_threshold = max(
            self.MIN_RMS_THRESHOLD,
            ambient_floor * self.NOISE_FLOOR_MULTIPLIER,
        )
        # Hysteresis: an utterance ends on a much quieter level than it started,
        # so soft trailing syllables are not cut off mid-word.
        tail_threshold = max(self.MIN_RMS_THRESHOLD * 0.6, onset_threshold * 0.5)
        logger.debug(
            f"VAD calibrated: ambient={ambient_floor:.0f} "
            f"onset={onset_threshold:.0f} tail={tail_threshold:.0f}"
        )

        speech_detected = False
        collected_frames: list[bytes] = []
        pre_buffer: list[bytes] = []
        consecutive_silent = 0
        loud_streak = 0
        total_frames = 0

        try:
            while total_frames < max_frames:
                data = stream.read(self.CHUNK, exception_on_overflow=False)
                total_frames += 1

                rms = self._compute_rms(data)

                # Echo guard INSIDE the capture loop: a capture can be open and
                # recording when JARVIS begins to reply. Discard any frames that
                # arrive while JARVIS is speaking or within the reverb cooldown,
                # so an open capture cannot swallow JARVIS's own voice.
                if text_to_speech_engine.is_speaking or (
                    (time.time() - getattr(text_to_speech_engine, "_last_speech_end", 0.0))
                    < self.ECHO_COOLDOWN_SECONDS
                ):
                    loud_streak = 0
                    continue

                if not speech_detected:
                    pre_buffer.append(data)
                    if len(pre_buffer) > pre_buffer_limit:
                        pre_buffer.pop(0)

                    if rms >= onset_threshold:
                        # Require a few consecutive loud frames so single noise
                        # blips (door slams, clicks) don't open an utterance.
                        loud_streak += 1
                        if loud_streak >= self.ONSET_FRAMES:
                            speech_detected = True
                            collected_frames.extend(pre_buffer)
                            pre_buffer.clear()
                            consecutive_silent = 0
                            loud_streak = 0
                    else:
                        loud_streak = 0
                else:
                    collected_frames.append(data)
                    if rms < tail_threshold:
                        consecutive_silent += 1
                        if consecutive_silent >= silence_tail_limit:
                            break
                    else:
                        consecutive_silent = 0
        finally:
            stream.stop_stream()
            stream.close()
            p.terminate()

        if not speech_detected or not collected_frames:
            return False

        # Reject captures that are far too short to contain real speech. Long
        # silence tails previously let short ambient noise spikes (which clear the
        # onset frame count) be shipped to the STT provider, producing endless
        # garbage "so"/"." transcripts that wasted API calls.
        utterance_seconds = len(collected_frames) / frames_per_second
        if utterance_seconds < self.MIN_UTTERANCE_SECONDS:
            logger.debug(f"Discarded too-short utterance capture ({utterance_seconds:.2f}s).")
            return False

        with wave.open(output_path, "wb") as wf:
            wf.setnchannels(self.CHANNELS)
            wf.setsampwidth(self.SAMPLE_WIDTH)
            wf.setframerate(self.RATE)
            wf.writeframes(b"".join(collected_frames))

        return True

    @staticmethod
    def _compute_rms(pcm_chunk: bytes) -> float:
        """Computes RMS amplitude of a 16-bit little-endian PCM chunk."""
        count = len(pcm_chunk) // 2
        if count == 0:
            return 0.0
        samples = struct.unpack(f"<{count}h", pcm_chunk[: count * 2])
        sum_squares = sum(s * s for s in samples)
        return (sum_squares / count) ** 0.5

    async def _publish_utterance(self, transcript: str):
        """Publishes a completed utterance transcript for WakeWord/session consumers."""
        # Ignore transcription noise (e.g. a bare '.' or 2-char artifacts like
        # "uh" from silence/noise captures): these previously spammed the Brain
        # pipeline and burned Groq STT calls every few seconds. Gate: a
        # multi-word phrase must be >= 4 chars; a single word must be >= 5 chars
        # (so real one-word commands like "screenshot" pass, junk like "uh"/"so"/
        # "go" is dropped before reaching any consumer).
        cleaned = (transcript or "").strip()
        if len(cleaned) < 2 or not any(ch.isalnum() for ch in cleaned):
            logger.debug("Discarded trivial utterance transcript.")
            return
        words = cleaned.split()
        if len(words) >= 2:
            if len(cleaned) < 4:
                logger.debug("Discarded too-short multi-word utterance transcript.")
                return
        elif len(cleaned) < 5:
            logger.debug("Discarded too-short single-word utterance transcript.")
            return

        event = JarvisEvent(
            event_type=EventType.VOICE,
            topic="system.audio.raw_utterance",
            timestamp=utc_now(),
            correlation_id=f"utter_{uuid.uuid4().hex[:8]}",
            task_id="VOICE_STREAM",
            source="AudioStreamPublisher",
            payload={"transcript": transcript}
        )
        await event_bus.publish(event)
        logger.info(f"Published utterance transcript ({len(transcript)} chars).")


audio_stream_publisher = AudioStreamPublisher()