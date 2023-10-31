# SPDX-License-Identifier: MIT
"""Example script to demonstrate tiny-v1 relay box"""

import serial
import struct
import logging
from time import sleep

# Box clock runs at 2.5MHz/16 +-0.5ppm
TINYRATE = 156250

# Internal constants
_DEFSERIALPORT = '/dev/ttyUSB0'
_SERIALBAUD = 115384
_CLOCK = 0x10
_INPUT1 = 0x20
_INPUT2 = 0x40
_INPUT3 = 0x80
_OUTPUT = 0x04

_log = logging.getLogger('tiny')


class Tiny:
    """Serial port wrapper for Tiny relay box"""

    def __init__(self, port=None):
        if port is None:
            port = _DEFSERIALPORT
        self._port = serial.Serial(port, _SERIALBAUD, timeout=0.3)
        _log.info('Connected to %r', port)
        aligned = self.align()
        _log.info('Tiny aligned=%r', aligned)

    def _write(self, buf):
        _log.debug('SEND: 0x%s', buf.hex())
        return self._port.write(buf)

    def _read(self, count=None):
        rcv = self._port.read(count)
        if len(rcv) == count:
            _log.debug('RECV: 0x%s', rcv.hex())
        else:
            if rcv:
                _log.debug('RECV: 0x%s [short read]', rcv.hex())
            else:
                _log.debug('RECV: [timeout]')
            rcv = b''
        return rcv

    def _waitFor(self, msg=None):
        """Read from port until timeout or msg is received"""
        count = 0
        while True:
            rcv = self._read(5)
            if rcv == b'':
                return False
            if msg and msg == rcv:
                return True
            count += 1
            if count > 20:
                return False

    def resetClock(self, ack=0):
        """Reset tiny clock, wait for ack if non-zero"""
        cmd = bytearray(5)
        struct.pack_into('>L', cmd, 1, ack)
        self._write(cmd)
        if ack:
            return self._waitFor(cmd)
        return True

    def align(self):
        """Re-align I/O buffers on FTDI chip

        Write a 6 byte command repeatedly until the 5 byte
        reply matches the expected signature.
 
        see FTDI AN232B-04 "Data Throughput, Latency & Handshaking"
    
        """
        cmd = b'\x10\xe0\x7f\x0f\x55\x2a'
        count = 0
        aligned = False
        while not aligned:
            _log.debug('Align:0x%s%s', '..' * (5 - count),
                       cmd[(5 - count):].hex())
            self._write(cmd)
            aligned = self._waitFor(cmd[1:6])
            count += 1
            if count > 5:
                _log.error('Too many align tries, giving up')
                break
        return aligned

    def readMsg(self):
        """Read and return the next message

        Returns a tuple: (msgtype, values...) eg:

            ('schedule', 'PA:09', 1234.5678)
            ('setports', 'PA:00', 'PB:00')
            ('setportb', 'PB:02')
            ('input', '2:08', 1606.9820992)
            ('clock', 65538, 0.4194432)

        Returns None if no message is received.
        """
        frame = self._read(5)
        if frame and len(frame) == 5:
            msgType = frame[0] & 0xf0
            boxClock = struct.unpack('>L', frame[1:5])[0]
            boxTime = boxClock / TINYRATE
            if msgType == _CLOCK:
                # tiny clock "heartbeat" message
                return ('clock', boxClock, boxTime)
            elif msgType == _INPUT1:
                # input on channel 1
                return ('input', '1', boxTime)
            elif msgType == _INPUT2:
                # input on channel 2
                return ('input', '2:%02X' % (frame[0] & 0xe), boxTime)
            elif msgType == _INPUT3:
                # input on channel 3
                return ('input', '3:%02X' % (frame[0] & 0x7), boxTime)
            elif frame[0] == _OUTPUT:
                # output port command
                if frame[3] & 0x80:
                    return ('setports', 'PA:%02X' % (frame[3] & 0x1f),
                            'PB:%02X' % (frame[1]))
                else:
                    # Port A is not altered unless msb is set on frame[3]
                    return ('setportb', 'PB:%02X' % (frame[1]))
            elif frame[0] == 0x00:
                # reset clock command
                return ('reset', frame[1:].hex())
            elif (frame[0] & 0xe0) == 0xe0:
                # scheduled PA output
                return ('schedule', 'PA:%02X' % (frame[0] & 0x1f), boxTime)
            else:
                return ('unknown', frame.hex())
        else:
            if frame:
                _log.info('Incomplete frame: %r', frame)
            return None

    def setPorts(self, pa, pb):
        """Set both port A and port B"""
        cmd = bytearray(5)
        cmd[0] = 0x04
        cmd[1] = pb & 0xff
        cmd[3] = 0x80 | (pa & 0x1f)
        self._write(cmd)

    def setPortB(self, pb):
        """Set port B without changing port A"""
        cmd = bytearray(5)
        cmd[0] = 0x04
        cmd[1] = pb & 0xff
        self._write(cmd)

    def schedulePortA(self, pa, clock):
        """Schedule port A to be set as instructed at clock time

           Clock should be about 0.5 second ahead of the current
           relay box time or the match will be missed.

           This call will replace a pending schedule request.

           Once scheduled, the match may be cancelled by
           setting port A manually via setPorts()

        """
        cmd = bytearray(5)
        cmd[0] = 0xe0 | (pa & 0x1f)
        if isinstance(clock, float):
            clock = int(round(clock * TINYRATE))
        if isinstance(clock, int):
            struct.pack_into('>L', cmd, 1, clock & 0xffffffff)
            self._write(cmd)
        else:
            _log.error('Invalid clock %r', clock)


if __name__ == "__main__":
    # setup a basic log handler
    logging.basicConfig(level=logging.DEBUG)
    _log.setLevel(logging.INFO)

    # uncomment to see debug logs
    #_log.setLevel(logging.DEBUG)

    # Connect to default serial port and align the I/O buffers
    t = Tiny()

    # Reset clock and don't wait for the acknowledge
    t.resetClock()

    # Flash all outputs on PA and PB
    t.setPorts(pa=0x1f, pb=0xff)
    sleep(0.1)
    t.setPorts(pa=0x00, pb=0x00)

    # Empty the read buffer by reading messages until timeout
    while True:
        msg = t.readMsg()
        if msg is not None:
            _log.info('Received: %r', msg)
        else:
            break

    # Reset tiny clock to zero, and wait for the acknowledge
    if t.resetClock(0xcafebeef):
        _log.info('Reset successful')
    else:
        _log.info('Timeout waiting for reset acknowledge')

    count = 0
    while True:
        # fetch next unread message
        msg = t.readMsg()
        if msg is not None:
            if msg[0] == 'clock':
                # report heartbeat to debug
                _log.debug('Received: %r', msg)
            else:
                # report all other messages to info
                _log.info('Received: %r', msg)
                if msg[0] == 'input':
                    # if an input inpulse was read, set pb and schedule pa
                    count += 1
                    t.schedulePortA(count, msg[2] + 1.0)
                    t.setPortB(count)
