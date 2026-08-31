"""Small OpenDMR ctypes wrapper for runtime parrot telemetry gain control.

The browser-DMR helper already deploys the same libopendmr.so on the HBlink host.
This module deliberately uses only the Python standard library and fails closed
so the existing pre-encoded AMBE telemetry path remains available as fallback.
"""
from __future__ import annotations

import ctypes
import os
from pathlib import Path

PCM_SAMPLES = 160
PCM_BYTES = PCM_SAMPLES * 2
AMBE_BYTES = 9


class ParrotCodecUnavailable(RuntimeError):
    pass


class ParrotCodecError(RuntimeError):
    pass


class OpenDMRParrotCodec:
    def __init__(self, library_path: str | None = None):
        candidates = [
            library_path or os.getenv("BROWSER_DMR_OPENDMR_LIB", ""),
            "/usr/local/lib/libopendmr.so",
            "/usr/lib/libopendmr.so",
        ]
        lib = None
        errors: list[str] = []
        for candidate in [value for value in candidates if value]:
            try:
                lib = ctypes.CDLL(str(Path(candidate)))
                self.library_path = candidate
                break
            except OSError as exc:
                errors.append(f"{candidate}: {exc}")
        if lib is None:
            raise ParrotCodecUnavailable(
                "OpenDMR shared library not available: " + "; ".join(errors)
            )
        self.lib = lib
        self._bind()
        self.enc = self.lib.opendmr_encoder_create()
        self.dec = self.lib.opendmr_decoder_create()
        if not self.enc or not self.dec:
            self.close()
            raise ParrotCodecUnavailable("OpenDMR could not create encoder/decoder state")

    def _bind(self) -> None:
        lib = self.lib
        lib.opendmr_encoder_create.restype = ctypes.c_void_p
        lib.opendmr_decoder_create.restype = ctypes.c_void_p
        lib.opendmr_encoder_destroy.argtypes = [ctypes.c_void_p]
        lib.opendmr_decoder_destroy.argtypes = [ctypes.c_void_p]
        lib.opendmr_encoder_reset.argtypes = [ctypes.c_void_p]
        lib.opendmr_decoder_reset.argtypes = [ctypes.c_void_p]
        lib.opendmr_encode.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_int16),
            ctypes.POINTER(ctypes.c_uint8),
        ]
        lib.opendmr_encode.restype = ctypes.c_bool
        lib.opendmr_decode.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.POINTER(ctypes.c_int16),
            ctypes.POINTER(ctypes.c_int),
        ]
        lib.opendmr_decode.restype = ctypes.c_bool
        lib.opendmr_version.restype = ctypes.c_char_p

    @property
    def version(self) -> str:
        raw = self.lib.opendmr_version()
        return raw.decode("utf-8", "replace") if raw else "unknown"

    def reset_encoder(self) -> None:
        self.lib.opendmr_encoder_reset(self.enc)

    def reset_decoder(self) -> None:
        self.lib.opendmr_decoder_reset(self.dec)

    def encode(self, pcm: bytes) -> bytes:
        if len(pcm) != PCM_BYTES:
            raise ValueError(f"PCM frame must be {PCM_BYTES} bytes")
        samples = (ctypes.c_int16 * PCM_SAMPLES).from_buffer_copy(pcm)
        out = (ctypes.c_uint8 * AMBE_BYTES)()
        if not self.lib.opendmr_encode(self.enc, samples, out):
            raise ParrotCodecError("OpenDMR encode failed")
        return bytes(out)

    def decode(self, ambe: bytes) -> bytes:
        if len(ambe) != AMBE_BYTES:
            raise ValueError(f"AMBE frame must be {AMBE_BYTES} bytes")
        src = (ctypes.c_uint8 * AMBE_BYTES).from_buffer_copy(ambe)
        pcm = (ctypes.c_int16 * PCM_SAMPLES)()
        errs = ctypes.c_int(0)
        if not self.lib.opendmr_decode(self.dec, src, pcm, ctypes.byref(errs)):
            raise ParrotCodecError("OpenDMR decode failed")
        return ctypes.string_at(ctypes.addressof(pcm), PCM_BYTES)

    def close(self) -> None:
        enc = getattr(self, "enc", None)
        dec = getattr(self, "dec", None)
        if enc:
            self.lib.opendmr_encoder_destroy(enc)
            self.enc = None
        if dec:
            self.lib.opendmr_decoder_destroy(dec)
            self.dec = None
