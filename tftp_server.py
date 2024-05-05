# coding: utf-8

'''
# Repository

[msoulier/tftpy | Github](https://github.com/msoulier/tftpy/)

# License

The MIT License

Copyright (c) 2009 Michael P. Soulier

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
'''

from logging import basicConfig, FileHandler, getLogger, StreamHandler, \
    INFO
import os
import select
import socket
import struct
import sys
import threading
import random
import time
from errno import EINTR

# logging
log = getLogger("tftpy.TftpServer")
address = ()

MIN_BLKSIZE = 8
DEF_BLKSIZE = 512
MAX_BLKSIZE = 65536
SOCK_TIMEOUT = 5
MAX_DUPS = 20
DEF_TIMEOUT_RETRIES = 3
DEF_TFTP_PORT = 69
DELAY_BLOCK = 0
NETWORK_UNRELIABILITY = 0

class TftpErrors:
    """This class is a convenience for defining the common tftp error codes,
    and making them more readable in the code."""
    NotDefined = 0
    FileNotFound = 1
    AccessViolation = 2
    DiskFull = 3
    IllegalTftpOp = 4
    UnknownTID = 5
    FileAlreadyExists = 6
    NoSuchUser = 7
    FailedNegotiation = 8

class TftpException(Exception):
    """This class is the parent class of all exceptions regarding the handling
    of the TFTP protocol."""
    pass

class TftpTimeout(TftpException):
    """This class represents a timeout error waiting for a response from the
    other end."""
    pass

class TftpTimeoutExpectACK(TftpTimeout):
    """This class represents a timeout error when waiting for ACK of the current block
    and receiving duplicate ACK for previous block from the other end."""
    pass

class TftpFileNotFoundError(TftpException):
    """This class represents an error condition where we received a file
    not found error."""
    pass

class TftpMetrics:
    """A class representing metrics of the transfer."""
    def __init__(self):
        self.bytes = 0
        self.resent_bytes = 0
        self.dups = {}
        self.dupcount = 0
        self.start_time = 0
        self.end_time = 0
        self.duration = 0
        self.last_dat_time = 0
        self.bps = 0
        self.kbps = 0
        self.errors = 0
    def compute(self):
        self.duration = self.end_time - self.start_time
        if self.duration == 0:
            self.duration = 1
        self.bps = (self.bytes * 8.0) / self.duration
        self.kbps = self.bps / 1024.0
        for key in self.dups:
            self.dupcount += self.dups[key]
    def add_dup(self, pkt):
        """This method adds a dup for a packet to the metrics."""
        s = str(pkt)
        if s in self.dups:
            self.dups[s] += 1
        else:
            self.dups[s] = 1

class TftpContext:
    """The base class of the contexts."""
    def __init__(self, host, port, timeout, retries=DEF_TIMEOUT_RETRIES, localip=""):
        """Constructor for the base context, setting shared instance
        variables."""
        self.file_to_transfer = None
        self.fileobj = None
        self.options = None
        self.packethook = None
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        if localip != "":
            self.sock.bind((localip, 0))
        self.sock.settimeout(timeout)
        self.timeout = timeout
        self.retries = retries
        self.state = None
        self.next_block = 0
        self.factory = TftpPacketFactory()
        self.host = host
        self.port = port
        self.tidport = None
        self.metrics = TftpMetrics()
        self.pending_complete = False
        self.last_update = 0
        self.last_pkt = None
        self.retry_count = 0
        self.timeout_expectACK = False
    def getBlocksize(self):
        """Fetch the current blocksize for this session."""
        return int(self.options.get("blksize", 512))
    def __del__(self):
        """Simple destructor to try to call housekeeping in the end method if
        not called explicitly. Leaking file descriptors is not a good
        thing."""
        self.end()
    def checkTimeout(self, now):
        """Compare current time with last_update time, and raise an exception
        if we're over the timeout time."""
        if self.timeout_expectACK:
            raise TftpTimeout("Timeout waiting for traffic")
        if now - self.last_update > self.timeout:
            raise TftpTimeout("Timeout waiting for traffic")
    def start(self):
        raise NotImplementedError("Abstract method")
    def end(self, close_fileobj=True):
        """Perform session cleanup, since the end method should always be
        called explicitly by the calling code, this works better than the
        destructor.
        Set close_fileobj to False so fileobj can be returned open."""
        self.sock.close()
        if close_fileobj and self.fileobj is not None and not self.fileobj.closed:
            self.fileobj.close()
    def gethost(self):
        """
        Simple getter method for use in a property.
        """
        return self.__host
    def sethost(self, host):
        """
        Setter method that also sets the address property as a result
        of the host that is set.
        """
        self.__host = host
        self.address = socket.gethostbyname(host)
    host = property(gethost, sethost)
    def setNextBlock(self, block):
        if block >= 2 ** 16:
            block = 0
        self.__eblock = block
    def getNextBlock(self):
        return self.__eblock
    next_block = property(getNextBlock, setNextBlock)
    def cycle(self):
        """
        Here we wait for a response from the server after sending it
        something, and dispatch appropriate action to that response.
        """
        try:
            (buffer, (raddress, rport)) = self.sock.recvfrom(MAX_BLKSIZE)
        except socket.timeout:
            raise TftpTimeout("Timed-out waiting for traffic")
        self.last_update = time.time()
        recvpkt = self.factory.parse(buffer)
        if self.packethook:
            self.packethook(recvpkt)
        self.state = self.state.handle(recvpkt, raddress, rport)
        self.retry_count = 0

