#!/usr/bin/env python3
"""Hardware-Accelerated Cloud Gaming Stream Engine (NVENC + PulseAudio).

Captures Xorg display :1 with zero-latency NVIDIA NVENC encoding.
Streams raw Annex-B NAL units & uncompressed 48kHz PCM audio directly
over asynchronous WebSockets to browser WebCodecs clients.
Enforces strict TCP backpressure frame-dropping to guarantee ~100ms ping.
"""

import os
import sys
import time
import asyncio
import subprocess
import logging

log = logging.getLogger("stream_engine")

class NVENCStreamEngine:
    def __init__(self, display=":1.0", width=1368, height=768, fps=60, bitrate_mbps=4.0, codec="h264"):
        self.display = display
        self.width = width
        self.height = height
        self.fps = fps
        self.bitrate_mbps = float(bitrate_mbps)
        self.codec = codec if codec in ("h264", "av1") else "h264"
        self.proc = None
        self.audio_proc = None
        self.running = False
        self.subscribers = set()
        self._read_task = None
        self._audio_task = None

    def get_pulse_monitor(self):
        """Find the active PulseAudio monitor source."""
        env = os.environ.copy()
        env["PULSE_SERVER"] = "unix:/run/user/1001/pulse/native"
        env["PULSE_COOKIE"] = "/home/user/.config/pulse/cookie"
        try:
            res = subprocess.run(
                ["pactl", "list", "short", "sources"],
                env=env,
                capture_output=True,
                text=True,
                timeout=2
            )
            for line in res.stdout.splitlines():
                parts = line.strip().split()
                if len(parts) >= 2 and "monitor" in parts[1]:
                    return parts[1]
        except Exception:
            pass
        return "auto_null.monitor"

    def start_ffmpeg(self):
        bitrate_k = int(self.bitrate_mbps * 1000)

        # Base zero-latency flags
        cmd = [
            "ffmpeg",
            "-nostdin",
            "-loglevel", "warning",
            "-fflags", "+nobuffer+flush_packets",
            "-flags", "low_delay",
            "-probesize", "32",
            "-analyzeduration", "0",
            "-f", "x11grab",
            "-draw_mouse", "1",
            "-video_size", f"{self.width}x{self.height}",
            "-framerate", str(self.fps),
            "-i", self.display
        ]

        # Codec selection: AV1 vs H264
        if self.codec == "av1":
            cmd += [
                "-c:v", "av1_nvenc",
                "-preset", "p1",
                "-tune", "ull",
                "-zerolatency", "1",
                "-delay", "0",
                "-rc", "cbr",
                "-b:v", f"{bitrate_k}k",
                "-maxrate", f"{int(bitrate_k * 1.15)}k",
                "-bufsize", f"{int(bitrate_k / 8)}k",
                "-g", "30",
                "-f", "obu",
                "pipe:1"
            ]
        else:
            cmd += [
                "-c:v", "h264_nvenc",
                "-preset", "p1",
                "-tune", "ull",
                "-zerolatency", "1",
                "-delay", "0",
                "-rc", "cbr",
                "-b:v", f"{bitrate_k}k",
                "-maxrate", f"{int(bitrate_k * 1.15)}k",
                "-bufsize", f"{int(bitrate_k / 8)}k",
                "-g", "30",
                "-flags", "-global_header",
                "-f", "h264",
                "pipe:1"
            ]

        log.info(f"Starting NVENC ({self.codec}) zero-latency engine: {' '.join(cmd)}")
        try:
            self.proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0  # Direct unbuffered pipe
            )
        except Exception as e:
            log.warning(f"Video capture pipeline could not be started: {e}")
            self.proc = None

        # PulseAudio stereo monitor capture into raw 48kHz 16-bit PCM (Zero Delay)
        pulse_source = self.get_pulse_monitor()
        pulse_env = os.environ.copy()
        pulse_env["PULSE_SERVER"] = "unix:/run/user/1001/pulse/native"
        pulse_env["PULSE_COOKIE"] = "/home/user/.config/pulse/cookie"

        audio_cmd = [
            "ffmpeg",
            "-nostdin",
            "-loglevel", "warning",
            "-fflags", "+nobuffer+flush_packets",
            "-flags", "low_delay",
            "-probesize", "32",
            "-analyzeduration", "0",
            "-f", "pulse",
            "-i", pulse_source,
            "-f", "s16le",
            "-ar", "48000",
            "-ac", "2",
            "pipe:1"
        ]
        try:
            self.audio_proc = subprocess.Popen(
                audio_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=pulse_env,
                bufsize=0
            )
            log.info(f"PulseAudio PCM audio pipeline active on: {pulse_source}")
        except Exception as e:
            log.warning(f"Audio capture pipeline could not be started: {e}")
            self.audio_proc = None

    async def _video_reader_loop(self):
        loop = asyncio.get_event_loop()
        buffer = bytearray()
        nal_delimiter = b"\x00\x00\x00\x01"
        sps_pps = bytearray()
        fd = self.proc.stdout.fileno()

        while self.running and self.proc and self.proc.poll() is None:
            try:
                # os.read returns immediately with whatever bytes are in kernel pipe
                chunk = await loop.run_in_executor(None, os.read, fd, 65536)
                if not chunk:
                    await asyncio.sleep(0.001)
                    continue

                buffer.extend(chunk)

                while True:
                    pos1 = buffer.find(nal_delimiter)
                    if pos1 == -1:
                        break
                    pos2 = buffer.find(nal_delimiter, pos1 + 4)
                    if pos2 == -1:
                        break

                    nal = bytes(buffer[pos1:pos2])
                    del buffer[:pos2]

                    if len(nal) < 5:
                        continue

                    nal_body = nal[4:]
                    nal_type = nal_body[0] & 0x1f

                    # SPS (7) or PPS (8): Save parameter sets
                    if nal_type in (7, 8):
                        sps_pps.extend(nal)
                    # IDR Keyframe (5): Prepend SPS & PPS to form complete Access Unit
                    elif nal_type == 5:
                        keyframe_payload = bytes(sps_pps) + nal
                        sps_pps.clear()
                        packet = bytes([0x01, 1]) + keyframe_payload
                        self._broadcast(packet, is_keyframe=True)
                    # P-frame delta slice (1)
                    elif nal_type == 1:
                        packet = bytes([0x01, 0]) + nal
                        self._broadcast(packet, is_keyframe=False)

            except Exception as e:
                log.warning(f"Video reader loop error: {e}")
                await asyncio.sleep(0.005)

    async def _audio_reader_loop(self):
        loop = asyncio.get_event_loop()
        # 1920 bytes = 10ms of 48000Hz 16-bit stereo PCM
        chunk_size = 1920
        fd = self.audio_proc.stdout.fileno()

        while self.running and self.audio_proc and self.audio_proc.poll() is None:
            try:
                chunk = await loop.run_in_executor(None, os.read, fd, chunk_size)
                if not chunk:
                    await asyncio.sleep(0.005)
                    continue

                # Tag 0x02: Audio PCM packet
                packet = bytes([0x02]) + chunk
                self._broadcast(packet, is_keyframe=False, is_audio=True)
            except Exception as e:
                log.warning(f"Audio reader loop error: {e}")
                await asyncio.sleep(0.01)

    def _broadcast(self, packet, is_keyframe=False, is_audio=False):
        """Broadcasts packets with anti-lag TCP backpressure frame dropping."""
        dead = []
        for ws in self.subscribers:
            try:
                if ws.closed:
                    dead.append(ws)
                    continue

                # Native TCP write buffer backpressure check
                writer = getattr(ws, "_writer", None)
                transport = getattr(writer, "transport", None) if writer else None
                if transport and not transport.is_closing():
                    buf_size = transport.get_write_buffer_size()
                    # If socket write buffer exceeds 32KB (~70ms latency), drop delta/audio frames
                    if buf_size > 32768:
                        if not is_keyframe:
                            continue  # Drop packet to keep latency ~100ms!

                asyncio.create_task(ws.send_bytes(packet))
            except Exception:
                dead.append(ws)

        for d in dead:
            self.subscribers.discard(d)

    def set_config(self, bitrate_mbps=None, codec=None):
        """Update live stream config dynamically."""
        restart = False
        if bitrate_mbps is not None:
            try:
                new_br = float(bitrate_mbps)
                if 1.0 <= new_br <= 50.0 and new_br != self.bitrate_mbps:
                    self.bitrate_mbps = new_br
                    log.info(f"Updated live bitrate to {self.bitrate_mbps} Mbps")
            except Exception:
                pass

        if codec and codec in ("h264", "av1") and codec != self.codec:
            self.codec = codec
            restart = True
            log.info(f"Switched live video codec to {self.codec}")

        if restart and self.running:
            log.info("Restarting FFmpeg encoder with new codec settings...")
            self.stop()
            self.running = True
            self.start_ffmpeg()
            self._read_task = asyncio.create_task(self._video_reader_loop())
            if self.audio_proc:
                self._audio_task = asyncio.create_task(self._audio_reader_loop())

    def add_client(self, ws):
        self.subscribers.add(ws)
        if not self.running:
            self.running = True
            self.start_ffmpeg()
            self._read_task = asyncio.create_task(self._video_reader_loop())
            if self.audio_proc:
                self._audio_task = asyncio.create_task(self._audio_reader_loop())
        log.info(f"Client joined. Active subscribers: {len(self.subscribers)}")

    def remove_client(self, ws):
        self.subscribers.discard(ws)
        log.info(f"Client left. Active subscribers: {len(self.subscribers)}")
        if not self.subscribers and self.running:
            log.info("No active clients remaining. Pausing video encode engine...")
            self.stop()

    def stop(self):
        self.running = False
        if self._read_task:
            self._read_task.cancel()
        if self._audio_task:
            self._audio_task.cancel()
        if self.proc:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=1)
            except Exception:
                self.proc.kill()
            self.proc = None
        if self.audio_proc:
            try:
                self.audio_proc.terminate()
                self.audio_proc.wait(timeout=1)
            except Exception:
                self.audio_proc.kill()
            self.audio_proc = None

# Global stream engine singleton (Default: 4.0 Mbps CBR, H.264 ULL)
engine = NVENCStreamEngine(bitrate_mbps=4.0, codec="h264")
