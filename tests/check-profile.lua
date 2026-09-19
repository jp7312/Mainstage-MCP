-- Run from package root: lua tests/check-profile.lua
local f = assert(io.open('src/mainstage_mcp/profile.lua', 'rb'))
local source = f:read('*a'); f:close()
local replacements = {__MS_INPUT__ = 'Test Input', __MS_OUTPUT__ = 'Test Output',
    __MS_MANUFACTURER__ = 'Test Maker', __MS_MODEL__ = 'Test Model'}
source = source:gsub('__MS_[A-Z]+__', function(token) return string.format('%q', assert(replacements[token])) end)
source = source:gsub('__MS_EXPERIMENTAL_ACTIONS__', 'false')
source = source:gsub('__MS_EXPERIMENTAL_PARAMETER__', 'true')
local timers = 0
settriggertimer = function(ms) assert(ms == 10); timers = timers + 1 end
MIDI_LSB = 999 -- Host-supplied placeholder for the variable value byte.
assert(load(source, 'profile'))()
local function decode(event)
    assert(event[1] == 240 and event[2] == 125 and event[#event] == 247)
    local chars = {}
    for i = 3, #event - 1 do chars[#chars + 1] = string.char(event[i]) end
    local fields = {}
    for field in (table.concat(chars) .. '\t'):gmatch('(.-)\t') do
        fields[#fields + 1] = field:gsub('%%(%x%x)', function(h) return string.char(tonumber(h, 16)) end)
    end
    assert(fields[1] == 'MSP2')
    return fields
end
local function request(id, port, suffix)
    local raw = string.char(240, 125) .. 'MSP2\trefresh\t' .. id .. string.char(247) .. (suffix or '')
    local event = {}
    for i = 1, #raw do event[i - 1] = raw:byte(i) end
    return controller_midi_in(event, port or 'Test Input')
end
local function refresh(id)
    assert(request(id).midi)
    local result = controller_timer_trigger().midi
    assert(decode(result[1])[2] == 'hello')
    assert(decode(result[1])[6] == 'selection,patch_list,raw_midi_cc,metronome_toggle,action_metronome,mapped_parameter_1')
    return result
end
local init = controller_initialize('MainStage', true)
local session = decode(init.midi[1])[5]
assert(decode(init.midi[1])[4] == '2')
assert(#decode(init.midi[1]) == 6)
assert(decode(init.midi[1])[6] == 'selection,patch_list,raw_midi_cc,metronome_toggle,action_metronome,mapped_parameter_1')
local info = controller_info()
assert(#info.items == 2 and info.model == 'Test Model' and info.version == 2.3)
assert(info.patchselector == nil and type(controller_midi_out) == 'function')
local action = info.items[1]
assert(action.inport == 'Test Input' and action.outport == 'Test Output')
assert(action.objectType == 'Button' and action.midiType == 'Momentary')
assert(action.midi[1] == 0xBF and action.midi[2] == 80 and action.midi[3] == MIDI_LSB)
assert(action.action_mainstage == 'Metronome')
local parameter = info.items[2]
assert(parameter.name == 'MCP Parameter 1' and parameter.objectType == 'VFader')
assert(parameter.inport == 'Test Input' and parameter.outport == 'Test Output')
assert(parameter.midi[1] == 0xBF and parameter.midi[2] == 90 and parameter.midi[3] == MIDI_LSB)
local missing = refresh('initial_0')
assert(decode(missing[2])[3] == 'initial_0' and decode(missing[2])[4] == 'no_snapshot')
local list = {{IsPatch = false, SetIndex = 0, PatchIndex = -1, Label = 'Set'},
    {IsPatch = true, SetIndex = 0, PatchIndex = 0, Label = 'Same'},
    {IsPatch = true, SetIndex = 0, PatchIndex = 1, Label = 'Same'}}
local function select() return controller_select_patch(0, 'Żółć 🎹\t%\n', 'Set', 'Concert', list, 0, 0).midi end
local first = select()
assert(#first == 6 and decode(first[2])[3] == '0' and decode(first[2])[8] == 'Żółć 🎹\t%\n')
assert(decode(first[1])[4] == '1' and decode(first[6])[6] == '3')
assert(#select() == 0)
list[2].Label = 'Rename'; local renamed = select(); assert(decode(renamed[1])[4] == '2')
list[2], list[3] = list[3], list[2]; local reordered = select(); assert(decode(reordered[1])[4] == '3')
local cached = refresh('request-64')
assert(decode(cached[2])[3] == session and decode(cached[2])[4] == '3')
assert(decode(cached[2])[5] == 'request-64' and decode(cached[#cached])[5] == 'request-64')
assert(controller_midi_in({[0] = 144, [1] = 60, [2] = 100}, 'Test Input') == nil)
assert(request('foreign', 'Other Bus') == nil)
assert(controller_midi_in({[0] = 0xBF, [1] = 80, [2] = 127}, 'Test Input') == nil)
assert(controller_midi_in({[0] = 0xBF, [1] = 80, [2] = 127}, 'Other Bus') == nil)
assert(request('echo', 'Test Output').midi and #controller_timer_trigger().midi == 0)
for _, id in ipairs({'', 'bad space', 'bad%20', 'bad\tfield', string.rep('x', 65)}) do
    request(id); assert(#controller_timer_trigger().midi == 0)
end
request('trailing', nil, 'x'); assert(#controller_timer_trigger().midi == 0)
request(string.rep('x', 64)); assert(decode(controller_timer_trigger().midi[2])[5] == string.rep('x', 64))
for i = 1, 17 do request('queue' .. i) end
for i = 1, 16 do assert(decode(controller_timer_trigger().midi[2])[5] == 'queue' .. i) end
assert(#controller_timer_trigger().midi == 0)
local function rejected(items, patch)
    local result = controller_select_patch(0, patch or 'Patch', 'Set', 'Concert', items, 0, 0).midi
    assert(#result == 1 and decode(result[1])[2] == 'error')
    local stale = refresh('rejected_callback')
    assert(#stale == 2 and decode(stale[2])[2] == 'error')
    assert(decode(stale[2])[3] == 'rejected_callback')
    assert(decode(stale[2])[4] == decode(result[1])[4])
    -- Even a callback identical to the last valid selection must restore feedback.
    local recovered = select()
    assert(#recovered == 6 and decode(recovered[1])[2] == 'snapshot_begin')
    assert(decode(refresh('recovered')[2])[2] == 'snapshot_begin')
end
rejected({[2] = list[1]})
rejected({{IsPatch = 'yes', SetIndex = 0, PatchIndex = 0, Label = 'bad'}})
rejected(list, string.rep('%', 23000))
local huge = {}; for i = 1, 4097 do huge[i] = list[1] end; rejected(huge)
local big = {}; for i = 1, 4096 do big[i] = {IsPatch = true, SetIndex = 0, PatchIndex = i, Label = string.rep('x', 1100)} end
rejected(big)
assert(controller_midi_out({[0] = 0xBF, [1] = 91, [2] = 64}, 'Other', '64', nil) == nil)
local callback = controller_midi_out({[0] = 0xBF, [1] = 90, [2] = 90}, 'Cutoff', '1.2 kHz', nil)
local value = decode(callback.midi)
assert(callback.outport == 'Test Output' and value[2] == 'parameter')
assert(value[3] == session and value[5] == '1' and value[6] == 'mapped_parameter_1')
assert(value[7] == '90' and value[8] == '0' and value[9] == '127' and value[10] == 'midi_7bit')
assert(value[11] == 'Cutoff' and value[12] == '1.2 kHz' and value[13] == 'screen_control_feedback')
local replay = refresh('parameter_replay')
local replayed = decode(replay[2])
assert(replayed[2] == 'parameter' and replayed[5] == '1')
assert(decode(replay[3])[2] == 'snapshot_begin', 'replay must precede correlated snapshot barrier')
local changed = controller_midi_out({[0] = 0xBF, [1] = 90, [2] = 91}, nil, nil, nil)
assert(decode(changed.midi)[5] == '2')
assert(#controller_midi_out({[0] = 0xBF, [1] = 90, [2] = 128}, nil, nil, nil).midi == 0)
assert(decode(controller_finalize().midi[1])[3] == session)
assert(#controller_timer_trigger().midi == 0)
local nextSession = decode(controller_initialize('MainStage', false).midi[1])[5]
assert(session ~= nextSession)
assert(decode(refresh('new_session')[2])[4] == 'no_snapshot')
print('profile checks passed: zero, Unicode, duplicates, revisions, correlation, sessions, pass-through, malformed input, bounds')

f = assert(io.open('src/mainstage_mcp/profile.lua', 'rb'))
local experimentalSource = f:read('*a'); f:close()
experimentalSource = experimentalSource:gsub('__MS_[A-Z]+__', function(token)
    return string.format('%q', assert(replacements[token]))
end):gsub('__MS_EXPERIMENTAL_ACTIONS__', 'true'):gsub('__MS_EXPERIMENTAL_PARAMETER__', 'false')
assert(load(experimentalSource, 'experimental profile'))()
local experimentalInfo = controller_info()
assert(#experimentalInfo.items == 4)
local expected = {
    {'Panic', 85, 'PanicFull'}, {'Master Mute', 86, 'MasterMute'},
    {'Play Stop', 87, 'PlayStop'}}
for i, item in ipairs(expected) do
    local control = experimentalInfo.items[i + 1]
    assert(control.name == item[1] and control.midi[1] == 0xBF and control.midi[2] == item[2])
    assert(control.midi[3] == MIDI_LSB and control.inport == 'Test Input' and control.outport == 'Test Output')
    assert(control.action_mainstage == item[3])
end
local experimentalHello = decode(controller_initialize('MainStage', true).midi[1])[6]
for _, name in ipairs({'panic', 'master_mute', 'play_stop'}) do
    assert(experimentalHello:find('action_' .. name, 1, true))
end
print('experimental action checks passed: explicit opt-in, capabilities, port-scoped CC85-87 declarations')

f = assert(io.open('src/mainstage_mcp/profile.lua', 'rb'))
local combinedSource = f:read('*a'); f:close()
combinedSource = combinedSource:gsub('__MS_[A-Z]+__', function(token)
    return string.format('%q', assert(replacements[token]))
end):gsub('__MS_EXPERIMENTAL_ACTIONS__', 'true'):gsub('__MS_EXPERIMENTAL_PARAMETER__', 'true')
assert(load(combinedSource, 'combined experimental profile'))()
local combinedInfo = controller_info()
assert(#combinedInfo.items == 5 and combinedInfo.items[5].midi[2] == 90)
local combinedHello = decode(controller_initialize('MainStage', true).midi[1])[6]
for _, capability in ipairs({'action_panic', 'action_master_mute', 'action_play_stop',
        'mapped_parameter_1'}) do
    assert(combinedHello:find(capability, 1, true))
end
print('combined profile checks passed: both opt-ins render the union')