class TftpContextServer(TftpContext):
    """The context for the server."""
    def __init__(
        self,
        host,
        port,
        timeout,
        root,
        dyn_file_func=None,
        upload_open=None,
        retries=DEF_TIMEOUT_RETRIES,
    ):
        TftpContext.__init__(self, host, port, timeout, retries)
        self.state = TftpStateServerStart(self)
        self.root = root
        self.dyn_file_func = dyn_file_func
        self.upload_open = upload_open
    def __str__(self):
        return f"{self.host}:{self.port} {self.state}"
    def start(self, buffer):
        """
        Start the state cycle. Note that the server context receives an
        initial packet in its start method. Also note that the server does not
        loop on cycle(), as it expects the TftpServer object to manage
        that.
        """
        self.metrics.start_time = time.time()
        self.last_update = time.time()
        pkt = self.factory.parse(buffer)
        self.state = self.state.handle(pkt, self.host, self.port)
    def end(self):
        """Finish up the context."""
        TftpContext.end(self)
        self.metrics.end_time = time.time()
        self.metrics.compute()

class TftpContextClientUpload(TftpContext):
    """The upload context for the client during an upload.
    Note: If input is a hyphen, then we will use stdin."""
    def __init__(
        self,
        host,
        port,
        filename,
        input,
        options,
        packethook,
        timeout,
        retries=DEF_TIMEOUT_RETRIES,
        localip="",
    ):
        TftpContext.__init__(self, host, port, timeout, retries, localip)
        self.file_to_transfer = filename
        self.options = options
        self.packethook = packethook
        if hasattr(input, "read"):
            self.fileobj = input
        elif input == "-":
            self.fileobj = sys.stdin.buffer
        else:
            self.fileobj = open(input, "rb")
    def __str__(self):
        return f"{self.host}:{self.port} {self.state}"
    def start(self):
        self.metrics.start_time = time.time()
        pkt = TftpPacketWRQ()
        pkt.filename = self.file_to_transfer
        pkt.mode = "octet"
        pkt.options = self.options
        self.sock.sendto(pkt.encode().buffer, (self.host, self.port))
        self.next_block = 1
        self.last_pkt = pkt
        self.state = TftpStateSentWRQ(self)
        while self.state:
            try:
                self.cycle()
            except TftpTimeout:
                self.retry_count += 1
                if self.retry_count >= self.retries:
                    raise
                else:
                    self.state.resendLast()
    def end(self):
        """Finish up the context."""
        TftpContext.end(self)
        self.metrics.end_time = time.time()
        self.metrics.compute()

class TftpContextClientDownload(TftpContext):
    """The download context for the client during a download.
    Note: If output is a hyphen, then the output will be sent to stdout."""
    def __init__(
        self,
        host,
        port,
        filename,
        output,
        options,
        packethook,
        timeout,
        retries=DEF_TIMEOUT_RETRIES,
        localip="",
    ):
        TftpContext.__init__(self, host, port, timeout, retries, localip)
        self.file_to_transfer = filename
        self.options = options
        self.packethook = packethook
        self.filelike_fileobj = False
        if hasattr(output, "write"):
            self.fileobj = output
            self.filelike_fileobj = True
        elif output == "-":
            self.fileobj = sys.stdout
            self.filelike_fileobj = True
        else:
            self.fileobj = open(output, "wb")
    def __str__(self):
        return f"{self.host}:{self.port} {self.state}"
    def start(self):
        """Initiate the download."""
        self.metrics.start_time = time.time()
        pkt = TftpPacketRRQ()
        pkt.filename = self.file_to_transfer
        pkt.mode = "octet"
        pkt.options = self.options
        self.sock.sendto(pkt.encode().buffer, (self.host, self.port))
        self.next_block = 1
        self.last_pkt = pkt
        self.state = TftpStateSentRRQ(self)
        while self.state:
            try:
                self.cycle()
            except TftpTimeout:
                self.retry_count += 1
                if self.retry_count >= self.retries:
                    raise
                else:
                    self.state.resendLast()
            except TftpFileNotFoundError as err:
                if self.fileobj is not None and not self.filelike_fileobj:
                    if os.path.exists(self.fileobj.name):
                        os.unlink(self.fileobj.name)
                raise
    def end(self):
        """Finish up the context."""
        TftpContext.end(self, not self.filelike_fileobj)
        self.metrics.end_time = time.time()
        self.metrics.compute()

class TftpSession:
    """This class is the base class for the tftp client and server. Any shared
    code should be in this class."""
    pass

