"""Lock-free Single-Producer Single-Consumer (SPSC) circular audio ring buffer."""

from __future__ import annotations

import numpy as np


class AudioRingBuffer:
    """Circular ring buffer for real-time audio streams with zero allocation in RT callbacks."""

    def __init__(self, capacity_frames: int = 4096, channels: int = 2):
        """
        Initialize the circular buffer.
        
        Args:
            capacity_frames: Capacity in frames. Must be a power of two.
            channels: Number of audio channels (e.g. 2 for stereo).
        """
        # Ensure capacity is power of two for fast bitwise wrapping
        power = 1
        while power < capacity_frames:
            power <<= 1
        self.capacity = power
        self.mask = self.capacity - 1
        self.channels = channels

        # Pre-allocated contiguous float32 buffer
        self.buffer = np.zeros((self.capacity, self.channels), dtype=np.float32)
        self.write_pos = 0
        self.read_pos = 0

    def clear(self) -> None:
        """Reset read and write pointers and zero the buffer."""
        self.read_pos = 0
        self.write_pos = 0
        self.buffer.fill(0.0)

    @property
    def available_read(self) -> int:
        """Number of frames available to read."""
        return max(0, self.write_pos - self.read_pos)

    @property
    def available_write(self) -> int:
        """Number of frames available to write before overrun."""
        return self.capacity - self.available_read

    def write(self, data: np.ndarray) -> int:
        """
        Write audio frames to the buffer. Drops oldest audio if buffer overruns.
        
        Zero allocations performed in this method.
        """
        frames = len(data)
        if frames == 0:
            return 0

        # If data exceeds buffer capacity, only take the latest capacity frames
        if frames > self.capacity:
            data = data[-self.capacity:]
            frames = self.capacity

        # Overrun policy: if writing would exceed capacity, advance read_pos to drop oldest
        avail = self.available_read
        if avail + frames > self.capacity:
            self.read_pos = self.write_pos - (self.capacity - frames)

        w = self.write_pos & self.mask
        part1 = self.capacity - w
        if part1 >= frames:
            self.buffer[w:w + frames] = data
        else:
            self.buffer[w:self.capacity] = data[:part1]
            self.buffer[:frames - part1] = data[part1:]

        self.write_pos += frames
        return frames

    def read(self, out: np.ndarray, num_frames: int) -> int:
        """
        Read up to num_frames into pre-allocated out buffer.
        Pads remainder with zeros if underflow occurs.
        
        Zero allocations performed in this method.
        """
        avail = self.available_read
        if avail <= 0:
            out[:num_frames].fill(0.0)
            return 0

        to_read = min(num_frames, avail)
        r = self.read_pos & self.mask
        part1 = self.capacity - r

        if part1 >= to_read:
            out[:to_read] = self.buffer[r:r + to_read]
        else:
            out[:part1] = self.buffer[r:self.capacity]
            out[part1:to_read] = self.buffer[:to_read - part1]

        if to_read < num_frames:
            out[to_read:num_frames].fill(0.0)

        self.read_pos += to_read
        return to_read
