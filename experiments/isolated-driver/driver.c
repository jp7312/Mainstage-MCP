#include <CoreFoundation/CFPlugInCOM.h>
#include <CoreMIDI/CoreMIDI.h>
#include <CoreMIDI/MIDIDriver.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdlib.h>

#define OWNER CFSTR("org.mainstage-mcp.driver")
#define MANUFACTURER CFSTR("MainStage MCP")
#define MODEL CFSTR("MainStage MCP Bridge")
#define FACTORY_ID CFUUIDGetConstantUUIDWithBytes(NULL, \
    0x61,0x28,0x23,0xDD,0xEE,0xF0,0x4D,0x1A,0x96,0xC6,0x27,0x52,0x38,0x58,0x87,0x34)

typedef struct {
    MIDIDriverInterface *interface; /* CFPlugIn ABI: first member is vtable pointer. */
    _Atomic UInt32 references;
    MIDIDeviceRef device;
    _Atomic MIDIEndpointRef sources[2];
} Driver;

static ULONG add_ref(void *self) {
    return atomic_fetch_add(&((Driver *)self)->references, 1) + 1;
}

static ULONG release(void *self) {
    UInt32 remaining = atomic_fetch_sub(&((Driver *)self)->references, 1) - 1;
    if (!remaining) {
        free(self);
        CFPlugInRemoveInstanceForFactory(FACTORY_ID);
    }
    return remaining;
}

static HRESULT query(void *self, REFIID iid, void **result) {
    if (!result) return E_POINTER;
    *result = NULL;
    CFUUIDRef uuid = CFUUIDCreateFromUUIDBytes(NULL, iid);
    Boolean supported = CFEqual(uuid, IUnknownUUID) || CFEqual(uuid, kMIDIDriverInterface2ID);
    CFRelease(uuid);
    if (!supported) return E_NOINTERFACE;
    add_ref(self);
    *result = self;
    return S_OK;
}

static Boolean named(MIDIObjectRef object, CFStringRef property, CFStringRef expected) {
    CFStringRef value = NULL;
    if (MIDIObjectGetStringProperty(object, property, &value)) return false;
    Boolean equal = CFEqual(value, expected);
    CFRelease(value);
    return equal;
}

static OSStatus start(MIDIDriverRef self, MIDIDeviceListRef devices) {
    Driver *driver = (Driver *)self;
    atomic_store(&driver->sources[0], 0);
    atomic_store(&driver->sources[1], 0);
    const CFStringRef buses[] = {CFSTR("MainStage MCP Input"), CFSTR("MainStage MCP Output")};
    ItemCount count = MIDIDeviceListGetNumberOfDevices(devices);
    if (count > 1) return -50; /* Fail closed on unexpected owned topology. */
    MIDIDeviceRef device = count ? MIDIDeviceListGetDevice(devices, 0) : 0;
    OSStatus status = noErr;
    Boolean created = !count;
    if (created) {
        status = MIDIDeviceCreate(self, MODEL, MANUFACTURER, MODEL, &device);
        if (status) return status;
        status = MIDIObjectSetStringProperty(device, kMIDIPropertyDriverOwner, OWNER);
        for (unsigned i = 0; i < 2 && !status; ++i) {
            MIDIEntityRef entity = 0;
            status = MIDIDeviceAddEntity(device, buses[i], true, 1, 1, &entity);
            if (!status) status = MIDIObjectSetStringProperty(MIDIEntityGetSource(entity, 0), kMIDIPropertyName, buses[i]);
            if (!status) status = MIDIObjectSetStringProperty(MIDIEntityGetDestination(entity, 0), kMIDIPropertyName, buses[i]);
        }
        if (status) { MIDIDeviceDispose(device); return status; }
    }
    MIDIEndpointRef sources[2], destinations[2];
    if (!named(device, kMIDIPropertyManufacturer, MANUFACTURER) ||
        !named(device, kMIDIPropertyModel, MODEL) ||
        !named(device, kMIDIPropertyDriverOwner, OWNER) ||
        MIDIDeviceGetNumberOfEntities(device) != 2) status = -50;
    for (unsigned i = 0; i < 2 && !status; ++i) {
        MIDIEntityRef entity = MIDIDeviceGetEntity(device, i);
        if (!named(entity, kMIDIPropertyName, buses[i]) ||
            MIDIEntityGetNumberOfSources(entity) != 1 ||
            MIDIEntityGetNumberOfDestinations(entity) != 1) { status = -50; break; }
        sources[i] = MIDIEntityGetSource(entity, 0);
        destinations[i] = MIDIEntityGetDestination(entity, 0);
        if (!sources[i] || !destinations[i]) { status = -50; break; }
        status = MIDIEndpointSetRefCons(destinations[i], (void *)(uintptr_t)(i + 1), NULL);
    }
    if (!status) status = MIDIObjectSetIntegerProperty(device, kMIDIPropertyAdvanceScheduleTimeMuSec, 0);
    if (!status) status = MIDIObjectSetIntegerProperty(device, kMIDIPropertyOffline, 0);
    if (!status && created) status = MIDISetupAddDevice(device);
    if (status) {
        if (created) MIDIDeviceDispose(device);
        return status;
    }
    driver->device = device;
    for (unsigned i = 0; i < 2; ++i) atomic_store(&driver->sources[i], sources[i]);
    return noErr;
}

static OSStatus stop(MIDIDriverRef self) {
    Driver *driver = (Driver *)self;
    atomic_store(&driver->sources[0], 0);
    atomic_store(&driver->sources[1], 0);
    return driver->device ? MIDIObjectSetIntegerProperty(driver->device, kMIDIPropertyOffline, 1) : noErr;
}

static OSStatus send_packets(MIDIDriverRef self, const MIDIPacketList *packets, void *bus, void *unused) {
    (void)unused;
    uintptr_t index = (uintptr_t)bus;
    if (!packets || index < 1 || index > 2) return -50;
    MIDIEndpointRef source = atomic_load(&((Driver *)self)->sources[index - 1]);
    return source ? MIDIReceived(source, packets) : kMIDIObjectNotFound;
}

static OSStatus enable_source(MIDIDriverRef self, MIDIEndpointRef source, Boolean enabled) {
    (void)self; (void)source; (void)enabled;
    return noErr; /* CoreMIDI fans out to that source's connected clients. */
}

static OSStatus flush(MIDIDriverRef self, MIDIEndpointRef destination, void *a, void *b) {
    (void)self; (void)destination; (void)a; (void)b;
    return noErr; /* No internal scheduling queue. */
}

static MIDIDriverInterface interface = {
    .QueryInterface = query, .AddRef = add_ref, .Release = release,
    .Start = start, .Stop = stop, .Send = send_packets,
    .EnableSource = enable_source, .Flush = flush
};

__attribute__((visibility("default")))
void *MainStageMCPDriverFactory(CFAllocatorRef allocator, CFUUIDRef type) {
    (void)allocator;
    if (!CFEqual(type, kMIDIDriverTypeID)) return NULL;
    Driver *driver = calloc(1, sizeof(*driver));
    if (!driver) return NULL;
    driver->interface = &interface;
    atomic_init(&driver->references, 1);
    atomic_init(&driver->sources[0], 0);
    atomic_init(&driver->sources[1], 0);
    CFPlugInAddInstanceForFactory(FACTORY_ID);
    return driver;
}