class TftpPacketWithOptions:
    """This class exists to permit some TftpPacket subclasses to share code
    regarding options handling. It does not inherit from TftpPacket, as the
    goal is just to share code here, and not cause diamond inheritance."""
    def __init__(self):
        self.options = {}
    def setoptions(self, options):
        myoptions = {}
        for key in options:
            newkey = key
            if isinstance(key, bytes):
                newkey = newkey.decode("ascii")
            newval = options[key]
            if isinstance(newval, bytes):
                newval = newval.decode("ascii")
            myoptions[newkey] = newval
        self._options = myoptions
    def getoptions(self):
        return self._options
    options = property(getoptions, setoptions)
    def decode_options(self, buffer):
        """This method decodes the section of the buffer that contains an
        unknown number of options. It returns a dictionary of option names and
        values."""
        fmt = b"!"
        options = {}
        if len(buffer) == 0:
            return {}
        length = 0
        for i in range(len(buffer)):
            if ord(buffer[i : i + 1]) == 0:
                if length > 0:
                    fmt += b"%dsx" % length
                    length = -1
                else:
                    raise TftpException("Invalid options in buffer")
            length += 1
        mystruct = struct.unpack(fmt, buffer)
        for i in range(0, len(mystruct), 2):
            key = mystruct[i].decode("ascii")
            val = mystruct[i + 1].decode("ascii")
            options[key] = val
        return options

class TftpPacket:
    """This class is the parent class of all tftp packet classes. It is an
    abstract class, providing an interface, and should not be instantiated
    directly."""
    def __init__(self):
        self.opcode = 0
        self.buffer = None
    def encode(self):
        """The encode method of a TftpPacket takes keyword arguments specific
        to the type of packet, and packs an appropriate buffer in network-byte
        order suitable for sending over the wire.
        This is an abstract method."""
        raise NotImplementedError("Abstract method")
    def decode(self):
        """The decode method of a TftpPacket takes a buffer off of the wire in
        network-byte order, and decodes it, populating internal properties as
        appropriate. This can only be done once the first 2-byte opcode has
        already been decoded, but the data section does include the entire
        datagram.
        This is an abstract method."""
        raise NotImplementedError("Abstract method")

class TftpPacketInitial(TftpPacket, TftpPacketWithOptions):
    """This class is a common parent class for the RRQ and WRQ packets, as
    they share quite a bit of code."""
    def __init__(self):
        TftpPacket.__init__(self)
        TftpPacketWithOptions.__init__(self)
        self.filename = None
        self.mode = None
    def encode(self):
        """Encode the packet's buffer from the instance variables."""
        filename = self.filename
        mode = self.mode
        if not isinstance(filename, bytes):
            filename = filename.encode("ascii")
        if not isinstance(self.mode, bytes):
            mode = mode.encode("ascii")
        ptype = None
        if self.opcode == 1:
            ptype = "RRQ"
        else:
            ptype = "WRQ"
        fmt = b"!H"
        fmt += b"%dsx" % len(filename)
        if mode == b"octet":
            fmt += b"5sx"
        else:
            raise AssertionError("Unsupported mode: %s" % mode)
        options_list = []
        if len(list(self.options.keys())) > 0:
            for key in self.options:
                name = key
                if not isinstance(name, bytes):
                    name = name.encode("ascii")
                options_list.append(name)
                fmt += b"%dsx" % len(name)
                value = self.options[key]
                if isinstance(value, int):
                    value = str(value)
                if not isinstance(value, bytes):
                    value = value.encode("ascii")
                options_list.append(value)
                fmt += b"%dsx" % len(value)
        self.buffer = struct.pack(fmt, self.opcode, filename, mode, *options_list)
        return self
    def decode(self):
        nulls = 0
        fmt = b""
        nulls = length = tlength = 0
        subbuf = self.buffer[2:]
        for i in range(len(subbuf)):
            if ord(subbuf[i : i + 1]) == 0:
                nulls += 1
                fmt += b"%dsx" % length
                length = -1
                if nulls == 2:
                    break
            length += 1
            tlength += 1
        shortbuf = subbuf[: tlength + 1]
        mystruct = struct.unpack(fmt, shortbuf)
        self.filename = mystruct[0].decode("ascii")
        self.mode = mystruct[1].decode("ascii").lower()
        self.options = self.decode_options(subbuf[tlength + 1 :])
        return self

class TftpPacketRRQ(TftpPacketInitial):
    """
    ::
                2 bytes    string   1 byte     string   1 byte
                -----------------------------------------------
        RRQ/  | 01/02 |  Filename  |   0  |    Mode    |   0  |
        WRQ     -----------------------------------------------
    """
    def __init__(self):
        TftpPacketInitial.__init__(self)
        self.opcode = 1
    def __str__(self):
        s = "RRQ packet: filename = %s" % self.filename
        s += " mode = %s" % self.mode
        if self.options:
            s += "\n    options = %s" % self.options
        return s

class TftpPacketWRQ(TftpPacketInitial):
    """
    ::
                2 bytes    string   1 byte     string   1 byte
                -----------------------------------------------
        RRQ/  | 01/02 |  Filename  |   0  |    Mode    |   0  |
        WRQ     -----------------------------------------------
    """
    def __init__(self):
        TftpPacketInitial.__init__(self)
        self.opcode = 2
    def __str__(self):
        s = "WRQ packet: filename = %s" % self.filename
        s += " mode = %s" % self.mode
        if self.options:
            s += "\n    options = %s" % self.options
        return s

