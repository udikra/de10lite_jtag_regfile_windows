"""Direct JTAG access to a classic Altera USB-Blaster (VID:PID 09FB:6001)."""

from __future__ import annotations

import math
import random
import threading
import time
from typing import Optional

import usb.core
import usb.util


VID = 0x09FB
PID = 0x6001
MAX10_IR_BITS = 10
MAX10_USER1_IR = 0x00E
MAX10_USER0_IR = 0x00C
VIRTUAL_IR_BITS = 5
VIRTUAL_IR_SELECTOR = 0b10000  # node address 1 above the 4-bit node IR field
VIRTUAL_IR_REG = 0  # 40-bit register-file command frame
VIRTUAL_IR_BUF_STORE = 1  # buffer write stream
VIRTUAL_IR_BUF_LOAD = 2  # buffer read stream
REGISTER_COUNT = 16
JWSR_COUNT = 8  # addresses 0..7: JTAG writes, system reads; 8..15: system writes
REGISTER_DATA_BITS = 32
DR_BITS = 40
NOP_FRAME = 1 << 39
DONE_BIT = 1 << 37
OVERRUN_BIT = 1 << 38
READ_RETRIES = 8
BUFFER_WORDS = 4096  # 2**BUF_ADDR_BITS in top.sv
BUFFER_BYTES = BUFFER_WORDS * 4

# System access engine (sys_access.sv): registers and SA_CMD fields
SA_ADDR_REG = 5
SA_DATA_REG = 6
SA_CMD_REG = 7
SA_RDATA_REG = 14
SA_STATUS_REG = 15
SA_OP_ABORT = 0
SA_OP_READ = 1
SA_OP_WRITE = 2
SA_OP_BLOCK_WRITE = 3
SA_OP_BLOCK_READ = 4
SA_STATUS_BUSY = 1 << 0
SA_STATUS_ABORTED = 1 << 1
SA_TIMEOUT_S = 1.0
WORD_MASK = 0xFFFFFFFF

# Each byte-shift command carries up to 63 bytes. With 62, the header, data and
# one idle byte fill a 64-byte packet exactly, so every header stays aligned.
_BUFFER_CHUNK_BYTES = 62
# TDO bytes per captured register frame: 4 byte-shifted bytes + 8 bit-banged bits
_REG_TDO_BYTES = DR_BITS // 8 - 1 + 8

_LED = 0x20
_NCE = 0x04
_NCS = 0x08
_TDI = 0x10
_TMS = 0x02
_TCK = 0x01
_READ = 0x40
_SHIFT_MODE = 0x80
_READ_TDO = 0x01


class UsbBlasterError(RuntimeError):
    """Raised when the USB-Blaster cannot perform a JTAG transaction."""


def _validate_address(address: int) -> None:
    if not 0 <= address < REGISTER_COUNT:
        raise ValueError(f"address must be in range 0..{REGISTER_COUNT - 1}")


def _validate_word(value: int, name: str) -> None:
    if not 0 <= value <= WORD_MASK:
        raise ValueError(f"{name} must be an unsigned 32-bit integer")


def encode_request(address: int, write: bool, data: int = 0) -> int:
    """Encode [nop:1][reserved:2][data:32][write:1][address:4], LSB shifted first."""
    _validate_address(address)
    if not 0 <= data < (1 << REGISTER_DATA_BITS):
        raise ValueError("data must be an unsigned 32-bit integer")
    return (data << 5) | (int(write) << 4) | address


def decode_response(frame: int) -> int:
    """Extract the 32-bit read-data field from a 40-bit response frame."""
    if not 0 <= frame < (1 << DR_BITS):
        raise ValueError("response frame must be an unsigned 40-bit integer")
    return (frame >> 5) & 0xFFFFFFFF


