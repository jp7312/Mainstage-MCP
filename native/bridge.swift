import Foundation
import CoreMIDI
import Darwin

// MIDI 1.0 packet APIs match MainStage's Lua byte tables. All limits include wire bytes.
let maximumFrame = 65536
// Stdin command lines share the contract's 4096-byte command bound. --decode-hex tokens are exactly
// two hex digits, so a maximum frame fits one line at 3 bytes per frame byte (single separators, CR).
let maximumLine = 4096, maximumHexLine = 3 * maximumFrame
// The stdout backlog counts JSON bytes, about 2x profile.lua wire bytes at worst (\u00XX, \", \\, \/).
// A timer batch (hello with a 1024-byte application, one replayed parameter frame, markers with 64-byte
// ids, a MAX_TOTAL - 1024 snapshot of 4096 items) is < 8.2 MiB of JSON (--self-test builds it). The
// profile holds MAX_PENDING = 16 refreshes (timed-out ones stay queued) and drains them 10 ms apart.
let maximumBacklog = 16 * 9 * 1024 * 1024
let receiveQueue = DispatchQueue(label: "mainstage.mcp.receive")
let outputQueue = DispatchQueue(label: "mainstage.mcp.stdout")
let outputLock = NSLock()
var pendingOutput = 0
func log(_ message: String) { FileHandle.standardError.write(Data((message + "\n").utf8)) }
func fatal(_ message: String) -> Never { log(message); exit(1) }
func encoded(_ object: Any) -> Data {
    guard let data = try? JSONSerialization.data(withJSONObject: object, options: [.sortedKeys]) else { fatal("JSON serialization failed") }
    return data
}
func json(_ object: Any) {
    let data = encoded(object)
    outputLock.lock()
    pendingOutput += data.count + 1
    let overflow = pendingOutput > maximumBacklog
    outputLock.unlock()
    // ponytail: bounded stdout backlog; absorbs the profile's largest legal burst while the consumer
    // briefly stalls; a truly stalled consumer still loses the transport by process exit.
    if overflow { fatal("Transport disconnected: stdout backlog exceeded \(maximumBacklog >> 20) MiB") }
    outputQueue.async {
        do { try FileHandle.standardOutput.write(contentsOf: data + Data([10])) }
        catch { fatal("Transport disconnected: stdout write failed") }
        outputLock.lock(); pendingOutput -= data.count + 1; outputLock.unlock()
    }
}
func decodedMessage(_ bytes: [UInt8]) -> [String: Any]? {
    guard bytes.first == 0x7D, let text = String(bytes: bytes.dropFirst(), encoding: .ascii) else { return nil }
    let parts = text.components(separatedBy: "\t")
    guard parts.count >= 2, parts[0] == "MSP2", ["hello", "snapshot_begin", "selection", "item", "snapshot_end", "parameter", "goodbye", "error"].contains(parts[1]) else { return nil }
    var fields: [String] = []
    for part in parts.dropFirst(2) {
        guard part.utf8.allSatisfy({ (32...126).contains($0) }), let field = part.removingPercentEncoding else { return nil }
        fields.append(field)
    }
    return ["kind": parts[1], "fields": fields]
}
struct SysExParser {
    var pending: [UInt8]? = nil
    mutating func feed(_ bytes: [UInt8], emit: ([String: Any]) -> Void) {
        for byte in bytes {
            if byte >= 0xF8 { continue }
            if byte == 0xF0 { pending = []; continue }
            guard pending != nil else { continue }
            if byte == 0xF7 {
                if let message = decodedMessage(pending!) { emit(message) }
                pending = nil
            } else if byte >= 0x80 { pending = nil }
            else if pending!.count < maximumFrame - 2 { pending!.append(byte) }
            else { pending = nil }
        }
    }
}
// Decode-hex EOF edge: a capture cut off inside another manufacturer's frame is invalid input;
// truncation before any data byte (F0, F0 F8) or after 7D stays tolerable for dev inspection.
func decodableTail(_ pending: [UInt8]?) -> Bool { (pending?.first ?? 0x7D) == 0x7D }
struct Command { let id: String; let bytes: [UInt8]; let quit: Bool }
func token(_ value: Any?) -> String? {
    guard let text = value as? String, (1...64).contains(text.utf8.count),
          text.utf8.allSatisfy({ (48...57).contains($0) || (65...90).contains($0) || (97...122).contains($0) || $0 == 45 || $0 == 95 }) else { return nil }
    return text
}
func command(_ data: Data) -> Command? {
    guard data.count <= maximumLine, let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
          let id = token(object["id"]), let name = object["command"] as? String else { return nil }
    func integer(_ key: String, _ range: ClosedRange<Int>) -> UInt8? {
        guard let value = object[key] as? NSNumber, CFGetTypeID(value) != CFBooleanGetTypeID(),
              !["f", "d"].contains(String(cString: value.objCType)), range.contains(value.intValue) else { return nil }
        return UInt8(value.intValue)
    }
    switch name {
    case "refresh", "quit":
        guard Set(object.keys) == ["id", "command"] else { return nil }
        return Command(id: id, bytes: name == "refresh" ? [0xF0, 0x7D] + Array("MSP2\trefresh\t\(id)".utf8) + [0xF7] : [], quit: name == "quit")
    case "pc":
        guard Set(object.keys) == ["id", "command", "program", "channel"], let program = integer("program", 0...127), let channel = integer("channel", 1...16) else { return nil }
        return Command(id: id, bytes: [0xC0 | (channel - 1), program], quit: false)
    case "cc":
        guard Set(object.keys) == ["id", "command", "control", "value", "channel"], let control = integer("control", 0...127), let value = integer("value", 0...127), let channel = integer("channel", 1...16) else { return nil }
        return Command(id: id, bytes: [0xB0 | (channel - 1), control, value], quit: false)
    default: return nil
    }
}
func hexByte(_ text: String) -> UInt8? {
    func value(_ digit: UInt8) -> UInt8? {
        switch digit {
        case 48...57: return digit - 48
        case 65...70: return digit - 55
        case 97...102: return digit - 87
        default: return nil
        }
    }
    let digits = Array(text.utf8)
    guard digits.count == 2, let high = value(digits[0]), let low = value(digits[1]) else { return nil }
    return high << 4 | low
}
// Bounded stdin line reader shared by the command loop and --decode-hex; unlike readLine(),
// it never buffers more than limit bytes.
struct LineReader {
    var limit = maximumLine
    var line = Data()
    var oversized = false
    // Returns true when byte terminates a line; oversized lines drain without buffering.
    mutating func accept(_ byte: UInt8) -> Bool {
        if byte != 10 {
            if line.count < limit { line.append(byte) } else { oversized = true }
            return false
        }
        return true
    }
    mutating func reset() { line.removeAll(keepingCapacity: true); oversized = false }
}
func readByte() -> UInt8? {
    while true {
        var byte: UInt8 = 0
        let count = Darwin.read(STDIN_FILENO, &byte, 1)
        if count < 0 && errno == EINTR { continue }
        if count <= 0 { return nil }
        return byte
    }
}
// Feeds each newline-terminated line — plus a trailing unterminated line at EOF — through consume;
// consume returns false to stop reading early (quit). Oversized lines pass the drain flag instead
// of their contents.
func readLines(_ limit: Int, _ next: () -> UInt8?, _ consume: (Data, Bool) -> Bool) {
    var reader = LineReader(limit: limit)
    var stopped = false
    while !stopped {
        guard let byte = next() else { break }
        guard reader.accept(byte) else { continue }
        stopped = !consume(reader.line, reader.oversized)
        reader.reset()
    }
    guard !stopped, !reader.line.isEmpty || reader.oversized else { return }
    _ = consume(reader.line, reader.oversized)
    reader.reset()
}
// Returns false on an oversized line, a token that is not two hex digits, or a foreign-frame tail.
func decodeHex(_ next: () -> UInt8?, emit: ([String: Any]) -> Void) -> Bool {
    var parser = SysExParser(), valid = true
    readLines(maximumHexLine, next) { line, oversized in
        let parts = String(decoding: line, as: UTF8.self).split(whereSeparator: { $0.isWhitespace })
        let bytes = parts.compactMap { hexByte(String($0)) }
        valid = !oversized && bytes.count == parts.count
        if valid { parser.feed(bytes, emit: emit) }
        return valid
    }
    return valid && decodableTail(parser.pending)
}
func stringProperty(_ object: MIDIObjectRef, _ property: CFString) -> String {
    var value: Unmanaged<CFString>?
    guard object != 0, MIDIObjectGetStringProperty(object, property, &value) == noErr, let value else { return "" }
    return value.takeRetainedValue() as String
}
func integerProperty(_ object: MIDIObjectRef, _ property: CFString) -> Int32 {
    var value: Int32 = 0
    if object != 0 { MIDIObjectGetIntegerProperty(object, property, &value) }
    return value
}
func endpointInfo(_ endpoint: MIDIEndpointRef, direction: String) -> [String: Any] {
    var entity: MIDIEntityRef = 0; var device: MIDIDeviceRef = 0
    MIDIEndpointGetEntity(endpoint, &entity)
    if entity != 0 { MIDIEntityGetDevice(entity, &device) }
    func inherited(_ property: CFString) -> String {
        for object in [endpoint, entity, device] {
            let value = stringProperty(object, property)
            if !value.isEmpty { return value }
        }
        return ""
    }
    return ["direction": direction, "name": stringProperty(endpoint, kMIDIPropertyName),
            "display_name": stringProperty(endpoint, kMIDIPropertyDisplayName),
            "unique_id": integerProperty(endpoint, kMIDIPropertyUniqueID), "entity": entity,
            "entity_unique_id": integerProperty(entity, kMIDIPropertyUniqueID), "device": device,
            "device_unique_id": integerProperty(device, kMIDIPropertyUniqueID),
            "device_name": stringProperty(device, kMIDIPropertyName),
            "manufacturer": inherited(kMIDIPropertyManufacturer), "model": inherited(kMIDIPropertyModel),
            "driver_owner": inherited(kMIDIPropertyDriverOwner)]
}
func routeIdentity(_ info: [String: Any]) -> [Any] {
    [info["direction"]!, info["name"]!, info["unique_id"]!, info["entity_unique_id"]!,
     info["device_unique_id"]!, info["driver_owner"]!]
}
func usableRoute(_ info: [String: Any]) -> Bool {
    // An endpoint that vanished mid-enumeration reports no names and unique_id 0; emitting it would
    // masquerade as a MIDI configuration change in downstream identity digesting.
    let name = info["name"] as? String ?? "", display = info["display_name"] as? String ?? ""
    return !(name.isEmpty && display.isEmpty) || (info["unique_id"] as? Int32 ?? 0) != 0
}
func check(_ status: OSStatus, _ operation: String) {
    if status != noErr { fatal("\(operation) failed (CoreMIDI \(status))") }
}
func send(_ bytes: [UInt8], output: MIDIPortRef, destination: MIDIEndpointRef) -> OSStatus {
    precondition(!bytes.isEmpty && bytes.count <= 256)
    var list = MIDIPacketList()
    return withUnsafeMutablePointer(to: &list) { pointer in
        let packet = MIDIPacketListInit(pointer)
        _ = bytes.withUnsafeBufferPointer {
            MIDIPacketListAdd(pointer, MemoryLayout<MIDIPacketList>.size, packet, 0, bytes.count, $0.baseAddress!)
        }
        return MIDISend(output, destination, pointer)
    }
}
func selfTest() {
    var parser = SysExParser(); var messages: [[String: Any]] = []
    let frame = [UInt8(0xF0), 0x7D] + Array("MSP2\tselection\tPian%C3%B3%09%25".utf8) + [0xF7]
    parser.feed(Array(frame.prefix(7))) { messages.append($0) }
    parser.feed([0xF8] + Array(frame.dropFirst(7)) + frame) { messages.append($0) }
    precondition(messages.count == 2 && (messages[0]["fields"] as? [String]) == ["Pianó\t%"])
    parser.feed([0xF0, 0x7D, 0x90, 0xF7] + [0xF0] + Array(repeating: 0, count: maximumFrame) + [0xF7] + frame) { messages.append($0) }
    precondition(messages.count == 3 && parser.pending == nil)
    precondition(decodedMessage([0x7D] + Array("MSP2\tparameter\ts\t1\t2\tmapped_parameter_1\t64\t0\t127\tmidi_7bit\tCutoff\t50%25\tscreen_control_feedback".utf8))?["kind"] as? String == "parameter")
    for value in ["%ZZ", "%FF"] { precondition(decodedMessage([0x7D] + Array("MSP2\titem\t\(value)".utf8)) == nil) }
    func parse(_ text: String) -> Command? { command(Data(text.utf8)) }
    for value in ["true", "false", "-1", "128", "1.0", "1e0", "1.5", "null", "\"1\""] {
        precondition(parse("{\"id\":\"x\",\"command\":\"pc\",\"program\":\(value),\"channel\":1}") == nil)
    }
    for channel in [0, 17] { precondition(parse("{\"id\":\"x\",\"command\":\"pc\",\"program\":1,\"channel\":\(channel)}") == nil) }
    precondition(parse("{\"id\":\"x\",\"command\":\"cc\",\"control\":127,\"value\":0,\"channel\":16}")?.bytes == [0xBF,127,0])
    precondition(parse("{\"id\":\"x\",\"command\":\"pc\",\"program\":127,\"channel\":16}")?.bytes == [0xCF,127])
    precondition(parse("{\"id\":\"x\",\"command\":\"refresh\"}")?.bytes == [0xF0,0x7D] + Array("MSP2\trefresh\tx".utf8) + [0xF7])
    for text in ["", "[]", "{}", "{\"id\":\"bad id\",\"command\":\"quit\"}", "{\"id\":\"x\",\"command\":\"quit\",\"extra\":1}"] { precondition(parse(text) == nil) }
    precondition(command(Data(repeating: 32, count: 4097)) == nil)
    // Worst legal timer batch under profile.lua's limits (MAX_FRAME, MAX_TOTAL - 1024 over MAX_ITEMS,
    // 1024-byte application, 64-byte request id; sessions assumed <= 64), measured as json() counts it.
    // Controls and '/' double under JSON escaping. The backlog must hold MAX_PENDING (16) batches.
    func packet(_ kind: String, _ fields: [String]) -> [UInt8] {
        var bytes = [UInt8(0xF0), 0x7D] + Array("MSP2\t\(kind)".utf8)
        for field in fields {
            bytes.append(9)
            for byte in field.utf8 {
                if (32...126).contains(byte) && byte != 37 { bytes.append(byte) } else { bytes += Array(String(format: "%%%02X", byte).utf8) }
            }
        }
        return bytes + [0xF7]
    }
    let session = String(repeating: "s", count: 64), request = String(repeating: "r", count: 64), revision = "9007199254740991"
    let fill = 4194304 - 1024 - packet("selection", ["0", "0", "0", "", "", ""]).count - 4096 * packet("item", ["0", "0", "0", ""]).count
    var snapshot = packet("selection", ["0", "0", "0", "", "", String(repeating: "/", count: fill % 4096)])
    for _ in 0..<4096 { snapshot += packet("item", ["0", "0", "0", String(repeating: "/", count: fill / 4096)]) }
    let parameter = [session, revision, "1", "mapped_parameter_1", "0", "0", "127", "midi_7bit"]
    let slack = maximumFrame - packet("parameter", parameter + ["", "", "screen_control_feedback"]).count
    let replay = packet("parameter", parameter + [String(repeating: "/", count: slack), "", "screen_control_feedback"])
    let capabilities = "selection,patch_list,raw_midi_cc,metronome_toggle,action_metronome,action_panic,action_master_mute,action_play_stop,mapped_parameter_1"
    let hello = packet("hello", [String(repeating: "\u{01}", count: 1024), "2", session, capabilities])
    precondition(snapshot.count == 4194304 - 1024 && replay.count == maximumFrame && hello.count < maximumFrame)
    var worst = SysExParser(), batch = 0, frames = 0
    worst.feed(hello + replay + packet("snapshot_begin", [session, revision, request]) + snapshot + packet("snapshot_end", [session, revision, request, "4096"])) {
        batch += encoded($0).count + 1; frames += 1
    }
    precondition(frames == 4101 && 16 * batch <= maximumBacklog)
    // --decode-hex tokens require exactly two hex digits.
    for (text, byte) in [("7d", UInt8(0x7D)), ("F0", UInt8(0xF0)), ("ab", UInt8(0xAB))] { precondition(hexByte(text) == byte) }
    for text in ["f", "7", "7dd", "0x7d", "+f", "-f", " f", "7d ", "", "zz"] { precondition(hexByte(text) == nil) }
    // --decode-hex takes a maximum frame on one line. Captures may end mid-frame after F0 or 7D, not
    // inside another manufacturer's frame.
    func decoded(_ text: String) -> Int? {
        var bytes = Array(text.utf8)[...], count = 0
        return decodeHex({ bytes.popFirst() }, emit: { _ in count += 1 }) ? count : nil
    }
    let maximal = replay.map { String(format: "%02X", $0) }.joined(separator: " ")
    precondition(decoded(maximal + "\r\n") == 1 && decoded(maximal + " 00") == nil)
    for text in ["", "F0", "F0 F8", "F0\nF8\n", "F0 7D", "F0 7D 4D"] { precondition(decoded(text) == 0) }
    for text in ["F0 41 06", "F0 f", "7D0"] { precondition(decoded(text) == nil) }
    // Line reader stays bounded and flags oversized lines instead of buffering past maximumLine.
    var reader = LineReader()
    for byte in Array("ok".utf8) { precondition(!reader.accept(byte)) }
    precondition(reader.accept(10) && reader.line == Data("ok".utf8) && !reader.oversized)
    reader.reset()
    precondition(reader.line.isEmpty && !reader.oversized)
    for _ in 0..<maximumLine + 64 { precondition(!reader.accept(65)) }
    precondition(reader.oversized && reader.line.count == maximumLine)
    precondition(reader.accept(10))
    // Rows from endpoints that vanished mid-enumeration never reach identity digesting.
    precondition(!usableRoute(["name": "", "display_name": "", "unique_id": Int32(0)] as [String: Any]))
    let surviving: [[String: Any]] = [["name": "Bus", "display_name": "", "unique_id": Int32(0)],
                                      ["name": "", "display_name": "Bus", "unique_id": Int32(0)],
                                      ["name": "", "display_name": "", "unique_id": Int32(7)]]
    for info in surviving { precondition(usableRoute(info)) }
    json(["self_test":"passed", "checks":"fragmentation, realtime, unicode, malformed input, frame bound, strict commands, correlation, timer burst backlog, hex tokens, hex frames and tails, bounded lines, list rows"])
}