class TftpPacketDAT(TftpPacket):
    """
    ::
                2 bytes    2 bytes       n bytes
                ---------------------------------
        DATA  | 03    |   Block #  |    Data    |
                ---------------------------------
    """
    def __init__(self):
        TftpPacket.__init__(self)
        self.opcode = 3
        self.blocknumber = 0
        self.data = None
    def __str__(self):
        s = "DAT packet: block %s" % self.blocknumber
        if self.data:
            s += "\n    data: %d bytes" % len(self.data)
        return s
    def encode(self):
        """Encode the DAT packet. This method populates self.buffer, and
        returns self for easy method chaining."""
        data = self.data
        if not isinstance(self.data, bytes):
            data = self.data.encode("ascii")
        fmt = b"!HH%ds" % len(data)
        self.buffer = struct.pack(fmt, self.opcode, self.blocknumber, data)
        return self
    def decode(self):
        """Decode self.buffer into instance variables. It returns self for
        easy method chaining."""
        (self.blocknumber,) = struct.unpack("!H", self.buffer[2:4])
        self.data = self.buffer[4:]
        return self

class TftpPacketACK(TftpPacket):
    """
    ::
                2 bytes    2 bytes
                -------------------
        ACK   | 04    |   Block #  |
                --------------------
    """
    def __init__(self):
        TftpPacket.__init__(self)
        self.opcode = 4
        self.blocknumber = 0
    def __str__(self):
        return "ACK packet: block %d" % self.blocknumber
    def encode(self):
        self.buffer = struct.pack("!HH", self.opcode, self.blocknumber)
        return self
    def decode(self):
        if len(self.buffer) > 4:
            self.buffer = self.buffer[0:4]
        self.opcode, self.blocknumber = struct.unpack("!HH", self.buffer)
        return self

class TftpPacketERR(TftpPacket):
    """
    ::
                2 bytes  2 bytes        string    1 byte
                ----------------------------------------
        ERROR | 05    |  ErrorCode |   ErrMsg   |   0  |
                ----------------------------------------
        Error Codes
        Value     Meaning
        0         Not defined, see error message (if any).
        1         File not found.
        2         Access violation.
        3         Disk full or allocation exceeded.
        4         Illegal TFTP operation.
        5         Unknown transfer ID.
        6         File already exists.
        7         No such user.
        8         Failed to negotiate options
    """
    def __init__(self):
        TftpPacket.__init__(self)
        self.opcode = 5
        self.errorcode = 0
        self.errmsg = None
        self.errmsgs = {
            1: b"File not found",
            2: b"Access violation",
            3: b"Disk full or allocation exceeded",
            4: b"Illegal TFTP operation",
            5: b"Unknown transfer ID",
            6: b"File already exists",
            7: b"No such user",
            8: b"Failed to negotiate options",
        }
    def __str__(self):
        s = "ERR packet: errorcode = %d" % self.errorcode
        s += "\n    msg = %s" % self.errmsgs.get(self.errorcode, "")
        return s
    def encode(self):
        """Encode the DAT packet based on instance variables, populating
        self.buffer, returning self."""
        fmt = b"!HH%dsx" % len(self.errmsgs[self.errorcode])
        self.buffer = struct.pack(
            fmt, self.opcode, self.errorcode, self.errmsgs[self.errorcode]
        )
        return self
    def decode(self):
        """Decode self.buffer, populating instance variables and return self."""
        buflen = len(self.buffer)
        if buflen == 4:
            fmt = b"!HH"
            self.opcode, self.errorcode = struct.unpack(fmt, self.buffer)
        else:
            fmt = b"!HH%dsx" % (len(self.buffer) - 5)
            self.opcode, self.errorcode, self.errmsg = struct.unpack(fmt, self.buffer)
        return self

class TftpPacketOACK(TftpPacket, TftpPacketWithOptions):
    """
    ::
        +-------+---~~---+---+---~~---+---+---~~---+---+---~~---+---+
        |  opc  |  opt1  | 0 | value1 | 0 |  optN  | 0 | valueN | 0 |
        +-------+---~~---+---+---~~---+---+---~~---+---+---~~---+---+
    """
    def __init__(self):
        TftpPacket.__init__(self)
        TftpPacketWithOptions.__init__(self)
        self.opcode = 6
    def __str__(self):
        return "OACK packet:\n    options = %s" % self.options
    def encode(self):
        fmt = b"!H"
        options_list = []
        for key in self.options:
            value = self.options[key]
            if isinstance(value, int):
                value = str(value)
            if not isinstance(key, bytes):
                key = key.encode("ascii")
            if not isinstance(value, bytes):
                value = value.encode("ascii")
            fmt += b"%dsx" % len(key)
            fmt += b"%dsx" % len(value)
            options_list.append(key)
            options_list.append(value)
        self.buffer = struct.pack(fmt, self.opcode, *options_list)
        return self
    def decode(self):
        self.options = self.decode_options(self.buffer[2:])
        return self
    def match_options(self, options):
        """This method takes a set of options, and tries to match them with
        its own. It can accept some changes in those options from the server as
        part of a negotiation. Changed or unchanged, it will return a dict of
        the options so that the session can update itself to the negotiated
        options."""
        for name in self.options:
            if name in options:
                if name == "blksize":
                    size = int(self.options[name])
                    if size >= MIN_BLKSIZE and size <= MAX_BLKSIZE:
                        options["blksize"] = size
                    else:
                        raise TftpException(
                            "blksize %s option outside allowed range" % size
                        )
                elif name == "tsize":
                    size = int(self.options[name])
                    if size < 0:
                        raise TftpException("Negative file sizes not supported")
                else:
                    raise TftpException("Unsupported option: %s" % name)
        return True

