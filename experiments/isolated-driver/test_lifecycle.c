#include <CoreMIDI/CoreMIDI.h>
#include <CoreMIDI/MIDIDriver.h>
#include <assert.h>
#include <stdio.h>

/* These executable-local mocks replace every CoreMIDI call exercised below. */
const CFStringRef kMIDIPropertyDriverOwner = CFSTR("driverOwner");
const CFStringRef kMIDIPropertyManufacturer = CFSTR("manufacturer");
const CFStringRef kMIDIPropertyModel = CFSTR("model");
const CFStringRef kMIDIPropertyName = CFSTR("name");
const CFStringRef kMIDIPropertyOffline = CFSTR("offline");
const CFStringRef kMIDIPropertyUniqueID = CFSTR("uniqueID");
const CFStringRef kMIDIPropertyAdvanceScheduleTimeMuSec = CFSTR("advanceScheduleTimeMuSec");
static ItemCount present;
static SInt32 offline;
static unsigned creates, entities_added, removes, disposes;
static Boolean invalid_model;
static OSStatus add_status;
static uintptr_t refcons[2];
ItemCount MIDIGetNumberOfDevices(void) { return present; }
MIDIDeviceRef MIDIGetDevice(ItemCount i) { return (MIDIDeviceRef)(10 + i); }
ItemCount MIDIDeviceListGetNumberOfDevices(MIDIDeviceListRef list) { (void)list; return present; }
MIDIDeviceRef MIDIDeviceListGetDevice(MIDIDeviceListRef list, ItemCount i) { (void)list; return MIDIGetDevice(i); }
ItemCount MIDIDeviceGetNumberOfEntities(MIDIDeviceRef d) { (void)d; return 2; }
MIDIEntityRef MIDIDeviceGetEntity(MIDIDeviceRef d, ItemCount i) { (void)d; return (MIDIEntityRef)(20 + i); }
ItemCount MIDIEntityGetNumberOfSources(MIDIEntityRef e) { (void)e; return 1; }
ItemCount MIDIEntityGetNumberOfDestinations(MIDIEntityRef e) { (void)e; return 1; }
MIDIEndpointRef MIDIEntityGetSource(MIDIEntityRef e, ItemCount i) { assert(i == 0); return e + 10; }
MIDIEndpointRef MIDIEntityGetDestination(MIDIEntityRef e, ItemCount i) { assert(i == 0); return e + 20; }
OSStatus MIDIObjectGetStringProperty(MIDIObjectRef object, CFStringRef key, CFStringRef *out) {
    CFStringRef value = NULL;
    if (CFEqual(key, kMIDIPropertyDriverOwner)) value = CFSTR("org.mainstage-mcp.driver");
    else if (CFEqual(key, kMIDIPropertyManufacturer)) value = CFSTR("MainStage MCP");
    else if (CFEqual(key, kMIDIPropertyModel)) value = invalid_model ? CFSTR("Foreign") : CFSTR("MainStage MCP Bridge");
    else if (CFEqual(key, kMIDIPropertyName)) value = object % 2 ? CFSTR("MainStage MCP Output") : CFSTR("MainStage MCP Input");
    if (!value) return -50;
    *out = CFRetain(value);
    return noErr;
}
OSStatus MIDIObjectGetIntegerProperty(MIDIObjectRef object, CFStringRef key, SInt32 *out) {
    if (CFEqual(key, kMIDIPropertyOffline)) *out = offline;
    else if (CFEqual(key, kMIDIPropertyUniqueID)) *out = (SInt32)object + 100;
    else return -50;
    return noErr;
}
OSStatus MIDIObjectSetIntegerProperty(MIDIObjectRef object, CFStringRef key, SInt32 value) {
    assert(object == 10);
    if (CFEqual(key, kMIDIPropertyOffline)) offline = value;
    else assert(CFEqual(key, kMIDIPropertyAdvanceScheduleTimeMuSec) && value == 0);
    return noErr;
}
OSStatus MIDIObjectSetStringProperty(MIDIObjectRef object, CFStringRef key, CFStringRef value) {
    CFStringRef expected;
    assert(!MIDIObjectGetStringProperty(object, key, &expected));
    assert(CFEqual(expected, value)); CFRelease(expected);
    return noErr;
}
OSStatus MIDIDeviceCreate(MIDIDriverRef owner, CFStringRef name, CFStringRef manufacturer, CFStringRef model, MIDIDeviceRef *out) {
    assert(owner && CFEqual(name, model) && CFEqual(manufacturer, CFSTR("MainStage MCP")));
    ++creates; *out = 10; return noErr;
}
OSStatus MIDIDeviceAddEntity(MIDIDeviceRef device, CFStringRef name, Boolean embedded, ItemCount sources, ItemCount destinations, MIDIEntityRef *out) {
    assert(device == 10 && embedded && sources == 1 && destinations == 1);
    *out = 20 + entities_added++;
    return MIDIObjectSetStringProperty(*out, kMIDIPropertyName, name);
}
OSStatus MIDIEndpointSetRefCons(MIDIEndpointRef endpoint, void *a, void *b) {
    assert(endpoint == 40 || endpoint == 41); assert(!b);
    refcons[endpoint - 40] = (uintptr_t)a; return noErr;
}
OSStatus MIDISetupAddDevice(MIDIDeviceRef device) { assert(device == 10); if (!add_status) present = 1; return add_status; }
OSStatus MIDIDeviceDispose(MIDIDeviceRef device) { assert(device == 10); ++disposes; return noErr; }
OSStatus MIDISetupRemoveDevice(MIDIDeviceRef device) { assert(device == 10 && offline == 1); ++removes; present = 0; return noErr; }

#include "driver.c"
#define REMOVE_DEVICE_TEST
#include "remove_device.c"

int main(void) {
    Driver driver = {.interface = &interface};
    atomic_init(&driver.references, 1);
    atomic_init(&driver.sources[0], 0);
    atomic_init(&driver.sources[1], 0);
    MIDIDriverRef ref = (MIDIDriverRef)&driver;
    assert(start(ref, 1) == noErr);
    assert(present == 1 && creates == 1 && entities_added == 2 && offline == 0);
    assert(atomic_load(&driver.sources[0]) == 30 && atomic_load(&driver.sources[1]) == 31);
    assert(refcons[0] == 1 && refcons[1] == 2);
    assert(remove_owned(true) == 1 && removes == 0); /* Online removal forbidden. */
    assert(stop(ref) == noErr && offline == 1);
    assert(start(ref, 1) == noErr && creates == 1 && entities_added == 2); /* Reuse. */
    assert(stop(ref) == noErr);
    invalid_model = true;
    assert(remove_owned(true) == 1 && removes == 0);
    assert(start(ref, 1) != noErr && creates == 1);
    invalid_model = false;
    present = 2;
    assert(remove_owned(true) == 1 && removes == 0);
    assert(start(ref, 1) != noErr && creates == 1);
    present = 1;
    assert(remove_owned(false) == 0 && removes == 0); /* Read-only preflight. */
    assert(remove_owned(true) == 0 && removes == 1 && present == 0);
    assert(remove_owned(true) == 0 && removes == 1); /* Already absent. */
    entities_added = 0; add_status = -123;
    assert(start(ref, 1) == -123 && disposes == 1 && present == 0);
    puts("mock create/reuse/failure/offline-removal checks passed; no live MIDI calls");
}
