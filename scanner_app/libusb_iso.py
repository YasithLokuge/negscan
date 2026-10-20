"""Isochronous USB reads, driven through libusb's async API with ctypes.

pyusb has no usable isochronous support, and this scanner sends its image data only on an
isochronous endpoint, so the transfers are built by hand here. The libusb library handle, context
and device handle are borrowed from the pyusb device, so the process still has just one libusb
context and pyusb keeps owning the device.

The one subtlety is the layout of `struct libusb_transfer`: its last member is the C99 flexible
array `iso_packet_desc[]`, which is 4-byte aligned and therefore starts at offset 60, while
`sizeof(struct libusb_transfer)` is padded up to 64 for pointer alignment. Writing the per-packet
lengths at offset 64 leaves libusb reading a zero length for packet 0, and macOS then rejects the
transfer with a bare `LIBUSB_ERROR_INVALID_PARAM`. `DESC_OFFSET` below is what keeps that right.
"""
import ctypes
import time

LIBUSB_TRANSFER_TYPE_ISOCHRONOUS = 1
LIBUSB_TRANSFER_COMPLETED = 0
LIBUSB_TRANSFER_CANCELLED = 3


class IsoPacketDesc(ctypes.Structure):
    _fields_ = [("length", ctypes.c_uint),
                ("actual_length", ctypes.c_uint),
                ("status", ctypes.c_int)]


TRANSFER_CB = ctypes.CFUNCTYPE(None, ctypes.c_void_p)


class Transfer(ctypes.Structure):
    _fields_ = [("dev_handle", ctypes.c_void_p),
                ("flags", ctypes.c_uint8),
                ("endpoint", ctypes.c_ubyte),
                ("type", ctypes.c_ubyte),
                ("timeout", ctypes.c_uint),
                ("status", ctypes.c_int),
                ("length", ctypes.c_int),
                ("actual_length", ctypes.c_int),
                ("callback", TRANSFER_CB),
                ("user_data", ctypes.c_void_p),
                ("buffer", ctypes.POINTER(ctypes.c_ubyte)),
                ("num_iso_packets", ctypes.c_int),
                ("iso_packet_desc", IsoPacketDesc * 0)]


class Timeval(ctypes.Structure):
    _fields_ = [("tv_sec", ctypes.c_long), ("tv_usec", ctypes.c_long)]


# The flexible array starts right after num_iso_packets, at the next 4-byte boundary, which is
# *not* sizeof(Transfer): that is padded up for pointer alignment. On 64-bit builds those are 60
# and 64. Derived rather than hard-coded so a 32-bit build stays correct.
DESC_OFFSET = Transfer.iso_packet_desc.offset
assert DESC_OFFSET == Transfer.num_iso_packets.offset + Transfer.num_iso_packets.size
assert DESC_OFFSET <= ctypes.sizeof(Transfer)


class IsoError(Exception):
    pass