class TftpPacketFactory:
    """This class generates TftpPacket objects. It is responsible for parsing
    raw buffers off of the wire and returning objects representing them, via
    the parse() method."""
    def __init__(self):
        self.classes = {
            1: TftpPacketRRQ,
            2: TftpPacketWRQ,
            3: TftpPacketDAT,
            4: TftpPacketACK,
            5: TftpPacketERR,
            6: TftpPacketOACK,
        }
    def parse(self, buffer):
        """This method is used to parse an existing datagram into its
        corresponding TftpPacket object. The buffer is the raw bytes off of
        the network."""
        (opcode,) = struct.unpack("!H", buffer[:2])
        packet = self.__create(opcode)
        packet.buffer = buffer
        return packet.decode()
    def __create(self, opcode):
        """This method returns the appropriate class object corresponding to
        the passed opcode."""
        packet = self.classes[opcode]()
        return packet

class TftpState:
    """The base class for the states."""
    def __init__(self, context):
        """Constructor for setting up common instance variables. The involved
        file object is required, since in tftp there's always a file
        involved."""
        self.context = context
    def handle(self, pkt, raddress, rport):
        """An abstract method for handling a packet. It is expected to return
        a TftpState object, either itself or a new state."""
        raise NotImplementedError("Abstract method")
    def handleOACK(self, pkt):
        """This method handles an OACK from the server, syncing any accepted
        options."""
        if len(pkt.options.keys()) > 0:
            if pkt.match_options(self.context.options):
                self.context.options = pkt.options
            else:
                raise TftpException("Failed to negotiate options")
        else:
            raise TftpException("No options found in OACK")
    def returnSupportedOptions(self, options):
        """This method takes a requested options list from a client, and
        returns the ones that are supported."""
        accepted_options = {}
        for option in options:
            if option == "blksize":
                if int(options[option]) > MAX_BLKSIZE:
                    accepted_options[option] = MAX_BLKSIZE
                elif int(options[option]) < MIN_BLKSIZE:
                    accepted_options[option] = MIN_BLKSIZE
                else:
                    accepted_options[option] = options[option]
            elif option == "tsize":
                accepted_options["tsize"] = 0
        return accepted_options
    def sendDAT(self):
        """This method sends the next DAT packet based on the data in the
        context. It returns a boolean indicating whether the transfer is
        finished."""
        finished = False
        blocknumber = self.context.next_block
        if DELAY_BLOCK and DELAY_BLOCK == blocknumber:
            time.sleep(10)
        dat = None
        blksize = self.context.getBlocksize()
        buffer = self.context.fileobj.read(blksize)
        if len(buffer) < blksize:
            finished = True
        dat = TftpPacketDAT()
        dat.data = buffer
        dat.blocknumber = blocknumber
        self.context.metrics.bytes += len(dat.data)
        if NETWORK_UNRELIABILITY > 0 and random.randrange(NETWORK_UNRELIABILITY) == 0:
            pass
        else:
            self.context.sock.sendto(
                dat.encode().buffer, (self.context.host, self.context.tidport)
            )
            self.context.metrics.last_dat_time = time.time()
        if self.context.packethook:
            self.context.packethook(dat)
        self.context.last_pkt = dat
        return finished
    def sendACK(self, blocknumber=None):
        """This method sends an ack packet to the block number specified. If
        none is specified, it defaults to the next_block property in the
        parent context."""
        if blocknumber is None:
            blocknumber = self.context.next_block
        ackpkt = TftpPacketACK()
        ackpkt.blocknumber = blocknumber
        if NETWORK_UNRELIABILITY > 0 and random.randrange(NETWORK_UNRELIABILITY) == 0:
            pass
        else:
            self.context.sock.sendto(
                ackpkt.encode().buffer, (self.context.host, self.context.tidport)
            )
        self.context.last_pkt = ackpkt
    def sendError(self, errorcode):
        """This method uses the socket passed, and uses the errorcode to
        compose and send an error packet."""
        errpkt = TftpPacketERR()
        errpkt.errorcode = errorcode
        if self.context.tidport is None:
            pass
        else:
            self.context.sock.sendto(
                errpkt.encode().buffer, (self.context.host, self.context.tidport)
            )
        self.context.last_pkt = errpkt
    def sendOACK(self):
        """This method sends an OACK packet with the options from the current
        context."""
        pkt = TftpPacketOACK()
        pkt.options = self.context.options
        self.context.sock.sendto(
            pkt.encode().buffer, (self.context.host, self.context.tidport)
        )
        self.context.last_pkt = pkt
    def resendLast(self):
        """Resend the last sent packet due to a timeout."""
        self.context.metrics.resent_bytes += len(self.context.last_pkt.buffer)
        self.context.metrics.add_dup(self.context.last_pkt)
        sendto_port = self.context.tidport
        if not sendto_port:
            sendto_port = self.context.port
        self.context.sock.sendto(
            self.context.last_pkt.encode().buffer, (self.context.host, sendto_port)
        )
        if self.context.packethook:
            self.context.packethook(self.context.last_pkt)
    def handleDat(self, pkt):
        """This method handles a DAT packet during a client download, or a
        server upload."""
        if pkt.blocknumber == self.context.next_block:
            self.sendACK()
            self.context.next_block += 1
            self.context.fileobj.write(pkt.data)
            self.context.metrics.bytes += len(pkt.data)
            if len(pkt.data) < self.context.getBlocksize():
                return None
        elif pkt.blocknumber < self.context.next_block:
            if pkt.blocknumber == 0:
                self.sendError(TftpErrors.IllegalTftpOp)
                raise TftpException("There is no block zero!")
            self.context.metrics.add_dup(pkt)
            self.sendACK(pkt.blocknumber)
        else:
            msg = "Whoa! Received future block %d but expected %d" % (
                pkt.blocknumber,
                self.context.next_block,
            )
            raise TftpException(msg)
        return TftpStateExpectDAT(self.context)