class UsbBlaster:
    """Bit-bang JTAG through the original FT245-based USB-Blaster protocol."""

    def __init__(
        self,
        device=None,
        *,
        user1_ir: int = MAX10_USER1_IR,
        user0_ir: int = MAX10_USER0_IR,
        virtual_ir_selector: int = VIRTUAL_IR_SELECTOR,
        ir_bits: int = MAX10_IR_BITS,
        timeout_ms: int = 1000,
    ) -> None:
        if not 0 <= user1_ir < (1 << ir_bits):
            raise ValueError("USER1 instruction does not fit the configured IR width")
        if not 0 <= user0_ir < (1 << ir_bits):
            raise ValueError("USER0 instruction does not fit the configured IR width")
        if not 0 <= virtual_ir_selector < (1 << VIRTUAL_IR_BITS):
            raise ValueError("virtual IR selector must fit in five bits")
        self.user1_ir = user1_ir
        self.user0_ir = user0_ir
        self.virtual_ir_selector = virtual_ir_selector
        self.ir_bits = ir_bits
        self.timeout_ms = timeout_ms
        self._claimed_interface = None
        self._selected_virtual_ir = None
        # Random start, so a stale tag left by an earlier session rarely matches.
        self._sa_tag = random.randrange(1, 256)

        if device is None:
            device = self._find_device()
        self.device = device
        self._configure_device()
        self._reset_tap()

    @staticmethod
    def _find_device():
        backends = []
        try:
            import libusb_package
            import usb.backend.libusb1

            backend = usb.backend.libusb1.get_backend(
                find_library=libusb_package.find_library
            )
            if backend is not None:
                backends.append(("libusb-1.0", backend))
        except (ImportError, OSError):
            pass

        try:
            import usb.backend.libusb0

            backend = usb.backend.libusb0.get_backend()
            if backend is not None:
                backends.append(("libusb-0.1", backend))
        except (ImportError, OSError):
            pass

        if not backends:
            raise UsbBlasterError(
                "No libusb backend loaded. Install requirements.txt or install a "
                "Zadig libusb-win32 driver."
            )

        device_found = False
        access_errors = []
        for backend_name, backend in backends:
            device = usb.core.find(idVendor=VID, idProduct=PID, backend=backend)
            if device is None:
                continue
            device_found = True
            try:
                device.get_active_configuration()
            except (NotImplementedError, OSError, usb.core.USBError) as exc:
                access_errors.append(f"{backend_name}: {exc}")
            else:
                return device

        if not device_found:
            raise UsbBlasterError("USB-Blaster 09FB:6001 was not found.")
        raise UsbBlasterError(
            "PyUSB found the USB-Blaster but could not open it. Bind the device to "
            "WinUSB or libusb-win32, and close Quartus/JTAG tools. "
            + "; ".join(access_errors)
        )

    def _configure_device(self) -> None:
        try:
            if self.device.get_active_configuration() is None:
                self.device.set_configuration()
            configuration = self.device.get_active_configuration()
            selected = None
            for interface in configuration:
                bulk_in = None
                bulk_out = None
                for endpoint in interface:
                    if usb.util.endpoint_type(endpoint.bmAttributes) != usb.util.ENDPOINT_TYPE_BULK:
                        continue
                    if usb.util.endpoint_direction(endpoint.bEndpointAddress) == usb.util.ENDPOINT_IN:
                        bulk_in = endpoint
                    else:
                        bulk_out = endpoint
                if bulk_in is not None and bulk_out is not None:
                    selected = (interface, bulk_in, bulk_out)
                    break
            if selected is None:
                raise UsbBlasterError("No interface with bulk IN and OUT endpoints was found.")

            interface, self._endpoint_in, self._endpoint_out = selected
            self._claimed_interface = interface.bInterfaceNumber
            usb.util.claim_interface(self.device, self._claimed_interface)
            # A transfer aborted by an earlier session can leave an endpoint
            # halted, and then every read times out until the halt is cleared.
            self._endpoint_in.clear_halt()
            self._endpoint_out.clear_halt()
            self._packet_size = self._endpoint_in.wMaxPacketSize
            if self._packet_size <= 2:
                raise UsbBlasterError("USB-Blaster bulk packet size is invalid.")

            self._ftdi_control(0x00, 0)
            self._ftdi_control(0x09, 2)
            self._ftdi_control(0x00, 1)
            self._ftdi_control(0x00, 2)
        except UsbBlasterError:
            self.close()
            raise
        except (usb.core.USBError, ValueError) as exc:
            self.close()
            raise UsbBlasterError(
                "Could not claim USB-Blaster. Confirm WinUSB is installed and no Quartus "
                "process is using the device."
            ) from exc

    def _ftdi_control(self, request: int, value: int) -> None:
        self.device.ctrl_transfer(
            0x40,
            request,
            value,
            self._claimed_interface,
            None,
            timeout=self.timeout_ms,
        )

    @staticmethod
    def _bitbang_byte(tms: int, tdi: int, tck: int, read: bool = False) -> int:
        value = _LED | _NCE | _NCS
        value |= _TMS if tms else 0
        value |= _TDI if tdi else 0
        value |= _TCK if tck else 0
        value |= _READ if read else 0
        return value

    @classmethod
    def _append_clock(
        cls, output: bytearray, tms: int, tdi: int, read: bool = False
    ) -> None:
        output.append(cls._bitbang_byte(tms, tdi, 0))
        output.append(cls._bitbang_byte(tms, tdi, 1, read))

    def _append_ir_scan(self, output: bytearray, instruction: int) -> None:
        for tms in (1, 1, 0, 0):
            self._append_clock(output, tms, 0)
        for bit_index in range(self.ir_bits):
            bit = (instruction >> bit_index) & 1
            self._append_clock(output, int(bit_index == self.ir_bits - 1), bit)
        self._append_clock(output, 1, 0)
        self._append_clock(output, 0, 0)

    def _append_dr_scan(self, output: bytearray, value: int, bit_count: int) -> None:
        for tms in (1, 0, 0):
            self._append_clock(output, tms, 0)
        for bit_index in range(bit_count):
            bit = (value >> bit_index) & 1
            self._append_clock(output, int(bit_index == bit_count - 1), bit)
        self._append_clock(output, 1, 0)
        self._append_clock(output, 0, 0)

    def _read_ftdi_payload(self, payload_length: int) -> bytes:
        payload = bytearray()
        deadline = time.monotonic() + self.timeout_ms / 1000
        while len(payload) < payload_length:
            remaining_time = deadline - time.monotonic()
            if remaining_time <= 0:
                raise UsbBlasterError(
                    f"Timed out waiting for TDO data ({len(payload)}/{payload_length} bytes)."
                )
            remaining = payload_length - len(payload)
            payload_per_packet = self._packet_size - 2
            packet_count = math.ceil(remaining / payload_per_packet)
            raw_length = remaining + 2 * packet_count
            read_timeout = max(1, int(remaining_time * 1000))
            try:
                raw = bytes(self._endpoint_in.read(raw_length, timeout=read_timeout))
            except usb.core.USBError as exc:
                raise UsbBlasterError(
                    f"Timed out or failed waiting for TDO data "
                    f"({len(payload)}/{payload_length} bytes): {exc}"
                ) from exc
            for offset in range(0, len(raw), self._packet_size):
                packet = raw[offset : offset + self._packet_size]
                if len(packet) > 2:
                    payload.extend(packet[2:])
        if len(payload) < payload_length:
            raise UsbBlasterError("USB-Blaster returned a short TDO response.")
        return bytes(payload[:payload_length])

    def _exchange(self, output: bytearray, read_length: int = 0) -> bytes:
        """Send output and return read_length TDO bytes (see _exchange_overlapped)."""
        if read_length:
            return self._exchange_overlapped(output, read_length)
        try:
            self._endpoint_out.write(output, timeout=self.timeout_ms)
            return b""
        except usb.core.USBError as exc:
            self._selected_virtual_ir = None
            raise UsbBlasterError(f"USB-Blaster bulk transfer failed: {exc}") from exc
        except BaseException:
            # The stream may have stopped part way; reselect next time.
            self._selected_virtual_ir = None
            raise

    def _reset_tap(self) -> None:
        output = bytearray()
        for _ in range(5):
            self._append_clock(output, 1, 0)
        self._append_clock(output, 0, 0)
        self._exchange(output)

    def _append_select(self, output: bytearray, virtual_ir: int) -> None:
        """Append the scans selecting `virtual_ir`, unless it is already selected.

        The selection is recorded as made; an exchange that fails clears it.
        """
        if self._selected_virtual_ir == virtual_ir:
            return
        self._append_ir_scan(output, self.user1_ir)
        self._append_dr_scan(
            output, self.virtual_ir_selector | virtual_ir, VIRTUAL_IR_BITS
        )
        self._append_ir_scan(output, self.user0_ir)
        self._selected_virtual_ir = virtual_ir

    def _select_user_dr(self, virtual_ir: int = VIRTUAL_IR_REG) -> None:
        output = bytearray()
        self._append_select(output, virtual_ir)
        if output:
            self._exchange(output)

    def _build_user_dr_scan(
        self, frame: int, read_tdo: bool, starting_offset: int = 0
    ) -> tuple[bytearray, int]:
        if not 0 <= frame < (1 << DR_BITS):
            raise ValueError("DR frame must be an unsigned 40-bit integer")
        self._select_user_dr()

        output = bytearray()
        for tms in (1, 0, 0):
            self._append_clock(output, tms, 0)
        output.append(self._bitbang_byte(0, 0, 0))

        # A byte-shift command must not straddle a USB packet: the adapter is
        # much slower when it does. This one (header + 4 bytes) is short, so
        # pad only when it would cross the next packet boundary.
        used = (starting_offset + len(output)) % self._packet_size
        if used + DR_BITS // 8 > self._packet_size:
            output.extend([self._bitbang_byte(0, 0, 0)] * (self._packet_size - used))

        shifted_byte_count = DR_BITS // 8 - 1
        frame_bytes = frame.to_bytes(DR_BITS // 8, "little")
        output.append(_SHIFT_MODE | (_READ if read_tdo else 0) | shifted_byte_count)
        output.extend(frame_bytes[:shifted_byte_count])

        for bit_index in range(shifted_byte_count * 8, DR_BITS):
            bit = (frame >> bit_index) & 1
            self._append_clock(output, int(bit_index == DR_BITS - 1), bit, read_tdo)
        self._append_clock(output, 1, 0)
        self._append_clock(output, 0, 0)
        output.append(self._bitbang_byte(0, 0, 0))

        tdo_bytes = DR_BITS - shifted_byte_count * 8
        response_length = tdo_bytes + shifted_byte_count if read_tdo else 0
        return output, response_length

    @staticmethod
    def _decode_user_dr_response(response: bytes) -> int:
        shifted_byte_count = DR_BITS // 8 - 1
        tdo_frame = 0
        for byte_index, value in enumerate(response[:shifted_byte_count]):
            tdo_frame |= value << (byte_index * 8)
        bitbang_start = shifted_byte_count * 8
        for bit_index, value in enumerate(response[shifted_byte_count:]):
            if value & _READ_TDO:
                tdo_frame |= 1 << (bitbang_start + bit_index)
        return tdo_frame

    def _scan_user_dr(self, frame: int, read_tdo: bool) -> Optional[int]:
        output, response_length = self._build_user_dr_scan(frame, read_tdo)
        response = self._exchange(output, response_length)
        if not read_tdo:
            return None
        return self._decode_user_dr_response(response)

    def write_reg(self, address: int, data: int) -> None:
        """Write one JWSR register (0..7) using a 40-bit USER1 data-register scan."""
        _validate_address(address)
        if address >= JWSR_COUNT:
            raise ValueError(
                f"address {address} is system-written; JTAG can write 0..{JWSR_COUNT - 1}"
            )
        frame = encode_request(address, True, data)
        self._scan_user_dr(frame, read_tdo=False)

    def read_reg(self, address: int) -> int:
        """Read one register; the first scan issues the read, NOP scans capture it.

        The read crosses into the system clock domain, so the capture is
        retried with NOP scans until the bridge reports it done.
        """
        _validate_address(address)
        request = encode_request(address, False)
        address_scan, _ = self._build_user_dr_scan(request, read_tdo=False)
        capture_scan, response_length = self._build_user_dr_scan(
            NOP_FRAME,
            read_tdo=True,
            starting_offset=len(address_scan),
        )
        response = self._exchange(address_scan + capture_scan, response_length)
        frame = self._decode_user_dr_response(response)
        for _ in range(READ_RETRIES):
            if frame & OVERRUN_BIT:
                raise UsbBlasterError(
                    "The bridge dropped a command issued while the previous one "
                    "was still in flight; check that the system clock is running."
                )
            if frame & DONE_BIT:
                return decode_response(frame)
            frame = self._scan_user_dr(NOP_FRAME, read_tdo=True)
        raise UsbBlasterError(
            "The bridge did not complete the read; check that the system clock "
            "is running and the system domain is out of reset."
        )

    def _build_buffer_scan(
        self,
        data: bytes,
        read_tdo: bool,
        starting_offset: int = 0,
        response_offset: int = 0,
    ) -> tuple[bytearray, int]:
        """Build one DR scan of len(data) * 8 bits; return it and its TDO byte count.

        All bytes but the last use byte-shift mode, one packet-aligned command
        per chunk; the last byte is bit-banged so its final bit can leave
        Shift-DR. A read is padded with TDO reads that do not clock TCK, so the
        response fills whole IN packets and none waits for the latency timer.
        The scan follows `starting_offset` output bytes and `response_offset`
        TDO bytes of the same exchange.
        """
        idle = self._bitbang_byte(0, 0, 0)
        output = bytearray()
        for tms in (1, 0, 0):
            self._append_clock(output, tms, 0)
        output.append(idle)

        shifted = data[:-1]
        for start in range(0, len(shifted), _BUFFER_CHUNK_BYTES):
            chunk = shifted[start : start + _BUFFER_CHUNK_BYTES]
            output.extend([idle] * (-(starting_offset + len(output)) % self._packet_size))
            output.append(_SHIFT_MODE | (_READ if read_tdo else 0) | len(chunk))
            output.extend(chunk)
            output.append(idle)

        last = data[-1]
        for bit_index in range(8):
            self._append_clock(
                output, int(bit_index == 7), (last >> bit_index) & 1, read_tdo
            )
        self._append_clock(output, 1, 0)
        self._append_clock(output, 0, 0)
        output.append(idle)
        if not read_tdo:
            return output, 0

        response_length = len(data) - 1 + 8
        padding = -(response_offset + response_length) % (self._packet_size - 2)
        output.extend([self._bitbang_byte(0, 0, 0, read=True)] * padding)
        return output, response_length + padding

    @staticmethod
    def _decode_buffer_response(response: bytes, length: int) -> bytes:
        """Join the byte-shift TDO bytes with the eight bit-banged bits of the last byte."""
        last = 0
        for bit_index, value in enumerate(response[length - 1 : length + 7]):
            if value & _READ_TDO:
                last |= 1 << bit_index
        return bytes(response[: length - 1]) + bytes([last])

    def _exchange_overlapped(self, output: bytearray, read_length: int) -> bytes:
        """Send output and return read_length TDO bytes, writing and reading concurrently.

        The FT245 holds only a few hundred TDO bytes; once it is full the
        adapter stops taking commands, so a write-then-read exchange would
        stall on large responses. Reading while a thread writes also lets the
        host schedule the IN transfer without waiting for the OUT transfer to
        finish, which saves a 1 ms USB frame on most small exchanges.

        The response is padded with TDO reads that do not clock TCK until it
        fills whole IN packets: the adapter holds a partial packet until its
        latency timer expires, which would add up to 2 ms.
        """
        padding = -read_length % (self._packet_size - 2)
        if padding:
            output = output + bytes([self._bitbang_byte(0, 0, 0, read=True)]) * padding
        write_error = []

        def write() -> None:
            try:
                self._endpoint_out.write(output, timeout=self.timeout_ms)
            except usb.core.USBError as exc:
                write_error.append(exc)

        writer = threading.Thread(target=write, daemon=True)
        writer.start()
        try:
            response = self._read_ftdi_payload(read_length + padding)[:read_length]
        except BaseException:
            self._selected_virtual_ir = None
            raise
        finally:
            writer.join()
        if write_error:
            self._selected_virtual_ir = None
            raise UsbBlasterError(
                f"USB-Blaster bulk transfer failed: {write_error[0]}"
            ) from write_error[0]
        return response

    def store_buf(self, data) -> None:
        """Write bytes to the buffer from word 0 in one BUF_STORE scan.

        Words are little-endian (data[0] is bits 7..0 of word 0). Up to
        BUFFER_BYTES bytes; a trailing partial word is zero-padded.
        """
        data = bytes(data)
        if len(data) > BUFFER_BYTES:
            raise ValueError(f"buffer holds at most {BUFFER_BYTES} bytes")
        if not data:
            return
        data += bytes(-len(data) % 4)
        output = bytearray()
        self._append_select(output, VIRTUAL_IR_BUF_STORE)
        scan, _ = self._build_buffer_scan(data, False, starting_offset=len(output))
        self._exchange(output + scan)

    def load_buf(self, length: int = BUFFER_BYTES) -> list[int]:
        """Read `length` bytes from the buffer, starting at word 0, in one BUF_LOAD scan."""
        if not 0 <= length <= BUFFER_BYTES:
            raise ValueError(f"length must be in range 0..{BUFFER_BYTES}")
        if not length:
            return []
        scan_length = -(-length // 4) * 4
        output = bytearray()
        self._append_select(output, VIRTUAL_IR_BUF_LOAD)
        scan, response_length = self._build_buffer_scan(
            bytes(scan_length), True, starting_offset=len(output)
        )
        response = self._exchange_overlapped(output + scan, response_length)
        return list(self._decode_buffer_response(response, scan_length)[:length])

    def _append_reg_scans(self, output: bytearray, writes, reads) -> int:
        """Append register scans: writes, then reads.

        Each read's request scan also captures the previous read's result,
        and a final NOP scan captures the last. Returns the TDO byte count.
        """
        frames = []
        for address, data in writes:
            if not 0 <= address < JWSR_COUNT:
                raise ValueError(f"JTAG can write addresses 0..{JWSR_COUNT - 1}")
            frames.append(encode_request(address, True, data))
        frames += [encode_request(address, False) for address in reads]
        if reads:
            frames.append(NOP_FRAME)

        self._append_select(output, VIRTUAL_IR_REG)
        response_length = 0
        for index, frame in enumerate(frames):
            scan, length = self._build_user_dr_scan(
                frame, index > len(writes), starting_offset=len(output)
            )
            output += scan
            response_length += length
        return response_length

    def _decode_reg_reads(self, response: bytes, reads, retry: bool = True) -> list:
        """Decode the read captures of _append_reg_scans.

        A capture that finds the bridge still busy means the next read was
        dropped (the capture after it clears its overrun flag). With `retry`
        the remaining reads are then repeated one at a time; otherwise they
        are returned as None.
        """
        values = []
        for index in range(len(reads)):
            frame = self._decode_user_dr_response(
                response[index * _REG_TDO_BYTES : (index + 1) * _REG_TDO_BYTES]
            )
            if frame & OVERRUN_BIT:
                raise UsbBlasterError(
                    "The bridge dropped a command issued while the previous one "
                    "was still in flight; check that the system clock is running."
                )
            if not frame & DONE_BIT:
                if retry:
                    values += [self.read_reg(address) for address in reads[index:]]
                else:
                    values += [None] * (len(reads) - index)
                break
            values.append(decode_response(frame))
        return values

    def transact(self, writes=(), reads=()) -> list[int]:
        """Write registers, then read registers, in one USB exchange.

        `writes` is a sequence of (address, data) pairs for addresses 0..7 and
        `reads` a sequence of addresses 0..15; they run in that order and the
        read values are returned.
        """
        reads = list(reads)
        if not writes and not reads:
            return []
        output = bytearray()
        response_length = self._append_reg_scans(output, writes, reads)
        response = self._exchange(output, response_length)
        return self._decode_reg_reads(response, reads)

    def _sa_batch(self, writes, reads, store=None, load_length=0):
        """One exchange: [buffer load], [buffer store], register writes, reads.

        Returns (values, loaded): a read value is None if its capture found
        the bridge busy, and loaded is the buffer contents (bytes) scanned
        before the writes, or None without a load.
        """
        output = bytearray()
        load_response = 0
        if load_length:
            self._append_select(output, VIRTUAL_IR_BUF_LOAD)
            scan, load_response = self._build_buffer_scan(
                bytes(load_length), True, starting_offset=len(output)
            )
            output += scan
        if store:
            self._append_select(output, VIRTUAL_IR_BUF_STORE)
            scan, _ = self._build_buffer_scan(store, False, starting_offset=len(output))
            output += scan
        response_length = self._append_reg_scans(output, writes, reads)
        loaded = None
        if load_length:
            response = self._exchange_overlapped(output, load_response + response_length)
            loaded = self._decode_buffer_response(response, load_length)
            response = response[load_response:]
        else:
            response = self._exchange(output, response_length)
        return self._decode_reg_reads(response, reads, retry=False), loaded

    def _sa_command(
        self,
        op: int,
        writes=(),
        reads=(),
        first_mask: int = 0xF,
        last_mask: int = 0xF,
        store: Optional[bytes] = None,
        load_length: int = 0,
    ) -> tuple[list[int], Optional[bytes]]:
        """Run one sys_access command; return (`reads` after it finished, loaded bytes).

        One exchange first loads `load_length` buffer bytes (the result of the
        previous, finished command), stores `store` into the buffer, writes the
        operand registers and SA_CMD, and reads SA_STATUS and then `reads`.
        Both buffer scans precede the command, so they never overlap it. If
        SA_STATUS does not yet show the command's tag with busy clear, it is
        polled until it does and `reads` are repeated. On timeout the command
        is aborted.
        """
        self._sa_tag = self._sa_tag % 255 + 1
        tag = self._sa_tag
        command = op | (tag << 8) | (first_mask << 16) | (last_mask << 20)
        writes = list(writes) + [(SA_CMD_REG, command)]
        reads = [SA_STATUS_REG] + list(reads)
        deadline = time.monotonic() + SA_TIMEOUT_S
        values, loaded = self._sa_batch(writes, reads, store, load_length)
        while not self._sa_done(values[0], tag):
            if time.monotonic() > deadline:
                self.transact([(SA_CMD_REG, SA_OP_ABORT)])
                raise UsbBlasterError(
                    f"System access (op {op}) did not finish within "
                    f"{SA_TIMEOUT_S} s (SA_STATUS {values[0]}); aborted. "
                    "Check that the system answers sa_enable with sa_ready."
                )
            values = self.transact((), reads)
        if None in values:
            values = values[:1] + self.transact((), reads[1:])
        return values[1:], loaded

    @staticmethod
    def _sa_done(status: Optional[int], tag: int) -> bool:
        if status is None:
            return False
        if status >> 16 != BUFFER_WORDS:
            raise UsbBlasterError(
                f"SA_STATUS 0x{status:08X} does not identify the sys_access "
                f"engine with a {BUFFER_WORDS}-word buffer; check the bitstream."
            )
        return (status >> 8) & 0xFF == tag and not status & SA_STATUS_BUSY

    def sys_wr(self, address: int, data: int) -> None:
        """Write one 32-bit word to the system address space; sa_addr = address."""
        _validate_word(address, "address")
        _validate_word(data, "data")
        self._sa_command(SA_OP_WRITE, [(SA_ADDR_REG, address), (SA_DATA_REG, data)])

    def sys_rd(self, address: int) -> int:
        """Read one 32-bit word from the system address space; sa_addr = address."""
        _validate_word(address, "address")
        values, _ = self._sa_command(
            SA_OP_READ, [(SA_ADDR_REG, address)], reads=[SA_RDATA_REG]
        )
        return values[0]

    def data_store(self, address: int, data) -> None:
        """Write bytes to consecutive system byte addresses starting at `address`.

        Byte address A is bits 8n+7..8n of the word at A - n, n = A % 4. Moves
        up to BUFFER_WORDS words per buffer pass, each stored in the USB
        exchange that starts its block write; partially covered first and
        last words are merged in hardware (read-modify-write).
        """
        _validate_word(address, "address")
        data = bytes(data)
        if not data:
            return
        offset = address & 3
        end = offset + len(data)
        word_count = -(-end // 4)
        padded = bytes(offset) + data + bytes(word_count * 4 - end)
        head_mask = (0xF << offset) & 0xF
        tail_mask = (1 << (end % 4)) - 1 if end % 4 else 0xF
        for first in range(0, word_count, BUFFER_WORDS):
            count = min(BUFFER_WORDS, word_count - first)
            self._sa_command(
                SA_OP_BLOCK_WRITE,
                [
                    (SA_ADDR_REG, (address - offset + 4 * first) & WORD_MASK),
                    (SA_DATA_REG, count),
                ],
                first_mask=head_mask if first == 0 else 0xF,
                last_mask=tail_mask if first + count == word_count else 0xF,
                store=padded[first * 4 : (first + count) * 4],
            )

    def data_load(self, address: int, length: int) -> list[int]:
        """Read `length` bytes from consecutive system byte addresses at `address`.

        Each buffer pass loads the previous pass's words in the same USB
        exchange that starts its own block read.
        """
        _validate_word(address, "address")
        if length < 0:
            raise ValueError("length must not be negative")
        if not length:
            return []
        offset = address & 3
        word_count = -(-(offset + length) // 4)
        loaded = bytearray()
        pending = 0             # bytes of the previous pass waiting in the buffer
        for first in range(0, word_count, BUFFER_WORDS):
            count = min(BUFFER_WORDS, word_count - first)
            _, data = self._sa_command(
                SA_OP_BLOCK_READ,
                [
                    (SA_ADDR_REG, (address - offset + 4 * first) & WORD_MASK),
                    (SA_DATA_REG, count),
                ],
                load_length=pending,
            )
            if data:
                loaded += data
            pending = count * 4
        loaded += bytes(self.load_buf(pending))
        return list(loaded[offset : offset + length])

    def close(self) -> None:
        if self._claimed_interface is not None:
            try:
                usb.util.release_interface(self.device, self._claimed_interface)
            except (usb.core.USBError, AttributeError):
                pass
            self._claimed_interface = None
        if getattr(self, "device", None) is not None:
            usb.util.dispose_resources(self.device)

    def __enter__(self) -> "UsbBlaster":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()