class IsoStream:
    """A pool of isochronous transfers kept in flight on one IN endpoint.

    Call `poll()` in a loop: it runs libusb's event loop once and returns the packets that have
    arrived since the last call, in order, resubmitting each transfer as it is drained.

    `packets_per_transfer` must be a multiple of 8 for a high-speed device, because macOS schedules
    isochronous transfers in whole 1 ms frames of 8 microframes. The pool covers
    `transfers * packets_per_transfer` microframes of bus time; at 125 us each, the defaults give
    about 170 ms of slack before the host would have to drop data.
    """

    def __init__(self, dev, endpoint, packet_size, packets_per_transfer=96, transfers=14,
                 timeout_ms=4000):
        if packets_per_transfer % 8:
            raise ValueError("packets_per_transfer must be a multiple of 8")
        self.lib = dev.backend.lib
        self.ctx = dev.backend.ctx
        self.packet_size = packet_size
        self.packets_per_transfer = packets_per_transfer
        self._bind()

        handle = dev._ctx.handle.handle
        self._cb = TRANSFER_CB(self._on_done)   # C keeps this pointer: hold a reference
        self._done = []
        self._slots = {}
        self._closed = False
        self.in_flight = 0

        buf_len = packet_size * packets_per_transfer
        try:
            for _ in range(transfers):
                xfer = self.lib.libusb_alloc_transfer(packets_per_transfer)
                if not xfer:
                    raise MemoryError("libusb_alloc_transfer failed")
                buf = (ctypes.c_ubyte * buf_len)()
                t = xfer.contents
                t.dev_handle = handle
                t.endpoint = endpoint
                t.type = LIBUSB_TRANSFER_TYPE_ISOCHRONOUS
                t.timeout = timeout_ms
                t.buffer = ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte))
                t.length = buf_len
                t.num_iso_packets = packets_per_transfer
                t.callback = self._cb
                t.user_data = None
                for d in self._descs(xfer):
                    d.length = packet_size
                # cast('B'): a memoryview over a ctypes array reports format '<B', which cannot be
                # indexed; plain unsigned bytes can.
                self._slots[ctypes.addressof(t)] = (xfer, buf, memoryview(buf).cast('B'))
            for xfer, _, _ in self._slots.values():
                self._submit(xfer)
        except BaseException:
            self.close()
            raise

    def _bind(self):
        lib = self.lib
        lib.libusb_alloc_transfer.argtypes = [ctypes.c_int]
        lib.libusb_alloc_transfer.restype = ctypes.POINTER(Transfer)
        lib.libusb_submit_transfer.argtypes = [ctypes.POINTER(Transfer)]
        lib.libusb_submit_transfer.restype = ctypes.c_int
        lib.libusb_cancel_transfer.argtypes = [ctypes.POINTER(Transfer)]
        lib.libusb_cancel_transfer.restype = ctypes.c_int
        lib.libusb_free_transfer.argtypes = [ctypes.POINTER(Transfer)]
        lib.libusb_free_transfer.restype = None
        lib.libusb_handle_events_timeout.argtypes = [ctypes.c_void_p, ctypes.POINTER(Timeval)]
        lib.libusb_handle_events_timeout.restype = ctypes.c_int

    def _descs(self, xfer):
        base = ctypes.addressof(xfer.contents) + DESC_OFFSET
        return ctypes.cast(base, ctypes.POINTER(IsoPacketDesc * self.packets_per_transfer)).contents

    def _on_done(self, xfer_addr):
        # Called from inside libusb_handle_events on this thread. Keep it to a single append.
        self._done.append(xfer_addr)

    def _submit(self, xfer):
        rc = self.lib.libusb_submit_transfer(xfer)
        if rc < 0:
            raise IsoError(f"libusb_submit_transfer failed: {rc}")
        self.in_flight += 1

    def poll(self, timeout_s=0.002, resubmit=True):
        """Run the event loop once; return a list of `memoryview`s, one per packet that arrived.

        The views alias the transfer buffers and stay valid only until the next `poll()`, which is
        what keeps this allocation-free at 16 MB/s. Copy anything you need to keep.
        """
        tv = Timeval(int(timeout_s), int(timeout_s % 1 * 1_000_000))
        self.lib.libusb_handle_events_timeout(self.ctx, ctypes.byref(tv))
        out = []
        done, self._done = self._done, []
        for addr in done:
            self.in_flight -= 1
            xfer, _, view = self._slots[addr]
            for i, d in enumerate(self._descs(xfer)):
                if d.status == LIBUSB_TRANSFER_COMPLETED and d.actual_length:
                    start = i * self.packet_size
                    out.append(view[start:start + d.actual_length])
            if resubmit and not self._closed:
                self._submit(xfer)
        return out

    def close(self, drain_seconds=0.5):
        """Cancel everything in flight, wait for the callbacks, then free the transfers."""
        if self._closed:
            return
        self._closed = True
        for xfer, _, _ in self._slots.values():
            self.lib.libusb_cancel_transfer(xfer)
        deadline = time.time() + drain_seconds
        while self.in_flight > 0 and time.time() < deadline:
            self.poll(0.01, resubmit=False)
        for xfer, _, _ in self._slots.values():
            self.lib.libusb_free_transfer(xfer)
        self._slots.clear()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