class TftpServerState(TftpState):
    """The base class for server states."""
    def __init__(self, context):
        TftpState.__init__(self, context)
        self.full_path = None
    def serverInitial(self, pkt, raddress, rport):
        """This method performs initial setup for a server context transfer,
        put here to refactor code out of the TftpStateServerRecvRRQ and
        TftpStateServerRecvWRQ classes, since their initial setup is
        identical. The method returns a boolean, sendoack, to indicate whether
        it is required to send an OACK to the client."""
        options = pkt.options
        sendoack = False
        if not self.context.tidport:
            self.context.tidport = rport
        self.context.options = {"blksize": DEF_BLKSIZE}
        if options:
            supported_options = self.returnSupportedOptions(options)
            self.context.options.update(supported_options)
            sendoack = True
        if self.context.host != raddress or self.context.port != rport:
            self.sendError(TftpErrors.UnknownTID)
            return self
        if pkt.filename.startswith(self.context.root):
            full_path = pkt.filename
        else:
            full_path = os.path.join(self.context.root, pkt.filename.lstrip("/"))
        self.full_path = os.path.abspath(full_path)
        if self.full_path.startswith(os.path.normpath(self.context.root) + os.sep):
            pass
        else:
            self.sendError(TftpErrors.IllegalTftpOp)
            raise TftpException("bad file path")
        self.context.file_to_transfer = pkt.filename
        return sendoack

class TftpStateServerRecvRRQ(TftpServerState):
    """This class represents the state of the TFTP server when it has just
    received an RRQ packet."""
    def handle(self, pkt, raddress, rport):
        """Handle an initial RRQ packet as a server."""
        sendoack = self.serverInitial(pkt, raddress, rport)
        path = self.full_path
        if os.path.isfile(path):
            log.info(f'({address[1]}) DoReadFile {os.path.basename(path)} B {os.path.getsize(path)} T 0')
        if os.path.exists(path):
            self.context.fileobj = open(path, "rb")
        elif self.context.dyn_file_func:
            self.context.fileobj = self.context.dyn_file_func(
                self.context.file_to_transfer, raddress=raddress, rport=rport
            )
            if self.context.fileobj is None:
                self.sendError(TftpErrors.FileNotFound)
                raise TftpException("File not found: %s" % path)
        else:
            self.sendError(TftpErrors.FileNotFound)
            raise TftpException(f"File not found: {path}")
        if sendoack and "tsize" in self.context.options:
            self.context.fileobj.seek(0, os.SEEK_END)
            tsize = str(self.context.fileobj.tell())
            self.context.fileobj.seek(0, 0)
            self.context.options["tsize"] = tsize
        if sendoack:
            self.sendOACK()
        else:
            self.context.next_block = 1
            self.context.pending_complete = self.sendDAT()
        return TftpStateExpectACK(self.context)

class TftpStateServerRecvWRQ(TftpServerState):
    """This class represents the state of the TFTP server when it has just
    received a WRQ packet."""
    def make_subdirs(self):
        """The purpose of this method is to, if necessary, create all of the
        subdirectories leading up to the file to the written."""
        subpath = self.full_path[len(self.context.root) :]
        dirs = subpath.split(os.sep)[:-1]
        current = self.context.root
        for dir in dirs:
            if dir:
                current = os.path.join(current, dir)
                if os.path.isdir(current):
                    pass
                else:
                    os.mkdir(current, 0o700)
    def handle(self, pkt, raddress, rport):
        """Handle an initial WRQ packet as a server."""
        sendoack = self.serverInitial(pkt, raddress, rport)
        path = self.full_path
        if self.context.upload_open:
            f = self.context.upload_open(path, self.context)
            if f is None:
                self.sendError(TftpErrors.AccessViolation)
                raise TftpException("Dynamic path %s not permitted" % path)
            else:
                self.context.fileobj = f
        else:
            self.make_subdirs()
            self.context.fileobj = open(path, "wb")
        if sendoack:
            self.sendOACK()
        else:
            self.sendACK()
        self.context.next_block = 1
        return TftpStateExpectDAT(self.context)

