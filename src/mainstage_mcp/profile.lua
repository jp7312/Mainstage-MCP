-- Original feedback-only profile. Installer replaces tokens with Lua string literals.
local INPUT, OUTPUT = __MS_INPUT__, __MS_OUTPUT__
local MANUFACTURER, MODEL = __MS_MANUFACTURER__, __MS_MODEL__
local EXPERIMENTAL_ACTIONS = __MS_EXPERIMENTAL_ACTIONS__
local EXPERIMENTAL_PARAMETER = __MS_EXPERIMENTAL_PARAMETER__
local MAX_FRAME, MAX_ITEMS, MAX_TOTAL, MAX_PENDING = 65536, 4096, 4194304, 16
local application, session, sessionAnchor, generation = nil, nil, nil, 0
local snapshot, snapshotKey, revision, pending = nil, nil, 0, {}
local snapshotError = nil
local capabilities = {'selection', 'patch_list', 'raw_midi_cc', 'metronome_toggle', 'action_metronome'}
local actionItems = {{name = 'Metronome', objectType = 'Button', midiType = 'Momentary',
    midi = {0xBF, 80, MIDI_LSB}, inport = INPUT, outport = OUTPUT,
    action_mainstage = 'Metronome'}}
if EXPERIMENTAL_ACTIONS then
    local experimental = {
        {'Panic', 85, 'PanicFull'}, {'Master Mute', 86, 'MasterMute'},
        {'Play Stop', 87, 'PlayStop'}}
    local names = {'panic', 'master_mute', 'play_stop'}
    for i, item in ipairs(experimental) do
        local control = {name = item[1], objectType = 'Button', midiType = 'Momentary',
            midi = {0xBF, item[2], MIDI_LSB}, inport = INPUT, outport = OUTPUT}
        control.action_mainstage = item[3]
        actionItems[#actionItems + 1] = control
        capabilities[#capabilities + 1] = 'action_' .. names[i]
    end
end
local parameterFeedback, parameterSequence = nil, 0
if EXPERIMENTAL_PARAMETER then
    actionItems[#actionItems + 1] = {name = 'MCP Parameter 1', objectType = 'VFader',
        midi = {0xBF, 90, MIDI_LSB}, inport = INPUT, outport = OUTPUT}
    capabilities[#capabilities + 1] = 'mapped_parameter_1'
end

-- Percent-encode bytes outside printable ASCII plus '%' itself; table lookups avoid
-- paying string.format per byte. The class is negated because Lua 5.1 patterns end at NUL.
local encodings = {}
for byte = 0, 255 do
    encodings[string.char(byte)] = (byte < 32 or byte > 126 or byte == 37)
        and string.format('%%%02X', byte) or string.char(byte)
end
local function escape(value)
    return (tostring(value):gsub('[^ -$&-~]', encodings))
end

local function packet(kind, fields)
    local parts = {'MSP2', kind}
    for _, value in ipairs(fields or {}) do
        if type(value) == 'string' and #value > MAX_FRAME then return nil end
        parts[#parts + 1] = escape(value)
    end
    local text = table.concat(parts, '\t')
    if #text + 3 > MAX_FRAME then return nil end
    local bytes = {0xF0, 0x7D}
    -- string.byte's multiple returns hit the Lua 5.1 stack cap near 8000; chunk well below it.
    for i = 1, #text, 1024 do
        local chunk = {string.byte(text, i, math.min(i + 1023, #text))}
        local base = #bytes
        for k = 1, #chunk do bytes[base + k] = chunk[k] end
    end
    bytes[#bytes + 1] = 0xF7
    return bytes
end

local function output(events) return {outport = OUTPUT, midi = events or {}} end
local function failure(id, code, message) return (packet('error', {id, code, message})) end
local function reject(code, message)
    snapshotError = {code, message}
    return output({failure('', code, message)})
end
local function integer(value, low, high)
    return type(value) == 'number' and value == math.floor(value) and value >= low and value <= high
end
local function index(value) return integer(value, -1, 2147483647) end
-- Well-formed UTF-8 (RFC 3629) left to right: each lead byte selects an anchored pattern for
-- its trail bytes (no overlongs, surrogates or > U+10FFFF) plus the ASCII run after it.
-- Other leads (lone continuations, C0, C1, F5-FF) and truncated sequences fail.
local utf8Trails = {}
for byte = 0xC2, 0xF4 do
    local second = byte == 0xE0 and '[\160-\191]' or byte == 0xED and '[\128-\159]'
        or byte == 0xF0 and '[\144-\191]' or byte == 0xF4 and '[\128-\143]' or '[\128-\191]'
    utf8Trails[byte] = '^' .. second .. string.rep('[\128-\191]', byte < 0xE0 and 0 or byte < 0xF0 and 1 or 2)
        .. '[^\128-\255]*()'
end
local function utf8(value)
    local i = value:find('[\128-\255]')
    while i and i <= #value do
        local trail = utf8Trails[value:byte(i)]
        i = trail and value:match(trail, i + 1)
        if not i then return false end
    end
    return true
end
local function text(value) return type(value) == 'string' and #value <= MAX_FRAME end
-- Dedup key tags raw values by type and length, so distinct field tuples cannot collide
-- without paying for escaping first.
local function tag(value)
    local kind = type(value)
    if kind == 'string' then return 's' .. #value .. ':' .. value end
    if kind == 'number' then return 'n' .. value end
    return kind == 'boolean' and (value and 'b1' or 'b0') or 'x'
end
local function hello() return (packet('hello', {application, 2, session, table.concat(capabilities, ',')})) end
local function complete(id)
    if snapshotError then return {failure(id, snapshotError[1], snapshotError[2])} end
    if not snapshot then return {failure(id, 'no_snapshot', 'No selection callback received')} end
    local events = {(packet('snapshot_begin', {session, revision, id}))}
    for _, event in ipairs(snapshot) do events[#events + 1] = event end
    events[#events + 1] = packet('snapshot_end', {session, revision, id, #snapshot - 1})
    return events
end

function controller_initialize(applicationName, deviceNewlyDetected)
    application = type(applicationName) == 'string' and applicationName or ''
    -- Hello has no rejection channel, so unsuitable names fall back to empty.
    if #application > 1024 or not utf8(application) then application = '' end
    generation = generation + 1
    -- Keep the table alive; the host need not expose clocks, IO or random numbers.
    sessionAnchor = {}
    session = tostring(sessionAnchor):gsub('[^A-Za-z0-9]', '') .. '_' .. generation
    snapshot, snapshotKey, revision, pending, snapshotError = nil, nil, 0, {}, nil
    parameterFeedback, parameterSequence = nil, 0
    return output({hello()})
end

function controller_finalize()
    local events = session and {(packet('goodbye', {session}))} or {}
    application, session, sessionAnchor, snapshot, snapshotKey, pending = nil, nil, nil, nil, nil, {}
    parameterFeedback, parameterSequence = nil, 0
    snapshotError = nil
    return output(events)
end

function controller_select_patch(program, patch, set, concert, list, setIndex, patchIndex)
    if not session then return output() end
    if not integer(program, -1, 127) or not index(setIndex) or not index(patchIndex)
        or not text(patch) or not text(set) or not text(concert) or type(list) ~= 'table' then
        return reject('invalid_snapshot', 'Invalid selection fields')
    end
    local count, tags = 0, {tag(program), tag(setIndex), tag(patchIndex),
        tag(concert), tag(set), tag(patch)}
    for key in pairs(list) do
        count = count + 1
        if count > MAX_ITEMS or not integer(key, 1, MAX_ITEMS) then
            return reject('snapshot_limit', 'Invalid or oversized list')
        end
    end
    for i = 1, count do
        local item = list[i]
        if type(item) == 'table' then
            tags[#tags + 1] = tag(item.IsPatch)
            tags[#tags + 1] = tag(item.SetIndex)
            tags[#tags + 1] = tag(item.PatchIndex)
            tags[#tags + 1] = tag(item.Label)
        else
            tags[#tags + 1] = 'x'
        end
    end
    local key = table.concat(tags)
    if key == snapshotKey and not snapshotError then return output() end
    if not utf8(patch) or not utf8(set) or not utf8(concert) then
        return reject('invalid_snapshot', 'Invalid selection fields')
    end
    local events, total = {}, 0
    local function append(kind, fields)
        local event = packet(kind, fields)
        if not event then return false end
        total = total + #event
        -- Reserve space for begin/end markers including the longest request token.
        if total > MAX_TOTAL - 1024 then return false end
        events[#events + 1] = event
        return true
    end
    if not append('selection', {program, setIndex, patchIndex, concert, set, patch}) then
        return reject('snapshot_limit', 'Selection exceeds frame limit')
    end
    for i = 1, count do
        local item = list[i]
        if type(item) ~= 'table' or type(item.IsPatch) ~= 'boolean' or not index(item.SetIndex)
            or not index(item.PatchIndex) or not text(item.Label) or not utf8(item.Label) then
            return reject('invalid_snapshot', 'Invalid list item')
        end
        if not append('item', {item.IsPatch and 1 or 0, item.SetIndex, item.PatchIndex, item.Label}) then
            return reject('snapshot_limit', 'Snapshot exceeds byte limit')
        end
    end
    -- ponytail: resend whole lists; introduce deltas only for measured large-list latency.
    snapshot, snapshotKey, revision, snapshotError = events, key, revision + 1, nil
    return output(complete(''))
end

local prefix = {0xF0, 0x7D, 77, 83, 80, 50, 9}
function controller_midi_in(event, portName)
    if portName ~= INPUT and portName ~= OUTPUT then return nil end
    for i, byte in ipairs(prefix) do if event[i - 1] ~= byte then return nil end end
    if portName == OUTPUT then return {midi = {}} end
    -- Host MIDI input arrays are zero-indexed. Only bounded ASCII requests are accepted.
    local chars, ended = {}, false
    for i = #prefix, 85 do
        local byte = event[i]
        if byte == 0xF7 then
            if event[i + 1] ~= nil then return {midi = {}} end
            ended = true
            break
        end
        if not integer(byte, 0, 127) then return {midi = {}} end
        chars[#chars + 1] = string.char(byte)
    end
    local id = ended and table.concat(chars):match('^refresh\t([A-Za-z0-9_-]+)$') or nil
    if not id or #id > 64 or not session then return {midi = {}} end
    if #pending >= MAX_PENDING then return {midi = {}} end
    pending[#pending + 1] = id
    settriggertimer(10)
    return {midi = {}}
end

function controller_timer_trigger()
    local id = table.remove(pending, 1)
    if not id or not session then return output() end
    local events = {hello()}
    -- Replay preserves its sequence and precedes the correlated snapshot barrier.
    if parameterFeedback then events[#events + 1] = parameterFeedback end
    for _, event in ipairs(complete(id)) do events[#events + 1] = event end
    if #pending > 0 then settriggertimer(10) end
    return output(events)
end

if EXPERIMENTAL_PARAMETER then
    function controller_midi_out(midiEvent, name, valueString, color)
        if not session or not snapshot or midiEvent[0] ~= 0xBF or midiEvent[1] ~= 90 then return nil end
        if not integer(midiEvent[2], 0, 127) then return output() end
        local function scalar(value)
            if value == nil then return '' end
            if type(value) == 'string' or type(value) == 'number' then return tostring(value) end
            return ''
        end
        local label, display = scalar(name), scalar(valueString)
        -- Invalid UTF-8 would make Swift silently drop the frame; drop it here instead.
        if not utf8(label) or not utf8(display) then return output() end
        local event = packet('parameter', {session, revision, parameterSequence + 1,
            'mapped_parameter_1', midiEvent[2], 0, 127, 'midi_7bit',
            label, display, 'screen_control_feedback'})
        if not event then return output() end
        parameterSequence, parameterFeedback = parameterSequence + 1, event
        return output(event)
    end
end

function controller_info()
    -- Only the metronome route is live verified; experimental routes require explicit install opt-in.
    return {model = MODEL, manufacturer = MANUFACTURER, version = 2.3, logicprox = false,
        items = actionItems}
end
