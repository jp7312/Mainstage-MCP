#include <CoreMIDI/CoreMIDI.h>
#include <stdio.h>
#include <string.h>

static Boolean matches(MIDIObjectRef object, CFStringRef key, CFStringRef expected) {
    CFStringRef value = NULL;
    if (MIDIObjectGetStringProperty(object, key, &value)) return false;
    Boolean result = CFEqual(value, expected);
    CFRelease(value);
    return result;
}

static Boolean has_id(MIDIObjectRef object) {
    SInt32 unique_id = 0;
    return object && !MIDIObjectGetIntegerProperty(object, kMIDIPropertyUniqueID, &unique_id) && unique_id;
}

static Boolean valid_device(MIDIDeviceRef device) {
    if (!has_id(device) ||
        !matches(device, kMIDIPropertyDriverOwner, CFSTR("org.mainstage-mcp.driver")) ||
        !matches(device, kMIDIPropertyManufacturer, CFSTR("MainStage MCP")) ||
        !matches(device, kMIDIPropertyModel, CFSTR("MainStage MCP Bridge")) ||
        MIDIDeviceGetNumberOfEntities(device) != 2) return false;
    const CFStringRef names[] = {CFSTR("MainStage MCP Input"), CFSTR("MainStage MCP Output")};
    for (unsigned i = 0; i < 2; ++i) {
        MIDIEntityRef entity = MIDIDeviceGetEntity(device, i);
        if (!has_id(entity) || !matches(entity, kMIDIPropertyName, names[i]) ||
            MIDIEntityGetNumberOfSources(entity) != 1 ||
            MIDIEntityGetNumberOfDestinations(entity) != 1) return false;
        MIDIEndpointRef source = MIDIEntityGetSource(entity, 0);
        MIDIEndpointRef destination = MIDIEntityGetDestination(entity, 0);
        if (!has_id(source) || !has_id(destination) ||
            !matches(source, kMIDIPropertyName, names[i]) ||
            !matches(destination, kMIDIPropertyName, names[i])) return false;
    }
    return true;
}

static int remove_owned(Boolean remove_device) {
    MIDIDeviceRef owned = 0;
    for (ItemCount i = 0; i < MIDIGetNumberOfDevices(); ++i) {
        MIDIDeviceRef device = MIDIGetDevice(i);
        if (!matches(device, kMIDIPropertyDriverOwner, CFSTR("org.mainstage-mcp.driver"))) continue;
        if (owned) { fputs("Refusing: multiple owned devices.\n", stderr); return 1; }
        owned = device;
    }
    if (!owned) { puts("Owned device absent; nothing to remove."); return 0; }
    SInt32 offline = 0;
    if (!valid_device(owned) || MIDIObjectGetIntegerProperty(owned, kMIDIPropertyOffline, &offline)) {
        fputs("Refusing: owned device identity/topology could not be verified.\n", stderr);
        return 1;
    }
    if (offline != 1) { fputs("Refusing: owned device is online.\n", stderr); return 1; }
    if (!remove_device) { puts("Exactly one validated owned device is offline; removal is eligible."); return 0; }
    /* Recheck immediately before mutation; remove only an explicitly retired offline device. */
    if (!valid_device(owned) || MIDIObjectGetIntegerProperty(owned, kMIDIPropertyOffline, &offline) || offline != 1) return 1;
    OSStatus status = MIDISetupRemoveDevice(owned);
    if (status) { fprintf(stderr, "MIDISetupRemoveDevice failed: %d\n", (int)status); return 1; }
    puts("Removed the validated offline MainStage MCP device.");
    return 0;
}

#ifndef REMOVE_DEVICE_TEST
int main(int argc, char **argv) {
    if (argc != 2 || (strcmp(argv[1], "--check") && strcmp(argv[1], "--remove-offline-owned-device"))) {
        fputs("Usage: remove_device --check | --remove-offline-owned-device\n", stderr);
        return 2;
    }
    MIDIClientRef client = 0;
    OSStatus status = MIDIClientCreate(CFSTR("MainStage MCP explicit uninstall"), NULL, NULL, &client);
    if (status) { fprintf(stderr, "MIDIClientCreate failed: %d\n", (int)status); return 1; }
    int result = remove_owned(!strcmp(argv[1], "--remove-offline-owned-device"));
    MIDIClientDispose(client);
    return result;
}
#endif