class TftpStateServerStart(TftpState):
    """The start state for the server. This is a transitory state since at
    this point we don't know if we're handling an upload or a download. We
    will commit to one of them once we interpret the initial packet."""
    def handle(self, pkt, raddress, rport):
        """Handle a packet we just received."""
        if isinstance(pkt, TftpPacketRRQ):
            return TftpStateServerRecvRRQ(self.context).handle(pkt, raddress, rport)
        elif isinstance(pkt, TftpPacketWRQ):
            return TftpStateServerRecvWRQ(self.context).handle(pkt, raddress, rport)
        else:
            self.sendError(TftpErrors.IllegalTftpOp)
            raise TftpException("Invalid packet to begin up/download: %s" % pkt)

class TftpStateExpectACK(TftpState):
    """This class represents the state of the transfer when a DAT was just
    sent, and we are waiting for an ACK from the server. This class is the
    same one used by the client during the upload, and the server during the
    download."""
    def handle(self, pkt, raddress, rport):
        """Handle a packet, hopefully an ACK since we just sent a DAT."""
        if isinstance(pkt, TftpPacketACK):
            if self.context.next_block == pkt.blocknumber:
                if self.context.pending_complete:
                    return None
                else:
                    self.context.next_block += 1
                    self.context.pending_complete = self.sendDAT()
            elif pkt.blocknumber < self.context.next_block:
                self.context.metrics.add_dup(pkt)
                if self.context.metrics.last_dat_time > 0:
                    if time.time() - self.context.metrics.last_dat_time > self.context.timeout:
                        raise TftpTimeoutExpectACK("Timeout waiting for ACK for block %d" % self.context.next_block)
            else:
                self.context.metrics.errors += 1
            return self
        elif isinstance(pkt, TftpPacketERR):
            raise TftpException("Received ERR packet from peer: %s" % str(pkt))
        else:
            return self

class TftpStateExpectDAT(TftpState):
    """Just sent an ACK packet. Waiting for DAT."""
    def handle(self, pkt, raddress, rport):
        """Handle the packet in response to an ACK, which should be a DAT."""
        if isinstance(pkt, TftpPacketDAT):
            return self.handleDat(pkt)
        elif isinstance(pkt, TftpPacketACK):
            self.sendError(TftpErrors.IllegalTftpOp)
            raise TftpException("Received ACK from peer when expecting DAT")
        elif isinstance(pkt, TftpPacketWRQ):
            self.sendError(TftpErrors.IllegalTftpOp)
            raise TftpException("Received WRQ from peer when expecting DAT")
        elif isinstance(pkt, TftpPacketERR):
            self.sendError(TftpErrors.IllegalTftpOp)
            raise TftpException("Received ERR from peer: " + str(pkt))
        else:
            self.sendError(TftpErrors.IllegalTftpOp)
            raise TftpException("Received unknown packet type from peer: " + str(pkt))

class TftpStateSentWRQ(TftpState):
    """Just sent an WRQ packet for an upload."""
    def handle(self, pkt, raddress, rport):
        """Handle a packet we just received."""
        if not self.context.tidport:
            self.context.tidport = rport
        if isinstance(pkt, TftpPacketOACK):
            try:
                self.handleOACK(pkt)
            except TftpException:
                self.sendError(TftpErrors.FailedNegotiation)
                raise
            else:
                self.context.pending_complete = self.sendDAT()
                return TftpStateExpectACK(self.context)
        elif isinstance(pkt, TftpPacketACK):
            if pkt.blocknumber == 0:
                self.context.pending_complete = self.sendDAT()
                return TftpStateExpectACK(self.context)
            else:
                return self
        elif isinstance(pkt, TftpPacketERR):
            raise TftpException("Received ERR from server: %s" % pkt)
        elif isinstance(pkt, TftpPacketRRQ):
            self.sendError(TftpErrors.IllegalTftpOp)
            raise TftpException("Received RRQ from server while in upload")
        elif isinstance(pkt, TftpPacketDAT):
            self.sendError(TftpErrors.IllegalTftpOp)
            raise TftpException("Received DAT from server while in upload")
        else:
            self.sendError(TftpErrors.IllegalTftpOp)
            raise TftpException("Received unknown packet type from server: %s" % pkt)
        return self

class TftpStateSentRRQ(TftpState):
    """Just sent an RRQ packet."""
    def handle(self, pkt, raddress, rport):
        """Handle the packet in response to an RRQ to the server."""
        if not self.context.tidport:
            self.context.tidport = rport
        if isinstance(pkt, TftpPacketOACK):
            try:
                self.handleOACK(pkt)
            except TftpException as err:
                self.sendError(TftpErrors.FailedNegotiation)
                raise
            else:
                self.sendACK(blocknumber=0)
                return TftpStateExpectDAT(self.context)
        elif isinstance(pkt, TftpPacketDAT):
            if self.context.options:
                self.context.options = {"blksize": DEF_BLKSIZE}
            return self.handleDat(pkt)
        elif isinstance(pkt, TftpPacketACK):
            self.sendError(TftpErrors.IllegalTftpOp)
            raise TftpException("Received ACK from server while in download")
        elif isinstance(pkt, TftpPacketWRQ):
            self.sendError(TftpErrors.IllegalTftpOp)
            raise TftpException("Received WRQ from server while in download")
        elif isinstance(pkt, TftpPacketERR):
            if pkt.errorcode == TftpErrors.FileNotFound:
                raise TftpFileNotFoundError("File not found")
            else:
                raise TftpException(f"Received ERR from server: {pkt}")
        else:
            self.sendError(TftpErrors.IllegalTftpOp)
            raise TftpException("Received unknown packet type from server: %s" % pkt)