func run() {
    let args = Array(CommandLine.arguments.dropFirst())
    if args == ["--self-test"] { selfTest(); return }
    if args == ["--decode-hex"] {
        guard decodeHex(readByte, emit: json) else { fatal("Invalid hex input") }
        return
    }
    let loopback = args == ["--loopback-self-test"]
    let iac = args.count == 4 && args[0] == "--iac-input" && args[2] == "--iac-output" && !args[1].isEmpty && !args[3].isEmpty && args[1] != args[3]
    guard args == ["--list"] || loopback || iac else {
        log("Usage: bridge --list | --self-test | --decode-hex | --loopback-self-test | --iac-input NAME --iac-output DIFFERENT_NAME")
        log("--decode-hex reads stdin lines of whitespace-separated two-digit hex bytes, at most \(maximumHexLine) bytes each"); exit(2)
    }
    let routeLock = NSLock(); var connected = false
    var client: MIDIClientRef = 0
    check(MIDIClientCreateWithBlock("MainStage MCP" as CFString, &client) { _ in
        routeLock.lock(); let wasConnected = connected; connected = false; routeLock.unlock()
        // Conservative invalidation includes unrelated device changes; restart revalidates identity.
        if wasConnected { json(["kind":"transport", "connected":false, "reason":"MIDI configuration changed; restart required"]) }
    }, "Create MIDI client")
    defer { MIDIClientDispose(client) }
    if args == ["--list"] {
        for index in 0..<MIDIGetNumberOfSources() {
            let info = endpointInfo(MIDIGetSource(index), direction: "source")
            if usableRoute(info) { json(info) }
        }
        for index in 0..<MIDIGetNumberOfDestinations() {
            let info = endpointInfo(MIDIGetDestination(index), direction: "destination")
            if usableRoute(info) { json(info) }
        }
        return
    }
    var parser = SysExParser(); let receiveSlots = DispatchSemaphore(value: 64)
    let loopbackDone = DispatchSemaphore(value: 0)
    let readBlock: MIDIReadBlock = { packets, _ in
        guard receiveSlots.wait(timeout: .now()) == .success else { fatal("Transport disconnected: receive queue overflow") }
        var bytes: [UInt8] = []
        var packet = UnsafeRawPointer(packets).advanced(by: MemoryLayout<MIDIPacketList>.offset(of: \.packet)!).assumingMemoryBound(to: MIDIPacket.self)
        for _ in 0..<packets.pointee.numPackets {
            let count = Int(packet.pointee.length)
            guard bytes.count + count <= maximumFrame else { fatal("Transport disconnected: receive batch overflow") }
            let address = UnsafeRawPointer(packet).advanced(by: MemoryLayout<MIDIPacket>.offset(of: \.data)!)
            bytes.append(contentsOf: UnsafeRawBufferPointer(start: address, count: count))
            packet = UnsafePointer(MIDIPacketNext(packet))
        }
        let copied = bytes
        receiveQueue.async {
            defer { receiveSlots.signal() }
            routeLock.lock(); defer { routeLock.unlock() }
            guard connected || loopback else { return }
            parser.feed(copied) { message in
                json(message)
                if loopback, message["kind"] as? String == "hello" { loopbackDone.signal() }
            }
        }
    }
    var destination: MIDIEndpointRef = 0; var input: MIDIPortRef = 0; var output: MIDIPortRef = 0
    check(MIDIOutputPortCreate(client, "Commands" as CFString, &output), "Create output")
    if loopback {
        check(MIDIDestinationCreateWithBlock(client, "MainStage MCP TEST ONLY" as CFString, &destination, readBlock), "Create test destination")
        check(send([0xF0,0x7D] + Array("MSP2\thello\tloopback\t2\ttest".utf8) + [0xF7], output:output, destination:destination), "Loopback send")
        guard loopbackDone.wait(timeout:.now() + 3) == .success else { fatal("CoreMIDI loopback timed out") }
        receiveQueue.sync {}; json(["loopback_test":"passed"]); return
    }
    func find(_ name: String, source: Bool) -> MIDIEndpointRef {
        let count = source ? MIDIGetNumberOfSources() : MIDIGetNumberOfDestinations()
        let matches = (0..<count).map { source ? MIDIGetSource($0) : MIDIGetDestination($0) }.filter { stringProperty($0,kMIDIPropertyName) == name }
        guard matches.count == 1 else { fatal("Expected one endpoint named '\(name)', found \(matches.count); configure dedicated IAC buses manually") }
        return matches[0]
    }
    destination = find(args[1], source:false)
    let source = find(args[3], source:true)
    let first = endpointInfo(destination,direction:"destination"), second = endpointInfo(source,direction:"source")
    guard first["driver_owner"] as? String == "com.apple.AppleMIDIIACDriver", second["driver_owner"] as? String == "com.apple.AppleMIDIIACDriver",
          let device = first["device"] as? UInt32, device != 0, second["device"] as? UInt32 == device,
          first["entity"] as? UInt32 != second["entity"] as? UInt32 else { fatal("Endpoints must be distinct buses on the same verified Apple IAC driver device") }
    // A persistent lock inode avoids unlink/reopen races; the kernel releases ownership on exit.
    var lockFDs: [Int32] = []
    defer { for descriptor in lockFDs { close(descriptor) } }
    let identities = [first["entity_unique_id"] as! Int32, second["entity_unique_id"] as! Int32].sorted()
    for identity in identities {
        let lockPath = NSTemporaryDirectory() + "mainstage-mcp-\(getuid())-\(identity).lock"
        let descriptor = open(lockPath, O_CREAT | O_RDWR | O_NOFOLLOW, S_IRUSR | S_IWUSR)
        guard descriptor >= 0, flock(descriptor, LOCK_EX | LOCK_NB) == 0 else { fatal("An IAC bus is already owned or its lock is unavailable") }
        lockFDs.append(descriptor)
    }
    check(MIDIInputPortCreateWithBlock(client, "Feedback" as CFString, &input, readBlock), "Create input")
    check(MIDIPortConnectSource(input, source, nil), "Connect feedback")
    routeLock.lock(); connected = true; routeLock.unlock()
    json(["kind":"transport", "connected":true, "reason":"Verified IAC endpoint pair connected",
          "route":[routeIdentity(first), routeIdentity(second)]])
    // Bounded reader with the same line discipline for every input, including a trailing
    // unterminated line at EOF, which still gets one validate/parse pass.
    readLines(maximumLine, readByte) { line, oversized in
        guard !oversized, let request = command(line) else {
            let object = (try? JSONSerialization.jsonObject(with:line)) as? [String:Any]
            json(["kind":"command_result", "id":token(object?["id"]) ?? "", "ok":false, "status":-50, "error":"Invalid command (4096-byte limit; strict JSON fields and integer ranges)"])
            return true
        }
        if request.quit { json(["kind":"command_result", "id":request.id, "ok":true, "status":0]); return false }
        routeLock.lock()
        if connected && (!NSDictionary(dictionary:first).isEqual(to:endpointInfo(destination,direction:"destination")) || !NSDictionary(dictionary:second).isEqual(to:endpointInfo(source,direction:"source"))) {
            connected = false
            json(["kind":"transport", "connected":false, "reason":"Endpoint identity changed; restart required"])
        }
        let status: OSStatus = connected ? send(request.bytes,output:output,destination:destination) : -1
        routeLock.unlock()
        var result: [String:Any] = ["kind":"command_result", "id":request.id, "ok":status == 0, "status":status]
        if status != 0 { result["error"] = "MIDI send failed or transport disconnected" }
        json(result)
        return true
    }
    receiveQueue.sync {}
}
run()
outputQueue.sync {}
