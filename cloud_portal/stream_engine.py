#!/usr/bin/env python3
"""Hardware-Accelerated Cloud Gaming Stream Engine (NVENC + PulseAudio).

Captures Xorg display :1 with zero-latency NVIDIA NVENC encoding.
Streams raw Annex-B NAL units directly over asynchronous WebSockets to
browser WebCodecs clients.
"""

import os
import sys
import time
import asyncio
import subprocess
import logging

log = logging.getLogger("stream_engine")

class NVENCStreamEngine:
    def __init__(self, display=":1.0", width=1368, height=768, fps=60, bitrate_mbps=15):
        self.display = display
        self.width = width
        self.height = height
        self.fps = fps
        self.bitrate_mbps = bitrate_mbps
        self.proc = None
        self.audio_proc = None
        self.running = False
        self.subscribers = set()
        self._read_task = None
        self._audio_task = None

    def get_supported_codec(self):
        """Always use h264_nvenc for universal silicon hardware decoding on mobile and desktop."""
        return "h264_nvenc"

    def start_ffmpeg(self):
        codec = self.get_supported_codec()
        bitrate_k = int(self.bitrate_mbps * 1000)

        # Build low-latency FFmpeg NVENC pipeline
        # -preset p1: fastest single-pass NVENC
        # -tune ull: ultra-low-latency tuning
        # -zerolatency 1: no lookahead frame buffering
        # -flags -global_header: repeat SPS/PPS headers before every IDR frame
        cmd = [
            "ffmpeg",
            "-nostdin",
            "-loglevel", "warning",
            "-f", "x11grab",
            "-draw_mouse", "1",
            "-video_size", f"{self.width}x{self.height}",
            "-framerate", str(self.fps),
            "-i", self.display,
            "-c:v", codec,
            "-preset", "p1",
            "-tune", "ull",
            "-zerolatency", "1",
            "-rc", "cbr",
            "-b:v", f"{bitrate_k}k",
            "-maxrate", f"{int(bitrate_k * 1.2)}k",
            "-bufsize", f"{int(bitrate_k / 4)}k",
            "-g", "30",  # Keyframe every 0.5s for instant client pickup
            "-flags", "-global_header",
            "-f", "h264",
            "pipe:1"
        ]

        log.info(f"Starting NVENC stream engine: {' '.join(cmd)}")
        try:
            self.proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=1024 * 1024
            )
        except Exception as e:
            log.warning(f"Video capture pipeline could not be started: {e}")
            self.proc = None

        # Start PulseAudio Opus / PCM capture pipeline
        audio_cmd = [
            "ffmpeg",
            "-nostdin",
            "-loglevel", "warning",
            "-f", "pulse",
            "-i", "sink-sunshine-stereo.monitor",
            "-c:a", "libopus",
            "-b:a", "128k",
            "-frame_duration", "10",  # 10ms low-latency audio frames
            "-application", "lowdelay",
            "-f", "opus",
            "pipe:1"
        ]
        try:
            self.audio_proc = subprocess.Popen(
                audio_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=64 * 1024
            )
        except Exception as e:
            log.warning(f"Audio capture pipeline could not be started: {e}")

    async def _video_reader_loop(self):
        loop = asyncio.get_event_loop()
        buffer = bytearray()
        nal_delimiter = b"\x00\x00\x00\x01"
        sps_pps = bytearray()

        while self.running and self.proc and self.proc.poll() is None:
            # Read chunks in non-blocking thread
            chunk = await loop.run_in_executor(None, self.proc.stdout.read, 32768)
            if not chunk:
                await asyncio.sleep(0.002)
                continue

            buffer.extend(chunk)

            # Split into Annex-B NAL units
            while True:
                pos1 = buffer.find(nal_delimiter)
                if pos1 == -1:
                    break
                pos2 = buffer.find(nal_delimiter, pos1 + 4)
                if pos2 == -1:
                    # Incomplete frame, wait for next chunk
                    break

                nal = bytes(buffer[pos1:pos2])
                del buffer[:pos2]

                if len(nal) < 5:
                    continue

                nal_body = nal[4:]
                nal_type = nal_body[0] & 0x1f

                # SPS (7) or PPS (8): Buffer parameter sets
                if nal_type in (7, 8):
                    sps_pps.extend(nal)
                # IDR Keyframe (5): Prepend SPS & PPS to form complete standalone Access Unit
                elif nal_type == 5:
                    keyframe_payload = bytes(sps_pps) + nal
                    sps_pps.clear()
                    # Packet format: [0x01, is_keyframe(1), data...]
                    packet = bytes([0x01, 1]) + keyframe_payload
                    self._broadcast(packet)
                # Non-IDR Slice (1, P-Frame delta)
                elif nal_type == 1:
                    # Packet format: [0x01, is_keyframe(0), data...]
                    packet = bytes([0x01, 0]) + nal
                    self._broadcast(packet)

    async def _audio_reader_loop(self):
        loop = asyncio.get_event_loop()
        while self.running and self.audio_proc and self.audio_proc.poll() is None:
            chunk = await loop.run_in_executor(None, self.audio_proc.stdout.read, 960)
            if not chunk:
                await asyncio.sleep(0.005)
                continue

            # Packet format:
            # Byte 0: 0x02 (Audio Tag)
            # Bytes 1..: Raw Opus / PCM data
            packet = bytes([0x02]) + chunk
            self._broadcast(packet)

    def _broadcast(self, packet):
        dead = []
        for ws in self.subscribers:
            try:
                # ws.send_bytes is synchronous or queued
                asyncio.create_task(ws.send_bytes(packet))
            except Exception:
                dead.append(ws)
        for d in dead:
            self.subscribers.discard(d)

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
                self.proc.wait(timeout=2)
            except Exception:
                self.proc.kill()
            self.proc = None
        if self.audio_proc:
            try:
                self.audio_proc.terminate()
                self.audio_proc.wait(timeout=2)
            except Exception:
                self.audio_proc.kill()
            self.audio_proc = None


# Global stream engine singleton
engine = NVENCStreamEngine()