class TftpServer(TftpSession):
    """This class implements a tftp server object. Run the listen() method to
    listen for client requests.
    tftproot is the path to the tftproot directory to serve files from and/or
    write them to.
    dyn_file_func is a callable that takes a requested download
    path that is not present on the file system and must return either a
    file-like object to read from or None if the path should appear as not
    found. This permits the serving of dynamic content.
    upload_open is a callable that is triggered on every upload with the
    requested destination path and server context. It must either return a
    file-like object ready for writing or None if the path is invalid."""
    def __init__(self, tftproot="/tftpboot", dyn_file_func=None, upload_open=None, logger=log):
        global log
        log = logger
        self.listenip = None
        self.listenport = None
        self.sock = None
        self.root = os.path.abspath(tftproot)
        self.dyn_file_func = dyn_file_func
        self.upload_open = upload_open
        self.sessions = {}
        self.is_running = threading.Event()
        self.shutdown_gracefully = False
        self.shutdown_immediately = False
        for name in "dyn_file_func", "upload_open":
            attr = getattr(self, name)
            if attr and not callable(attr):
                raise TftpException(f"{name} supplied, but it is not callable.")
        if os.path.exists(self.root):
            if not os.path.isdir(self.root):
                raise TftpException("The tftproot must be a directory.")
            else:
                if not os.access(self.root, os.R_OK):
                    raise TftpException("The tftproot must be readable")
        else:
            raise TftpException("The tftproot does not exist.")
    def listen(
        self,
        listenip="",
        listenport=DEF_TFTP_PORT,
        timeout=SOCK_TIMEOUT,
        retries=DEF_TIMEOUT_RETRIES,
    ):
        """Start a server listening on the supplied interface and port. This
        defaults to INADDR_ANY (all interfaces) and UDP port 69. You can also
        supply a different socket timeout value, if desired."""
        global address
        address = (listenip, listenport)
        tftp_factory = TftpPacketFactory()
        if not listenip:
            listenip = "0.0.0.0"
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.sock.bind((listenip, listenport))
            _, self.listenport = self.sock.getsockname()
        except OSError as err:
            raise err
        self.is_running.set()
        while True:
            if self.shutdown_immediately:
                self.sock.close()
                for key in self.sessions:
                    self.sessions[key].end()
                self.sessions = []
                break
            elif self.shutdown_gracefully:
                if not self.sessions:
                    self.sock.close()
                    break
            inputlist = [self.sock]
            for key in self.sessions:
                inputlist.append(self.sessions[key].sock)
            try:
                readyinput, _, _ = select.select(
                    inputlist, [], [], timeout
                )
            except OSError as err:
                if err[0] == EINTR:
                    continue
                else:
                    raise
            deletion_list = []
            for readysock in readyinput:
                if readysock == self.sock:
                    buffer, (raddress, rport) = self.sock.recvfrom(MAX_BLKSIZE)
                    if self.shutdown_gracefully:
                        continue
                    key = f"{raddress}:{rport}"
                    if key not in self.sessions:
                        self.sessions[key] = TftpContextServer(
                            raddress,
                            rport,
                            timeout,
                            self.root,
                            self.dyn_file_func,
                            self.upload_open,
                            retries=retries,
                        )
                        try:
                            self.sessions[key].start(buffer)
                        except TftpTimeoutExpectACK:
                            self.sessions[key].timeout_expectACK = True
                        except TftpException as err:
                            deletion_list.append(key)
                else:
                    for key in self.sessions:
                        if readysock == self.sessions[key].sock:
                            self.sessions[key].timeout_expectACK = False
                            try:
                                self.sessions[key].cycle()
                                if self.sessions[key].state is None:
                                    deletion_list.append(key)
                            except TftpTimeoutExpectACK:
                                self.sessions[key].timeout_expectACK = True
                            except TftpException as err:
                                deletion_list.append(key)
                            break
            now = time.time()
            for key in self.sessions:
                try:
                    self.sessions[key].checkTimeout(now)
                except TftpTimeout:
                    self.sessions[key].retry_count += 1
                    if self.sessions[key].retry_count >= self.sessions[key].retries:
                        deletion_list.append(key)
                    else:
                        self.sessions[key].state.resendLast()
            for key in deletion_list:
                if key in self.sessions:
                    self.sessions[key].end()
                    del self.sessions[key]
        self.is_running.clear()
        self.shutdown_gracefully = self.shutdown_immediately = False
    def stop(self, now=False):
        """Stop the server gracefully. Do not take any new transfers,
        but complete the existing ones. If force is True, drop everything
        and stop. Note, immediately will not interrupt the select loop, it
        will happen when the server returns on ready data, or a timeout.
        ie. SOCK_TIMEOUT"""
        if now:
            self.shutdown_immediately = True
        else:
            self.shutdown_gracefully = True